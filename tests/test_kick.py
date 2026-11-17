"""Понятные статусы «не идут» и «Пнуть» (2026-11-16)."""
from app import main as m


def test_explain_no_money_with_topup_reason():
    st = {"code": "stopped", "label": "Показы не идут", "detail": "Accepted by moderator", "ads": {}}
    note = ("Маркировка: ok; Пополнение не выполнено: {'message': 'Found errors', 'errors': [{'fieldPath': None, "
            "'type': 'PaymentError', 'code': 'UNKNOWN', 'message': 'Пополнение аккаунта недоступно до 13:43:35 14.11.2026', "
            "'details': None}]}; Запущено: на модерацию отправлено объявлений — 1")
    r = m.explain_status(st, 0, {"note": note, "topup_status": "ERROR", "started": True})
    assert r["code"] == "no_money" and "недоступно до 13:43:35 14.11.2026" in r["detail"]
    assert r["kick"] == {"topup": True, "start": True}


def test_explain_draft_start_failed():
    st = {"code": "draft", "label": "Черновик", "detail": "", "ads": {}}
    r = m.explain_status(st, 1000, {"note": "Пополнено на 1220 ₽ с НДС; Не запущено: Превышен лимит API Click.ru (429).",
                                     "topup_status": "SUCCESS", "started": False})
    assert r["code"] == "draft" and "429" in r["detail"] and r["kick"] == {"topup": False, "start": True}


def test_live_has_no_kick():
    assert m.explain_status({"code": "live"}, 0, None)["kick"] is None


def test_accepted_off_label():
    camp = {"State": "OFF", "Status": "ACCEPTED", "StatusPayment": "ALLOWED", "StatusClarification": ""}
    r = m.account_status({"state": "ACTIVE"}, camp, [{"Status": "ACCEPTED"}])
    assert r["code"] == "stopped" and r["label"] == "Одобрено, но кампания выключена"


def test_normalize_site_idn():
    assert m._normalize_site("мойюрист24.рф") == m._normalize_site("https://xn--24-vlcdompjj0j.xn--p1ai/path?x=1")
    assert m._normalize_site("www.Example.com") == "example.com"


def test_kick_plan_uses_zero_balance_without_direct(monkeypatch, tmp_path):
    import asyncio
    from fastapi.testclient import TestClient
    from app import db
    from test_bulk_campaign_create import _configure_test_env, _make_project
    _configure_test_env(monkeypatch, tmp_path / "kick.db")

    class C:
        async def accounts(self):
            return [{"id": 101, "state": "ACTIVE", "balance": 0}, {"id": 102, "state": "ACTIVE", "balance": 900},
                    {"id": 103, "state": "BAN", "balance": 0}]

    async def unknown(rows, refresh=False, budget=None):
        return {int(r["account_id"]): {"code": "unknown", "label": "Статус обновляется", "detail": "", "ads": {}} for r in rows}

    monkeypatch.setattr(m, "_resolve_clickru_client", lambda _c: C())
    monkeypatch.setattr(m, "_statuses_for", unknown)
    with TestClient(m.app) as client:
        p = _make_project(client, ("test-admin", "test-password-long-enough"))
        for aid in (101, 102, 103):
            db.record_bulk_campaign(p["id"], f"pool-{aid}", aid, 7000 + aid, connection_id=None, payer_id=1)
        r = client.post(f"/api/projects/{p['id']}/kick", auth=("test-admin", "test-password-long-enough"), json={"dry_run": True}).json()
    assert [x["account_id"] for x in r["items"]] == [101] and r["items"][0]["topup"] is True
    assert r["need_topup"] == 1 and r["items"][0]["status"]["code"] == "no_money"
