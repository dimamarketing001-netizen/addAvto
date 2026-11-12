from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
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

from . import creative_images, db, lk
from .click_client import ClickRUClient, ClickRUError
from .metrika_client import MetrikaAPIError, YandexMetrikaClient
from .ozon_ad_creator import GeminiError, OzonBrowser, OzonParseError, build_ozon_sitelinks, process_ozon_url
from .parsya_pool_client import ParsYaPoolClient, ParsYaPoolError
from .schemas import (
    BUTTON_ACTIONS,
    BulkCampaignCreateRequest,
    BulkCampaignItemResult,
    BulkJobOut,
    BulkJobStartOut,
    CampaignPlan,
    ClickRUConnectionIn,
    ClickRUWebCredentialsIn,
    OzonGenerateIn,
    OzonGenerateOut,
    PayerCreateIn,
    DirectAccountCreate,
    PlanItem,
    ProjectIn,
)
from .token_crypto import TokenCryptoError, decrypt_secret, encrypt_secret
from .yandex_oauth import YandexOAuthClient, YandexOAuthError

# Один браузер Playwright на всё приложение (лениво стартует при первом
# запросе Ozon-режима, см. ozon_ad_creator.OzonBrowser.start) — запускать
# новый процесс браузера на каждый запрос было бы намного медленнее и
# быстрее исчерпало бы память. Закрывается в lifespan при остановке.
_ozon_browser = OzonBrowser()


security = HTTPBasic()
templates = Jinja2Templates(directory="app/templates")
# asyncio.create_task() doesn't keep its own strong reference — without this
# the task object could be garbage-collected mid-run on some event loop
# implementations. Standard "fire and forget" pattern per the asyncio docs.
_BACKGROUND_JOB_TASKS: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    interrupted = db.fail_interrupted_bulk_jobs()
    if interrupted:
        print(f"[startup] прерванные запуски помечены failed: {interrupted}", flush=True)
        if os.getenv("BULK_AUTO_RESUME", "1") != "0":
            task = asyncio.create_task(_auto_resume_interrupted(interrupted))
            _BACKGROUND_JOB_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_JOB_TASKS.discard)
    lk.init_lk_db()
    worker = None
    if os.getenv("LK_WORKER", "1") != "0":
        worker = asyncio.create_task(lk.worker_loop(_lk_metrika_client))
    yield
    if worker:
        worker.cancel()
    await _ozon_browser.close()


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


# --- Веб-кабинет Click.ru на уровне подключения (2026-11-10) ---------------
# API Click.ru не умеет подключать Яндекс ID как Директ-интеграцию — это
# делается Selenium'ом в веб-кабинете (ParsYa, WSL). Раньше логин/пароль
# кабинета брались из .env ParsYa — одни на всех, независимо от выбранного
# в визарде подключения, так что со вторым кабинетом аккаунты создавались
# бы не там, где потом создаются кампании. Теперь каждое подключение хранит
# свой логин, пароль и cookies сессии (Fernet, token_crypto.py), а WSL
# получает их в запросе и возвращает обновлённые cookies.

# Маркер в тексте ошибки скрипта ParsYa, когда не удался именно вход в
# кабинет (неверный пароль, капча) — отличает это от прочих сбоев.
CLICKRU_LOGIN_FAILED_MARKER = "[clickru-login-failed]"

CLICKRU_WEB_STATUS_TEXT = {
    "none": "Вход в кабинет не настроен",
    "ok": "Вход в кабинет выполнен",
    "relogin_required": "Нужен повторный вход",
    "failed": "Вход в кабинет не удался",
}


def _public_clickru_connection(item: dict[str, Any]) -> dict[str, Any]:
    """Только безопасные поля: пароль и cookies наружу не отдаются никогда."""
    return {
        "id": item["id"],
        "label": item["label"],
        "user_id": item["user_id"],
        "token_preview": "…" + item["token"][-4:] if len(item["token"]) >= 4 else "…",
        "created_at": item["created_at"],
        "web_username": item.get("web_username") or "",
        "has_web_password": bool(item.get("web_password_enc")),
        "web_status": item.get("web_status") or "none",
        "web_status_message": item.get("web_status_message") or "",
        "web_checked_at": item.get("web_checked_at"),
    }


def _clickru_uses_env_web_login(connection: dict[str, Any]) -> bool:
    """Подключение без своих логина/пароля может работать по-старому (через
    CLICKRU_WEB_* из .env ParsYa) ТОЛЬКО если это тот же кабинет, что и
    CLICKRU_API_TOKEN из .env — иначе аккаунты создались бы в чужом кабинете."""
    env_token = os.getenv("CLICKRU_API_TOKEN", "").strip()
    return bool(env_token) and connection["token"].strip() == env_token


def _clickru_web_secrets(connection: dict[str, Any]) -> dict[str, Any] | None:
    """Расшифрованные логин/пароль/cookies подключения или None, если их нет."""
    if not (connection.get("web_username") and connection.get("web_password_enc")):
        return None
    try:
        password = decrypt_secret(connection["web_password_enc"])
        cookies_enc = connection.get("web_cookies_enc") or ""
        cookies = json.loads(decrypt_secret(cookies_enc)) if cookies_enc else []
    except (TokenCryptoError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось расшифровать данные входа Click.ru «{connection['label']}»: {exc}",
        ) from exc
    return {
        "connection_id": connection["id"],
        "username": connection["web_username"],
        "password": password,
        "cookies": cookies if isinstance(cookies, list) else [],
    }


def _clickru_provision_payload(connection: dict[str, Any]) -> dict[str, Any] | None:
    """Что передать в провижининг ParsYa для этого подключения. None —
    легаси-режим (.env ParsYa), допустим только для «.env-кабинета».
    Ошибка 422 — подключение нельзя использовать для запуска."""
    secrets_ = _clickru_web_secrets(connection)
    if secrets_ is None:
        if _clickru_uses_env_web_login(connection):
            return None
        raise HTTPException(
            status_code=422,
            detail=(
                f"У подключения Click.ru «{connection['label']}» не указан логин и пароль "
                "веб-кабинета — добавьте их в разделе «Клик.ру»."
            ),
        )
    if connection.get("web_status") == "failed":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Вход в кабинет Click.ru «{connection['label']}» не удался: "
                f"{connection.get('web_status_message') or 'проверьте логин и пароль'}. "
                "Обновите логин и пароль в разделе «Клик.ру»."
            ),
        )
    return secrets_


def _save_clickru_cookies(connection_id: int, cookies: Any, message: str) -> None:
    if not isinstance(cookies, list) or not cookies:
        return
    db.set_clickru_web_session(connection_id, "ok", message, cookies_enc=encrypt_secret(json.dumps(cookies)))


async def _clickru_web_login(connection_id: int) -> dict[str, Any]:
    """Вход/проверка веб-сессии через Chrome в WSL и запись результата."""
    connection = db.get_clickru_connection(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Подключение Click.ru не найдено")
    secrets_ = _clickru_web_secrets(connection)
    if secrets_ is None:
        raise HTTPException(status_code=422, detail="Сначала укажите логин и пароль веб-кабинета Click.ru.")
    try:
        result = await ParsYaPoolClient().clickru_web_login(
            secrets_["username"], secrets_["password"], secrets_["cookies"],
        )
    except ParsYaPoolError as exc:
        reason = str(exc).replace(CLICKRU_LOGIN_FAILED_MARKER, "").strip()
        if exc.status_code == 409:
            # Вход действительно не удался — старые cookies больше не верим.
            db.set_clickru_web_session(connection_id, "failed", reason, cookies_enc="")
        else:
            # WSL/туннель недоступен и т.п. — про саму сессию ничего не
            # узнали, статус и cookies не трогаем.
            db.set_clickru_web_session(
                connection_id, connection.get("web_status") or "none", f"Проверка не выполнена: {reason}",
            )
    else:
        cookies = result.get("cookies")
        if isinstance(cookies, list) and cookies:
            _save_clickru_cookies(
                connection_id, cookies,
                "Вход выполнен по паролю" if result.get("relogged") else "Сессия активна",
            )
        else:
            db.set_clickru_web_session(connection_id, "failed", "Click.ru не вернул cookies сессии", cookies_enc="")
    updated = db.get_clickru_connection(connection_id)
    assert updated is not None
    return _public_clickru_connection(updated)


@app.get("/api/clickru-connections")
async def list_clickru_connections(_: AuthUser):
    return {"items": [_public_clickru_connection(item) for item in db.list_clickru_connections()]}


@app.post("/api/clickru-connections", status_code=201)
async def add_clickru_connection(payload: ClickRUConnectionIn, _: AuthUser):
    web_username = payload.web_username.strip()
    web_password = payload.web_password
    if bool(web_username) != bool(web_password):
        raise HTTPException(status_code=422, detail="Укажите и логин, и пароль кабинета Click.ru (или оставьте оба пустыми).")
    client = ClickRUClient(token=payload.token, user_id=payload.user_id or None)
    try:
        await client.user()
    except ClickRUError as exc:
        raise HTTPException(status_code=422, detail=f"Токен не прошёл проверку Click.ru: {exc}") from exc
    if web_username:
        try:
            password_enc = encrypt_secret(web_password)
        except TokenCryptoError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    connection = db.create_clickru_connection(payload.label.strip(), payload.token.strip(), payload.user_id.strip())
    if web_username:
        db.set_clickru_web_credentials(connection["id"], web_username, password_enc)
        return await _clickru_web_login(connection["id"])
    return _public_clickru_connection(connection)


@app.put("/api/clickru-connections/{connection_id}/web-credentials")
async def set_clickru_web_credentials(connection_id: int, payload: ClickRUWebCredentialsIn, _: AuthUser):
    if db.get_clickru_connection(connection_id) is None:
        raise HTTPException(status_code=404, detail="Подключение Click.ru не найдено")
    try:
        password_enc = encrypt_secret(payload.web_password)
    except TokenCryptoError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    db.set_clickru_web_credentials(connection_id, payload.web_username.strip(), password_enc)
    return await _clickru_web_login(connection_id)


@app.post("/api/clickru-connections/{connection_id}/web-check")
async def check_clickru_web_session(connection_id: int, _: AuthUser):
    return await _clickru_web_login(connection_id)


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
async def create_direct_account(
    payload: DirectAccountCreate,
    _: AuthUser,
    connection_id: int | None = Query(default=None, ge=1),
):
    """Not called from this app's own UI (see DirectAccountCreate) — this is
    a machine endpoint for ParsYa's connect_direct_account.py, which drives
    a real Click.ru web-login Selenium flow to connect a pool Yandex ID as
    a YANDEX_DIRECT integration, then calls this to create the actual
    Direct account under it. Do not remove without checking that script.
    `connection_id` (с 2026-11-10) — тот же кабинет Click.ru, в котором
    скрипт подключил интеграцию; без него — токен из .env (легаси).
    """
    client = _resolve_clickru_client(connection_id)
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
    return {"items": [_with_payer_name(p) for p in payers]}


def _with_payer_name(payer: Any) -> Any:
    """У физлиц и ИП Click.ru не отдаёт `name` — только lastName/firstName/
    middleName, и визард показывал голый номер (2026-11-10, «#1480787» вместо
    «Муфатыков Роман Маратович»). Собираем ФИО в `name`, если его нет."""
    if not isinstance(payer, dict) or str(payer.get("name") or "").strip():
        return payer
    fio = " ".join(
        str(payer.get(k) or "").strip() for k in ("lastName", "firstName", "middleName") if str(payer.get(k) or "").strip()
    )
    name = fio or str(payer.get("fullName") or payer.get("description") or "").strip()
    return {**payer, "name": name} if name else payer


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


GEO_RUSSIA_ID = 225
# v4 (2026-11-10): русские названия из app/geo_regions_ru.json — старый
# кэш с английскими (через Click.ru) должен перестать использоваться.
GEO_CACHE_KEY = "geo_regions_ru_tree_v4"
# Справочник на русском, выгруженный напрямую из Direct API (через Click.ru
# названия только английские — прокси режет Accept-Language). Обновление:
# YANDEX_DIRECT_TOKEN=... python tools/refresh_geo_regions.py
GEO_BUNDLED_FILE = os.path.join(os.path.dirname(__file__), "geo_regions_ru.json")


def _bundled_geo_raw() -> list[dict[str, Any]]:
    """Строки в формате Dictionaries.get GeoRegions из app/geo_regions_ru.json.
    GEO_REGIONS_BUNDLED=0 — не использовать файл (только Click.ru)."""
    if os.getenv("GEO_REGIONS_BUNDLED", "1") == "0":
        return []
    try:
        with open(GEO_BUNDLED_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [
        {"GeoRegionId": r[0], "ParentId": r[1], "GeoRegionType": r[2], "GeoRegionName": r[3]}
        for r in data.get("regions") or [] if isinstance(r, list) and len(r) == 4
    ]
# Живой ответ Директа (проверено 2026-11-10): субъект РФ — "Federation subject".
# "Administrative area" — вариант из документации, оставлен на всякий случай.
GEO_AREA_TYPES = {"Federation subject", "Administrative area"}


def _geo_sort_key(name: str) -> str:
    return (name or "").lower().replace("ё", "е")


def build_russia_geo(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Дерево «субъект РФ → города» из Dictionaries.get GeoRegions.

    Реальный формат строки Директа API v5: GeoRegionId, GeoRegionName,
    GeoRegionType (живьём: "Country", "Federal district", "Federation
    subject", "City", "Federal subject district", "Village", "Rural
    settlement", ...) и ParentId. Никаких списков имён родителей там нет —
    прежняя версия фильтровала по несуществующему ParentGeoRegionNames и
    на проде всегда отдавала пустой справочник (найдено 2026-11-10).

    Возвращает:
      items — плоский список {id, name, type, parent_id} (для поиска по
              названию и показа имён по id);
      tree  — для nv-tree-select: [{value, label, children}], где первым
              идёт «Вся Россия» (225), дальше субъекты по алфавиту, внутри —
              города субъекта. Город относится к ближайшему предку-субъекту
              (автономные округа внутри областей — отдельные субъекты).
    """
    nodes: dict[int, dict[str, Any]] = {}
    for row in raw:
        try:
            rid = int(row["GeoRegionId"])
        except (KeyError, TypeError, ValueError):
            continue
        parent = row.get("ParentId")
        try:
            parent_id = int(parent) if parent not in (None, "") else None
        except (TypeError, ValueError):
            parent_id = None
        nodes[rid] = {
            "id": rid,
            "name": str(row.get("GeoRegionName") or "").strip(),
            "type": str(row.get("GeoRegionType") or ""),
            "parent_id": parent_id,
        }

    def ancestors(rid: int) -> list[int]:
        chain: list[int] = []
        cur = nodes[rid]["parent_id"]
        while cur is not None and cur in nodes and cur not in chain and len(chain) < 32:
            chain.append(cur)
            cur = nodes[cur]["parent_id"]
        return chain

    areas: dict[int, dict[str, Any]] = {}
    cities: list[tuple[dict[str, Any], list[int]]] = []
    items: list[dict[str, Any]] = []
    for node in nodes.values():
        if node["id"] == GEO_RUSSIA_ID:
            continue
        chain = ancestors(node["id"])
        if GEO_RUSSIA_ID not in chain or not node["name"]:
            continue
        if node["type"] in GEO_AREA_TYPES:
            areas[node["id"]] = {"value": str(node["id"]), "label": node["name"], "children": []}
            items.append(node)
        elif node["type"] == "City":
            cities.append((node, chain))
            items.append(node)

    top_cities: list[dict[str, Any]] = []
    for node, chain in cities:
        leaf = {"value": str(node["id"]), "label": node["name"]}
        area_id = next((a for a in chain if a in areas), None)
        if area_id is None:
            top_cities.append(leaf)
        else:
            areas[area_id]["children"].append(leaf)

    regions = sorted(areas.values(), key=lambda n: _geo_sort_key(n["label"]))
    for region in regions:
        if region["children"]:
            region["children"].sort(key=lambda n: _geo_sort_key(n["label"]))
        else:
            del region["children"]
    top_cities.sort(key=lambda n: _geo_sort_key(n["label"]))

    tree: list[dict[str, Any]] = []
    if regions or top_cities:
        tree.append({"value": str(GEO_RUSSIA_ID), "label": "Вся Россия"})
        items.append({"id": GEO_RUSSIA_ID, "name": "Россия", "type": "Country", "parent_id": None})
    tree.extend(top_cities + regions)
    items.sort(key=lambda x: _geo_sort_key(x["name"]))
    return {"items": items, "tree": tree}


@app.get("/api/geo-regions")
async def get_geo_regions(_: AuthUser):
    """Яндекс-справочник регионов (Dictionaries.get GeoRegions) по России —
    визард (шаг 1, поле «Город или регион показа») показывает его деревом
    «регион → города» и отправляет выбранные id в region_ids. Не привязан к
    конкретному проекту/запуску — любой уже подключённый аккаунт Click.ru
    подходит, справочник не account-specific.
    Кэшируется в app_settings (статичный справочник, меняется редко):
    первый запрос идёт в Click.ru/Direct, остальные — из кэша. Пустой
    результат НЕ кэшируется, чтобы временный сбой не залипал навсегда.
    """
    cached = db.get_setting(GEO_CACHE_KEY)
    if cached and cached.get("tree"):
        return cached

    bundled = _bundled_geo_raw()
    if bundled:
        geo = build_russia_geo(bundled)
        if geo["tree"]:
            db.set_setting(GEO_CACHE_KEY, geo)
            return geo

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

    geo = build_russia_geo(result.get("GeoRegions") or [])
    if geo["tree"]:
        db.set_setting(GEO_CACHE_KEY, geo)
    return geo


@app.post("/api/creative-images", status_code=201)
async def upload_creative_image(request: Request, _: AuthUser):
    """Картинка креатива с компьютера (визард, шаг 5). Тело — сам файл
    (Content-Type: image/jpeg|png|gif), не base64: nginx перед addAvto
    пропускает до 10 МБ, а base64 раздул бы файл на треть. Ответ —
    {"ref": "local:<sha>.jpg"}, его кладут в image_urls проекта; при
    запуске байты уйдут в Директ через AdImages.add."""
    data = await request.body()
    try:
        ref = creative_images.save_image(data)
    except creative_images.CreativeImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ref": ref}


@app.get("/api/creative-images/{name}")
async def get_creative_image(name: str, _: AuthUser):
    """Превью для визарда — JSON с data URL (прокси ЛК отдаёт только JSON)."""
    try:
        return {"ref": creative_images.REF_PREFIX + name, "data_url": creative_images.data_url(name)}
    except creative_images.CreativeImageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _mask_account(acc: dict[str, Any]) -> dict[str, Any]:
    """Аккаунт Click.ru без секретов: servicePassword заменяем признаком."""
    out = {k: v for k, v in acc.items() if k not in ("servicePassword", "password")}
    pw = acc.get("servicePassword")
    out["has_service_password"] = bool(pw)
    out["service_password_length"] = len(pw) if isinstance(pw, str) else 0
    return out


@app.get("/api/accounts/{account_id}")
async def get_clickru_account(
    account_id: int, _: AuthUser, connection_id: int | None = Query(default=None, ge=1)
):
    """Один аккаунт Click.ru (GET /accounts/{id}) — пароль Директа
    (servicePassword) не отдаётся, только признак, есть ли он."""
    client = _resolve_clickru_client(connection_id)
    try:
        return _mask_account(await client.account(account_id))
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    stats = db.project_stats()
    for item in items:
        st = stats.get(item["id"]) or {}
        item["has_launched"] = bool(st.get("jobs_count"))
        # Для фильтров/сортировки списка в ЛК (2026-11-10).
        item["stats"] = {
            "jobs_count": st.get("jobs_count", 0), "last_job_status": st.get("last_job_status"),
            "last_job_at": st.get("last_job_at"), "running_job_id": st.get("running_job_id"),
            "campaigns_count": st.get("campaigns_count", 0), "payer_ids": st.get("payer_ids", []),
            "connection_ids": st.get("connection_ids", []),
            "ozon_running": (_OZON_JOBS.get(item["id"]) or {}).get("status") == "running",
        }
    return {"items": items}


def _project_history(project: dict[str, Any]) -> dict[str, Any]:
    """Таймлайн проекта: создание, генерации Ozon и прочие события журнала,
    запуски bulk-create с итогом по каждому аккаунту — новые сверху."""
    pid = project["id"]
    events: list[dict[str, Any]] = [{
        "kind": "created", "at": project.get("created_at"), "title": "Проект создан",
        "detail": f"{'Директ Озон' if project.get('launch_mode') == 'ozon' else 'Директ'} · {project.get('domain') or ''}",
    }]
    for ev in db.list_project_events(pid):
        events.append({"kind": ev["kind"], "at": ev["created_at"], "title": ev["title"], "detail": ev["detail"]})
    for job in db.list_bulk_jobs(pid):
        items = job.get("items") or []
        events.append({
            "kind": "launch_started", "at": job["started_at"], "job_id": job["id"],
            "title": f"Запуск #{job['id']}: заказано аккаунтов — {job['requested']}"
                     + (f" (продолжение #{job['resumed_from']})" if job.get("resumed_from") else ""), "detail": "",
        })
        if job.get("finished_at") or job["status"] != "running":
            failed = [i for i in items if i.get("status") != "created"]
            events.append({
                "kind": "launch_" + ("done" if job["status"] == "done" else job["status"]),
                "at": job.get("finished_at") or job["started_at"], "job_id": job["id"],
                "title": f"Запуск #{job['id']} {'завершён' if job['status'] == 'done' else 'с ошибкой' if job['status'] == 'failed' else 'идёт'}: "
                         f"создано {job['created']} из {job['requested']}",
                "detail": job.get("error") or ("; ".join(
                    f"{i.get('pool_login') or 'аккаунт'}: {i.get('error') or 'ошибка'}" for i in failed[:5]) if failed else ""),
                "items": items,
            })
    events.sort(key=lambda e: e.get("at") or "", reverse=True)

    # Аккаунты, в которых реально есть кампании проекта, + итог их запуска.
    by_login: dict[str, dict[str, Any]] = {}
    for job in db.list_bulk_jobs(pid):
        for it in job.get("items") or []:
            if it.get("pool_login") and it["pool_login"] not in by_login:
                by_login[it["pool_login"]] = {**it, "job_id": job["id"]}
    accounts = []
    for row in db.list_bulk_campaigns(pid):
        it = by_login.get(row.get("pool_login") or "", {})
        accounts.append({
            "pool_login": row.get("pool_login"), "account_id": row.get("account_id"),
            "campaign_id": row.get("campaign_id"), "connection_id": row.get("connection_id"),
            "payer_id": row.get("payer_id"), "created_at": row.get("created_at"),
            "job_id": it.get("job_id"), "headline": it.get("headline"), "ads_created": it.get("ads_created"),
            "image_used": it.get("image_used"), "sitelinks_used": it.get("sitelinks_used"),
            "callouts_used": it.get("callouts_used"), "note": it.get("note"),
        })
    ozon = _OZON_JOBS.get(pid) or {}
    resume = None
    for job in db.list_bulk_jobs(pid):  # новые сверху; при продолжении частями остаток может быть у старого
        plan = _resume_plan(job)
        if plan:
            resume = {"job_id": job["id"], "remaining": plan["remaining"], "reuse_accounts": len(plan["reuse"])}
            break
    return {
        "project_id": pid, "events": events, "accounts": accounts, "resume": resume,
        "ozon_status": ozon.get("status"),
        "running_job_id": next((j["id"] for j in db.list_bulk_jobs(pid) if j["status"] == "running"), None),
    }


@app.get("/api/projects/{project_id}/history")
async def get_project_history(project_id: int, _: AuthUser):
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return _project_history(project)


def _project_campaign_row(project_id: int, account_id: int) -> dict[str, Any]:
    if db.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    row = next((r for r in db.list_bulk_campaigns(project_id) if r.get("account_id") == account_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="В этом проекте нет кампаний в таком аккаунте")
    return row


@app.get("/api/projects/{project_id}/accounts/{account_id}/check")
async def check_project_account(project_id: int, account_id: int, _: AuthUser):
    """Что реально лежит в Директе у аккаунта проекта (2026-11-10): кампания
    (статус/состояние), группы, объявления с модерацией, ключи, картинка,
    быстрые ссылки и уточнения — живым запросом к Direct API через Click.ru.

    Кампании создаются в отдельном аккаунте Click.ru (логин вида porg-…),
    в который пуловый Яндекс ID войти не может (проверено) — поэтому
    проверка через API, а не через браузер."""
    row = _project_campaign_row(project_id, account_id)
    client = _resolve_clickru_client(row.get("connection_id"))
    try:
        login = await _resolve_client_login(client, account_id)
        out: dict[str, Any] = {"account_id": account_id, "client_login": login, "pool_login": row.get("pool_login"),
                               "campaigns": []}
        cid = row.get("campaign_id")
        if not cid:
            return out
        camps = (await client.direct_api(login, "campaigns", {"method": "get", "params": {
            "SelectionCriteria": {"Ids": [cid]},
            "FieldNames": ["Id", "Name", "Status", "State", "StatusClarification", "StatusPayment", "Type"],
        }})).get("result", {}).get("Campaigns", [])
        groups = (await client.direct_api(login, "adgroups", {"method": "get", "params": {
            "SelectionCriteria": {"CampaignIds": [cid]}, "FieldNames": ["Id", "Name", "Status", "RegionIds"],
        }})).get("result", {}).get("AdGroups", [])
        ads = (await client.direct_api(login, "ads", {"method": "get", "params": {
            "SelectionCriteria": {"CampaignIds": [cid]},
            "FieldNames": ["Id", "AdGroupId", "Status", "StatusClarification", "State", "Type"],
            "TextAdFieldNames": ["Title", "Title2", "Text", "Href", "AdImageHash", "SitelinkSetId", "AdExtensions"],
        }})).get("result", {}).get("Ads", [])
        kw = (await client.direct_api(login, "keywords", {"method": "get", "params": {
            "SelectionCriteria": {"CampaignIds": [cid]}, "FieldNames": ["Id", "Status"],
        }})).get("result", {}).get("Keywords", [])
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    for c in camps:
        out["campaigns"].append({
            **{k: c.get(k) for k in ("Id", "Name", "Status", "State", "StatusClarification", "StatusPayment", "Type")},
            "ad_groups": len(groups), "keywords": len(kw),
            "keywords_by_status": {s: sum(1 for k in kw if k.get("Status") == s) for s in {k.get("Status") for k in kw}},
            "ads": [{
                "id": a.get("Id"), "status": a.get("Status"), "state": a.get("State"),
                "clarification": a.get("StatusClarification"),
                "title": (a.get("TextAd") or {}).get("Title"), "title2": (a.get("TextAd") or {}).get("Title2"),
                "text": (a.get("TextAd") or {}).get("Text"), "href": (a.get("TextAd") or {}).get("Href"),
                "has_image": bool((a.get("TextAd") or {}).get("AdImageHash")),
                "has_sitelinks": bool((a.get("TextAd") or {}).get("SitelinkSetId")),
                "callouts": len(((a.get("TextAd") or {}).get("AdExtensions") or [])),
            } for a in ads],
        })
    return out


@app.post("/api/projects/{project_id}/accounts/{account_id}/open-direct")
async def open_account_direct(project_id: int, account_id: int, _: AuthUser):
    """Открывает Директ аккаунта Click.ru (porg-…) в Chrome на ПК в WSL:
    cookies пулового Yandex ID → Яндекс ID → «Ваши организации» → организация
    porg-… → Директ с её кампаниями (схема Dima, проверено 2026-11-10).
    Только для аккаунтов кампаний этого проекта."""
    row = _project_campaign_row(project_id, account_id)
    if not row.get("pool_login"):
        raise HTTPException(status_code=409, detail="У аккаунта нет логина пула")
    client = _resolve_clickru_client(row.get("connection_id"))
    try:
        org_login = await _resolve_client_login(client, account_id)
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        result = await ParsYaPoolClient().open_pool_browser(row["pool_login"], "direct", org_login=org_login)
    except ParsYaPoolError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    db.add_project_event(project_id, "browser_opened", f"Открыт Директ {org_login} в Chrome на ПК",
                         "Вход выполнен" if result.get("logged_in") else (result.get("error") or "Войти не удалось"))
    return {**result, "org_login": org_login}


@app.post("/api/projects/{project_id}/sync-href")
async def sync_project_href(project_id: int, _: AuthUser):
    """Проставить в уже созданных объявлениях проекта полную ссылку запуска
    (с UTM). Объявления, где ссылка уже такая, не трогает. Изменение ссылки
    у промодерированного объявления Директ отправит на перемодерацию."""
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    href = _ad_href(project)
    api_v = {"api_version": "v501"} if project.get("campaign_mode") == "single_epk" else {}
    results = []
    for row in db.list_bulk_campaigns(project_id):
        res: dict[str, Any] = {"account_id": row.get("account_id"), "campaign_id": row.get("campaign_id"), "updated": 0}
        try:
            client = _resolve_clickru_client(row.get("connection_id"))
            login = await _resolve_client_login(client, int(row["account_id"]))
            ads = (await client.direct_api(login, "ads", {"method": "get", "params": {
                "SelectionCriteria": {"CampaignIds": [int(row["campaign_id"])]},
                "FieldNames": ["Id"], "TextAdFieldNames": ["Href"],
            }}, **api_v)).get("result", {}).get("Ads") or []
            todo = [{"Id": a["Id"], "TextAd": {"Href": href}} for a in ads if (a.get("TextAd") or {}).get("Href") != href]
            if todo:
                data = await client.direct_api(login, "ads", {"method": "update", "params": {"Ads": todo}}, **api_v)
                errs = [e for r in (data.get("result") or {}).get("UpdateResults") or [] for e in r.get("Errors") or []]
                if errs:
                    raise ClickRUError("; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in errs[:3]))
            res["updated"] = len(todo)
        except (ClickRUError, HTTPException, httpx.HTTPError) as exc:
            res["error"] = str(getattr(exc, "detail", None) or exc)[:300]
        results.append(res)
    total = sum(r["updated"] for r in results)
    db.add_project_event(project_id, "href_sync", f"Ссылка объявлений обновлена: {total}", href[:300])
    return {"href": href, "items": results, "updated": total}


@app.get("/api/direct/button-actions")
async def direct_button_actions(_: AuthUser):
    """Кнопки объявлений, которые предлагает Директ (без своего текста)."""
    return {"items": [{"value": k, "label": v} for k, v in BUTTON_ACTIONS.items()]}


def _strategy_for_update(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strategy_for_update(v) for k, v in obj.items()
                if v is not None and k not in ("ExplorationBudget", "PlacementTypes")}
    return obj


def _raise_update_errors(data: dict[str, Any]) -> None:
    errs = [e for r in (data.get("result") or {}).get("UpdateResults") or [] for e in r.get("Errors") or []]
    if errs:
        raise ClickRUError("; ".join(f"{e.get('Message')} ({e.get('Details')})" for e in errs[:3]))


@app.post("/api/projects/{project_id}/apply-ad-settings")
async def apply_ad_settings(project_id: int, payload: dict[str, Any], _: AuthUser):
    """Применить к УЖЕ созданным кампаниям проекта:
      button_action — кнопку во всех объявлениях (пусто — не трогать);
      maps_off — выключить Яндекс Карты в площадках поиска (только ЕПК).
    Изменение объявления Директ отправит на перемодерацию."""
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    button = payload.get("button_action") or None
    if button is not None and button not in BUTTON_ACTIONS:
        raise HTTPException(status_code=422, detail="Неизвестная кнопка — выберите из списка Директа")
    is_epk = project.get("campaign_mode") == "single_epk"
    maps_off = bool(payload.get("maps_off")) and is_epk
    if not button and not maps_off:
        raise HTTPException(status_code=422, detail="Нечего применять: выберите кнопку и/или выключение Карт (только ЕПК)")
    api_v = {"api_version": "v501"} if is_epk else {}
    href = _ad_href(project)
    results = []
    for row in db.list_bulk_campaigns(project_id):
        res: dict[str, Any] = {"account_id": row.get("account_id"), "campaign_id": row.get("campaign_id"),
                               "ads_updated": 0, "maps_off": False}
        try:
            client = _resolve_clickru_client(row.get("connection_id"))
            login = await _resolve_client_login(client, int(row["account_id"]))
            cid = int(row["campaign_id"])
            if maps_off:
                # Директ требует в update всю стратегию поиска (тип + настройки):
                # берём текущую, убираем null и read-only ExplorationBudget.
                got = await client.direct_api(login, "campaigns", {"method": "get", "params": {
                    "SelectionCriteria": {"Ids": [cid]}, "FieldNames": ["Id"],
                    "UnifiedCampaignFieldNames": ["BiddingStrategy"]}}, **api_v)
                camps = (got.get("result") or {}).get("Campaigns") or []
                search = _strategy_for_update(
                    ((camps[0].get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Search") if camps else None)
                if not search:
                    raise ClickRUError("не удалось прочитать стратегию поиска кампании")
                search["PlacementTypes"] = {"Maps": "NO"}
                data = await client.direct_api(login, "campaigns", {"method": "update", "params": {"Campaigns": [{
                    "Id": cid, "UnifiedCampaign": {"BiddingStrategy": {"Search": search}},
                }]}}, **api_v)
                _raise_update_errors(data)
                res["maps_off"] = True
            if button:
                ads = (await client.direct_api(login, "ads", {"method": "get", "params": {
                    "SelectionCriteria": {"CampaignIds": [cid]}, "FieldNames": ["Id"],
                    "TextAdFieldNames": ["Href", "ButtonExtension"],
                }}, **api_v)).get("result", {}).get("Ads") or []
                todo = [{"Id": a["Id"], "TextAd": {"ButtonExtension": {
                            "Action": button, "Href": (a.get("TextAd") or {}).get("Href") or href}}}
                        for a in ads if "TextAd" in a
                        and ((a["TextAd"].get("ButtonExtension") or {}).get("Action") != button)]
                if todo:
                    _raise_update_errors(await client.direct_api(
                        login, "ads", {"method": "update", "params": {"Ads": todo}}, **api_v))
                res["ads_updated"] = len(todo)
        except (ClickRUError, HTTPException, httpx.HTTPError) as exc:
            res["error"] = str(getattr(exc, "detail", None) or exc)[:300]
        results.append(res)
    parts = []
    if button:
        parts.append(f"кнопка «{BUTTON_ACTIONS[button]}» — объявлений {sum(r['ads_updated'] for r in results)}")
    if maps_off:
        parts.append(f"Карты выключены — кампаний {sum(1 for r in results if r['maps_off'])}")
    errors = "; ".join(f"{r['account_id']}: {r['error']}" for r in results if r.get("error"))
    db.add_project_event(project_id, "ad_settings", "Настройки объявлений: " + "; ".join(parts), errors[:300])
    return {"items": results, "button_action": button, "maps_off": maps_off}


@app.post("/api/projects/{project_id}/fund-and-start")
async def fund_and_start_project(project_id: int, payload: dict[str, Any], _: AuthUser):
    """Пополнить и/или запустить уже созданные кампании проекта.
    Тело: {"amount_rub": 1220, "start": true, "account_ids": [...] (необязательно,
    по умолчанию все)}. Пополнение НЕ идемпотентно — каждый вызов списывает
    деньги заново, поэтому ЛК спрашивает подтверждение с итоговой суммой."""
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    try:
        amount = float(payload.get("amount_rub") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="amount_rub — число")
    if amount < 0 or amount > 100000:
        raise HTTPException(status_code=422, detail="Сумма пополнения — от 0 до 100 000 ₽")
    start = bool(payload.get("start"))
    if not amount and not start:
        raise HTTPException(status_code=422, detail="Укажите сумму пополнения и/или запуск")
    wanted = {int(x) for x in payload.get("account_ids") or []}
    rows = [r for r in db.list_bulk_campaigns(project_id) if not wanted or r.get("account_id") in wanted]
    if not rows:
        raise HTTPException(status_code=404, detail="Нет кампаний для пополнения/запуска")
    is_epk = project.get("campaign_mode") == "single_epk"
    results = []
    for row in rows:
        res: dict[str, Any] = {"account_id": row.get("account_id"), "campaign_id": row.get("campaign_id")}
        try:
            client = _resolve_clickru_client(row.get("connection_id"))
            login = await _resolve_client_login(client, int(row["account_id"]))
            post = await _fund_and_start(client, int(row["account_id"]), login, int(row["campaign_id"]),
                                         row.get("payer_id"), amount, start, is_epk)
            res.update(post)
        except (ClickRUError, HTTPException, httpx.HTTPError) as exc:
            res.update(notes=[str(getattr(exc, "detail", None) or exc)[:300]], started=False, topup_status="ERROR")
        results.append(res)
    funded = sum(1 for r in results if r.get("topup_status") == "SUCCESS")
    started = sum(1 for r in results if r.get("started"))
    db.add_project_event(
        project_id, "fund_start",
        f"Пополнение/запуск: пополнено {funded} из {len(results)}" + (f" по {amount:g} ₽" if amount else "")
        + (f", запущено {started}" if start else ""),
        "; ".join(f"{r['account_id']}: " + ", ".join(r.get("notes") or []) for r in results)[:1500],
    )
    return {"items": results, "funded": funded, "started": started, "total": len(results)}


@app.post("/api/projects/{project_id}/metrika-grants")
async def grant_project_metrika(project_id: int, _: AuthUser):
    """Для уже созданных кампаний проекта: выдать организации Директа
    (porg-…) доступ «на просмотр» к счётчику Метрики пулового Yandex ID,
    где лежит цель lead. Идемпотентно — существующий доступ не дублируется."""
    if db.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    project = db.get_project(project_id)
    from urllib.parse import urlsplit
    host = (urlsplit(project["domain"]).hostname or "").strip().lower().rstrip(".")
    results = []
    for row in db.list_bulk_campaigns(project_id):
        res: dict[str, Any] = {"account_id": row.get("account_id"), "pool_login": row.get("pool_login")}
        try:
            client = _resolve_clickru_client(row.get("connection_id"))
            org_login = await _resolve_client_login(client, int(row["account_id"]))
            counter_id, _goal = await _ensure_lead_goal_for_account(row["pool_login"], host, project["name"])
            err = await _grant_metrika_to_org(row["pool_login"], counter_id, org_login)
            res.update(org_login=org_login, counter_id=counter_id, ok=err is None, error=err)
        except (ClickRUError, MetrikaAPIError, HTTPException, httpx.HTTPError) as exc:
            res.update(ok=False, error=str(getattr(exc, "detail", None) or exc)[:300])
        results.append(res)
    ok = sum(1 for r in results if r.get("ok"))
    db.add_project_event(project_id, "metrika_grants", f"Доступ к счётчикам Метрики выдан организациям: {ok} из {len(results)}",
                         "; ".join(r["error"] for r in results if r.get("error"))[:1500])
    return {"items": results, "ok": ok, "total": len(results)}


@app.post("/api/projects/{project_id}/accounts/{account_id}/open-metrika")
async def open_account_metrika(project_id: int, account_id: int, _: AuthUser):
    """Открывает Яндекс Метрику пулового Яндекс ID аккаунта (там скрипт
    создаёт счётчик и цель «лид») в Chrome на ПК в WSL с cookies из пула —
    глазами проверить счётчик и цель. Только для аккаунтов кампаний этого
    проекта — произвольный логин пула из ЛК не открыть."""
    row = _project_campaign_row(project_id, account_id)
    if not row.get("pool_login"):
        raise HTTPException(status_code=409, detail="У аккаунта нет логина пула")
    try:
        result = await ParsYaPoolClient().open_pool_browser(row["pool_login"], "metrika")
    except ParsYaPoolError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    db.add_project_event(project_id, "browser_opened", f"Открыта Метрика логина {row['pool_login']} в Chrome на ПК",
                         "Вход выполнен" if result.get("logged_in") else "Сессия Яндекса устарела — нужен вход вручную")
    return result


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


# Генерация Ozon→Gemini идёт 1–5 минут (браузер + несколько вызовов
# Gemini), а запрос из ЛК проходит nginx (120 с), parsya-control (30 с) и
# nginx перед addAvto (60 с) — синхронный ответ обрывался по дороге
# («addAvto unreachable»). Поэтому POST только ЗАПУСКАЕТ задачу и сразу
# отвечает 202, а фронт опрашивает GET того же адреса. Состояние — в памяти
# процесса: uvicorn здесь один; после рестарта задачу просто запускают снова.
_OZON_JOBS: dict[int, dict[str, Any]] = {}
# Капча Ozon проходится вручную до 8 мин + Gemini — с запасом.
OZON_JOB_TIMEOUT_SECONDS = 900


def _ozon_job_public(job: dict[str, Any] | None) -> dict[str, Any]:
    if job is None:
        return {"status": "none"}
    return {k: job.get(k) for k in ("status", "error", "result", "url", "started_at", "finished_at", "captcha_session")}


OZON_MAX_IMAGES = 10


def _ozon_remote_fetch(project_id: int, session_id: str, label: str, image_refs: list[str]):
    """Карточку Ozon открывает настоящий Chrome в WSL (parsya-control →
    ozon_fetch.py): с IP этого сервера Ozon отдаёт 403 и Chromium, и Google
    Chrome (проверено 2026-11-10). OZON_FETCH_VIA=local — старый путь
    (HTTP + браузер в контейнере), например для запуска вне прода.

    Фото товара тот же Chrome скачивает из открытой страницы (CDN Ozon режет
    и сервер, и curl) — сохраняем их в data/creative-images/ и кладём
    ссылки "local:..." в image_refs: при запуске в Директ уйдут байты."""
    if os.getenv("OZON_FETCH_VIA", "wsl").strip().lower() == "local":
        return None

    async def fetch(path: str, extra_paths: list[str]) -> dict[str, Any]:
        try:
            got = await ParsYaPoolClient().ozon_fetch_pages(path, extra_paths, session_id, label,
                                                             max_images=OZON_MAX_IMAGES)
        except ParsYaPoolError as e:
            raise OzonParseError(f"Карточка Ozon не получена: {e}") from e
        local_paths: list[str] = []
        for img in got.get("images") or []:
            try:
                raw = base64.b64decode(img.get("b64") or "")
                ref = creative_images.save_image(raw)
            except (creative_images.CreativeImageError, ValueError) as e:
                print(f"[ozon] фото {img.get('url')} не сохранено: {e}", flush=True)
                continue
            path = str(creative_images.path_of(ref))
            if path not in local_paths:
                local_paths.append(path)  # Gemini смотрит все фото
            # в объявления — только те, что примет Директ (стороны/пропорции)
            if ref not in image_refs and creative_images.direct_image_ok(raw):
                image_refs.append(ref)
        fetch.used = True  # фото брались через Chrome — ссылки CDN Ozon в проект не пишем
        return {"pages": got.get("pages") or [], "local_images": local_paths}

    fetch.used = False
    return fetch


async def _ozon_generate_apply(project_id: int, url: str, captcha_session: str = "") -> dict[str, Any]:
    """Парсинг + Gemini + запись результата в проект. Возвращает
    OzonGenerateOut как dict; ошибки — OzonParseError/GeminiError."""
    current = db.get_project(project_id) or {}
    image_refs: list[str] = []
    remote = _ozon_remote_fetch(project_id, captcha_session or secrets.token_urlsafe(12),
                                f"проект #{project_id} {current.get('name') or ''}".strip(), image_refs)
    try:
        result = await asyncio.wait_for(
            process_ozon_url(url, out_dir=None, download=False, use_ai=True, browser=_ozon_browser,
                             remote_fetch=remote),
            timeout=OZON_JOB_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise OzonParseError(f"Генерация не уложилась в {OZON_JOB_TIMEOUT_SECONDS // 60} минут — попробуйте ещё раз.")
    project = db.get_project(project_id)
    if project is None:
        raise OzonParseError("Проект удалён во время генерации")

    product, ad_copy = result.product, result.ad_copy
    assert ad_copy is not None  # use_ai=True всегда возвращает ad_copy или бросает GeminiError выше

    # Headlines/ad_texts пулы для bulk-create's shuffle — берём ИМЕННО
    # yandex_direct (title1/text уже написаны с лимитами Директа 56/81
    # символов и привязаны к своей группе ключей), а не ad_copy.headlines
    # (универсальные заголовки до 60 символов — превышают лимит Директа).
    headlines = list(dict.fromkeys(d.title1 for d in ad_copy.yandex_direct if d.title1))[:100]
    ad_texts = list(dict.fromkeys(d.text for d in ad_copy.yandex_direct if d.text))[:100]

    update_data = {
        "domain": product.url.rstrip("/"),  # same normalization ProjectIn.validate_domain applies to a typed-in domain
        "ozon_url": url,
        "keywords": "\n".join(ad_copy.keywords),
        "negative_keywords": "\n".join(ad_copy.negative_keywords),
        # Скачанные в WSL фото ("local:..."); ссылки CDN Ozon — только если
        # шли старым путём (OZON_FETCH_VIA=local), сервер их всё равно не скачает.
        "image_urls": (image_refs if getattr(remote, "used", False) else product.images)[:20],
        "headlines": headlines,
        "ad_texts": ad_texts,
        # Уточнения и быстрые ссылки пишет Gemini; адреса ссылок строит
        # скрипт от ссылки на товар (отзывы/вопросы/описание/характеристики).
        "callouts_pool": list(dict.fromkeys(c.strip() for c in ad_copy.yandex_callouts
                                            if c.strip() and len(c.strip()) <= 25))[:200],
        "sitelinks_pool": build_ozon_sitelinks(product.url, ad_copy.yandex_sitelinks),
    }
    # launch_mode остаётся "ozon" (bulk-create его не читает вовсе — только
    # headlines/ad_texts/keywords/image_urls/domain выше, которые уже
    # обычные project-поля) — это только для UI: открыв проект снова,
    # визард помнит, что creative pool пришёл из Ozon, и показывает кнопку
    # "Пересоздать через Gemini" на шаге 5.
    # "Товар Ozon" is the generic placeholder the wizard itself sends when the
    # name field is left blank (ProjectIn.name needs min_length=2) — treat it
    # the same as genuinely blank, so the real scraped title still wins.
    current_name = (project.get("name") or "").strip()
    if not current_name or current_name == "Товар Ozon":
        update_data["name"] = (product.title or "Товар Ozon")[:120]
    updated = db.update_project(project_id, update_data)
    assert updated is not None

    return OzonGenerateOut(
        project=updated,
        ad_copy=ad_copy.model_dump(mode="json"),
        warnings=ad_copy.warnings,
    ).model_dump(mode="json")


async def _ozon_generate_job(project_id: int, url: str, job: dict[str, Any]) -> None:
    try:
        job["result"] = await _ozon_generate_apply(project_id, url, job.get("captcha_session") or "")
        job["status"] = "done"
    except (OzonParseError, GeminiError) as e:
        job["status"], job["error"] = "failed", str(e)
    except Exception as e:  # noqa: BLE001 — любую ошибку показываем в UI, а не теряем в фоне
        print(f"[ozon-generate] project={project_id}: {type(e).__name__}: {e}", flush=True)
        job["status"], job["error"] = "failed", f"Внутренняя ошибка генерации: {type(e).__name__}: {str(e)[:300]}"
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            if job["status"] == "done":
                res = job.get("result") or {}
                p, ad = res.get("project") or {}, res.get("ad_copy") or {}
                db.add_project_event(
                    project_id, "ozon_done", "Gemini написал тексты по карточке Ozon",
                    f"Модель {ad.get('model_used') or '—'}: {len(p.get('headlines') or [])} заголовков, "
                    f"{len(p.get('ad_texts') or [])} текстов, {len(p.get('image_urls') or [])} фото, "
                    f"{len(p.get('callouts_pool') or [])} уточнений, {len(p.get('sitelinks_pool') or [])} быстрых ссылок",
                )
            else:
                db.add_project_event(project_id, "ozon_failed", "Генерация по Ozon не удалась", job.get("error") or "")
        except Exception as exc:  # noqa: BLE001 — журнал не должен ронять генерацию
            print(f"[project-events] {exc}", flush=True)


@app.post("/api/projects/{project_id}/ozon-generate", status_code=202)
async def ozon_generate(project_id: int, payload: OzonGenerateIn, _: AuthUser):
    """Директ-Озон режим (wizard шаг 1): ЗАПУСКАЕТ фоном парсинг карточки
    Ozon и тексты Gemini (app/ozon_ad_creator.py). По готовности результат
    записывается прямо в project["domain"/"headlines"/"ad_texts"/"keywords"/
    "negative_keywords"/"image_urls"] — те же поля, что ручной режим
    заполняет через PUT /api/projects/{id}; шаги 2-5 и bulk-create не знают,
    откуда они взялись. Ответ 202 со статусом задачи; итог — GET того же
    адреса: done → result = {project, ad_copy, warnings}, failed → error.

    url в теле — для одноразовой регенерации по другой ссылке без
    предварительного PUT; пустая строка/отсутствие — берём project["ozon_url"].
    Повторный POST, пока задача идёт, вторую не запускает — отдаёт текущую.
    """
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if db.has_bulk_job(project_id):
        raise HTTPException(status_code=409, detail="Проект уже запущен — генерация недоступна.")

    url = (payload.url or project.get("ozon_url") or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="Нужна ссылка на карточку Ozon (ozon_url)")

    current = _OZON_JOBS.get(project_id)
    if current and current["status"] == "running":
        return _ozon_job_public(current)
    job: dict[str, Any] = {"status": "running", "error": None, "result": None, "url": url,
                           "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
                           "captcha_session": secrets.token_urlsafe(12)}
    _OZON_JOBS[project_id] = job
    db.add_project_event(project_id, "ozon_started", "Запущена генерация по карточке Ozon", url)
    task = asyncio.create_task(_ozon_generate_job(project_id, url, job))
    _BACKGROUND_JOB_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_JOB_TASKS.discard)
    return _ozon_job_public(job)


@app.get("/api/projects/{project_id}/ozon-generate")
async def ozon_generate_status(project_id: int, _: AuthUser):
    """Статус фоновой генерации: none | running | done (+result) | failed (+error)."""
    if db.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return _ozon_job_public(_OZON_JOBS.get(project_id))


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
    if not _marking_ok(schema):
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


SITELINK_TITLES_MAX_TOTAL = 66  # Директ: сумма заголовков быстрых ссылок 1–4


import unicodedata as _ud

# Директ (2026-11-11, живая ошибка): «Sitelink titles may contain letters …,
# digits, and punctuation. The characters "!" and "?", line breaks, and
# emojis are not allowed» — Gemini написал «Отзывы 4.9★», и весь набор
# быстрых ссылок (а с ним кампания) не создался, хотя аккаунт уже был создан.
_DIRECT_SAFE_PUNCT = set(" .,:;-–—\"'«»()/%№+&*#@₽$€")


def _direct_safe_text(text: str, allow_marks: bool = False) -> str:
    """Оставляет буквы любых алфавитов, цифры, пробел и обычную пунктуацию;
    убирает эмодзи, «★»-подобные символы, переводы строк; «!» и «?» —
    только если allow_marks."""
    out = []
    for ch in str(text or ""):
        cat = _ud.category(ch)
        if cat[0] in ("L", "N") or ch in _DIRECT_SAFE_PUNCT or (allow_marks and ch in "!?"):
            out.append(ch)
        elif ch in "\n\r\t":
            out.append(" ")
    return " ".join("".join(out).split())


def _safe_sitelink(sl: dict[str, Any]) -> dict[str, Any] | None:
    title = _direct_safe_text(sl.get("title"))[:30]
    if not title:
        return None
    return {**sl, "title": title, "description": _direct_safe_text(sl.get("description"))[:60]}


def _pick_sitelinks(pool: list[dict[str, Any]], shuffle: bool, count: int = 4) -> list[dict[str, Any]]:
    """До 4 быстрых ссылок, у которых сумма заголовков ≤ 66 символов —
    иначе Директ отклоняет весь набор. shuffle — случайный набор на
    аккаунт; иначе по порядку пула. Не влезает четыре — берём меньше."""
    # Чистим ДО подсчёта 66 символов: очистка укорачивает заголовки; дубли
    # заголовков после очистки Директ тоже отклонит — оставляем первый.
    seen_titles: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for sl in pool or []:
        safe = _safe_sitelink(sl)
        if safe and safe["title"].lower() not in seen_titles:
            seen_titles.add(safe["title"].lower())
            cleaned.append(safe)
    pool = cleaned
    if not pool:
        return []
    order = list(pool)
    best: list[dict[str, Any]] = []
    for _ in range(30 if shuffle else 1):
        if shuffle:
            random.shuffle(order)
        picked: list[dict[str, Any]] = []
        total = 0
        for sl in order:
            n = len(str(sl.get("title") or ""))
            if total + n <= SITELINK_TITLES_MAX_TOTAL:
                picked.append(sl)
                total += n
            if len(picked) == count:
                return picked
        if len(picked) > len(best):
            best = picked
    return best


async def _download_image_base64(url: str) -> str:
    # "local:<sha>.jpg" — картинка уже у нас на диске (фото Ozon, скачанное
    # Chrome в WSL, или файл, загруженный с компьютера) — app/creative_images.py.
    if creative_images.is_ref(url):
        return creative_images.read_b64(url)
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


def _ad_href(project: dict[str, Any]) -> str:
    """Ссылка объявления — РОВНО та, что вставлена при запуске (2026-11-12,
    Dima: «вставлялась с utm-метками, а в Директе без них»). У Ozon-проекта
    domain — очищенный адрес товара (для счётчика Метрики), а полная ссылка с
    UTM и макросами Директа ({campaign_id}, {banner_id}…) — в ozon_url."""
    if project.get("launch_mode") == "ozon" and (project.get("ozon_url") or "").strip():
        url = project["ozon_url"].strip()
        return url if url.startswith(("http://", "https://")) else "https://" + url
    return project["domain"]


TOPUP_STATUS_WAIT_SECONDS = 60
# Тип схемы маркировки, создаваемой автоматически перед пополнением/запуском.
MARKING_SCHEMA_TYPE = os.getenv("CLICKRU_MARKING_SCHEMA_TYPE", "END_ADVERTISER").strip() or "END_ADVERTISER"


def _marking_ok(schema: dict[str, Any]) -> bool:
    """GET /markings/schemas/account отвечает {"schemas": [...]}; пустой
    список = маркировка не настроена."""
    return bool(schema.get("schemas") or schema.get("configured") or schema.get("isConfigured"))


async def _ensure_marking(client: ClickRUClient, account_id: int, payer_id: int | None) -> tuple[bool, str | None]:
    """Схема маркировки обязательна и для пополнения, и для запуска
    (2026-11-12: «Для пополнения рекламного аккаунта заполните схему
    маркировки»). Если её нет — создаём «Конечный рекламодатель» (основная)
    на юрлицо запуска. Возвращает (готово, заметка)."""
    try:
        if _marking_ok(await client.marking_schema(account_id)):
            return True, None
        if not payer_id:
            return False, "маркировка не настроена, а юрлицо для схемы не выбрано"
        scheme_id = await client.create_marking_schema(account_id, int(payer_id), MARKING_SCHEMA_TYPE)
        label = "Конечный рекламодатель" if MARKING_SCHEMA_TYPE == "END_ADVERTISER" else MARKING_SCHEMA_TYPE
        return True, f"Маркировка: создана схема «{label}» на юрлицо #{payer_id}" + (f" (№{scheme_id})" if scheme_id else "")
    except (ClickRUError, httpx.HTTPError) as exc:
        return False, f"маркировка не настроена: {str(exc)[:200]}"


async def _fund_and_start(
    client: ClickRUClient, account_id: int, client_login: str, campaign_id: int,
    payer_id: int | None, amount_rub: float | None, start: bool, is_epk: bool,
) -> dict[str, Any]:
    """После создания кампании (2026-11-12, Dima: «автоматом пополнялись с
    Click.ru по 1220 ₽ с НДС и автоматом запускались»):
      1) пополнение аккаунта с баланса Click.ru (payments/add_money, сумма с
         НДС, с юрлица запуска) и ожидание статуса до 60 с;
      0) маркировка ОРД/ERID: нет схемы → создаём (_ensure_marking), иначе
         ни пополнение, ни запуск Click.ru не пропустит;
      2) запуск: объявления на модерацию (ads.moderate) → campaigns.resume.
    НИКОГДА не бросает: кампания уже создана, ошибки уходят в notes."""
    out: dict[str, Any] = {"topup_rub": None, "topup_status": None, "started": False, "notes": []}
    want_topup = bool(amount_rub and amount_rub > 0)
    if want_topup or start:
        marked, note = await _ensure_marking(client, account_id, payer_id)
        if not marked:
            if want_topup:
                out["topup_rub"] = round(float(amount_rub), 2)
                out["topup_status"] = "SKIPPED"
            out["notes"].append(f"Не пополнено и не запущено: {note}")
            return out
        if note:
            out["notes"].append(note)
    if want_topup:
        out["topup_rub"] = round(float(amount_rub), 2)
        try:
            task_id = await client.add_money(account_id, amount_rub, payer_id)
            status = "SENT"
            if task_id:
                for _ in range(TOPUP_STATUS_WAIT_SECONDS // 5):
                    status = await client.add_money_status(task_id) or status
                    if status in ("SUCCESS", "ERROR", "CANCELLED"):
                        break
                    await asyncio.sleep(5)
            out["topup_status"] = status
            if status == "SUCCESS":
                out["notes"].append(f"Пополнено на {amount_rub:g} ₽ с НДС")
            elif status in ("ERROR", "CANCELLED"):
                out["notes"].append(f"Пополнение на {amount_rub:g} ₽ не прошло ({status}, задача {task_id})")
            else:
                out["notes"].append(f"Пополнение на {amount_rub:g} ₽ в обработке (задача {task_id})")
        except (ClickRUError, httpx.HTTPError) as exc:
            out["topup_status"] = "ERROR"
            out["notes"].append(f"Пополнение не выполнено: {str(exc)[:200]}")
    if start:
        try:
            sent = await client.moderate_campaign_ads(client_login, campaign_id, **({"api_version": "v501"} if is_epk else {}))
            try:
                await client.resume_campaign(client_login, campaign_id)
            except ClickRUError as exc:
                # новая кампания обычно уже «включена» — Директ может ответить,
                # что возобновлять нечего; модерация при этом отправлена
                if sent == 0:
                    raise
                out["notes"].append(f"resume: {str(exc)[:120]}")
            out["started"] = True
            out["notes"].append(f"Запущено: на модерацию отправлено объявлений — {sent}")
        except (ClickRUError, httpx.HTTPError) as exc:
            out["notes"].append(f"Не запущено: {str(exc)[:200]}")
    return out


async def _grant_metrika_to_org(pool_login: str, counter_id: int, org_login: str) -> str | None:
    """Выдаёт логину организации Директа (porg-…) доступ «на просмотр» к
    счётчику пулового Yandex ID (2026-11-11, Dima: «у организации нет
    доступа к счётчику»). Возвращает текст ошибки или None. Не роняет запуск:
    кампания без доступа к цели всё равно создаётся, ошибка видна в note."""
    try:
        metrika = await _metrika_context(pool_login=pool_login)
        await metrika.ensure_grant(counter_id, org_login, "view", comment="addAvto: Директ организации Click.ru")
        return None
    except (MetrikaAPIError, HTTPException, httpx.HTTPError) as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        return f"Доступ к счётчику {counter_id} для {org_login} не выдан: {str(detail)[:200]}"


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
    job_id = _start_bulk_job(project_id, payload)
    return BulkJobStartOut(job_id=job_id, project_id=project_id, status="running")


def _start_bulk_job(
    project_id: int,
    payload: BulkCampaignCreateRequest,
    reuse: list[dict[str, Any]] | None = None,
    resumed_from: int | None = None,
    indexes: list[int] | None = None,
) -> int:
    """Проверки + создание задания + запуск фоновой корутины. Общая часть
    обычного запуска и продолжения прерванного (reuse — аккаунты, уже
    созданные провижинингом, но без кампании; indexes — какие номера
    аккаунтов исходного запуска ещё не сделаны)."""
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
    # Проверить кабинет ДО старта: иначе каждый аккаунт пула был бы
    # захвачен и тут же отпущен с одной и той же ошибкой входа.
    connection = db.get_clickru_connection(payload.connection_id)
    assert connection is not None  # _resolve_clickru_client уже вернул бы 404
    _clickru_provision_payload(connection)

    pool_client = ParsYaPoolClient()
    if not pool_client.configured:
        raise HTTPException(status_code=503, detail="PARSYA_POOL_API_TOKEN не задан в .env")

    if db.get_running_bulk_job(project_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Массовый запуск для этого проекта уже выполняется — подождите его завершения.",
        )

    request = payload.model_dump(mode="json")
    if indexes:
        request["_indexes"] = indexes  # номера аккаунтов исходного запуска — для следующего продолжения
    job_id = db.create_bulk_job(project_id, payload.count, request=request, resumed_from=resumed_from)
    task = asyncio.create_task(_run_bulk_job(job_id, project_id, project, project_host, phrases, client, pool_client,
                                             payload, reuse=reuse or [], indexes=indexes))
    _BACKGROUND_JOB_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_JOB_TASKS.discard)
    return job_id


async def _run_bulk_job(
    job_id: int,
    project_id: int,
    project: dict[str, Any],
    project_host: str,
    phrases: list[str],
    client: ClickRUClient,
    pool_client: ParsYaPoolClient,
    payload: BulkCampaignCreateRequest,
    reuse: list[dict[str, Any]] | None = None,
    indexes: list[int] | None = None,
) -> None:
    reuse = list(reuse or [])
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
            campaign_id: int | None = None
            # Номер аккаунта в ИСХОДНОМ запуске (при продолжении — не 0..N).
            orig_index = indexes[index] if indexes and index < len(indexes) else index
            existing_campaign_id: int | None = None
            try:
                if reuse:
                    # Продолжение: аккаунт уже создан прошлым запуском
                    # (пул потрачен), кампании в нём нет — провижининг не нужен.
                    again = reuse.pop(0)
                    pool_login, account_id = again["pool_login"], int(again["account_id"])
                    # кампания уже создана прошлым запуском, но не доделана —
                    # доделываем её, а не создаём вторую (2026-11-11)
                    existing_campaign_id = again.get("campaign_id")
                else:
                    # Перечитываем подключение на каждой итерации: прошлая могла
                    # обновить cookies сессии кабинета.
                    connection = db.get_clickru_connection(payload.connection_id)
                    if connection is None:
                        raise ParsYaPoolError("Подключение Click.ru удалено во время запуска")
                    try:
                        clickru_web = _clickru_provision_payload(connection)
                    except HTTPException as exc:
                        raise ParsYaPoolError(str(exc.detail)) from exc
                    try:
                        provisioned = await pool_client.provision_direct_account(
                            account_name_prefix=project["name"][:10], clickru=clickru_web,
                        )
                    except ParsYaPoolError as exc:
                        if CLICKRU_LOGIN_FAILED_MARKER in str(exc):
                            reason = str(exc).replace(CLICKRU_LOGIN_FAILED_MARKER, "").strip()
                            db.set_clickru_web_session(payload.connection_id, "failed", reason, cookies_enc="")
                            raise ParsYaPoolError(f"Вход в кабинет Click.ru не удался: {reason}") from exc
                        raise
                    if clickru_web is not None:
                        _save_clickru_cookies(payload.connection_id, provisioned.get("clickru_cookies"), "Сессия активна")
                    pool_login = provisioned["pool_login"]
                    account_id = int(provisioned["account_id"])
                db.set_bulk_job_pending(job_id, {"pool_login": pool_login, "account_id": account_id, "index": orig_index})

                client_login = await _resolve_client_login(client, account_id)
                counter_id, goal_id = await _ensure_lead_goal_for_account(pool_login, project_host, project["name"])
                # Счётчик в Метрике пулового ID, кампания — в организации porg-…:
                # даём организации доступ к счётчику, иначе её Директ не видит цель.
                grant_note = await _grant_metrika_to_org(pool_login, counter_id, client_login)
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
                    this_sitelinks = [s for s in (_safe_sitelink(sl.model_dump()) for sl in creative.sitelinks) if s]
                    this_callouts = list(creative.callouts)
                elif payload.creative_mode == "duplicate_single":
                    this_sitelinks = _pick_sitelinks(sitelinks_pool, shuffle=False)
                    this_callouts = callouts_pool[:5]
                else:  # shuffle
                    this_sitelinks = _pick_sitelinks(sitelinks_pool, shuffle=True)
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

                # те же ограничения символов у уточнений (эмодзи, «★» и т.п.)
                this_callouts = [c for c in dict.fromkeys(_direct_safe_text(t)[:25] for t in this_callouts) if c]
                ad_extension_ids = [await client.create_callout(client_login, text) for text in this_callouts]

                # ЕПК: кампания, группа (UnifiedAdGroup), фразы и объявления — Direct API v501
                is_epk = project["campaign_mode"] == "single_epk"
                api_v = {"api_version": "v501"} if is_epk else {}
                if existing_campaign_id:
                    campaign_id = int(existing_campaign_id)
                elif is_epk:
                    campaign_id = await client.create_draft_unified_campaign(
                        client_login,
                        project["name"],
                        date.today().isoformat(),
                        target_drr=project["target_drr"],
                        goal_id=goal_id,
                        counter_id=counter_id,
                    )
                else:
                    campaign_id = await client.create_draft_text_campaign(
                        client_login,
                        project["name"],
                        date.today().isoformat(),
                        target_drr=project["target_drr"],
                        goal_id=goal_id,
                    )

                note: str | None = "; ".join(x for x in (
                    "Доделана кампания прошлого запуска" if existing_campaign_id else None, grant_note) if x) or None
                mobile_pct = project.get("mobile_bid_adjustment_percent")
                if mobile_pct is not None and not existing_campaign_id:
                    try:
                        await client.add_mobile_bid_adjustment(client_login, campaign_id, int(mobile_pct))
                    except ClickRUError as exc:
                        # Best-effort: the campaign itself is already valid
                        # without this, so a rejected adjustment shouldn't
                        # sink the whole account.
                        note = f"Корректировка для мобильных не применена: {exc}"

                ad_group_id = None
                has_keywords = False
                if existing_campaign_id:
                    groups = (await client.direct_api(client_login, "adgroups", {"method": "get", "params": {
                        "SelectionCriteria": {"CampaignIds": [campaign_id]}, "FieldNames": ["Id"],
                    }}, **api_v)).get("result", {}).get("AdGroups") or []
                    if groups:
                        ad_group_id = int(groups[0]["Id"])
                        kws = (await client.direct_api(client_login, "keywords", {"method": "get", "params": {
                            "SelectionCriteria": {"AdGroupIds": [ad_group_id]}, "FieldNames": ["Id"],
                        }}, **api_v)).get("result", {}).get("Keywords") or []
                        has_keywords = bool(kws)
                if ad_group_id is None:
                    ad_group_id = await client.create_adgroup(
                        client_login, campaign_id, project["name"], project["region_ids"],
                        negative_keywords=negative_keywords or None,
                        **({"unified": True} if is_epk else {}),
                    )
                if not has_keywords:
                    await client.add_keywords(client_login, ad_group_id, phrases, **api_v)

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
                        # Картинка необязательна: неподходящую по размеру/пропорциям
                        # пропускаем, пробуем другие из пула, иначе объявление без
                        # картинки — но аккаунт с кампанией не валим (2026-11-11).
                        tries = [this_image_url] + [u for u in image_urls if u != this_image_url][:3]
                        img_errors: list[str] = []
                        for url_try in tries:
                            try:
                                image_b64 = await _download_image_base64(url_try)
                                if not creative_images.direct_image_ok(base64.b64decode(image_b64)):
                                    img_errors.append("размер/пропорции не подходят Директу")
                                    continue
                                ad_image_hash = await client.upload_ad_image(client_login, project["name"][:40], image_b64)
                                break
                            except (ClickRUError, httpx.HTTPError, creative_images.CreativeImageError, ValueError) as img_exc:
                                img_errors.append(str(img_exc)[:120])
                        if ad_image_hash is None and img_errors:
                            note = ((note + "; ") if note else "") + "Объявление без картинки: " + img_errors[-1]

                    ad_kwargs = dict(
                        ad_image_hash=ad_image_hash, sitelink_set_id=sitelink_set_id,
                        ad_extension_ids=ad_extension_ids or None, **({"api_version": "v501"} if is_epk else {}),
                    )
                    button = getattr(payload, "button_action", None)
                    try:
                        await client.create_text_ad(client_login, ad_group_id, headline, ad_text, _ad_href(project),
                                                    button_action=button, **ad_kwargs)
                    except ClickRUError as btn_exc:
                        if not button:
                            raise
                        # кнопку Директ может не принять — объявление важнее
                        await client.create_text_ad(client_login, ad_group_id, headline, ad_text, _ad_href(project), **ad_kwargs)
                        note = ((note + "; ") if note else "") + f"Объявление без кнопки: {str(btn_exc)[:150]}"
                    ads_created += 1
                    if ad_index == 0:
                        first_headline, first_ad_text, first_image_used = headline, ad_text, ad_image_hash is not None

                db.record_bulk_campaign(
                    project_id, pool_login, account_id, campaign_id,
                    connection_id=payload.connection_id, payer_id=payload.payer_id,
                )
                post = await _fund_and_start(
                    client, account_id, client_login, campaign_id, payload.payer_id,
                    payload.topup_amount_rub, payload.auto_start, is_epk,
                )
                if post["notes"]:
                    note = "; ".join(x for x in [note, *post["notes"]] if x)
                db.append_bulk_job_item(job_id, BulkCampaignItemResult(
                    topup_rub=post["topup_rub"], topup_status=post["topup_status"], started=post["started"],
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
                    index=orig_index,
                ).model_dump())
                db.set_bulk_job_pending(job_id, None)
            except (ParsYaPoolError, ClickRUError, MetrikaAPIError, httpx.HTTPError) as exc:
                db.append_bulk_job_item(job_id, BulkCampaignItemResult(
                    pool_login=pool_login,
                    account_id=account_id,
                    counter_id=counter_id,
                    goal_id=goal_id,
                    campaign_id=campaign_id,
                    status="failed",
                    error=str(exc),
                    index=orig_index,
                ).model_dump())
                db.set_bulk_job_pending(job_id, None)
        db.finish_bulk_job(job_id, "done")
    except Exception as exc:  # pragma: no cover - safety net so a job never sticks at "running"
        db.finish_bulk_job(job_id, "failed", error=str(exc))


def _resume_plan(job: dict[str, Any]) -> dict[str, Any] | None:
    """Что можно продолжить у завершённого/прерванного запуска: сколько
    кампаний не хватает, какие уже созданные провижинингом аккаунты стоят
    без кампании (их берём первыми — пул не тратим заново) и какие номера
    аккаунтов исходного запуска ещё не сделаны. None — продолжать нечего
    или запуск старый (до 2026-11-10 параметры запуска не сохранялись)."""
    if job["status"] == "running" or not job.get("request"):
        return None
    remaining = int(job["requested"]) - int(job["created"])
    if remaining <= 0:
        return None
    pid = job["project_id"]
    used_accounts = {r.get("account_id") for r in db.list_bulk_campaigns(pid)}
    # Продолжения этого запуска (можно частями, limit): их номера аккаунтов
    # и взятые ими аккаунты уже не наши — остаток продолжается дальше.
    children = [later for later in db.list_bulk_jobs(pid) if later.get("resumed_from") == job["id"]]
    if any(ch["status"] == "running" for ch in children):
        return None
    taken_idx: set[int] = set()
    for ch in children:
        taken_idx.update((ch.get("request") or {}).get("_indexes") or [])
        used_accounts.update(it.get("account_id") for it in ch["items"] if it.get("account_id"))
        if ch.get("pending"):
            used_accounts.add(ch["pending"].get("account_id"))
    remaining -= sum(int(ch["requested"]) for ch in children)
    if remaining <= 0:
        return None
    reuse: list[dict[str, Any]] = []
    candidates = ([job["pending"]] if job.get("pending") else []) + [
        # и аккаунты с недоделанной кампанией (campaign_id есть, item failed) — их доделаем
        it for it in job["items"] if it.get("status") != "created" and it.get("account_id")
    ]
    for c in candidates:
        if c.get("account_id") and c.get("pool_login") and c["account_id"] not in used_accounts \
                and all(r["account_id"] != c["account_id"] for r in reuse):
            reuse.append({"pool_login": c["pool_login"], "account_id": int(c["account_id"]),
                          "campaign_id": c.get("campaign_id")})
    done = {it.get("index") for it in job["items"] if it.get("status") == "created"}
    base = job["request"].get("_indexes") or list(range(int(job["requested"])))
    if None in done:
        left = base[len(done):]
    else:
        left = [i for i in base if i not in done]
    left = [i for i in left if i not in taken_idx][:remaining]
    return {"remaining": remaining, "reuse": reuse[:remaining], "indexes": left}


@app.get("/api/projects/{project_id}/campaigns/bulk-jobs/{job_id}", response_model=BulkJobOut)
async def get_bulk_job(project_id: int, job_id: int, _: AuthUser):
    job = db.get_bulk_job(job_id)
    if job is None or job["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    plan = _resume_plan(job)
    return BulkJobOut(
        job_id=job["id"],
        project_id=job["project_id"],
        status=job["status"],
        requested=job["requested"],
        created=job["created"],
        items=job["items"],
        error=job["error"],
        resumed_from=job.get("resumed_from"),
        can_resume=plan is not None,
        remaining=plan["remaining"] if plan else 0,
    )


def _resume_bulk_job(project_id: int, job: dict[str, Any], limit: int | None = None) -> int:
    plan = _resume_plan(job)
    if plan is None:
        raise HTTPException(status_code=409, detail="Этот запуск продолжить нельзя: всё создано, он ещё идёт, "
                                                    "уже продолжен или запущен до сохранения параметров")
    if limit:  # частями: например, сначала один аккаунт для проверки
        n = min(plan["remaining"], limit)
        plan = {"remaining": n, "reuse": plan["reuse"][:n], "indexes": plan["indexes"][:n]}
    req = dict(job["request"])
    base = req.pop("_indexes", None) or list(range(int(job["requested"])))
    if req.get("creative_mode") == "manual_per_account":
        # creative_sets этого запуска идут в порядке base (номера исходного запуска)
        pos = {orig: p for p, orig in enumerate(base)}
        req["creative_sets"] = [req["creative_sets"][pos[i]] for i in plan["indexes"]]
    payload = BulkCampaignCreateRequest(**{**req, "count": plan["remaining"]})
    return _start_bulk_job(project_id, payload, reuse=plan["reuse"], resumed_from=job["id"], indexes=plan["indexes"])


AUTO_RESUME_MAX_CHAIN = 3
AUTO_RESUME_DELAY_SECONDS = 20


async def _auto_resume_interrupted(job_ids: list[int]) -> None:
    """После перезапуска сервиса сами продолжаем запуски, которые он
    оборвал (просьба Dima 2026-11-10: «нажать продолжить или автоматом»).
    Пауза — чтобы сервис поднялся; не больше 3 продолжений подряд в одной
    цепочке, чтобы падающий запуск не крутился бесконечно."""
    await asyncio.sleep(AUTO_RESUME_DELAY_SECONDS)
    for job_id in job_ids:
        job = db.get_bulk_job(job_id)
        if job is None or _resume_plan(job) is None:
            continue
        depth, cur = 0, job
        while cur and cur.get("resumed_from"):
            depth += 1
            cur = db.get_bulk_job(int(cur["resumed_from"]))
        if depth >= AUTO_RESUME_MAX_CHAIN:
            print(f"[auto-resume] #{job_id}: уже {depth} продолжений подряд — только вручную", flush=True)
            continue
        try:
            new_id = _resume_bulk_job(job["project_id"], job)
        except HTTPException as exc:
            print(f"[auto-resume] #{job_id}: {exc.detail}", flush=True)
            db.add_project_event(job["project_id"], "launch_failed", f"Автопродолжение запуска #{job_id} не удалось",
                                 str(exc.detail))
            continue
        print(f"[auto-resume] #{job_id} → #{new_id}", flush=True)
        db.add_project_event(job["project_id"], "launch_resumed",
                             f"Запуск #{job_id} продолжен автоматически как #{new_id}",
                             f"Осталось {job['requested'] - job['created']} — после перезапуска сервиса")


@app.post("/api/projects/{project_id}/campaigns/bulk-jobs/{job_id}/resume", response_model=BulkJobStartOut, status_code=202)
async def resume_bulk_job(
    project_id: int, job_id: int, _: AuthUser, limit: int | None = Query(default=None, ge=1, le=20),
):
    """«Продолжить запуск»: досоздаёт недостающие кампании прерванного или
    частично неудачного запуска с теми же параметрами. Сначала — аккаунты,
    которые провижининг уже создал, а кампания в них не появилась.
    limit — продолжить частями (остаток можно продолжить следующим вызовом)."""
    job = db.get_bulk_job(job_id)
    if job is None or job["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    new_id = _resume_bulk_job(project_id, job, limit=limit)
    db.add_project_event(project_id, "launch_resumed", f"Запуск #{job_id} продолжен как #{new_id}",
                         f"Осталось {job['requested'] - job['created']}")
    return BulkJobStartOut(job_id=new_id, project_id=project_id, status="running")


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
