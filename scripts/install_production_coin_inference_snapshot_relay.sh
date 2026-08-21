#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRMATION_VALUE="publish-production-coin-inference-snapshot"
[[ "${PRODUCTION_COIN_INFERENCE_CONFIRM:-}" == "$CONFIRMATION_VALUE" ]] || {
    printf 'production_confirmation_required\n' >&2
    exit 2
}

PROJECT_DIR="${PROJECT_DIR:-/root/trading-bot/trading_bot}"
SOURCE_ROOT="${PRODUCTION_COIN_INFERENCE_SOURCE_ROOT:-/srv/trading-bot/production-data/coin-intelligence/private-gold-live}"
SOURCE_STORE="${PRODUCTION_COIN_INFERENCE_SOURCE_STORE:-$SOURCE_ROOT/market/market.sqlite3}"
LOCAL_ROOT="${PRODUCTION_COIN_INFERENCE_RUNTIME_ROOT:-/srv/trading-bot/production-data/coin-intelligence/production-runtime}"
LOCAL_SNAPSHOT="${PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_PATH:-$LOCAL_ROOT/coin-rates.json}"
REMOTE_HOST="${PRODUCTION_COIN_INFERENCE_REMOTE_HOST:-}"
REMOTE_PORT="${PRODUCTION_COIN_INFERENCE_REMOTE_PORT:-}"
REMOTE_ROOT="${PRODUCTION_COIN_INFERENCE_REMOTE_RUNTIME_ROOT:-/srv/trading-bot/production-data/coin-intelligence/production-runtime}"
REMOTE_SNAPSHOT="${PRODUCTION_COIN_INFERENCE_REMOTE_SNAPSHOT:-$REMOTE_ROOT/coin-rates.json}"
REMOTE_PROJECT_DIR="${PRODUCTION_COIN_INFERENCE_REMOTE_PROJECT_DIR:-}"
REMOTE_IDENTITY_FILE="${PRODUCTION_COIN_INFERENCE_REMOTE_IDENTITY_FILE:-}"
MAXIMUM_AGE_SECONDS="${PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS:-120}"
SYSTEMD_DIR="/etc/systemd/system"
BACKUP_ROOT="/var/backups/trading-bot/systemd"
PRODUCTION_OPERATION_LOCK_DIR="/root/secure-envs/trading-bot/queue-cutover-artifacts"
PRODUCTION_OPERATION_LOCK_PATH="$PRODUCTION_OPERATION_LOCK_DIR/production-release.lock"
PRODUCTION_SOURCE_LOCK_PATH="/root/secure-envs/trading-bot/.production-runtime-source.lock"
SERVICE_NAME="coin-intelligence-production-snapshot-relay.service"
TIMER_NAME="coin-intelligence-production-snapshot-relay.timer"
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME"
TIMER_PATH="$SYSTEMD_DIR/$TIMER_NAME"

render_dir=""
BACKUP_DIR=""
SERVICE_EXISTED=0
TIMER_EXISTED=0
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
TRANSACTION_READY=0
INSTALL_MUTATED=0
INSTALL_COMMITTED=0
INSTALL_CANDIDATES=()
INSTALL_LOCK_OWNED=0
SOURCE_LOCK_FD=""
SOURCE_LOCK_OWNED=0

acquire_install_locks() {
    command -v flock >/dev/null 2>&1 || { printf 'flock_required\n' >&2; exit 2; }
    if [[ ! -e "$PRODUCTION_OPERATION_LOCK_DIR" && ! -L "$PRODUCTION_OPERATION_LOCK_DIR" ]]; then
        install -d -m 0700 -- "$PRODUCTION_OPERATION_LOCK_DIR"
    fi
    [[ -d "$PRODUCTION_OPERATION_LOCK_DIR" && ! -L "$PRODUCTION_OPERATION_LOCK_DIR" \
        && "$(stat -c '%u' "$PRODUCTION_OPERATION_LOCK_DIR")" == "$(id -u)" \
        && "$(stat -c '%a' "$PRODUCTION_OPERATION_LOCK_DIR")" == "700" ]] || {
        printf 'production_operation_lock_directory_invalid\n' >&2
        exit 2
    }
    if [[ ! -e "$PRODUCTION_SOURCE_LOCK_PATH" ]]; then
        (set -o noclobber; umask 077; : >"$PRODUCTION_SOURCE_LOCK_PATH") 2>/dev/null || true
    fi
    [[ -f "$PRODUCTION_SOURCE_LOCK_PATH" && ! -L "$PRODUCTION_SOURCE_LOCK_PATH" \
        && "$(stat -c '%u' "$PRODUCTION_SOURCE_LOCK_PATH")" == "$(id -u)" \
        && "$(stat -c '%a' "$PRODUCTION_SOURCE_LOCK_PATH")" == "600" \
        && "$(stat -c '%h' "$PRODUCTION_SOURCE_LOCK_PATH")" == "1" ]] || {
        printf 'production_source_lock_invalid\n' >&2
        exit 2
    }
    if [[ "${PRODUCTION_INSTALL_LOCK_INHERITED:-}" == "verified-release-held-lock" ]]; then
        [[ -f "$PRODUCTION_OPERATION_LOCK_PATH" && ! -L "$PRODUCTION_OPERATION_LOCK_PATH" \
            && "$(stat -c '%u' "$PRODUCTION_OPERATION_LOCK_PATH")" == "$(id -u)" \
            && "$(stat -c '%a' "$PRODUCTION_OPERATION_LOCK_PATH")" == "600" \
            && "$(stat -c '%h' "$PRODUCTION_OPERATION_LOCK_PATH")" == "1" ]] || {
            printf 'inherited_production_operation_lock_missing\n' >&2
            exit 2
        }
        local probe_fd
        exec {probe_fd}<>"$PRODUCTION_SOURCE_LOCK_PATH"
        if flock -n "$probe_fd"; then
            flock -u "$probe_fd" >/dev/null 2>&1 || true
            exec {probe_fd}>&-
            printf 'inherited_production_source_lock_not_held\n' >&2
            exit 2
        fi
        exec {probe_fd}>&-
        return 0
    fi
    if ! (set -o noclobber; umask 077; printf '{"owner":"coin-relay-installer"}\n' >"$PRODUCTION_OPERATION_LOCK_PATH") 2>/dev/null; then
        printf 'production_operation_locked\n' >&2
        exit 2
    fi
    chmod 0600 "$PRODUCTION_OPERATION_LOCK_PATH"
    INSTALL_LOCK_OWNED=1
    exec {SOURCE_LOCK_FD}<>"$PRODUCTION_SOURCE_LOCK_PATH"
    flock -n "$SOURCE_LOCK_FD" || { printf 'production_source_locked\n' >&2; exit 2; }
    SOURCE_LOCK_OWNED=1
}

release_install_locks() {
    if [[ "$SOURCE_LOCK_OWNED" == "1" && -n "$SOURCE_LOCK_FD" ]]; then
        flock -u "$SOURCE_LOCK_FD" >/dev/null 2>&1 || true
        exec {SOURCE_LOCK_FD}>&-
        SOURCE_LOCK_FD=""
        SOURCE_LOCK_OWNED=0
    fi
    if [[ "$INSTALL_LOCK_OWNED" == "1" ]]; then
        rm -f -- "$PRODUCTION_OPERATION_LOCK_PATH"
        INSTALL_LOCK_OWNED=0
    fi
}

cleanup_temporary_files() {
    local candidate
    for candidate in "${INSTALL_CANDIDATES[@]:-}"; do
        [[ -n "$candidate" ]] && rm -f -- "$candidate"
    done
    if [[ -n "$render_dir" && -d "$render_dir" ]]; then
        rm -rf -- "$render_dir"
    fi
}

validate_absolute_local_path() {
    local value="$1"
    [[ "$value" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        && [[ "$value" != *"//"* ]] \
        && [[ "$value" != */./* && "$value" != */. ]] \
        && [[ "$value" != */../* && "$value" != */.. ]] \
        && [[ "$value" != *"%"* ]] || {
        printf 'configured_local_path_invalid\n' >&2
        exit 2
    }
    local canonical
    canonical="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' "$value")"
    [[ "$canonical" == "$value" ]] || {
        printf 'configured_local_path_not_canonical\n' >&2
        exit 2
    }
}

validate_absolute_remote_path() {
    local value="$1"
    [[ "$value" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        && [[ "$value" != *"//"* ]] \
        && [[ "$value" != */./* && "$value" != */. ]] \
        && [[ "$value" != */../* && "$value" != */.. ]] \
        && [[ "$value" != *"%"* ]] || {
        printf 'configured_remote_path_invalid\n' >&2
        exit 2
    }
}

timer_state_code() {
    local action="$1"
    local status
    if systemctl "$action" --quiet "$TIMER_NAME" >/dev/null 2>&1; then
        status=0
    else
        status=$?
    fi
    printf '%s' "$status"
}

capture_prior_units_and_timer_state() {
    local unit path status
    if [[ ! -e "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]]; then
        install -d -m 0700 -- "$BACKUP_ROOT"
    fi
    [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" \
        && "$(stat -c '%u' "$BACKUP_ROOT")" == "$(id -u)" \
        && "$(stat -c '%a' "$BACKUP_ROOT")" == "700" ]] || {
        printf 'systemd_backup_root_invalid\n' >&2
        return 1
    }
    BACKUP_DIR="$(mktemp -d "$BACKUP_ROOT/coin-snapshot-relay.XXXXXXXX")"
    chmod 0700 "$BACKUP_DIR"
    for unit in "$SERVICE_NAME" "$TIMER_NAME"; do
        path="$SYSTEMD_DIR/$unit"
        if [[ -L "$path" ]]; then
            printf 'existing_unit_symlink_rejected\n' >&2
            return 1
        elif [[ -f "$path" ]]; then
            install -m 0600 -- "$path" "$BACKUP_DIR/$unit"
            if [[ "$unit" == "$SERVICE_NAME" ]]; then SERVICE_EXISTED=1; else TIMER_EXISTED=1; fi
        elif [[ -e "$path" ]]; then
            printf 'existing_unit_type_invalid\n' >&2
            return 1
        fi
    done
    if [[ "$TIMER_EXISTED" == "1" ]]; then
        status="$(timer_state_code is-enabled)"
        case "$status" in
            0) TIMER_WAS_ENABLED=1 ;;
            1) ;;
            *) printf 'timer_enabled_state_unavailable\n' >&2; return 1 ;;
        esac
        status="$(timer_state_code is-active)"
        case "$status" in
            0) TIMER_WAS_ACTIVE=1 ;;
            3) ;;
            *) printf 'timer_active_state_unavailable\n' >&2; return 1 ;;
        esac
    fi
    TRANSACTION_READY=1
}

atomic_install_unit() {
    local source="$1"
    local destination="$2"
    local candidate
    candidate="$(mktemp "$SYSTEMD_DIR/.${destination##*/}.install.XXXXXXXX")"
    INSTALL_CANDIDATES+=("$candidate")
    install -m 0644 -- "$source" "$candidate"
    mv -fT -- "$candidate" "$destination"
}

apply_timer_state() {
    local enabled="$1"
    local active="$2"
    if [[ "$enabled" == "1" ]]; then
        systemctl enable "$TIMER_NAME" >/dev/null 2>&1
    else
        systemctl disable "$TIMER_NAME" >/dev/null 2>&1
    fi
    if [[ "$active" == "1" ]]; then
        systemctl restart "$TIMER_NAME" >/dev/null 2>&1
    else
        systemctl stop "$TIMER_NAME" >/dev/null 2>&1
    fi
}

verify_timer_state() {
    local expected_enabled="$1"
    local expected_active="$2"
    local status
    status="$(timer_state_code is-enabled)"
    if [[ "$expected_enabled" == "1" ]]; then
        [[ "$status" == "0" ]] || return 1
    else
        [[ "$status" == "1" ]] || return 1
    fi
    status="$(timer_state_code is-active)"
    if [[ "$expected_active" == "1" ]]; then
        [[ "$status" == "0" ]] || return 1
    else
        [[ "$status" == "3" ]] || return 1
    fi
}

restore_prior_units_and_state() {
    local unit existed candidate failed=0
    set +e
    systemctl stop "$TIMER_NAME" >/dev/null 2>&1 || failed=1
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl disable "$TIMER_NAME" >/dev/null 2>&1 || true
    for unit in "$SERVICE_NAME" "$TIMER_NAME"; do
        if [[ "$unit" == "$SERVICE_NAME" ]]; then existed="$SERVICE_EXISTED"; else existed="$TIMER_EXISTED"; fi
        if [[ "$existed" == "1" ]]; then
            candidate="$(mktemp "$SYSTEMD_DIR/.${unit}.rollback.XXXXXXXX")" || { failed=1; continue; }
            install -m 0644 -- "$BACKUP_DIR/$unit" "$candidate" || failed=1
            mv -fT -- "$candidate" "$SYSTEMD_DIR/$unit" || failed=1
            rm -f -- "$candidate"
        else
            rm -f -- "$SYSTEMD_DIR/$unit" || failed=1
        fi
    done
    systemctl daemon-reload >/dev/null 2>&1 || failed=1
    if [[ "$TIMER_EXISTED" == "1" ]]; then
        apply_timer_state "$TIMER_WAS_ENABLED" "$TIMER_WAS_ACTIVE" || failed=1
    fi
    set -e
    return "$failed"
}

transaction_exit_handler() {
    local status=$?
    local rollback_failed=0
    trap - ERR EXIT INT TERM
    set +e
    if [[ "$TRANSACTION_READY" == "1" && "$INSTALL_MUTATED" == "1" \
        && "$INSTALL_COMMITTED" != "1" ]]; then
        restore_prior_units_and_state || rollback_failed=1
        if [[ "$rollback_failed" == "1" ]]; then
            printf 'production_coin_inference_snapshot_relay=rollback_incomplete\n' >&2
        else
            printf 'production_coin_inference_snapshot_relay=rolled_back\n' >&2
        fi
        [[ "$status" != "0" ]] || status=1
    fi
    cleanup_temporary_files
    release_install_locks
    exit "$status"
}

trap transaction_exit_handler EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$(id -u)" == "0" ]] || { printf 'root_required\n' >&2; exit 2; }
for required_command in flock install mktemp mv python3 scp ssh stat systemctl systemd-analyze; do
    command -v "$required_command" >/dev/null 2>&1 || {
        printf 'required_command_missing=%s\n' "$required_command" >&2
        exit 2
    }
done
acquire_install_locks
[[ -n "$REMOTE_HOST" && -n "$REMOTE_PORT" && -n "$REMOTE_PROJECT_DIR" ]] || {
    printf 'production_remote_manifest_required\n' >&2
    exit 2
}
[[ "$REMOTE_HOST" =~ ^([A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*$ ]] \
    && [[ "$REMOTE_HOST" != *".."* && "$REMOTE_HOST" != *"%"* ]] || {
    printf 'remote_host_invalid\n' >&2
    exit 2
}
[[ "$REMOTE_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( REMOTE_PORT <= 65535 )) || {
    printf 'remote_port_invalid\n' >&2
    exit 2
}
for local_path in \
    "$PROJECT_DIR" "$SOURCE_ROOT" "$SOURCE_STORE" "$LOCAL_ROOT" \
    "$LOCAL_SNAPSHOT" "$SYSTEMD_DIR" "$BACKUP_ROOT"; do
    validate_absolute_local_path "$local_path"
done
for remote_path in "$REMOTE_ROOT" "$REMOTE_SNAPSHOT" "$REMOTE_PROJECT_DIR"; do
    validate_absolute_remote_path "$remote_path"
done
[[ -n "$REMOTE_IDENTITY_FILE" ]] || {
    printf 'remote_identity_file_required\n' >&2
    exit 2
}
validate_absolute_local_path "$REMOTE_IDENTITY_FILE"
[[ -f "$REMOTE_IDENTITY_FILE" && ! -L "$REMOTE_IDENTITY_FILE" ]] || {
    printf 'remote_identity_file_invalid\n' >&2
    exit 2
}
[[ "$(stat -c '%u' "$REMOTE_IDENTITY_FILE")" == "$(id -u)" ]] || {
    printf 'remote_identity_file_owner_invalid\n' >&2
    exit 2
}
identity_mode="$(stat -c '%a' "$REMOTE_IDENTITY_FILE")"
[[ "$identity_mode" == "400" || "$identity_mode" == "600" ]] || {
    printf 'remote_identity_file_permissions_invalid\n' >&2
    exit 2
}
[[ -f "$PROJECT_DIR/scripts/relay_production_coin_inference_snapshot.py" ]] || {
    printf 'relay_script_missing\n' >&2
    exit 2
}
[[ -f "$SOURCE_STORE" && -d "$SYSTEMD_DIR" && ! -L "$SYSTEMD_DIR" \
    && "$(stat -c '%u' "$SYSTEMD_DIR")" == "$(id -u)" ]] || {
    printf 'source_store_or_systemd_dir_missing\n' >&2
    exit 2
}
[[ "$SOURCE_ROOT" == *production* && "$SOURCE_ROOT" != *staging* ]] || {
    printf 'source_scope_invalid\n' >&2
    exit 2
}
[[ "$LOCAL_ROOT" == *production* && "$LOCAL_ROOT" != *staging* ]] || {
    printf 'local_scope_invalid\n' >&2
    exit 2
}
[[ "$REMOTE_ROOT" == *production* && "$REMOTE_ROOT" != *staging* ]] || {
    printf 'remote_scope_invalid\n' >&2
    exit 2
}
[[ "$LOCAL_ROOT" != "$SOURCE_ROOT" ]] || { printf 'runtime_must_be_separate\n' >&2; exit 2; }
[[ "$SOURCE_STORE" == "$SOURCE_ROOT/"* ]] || { printf 'source_store_outside_root\n' >&2; exit 2; }
[[ "$LOCAL_SNAPSHOT" == "$LOCAL_ROOT/"* ]] || { printf 'local_snapshot_outside_root\n' >&2; exit 2; }
[[ "$REMOTE_SNAPSHOT" == "$REMOTE_ROOT/"* ]] || { printf 'remote_snapshot_outside_root\n' >&2; exit 2; }
[[ "$MAXIMUM_AGE_SECONDS" == "120" ]] || {
    printf 'maximum_age_invalid\n' >&2
    exit 2
}

install -d -m 0755 -- "$LOCAL_ROOT"

ssh_probe=(
    ssh -p "$REMOTE_PORT"
    -o StrictHostKeyChecking=accept-new
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o PasswordAuthentication=no
    -o KbdInteractiveAuthentication=no
    -o ConnectTimeout=10
)
ssh_probe+=(-i "$REMOTE_IDENTITY_FILE")
"${ssh_probe[@]}" "$REMOTE_HOST" true >/dev/null 2>&1 || {
    printf 'remote_key_connectivity_failed\n' >&2
    exit 2
}

render_dir="$(mktemp -d)"
tmp_service="$render_dir/$SERVICE_NAME"
tmp_timer="$render_dir/$TIMER_NAME"
identity_argument=" --remote-identity-file $REMOTE_IDENTITY_FILE"

cat >"$tmp_service" <<EOF
[Unit]
Description=Publish and relay the validated production coin-inference Snapshot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
TimeoutStartSec=180
TimeoutStopSec=15
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONDONTWRITEBYTECODE=1
UMask=0022
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadOnlyPaths=$PROJECT_DIR $SOURCE_ROOT
ReadWritePaths=$LOCAL_ROOT
ExecStart=/usr/bin/python3 $PROJECT_DIR/scripts/relay_production_coin_inference_snapshot.py publish-relay --environment production --production-confirmation $CONFIRMATION_VALUE --source-root $SOURCE_ROOT --market-store $SOURCE_STORE --runtime-root $LOCAL_ROOT --snapshot $LOCAL_SNAPSHOT --maximum-age-seconds $MAXIMUM_AGE_SECONDS --remote-host $REMOTE_HOST --remote-port $REMOTE_PORT --remote-runtime-root $REMOTE_ROOT --remote-snapshot $REMOTE_SNAPSHOT --remote-project-dir $REMOTE_PROJECT_DIR$identity_argument
EOF

cat >"$tmp_timer" <<'EOF'
[Unit]
Description=Refresh the production coin-inference Snapshot every 30 seconds

[Timer]
OnBootSec=10s
OnCalendar=*-*-* *:*:05,35
AccuracySec=1s
RandomizedDelaySec=0
Persistent=true
Unit=coin-intelligence-production-snapshot-relay.service

[Install]
WantedBy=timers.target
EOF

systemd-analyze verify "$tmp_service" "$tmp_timer" >/dev/null 2>&1
capture_prior_units_and_timer_state
INSTALL_MUTATED=1
if [[ "$TIMER_EXISTED" == "1" && "$TIMER_WAS_ACTIVE" == "1" ]]; then
    systemctl stop "$TIMER_NAME" >/dev/null 2>&1
fi
atomic_install_unit "$tmp_service" "$SERVICE_PATH"
atomic_install_unit "$tmp_timer" "$TIMER_PATH"
systemctl daemon-reload >/dev/null 2>&1
systemd-analyze verify "$SERVICE_PATH" "$TIMER_PATH" >/dev/null 2>&1
systemctl start "$SERVICE_NAME" >/dev/null 2>&1
if [[ "$TIMER_EXISTED" == "1" ]]; then
    desired_enabled="$TIMER_WAS_ENABLED"
    desired_active="$TIMER_WAS_ACTIVE"
else
    desired_enabled=1
    desired_active=1
fi
apply_timer_state "$desired_enabled" "$desired_active"
verify_timer_state "$desired_enabled" "$desired_active"
INSTALL_COMMITTED=1
printf 'production_coin_inference_snapshot_relay=installed backup_retained=true prior_state_preserved=true\n'
