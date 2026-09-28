"""演示社交账号 / 联系人入参与出参。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SocialAccountConnectIn(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    external_account_id: str = Field(min_length=1, max_length=64)


class SocialAccountStatusIn(BaseModel):
    status: str = Field(min_length=1, max_length=16)


class SocialAccountOut(BaseModel):
    id: int
    platform: str
    external_account_id: str
    display_name: str
    status: str
    data_source: str
    last_synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class SocialAccountListOut(BaseModel):
    items: list[SocialAccountOut]


class SocialContactOut(BaseModel):
    id: int
    account_id: int
    external_user_id: str
    nickname: str
    avatar_url: str | None = None
    remark: str | None = None
    tags: list[Any] = Field(default_factory=list)
    data_source: str

    model_config = {"from_attributes": True}


class SocialContactListOut(BaseModel):
    items: list[SocialContactOut]


class SocialContactAggOut(BaseModel):
    """聚合列表：联系人字段 + 来源账号 platform / display_name / external_account_id。"""

    id: int
    account_id: int
    platform: str
    display_name: str
    external_account_id: str
    external_user_id: str
    nickname: str
    avatar_url: str | None = None
    remark: str | None = None
    tags: list[Any] = Field(default_factory=list)
    data_source: str


class SocialContactAggListOut(BaseModel):
    items: list[SocialContactAggOut]


class SocialContactPatchIn(BaseModel):
    remark: str | None = Field(default=None, max_length=256)
    tags: list[str] | None = None


class SocialSyncOut(BaseModel):
    synced: int
