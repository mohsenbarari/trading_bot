#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRMATION_VALUE="install-staging-coin-inference-snapshot-publisher"
PUBLISH_CONFIRMATION_VALUE="publish-staging-no-data-snapshot"
ENVIRONMENT_VALUE="${STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_ENVIRONMENT:-}"

[[ "$ENVIRONMENT_VALUE" == "staging" ]] || {
    printf 'staging_environment_confirmation_required\n' >&2
    exit 2
}
[[ "${STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_CONFIRM:-}" == "$CONFIRMATION_VALUE" ]] || {
    printf 'staging_publisher_install_confirmation_required\n' >&2
    exit 2
}

PROJECT_DIR="${PROJECT_DIR:-/root/trading-bot/trading_bot}"
RUNTIME_ROOT="${STAGING_COIN_INFERENCE_SOURCE_RUNTIME_ROOT:-/srv/trading-bot/production-data/coin-intelligence/private-gold-live}"
SYSTEMD_DIR="${STAGING_COIN_INFERENCE_SYSTEMD_DIR:-/etc/systemd/system}"
BACKUP_ROOT="${STAGING_COIN_INFERENCE_SYSTEMD_BACKUP_ROOT:-/var/backups/trading-bot/systemd}"
INSTALL_LOCK_PATH="${STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_LOCK_PATH:-/run/lock/trading-bot/staging-coin-inference-publisher.install.lock}"
MAXIMUM_AGE_SECONDS="${STAGING_COIN_INFERENCE_MAXIMUM_AGE_SECONDS:-120}"
IMAGE="${STAGING_COIN_INFERENCE_PUBLISHER_IMAGE:-trading_bot_staging_preview:coin-intelligence-preview}"

SERVICE_NAME="coin-intelligence-staging-snapshot-publish.service"
TIMER_NAME="coin-intelligence-staging-snapshot-publish.timer"
DROPIN_NAME="host-python-toman.conf"
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME"
TIMER_PATH="$SYSTEMD_DIR/$TIMER_NAME"
DROPIN_DIR="$SYSTEMD_DIR/$SERVICE_NAME.d"
DROPIN_PATH="$DROPIN_DIR/$DROPIN_NAME"
SOURCE_STORE="$RUNTIME_ROOT/market/market.sqlite3"
SNAPSHOT_PATH="$RUNTIME_ROOT/staging/coin-rates.json"

render_dir=""
backup_dir=""
lock_fd=""
transaction_ready=0
install_mutated=0
install_committed=0
timer_existed=0
timer_was_enabled=0
timer_was_active=0
dropin_dir_existed=0
declare -A unit_existed=()
install_candidates=()

validate_absolute_path() {
    local value="$1"
    [[ "$value" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        && [[ "$value" != *"//"* ]] \
        && [[ "$value" != */./* && "$value" != */. ]] \
        && [[ "$value" != */../* && "$value" != */.. ]] \
        && [[ "$value" != *"%"* ]] || {
        printf 'configured_path_invalid\n' >&2
        exit 2
    }
    [[ "$(realpath -m -- "$value")" == "$value" ]] || {
        printf 'configured_path_not_canonical\n' >&2
        exit 2
    }
}

validate_regular_single_link_file() {
    local path="$1"
    local reason="$2"
    [[ -f "$path" && ! -L "$path" && "$(stat -c '%h' "$path")" == "1" ]] || {
        printf '%s\n' "$reason" >&2
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

capture_prior_state() {
    local label path status
    if [[ ! -e "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]]; then
        install -d -m 0700 -- "$BACKUP_ROOT"
    fi
    [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" \
        && "$(stat -c '%u' "$BACKUP_ROOT")" == "$(id -u)" \
        && "$(stat -c '%a' "$BACKUP_ROOT")" == "700" ]] || {
        printf 'systemd_backup_root_invalid\n' >&2
        return 1
    }
    backup_dir="$(mktemp -d "$BACKUP_ROOT/staging-coin-snapshot-publisher.XXXXXXXX")"
    chmod 0700 "$backup_dir"
    for label in service timer dropin; do
        case "$label" in
            service) path="$SERVICE_PATH" ;;
            timer) path="$TIMER_PATH" ;;
            dropin) path="$DROPIN_PATH" ;;
        esac
        unit_existed["$label"]=0
        if [[ -L "$path" ]]; then
            printf 'existing_unit_symlink_rejected\n' >&2
            return 1
        elif [[ -f "$path" ]]; then
            [[ "$(stat -c '%h' "$path")" == "1" ]] || {
                printf 'existing_unit_hardlink_rejected\n' >&2
                return 1
            }
            install -m 0600 -- "$path" "$backup_dir/$label"
            unit_existed["$label"]=1
        elif [[ -e "$path" ]]; then
            printf 'existing_unit_type_invalid\n' >&2
            return 1
        fi
    done
    if [[ -e "$DROPIN_DIR" || -L "$DROPIN_DIR" ]]; then
        [[ -d "$DROPIN_DIR" && ! -L "$DROPIN_DIR" ]] || {
            printf 'existing_dropin_directory_invalid\n' >&2
            return 1
        }
        dropin_dir_existed=1
    fi
    if [[ "${unit_existed[timer]}" == "1" ]]; then
        timer_existed=1
        status="$(timer_state_code is-enabled)"
        case "$status" in
            0) timer_was_enabled=1 ;;
            1) ;;
            *) printf 'timer_enabled_state_unavailable\n' >&2; return 1 ;;
        esac
        status="$(timer_state_code is-active)"
        case "$status" in
            0) timer_was_active=1 ;;
            3) ;;
            *) printf 'timer_active_state_unavailable\n' >&2; return 1 ;;
        esac
    fi
    transaction_ready=1
}

atomic_install_unit() {
    local source="$1"
    local destination="$2"
    local candidate
    candidate="$(mktemp "$(dirname "$destination")/.${destination##*/}.install.XXXXXXXX")"
    install_candidates+=("$candidate")
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
    [[ ( "$expected_enabled" == "1" && "$status" == "0" ) \
        || ( "$expected_enabled" == "0" && "$status" == "1" ) ]] || return 1
    status="$(timer_state_code is-active)"
    [[ ( "$expected_active" == "1" && "$status" == "0" ) \
        || ( "$expected_active" == "0" && "$status" == "3" ) ]] || return 1
}

restore_prior_state() {
    local label path candidate failed=0
    set +e
    systemctl stop "$TIMER_NAME" >/dev/null 2>&1 || true
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl disable "$TIMER_NAME" >/dev/null 2>&1 || true
    for label in service timer dropin; do
        case "$label" in
            service) path="$SERVICE_PATH" ;;
            timer) path="$TIMER_PATH" ;;
            dropin) path="$DROPIN_PATH" ;;
        esac
        if [[ "${unit_existed[$label]:-0}" == "1" ]]; then
            install -d -m 0755 -- "$(dirname "$path")" || { failed=1; continue; }
            candidate="$(mktemp "$(dirname "$path")/.${path##*/}.rollback.XXXXXXXX")" \
                || { failed=1; continue; }
            install -m 0644 -- "$backup_dir/$label" "$candidate" || failed=1
            mv -fT -- "$candidate" "$path" || failed=1
            rm -f -- "$candidate"
        else
            rm -f -- "$path" || failed=1
        fi
    done
    if [[ "$dropin_dir_existed" == "0" && -d "$DROPIN_DIR" ]]; then
        rmdir -- "$DROPIN_DIR" >/dev/null 2>&1 || true
    fi
    systemctl daemon-reload >/dev/null 2>&1 || failed=1
    if [[ "$timer_existed" == "1" ]]; then
        apply_timer_state "$timer_was_enabled" "$timer_was_active" || failed=1
    fi
    set -e
    return "$failed"
}

cleanup() {
    local status=$?
    local candidate rollback_failed=0
    trap - ERR EXIT INT TERM
    set +e
    if [[ "$transaction_ready" == "1" && "$install_mutated" == "1" \
        && "$install_committed" != "1" ]]; then
        restore_prior_state || rollback_failed=1
        if [[ "$rollback_failed" == "1" ]]; then
            printf 'staging_coin_inference_snapshot_publisher=rollback_incomplete\n' >&2
        else
            printf 'staging_coin_inference_snapshot_publisher=rolled_back\n' >&2
        fi
        [[ "$status" != "0" ]] || status=1
    fi
    for candidate in "${install_candidates[@]:-}"; do
        [[ -n "$candidate" ]] && rm -f -- "$candidate"
    done
    [[ -n "$render_dir" && -d "$render_dir" ]] && rm -rf -- "$render_dir"
    if [[ -n "$lock_fd" ]]; then
        flock -u "$lock_fd" >/dev/null 2>&1 || true
        exec {lock_fd}>&-
    fi
    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$(id -u)" == "0" ]] || { printf 'root_required\n' >&2; exit 2; }
for required_command in flock install mktemp mv python3 realpath stat systemctl systemd-analyze; do
    command -v "$required_command" >/dev/null 2>&1 || {
        printf 'required_command_missing=%s\n' "$required_command" >&2
        exit 2
    }
done
[[ "$MAXIMUM_AGE_SECONDS" == "120" ]] || {
    printf 'maximum_age_must_equal_120\n' >&2
    exit 2
}
[[ "$IMAGE" =~ ^[A-Za-z0-9._:/-]+$ && "$IMAGE" == *staging* ]] || {
    printf 'staging_publisher_image_invalid\n' >&2
    exit 2
}
for path in "$PROJECT_DIR" "$RUNTIME_ROOT" "$SYSTEMD_DIR" "$BACKUP_ROOT" "$INSTALL_LOCK_PATH"; do
    validate_absolute_path "$path"
done
[[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] || { printf 'project_dir_invalid\n' >&2; exit 2; }
validate_regular_single_link_file \
    "$PROJECT_DIR/scripts/publish_coin_intelligence_snapshot.py" \
    "publisher_script_invalid"
for template in \
    "$PROJECT_DIR/deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.service.template" \
    "$PROJECT_DIR/deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.timer" \
    "$PROJECT_DIR/deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.service.d/host-python-toman.conf.template"; do
    validate_regular_single_link_file "$template" "publisher_unit_template_invalid"
done
[[ -d "$RUNTIME_ROOT" && ! -L "$RUNTIME_ROOT" \
    && "$RUNTIME_ROOT" == */production-data/coin-intelligence/private-gold-live \
    && "$RUNTIME_ROOT" != *production-runtime* ]] || {
    printf 'staging_source_runtime_scope_invalid\n' >&2
    exit 2
}
validate_regular_single_link_file "$SOURCE_STORE" "market_store_invalid"
[[ -d "$(dirname "$SNAPSHOT_PATH")" && ! -L "$(dirname "$SNAPSHOT_PATH")" ]] || {
    printf 'staging_snapshot_directory_invalid\n' >&2
    exit 2
}
[[ -d "$SYSTEMD_DIR" && ! -L "$SYSTEMD_DIR" \
    && "$(stat -c '%u' "$SYSTEMD_DIR")" == "$(id -u)" ]] || {
    printf 'systemd_directory_invalid\n' >&2
    exit 2
}
if [[ -e "$DROPIN_DIR" || -L "$DROPIN_DIR" ]]; then
    [[ -d "$DROPIN_DIR" && ! -L "$DROPIN_DIR" ]] || {
        printf 'existing_dropin_directory_invalid\n' >&2
        exit 2
    }
    for existing_dropin in "$DROPIN_DIR"/* "$DROPIN_DIR"/.[!.]* "$DROPIN_DIR"/..?*; do
        [[ -e "$existing_dropin" || -L "$existing_dropin" ]] || continue
        [[ "$existing_dropin" == "$DROPIN_PATH" ]] || {
            printf 'unexpected_publisher_dropin_rejected\n' >&2
            exit 2
        }
    done
fi

lock_dir="$(dirname "$INSTALL_LOCK_PATH")"
if [[ ! -e "$lock_dir" && ! -L "$lock_dir" ]]; then
    install -d -m 0700 -- "$lock_dir"
fi
[[ -d "$lock_dir" && ! -L "$lock_dir" \
    && "$(stat -c '%u' "$lock_dir")" == "$(id -u)" \
    && "$(stat -c '%a' "$lock_dir")" == "700" ]] || {
    printf 'install_lock_directory_invalid\n' >&2
    exit 2
}
if [[ ! -e "$INSTALL_LOCK_PATH" ]]; then
    (set -o noclobber; umask 077; : >"$INSTALL_LOCK_PATH") 2>/dev/null || true
fi
[[ -f "$INSTALL_LOCK_PATH" && ! -L "$INSTALL_LOCK_PATH" \
    && "$(stat -c '%u' "$INSTALL_LOCK_PATH")" == "$(id -u)" \
    && "$(stat -c '%a' "$INSTALL_LOCK_PATH")" == "600" \
    && "$(stat -c '%h' "$INSTALL_LOCK_PATH")" == "1" ]] || {
    printf 'install_lock_invalid\n' >&2
    exit 2
}
exec {lock_fd}<>"$INSTALL_LOCK_PATH"
flock -n "$lock_fd" || { printf 'staging_publisher_install_locked\n' >&2; exit 75; }

render_dir="$(mktemp -d)"
rendered_service="$render_dir/$SERVICE_NAME"
rendered_timer="$render_dir/$TIMER_NAME"
rendered_dropin_dir="$render_dir/$SERVICE_NAME.d"
rendered_dropin="$rendered_dropin_dir/$DROPIN_NAME"
install -d -m 0755 -- "$rendered_dropin_dir"

service_content="$(<"$PROJECT_DIR/deploy/coin_intelligence/systemd/$SERVICE_NAME.template")"
service_content="${service_content//@RUNTIME_ROOT@/$RUNTIME_ROOT}"
service_content="${service_content//@IMAGE@/$IMAGE}"
printf '%s\n' "$service_content" >"$rendered_service"
install -m 0644 -- \
    "$PROJECT_DIR/deploy/coin_intelligence/systemd/$TIMER_NAME" \
    "$rendered_timer"
dropin_content="$(<"$PROJECT_DIR/deploy/coin_intelligence/systemd/$SERVICE_NAME.d/$DROPIN_NAME.template")"
dropin_content="${dropin_content//@PROJECT_DIR@/$PROJECT_DIR}"
dropin_content="${dropin_content//@RUNTIME_ROOT@/$RUNTIME_ROOT}"
printf '%s\n' "$dropin_content" >"$rendered_dropin"

for rendered in "$rendered_service" "$rendered_timer" "$rendered_dropin"; do
    [[ "$(<"$rendered")" != *"@"* ]] || { printf 'unit_placeholder_unresolved\n' >&2; exit 2; }
done
grep -Fq -- "--environment staging --confirm $PUBLISH_CONFIRMATION_VALUE" "$rendered_dropin" || {
    printf 'publisher_staging_authority_missing\n' >&2
    exit 2
}
grep -Fq -- "--maximum-age-seconds" "$rendered_dropin" && {
    printf 'publisher_unit_must_not_override_freshness_check\n' >&2
    exit 2
}
systemd-analyze verify "$rendered_service" "$rendered_timer" >/dev/null 2>&1

capture_prior_state
install_mutated=1
if [[ "$timer_existed" == "1" && "$timer_was_active" == "1" ]]; then
    systemctl stop "$TIMER_NAME" >/dev/null 2>&1
fi
systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
install -d -m 0755 -- "$DROPIN_DIR"
atomic_install_unit "$rendered_service" "$SERVICE_PATH"
atomic_install_unit "$rendered_timer" "$TIMER_PATH"
atomic_install_unit "$rendered_dropin" "$DROPIN_PATH"
systemctl daemon-reload >/dev/null 2>&1
systemd-analyze verify "$SERVICE_PATH" "$TIMER_PATH" >/dev/null 2>&1
effective_success_exit_status="$(
    systemctl show "$SERVICE_NAME" --property=SuccessExitStatus --value
)" || {
    printf 'publisher_success_exit_status_unavailable\n' >&2
    exit 2
}
[[ -z "${effective_success_exit_status//[[:space:]]/}" ]] || {
    printf 'publisher_nonzero_success_exit_status_rejected\n' >&2
    exit 2
}
effective_exec_start="$(
    systemctl show "$SERVICE_NAME" --property=ExecStart --value
)" || {
    printf 'publisher_effective_exec_start_unavailable\n' >&2
    exit 2
}
[[ "$effective_exec_start" == *"$PROJECT_DIR/scripts/publish_coin_intelligence_snapshot.py publish"* \
    && "$effective_exec_start" == *"--runtime-root $RUNTIME_ROOT"* \
    && "$effective_exec_start" == *"--snapshot staging/coin-rates.json"* \
    && "$effective_exec_start" == *"--publish-staging-no-data-snapshot"* \
    && "$effective_exec_start" == *"--environment staging --confirm $PUBLISH_CONFIRMATION_VALUE"* \
    && "$effective_exec_start" != *"--environment production"* ]] || {
    printf 'publisher_effective_exec_start_invalid\n' >&2
    exit 2
}
systemctl start "$SERVICE_NAME" >/dev/null 2>&1

snapshot_report="$(
    python3 "$PROJECT_DIR/scripts/publish_coin_intelligence_snapshot.py" check \
        --runtime-root "$RUNTIME_ROOT" \
        --snapshot staging/coin-rates.json \
        --maximum-age-seconds 120
)" || {
    printf 'published_snapshot_check_failed\n' >&2
    exit 2
}
snapshot_status="$(
    printf '%s' "$snapshot_report" | python3 -c \
        'import json, sys; print(str(json.load(sys.stdin).get("status") or ""))'
)" || {
    printf 'published_snapshot_check_output_invalid\n' >&2
    exit 2
}
case "$snapshot_status" in
    FRESH|FRESH_NO_DATA) ;;
    *) printf 'published_snapshot_state_invalid\n' >&2; exit 2 ;;
esac

if [[ "$timer_existed" == "1" ]]; then
    desired_enabled="$timer_was_enabled"
    desired_active="$timer_was_active"
else
    desired_enabled=1
    desired_active=1
fi
apply_timer_state "$desired_enabled" "$desired_active"
verify_timer_state "$desired_enabled" "$desired_active"
install_committed=1
printf 'staging_coin_inference_snapshot_publisher=installed snapshot_status=%s backup_retained=true prior_timer_state_preserved=true\n' "$snapshot_status"
