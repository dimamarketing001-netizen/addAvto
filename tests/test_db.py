from app import db


def test_project_create_list_update_delete(tmp_path, monkeypatch):
    database = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    db.init_db()
    project = db.create_project({
        "name": "Test project", "domain": "https://example.ru", "campaign_mode": "separate",
        "strategy": "AVERAGE_CRR", "target_drr": 30, "budget": 10000, "lead_value": 0,
        "headline": "Test", "ad_text": "", "keywords": "", "negative_keywords": "",
        "image_urls": ["https://example.ru/image.png"], "account_ids": [10, 20],
        "counter_id": None, "goal_id": None, "region_ids": [213],
    })
    assert project["id"] > 0
    assert project["image_urls"] == ["https://example.ru/image.png"]
    assert project["account_ids"] == [10, 20]
    changed = db.update_project(project["id"], {**project, "campaign_mode": "single_epk"})
    assert changed["campaign_mode"] == "single_epk"
    assert len(db.list_projects()) == 1
    assert db.delete_project(project["id"])
    assert db.list_projects() == []
