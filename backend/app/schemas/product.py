"""自家商品与事实版本。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProductCreateIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    facts: dict[str, Any] = Field(default_factory=dict)
    claim_evidence: dict[str, Any] = Field(default_factory=dict)
    primary_asset_id: int | None = None


class ProductPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    active: bool | None = None
    primary_asset_id: int | None = None
    facts: dict[str, Any] | None = None
    claim_evidence: dict[str, Any] | None = None
    expected_version: int | None = None


class FactVersionOut(BaseModel):
    id: int
    version: int
    facts: dict[str, Any]
    claim_evidence: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    active: bool
    primary_asset_id: int | None = None
    latest_fact: FactVersionOut | None = None


class ProductListOut(BaseModel):
    items: list[ProductOut]
