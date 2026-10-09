from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_FIELDS = [
    "name", "domain", "campaign_mode", "strategy", "target_drr", "budget", "lead_value",
    "headline", "ad_text", "keywords", "negative_keywords", "image_urls", "account_ids",
    "counter_id", "goal_id", "region_ids",
]


def _db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", "./data/app.db"))


def connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def init_db() -> None:
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                domain TEXT NOT NULL,
                campaign_mode TEXT NOT NULL DEFAULT 'separate',
                strategy TEXT NOT NULL DEFAULT 'AVERAGE_CRR',
                target_drr REAL NOT NULL DEFAULT 30,
                budget REAL NOT NULL DEFAULT 20000,
                lead_value REAL NOT NULL DEFAULT 0,
                headline TEXT NOT NULL DEFAULT '',
                ad_text TEXT NOT NULL DEFAULT '',
                keywords TEXT NOT NULL DEFAULT '',
                negative_keywords TEXT NOT NULL DEFAULT '',
                image_urls TEXT NOT NULL DEFAULT '[]',
                account_ids TEXT NOT NULL DEFAULT '[]',
                counter_id INTEGER,
                goal_id INTEGER,
                region_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump_project(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("image_urls", "account_ids", "region_ids"):
        item[key] = json.loads(item[key] or "[]")
    return item


def list_projects() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC").fetchall()
    return [_dump_project(row) for row in rows]


def get_project(project_id: int) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _dump_project(row) if row else None


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    fields = PROJECT_FIELDS
    values: list[Any] = []
    for field in fields:
        value = data.get(field)
        if field in ("image_urls", "account_ids", "region_ids"):
            value = json.dumps(value or [], ensure_ascii=False)
        values.append(value)
    placeholders = ", ".join("?" for _ in fields)
    columns = ", ".join(fields)
    with connect() as db:
        cursor = db.execute(
            f"INSERT INTO projects ({columns}, created_at, updated_at) VALUES ({placeholders}, ?, ?)",
            [*values, now, now],
        )
        project_id = int(cursor.lastrowid)
    project = get_project(project_id)
    assert project is not None
    return project


def update_project(project_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    if not get_project(project_id):
        return None
    assignments: list[str] = []
    values: list[Any] = []
    for field in PROJECT_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field in ("image_urls", "account_ids", "region_ids"):
            value = json.dumps(value or [], ensure_ascii=False)
        assignments.append(f"{field} = ?")
        values.append(value)
    if not assignments:
        return get_project(project_id)
    assignments.append("updated_at = ?")
    values.extend([_now(), project_id])
    with connect() as db:
        db.execute(f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?", values)
    return get_project(project_id)


def delete_project(project_id: int) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0


def get_setting(key: str, default: Any = None) -> Any:
    with connect() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with connect() as db:
        db.execute(
            """INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, json.dumps(value, ensure_ascii=False), _now()),
        )
