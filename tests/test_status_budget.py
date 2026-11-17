"""Бюджет времени на статусы (2026-11-13)."""
import asyncio

from app import main as m


def test_statuses_budget_falls_back(monkeypatch):
    async def slow(row, force=False, **k):
        if row["account_id"] == 2:
            await asyncio.sleep(5)
        return {"code": "live", "label": "ok"}

    monkeypatch.setattr(m, "_account_status_live", slow)
    m._STATUS_CACHE.pop(2, None)
    m._STATUS_CACHE[3] = (0.0, {"code": "review", "label": "old"})

    async def run():
        out = await m._statuses_for([{"account_id": 1}, {"account_id": 2}], budget=0.3)
        for t in list(m._STATUS_BG):
            t.cancel()
        return out

    out = asyncio.run(run())
    assert out[1]["code"] == "live" and out[2]["code"] == "unknown"


def test_statuses_budget_uses_stale_cache(monkeypatch):
    async def slow(row, force=False, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(m, "_account_status_live", slow)
    m._STATUS_CACHE[7] = (0.0, {"code": "review", "label": "old"})

    async def run():
        out = await m._statuses_for([{"account_id": 7}], refresh=True, budget=0.2)
        for t in list(m._STATUS_BG):
            t.cancel()
        return out

    out = asyncio.run(run())
    assert out[7]["code"] == "review" and out[7]["stale"] is True


def test_error_keeps_last_good(monkeypatch):
    m._STATUS_COOLDOWN["until"] = 0
    import time as _t
    m._STATUS_CACHE[11] = (_t.time() - 1000, {"code": "live", "label": "Идут показы", "detail": ""})

    class C:
        async def account(self, _id):
            raise m.ClickRUError("Превышен лимит API Click.ru (429)")

    monkeypatch.setattr(m, "_resolve_clickru_client", lambda _c: C())

    async def boom(*a, **k):
        raise m.ClickRUError("Превышен лимит API Click.ru (429)")

    monkeypatch.setattr(m, "_resolve_client_login", boom)
    res = asyncio.run(m._account_status_live({"account_id": 11, "connection_id": 1, "campaign_id": 5}, force=True))
    assert res["code"] == "live" and res["stale"] is True
    assert m._STATUS_CACHE[11][1]["code"] == "live"


def test_cooldown_after_429_serves_cache(monkeypatch):
    import time as _t
    m._STATUS_COOLDOWN["until"] = _t.time() + 60
    m._STATUS_CACHE[21] = (0.0, {"code": "live", "label": "ok"})
    called = []
    monkeypatch.setattr(m, "_resolve_clickru_client", lambda _c: called.append(1))
    r = asyncio.run(m._account_status_live({"account_id": 21, "connection_id": 1}, force=True))
    assert r["code"] == "live" and r["stale"] is True and not called
    r2 = asyncio.run(m._account_status_live({"account_id": 22, "connection_id": 1}, force=True))
    assert r2["code"] == "unknown" and not called
    m._STATUS_COOLDOWN["until"] = 0
