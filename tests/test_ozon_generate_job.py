"""ozon-generate фоном (2026-11-10): генерация идёт минуты, а прокси по
дороге (nginx 60/120 с, parsya-control 30 с) обрывали синхронный ответ —
«addAvto unreachable». POST теперь отвечает 202 сразу, итог — GET."""
from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from app import main as main_module
from app.ozon_ad_creator import AdCopy, OzonParseError, OzonProduct, ProcessResult, YandexDirectAd
from test_bulk_campaign_create import _configure_test_env, _make_project

AUTH = ("test-admin", "test-password-long-enough")
OZON_URL = "https://www.ozon.ru/product/santa-3160445300/"


def _result():
    return ProcessResult(
        product=OzonProduct(url=OZON_URL, title="Санта Клаус 3D", images=["https://ir.ozone.ru/a.jpg"]),
        ad_copy=AdCopy(
            yandex_direct=[YandexDirectAd(title1="Санта Клаус 3D", title2="t2", text="Подвижная фигурка", angle="a")],
            keywords=["санта клаус фигурка"],
            negative_keywords=["бесплатно"],
        ),
    )


def _poll(client, project_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/projects/{project_id}/ozon-generate", auth=AUTH).json()
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _setup(monkeypatch, tmp_path, outcome):
    _configure_test_env(monkeypatch, tmp_path / "ozon-job.db")
    main_module._OZON_JOBS.clear()
    calls = []

    async def fake_process(url, **kwargs):
        calls.append(url)
        await asyncio.sleep(0.3)  # «долгая» генерация — POST не должен её ждать
        if isinstance(outcome, Exception):
            raise outcome
        return _result()

    monkeypatch.setattr(main_module, "process_ozon_url", fake_process)
    return calls


def test_post_returns_immediately_and_get_returns_result(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "ok")
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, name="Товар Ozon", launch_mode="ozon", ozon_url=OZON_URL)
        assert client.get(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH).json() == {"status": "none"}
        t0 = time.time()
        resp = client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={"url": OZON_URL})
        assert resp.status_code == 202 and resp.json()["status"] == "running"
        assert time.time() - t0 < 0.25
        # повторный «Далее», пока идёт генерация, вторую задачу не запускает
        again = client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={"url": OZON_URL})
        assert again.json()["status"] == "running"
        st = _poll(client, project["id"])
        assert st["status"] == "done", st
        assert calls == [OZON_URL]
        saved = st["result"]["project"]
        assert saved["name"] == "Санта Клаус 3D"
        assert saved["headlines"] == ["Санта Клаус 3D"] and saved["ad_texts"] == ["Подвижная фигурка"]
        assert saved["image_urls"] == ["https://ir.ozone.ru/a.jpg"]
        from app import db
        assert db.get_project(project["id"])["keywords"] == "санта клаус фигурка"


def test_failure_is_reported_via_status(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, OzonParseError("Ozon заблокировал запрос (HTTP 403)"))
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON_URL)
        assert client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={}).status_code == 202
        st = _poll(client, project["id"])
        assert st["status"] == "failed" and "403" in st["error"]


def test_unexpected_error_does_not_get_lost(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, RuntimeError("boom"))
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON_URL)
        client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={})
        st = _poll(client, project["id"])
        assert st["status"] == "failed" and "RuntimeError: boom" in st["error"]


def test_validation_errors_stay_synchronous(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "ok")
    with TestClient(main_module.app) as client:
        assert client.post("/api/projects/9999/ozon-generate", auth=AUTH, json={}).status_code == 404
        project = _make_project(client, AUTH)
        assert client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={}).status_code == 422
