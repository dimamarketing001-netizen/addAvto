"""Автоповтор пополнения аккаунта Click.ru (2026-11-17, Dima).

Новые аккаунты Click.ru пополнять сразу нельзя: «Пополнение аккаунта
недоступно до 13:43:35 14.11.2026» (время московское, обычно через минуту-две
после создания). Такой отказ при запуске / «Пополнить и запустить» / «Пнуть»
ставит повтор на это время + запас; фоновый обработчик (main._topup_retry_loop)
пополняет на ту же сумму и запускает показы. Здесь — только хранение.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

MSK = timezone(timedelta(hours=3))
RETRY_MARGIN = timedelta(minutes=2)
MAX_ATTEMPTS = 6
_UNTIL_RE = re.compile(r"недоступно до\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+(\d{1,2}\.\d{1,2}\.\d{4})", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_until(text: str) -> datetime | None:
    """«…недоступно до 13:43:35 14.11.2026…» → UTC-время (Click.ru пишет по Москве)."""
    m = _UNTIL_RE.search(text or "")
    if not m:
        return None
    t, d = m.group(1), m.group(2)
    fmt = "%H:%M:%S %d.%m.%Y" if t.count(":") == 2 else "%H:%M %d.%m.%Y"
    try:
        return datetime.strptime(f"{t} {d}", fmt).replace(tzinfo=MSK).astimezone(timezone.utc)
    except ValueError:
        return None


def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS topup_retry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        campaign_id INTEGER,
        connection_id INTEGER,
        payer_id INTEGER,
        amount_rub REAL NOT NULL,
        start INTEGER NOT NULL DEFAULT 1,
        is_epk INTEGER NOT NULL DEFAULT 0,
        retry_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        last_note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_topup_retry_due ON topup_retry(status, retry_at)")


def schedule(project_id: int, account_id: int, campaign_id: int | None, connection_id: int | None,
             payer_id: int | None, amount_rub: float, start: bool, is_epk: bool, until: datetime,
             note: str = "") -> datetime:
    """Один ожидающий повтор на аккаунт: новый заменяет старый."""
    when = max(until + RETRY_MARGIN, _now() + timedelta(minutes=1))
    now = _now().isoformat()
    with db.connect() as c:
        _init(c)
        c.execute("UPDATE topup_retry SET status='cancelled', last_note='заменён новым повтором', updated_at=? "
                  "WHERE account_id=? AND status='pending'", (now, account_id))
        c.execute("INSERT INTO topup_retry(project_id, account_id, campaign_id, connection_id, payer_id, amount_rub, start, "
                  "is_epk, retry_at, last_note, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (project_id, account_id, campaign_id, connection_id, payer_id, float(amount_rub), int(bool(start)),
                   int(bool(is_epk)), when.isoformat(), note[:300], now, now))
    return when


def due(limit: int = 20) -> list[dict[str, Any]]:
    with db.connect() as c:
        _init(c)
        rows = c.execute("SELECT * FROM topup_retry WHERE status='pending' AND retry_at<=? ORDER BY retry_at LIMIT ?",
                         (_now().isoformat(), limit)).fetchall()
    return [dict(r) for r in rows]


def finish(retry_id: int, status: str, note: str) -> None:
    with db.connect() as c:
        _init(c)
        c.execute("UPDATE topup_retry SET status=?, last_note=?, attempts=attempts+1, updated_at=? WHERE id=?",
                  (status, note[:500], _now().isoformat(), retry_id))


def reschedule(retry_id: int, until: datetime, note: str) -> tuple[bool, datetime | None]:
    """Перенос; False — попытки кончились (помечено failed)."""
    with db.connect() as c:
        _init(c)
        row = c.execute("SELECT attempts FROM topup_retry WHERE id=?", (retry_id,)).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        now = _now().isoformat()
        if attempts >= MAX_ATTEMPTS:
            c.execute("UPDATE topup_retry SET status='failed', attempts=?, last_note=?, updated_at=? WHERE id=?",
                      (attempts, ("попытки кончились: " + note)[:500], now, retry_id))
            return False, None
        when = max(until + RETRY_MARGIN, _now() + timedelta(minutes=1))
        c.execute("UPDATE topup_retry SET retry_at=?, attempts=?, last_note=?, updated_at=? WHERE id=?",
                  (when.isoformat(), attempts, note[:500], now, retry_id))
    return True, when


def cancel_account(account_id: int, note: str) -> int:
    with db.connect() as c:
        _init(c)
        return c.execute("UPDATE topup_retry SET status='cancelled', last_note=?, updated_at=? WHERE account_id=? AND status='pending'",
                         (note[:300], _now().isoformat(), account_id)).rowcount


def cancel_project(project_id: int, note: str) -> int:
    with db.connect() as c:
        _init(c)
        return c.execute("UPDATE topup_retry SET status='cancelled', last_note=?, updated_at=? WHERE project_id=? AND status='pending'",
                         (note[:300], _now().isoformat(), project_id)).rowcount


def pending_for_account(account_id: int) -> dict[str, Any] | None:
    with db.connect() as c:
        _init(c)
        row = c.execute("SELECT * FROM topup_retry WHERE account_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                        (account_id,)).fetchone()
    return dict(row) if row else None


def list_project(project_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with db.connect() as c:
        _init(c)
        rows = c.execute("SELECT * FROM topup_retry WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit)).fetchall()
    return [dict(r) for r in rows]


def msk_hhmm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(MSK).strftime("%H:%M")
    except ValueError:
        return iso
