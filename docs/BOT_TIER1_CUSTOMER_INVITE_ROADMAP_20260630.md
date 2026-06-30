# Telegram Bot Tier1 Customer Invite Roadmap - 2026-06-30

## Scope

This roadmap covers adding **tier1 customer invitation from the Telegram bot** while keeping Iran as the authoritative WebApp/customer-invite server and foreign as the Telegram-only server.

Non-negotiable boundaries:

- Telegram stays on `foreign`; Iran never connects to Telegram.
- Customer invitation creation remains authoritative on Iran.
- The bot may collect input and perform read-only preliminary validation on foreign, but it must not create the customer relation locally.
- Customer/accountant users must not be able to invite customers from bot or WebApp.
- Bot-based invitation is allowed only when cross-server connectivity is normal and required customer/user/invitation data is fully synced.
- Only tier1 customers can be invited from the bot. Tier2 invitation stays WebApp-only.

## Current Code Evidence

### Bot panel and current placeholder

- `bot/keyboards.py:get_persistent_menu_keyboard()` exposes `👤 پنل کاربر` to `STANDARD`, `POLICE`, `MIDDLE_MANAGER`, and `SUPER_ADMIN`.
- `bot/keyboards.py:get_user_panel_keyboard(..., standard_actions=True)` currently exposes the standard panel actions only when `user_role == UserRole.STANDARD`.
- `bot/handlers/panel.py:show_my_profile_and_change_keyboard()` currently enters the new standard user panel only for `STANDARD` non-customer users.
- `bot/handlers/panel.py:get_user_panel_customers_keyboard()` currently adds one button: `➕ دعوت مشتری`.
- `bot/handlers/panel.py:user_panel_customer_invite_placeholder()` only answers: `دعوت مشتری از بات در مرحله بعد اضافه می‌شود.`
- `tests/test_bot_panel_standard_actions.py` currently asserts the placeholder behavior.

Impact:

- The bot invite feature is not implemented yet.
- Managers can see the top-level user panel button, but the customer sub-actions must be explicitly opened for eligible managers as well.

### WebApp customer invite source of truth

- `api/routers/customers.py:create_my_customer()` is the current WebApp route for `POST /owner-relations`.
- `api/routers/customers.py:ensure_owner_context()` blocks accountants and customer users from managing owner customers.
- `core/services/customer_relation_service.py:create_owner_customer_relation()` performs the authoritative validation and creates:
  - `Invitation`
  - `CustomerRelation`
  - pending status
  - customer invitation token
  - default `UserRole.STANDARD` invitation
- `core/sms.py:send_customer_invitation_sms()` sends the dedicated customer invitation SMS and returns a boolean.
- The current WebApp route calls `send_customer_invitation_sms()` but does not expose the returned boolean.

Impact:

- The bot implementation must reuse this service path through an internal Iran endpoint.
- For the bot success message to mean "SMS was sent", the internal endpoint must return `sms_sent`.
- If the relation is created but SMS sending fails, retry must not create duplicate pending relations.

### WebApp account name generation

- `frontend/src/views/CustomerWorkspaceView.vue` generates customer `account_name` as `customer_<mobile_digits>`.
- `frontend/src/components/OwnerCustomerManagerModal.vue` sends:
  - `account_name`
  - `management_name`
  - `mobile_number`
  - tier/limit payload

Impact:

- Bot invite should generate the same account name from mobile: `customer_<normalized_mobile_digits>`.
- Bot tier1 payload should not send tier2-only commission/limit fields unless a future product decision adds them.

### Customer display name pattern already exists

- `bot/utils/customer_display.py:user_display_name()` prefers `customer_management_name` over `account_name`.
- `bot/utils/customer_display.py:attach_customer_management_names()` enriches active customer users from `CustomerRelation.management_name`.
- `bot/handlers/panel.py:_customer_relation_name()` uses `relation.management_name` before customer account name.
- `core/services/user_management_context_service.py:attach_user_management_context()` sets `customer_management_name`, owner id/name, and customer tier.
- `api/routers/users_public.py` serializes `customer_management_name` for visible customer search results.
- `api/routers/chat.py` loads `CustomerRelation.management_name` for visible customer message senders.

Impact:

- The naming policy already exists in major WebApp and bot paths.
- The new bot invite flow must preserve it and add regression tests around the new customer panel and invite-result paths.

### Existing internal cross-server signing pattern

- `core/trade_forwarding.py` provides:
  - deterministic JSON body serialization
  - `sign_internal_payload()`
  - `verify_internal_signature()`
  - `X-API-Key`, `X-Timestamp`, `X-Signature`, `X-Source-Server`
  - TLS verify configuration
  - `peer_server_url_for()`
- Internal trade/offer endpoints already use this pattern.

Impact:

- Customer invite forwarding should reuse the same signed internal request pattern instead of creating a new trust model.

### Existing sync health signals

- `api/routers/sync.py:get_sync_health()` returns:
  - `peer_server_url_configured`
  - `redis_ok`
  - `unsynced_change_log_count`
  - `unsynced_by_table`
  - `redis_queues.sync:outbound`
  - `redis_queues.sync:retry`
  - `parity_status`
  - publication reconciliation state

Impact:

- Bot invite must check connectivity/sync health before accepting input for creation.
- The gate must be strict enough to avoid creating customer invitations while customer/user/invitation tables are stale.
- The gate should not be blocked forever by unrelated historical drift if the required customer-invite tables are clean.

## Product Decisions Locked

- Bot invite creates only tier1 customer invitations.
- Tier2 button is visible but WebApp-only. On click it must show:

```text
مشتریان سطح2 فقط به وب اپ دسترسی دارند! بنابراین برای دعوت این مشتریان به وب اپ مراجعه فرمایید.
```

- Standard users and managers are eligible to invite tier1 customers from the bot, provided they are not customers/accountants.
- Customers and accountants must be blocked from inviting customers.
- Bot flow collects customer display/management name and mobile number.
- Payload forwarded to Iran must include owner/group-leader identity, customer name, mobile number, generated account name, and tier1.
- Foreign performs preliminary validation from synced data. Iran performs final validation and is authoritative.
- If preliminary or final validation fails, the bot must show a clear Persian message.
- After SMS provider acceptance is confirmed, the bot sends a success message to the inviter. No invitation link should be displayed in the bot.
- SMS provider acceptance means the template request returned a successful provider response and usable acceptance result. It does not guarantee final delivery to the customer's handset.
- If a duplicate pending invitation already exists for the same owner/mobile/account-name, the bot should not create another row; it should return a success-like "already registered" message.
- If the success response is delayed by a short or medium cross-server interruption, the bot should send the success message after connectivity returns and the successful result is known. For a long interruption, no delayed success message is required.

## Engineering Decisions

### SMS result handling

The current WebApp route ignores the boolean returned by `send_customer_invitation_sms()`. For bot correctness, the internal Iran endpoint should return:

- `created: true/false`
- `already_pending: true/false`
- `relation_id`
- `sms_sent: true/false`

Bot message behavior:

- `sms_sent=true`: tell inviter the invitation was registered and SMS was sent.
- `already_pending=true`: tell inviter an active pending invitation already exists and another invitation was not created.
- `sms_sent=false` after a newly created relation: tell inviter the invitation was registered but SMS sending failed, without retrying creation automatically.

Reason:

- This avoids false success messages.
- It avoids duplicate pending relations.
- It keeps retry/resend as a future explicit operator/user feature instead of a hidden loop.

`messageId` policy:

- Returning, storing, or exposing the provider `messageId` is not required in this feature.
- `sms_sent=true` is sufficient for the bot contract because it means SMS.ir accepted the template send request according to the current helper checks.
- Do not add `messageId` to the bot-facing contract, internal endpoint response, or persistence layer unless a future explicit requirement reopens provider-level delivery tracking.

### Sync/connectivity gate

Before starting or submitting the tier1 invite flow, foreign should require:

- peer Iran URL configured
- signed internal request path reachable
- Redis health readable
- local foreign `sync:outbound == 0`
- local foreign `sync:retry == 0`
- Iran `/api/sync/health` reachable with `X-Observability-Api-Key`
- Iran `sync:outbound == 0`
- Iran `sync:retry == 0`
- no Iran unsynced rows in required Iran-authoritative tables:
  - `users`
  - `customer_relations`
  - `accountant_relations`
  - `invitations`
- latest parity status fresh and clean for the required table scope, if a table-scoped parity summary is available

If only global parity is available and reports unrelated historical drift, the implementation should prefer a customer-invite-scoped gate rather than permanently disabling this feature.

Operational definition for this feature:

- "Synced" is primarily state-based, not only time-based.
- If any required Iran-authored table has unsynced Iran rows, or either relevant sync queue is non-empty, the bot should treat customer invitation as not fully synced.
- A short grace window is allowed for momentary backlog: retry the health check for about 5-10 seconds.
- If the required table/queue backlog is still present after that grace window, the bot must reject the invite flow as temporarily unavailable due to sync state.
- The existing global parity freshness window is 900 seconds, but this feature must not rely on that broad observability value as permission to invite customers. The invite gate must inspect the required tables and queues at request time.
- Foreign-only `/api/sync/health` is not enough for this decision because `users`, `customer_relations`, `accountant_relations`, and `invitations` are Iran-authored and sync-apply on foreign suppresses outbound `change_log` rows. The gate must measure the Iran -> foreign direction by reading Iran's outbound health and foreign's local queue drain state.

Outage behavior:

- Short outage: up to 2 minutes. Do not accept new bot customer invites. If Iran already accepted the invitation and the success response was delayed, send the success message after connectivity returns and the successful result is known.
- Medium outage: more than 2 minutes up to 1 hour. Do not accept new bot customer invites. If a previous successful result can be resolved after recovery, send the success message.
- Long outage: more than 1 hour. Do not accept new bot customer invites. No delayed success message is required.

Reason:

- Customer invite depends on current user, customer, accountant, and invitation state.
- Global offer/history drift should not block customer invite forever if customer-invite tables are clean.
- Iran final validation remains the source of truth even after foreign preliminary validation passes.
- A 5-10 second grace prevents false denial for normal momentary queue movement while still keeping this feature strict enough for customer identity/ownership data.

### Idempotency

The internal Iran endpoint must accept an idempotency key derived from:

- source server
- owner user id
- normalized mobile
- customer tier

The endpoint must still query existing pending/active relation state before creating. For this release, duplicate/race protection is implemented as:

- explicit pre-check after `sweep_expired_pending_customer_relations()`;
- an Iran-side Redis atomic lock keyed by the idempotency key before the authoritative create path;
- a second pre-check after acquiring the lock.

Reason:

- This avoids adding a schema migration while unrelated bot-onboarding migration files are currently dirty in the checkout.
- Same owner/mobile/tier retries and double-taps serialize safely.
- If the first request created the relation but the response was lost, the next request returns the existing pending relation instead of creating a second row.

Future hardening:

- Add a database-backed partial unique guard or a dedicated idempotency table after the migration chain is clean, especially if product policy must prevent cross-owner simultaneous pending relations for the same mobile at the database layer.

## Implementation Roadmap

### Stage 0 - Branch, Scope, and Safety Guard

- Check `git status --short --branch` before any implementation commit.
- Keep this feature on the intended bot/WebApp integration branch unless the user explicitly asks otherwise.
- Do not deploy production without explicit user approval.
- Do not alter tier2 WebApp invitation logic.
- Do not alter messenger behavior.
- Add/maintain a short work summary in `.github/copilot-instructions.md` after code changes.

Exit criteria:

- Current branch is confirmed.
- No unrelated dirty files are mixed into the work.

### Stage 1 - Shared Customer Invite Contract

Add shared backend helpers/schemas for bot-origin customer invite:

- Normalize mobile using the same Persian-to-Latin normalization used by existing customer service.
- Generate `account_name = customer_<mobile_digits>`.
- Validate bot input:
  - non-empty management name
  - mobile format `09xxxxxxxxx`
  - tier is forced to `tier1`
- Define an internal request/response schema for Iran endpoint.

Recommended files:

- `schemas.py`
- new helper module under `core/services/` or `core/customer_invite_forwarding.py`
- tests near existing customer service/router tests

Exit criteria:

- Unit tests cover normalization and account-name generation.
- No database mutation happens on foreign in these helpers.

### Stage 2 - Bot Panel Eligibility and UI

Update the bot user panel:

- Make the customer panel actions available to eligible standard users and managers.
- Keep customers and accountants blocked from customer-invite actions.
- Replace the single `➕ دعوت مشتری` button with:
  - `➕ دعوت مشتری سطح1`
  - `➕ دعوت مشتری سطح2`
- Tier2 callback only shows the exact product message and does not start any flow.

Required care:

- Do not expose customer invite to customer users even if their role is `STANDARD`.
- Do not expose customer invite to accountants.
- Keep existing customer list/detail/unlink behavior intact.

Exit criteria:

- Bot keyboard tests updated.
- Placeholder test removed or replaced with tier1/tier2 behavior tests.
- Manager eligibility test added.
- Customer/accountant denial test added.

### Stage 3 - Foreign Preliminary Validation and Sync Gate

Implement a foreign-side read-only validation before forwarding:

- Check current server is foreign for the Telegram path.
- Check connectivity/sync gate with two-sided direction awareness:
  - Iran health via `/api/sync/health` and `OBSERVABILITY_API_KEY`;
  - local foreign Redis queues;
  - required Iran-authored table backlog from Iran, not foreign.
- Load inviter user by Telegram-linked account.
- Reject if inviter is customer.
- Reject if inviter is accountant.
- Validate owner capacity using synced data.
- Check duplicate existing user by mobile/account name using synced data.
- Check duplicate pending/active customer relation by mobile/account name using synced invitation/customer data.
- Check duplicate management name for the same owner.

Important:

- This validation is only a fast user-facing guard.
- Race conditions or stale synced data are still resolved by Iran final validation.

Exit criteria:

- Tests prove foreign validation does not commit.
- Tests prove dirty sync gate blocks invite before asking/forwarding final creation.
- Tests prove Iran validation error is still surfaced if foreign data was stale.

### Stage 4 - Internal Iran Endpoint

Add a signed internal endpoint on Iran, for example:

```text
POST /api/customers/internal/owner-relations
```

Endpoint requirements:

- Verify `X-API-Key`, `X-Timestamp`, `X-Signature`, `X-Source-Server`.
- Reject missing/invalid signature.
- Reject if current server is not Iran.
- Reject if source server is not foreign.
- Load owner/group-leader user by `owner_user_id`.
- Do not reuse JWT/session-bound WebApp owner context dependencies directly. Re-implement the same owner policy from the signed payload:
  - owner must not be deleted/disabled
  - actor/owner must not be customer
  - accountant context cannot create customers
- Force `customer_tier = tier1`.
- Generate or validate `account_name = customer_<mobile_digits>`.
- Reuse `create_owner_customer_relation()` for final creation.
- Send SMS through `send_customer_invitation_sms()`.
- Return `sms_sent`.
- If matching pending invitation already exists, return `already_pending=true` without creating a duplicate.
- Call SMS sending based on the relation/invitation creation result, not on `registration_link` availability. The SMS helper currently ignores the web link value and returns provider acceptance as a boolean.

Exit criteria:

- Internal endpoint tests cover signature accept/reject, source-server reject, non-Iran reject, tier2 reject, duplicate pending, SMS true/false.
- WebApp route behavior remains unchanged.

### Stage 5 - Bot FSM and Forwarding

Implement the Telegram flow:

1. User taps `دعوت مشتری سطح1`.
2. Bot checks eligibility and sync gate.
3. Bot asks for customer name.
4. Bot asks for customer mobile.
5. Bot shows a short confirmation summary.
6. Bot forwards signed request to Iran.
7. Bot maps Iran response to Persian message.

Forwarding requirements:

- Reuse the internal signing/TLS pattern from `core/trade_forwarding.py`.
- Use a short timeout with clear failure messages.
- Log safe metadata only:
  - owner user id
  - source/target server
  - idempotency present
  - response status
  - no raw mobile in general logs

Exit criteria:

- FSM tests cover normal success, cancel/back, invalid mobile, invalid name, sync dirty, timeout, Iran validation failure, duplicate pending, SMS failure.

### Stage 6 - Customer Display Name Regression

Keep and test the existing display rule:

- Owner sees customer by `CustomerRelation.management_name`.
- Same-group accountants see customer by `CustomerRelation.management_name` where they are allowed to view the customer.
- Other users must not gain customer visibility because of this feature.
- Bot customer panel uses `management_name`.
- Bot trade/history/profile display continues to prefer `customer_management_name`.

Exit criteria:

- Regression tests cover bot customer list/detail labels.
- Existing `users_public`, chat, trade-history display tests still pass.
- No fallback to `customer_09...` appears in owner/accountant-visible customer contexts when `management_name` exists.

### Stage 7 - Observability and Audit

Add explicit logs/events for:

- bot customer invite started
- validation denied
- sync gate denied
- internal forward success/failure
- Iran endpoint created relation
- duplicate pending returned
- SMS sent/failed

Audit requirements:

- Iran creation should audit similarly to WebApp `customer.link`.
- Do not log raw invitation token.
- Avoid raw mobile in ordinary logs; if needed, log a short digest.

Exit criteria:

- Tests or log redaction checks ensure sensitive data is not exposed.

### Stage 8 - Test Matrix

Required automated coverage:

- Standard user invites tier1 successfully from bot.
- Middle manager invites tier1 successfully from bot.
- Super admin behavior is explicit: either supported through user panel or rejected by policy, with test coverage.
- Customer user cannot invite.
- Accountant cannot invite.
- Tier2 button only shows WebApp-only message.
- Invalid mobile rejected before forwarding.
- Empty customer name rejected before forwarding.
- Duplicate pending returns already-pending message.
- Existing user/mobile duplicate denied.
- Duplicate management name for same owner denied.
- Sync dirty blocks invitation.
- Iran endpoint rejects unsigned request.
- Iran endpoint rejects wrong source server.
- Iran endpoint rejects tier2 payload.
- SMS false does not create a second relation on retry.
- After sync, bot customer list shows management name.

Required staging manual validation:

- Foreign bot -> Iran internal endpoint -> WebApp invitation appears.
- SMS is sent for the invited mobile.
- Invited user completes WebApp registration.
- After sync, bot customer list shows the customer under the chosen management name.
- Dirty sync/connection failure shows a clear bot message and creates no relation.

### Stage 9 - Deployment Readiness

Before production:

- Run focused backend/bot tests.
- Run customer-related WebApp tests.
- Run a small two-server staging validation for:
  - sync clean
  - bot invite success
  - duplicate pending
  - customer/accountant denial
  - tier2 WebApp-only message
- Confirm no pending test data remains in staging after validation.

Production release only after explicit user approval.

## Remaining Technical Watchpoints

- The exact sync gate must be implemented carefully. A global parity failure caused by unrelated historical drift should not make bot customer invite unusable forever, but customer/user/invitation table drift must block the flow.
- Current WebApp SMS behavior does not report SMS failure to the user. The bot internal endpoint should return `sms_sent` to avoid a false success message.
- Preliminary validation and final validation can disagree during a race. The bot must treat Iran response as authoritative.
- Existing customer display helpers are broad but not universal. New bot invite/customer-panel tests must prevent `customer_...` regressions in owner/accountant-visible contexts.
