"""15 дизайнов юридических направлений (2026-11-19) — по одному на направление,
каждый по мотивам концепции сильных сайтов ниши (оффер и механика, не тексты):

  accordion  семейные споры    — каталог подуслуг-аккордеон (как Яндекс Услуги) → плитки
  situations жилищные споры    — «ваша ситуация»: карточки подуслуг + липкий квиз сбоку
  deadline   наследство        — проверка 6-месячного срока по дате смерти → квиз
  receipt    потребители       — чек-лист «можно ли вернуть деньги» в виде кассового чека
  payout     трудовые споры    — калькулятор зарплата × месяцы прогула (ст. 394 ТК)
  banks      кредитные споры   — выбор банков/МФО чипсами → квиз
  iou        взыскание долгов  — проверка расписки по реквизитам (тетрадный лист)
  alert      приставы          — тревожный экран «счёт арестован?» → план действий
  garage     автоюрист         — разница выплаты и ремонта (ползунки), тёмный асфальт
  stages     уголовные дела    — стадия дела (таймлайн) + срочный звонок адвокату
  plot       земельные споры   — «узнать стоимость» + план в мессенджер
  clinic     медицинские споры — что произошло (чипсы) → что можно взыскать
  tenday     административные  — 10 дней на жалобу: срок по дате + анкета-постановление
  duty       военное право     — чат с военным юристом, хаки
  booking    миграционные      — запись на консультацию: день/время + мессенджер

Квиз у всех начинается с подуслуги (config.services), ?s=<id> — предвыбор.
"""
from __future__ import annotations

from typing import Any

from . import site_designs as sd

_e, ic, header, bullets, trust, how, faq, footer, quiz_box, svc_list, hero = (
    sd._e, sd.ic, sd.header, sd.bullets, sd.trust, sd.how, sd.faq, sd.footer, sd.quiz_box, sd.svc_list, sd._hero_text)

DESIGNS = {
    "accordion": {"title": "Каталог-аккордеон", "form": "подуслуги раскрываются как в Яндекс Услугах → плитки"},
    "situations": {"title": "Ваша ситуация", "form": "карточки ситуаций + квиз сбоку"},
    "deadline": {"title": "Проверка срока по дате", "form": "дата → сколько осталось → квиз"},
    "receipt": {"title": "Чек-лист-чек", "form": "чек-лист возврата в виде кассового чека"},
    "payout": {"title": "Калькулятор выплат", "form": "зарплата × месяцы → сумма → квиз"},
    "banks": {"title": "Выбор банков", "form": "чипсы банков/МФО → квиз"},
    "iou": {"title": "Проверка расписки", "form": "реквизиты расписки → оценка → квиз"},
    "alert": {"title": "Тревожный экран", "form": "срочный квиз «план действий»"},
    "garage": {"title": "Автогараж", "form": "разница выплаты и ремонта → квиз"},
    "stages": {"title": "Стадии дела", "form": "таймлайн стадий → срочный звонок"},
    "plot": {"title": "Узнать стоимость", "form": "квиз + план в мессенджер"},
    "clinic": {"title": "Медицинский", "form": "что произошло → что можно взыскать"},
    "tenday": {"title": "10 дней на жалобу", "form": "срок по дате + анкета списками"},
    "duty": {"title": "Военный чат", "form": "переписка с военным юристом"},
    "booking": {"title": "Запись на консультацию", "form": "день и время + мессенджер"},
}
FONTS = {
    "accordion": "Lora:wght@500;600;700&family=Nunito+Sans:wght@400;600;700",
    "situations": "Roboto+Slab:wght@500;700&family=Roboto:wght@400;500;700",
    "deadline": "Cormorant+Garamond:wght@500;600;700&family=Source+Sans+3:wght@400;600;700",
    "receipt": "JetBrains+Mono:wght@400;600;700&family=Manrope:wght@400;600;800",
    "payout": "Space+Grotesk:wght@400;500;700",
    "banks": "Inter+Tight:wght@400;500;600;800",
    "iou": "Merriweather:wght@400;700&family=Open+Sans:wght@400;600;700",
    "alert": "Oswald:wght@500;600;700&family=Roboto:wght@400;500;700",
    "garage": "Russo+One&family=Exo+2:wght@400;600;700",
    "stages": "Fira+Sans+Condensed:wght@400;600;800&family=Fira+Sans:wght@400;600",
    "plot": "Comfortaa:wght@500;700&family=Nunito:wght@400;600;700",
    "clinic": "Mulish:wght@400;600;800",
    "tenday": "Noto+Serif:wght@400;700&family=Noto+Sans:wght@400;600;700",
    "duty": "Play:wght@400;700&family=Golos+Text:wght@400;500;600",
    "booking": "Raleway:wght@500;700;800&family=Nunito+Sans:wght@400;600;700",
}


def W(c: dict[str, Any], cls: str = "w") -> str:
    return f'<div class="{cls}" data-w></div>' if isinstance(c.get("widget"), dict) else ""


def ph_btn(c: dict[str, Any], cls: str = "btn", label: str = "") -> str:
    if not c.get("phone"):
        return ""
    return f'<a class="{cls}" href="tel:{_e(c["phone_href"])}">{ic("phone", 18)} {_e(label or c["phone"])}</a>'


def accordion_list(c: dict[str, Any]) -> str:
    out = []
    for k, s in enumerate(c.get("services") or []):
        subs = "".join(f"<li>{_e(x)}</li>" for x in s.get("subs") or [])
        out.append(f'<details class="ac-i"{" open" if k == 0 else ""}><summary><b>{_e(s["t"])}</b><span>{_e(s.get("d") or "")}</span></summary>'
                   f'<div class="ac-b"><p>{_e(s.get("subtitle") or "")}</p>{"<ul>" + subs + "</ul>" if subs else ""}'
                   f'<a class="btn" href="?s={_e(s["id"])}" data-svc="{k}">Разобрать мою ситуацию</a></div></details>')
    return '<div class="ac">' + "".join(out) + "</div>"


def l_accordion(c):
    return (header(c, "hd-ac") + f'<main><section class="hero"><div class="wrap ac-hero"><div>{hero(c)}{bullets(c)}</div>'
            f'<div class="ac-blob">{trust(c, "trust trust-col")}</div></div></section>'
            f'<section class="ac-sec"><div class="wrap"><h2>{_e(c.get("services_title") or "Чем поможем")}</h2>{accordion_list(c)}</div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap">{quiz_box("qz-tiles2")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_situations(c):
    return (header(c, "hd-si") + f'<main><section class="hero"><div class="wrap">{hero(c)}{bullets(c, "bul bul-row")}</div></section>'
            f'<section class="si-sec"><div class="wrap si-grid"><div><h2>{_e(c.get("services_title") or "Выберите свою ситуацию")}</h2>'
            f'{svc_list(c, "svc svc-cards", "Это моя ситуация")}</div><aside class="si-side">{quiz_box("qz-list")}</aside></div></section>'
            f'<section class="tr-sec"><div class="wrap">{trust(c)}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_deadline(c):
    return (header(c, "hd-dl") + f'<main><section class="hero"><div class="wrap dl-hero">{hero(c)}<div class="dl-card">{W(c)}</div></div></section>'
            f'<section class="dl-svc"><div class="wrap">{svc_list(c, "svc svc-line", "Подробнее")}</div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap dl-q">{quiz_box("qz-list", letters=True)}</div></section>'
            f'{how(c, "Как проходит работа")}{faq(c)}</main>' + footer(c))


def l_receipt(c):
    return (header(c, "hd-rc") + f'<main><section class="hero"><div class="wrap rc-grid"><div>{hero(c)}{bullets(c)}</div>'
            f'<div class="rc-paper">{W(c)}</div></div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap rc-q">{quiz_box("qz-pills")}</div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-tags", "Выбрать")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_payout(c):
    return (header(c, "hd-po") + f'<main><section class="hero"><div class="wrap po-grid"><div>{hero(c)}{bullets(c)}{trust(c)}</div>'
            f'<div class="po-calc">{W(c)}</div></div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap po-q">{quiz_box("qz-list")}</div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-grid", "Выбрать")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_banks(c):
    return (header(c, "hd-bk") + f'<main><section class="hero"><div class="wrap bk-hero">{hero(c)}<div class="bk-card">{W(c)}</div></div></section>'
            f'<section class="tr-sec"><div class="wrap">{trust(c)}</div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap">{quiz_box("qz-tiles2")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_iou(c):
    return (header(c, "hd-io") + f'<main><section class="hero"><div class="wrap io-grid"><div>{hero(c)}{bullets(c)}</div>'
            f'<div class="io-sheet">{W(c)}</div></div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap io-q">{quiz_box("qz-list")}</div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-line", "Подробнее")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_alert(c):
    return (f'<div class="al-band">{_e(c.get("badge") or "Срочно")}</div>' + header(c, "hd-al")
            + f'<main><section class="hero"><div class="wrap al-grid"><div><h1 class="h-title">{_e(c.get("title"))}</h1>'
            f'<p class="h-sub">{_e(c.get("subtitle"))}</p>{bullets(c)}{ph_btn(c, "btn al-call", "Позвонить юристу")}</div>'
            f'{quiz_box("qz-al")}</div></section><section class="tr-sec"><div class="wrap">{trust(c)}</div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-grid", "Выбрать")}</div></section>{how(c, "План действий")}{faq(c)}</main>' + footer(c))


def l_garage(c):
    return (header(c, "hd-ga") + f'<main><section class="hero"><div class="wrap">{hero(c)}</div><div class="ga-road"></div></section>'
            f'<section class="ga-svc"><div class="wrap">{svc_list(c, "svc svc-icons", "Выбрать")}</div></section>'
            f'<section class="ga-calc-sec"><div class="wrap ga-grid"><div class="ga-calc">{W(c)}</div>{quiz_box("qz-list")}</div></section>'
            f'<section class="tr-sec"><div class="wrap">{trust(c)}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_stages(c):
    return (header(c, "hd-sg") + f'<main><section class="hero"><div class="wrap sg-hero"><h1 class="h-title">{_e(c.get("title"))}</h1>'
            f'<p class="h-sub">{_e(c.get("subtitle"))}</p>{ph_btn(c, "btn sg-call", "Срочно: " + str(c.get("phone") or ""))}</div></section>'
            f'<section class="sg-w"><div class="wrap">{W(c)}</div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap sg-q">{quiz_box("qz-list", letters=True)}</div></section>'
            f'<section class="tr-sec"><div class="wrap">{trust(c)}</div></section>{how(c, "Порядок работы")}{faq(c)}</main>' + footer(c))


def l_plot(c):
    return (header(c, "hd-pl") + f'<main><section class="hero"><div class="wrap pl-grid"><div>{hero(c)}{bullets(c)}</div>'
            f'<div class="pl-card">{quiz_box("qz-tiles2")}</div></div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-grid", "Узнать стоимость")}</div></section>'
            f'<section class="tr-sec"><div class="wrap">{trust(c)}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_clinic(c):
    return (header(c, "hd-cl") + f'<main><section class="hero"><div class="wrap cl-hero">{hero(c)}{bullets(c, "bul bul-row")}</div></section>'
            f'<section class="cl-w"><div class="wrap"><div class="cl-card">{W(c)}</div></div></section>'
            f'<section class="qz-sec" id="quiz"><div class="wrap cl-q">{quiz_box("qz-pills")}</div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-line", "Подробнее")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_tenday(c):
    return (header(c, "hd-td") + f'<main><div class="td-strip"><div class="wrap">{_e(c.get("badge") or "Обжалование")}</div></div>'
            f'<section class="hero"><div class="wrap td-grid"><div>{hero(c)}<div class="td-w">{W(c)}</div></div>'
            f'<div class="td-doc"><h2>{_e(c.get("form_title") or "Анкета")}</h2><p class="td-note">{_e(c.get("result_text") or "")}</p>'
            f'<div data-all="select"></div></div></div></section><section class="tr-sec"><div class="wrap">{trust(c)}</div></section>'
            f'{how(c, "Порядок обжалования")}{faq(c, "Справка")}</main>' + footer(c))


def l_duty(c):
    op = _e(c.get("operator") or "Военный юрист")
    av = "".join(w[:1] for w in str(c.get("operator") or "В Ю").split()[:2]).upper()
    chat = (f'<div class="ch" data-chat><div class="ch-head"><span class="ch-av">{_e(av)}</span><div><b>{op}</b>'
            f'<small>{_e(c.get("operator_role") or "военный юрист")} · <i class="ch-on"></i>на связи</small></div></div>'
            '<div class="ch-log" aria-live="polite"></div><div class="ch-foot"></div></div>')
    return (header(c, "hd-du") + f'<main><section class="hero"><div class="wrap du-grid"><div>{hero(c)}{bullets(c)}{trust(c)}</div>{chat}</div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-grid", "Спросить")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


def l_booking(c):
    return (header(c, "hd-bo") + f'<main><section class="hero"><div class="wrap bo-grid"><div>{hero(c)}{bullets(c)}{trust(c, "trust trust-col")}</div>'
            f'<div class="bo-card"><div class="bo-head">{ic("clock")} {_e(c.get("form_title") or "Запись на консультацию")}</div>{quiz_box("qz-list")}</div></div></section>'
            f'<section><div class="wrap">{svc_list(c, "svc svc-grid", "Записаться")}</div></section>{how(c)}{faq(c)}</main>' + footer(c))


LAYOUT = {"accordion": l_accordion, "situations": l_situations, "deadline": l_deadline, "receipt": l_receipt,
          "payout": l_payout, "banks": l_banks, "iou": l_iou, "alert": l_alert, "garage": l_garage, "stages": l_stages,
          "plot": l_plot, "clinic": l_clinic, "tenday": l_tenday, "duty": l_duty, "booking": l_booking}

# Общие компоненты (через CSS-переменные) + у каждого дизайна своя вёрстка/палитра.
COMMON = """body{background:var(--bg);color:var(--fg);font-family:var(--f2)}h1,h2,h3,.h-title{font-family:var(--f1)}
.hd{background:var(--hd,#fff);color:var(--hdc,inherit)}.hd-ph{color:var(--ac)}.badge{background:var(--soft);color:var(--ac2,var(--ac))}
.bul .ic{color:var(--ac)}.tr-i b{color:var(--ac)}.hw-n{background:var(--soft);color:var(--ac2,var(--ac))}
.btn{background:var(--ac);color:var(--on,#fff);border-radius:var(--r)}
.qz{background:var(--card);color:var(--cfg,var(--fg));border-radius:var(--r);padding:22px;box-shadow:var(--sh,0 16px 40px rgba(0,0,0,.08))}
.qz-bar i{background:var(--ac)}.qz-opt{background:var(--opt,var(--soft));border-radius:calc(var(--r) - 4px);padding:14px 16px;color:inherit}
.qz-opt:hover,.qz-opt.on{border-color:var(--ac)}.qz-mark{width:26px;height:26px;border-radius:50%;border:2px solid var(--ac);display:grid;place-items:center;font-size:12px;font-weight:700;color:var(--ac)}
.qz-opt.on .qz-mark{background:var(--ac);color:var(--on,#fff)}
.svc{display:grid;gap:12px}.svc-i{display:flex;flex-direction:column;gap:4px;text-decoration:none;color:inherit;background:var(--card);border:1.5px solid var(--line,rgba(127,127,127,.2));border-radius:var(--r);padding:16px}
.svc-i b{font-size:17px}.svc-i span{font-size:14px;opacity:.75}.svc-i em{font-style:normal;font-weight:700;color:var(--ac);font-size:14px;margin-top:4px}
.svc-i ul{margin:6px 0 0;padding-left:18px;font-size:13.5px;opacity:.8}.svc-i.on,.svc-i:hover{border-color:var(--ac)}
.svc-grid{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}.svc-line .svc-i{flex-direction:row;flex-wrap:wrap;align-items:baseline;gap:6px 14px}.svc-line .svc-i em{margin-left:auto}
.svc-tags{display:flex;flex-wrap:wrap}.svc-tags .svc-i{padding:10px 16px;border-radius:99px;flex-direction:row;gap:8px}.svc-tags .svc-i span,.svc-tags .svc-i em{display:none}
.w{background:var(--card);color:var(--cfg,var(--fg));border-radius:var(--r);padding:22px;box-shadow:var(--sh,0 16px 40px rgba(0,0,0,.08))}
.w-t{margin:0 0 8px;font-size:21px}.w-p{margin:0 0 14px;opacity:.8;font-size:15px}.w-l{display:grid;gap:6px;font-weight:600;margin-bottom:12px}
.w-l input{padding:13px;border-radius:10px;border:1px solid rgba(127,127,127,.4);font-size:16px;background:#fff;color:#111}
.w .btn{width:100%;margin-top:6px}.w-res{margin-top:16px;border-top:1px dashed rgba(127,127,127,.4);padding-top:14px}.w-big{font-size:19px;font-weight:700;margin:0 0 8px}.w-big.late{color:#c2410c}
.w-list{display:grid;gap:8px;margin-bottom:8px}.w-ck{display:flex;gap:10px;align-items:flex-start;cursor:pointer}.w-ck input{width:20px;height:20px;margin-top:2px;accent-color:var(--ac)}
.w-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}.w-chips button{border:1.5px solid var(--line,rgba(127,127,127,.35));background:transparent;color:inherit;border-radius:99px;padding:9px 15px;font-weight:600}
.w-chips button.on{background:var(--ac);border-color:var(--ac);color:var(--on,#fff)}
.w-sl{display:grid;grid-template-columns:1fr auto;gap:6px;font-weight:600;margin-bottom:14px}.w-sl output{font-size:20px;color:var(--ac)}.w-sl input{grid-column:1/-1;accent-color:var(--ac)}
.w-st{list-style:none;padding:0;margin:0;display:grid;gap:10px}.w-st button{width:100%;text-align:left;border:1.5px solid var(--line,rgba(127,127,127,.3));background:transparent;color:inherit;border-radius:var(--r);padding:14px 16px;display:grid;gap:2px}
.w-st button span{font-size:13.5px;opacity:.75}.w-st button.on{border-color:var(--ac);background:var(--soft)}
.lf-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.lf-ms{display:grid;gap:6px;font-size:13.5px;font-weight:600}.lf-ms>div{display:flex;flex-wrap:wrap;gap:6px}
.lf-ms label{display:block}.lf-ms input{position:absolute;opacity:0}.lf-ms label span{display:inline-block;border:1.5px solid var(--line,rgba(127,127,127,.35));border-radius:99px;padding:7px 13px;font-weight:600;cursor:pointer}
.lf-ms input:checked+span{background:var(--ac);border-color:var(--ac);color:var(--on,#fff)}
.how,.faq{background:var(--sec,transparent)}.ft{background:var(--ftb,#111);color:var(--ftc,#ddd)}
@media(max-width:860px){.lf-row{grid-template-columns:1fr}}
"""

_CSS = {
"accordion": """:root{--bg:#fff8f2;--fg:#3b2a24;--ac:#c2563a;--ac2:#9c3d25;--soft:#fbe4d8;--card:#fff;--r:20px;--f1:Lora,serif;--f2:'Nunito Sans',sans-serif;--sec:#fff;--ftb:#3b2a24;--ftc:#f6dccf}
.hero{padding:44px 0}.ac-hero{display:grid;grid-template-columns:1.3fr .7fr;gap:30px;align-items:center}.ac-blob{background:#fbe4d8;border-radius:46% 54% 42% 58%/55% 40% 60% 45%;padding:44px 34px}
.trust-col{grid-template-columns:1fr}.ac-sec{background:#fff}.ac{display:grid;gap:10px}.ac-i{border:1.5px solid #f0d3c4;border-radius:16px;background:#fffdfb}
.ac-i summary{list-style:none;cursor:pointer;padding:16px 18px;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}.ac-i summary::after{content:"+";margin-left:auto;font-size:24px;color:#c2563a}
.ac-i[open] summary::after{content:"–"}.ac-i summary b{font-size:18px}.ac-i summary span{font-size:14px;opacity:.7}.ac-b{padding:0 18px 18px}.ac-b ul{columns:2;column-gap:28px;padding-left:18px;margin:8px 0 14px}
.ac-b li{margin-bottom:6px;font-size:14.5px}.qz-tiles2 .qz-opts{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
@media(max-width:860px){.ac-hero{grid-template-columns:1fr}.ac-b ul{columns:1}}""",
"situations": """:root{--bg:#f4f6fb;--fg:#17223b;--ac:#b8432f;--ac2:#8d2f20;--soft:#f7e2dc;--card:#fff;--r:6px;--f1:'Roboto Slab',serif;--f2:Roboto,sans-serif;--hd:#17223b;--hdc:#fff;--ftb:#17223b}
.hd-si .hd-ph{color:#ffb4a6}.hero{background:#17223b;color:#fff;padding:40px 0 56px}.hero .badge{background:#b8432f;color:#fff}.bul-row{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}.hero .bul .ic{color:#ffb4a6}
.si-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:28px;align-items:start}.si-side{position:sticky;top:16px}.svc-cards{grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.svc-cards .svc-i{border-left:5px solid #b8432f;border-radius:4px}.qz{box-shadow:0 0 0 1px #d8deea,0 20px 40px rgba(23,34,59,.12)}
@media(max-width:900px){.si-grid{grid-template-columns:1fr}.si-side{position:static}}""",
"deadline": """:root{--bg:#f7f2ea;--fg:#2a1a1f;--ac:#7a1f35;--soft:#efe1d6;--card:#fffdf8;--r:2px;--f1:'Cormorant Garamond',serif;--f2:'Source Sans 3',sans-serif;--line:#d9c7b8;--ftb:#2a1a1f}
.hero{padding:56px 0 40px;text-align:center}.dl-hero{max-width:780px}.h-title{font-size:clamp(34px,6vw,60px);font-weight:600}.dl-card{max-width:520px;margin:26px auto 0;text-align:left}
.w{border:1px solid #d9c7b8;box-shadow:0 2px 0 #d9c7b8}.dl-svc{padding:12px 0}.dl-q{max-width:760px}.qz{border:1px solid #d9c7b8;box-shadow:none}
.qz-opt{background:transparent;border:1px solid #d9c7b8;border-radius:2px}.svc-line .svc-i{border-radius:0;border-width:0 0 1px;background:transparent}""",
"receipt": """:root{--bg:#ffd84d;--fg:#1a1a1a;--ac:#1a1a1a;--on:#ffd84d;--soft:#fff3c2;--card:#fff;--r:14px;--f1:Manrope,sans-serif;--f2:Manrope,sans-serif;--sec:#fff8dc;--ftb:#1a1a1a;--ftc:#ffe680}
.hd{background:#1a1a1a;color:#ffd84d}.hero{padding:40px 0}.h-title{font-weight:800}.rc-grid{display:grid;grid-template-columns:1fr 420px;gap:34px;align-items:start}
.rc-paper .w{font-family:'JetBrains Mono',monospace;border-radius:0;box-shadow:0 18px 40px rgba(0,0,0,.18);position:relative;font-size:14.5px}
.rc-paper .w::after{content:"";position:absolute;left:0;right:0;bottom:-12px;height:12px;background:linear-gradient(-45deg,transparent 8px,#fff 0) 0 0/16px 12px,linear-gradient(45deg,transparent 8px,#fff 0) 0 0/16px 12px}
.rc-paper .w-t{font-family:'JetBrains Mono',monospace;text-align:center;border-bottom:2px dashed #1a1a1a;padding-bottom:10px}
.qz-pills .qz-opts{display:flex;flex-wrap:wrap}.qz-pills .qz-opt{width:auto;border-radius:99px;background:#fff3c2}.qz-pills .qz-mark{display:none}.rc-q{max-width:820px}
@media(max-width:900px){.rc-grid{grid-template-columns:1fr}}""",
"payout": """:root{--bg:#1e2329;--fg:#e9edf1;--ac:#ff7a1a;--on:#111;--soft:#2b323a;--card:#262c33;--cfg:#e9edf1;--r:4px;--f1:'Space Grotesk',sans-serif;--f2:'Space Grotesk',sans-serif;--line:#3a434d;--hd:#14181c;--sec:#22282e;--ftb:#14181c}
.hero{padding:44px 0;background:repeating-linear-gradient(135deg,#1e2329 0 22px,#20262c 22px 44px)}.po-grid{display:grid;grid-template-columns:1fr 440px;gap:30px;align-items:start}
.h-title{text-transform:uppercase;letter-spacing:-.5px}.po-calc .w{border-top:6px solid #ff7a1a}.qz-opt{border-color:#3a434d}.po-q{max-width:820px}
.lf input[type=text],.lf input[type=tel],.lf input:not([type]),.lf select{background:#14181c;color:#fff;border-color:#3a434d}
@media(max-width:900px){.po-grid{grid-template-columns:1fr}}""",
"banks": """:root{--bg:#f2f6ff;--fg:#0b1a3a;--ac:#00c48c;--on:#062b20;--soft:#e2f8f1;--card:#fff;--r:18px;--f1:'Inter Tight',sans-serif;--f2:'Inter Tight',sans-serif;--ftb:#0b1a3a}
.hd{background:#0b1a3a;color:#fff}.hero{background:radial-gradient(circle at 80% 0,#1f4fd6,#0b1a3a 60%);color:#fff;padding:52px 0 70px}.bk-hero{text-align:center;max-width:900px}
.h-title{font-weight:800}.hero .badge{background:rgba(0,196,140,.2);color:#7ff0c9}.bk-card{max-width:760px;margin:24px auto 0;text-align:left}
.w-chips button{font-size:15px;padding:11px 18px}.qz-tiles2 .qz-opts{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}.qz-tiles2{max-width:900px;margin:0 auto}""",
"iou": """:root{--bg:#f3f0e8;--fg:#2b2b2b;--ac:#1d4ed8;--soft:#e3eafc;--card:#fffef9;--r:8px;--f1:Merriweather,serif;--f2:'Open Sans',sans-serif;--ftb:#2b2b2b}
.hero{padding:42px 0}.io-grid{display:grid;grid-template-columns:1fr 440px;gap:30px;align-items:start}
.io-sheet .w{background:#fffef9 repeating-linear-gradient(#fffef9 0 31px,#cfe0f5 31px 32px);border-left:3px solid #e8a0a0;box-shadow:0 14px 30px rgba(0,0,0,.12);font-size:15px}
.io-q{max-width:800px}.svc-line .svc-i{background:transparent;border-width:0 0 1px;border-radius:0}
@media(max-width:900px){.io-grid{grid-template-columns:1fr}}""",
"alert": """:root{--bg:#fff;--fg:#1c1c1c;--ac:#d61f1f;--soft:#fde3e3;--card:#fff;--r:10px;--f1:Oswald,sans-serif;--f2:Roboto,sans-serif;--sec:#fafafa;--ftb:#1c1c1c}
.al-band{background:#d61f1f;color:#fff;text-align:center;font-family:Oswald,sans-serif;letter-spacing:1px;text-transform:uppercase;padding:8px 16px;font-size:14px}
.hero{padding:40px 0;background:linear-gradient(180deg,#fff,#fff5f5)}.al-grid{display:grid;grid-template-columns:1fr 460px;gap:30px;align-items:start}.h-title{text-transform:uppercase;font-weight:700}
.qz-al{border:3px solid #d61f1f;box-shadow:0 20px 50px rgba(214,31,31,.18)}.al-call{background:#1c1c1c;margin-top:6px}
@media(max-width:900px){.al-grid{grid-template-columns:1fr}}""",
"garage": """:root{--bg:#121417;--fg:#f1f1f1;--ac:#ffcc00;--on:#121417;--soft:#24282d;--card:#1b1e22;--cfg:#f1f1f1;--r:6px;--f1:'Russo One',sans-serif;--f2:'Exo 2',sans-serif;--line:#33383e;--hd:#0b0c0e;--sec:#16181b;--ftb:#0b0c0e}
.hero{padding:52px 0 0;background:linear-gradient(180deg,#0b0c0e,#121417)}.h-title{text-transform:uppercase}
.ga-road{height:34px;margin-top:36px;background:#2a2d31 repeating-linear-gradient(90deg,#ffcc00 0 60px,transparent 60px 120px) center/100% 6px no-repeat}
.svc-icons{grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}.svc-icons .svc-i{border-top:4px solid #ffcc00}
.ga-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}.qz-opt{border-color:#33383e}
.lf input[type=text],.lf input[type=tel],.lf input:not([type]),.lf select{background:#0b0c0e;color:#fff;border-color:#33383e}
@media(max-width:900px){.ga-grid{grid-template-columns:1fr}}""",
"stages": """:root{--bg:#fff;--fg:#000;--ac:#000;--soft:#f0f0f0;--card:#fff;--r:0px;--f1:'Fira Sans Condensed',sans-serif;--f2:'Fira Sans',sans-serif;--line:#000;--sh:none;--ftb:#000}
.hd{border-bottom:3px solid #000}.hero{padding:60px 0 30px}.sg-hero .h-title{font-size:clamp(40px,8vw,84px);font-weight:800;text-transform:uppercase;line-height:.95}
.sg-call{background:#e11d1d;font-size:19px;padding:18px 26px}.w{border:3px solid #000}.w-st{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.w-st button{border:2px solid #000;border-radius:0;min-height:96px}.w-st button.on{background:#000;color:#fff}.qz{border:3px solid #000}.sg-q{max-width:820px}
.qz-opt{border:2px solid #000;background:#fff}.qz-opt:hover,.qz-opt.on{background:#000;color:#fff}""",
"plot": """:root{--bg:#f1f5ec;--fg:#23331f;--ac:#4c7a2e;--soft:#e1ecd3;--card:#fff;--r:24px;--f1:Comfortaa,sans-serif;--f2:Nunito,sans-serif;--sec:#fff;--ftb:#23331f}
.hero{padding:44px 0;background:linear-gradient(90deg,rgba(76,122,46,.08) 1px,transparent 1px) 0 0/40px 40px,linear-gradient(rgba(76,122,46,.08) 1px,transparent 1px) 0 0/40px 40px}
.pl-grid{display:grid;grid-template-columns:1fr 480px;gap:30px;align-items:start}.h-title{font-weight:700}.qz-tiles2 .qz-opts{grid-template-columns:1fr 1fr}
.qz-tiles2 .qz-opt{flex-direction:column;align-items:flex-start;gap:2px}.qz-tiles2 .qz-mark{display:none}
@media(max-width:900px){.pl-grid{grid-template-columns:1fr}.qz-tiles2 .qz-opts{grid-template-columns:1fr}}""",
"clinic": """:root{--bg:#f5fbfb;--fg:#123638;--ac:#0e9384;--soft:#d9f3f0;--card:#fff;--r:28px;--f1:Mulish,sans-serif;--f2:Mulish,sans-serif;--sec:#fff;--ftb:#123638}
.hero{padding:50px 0 20px;text-align:center}.cl-hero{max-width:860px}.h-title{font-weight:800}.bul-row{grid-template-columns:repeat(auto-fit,minmax(220px,1fr));text-align:left}
.cl-card{max-width:820px;margin:0 auto}.cl-q{max-width:820px}.qz-pills .qz-opts{display:flex;flex-wrap:wrap}.qz-pills .qz-opt{width:auto;border-radius:99px}.qz-pills .qz-mark{display:none}""",
"tenday": """:root{--bg:#eef0f2;--fg:#1f2933;--ac:#1d3b6e;--soft:#dde5f2;--card:#fff;--r:3px;--f1:'Noto Serif',serif;--f2:'Noto Sans',sans-serif;--ftb:#1f2933}
.td-strip{background:#1d3b6e;color:#fff;font-size:13px;padding:6px 0;text-transform:uppercase;letter-spacing:.6px}.hero{padding:36px 0}
.td-grid{display:grid;grid-template-columns:1fr 460px;gap:30px;align-items:start}.td-w{margin-top:16px}
.td-doc{background:#fff;padding:26px;border:1px solid #c9d1db;box-shadow:6px 6px 0 #c9d1db}.td-doc h2{text-align:center;text-transform:uppercase;font-size:18px;letter-spacing:.5px;border-bottom:1px solid #1f2933;padding-bottom:10px}
.td-note{font-size:13.5px;opacity:.75}.sel{display:grid;gap:4px;font-size:13.5px;font-weight:600;margin-bottom:10px}.sel small{font-weight:400;opacity:.7}
.sel select{padding:11px;border:1px solid #9aa5b1;border-radius:2px;font-size:15px;background:#fff}
@media(max-width:900px){.td-grid{grid-template-columns:1fr}}""",
"duty": """:root{--bg:#2f3526;--fg:#eef0e6;--ac:#c9b458;--on:#20240f;--soft:#3d4531;--card:#383f2d;--cfg:#eef0e6;--r:8px;--f1:Play,sans-serif;--f2:'Golos Text',sans-serif;--line:#56604a;--hd:#232819;--sec:#2a3021;--ftb:#1d2115}
.hero{padding:40px 0}.du-grid{display:grid;grid-template-columns:1.05fr .95fr;gap:30px;align-items:start}.h-title{text-transform:uppercase}
.ch{background:#e9ead8;color:#20240f;border-radius:14px;overflow:hidden;display:flex;flex-direction:column;height:560px;box-shadow:0 20px 50px rgba(0,0,0,.35)}
.ch-head{display:flex;gap:10px;align-items:center;background:#4b5536;color:#fff;padding:12px 14px}.ch-head small{display:block;opacity:.85;font-size:12.5px}
.ch-av{width:42px;height:42px;border-radius:50%;background:#c9b458;color:#20240f;display:grid;place-items:center;font-weight:800}.ch-on{display:inline-block;width:8px;height:8px;border-radius:50%;background:#b6f26b;margin-right:4px}
.ch-log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px}.ch-msg{max-width:84%;padding:9px 12px;border-radius:10px;font-size:15px;line-height:1.4}
.ch-msg small{display:block;margin-top:4px;opacity:.7;font-size:12.5px}.bot{background:#fff;align-self:flex-start}.me{background:#d7e3b5;align-self:flex-end}
.typing i{display:inline-block;width:7px;height:7px;margin:0 2px;border-radius:50%;background:#889;animation:bl 1s infinite}.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}@keyframes bl{50%{opacity:.25}}
.ch-foot{background:#d9dbc4;padding:10px}.ch-chips{display:flex;flex-wrap:wrap;gap:8px}.ch-chips button{border:1.5px solid #4b5536;color:#20240f;background:#fff;border-radius:99px;padding:8px 14px;font-weight:600;font-size:14.5px}
.ch-in{display:flex;gap:8px}.ch-in input{flex:1;border:0;border-radius:22px;padding:12px 16px;font-size:16px}.ch-in button{width:46px;height:46px;border-radius:50%;border:0;background:#4b5536;color:#fff}
.ch-in.err input{outline:2px solid #dc2626}.ch-ok{display:flex;gap:6px;font-size:12px;margin-top:6px}.ch-wait{text-align:center;font-size:14px}
@media(max-width:900px){.du-grid{grid-template-columns:1fr}.ch{height:520px}}""",
"booking": """:root{--bg:#f6f4ee;--fg:#14213d;--ac:#14213d;--on:#fcd581;--soft:#e8ebf3;--card:#fff;--r:16px;--f1:Raleway,sans-serif;--f2:'Nunito Sans',sans-serif;--ftb:#14213d;--ftc:#fcd581}
.hd{background:#14213d;color:#fcd581}.hero{padding:44px 0}.bo-grid{display:grid;grid-template-columns:1fr 460px;gap:30px;align-items:start}.h-title{font-weight:800}
.trust-col{grid-template-columns:1fr 1fr}.bo-card{background:#fff;border-radius:16px;box-shadow:0 22px 50px rgba(20,33,61,.15);overflow:hidden}
.bo-head{background:#14213d;color:#fcd581;padding:14px 20px;font-weight:700;display:flex;gap:8px;align-items:center}.bo-card .qz{box-shadow:none}
@media(max-width:900px){.bo-grid{grid-template-columns:1fr}}""",
}
CSS = {k: COMMON + v for k, v in _CSS.items()}

# регистрация в общем движке (импорт в любом порядке: site_designs импортирует этот модуль в конце)
sd.DESIGNS.update(DESIGNS)
sd.LAYOUT.update(LAYOUT)
sd.CSS.update(CSS)
sd.FONTS.update(FONTS)
