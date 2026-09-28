#!/bin/sh
set -eu

read_secret() {
    path="/run/secrets/$1"
    if [ ! -s "$path" ]; then
        echo "required deployment secret missing: $1" >&2
        exit 2
    fi
    cat "$path"
}

POSTGRES_PASSWORD="$(read_secret postgres_password)"; export POSTGRES_PASSWORD
REDIS_PASSWORD="$(read_secret redis_password)"; export REDIS_PASSWORD
MINIO_ACCESS_KEY="$(read_secret minio_access_key)"; export MINIO_ACCESS_KEY
MINIO_SECRET_KEY="$(read_secret minio_secret_key)"; export MINIO_SECRET_KEY
SECRET_KEY="$(read_secret session_key)"; export SECRET_KEY
DOUYIN_OAUTH_KEYS="$(read_secret oauth_keys)"; export DOUYIN_OAUTH_KEYS

if [ "$SECRET_KEY" = change-me ] || [ "${PUBLISH_LIVE:-0}" != 0 ] || [ "${DOUYIN_OAUTH_ENABLED:-0}" != 0 ]; then
    echo "unsafe deployment configuration; live platform calls are disabled in this stack" >&2
    exit 2
fi

case "${1:-}" in
    verify)
        exec python verify_runtime.py --schema
        ;;
    api)
        python verify_runtime.py --schema
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 110 --no-access-log
        ;;
    migrate)
        python verify_runtime.py --no-schema
        exec alembic upgrade head
        ;;
    migrate-0014)
        python verify_runtime.py --no-schema
        exec alembic upgrade 0014_operation_plans
        ;;
    generation-worker)
        python verify_runtime.py --schema
        exec python -m app.pipeline_worker
        ;;
    legacy-generation-worker)
        python verify_runtime.py --schema
        exec python -m app.worker
        ;;
    publish-worker)
        python verify_runtime.py --schema
        exec python -m app.publish_worker
        ;;
    operation-scheduler)
        python verify_runtime.py --schema
        exec python -m app.operation_scheduler --loop
        ;;
    *)
        echo "unknown entrypoint mode" >&2
        exit 2
        ;;
esac
