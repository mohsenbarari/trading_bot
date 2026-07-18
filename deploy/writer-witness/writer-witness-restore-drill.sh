#!/bin/bash
set -Eeuo pipefail
set +x
[[ "$-" != *x* ]] || { echo "Writer Witness restore drill refuses shell tracing" >&2; exit 70; }

BACKUP_DIR="${WRITER_WITNESS_BACKUP_DIR:-/var/backups/trading-bot-witness}"
backup_path="${1:-}"
if [[ -z "$backup_path" ]]; then
    backup_path="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'writer-witness-*.dump' -printf '%T@ %p\n' | sort -nr | awk 'NR == 1 {print $2}')"
fi
if [[ "$backup_path" != "-" && ( -z "$backup_path" || ! -f "$backup_path" ) ]]; then
    echo "writer witness restore drill requires an existing backup" >&2
    exit 2
fi
if [[ "$backup_path" != "-" && -f "$backup_path.sha256" ]]; then
    sha256sum --check "$backup_path.sha256"
fi

suffix="$(date -u +%Y%m%d%H%M%S)-$$"
drill_database="writer_witness_restore_drill_${suffix//-/_}"
cleanup() {
    runuser -u postgres -- dropdb --if-exists "$drill_database" >/dev/null 2>&1 || true
}
trap cleanup EXIT

runuser -u postgres -- createdb --template=template0 "$drill_database"
restore_arguments=(
    --exit-on-error
    --no-owner
    --no-privileges
    --dbname="$drill_database"
)
if [[ "$backup_path" == "-" ]]; then
    runuser -u postgres -- pg_restore "${restore_arguments[@]}"
else
    # The backup is deliberately root-only. Open it in the privileged shell
    # and pass only its bytes to pg_restore running as postgres.
    runuser -u postgres -- pg_restore "${restore_arguments[@]}" <"$backup_path"
fi

version="$(runuser -u postgres -- psql -XAtqc \
    'SELECT version_num FROM writer_witness_schema_version' "$drill_database")"
state="$(runuser -u postgres -- psql -XAtqc \
    "SELECT authority || ':' || writer_epoch || ':' || lease_status FROM webapp_writer_witness_state" \
    "$drill_database")"
receipt_count="$(runuser -u postgres -- psql -XAtqc \
    'SELECT count(*) FROM webapp_writer_witness_receipts' "$drill_database")"

[[ "$version" == "001" ]]
[[ "$state" == webapp:* ]]
[[ "$receipt_count" =~ ^[0-9]+$ ]]
printf '{"status":"passed","schema_version":"%s","state":"%s","receipt_count":%s}\n' \
    "$version" "$state" "$receipt_count"
