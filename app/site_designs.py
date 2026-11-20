"""Дизайны квиз-лендингов для A/B-тестов (2026-11-18).

Шаблон kind="design": config = {design, direction, тексты (title, steps, faq…),
бренд и контакты, integration_id}. 10 дизайнов — у каждого своя вёрстка,
шрифт, палитра и СВОЯ механика формы:

  chat       — переписка с дежурным юристом (кнопки-ответы, имя → город → телефон)
  tiles      — пошаговый квиз крупными плитками с пояснениями
  split      — экран пополам: оффер слева, квиз справа (липкий)
  noir       — тёмный премиальный, нумерованный список ответов
  editorial  — экспертная статья, анкета радиокнопками внутри текста
  calc       — калькулятор: ползунок + уточнения пилюлями → расчёт → форма
  flow       — «один вопрос на экран» поверх страницы, клавиши A–D / 1–4
  yesno      — проверка утверждениями «Да / Нет»
  official   — спокойная анкета с выпадающими списками в одной форме
  bold       — яркий промо, 3 быстрых шага пилюлями + липкая кнопка звонка

2026-11-19 — юр. направления (legal_designs.py, ещё 15 дизайнов) и разбивка на
подуслуги как у Яндекс Услуг: config.services = [{id, t, d, title, subtitle,
subs, steps}] — первый шаг квиза «С каким вопросом?», дальше вопросы выбранной
подуслуги + общие steps. ?s=<id> в адресе (группа объявлений) — заголовок
подуслуги и первый шаг уже выбран. config.widget — интерактив до квиза
(срок по дате, чек-лист, чипсы, калькулятор из двух ползунков, стадии);
form_booking / form_messenger — запись на время и «куда ответить» в форме.

Заявка: POST /lead {name, phone, city, answers:[{q,a}], quiz_title, utm, page,
ref, yandex_client_id} — нормализуется site_bundles.normalize (generic) и
уходит интеграцией шаблона (МЮ / ЮДЛ / Б24). Метрика — счётчики аккаунтов
домена (site_bundles.metrika_head): цели quiz_start, quiz_step_N, lead_submit, lead.
"""
from __future__ import annotations

import html
import json
from typing import Any

from . import site_bundles

DESIGNS: dict[str, dict[str, str]] = {
    "chat": {"title": "Чат с юристом", "form": "переписка в мессенджере"},
    "tiles": {"title": "Плитки по шагам", "form": "пошаговый квиз, плитки с пояснениями"},
    "split": {"title": "Экран пополам", "form": "квиз справа от оффера"},
    "noir": {"title": "Тёмный премиум", "form": "нумерованный список ответов"},
    "editorial": {"title": "Экспертная статья", "form": "анкета внутри статьи"},
    "calc": {"title": "Калькулятор", "form": "ползунок + уточнения → расчёт"},
    "flow": {"title": "Вопрос на весь экран", "form": "по одному вопросу, клавиши A–D"},
    "yesno": {"title": "Проверка «Да / Нет»", "form": "утверждения с ответами Да/Нет"},
    "official": {"title": "Анкета", "form": "выпадающие списки в одной форме"},
    "bold": {"title": "Яркий промо", "form": "3 быстрых шага + липкая кнопка"},
}
DIRECTIONS = {"bfl": "БФЛ", "pension": "Пенсия", "legal": "Юр. услуги"}

FONTS = {
    "chat": "Inter:wght@400;500;600;700;800", "tiles": "Montserrat:wght@400;500;600;700;800",
    "split": "Rubik:wght@400;500;600;700", "noir": "Playfair+Display:wght@500;600;700&family=Inter:wght@300;400;500;600",
    "editorial": "PT+Serif:wght@400;700&family=PT+Sans:wght@400;700", "calc": "Nunito:wght@400;600;700;800",
    "flow": "Golos+Text:wght@400;500;600;700", "yesno": "IBM+Plex+Sans:wght@400;500;600;700",
    "official": "PT+Sans:wght@400;700", "bold": "Unbounded:wght@500;700;800&family=Manrope:wght@400;600;700",
}

CITIES = ["Москва", "Санкт-Петербург", "Екатеринбург", "Новосибирск", "Казань", "Нижний Новгород", "Челябинск",
          "Самара", "Омск", "Ростов-на-Дону", "Уфа", "Красноярск", "Пермь", "Воронеж", "Волгоград", "Краснодар",
          "Саратов", "Тюмень", "Тольятти", "Ижевск", "Барнаул", "Ульяновск", "Иркутск", "Хабаровск", "Ярославль",
          "Владивосток", "Махачкала", "Томск", "Оренбург", "Кемерово", "Новокузнецк", "Рязань", "Астрахань",
          "Набережные Челны", "Пенза", "Липецк", "Киров", "Чебоксары", "Тула", "Калининград", "Курган", "Магнитогорск",
          "Сургут", "Нижний Тагил", "Каменск-Уральский"]

ICONS = {
    "check": '<path d="M20 6 9 17l-5-5"/>', "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>', "phone": '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
    "doc": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
    "scale": '<path d="M12 3v18M5 21h14M7 7h10M7 7l-3 7a3 3 0 0 0 6 0zM17 7l-3 7a3 3 0 0 0 6 0z"/>',
    "arrow": '<path d="M5 12h14M13 5l7 7-7 7"/>', "send": '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
}


def ic(name: str, size: int = 20) -> str:
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS.get(name, "")}</svg>')


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _ctx(cfg: dict[str, Any]) -> dict[str, Any]:
    c = dict(cfg or {})
    phone = str(c.get("phone") or "")
    d = "".join(ch for ch in phone if ch.isdigit())
    if len(d) >= 10:
        ten = d[-10:]
        c["phone_href"] = ("8" + ten) if ten.startswith("800") else ("+7" + ten)
    else:
        c["phone_href"] = d
    c["steps"] = [s for s in _list(c.get("steps")) if isinstance(s, dict) and s.get("q") and _list(s.get("options"))]
    for s in c["steps"]:
        s["options"] = [o if isinstance(o, dict) else {"t": str(o)} for o in s["options"] if o][:6]
    svcs = []
    for n, sv in enumerate(_list(c.get("services"))):
        if not isinstance(sv, dict) or not sv.get("t"):
            continue
        sv = dict(sv)
        sv["id"] = "".join(ch for ch in str(sv.get("id") or f"s{n + 1}").lower() if ch.isalnum() or ch in "-_")[:40] or f"s{n + 1}"
        sv["steps"] = [s for s in _list(sv.get("steps")) if isinstance(s, dict) and s.get("q") and _list(s.get("options"))][:4]
        for s in sv["steps"]:
            s["options"] = [o if isinstance(o, dict) else {"t": str(o)} for o in s["options"] if o][:6]
        sv["subs"] = [str(x) for x in _list(sv.get("subs")) if x][:14]
        svcs.append(sv)
    c["services"] = svcs[:14]
    return c


def apply_service(c: dict[str, Any], service: str | None) -> int:
    """?s=<id>: заголовок/подзаголовок подуслуги; индекс для предвыбора первого шага (-1 — нет)."""
    sid = (service or "").strip().lower()
    for k, sv in enumerate(c.get("services") or []):
        if sid and sv["id"] == sid:
            for key in ("title", "subtitle", "badge"):
                if sv.get(key):
                    c[key] = sv[key]
            return k
    return -1


def svc_list(c: dict[str, Any], cls: str = "svc", btn: str = "Разобрать мою ситуацию", subs: bool = False) -> str:
    """Подуслуги ссылками ?s=id (без JS — перезагрузка с заголовком подуслуги; с JS — выбор в квизе)."""
    out = []
    for k, s in enumerate(c.get("services") or []):
        sub = ("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in s.get("subs") or []) + "</ul>") if subs and s.get("subs") else ""
        out.append(f'<a class="svc-i" href="?s={_e(s["id"])}" data-svc="{k}"><b>{_e(s["t"])}</b>'
                   + (f'<span>{_e(s.get("d"))}</span>' if s.get("d") else "") + sub + f'<em>{_e(btn)} →</em></a>')
    return f'<div class="{cls}">' + "".join(out) + "</div>" if out else ""


# ───────────────────────── общие блоки ─────────────────────────
def header(c: dict[str, Any], cls: str = "") -> str:
    brand = _e(c.get("brand") or "")
    ph = _e(c.get("phone") or "")
    right = (f'<a class="hd-ph" href="tel:{_e(c["phone_href"])}">{ic("phone", 18)}<span>{ph}</span></a>' if ph else "")
    return f'<header class="hd {cls}"><div class="wrap hd-in"><div class="hd-brand">{brand}</div>{right}</div></header>'


def bullets(c: dict[str, Any], cls: str = "bul") -> str:
    return f'<ul class="{cls}">' + "".join(f"<li>{ic('check', 18)}<span>{_e(b)}</span></li>" for b in _list(c.get("bullets"))[:4]) + "</ul>"


def trust(c: dict[str, Any], cls: str = "trust") -> str:
    items = _list(c.get("trust"))[:4]
    if not items:
        return ""
    return f'<div class="{cls}">' + "".join(
        f'<div class="tr-i"><b>{_e(t.get("n"))}</b><span>{_e(t.get("t"))}</span></div>' for t in items if isinstance(t, dict)) + "</div>"


def how(c: dict[str, Any], heading: str = "Как мы работаем", cls: str = "how") -> str:
    items = [h for h in _list(c.get("how")) if isinstance(h, dict)][:4]
    if not items:
        return ""
    return (f'<section class="{cls}"><div class="wrap"><h2>{_e(heading)}</h2><ol>'
            + "".join(f'<li><span class="hw-n">{n}</span><div><b>{_e(h.get("t"))}</b><p>{_e(h.get("d"))}</p></div></li>'
                      for n, h in enumerate(items, 1)) + "</ol></div></section>")


def faq(c: dict[str, Any], heading: str = "Частые вопросы", cls: str = "faq") -> str:
    items = [f for f in _list(c.get("faq")) if isinstance(f, dict)][:6]
    if not items:
        return ""
    return (f'<section class="{cls}"><div class="wrap"><h2>{_e(heading)}</h2>'
            + "".join(f'<details><summary>{_e(f.get("q"))}</summary><p>{_e(f.get("a"))}</p></details>' for f in items)
            + "</div></section>")


def footer(c: dict[str, Any], cls: str = "ft") -> str:
    legal = [x for x in (c.get("legal_name"), f"ИНН {c['inn']}" if c.get("inn") else "", f"ОГРН {c['ogrn']}" if c.get("ogrn") else "",
                         c.get("address")) if x]
    contacts = [x for x in (f'<a href="tel:{_e(c["phone_href"])}">{_e(c["phone"])}</a>' if c.get("phone") else "",
                            f'<a href="mailto:{_e(c["email"])}">{_e(c["email"])}</a>' if c.get("email") else "",
                            _e(c.get("worktime") or "")) if x]
    return (f'<footer class="{cls}"><div class="wrap"><div class="ft-b">{_e(c.get("brand") or "")}</div>'
            f'<div>{" · ".join(_e(x) for x in legal)}</div><div>{" · ".join(contacts)}</div>'
            f'<div><a href="/privacy">Политика конфиденциальности</a></div>'
            '<p class="ft-d">Информация на сайте носит справочный характер и не является публичной офертой. '
            'Результат зависит от обстоятельств дела; первичная консультация ни к чему не обязывает.</p></div></footer>')


def quiz_box(cls: str, head: str = "", letters: bool = False) -> str:
    return (f'<div class="qz {cls}" data-qz{" data-letters" if letters else ""}>{head}'
            '<div class="qz-top"><button type="button" class="qz-back" hidden aria-label="Назад">←</button>'
            '<span class="qz-count"></span></div><div class="qz-bar"><i></i></div><div class="qz-body"></div></div>')


# ───────────────────────── дизайны (вёрстка) ─────────────────────────
def _hero_text(c: dict[str, Any], h: str = "h1") -> str:
    badge = f'<span class="badge">{_e(c.get("badge"))}</span>' if c.get("badge") else ""
    return f'{badge}<{h} class="h-title">{_e(c.get("title"))}</{h}><p class="h-sub">{_e(c.get("subtitle"))}</p>'


def d_chat(c):
    op = _e(c.get("operator") or "Юрист")
    av = "".join(w[:1] for w in str(c.get("operator") or "Ю").split()[:2]).upper()
    chat = (f'<div class="ch" data-chat><div class="ch-head"><span class="ch-av">{_e(av)}</span><div><b>{op}</b>'
            f'<small>{_e(c.get("operator_role") or "дежурный юрист")} · <i class="ch-on"></i>онлайн</small></div></div>'
            '<div class="ch-log" aria-live="polite"></div><div class="ch-foot"></div></div>')
    return (header(c) + f'<main><section class="hero"><div class="wrap hero-in"><div class="hero-l">{_hero_text(c)}{bullets(c)}'
            f'{trust(c)}</div><div class="hero-r">{chat}</div></div></section>{how(c)}{faq(c)}</main>' + footer(c))


def d_tiles(c):
    return (header(c) + f'<main><section class="hero"><div class="wrap hero-c">{_hero_text(c)}{bullets(c, "bul bul-row")}</div></section>'
            f'<section class="qz-sec"><div class="wrap">{quiz_box("qz-tiles")}</div></section>'
            f'<section class="tr-sec"><div class="wrap">{trust(c)}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def d_split(c):
    return (f'<div class="sp"><aside class="sp-l"><div class="sp-l-in"><div class="hd-brand">{_e(c.get("brand") or "")}</div>'
            f'{_hero_text(c)}{bullets(c)}{trust(c)}'
            + (f'<a class="sp-ph" href="tel:{_e(c["phone_href"])}">{ic("phone", 18)} {_e(c["phone"])}</a>' if c.get("phone") else "")
            + f'</div></aside><section class="sp-r"><div class="sp-r-in">{quiz_box("qz-split")}</div></section></div>'
            f'<main>{how(c)}{faq(c)}</main>' + footer(c))


def d_noir(c):
    return (header(c, "hd-noir") + f'<main><section class="hero"><div class="wrap"><div class="nr-line"></div>{_hero_text(c)}'
            f'{bullets(c)}</div></section><section class="qz-sec"><div class="wrap nr-grid"><div class="nr-side">{trust(c, "trust trust-col")}</div>'
            f'{quiz_box("qz-list", letters=True)}</div></section>{how(c, "Порядок работы")}{faq(c, "Вопросы")}</main>' + footer(c))


def d_editorial(c):
    paras = "".join(f'<h2>{_e(f.get("q"))}</h2><p>{_e(f.get("a"))}</p>' for f in _list(c.get("faq"))[:5] if isinstance(f, dict))
    quote = "".join(f"<li>{_e(b)}</li>" for b in _list(c.get("bullets"))[:3])
    return (header(c, "hd-ed") + f'<main class="ed"><article class="wrap ed-col"><div class="ed-kicker">{_e(c.get("badge") or "Разбор")}</div>'
            f'<h1 class="h-title">{_e(c.get("title"))}</h1><p class="ed-lead">{_e(c.get("subtitle"))}</p>'
            f'<div class="ed-meta">{_e(c.get("operator") or "")}{", " + _e(c.get("operator_role")) if c.get("operator_role") else ""}</div>'
            f'<aside class="ed-quote"><ul>{quote}</ul></aside>{paras}'
            f'<section class="ed-form" id="anketa"><h2>{_e(c.get("form_title") or "Анкета")}</h2><p>{_e(c.get("result_text") or "")}</p>'
            '<div data-all="radio"></div></section></article></main>' + footer(c))


def d_calc(c):
    sl = c.get("slider") if isinstance(c.get("slider"), dict) else {}
    calc = (f'<div class="cc" data-calc><div class="cc-sl"><label>{_e(sl.get("label") or "Сумма")}</label>'
            '<div class="cc-val"><output></output></div><input type="range"><div class="cc-mm"><span></span><span></span></div></div>'
            '<div class="cc-steps"></div><button type="button" class="btn cc-go">' + _e(c.get("cta") or "Рассчитать")
            + '</button><div class="cc-res" hidden></div></div>')
    return (header(c) + f'<main><section class="hero"><div class="wrap cc-grid"><div>{_hero_text(c)}{bullets(c)}{trust(c)}</div>'
            f'<div>{calc}</div></div></section>{how(c)}{faq(c)}</main>' + footer(c))


def d_flow(c):
    return (header(c, "hd-flow") + f'<main><section class="hero"><div class="wrap fl-hero">{_hero_text(c)}'
            f'<button type="button" class="btn fl-start">{_e(c.get("cta") or "Начать")} {ic("arrow")}</button>'
            '<p class="fl-hint">Около минуты · можно отвечать клавишами A–D</p></div></section>'
            f'<section class="tr-sec"><div class="wrap">{trust(c)}</div></section>{how(c)}{faq(c)}</main>' + footer(c)
            + '<div class="fl-ov" hidden><button type="button" class="fl-x" aria-label="Закрыть">×</button>'
            + quiz_box("qz-flow", letters=True) + "</div>")


def d_yesno(c):
    return (header(c) + f'<main><section class="hero"><div class="wrap yn-hero">{_hero_text(c)}</div></section>'
            f'<section class="qz-sec"><div class="wrap">{quiz_box("qz-yn")}</div></section>'
            f'<section class="wrap yn-b">{bullets(c)}{trust(c)}</section>{how(c)}{faq(c)}</main>' + footer(c))


def d_official(c):
    return (header(c, "hd-of") + f'<main><div class="of-strip"><div class="wrap">{_e(c.get("badge") or "Консультация")}</div></div>'
            f'<section class="hero"><div class="wrap of-grid"><div>{_hero_text(c)}{bullets(c)}</div>'
            f'<div class="of-card"><h2>{_e(c.get("form_title") or "Анкета")}</h2><p class="of-note">{_e(c.get("result_text") or "")}</p>'
            f'<div data-all="select"></div></div></div></section><section class="tr-sec"><div class="wrap">{trust(c)}</div></section>'
            f'{how(c, "Порядок обращения")}{faq(c, "Справочная информация")}</main>' + footer(c))


def d_bold(c):
    sticky = (f'<a class="bd-sticky" href="tel:{_e(c["phone_href"])}">{ic("phone", 18)} Позвонить</a>' if c.get("phone") else "")
    return (header(c, "hd-bold") + f'<main><section class="hero"><div class="wrap bd-grid"><div>{_hero_text(c)}{bullets(c)}</div>'
            f'{quiz_box("qz-pills")}</div></section><section class="tr-sec"><div class="wrap">{trust(c)}</div></section>'
            f'{how(c)}{faq(c)}</main>' + footer(c) + sticky)


LAYOUT = {"chat": d_chat, "tiles": d_tiles, "split": d_split, "noir": d_noir, "editorial": d_editorial, "calc": d_calc,
          "flow": d_flow, "yesno": d_yesno, "official": d_official, "bold": d_bold}

# ───────────────────────── стили ─────────────────────────
BASE_CSS = """*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}body{margin:0;line-height:1.5}img{max-width:100%}
a{color:inherit}button{font:inherit;cursor:pointer}input,select,textarea{font:inherit}
.wrap{max-width:1120px;margin:0 auto;padding:0 16px}.ic{flex:none;vertical-align:middle}
.hd-in{display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:60px}.hd-brand{font-weight:700;font-size:18px}
.hd-ph{display:inline-flex;gap:8px;align-items:center;text-decoration:none;font-weight:700;white-space:nowrap}
.badge{display:inline-block;font-size:13px;font-weight:600;padding:5px 12px;border-radius:99px;margin-bottom:14px}
.h-title{margin:0 0 12px;line-height:1.12;font-size:clamp(28px,5vw,48px)}.h-sub{margin:0 0 18px;font-size:clamp(16px,2.2vw,19px);opacity:.85}
.bul{list-style:none;padding:0;margin:0 0 20px;display:grid;gap:10px}.bul li{display:flex;gap:10px;align-items:flex-start}
.trust{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:8px 0}.tr-i{display:flex;flex-direction:column;gap:2px}.tr-i b{font-size:20px}.tr-i span{font-size:13.5px;opacity:.8}
section{padding:36px 0}.how ol{list-style:none;padding:0;margin:0;display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.how li{display:flex;gap:12px}.hw-n{flex:none;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font-weight:700}.how p{margin:4px 0 0;font-size:14.5px;opacity:.85}
h2{font-size:clamp(22px,3.2vw,30px);margin:0 0 18px;line-height:1.2}
.faq details{border-bottom:1px solid rgba(127,127,127,.25);padding:14px 0}.faq summary{cursor:pointer;font-weight:600;list-style:none;display:flex;justify-content:space-between;gap:12px}
.faq summary::after{content:"+";font-size:22px;line-height:1}.faq details[open] summary::after{content:"–"}.faq p{margin:10px 0 0;opacity:.85}
.ft{padding:28px 0 90px;font-size:13px;opacity:.85}.ft .wrap>div{margin:3px 0}.ft-b{font-weight:700;font-size:15px}.ft-d{margin-top:10px;font-size:12px;opacity:.75}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:0;border-radius:12px;padding:15px 22px;font-weight:700;font-size:17px;text-decoration:none}
.btn:disabled{opacity:.6}
.qz-top{display:flex;align-items:center;gap:10px;min-height:28px;font-size:13.5px}.qz-back{border:0;background:none;font-size:20px;padding:0 6px;color:inherit;opacity:.7}
.qz-bar{height:6px;border-radius:6px;overflow:hidden;margin:6px 0 16px;background:rgba(127,127,127,.18)}.qz-bar i{display:block;height:100%;width:0;transition:width .3s}
.qz-q{margin:0 0 6px;font-size:clamp(19px,2.6vw,24px);line-height:1.25}.qz-hint{margin:0 0 16px;font-size:14.5px;opacity:.75}
.qz-opts{display:grid;gap:10px}.qz-opt{display:flex;align-items:center;gap:12px;text-align:left;width:100%;border:2px solid transparent;transition:.15s}
.qz-mark{flex:none}.qz-t{font-weight:600}.qz-d{display:block;font-size:13px;opacity:.7;font-weight:400}.qz-opt>span:nth-child(2){flex:1}
.qz-next{margin-top:14px;width:100%}
.lf{display:grid;gap:10px;margin-top:6px}.lf label{display:grid;gap:4px;font-size:13.5px;font-weight:600}
.lf input[type=text],.lf input[type=tel],.lf input:not([type]),.lf select{width:100%;padding:13px 14px;border-radius:10px;border:1px solid rgba(127,127,127,.4);font-size:16px;background:#fff;color:#111}
.lf .lf-ok{display:flex;gap:8px;align-items:flex-start;font-weight:400;font-size:12.5px;opacity:.85}.lf-ok input{margin-top:3px}
.lf-msg{margin:0;font-size:14px;color:#dc2626;min-height:0}.lf-note{margin:0;font-size:12.5px;opacity:.7}
.thx{text-align:center;padding:10px 0}.thx b{display:block;font-size:22px;margin-bottom:8px}
.err input{border-color:#dc2626!important}
@media(max-width:820px){section{padding:28px 0}}"""

CSS = {
"chat": """body{font-family:Inter,system-ui,sans-serif;background:#eef2f1;color:#13201c}.hd{background:#fff;border-bottom:1px solid #dfe7e4}.hd-ph{color:#0f8a5f}
.badge{background:#d8f3e7;color:#0b6b49}.hero{padding:36px 0}.hero-in{display:grid;grid-template-columns:1.05fr .95fr;gap:32px;align-items:start}
.bul .ic{color:#12a26d}.tr-i b{color:#0f8a5f}
.ch{background:#e5ddd5 radial-gradient(circle at 20% 20%,rgba(255,255,255,.35),transparent 40%);border-radius:18px;overflow:hidden;box-shadow:0 18px 50px rgba(15,60,45,.18);display:flex;flex-direction:column;height:560px;position:sticky;top:16px}
.ch-head{display:flex;gap:10px;align-items:center;background:#0f8a5f;color:#fff;padding:12px 14px}.ch-head small{display:block;opacity:.85;font-size:12.5px}
.ch-av{width:42px;height:42px;border-radius:50%;background:#fff;color:#0f8a5f;display:grid;place-items:center;font-weight:800}
.ch-on{display:inline-block;width:8px;height:8px;border-radius:50%;background:#7dffb8;margin-right:4px}
.ch-log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px}
.ch-msg{max-width:84%;padding:9px 12px;border-radius:12px;font-size:15px;line-height:1.4;box-shadow:0 1px 1px rgba(0,0,0,.08);animation:pop .2s}
.ch-msg small{display:block;margin-top:4px;opacity:.7;font-size:12.5px}.bot{background:#fff;border-top-left-radius:3px;align-self:flex-start}.me{background:#d9fdd3;align-self:flex-end;border-top-right-radius:3px}
.typing i{display:inline-block;width:7px;height:7px;margin:0 2px;border-radius:50%;background:#9aa;animation:bl 1s infinite}.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}
@keyframes bl{50%{opacity:.25}}@keyframes pop{from{transform:translateY(6px);opacity:0}}
.ch-foot{background:#f0f2f5;padding:10px}.ch-chips{display:flex;flex-wrap:wrap;gap:8px}.ch-chips button{border:1.5px solid #0f8a5f;color:#0f8a5f;background:#fff;border-radius:99px;padding:8px 14px;font-weight:600;font-size:14.5px}
.ch-chips button:hover{background:#0f8a5f;color:#fff}.ch-in{display:flex;gap:8px}.ch-in input{flex:1;border:0;border-radius:22px;padding:12px 16px;font-size:16px}
.ch-in button{width:46px;height:46px;border-radius:50%;border:0;background:#0f8a5f;color:#fff;font-size:18px}.ch-in.err input{outline:2px solid #dc2626}
.ch-ok{display:flex;gap:6px;font-size:12px;margin-top:6px;color:#456}.ch-wait{text-align:center;font-size:14px;color:#456}
.how,.faq{background:#fff}.hw-n{background:#d8f3e7;color:#0b6b49}.ft{background:#13201c;color:#cfe}
@media(max-width:860px){.hero-in{grid-template-columns:1fr}.ch{height:520px;position:static}}""",
"tiles": """body{font-family:Montserrat,system-ui,sans-serif;background:#f6f5ff;color:#1d1b3a}.hd{background:#fff}.hd-ph{color:#5b3df5}
.hero{background:linear-gradient(160deg,#5b3df5,#8e6bff);color:#fff;padding:48px 0 110px;text-align:center}.hero-c{max-width:860px}
.badge{background:rgba(255,255,255,.18);color:#fff}.bul-row{grid-template-columns:repeat(auto-fit,minmax(210px,1fr));text-align:left;max-width:820px;margin:0 auto}.bul .ic{color:#c8f169}
.qz-sec{padding:0}.qz-tiles{background:#fff;border-radius:22px;box-shadow:0 30px 60px rgba(60,40,160,.16);padding:26px;max-width:860px;margin:-80px auto 0;position:relative}
.qz-bar i{background:linear-gradient(90deg,#5b3df5,#c8f169)}
.qz-tiles .qz-opts{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}.qz-tiles .qz-opt{flex-direction:column;align-items:flex-start;gap:4px;background:#f3f1ff;border-radius:16px;padding:18px;min-height:96px}
.qz-tiles .qz-opt:hover,.qz-tiles .qz-opt.on{border-color:#5b3df5;background:#fff}.qz-tiles .qz-t{font-size:16.5px}
.btn{background:#5b3df5;color:#fff}.tr-sec{padding:30px 0 0}.trust{text-align:center}.tr-i b{color:#5b3df5;font-size:24px}.hw-n{background:#5b3df5;color:#fff}
.faq details{background:#fff;border-radius:12px;border:0;padding:14px 18px;margin-bottom:8px}.ft{background:#1d1b3a;color:#cfcdf0}""",
"split": """body{font-family:Rubik,system-ui,sans-serif;background:#fff;color:#102a2e}.sp{display:grid;grid-template-columns:1fr 1fr;min-height:100vh}
.sp-l{background:#0e5e66;color:#eafaf8;padding:44px 40px}.sp-l-in{max-width:520px;margin-left:auto}.sp-l .hd-brand{margin-bottom:40px;letter-spacing:.5px}
.badge{background:#f6c445;color:#3b2c00}.bul .ic{color:#f6c445}.tr-i b{color:#f6c445}.sp-ph{display:inline-flex;gap:8px;margin-top:20px;color:#fff;font-weight:600;text-decoration:none}
.sp-r{padding:44px 40px;background:#f4f8f8}.sp-r-in{max-width:520px;position:sticky;top:24px}
.qz-split{background:#fff;border-radius:18px;padding:24px;box-shadow:0 10px 40px rgba(14,94,102,.12)}.qz-bar i{background:#0e5e66}
.qz-split .qz-opt{background:#fff;border:1.5px solid #cfe1e2;border-radius:12px;padding:14px 16px}.qz-split .qz-opt:hover,.qz-split .qz-opt.on{border-color:#0e5e66;background:#eaf6f6}
.qz-split .qz-mark{width:20px;height:20px;border-radius:50%;border:2px solid #0e5e66}.qz-split .qz-opt.on .qz-mark{background:#0e5e66;box-shadow:inset 0 0 0 3px #fff}
.btn{background:#f6c445;color:#3b2c00}.hw-n{background:#0e5e66;color:#fff}.ft{background:#0b3d42;color:#d6eeec}
@media(max-width:900px){.sp{grid-template-columns:1fr}.sp-l,.sp-r{padding:28px 16px}.sp-l-in,.sp-r-in{margin:0 auto}}""",
"noir": """body{font-family:Inter,system-ui,sans-serif;background:#0d0d10;color:#e9e6df;font-weight:300}.hd{border-bottom:1px solid #2a2a30}.hd-brand{font-family:'Playfair Display',serif;letter-spacing:1px}
.hd-ph{color:#c9a45c}.h-title,h2,.qz-q{font-family:'Playfair Display',serif;font-weight:600}.hero{padding:60px 0 30px}.nr-line{width:64px;height:2px;background:#c9a45c;margin-bottom:22px}
.badge{background:transparent;border:1px solid #c9a45c;color:#c9a45c;border-radius:2px;letter-spacing:1px;text-transform:uppercase;font-size:11.5px}
.h-title{max-width:820px}.h-sub{max-width:700px}.bul .ic{color:#c9a45c}.nr-grid{display:grid;grid-template-columns:280px 1fr;gap:40px}
.trust-col{grid-template-columns:1fr;gap:22px;border-left:1px solid #2a2a30;padding-left:20px}.tr-i b{font-family:'Playfair Display',serif;color:#c9a45c;font-size:26px}
.qz-list{border:1px solid #2a2a30;padding:30px}.qz-bar{background:#2a2a30;height:2px}.qz-bar i{background:#c9a45c}
.qz-list .qz-opt{background:transparent;color:#e9e6df;border:0;border-bottom:1px solid #2a2a30;border-radius:0;padding:16px 4px}
.qz-list .qz-mark{font-family:'Playfair Display',serif;color:#c9a45c;font-size:20px;width:28px}.qz-list .qz-opt:hover,.qz-list .qz-opt.on{background:#17171c}
.btn{background:#c9a45c;color:#111;border-radius:2px;letter-spacing:.5px}.lf input:not([type=checkbox]){background:#17171c!important;color:#eee!important;border-color:#3a3a40!important;border-radius:2px!important}
.hw-n{border:1px solid #c9a45c;color:#c9a45c;background:none}.faq details{border-color:#2a2a30}.ft{border-top:1px solid #2a2a30;color:#9a968c}
@media(max-width:860px){.nr-grid{grid-template-columns:1fr}.trust-col{grid-template-columns:repeat(2,1fr);border:0;padding:0}}""",
"editorial": """body{font-family:'PT Serif',Georgia,serif;background:#fbfaf7;color:#1a1a1a}.hd{border-bottom:3px double #1a1a1a;font-family:'PT Sans',sans-serif}
.hd-brand{font-family:'PT Serif',serif;font-size:22px}.hd-ph{color:#b3261e}.ed-col{max-width:720px}.ed{padding-top:30px}
.ed-kicker{font-family:'PT Sans',sans-serif;text-transform:uppercase;letter-spacing:1.5px;font-size:12.5px;color:#b3261e;font-weight:700;margin-bottom:10px}
.h-title{font-size:clamp(30px,5vw,46px)}.ed-lead{font-size:20px;line-height:1.55;color:#333}.ed-lead::first-letter{float:left;font-size:58px;line-height:.9;padding:6px 8px 0 0;color:#b3261e;font-weight:700}
.ed-meta{font-family:'PT Sans',sans-serif;font-size:13.5px;color:#666;border-top:1px solid #ddd;border-bottom:1px solid #ddd;padding:8px 0;margin:18px 0 22px}
.ed-quote{border-left:4px solid #b3261e;padding:4px 0 4px 18px;margin:24px 0;font-size:19px;font-style:italic}.ed-quote ul{margin:0;padding-left:18px}
.ed h2{font-size:25px;margin:30px 0 10px}.ed p{font-size:18px;line-height:1.7}.ed-form{background:#fff;border:1px solid #e1ddd3;padding:24px;margin:34px 0;font-family:'PT Sans',sans-serif}
.ed-form fieldset{border:0;padding:0;margin:0 0 18px}.ed-form legend{font-weight:700;font-size:17px;margin-bottom:8px}.ed-form .rd{display:flex;gap:8px;align-items:center;padding:6px 0;font-size:16px;cursor:pointer}
.btn{background:#1a1a1a;color:#fff;border-radius:0;font-family:'PT Sans',sans-serif}.ft{border-top:3px double #1a1a1a;font-family:'PT Sans',sans-serif}""",
"calc": """body{font-family:Nunito,system-ui,sans-serif;background:#f1f7f2;color:#173022}.hd{background:#fff}.hd-ph{color:#1f9d55}
.badge{background:#ffe9a8;color:#6a4b00}.cc-grid{display:grid;grid-template-columns:1fr 1fr;gap:36px;align-items:start}.bul .ic{color:#1f9d55}.tr-i b{color:#1f9d55}
.cc{background:#fff;border-radius:24px;padding:26px;box-shadow:0 20px 50px rgba(31,157,85,.15)}.cc-sl label{font-weight:700}.cc-val{font-size:38px;font-weight:800;color:#1f9d55;margin:6px 0}
.cc input[type=range]{width:100%;accent-color:#1f9d55;height:28px}.cc-mm{display:flex;justify-content:space-between;font-size:12.5px;opacity:.6}
.cc-steps fieldset{border:0;padding:0;margin:16px 0 0}.cc-steps legend{font-weight:700;margin-bottom:8px}.cc-pills{display:flex;flex-wrap:wrap;gap:8px}
.cc-pills button{border:1.5px solid #cfe5d6;background:#f6fbf7;border-radius:99px;padding:8px 14px;font-weight:600}.cc-pills button.on{background:#1f9d55;color:#fff;border-color:#1f9d55}
.cc-go{width:100%;margin-top:18px}.btn{background:#1f9d55;color:#fff;border-radius:14px}
.cc-res{margin-top:18px;border-top:1px dashed #cfe5d6;padding-top:16px}.cc-res .cc-big{font-size:22px;font-weight:800}.cc-meter{height:10px;border-radius:10px;background:#e6f2ea;overflow:hidden;margin:10px 0}.cc-meter i{display:block;height:100%;background:linear-gradient(90deg,#ffd166,#1f9d55)}
.hw-n{background:#1f9d55;color:#fff}.faq details{background:#fff;border-radius:14px;padding:14px 18px;margin-bottom:8px;border:0}.ft{background:#173022;color:#cfe7d6}
@media(max-width:860px){.cc-grid{grid-template-columns:1fr}}""",
"flow": """body{font-family:'Golos Text',system-ui,sans-serif;background:#f4efe6;color:#2b2118}.hd-ph{color:#e8590c}.hero{padding:70px 0 40px}
.fl-hero{max-width:820px}.badge{background:#2b2118;color:#f4efe6;border-radius:4px}.h-title{font-size:clamp(34px,6vw,62px);letter-spacing:-.5px}
.fl-start{background:#e8590c;color:#fff;border-radius:6px;font-size:19px;padding:18px 28px}.fl-hint{font-size:13.5px;opacity:.65;margin-top:10px}
.tr-i b{color:#e8590c}.hw-n{background:#2b2118;color:#f4efe6;border-radius:4px}.btn{background:#e8590c;color:#fff;border-radius:6px}
.fl-ov{position:fixed;inset:0;background:#f4efe6;z-index:50;overflow-y:auto;display:flex;align-items:center;justify-content:center;padding:20px}
.fl-ov[hidden]{display:none}.fl-x{position:fixed;top:12px;right:16px;border:0;background:none;font-size:34px;color:#2b2118}
.qz-flow{width:100%;max-width:680px}.qz-flow .qz-q{font-size:clamp(24px,4vw,36px)}.qz-flow .qz-bar{position:fixed;top:0;left:0;right:0;margin:0;border-radius:0;height:5px}.qz-flow .qz-bar i{background:#e8590c}
.qz-flow .qz-opt{background:rgba(232,89,12,.06);border:1.5px solid rgba(232,89,12,.35);border-radius:6px;padding:12px 14px;font-size:18px;color:#2b2118}
.qz-flow .qz-mark{width:28px;height:28px;border:1.5px solid #e8590c;border-radius:4px;display:grid;place-items:center;font-size:13px;font-weight:700;color:#e8590c}
.qz-flow .qz-opt:hover,.qz-flow .qz-opt.on{background:#e8590c;color:#fff}.qz-flow .qz-opt.on .qz-mark,.qz-flow .qz-opt:hover .qz-mark{background:#fff}
.faq details{border-color:rgba(43,33,24,.2)}.ft{background:#2b2118;color:#e9dfd0}""",
"yesno": """body{font-family:'IBM Plex Sans',system-ui,sans-serif;background:#eef4fb;color:#0f2238}.hd{background:#fff}.hd-ph{color:#1667d9}
.yn-hero{text-align:center;max-width:820px}.badge{background:#dbe9fb;color:#1667d9}.qz-yn{max-width:720px;margin:0 auto;background:#fff;border-radius:20px;padding:28px;box-shadow:0 16px 40px rgba(22,103,217,.12)}
.qz-yn .qz-q{font-size:clamp(20px,3vw,27px);text-align:center}.qz-yn .qz-hint{text-align:center}.qz-yn .qz-count{margin:0 auto}
.qz-yn .qz-opts{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}.qz-yn .qz-opt{justify-content:center;border-radius:14px;padding:18px;font-size:18px;background:#f2f7fd;border:2px solid #dbe9fb}
.qz-yn .qz-opt:first-child:hover,.qz-yn .qz-opt:first-child.on{background:#e3f7ec;border-color:#21a35a}.qz-yn .qz-opt:nth-child(2):hover,.qz-yn .qz-opt:nth-child(2).on{background:#fdecec;border-color:#d64545}
.qz-yn .qz-opt:nth-child(3):hover{border-color:#8aa}.qz-bar i{background:#1667d9}.yn-b{display:grid;grid-template-columns:1fr 1fr;gap:24px;padding-top:10px}
.bul .ic{color:#21a35a}.tr-i b{color:#1667d9}.btn{background:#1667d9;color:#fff}.hw-n{background:#1667d9;color:#fff}.ft{background:#0f2238;color:#c9d8ea}
@media(max-width:760px){.yn-b{grid-template-columns:1fr}}""",
"official": """body{font-family:'PT Sans',Arial,sans-serif;background:#f5f7fa;color:#0b1f33}.hd{background:#fff;border-bottom:1px solid #e1e6ed}.hd-ph{color:#0d4cd3}
.of-strip{background:#0d4cd3;color:#fff;font-size:14px;padding:8px 0}.of-grid{display:grid;grid-template-columns:1fr 440px;gap:32px;align-items:start}
.badge{display:none}.h-title{font-size:clamp(26px,4vw,40px)}.bul .ic{color:#0d4cd3}
.of-card{background:#fff;border:1px solid #e1e6ed;border-radius:8px;padding:24px}.of-card h2{font-size:22px;margin-bottom:6px}.of-note{font-size:14px;color:#66727f;margin:0 0 14px}
.of-card .sel{display:grid;gap:4px;margin-bottom:12px;font-size:14px;font-weight:700}.of-card .sel small{font-weight:400;color:#66727f}
.of-card select{width:100%;padding:12px;border:1px solid #b7c2ce;border-radius:4px;background:#fff;font-size:15.5px}
.lf input:not([type=checkbox]){border-radius:4px!important;border-color:#b7c2ce!important}.btn{background:#0d4cd3;color:#fff;border-radius:4px;width:100%}
.tr-i{background:#fff;border:1px solid #e1e6ed;border-radius:8px;padding:14px}.tr-i b{color:#0d4cd3}.hw-n{background:#e8effc;color:#0d4cd3;border-radius:4px}
.faq details{background:#fff;border:1px solid #e1e6ed;border-radius:6px;padding:12px 16px;margin-bottom:6px}.ft{background:#fff;border-top:1px solid #e1e6ed}
@media(max-width:900px){.of-grid{grid-template-columns:1fr}}""",
"bold": """body{font-family:Manrope,system-ui,sans-serif;background:#fff;color:#14051f}.hd-bold{background:#14051f;color:#fff}.hd-ph{color:#ffd23f}
.hero{background:linear-gradient(125deg,#ff2d75,#ff7a2d 60%,#ffd23f);color:#fff;padding:50px 0}.bd-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:30px;align-items:center}
.h-title{font-family:Unbounded,sans-serif;font-weight:800;font-size:clamp(28px,5vw,52px);text-transform:uppercase;letter-spacing:-.5px}
.badge{background:#14051f;color:#ffd23f;font-family:Unbounded,sans-serif;font-size:12px}.bul .ic{color:#14051f}.bul li{font-weight:700}
.qz-pills{background:#fff;color:#14051f;border-radius:28px;padding:24px;box-shadow:0 30px 60px rgba(20,5,31,.3)}.qz-bar i{background:linear-gradient(90deg,#ff2d75,#ff7a2d)}
.qz-pills .qz-opts{display:flex;flex-wrap:wrap}.qz-pills .qz-opt{width:auto;border-radius:99px;background:#fff1f6;padding:12px 18px;border:2px solid #ffd0e1}
.qz-pills .qz-opt:hover,.qz-pills .qz-opt.on{background:#ff2d75;color:#fff;border-color:#ff2d75}.qz-pills .qz-d{display:none}
.btn{background:#14051f;color:#ffd23f;border-radius:99px;font-family:Unbounded,sans-serif;font-size:15px}.tr-i b{font-family:Unbounded,sans-serif;color:#ff2d75}
.hw-n{background:#ff2d75;color:#fff}h2{font-family:Unbounded,sans-serif;text-transform:uppercase;font-size:clamp(20px,3vw,28px)}.ft{background:#14051f;color:#e8d9f0}
.bd-sticky{position:fixed;left:12px;right:12px;bottom:12px;z-index:40;display:none;justify-content:center;gap:8px;padding:15px;border-radius:99px;background:#14051f;color:#ffd23f;font-weight:800;text-decoration:none;box-shadow:0 10px 30px rgba(0,0,0,.3)}
@media(max-width:860px){.bd-grid{grid-template-columns:1fr}.bd-sticky{display:flex}}""",
}

# ───────────────────────── JS ─────────────────────────
JS = r"""(function(){
var D=JSON.parse(document.getElementById('qd').textContent),A=[],X={};
var SV=D.services||[],BASE=(D.steps||[]).slice(),SVQ=SV.length?{q:D.service_q||'С каким вопросом обращаетесь?',hint:D.service_hint||'',options:SV.map(function(s){return{t:s.t,d:s.d||''}}),svc:1}:null;
function setSvc(k){if(!SVQ)return;D.svc=SV[k]||null;var a=[SVQ].concat(D.svc?(D.svc.steps||[]):[],BASE);D.steps.length=0;a.forEach(function(x){D.steps.push(x)})}
if(SVQ){setSvc(D.preset);if(D.svc)A[0]={q:SVQ.q,a:D.svc.t}}
function $(s,r){return (r||document).querySelector(s)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function goal(g){try{window.__ymAll&&window.__ymAll('reachGoal',g)}catch(e){}}
function utm(){var o={};try{new URLSearchParams(location.search).forEach(function(v,k){if(/^utm_|^yclid$/.test(k))o[k]=v})}catch(e){}return o}
function digits(s){return String(s||'').replace(/\D/g,'')}
function mask(inp){inp.addEventListener('input',function(){var d=digits(inp.value);if(d[0]==='8')d='7'+d.slice(1);if(d&&d[0]!=='7')d='7'+d;d=d.slice(0,11);
 var r='+7';if(d.length>1)r+=' ('+d.slice(1,4);if(d.length>=4)r+=') '+d.slice(4,7);if(d.length>=7)r+='-'+d.slice(7,9);if(d.length>=9)r+='-'+d.slice(9,11);inp.value=d.length?r:''})}
var started=false;function start(){if(!started){started=true;goal('quiz_start')}}
function cid(cb){var done=false;function f(v){if(!done){done=true;cb(v||'')}}setTimeout(f,700);try{if(window.__ymAll&&(window.__YM_IDS||[]).length)window.__ymAll('getClientID',f);else f('')}catch(e){f('')}}
function send(c,ok,fail){cid(function(id){fetch('/lead',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:c.name||'',phone:c.phone,city:c.city||'',
 answers:A.filter(Boolean).concat(Object.keys(X).map(function(k){return X[k]})),quiz_title:(D.quiz_title||'')+(D.svc?' → '+D.svc.t:''),niche:D.niche||'',service:D.svc?D.svc.t:'',request:(function(){var q=D.svc&&D.svc.steps&&D.svc.steps[0]?D.svc.steps[0].q:'';var r=q?A.filter(function(x){return x&&x.q===q})[0]:null;return r?String(r.a):''})(),method:X.ms?X.ms.a:'',
 utm:utm(),page:location.href,ref:document.referrer,yandex_client_id:id})})
 .then(function(r){if(!r.ok)throw 0;goal('lead_submit');ok()}).catch(fail)})}
var CITIES='<datalist id="dl-cities">'+D.cities.map(function(c){return '<option value="'+esc(c)+'">'}).join('')+'</datalist>';
function days(){var o='',d=new Date(),n=0;while(n<6){d.setDate(d.getDate()+1);var w=d.getDay();if(w===0||w===6)continue;n++;o+='<option>'+d.toLocaleDateString('ru-RU',{weekday:'short',day:'numeric',month:'long'})+'</option>'}return o}
function extraHtml(){return (D.form_booking?'<div class="lf-row"><label>День консультации<select name="bday">'+days()+'</select></label><label>Время<select name="btime">'+
  ['10:00','11:00','12:00','13:00','14:00','15:00','16:00','17:00','18:00','19:00'].map(function(t){return '<option>'+t+'</option>'}).join('')+'</select></label></div>':'')+
 (D.form_messenger?'<div class="lf-ms"><span>'+esc(D.form_messenger_label||'Куда ответить')+'</span><div>'+['Звонок','WhatsApp','Telegram','MAX'].map(function(m,k){return '<label><input type="radio" name="ms" value="'+m+'"'+(k?'':' checked')+'><span>'+m+'</span></label>'}).join('')+'</div></div>':'')}
function formHtml(){return '<form class="lf" novalidate>'+
 '<label>Имя<input name="name" autocomplete="name" placeholder="Как к вам обращаться"></label>'+
 '<label>Телефон<input name="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+7 (___) ___-__-__"></label>'+
 '<label>Город<input name="city" list="dl-cities" autocomplete="address-level2" placeholder="Например, Екатеринбург"></label>'+CITIES+extraHtml()+
 '<label class="lf-ok"><input type="checkbox" name="ok" checked><span>Согласен(на) на обработку персональных данных, <a href="/privacy" target="_blank">политика</a></span></label>'+
 '<button type="submit" class="btn">'+esc(D.form_cta)+'</button><p class="lf-msg"></p>'+(D.form_note?'<p class="lf-note">'+esc(D.form_note)+'</p>':'')+'</form>'}
function thanks(){return '<div class="thx"><b>Заявка принята</b><p>'+esc(D.thanks)+'</p>'+(D.phone?'<p>Если удобнее — позвоните: <a href="tel:'+esc(D.phone_href)+'">'+esc(D.phone)+'</a></p>':'')+'</div>'}
function wireForm(f,done){var ph=f.querySelector('[name=phone]'),m=f.querySelector('.lf-msg'),b=f.querySelector('button[type=submit]');mask(ph);
 f.addEventListener('submit',function(e){e.preventDefault();m.textContent='';f.classList.remove('err');
  if(digits(ph.value).length!==11){m.textContent='Проверьте номер телефона';ph.parentNode.classList.add('err');ph.focus();return}
  if(!f.querySelector('[name=ok]').checked){m.textContent='Нужно согласие на обработку данных';return}
  if(f.dataset.need&&!f.dataset.ready){m.textContent='Ответьте, пожалуйста, на вопросы анкеты';return}
  var bd=f.querySelector('[name=bday]'),ms=f.querySelector('[name=ms]:checked');
  if(bd)X.booking={q:'Удобное время консультации',a:bd.value+', '+f.querySelector('[name=btime]').value};if(ms)X.ms={q:'Удобный способ связи',a:ms.value};
  b.disabled=true;b.textContent='Отправляем…';start();
  send({name:f.querySelector('[name=name]').value.trim(),phone:ph.value,city:f.querySelector('[name=city]').value.trim()},function(){done()},
   function(){b.disabled=false;b.textContent=D.form_cta;m.textContent='Не отправилось — попробуйте ещё раз или позвоните нам'})})}
function optsHtml(s,letters){return s.options.map(function(o,k){return '<button type="button" class="qz-opt" data-k="'+k+'"><span class="qz-mark">'+(letters?String.fromCharCode(65+k):'')+'</span><span><span class="qz-t">'+esc(o.t)+'</span>'+(o.d?'<span class="qz-d">'+esc(o.d)+'</span>':'')+'</span></button>'}).join('')}
/* пошаговый квиз (плитки, список, да/нет, пилюли, на весь экран) */
function runSteps(root){var i=D.svc?1:0,S=D.steps,body=$('.qz-body',root),bar=$('.qz-bar i',root),cnt=$('.qz-count',root),back=$('.qz-back',root),letters=root.hasAttribute('data-letters');
 root._keys=function(e){var k=e.key.toUpperCase(),n='ABCDEF'.indexOf(k);if(n<0)n='123456'.indexOf(k);var b=body.querySelectorAll('.qz-opt')[n];if(b&&!/INPUT|TEXTAREA/.test((document.activeElement||{}).tagName||''))b.click()};
 function render(){if(i>=S.length)return fin();var s=S[i];cnt.textContent='Вопрос '+(i+1)+' из '+S.length;bar.style.width=(100*i/(S.length+1))+'%';back.hidden=i===0;
  body.innerHTML='<h3 class="qz-q">'+esc(s.q)+'</h3>'+(s.hint?'<p class="qz-hint">'+esc(s.hint)+'</p>':'')+'<div class="qz-opts">'+optsHtml(s,letters)+'</div>'+(s.multi?'<button type="button" class="btn qz-next">Далее</button>':'');
  var sel=[];body.querySelectorAll('.qz-opt').forEach(function(b){b.onclick=function(){start();var o=s.options[+b.dataset.k];
   if(s.multi){b.classList.toggle('on');var x=sel.indexOf(o.t);x<0?sel.push(o.t):sel.splice(x,1);return}
   if(s.svc){setSvc(+b.dataset.k);A.length=0}b.classList.add('on');A[i]={q:s.q,a:o.t};setTimeout(function(){i++;goal('quiz_step_'+i);render()},230)}});
  var nx=$('.qz-next',body);if(nx)nx.onclick=function(){if(!sel.length)return;A[i]={q:s.q,a:sel.join('; ')};i++;goal('quiz_step_'+i);render()}}
 function fin(){bar.style.width='92%';cnt.textContent='Последний шаг';back.hidden=!S.length;
  body.innerHTML='<h3 class="qz-q">'+esc(D.result_title)+'</h3><p class="qz-hint">'+esc(D.result_text)+'</p>'+formHtml();
  wireForm($('form',body),function(){body.innerHTML=thanks();back.hidden=true;cnt.textContent='';bar.style.width='100%'})}
 back.onclick=function(){if(i>0){i--;A.length=i;render()}};
 root._jump=function(k){if(!SVQ)return;setSvc(k);A.length=0;A[0]={q:SVQ.q,a:D.svc.t};i=1;render()};render()}
/* чат с юристом */
function runChat(root){var log=$('.ch-log',root),foot=$('.ch-foot',root),i=D.svc?1:0,C={};
 function sc(){log.scrollTop=log.scrollHeight}
 function bot(h,cb){var t=document.createElement('div');t.className='ch-msg bot typing';t.innerHTML='<i></i><i></i><i></i>';log.appendChild(t);sc();
  setTimeout(function(){t.className='ch-msg bot';t.innerHTML=h;sc();cb&&cb()},Math.min(1400,500+h.length*6))}
 function me(x){var t=document.createElement('div');t.className='ch-msg me';t.textContent=x;log.appendChild(t);sc()}
 function ask(){if(i>=D.steps.length)return askName();var s=D.steps[i];
  bot(esc(s.q)+(s.hint?'<small>'+esc(s.hint)+'</small>':''),function(){
   foot.innerHTML='<div class="ch-chips">'+s.options.map(function(o,k){return '<button type="button" data-k="'+k+'">'+esc(o.t)+'</button>'}).join('')+'</div>';
   foot.querySelectorAll('button').forEach(function(b){b.onclick=function(){start();var o=s.options[+b.dataset.k];if(s.svc){setSvc(+b.dataset.k);A.length=0}A[i]={q:s.q,a:o.t};foot.innerHTML='';me(o.t);i++;goal('quiz_step_'+i);ask()}})})}
 function input(ph,tel,cb){foot.innerHTML='<form class="ch-in"><input '+(tel?'type="tel" inputmode="tel" autocomplete="tel"':'autocomplete="'+(ph==='Город'?'address-level2" list="dl-cities':'name')+'"')+' placeholder="'+esc(ph)+'"><button type="submit" aria-label="Отправить">➤</button></form>'
   +(ph==='Город'?CITIES:'')+(tel?'<label class="ch-ok"><input type="checkbox" checked><span>согласен(на) на обработку персональных данных, <a href="/privacy" target="_blank">политика</a></span></label>':'');
  var f=$('form',foot),inp=$('input',f);if(tel)mask(inp);try{inp.focus({preventScroll:true})}catch(e){}
  f.onsubmit=function(e){e.preventDefault();cb(inp.value.trim(),f)}}
 function askName(){bot(esc(D.result_text||'')+'<br><br>Как к вам обращаться?',function(){input('Ваше имя',false,function(v){if(!v)return;C.name=v;me(v);askCity()})})}
 function askCity(){bot('Из какого вы города?',function(){input('Город',false,function(v){C.city=v;me(v||'—');askPhone()})})}
 function askPhone(){bot(esc(C.name)+', оставьте номер — юрист перезвонит и ответит по вашей ситуации.',function(){input('+7 (___) ___-__-__',true,function(v,f){
  var ok=$('.ch-ok input',foot);if(digits(v).length!==11){f.classList.add('err');return}if(ok&&!ok.checked)return;
  me(v);foot.innerHTML='<div class="ch-wait">Отправляем…</div>';C.phone=v;
  send(C,function(){foot.innerHTML='';bot(esc(D.thanks))},function(){foot.innerHTML='';bot('Не получилось отправить.'+(D.phone?' Позвоните нам: <a href="tel:'+esc(D.phone_href)+'">'+esc(D.phone)+'</a>':''));askPhone()})})})}
 bot(esc(D.chat_greeting||D.title),function(){setTimeout(ask,350)})}
/* все вопросы сразу: радиокнопки (статья) или выпадающие списки (анкета) */
function runAll(root,kind){root.innerHTML='<div class="all-q"></div>'+formHtml();var box=$('.all-q',root),f=$('form',root);f.dataset.need='1';
 function draw(){var h='';D.steps.forEach(function(s,i){
  if(kind==='select'){h+='<label class="sel">'+esc(s.q)+(s.hint?' <small>'+esc(s.hint)+'</small>':'')+'<select data-i="'+i+'"><option value="">— выберите —</option>'+s.options.map(function(o,k){return '<option value="'+k+'">'+esc(o.t)+'</option>'}).join('')+'</select></label>'}
  else{h+='<fieldset data-i="'+i+'"><legend>'+(i+1)+'. '+esc(s.q)+'</legend>'+s.options.map(function(o,k){return '<label class="rd"><input type="radio" name="q'+i+'" value="'+k+'"> '+esc(o.t)+'</label>'}).join('')+'</fieldset>'}});
  box.innerHTML=h;if(D.svc){var k=SV.indexOf(D.svc),el=kind==='select'?box.querySelector('select[data-i="0"]'):box.querySelector('input[name="q0"][value="'+k+'"]');if(el){if(kind==='select')el.value=k;else el.checked=true}}}
 root._pick=function(k){setSvc(k);draw();upd()};
 function upd(e){var t=e&&e.target;if(SVQ&&t&&(t.getAttribute('data-i')==='0'||t.name==='q0')){setSvc(t.value===''?-1:+t.value);draw()}
  var n=0;A.length=D.steps.length;D.steps.forEach(function(s,i){var v=kind==='select'?(root.querySelector('select[data-i="'+i+'"]')||{}).value:((root.querySelector('input[name="q'+i+'"]:checked')||{}).value);
  if(v!==undefined&&v!==''){A[i]={q:s.q,a:s.options[+v].t};n++}else A[i]=null});f.dataset.ready=n===D.steps.length?'1':'';if(n)start()}
 draw();if(D.svc)upd();box.addEventListener('change',upd);wireForm(f,function(){root.innerHTML=thanks()})}
/* калькулятор */
function runCalc(root){var sl=D.slider||{},r=$('input[type=range]',root),out=$('output',root),mm=root.querySelectorAll('.cc-mm span'),st=$('.cc-steps',root),res=$('.cc-res',root);
 var min=+sl.min||0,max=+sl.max||100,step=+sl.step||1,def=+sl.default||Math.round((min+max)/2/step)*step;r.min=min;r.max=max;r.step=step;r.value=def;
 function fmt(v){return Number(v).toLocaleString('ru-RU')+(sl.unit?' '+sl.unit:'')}mm[0].textContent=fmt(min);mm[1].textContent=fmt(max);
 function show(){out.textContent=fmt(r.value)}r.addEventListener('input',function(){start();show()});show();
 var S=D.steps.slice(0,3);st.innerHTML=S.map(function(s,i){return '<fieldset><legend>'+esc(s.q)+'</legend><div class="cc-pills">'+s.options.map(function(o,k){return '<button type="button" data-i="'+i+'" data-k="'+k+'">'+esc(o.t)+'</button>'}).join('')+'</div></fieldset>'}).join('');
 st.querySelectorAll('button').forEach(function(b){b.onclick=function(){start();var i=+b.dataset.i;st.querySelectorAll('button[data-i="'+i+'"]').forEach(function(x){x.classList.remove('on')});b.classList.add('on');if(S[i].svc)D.svc=SV[+b.dataset.k]||null;A[i]={q:S[i].q,a:S[i].options[+b.dataset.k].t}}});
 $('.cc-go',root).onclick=function(){start();X.calc={q:sl.label||'Значение',a:fmt(r.value)};var v=+r.value,p=Math.round(100*(v-min)/Math.max(1,max-min)),note='';
  if(D.direction==='bfl'){note=v<=1000000?'Сумма в пределах внесудебной процедуры через МФЦ (до 1 млн ₽ — если исполнительное производство окончено). Подходит ли она вам — проверит юрист.':'Для такой суммы обычно подходит судебная процедура банкротства по ФЗ-127.'}
  res.hidden=false;res.innerHTML='<div class="cc-big">'+esc(sl.label||'Значение')+': '+esc(fmt(v))+'</div><div class="cc-meter"><i style="width:'+Math.max(6,p)+'%"></i></div>'+(note?'<p>'+esc(note)+'</p>':'')+
   '<p><b>'+esc(D.result_title)+'</b><br>'+esc(D.result_text)+'</p>'+formHtml();goal('quiz_step_calc');
  wireForm($('form',res),function(){res.innerHTML=thanks()});res.scrollIntoView({behavior:'smooth',block:'start'})}}
document.querySelectorAll('[data-qz]').forEach(function(q){if(!q.closest('.fl-ov'))runSteps(q)});
var ch=$('[data-chat]');if(ch)runChat(ch);
document.querySelectorAll('[data-all]').forEach(function(x){runAll(x,x.getAttribute('data-all'))});
var cc=$('[data-calc]');if(cc)runCalc(cc);
var ov=$('.fl-ov');if(ov){var q=$('[data-qz]',ov),inited=false;
 document.querySelectorAll('.fl-start').forEach(function(b){b.onclick=function(){ov.hidden=false;document.body.style.overflow='hidden';if(!inited){inited=true;runSteps(q)}start()}});
 $('.fl-x',ov).onclick=function(){ov.hidden=true;document.body.style.overflow=''};
 document.addEventListener('keydown',function(e){if(ov.hidden)return;if(e.key==='Escape'){$('.fl-x',ov).click();return}q._keys&&q._keys(e)})}
/* выбор подуслуги из списка на странице (без перезагрузки) */
function quizEl(){var q=[].slice.call(document.querySelectorAll('[data-qz],[data-all],[data-chat],[data-calc]')).filter(function(x){return !x.closest('.fl-ov')});return q[0]}
function toQuiz(){var q=quizEl();if(q)q.scrollIntoView({behavior:'smooth',block:'start'})}
document.querySelectorAll('[data-svc]').forEach(function(a){a.addEventListener('click',function(e){var k=+a.getAttribute('data-svc'),q=quizEl();
 if(!q||!(q._jump||q._pick))return;e.preventDefault();start();(q._jump||q._pick)(k);
 document.querySelectorAll('[data-svc]').forEach(function(x){x.classList.toggle('on',x===a)});
 var t=D.svc_titles&&D.svc_titles[k];if(t){var h=$('.h-title');if(h&&t[0])h.textContent=t[0];var p=$('.h-sub');if(p&&t[1])p.textContent=t[1]}
 goal('service_'+(SV[k]||{}).id);toQuiz()})});
/* виджет до квиза: срок по дате, чек-лист, чипсы, два ползунка, стадии */
function money(v,u){return Number(v).toLocaleString('ru-RU')+(u?' '+u:'')}
function runWidget(el){var w=D.widget||{},t=w.type,h='';
 if(t==='deadline')h='<label class="w-l">'+esc(w.label)+'<input type="date" class="w-date"></label><button type="button" class="btn w-go">'+esc(w.cta||'Проверить')+'</button>';
 if(t==='checklist')h='<div class="w-list">'+w.items.map(function(x,k){return '<label class="w-ck"><input type="checkbox" value="'+k+'"><span>'+esc(x)+'</span></label>'}).join('')+'</div><button type="button" class="btn w-go">'+esc(w.cta||'Проверить')+'</button>';
 if(t==='chips')h='<div class="w-chips">'+w.items.map(function(x,k){return '<button type="button" data-k="'+k+'">'+esc(x)+'</button>'}).join('')+'</div><button type="button" class="btn w-go">'+esc(w.cta||'Далее')+'</button>';
 if(t==='sliders')h=[w.a,w.b].map(function(s,k){return '<label class="w-sl">'+esc(s.label)+'<output></output><input type="range" data-s="'+k+'" min="'+s.min+'" max="'+s.max+'" step="'+s.step+'" value="'+s.default+'"></label>'}).join('')+'<button type="button" class="btn w-go">'+esc(w.cta||'Рассчитать')+'</button>';
 if(t==='stages')h='<ol class="w-st">'+w.items.map(function(x,k){return '<li><button type="button" data-k="'+k+'"><b>'+esc(x.t)+'</b>'+(x.d?'<span>'+esc(x.d)+'</span>':'')+'</button></li>'}).join('')+'</ol>';
 el.innerHTML=(w.title?'<h3 class="w-t">'+esc(w.title)+'</h3>':'')+(w.text?'<p class="w-p">'+esc(w.text)+'</p>':'')+h+'<div class="w-res" hidden></div>';
 var res=$('.w-res',el),go=$('.w-go',el);
 function out(html){res.innerHTML=html+'<button type="button" class="btn w-next">'+esc(w.next||'Получить консультацию')+'</button>';res.hidden=false;start();goal('widget');
  $('.w-next',res).onclick=toQuiz}
 if(t==='deadline'){var di=$('.w-date',el);di.max=new Date().toISOString().slice(0,10);go.onclick=function(){if(!di.value){di.focus();return}
  var d0=new Date(di.value+'T00:00:00'),lim=new Date(d0);if(w.months)lim.setMonth(lim.getMonth()+w.months);if(w.days)lim.setDate(lim.getDate()+w.days);
  var now=new Date();now.setHours(0,0,0,0);var left=Math.round((lim-now)/864e5),ds=lim.toLocaleDateString('ru-RU'),txt=(left>=0?w.ok:w.late).replace('{n}',Math.abs(left)).replace('{date}',ds);
  X.w={q:w.label,a:d0.toLocaleDateString('ru-RU')+' — '+(left>=0?'осталось дней: '+left:'срок пропущен на '+(-left)+' дн.')};out('<p class="w-big'+(left>=0?'':' late')+'">'+esc(txt)+'</p>')}}
 if(t==='checklist')go.onclick=function(){var on=[].slice.call(el.querySelectorAll('.w-ck input:checked')),n=on.length;
  X.w={q:w.title,a:n+' из '+w.items.length+(n?': '+on.map(function(x){return w.items[+x.value]}).join('; '):'')};
  out('<p class="w-big">'+esc(w.score.replace('{n}',n).replace('{m}',w.items.length))+'</p><p>'+esc(n>=w.good?w.hi:w.lo)+'</p>')};
 if(t==='chips'){var sel=[];el.querySelectorAll('.w-chips button').forEach(function(b){b.onclick=function(){start();b.classList.toggle('on');var x=w.items[+b.dataset.k],i=sel.indexOf(x);i<0?sel.push(x):sel.splice(i,1);
   X.w={q:w.title,a:sel.join(', ')};if(!sel.length)delete X.w}});
  go.onclick=function(){if(!sel.length){toQuiz();return}out('<p>'+esc((w.res||'').replace('{n}',sel.length))+'</p>')}}
 if(t==='sliders'){var R=el.querySelectorAll('input[type=range]'),O=el.querySelectorAll('.w-sl output'),S2=[w.a,w.b];
  function sh(){R.forEach(function(r,k){O[k].textContent=money(r.value,S2[k].unit)})}R.forEach(function(r){r.addEventListener('input',function(){start();sh()})});sh();
  go.onclick=function(){var a=+R[0].value,b=+R[1].value,v=w.op==='mul'?a*b:Math.max(0,b-a);
   X.w={q:w.title,a:S2[0].label+': '+money(a,S2[0].unit)+'; '+S2[1].label+': '+money(b,S2[1].unit)};
   out('<p class="w-big">'+esc(w.res_label)+': '+esc(money(v,w.unit||'₽'))+'</p><p>'+esc(w.note||'')+'</p>')}}
 if(t==='stages')el.querySelectorAll('.w-st button').forEach(function(b){b.onclick=function(){var x=w.items[+b.dataset.k];
  el.querySelectorAll('.w-st button').forEach(function(y){y.classList.toggle('on',y===b)});X.w={q:w.title,a:x.t};out('<p>'+esc(x.r||w.res||'')+'</p>')}})}
var wd=$('[data-w]');if(wd&&D.widget&&D.widget.type)runWidget(wd);
var nq=$('[data-qz][data-letters]');if(nq&&!nq.closest('.fl-ov'))document.addEventListener('keydown',function(e){nq._keys&&nq._keys(e)});
})();"""


def niche_of(c: dict[str, Any]) -> str:
    """Направление для CRM: config.niche, иначе из quiz_title «Юр. услуги: Семейные споры» → «Семейные споры»."""
    n = str(c.get("niche") or "").strip()
    if not n and c.get("services"):
        q = str(c.get("quiz_title") or "").strip()
        n = q.split(":", 1)[1].strip() if ":" in q else q
    return n[:120]


def render(cfg: dict[str, Any], domain: str = "", metrika_ids: list[int] | None = None, service: str | None = None) -> str:
    c = _ctx(cfg)
    design = c.get("design") if c.get("design") in DESIGNS else "tiles"
    titles = [[s.get("title") or "", s.get("subtitle") or ""] for s in c["services"]]
    preset = apply_service(c, service)
    body = LAYOUT[design](c)
    data = {"steps": c["steps"], "slider": c.get("slider") if isinstance(c.get("slider"), dict) else None,
            "direction": c.get("direction") or "", "quiz_title": c.get("quiz_title") or c.get("name") or c.get("title") or "",
            "niche": niche_of(c),
            "result_title": c.get("result_title") or "Почти готово", "result_text": c.get("result_text") or "",
            "form_cta": c.get("form_cta") or c.get("cta") or "Отправить", "form_note": c.get("form_note") or "",
            "thanks": c.get("thanks") or "Спасибо! Юрист свяжется с вами в ближайшее время.",
            "chat_greeting": c.get("chat_greeting") or "", "title": c.get("title") or "",
            "phone": c.get("phone") or "", "phone_href": c.get("phone_href") or "", "cities": CITIES,
            "services": [{"id": s["id"], "t": s["t"], "d": s.get("d") or "", "steps": s["steps"]} for s in c["services"]],
            "svc_titles": titles, "preset": preset, "service_q": c.get("service_q") or "", "service_hint": c.get("service_hint") or "",
            "widget": c.get("widget") if isinstance(c.get("widget"), dict) else None,
            "form_booking": bool(c.get("form_booking")), "form_messenger": bool(c.get("form_messenger")),
            "form_messenger_label": c.get("form_messenger_label") or ""}
    js_data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = _e(c.get("title") or c.get("brand") or domain)
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><meta name="description" content="{_e(c.get("subtitle"))}">'
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="https://fonts.googleapis.com/css2?family={FONTS[design]}&display=swap" rel="stylesheet">'
            f'<style>{BASE_CSS}\n{CSS[design]}</style>{site_bundles.metrika_head(metrika_ids or [])}</head>'
            f'<body class="d-{design}">{body}<script id="qd" type="application/json">{js_data}</script><script>{JS}</script></body></html>')


def privacy(cfg: dict[str, Any], domain: str = "") -> str:
    c = _ctx(cfg)
    who = ", ".join(x for x in (c.get("legal_name") or c.get("brand"), f"ИНН {c['inn']}" if c.get("inn") else "",
                                f"ОГРН {c['ogrn']}" if c.get("ogrn") else "", c.get("address")) if x) or domain
    mail = _e(c.get("email") or "")
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Политика конфиденциальности</title><style>body{{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:0 auto;padding:24px 16px;color:#222}}h1{{font-size:26px}}h2{{font-size:19px;margin-top:24px}}</style></head><body>'
            f'<p><a href="/">← на главную</a></p><h1>Политика обработки персональных данных</h1>'
            f'<p>Оператор персональных данных: {_e(who)}. Сайт: {_e(domain)}.</p>'
            '<h2>Какие данные мы получаем</h2><p>Имя, номер телефона, город и ответы на вопросы анкеты, которые вы указываете в форме; '
            'технические данные: адрес страницы и рекламные метки, данные Яндекс Метрики (cookie, идентификатор посетителя).</p>'
            '<h2>Зачем</h2><p>Чтобы связаться с вами, ответить на ваш вопрос и подготовить консультацию; для статистики и улучшения сайта.</p>'
            '<h2>Правовые основания</h2><p>Ваше согласие, которое вы даёте, отправляя форму (ст. 6 и 9 Федерального закона № 152-ФЗ «О персональных данных»).</p>'
            '<h2>Хранение и передача</h2><p>Данные хранятся в CRM оператора и не передаются третьим лицам, кроме случаев, предусмотренных законом. '
            'Срок хранения — до достижения целей обработки или отзыва согласия.</p>'
            f'<h2>Ваши права</h2><p>Вы можете запросить сведения об обработке, уточнение или удаление данных и отозвать согласие, написав{" на " + mail if mail else " оператору"}'
            f'{" или позвонив по телефону " + _e(c["phone"]) if c.get("phone") else ""}.</p></body></html>')


# юр. направления (2026-11-19): ещё 15 дизайнов — app/legal_designs.py
from . import legal_designs  # noqa: E402,F401  (регистрирует себя в DESIGNS/LAYOUT/CSS/FONTS)
