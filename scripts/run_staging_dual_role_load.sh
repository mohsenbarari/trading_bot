#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/deploy/staging/docker-compose.staging.yml"
ENV_FILE="$PROJECT_DIR/.env.staging"
STAGING_PROJECT_NAME="${STAGING_PROJECT_NAME:-trading_bot_staging}"
STAGING_APP_PORT="${STAGING_APP_PORT:-8100}"
STAGING_FRONTEND_DOCKER_DIST_DIR="${STAGING_FRONTEND_DOCKER_DIST_DIR:-mini_app_dist_staging}"

PREFIX="${PREFIX:-P7_STAGE_DUAL_$(date -u +%Y%m%d_%H%M%S)_}"
ARTIFACT_DIR="${ARTIFACT_DIR:-/tmp/trading-bot-staging-load/$PREFIX}"
OFFER_ORIGIN="${OFFER_ORIGIN:-webapp}"
USER_COUNT="${USER_COUNT:-50}"
HOT_OFFER_REQUESTS="${HOT_OFFER_REQUESTS:-40}"
TARGET_RPS="${TARGET_RPS:-20}"
TELEGRAM_RATIO="${TELEGRAM_RATIO:-0.6}"
REQUEST_AMOUNT="${REQUEST_AMOUNT:-5}"
HOT_OFFER_QUANTITY="${HOT_OFFER_QUANTITY:-5}"
EXPECTED_WINNER_COUNT="${EXPECTED_WINNER_COUNT:-1}"
PRICE="${PRICE:-100000}"
OFFER_TYPE="${OFFER_TYPE:-sell}"
HOT_OFFER_IS_WHOLESALE="${HOT_OFFER_IS_WHOLESALE:-1}"
HOT_OFFER_LOT_SIZES="${HOT_OFFER_LOT_SIZES:-}"
BARRIER_DELAY_SECONDS="${BARRIER_DELAY_SECONDS:-8}"
DB_POOL_SIZE="${DB_POOL_SIZE:-20}"
DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-20}"
KEEP_DATA="${KEEP_DATA:-0}"

compose_cmd=(
    docker compose
    -p "$STAGING_PROJECT_NAME"
    --env-file "$ENV_FILE"
    -f "$COMPOSE_FILE"
)

log() {
    printf '[staging-load] %s\n' "$*"
}

die() {
    printf '[staging-load] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<EOF
Usage: scripts/run_staging_dual_role_load.sh [options]

Options:
  --prefix VALUE             Synthetic data prefix. Default: $PREFIX
  --artifact-dir PATH        Host artifact directory. Default: $ARTIFACT_DIR
  --offer-origin webapp|bot  Surface that creates the hot offer. Default: $OFFER_ORIGIN
  --users N                  Synthetic user count. Default: $USER_COUNT
  --requests N               Hot-offer request count. Default: $HOT_OFFER_REQUESTS
  --target-rps N             Target business request RPS. Default: $TARGET_RPS
  --telegram-ratio N         Telegram request ratio. Default: $TELEGRAM_RATIO
  --retail                   Create a retail hot offer instead of a wholesale offer.
  --lot-sizes VALUE          Retail lot sizes, comma/space separated. Default: $HOT_OFFER_LOT_SIZES
  --db-pool-size N           Load-runner DB pool size. Default: $DB_POOL_SIZE
  --db-max-overflow N        Load-runner DB max overflow. Default: $DB_MAX_OVERFLOW
  --keep-data                Do not clean synthetic staging DB rows after the run.
  -h, --help                 Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            PREFIX="${2:?missing --prefix value}"
            shift 2
            ;;
        --artifact-dir)
            ARTIFACT_DIR="${2:?missing --artifact-dir value}"
            shift 2
            ;;
        --offer-origin)
            OFFER_ORIGIN="${2:?missing --offer-origin value}"
            shift 2
            ;;
        --users)
            USER_COUNT="${2:?missing --users value}"
            shift 2
            ;;
        --requests)
            HOT_OFFER_REQUESTS="${2:?missing --requests value}"
            shift 2
            ;;
        --target-rps)
            TARGET_RPS="${2:?missing --target-rps value}"
            shift 2
            ;;
        --telegram-ratio)
            TELEGRAM_RATIO="${2:?missing --telegram-ratio value}"
            shift 2
            ;;
        --retail)
            HOT_OFFER_IS_WHOLESALE=0
            shift
            ;;
        --lot-sizes)
            HOT_OFFER_LOT_SIZES="${2:?missing --lot-sizes value}"
            shift 2
            ;;
        --db-pool-size)
            DB_POOL_SIZE="${2:?missing --db-pool-size value}"
            shift 2
            ;;
        --db-max-overflow)
            DB_MAX_OVERFLOW="${2:?missing --db-max-overflow value}"
            shift 2
            ;;
        --keep-data)
            KEEP_DATA=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

case "$OFFER_ORIGIN" in
    webapp|bot) ;;
    *) die "--offer-origin must be webapp or bot" ;;
esac

prepare_offer_shape_args=()
if [[ "$HOT_OFFER_IS_WHOLESALE" == "0" || "$HOT_OFFER_IS_WHOLESALE" == "false" || "$HOT_OFFER_IS_WHOLESALE" == "False" ]]; then
    prepare_offer_shape_args+=(--retail)
fi
if [[ -n "$HOT_OFFER_LOT_SIZES" ]]; then
    prepare_offer_shape_args+=(--lot-sizes "$HOT_OFFER_LOT_SIZES")
fi

[[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE; run scripts/deploy_staging.sh ensure-env first"
[[ -f "$COMPOSE_FILE" ]] || die "missing $COMPOSE_FILE"

mkdir -p "$ARTIFACT_DIR"

run_load_service() {
    local service="$1"
    shift
    STAGING_APP_PORT="$STAGING_APP_PORT" \
    STAGING_FRONTEND_DOCKER_DIST_DIR="$STAGING_FRONTEND_DOCKER_DIST_DIR" \
    STAGING_LOAD_DB_POOL_SIZE="$DB_POOL_SIZE" \
    STAGING_LOAD_DB_MAX_OVERFLOW="$DB_MAX_OVERFLOW" \
    "${compose_cmd[@]}" --profile staging-load run --rm --no-deps \
        -v "$ARTIFACT_DIR:/artifacts" \
        "$service" "$@"
}

cleanup_synthetic_data() {
    if [[ "$KEEP_DATA" == "1" ]]; then
        log "keeping synthetic data for prefix=$PREFIX"
        return
    fi
    log "cleaning synthetic data for prefix=$PREFIX"
    run_load_service load_webapp_iran \
        python scripts/trading_core_probe_worker.py cleanup \
        --prefix "$PREFIX" \
        --artifact /artifacts/cleanup.json || true
}

finish() {
    local status=$?
    cleanup_synthetic_data
    log "artifacts: $ARTIFACT_DIR"
    exit "$status"
}
trap finish EXIT

log "checking staging health"
scripts/deploy_staging.sh health >/dev/null

log "checking load-runner runtime guards"
run_load_service load_telegram_foreign \
    python scripts/trading_core_probe_worker.py load-runner-ready --role telegram_foreign >/dev/null
run_load_service load_webapp_iran \
    python scripts/trading_core_probe_worker.py load-runner-ready --role webapp_iran >/dev/null

prepare_service="load_webapp_iran"
if [[ "$OFFER_ORIGIN" == "bot" ]]; then
    prepare_service="load_telegram_foreign"
fi

log "preparing dual-role run origin=$OFFER_ORIGIN users=$USER_COUNT requests=$HOT_OFFER_REQUESTS target_rps=$TARGET_RPS"
run_load_service "$prepare_service" \
    python scripts/trading_core_probe_worker.py prepare-dual-role-run \
    --output-dir /artifacts \
    --prefix "$PREFIX" \
    --offer-origin "$OFFER_ORIGIN" \
    --user-count "$USER_COUNT" \
    --hot-offer-requests "$HOT_OFFER_REQUESTS" \
    --telegram-ratio "$TELEGRAM_RATIO" \
    --target-rps "$TARGET_RPS" \
    --hot-offer-quantity "$HOT_OFFER_QUANTITY" \
    --request-amount "$REQUEST_AMOUNT" \
    --expected-winner-count "$EXPECTED_WINNER_COUNT" \
    --price "$PRICE" \
    --offer-type "$OFFER_TYPE" \
    "${prepare_offer_shape_args[@]}" \
    --barrier-delay-seconds "$BARRIER_DELAY_SECONDS" \
    >"$ARTIFACT_DIR/prepare.stdout.log" \
    2>"$ARTIFACT_DIR/prepare.stderr.log"

log "running telegram_foreign and webapp_iran role workers in parallel"
run_load_service load_telegram_foreign \
    python scripts/trading_core_probe_worker.py run-role-plan \
    --plan /artifacts/telegram_foreign.plan.json \
    --output /artifacts/telegram_foreign.result.json \
    --patch-boundaries \
    >"$ARTIFACT_DIR/telegram_foreign.stdout.log" \
    2>"$ARTIFACT_DIR/telegram_foreign.stderr.log" &
telegram_pid=$!

run_load_service load_webapp_iran \
    python scripts/trading_core_probe_worker.py run-role-plan \
    --plan /artifacts/webapp_iran.plan.json \
    --output /artifacts/webapp_iran.result.json \
    --patch-boundaries \
    >"$ARTIFACT_DIR/webapp_iran.stdout.log" \
    2>"$ARTIFACT_DIR/webapp_iran.stderr.log" &
webapp_pid=$!

telegram_status=0
webapp_status=0
wait "$telegram_pid" || telegram_status=$?
wait "$webapp_pid" || webapp_status=$?
if [[ "$telegram_status" != "0" || "$webapp_status" != "0" ]]; then
    log "telegram_foreign exit=$telegram_status webapp_iran exit=$webapp_status"
    die "one or more role workers failed; inspect $ARTIFACT_DIR/*.stderr.log"
fi

log "merging role results"
run_load_service load_webapp_iran \
    python scripts/trading_core_probe_worker.py merge-role-results \
    --output /artifacts/merged.result.json \
    /artifacts/telegram_foreign.result.json \
    /artifacts/webapp_iran.result.json \
    >"$ARTIFACT_DIR/merge.stdout.log" \
    2>"$ARTIFACT_DIR/merge.stderr.log"

log "finalizing correctness report"
run_load_service load_webapp_iran \
    python scripts/trading_core_probe_worker.py finalize-dual-role-run \
    --prepare /artifacts/prepare.json \
    --merged-result /artifacts/merged.result.json \
    --output /artifacts/final.json \
    --check \
    >"$ARTIFACT_DIR/finalize.stdout.log" \
    2>"$ARTIFACT_DIR/finalize.stderr.log"

log "collecting observability snapshot"
run_load_service load_webapp_iran \
    python scripts/trading_core_probe_worker.py observability-snapshot \
    --output /artifacts/observability.json \
    >"$ARTIFACT_DIR/observability.stdout.log" \
    2>"$ARTIFACT_DIR/observability.stderr.log"

log "building capacity report"
python3 scripts/report_bot_webapp_capacity.py build \
    --mixed-payload "$ARTIFACT_DIR/final.json" \
    --observability "$ARTIFACT_DIR/observability.json" \
    --telegram-gateway-boundary mock \
    --target-business-rps "$TARGET_RPS" \
    --output "$ARTIFACT_DIR/capacity.json" \
    >"$ARTIFACT_DIR/capacity.stdout.log" \
    2>"$ARTIFACT_DIR/capacity.stderr.log"

log "summary"
python3 - "$ARTIFACT_DIR/final.json" "$ARTIFACT_DIR/capacity.json" <<'PY'
import json
import sys
from pathlib import Path

final = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
capacity = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
print(
    json.dumps(
        {
            "final_status": final.get("status"),
            "correctness_failures": final.get("correctness_failures", []),
            "business_request_rps": capacity.get("business_request_rps"),
            "telegram_update_rps": capacity.get("telegram_update_rps"),
            "capacity_warnings": capacity.get("capacity_warnings", []),
            "production_gate": (capacity.get("production_gate") or {}).get("status"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
)
PY
