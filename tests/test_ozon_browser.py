"""OzonBrowser: если браузер из OZON_BROWSER_CHANNEL не установлен (прод
2026-11-10: channel=chrome, а в образе только встроенный Chromium) —
запускается встроенный Chromium вместо 500 на ozon-generate."""
from __future__ import annotations

import asyncio

import pytest

from app import ozon_ad_creator as oz


class FakeBrowser:
    async def new_context(self, **kwargs):
        return object()


def _fake_playwright(launches, fail_with):
    class Chromium:
        async def launch(self, **kwargs):
            launches.append(kwargs)
            if kwargs.get("channel") and fail_with:
                raise Exception(fail_with)
            return FakeBrowser()

    class PW:
        chromium = Chromium()

    class Starter:
        async def start(self):
            return PW()

    return lambda: Starter()


@pytest.mark.parametrize("message", [
    "BrowserType.launch: Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome",
    "BrowserType.launch: Executable doesn't exist at /opt/pw-browsers/chrome",
])
def test_missing_channel_falls_back_to_bundled_chromium(monkeypatch, message):
    launches: list[dict] = []
    monkeypatch.setattr(oz, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(oz, "async_playwright", _fake_playwright(launches, message), raising=False)
    browser = asyncio.run(oz.OzonBrowser(channel="chrome").start())
    assert [l.get("channel") for l in launches] == ["chrome", None]
    assert browser.channel == ""
    assert browser._browser is not None and browser._context is not None


def test_installed_channel_is_used_as_is(monkeypatch):
    launches: list[dict] = []
    monkeypatch.setattr(oz, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(oz, "async_playwright", _fake_playwright(launches, None), raising=False)
    asyncio.run(oz.OzonBrowser(channel="chrome").start())
    assert [l.get("channel") for l in launches] == ["chrome"]


def test_other_launch_errors_are_not_masked(monkeypatch):
    launches: list[dict] = []
    monkeypatch.setattr(oz, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(oz, "async_playwright", _fake_playwright(launches, "Target page, context or browser has been closed"), raising=False)
    with pytest.raises(Exception, match="has been closed"):
        asyncio.run(oz.OzonBrowser(channel="chrome").start())
    assert len(launches) == 1
