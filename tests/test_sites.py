from __future__ import annotations

from fastapi.testclient import TestClient

from app import crm_stats, sites
from app import main as main_module
from tests.test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient, _configure_test_env, _make_project,
    _seed_connection, _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")


def test_templates_sites_and_host_routing(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "sites.db")
    pushed = []

    async def fake_call(webhook, method, params):
        pushed.append((method, params))
        return {"result": 777}

    monkeypatch.setattr(crm_stats, "_call", fake_call)
    monkeypatch.setattr(crm_stats, "_source", lambda sid, owner=None: {"id": sid})
    monkeypatch.setattr(crm_stats, "_webhook", lambda src: "https://b24.example/rest/1/x")
    with TestClient(main_module.app) as client:
        t = client.post("/api/site-templates", auth=AUTH, json={"name": "Квиз БФЛ", "kind": "quiz", "config": {
            "title": "Спишем долги <законно>", "questions": [{"q": "Сумма долга?", "options": ["до 300 тыс", "больше"]}]}}).json()
        assert t["kind"] == "quiz"
        prev = client.post("/api/site-templates/preview", auth=AUTH, json={"kind": "quiz", "config": t["config"]}).json()["html"]
        assert "Спишем долги &lt;законно&gt;" in prev and "Сумма долга?" in prev
        project = _make_project(client, AUTH)
        crm_stats.set_project_source_id(project["id"], 5)
        s = client.put("/api/sites", auth=AUTH, json={"domain": "https://www.Bfl-Site.ru/", "template_id": t["id"],
                                                      "project_id": project["id"]}).json()
        assert s["domain"] == "bfl-site.ru"
        assert client.get("/api/sites", auth=AUTH).json()["items"][0]["template_name"] == "Квиз БФЛ"

        page = client.get("/", headers={"host": "www.bfl-site.ru"})
        assert page.status_code == 200 and "Сумма долга?" in page.text
        # на домене сайта API и ЛК не видны
        assert client.get("/api/projects", headers={"host": "bfl-site.ru"}, auth=AUTH).status_code == 404
        assert client.get("/lk", headers={"host": "bfl-site.ru"}).status_code == 404
        # заявка
        bad = client.post("/lead", headers={"host": "bfl-site.ru"}, json={"phone": "123"})
        assert bad.status_code == 422
        ok = client.post("/lead", headers={"host": "bfl-site.ru", "x-real-ip": "1.2.3.4"}, json={
            "name": "Иван", "phone": "+7 (900) 123-45-67", "answers": [{"q": "Сумма долга?", "a": "больше"}],
            "utm": {"utm_campaign": "bfl_716217728", "utm_source": "yandex"}})
        assert ok.status_code == 200 and ok.json()["ok"]
        method, params = pushed[-1]
        assert method == "crm.lead.add"
        f = params["fields"]
        assert f["PHONE"][0]["VALUE"] == "+79001234567" and f["UTM_CAMPAIGN"] == "bfl_716217728"
        assert "Сумма долга?: больше" in f["COMMENTS"]
        leads = client.get("/api/sites/leads", auth=AUTH).json()["items"]
        assert leads[0]["b24_status"] == "sent" and leads[0]["b24_lead_id"] == 777
        # обычный хост работает как раньше
        assert client.get("/api/sites", auth=AUTH).status_code == 200
        # шаблон занят сайтом — не удаляется
        assert client.delete(f"/api/site-templates/{t['id']}", auth=AUTH).status_code == 409


def test_lead_without_source_is_kept(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "sites2.db")
    with TestClient(main_module.app) as client:
        sites.upsert_site("free-site.ru")
        r = client.post("/lead", headers={"host": "free-site.ru"}, json={"phone": "89001112233"})
        assert r.status_code == 200
        lead = client.get("/api/sites/leads", auth=AUTH).json()["items"][0]
        assert lead["b24_status"] == "skipped" and lead["phone"] == "89001112233"
        assert client.get("/", headers={"host": "free-site.ru"}).text.count("Сайт скоро откроется") == 1


class DomainsClient(FakeClickRUClient):
    ads: list = []
    sitelinks: list = []

    async def create_text_ad(self, client_login, ad_group_id, title, text, href, **kwargs):
        DomainsClient.ads.append(href)
        return 1

    async def create_sitelinks_set(self, client_login, sitelinks):
        DomainsClient.sitelinks.append([s["Href"] for s in sitelinks])
        return 2


def test_multi_domain_launch(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "md.db")
    monkeypatch.setattr(main_module, "ClickRUClient", DomainsClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0
    DomainsClient.ads, DomainsClient.sitelinks = [], []
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, domain="https://example.ru/?utm_source=yandex&utm_campaign={campaign_id}",
                                sitelinks_pool=[{"title": "Цены", "href": "https://example.ru/#price", "description": ""}])
        sites.upsert_site("one-site.ru")
        conn = _seed_connection()
        bad = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH,
                          json={"count": 2, "connection_id": conn, "domains": ["one-site.ru"]})
        assert bad.status_code == 422
        r = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH,
                        json={"count": 2, "connection_id": conn, "domains": ["one-site.ru", "www.two-site.ru"]})
        assert r.status_code == 202, r.text
        body = _wait_for_job(client, AUTH, project["id"], r.json()["job_id"])
        assert body["created"] == 2
        rows = sorted(main_module.db.list_bulk_campaigns(project["id"]), key=lambda x: x["id"])
    assert [x["site_domain"] for x in rows] == ["one-site.ru", "two-site.ru"]
    base = project["domain"]
    assert "utm_source=yandex" in base
    assert DomainsClient.ads == [base.replace("example.ru", "one-site.ru"), base.replace("example.ru", "two-site.ru")]
    assert DomainsClient.sitelinks[0][0].startswith("https://one-site.ru/") and "#price" in DomainsClient.sitelinks[0][0]
    assert sites.site_by_domain("one-site.ru")["metrika_ids"]  # счётчик аккаунта встроен в сайт
