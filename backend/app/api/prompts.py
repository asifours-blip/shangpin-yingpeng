"""Prompt 优化与图片反推。请求内同步调方舟 Chat，禁止 Mock；成功必落库。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_unfrozen
from app.core.db import get_db
from app.models import ImageAsset, PromptOperation, User
from app.schemas import (
    AssetOut,
    PromptOperationListOut,
    PromptOperationOut,
    PromptOptimizeIn,
    PromptOut,
    PromptReverseIn,
)
from app.services import storage
from app.services.ark import (
    MIN_IMAGE_SIDE,
    ArkError,
    bytes_to_data_url,
    chat_text,
    chat_vision,
)
from app.services.image_dims import image_dimensions
from app.services.prompt_templates import OPTIMIZE_SYSTEM, REVERSE_USER, reverse_system_prompt

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


def _http_from_ark(exc: ArkError) -> HTTPException:
    """方舟错误如实返回；不要把上游 401 误当成未登录。"""
    code = exc.status_code or status.HTTP_502_BAD_GATEWAY
    if code == status.HTTP_401_UNAUTHORIZED or code < 400 or code >= 500:
        code = status.HTTP_502_BAD_GATEWAY
    return HTTPException(status_code=code, detail=exc.message)


def _save_op(
    db: Session,
    *,
    user_id: int,
    op_type: str,
    input_text: str | None = None,
    input_asset_id: int | None = None,
    input_object_key: str | None = None,
    output_prompt: str | None = None,
    status_value: str = "succeeded",
    error_message: str | None = None,
) -> None:
    db.add(
        PromptOperation(
            user_id=user_id,
            op_type=op_type,
            input_text=input_text,
            input_asset_id=input_asset_id,
            input_object_key=input_object_key,
            output_prompt=output_prompt,
            status=status_value,
            error_message=error_message,
        )
    )
    db.commit()


def _save_failed(db: Session, **kwargs) -> None:
    """失败记录尽量落；落库失败不影响把上游错误返回给前端。"""
    try:
        _save_op(db, status_value="failed", **kwargs)
    except Exception:
        db.rollback()


def _to_op_out(row: PromptOperation) -> PromptOperationOut:
    asset_out = None
    if row.asset is not None:
        url = None
        try:
            url = storage.presigned_url(row.asset.object_key)
        except Exception:
            url = None
        asset_out = AssetOut(
            id=row.asset.id,
            role="input",
            url=url,
            mime=row.asset.mime,
            size_bytes=row.asset.size_bytes,
            width=row.asset.width,
            height=row.asset.height,
        )
    return PromptOperationOut(
        id=row.id,
        op_type=row.op_type,
        input_text=row.input_text,
        input_asset_id=row.input_asset_id,
        input_object_key=row.input_object_key,
        output_prompt=row.output_prompt,
        status=row.status,
        error_message=row.error_message,
        created_at=row.created_at,
        asset=asset_out,
    )


@router.get("/operations", response_model=PromptOperationListOut)
def list_prompt_operations(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
) -> PromptOperationListOut:
    rows = db.scalars(
        select(PromptOperation)
        .options(selectinload(PromptOperation.asset))
        .where(PromptOperation.user_id == user.id)
        .order_by(PromptOperation.created_at.desc())
        .limit(limit)
    ).all()
    return PromptOperationListOut(items=[_to_op_out(r) for r in rows])


@router.post("/optimize", response_model=PromptOut)
def optimize_prompt(
    body: PromptOptimizeIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> PromptOut:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请填写要优化的 Prompt")
    try:
        prompt = chat_text(OPTIMIZE_SYSTEM, text)
    except ArkError as exc:
        _save_failed(
            db,
            user_id=user.id,
            op_type="optimize",
            input_text=text,
            error_message=exc.message,
        )
        raise _http_from_ark(exc) from exc
    _save_op(
        db,
        user_id=user.id,
        op_type="optimize",
        input_text=text,
        output_prompt=prompt,
        status_value="succeeded",
    )
    return PromptOut(prompt=prompt)


@router.post("/reverse", response_model=PromptOut)
def reverse_prompt(
    body: PromptReverseIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> PromptOut:
    asset = db.get(ImageAsset, body.asset_id)
    if asset is None or asset.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="图片不存在或不属于当前用户",
        )
    object_key = asset.object_key
    asset_id = asset.id
    try:
        raw = storage.get_bytes(object_key)
    except S3Error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图片不存在")
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片为空")

    dims = image_dimensions(raw)
    if dims is not None:
        width, height = dims
        if min(width, height) < MIN_IMAGE_SIDE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"图片最短边不足 {MIN_IMAGE_SIDE} 像素（当前 {width}×{height}），无法反推",
            )

    data_url = bytes_to_data_url(raw, asset.mime)
    try:
        prompt = chat_vision(reverse_system_prompt(), REVERSE_USER, data_url)
    except ArkError as exc:
        _save_failed(
            db,
            user_id=user.id,
            op_type="reverse",
            input_asset_id=asset_id,
            input_object_key=object_key,
            error_message=exc.message,
        )
        raise _http_from_ark(exc) from exc
    _save_op(
        db,
        user_id=user.id,
        op_type="reverse",
        input_asset_id=asset_id,
        input_object_key=object_key,
        output_prompt=prompt,
        status_value="succeeded",
    )
    return PromptOut(prompt=prompt)
