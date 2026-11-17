"""Автоповтор пополнения (2026-11-17)."""
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db, topup_retry
from app import main as m
from test_bulk_campaign_create import _configure_test_env, _make_project

AUTH = ("test-admin", "test-password-long-enough")
ERR = ("{'message': 'Found errors', 'errors': [{'type': 'PaymentError', 'code': 'UNKNOWN', "
       "'message': 'Пополнение аккаунта недоступно до 13:43:35 14.11.2026', 'details': None}]}")


def test_parse_until_msk():
    u = topup_retry.parse_until("Пополнение не выполнено: " + ERR)
    assert u == datetime(2026, 11, 14, 10, 43, 35, tzinfo=timezone.utc)
    assert topup_retry.parse_until("Превышен лимит API Click.ru (429)") is None


def test_schedule_and_worker(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "retry.db")
    calls = []
    state = {"balance": 0}

    async def fund(client, aid, login, cid, payer, amount, start, is_epk):
        calls.append(amount)
        if len(calls) == 1:  # первая попытка — снова кулдаун
            return {"topup_rub": amount, "topup_status": "ERROR", "started": False, "notes": ["Пополнение не выполнено"],
                    "topup_retry_until": datetime.now(timezone.utc) - timedelta(minutes=5)}
        return {"topup_rub": amount, "topup_status": "SUCCESS", "started": True, "notes": ["Пополнено"]}

    class C:
        async def account(self, aid):
            return {"state": "ACTIVE", "balance": state["balance"], "serviceLogin": "porg-x"}

    monkeypatch.setattr(m, "_fund_and_start", fund)
    monkeypatch.setattr(m, "_resolve_clickru_client", lambda _c: C())
    monkeypatch.setattr(m, "TOPUP_RETRY_PAUSE_SECONDS", 0)
    with TestClient(m.app) as client:
        p = _make_project(client, AUTH)
        post = {"notes": ["Пополнение не выполнено"], "topup_retry_until": datetime.now(timezone.utc) - timedelta(minutes=10)}
        m._maybe_schedule_topup_retry(p["id"], 501, 9001, None, 1, 1220, True, False, post)
        assert "Автоповтор пополнения в" in post["notes"][-1]
        assert len(client.get(f"/api/projects/{p['id']}/topup-retries", auth=AUTH).json()["pending"]) == 1
        with db.connect() as c:  # «созрел»
            c.execute("UPDATE topup_retry SET retry_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        asyncio.run(m._topup_retry_once())  # снова кулдаун → перенос
        rows = topup_retry.list_project(p["id"])
        assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 1
        with db.connect() as c:
            c.execute("UPDATE topup_retry SET retry_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        asyncio.run(m._topup_retry_once())
        assert topup_retry.list_project(p["id"])[0]["status"] == "done" and calls == [1220.0, 1220.0]
        # уже пополнен вручную → без денег
        post = {"notes": [], "topup_retry_until": datetime.now(timezone.utc) - timedelta(minutes=10)}
        m._maybe_schedule_topup_retry(p["id"], 501, 9001, None, 1, 1220, True, False, post)
        state["balance"] = 1000
        with db.connect() as c:
            c.execute("UPDATE topup_retry SET retry_at=? WHERE status='pending'", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        asyncio.run(m._topup_retry_once())
        assert calls[-1] == 0
        # отмена
        m._maybe_schedule_topup_retry(p["id"], 502, 9002, None, 1, 1220, True, False,
                                      {"notes": [], "topup_retry_until": datetime.now(timezone.utc)})
        assert client.post(f"/api/projects/{p['id']}/topup-retries/cancel", auth=AUTH).json()["cancelled"] == 1
