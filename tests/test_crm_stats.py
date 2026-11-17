"""Б24: лиды / клиенты / договоры по кампаниям (2026-11-17)."""
import asyncio
from datetime import date

from fastapi.testclient import TestClient

from app import crm_stats, db, lk
from app import main as main_module
from app.token_crypto import encrypt_secret
from test_bulk_campaign_create import _configure_test_env, _make_project

AUTH = ("test-admin", "test-password-long-enough")


def _fake_b24(monkeypatch):
    leads = [
        {"ID": "1", "UTM_CAMPAIGN": "rsya_7001", "CONTACT_ID": "50", "STATUS_ID": "CONVERTED"},
        {"ID": "2", "UTM_CAMPAIGN": "7001", "CONTACT_ID": "0", "STATUS_ID": "NEW"},
        {"ID": "3", "UTM_CAMPAIGN": "МК / Все города_7001", "CONTACT_ID": "51", "STATUS_ID": "NEW"},
        {"ID": "4", "UTM_CAMPAIGN": "rsya_97001", "CONTACT_ID": "52", "STATUS_ID": "NEW"},  # чужая: 7001 лишь вхождение
        {"ID": "5", "UTM_CAMPAIGN": "rsya_7002", "CONTACT_ID": None, "STATUS_ID": "NEW"},
    ]
    deals = [
        {"ID": "900", "CONTACT_ID": "50", "LEAD_ID": "1", "CATEGORY_ID": "6", "STAGE_SEMANTIC_ID": "S", "OPPORTUNITY": "150000"},
        {"ID": "901", "CONTACT_ID": "51", "LEAD_ID": "0", "CATEGORY_ID": "6", "STAGE_SEMANTIC_ID": "P", "OPPORTUNITY": "0"},
        {"ID": "902", "CONTACT_ID": "0", "LEAD_ID": "5", "CATEGORY_ID": "6", "STAGE_SEMANTIC_ID": "F", "OPPORTUNITY": "0"},
        {"ID": "903", "CONTACT_ID": "50", "LEAD_ID": "0", "CATEGORY_ID": "2", "STAGE_SEMANTIC_ID": "S", "OPPORTUNITY": "9"},  # не «Продажа»
    ]
    from urllib.parse import parse_qsl, unquote

    async def fake(webhook, method, params):
        if method == "crm.category.list":
            return {"result": {"categories": [{"id": 2, "name": "Производство"}, {"id": 6, "name": "Продажа"}]}}
        if method == "crm.lead.list":
            return {"result": leads, "total": len(leads)}
        assert method == "batch"
        res = {}
        for key, cmd in params["cmd"].items():
            m, _, qs = cmd.partition("?")
            q = parse_qsl(qs, keep_blank_values=True)
            if m == "crm.lead.list":
                res[key] = leads if key == "p0" else []
            else:
                cat = dict(q)["filter[CATEGORY_ID]"]
                field = "CONTACT_ID" if any(k == "filter[CONTACT_ID][]" for k, _ in q) else "LEAD_ID"
                ids = {v for k, v in q if k == f"filter[{field}][]"}
                res[key] = [d for d in deals if d["CATEGORY_ID"] == cat and d[field] in ids]
        return {"result": {"result": res, "result_next": {}, "result_error": {}}}

    monkeypatch.setattr(lk, "b24_call", fake)


def _seed_source(owner="test-admin", enabled=1):
    with db.connect() as c:
        cur = c.execute("INSERT INTO b24_sources(owner_username,name,webhook_enc,enabled,created_at) VALUES(?,?,?,?,?)",
                        (owner, "МЮ", encrypt_secret("https://x.bitrix24.ru/rest/1/abc"), enabled, "2026-11-17"))
        return cur.lastrowid


def test_campaign_stats_and_endpoints(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "crm.db")
    _fake_b24(monkeypatch)
    crm_stats._cache.clear(); crm_stats._funnel_cache.clear()
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        sid = _seed_source()
        off = _seed_source(enabled=0)
        db.record_bulk_campaign(p["id"], "pool-a", 101, 7001, connection_id=None, payer_id=1)
        db.record_bulk_campaign(p["id"], "pool-b", 102, 7002, connection_id=None, payer_id=1)
        assert client.get("/api/crm/sources", auth=AUTH).json()["items"][0]["name"] == "МЮ"
        assert client.put(f"/api/projects/{p['id']}/crm-source", auth=AUTH, json={"source_id": off}).status_code == 422
        assert client.put(f"/api/projects/{p['id']}/crm-source", auth=AUTH, json={"source_id": 999}).status_code == 404
        empty = client.get("/api/ads-crm", auth=AUTH, params={"days": 7}).json()
        assert empty["items"] == {} and empty["projects"][0]["source"] is None
        ok = client.put(f"/api/projects/{p['id']}/crm-source", auth=AUTH, json={"source_id": sid}).json()
        assert ok["source"]["name"] == "МЮ"
        assert client.get(f"/api/projects/{p['id']}/crm-source", auth=AUTH).json()["source_id"] == sid
        r = client.get("/api/ads-crm", auth=AUTH, params={"days": 7}).json()
    a, b = r["items"]["101"], r["items"]["102"]
    assert (a["leads"], a["clients"], a["deals"], a["won"], a["won_sum"]) == (3, 2, 2, 1, 150000.0)
    assert (b["leads"], b["clients"], b["deals"], b["won"]) == (1, 0, 1, 0)
    assert r["totals"] == {"leads": 4, "clients": 2, "deals": 3, "won": 1, "won_sum": 150000.0}
    assert r["projects"][0]["funnel"] == {"id": 6, "name": "Продажа"} and not r["errors"]


def test_no_sales_funnel(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "crm2.db")

    async def fake(webhook, method, params):
        return {"result": {"categories": [{"id": 1, "name": "Производство"}]}}

    monkeypatch.setattr(lk, "b24_call", fake)
    crm_stats._cache.clear(); crm_stats._funnel_cache.clear()
    with TestClient(main_module.app):
        sid = _seed_source()
        try:
            asyncio.run(crm_stats.campaign_stats(sid, [7001], date(2026, 11, 1), date(2026, 11, 17)))
            assert False
        except crm_stats.CrmError as exc:
            assert "Продажа" in str(exc)
