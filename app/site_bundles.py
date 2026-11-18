"""Фирменные квизы — копии страниц мойюрист24.рф (2026-11-18).

Бандл = папка app/site_bundles/<id>/ с оригинальными файлами страницы
(html/css/js/картинки) + manifest.json. При отдаче текстовые файлы
проходят однопроходную замену литералов оригинала (бренд, юрлицо, ИНН,
ОГРН, телефон в трёх форматах, почта, домен, путь, адрес отправки заявки)
на значения из настроек шаблона. Метрика оригинала вырезается; вместо неё
— счётчики аккаунтов домена (window.__YM_IDS) и прослойка __ymAll: каждая
цель квиза уходит во все счётчики, успешный POST /lead → reachGoal('lead').
Заявка квиза (свой формат у каждой страницы) приводится normalize() к общему
виду, который понимают интеграции (lead_integrations).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).parent / "site_bundles"
# в slim-образе нет /etc/mime.types — webp/woff2 иначе уходят как octet-stream
for _ext, _type in ((".webp", "image/webp"), (".svg", "image/svg+xml"), (".woff2", "font/woff2"), (".woff", "font/woff"),
                    (".js", "application/javascript"), (".css", "text/css"), (".pdf", "application/pdf")):
    mimetypes.add_type(_type, _ext)
TEXT_EXT = (".html", ".css", ".js", ".svg", ".txt")
BRAND_FIELDS = ("brand", "legal_name", "inn", "ogrn", "address", "phone", "email", "worktime")
_BAD = re.compile(r"[<>\"'`\\\r\n]")
_YM_CALL = re.compile(r"window\.ym\(\s*(?:\d+|YM_ID)\s*,\s*")
_YM_ZERO = re.compile(r"\bYM_ID\s*=\s*0\s*;")
_YM_SCRIPT = re.compile(r"<script[^>]*>(?:(?!</script>).)*?mc\.yandex(?:(?!</script>).)*?</script>", re.S)
_YM_NOSCRIPT = re.compile(r"<noscript>(?:(?!</noscript>).)*?mc\.yandex(?:(?!</noscript>).)*?</noscript>", re.S)
_YM_COMMENT = re.compile(r"<!--\s*/?\s*Yandex\.Metrika counter\s*-->")


@lru_cache(maxsize=1)
def manifests() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not ROOT.exists():
        return out
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        mf = d / "manifest.json"
        if not mf.exists():
            continue
        m = json.loads(mf.read_text("utf-8"))
        m["id"] = d.name
        m["files"] = sorted(str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*")
                            if p.is_file() and p.name != "manifest.json")
        out[d.name] = m
    return out


def get(bundle_id: str) -> dict[str, Any] | None:
    return manifests().get(str(bundle_id or ""))


def public_list() -> list[dict[str, Any]]:
    return [{"id": m["id"], "title": m["title"], "description": m.get("description", ""),
             "lead_type": m.get("lead_type", ""), "quiz_title": m.get("quiz_title", ""),
             "defaults": m.get("defaults", {}), "images": list((m.get("images") or {}).keys())}
            for m in manifests().values()]


def _clean(v: Any) -> str:
    return _BAD.sub("", str(v if v is not None else "")).strip()[:200]


def _ten(phone: str) -> str:
    d = "".join(ch for ch in phone if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


def values(m: dict[str, Any], cfg: dict[str, Any], domain: str) -> dict[str, str]:
    """Значения подстановки: из настроек шаблона (ключ задан, даже пустой —
    берём его), иначе — оригинал бандла."""
    base = {**(m.get("defaults") or {})}
    for k in BRAND_FIELDS:
        if k in (cfg or {}):
            base[k] = cfg.get(k) or ""
    phone = _clean(base.get("phone"))
    ten = _ten(phone)
    try:
        uni = domain.encode("ascii").decode("idna") if domain else ""
    except UnicodeError:
        uni = domain
    return {"brand": _clean(base.get("brand")), "legal_name": _clean(base.get("legal_name")),
            "inn": _clean(base.get("inn")), "ogrn": _clean(base.get("ogrn")), "email": _clean(base.get("email")),
            "phone_display": phone, "phone_8": ("8" + ten) if ten else "", "phone_7": ("7" + ten) if ten else "",
            "phone_10": ten, "domain": _clean(uni), "domain_puny": _clean(domain), "base": "/", "endpoint": "/lead"}


def _replacer(m: dict[str, Any], vals: dict[str, str]) -> Callable[[str], str]:
    pairs = sorted(((lit, vals.get(key, "")) for lit, key in m.get("replace") or []), key=lambda p: -len(p[0]))
    if not pairs:
        return lambda t: t
    table = dict(pairs)
    rx = re.compile("|".join(re.escape(lit) for lit, _ in pairs))
    return lambda t: rx.sub(lambda mm: table[mm.group(0)], t)


def metrika_head(ids: list[int]) -> str:
    ids = [int(i) for i in ids or [] if str(i).isdigit()]
    shim = ("<script>window.__YM_IDS=" + json.dumps(ids) + ";"
            "window.__ymAll=function(){var a=[].slice.call(arguments),ids=window.__YM_IDS||[];"
            "if(typeof window.ym!=='function'||!ids.length)return;"
            "if(a[0]==='getClientID'){return window.ym.apply(window,[ids[0]].concat(a))}"
            "ids.forEach(function(i){try{window.ym.apply(window,[i].concat(a))}catch(e){}})};"
            "(function(){var f=window.fetch;if(!f)return;window.fetch=function(u,o){var p=f.apply(this,arguments);"
            "try{if(o&&String(o.method).toUpperCase()==='POST'&&/(^|\\/)lead(\\?|$)/.test(String(u).replace(location.origin,'')))"
            "{p.then(function(r){if(r&&r.ok)window.__ymAll('reachGoal','lead')},function(){})}}catch(e){}return p}})();</script>")
    if not ids:
        return shim
    init = "".join(f'ym({i},"init",{{clickmap:true,trackLinks:true,accurateTrackBounce:true,webvisor:true}});' for i in ids)
    return (shim + "<script>(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};"
            "m[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0];k.async=1;k.src=r;"
            "a.parentNode.insertBefore(k,a)})(window,document,'script','https://mc.yandex.ru/metrika/tag.js','ym');"
            f"{init}</script>")


def resolve(bundle_id: str, path: str) -> str | None:
    """Путь запроса → файл бандла (только существующие файлы)."""
    m = get(bundle_id)
    if not m:
        return None
    rel = (path or "/").split("?")[0].lstrip("/")
    if rel in ("", "index.html"):
        rel = m.get("entry") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    return rel if rel in m["files"] else None


def image_override(m: dict[str, Any], rel: str, cfg: dict[str, Any]) -> str | None:
    """local:… из настроек шаблона вместо картинки бандла (логотип, фото)."""
    for key, file in (m.get("images") or {}).items():
        if file == rel:
            ref = str((cfg or {}).get(key) or "")
            return ref if ref.startswith("local:") else None
    return None


def render_file(bundle_id: str, rel: str, cfg: dict[str, Any], domain: str,
                metrika_ids: list[int] | None = None) -> tuple[bytes, str]:
    m = get(bundle_id)
    if not m or rel not in m["files"]:
        raise FileNotFoundError(rel)
    path = ROOT / m["id"] / rel
    ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    if rel.endswith((".js",)):
        ctype = "application/javascript"
    if not rel.endswith(TEXT_EXT):
        return path.read_bytes(), ctype
    text = path.read_text("utf-8")
    text = _replacer(m, values(m, cfg, domain))(text)
    if rel.endswith(".js"):
        text = _YM_ZERO.sub("YM_ID = (window.__YM_IDS||[])[0]||0;", _YM_CALL.sub("window.__ymAll(", text))
    if rel.endswith(".html"):
        text = _YM_COMMENT.sub("", _YM_NOSCRIPT.sub("", _YM_SCRIPT.sub("", text)))
        head = metrika_head(metrika_ids or [])
        text = text.replace("</head>", head + "</head>", 1) if "</head>" in text else head + text
    if ctype.startswith("text/") or ctype == "application/javascript":
        ctype += "; charset=utf-8"
    return text.encode("utf-8"), ctype


def preview_html(bundle_id: str, cfg: dict[str, Any], domain: str,
                 load_image: Callable[[str], str] | None = None) -> str:
    """Главная страница одним HTML (для превью в ЛК, iframe srcdoc): css/js
    встроены, картинки — data-URL."""
    m = get(bundle_id)
    if not m:
        raise FileNotFoundError(bundle_id)
    entry = m.get("entry") or "index.html"
    html_text = render_file(bundle_id, entry, cfg, domain)[0].decode("utf-8")
    data_urls: dict[str, str] = {}
    for rel in m["files"]:
        if rel.endswith((".html", ".css", ".js")):
            continue
        ref = image_override(m, rel, cfg)
        url = ""
        if ref and load_image:
            try:
                url = load_image(ref)
            except Exception:  # noqa: BLE001 - нет файла: оставляем оригинал
                url = ""
        if not url:
            raw = (ROOT / m["id"] / rel).read_bytes()
            url = f"data:{mimetypes.guess_type(rel)[0] or 'application/octet-stream'};base64,{base64.b64encode(raw).decode()}"
        data_urls[rel] = url
    rx_bin = re.compile(r"(?<![\w.-])/?(" + "|".join(re.escape(r) for r in sorted(data_urls, key=len, reverse=True)) + r")(?:\?v=[\w.]+)?") if data_urls else None

    def inline_bins(t: str) -> str:
        return rx_bin.sub(lambda mm: data_urls[mm.group(1)], t) if rx_bin else t

    def file_text(rel: str) -> str:
        return render_file(bundle_id, rel, cfg, domain)[0].decode("utf-8")

    def css_sub(mm: re.Match) -> str:
        rel = mm.group(1).lstrip("/")
        return f"<style>{inline_bins(file_text(rel))}</style>" if rel in m["files"] else ""

    def js_sub(mm: re.Match) -> str:
        rel = mm.group(1).lstrip("/")
        if rel not in m["files"]:
            return ""
        return "<script>" + inline_bins(file_text(rel)).replace("</script", "<\\/script") + "</script>"

    html_text = re.sub(r"<link[^>]+href=\"([^\"]+?\.css)(?:\?[^\"]*)?\"[^>]*>", css_sub, html_text)
    html_text = re.sub(r"<script[^>]+src=\"([^\"]+?\.js)(?:\?[^\"]*)?\"[^>]*>\s*</script>", js_sub, html_text)
    return inline_bins(html_text)


# ---------- заявки: формат каждой страницы → общий вид ----------
def _s(v: Any, n: int = 300) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


def _utm(v: Any) -> dict[str, str]:
    if not isinstance(v, dict):
        return {}
    return {str(k)[:40]: _s(x, 250) for k, x in list(v.items())[:30] if isinstance(k, str) and x not in (None, "")}


def normalize(bundle_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
    """{name, phone, region, city, answers:[{q,a}], quiz_title, quality, score,
    findings, method, utm, page, ref, client_id, spam}."""
    m = get(bundle_id) if bundle_id else None
    norm = m.get("normalizer") if m else "generic"
    out: dict[str, Any] = {"name": _s(data.get("name"), 100), "phone": _s(data.get("phone"), 40), "region": "", "city": "",
                           "answers": [], "quiz_title": (m or {}).get("quiz_title", ""), "quality": "", "score": "",
                           "findings": [], "method": "", "utm": _utm(data.get("utm")),
                           "page": _s(data.get("page") or data.get("source"), 1000), "ref": _s(data.get("ref") or data.get("referrer"), 500),
                           "client_id": _s(data.get("yandex_client_id"), 40), "spam": False}
    if norm == "mu_amnesty":
        ans = data.get("answers") if isinstance(data.get("answers"), dict) else {}
        for qid, q in (m.get("questions") or {}).items():
            if qid in ans and ans[qid] not in (None, ""):
                v = ans[qid]
                label = q.get("options", {}).get(str(v), v)
                out["answers"].append({"q": q.get("title", qid), "a": _s(label, 200)})
        out["region"] = _s(ans.get("regionName") or data.get("region"), 120)
        out["city"] = _s(ans.get("city") or data.get("city"), 120)
        out["name"] = out["name"] or _s(ans.get("name"), 100)
        out["score"] = _s(data.get("score"), 20)
    elif norm == "mu_pension":
        for line in data.get("summary") or []:
            q, _, a = str(line).partition(": ")
            out["answers"].append({"q": _s(q, 200), "a": _s(a, 300)})
        out["findings"] = [_s(x, 300) for x in (data.get("findings") or [])[:20]]
        out["region"] = _s(data.get("region"), 120)
        out["city"] = _s(data.get("city"), 120)
        out["method"] = _s(data.get("method"), 60)
        out["quality"] = _s(data.get("quality"), 20)
        out["score"] = _s(data.get("score"), 20)
        out["spam"] = bool(str(data.get("website") or "").strip())
    else:
        out["answers"] = [{"q": _s(a.get("q"), 200), "a": _s(a.get("a"), 300)}
                          for a in (data.get("answers") or [])[:20] if isinstance(a, dict)]
    # квизы передают только стандартные utm_* — остальные метки (utm_product,
    # utm_manager, utm_account, utm_campaign_type, yclid…) берём из адреса страницы
    try:
        from urllib.parse import parse_qsl, urlsplit
        for k, v in parse_qsl(urlsplit(out["page"]).query)[:40]:
            if (k.startswith("utm_") or k in ("yclid", "r_name")) and v:
                out["utm"].setdefault(k[:40], _s(v, 250))
    except ValueError:
        pass
    return out
