#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "writer witness live restore must run as root" >&2
    exit 2
fi
if [[ "${1:-}" != "--apply-from-stdin" || "$#" -ne 1 ]]; then
    echo "usage: writer-witness-live-restore --apply-from-stdin" >&2
    exit 2
fi

EXPECTED_STATE="${WRITER_WITNESS_RESTORE_EXPECTED_STATE:-}"
EXPECTED_RECEIPTS="${WRITER_WITNESS_RESTORE_EXPECTED_RECEIPTS:-}"
REQUIRED_CURRENT_STATE="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE:-}"
REQUIRED_CURRENT_RECEIPTS="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS:-}"
for state in "$EXPECTED_STATE" "$REQUIRED_CURRENT_STATE"; do
    if [[ ! "$state" =~ ^webapp:[0-9]+:[a-z_]+$ ]]; then
        echo "writer witness restore state guard is missing or unsafe" >&2
        exit 2
    fi
done
for count in "$EXPECTED_RECEIPTS" "$REQUIRED_CURRENT_RECEIPTS"; do
    if [[ ! "$count" =~ ^[0-9]+$ ]]; then
        echo "writer witness restore receipt guard is missing or unsafe" >&2
        exit 2
    fi
done

secrets_file=/etc/trading-bot-witness/bootstrap-secrets.env
if [[ ! -f "$secrets_file" || $(stat -c '%a' "$secrets_file") != 600 ]]; then
    echo "writer witness bootstrap secrets are missing or unsafe" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$secrets_file"
if [[ -z "${WITNESS_DB_MIGRATOR_PASSWORD:-}" ]]; then
    echo "writer witness migrator credential is missing" >&2
    exit 2
fi
for command in pg_restore psql runuser systemctl curl; do
    command -v "$command" >/dev/null || {
        echo "missing restore command: $command" >&2
        exit 2
    }
done

umask 077
install -d -m 0700 -o root -g root /var/backups/trading-bot-witness
input_path="$(mktemp /var/backups/trading-bot-witness/.replacement-restore.XXXXXXXX.dump)"
suffix="$(date -u +%Y%m%d%H%M%S)_$$"
candidate_database="writer_witness_candidate_$suffix"
rollback_database="writer_witness_rollback_$suffix"
failed_database="writer_witness_failed_$suffix"
switched=false

database_exists() {
    local database_name="$1"
    [[ "$database_name" =~ ^writer_witness_[a-z]+_[0-9_]+$ ]] || return 1
    runuser -u postgres -- psql -XAt postgres \
        -c "SELECT 1 FROM pg_database WHERE datname = '$database_name'" | grep -qx 1
}

restore_previous_database() {
    set +e
    systemctl stop writer-witness.service
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres <<SQL
ALTER DATABASE writer_witness WITH ALLOW_CONNECTIONS false;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'writer_witness';
ALTER DATABASE writer_witness RENAME TO $failed_database;
ALTER DATABASE $rollback_database RENAME TO writer_witness;
ALTER DATABASE writer_witness WITH ALLOW_CONNECTIONS true;
SQL
    systemctl start writer-witness.service
}

cleanup() {
    status=$?
    if [[ "$status" -ne 0 ]]; then
        if [[ "$switched" == "true" ]]; then
            restore_previous_database
        elif database_exists "$candidate_database"; then
            runuser -u postgres -- dropdb --if-exists "$candidate_database" >/dev/null 2>&1 || true
        fi
    fi
    rm -f "$input_path"
    exit "$status"
}
trap cleanup EXIT

cat >"$input_path"
chmod 0600 "$input_path"
input_size="$(stat -c '%s' "$input_path")"
if [[ "$input_size" -lt 1 || "$input_size" -gt 67108864 ]]; then
    echo "writer witness restore input has an unsafe size" >&2
    exit 1
fi
pg_restore --list "$input_path" >/dev/null

current_state="$(runuser -u postgres -- psql -XAt writer_witness -c \
    "SELECT authority || ':' || writer_epoch || ':' || lease_status FROM webapp_writer_witness_state")"
current_receipts="$(runuser -u postgres -- psql -XAt writer_witness -c \
    'SELECT count(*) FROM webapp_writer_witness_receipts')"
if [[ "$current_state" != "$REQUIRED_CURRENT_STATE" || "$current_receipts" != "$REQUIRED_CURRENT_RECEIPTS" ]]; then
    echo "current writer witness state failed replacement restore guard" >&2
    exit 1
fi

runuser -u postgres -- createdb \
    --owner=writer_witness_migrator \
    --template=template0 \
    "$candidate_database"
PGPASSWORD="$WITNESS_DB_MIGRATOR_PASSWORD" pg_restore \
    --exit-on-error \
    --no-owner \
    --no-privileges \
    --host=127.0.0.1 \
    --username=writer_witness_migrator \
    --dbname="$candidate_database" \
    "$input_path"

candidate_version="$(runuser -u postgres -- psql -XAt "$candidate_database" -c \
    'SELECT version_num FROM writer_witness_schema_version')"
candidate_state="$(runuser -u postgres -- psql -XAt "$candidate_database" -c \
    "SELECT authority || ':' || writer_epoch || ':' || lease_status FROM webapp_writer_witness_state")"
candidate_receipts="$(runuser -u postgres -- psql -XAt "$candidate_database" -c \
    'SELECT count(*) FROM webapp_writer_witness_receipts')"
if [[ "$candidate_version" != "001" || "$candidate_state" != "$EXPECTED_STATE" || "$candidate_receipts" != "$EXPECTED_RECEIPTS" ]]; then
    echo "restored writer witness candidate failed state guard" >&2
    exit 1
fi

runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 "$candidate_database" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO writer_witness_runtime;
GRANT SELECT ON writer_witness_schema_version TO writer_witness_runtime;
GRANT SELECT, UPDATE ON webapp_writer_witness_state TO writer_witness_runtime;
GRANT SELECT, INSERT ON webapp_writer_witness_receipts TO writer_witness_runtime;
SQL
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres -v candidate="$candidate_database" <<'SQL'
REVOKE ALL ON DATABASE :"candidate" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"candidate" TO writer_witness_migrator, writer_witness_runtime;
SQL

systemctl stop writer-witness.service
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres <<SQL
ALTER DATABASE writer_witness WITH ALLOW_CONNECTIONS false;
ALTER DATABASE $candidate_database WITH ALLOW_CONNECTIONS false;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('writer_witness', '$candidate_database');
ALTER DATABASE writer_witness RENAME TO $rollback_database;
ALTER DATABASE $candidate_database RENAME TO writer_witness;
ALTER DATABASE writer_witness WITH ALLOW_CONNECTIONS true;
SQL
switched=true
systemctl start writer-witness.service

for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error http://127.0.0.1:8011/health/ready >/dev/null; then
        break
    fi
    if [[ "$attempt" -eq 30 ]]; then
        echo "restored writer witness did not become ready" >&2
        exit 1
    fi
    sleep 1
done

live_state="$(runuser -u postgres -- psql -XAt writer_witness -c \
    "SELECT authority || ':' || writer_epoch || ':' || lease_status FROM webapp_writer_witness_state")"
live_receipts="$(runuser -u postgres -- psql -XAt writer_witness -c \
    'SELECT count(*) FROM webapp_writer_witness_receipts')"
if [[ "$live_state" != "$EXPECTED_STATE" || "$live_receipts" != "$EXPECTED_RECEIPTS" ]]; then
    echo "live writer witness failed post-restore state guard" >&2
    exit 1
fi
post_restore_backup="$(/usr/local/sbin/writer-witness-backup)"
sha256sum --check "$post_restore_backup.sha256" >/dev/null

trap - EXIT
rm -f "$input_path"
printf '{"status":"restored-live-dark","schema_version":"%s","state":"%s","receipt_count":%s,"rollback_database":"%s","post_restore_backup_created":true}\n' \
    "$candidate_version" "$live_state" "$live_receipts" "$rollback_database"
