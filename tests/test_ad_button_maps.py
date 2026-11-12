"""Кнопка объявления из списка Директа и выключенные Яндекс Карты в ЕПК
(2026-11-12)."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from app.click_client import ClickRUClient, ClickRUError
from test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient,
    _configure_test_env, _make_project, _seed_connection, _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")


def _capture(monkeypatch):
    calls = []

    async def fake_direct_api(self, client_login, method_path, body, api_version="v5", accept_language=None):
        calls.append((method_path, api_version, body))
        return {"result": {"AddResults": [{"Id": 777}]}}

    monkeypatch.setattr(ClickRUClient, "direct_api", fake_direct_api)
    return calls


def test_epk_turns_off_maps_and_ad_gets_button(monkeypatch):
    calls = _capture(monkeypatch)
    c = ClickRUClient(token="t")

    async def run():
        await c.create_draft_unified_campaign("porg-1", "Санта", "2026-11-12", target_drr=30, goal_id=5)
        await c.create_text_ad("porg-1", 1, "Заголовок", "Текст", "https://x.ru/?utm=1", button_action="BUY")
        await c.create_text_ad("porg-1", 1, "Заголовок", "Текст", "https://x.ru/")

    asyncio.run(run())
    camp, ad_btn, ad_plain = calls
    search = camp[2]["params"]["Campaigns"][0]["UnifiedCampaign"]["BiddingStrategy"]["Search"]
    assert search["PlacementTypes"] == {"Maps": "NO"}
    assert ad_btn[2]["params"]["Ads"][0]["TextAd"]["ButtonExtension"] == {"Action": "BUY", "Href": "https://x.ru/?utm=1"}
    assert "ButtonExtension" not in ad_plain[2]["params"]["Ads"][0]["TextAd"]


def _setup(monkeypatch, tmp_path, name):
    _configure_test_env(monkeypatch, tmp_path / name)
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0


def test_launch_passes_button_and_falls_back_without_it(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "btn.db")
    seen = []

    async def create_text_ad(self, client_login, ad_group_id, title, text, href, **kwargs):
        seen.append(kwargs.get("button_action"))
        if kwargs.get("button_action"):
            raise ClickRUError("Кнопка недоступна")
        return 1

    monkeypatch.setattr(FakeClickRUClient, "create_text_ad", create_text_ad)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        cid = _seed_connection()
        bad = client.post(f"/api/projects/{p['id']}/campaigns/bulk-create", auth=AUTH,
                          json={"count": 1, "connection_id": cid, "button_action": "CUSTOM_TEXT"})
        assert bad.status_code == 422
        r = client.post(f"/api/projects/{p['id']}/campaigns/bulk-create", auth=AUTH,
                        json={"count": 1, "connection_id": cid, "button_action": "ORDER"})
        job = _wait_for_job(client, AUTH, p["id"], r.json()["job_id"])
        items = client.get("/api/direct/button-actions", auth=AUTH).json()["items"]
    assert job["created"] == 1
    assert seen[:2] == ["ORDER", None]
    assert "без кнопки" in job["items"][0]["note"]
    assert {"value": "ORDER", "label": "Заказать"} in items
    assert all(i["value"] != "CUSTOM_TEXT" for i in items)


def test_apply_ad_settings_to_existing(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "apply.db")
    calls = []

    async def direct_api(self, client_login, method_path, body, api_version="v5", accept_language=None):
        calls.append((method_path, body["method"], api_version, body["params"]))
        if body["method"] == "get":
            return {"result": {"Ads": [
                {"Id": 1, "TextAd": {"Href": "https://a.ru/?u=1", "ButtonExtension": None}},
                {"Id": 2, "TextAd": {"Href": "https://a.ru/?u=1", "ButtonExtension": {"Action": "BUY"}}},
            ]}}
        return {"result": {"UpdateResults": [{"Id": 1}]}}

    async def resolve_login(client, account_id):
        return "porg-x"

    monkeypatch.setattr(FakeClickRUClient, "direct_api", direct_api, raising=False)
    monkeypatch.setattr(main_module, "_resolve_client_login", resolve_login)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        cid = _seed_connection()
        db.record_bulk_campaign(p["id"], "pool-a", 9105, 77, connection_id=cid, payer_id=1)
        assert client.post(f"/api/projects/{p['id']}/apply-ad-settings", auth=AUTH, json={}).status_code == 422
        r = client.post(f"/api/projects/{p['id']}/apply-ad-settings", auth=AUTH,
                        json={"button_action": "BUY", "maps_off": True}).json()
    # проект не ЕПК → Карты не трогаем; кнопку ставим только там, где её нет
    assert r["maps_off"] is False and r["items"][0]["ads_updated"] == 1
    upd = [c for c in calls if c[1] == "update"]
    assert len(upd) == 1 and upd[0][3]["Ads"] == [
        {"Id": 1, "TextAd": {"ButtonExtension": {"Action": "BUY", "Href": "https://a.ru/?u=1"}}}]


def test_strategy_for_update_keeps_settings():
    got = {"BiddingStrategyType": "AVERAGE_CRR", "PlacementTypes": {"Maps": "YES"},
           "AverageCrr": {"Crr": 30, "GoalId": 5, "WeeklySpendLimit": None, "BudgetType": "WEEKLY_BUDGET",
                          "ExplorationBudget": {"MinimumExplorationBudget": 1}}}
    assert main_module._strategy_for_update(got) == {
        "BiddingStrategyType": "AVERAGE_CRR", "AverageCrr": {"Crr": 30, "GoalId": 5, "BudgetType": "WEEKLY_BUDGET"}}
