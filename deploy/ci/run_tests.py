"""Fail closed before the full backend suite runs in the isolated CI network."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    required = {
        "APP_ENV": "test",
        "PUBLIC_CI_STRICT": "1",
        "PUBLISH_LIVE": "0",
        "DOUYIN_OAUTH_ENABLED": "0",
        "ARK_API_KEY": "synthetic-ci-model-marker",
        "DOUYIN_CLIENT_SECRET": "",
    }
    for name, expected in required.items():
        if os.environ.get(name) != expected:
            raise SystemExit(f"Unsafe CI setting: {name}")
    if Path("/app/.env").exists() or Path("/app/backend/.env").exists():
        raise SystemExit("CI image must not contain a .env file")
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            raise SystemExit(f"Missing required executable: {executable}")

    from app.core.config import settings
    from app.core.db import engine
    from app.core.minio_client import ensure_bucket
    from app.services.media_render import FONT_PATH
    from redis import Redis
    from sqlalchemy import text

    if not FONT_PATH.is_file():
        raise SystemExit("Chinese rendering font is missing")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    redis = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    if redis.ping() is not True:
        raise SystemExit("Redis did not answer PING")
    ensure_bucket()
    print("CI preflight: PostgreSQL, Redis, MinIO, ffmpeg, ffprobe, font ready", flush=True)
    return subprocess.call([
        sys.executable, "-m", "pytest", "-q", "-ra", "--strict-markers",
        "--junitxml=/reports/backend.xml", "tests",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
