"""营销文案入参 / 出参。结构必须经模型校验，不能只 json.loads。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class CreateIn(BaseModel):
    asset_id: int = Field(gt=0)
    platform: Literal["douyin", "xiaohongshu"]
    product_name: str | None = Field(default=None, max_length=64)
    selling_points: str | None = Field(default=None, max_length=500)
    campaign: str | None = Field(default=None, max_length=200)

    @field_validator("product_name", "selling_points", "campaign")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class GeneratedContent(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=2000)
    hashtags: list[str] = Field(default_factory=list, max_length=15)
    facts_to_confirm: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "body")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @field_validator("hashtags")
    @classmethod
    def _clean_hashtags(cls, value: list[str]) -> list[str]:
        items: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if not text:
                continue
            if len(text) > 40:
                raise ValueError("话题标签过长")
            items.append(text)
        if len(items) > 15:
            raise ValueError("话题标签过多")
        return items

    @field_validator("facts_to_confirm")
    @classmethod
    def _clean_facts(cls, value: list[str]) -> list[str]:
        items: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if not text:
                continue
            if len(text) > 200:
                raise ValueError("待确认事实过长")
            items.append(text)
        if len(items) > 20:
            raise ValueError("待确认事实过多")
        return items


class RiskHit(BaseModel):
    fragment: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=200)
    suggestion: str = Field(min_length=1, max_length=200)


class RiskResult(BaseModel):
    hits: list[RiskHit] = Field(default_factory=list, max_length=50)
    summary: str = Field(min_length=1, max_length=64)
    need_human_review: bool


class Out(BaseModel):
    id: int
    user_id: int
    input_asset_id: int
    platform: str
    product_facts: dict[str, Any] = Field(default_factory=dict)
    generated_content: GeneratedContent | None = None
    edited_content: GeneratedContent | None = None
    risk_result: RiskResult | None = None
    status: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    user_deleted_at: datetime | None = None

    model_config = {"from_attributes": True}


class ListOut(BaseModel):
    items: list[Out]


class PatchIn(BaseModel):
    edited_content: GeneratedContent
