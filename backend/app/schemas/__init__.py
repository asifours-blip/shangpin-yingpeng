from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_frozen: bool = False
    freeze_reason: str | None = None
    frozen_at: datetime | None = None

    model_config = {"from_attributes": True}


class AssetOut(BaseModel):
    id: int
    role: str
    url: str | None = None
    mime: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None


class GenerationCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    mode: str = "t2i"
    size: str = "2048x2048"
    retry_of_id: int | None = None
    # i2i：优先 product_asset_id + 可选 scene_asset_id；兼容旧 input_asset_ids（[0] 商品 [1] 场景）
    input_asset_ids: list[int] = Field(default_factory=list)
    product_asset_id: int | None = None
    scene_asset_id: int | None = None
    reverse_op_id: int | None = None
    optimize_op_id: int | None = None
    # 生成意图备注，写入 params.keep_features；不是保真保证
    keep_features: str | None = None


class GenerationReviewIn(BaseModel):
    review_status: str
    review_reason: str | None = None


class GenerationOut(BaseModel):
    id: int
    mode: str
    prompt: str
    params: dict[str, Any]
    status: str
    error_message: str | None
    retry_of_id: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    assets: list[AssetOut] = []
    review_status: str = "unreviewed"
    review_reason: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by_id: int | None = None
    reverse_op_id: int | None = None
    optimize_op_id: int | None = None
    # 旧行仅有 role=input、无 product/scene 时为 true
    legacy_unlabeled: bool = False

    model_config = {"from_attributes": True}


class GenerationListOut(BaseModel):
    items: list[GenerationOut]


class PromptOptimizeIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PromptReverseIn(BaseModel):
    asset_id: int


class PromptOut(BaseModel):
    prompt: str


class PromptOperationOut(BaseModel):
    id: int
    op_type: str
    input_text: str | None = None
    input_asset_id: int | None = None
    input_object_key: str | None = None
    output_prompt: str | None = None
    status: str
    error_message: str | None = None
    created_at: datetime
    asset: AssetOut | None = None


class PromptOperationListOut(BaseModel):
    items: list[PromptOperationOut]
