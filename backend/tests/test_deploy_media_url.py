"""部署代理只替换浏览器地址，MinIO 签名路径与查询参数保持原样。"""

from app.core.config import settings
from app.services import storage


def test_presigned_url_uses_public_proxy_without_changing_signed_path_or_query(monkeypatch):
    signed = "http://minio:9000/studio-assets/uploads/a%20b.jpg?X-Amz-Signature=abc%2Fdef&partNumber=1"
    monkeypatch.setattr(storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(storage.minio_client, "presigned_get_object", lambda *_args, **_kwargs: signed)
    monkeypatch.setattr(settings, "MINIO_PUBLIC_BASE_URL", "http://127.0.0.1:18080/objects")
    assert storage.presigned_url("uploads/a b.jpg") == (
        "http://127.0.0.1:18080/objects/studio-assets/uploads/a%20b.jpg"
        "?X-Amz-Signature=abc%2Fdef&partNumber=1"
    )


def test_presigned_url_keeps_existing_behavior_without_public_proxy(monkeypatch):
    signed = "http://127.0.0.1:19000/studio-assets/video.mp4?X-Amz-Signature=abc"
    monkeypatch.setattr(storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(storage.minio_client, "presigned_get_object", lambda *_args, **_kwargs: signed)
    monkeypatch.setattr(settings, "MINIO_PUBLIC_BASE_URL", "")
    assert storage.presigned_url("video.mp4") == signed
