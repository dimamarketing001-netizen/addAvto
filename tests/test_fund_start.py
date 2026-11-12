"""Пополнение с Click.ru и автозапуск после создания (2026-11-12)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient,
    _configure_test_env, _make_project, _seed_connection, _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")


def _setup(monkeypatch, tmp_path, name, *, marking=True, topup_status="SUCCESS"):
    _configure_test_env(monkeypatch, tmp_path / name)
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    monkeypatch.setattr(main_module, "TOPUP_STATUS_WAIT_SECONDS", 5)
    FakeParsYaPoolClient._counter = 0
    calls = {"money": [], "moderate": [], "resume": []}

    async def add_money(self, account_id, amount_rub, payer_id=None):
        calls["money"].append((account_id, amount_rub, payer_id))
        return 555

    async def add_money_status(self, task_id):
        return topup_status

    async def marking_schema(self, account_id):
        return {"configured": marking}

    async def moderate_campaign_ads(self, client_login, campaign_id, api_version="v5"):
        calls["moderate"].append((client_login, campaign_id))
        return 1

    async def resume_campaign(self, client_login, campaign_id):
        calls["resume"].append(campaign_id)

    for n, f in (("add_money", add_money), ("add_money_status", add_money_status), ("marking_schema", marking_schema),
                 ("moderate_campaign_ads", moderate_campaign_ads), ("resume_campaign", resume_campaign)):
        monkeypatch.setattr(FakeClickRUClient, n, f, raising=False)
    return calls


def _launch(client, project_id, cid, **extra):
    r = client.post(f"/api/projects/{project_id}/campaigns/bulk-create", auth=AUTH,
                    json={"count": 2, "connection_id": cid, "payer_id": 1480787, **extra})
    return _wait_for_job(client, AUTH, project_id, r.json()["job_id"])


def test_launch_tops_up_and_starts(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "fs.db")
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        job = _launch(client, p["id"], _seed_connection(), topup_amount_rub=1220, auto_start=True)
    assert job["created"] == 2
    assert [c[1:] for c in calls["money"]] == [(1220, 1480787), (1220, 1480787)]
    for it in job["items"]:
        assert it["topup_rub"] == 1220 and it["topup_status"] == "SUCCESS" and it["started"] is True
        assert "Пополнено на 1220" in it["note"] and "Запущено" in it["note"]
    assert len(calls["moderate"]) == 2 and len(calls["resume"]) == 2


def test_default_launch_spends_nothing(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "none.db")
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        job = _launch(client, p["id"], _seed_connection())
    assert job["created"] == 2 and calls["money"] == [] and calls["moderate"] == []
    assert all(not it["started"] and it["topup_status"] is None for it in job["items"])


def test_no_marking_means_not_started_but_created(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "nomark.db", marking=False, topup_status="ERROR")
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        job = _launch(client, p["id"], _seed_connection(), topup_amount_rub=1220, auto_start=True)
    assert job["created"] == 2  # кампании созданы, несмотря на отказ
    it = job["items"][0]
    assert it["started"] is False and it["topup_status"] == "ERROR"
    assert "не прошло" in it["note"] and "маркировка" in it["note"]
    assert calls["moderate"] == []


def test_fund_and_start_existing(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "exist.db")
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        cid = _seed_connection()
        db.record_bulk_campaign(p["id"], "pool-a", 9105, 77, connection_id=cid, payer_id=1480787)
        assert client.post(f"/api/projects/{p['id']}/fund-and-start", auth=AUTH, json={}).status_code == 422
        assert client.post(f"/api/projects/{p['id']}/fund-and-start", auth=AUTH, json={"amount_rub": 500000}).status_code == 422
        r = client.post(f"/api/projects/{p['id']}/fund-and-start", auth=AUTH, json={"amount_rub": 1220, "start": True}).json()
        assert r["funded"] == 1 and r["started"] == 1 and r["total"] == 1
        assert calls["money"] == [(9105, 1220.0, 1480787)] and calls["resume"] == [77]
        h = client.get(f"/api/projects/{p['id']}/history", auth=AUTH).json()
        assert any(e["kind"] == "fund_start" for e in h["events"])
