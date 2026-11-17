"""Лиды, клиенты и договоры из Битрикс24 для отчёта рекламы (2026-11-17, Dima).

К проекту привязывается источник Б24 из раздела «Конверсии» (таблица
b24_sources модуля lk — здесь только ЧИТАЕМ её, lk.py не трогаем). По
вебхуку источника:
  • лиды за период, у которых UTM_CAMPAIGN заканчивается ID кампании Директа
    (метки пишутся по-разному: «rsya_716218280», «710450259»,
    «МК / Все города_713853855» — берём хвостовые цифры, lk.campaign_key);
  • клиенты — контакты этих лидов (CONTACT_ID);
  • договоры — сделки ТОЛЬКО воронки «Продажа» по этим контактам (и по самим
    лидам — у части порталов лид конвертируется в сделку без контакта);
    успешный договор — стадия с семантикой «успех» (STAGE_SEMANTIC_ID = S).
Только чтение Б24. Кэш 5 минут.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import db, lk
from .token_crypto import TokenCryptoError, decrypt_secret

SALES_FUNNEL_NAME = "продажа"
CACHE_SECONDS = 300
BATCH = 40  # команд в одном batch Б24 (лимит 50)

_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
_funnel_cache: dict[int, tuple[float, dict[str, Any]]] = {}


class CrmError(RuntimeError):
    pass


# ───────────── привязка источника к проекту ─────────────

def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS project_crm (
        project_id INTEGER PRIMARY KEY,
        source_id INTEGER,
        updated_at TEXT NOT NULL
    )""")


def get_project_source_id(project_id: int) -> int | None:
    with db.connect() as c:
        _init(c)
        row = c.execute("SELECT source_id FROM project_crm WHERE project_id=?", (project_id,)).fetchone()
    return int(row["source_id"]) if row and row["source_id"] is not None else None


def set_project_source_id(project_id: int, source_id: int | None) -> None:
    with db.connect() as c:
        _init(c)
        c.execute(
            "INSERT INTO project_crm(project_id, source_id, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET source_id=excluded.source_id, updated_at=excluded.updated_at",
            (project_id, source_id, datetime.now(timezone.utc).isoformat()),
        )


def list_sources(owner: str) -> list[dict[str, Any]]:
    """Источники Б24 владельца (без вебхука)."""
    with db.connect() as c:
        rows = c.execute("SELECT id, name, enabled, last_status FROM b24_sources WHERE owner_username=? ORDER BY id",
                         (owner,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "last_status": r["last_status"]} for r in rows]


def _source(source_id: int, owner: str | None = None) -> dict[str, Any]:
    with db.connect() as c:
        row = c.execute("SELECT * FROM b24_sources WHERE id=?", (source_id,)).fetchone()
    if not row or (owner is not None and row["owner_username"] != owner):
        raise CrmError("Источник Б24 не найден — выберите другой в настройках проекта")
    return dict(row)


def _webhook(src: dict[str, Any]) -> str:
    try:
        return decrypt_secret(src["webhook_enc"]).rstrip("/")
    except TokenCryptoError as exc:
        raise CrmError(f"Вебхук источника «{src['name']}» не расшифровывается") from exc


# ───────────── Б24 ─────────────

async def _call(webhook: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return await lk.b24_call(webhook, method, params)
    except RuntimeError as exc:
        raise CrmError(str(exc)) from exc


async def _batch(webhook: str, cmds: dict[str, str], ignore_errors: bool = False) -> dict[str, Any]:
    """batch → {"result": {key: [...]}, "next": {key: start}}. Ошибки отдельных команд — CrmError."""
    data = (await _call(webhook, "batch", {"halt": 0, "cmd": cmds})).get("result") or {}
    errors = data.get("result_error") or {}
    if errors and not ignore_errors:
        first = next(iter(errors.values()))
        text = first.get("error_description") or first.get("error") if isinstance(first, dict) else str(first)
        raise CrmError(f"Б24: {str(text)[:200]}")
    res = data.get("result") or {}
    nxt = data.get("result_next") or {}
    return {"result": res if isinstance(res, dict) else {}, "next": nxt if isinstance(nxt, dict) else {}}


def _q(method: str, params: list[tuple[str, Any]]) -> str:
    from urllib.parse import quote
    return method + "?" + "&".join(f"{quote(k, safe='[]')}={quote(str(v), safe='')}" for k, v in params)


async def sales_funnel(source_id: int, webhook: str) -> dict[str, Any]:
    """Воронка сделок с названием «Продажа» → {"id", "name", "all": [...названия]}."""
    cached = _funnel_cache.get(source_id)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    cats = ((await _call(webhook, "crm.category.list", {"entityTypeId": 2})).get("result") or {}).get("categories") or []
    names = [str(c.get("name") or "") for c in cats]
    exact = [c for c in cats if str(c.get("name") or "").strip().casefold() == SALES_FUNNEL_NAME]
    pick = exact[0] if exact else next((c for c in cats if str(c.get("name") or "").strip().casefold().startswith(SALES_FUNNEL_NAME)), None)
    if pick is None:
        raise CrmError("В Б24 нет воронки «Продажа». Есть: " + ", ".join(names[:12]))
    out = {"id": int(pick.get("id") or 0), "name": pick.get("name"), "all": names}
    _funnel_cache[source_id] = (time.time(), out)
    return out


MAX_LEADS = 60000  # потолок лидов за период
CHAIN = 40  # страниц по 50 в одном batch, сцепленных через $result


async def _leads_by_campaign(webhook: str, campaign_ids: list[str], date_from: date, date_to: date) -> dict[str, list[dict[str, Any]]]:
    """Все лиды периода с непустой UTM_CAMPAIGN, сопоставление — у нас (хвост
    метки = ID кампании). Перебор по ID без подсчёта (start=-1, filter[>ID]) —
    быстрый способ Б24: на портале с 20 тыс. лидов в месяц фильтр по вхождению
    метки шёл 60 с, а страницы со смещением — минуты. В одном batch — цепочка
    из CHAIN страниц: следующая берёт ID последнего лида предыдущей."""
    d1 = datetime.combine(date_from, datetime.min.time()).isoformat()
    d2 = datetime.combine(date_to + timedelta(days=1), datetime.min.time()).isoformat()
    qbase = [("filter[>=DATE_CREATE]", d1), ("filter[<DATE_CREATE]", d2), ("filter[!UTM_CAMPAIGN]", ""),
             ("select[]", "ID"), ("select[]", "UTM_CAMPAIGN"), ("select[]", "CONTACT_ID"), ("select[]", "STATUS_ID"),
             ("select[]", "DATE_CREATE"), ("order[ID]", "ASC"), ("start", -1)]
    rows: list[dict[str, Any]] = []
    last_id = 0
    while len(rows) < MAX_LEADS:
        cmds = {"p0": _q("crm.lead.list", qbase + [("filter[>ID]", last_id)])}
        for i in range(1, CHAIN):
            cmds[f"p{i}"] = _q("crm.lead.list", qbase) + f"&filter[>ID]=$result[p{i - 1}][49][ID]"
        data = await _batch(webhook, cmds, ignore_errors=True)
        done = False
        for i in range(CHAIN):
            page = data["result"].get(f"p{i}")
            if not isinstance(page, list) or not page:
                done = True
                break
            rows.extend(page)
            last_id = int(page[-1]["ID"])
            if len(page) < 50:
                done = True
                break
        if done:
            break
        await asyncio.sleep(0.3)
    wanted = set(campaign_ids)
    out: dict[str, list[dict[str, Any]]] = {cid: [] for cid in campaign_ids}
    seen: set[str] = set()
    for lead in rows:
        lid = str(lead.get("ID"))
        cid = lk.campaign_key(str(lead.get("UTM_CAMPAIGN") or ""))
        if cid in wanted and lid not in seen:
            seen.add(lid)
            out[cid].append(lead)
    return out


async def _deals(webhook: str, category_id: int, field: str, ids: list[str]) -> list[dict[str, Any]]:
    """Сделки воронки category_id, у которых field (CONTACT_ID / LEAD_ID) ∈ ids."""
    deals: list[dict[str, Any]] = []
    groups = [ids[i:i + 50] for i in range(0, len(ids), 50)]
    pending: list[tuple[int, int]] = [(i, 0) for i in range(len(groups))]
    guard = 0
    while pending and guard < 60:
        guard += 1
        chunk, pending = pending[:BATCH], pending[BATCH:]
        cmds = {}
        for gi, start in chunk:
            params: list[tuple[str, Any]] = [("filter[CATEGORY_ID]", category_id)]
            params += [(f"filter[{field}][]", x) for x in groups[gi]]
            params += [("select[]", "ID"), ("select[]", "CONTACT_ID"), ("select[]", "LEAD_ID"), ("select[]", "STAGE_ID"),
                       ("select[]", "STAGE_SEMANTIC_ID"), ("select[]", "OPPORTUNITY"), ("start", start)]
            cmds[f"g{gi}_{start}"] = _q("crm.deal.list", params)
        data = await _batch(webhook, cmds)
        for gi, start in chunk:
            key = f"g{gi}_{start}"
            deals.extend(data["result"].get(key) or [])
            nxt = data["next"].get(key)
            if nxt is not None:
                pending.append((gi, int(nxt)))
        if pending:
            await asyncio.sleep(0.4)
    return deals


def _nz(v: Any) -> str:
    s = str(v or "").strip()
    return "" if s in ("", "0") else s


async def campaign_stats(source_id: int, campaign_ids: list[int | str], date_from: date, date_to: date,
                         refresh: bool = False, not_before: date | None = None) -> dict[str, Any]:
    """→ {"by_campaign": {id: {leads, clients, deals, won, won_sum}}, "funnel": {...}, "source": {...}}"""
    cids = sorted({str(int(c)) for c in campaign_ids if c})
    # лидов раньше создания первой кампании быть не может — не листаем лишнее
    if not_before and not_before > date_from:
        date_from = min(not_before, date_to)
    key = (source_id, tuple(cids), date_from.isoformat(), date_to.isoformat())
    cached = _cache.get(key)
    if not refresh and cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    src = _source(source_id)
    webhook = _webhook(src)
    funnel = await sales_funnel(source_id, webhook)
    leads = await _leads_by_campaign(webhook, cids, date_from, date_to)
    lead_camp: dict[str, str] = {}
    contact_camp: dict[str, str] = {}
    for cid, items in leads.items():
        for lead in items:
            lead_camp[str(lead["ID"])] = cid
            contact = _nz(lead.get("CONTACT_ID"))
            if contact:
                contact_camp.setdefault(contact, cid)
    deals: dict[str, dict[str, Any]] = {}
    if contact_camp:
        for d in await _deals(webhook, funnel["id"], "CONTACT_ID", sorted(contact_camp)):
            deals[str(d["ID"])] = d
    if lead_camp:
        for d in await _deals(webhook, funnel["id"], "LEAD_ID", sorted(lead_camp)):
            deals.setdefault(str(d["ID"]), d)
    by: dict[str, dict[str, Any]] = {cid: {"leads": len(leads[cid]), "clients": 0, "deals": 0, "won": 0, "won_sum": 0.0}
                                      for cid in cids}
    clients: dict[str, set[str]] = {cid: set() for cid in cids}
    for contact, cid in contact_camp.items():
        clients[cid].add(contact)
    for d in deals.values():
        cid = contact_camp.get(_nz(d.get("CONTACT_ID"))) or lead_camp.get(_nz(d.get("LEAD_ID")))
        if cid is None:
            continue
        by[cid]["deals"] += 1
        contact = _nz(d.get("CONTACT_ID"))
        if contact:
            clients[cid].add(contact)
        if str(d.get("STAGE_SEMANTIC_ID") or "") == "S":
            by[cid]["won"] += 1
            try:
                by[cid]["won_sum"] += float(d.get("OPPORTUNITY") or 0)
            except (TypeError, ValueError):
                pass
    for cid in cids:
        by[cid]["clients"] = len(clients[cid])
        by[cid]["won_sum"] = round(by[cid]["won_sum"], 2)
    out = {"by_campaign": by, "funnel": {"id": funnel["id"], "name": funnel["name"]},
           "source": {"id": src["id"], "name": src["name"]}}
    _cache[key] = (time.time(), out)
    return out
