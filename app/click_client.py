from __future__ import annotations

import csv
import io
import os
from datetime import date
from typing import Any

import httpx


class ClickRUError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ClickRUClient:
    """Minimal Click.ru API client. Secrets are read only from environment variables."""

    def __init__(self) -> None:
        self.base_url = os.getenv("CLICKRU_API_BASE_URL", "https://api.click.ru/V0").rstrip("/")
        self.token = os.getenv("CLICKRU_API_TOKEN", "").strip()
        self.user_id = os.getenv("CLICKRU_USER_ID", "").strip()
        self.timeout = float(os.getenv("CLICKRU_TIMEOUT_SECONDS", "30"))

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise ClickRUError("CLICKRU_API_TOKEN не задан в .env")
        headers = {"X-Auth-Token": self.token, "Accept": "application/json"}
        if self.user_id:
            headers["X-Auth-UserId"] = self.user_id
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = self._headers()
        headers.update(kwargs.pop("headers", {}))
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ClickRUError("Click.ru не ответил вовремя. Проверьте сеть и тайм-аут.") from exc
        except httpx.HTTPError as exc:
            raise ClickRUError(f"Ошибка соединения с Click.ru: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            details = "Не удалось выполнить запрос к Click.ru."
            try:
                body = response.json()
                details = str(body.get("detail") or body.get("message") or body.get("error") or details)
            except ValueError:
                pass
            if response.status_code == 401:
                details = "Click.ru отклонил токен (401). Проверьте токен в .env."
            elif response.status_code == 403:
                details = "Недостаточно прав Click.ru (403). Проверьте тип аккаунта и права."
            elif response.status_code == 429:
                details = "Превышен лимит API Click.ru (429). Повторите запрос позже."
            raise ClickRUError(details[:500], response.status_code)
        return response

    async def user(self) -> dict[str, Any]:
        response = await self._request("GET", "/user")
        data = response.json()
        return data.get("response", data)

    async def integrations(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/integrations", params={"service": "YANDEX_DIRECT"})
        data = response.json()
        envelope = data.get("response", data)
        if isinstance(envelope, dict):
            return envelope.get("integrations", [])
        return envelope if isinstance(envelope, list) else []

    async def accounts(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/accounts")
        data = response.json()
        envelope = data.get("response", data)
        if isinstance(envelope, dict):
            return envelope.get("accounts", [])
        return envelope if isinstance(envelope, list) else []

    async def campaigns(self, account_id: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if account_id is not None:
            params["accountId"] = account_id
        response = await self._request("GET", "/campaigns", params=params)
        data = response.json()
        envelope = data.get("response", data)
        return envelope.get("items", []) if isinstance(envelope, dict) else []

    async def report(self, date_from: date, date_to: date, account_ids: list[int] | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": "accountId,campaignId,date,impressions,clicks,loss",
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "includeHeaders": "true",
        }
        if account_ids:
            params["accountIds"] = ",".join(str(value) for value in account_ids)
        response = await self._request("GET", "/stat", params=params)
        text = response.text.strip()
        if not text:
            return []
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows: list[dict[str, Any]] = []
        for row in csv.DictReader(io.StringIO(text), dialect=dialect):
            normalized = {str(key).strip(): value for key, value in row.items() if key is not None}
            rows.append(normalized)
        return rows
