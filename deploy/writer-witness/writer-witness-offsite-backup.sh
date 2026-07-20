#!/bin/bash
set -Eeuo pipefail
set +x
[[ "$-" != *x* ]] || { echo "Writer Witness offsite backup refuses shell tracing" >&2; exit 70; }

PATH=/usr/sbin:/usr/bin:/sbin:/bin
BACKUP_DIR="${WRITER_WITNESS_BACKUP_DIR:-/var/backups/trading-bot-witness}"
RECIPIENT_FILE="${WRITER_WITNESS_OFFSITE_RECIPIENT_FILE:-/etc/trading-bot-witness/offsite-age-recipient.txt}"
S3_PUT_HELPER=/usr/local/sbin/writer-witness-s3-put
MAX_BACKUP_AGE_SECONDS="${WRITER_WITNESS_OFFSITE_MAX_BACKUP_AGE_SECONDS:-129600}"

isolated_system_python() {
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null "$@"
}
s3_put() {
    /usr/bin/env -i \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        WRITER_WITNESS_S3_ENDPOINT="${WRITER_WITNESS_S3_ENDPOINT:?}" \
        WRITER_WITNESS_S3_REGION="${WRITER_WITNESS_S3_REGION:?}" \
        WRITER_WITNESS_S3_BUCKET="${WRITER_WITNESS_S3_BUCKET:?}" \
        WRITER_WITNESS_S3_ACCESS_KEY="${WRITER_WITNESS_S3_ACCESS_KEY:?}" \
        WRITER_WITNESS_S3_SECRET_KEY="${WRITER_WITNESS_S3_SECRET_KEY:?}" \
        /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$S3_PUT_HELPER" "$@"
}

if [[ ! "$MAX_BACKUP_AGE_SECONDS" =~ ^[0-9]+$ ]] \
    || (( MAX_BACKUP_AGE_SECONDS < 3600 || MAX_BACKUP_AGE_SECONDS > 604800 )); then
    echo "writer witness backup maximum age must be between 1 hour and 7 days" >&2
    exit 2
fi

for required in "$RECIPIENT_FILE" "$S3_PUT_HELPER"; do
    if [[ ! -f "$required" ]]; then
        echo "missing writer witness off-site material: $required" >&2
        exit 2
    fi
done
if [[ ! -x "$S3_PUT_HELPER" ]]; then
    echo "writer witness S3 upload helper is not executable" >&2
    exit 2
fi

backup_path="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'writer-witness-*.dump' \
    -printf '%T@ %p\n' | sort -nr | awk 'NR == 1 {print $2}')"
if [[ -z "$backup_path" || ! -f "$backup_path.sha256" ]]; then
    echo "writer witness off-site backup requires a checksumed local backup" >&2
    exit 2
fi
backup_age_seconds=$(( $(date -u +%s) - $(stat -c '%Y' "$backup_path") ))
if (( backup_age_seconds < 0 || backup_age_seconds > MAX_BACKUP_AGE_SECONDS )); then
    echo "writer witness off-site backup refuses a stale or future local dump" >&2
    exit 2
fi
backup_name="$(basename "$backup_path")"
if [[ ! "$backup_name" =~ ^writer-witness-[0-9]{8}T[0-9]{6}Z\.dump$ ]]; then
    echo "writer witness local backup name is unsafe" >&2
    exit 2
fi
sha256sum --check "$backup_path.sha256" >/dev/null
source_sha="$(sha256sum "$backup_path" | awk '{print $1}')"
marker="$backup_path.offsite.json"
if [[ -f "$marker" ]] && isolated_system_python - "$marker" "$source_sha" <<'PY'
from pathlib import Path
import json
import sys

marker = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if marker.get("status") != "uploaded" or marker.get("source_sha256") != sys.argv[2]:
    raise SystemExit(1)
upload = marker.get("upload")
if not isinstance(upload, dict) or not upload.get("version_id"):
    raise SystemExit(1)
PY
then
    printf '{"status":"already_uploaded","source_file":"%s"}\n' "$backup_name"
    exit 0
fi

encrypted_path="$(mktemp "$BACKUP_DIR/.writer-witness-offsite.XXXXXXXX.age")"
cleanup() {
    rm -f "$encrypted_path"
}
trap cleanup EXIT
chmod 0600 "$encrypted_path"
age --encrypt \
    --recipients-file "$RECIPIENT_FILE" \
    --output "$encrypted_path" \
    "$backup_path"

encrypted_name="$backup_name.age"
encrypted_sha="$(sha256sum "$encrypted_path" | awk '{print $1}')"
encrypted_size="$(stat -c '%s' "$encrypted_path")"
object_key="witness/$encrypted_name"
upload_json="$(s3_put --file "$encrypted_path" --key "$object_key")"

marker_tmp="$marker.$$"
isolated_system_python - \
    "$marker_tmp" "$upload_json" "$backup_name" "$source_sha" \
    "$encrypted_name" "$encrypted_sha" "$encrypted_size" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" <<'PY'
from pathlib import Path
import json
import sys

path, upload_raw, source_file, source_sha, encrypted_file, encrypted_sha, size, uploaded_at = sys.argv[1:]
upload = json.loads(upload_raw)
if (
    upload.get("status") != "uploaded"
    or upload.get("object_key") != f"witness/{encrypted_file}"
    or upload.get("sha256") != encrypted_sha
    or upload.get("bytes") != int(size)
    or not upload.get("version_id")
):
    raise SystemExit("S3 upload result did not pass local validation")
payload = {
    "status": "uploaded",
    "source_file": source_file,
    "source_sha256": source_sha,
    "encrypted_file": encrypted_file,
    "encrypted_sha256": encrypted_sha,
    "encrypted_bytes": int(size),
    "uploaded_at": uploaded_at,
    "upload": upload,
}
Path(path).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 0600 "$marker_tmp"
mv -f "$marker_tmp" "$marker"
printf '%s\n' "$upload_json"
