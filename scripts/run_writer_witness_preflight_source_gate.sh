#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${WRITER_WITNESS_SOURCE_GATE_HERMETIC:-}" != "1" ]]; then
    exec /usr/bin/env -i \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        HOME=/nonexistent \
        USER=writer-witness-source-gate \
        LOGNAME=writer-witness-source-gate \
        LANG=C.UTF-8 \
        LC_ALL=C.UTF-8 \
        TMPDIR=/tmp \
        WRITER_WITNESS_SOURCE_GATE_HERMETIC=1 \
        /bin/bash "${BASH_SOURCE[0]}"
fi

cd "$ROOT_DIR"

# The source gate is hermetic: imports receive non-secret placeholders while
# every real PostgreSQL test uses its separately guarded scratch database.
export DATABASE_URL="postgresql+asyncpg://matrix_gate:matrix_gate@127.0.0.1:1/matrix_gate"
export SYNC_DATABASE_URL="postgresql://matrix_gate:matrix_gate@127.0.0.1:1/matrix_gate"
export POSTGRES_DB="matrix_gate"
export POSTGRES_USER="matrix_gate"
export POSTGRES_PASSWORD="matrix-gate-placeholder"
export FRONTEND_URL="https://matrix-gate.invalid"
export REDIS_URL="redis://127.0.0.1:1/0"
export JWT_SECRET_KEY="matrix-gate-placeholder-jwt-secret-32-bytes"

shell_sources=( \
    scripts/build_writer_witness_release.sh \
    scripts/build_writer_witness_wheelhouse.sh \
    scripts/provision_writer_witness_host.sh \
    scripts/run_writer_witness_preflight_source_gate.sh \
    scripts/run_writer_witness_failure_drill.sh \
    deploy/writer-witness/writer-witness-live-restore.sh \
    deploy/writer-witness/writer-witness-matrix-host-faults.sh \
    deploy/writer-witness/writer-witness-state-manifest.sh \
    deploy/writer-witness/writer-witness-backup.sh \
    deploy/writer-witness/writer-witness-offsite-backup.sh \
    deploy/writer-witness/writer-witness-restore-drill.sh \
    deploy/writer-witness/writer-witness-activation-watchdog.sh \
)

python_sources=( \
    scripts/plan_writer_witness_real_host_matrix.py \
    scripts/provision_writer_witness_matrix_controller.py \
    scripts/render_writer_witness_credentials.py \
    scripts/run_writer_witness_clock_jump_probe.py \
    scripts/run_writer_witness_postgres_gate.py \
    scripts/run_writer_witness_real_host_matrix.py \
    scripts/verify_writer_witness_release.py \
    scripts/verify_writer_witness_runtime.py \
    scripts/verify_writer_witness_runtime_provenance.py \
    scripts/verify_writer_witness_wheelhouse.py \
    scripts/verify_writer_witness_nftables.py \
    scripts/writer_witness_matrix_client.py \
    scripts/smoke_writer_witness_client.py \
    writer_witness_app.py \
    deploy/writer-witness/writer-witness-activation.py \
    deploy/writer-witness/writer-witness-matrix-campaign.py \
    deploy/writer-witness/writer-witness-matrix-host-fault-state.py \
    deploy/writer-witness/writer-witness-rotate-hmac.py \
    deploy/writer-witness/writer-witness-s3-put.py \
)

for source in "${shell_sources[@]}"; do
    test -f "$source"
    bash -n "$source"
done

python3 -I - "$ROOT_DIR" "${python_sources[@]}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
for relative in sys.argv[2:]:
    path = root / relative
    if not path.is_file():
        raise SystemExit(f"missing Python source: {relative}")
    compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
print('{"status":"passed","gate":"writer-witness-closed-source-syntax"}')
PY

unit_modules=( \
    tests.test_writer_witness \
    tests.test_writer_witness_client \
    tests.test_writer_witness_deployment \
    tests.test_writer_witness_hmac_rotation \
    tests.test_writer_witness_clock_jump_probe \
    tests.test_writer_witness_host_fault_recovery \
    tests.test_writer_witness_matrix_campaign \
    tests.test_writer_witness_matrix_controller_provision \
    tests.test_verify_writer_witness_runtime \
    tests.test_verify_writer_witness_runtime_provenance \
    tests.test_verify_writer_witness_wheelhouse \
    tests.test_verify_writer_witness_nftables \
    tests.test_verify_writer_witness_release \
    tests.test_writer_witness_service \
    tests.test_webapp_writer_control \
    tests.test_writer_fencing \
    tests.test_runtime_identity \
    tests.test_background_job_authority \
    tests.test_render_writer_witness_credentials \
    tests.test_render_runtime_envs \
    tests.test_main_lifespan \
    tests.test_main_public_config \
    tests.test_arvan_origin_switch \
    tests.test_writer_witness_real_host_matrix_preflight \
    tests.test_writer_witness_real_host_matrix_runner \
)

python3 -I - "$ROOT_DIR" "${unit_modules[@]}" <<'PY'
import json
import sys
import unittest

sys.path.insert(0, sys.argv[1])
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[2:])
result = unittest.TextTestRunner(verbosity=1).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
if result.skipped:
    print(
        json.dumps(
            {
                "status": "failed",
                "reason": "unit_tests_skipped",
                "skipped": len(result.skipped),
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    raise SystemExit(1)
print(
    json.dumps(
        {"status": "passed", "gate": "writer-witness-unit-suite", "skipped": 0},
        sort_keys=True,
    )
)
PY

bash "$ROOT_DIR/scripts/run_writer_witness_failure_drill.sh"

printf '%s\n' '{"status":"passed","gate":"writer-witness-preflight-source","guarded_postgres_tests":4,"skipped":0,"four_database_drill":true}'
