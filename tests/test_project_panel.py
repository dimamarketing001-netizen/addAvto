"""Панель проекта в ЛК (2026-11-10): таймлайн, аккаунты, проверка через
Direct API, Метрика в браузере; сводка для фильтров списка проектов."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from app.parsya_pool_client import ParsYaPoolError
from test_bulk_campaign_create import _configure_test_env, _make_project

AUTH = ("test-admin", "test-password-long-enough")


def _seed(project_id: int) -> int:
    job = db.create_bulk_job(project_id, 2)
    db.append_bulk_job_item(job, {"pool_login": "pool-a", "account_id": 501, "campaign_id": 9001, "status": "created",
                                  "headline": "Санта", "ads_created": 1, "image_used": True, "sitelinks_used": 4,
                                  "callouts_used": 3})
    db.append_bulk_job_item(job, {"pool_login": "pool-b", "status": "failed", "error": "нет свободных логинов"})
    db.record_bulk_campaign(project_id, "pool-a", 501, 9001, connection_id=None, payer_id=1480787)
    db.finish_bulk_job(job, "done")
    return job


def test_history_and_list_stats(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "panel.db")
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        other = _make_project(client, AUTH, name="Второй")
        job = _seed(p["id"])
        db.add_project_event(p["id"], "ozon_done", "Gemini написал тексты по карточке Ozon", "Модель gemini-3.5-flash")

        h = client.get(f"/api/projects/{p['id']}/history", auth=AUTH).json()
        kinds = [e["kind"] for e in h["events"]]
        assert {"created", "ozon_done", "launch_started", "launch_done"} <= set(kinds)
        done = next(e for e in h["events"] if e["kind"] == "launch_done")
        assert done["job_id"] == job and "создано 1 из 2" in done["title"] and "pool-b: нет свободных логинов" in done["detail"]
        assert h["accounts"] == [{**h["accounts"][0]}] and h["accounts"][0]["pool_login"] == "pool-a"
        a = h["accounts"][0]
        assert a["account_id"] == 501 and a["campaign_id"] == 9001 and a["payer_id"] == 1480787
        assert a["sitelinks_used"] == 4 and a["image_used"] is True

        items = {x["id"]: x for x in client.get("/api/projects", auth=AUTH).json()["items"]}
        st = items[p["id"]]["stats"]
        assert st["jobs_count"] == 1 and st["last_job_status"] == "done" and st["campaigns_count"] == 1
        assert st["payer_ids"] == [1480787] and items[p["id"]]["has_launched"] is True
        assert items[other["id"]]["stats"]["jobs_count"] == 0 and items[other["id"]]["has_launched"] is False
        assert client.get("/api/projects/9999/history", auth=AUTH).status_code == 404


def test_check_account_via_direct_api(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "check.db")
    calls = []

    async def accounts(self):
        return [{"id": 501, "serviceLogin": "porg-abc"}]

    async def direct_api(self, login, method, body, **kw):
        calls.append((login, method))
        return {"result": {
            "campaigns": {"Campaigns": [{"Id": 9001, "Name": "Санта", "Status": "DRAFT", "State": "OFF"}]},
            "adgroups": {"AdGroups": [{"Id": 1}]},
            "ads": {"Ads": [{"Id": 7, "Status": "DRAFT", "State": "OFF", "TextAd": {
                "Title": "Санта", "Text": "т", "AdImageHash": "h", "SitelinkSetId": 5, "AdExtensions": [{}, {}]}}]},
            "keywords": {"Keywords": [{"Id": 1, "Status": "DRAFT"}, {"Id": 2, "Status": "DRAFT"}]},
        }[method]}

    monkeypatch.setattr(main_module.ClickRUClient, "accounts", accounts)
    monkeypatch.setattr(main_module.ClickRUClient, "direct_api", direct_api)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        _seed(p["id"])
        r = client.get(f"/api/projects/{p['id']}/accounts/501/check", auth=AUTH).json()
        assert r["client_login"] == "porg-abc"
        c = r["campaigns"][0]
        assert c["Status"] == "DRAFT" and c["ad_groups"] == 1 and c["keywords"] == 2
        assert c["ads"][0]["has_image"] and c["ads"][0]["has_sitelinks"] and c["ads"][0]["callouts"] == 2
        assert {m for _, m in calls} == {"campaigns", "adgroups", "ads", "keywords"}
        # чужой аккаунт из ЛК не проверить
        assert client.get(f"/api/projects/{p['id']}/accounts/777/check", auth=AUTH).status_code == 404


def test_open_metrika_only_for_project_accounts(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "metrika.db")
    opened = []

    class Pool:
        async def open_pool_browser(self, login, target="metrika"):
            opened.append((login, target))
            return {"ok": True, "logged_in": True, "url": "https://metrika.yandex.ru/list"}

    monkeypatch.setattr(main_module, "ParsYaPoolClient", Pool)
    with TestClient(main_module.app) as client:
        p = _make_project(client, AUTH)
        _seed(p["id"])
        assert client.post(f"/api/projects/{p['id']}/accounts/777/open-metrika", auth=AUTH).status_code == 404
        r = client.post(f"/api/projects/{p['id']}/accounts/501/open-metrika", auth=AUTH)
        assert r.status_code == 200 and r.json()["logged_in"] is True
        assert opened == [("pool-a", "metrika")]
        h = client.get(f"/api/projects/{p['id']}/history", auth=AUTH).json()
        assert any(e["kind"] == "browser_opened" for e in h["events"])

    class BadPool:
        async def open_pool_browser(self, login, target="metrika"):
            raise ParsYaPoolError("У аккаунта в пуле нет сохранённой сессии Яндекса", 409)

    monkeypatch.setattr(main_module, "ParsYaPoolClient", BadPool)
    with TestClient(main_module.app) as client:
        r = client.post(f"/api/projects/{p['id']}/accounts/501/open-metrika", auth=AUTH)
        assert r.status_code == 409 and "сессии" in r.json()["detail"]
