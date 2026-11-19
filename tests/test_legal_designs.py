"""Юр. направления: 15 дизайнов, подуслуги (services), ?s= предвыбор, виджеты."""
import json
import re

from app import legal_designs, site_designs

SVC = [{"id": "alimenty", "t": "Алименты", "d": "взыскать", "title": "Взыщем алименты", "subtitle": "Под",
        "subs": ["Помощь при взыскании алиментов"], "steps": [{"q": "Есть решение суда?", "options": ["Да", "Нет"]}]},
       {"id": "razvod", "t": "Развод", "title": "Развод через суд", "steps": []}]
BASE = {"title": "Семейные споры", "subtitle": "S", "steps": [{"q": "Когда?", "options": ["Срочно", "Нет"]}], "services": SVC,
        "faq": [{"q": "Q", "a": "A"}], "how": [{"t": "T", "d": "D"}], "trust": [{"n": "1", "t": "t"}], "bullets": ["b"]}


def data(html):
    return json.loads(re.search(r'<script id="qd" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_all_legal_designs_render():
    assert set(legal_designs.DESIGNS) <= set(site_designs.DESIGNS)
    for d in legal_designs.DESIGNS:
        cfg = {**BASE, "design": d, "widget": {"type": "chips", "title": "W", "items": ["a"]}}
        html = site_designs.render(cfg, "x.ru", [123])
        assert "Семейные споры" in html and f"d-{d}" in html
        assert data(html)["services"][0]["id"] == "alimenty" and data(html)["preset"] == -1


def test_service_preset_changes_title_and_preselects():
    html = site_designs.render({**BASE, "design": "accordion"}, "x.ru", [], service="alimenty")
    assert '<h1 class="h-title">Взыщем алименты</h1>' in html
    assert data(html)["preset"] == 0
    html = site_designs.render({**BASE, "design": "accordion"}, "x.ru", [], service="nope")
    assert data(html)["preset"] == -1 and "Семейные споры</h1>" in html


def test_service_links_and_ids_sanitized():
    cfg = {**BASE, "design": "situations", "services": [{"id": "a b<script>", "t": "X"}, {"t": "Y"}]}
    c = site_designs._ctx(cfg)
    assert [s["id"] for s in c["services"]] == ["abscript", "s2"]
    html = site_designs.render(cfg, "x.ru", [])
    assert 'href="?s=abscript" data-svc="0"' in html


def test_widget_and_form_flags_in_data():
    cfg = {**BASE, "design": "booking", "form_booking": True, "form_messenger": True,
           "widget": {"type": "deadline", "title": "Срок", "label": "Дата", "months": 6, "ok": "{n}", "late": "{n}"}}
    d = data(site_designs.render(cfg, "x.ru", []))
    assert d["form_booking"] and d["form_messenger"] and d["widget"]["months"] == 6
    assert "data-w" in site_designs.render({**cfg, "design": "deadline"}, "x.ru", [])
