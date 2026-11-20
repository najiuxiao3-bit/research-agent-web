# -*- coding: utf-8 -*-
"""
跨平台签到 / 领额度任务模块（任务中心，独立于调研链路与浏览器搜索链路）。

设计原则：
- 平台清单由前端页面可视化配置，存入 SQLite（storage.platforms 表）；
  本模块只负责“拿到一个平台 dict → 打开 → 探测 → 点击 → 回传结果”，不维护写死清单。
- 通用探测：进入页面后识别“签到 / 领取 / 每日福利 / 领额度”等可点入口，
  不把选择器写死到某个平台；命中即点击，未命中如实回传。
- 登录保守策略：检测到登录拦截时，有头模式下保留浏览器窗口等待人工扫码，
  轮询确认登录成功后再继续；超时仍未登录则标记 login_required 交回前端，
  不绕过登录、不填写任何账号密码或验证码。
- 登录态持久化：使用持久化浏览器目录 data/pw-profile（已被 .gitignore 排除），
  扫码一次后 cookie 留在本地目录，后续任务自动复用，无需重复扫码。

平台 dict 字段（storage.list_platforms 对外结构）：
    id, name, url, description, login_required, enabled, note

返回结构（单条结果）：
{
  "ok": bool,
  "id": str,
  "name": str,
  "url": str,
  "status": "claim" | "already" | "login_required" | "not_found" | "error",
  "message": str,
  "detail": str,
  "candidates": [str],
  "screenshot_url": str,
  "logged_in": bool,
  "ran_at": str
}
"""

import hashlib
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

import config

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "screenshots"
PROFILE_DIR = Path(__file__).resolve().parent.parent / "data" / "pw-profile"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/159.0.0.0 Safari/537.36"
)

# 等待人工扫码登录的最长时间（秒）；轮询间隔（毫秒）
# 第一次登录需要打开手机扫码，留足时间；登录成功后 cookie 落盘，后续无需再等。
LOGIN_WAIT_SECONDS = 180
LOGIN_POLL_MS = 2000

# 领取 / 签到类按钮文案关键词（中英文）
CLAIM_KEYWORDS = (
    "签到", "领取", "领额度", "领福利", "每日福利", "领取奖励", "领取积分",
    "打卡", "checkin", "check-in", "claim", "daily reward", "redeem",
)
# 积分 / 额度 / 余额 / 权益类入口（状态展示按钮，点进去再找可领取项）
BALANCE_KEYWORDS = ("积分", "额度", "余额", "福利", "权益", "reward")
# 已领取 / 已签到 / 领取成功文案关键词
ALREADY_KEYWORDS = (
    "已领取", "已签到", "已打卡", "今日已领", "已完成", "领取成功",
    "已到账", "到账成功", "claimed", "checked in",
)
# 登录拦截信号
LOGIN_URL_HINTS = ("login", "signin", "sign-in", "oauth", "passport", "auth")
LOGIN_TEXT_HINTS = ("扫码登录", "账号登录", "密码登录", "手机验证码登录", "请登录", "登录后")
LOGIN_INPUT_SELECTORS = ("input[type='password']", "input[placeholder*='密码']", "input[placeholder*='验证码']")
# 可见的登录入口按钮 / 链接文案（不写死到具体站点）
LOGIN_BUTTON_HINTS = ("登录", "立即登录", "登录/注册", "登录注册", "sign in", "log in", "sign up")
# 命中这些词视为“已登录态下的退出入口”，不能当成登录拦截
LOGIN_EXCLUDE_HINTS = ("退出登录", "退出", "登出", "注销", "logout")
# 关闭 / 跳过引导弹窗的安全文案（纯关闭、不推进业务）
DISMISS_BUTTON_HINTS = ("我知道了", "知道了", "暂不", "跳过", "稍后", "先逛逛", "先不用", "下次再说", "关闭")
# 弹窗里明确不点的推进 / 跳转 / 确认类主按钮
CONFIRM_EXCLUDE_HINTS = ("去看看", "立即开工", "立即升级", "立即领取", "确认", "创建", "提交", "同意", "领取", "签到")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def base_result(platform_id, name: str = "", url: str = "") -> dict:
    return {
        "ok": False,
        "id": str(platform_id),
        "name": name,
        "url": url,
        "status": "error",
        "message": "",
        "detail": "",
        "candidates": [],
        "screenshot_url": "",
        "logged_in": False,
        "ran_at": _now(),
    }


def _visible_login_button(page) -> str:
    """查找可见的登录入口按钮 / 链接，命中返回其文案，否则返回空串。

    只在可点元素里找，避免把营销文案（如“登录即到账”的说明文字）误判成登录拦截。
    已登录态下的“退出 / 登出”入口会被排除。
    """
    for sel in ("button", "a", "[role='button']"):
        try:
            loc = page.locator(sel)
            count = loc.count()
        except Exception:
            continue
        for i in range(min(count, 40)):
            try:
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                text = (el.inner_text() or "").strip().replace("\n", " ")
                if not text or len(text) > 12:
                    continue
                if any(x in text for x in LOGIN_EXCLUDE_HINTS):
                    continue
                low = text.lower()
                if any(h in low for h in LOGIN_BUTTON_HINTS):
                    return text
            except Exception:
                continue
    return ""


def _detect_login(page) -> tuple[bool, str]:
    """判断当前是否被登录拦截，返回 (是否需登录, 依据说明)。"""
    url = (page.url or "").lower()
    if any(h in url for h in LOGIN_URL_HINTS):
        return True, "页面跳转到了登录 / 授权地址"

    for sel in LOGIN_INPUT_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True, "页面出现密码 / 验证码输入框"
        except Exception:
            continue

    btn_text = _visible_login_button(page)
    if btn_text:
        return True, f"页面存在登录入口（按钮：{btn_text}）"

    try:
        body_text = page.locator("body").first.inner_text()[:1200]
        for hint in LOGIN_TEXT_HINTS:
            if hint in body_text:
                return True, f"页面出现登录引导（{hint}）"
    except Exception:
        pass

    return False, ""


def _wait_for_login(page) -> tuple[bool, str]:
    """有头模式下保留窗口等待人工扫码；轮询直到登录解除或超时。

    返回 (是否已登录, 说明)。无头环境无法扫码，直接判定超时。
    """
    if config.BROWSER_HEADLESS:
        return False, "当前为无头运行环境，无法弹出窗口扫码；请在本地有头模式登录一次。"

    deadline = time.time() + LOGIN_WAIT_SECONDS
    while True:
        blocked, reason = _detect_login(page)
        if not blocked:
            return True, "登录状态已确认"
        if time.time() >= deadline:
            return False, f"等待扫码超时（{LOGIN_WAIT_SECONDS}s），仍停留在登录 / 验证页"
        page.wait_for_timeout(LOGIN_POLL_MS)


def _find_claim_candidates(page) -> list[dict]:
    """收集页面上疑似领取 / 签到 / 积分额度的可点元素。

    返回 [{text, locator, level}]；level=0 为强领取入口（签到 / 领取类文案），
    level=1 为弱余额入口（积分 / 额度展示，点进去再找可领取项）。强候选优先点击。
    """
    candidates: list[dict] = []
    seen: set[str] = set()
    selectors = ["button", "a", "[role='button']", ".btn", "[class*='sign']", "[class*='claim']"]

    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
        except Exception:
            continue
        for i in range(min(count, 40)):
            try:
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                text = (el.inner_text() or "").strip().replace("\n", " ")
                if not text or len(text) > 30:
                    continue
                key = text.lower()
                if any(k in key for k in CLAIM_KEYWORDS):
                    level = 0
                elif any(k in key for k in BALANCE_KEYWORDS):
                    level = 1
                else:
                    continue
                if text in seen:
                    continue
                seen.add(text)
                candidates.append({"text": text, "locator": el, "level": level})
            except Exception:
                continue

    candidates.sort(key=lambda c: c["level"])
    return candidates


def _detect_already(page) -> str:
    """检测页面是否已显示已领取 / 已签到状态。"""
    try:
        body_text = page.locator("body").first.inner_text()[:1500]
        for hint in ALREADY_KEYWORDS:
            if hint in body_text:
                return hint
    except Exception:
        pass
    return ""


OVERLAY_SELECTORS = (
    "[role='dialog']", ".modal", "[class*='modal']", "[class*='Modal']",
    "[class*='dialog']", "[class*='Dialog']",
)


def _safe_click(el) -> bool:
    """滚动到可视区后标准点击；被遮挡 / 在视口外时回退 JS click。成功返回 True。"""
    try:
        el.scroll_into_view_if_needed(timeout=1000)
    except Exception:
        pass
    try:
        el.click(timeout=1500)
        return True
    except Exception:
        try:
            el.evaluate("e => e.click()")
            return True
        except Exception:
            return False


def _visible_overlays(page) -> list:
    """返回当前可见的弹窗 / 遮罩容器列表。

    同一弹窗会被多个选择器（role=dialog / .modal / [class*=modal]）重复命中，
    这里按 DOM 元素路径去重，避免重复处理同一个弹窗。
    """
    overlays = []
    seen: set[str] = set()
    for sel in OVERLAY_SELECTORS:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 4)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                try:
                    key = el.evaluate(
                        "e => { const s=[]; let n=e; "
                        "while(n && n.nodeType===1){ "
                        "const i = n.parentElement ? Array.prototype.indexOf.call(n.parentElement.children, n) : -1; "
                        "s.push(n.tagName+'.'+(typeof n.className==='string'?n.className:'')+'#'+i); n=n.parentElement; } "
                        "return s.join('>'); }"
                    )
                except Exception:
                    key = None
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                overlays.append(el)
        except Exception:
            continue
    return overlays


def _overlay_has_claim(overlay) -> bool:
    """弹窗内是否存在领取 / 签到类业务按钮。

    含这类按钮的弹窗是“领取目标弹窗”，不能被当成引导弹窗关掉，需保留并交由候选流程点击。
    """
    btns = overlay.locator("button, a, [role='button']")
    for j in range(min(btns.count(), 12)):
        try:
            b = btns.nth(j)
            if not b.is_visible():
                continue
            text = (b.inner_text() or "").strip().replace("\n", " ")
            if text and any(k in text.lower() for k in CLAIM_KEYWORDS):
                return True
        except Exception:
            continue
    return False


def _dismiss_overlays(page) -> list[str]:
    """关闭可见的引导 / 提示弹窗。

    只点关闭图标或“我知道了 / 暂不 / 跳过”这类纯关闭按钮；
    不点“去看看 / 立即开工 / 立即升级 / 确认 / 创建”等会推进业务或跳转的主按钮。
    含领取 / 签到按钮的目标弹窗直接跳过，不关闭。返回已执行的关闭动作说明列表。
    """
    dismissed: list[str] = []
    for _ in range(2):  # 最多两轮，处理叠层弹窗
        acted = False
        for overlay in _visible_overlays(page):
            # 领取 / 签到目标弹窗：保留，绝不关闭
            if _overlay_has_claim(overlay):
                continue

            # 1) 优先关闭图标
            icon = overlay.locator(
                "[aria-label*='close' i], [aria-label*='关闭'], "
                "[class*='close-icon'], [class*='CloseIcon'], [class*='close-btn']"
            ).first
            try:
                if icon.count() and icon.is_visible():
                    if _safe_click(icon):
                        dismissed.append("关闭弹窗图标")
                        acted = True
                        page.wait_for_timeout(600)
                        continue
            except Exception:
                pass

            # 2) 安全关闭文案按钮
            btns = overlay.locator("button, a, [role='button']")
            for j in range(min(btns.count(), 10)):
                try:
                    b = btns.nth(j)
                    if not b.is_visible():
                        continue
                    text = (b.inner_text() or "").strip().replace("\n", " ")
                    if not text or len(text) > 12:
                        continue
                    if any(x in text for x in CONFIRM_EXCLUDE_HINTS):
                        continue
                    if any(h in text for h in DISMISS_BUTTON_HINTS):
                        if _safe_click(b):
                            dismissed.append(f"关闭弹窗（{text}）")
                            acted = True
                            page.wait_for_timeout(600)
                            break
                except Exception:
                    continue
        if not acted:
            break
    return dismissed


def _shoot(page, platform_id) -> str:
    """截图并返回静态访问路径；失败返回空串，不阻断主流程。"""
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        fname = (
            f"quest_{int(time.time())}_"
            f"{hashlib.md5(str(platform_id).encode('utf-8')).hexdigest()[:8]}.png"
        )
        page.screenshot(path=str(SCREENSHOT_DIR / fname), full_page=False, type="png")
        return f"/screenshots/{fname}"
    except Exception:
        return ""


def run_platform(platform: dict) -> dict:
    """执行单个平台的签到 / 领额度任务，返回结果 dict。"""
    base = base_result(platform.get("id"), platform.get("name", ""), platform.get("url", ""))

    if not platform.get("enabled", True):
        base["status"] = "not_found"
        base["message"] = "该平台为占位 / 已停用，尚未配置真实入口。"
        return base

    try:
        with sync_playwright() as p:
            # 持久化浏览器目录：扫码一次后 cookie 保留，后续自动复用（目录仅本机、已 gitignore）
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=config.BROWSER_HEADLESS,
                user_agent=_UA,
                viewport={"width": 1280, "height": 900},
                locale="zh-CN",
            )
            # 复用持久化目录里已有的标签页，避免多出一个空白初始页
            page = context.pages[0] if context.pages else context.new_page()

            page.goto(platform["url"], timeout=config.REQUEST_TIMEOUT * 1000)
            page.wait_for_load_state("domcontentloaded", timeout=config.REQUEST_TIMEOUT * 1000)
            page.wait_for_timeout(1200)

            # 1. 登录拦截识别
            login_blocked, reason = _detect_login(page)
            base["logged_in"] = not login_blocked

            if login_blocked:
                # 有头模式保留窗口等待人工登录；轮询确认登录解除后再继续。
                # 登录成功后 cookie 随持久化目录落盘，下次打开自动带登录态。
                logged_in, wait_note = _wait_for_login(page)
                if not logged_in:
                    base["status"] = "login_required"
                    base["message"] = "需要登录后才能领取。请在弹出的浏览器窗口完成扫码 / 登录；登录一次后 cookie 会保存在本地目录，后续自动复用。"
                    base["detail"] = wait_note
                    base["screenshot_url"] = _shoot(page, platform["id"])
                    context.close()
                    return base
                # 登录解除，稍等页面刷新状态再继续
                page.wait_for_timeout(800)
                base["logged_in"] = True

            # 2. 先探测可领取入口（含弹窗内的强领取按钮），避免先把领取目标弹窗关掉
            candidates = _find_claim_candidates(page)
            strong = [c for c in candidates if c["level"] == 0]

            # 没有强领取入口时，再关闭纯引导 / 提示弹窗（含领取按钮的弹窗会被跳过）
            if not strong:
                dismissed = _dismiss_overlays(page)
                if dismissed:
                    base["detail"] = "已关闭弹窗：" + "；".join(dismissed)
                candidates = _find_claim_candidates(page)
                strong = [c for c in candidates if c["level"] == 0]

            # 登录态确认后再截图，避免回传刚打开时的未登录页面
            base["screenshot_url"] = _shoot(page, platform["id"])

            # 3. 是否已领取
            already_hint = _detect_already(page)
            if already_hint:
                base["status"] = "already"
                base["message"] = f"今日已领取 / 已签到（页面显示“{already_hint}”），无需重复操作。"
                context.close()
                return base

            base["candidates"] = [c["text"] for c in candidates]

            # 4. 选择目标：强领取入口优先，其次余额 / 权益入口
            target_list = strong or candidates
            if not target_list:
                base["status"] = "not_found"
                if _visible_overlays(page):
                    base["message"] = "页面仍被弹窗遮挡，领取 / 积分入口可能在主界面或二级页面，需人工确认。"
                else:
                    base["message"] = "当前页未发现可领取 / 签到 / 积分入口；入口可能在二级页面，或需人工确认。"
                context.close()
                return base

            target = target_list[0]
            if _safe_click(target["locator"]):
                page.wait_for_timeout(1500)
                # 点击后识别到账 / 已领取反馈
                feedback = _detect_already(page)
                if feedback:
                    base["status"] = "already"
                    base["message"] = f"领取成功（页面显示“{feedback}”），积分 / 额度已到账。"
                else:
                    base["status"] = "claim"
                    base["message"] = f"已点击“{target['text']}”，请查看页面反馈确认是否到账。"
                base["detail"] = f"候选入口：{', '.join(c['text'] for c in target_list)}"
            else:
                base["status"] = "not_found"
                base["message"] = "找到了疑似入口，但点击未成功，可能被遮挡或需要人工操作。"
                base["detail"] = f"候选入口：{', '.join(c['text'] for c in target_list)}"

            base["screenshot_url"] = _shoot(page, f"{platform['id']}_after")
            context.close()

        base["ok"] = True
        return base

    except PWTimeout:
        base["message"] = "页面加载超时，站点响应过慢或被拦截。"
        return base
    except Exception as exc:
        base["message"] = f"任务执行异常：{type(exc).__name__}: {exc}"
        return base


def run_platforms(platforms: list[dict]) -> list[dict]:
    """顺序执行一组平台配置，返回结果列表。"""
    return [run_platform(p) for p in platforms]
