from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module


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
