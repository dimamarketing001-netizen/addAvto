from __future__ import annotations

import asyncio
import csv
import io
import os
from datetime import date
from typing import Any

import httpx


# Временные ошибки Direct API: сервис недоступен/перегружен — повторяем.
TRANSIENT_DIRECT_ERRORS = {"52", "1000", "1001", "1002"}
# Паузы между повторами при 429 Click.ru (секунды).
CLICKRU_429_BACKOFF = (3.0, 8.0, 15.0)


class ClickRUError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ClickRUClient:
    """Minimal Click.ru API client. By default reads the token from
    CLICKRU_API_TOKEN in .env (back-compat — ParsYa's connect_direct_account.py
    always calls this through addAvto's HTTP API, never constructs this
    class itself, so that caller is unaffected either way). An explicit
    `token` overrides it — used when the operator picked a specific stored
    Click.ru connection (see db.py's clickru_connections table) instead of
    the single env-configured master account.
    """

    def __init__(self, token: str | None = None, user_id: str | None = None) -> None:
        self.base_url = os.getenv("CLICKRU_API_BASE_URL", "https://api.click.ru/V0").rstrip("/")
        self.token = (token if token is not None else os.getenv("CLICKRU_API_TOKEN", "")).strip()
        self.user_id = (user_id if user_id is not None else os.getenv("CLICKRU_USER_ID", "")).strip()
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
        # 429: Click.ru отклонил запрос из-за лимита — он НЕ выполнен, поэтому
        # повтор безопасен даже для POST (в т.ч. add_money). Ждём Retry-After
        # (не дольше 30 с) или 3/8/15 с.
        for attempt in range(len(CLICKRU_429_BACKOFF) + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                    response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                raise ClickRUError("Click.ru не ответил вовремя. Проверьте сеть и тайм-аут.") from exc
            except httpx.HTTPError as exc:
                raise ClickRUError(f"Ошибка соединения с Click.ru: {type(exc).__name__}") from exc
            if response.status_code != 429 or attempt >= len(CLICKRU_429_BACKOFF):
                break
            delay = CLICKRU_429_BACKOFF[attempt]
            try:
                delay = min(30.0, max(1.0, float(response.headers.get("Retry-After", ""))))
            except ValueError:
                pass
            await asyncio.sleep(delay)

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

    async def integrations(
        self,
        service: str = "YANDEX_DIRECT",
        with_access_token: bool = False,
    ) -> list[dict[str, Any]]:
        # Click.ru documents access_token for YANDEX_METRIKA only when
        # withAccessToken=true. The token remains server-side and is never
        # returned through an application endpoint.
        params: dict[str, Any] = {"service": service}
        if with_access_token:
            params["withAccessToken"] = "true"
        response = await self._request("GET", "/integrations", params=params)
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

    async def account(self, account_id: int) -> dict[str, Any]:
        """GET /accounts/{id}. По openapi.json Click.ru (Accounts.Account) тут
        есть serviceLogin и servicePassword — «Пароль в системе DIRECT
        (временный)», nullable. Пароль наружу не отдаём (см. main.py)."""
        response = await self._request("GET", f"/accounts/{int(account_id)}")
        data = response.json()
        envelope = data.get("response", data)
        if isinstance(envelope, dict) and isinstance(envelope.get("account"), dict):
            return envelope["account"]
        return envelope if isinstance(envelope, dict) else {}

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


    async def payers(self) -> list[dict[str, Any]]:
        """GET /users/payers — legal entities (юрлица) available under this
        Click.ru master account, with their balance. Needed before any real
        campaign launch: Russian ad-marking law (ОРД/маркировка, ERID)
        requires ads to be tied to a registered payer/legal entity.
        """
        response = await self._request("GET", "/users/payers")
        data = response.json()
        envelope = data.get("response", data)
        if isinstance(envelope, dict):
            return envelope.get("payers", []) or envelope.get("items", [])
        return envelope if isinstance(envelope, list) else []

    async def create_payer(self, payload: dict[str, Any]) -> int:
        """POST /users/payers — creates a BUSINESS/INDIVIDUAL/PERSON payer
        (юрлицо/ИП/физлицо) directly, confirmed in Click.ru's own
        openapi.json (2026-11-02) — contrary to the earlier assumption that
        this had to be done manually in the Click.ru dashboard. `payload`
        is the already-camelCased, type-specific body (see main.py's
        `_build_payer_payload`); Click.ru rejects it with a 422 listing the
        missing/invalid fields if the shape is wrong for the chosen type —
        not pre-validated further here.
        """
        response = await self._request("POST", "/users/payers", json=payload)
        data = response.json()
        envelope = data.get("response", data)
        payer_id = envelope.get("payerId") if isinstance(envelope, dict) else None
        if payer_id is None:
            raise ClickRUError(f"Click.ru не вернул payerId: {data}")
        return int(payer_id)

    async def marking_schema(self, account_id: int) -> dict[str, Any]:
        """GET /markings/schemas/account?accountId=<id> — whether this
        Direct account has a marking (ОРД/ERID) scheme configured, and
        what it requires. MUST be checked and confirmed configured before
        any real campaign launch (activating a DRAFT campaign) — creating
        a DRAFT itself doesn't need this, only actually serving ads does.
        """
        response = await self._request("GET", "/markings/schemas/account", params={"accountId": account_id})
        data = response.json()
        envelope = data.get("response", data)
        return envelope if isinstance(envelope, dict) else {"result": envelope}

    async def create_direct_account(
        self, name: str, integration_id: int, payer_id: int | None = None
    ) -> dict[str, Any]:
        """payer_id is passed through as `payerId` if given — NOT confirmed
        live whether Click.ru's POST /accounts actually accepts/uses it at
        creation time, or whether the payer/ERID marking is always a
        separate manual step in the dashboard afterwards (see TASKS.md,
        "payerId в POST /accounts не выяснено"). Omitted from the body
        entirely when not given, so this stays a no-op change until that's
        confirmed.
        """
        body: dict[str, Any] = {"service": "DIRECT", "name": name, "integrationId": integration_id}
        if payer_id is not None:
            body["payerId"] = payer_id
        response = await self._request("POST", "/accounts", json=body)
        data = response.json()
        envelope = data.get("response", data)
        return envelope if isinstance(envelope, dict) else {"result": envelope}

    async def direct_api(
        self,
        client_login: str,
        method_path: str,
        body: dict[str, Any],
        api_version: str = "v5",
        accept_language: str | None = None,
    ) -> dict[str, Any]:
        """Low-level pass-through to Yandex Direct API v5 via Click.ru's
        proxy (POST /api_proxy/yandex_direct). `method_path` is the Direct
        API endpoint, e.g. "campaigns" for Campaigns.add/get. Click.ru
        injects the OAuth token for the integration behind `client_login`
        server-side — we only say which Direct account to act on.

        Yandex Direct API v5 itself returns HTTP 200 even for method-level
        errors (error details live inside the JSON body), so errors are
        checked there, not via HTTP status.
        """
        url = f"https://api.direct.yandex.com/json/{api_version}/{method_path.lstrip('/')}"
        headers = {"Client-Login": client_login}
        if accept_language:
            # Direct локализует справочники (названия регионов и т.п.) по
            # Accept-Language; без него может отдать английские названия.
            headers["Accept-Language"] = accept_language
        # Временные ошибки Директа («The service is temporarily unavailable»,
        # код 1000 — живой запуск 2026-11-11 оставил аккаунт с недоделанной
        # кампанией) повторяем: запрос с такой ошибкой Директ не выполнял.
        for attempt in range(3):
            response = await self._request(
                "POST",
                "/api_proxy/yandex_direct",
                params={"url": url},
                headers=headers,
                json=body,
            )
            data = response.json()
            if isinstance(data, dict) and "error" in data:
                error = data["error"]
                code = str(error.get("error_code"))
                if code in TRANSIENT_DIRECT_ERRORS and attempt < 2:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                raise ClickRUError(
                    f"Yandex Direct API error {error.get('error_code')}: "
                    f"{error.get('error_string')} — {error.get('error_detail')}",
                    response.status_code,
                )
            return data
        return data

    async def create_draft_text_campaign(
        self,
        client_login: str,
        name: str,
        start_date: str,
        average_cpc_micros: int = 10_000_000,
        target_drr: float | None = None,
        goal_id: int | None = None,
    ) -> int:
        """Creates a minimal valid TextCampaign. Confirmed live 2026-10-26:
        a freshly-created campaign is a DRAFT by default — it cannot serve
        or spend until ads are added and it passes moderation, and Yandex
        itself refuses Campaigns.suspend on a draft ("Кампания является
        черновиком и не может быть остановлена") because there's nothing
        to stop. So no extra "make it safe" step is needed here — don't
        add one back without a real reason; it'll just error.

        BiddingStrategyType SERVING_OFF on both Search and Network at
        creation time is rejected by Yandex Direct ("these strategies are
        not compatible") — SERVING_OFF is only a valid later *update* on an
        existing campaign, not a creation-time combination.

        Default strategy is a minimal AVERAGE_CPC (kept for the existing
        single-campaign endpoint). When target_drr+goal_id are given (the
        bulk-generation flow, which always uses ДРР per product decision),
        use AVERAGE_CRR instead — it REQUIRES a Metrica GoalId (the 'lead'
        goal set up via /metrika/setup), there's no CRR strategy without one.
        """
        if target_drr is not None and goal_id is not None:
            search_block: dict[str, Any] = {
                "BiddingStrategyType": "AVERAGE_CRR",
                "AverageCrr": {"Crr": round(target_drr), "GoalId": goal_id},
            }
            # Seen live: AVERAGE_CRR on BOTH Search and Network is rejected
            # ("these strategies are not compatible") — same rule as
            # AVERAGE_CPC below, Network takes NETWORK_DEFAULT regardless of
            # what Search uses, not a mirrored copy of it.
            network_block: dict[str, Any] = {
                "BiddingStrategyType": "NETWORK_DEFAULT",
                "NetworkDefault": {"LimitPercent": 100},
            }
        else:
            search_block = {
                "BiddingStrategyType": "AVERAGE_CPC",
                "AverageCpc": {"AverageCpc": average_cpc_micros},
            }
            network_block = {
                "BiddingStrategyType": "NETWORK_DEFAULT",
                # LimitPercent is accepted but flagged "not supported
                # for this strategy" (warning, not error) — harmless,
                # kept only because an empty {} here gets misread as
                # an array by Click.ru's (PHP) backend.
                "NetworkDefault": {"LimitPercent": 100},
            }

        body = {
            "method": "add",
            "params": {
                "Campaigns": [
                    {
                        "Name": name,
                        "StartDate": start_date,
                        "TextCampaign": {
                            "BiddingStrategy": {
                                "Search": search_block,
                                "Network": network_block,
                            },
                        },
                    }
                ]
            },
        }
        return await self._add_campaign(client_login, body)

    async def create_draft_unified_campaign(
        self,
        client_login: str,
        name: str,
        start_date: str,
        target_drr: float,
        goal_id: int,
        counter_id: int | None = None,
    ) -> int:
        """ЕПК идут ТОЛЬКО через Direct API v501 (документация: «Для работы с
        единой перфоманс-кампанией используется адрес .../v501/»); в v5
        Click.ru-прокси отвечал «unknown parameter UnifiedCampaign» (живой
        запуск 2026-11-11). Группы ЕПК — UnifiedAdGroup, тоже v501.

        Creates a minimal valid UnifiedCampaign (ЕПК — Search + РСЯ in
        one campaign). NOT yet confirmed live against the real Direct API
        the way create_draft_text_campaign is (that one has "seen live"
        notes throughout from iterative real testing) — this mirrors the
        same AVERAGE_CRR/NETWORK_DEFAULT Search+Network shape documented
        for Direct API v5's UnifiedCampaign, by analogy with TextCampaign,
        since both expose the same BiddingStrategy vocabulary. Test with
        a single real campaign (bulk-create count=1, campaign_mode=
        single_epk) before relying on this for real ЕПК launches — if
        Yandex rejects the shape, the error message from Errors will say
        why and this needs a live-debugging pass same as the TextCampaign
        constraints were found (SERVING_OFF/AVERAGE_CRR-on-both-blocks
        rejections documented above).
        """
        body = {
            "method": "add",
            "params": {
                "Campaigns": [
                    {
                        "Name": name,
                        "StartDate": start_date,
                        "UnifiedCampaign": {
                            "BiddingStrategy": {
                                "Search": {
                                    "BiddingStrategyType": "AVERAGE_CRR",
                                    "AverageCrr": {"Crr": round(target_drr), "GoalId": goal_id},
                                },
                                # В ЕПК у NETWORK_DEFAULT нет объекта NetworkDefault
                                # (v501: «unknown parameter …Network.NetworkDefault», 2026-11-11).
                                "Network": {"BiddingStrategyType": "NETWORK_DEFAULT"},
                            },
                            **({"CounterIds": {"Items": [int(counter_id)]}} if counter_id else {}),
                        },
                    }
                ]
            },
        }
        return await self._add_campaign(client_login, body, api_version="v501")

    async def _add_campaign(self, client_login: str, body: dict[str, Any], api_version: str = "v5") -> int:
        data = await self.direct_api(client_login, "campaigns", body, api_version=api_version)
        add_results = (data.get("result") or {}).get("AddResults") or []
        if not add_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = add_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось создать кампанию: {errors}")
        return int(first["Id"])

    async def create_adgroup(
        self,
        client_login: str,
        campaign_id: int,
        name: str,
        region_ids: list[int],
        negative_keywords: list[str] | None = None,
        unified: bool = False,
    ) -> int:
        """unified=True — группа ЕПК (UnifiedAdGroup, Direct API v501).

        AdGroups.add for a TextAdGroup — minimal required fields
        (Name, CampaignId, RegionIds) plus optional NegativeKeywords
        (shared by every keyword in the group; Yandex caps this at 3
        phrases, <=7 words each, <=4096 chars combined — not pre-validated
        here, same "let Direct's own error be the validator" policy as
        headline/text length elsewhere). Keywords and ads are separate
        calls (Keywords.add / Ads.add), not nestable here.
        """
        group: dict[str, Any] = {"Name": name, "CampaignId": campaign_id, "RegionIds": region_ids}
        if negative_keywords:
            group["NegativeKeywords"] = {"Items": negative_keywords}
        if unified:
            # По схеме v501 OfferRetargeting обязателен (хотя помечен устаревшим).
            group["UnifiedAdGroup"] = {"OfferRetargeting": "NO"}
        body = {
            "method": "add",
            "params": {"AdGroups": [group]},
        }
        data = await self.direct_api(client_login, "adgroups", body, api_version="v501" if unified else "v5")
        add_results = (data.get("result") or {}).get("AddResults") or []
        if not add_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = add_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось создать группу объявлений: {errors}")
        return int(first["Id"])

    async def add_keywords(self, client_login: str, ad_group_id: int, phrases: list[str], api_version: str = "v5") -> list[int]:
        """Keywords.add — one call for all phrases in this ad group. Partial
        failure (some phrases rejected, others fine) raises with every
        rejected phrase named, rather than silently dropping any.
        """
        if not phrases:
            return []
        body = {
            "method": "add",
            "params": {"Keywords": [{"Keyword": phrase, "AdGroupId": ad_group_id} for phrase in phrases]},
        }
        data = await self.direct_api(client_login, "keywords", body, api_version=api_version)
        add_results = (data.get("result") or {}).get("AddResults") or []
        ids: list[int] = []
        failures: list[str] = []
        for phrase, item in zip(phrases, add_results):
            if item.get("Errors"):
                detail = "; ".join(e.get("Message", "") for e in item["Errors"])
                failures.append(f"{phrase!r}: {detail}")
            else:
                ids.append(int(item["Id"]))
        if failures:
            raise ClickRUError("Не все ключевые фразы добавлены: " + "; ".join(failures))
        return ids

    async def create_text_ad(
        self,
        client_login: str,
        ad_group_id: int,
        title: str,
        text: str,
        href: str,
        title2: str | None = None,
        ad_image_hash: str | None = None,
        sitelink_set_id: int | None = None,
        ad_extension_ids: list[int] | None = None,
        api_version: str = "v5",
    ) -> int:
        """Ads.add for a single TextAd. Title <=56 chars, Text <=81 chars,
        Title2 <=30 chars — Yandex Direct enforces this itself with a clear
        error; not pre-truncated here, since silently cutting ad copy is
        worse than a loud validation failure.
        """
        text_ad: dict[str, Any] = {"Title": title, "Text": text, "Href": href, "Mobile": "NO"}
        if title2:
            text_ad["Title2"] = title2
        if ad_image_hash:
            text_ad["AdImageHash"] = ad_image_hash
        if sitelink_set_id:
            text_ad["SitelinkSetId"] = sitelink_set_id
        if ad_extension_ids:
            text_ad["AdExtensionIds"] = ad_extension_ids

        body = {
            "method": "add",
            "params": {"Ads": [{"AdGroupId": ad_group_id, "TextAd": text_ad}]},
        }
        data = await self.direct_api(client_login, "ads", body, api_version=api_version)
        add_results = (data.get("result") or {}).get("AddResults") or []
        if not add_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = add_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось создать объявление: {errors}")
        return int(first["Id"])

    async def create_sitelinks_set(
        self, client_login: str, sitelinks: list[dict[str, str]]
    ) -> int:
        """Sitelinks.add — one set of 1-8 {Title (<=30), Href, Description
        (<=60)} items, referenced from a TextAd via SitelinkSetId. A set is
        immutable-by-reuse in Yandex Direct's model (campaigns reference a
        set, not individual links), so each campaign that wants a different
        combination needs its own set.
        """
        body = {
            "method": "add",
            "params": {"SitelinksSets": [{"Sitelinks": sitelinks}]},
        }
        data = await self.direct_api(client_login, "sitelinks", body)
        add_results = (data.get("result") or {}).get("AddResults") or []
        if not add_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = add_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось создать быстрые ссылки: {errors}")
        return int(first["Id"])

    async def create_callout(self, client_login: str, text: str) -> int:
        """AdExtensions.add for ONE callout (<=25 chars). Each callout gets
        its own AdExtensionId; a TextAd references up to 50 of them via
        AdExtensionIds — unlike sitelinks, callouts are added individually
        and referenced as a list, not grouped into a set server-side.
        """
        body = {
            "method": "add",
            "params": {"AdExtensions": [{"Callout": {"CalloutText": text}}]},
        }
        data = await self.direct_api(client_login, "adextensions", body)
        add_results = (data.get("result") or {}).get("AddResults") or []
        if not add_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = add_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось создать уточнение: {errors}")
        return int(first["Id"])

    async def create_marking_schema(
        self, account_id: int, payer_id: int, schema_type: str = "END_ADVERTISER", account_schema_type: str = "MAIN",
    ) -> int | None:
        """POST /markings/schemas/create — схема маркировки (ОРД/ERID) для
        рекламного аккаунта. Без неё Click.ru не даёт пополнить аккаунт
        («заполните схему маркировки»). END_ADVERTISER — юрлицо-плательщик
        само является конечным рекламодателем, посредники не нужны.
        Возвращает schemeId."""
        response = await self._request("POST", "/markings/schemas/create", json={
            "accountId": int(account_id), "accountSchemaType": account_schema_type,
            "schemaType": schema_type, "payerId": int(payer_id), "items": [], "agreeWithNotices": True,
        })
        data = response.json()
        envelope = data.get("response", data) if isinstance(data, dict) else {}
        scheme_id = envelope.get("schemeId") if isinstance(envelope, dict) else None
        return int(scheme_id) if scheme_id else None

    async def add_money(self, account_id: int, amount_rub: float, payer_id: int | None = None) -> int | None:
        """POST /payments/add_money — пополнить рекламный аккаунт с баланса
        менеджерского аккаунта Click.ru. amount — «в рублях в т.ч. НДС»
        (openapi.json), payerId — юрлицо, с баланса которого (0 — физлицо).
        РЕАЛЬНОЕ списание денег. Возвращает task_id для статуса."""
        response = await self._request("POST", "/payments/add_money", json={
            "accountId": int(account_id), "payerId": int(payer_id or 0), "amount": round(float(amount_rub), 2),
        })
        data = response.json()
        envelope = data.get("response", data) if isinstance(data, dict) else {}
        task_id = envelope.get("task_id") if isinstance(envelope, dict) else None
        return int(task_id) if task_id else None

    async def add_money_status(self, task_id: int) -> str:
        """NEW | PROCESSING | ERROR | CANCELLED | SUCCESS."""
        response = await self._request("GET", "/payments/add_money/status", params={"task_id": int(task_id)})
        data = response.json()
        envelope = data.get("response", data) if isinstance(data, dict) else {}
        return str((envelope or {}).get("status") or "")

    async def moderate_campaign_ads(self, client_login: str, campaign_id: int, api_version: str = "v5") -> int:
        """Отправить на модерацию все черновики объявлений кампании
        (Ads.moderate принимает только статус DRAFT). Возвращает сколько ушло."""
        ads = (await self.direct_api(client_login, "ads", {"method": "get", "params": {
            "SelectionCriteria": {"CampaignIds": [int(campaign_id)]}, "FieldNames": ["Id", "Status"],
        }}, api_version=api_version)).get("result", {}).get("Ads") or []
        draft_ids = [int(a["Id"]) for a in ads if a.get("Status") == "DRAFT"]
        if not draft_ids:
            return 0
        data = await self.direct_api(client_login, "ads", {"method": "moderate", "params": {
            "SelectionCriteria": {"Ids": draft_ids},
        }}, api_version=api_version)
        errors = [e for r in (data.get("result") or {}).get("ModerateResults") or [] for e in r.get("Errors") or []]
        if errors:
            raise ClickRUError("Не удалось отправить на модерацию: " + "; ".join(
                f"{e.get('Message')} ({e.get('Details')})" for e in errors[:3]))
        return len(draft_ids)

    async def resume_campaign(self, client_login: str, campaign_id: int) -> None:
        """Campaigns.resume — the ONLY step in this whole app that actually
        makes a campaign eligible to serve and spend budget (every campaign
        created here starts DRAFT/OFF). Caller MUST have already confirmed
        an ОРД/ERID marking scheme is configured for the account (see
        ClickRUClient.marking_schema) — Yandex itself may also reject this
        if marking isn't set up, but that check should happen earlier so
        the operator sees a clear reason before this call, not a cryptic
        Direct API error.
        """
        body = {
            "method": "resume",
            "params": {"SelectionCriteria": {"Ids": [campaign_id]}},
        }
        data = await self.direct_api(client_login, "campaigns", body)
        resume_results = (data.get("result") or {}).get("ResumeResults") or []
        if not resume_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = resume_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось запустить кампанию: {errors}")

    async def add_mobile_bid_adjustment(self, client_login: str, campaign_id: int, percent: int) -> None:
        """BidModifiers.add — a campaign-level "корректировка ставки" for
        mobile traffic (percent relative to the base bid; Direct allows
        only ONE mobile adjustment per campaign and rejects a second add).
        No OperatingSystemType given = applies to all mobile OSes as one
        blanket adjustment, the simplest useful case.
        """
        body = {
            "method": "add",
            "params": {"BidModifiers": [{"CampaignId": campaign_id, "MobileAdjustment": {"BidModifier": percent}}]},
        }
        data = await self.direct_api(client_login, "bidmodifiers", body)
        add_results = (data.get("result") or {}).get("AddResults") or []
        if not add_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = add_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось настроить корректировку для мобильных: {errors}")

    async def dictionaries(self, client_login: str, names: list[str]) -> dict[str, Any]:
        """Dictionaries.get — static Yandex Direct reference data (region
        tree, timezones, etc.), identical regardless of which real Direct
        account's `client_login` routes the call through Click.ru (the data
        itself isn't account-specific) — any already-connected account
        works as the routing login.
        """
        body = {"method": "get", "params": {"DictionaryNames": names}}
        data = await self.direct_api(client_login, "dictionaries", body, accept_language="ru")
        result = data.get("result")
        if not isinstance(result, dict):
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        return result

    async def suspend_campaign(self, client_login: str, campaign_id: int) -> None:
        """Campaigns.suspend — the kill-switch counterpart to resume_campaign.
        Stops serving without deleting anything; the campaign can be resumed
        again later. Added 2026-10-31 alongside the bulk-launch wizard so a
        mass-activated batch always has an equally direct way to stop.
        """
        body = {
            "method": "suspend",
            "params": {"SelectionCriteria": {"Ids": [campaign_id]}},
        }
        data = await self.direct_api(client_login, "campaigns", body)
        suspend_results = (data.get("result") or {}).get("SuspendResults") or []
        if not suspend_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = suspend_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось остановить кампанию: {errors}")

    async def upload_ad_image(self, client_login: str, name: str, image_base64: str) -> str:
        """AdImages.add — Yandex Direct takes the raw image as base64, never
        a URL (the caller downloads the image first, see main.py). Returns
        AdImageHash, referenced from a TextAd via AdImageHash."""
        body = {
            "method": "add",
            "params": {"AdImages": [{"ImageData": image_base64, "Name": name[:255]}]},
        }
        data = await self.direct_api(client_login, "adimages", body)
        add_results = (data.get("result") or {}).get("AddResults") or []
        if not add_results:
            raise ClickRUError(f"Yandex Direct API вернул пустой результат: {data}")
        first = add_results[0]
        if first.get("Errors"):
            errors = "; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in first["Errors"])
            raise ClickRUError(f"Не удалось загрузить изображение: {errors}")
        return str(first["AdImageHash"])
