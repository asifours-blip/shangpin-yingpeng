"""MinIO 客户端。桶不存在时创建。"""

from minio import Minio

from app.core.config import settings


def _endpoint() -> tuple[str, bool]:
    raw = settings.MINIO_ENDPOINT.strip()
    secure = raw.startswith("https://")
    host = raw.replace("https://", "").replace("http://", "")
    return host, secure


_host, _secure = _endpoint()
minio_client = Minio(
    _host,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    secure=_secure,
)


def ensure_bucket() -> None:
    name = settings.MINIO_BUCKET
    if not minio_client.bucket_exists(name):
        minio_client.make_bucket(name)
