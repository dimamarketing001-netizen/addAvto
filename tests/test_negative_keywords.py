from app.main import _split_negative_keywords


def test_comma_list_on_one_line_is_split():
    text = "бесплатно\nТ-Банк, Платинум, банк тиньков.\n-скачать; вакансии\n\nбесплатно"
    assert _split_negative_keywords(text) == ["бесплатно", "Т Банк", "Платинум", "банк тиньков", "скачать", "вакансии"]


def test_only_allowed_characters_left():
    import re
    out = _split_negative_keywords("зао «ткс»/банк, 100% кредит, !как +на \"авто\", [а б]")
    assert out and all(re.fullmatch(r"[\w \"\[\]+!]+", x) for x in out), out
    assert "100 кредит" in out


def test_long_and_empty_phrases_dropped():
    assert _split_negative_keywords(" , ;\n - \n один два три четыре пять шесть семь восемь") == []
