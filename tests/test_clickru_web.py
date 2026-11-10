"""Веб-кабинет Click.ru на уровне подключения (2026-11-10): логин/пароль/
cookies хранятся зашифрованными у подключения, вход идёт через WSL
(ParsYaPoolClient.clickru_web_login), провижининг получает кабинет
выбранного подключения и возвращает обновлённые cookies."""
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from app import main as main_module
from app.parsya_pool_client import ParsYaPoolError
from test_bulk_campaign_create import (  # tests/ в sys.path (rootdir без __init__.py)
    FakeClickRUClient,
    FakeParsYaPoolClient,
    FakeYandexMetrikaClient,
    _configure_test_env,
    _make_project,
    _seed_connection,
    _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")
COOKIES = [{"name": "sid", "value": "fresh-session", "domain": ".click.ru", "httpOnly": True}]


class LoginPool:
    """Фейковый WSL-вход: запоминает вызовы, отвечает по сценарию."""

    calls: list[dict] = []
    outcome: object = "ok"

    def __init__(self):
        pass

    async def clickru_web_login(self, username, password, cookies=None):
        LoginPool.calls.append({"username": username, "password": password, "cookies": cookies})
        if isinstance(LoginPool.outcome, Exception):
            raise LoginPool.outcome
        return {"ok": True, "relogged": not cookies, "cookies": COOKIES}


def _setup(monkeypatch, tmp_path, outcome="ok"):
    _configure_test_env(monkeypatch, tmp_path / "clickru-web.db")

    async def fake_user(self):
        return {"id": 1}

    monkeypatch.setattr(main_module.ClickRUClient, "user", fake_user)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", LoginPool)
    LoginPool.calls = []
    LoginPool.outcome = outcome


def _raw_row(tmp_path, connection_id):
    db = sqlite3.connect(tmp_path / "clickru-web.db")
    db.row_factory = sqlite3.Row
    try:
        return dict(db.execute("SELECT * FROM clickru_connections WHERE id = ?", (connection_id,)).fetchone())
    finally:
        db.close()


def _create(client, **extra):
    body = {"label": "Кабинет 2", "token": "second-cabinet-token", **extra}
    return client.post("/api/clickru-connections", auth=AUTH, json=body)


def test_connect_with_web_login_stores_encrypted_and_never_returns_secrets(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        resp = _create(client, web_username="agency-1", web_password="s3cret-pass")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["web_status"] == "ok"
        assert body["web_username"] == "agency-1"
        assert body["has_web_password"] is True
        text = json.dumps(body) + client.get("/api/clickru-connections", auth=AUTH).text
        assert "s3cret-pass" not in text and "fresh-session" not in text
        assert "web_password_enc" not in text and "cookies" not in text

        assert LoginPool.calls == [{"username": "agency-1", "password": "s3cret-pass", "cookies": []}]
        row = _raw_row(tmp_path, body["id"])
        # В БД — только шифртекст.
        assert row["web_password_enc"] and "s3cret-pass" not in row["web_password_enc"]
        assert row["web_cookies_enc"] and "fresh-session" not in row["web_cookies_enc"]


def test_connect_requires_both_username_and_password(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        assert _create(client, web_username="agency-1").status_code == 422
        assert _create(client, web_password="x").status_code == 422
        assert LoginPool.calls == []
        # Без кабинета вовсе — можно (например, только для отчётов).
        resp = _create(client)
        assert resp.status_code == 201
        assert resp.json()["web_status"] == "none"


def test_failed_login_marks_connection_and_drops_cookies(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        cid = _create(client, web_username="agency-1", web_password="old").json()["id"]
        LoginPool.outcome = ParsYaPoolError("[clickru-login-failed] Логин в click.ru не удался", 409)
        resp = client.post(f"/api/clickru-connections/{cid}/web-check", auth=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["web_status"] == "failed"
        assert "[clickru-login-failed]" not in body["web_status_message"]
        assert "не удался" in body["web_status_message"]
        assert _raw_row(tmp_path, cid)["web_cookies_enc"] == ""


def test_unreachable_wsl_keeps_session_status(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        cid = _create(client, web_username="agency-1", web_password="p").json()["id"]
        cookies_before = _raw_row(tmp_path, cid)["web_cookies_enc"]
        LoginPool.outcome = ParsYaPoolError("Ошибка соединения с ParsYa pool API: ConnectError", 503)
        body = client.post(f"/api/clickru-connections/{cid}/web-check", auth=AUTH).json()
        assert body["web_status"] == "ok"
        assert body["web_status_message"].startswith("Проверка не выполнена")
        assert _raw_row(tmp_path, cid)["web_cookies_enc"] == cookies_before


def test_check_reuses_saved_cookies_and_new_credentials_reset_them(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        cid = _create(client, web_username="agency-1", web_password="p1").json()["id"]
        client.post(f"/api/clickru-connections/{cid}/web-check", auth=AUTH)
        assert LoginPool.calls[-1]["cookies"] == COOKIES

        resp = client.put(f"/api/clickru-connections/{cid}/web-credentials", auth=AUTH,
                          json={"web_username": "agency-2", "web_password": "p2"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["web_username"] == "agency-2"
        # Cookies прежней учётки не передаются в вход под новой.
        assert LoginPool.calls[-1] == {"username": "agency-2", "password": "p2", "cookies": []}

        assert client.post("/api/clickru-connections/999/web-check", auth=AUTH).status_code == 404


def _bulk_setup(monkeypatch, tmp_path, pool_cls):
    _configure_test_env(monkeypatch, tmp_path / "clickru-web.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", pool_cls)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0
    FakeParsYaPoolClient.clickru_calls = []


def test_bulk_sends_connection_cabinet_and_saves_refreshed_cookies(tmp_path, monkeypatch):
    _bulk_setup(monkeypatch, tmp_path, FakeParsYaPoolClient)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        resp = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH,
                           json={"count": 2, "connection_id": cid})
        assert resp.status_code == 202, resp.text
        assert _wait_for_job(client, AUTH, project["id"], resp.json()["job_id"])["created"] == 2

        calls = FakeParsYaPoolClient.clickru_calls
        assert [c["connection_id"] for c in calls] == [cid, cid]
        assert calls[0]["username"] == "agency-login" and calls[0]["password"] == "agency-password"
        # Второй аккаунт получает сессию, которую вернул первый.
        assert calls[0]["cookies"][0]["value"] == "session-0"
        assert calls[1]["cookies"][0]["value"] == "session-1"


def test_bulk_rejects_foreign_connection_without_cabinet_login(tmp_path, monkeypatch):
    _bulk_setup(monkeypatch, tmp_path, FakeParsYaPoolClient)
    with TestClient(main_module.app) as client:
        from app import db
        project = _make_project(client, AUTH)
        cid = db.create_clickru_connection("Чужой кабинет", "other-token", "")["id"]
        resp = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH,
                           json={"count": 1, "connection_id": cid})
        assert resp.status_code == 422
        assert "логин и пароль" in resp.json()["detail"]
        assert FakeParsYaPoolClient.clickru_calls == []


def test_bulk_env_cabinet_without_login_uses_legacy_env_path(tmp_path, monkeypatch):
    _bulk_setup(monkeypatch, tmp_path, FakeParsYaPoolClient)
    with TestClient(main_module.app) as client:
        from app import db
        project = _make_project(client, AUTH)
        # Тот же токен, что CLICKRU_API_TOKEN из .env — старый .env-логин ParsYa валиден.
        cid = db.create_clickru_connection("Кабинет из .env", "test-token-not-used-for-network", "")["id"]
        resp = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH,
                           json={"count": 1, "connection_id": cid})
        assert resp.status_code == 202, resp.text
        assert _wait_for_job(client, AUTH, project["id"], resp.json()["job_id"])["created"] == 1
        assert FakeParsYaPoolClient.clickru_calls == [None]


def test_bulk_login_failure_marks_connection_and_stops_claiming_pool(tmp_path, monkeypatch):
    class BadLoginPool(FakeParsYaPoolClient):
        async def provision_direct_account(self, account_name_prefix=None, clickru=None):
            FakeParsYaPoolClient.clickru_calls.append(clickru)
            raise ParsYaPoolError("[clickru-login-failed] Логин в click.ru не удался (неверный логин или пароль?)", 409)

    _bulk_setup(monkeypatch, tmp_path, BadLoginPool)
    with TestClient(main_module.app) as client:
        from app import db
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        resp = client.post(f"/api/projects/{project['id']}/campaigns/bulk-create", auth=AUTH,
                           json={"count": 3, "connection_id": cid})
        assert resp.status_code == 202, resp.text
        body = _wait_for_job(client, AUTH, project["id"], resp.json()["job_id"])
        assert body["created"] == 0
        assert [i["status"] for i in body["items"]] == ["failed"] * 3
        assert "[clickru-login-failed]" not in body["items"][0]["error"]
        # Пул дёрнули один раз: дальше подключение уже помечено failed.
        assert len(FakeParsYaPoolClient.clickru_calls) == 1
        connection = db.get_clickru_connection(cid)
        assert connection["web_status"] == "failed"
        assert connection["web_cookies_enc"] == ""
