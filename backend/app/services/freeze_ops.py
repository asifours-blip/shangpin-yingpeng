"""冻结 / 解冻事务。不 commit，由调用方提交。"""

from datetime import datetime, timezone

from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from app.models import Appeal, FreezeEvent, GenerationTask, User

QUEUED_STOPPED_REASON = "用户被冻结，排队任务停止"

_PROTECTED_USERNAMES = frozenset({"demo", "admin"})


def lock_user_for_update(db: Session, user_id: int) -> User:
    """锁定 users 行并刷新。用户不存在时抛 LookupError。"""
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError("用户不存在")
    db.refresh(user)
    return user


def freeze_user(db: Session, *, target_id: int, actor: User, reason: str) -> FreezeEvent:
    """冻结目标用户：写冻结事件、停掉 queued 任务。已冻结则幂等返回现有事件。"""
    reason = reason.strip()
    if not reason:
        raise ValueError("冻结原因不能为空")

    target = lock_user_for_update(db, target_id)

    if actor.id == target.id:
        raise ValueError("不能冻结自己")
    if target.role != "user" or target.username.lower() in _PROTECTED_USERNAMES:
        raise ValueError("不能冻结管理员或演示账号")

    if target.is_frozen:
        if target.current_freeze_event_id is None:
            raise LookupError("当前冻结事件不存在")
        event = db.get(FreezeEvent, target.current_freeze_event_id)
        if event is None:
            raise LookupError("当前冻结事件不存在")
        return event

    event = FreezeEvent(user_id=target.id, reason=reason, frozen_by_id=actor.id)
    db.add(event)
    db.flush()

    target.is_frozen = True
    target.current_freeze_event_id = event.id

    now = datetime.now(timezone.utc)
    db.execute(
        update(GenerationTask)
        .where(
            GenerationTask.user_id == target.id,
            GenerationTask.status == "queued",
        )
        .values(
            status="failed",
            error_message=QUEUED_STOPPED_REASON,
            finished_at=now,
        )
    )
    return event


def unfreeze_user(db: Session, *, target_id: int, actor: User) -> FreezeEvent | None:
    """解冻目标用户。未冻结则幂等返回当前事件或 None。保留 current_freeze_event_id。"""
    target = lock_user_for_update(db, target_id)

    if not target.is_frozen:
        if target.current_freeze_event_id is None:
            return None
        return db.get(FreezeEvent, target.current_freeze_event_id)

    now = datetime.now(timezone.utc)
    event: FreezeEvent | None = None
    if target.current_freeze_event_id is not None:
        event = db.get(FreezeEvent, target.current_freeze_event_id)

    target.is_frozen = False

    if event is not None:
        event.unfrozen_by_id = actor.id
        event.unfrozen_at = now
        db.execute(
            update(Appeal)
            .where(
                Appeal.freeze_event_id == event.id,
                Appeal.status == "pending",
            )
            .values(
                status="closed",
                admin_reply=case(
                    (
                        or_(Appeal.admin_reply.is_(None), Appeal.admin_reply == ""),
                        "账号已解冻",
                    ),
                    else_=Appeal.admin_reply,
                ),
                handled_by_id=actor.id,
                handled_at=now,
            )
        )

    return event
