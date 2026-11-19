# -*- coding: utf-8 -*-
"""
Agent 主循环：思考 → 调工具 → 观察结果 → 再决策，直到模型给出最终摘要。

run() 返回结构化结果，供命令行与 Web 后端复用：
    {
      "ok": bool, "status": "ok" | "truncated" | "error",
      "answer": str, "sources": [{title, url, snippet}],
      "trace": [{turn, tool, args, ok, n_results, message}],
      "turns": int, "error": str
    }
"""

import json

import requests

import config
import prompts
from tools.search import search_web

# 把工具函数注册到提示词里的工具表：工具名 → 实际函数
prompts.TOOL_REGISTRY["search_web"] = search_web


def call_llm(messages: list[dict]) -> dict:
    """调用 OpenAI 兼容的 chat/completions 接口，返回模型的 assistant 消息。"""
    resp = requests.post(
        f"{config.LLM_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.LLM_MODEL,
            "messages": messages,
            "tools": [prompts.SEARCH_TOOL_SCHEMA],
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]


def _execute_tool(tool_call: dict) -> str:
    """按工具名查注册表并执行，把结果序列化成文本回灌给模型。"""
    fn_name = tool_call["function"]["name"]
    try:
        args = json.loads(tool_call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        args = {}

    func = prompts.TOOL_REGISTRY.get(fn_name)
    if func is None:
        return json.dumps(
            {"ok": False, "message": f"未知工具：{fn_name}"}, ensure_ascii=False
        )

    result = func(**args)
    return json.dumps(result, ensure_ascii=False)


def run(user_query: str, verbose: bool = False) -> dict:
    """跑完整 Agent 循环，返回结构化结果。"""
    messages: list[dict] = [
        {"role": "system", "content": prompts.SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    sources: list[dict] = []
    trace: list[dict] = []
    seen_urls: set[str] = set()
    final_answer = ""
    turns = 0

    for turn in range(1, config.MAX_TURNS + 1):
        turns = turn
        try:
            message = call_llm(messages)
        except Exception as exc:  # 模型链路异常，结构化返回，不抛崩上层
            return {
                "ok": False,
                "status": "error",
                "answer": "",
                "sources": sources,
                "trace": trace,
                "turns": turns,
                "error": f"模型调用失败：{type(exc).__name__}: {exc}",
            }

        # 只保留合法字段，组装干净的 assistant 消息回写
        assistant_msg = {
            "role": "assistant",
            "content": message.get("content"),
        }
        if message.get("tool_calls"):
            assistant_msg["tool_calls"] = message["tool_calls"]
        messages.append(assistant_msg)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            # 模型不再调工具，认为任务完成
            final_answer = message.get("content") or ""
            break

        if verbose:
            print(f"[第 {turn} 轮] 请求调用 {len(tool_calls)} 个工具：")

        for call in tool_calls:
            fn_name = call["function"]["name"]
            args_text = call["function"]["arguments"]
            try:
                args = json.loads(args_text or "{}")
            except json.JSONDecodeError:
                args = {}
            if verbose:
                print(f"  → {fn_name}({args_text})")

            observation = _execute_tool(call)
            try:
                obs = json.loads(observation)
            except json.JSONDecodeError:
                obs = {}

            # 收集去重后的来源条目
            for r in obs.get("results", []) or []:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append(
                        {
                            "title": r.get("title", ""),
                            "url": url,
                            "snippet": r.get("snippet", ""),
                        }
                    )

            trace.append(
                {
                    "turn": turn,
                    "tool": fn_name,
                    "args": args,
                    "ok": obs.get("ok"),
                    "n_results": len(obs.get("results", []) or []),
                    "message": obs.get("message", ""),
                }
            )
            if verbose:
                print(f"  ← {observation[:200]}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": observation,
                }
            )
    else:
        # 达到最大轮数仍未收尾
        final_answer = "已达到最大检索轮数，资料收集停止；以下结论基于现有资料，可能不完整。"
        return {
            "ok": True,
            "status": "truncated",
            "answer": final_answer,
            "sources": sources,
            "trace": trace,
            "turns": turns,
            "error": "",
        }

    return {
        "ok": True,
        "status": "ok",
        "answer": final_answer,
        "sources": sources,
        "trace": trace,
        "turns": turns,
        "error": "",
    }
