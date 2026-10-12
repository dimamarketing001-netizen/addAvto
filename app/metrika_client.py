from __future__ import annotations

from typing import Any

import httpx


class MetrikaAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class YandexMetrikaClient:
    """Yandex Metrica Management API client. OAuth tokens are never returned to callers."""

    BASE_URL = "https://api-metrika.yandex.net"

    def __init__(self, access_token: str, timeout: float = 30.0) -> None:
        self.access_token = access_token.strip()
        self.timeout = timeout
        if not self.access_token:
            raise MetrikaAPIError("Для интеграции не получен OAuth-токен Яндекс.Метрики.")

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {
            "Authorization": f"OAuth {self.access_token}",
            "Accept": "application/json",
        }
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.request(
                    method, f"{self.BASE_URL}{path}", headers=headers, **kwargs
                )
        except httpx.TimeoutException as exc:
            raise MetrikaAPIError("Яндекс.Метрика не ответила вовремя.") from exc
        except httpx.HTTPError as exc:
            raise MetrikaAPIError(
                f"Ошибка соединения с Яндекс.Метрикой: {type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            message = "Не удалось выполнить запрос к Яндекс.Метрике."
            try:
                body = response.json()
                if isinstance(body, dict):
                    errors = body.get("errors")
                    if isinstance(errors, list) and errors:
                        first = errors[0]
                        if isinstance(first, dict):
                            message = str(first.get("message") or first.get("text") or message)
                    message = str(body.get("message") or message)
            except ValueError:
                pass
            if response.status_code in (401, 403):
                message = (
                    "У интеграции Click.ru нет доступа к этому счётчику или токен Метрики "
                    "не подходит. Проверьте подключённый Яндекс ID и его права."
                )
            elif response.status_code == 429:
                message = "Яндекс.Метрика ограничила частоту запросов. Повторите позже."
            raise MetrikaAPIError(message[:500], response.status_code)

        try:
            data = response.json()
        except ValueError as exc:
            raise MetrikaAPIError("Яндекс.Метрика вернула ответ в неизвестном формате.") from exc
        return data if isinstance(data, dict) else {}

    async def list_counters(self) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", "/management/v1/counters", params={"field": "code,goals"}
        )
        items = data.get("counters", [])
        return items if isinstance(items, list) else []

    async def list_goals(self, counter_id: int) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", f"/management/v1/counter/{counter_id}/goals"
        )
        items = data.get("goals", [])
        return items if isinstance(items, list) else []

    async def create_counter(self, name: str, site: str) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/management/v1/counters",
            params={"field": "code,goals"},
            json={"counter": {"name": name, "site": site}},
        )
        counter = data.get("counter")
        if not isinstance(counter, dict) or not counter.get("id"):
            raise MetrikaAPIError("Метрика не вернула ID созданного счётчика.")
        return counter

    async def create_goal(
        self, counter_id: int, default_price: float | None = None
    ) -> dict[str, Any]:
        goal: dict[str, Any] = {
            "name": "lead",
            "type": "action",
            "conditions": [{"type": "exact", "url": "lead"}],
        }
        if default_price is not None and default_price > 0:
            goal["default_price"] = default_price
        data = await self._request(
            "POST",
            f"/management/v1/counter/{counter_id}/goals",
            json={"goal": goal},
        )
        created = data.get("goal")
        if not isinstance(created, dict) or not created.get("id"):
            raise MetrikaAPIError("Метрика не вернула ID созданной цели lead.")
        return created
