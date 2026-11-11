"""Продолжение прерванного запуска (2026-11-10): кнопка «Продолжить» и
автоматически после перезапуска сервиса; аккаунт, уже созданный
провижинингом (пул потрачен), но без кампании — используется повторно."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import db
from app import main as main_module
from app.schemas import BulkCampaignCreateRequest
from test_bulk_campaign_create import (
    FakeClickRUClient, FakeParsYaPoolClient, FakeYandexMetrikaClient,
    _configure_test_env, _make_project, _seed_connection, _wait_for_job,
)

AUTH = ("test-admin", "test-password-long-enough")


def _fakes(monkeypatch):
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0


def _orphan_job(project_id: int, connection_id: int) -> int:
    """Запуск на 3 аккаунта: первый сделан, второй — аккаунт создан, кампании
    нет, процесс умер (status остался running)."""
    req = BulkCampaignCreateRequest(count=3, connection_id=connection_id).model_dump(mode="json")
    job = db.create_bulk_job(project_id, 3, request=req)
    db.append_bulk_job_item(job, {"pool_login": "done@pool", "account_id": 9108, "campaign_id": 1, "status": "created", "index": 0})
    db.record_bulk_campaign(project_id, "done@pool", 9108, 1, connection_id=connection_id)
    db.set_bulk_job_pending(job, {"pool_login": "spent@pool", "account_id": 9109, "index": 1})
    return job


def test_manual_resume_reuses_spent_account(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "resume.db")
    _fakes(monkeypatch)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        job = _orphan_job(project["id"], cid)
    with TestClient(main_module.app) as client:  # перезапуск сервиса
        st = client.get(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}", auth=AUTH).json()
        assert st["status"] == "failed" and st["can_resume"] and st["remaining"] == 2
        h = client.get(f"/api/projects/{project['id']}/history", auth=AUTH).json()
        assert h["resume"] == {"job_id": job, "remaining": 2, "reuse_accounts": 1}

        r = client.post(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}/resume", auth=AUTH)
        assert r.status_code == 202, r.text
        new = _wait_for_job(client, AUTH, project["id"], r.json()["job_id"])
        assert new["status"] == "done" and new["requested"] == 2 and new["created"] == 2
        assert new["resumed_from"] == job
        logins = [i["pool_login"] for i in new["items"]]
        assert logins[0] == "spent@pool"            # потраченный аккаунт использован
        assert FakeParsYaPoolClient._counter == 1   # из пула взят только один новый
        assert [i["index"] for i in new["items"]] == [1, 2]
        # второй раз продолжать нечего / нельзя
        assert client.post(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}/resume", auth=AUTH).status_code == 409
        assert client.get(f"/api/projects/{project['id']}/history", auth=AUTH).json()["resume"] is None


def test_auto_resume_after_restart(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "auto.db")
    _fakes(monkeypatch)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        job = _orphan_job(project["id"], cid)
    monkeypatch.setenv("BULK_AUTO_RESUME", "1")
    monkeypatch.setattr(main_module, "AUTO_RESUME_DELAY_SECONDS", 0)
    with TestClient(main_module.app) as client:
        deadline = time.monotonic() + 30
        new_id = None
        while time.monotonic() < deadline and new_id is None:
            new_id = next((j["id"] for j in db.list_bulk_jobs(project["id"]) if j.get("resumed_from") == job), None)
            time.sleep(0.2)
        assert new_id, "автопродолжение не стартовало"
        new = _wait_for_job(client, AUTH, project["id"], new_id)
        assert new["created"] == 2 and new["items"][0]["pool_login"] == "spent@pool"
        h = client.get(f"/api/projects/{project['id']}/history", auth=AUTH).json()
        assert any("продолжен автоматически" in e["title"] for e in h["events"])


def test_old_job_without_request_cannot_resume(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "old.db")
    _fakes(monkeypatch)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        job = db.create_bulk_job(project["id"], 3)
        db.finish_bulk_job(job, "failed", "старый запуск")
        st = client.get(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}", auth=AUTH).json()
        assert st["can_resume"] is False
        assert client.post(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}/resume", auth=AUTH).status_code == 409


def test_resume_in_parts_with_limit(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "parts.db")
    _fakes(monkeypatch)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        job = _orphan_job(project["id"], cid)
    with TestClient(main_module.app) as client:
        r = client.post(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}/resume?limit=1", auth=AUTH)
        first = _wait_for_job(client, AUTH, project["id"], r.json()["job_id"])
        assert first["requested"] == 1 and first["created"] == 1
        assert first["items"][0]["pool_login"] == "spent@pool" and first["items"][0]["index"] == 1
        assert FakeParsYaPoolClient._counter == 0  # пул не тронут
        # остаток (1) у исходного запуска всё ещё можно продолжить
        h = client.get(f"/api/projects/{project['id']}/history", auth=AUTH).json()
        assert h["resume"] == {"job_id": job, "remaining": 1, "reuse_accounts": 0}
        r2 = client.post(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}/resume", auth=AUTH)
        second = _wait_for_job(client, AUTH, project["id"], r2.json()["job_id"])
        assert second["created"] == 1 and second["items"][0]["index"] == 2
        assert FakeParsYaPoolClient._counter == 1
        assert client.get(f"/api/projects/{project['id']}/history", auth=AUTH).json()["resume"] is None


def test_resume_finishes_half_created_campaign(monkeypatch, tmp_path):
    """Аккаунт, где кампания создана, а группа/объявления — нет (Директ
    ответил 1000 «временно недоступен»): продолжение доделывает ЭТУ кампанию,
    вторую не создаёт и пул не трогает."""
    _configure_test_env(monkeypatch, tmp_path / "finish.db")
    _fakes(monkeypatch)
    created_campaigns = []
    orig_text = FakeClickRUClient.create_draft_text_campaign

    async def track_campaign(self, *a, **kw):
        cid = await orig_text(self, *a, **kw)
        created_campaigns.append(cid)
        return cid

    async def direct_api(self, client_login, method_path, body, **kw):
        return {"result": {"AdGroups": []} if method_path == "adgroups" else {"Keywords": []}}

    monkeypatch.setattr(FakeClickRUClient, "create_draft_text_campaign", track_campaign)
    monkeypatch.setattr(FakeClickRUClient, "direct_api", direct_api, raising=False)
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH)
        cid = _seed_connection()
        req = BulkCampaignCreateRequest(count=1, connection_id=cid).model_dump(mode="json")
        job = db.create_bulk_job(project["id"], 1, request=req)
        db.append_bulk_job_item(job, {"pool_login": "half@pool", "account_id": 9107, "campaign_id": 55555,
                                      "status": "failed", "error": "Yandex Direct API error 1000", "index": 0})
        db.finish_bulk_job(job, "done")
        r = client.post(f"/api/projects/{project['id']}/campaigns/bulk-jobs/{job}/resume", auth=AUTH)
        new = _wait_for_job(client, AUTH, project["id"], r.json()["job_id"])
        it = new["items"][0]
        assert new["created"] == 1 and it["campaign_id"] == 55555 and it["account_id"] == 9107
        assert "Доделана кампания" in (it.get("note") or "")
        assert created_campaigns == [] and FakeParsYaPoolClient._counter == 0
