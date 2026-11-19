# -*- coding: utf-8 -*-
"""
Agent 主循环（LangGraph 状态图版）：思考 → 调工具 → 观察结果 → 再决策，直到模型给出最终摘要。

与旧版手写循环对外契约完全一致，run() 返回：
    {
      "ok": bool, "status": "ok" | "truncated" | "error",
      "answer": str, "sources": [{title, url, snippet}],
      "trace": [{turn, tool, args, ok, n_results, message}],
      "turns": int, "error": str
    }

架构（LangGraph ReAct 图）：
    START → agent(模型绑定工具) ──有 tool_calls──▶ tools(执行并回灌) ──▶ agent
                                  └──无 tool_calls──▶ END

状态：
    messages  对话消息（add_messages 追加 reducer）
    sources   去重后的检索来源
    trace     工具调用轨迹
"""

import json
import os
from typing import Annotated, TypedDict

# 关闭 LangSmith 在线追踪，避免无 Key 时产生上报告警
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.errors import GraphRecursionError

import config
import prompts
from tools.search import search_web as _search_web

# 工具注册表：工具名 → 实际函数（便于后续新增工具时在此挂载）
TOOL_REGISTRY: dict[str, object] = {}


# ---------- 工具定义（@tool 自动生成 function-calling schema） ----------

@tool
def search_web(query: str, top_k: int = 5) -> str:
    """联网检索公开资料，返回标题、摘要片段和链接。用于查事实、数据、近期事件；不用于读取本地文件或数学计算。

    Args:
        query: 检索关键词，尽量用名词短语；一次聚焦一个主题。
        top_k: 返回资料条数，默认 5，最多 8。
    """
    result = _search_web(query, top_k)
    return json.dumps(result, ensure_ascii=False)


TOOL_REGISTRY["search_web"] = _search_web
TOOLS = [search_web]


# ---------- 图状态 ----------

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    sources: list[dict]
    trace: list[dict]


def _build_llm() -> ChatOpenAI:
    """按配置构造 OpenAI 兼容模型（DeepSeek 兼容端点）。"""
    return ChatOpenAI(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        model=config.LLM_MODEL,
        timeout=config.REQUEST_TIMEOUT,
    )


# ---------- 图节点 ----------

def agent_node(state: AgentState) -> dict:
    """模型节点：绑定工具后决策下一步（继续调工具或直接收尾）。"""
    llm_with_tools = _build_llm().bind_tools(TOOLS)
    response: AIMessage = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}


def tools_node(state: AgentState) -> dict:
    """工具节点：执行上一条模型消息里的工具调用，回灌 ToolMessage，并累积来源与轨迹。"""
    last: AIMessage = state["messages"][-1]
    new_messages: list[ToolMessage] = []
    sources: list[dict] = list(state["sources"])
    trace: list[dict] = list(state["trace"])
    seen_urls: set[str] = {s.get("url", "") for s in sources}
    turn = len(trace) + 1

    for call in last.tool_calls:
        fn_name = call["name"]
        args = call["args"] or {}
        func = TOOL_REGISTRY.get(fn_name)

        if func is None:
            obs = {"ok": False, "results": [], "message": f"未知工具：{fn_name}"}
        else:
            try:
                obs = func(**args)
            except Exception as exc:  # 工具异常不能让主循环崩溃
                obs = {"ok": False, "results": [], "message": f"工具异常：{type(exc).__name__}: {exc}"}

        obs_str = json.dumps(obs, ensure_ascii=False)

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

        new_messages.append(ToolMessage(content=obs_str, tool_call_id=call["id"]))

    return {"messages": new_messages, "sources": sources, "trace": trace}


# ---------- 条件路由 ----------

def should_continue(state: AgentState) -> str:
    """模型仍要调工具就回工具节点，否则收尾结束。"""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


# ---------- 编译图（模块级只编译一次） ----------

def _build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


_GRAPH = _build_graph()


# ---------- 对外入口 ----------

def run(user_query: str, verbose: bool = False) -> dict:
    """跑完整 Agent 图，返回结构化结果（契约与旧版一致）。"""
    initial_state: AgentState = {
        "messages": [
            SystemMessage(content=prompts.SYSTEM_PROMPT),
            HumanMessage(content=user_query),
        ],
        "sources": [],
        "trace": [],
    }
    # 每轮「模型 → 工具」占 2 步，留 1 步余量收尾
    recursion_limit = config.MAX_TURNS * 2 + 1

    final_state: AgentState | None = None
    truncated = False

    try:
        # 用 stream 逐轮取状态：超限时仍能保留已累积的来源与轨迹
        for chunk in _GRAPH.stream(
            initial_state,
            config={"recursion_limit": recursion_limit},
            stream_mode="values",
        ):
            final_state = chunk
            if verbose:
                last = chunk["messages"][-1]
                if isinstance(last, AIMessage) and last.tool_calls:
                    for tc in last.tool_calls:
                        print(f"  → {tc['name']}({tc['args']})")
    except GraphRecursionError:
        truncated = True
    except Exception as exc:  # 模型链路异常，结构化返回，不抛崩上层
        return {
            "ok": False,
            "status": "error",
            "answer": "",
            "sources": [],
            "trace": [],
            "turns": 0,
            "error": f"模型调用失败：{type(exc).__name__}: {exc}",
        }

    state = final_state or initial_state
    messages = state["messages"]
    sources = state["sources"]
    trace = state["trace"]

    last_message = messages[-1] if messages else None
    final_answer = last_message.content if isinstance(last_message, AIMessage) else ""
    final_answer = final_answer or ""

    model_turns = sum(1 for m in messages if isinstance(m, AIMessage))

    if truncated:
        return {
            "ok": True,
            "status": "truncated",
            "answer": "已达到最大检索轮数，资料收集停止；以下结论基于现有资料，可能不完整。",
            "sources": sources,
            "trace": trace,
            "turns": model_turns,
            "error": "",
        }

    return {
        "ok": True,
        "status": "ok",
        "answer": final_answer,
        "sources": sources,
        "trace": trace,
        "turns": model_turns,
        "error": "",
    }
