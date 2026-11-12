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


def _setup(monkeypatch, tmp_path, name, *, marking=True, topup_status="SUCCESS", schema_create_error=None):
    _configure_test_env(monkeypatch, tmp_path / name)
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    monkeypatch.setattr(main_module, "TOPUP_STATUS_WAIT_SECONDS", 5)
    FakeParsYaPoolClient._counter = 0
    calls = {"money": [], "moderate": [], "resume": [], "schema_create": []}

    async def add_money(self, account_id, amount_rub, payer_id=None):
        calls["money"].append((account_id, amount_rub, payer_id))
        return 555

    async def add_money_status(self, task_id):
        return topup_status

    async def marking_schema(self, account_id):
        return {"schemas": [{"schemeId": 1, "schemaType": "END_ADVERTISER"}] if marking else []}

    async def create_marking_schema(self, account_id, payer_id, schema_type="END_ADVERTISER", account_schema_type="MAIN"):
        if schema_create_error:
            raise main_module.ClickRUError(schema_create_error, 422)
        calls["schema_create"].append((account_id, payer_id, schema_type))
        return 4242

    async def moderate_campaign_ads(self, client_login, campaign_id, api_version="v5"):
        calls["moderate"].append((client_login, campaign_id))
        return 1

    async def resume_campaign(self, client_login, campaign_id):
        calls["resume"].append(campaign_id)

    for n, f in (("add_money", add_money), ("add_money_status", add_money_status), ("marking_schema", marking_schema),
                 ("create_marking_schema", create_marking_schema),
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


def test_missing_marking_is_created_then_funded(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "mkcreate.db", marking=False)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        job = _launch(client, p["id"], _seed_connection(), topup_amount_rub=1220, auto_start=True)
    assert job["created"] == 2
    assert [c[1:] for c in calls["schema_create"]] == [(1480787, "END_ADVERTISER")] * 2
    assert len(calls["money"]) == 2 and len(calls["resume"]) == 2
    it = job["items"][0]
    assert it["started"] is True and it["topup_status"] == "SUCCESS"
    assert "создана схема" in it["note"] and "4242" in it["note"]


def test_marking_failure_skips_topup_and_start(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "nomark.db", marking=False, schema_create_error="bad schema")
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        job = _launch(client, p["id"], _seed_connection(), topup_amount_rub=1220, auto_start=True)
    assert job["created"] == 2  # кампании созданы, несмотря на отказ
    it = job["items"][0]
    assert it["started"] is False and it["topup_status"] == "SKIPPED"
    assert "маркировка" in it["note"] and "bad schema" in it["note"]
    assert calls["money"] == [] and calls["moderate"] == []


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


def test_clickru_retries_429(monkeypatch):
    import asyncio
    import httpx
    from app import click_client

    hits = []

    def handler(request):
        hits.append(request.url.path)
        if len(hits) < 3:
            return httpx.Response(429, json={"message": "limit"})
        return httpx.Response(200, json={"response": {"task_id": 9}})

    real = httpx.AsyncClient
    monkeypatch.setattr(click_client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(click_client, "CLICKRU_429_BACKOFF", (0.0, 0.0, 0.0))
    c = click_client.ClickRUClient(token="t")
    assert asyncio.run(c.add_money(1, 1220, 5)) == 9
    assert len(hits) == 3
