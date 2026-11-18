from __future__ import annotations

from fastapi.testclient import TestClient

from app import crm_stats, lead_integrations as li, site_bundles as sb
from app import main as main_module
from tests.test_bulk_campaign_create import _configure_test_env

AUTH = ("test-admin", "test-password-long-enough")


def test_bundles_loaded_and_rendered():
    ids = {b["id"] for b in sb.public_list()}
    assert {"mu_amnesty", "mu_pension"} <= ids
    cfg = {"brand": "Ромашка", "legal_name": "ООО «Ромашка»", "inn": "1234567890", "ogrn": "1111111111111",
           "phone": "+7 (343) 111-22-33", "email": "info@romashka.ru"}
    html = sb.render_file("mu_amnesty", "index.html", cfg, "xn--80aaoto6a.ru", [555])[0].decode()
    assert "Мой юрист" not in html and "6658577091" not in html and "505-67-97" not in html
    assert "ООО «Ромашка»" in html and "1234567890" in html and "+7 (343) 111-22-33" in html and "tel:83431112233" in html
    assert "111540304" not in html and "__YM_IDS=[555]" in html and 'ym(555,"init"' in html
    assert "/кредитная-амнистия/" not in html and 'href="/css/style.css' in html
    js = sb.render_file("mu_amnesty", "js/app.js", cfg, "romashka.ru", [555])[0].decode()
    assert "submit.php" not in js and "'/lead'" in js and "/sps.html" in js
    assert "ym(111540304" not in js and "ym(109281290" not in js and "window.__ymAll('getClientID'" in js
    assert "78005056797" not in js and "73431112233" in js
    pjs = sb.render_file("mu_pension", "js/app.js", {}, "x.ru")[0].decode()
    assert "YM_ID = (window.__YM_IDS||[])[0]||0;" in pjs and "window.__ymAll('reachGoal'" in pjs and "'/lead'" in pjs
    # пустой ИНН в настройках = пусто, а не ИНН МЮ
    assert "6658577091" not in sb.render_file("mu_pension", "index.html", {"inn": ""}, "x.ru")[0].decode()
    assert sb.resolve("mu_amnesty", "/") == "index.html" and sb.resolve("mu_amnesty", "/sps.html") == "sps.html"
    assert sb.resolve("mu_amnesty", "/manifest.json") is None and sb.resolve("mu_amnesty", "/../main.py") is None
    prev = sb.preview_html("mu_amnesty", cfg, "x.ru")
    assert "data:image/webp;base64," in prev and '<script src="/js' not in prev and "<style>" in prev


def test_normalize_formats():
    a = sb.normalize("mu_amnesty", {"name": "Иван", "phone": "+7 900 123-45-67", "score": 75, "source": "https://x.ru/?utm_campaign=1",
                                    "utm": {"utm_campaign": "rsya_1"}, "yandex_client_id": "123",
                                    "answers": {"debt": "500-1500", "court": "yes", "regionName": "Свердловская область", "city": "Екатеринбург"}})
    assert a["answers"][0] == {"q": "Какая у вас общая сумма долга?", "a": "500 000 – 1 500 000 ₽"}
    assert a["region"] == "Свердловская область" and a["city"] == "Екатеринбург" and a["page"].startswith("https://x.ru")
    p = sb.normalize("mu_pension", {"name": "Анна", "phone": "89001234567", "region": "Челябинская область", "city": "Миасс",
                                    "summary": ["Возраст: 70–79", "Трудовой стаж: Больше 35 лет"], "quality": "good", "score": 5,
                                    "method": "Позвонить", "website": "spam"})
    assert p["answers"][1] == {"q": "Трудовой стаж", "a": "Больше 35 лет"} and p["spam"] is True and p["quality"] == "good"


def test_integration_logic_mu_ydl():
    lead = {**li.SAMPLE_LEAD, "region": "Челябинская область", "city": "Миасс", "phone": "8 (900) 123-45-67",
            "utm": {"utm_campaign": "rsya_1", "utm_product": "PENSIA"}}
    mu = {"kind": "mu", "settings": li.merged("mu", {"title_prefix": "БФЛ", "lead_type_id": "70"})}
    f = li.build(mu, lead, "x.ru")
    assert f["TITLE"] == "БФЛ | Иван" and f["SOURCE_ID"] == "UC_11UXNN" and f["UF_CRM_1790230122139"] == "70"
    assert f["UF_CRM_1779024295"] == "60" and f["UF_CRM_1730379820755"] == "Миасс"
    assert f["SOURCE_DESCRIPTION"] == "По региону из формы: Челябинская область"
    assert "═══ ОТВЕТЫ ═══" in f["COMMENTS"] and f["PHONE"][0]["VALUE"] == "79001234567" and f["UTM_CAMPAIGN"] == "rsya_1"
    f2 = li.build(mu, {**lead, "region": "Свердловская область", "city": "Екатеринбург"}, "x.ru")
    assert f2["UF_CRM_1779024295"] == "58"
    ydl = {"kind": "ydl", "settings": li.merged("ydl", {"b24_source": "132"})}
    srcs = [("57", "Волгоград"), ("130", "Владивосток 2"), ("129", "Владивосток 1"), ("69", "Ростов-на-Дону")]
    y = li.build(ydl, {**lead, "city": "Ростов на Дону"}, "x.ru", srcs)
    assert y["SOURCE_ID"] == "69" and y["TITLE"] == "Пенсионный юрист | Ростов на Дону | Иван 7900***4567"
    assert y["UF_CRM_1662639727"] == "22747" and y["UF_CRM_1620728999067"] == "651" and y["UF_CRM_1660713522"] == "1"
    assert y["UF_UTM_PRODUCT"] == "PENSIA" and y["UF_CLIENT_ID"] == "1763400000123456789"
    assert y["UF_CRM_1651049909485"].startswith("Какая у вас общая сумма долга?:\n- ")
    assert li.build(ydl, {**lead, "city": "Владивосток"}, "x.ru", srcs)["SOURCE_ID"] == "129"
    assert li.build(ydl, {**lead, "city": "Тверь", "region": ""}, "x.ru", srcs)["SOURCE_ID"] == "132"


def test_bundle_site_with_integration(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bundles.db")
    pushed = []

    async def fake_call(webhook, method, params):
        pushed.append((webhook, method, params))
        if method == "crm.status.list":
            return {"result": [{"STATUS_ID": "57", "NAME": "Волгоград"}]}
        if method == "crm.lead.fields":
            return {"result": {"UF_CRM_1790230122139": {"type": "enumeration", "items": [{"ID": "70", "VALUE": "БФЛ"}]}}}
        return {"result": 4242}

    monkeypatch.setattr(crm_stats, "_call", fake_call)
    monkeypatch.setattr(crm_stats, "_source", lambda sid, owner=None: {"id": sid})
    monkeypatch.setattr(crm_stats, "_webhook", lambda src: f"https://b24.example/{src['id']}")
    with TestClient(main_module.app) as client:
        lst = client.get("/api/lead-integrations", auth=AUTH).json()
        assert set(lst["kinds"]) == {"mu", "ydl", "b24"}
        integ = client.post("/api/lead-integrations", auth=AUTH, json={
            "name": "МЮ — БФЛ", "kind": "mu", "source_id": 2, "settings": {"title_prefix": "БФЛ", "lead_type_id": "70"}}).json()
        assert integ["settings"]["fields"]["dept"] == "UF_CRM_1779024295"
        meta = client.get(f"/api/lead-integrations/meta?source_id=2&kind=mu", auth=AUTH).json()
        assert meta["lead_types"] == [{"id": "70", "name": "БФЛ"}]
        pv = client.post(f"/api/lead-integrations/{integ['id']}/preview", auth=AUTH, json={}).json()
        assert pv["fields"]["TITLE"] == "БФЛ | Иван" and not [p for p in pushed if p[1] == "crm.lead.add"]
        assert client.post("/api/site-templates", auth=AUTH, json={"name": "x", "kind": "bundle", "config": {"bundle": "nope"}}).status_code == 422
        tpl = client.post("/api/site-templates", auth=AUTH, json={"name": "МЮ амнистия", "kind": "bundle", "config": {
            "bundle": "mu_amnesty", "integration_id": integ["id"], "brand": "Мой юрист", "phone": "8 (800) 505-67-97"}}).json()
        assert client.delete(f"/api/lead-integrations/{integ['id']}", auth=AUTH).status_code == 409
        prev = client.post("/api/site-templates/preview", auth=AUTH, json={"kind": "bundle", "config": tpl["config"]}).json()["html"]
        assert "Кредитная" in prev and "data:image" in prev
        client.put("/api/sites", auth=AUTH, json={"domain": "mu-site.ru", "template_id": tpl["id"]})
        h = {"host": "mu-site.ru"}
        page = client.get("/", headers=h)
        assert page.status_code == 200 and "__ymAll" in page.text and "111540304" not in page.text
        assert client.get("/css/style.css?v=3", headers=h).headers["content-type"].startswith("text/css")
        assert client.get("/img/lawyer.webp", headers=h).headers["content-type"] == "image/webp"
        assert client.get("/sps.html", headers=h).status_code == 200
        assert client.get("/manifest.json", headers=h).status_code == 404
        assert client.get("/api/projects", headers=h, auth=AUTH).status_code == 404
        r = client.post("/lead", headers={**h, "x-real-ip": "5.5.5.5"}, json={
            "name": "Пётр", "phone": "+7 (912) 000-11-22", "answers": {"debt": "300-500", "regionName": "Свердловская область",
                                                                       "city": "Екатеринбург", "name": "Пётр", "phone": "+7 (912) 000-11-22"},
            "utm": {"utm_campaign": "rsya_9"}, "source": "https://mu-site.ru/?utm_campaign=rsya_9"})
        assert r.status_code == 200
        wh, method, params = pushed[-1]
        assert wh == "https://b24.example/2" and method == "crm.lead.add"
        f = params["fields"]
        assert f["TITLE"] == "БФЛ | Пётр" and f["UF_CRM_1779024295"] == "58" and "300 000 – 500 000 ₽" in f["COMMENTS"]
        lead = client.get("/api/sites/leads", auth=AUTH).json()["items"][0]
        assert lead["b24_status"] == "sent" and lead["b24_lead_id"] == 4242
        assert client.post(f"/api/sites/leads/{lead['id']}/resend", auth=AUTH).status_code == 409
