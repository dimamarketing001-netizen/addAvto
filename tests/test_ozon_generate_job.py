"""ozon-generate фоном (2026-11-10): генерация идёт минуты, а прокси по
дороге (nginx 60/120 с, parsya-control 30 с) обрывали синхронный ответ —
«addAvto unreachable». POST теперь отвечает 202 сразу, итог — GET."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi.testclient import TestClient

from app import main as main_module
from app.ozon_ad_creator import AdCopy, OzonParseError, OzonProduct, ProcessResult, YandexDirectAd
from test_bulk_campaign_create import _configure_test_env, _make_project

AUTH = ("test-admin", "test-password-long-enough")
OZON_URL = "https://www.ozon.ru/product/santa-3160445300/"
# минимальный валидный JPEG (сигнатура FFD8FF)
JPEG_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAKAAoBAREA/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAA/AL+AD//Z"


def _result():
    return ProcessResult(
        product=OzonProduct(url=OZON_URL, title="Санта Клаус 3D", images=["https://ir.ozone.ru/a.jpg"]),
        ad_copy=AdCopy(
            yandex_direct=[YandexDirectAd(title1="Санта Клаус 3D", title2="t2", text="Подвижная фигурка", angle="a")],
            keywords=["санта клаус фигурка"],
            negative_keywords=["бесплатно"],
        ),
    )


def _poll(client, project_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/projects/{project_id}/ozon-generate", auth=AUTH).json()
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _setup(monkeypatch, tmp_path, outcome):
    _configure_test_env(monkeypatch, tmp_path / "ozon-job.db")
    main_module._OZON_JOBS.clear()
    calls = []

    async def fake_process(url, **kwargs):
        calls.append(url)
        await asyncio.sleep(0.3)  # «долгая» генерация — POST не должен её ждать
        if isinstance(outcome, Exception):
            raise outcome
        return _result()

    monkeypatch.setattr(main_module, "process_ozon_url", fake_process)
    return calls


def test_post_returns_immediately_and_get_returns_result(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, "ok")
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, name="Товар Ozon", launch_mode="ozon", ozon_url=OZON_URL)
        assert client.get(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH).json() == {"status": "none"}
        t0 = time.time()
        resp = client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={"url": OZON_URL})
        assert resp.status_code == 202 and resp.json()["status"] == "running"
        assert time.time() - t0 < 0.25
        # повторный «Далее», пока идёт генерация, вторую задачу не запускает
        again = client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={"url": OZON_URL})
        assert again.json()["status"] == "running"
        st = _poll(client, project["id"])
        assert st["status"] == "done", st
        assert calls == [OZON_URL]
        saved = st["result"]["project"]
        assert saved["name"] == "Санта Клаус 3D"
        assert saved["headlines"] == ["Санта Клаус 3D"] and saved["ad_texts"] == ["Подвижная фигурка"]
        assert saved["image_urls"] == ["https://ir.ozone.ru/a.jpg"]
        from app import db
        assert db.get_project(project["id"])["keywords"] == "санта клаус фигурка"


def test_failure_is_reported_via_status(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, OzonParseError("Ozon заблокировал запрос (HTTP 403)"))
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON_URL)
        assert client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={}).status_code == 202
        st = _poll(client, project["id"])
        assert st["status"] == "failed" and "403" in st["error"]


def test_unexpected_error_does_not_get_lost(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, RuntimeError("boom"))
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON_URL)
        client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={})
        st = _poll(client, project["id"])
        assert st["status"] == "failed" and "RuntimeError: boom" in st["error"]


def test_validation_errors_stay_synchronous(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "ok")
    with TestClient(main_module.app) as client:
        assert client.post("/api/projects/9999/ozon-generate", auth=AUTH, json={}).status_code == 404
        project = _make_project(client, AUTH)
        assert client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={}).status_code == 422


# --- карточка через Chrome в WSL (parsya-control /api/addavto/ozon-fetch) ---

DOM_PAGE = {
    "widgetStates": {
        "webProductHeading-1-default-1": '{"title": "Санта Клаус 3D"}',
        "webGallery-2-default-1": '{"images": [{"src": "https://ir.ozone.ru/s3/a/wc1000/1.jpg"}]}',
        "webPrice-3-default-1": '{"price": "291 ₽"}',
    },
    "seo": {"title": "Санта", "script": [{"innerHTML": '{"@type": "Product", "description": "Подвижная фигурка"}'}]},
}


def test_parse_ozon_product_uses_remote_fetch_only():
    from app import ozon_ad_creator as oz

    calls = []

    async def remote(path, extra):
        calls.append((path, extra))
        return [DOM_PAGE, None]

    product = asyncio.run(oz.parse_ozon_product(OZON_URL, remote_fetch=remote))
    assert calls == [("/product/santa-3160445300/", ["/product/santa-3160445300/?layout_container=pdpPage2column&layout_page_index=2"])]
    assert product.title == "Санта Клаус 3D" and product.price == "291 ₽"
    assert product.description == "Подвижная фигурка" and len(product.images) == 1


def _setup_remote(monkeypatch, tmp_path, pool_outcome):
    _configure_test_env(monkeypatch, tmp_path / "ozon-remote.db")
    main_module._OZON_JOBS.clear()
    seen = {}

    class Pool:
        async def ozon_fetch_pages(self, path, extra_paths, session_id, label="", max_images=0):
            seen.update(path=path, session_id=session_id, label=label, max_images=max_images, seen_by_gemini=None)
            if isinstance(pool_outcome, Exception):
                raise pool_outcome
            return {"pages": [DOM_PAGE, None], "images": [
                {"url": "https://ir.ozone.ru/1.jpg", "mime": "image/jpeg", "b64": JPEG_B64},
                {"url": "https://ir.ozone.ru/bad.webp", "mime": "image/webp", "b64": "UklGRg=="},
            ]}

    async def fake_generate(product, **kwargs):
        from app.ozon_ad_creator import AdCopy, YandexDirectAd, YandexSitelink
        seen["seen_by_gemini"] = list(product.local_images)
        return AdCopy(yandex_direct=[YandexDirectAd(title1=product.title, title2="t", text="txt", angle="a")],
                      keywords=["k"], yandex_callouts=["Рейтинг 4,9", "Слишком длинное уточнение больше 25"],
                      yandex_sitelinks=[YandexSitelink(kind="reviews", title="Отзывы 4,9★", description="515 отзывов"),
                                        YandexSitelink(kind="main", title="Купить за 291 ₽", description="")])

    monkeypatch.setattr(main_module, "ParsYaPoolClient", Pool)
    from app import ozon_ad_creator as oz
    monkeypatch.setattr(oz, "generate_ad_copy", fake_generate)
    return seen


def test_job_fetches_card_via_wsl_with_its_captcha_session(monkeypatch, tmp_path):
    seen = _setup_remote(monkeypatch, tmp_path, "ok")
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON_URL)
        started = client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={}).json()
        sid = started["captcha_session"]
        assert sid and len(sid) >= 8
        st = _poll(client, project["id"])
        assert st["status"] == "done", st
        assert seen["session_id"] == sid and seen["path"] == "/product/santa-3160445300/"
        p = st["result"]["project"]
        assert p["headlines"] == ["Санта Клаус 3D"]
        # фото: скачаны в WSL, лежат у нас, в проекте — local: (webp отброшен)
        assert seen["max_images"] == main_module.OZON_MAX_IMAGES
        assert len(p["image_urls"]) == 1 and p["image_urls"][0].startswith("local:") and p["image_urls"][0].endswith(".jpg")
        assert seen["seen_by_gemini"] and seen["seen_by_gemini"][0].endswith(p["image_urls"][0][6:])
        # уточнения/быстрые ссылки от Gemini, адреса — от ссылки на товар
        assert p["callouts_pool"] == ["Рейтинг 4,9"]
        assert [s["href"] for s in p["sitelinks_pool"]] == [
            "https://www.ozon.ru/product/santa-3160445300/reviews/", "https://www.ozon.ru/product/santa-3160445300/"]


def test_wsl_failure_is_reported(monkeypatch, tmp_path):
    from app.parsya_pool_client import ParsYaPoolError
    _setup_remote(monkeypatch, tmp_path, ParsYaPoolError("Капчу Ozon никто не прошёл вовремя", 409))
    with TestClient(main_module.app) as client:
        project = _make_project(client, AUTH, launch_mode="ozon", ozon_url=OZON_URL)
        client.post(f"/api/projects/{project['id']}/ozon-generate", auth=AUTH, json={})
        st = _poll(client, project["id"])
        assert st["status"] == "failed" and "Капчу Ozon никто не прошёл" in st["error"]


def test_short_characteristics_from_page_dom():
    from app import ozon_ad_creator as oz
    state = {"characteristics": [
        {"id": "Color_1", "title": {"textRs": [{"type": "text", "content": "Цвет"}]},
         "values": [{"id": "Color_0", "text": "Красный с белым"}]},
        {"id": "Material_2", "title": {"textRs": [{"type": "text", "content": "Материал"}]},
         "values": [{"text": "PLA пластик"}]},
    ]}
    product = oz.OzonProduct(url="u")
    oz._apply_pages(product, {"widgetStates": {"webShortCharacteristics-1-default-1": json.dumps(state)}}, None)
    assert product.characteristics == {"Цвет": "Красный с белым", "Материал": "PLA пластик"}


def test_empty_proxy_env_means_no_proxy(monkeypatch):
    """Прод .env: GEMINI_PROXY= и OZON_PROXY= пустые — это «без прокси», а
    не proxy='' (httpx: Unknown scheme for proxy URL URL(''))."""
    from app import ozon_ad_creator as oz
    monkeypatch.setenv("GEMINI_PROXY", "")
    monkeypatch.setenv("OZON_PROXY", "")
    monkeypatch.setenv("GEMINI_VIA", "direct")
    seen = {}
    real = oz.httpx.AsyncClient

    def spy(*a, **kw):
        seen.setdefault("proxies", []).append(kw.get("proxy"))
        return real(*a, **kw)

    monkeypatch.setattr(oz.httpx, "AsyncClient", spy)
    monkeypatch.setenv("GEMINI_API_KEY", "x")

    async def run():
        try:
            await oz.generate_ad_copy(oz.OzonProduct(url="u", title="t"), api_key="x")
        except Exception as e:  # сеть/ключ не важны — важно, что клиент создался без proxy=''
            return e

    asyncio.run(run())
    assert seen["proxies"] and all(p is None for p in seen["proxies"])
    assert oz.OzonBrowser().proxy is None and oz._OzonHttp(proxy="").proxy is None


def test_gemini_goes_through_wsl_gateway(monkeypatch):
    """Все запросы к Gemini — через шлюз parsya-control (ключ и WireGuard в WSL)."""
    import httpx
    from app import ozon_ad_creator as oz
    monkeypatch.delenv("GEMINI_VIA", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("PARSYA_POOL_API_TOKEN", "pool-token")
    monkeypatch.setenv("PARSYA_POOL_API_URL", "http://wsl.test:8091")
    sent = []
    ad = {"yandex_direct": [{"title1": "Санта", "title2": "t", "text": "Подвижная фигурка", "angle": "a"}],
          "keyword_groups": [{"name": "g", "phrases": ["санта фигурка"]}]}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(ad, ensure_ascii=False)}]}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await oz.generate_ad_copy(oz.OzonProduct(url="u", title="Санта"), images=0,
                                             client=client, fix_issues=False, fallback_models=[])

    result = asyncio.run(run())
    url, auth, body = sent[0]
    assert url == "http://wsl.test:8091/api/addavto/gemini/generate" and auth == "Bearer pool-token"
    assert body["model"] == oz.GEMINI_MODEL_DEFAULT and "contents" in body["body"]
    assert "x-goog-api-key" not in json.dumps(body)
    assert result.yandex_direct[0].title1 == "Санта" and result.keywords == ["санта фигурка"]


def test_gemini_direct_mode_still_works(monkeypatch):
    from app import ozon_ad_creator as oz
    monkeypatch.setenv("GEMINI_VIA", "direct")
    assert oz._gemini_gateway() is None
    monkeypatch.setenv("GEMINI_VIA", "wsl")
    monkeypatch.delenv("PARSYA_POOL_API_TOKEN", raising=False)
    import pytest
    with pytest.raises(oz.GeminiError):
        oz._gemini_gateway()


def test_hung_model_is_not_retried_next_model_used(monkeypatch):
    """Таймаут/504 шлюза — сразу следующая модель, без двух повторных ожиданий."""
    import httpx
    from app import ozon_ad_creator as oz
    monkeypatch.delenv("GEMINI_VIA", raising=False)
    monkeypatch.setenv("PARSYA_POOL_API_TOKEN", "pool-token")
    calls = []
    ad = {"yandex_direct": [{"title1": "Санта", "title2": "t", "text": "x", "angle": "a"}]}

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "m-hung":
            raise httpx.ReadTimeout("hung", request=request)
        if model == "m-504":
            return httpx.Response(504, json={"error": {"message": "Gemini не ответил за 300 с (WSL)"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(ad)}]}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await oz.generate_ad_copy(oz.OzonProduct(url="u", title="Санта"), model="m-hung", images=0,
                                             client=client, fix_issues=False, fallback_models=["m-504", "m-ok"])

    result = asyncio.run(run())
    assert calls == ["m-hung", "m-504", "m-ok"] and result.model_used == "m-ok"


def test_empty_fallback_env_uses_default_chain(monkeypatch):
    """Прод .env: GEMINI_FALLBACK_MODELS= пустое — нужна цепочка по умолчанию,
    а не одна основная модель (было: «Все модели недоступны: gemini-3.8-flash: 503»)."""
    import httpx
    from app import ozon_ad_creator as oz
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    monkeypatch.setenv("GEMINI_MODEL", "")
    monkeypatch.delenv("GEMINI_VIA", raising=False)
    monkeypatch.setenv("PARSYA_POOL_API_TOKEN", "pool-token")
    calls = []
    ad = {"yandex_direct": [{"title1": "Санта", "title2": "t", "text": "x", "angle": "a"}]}

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == oz.GEMINI_MODEL_DEFAULT:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(ad)}]}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await oz.generate_ad_copy(oz.OzonProduct(url="u", title="Санта"), images=0, client=client, fix_issues=False)

    real_sleep = asyncio.sleep
    monkeypatch.setattr(oz.asyncio, "sleep", lambda *_: real_sleep(0))
    result = asyncio.run(run())
    distinct = list(dict.fromkeys(calls))
    assert distinct[0] == oz.GEMINI_MODEL_DEFAULT and distinct[1] == oz.GEMINI_FALLBACK_MODELS_DEFAULT[0]
    assert result.model_used == distinct[-1] and result.model_used in oz.GEMINI_FALLBACK_MODELS_DEFAULT


def test_build_ozon_sitelinks_unique_hrefs_and_limits():
    from app.ozon_ad_creator import YandexSitelink, build_ozon_sitelinks
    sl = build_ozon_sitelinks("https://www.ozon.ru/product/x-1/?utm=1", [
        YandexSitelink(kind="reviews", title="Отзывы", description="d" * 61),
        YandexSitelink(kind="questions", title="Вопросы", description="Ответы продавца"),
        YandexSitelink(kind="main", title="Купить", description=""),
        YandexSitelink(kind="main", title="Цена со скидкой", description=""),
        YandexSitelink(kind="description", title="отзывы", description=""),  # дубль заголовка
        YandexSitelink(kind="characteristics", title="Т" * 31, description=""),  # длиннее 30
    ])
    assert [s["title"] for s in sl] == ["Отзывы", "Вопросы", "Купить", "Цена со скидкой"]
    assert sl[0]["href"] == "https://www.ozon.ru/product/x-1/reviews/" and sl[0]["description"] == ""
    assert sl[1]["href"] == "https://www.ozon.ru/product/x-1/questions/"
    assert sl[2]["href"] == "https://www.ozon.ru/product/x-1/"
    assert sl[3]["href"] == "https://www.ozon.ru/product/x-1/?utm_content=sl4"
    assert len({s["href"] for s in sl}) == len(sl)


def test_pick_sitelinks_fits_66_chars():
    pool = [{"title": t, "href": f"https://x.ru/{i}"} for i, t in enumerate(
        ["А" * 30, "Б" * 30, "В" * 10, "Г" * 10, "Д" * 5, "Е" * 5])]
    for shuffle in (False, True):
        for _ in range(20):
            got = main_module._pick_sitelinks(pool, shuffle=shuffle)
            assert sum(len(s["title"]) for s in got) <= 66 and got
    assert len(main_module._pick_sitelinks(pool, shuffle=True)) == 4


def test_creative_image_upload_get_and_launch_bytes(monkeypatch, tmp_path):
    import base64
    _configure_test_env(monkeypatch, tmp_path / "img.db")
    raw = base64.b64decode(JPEG_B64)
    with TestClient(main_module.app) as client:
        r = client.post("/api/creative-images", auth=AUTH, content=raw, headers={"Content-Type": "image/jpeg"})
        assert r.status_code == 201, r.text
        ref = r.json()["ref"]
        assert ref.startswith("local:") and ref.endswith(".jpg")
        again = client.post("/api/creative-images", auth=AUTH, content=raw, headers={"Content-Type": "image/jpeg"})
        assert again.json()["ref"] == ref  # без дублей
        bad = client.post("/api/creative-images", auth=AUTH, content=b"RIFF0000WEBPVP8 ", headers={"Content-Type": "image/webp"})
        assert bad.status_code == 422 and "JPG" in bad.json()["detail"]
        got = client.get(f"/api/creative-images/{ref[6:]}", auth=AUTH).json()
        assert got["data_url"] == "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        assert client.get("/api/creative-images/../../etc", auth=AUTH).status_code == 404
        # проект принимает local:-ссылку, при запуске уходят байты файла
        project = _make_project(client, AUTH, image_urls=[ref])
        assert project["image_urls"] == [ref]
    assert asyncio.run(main_module._download_image_base64(ref)) == base64.b64encode(raw).decode()


def test_direct_safe_text_strips_forbidden_symbols():
    """Живая ошибка 2026-11-11: «Отзывы 4.9★» — Директ отклонил набор
    быстрых ссылок (и кампанию) из-за символа ★."""
    f = main_module._direct_safe_text
    assert f("Отзывы 4.9★") == "Отзывы 4.9"
    assert f("Купить! Сейчас?") == "Купить Сейчас"
    assert f("Скидка 63% 🎁\nна Ozon") == "Скидка 63% на Ozon"
    assert f("Цена 291 ₽ «хит»") == "Цена 291 ₽ «хит»"
    picked = main_module._pick_sitelinks([
        {"title": "Отзывы 4.9★", "href": "https://x.ru/1", "description": "516 отзывов 👍"},
        {"title": "Отзывы 4.9", "href": "https://x.ru/2", "description": ""},  # дубль после очистки
        {"title": "🎁", "href": "https://x.ru/3", "description": ""},          # пустой после очистки
        {"title": "Размеры", "href": "https://x.ru/4", "description": ""},
    ], shuffle=False)
    assert [p["title"] for p in picked] == ["Отзывы 4.9", "Размеры"]
    assert picked[0]["description"] == "516 отзывов"
