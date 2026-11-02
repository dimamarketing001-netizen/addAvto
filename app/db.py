from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_FIELDS = [
    "name", "domain", "campaign_mode", "target_drr",
    "keywords", "negative_keywords", "image_urls",
    "region_ids", "headlines", "ad_texts", "sitelinks_pool", "callouts_pool",
    "mobile_bid_adjustment_percent",
]
# strategy/budget/lead_value/headline/ad_text columns still exist in the
# `projects` table (old rows have real values) but are no longer written —
# they were decorative, never read by bulk-create (see ProjectIn's
# docstring in schemas.py, 2026-11-02).

_JSON_PROJECT_FIELDS = (
    "image_urls", "region_ids", "headlines", "ad_texts",
    "sitelinks_pool", "callouts_pool",
)


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
                region_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        existing_columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
        for json_field in ("headlines", "ad_texts", "sitelinks_pool", "callouts_pool"):
            if json_field not in existing_columns:
                db.execute(f"ALTER TABLE projects ADD COLUMN {json_field} TEXT NOT NULL DEFAULT '[]'")
        if "mobile_bid_adjustment_percent" not in existing_columns:
            db.execute("ALTER TABLE projects ADD COLUMN mobile_bid_adjustment_percent INTEGER")
        db.execute("""
            CREATE TABLE IF NOT EXISTS bulk_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                pool_login TEXT,
                account_id INTEGER,
                campaign_id INTEGER,
                connection_id INTEGER,
                payer_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        bulk_columns = {row["name"] for row in db.execute("PRAGMA table_info(bulk_campaigns)")}
        if "connection_id" not in bulk_columns:
            db.execute("ALTER TABLE bulk_campaigns ADD COLUMN connection_id INTEGER")
        if "payer_id" not in bulk_columns:
            db.execute("ALTER TABLE bulk_campaigns ADD COLUMN payer_id INTEGER")
        db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS clickru_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                token TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS bulk_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                requested INTEGER NOT NULL,
                created INTEGER NOT NULL DEFAULT 0,
                items TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            )
        """)
        # Per-user Yandex OAuth connections — NOT used by this app's own
        # admin bulk-create flow (that's pool_login-only, see
        # _metrika_context in main.py), but needed by the /lk "Конверсии в
        # Метрику" module: each lk member connects their OWN Yandex ID here
        # to pick a Metrika counter for Bitrix24 lead uploads (see lk.py).
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
                created_at TEXT NOT NULL,
                return_to TEXT NOT NULL DEFAULT '/'
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump_project(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in _JSON_PROJECT_FIELDS:
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
        if field in _JSON_PROJECT_FIELDS:
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
        if field in _JSON_PROJECT_FIELDS:
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


def record_bulk_campaign(
    project_id: int,
    pool_login: str | None,
    account_id: int | None,
    campaign_id: int | None,
    connection_id: int | None = None,
    payer_id: int | None = None,
) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO bulk_campaigns(project_id, pool_login, account_id, campaign_id, "
            "connection_id, payer_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, pool_login, account_id, campaign_id, connection_id, payer_id, _now()),
        )


def list_bulk_campaigns(project_id: int) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM bulk_campaigns WHERE project_id = ? ORDER BY id DESC", (project_id,)
        ).fetchall()
    return [dict(row) for row in rows]


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


# --- Click.ru connections: multiple master-account API tokens, selectable
# per launch (see main.py's ClickRUClient(token=...) override). Tokens are
# stored in plain SQLite, same trust level as CLICKRU_API_TOKEN already had
# in plain .env — not Fernet-encrypted (that machinery was removed with the
# old Metrika OAuth flow, see TASKS.md 2026-10-31).

def create_clickru_connection(label: str, token: str, user_id: str = "") -> dict[str, Any]:
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO clickru_connections(label, token, user_id, created_at) VALUES (?, ?, ?, ?)",
            (label, token, user_id, _now()),
        )
        connection_id = int(cursor.lastrowid)
        row = db.execute("SELECT * FROM clickru_connections WHERE id = ?", (connection_id,)).fetchone()
    assert row is not None
    return dict(row)


def list_clickru_connections() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM clickru_connections ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def get_clickru_connection(connection_id: int) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM clickru_connections WHERE id = ?", (connection_id,)).fetchone()
    return dict(row) if row else None


def delete_clickru_connection(connection_id: int) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM clickru_connections WHERE id = ?", (connection_id,))
        return cursor.rowcount > 0


# --- Bulk-create runs as a background job (2026-10-31) rather than inside
# the request/response cycle: provisioning N accounts one at a time through
# a real browser plus the Click.ru API can take many minutes, which risked
# a client/proxy timeout losing the final response even though the server
# kept working and persisting bulk_campaigns rows regardless. The job row
# here is what the UI polls for progress instead.

def _dump_job(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["items"] = json.loads(item["items"] or "[]")
    return item


def create_bulk_job(project_id: int, requested: int) -> int:
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO bulk_jobs(project_id, status, requested, created, items, started_at) "
            "VALUES (?, 'running', ?, 0, '[]', ?)",
            (project_id, requested, _now()),
        )
        return int(cursor.lastrowid)


def has_bulk_job(project_id: int) -> bool:
    """True once at least one bulk-create job (any status) has ever been
    started for this project — used to lock project editing after launch
    (см. ProjectIn в main.py: редактирование разрешено только до первого
    запуска, иначе уже созданные кампании и отредактированные позже
    настройки проекта расходятся между собой)."""
    with connect() as db:
        row = db.execute("SELECT 1 FROM bulk_jobs WHERE project_id = ? LIMIT 1", (project_id,)).fetchone()
    return row is not None


def get_running_bulk_job(project_id: int) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM bulk_jobs WHERE project_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return _dump_job(row) if row else None


def get_bulk_job(job_id: int) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM bulk_jobs WHERE id = ?", (job_id,)).fetchone()
    return _dump_job(row) if row else None


def append_bulk_job_item(job_id: int, item: dict[str, Any]) -> None:
    job = get_bulk_job(job_id)
    if job is None:
        return
    items = job["items"]
    items.append(item)
    created = sum(1 for entry in items if entry.get("status") == "created")
    with connect() as db:
        db.execute(
            "UPDATE bulk_jobs SET items = ?, created = ? WHERE id = ?",
            (json.dumps(items, ensure_ascii=False), created, job_id),
        )


def finish_bulk_job(job_id: int, status: str, error: str | None = None) -> None:
    with connect() as db:
        db.execute(
            "UPDATE bulk_jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, _now(), job_id),
        )


# --- Per-user Yandex OAuth connections, used by /lk (see init_db() above
# for why this exists alongside the pool-only admin flow).

def save_metrika_oauth_state(
    state_hash: str,
    owner_username: str,
    label: str,
    code_verifier_enc: str,
    expires_at: str,
    return_to: str = "/",
) -> None:
    with connect() as db:
        db.execute("DELETE FROM metrika_oauth_states WHERE expires_at < ?", (_now(),))
        db.execute(
            """INSERT INTO metrika_oauth_states
               (state_hash, owner_username, label, code_verifier_enc, expires_at, created_at, return_to)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (state_hash, owner_username, label, code_verifier_enc, expires_at, _now(), return_to),
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

