"""
ozon_ad_creator.py
==================

Парсер карточки товара Ozon + генерация продающих рекламных текстов через Gemini API.

Что делает:
  1. Принимает ссылку на товар Ozon.
  2. Достаёт заголовок, описание, характеристики, цену, бренд и все изображения.
  3. Скачивает изображения в папку.
  4. Отправляет данные в Gemini с промтом «профессионального маркетолога»
     и получает структурированный JSON: заголовки и тексты для Яндекс.Директа,
     VK Рекламы, новый заголовок/описание карточки, УТП, буллеты и т.д.

Стек совместим с FastAPI-проектом: async, httpx, Pydantic v2.

Зависимости:
    pip install "httpx>=0.27" "pydantic>=2" curl_cffi playwright
    # Playwright использует установленный Google Chrome (channel="chrome").
    # Если Chrome на сервере нет:  playwright install --with-deps chromium  и  OZON_BROWSER_CHANNEL=""

Режимы получения данных (OZON_FETCH_MODE / --mode):
    auto    — быстрый HTTP-запрос; если Ozon ответил 403 — автоматически браузер (по умолчанию)
    http    — только HTTP (быстро, но Ozon часто блокирует)
    browser — сразу Playwright: открывает карточку, затем берёт JSON API изнутри страницы

Настройки через переменные окружения:
    GEMINI_API_KEY        — токен Gemini (или впишите в GEMINI_API_KEY_DEFAULT ниже)
    GEMINI_MODEL          — модель (по умолчанию gemini-3.8-flash — победитель бенчмарка)
    GEMINI_FALLBACK_MODELS— запасные модели через запятую (по умолчанию
                            gemini-3.7-flash,gemini-flash-latest,gemini-3.5-flash; "" — отключить)
    GEMINI_PROXY          — прокси для Gemini API
    OZON_FETCH_MODE       — auto | http | browser
    OZON_HEADLESS         — 0 (по умолчанию, видимое окно) / 1 — headless (Ozon его блокирует)
    OZON_BROWSER_CHANNEL  — chrome (по умолчанию) / "" — встроенный Chromium Playwright
    OZON_COOKIES          — строка Cookie для HTTP-режима
    OZON_PROXY            — прокси, например http://user:pass@host:port

CLI:
    python ozon_ad_creator.py "https://www.ozon.ru/product/...-5714492571/"
    python ozon_ad_creator.py URL --out ./result --no-images
    python ozon_ad_creator.py URL --no-ai                  # только парсинг
    python ozon_ad_creator.py URL --mode browser --headful # браузер с видимым окном

Интеграция в FastAPI (пример внизу файла, функция build_router()):
    from ozon_ad_creator import process_ozon_url
    result = await process_ozon_url(url, download=False)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from html import unescape
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError

try:  # curl_cffi имитирует TLS-отпечаток Chrome — Ozon пропускает его намного охотнее
    from curl_cffi.requests import AsyncSession as CurlAsyncSession  # type: ignore

    HAS_CURL_CFFI = True
except Exception:  # pragma: no cover
    HAS_CURL_CFFI = False

try:  # Playwright — режим настоящего браузера (Ozon пропускает его там, где HTTP-клиент получает 403)
    from playwright.async_api import async_playwright  # type: ignore

    HAS_PLAYWRIGHT = True
except Exception:  # pragma: no cover
    HAS_PLAYWRIGHT = False


def _browser_not_installed(exc: Exception) -> bool:
    """Ошибка Playwright «нет такого браузера» (а не сбой уже запущенного):
    'Chromium distribution 'chrome' is not found at ...' / 'Executable
    doesn't exist at ...'."""
    text = str(exc)
    return "is not found at" in text or "Executable doesn't exist" in text


# ============================================================================
#                               НАСТРОЙКИ
# ============================================================================

GEMINI_API_KEY_DEFAULT = ""  # <-- можно вписать токен сюда, но лучше через env GEMINI_API_KEY
# Выбрано по бенчмарку (ноябрь 2026): лучшая точность фактов, попадание в интент и 0 нарушений лимитов.
GEMINI_MODEL_DEFAULT = "gemini-3.8-flash"
# Запасные модели — используются по очереди, если основная перегружена (503), упёрлась в квоту (429)
# или недоступна (404). Переопределяется env GEMINI_FALLBACK_MODELS="m1,m2".
GEMINI_FALLBACK_MODELS_DEFAULT = ("gemini-3.7-flash", "gemini-flash-latest", "gemini-3.5-flash")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

OZON_BASE = "https://www.ozon.ru"
OZON_API_PATHS = (
    "/api/entrypoint-api.bx/page/json/v2",
    "/api/composer-api.bx/page/json/v2",
)

# Режим получения данных: auto (HTTP, при блокировке — браузер) | http | browser
OZON_FETCH_MODE_DEFAULT = "auto"
# Браузер для Playwright: "chrome" — установленный Google Chrome, "" — встроенный Chromium Playwright
OZON_BROWSER_CHANNEL_DEFAULT = "chrome"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.ozon.ru/",
    "Origin": "https://www.ozon.ru",
}

# Лимиты рекламных площадок (символы). Используются в промте и при валидации ответа.
LIMITS = {
    "yandex_title1": 56,
    "yandex_title2": 30,
    "yandex_text": 81,
    "yandex_callout": 25,
    "vk_title": 40,
    "vk_text": 220,
    "card_title": 200,
    "bullet": 60,
    "headline": 60,
    "banner": 35,
    "cta": 25,
}

# Слова/обороты, которые режет модерация Директа/VK или которые являются недоказуемыми обещаниями.
# Проверяются в коде; тексты с ними автоматически отправляются на переписывание.
STOP_PATTERNS = [
    r"\bлучш\w*", r"\bсам(ый|ая|ое|ые|ого|ой)\b", r"№\s*1\b", r"\bномер\s+один\b", r"\bхит\w*",
    r"\bжми(те)?\b", r"\bкликай\w*", r"\bуникальн\w*", r"\bгарантир\w*", r"\bгаранти[яи]\w*",
    r"\bбесплатн\w*", r"\bкачествен\w*", r"\bидеальн\w*", r"\bположительн\w*\s+отзыв\w*",
    r"\bбыстр\w*\s+доставк\w*", r"!{2,}",
]


class OzonParseError(RuntimeError):
    pass


class GeminiError(RuntimeError):
    pass


# ============================================================================
#                               МОДЕЛИ ДАННЫХ
# ============================================================================


class OzonProduct(BaseModel):
    url: str
    sku: str | None = None
    title: str = ""
    description: str = ""
    brand: str | None = None
    price: str | None = None
    old_price: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    characteristics: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    local_images: list[str] = Field(default_factory=list)


class YandexDirectAd(BaseModel):
    keyword_group: str = Field(default="", description="Группа ключевых фраз (интент), под которую объявление")
    title1: str = Field(description=f"Заголовок 1, до {LIMITS['yandex_title1']} символов")
    title2: str = Field(description=f"Заголовок 2, до {LIMITS['yandex_title2']} символов")
    text: str = Field(description=f"Текст объявления, до {LIMITS['yandex_text']} символов")
    angle: str = Field(description="Какой маркетинговый угол использован")


class VkAd(BaseModel):
    title: str
    text: str
    angle: str


class KeywordGroup(BaseModel):
    name: str
    intent: str = ""
    phrases: list[str] = Field(default_factory=list)


class AdCopy(BaseModel):
    facts: list[str] = Field(default_factory=list, description="Проверяемые факты из данных и фото")
    big_idea: str = ""
    product_summary: str = ""
    target_audience: list[str] = Field(default_factory=list)
    pains: list[str] = Field(default_factory=list)
    usp: list[str] = Field(default_factory=list)
    card_title: str = ""
    card_description: str = ""
    bullets: list[str] = Field(default_factory=list)
    headlines: list[str] = Field(default_factory=list)
    yandex_direct: list[YandexDirectAd] = Field(default_factory=list)
    yandex_callouts: list[str] = Field(default_factory=list, description="Уточнения Директа")
    vk_ads: list[VkAd] = Field(default_factory=list)
    keyword_groups: list[KeywordGroup] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, description="Плоский список, собирается из keyword_groups")
    negative_keywords: list[str] = Field(default_factory=list)
    banner_texts: list[str] = Field(default_factory=list)
    cta: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="Заполняется скриптом")
    model_used: str = Field(default="", description="Какая модель реально сгенерировала тексты")


class ProcessResult(BaseModel):
    product: OzonProduct
    ad_copy: AdCopy | None = None
    output_dir: str | None = None


# ============================================================================
#                               ПАРСИНГ OZON
# ============================================================================


def _product_path(url: str) -> str:
    """Из полной ссылки делаем чистый путь /product/slug-123/ (без трекинговых параметров)."""
    parsed = urlparse(url.strip())
    if "ozon." not in parsed.netloc:
        raise OzonParseError(f"Это не ссылка Ozon: {url}")
    path = parsed.path
    if not path.startswith("/product/"):
        raise OzonParseError(f"Ссылка не похожа на карточку товара: {url}")
    if not path.endswith("/"):
        path += "/"
    return path


def _extract_sku(path: str) -> str | None:
    m = re.search(r"-(\d{5,})/?$", path) or re.search(r"/(\d{5,})/?$", path)
    return m.group(1) if m else None


def _clean_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>|</li\s*>|</h\d\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _original_image(src: str) -> str:
    """ir.ozone.ru/s3/multimedia-x/wc1000/123.jpg -> оригинал без ресайза."""
    return re.sub(r"/(?:wc|c)\d+/", "/", src, count=1)


def _walk(obj: Any):
    """Рекурсивный обход JSON (для поиска значений в непредсказуемой структуре виджетов)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _rich_to_text(node: Any) -> str:
    """Преобразование richAnnotationJson (блочный формат Ozon) в текст."""
    parts: list[str] = []
    for d in _walk(node):
        for key in ("text", "content"):
            val = d.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(_clean_html(val))
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item.strip():
                        parts.append(_clean_html(item))
    # убираем дубли, сохраняя порядок
    seen: set[str] = set()
    uniq = [p for p in parts if not (p in seen or seen.add(p))]
    return "\n".join(uniq).strip()


class _OzonHttp:
    """Обёртка: curl_cffi (если установлен) или httpx — единый async-интерфейс .get_text()."""

    def __init__(self, cookies: str | None = None, proxy: str | None = None, timeout: float = 30.0):
        self.headers = dict(BROWSER_HEADERS)
        if cookies:
            self.headers["Cookie"] = cookies
        self.proxy = proxy or None
        self.timeout = timeout
        self._curl = None
        self._httpx: httpx.AsyncClient | None = None

    async def __aenter__(self):
        if HAS_CURL_CFFI:
            self._curl = CurlAsyncSession(
                impersonate="chrome",
                headers=self.headers,
                proxies={"http": self.proxy, "https": self.proxy} if self.proxy else None,
                timeout=self.timeout,
            )
        else:
            self._httpx = httpx.AsyncClient(
                headers=self.headers,
                proxy=self.proxy,
                timeout=self.timeout,
                follow_redirects=True,
                http2=False,
            )
        return self

    async def __aexit__(self, *exc):
        if self._curl is not None:
            await self._curl.close()
        if self._httpx is not None:
            await self._httpx.aclose()

    async def get(self, url: str) -> tuple[int, str]:
        if self._curl is not None:
            r = await self._curl.get(url, allow_redirects=True)
            return r.status_code, r.text
        assert self._httpx is not None
        r = await self._httpx.get(url)
        return r.status_code, r.text

    async def get_bytes(self, url: str) -> bytes:
        if self._curl is not None:
            r = await self._curl.get(url)
            r.raise_for_status()
            return r.content
        assert self._httpx is not None
        r = await self._httpx.get(url)
        r.raise_for_status()
        return r.content


def _looks_like_antibot(status: int, text: str) -> bool:
    if status in (403, 429, 307):
        return True
    low = text[:5000].lower()
    return "antibot" in low or ("challenge" in low and "<html" in low)


async def _fetch_page_json(http: _OzonHttp, page_path: str) -> dict[str, Any] | None:
    last_err = ""
    for api in OZON_API_PATHS:
        api_url = f"{OZON_BASE}{api}?url={quote(page_path, safe='')}"
        try:
            status, text = await http.get(api_url)
        except Exception as e:  # сеть / таймаут
            last_err = str(e)
            continue
        if status == 200 and text.lstrip().startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                last_err = str(e)
                continue
        last_err = f"HTTP {status}" + (" (антибот)" if _looks_like_antibot(status, text) else "")
    if last_err:
        print(f"[ozon] API не ответил для {page_path}: {last_err}", file=sys.stderr)
    return None


def _widget_states(page: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (page.get("widgetStates") or {}).items():
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except json.JSONDecodeError:
                continue
        elif isinstance(v, dict):
            out[k] = v
    return out


def _apply_ld_json(product: OzonProduct, ld: dict[str, Any]) -> None:
    if not isinstance(ld, dict):
        return
    if ld.get("@type") not in (None, "Product"):
        return
    if not product.title and ld.get("name"):
        product.title = _clean_html(str(ld["name"]))
    if not product.description and ld.get("description"):
        product.description = _clean_html(str(ld["description"]))
    brand = ld.get("brand")
    if not product.brand and brand:
        product.brand = brand.get("name") if isinstance(brand, dict) else str(brand)
    img = ld.get("image")
    if img:
        for src in img if isinstance(img, list) else [img]:
            if isinstance(src, str):
                product.images.append(src)
    offers = ld.get("offers")
    if isinstance(offers, dict) and not product.price and offers.get("price"):
        product.price = f"{offers['price']} {offers.get('priceCurrency', 'RUB')}"
    agg = ld.get("aggregateRating")
    if isinstance(agg, dict):
        try:
            product.rating = product.rating or float(agg.get("ratingValue"))
            product.reviews_count = product.reviews_count or int(agg.get("reviewCount"))
        except (TypeError, ValueError):
            pass
    if not product.sku and ld.get("sku"):
        product.sku = str(ld["sku"])


def _parse_seo(product: OzonProduct, page: dict[str, Any]) -> None:
    """В ответе API есть seo.script с JSON-LD — надёжный источник name/description/image."""
    seo = page.get("seo") or {}
    for script in seo.get("script") or []:
        inner = script.get("innerHTML") if isinstance(script, dict) else None
        if not inner:
            continue
        try:
            _apply_ld_json(product, json.loads(inner))
        except json.JSONDecodeError:
            continue
    if not product.title:
        title = seo.get("title")
        if isinstance(title, str):
            product.title = title.split(" купить", 1)[0].strip()


def _parse_widgets(product: OzonProduct, states: dict[str, Any]) -> None:
    for key, data in states.items():
        name = key.split("-", 1)[0]

        if name == "webProductHeading" and data.get("title"):
            product.title = _clean_html(data["title"])

        elif name == "webGallery":
            for img in data.get("images") or []:
                src = img.get("src") if isinstance(img, dict) else None
                if src:
                    product.images.append(src)
            cover = data.get("coverImage")
            if isinstance(cover, str):
                product.images.insert(0, cover)

        elif name == "webDescription":
            text = ""
            rich = data.get("richAnnotation")  # основной формат: HTML-строка (richAnnotationType=HTML)
            if isinstance(rich, str) and rich.strip():
                text = _clean_html(rich)
            elif isinstance(rich, (dict, list)):
                text = _rich_to_text(rich)
            if not text and data.get("richAnnotationJson"):
                text = _rich_to_text(data["richAnnotationJson"])
            if not text and isinstance(data.get("annotation"), str):
                text = _clean_html(data["annotation"])
            if not text and data.get("characteristics"):
                text = _rich_to_text(data["characteristics"])
            if text and len(text) > len(product.description):
                product.description = text

        elif name in ("webCharacteristics", "webShortCharacteristics"):
            for d in _walk(data):
                # формат: {"name"/"key": "...", "values": [{"text": "..."}]}
                label = d.get("name") or d.get("title")
                if isinstance(label, dict):
                    # webShortCharacteristics со страницы (2026-11-10):
                    # {"title": {"textRs": [{"content": "Цвет"}]}, "values": [...]}
                    label = "".join(
                        str(t.get("content") or "") for t in label.get("textRs") or [] if isinstance(t, dict)
                    ) or None
                values = d.get("values")
                if isinstance(label, str) and isinstance(values, list):
                    vals = [v.get("text") for v in values if isinstance(v, dict) and v.get("text")]
                    if vals:
                        product.characteristics[_clean_html(label)] = ", ".join(vals)

        elif name == "webPrice":
            product.price = product.price or data.get("price") or data.get("cardPrice")
            product.old_price = product.old_price or data.get("originalPrice")

        elif name in ("webBrand", "webStoreBrand") and not product.brand:
            title = (data.get("content") or {}).get("title")
            product.brand = data.get("name") or (title.get("text") if isinstance(title, dict) else title)


async def _fallback_html(http: _OzonHttp, product: OzonProduct, path: str) -> None:
    """Если API закрыт — пробуем HTML страницы: JSON-LD + og:теги."""
    status, html = await http.get(f"{OZON_BASE}{path}")
    if status != 200:
        return
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            _apply_ld_json(product, json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    if not product.title:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
        if m:
            product.title = unescape(m.group(1))
    if not product.images:
        product.images += re.findall(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)


class OzonBrowser:
    """Настоящий браузер через Playwright.

    Открывает карточку товара (браузер проходит JS-проверку Ozon и получает cookies),
    затем запрашивает тот же JSON API изнутри страницы через fetch().

    Можно держать один экземпляр на всё приложение (FastAPI lifespan), чтобы не запускать
    браузер на каждый запрос:
        browser = OzonBrowser(); await browser.start()
        product = await parse_ozon_product(url, browser=browser)
        await browser.close()
    """

    _JS_FETCH = """async (paths) => {
        const out = [];
        for (const p of paths) {
            try {
                const r = await fetch('/api/entrypoint-api.bx/page/json/v2?url=' + encodeURIComponent(p),
                                      {credentials: 'include', headers: {'Accept': 'application/json'}});
                out.push({status: r.status, text: await r.text()});
            } catch (e) { out.push({status: 0, text: String(e)}); }
        }
        return out;
    }"""

    def __init__(
        self,
        *,
        headless: bool | None = None,
        channel: str | None = None,
        proxy: str | None = None,
        timeout_ms: int = 45000,
    ):
        if headless is None:
            # По умолчанию окно видимое: headless-Chrome Ozon блокирует (проверено — 403).
            # На Linux-сервере без экрана запускайте через Xvfb: xvfb-run python ...
            headless = os.getenv("OZON_HEADLESS", "0").lower() not in ("0", "false", "no")
        self.headless = headless
        self.channel = channel if channel is not None else os.getenv("OZON_BROWSER_CHANNEL", OZON_BROWSER_CHANNEL_DEFAULT)
        self.proxy = (proxy if proxy is not None else os.getenv("OZON_PROXY")) or None
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self._lock = asyncio.Lock()

    async def start(self) -> "OzonBrowser":
        if not HAS_PLAYWRIGHT:
            raise OzonParseError(
                "Режим браузера требует Playwright: pip install playwright "
                "(и либо установленный Google Chrome, либо `playwright install chromium`)"
            )
        if self._context is not None:
            return self
        self._pw = await async_playwright().start()
        launch: dict[str, Any] = {
            "headless": self.headless,
            "args": ["--disable-blink-features=AutomationControlled", "--lang=ru-RU"],
        }
        if self.proxy:
            launch["proxy"] = {"server": self.proxy}
        if self.channel:
            try:
                self._browser = await self._pw.chromium.launch(channel=self.channel, **launch)
            except Exception as exc:
                # 2026-11-10, прод: OZON_BROWSER_CHANNEL=chrome, а в
                # Docker-образе только встроенный Chromium Playwright (см.
                # Dockerfile) — запуск падал «Chromium distribution 'chrome'
                # is not found», и обход антибота Ozon не работал вовсе.
                # Нет заказанного браузера — берём встроенный, а не 500.
                if not _browser_not_installed(exc):
                    raise
                print(
                    f"[ozon] браузер channel={self.channel!r} не установлен — "
                    "запускаю встроенный Chromium Playwright",
                    file=sys.stderr,
                )
                self.channel = ""
        if self._browser is None:
            self._browser = await self._pw.chromium.launch(**launch)
        self._context = await self._browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 900},
        )
        return self

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._context = None

    async def __aenter__(self) -> "OzonBrowser":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def fetch_pages(self, path: str, extra_paths: list[str]) -> list[dict[str, Any] | None]:
        """Открывает карточку и возвращает JSON для [path, *extra_paths] (None — если не удалось)."""
        await self.start()
        assert self._context is not None
        async with self._lock:  # одна вкладка за раз — меньше шансов нарваться на rate limit
            page = await self._context.new_page()
            try:
                await page.goto(f"{OZON_BASE}{path}", wait_until="domcontentloaded", timeout=self.timeout_ms)
                try:  # ждём, пока пройдёт проверка и отрисуется карточка
                    await page.wait_for_function(
                        "() => !!document.querySelector('[data-widget=\"webProductHeading\"], h1')",
                        timeout=self.timeout_ms,
                    )
                except Exception:
                    print("[ozon/browser] карточка не отрисовалась, пробуем API всё равно", file=sys.stderr)
                raw = await page.evaluate(self._JS_FETCH, [path, *extra_paths])
            finally:
                await page.close()

        out: list[dict[str, Any] | None] = []
        for p, item in zip([path, *extra_paths], raw):
            text = item.get("text") or ""
            if item.get("status") == 200 and text.lstrip().startswith("{"):
                try:
                    out.append(json.loads(text))
                    continue
                except json.JSONDecodeError:
                    pass
            print(f"[ozon/browser] API не ответил для {p}: HTTP {item.get('status')}", file=sys.stderr)
            out.append(None)
        return out


def _apply_pages(product: OzonProduct, page1: dict[str, Any] | None, page2: dict[str, Any] | None) -> None:
    if page1:
        _parse_widgets(product, _widget_states(page1))
        _parse_seo(product, page1)
    if page2:
        _parse_widgets(product, _widget_states(page2))


async def parse_ozon_product(
    url: str,
    *,
    cookies: str | None = None,
    proxy: str | None = None,
    mode: str | None = None,
    browser: OzonBrowser | None = None,
    remote_fetch: Callable[[str, list[str]], Awaitable[list[dict[str, Any] | None]]] | None = None,
) -> OzonProduct:
    """Главная функция парсинга. Возвращает OzonProduct (без скачивания картинок).

    mode: "auto" — сначала быстрый HTTP, при блокировке — браузер (по умолчанию);
          "http" — только HTTP; "browser" — сразу браузер.
    browser: общий экземпляр OzonBrowser (для FastAPI), иначе браузер запускается на один вызов.
    remote_fetch: (path, extra_paths) -> страницы {widgetStates, seo} — внешний
          настоящий браузер (addAvto: Chrome в WSL через parsya-control). Если
          задан, используется вместо HTTP и локального браузера: с IP
          прод-сервера Ozon блокирует и то и другое (2026-11-10).
    """
    cookies = cookies if cookies is not None else os.getenv("OZON_COOKIES")
    proxy = (proxy if proxy is not None else os.getenv("OZON_PROXY")) or None
    mode = (mode or os.getenv("OZON_FETCH_MODE") or OZON_FETCH_MODE_DEFAULT).lower()
    if mode not in ("auto", "http", "browser"):
        raise ValueError(f"Неизвестный режим: {mode}")

    path = _product_path(url)
    page2_path = f"{path}?layout_container=pdpPage2column&layout_page_index=2"
    product = OzonProduct(url=f"{OZON_BASE}{path}", sku=_extract_sku(path))

    if remote_fetch is not None:
        pages = await remote_fetch(path, [page2_path])
        pages = (list(pages) + [None, None])[:2]
        _apply_pages(product, pages[0], pages[1])
        mode = "remote"
    elif mode in ("auto", "http"):
        async with _OzonHttp(cookies=cookies, proxy=proxy) as http:
            # Страница 1: заголовок, галерея, цена. Страница 2: описание и характеристики.
            page1 = await _fetch_page_json(http, path)
            page2 = await _fetch_page_json(http, page2_path) if page1 else None
            _apply_pages(product, page1, page2)
            if mode == "http" and (not product.title or not product.images):
                await _fallback_html(http, product, path)

    if mode != "remote" and (mode == "browser" or (mode == "auto" and not product.title)):
        if mode == "auto":
            print("[ozon] HTTP заблокирован — переключаюсь на браузер (Playwright)", file=sys.stderr)
        if browser is not None:
            page1, page2 = await browser.fetch_pages(path, [page2_path])
        else:
            async with OzonBrowser(proxy=proxy) as own:
                page1, page2 = await own.fetch_pages(path, [page2_path])
        _apply_pages(product, page1, page2)

    # нормализуем картинки: оригинальный размер, без дублей
    seen: set[str] = set()
    images: list[str] = []
    for src in product.images:
        full = _original_image(src)
        if full not in seen:
            seen.add(full)
            images.append(full)
    product.images = images

    if not product.title:
        hint = ""
        if mode == "remote":
            hint = " Chrome в WSL открыл страницу, но данных товара на ней нет — проверьте ссылку."
        elif mode == "http":
            hint = " Попробуйте режим браузера: mode='browser' / OZON_FETCH_MODE=browser."
        elif not HAS_PLAYWRIGHT:
            hint = " Установите Playwright: pip install playwright."
        else:
            hint = " Попробуйте видимый браузер (OZON_HEADLESS=0) или другой IP (OZON_PROXY)."
        raise OzonParseError(f"Не удалось получить данные товара — Ozon не отдал карточку.{hint}")
    return product


async def download_images(
    product: OzonProduct,
    out_dir: str | Path,
    *,
    limit: int | None = None,
    proxy: str | None = None,
) -> list[str]:
    """Скачивает картинки товара в out_dir/images. Возвращает список локальных путей."""
    img_dir = Path(out_dir) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    urls = product.images[:limit] if limit else product.images
    saved: list[str] = []

    async with _OzonHttp(proxy=(proxy if proxy is not None else os.getenv("OZON_PROXY")) or None) as http:
        sem = asyncio.Semaphore(5)

        async def _one(i: int, src: str) -> str | None:
            ext = Path(urlparse(src).path).suffix or ".jpg"
            target = img_dir / f"{i:02d}{ext}"
            async with sem:
                try:
                    target.write_bytes(await http.get_bytes(src))
                    return str(target)
                except Exception as e:
                    print(f"[ozon] не скачалось {src}: {e}", file=sys.stderr)
                    return None

        results = await asyncio.gather(*(_one(i, s) for i, s in enumerate(urls, 1)))
    saved = [r for r in results if r]
    product.local_images = saved
    return saved


# ============================================================================
#                               GEMINI: ПРОМТ
# ============================================================================

SYSTEM_PROMPT = f"""Ты — performance-маркетолог и директ-копирайтер уровня lead с опытом 15 лет в e-commerce \
(Ozon, Wildberries, Яндекс Маркет), Яндекс.Директе и VK Рекламе. Твоя метрика — прибыль с рекламы: \
высокий CTR, дешёвый клик, конверсия карточки в заказ. Ты знаешь: покупают не товар, а результат, \
эмоцию и уверенность, что выбор правильный.

ЗАДАЧА: по данным карточки товара Ozon (текст + фотографии) создать комплект рекламных материалов, \
который можно запускать без доработки и который пройдёт модерацию с первого раза.

══════ ШАГ 1. ФАКТЫ (поле facts) ══════
Выпиши 6–15 проверяемых фактов о товаре: из текста карточки И с фотографий (инфографика на фото — \
ценный источник: размеры, материал, комплектация, особенности; бери только то, что читается однозначно). \
Цена, старая цена, рейтинг, число отзывов — тоже факты (точные цифры).
ЖЕЛЕЗНОЕ ПРАВИЛО: любое утверждение в рекламе опирается на факт из этого списка или является очевидным \
следствием (например, «подойдёт как подарок» для сувенира). НЕЛЬЗЯ утверждать то, чего нет в фактах: \
прочность, устойчивость, качество материалов, безопасность, гипоаллергенность, «не царапает», упаковку, \
гарантию, доставку и её сроки, наличие, остатки, «хит продаж», «популярный», «выбор тысяч», \
«сборный/конструктор» (если не указано). Свойства, которые нельзя прочитать в данных или однозначно \
увидеть на фото, не упоминай. Отзывы называй только числом («154 отзыва»), НЕ «положительные» и \
НЕ «довольные покупатели». Не предлагай выбор цвета/размера, если вариантов нет в данных. \
Нет факта — нет утверждения.

══════ ШАГ 2. СТРАТЕГИЯ ══════
- big_idea: одна центральная идея кампании (1 фраза) — эмоциональная и конкретная, вокруг неё строятся тексты.
- target_audience: 3–5 сегментов в формате «кто покупает → в какой момент → что ищет». \
Покупатель ≠ пользователь (подарок, ребёнку, коллегам). Учитывай сезон и поводы (праздники, если \
товар сезонный).
- pains: 4–6 болей, желаний и возражений (в т.ч. «а вдруг маленький / дешёвый на вид / не успеет \
к празднику») — тексты должны их снимать.
- usp: 3–5 УТП по схеме «свойство → что это даёт покупателю → эмоция», только из фактов.

══════ ШАГ 3. ПОИСК: ГРУППЫ КЛЮЧЕЙ И ДИРЕКТ ══════
keyword_groups: 4–6 групп по ИНТЕНТУ (что человек хочет в момент запроса): прямой товарный \
(«фигурка снеговик купить»), подарочный («подарок коллеге на новый год недорого»), ситуационный / \
декор («новогодний декор на полку»), по признаку («снеговик с подвижными ножками»), маркетплейс \
(«... озон»). В каждой группе 4–8 фраз по 2–5 слов, без операторов и спецсимволов, \
реальными словами, которыми люди ищут.

yandex_direct: по одному объявлению на каждую группу (в keyword_group — точное имя группы):
- title1 (≤ {LIMITS['yandex_title1']}): НАЧИНАЕТСЯ с главной фразы своей группы (Директ подсвечивает \
совпадение с запросом жирным → CTR выше, клик дешевле) + главная выгода или оффер;
- title2 (≤ {LIMITS['yandex_title2']}): второй сильный аргумент, НЕ повторяющий title1 \
(цена со скидкой, рейтинг, размер, повод);
- text (≤ {LIMITS['yandex_text']}): выгода + доказательство цифрой + призыв глаголом действия \
(«Закажите на Ozon», «Выберите цвет»). Не больше одного «!» на объявление.
- angle: какой угол (выгода, подарок, социальное доказательство, сезон, решение боли, сравнение «вместо…»).
yandex_callouts: 4 уточнения ≤ {LIMITS['yandex_callout']} символов — короткие факты \
(«Рейтинг 4,9», «Размер 5 см», «Скидка 43%»).

══════ ШАГ 4. ЛЕНТА: VK РЕКЛАМА ══════
vk_ads: 3 объявления, человек НЕ ищет товар — его надо остановить в ленте:
- title (≤ {LIMITS['vk_title']}): интрига, вопрос или выгода, без эмодзи;
- text (≤ {LIMITS['vk_text']}): первые 90 символов — крючок (видны до «Показать полностью»): \
узнаваемая сцена из жизни, вопрос-боль или яркая выгода. Дальше: решение → доказательство цифрой → \
призыв. Можно 1–2 уместных эмодзи.
- Разные углы для трёх объявлений: эмоция/сцена, подарок, выгода/цена.

══════ ШАГ 5. КАРТОЧКА И КРЕАТИВЫ ══════
- card_title (≤ {LIMITS['card_title']}): тип товара В НАЧАЛЕ + ключевой признак + назначение/для кого \
+ бренд (если есть). Без цены, без «!», без субъективных оценок. Главные поисковые ключи — в первых 60 символах.
- card_description (1000–2000 символов): цепляющий первый абзац (боль/желание, сцена) → выгоды списком \
(каждый пункт с новой строки, начиная с «— ») → характеристики простым языком (размеры, материал, цвета — \
только из фактов) → сценарии использования → мягкий призыв. 4–6 абзацев, разделённых пустой строкой. \
Органично впиши 5–8 поисковых фраз из keyword_groups. Без HTML и markdown.
- bullets: 5–7 выгод для инфографики (≤ {LIMITS['bullet']}).
- headlines: 10 универсальных продающих заголовков (≤ {LIMITS['headline']}), все разных углов.
- banner_texts: 5 текстов для баннеров (≤ {LIMITS['banner']}) — читаются за 1 секунду.
- cta: 5 призывов к действию (≤ {LIMITS['cta']}).
- negative_keywords: 10–20 минус-слов против нецелевого трафика именно для этого товара \
(«своими руками», «схема», «раскраска», «б/у», «авито», «бесплатно», нерелевантные значения слов).

══════ ЧЕСТНЫЕ ТРИГГЕРЫ (использовать) ══════
Якорь цены (старая → новая цена, % скидки — посчитай точно), социальное доказательство (точный рейтинг \
и число отзывов), конкретика (размеры, цифры), визуализация сцены использования, реальный сезонный повод \
(если товар сезонный), сравнение «вместо …» с альтернативой (без упоминания конкурентов), \
снятие возражения. Для товара дешевле 1000 ₽ цена — сильнейший аргумент, используй её часто.

══════ ЗАПРЕЩЕНО (модерация Директа/VK и потеря доверия) ══════
Слова: «лучший», «самый», «№1», «хит», «уникальный», «идеальный», «качественный», «гарантия», \
«бесплатно», «быстрая доставка», «жмите», «кликайте». Не больше одного «!» на всё объявление \
(title1 + title2 + text вместе). Не начинай все тексты одним и тем же призывом — варьируй CTA. Ложная срочность («осталось 2 шт», «только сегодня»), выдуманные скидки и \
акции, CAPS LOCK, «!!!», обращение «ты», медицинские обещания, канцелярит и вода. Обращение на «вы» \
с маленькой буквы. Числа пиши точно как в фактах.

══════ ДЛИНА ══════
Лимиты — жёсткие (символы с пробелами и знаками). Пиши с запасом 2–3 символа, \
перед ответом мысленно пересчитай длину каждой строки с лимитом.
"""


def _build_user_prompt(product: OzonProduct, n_images: int = 0) -> str:
    chars = "\n".join(f"- {k}: {v}" for k, v in list(product.characteristics.items())[:40]) or "нет данных"
    description = product.description[:6000] or "нет данных"
    parts = [
        "ДАННЫЕ КАРТОЧКИ ТОВАРА OZON",
        f"Заголовок: {product.title}",
        f"Бренд: {product.brand or 'не указан'}",
        f"Цена: {product.price or 'не указана'}"
        + (f" (старая цена: {product.old_price})" if product.old_price else ""),
    ]
    if product.rating:
        parts.append(f"Рейтинг: {product.rating} ({product.reviews_count or '?'} отзывов)")
    parts += [
        f"\nХарактеристики:\n{chars}",
        f"\nОписание продавца:\n{description}",
    ]
    if n_images:
        parts.append(f"\nПриложено фото товара: {n_images} шт. (первое — главное фото карточки). "
                     "Извлеки с них факты, которых нет в тексте.")
    parts.append("\nВыполни шаги 1–5 и верни JSON.")
    return "\n".join(parts)


def _response_schema() -> dict[str, Any]:
    """Схема structured output Gemini. propertyOrdering: сначала факты и стратегия, затем тексты —
    модель генерирует поля по порядку, поэтому тексты пишутся уже «опираясь» на факты."""
    s = {"type": "STRING"}
    arr = {"type": "ARRAY", "items": s}
    order = [
        "facts", "big_idea", "product_summary", "target_audience", "pains", "usp",
        "keyword_groups", "yandex_direct", "yandex_callouts", "vk_ads",
        "card_title", "card_description", "bullets", "headlines", "banner_texts", "cta",
        "negative_keywords",
    ]
    props = {
        "facts": arr, "big_idea": s, "product_summary": s, "target_audience": arr, "pains": arr, "usp": arr,
        "keyword_groups": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"name": s, "intent": s, "phrases": arr},
            "required": ["name", "intent", "phrases"],
            "propertyOrdering": ["name", "intent", "phrases"],
        }},
        "yandex_direct": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"keyword_group": s, "angle": s, "title1": s, "title2": s, "text": s},
            "required": ["keyword_group", "angle", "title1", "title2", "text"],
            "propertyOrdering": ["keyword_group", "angle", "title1", "title2", "text"],
        }},
        "yandex_callouts": arr,
        "vk_ads": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"angle": s, "title": s, "text": s},
            "required": ["angle", "title", "text"],
            "propertyOrdering": ["angle", "title", "text"],
        }},
        "card_title": s, "card_description": s, "bullets": arr, "headlines": arr,
        "banner_texts": arr, "cta": arr, "negative_keywords": arr,
    }
    return {"type": "OBJECT", "properties": props, "required": order, "propertyOrdering": order}


# ---------------------------------------------------------------------------- проверки


class _Issue:
    """Проблемный текст: превышение лимита и/или стоп-слова. setter подставляет исправление."""

    def __init__(self, label: str, text: str, limit: int, problems: list[str], setter):
        self.label, self.text, self.limit, self.problems, self.setter = label, text, limit, problems, setter


def _stop_hits(text: str) -> list[str]:
    hits = []
    for p in STOP_PATTERNS:
        m = re.search(p, text, flags=re.I)
        if m:
            hits.append(m.group(0))
    return hits


def _collect_issues(ad: AdCopy, check_card_description: bool = True) -> list[_Issue]:
    issues: list[_Issue] = []

    def chk(label: str, text: str, limit: int | None, setter, stop: bool = True):
        problems = []
        if limit is not None and len(text) > limit:
            problems.append(f"длина {len(text)} > {limit}")
        hits = _stop_hits(text) if stop else []
        if hits:
            problems.append("запрещённые слова: " + ", ".join(hits))
        if problems:
            issues.append(_Issue(label, text, limit or 10_000, problems, setter))

    def list_setter(lst: list[str], i: int):
        def _set(v: str):
            lst[i] = v
        return _set

    def attr_setter(obj: Any, field: str):
        return lambda v: setattr(obj, field, v)

    for i, a in enumerate(ad.yandex_direct, 1):
        chk(f"Директ #{i} title1", a.title1, LIMITS["yandex_title1"], attr_setter(a, "title1"))
        chk(f"Директ #{i} title2", a.title2, LIMITS["yandex_title2"], attr_setter(a, "title2"))
        chk(f"Директ #{i} text", a.text, LIMITS["yandex_text"], attr_setter(a, "text"))
    for i, a in enumerate(ad.vk_ads, 1):
        chk(f"VK #{i} title", a.title, LIMITS["vk_title"], attr_setter(a, "title"))
        chk(f"VK #{i} text", a.text, LIMITS["vk_text"], attr_setter(a, "text"))
    chk("card_title", ad.card_title, LIMITS["card_title"], attr_setter(ad, "card_title"))
    if check_card_description:
        chk("card_description", ad.card_description, None, attr_setter(ad, "card_description"))
    for name, key in (("yandex_callouts", "yandex_callout"), ("bullets", "bullet"),
                      ("headlines", "headline"), ("banner_texts", "banner"), ("cta", "cta")):
        lst = getattr(ad, name)
        for i, t in enumerate(lst):
            chk(f"{name} #{i + 1}", t, LIMITS[key], list_setter(lst, i))
    return issues


def _check_limits(ad: AdCopy) -> list[str]:
    return [f"{x.label}: {'; '.join(x.problems)} — «{x.text[:120]}»" for x in _collect_issues(ad)]


# ---------------------------------------------------------------------------- вызов Gemini


def _gemini_gateway() -> tuple[str, str] | None:
    """Шлюз Gemini в WSL (parsya-control POST /api/addavto/gemini/generate):
    там ключ и сеть до Google (WireGuard), как у остального бэка ParsYa.
    GEMINI_VIA=wsl — всегда через шлюз; direct — напрямую с GEMINI_API_KEY и
    GEMINI_PROXY; не задано — через шлюз, если есть PARSYA_POOL_API_TOKEN."""
    via = (os.getenv("GEMINI_VIA") or "").strip().lower()
    token = os.getenv("PARSYA_POOL_API_TOKEN", "").strip()
    if via == "direct" or (via != "wsl" and not token):
        return None
    if not token:
        raise GeminiError("GEMINI_VIA=wsl, но PARSYA_POOL_API_TOKEN не задан")
    base = os.getenv("PARSYA_POOL_API_URL", "http://127.0.0.1:8091").rstrip("/")
    return f"{base}/api/addavto/gemini/generate", token


async def _call_gemini(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], payload: dict[str, Any]
) -> str:
    """ЕДИНАЯ точка всех запросов к Gemini: POST с ретраями на 429/5xx/таймауты.
    Возвращает текст ответа (JSON-строку). По умолчанию запрос уходит через
    шлюз в WSL (_gemini_gateway) — тело generateContent то же, ответ Google
    приходит как есть, поэтому разбор и ретраи ниже не меняются."""
    gateway = _gemini_gateway()
    if gateway is not None:
        m = re.search(r"/models/([^/:]+):generateContent", url)
        if not m:
            raise GeminiError(f"Не удалось определить модель Gemini из {url}")
        gw_url, gw_token = gateway
        url, payload = gw_url, {"model": m.group(1), "body": payload}
        headers = {"Authorization": f"Bearer {gw_token}", "Content-Type": "application/json"}
    resp = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as e:  # таймаут / обрыв сети — повторяем
            last_exc, resp = e, None
            print(f"[gemini] попытка {attempt + 1}: {type(e).__name__}, повторяю", file=sys.stderr)
            continue
        if resp.status_code not in (429, 500, 502, 503, 504):
            break
        await asyncio.sleep(2 ** attempt * 2)
    if resp is None:
        raise GeminiError(f"Gemini не ответил (сеть/таймаут): {type(last_exc).__name__}: {last_exc}")
    if resp.status_code != 200:
        raise GeminiError(f"Gemini HTTP {resp.status_code}: {resp.text[:1000]}")
    data = resp.json()
    try:
        candidate = data["candidates"][0]
        text = "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought"))
    except (KeyError, IndexError) as e:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or data
        raise GeminiError(f"Пустой ответ Gemini: {reason}") from e
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())


async def _fix_issues(
    ad: AdCopy, client: httpx.AsyncClient, url: str, headers: dict[str, str], attempts: int = 2
) -> None:
    """Отправляет на переписывание только проблемные тексты (длина/стоп-слова) и подставляет исправления."""
    for _ in range(attempts):
        issues = _collect_issues(ad)
        if not issues:
            return
        items = [
            {"id": str(i), "max_length": x.limit if x.limit < 10_000 else None,
             "problems": x.problems, "text": x.text}
            for i, x in enumerate(issues)
        ]
        payload = {
            "systemInstruction": {"parts": [{"text": (
                "Ты — редактор рекламных текстов для Яндекс.Директа и VK. Исправь каждый текст: "
                "уложи в max_length (символы с пробелами, пиши с запасом 2 символа) и убери запрещённые "
                "слова, заменив их конкретикой без превосходных степеней. Сохрани смысл, ключевую фразу "
                "в начале, цифры и продающий посыл. Не добавляй новых фактов. "
                "Верни JSON-массив [{id, text}]."
            )}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(items, ensure_ascii=False)}]}],
            "generationConfig": {
                "temperature": 0.3,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "ARRAY",
                    "items": {"type": "OBJECT",
                              "properties": {"id": {"type": "STRING"}, "text": {"type": "STRING"}},
                              "required": ["id", "text"]},
                },
            },
        }
        try:
            fixes = json.loads(await _call_gemini(client, url, headers, payload))
        except (GeminiError, json.JSONDecodeError) as e:
            print(f"[gemini] не удалось исправить тексты: {e}", file=sys.stderr)
            return
        by_id = {str(f.get("id")): f.get("text") for f in fixes if isinstance(f, dict)}
        for i, x in enumerate(issues):
            new = (by_id.get(str(i)) or "").strip()
            if new and len(new) <= x.limit:
                x.setter(new)


async def _load_images_for_ai(product: OzonProduct, client: httpx.AsyncClient, limit: int) -> list[dict]:
    """Готовит до `limit` фото для мультимодального запроса (локальные файлы или скачивание с CDN)."""
    import base64

    parts: list[dict] = []
    sources: list[str] = (product.local_images or [])[:limit] or product.images[:limit]
    for src in sources:
        try:
            if src.startswith("http"):
                r = await client.get(src, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]})
                r.raise_for_status()
                data, mime = r.content, r.headers.get("content-type", "image/jpeg").split(";")[0]
            else:
                data = Path(src).read_bytes()
                mime = "image/png" if src.lower().endswith(".png") else "image/webp" \
                    if src.lower().endswith(".webp") else "image/jpeg"
            if len(data) > 4_000_000:  # слишком тяжёлое фото не отправляем
                continue
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
        except Exception as e:
            print(f"[gemini] фото не добавлено ({src}): {e}", file=sys.stderr)
    return parts


async def generate_ad_copy(
    product: OzonProduct,
    *,
    api_key: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    images: int = 3,
    proxy: str | None = None,
    timeout: float = 300.0,
    client: httpx.AsyncClient | None = None,
    fix_issues: bool = True,
    fallback_models: list[str] | tuple[str, ...] | None = None,
) -> AdCopy:
    """Отправляет данные товара (+ до `images` фото) в Gemini и возвращает AdCopy.

    fix_issues — второй короткий запрос: переписать тексты с превышением лимитов или стоп-словами.
    fallback_models — запасные модели при 503/429/404/таймауте основной (None — из env/по умолчанию,
    [] — без запасных).
    proxy — для серверов в РФ Gemini API напрямую недоступен (env GEMINI_PROXY).
    """
    api_key = api_key or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY_DEFAULT
    if not api_key and _gemini_gateway() is None:  # через шлюз WSL ключ здесь не нужен
        raise GeminiError("Не задан токен Gemini: передайте api_key или установите GEMINI_API_KEY")
    model = model or os.getenv("GEMINI_MODEL") or GEMINI_MODEL_DEFAULT
    if fallback_models is None:
        env_fb = os.getenv("GEMINI_FALLBACK_MODELS")
        fallback_models = [x.strip() for x in env_fb.split(",") if x.strip()] if env_fb is not None \
            else list(GEMINI_FALLBACK_MODELS_DEFAULT)
    models = [model] + [m_ for m_ in fallback_models if m_ != model]
    # Пустая строка из .env (GEMINI_PROXY=) — «без прокси»: httpx на proxy="" падает
    # «Unknown scheme for proxy URL» (прод, 2026-11-10).
    proxy = (proxy if proxy is not None else os.getenv("GEMINI_PROXY")) or None
    headers = {"x-goog-api-key": api_key or "", "Content-Type": "application/json"}

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout, proxy=proxy, follow_redirects=True)
    try:
        image_parts = await _load_images_for_ai(product, client, images) if images else []
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [
                {"text": _build_user_prompt(product, len(image_parts))}, *image_parts,
            ]}],
            "generationConfig": {
                "temperature": temperature,
                "topP": 0.95,
                "maxOutputTokens": 32768,  # у thinking-моделей сюда входят и токены рассуждений
                "responseMimeType": "application/json",
                "responseSchema": _response_schema(),
            },
        }
        ad: AdCopy | None = None
        errors: list[str] = []
        for current in models:
            url = GEMINI_ENDPOINT.format(model=current)
            try:
                text = await _call_gemini(client, url, headers, payload)
            except GeminiError as e:
                msg = str(e)
                errors.append(f"{current}: {msg[:200]}")
                # перегрузка / квота / модель снята / сеть — пробуем следующую модель, иначе ошибка
                if any(code in msg for code in ("HTTP 429", "HTTP 503", "HTTP 500", "HTTP 404", "не ответил")):
                    print(f"[gemini] {current} недоступна ({msg[:60]}...), пробую следующую", file=sys.stderr)
                    continue
                raise
            try:
                ad = AdCopy.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as e:
                raise GeminiError(f"Gemini ({current}) вернул невалидный JSON: {e}\n{text[:1500]}") from e
            ad.model_used = current
            break
        if ad is None:
            raise GeminiError("Все модели Gemini недоступны:\n" + "\n".join(errors))

        ad.keywords = [p for g in ad.keyword_groups for p in g.phrases]
        if fix_issues:
            await _fix_issues(ad, client, url, headers)
    finally:
        if own_client:
            await client.aclose()

    ad.warnings = _check_limits(ad)
    return ad


# ============================================================================
#                               ОРКЕСТРАЦИЯ
# ============================================================================


async def process_ozon_url(
    url: str,
    *,
    out_dir: str | Path | None = None,
    download: bool = True,
    use_ai: bool = True,
    api_key: str | None = None,
    model: str | None = None,
    cookies: str | None = None,
    proxy: str | None = None,
    mode: str | None = None,
    browser: OzonBrowser | None = None,
    remote_fetch: Callable[[str, list[str]], Awaitable[list[dict[str, Any] | None]]] | None = None,
) -> ProcessResult:
    """Полный цикл: парсинг → (скачивание картинок) → (генерация текстов) → (сохранение JSON).

    out_dir=None и download=False — ничего не пишет на диск (удобно для API-эндпоинта).
    """
    product = await parse_ozon_product(url, cookies=cookies, proxy=proxy, mode=mode, browser=browser,
                                       remote_fetch=remote_fetch)

    target: Path | None = None
    if out_dir is not None:
        target = Path(out_dir) / f"ozon_{product.sku or 'product'}"
        target.mkdir(parents=True, exist_ok=True)

    if download and target is not None:
        await download_images(product, target, proxy=proxy)

    ad_copy = await generate_ad_copy(product, api_key=api_key, model=model) if use_ai else None

    result = ProcessResult(product=product, ad_copy=ad_copy, output_dir=str(target) if target else None)

    if target is not None:
        (target / "product.json").write_text(product.model_dump_json(indent=2), encoding="utf-8")
        if ad_copy:
            (target / "ad_copy.json").write_text(ad_copy.model_dump_json(indent=2), encoding="utf-8")
            (target / "ad_copy.txt").write_text(render_text_report(result), encoding="utf-8")
    return result


def render_text_report(result: ProcessResult) -> str:
    p, a = result.product, result.ad_copy
    lines = [f"ТОВАР: {p.title}", f"Ссылка: {p.url}", ""]
    if not a:
        return "\n".join(lines)
    lines += [f"Модель: {a.model_used}", "",
              "== ФАКТЫ (основа всех текстов) ==", *[f"• {f}" for f in a.facts], "",
              "== БОЛЬШАЯ ИДЕЯ ==", a.big_idea, "",
              "== ЦЕЛЕВАЯ АУДИТОРИЯ ==", *[f"• {t}" for t in a.target_audience], "",
              "== НОВЫЙ ЗАГОЛОВОК КАРТОЧКИ ==", a.card_title, "",
              "== НОВОЕ ОПИСАНИЕ КАРТОЧКИ ==", a.card_description, "",
              "== БУЛЛЕТЫ ==", *[f"• {b}" for b in a.bullets], "",
              "== УТП ==", *[f"• {u}" for u in a.usp], "",
              "== ЗАГОЛОВКИ ==", *[f"{i}. {h}" for i, h in enumerate(a.headlines, 1)], ""]
    lines.append("== ЯНДЕКС.ДИРЕКТ ==")
    groups = {g.name: g for g in a.keyword_groups}
    for i, d in enumerate(a.yandex_direct, 1):
        lines += [f"#{i} группа «{d.keyword_group}» [{d.angle}]",
                  f"  З1: {d.title1}", f"  З2: {d.title2}", f"  Текст: {d.text}"]
        g = groups.get(d.keyword_group)
        if g:
            lines.append(f"  Ключи: {', '.join(g.phrases)}")
    lines += ["  Уточнения: " + " · ".join(a.yandex_callouts)]
    lines += ["", "== VK РЕКЛАМА =="]
    for i, v in enumerate(a.vk_ads, 1):
        lines += [f"#{i} [{v.angle}]", f"  {v.title}", f"  {v.text}"]
    lines += ["", "== ГРУППЫ КЛЮЧЕВЫХ ФРАЗ =="]
    for g in a.keyword_groups:
        lines.append(f"• {g.name} ({g.intent}): {', '.join(g.phrases)}")
    lines += ["", "== МИНУС-СЛОВА ==", ", ".join(f"-{k}" for k in a.negative_keywords),
              "", "== ТЕКСТЫ ДЛЯ БАННЕРОВ ==", *a.banner_texts,
              "", "== CTA ==", *a.cta]
    if a.warnings:
        lines += ["", "!! ТРЕБУЕТ ПРОВЕРКИ (лимиты / стоп-слова) !!", *a.warnings]
    return "\n".join(lines)


# ============================================================================
#                     ПРИМЕР ИНТЕГРАЦИИ В FASTAPI (опционально)
# ============================================================================


def build_router(browser: OzonBrowser | None = None):
    """Готовый APIRouter. В проекте:

        ozon_browser = OzonBrowser()          # один браузер на всё приложение

        @asynccontextmanager
        async def lifespan(app):
            yield
            await ozon_browser.close()        # браузер стартует лениво при первом запросе

        app = FastAPI(lifespan=lifespan)
        app.include_router(build_router(ozon_browser), prefix="/ozon")
    """
    from fastapi import APIRouter, HTTPException

    router = APIRouter(tags=["ozon-ads"])

    class Req(BaseModel):
        url: str
        use_ai: bool = True

    @router.post("/ad-copy", response_model=ProcessResult)
    async def ad_copy_endpoint(req: Req) -> ProcessResult:
        try:
            return await process_ozon_url(
                req.url, out_dir=None, download=False, use_ai=req.use_ai, browser=browser
            )
        except OzonParseError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except GeminiError as e:
            raise HTTPException(status_code=502, detail=str(e))

    return router


# ============================================================================
#                               CLI
# ============================================================================


def main() -> None:
    ap = argparse.ArgumentParser(description="Ozon → изображения + тексты + продающие тексты от Gemini")
    ap.add_argument("url", help="Ссылка на товар Ozon")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "ozon_results"),
                    help="Папка для результатов")
    ap.add_argument("--no-images", action="store_true", help="Не скачивать изображения")
    ap.add_argument("--no-ai", action="store_true", help="Не отправлять в Gemini")
    ap.add_argument("--model", default=None, help="Модель Gemini")
    ap.add_argument("--mode", choices=["auto", "http", "browser"], default=None,
                    help="Как получать данные: auto (по умолчанию), http, browser")
    ap.add_argument("--headful", action="store_true", help="Показывать окно браузера (режим browser)")
    args = ap.parse_args()
    if args.headful:
        os.environ["OZON_HEADLESS"] = "0"

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    try:
        result = asyncio.run(process_ozon_url(
            args.url, out_dir=args.out, download=not args.no_images,
            use_ai=not args.no_ai, model=args.model, mode=args.mode,
        ))
    except (OzonParseError, GeminiError) as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)

    p = result.product
    print(f"Товар:     {p.title}")
    print(f"SKU:       {p.sku}   Цена: {p.price}")
    print(f"Картинок:  {len(p.images)} (скачано {len(p.local_images)})")
    print(f"Описание:  {len(p.description)} симв., характеристик: {len(p.characteristics)}")
    print(f"Результат: {result.output_dir}")
    if result.ad_copy:
        print("\n" + render_text_report(result))


if __name__ == "__main__":
    main()
