"""ИИ-тексты для запуска (2026-11-18): заголовки, тексты, уточнения, быстрые
ссылки, ключевые и минус-фразы — генерация с нуля или рерайт существующих.

API — Anthropic-совместимый прокси (claude-n-codex.com:8443, POST
/v1/messages). Ключ только в .env сервера:
    AI_API_KEY    — ключ (обязателен)
    AI_API_BASE   — по умолчанию https://claude-n-codex.com:8443
    AI_MODEL      — по умолчанию claude-sonnet-5

Картинки: генерации изображений у прокси нет (image-модели выключены), поэтому
«рерайт картинки» = уникализация (см. image_variants): лёгкая обрезка,
зеркало, яркость/контраст/насыщенность — для разных аккаунтов разные файлы.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import random
import re
from typing import Any

import httpx

# Лимиты Директа (символы)
LIMITS = {"headlines": 56, "ad_texts": 81, "callouts": 25, "sitelink_title": 30, "sitelink_description": 60}
FIELDS = ("headlines", "ad_texts", "callouts", "sitelinks", "keywords", "negative_keywords")
DEFAULT_COUNTS = {"headlines": 8, "ad_texts": 6, "callouts": 8, "sitelinks": 6, "keywords": 30, "negative_keywords": 30}


class AIError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("AI_API_KEY"))


def _base() -> str:
    return (os.getenv("AI_API_BASE") or "https://claude-n-codex.com:8443").rstrip("/")


def _model() -> str:
    return os.getenv("AI_MODEL") or "claude-sonnet-5"


async def complete(system: str, user: str, max_tokens: int = 4000, model: str | None = None) -> str:
    """Один запрос к /v1/messages; временные ошибки (5xx, «temporarily
    unavailable») — до 3 попыток."""
    key = os.getenv("AI_API_KEY")
    if not key:
        raise AIError("AI_API_KEY не задан в .env сервера")
    body = {"model": model or _model(), "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    last = ""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as http:
                r = await http.post(_base() + "/v1/messages", headers=headers, json=body)
        except httpx.HTTPError as exc:
            last = f"сеть: {exc}"
        else:
            try:
                data = r.json()
            except ValueError:
                data = {}
            if r.status_code == 200 and data.get("content"):
                return "".join(c.get("text", "") for c in data["content"] if c.get("type") == "text")
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            last = f"HTTP {r.status_code}: {err.get('message') or r.text[:200]}"
            if r.status_code < 500 and "temporarily" not in last and r.status_code != 429:
                raise AIError("ИИ-сервис: " + last)
        await asyncio.sleep(4 * (attempt + 1))
    raise AIError("ИИ-сервис недоступен: " + last)


def parse_json(text: str) -> dict[str, Any]:
    """Первый JSON-объект в ответе (модель иногда оборачивает в ```json)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise AIError("ИИ вернул не JSON")
    try:
        return json.loads(m.group(0))
    except ValueError as exc:
        raise AIError(f"ИИ вернул битый JSON: {exc}") from exc


SYSTEM = (
    "Ты — опытный специалист по Яндекс Директу. Пишешь рекламу на русском языке для поиска и РСЯ. "
    "Строго соблюдай лимиты символов (с пробелами), не используй эмодзи, кавычки-ёлочки в начале, "
    "КАПС, восклицательные знаки подряд, превосходные степени без подтверждения («лучший», «№1»), "
    "гарантии результата и запрещённые обещания. Отвечай ТОЛЬКО JSON-объектом без пояснений."
)


def build_prompt(fields: list[str], counts: dict[str, int], domain: str, topic: str, mode: str,
                 examples: dict[str, Any], extra: str = "") -> str:
    spec = {
        "headlines": f"заголовки объявления, каждый до {LIMITS['headlines']} символов",
        "ad_texts": f"тексты объявления, каждый до {LIMITS['ad_texts']} символов",
        "callouts": f"уточнения, каждое до {LIMITS['callouts']} символов",
        "sitelinks": (f"быстрые ссылки: объекты {{\"title\": до {LIMITS['sitelink_title']} символов, "
                      f"\"description\": до {LIMITS['sitelink_description']} символов, \"anchor\": "
                      "короткий якорь латиницей для ссылки вида сайт/#anchor}"),
        "keywords": "ключевые фразы для поиска, 2–5 слов, без операторов, коммерческие и информационно-коммерческие",
        "negative_keywords": "минус-слова (по одному слову, без «-»), отсекающие нецелевой трафик: бесплатно, скачать, вакансии, реферат и т.п. по тематике",
    }
    lines = [f"Сайт: {domain}", f"Тематика / оффер: {topic or 'определи по домену и примерам'}"]
    if extra:
        lines.append(f"Дополнительно: {extra}")
    if mode == "rewrite":
        lines.append("Задача: РЕРАЙТ — перепиши примеры другими словами, сохранив смысл и оффер; "
                     "варианты не должны повторять друг друга и исходник.")
    else:
        lines.append("Задача: придумай новые варианты.")
    ex = {k: v for k, v in (examples or {}).items() if v}
    if ex:
        lines.append("Примеры / исходные тексты:\n" + json.dumps(ex, ensure_ascii=False)[:6000])
    lines.append("Нужно вернуть JSON с ключами:")
    for f in fields:
        lines.append(f"- \"{f}\": массив из {counts[f]} элементов — {spec[f]}")
    return "\n".join(lines)


def _clean_text(s: Any) -> str:
    s = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", "", str(s or ""))
    return " ".join(s.replace("\n", " ").split()).strip(" «»\"'")


def clean_result(raw: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Отбрасываем то, что не влезает в лимиты Директа (не обрезаем — обрезанный
    текст хуже), убираем дубли."""
    out: dict[str, Any] = {}
    for f in fields:
        items = raw.get(f) or []
        if not isinstance(items, list):
            items = []
        if f == "sitelinks":
            seen, sl = set(), []
            for it in items:
                if not isinstance(it, dict):
                    continue
                t, d = _clean_text(it.get("title")), _clean_text(it.get("description"))
                a = re.sub(r"[^a-z0-9_-]", "", str(it.get("anchor") or "").lower())[:40]
                if not t or len(t) > LIMITS["sitelink_title"] or t.lower() in seen:
                    continue
                if len(d) > LIMITS["sitelink_description"]:
                    d = ""
                seen.add(t.lower())
                sl.append({"title": t, "description": d, "anchor": a})
            out[f] = sl
            continue
        vals = [_clean_text(x) for x in items]
        if f == "negative_keywords":
            vals = [v.lstrip("-").strip().lower() for v in vals]
        if f == "keywords":
            vals = [re.sub(r"[^\w\s-]", " ", v).lower() for v in vals]
            vals = [" ".join(v.split()) for v in vals if 1 <= len(v.split()) <= 7]
        lim = LIMITS.get(f)
        if lim:
            vals = [v for v in vals if len(v) <= lim]
        out[f] = list(dict.fromkeys(v for v in vals if v))
    return out


async def generate(fields: list[str], domain: str, topic: str = "", mode: str = "new",
                   examples: dict[str, Any] | None = None, counts: dict[str, int] | None = None,
                   extra: str = "") -> dict[str, Any]:
    fields = [f for f in fields if f in FIELDS] or list(FIELDS)
    c = {f: int((counts or {}).get(f) or DEFAULT_COUNTS[f]) for f in fields}
    text = await complete(SYSTEM, build_prompt(fields, c, domain, topic, mode, examples or {}, extra),
                          max_tokens=6000)
    return clean_result(parse_json(text), fields)


def image_variants(data: bytes, count: int = 3, seed: int | None = None) -> list[bytes]:
    """Уникализация картинки: у каждого варианта своя лёгкая обрезка (2–7 %),
    иногда зеркало, сдвиг яркости/контраста/насыщенности ±4–10 %. Результат —
    JPEG; под требования Директа его потом подгоняет fit_for_direct."""
    from PIL import Image, ImageEnhance

    rnd = random.Random(seed)
    src = Image.open(io.BytesIO(data))
    src.load()
    src = src.convert("RGB")
    out: list[bytes] = []
    for i in range(max(1, min(count, 10))):
        img = src.copy()
        w, h = img.size
        cl, ct = rnd.uniform(0.01, 0.035), rnd.uniform(0.01, 0.035)
        cr, cb = rnd.uniform(0.01, 0.035), rnd.uniform(0.01, 0.035)
        img = img.crop((int(w * cl), int(h * ct), int(w * (1 - cr)), int(h * (1 - cb))))
        if rnd.random() < 0.5:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        for enh in (ImageEnhance.Brightness, ImageEnhance.Contrast, ImageEnhance.Color):
            img = enh(img).enhance(1 + rnd.choice((-1, 1)) * rnd.uniform(0.04, 0.10))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=rnd.randint(86, 93))
        out.append(buf.getvalue())
    return out
