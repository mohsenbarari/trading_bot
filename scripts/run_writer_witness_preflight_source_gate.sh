#!/bin/bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${WRITER_WITNESS_SOURCE_GATE_HERMETIC:-}" != "1" ]]; then
    exec /usr/bin/env -i \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
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
    scripts/configure_writer_witness_s3_backup.sh \
    scripts/provision_writer_witness_host.sh \
    scripts/run_writer_witness_preflight_source_gate.sh \
    scripts/run_writer_witness_failure_drill.sh \
    scripts/run_writer_witness_matrix_controller.sh \
    deploy/writer-witness/writer-witness-live-restore.sh \
    deploy/writer-witness/writer-witness-matrix-host-faults.sh \
    deploy/writer-witness/writer-witness-state-manifest.sh \
    deploy/writer-witness/writer-witness-backup.sh \
    deploy/writer-witness/writer-witness-offsite-backup.sh \
    deploy/writer-witness/writer-witness-restore-drill.sh \
    deploy/writer-witness/writer-witness-activation-watchdog.sh \
)

python_sources=( \
    core/secure_file_io.py \
    scripts/hold_writer_witness_package_locks.py \
    scripts/plan_writer_witness_real_host_matrix.py \
    scripts/publish_wa_ir_object_storage_preflight.py \
    scripts/publish_wa_ir_object_storage_transfer.py \
    scripts/provision_wa_ir_staging_volume.py \
    scripts/provision_writer_witness_matrix_controller.py \
    scripts/render_writer_witness_credentials.py \
    scripts/run_writer_witness_clock_jump_probe.py \
    scripts/run_writer_witness_postgres_gate.py \
    scripts/run_writer_witness_real_host_matrix.py \
    scripts/run_wa_ir_object_storage_preflight.py \
    scripts/generate_writer_witness_command_surfaces.py \
    scripts/verify_writer_witness_controller_toolchain.py \
    scripts/writer_witness_controller_runtime.py \
    scripts/verify_writer_witness_release.py \
    scripts/verify_writer_witness_runtime.py \
    scripts/verify_writer_witness_host_toolchain.py \
    scripts/verify_writer_witness_runtime_provenance.py \
    scripts/verify_writer_witness_process_maps.py \
    scripts/verify_writer_witness_wheelhouse.py \
    scripts/verify_writer_witness_nftables.py \
    scripts/writer_witness_matrix_client.py \
    scripts/wa_ir_object_storage_preflight_agent.py \
    scripts/smoke_writer_witness_client.py \
    writer_witness_app.py \
    deploy/writer-witness/models-package-init.py \
    deploy/writer-witness/writer-witness-activation.py \
    deploy/writer-witness/writer-witness-matrix-campaign.py \
    deploy/writer-witness/writer-witness-matrix-host-fault-state.py \
    deploy/writer-witness/writer-witness-rotate-hmac.py \
    deploy/writer-witness/writer-witness-s3-put.py \
)

for source in "${shell_sources[@]}"; do
    test -f "$source"
    /bin/bash -n "$source"
done

/usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null - "$ROOT_DIR" "${python_sources[@]}" <<'PY'
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

/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    scripts/verify_writer_witness_host_toolchain.py \
    --verify-command-surface "$ROOT_DIR" >/dev/null

/usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    scripts/generate_writer_witness_command_surfaces.py \
    --verify deploy/writer-witness/command-surfaces.generated.json >/dev/null

unit_modules=( \
    tests.test_writer_witness \
    tests.test_writer_witness_client \
    tests.test_writer_witness_deployment \
    tests.test_writer_witness_hmac_rotation \
    tests.test_writer_witness_clock_jump_probe \
    tests.test_writer_witness_host_fault_recovery \
    tests.test_writer_witness_matrix_campaign \
    tests.test_writer_witness_matrix_controller_provision \
    tests.test_writer_witness_command_surfaces \
    tests.test_verify_writer_witness_runtime \
    tests.test_verify_writer_witness_host_toolchain \
    tests.test_verify_writer_witness_controller_toolchain \
    tests.test_verify_writer_witness_runtime_provenance \
    tests.test_verify_writer_witness_process_maps \
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
    tests.test_publish_wa_ir_object_storage_preflight \
    tests.test_publish_wa_ir_object_storage_transfer \
    tests.test_provision_wa_ir_staging_volume \
    tests.test_run_wa_ir_object_storage_preflight \
    tests.test_wa_ir_object_storage_preflight_agent \
)

/usr/bin/python3.12 -I -B -X utf8 -X pycache_prefix=/dev/null - "$ROOT_DIR" "${unit_modules[@]}" <<'PY'
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
        {
            "status": "passed",
            "gate": "writer-witness-unit-suite",
            "tests": result.testsRun,
            "skipped": 0,
        },
        sort_keys=True,
    )
)
PY

failure_drill_output="$(/bin/bash "$ROOT_DIR/scripts/run_writer_witness_failure_drill.sh")"
printf '%s\n' "$failure_drill_output"
/usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null - "$failure_drill_output" <<'PY'
import json
import sys

documents = []
for line in sys.argv[1].splitlines():
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        continue
    if (
        isinstance(value, dict)
        and value.get("drill") == "writer-witness-four-database-failure-matrix"
    ):
        documents.append(value)
if len(documents) != 1 or documents[0].get("status") != "passed":
    raise SystemExit("four-database failure drill did not emit one passing result")
tests = documents[0].get("guarded_postgres_tests")
if not isinstance(tests, int) or isinstance(tests, bool) or tests < 1:
    raise SystemExit("four-database failure drill did not bind its PostgreSQL test count")
print(
    json.dumps(
        {
            "status": "passed",
            "gate": "writer-witness-preflight-source",
            "guarded_postgres_tests": tests,
            "skipped": 0,
            "four_database_drill": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
PY
