"""生成任务：创建、查询、本人历史、审核、软删。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import ACCOUNT_FROZEN_DETAIL, require_unfrozen
from app.core.db import get_db
from app.models import GenerationTask, GenerationTaskAsset, ImageAsset, PromptOperation, User
from app.schemas import (
    AssetOut,
    GenerationCreate,
    GenerationListOut,
    GenerationOut,
    GenerationReviewIn,
)
from app.services import storage
from app.services.ark import ALLOWED_SIZES
from app.services.freeze_ops import lock_user_for_update
from app.services.review_notify import apply_review_notifications

router = APIRouter(prefix="/api/generations", tags=["generations"])

_REVIEW_STATUSES = {"unreviewed", "usable", "needs_revision"}
_DECISION_STATUSES = {"usable", "needs_revision"}
_TERMINAL_STATUSES = {"succeeded", "failed", "unknown"}


def _legacy_unlabeled(task: GenerationTask) -> bool:
    """旧行仅有 role=input、无 product/scene 时标记为未标注。"""
    roles = {link.role for link in (task.assets or [])}
    return "input" in roles and "product" not in roles and "scene" not in roles


def _to_out(task: GenerationTask) -> GenerationOut:
    assets: list[AssetOut] = []
    for link in sorted(task.assets, key=lambda x: x.position):
        url = None
        mime = None
        size_bytes = None
        width = None
        height = None
        if link.asset is not None:
            mime = link.asset.mime
            size_bytes = link.asset.size_bytes
            width = link.asset.width
            height = link.asset.height
            try:
                url = storage.presigned_url(link.asset.object_key)
            except Exception:
                url = None
        assets.append(
            AssetOut(
                id=link.asset_id,
                role=link.role,
                url=url,
                mime=mime,
                size_bytes=size_bytes,
                width=width,
                height=height,
            )
        )
    return GenerationOut(
        id=task.id,
        mode=task.mode,
        prompt=task.prompt,
        params=task.params or {},
        status=task.status,
        error_message=task.error_message,
        retry_of_id=task.retry_of_id,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        assets=assets,
        review_status=task.review_status or "unreviewed",
        review_reason=task.review_reason,
        reviewed_at=task.reviewed_at,
        reviewed_by_id=task.reviewed_by_id,
        reverse_op_id=task.reverse_op_id,
        optimize_op_id=task.optimize_op_id,
        legacy_unlabeled=_legacy_unlabeled(task),
    )


def _load_query():
    return select(GenerationTask).options(
        selectinload(GenerationTask.assets).selectinload(GenerationTaskAsset.asset)
    )


def _require_owned_asset(db: Session, asset_id: int, user_id: int) -> ImageAsset:
    asset = db.get(ImageAsset, asset_id)
    if asset is None or asset.owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="参考图不存在或不属于当前用户",
        )
    return asset


def _require_owned_op(
    db: Session,
    op_id: int,
    user_id: int,
    expected_type: str,
) -> PromptOperation:
    op = db.get(PromptOperation, op_id)
    if op is None or op.user_id != user_id or op.op_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"来源操作不存在、不属于当前用户或类型不是 {expected_type}",
        )
    return op


def _resolve_i2i_assets(body: GenerationCreate) -> tuple[int, int | None]:
    """优先 product/scene；兼容 input_asset_ids（[0] 商品 [1] 场景）。"""
    has_named = body.product_asset_id is not None or body.scene_asset_id is not None
    if has_named:
        if body.product_asset_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="参考图生图至少需要 1 张商品图",
            )
        product_id = body.product_asset_id
        scene_id = body.scene_asset_id
        if scene_id is not None and scene_id == product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="参考图不能重复",
            )
        return product_id, scene_id

    input_ids = list(body.input_asset_ids or [])
    if len(input_ids) != len(set(input_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="参考图不能重复",
        )
    if not (1 <= len(input_ids) <= 2):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="参考图生图需要 1 或 2 张本人图片",
        )
    product_id = input_ids[0]
    scene_id = input_ids[1] if len(input_ids) > 1 else None
    return product_id, scene_id


@router.post("", response_model=GenerationOut)
def create_generation(
    body: GenerationCreate,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> GenerationOut:
    if body.mode not in {"t2i", "i2i", "i2v"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持文生图（t2i）、参考图生图（i2i）和图生视频（i2v）",
        )
    if body.mode != "i2v" and body.size not in ALLOWED_SIZES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"仅开放已验证尺寸：{sorted(ALLOWED_SIZES)}",
        )

    product_id: int | None = None
    scene_id: int | None = None
    has_any_ref = bool(body.input_asset_ids) or body.product_asset_id is not None or body.scene_asset_id is not None

    if body.mode == "t2i":
        if has_any_ref:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="文生图不需要参考图",
            )
    elif body.mode == "i2v":
        if not has_any_ref:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="图生视频需要 1 张商品图",
            )
        product_id, scene_id = _resolve_i2i_assets(body)
        if scene_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="图生视频只需要 1 张商品图",
            )
        _require_owned_asset(db, product_id, user.id)
    else:
        product_id, scene_id = _resolve_i2i_assets(body)
        _require_owned_asset(db, product_id, user.id)
        if scene_id is not None:
            _require_owned_asset(db, scene_id, user.id)

    reverse_op_id = body.reverse_op_id
    optimize_op_id = body.optimize_op_id
    if reverse_op_id is not None:
        _require_owned_op(db, reverse_op_id, user.id, "reverse")
    if optimize_op_id is not None:
        _require_owned_op(db, optimize_op_id, user.id, "optimize")

    if body.mode == "i2v":
        params: dict = {
            "duration": 5,
            "resolution": "480p",
            "ratio": "16:9",
            "watermark": False,
            "model": "ARK_VIDEO_ENDPOINT",
        }
    else:
        params = {
            "size": body.size,
            "watermark": False,
            "output_format": "png",
            "response_format": "url",
            "model": "ARK_IMAGE_ENDPOINT",
        }
        if body.keep_features is not None:
            params["keep_features"] = body.keep_features

    # 入队与冻结抢同一 users 行，不信任 Depends 里的旧 user 对象
    try:
        locked = lock_user_for_update(db, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用") from None
    if locked.is_frozen:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ACCOUNT_FROZEN_DETAIL,
        )

    if body.retry_of_id is not None:
        origin = db.get(GenerationTask, body.retry_of_id)
        if origin is None or origin.user_id != locked.id or origin.user_deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="原任务不存在")

    task = GenerationTask(
        user_id=locked.id,
        mode=body.mode,
        prompt=body.prompt.strip(),
        params=params,
        status="queued",
        retry_of_id=body.retry_of_id,
        reverse_op_id=reverse_op_id,
        optimize_op_id=optimize_op_id,
        review_status="unreviewed",
    )
    db.add(task)
    db.flush()

    if body.mode in {"i2i", "i2v"} and product_id is not None:
        db.add(
            GenerationTaskAsset(
                task_id=task.id,
                asset_id=product_id,
                role="product",
                position=0,
            )
        )
        if scene_id is not None:
            db.add(
                GenerationTaskAsset(
                    task_id=task.id,
                    asset_id=scene_id,
                    role="scene",
                    position=1,
                )
            )

    db.commit()
    task = db.scalar(_load_query().where(GenerationTask.id == task.id))
    assert task is not None
    return _to_out(task)


@router.get("", response_model=GenerationListOut)
def list_generations(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
) -> GenerationListOut:
    rows = db.scalars(
        _load_query()
        .where(
            GenerationTask.user_id == user.id,
            GenerationTask.user_deleted_at.is_(None),
        )
        .order_by(GenerationTask.created_at.desc())
        .limit(limit)
    ).all()
    return GenerationListOut(items=[_to_out(t) for t in rows])


@router.get("/{task_id}", response_model=GenerationOut)
def get_generation(
    task_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> GenerationOut:
    task = db.scalar(
        _load_query().where(
            GenerationTask.id == task_id,
            GenerationTask.user_id == user.id,
            GenerationTask.user_deleted_at.is_(None),
        )
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return _to_out(task)


@router.delete("/{task_id}", response_model=GenerationOut)
def delete_generation(
    task_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> GenerationOut:
    """本人软删终态任务；重复删除幂等 200。不碰 MinIO。"""
    task = db.scalar(
        _load_query().where(GenerationTask.id == task_id, GenerationTask.user_id == user.id)
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task.user_deleted_at is not None:
        return _to_out(task)
    if task.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="排队中或生成中的任务不可删除",
        )
    task.user_deleted_at = datetime.now(timezone.utc)
    task.user_deleted_by = user.id
    db.commit()
    task = db.scalar(_load_query().where(GenerationTask.id == task.id))
    assert task is not None
    return _to_out(task)


@router.patch("/{task_id}/review", response_model=GenerationOut)
def review_generation(
    task_id: int,
    body: GenerationReviewIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> GenerationOut:
    """owner 可审本人；admin 可审他人。普通用户看他人任务一律 404，不泄露。"""
    review_status = (body.review_status or "").strip()
    if review_status not in _REVIEW_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="review_status 只能是 unreviewed / usable / needs_revision",
        )

    task = db.scalar(_load_query().where(GenerationTask.id == task_id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    is_owner = task.user_id == user.id
    is_admin = user.role == "admin"
    if not is_owner and not is_admin:
        # 不向普通用户泄露他人任务是否存在
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    # 已删走 owner 语义 404；admin 审他人请走 admin.py，这里不开后门
    if task.user_deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    if review_status in _DECISION_STATUSES and task.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅成功的任务可标记为可用或需修订",
        )

    if is_admin and review_status == "needs_revision":
        if not (body.review_reason or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="管理员打回必须填写原因",
            )

    previous_status = task.review_status
    previous_reason = task.review_reason

    task.review_status = review_status
    task.review_reason = body.review_reason
    task.reviewed_at = datetime.now(timezone.utc)
    task.reviewed_by_id = user.id
    try:
        apply_review_notifications(
            db,
            task=task,
            actor=user,
            previous_status=previous_status,
            previous_reason=previous_reason,
            new_status=review_status,
            new_reason=body.review_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    db.commit()

    task = db.scalar(_load_query().where(GenerationTask.id == task.id))
    assert task is not None
    return _to_out(task)
