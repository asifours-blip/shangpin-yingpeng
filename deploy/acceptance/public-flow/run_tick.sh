#!/bin/sh
set -eu
POSTGRES_PASSWORD="$(cat /run/secrets/postgres_password)"; export POSTGRES_PASSWORD
REDIS_PASSWORD="$(cat /run/secrets/redis_password)"; export REDIS_PASSWORD
MINIO_ACCESS_KEY="$(cat /run/secrets/minio_access_key)"; export MINIO_ACCESS_KEY
MINIO_SECRET_KEY="$(cat /run/secrets/minio_secret_key)"; export MINIO_SECRET_KEY
SECRET_KEY="$(cat /run/secrets/session_key)"; export SECRET_KEY
exec python /acceptance/run_tick.py
