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
        db.execute("""
            CREATE TABLE IF NOT EXISTS metrika_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL,
                label TEXT NOT NULL,
                access_token_enc TEXT NOT NULL,
                refresh_token_enc TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                scopes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'connected',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrika_connections_owner
            ON metrika_connections(owner_username, id)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS metrika_oauth_states (
                state_hash TEXT PRIMARY KEY,
                owner_username TEXT NOT NULL,
                label TEXT NOT NULL,
                code_verifier_enc TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
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



def save_metrika_oauth_state(
    state_hash: str,
    owner_username: str,
    label: str,
    code_verifier_enc: str,
    expires_at: str,
) -> None:
    with connect() as db:
        db.execute("DELETE FROM metrika_oauth_states WHERE expires_at < ?", (_now(),))
        db.execute(
            """INSERT INTO metrika_oauth_states
               (state_hash, owner_username, label, code_verifier_enc, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (state_hash, owner_username, label, code_verifier_enc, expires_at, _now()),
        )


def consume_metrika_oauth_state(state_hash: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM metrika_oauth_states WHERE state_hash = ?",
            (state_hash,),
        ).fetchone()
        if row is None:
            return None
        # Delete regardless of expiry: an OAuth state is single-use.
        db.execute("DELETE FROM metrika_oauth_states WHERE state_hash = ?", (state_hash,))
        if row["expires_at"] < _now():
            return None
        return dict(row)


def create_metrika_connection(
    owner_username: str,
    label: str,
    access_token_enc: str,
    refresh_token_enc: str = "",
    expires_at: str | None = None,
    scopes: str = "",
) -> dict[str, Any]:
    now = _now()
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO metrika_connections
               (owner_username, label, access_token_enc, refresh_token_enc, expires_at,
                scopes, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'connected', ?, ?)""",
            (owner_username, label, access_token_enc, refresh_token_enc, expires_at, scopes, now, now),
        )
        connection_id = int(cursor.lastrowid)
        row = db.execute(
            "SELECT * FROM metrika_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def list_metrika_connections(owner_username: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """SELECT id, owner_username, label, expires_at, scopes, status, created_at, updated_at
               FROM metrika_connections WHERE owner_username = ? ORDER BY id DESC""",
            (owner_username,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_metrika_connection(connection_id: int, owner_username: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            """SELECT * FROM metrika_connections
               WHERE id = ? AND owner_username = ?""",
            (connection_id, owner_username),
        ).fetchone()
    return dict(row) if row else None


def update_metrika_connection_tokens(
    connection_id: int,
    owner_username: str,
    access_token_enc: str,
    refresh_token_enc: str,
    expires_at: str | None,
    scopes: str,
) -> bool:
    with connect() as db:
        cursor = db.execute(
            """UPDATE metrika_connections
               SET access_token_enc = ?, refresh_token_enc = ?, expires_at = ?,
                   scopes = ?, status = 'connected', updated_at = ?
               WHERE id = ? AND owner_username = ?""",
            (access_token_enc, refresh_token_enc, expires_at, scopes, _now(), connection_id, owner_username),
        )
    return cursor.rowcount > 0


def update_metrika_connection_status(
    connection_id: int,
    owner_username: str,
    status: str,
) -> bool:
    with connect() as db:
        cursor = db.execute(
            """UPDATE metrika_connections SET status = ?, updated_at = ?
               WHERE id = ? AND owner_username = ?""",
            (status, _now(), connection_id, owner_username),
        )
    return cursor.rowcount > 0


def delete_metrika_connection(connection_id: int, owner_username: str) -> bool:
    with connect() as db:
        cursor = db.execute(
            "DELETE FROM metrika_connections WHERE id = ? AND owner_username = ?",
            (connection_id, owner_username),
        )
    return cursor.rowcount > 0
