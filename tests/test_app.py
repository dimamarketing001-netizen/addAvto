from fastapi.testclient import TestClient

from app.main import app


def test_project_routes_and_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "")

    with TestClient(app) as client:
        assert client.get("/").status_code == 401
        auth = ("test-admin", "test-password-long-enough")
        response = client.post("/api/projects", auth=auth, json={
            "name": "Test search and RSYA",
            "domain": "https://example.ru",
            "campaign_mode": "separate",
            "strategy": "AVERAGE_CRR",
            "target_drr": 30,
            "budget": 12000,
            "lead_value": 0,
            "headline": "Test headline",
            "ad_text": "Test body",
            "keywords": "lawyer",
            "negative_keywords": "free",
            "image_urls": [],
            "region_ids": [213],
        })
        assert response.status_code == 201, response.text
        project = response.json()
        assert project["domain"] == "https://example.ru"

        plan_response = client.post(f"/api/projects/{project['id']}/plan", auth=auth)
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert len(plan["items"]) == 2
        assert [item["placement"] for item in plan["items"]] == ["SEARCH", "NETWORK"]

        project["campaign_mode"] = "single_epk"
        update = client.put(f"/api/projects/{project['id']}", auth=auth, json=project)
        assert update.status_code == 200, update.text
        plan = client.post(f"/api/projects/{project['id']}/plan", auth=auth).json()
        assert len(plan["items"]) == 1
        assert plan["items"][0]["placement"] == "SEARCH_AND_NETWORK"


def test_direct_account_creation_uses_selected_integration(tmp_path, monkeypatch):
    """Machine endpoint for ParsYa's connect_direct_account.py, not the UI —
    see DirectAccountCreate in schemas.py. Keep this test; removing the
    endpoint again breaks that script's pool-provisioning pipeline."""
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "account-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    async def fake_integrations(self):
        return [{"id": 321, "service": "YANDEX_DIRECT"}]

    async def fake_create_direct_account(self, name, integration_id):
        assert name == "Test Direct account"
        assert integration_id == 321
        return {"accountId": 98765}

    monkeypatch.setattr(ClickRUClient, "integrations", fake_integrations)
    monkeypatch.setattr(ClickRUClient, "create_direct_account", fake_create_direct_account)

    with TestClient(app) as client:
        response = client.post(
            "/api/accounts/direct",
            auth=("test-admin", "test-password-long-enough"),
            json={"name": "Test Direct account", "integration_id": 321},
        )
        assert response.status_code == 201, response.text
        assert response.json()["account_id"] == 98765
        assert response.json()["created"] is True


def test_clickru_connections_crud(tmp_path, monkeypatch):
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "connections-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")

    async def fake_user(self):
        assert self.token == "my-real-looking-token"
        return {"id": 1, "email": "test@example.ru"}

    monkeypatch.setattr(ClickRUClient, "user", fake_user)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")

        created = client.post("/api/clickru-connections", auth=auth, json={
            "label": "Основной кабинет", "token": "my-real-looking-token",
        })
        assert created.status_code == 201, created.text
        connection_id = created.json()["id"]

        listed = client.get("/api/clickru-connections", auth=auth)
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["label"] == "Основной кабинет"
        assert items[0]["token_preview"] == "…oken"  # last 4 chars of the token, never the full value

        deleted = client.delete(f"/api/clickru-connections/{connection_id}", auth=auth)
        assert deleted.status_code == 200
        assert client.get("/api/clickru-connections", auth=auth).json()["items"] == []


def test_payers_and_marking_schema(tmp_path, monkeypatch):
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "payers-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    async def fake_payers(self):
        return [{"id": 1, "name": "ООО Ромашка", "balance": 10000}]

    async def fake_marking_schema(self, account_id):
        assert account_id == 321
        return {"accountId": 321, "configured": False}

    monkeypatch.setattr(ClickRUClient, "payers", fake_payers)
    monkeypatch.setattr(ClickRUClient, "marking_schema", fake_marking_schema)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")

        payers_response = client.get("/api/payers", auth=auth)
        assert payers_response.status_code == 200, payers_response.text
        assert payers_response.json()["items"][0]["name"] == "ООО Ромашка"

        schema_response = client.get("/api/accounts/321/marking-schema", auth=auth)
        assert schema_response.status_code == 200, schema_response.text
        assert schema_response.json()["configured"] is False


def test_create_payer_business_builds_correct_clickru_payload(tmp_path, monkeypatch):
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "payer-create-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    captured = {}

    async def fake_create_payer(self, payload):
        captured.update(payload)
        return 555

    monkeypatch.setattr(ClickRUClient, "create_payer", fake_create_payer)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.post("/api/payers", auth=auth, json={
            "type": "BUSINESS",
            "inn": "7701234567",
            "name": "ООО Ромашка",
            "full_name": "Общество с ограниченной ответственностью «Ромашка»",
            "kpp": "770101001",
            "ogrn": 1027700132195,
            "tax_form": "USN",
            "business_address": {"address": "Москва, ул. Новая, д. 1", "postal_code": "101000"},
            "actual_address": {"address": "Москва, ул. Новая, д. 1", "postal_code": "101000"},
            "bank": {"name": "Альфа-банк", "rcbic": "044525593", "account": "40702810100000000001", "correspondent_account": "30101810200000000593"},
        })
        assert response.status_code == 201, response.text
        assert response.json()["payer_id"] == 555
        assert captured["type"] == "BUSINESS"
        assert captured["fullName"].startswith("Общество")
        assert captured["businessAddress"] == {"address": "Москва, ул. Новая, д. 1", "postalCode": "101000"}
        assert captured["bank"]["correspondentAccount"] == "30101810200000000593"
        assert captured["ogrn"] == 1027700132195


def test_create_payer_business_requires_type_specific_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "payer-missing-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.post("/api/payers", auth=auth, json={"type": "BUSINESS", "inn": "7701234567"})
        assert response.status_code == 422
        assert "обязательно" in response.json()["detail"]


def test_geo_regions_filters_to_russia_and_caches(tmp_path, monkeypatch):
    from app import db
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "geo-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    calls = {"accounts": 0, "dictionaries": 0}

    async def fake_accounts(self):
        calls["accounts"] += 1
        return [{"id": 1, "serviceLogin": "porg-1"}]

    async def fake_dictionaries(self, client_login, names):
        calls["dictionaries"] += 1
        assert names == ["GeoRegions"]
        return {
            "GeoRegions": [
                {"GeoRegionId": 225, "GeoRegionName": "Россия", "ParentGeoRegionNames": {"Items": ["Земля"]}},
                {"GeoRegionId": 213, "GeoRegionName": "Москва", "ParentGeoRegionNames": {"Items": ["Земля", "Россия", "Центральный федеральный округ"]}},
                {"GeoRegionId": 187, "GeoRegionName": "Киев", "ParentGeoRegionNames": {"Items": ["Земля", "Украина"]}},
            ]
        }

    monkeypatch.setattr(ClickRUClient, "accounts", fake_accounts)
    monkeypatch.setattr(ClickRUClient, "dictionaries", fake_dictionaries)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.get("/api/geo-regions", auth=auth)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        names = {item["name"] for item in items}
        assert names == {"Москва"}  # Россия сама по себе и Киев (не РФ) отфильтрованы

        # Второй запрос идёт из кэша app_settings, не дёргает Click.ru снова.
        response2 = client.get("/api/geo-regions", auth=auth)
        assert response2.status_code == 200
        assert calls["accounts"] == 1
        assert calls["dictionaries"] == 1


def test_geo_regions_skips_accounts_with_invalid_login(tmp_path, monkeypatch):
    """2026-11-02: пул аккаунтов Click.ru содержит логины, которые
    Dictionaries.get не принимает (другие интеграции типа
    zzclickadwords-*, неактивные логины) — первый попавшийся serviceLogin
    не гарантирует успех, нужно пробовать следующий."""
    from app.click_client import ClickRUClient, ClickRUError

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "geo-fallback-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    async def fake_accounts(self):
        return [
            {"id": 1, "serviceLogin": "zzclickadwords-1-xxx", "status": "ACTIVE"},
            {"id": 2, "serviceLogin": "porg-good", "status": "ACTIVE"},
        ]

    async def fake_dictionaries(self, client_login, names):
        if client_login == "zzclickadwords-1-xxx":
            raise ClickRUError("Логин не найден или неактивен")
        return {"GeoRegions": [{"GeoRegionId": 213, "GeoRegionName": "Москва", "ParentGeoRegionNames": {"Items": ["Россия"]}}]}

    monkeypatch.setattr(ClickRUClient, "accounts", fake_accounts)
    monkeypatch.setattr(ClickRUClient, "dictionaries", fake_dictionaries)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.get("/api/geo-regions", auth=auth)
        assert response.status_code == 200, response.text
        assert {item["name"] for item in response.json()["items"]} == {"Москва"}


def test_project_locked_after_launch_but_duplicate_is_editable(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "lock-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "")

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        created = client.post("/api/projects", auth=auth, json={
            "name": "Проект", "domain": "https://example.ru",
            "campaign_mode": "separate", "target_drr": 30,
            "keywords": "lawyer", "region_ids": [213],
        }).json()

        items = client.get("/api/projects", auth=auth).json()["items"]
        assert next(p for p in items if p["id"] == created["id"])["has_launched"] is False

        db.create_bulk_job(created["id"], requested=1)

        items = client.get("/api/projects", auth=auth).json()["items"]
        assert next(p for p in items if p["id"] == created["id"])["has_launched"] is True

        created["name"] = "Изменённое имя"
        blocked = client.put(f"/api/projects/{created['id']}", auth=auth, json=created)
        assert blocked.status_code == 409, blocked.text

        duplicate = client.post(f"/api/projects/{created['id']}/duplicate", auth=auth)
        assert duplicate.status_code == 201, duplicate.text
        copy = duplicate.json()
        assert copy["id"] != created["id"]
        assert copy["name"] == "Проект (копия)"
        assert copy["keywords"] == "lawyer"
        assert copy["region_ids"] == [213]

        copy["name"] = "Отредактированная копия"
        edit_copy = client.put(f"/api/projects/{copy['id']}", auth=auth, json=copy)
        assert edit_copy.status_code == 200, edit_copy.text


def test_activate_campaign_requires_marking_schema(tmp_path, monkeypatch):
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "activate-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    async def fake_marking_schema_missing(self, account_id):
        return {"accountId": account_id, "configured": False}

    monkeypatch.setattr(ClickRUClient, "marking_schema", fake_marking_schema_missing)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.post("/api/accounts/321/campaigns/70000/activate", auth=auth)
        assert response.status_code == 409, response.text
        assert "маркировк" in response.json()["detail"]


def test_activate_campaign_succeeds_when_marking_configured(tmp_path, monkeypatch):
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "activate-ok-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    async def fake_marking_schema_ok(self, account_id):
        return {"accountId": account_id, "configured": True}

    async def fake_accounts(self):
        return [{"id": 321, "serviceLogin": "porg-321"}]

    resumed = {}

    async def fake_resume_campaign(self, client_login, campaign_id):
        resumed["client_login"] = client_login
        resumed["campaign_id"] = campaign_id

    monkeypatch.setattr(ClickRUClient, "marking_schema", fake_marking_schema_ok)
    monkeypatch.setattr(ClickRUClient, "accounts", fake_accounts)
    monkeypatch.setattr(ClickRUClient, "resume_campaign", fake_resume_campaign)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.post("/api/accounts/321/campaigns/70000/activate", auth=auth)
        assert response.status_code == 200, response.text
        assert response.json()["activated"] is True
        assert resumed == {"client_login": "porg-321", "campaign_id": 70000}


def test_suspend_campaign(tmp_path, monkeypatch):
    """Kill-switch counterpart to /activate — no marking-schema check,
    always allowed, since stopping spend is never the risky direction."""
    from app.click_client import ClickRUClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "suspend-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    async def fake_accounts(self):
        return [{"id": 321, "serviceLogin": "porg-321"}]

    suspended = {}

    async def fake_suspend_campaign(self, client_login, campaign_id):
        suspended["client_login"] = client_login
        suspended["campaign_id"] = campaign_id

    monkeypatch.setattr(ClickRUClient, "accounts", fake_accounts)
    monkeypatch.setattr(ClickRUClient, "suspend_campaign", fake_suspend_campaign)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        response = client.post("/api/accounts/321/campaigns/70000/suspend", auth=auth)
        assert response.status_code == 200, response.text
        assert response.json()["suspended"] is True
        assert suspended == {"client_login": "porg-321", "campaign_id": 70000}
