# -*- coding: utf-8 -*-
"""
搜索工具：search_web(query, top_k) -> dict

默认走 Bing 网页结果（当前网络可达、免 Key），解析标题 / 摘要 / 链接。
接入真实搜索服务（Tavily / SerpAPI / Bing Web Search）时，
在 _search_via_tavily 实现并切换调用即可，对外返回结构保持不变。
"""

import html
import re

import requests

import config

BING_URL = "https://cn.bing.com/search"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _clean(text: str) -> str:
    """去 HTML 标签并反转义实体。"""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _parse_bing(page: str, top_k: int) -> list[dict]:
    """从 Bing 结果页解析出标题、链接、摘要片段。"""
    blocks = re.findall(r'<li class="b_algo".*?</li>', page, re.S)
    results: list[dict] = []
    for block in blocks:
        a = re.search(r'<h2>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        results.append(
            {
                "title": _clean(a.group(2)),
                "url": a.group(1),
                "snippet": _clean(p.group(1)) if p else "",
            }
        )
        if len(results) >= top_k:
            break
    return results


def _search_via_bing(query: str, top_k: int) -> dict:
    """默认实现：Bing 网页结果抓取。"""
    resp = requests.get(
        BING_URL,
        params={"q": query, "count": top_k},
        headers=_HEADERS,
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    results = _parse_bing(resp.text, top_k)
    if not results:
        return {
            "ok": False,
            "results": [],
            "message": "未检索到相关条目，请换更具体的关键词重试。",
        }
    return {"ok": True, "results": results, "message": ""}


def _search_via_tavily(query: str, top_k: int) -> dict:
    """
    预留：接入 Tavily / SerpAPI / Bing Web Search 等官方搜索 API。
    需要在环境变量配置 SEARCH_API_KEY 后启用。
    示例（Tavily）：
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": config.SEARCH_API_KEY, "query": query, "max_results": top_k},
            timeout=config.REQUEST_TIMEOUT,
        )
        data = resp.json()
        results = [{"title": r["title"], "snippet": r["content"], "url": r["url"]}
                   for r in data["results"]]
    完成后把 search_web 里的调用换成本函数即可。
    """
    raise NotImplementedError("请在 _search_via_tavily 中填入你的搜索服务实现。")


def search_web(query: str, top_k: int = 5) -> dict:
    """
    对外统一入口：按关键词检索资料。

    返回结构：
        {
          "ok": bool,
          "results": [{"title": str, "snippet": str, "url": str}, ...],
          "message": str   # 失败时给出可行动的提示
        }
    """
    if not query or not query.strip():
        return {"ok": False, "results": [], "message": "query 不能为空，请补充检索关键词。"}

    try:
        # 想换官方搜索 API：把下面这行改成 _search_via_tavily(query, top_k)
        return _search_via_bing(query.strip(), min(top_k, 8))
    except requests.Timeout:
        return {"ok": False, "results": [], "message": "搜索请求超时，请稍后重试或缩短关键词。"}
    except requests.RequestException as exc:
        return {"ok": False, "results": [], "message": f"搜索网络异常：{type(exc).__name__}，请检查网络后重试。"}
    except Exception as exc:  # 兜底：工具异常不能让主循环崩溃
        return {"ok": False, "results": [], "message": f"搜索工具内部错误：{type(exc).__name__}: {exc}"}
