"""管理员打回产生的待处理通知。

调用方须已锁任务行并改好审核字段后再调用。本模块不改 task 的审核字段。
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import GenerationTask, Notification, User


def _normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    stripped = reason.strip()
    return stripped if stripped else None


def _get_pending(db: Session, user_id: int, task_id: int) -> Notification | None:
    return db.scalar(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.task_id == task_id,
            Notification.status == "pending",
        )
    )


def _insert_pending(db: Session, user_id: int, task_id: int, reason: str) -> None:
    # 先把调用方已改的审核字段刷进外层事务，避免插入冲突回滚时带走它们。
    db.flush()
    row = Notification(
        user_id=user_id,
        task_id=task_id,
        reason=reason,
        status="pending",
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        if row in db:
            db.expunge(row)
        pending = _get_pending(db, user_id, task_id)
        if pending is not None:
            pending.reason = reason


def close_pending_for_task(db: Session, user_id: int, task_id: int) -> None:
    """将该 user+task 下 pending 通知全部标 handled，不删行、不改审核。"""
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.task_id == task_id,
            Notification.status == "pending",
        )
    ).all()
    for row in rows:
        row.status = "handled"
        row.handled_at = now


def list_pending_for_user(db: Session, user_id: int) -> list[Notification]:
    """本人 pending 通知，新的在前。"""
    return list(
        db.scalars(
            select(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.status == "pending",
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
        ).all()
    )


def apply_review_notifications(
    db: Session,
    *,
    task: GenerationTask,
    actor: User,
    previous_status: str,
    previous_reason: str | None,
    new_status: str,
    new_reason: str | None,
) -> None:
    """按审核结果创建或关闭管理员打回通知。不改 task.review_status。"""
    if actor.role != "admin":
        return

    if new_status == "needs_revision":
        reason = _normalize_reason(new_reason)
        if not reason:
            raise ValueError("管理员打回必须填写原因")
        pending = _get_pending(db, task.user_id, task.id)
        if pending is not None:
            pending.reason = reason
            return
        if (
            previous_status == "needs_revision"
            and _normalize_reason(previous_reason) == reason
        ):
            return
        _insert_pending(db, task.user_id, task.id, reason)
        return

    if new_status in {"usable", "unreviewed"}:
        close_pending_for_task(db, task.user_id, task.id)
