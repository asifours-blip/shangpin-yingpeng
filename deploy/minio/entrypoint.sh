#!/bin/sh
set -eu

test -s /run/secrets/minio_access_key
test -s /run/secrets/minio_secret_key
MINIO_ROOT_USER="$(cat /run/secrets/minio_access_key)"
MINIO_ROOT_PASSWORD="$(cat /run/secrets/minio_secret_key)"
export MINIO_ROOT_USER MINIO_ROOT_PASSWORD
unset MINIO_ROOT_USER_FILE MINIO_ROOT_PASSWORD_FILE
exec /usr/local/bin/minio "$@"
