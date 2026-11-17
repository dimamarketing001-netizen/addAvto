"""Жизнь рекламных аккаунтов и автозамена «умерших» (2026-11-17, Dima).

Раз в день (main._lifecycle_loop, утро МСК):
  1. показы/клики/расход за последние дни по всем аккаунтам проектов —
     Click.ru /stat (1 запрос на подключение) → account_daily;
  2. «умер» = вчера 0 показов, аккаунту ≥ MIN_AGE_DAYS, не ждёт пополнения.
     Если у проекта включена автозамена — кампания останавливается
     (без этого Click.ru не даёт вернуть деньги) и заводится новый аккаунт с
     теми же настройками и креативами (запуск из параметров последнего);
  3. остановленные ≥ REFUND_WAIT_DAYS назад → GET /payments/refund_available,
     AVAILABLE → POST /payments/refund (возврат ВСЕГО остатка на баланс
     Click.ru, Click.ru обрабатывает до 7 рабочих дней); баланс стал 0 →
     «деньги возвращены».
Здесь — только хранение и правила; обращения к Click.ru — в main.py.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import db

MSK = timezone(timedelta(hours=3))
MIN_AGE_DAYS = 2           # моложе — ещё могут идти модерация и раскачка
REFUND_WAIT_DAYS = 2       # Click.ru: «возврат возможен минимум через 2 дня после остановки»
DEFAULT_MAX_PER_DAY = 5    # замен в день на проект

STATES = {
    "active": "Работает",
    "dead": "0 показов — ждёт замены",
    "stopped": "Остановлен, ждёт возврата денег",
    "refund_requested": "Заявка на возврат подана",
    "refunded": "Деньги возвращены",
    "closed": "Закрыт (денег не было)",
    "refund_failed": "Возврат не удался",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_msk() -> date:
    return datetime.now(MSK).date()


def _init(c) -> None:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS account_daily (
        account_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        impressions INTEGER NOT NULL DEFAULT 0,
        clicks INTEGER NOT NULL DEFAULT 0,
        spend REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (account_id, day)
    );
    CREATE TABLE IF NOT EXISTS account_life (
        account_id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'active',
        died_at TEXT,
        death_reason TEXT NOT NULL DEFAULT '',
        stopped_at TEXT,
        balance_at_stop REAL,
        refund_requested_at TEXT,
        refunded_at TEXT,
        refund_note TEXT NOT NULL DEFAULT '',
        replacement_job_id INTEGER,
        replacement_note TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS project_autoreplace (
        project_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        max_per_day INTEGER NOT NULL DEFAULT 5,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS lifecycle_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        trigger TEXT NOT NULL DEFAULT 'schedule',
        summary TEXT NOT NULL DEFAULT ''
    );
    """)


# ───────── настройки проекта ─────────

def get_settings(project_id: int) -> dict[str, Any]:
    with db.connect() as c:
        _init(c)
        row = c.execute("SELECT * FROM project_autoreplace WHERE project_id=?", (project_id,)).fetchone()
    return {"enabled": bool(row["enabled"]) if row else False,
            "max_per_day": int(row["max_per_day"]) if row else DEFAULT_MAX_PER_DAY}


def set_settings(project_id: int, enabled: bool, max_per_day: int) -> dict[str, Any]:
    with db.connect() as c:
        _init(c)
        c.execute("INSERT INTO project_autoreplace(project_id, enabled, max_per_day, updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(project_id) DO UPDATE SET enabled=excluded.enabled, max_per_day=excluded.max_per_day, "
                  "updated_at=excluded.updated_at", (project_id, int(bool(enabled)), int(max_per_day), now_utc().isoformat()))
    return get_settings(project_id)


# ───────── статистика по дням ─────────

def upsert_daily(rows: list[tuple[int, str, int, int, float]]) -> None:
    if not rows:
        return
    with db.connect() as c:
        _init(c)
        c.executemany("INSERT INTO account_daily(account_id, day, impressions, clicks, spend) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(account_id, day) DO UPDATE SET impressions=excluded.impressions, "
                      "clicks=excluded.clicks, spend=excluded.spend", rows)


def daily_for(account_ids: list[int], since: date) -> dict[int, dict[str, dict[str, Any]]]:
    out: dict[int, dict[str, dict[str, Any]]] = {}
    if not account_ids:
        return out
    with db.connect() as c:
        _init(c)
        marks = ",".join("?" * len(account_ids))
        for r in c.execute(f"SELECT * FROM account_daily WHERE account_id IN ({marks}) AND day>=?",
                           (*account_ids, since.isoformat())):
            out.setdefault(int(r["account_id"]), {})[r["day"]] = dict(r)
    return out


def totals_for(account_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Сумма за всю историю + первый/последний день с показами."""
    out: dict[int, dict[str, Any]] = {}
    if not account_ids:
        return out
    with db.connect() as c:
        _init(c)
        marks = ",".join("?" * len(account_ids))
        for r in c.execute(
            f"SELECT account_id, SUM(impressions) imp, SUM(clicks) clk, SUM(spend) sp, "
            f"MIN(CASE WHEN impressions>0 THEN day END) first_day, MAX(CASE WHEN impressions>0 THEN day END) last_day "
            f"FROM account_daily WHERE account_id IN ({marks}) GROUP BY account_id", tuple(account_ids)):
            out[int(r["account_id"])] = {"impressions": r["imp"] or 0, "clicks": r["clk"] or 0,
                                         "spend": round(r["sp"] or 0, 2), "first_impression_day": r["first_day"],
                                         "last_impression_day": r["last_day"]}
    return out


# ───────── состояние аккаунта ─────────

def get_life(account_id: int) -> dict[str, Any] | None:
    with db.connect() as c:
        _init(c)
        row = c.execute("SELECT * FROM account_life WHERE account_id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def lives_for(account_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not account_ids:
        return {}
    with db.connect() as c:
        _init(c)
        marks = ",".join("?" * len(account_ids))
        return {int(r["account_id"]): dict(r) for r in
                c.execute(f"SELECT * FROM account_life WHERE account_id IN ({marks})", tuple(account_ids))}


def set_life(account_id: int, project_id: int, **fields: Any) -> None:
    fields["updated_at"] = now_utc().isoformat()
    with db.connect() as c:
        _init(c)
        if c.execute("SELECT 1 FROM account_life WHERE account_id=?", (account_id,)).fetchone() is None:
            c.execute("INSERT INTO account_life(account_id, project_id, updated_at) VALUES(?,?,?)",
                      (account_id, project_id, fields["updated_at"]))
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE account_life SET {cols} WHERE account_id=?", (*fields.values(), account_id))


def replacements_today(project_id: int) -> int:
    start = datetime.combine(today_msk(), datetime.min.time(), MSK).astimezone(timezone.utc).isoformat()
    with db.connect() as c:
        _init(c)
        return c.execute("SELECT COUNT(*) FROM account_life WHERE project_id=? AND replacement_job_id IS NOT NULL "
                         "AND died_at>=?", (project_id, start)).fetchone()[0]


def is_dead(daily: dict[str, dict[str, Any]], created_at: str | None, yesterday: date) -> tuple[bool, str]:
    """Правило «умер»: вчера 0 показов и аккаунту ≥ MIN_AGE_DAYS."""
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).astimezone(MSK).date() if created_at else None
    except ValueError:
        created = None
    if created is None or (yesterday - created).days + 1 < MIN_AGE_DAYS:
        return False, ""
    y = daily.get(yesterday.isoformat())
    if y is None:
        return False, ""  # нет данных за вчера — не решаем
    if int(y.get("impressions") or 0) > 0:
        return False, ""
    ever = [d for d, v in daily.items() if int(v.get("impressions") or 0) > 0]
    reason = (f"0 показов за {yesterday.strftime('%d.%m')}, последние показы {max(ever)[8:10]}.{max(ever)[5:7]}"
              if ever else f"0 показов за {yesterday.strftime('%d.%m')} и ни одного показа с запуска")
    return True, reason


def log_run(trigger: str, summary: str, started: datetime) -> None:
    with db.connect() as c:
        _init(c)
        c.execute("INSERT INTO lifecycle_runs(started_at, finished_at, trigger, summary) VALUES(?,?,?,?)",
                  (started.isoformat(), now_utc().isoformat(), trigger, summary[:2000]))


def last_runs(limit: int = 10) -> list[dict[str, Any]]:
    with db.connect() as c:
        _init(c)
        return [dict(r) for r in c.execute("SELECT * FROM lifecycle_runs ORDER BY id DESC LIMIT ?", (limit,))]
