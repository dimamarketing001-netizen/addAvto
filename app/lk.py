"""ЛК «Конверсии в Метрику».

Берёт лиды из одного или нескольких Битрикс24, по ID рекламной кампании (UTM_CAMPAIGN)
находит счётчик Метрики и отправляет офлайн-конверсию с целью lead.
Формат загрузки и правила поиска идентификаторов повторяют скрипт offCon-hourly.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import socket
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Awaitable, Callable, Literal
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import db
from .metrika_client import MetrikaAPIError, YandexMetrikaClient
from .token_crypto import TokenCryptoError, decrypt_secret, encrypt_secret

log = logging.getLogger("lk")

POLL_SECONDS = max(120, int(os.getenv("LK_POLL_SECONDS", "600")))
LEAD_GOAL = "lead"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")

_run_lock = asyncio.Lock()


class Member(BaseModel):
    username: str
    is_admin: bool = False


# ───────────────────────── БД ─────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_lk_db() -> None:
    with db.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS app_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS b24_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT NOT NULL,
            name TEXT NOT NULL,
            webhook_enc TEXT NOT NULL,
            lookback_hours INTEGER NOT NULL DEFAULT 24,
            mode TEXT NOT NULL DEFAULT 'simple',
            score_field TEXT NOT NULL DEFAULT '',
            scored_flag_field TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            dry_run INTEGER NOT NULL DEFAULT 1,
            send_backlog INTEGER NOT NULL DEFAULT 0,
            baselined INTEGER NOT NULL DEFAULT 0,
            last_run_at TEXT,
            last_status TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_b24_sources_owner ON b24_sources(owner_username);
        CREATE TABLE IF NOT EXISTS campaign_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT NOT NULL,
            campaign_key TEXT NOT NULL,
            connection_id INTEGER NOT NULL,
            counter_id INTEGER NOT NULL,
            goal_id INTEGER,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(owner_username, campaign_key)
        );
        CREATE TABLE IF NOT EXISTS lead_state (
            source_id INTEGER NOT NULL,
            lead_id TEXT NOT NULL,
            last_value INTEGER,
            dry_value INTEGER,
            skip_reason TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (source_id, lead_id)
        );
        CREATE TABLE IF NOT EXISTS unmapped_campaigns (
            source_id INTEGER NOT NULL,
            owner_username TEXT NOT NULL,
            campaign_key TEXT NOT NULL,
            raw_value TEXT NOT NULL DEFAULT '',
            leads INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            PRIMARY KEY (source_id, campaign_key)
        );
        CREATE TABLE IF NOT EXISTS conv_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            lead_id TEXT NOT NULL,
            campaign_key TEXT NOT NULL DEFAULT '',
            counter_id INTEGER,
            ident_type TEXT NOT NULL DEFAULT '',
            revenue INTEGER,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conv_log_owner ON conv_log(owner_username, id DESC);
        """)
        have = {r[1] for r in c.execute("PRAGMA table_info(b24_sources)")}
        for col in (*MARK_COLUMNS, "marks_status"):
            if col not in have:
                c.execute(f"ALTER TABLE b24_sources ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")


# ───────────────────── служебные поля лида в Б24 ─────────────────────

# колонка источника → (имя нового поля, допустимые типы [первый — для создания], название, как это поле зовётся у offCon).
# Флаг и сумма — те же поля, что заполняет offCon-hourly (metrika_sent_score и Score): ищем по названию и берём их ключи.
# «Статус отправки» у старого скрипта не было — его создаём (Score_LOG старый скрипт использует под свой лог).
MARK_SPECS: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]] = {
    "mark_sent": ("LK_METRIKA_SENT", ("boolean",), "metrika_sent_score",
                  ("metrika_sent_score", "UF_CRM_LEAD_METRIKA_SENT_score", "Отправлено в Метрику",
                   "Лид отправлен в Метрику", "Метрика отправлено")),
    "mark_value": ("LK_METRIKA_VALUE", ("double", "string", "integer"), "Score",
                   ("Score", "UF_CRM_LEAD_SCORE", "Метрика: сумма конверсии", "Сумма в Метрике")),
    "mark_info": ("LK_METRIKA_INFO", ("string",), "Метрика: статус отправки",
                  ("Метрика: статус отправки", "Метрика статус", "Metrika status")),
}
MARK_COLUMNS = tuple(MARK_SPECS)


def _norm_label(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", str(value or "").lower().replace("ё", "е")).strip()


def _field_labels(field: dict[str, Any], meta: dict[str, Any] | None = None) -> set[str]:
    """Названия поля: из userfield.list и (у большинства порталов они только тут) из crm.lead.fields."""
    out: set[str] = {_norm_label(field.get("FIELD_NAME"))}
    for key in ("EDIT_FORM_LABEL", "LIST_COLUMN_LABEL", "LIST_FILTER_LABEL"):
        val = field.get(key)
        for item in (val.values() if isinstance(val, dict) else [val]):
            if item:
                out.add(_norm_label(item))
    for key in ("formLabel", "listLabel", "filterLabel", "title"):
        if (meta or {}).get(key):
            out.add(_norm_label(meta[key]))
    out.discard("")
    return out


def marks_ready(src: dict[str, Any]) -> bool:
    return all(src.get(col) for col in MARK_COLUMNS)


async def ensure_mark_fields(webhook: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Находит в Б24 поля для отметок по названию, недостающие создаёт. Возвращает (колонка → ключ поля, отчёт)."""
    fields: list[dict[str, Any]] = []
    start = 0
    while True:
        data = await b24_call(webhook, "crm.lead.userfield.list", {"order": {"SORT": "ASC"}, "start": start})
        batch = data.get("result") or []
        fields.extend(batch)
        if not batch or data.get("next") is None:
            break
        start = int(data["next"])
    metas = (await b24_call(webhook, "crm.lead.fields", {})).get("result") or {}
    keys: dict[str, str] = {}
    report: list[dict[str, str]] = []
    for col, (name, types, label, aliases) in MARK_SPECS.items():
        utype = types[0]
        wanted = {_norm_label(label), *(_norm_label(a) for a in aliases)}
        found = next((f for f in fields if f.get("FIELD_NAME") == "UF_CRM_" + name), None) or next(
            (f for f in fields if f.get("USER_TYPE_ID") in types and _field_labels(f, metas.get(f.get("FIELD_NAME"))) & wanted), None)
        if found:
            keys[col] = found["FIELD_NAME"]
            report.append({"field": label, "key": found["FIELD_NAME"], "action": "found", "detail": "уже есть в Б24"})
            continue
        try:
            await b24_call(webhook, "crm.lead.userfield.add", {"fields": {
                "FIELD_NAME": name, "USER_TYPE_ID": utype, "XML_ID": name,
                "EDIT_FORM_LABEL": {"ru": label, "en": label},
                "LIST_COLUMN_LABEL": {"ru": label, "en": label},
                "LIST_FILTER_LABEL": {"ru": label, "en": label},
                "SHOW_IN_LIST": "Y", "EDIT_IN_LIST": "Y", "MANDATORY": "N", "MULTIPLE": "N", "SHOW_FILTER": "N",
            }})
            keys[col] = "UF_CRM_" + name
            report.append({"field": label, "key": keys[col], "action": "created", "detail": "создано"})
        except RuntimeError as exc:
            report.append({"field": label, "key": "", "action": "error", "detail": str(exc)[:200]})
    return keys, report


def marks_status_text(report: list[dict[str, str]]) -> str:
    bad = [r for r in report if r["action"] == "error"]
    if not bad:
        made = sum(r["action"] == "created" for r in report)
        return f"готово: создано {made}, найдено {len(report) - made}"
    return "не настроено: " + "; ".join(f"{r['field']} — {r['detail']}" for r in bad)[:300] + \
        ". Вебхуку нужны права на настройку CRM (поля лида)."


async def setup_marks(source_id: int, webhook: str) -> dict[str, Any]:
    keys, report = await ensure_mark_fields(webhook)
    status = marks_status_text(report)
    with db.connect() as c:
        for col in MARK_COLUMNS:
            c.execute(f"UPDATE b24_sources SET {col}=? WHERE id=?", (keys.get(col, ""), source_id))
        c.execute("UPDATE b24_sources SET marks_status=? WHERE id=?", (status, source_id))
    return {"ready": len(keys) == len(MARK_COLUMNS), "status": status, "report": report}


async def write_lead_marks(webhook: str, src: dict[str, Any], lead_id: str, *, sent: bool, value: int | None,
                           info: str) -> str | None:
    """Пишет отметки в карточку лида. Возвращает текст ошибки или None.

    В режиме со скорингом флаг «отправлено» и сумма (Score) — рабочие поля offCon, ими управляет он, мы пишем только статус.
    В простом режиме ставим флаг, а сумму — если она есть.
    """
    fields: dict[str, Any] = {src["mark_info"]: info[:250]}
    if sent and src["mode"] != "score_field":
        fields[src["mark_sent"]] = "1"
        if value is not None:
            fields[src["mark_value"]] = value
    try:
        await b24_call(webhook, "crm.lead.update", {"id": lead_id, "fields": fields,
                                                    "params": {"REGISTER_SONET_EVENT": "N"}})
    except RuntimeError as exc:
        return str(exc)[:200]
    return None


def already_marked(src: dict[str, Any], lead: dict[str, Any], value: int | None) -> bool:
    """В Б24 уже видно, что лид отправлен в Метрику (с этой суммой)."""
    if not marks_ready(src):
        return False
    if src["mode"] != "score_field":
        return _truthy(lead.get(src["mark_sent"]))
    m = re.match(r"Отправлено в Метрику .*сумма (\d+)\s*$", str(lead.get(src["mark_info"]) or ""))
    return bool(m) and value is not None and int(m.group(1)) == value


# ───────────────────────── пользователи ─────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def verify_user(username: str, password: str) -> bool:
    with db.connect() as c:
        row = c.execute("SELECT password_hash FROM app_users WHERE username = ?", (username,)).fetchone()
    # Хеш считаем даже для несуществующего пользователя, чтобы не выдавать наличие логина по времени.
    stored = row["password_hash"] if row else "scrypt$" + "00" * 16 + "$" + "00" * 32
    ok = check_password(password, stored)
    return bool(row) and ok


# ───────────────────────── Б24 ─────────────────────────

def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return bool(infos)


def validate_webhook(url: str) -> str:
    """Вебхук вводит пользователь, а запрос делает сервер, поэтому внутренние адреса запрещаем."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise HTTPException(422, "Вебхук должен начинаться с https://")
    if not re.search(r"/rest/\d+/[A-Za-z0-9]+$", parts.path):
        raise HTTPException(422, "Вебхук должен выглядеть так: https://ваш-портал.bitrix24.ru/rest/1/xxxxxxxx")
    try:
        ipaddress.ip_address(parts.hostname)
        raise HTTPException(422, "Укажите адрес портала по имени, а не по IP.")
    except ValueError:
        pass
    if parts.port not in (None, 443):
        raise HTTPException(422, "Нестандартные порты не поддерживаются.")
    if not _is_public_host(parts.hostname):
        raise HTTPException(422, "Адрес портала не открывается из интернета.")
    return url


def mask_webhook(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname}/…{url[-4:]}"


async def b24_call(webhook: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    last = ""
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                r = await client.post(f"{webhook}/{method}.json", json=params)
            if r.status_code in (429, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                await asyncio.sleep(attempt * 2)
                continue
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = type(exc).__name__
            await asyncio.sleep(attempt * 2)
            continue
        if data.get("error") == "QUERY_LIMIT_EXCEEDED":
            last = "QUERY_LIMIT_EXCEEDED"
            await asyncio.sleep(attempt * 2)
            continue
        if data.get("error"):
            raise RuntimeError(f"Б24: {data.get('error')} {data.get('error_description', '')}"[:300])
        return data
    raise RuntimeError(f"Б24 не отвечает ({last})")


async def fetch_recent_leads(webhook: str, hours: int, limit: int | None = None) -> list[dict[str, Any]]:
    date_from = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    leads: list[dict[str, Any]] = []
    start = 0
    while True:
        data = await b24_call(webhook, "crm.lead.list", {
            "filter": {">=DATE_CREATE": date_from},
            "select": ["*", "UF_*"],
            "order": {"DATE_CREATE": "DESC"},
            "start": start,
        })
        batch = data.get("result") or []
        leads.extend(batch)
        if limit and len(leads) >= limit:
            return leads[:limit]
        nxt = data.get("next")
        if not batch or nxt is None:
            return leads
        start = int(nxt)
        await asyncio.sleep(0.25)


# ───────────── разбор лида (повторяет offCon-hourly/lead_processor.py) ─────────────

FIELD_FORM_TEXT = "UF_CRM_1651049909485"


def campaign_key(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw.isdigit():
        return raw
    m = re.search(r"_(\d+)\s*$", raw) or re.search(r"(\d+)\s*$", raw)
    return m.group(1) if m else raw


def _comment_text(lead: dict[str, Any]) -> str:
    return str(lead.get(FIELD_FORM_TEXT) or lead.get("COMMENTS") or "")


def parse_ym_uid(lead: dict[str, Any]) -> str | None:
    sources = [_comment_text(lead), lead.get("UF_CRM_COOKIES") or "", lead.get("UF_CRM_YM_UID") or ""]
    patterns = [r"(?<![A-Za-z])_?ym_uid\s*[:=]\s*(\d+)", r"yandex[_\s]*client[_\s]*id\s*[:=]\s*(\d+)", r"client[_\s]*id\s*[:=]\s*(\d+)"]
    for text in sources:
        for pattern in patterns:
            m = re.search(pattern, str(text), re.IGNORECASE)
            if m:
                return m.group(1)
    return None


def parse_yclid(lead: dict[str, Any]) -> str | None:
    desc = (lead.get("SOURCE_DESCRIPTION") or "").strip()
    if desc:
        try:
            qs = parse_qs(urlsplit(desc).query)
            for key in ("utm_yclid", "yclid"):
                if qs.get(key) and qs[key][0]:
                    return qs[key][0].strip()
        except ValueError:
            pass
        m = re.search(r"(?:utm_yclid|yclid)=([0-9]+)", desc, re.IGNORECASE)
        if m:
            return m.group(1)
    # сайты на Lead-Injector кладут yclid строкой «yclid: 123» в комментарий; SOURCE_DESCRIPTION там занят регионом
    m = re.search(r"(?mi)^\s*(?:utm_)?yclid\s*[:=]\s*(\d+)", _comment_text(lead))
    return m.group(1) if m else None


def lead_phone(lead: dict[str, Any]) -> str | None:
    phones = lead.get("PHONE")
    if phones and isinstance(phones, list) and isinstance(phones[0], dict):
        digits = "".join(ch for ch in str(phones[0].get("VALUE") or "") if ch.isdigit())
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        return "+" + digits if digits else None
    return None


def identifier(lead: dict[str, Any]) -> tuple[str, str] | None:
    """(тип, значение): ClientId → Yclid → Phone, как в старом скрипте."""
    if uid := parse_ym_uid(lead):
        return "ClientId", uid
    if yclid := parse_yclid(lead):
        return "Yclid", yclid
    if phone := lead_phone(lead):
        return "Phone", phone
    return None


def missing_ident_reason(lead: dict[str, Any]) -> str:
    """Человеческое объяснение, чего именно не хватает для отправки в Метрику."""
    src = ""
    m = re.search(r"(?mi)^\s*source\s*:\s*(\S+)", _comment_text(lead))
    if m:
        src = m.group(1)
        try:
            src = src.encode("ascii").decode("idna")
        except UnicodeError:
            pass
    has_comment = bool(_comment_text(lead).strip())
    parts = [
        "Не хватает идентификатора посетителя, по которому Метрика сопоставит конверсию.",
        "Нет _ym_uid (искал в комментарии лида и в UF_CRM_COOKIES)",
        "и нет yclid (искал в SOURCE_DESCRIPTION и в комментарии).",
    ]
    if not has_comment:
        parts.append("Комментарий лида пустой: лид создан не формой сайта (звонок, ручной ввод).")
    else:
        parts.append(
            "Скорее всего, при заявке на сайте не было cookie Метрики: счётчик не установлен на странице, "
            "заблокирован у посетителя, или ссылка рекламы открылась без yclid."
        )
    if src:
        parts.append(f"Сайт-источник: {src}.")
    parts.append("Телефон как запасной вариант ЛК не использует.")
    return " ".join(parts)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "y", "yes", "true", "да"}


def _to_int(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


# ───────────────────────── Метрика ─────────────────────────

async def upload_conversions(client: YandexMetrikaClient, counter_id: int, ident_type: str,
                             rows: list[tuple[str, int | None]]) -> None:
    with_price = any(price is not None for _, price in rows)
    ts = int(time.time())
    header = f"{ident_type},Target,DateTime" + (",Price,Currency" if with_price else "")
    lines = [header]
    for ident, price in rows:
        line = f"{ident},{LEAD_GOAL},{ts}"
        if with_price:
            line += f",{price if price is not None else 0},RUB"
        lines.append(line)
    await client.upload_offline_conversions(counter_id, "\n".join(lines))


def is_lead_goal(goal: dict[str, Any]) -> bool:
    if str(goal.get("name", "")).strip().casefold() != LEAD_GOAL:
        return False
    if str(goal.get("type", "")).strip().casefold() != "action":
        return False
    return any(
        isinstance(c, dict) and str(c.get("type", "")).casefold() == "exact"
        and str(c.get("url", "")).strip() == LEAD_GOAL
        for c in goal.get("conditions") or []
    )


async def ensure_lead_goal(client: YandexMetrikaClient, counter_id: int) -> tuple[int, bool]:
    """Возвращает (goal_id, создана_ли_цель)."""
    for goal in await client.list_goals(counter_id):
        if is_lead_goal(goal) and goal.get("id"):
            return int(goal["id"]), False
    created = await client.create_goal(counter_id)
    return int(created["id"]), True


# ───────────────────────── воркер ─────────────────────────

def _log(c, owner: str, source_id: int, lead_id: str, key: str, counter_id: int | None,
         ident_type: str, revenue: int | None, status: str, error: str = "") -> None:
    c.execute(
        """INSERT INTO conv_log (owner_username, source_id, lead_id, campaign_key, counter_id,
                                 ident_type, revenue, status, error, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (owner, source_id, lead_id, key, counter_id, ident_type, revenue, status, error[:300], _now()),
    )


def _set_state(c, source_id: int, lead_id: str, *, last=None, dry=None, skip=None) -> None:
    row = c.execute("SELECT * FROM lead_state WHERE source_id=? AND lead_id=?", (source_id, lead_id)).fetchone()
    new_last = last if last is not None else (row["last_value"] if row else None)
    new_dry = dry if dry is not None else (row["dry_value"] if row else None)
    new_skip = skip if skip is not None else (row["skip_reason"] if row else "")
    c.execute(
        """INSERT INTO lead_state (source_id, lead_id, last_value, dry_value, skip_reason, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(source_id, lead_id) DO UPDATE SET last_value=excluded.last_value,
             dry_value=excluded.dry_value, skip_reason=excluded.skip_reason, updated_at=excluded.updated_at""",
        (source_id, lead_id, new_last, new_dry, new_skip, _now()),
    )


async def run_source(src: dict[str, Any], get_client: Callable[[int, str], Awaitable[YandexMetrikaClient]],
                     hours: int | None = None, only_ids: set[str] | None = None) -> dict[str, Any]:
    """Один проход по источнику. Возвращает статистику. hours — ручной запуск за нужный период."""
    stats: Counter[str] = Counter()
    owner, sid = src["owner_username"], int(src["id"])
    webhook = decrypt_secret(src["webhook_enc"])
    if not _is_public_host(urlsplit(webhook).hostname or ""):
        raise RuntimeError("Адрес портала Б24 недоступен или внутренний")
    leads = await fetch_recent_leads(webhook, int(hours or src["lookback_hours"]))
    if only_ids is not None:
        leads = [l for l in leads if str(l.get("ID")) in only_ids]
    stats["leads"] = len(leads)

    with db.connect() as c:
        mappings = {r["campaign_key"]: dict(r) for r in
                    c.execute("SELECT * FROM campaign_map WHERE owner_username=?", (owner,))}
        states = {r["lead_id"]: dict(r) for r in
                  c.execute("SELECT * FROM lead_state WHERE source_id=?", (sid,))}

    live = not src["dry_run"]
    use_marks = marks_ready(src)
    baseline = (not src["baselined"]) and (not src["send_backlog"])
    score_mode = src["mode"] == "score_field"
    pending: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    info_writes: list[tuple[str, str]] = []
    sent_marks: list[tuple[str, int | None, str]] = []
    unmapped: dict[str, list[Any]] = {}

    with db.connect() as c:
        for lead in leads:
            lid = str(lead.get("ID"))
            raw = (lead.get("UTM_CAMPAIGN") or "").strip()
            key = campaign_key(raw)
            if not key:
                stats["no_campaign"] += 1
                continue
            mapping = mappings.get(key)
            if not mapping:
                stats["unmapped"] += 1
                item = unmapped.setdefault(key, [raw, 0])
                item[1] += 1
                continue

            value: int | None = None
            if score_mode:
                if src["scored_flag_field"] and not _truthy(lead.get(src["scored_flag_field"])):
                    stats["not_scored"] += 1
                    continue
                value = _to_int(lead.get(src["score_field"]))
                if value is None:
                    stats["not_scored"] += 1
                    continue

            st = states.get(lid)
            prev = (st["last_value"] if live else st["dry_value"]) if st else None
            if baseline:
                _set_state(c, sid, lid, last=value if value is not None else 0,
                           dry=value if value is not None else 0)
                stats["baseline"] += 1
                continue
            if already_marked(src, lead, value):
                # в Б24 уже стоит метка «отправлено» — повторно не шлём, даже если наша база пуста
                if not st or st["last_value"] is None:
                    _set_state(c, sid, lid, last=value if value is not None else 0,
                               dry=value if value is not None else 0)
                stats["already_sent"] += 1
                continue
            if st and prev is not None and (not score_mode or prev == value):
                stats["already_sent"] += 1
                continue

            ident = identifier(lead)
            if not ident:
                if not (st and st["skip_reason"] == "no_id"):
                    _log(c, owner, sid, lid, key, mapping["counter_id"], "", value, "skipped_no_id",
                         missing_ident_reason(lead))
                    _set_state(c, sid, lid, skip="no_id")
                    if use_marks and live:
                        info_writes.append((lid, "Не отправлено: нет _ym_uid и yclid (" + _msk_now() + ")"))
                stats["no_id"] += 1
                continue

            entry = {"lead_id": lid, "key": key, "ident": ident[1], "value": value}
            pending.setdefault((mapping["connection_id"], mapping["counter_id"], ident[0]), []).append(entry)

        for key, (raw, n) in unmapped.items():
            c.execute(
                """INSERT INTO unmapped_campaigns (source_id, owner_username, campaign_key, raw_value, leads, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(source_id, campaign_key) DO UPDATE SET leads=excluded.leads, last_seen=excluded.last_seen""",
                (sid, owner, key, raw[:200], n, _now(), _now()),
            )

        if baseline:
            c.execute("UPDATE b24_sources SET baselined=1 WHERE id=?", (sid,))

    # Сеть — вне транзакции БД, иначе блокируем остальные записи.
    outcomes: list[tuple[dict[str, Any], int, str, str, str]] = []  # (entry, counter, ident_type, status, error)
    for (conn_id, counter_id, ident_type), entries in pending.items():
        if not live:
            outcomes += [(e, counter_id, ident_type, "dry", "") for e in entries]
            continue
        try:
            client = await get_client(conn_id, owner)
            await upload_conversions(client, counter_id, ident_type, [(e["ident"], e["value"]) for e in entries])
            outcomes += [(e, counter_id, ident_type, "ok", "") for e in entries]
        except (MetrikaAPIError, RuntimeError, TokenCryptoError, HTTPException) as exc:
            msg = str(getattr(exc, "detail", None) or exc)
            outcomes += [(e, counter_id, ident_type, "failed", msg) for e in entries]

    with db.connect() as c:
        for e, counter_id, ident_type, status, msg in outcomes:
            v = e["value"] if e["value"] is not None else 0
            if status == "failed":
                prev_log = c.execute(
                    "SELECT status, error FROM conv_log WHERE source_id=? AND lead_id=? ORDER BY id DESC LIMIT 1",
                    (sid, e["lead_id"])).fetchone()
                if not (prev_log and prev_log["status"] == "failed" and prev_log["error"] == msg[:300]):
                    _log(c, owner, sid, e["lead_id"], e["key"], counter_id, ident_type, e["value"], "failed", msg)
                stats["failed"] += 1
                continue
            _log(c, owner, sid, e["lead_id"], e["key"], counter_id, ident_type, e["value"], status)
            if status == "ok":
                _set_state(c, sid, e["lead_id"], last=v, dry=v)
                stats["sent"] += 1
                sent_marks.append((e["lead_id"], e["value"],
                                   f"Отправлено в Метрику {_msk_now()}: счётчик {counter_id}, {ident_type}"
                                   + (f", сумма {e['value']}" if e["value"] is not None else "")))
            else:
                _set_state(c, sid, e["lead_id"], dry=v)
                stats["dry"] += 1

    if use_marks and live:
        first_err = ""
        for lid, value, info in sent_marks:
            err = await write_lead_marks(webhook, src, lid, sent=True, value=value, info=info)
            if err:
                stats["marks_failed"] += 1
                first_err = first_err or err
            await asyncio.sleep(0.2)
        for lid, info in info_writes:
            if await write_lead_marks(webhook, src, lid, sent=False, value=None, info=info):
                stats["marks_failed"] += 1
            await asyncio.sleep(0.2)
        if first_err:
            log.warning("source %s: не удалось записать метки в Б24: %s", sid, first_err)
    return dict(stats)


def _msk_now() -> str:
    return datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК")


def _summary(stats: dict[str, Any]) -> str:
    names = [("sent", "отправлено"), ("dry", "пробно"), ("failed", "ошибок"), ("baseline", "зафиксировано без отправки"),
             ("unmapped", "кампания не привязана"), ("no_id", "нет идентификатора"), ("not_scored", "ещё без скоринга"),
             ("marks_failed", "не записано меток в Б24")]
    parts = [f"{label}: {stats[k]}" for k, label in names if stats.get(k)]
    return f"лидов {stats.get('leads', 0)}" + (" · " + ", ".join(parts) if parts else "")


async def run_all(get_client: Callable[[int, str], Awaitable[YandexMetrikaClient]], only_source: int | None = None) -> None:
    async with _run_lock:
        with db.connect() as c:
            q = "SELECT * FROM b24_sources WHERE enabled=1" + (" AND id=?" if only_source else "")
            sources = [dict(r) for r in c.execute(q, (only_source,) if only_source else ())]
        for src in sources:
            try:
                stats = await run_source(src, get_client)
                status = "ok: " + _summary(stats)
            except Exception as exc:  # один источник не должен ронять остальные
                log.exception("source %s failed", src["id"])
                status = "error: " + (str(getattr(exc, "detail", None) or exc))[:250]
            with db.connect() as c:
                c.execute("UPDATE b24_sources SET last_run_at=?, last_status=? WHERE id=?", (_now(), status, src["id"]))


async def worker_loop(get_client: Callable[[int, str], Awaitable[YandexMetrikaClient]]) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await run_all(get_client)
        except Exception:
            log.exception("worker iteration failed")
        await asyncio.sleep(POLL_SECONDS)


# ───────────────────────── API ─────────────────────────

class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    webhook: str | None = Field(default=None, max_length=300)
    lookback_hours: int = Field(default=24, ge=1, le=168)
    mode: Literal["simple", "score_field"] = "simple"
    score_field: str = Field(default="", max_length=60, pattern=r"^[A-Z0-9_]*$")
    scored_flag_field: str = Field(default="", max_length=60, pattern=r"^[A-Z0-9_]*$")
    enabled: bool = True
    send_backlog: bool = False


class MappingIn(BaseModel):
    campaign_keys: str = Field(min_length=1, max_length=4000)
    connection_id: int = Field(ge=1)
    counter_id: int = Field(ge=1)
    note: str = Field(default="", max_length=120)


class UserIn(BaseModel):
    username: str
    password: str = Field(min_length=10, max_length=100)


def _src_public(row: dict[str, Any]) -> dict[str, Any]:
    try:
        masked = mask_webhook(decrypt_secret(row["webhook_enc"]))
    except TokenCryptoError:
        masked = "не удалось расшифровать"
    out = {k: row[k] for k in ("id", "name", "lookback_hours", "mode", "score_field", "scored_flag_field",
                               "enabled", "dry_run", "send_backlog", "last_run_at", "last_status", "created_at",
                               "marks_status")}
    out["marks_ready"] = marks_ready(row)
    out["webhook"] = masked
    return out


def build_router(
    member_dep: Callable[..., Member],
    metrika_context: Callable[..., Awaitable[YandexMetrikaClient]],
) -> APIRouter:
    router = APIRouter(prefix="/api/lk")
    MemberDep = Annotated[Member, Depends(member_dep)]

    async def client_for(connection_id: int, owner: str) -> YandexMetrikaClient:
        return await metrika_context(connection_id, owner, None)

    def own_source(c, source_id: int, owner: str) -> dict[str, Any]:
        row = c.execute("SELECT * FROM b24_sources WHERE id=? AND owner_username=?", (source_id, owner)).fetchone()
        if not row:
            raise HTTPException(404, "Источник не найден")
        return dict(row)

    @router.get("/me")
    async def me(user: MemberDep):
        return user

    @router.get("/counters")
    async def counters(user: MemberDep, connection_id: int = Query(ge=1)):
        client = await client_for(connection_id, user.username)
        try:
            items = await client.list_counters()
        except MetrikaAPIError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"items": [{
            "id": c.get("id"), "name": c.get("name"),
            "site": c.get("site") or (c.get("site2") or {}).get("site", ""),
            "has_lead_goal": any(is_lead_goal(g) for g in c.get("goals") or []),
        } for c in items if c.get("id") is not None]}

    # --- источники Б24 ---
    @router.get("/sources")
    async def list_sources(user: MemberDep):
        with db.connect() as c:
            rows = c.execute("SELECT * FROM b24_sources WHERE owner_username=? ORDER BY id", (user.username,)).fetchall()
        return {"items": [_src_public(dict(r)) for r in rows], "poll_seconds": POLL_SECONDS}

    def _check_mode(body: SourceIn) -> None:
        if body.mode == "score_field" and not body.score_field:
            raise HTTPException(422, "Для режима «со скорингом» укажите поле, где лежит скоринг.")

    @router.post("/sources", status_code=201)
    async def add_source(body: SourceIn, user: MemberDep):
        _check_mode(body)
        if not body.webhook:
            raise HTTPException(422, "Укажите вебхук Битрикс24.")
        webhook = validate_webhook(body.webhook)
        try:
            await b24_call(webhook, "crm.lead.list", {"select": ["ID"], "start": -1})
        except RuntimeError as exc:
            raise HTTPException(422, f"Вебхук не работает: {exc}. Нужны права на CRM.") from exc
        with db.connect() as c:
            cur = c.execute(
                """INSERT INTO b24_sources (owner_username, name, webhook_enc, lookback_hours, mode, score_field,
                       scored_flag_field, enabled, dry_run, send_backlog, created_at)
                   VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
                (user.username, body.name.strip(), encrypt_secret(webhook), body.lookback_hours, body.mode,
                 body.score_field, body.scored_flag_field, int(body.enabled), int(body.send_backlog), _now()))
            sid = cur.lastrowid
        try:  # поля для меток — по возможности сразу; не получилось, источник всё равно создаём
            marks = await setup_marks(sid, webhook)
        except RuntimeError as exc:
            marks = {"ready": False, "status": f"не настроено: {str(exc)[:200]}", "report": []}
            with db.connect() as c:
                c.execute("UPDATE b24_sources SET marks_status=? WHERE id=?", (marks["status"], sid))
        return {"id": sid, "marks": marks}

    @router.post("/sources/{source_id}/marks/setup")
    async def marks_setup(source_id: int, user: MemberDep):
        """Найти по названию или создать в Б24 поля для меток «отправлено в Метрику»."""
        with db.connect() as c:
            src = own_source(c, source_id, user.username)
        webhook = decrypt_secret(src["webhook_enc"])
        if not _is_public_host(urlsplit(webhook).hostname or ""):
            raise HTTPException(422, "Адрес портала Б24 недоступен или внутренний")
        try:
            return await setup_marks(source_id, webhook)
        except RuntimeError as exc:
            raise HTTPException(502, f"Б24 не принял запрос: {exc}") from exc

    @router.put("/sources/{source_id}")
    async def update_source(source_id: int, body: SourceIn, user: MemberDep):
        _check_mode(body)
        with db.connect() as c:
            own_source(c, source_id, user.username)
            if body.webhook:
                c.execute("UPDATE b24_sources SET webhook_enc=? WHERE id=?",
                          (encrypt_secret(validate_webhook(body.webhook)), source_id))
            c.execute(
                """UPDATE b24_sources SET name=?, lookback_hours=?, mode=?, score_field=?, scored_flag_field=?,
                       enabled=?, send_backlog=? WHERE id=?""",
                (body.name.strip(), body.lookback_hours, body.mode, body.score_field, body.scored_flag_field,
                 int(body.enabled), int(body.send_backlog), source_id))
        return {"ok": True}

    @router.delete("/sources/{source_id}")
    async def delete_source(source_id: int, user: MemberDep):
        with db.connect() as c:
            own_source(c, source_id, user.username)
            for table in ("lead_state", "unmapped_campaigns", "conv_log"):
                c.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))
            c.execute("DELETE FROM b24_sources WHERE id=?", (source_id,))
        return {"deleted": True}

    @router.post("/sources/{source_id}/run")
    async def run_now(source_id: int, user: MemberDep, hours: int | None = Query(default=None, ge=1, le=720),
                      lead_ids: str | None = Query(default=None, max_length=2000, pattern=r"^[0-9, ]+$")):
        with db.connect() as c:
            src = own_source(c, source_id, user.username)
        if _run_lock.locked():
            raise HTTPException(409, "Проверка уже идёт, подождите минуту.")
        try:
            async with _run_lock:
                only = {x.strip() for x in lead_ids.split(",") if x.strip()} if lead_ids else None
                stats = await run_source(src, client_for, hours, only)
        except Exception as exc:
            msg = str(getattr(exc, "detail", None) or exc)
            with db.connect() as c:
                c.execute("UPDATE b24_sources SET last_run_at=?, last_status=? WHERE id=?",
                          (_now(), "error: " + msg[:250], source_id))
            raise HTTPException(502, msg) from exc
        with db.connect() as c:
            c.execute("UPDATE b24_sources SET last_run_at=?, last_status=? WHERE id=?",
                      (_now(), "ok: " + (f"вручную за {hours} ч" + (f", лидов выбрано {len(only)}" if only else "") + " · " if hours else "") + _summary(stats), source_id))
        return {"stats": stats}

    @router.post("/sources/{source_id}/live")
    async def set_live(source_id: int, user: MemberDep, on: bool = Query(...)):
        """Включить реальную отправку. Всё, что видели в пробном режиме, считается уже отправленным."""
        with db.connect() as c:
            own_source(c, source_id, user.username)
            if on:
                c.execute("UPDATE lead_state SET last_value = dry_value WHERE source_id=? AND dry_value IS NOT NULL",
                          (source_id,))
            c.execute("UPDATE b24_sources SET dry_run=? WHERE id=?", (0 if on else 1, source_id))
        return {"dry_run": not on}

    # --- привязки кампаний ---
    @router.get("/mappings")
    async def list_mappings(user: MemberDep):
        with db.connect() as c:
            rows = c.execute("SELECT * FROM campaign_map WHERE owner_username=? ORDER BY id DESC", (user.username,)).fetchall()
        return {"items": [dict(r) for r in rows]}

    @router.post("/mappings", status_code=201)
    async def add_mappings(body: MappingIn, user: MemberDep):
        keys = [campaign_key(k) for k in re.split(r"[\s,;]+", body.campaign_keys) if k.strip()]
        keys = list(dict.fromkeys(k for k in keys if re.fullmatch(r"\d{4,20}", k)))
        if not keys:
            raise HTTPException(422, "Укажите ID кампаний Директа — числа через запятую или с новой строки.")
        client = await client_for(body.connection_id, user.username)
        try:
            goal_id, created = await ensure_lead_goal(client, body.counter_id)
        except MetrikaAPIError as exc:
            raise HTTPException(502, f"Счётчик недоступен или нет прав на создание цели: {exc}") from exc
        with db.connect() as c:
            for key in keys:
                c.execute(
                    """INSERT INTO campaign_map (owner_username, campaign_key, connection_id, counter_id, goal_id, note, created_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(owner_username, campaign_key) DO UPDATE SET connection_id=excluded.connection_id,
                         counter_id=excluded.counter_id, goal_id=excluded.goal_id, note=excluded.note""",
                    (user.username, key, body.connection_id, body.counter_id, goal_id, body.note.strip(), _now()))
                c.execute("DELETE FROM unmapped_campaigns WHERE owner_username=? AND campaign_key=?", (user.username, key))
        return {"saved": len(keys), "goal_id": goal_id, "goal_created": created}

    @router.delete("/mappings/{mapping_id}")
    async def delete_mapping(mapping_id: int, user: MemberDep):
        with db.connect() as c:
            cur = c.execute("DELETE FROM campaign_map WHERE id=? AND owner_username=?", (mapping_id, user.username))
        if not cur.rowcount:
            raise HTTPException(404, "Привязка не найдена")
        return {"deleted": True}

    @router.get("/unmapped")
    async def unmapped(user: MemberDep):
        with db.connect() as c:
            rows = c.execute(
                """SELECT u.campaign_key, u.raw_value, SUM(u.leads) AS leads, MAX(u.last_seen) AS last_seen,
                          GROUP_CONCAT(s.name, ', ') AS sources
                   FROM unmapped_campaigns u JOIN b24_sources s ON s.id=u.source_id
                   WHERE u.owner_username=? GROUP BY u.campaign_key ORDER BY leads DESC""", (user.username,)).fetchall()
        return {"items": [dict(r) for r in rows]}

    # --- журнал ---
    @router.get("/log")
    async def conv_log(user: MemberDep, limit: int = Query(default=100, ge=1, le=500), status: str | None = None):
        sql = ("SELECT l.*, s.name AS source_name FROM conv_log l LEFT JOIN b24_sources s ON s.id=l.source_id "
               "WHERE l.owner_username=?")
        args: list[Any] = [user.username]
        if status:
            sql += " AND l.status=?"
            args.append(status)
        sql += " ORDER BY l.id DESC LIMIT ?"
        args.append(limit)
        with db.connect() as c:
            rows = [dict(r) for r in c.execute(sql, args)]
            day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
            counts = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) AS n FROM conv_log WHERE owner_username=? AND created_at>=? GROUP BY status",
                (user.username, day_ago))}
        return {"items": rows, "counts_24h": counts}

    # --- пользователи (только администратор) ---
    def admin_only(user: MemberDep) -> Member:
        if not user.is_admin:
            raise HTTPException(403, "Только для администратора")
        return user

    AdminDep = Annotated[Member, Depends(admin_only)]

    @router.get("/users")
    async def list_users(_: AdminDep):
        with db.connect() as c:
            rows = c.execute("SELECT username, created_at FROM app_users ORDER BY username").fetchall()
        return {"items": [dict(r) for r in rows]}

    @router.post("/users", status_code=201)
    async def create_user(body: UserIn, _: AdminDep):
        if not USERNAME_RE.match(body.username):
            raise HTTPException(422, "Логин: 3–40 символов, латиница, цифры, . _ -")
        if body.username == os.getenv("APP_USERNAME", "admin"):
            raise HTTPException(409, "Этот логин занят администратором")
        with db.connect() as c:
            if c.execute("SELECT 1 FROM app_users WHERE username=?", (body.username,)).fetchone():
                raise HTTPException(409, "Такой пользователь уже есть")
            c.execute("INSERT INTO app_users (username, password_hash, created_at) VALUES (?,?,?)",
                      (body.username, hash_password(body.password), _now()))
        return {"created": body.username}

    @router.delete("/users/{username}")
    async def delete_user(username: str, _: AdminDep):
        with db.connect() as c:
            cur = c.execute("DELETE FROM app_users WHERE username=?", (username,))
            # Источники удалённого пользователя больше не должны отправлять конверсии.
            c.execute("UPDATE b24_sources SET enabled=0 WHERE owner_username=?", (username,))
        if not cur.rowcount:
            raise HTTPException(404, "Пользователь не найден")
        return {"deleted": username}

    return router
