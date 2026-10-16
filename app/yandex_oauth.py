from __future__ import annotations

import base64
import hashlib
import os
from typing import Any
from urllib.parse import urlencode

import httpx


class YandexOAuthError(RuntimeError):
    pass


class YandexOAuthClient:
    AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
    TOKEN_URL = "https://oauth.yandex.ru/token"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, timeout: float = 30):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.redirect_uri = redirect_uri.strip()
        self.timeout = timeout
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise YandexOAuthError(
                "Настройте METRIKA_OAUTH_CLIENT_ID, METRIKA_OAUTH_CLIENT_SECRET и "
                "METRIKA_OAUTH_REDIRECT_URI в серверном .env."
            )

    @classmethod
    def from_env(cls) -> "YandexOAuthClient":
        return cls(
            client_id=os.getenv("METRIKA_OAUTH_CLIENT_ID", ""),
            client_secret=os.getenv("METRIKA_OAUTH_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("METRIKA_OAUTH_REDIRECT_URI", ""),
            timeout=float(os.getenv("METRIKA_OAUTH_TIMEOUT_SECONDS", "30")),
        )

    @staticmethod
    def pkce_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def authorization_url(self, state: str, code_verifier: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "metrika:read metrika:write",
            "state": state,
            "force_confirm": "yes",
            "code_challenge": self.pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        return self.AUTHORIZE_URL + "?" + urlencode(params)

    async def _post_token(self, payload: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    self.TOKEN_URL,
                    data=payload,
                    headers={"Accept": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise YandexOAuthError("Яндекс OAuth не ответил вовремя.") from exc
        except httpx.HTTPError as exc:
            raise YandexOAuthError(
                f"Ошибка соединения с Яндекс OAuth: {type(exc).__name__}"
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            error_code = str(body.get("error", "")) if isinstance(body, dict) else ""
            safe_messages = {
                "invalid_grant": "Код авторизации истёк или уже использован. Повторите подключение.",
                "invalid_client": "Яндекс OAuth отклонил Client ID или Client Secret. Проверьте настройки OAuth-приложения.",
                "invalid_request": "Яндекс OAuth отклонил параметры запроса. Проверьте Redirect URI и настройки приложения.",
                "unauthorized_client": "OAuth-приложению не разрешён выбранный тип авторизации.",
            }
            raise YandexOAuthError(
                safe_messages.get(error_code, f"Не удалось обменять код на токен Яндекса (HTTP {response.status_code}).")
            )
        if not isinstance(body, dict) or not body.get("access_token"):
            raise YandexOAuthError("Яндекс OAuth вернул ответ без access_token.")
        return body

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        if not code.strip():
            raise YandexOAuthError("Яндекс не вернул код авторизации.")
        return await self._post_token({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        })

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        if not refresh_token.strip():
            raise YandexOAuthError("Нет refresh-токена. Подключите Яндекс ID заново.")
        return await self._post_token({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })

    async def revoke(self, access_token: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    "https://oauth.yandex.ru/revoke_token",
                    data={
                        "access_token": access_token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise YandexOAuthError(
                f"Ошибка соединения при отзыве токена: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise YandexOAuthError(
                f"Яндекс OAuth не подтвердил отзыв токена (HTTP {response.status_code})."
            )
