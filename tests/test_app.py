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
