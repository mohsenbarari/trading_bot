#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRMATION_VALUE="install-coin-intelligence-input-timers"
REPAIR_CONFIRMATION_VALUE="repair-production-coin-input-timers"
[[ "${COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM:-}" == "$CONFIRMATION_VALUE" ]] || {
    printf 'timer_install_confirmation_required\n' >&2
    exit 2
}
CHECK_ONLY="${COIN_INTELLIGENCE_INPUT_TIMERS_CHECK_ONLY:-0}"
[[ "$CHECK_ONLY" == "0" || "$CHECK_ONLY" == "1" ]] || {
    printf 'input_timer_check_only_invalid\n' >&2
    exit 2
}
FORCE_ACTIVE="${COIN_INTELLIGENCE_INPUT_TIMERS_FORCE_ACTIVE:-0}"
[[ "$FORCE_ACTIVE" == "0" || "$FORCE_ACTIVE" == "1" ]] || {
    printf 'input_timer_force_active_invalid\n' >&2
    exit 2
}
REPAIR_CONFIRMATION="${COIN_INTELLIGENCE_INPUT_TIMERS_REPAIR_CONFIRM:-}"
RELEASE_LOCK_INHERITED="${PRODUCTION_INSTALL_LOCK_INHERITED:-}"
if [[ "$FORCE_ACTIVE" == "1" \
    && "$RELEASE_LOCK_INHERITED" != "verified-release-held-lock" \
    && "$REPAIR_CONFIRMATION" != "$REPAIR_CONFIRMATION_VALUE" ]]; then
    # Turning previously-disabled production collectors on is never an
    # installer convenience.  It requires either the release-owned locks or
    # this script's exact, bounded repair authority and self-owned locks.
    printf 'input_timer_force_active_requires_release_lock_or_repair_confirmation\n' >&2
    exit 2
fi
if [[ -n "$REPAIR_CONFIRMATION" \
    && "$REPAIR_CONFIRMATION" != "$REPAIR_CONFIRMATION_VALUE" ]]; then
    printf 'input_timer_repair_confirmation_invalid\n' >&2
    exit 2
fi

PROJECT_DIR="${PROJECT_DIR:-/root/trading-bot/trading_bot}"
UNIT_SOURCE_DIR="$PROJECT_DIR/deploy/coin_intelligence/systemd"
MARKET_RUNTIME_ROOT="${COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT:-/srv/trading-bot/production-data/coin-intelligence/private-gold-live}"
ESTIMATOR_RUNTIME_ROOT="${COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT:-/srv/trading-bot/production-data/coin-intelligence/estimator-live}"
MARKET_ENV="${COIN_INTELLIGENCE_MARKET_ENV:-$MARKET_RUNTIME_ROOT/public-market-telegram.env}"
PRIVATE_MARKET_ENV="$MARKET_RUNTIME_ROOT/private-gold-telegram.env"
PYTHON_PACKAGES="${COIN_INTELLIGENCE_PYTHON_PACKAGES:-$MARKET_RUNTIME_ROOT/python-packages}"
GROUP_EVENT_CHANNEL_ID="${COIN_GROUP_EVENT_CHANNEL_ID:-}"
EXPECTED_GROUP_EVENT_CHANNEL_ID="${COIN_INTELLIGENCE_EXPECTED_GROUP_EVENT_CHANNEL_ID:-}"
EXPECTED_PRIVATE_OFFER_CHANNEL_ID="${COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID:-}"
EXPECTED_PRIVATE_TRADE_CHANNEL_ID="${COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID:-}"
EXPECTED_TELEGRAM_API_ID="${COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID:-}"
GROUP_SESSION_FILE="$MARKET_RUNTIME_ROOT/session/coin-group-event-reader.session"
PRIVATE_SESSION_FILE="$MARKET_RUNTIME_ROOT/session/telegram-reader.session"
EXPECTED_GROUP_SESSION_FILE="${COIN_INTELLIGENCE_EXPECTED_GROUP_SESSION_FILE:-}"
EXPECTED_PRIVATE_SESSION_FILE="${COIN_INTELLIGENCE_EXPECTED_PRIVATE_SESSION_FILE:-}"
SYSTEMD_DIR="/etc/systemd/system"
BACKUP_ROOT="/var/backups/trading-bot/systemd"
PRODUCTION_OPERATION_LOCK_DIR="/root/secure-envs/trading-bot/queue-cutover-artifacts"
PRODUCTION_OPERATION_LOCK_PATH="$PRODUCTION_OPERATION_LOCK_DIR/production-release.lock"
PRODUCTION_SOURCE_LOCK_PATH="/root/secure-envs/trading-bot/.production-runtime-source.lock"

GROUP_SERVICE="coin-group-event-telegram.service"
GROUP_TIMER="coin-group-event-telegram.timer"
PRIVATE_SERVICE="trading-bot-private-gold-collector.service"
PRIVATE_TIMER="trading-bot-private-gold-collector.timer"
TIMERS=("$GROUP_TIMER" "$PRIVATE_TIMER")
UNITS=("$GROUP_SERVICE" "$GROUP_TIMER" "$PRIVATE_SERVICE" "$PRIVATE_TIMER")

declare -A UNIT_EXISTED=()
declare -A TIMER_WAS_ENABLED=()
declare -A TIMER_WAS_ACTIVE=()
INSTALL_CANDIDATES=()
TRANSACTION_READY=0
INSTALL_MUTATED=0
INSTALL_COMMITTED=0
BACKUP_DIR=""
render_dir=""
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
    if [[ "$RELEASE_LOCK_INHERITED" == "verified-release-held-lock" ]]; then
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
    if ! (set -o noclobber; umask 077; printf '{"owner":"coin-input-installer"}\n' >"$PRODUCTION_OPERATION_LOCK_PATH") 2>/dev/null; then
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
        printf 'configured_path_invalid\n' >&2
        exit 2
    }
    local canonical
    canonical="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' "$value")"
    [[ "$canonical" == "$value" ]] || {
        printf 'configured_path_not_canonical\n' >&2
        exit 2
    }
}

require_production_root() {
    local value="$1"
    local label="$2"
    [[ "$value" == *production* && "$value" != *staging* ]] || {
        printf '%s_scope_invalid\n' "$label" >&2
        exit 2
    }
}

validate_secure_credential_file() {
    local value="$1"
    local label="$2"
    [[ -f "$value" && ! -L "$value" \
        && "$(stat -c '%u' "$value")" == "$(id -u)" \
        && "$(stat -c '%a' "$value")" == "600" \
        && "$(stat -c '%h' "$value")" == "1" ]] || {
        printf '%s_invalid\n' "$label" >&2
        exit 2
    }
}

read_unique_env_value() {
    local path="$1"
    local key="$2"
    python3 - "$path" "$key" <<'PY'
from pathlib import Path
import re
import sys

path, key = Path(sys.argv[1]), sys.argv[2]
matches = []
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    candidate, value = line.split("=", 1)
    if candidate.strip() == key:
        matches.append(value.strip())
if len(matches) != 1 or not matches[0] or "\n" in matches[0] or "\r" in matches[0]:
    raise SystemExit(2)
print(matches[0])
PY
}

timer_state_code() {
    local action="$1"
    local timer="$2"
    local status
    if systemctl "$action" --quiet "$timer" >/dev/null 2>&1; then
        status=0
    else
        status=$?
    fi
    printf '%s' "$status"
}

capture_prior_units_and_timer_state() {
    local unit timer status
    if [[ ! -e "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]]; then
        install -d -m 0700 -- "$BACKUP_ROOT"
    fi
    [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" \
        && "$(stat -c '%u' "$BACKUP_ROOT")" == "$(id -u)" \
        && "$(stat -c '%a' "$BACKUP_ROOT")" == "700" ]] || {
        printf 'systemd_backup_root_invalid\n' >&2
        return 1
    }
    BACKUP_DIR="$(mktemp -d "$BACKUP_ROOT/coin-input-units.XXXXXXXX")"
    chmod 0700 "$BACKUP_DIR"
    for unit in "${UNITS[@]}"; do
        if [[ -L "$SYSTEMD_DIR/$unit" ]]; then
            printf 'existing_unit_symlink_rejected\n' >&2
            return 1
        elif [[ -f "$SYSTEMD_DIR/$unit" ]]; then
            UNIT_EXISTED["$unit"]=1
            install -m 0600 -- "$SYSTEMD_DIR/$unit" "$BACKUP_DIR/$unit"
        elif [[ -e "$SYSTEMD_DIR/$unit" ]]; then
            printf 'existing_unit_type_invalid\n' >&2
            return 1
        else
            UNIT_EXISTED["$unit"]=0
        fi
    done
    for timer in "${TIMERS[@]}"; do
        TIMER_WAS_ENABLED["$timer"]=0
        TIMER_WAS_ACTIVE["$timer"]=0
        if [[ "${UNIT_EXISTED[$timer]}" == "1" ]]; then
            status="$(timer_state_code is-enabled "$timer")"
            case "$status" in
                0) TIMER_WAS_ENABLED["$timer"]=1 ;;
                1) ;;
                *) printf 'timer_enabled_state_unavailable\n' >&2; return 1 ;;
            esac
            status="$(timer_state_code is-active "$timer")"
            case "$status" in
                0) TIMER_WAS_ACTIVE["$timer"]=1 ;;
                3) ;;
                *) printf 'timer_active_state_unavailable\n' >&2; return 1 ;;
            esac
        fi
    done
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
    local timer="$1"
    local enabled="$2"
    local active="$3"
    if [[ "$enabled" == "1" ]]; then
        systemctl enable "$timer" >/dev/null 2>&1
    else
        systemctl disable "$timer" >/dev/null 2>&1
    fi
    if [[ "$active" == "1" ]]; then
        systemctl restart "$timer" >/dev/null 2>&1
    else
        systemctl stop "$timer" >/dev/null 2>&1
    fi
}

verify_timer_state() {
    local timer="$1"
    local expected_enabled="$2"
    local expected_active="$3"
    local status
    status="$(timer_state_code is-enabled "$timer")"
    if [[ "$expected_enabled" == "1" ]]; then
        [[ "$status" == "0" ]] || return 1
    else
        [[ "$status" == "1" ]] || return 1
    fi
    status="$(timer_state_code is-active "$timer")"
    if [[ "$expected_active" == "1" ]]; then
        [[ "$status" == "0" ]] || return 1
    else
        [[ "$status" == "3" ]] || return 1
    fi
}

verify_installed_input_runtime() {
    local unit timer result status
    for unit in "${UNITS[@]}"; do
        [[ -f "$SYSTEMD_DIR/$unit" && ! -L "$SYSTEMD_DIR/$unit" \
            && "$(stat -c '%u' "$SYSTEMD_DIR/$unit")" == "$(id -u)" \
            && "$(stat -c '%a' "$SYSTEMD_DIR/$unit")" == "644" \
            && "$(stat -c '%h' "$SYSTEMD_DIR/$unit")" == "1" ]] \
            && cmp -s -- "$render_dir/$unit" "$SYSTEMD_DIR/$unit" || {
            printf 'installed_input_unit_contract_invalid\n' >&2
            return 1
        }
    done
    for timer in "${TIMERS[@]}"; do
        status="$(timer_state_code is-enabled "$timer")"
        [[ "$status" == "0" ]] || {
            printf 'input_timer_not_enabled\n' >&2
            return 1
        }
        status="$(timer_state_code is-active "$timer")"
        [[ "$status" == "0" ]] || {
            printf 'input_timer_not_active\n' >&2
            return 1
        }
    done
    for unit in "$GROUP_SERVICE" "$PRIVATE_SERVICE"; do
        result="$(systemctl show --property=Result --value "$unit" 2>/dev/null)" || {
            printf 'input_collector_result_unavailable\n' >&2
            return 1
        }
        status="$(systemctl show --property=ExecMainStatus --value "$unit" 2>/dev/null)" || {
            printf 'input_collector_status_unavailable\n' >&2
            return 1
        }
        [[ "$result" == "success" && "$status" == "0" ]] || {
            printf 'input_collector_last_run_failed\n' >&2
            return 1
        }
    done
}

restore_prior_units_and_state() {
    local unit timer candidate failed=0
    set +e
    for timer in "${TIMERS[@]}"; do
        systemctl stop "$timer" >/dev/null 2>&1 || failed=1
        systemctl disable "$timer" >/dev/null 2>&1 || true
    done
    for unit in "${UNITS[@]}"; do
        if [[ "${UNIT_EXISTED[$unit]:-0}" == "1" ]]; then
            candidate="$(mktemp "$SYSTEMD_DIR/.${unit}.rollback.XXXXXXXX")" || { failed=1; continue; }
            install -m 0644 -- "$BACKUP_DIR/$unit" "$candidate" || failed=1
            mv -fT -- "$candidate" "$SYSTEMD_DIR/$unit" || failed=1
            rm -f -- "$candidate"
        else
            rm -f -- "$SYSTEMD_DIR/$unit" || failed=1
        fi
    done
    systemctl daemon-reload >/dev/null 2>&1 || failed=1
    for timer in "${TIMERS[@]}"; do
        if [[ "${UNIT_EXISTED[$timer]:-0}" == "1" ]]; then
            apply_timer_state "$timer" \
                "${TIMER_WAS_ENABLED[$timer]}" "${TIMER_WAS_ACTIVE[$timer]}" || failed=1
        fi
    done
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
            printf 'coin_intelligence_input_timers=rollback_incomplete\n' >&2
        else
            printf 'coin_intelligence_input_timers=rolled_back\n' >&2
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
for required_command in cmp flock install mktemp mv python3 systemctl systemd-analyze; do
    command -v "$required_command" >/dev/null 2>&1 || {
        printf 'required_command_missing=%s\n' "$required_command" >&2
        exit 2
    }
done
acquire_install_locks
for path_value in \
    "$PROJECT_DIR" "$UNIT_SOURCE_DIR" "$MARKET_RUNTIME_ROOT" \
    "$ESTIMATOR_RUNTIME_ROOT" "$MARKET_ENV" "$PRIVATE_MARKET_ENV" \
    "$PYTHON_PACKAGES" "$GROUP_SESSION_FILE" "$PRIVATE_SESSION_FILE" \
    "$SYSTEMD_DIR" "$BACKUP_ROOT"; do
    validate_absolute_local_path "$path_value"
done
require_production_root "$MARKET_RUNTIME_ROOT" "market_runtime_root"
require_production_root "$ESTIMATOR_RUNTIME_ROOT" "estimator_runtime_root"
[[ "$MARKET_ENV" == "$MARKET_RUNTIME_ROOT/public-market-telegram.env" \
    && "$PRIVATE_MARKET_ENV" == "$MARKET_RUNTIME_ROOT/private-gold-telegram.env" ]] || {
    printf 'credential_env_path_binding_invalid\n' >&2
    exit 2
}
[[ "$GROUP_EVENT_CHANNEL_ID" =~ ^-100[0-9]{8,16}$ ]] || {
    printf 'group_event_channel_id_required\n' >&2
    exit 2
}
[[ "$EXPECTED_GROUP_EVENT_CHANNEL_ID" == "$GROUP_EVENT_CHANNEL_ID" ]] || {
    printf 'group_event_channel_binding_invalid\n' >&2
    exit 2
}
[[ "$EXPECTED_TELEGRAM_API_ID" =~ ^[1-9][0-9]{0,15}$ ]] || {
    printf 'telegram_api_identity_binding_required\n' >&2
    exit 2
}
[[ "$EXPECTED_GROUP_SESSION_FILE" == "$GROUP_SESSION_FILE" \
    && "$EXPECTED_PRIVATE_SESSION_FILE" == "$PRIVATE_SESSION_FILE" ]] || {
    printf 'telegram_session_path_binding_invalid\n' >&2
    exit 2
}
[[ -d "$PROJECT_DIR" && -d "$UNIT_SOURCE_DIR" \
    && -d "$SYSTEMD_DIR" && ! -L "$SYSTEMD_DIR" \
    && "$(stat -c '%u' "$SYSTEMD_DIR")" == "$(id -u)" ]] || {
    printf 'project_or_unit_source_missing\n' >&2
    exit 2
}
validate_secure_credential_file "$MARKET_ENV" "public_market_telegram_env"
validate_secure_credential_file "$PRIVATE_MARKET_ENV" "private_gold_telegram_env"
validate_secure_credential_file "$GROUP_SESSION_FILE" "group_telegram_session"
validate_secure_credential_file "$PRIVATE_SESSION_FILE" "private_gold_telegram_session"
public_api_id="$(read_unique_env_value "$MARKET_ENV" COIN_MARKET_TELEGRAM_API_ID)" || {
    printf 'public_market_telegram_identity_invalid\n' >&2
    exit 2
}
private_api_id="$(read_unique_env_value "$PRIVATE_MARKET_ENV" COIN_MARKET_TELEGRAM_API_ID)" || {
    printf 'private_gold_telegram_identity_invalid\n' >&2
    exit 2
}
private_offer_channel="$(read_unique_env_value "$PRIVATE_MARKET_ENV" COIN_INTELLIGENCE_PRIVATE_GOLD_OFFER_EVENT_CHANNEL_ID)" || {
    printf 'private_gold_channel_binding_invalid\n' >&2
    exit 2
}
private_trade_channel="$(read_unique_env_value "$PRIVATE_MARKET_ENV" COIN_INTELLIGENCE_PRIVATE_GOLD_TRADE_EVENT_CHANNEL_ID)" || {
    printf 'private_gold_channel_binding_invalid\n' >&2
    exit 2
}
[[ "$public_api_id" == "$EXPECTED_TELEGRAM_API_ID" \
    && "$private_api_id" == "$EXPECTED_TELEGRAM_API_ID" ]] || {
    printf 'telegram_api_identity_binding_invalid\n' >&2
    exit 2
}
[[ "$EXPECTED_PRIVATE_OFFER_CHANNEL_ID" =~ ^-100[0-9]{8,16}$ \
    && "$EXPECTED_PRIVATE_TRADE_CHANNEL_ID" =~ ^-100[0-9]{8,16}$ \
    && "$EXPECTED_PRIVATE_OFFER_CHANNEL_ID" != "$EXPECTED_PRIVATE_TRADE_CHANNEL_ID" \
    && "$private_offer_channel" == "$EXPECTED_PRIVATE_OFFER_CHANNEL_ID" \
    && "$private_trade_channel" == "$EXPECTED_PRIVATE_TRADE_CHANNEL_ID" ]] || {
    printf 'private_gold_channel_binding_invalid\n' >&2
    exit 2
}
for runtime_directory in \
    "$MARKET_RUNTIME_ROOT" "$MARKET_RUNTIME_ROOT/market" \
    "$MARKET_RUNTIME_ROOT/staging" "$MARKET_RUNTIME_ROOT/session" \
    "$ESTIMATOR_RUNTIME_ROOT" "$ESTIMATOR_RUNTIME_ROOT/conversation"; do
    [[ -d "$runtime_directory" ]] || {
        printf 'runtime_prerequisite_missing\n' >&2
        exit 2
    }
done
for required_script in \
    scripts/collect_coin_group_event_telegram.py \
    scripts/project_group_market_to_estimator.py \
    scripts/collect_private_gold_event_telegram.py; do
    [[ -f "$PROJECT_DIR/$required_script" ]] || {
        printf 'collector_script_missing=%s\n' "$required_script" >&2
        exit 2
    }
done
for tracked_unit in \
    coin-group-event-telegram.service.template \
    "$GROUP_TIMER" \
    trading-bot-private-gold-collector.service.template \
    "$PRIVATE_TIMER"; do
    [[ -f "$UNIT_SOURCE_DIR/$tracked_unit" ]] || {
        printf 'tracked_unit_missing=%s\n' "$tracked_unit" >&2
        exit 2
    }
done

render_dir="$(mktemp -d)"

python3 - \
    "$UNIT_SOURCE_DIR/coin-group-event-telegram.service.template" \
    "$render_dir/$GROUP_SERVICE" \
    "$PROJECT_DIR" "$MARKET_ENV" "$GROUP_EVENT_CHANNEL_ID" \
    "$ESTIMATOR_RUNTIME_ROOT" "$PYTHON_PACKAGES" "$MARKET_RUNTIME_ROOT" <<'PY'
from pathlib import Path
import sys

source, destination, code_root, market_env, channel_id, estimator_root, python_packages, market_root = sys.argv[1:]
rendered = Path(source).read_text(encoding="utf-8")
replacements = {
    "@CODE_ROOT@": code_root,
    "@MARKET_ENV@": market_env,
    "@GROUP_EVENT_CHANNEL_ID@": channel_id,
    "@ESTIMATOR_RUNTIME_ROOT@": estimator_root,
    "@PYTHON_PACKAGES@": python_packages,
    "@MARKET_RUNTIME_ROOT@": market_root,
}
for placeholder, value in replacements.items():
    rendered = rendered.replace(placeholder, value)
if "@" in rendered:
    raise SystemExit("group_service_placeholder_unresolved")
Path(destination).write_text(rendered, encoding="utf-8")
PY

python3 - \
    "$UNIT_SOURCE_DIR/trading-bot-private-gold-collector.service.template" \
    "$render_dir/$PRIVATE_SERVICE" "$PROJECT_DIR" "$MARKET_RUNTIME_ROOT" <<'PY'
from pathlib import Path
import sys

source, destination, code_root, runtime_root = sys.argv[1:]
rendered = Path(source).read_text(encoding="utf-8")
rendered = rendered.replace("@CODE_ROOT@", code_root).replace("@RUNTIME_ROOT@", runtime_root)
if "@" in rendered:
    raise SystemExit("private_service_placeholder_unresolved")
Path(destination).write_text(rendered, encoding="utf-8")
PY

install -m 0644 -- "$UNIT_SOURCE_DIR/$GROUP_TIMER" "$render_dir/$GROUP_TIMER"
install -m 0644 -- "$UNIT_SOURCE_DIR/$PRIVATE_TIMER" "$render_dir/$PRIVATE_TIMER"

systemd-analyze verify \
    "$render_dir/$GROUP_SERVICE" "$render_dir/$GROUP_TIMER" \
    "$render_dir/$PRIVATE_SERVICE" "$render_dir/$PRIVATE_TIMER" \
    >/dev/null 2>&1

if [[ "$CHECK_ONLY" == "1" ]]; then
    verify_installed_input_runtime
    printf 'coin_intelligence_input_timers=ready credentials_bound=true timers_active=true last_runs_successful=true\n'
    exit 0
fi

capture_prior_units_and_timer_state
INSTALL_MUTATED=1
for unit in "${UNITS[@]}"; do
    atomic_install_unit "$render_dir/$unit" "$SYSTEMD_DIR/$unit"
done
systemctl daemon-reload >/dev/null 2>&1
systemd-analyze verify \
    "$SYSTEMD_DIR/$GROUP_SERVICE" "$SYSTEMD_DIR/$GROUP_TIMER" \
    "$SYSTEMD_DIR/$PRIVATE_SERVICE" "$SYSTEMD_DIR/$PRIVATE_TIMER" \
    >/dev/null 2>&1

for timer in "${TIMERS[@]}"; do
    if [[ "$FORCE_ACTIVE" == "1" ]]; then
        # This override is used only by the lock-held, explicitly authorized
        # production inference rollout.  The standalone/default installer
        # continues to preserve the operator's prior timer state.
        desired_enabled=1
        desired_active=1
    elif [[ "${UNIT_EXISTED[$timer]}" == "1" ]]; then
        desired_enabled="${TIMER_WAS_ENABLED[$timer]}"
        desired_active="${TIMER_WAS_ACTIVE[$timer]}"
    else
        desired_enabled=1
        desired_active=1
    fi
    # Only timers are restarted. Collector services are never interrupted or
    # overlapped; OnUnitInactiveSec arms from the collector's real completion.
    apply_timer_state "$timer" "$desired_enabled" "$desired_active"
    verify_timer_state "$timer" "$desired_enabled" "$desired_active"
done
INSTALL_COMMITTED=1
if [[ "$FORCE_ACTIVE" == "1" ]]; then
    if [[ "$RELEASE_LOCK_INHERITED" == "verified-release-held-lock" ]]; then
        printf 'coin_intelligence_input_timers=installed backup_retained=true explicitly_activated=true authority=release\n'
    else
        printf 'coin_intelligence_input_timers=installed backup_retained=true explicitly_activated=true authority=bounded_repair\n'
    fi
else
    printf 'coin_intelligence_input_timers=installed backup_retained=true prior_state_preserved=true\n'
fi
