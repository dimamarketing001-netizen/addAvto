from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module
from app.metrika_client import YandexMetrikaClient


def _configure_test_env(monkeypatch, database_path):
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "")
    monkeypatch.setenv("PARSYA_POOL_API_TOKEN", "test-pool-token")


class FakePoolClient:
    configured = True

    async def get_account(self, login):
        assert login == "pavel3871@mcdonalds001.shop"
        return {
            "login": login,
            "metrika_access_token": "fake-pool-access-token",
            "metrika_refresh_token": "fake-pool-refresh-token",
        }

    async def list_accounts(self):
        return [{"login": "pavel3871@mcdonalds001.shop", "has_metrika_token": True}]


async def _fake_list_counters(self):
    assert self.access_token == "fake-pool-access-token"
    return [{"id": 555, "name": "Test site", "site": "example.ru"}]


async def _fake_list_goals(self, counter_id):
    assert counter_id == 555
    return []


async def _fake_create_goal(self, counter_id, default_price=None):
    return {"id": 777, "name": "lead"}


def test_setup_project_metrika_with_pool_login(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "pool-test.db")
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakePoolClient)
    monkeypatch.setattr(YandexMetrikaClient, "list_counters", _fake_list_counters)
    monkeypatch.setattr(YandexMetrikaClient, "list_goals", _fake_list_goals)
    monkeypatch.setattr(YandexMetrikaClient, "create_goal", _fake_create_goal)

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        created = client.post("/api/projects", auth=auth, json={
            "name": "Pool-sourced project",
            "domain": "https://example.ru",
            "account_ids": [],
            "region_ids": [],
        })
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]

        setup = client.post(
            f"/api/projects/{project_id}/metrika/setup",
            auth=auth,
            json={"pool_login": "pavel3871@mcdonalds001.shop"},
        )
        assert setup.status_code == 200, setup.text
        body = setup.json()
        assert body["counter_id"] == 555
        assert body["goal_id"] == 777

        project = client.get("/api/projects", auth=auth).json()["items"][0]
        assert project["metrika_pool_login"] == "pavel3871@mcdonalds001.shop"


def test_list_parsya_pool_accounts(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "pool-list-test.db")
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakePoolClient)

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.get("/api/parsya-pool/accounts", auth=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["configured"] is True
        assert body["items"][0]["login"] == "pavel3871@mcdonalds001.shop"
