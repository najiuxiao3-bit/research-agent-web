# -*- coding: utf-8 -*-
"""
FastAPI Web 后端：把命令行 Agent 包装成可交互的 HTTP 服务。

接口：
    GET  /                  前端页面
    POST /api/ask           一次性返回完整结果（回退兼容）
    POST /api/ask/stream    SSE 事件流：状态 → 答案分片 → 来源 → 完成
    POST /api/browser/open  独立浏览器自动化（不走 Agent、不落库）
    GET  /api/history       历史记录列表
    GET  /api/history/{id}  单条记录详情

启动：
    uvicorn webapp:app --host 0.0.0.0 --port 8000
"""

import json
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import storage
from agent import run
from tools.browser import open_first_result

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="资料查询总结助手")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AskRequest(BaseModel):
    query: str


class BrowserRequest(BaseModel):
    keyword: str


@app.on_event("startup")
def _startup() -> None:
    storage.init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ---------- 一次性接口（兼容回退） ----------

@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="问题不能为空")
    if len(query) > 1000:
        raise HTTPException(status_code=400, detail="问题过长，请控制在 1000 字以内")

    err = config.check_config()
    if err:
        raise HTTPException(status_code=503, detail=err)

    result = run(query)

    if not result["ok"]:
        storage.save_record(query, "", [], result["trace"], "error")
        raise HTTPException(status_code=502, detail=result["error"] or "模型调用失败")

    sources = result["sources"][:10]
    record_id = storage.save_record(
        query, result["answer"], sources, result["trace"], result["status"]
    )
    record = storage.get_record(record_id)

    return {
        "id": record_id,
        "answer": result["answer"],
        "sources": sources,
        "trace": result["trace"],
        "turns": result["turns"],
        "status": result["status"],
        "created_at": record["created_at"],
    }


# ---------- SSE 流式接口 ----------

def _sse(event: str, data: dict) -> str:
    """组装一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _split_chunks(text: str, max_len: int = 40) -> list[str]:
    """按句子边界切片，超长句再按长度硬切，便于逐段推送。"""
    parts = re.split(r"(?<=[。！？\n；])", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if len(buf) + len(part) <= max_len:
            buf += part
            continue
        if buf:
            chunks.append(buf)
        # 单句仍超长，按 max_len 硬切
        while len(part) > max_len:
            chunks.append(part[:max_len])
            part = part[max_len:]
        buf = part
    if buf:
        chunks.append(buf)
    return chunks


def _stream_generator(query: str):
    """SSE 事件流：status → sources → 逐片 delta → done / error。"""
    yield _sse("status", {"text": "正在联网检索与提炼，请稍候…"})

    result = run(query)

    if not result["ok"]:
        storage.save_record(query, "", [], result["trace"], "error")
        yield _sse("error", {"detail": result["error"] or "模型调用失败"})
        return

    sources = result["sources"][:10]
    record_id = storage.save_record(
        query, result["answer"], sources, result["trace"], result["status"]
    )
    record = storage.get_record(record_id)

    yield _sse("sources", {"sources": sources})

    for chunk in _split_chunks(result["answer"]):
        yield _sse("delta", {"text": chunk})
        time.sleep(0.012)  # 控制打字机节奏

    yield _sse(
        "done",
        {
            "id": record_id,
            "turns": result["turns"],
            "status": result["status"],
            "created_at": record["created_at"],
        },
    )


@app.post("/api/ask/stream")
def ask_stream(req: AskRequest) -> StreamingResponse:
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="问题不能为空")
    if len(query) > 1000:
        raise HTTPException(status_code=400, detail="问题过长，请控制在 1000 字以内")

    err = config.check_config()
    if err:
        raise HTTPException(status_code=503, detail=err)

    return StreamingResponse(
        _stream_generator(query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------- 独立浏览器自动化（不经过 Agent、不写调研库） ----------

@app.post("/api/browser/open")
def browser_open(req: BrowserRequest) -> dict:
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="关键词不能为空")
    if len(keyword) > 100:
        raise HTTPException(status_code=400, detail="关键词过长，请控制在 100 字以内")

    result = open_first_result(keyword)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result.get("message") or "浏览器操作失败")
    return result


# ---------- 历史 ----------

@app.get("/api/history")
def history(limit: int = 20) -> dict:
    return {"records": storage.list_records(limit)}


@app.get("/api/history/{record_id}")
def detail(record_id: int) -> dict:
    record = storage.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return record
