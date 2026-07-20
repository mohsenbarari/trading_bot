#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="${1:-}"
if [[ -z "$DESTINATION" || "$DESTINATION" == "/" ]]; then
    echo "usage: $0 /absolute/empty/destination" >&2
    exit 2
fi
if [[ "$DESTINATION" != /* ]]; then
    echo "destination must be absolute" >&2
    exit 2
fi
if [[ -e "$DESTINATION" && -n "$(find "$DESTINATION" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "destination must be empty: $DESTINATION" >&2
    exit 2
fi

system_runtime_manifest="$ROOT_DIR/deploy/writer-witness/python-runtime.json"
system_runtime_manifest_sha256="$(sha256sum "$system_runtime_manifest" | awk '{print $1}')"
expected_system_python="$(sed -n 's/^  "executable_path": "\([^"]*\)",$/\1/p' "$system_runtime_manifest")"
[[ "$expected_system_python" == /usr/bin/python3.12 \
    && "$system_runtime_manifest_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "release-bound system runtime manifest identity is invalid" >&2
    exit 2
}
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$expected_system_python" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    "$ROOT_DIR/scripts/verify_writer_witness_runtime.py" \
    --system-only \
    --system-runtime-manifest "$system_runtime_manifest" \
    --expected-system-runtime-manifest-sha256 "$system_runtime_manifest_sha256" \
    --expected-lock-uid 0 \
    >/dev/null

# Canonical builders additionally regenerate the manifest from the already
# verified wheelhouse. Ordinary reproducible release builds still require the
# exact approved host manifest above, but never self-approve a changed one.
if [[ -n "${WRITER_WITNESS_CANONICAL_WHEELHOUSE:-}" ]]; then
    canonical_wheelhouse="$WRITER_WITNESS_CANONICAL_WHEELHOUSE"
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$expected_system_python" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$ROOT_DIR/scripts/verify_writer_witness_wheelhouse.py" \
        --wheelhouse "$canonical_wheelhouse" \
        --manifest "$ROOT_DIR/deploy/writer-witness/wheelhouse.sha256" \
        --expected-uid 0 \
        >/dev/null
    observed_manifest="$(mktemp)"
    trap 'rm -f "$observed_manifest"' EXIT
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$expected_system_python" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$ROOT_DIR/scripts/verify_writer_witness_runtime.py" \
        --emit-system-runtime-manifest \
        --wheelhouse "$canonical_wheelhouse" \
        >"$observed_manifest"
    cmp -s "$system_runtime_manifest" "$observed_manifest" || {
        echo "canonical builder system runtime differs from the approved manifest" >&2
        exit 2
    }
    rm -f "$observed_manifest"
    trap - EXIT
fi

install -d -m 0755 \
    "$DESTINATION/core" \
    "$DESTINATION/models" \
    "$DESTINATION/deploy/writer-witness" \
    "$DESTINATION/scripts"
chmod 0755 "$DESTINATION"
install -m 0644 "$ROOT_DIR/writer_witness_app.py" "$DESTINATION/writer_witness_app.py"
install -m 0755 \
    "$ROOT_DIR/scripts/smoke_writer_witness_client.py" \
    "$DESTINATION/scripts/smoke_writer_witness_client.py"
install -m 0755 \
    "$ROOT_DIR/scripts/run_writer_witness_clock_jump_probe.py" \
    "$DESTINATION/scripts/run_writer_witness_clock_jump_probe.py"
install -m 0644 \
    "$ROOT_DIR/scripts/hold_writer_witness_package_locks.py" \
    "$DESTINATION/scripts/hold_writer_witness_package_locks.py"
install -m 0755 \
    "$ROOT_DIR/scripts/verify_writer_witness_host_toolchain.py" \
    "$DESTINATION/scripts/verify_writer_witness_host_toolchain.py"
install -m 0755 \
    "$ROOT_DIR/scripts/verify_writer_witness_release.py" \
    "$DESTINATION/scripts/verify_writer_witness_release.py"
install -m 0755 \
    "$ROOT_DIR/scripts/verify_writer_witness_runtime.py" \
    "$DESTINATION/scripts/verify_writer_witness_runtime.py"
install -m 0755 \
    "$ROOT_DIR/scripts/verify_writer_witness_runtime_provenance.py" \
    "$DESTINATION/scripts/verify_writer_witness_runtime_provenance.py"
install -m 0755 \
    "$ROOT_DIR/scripts/verify_writer_witness_process_maps.py" \
    "$DESTINATION/scripts/verify_writer_witness_process_maps.py"
install -m 0755 \
    "$ROOT_DIR/scripts/verify_writer_witness_wheelhouse.py" \
    "$DESTINATION/scripts/verify_writer_witness_wheelhouse.py"
install -m 0755 \
    "$ROOT_DIR/scripts/verify_writer_witness_nftables.py" \
    "$DESTINATION/scripts/verify_writer_witness_nftables.py"
install -m 0644 \
    "$ROOT_DIR/scripts/render_writer_witness_credentials.py" \
    "$DESTINATION/scripts/render_writer_witness_credentials.py"

for source in \
    __init__.py \
    enums.py \
    offer_identity.py \
    registration_identity.py \
    runtime_sites.py \
    writer_lease_clock.py \
    writer_witness_auth.py \
    writer_witness_contract.py \
    writer_witness_control.py
do
    install -m 0644 "$ROOT_DIR/core/$source" "$DESTINATION/core/$source"
done

install -m 0644 \
    "$ROOT_DIR/deploy/writer-witness/models-package-init.py" \
    "$DESTINATION/models/__init__.py"
for source in database.py webapp_writer_state.py; do
    install -m 0644 "$ROOT_DIR/models/$source" "$DESTINATION/models/$source"
done

for source in \
    001_initial.sql \
    002_failover_operation_ledger.sql \
    requirements.txt \
    requirements.lock \
    python-runtime.json \
    nftables-policy.json \
    wheelhouse.sha256 \
    nginx.conf.template \
    writer-witness-activation.py \
    writer-witness-activation-recovery.service \
    writer-witness-activation-watchdog.sh \
    writer-witness-activation-watchdog.service \
    writer-witness-activation-watchdog.timer \
    writer-witness.service \
    writer-witness-backup.sh \
    writer-witness-offsite-backup.sh \
    writer-witness-s3-put.py \
    writer-witness-rotate-hmac.py \
    writer-witness-live-restore.sh \
    writer-witness-matrix-campaign.py \
    writer-witness-matrix-host-faults.sh \
    writer-witness-matrix-host-fault-state.py \
    writer-witness-state-manifest.sh \
    writer-witness-restore-drill.sh \
    writer-witness-backup.service \
    writer-witness-backup.timer \
    writer-witness-offsite-backup.service \
    writer-witness-offsite-backup.timer
do
    install -m 0644 \
        "$ROOT_DIR/deploy/writer-witness/$source" \
        "$DESTINATION/deploy/writer-witness/$source"
done

/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$expected_system_python" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    - "$DESTINATION" <<'PY'
from pathlib import Path
import hashlib
import json
import sys

root = Path(sys.argv[1]).resolve()
manifest = {}
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    relative = path.relative_to(root).as_posix()
    manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
(root / "release-manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
)
(root / "release-manifest.json").chmod(0o644)
PY

manifest_sha256="$(sha256sum "$DESTINATION/release-manifest.json" | awk '{print $1}')"
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$expected_system_python" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    "$DESTINATION/scripts/verify_writer_witness_release.py" \
    --release-root "$DESTINATION" \
    --expected-manifest-sha256 "$manifest_sha256" \
    >/dev/null

echo "$DESTINATION"
