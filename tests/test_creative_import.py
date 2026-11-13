"""Импорт картинок креативов по ссылкам из подборок парсера (2026-11-13)."""
import io

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app import creative_images
from app import main as main_module
from test_bulk_campaign_create import _configure_test_env

AUTH = ("test-admin", "test-password-long-enough")


def _jpeg(w, h):
    b = io.BytesIO(); Image.new("RGB", (w, h), (200, 30, 30)).save(b, "JPEG"); return b.getvalue()


def test_avatars_original():
    u = "https://avatars.mds.yandex.net/get-direct/5432401/2T13YUhYYAmGK8TDFysGkQ/y300"
    assert creative_images.avatars_original(u) == "https://avatars.mds.yandex.net/get-direct/5432401/2T13YUhYYAmGK8TDFysGkQ/orig"
    assert creative_images.avatars_original("https://example.com/a.jpg") is None


def test_fit_for_direct_crops_and_scales():
    out = creative_images.fit_for_direct(_jpeg(1800, 1000))  # 1.8 -> 4:3
    w, h = creative_images.image_size(out)
    assert creative_images.direct_image_ok(out) and abs(w / h - 4 / 3) < 0.01
    out = creative_images.fit_for_direct(_jpeg(400, 300))  # x1.5 upscale
    assert creative_images.direct_image_ok(out)
    try:
        creative_images.fit_for_direct(_jpeg(150, 100)); assert False
    except creative_images.CreativeImageError:
        pass


def test_import_endpoint(monkeypatch, tmp_path):
    _configure_test_env(monkeypatch, tmp_path / "imp.db")
    big, tiny = _jpeg(1200, 675), _jpeg(100, 80)

    def handler(request):
        u = str(request.url)
        if u.endswith("/orig"):
            return httpx.Response(200, content=big)
        if "tiny" in u:
            return httpx.Response(200, content=tiny)
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(main_module.httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(handler), **{x: y for x, y in k.items() if x != "transport"}))
    with TestClient(main_module.app) as client:
        r = client.post("/api/creative-images/import-urls", auth=AUTH, json={"urls": [
            "https://avatars.mds.yandex.net/get-direct/1/abc/y300", "https://x.ru/tiny.jpg", "ftp://bad"]}).json()
    assert r["ok"] == 1 and r["failed"] == 2
    assert r["items"][0]["ref"].startswith("local:") and creative_images.direct_image_ok(creative_images.path_of(r["items"][0]["ref"]).read_bytes())
