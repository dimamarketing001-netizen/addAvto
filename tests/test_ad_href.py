"""Ссылка объявления — полная ссылка запуска с UTM (2026-11-12)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient,
    _configure_test_env, _make_project, _seed_connection, _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")
OZON = "https://ozon.ru/product/santa-1?hs=1&utm_campaign=vendor_{campaign_id}&utm_content={banner_id}_{keyword}&utm_source=yandex"


def test_ad_href_helper():
    assert main_module._ad_href({"launch_mode": "ozon", "ozon_url": OZON, "domain": "https://www.ozon.ru/product/santa-1"}) == OZON
    assert main_module._ad_href({"launch_mode": "ozon", "ozon_url": "ozon.ru/p?a=1", "domain": "d"}) == "https://ozon.ru/p?a=1"
    assert main_module._ad_href({"launch_mode": "direct", "ozon_url": "", "domain": "https://x.ru/?utm_source=y"}) == "https://x.ru/?utm_source=y"


def test_launch_uses_full_ozon_link(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "href.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0
    hrefs = []
    orig = FakeClickRUClient.create_text_ad

    async def spy(self, client_login, ad_group_id, title, text, href, **kw):
        hrefs.append(href)
        return await orig(self, client_login, ad_group_id, title, text, href, **kw)

    monkeypatch.setattr(FakeClickRUClient, "create_text_ad", spy)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON)
        r = client.post(f"/api/projects/{p['id']}/campaigns/bulk-create", auth=AUTH, json={"count": 1, "connection_id": _seed_connection()})
        assert _wait_for_job(client, AUTH, p["id"], r.json()["job_id"])["created"] == 1
    assert hrefs == [OZON]


def test_sync_href_updates_existing_ads(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "sync.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    sent = []

    async def direct_api(self, login, method_path, body, **kw):
        sent.append((method_path, body["method"], kw.get("api_version")))
        if body["method"] == "get":
            return {"result": {"Ads": [{"Id": 1, "TextAd": {"Href": "https://www.ozon.ru/product/santa-1"}},
                                       {"Id": 2, "TextAd": {"Href": OZON}}]}}
        assert body["params"]["Ads"] == [{"Id": 1, "TextAd": {"Href": OZON}}]
        return {"result": {"UpdateResults": [{"Id": 1}]}}

    monkeypatch.setattr(FakeClickRUClient, "direct_api", direct_api, raising=False)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON, campaign_mode="single_epk")
        db.record_bulk_campaign(p["id"], "pool-a", 9105, 77, connection_id=_seed_connection())
        r = client.post(f"/api/projects/{p['id']}/sync-href", auth=AUTH).json()
    assert r["updated"] == 1 and r["href"] == OZON
    assert ("ads", "update", "v501") in sent
