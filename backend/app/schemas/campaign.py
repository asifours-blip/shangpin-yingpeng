"""活动创建、启动和步骤查询。"""

from datetime import datetime
from typing import Any

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CampaignCreateIn(BaseModel):
    product_id: int = Field(gt=0)
    fact_version_id: int = Field(gt=0)
    source_item_ids: list[int] = Field(default_factory=list, max_length=100)
    target_platforms: list[Literal["douyin", "xiaohongshu"]] = Field(
        default_factory=lambda: ["douyin", "xiaohongshu"], min_length=1, max_length=2,
    )
    generation_requirements: str = Field(default="", max_length=500)
    generation_budget: int = Field(default=12, ge=0, le=100)

    @field_validator("target_platforms")
    @classmethod
    def unique_platforms(cls, platforms: list[str]) -> list[str]:
        if len(platforms) != len(set(platforms)):
            raise ValueError("目标平台不能重复")
        return platforms


class VariantOut(BaseModel):
    id: int
    platform: str
    content_type: str
    version: int
    title: str | None = None
    body: str | None = None
    hashtags: list[Any] = Field(default_factory=list)
    storyboard: dict[str, Any] | None = None
    status: str
    qc_result: dict[str, Any] | None = None
    fact_version_id: int | None = None

    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    id: int
    step_key: str
    variant_platform: str
    version: int
    status: str
    depends_on: list[str]
    attempt: int
    local_request_id: str | None = None
    provider_request_id: str | None = None
    copywriting_operation_id: int | None = None
    error_code: str | None = None
    heartbeat_at: datetime | None = None
    output: dict[str, Any]
    variant_id: int | None = None

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: int
    recipe_version: str
    generation_budget: int
    budget_reserved: int
    idempotency_key: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[StepOut]


class CampaignOut(BaseModel):
    id: int
    product_id: int
    fact_version_id: int
    status: str
    brief: dict[str, Any]
    selected_source_item_ids: list[int]
    target_platforms: list[str]
    generation_connection: str
    variants: list[VariantOut]
    run: RunOut | None = None
