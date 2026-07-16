#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# The source gate is hermetic: imports receive non-secret placeholders while
# every real PostgreSQL test uses its separately guarded scratch database.
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://matrix_gate:matrix_gate@127.0.0.1:1/matrix_gate}"
export SYNC_DATABASE_URL="${SYNC_DATABASE_URL:-postgresql://matrix_gate:matrix_gate@127.0.0.1:1/matrix_gate}"
export POSTGRES_DB="${POSTGRES_DB:-matrix_gate}"
export POSTGRES_USER="${POSTGRES_USER:-matrix_gate}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-matrix-gate-placeholder}"
export FRONTEND_URL="${FRONTEND_URL:-https://matrix-gate.invalid}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:1/0}"
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-matrix-gate-placeholder-jwt-secret-32-bytes}"

python3 -m unittest \
    tests.test_writer_witness \
    tests.test_writer_witness_client \
    tests.test_writer_witness_deployment \
    tests.test_writer_witness_hmac_rotation \
    tests.test_writer_witness_service \
    tests.test_webapp_writer_control \
    tests.test_writer_fencing \
    tests.test_runtime_identity \
    tests.test_background_job_authority \
    tests.test_render_runtime_envs \
    tests.test_main_lifespan \
    tests.test_main_public_config \
    tests.test_arvan_origin_switch \
    tests.test_writer_witness_real_host_matrix_preflight \
    tests.test_writer_witness_real_host_matrix_runner

bash "$ROOT_DIR/scripts/run_writer_witness_failure_drill.sh"

printf '%s\n' '{"status":"passed","gate":"writer-witness-preflight-source","guarded_postgres_tests":4,"skipped":0,"four_database_drill":true}'
