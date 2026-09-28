"""管理员接口入参 / 出参。"""

from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    role: str = "user"


class AdminUserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    is_frozen: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserListOut(BaseModel):
    items: list[AdminUserOut]


class AdminUserStatusIn(BaseModel):
    is_active: bool


class AdminResetPasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class AdminResetPasswordOut(BaseModel):
    ok: bool


class AdminAssetOut(BaseModel):
    """管理端核验用最小资产：id + 角色 + 鉴权后预签名预览。"""

    id: int
    role: str  # product | scene | input | output
    url: str | None = None  # 管理员接口鉴权后签发；勿走用户 /url
    file_path: str  # 同源鉴权流出图：/api/assets/{id}/file（owner|admin）
    mime: str | None = None


class AdminGenerationOut(BaseModel):
    id: int
    user_id: int
    username: str
    mode: str
    status: str
    prompt: str
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    assets: list[AdminAssetOut] = []
    review_status: str = "unreviewed"
    review_reason: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by_id: int | None = None
    reverse_op_id: int | None = None
    optimize_op_id: int | None = None
    legacy_unlabeled: bool = False
    user_deleted_at: datetime | None = None
    user_deleted_by: int | None = None


class AdminGenerationListOut(BaseModel):
    items: list[AdminGenerationOut]
