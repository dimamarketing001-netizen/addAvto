from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import random
import secrets
import time as _time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, lk
from .click_client import ClickRUClient, ClickRUError
from .metrika_client import MetrikaAPIError, YandexMetrikaClient
from .parsya_pool_client import ParsYaPoolClient, ParsYaPoolError
from .schemas import (
    BulkCampaignCreateRequest,
    BulkCampaignItemResult,
    BulkJobOut,
    BulkJobStartOut,
    CampaignPlan,
    ClickRUConnectionIn,
    PayerCreateIn,
    DirectAccountCreate,
    PlanItem,
    ProjectIn,
)
from .token_crypto import TokenCryptoError, decrypt_secret, encrypt_secret
from .yandex_oauth import YandexOAuthClient, YandexOAuthError


security = HTTPBasic()
templates = Jinja2Templates(directory="app/templates")
# asyncio.create_task() doesn't keep its own strong reference — without this
# the task object could be garbage-collected mid-run on some event loop
# implementations. Standard "fire and forget" pattern per the asyncio docs.
_BACKGROUND_JOB_TASKS: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    lk.init_lk_db()
    worker = None
    if os.getenv("LK_WORKER", "1") != "0":
        worker = asyncio.create_task(lk.worker_loop(_lk_metrika_client))
    yield
    if worker:
        worker.cancel()


app = FastAPI(
    title="Direct Campaign Manager",
    description="Подготовка и мониторинг проектов Яндекс Директа через Click.ru.",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def require_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    expected_user = os.getenv("APP_USERNAME", "admin")
    expected_password = os.getenv("APP_PASSWORD", "local-only-change-this-password")
    user_ok = hmac.compare_digest(credentials.username.encode(), expected_user.encode())
    password_ok = hmac.compare_digest(credentials.password.encode(), expected_password.encode())
    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    if os.getenv("APP_ENV", "development").lower() == "production":
        if expected_password == "local-only-change-this-password" or len(expected_password) < 16:
            raise HTTPException(status_code=500, detail="Задайте в .env APP_PASSWORD длиной от 16 символов.")
    return credentials.username


AuthUser = Annotated[str, Depends(require_auth)]

_login_failures: dict[str, list[float]] = {}


def require_member(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> lk.Member:
    """Администратор (из .env) или пользователь ЛК (из БД). Остальные разделы приложения — только админу."""
    username = credentials.username
    admin_user = os.getenv("APP_USERNAME", "admin")
    if hmac.compare_digest(username.encode(), admin_user.encode()):
        require_auth(credentials)
        return lk.Member(username=username, is_admin=True)

    recent = [t for t in _login_failures.get(username, []) if t > _time.time() - 600]
    if len(recent) >= 8:
        raise HTTPException(status_code=429, detail="Слишком много неудачных попыток входа. Подождите 10 минут.")
    if lk.verify_user(username, credentials.password):
        _login_failures.pop(username, None)
        return lk.Member(username=username)
    recent.append(_time.time())
    _login_failures[username] = recent[-20:]
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверный логин или пароль",
        headers={"WWW-Authenticate": "Basic"},
    )


MemberUser = Annotated[lk.Member, Depends(require_member)]


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, _: AuthUser):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/lk", response_class=HTMLResponse)
async def lk_page(request: Request, user: MemberUser):
    return templates.TemplateResponse(
        request=request, name="lk.html", context={"user": user.username, "is_admin": user.is_admin}
    )


@app.get("/health")
async def health():
    return {"status": "ok", "app": "direct-campaign-manager"}


def _resolve_clickru_client(connection_id: int | None) -> ClickRUClient:
    """Wizard flow (2026-10-31) lets the operator pick from several stored
    Click.ru master-account tokens instead of the single CLICKRU_API_TOKEN
    in .env — resolve whichever was asked for. No connection_id falls back
    to the env token, for the handful of endpoints that still work that way
    (/api/accounts/direct, used by ParsYa's script, never passes one).
    """
    if connection_id is None:
        return ClickRUClient()
    connection = db.get_clickru_connection(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Подключение Click.ru не найдено")
    return ClickRUClient(token=connection["token"], user_id=connection.get("user_id") or None)


@app.get("/api/clickru-connections")
async def list_clickru_connections(_: AuthUser):
    items = db.list_clickru_connections()
    return {
        "items": [
            {
                "id": item["id"],
                "label": item["label"],
                "user_id": item["user_id"],
                "token_preview": "…" + item["token"][-4:] if len(item["token"]) >= 4 else "…",
                "created_at": item["created_at"],
            }
            for item in items
        ]
    }


@app.post("/api/clickru-connections", status_code=201)
async def add_clickru_connection(payload: ClickRUConnectionIn, _: AuthUser):
    client = ClickRUClient(token=payload.token, user_id=payload.user_id or None)
    try:
        await client.user()
    except ClickRUError as exc:
        raise HTTPException(status_code=422, detail=f"Токен не прошёл проверку Click.ru: {exc}") from exc
    connection = db.create_clickru_connection(payload.label.strip(), payload.token.strip(), payload.user_id.strip())
    return {
        "id": connection["id"],
        "label": connection["label"],
        "user_id": connection["user_id"],
        "created_at": connection["created_at"],
    }


@app.delete("/api/clickru-connections/{connection_id}")
async def remove_clickru_connection(connection_id: int, _: AuthUser):
    if not db.delete_clickru_connection(connection_id):
        raise HTTPException(status_code=404, detail="Подключение Click.ru не найдено")
    return {"deleted": True, "connection_id": connection_id}


@app.get("/api/connection")
async def click_connection(_: AuthUser, connection_id: int | None = Query(default=None, ge=1)):
    client = _resolve_clickru_client(connection_id)
    if not client.configured:
        return {
            "configured": False,
            "connected": False,
            "message": "Укажите новый токен Click.ru в серверном .env.",
            "user": None,
            "integrations": [],
            "accounts": [],
        }
    try:
        user, integrations, accounts = await _load_click_data(client)
        return {
            "configured": True,
            "connected": True,
            "message": "Подключение к Click.ru работает.",
            "user": _safe_user(user),
            "integrations": [_safe_integration(item) for item in integrations],
            "accounts": [_safe_account(item) for item in accounts],
        }
    except ClickRUError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "configured": True,
                "connected": False,
                "message": str(exc),
                "user": None,
                "integrations": [],
                "accounts": [],
            },
        )


@app.post("/api/accounts/direct", status_code=201)
async def create_direct_account(payload: DirectAccountCreate, _: AuthUser):
    """Not called from this app's own UI (see DirectAccountCreate) — this is
    a machine endpoint for ParsYa's connect_direct_account.py, which drives
    a real Click.ru web-login Selenium flow to connect a pool Yandex ID as
    a YANDEX_DIRECT integration, then calls this to create the actual
    Direct account under it. Do not remove without checking that script.
    """
    client = ClickRUClient()
    if not client.configured:
        raise HTTPException(status_code=503, detail="Сначала укажите CLICKRU_API_TOKEN в серверном .env")
    try:
        integrations = await client.integrations()
        allowed_ids = {
            int(item["id"]) for item in integrations
            if item.get("id") is not None and str(item["id"]).isdigit()
        }
        if payload.integration_id not in allowed_ids:
            raise HTTPException(
                status_code=422,
                detail="Выбранная интеграция Яндекс Директа не найдена. Обновите список интеграций.",
            )
        result = await client.create_direct_account(payload.name.strip(), payload.integration_id)
        account_id = result.get("accountId") or result.get("account_id")
        return {
            "created": True,
            "account_id": account_id,
            "name": payload.name.strip(),
            "integration_id": payload.integration_id,
            "message": "Запрос на создание аккаунта отправлен в Click.ru.",
        }
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/payers")
async def list_payers(_: AuthUser, connection_id: int | None = Query(default=None, ge=1)):
    """Legal entities (юрлица) with a balance, available under the chosen
    Click.ru master account (wizard step 2 — picked after step 1's account
    connection). Needed before a real campaign launch — Russian ad-marking
    law (ОРД/ERID) ties every served ad to a registered payer.
    """
    client = _resolve_clickru_client(connection_id)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Сначала укажите CLICKRU_API_TOKEN в серверном .env")
    try:
        payers = await client.payers()
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": payers}


def _build_payer_payload(payload: PayerCreateIn) -> dict[str, Any]:
    """Переводит PayerCreateIn в точную форму, которую ждёт Click.ru
    (camelCase, разный набор полей по типу — см. openapi.json,
    Users.Payers.Business/Individual/Person, сверено 2026-11-02)."""
    body: dict[str, Any] = {"type": payload.type, "inn": payload.inn}
    if payload.phone:
        body["phone"] = payload.phone

    def require(value: Any, field_label: str) -> Any:
        if value is None:
            raise HTTPException(status_code=422, detail=f"Для этого типа плательщика обязательно поле «{field_label}».")
        return value

    if payload.type in ("BUSINESS", "INDIVIDUAL"):
        body["name"] = require(payload.name, "Название")
        body["taxForm"] = require(payload.tax_form, "Форма налогообложения")
        address = require(payload.business_address, "Юридический адрес")
        body["businessAddress"] = {"address": address.address, "postalCode": address.postal_code}
        actual = require(payload.actual_address, "Фактический адрес")
        body["actualAddress"] = {"address": actual.address, "postalCode": actual.postal_code}
        bank = require(payload.bank, "Банковские реквизиты")
        body["bank"] = {
            "name": bank.name, "rcbic": bank.rcbic,
            "account": bank.account, "correspondentAccount": bank.correspondent_account,
        }
        if payload.type == "BUSINESS":
            body["fullName"] = require(payload.full_name, "Полное название")
            body["kpp"] = require(payload.kpp, "КПП")
            body["ogrn"] = require(payload.ogrn, "ОГРН")
        else:
            body["ogrnip"] = require(payload.ogrnip, "ОГРНИП")
    else:  # PERSON
        body["lastName"] = require(payload.last_name, "Фамилия")
        body["firstName"] = require(payload.first_name, "Имя")
        body["middleName"] = require(payload.middle_name, "Отчество")

    return body


@app.post("/api/payers", status_code=201)
async def create_payer(
    payload: PayerCreateIn, _: AuthUser, connection_id: int | None = Query(default=None, ge=1)
):
    """Создаёт юрлицо/ИП/физлицо прямо в Click.ru (POST /users/payers) —
    визард, шаг 2, «+ Добавить юрлицо». Раньше (до 2026-11-02) это
    документация описывала как исключительно ручную настройку в кабинете
    Click.ru — на самом деле API это поддерживает, см. openapi.json.
    """
    client = _resolve_clickru_client(connection_id)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Сначала укажите CLICKRU_API_TOKEN в серверном .env")
    body = _build_payer_payload(payload)
    try:
        payer_id = await client.create_payer(body)
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"payer_id": payer_id}


@app.get("/api/geo-regions")
async def get_geo_regions(_: AuthUser):
    """Яндекс-справочник регионов (Dictionaries.getGeoRegions), отфильтрованный
    до России — визард (шаг 1) использует это вместо ручного ввода ID
    регионов. Не привязан к конкретному проекту/запуску — любой уже
    подключённый аккаунт Click.ru подходит, справочник не account-specific.
    Кэшируется в app_settings (статичный справочник, меняется редко —
    см. блог Яндекс.Директа про обновления гео-дерева): первый запрос после
    рестарта сервера идёт в Click.ru/Direct, остальные — из кэша.
    """
    cached = db.get_setting("geo_regions_russia")
    if cached:
        return {"items": cached}

    connections = db.list_clickru_connections()
    client = _resolve_clickru_client(connections[0]["id"]) if connections else ClickRUClient()
    if not client.configured:
        raise HTTPException(
            status_code=503,
            detail="Нужен хотя бы один настроенный аккаунт Click.ru для справочника регионов.",
        )
    try:
        accounts = await client.accounts()
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Не каждый serviceLogin в пуле — настоящий логин Яндекс.Директа: среди
    # подключённых через Click.ru аккаунтов встречаются интеграции других
    # сетей (например "zzclickadwords-..." — это Google Ads, а не Директ) и
    # неактивные логины, которые Dictionaries.get отвергает с ошибкой
    # "Логин не найден или неактивен", хотя Click.ru помечает сам аккаунт
    # ACTIVE. Поэтому пробуем по очереди (сначала ACTIVE), а не берём первый
    # попавшийся — иначе один неудачный логин навсегда ломает справочник.
    candidates = [a for a in accounts if a.get("serviceLogin")]
    candidates.sort(key=lambda a: a.get("status") != "ACTIVE")
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail="Нет ни одного Директ-аккаунта с serviceLogin — справочник регионов недоступен.",
        )
    result = None
    last_error: ClickRUError | None = None
    for account in candidates[:15]:
        try:
            result = await client.dictionaries(account["serviceLogin"], ["GeoRegions"])
            break
        except ClickRUError as exc:
            last_error = exc
            continue
    if result is None:
        raise HTTPException(status_code=502, detail=str(last_error))

    raw = result.get("GeoRegions") or []
    items = []
    for row in raw:
        parent_names = ((row.get("ParentGeoRegionNames") or {}).get("Items")) or []
        # Only Russia's own subtree — "Russia" itself is always the first
        # entry in ParentGeoRegionNames for anything under it (confirmed
        # against a live response, see CHECKS.md).
        if "Russia" not in parent_names and "Россия" not in parent_names:
            continue
        items.append({
            "id": int(row["GeoRegionId"]),
            "name": row.get("GeoRegionName", ""),
            "parents": parent_names,
        })

    items.sort(key=lambda x: x["name"])
    db.set_setting("geo_regions_russia", items)
    return {"items": items}


@app.get("/api/accounts/{account_id}/marking-schema")
async def get_marking_schema(
    account_id: int, _: AuthUser, connection_id: int | None = Query(default=None, ge=1)
):
    """Whether this Direct account has an ОРД/ERID marking scheme
    configured, and what it requires. MUST be checked (and confirmed
    configured) before activating any real campaign — creating a DRAFT
    doesn't need this, only actually serving ads does.
    """
    client = _resolve_clickru_client(connection_id)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Сначала укажите CLICKRU_API_TOKEN в серверном .env")
    try:
        schema = await client.marking_schema(account_id)
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return schema


async def _load_click_data(client: ClickRUClient):
    user = await client.user()
    integrations = await client.integrations()
    accounts = await client.accounts()
    return user, integrations, accounts


def _safe_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user.get(key) for key in ("id", "email", "login") if user.get(key) is not None}


def _safe_integration(item: dict[str, Any]) -> dict[str, Any]:
    allowed = ("id", "service", "name", "title", "login", "state", "status")
    return {key: item.get(key) for key in allowed if item.get(key) is not None}


def _safe_account(item: dict[str, Any]) -> dict[str, Any]:
    # serviceLogin/serviceId are needed to drive the Direct API proxy
    # (Client-Login header) — not a secret, just the account's own Direct
    # login, so safe to expose here same as the other identifying fields.
    allowed = ("id", "name", "service", "status", "state", "currency", "serviceLogin", "serviceId")
    return {key: item.get(key) for key in allowed if item.get(key) is not None}


async def _metrika_context(
    connection_id: int | None = None,
    owner_username: str | None = None,
    pool_login: str | None = None,
) -> YandexMetrikaClient:
    """Two independent paths to a Metrika client, used by two unrelated
    features:
    - `pool_login`: ParsYa's shared yandex_accounts pool (MySQL) — the ONLY
      path addAvto's own bulk-create flow uses (see
      ADS_MANAGER_OVERVIEW.md in ParsYa); reads the token live each call,
      so there's exactly one place it can go stale.
    - `connection_id`+`owner_username`: a per-user Yandex OAuth connection
      (`metrika_connections`), used ONLY by /lk members (see lk.py) to pick
      their own Metrika counter for Bitrix24 lead uploads — unrelated to
      campaign automation.
    """
    if pool_login:
        pool = ParsYaPoolClient()
        if not pool.configured:
            raise HTTPException(
                status_code=503,
                detail="PARSYA_POOL_API_TOKEN не задан в .env — пул ParsYa недоступен.",
            )
        try:
            account = await pool.get_account(pool_login)
        except ParsYaPoolError as exc:
            status_code = exc.status_code if exc.status_code and exc.status_code < 500 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return YandexMetrikaClient(account["metrika_access_token"])

    if connection_id is None or not owner_username:
        raise HTTPException(status_code=422, detail="Нужен connection_id или pool_login.")
    connection = db.get_metrika_connection(connection_id, owner_username)
    if connection is None:
        raise HTTPException(status_code=404, detail="Подключение Яндекс ID не найдено.")
    if connection.get("status") != "connected":
        raise HTTPException(
            status_code=409,
            detail="Подключение Яндекс ID требует повторной авторизации. Подключите аккаунт заново.",
        )

    try:
        access_token = decrypt_secret(connection["access_token_enc"])
        refresh_enc = connection.get("refresh_token_enc") or ""
        expires_at = connection.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except ValueError:
                expiry = datetime.min.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc) + timedelta(minutes=2):
                if not refresh_enc:
                    db.update_metrika_connection_status(connection_id, owner_username, "reconnect_required")
                    raise HTTPException(
                        status_code=409,
                        detail="Срок действия токена закончился. Подключите Яндекс ID заново.",
                    )
                oauth = YandexOAuthClient.from_env()
                try:
                    refreshed = await oauth.refresh(decrypt_secret(refresh_enc))
                except YandexOAuthError as exc:
                    db.update_metrika_connection_status(connection_id, owner_username, "reconnect_required")
                    raise HTTPException(
                        status_code=409,
                        detail="Не удалось обновить токен. Подключите Яндекс ID заново.",
                    ) from exc
                access_token = str(refreshed["access_token"])
                new_refresh = str(refreshed.get("refresh_token") or decrypt_secret(refresh_enc))
                new_expiry = _oauth_expiry(refreshed)
                db.update_metrika_connection_tokens(
                    connection_id,
                    owner_username,
                    encrypt_secret(access_token),
                    encrypt_secret(new_refresh) if new_refresh else "",
                    new_expiry,
                    str(refreshed.get("scope") or connection.get("scopes") or ""),
                )
        return YandexMetrikaClient(access_token)
    except TokenCryptoError as exc:
        raise HTTPException(
            status_code=503,
            detail="Не удалось прочитать зашифрованный токен. Проверьте METRIKA_TOKEN_ENCRYPTION_KEY.",
        ) from exc
    except YandexOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _oauth_expiry(token_response: dict[str, Any]) -> str | None:
    try:
        seconds = int(token_response.get("expires_in"))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


async def _lk_metrika_client(connection_id: int, owner: str) -> YandexMetrikaClient:
    # Пул ParsYa для ЛК недоступен: только собственные подключения пользователя.
    return await _metrika_context(connection_id, owner, None)


async def _lk_metrika_context(connection_id: int, owner: str, pool_login: str | None) -> YandexMetrikaClient:
    return await _lk_metrika_client(connection_id, owner)


app.include_router(lk.build_router(require_member, _lk_metrika_context))


@app.get("/api/metrika/connections")
async def get_metrika_connections(user: MemberUser):
    username = user.username
    try:
        encrypt_secret("oauth-config-check")
        encryption_ready = True
    except TokenCryptoError:
        encryption_ready = False
    oauth_configured = encryption_ready and all(
        os.getenv(key, "").strip()
        for key in ("METRIKA_OAUTH_CLIENT_ID", "METRIKA_OAUTH_CLIENT_SECRET", "METRIKA_OAUTH_REDIRECT_URI")
    )
    return {
        "items": db.list_metrika_connections(username),
        "oauth_configured": bool(oauth_configured),
    }


@app.delete("/api/metrika/connections/{connection_id}")
async def disconnect_metrika_connection(connection_id: int, user: MemberUser):
    username = user.username
    connection = db.get_metrika_connection(connection_id, username)
    if connection is None:
        raise HTTPException(status_code=404, detail="Подключение Яндекс ID не найдено.")
    try:
        access_token = decrypt_secret(connection["access_token_enc"])
        oauth = YandexOAuthClient.from_env()
        await oauth.revoke(access_token)
    except TokenCryptoError as exc:
        raise HTTPException(status_code=503, detail="Не удалось расшифровать токен для отключения.") from exc
    except YandexOAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not db.delete_metrika_connection(connection_id, username):
        raise HTTPException(status_code=500, detail="Не удалось удалить подключение.")
    return {"deleted": True, "connection_id": connection_id}


@app.get("/oauth/yandex/start")
async def yandex_oauth_start(
    user: MemberUser,
    label: str = Query(default="Яндекс ID", min_length=1, max_length=80),
    return_to: str = Query(default="/"),
):
    username = user.username
    # Участники ЛК не имеют доступа к «/», поэтому возвращаем их в /lk.
    return_to = "/lk" if (return_to == "/lk" or not user.is_admin) else "/"
    try:
        oauth = YandexOAuthClient.from_env()
        # Validate the persistent encryption key before redirecting the user to Yandex.
        encrypt_secret("oauth-key-check")
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="seconds")
        clean_label = " ".join(label.split()).strip() or "Яндекс ID"
        db.save_metrika_oauth_state(
            state_hash=state_hash,
            owner_username=username,
            label=clean_label,
            code_verifier_enc=encrypt_secret(code_verifier),
            expires_at=expires_at,
            return_to=return_to,
        )
        return RedirectResponse(
            oauth.authorization_url(state, code_verifier),
            status_code=status.HTTP_302_FOUND,
        )
    except (YandexOAuthError, TokenCryptoError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/oauth/yandex/callback")
async def yandex_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    base = "/"

    def finish(result: str) -> RedirectResponse:
        return RedirectResponse(url=f"{base}?metrika_oauth={result}", status_code=status.HTTP_303_SEE_OTHER)

    if not state:
        return finish("invalid_state")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    oauth_state = db.consume_metrika_oauth_state(state_hash)
    if oauth_state is None:
        return finish("invalid_state")
    base = oauth_state.get("return_to") if oauth_state.get("return_to") in ("/", "/lk") else "/"
    if error:
        return finish("denied" if error == "access_denied" else "failed")
    if not code:
        return finish("failed")

    try:
        code_verifier = decrypt_secret(oauth_state["code_verifier_enc"])
        oauth = YandexOAuthClient.from_env()
        token_data = await oauth.exchange_code(code, code_verifier)
        access_token = str(token_data["access_token"])
        # Verify the granted token against Metrica before storing it.
        metrika = YandexMetrikaClient(access_token)
        await metrika.list_counters()
        refresh_token = str(token_data.get("refresh_token") or "")
        scopes = str(token_data.get("scope") or "metrika:read metrika:write")
        connection = db.create_metrika_connection(
            owner_username=oauth_state["owner_username"],
            label=oauth_state["label"],
            access_token_enc=encrypt_secret(access_token),
            refresh_token_enc=encrypt_secret(refresh_token) if refresh_token else "",
            expires_at=_oauth_expiry(token_data),
            scopes=scopes,
        )
        return RedirectResponse(
            url=f"{base}?metrika_oauth=connected&connection_id={int(connection['id'])}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except MetrikaAPIError as exc:
        return finish("permissions" if exc.status_code in (401, 403) else "failed")
    except (YandexOAuthError, TokenCryptoError, HTTPException):
        return finish("failed")


def _normalize_site(value: str | None) -> str:
    if not value:
        return ""
    from urllib.parse import urlsplit

    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else "//" + raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _counter_site(counter: dict[str, Any]) -> str:
    site = counter.get("site")
    if not site and isinstance(counter.get("site2"), dict):
        site = counter["site2"].get("site") or counter["site2"].get("domain")
    return str(site or "")


def _is_lead_goal(goal: dict[str, Any]) -> bool:
    if str(goal.get("name", "")).strip().casefold() != "lead":
        return False
    if str(goal.get("type", "")).strip().casefold() != "action":
        return False
    conditions = goal.get("conditions") or []
    return any(
        isinstance(condition, dict)
        and str(condition.get("type", "")).strip().casefold() == "exact"
        and str(condition.get("url", "")).strip() == "lead"
        for condition in conditions
    )


@app.get("/api/parsya-pool/accounts")
async def list_parsya_pool_accounts(_: AuthUser):
    pool = ParsYaPoolClient()
    if not pool.configured:
        return {"configured": False, "items": []}
    try:
        items = await pool.list_accounts()
    except ParsYaPoolError as exc:
        status_code = exc.status_code if exc.status_code and exc.status_code < 500 else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"configured": True, "items": items}


@app.get("/api/projects")
async def get_projects(_: AuthUser):
    items = db.list_projects()
    for item in items:
        item["has_launched"] = db.has_bulk_job(item["id"])
    return {"items": items}


@app.post("/api/projects", status_code=201)
async def create_project(project: ProjectIn, _: AuthUser):
    return db.create_project(project.model_dump(mode="json"))


@app.put("/api/projects/{project_id}")
async def update_project(project_id: int, project: ProjectIn, _: AuthUser):
    if db.has_bulk_job(project_id):
        raise HTTPException(
            status_code=409,
            detail="Проект уже запущен — редактирование настроек недоступно (чтобы не разойтись с уже созданными кампаниями).",
        )
    item = db.update_project(project_id, project.model_dump(mode="json"))
    if item is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return item


@app.post("/api/projects/{project_id}/duplicate", status_code=201)
async def duplicate_project(project_id: int, _: AuthUser):
    """Копия проекта со всеми настройками (ключевые фразы, регионы, пулы
    креативов) — для случая "запустить то же самое ещё раз, может с
    правками". Копия — всегда новый, никогда не запущенный проект, так
    что её можно редактировать независимо от того, запущен ли оригинал.
    """
    source = db.get_project(project_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    data = {field: source.get(field) for field in db.PROJECT_FIELDS}
    data["name"] = (data.get("name") or "").strip() + " (копия)"
    return db.create_project(data)


@app.delete("/api/projects/{project_id}")
async def remove_project(project_id: int, _: AuthUser):
    if not db.delete_project(project_id):
        raise HTTPException(status_code=404, detail="Проект не найден")
    return {"deleted": True}


@app.post("/api/projects/{project_id}/plan", response_model=CampaignPlan)
async def preview_plan(project_id: int, _: AuthUser):
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    if project["campaign_mode"] == "separate":
        items = [
            PlanItem(name=f'{project["name"]} — Поиск', placement="SEARCH",
                     note="Отдельная кампания с поисковыми объявлениями и ключевыми фразами."),
            PlanItem(name=f'{project["name"]} — РСЯ', placement="NETWORK",
                     note="Отдельная кампания с графическими/текстово-графическими креативами."),
        ]
    else:
        items = [
            PlanItem(name=f'{project["name"]} — ЕПК', placement="SEARCH_AND_NETWORK",
                     note="Одна ЕПК; места показа Поиск и Рекламная сеть Яндекса должны быть включены в её настройках."),
        ]
    return CampaignPlan(
        project_id=project_id,
        campaign_mode=project["campaign_mode"],
        strategy="AVERAGE_CRR",  # bulk-create always uses this, по продуктовому решению — не настраивается
        items=items,
    )


@app.get("/api/projects/{project_id}/campaigns")
async def list_project_campaigns(project_id: int, _: AuthUser):
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return {"items": db.list_bulk_campaigns(project_id)}


@app.post("/api/accounts/{account_id}/campaigns/{campaign_id}/activate")
async def activate_campaign(
    account_id: int,
    campaign_id: int,
    _: AuthUser,
    connection_id: int | None = Query(default=None, ge=1),
):
    """The ONLY endpoint in this app that makes a campaign eligible to
    serve and spend real budget (see SECURITY.md — campaign start/stop was
    intentionally withheld from the MVP until explicit operator approval,
    given 2026-10-31). Every campaign this app creates starts DRAFT/OFF;
    nothing else in the API activates one. `connection_id` should match
    whichever Click.ru account the campaign was created under
    (bulk_campaigns.connection_id, see GET /api/projects/{id}/campaigns).

    Requires a configured ОРД/ERID marking scheme for the account FIRST —
    checked here (not just left to Yandex's own validation) so the
    operator gets a clear, actionable error instead of a cryptic Direct
    API rejection.
    """
    client = _resolve_clickru_client(connection_id)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Сначала укажите CLICKRU_API_TOKEN в серверном .env")

    try:
        schema = await client.marking_schema(account_id)
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось проверить схему маркировки: {exc}") from exc
    if not schema.get("configured", schema.get("isConfigured", False)):
        raise HTTPException(
            status_code=409,
            detail=(
                "У аккаунта не настроена схема маркировки рекламы (ОРД/ERID). "
                "Настройте юрлицо и маркировку в кабинете Click.ru перед запуском."
            ),
        )

    try:
        client_login = await _resolve_client_login(client, account_id)
        await client.resume_campaign(client_login, campaign_id)
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"activated": True, "account_id": account_id, "campaign_id": campaign_id}


@app.post("/api/accounts/{account_id}/campaigns/{campaign_id}/suspend")
async def suspend_campaign(
    account_id: int,
    campaign_id: int,
    _: AuthUser,
    connection_id: int | None = Query(default=None, ge=1),
):
    """Kill-switch counterpart to /activate (added 2026-10-31) — stops a
    campaign from serving without deleting it. No marking-schema check
    needed, unlike activation: stopping spend is always safe to allow.
    """
    client = _resolve_clickru_client(connection_id)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Сначала укажите CLICKRU_API_TOKEN в серверном .env")
    try:
        client_login = await _resolve_client_login(client, account_id)
        await client.suspend_campaign(client_login, campaign_id)
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"suspended": True, "account_id": account_id, "campaign_id": campaign_id}


async def _resolve_client_login(client: ClickRUClient, account_id: int) -> str:
    """A just-created Direct account can take a moment to show up in
    Click.ru's own /accounts listing — retry briefly rather than failing
    the whole campaign on a timing race."""
    for attempt in range(5):
        accounts = await client.accounts()
        account = next((a for a in accounts if int(a.get("id") or 0) == account_id), None)
        if account and account.get("serviceLogin"):
            return account["serviceLogin"]
        if attempt < 4:
            await asyncio.sleep(3)
    raise ClickRUError(f"У аккаунта {account_id} нет serviceLogin после создания")


async def _download_image_base64(url: str) -> str:
    # "data:image/...;base64,..." — файл, загруженный пользователем прямо
    # с компьютера (визард, шаг 5) — уже сырые base64-байты, скачивать
    # нечего, просто отрезаем заголовок data-URI.
    if url.startswith("data:image/"):
        _, _, data = url.partition(",")
        return data
    async with httpx.AsyncClient(timeout=20.0) as http_client:
        response = await http_client.get(url)
        response.raise_for_status()
        return base64.b64encode(response.content).decode("ascii")


async def _ensure_lead_goal_for_account(pool_login: str, project_host: str, project_name: str) -> tuple[int, int]:
    """Every pool Yandex ID gets its OWN Metrika counter + 'lead' goal —
    by product decision, never shared across accounts (sharing one counter
    across nominally-unrelated Direct accounts is exactly the kind of
    affiliation signal this whole one-account-per-campaign architecture
    exists to avoid). Unlike /metrika/setup, always creates what's missing
    and never persists onto the project (there's no single "the" counter
    for a bulk-generated project — each campaign's account has its own).
    """
    metrika = await _metrika_context(pool_login=pool_login)
    counters = await metrika.list_counters()
    chosen = next(
        (c for c in counters if _normalize_site(_counter_site(c)) == _normalize_site(project_host)),
        None,
    )
    if chosen is None:
        chosen = await metrika.create_counter(project_name, project_host)
    counter_id = int(chosen["id"])

    goals = await metrika.list_goals(counter_id)
    named_lead_goals = [g for g in goals if str(g.get("name", "")).strip().casefold() == "lead"]
    matching = next((g for g in named_lead_goals if _is_lead_goal(g)), None)
    if matching is not None:
        goal_id = int(matching["id"])
    elif named_lead_goals:
        raise MetrikaAPIError(
            f"У счётчика {counter_id} ({pool_login}) уже есть цель 'lead' другого типа/условия"
        )
    else:
        goal = await metrika.create_goal(counter_id)
        goal_id = int(goal["id"])

    return counter_id, goal_id


@app.post(
    "/api/projects/{project_id}/campaigns/bulk-create",
    response_model=BulkJobStartOut,
    status_code=202,
)
async def bulk_create_campaigns(project_id: int, payload: BulkCampaignCreateRequest, _: AuthUser):
    """Kicks off `count` campaign provisions as a BACKGROUND job and returns
    immediately with a job_id — see GET .../bulk-jobs/{job_id} to poll
    progress. Changed from a synchronous request (2026-10-31): provisioning
    each account drives a real browser plus the Click.ru/Direct API and can
    take minutes per account, which risked a client/proxy timeout losing
    the response even though the server kept working underneath.

    One BRAND NEW Direct account per campaign, from ParsYa's pool (see
    ParsYaPoolClient.provision_direct_account -> parsya-control ->
    connect_direct_account.py's Selenium pipeline), all under the chosen
    Click.ru connection (`connection_id` — wizard step 1). Strategy is
    always AVERAGE_CRR (ДРР) against the project's Metrica 'lead' goal —
    not configurable per call, by product decision.

    Each campaign's creative (headline/ad_text/image/sitelinks/callouts)
    comes from `payload.creative_mode` (wizard step 4):
    - "shuffle" (default): one random pick per account from the project's
      pools.
    - "duplicate_single": the SAME first headline/ad_text/image + the full
      sitelinks/callouts pools, identical on every account.
    - "manual_per_account": `payload.creative_sets[i]` applied 1:1, one
      distinct hand-picked set per account (schema already enforces
      len(creative_sets) == count).

    Rejects a second launch for the same project while one is still
    running (409) — two concurrent batches would both draw from the same
    ParsYa pool and double-book accounts.
    """
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    headlines = project.get("headlines") or []
    ad_texts = project.get("ad_texts") or []

    if payload.creative_mode in ("shuffle", "duplicate_single") and (not headlines or not ad_texts):
        raise HTTPException(
            status_code=422,
            detail="Нужен хотя бы один заголовок (headlines) и один текст объявления (ad_texts)",
        )
    phrases = [line.strip() for line in project.get("keywords", "").splitlines() if line.strip()]
    if not phrases:
        raise HTTPException(status_code=422, detail="Нужна хотя бы одна ключевая фраза (keywords)")
    if not project.get("region_ids"):
        raise HTTPException(status_code=422, detail="Нужен хотя бы один region_id (геотаргетинг)")

    from urllib.parse import urlsplit
    project_host = (urlsplit(project["domain"]).hostname or "").strip().lower().rstrip(".")
    if not project_host:
        raise HTTPException(status_code=422, detail="Не удалось определить домен проекта")

    client = _resolve_clickru_client(payload.connection_id)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Выбранное подключение Click.ru не настроено")

    pool_client = ParsYaPoolClient()
    if not pool_client.configured:
        raise HTTPException(status_code=503, detail="PARSYA_POOL_API_TOKEN не задан в .env")

    if db.get_running_bulk_job(project_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Массовый запуск для этого проекта уже выполняется — подождите его завершения.",
        )

    job_id = db.create_bulk_job(project_id, payload.count)
    task = asyncio.create_task(_run_bulk_job(job_id, project_id, project, project_host, phrases, client, pool_client, payload))
    _BACKGROUND_JOB_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_JOB_TASKS.discard)
    return BulkJobStartOut(job_id=job_id, project_id=project_id, status="running")


async def _run_bulk_job(
    job_id: int,
    project_id: int,
    project: dict[str, Any],
    project_host: str,
    phrases: list[str],
    client: ClickRUClient,
    pool_client: ParsYaPoolClient,
    payload: BulkCampaignCreateRequest,
) -> None:
    headlines = project.get("headlines") or []
    ad_texts = project.get("ad_texts") or []
    sitelinks_pool = project.get("sitelinks_pool") or []
    callouts_pool = project.get("callouts_pool") or []
    image_urls = project.get("image_urls") or []
    negative_keywords = [line.strip() for line in project.get("negative_keywords", "").splitlines() if line.strip()]

    try:
        for index in range(payload.count):
            pool_login: str | None = None
            account_id: int | None = None
            counter_id: int | None = None
            goal_id: int | None = None
            try:
                provisioned = await pool_client.provision_direct_account(account_name_prefix=project["name"][:10])
                pool_login = provisioned["pool_login"]
                account_id = int(provisioned["account_id"])

                client_login = await _resolve_client_login(client, account_id)
                counter_id, goal_id = await _ensure_lead_goal_for_account(pool_login, project_host, project["name"])
                # Seen live: a just-created goal isn't immediately visible to
                # the Direct API's own GoalId validation (same "just created,
                # not indexed yet" lag as the Click.ru integration/account
                # checks elsewhere in this flow) — AVERAGE_CRR creation fails
                # with "Specified goal not found" without this.
                await asyncio.sleep(8)

                # Sitelinks/callouts are picked ONCE per account, shared by
                # every ad in its group (re-sampling per ad would multiply
                # API calls for no real benefit — only headline/text/image
                # are worth rotating within a group, see ads_per_group below).
                if payload.creative_mode == "manual_per_account":
                    creative = payload.creative_sets[index]
                    this_sitelinks = [sl.model_dump() for sl in creative.sitelinks]
                    this_callouts = list(creative.callouts)
                elif payload.creative_mode == "duplicate_single":
                    this_sitelinks = sitelinks_pool[:4]
                    this_callouts = callouts_pool[:5]
                else:  # shuffle
                    this_sitelinks = random.sample(sitelinks_pool, k=min(4, len(sitelinks_pool))) if sitelinks_pool else []
                    this_callouts = random.sample(callouts_pool, k=min(5, len(callouts_pool))) if callouts_pool else []

                sitelink_set_id = None
                if this_sitelinks:
                    sitelink_set_id = await client.create_sitelinks_set(
                        client_login,
                        [
                            {"Title": sl["title"], "Href": sl["href"], **({"Description": sl["description"]} if sl.get("description") else {})}
                            for sl in this_sitelinks
                        ],
                    )

                ad_extension_ids = [await client.create_callout(client_login, text) for text in this_callouts]

                if project["campaign_mode"] == "single_epk":
                    campaign_id = await client.create_draft_unified_campaign(
                        client_login,
                        project["name"],
                        date.today().isoformat(),
                        target_drr=project["target_drr"],
                        goal_id=goal_id,
                    )
                else:
                    campaign_id = await client.create_draft_text_campaign(
                        client_login,
                        project["name"],
                        date.today().isoformat(),
                        target_drr=project["target_drr"],
                        goal_id=goal_id,
                    )

                note: str | None = None
                mobile_pct = project.get("mobile_bid_adjustment_percent")
                if mobile_pct is not None:
                    try:
                        await client.add_mobile_bid_adjustment(client_login, campaign_id, int(mobile_pct))
                    except ClickRUError as exc:
                        # Best-effort: the campaign itself is already valid
                        # without this, so a rejected adjustment shouldn't
                        # sink the whole account.
                        note = f"Корректировка для мобильных не применена: {exc}"

                ad_group_id = await client.create_adgroup(
                    client_login, campaign_id, project["name"], project["region_ids"],
                    negative_keywords=negative_keywords or None,
                )
                await client.add_keywords(client_login, ad_group_id, phrases)

                # ads_per_group (wizard step 4, "структура объявлений"): >1
                # only with creative_mode=shuffle (schema-enforced) — each
                # ad in the group gets its OWN random headline/text/image
                # pick, letting Direct's own delivery engine find the best
                # performer for this account instead of one fixed ad.
                first_headline: str | None = None
                first_ad_text: str | None = None
                first_image_used = False
                ads_created = 0
                for ad_index in range(payload.ads_per_group):
                    if payload.creative_mode == "manual_per_account":
                        creative = payload.creative_sets[index]
                        headline = creative.headline
                        ad_text = creative.ad_text
                        this_image_url = creative.image_url
                    elif payload.creative_mode == "duplicate_single":
                        headline = headlines[0]
                        ad_text = ad_texts[0]
                        this_image_url = image_urls[0] if image_urls else None
                    else:  # shuffle
                        headline = random.choice(headlines)
                        ad_text = random.choice(ad_texts)
                        this_image_url = random.choice(image_urls) if image_urls else None

                    ad_image_hash = None
                    if this_image_url:
                        image_b64 = await _download_image_base64(this_image_url)
                        ad_image_hash = await client.upload_ad_image(client_login, project["name"][:40], image_b64)

                    await client.create_text_ad(
                        client_login,
                        ad_group_id,
                        headline,
                        ad_text,
                        project["domain"],
                        ad_image_hash=ad_image_hash,
                        sitelink_set_id=sitelink_set_id,
                        ad_extension_ids=ad_extension_ids or None,
                    )
                    ads_created += 1
                    if ad_index == 0:
                        first_headline, first_ad_text, first_image_used = headline, ad_text, ad_image_hash is not None

                db.record_bulk_campaign(
                    project_id, pool_login, account_id, campaign_id,
                    connection_id=payload.connection_id, payer_id=payload.payer_id,
                )
                db.append_bulk_job_item(job_id, BulkCampaignItemResult(
                    pool_login=pool_login,
                    account_id=account_id,
                    counter_id=counter_id,
                    goal_id=goal_id,
                    campaign_id=campaign_id,
                    headline=first_headline,
                    ad_text=first_ad_text,
                    image_used=first_image_used,
                    sitelinks_used=len(this_sitelinks),
                    callouts_used=len(ad_extension_ids),
                    ads_created=ads_created,
                    note=note,
                    status="created",
                ).model_dump())
            except (ParsYaPoolError, ClickRUError, MetrikaAPIError, httpx.HTTPError) as exc:
                db.append_bulk_job_item(job_id, BulkCampaignItemResult(
                    pool_login=pool_login,
                    account_id=account_id,
                    counter_id=counter_id,
                    goal_id=goal_id,
                    status="failed",
                    error=str(exc),
                ).model_dump())
        db.finish_bulk_job(job_id, "done")
    except Exception as exc:  # pragma: no cover - safety net so a job never sticks at "running"
        db.finish_bulk_job(job_id, "failed", error=str(exc))


@app.get("/api/projects/{project_id}/campaigns/bulk-jobs/{job_id}", response_model=BulkJobOut)
async def get_bulk_job(project_id: int, job_id: int, _: AuthUser):
    job = db.get_bulk_job(job_id)
    if job is None or job["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return BulkJobOut(
        job_id=job["id"],
        project_id=job["project_id"],
        status=job["status"],
        requested=job["requested"],
        created=job["created"],
        items=job["items"],
        error=job["error"],
    )


@app.get("/api/report")
async def get_report(
    _: AuthUser,
    date_from: date = Query(alias="date_from"),
    date_to: date = Query(alias="date_to"),
    account_ids: str | None = Query(default=None),
):
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="Дата окончания раньше даты начала")
    if (date_to - date_from).days > 366:
        raise HTTPException(status_code=422, detail="Максимальный диапазон отчёта — 366 дней")
    ids: list[int] | None = None
    if account_ids and account_ids.strip():
        try:
            ids = [int(part.strip()) for part in account_ids.split(",") if part.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="ID аккаунтов укажите через запятую") from exc
        if any(value <= 0 for value in ids):
            raise HTTPException(status_code=422, detail="ID аккаунтов должны быть положительными")
    client = ClickRUClient()
    if not client.configured:
        raise HTTPException(status_code=503, detail="Сначала укажите CLICKRU_API_TOKEN в серверном .env")
    try:
        rows = await client.report(date_from, date_to, ids)
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    clean_rows: list[dict[str, Any]] = []
    totals = {"impressions": 0, "clicks": 0, "spend": 0.0}
    for row in rows:
        account_id = _to_int(row, "accountId", "account_id")
        campaign_id = _to_int(row, "campaignId", "campaign_id")
        impressions = _to_int(row, "impressions")
        clicks = _to_int(row, "clicks")
        spend = _to_float(row, "loss")
        clean_rows.append({
            "account_id": account_id,
            "campaign_id": campaign_id,
            "date": row.get("date", ""),
            "impressions": impressions,
            "clicks": clicks,
            "spend": spend,
            "leads": None,
            "lead_status": "Нужно подключить отчёт конверсий Директа по ID счётчика/цели",
        })
        totals["impressions"] += impressions
        totals["clicks"] += clicks
        totals["spend"] += spend
    totals["cpc"] = round(totals["spend"] / totals["clicks"], 2) if totals["clicks"] else None
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "items": clean_rows,
        "totals": totals,
        "conversions_note": (
            "Click.ru /stat предоставляет показы, клики и расход. Конверсии lead будут добавлены "
            "отдельным запросом к Reports API Директа через Click.ru proxy, когда будут известны "
            "Client-Login и ID счётчика/цели для каждого аккаунта."
        ),
    }


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in row and row[key] is not None:
            return str(row[key]).strip()
    return ""


def _to_int(row: dict[str, Any], *keys: str) -> int:
    value = _first(row, *keys).replace(" ", "")
    try:
        return int(float(value.replace(",", "."))) if value else 0
    except ValueError:
        return 0


def _to_float(row: dict[str, Any], *keys: str) -> float:
    value = _first(row, *keys).replace(" ", "").replace(",", ".")
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0
