#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

coverage_args=()
runner_command=(python scripts/run_registration_scratch_suite.py)
postgres_name="trading_bot_stage9_postgres_$$"
postgres_user="stage9"
postgres_password="stage9-disposable-only"
postgres_database="stage9_runtime"
runtime_async_url="postgresql+asyncpg://${postgres_user}:${postgres_password}@${postgres_name}:5432/${postgres_database}"
runtime_sync_url="postgresql+psycopg2://${postgres_user}:${postgres_password}@${postgres_name}:5432/${postgres_database}"
stage9_commit="$(git rev-parse HEAD)"
printf 'stage9_evidence_commit=%s\n' "$stage9_commit" >&2

cleanup() {
  docker rm -fv "$postgres_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
if [[ "${1:-}" == "--coverage" ]]; then
  mkdir -p "$repo_root/tmp/stage9-postgres-coverage"
  find "$repo_root/tmp/stage9-postgres-coverage" -maxdepth 1 -type f -name '.coverage.*' -delete
  coverage_args+=(
    -e STAGE9_COVERAGE_FILE=/app/tmp/stage9-postgres-coverage/.coverage
    -e PYTHONPATH=/app/stage9-test-packages-py311
    -v "$repo_root/tmp:/app/tmp"
    -v "$repo_root/tmp/stage9-site-packages-py311:/app/stage9-test-packages-py311:ro"
  )
  runner_command=(
    python -m coverage run --branch --parallel-mode
    scripts/run_registration_scratch_suite.py
  )
elif [[ -n "${1:-}" ]]; then
  printf 'unknown argument: %s\n' "$1" >&2
  exit 2
fi

docker compose run --rm --no-deps --name "$postgres_name" -d \
  -e POSTGRES_USER="$postgres_user" \
  -e POSTGRES_PASSWORD="$postgres_password" \
  -e POSTGRES_DB="$postgres_database" \
  -v /var/lib/postgresql/data \
  db >/dev/null

data_mount_name="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$postgres_name")"
if [[ -z "$data_mount_name" || "$data_mount_name" == *postgres_data ]]; then
  printf 'Stage 9 PostgreSQL refused unsafe data mount: %s\n' "$data_mount_name" >&2
  exit 1
fi
printf 'Stage 9 PostgreSQL disposable resource: container=%s anonymous_data_volume=%s\n' \
  "$postgres_name" "$data_mount_name" >&2

ready=false
for _attempt in $(seq 1 40); do
  if docker exec "$postgres_name" pg_isready -U "$postgres_user" -d "$postgres_database" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 0.25
done
if [[ "$ready" != true ]]; then
  printf 'Stage 9 disposable PostgreSQL did not become ready\n' >&2
  exit 1
fi

scratch_cluster_system_id="$(
  docker exec "$postgres_name" \
    psql -U "$postgres_user" -d "$postgres_database" -Atqc \
    'SELECT system_identifier FROM pg_control_system()'
)"
if [[ ! "$scratch_cluster_system_id" =~ ^[1-9][0-9]{15,19}$ ]]; then
  printf 'Stage 9 disposable PostgreSQL system identifier is invalid\n' >&2
  exit 1
fi

docker compose run --rm --no-deps \
  "${coverage_args[@]}" \
  -e TRADING_BOT_EXPECTED_CHECKOUT=/app \
  -e STAGE9_SCRATCH_DATABASES_ALLOWED=true \
  -e ENVIRONMENT=test \
  -e TRADING_BOT_EXPECTED_SCRATCH_CLUSTER_SYSTEM_ID="$scratch_cluster_system_id" \
  -e DATABASE_URL="$runtime_async_url" \
  -e SYNC_DATABASE_URL="$runtime_sync_url" \
  bot "${runner_command[@]}"

printf 'Stage 9 PostgreSQL suite complete: disposable_container=%s anonymous_data_volume=%s\n' \
  "$postgres_name" "$data_mount_name" >&2
