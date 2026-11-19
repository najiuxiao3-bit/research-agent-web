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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quest_runs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at     TEXT NOT NULL,
                quest_id       TEXT NOT NULL,
                name           TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT 'pending',
                message        TEXT NOT NULL DEFAULT '',
                detail         TEXT NOT NULL DEFAULT '',
                candidates_json TEXT NOT NULL DEFAULT '[]',
                screenshot_url TEXT NOT NULL DEFAULT '',
                logged_in      INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platforms (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at     TEXT NOT NULL,
                name           TEXT NOT NULL,
                url            TEXT NOT NULL,
                description    TEXT NOT NULL DEFAULT '',
                login_required INTEGER NOT NULL DEFAULT 1,
                enabled        INTEGER NOT NULL DEFAULT 1,
                note           TEXT NOT NULL DEFAULT ''
            )
            """
        )

    _seed_default_platform()


def _seed_default_platform() -> None:
    """平台表为空时写入一条禁用的示例占位，便于首次打开看到结构。"""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM platforms").fetchone()
        if row["n"] > 0:
            return
    save_platform(
        {
            "name": "示例平台（待配置）",
            "url": "https://example.com",
            "description": "在右侧表单添加真实平台网址；进入后会自动探测签到 / 领取入口。",
            "login_required": True,
            "enabled": False,
            "note": "占位模板：未配置真实入口前不会被批量运行执行。",
        }
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


def clear_all_records() -> int:
    """清空全部历史会话（连同答案、来源、轨迹一并删除），返回删除条数。"""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM records")
        return cur.rowcount


def clear_sources() -> int:
    """清空所有记录里保存的来源引用，保留问答正文与轨迹，返回受影响条数。"""
    with _connect() as conn:
        cur = conn.execute("UPDATE records SET sources_json = '[]'")
        return cur.rowcount


# ---------- 任务中心：签到 / 领额度运行记录 ----------

def save_quest_run(result: dict) -> int:
    """保存一条任务运行结果，返回自增 id。"""
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO quest_runs
                (created_at, quest_id, name, status, message, detail,
                 candidates_json, screenshot_url, logged_in)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                result.get("id", ""),
                result.get("name", ""),
                result.get("status", "error"),
                result.get("message", ""),
                result.get("detail", ""),
                json.dumps(result.get("candidates", []), ensure_ascii=False),
                result.get("screenshot_url", ""),
                1 if result.get("logged_in") else 0,
            ),
        )
        return int(cur.lastrowid)


def latest_quest_runs() -> dict[str, dict]:
    """返回每个任务最近一次运行结果，键为 quest_id。"""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT q.* FROM quest_runs q
            JOIN (
                SELECT quest_id, MAX(id) AS max_id FROM quest_runs GROUP BY quest_id
            ) t ON q.id = t.max_id
            """
        ).fetchall()

    result: dict[str, dict] = {}
    for row in rows:
        result[row["quest_id"]] = {
            "id": row["quest_id"],
            "name": row["name"],
            "status": row["status"],
            "message": row["message"],
            "detail": row["detail"],
            "candidates": json.loads(row["candidates_json"] or "[]"),
            "screenshot_url": row["screenshot_url"],
            "logged_in": bool(row["logged_in"]),
            "ran_at": row["created_at"],
        }
    return result


# ---------- 任务中心：平台配置（页面可视化维护） ----------

def _platform_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "name": row["name"],
        "url": row["url"],
        "description": row["description"],
        "login_required": bool(row["login_required"]),
        "enabled": bool(row["enabled"]),
        "note": row["note"],
    }


def list_platforms() -> list[dict]:
    """返回全部平台配置，按创建时间正序。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM platforms ORDER BY id ASC"
        ).fetchall()
    return [_platform_row(r) for r in rows]


def get_platform(platform_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM platforms WHERE id = ?", (platform_id,)
        ).fetchone()
    return _platform_row(row) if row else None


def save_platform(data: dict) -> int:
    """新增一条平台配置，返回自增 id。"""
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO platforms
                (created_at, name, url, description, login_required, enabled, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                (data.get("name") or "").strip(),
                (data.get("url") or "").strip(),
                data.get("description", ""),
                1 if data.get("login_required", True) else 0,
                1 if data.get("enabled", True) else 0,
                data.get("note", ""),
            ),
        )
        return int(cur.lastrowid)


def update_platform(platform_id: int, data: dict) -> bool:
    """按 id 更新平台配置；存在并更新成功返回 True。"""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE platforms
            SET name = ?, url = ?, description = ?, login_required = ?,
                enabled = ?, note = ?
            WHERE id = ?
            """,
            (
                (data.get("name") or "").strip(),
                (data.get("url") or "").strip(),
                data.get("description", ""),
                1 if data.get("login_required", True) else 0,
                1 if data.get("enabled", True) else 0,
                data.get("note", ""),
                platform_id,
            ),
        )
        return cur.rowcount > 0


def delete_platform(platform_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM platforms WHERE id = ?", (platform_id,))
        return cur.rowcount > 0
