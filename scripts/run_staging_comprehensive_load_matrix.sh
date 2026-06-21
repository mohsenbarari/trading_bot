#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/deploy/staging/docker-compose.staging.yml"
ENV_FILE="$PROJECT_DIR/.env.staging"
STAGING_PROJECT_NAME="${STAGING_PROJECT_NAME:-trading_bot_staging}"
STAGING_APP_PORT="${STAGING_APP_PORT:-8100}"
STAGING_FRONTEND_DOCKER_DIST_DIR="${STAGING_FRONTEND_DOCKER_DIST_DIR:-mini_app_dist_staging}"

PREFIX="${PREFIX:-P7_STAGE_FULL_$(date -u +%Y%m%d_%H%M%S)_}"
ARTIFACT_DIR="${ARTIFACT_DIR:-/tmp/trading-bot-staging-load/$PREFIX}"
USER_COUNT="${USER_COUNT:-1000}"
ATTEMPTS_PER_SCENARIO="${ATTEMPTS_PER_SCENARIO:-40}"
TARGET_RPS="${TARGET_RPS:-600}"
TELEGRAM_RATIO="${TELEGRAM_RATIO:-0.6}"
DB_POOL_SIZE="${DB_POOL_SIZE:-32}"
DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-32}"
KEEP_DATA="${KEEP_DATA:-0}"
MAX_SCENARIOS=""
families=()
scenarios=()

compose_cmd=(
    docker compose
    -p "$STAGING_PROJECT_NAME"
    --env-file "$ENV_FILE"
    -f "$COMPOSE_FILE"
)

log() {
    printf '[staging-full-load] %s\n' "$*"
}

die() {
    printf '[staging-full-load] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<EOF
Usage: scripts/run_staging_comprehensive_load_matrix.sh [options]

Options:
  --prefix VALUE               Synthetic data prefix. Default: $PREFIX
  --artifact-dir PATH          Host artifact directory. Default: $ARTIFACT_DIR
  --users N                    Synthetic user count. Default: $USER_COUNT
  --attempts-per-scenario N    Business attempts per logical scenario. Default: $ATTEMPTS_PER_SCENARIO
  --target-rps N               Target business request RPS per scenario. Default: $TARGET_RPS
  --telegram-ratio N           Telegram request ratio. Default: $TELEGRAM_RATIO
  --family NAME                Run only a scenario family. Repeatable.
  --scenario ID_OR_NAME        Run only a scenario id/name. Repeatable.
  --max-scenarios N            Run only the first N selected scenarios.
  --keep-data                  Do not clean synthetic staging DB rows after the run.
  -h, --help                   Show this help.
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
        --users)
            USER_COUNT="${2:?missing --users value}"
            shift 2
            ;;
        --attempts-per-scenario)
            ATTEMPTS_PER_SCENARIO="${2:?missing --attempts-per-scenario value}"
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
        --family)
            families+=("${2:?missing --family value}")
            shift 2
            ;;
        --scenario)
            scenarios+=("${2:?missing --scenario value}")
            shift 2
            ;;
        --max-scenarios)
            MAX_SCENARIOS="${2:?missing --max-scenarios value}"
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

log "checking staging health"
scripts/deploy_staging.sh health >/dev/null

log "checking load-runner runtime guards"
run_load_service load_telegram_foreign \
    python scripts/trading_core_probe_worker.py load-runner-ready --role telegram_foreign >/dev/null
run_load_service load_webapp_iran \
    python scripts/trading_core_probe_worker.py load-runner-ready --role webapp_iran >/dev/null

args=(
    --prefix "$PREFIX"
    --user-count "$USER_COUNT"
    --attempts-per-scenario "$ATTEMPTS_PER_SCENARIO"
    --target-rps "$TARGET_RPS"
    --telegram-ratio "$TELEGRAM_RATIO"
    --output /artifacts/comprehensive-matrix.json
    --check
)
if [[ "$KEEP_DATA" == "1" ]]; then
    args+=(--keep-data)
fi
if [[ -n "$MAX_SCENARIOS" ]]; then
    args+=(--max-scenarios "$MAX_SCENARIOS")
fi
for family in "${families[@]}"; do
    args+=(--family "$family")
done
for scenario in "${scenarios[@]}"; do
    args+=(--scenario "$scenario")
done

log "running comprehensive matrix users=$USER_COUNT attempts_per_scenario=$ATTEMPTS_PER_SCENARIO target_rps=$TARGET_RPS telegram_ratio=$TELEGRAM_RATIO"
run_load_service load_webapp_iran \
    python scripts/run_bot_webapp_comprehensive_load_matrix.py "${args[@]}" \
    >"$ARTIFACT_DIR/comprehensive.stdout.log" \
    2>"$ARTIFACT_DIR/comprehensive.stderr.log"

log "summary"
python3 - "$ARTIFACT_DIR/comprehensive-matrix.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    json.dumps(
        {
            "status": payload.get("status"),
            "scenario_count": payload.get("scenario_count"),
            "family_counts": payload.get("family_counts"),
            "total_business_requests": payload.get("total_business_requests"),
            "aggregate_business_request_rps": payload.get("aggregate_business_request_rps"),
            "failed_scenarios": payload.get("failed_scenarios", [])[:10],
            "production_gate": (payload.get("production_gate") or {}).get("status"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
)
PY

log "artifacts: $ARTIFACT_DIR"
