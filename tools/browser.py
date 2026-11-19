# -*- coding: utf-8 -*-
"""
独立浏览器自动化模块：直达百度结果页 → 依次进入前几条结果 → 抽取有正文的页面。

由 Web 后端独立接口 POST /api/browser/open 直接调用，不进入 Agent 循环、
流程不由模型决策、不写调研历史；页面摘要可选调用模型提炼，失败回退本地抽取。
返回结构：
{
  "ok": bool,
  "title": str,
  "url": str,
  "content": str,                              # 正文截断，供前端展示
  "summary": str,                              # 提炼后的要点摘要
  "screenshot_url": str,                        # 页面截图静态访问路径
  "results": [{"title", "url", "snippet"}],
  "message": str
}

说明：
- 无头环境下百度首页表单可能被判定为不可见，因此不填首页搜索框，
  直接用结果页 URL（/s?wd=关键词）导航。
- 前几条结果可能是广告/落地页/聚合卡片（正文稀疏），工具会依次进入
  前 3 条，抽到足够正文才采用；都不足时回退最后一条并给出提示。
"""

import hashlib
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

import config
from tools.summarize import summarize_text

BAIDU_SEARCH = "https://www.baidu.com/s?wd="
_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "screenshots"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/159.0.0.0 Safari/537.36"
)
MAX_CONTENT = 1500
MAX_TRY = 3
MIN_BODY = 30

# 各站点常见正文容器，按优先级依次尝试
CONTAINER_SELECTORS = [
    ".lemma-summary",
    ".J-lemma-content",
    ".para",
    "article",
    "main",
    ".content",
    ".detail",
    "#content",
]

# 页脚/版权噪音行关键词
NOISE_KEYWORDS = ("备案", "ICP", "版权所有", "前必读", "合作平台", "公网安备", "©", "隐私政策", "cookie")


def _filter_noise(text: str) -> str:
    """丢弃页脚、备案、版权等短噪音行。"""
    kept = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) < 25 and any(k in line for k in NOISE_KEYWORDS):
            continue
        kept.append(line)
    return "\n".join(kept)


def _extract_page(page) -> tuple[str, str, str]:
    """抽取当前页标题、正文段落和最终 URL。"""
    title = ""
    h1 = page.locator("h1").first
    if h1.count():
        title = h1.inner_text().strip()
    if not title:
        title = page.title().strip()

    body = ""
    # 1. 优先从专用正文容器取
    for sel in CONTAINER_SELECTORS:
        loc = page.locator(sel).first
        if loc.count():
            txt = loc.inner_text().strip()
            if len(txt) >= MIN_BODY:
                body = txt
                break

    # 2. 回退：普通段落
    if len(body) < MIN_BODY:
        paragraphs = page.locator("p").all_inner_texts()
        body = "\n".join(p.strip() for p in paragraphs if len(p.strip()) > 15)

    # 3. 再回退：整页可见文本
    if len(body) < MIN_BODY and page.locator("body").count():
        body = page.locator("body").first.inner_text()

    body = _filter_noise(body)
    if len(body) > MAX_CONTENT:
        body = body[:MAX_CONTENT] + "…"
    return title, page.url, body


def open_first_result(keyword: str) -> dict:
    """百度搜索关键词，依次打开前几条结果，回传有正文页面的标题、正文与链接。"""
    if not keyword or not keyword.strip():
        return {
            "ok": False,
            "title": "",
            "url": "",
            "content": "",
            "results": [],
            "summary": "",
            "screenshot_url": "",
            "message": "keyword 不能为空，请补充搜索关键词。",
        }

    keyword = keyword.strip()
    empty = {
        "ok": False,
        "title": "",
        "url": "",
        "content": "",
        "results": [],
        "summary": "",
        "screenshot_url": "",
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=config.BROWSER_HEADLESS,
            )
            page = browser.new_page(user_agent=_UA, viewport={"width": 1280, "height": 900})

            # 1. 直达结果页
            search_url = BAIDU_SEARCH + urllib.parse.quote(keyword)
            page.goto(search_url, timeout=config.REQUEST_TIMEOUT * 1000)
            page.wait_for_selector(
                "#content_left h3 a",
                timeout=config.REQUEST_TIMEOUT * 1000,
            )

            # 2. 收集前几条候选结果
            anchors = page.locator("#content_left h3 a")
            n = anchors.count()
            if n == 0:
                browser.close()
                return {**empty, "message": "未找到相关搜索结果，请换更具体的关键词。"}

            candidates = []
            for i in range(min(MAX_TRY, n)):
                a = anchors.nth(i)
                candidates.append(
                    {
                        "title": a.inner_text().strip(),
                        "href": a.get_attribute("href") or "",
                    }
                )

            # 3. 逐条进入，抽到足够正文即采用；空壳页继续下一条
            chosen = None
            last = None
            for cand in candidates:
                if not cand["href"]:
                    continue
                try:
                    page.goto(cand["href"], timeout=config.REQUEST_TIMEOUT * 1000)
                    page.wait_for_load_state(
                        "domcontentloaded", timeout=config.REQUEST_TIMEOUT * 1000
                    )
                    page.wait_for_timeout(1000)
                    page_title, page_url, content = _extract_page(page)
                    rec = {
                        "title": page_title or cand["title"],
                        "url": page_url or cand["href"],
                        "content": content,
                    }
                    last = rec
                    if len(content) >= MIN_BODY:
                        chosen = rec
                        break
                except Exception:
                    continue

            # 截图当前最终落地页（截图失败不阻断主流程）
            screenshot_url = ""
            try:
                _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                fname = (
                    f"{int(time.time())}_"
                    f"{hashlib.md5(keyword.encode('utf-8')).hexdigest()[:8]}.png"
                )
                page.screenshot(path=str(_SCREENSHOT_DIR / fname), full_page=False, type="png")
                screenshot_url = f"/screenshots/{fname}"
            except Exception:
                screenshot_url = ""
            browser.close()

        if not chosen and not last:
            return {**empty, "message": "未能进入任何结果页面。"}

        rec = chosen or last
        message = "" if chosen else "已打开相关页面，但该页正文较稀疏，仅回传标题与链接。"
        summary = summarize_text(rec["content"], keyword)
        return {
            "ok": True,
            "title": rec["title"],
            "url": rec["url"],
            "content": rec["content"],
            "summary": summary,
            "screenshot_url": screenshot_url,
            "results": [
                {"title": rec["title"], "url": rec["url"], "snippet": rec["content"][:200]}
            ],
            "message": message,
        }
    except PWTimeout:
        return {**empty, "message": "浏览器操作超时，页面加载过慢或被目标站点拦截。"}
    except Exception as exc:  # 兜底：工具异常不能让主循环崩溃
        return {
            **empty,
            "message": f"浏览器自动化异常：{type(exc).__name__}: {exc}",
        }
