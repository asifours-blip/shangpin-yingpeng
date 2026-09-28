"""管理员：用户 CRUD 状态、重置密码、全站生成记录（含核验图）。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.redis_client import redis_client
from app.core.security import hash_password
from app.models import Appeal, GenerationTask, GenerationTaskAsset, User
from app.schemas import GenerationReviewIn
from app.schemas.admin import (
    AdminAssetOut,
    AdminGenerationListOut,
    AdminGenerationOut,
    AdminResetPasswordIn,
    AdminResetPasswordOut,
    AdminUserCreate,
    AdminUserListOut,
    AdminUserOut,
    AdminUserStatusIn,
)
from app.schemas.freeze import AppealListOut, AppealOut
from app.services import storage
from app.services.freeze_ops import freeze_user, unfreeze_user
from app.services.review_notify import apply_review_notifications

router = APIRouter(prefix="/api/admin", tags=["admin"])

_ALLOWED_ROLES = {"user", "admin"}
_ASSET_ROLES = {"product", "scene", "input", "output"}
_APPEAL_STATUSES = {"pending", "closed"}


class AdminFreezeIn(BaseModel):
    """管理员冻结/解冻入参。冻结时 reason 去空白后必填。"""

    action: str
    reason: str | None = None


class AdminAppealPatchIn(BaseModel):
    """管理员关闭申诉。回复不自动解冻。"""

    status: str
    admin_reply: str


def require_admin(user: User = Depends(get_current_user)) -> User:
    """未登录由 get_current_user 返回 401；普通用户 403。"""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def destroy_user_sessions(user_id: int) -> None:
    """扫描 Redis session:* ，删掉该用户全部 Session，立即踢下线。"""
    uid = str(user_id)
    stale: list[str] = []
    for key in redis_client.scan_iter(match="session:*", count=200):
        if redis_client.get(key) == uid:
            stale.append(key)
    if stale:
        redis_client.delete(*stale)


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


def _generation_load_options():
    return (
        joinedload(GenerationTask.user),
        selectinload(GenerationTask.assets).selectinload(GenerationTaskAsset.asset),
    )


def _legacy_unlabeled(task: GenerationTask) -> bool:
    roles = {link.role for link in (task.assets or [])}
    return "input" in roles and "product" not in roles and "scene" not in roles


def _assets_for_task(task: GenerationTask) -> list[AdminAssetOut]:
    """只返回挂在该任务上的资产；预签名在管理员鉴权后签发。"""
    out: list[AdminAssetOut] = []
    for link in sorted(task.assets or [], key=lambda x: (x.position, x.id)):
        # 关系已限定 task_id；再防脏数据：link 必须指向本任务且资产存在
        if link.task_id != task.id or link.asset is None:
            continue
        if link.role not in _ASSET_ROLES:
            continue
        url: str | None = None
        try:
            url = storage.presigned_url(link.asset.object_key)
        except Exception:
            url = None
        out.append(
            AdminAssetOut(
                id=link.asset_id,
                role=link.role,
                url=url,
                file_path=f"/api/assets/{link.asset_id}/file",
                mime=link.asset.mime if link.asset is not None else None,
            )
        )
    return out


def _to_admin_generation(task: GenerationTask) -> AdminGenerationOut:
    kwargs = {
        "id": task.id,
        "user_id": task.user_id,
        "username": task.user.username if task.user is not None else "",
        "mode": task.mode,
        "status": task.status,
        "prompt": task.prompt,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "finished_at": task.finished_at,
        "assets": _assets_for_task(task),
        "review_status": task.review_status or "unreviewed",
        "review_reason": task.review_reason,
        "reviewed_at": task.reviewed_at,
        "reviewed_by_id": task.reviewed_by_id,
        "reverse_op_id": task.reverse_op_id,
        "optimize_op_id": task.optimize_op_id,
        "legacy_unlabeled": _legacy_unlabeled(task),
        "user_deleted_at": task.user_deleted_at,
        "user_deleted_by": task.user_deleted_by,
    }
    try:
        return AdminGenerationOut(**kwargs)
    except (TypeError, ValidationError):
        kwargs.pop("user_deleted_at", None)
        kwargs.pop("user_deleted_by", None)
        return AdminGenerationOut(**kwargs)


@router.get("/users", response_model=AdminUserListOut)
def list_users(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserListOut:
    rows = db.scalars(select(User).order_by(User.id.asc())).all()
    return AdminUserListOut(items=[AdminUserOut.model_validate(u) for u in rows])


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: AdminUserCreate,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名不能为空")
    if body.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="角色只能是 user 或 admin")
    exists = db.scalar(select(User.id).where(User.username == username))
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    user = User(
        username=username,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from None
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/status", response_model=AdminUserOut)
def patch_user_status(
    user_id: int,
    body: AdminUserStatusIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id and not body.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能禁用当前登录账号")
    user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    if not user.is_active:
        destroy_user_sessions(user.id)
    return user


@router.post("/users/{user_id}/reset-password", response_model=AdminResetPasswordOut)
def reset_password(
    user_id: int,
    body: AdminResetPasswordIn,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminResetPasswordOut:
    user = _get_user_or_404(db, user_id)
    user.password_hash = hash_password(body.password)
    db.commit()
    # 旧密码失效的同时踢掉旧 Session，必须重新登录
    destroy_user_sessions(user.id)
    return AdminResetPasswordOut(ok=True)


@router.patch("/users/{user_id}/freeze", response_model=AdminUserOut)
def patch_user_freeze(
    user_id: int,
    body: AdminFreezeIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    """冻结/解冻普通用户。不清 Session；已冻再冻 / 未冻再解冻由服务层幂等。"""
    action = (body.action or "").strip()
    if action not in {"freeze", "unfreeze"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action 只能是 freeze 或 unfreeze")
    try:
        if action == "freeze":
            reason = (body.reason or "").strip()
            if not reason:
                raise ValueError("冻结原因不能为空")
            freeze_user(db, target_id=user_id, actor=admin, reason=reason)
        else:
            unfreeze_user(db, target_id=user_id, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "用户不存在",
        ) from None
    db.commit()
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


@router.get("/appeals", response_model=AppealListOut)
def list_admin_appeals(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    appeal_status: str | None = Query("pending", alias="status"),
) -> AppealListOut:
    """管理员看申诉。默认 pending；Query status 可选。新的在前。"""
    q = select(Appeal)
    if appeal_status is not None and appeal_status.strip():
        wanted = appeal_status.strip()
        if wanted not in _APPEAL_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status 只能是 pending 或 closed",
            )
        q = q.where(Appeal.status == wanted)
    else:
        q = q.where(Appeal.status == "pending")
    rows = db.scalars(q.order_by(Appeal.created_at.desc(), Appeal.id.desc())).all()
    return AppealListOut(items=[AppealOut.model_validate(r) for r in rows])


@router.patch("/appeals/{appeal_id}", response_model=AppealOut)
def patch_appeal(
    appeal_id: int,
    body: AdminAppealPatchIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Appeal:
    """关闭申诉并写回复。不自动解冻，也不改 users.is_frozen。非 pending 再关返回 400。"""
    new_status = (body.status or "").strip()
    if new_status != "closed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status 只能是 closed")
    reply = (body.admin_reply or "").strip()
    if not reply:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="管理员回复不能为空")

    appeal = db.get(Appeal, appeal_id)
    if appeal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="申诉不存在")
    if appeal.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="申诉已处理，不能重复关闭")

    appeal.status = "closed"
    appeal.admin_reply = reply
    appeal.handled_by_id = admin.id
    appeal.handled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(appeal)
    return appeal


@router.get("/generations", response_model=AdminGenerationListOut)
def list_all_generations(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
) -> AdminGenerationListOut:
    rows = db.scalars(
        select(GenerationTask)
        .options(*_generation_load_options())
        .order_by(GenerationTask.created_at.desc())
        .limit(limit)
    ).unique().all()
    return AdminGenerationListOut(items=[_to_admin_generation(task) for task in rows])


@router.get("/generations/{task_id}", response_model=AdminGenerationOut)
def get_generation_detail(
    task_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminGenerationOut:
    """管理端单条核验；不存在 404。资产仅返回挂在该任务上的。"""
    task = db.scalar(
        select(GenerationTask)
        .options(*_generation_load_options())
        .where(GenerationTask.id == task_id)
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return _to_admin_generation(task)


_REVIEW_STATUSES = {"unreviewed", "usable", "needs_revision"}
_DECISION_STATUSES = {"usable", "needs_revision"}


@router.patch("/generations/{task_id}/review", response_model=AdminGenerationOut)
def review_generation(
    task_id: int,
    body: GenerationReviewIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminGenerationOut:
    """管理员标记人工决定。仅 succeeded 可标可用/需修订。"""
    review_status = (body.review_status or "").strip()
    if review_status not in _REVIEW_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="review_status 只能是 unreviewed / usable / needs_revision",
        )
    task = db.scalar(
        select(GenerationTask)
        .options(*_generation_load_options())
        .where(GenerationTask.id == task_id)
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if review_status in _DECISION_STATUSES and task.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅成功的任务可标记为可用或需修订",
        )
    if review_status == "needs_revision" and not (body.review_reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="管理员打回必须填写原因",
        )
    previous_status = task.review_status or "unreviewed"
    previous_reason = task.review_reason
    task.review_status = review_status
    task.review_reason = body.review_reason
    task.reviewed_at = datetime.now(timezone.utc)
    task.reviewed_by_id = admin.id
    try:
        apply_review_notifications(
            db,
            task=task,
            actor=admin,
            previous_status=previous_status,
            previous_reason=previous_reason,
            new_status=review_status,
            new_reason=body.review_reason,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    db.commit()
    task = db.scalar(
        select(GenerationTask)
        .options(*_generation_load_options())
        .where(GenerationTask.id == task_id)
    )
    assert task is not None
    return _to_admin_generation(task)
