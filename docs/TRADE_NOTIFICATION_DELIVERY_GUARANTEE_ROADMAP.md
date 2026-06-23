# Low-Risk Plan: Trade Notification Delivery Guarantee

## Status

The previous full atomic delivery-ledger refactor is deprecated for now.

Reason: that approach changes the hot trade execution path, the commit boundary, Telegram side effects, WebApp notifications, sync behavior, and production rollback behavior at the same time. The blast radius is too high for the current phase.

This document replaces that roadmap with a lower-risk reconciliation-based plan. The core principle is:

> Do not make the trade transaction responsible for notification delivery. Use committed `trades` rows as the durable source of truth, then reconcile missing WebApp and Telegram delivery outside the trade path.

This plan is still a planning document. It is not approval to implement until the user explicitly confirms the selected approach.

## Goal

In stable Iran/foreign connectivity, every successfully committed trade must lead to the required trade-completion information being delivered through the correct channels:

1. WebApp notification on Iran for every required WebApp recipient.
2. Telegram private message on foreign for every required Telegram recipient.
3. Auditable state showing what was sent, what is pending, and what failed.

The guarantee is application-level and practical:

- If a required delivery is missed by the immediate code path, reconciliation must discover and repair it.
- If a temporary failure happens, retry must continue.
- If delivery is impossible, the reason must be explicit and auditable.

This does not guarantee that Telegram, the browser, or the user's device physically displays a message. It guarantees that the application does not silently lose the obligation to notify.

## Fixed Product Rules

These rules are fixed and should not be reopened without an explicit product decision:

1. Accountants have no market access and no Telegram bot access under any condition.
2. Accountants may receive WebApp trade notifications when they are required monitoring recipients.
3. Accountants must never receive Telegram delivery and must never execute trades.
4. Tier-2 customers have no Telegram bot access.
5. Tier-2 customers have limited WebApp market access: they may request/trade against other users' offers, but they may not publish offers.
6. Normal users and admins have full market access in the WebApp and Telegram bot.
7. Tier-1 customers have full WebApp market access now. Tier-1 Telegram bot access must be added at the beginning of this roadmap before Telegram delivery guarantees are validated for tier-1 customers.
8. For normal users, admins, and future bot-enabled tier-1 customers, Telegram account linking is performed through the WebApp.
9. Telegram linking is optional.
10. A bot-eligible user who has not linked Telegram must still receive WebApp trade notifications, but no Telegram message can be required for that user until Telegram is linked.
11. Product expectation is that almost all normal users and admins, and more than 80 percent of tier-1 customers after bot enablement, will link Telegram.
12. If a tier-1 or tier-2 customer is disconnected by their owner/principal, the customer user account must be soft-deleted through the existing customer unlink lifecycle.
13. A soft-deleted former customer may be added to the project again later as an accountant, customer, or normal user through the existing onboarding flows. Bot eligibility must be evaluated from the new live account/relation state, not from the old soft-deleted account.

## Telegram Account Linking Foundation

This section must be implemented before tier-1 customer bot access is enabled.

### Eligibility

The WebApp must show Telegram connection entry points only to bot-eligible users:

1. Normal users.
2. Admin users.
3. Tier-1 customers after the tier-1 bot-access stage is enabled.

The WebApp must not show Telegram connection entry points to:

1. Accountants.
2. Tier-2 customers.
3. Deleted, inactive, or market-blocked accounts.
4. Any role explicitly marked WebApp-only.

Telegram connection is optional, but recommended. Product copy should make it clear that users can continue with the WebApp if they skip Telegram.

### Authentication Method

The Telegram bot must require the Telegram `request_contact` flow for account linking.

Required validation:

1. The shared Telegram contact must belong to the Telegram sender: `contact.user_id == message.from_user.id`.
2. The normalized shared phone number must match the WebApp account `mobile_number`.
3. If the WebApp account is already linked to a different `telegram_id`, linking must be rejected unless a later explicit relink/recovery flow is implemented.
4. Accountants and tier-2 customers must remain blocked even if their phone number matches.
5. Inactive, deleted, or blocked accounts must remain blocked.

Phone sharing with the sender-owned contact check is strong enough to prove that the Telegram account controls the same Telegram phone number as the WebApp account.

Add a one-time WebApp-issued Telegram link token for WebApp entry points. The token is not a replacement for phone verification. It improves flow integrity and prevents generic phone-number probing through the bot.

Use a synced table, for example `telegram_link_tokens`, instead of a stateless signed token. A table is preferred because the token must be single-use, revocable, auditable, and visible to the foreign Telegram bot after sync.

Recommended token fields:

- `id`
- `token_hash`
- `user_id`
- `issued_by_server`
- `status`, one of `pending`, `used`, `expired`, `revoked`
- `expires_at`
- `used_at`
- `used_telegram_id`
- `created_at`
- `updated_at`

Token rules:

- Store only a hash of the token.
- Token TTL should be short, recommended 10 minutes.
- Token must be single-use.
- Creating a new token may revoke previous pending tokens for the same user.
- Consuming the token and writing `User.telegram_id` should happen in one transaction on the foreign server.
- Token status changes must sync back for audit.
- If token sync has not reached foreign yet, the bot should show a short "try again in a few moments" message rather than falling back to unsafe linking.

Recommended token flow:

1. WebApp creates a short-lived, single-use token for the authenticated user.
2. WebApp opens `https://t.me/{bot_username}?start=link_{token}`.
3. The token must fit Telegram's start-parameter restrictions.
4. The token record syncs to foreign before or during the bot link attempt.
5. The bot validates token existence, expiry, not-used state, and user ownership.
6. The bot then requires contact sharing and validates phone plus `contact.user_id`.
7. Token is consumed only after successful link.
8. Expired or invalid token tells the user to reopen the WebApp connection button.

Direct `/link` without a WebApp-issued token must not remain as an account-linking path.

Policy:

- WebApp-to-bot account linking must always start from the WebApp-issued token link.
- Direct `/link` should be removed or disabled as a user-facing linking command. If a stale deployment or Telegram client still sends `/link`, it must not start contact collection and must return only the same neutral low-information response as invalid `/start`.
- `/start` without a valid `link_{token}` must not link an account based on phone number alone.
- `/start` without a valid token, and any stale `/link` fallback, should return a neutral low-information message such as: "پس از تکمیل ثبت‌نام، بات را از مسیر اعلام‌شده شروع کنید."
- Sender-owned contact sharing remains mandatory after a valid token is accepted.

### Existing Users

Existing bot-eligible users who are not linked must see Telegram connection in the WebApp:

1. Account/Profile settings: add a clearly styled Telegram connection action.
2. Dashboard: for eligible unlinked users, show a Telegram connection button below the "today's trades" section.
3. If the user is already linked, show connected state and do not show onboarding prompts.
4. If the user skips or closes the flow, keep the dashboard/profile connection action available.

### New Users

New bot-eligible users must see Telegram connection after registration completion and before full WebApp entry:

1. Show a dedicated Telegram connection step after registration is complete.
2. Provide two actions: connect Telegram and skip.
3. If connect is selected, open the bot deep link and require contact verification in the bot.
4. If skip is selected, allow full WebApp entry and continue showing the dashboard connection action.
5. Skipping Telegram must not limit WebApp market access for otherwise eligible users.

### Current Code Reuse And Required Changes

Existing reusable pieces:

- `prompt_contact_for_account_link` already prompts users to share contact.
- `handle_contact` already checks `contact.user_id == message.from_user.id`.
- `finalize_account_link` already writes `telegram_id` and joins the mandatory channel flow.

Required changes:

1. Replace broad "all customers are WebApp-only" bot-link blocking with a tier-aware rule.
2. Tier-1 customers become bot-eligible in the tier-1 stage.
3. Tier-2 customers remain WebApp-only.
4. Accountants remain WebApp-only.
5. Add WebApp UI entry points for existing and new users.
6. Add the synced `telegram_link_tokens` table and one-time WebApp-issued token flow.
7. Tests must cover normal/admin/tier1 allowed, tier2/accountant denied, wrong phone denied, shared contact from another Telegram account denied, already-linked-to-another-Telegram denied, skip flow, and invalid/expired token.

## Server Rules

1. WebApp exists only on Iran.
2. Telegram Bot API execution exists only on foreign.
3. Iran must never call Telegram directly.
4. Foreign must not become the WebApp host.
5. `offer.home_server` determines authoritative trade execution.
6. `user.home_server` must not decide delivery channel routing.

Channel routing:

| Channel | Destination server | Reason |
| --- | --- | --- |
| WebApp notification | `iran` | WebApp exists only on Iran. |
| Telegram message | `foreign` | Telegram can be called only from foreign. |

## Required Recipients

The reconciler must use one canonical audience builder. The builder must be read-only and deterministic.

Required recipient groups:

1. Offer owner.
2. Trade responder/requester.
3. Customer-chain participants when one visible request expands into multiple trade legs.
4. Owner/principal users who must see trades performed by their customers.
5. Active accountants who must monitor the relevant owner/customer relationship. Accountants are WebApp-only recipients.
6. Any future role explicitly defined as a trade-completion recipient.

Recipient channel rules:

1. Direct normal users/admins: WebApp required; Telegram required only if linked.
2. Tier-1 customers before the new bot-access stage is enabled: WebApp required; Telegram not required.
3. Tier-1 customers after the new bot-access stage is enabled: WebApp required; Telegram required only if linked.
4. Tier-2 customers: WebApp required for allowed trade activity; Telegram never required.
5. Accountants: WebApp required when they are monitoring recipients; Telegram never required.
6. Customer owner/principal: WebApp required; Telegram required only if linked and bot-eligible.

Customer-chain handling:

- The existing trade execution path already resolves customer/principal relationships and expands a customer trade into multiple persisted `trades` rows when needed.
- Customer trades are not a new delivery product flow; they follow the already implemented rule that every customer trade passes through the customer's owner/principal.
- This roadmap must not rewrite that execution logic.
- The delivery audience builder must read the persisted trade legs and existing customer/accountant relation helpers so the same business meaning is used for delivery on both servers.
- Reconciliation must treat the current committed trade records and current message-building behavior as authoritative, instead of inventing a new aggregated customer-chain notification model.
- The only implementation risk is accidentally building a recipient's notification from the wrong committed trade record or from a duplicated interpretation of the customer path.

Existing code that must be reused or extracted safely:

- `build_trade_notification_audience_user_ids` already returns owner user ids plus their active accountants for WebApp trade notifications.
- The current trade route already calls that helper for each normal trade side and for each customer-chain leg.
- The current customer-chain route already builds participant identity, customer relation maps, trade path summaries, and message bundles.
- Implementation must prefer extracting shared read-only helpers from this existing logic over duplicating recipient or message-building rules.

## Why Reconciliation Is Lower Risk

The current high-risk point is the trade commit path. This plan does not require changing that path first.

Instead:

1. A committed `trades` row is treated as the durable fact.
2. Reconciliation repeatedly scans committed trades.
3. Missing delivery records are created idempotently.
4. Channel-specific workers process those records on the correct server.
5. Existing immediate side effects may remain for speed, but they are not the guarantee.

If immediate side effects fail, the reconciler repairs them. If the reconciler crashes, it resumes from database state. If sync is delayed, the destination server repairs after synced trade rows arrive.

## Race And Duplicate Control Decision

The current trade path already performs immediate side effects. A reconciler that sends independently can race with those side effects and create duplicate WebApp notifications or duplicate Telegram messages.

Engineering decision:

1. Immediate trade side effects and reconcilers must both use the same delivery gate.
2. The delivery gate is `trade_delivery_receipts` plus an atomic claim/lease protocol.
3. No code path may directly create a trade-completion WebApp notification or send a trade-completion Telegram message after the receipt-backed delivery gate is enabled.
4. Before enabling reconciler sending, existing immediate trade notification calls must either be routed through the receipt-backed helper or disabled for that channel.

Required idempotency key:

- `event_type + trade_id + recipient_user_id + channel`

Claim protocol:

1. Upsert the receipt if it does not exist.
2. Atomically claim only receipts in sendable states such as `pending` or `retry_pending`.
3. Move the claimed receipt to `processing` with a short `lease_until` and `worker_id`.
4. Only the worker/request task that owns the active lease may perform the side effect.
5. Mark `sent`, `skipped`, `not_required`, `retry_pending`, or `permanent_failed` after classification.
6. Other concurrent workers must skip `processing`, `sent`, `skipped`, and `not_required` receipts.

WebApp-specific rule:

- Add a nullable `dedupe_key` column to `notifications`.
- Add a partial unique index on `notifications.dedupe_key` where `dedupe_key IS NOT NULL`.
- Trade-completion WebApp notifications must use `dedupe_key = trade_completed:webapp:{trade_id}:{recipient_user_id}`.
- Existing non-trade or legacy notifications may keep `dedupe_key = NULL` and must not be affected by the new uniqueness rule.
- Add nullable `extra_payload JSONB` to `notifications` so persisted notification history carries the same route/trade metadata as real-time notification payloads.
- Trade-completion notification `extra_payload` must include at least `trade_id`, `trade_number`, `offer_id`, `route`, `counterparty_profile_user_id`, `counterparty_profile_account_name`, `recipient_role`, and `delivery_receipt_id` when those values are known.
- Notification read APIs must return `extra_payload`, or equivalent flattened fields, so notification history after refresh behaves like real-time notifications.
- Frontend notification history and real-time notification handling must normalize the same schema.
- WebApp notification creation and receipt `sent` update must happen in one database transaction.
- The existing generic notification helper commits internally and must not be used directly for receipt-backed idempotent trade delivery unless it is safely adapted.
- Duplicate-key conflicts for the same trade WebApp notification must be treated as successful idempotent delivery after the existing notification row is loaded and linked to the receipt.

Telegram-specific rule:

- Telegram has no durable client idempotency key for `sendMessage`.
- To avoid normal race duplicates, only a claimed receipt may send Telegram.
- Once Telegram repair is enabled, direct Telegram send calls from the trade route are not allowed.
- The trade route may create or upsert Telegram receipts and wake only a local worker, but it must not call Telegram itself.
- Only the foreign Telegram delivery worker may claim Telegram receipts and call Telegram `sendMessage`.
- The only accepted duplicate case is worker crash after Telegram accepts the message but before the receipt is marked `sent`; that case uses the already accepted duplicate-safe resend wording.

## Minimal New State

Use one small table, name subject to implementation review:

`trade_delivery_receipts`

Required fields:

- `id`
- `event_type`, initially `trade_completed`
- `trade_id`
- `trade_number`
- `offer_id`
- `recipient_user_id`
- `recipient_role`
- `channel`, one of `webapp`, `telegram`
- `destination_server`, one of `iran`, `foreign`
- `status`, one of `pending`, `processing`, `sent`, `retry_pending`, `not_required`, `skipped`, `permanent_failed`
- `reason`, such as `linked_telegram`, `webapp_only_role`, `not_linked`, `accountant_webapp_only`, `tier2_webapp_only`
- `notification_id`, nullable, for WebApp notification rows
- `telegram_message_id`, nullable, if Telegram returns it
- `attempt_count`
- `next_retry_at`
- `last_error`
- `last_error_class`
- `created_at`
- `updated_at`
- `sent_at`

Required uniqueness:

- `event_type + trade_id + recipient_user_id + channel`

This table is not required inside the trade transaction in the low-risk plan. It is generated from committed trades and can be regenerated if missing.

Status rule:

- `permanent_failed` must not be used for user Telegram account problems, because the user may fix Telegram, reconnect the bot, or change the linked account later.
- User Telegram account/chat delivery problems for the current trade must be marked `skipped`, not `retry_pending`.
- A skipped Telegram delivery must not stay in the sending queue and must not be resent automatically after the user fixes Telegram.
- If the user later fixes Telegram or links a new Telegram account, only future trades create new Telegram delivery attempts.
- `permanent_failed` is reserved only for non-user fatal system/data errors that cannot be repaired by user action.

Retention:

- `trade_delivery_receipts` must retain at least the last one year of data.
- Rows older than one year may be archived or deleted by an explicit retention job.
- Cleanup should target terminal rows first: `sent`, `skipped`, `not_required`, `permanent_failed`.
- Non-terminal rows older than one year must trigger an operational alert before cleanup, because they indicate a stuck delivery flow.
- Phase 1 should not introduce table partitioning.
- Phase 1 must add the indexes needed for queue scanning, audit lookup, and recipient history.
- The schema and queries should be written so future monthly partitioning by `created_at` can be added without changing delivery logic.
- If volume becomes high later, use database partitioning or archival/compressed old partitions. Deleting rows older than the approved one-year retention window is allowed only through the retention job.

Required phase-1 indexes:

- unique: `event_type + trade_id + recipient_user_id + channel`
- queue scan: `destination_server + channel + status + next_retry_at`
- audit lookup: `trade_number`
- recipient history: `recipient_user_id + created_at`
- partial queue index for active sendable states: `status IN ('pending', 'retry_pending', 'processing')`

Operational requirement:

- monitor row count, active queue count, oldest pending/retry row, and table/index size
- monitor rows approaching and exceeding the one-year retention boundary

## Stable-Connectivity Flow

### 1. Trade Commit

The current trade execution code remains responsible for:

- creating `trades`
- updating offer quantity/status
- writing existing request ledger data

No hard dependency on notification delivery is added to the trade transaction.

Post-commit receipt creation:

1. After the trade transaction commits successfully, the route should best-effort create/upsert the expected delivery receipts.
2. The route may wake the correct local worker after receipt creation.
3. Receipt creation failure after commit must not roll back or invalidate the trade.
4. If post-commit receipt creation fails, the reconciler must later discover the committed trade and create missing receipts.
5. This keeps stable-path latency low without making notification delivery part of the trade transaction.
6. Existing direct immediate side effects are replaced stage-by-stage by receipt-backed helpers before reconciler sending is enabled for that channel.
7. If a trade request is replayed idempotently and the committed trade already exists, the route or reconciler must still repair missing receipts/notifications instead of hiding the delivery gap behind the replay response.

### 2. Iran WebApp Reconciler

Iran runs a WebApp delivery reconciler.

It scans committed trades visible on Iran and:

1. Builds the required audience.
2. Creates missing `trade_delivery_receipts` rows for `channel=webapp`.
3. Creates missing persistent WebApp notification rows.
4. Marks WebApp delivery `sent` only after the persistent notification row exists.
5. Repeats with an overlap window so missed rows are found later.

### 3. Foreign Telegram Reconciler

Foreign runs a Telegram delivery reconciler.

It scans committed trades visible on foreign and:

1. Builds the required audience.
2. Creates missing `trade_delivery_receipts` rows for `channel=telegram`.
3. Marks recipients without required Telegram as `not_required`.
4. Sends Telegram messages only for required linked Telegram recipients.
5. Retries only temporary infrastructure failures.
6. Marks user Telegram account/chat delivery failures as `skipped` for that trade.
7. Marks explicit non-user fatal failures with reason.

### 4. Cross-Server Recovery

When `offer.home_server=iran`, the trade is committed on Iran. After sync reaches foreign, the foreign Telegram reconciler sees the trade and sends missing Telegram messages.

When `offer.home_server=foreign`, the trade is committed on foreign. After sync reaches Iran, the Iran WebApp reconciler sees the trade and creates missing WebApp notifications.

This avoids Iran-to-Telegram calls and avoids foreign WebApp hosting.

## Worker Wake And Polling Decision

Wake is an optimization, not a correctness dependency.

Rules:

1. A server may only wake workers running on the same server.
2. Iran must not call foreign just to wake Telegram delivery.
3. Foreign must not call Iran just to wake WebApp delivery.
4. The opposite server discovers work only after sync makes the trade/receipt visible there.
5. Reconcilers must poll frequently enough that stable-connectivity latency remains near the accepted 1 to 2 second target.
6. Recommended staging interval is 0.5 to 1 second with overlap windows.
7. If wake fails, polling must still eventually process the work.

## Trade Source And Sync Safety Decision

Engineering recommendation:

1. Treat committed `trades` rows as append-only delivery facts.
2. A committed completed trade must not be deleted, overwritten, reopened, or hidden by cross-server sync conflict handling.
3. If sync receives a conflicting update/delete for a committed completed trade, keep the local completed trade, write a conflict log, and require manual/operator review instead of applying the destructive change.
4. Use `trade_number` as the human/support reconciliation key because it is unique and server sequences are split by parity.
5. Delivery repair may use `trade_id` for local foreign keys, but reports and cross-server audits must also include `trade_number`.
6. Before enabling delivery guarantee in production, add a sync-health check for recent completed trades: count and checksum by `trade_number`, participant ids, quantity, price, status, and created time window.

Implementation decision:

1. Add a dedicated trade sync guard, for example `_trade_sync_guard_reason`, in the sync receiver before `trades` upsert/delete is executed.
2. The guard must resolve local trades by `trade_number`, not only by numeric id.
3. If a local trade with the same `trade_number` is already `COMPLETED`, synced `DELETE` must be ignored and logged as a conflict.
4. If a local completed trade receives an incoming update that would make it non-completed, reopen it, hide it, or change its business meaning, the incoming update must be ignored and logged as a conflict.
5. Protected completed-trade fields include `offer_id`, `offer_user_id`, `responder_user_id`, `actor_user_id`, `commodity_id`, `trade_type`, `quantity`, `price`, `status`, and `trade_number`.
6. If incoming data is completed and the local trade is missing, insert is allowed.
7. If local trade is not completed and incoming data is completed, transition to completed is allowed if normal constraints pass.
8. If both local and incoming trades are completed and business fields match, the sync item is idempotent and may be treated as success.
9. The unique-violation natural-key fallback path for `trades` must call the same guard before updating by `trade_number`.
10. Every ignored destructive trade sync event must increment/report a sync conflict metric with `table=trades` and a specific reason.

Required guard tests:

- synced `DELETE` for a local completed trade is ignored
- synced update from completed to non-completed is ignored
- synced update that changes completed trade `price` or `quantity` is ignored
- synced update that changes completed trade participants is ignored
- duplicate completed sync with identical business fields is idempotent
- missing incoming completed trade is inserted
- local non-completed trade can become completed from a valid incoming completed sync item
- natural-key fallback by `trade_number` cannot bypass the guard

This is safer and simpler than making notification delivery part of the hot trade transaction. The trade is first protected as a durable fact; then WebApp and Telegram delivery can be repaired from that fact.

## Receipt Sync And Ownership Decision

`trade_delivery_receipts` should sync between Iran and foreign because it is non-messenger operational data and gives both servers a consistent audit view.

Execution ownership:

1. A worker may only claim receipts where `destination_server == current_server`.
2. WebApp receipts are operationally owned by Iran.
3. Telegram receipts are operationally owned by foreign.
4. Receipts with the opposite `destination_server` are read-only/audit data on the local server.
5. A server must not execute or mutate the send lifecycle for the other server's receipts.

Sync conflict policy:

1. Terminal states are monotonic: `sent`, `skipped`, `not_required`, and `permanent_failed` must not be overwritten by older non-terminal sync data.
2. `processing` leases are local execution state and must not cause the opposite server to execute the receipt.
3. If a synced receipt conflicts on the unique key, merge by the newest valid lifecycle update while preserving terminal-state precedence.
4. `destination_server` must be treated as immutable after receipt creation.
5. Retention cleanup should be coordinated through the normal sync path so rows older than the approved one-year retention window disappear consistently from both servers.

## Latency Target

The guarantee is eventual, but stable-connectivity latency should be low.

Recommended target:

- Reconciler interval: 0.5 to 1 second in staging evaluation.
- Stable-connectivity delivery target: about 1 to 2 seconds after the trade is visible on the destination server.
- Existing immediate side effects may keep the common path fast.
- The reconciler is the safety net and the auditable guarantee.

Accepted stable-connectivity SLA:

- Target: 1 to 2 seconds.
- Hard staging acceptance threshold should be defined during performance testing, but the design should optimize for the observed 1 to 2 second target.

## Outage Delivery Policy

Cross-server outage is classified as:

1. Short outage: up to 2 minutes.
2. Medium outage: more than 2 minutes and up to 1 hour.
3. Long outage: more than 1 hour.

Policy:

1. During short outage recovery, delayed cross-server trade messages should still be delivered after sync resumes.
2. During medium or long outage recovery, the opposite-server Telegram/WebApp trade messages for old trades should not be sent automatically.
3. During medium or long outage, each server sends only the messages it can deliver locally for the trades visible and authoritative on that server.
4. Delivery receipts for skipped remote delivery after medium/long outage should be marked with an explicit terminal reason such as `expired_delivery_after_outage`.
5. The exact outage classification must be based on the time between trade commit and visibility on the destination server.
6. No user-facing old-trade report, WebApp notification, or Telegram message should be sent after medium/long outage recovery for these skipped remote deliveries.
7. Passive trade history remains the source for old trades, similar to the bot behavior.
8. Operational receipt/audit state may keep the skip reason for support investigation, but it must not create a user-facing report or notification.

## Failure Policy

### Telegram Send Error Classification

Telegram private-message delivery must be classified conservatively:

| Class | Examples | Receipt status |
| --- | --- | --- |
| Success | Telegram returns `ok=true` and a message id | `sent` |
| Not required by product | unlinked user, accountant, tier-2 customer, WebApp-only role | `not_required` |
| Temporary infrastructure failure | network timeout, DNS/TLS failure, Telegram 5xx, `429 Too Many Requests`, `retry_after`, worker/db failure | `retry_pending` |
| User Telegram account unreachable | chat/account not found, bot cannot send private message to the user, blocked/deleted/banned/unreachable Telegram account | `skipped` |
| Non-user fatal system/data error | malformed message payload, invalid parse mode/entities, invalid bot token/config, corrupted stored destination | `permanent_failed` plus alert |

Classifier rules:

- `retry_after` must be honored when Telegram provides it.
- `skipped` must only be used when the error clearly belongs to the user's Telegram account/chat reachability.
- Unknown Telegram errors must not be classified as `skipped` by default.
- Unknown errors should be retried as temporary if they look transient, otherwise reported as non-user system review/fatal state.
- The classifier must be covered by tests using captured/sanitized Telegram error payload fixtures.

### Temporary failures

Examples:

- Telegram timeout
- Telegram 5xx
- Telegram rate limit
- worker restart
- database transaction failure in delivery worker
- temporary sync delay

Policy:

- keep or move receipt to `retry_pending`
- increment `attempt_count`
- set `next_retry_at`
- keep `last_error`
- retry until success, the user becomes no longer Telegram-required, or an explicit operator/product decision changes the state
- retries must use bounded backoff and jitter
- every retry must reload the latest user Telegram identity from the database

### Telegram User Account Delivery Failures

Examples:

- Telegram says the linked chat/account is not found
- Telegram says the bot cannot send a private message to this user
- Telegram rejects delivery because the current Telegram account is deleted, banned, blocked, or otherwise unreachable
- linked Telegram account exists in the database but is not usable at send time

Policy:

- mark that trade's Telegram receipt as `skipped`
- set an explicit reason such as `telegram_user_unreachable`
- record `last_error` and `last_error_class` for support/audit
- do not keep the receipt in the sending queue
- do not retry that old trade automatically after Telegram is fixed
- do not send accumulated backlog after Telegram is fixed
- if the user fixes Telegram or links a new account later, only trades created after that point should attempt Telegram delivery
- the WebApp notification for that trade remains required and should be delivered normally
- these failures must never crash trade execution, the bot, or the reconciler

### Non-Retryable Product States And Fatal System Errors

Examples:

- user is not Telegram-linked
- user role is WebApp-only
- accountant WebApp-only rule
- tier-2 customer WebApp-only rule
- internal data/configuration error that cannot be solved by retrying the user Telegram account

Policy:

- `not_required` for valid product cases like not linked, accountant, or tier-2 customer
- `permanent_failed` only for non-user fatal data/configuration errors
- all permanent states must be reportable by `trade_number`
- Telegram account/chat delivery errors for a user are not permanent failures; they are `skipped`
- WebApp notification delivery remains required even when Telegram delivery is skipped

### Telegram exactly-once limitation

Telegram `sendMessage` has no durable client idempotency key.

If the worker sends a message and crashes after Telegram accepts it but before the database records `sent`, the application cannot ask Telegram whether that exact private message was delivered.

Policy:

- prefer duplicate-safe resend over silent loss
- use the same trade number
- make recovered duplicate wording understandable
- keep the "today's trades" Telegram surface as user-visible verification

## Current Code Discovery And Reuse Notes

This section records the current code and test surfaces that must be reviewed before implementing each roadmap stage. The purpose is to prevent duplicate rewrites, preserve the current customer/accountant trade behavior, and expose risks found during code discovery.

### Telegram account linking

Current reusable code:

- `bot/handlers/link_account.py` already has `prompt_contact_for_account_link`, `handle_contact`, `handle_address_completion`, and `finalize_account_link`.
- `handle_contact` already validates sender-owned contact with `contact.user_id == message.from_user.id`.
- `handle_contact` already normalizes Iranian phone numbers, loads `User` by `mobile_number`, rejects unknown users as sync-pending, rejects inactive/deleted/market-blocked accounts, rejects accounts already linked to a different `telegram_id`, and supports address completion before final link.
- `finalize_account_link` already writes `User.telegram_id`, `username`, optional `full_name`, optional `address`, calls `set_legacy_has_bot_access_compatibility`, ensures mandatory channel membership, commits, and sends the success message.
- `bot/keyboards.py` already has a request-contact keyboard pattern through `KeyboardButton(..., request_contact=True)`.
- `models/user.py` already has nullable unique `telegram_id`, so WebApp-only users are supported.

Current risks and required changes:

- `get_web_only_bot_access_reason` currently blocks every customer through `is_user_customer`. This must become tier-aware: tier-1 customers allowed after Stage A0, tier-2 customers still denied.
- `core/services/customer_relation_service.py` currently has `get_active_customer_relation_for_customer` and `is_user_customer`, but no dedicated `is_tier1_customer` / `is_tier2_customer` helper. Add a small helper or use the active relation directly instead of changing customer trade rules.
- The existing customer unlink flow soft-deletes active customer user accounts and marks the relation deleted. Bot eligibility must therefore use only the current non-deleted user plus current active relation state; old soft-deleted customer accounts are not eligible.
- `bot/handlers/start.py` currently lets `/start` without a token call `prompt_contact_for_account_link` for unknown Telegram users. The new WebApp-issued token policy requires changing this to a neutral low-information response.
- Direct `/link` currently starts contact linking for unknown users. It must stop being a linking path without a valid WebApp-issued token.
- Existing invitation/customer/accountant token tables should not be reused for Telegram linking because invitation tokens represent registration/relation creation, while Telegram link tokens are short-lived, single-use account binding records.
- The token consume path must update token status and `User.telegram_id` in one foreign-side transaction after phone/contact validation.

Existing tests to reuse/update:

- `tests/test_bot_link_account_guards.py` covers wrong sender contact, missing user, already linked account, accountant denial, customer denial, and address-completion guards.
- `tests/test_bot_link_account_success.py` covers successful phone normalization/linking, mandatory channel membership, commit rollback handling, missing-row sync-pending messaging, inactive/deleted denial, and placeholder-address completion.
- `tests/test_bot_start_without_token.py` currently expects unknown `/start` to enter contact linking. This test must be changed to expect the neutral no-token response.
- Add tests for valid token, invalid token, expired token, used token, token not yet synced to foreign, tier-1 allowed, tier-2 denied, accountant denied, already linked to another Telegram id, and contact phone mismatch.

### WebApp entry points for linking

Current reusable code:

- `frontend/src/views/DashboardView.vue` already loads `/api/auth/me` data into `user`, calculates `isAccountant`, `isCustomer`, `customer_tier`, inactive state, restricted state, and today's trade summary. This is the right existing dashboard surface for the eligible-unlinked Telegram connect action below today's trades.
- `frontend/src/views/SettingsView.vue` already uses `currentUserSummary` and is the right account/settings surface for the Telegram connection action.
- `frontend/src/views/WebRegister.vue` already owns the registration-complete transition: after `register-complete` it stores `access_token` and `refresh_token`, then routes to `/`. This is the right place to insert the post-registration connect/skip step for eligible users.
- `frontend/src/utils/currentUser.ts` already normalizes `is_accountant`, `is_customer`, `customer_tier`, owner ids, and role into a cached summary.
- `api/routers/auth.py` `/api/auth/me` already serializes accountant/customer relation context through `_serialize_current_user_response` and `_load_current_user_relation_context`.
- `schemas.py` `UserRead` already exposes `telegram_id`, `is_accountant`, `is_customer`, and `customer_tier`.

Current risks and required changes:

- UI gating must not rely only on cached local state. Token creation must be enforced by a backend endpoint that checks the current authenticated user and the same eligibility rules as the bot.
- `SettingsView.vue` currently only separates accountants for session management. It will need `telegram_id` / bot-link eligibility state either from `/api/auth/me` or a small dedicated status endpoint.
- `WebRegister.vue` currently immediately enters the WebApp after registration. The new connect/skip step must not break token storage, refresh token storage, or normal WebApp entry.
- Dashboard and settings should show connected state when `telegram_id` exists and should keep the connect action visible after skip when the user remains eligible and unlinked.
- The deep link should use `/api/config` or an equivalent config source for `bot_username`; avoid hardcoding the bot username in the frontend.

Existing tests to reuse/update:

- `frontend/src/views/DashboardView.test.ts` already covers role/customer-tier dashboard behavior and can be extended for eligible/unlinked/linked connect CTA states.
- `frontend/src/views/SettingsView.test.ts` already covers accountant-specific settings behavior and can be extended for hidden Telegram connection for accountants/tier-2.
- `frontend/src/views/WebRegister.vue` currently has no dedicated post-registration connect step; add frontend tests for connect, skip, and normal token storage.
- `frontend/src/utils/currentUser.ts` tests should cover `telegram_id` if the UI starts using it from cached summary.

### Trade execution, customer-chain, and recipients

Current reusable code:

- `api/routers/trades.py` already contains `TradeExecutionPlan` and `uses_customer_trade_chain`; customer trades are expanded into committed trade legs before notification side effects run.
- The route already resolves responder/source customer relations, owner/principal users, tier-2 commission, participant identity maps, customer relation maps, and trade path summaries.
- `_load_trade_customer_relation_map_for_user_ids`, `_build_trade_path_payload`, `_build_trade_notification_extra_payload`, `_build_trade_notification_message`, and `_build_trade_message_bundle` already encode the current customer-chain display behavior.
- `core/services/accountant_relation_service.py` `build_trade_notification_audience_user_ids` already returns owner ids plus active accountant ids and is used by the trade route for both normal and customer-chain legs.
- `_build_trade_notification_message` already suppresses counterparty display for tier-2 recipients and includes offer notes when present.
- `_build_trade_message_bundle` already builds the Telegram private-message format that the user preferred, including price, quantity, commodity, counterparty, trade number, time, path, and notes.

Current risks and required changes:

- Do not rewrite customer-chain settlement logic. The audience builder should read committed trade rows and call/extract the existing read-only helpers.
- The current notification helper is nested inside the route (`_create_trade_notifications_for_leg`). Move only the reusable read-only parts into a shared module first; avoid moving commit-sensitive trade execution code.
- Customer trades can create multiple trade rows from one visible action. Delivery receipts must be created per committed trade leg and recipient/channel, not from an invented aggregate unless explicitly designed later.
- Accountants are included only for WebApp notifications through `build_trade_notification_audience_user_ids`; Telegram receipt generation must filter accountants as `not_required`.
- Tier-1 customer bot access must preserve the existing owner/principal trade path. Tier-1 bot trades must not turn into direct settlement with the external counterparty when the existing customer path would route through the owner.
- If a customer relation is disconnected, the existing service soft-deletes the customer user account. Future participation as accountant/customer/normal user must happen through a new live account/onboarding flow, so delivery/audience logic must not treat closed customer relations as active recipients.

Existing tests to reuse/update:

- `tests/test_bot_trade_execute_local_success.py` proves bot trade callbacks delegate to the shared authoritative trade command and preserve Telegram metadata/idempotency.
- `tests/test_bot_trade_execute_remote_home.py`, `tests/test_bot_trade_execute_local_pending.py`, and related bot trade tests cover local/remote offer-home paths and should remain green.
- `frontend/src/components/TradingView.vue` and `frontend/src/views/MarketView.test.ts` already exercise market/trade display and customer-tier behavior; extend only where delivery UI state is visible.
- Add backend tests for audience builder results across direct users, admins, tier-1, tier-2, accountants, owner/principal users, and customer-chain legs.

### WebApp notification delivery

Current reusable code:

- `models/notification.py` defines persistent WebApp notifications with `user_id`, `message`, `is_read`, `created_at`, `level`, and `category`.
- `core/utils.py` `create_user_notification` creates a row, commits, refreshes, increments Redis unread count, publishes the user event, and returns the row.
- `api/routers/trades.py` currently calls `create_user_notification` directly when side effects are not deferred, or via `_create_user_notification_background` when deferred.
- `core/services/cross_server_recovery_service.py` already creates owner WebApp notifications directly with `Notification(...)` for outage recovery.

Current risks and required changes:

- `create_user_notification` commits internally. Receipt-backed WebApp delivery needs a no-auto-commit variant or a new idempotent helper so `Notification` creation and receipt `sent` update happen in one transaction.
- Add nullable `notifications.dedupe_key` and partial unique index without changing existing non-trade notifications.
- Add nullable `notifications.extra_payload JSONB` because the current helper merges `extra_payload` only into the real-time payload and does not persist it to notification history.
- Notification history APIs currently expose only the base notification fields, so they must return persisted trade metadata before WebApp delivery can be considered user/support complete.
- Frontend notification history and real-time notifications must be normalized from one shared shape so refresh does not remove route/trade/counterparty behavior.
- The idempotent helper should still publish the same real-time event and unread count after the durable row exists.
- Duplicate notification conflict on the same dedupe key must be treated as success after loading the existing row and linking/marking the receipt.

Existing tests to reuse/update:

- `tests/test_core_notifications_runtime.py`, `tests/test_notifications_router_reads.py`, `tests/test_notifications_router_mutations.py`, `tests/test_notifications_router_stream.py`, and frontend notification runtime tests cover current notification behavior.
- Add focused tests for `dedupe_key` uniqueness, persisted `extra_payload`, history/read API metadata, idempotent duplicate success, receipt/notification same-transaction behavior, and unchanged legacy null-dedupe notifications.

### Telegram private-message delivery

Current reusable code:

- `core/telegram_gateway.py` centralizes Telegram Bot API calls and enforces foreign-only execution through `assert_telegram_execution_surface`.
- `telegram_gateway.send_message` and `send_message_sync` already return `TelegramGatewayResult` with `ok`, `status_code`, `response_text`, `response_json`, `idempotency_key`, `error`, and `message_id`.
- `api/routers/trades.py` currently uses `_queue_trade_telegram_message` and `send_telegram_message_sync` for trade private messages. `_queue_trade_telegram_message` already skips when `current_server() != "foreign"` or `chat_id` is missing.
- `core/notifications.py` has a generic Iran-relay notification path, but this roadmap should not use it for guaranteed trade-completion delivery because receipts must own routing, status, retry, and audit.

Current risks and required changes:

- After Telegram repair is enabled, direct trade-route Telegram sends must be disabled or routed through the receipt-backed delivery gate to avoid duplicates.
- The error classifier should be built around `TelegramGatewayResult` and sanitized Telegram response bodies. Unknown errors must not default to `skipped`.
- User Telegram account failures must become terminal `skipped` for that trade, not `retry_pending` and not `permanent_failed`.
- Temporary Telegram/network errors must stay retryable with bounded backoff and latest `User.telegram_id` reload on each retry.
- Duplicate-safe resend wording is needed only for the ambiguous crash-after-Telegram-accepts-before-receipt-sent case.

Existing tests to reuse/update:

- `tests/test_telegram_gateway_policy.py` proves Telegram execution is blocked on Iran before HTTP calls and centralized through the gateway.
- `tests/test_core_notifications_runtime.py` covers the older generic notification relay behavior; keep it separate from trade-delivery receipts.
- Add classifier tests using sanitized fixtures for success, 429/retry_after, timeout/network, 5xx, user unreachable, malformed payload, invalid parse mode, invalid bot token/config, and unknown Telegram errors.

### Cross-server sync and completed-trade safety

Current reusable code:

- `api/routers/sync.py` already has `NATURAL_KEYS["trades"] = "trade_number"`.
- `_build_upsert_stmt` currently uses `ON CONFLICT (trade_number)` for trades and updates all incoming columns except `id` and `trade_number`.
- `_apply_item` already has explicit stale guards and logging patterns for `offers` and `offer_requests`.
- Delete handling in `_apply_item` resolves local ids by public identity when available, then deletes by `id`.
- `core/sync_registry.py` already marks `trades` as `SyncPolicy.SYNC`.

Current risks and required changes:

- A completed trade can currently be overwritten through the trade `trade_number` upsert path if a destructive synced update arrives. Add `_trade_sync_guard_reason` before building/executing the trade upsert.
- A completed trade can currently be deleted by a synced `DELETE` unless the trade-specific guard blocks it. Add the guard in the delete branch too.
- The natural-key merge fallback for unique violations must call the same trade guard before updating by `trade_number`.
- The guard should follow the existing offer/offer_request guard style: return a reason, log structured metadata, treat ignored destructive events as successful sync application, and increment/report conflict metrics.
- Receipt and token tables must be added to the sync registry with explicit policy and must avoid messenger-owned sync paths.

Existing tests to reuse/update:

- `tests/test_sync_router_stale_events.py` already tests offer and offer_request stale/atomic guard behavior. Add trade guard tests beside this pattern.
- `tests/test_sync_router_apply_item_success.py`, `tests/test_sync_router_apply_item_errors.py`, `tests/test_sync_router_receive_basic.py`, `tests/test_sync_registry.py`, and sync health tests should cover the new table registrations and trade guard behavior.
- Add tests for destructive completed-trade delete/update ignored, duplicate completed sync idempotent, missing completed trade inserted, local non-completed becoming completed, and natural-key fallback guard coverage.

### Delivery receipt table and workers

Current reusable code:

- There is no existing `trade_delivery_receipts` model/table. This is additive state.
- Existing worker/reconciler patterns live in sync worker and recovery services, but delivery receipts should not be mixed with messenger runtime or generic notification relay state.
- `core/sync_registry.py` is the required place to document sync policy for new tables.

Current risks and required changes:

- `processing` leases are local execution state. If receipt rows sync, the opposite server must still never execute rows whose `destination_server` is not local.
- Terminal receipt states need monotonic conflict rules during sync, otherwise old non-terminal rows can reopen sent/skipped/not_required deliveries.
- Cleanup must retain one year, remove/archive terminal rows first, and alert if non-terminal rows older than one year exist.
- Queue scans must use indexed filters by `destination_server`, `channel`, `status`, and `next_retry_at`.
- Metrics must expose active queue count, oldest pending/retry, terminal counts, conflict counts, and rows near retention boundary.

Existing tests to add:

- Receipt upsert uniqueness and idempotency.
- Atomic claim under concurrency.
- Lease expiry/reclaim.
- Destination-server ownership.
- Terminal monotonic sync merge.
- Cleanup and non-terminal-old-row alert.
- WebApp receipt worker transaction behavior.
- Telegram receipt worker retry/skip/permanent-failed classification.

## Low-Risk Implementation Stages

### Stage A0: Tier-1 Telegram Bot Access

Add Telegram bot access for tier-1 customers before validating Telegram delivery guarantees for tier-1 users.

This stage must run after the Telegram account-linking foundation above, because tier-1 bot access depends on the same WebApp-issued link flow, contact verification, and bot-eligibility guards.

Exit criteria:

- tier-1 customers can link Telegram through the WebApp
- tier-1 customers can use the bot market capabilities allowed for full market access
- tier-2 customers remain blocked from Telegram bot access
- accountants remain blocked from Telegram bot access
- bot eligibility is enforced by a shared backend policy, not only by frontend visibility or message text
- the same policy gates WebApp link-token creation, bot account linking, bot menu/market visibility, bot offer creation, bot trade execution, and mandatory channel join handling
- disconnected customer users are not bot-eligible because the existing unlink flow soft-deletes them; any future access requires a new live account/relation created through the existing onboarding flows
- tests prove tier-1 linked and unlinked delivery behavior
- tests prove tier-1 bot trades keep the existing customer-owner trade path and do not allow direct customer-to-counterparty settlement

### Stage A: Read-Only Audience Builder

Create a canonical read-only audience builder for trade completion.

It must not send messages and must not mutate trade state.

It must reuse the current customer-chain behavior rather than reimplementing customer trade execution. The builder should derive recipients from already committed `trades` rows, active customer relations, and active accountant relations.

Before writing new audience logic, the implementation must search and review the existing trade notification paths, especially the owner/accountant audience helper, customer relation map loader, trade path summary builder, and trade message bundle builder.

Exit criteria:

- tests cover direct users, admins, tier-1, tier-2, accountants, customer owners, and customer-chain trades
- tests prove customer-chain delivery matches the current trade route behavior without changing the trade execution path
- tests cover linked and unlinked Telegram states
- tests cover all `offer.home_server` combinations

### Stage B: Delivery Receipt Table

Add `trade_delivery_receipts` with unique constraints and indexes.

Exit criteria:

- migration is additive
- no existing trade path depends on the new table
- repeated upsert cannot create duplicate delivery rows
- retention keeps at least one year of data
- retention cleanup exists for rows older than one year
- non-terminal rows older than one year are alerted before cleanup
- phase 1 does not require table partitioning
- queue, audit, recipient-history, and active-state partial indexes are added
- table and index size metrics are observable
- `notifications.dedupe_key` is added as nullable
- a partial unique index protects non-null notification dedupe keys
- `notifications.extra_payload JSONB` is added as nullable
- notification read APIs return `extra_payload`, or equivalent flattened metadata, for trade notifications
- frontend history and real-time notification handling use one normalized notification schema
- trade notification history keeps route, trade number, counterparty, and receipt metadata after refresh
- WebApp trade notifications use `trade_completed:webapp:{trade_id}:{recipient_user_id}`
- existing notifications with null dedupe keys continue to work unchanged

### Stage C: Shadow Reconciliation

Run reconcilers in dry-run or shadow mode.

They should compute missing deliveries and log/report them without sending new messages.

This stage is mandatory before any new reconciler performs real sending. It is the gate that proves expected delivery receipts, current immediate side-effect coverage, and duplicate risk before behavior changes.

Exit criteria:

- no production behavior change
- report shows expected recipients per trade
- report proves current gaps, especially Iran-authoritative Telegram delivery gaps
- report proves no completed trade is invisible to the reconciler because of sync conflict handling
- report proves workers ignore receipts whose `destination_server` belongs to the other server
- report compares immediate side-effect coverage against receipt/reconciler expectations without sending duplicates
- report shows idempotent trade replays whose delivery receipts/notifications are missing and proves they can be repaired

### Stage D: WebApp Repair First

Enable WebApp delivery repair on Iran only.

This is lower risk than Telegram because WebApp notifications are persistent database rows and can be inspected directly.

Exit criteria:

- missing WebApp notifications are repaired
- no duplicate visible notifications for the same trade/recipient
- notification repair can be disabled without affecting trade execution
- immediate WebApp trade notifications and WebApp reconciler repair both pass through the same idempotent delivery helper
- the idempotent helper creates/loads the WebApp notification and marks the receipt `sent` in one transaction
- direct calls to the generic auto-committing notification helper are not used for receipt-backed trade delivery
- grep/static tests prove trade-completion paths no longer call the generic auto-committing helper directly except through the receipt-backed helper

### Stage E: Telegram Repair On Foreign

Enable Telegram delivery repair on foreign.

Exit criteria:

- only foreign sends Telegram
- linked required recipients get Telegram messages
- unlinked or WebApp-only recipients are marked `not_required`
- temporary infrastructure failures retry
- non-user fatal failures are visible by `trade_number`
- invalid/unreachable Telegram accounts are marked `skipped` with logs, never unhandled exceptions
- skipped Telegram receipts are not left in the queue and are not resent as backlog after account repair
- WebApp-only fallback remains visible when Telegram delivery is skipped
- retry uses the latest linked Telegram identity on every infrastructure retry
- direct trade-route Telegram sends are removed/disabled before Telegram repair sends messages
- the trade route only creates/upserts Telegram receipts and optionally wakes a local worker
- only the foreign Telegram delivery worker claims receipts and sends Telegram private messages
- grep/static tests prove trade-completion paths no longer call `_queue_trade_telegram_message` or `send_telegram_message_sync` directly after Telegram repair sending is enabled
- if a worker crash creates an ambiguous Telegram-send state, duplicate-safe resend is preferred over silence
- duplicate-safe resend text must briefly say that if the user already received the same trade message, the second copy can be ignored

### Stage F: Observability And Manual Recovery

Add support view or operational command by `trade_number`.

Minimum report:

- trade participants
- required recipients
- WebApp delivery status
- Telegram delivery status
- reason for not required
- attempt count
- last error
- destination server
- sent time

Exit criteria:

- support can answer "why did user X not receive trade Y?"
- manual retry is not required in the first phase
- the design should not block adding admin/operator manual retry in a future phase

### Stage G: Staging Validation

Run staging validation before any production enablement.

Required tests:

- all `OH/OUH/RUH` combinations
- existing eligible user sees Telegram connect in profile/settings
- existing eligible unlinked user sees Telegram connect below today's trades
- existing linked user sees connected state and no onboarding prompt
- new eligible user sees post-registration Telegram connect/skip step
- skipped new eligible user enters WebApp and still sees dashboard connect action
- WebApp-issued Telegram link token valid/expired/used/invalid cases
- Telegram contact belongs to sender and phone matches WebApp account
- Telegram contact from another Telegram user is rejected
- `/start` without a valid `link_{token}` returns only the neutral low-information response
- `/link` without a valid WebApp-issued token cannot start contact linking
- WebApp offer plus WebApp request
- WebApp offer plus Telegram request
- Telegram offer plus WebApp request
- Telegram offer plus Telegram request
- linked and unlinked bot-eligible users
- tier-2 customer request-only path
- tier-1 WebApp-full path
- accountant WebApp-only monitoring path
- customer owner/principal delivery
- customer-chain trades
- Telegram id present but private message delivery fails because the Telegram user/account is unreachable; receipt becomes `skipped`, the app continues, and no backlog is created
- Telegram account is fixed or changed after earlier skipped deliveries; only new trades after the fix send Telegram messages
- notification history after refresh preserves trade route, trade number, counterparty, and receipt metadata
- real-time and history notifications use the same normalized frontend shape
- idempotent trade replay with missing receipt or notification triggers repair instead of hiding the gap
- partial and full offers
- concurrent requests on one offer
- sync delay and recovery
- sync conflict guard for completed trades
- destructive completed-trade sync delete/update ignored with conflict metrics
- receipt sync ownership by `destination_server`
- Telegram temporary failure and retry
- worker crash before send
- worker crash after send before `sent`
- medium and long outage skip policy
- medium/long outage recovery does not send old-trade WebApp reports, WebApp notifications, or Telegram messages
- one-year receipt retention and cleanup guard behavior

## Why This Can Satisfy The User Requirements

This plan can satisfy the stable-connectivity requirement because it does not depend on the original side effect completing successfully.

If a trade exists, the reconcilers can derive:

- who must be notified
- which channels are required
- which server must deliver each channel
- which deliveries are missing

Therefore, under stable connectivity and healthy workers, missing delivery is repaired from durable trade data.

The trade path stays stable. The guarantee is added around it.

## Resolved Product Decisions From Review

1. When a tier-1 or tier-2 customer is disconnected by their owner/principal, the existing behavior remains authoritative: the customer user account is soft-deleted and the relation is closed. The same person may later be added again as an accountant, customer, or normal user through existing onboarding flows.
2. Medium/long outage recovery must not send old-trade reports or notifications to users. Old trades remain visible through passive history where applicable, and operational audit/receipt state may retain the skip reason without creating user-facing messages.
3. The neutral no-token bot response must reveal no project structure, role model, WebApp mechanics, token policy, or market details to random Telegram users. The accepted baseline copy is: "پس از تکمیل ثبت‌نام، بات را از مسیر اعلام‌شده شروع کنید." This is understandable enough for real project users and ambiguous for outsiders.

## Resolved Engineering Directions From Review

1. `_trade_sync_guard_reason` must be implemented as a small deterministic guard in the sync receiver before trade upsert/delete execution. It must use existing local trade terminal state, incoming sync operation shape, and server authority metadata; it must not rely on ad hoc text matching, bypassable natural-key fallback, or broad exception handling. Existing `offers` and `offer_requests` guards stay intact and get only the minimal integration needed for consistent metrics/tests.
2. Phase-1 receipt storage must use additive, reversible Alembic migrations with PostgreSQL-native constraints and indexes. Use no table partitioning in phase 1. Dedupe must be enforced with partial unique indexes where needed, receipt lookup/lease cleanup paths must have explicit indexes, and migration tests or schema inspection tests must verify the generated indexes and constraints.
3. Future manual/operator retry is reserved as an audited operator capability, not as a phase-1 user-facing resend feature. If added later, it must reuse the same receipt claim/lease/destination ownership/idempotency path and must never bypass delivery guards or create direct duplicate sends.
4. Telegram send failures caused by an unusable linked Telegram account must be non-terminal for the user identity and non-blocking for the application. They should be recorded on that trade delivery attempt, should not create an infinite pending backlog, and future trades should retry against the latest linked Telegram identity.

## Remaining Engineering Design Work

1. Specify the exact `_trade_sync_guard_reason` return contract, metrics labels, and sync receiver call site during implementation.
2. Specify the exact Alembic revision contents for receipt tables, partial unique indexes, cleanup indexes, and downgrade behavior before coding the migration.
3. Specify the future operator retry permission/audit shape only when that phase is explicitly requested; phase 1 must only keep the design compatible with it.

## Non-Goals

1. This plan does not make Telegram physically reliable.
2. This plan does not guarantee browser push delivery to a closed or restricted device.
3. This plan does not move WebApp hosting to foreign.
4. This plan does not allow Iran to call Telegram.
5. This plan does not require rewriting trade execution before the delivery guarantee is validated.

## Implementation Rule

All implementation based on this document must be done on the correct bot/WebApp integration branch or a branch explicitly created for this work. Before any commit, the active branch must be checked. Changes must be validated in staging first and must not be merged to another branch or deployed to production without an explicit user request.
