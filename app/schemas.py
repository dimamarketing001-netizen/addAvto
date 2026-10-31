from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator


CampaignMode = Literal["separate", "single_epk"]
StrategyType = Literal["AVERAGE_CRR", "PAY_FOR_CONVERSION_CRR"]


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


class ClickRUConnectionOut(BaseModel):
    id: int
    label: str
    user_id: str
    token_preview: str
    created_at: str


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
    name: str = Field(min_length=2, max_length=120)
    domain: str = Field(min_length=8, max_length=2048)
    campaign_mode: CampaignMode = "separate"
    strategy: StrategyType = "AVERAGE_CRR"
    target_drr: float = Field(default=30, ge=0.1, le=1000)
    budget: float = Field(default=20000, ge=0, le=100000000)
    lead_value: float = Field(default=0, ge=0, le=100000000)
    headline: str = Field(default="", max_length=200)
    ad_text: str = Field(default="", max_length=2000)
    keywords: str = Field(default="", max_length=30000)
    negative_keywords: str = Field(default="", max_length=30000)
    image_urls: list[str] = Field(default_factory=list, max_length=20)
    region_ids: list[int] = Field(default_factory=list, max_length=300)

    # Creative pools for bulk campaign generation (POST /campaigns/bulk-create):
    # each generated campaign picks ONE random headline + ad_text + image,
    # and a random SUBSET of sitelinks/callouts, from these. headline/
    # ad_text/image_urls above are the fallback when a pool field is empty.
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
        cleaned: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
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


class BulkCampaignCreateResult(BaseModel):
    project_id: int
    requested: int
    created: int
    items: list[BulkCampaignItemResult]
    warning: str = (
        "Каждая кампания создана ЧЕРНОВИКОМ (Status=DRAFT, State=OFF) — "
        "запуск не выполняется автоматически."
    )


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
    warning: str = (
        "Каждая кампания создана ЧЕРНОВИКОМ (Status=DRAFT, State=OFF) — "
        "запуск не выполняется автоматически."
    )
