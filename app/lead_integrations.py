"""Интеграции заявок с сайтов → Б24 (2026-11-18, раздел «Реклама → Интеграции»).

Не просто вебхук: у каждого портала своя логика лида — повторяет то, как
лиды уже заводят их собственные сайты (сверено по живым лидам, только чтение):

МЮ (портал «Мой юрист», источник Б24 «МЮ»):
  TITLE «<Тип> | <Имя>», SOURCE_ID UC_11UXNN (LeadGen),
  SOURCE_DESCRIPTION «По региону из формы: <регион>»,
  «Тип лида» (UF_CRM_1790230122139: 70 БФЛ / 72 Юр.Услуга / 82 Пенсия),
  «Отдел продаж» (UF_CRM_1779024295: 58 Екатеринбург / 60 Челябинск — по региону),
  «Город» (UF_CRM_1730379820755), COMMENTS: Name/city/region, квиз, оценка,
  способ связи, «═══ ОТВЕТЫ ═══» списком.
ЮДЛ (портал «Юрист для людей»):
  TITLE «<Тип> | <Город> | <Имя> 7XXX***XXXX», SOURCE_ID — офис по городу
  (источники портала названы городами: «Волгоград», «Владивосток 2»…),
  иначе источник по умолчанию; SOURCE_DESCRIPTION — полный адрес страницы с UTM;
  «Тип лида» (UF_CRM_1662639727), «Источник лида» = Сайт (UF_CRM_1620728999067=651),
  «Город», «Комментарий» (UF_CRM_1651049909485: «Вопрос:\\n- ответ»), «Горячий» и
  «Рабочий» лид, UF_UTM_PRODUCT/MANAGER/ACCOUNT/CAMPAIGN_TYPE (из адреса или
  настроек), UF_CLIENT_ID (ClientID Метрики).
b24: простой лид (как раньше — TITLE, имя, телефон, ответы в COMMENTS, UTM).

Коды полей лежат в settings.fields — если в портале поле переименуют, правится
в ЛК без релиза.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from . import crm_stats, db

KINDS = {"mu": "МЮ — «Мой юрист»", "ydl": "ЮДЛ — «Юрист для людей»", "b24": "Б24 — простой лид"}

DEFAULTS: dict[str, dict[str, Any]] = {
    "mu": {"title_prefix": "Пенсия", "lead_type_id": "82", "b24_source": "UC_11UXNN", "dept_default": "58",
           "dept_rules": [{"match": "Челяб", "value": "60"}], "quiz_title": "",
           "fields": {"lead_type": "UF_CRM_1790230122139", "dept": "UF_CRM_1779024295", "city": "UF_CRM_1730379820755"}},
    "ydl": {"title_prefix": "Пенсионный юрист", "lead_type_id": "22747", "source_by_city": True, "b24_source": "",
            "origin_id": "651", "hot": True, "work": True,
            "utm_product": "", "utm_manager": "", "utm_account": "", "utm_campaign_type": "",
            "fields": {"lead_type": "UF_CRM_1662639727", "origin": "UF_CRM_1620728999067", "city": "UF_CRM_1622543817615",
                       "comment": "UF_CRM_1651049909485", "hot": "UF_CRM_1660713522", "work": "UF_CRM_611642190F792",
                       "client_id": "UF_CLIENT_ID", "utm_product": "UF_UTM_PRODUCT", "utm_manager": "UF_UTM_MANAGER",
                       "utm_account": "UF_UTM_ACCOUNT", "utm_campaign_type": "UF_UTM_CAMPAIGN_TYPE"}},
    "b24": {"title_prefix": "Заявка с сайта", "b24_source": ""},
}

SAMPLE_LEAD: dict[str, Any] = {
    "name": "Иван", "phone": "+7 (900) 123-45-67", "region": "Свердловская область", "city": "Екатеринбург",
    "answers": [{"q": "Какая у вас общая сумма долга?", "a": "500 000 – 1 500 000 ₽"},
                {"q": "Кредиторы подавали на вас в суд?", "a": "Да, подавали"}],
    "quiz_title": "Кредитная амнистия — списание долгов", "quality": "good", "score": "4", "findings": [],
    "method": "Позвонить", "utm": {"utm_source": "yandex", "utm_medium": "cpc", "utm_campaign": "rsya_716218280",
                                   "utm_content": "ad_17000000000", "utm_term": "списание долгов"},
    "page": "https://example.ru/?utm_source=yandex&utm_medium=cpc&utm_campaign=rsya_716218280", "ref": "",
    "client_id": "1763400000123456789", "spam": False}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS lead_integrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
        source_id INTEGER, settings TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def _conn():
    c = db.connect()
    _init(c)
    return c


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["settings"] = merged(d["kind"], json.loads(d.get("settings") or "{}"))
    d["kind_label"] = KINDS.get(d["kind"], d["kind"])
    return d


def merged(kind: str, settings: dict[str, Any]) -> dict[str, Any]:
    base = json.loads(json.dumps(DEFAULTS.get(kind, {})))
    for k, v in (settings or {}).items():
        if k == "fields" and isinstance(v, dict):
            base.setdefault("fields", {}).update({fk: str(fv).strip() for fk, fv in v.items() if str(fv).strip()})
        else:
            base[k] = v
    return base


def list_all() -> list[dict[str, Any]]:
    with _conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM lead_integrations ORDER BY id").fetchall()]


def get(iid: int) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM lead_integrations WHERE id=?", (iid,)).fetchone()
    return _row(r) if r else None


def save(iid: int | None, name: str, kind: str, source_id: int | None, settings: dict[str, Any]) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("Неизвестный тип интеграции")
    js = json.dumps(settings or {}, ensure_ascii=False)
    with _conn() as c:
        if iid:
            c.execute("UPDATE lead_integrations SET name=?, kind=?, source_id=?, settings=?, updated_at=? WHERE id=?",
                      (name, kind, source_id, js, _now(), iid))
        else:
            iid = int(c.execute("INSERT INTO lead_integrations (name, kind, source_id, settings, created_at, updated_at) "
                                "VALUES (?,?,?,?,?,?)", (name, kind, source_id, js, _now(), _now())).lastrowid)
    return get(int(iid))  # type: ignore[return-value]


def delete(iid: int, used_by: list[str]) -> bool:
    if used_by:
        raise ValueError("Интеграция выбрана в шаблонах: " + ", ".join(used_by[:5]))
    with _conn() as c:
        return c.execute("DELETE FROM lead_integrations WHERE id=?", (iid,)).rowcount > 0


def b24_sources() -> list[dict[str, Any]]:
    with db.connect() as c:
        try:
            rows = c.execute("SELECT id, name, enabled FROM b24_sources ORDER BY id").fetchall()
        except Exception:  # noqa: BLE001 - таблицы ещё нет
            return []
    return [{"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"])} for r in rows]


# ---------- сборка лида ----------
def phone_digits(phone: str) -> str:
    d = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    elif len(d) == 10:
        d = "7" + d
    return d


def _mask(d: str) -> str:
    return f"{d[:4]}***{d[-4:]}" if len(d) >= 8 else d


def _std(lead: dict[str, Any]) -> dict[str, Any]:
    d = phone_digits(lead.get("phone", ""))
    f: dict[str, Any] = {"NAME": lead.get("name") or "Без имени", "PHONE": [{"VALUE": d or lead.get("phone", ""), "VALUE_TYPE": "WORK"}]}
    utm = lead.get("utm") or {}
    for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
        if utm.get(k):
            f[k.upper()] = str(utm[k])[:250]
    return f


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("ё", "е").replace("-", " ").split())


def route_source(city: str, region: str, sources: list[tuple[str, str]]) -> str | None:
    """Источник ЮДЛ по городу: точное имя или «Город N» (несколько офисов)."""
    for place in (city, region):
        p = _norm(place)
        if not p:
            continue
        exact = [sid for sid, name in sources if _norm(name) == p]
        if exact:
            return exact[0]
        numbered = [sid for sid, name in sources if re.fullmatch(re.escape(p) + r" \d+", _norm(name))]
        if numbered:
            return sorted(numbered, key=lambda s: _norm(dict(sources)[s]))[0]
    return None


def build(integ: dict[str, Any], lead: dict[str, Any], domain: str = "",
          sources: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    kind, s = integ["kind"], integ["settings"]
    fl = s.get("fields") or {}
    f = _std(lead)
    name, city, region = lead.get("name") or "", lead.get("city") or "", lead.get("region") or ""
    answers = [a for a in lead.get("answers") or [] if isinstance(a, dict)]
    prefix = (s.get("title_prefix") or "").strip()
    if kind == "mu":
        f["TITLE"] = f"{prefix} | {name or 'Без имени'}" if prefix else (name or "Заявка с сайта")
        if s.get("b24_source"):
            f["SOURCE_ID"] = s["b24_source"]
        f["SOURCE_DESCRIPTION"] = f"По региону из формы: {region}" if region else (lead.get("page") or domain)[:500]
        quiz = s.get("quiz_title") or lead.get("quiz_title") or domain
        lines = [f"Name: {name}", f"city: {city}", f"region: {region}", f"0__: Заявка с квиза «{quiz}»"]
        if lead.get("quality"):
            lines.append(f"Оценка лида: {lead['quality']} (найдено оснований: {lead.get('score') or 0})")
        lines.append(f"Регион: {', '.join(x for x in (city, region) if x)}")
        if lead.get("method"):
            lines.append(f"Удобный способ связи: {lead['method']}")
        lines += ["", "═══ ОТВЕТЫ ═══"] + [f"• {a.get('q')}: {a.get('a')}" for a in answers]
        if lead.get("findings"):
            lines += ["", "═══ НАЙДЕННЫЕ ОСНОВАНИЯ ═══"] + [f"• {x}" for x in lead["findings"]]
        lines += ["", f"Страница: {lead.get('page') or domain}"]
        f["COMMENTS"] = "\n".join(lines)
        if fl.get("lead_type") and s.get("lead_type_id"):
            f[fl["lead_type"]] = str(s["lead_type_id"])
        if fl.get("dept"):
            where = _norm(f"{region} {city}")
            dept = next((str(r.get("value")) for r in s.get("dept_rules") or []
                         if r.get("match") and _norm(r["match"]) in where and r.get("value")), None) or s.get("dept_default")
            if dept:
                f[fl["dept"]] = str(dept)
        if fl.get("city") and city:
            f[fl["city"]] = city
    elif kind == "ydl":
        d = phone_digits(lead.get("phone", ""))
        f["TITLE"] = " | ".join(x for x in (prefix, city or region or "—", f"{name} {_mask(d)}".strip()) if x)
        src = route_source(city, region, sources or []) if s.get("source_by_city") else None
        src = src or s.get("b24_source")
        if src:
            f["SOURCE_ID"] = str(src)
        f["SOURCE_DESCRIPTION"] = (lead.get("page") or f"https://{domain}/")[:1000]
        if fl.get("lead_type") and s.get("lead_type_id"):
            f[fl["lead_type"]] = str(s["lead_type_id"])
        if fl.get("origin") and s.get("origin_id"):
            f[fl["origin"]] = str(s["origin_id"])
        if fl.get("city") and city:
            f[fl["city"]] = city
        qa = [f"{a.get('q')}:\n- {a.get('a')}" for a in answers]
        extra = [x for x in (f"Регион: {region}" if region else "", f"Способ связи: {lead.get('method')}" if lead.get("method") else "",
                             f"Квиз: {lead.get('quiz_title')}" if lead.get("quiz_title") else "") if x]
        if fl.get("comment"):
            f[fl["comment"]] = "\n\n".join(qa + (["\n".join(extra)] if extra else []))
        if fl.get("hot") and s.get("hot"):
            f[fl["hot"]] = "1"
        if fl.get("work") and s.get("work"):
            f[fl["work"]] = "1"
        cid = "".join(ch for ch in str(lead.get("client_id") or "") if ch.isdigit())
        if fl.get("client_id") and cid:
            f[fl["client_id"]] = cid
        utm = lead.get("utm") or {}
        for key in ("utm_product", "utm_manager", "utm_account", "utm_campaign_type"):
            val = utm.get(key) or s.get(key)
            if fl.get(key) and val:
                f[fl[key]] = str(val)[:250]
    else:
        f["TITLE"] = f"{prefix or 'Заявка с сайта'} {domain}".strip()
        if s.get("b24_source"):
            f["SOURCE_ID"] = s["b24_source"]
        f["SOURCE_DESCRIPTION"] = (lead.get("page") or f"https://{domain}/")[:1000]
        lines = [f"{a.get('q')}: {a.get('a')}" for a in answers]
        lines += [x for x in (f"Регион: {region}" if region else "", f"Город: {city}" if city else "") if x]
        f["COMMENTS"] = "\n".join(lines)
    return f


# ---------- Б24 ----------
_meta_cache: dict[tuple, tuple[float, Any]] = {}


async def _hook(source_id: int | None) -> str:
    if not source_id:
        raise crm_stats.CrmError("У интеграции не выбран портал Б24 (источник)")
    return crm_stats._webhook(crm_stats._source(int(source_id)))


async def portal_sources(source_id: int) -> list[tuple[str, str]]:
    key = ("src", source_id)
    hit = _meta_cache.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    res = await crm_stats._call(await _hook(source_id), "crm.status.list", {"filter": {"ENTITY_ID": "SOURCE"}, "order": {"SORT": "ASC"}})
    items = [(str(x["STATUS_ID"]), str(x["NAME"])) for x in res.get("result") or []]
    _meta_cache[key] = (time.time(), items)
    return items


async def meta(source_id: int, kind: str) -> dict[str, Any]:
    """Списки портала для настроек: типы лида, отделы, источники."""
    key = ("meta", source_id, kind)
    hit = _meta_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    hook = await _hook(source_id)
    fields = (await crm_stats._call(hook, "crm.lead.fields", {})).get("result") or {}
    fl = (DEFAULTS.get(kind) or {}).get("fields") or {}

    def items(code: str | None) -> list[dict[str, str]]:
        return [{"id": str(i["ID"]), "name": str(i["VALUE"])} for i in (fields.get(code or "", {}).get("items") or [])]

    out = {"lead_types": items(fl.get("lead_type")), "departments": items(fl.get("dept")), "origins": items(fl.get("origin")),
           "sources": [{"id": sid, "name": n} for sid, n in await portal_sources(source_id)],
           "uf_fields": sorted([{"code": k, "name": v.get("listLabel") or v.get("formLabel") or v.get("title") or k, "type": v.get("type")}
                                for k, v in fields.items() if k.startswith("UF_")], key=lambda x: x["name"])}
    _meta_cache[key] = (time.time(), out)
    return out


async def prepare(integ: dict[str, Any], lead: dict[str, Any], domain: str) -> dict[str, Any]:
    sources = None
    if integ["kind"] == "ydl" and integ["settings"].get("source_by_city") and integ.get("source_id"):
        try:
            sources = await portal_sources(int(integ["source_id"]))
        except Exception:  # noqa: BLE001 - без маршрутизации уйдёт в источник по умолчанию
            sources = None
    return build(integ, lead, domain, sources)


async def push(integ: dict[str, Any], lead: dict[str, Any], domain: str) -> int | None:
    fields = await prepare(integ, lead, domain)
    res = await crm_stats._call(await _hook(integ.get("source_id")), "crm.lead.add",
                                {"fields": fields, "params": {"REGISTER_SONET_EVENT": "Y"}})
    rid = res.get("result")
    return int(rid) if str(rid).isdigit() else None
