"""Статусы аккаунтов и дашборд проектов (2026-11-12)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from app.main import account_status
from test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient,
    _configure_test_env, _make_project, _seed_connection,
)

AUTH = ("test-admin", "test-password-long-enough")
ON = {"State": "ON", "Status": "ACCEPTED", "StatusPayment": "ALLOWED"}


def _ad(status, clar=""):
    return {"Status": status, "State": "ON", "StatusClarification": clar}


def test_account_status_rules():
    assert account_status({"state": "BAN"}, None, None)["code"] == "banned"
    assert account_status({"state": "PERMISSION_DENIED"}, ON, [_ad("ACCEPTED")])["code"] == "no_access"
    assert account_status({"state": "ACTIVE"}, ON, [_ad("ACCEPTED")])["code"] == "live"
    part = account_status({"state": "ACTIVE"}, ON, [_ad("ACCEPTED"), _ad("REJECTED", "Нарушение п. 3")])
    assert part["code"] == "partial" and "1 из 2" in part["label"] and "п. 3" in part["detail"]
    assert account_status({"state": "ACTIVE"}, {**ON, "Status": "MODERATION"}, [_ad("MODERATION")])["code"] == "review"
    rej = account_status({"state": "ACTIVE"}, ON, [_ad("REJECTED", "Нарушение")])
    assert rej["code"] == "rejected" and rej["detail"] == "Нарушение"
    assert account_status({"state": "ACTIVE"}, {**ON, "Status": "DRAFT", "State": "OFF"}, [_ad("DRAFT")])["code"] == "draft"
    assert account_status({"state": "ACTIVE"}, {**ON, "State": "SUSPENDED"}, [_ad("ACCEPTED")])["code"] == "paused"
    nm = account_status({"state": "ACTIVE"}, {**ON, "State": "OFF", "StatusPayment": "DISALLOWED",
                                               "StatusClarification": "Нет средств"}, [_ad("ACCEPTED")])
    assert nm["code"] == "no_money" and nm["detail"] == "Нет средств"
    assert account_status({"state": "ACTIVE"}, None, None)["code"] == "unknown"


def test_dashboard_aggregates(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "dash.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    main_module._STATUS_CACHE.clear()

    async def stat(self, fields, date_from, date_to, account_ids, v2=False):
        if v2:
            return [{"accountId": "101", "date": "2026-11-02", "conversions": "12"}]
        return [
            {"accountId": "101", "date": "2026-11-01", "impressions": "500", "clicks": "20", "loss": "300.5"},
            {"accountId": "101", "date": "2026-11-02", "impressions": "100", "clicks": "5", "loss": "100"},
            {"accountId": "102", "date": "2026-11-02", "impressions": "8", "clicks": "0", "loss": "0"},
            {"accountId": "999", "date": "2026-11-02", "impressions": "7", "clicks": "1", "loss": "1"},  # чужой
        ]

    async def account(self, account_id):
        return {"state": "BAN" if account_id == 102 else "ACTIVE", "serviceLogin": "porg-x"}

    async def direct_api(self, login, path, body, api_version="v5", accept_language=None):
        if path == "campaigns":
            return {"result": {"Campaigns": [ON]}}
        return {"result": {"Ads": [_ad("ACCEPTED")]}}

    for n, f in (("stat", stat), ("account", account), ("direct_api", direct_api)):
        monkeypatch.setattr(FakeClickRUClient, n, f, raising=False)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        cid = _seed_connection()
        db.record_bulk_campaign(p["id"], "pool-a", 101, 7001, connection_id=cid, payer_id=1)
        db.record_bulk_campaign(p["id"], "pool-b", 102, 7002, connection_id=cid, payer_id=1)
        r = client.get("/api/ads-dashboard", auth=AUTH, params={
            "date_from": "2026-11-01", "date_to": "2026-11-03", "project_ids": str(p["id"])}).json()
        st = client.get(f"/api/projects/{p['id']}/statuses", auth=AUTH).json()
        other = client.get("/api/ads-dashboard", auth=AUTH, params={
            "date_from": "2026-11-01", "date_to": "2026-11-03", "project_ids": "9999"}).json()
    s = r["summary"]
    assert s["accounts"] == 2 and s["with_impressions"] == 1 and s["with_clicks"] == 1 and s["with_leads"] == 1
    assert s["by_status"] == {"banned": 1, "live": 1}
    assert r["totals"]["impressions"] == 608 and r["totals"]["clicks"] == 25 and r["totals"]["leads"] == 12
    assert r["totals"]["spend"] == 400.5 and r["totals"]["cpl"] == round(400.5 / 12, 2)
    a = r["items"][0]
    assert a["account_id"] == 101 and a["series"]["clicks"] == [20, 5, 0] and a["series"]["leads"] == [0, 12, 0]
    assert r["daily"]["labels"] == ["2026-11-01", "2026-11-02", "2026-11-03"] and r["daily"]["impressions"] == [500, 108, 0]
    assert {x["account_id"]: x["code"] for x in st["items"]} == {101: "live", 102: "banned"}
    assert other["summary"]["accounts"] == 0
