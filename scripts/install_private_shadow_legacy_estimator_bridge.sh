#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRMATION_VALUE="install-private-shadow-legacy-estimator-bridge"
[[ "${PRIVATE_SHADOW_LEGACY_BRIDGE_CONFIRM:-}" == "$CONFIRMATION_VALUE" ]] || {
    printf 'bridge_install_confirmation_required\n' >&2
    exit 2
}

CHECK_ONLY="${PRIVATE_SHADOW_LEGACY_BRIDGE_CHECK_ONLY:-0}"
ACTIVATE_TIMER="${PRIVATE_SHADOW_LEGACY_BRIDGE_ACTIVATE_TIMER:-0}"
[[ "$CHECK_ONLY" == "0" || "$CHECK_ONLY" == "1" ]] || {
    printf 'bridge_check_only_invalid\n' >&2
    exit 2
}
[[ "$ACTIVATE_TIMER" == "0" || "$ACTIVATE_TIMER" == "1" ]] || {
    printf 'bridge_activate_timer_invalid\n' >&2
    exit 2
}

PROJECT_DIR="${PROJECT_DIR:-/root/trading-bot/trading_bot}"
RELEASE_SHA="${PRIVATE_SHADOW_LEGACY_BRIDGE_RELEASE_SHA:-}"
RELEASE_ROOT_BASE="${PRIVATE_SHADOW_LEGACY_BRIDGE_RELEASE_ROOT:-/srv/trading-bot/market-estimator-bridge-releases}"
SHADOW_MARKET_STORE="${PRIVATE_SHADOW_LEGACY_BRIDGE_SHADOW_STORE:-/srv/trading-bot/staging-data/coin-intelligence/private-pipeline-shadow/market-store/market-store.sqlite}"
MARKET_RUNTIME_ROOT="${COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT:-/srv/trading-bot/production-data/coin-intelligence/private-gold-live}"
ESTIMATOR_RUNTIME_ROOT="${COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT:-/srv/trading-bot/production-data/coin-intelligence/estimator-live}"
BRIDGE_STATE_ROOT="${PRIVATE_SHADOW_LEGACY_BRIDGE_STATE_ROOT:-/srv/trading-bot/production-data/coin-intelligence/private-shadow-legacy-bridge}"
CUTOFF_UTC="${PRIVATE_SHADOW_LEGACY_BRIDGE_CUTOFF_UTC:-2026-08-25T09:33:00Z}"
SYSTEMD_DIR="${PRIVATE_SHADOW_LEGACY_BRIDGE_SYSTEMD_DIR:-/etc/systemd/system}"
SKIP_SYSTEMCTL="${PRIVATE_SHADOW_LEGACY_BRIDGE_SKIP_SYSTEMCTL:-0}"
UNIT_SOURCE_DIR="$PROJECT_DIR/deploy/coin_intelligence/systemd"
SERVICE_NAME="coin-private-shadow-legacy-estimator-bridge.service"
TIMER_NAME="coin-private-shadow-legacy-estimator-bridge.timer"

if [[ -z "$RELEASE_SHA" ]]; then
    RELEASE_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
fi
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || {
    printf 'bridge_release_sha_invalid\n' >&2
    exit 2
}

LEGACY_MARKET_STORE="$MARKET_RUNTIME_ROOT/market/market.sqlite3"
CONVERSATION_DB="$ESTIMATOR_RUNTIME_ROOT/conversation/conversation_events.sqlite3"
MARKET_LOCK="$MARKET_RUNTIME_ROOT/staging/.market-store-writer.lock"
CONVERSATION_LOCK="$ESTIMATOR_RUNTIME_ROOT/.conversation-writer.lock"
LEDGER="$BRIDGE_STATE_ROOT/projection-ledger.sqlite"
HEARTBEAT="$BRIDGE_STATE_ROOT/health.json"
GROUP_PROJECTION_HEALTH="$ESTIMATOR_RUNTIME_ROOT/conversation/group-event-health.json"
RELEASE_ROOT="$RELEASE_ROOT_BASE/$RELEASE_SHA"
SHADOW_STORE_DIR="$(dirname -- "$SHADOW_MARKET_STORE")"

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
}

for candidate in \
    "$PROJECT_DIR" "$RELEASE_ROOT_BASE" "$SHADOW_MARKET_STORE" \
    "$MARKET_RUNTIME_ROOT" "$ESTIMATOR_RUNTIME_ROOT" "$BRIDGE_STATE_ROOT" \
    "$SYSTEMD_DIR" "$LEGACY_MARKET_STORE" "$CONVERSATION_DB" \
    "$GROUP_PROJECTION_HEALTH"; do
    validate_absolute_local_path "$candidate"
done

[[ -f "$UNIT_SOURCE_DIR/$SERVICE_NAME.template" && -f "$UNIT_SOURCE_DIR/$TIMER_NAME" ]] || {
    printf 'bridge_unit_template_missing\n' >&2
    exit 2
}
[[ -f "$PROJECT_DIR/scripts/run_private_shadow_legacy_estimator_bridge.py" ]] || {
    printf 'bridge_orchestrator_missing\n' >&2
    exit 2
}

if [[ "$CHECK_ONLY" == "1" ]]; then
    printf 'private_shadow_legacy_bridge=ready release_sha=%s timer_activate=%s\n' \
        "$RELEASE_SHA" "$ACTIVATE_TIMER"
    exit 0
fi

umask 077
install -d -m 0755 -- "$RELEASE_ROOT_BASE"
install -d -m 0700 -- "$BRIDGE_STATE_ROOT"
install -d -m 0755 -- "$SYSTEMD_DIR"
if [[ ! -e "$MARKET_LOCK" ]]; then
    : >"$MARKET_LOCK"
    chmod 0600 "$MARKET_LOCK"
fi
if [[ ! -e "$CONVERSATION_LOCK" ]]; then
    : >"$CONVERSATION_LOCK"
    chmod 0600 "$CONVERSATION_LOCK"
fi

if [[ ! -f "$RELEASE_ROOT/RELEASE_SHA" ]]; then
    stage="$(mktemp -d "$RELEASE_ROOT_BASE/.stage.XXXXXX")"
    git -C "$PROJECT_DIR" archive --format=tar "$RELEASE_SHA" | tar -C "$stage" -xf -
    printf '%s\n' "$RELEASE_SHA" >"$stage/RELEASE_SHA"
    chmod 0644 "$stage/RELEASE_SHA"
    chmod -R a-w "$stage" || true
    rm -rf -- "$RELEASE_ROOT"
    mv -- "$stage" "$RELEASE_ROOT"
fi
[[ "$(tr -d '[:space:]' <"$RELEASE_ROOT/RELEASE_SHA")" == "$RELEASE_SHA" ]] || {
    printf 'bridge_release_binding_mismatch\n' >&2
    exit 2
}

render_dir="$(mktemp -d)"
trap 'rm -rf -- "$render_dir"' EXIT
python3 - \
    "$UNIT_SOURCE_DIR/$SERVICE_NAME.template" \
    "$render_dir/$SERVICE_NAME" \
    "$RELEASE_ROOT" "$SHADOW_STORE_DIR" "$SHADOW_MARKET_STORE" \
    "$MARKET_RUNTIME_ROOT" "$ESTIMATOR_RUNTIME_ROOT" "$BRIDGE_STATE_ROOT" \
    "$LEGACY_MARKET_STORE" "$CONVERSATION_DB" "$LEDGER" "$HEARTBEAT" \
    "$GROUP_PROJECTION_HEALTH" "$RELEASE_SHA" "$CUTOFF_UTC" \
    "$MARKET_LOCK" "$CONVERSATION_LOCK" <<'PY'
from pathlib import Path
import sys

(
    source, destination, release_root, shadow_dir, shadow_store,
    market_root, estimator_root, bridge_root, legacy_store, conversation,
    ledger, heartbeat, group_projection_health, release_sha, cutoff,
    market_lock, conversation_lock,
) = sys.argv[1:]
rendered = Path(source).read_text(encoding="utf-8")
replacements = {
    "@RELEASE_ROOT@": release_root,
    "@SHADOW_STORE_DIR@": shadow_dir,
    "@SHADOW_MARKET_STORE@": shadow_store,
    "@MARKET_RUNTIME_ROOT@": market_root,
    "@ESTIMATOR_RUNTIME_ROOT@": estimator_root,
    "@BRIDGE_STATE_ROOT@": bridge_root,
    "@LEGACY_MARKET_STORE@": legacy_store,
    "@CONVERSATION_DB@": conversation,
    "@LEDGER@": ledger,
    "@HEARTBEAT@": heartbeat,
    "@GROUP_PROJECTION_HEALTH@": group_projection_health,
    "@RELEASE_SHA@": release_sha,
    "@CUTOFF_UTC@": cutoff,
    "@MARKET_LOCK@": market_lock,
    "@CONVERSATION_LOCK@": conversation_lock,
}
for placeholder, value in replacements.items():
    rendered = rendered.replace(placeholder, value)
if "@" in rendered:
    raise SystemExit("bridge_service_placeholder_unresolved")
Path(destination).write_text(rendered, encoding="utf-8")
PY
install -m 0644 -- "$UNIT_SOURCE_DIR/$TIMER_NAME" "$render_dir/$TIMER_NAME"
if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "$render_dir/$SERVICE_NAME" "$render_dir/$TIMER_NAME" >/dev/null
fi
install -m 0644 -- "$render_dir/$SERVICE_NAME" "$SYSTEMD_DIR/$SERVICE_NAME"
install -m 0644 -- "$render_dir/$TIMER_NAME" "$SYSTEMD_DIR/$TIMER_NAME"

if [[ "$SKIP_SYSTEMCTL" != "1" ]]; then
    systemctl daemon-reload >/dev/null
    if [[ "$ACTIVATE_TIMER" == "1" ]]; then
        systemctl enable --now "$TIMER_NAME" >/dev/null
    fi
fi

printf 'private_shadow_legacy_bridge=installed release_sha=%s timer_active=%s\n' \
    "$RELEASE_SHA" "$ACTIVATE_TIMER"
