from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.schemas import ProjectIn
from tests.test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient, _configure_test_env, _make_project,
    _seed_connection, _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")


def test_interest_ids_validation():
    ok = ProjectIn(name="xx", domain="example.ru", interest_ids=[302499001225, 302499001225, 102499001225])
    assert ok.interest_ids == [302499001225, 102499001225]
    with pytest.raises(ValueError):
        ProjectIn(name="xx", domain="example.ru", interest_ids=[2499001225])


class InterestsClient(FakeClickRUClient):
    calls: list = []

    async def add_audience_interests(self, client_login, ad_group_id, name, interest_ids, api_version="v5"):
        InterestsClient.calls.append((ad_group_id, list(interest_ids), api_version))
        return 555


def test_launch_attaches_interests(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "int.db")
    monkeypatch.setattr(main_module, "ClickRUClient", InterestsClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0
    InterestsClient.calls = []
    with TestClient(main_module.app) as client:
        d = client.get("/api/audience-interests", auth=AUTH).json()
        assert len(d["items"]) == 441 and any(x["name"] == "Юридическая поддержка" for x in d["items"])
        project = _make_project(client, AUTH, interest_ids=[302499001225])
        assert project["interest_ids"] == [302499001225]
        conn = _seed_connection()
        r = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH, json={"count": 2, "connection_id": conn})
        body = _wait_for_job(client, AUTH, project["id"], r.json()["job_id"])
    assert body["created"] == 2
    assert [c[1] for c in InterestsClient.calls] == [[302499001225], [302499001225]]


def test_launch_without_interests_skips(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "int2.db")
    monkeypatch.setattr(main_module, "ClickRUClient", InterestsClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    InterestsClient.calls = []
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        conn = _seed_connection()
        r = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH, json={"count": 1, "connection_id": conn})
        body = _wait_for_job(client, AUTH, project["id"], r.json()["job_id"])
    assert body["created"] == 1 and InterestsClient.calls == []
