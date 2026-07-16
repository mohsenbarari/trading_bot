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

install -d -m 0755 \
    "$DESTINATION/core" \
    "$DESTINATION/models" \
    "$DESTINATION/deploy/writer-witness" \
    "$DESTINATION/scripts"
install -m 0644 "$ROOT_DIR/writer_witness_app.py" "$DESTINATION/writer_witness_app.py"
install -m 0755 \
    "$ROOT_DIR/scripts/smoke_writer_witness_client.py" \
    "$DESTINATION/scripts/smoke_writer_witness_client.py"
install -m 0755 \
    "$ROOT_DIR/scripts/run_writer_witness_clock_jump_probe.py" \
    "$DESTINATION/scripts/run_writer_witness_clock_jump_probe.py"

for source in \
    __init__.py \
    enums.py \
    offer_identity.py \
    registration_identity.py \
    runtime_sites.py \
    writer_witness_auth.py \
    writer_witness_contract.py \
    writer_witness_control.py
do
    install -m 0644 "$ROOT_DIR/core/$source" "$DESTINATION/core/$source"
done

find "$ROOT_DIR/models" -maxdepth 1 -type f -name '*.py' -print0 \
    | while IFS= read -r -d '' source; do
        install -m 0644 "$source" "$DESTINATION/models/$(basename "$source")"
    done

for source in \
    001_initial.sql \
    requirements.txt \
    requirements.lock \
    nginx.conf.template \
    writer-witness.service \
    writer-witness-backup.sh \
    writer-witness-offsite-backup.sh \
    writer-witness-s3-put.py \
    writer-witness-rotate-hmac.py \
    writer-witness-live-restore.sh \
    writer-witness-matrix-host-faults.sh \
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

python3 - "$DESTINATION" <<'PY'
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
PY

echo "$DESTINATION"
