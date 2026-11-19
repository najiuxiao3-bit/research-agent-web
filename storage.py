# -*- coding: utf-8 -*-
"""
SQLite 存储层：保存每次提问、检索来源、生成的摘要与工具轨迹。

数据库文件落在项目根的 data/ 目录（已在 .gitignore 排除），
单文件、零额外依赖，适合这个规模的演示项目。
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import config

DB_PATH = Path(__file__).parent / "data" / "records.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（若不存在）。在后端启动时调用一次。"""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL,
                query       TEXT NOT NULL,
                answer      TEXT NOT NULL,
                sources_json TEXT NOT NULL DEFAULT '[]',
                trace_json  TEXT NOT NULL DEFAULT '[]',
                model       TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'ok'
            )
            """
        )


def save_record(
    query: str,
    answer: str,
    sources: list[dict],
    trace: list[dict],
    status: str,
) -> int:
    """保存一条记录，返回自增 id。"""
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO records (created_at, query, answer, sources_json, trace_json, model, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                query,
                answer,
                json.dumps(sources, ensure_ascii=False),
                json.dumps(trace, ensure_ascii=False),
                config.LLM_MODEL,
                status,
            ),
        )
        return int(cur.lastrowid)


def list_records(limit: int = 20) -> list[dict]:
    """历史列表：按时间倒序，只带摘要片段，不返回完整轨迹。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, query, answer, sources_json, model, status "
            "FROM records ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    result = []
    for row in rows:
        sources = json.loads(row["sources_json"] or "[]")
        result.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "query": row["query"],
                "answer_preview": (row["answer"] or "")[:120],
                "n_sources": len(sources),
                "model": row["model"],
                "status": row["status"],
            }
        )
    return result


def get_record(record_id: int) -> dict | None:
    """按 id 取完整记录。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM records WHERE id = ?", (record_id,)
        ).fetchone()
    if row is None:
        return None

    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "query": row["query"],
        "answer": row["answer"],
        "sources": json.loads(row["sources_json"] or "[]"),
        "trace": json.loads(row["trace_json"] or "[]"),
        "model": row["model"],
        "status": row["status"],
    }
