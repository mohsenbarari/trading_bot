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
  "pwacct_"
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

is_explicit_staging_app_container() {
  local name="$1"
  [[ "$name" == *staging* ]] && return 0
  [[ "$name" =~ ^trading-bot-three-site-stage[0-9]+-[0-9a-f-]+-webapp-(fi|ir)-webapp_(fi|ir)_api-1$ ]]
}

is_explicit_staging_redis_container() {
  local name="$1"
  [[ "$name" == *staging* ]] && return 0
  [[ "$name" =~ ^trading-bot-three-site-stage[0-9]+-[0-9a-f-]+-webapp-(fi|ir)-webapp_(fi|ir)_redis-1$ ]]
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
  is_explicit_staging_app_container "$STAGING_APP_CONTAINER_NAME" || die "staging app container is not an approved staging name: $STAGING_APP_CONTAINER_NAME"
  [[ "$STAGING_REDIS_CONTAINER_NAME" != "trading_bot_redis" ]] || die "refusing to target production-like Redis container trading_bot_redis"
  is_explicit_staging_redis_container "$STAGING_REDIS_CONTAINER_NAME" || die "staging Redis container is not an approved staging name: $STAGING_REDIS_CONTAINER_NAME"
  [[ "$STAGING_BACKEND_BASE_URL" != "http://127.0.0.1:8000" ]] || die "refusing to target default production-like backend URL"
  [[ "$STAGING_BACKEND_BASE_URL" == *":8100"* || "$STAGING_BACKEND_BASE_URL" == *staging* ]] || die "backend URL must visibly point to staging: $STAGING_BACKEND_BASE_URL"

  local running
  running="$(docker inspect -f '{{.State.Running}}' "$STAGING_APP_CONTAINER_NAME" 2>/dev/null || true)"
  [[ "$running" == "true" ]] || die "staging app container is not running: $STAGING_APP_CONTAINER_NAME"
  running="$(docker inspect -f '{{.State.Running}}' "$STAGING_REDIS_CONTAINER_NAME" 2>/dev/null || true)"
  [[ "$running" == "true" ]] || die "staging Redis container is not running: $STAGING_REDIS_CONTAINER_NAME"

  docker exec -i "$STAGING_APP_CONTAINER_NAME" python - <<'PY' >"$ARTIFACT_ROOT/container-env.json"
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

  run_three_site_fenced_cleanup "$prefix" dry-run >"$dry_run_path"
  run_three_site_fenced_cleanup "$prefix" delete >"$delete_path"
}

run_three_site_fenced_cleanup() {
  local prefix="$1"
  local mode="$2"
  docker exec -i "$STAGING_APP_CONTAINER_NAME" python - "$prefix" "$mode" <<'PY'
import asyncio
import json
import sys

from core.config import settings
from core.db import AsyncSessionLocal
from core.runtime_identity import resolve_runtime_identity
from core.webapp_writer_control import load_writer_snapshot
from core.writer_fencing import writer_fence_scope
from models.accountant_relation import AccountantRelation
from models.chat_member import ChatMember
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.notification import Notification
from models.offer import Offer
from models.offer_publication_state import OfferPublicationState
from models.offer_request import OfferRequest
from models.push_subscription import PushSubscription
from models.session import (
    SessionLoginRequest,
    SingleSessionRecoveryAdminTarget,
    SingleSessionRecoveryRequest,
    UserSession,
)
from models.telegram_admin_broadcast import TelegramAdminBroadcast, TelegramAdminBroadcastReceipt
from models.telegram_link_token import TelegramLinkToken
from models.trade import Trade
from models.trade_delivery_receipt import TradeDeliveryReceipt
from models.user import User
from models.user_block import UserBlock
from scripts.trading_core_probe_worker import (
    cleanup_redis_for_user_ids,
    cleanup_report_payload,
    collect_cleanup_plan,
)
from sqlalchemy import select

prefix = sys.argv[1]
dry_run = sys.argv[2] == "dry-run"

async def delete_by_ids(db, model, ids):
    deleted = 0
    for record_id in ids:
        record = await db.get(model, record_id)
        if record is not None:
            await db.delete(record)
            deleted += 1
    await db.flush()
    return deleted

async def main():
    plan = await collect_cleanup_plan(prefix)
    if dry_run:
        planned_redis_keys = await cleanup_redis_for_user_ids(plan.user_ids, dry_run=True)
        print(json.dumps(cleanup_report_payload(
            plan=plan,
            dry_run=True,
            deleted_redis_keys=planned_redis_keys,
        ), ensure_ascii=False, sort_keys=True))
        return

    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_authority:
        raise RuntimeError("three-site E2E cleanup requires a WebApp authority")
    async with AsyncSessionLocal() as control_db:
        snapshot = await load_writer_snapshot(control_db)

    with writer_fence_scope(
        identity,
        snapshot,
        source="staging_role_trading_e2e_cleanup",
        require_witness_lease=bool(settings.writer_witness_required),
    ):
        async with AsyncSessionLocal() as db:
            deleted_recovery_admin_targets = await delete_by_ids(
                db, SingleSessionRecoveryAdminTarget, plan.recovery_admin_target_ids)
            deleted_recovery_requests = await delete_by_ids(
                db, SingleSessionRecoveryRequest, plan.recovery_request_ids)
            deleted_session_login_requests = await delete_by_ids(
                db, SessionLoginRequest, plan.session_login_request_ids)
            deleted_user_sessions = await delete_by_ids(db, UserSession, plan.user_session_ids)
            deleted_telegram_link_tokens = await delete_by_ids(
                db, TelegramLinkToken, plan.telegram_link_token_ids)
            deleted_push_subscriptions = await delete_by_ids(db, PushSubscription, plan.push_subscription_ids)
            deleted_trade_delivery_receipts = await delete_by_ids(
                db, TradeDeliveryReceipt, plan.trade_delivery_receipt_ids)
            deleted_telegram_admin_broadcast_receipts = await delete_by_ids(
                db, TelegramAdminBroadcastReceipt, plan.telegram_admin_broadcast_receipt_ids)
            deleted_notifications = await delete_by_ids(db, Notification, plan.notification_ids)
            deleted_telegram_admin_broadcasts = await delete_by_ids(
                db, TelegramAdminBroadcast, plan.telegram_admin_broadcast_ids)
            deleted_publication_states = await delete_by_ids(
                db, OfferPublicationState, plan.publication_state_ids)
            deleted_offer_requests = await delete_by_ids(db, OfferRequest, plan.offer_request_ids)
            deleted_chat_members = await delete_by_ids(db, ChatMember, plan.chat_member_ids)
            deleted_user_blocks = await delete_by_ids(db, UserBlock, plan.user_block_ids)
            deleted_accountant_relations = await delete_by_ids(
                db, AccountantRelation, plan.accountant_relation_ids)
            deleted_customer_relations = await delete_by_ids(
                db, CustomerRelation, plan.customer_relation_ids)
            deleted_invitations = await delete_by_ids(db, Invitation, plan.invitation_ids)
            deleted_trades = await delete_by_ids(db, Trade, plan.trade_ids)
            deleted_offers = await delete_by_ids(db, Offer, plan.offer_ids)
            residual_members = list((await db.execute(
                select(ChatMember).where(ChatMember.user_id.in_(plan.user_ids))
            )).scalars().all()) if plan.user_ids else []
            for member in residual_members:
                await db.delete(member)
            await db.flush()
            deleted_chat_members += len(residual_members)
            deleted_users = await delete_by_ids(db, User, plan.user_ids)
            await db.commit()

    deleted_redis_keys = await cleanup_redis_for_user_ids(plan.user_ids)
    print(json.dumps(cleanup_report_payload(
        plan=plan,
        dry_run=False,
        deleted_users=deleted_users,
        deleted_invitations=deleted_invitations,
        deleted_accountant_relations=deleted_accountant_relations,
        deleted_customer_relations=deleted_customer_relations,
        deleted_user_sessions=deleted_user_sessions,
        deleted_session_login_requests=deleted_session_login_requests,
        deleted_recovery_requests=deleted_recovery_requests,
        deleted_recovery_admin_targets=deleted_recovery_admin_targets,
        deleted_telegram_link_tokens=deleted_telegram_link_tokens,
        deleted_push_subscriptions=deleted_push_subscriptions,
        deleted_chat_members=deleted_chat_members,
        deleted_user_blocks=deleted_user_blocks,
        deleted_offers=deleted_offers,
        deleted_trades=deleted_trades,
        deleted_trade_delivery_receipts=deleted_trade_delivery_receipts,
        deleted_telegram_admin_broadcasts=deleted_telegram_admin_broadcasts,
        deleted_telegram_admin_broadcast_receipts=deleted_telegram_admin_broadcast_receipts,
        deleted_notifications=deleted_notifications,
        deleted_offer_requests=deleted_offer_requests,
        deleted_publication_states=deleted_publication_states,
        deleted_change_logs=0,
        deleted_redis_keys=deleted_redis_keys,
    ), ensure_ascii=False, sort_keys=True))

asyncio.run(main())
PY
}

cleanup_test_commodities() {
  local phase="$1"
  docker exec -i "$STAGING_APP_CONTAINER_NAME" python - "${COMMODITY_NAME_PREFIXES[@]}" <<'PY' >"$ARTIFACT_ROOT/${phase}-commodity-cleanup.json"
import asyncio
import json
import sys
from sqlalchemy import select
from core.config import settings
from core.db import AsyncSessionLocal
from core.runtime_identity import resolve_runtime_identity
from core.webapp_writer_control import load_writer_snapshot
from core.writer_fencing import writer_fence_scope
from models.commodity import Commodity, CommodityAlias

prefixes = sys.argv[1:]

async def main():
    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_authority:
        raise RuntimeError("three-site E2E commodity cleanup requires a WebApp authority")
    async with AsyncSessionLocal() as control_db:
        snapshot = await load_writer_snapshot(control_db)
    with writer_fence_scope(
        identity,
        snapshot,
        source="staging_role_trading_e2e_commodity_cleanup",
        require_witness_lease=bool(settings.writer_witness_required),
    ):
        async with AsyncSessionLocal() as db:
            commodities = []
            for prefix in prefixes:
                result = await db.execute(select(Commodity).where(Commodity.name.like(f"{prefix}%")))
                commodities.extend(result.scalars().all())
            by_id = {int(commodity.id): commodity for commodity in commodities}
            ids = sorted(by_id)
            for commodity in by_id.values():
                aliases = list((await db.execute(
                    select(CommodityAlias).where(CommodityAlias.commodity_id == commodity.id)
                )).scalars().all())
                for alias in aliases:
                    await db.delete(alias)
                await db.delete(commodity)
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
