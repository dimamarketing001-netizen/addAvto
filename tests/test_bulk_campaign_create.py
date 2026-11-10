from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import main as main_module


def _wait_for_job(client, auth, project_id, job_id, timeout=120):
    """bulk-create now returns a job_id immediately and runs the batch in
    the background (2026-10-31) — poll until it's no longer 'running'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}/campaigns/bulk-jobs/{job_id}", auth=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] != "running":
            return body
        time.sleep(0.3)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s")


def _configure_test_env(monkeypatch, database_path):
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BULK_AUTO_RESUME", "0")  # автопродолжение проверяется отдельно
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")
    monkeypatch.setenv("PARSYA_POOL_API_TOKEN", "test-pool-token-not-used-for-network")
    # Fernet-ключ для логина/пароля/cookies кабинета Click.ru (2026-11-10).
    monkeypatch.setenv("METRIKA_TOKEN_ENCRYPTION_KEY", "mX94veC1967OrYtU45Xa36eGPOKLRhtQR2TV_d18exg=")


class FakeClickRUClient:
    configured = True

    def __init__(self, token=None, user_id=None):
        # Real signature takes token/user_id (see click_client.py) when
        # resolved from a stored connection — accept and ignore them here.
        pass

    async def accounts(self):
        return [{"id": aid, "name": f"acc-{aid}", "serviceLogin": f"porg-{aid}"} for aid in range(9100, 9110)]

    async def create_draft_text_campaign(self, client_login, name, start_date, target_drr=None, goal_id=None):
        assert target_drr == 30.0
        assert goal_id is not None  # per-account goal, created by FakeYandexMetrikaClient below
        return 70000 + int(client_login.split("-")[1])

    async def create_draft_unified_campaign(self, client_login, name, start_date, target_drr, goal_id):
        assert target_drr == 30.0
        assert goal_id is not None
        return 60000 + int(client_login.split("-")[1])

    async def create_adgroup(self, client_login, campaign_id, name, region_ids, negative_keywords=None):
        assert region_ids == [225]
        return 80000 + campaign_id

    async def add_keywords(self, client_login, ad_group_id, phrases):
        assert phrases == ["юридическая консультация"]
        return [90001, 90002]

    created_ads: list[dict] = []
    mobile_adjustments: list[dict] = []

    async def create_text_ad(self, client_login, ad_group_id, title, text, href, **kwargs):
        FakeClickRUClient.created_ads.append({"ad_group_id": ad_group_id, "title": title, "text": text})
        return 100000 + ad_group_id

    async def upload_ad_image(self, client_login, name, image_b64):
        return "fakehash123"

    async def create_sitelinks_set(self, client_login, sitelinks):
        assert 1 <= len(sitelinks) <= 4
        return 11111

    async def add_mobile_bid_adjustment(self, client_login, campaign_id, percent):
        FakeClickRUClient.mobile_adjustments.append({"campaign_id": campaign_id, "percent": percent})

    async def create_callout(self, client_login, text):
        return 22222


class FakeParsYaPoolClient:
    configured = True
    _counter = 0

    clickru_calls: list = []

    async def provision_direct_account(self, account_name_prefix=None, clickru=None):
        FakeParsYaPoolClient._counter += 1
        FakeParsYaPoolClient.clickru_calls.append(clickru)
        n = FakeParsYaPoolClient._counter
        return {
            "pool_login": f"testuser{n}@mcdonalds001.shop",
            "integration_id": 300000 + n,
            "account_id": 9100 + n,
            "account_name": f"acc{n}",
            # Скрипт ParsYa возвращает обновлённую сессию кабинета.
            "clickru_cookies": [{"name": "sid", "value": f"session-{n}", "domain": ".click.ru"}],
        }

    async def get_account(self, login):
        return {"login": login, "metrika_access_token": f"fake-token-for-{login}"}


class FakeYandexMetrikaClient:
    """Each pool login gets its OWN counter/goal (per product decision) —
    this fake has no counters/goals yet, so every call creates fresh ones,
    keyed by the token (which _metrika_context derives 1:1 from pool_login).
    """

    def __init__(self, access_token):
        self.access_token = access_token

    async def list_counters(self):
        return []

    async def create_counter(self, name, site):
        return {"id": abs(hash(self.access_token)) % 100000 + 500000, "name": name, "site": site}

    async def list_goals(self, counter_id):
        return []

    async def create_goal(self, counter_id, default_price=None):
        return {"id": counter_id + 1, "name": "lead", "type": "action",
                "conditions": [{"type": "exact", "url": "lead"}]}


def _seed_connection() -> int:
    """Подключение со своим логином/паролем кабинета (2026-11-10): без них
    подключение с токеном не из .env для запуска не годится."""
    import json
    from app import db
    from app.token_crypto import encrypt_secret
    connection = db.create_clickru_connection("Test Click.ru account", "fake-token", "")
    db.set_clickru_web_credentials(connection["id"], "agency-login", encrypt_secret("agency-password"))
    db.set_clickru_web_session(
        connection["id"], "ok", "Сессия активна",
        cookies_enc=encrypt_secret(json.dumps([{"name": "sid", "value": "session-0", "domain": ".click.ru"}])),
    )
    return int(connection["id"])


def _make_project(client, auth, **overrides):
    payload = {
        "name": "Bulk test project",
        "domain": "https://example.ru",
        "headlines": ["Заголовок 1", "Заголовок 2"],
        "ad_texts": ["Текст 1", "Текст 2"],
        "keywords": "юридическая консультация",
        "region_ids": [225],
        "target_drr": 30.0,
    }
    payload.update(overrides)
    resp = client.post("/api/projects", auth=auth, json=payload)
    project = resp.json()
    from app import db
    return db.get_project(project["id"])


def test_bulk_create_success(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth)
        connection_id = _seed_connection()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 3, "connection_id": connection_id},
        )
        assert response.status_code == 202, response.text
        body = _wait_for_job(client, auth, project["id"], response.json()["job_id"])
        assert body["status"] == "done"
        assert body["requested"] == 3
        assert body["created"] == 3
        assert len(body["items"]) == 3
        goal_ids = set()
        for item in body["items"]:
            assert item["status"] == "created"
            assert item["campaign_id"] is not None
            assert item["counter_id"] is not None
            assert item["goal_id"] is not None
            assert item["headline"] in ("Заголовок 1", "Заголовок 2")
            assert item["ad_text"] in ("Текст 1", "Текст 2")
            goal_ids.add(item["goal_id"])
        # Each account's goal must be its own, not shared across accounts.
        assert len(goal_ids) == 3

        # The campaign's connection_id/payer_id are recorded for audit.
        campaigns = client.get(f"/api/projects/{project['id']}/campaigns", auth=auth).json()["items"]
        assert all(c["connection_id"] == connection_id for c in campaigns)


def test_bulk_create_uses_unified_campaign_for_single_epk(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-epk-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth, campaign_mode="single_epk")
        connection_id = _seed_connection()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 1, "connection_id": connection_id},
        )
        assert response.status_code == 202, response.text
        body = _wait_for_job(client, auth, project["id"], response.json()["job_id"])
        assert body["created"] == 1
        # FakeClickRUClient.create_draft_unified_campaign returns IDs in the
        # 60000 range, create_draft_text_campaign in the 70000 range — this
        # confirms single_epk actually took the UnifiedCampaign branch.
        assert 60000 <= body["items"][0]["campaign_id"] < 70000


def test_bulk_create_duplicate_single_uses_same_creative_every_time(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-dup-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth)
        connection_id = _seed_connection()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 3, "connection_id": connection_id, "creative_mode": "duplicate_single"},
        )
        assert response.status_code == 202, response.text
        body = _wait_for_job(client, auth, project["id"], response.json()["job_id"])
        assert body["created"] == 3
        headlines = {item["headline"] for item in body["items"]}
        ad_texts = {item["ad_text"] for item in body["items"]}
        assert headlines == {"Заголовок 1"}
        assert ad_texts == {"Текст 1"}


def test_bulk_create_ads_per_group_creates_multiple_ads_in_one_group(tmp_path, monkeypatch):
    """Wizard step 4 "структура объявлений": ads_per_group > 1 should
    create that many ads inside the SAME ad group (2026-10-31), not spread
    across accounts — only valid with creative_mode=shuffle."""
    _configure_test_env(monkeypatch, tmp_path / "bulk-adsgroup-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0
    FakeClickRUClient.created_ads = []

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth)
        connection_id = _seed_connection()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 1, "connection_id": connection_id, "creative_mode": "shuffle", "ads_per_group": 5},
        )
        assert response.status_code == 202, response.text
        body = _wait_for_job(client, auth, project["id"], response.json()["job_id"])
        assert body["created"] == 1
        assert body["items"][0]["ads_created"] == 5
        # All 5 ads landed in the SAME ad group, not 5 separate groups.
        group_ids = {ad["ad_group_id"] for ad in FakeClickRUClient.created_ads}
        assert len(FakeClickRUClient.created_ads) == 5
        assert len(group_ids) == 1


def test_bulk_create_ads_per_group_rejects_non_shuffle_mode(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-adsgroup-reject-test.db")

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.post(
            "/api/projects/1/campaigns/bulk-create", auth=auth,
            json={"count": 1, "connection_id": 1, "creative_mode": "duplicate_single", "ads_per_group": 3},
        )
        assert response.status_code == 422


def test_bulk_create_applies_mobile_bid_adjustment(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-mobile-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0
    FakeClickRUClient.mobile_adjustments = []

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth, mobile_bid_adjustment_percent=-50)
        connection_id = _seed_connection()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 1, "connection_id": connection_id},
        )
        assert response.status_code == 202, response.text
        body = _wait_for_job(client, auth, project["id"], response.json()["job_id"])
        assert body["created"] == 1
        assert body["items"][0]["note"] is None
        assert len(FakeClickRUClient.mobile_adjustments) == 1
        assert FakeClickRUClient.mobile_adjustments[0]["percent"] == -50


def test_bulk_create_manual_per_account_uses_distinct_creative(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-manual-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth)
        connection_id = _seed_connection()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={
                "count": 2,
                "connection_id": connection_id,
                "creative_mode": "manual_per_account",
                "creative_sets": [
                    {"headline": "Ручной 1", "ad_text": "Текст ручной 1"},
                    {"headline": "Ручной 2", "ad_text": "Текст ручной 2"},
                ],
            },
        )
        assert response.status_code == 202, response.text
        body = _wait_for_job(client, auth, project["id"], response.json()["job_id"])
        assert body["created"] == 2
        headlines = [item["headline"] for item in body["items"]]
        assert headlines == ["Ручной 1", "Ручной 2"]


def test_bulk_create_manual_mode_requires_matching_creative_set_count(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-manual-mismatch-test.db")

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.post(
            "/api/projects/1/campaigns/bulk-create", auth=auth,
            json={
                "count": 2,
                "connection_id": 1,
                "creative_mode": "manual_per_account",
                "creative_sets": [{"headline": "Только один", "ad_text": "Текст"}],
            },
        )
        assert response.status_code == 422


def test_bulk_create_requires_headlines_and_texts(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-noheadline-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        resp = client.post("/api/projects", auth=auth, json={
            "name": "No headlines project",
            "domain": "https://example.ru",
            "keywords": "фраза",
            "region_ids": [225],
        })
        project = resp.json()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 1, "connection_id": 1},
        )
        assert response.status_code == 422
        assert "заголов" in response.json()["detail"].lower()


def test_bulk_create_rejects_second_launch_while_one_is_running(tmp_path, monkeypatch):
    """Two concurrent batches for the same project would double-book the
    same ParsYa pool accounts — the second launch must be rejected (409)
    until the first job finishes (2026-10-31)."""
    _configure_test_env(monkeypatch, tmp_path / "bulk-lock-test.db")
    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FakeParsYaPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FakeParsYaPoolClient._counter = 0

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth)
        connection_id = _seed_connection()

        first = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 2, "connection_id": connection_id},
        )
        assert first.status_code == 202, first.text

        second = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 1, "connection_id": connection_id},
        )
        assert second.status_code == 409, second.text

        _wait_for_job(client, auth, project["id"], first.json()["job_id"])


def test_bulk_create_partial_failure_continues(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "bulk-partial-test.db")

    class FlakyPoolClient(FakeParsYaPoolClient):
        _calls = 0

        async def provision_direct_account(self, account_name_prefix=None, clickru=None):
            FlakyPoolClient._calls += 1
            if FlakyPoolClient._calls == 2:
                from app.parsya_pool_client import ParsYaPoolError
                raise ParsYaPoolError("Пул пуст")
            return await super().provision_direct_account(account_name_prefix, clickru)

    monkeypatch.setattr(main_module, "ClickRUClient", FakeClickRUClient)
    monkeypatch.setattr(main_module, "ParsYaPoolClient", FlakyPoolClient)
    monkeypatch.setattr(main_module, "YandexMetrikaClient", FakeYandexMetrikaClient)
    FlakyPoolClient._calls = 0
    FakeParsYaPoolClient._counter = 0

    with TestClient(main_module.app) as client:
        auth = ("test-admin", "test-password-long-enough")
        project = _make_project(client, auth)
        connection_id = _seed_connection()

        response = client.post(
            f"/api/projects/{project['id']}/campaigns/bulk-create", auth=auth,
            json={"count": 3, "connection_id": connection_id},
        )
        assert response.status_code == 202, response.text
        body = _wait_for_job(client, auth, project["id"], response.json()["job_id"])
        assert body["requested"] == 3
        assert body["created"] == 2
        statuses = [item["status"] for item in body["items"]]
        assert statuses.count("created") == 2
        assert statuses.count("failed") == 1
