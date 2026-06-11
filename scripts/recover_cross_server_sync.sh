#!/bin/bash
set -euo pipefail

PROJECT_DIR="/root/trading-bot/trading_bot"
DEPLOY_CONFIG_SCRIPT="$PROJECT_DIR/scripts/deploy_config.py"
LOCAL_API_URL="${LOCAL_API_URL:-http://127.0.0.1:8000}"
IRAN_HOST="${IRAN_HOST:-}"
IRAN_USER="${IRAN_USER:-}"
IRAN_SSH_PORT="${IRAN_SSH_PORT:-}"
IRAN_PROJECT_DIR="${IRAN_PROJECT_DIR:-}"
IRAN_API_URL="${IRAN_API_URL:-http://127.0.0.1:8000}"
SYNC_LIMIT="${SYNC_LIMIT:-200}"
SYNC_MAX_ROUNDS="${SYNC_MAX_ROUNDS:-20}"
TABLES=(
    users
    accountant_relations
    customer_relations
    chats
    chat_members
    invitations
    admin_market_messages
    admin_broadcast_messages
    notifications
    user_blocks
    commodities
    commodity_aliases
    trading_settings
    market_schedule_overrides
    market_runtime_state
    offers
    trades
)

load_shared_deploy_surface() {
    if [[ -f "$DEPLOY_CONFIG_SCRIPT" ]]; then
        local explicit_iran_user="${IRAN_USER:-}"
        local shell_exports
        shell_exports="$(python3 "$DEPLOY_CONFIG_SCRIPT" --format shell 2>/dev/null || true)"
        if [[ -n "$shell_exports" ]]; then
            eval "$shell_exports"
            IRAN_USER="${explicit_iran_user:-${IRAN_SSH_USER:-${IRAN_USER:-}}}"
            IRAN_PROJECT_DIR="${IRAN_PROJECT_DIR:-${IRAN_DIR:-}}"
        fi
    fi
    : "${IRAN_HOST:?IRAN_HOST is required. Define it in DEPLOY_MANIFEST or environment.}"
    : "${IRAN_USER:?IRAN_USER/IRAN_SSH_USER is required. Define it in DEPLOY_MANIFEST or environment.}"
    : "${IRAN_SSH_PORT:?IRAN_SSH_PORT is required. Define it in DEPLOY_MANIFEST or environment.}"
    : "${IRAN_PROJECT_DIR:?IRAN_PROJECT_DIR is required. Define it in DEPLOY_MANIFEST or environment.}"
}

print_header() {
    echo ""
    echo "============================================"
    echo "  $1"
    echo "============================================"
}

ensure_local_env() {
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        DEV_API_KEY="${DEV_API_KEY:-$(read_env_value "$PROJECT_DIR/.env" DEV_API_KEY)}"
        IRAN_DEV_API_KEY="${IRAN_DEV_API_KEY:-$(read_env_value "$PROJECT_DIR/.env" IRAN_DEV_API_KEY)}"
    fi

    if [[ -z "${DEV_API_KEY:-}" ]]; then
        echo "❌ DEV_API_KEY is required. Export it or define it in $PROJECT_DIR/.env"
        exit 1
    fi

    IRAN_DEV_API_KEY="${IRAN_DEV_API_KEY:-$DEV_API_KEY}"
}

read_env_value() {
    local env_file="$1"
    local wanted_key="$2"
    python3 - "$env_file" "$wanted_key" <<'PY'
import sys
from pathlib import Path

env_file = Path(sys.argv[1])
wanted_key = sys.argv[2]
if not env_file.exists():
    raise SystemExit(0)

for raw_line in env_file.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == wanted_key:
        print(value.strip().strip('"').strip("'"))
        break
PY
}

wait_for_local_api() {
    for _ in $(seq 1 30); do
        if curl -fsS "$LOCAL_API_URL/api/config" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    echo "❌ Local API is not responding at $LOCAL_API_URL"
    exit 1
}

wait_for_iran_api() {
    for _ in $(seq 1 30); do
        if ssh -o StrictHostKeyChecking=no -p "$IRAN_SSH_PORT" "$IRAN_USER@$IRAN_HOST" "curl -fsS '$IRAN_API_URL/api/config' >/dev/null" 2>/dev/null; then
            return 0
        fi
        sleep 4
    done
    echo "❌ Iran API is not reachable via SSH/curl"
    exit 1
}

local_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        printf 'docker compose\n'
    elif command -v docker-compose >/dev/null 2>&1; then
        printf 'docker-compose\n'
    else
        echo "❌ No Docker Compose command is available on the foreign host"
        exit 1
    fi
}

start_sync_workers() {
    print_header "Starting sync workers"
    (
        cd "$PROJECT_DIR"
        compose_cmd="$(local_compose_cmd)"
        $compose_cmd rm -sf sync_worker >/dev/null 2>&1 || docker rm -f trading_bot_sync_worker >/dev/null 2>&1 || true
        $compose_cmd up -d --no-deps sync_worker >/dev/null
    )
    ssh -o StrictHostKeyChecking=no -p "$IRAN_SSH_PORT" "$IRAN_USER@$IRAN_HOST" \
        "cd '$IRAN_PROJECT_DIR' && if docker compose version >/dev/null 2>&1; then compose_cmd='docker compose'; elif command -v docker-compose >/dev/null 2>&1; then compose_cmd='docker-compose'; else echo 'No Docker Compose command is available on the Iran host.' >&2; exit 1; fi; \$compose_cmd -f docker-compose.iran.yml rm -sf sync_worker >/dev/null 2>&1 || docker rm -f trading_bot_sync_worker >/dev/null 2>&1 || true; \$compose_cmd -f docker-compose.iran.yml up -d --no-deps sync_worker >/dev/null"
}

parse_processed() {
    /bin/python3 -c 'import json,sys; data=json.load(sys.stdin); print(int(data.get("processed", 0)))'
}

parse_errors() {
    /bin/python3 -c 'import json,sys; data=json.load(sys.stdin); print(int(data.get("errors", 0)))'
}

resync_local_table() {
    local table="$1"
    curl -fsS -X POST "$LOCAL_API_URL/api/sync/resync?limit=$SYNC_LIMIT&table_filter=$table" \
        -H "X-Dev-Api-Key: $DEV_API_KEY"
}

resync_iran_table() {
    local table="$1"
    ssh -o StrictHostKeyChecking=no -p "$IRAN_SSH_PORT" "$IRAN_USER@$IRAN_HOST" \
        "curl -fsS -X POST '$IRAN_API_URL/api/sync/resync?limit=$SYNC_LIMIT&table_filter=$table' -H 'X-Dev-Api-Key: $IRAN_DEV_API_KEY'"
}

drain_direction() {
    local direction="$1"
    local total_processed=0
    local total_errors=0

    print_header "Draining $direction"
    for table in "${TABLES[@]}"; do
        local rounds=0
        while true; do
            local response
            if [[ "$direction" == "foreign->iran" ]]; then
                response="$(resync_local_table "$table")"
            else
                response="$(resync_iran_table "$table")"
            fi

            local processed
            processed="$(printf '%s' "$response" | parse_processed)"
            local errors
            errors="$(printf '%s' "$response" | parse_errors)"

            total_processed=$((total_processed + processed))
            total_errors=$((total_errors + errors))
            rounds=$((rounds + 1))

            echo "[$direction][$table][round $rounds] processed=$processed errors=$errors"

            if [[ "$errors" -gt 0 ]]; then
                echo "❌ Resync reported errors for $direction / $table"
                printf '%s\n' "$response"
                exit 1
            fi

            if [[ "$processed" -eq 0 ]]; then
                break
            fi

            if [[ "$rounds" -ge "$SYNC_MAX_ROUNDS" ]]; then
                echo "❌ Reached SYNC_MAX_ROUNDS=$SYNC_MAX_ROUNDS while draining $direction / $table"
                exit 1
            fi
        done
    done

    echo "✅ $direction complete: processed=$total_processed errors=$total_errors"
}

main() {
    load_shared_deploy_surface
    ensure_local_env

    print_header "Waiting for APIs"
    wait_for_local_api
    wait_for_iran_api

    start_sync_workers

    # Run both directions twice for convergence after a long outage.
    drain_direction "foreign->iran"
    drain_direction "iran->foreign"
    drain_direction "foreign->iran"
    drain_direction "iran->foreign"

    print_header "Sync recovery completed"
    echo "✅ Both directions drained. If desired, keep sync_worker running on both servers."
}

main "$@"
