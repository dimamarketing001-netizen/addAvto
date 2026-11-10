"""Юрлица: у физлиц/ИП Click.ru не отдаёт name — собираем ФИО (2026-11-10)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module
from test_bulk_campaign_create import _configure_test_env

AUTH = ("test-admin", "test-password-long-enough")


def test_payers_get_fio_name(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "payers.db")

    async def payers(self):
        return [
            {"id": 1480787, "type": "PERSON", "lastName": "Муфатыков", "firstName": "Роман", "middleName": "Маратович", "inn": "1", "balance": 0},
            {"id": 99034, "type": "BUSINESS", "name": "ООО \"ЮРИСТ ДЛЯ ЛЮДЕЙ\"", "balance": -6000.15},
            {"id": 5, "type": "INDIVIDUAL", "lastName": "Иванов", "firstName": "Иван", "middleName": ""},
        ]

    monkeypatch.setattr(main_module.ClickRUClient, "payers", payers)
    with TestClient(main_module.app) as client:
        items = {p["id"]: p for p in client.get("/api/payers", auth=AUTH).json()["items"]}
    assert items[1480787]["name"] == "Муфатыков Роман Маратович"
    assert items[99034]["name"] == "ООО \"ЮРИСТ ДЛЯ ЛЮДЕЙ\""
    assert items[5]["name"] == "Иванов Иван"
