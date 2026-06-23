# Trade Notification Delivery Guarantee Implementation Contract

Date: 2026-06-23

Branch: `candidate/bot-webapp-integration`

Source roadmap: `docs/TRADE_NOTIFICATION_DELIVERY_GUARANTEE_ROADMAP.md`

This contract turns the trade notification delivery guarantee roadmap into a staged implementation
sequence. The roadmap is the source of product and engineering decisions. This file is the execution
contract for coding, tests, commits, staging validation, and stop/go gates.

## Contract Status

- The roadmap has no remaining product-decision blocker.
- Implementation must happen on `candidate/bot-webapp-integration` or on a branch explicitly created
  from it for this work.
- No change from this contract may be merged to another branch or deployed to production without an
  explicit owner request.
- Staging is the only runtime validation target until production approval is explicitly given later.
- This contract is intentionally stage based: small related tasks are grouped, while large or
  sensitive changes are split into separate stages with their own gates.

## Global Rules For Every Stage

1. Check the active branch with `git branch --show-current` before every edit, test run with side
   effects, commit, push, staging deploy, or validation action.
2. Keep each stage independently reviewable. A stage must end with a clean worktree, focused commit,
   pushed branch, and a short verification note.
3. Do not mix unrelated WebApp fixes, messenger work, production cleanup, UI polish, or deployment
   changes into these stages.
4. Any schema migration must be additive and reversible unless the owner explicitly approves a
   destructive operation.
5. Any policy conflict between roadmap, contract, tests, and code must stop the stage. Do not resolve
   product policy conflicts by assumption.
6. Every sensitive change must begin with repository keyword searches. The search results must be
   used to reuse existing code and tests before adding new abstractions.
7. Tests must be complete, precise, and broad enough to cover all known and reasonably possible
   states. Happy-path-only tests do not complete any stage.
8. If a stage cannot test a scenario automatically, the reason and the manual/staging verification
   plan must be documented before the stage is considered complete.
9. Before enabling a new sender or reconciler, direct legacy side effects for the same channel must
   either route through the receipt-backed gate or be disabled for that channel.
10. Iran must never call Telegram. Telegram execution remains foreign-only.

## Mandatory Pre-Change Repository Search Rule

Before changing a sensitive area, search the repository for related keywords and read the existing
code and tests. This is required to prevent duplicate logic, missed guards, or behavior drift.

Minimum search requirements:

- Use `rg` or `rg --files` first.
- Search both implementation and tests.
- Search current helpers before creating a new helper.
- Search direct side-effect calls before adding receipt-backed delivery.
- Search sync registry/field policy/outbox paths before adding synced state.
- Search frontend realtime/history handling before changing notification payloads.

Each stage below lists required keyword families. Add more searches if code discovery points to
adjacent surfaces.

## Exhaustive Test Coverage Rule

Every stage must add or update enough tests to cover all relevant combinations for that stage.

Required dimensions across the whole contract:

- role: `STANDARD`, `POLICE`, `MIDDLE_MANAGER`, `SUPER_ADMIN`, `WATCH`, accountant, tier-1 customer,
  tier-2 customer
- account state: active, inactive, deleted, market-blocked, trading-restricted
- Telegram link state: linked, unlinked, linked to another Telegram id, unreachable Telegram account,
  expired/invalid/not-yet-synced link token
- trade side: offer owner, responder, customer owner/principal, customer-chain recipient, accountant
  monitoring recipient
- surface: WebApp, Telegram bot
- server authority: Iran WebApp, foreign bot, `offer.home_server=iran`, `offer.home_server=foreign`
- receipt channel: `webapp`, `telegram`
- receipt state: `pending`, `processing`, `retry_pending`, `sent`, `skipped`, `not_required`,
  `permanent_failed`
- sync condition: stable sync, delayed sync, short outage, medium outage, long outage, stale update,
  destructive completed-trade update, destructive completed-trade delete
- concurrency: repeated request replay, many concurrent requests on one offer, worker crash before
  send, worker crash after send before `sent`

No stage is complete if it only proves the ideal path and leaves known edge states untested.

## Stage Completion Definition

A stage is complete only when all of these are true:

- Scope stayed inside the stage boundary.
- Required repository searches were done before sensitive edits.
- Existing code paths were reused where appropriate or the reason for a new abstraction is recorded.
- Tests listed for the stage pass, or skipped tests and reasons are documented.
- `git diff --check` passes.
- No unrelated files are staged.
- The stage commit is focused and names the stage intent.
- The branch is pushed to `origin/candidate/bot-webapp-integration`.
- The next stage's start condition is still true.

## Stage 0: Freshness, Discovery, And Baseline Matrix

Purpose: establish a safe starting point before code changes.

Required searches:

- `TRADE_NOTIFICATION_DELIVERY_GUARANTEE_ROADMAP`
- `create_user_notification`
- `_build_trade_notification`
- `_queue_trade_telegram_message`
- `send_telegram_message_sync`
- `trade_number`
- `NATURAL_KEYS`
- `offer_requests`
- `get_web_only_bot_access_reason`

Implementation work:

- Re-check branch, worktree, local/remote SHA, and merge base with `main`.
- Verify no unrelated changes are present.
- Read the current roadmap and this contract.
- Produce or update a short freshness note if `main` or the candidate branch moved materially.
- Inventory current tests that already cover bot linking, trades, notifications, sync, and customer
  relations.

Required tests/checks:

- No code tests are required if this is purely read-only.
- Run targeted smoke tests only if code discovery indicates branch drift risk.

Stop conditions:

- Dirty worktree with unrelated changes.
- Candidate branch no longer matches the expected implementation base.
- Roadmap/contract conflict.

## Stage 1: Bot Eligibility And Account Linking Foundation

Purpose: make Telegram account linking safe before tier-1 bot access or notification guarantees rely
on it.

This is sensitive but cohesive, so it is split into three implementation slices under one stage.

Required searches:

- `get_web_only_bot_access_reason`
- `is_user_customer`
- `is_user_accountant`
- `UserRole`
- `CommandStart`
- `Command("link")`
- `prompt_contact_for_account_link`
- `finalize_account_link`
- `telegram_id`
- `mandatory channel`
- `WebRegister`
- `DashboardView`
- `SettingsView`

Stage 1A: shared backend eligibility policy.

- Add a shared bot-eligibility policy helper.
- Explicitly allow `STANDARD`, `POLICE`, `MIDDLE_MANAGER`, `SUPER_ADMIN` when account/customer state
  allows it.
- Explicitly deny `WATCH`, accountants, tier-2 customers, deleted users, inactive users,
  market-blocked users, and disconnected soft-deleted customer users.
- Use the helper for token creation, bot linking, bot menu/market visibility, bot offer creation, bot
  trade execution, and mandatory channel join handling.

Stage 1B: WebApp-issued token flow.

- Add the synced link-token state.
- Store only token hashes.
- Enforce short TTL, single-use, revocation of older pending tokens for the same user, and audit
  fields.
- Consume token and write `User.telegram_id` in one transaction on foreign.
- Lock token row and target user row before consume.
- Pre-check duplicate `telegram_id` ownership before writing.

Stage 1C: bot and WebApp entry points.

- `/start` without a valid `link_{token}` must not collect contact.
- Direct `/link` must not collect contact and must return only the neutral low-information fallback
  if still reachable.
- Contact sharing remains mandatory after a valid token is accepted.
- Existing eligible users see connect entry points in dashboard and profile/settings.
- New eligible users see connect/skip after registration completion.
- Skipping Telegram must not reduce WebApp access.

Required tests:

- Allowed roles: `STANDARD`, `POLICE`, `MIDDLE_MANAGER`, `SUPER_ADMIN`.
- Denied roles/states: `WATCH`, accountant, tier-2 customer, deleted, inactive, market-blocked.
- Tier-1 customer allowed only after this stage's policy is enabled.
- Token valid, expired, used, revoked, invalid, and not-yet-synced.
- Token hash only; raw token never persisted.
- Token consume row locks and duplicate `telegram_id` pre-check.
- Contact belongs to Telegram sender and phone matches WebApp account.
- Contact from another Telegram sender is rejected.
- Already linked to another Telegram id is rejected.
- `/start` without valid token returns the neutral low-information response.
- `/link` cannot start account linking without valid WebApp-issued token.
- Dashboard/profile/register frontend states for linked, unlinked, skipped, denied roles.

Stop conditions:

- Any account-link path can bind a Telegram id using phone number alone.
- A denied role can see or use a bot-link path.
- Token consume can race and link one Telegram account to two users.

## Stage 2: Completed Trade Sync Guard

Purpose: protect committed completed trades before any delivery guarantee relies on trades as durable
facts.

Required searches:

- `_build_upsert_stmt`
- `_apply_item`
- `NATURAL_KEYS`
- `trade_number`
- `TradeStatus.COMPLETED`
- `record_sync_conflict`
- `terminal_state_protected`
- `atomic_upsert_guard_noop`
- `tests/test_sync_router_stale_events.py`

Stage 2A: preflight guard.

- Add `_trade_sync_guard_reason`.
- Resolve local trade by `trade_number` first, then local `id`.
- Return specific reasons for completed-trade delete, completed-to-non-completed update, protected
  business-field mismatch, and incomplete destructive payload.
- Log and meter ignored destructive sync events.
- Call the guard before `trades` upsert and before natural-key fallback update.

Stage 2B: atomic write guard.

- Add a PostgreSQL `WHERE` guard to trade upsert so raced destructive completed-trade updates become
  no-ops.
- Add delete protection so completed trades cannot be deleted by `id` or resolved `trade_number`.
- Treat `rowcount == 0` from the atomic guard as an ignored conflict, not a sync success without
  visibility.
- Keep existing offer and offer_request stale/atomic guards intact.

Required tests:

- Synced delete for local completed trade is ignored.
- Synced completed-to-non-completed update is ignored.
- Synced update changing completed trade price, quantity, participant, commodity, offer, or trade
  type is ignored.
- Duplicate completed sync with identical business fields is idempotent.
- Missing incoming completed trade is inserted.
- Local non-completed trade can become completed from valid incoming completed sync.
- Natural-key fallback by `trade_number` cannot bypass the guard.
- Atomic guard handles a race where preflight saw non-completed but write time sees completed.
- Existing offer/offer_request stale guard tests remain green.

Stop conditions:

- Any completed trade can be reopened, hidden, deleted, or business-mutated by sync.
- Natural-key fallback has a path that bypasses the guard.

## Stage 3: Read-Only Audience Builder

Purpose: derive required trade notification recipients without sending anything and without changing
trade execution.

Required searches:

- `_build_trade_notification_extra_payload`
- `_build_trade_notification_message`
- `_build_trade_message_bundle`
- `_load_trade_customer_relation_map_for_user_ids`
- `build_trade_notification_audience_user_ids`
- `accountant`
- `customer_relation`
- `recipient_role`
- `Trade`

Implementation work:

- Build a canonical read-only audience builder from committed trades.
- Reuse current customer-chain and accountant behavior.
- Derive WebApp and Telegram requirements separately.
- Mark Telegram not required for unlinked users, accountants, tier-2 customers, and WebApp-only
  roles.
- Include enough metadata for receipt creation and notification payloads.
- Do not send messages.
- Do not mutate trade state.

Required tests:

- Direct user trade.
- Admin/normal user trade.
- Tier-1 customer trade through owner/principal path.
- Tier-2 customer request-only path.
- Accountant monitoring recipient.
- Customer owner/principal delivery.
- Customer-chain multi-leg trade behavior matches existing route behavior.
- Linked and unlinked Telegram states.
- All `offer.home_server` combinations.
- WebApp required for all required recipients; Telegram only for linked bot-eligible recipients.

Stop conditions:

- Builder reimplements customer trade execution instead of deriving from committed trades.
- Builder changes current customer owner/principal semantics.

## Stage 4: Schema Foundation

Purpose: add durable receipt and notification metadata without changing runtime delivery behavior.

This is sensitive because migrations affect synced state, so it is split into three slices.

Required searches:

- `Notification`
- `notifications`
- `create_user_notification`
- `sync_registry`
- `sync_field_policy`
- `get_model_class`
- `TABLE_ORDER`
- `SHARED_SYNC_TABLES_SQL`
- `migrations/versions`
- `offer_publication_states`
- `offer_requests`

Stage 4A: models and migrations.

- Add `trade_delivery_receipts`.
- Use `event_type + trade_number + recipient_user_id + channel` as unique identity.
- Keep `trade_id` as local FK/lookup, not as cross-server dedupe identity.
- Add receipt fields for status, destination, reason, notification id, Telegram message id, local
  lease, retry, error class, event time, terminal time, and audit timestamps.
- Add `notifications.dedupe_key` nullable.
- Add partial unique index for non-null notification dedupe keys.
- Add `notifications.extra_payload` nullable JSONB.
- Add queue, audit, recipient-history, lease-recovery, and terminal-cleanup indexes.
- No table partitioning in phase 1.

Stage 4B: sync integration.

- Register `trade_delivery_receipts` as synced non-messenger operational data.
- Add model mapping and table order after dependencies.
- Add field policy entries for sensitive payload/error fields.
- Ensure lease fields are local-authoritative only and cannot trigger opposite-server execution.
- Update deployment/shared table lists that seed or synchronize shared product data.

Stage 4C: notification read/history shape.

- Return `extra_payload` or equivalent flattened metadata from notification read APIs.
- Normalize frontend realtime and history notifications through one shared shape.
- Preserve route, trade number, counterparty, recipient role, and receipt metadata after refresh.
- Keep legacy notifications with null dedupe keys working.

Required tests:

- Migration/model tests for columns, constraints, indexes, and downgrade.
- Partial unique index allows many null dedupe keys and rejects duplicate non-null keys.
- Receipt unique key uses `trade_number`, not local `trade_id`.
- Queue and lease-recovery queries use indexed columns.
- Sync registry and field-policy tests cover `trade_delivery_receipts`.
- Notification API read tests include `extra_payload`.
- Frontend tests prove realtime and history use the same normalized schema.
- Legacy notification tests remain green.

Stop conditions:

- Migration requires destructive data cleanup.
- Duplicate receipts can be created for the same trade number, recipient, and channel.
- Notification history loses metadata that realtime has.

## Stage 5: Receipt State Machine And Idempotent Services

Purpose: centralize receipt lifecycle rules before any sender uses them.

Required searches:

- `trade_delivery_receipts`
- `dedupe_key`
- `FOR UPDATE SKIP LOCKED`
- `lease_until`
- `retry_pending`
- `permanent_failed`
- `not_required`
- `create_user_notification`
- `publish_user_event`

Implementation work:

- Add a receipt service/helper that owns all lifecycle transitions.
- Implement upsert by `event_type + trade_number + recipient_user_id + channel`.
- Implement atomic claim for local `destination_server`.
- Implement lease recovery for expired local `processing` rows.
- Prevent terminal-to-non-terminal transitions.
- Implement WebApp notification row creation/loading by dedupe key without committing internally.
- Mark WebApp receipt `sent` in the same transaction as persistent notification creation/loading.
- Publish realtime/Web Push only after durable row state is safe.

Required tests:

- State-machine transition table.
- Terminal states cannot revert.
- Opposite-server receipts cannot be claimed or mutated by local worker.
- Expired local lease can be reclaimed.
- Non-expired local lease cannot be stolen.
- Duplicate WebApp notification conflict is treated as idempotent success.
- Receipt `sent` and Notification row creation happen in one transaction.
- Generic auto-committing notification helper is not used directly for receipt-backed trade delivery.

Stop conditions:

- Any trade delivery path can assign receipt status directly outside the service/helper.
- WebApp `sent` can be recorded without a durable Notification row.

## Stage 6: Shadow Reconciliation

Purpose: prove expected delivery and current gaps without sending new messages.

Required searches:

- `trades`
- `TradeStatus.COMPLETED`
- `trade_number`
- `create_user_notification`
- `_queue_trade_telegram_message`
- `send_telegram_message_sync`
- `background_tasks`
- `reconciler`

Implementation work:

- Add dry-run reconcilers for WebApp and Telegram delivery expectations.
- Derive missing receipts from committed trades and the audience builder.
- Report gaps without sending messages.
- Compare current immediate side effects against expected receipts.
- Detect idempotent trade replay cases where trade exists but receipt/notification is missing.
- Respect `destination_server` ownership in all reports.

Required tests:

- Dry-run creates no user-facing messages.
- Report includes expected recipients per trade.
- Report includes missing WebApp and Telegram delivery obligations.
- Completed trades protected by Stage 2 are visible to reconciliation.
- Replayed existing trade with missing receipt is reported as repairable.
- Opposite-server destination receipts are read-only in local reports.

Stop conditions:

- Shadow mode sends any WebApp notification or Telegram message.
- Reconciler cannot explain why a recipient/channel is required or not required.

## Stage 7: WebApp Delivery Repair

Purpose: enable persistent WebApp delivery repair on Iran first.

Required searches:

- `create_user_notification`
- `publish_user_event`
- `NotificationRead`
- `notifications_router`
- `web_push`
- `dedupe_key`
- `extra_payload`

Implementation work:

- Route immediate WebApp trade notifications and WebApp repair through the receipt-backed helper.
- Claim only WebApp receipts whose `destination_server=iran`.
- Create/load Notification row idempotently by `trade_completed:webapp:{trade_number}:{recipient_user_id}`.
- Mark receipt `sent` in the same transaction.
- Keep realtime and Web Push as after-durable side effects.
- Add static/grep tests to prevent direct generic helper usage for receipt-backed trade delivery.

Required tests:

- WebApp offer/WebApp request.
- Telegram offer/WebApp request after sync visibility.
- Linked and unlinked recipients.
- Customer-chain recipients and accountant monitoring recipients.
- Duplicate repair does not create duplicate notification rows.
- History after refresh preserves metadata.
- Web Push failure does not roll back durable WebApp notification.

Stop conditions:

- WebApp notification can be duplicated for the same trade number and recipient.
- A WebApp receipt can be marked `sent` without durable notification history.

## Stage 8: Telegram Classifier And Foreign Worker

Purpose: add Telegram delivery repair on foreign only after WebApp repair is stable.

Required searches:

- `telegram_gateway`
- `send_telegram_message_sync`
- `_queue_trade_telegram_message`
- `TelegramGatewayResult`
- `retry_after`
- `Forbidden`
- `Bad Request`
- `bot was blocked`
- `chat not found`

Implementation work:

- Add Telegram delivery classifier from structured gateway results.
- Claim only Telegram receipts whose `destination_server=foreign`.
- Send private trade messages only through foreign Telegram worker.
- Classify temporary failures as `retry_pending` with bounded backoff and jitter.
- Classify user Telegram account unreachable errors as `skipped` for that trade only.
- Classify non-user fatal system/config/data errors as `permanent_failed` plus alert/review.
- Reload latest linked Telegram identity on every retry.
- Prefer duplicate-safe resend wording only for ambiguous crash-after-send cases.

Required tests:

- Success with Telegram message id.
- 429 with retry_after.
- Timeout/network failure.
- Telegram 5xx.
- User blocked/deleted/banned/unreachable/chat not found.
- Invalid token/config or malformed payload.
- Unknown errors are not silently skipped.
- Iran cannot execute Telegram delivery.
- Fixed/relinked Telegram account sends only future trade messages, not old skipped backlog.

Stop conditions:

- Iran can call Telegram.
- User-account Telegram failure creates infinite pending backlog.
- Unknown Telegram errors are classified as skipped by default.

## Stage 9: Remove Direct Trade Completion Side Effects

Purpose: prevent duplicate delivery once repair workers can send.

Required searches:

- `_queue_trade_telegram_message`
- `send_telegram_message_sync`
- `create_user_notification`
- `background_tasks.add_task`
- `trading_side_effect`
- `trade_completed`

Implementation work:

- Replace or disable direct WebApp trade-completion notification calls for receipt-backed paths.
- Replace or disable direct Telegram trade-completion sends for receipt-backed paths.
- Trade routes may create/upsert receipts and optionally wake local workers; they must not send the
  channel directly after that channel's repair is enabled.
- Keep non-trade notifications and unrelated admin/system notifications untouched.

Required tests:

- Static/grep tests prove trade-completion paths do not call direct Telegram send helpers after Stage
  8 is enabled.
- Static/grep tests prove receipt-backed WebApp paths do not call the generic auto-committing
  notification helper directly.
- Immediate trade response still returns promptly.
- Repair workers produce the expected user-facing delivery.

Stop conditions:

- A completed trade path can both send directly and enqueue a sendable receipt for the same channel.

## Stage 10: Outage, Retention, And Support Audit

Purpose: make operational behavior explicit for outages and long-term receipt storage.

Required searches:

- `cross_server_recovery`
- `sync_health`
- `unsynced_change_log`
- `retention`
- `cleanup`
- `trade_number`
- `support`

Implementation work:

- Implement short/medium/long outage classification for delivery receipts.
- Short outage recovery may deliver delayed opposite-server messages.
- Medium/long outage recovery must skip old opposite-server delivery with terminal reason and no
  user-facing old-trade report/notification/message.
- Add one-year retention cleanup for terminal receipts.
- Alert on non-terminal receipts older than one year before cleanup.
- Add support/audit read path by `trade_number`.
- Keep operator retry as future-compatible only; do not add a manual resend UI/API in this phase.

Required tests:

- Short outage delayed delivery.
- Medium outage skip.
- Long outage skip.
- No old-trade WebApp notification, Telegram message, or user-facing report after medium/long outage.
- Terminal cleanup only.
- Non-terminal old rows alert before cleanup.
- Support can answer why user X did or did not receive trade Y.

Stop conditions:

- Medium/long outage recovery sends old remote messages.
- Cleanup can delete non-terminal rows silently.

## Stage 11: Staging Validation Matrix And Load

Purpose: validate correctness, latency, and duplicate safety in staging before any production plan.

Required searches:

- `test_bot_webapp_integration_matrix`
- `trading_core_probe_worker`
- `load`
- `600`
- `1000`
- `Stage G`
- `acceptance matrix`

Implementation work:

- Build/update a staging validation matrix from the roadmap's Stage G list.
- Include both deterministic unit/integration tests and staging scenario tests.
- Include mixed WebApp/Telegram simulation where practical.
- Include high-contention offers with many simultaneous requests.
- Include 60 percent Telegram / 40 percent WebApp request mix for load-style validation.
- Capture sanitized app, bot, sync, DB, and worker logs.
- Capture receipt metrics and latency distributions.

Required tests/checks:

- All role/link/server/channel/outage/concurrency matrix cases listed in the roadmap.
- No duplicate trades from concurrent requests.
- No duplicate visible notifications for one trade/recipient/channel.
- Stable-connectivity target near 1-2 seconds is measured, with any threshold misses explained.
- Worker crash before send and crash after send before `sent`.
- Load-style test around 1000 simulated users and about 600 rps where staging capacity allows.
- Artifact report includes scenario counts, pass/fail, skipped cases with reasons, and log pointers.

Stop conditions:

- Any correctness failure in trade creation, receipt uniqueness, or required delivery.
- Any unexplained missing required WebApp notification or Telegram message under stable connectivity.
- Any duplicate visible notification outside the accepted ambiguous Telegram crash-after-send case.

## Stage 12: Cutover Readiness And Production Gate

Purpose: prepare for, but not perform, production rollout.

Required searches:

- `production_deploy_online`
- `sync-health`
- `cutover`
- `rollback`
- `observability`
- `release readiness`

Implementation work:

- Produce a cutover readiness report.
- Confirm staging matrix passes.
- Confirm no active offer requirement is satisfied for migration/cutover assumptions if needed.
- Confirm sync health is clean on both servers.
- Confirm migrations are additive and reversible.
- Confirm metrics/logs can show receipt backlog, oldest pending, terminal counts, Telegram failures,
  sync conflicts, and duplicate guards.
- Confirm rollback strategy for code-only rollback and migration-forward compatibility.
- Do not deploy production in this stage.

Required checks:

- Contract stages are complete and pushed.
- Staging owner validation is complete.
- Required logs and artifacts are available.
- Production deploy command is not run.

Stop conditions:

- Owner has not explicitly approved production deploy.
- Staging evidence is missing, stale, or incomplete.
- Any open P0/P1 correctness issue remains.

## Stage Order Summary

1. Stage 0: Freshness, Discovery, And Baseline Matrix
2. Stage 1: Bot Eligibility And Account Linking Foundation
3. Stage 2: Completed Trade Sync Guard
4. Stage 3: Read-Only Audience Builder
5. Stage 4: Schema Foundation
6. Stage 5: Receipt State Machine And Idempotent Services
7. Stage 6: Shadow Reconciliation
8. Stage 7: WebApp Delivery Repair
9. Stage 8: Telegram Classifier And Foreign Worker
10. Stage 9: Remove Direct Trade Completion Side Effects
11. Stage 10: Outage, Retention, And Support Audit
12. Stage 11: Staging Validation Matrix And Load
13. Stage 12: Cutover Readiness And Production Gate

## Commit And Report Template

Each stage commit should use a focused message:

```text
stage <n>: <short stage intent>
```

The stage report should include:

- branch name and commit hash
- files changed
- repository searches performed
- tests run
- tests skipped and why
- risks discovered
- remaining stages
- whether the next stage start condition is satisfied

## Non-Goals

- This contract does not approve production deployment.
- This contract does not make Telegram physically reliable.
- This contract does not guarantee browser push delivery to closed/restricted devices.
- This contract does not move WebApp hosting to foreign.
- This contract does not allow Iran to call Telegram.
- This contract does not implement manual/operator retry in phase 1.
