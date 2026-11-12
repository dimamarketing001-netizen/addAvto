"""Дашборд пула ParsYa (2026-11-12)."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from test_bulk_campaign_create import FakeParsYaPoolClient, _configure_test_env, _make_project

AUTH = ("test-admin", "test-password-long-enough")


def test_pool_dashboard(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "pool.db")
    now = datetime.utcnow()
    accounts = [
        {"login": "a@x.ru", "status": "active", "is_free": True, "has_metrika_token": True,
         "metrika_token_expires_at": (now + timedelta(days=300)).isoformat(), "created_at": (now - timedelta(hours=2)).isoformat()},
        {"login": "b@x.ru", "status": "active", "is_free": True, "has_metrika_token": True,
         "metrika_token_expires_at": (now + timedelta(days=10)).isoformat(), "created_at": (now - timedelta(days=3)).isoformat()},
        {"login": "c@x.ru", "status": "invalid_cookies", "is_free": True, "has_metrika_token": False,
         "metrika_token_expires_at": None, "created_at": (now - timedelta(days=40)).isoformat(), "last_error": "cookies"},
    ]

    async def list_accounts(self):
        return accounts

    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(FakeParsYaPoolClient, "configured", True, raising=False)
    monkeypatch.setattr(FakeParsYaPoolClient, "list_accounts", list_accounts, raising=False)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        db.record_bulk_campaign(p["id"], "b@x.ru", 501, 9001, connection_id=None, payer_id=1)
        r = client.get("/api/parsya-pool/dashboard", auth=AUTH, params={"days": 7}).json()
    s = r["summary"]
    assert s["total"] == 3 and s["used"] == 1 and s["free"] == 1 and s["problem"] == 1
    assert s["added_today"] == 1 and s["added_7d"] == 2 and s["token_expiring_30d"] == 1 and s["campaigns"] == 1
    assert len(r["daily"]["labels"]) == 7 and sum(r["daily"]["added"]) == 2 and sum(r["daily"]["used"]) == 1
    b = next(a for a in r["items"] if a["login"] == "b@x.ru")
    assert b["state"] == "used" and b["uses"][0]["project_id"] == p["id"] and b["uses"][0]["account_id"] == 501
    assert r["items"][0]["login"] == "a@x.ru"  # новые сверху
