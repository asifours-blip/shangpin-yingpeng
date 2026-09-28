"""MinIO 存取。"""

from datetime import timedelta
from contextlib import contextmanager
from io import BytesIO
from urllib.parse import urlsplit
from uuid import uuid4

from minio.error import S3Error

from app.core.config import settings
from app.core.minio_client import ensure_bucket, minio_client


def put_bytes(object_key: str, data: bytes, content_type: str) -> None:
    ensure_bucket()
    minio_client.put_object(
        settings.MINIO_BUCKET,
        object_key,
        BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def presigned_url(object_key: str, expires_seconds: int = 3600) -> str:
    ensure_bucket()
    signed = minio_client.presigned_get_object(
        settings.MINIO_BUCKET,
        object_key,
        expires=timedelta(seconds=expires_seconds),
    )
    public_base = settings.MINIO_PUBLIC_BASE_URL.rstrip("/")
    if not public_base:
        return signed
    internal = urlsplit(signed)
    return f"{public_base}{internal.path}?{internal.query}"


def get_bytes(object_key: str) -> bytes:
    ensure_bucket()
    resp = minio_client.get_object(settings.MINIO_BUCKET, object_key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


@contextmanager
def open_stream(object_key: str):
    """Read an object in chunks without retaining its full body in memory."""
    ensure_bucket()
    resp = minio_client.get_object(settings.MINIO_BUCKET, object_key)
    try:
        yield resp
    finally:
        resp.close()
        resp.release_conn()


def new_object_key(prefix: str, ext: str) -> str:
    return f"{prefix}/{uuid4().hex}.{ext}"


def object_exists(object_key: str) -> bool:
    try:
        minio_client.stat_object(settings.MINIO_BUCKET, object_key)
        return True
    except S3Error:
        return False


def delete_object(object_key: str) -> None:
    """删掉本次上传但没有完成关联的对象。已经不存在就当删完了。"""
    try:
        minio_client.remove_object(settings.MINIO_BUCKET, object_key)
    except S3Error as exc:
        if getattr(exc, "code", "") in {"NoSuchKey", "NoSuchBucket"}:
            return
        raise
