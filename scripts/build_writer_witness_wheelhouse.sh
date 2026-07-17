#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="${1:-}"
if [[ -z "$DESTINATION" || "$DESTINATION" != /* || "$DESTINATION" == "/" ]]; then
    echo "usage: $0 /absolute/empty/destination" >&2
    exit 2
fi
if [[ -e "$DESTINATION" && -n "$(find "$DESTINATION" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "destination must be empty: $DESTINATION" >&2
    exit 2
fi
install -d -m 0755 "$DESTINATION"
BOUND_MANIFEST="$ROOT_DIR/deploy/writer-witness/wheelhouse.sha256"
if [[ ! -f "$BOUND_MANIFEST" ]]; then
    echo "bound Writer Witness wheelhouse manifest is missing" >&2
    exit 2
fi

python3 -m pip download \
    --disable-pip-version-check \
    --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --python-version 3.12 \
    --implementation cp \
    --abi cp312 \
    --no-deps \
    --dest "$DESTINATION" \
    --requirement "$ROOT_DIR/deploy/writer-witness/requirements.lock"

python3 "$ROOT_DIR/scripts/verify_writer_witness_wheelhouse.py" \
    --wheelhouse "$DESTINATION" \
    --manifest "$BOUND_MANIFEST" \
    --expected-uid "$(id -u)" \
    >/dev/null
echo "$DESTINATION"
