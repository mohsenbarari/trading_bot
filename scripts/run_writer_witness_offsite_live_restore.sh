#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" != "--apply" || "$#" -ne 1 ]]; then
    echo "usage: $0 --apply" >&2
    exit 2
fi

WITNESS_HOST="${WRITER_WITNESS_HOST:-}"
EXPECTED_STATE="${WRITER_WITNESS_RESTORE_EXPECTED_STATE:-}"
EXPECTED_RECEIPTS="${WRITER_WITNESS_RESTORE_EXPECTED_RECEIPTS:-}"
REQUIRED_CURRENT_STATE="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE:-}"
REQUIRED_CURRENT_RECEIPTS="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS:-}"
REQUIRED_CURRENT_MANIFEST_SHA256="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256:-}"
EXPECTED_MANIFEST_SHA256="${WRITER_WITNESS_RESTORE_EXPECTED_MANIFEST_SHA256:-}"
EXPECTED_BACKUP_SHA256="${WRITER_WITNESS_RESTORE_EXPECTED_BACKUP_SHA256:-}"
IDENTITY_FILE="${WRITER_WITNESS_S3_IDENTITY_FILE:-/root/secure-envs/hetzner/witness-object-storage-age-identity.txt}"
ADMIN_ENV="${WRITER_WITNESS_S3_ADMIN_ENV:-/root/secure-envs/hetzner/witness-object-storage-admin.env}"
BUCKET_ENV="${WRITER_WITNESS_S3_BUCKET_ENV:-/root/secure-envs/hetzner/witness-object-storage-bucket.env}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! "$WITNESS_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "WRITER_WITNESS_HOST is missing or unsafe" >&2
    exit 2
fi
for state in "$EXPECTED_STATE" "$REQUIRED_CURRENT_STATE"; do
    [[ "$state" =~ ^webapp:[0-9]+:[a-z_]+$ ]] || {
        echo "writer witness restore state guard is missing or unsafe" >&2
        exit 2
    }
done
for count in "$EXPECTED_RECEIPTS" "$REQUIRED_CURRENT_RECEIPTS"; do
    [[ "$count" =~ ^[0-9]+$ ]] || {
        echo "writer witness restore receipt guard is missing or unsafe" >&2
        exit 2
    }
done
for digest in "$EXPECTED_MANIFEST_SHA256" "$EXPECTED_BACKUP_SHA256" "$REQUIRED_CURRENT_MANIFEST_SHA256"; do
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || {
        echo "writer witness restore digest guard is missing or unsafe" >&2
        exit 2
    }
done
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

encrypted_path="$(mktemp /tmp/writer-witness-s3-live-restore.XXXXXXXX.dump.age)"
decrypted_path="$(mktemp /tmp/writer-witness-s3-live-restore.XXXXXXXX.dump)"
cleanup() {
    rm -f "$encrypted_path" "$decrypted_path"
}
trap cleanup EXIT
rm -f "$encrypted_path"
python3 "$ROOT_DIR/scripts/download_writer_witness_s3_backup.py" \
    --admin-env "$ADMIN_ENV" \
    --bucket-env "$BUCKET_ENV" \
    --output "$encrypted_path"

age --decrypt --identity "$IDENTITY_FILE" --output "$decrypted_path" "$encrypted_path"
chmod 0600 "$decrypted_path"
actual_backup_sha256="$(sha256sum "$decrypted_path" | awk '{print $1}')"
[[ "$actual_backup_sha256" == "$EXPECTED_BACKUP_SHA256" ]] || {
    echo "decrypted writer witness backup checksum mismatch" >&2
    exit 1
}
remote_command="WRITER_WITNESS_RESTORE_EXPECTED_STATE=$EXPECTED_STATE WRITER_WITNESS_RESTORE_EXPECTED_RECEIPTS=$EXPECTED_RECEIPTS WRITER_WITNESS_RESTORE_EXPECTED_MANIFEST_SHA256=$EXPECTED_MANIFEST_SHA256 WRITER_WITNESS_RESTORE_EXPECTED_BACKUP_SHA256=$EXPECTED_BACKUP_SHA256 WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE=$REQUIRED_CURRENT_STATE WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS=$REQUIRED_CURRENT_RECEIPTS WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256=$REQUIRED_CURRENT_MANIFEST_SHA256 /usr/local/sbin/writer-witness-live-restore --apply-from-stdin"
ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$WITNESS_HOST" "$remote_command" <"$decrypted_path"
