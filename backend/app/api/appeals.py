"""本人申诉。未冻结可读历史；提交必须当前仍冻结。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import Appeal, FreezeEvent, User
from app.schemas.freeze import AppealCreate, AppealListOut, AppealOut
from app.services.freeze_ops import lock_user_for_update

router = APIRouter(prefix="/api/appeals", tags=["appeals"])


@router.get("", response_model=AppealListOut)
def list_appeals(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AppealListOut:
    """本人申诉列表，新的在前。未冻结也可读历史。"""
    rows = db.scalars(
        select(Appeal)
        .where(Appeal.user_id == user.id)
        .order_by(Appeal.created_at.desc(), Appeal.id.desc())
    ).all()
    return AppealListOut(items=[AppealOut.model_validate(r) for r in rows])


@router.post("", response_model=AppealOut, status_code=status.HTTP_201_CREATED)
def create_appeal(
    body: AppealCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Appeal:
    """提交申诉。须当前仍冻结且冻结事件有效；同一事件仅允许一条 pending。"""
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="申诉内容不能为空")

    try:
        locked = lock_user_for_update(db, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用") from None

    if not locked.is_frozen or locked.current_freeze_event_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前未冻结，不能申诉",
        )

    event = db.get(FreezeEvent, locked.current_freeze_event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前未冻结，不能申诉",
        )

    existing = db.scalar(
        select(Appeal.id).where(
            Appeal.freeze_event_id == event.id,
            Appeal.status == "pending",
        )
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已有待处理申诉")

    appeal = Appeal(
        user_id=locked.id,
        freeze_event_id=event.id,
        content=content,
        status="pending",
    )
    db.add(appeal)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已有待处理申诉") from None
    db.refresh(appeal)
    return appeal
