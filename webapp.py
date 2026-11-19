# -*- coding: utf-8 -*-
"""
FastAPI Web 后端：把命令行 Agent 包装成可交互的 HTTP 服务。

接口：
    GET  /                 前端页面
    POST /api/ask          提交问题，跑 Agent 并落库，返回摘要与来源
    GET /api/history      历史记录列表
    GET /api/history/{id} 单条记录详情

启动：
    uvicorn webapp:app --host 0.0.0.0 --port 8000
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
import storage
from agent import run

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="资料查询总结助手")


class AskRequest(BaseModel):
    query: str


@app.on_event("startup")
def _startup() -> None:
    storage.init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


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
        # 失败也留一条 error 记录，便于排查
        storage.save_record(query, "", [], result["trace"], "error")
        raise HTTPException(status_code=502, detail=result["error"] or "模型调用失败")

    sources = result["sources"][:10]
    record_id = storage.save_record(
        query,
        result["answer"],
        sources,
        result["trace"],
        result["status"],
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


@app.get("/api/history")
def history(limit: int = 20) -> dict:
    return {"records": storage.list_records(limit)}


@app.get("/api/history/{record_id}")
def detail(record_id: int) -> dict:
    record = storage.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return record
