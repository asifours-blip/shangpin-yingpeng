"""图片上传与临时访问地址。"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_unfrozen
from app.core.config import settings
from app.core.db import get_db
from app.models import GenerationTask, GenerationTaskAsset, ImageAsset, User
from app.services import storage

router = APIRouter(prefix="/api/assets", tags=["assets"])

ALLOWED_MIME = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
    "image/gif",
}

MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
}


def _owner_output_soft_deleted(db: Session, asset_id: int, user_id: int) -> bool:
    """Owner 软删闸门：仅作为本人已删任务的 output、且无未删引用时拒绝。

    无任务关联（纯上传原图）或存在未删任务的任何引用（product/scene/input/output）则放行。
    不物理删除 MinIO。管理员不走此闸门。
    """
    rows = db.execute(
        select(GenerationTaskAsset.role, GenerationTask.user_deleted_at)
        .join(GenerationTask, GenerationTask.id == GenerationTaskAsset.task_id)
        .where(
            GenerationTaskAsset.asset_id == asset_id,
            GenerationTask.user_id == user_id,
        )
    ).all()
    if not rows:
        return False
    if any(deleted_at is None for _, deleted_at in rows):
        return False
    return all(role == "output" for role, _ in rows)


def _get_visible_asset(db: Session, asset_id: int, user: User) -> ImageAsset:
    """授权后再做软删闸门；管理员忽略软删但仍须 admin 角色。"""
    asset = db.get(ImageAsset, asset_id)
    is_admin = user.role == "admin"
    if asset is None or (asset.owner_id != user.id and not is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产不存在")
    if not is_admin and _owner_output_soft_deleted(db, asset.id, user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产不存在")
    return asset


@router.post("")
def upload_asset(
    file: UploadFile = File(...),
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持常见图片格式")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="空文件")
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="单张图片不能超过 30MB")
    ext = MIME_EXT[mime]
    object_key = storage.new_object_key(f"uploads/{user.id}", ext)
    storage.put_bytes(object_key, data, mime)
    asset = ImageAsset(
        owner_id=user.id,
        bucket=settings.MINIO_BUCKET,
        object_key=object_key,
        mime=mime,
        size_bytes=len(data),
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return {"id": asset.id, "mime": asset.mime, "size_bytes": asset.size_bytes}


@router.get("/{asset_id}/url")
def get_asset_url(
    asset_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    asset = _get_visible_asset(db, asset_id, user)
    url = storage.presigned_url(asset.object_key)
    return {"id": asset.id, "url": url}


@router.get("/{asset_id}/file")
def download_asset_file(
    asset_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> Response:
    asset = _get_visible_asset(db, asset_id, user)
    try:
        data = storage.get_bytes(asset.object_key)
    except (S3Error, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对象存储里没有这个文件或无法读取") from exc
    ext = MIME_EXT.get((asset.mime or "").split(";")[0].strip().lower(), "png")
    filename = f"yingpeng-{asset.id}.{ext}"
    return Response(
        content=data,
        media_type=asset.mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
