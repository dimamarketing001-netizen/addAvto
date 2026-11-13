"""Запрет удаления подключения Click.ru с привязанными аккаунтами (2026-11-13)."""
from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from test_bulk_campaign_create import _configure_test_env, _make_project
from test_dashboard import _seed_connection

AUTH = ("test-admin", "test-password-long-enough")


def test_delete_blocked_when_accounts_bound(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "del.db")
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        used = _seed_connection()
        free = _seed_connection()
        db.record_bulk_campaign(p["id"], "pool-a", 101, 7001, connection_id=used, payer_id=1)
        r = client.delete(f"/api/clickru-connections/{used}", auth=AUTH)
        assert r.status_code == 409 and "аккаунтов кампаний: 1" in r.json()["detail"]
        assert db.get_clickru_connection(used) is not None
        ok = client.delete(f"/api/clickru-connections/{free}", auth=AUTH)
        assert ok.status_code == 200 and db.get_clickru_connection(free) is None
