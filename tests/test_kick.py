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
