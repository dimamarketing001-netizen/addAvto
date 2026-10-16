from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .click_client import ClickRUClient, ClickRUError
from .metrika_client import MetrikaAPIError, YandexMetrikaClient
from .schemas import CampaignPlan, DirectAccountCreate, MetrikaSetupRequest, PlanItem, ProjectIn
from .token_crypto import TokenCryptoError, decrypt_secret, encrypt_secret
from .yandex_oauth import YandexOAuthClient, YandexOAuthError


security = HTTPBasic()
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


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


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, _: AuthUser):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/health")
async def health():
    return {"status": "ok", "app": "direct-campaign-manager"}


@app.get("/api/connection")
async def click_connection(_: AuthUser):
    client = ClickRUClient()
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
    allowed = ("id", "name", "service", "status", "state", "currency")
    return {key: item.get(key) for key in allowed if item.get(key) is not None}


async def _metrika_context(
    connection_id: int | None = None,
    owner_username: str | None = None,
) -> YandexMetrikaClient:
    # Legacy single-token support is retained during migration. New users should connect
    # each Yandex ID with the authorization-code flow and select its saved connection.
    if connection_id is None:
        access_token = os.getenv("METRIKA_OAUTH_TOKEN", "").strip()
        if not access_token:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Сначала нажмите «Подключить Яндекс ID» и разрешите доступ к Метрике. "
                    "Либо временно задайте METRIKA_OAUTH_TOKEN в серверном .env."
                ),
            )
        return YandexMetrikaClient(access_token)

    if not owner_username:
        raise HTTPException(status_code=401, detail="Не удалось определить владельца подключения.")
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
                refreshed = None
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


@app.get("/api/metrika/connections")
async def get_metrika_connections(username: AuthUser):
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
        "legacy_token_configured": bool(os.getenv("METRIKA_OAUTH_TOKEN", "").strip()),
        "oauth_configured": bool(oauth_configured),
    }


@app.delete("/api/metrika/connections/{connection_id}")
async def disconnect_metrika_connection(connection_id: int, username: AuthUser):
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
    username: AuthUser,
    label: str = Query(default="Яндекс ID", min_length=1, max_length=80),
):
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
    def finish(result: str) -> RedirectResponse:
        return RedirectResponse(url=f"/?metrika_oauth={result}", status_code=status.HTTP_303_SEE_OTHER)

    if not state:
        return finish("invalid_state")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    oauth_state = db.consume_metrika_oauth_state(state_hash)
    if oauth_state is None:
        return finish("invalid_state")
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
            url=f"/?metrika_oauth=connected&connection_id={int(connection['id'])}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except MetrikaAPIError as exc:
        return finish("permissions" if exc.status_code in (401, 403) else "failed")
    except (YandexOAuthError, TokenCryptoError, HTTPException):
        return finish("failed")


@app.get("/api/metrika/counters")
async def get_metrika_counters(
    username: AuthUser,
    connection_id: int | None = Query(default=None, ge=1),
):
    metrika = await _metrika_context(connection_id, username)
    try:
        counters = await metrika.list_counters()
        return {
            "items": [
                {
                    "id": counter.get("id"),
                    "name": counter.get("name"),
                    "site": _counter_site(counter),
                    "permission": counter.get("permission"),
                    "code_status": counter.get("code_status"),
                }
                for counter in counters
                if counter.get("id") is not None
            ]
        }
    except MetrikaAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/metrika/setup")
async def setup_project_metrika(
    project_id: int,
    payload: MetrikaSetupRequest,
    username: AuthUser,
):
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    from urllib.parse import urlsplit
    project_host = (urlsplit(project["domain"]).hostname or "").strip().lower().rstrip(".")
    if not project_host:
        raise HTTPException(status_code=422, detail="Не удалось определить домен проекта")

    metrika = await _metrika_context(payload.connection_id, username)
    created_counter = False
    try:
        counters = await metrika.list_counters()
        chosen: dict[str, Any] | None = None

        if payload.counter_id is not None:
            chosen = next(
                (counter for counter in counters if str(counter.get("id", "")) == str(payload.counter_id)),
                None,
            )
            if chosen is None:
                raise HTTPException(
                    status_code=422,
                    detail="Выбранный счётчик не найден среди доступных счётчиков для OAuth-токена Метрики.",
                )
            if _normalize_site(_counter_site(chosen)) != _normalize_site(project_host):
                raise HTTPException(
                    status_code=422,
                    detail="Домен выбранного счётчика не совпадает с доменом проекта.",
                )
        else:
            chosen = next(
                (
                    counter for counter in counters
                    if _normalize_site(_counter_site(counter)) == _normalize_site(project_host)
                ),
                None,
            )
            if chosen is None:
                if not payload.create_if_missing:
                    raise HTTPException(
                        status_code=404,
                        detail="Счётчик для домена проекта не найден. Выберите счётчик или разрешите создание нового.",
                    )
                chosen = await metrika.create_counter(project["name"], project_host)
                created_counter = True

        counter_id = int(chosen["id"])
        goals = await metrika.list_goals(counter_id)
        named_lead_goals = [
            goal for goal in goals
            if str(goal.get("name", "")).strip().casefold() == "lead"
        ]
        matching_goal = next((goal for goal in named_lead_goals if _is_lead_goal(goal)), None)
        if matching_goal is not None:
            goal = matching_goal
            created_goal = False
        elif named_lead_goals:
            raise HTTPException(
                status_code=409,
                detail=(
                    "В этом счётчике уже есть цель с именем lead, но её тип/условие отличается. "
                    "Измените её вручную в Метрике или выберите другой счётчик."
                ),
            )
        else:
            goal = await metrika.create_goal(counter_id)
            created_goal = True

        updated = db.update_project(
            project_id,
            {"counter_id": counter_id, "goal_id": int(goal["id"])},
        )
        if updated is None:
            raise HTTPException(status_code=500, detail="Не удалось сохранить ID счётчика и цели в проекте.")

        return {
            "project_id": project_id,
            "counter_id": counter_id,
            "counter_name": chosen.get("name") or project["name"],
            "counter_site": _counter_site(chosen) or project_host,
            "goal_id": int(goal["id"]),
            "goal_name": goal.get("name", "lead"),
            "created_counter": created_counter,
            "created_goal": created_goal,
            "tracking_snippet": f"ym({counter_id}, 'reachGoal', 'lead');",
            "instructions": (
                "Установите код счётчика Метрики на сайт и вызывайте событие reachGoal "
                "только после подтверждённой успешной отправки заявки. Само создание счётчика "
                "не устанавливает код на сайт."
            ),
        }
    except ClickRUError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MetrikaAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/accounts/direct", status_code=201)
async def create_direct_account(payload: DirectAccountCreate, _: AuthUser):
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


@app.get("/api/projects")
async def get_projects(_: AuthUser):
    return {"items": db.list_projects()}


@app.post("/api/projects", status_code=201)
async def create_project(project: ProjectIn, _: AuthUser):
    return db.create_project(project.model_dump(mode="json"))


@app.put("/api/projects/{project_id}")
async def update_project(project_id: int, project: ProjectIn, _: AuthUser):
    item = db.update_project(project_id, project.model_dump(mode="json"))
    if item is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return item


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
        strategy=project["strategy"],
        items=items,
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
