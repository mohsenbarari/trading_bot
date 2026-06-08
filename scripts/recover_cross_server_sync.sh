#!/bin/bash
set -euo pipefail

PROJECT_DIR="/root/trading-bot/trading_bot"
LOCAL_API_URL="${LOCAL_API_URL:-http://127.0.0.1:8000}"
IRAN_HOST="${IRAN_HOST:-87.107.110.68}"
IRAN_USER="${IRAN_USER:-root}"
IRAN_PROJECT_DIR="${IRAN_PROJECT_DIR:-/root/trading-bot/trading_bot}"
IRAN_API_URL="${IRAN_API_URL:-http://127.0.0.1:8000}"
SYNC_LIMIT="${SYNC_LIMIT:-200}"
SYNC_MAX_ROUNDS="${SYNC_MAX_ROUNDS:-20}"
TABLES=(users commodities commodity_aliases trading_settings offers trades user_blocks)

print_header() {
    echo ""
    echo "============================================"
    echo "  $1"
    echo "============================================"
}

ensure_local_env() {
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "$PROJECT_DIR/.env"
        set +a
    fi

    if [[ -z "${DEV_API_KEY:-}" ]]; then
        echo "❌ DEV_API_KEY is required. Export it or define it in $PROJECT_DIR/.env"
        exit 1
    fi

    IRAN_DEV_API_KEY="${IRAN_DEV_API_KEY:-$DEV_API_KEY}"
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
        if ssh -o StrictHostKeyChecking=no "$IRAN_USER@$IRAN_HOST" "curl -fsS '$IRAN_API_URL/api/config' >/dev/null" 2>/dev/null; then
            return 0
        fi
        sleep 4
    done
    echo "❌ Iran API is not reachable via SSH/curl"
    exit 1
}

start_sync_workers() {
    print_header "Starting sync workers"
    (
        cd "$PROJECT_DIR"
        docker compose up -d sync_worker >/dev/null
    )
    ssh -o StrictHostKeyChecking=no "$IRAN_USER@$IRAN_HOST" \
        "cd '$IRAN_PROJECT_DIR' && docker compose -f docker-compose.iran.yml up -d sync_worker >/dev/null"
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
    ssh -o StrictHostKeyChecking=no "$IRAN_USER@$IRAN_HOST" \
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
