"""Deployment dependency probe; /health remains only a liveness endpoint."""

import argparse
import os
import shutil
from pathlib import Path

from minio import Minio
from redis import Redis
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.integrations.publish.oauth_crypto import active_version


def verify(schema: bool) -> None:
    if settings.SECRET_KEY == "change-me" or len(settings.SECRET_KEY) < 32:
        raise RuntimeError("invalid session secret")
    for name in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        if not os.environ.get(name) or os.environ[name].lower() in {"change-me", "password", "example"}:
            raise RuntimeError(f"invalid deployment secret: {name}")
    try:
        active_version()
    except Exception as exc:
        raise RuntimeError("invalid OAuth key version") from None
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required")
    if not Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc").is_file():
        raise RuntimeError("Chinese font is required")
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        if schema:
            head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if head != "0014_operation_plans":
                raise RuntimeError("database schema is not 0014")
    Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, password=settings.REDIS_PASSWORD,
          socket_connect_timeout=5, socket_timeout=5).ping()
    client = Minio(settings.MINIO_ENDPOINT, access_key=settings.MINIO_ACCESS_KEY,
                   secret_key=settings.MINIO_SECRET_KEY, secure=False)
    client.list_buckets()
    print("deployment dependencies and schema verified" if schema else "deployment dependencies verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--no-schema", action="store_true")
    arguments = parser.parse_args()
    verify(arguments.schema)
