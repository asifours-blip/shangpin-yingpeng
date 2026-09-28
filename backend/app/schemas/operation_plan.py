"""Operator-managed schedule input; budget values are generation-call counts."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


class PlanFields(BaseModel):
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    local_time: str = "09:00"
    source_config_id: int | None = Field(default=None, gt=0)
    product_ids: list[int] = Field(min_length=1, max_length=100)
    target_platforms: list[str] = Field(min_length=1, max_length=2)
    daily_campaign_limit: int = Field(ge=1, le=100)
    daily_budget_limit: int = Field(ge=0, le=10000)
    generation_budget: int = Field(ge=0, le=100)
    auto_advance_to_review: bool = True

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("需要有效 IANA 时区") from exc
        return value

    @field_validator("local_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":" or not value[:2].isdigit() or not value[3:].isdigit():
            raise ValueError("执行时间使用 HH:MM")
        hour, minute = int(value[:2]), int(value[3:])
        if hour > 23 or minute > 59:
            raise ValueError("执行时间使用 24 小时制 HH:MM")
        return value

    @field_validator("product_ids")
    @classmethod
    def valid_products(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value) or len(set(value)) != len(value):
            raise ValueError("商品范围必须是互不重复的有效编号")
        return value

    @field_validator("target_platforms")
    @classmethod
    def valid_platforms(cls, value: list[str]) -> list[str]:
        if any(item not in {"douyin", "xiaohongshu"} for item in value) or len(set(value)) != len(value):
            raise ValueError("目标平台必须从抖音、小红书选择且不能重复")
        return value


class PlanCreateIn(PlanFields):
    pass


class PlanPatchIn(BaseModel):
    expected_version: int = Field(ge=1)
    enabled: bool | None = None
    timezone: str | None = None
    local_time: str | None = None
    source_config_id: int | None = None
    product_ids: list[int] | None = None
    target_platforms: list[str] | None = None
    daily_campaign_limit: int | None = None
    daily_budget_limit: int | None = None
    generation_budget: int | None = None
    auto_advance_to_review: bool | None = None


class WindowIn(BaseModel):
    expected_version: int = Field(ge=1)
    scheduled_for: datetime


class RunActionIn(BaseModel):
    expected_version: int = Field(ge=1)


class ResolveIn(RunActionIn):
    source_item_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
