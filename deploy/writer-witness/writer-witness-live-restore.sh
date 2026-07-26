#!/bin/bash
set -Eeuo pipefail
set +x
[[ "$-" != *x* ]] || { echo "Writer Witness live restore refuses shell tracing" >&2; exit 70; }

SERVICE=writer-witness.service
INTERNAL_TEST_MODE=false

if [[ "$#" -eq 1 && "$1" == "--test-input-primitive" \
    && "${WRITER_WITNESS_RESTORE_INTERNAL_TEST_MODE:-}" == "1" ]]; then
    INTERNAL_TEST_MODE=true
    mode="$1"
    STATE_ROOT="${WRITER_WITNESS_RESTORE_TEST_STATE_ROOT:-}"
    BACKUP_DIR="${WRITER_WITNESS_RESTORE_TEST_BACKUP_DIR:-}"
    [[ "$STATE_ROOT" == /* && "$BACKUP_DIR" == /* \
        && "$STATE_ROOT" != / && "$BACKUP_DIR" != / \
        && "$STATE_ROOT" != "$BACKUP_DIR" ]] || {
        echo "restore input primitive test roots are unsafe" >&2
        exit 2
    }
else
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        echo "writer witness live restore must run as root" >&2
        exit 2
    fi
    if [[ "$#" -ne 1 || ( "$1" != "--apply-from-stdin" && "$1" != "--recover" ) ]]; then
        echo "usage: writer-witness-live-restore --apply-from-stdin|--recover" >&2
        exit 2
    fi
    mode="$1"
    STATE_ROOT=/var/lib/trading-bot-witness/restore-state
    BACKUP_DIR=/var/backups/trading-bot-witness
fi
JOURNAL_PATH="$STATE_ROOT/active.env"
HISTORY_DIR="$STATE_ROOT/history"

for command in flock ln sha256sum sync; do
    command -v "$command" >/dev/null || {
        echo "missing restore command: $command" >&2
        exit 2
    }
done
if [[ "$INTERNAL_TEST_MODE" != true ]]; then
    for command in pg_restore psql runuser systemctl curl; do
        command -v "$command" >/dev/null || {
            echo "missing restore command: $command" >&2
            exit 2
        }
    done
    if [[ ! -x /usr/local/sbin/writer-witness-state-manifest ]]; then
        echo "writer witness state manifest helper is missing" >&2
        exit 2
    fi
fi

umask 077
for private_directory in "$BACKUP_DIR" "$STATE_ROOT"; do
    if [[ -L "$private_directory" || ( -e "$private_directory" && ! -d "$private_directory" ) ]]; then
        echo "restore private directory is unsafe: $private_directory" >&2
        exit 2
    fi
done
if [[ "$INTERNAL_TEST_MODE" == true ]]; then
    install -d -m 0700 "$BACKUP_DIR" "$STATE_ROOT"
else
    install -d -m 0700 -o root -g root "$BACKUP_DIR" "$STATE_ROOT"
fi
for private_directory in "$BACKUP_DIR" "$STATE_ROOT"; do
    [[ -d "$private_directory" && ! -L "$private_directory" \
        && "$(stat -c '%u' "$private_directory")" == "${EUID:-$(id -u)}" \
        && "$(stat -c '%g' "$private_directory")" == "$(id -g)" \
        && "$(stat -c '%a' "$private_directory")" == 700 ]] || {
        echo "restore private directory ownership is unsafe: $private_directory" >&2
        exit 2
    }
done

# Lock the directory inode rather than a pathname-created lock file. This keeps
# the lock descriptor held by this shell, avoids symlink creation races, and
# serializes apply, recovery, and internal crash probes before any journal or
# restore input is inspected or changed.
exec {RESTORE_LOCK_FD}<"$STATE_ROOT"
if ! flock --exclusive --nonblock "$RESTORE_LOCK_FD"; then
    echo "another writer witness live restore operation is already active" >&2
    exit 75
fi

if [[ -L "$HISTORY_DIR" || ( -e "$HISTORY_DIR" && ! -d "$HISTORY_DIR" ) ]]; then
    echo "restore private directory is unsafe: $HISTORY_DIR" >&2
    exit 2
fi
if [[ "$INTERNAL_TEST_MODE" == true ]]; then
    install -d -m 0700 "$HISTORY_DIR"
else
    install -d -m 0700 -o root -g root "$HISTORY_DIR"
fi
[[ -d "$HISTORY_DIR" && ! -L "$HISTORY_DIR" \
    && "$(stat -c '%u' "$HISTORY_DIR")" == "${EUID:-$(id -u)}" \
    && "$(stat -c '%g' "$HISTORY_DIR")" == "$(id -g)" \
    && "$(stat -c '%a' "$HISTORY_DIR")" == 700 ]] || {
    echo "restore private directory ownership is unsafe: $HISTORY_DIR" >&2
    exit 2
}

database_exists() {
    local database_name="$1"
    [[ "$database_name" =~ ^writer_witness(_(candidate|rollback|failed)_(wwm_[0-9a-f]{12}_)?[0-9_]+)?$ ]] || return 1
    runuser -u postgres -- psql -XAt postgres \
        -c "SELECT 1 FROM pg_database WHERE datname = '$database_name'" | grep -qx 1
}

database_oid() {
    local database_name="$1"
    [[ "$database_name" =~ ^writer_witness(_(candidate|rollback|failed)_(wwm_[0-9a-f]{12}_)?[0-9_]+)?$ ]] || return 1
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
    [[ "$database_name" =~ ^writer_witness(_(candidate|rollback|failed)_(wwm_[0-9a-f]{12}_)?[0-9_]+)?$ ]] || return 1
    [[ "$allowed" == "true" || "$allowed" == "false" ]] || return 1
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
        -c "ALTER DATABASE $database_name WITH ALLOW_CONNECTIONS $allowed;"
}

terminate_database() {
    local database_name="$1"
    [[ "$database_name" =~ ^writer_witness(_(candidate|rollback|failed)_(wwm_[0-9a-f]{12}_)?[0-9_]+)?$ ]] || return 1
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$database_name';" \
        >/dev/null
}

drop_exact_journal_owned_database() {
    local database_name="$1"
    local expected_oid="$2"
    [[ "$database_name" =~ ^writer_witness_(candidate|failed)_(wwm_[0-9a-f]{12}_)?[0-9_]+$ ]] \
        || return 1
    [[ "$expected_oid" =~ ^[0-9]+$ ]] || return 1
    local observed_oid
    observed_oid="$(database_oid "$database_name" || true)"
    [[ -n "$observed_oid" ]] || return 0
    if [[ "$expected_oid" == 0 || "$observed_oid" != "$expected_oid" ]]; then
        echo "restore recovery auxiliary database OID does not match its journal" >&2
        return 1
    fi
    set_allow_connections "$database_name" false
    terminate_database "$database_name"
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
        -c "DROP DATABASE $database_name;"
    ! database_exists "$database_name"
}

candidate_oid_from_operation() {
    local operation="$1"
    [[ "$operation" =~ ^[0-9a-f]{32}$ ]] || return 1
    local prefix_value=$((16#${operation:0:8}))
    # PostgreSQL reserves OIDs below 16384. Keep the selected OID in the
    # positive signed range so shell and psql handling stay unambiguous.
    printf '%s\n' "$((prefix_value % 2000000000 + 16384))"
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
    [[ "${journal_version:-}" == "3" ]]
    [[ "${operation_id:-}" =~ ^[0-9a-f]{32}$ ]]
    [[ "${phase:-}" =~ ^[a-z_]+$ ]]
    for value in "$candidate_database" "$rollback_database" "$failed_database"; do
        [[ "$value" =~ ^writer_witness_(candidate|rollback|failed)_(wwm_[0-9a-f]{12}_)?[0-9_]+$ ]]
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
    validate_input_path "$input_path"
}

validate_input_path() {
    local path="$1"
    local basename
    basename="$(basename -- "$path")"
    [[ "$(dirname -- "$path")" == "$BACKUP_DIR" \
        && "$path" == "$BACKUP_DIR/$basename" \
        && "$basename" =~ ^\.replacement-restore\.(wwm_[0-9a-f]{12}_)?[0-9_]+\.dump$ ]]
}

path_exists() {
    [[ -e "$1" || -L "$1" ]]
}

new_operation_id() {
    local uuid operation
    IFS= read -r uuid </proc/sys/kernel/random/uuid
    operation="${uuid//-/}"
    [[ "$operation" =~ ^[0-9a-f]{32}$ ]] || return 1
    printf '%s\n' "$operation"
}

validate_owned_private_file() {
    local path="$1"
    [[ -f "$path" && ! -L "$path" \
        && "$(stat -c '%u' "$path")" == "${EUID:-$(id -u)}" \
        && "$(stat -c '%g' "$path")" == "$(id -g)" \
        && "$(stat -c '%a' "$path")" == 600 \
        && "$(stat -c '%h' "$path")" == 1 ]]
}

validate_owned_private_file_with_links() {
    local path="$1"
    shift
    [[ -f "$path" && ! -L "$path" \
        && "$(stat -c '%u' "$path")" == "${EUID:-$(id -u)}" \
        && "$(stat -c '%g' "$path")" == "$(id -g)" \
        && "$(stat -c '%a' "$path")" == 600 ]] || return 1
    local observed_links allowed_links
    observed_links="$(stat -c '%h' "$path")"
    for allowed_links in "$@"; do
        [[ "$observed_links" == "$allowed_links" ]] && return 0
    done
    return 1
}

journal_scalar_field() {
    local path="$1"
    local requested="$2"
    local line key value found=""
    while IFS= read -r line; do
        key="${line%%=*}"
        value="${line#*=}"
        if [[ "$key" == "$requested" ]]; then
            [[ -z "$found" ]] || return 1
            found="$value"
        fi
    done <"$path"
    [[ -n "$found" ]] || return 1
    printf '%s\n' "$found"
}

validate_journal_file_for_operation() {
    local path="$1"
    local expected_operation_id="$2"
    shift 2
    validate_owned_private_file_with_links "$path" "$@" || return 1
    [[ "$(journal_scalar_field "$path" operation_id)" == "$expected_operation_id" ]] || return 1
    (
        unset journal_version operation_id phase candidate_database rollback_database
        unset failed_database current_oid candidate_oid required_current_state
        unset required_current_receipts required_current_manifest expected_state
        unset expected_receipts expected_manifest input_sha256 input_path
        # Generated journals contain only schema-validated scalar values.
        # shellcheck disable=SC1090
        source "$path"
        validate_loaded_journal
        [[ "$operation_id" == "$expected_operation_id" ]]
    )
}

load_journal() {
    validate_owned_private_file "$JOURNAL_PATH" || {
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
    [[ "$operation_id" =~ ^[0-9a-f]{32}$ ]] || return 1
    temporary="$(mktemp "$STATE_ROOT/.active.$operation_id.XXXXXXXX.env")"
    {
        printf 'journal_version=3\n'
        printf 'operation_id=%q\n' "$operation_id"
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
        printf 'input_path=%q\n' "$input_path"
    } >"$temporary"
    chmod 0600 "$temporary"
    validate_owned_private_file "$temporary"
    sync -f "$temporary"
    maybe_kill_input_primitive "${phase}_journal_temp_fsynced"
    validate_journal_file_for_operation "$JOURNAL_PATH" "$operation_id" 1 || {
        echo "writer witness restore journal replacement is unsafe" >&2
        rm -f -- "$temporary"
        return 1
    }
    mv -T -f "$temporary" "$JOURNAL_PATH"
    validate_journal_file_for_operation "$JOURNAL_PATH" "$operation_id" 1
    sync -f "$STATE_ROOT"
    maybe_kill_input_primitive "${phase}_journal_replaced"
}

publish_initial_journal() {
    local next_phase="$1"
    [[ "$next_phase" =~ ^[a-z_]+$ ]] || return 1
    phase="$next_phase"
    [[ "$operation_id" =~ ^[0-9a-f]{32}$ ]] || return 1
    local temporary
    temporary="$(mktemp "$STATE_ROOT/.active.$operation_id.XXXXXXXX.env")"
    {
        printf 'journal_version=3\n'
        printf 'operation_id=%q\n' "$operation_id"
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
        printf 'input_path=%q\n' "$input_path"
    } >"$temporary"
    chmod 0600 "$temporary"
    validate_journal_file_for_operation "$temporary" "$operation_id" 1
    sync -f "$temporary"
    maybe_kill_input_primitive "${phase}_journal_temp_fsynced"
    if ! ln -T -- "$temporary" "$JOURNAL_PATH"; then
        validate_journal_file_for_operation "$temporary" "$operation_id" 1 || return 1
        rm -f -- "$temporary"
        sync -f "$STATE_ROOT"
        echo "unfinished writer witness restore already exists" >&2
        return 1
    fi
    sync -f "$STATE_ROOT"
    maybe_kill_input_primitive "${phase}_journal_linked"
    [[ "$temporary" -ef "$JOURNAL_PATH" ]] \
        && validate_journal_file_for_operation "$temporary" "$operation_id" 2 \
        && validate_journal_file_for_operation "$JOURNAL_PATH" "$operation_id" 2
    rm -f -- "$temporary"
    sync -f "$STATE_ROOT"
    validate_journal_file_for_operation "$JOURNAL_PATH" "$operation_id" 1
    maybe_kill_input_primitive "${phase}_journal_published"
}

recover_owned_journal_temps() {
    local active_present=false active_operation_id=""
    local candidate name candidate_operation_id candidate_phase candidate_input
    local -a candidates=()
    while IFS= read -r -d '' candidate; do
        candidates+=("$candidate")
    done < <(find "$STATE_ROOT" -mindepth 1 -maxdepth 1 \
        -name '.active.*.env' -print0)
    ((${#candidates[@]})) || return 0

    if path_exists "$JOURNAL_PATH"; then
        active_present=true
        validate_owned_private_file_with_links "$JOURNAL_PATH" 1 2 || {
            echo "writer witness restore journal is unsafe during temp recovery" >&2
            return 1
        }
        active_operation_id="$(journal_scalar_field "$JOURNAL_PATH" operation_id)" || {
            echo "writer witness restore journal operation identity is unsafe" >&2
            return 1
        }
        [[ "$active_operation_id" =~ ^[0-9a-f]{32}$ ]] \
            && validate_journal_file_for_operation \
                "$JOURNAL_PATH" "$active_operation_id" 1 2 || {
            echo "writer witness restore journal is unsafe during temp recovery" >&2
            return 1
        }
    fi

    for candidate in "${candidates[@]}"; do
        name="${candidate##*/}"
        [[ "$name" =~ ^\.active\.([0-9a-f]{32})\.[A-Za-z0-9_]{8}\.env$ ]] || {
            echo "unrecognized writer witness restore journal temp exists" >&2
            return 1
        }
        candidate_operation_id="${BASH_REMATCH[1]}"
        validate_journal_file_for_operation \
            "$candidate" "$candidate_operation_id" 1 2 || {
            echo "writer witness restore journal temp is not safely owned" >&2
            return 1
        }
        if [[ "$active_present" == true ]]; then
            [[ "$candidate_operation_id" == "$active_operation_id" ]] || {
                echo "foreign writer witness restore journal temp exists" >&2
                return 1
            }
            if [[ "$candidate" -ef "$JOURNAL_PATH" ]]; then
                [[ "$(stat -c '%h' "$candidate")" == 2 ]] || return 1
            else
                [[ "$(stat -c '%h' "$candidate")" == 1 \
                    && "$(stat -c '%h' "$JOURNAL_PATH")" == 1 ]] || return 1
            fi
        else
            [[ "${#candidates[@]}" == 1 \
                && "$(stat -c '%h' "$candidate")" == 1 ]] || {
                echo "ambiguous writer witness restore journal temps exist" >&2
                return 1
            }
            candidate_phase="$(journal_scalar_field "$candidate" phase)"
            candidate_input="$(journal_scalar_field "$candidate" input_path)"
            [[ "$candidate_phase" == intent_recorded ]] \
                && validate_input_path "$candidate_input" \
                && ! path_exists "$candidate_input" || {
                echo "unpublished restore intent temp cannot be reconciled safely" >&2
                return 1
            }
            # The fsynced initial intent is already the only durable owner of
            # the selected input/database names. Publish that exact inode as
            # active instead of discarding its guards; normal --recover can
            # then archive it through the same journaled path without needing
            # caller-supplied state assumptions.
            ln -T -- "$candidate" "$JOURNAL_PATH" || {
                echo "unpublished restore intent could not be promoted safely" >&2
                return 1
            }
            sync -f "$STATE_ROOT"
            [[ "$candidate" -ef "$JOURNAL_PATH" \
                && "$(stat -c '%h' "$candidate")" == 2 ]] \
                && validate_journal_file_for_operation \
                    "$JOURNAL_PATH" "$candidate_operation_id" 2 || {
                echo "promoted restore intent journal is unsafe" >&2
                return 1
            }
            active_present=true
            active_operation_id="$candidate_operation_id"
        fi
    done

    for candidate in "${candidates[@]}"; do
        rm -f -- "$candidate"
    done
    sync -f "$STATE_ROOT"
    if [[ "$active_present" == true ]]; then
        validate_journal_file_for_operation "$JOURNAL_PATH" "$active_operation_id" 1 || {
            echo "writer witness restore journal temp cleanup did not converge" >&2
            return 1
        }
    fi
}

archive_journal() {
    local outcome="$1"
    [[ "$outcome" =~ ^[a-z_]+$ ]] || return 1
    local target="$HISTORY_DIR/restore-$(date -u +%Y%m%dT%H%M%S%N)-$$-$outcome.env"
    validate_owned_private_file "$JOURNAL_PATH"
    ! path_exists "$target"
    sync -f "$JOURNAL_PATH"
    mv -T "$JOURNAL_PATH" "$target"
    maybe_kill_input_primitive journal_moved
    chmod 0600 "$target"
    validate_owned_private_file "$target"
    sync -f "$target"
    sync -f "$HISTORY_DIR"
    sync -f "$STATE_ROOT"
}

validate_owned_input() {
    validate_input_path "$input_path" || return 1
    validate_owned_private_file "$input_path"
}

delete_owned_input() {
    validate_input_path "$input_path" || {
        echo "restore journal input path is unsafe" >&2
        return 1
    }
    if path_exists "$input_path"; then
        validate_owned_input || {
            echo "restore input ownership is ambiguous; refusing deletion" >&2
            return 1
        }
        rm -f -- "$input_path"
        ! path_exists "$input_path" || return 1
        sync -f "$BACKUP_DIR"
    fi
}

maybe_kill_input_primitive() {
    local point="$1"
    if [[ "$INTERNAL_TEST_MODE" == true \
        && "${WRITER_WITNESS_RESTORE_TEST_KILL_AFTER:-}" == "$point" ]]; then
        kill -KILL "$$"
    fi
}

publish_owned_input() {
    [[ "$phase" == intent_recorded ]] && validate_owned_private_file "$JOURNAL_PATH" || {
        echo "restore input publication lacks its durable intent journal" >&2
        return 1
    }
    validate_input_path "$input_path" || return 1
    ! path_exists "$input_path" || {
        echo "restore input path already exists" >&2
        return 1
    }
    local input_fd
    set -o noclobber
    if ! exec {input_fd}>"$input_path"; then
        set +o noclobber
        echo "restore input could not be created exclusively" >&2
        return 1
    fi
    set +o noclobber
    chmod 0600 "$input_path"
    maybe_kill_input_primitive input_opened
    # Bound the durable copy itself. A hostile/accidental oversized stream is
    # rejected after at most 64 MiB reaches disk; the one-byte overflow probe
    # remains on stdin and is never persisted.
    if ! dd iflag=fullblock bs=1048576 count=64 status=none >&"$input_fd"; then
        exec {input_fd}>&-
        echo "restore input stream could not be copied safely" >&2
        return 1
    fi
    local overflow_bytes
    overflow_bytes="$(dd bs=1 count=1 status=none | wc -c)"
    if [[ "$overflow_bytes" != 0 ]]; then
        exec {input_fd}>&-
        echo "writer witness restore input exceeds the 64 MiB durable limit" >&2
        return 1
    fi
    exec {input_fd}>&-
    validate_owned_input
    sync -f "$input_path"
    sync -f "$BACKUP_DIR"
    maybe_kill_input_primitive input_fsynced
}

find_orphan_input() {
    find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 \
        -name '.replacement-restore.*.dump' -print -quit
}

find_foreign_input() {
    local candidate
    while IFS= read -r -d '' candidate; do
        if [[ "$candidate" != "$input_path" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 \
        -name '.replacement-restore.*.dump' -print0)
}

refuse_foreign_inputs() {
    if [[ -n "$(find_foreign_input)" ]]; then
        echo "restore input exists outside the exact active journal ownership" >&2
        return 1
    fi
}

maybe_fail() {
    local point="$1"
    local requested="${WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER:-}"
    if [[ -n "$requested" && "$requested" == "$point" ]]; then
        echo "injected hard-kill restore failure after $point" >&2
        kill -KILL "$$"
        return 137
    fi
}

database_failpoint_is_supported() {
    case "$1" in
        input_validated|candidate_created|candidate_restored|candidate_validated|grants_applied|prepared|service_stopped|current_disabled|current_renamed|candidate_promoted|candidate_enabled|service_started) ;;
        *) return 1 ;;
    esac
}

recover_from_journal() {
    recover_owned_journal_temps
    load_journal
    refuse_foreign_inputs
    if [[ "$phase" == intent_recorded || "$phase" == input_reconciled ]]; then
        delete_owned_input
        write_journal input_reconciled
        maybe_kill_input_primitive input_deleted
        archive_journal recovered_input
        recovery_status=recovered-owned-input
        return 0
    fi
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
    if [[ "$(state_value writer_witness)" != "$required_current_state" \
        || "$(receipt_count writer_witness)" != "$required_current_receipts" \
        || "$(manifest_hash writer_witness)" != "$required_current_manifest" ]]; then
        systemctl stop "$SERVICE" || true
        echo "recovered writer witness failed its exact prior-state guard" >&2
        return 1
    fi
    drop_exact_journal_owned_database "$candidate_database" "$candidate_oid"
    drop_exact_journal_owned_database "$failed_database" "$candidate_oid"
    if database_exists "$rollback_database" \
        || database_exists "$candidate_database" \
        || database_exists "$failed_database"; then
        systemctl stop "$SERVICE" || true
        echo "restore recovery did not converge to its exact database inventory" >&2
        return 1
    fi
    write_journal recovered
    delete_owned_input
    maybe_kill_input_primitive input_deleted
    archive_journal recovered
    recovery_status=recovered-previous-live-database
}

run_internal_input_primitive() {
    local action="${WRITER_WITNESS_RESTORE_TEST_ACTION:-}"
    [[ "$action" == apply || "$action" == recover || "$action" == failpoint ]] || {
        echo "restore input primitive test action is invalid" >&2
        return 2
    }
    if [[ "$action" == recover ]]; then
        if ! path_exists "$JOURNAL_PATH"; then
            if [[ -n "$(find_orphan_input)" ]]; then
                echo "unowned restore input exists without an active journal" >&2
                return 1
            fi
            return 0
        fi
        load_journal
        refuse_foreign_inputs
        delete_owned_input
        write_journal input_reconciled
        maybe_kill_input_primitive input_deleted
        archive_journal recovered_input
        return 0
    fi
    ! path_exists "$JOURNAL_PATH"
    [[ -z "$(find_orphan_input)" ]] || {
        echo "unowned restore input exists without an active journal" >&2
        return 1
    }
    expected_backup_sha256="${WRITER_WITNESS_RESTORE_TEST_EXPECTED_SHA256:-}"
    [[ "$expected_backup_sha256" =~ ^[0-9a-f]{64}$ ]] || return 2
    suffix="20990101000000_$$"
    candidate_database="writer_witness_candidate_$suffix"
    rollback_database="writer_witness_rollback_$suffix"
    failed_database="writer_witness_failed_$suffix"
    current_oid=0
    candidate_oid=0
    required_current_state=webapp:0:vacant
    required_current_receipts=0
    required_current_manifest="$(printf 'a%.0s' {1..64})"
    expected_state=webapp:0:vacant
    expected_receipts=0
    expected_manifest="$(printf 'b%.0s' {1..64})"
    input_sha256="$expected_backup_sha256"
    input_path="$BACKUP_DIR/.replacement-restore.$suffix.dump"
    operation_id="$(new_operation_id)"
    publish_initial_journal intent_recorded
    if [[ "$action" == failpoint ]]; then
        local requested_failpoint="${WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER:-}"
        database_failpoint_is_supported "$requested_failpoint" || return 2
        write_journal input_validated
        maybe_fail "$requested_failpoint"
        return 99
    fi
    maybe_kill_input_primitive intent_recorded
    publish_owned_input
    [[ "$(sha256sum "$input_path" | awk '{print $1}')" == "$expected_backup_sha256" ]]
    write_journal input_validated
    delete_owned_input
    maybe_kill_input_primitive input_deleted
    write_journal input_reconciled
    archive_journal completed
}

recover_owned_journal_temps

if [[ "$INTERNAL_TEST_MODE" == true ]]; then
    run_internal_input_primitive
    exit $?
fi

if [[ "$mode" == "--recover" ]]; then
    [[ ! -t 0 ]] || true
    if ! path_exists "$JOURNAL_PATH"; then
        if [[ -n "$(find_orphan_input)" ]]; then
            echo "unowned restore input exists without an active journal" >&2
            exit 1
        fi
        guard_state="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE:-}"
        guard_receipts="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_RECEIPTS:-}"
        guard_manifest="${WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256:-}"
        orphan_count="$(runuser -u postgres -- psql -XAt postgres -c \
            "SELECT count(*) FROM pg_database WHERE datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%'")"
        enabled_aux="$(runuser -u postgres -- psql -XAt postgres -c \
            "SELECT count(*) FROM pg_database WHERE datname<>'writer_witness' AND datname LIKE 'writer_witness_%' AND datallowconn")"
        if [[ "$guard_state" =~ ^webapp:[0-9]+:[a-z_]+$ \
            && "$guard_receipts" =~ ^[0-9]+$ \
            && "$guard_manifest" =~ ^[0-9a-f]{64}$ ]] \
            && systemctl is-active --quiet "$SERVICE" \
            && wait_ready \
            && [[ "$(state_value writer_witness)" == "$guard_state" ]] \
            && [[ "$(receipt_count writer_witness)" == "$guard_receipts" ]] \
            && [[ "$(manifest_hash writer_witness)" == "$guard_manifest" ]] \
            && [[ "$orphan_count" == 0 && "$enabled_aux" == 0 ]]; then
            printf '%s\n' '{"status":"no-recovery-required","journal_present":false,"service_ready":true,"full_manifest_verified":true}'
            exit 0
        fi
        echo "writer witness has no restore journal but lacks or failed the exact recovery baseline" >&2
        exit 1
    fi
    recovery_status=""
    recover_from_journal
    [[ "$recovery_status" == recovered-owned-input \
        || "$recovery_status" == recovered-previous-live-database ]]
    printf '{"status":"%s","journal_archived":true,"owned_input_reconciled":true}\n' \
        "$recovery_status"
    exit 0
fi

if path_exists "$JOURNAL_PATH"; then
    echo "unfinished writer witness restore exists; run --recover first" >&2
    exit 2
fi
if [[ -n "$(find_orphan_input)" ]]; then
    echo "unowned restore input exists without an active journal" >&2
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
if [[ -n "${WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER:-}" ]] \
    && ! database_failpoint_is_supported "$WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER"; then
    echo "unsupported guarded restore failure point" >&2
    exit 2
fi

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

suffix="$(date -u +%Y%m%d%H%M%S)_$$"
operation_tag="${WRITER_WITNESS_RESTORE_OPERATION_TAG:-}"
if [[ -n "$operation_tag" ]]; then
    [[ "$operation_tag" =~ ^wwm_[0-9a-f]{12}$ ]] || {
        echo "writer witness restore operation tag is unsafe" >&2
        exit 2
    }
    suffix="${operation_tag}_${suffix}"
fi
candidate_database="writer_witness_candidate_$suffix"
rollback_database="writer_witness_rollback_$suffix"
failed_database="writer_witness_failed_$suffix"
input_path="$BACKUP_DIR/.replacement-restore.$suffix.dump"
current_oid=0
candidate_oid=0
input_sha256="$expected_backup_sha256"

for reserved_database in "$candidate_database" "$rollback_database" "$failed_database"; do
    if database_exists "$reserved_database"; then
        echo "writer witness restore database name is already occupied" >&2
        exit 2
    fi
done

for oid_attempt in $(seq 1 32); do
    operation_id="$(new_operation_id)"
    proposed_candidate_oid="$(candidate_oid_from_operation "$operation_id")"
    if [[ -z "$(database_by_oid "$proposed_candidate_oid" || true)" ]]; then
        candidate_oid="$proposed_candidate_oid"
        break
    fi
done
[[ "$candidate_oid" =~ ^[1-9][0-9]+$ ]] || {
    echo "writer witness restore could not reserve an unused candidate database OID" >&2
    exit 2
}

cleanup() {
    local status=$?
    trap - EXIT
    if [[ "$status" -ne 0 ]] && path_exists "$JOURNAL_PATH"; then
        if ! validate_journal_file_for_operation \
            "$JOURNAL_PATH" "$operation_id" 1 2; then
            echo "automatic restore recovery refused a foreign or unsafe journal" >&2
            status=70
        elif ! recover_from_journal; then
            echo "automatic restore recovery failed; service remains fail closed" >&2
            status=70
        fi
    elif [[ "$status" -ne 0 ]] && database_exists "$candidate_database"; then
        set_allow_connections "$candidate_database" false || true
    fi
    exit "$status"
}
trap cleanup EXIT

publish_initial_journal intent_recorded
publish_owned_input
input_size="$(stat -c '%s' "$input_path")"
if [[ "$input_size" -lt 1 || "$input_size" -gt 67108864 ]]; then
    echo "writer witness restore input has an unsafe size" >&2
    exit 1
fi
observed_input_sha256="$(sha256sum "$input_path" | awk '{print $1}')"
[[ "$observed_input_sha256" == "$input_sha256" ]] || {
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

current_oid="$(database_oid writer_witness)"
write_journal input_validated
maybe_fail input_validated

runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres \
    -c "CREATE DATABASE $candidate_database WITH OWNER writer_witness_migrator TEMPLATE template0 OID $candidate_oid;"
[[ "$(database_oid "$candidate_database")" == "$candidate_oid" ]] || {
    echo "writer witness candidate database did not receive its journaled OID" >&2
    exit 1
}
write_journal candidate_created
maybe_fail candidate_created
PGPASSWORD="$WITNESS_DB_MIGRATOR_PASSWORD" pg_restore \
    --exit-on-error --no-owner --no-privileges \
    --host=127.0.0.1 --username=writer_witness_migrator \
    --dbname="$candidate_database" "$input_path"
write_journal candidate_restored
maybe_fail candidate_restored

candidate_version="$(runuser -u postgres -- psql -XAt "$candidate_database" -c \
    'SELECT version_num FROM writer_witness_schema_version')"
if [[ "$candidate_version" != "002" && "$candidate_version" != "003" ]]; then
    echo "restored writer witness candidate has an unsupported schema version" >&2
    exit 1
fi
# The external expected manifest describes the exact backup bytes before any
# controlled schema migration.  Verify it first.  A 002 backup then receives
# only the reviewed 003 migration; the resulting 003 manifest is separately
# pinned for the promote/post-promote checks below.  This keeps old backups
# restorable without weakening their byte-derived state guard.
if [[ "$(state_value "$candidate_database")" != "$expected_state" \
    || "$(receipt_count "$candidate_database")" != "$expected_receipts" \
    || "$(manifest_hash "$candidate_database")" != "$expected_manifest" ]]; then
    echo "restored writer witness candidate failed its source manifest guard" >&2
    exit 1
fi
if [[ "$candidate_version" == "002" ]]; then
    migration=/srv/trading-bot-witness/current/deploy/writer-witness/003_human_approval_relay.sql
    [[ -f "$migration" && ! -L "$migration" ]] || {
        echo "current Writer Witness relay migration is unavailable" >&2
        exit 1
    }
    PGPASSWORD="$WITNESS_DB_MIGRATOR_PASSWORD" psql \
        -Xv ON_ERROR_STOP=1 \
        -h 127.0.0.1 \
        -U writer_witness_migrator \
        -d "$candidate_database" \
        -f "$migration"
    candidate_version="$(runuser -u postgres -- psql -XAt "$candidate_database" -c \
        'SELECT version_num FROM writer_witness_schema_version')"
fi
if [[ "$candidate_version" != "003" \
    || "$(state_value "$candidate_database")" != "$expected_state" \
    || "$(receipt_count "$candidate_database")" != "$expected_receipts" ]]; then
    echo "restored writer witness candidate failed post-migration state guard" >&2
    exit 1
fi
expected_promoted_manifest="$(manifest_hash "$candidate_database")"
[[ "$expected_promoted_manifest" =~ ^[0-9a-f]{64}$ ]] || {
    echo "restored writer witness candidate manifest is invalid after migration" >&2
    exit 1
}
write_journal candidate_validated
maybe_fail candidate_validated

runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 "$candidate_database" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO writer_witness_runtime;
GRANT SELECT ON writer_witness_schema_version TO writer_witness_runtime;
GRANT SELECT, UPDATE ON webapp_writer_witness_state TO writer_witness_runtime;
GRANT SELECT, INSERT ON webapp_writer_witness_receipts TO writer_witness_runtime;
GRANT SELECT, INSERT, UPDATE ON dr_failover_operation_ledger TO writer_witness_runtime;
GRANT SELECT, INSERT ON human_approval_relay_receipts TO writer_witness_runtime;
SQL
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 postgres -v candidate="$candidate_database" <<'SQL'
REVOKE ALL ON DATABASE :"candidate" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"candidate" TO writer_witness_migrator, writer_witness_runtime;
SQL
write_journal grants_applied
maybe_fail grants_applied
write_journal prepared
maybe_fail prepared

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
    || "$live_manifest" != "$expected_promoted_manifest" ]]; then
    echo "live writer witness failed post-restore full manifest guard" >&2
    exit 1
fi
post_restore_backup="$(/usr/local/sbin/writer-witness-backup)"
sha256sum --check "$post_restore_backup.sha256" >/dev/null

write_journal completed
delete_owned_input
maybe_kill_input_primitive input_deleted
archive_journal completed
trap - EXIT
printf '{"status":"restored-live-dark","schema_version":"%s","state":"%s","receipt_count":%s,"manifest_sha256":"%s","rollback_database":"%s","post_restore_backup_created":true,"journal_archived":true}\n' \
    "$candidate_version" "$live_state" "$live_receipts" "$live_manifest" "$rollback_database"
