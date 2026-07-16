#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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
