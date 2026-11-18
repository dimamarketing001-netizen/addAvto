from __future__ import annotations

from fastapi.testclient import TestClient

from app import crm_stats, site_designs as sd
from app import main as main_module
from tests.test_bulk_campaign_create import _configure_test_env

AUTH = ("test-admin", "test-password-long-enough")
CFG = {"title": "Списать долги <законно>", "subtitle": "Подзаголовок", "badge": "ФЗ-127", "bullets": ["a", "b"], "cta": "Проверить",
       "steps": [{"q": "Сумма долга?", "hint": "примерно", "options": [{"t": "до 500 тыс", "d": "x"}, {"t": "больше"}]},
                 {"q": "Есть ипотека?", "options": ["Да", "Нет"]}],
       "slider": {"label": "Сумма", "min": 25000, "max": 5000000, "step": 25000, "unit": "₽", "default": 500000},
       "trust": [{"n": "15 минут", "t": "разбор"}], "how": [{"t": "Шаг", "d": "описание"}], "faq": [{"q": "Вопрос?", "a": "Ответ"}],
       "result_text": "Итог", "thanks": "Спасибо", "chat_greeting": "Здравствуйте", "operator": "Анна Петрова",
       "brand": "Мой юрист", "legal_name": "ООО «Мой юрист»", "inn": "6658577091", "phone": "8 (800) 505-67-97", "email": "a@b.ru"}


def test_all_designs_render():
    assert len(sd.DESIGNS) == 10
    for name in sd.DESIGNS:
        html = sd.render({**CFG, "design": name, "direction": "bfl"}, "x.ru", [77])
        assert "Списать долги &lt;законно&gt;" in html and "__YM_IDS=[77]" in html and f'class="d-{name}"' in html
        assert "tel:88005056797" in html and "ИНН 6658577091" in html and 'href="/privacy"' in html
        assert '"Сумма долга?"' in html and "</script><script>" in html
    assert "data-chat" in sd.render({**CFG, "design": "chat"}) and "data-calc" in sd.render({**CFG, "design": "calc"})
    assert 'data-all="select"' in sd.render({**CFG, "design": "official"}) and "fl-ov" in sd.render({**CFG, "design": "flow"})
    # строковые варианты превращаются в объекты, «</» в данных не ломает скрипт
    h = sd.render({**CFG, "design": "tiles", "title": "</script><b>x"})
    assert "<\/script><b>x" in h
    p = sd.privacy(CFG, "x.ru")
    assert "ООО «Мой юрист»" in p and "152-ФЗ" in p


def test_design_site_lead(tmp_path, monkeypatch):
    _configure_test_env(monkeypatch, tmp_path / "designs.db")
    pushed = []

    async def fake_call(webhook, method, params):
        pushed.append((method, params))
        return {"result": 55}

    monkeypatch.setattr(crm_stats, "_call", fake_call)
    monkeypatch.setattr(crm_stats, "_source", lambda sid, owner=None: {"id": sid})
    monkeypatch.setattr(crm_stats, "_webhook", lambda src: "https://b24.example/x")
    with TestClient(main_module.app) as client:
        integ = client.post("/api/lead-integrations", auth=AUTH, json={"name": "МЮ — БФЛ", "kind": "mu", "source_id": 2,
                                                                       "settings": {"title_prefix": "БФЛ", "lead_type_id": "70"}}).json()
        tpl = client.post("/api/site-templates", auth=AUTH, json={"name": "БФЛ 01", "kind": "design", "config": {
            **CFG, "design": "chat", "direction": "bfl", "integration_id": integ["id"], "quiz_title": "БФЛ: МФЦ"}}).json()
        meta = client.get("/api/site-templates", auth=AUTH).json()
        assert "chat" in meta["designs"] and meta["directions"]["bfl"] == "БФЛ"
        client.put("/api/sites", auth=AUTH, json={"domain": "d1.ru", "template_id": tpl["id"]})
        h = {"host": "d1.ru"}
        assert "data-chat" in client.get("/", headers=h).text
        assert "Политика" in client.get("/privacy", headers=h).text
        r = client.post("/lead", headers={**h, "x-real-ip": "7.7.7.7"}, json={
            "name": "Олег", "phone": "+7 (912) 111-22-33", "city": "Челябинск", "quiz_title": "БФЛ: МФЦ",
            "answers": [{"q": "Сумма долга?", "a": "больше"}], "utm": {"utm_campaign": "rsya_5"}, "page": "https://d1.ru/?utm_campaign=rsya_5"})
        assert r.status_code == 200
        f = pushed[-1][1]["fields"]
        assert f["TITLE"] == "БФЛ | Олег" and f["UF_CRM_1730379820755"] == "Челябинск" and f["UF_CRM_1779024295"] == "60"
        assert "• Сумма долга?: больше" in f["COMMENTS"] and "«БФЛ: МФЦ»" in f["COMMENTS"]
