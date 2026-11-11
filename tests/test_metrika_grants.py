"""Доступ организации Директа к счётчику Метрики (2026-11-11): счётчик и
цель lead — в Метрике пулового Yandex ID, кампания — в организации porg-…."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient,
    _configure_test_env, _make_project, _seed_connection, _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")


def _fakes(monkeypatch):
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0
    FakeYandexMetrikaClient.grants = []


def test_launch_grants_counter_to_org(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "grants.db")
    _fakes(monkeypatch)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        r = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH, json={"count": 2, "connection_id": cid})
        job = _wait_for_job(client, AUTH, project["id"], r.json()["job_id"])
        assert job["created"] == 2
        logins = sorted(g[1] for g in FakeYandexMetrikaClient.grants)
        assert logins == ["porg-9101", "porg-9102"] and all(g[2] == "view" for g in FakeYandexMetrikaClient.grants)


def test_backfill_endpoint_for_existing_campaigns(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "backfill.db")
    _fakes(monkeypatch)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        db.record_bulk_campaign(project["id"], "pool-a", 9105, 1, connection_id=cid)
        r = client.post(f"/api/projects/{project['id']}/metrika-grants", auth=AUTH).json()
        assert r["ok"] == 1 and r["items"][0]["org_login"] == "porg-9105"
        assert FakeYandexMetrikaClient.grants and FakeYandexMetrikaClient.grants[0][1] == "porg-9105"
