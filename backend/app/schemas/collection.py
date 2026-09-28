"""采集配置与批次。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CollectionConfigIn(BaseModel):
    provider: Literal["fixture", "taobao", "douyin", "xiaohongshu"]
    item_kind: Literal["product", "post"]
    category: str = Field(default="箱包", max_length=64)
    query: str = Field(default="箱包", max_length=256)
    window: str = Field(default="7d", max_length=32)
    sort_metric: str = Field(default="total_sales", max_length=64)
    max_items: int = Field(default=100, ge=1, le=100)
    schedule: str = Field(default="manual", max_length=64)


class CollectionConfigOut(BaseModel):
    id: int
    provider: str
    item_kind: str
    category: str
    query: str
    window: str
    sort_metric: str
    max_items: int
    schedule: str
    enabled: bool

    model_config = {"from_attributes": True}


class CollectionConfigListOut(BaseModel):
    items: list[CollectionConfigOut]


class CollectionRunCreateIn(BaseModel):
    config_id: int


class CollectionRunOut(BaseModel):
    id: int
    config_id: int
    status: str
    actual_count: int
    scope_description: str
    error_summary: str | None = None
    provider_response_version: str | None = None
    dropped_duplicate: int
    dropped_invalid: int
    started_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class CollectionRunSummaryOut(CollectionRunOut):
    provider: str
    item_kind: str
    sort_metric: str
    window: str


class CollectionRunListOut(BaseModel):
    items: list[CollectionRunSummaryOut]
    total: int


class SourceItemOut(BaseModel):
    id: int
    platform: str
    item_kind: str
    external_id: str
    url: str | None = None
    title: str | None = None
    text_excerpt: str | None = None
    source_rank: int | None = None
    raw_metrics: dict[str, Any]
    observed_at: datetime
    rights_scope: str

    model_config = {"from_attributes": True}


class SourceItemListOut(BaseModel):
    items: list[SourceItemOut]
    total: int
    scope_description: str
    actual_count: int
    status: str
