#!/bin/bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/writer-witness-drill/docker-compose.yml"
PROJECT_NAME="writer-witness-drill"

compose() {
    /usr/bin/docker compose --project-name "$PROJECT_NAME" --file "$COMPOSE_FILE" "$@"
}

cleanup() {
    compose unpause witness_db >/dev/null 2>&1 || true
    if [[ "${WRITER_WITNESS_DRILL_KEEP:-false}" != "true" ]]; then
        compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

compose down --volumes --remove-orphans >/dev/null 2>&1 || true
compose up --detach --wait bot_db webapp_fi_db webapp_ir_db witness_db postgres_gate_db
postgres_gate_output="$(
    compose run --rm --no-deps runner python scripts/run_writer_witness_postgres_gate.py
)"
printf '%s\n' "$postgres_gate_output"
guarded_postgres_tests="$(
    /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null -c '
import json, sys
documents = []
for line in sys.stdin:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(value, dict) and value.get("gate") == "writer-witness-real-postgres":
        documents.append(value)
if len(documents) != 1 or documents[0].get("status") != "passed":
    raise SystemExit("guarded PostgreSQL gate did not emit one passing result")
tests = documents[0].get("tests")
if not isinstance(tests, int) or isinstance(tests, bool) or tests < 1:
    raise SystemExit("guarded PostgreSQL gate emitted an invalid test count")
print(tests)
' <<<"$postgres_gate_output"
)"
compose run --rm --no-deps runner python scripts/run_writer_witness_failure_drill.py core
compose pause witness_db
compose run --rm --no-deps runner python scripts/run_writer_witness_failure_drill.py pause
compose unpause witness_db
compose run --rm --no-deps runner python scripts/run_writer_witness_failure_drill.py recovery

printf '{"status":"passed","drill":"writer-witness-four-database-failure-matrix","guarded_postgres_tests":%s}\n' \
    "$guarded_postgres_tests"
