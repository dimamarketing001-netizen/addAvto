from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from app.metrika_client import YandexMetrikaClient
from app.token_crypto import decrypt_secret, encrypt_secret
from app.yandex_oauth import YandexOAuthClient


def _configure_test_env(monkeypatch, database_path):
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("METRIKA_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("METRIKA_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "METRIKA_OAUTH_REDIRECT_URI",
        "https://example.test/oauth/yandex/callback",
    )
    monkeypatch.setenv("METRIKA_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("METRIKA_OAUTH_TOKEN", "")


def test_oauth_start_and_callback_save_encrypted_connection(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "oauth-test.db")

    class FakeOAuth:
        @classmethod
        def from_env(cls):
            return cls()

        def authorization_url(self, state, code_verifier):
            return (
                "https://oauth.yandex.test/authorize?state=" + state +
                "&code_challenge=" + self.pkce_challenge(code_verifier)
            )

        async def exchange_code(self, code, code_verifier):
            assert code == "fake-code"
            assert len(code_verifier) >= 43
            return {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_in": 3600,
                "scope": "metrika:read metrika:write",
            }

        @staticmethod
        def pkce_challenge(verifier):
            return YandexOAuthClient.pkce_challenge(verifier)

    async def fake_list_counters(self):
        return []

    monkeypatch.setattr(main_module, "YandexOAuthClient", FakeOAuth)
    monkeypatch.setattr(YandexMetrikaClient, "list_counters", fake_list_counters)

    with TestClient(main_module.app, follow_redirects=False) as client:
        start = client.get(
            "/oauth/yandex/start?label=Marketing%20Yandex",
            auth=("test-admin", "test-password-long-enough"),
        )
        assert start.status_code == 302
        auth_url = urlparse(start.headers["location"])
        state = parse_qs(auth_url.query)["state"][0]

        callback = client.get(
            "/oauth/yandex/callback",
            params={"code": "fake-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert "metrika_oauth=connected" in callback.headers["location"]

        connection = db.get_metrika_connection(1, "test-admin")
        assert connection is not None
        assert connection["label"] == "Marketing Yandex"
        assert connection["access_token_enc"] != "fake-access-token"
        assert decrypt_secret(connection["access_token_enc"]) == "fake-access-token"
        assert decrypt_secret(connection["refresh_token_enc"]) == "fake-refresh-token"

        list_response = client.get(
            "/api/metrika/connections",
            auth=("test-admin", "test-password-long-enough"),
        )
        assert list_response.status_code == 200
        assert len(list_response.json()["items"]) == 1
        assert "access_token" not in str(list_response.json())


def test_oauth_state_is_single_use_and_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "oauth-state-test.db"))
    monkeypatch.setenv("METRIKA_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db.init_db()

    from datetime import datetime, timedelta, timezone
    import hashlib

    state = "random-single-use-state"
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    verifier_enc = encrypt_secret("test-code-verifier")
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")

    db.save_metrika_oauth_state(
        state_hash=state_hash,
        owner_username="test-admin",
        label="First account",
        code_verifier_enc=verifier_enc,
        expires_at=expiry,
    )
    first = db.consume_metrika_oauth_state(state_hash)
    second = db.consume_metrika_oauth_state(state_hash)

    assert first is not None
    assert decrypt_secret(first["code_verifier_enc"]) == "test-code-verifier"
    assert second is None


def test_pkce_challenge_uses_s256():
    challenge = YandexOAuthClient.pkce_challenge("test-verifier")
    assert challenge
    assert "=" not in challenge
    assert len(challenge) == 43
