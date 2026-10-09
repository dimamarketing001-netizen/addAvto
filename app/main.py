from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .click_client import ClickRUClient, ClickRUError
from .schemas import CampaignPlan, DirectAccountCreate, PlanItem, ProjectIn


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
