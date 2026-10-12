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
            "account_ids": [111, 222],
            "region_ids": [213],
        })
        assert response.status_code == 201, response.text
        project = response.json()
        assert project["domain"] == "https://example.ru"
        assert project["account_ids"] == [111, 222]

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



def test_metrika_setup_creates_counter_and_lead_goal_once(tmp_path, monkeypatch):
    from app.click_client import ClickRUClient
    from app.metrika_client import YandexMetrikaClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "metrika-test.db"))
    monkeypatch.setenv("APP_USERNAME", "test-admin")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLICKRU_API_TOKEN", "test-token-not-used-for-network")

    state = {"counters": [], "goals": []}

    async def fake_integrations(self, service="YANDEX_DIRECT", with_access_token=False):
        if service == "YANDEX_METRIKA":
            row = {"id": 456, "service": "YANDEX_METRIKA", "email": "metrika@example.ru"}
            if with_access_token:
                row["access_token"] = "mock-metrika-token"
            return [row]
        return []

    async def fake_list_counters(self):
        return list(state["counters"])

    async def fake_create_counter(self, name, site):
        row = {"id": 12345, "name": name, "site": site, "permission": "edit"}
        state["counters"].append(row)
        return row

    async def fake_list_goals(self, counter_id):
        assert counter_id == 12345
        return list(state["goals"])

    async def fake_create_goal(self, counter_id, default_price=None):
        row = {
            "id": 98765,
            "name": "lead",
            "type": "action",
            "conditions": [{"type": "exact", "url": "lead"}],
        }
        state["goals"].append(row)
        return row

    monkeypatch.setattr(ClickRUClient, "integrations", fake_integrations)
    monkeypatch.setattr(YandexMetrikaClient, "list_counters", fake_list_counters)
    monkeypatch.setattr(YandexMetrikaClient, "create_counter", fake_create_counter)
    monkeypatch.setattr(YandexMetrikaClient, "list_goals", fake_list_goals)
    monkeypatch.setattr(YandexMetrikaClient, "create_goal", fake_create_goal)

    with TestClient(app) as client:
        auth = ("test-admin", "test-password-long-enough")
        created = client.post("/api/projects", auth=auth, json={
            "name": "Metrika test",
            "domain": "https://www.example.ru",
            "campaign_mode": "separate",
            "strategy": "AVERAGE_CRR",
            "target_drr": 30,
            "budget": 10000,
            "lead_value": 0,
            "headline": "Test",
            "ad_text": "Test",
            "keywords": "",
            "negative_keywords": "",
            "image_urls": [],
            "account_ids": [],
            "region_ids": [],
        })
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]

        response = client.post(
            f"/api/projects/{project_id}/metrika/setup",
            auth=auth,
            json={"integration_id": 456, "create_if_missing": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counter_id"] == 12345
        assert body["goal_id"] == 98765
        assert body["created_counter"] is True
        assert body["created_goal"] is True
        assert body["tracking_snippet"] == "ym(12345, 'reachGoal', 'lead');"

        updated = client.get("/api/projects", auth=auth).json()["items"][0]
        assert updated["counter_id"] == 12345
        assert updated["goal_id"] == 98765
