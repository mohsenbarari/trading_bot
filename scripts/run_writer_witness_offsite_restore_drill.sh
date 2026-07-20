#!/usr/bin/env bash
set -Eeuo pipefail

WITNESS_HOST="${WRITER_WITNESS_HOST:-}"
IDENTITY_FILE="${WRITER_WITNESS_S3_IDENTITY_FILE:-/root/secure-envs/hetzner/witness-object-storage-age-identity.txt}"
ADMIN_ENV="${WRITER_WITNESS_S3_ADMIN_ENV:-/root/secure-envs/hetzner/witness-object-storage-admin.env}"
BUCKET_ENV="${WRITER_WITNESS_S3_BUCKET_ENV:-/root/secure-envs/hetzner/witness-object-storage-bucket.env}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! "$WITNESS_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "WRITER_WITNESS_HOST is missing or unsafe" >&2
    exit 2
fi
for path_value in "$IDENTITY_FILE" "$ADMIN_ENV" "$BUCKET_ENV"; do
    if [[ ! "$path_value" =~ ^/[A-Za-z0-9._/-]+$ || ! -f "$path_value" ]]; then
        echo "missing or unsafe local S3 restore material" >&2
        exit 2
    fi
done
if [[ $(stat -c '%a' "$IDENTITY_FILE") != 600 ]]; then
    echo "Writer Witness age identity must have mode 0600" >&2
    exit 2
fi
for command in age python3 ssh; do
    command -v "$command" >/dev/null || {
        echo "missing restore command: $command" >&2
        exit 2
    }
done

encrypted_path="$(mktemp /tmp/writer-witness-s3-restore.XXXXXXXX.dump.age)"
cleanup() {
    rm -f "$encrypted_path"
}
trap cleanup EXIT
rm -f "$encrypted_path"
python3 "$ROOT_DIR/scripts/download_writer_witness_s3_backup.py" \
    --admin-env "$ADMIN_ENV" \
    --bucket-env "$BUCKET_ENV" \
    --output "$encrypted_path"

age --decrypt --identity "$IDENTITY_FILE" "$encrypted_path" \
    | ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$WITNESS_HOST" \
        '/usr/local/sbin/writer-witness-restore-drill -'
