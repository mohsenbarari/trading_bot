# Trade Notification Delivery Guarantee Stage 0 Report

Date: 2026-06-23T07:13:24Z

Branch: `candidate/bot-webapp-integration`

Contract: `docs/TRADE_NOTIFICATION_DELIVERY_GUARANTEE_IMPLEMENTATION_CONTRACT.md`

Roadmap: `docs/TRADE_NOTIFICATION_DELIVERY_GUARANTEE_ROADMAP.md`

## Status

Stage 0 is complete.

This stage was read-only for application code. It produced this freshness and discovery report only.

## Branch And Freshness Snapshot

- Active branch: `candidate/bot-webapp-integration`
- Worktree before report creation: clean
- `HEAD`: `37c2436aa3be10633305a33589c2dbbe03ab423d`
- `origin/candidate/bot-webapp-integration`: `37c2436aa3be10633305a33589c2dbbe03ab423d`
- `main`: `63c643ced0c61a290feeb0598fa8e71ebe933cc0`
- `origin/main`: `63c643ced0c61a290feeb0598fa8e71ebe933cc0`
- Merge base with `origin/main`: `63c643ced0c61a290feeb0598fa8e71ebe933cc0`
- Divergence `origin/main...HEAD`: `0 151`
- `git merge-tree --write-tree origin/main HEAD`: `cc46317f61641c909e698eeafa2e7827532e590e`
- `HEAD^{tree}`: `cc46317f61641c909e698eeafa2e7827532e590e`

Interpretation:

- The candidate branch is aligned with its remote.
- The local `main` and `origin/main` refs are aligned.
- `origin/main` is the merge base, so the candidate branch is not behind main.
- The merge-tree output equals the current `HEAD` tree, so no pre-implementation merge from main is required at this snapshot.

## Required Repository Searches

The required Stage 0 keyword searches were performed before any code change.

### `TRADE_NOTIFICATION_DELIVERY_GUARANTEE_ROADMAP`

Relevant files:

- `docs/TRADE_NOTIFICATION_DELIVERY_GUARANTEE_ROADMAP.md`
- `docs/TRADE_NOTIFICATION_DELIVERY_GUARANTEE_IMPLEMENTATION_CONTRACT.md`

Finding:

- Roadmap and contract are aligned. No product-decision blocker remains.

### `create_user_notification`

Relevant files:

- `core/utils.py`
- `api/routers/trades.py`
- `api/routers/admin_messages.py`
- `api/routers/users.py`
- `api/routers/auth.py`
- `bot/handlers/admin_users.py`
- `core/services/user_account_status_service.py`
- `tests/test_core_utils_runtime.py`
- `tests/test_notifications_router_reads.py`
- `tests/test_notifications_router_mutations.py`
- `tests/test_notifications_router_stream.py`
- `tests/test_trades_router_authoritative_success.py`

Finding:

- `core.utils.create_user_notification` commits internally and merges `extra_payload` only into the realtime payload. Receipt-backed WebApp delivery must not call it directly for trade-completion receipts unless adapted through a no-auto-commit/idempotent helper.
- Existing non-trade/admin/system notification behavior must remain untouched.

### `_build_trade_notification`, trade message bundle, and audience helpers

Relevant files:

- `api/routers/trades.py`
- `core/services/accountant_relation_service.py`
- `tests/test_trades_router_helpers.py`
- `tests/test_trade_execution_seams.py`
- `tests/test_accountant_relation_service.py`
- `tests/test_trades_router_authoritative_success.py`
- `tests/test_trades_router_reads.py`

Finding:

- Existing trade notification message/payload helpers are concentrated in `api/routers/trades.py`.
- Accountant/owner audience expansion already exists in `build_trade_notification_audience_user_ids`.
- Stage 3 must reuse these paths and customer relation behavior instead of rebuilding customer-chain semantics from scratch.

### `_queue_trade_telegram_message` and `send_telegram_message_sync`

Relevant files:

- `api/routers/trades.py`
- `tests/test_trades_router_helpers.py`
- `scripts/trading_core_probe_worker.py`

Finding:

- Direct trade-completion Telegram side effects still exist in `api/routers/trades.py`.
- Stage 8 must add receipt-backed Telegram worker behavior first; Stage 9 must then remove or route direct side effects through the receipt-backed gate.
- Static/grep tests are required before direct sends are removed.

### `trade_number` and `NATURAL_KEYS`

Relevant files:

- `models/trade.py`
- `api/routers/sync.py`
- `core/sync_metadata.py`
- `core/events.py`
- `tests/test_sync_router_apply_item_success.py`
- `tests/test_sync_metadata.py`
- `tests/test_trading_production_contract_matrix.py`
- trade router and bot trade tests using `trade_number`

Finding:

- `Trade.trade_number` is unique and indexed.
- Sync uses `NATURAL_KEYS["trades"] = "trade_number"`.
- Current trade upsert updates by `ON CONFLICT (trade_number)` and can overwrite incoming columns without a completed-trade guard.
- Natural-key fallback can update by `trade_number`.
- Stage 2 is required before shadow reconciliation because completed trades must be protected as durable facts.

### `offer_requests`

Relevant files:

- `models/offer_request.py`
- `core/services/offer_request_ledger_service.py`
- `api/routers/trades.py`
- `core/events.py`
- `api/routers/sync.py`
- `tests/test_offer_request_ledger_model.py`
- `tests/test_offer_request_ledger_service.py`
- `tests/test_offer_request_policy.py`
- `tests/test_sync_router_apply_item_success.py`
- `tests/test_core_events.py`

Finding:

- Offer request ledger already captures request-source/idempotency data and syncs as product data.
- Stage 3 audience derivation and Stage 5 receipt derivation should treat committed trades and existing ledger outcomes as source facts, not duplicate request logic.

### `get_web_only_bot_access_reason`

Relevant files:

- `bot/handlers/link_account.py`
- `bot/handlers/start.py`
- `core/services/accountant_relation_service.py`
- `core/services/customer_relation_service.py`
- `tests/test_bot_link_account_guards.py`
- `tests/test_bot_link_account_success.py`
- `tests/test_bot_start_without_token.py`

Finding:

- Current bot access blocking still uses broad accountant/customer checks.
- `/start` without token and direct `/link` still have contact-collection paths today.
- Stage 1 must introduce shared explicit bot eligibility and WebApp-issued token linking before tier-1 bot access is enabled.

## Test Inventory

Existing tests that should be reused or extended:

- Bot linking: `tests/test_bot_link_account_cmd.py`, `tests/test_bot_link_account_guards.py`, `tests/test_bot_link_account_success.py`, `tests/test_bot_start_without_token.py`
- Bot trading: `tests/test_bot_trade_create_*.py`, `tests/test_bot_trade_execute_*.py`, `tests/test_bot_trade_manage_*.py`, `tests/test_bot_trade_contention_gate_middleware.py`
- WebApp/API trading: `tests/test_trades_router_authoritative_success.py`, `tests/test_trades_router_authoritative_guards.py`, `tests/test_trades_router_execution_wrappers.py`, `tests/test_trades_router_helpers.py`, `tests/test_trades_router_reads.py`
- Customer/accountant relations: `tests/test_customer_relation_service.py`, `tests/test_customers_router.py`, `tests/test_accountant_relation_service.py`, `tests/test_accountants_router.py`
- Notifications: `tests/test_core_notifications_runtime.py`, `tests/test_notifications_router_reads.py`, `tests/test_notifications_router_mutations.py`, `tests/test_notifications_router_stream.py`, `frontend/src/stores/notifications.test.ts`, `frontend/src/types/notifications.test.ts`, `frontend/src/utils/notificationUi.test.ts`
- Sync: `tests/test_sync_router_stale_events.py`, `tests/test_sync_router_apply_item_success.py`, `tests/test_sync_router_apply_item_errors.py`, `tests/test_sync_router_receive_basic.py`, `tests/test_sync_router_receive_errors.py`, `tests/test_sync_router_remaining_paths.py`, `tests/test_sync_registry.py`, `tests/test_sync_field_policy.py`, `tests/test_sync_metadata.py`, `tests/test_sync_worker.py`
- Offer request ledger: `tests/test_offer_request_ledger_model.py`, `tests/test_offer_request_ledger_service.py`, `tests/test_offer_request_policy.py`
- Telegram gateway: `tests/test_telegram_gateway_policy.py`
- Load/matrix/evidence: `tests/test_bot_webapp_sync_evidence.py`, existing bot/WebApp comprehensive load and capacity scripts under `scripts/`

## Stage 0 Test Decision

No application tests were run in Stage 0 because this stage made no application-code change. The
required verification for this stage is branch/freshness discovery, repository keyword discovery,
report creation, and `git diff --check` before commit.

## Risks Found

1. `create_user_notification` is not receipt-transaction-safe for trade-completion delivery because
   it commits internally.
2. Direct Telegram send helpers still exist on trade-completion paths and must be gated/removed only
   after receipt-backed Telegram repair is ready.
3. Trade sync currently has `trade_number` identity but no completed-trade guard before upsert,
   natural-key fallback, and delete.
4. Current bot linking still has direct contact-collection paths outside WebApp-issued token flow.
5. Customer/accountant trade notification behavior is already complex and must be reused, not
   reimplemented.

## Stage 1 Start Condition

Stage 1 can start.

Required first action for Stage 1:

- Run the Stage 1 keyword searches listed in the contract before editing code.
- Implement shared backend bot eligibility before changing WebApp UI or bot linking behavior.

## Remaining Stages

1. Stage 1: Bot Eligibility And Account Linking Foundation
2. Stage 2: Completed Trade Sync Guard
3. Stage 3: Read-Only Audience Builder
4. Stage 4: Schema Foundation
5. Stage 5: Receipt State Machine And Idempotent Services
6. Stage 6: Shadow Reconciliation
7. Stage 7: WebApp Delivery Repair
8. Stage 8: Telegram Classifier And Foreign Worker
9. Stage 9: Remove Direct Trade Completion Side Effects
10. Stage 10: Outage, Retention, And Support Audit
11. Stage 11: Staging Validation Matrix And Load
12. Stage 12: Cutover Readiness And Production Gate
