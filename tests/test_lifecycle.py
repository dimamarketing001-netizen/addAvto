"""Жизнь аккаунтов и автозамена (2026-11-17)."""
import asyncio
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db, lifecycle
from app import main as m
from test_bulk_campaign_create import _configure_test_env, _make_project

AUTH = ("test-admin", "test-password-long-enough")


def test_is_dead_rules():
    y = date(2026, 11, 16)
    old = "2026-11-10T08:00:00+00:00"
    assert lifecycle.is_dead({"2026-11-16": {"impressions": 0}, "2026-11-15": {"impressions": 40}}, old, y)[0]
    assert not lifecycle.is_dead({"2026-11-16": {"impressions": 3}}, old, y)[0]
    assert not lifecycle.is_dead({}, old, y)[0]  # нет данных за вчера — не решаем
    assert not lifecycle.is_dead({"2026-11-16": {"impressions": 0}}, "2026-11-16T08:00:00+00:00", y)[0]  # молодой


def test_run_stop_replace_refund(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "life.db")
    yday = lifecycle.today_msk() - timedelta(days=1)
    calls = {"suspend": [], "refund": [], "jobs": []}
    bal = {101: 0, 102: 500.0}

    class C:
        configured = True

        async def stat(self, fields, d1, d2, ids, v2=False):
            return [{"accountId": "101", "date": yday.isoformat(), "impressions": "120", "clicks": "3", "loss": "50"},
                    {"accountId": "102", "date": (yday - timedelta(days=1)).isoformat(), "impressions": "80", "clicks": "2", "loss": "30"}]

        async def accounts(self):
            return [{"id": 101, "state": "ACTIVE", "balance": bal[101]}, {"id": 102, "state": "ACTIVE", "balance": bal[102]}]

        async def suspend_campaign(self, login, cid):
            calls["suspend"].append(cid)

        async def refund_available(self, aid):
            return "AVAILABLE", ""

        async def refund(self, aid):
            calls["refund"].append(aid)

    async def login(client, aid):
        return "porg-x"

    def start_job(pid, payload, **k):
        calls["jobs"].append((pid, payload.count, payload.topup_amount_rub, payload.button_action))
        return 77

    monkeypatch.setattr(m, "_resolve_clickru_client", lambda _c: C())
    monkeypatch.setattr(m, "_resolve_client_login", login)
    monkeypatch.setattr(m, "_start_bulk_job", start_job)
    with TestClient(m.app) as client:
        p = _make_project(client, AUTH)
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        for aid in (101, 102):
            db.record_bulk_campaign(p["id"], f"pool-{aid}", aid, 7000 + aid, connection_id=None, payer_id=1)
        with db.connect() as c:
            c.execute("UPDATE bulk_campaigns SET created_at=?", (old,))
        jid = db.create_bulk_job(p["id"], 2, request={"count": 2, "connection_id": 1, "payer_id": 1, "topup_amount_rub": 1220,
                                                      "auto_start": True, "button_action": "BUY"})
        db.finish_bulk_job(jid, "done")
        # выключено → только план, без действий
        plan = client.post("/api/lifecycle/run", auth=AUTH, json={"dry_run": True}).json()
        assert [x["account_id"] for x in plan["dead"]] == [102] and plan["dead"][0]["action"] == "автозамена выключена"
        assert client.put(f"/api/projects/{p['id']}/autoreplace", auth=AUTH, json={"enabled": True, "max_per_day": 3}).json()["enabled"]
        r = client.post("/api/lifecycle/run", auth=AUTH, json={"dry_run": False}).json()
        assert r["dead"][0]["action"] == "остановить и заменить"
        assert calls["suspend"] == [7102] and calls["jobs"] == [(p["id"], 1, 1220.0, "BUY")]
        life = lifecycle.get_life(102)
        assert life["state"] == "stopped" and life["replacement_job_id"] == 77 and life["balance_at_stop"] == 500.0
        # повтор в тот же день — ничего нового
        client.post("/api/lifecycle/run", auth=AUTH, json={"dry_run": False})
        assert calls["suspend"] == [7102] and len(calls["jobs"]) == 1
        # прошло 2 суток → заявка на возврат
        lifecycle.set_life(102, p["id"], stopped_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
        client.post("/api/lifecycle/run", auth=AUTH, json={"dry_run": False})
        assert calls["refund"] == [102] and lifecycle.get_life(102)["state"] == "refund_requested"
        bal[102] = 0
        client.post("/api/lifecycle/run", auth=AUTH, json={"dry_run": False})
        assert lifecycle.get_life(102)["state"] == "refunded"
        dash = client.get("/api/lifecycle/dashboard", auth=AUTH).json()
    s = dash["summary"]
    assert s["accounts"] == 2 and s["alive"] == 1 and s["refunded"] == 1 and s["money_refunded"] == 500.0 and s["replaced"] == 1
    assert dash["items"][0]["state"] == "active" and dash["items"][0]["impressions"] == 120


def test_recently_kicked_is_not_dead(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "life2.db")
    yday = lifecycle.today_msk() - timedelta(days=1)

    class C:
        configured = True

        async def stat(self, fields, d1, d2, ids, v2=False):
            return []

        async def accounts(self):
            return [{"id": 201, "state": "ACTIVE", "balance": 1000}, {"id": 202, "state": "ACTIVE", "balance": 1000}]

    monkeypatch.setattr(m, "_resolve_clickru_client", lambda _c: C())
    with TestClient(m.app) as client:
        p = _make_project(client, AUTH)
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        for aid in (201, 202):
            db.record_bulk_campaign(p["id"], f"pool-{aid}", aid, 7000 + aid, connection_id=None, payer_id=1)
        with db.connect() as c:
            c.execute("UPDATE bulk_campaigns SET created_at=?", (old,))
        db.add_project_event(p["id"], "kick", "«Пнуть»: запущено 1 из 1", "201: Пополнено на 1220 ₽ с НДС")
        r = client.post("/api/lifecycle/run", auth=AUTH, json={"dry_run": True}).json()
    assert [x["account_id"] for x in r["dead"]] == [202]



def test_dead_revives_and_kick_takes_dead(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "life3.db")
    yday = lifecycle.today_msk() - timedelta(days=1)

    class C:
        configured = True

        async def stat(self, fields, d1, d2, ids, v2=False):
            return [{"accountId": "301", "date": yday.isoformat(), "impressions": "50", "clicks": "1", "loss": "9"}]

        async def accounts(self):
            return [{"id": 301, "state": "ACTIVE", "balance": 900}, {"id": 302, "state": "ACTIVE", "balance": 1000}]

    async def unknown(rows, refresh=False, budget=None):
        return {int(r["account_id"]): {"code": "unknown", "label": "?", "detail": "", "ads": {}} for r in rows}

    monkeypatch.setattr(m, "_resolve_clickru_client", lambda _c: C())
    monkeypatch.setattr(m, "_statuses_for", unknown)
    with TestClient(m.app) as client:
        p = _make_project(client, AUTH)
        for aid in (301, 302):
            db.record_bulk_campaign(p["id"], f"pool-{aid}", aid, 7000 + aid, connection_id=None, payer_id=1)
            lifecycle.set_life(aid, p["id"], state="dead", died_at="2026-11-17T06:00:00+00:00", death_reason="0 показов")
        client.post("/api/lifecycle/run", auth=AUTH, json={"dry_run": False})
        assert lifecycle.get_life(301)["state"] == "active"  # вчера были показы
        plan = client.post(f"/api/projects/{p['id']}/kick", auth=AUTH, json={"dry_run": True}).json()
    assert [x["account_id"] for x in plan["items"]] == [302] and plan["items"][0]["topup"] is False
