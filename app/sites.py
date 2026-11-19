"""Сайты и квизы для запусков (2026-11-18).

Шаблон (site_templates) — тип (landing | quiz) + конфиг: заголовок, оффер,
пункты, кнопка, телефон, цвет, картинка, вопросы квиза. HTML собирается
программно (render) — один адаптивный макет на тип, без ручной вёрстки.

Сайт (sites) — домен (из reg.ru) + шаблон + проект. Отдаёт его сам addAvto по
заголовку Host (см. main.py, site_host_middleware): nginx на домен сайта
проксирует на 127.0.0.1:8000 только /, /lead, /img/*, /robots.txt.

Заявка (POST /lead) → site_leads + лид в Б24 по вебхуку источника проекта
(«Конверсии → Источники Б24»), UTM из адреса страницы — отчёт находит лид по
utm_campaign. Счётчики Метрики, созданные запуском для домена, встраиваются
в страницу (metrika_ids), заявка шлёт reachGoal('lead').
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from . import db

KINDS = {"landing": "Лендинг", "quiz": "Квиз", "bundle": "Копия страницы (квиз МЮ)", "design": "Квиз-лендинг (10 дизайнов)"}
BRAND_FIELDS = {"brand": "Бренд", "legal_name": "Юрлицо", "inn": "ИНН", "ogrn": "ОГРН", "address": "Адрес",
                "phone": "Телефон", "email": "Email", "worktime": "Часы работы"}
SITE_STATUSES = {"new": "Не размещён", "dns": "Ждём DNS", "ssl": "Выпускаем SSL", "live": "Работает", "error": "Ошибка"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _init(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS site_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'landing',
        config TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL UNIQUE, template_id INTEGER,
        project_id INTEGER, status TEXT NOT NULL DEFAULT 'new', note TEXT NOT NULL DEFAULT '',
        metrika_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        deployed_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS site_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER NOT NULL, created_at TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}',
        b24_status TEXT NOT NULL DEFAULT '', b24_lead_id INTEGER, error TEXT NOT NULL DEFAULT '')""")


def _conn():
    c = db.connect()
    _init(c)
    return c


# ---------- шаблоны ----------
def _tpl(row) -> dict[str, Any]:
    d = dict(row)
    d["config"] = json.loads(d.get("config") or "{}")
    return d


def list_templates() -> list[dict[str, Any]]:
    with _conn() as c:
        return [_tpl(r) for r in c.execute("SELECT * FROM site_templates ORDER BY id DESC")]


def get_template(tid: int) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM site_templates WHERE id=?", (tid,)).fetchone()
    return _tpl(r) if r else None


def save_template(tid: int | None, name: str, kind: str, config: dict[str, Any]) -> dict[str, Any]:
    now, cfg = _now(), json.dumps(config, ensure_ascii=False)
    with _conn() as c:
        if tid:
            c.execute("UPDATE site_templates SET name=?, kind=?, config=?, updated_at=? WHERE id=?", (name, kind, cfg, now, tid))
        else:
            tid = int(c.execute("INSERT INTO site_templates (name, kind, config, created_at, updated_at) VALUES (?,?,?,?,?)",
                                (name, kind, cfg, now, now)).lastrowid)
    t = get_template(int(tid))
    assert t is not None
    return t


def delete_template(tid: int) -> bool:
    with _conn() as c:
        if c.execute("SELECT 1 FROM sites WHERE template_id=?", (tid,)).fetchone():
            raise ValueError("Шаблон используется сайтами — сначала смените им шаблон")
        return c.execute("DELETE FROM site_templates WHERE id=?", (tid,)).rowcount > 0


# ---------- сайты ----------
def norm_domain(value: str) -> str:
    from urllib.parse import urlsplit
    raw = (value or "").strip().lower()
    host = (urlsplit(raw if "://" in raw else "//" + raw).hostname or "").rstrip(".")
    host = host[4:] if host.startswith("www.") else host
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return host


def _site(row) -> dict[str, Any]:
    d = dict(row)
    d["metrika_ids"] = json.loads(d.get("metrika_ids") or "[]")
    d["status_label"] = SITE_STATUSES.get(d["status"], d["status"])
    return d


def list_sites() -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("""SELECT s.*, t.name AS template_name, t.kind AS template_kind,
            (SELECT COUNT(*) FROM site_leads l WHERE l.site_id=s.id) AS leads
            FROM sites s LEFT JOIN site_templates t ON t.id=s.template_id ORDER BY s.id DESC""").fetchall()
    return [_site(r) for r in rows]


def get_site(site_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    return _site(r) if r else None


def site_by_domain(domain: str) -> dict[str, Any] | None:
    d = norm_domain(domain)
    if not d:
        return None
    with _conn() as c:
        r = c.execute("SELECT * FROM sites WHERE domain=?", (d,)).fetchone()
    return _site(r) if r else None


def upsert_site(domain: str, template_id: int | None = None, project_id: int | None = None, status: str | None = None,
                note: str | None = None) -> dict[str, Any]:
    d, now = norm_domain(domain), _now()
    if not d or "." not in d:
        raise ValueError(f"Неверный домен: {domain!r}")
    with _conn() as c:
        row = c.execute("SELECT id FROM sites WHERE domain=?", (d,)).fetchone()
        if row is None:
            c.execute("INSERT INTO sites (domain, template_id, project_id, status, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                      (d, template_id, project_id, status or "new", note or "", now, now))
        else:
            sets, vals = ["updated_at=?"], [now]
            for k, v in (("template_id", template_id), ("project_id", project_id), ("status", status), ("note", note)):
                if v is not None:
                    sets.append(f"{k}=?")
                    vals.append(v)
            if status == "live":
                sets.append("deployed_at=?")
                vals.append(now)
            c.execute(f"UPDATE sites SET {', '.join(sets)} WHERE domain=?", (*vals, d))
    s = site_by_domain(d)
    assert s is not None
    _HOSTS.clear()
    return s


def delete_site(site_id: int) -> bool:
    with _conn() as c:
        ok = c.execute("DELETE FROM sites WHERE id=?", (site_id,)).rowcount > 0
    _HOSTS.clear()
    return ok


def add_metrika_counter(domain: str, counter_id: int) -> None:
    s = site_by_domain(domain)
    if not s or not counter_id:
        return
    ids = list(dict.fromkeys([*s["metrika_ids"], int(counter_id)]))[-10:]
    with _conn() as c:
        c.execute("UPDATE sites SET metrika_ids=?, updated_at=? WHERE id=?", (json.dumps(ids), _now(), s["id"]))


_HOSTS: dict[str, int | None] = {}


def site_id_for_host(host: str) -> int | None:
    """Кэш Host → id сайта (сбрасывается при изменении сайтов)."""
    h = norm_domain(host.split(":")[0]) if host else ""
    if not h:
        return None
    if h not in _HOSTS:
        s = site_by_domain(h)
        _HOSTS[h] = s["id"] if s else None
    return _HOSTS[h]


# ---------- заявки ----------
def add_lead(site_id: int, name: str, phone: str, data: dict[str, Any]) -> int:
    with _conn() as c:
        return int(c.execute("INSERT INTO site_leads (site_id, created_at, name, phone, data) VALUES (?,?,?,?,?)",
                             (site_id, _now(), name, phone, json.dumps(data, ensure_ascii=False))).lastrowid)


def set_lead_b24(lead_id: int, status: str, b24_id: int | None = None, error: str = "") -> None:
    with _conn() as c:
        c.execute("UPDATE site_leads SET b24_status=?, b24_lead_id=?, error=? WHERE id=?", (status, b24_id, error[:500], lead_id))


def get_lead(lead_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM site_leads WHERE id=?", (lead_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["data"] = json.loads(d.get("data") or "{}")
    return d


def list_leads(site_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = "SELECT l.*, s.domain FROM site_leads l JOIN sites s ON s.id=l.site_id"
    args: tuple = ()
    if site_id:
        q += " WHERE l.site_id=?"
        args = (site_id,)
    with _conn() as c:
        rows = c.execute(q + " ORDER BY l.id DESC LIMIT ?", (*args, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d.get("data") or "{}")
        out.append(d)
    return out


def b24_lead_fields(site: dict[str, Any], name: str, phone: str, data: dict[str, Any]) -> dict[str, Any]:
    utm = data.get("utm") or {}
    answers = data.get("answers") or []
    comments = "\n".join(f"{a.get('q')}: {a.get('a')}" for a in answers if isinstance(a, dict))
    fields: dict[str, Any] = {
        "TITLE": f"Заявка с сайта {site['domain']}",
        "NAME": name or "Без имени",
        "PHONE": [{"VALUE": phone, "VALUE_TYPE": "WORK"}],
        "SOURCE_DESCRIPTION": f"https://{site['domain']}/",
        "COMMENTS": comments,
    }
    for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
        if utm.get(k):
            fields[k.upper()] = str(utm[k])[:250]
    return fields


# ---------- рендер ----------
DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "landing": {"title": "Заголовок оффера", "subtitle": "Короткое пояснение, почему стоит оставить заявку",
                "bullets": ["Первое преимущество", "Второе преимущество", "Третье преимущество"],
                "cta": "Получить консультацию", "phone": "", "company": "", "color": "#2563eb", "image": "",
                "thanks": "Спасибо! Мы перезвоним в ближайшее время."},
    "quiz": {"title": "Ответьте на 3 вопроса", "subtitle": "и узнайте, как решить вашу задачу",
             "questions": [{"q": "Первый вопрос?", "options": ["Вариант 1", "Вариант 2"]}],
             "cta": "Узнать результат", "phone": "", "company": "", "color": "#2563eb", "image": "",
             "thanks": "Спасибо! Мы перезвоним в ближайшее время."},
}


def _e(v: Any) -> str:
    return html.escape(str(v or ""), quote=True)


def _img_src(ref: str) -> str:
    ref = (ref or "").strip()
    if ref.startswith("local:"):
        return "/img/" + ref[6:]
    return ref if ref.startswith(("https://", "http://")) else ""


def render(kind: str, config: dict[str, Any], domain: str = "", metrika_ids: list[int] | None = None,
           service: str | None = None) -> str:
    if kind == "design":
        from . import site_designs
        return site_designs.render(config, domain, metrika_ids, service)
    cfg = {**DEFAULT_CONFIG.get(kind, DEFAULT_CONFIG["landing"]), **(config or {})}
    color = cfg.get("color") or "#2563eb"
    if not (isinstance(color, str) and color.startswith("#") and len(color) in (4, 7)):
        color = "#2563eb"
    img = _img_src(cfg.get("image") or "")
    ids = [int(i) for i in (metrika_ids or []) if str(i).isdigit()]
    ym = "".join(
        f'ym({i},"init",{{clickmap:true,trackLinks:true,accurateTrackBounce:true,webvisor:true}});' for i in ids)
    metrika = ("<script>(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};"
               "m[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0];k.async=1;k.src=r;"
               "a.parentNode.insertBefore(k,a)})(window,document,'script','https://mc.yandex.ru/metrika/tag.js','ym');"
               f"{ym}</script>") if ids else ""
    goal = ";".join(f'ym({i},"reachGoal","lead")' for i in ids)
    phone = _e(cfg.get("phone"))
    phone_link = "".join(ch for ch in str(cfg.get("phone") or "") if ch.isdigit() or ch == "+")
    head_phone = f'<a class="ph" href="tel:{phone_link}">{phone}</a>' if phone else ""
    form = (f'<form id="lf" class="form"><input name="name" placeholder="Ваше имя" autocomplete="name">'
            f'<input name="phone" type="tel" placeholder="+7 (___) ___-__-__" required autocomplete="tel">'
            f'<label class="ok"><input type="checkbox" required checked> Согласен на обработку персональных данных</label>'
            f'<button type="submit">{_e(cfg.get("cta"))}</button><div class="msg" id="lm"></div></form>')
    if kind == "quiz":
        qs = [q for q in (cfg.get("questions") or []) if isinstance(q, dict) and q.get("q")]
        steps = "".join(
            f'<div class="step" data-i="{n}"{"" if n == 0 else " hidden"}><div class="qn">Вопрос {n + 1} из {len(qs)}</div>'
            f'<h2>{_e(q["q"])}</h2><div class="opts">'
            + "".join(f'<button type="button" class="opt" data-q="{_e(q["q"])}" data-a="{_e(o)}">{_e(o)}</button>'
                      for o in (q.get("options") or []) if o)
            + "</div></div>" for n, q in enumerate(qs))
        body = (f'<section class="quiz"><div class="bar"><i id="pb"></i></div>{steps}'
                f'<div class="step" data-i="{len(qs)}"{"" if not qs else " hidden"}><h2>Куда отправить результат?</h2>{form}</div></section>')
    else:
        bullets = "".join(f"<li>{_e(b)}</li>" for b in (cfg.get("bullets") or []) if b)
        body = f'<ul class="bul">{bullets}</ul>{form}'
    hero_img = f'<img class="hero" src="{_e(img)}" alt="">' if img else ""
    legal = [x for x in (cfg.get("legal_name") or cfg.get("company"),
                         f"ИНН {cfg['inn']}" if cfg.get("inn") else "", f"ОГРН {cfg['ogrn']}" if cfg.get("ogrn") else "",
                         cfg.get("address"), cfg.get("email"), cfg.get("worktime")) if x]
    company = " · ".join(_e(x) for x in legal)
    brand = _e(cfg.get("brand")) or _e(domain)
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(cfg.get("title"))}</title><meta name="description" content="{_e(cfg.get("subtitle"))}">
<style>:root{{--c:{color}}}*{{box-sizing:border-box}}body{{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111;background:#f6f7fb}}
header{{display:flex;justify-content:space-between;align-items:center;padding:14px 20px;background:#fff;box-shadow:0 1px 0 #e5e7eb}}
header b{{font-size:15px}}.ph{{color:var(--c);font-weight:700;text-decoration:none}}
main{{max-width:760px;margin:0 auto;padding:28px 16px 40px}}h1{{font-size:clamp(26px,5vw,38px);line-height:1.15;margin:0 0 10px}}
.sub{{font-size:18px;color:#4b5563;margin:0 0 20px}}.hero{{width:100%;border-radius:14px;margin:0 0 20px;display:block}}
.bul{{padding:0;list-style:none;margin:0 0 22px}}.bul li{{padding:8px 0 8px 30px;position:relative}}.bul li:before{{content:"✓";position:absolute;left:0;color:var(--c);font-weight:800}}
.form{{display:grid;gap:10px;background:#fff;padding:18px;border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.06)}}
.form input[name]{{padding:14px;border:1px solid #d1d5db;border-radius:10px;font-size:16px}}.ok{{font-size:12.5px;color:#6b7280}}
button{{cursor:pointer;font:inherit}}.form button,.opt{{padding:15px;border:0;border-radius:10px;background:var(--c);color:#fff;font-weight:700;font-size:17px}}
.quiz{{background:#fff;padding:20px;border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.06)}}.quiz .form{{box-shadow:none;padding:0}}
.bar{{height:6px;background:#e5e7eb;border-radius:3px;margin-bottom:16px}}.bar i{{display:block;height:100%;width:0;background:var(--c);border-radius:3px;transition:width .2s}}
.qn{{color:#6b7280;font-size:13px}}.opts{{display:grid;gap:8px}}.opt{{background:#fff;color:#111;border:2px solid #e5e7eb;text-align:left;font-weight:600}}.opt:hover{{border-color:var(--c)}}
.msg{{font-size:14px}}footer{{text-align:center;color:#9ca3af;font-size:12px;padding:20px}}</style>{metrika}</head>
<body><header><b>{brand}</b>{head_phone}</header><main><h1>{_e(cfg.get("title"))}</h1><p class="sub">{_e(cfg.get("subtitle"))}</p>{hero_img}{body}</main>
<footer>{company}</footer>
<script>(function(){{var ans=[],steps=[].slice.call(document.querySelectorAll('.step')),pb=document.getElementById('pb');
function show(i){{steps.forEach(function(s){{s.hidden=+s.dataset.i!==i}});if(pb)pb.style.width=(100*i/Math.max(1,steps.length-1))+'%'}}
[].forEach.call(document.querySelectorAll('.opt'),function(b){{b.onclick=function(){{ans.push({{q:b.dataset.q,a:b.dataset.a}});show(+b.closest('.step').dataset.i+1)}}}});
var f=document.getElementById('lf'),m=document.getElementById('lm'),sent=false;f.onsubmit=function(e){{e.preventDefault();if(sent)return;
var p=f.phone.value.replace(/[^0-9+]/g,'');if(p.replace(/\\D/g,'').length<10){{m.textContent='Проверьте номер телефона';m.style.color='#dc2626';return}}
var q={{}};new URLSearchParams(location.search).forEach(function(v,k){{if(/^utm_|^yclid$/.test(k))q[k]=v}});
sent=true;f.querySelector('button').disabled=true;m.style.color='';m.textContent='Отправляем…';
fetch('/lead',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:f.name.value,phone:p,answers:ans,utm:q,page:location.href,ref:document.referrer}})}})
.then(function(r){{if(!r.ok)throw 0;try{{{goal}}}catch(x){{}};f.innerHTML='<p><b>{_e(cfg.get("thanks"))}</b></p>'}})
.catch(function(){{sent=false;f.querySelector('button').disabled=false;m.textContent='Не отправилось — попробуйте ещё раз или позвоните нам';m.style.color='#dc2626'}})}};show(0)}})();</script>
</body></html>"""
