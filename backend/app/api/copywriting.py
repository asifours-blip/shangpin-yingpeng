"""独立营销文案接口。同步 HTTP 仍给工作台用；生成本身在 copywriting_generator。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ACCOUNT_FROZEN_DETAIL, get_current_user, require_unfrozen
from app.core.db import get_db
from app.models import CopywritingOperation, ImageAsset, User
from app.schemas.copywriting import (
    CreateIn,
    GeneratedContent,
    ListOut,
    Out,
    PatchIn,
    RiskResult,
)
from app.services import storage
from app.services.ark import bytes_to_data_url
from app.services.copywriting_generator import produce_copy, sanitize_error
from app.services.risk_lexicon import check_content

router = APIRouter(prefix="/api/copywriting", tags=["copywriting"])
admin_router = APIRouter(prefix="/api/admin/copywriting", tags=["admin-copywriting"])

# 与上传接口常见图片类型对齐；归属校验过后再看 mime。
_ALLOWED_MIME = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/bmp",
    "image/gif",
}


def _require_admin(user: User = Depends(get_current_user)) -> User:
    """本模块自带管理员检查，避免 import admin.py 形成环。"""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def _to_out(row: CopywritingOperation) -> Out:
    return Out(
        id=row.id,
        user_id=row.user_id,
        input_asset_id=row.input_asset_id,
        platform=row.platform,
        product_facts=row.product_facts or {},
        generated_content=_maybe_content(row.generated_content),
        edited_content=_maybe_content(row.edited_content),
        risk_result=_maybe_risk(row.risk_result),
        status=row.status,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        user_deleted_at=row.user_deleted_at,
    )


def _maybe_content(data: object) -> GeneratedContent | None:
    if not data:
        return None
    try:
        return GeneratedContent.model_validate(data)
    except ValidationError:
        return None


def _maybe_risk(data: object) -> RiskResult | None:
    if not data:
        return None
    try:
        return RiskResult.model_validate(data)
    except ValidationError:
        return None


def _product_facts(body: CreateIn) -> dict:
    facts: dict[str, str] = {}
    if body.product_name:
        facts["product_name"] = body.product_name
    if body.selling_points:
        facts["selling_points"] = body.selling_points
    if body.campaign:
        facts["campaign"] = body.campaign
    return facts


def _fail_http(op_id: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"operation_id": op_id, "message": message},
    )


def _commit_failed(db: Session, op_id: int, message: str) -> None:
    """失败必须 commit。禁止未落库就抛错把 processing 卷走。"""
    try:
        row = db.get(CopywritingOperation, op_id)
        if row is None or row.status != "processing":
            return
        row.status = "failed"
        row.error_message = message
        db.commit()
    except Exception:
        db.rollback()


def _load_owned(db: Session, op_id: int, user_id: int) -> CopywritingOperation:
    row = db.get(CopywritingOperation, op_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文案记录不存在")
    return row


@router.post("", response_model=Out)
def create_copywriting(
    body: CreateIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> Out:
    asset = db.get(ImageAsset, body.asset_id)
    if asset is None or asset.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="图片不存在或不属于当前用户",
        )
    mime = (asset.mime or "").split(";")[0].strip().lower()
    if mime not in _ALLOWED_MIME:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持常见图片格式")

    facts = _product_facts(body)
    object_key = asset.object_key
    mime_for_url = asset.mime
    user_id = user.id

    # 先落 processing 并 commit，不要拖着事务等方舟。
    row = CopywritingOperation(
        user_id=user_id,
        input_asset_id=asset.id,
        platform=body.platform,
        product_facts=facts,
        status="processing",
    )
    db.add(row)
    db.commit()
    op_id = row.id
    platform = row.platform

    try:
        raw = storage.get_bytes(object_key)
        if not raw:
            raise ValueError("图片为空")
        data_url = bytes_to_data_url(raw, mime_for_url)
        content, risk = produce_copy(platform=platform, facts=facts, data_url=data_url)
    except Exception as exc:
        message = sanitize_error(exc)
        _commit_failed(db, op_id, message)
        raise _fail_http(op_id, message) from exc

    row = db.get(CopywritingOperation, op_id)
    if row is None:
        raise _fail_http(op_id, "文案记录丢失")
    row.status = "succeeded"
    row.generated_content = content.model_dump()
    row.risk_result = risk.model_dump()
    row.error_message = None
    db.commit()
    db.refresh(row)

    # 生成过程中若被冻结：记录留档，正文不回给冻结用户。
    frozen = db.scalar(select(User.is_frozen).where(User.id == user_id))
    if frozen:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ACCOUNT_FROZEN_DETAIL,
        )
    return _to_out(row)


@router.get("", response_model=ListOut)
def list_copywriting(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
) -> ListOut:
    rows = db.scalars(
        select(CopywritingOperation)
        .where(
            CopywritingOperation.user_id == user.id,
            CopywritingOperation.user_deleted_at.is_(None),
        )
        .order_by(CopywritingOperation.created_at.desc())
        .limit(limit)
    ).all()
    # processing 原样返回，不伪造成功。
    return ListOut(items=[_to_out(row) for row in rows])


@router.patch("/{op_id}", response_model=Out)
def patch_copywriting(
    op_id: int,
    body: PatchIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> Out:
    row = _load_owned(db, op_id, user.id)
    if row.user_deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文案记录不存在")
    if row.status == "processing":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="生成中的记录不可编辑")
    if row.status != "succeeded":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅成功的文案可编辑")

    row.edited_content = body.edited_content.model_dump()
    row.risk_result = check_content(body.edited_content).model_dump()
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{op_id}", response_model=Out)
def delete_copywriting(
    op_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> Out:
    row = _load_owned(db, op_id, user.id)
    if row.status == "processing" and row.user_deleted_at is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="生成中的记录不可删除")
    if row.user_deleted_at is not None:
        return _to_out(row)
    row.user_deleted_at = datetime.now(timezone.utc)
    row.user_deleted_by = user.id
    db.commit()
    db.refresh(row)
    return _to_out(row)


@admin_router.get("", response_model=ListOut)
def admin_list_copywriting(
    _admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ListOut:
    """管理员看文案记录（含已删）。不要伪装成图片生成任务。"""
    rows = db.scalars(
        select(CopywritingOperation)
        .order_by(CopywritingOperation.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return ListOut(items=[_to_out(row) for row in rows])
