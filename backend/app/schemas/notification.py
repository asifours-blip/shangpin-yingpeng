"""本人打回通知出参。"""

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    task_id: int
    reason: str
    status: str
    created_at: datetime
    handled_at: datetime | None = None

    model_config = {"from_attributes": True}


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
