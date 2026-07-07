#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
ARTIFACT_ROOT="${ROLE_TRADING_E2E_ARTIFACT_ROOT:-$ROOT_DIR/tmp/staging-role-trading-e2e/$(date -u +%Y%m%dT%H%M%SZ)}"

STAGING_APP_CONTAINER_NAME="${E2E_APP_CONTAINER_NAME:-trading_bot_staging-app-1}"
STAGING_REDIS_CONTAINER_NAME="${E2E_REDIS_CONTAINER_NAME:-trading_bot_staging-redis-1}"
STAGING_BACKEND_BASE_URL="${E2E_BACKEND_BASE_URL:-http://127.0.0.1:${STAGING_APP_PORT:-8100}}"
STAGING_CONFIRM_VALUE="role-trading-staging-only"

SPEC_FILES=(
  "e2e/market-offers.spec.ts"
  "e2e/market-schedule.spec.ts"
  "e2e/lot-suggestion.spec.ts"
  "e2e/trade-history-accountant.spec.ts"
  "e2e/customer-owner-flow.spec.ts"
  "e2e/accountant-owner-flow.spec.ts"
)

CLEANUP_PREFIXES=(
  "pw_warning_"
  "pw_warn_"
  "pw_market_"
  "pw_customer_"
  "pw_exec_"
  "pw_tier1_"
  "pw_viewer_"
  "pw_owner_"
  "pw_trade_"
  "pw_pp_"
  "pw_block_"
  "pw_accountant_"
)

COMMODITY_NAME_PREFIXES=(
  "PW Trade"
  "PW History"
  "PW Block"
  "PW Customer"
  "کالای تست"
  "کالای قیمت مشتری"
  "کالای اجرای مشتری"
  "کالای زمان‌بندی"
)

log() {
  printf '[role-trading-e2e] %s\n' "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

write_json() {
  local path="$1"
  shift
  python3 - "$path" "$@" <<'PY'
import json
import sys
path = sys.argv[1]
payload = {}
for item in sys.argv[2:]:
    key, _, value = item.partition("=")
    payload[key] = value
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

assert_safe_staging_target() {
  [[ "$STAGING_APP_CONTAINER_NAME" != "trading_bot_app" ]] || die "refusing to target production-like container trading_bot_app"
  [[ "$STAGING_APP_CONTAINER_NAME" == *staging* ]] || die "staging app container must include 'staging': $STAGING_APP_CONTAINER_NAME"
  [[ "$STAGING_REDIS_CONTAINER_NAME" != "trading_bot_redis" ]] || die "refusing to target production-like Redis container trading_bot_redis"
  [[ "$STAGING_REDIS_CONTAINER_NAME" == *staging* ]] || die "staging Redis container must include 'staging': $STAGING_REDIS_CONTAINER_NAME"
  [[ "$STAGING_BACKEND_BASE_URL" != "http://127.0.0.1:8000" ]] || die "refusing to target default production-like backend URL"
  [[ "$STAGING_BACKEND_BASE_URL" == *":8100"* || "$STAGING_BACKEND_BASE_URL" == *staging* ]] || die "backend URL must visibly point to staging: $STAGING_BACKEND_BASE_URL"

  local running
  running="$(docker inspect -f '{{.State.Running}}' "$STAGING_APP_CONTAINER_NAME" 2>/dev/null || true)"
  [[ "$running" == "true" ]] || die "staging app container is not running: $STAGING_APP_CONTAINER_NAME"
  running="$(docker inspect -f '{{.State.Running}}' "$STAGING_REDIS_CONTAINER_NAME" 2>/dev/null || true)"
  [[ "$running" == "true" ]] || die "staging Redis container is not running: $STAGING_REDIS_CONTAINER_NAME"

  docker exec "$STAGING_APP_CONTAINER_NAME" python - <<'PY' >"$ARTIFACT_ROOT/container-env.json"
import json
from core.config import settings
from core.server_routing import current_server
payload = {
    "environment": str(getattr(settings, "environment", "") or "").strip().lower(),
    "server_mode": current_server(),
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY

  python3 - "$ARTIFACT_ROOT/container-env.json" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("environment") != "staging":
    raise SystemExit(f"target container is not staging: {payload}")
PY

  curl -fsS --max-time 10 "$STAGING_BACKEND_BASE_URL/api/config" >"$ARTIFACT_ROOT/backend-config.json"
  python3 - "$ARTIFACT_ROOT/backend-config.json" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
frontend_url = str(payload.get("frontend_url") or "")
if "staging" not in frontend_url:
    raise SystemExit(f"backend config does not look like staging: {payload}")
PY
}

cleanup_prefix() {
  local prefix="$1"
  local phase="$2"
  local dry_run_path="$ARTIFACT_ROOT/${phase}-${prefix}-dry-run.json"
  local delete_path="$ARTIFACT_ROOT/${phase}-${prefix}-delete.json"

  docker exec "$STAGING_APP_CONTAINER_NAME" python scripts/trading_core_probe_worker.py cleanup \
    --prefix "$prefix" \
    --dry-run >"$dry_run_path"

  docker exec "$STAGING_APP_CONTAINER_NAME" python scripts/trading_core_probe_worker.py cleanup \
    --prefix "$prefix" >"$delete_path"
}

cleanup_test_commodities() {
  local phase="$1"
  docker exec -i "$STAGING_APP_CONTAINER_NAME" python - "${COMMODITY_NAME_PREFIXES[@]}" <<'PY' >"$ARTIFACT_ROOT/${phase}-commodity-cleanup.json"
import asyncio
import json
import sys
from sqlalchemy import delete, select
from core.db import AsyncSessionLocal
from models.commodity import Commodity, CommodityAlias

prefixes = sys.argv[1:]

async def main():
    async with AsyncSessionLocal() as db:
        ids = []
        for prefix in prefixes:
            result = await db.execute(select(Commodity.id).where(Commodity.name.like(f"{prefix}%")))
            ids.extend(int(item) for item in result.scalars().all())
        ids = sorted(set(ids))
        if ids:
            await db.execute(delete(CommodityAlias).where(CommodityAlias.commodity_id.in_(ids)))
            await db.execute(delete(Commodity).where(Commodity.id.in_(ids)))
            await db.commit()
    print(json.dumps({"commodity_ids": ids, "deleted": len(ids)}, ensure_ascii=False, sort_keys=True))

asyncio.run(main())
PY
}

cleanup_all() {
  local phase="$1"
  for prefix in "${CLEANUP_PREFIXES[@]}"; do
    cleanup_prefix "$prefix" "$phase"
  done
  cleanup_test_commodities "$phase"
}

main() {
  require_command docker
  require_command curl
  require_command python3
  require_command npm
  require_command npx

  mkdir -p "$ARTIFACT_ROOT"
  write_json "$ARTIFACT_ROOT/run-config.json" \
    "artifact_root=$ARTIFACT_ROOT" \
    "app_container=$STAGING_APP_CONTAINER_NAME" \
    "redis_container=$STAGING_REDIS_CONTAINER_NAME" \
    "backend_base_url=$STAGING_BACKEND_BASE_URL" \
    "branch=$(git -C "$ROOT_DIR" branch --show-current)" \
    "head=$(git -C "$ROOT_DIR" rev-parse HEAD)"

  assert_safe_staging_target
  cleanup_all "pre"

  export E2E_TARGET_ENV=staging
  export E2E_ALLOW_STAGING_MUTATION="$STAGING_CONFIRM_VALUE"
  export E2E_APP_CONTAINER_NAME="$STAGING_APP_CONTAINER_NAME"
  export E2E_REDIS_CONTAINER_NAME="$STAGING_REDIS_CONTAINER_NAME"
  export E2E_BACKEND_BASE_URL="$STAGING_BACKEND_BASE_URL"
  export VITE_DEV_PROXY_TARGET="$STAGING_BACKEND_BASE_URL"
  unset VITE_API_BASE_URL
  export PLAYWRIGHT_JSON_OUTPUT_NAME="$ARTIFACT_ROOT/report.json"
  export PLAYWRIGHT_HTML_REPORT="$ARTIFACT_ROOT/html-report"

  set +e
  (
    cd "$FRONTEND_DIR"
    npx playwright test "${SPEC_FILES[@]}" --project=chromium --workers=1 --reporter=line,json,html
  ) 2>&1 | tee "$ARTIFACT_ROOT/playwright.log"
  local test_status="${PIPESTATUS[0]}"
  set -e

  cleanup_all "post"

  if [[ "$test_status" -ne 0 ]]; then
    die "role/trading e2e gate failed; artifacts: $ARTIFACT_ROOT"
  fi

  log "role/trading e2e gate passed; artifacts: $ARTIFACT_ROOT"
}

main "$@"
