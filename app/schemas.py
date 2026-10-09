from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator


CampaignMode = Literal["separate", "single_epk"]
StrategyType = Literal["AVERAGE_CRR", "PAY_FOR_CONVERSION_CRR"]


class DirectAccountCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    integration_id: int = Field(ge=1)


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
    account_ids: list[int] = Field(default_factory=list, max_length=200)
    counter_id: int | None = Field(default=None, ge=1)
    goal_id: int | None = Field(default=None, ge=1)
    region_ids: list[int] = Field(default_factory=list, max_length=300)

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

    @field_validator("account_ids", "region_ids")
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
