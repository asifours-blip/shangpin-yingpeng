#!/usr/bin/env bash
set -euo pipefail

project="${PUBLIC_CI_PROJECT:-studio-public-ci}"
if [[ ! "$project" =~ ^[a-z][a-z0-9-]{2,40}$ ]]; then
  echo 'Invalid PUBLIC_CI_PROJECT' >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$root/deploy/ci/results"
compose=(docker compose --env-file "$root/deploy/ci/empty.env" -f "$root/deploy/ci/compose.yaml" -p "$project")
cleanup() { "${compose[@]}" stop >/dev/null || true; }
trap cleanup EXIT

"${compose[@]}" up -d --wait postgres redis minio
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" --profile tools run --rm test
