"""Справочник регионов на русском из app/geo_regions_ru.json (2026-11-10):
через Click.ru названия только английские, файл выгружен напрямую из Direct API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module
from test_bulk_campaign_create import _configure_test_env

AUTH = ("test-admin", "test-password-long-enough")


def test_geo_regions_come_from_bundled_russian_file(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "geo-bundled.db")

    async def no_clickru(*a, **kw):
        raise AssertionError("при наличии файла Click.ru не должен вызываться")

    monkeypatch.setattr(main_module.ClickRUClient, "accounts", no_clickru)
    with TestClient(main_module.app) as client:
        geo = client.get("/api/geo-regions", auth=AUTH).json()
    tree = geo["tree"]
    assert tree[0] == {"value": "225", "label": "Вся Россия"}
    labels = {n["label"]: n for n in tree}
    assert "Москва и область" in labels and "Санкт-Петербург и Ленинградская область" in labels
    moscow_cities = {c["label"] for c in labels["Москва и область"].get("children", [])}
    assert "Москва" in moscow_cities
    names = {i["id"]: i["name"] for i in geo["items"]}
    assert names[213] == "Москва" and names[2] == "Санкт-Петербург"
    assert len(tree) > 70  # субъекты РФ
