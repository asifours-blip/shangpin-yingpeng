"""冻结申诉入参 / 出参。管理员 PATCH 在 Wave 2 admin.py。"""

from datetime import datetime

from pydantic import BaseModel, Field


class AppealCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class AppealOut(BaseModel):
    id: int
    user_id: int
    freeze_event_id: int
    content: str
    status: str
    admin_reply: str | None
    handled_by_id: int | None
    handled_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AppealListOut(BaseModel):
    items: list[AppealOut]
