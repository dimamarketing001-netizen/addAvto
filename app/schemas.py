from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator


CampaignMode = Literal["separate", "single_epk"]
StrategyType = Literal["AVERAGE_CRR", "PAY_FOR_CONVERSION_CRR"]
LaunchMode = Literal["direct", "ozon"]


class DirectAccountCreate(BaseModel):
    """Not UI-facing (the manual "create account" form was removed
    2026-10-31 as redundant with bulk-create's auto-provisioning) — kept as
    a machine endpoint because ParsYa's connect_direct_account.py calls
    POST /api/accounts/direct directly as part of the pool-provisioning
    pipeline (confirmed live 2026-10-31, two sequential accounts). Removing
    this would silently break that script.
    """
    name: str = Field(min_length=2, max_length=120)
    integration_id: int = Field(ge=1)


class ClickRUConnectionIn(BaseModel):
    label: str = Field(min_length=2, max_length=120)
    token: str = Field(min_length=10, max_length=500)
    user_id: str = Field(default="", max_length=120)
    # Логин/пароль веб-кабинета click.ru — для подключения Яндекс ID из пула
    # как Директ-интеграции (в API Click.ru такого метода нет). Необязательны
    # при создании, но без них подключение нельзя использовать для запуска
    # (кроме случая, когда токен совпадает с CLICKRU_API_TOKEN из .env).
    web_username: str = Field(default="", max_length=200)
    web_password: str = Field(default="", max_length=200)


class ClickRUWebCredentialsIn(BaseModel):
    web_username: str = Field(min_length=2, max_length=200)
    web_password: str = Field(min_length=1, max_length=200)


class ClickRUConnectionOut(BaseModel):
    id: int
    label: str
    user_id: str
    token_preview: str
    created_at: str


PayerType = Literal["BUSINESS", "INDIVIDUAL", "PERSON"]
TaxForm = Literal["OSN", "USN"]


class PayerAddressIn(BaseModel):
    address: str = Field(min_length=5, max_length=500)
    postal_code: str = Field(min_length=3, max_length=20)


class PayerBankIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    rcbic: str = Field(min_length=9, max_length=9, description="БИК банка")
    account: str = Field(min_length=5, max_length=34)
    correspondent_account: str = Field(min_length=5, max_length=34)


class PayerCreateIn(BaseModel):
    """Создание юрлица/ИП/физлица в Click.ru (POST /users/payers) — шаг 2
    визарда, "+ Добавить юрлицо". Поля опциональны на уровне схемы и
    проверяются по `type` в main.py, т.к. у BUSINESS/INDIVIDUAL/PERSON
    разный обязательный набор (см. Click.ru openapi.json,
    Users.Payers.Business/Individual/Person) — тащить это в pydantic
    discriminated union ради одной формы не стоило.
    """
    type: PayerType
    inn: str = Field(min_length=10, max_length=12)
    phone: str | None = Field(default=None, max_length=20)

    # BUSINESS и INDIVIDUAL
    name: str | None = Field(default=None, max_length=300)
    tax_form: TaxForm | None = None
    business_address: PayerAddressIn | None = None
    actual_address: PayerAddressIn | None = None
    bank: PayerBankIn | None = None

    # Только BUSINESS (ООО)
    full_name: str | None = Field(default=None, max_length=500)
    kpp: str | None = Field(default=None, min_length=9, max_length=9)
    ogrn: int | None = None

    # Только INDIVIDUAL (ИП)
    ogrnip: int | None = None

    # Только PERSON (физлицо)
    last_name: str | None = Field(default=None, max_length=150)
    first_name: str | None = Field(default=None, max_length=150)
    middle_name: str | None = Field(default=None, max_length=150)


class SitelinkIn(BaseModel):
    title: str = Field(max_length=30)
    href: str = Field(max_length=1024)
    description: str = Field(default="", max_length=60)


CreativeMode = Literal["shuffle", "duplicate_single", "manual_per_account"]


class CreativeSetIn(BaseModel):
    """One account's worth of ad creative — used when creative_mode is
    manual_per_account (one entry per account, applied 1:1 in order)."""
    headline: str = Field(max_length=56)
    ad_text: str = Field(max_length=81)
    image_url: str | None = None
    sitelinks: list[SitelinkIn] = Field(default_factory=list, max_length=4)
    callouts: list[str] = Field(default_factory=list, max_length=5)


class ProjectIn(BaseModel):
    """Создаётся ТОЛЬКО через визард массового запуска (шаг 1) — отдельной
    формы создания проекта больше нет (убрана 2026-11-02 вместе со
    `strategy`/`budget`/`lead_value`/`headline`/`ad_text`: все эти поля
    были декоративными, bulk-create их никогда не читал — реальная
    стратегия всегда AVERAGE_CRR, бюджет/ценность лида никуда не
    передавались, headline/ad_text дублировали headlines/ad_texts пулы).
    """
    name: str = Field(min_length=2, max_length=120)
    domain: str = Field(min_length=8, max_length=2048)
    campaign_mode: CampaignMode = "separate"
    # "ozon": domain/headlines/ad_texts/keywords/negative_keywords/image_urls
    # come from POST /api/projects/{id}/ozon-generate (app/ozon_ad_creator.py)
    # instead of being typed in by hand — everything downstream of them
    # (bulk-create, campaign creation) reads the exact same project fields
    # either way and needs no mode-specific branching.
    launch_mode: LaunchMode = "direct"
    ozon_url: str = Field(default="", max_length=2048)
    target_drr: float = Field(default=30, ge=0.1, le=1000)
    keywords: str = Field(default="", max_length=30000)
    # Общие для группы минус-фразы (AdGroups.NegativeKeywords) — Яндекс
    # ограничивает это максимум 3 фразами, до 7 слов каждая, не больше
    # 4096 символов суммарно; не валидируем заранее, Direct сам откажет
    # понятной ошибкой при превышении.
    negative_keywords: str = Field(default="", max_length=30000)
    image_urls: list[str] = Field(default_factory=list, max_length=20)
    region_ids: list[int] = Field(default_factory=list, max_length=300)

    # Creative pools for bulk campaign generation (POST /campaigns/bulk-create):
    # each generated campaign picks ONE random headline + ad_text + image,
    # and a random SUBSET of sitelinks/callouts, from these.
    headlines: list[str] = Field(default_factory=list, max_length=100)
    ad_texts: list[str] = Field(default_factory=list, max_length=100)
    sitelinks_pool: list[SitelinkIn] = Field(default_factory=list, max_length=100)
    callouts_pool: list[str] = Field(default_factory=list, max_length=200)

    # Bid adjustment (корректировка ставки) for mobile traffic, applied to
    # EVERY campaign bulk-create generates for this project via
    # BidModifiers.add — percent relative to the base bid (100 = no change,
    # e.g. -50 halves the mobile bid, 200 doubles it). None skips it
    # entirely (Direct defaults to no adjustment). Range per Direct API's
    # own BidModifier bounds; best-effort at creation time — a failure here
    # doesn't block the campaign itself (see main.py's bulk-create).
    mobile_bid_adjustment_percent: int | None = Field(default=None, ge=-100, le=1200)

    @field_validator("headlines")
    @classmethod
    def validate_headlines(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 56:
                raise ValueError(f"Заголовок длиннее 56 символов: {item!r}")
        return value

    @field_validator("ad_texts")
    @classmethod
    def validate_ad_texts(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 81:
                raise ValueError(f"Текст объявления длиннее 81 символа: {item!r}")
        return value

    @field_validator("callouts_pool")
    @classmethod
    def validate_callouts(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 25:
                raise ValueError(f"Уточнение длиннее 25 символов: {item!r}")
        return value

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("https://", "http://")):
            value = "https://" + value
        parsed = HttpUrl(value)
        return str(parsed).rstrip("/")

    @field_validator("image_urls")
    @classmethod
    def validate_images(cls, value: list[str]) -> list[str]:
        # Yandex Direct's AdImages.add only ever takes raw base64 image
        # bytes — it has no concept of a URL at all. "image_urls" историческое
        # имя поля: ссылка — это НАШЕ удобство (main.py сам её скачивает),
        # а "data:image/...;base64,..." — прямая загрузка с компьютера
        # (см. wizard шаг 5) — оставляем как есть, без HttpUrl-валидации.
        cleaned: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
                continue
            # "local:<sha>.jpg" — картинка уже на нашем диске (app/creative_images.py)
            if item.startswith("data:image/") or re.match(r"^local:[0-9a-f]{32}\.(jpg|png|gif)$", item):
                cleaned.append(item)
                continue
            parsed = HttpUrl(item)
            cleaned.append(str(parsed))
        return cleaned

    @field_validator("region_ids")
    @classmethod
    def unique_positive_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("ID должны быть положительными числами")
        return list(dict.fromkeys(value))


class ProjectOut(ProjectIn):
    id: int
    created_at: str
    updated_at: str


class PlanItem(BaseModel):
    name: str
    placement: str
    note: str


class CampaignPlan(BaseModel):
    project_id: int
    campaign_mode: CampaignMode
    strategy: StrategyType
    items: list[PlanItem]
    warning: str = (
        "Это предварительный план. Реальное создание и включение рекламы не выполняется "
        "до проверки авторизаций и схем API на тестовом аккаунте."
    )


class BulkCampaignCreateRequest(BaseModel):
    count: int = Field(ge=1, le=20)
    connection_id: int = Field(ge=1)
    payer_id: int | None = Field(default=None, ge=1)
    creative_mode: CreativeMode = "shuffle"
    # Required (exactly `count` entries) when creative_mode is
    # manual_per_account; ignored for shuffle/duplicate_single, which draw
    # from the project's own headlines/ad_texts/image_urls/sitelinks_pool/
    # callouts_pool instead.
    creative_sets: list[CreativeSetIn] = Field(default_factory=list, max_length=20)
    # How many separate ads to create in EACH account's ad group (wizard
    # step 4, "структура объявлений"). Yandex Direct allows up to 50 ads
    # per group — letting the group hold several lets Direct's own
    # delivery engine pick the best-performing combo for that account,
    # instead of each account getting exactly one fixed ad. Only makes
    # sense with creative_mode="shuffle" (duplicate_single/manual_per_account
    # give every account exactly one deliberately-chosen creative; >1
    # identical or >1 hand-picked-but-then-duplicated ads isn't useful).
    ads_per_group: int = Field(default=1, ge=1, le=50)

    @field_validator("creative_sets")
    @classmethod
    def validate_creative_sets_count(cls, value: list, info) -> list:
        mode = info.data.get("creative_mode")
        count = info.data.get("count")
        if mode == "manual_per_account" and count is not None and len(value) != count:
            raise ValueError(
                f"creative_mode=manual_per_account требует ровно {count} наборов "
                f"креативов (по одному на аккаунт), получено {len(value)}"
            )
        return value

    @field_validator("ads_per_group")
    @classmethod
    def validate_ads_per_group_needs_shuffle(cls, value: int, info) -> int:
        mode = info.data.get("creative_mode")
        if value > 1 and mode != "shuffle":
            raise ValueError(
                "ads_per_group > 1 поддерживается только с creative_mode=shuffle "
                "(duplicate_single/manual_per_account уже дают ровно один "
                "осознанно выбранный креатив на аккаунт)"
            )
        return value


class BulkCampaignItemResult(BaseModel):
    pool_login: str | None = None
    account_id: int | None = None
    counter_id: int | None = None
    goal_id: int | None = None
    campaign_id: int | None = None
    headline: str | None = None
    ad_text: str | None = None
    image_used: bool = False
    sitelinks_used: int = 0
    callouts_used: int = 0
    ads_created: int = 0
    note: str | None = None
    status: str  # "created" or "failed"
    error: str | None = None
    # Порядковый номер аккаунта в запуске (для manual_per_account при
    # продолжении — какие наборы креативов ещё не использованы).
    index: int | None = None


class BulkCampaignCreateResult(BaseModel):
    project_id: int
    requested: int
    created: int
    items: list[BulkCampaignItemResult]
    warning: str = (
        "Каждая кампания создана ЧЕРНОВИКОМ (Status=DRAFT, State=OFF) — "
        "запуск не выполняется автоматически."
    )


class OzonGenerateIn(BaseModel):
    """POST /api/projects/{id}/ozon-generate body — url overrides the
    project's own stored ozon_url for a one-off regenerate with a different
    link; omitted/empty falls back to project["ozon_url"]."""
    url: str = Field(default="", max_length=2048)


class OzonGenerateOut(BaseModel):
    """Response for a successful (re)generation — `project` is the project
    AFTER its headlines/ad_texts/keywords/negative_keywords/image_urls/
    domain were overwritten from the scrape+Gemini result, `ad_copy` is the
    full result (facts/big_idea/vk_ads/etc.) for the wizard to preview even
    though only a subset of it maps onto the project's own creative pools.
    """
    project: ProjectOut
    ad_copy: dict
    warnings: list[str] = Field(default_factory=list)


BulkJobStatus = Literal["running", "done", "failed"]


class BulkJobStartOut(BaseModel):
    job_id: int
    project_id: int
    status: BulkJobStatus


class BulkJobOut(BaseModel):
    job_id: int
    project_id: int
    status: BulkJobStatus
    requested: int
    created: int
    items: list[BulkCampaignItemResult]
    error: str | None = None
    # Продолжение прерванного запуска (2026-11-10).
    resumed_from: int | None = None
    can_resume: bool = False
    remaining: int = 0
    warning: str = (
        "Каждая кампания создана ЧЕРНОВИКОМ (Status=DRAFT, State=OFF) — "
        "запуск не выполняется автоматически."
    )
