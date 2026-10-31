import asyncio
import base64
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_PATH": os.path.join(tmp, "t.db"),
    "APP_USERNAME": "admin", "APP_PASSWORD": "x" * 20, "APP_ENV": "development",
    "METRIKA_TOKEN_ENCRYPTION_KEY": "3t4n0m9y1uV6mJm0GmQ0mQm3q8Q9rXqG0q1l8o3m5aE=",
    "METRIKA_OAUTH_CLIENT_ID": "a", "METRIKA_OAUTH_CLIENT_SECRET": "b", "METRIKA_OAUTH_REDIRECT_URI": "https://x/cb",
    "LK_WORKER": "0",
})
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cryptography.fernet import Fernet
os.environ["METRIKA_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient
from app import db, lk, main
from app.metrika_client import MetrikaAPIError, YandexMetrikaClient
from app.token_crypto import encrypt_secret

uploads, goals_created = [], []
LEADS = []
FAIL = {"on": False}


UF = {"fields": [], "updates": [], "deny": False, "meta": {}}


async def fake_b24(webhook, method, params):
    if method == "crm.lead.fields":
        return {"result": UF["meta"]}
    if method == "crm.lead.userfield.list":
        return {"result": list(UF["fields"]), "next": None}
    if method == "crm.lead.userfield.add":
        if UF["deny"]:
            raise RuntimeError("Б24: INSUFFICIENT_SCOPE Insufficient scope")
        f = params["fields"]
        UF["fields"].append({"FIELD_NAME": "UF_CRM_" + f["FIELD_NAME"], "USER_TYPE_ID": f["USER_TYPE_ID"],
                             "EDIT_FORM_LABEL": f["EDIT_FORM_LABEL"]})
        return {"result": len(UF["fields"])}
    if method == "crm.lead.update":
        UF["updates"].append(params)
        return {"result": True}
    return {"result": LEADS, "next": None}


async def fake_upload(self, counter_id, csv_text):
    if FAIL["on"]:
        raise MetrikaAPIError("Нет прав на загрузку")
    uploads.append((counter_id, csv_text))
    return {"uploading": {"id": 1}}


async def fake_goals(self, counter_id):
    return []


async def fake_create_goal(self, counter_id, default_price=None):
    goals_created.append(counter_id)
    return {"id": 777}


async def fake_counters(self):
    return [{"id": 111, "name": "site", "site": "site.ru", "goals": []}]


lk.b24_call = fake_b24
lk._is_public_host = lambda host: host != "internal.local"
YandexMetrikaClient.upload_offline_conversions = fake_upload
YandexMetrikaClient.list_goals = fake_goals
YandexMetrikaClient.create_goal = fake_create_goal
YandexMetrikaClient.list_counters = fake_counters

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    ok = ok and bool(cond)


def auth(u, p):
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


ADMIN = auth("admin", "x" * 20)

with TestClient(main.app) as c:
    # --- доступ
    check("без пароля /lk -> 401", c.get("/lk").status_code == 401)
    check("админ /lk -> 200", c.get("/lk", headers=ADMIN).status_code == 200)
    r = c.post("/api/lk/users", headers=ADMIN, json={"username": "client1", "password": "longpassword1"})
    check("создание пользователя", r.status_code == 201, r.text)
    check("короткий пароль отклонён", c.post("/api/lk/users", headers=ADMIN, json={"username": "c2", "password": "short"}).status_code == 422)
    check("логин админа занят", c.post("/api/lk/users", headers=ADMIN, json={"username": "admin", "password": "longpassword1"}).status_code == 409)
    U = auth("client1", "longpassword1")
    check("пользователь /lk -> 200", c.get("/lk", headers=U).status_code == 200)
    check("пользователь не видит '/'", c.get("/", headers=U).status_code == 401)
    check("пользователь не видит проекты", c.get("/api/projects", headers=U).status_code == 401)
    check("пользователь не видит Click.ru", c.get("/api/connection", headers=U).status_code == 401)
    check("пользователь не создаёт пользователей", c.post("/api/lk/users", headers=U, json={"username": "zzz", "password": "longpassword1"}).status_code == 403)
    check("неверный пароль -> 401", c.get("/lk", headers=auth("client1", "bad")).status_code == 401)
    check("страница админа содержит ссылку на ЛК", "/lk" in c.get("/", headers=ADMIN).text)
    check("в /lk у клиента нет блока пользователей", 'id="users"' not in c.get("/lk", headers=U).text)

    # --- подключение Яндекс ID (пишем в БД напрямую, как это делает callback)
    conn_admin = db.create_metrika_connection("admin", "A", encrypt_secret("tokA"))
    conn_u = db.create_metrika_connection("client1", "U", encrypt_secret("tokU"))
    cid_a, cid_u = conn_admin["id"], conn_u["id"]
    check("клиент не видит чужие подключения", [x["label"] for x in c.get("/api/metrika/connections", headers=U).json()["items"]] == ["U"])

    # --- вебхук
    bad = c.post("/api/lk/sources", headers=ADMIN, json={"name": "x", "webhook": "http://p.bitrix24.ru/rest/1/abc"})
    check("http-вебхук отклонён", bad.status_code == 422)
    bad = c.post("/api/lk/sources", headers=ADMIN, json={"name": "x", "webhook": "https://127.0.0.1/rest/1/abc"})
    check("IP-вебхук отклонён", bad.status_code == 422)
    bad = c.post("/api/lk/sources", headers=ADMIN, json={"name": "x", "webhook": "https://internal.local/rest/1/abc"})
    check("внутренний хост отклонён", bad.status_code == 422)
    bad = c.post("/api/lk/sources", headers=ADMIN, json={"name": "x", "webhook": "https://p.bitrix24.ru/foo"})
    check("не-rest путь отклонён", bad.status_code == 422)
    bad = c.post("/api/lk/sources", headers=ADMIN, json={"name": "x", "webhook": "https://p.bitrix24.ru/rest/1/abc", "mode": "score_field"})
    check("скоринг без поля отклонён", bad.status_code == 422)

    r = c.post("/api/lk/sources", headers=ADMIN, json={
        "name": "Б24-1", "webhook": "https://p.bitrix24.ru/rest/1/secretsecret", "mode": "simple"})
    check("источник создан", r.status_code == 201, r.text)
    sid = r.json()["id"]
    src = c.get("/api/lk/sources", headers=ADMIN).json()["items"][0]
    check("вебхук в ответе замаскирован", "secretsecret" not in str(src) and src["webhook"].endswith("cret"), src["webhook"])
    check("источник по умолчанию в пробном режиме", src["dry_run"] == 1)
    check("клиент не видит чужие источники", c.get("/api/lk/sources", headers=U).json()["items"] == [])
    check("клиент не может запустить чужой источник", c.post(f"/api/lk/sources/{sid}/run", headers=U).status_code == 404)

    # --- привязки
    r = c.post("/api/lk/mappings", headers=ADMIN, json={"campaign_keys": "713904508, 713988705\nabc", "connection_id": cid_a, "counter_id": 111})
    check("привязка + создание цели", r.status_code == 201 and r.json()["saved"] == 2 and r.json()["goal_created"], r.text)
    check("цель lead создана один раз", goals_created == [111])
    r = c.post("/api/lk/mappings", headers=ADMIN, json={"campaign_keys": "713904508", "connection_id": cid_u, "counter_id": 111})
    check("чужое подключение для привязки -> 404", r.status_code == 404, r.text)
    check("мусорные ключи -> 422", c.post("/api/lk/mappings", headers=ADMIN, json={"campaign_keys": "abc", "connection_id": cid_a, "counter_id": 111}).status_code == 422)

    # --- прогон: базовая линия
    LEADS[:] = [
        {"ID": "1", "UTM_CAMPAIGN": "713904508", "COMMENTS": "_ym_uid: 1111111111", "PHONE": [{"VALUE": "89001112233"}]},
        {"ID": "2", "UTM_CAMPAIGN": "x_713988705", "SOURCE_DESCRIPTION": "https://s.ru/?yclid=5555"},
        {"ID": "3", "UTM_CAMPAIGN": "999999999", "COMMENTS": "_ym_uid: 3"},
    ]
    r = c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN)
    st = r.json()["stats"]
    check("первый прогон = базовая линия, без отправки", st.get("baseline") == 2 and not uploads and not st.get("sent"), str(st))
    check("неизвестная кампания попала в список", [x["campaign_key"] for x in c.get("/api/lk/unmapped", headers=ADMIN).json()["items"]] == ["999999999"])

    # --- пробный режим: новый лид -> dry
    LEADS.append({"ID": "4", "UTM_CAMPAIGN": "713904508", "COMMENTS": "Yandex ClientID: 424242", "PHONE": []})
    st = c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN).json()["stats"]
    check("пробный режим: dry, не отправлено", st.get("dry") == 1 and not uploads, str(st))
    st = c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN).json()["stats"]
    check("повторный пробный прогон не дублирует", not st.get("dry"), str(st))

    # --- боевой режим
    c.post(f"/api/lk/sources/{sid}/live?on=true", headers=ADMIN)
    st = c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN).json()["stats"]
    check("после включения виденное в dry не уходит повторно", not uploads and not st.get("sent"), str(st))
    LEADS.append({"ID": "5", "UTM_CAMPAIGN": "713988705", "COMMENTS": "_ym_uid: 777", "PHONE": []})
    LEADS.append({"ID": "6", "UTM_CAMPAIGN": "713988705", "SOURCE_DESCRIPTION": "https://s.ru/?utm_yclid=9999"})
    LEADS.append({"ID": "7", "UTM_CAMPAIGN": "713988705", "PHONE": [{"VALUE": "8 (900) 111-22-33"}]})
    LEADS.append({"ID": "8", "UTM_CAMPAIGN": "713988705"})
    st = c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN).json()["stats"]
    check("отправлено 3, без идентификатора пропущен 1", st.get("sent") == 3 and st.get("no_id") == 1, str(st))
    csvs = [u[1] for u in uploads]
    check("CSV ClientId", any(x.startswith("ClientId,Target,DateTime\n777,lead,") for x in csvs), str(csvs))
    check("CSV Yclid", any(x.startswith("Yclid,Target,DateTime\n9999,lead,") for x in csvs), str(csvs))
    check("CSV Phone в формате +7", any(x.startswith("Phone,Target,DateTime\n+79001112233,lead,") for x in csvs), str(csvs))
    st = c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN).json()["stats"]
    check("повторный боевой прогон ничего не шлёт", not st.get("sent") and len(uploads) == 3 and st.get("no_id") == 1, str(st))
    log = c.get("/api/lk/log", headers=ADMIN).json()
    check("no_id залогирован один раз", sum(1 for l in log["items"] if l["status"] == "skipped_no_id") == 1)
    check("клиент не видит чужой журнал", c.get("/api/lk/log", headers=U).json()["items"] == [])

    # --- ошибка загрузки
    LEADS.append({"ID": "9", "UTM_CAMPAIGN": "713904508", "COMMENTS": "_ym_uid: 99"})
    FAIL["on"] = True
    for _ in range(3):
        c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN)
    log = c.get("/api/lk/log?status=failed", headers=ADMIN).json()
    check("ошибка залогирована один раз, не 3", len(log["items"]) == 1, str(len(log["items"])))
    FAIL["on"] = False
    n = len(uploads)
    st = c.post(f"/api/lk/sources/{sid}/run", headers=ADMIN).json()["stats"]
    check("после починки лид уходит повторно", st.get("sent") == 1 and len(uploads) == n + 1, str(st))

    # --- скоринг
    r = c.post("/api/lk/sources", headers=ADMIN, json={
        "name": "Скоринг", "webhook": "https://q.bitrix24.ru/rest/1/zzzzzzzz", "mode": "score_field",
        "score_field": "UF_SCORE", "scored_flag_field": "UF_FLAG", "send_backlog": True})
    sid2 = r.json()["id"]
    c.post(f"/api/lk/sources/{sid2}/live?on=true", headers=ADMIN)
    LEADS[:] = [{"ID": "50", "UTM_CAMPAIGN": "713904508", "COMMENTS": "_ym_uid: 50", "UF_FLAG": "0", "UF_SCORE": "5"}]
    n = len(uploads)
    c.post(f"/api/lk/sources/{sid2}/run", headers=ADMIN)
    check("скоринг не посчитан -> не отправлять", len(uploads) == n)
    LEADS[0].update({"UF_FLAG": "1", "UF_SCORE": "5"})
    c.post(f"/api/lk/sources/{sid2}/run", headers=ADMIN)
    check("скоринг 5 -> отправка с Price", len(uploads) == n + 1 and "Price,Currency" in uploads[-1][1] and ",5,RUB" in uploads[-1][1], uploads[-1][1])
    c.post(f"/api/lk/sources/{sid2}/run", headers=ADMIN)
    check("тот же скоринг не дублируется", len(uploads) == n + 1)
    LEADS[0]["UF_SCORE"] = "8"
    c.post(f"/api/lk/sources/{sid2}/run", headers=ADMIN)
    check("скоринг изменился -> новая отправка", len(uploads) == n + 2 and ",8,RUB" in uploads[-1][1])
    LEADS[0]["UF_SCORE"] = "0"
    c.post(f"/api/lk/sources/{sid2}/run", headers=ADMIN)
    check("STOP: скоринг 0 отправляется", len(uploads) == n + 3 and ",0,RUB" in uploads[-1][1])

    # --- удаление пользователя гасит его источники
    r = c.post("/api/lk/sources", headers=U, json={"name": "u", "webhook": "https://u.bitrix24.ru/rest/1/aaaaaaaa"})
    check("клиент создаёт свой источник", r.status_code == 201)
    c.delete("/api/lk/users/client1", headers=ADMIN)
    with db.connect() as conn:
        n_en = conn.execute("SELECT COUNT(*) FROM b24_sources WHERE owner_username='client1' AND enabled=1").fetchone()[0]
    check("источники удалённого пользователя выключены", n_en == 0)
    check("удалённый пользователь не входит", c.get("/lk", headers=U).status_code == 401)

    # --- удаление источника чистит историю
    c.delete(f"/api/lk/sources/{sid}", headers=ADMIN)
    with db.connect() as conn:
        left = conn.execute("SELECT COUNT(*) FROM lead_state WHERE source_id=?", (sid,)).fetchone()[0]
    check("удаление источника чистит состояние", left == 0)

# ручной запуск за выбранные часы
_seen_hours = []
_orig_fetch = lk.fetch_recent_leads
async def _spy_fetch(webhook, hours, limit=None):
    _seen_hours.append(hours)
    return await _orig_fetch(webhook, hours, limit)
lk.fetch_recent_leads = _spy_fetch
_c = TestClient(main.app)
_src = _c.get("/api/lk/sources", headers=ADMIN).json()["items"]
if _src:
    _id = _src[0]["id"]
    _c.post(f"/api/lk/sources/{_id}/run", headers=ADMIN)
    _c.post(f"/api/lk/sources/{_id}/run?hours=72", headers=ADMIN)
    check("автозапуск берёт глубину источника, ручной - выбранные часы", _seen_hours[-2:] == [_src[0]["lookback_hours"], 72], _seen_hours)
    check("часы вне диапазона отклоняются", _c.post(f"/api/lk/sources/{_id}/run?hours=0", headers=ADMIN).status_code == 422)
lk.fetch_recent_leads = _orig_fetch

# разбор идентификаторов в разных форматах комментария
check("ym_uid без подчёркивания", lk.parse_ym_uid({"COMMENTS": "Name: a\nym_uid: 1793335081848925271\nip: 1.1.1.1"}) == "1793335081848925271")
check("_ym_uid в куках", lk.parse_ym_uid({"COMMENTS": "COOKIES: a=1; _ym_uid=1790000000111; b=2"}) == "1790000000111")
check("yandex_client_id", lk.parse_ym_uid({"COMMENTS": "yandex_client_id: 1780000000999"}) == "1780000000999")
check("Yandex ClientID", lk.parse_ym_uid({"COMMENTS": "Yandex ClientID: 1780000000888"}) == "1780000000888")
check("yclid из комментария", lk.parse_yclid({"COMMENTS": "Name: a\nyclid: 12561993399\nip: 1"}) == "12561993399")
check("yclid из SOURCE_DESCRIPTION приоритетнее", lk.parse_yclid({"SOURCE_DESCRIPTION": "https://x/?yclid=777", "COMMENTS": "yclid: 888"}) == "777")
check("без идентификатора", lk.identifier({"COMMENTS": "Name: a\ncity: b"}) is None)
_r = lk.missing_ident_reason({"COMMENTS": "Name: a\nsource: xn--h1apee0d.xn--b1ajdba5acbodeeeaj1qb.xn--p1ai"})
check("причина называет _ym_uid, yclid и сайт", "_ym_uid" in _r and "yclid" in _r and "юрист.союзюристовроссии.рф" in _r, _r)
check("причина для лида без комментария", "не формой сайта" in lk.missing_ident_reason({"COMMENTS": ""}))

# ───── метки в Б24: те же поля, что у offCon (флаг metrika_sent_score и Score), статус создаётся ─────
LEADS.clear(); uploads.clear(); UF["updates"].clear()
# как на реальном портале: userfield.list без подписей, подписи — в crm.lead.fields
UF["fields"] = [{"FIELD_NAME": "UF_CRM_555", "USER_TYPE_ID": "boolean"},
                {"FIELD_NAME": "UF_CRM_556", "USER_TYPE_ID": "double"},
                {"FIELD_NAME": "UF_CRM_557", "USER_TYPE_ID": "boolean"},
                {"FIELD_NAME": "UF_CRM_558", "USER_TYPE_ID": "string"}]
UF["meta"] = {"UF_CRM_555": {"formLabel": "UF_CRM_LEAD_METRIKA_SENT_score"}, "UF_CRM_556": {"formLabel": "Score"},
              "UF_CRM_557": {"formLabel": "UF_CRM_LEAD_METRIKA_SENT_score_region"}, "UF_CRM_558": {"formLabel": "Score_LOG"}}
_c = TestClient(main.app)
_cn = db.create_metrika_connection("admin", "M", encrypt_secret("tokM"))["id"]
r = _c.post("/api/lk/sources", headers=ADMIN, json={"name": "Метки", "webhook": "https://m.bitrix24.ru/rest/1/abcdefghijklmnop/", "lookback_hours": 24, "mode": "simple", "send_backlog": True})
check("источник создан, поля настроены", r.status_code == 201 and r.json()["marks"]["ready"], r.text)
_sid = r.json()["id"]
with db.connect() as _cx:
    _row = dict(_cx.execute("SELECT * FROM b24_sources WHERE id=?", (_sid,)).fetchone())
check("флаг найден по названию как у offCon", _row["mark_sent"] == "UF_CRM_555", _row["mark_sent"])
check("сумма = существующее поле Score", _row["mark_value"] == "UF_CRM_556", _row["mark_value"])
check("похожее поле score_region не перепутано", _row["mark_sent"] != "UF_CRM_557")
check("Score_LOG не занят под статус, статус создан отдельно", _row["mark_info"] == "UF_CRM_LK_METRIKA_INFO", _row["mark_info"])
check("создано только недостающее (1 поле)", [f["FIELD_NAME"] for f in UF["fields"] if f["FIELD_NAME"].startswith("UF_CRM_LK")] == ["UF_CRM_LK_METRIKA_INFO"])
_c.post("/api/lk/mappings", headers=ADMIN, json={"campaign_keys": "800100", "connection_id": _cn, "counter_id": 111})
_c.post(f"/api/lk/sources/{_sid}/live?on=true", headers=ADMIN)
LEADS += [{"ID": "901", "UTM_CAMPAIGN": "800100", "COMMENTS": "ym_uid: 5001"},
          {"ID": "902", "UTM_CAMPAIGN": "800100", "COMMENTS": "ym_uid: 5002", "UF_CRM_555": "1"},
          {"ID": "903", "UTM_CAMPAIGN": "800100", "COMMENTS": "Name: x"}]
st = _c.post(f"/api/lk/sources/{_sid}/run", headers=ADMIN).json()["stats"]
check("уже помеченный в Б24 лид не уходит повторно", st.get("sent") == 1 and st.get("already_sent") == 1 and st.get("no_id") == 1, str(st))
_upd = {u["id"]: u["fields"] for u in UF["updates"]}
check("после отправки: флаг и статус в лиде", _upd.get("901", {}).get("UF_CRM_555") == "1"
      and "Отправлено в Метрику" in _upd.get("901", {}).get("UF_CRM_LK_METRIKA_INFO", ""), str(_upd))
check("в простом режиме суммы нет — Score не трогаем", "UF_CRM_556" not in _upd.get("901", {}), str(_upd))
check("для лида без идентификатора записана причина, флаг не ставится", "нет _ym_uid" in _upd.get("903", {}).get("UF_CRM_LK_METRIKA_INFO", "") and "UF_CRM_555" not in _upd.get("903", {}), str(_upd))
check("помеченному лиду метки не перезаписываются", "902" not in _upd, str(_upd))
UF["updates"].clear()
LEADS[0]["UF_CRM_555"] = "1"
st = _c.post(f"/api/lk/sources/{_sid}/run", headers=ADMIN).json()["stats"]
check("повторный прогон: ни отправок, ни записей в Б24", not st.get("sent") and not UF["updates"], str(st) + str(UF["updates"]))
with db.connect() as _cx:
    _cx.execute("DELETE FROM lead_state WHERE source_id=?", (_sid,))
n_up = len(uploads)
st = _c.post(f"/api/lk/sources/{_sid}/run", headers=ADMIN).json()["stats"]
check("при пустой локальной базе метка в Б24 защищает от дубля", len(uploads) == n_up and st.get("already_sent") == 2, str(st))

# режим скоринга: флаг и Score ведёт offCon, ЛК пишет только статус и по нему не дублирует
UF["updates"].clear(); LEADS.clear(); uploads.clear()
r = _c.post("/api/lk/sources", headers=ADMIN, json={"name": "Скоринг", "webhook": "https://s.bitrix24.ru/rest/1/abcdefghijklmnop/", "lookback_hours": 24,
        "mode": "score_field", "score_field": "UF_CRM_556", "scored_flag_field": "UF_CRM_555", "send_backlog": True})
_sid3 = r.json()["id"]
_c.post(f"/api/lk/sources/{_sid3}/live?on=true", headers=ADMIN)
LEADS += [{"ID": "911", "UTM_CAMPAIGN": "800100", "COMMENTS": "ym_uid: 6001", "UF_CRM_555": "1", "UF_CRM_556": "1560"}]
st = _c.post(f"/api/lk/sources/{_sid3}/run", headers=ADMIN).json()["stats"]
_u = {u["id"]: u["fields"] for u in UF["updates"]}
check("скоринг: отправлено, пишется только статус", st.get("sent") == 1 and list(_u.get("911", {})) == ["UF_CRM_LK_METRIKA_INFO"], str(st) + str(_u))
check("в статусе сумма", _u["911"]["UF_CRM_LK_METRIKA_INFO"].endswith("сумма 1560"), str(_u))
LEADS[0]["UF_CRM_LK_METRIKA_INFO"] = _u["911"]["UF_CRM_LK_METRIKA_INFO"]
with db.connect() as _cx:
    _cx.execute("DELETE FROM lead_state WHERE source_id=?", (_sid3,))
n_up = len(uploads)
st = _c.post(f"/api/lk/sources/{_sid3}/run", headers=ADMIN).json()["stats"]
check("скоринг: по статусу в Б24 дубль не шлём", len(uploads) == n_up and st.get("already_sent") == 1, str(st))
LEADS[0]["UF_CRM_556"] = "1760"
st = _c.post(f"/api/lk/sources/{_sid3}/run", headers=ADMIN).json()["stats"]
check("скоринг: сумма выросла — отправляем снова", len(uploads) == n_up + 1, str(st))

# нет прав на создание полей
UF["fields"] = []; UF["meta"] = {}; UF["deny"] = True
r = _c.post("/api/lk/sources", headers=ADMIN, json={"name": "Без прав", "webhook": "https://n.bitrix24.ru/rest/1/abcdefghijklmnop/", "lookback_hours": 24, "mode": "simple"})
check("без прав на поля источник создаётся, статус объясняет", r.status_code == 201 and not r.json()["marks"]["ready"] and "прав" in r.json()["marks"]["status"], r.text)
_sid2 = r.json()["id"]
UF["deny"] = False
r = _c.post(f"/api/lk/sources/{_sid2}/marks/setup", headers=ADMIN)
check("повторная настройка полей работает", r.status_code == 200 and r.json()["ready"], r.text)
_c.post("/api/lk/users", headers=ADMIN, json={"username": "client9", "password": "longpassword9"})
check("чужой источник не настроить", _c.post(f"/api/lk/sources/{_sid2}/marks/setup", headers=auth("client9", "longpassword9")).status_code == 404)
srcs = {s["id"]: s for s in _c.get("/api/lk/sources", headers=ADMIN).json()["items"]}
check("в списке источников виден статус меток", srcs[_sid]["marks_ready"] is True and srcs[_sid]["marks_status"].startswith("готово"))

check("yclid из комментария, даже если SOURCE_DESCRIPTION занят регионом", lk.parse_yclid({"SOURCE_DESCRIPTION": "По региону из формы: Свердловская область", "COMMENTS": "Name: a\nyclid: 8090430001\nip: 1"}) == "8090430001")
check("yclid не берётся из чужого текста", lk.parse_yclid({"SOURCE_DESCRIPTION": "По региону", "COMMENTS": "Name: a"}) is None)

# чистые функции
check("campaign_key числовой", lk.campaign_key("713904508") == "713904508")
check("campaign_key с хвостом", lk.campaign_key("brand_713904508") == "713904508")
check("campaign_key пустой", lk.campaign_key("") == "")

print("\nALL PASS" if ok else "\nFAILURES")
sys.exit(0 if ok else 1)
