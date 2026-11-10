from __future__ import annotations

import os
from typing import Any

import httpx


class ParsYaPoolError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ParsYaPoolClient:
    """Reads Yandex-account data (login + Metrika OAuth token) from ParsYa's
    shared MySQL pool, via a read-only endpoint on parsya-control.

    parsya-control runs on the same WSL machine as ParsYa's scraper and its
    MySQL database; this server reaches it over the existing reverse SSH
    tunnel (127.0.0.1:8091 here is really WSL, see PROJECT_OVERVIEW.md in
    ParsYa) — no new network path needed, just a new endpoint on the app
    that's already reachable.
    """

    def __init__(self) -> None:
        self.base_url = os.getenv("PARSYA_POOL_API_URL", "http://127.0.0.1:8091").rstrip("/")
        self.token = os.getenv("PARSYA_POOL_API_TOKEN", "").strip()
        self.timeout = float(os.getenv("PARSYA_POOL_API_TIMEOUT_SECONDS", "10"))

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def _request(self, path: str) -> dict[str, Any]:
        if not self.token:
            raise ParsYaPoolError("PARSYA_POOL_API_TOKEN не задан в .env")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.get(
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise ParsYaPoolError("ParsYa pool API не ответил вовремя.") from exc
        except httpx.HTTPError as exc:
            raise ParsYaPoolError(f"Ошибка соединения с ParsYa pool API: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail"))
            except ValueError:
                detail = f"HTTP {response.status_code}"
            raise ParsYaPoolError(detail, response.status_code)
        return response.json()

    async def list_accounts(self) -> list[dict[str, Any]]:
        data = await self._request("/api/addavto/yandex-pool-accounts")
        return data.get("items", [])

    async def get_account(self, login: str) -> dict[str, Any]:
        from urllib.parse import quote

        return await self._request(f"/api/addavto/yandex-pool-accounts/{quote(login, safe='')}")

    async def _post(self, path: str, payload: dict[str, Any], timeout: float, timeout_message: str) -> dict[str, Any]:
        if not self.token:
            raise ParsYaPoolError("PARSYA_POOL_API_TOKEN не задан в .env")
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ParsYaPoolError(timeout_message) from exc
        except httpx.HTTPError as exc:
            raise ParsYaPoolError(f"Ошибка соединения с ParsYa pool API: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail"))
            except ValueError:
                detail = f"HTTP {response.status_code}"
            raise ParsYaPoolError(detail, response.status_code)
        return response.json()

    async def provision_direct_account(
        self,
        account_name_prefix: str | None = None,
        clickru: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Claims one fresh pool login and runs the Selenium-driven
        connect-to-Click.ru + create-Direct-account pipeline on WSL (see
        connect_direct_account.py / provision_next_direct_account.py in
        ParsYa). Slow — real browser automation, not a quick DB read like
        the other methods here — budget up to ~3 minutes per call.

        `clickru` — веб-кабинет выбранного подключения Click.ru:
        {"connection_id", "username", "password", "cookies"}. Без него
        скрипт работает по-старому, от CLICKRU_WEB_* из .env ParsYa (только
        для подключения, совпадающего с CLICKRU_API_TOKEN, см. main.py).
        В ответе может прийти "clickru_cookies" — обновлённая сессия.
        """
        payload: dict[str, Any] = {}
        if account_name_prefix:
            payload["account_name_prefix"] = account_name_prefix
        if clickru:
            payload["clickru"] = clickru
        return await self._post(
            "/api/addavto/provision-direct-account", payload, 300.0,
            "Провижининг аккаунта не успел завершиться вовремя.",
        )

    async def ozon_fetch_pages(
        self, path: str, extra_paths: list[str], session_id: str, label: str = "", max_images: int = 0,
    ) -> dict[str, Any]:
        """Карточка Ozon через настоящий Chrome в WSL (с IP прод-сервера Ozon
        отдаёт 403 любому браузеру). Ответ: {"pages": [{widgetStates, seo}
        | None, ...], "images": [{url, mime, b64}, ...]} — страницы в формате
        JSON API Ozon и до max_images фото товара, скачанных тем же Chrome
        (CDN Ozon режет сервер). Если Ozon показал капчу, её проходит человек
        (окно в визарде / ссылка в Telegram, session_id) — таймаут до ~11 мин.
        ParsYaPoolError: 409 — капчу не прошли вовремя, 503 — Chrome занят.
        """
        result = await self._post(
            "/api/addavto/ozon-fetch",
            {"path": path, "extra_paths": extra_paths, "session_id": session_id, "label": label,
             "max_images": max_images},
            660.0,
            "Chrome в WSL не вернул карточку Ozon вовремя.",
        )
        return {"pages": list(result.get("pages") or []), "images": list(result.get("images") or [])}

    async def clickru_web_login(
        self, username: str, password: str, cookies: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Вход в веб-кабинет click.ru через Chrome в WSL (тот же браузер и
        IP, откуда потом идёт провижининг). Сначала пробует сохранённые
        cookies, при необходимости логинится паролем.
        Ответ: {"ok": true, "cookies": [...], "relogged": bool} или
        ParsYaPoolError (409 — вход не удался, detail — причина).
        """
        return await self._post(
            "/api/addavto/clickru-web-login",
            {"username": username, "password": password, "cookies": cookies or []},
            180.0,
            "Вход в кабинет Click.ru не успел завершиться вовремя.",
        )
