#!/usr/bin/env bash
set -Eeuo pipefail

STATE_ROOT=/var/lib/trading-bot-witness/restore-state
JOURNAL_PATH="$STATE_ROOT/active.env"
HISTORY_DIR="$STATE_ROOT/history"
BACKUP_DIR=/var/backups/trading-bot-witness
SERVICE=writer-witness.service

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "writer witness live restore must run as root" >&2
    exit 2
fi
if [[ "$#" -ne 1 || ( "$1" != "--apply-from-stdin" && "$1" != "--recover" ) ]]; then
    echo "usage: writer-witness-live-restore --apply-from-stdin|--recover" >&2
    exit 2
fi
mode="$1"

for command in pg_restore psql runuser systemctl curl sha256sum sync; do
    command -v "$command" >/dev/null || {
        echo "missing restore command: $command" >&2
        exit 2
    }
done
if [[ ! -x /usr/local/sbin/writer-witness-state-manifest ]]; then
    echo "writer witness state manifest helper is missing" >&2
    exit 2
fi

umask 077
install -d -m 0700 -o root -g root "$BACKUP_DIR" "$STATE_ROOT" "$HISTORY_DIR"

database_exists() {
    local database_name="$1"
    [[ "$database_name" =~ ^writer_witness(_[a-z]+_[0-9_]+)?$ ]] || return 1
    runuser -u postgres -- psql -XAt postgres \
        -c "SELECT 1 FROM pg_database WHERE datname = '$database_name'" | grep -qx 1
}

database_oid() {
    local database_name="$1"
    [[ "$database_name" =~ ^writer_witness(_[a-z]+_[0-9_]+)?$ ]] || return 1
    runuser -u postgres -- psql -XAt postgres \
        -c "SELECT oid FROM pg_database WHERE datname = '$database_name'"
}

database_by_oid() {
    local oid="$1"
    [[ "$oid" =~ ^[0-9]+$ ]] || return 1
    runuser -u postgres -- psql -XAt postgres \
        -c "SELECT datname FROM pg_database WHERE oid = $oid"
}

set_allow_connections() {
    local database_name="$1"
    local allowed="$2"
    [[ "$database_name" =~ ^writer_witness(_[a-z]+_[0-9_]+)?$ ]] || return 1
    [[ "$allowed" == "true" || "$allowed" == "false" ]] || return 1
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
        -c "ALTER DATABASE $database_name WITH ALLOW_CONNECTIONS $allowed;"
}

terminate_database() {
    local database_name="$1"
    [[ "$database_name" =~ ^writer_witness(_[a-z]+_[0-9_]+)?$ ]] || return 1
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$database_name';" \
        >/dev/null
}

wait_ready() {
    local attempt
    for attempt in $(seq 1 30); do
        if curl --fail --silent --show-error http://127.0.0.1:8011/health/ready >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

state_value() {
    local database_name="$1"
    runuser -u postgres -- psql -XAt "$database_name" -c \
        "SELECT authority || ':' || writer_epoch || ':' || lease_status FROM webapp_writer_witness_state"
}

receipt_count() {
    local database_name="$1"
    runuser -u postgres -- psql -XAt "$database_name" -c \
        'SELECT count(*) FROM webapp_writer_witness_receipts'
}

manifest_hash() {
    /usr/local/sbin/writer-witness-state-manifest --database "$1"
}

validate_loaded_journal() {
    [[ "${journal_version:-}" == "1" ]]
    [[ "${phase:-}" =~ ^[a-z_]+$ ]]
    for value in "$candidate_database" "$rollback_database" "$failed_database"; do
        [[ "$value" =~ ^writer_witness_[a-z]+_[0-9_]+$ ]]
    done
    [[ "$current_oid" =~ ^[0-9]+$ && "$candidate_oid" =~ ^[0-9]+$ ]]
    for value in "$required_current_state" "$expected_state"; do
        [[ "$value" =~ ^webapp:[0-9]+:[a-z_]+$ ]]
    done
    for value in "$required_current_receipts" "$expected_receipts"; do
        [[ "$value" =~ ^[0-9]+$ ]]
    done
    for value in "$required_current_manifest" "$expected_manifest" "$input_sha256"; do
        [[ "$value" =~ ^[0-9a-f]{64}$ ]]
    done
}

load_journal() {
    [[ -f "$JOURNAL_PATH" && $(stat -c '%a' "$JOURNAL_PATH") == 600 ]] || {
        echo "writer witness restore journal is missing or unsafe" >&2
        return 1
    }
    # The journal is generated only from validated identifiers and digests.
    # shellcheck disable=SC1090
    source "$JOURNAL_PATH"
    validate_loaded_journal || {
        echo "writer witness restore journal failed validation" >&2
        return 1
    }
}

write_journal() {
    local next_phase="$1"
    [[ "$next_phase" =~ ^[a-z_]+$ ]] || return 1
    phase="$next_phase"
    local temporary
    temporary="$(mktemp "$STATE_ROOT/.active.XXXXXXXX.env")"
    {
        printf 'journal_version=1\n'
        printf 'phase=%q\n' "$phase"
        printf 'candidate_database=%q\n' "$candidate_database"
        printf 'rollback_database=%q\n' "$rollback_database"
        printf 'failed_database=%q\n' "$failed_database"
        printf 'current_oid=%q\n' "$current_oid"
        printf 'candidate_oid=%q\n' "$candidate_oid"
        printf 'required_current_state=%q\n' "$required_current_state"
        printf 'required_current_receipts=%q\n' "$required_current_receipts"
        printf 'required_current_manifest=%q\n' "$required_current_manifest"
        printf 'expected_state=%q\n' "$expected_state"
        printf 'expected_receipts=%q\n' "$expected_receipts"
        printf 'expected_manifest=%q\n' "$expected_manifest"
        printf 'input_sha256=%q\n' "$input_sha256"
    } >"$temporary"
    chmod 0600 "$temporary"
    sync -f "$temporary"
    mv -f "$temporary" "$JOURNAL_PATH"
    sync -f "$STATE_ROOT"
}

archive_journal() {
    local outcome="$1"
    [[ "$outcome" =~ ^[a-z_]+$ ]] || return 1
    local target="$HISTORY_DIR/restore-$(date -u +%Y%m%dT%H%M%SZ)-$$-$outcome.env"
    mv -f "$JOURNAL_PATH" "$target"
    chmod 0600 "$target"
    sync -f "$HISTORY_DIR"
}

maybe_fail() {
    local point="$1"
    local requested="${WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER:-}"
    if [[ -n "$requested" && "$requested" == "$point" ]]; then
        echo "injected guarded restore failure after $point" >&2
        return 97
    fi
}

recover_from_journal() {
    load_journal
    systemctl stop "$SERVICE" || true

    local live_oid live_name rollback_name
    live_oid="$(database_oid writer_witness || true)"
    live_name="$(database_by_oid "$current_oid" || true)"
    rollback_name="$(database_by_oid "$candidate_oid" || true)"

    if [[ "$live_oid" == "$current_oid" ]]; then
        : # The original database never left the live name.
    elif [[ "$live_oid" == "$candidate_oid" ]]; then
        if database_exists "$failed_database"; then
            echo "restore recovery refused an occupied failed-database name" >&2
            return 1
        fi
        set_allow_connections writer_witness false
        terminate_database writer_witness
        runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
            -c "ALTER DATABASE writer_witness RENAME TO $failed_database;"
        live_name="$(database_by_oid "$current_oid" || true)"
        [[ "$live_name" == "$rollback_database" ]] || {
            echo "restore recovery cannot locate the original database by OID" >&2
            return 1
        }
        runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
            -c "ALTER DATABASE $rollback_database RENAME TO writer_witness;"
    elif [[ -z "$live_oid" ]]; then
        live_name="$(database_by_oid "$current_oid" || true)"
        [[ "$live_name" == "$rollback_database" ]] || {
            echo "restore recovery cannot locate a live or rollback original database" >&2
            return 1
        }
        runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
            -c "ALTER DATABASE $rollback_database RENAME TO writer_witness;"
    else
        echo "restore recovery found an unknown database at the live name" >&2
        return 1
    fi

    for retained in "$candidate_database" "$failed_database"; do
        if database_exists "$retained"; then
            set_allow_connections "$retained" false
            terminate_database "$retained"
        fi
    done
    set_allow_connections writer_witness true
    systemctl start "$SERVICE"
    wait_ready || {
        echo "recovered writer witness did not become ready" >&2
        return 1
    }
    [[ "$(state_value writer_witness)" == "$required_current_state" ]]
    [[ "$(receipt_count writer_witness)" == "$required_current_receipts" ]]
    [[ "$(manifest_hash writer_witness)" == "$required_current_manifest" ]]
    write_journal recovered
    archive_journal recovered
}

if [[ "$mode" == "--recover" ]]; then
    [[ ! -t 0 ]] || true
    recover_from_journal
    printf '%s\n' '{"status":"recovered-previous-live-database","journal_archived":true}'
    exit 0
fi

if [[ -e "$JOURNAL_PATH" ]]; then
    echo "unfinished writer witness restore exists; run --recover first" >&2
    exit 2
fi

expected_state="${WRITER_WITNESS_RESTORE_EXPECTED_STATE:-}"
expected_receipts="${WRITER_WITNESS_RESTORE_EXPECTED_RECEIPTS:-}"
expected_manifest="${WRITER_WITNESS_RESTORE_EXPECTED_MANIFEST_SHA256:-}"
expected_backup_sha256="${WRITER_WITNESS_RESTORE_EXPECTED_BACKUP_SHA256:-}"
required_current_state="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE:-}"
required_current_receipts="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS:-}"
required_current_manifest="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256:-}"
for value in "$expected_state" "$required_current_state"; do
    [[ "$value" =~ ^webapp:[0-9]+:[a-z_]+$ ]] || {
        echo "writer witness restore state guard is missing or unsafe" >&2
        exit 2
    }
done
for value in "$expected_receipts" "$required_current_receipts"; do
    [[ "$value" =~ ^[0-9]+$ ]] || {
        echo "writer witness restore receipt guard is missing or unsafe" >&2
        exit 2
    }
done
for value in "$expected_manifest" "$expected_backup_sha256" "$required_current_manifest"; do
    [[ "$value" =~ ^[0-9a-f]{64}$ ]] || {
        echo "writer witness restore digest guard is missing or unsafe" >&2
        exit 2
    }
done
case "${WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER:-}" in
    ""|service_stopped|current_disabled|current_renamed|candidate_promoted|candidate_enabled|service_started) ;;
    *) echo "unsupported guarded restore failure point" >&2; exit 2 ;;
esac

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

input_path="$(mktemp "$BACKUP_DIR/.replacement-restore.XXXXXXXX.dump")"
suffix="$(date -u +%Y%m%d%H%M%S)_$$"
candidate_database="writer_witness_candidate_$suffix"
rollback_database="writer_witness_rollback_$suffix"
failed_database="writer_witness_failed_$suffix"
current_oid=0
candidate_oid=0
input_sha256=""

cleanup() {
    local status=$?
    trap - EXIT
    if [[ "$status" -ne 0 && -f "$JOURNAL_PATH" ]]; then
        if ! recover_from_journal; then
            echo "automatic restore recovery failed; service remains fail closed" >&2
            status=70
        fi
    elif [[ "$status" -ne 0 ]] && database_exists "$candidate_database"; then
        set_allow_connections "$candidate_database" false || true
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
input_sha256="$(sha256sum "$input_path" | awk '{print $1}')"
[[ "$input_sha256" == "$expected_backup_sha256" ]] || {
    echo "writer witness restore input checksum mismatch" >&2
    exit 1
}
pg_restore --list "$input_path" >/dev/null

observed_current_manifest="$(manifest_hash writer_witness)"
if [[ "$(state_value writer_witness)" != "$required_current_state" \
    || "$(receipt_count writer_witness)" != "$required_current_receipts" \
    || "$observed_current_manifest" != "$required_current_manifest" ]]; then
    echo "current writer witness state failed replacement restore guard" >&2
    exit 1
fi

runuser -u postgres -- createdb --owner=writer_witness_migrator --template=template0 "$candidate_database"
PGPASSWORD="$WITNESS_DB_MIGRATOR_PASSWORD" pg_restore \
    --exit-on-error --no-owner --no-privileges \
    --host=127.0.0.1 --username=writer_witness_migrator \
    --dbname="$candidate_database" "$input_path"

candidate_version="$(runuser -u postgres -- psql -XAt "$candidate_database" -c \
    'SELECT version_num FROM writer_witness_schema_version')"
if [[ "$candidate_version" != "001" \
    || "$(state_value "$candidate_database")" != "$expected_state" \
    || "$(receipt_count "$candidate_database")" != "$expected_receipts" \
    || "$(manifest_hash "$candidate_database")" != "$expected_manifest" ]]; then
    echo "restored writer witness candidate failed full manifest guard" >&2
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

current_oid="$(database_oid writer_witness)"
candidate_oid="$(database_oid "$candidate_database")"
write_journal prepared

systemctl stop "$SERVICE"
write_journal service_stopped
maybe_fail service_stopped

set_allow_connections writer_witness false
terminate_database writer_witness
write_journal current_disabled
maybe_fail current_disabled

set_allow_connections "$candidate_database" false
terminate_database "$candidate_database"
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
    -c "ALTER DATABASE writer_witness RENAME TO $rollback_database;"
write_journal current_renamed
maybe_fail current_renamed

runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
    -c "ALTER DATABASE $candidate_database RENAME TO writer_witness;"
write_journal candidate_promoted
maybe_fail candidate_promoted

set_allow_connections writer_witness true
write_journal candidate_enabled
maybe_fail candidate_enabled

systemctl start "$SERVICE"
write_journal service_started
maybe_fail service_started
wait_ready || {
    echo "restored writer witness did not become ready" >&2
    exit 1
}

live_state="$(state_value writer_witness)"
live_receipts="$(receipt_count writer_witness)"
live_manifest="$(manifest_hash writer_witness)"
if [[ "$live_state" != "$expected_state" \
    || "$live_receipts" != "$expected_receipts" \
    || "$live_manifest" != "$expected_manifest" ]]; then
    echo "live writer witness failed post-restore full manifest guard" >&2
    exit 1
fi
post_restore_backup="$(/usr/local/sbin/writer-witness-backup)"
sha256sum --check "$post_restore_backup.sha256" >/dev/null

write_journal completed
archive_journal completed
trap - EXIT
rm -f "$input_path"
printf '{"status":"restored-live-dark","schema_version":"%s","state":"%s","receipt_count":%s,"manifest_sha256":"%s","rollback_database":"%s","post_restore_backup_created":true,"journal_archived":true}\n' \
    "$candidate_version" "$live_state" "$live_receipts" "$live_manifest" "$rollback_database"
