# -*- coding: utf-8 -*-
"""
页面摘要：优先调用大模型提炼要点；无 Key、超时或异常时回退本地抽取式摘要。

被独立浏览器自动化链路调用，不进入调研 Agent 循环、不写调研历史。
"""

import re

import requests

import config


def _extract_local(text: str, max_sentences: int = 4) -> str:
    """本地抽取式摘要：按句号/换行取前若干句，不依赖模型。"""
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？\n；])", text)
    sents = [p.strip() for p in parts if len(p.strip()) > 8]
    if not sents:
        return text[:120]
    summary = "。".join(sents[:max_sentences]).strip("。")
    return summary + "。"


def summarize_text(text: str, focus: str = "") -> str:
    """提炼网页正文要点；模型不可用时回退本地抽取。"""
    text = (text or "").strip()
    if len(text) < 30:
        return _extract_local(text)

    if not config.LLM_API_KEY:
        return _extract_local(text)

    prompt = (
        "请基于以下网页正文，提炼 3~5 条中文要点摘要，每条一句话，简洁客观；"
        "不要编造正文没有的信息，不要输出标题和多余解释。\n\n"
        f"关键词：{focus}\n正文：\n{text[:2000]}"
    )
    try:
        resp = requests.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"].get("content") or ""
        content = content.strip()
        return content or _extract_local(text)
    except Exception:
        return _extract_local(text)
