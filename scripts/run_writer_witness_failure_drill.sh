#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/writer-witness-drill/docker-compose.yml"
PROJECT_NAME="writer-witness-drill"

compose() {
    docker compose --project-name "$PROJECT_NAME" --file "$COMPOSE_FILE" "$@"
}

cleanup() {
    compose unpause witness_db >/dev/null 2>&1 || true
    if [[ "${WRITER_WITNESS_DRILL_KEEP:-false}" != "true" ]]; then
        compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

compose down --volumes --remove-orphans >/dev/null 2>&1 || true
compose up --detach --wait bot_db webapp_fi_db webapp_ir_db witness_db
compose run --rm --no-deps runner python scripts/run_writer_witness_failure_drill.py core
compose pause witness_db
compose run --rm --no-deps runner python scripts/run_writer_witness_failure_drill.py pause
compose unpause witness_db
compose run --rm --no-deps runner python scripts/run_writer_witness_failure_drill.py recovery

echo '{"status":"passed","drill":"writer-witness-four-database-failure-matrix"}'
