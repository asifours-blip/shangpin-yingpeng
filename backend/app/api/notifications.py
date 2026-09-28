"""本人打回通知。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_unfrozen
from app.core.db import get_db
from app.models import Notification, User
from app.schemas.notification import NotificationListOut, NotificationOut
from app.services.review_notify import list_pending_for_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListOut)
def list_my_notifications(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> NotificationListOut:
    items = list_pending_for_user(db, user.id)
    return NotificationListOut(items=items)


@router.patch("/{notification_id}/handle", response_model=NotificationOut)
def handle_notification(
    notification_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> Notification:
    row = db.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    if row.status != "handled":
        row.status = "handled"
        row.handled_at = datetime.now(timezone.utc)
        db.commit()
    return row
