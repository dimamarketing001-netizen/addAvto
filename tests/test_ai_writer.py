from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app import ai_writer, creative_images
from app import main as main_module
from tests.test_bulk_campaign_create import _configure_test_env

AUTH = ("test-admin", "test-password-long-enough")


def test_clean_result_limits_and_dupes():
    raw = {"headlines": ["Юрист по банкротству", "Юрист по банкротству", "x" * 57, "Списание долгов 🚀"],
           "callouts": ["Бесплатная консультация", "Очень длинное уточнение больше лимита"],
           "sitelinks": [{"title": "Цены", "description": "Стоимость услуг", "anchor": "Price!"}, {"title": "y" * 31}],
           "keywords": ["банкротство физ лиц", "a b c d e f g h"],
           "negative_keywords": ["-бесплатно", "Скачать"]}
    out = ai_writer.clean_result(raw, ["headlines", "callouts", "sitelinks", "keywords", "negative_keywords"])
    assert out["headlines"] == ["Юрист по банкротству", "Списание долгов"]
    assert out["callouts"] == ["Бесплатная консультация"]
    assert out["sitelinks"] == [{"title": "Цены", "description": "Стоимость услуг", "anchor": "price"}]
    assert out["keywords"] == ["банкротство физ лиц"]
    assert out["negative_keywords"] == ["бесплатно", "скачать"]


def test_ai_generate_endpoint(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "ai.db")
    monkeypatch.setenv("AI_API_KEY", "k")
    seen = {}

    async def fake_complete(system, user, max_tokens=4000, model=None):
        seen["user"] = user
        return '```json\n{"headlines": ["Списание долгов законно"], "sitelinks": [{"title": "Цены", "description": "", "anchor": "price"}]}\n```'

    monkeypatch.setattr(ai_writer, "complete", fake_complete)
    with TestClient(main_module.app) as client:
        r = client.post("/api/ai/generate", auth=AUTH, json={"domain": "https://bfl.ru/landing?x=1", "topic": "банкротство",
                                                             "fields": ["headlines", "sitelinks"], "mode": "rewrite",
                                                             "examples": {"headlines": ["Спишем долги"]}})
        assert r.status_code == 200, r.text
        assert r.json() == {"headlines": ["Списание долгов законно"],
                            "sitelinks": [{"title": "Цены", "description": "", "href": "https://bfl.ru/#price"}]}
        assert "РЕРАЙТ" in seen["user"] and "Спишем долги" in seen["user"]
        monkeypatch.delenv("AI_API_KEY")
        assert client.post("/api/ai/generate", auth=AUTH, json={"domain": "x.ru"}).status_code == 503


def test_image_variants(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "ai2.db")
    monkeypatch.setenv("CREATIVE_IMAGES_DIR", str(tmp_path / "img"))
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), (200, 50, 50)).save(buf, "PNG")
    ref = creative_images.save_image(buf.getvalue())
    with TestClient(main_module.app) as client:
        r = client.post("/api/ai/image-variants", auth=AUTH, json={"image": ref, "count": 3})
    assert r.status_code == 200, r.text
    refs = r.json()["refs"]
    assert len(refs) == 3 and len(set(refs)) == 3 and all(x.startswith("local:") for x in refs)
