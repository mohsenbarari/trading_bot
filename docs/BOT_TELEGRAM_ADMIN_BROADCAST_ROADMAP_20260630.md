# Telegram Bot Admin Broadcast Roadmap - 2026-06-30

## Goal

Add a Telegram-bot-only broadcast feature so `SUPER_ADMIN` can send a plain text management message to:

- all users who are currently connected to and eligible for the Telegram bot;
- selected bot users chosen through search and multi-select;
- useful predefined groups of bot users, including ordinary users, managers, and Tier 1 customers.

This feature is intentionally independent from the existing WebApp/admin-message broadcast surface for this release.

## Locked Product Decisions

- Scope is Telegram bot direct messages only.
- WebApp notifications, WebApp admin-message history, and internal messenger rooms are out of scope for this stage.
- Only `SUPER_ADMIN` can create and send a Telegram bot broadcast.
- `MIDDLE_MANAGER` cannot send bot broadcasts in this stage.
- "All users" means all active users who have a linked Telegram account and pass the shared bot access policy.
- Users without `telegram_id`, users without bot access, accountants, Tier 2 customers, deleted users, and inactive users must be skipped.
- Tier 1 customers that are linked to the bot are valid recipients.
- Text is plain text. Plain URLs are allowed as ordinary text.
- Markdown/HTML formatting is out of scope for this stage to avoid Telegram parse failures.
- The bot UX must support:
  - send to everyone;
  - send to predefined groups;
  - send to multiple specific users selected by search;
  - cancel from every step;
  - preview and explicit confirmation before enqueueing delivery.

## Current Code Context

- Existing WebApp/admin management messages live in `models/admin_message.py`, `api/routers/admin_messages.py`, and `core/services/admin_message_service.py`.
- Existing WebApp broadcast creates internal messenger system rooms plus WebApp notifications; it does not send Telegram direct messages.
- Existing WebApp broadcast is already a synced WebApp/admin surface (`admin_broadcast_messages`) and must not be reused directly for this bot-only feature, because that would create messenger/WebApp side effects.
- Existing Telegram execution must stay foreign-only through `core.telegram_gateway`.
- Existing bot access eligibility is centralized in `core.services.bot_access_policy.evaluate_bot_access`.
- Existing Telegram trade delivery code already contains useful classification patterns for retryable, unreachable, and malformed Telegram errors in `core/services/trade_telegram_delivery_service.py`.
- Existing trade Telegram delivery also provides the safest local pattern for receipt-backed delivery, per-recipient status, retry/backoff, idempotency, and no-crash handling of unreachable Telegram users.
- Existing bot admin panel entrypoint is in `bot/handlers/admin.py` and admin keyboard wiring is in `bot/keyboards.py`.
- Existing bot runtime starts background tasks in `run_bot.py`; the broadcast worker must be registered there only after a foreign-only authority guard is in place.

## Recommended Architecture

Use a dedicated Telegram broadcast delivery pipeline instead of sending messages inline from an aiogram handler.

### Persistence

Add dedicated tables rather than overloading `admin_broadcast_messages`, because this release must stay independent from WebApp/messenger broadcast behavior:

- `telegram_admin_broadcasts`
  - `id`
  - `content`
  - `created_by_id`
  - `audience_type`: `all`, `group`, `selected`
  - `target_groups`: JSON list for grouped sends
  - `recipient_count`
  - `status`: `drafted`, `queued`, `running`, `completed`, `completed_with_errors`, `failed`
  - `created_at`, `queued_at`, `completed_at`
- `telegram_admin_broadcast_receipts`
  - `id`
  - `broadcast_id`
  - `recipient_user_id`
  - `telegram_id_at_enqueue`
  - `dedupe_key`
  - `status`: `pending`, `sending`, `sent`, `skipped`, `retryable_failed`, `terminal_failed`
  - `reason`
  - `telegram_message_id`
  - `attempt_count`
  - `next_retry_at`
  - `last_error_class`
  - `last_error_message`
  - `worker_id`
  - `lease_until`
  - `created_at`, `sent_at`, `updated_at`

Unique constraints:

- `(broadcast_id, recipient_user_id)` on receipts.
- `dedupe_key` on receipts.
- An idempotency key generated as `telegram-admin-broadcast:{broadcast_id}:{recipient_user_id}:{attempt_count}` for each Telegram send attempt.

Recommended indexes:

- queue index on `(status, next_retry_at, id)`;
- lease recovery index on `(status, lease_until)`;
- broadcast status/history index on `(created_at, id)`;
- recipient lookup index on `(recipient_user_id, created_at)`.

### Cross-Server And Sync Contract

This feature is bot-only, but the tables are non-messenger operational data. To stay aligned with the project-level sync policy, the default implementation should sync the new tables unless a later product decision explicitly marks them foreign-local.

Required sync work if the dedicated tables are added:

- register both tables in `core.sync_registry`;
- add SQLAlchemy event listeners in `core.events` so inserts/updates/deletes emit change-log rows;
- add model mapping/table priority/public identity handling in the sync router where needed;
- add parity identity fields in `core.sync_parity`;
- add field policy entries so local execution fields do not create false parity drift.

Field policy recommendation:

- sync stable business/audit fields: `content`, `created_by_id`, `audience_type`, `target_groups`, `recipient_count`, `status`, `broadcast_id`, `recipient_user_id`, `telegram_id_at_enqueue`, `dedupe_key`, `reason`, `telegram_message_id`, `attempt_count`, `next_retry_at`, `last_error_class`, `last_error_message`, timestamps;
- treat worker execution fields as local-only: `worker_id`, `lease_until`;
- never let the Iran server process or send Telegram broadcast receipts even if it receives synced rows.

### Runtime

- The aiogram FSM only gathers audience and content, shows preview, then creates queued rows.
- A background worker running on the foreign bot service sends queued receipts with rate limiting.
- The worker must never run on Iran.
- Add a `core.background_job_authority` entry for the broadcast worker before wiring the loop into `run_bot.py`.
- The worker uses `telegram_gateway.send_message` with no parse mode.
- Telegram `429 retry_after` should set `next_retry_at`.
- Network/timeouts should be retryable.
- Permanent user-unreachable errors should become terminal/skipped without crashing.
- Message too long or malformed payload should fail the broadcast safely and alert in logs.
- Claim receipts atomically with a short lease; recover expired leases in the worker cycle.

## Bot UX Flow

### Entry

Add one admin-panel button:

- `📣 ارسال پیام همگانی بات`

Only show or honor it for `SUPER_ADMIN`.

### Main Menu

After the button:

- `ارسال برای همه کاربران بات`
- `ارسال برای گروه‌ها`
- `ارسال برای کاربران خاص`
- `لغو`

### All Users

1. Ask for message text.
2. Validate non-empty and Telegram length-safe.
3. Show preview and estimated recipient count.
4. Require explicit confirmation.
5. Enqueue broadcast and show queued summary.

### Groups

Group choices should be explicit and multi-select:

- کاربران عادی
- مدیران
- مشتریان سطح1

The resolver must still apply linked-Telegram and bot-access checks.

### Selected Users

1. Ask for search text: account name, full name, username, or mobile.
2. Show only active, non-deleted, linked, bot-eligible users.
3. Let admin toggle multiple users.
4. Keep a selected-count summary.
5. Admin can continue searching and adding more users.
6. Admin presses `ادامه و نوشتن پیام`.
7. Ask for text, preview, confirm, enqueue.

Implementation note:

- Do not store selected recipient ids inside Telegram `callback_data`; Telegram callback payloads are small.
- Store selected ids in FSM/Redis state and keep callback data limited to small actions like `toggle:<user_id>`, `page:<n>`, `done`, and `cancel`.

## Recipient Resolution Rules

Every candidate recipient must pass all of these:

- `User.is_deleted == False`
- active account status
- `telegram_id is not null`
- shared `evaluate_bot_access(db, user).allowed == True`
- active bot-linked role policy
- not the sender if product chooses to exclude sender; default should exclude sender for all/group broadcasts and include only if explicitly selected.

For selected-user search, customers should be displayed with the customer management name where local helpers already support it.

## Operational Challenges

1. Rate limiting and flood control.
   - Use worker-level throttling and `retry_after`.
2. Exactly-once behavior.
   - Receipt uniqueness and idempotency keys prevent accidental duplicate sends from retries.
3. Long-running sends.
   - Do not block bot polling or callback responses.
4. Telegram delivery uncertainty.
   - Store sent/skipped/failed status per recipient.
5. Cross-server scope.
   - Because this stage is bot-only, do not create WebApp notifications or messenger rows.
6. User eligibility drift.
   - Resolve recipient list at enqueue time and store `telegram_id_at_enqueue`.
   - Worker should re-check current basic eligibility before sending to avoid messaging deleted/inactive users.
7. Audit and abuse prevention.
   - Store actor, recipient count, target mode, and final status.
   - Log without leaking message content in high-cardinality/structured logs.
8. Message content safety.
   - Plain text only; no Markdown/HTML parse mode.
   - Enforce Telegram text length limits before queueing.
9. Sync/parity drift.
   - New broadcast tables must either be explicitly registered as synced non-messenger operational data or explicitly documented as foreign-local. The default is synced.
   - Worker lease fields must be local-only to avoid false parity mismatches.
10. Callback forgery and role drift.
   - Every callback handler must re-check `SUPER_ADMIN`; showing the button only to superadmin is not enough.
   - Re-check recipient eligibility before send so deleted/inactive users do not receive queued messages after enqueue.

## Implementation Stages

### Stage 1 - Contract And Schema

- Add models and Alembic migration for broadcasts and receipts.
- Add enums/constants for audience type and receipt status.
- Add queue, lease, dedupe, and recipient lookup indexes.
- Add unit tests for schema constraints and model import/sync registry requirements.

### Stage 1B - Sync/Authority Registration

- Add background job authority for the foreign-only broadcast worker.
- Register new tables in sync registry/events/router/parity if they are implemented as synced operational data.
- Add sync field policy for local-only worker fields.
- Add tests proving Iran cannot claim/send Telegram broadcast receipts.

### Stage 2 - Recipient Resolver

- Implement a shared service for:
  - all bot users;
  - group bot users;
  - selected searched users.
- Reuse `evaluate_bot_access`.
- Add tests for standard users, managers, Tier 1 customers, Tier 2 customers, accountants, inactive users, deleted users, unlinked users, and duplicate candidates.

### Stage 3 - Queue Creation Service

- Implement a service that validates content, creates broadcast row, creates deduped receipt rows, and returns a queued summary.
- Add idempotency/unique constraint tests.
- Make sure no Telegram API call happens in this stage.

### Stage 4 - Telegram Delivery Worker

- Implement foreign-only worker loop.
- Send pending receipts with bounded concurrency and rate limits.
- Classify Telegram gateway responses using patterns aligned with trade delivery.
- Persist retry/terminal/sent status.
- Add tests for success, retryable network failure, `429 retry_after`, user unreachable, missing bot token, and long/malformed message.

### Stage 5 - Bot FSM And Admin UI

- Add bot states for broadcast audience selection, search, selected recipients, content, preview, and confirmation.
- Add admin keyboard button for `SUPER_ADMIN`.
- Store selected users in FSM state, not in callback payloads.
- Add cancel/back behavior in every state.
- Add tests for all-users flow, group flow, selected-users flow, denial for non-superadmin, and confirmation protection.

### Stage 6 - Observability And Status

- Add concise bot response after enqueue.
- Add admin-visible summary command or callback for recent broadcast status if low risk.
- Add structured logs for queued/completed/failed counts without printing content.

### Stage 7 - Staging Validation

- Use staging bot with a small set of linked users.
- Validate all-users, group, selected-users, cancellation, retryable failure, and no-WebApp-side-effect behavior.
- Confirm no Iran Telegram calls occur.

## Test Matrix

- Superadmin can open broadcast menu.
- Middle manager cannot open or trigger callbacks.
- Standard user cannot trigger callbacks.
- Send-to-all resolves only linked and bot-eligible users.
- Group ordinary users excludes managers, accountants, Tier 1 customers, Tier 2 customers.
- Group managers includes superadmin and middle managers that are bot-eligible.
- Group Tier 1 customers includes only linked Tier 1 customers.
- Selected-user search excludes unlinked, deleted, inactive, accountant, and Tier 2 users.
- Selected-user search supports multiple selections and dedupes repeated selection.
- Plain URL in text sends as text.
- Empty text rejected.
- Over-limit text rejected before queueing.
- Confirm button required before queueing.
- Cancel clears FSM state.
- Worker sends pending receipts and records Telegram message id.
- Worker respects retry_after.
- Worker does not crash on permanent Telegram errors.
- Worker does not run on Iran.
- No WebApp notification, admin-message room, or messenger row is created in this stage.
- Synced receipt rows do not cause Iran-side Telegram side effects.
- Worker lease fields do not appear as business parity drift.
- Forged non-superadmin callbacks are rejected server-side.

## Open Items Before Coding

No blocking product question remains after the 2026-06-30 decisions. The implementation should proceed with the defaults above unless the product scope changes.
