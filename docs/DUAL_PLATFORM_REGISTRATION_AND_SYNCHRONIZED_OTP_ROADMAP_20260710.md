# Dual-Platform Registration And Synchronized OTP Roadmap - 2026-07-10

## Status

This roadmap records the accepted product and architecture baseline for adding users through
either the Iran WebApp or the foreign Telegram bot.

- Status: accepted design, implementation not started.
- Runtime scope: staging first; production is forbidden until the final production gate.
- Required implementation branch: `candidate/bot-webapp-integration`.
- Document handling: tracked and committed only on `candidate/bot-webapp-integration` by explicit
  owner request; this document alone does not authorize implementation or deployment.
- This document does not authorize a production deploy.

## Goal

Every eligible invitation must expose two usable completion paths:

1. Web-first registration in the Iran WebApp.
2. Telegram-first registration in the foreign bot without opening the WebApp.

Both paths must converge on one logical user account, one invitation-consumption decision, and
one cross-server product state. A near-simultaneous WebApp and Telegram attempt must never create
two users for the same invitation, mobile number, account name, or Telegram identity.

The target topology is:

```text
Web invitation link
  -> Iran WebApp
  -> shared authoritative registration service on Iran
  -> committed User + consumed Invitation
  -> durable sync to foreign

Telegram invitation link
  -> foreign Telegram bot
  -> Telegram-owned contact verification + address collection
  -> signed idempotent registration command to Iran
  -> shared authoritative registration service on Iran
  -> committed User with telegram_id + consumed Invitation
  -> durable sync to foreign
  -> foreign bot enables onboarding and panel after synced User is visible
```

## Locked Product Decisions

The following decisions are final for this roadmap and must not be reinterpreted during
implementation.

1. The Telegram bot runs only on `foreign`.
2. The WebApp runs only on `iran`.
3. Iran must not call the Telegram Bot API directly.
4. Foreign must not serve a WebApp or send users to a foreign WebApp hostname.
5. A normal invitation creates one token and two entry URLs. The URLs are two surfaces for the
   same invitation, not two independent invitations.
6. The WebApp link must complete registration fully in the WebApp.
7. The Telegram link must complete registration fully in the bot without requiring the user to
   open the WebApp.
8. Telegram-first registration does not require an OTP.
9. Telegram-first identity proof requires the Telegram `request_contact` flow.
10. The shared contact must belong to the sender: `contact.user_id == message.from_user.id`.
11. The normalized shared contact number must exactly match the invitation mobile number.
12. A typed mobile number is not acceptable proof for Telegram-first registration.
13. Telegram-first registration collects and confirms the user's address in the bot.
14. Iran is the single final writer for invitation completion and user creation/linking.
15. The foreign bot may collect and durably retain a completed registration intent, but it must
    not create a provisional or active `User` while Iran is unreachable.
16. A pending Telegram registration intent grants no full bot access.
17. After reconnection, Iran must reconcile current invitation, mobile, account-name, user, and
    Telegram-ID state before deciding to create or link anything.
18. A delayed Telegram intent must never cause blind user creation after an outage.
19. If the same user was created in the WebApp during the outage, reconciliation links the
    verified Telegram ID to that existing user instead of creating a second user.
20. Existing WebApp-created identity, role, address, limits, account status, and relation data
    must not be overwritten by a delayed Telegram intent.
21. A conflicting mobile, account name, invitation, or Telegram ID must fail closed with an
    explicit terminal rejection code. There is no manual-review state or human override path.
22. Web registration continues to require its existing mobile OTP verification.
23. A user registered through Telegram must always pass OTP verification before a later WebApp
    login session is created.
24. A WebApp-login OTP may be delivered through Telegram when the user has a Telegram ID.
25. Telegram and SMS delivery use exactly one OTP value, one request identity, and one expiry
    timeline.
26. When Telegram delivery is available, Telegram is the first delivery channel.
27. A confirmed Telegram delivery schedules automatic SMS fallback 40 seconds later.
28. If the OTP has been successfully verified before the 40-second decision point, no automatic
    SMS fallback is sent.
29. If Telegram delivery fails, times out, or has an ambiguous acknowledgement, the same OTP is
    sent by SMS immediately.
30. If the user has no Telegram ID, the OTP is sent by SMS immediately.
31. SMS fallback does not generate a new OTP and does not reset the OTP TTL.
32. The current accepted OTP TTL is 120 seconds from initial generation. Therefore an automatic
    SMS fallback at 40 seconds normally leaves about 80 seconds of validity.
33. "The user did not log in within 40 seconds" means that OTP verification has not succeeded;
    session-table presence is not the fallback criterion.
34. Only one active login OTP may exist for the same mobile and purpose. A repeated request during
    the active TTL must not replace it with a new code.
35. Current LoginView copy remains unchanged. When delivery method is Telegram, its existing
    30-second countdown becomes 40 seconds; this timer represents automatic SMS fallback delay, not
    OTP TTL. At zero no manual resend button appears: Iran sends the same OTP automatically. The
    backend OTP TTL remains 120 seconds and is not reset.
36. The legacy SMS-resend endpoint may remain temporarily for stale-client compatibility only. It
    must reuse the same active code, must not reset TTL, and successful provider acceptance makes
    the scheduled automatic fallback a no-op.
37. OTPs must not be queued for delayed Telegram delivery after they expire. If connectivity is
    unavailable, use immediate SMS or require a new request later.
38. OTP values must not appear in application logs, audit logs, metrics, sync payloads, or durable
    cross-server notification tables.
39. A Telegram registration intent whose contact proof and local completion both occurred while
    the invitation was naturally valid may be reconciled automatically for at most 24 hours after
    `Invitation.expires_at`. After that boundary it is rejected as expired and requires a new
    invitation/contact proof. Explicit revocation always rejects it, including during the 24-hour
    window.
40. The 24-hour allowance never relaxes identity, uniqueness, role, relation, inactive/deleted-user,
    or Telegram-ID conflict checks; Iran always re-reads current authoritative state.
41. Users created through the new Telegram registration path are not forced to create an admin
    password. Their later WebApp authentication uses the same mandatory OTP flow as other users.
42. Existing legacy `must_change_password` and `/setup-password` behavior remains unchanged for
    existing accounts and is not extended into this roadmap. Its eventual removal requires a
    separate audit because `admin_password_hash` is currently stored but not used for login.
43. All new invitation kinds use `core.trading_settings` as the one authoritative source for
    `invitation_expiry_days`, whose accepted value is two days. The current direct invitation path
    incorrectly reads the separate `core.config` default of one day; this duplicate source must no
    longer be used for invitation lifetime. `Invitation.expires_at` is the authoritative timestamp.
44. Pending customer/accountant relations use the exact same `expires_at` as their Invitation and
    must not calculate an independent lifetime.
45. Changing `invitation_expiry_days` affects only invitations created after the change. If future
    business rules need kind-specific durations, they must use an explicit versioned kind policy,
    not scattered constants.
46. Invitation SMS uses one Iran-authoritative kind/tier policy, independent of whether creation was
    initiated from bot or WebApp. Standard/admin invitations and Tier-1 customer invitations are
    temporarily disabled for SMS; accountant and Tier-2 customer invitations remain SMS-enabled.
47. Per-category flags preserve the temporarily disabled capability rather than deleting it. A
    disabled category makes no SMS.ir call; enabled accountant/Tier-2 paths use their existing
    dedicated SMS.ir template and parameter contract unchanged, with no Telegram link injected.
48. `manual_review` is not part of the product or data model. Every rule violation, identity
    conflict, ambiguous legacy state, explicit revocation, or intent older than the 24-hour grace
    is terminally rejected with a safe reason code. There is no force-create/link, operator retry,
    approval queue, or human override; a new valid invitation/proof is required where applicable.
49. This roadmap adds no special timed deletion, hashing, or scrubbing policy for durable Telegram
    registration intents. They follow the project's current database/backup retention behavior,
    remain foreign-local `no-sync` operational state, and their accepted product result is synced
    through authoritative User, Invitation, and Relation records exactly as current product data is.
50. Every new User completed by either WebApp or Telegram is created with literal
    `home_server="iran"` inside the shared authoritative service, independent of request host,
    `_login_home_server`, or the model's current `foreign` default. Bot use never changes it;
    foreign creates no Web session/JWT/refresh token; later Web login creates an Iran-local session.
    Existing users and Offer/request ownership are unchanged.
51. WebApp and bot map the same bounded terminal reason codes to approved safe Persian messages.
    Expired/late, revoked, sender-contact ownership, invited-mobile mismatch, and identity conflict
    are distinguishable without exposing another account's mobile, account name, or Telegram ID.
52. Invitation has an explicit `kind=standard|accountant|customer|legacy_unknown` but no stored
    status column. Pending/completed/revoked/expired is derived from completion metadata,
    `revoked_at`, and `expires_at`. Pending deletion becomes soft revocation; ambiguous legacy rows
    are `legacy_unknown` and cannot complete registration.
53. Iran reserves normalized mobile and account name globally across every pending invitation kind
    through a local `no-sync` reservation table with unique constraints and advisory locks. Only an
    exact same authorized owner/kind/payload retry returns the existing invitation; every differing
    collision is rejected without disclosing its token/link.
54. User sync is field-authoritative and patch-based, not whole-row last-write-wins. Iran owns
    account/identity/admin fields; foreign owns bot onboarding progress; monotonic fields cannot
    regress; generic User snapshots never merge trade counters; foreign Telegram identity changes
    use signed Iran commands before normal Iran-to-foreign sync.
55. Existing invitation token/short-code link structure remains unchanged; no exchange/session
    redesign is added solely for token privacy. Public invitation responses mask mobile, use
    `Cache-Control: no-store`, and are rate-limited; authenticated admins alone receive full links;
    Nginx/application logs redact raw token, mobile, and Telegram ID.
56. The existing UTC/timezone standard on both servers remains unchanged. This roadmap adds no
    broad timestamp migration or timezone refactor; new fields follow current project conventions,
    and exact expiry/+24h behavior is verified using existing helpers and synchronized server time.
57. Telegram registration uses exactly the current Web registration address rule: minimum 10
    characters and the same Persian validation message, with no new invitation-registration
    maximum or normalization policy. Iran enforces this shared rule for both completion adapters;
    the separate existing profile-address edit contract remains unchanged.
58. Invitation API contract version 2 adds explicit `bot_link`, `web_link`, `web_short_link`,
    per-surface availability, derived state, expiry, and `sms_status=disabled|accepted|failed`.
    Legacy `link`/`short_link` remain temporary aliases; new clients use only explicit fields and
    internal signed schemas reject unknown fields.
59. Internal registration commands reuse existing HMAC headers and add stable UUID `command_id`,
    `idempotency_key`, canonical request hash, strict schema, Iran-only route guard, and an Iran-local
    `no-sync` receipt committed with the mutation. Same ID/payload returns the prior result; same ID
    with different payload rejects. OTP uses only TTL-bounded Redis dedupe.
60. Incomplete Telegram contact/address collection lives only in foreign Redis FSM with TTL no
    later than Invitation expiry. After user confirmation, foreign commits one durable PostgreSQL
    registration intent before clearing FSM; a foreign-only job in the existing background leader
    resumes it across restart. User/Relation activation occurs only after Iran commit and complete
    synced projections are locally visible.
61. OTP SMS fallback remains independent from invitation-SMS category flags. An Iran-only job in
    the existing background leader uses a real async SMS.ir adapter, Redis sorted-set due work, and
    atomic claim. Provider `ambiguous`/timeout is not automatically retried for the same OTP because
    acceptance may already have occurred; restart resumes only unclaimed pending work.
62. Registration side effects reuse the existing `telegram_notification_outbox` with a
    transactionally created unique event and its established foreign delivery/dedupe behavior; no
    second outbox or worker is introduced. Retry/replay creates neither a second logical event nor
    success message, and delivery failure never rolls back committed registration. Staging OTP
    logging is disabled in defaults and deploy-time enforcement; centralized redaction removes raw
    token/OTP/mobile/address/Telegram ID from logs and metrics.
63. This roadmap does not add or change User deletion, recovery, or reinvite behavior in bot or
    WebApp. Existing User lifecycle remains untouched and receives regression coverage only.
    Invitation soft revocation is independent and must not create a User deletion/restore surface.
64. Authoritative User, Invitation, CustomerRelation, and AccountantRelation changes carry an
    increasing `sync_version`; stale/repeated versions do not apply. Bot activation uses a
    projection policy derived from authoritative Invitation kind/tier: Standard/admin/police need no
    Relation, while customer/accountant require their active Relation; Web-only policy still applies.
65. Existing project mechanisms for mixed-version deployment, queue handling, cross-server outage,
    recovery, and rollback remain authoritative and are not redesigned. New additive migrations,
    tables, flags, and contracts integrate into and receive regression coverage against them.
66. Existing reset/fixture/seed, sync registry/parity, backup/restore, purge, migration-health, and
    deploy tooling is extended only where explicit table/schema lists require it. No parallel
    tooling or production intent/receipt purge is added; restore and dependency-order staging reset
    are verified for new local tables.
67. The two new background-leader jobs use existing process/pools with fully async I/O and no DB
    transaction held across HTTP. Registration defaults to batch 10/concurrency 1; OTP fallback to
    concurrency 4; values are configurable, jobs are independently flag-disabled, and staging fails
    if market p95 degrades over 10 percent or market backlog grows persistently.
68. Registration outage handling reuses the project's existing health, backoff, queue, recovery,
    and alert mechanisms without a new incident workflow, runbook, escalation, or admin tool. Intent
    stays foreign-local, no temporary access is granted, reconnect reconciles automatically, and
    revoked or expiry-plus-24-hour cases terminate as already defined.
69. Existing health/metrics/alerts are extended for the two jobs without a new monitoring system or
    dashboard: heartbeat, last error/success, pending count/oldest age, and batch duration. Initial
    alerts are heartbeat over 60s, healthy-connectivity intent pending over 5m, OTP lag over 2s, and
    the existing 10% market-p95 staging gate; all labels are PII-free.
70. This roadmap adds no provider abstraction, vendor change, finance/accounting, SMS cost counter,
    provider-specific alert, threshold, or escalation workflow. Existing SMS.ir/Telegram provider,
    templates, rate limits, and operations remain unchanged except for the already approved async
    OTP delivery and invitation-category behavior; staging verifies functional readiness only.
71. Existing bot welcome/channel-join/onboarding behavior remains unchanged. After complete synced
    registration state is visible, the new path enters the current linked-account/panel flow; the
    current channel join request triggers the two existing tutorial messages in their existing
    order/conditions, and current `خواندم` callbacks/messages are reused without copy or trigger changes.
72. Existing LoginView, contact-sharing pattern, confirmed-contact/address, welcome, channel,
    tutorial, `خواندم`, and completion copy is reused without redesign. Only already approved safe
    terminal rejection and connectivity-pending text is new; cross-path completion routes into the
    existing login/linked-account flows without a new first-wins explanation screen.
73. Staging rollout uses existing deployment with all flags initially off, then validates schema/
    sync/background leader, contract/invitation policy, online Telegram registration, existing
    outage/recovery, automatic Web-login OTP Telegram-to-SMS behavior, four-category invitation SMS,
    combined market load, and owner acceptance in that order. Each feature rolls back by its own
    flag, no role-specific rollout restriction is added, and production remains separately gated.
74. Production behavior remains unchanged until all staging gates and owner-led manual scenarios
    pass and the owner explicitly requests `production deploy`.

## Eligibility Matrix

Direct Telegram registration follows the same centralized bot-access policy used by account
linking, channel membership, and bot market access.

| Account type | Web registration | Telegram registration | Web-login OTP delivery via Telegram |
|---|---:|---:|---:|
| Standard user | Yes | Yes | Yes, after Telegram identity exists |
| Police | Yes | Yes | Yes |
| Middle manager | Yes | Yes | Yes |
| Super admin | Yes | Yes | Yes |
| Tier-1 customer | Yes | Yes | Yes |
| Watch-only role | Yes | No | No |
| Accountant | Yes | No | No |
| Tier-2 customer | Yes | No | No |
| Inactive/deleted account | No new completion | No | No |

Web-only invitation types may still produce a Telegram URL for compatibility during migration,
but the bot must explain that the account type is WebApp-only and present the canonical Iran
WebApp URL. The preferred final UI is not to present Telegram registration as available for an
ineligible invitation. Police, Middle Manager, and Super Admin invitations use the same direct
Telegram eligibility rules as other eligible roles; no additional role-specific OTP, approval, or
registration gate is introduced.

## Non-Goals

This roadmap does not:

- move the Telegram bot to Iran;
- serve the WebApp from foreign;
- sync WebApp session rows or bot FSM state;
- make `user.home_server` represent the user's currently active surface;
- let foreign create a temporary User during an outage;
- add a distributed transaction or two-phase commit between the two PostgreSQL databases;
- guarantee physical handset delivery by Telegram or SMS providers;
- change the existing role, customer-tier, or accountant bot-access policy beyond enabling the
  accepted eligible roles above;
- authorize production deployment.

## Current Implementation Baseline

### Invitation links

`api/routers/invitations.py:build_invitation_links()` already derives these URLs from the same
invitation token:

- `https://t.me/<bot>?start=<INV-token>`
- `<frontend>/register?token=<INV-token>`
- `<frontend>/i/<short-code>`

This is the correct token model. The behavior behind the Telegram URL is incomplete.

### Current Telegram invitation behavior

`bot/handlers/start.py:handle_start_with_token()` currently validates a normal invitation and then
redirects the user to WebApp registration. It does not:

- enter a registration FSM;
- request the sender-owned contact;
- collect an address;
- create or reconcile a user;
- consume the invitation;
- emit a durable invitation-open audit event.

The historical implementation had a Telegram contact/address flow and created `User` directly on
foreign. Its UI mechanics are reusable, but its foreign database write is not safe to restore.

### Current WebApp behavior and copy

The invitation landing page currently describes Telegram as a separate and faster registration
path, although the bot redirects back to Web registration. This copy must be made truthful when
the direct bot flow is enabled.

The foreign bot runtime currently uses a foreign `FRONTEND_URL`. The generated registration URL
can therefore point to a foreign hostname that does not serve the WebApp. A distinct canonical
`PUBLIC_WEBAPP_URL` pointing to Iran must replace this overloaded configuration meaning for all
bot messages and menus.

### Current registration write path

`api/routers/auth.py:register_complete()` currently performs Web registration inline:

- validates the invitation/registration session;
- creates `User` with `telegram_id=None`;
- marks `Invitation.is_used=True`;
- activates accountant/customer relations when applicable;
- creates mandatory membership state;
- commits;
- creates a local WebApp session.

This logic must move into a shared authoritative registration service before a second surface is
enabled.

The current invitation lifetime has two conflicting sources with the same field name:
`core.config.settings.invitation_expiry_days` defaults to one day and is what
`api/routers/invitations.py:create_invitation()` currently reads, while the existing central
`core.trading_settings`/`trading_settings.json` value is two days and is already exposed through
the trading-settings UI. The async shared invitation service must use
`await get_trading_settings_async()` only; this roadmap does not introduce a third setting, use the
sync compatibility accessor in a new async path, or silently change existing stored expiries.

All current standard, customer, and accountant invitation creation call sites invoke an SMS.ir
sender. The approved Standard/admin and Tier-1 disabled policy is therefore an intentional behavior
change, while accountant and Tier-2 retain their existing template contracts.

### Current sync conflict risk

The sync receiver upserts users by integer `id`, while `mobile_number`, `account_name`, and
`telegram_id` also have unique constraints. If Iran and foreign independently create the same
logical user with different partitioned IDs, receiver replay can fail on a natural-key unique
constraint. This is why foreign direct User creation is prohibited.

### Current OTP behavior

The current login OTP flow already has useful foundations:

- one active code per mobile;
- 120-second TTL;
- Telegram-first intent for linked users;
- SMS fallback;
- an SMS-resend endpoint that reuses the active code.

The current Telegram relay is not an adequate delivery acknowledgement. Iran's helper may swallow
a relay error while the auth route marks Telegram delivery successful. The new OTP path must use a
dedicated signed delivery contract with an explicit foreign result.

### Focused baseline test evidence

On 2026-07-10, the focused current-behavior suite below passed `53` tests:

```text
python3 -m unittest \
  tests.test_invitations_router \
  tests.test_auth_router_registration_flows \
  tests.test_auth_router_login_otp_flows \
  tests.test_bot_start_invitation_entry \
  tests.test_bot_start_registration_contact \
  tests.test_bot_start_registration_address \
  tests.test_customer_invite_contract
```

This is a regression baseline, not evidence that the target architecture exists. The run emits
multiple `datetime.utcnow()` deprecation warnings; the owner confirmed the existing two-server UTC
standard remains unchanged in this roadmap, so those warnings are separate technical debt rather
than a migration requirement here. Test-path logs containing raw sample mobile numbers reinforce
`DT-15`. Several tests encode current redirect/manual-resend behavior and must be deliberately
updated rather than treated as immutable requirements.

## Data Ownership And Sync Policy

| State | Authority / location | Sync policy |
|---|---|---|
| Invitation product state | Iran authoritative | Sync to foreign |
| Pending invitation identity reservation | Iran-local concurrency/uniqueness state | No sync |
| User/account product state | Iran final writer with field-level policy | Sync to foreign |
| New User `home_server` | Always `iran` for both completion surfaces | Sync as account-origin compatibility; never mutate on bot use |
| `telegram_id`, username, eligible profile fields | Iran committed account state after command | Sync to foreign |
| Bot onboarding required/completed step and completion time | Foreign bot runtime | Patch sync to Iran with monotonic merge |
| `last_seen_at` | Both surfaces may observe activity | Merge by maximum trusted timestamp |
| Trade/customer counters | Existing domain authority | Never overwrite through a generic full-User snapshot |
| WebApp sessions and refresh-token state | Iran local runtime | No sync |
| Bot FSM while collecting contact/address | Foreign Redis | No sync |
| Completed Telegram registration intent | Foreign durable local command state | No direct User sync |
| Registration command receipt/idempotency | Iran durable local command state | No sync required |
| Login OTP value and delivery state | Iran Redis | No sync |
| OTP Telegram send dedupe receipt | Foreign short-lived Redis/local receipt | No product sync |
| Audit summaries | Each executing surface, redacted | Ship through approved observability path |

The registration intent is not product truth. It is evidence that the foreign Telegram surface
completed its local proof/collection steps and is asking Iran to make an authoritative decision.

User update events contain only fields actually changed in the transaction. Receiver policy is
source-aware: foreign-origin User patches may contain only bot onboarding fields and approved
`last_seen_at`; Iran-origin patches may contain account identity, address, role/status, limits,
access, Telegram identity, and `home_server`. Onboarding steps merge by `max`, completion cannot
regress, and `last_seen_at` merges by maximum timestamp. Unauthorized fields are dropped with a
redacted security event. User insert is accepted only from Iran. Trade/customer counters remain
under their existing domain event/recalculation policy and are excluded from generic User patches.

Authoritative User, Invitation, CustomerRelation, and AccountantRelation rows/events carry a
monotonic `sync_version`. Receiver applies only a newer version and ignores duplicate/stale events.
Cross-table arrival order is never assumed. Activation requirements are derived from authoritative
Invitation kind/tier: Standard/admin/police require User, completed Invitation, Telegram identity,
and access policy only; customer/accountant additionally require their active Relation. Accountant
and Tier-2 customer remain Web-only regardless of arrival order.

## Target Data Model

### Invitation completion metadata

Extend `Invitation` conservatively while retaining `is_used` for compatibility:

- `kind`: non-null enum/string: `standard`, `accountant`, `customer`, or `legacy_unknown`;
- `registered_user_id`: nullable FK to `users.id`;
- `completed_at`: nullable timestamp following the existing project UTC convention;
- `completed_via`: nullable enum/string: `web` or `telegram`;
- `revoked_at`: nullable timestamp so explicit revocation is distinguishable from natural expiry;
- `updated_at`: recency timestamp following the existing project UTC convention.

Do not add a stored status column. Runtime status is derived in this order:

1. `completed` when complete metadata and compatibility `is_used=True` agree;
2. `revoked` when `revoked_at` exists and completion does not;
3. `expired` when current trusted time is after `expires_at`;
4. otherwise `pending`.

New code sets `is_used=True`, `registered_user_id`, `completed_at`, and `completed_via` atomically.
Database constraints reject partial completion metadata and simultaneous completed/revoked state.
Pending deletion records `revoked_at` instead of hard-deleting the row.

Backfill `kind` from exact relation evidence first and token prefix second. Any conflict or ambiguity
becomes `legacy_unknown`; it cannot complete registration and returns safe new-invitation guidance.

For every new invitation, the authoritative service snapshots the central two-day policy into
`Invitation.expires_at`. A customer/accountant relation receives that exact timestamp. Runtime
setting changes never recalculate existing rows. The 24-hour delayed-intent boundary is calculated
only from the stored Invitation timestamp.

Invitation opening should be an audit event rather than a single `opened_at` column because the
same invitation may be opened multiple times on both surfaces.

### Iran invitation identity reservation

Add an Iran-local `invitation_identity_reservations` table and register it explicitly as `no-sync`:

- unique `invitation_id` FK;
- unique normalized mobile;
- unique normalized account name;
- created/updated timestamps.

Every invitation-create adapter uses one Iran transaction. It normalizes both natural keys,
acquires deterministic PostgreSQL advisory locks for them, checks existing User and reservation
state, then creates Invitation, optional Relation, and reservation together. Completion,
revocation, or expiry releases the reservation transactionally. Expiry sweeping must use the
stored Invitation timestamp.

An exact retry returns an existing invitation only when authorized creator/owner, kind, role,
normalized keys, relation identity, and business payload all match. Any differing collision returns
a safe conflict and never exposes another invitation token or link. Owner-scoped relation display
name/management-name constraints remain independent.

### Foreign Telegram registration intent

Add a foreign-local `telegram_registration_intents` table and register it explicitly as `no-sync`.
Minimum fields:

- UUID `id` / command id;
- stable `idempotency_key`;
- invitation token or an approved protected representation sufficient for forwarding;
- normalized invited mobile;
- Telegram ID;
- Telegram username/full name snapshot;
- address;
- `contact_verified_at`;
- `completed_at` for the local bot flow;
- `invitation_expires_at_snapshot`;
- status;
- retry count and next retry time;
- last safe error code;
- authoritative result user id when known;
- created/updated timestamps.

Statuses:

```text
collecting
ready
forwarding
retry_wait
reconciled_created
reconciled_linked_existing
reconciled_already_linked
rejected
expired
```

Raw mobile, address, token, Telegram ID, and names are sensitive. Logs expose only masked or hashed
summaries.

### Iran command receipt

Add an Iran-local idempotency receipt, either as a dedicated
`telegram_registration_command_receipts` table or an approved generic internal-command receipt.
It must record:

- unique intent/command id;
- unique idempotency key;
- final outcome code;
- authoritative user id when applicable;
- invitation token hash/reference;
- source server;
- first-received and completed timestamps.

The receipt ensures that a lost HTTP response and later retry cannot create or link twice.
It also stores a canonical request hash. A retry with the same command/idempotency identity and hash
returns the committed prior result; reuse with a different hash is terminally rejected. Receipt
insert/finalization and User/Invitation/Relation mutation share one transaction. Each transport
retry uses a fresh signed timestamp while retaining the logical command ID.

### OTP delivery state

OTP values stay in Iran Redis with the existing bounded TTL. During the stale-client compatibility
window, the existing `otp:{mobile}` key remains the single code-of-record. Introduce an OTP request
identity and structured state that references that same key/value and is sufficient for automatic
fallback; it must not create a second code store:

```text
otp_request_id
purpose=web_login
mobile
otp_code or protected recoverable value
telegram_id
status=pending|consumed|expired
created_at
expires_at
telegram_delivery_status
telegram_sent_at
sms_fallback_at
sms_delivery_status
sms_sent_at
```

Use a Redis sorted set keyed by `sms_fallback_at` plus an atomic claim/lock for due work. Do not
use a process-local sleep as the source of truth. OTP generation, verification, legacy resend,
scheduled claim, SMS outcome, and cancellation must update/read the same structured state through
one atomic Redis contract. The exact legacy-resend-versus-due-claim race is part of the mandatory
concurrency matrix.

## Shared Authoritative Registration Service

Create a service such as `complete_invitation_registration()` and require both Web and internal
Telegram command adapters to use it.

Inputs:

- invitation token;
- source surface: `webapp` or `telegram_bot`;
- idempotency/command id;
- identity proof type;
- normalized mobile;
- address;
- Telegram identity fields when source is Telegram;
- proof and local-completion timestamps;
- optional Web registration-session context.

Transaction contract:

1. Start one Iran PostgreSQL transaction.
2. Lock the invitation row with `SELECT ... FOR UPDATE`.
3. Load any existing user by `registered_user_id`, mobile, account name, and Telegram ID.
4. Validate invitation existence, explicit revocation, expiry/proof timing, role, relation state,
   account status, and command idempotency.
5. Apply the reconciliation decision matrix below.
6. Create or update mandatory product projections in the same transaction where required.
7. Set invitation completion metadata only when this transaction owns completion.
8. Insert/finalize the command receipt.
9. Commit User, Invitation, relation changes, receipt, and durable sync/outbox recording together.
10. Run surface notifications and other non-authoritative side effects only after commit.

The Web adapter may create a Web session after authoritative registration succeeds. The Telegram
adapter must not create a Web session or JWT.

## Reconciliation Decision Matrix

Iran must make one of these explicit decisions. There is no generic "upsert whatever foreign
sent" path.

| Current Iran state | Required outcome |
|---|---|
| No matching User; invitation pending, not revoked, proof completed while valid | Create one User with Telegram identity; consume invitation |
| Matching User created by Web; invitation points to that User; `telegram_id` is null | Preserve Web fields and link verified Telegram ID |
| Matching User already has the same Telegram ID | Return idempotent `already_linked` success |
| Matching User has a different Telegram ID | Reject `telegram_account_conflict` |
| Telegram ID belongs to another User | Reject `telegram_id_already_used` |
| Mobile belongs to another account/invitation | Reject terminally as `mobile_conflict` |
| Account name belongs to another mobile | Reject terminally as `account_name_conflict` |
| Invitation explicitly revoked | Reject regardless of delayed intent |
| Invitation naturally expired before local Telegram proof completed | Reject as expired |
| Intent completed while invitation was valid, arrives no later than 24 hours after natural expiry, no conflict | Reconcile automatically after a fresh full-state read; never bypass explicit revocation |
| Validly completed intent arrives more than 24 hours after natural expiry | Reject as expired; require a new invitation/contact proof |
| Invitation used but `registered_user_id` is missing on a legacy row | Resolve only through exact mobile/account evidence; otherwise reject `legacy_state_ambiguous` |
| Invitation says completed but authoritative User is missing | Stop and alert; do not recreate blindly |
| Existing User is inactive or deleted | Reject and require an explicit account lifecycle action |

When linking an existing Web-created User:

- preserve authoritative Web address and profile fields;
- do not downgrade or overwrite role/status/limits;
- set Telegram fields only after all identity checks pass;
- retain `completed_via=web` on the invitation;
- record a separate `telegram_registration_intent.linked_existing_user` audit event.

## Online Telegram Registration Flow

1. User opens the Telegram `INV-...` deep link and presses Start.
2. Foreign looks up the synced invitation.
3. If the invitation is momentarily missing, use a bounded sync grace similar to account-link
   token grace before declaring it invalid.
4. Validate current local shape and bot eligibility without treating local state as final
   authority.
5. Display invited account name, role, and masked mobile.
6. Request sender-owned contact.
7. Reject a forwarded contact, manually typed number, or mismatched mobile.
8. Collect an address and show a confirmation summary.
9. Persist a ready intent before forwarding.
10. Forward the signed idempotent command to Iran.
11. Iran runs authoritative reconciliation.
12. On `created`, `linked_existing`, or `already_linked`, foreign waits for the committed User and
    Telegram fields to become visible through normal sync.
13. Enable onboarding and the bot panel only after local synced state confirms access.
14. Preserve intent/result evidence for audit.

No OTP is requested in this flow.

## Outage And Recovery Flow

### While Iran is unreachable

Foreign may complete contact/address collection and persist a `ready` intent. It must not:

- insert a User;
- mark the synced Invitation used as final truth;
- grant market, channel, admin, or full panel access;
- tell the user registration is final.

The user-facing terminal message for this temporary state must clearly say that information was
received and final registration is waiting for server connectivity.

### Existing background-leader reconciliation job

A foreign-only registration-intent job registered in the existing `main.py` background leader
retries ready/retryable intents with:

- stable command id and idempotency key;
- bounded exponential backoff and jitter;
- TLS verification;
- HMAC/API-key/timestamp/source-server headers;
- safe timeout classification;
- no payload values in logs.

### After reconnection

The job does not replay a User payload. It re-submits the registration intent for a fresh Iran
decision. Iran re-reads current truth and may:

- create a User;
- link the intent to a Web-created User;
- return already linked;
- reject;

### Lost response after Iran commit

If Iran commits and the response is lost, retry returns the command receipt's existing result.
Foreign must then wait for sync and must not submit a new logical command.

## Web Registration And Cross-Path Convergence

The Web path keeps the current user experience:

1. Validate invitation.
2. Send and verify Web registration OTP.
3. Collect address.
4. Call the shared authoritative registration service.
5. Create a local Iran Web session.
6. Offer optional Telegram connection for eligible accounts.

Cross-path behavior:

- If Web wins first and the original Telegram invitation is opened later, the bot must offer
  sender-owned contact verification and link the same User rather than reporting a generic invalid
  invitation.
- If Telegram wins first and the Web link is opened later, the WebApp must explain that
  registration is complete and route to mobile OTP login rather than attempting another User
  creation.
- If both final requests race, Iran's invitation row lock and idempotency receipt determine one
  result.

## Synchronized Web Login OTP State Machine

### Request and first delivery

1. User requests Web login OTP from Iran.
2. Iran resolves the existing User and enforces account/session authority checks.
3. Iran creates one five-digit OTP and one `otp_request_id` with a 120-second TTL.
4. If no Telegram ID exists, send the code by SMS immediately.
5. If a Telegram ID exists, send a dedicated signed delivery request to foreign.
6. Foreign sends the message through the central Telegram gateway and returns an explicit result.
7. A successful foreign acknowledgement records `telegram_sent_at` and schedules SMS fallback for
   `telegram_sent_at + 40 seconds`.
8. A failed, timed-out, malformed, or ambiguous foreign result triggers immediate SMS of the same
   code.

A separate health probe must not be the delivery decision. The actual send request and its
acknowledgement are the connectivity test.

### Verification and cancellation

1. The existing verify endpoint checks the one active code.
2. On success, atomically mark the OTP request consumed before returning authentication success.
3. Remove or invalidate the sorted-set fallback member.
4. Any worker that later observes the member must no-op because status is no longer pending.
5. The same OTP cannot be replayed after successful verification.

### Automatic SMS fallback

At the 40-second decision point, an Iran-only worker atomically claims due fallback work and checks:

- OTP request still exists;
- status is `pending`;
- TTL is positive;
- SMS has not already been accepted;
- no other worker owns the fallback claim.

If all checks pass, send the same code through SMS.ir and record the provider acceptance result.

The decision is based on state at the atomic claim point. A verification racing immediately after
that point may coincide with an SMS already handed to the provider; this is acceptable because it
is the same code and the fallback condition was true when claimed.

### Legacy SMS-resend compatibility

The target UI has no manual SMS action. During a bounded compatibility window, an old client may
still call the legacy resend endpoint. That endpoint:

- reads the same active OTP and never generates a new code;
- does not extend expiry or create a second countdown;
- records successful provider acceptance in the same OTP request state;
- makes the scheduled fallback a no-op;
- remains rate-limited and is removed only after stale-client usage reaches the agreed threshold.

### Restart and outage behavior

- Do not implement the 40-second delay with process-local `asyncio.sleep()`.
- Redis state and the due-work sorted set must survive API/background-job restart according to staging and
  production Redis persistence policy.
- If Telegram delivery cannot be acknowledged, immediate SMS is the safe fallback.
- Do not deliver a stale Telegram OTP after cross-server connectivity returns.
- If Iran itself restarts, the worker resumes due, unconsumed, unexpired fallback work.

## Internal Transport Contracts

Add narrow internal endpoints rather than reusing a generic notification body.

### Telegram registration reconciliation

Recommended endpoint:

```text
POST /api/auth/internal/telegram-registration/reconcile
```

Required controls:

- callable only on Iran;
- source must be foreign;
- verified TLS transport;
- `X-API-Key`, `X-Timestamp`, `X-Signature`, `X-Source-Server`;
- canonical JSON body signing;
- timestamp replay window;
- command id and idempotency receipt;
- strict schema and unknown-field rejection;
- no intermediary cache, body rewrite, or header stripping;
- redacted structured audit.

### WebApp login OTP delivery through Telegram

Recommended endpoint on foreign:

```text
POST /api/auth/internal/telegram-otp/deliver
```

Required controls:

- callable only on foreign;
- source must be Iran;
- same signed transport requirements;
- destination Telegram ID must come from authoritative User state, never a browser-provided chat id;
- short request deadline;
- short-lived foreign dedupe receipt keyed by `otp_request_id`;
- explicit response taxonomy: `sent`, `unreachable`, `rate_limited`, `provider_error`, `invalid`,
  `duplicate_sent`;
- no durable OTP text in notification outbox/change log;
- no OTP value in logs or response body.

An ambiguous timeout after Telegram accepted a message may cause immediate SMS as well. This is an
acceptable at-least-once cross-channel outcome because both channels carry the same OTP.

## Invitation Creation Authority

Both admin surfaces may initiate an invitation, but Iran must create the authoritative Invitation.

- Web admin continues to call the Iran invitation service locally.
- Bot admin must stop calling `create_invitation()` against the foreign DB directly.
- Bot admin sends a signed idempotent invitation-create command to Iran.
- Iran creates the token, short code, role, mobile, expiry, and audit record.
- Iran returns both canonical URLs and syncs Invitation to foreign.
- Iran returns both canonical URLs to the admin for manual sharing.
- One central policy function resolves SMS from Invitation kind, customer tier, and category flag;
  source surface never changes the result.
- Standard/admin and Tier-1 categories currently return structured `disabled` without contacting
  SMS.ir; accountant and Tier-2 categories invoke their existing dedicated templates.
- Existing template/parameter contracts remain unchanged and no Telegram link is injected. A sent
  result is reported only after SMS.ir acceptance; disabled, failed, and accepted are distinct.

This closes the same dual-writer gap before end-user registration starts.

## User Experience Requirements

### Admin invitation result

Show two accurately labeled links for eligible roles:

- `ثبت نام از طریق تلگرام`
- `ثبت نام از طریق وب اپ`

For Web-only roles, do not claim Telegram registration is available.

### Telegram registration

The bot must show concise Persian states for:

- invitation valid;
- contact required;
- contact mismatch;
- address required;
- confirmation;
- finalizing with Iran;
- waiting for connectivity;
- registration complete;
- existing Web account linked;
- invitation expired/revoked;
- terminal rejection with a safe reason and new-invitation guidance when applicable.

### Approved terminal rejection copy

Both adapters consume a bounded machine-readable reason code from the shared authoritative service
and render the same approved meaning:

| Safe reason code | Approved Persian message |
|---|---|
| `invitation_expired` | `مهلت ثبت‌نام پایان یافته است. لطفاً دعوت‌نامه جدید دریافت کنید.` |
| `invitation_revoked` | `این دعوت‌نامه دیگر معتبر نیست.` |
| `contact_not_owned` | `شماره تماس باید مستقیماً از حساب تلگرام خودتان ارسال شود.` |
| `contact_mobile_mismatch` | `شماره ارسال‌شده با شماره ثبت‌شده در دعوت‌نامه مطابقت ندارد.` |
| `identity_conflict` | `امکان تکمیل ثبت‌نام با این دعوت‌نامه وجود ندارد. لطفاً با دعوت‌کننده تماس بگیرید.` |
| `legacy_state_ambiguous` | `امکان تکمیل ثبت‌نام با این دعوت‌نامه وجود ندارد. لطفاً دعوت‌نامه جدید دریافت کنید.` |

`identity_conflict` deliberately does not reveal whether mobile, account name, or Telegram ID is
owned by another account. Logs and metrics retain only the bounded safe reason code and opaque
request identifiers.

### Web invitation landing

The current "Telegram is faster" copy becomes valid only after the direct bot flow is enabled.
Before feature enablement, feature flags must keep copy aligned with actual behavior.

### Web login OTP

When Telegram first delivery succeeds, show:

- the existing LoginView copy unchanged;
- the existing Telegram-method countdown changed from 30 to 40 seconds.

The 40-second UI timer represents the automatic SMS fallback delay, not OTP expiry. At zero, do not
show the current manual `ارسال مجدد کد` action; the Iran background job sends the same code. OTP
remains valid only until its original 120-second backend TTL.

When immediate SMS is used, do not claim Telegram delivery succeeded.

## Security Requirements

1. Use cryptographically secure OTP and token generation.
2. Preserve constant-time OTP comparison and bounded verification failures.
3. Keep one active OTP per mobile/purpose.
4. Verify sender-owned Telegram contacts.
5. Reject Telegram-ID reuse across users.
6. Reject invitation token replay after completion/revocation.
7. Lock authoritative invitation completion rows.
8. Use command idempotency for every internal retry.
9. Never trust role, mobile, account name, destination chat id, or completion status solely from
   the browser or foreign payload; Iran re-derives/validates authoritative values.
10. Never log raw invitation tokens, OTP values, mobile numbers, addresses, Telegram IDs, signed
    bodies, API keys, or provider credentials.
11. Do not persist OTP text in synced database tables or durable observability sinks.
12. Apply rate limits to OTP request, verify, Telegram delivery, legacy SMS-resend compatibility,
    registration intents, and reconciliation retries.
13. Fail closed when Redis locks/state, command receipts, or authoritative DB transaction behavior
    is unavailable.

## Observability And Audit

Required registration events:

```text
invitation.opened
telegram_registration.contact_verified
telegram_registration.intent_ready
telegram_registration.forward_attempt
telegram_registration.reconciled_created
telegram_registration.reconciled_linked_existing
telegram_registration.reconciled_already_linked
telegram_registration.rejected
```

Required OTP events:

```text
otp.requested
otp.telegram_delivery_attempt
otp.telegram_delivery_result
otp.sms_fallback_scheduled
otp.sms_fallback_claimed
otp.sms_delivery_result
otp.verified
otp.expired
```

Metrics:

- registration completions by surface and outcome;
- pending intent count and oldest age;
- reconciliation retry count and latency;
- terminal rejection count by bounded safe reason code;
- time from Iran commit to foreign User visibility;
- OTP request count;
- Telegram delivery success/failure/ambiguous rate;
- automatic SMS fallback count;
- OTP verification before/after SMS fallback;
- fallback scheduling delay from the 40-second target;

No metric label may contain mobile, Telegram ID, user name, token, OTP, or address.

## Feature Flags And Configuration

Introduce explicit settings with conservative defaults:

```text
TELEGRAM_DIRECT_REGISTRATION_ENABLED=0
TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED=0
TELEGRAM_LOGIN_OTP_ENABLED=0
OTP_SMS_AUTO_FALLBACK_ENABLED=0
OTP_SMS_AUTO_FALLBACK_SECONDS=40
OTP_TTL_SECONDS=120
TELEGRAM_REGISTRATION_POST_EXPIRY_GRACE_SECONDS=86400
TELEGRAM_REGISTRATION_JOB_BATCH_SIZE=10
TELEGRAM_REGISTRATION_JOB_CONCURRENCY=1
OTP_SMS_FALLBACK_JOB_CONCURRENCY=4
INVITATION_SMS_STANDARD_ENABLED=0
INVITATION_SMS_CUSTOMER_TIER1_ENABLED=0
INVITATION_SMS_ACCOUNTANT_ENABLED=1
INVITATION_SMS_CUSTOMER_TIER2_ENABLED=1
PUBLIC_WEBAPP_URL=https://<iran-webapp-host>
```

Rules:

- staging enables one feature stage at a time;
- production defaults remain disabled through all staging work;
- `PUBLIC_WEBAPP_URL` is distinct from local API/peer/internal transport URLs;
- startup and link generation fail closed when `PUBLIC_WEBAPP_URL` is empty, malformed, non-HTTPS
  outside local test, or resolves to a configured foreign Web/API hostname;
- internal Iran/foreign endpoint base URLs come from environment configuration;
- the 24-hour reconciliation grace is server configuration with a locked production default of
  `86400` seconds, not a browser- or foreign-supplied value;
- flags disable new entry paths without deleting intents, receipts, completion metadata, or users.

## Engineering Challenge Registry

This registry is the authoritative list of known challenges. Stage checklists below reference these
stable IDs. `Locked` means the product/architecture outcome is fixed but implementation and proof
are still required. `Open` means the stage owner must compare viable options and record a decision
before implementation. A challenge is closed only when its decision record and stated evidence both
exist.

### Direct technical challenges

| ID | Challenge and failure mode | Decision status and recommended direction | Closure evidence |
|---|---|---|---|
| DT-01 | `Invitation` has only `is_used`/`expires_at`, while `INV-*`, `ACCT-*`, and `CUST-*` share one table and infer type from prefixes. | Closed by owner decision in Stage 1: add explicit kind including `legacy_unknown`, completion/revocation metadata, derived status with no status column, soft revocation, atomic completion constraints, and conservative relation-first backfill. | Migration round trip, legacy-row matrix, constraint tests, and soft-revocation audit. |
| DT-02 | Generic active-invitation lookup is mobile-only and can select another owner, role, relation, or invitation kind; specialized paths can create cross-type overlap. | Closed by owner decision in Stage 1: one Iran-local global reservation across normalized mobile/account name for every kind; exact same authorized owner/kind/payload retry may return existing, while any differing collision rejects without link disclosure. | Call-site inventory plus cross-kind/owner/payload tests. |
| DT-03 | Web completion has no invitation row lock, and relation/expiry helpers commit internally. Concurrent Web/Telegram completion can produce a unique-constraint 500 or partial business state. | Locked outcome: one Iran transaction, row lock, flush without nested commits, deterministic conflict result. | Concurrency tests prove one User, one completion, and full rollback. |
| DT-04 | Bot-admin invitation creation writes foreign directly; Web-to-Telegram link-token consumption also writes `users.telegram_id` on foreign. Either path breaks the single-final-writer invariant. | Locked outcome: migrate every identity/invitation writer to signed Iran authority before enabling direct registration. | Writer inventory has no unapproved foreign mutation; server-guard tests pass. |
| DT-05 | `users` cannot simply become Iran-only: bot onboarding legitimately changes on foreign, while current full-row events can overwrite newer Iran account/profile state. | Closed by owner decision in Stage 1: patch-only source-aware events; Iran owns account/identity/admin fields, foreign owns monotonic bot onboarding, `last_seen_at=max`, counters stay out of generic User patches, and inserts/Telegram identity finalize on Iran. | Per-field contract, unauthorized-field drop audit, stale-event/reorder tests, and counter regressions. |
| DT-06 | User and customer/accountant Relation projections may arrive in separate batches; Tier-1 may temporarily look like Standard. | Closed by owner decision: projection gate is derived from authoritative Invitation kind/tier. Standard/admin/police do not wait for Relation; customer/accountant require active Relation; Web-only policy remains; no incomplete state grants access. | Per-kind/tier delayed/out-of-order projection tests. |
| DT-07 | Current Telegram registration FSM is a redirect; restored historical code needs safe transient/durable state boundaries and restart behavior. | Closed by owner decision in Stage 1: incomplete collection stays in foreign Redis through Invitation expiry; confirmed intent commits to foreign PostgreSQL before FSM clear; existing foreign background leader resumes it; no foreign User/access; activation waits for complete synced projections. | Contact/address, Redis-loss, atomic-handoff, process/job-restart, and projection-wait tests. |
| DT-08 | Public invitation lookup exposes full mobile/raw token, while URLs and access logs can retain them; token alone still cannot complete Web/Telegram identity proof. | Closed by owner decision in Stage 1: retain existing token/short-code links and avoid exchange redesign; mask public mobile, admin-gate full links, add no-store/rate limits, and redact Nginx/application token/mobile/Telegram-ID logs. | Public/admin API tests, rate-limit/no-store assertions, and automated log scans. |
| DT-09 | OTP generation is not yet cryptographically strong everywhere, and Iran can mark Telegram success after a swallowed relay error. | Locked outcome: secure randomness, one request state, and actual signed Telegram send acknowledgement as the connectivity decision. | Deterministic state-machine tests plus failure/timeout tests. |
| DT-10 | Synchronous SMS provider calls inside async code can block the event loop, while process-local sleep is not restart-safe and provider timeout can hide acceptance. | Closed by owner decision: OTP fallback stays; use an Iran job in the existing background leader, real async SMS.ir adapter, Redis sorted set, atomic claim, bounded timeout, and no automatic same-OTP retry after ambiguous provider outcome. Invitation-SMS flags are independent. | Event-loop benchmark, 40s jitter, explicit/ambiguous provider, restart, and duplicate-instance tests. |
| DT-11 | Invitation APIs overload `link`/`short_link` and lack role-aware availability, derived state, and truthful SMS outcome. | Closed by owner decision in Stage 1: contract v2 adds explicit bot/Web/short links, availability flags, derived state, expiry, and bounded SMS status; old aliases remain temporarily while new clients use explicit fields; internal schemas forbid extras. | Old/new backend/frontend contract matrix and alias-usage telemetry. |
| DT-12 | Invitation deletion can erase history; code also contains mixed datetime representations. | Closed by owner decisions: Invitation uses soft revocation; existing User lifecycle is unchanged; existing two-server UTC/timezone conventions remain unchanged with no broad migration/refactor; current helpers receive exact boundary tests. | Invitation audit, no-User-lifecycle-change regression, and current-helper boundary tests. |
| DT-13 | The database has no explicit active-invitation uniqueness invariant; a time-dependent partial unique index cannot safely depend on current time. | Closed by owner decision in Stage 1: Iran-local `no-sync` reservation table with unique mobile/account/invitation constraints, deterministic advisory locks, and transactional create/release. | Migration/registry proof and high-concurrency create/expiry/revoke/complete tests. |
| DT-14 | Internal commands need strict schemas, canonical signing, replay protection, source/server guards, and atomic idempotency receipts. | Closed by owner decision in Stage 1: reuse existing HMAC headers; stable UUID command/idempotency identity plus canonical request hash; Iran-local no-sync receipt in the mutation transaction; same payload returns prior result, changed payload rejects; OTP uses TTL Redis dedupe only. | Security-negative, concurrent/lost-response replay, receipt-rollback, and route-surface tests. |
| DT-15 | Post-commit notifications can duplicate on retry; staging/failure paths can log OTP/mobile/Telegram identity. | Closed by owner decision: reuse `telegram_notification_outbox` with a unique transactional event, its foreign worker/dedupe behavior, first-terminal-transition success message, no registration rollback on delivery failure, deploy-enforced staging OTP logging disabled, and centralized sensitive-value redaction. | Retry/replay/outbox tests, staging redeploy assertion, and zero-match sensitive log scan. |
| DT-16 | Web registration currently enforces minimum 10 characters in frontend while registration backend and old bot paths differ. | Closed by owner decision in Stage 1: copy the Web registration rule exactly to bot and enforce the same minimum-10 rule/message authoritatively on Iran for both adapters; add no new max/normalization policy and leave profile editing unchanged. | Shared registration validator tests plus Web/bot exact-message parity. |
| DT-17 | Invitation call sites invoke SMS inconsistently and a global off switch would incorrectly disable Web-only accountant/Tier-2 delivery. | Closed by refined owner decision: central kind/tier policy with independent flags; Standard/admin and Tier-1 false temporarily, accountant and Tier-2 true; source surface is irrelevant; disabled means no provider call; existing enabled templates/contracts stay unchanged with no Telegram link. | Four-category x two-surface policy tests, zero-call disabled tests, and unchanged-template provider tests. |
| DT-18 | A delayed intent can be valid at proof time but arrive after expiry; clock drift and explicit revocation complicate the boundary. | Locked policy: auto-reconcile only through `expires_at + 24h`, require proof completed while valid, re-read all state, and always reject revocation. | Boundary tests at expiry, +24h, clock tolerance, and revocation races. |
| DT-19 | A manual-review state would create an operator override surface and unresolved lifecycle. | Closed by owner decision in Stage 0: remove manual review entirely. Every invalid/conflicting/ambiguous/late case is terminally rejected with a safe code; no force mutation, operator retry, approval queue, or override exists. | State-enum/schema tests, forbidden-surface tests, and terminal-rejection matrix. |

### Direct non-technical challenges

| ID | Challenge and impact | Decision status and recommended direction | Closure evidence |
|---|---|---|---|
| DN-01 | The matrix lists Super Admin Telegram registration, while current invitation UIs may not expose every manager invitation path. | Closed by owner decision in Stage 0: whenever the existing product permits issuing a valid invitation, direct Telegram registration is eligible; no additional high-privilege approval gate is added. | Role matrix and invitation-path contract tests. |
| DN-02 | Telegram-first registration for Police, Middle Manager, or Super Admin skips OTP. | Closed by owner risk decision in Stage 0: no role-specific OTP or extra approval is added. The universal controls remain a valid authoritative invitation, sender-owned Telegram contact, exact normalized mobile match, identity-conflict checks, and audit. | Identity/role matrix, inviter-permission regression, and abuse/revocation tests. |
| DN-03 | `core.config` currently gives direct invitations one day while `core.trading_settings`/`trading_settings.json` gives the central product setting two days; customer/accountant constants can add further drift. | Closed by owner decision in Stage 0: `core.trading_settings` is the sole authoritative setting at its accepted two-day value; all creation paths snapshot it into `Invitation.expires_at`, and related pending records copy that timestamp exactly. Setting changes affect only future invitations. | Source-of-truth guard, effective-value assertion, creation/relation timestamp equality, setting-change, and expiry-boundary tests. |
| DN-04 | One invitation has two paths and first valid completion wins; UI must not behave as two independent registrations. | Closed by owner decision: add no explanatory first-wins screen; after one path completes, the other routes into the existing Web login or linked-account/panel behavior. Existing copy stays unless a new safe rejection/pending state has no current equivalent. | Cross-path UI routing and manual acceptance. |
| DN-05 | Invitation SMS policy differs by invitation eligibility/type and is temporarily disabled for some categories. | Closed by refined owner decision: Standard/admin and Tier-1 use manual admin sharing with SMS flag false; accountant and Tier-2 are Web-only and keep current invitation SMS through existing templates; no capability is removed. | Category/source matrix, admin workflow acceptance, and real enabled-template evidence. |
| DN-06 | Manual-review ownership, SLA, escalation, and user communication would be required only if such a workflow existed. | Closed as not applicable by owner decision in Stage 0: no manual-review workflow exists. The owner approved bounded rejection/new-invitation copy; rejected users restart only with a new valid invitation when allowed. | Copy-contract tests and proof that no review queue/action is exposed. |
| DN-07 | Contact, address, Telegram identity, intent snapshots, and receipts needed an explicit retention/sync decision. | Closed by owner decision in Stage 0: introduce no new timed purge/scrubbing policy; durable intents follow current database/backup retention, remain foreign-local `no-sync`, and only accepted authoritative product records sync normally. Existing no-secret-log and access-control requirements still apply. | No-sync registry test, accepted-product sync test, backup behavior check, and log-redaction test. |
| DN-08 | Automatic OTP fallback may change SMS volume. | Closed by owner decision: preserve approved fallback semantics but add no SMS cost counter, budget system, provider alert, or finance workflow in this roadmap. | Functional OTP fallback and existing rate-limit regressions only. |

### Indirect technical challenges

| ID | Affected area and failure mode | Decision status and recommended direction | Closure evidence |
|---|---|---|---|
| IT-01 | Existing Web-to-Telegram link-token flow can still mutate Telegram identity on foreign and race the new registration command. | Locked outcome: route linking through the same Iran-authoritative identity service and conflict rules. | Both old/new link-flow integration tests. |
| IT-02 | User deletion/reinvite/recovery was considered as a possible indirect impact. | Closed as out of scope by owner decision: add no bot deletion, restore, reinvite, or User lifecycle behavior; preserve all current behavior. Invitation soft revocation remains separate. | Existing lifecycle regression suite and route-surface proof that no new bot action exists. |
| IT-03 | Relation activation affects customer pricing, trading limits, accountant access, and market behavior beyond registration. Partial or duplicate activation has financial impact. | Locked outcome: preserve current relation semantics in the authoritative transaction and gate access until projection completeness. | Domain regression suite and exact relation-state assertions. |
| IT-04 | Telegram-created users later log in on Iran; the model default is `foreign`, and request-derived `_login_home_server` can make a signed foreign-origin command choose the wrong owner. | Closed by owner decision in Stage 0: the shared Iran service writes literal `home_server=iran` for every new User from either surface; request context and model default are not authoritative; bot use does not mutate it; foreign creates no Web session/token. Existing users and Offer/request home fields are out of scope. | Service-mechanism assertion, registration, OTP login, refresh/logout/reset-session, no-foreign-session, and Offer-ownership regressions. |
| IT-05 | The legacy manager password setup stores `admin_password_hash`, but current authentication does not verify that hash; extending it would add a blocking step without an effective authentication factor. | Closed by owner decision in Stage 0: new Telegram registrations keep `must_change_password=False`; later Web login always uses OTP. Existing legacy password behavior stays unchanged and its removal is a separate scope. | Role-based Telegram-registration-to-Web-OTP tests and regression proof that legacy accounts are unchanged. |
| IT-06 | New registration must not start existing bot/channel/onboarding behavior from incomplete projections or accidentally reorder current tutorials. | Closed by owner decision: after the approved projection gate, enter the current linked-account/panel flow unchanged; current channel join request, two tutorials, order/conditions, `خواندم` callbacks, welcome/menu copy, and access blocking remain exactly as implemented. | Existing onboarding/join-request regression suite plus direct-registration handoff test. |
| IT-07 | Generic new-user and invitation notifications can fire twice after command retry, Web/Telegram race, or sync replay. | Closed with `DT-15`: stable unique outbox event identity, post-commit dispatch, foreign durable dedupe, and one success message on first terminal intent transition. | Duplicate-command, race, replay, worker-restart, and delivery-failure tests. |
| IT-08 | Registration reconciliation and OTP fallback need delayed execution without adding services or duplicate jobs. | Closed by owner decision: add two server-guarded job factories to the existing `main.py` background leader, not new workers/processes/containers/queues. Registration job runs only foreign; OTP fallback job only Iran; existing singleton lease plus atomic claim protects duplicates. | Startup/surface matrix, leader failover, wrong-server absence, and duplicate-claim tests. |
| IT-09 | New registration contracts must remain safe while server versions differ or connectivity is interrupted. | Closed by owner decision: do not redesign deployment/outage handling; integrate additive migrations, tables, contracts, and flags into the project's existing compatibility, queue, recovery, and rollback mechanisms. | Existing deployment/outage regression suite plus mixed-version staging drill for the added artifacts. |
| IT-10 | New local-only tables, completion fields, `sync_version`, and backfills can break event builders, receiver apply, field policy, sync registry/parity, or fabricate historical truth. | Locked conservative migration and mixed-version policy; ambiguous rows remain unresolved, and every affected sync-stack artifact is inventoried before implementation. | Production-shaped migration, mixed-version, field-policy, and parity reports. |
| IT-11 | Explicit table/schema lists in reset, fixture, parity, backup verification, purge, migration, or deploy tooling may omit new local state. | Closed by owner decision: extend existing tooling only; register local tables no-sync, update dependency-order staging reset and health checks, verify normal PostgreSQL restore, add no production purge, and preserve data on rollback. | Tool inventory, script report, migration-health assertions, and backup/restore drill. |
| IT-12 | The required 100% changed-code, state/transition, property/fuzz, deterministic-race, mutation, and traceability gates exceed the repository's current coverage tooling. Without an explicit workstream they are not executable release gates. | Closed by owner decision: Stage 9 first adds and proves the missing test infrastructure using the current coverage/CI foundation; production-safe injectable checkpoints have no runtime/public control path, and no required test may be waived because tooling is absent. | One end-to-end proof for each new tool, CI gate failure fixtures, mutation kill report, deterministic race proof, and traceability completeness test. |
| IT-13 | PWA/browser cache and legacy-client compatibility were evaluated as a possible indirect scope. | Closed as no-change by owner decision: do not add a compatibility window, deprecation telemetry/header, forced upgrade behavior, or service-worker redesign. Existing PWA, endpoint, and cache behavior remains unchanged; contract-v2 aliases already approved under `DT-11` remain part of that API change only. | Existing PWA/API regression suite. |
| IT-14 | Sync batches can reorder User/Relation/Invitation changes and stale events can overwrite newer state. | Closed by owner decision: monotonic `sync_version` on authoritative rows/events, newer-only receiver apply, temporary unversioned mixed-deploy compatibility, and kind/tier-derived projection gate without cross-table order assumptions. | Reorder, duplicate/stale-version, mixed-version, per-kind gate, and recovery tests. |
| IT-15 | New reconciliation and OTP background jobs share process/DB/Redis/HTTP resources with latency-sensitive market updates. | Closed by owner decision: no new process/pool; fully async I/O; no DB transaction across HTTP; registration batch 10/concurrency 1, OTP concurrency 4, configurable and independently disabled; fail staging above 10% market p95 degradation or persistent market backlog growth. | Combined-load event-loop/DB/Redis/HTTP benchmark and recovery-to-baseline report. |
| IT-16 | SMS.ir IP allowlists, template approval, Telegram gateway health, clock sync, and Redis persistence are external runtime dependencies. | Locked fail-closed/fallback semantics; operational readiness remains open per environment. | Dependency readiness checklist and failure drills. |

### Indirect non-technical challenges

| ID | Organizational impact | Decision status and recommended direction | Closure evidence |
|---|---|---|---|
| IN-01 | A new support/intent inspection surface was considered as a possible operational addition. | Closed as no-change by owner decision: add no panel, endpoint, search, retry command, diagnostic view, runbook, or support workflow. The existing WebApp pending-invitation list remains the only required administrative view. | Existing pending-invitation UI regression test and route-surface proof of no new actions. |
| IN-02 | Cross-server outage handling for registration could have introduced a separate incident workflow. | Closed by owner decision: reuse existing project health/backoff/queue/recovery/alerts with no new incident system, runbook, escalation, or admin tool; user gets pending text and reconnect auto-reconciles under existing terminal rules. | Existing outage/recovery regression plus registration-specific disconnect scenarios. |
| IN-03 | New background jobs can fail while API health still appears normal. | Closed by owner decision: extend existing health/metric/alert path only, no new dashboard/system; PII-free job heartbeat/error/success/pending/oldest/batch metrics with 60s, healthy 5m, OTP 2s, and market 10% thresholds. | Health/metric assertions, alert-routing tests, outage suppression, and no-PII label scan. |
| IN-04 | Additional provider/vendor/finance alerts and operating thresholds were considered. | Closed as no-change by owner decision: add no abstraction, vendor change, accounting, cost metric, provider-specific alert/threshold, or escalation workflow; retain existing operations and verify only functional readiness in staging. | Existing provider/template/rate-limit regressions plus staging send checks. |
| IN-05 | New flows could have triggered broad rewrites of existing Persian copy. | Closed by owner decisions: reuse current LoginView/contact/address/welcome/channel/tutorial/ack/completion copy and flow; no new first-wins screen; keep approved safe rejection and connectivity-pending text only where current behavior has no equivalent; Telegram-method timer changes behavior, not copy. | Existing-copy snapshot regressions plus pending/rejection and cross-path acceptance. |
| IN-06 | Cross-server features need a deterministic staging enable/rollback order. | Closed by owner decision: existing deploy, flags initially off, then schema/sync/leader, contract/invitation, online registration, outage/recovery, OTP auto-fallback, invitation-SMS matrix, combined market load, and owner acceptance; per-feature flag rollback, no role restriction, and explicit production gate. | Staging rollout checklist, evidence bundle, and per-flag rollback drill. |

### Required decision record for every open challenge

Before code for an `Open` challenge is accepted, add a decision record under the relevant stage or
an approved local engineering note with:

```text
Challenge ID:
Decision owner:
Selected option:
Alternatives considered:
Correctness and security tradeoffs:
Operational and migration impact:
Evidence/tests required:
Rollback or disable path:
Decision date and approval:
```

Closing an item requires updating its registry status to `Closed in Stage N` and linking the exact
test, migration report, benchmark, owner approval, or staging evidence. A passing happy-path test by
itself does not close a race, outage, privacy, or operational challenge.

### Mandatory ChatGPT Review Handoff At Every Stage

After a stage implementation, verification, documentation, and local commit are complete, and
before implementation of the next stage begins, produce an external-review handoff under the
ignored `tmp/chatgpt/` path. This is a mandatory stage boundary, not a replacement for repository
tests or owner acceptance.

The handoff must contain:

1. one ZIP named `dual-platform-registration-stage-N-review-YYYYMMDD.zip`;
2. an English `review-prompt.md` that identifies the exact branch, commit/range, roadmap path,
   stage evidence path, architecture rationale, locked product decisions, out-of-scope surfaces,
   and review questions;
3. the reviewed commit patch, changed-file list, commit metadata, and clean-worktree evidence;
4. raw logs with commands and exit status for every test/migration/static/deploy/build gate actually
   used to close the stage, plus a non-overlapping summary that does not claim mathematical 100%
   coverage without Stage 9 evidence;
5. environment-safe evidence for any live read-only check, explicitly distinguishing repository
   evidence from runtime evidence because the ChatGPT advisor can inspect the GitHub repository but
   cannot inspect this server unless outputs are attached;
6. a package manifest with relative file path, byte size, and SHA-256 for every payload file; hash
   the manifest through the checksum index and validate the final ZIP with a separate outer SHA-256.

The package must not contain `.env` files, credentials, tokens, OTP values, raw invitation links,
database dumps, user PII, private keys, object-storage secrets, or unredacted server logs. Scan both
the unpacked directory and final ZIP before handoff. Generated packages remain outside Git.

The English prompt must explain why this roadmap exists, not only what changed: Telegram is
foreign-only, the WebApp is Iran-only, Iran is the final identity/registration writer, accepted
non-messenger product state syncs near-real-time, messenger runtime state does not sync, users may
use both surfaces concurrently, outage reconciliation must compare against current Iran truth, and
the rollout is deliberately additive and flags-off to contain cross-server race and migration risk.
It must also restate the approved Telegram contact proof, no-Telegram-registration-OTP rule, later
Web-login OTP behavior, invitation SMS category policy, no-manual-review rule, and unchanged
CDN/Object Storage runtime architecture.

The reviewer must report findings first, ordered by severity, with exact repository file/line and
roadmap-section references. It must check code/roadmap consistency, reuse of existing services,
project invariants, migration/rollback safety, sync ownership and mixed-version behavior, security
and privacy, test completeness for that stage, and risk to later stages. A blocking finding reopens
the stage; the next stage starts only after findings are classified and any accepted blocker is
resolved and re-verified.

## Implementation Roadmap

### Stage 0 - Freshness, Branch, And Scope Guard

- Confirm current branch is exactly `candidate/bot-webapp-integration` before each edit/commit.
- Do not switch branches, edit tracked files, commit, or push merely because this local roadmap was
  updated; implementation starts only on an explicit owner instruction.
- Re-read current registration, invitation, sync, bot-access, OTP, notification, and deployment
  configuration code before implementation.
- Capture current two-server staging sync/health baseline.
- Confirm no unrelated dirty changes are included.
- Keep production flags disabled.

Challenges and engineering decisions in this stage:

- `DT-02`, `DT-04`, `DT-05`: produce a call-site/writer/field-ownership inventory before selecting
  schema or service boundaries; no known invitation or Telegram-identity writer may be omitted.
- `DN-01`, `DN-02`: apply the owner-approved uniform eligibility policy without introducing
  role-specific OTP or approval gates; preserve universal identity and existing inviter controls.
- `IT-05`: apply the owner-approved no-new-password rule and inventory the legacy behavior only to
  prove that this roadmap does not change existing accounts.
- `DN-03`: apply the owner-approved central two-day expiry and exact relation timestamp policy.
- `DT-17`, `DN-05`: apply the approved per-category SMS policy and flags; preserve manual sharing
  for Standard/admin/Tier-1 and enabled existing templates for accountant/Tier-2.
- `DT-19`, `DN-06`: apply the owner decision that no manual-review state, queue, SLA, or override
  surface exists; only safe terminal rejection copy remains to approve.
- `DN-07`: apply the owner-approved current-retention behavior, foreign-local no-sync intent, and
  normal sync of accepted product records.
- `IT-04`: apply the owner-approved Iran home/session authority for every new User without migrating
  existing users or touching Offer/request ownership.
- `IT-11`: extend only existing explicit table/schema lists, no-sync registry, dependency-order
  staging reset, migration health, deploy packaging, and restore verification.
- `IT-12`: inventory the existing coverage workflow and prove the selected property/fuzz, mutation,
  diff-coverage, deterministic-checkpoint, and traceability tools before treating 100% as an
  executable gate.
- `IN-06`: follow the approved existing-deploy staging sequence, per-feature flags/rollback, owner
  acceptance, no role-specific restriction, and explicit production gate.

Exit criteria:

- branch and worktree checks pass;
- baseline evidence is recorded;
- implementation file list and migration heads are current;
- the inventory and open-decision owner list cover every registry ID assigned above.

Execution record (2026-07-11): **Complete**. The read-only source/runtime inventory, two-server
staging health and migration baseline, test results, implementation file map, assigned challenge
disposition, and forward preconditions are recorded in
`docs/DUAL_PLATFORM_REGISTRATION_STAGE0_BASELINE_20260711.md`. No application code, schema, runtime
configuration, feature flag, staging deployment, or production surface changed in Stage 0.

### Stage 1 - Contracts, Schema, And Registry

- Add invitation completion metadata migration.
- Add foreign-local registration intent model/migration.
- Add Iran command receipt model/migration.
- Register each new table as `sync` or `no-sync` with field-level sensitive policy.
- Define request/response schemas and outcome enums.
- Add configuration flags and URL separation.
- Remove direct invitation-lifetime reads from `core.config`; all new async invitation services use
  `get_trading_settings_async()` and snapshot the accepted two-day value from the existing
  `core.trading_settings` source.
- Inventory and update event payload builders, receiver apply, `core.sync_field_policy`,
  `core.sync_registry`, parity comparators, reset/fixture/seed lists, migration health, and
  backup/restore verification for `sync_version` and every new table/field.

Challenges and engineering decisions in this stage:

- `DT-01`: implement the approved explicit kind, derived status, atomic completion constraints,
  relation-first legacy backfill, `legacy_unknown` rejection, and soft revocation.
- `DT-02`, `DT-13`: implement global normalized-key reservation, deterministic advisory locks,
  transactional release, exact-retry matching, and safe collision rejection as approved.
- `DT-05`, `IT-10`, `IT-14`: implement patch-only source-aware fields plus authoritative
  `sync_version`, newer-only apply, temporary unversioned compatibility, and stale full-row
  sanitizer across the complete sync-stack inventory. Unversioned compatibility ends only after
  both nodes report a release with versioned-event support and an explicit compatibility flag is
  disabled; later unversioned events fail closed instead of remaining permanently accepted.
- `DT-08`: preserve link structure while masking public mobile, admin-gating full links, applying
  no-store/rate limits, and redacting Nginx/application logs without a token-exchange redesign.
- `DT-11`: implement approved contract v2, temporary aliases, explicit availability/state/SMS
  fields, strict internal schemas, and alias-usage telemetry.
- `DT-12`: preserve current UTC/timezone conventions with no broad migration; implement only the
  approved Invitation soft-revocation lifecycle and exact boundary tests using existing helpers.
- `DN-07`: register durable intent as foreign-local `no-sync`, retain it under current database and
  backup behavior, and sync only accepted User/Invitation/Relation product state.
- `DN-03`: use `core.trading_settings` as the sole lifetime source, assert its effective two-day
  value, snapshot it into Invitation, and copy that exact UTC timestamp to related pending records;
  the old one-day `core.config` default cannot affect creation. Leave a versioned kind-policy
  extension point only for future changes.
- `DT-14`: implement the approved existing-HMAC, strict-schema, command/hash/receipt transaction,
  retry, changed-payload rejection, and OTP Redis-dedupe contracts.
- `DT-18`: preserve the exact `expires_at + 86400` boundary.
- `DT-16`: implement the exact existing Web registration minimum-10 address rule and message in bot
  and the shared Iran completion boundary; leave profile-edit validation unchanged.
- `IT-09`: integrate with the existing deployment, queue, outage, recovery, and rollback mechanisms
  without rewriting them.
- `IT-10`, `IT-11`, `IT-13`: make migrations additive, register no-sync tables, update every
  explicit schema/tooling list, and define stale-client compatibility.

Exit criteria:

- migration upgrade/downgrade smoke passes on empty and production-shaped staging databases;
- sync registry coverage passes;
- sensitive-field policy is explicit;
- no behavior is enabled;
- ADRs/decision records for `DT-01`, `DT-02`, `DT-05`, `DT-08`, `DT-12`, `DT-13`, `DT-14`,
  `DT-16`, and `DT-18` are approved.

Original execution record (2026-07-11): **Completed as a source milestone; not deployed**. The additive
migration, models, strict contracts, reservation/receipt primitives, source-aware versioned sync
policy, public invitation protections, runtime configuration, explicit registry/tooling updates,
decision records, and verification evidence are recorded in
`docs/DUAL_PLATFORM_REGISTRATION_STAGE1_CONTRACTS_SCHEMA_REGISTRY_20260711.md`. Scratch migration
upgrade/downgrade/backfill/collision tests and the complete backend regression suite passed. Active
staging remains on `f7c8d9e0a1b2`, no production action occurred, and every new entry/worker/Sync-v2
flag remains off. Before any migration rollout that permits invitation writes, Stage 3 must connect
all canonical writers to the reservation service.

External review handoff (2026-07-11): the mandatory English prompt, implementation patch, command
logs, migration evidence, read-only staging proof, redaction report, and per-file hashes are packaged
outside Git at
`tmp/chatgpt/dual-platform-registration-stage1-review-20260711.zip`. The review target is
`4a36574f..37c4451d`; a blocking ChatGPT finding reopens Stage 1 before Stage 2 may begin.

Independent review update (2026-07-11): **Reopened, remediated, and awaiting re-review**. The
review returned `NO-GO` with one critical, six high, and related medium findings. The accepted
findings were reproduced against current code and remediated without rewriting Stage 1/2 history.
The remediation adds transactional delta/epoch counter events and exact-once receipts, conservative
relation-and-time-backed legacy completion, exact non-trimmed command addresses, one shared
role/kind/tier bot policy, real Nginx credential-log suppression, natural-identity User sync,
source-watermark-only unversioned ordering, foreign local-write enforcement, role-aware WebApp URL
classification, command-receipt tuple constraints, and hashed identity locks. Design and evidence
are recorded in `docs/DUAL_PLATFORM_REGISTRATION_STAGE1_REMEDIATION_20260711.md`. Stage 1 is not
self-approved: Stage 3 remains blocked until the mandatory independent remediation review returns
`GO`. The mixed-version apply matrix also retains the accepted legacy Iran User INSERT path while
requiring natural identity for versioned events. Final remediation evidence reports `2730`
full-backend passes, `182` focused passes, `2` real migration passes, `8` real PostgreSQL
service/counter passes, `3` real sender-side counter/outbox passes, and `3` real Nginx passes. No
application deployment or feature flag was applied. One evidence command briefly started the
active database migration dependency; the official downgrade restored `f7c8d9e0a1b2`, schema
absence and sync health were verified, and all later scratch runs used `--no-deps`.

### Stage 2 - Shared Authoritative Registration Service

- Extract current Web registration mutation into a shared Iran service.
- Add invitation row lock, natural-key conflict reads, and idempotency receipt integration.
- Preserve relation activation, mandatory membership, user defaults, sync/outbox, and post-commit
  notifications.
- Route current Web `register-complete` through the service without changing Web behavior.
- Set literal `home_server="iran"` inside the shared service for both adapters; do not derive it from
  request host and do not permit the model's `foreign` default to supply it.

Challenges and engineering decisions in this stage:

- `DT-03`: remove nested commits, lock the invitation, and define deterministic loser outcomes for
  Web/Web and Web/Telegram races.
- `DT-04`, `IT-01`: route all invitation and Telegram-link identity mutations through Iran; retain
  compatibility adapters only as callers of the authoritative service.
- `DT-05`, `IT-14`: emit only source-owned fields/events so an old foreign snapshot cannot overwrite
  Iran identity or profile state.
- `DT-06`, `IT-03`, `IT-06`: preserve relation/membership/pricing/limit semantics and apply the
  approved kind/tier-derived projection gate without making Standard/admin/police wait for Relation.
- `DT-12`: use soft revocation for Invitation history.
- `IT-02`: leave all existing User deletion/recovery/reinvite behavior unchanged and add no bot
  lifecycle surface.
- `DT-15`, `IT-07`: reuse `telegram_notification_outbox` for the approved transactional unique
  event, existing foreign delivery/dedupe path, first-terminal-transition message, delivery retry,
  and centralized redaction policy; add no second outbox or worker.
- `IT-04`: set literal `home_server=iran` for both completion surfaces and assert the mechanism, not
  only the result; create no foreign session/token and leave existing users and Offer/request
  ownership untouched.
- `IT-05`: explicitly keep `must_change_password=False` for new Telegram registrations and leave
  existing legacy account state untouched.

Exit criteria:

- existing Web registration tests remain green;
- duplicate/race tests prove one User and one consumed invitation;
- transaction rollback leaves neither User nor consumed invitation;
- no Telegram path is enabled yet;
- field-authority, relation-completeness, session, and side-effect invariants have regression tests.

Original execution record (2026-07-11): **Completed as a source-code milestone**. The existing Web
`register-complete` adapter now delegates all registration product-state mutation to one
Iran-guarded authoritative transaction. Invitation/User/Relation locks, deterministic natural-key
and Telegram-ID serialization, command receipt replay, mandatory membership, transactional reuse
of `telegram_notification_outbox`, literal Iran ownership, rollback checkpoints, and post-commit
Web notification boundaries are implemented and recorded in
`docs/DUAL_PLATFORM_REGISTRATION_STAGE2_AUTHORITATIVE_SERVICE_20260711.md`. The complete backend
suite passed `2700` tests; the opt-in real PostgreSQL race/rollback module passed `6` tests on a
fresh scratch database. All scratch databases were removed and the active database remained at
`f7c8d9e0a1b2`. No Telegram route, job, worker, feature flag, staging deploy, or production action
was enabled.

External review handoff (2026-07-11): the mandatory English prompt, exact Stage 2 patch/metadata,
sanitized command logs, migration/runtime safety evidence, redaction report, and per-file hashes are
packaged outside Git at
`tmp/chatgpt/dual-platform-registration-stage2-review-20260711.zip`. The review target is the Stage
2 range beginning at `00aab349`; a blocking ChatGPT finding reopens Stage 2 before Stage 3 may
begin.

Dependency update (2026-07-11): **Provisional and frozen before Stage 3**. Stage 2 was implemented
before the delayed independent Stage 1 review was received. It has been revalidated against the
accepted Stage 1 remediation, including the stricter receipt constraints, shared projection policy,
hashed locks, natural-identity sync, counter contract, race/rollback paths, and real PostgreSQL
suite. This preserves the Stage 2 implementation commit rather than rewriting history, but does not
override the Stage 1 `NO-GO`; both milestones require a clean remediation review before Stage 3.

### Stage 3 - Canonical Invitation Creation And URL Repair

- Route bot-admin invitation creation to signed Iran authority.
- Return canonical Telegram and Iran WebApp URLs.
- Replace overloaded foreign `FRONTEND_URL` use with `PUBLIC_WEBAPP_URL` for user-facing links.
- Validate `PUBLIC_WEBAPP_URL` at startup/link generation and fail closed for an empty, malformed,
  insecure non-test, or configured foreign-host value.
- Fix pending-invitation and landing copy contracts.
- Add invitation-open audit on both surfaces.

Challenges and engineering decisions in this stage:

- `DT-02`, `DT-04`, `DT-13`: make Iran the only creator and make repeated/cross-kind creation return
  an explicit deterministic result.
- `DT-08`, `DT-11`: return explicit contract-v2 canonical links only to authenticated admins;
  public responses mask mobile, logs redact token/mobile, and legacy aliases remain compatible.
- `DT-17`, `DN-05`: treat Standard/admin and Tier-1 suppression as an intentional change from the
  current all-category SMS behavior; return generated links to admins and apply the central category
  policy equally for bot/Web creation while preserving existing accountant/Tier-2 templates.
- `DN-03`: display the stored authoritative expiry consistently for all invitation kinds.
- `DN-04`, `IN-05`: add no first-wins explanation or broad copy change; route completed paths into
  existing login/linked-account UI and reuse current messages except approved pending/rejections.
- `IT-09`, `IT-13`: use existing mixed-version deployment handling and keep response additions
  backward-compatible for stale PWA clients.
- User-facing URLs remain environment-driven; only the canonical Iran Web URL is a product
  requirement.

Exit criteria:

- every bot-generated Web URL resolves to the Iran WebApp;
- foreign public WebApp routes remain blocked;
- no invitation is authored only on foreign;
- retries do not create duplicate invitations;
- Standard/admin/Tier-1 flag-off tests prove zero SMS.ir calls; accountant/Tier-2 flag-on tests
  prove unchanged templates are used and provider acceptance is reported accurately;
- admin acceptance proves both generated links can be copied and shared manually.

### Stage 4 - Telegram Registration Intent And Reconciliation

- Add foreign intent persistence and a server-guarded job to the existing background leader.
- Add signed Iran reconciliation endpoint.
- Implement full decision matrix and command receipts.
- Add bounded sync wait after successful Iran completion.
- Preserve Web-created fields when linking an existing user.

Challenges and engineering decisions in this stage:

- `DT-03`, `DT-14`: make retries/lost responses resolve through one receipt and never repeat the
  authoritative mutation.
- `DT-05`, `IT-14`: reconcile current Iran truth and apply field-level ownership; never replay a
  foreign User snapshot.
- `DT-06`, `IT-03`, `IT-06`: successful command response is not bot activation; require exactly the
  kind/tier-specific synced projections and access policy, with no unnecessary Relation wait for
  Standard/admin/police.
- `DT-18`: encode proof-time validity, explicit-revocation precedence, the inclusive 24-hour
  automatic boundary, and terminal expiry rejection after the boundary.
- `DT-19`, `DN-06`: exclude manual review from enums, schemas, routes, workers, admin UI, and retry
  logic; return safe terminal codes and new-invitation guidance only where applicable.
- `DN-07`: protect retained intent/receipt data under current access, backup, and no-secret-log
  controls; do not introduce an automatic purge or generic table sync.
- `IT-01`: cover old link tokens in the same reconciliation rules.
- `IT-02`: prove existing User lifecycle remains unchanged and no new bot deletion/recovery action
  is introduced.
- `IT-07`: cover duplicate post-commit notifications in the same reconciliation matrix.
- `IT-08`, `IT-15`, `IN-02`: place the foreign background job with bounded load and preserve clear
  user-visible pending/outage outcomes.

Exit criteria:

- no User is created on foreign during normal or outage flows;
- lost-response retry is idempotent;
- Web-created-during-outage scenario links existing User;
- all conflicts fail closed with safe outcome codes;
- expiry `-1s`, exact expiry, `+24h`, `+24h+1s`, revocation-race, and clock-tolerance tests pass.

### Stage 5 - Direct Telegram Registration FSM

- Replace Web redirect for eligible normal invitations with contact/address/confirmation FSM.
- Reuse sender-owned contact guards.
- Enforce eligibility matrix.
- Persist ready intent before forwarding.
- Show pending-not-final state during outage.
- After complete required projections are visible, hand off to the existing linked-account/panel,
  channel-join, and onboarding flow without changing its messages, order, conditions, or callbacks.

Challenges and engineering decisions in this stage:

- `DT-07`: implement the approved Redis-until-confirmation/PostgreSQL-after-confirmation boundary,
  expiry-bounded TTL, commit-before-FSM-clear handoff, restart recovery, and no-foreign-User rule.
- `DT-08`, `DT-16`: mask public invitation/mobile data, redact logs, and enforce the exact current
  Web registration minimum-10 address rule/message in bot and Iran authority.
- `DT-11`, `DN-04`: display exact pending/created/linked/rejected states; never label a collected
  intent as completed registration.
- `DT-19`, `DN-06`: present safe terminal rejection/new-invitation guidance without exposing PII or
  implying that an operator can approve, retry, or override the result.
- `DN-01`, `DN-02`: apply the same direct Telegram registration controls to all eligible invited
  roles; do not add hidden role-specific restrictions.
- `IT-05`: do not collect/set a password or force password setup in the new Telegram flow; preserve
  legacy account behavior outside this path.
- `IT-03`, `IT-06`: cross the projection-complete gate, then reuse the existing linked-account,
  channel join, tutorials, `خواندم`, menu, and access behavior exactly as-is.
- `IT-13`, `IN-05`: preserve existing PWA/deep-link behavior and reuse current contact/address copy,
  adding only approved safe pending/rejection states.

Exit criteria:

- eligible user completes registration without opening WebApp;
- mismatched/forwarded contact cannot register;
- Web-only roles remain blocked from direct bot completion;
- bot restart during collection resumes through Redis FSM;
- bot restart after ready intent resumes through durable worker state;
- no role, outage, or rejected/expired state grants premature bot access.

### Stage 6 - WebApp Login OTP Via Telegram And 40-Second SMS Fallback

- Replace generic Iran Telegram OTP relay with dedicated signed request/acknowledgement.
- Introduce OTP request identity and structured Redis state.
- Add foreign short-lived delivery dedupe.
- Add Iran fallback sorted set and worker.
- Integrate verify success with fallback cancellation.
- Keep the legacy SMS-resend endpoint safe for stale clients without exposing it in the target UI.
- Treat timeout/ambiguity as immediate SMS fallback.
- Keep `otp:{mobile}` as the one code-of-record during legacy compatibility; request metadata,
  verification, manual resend, due claim, and cancellation use one atomic structured Redis contract.
- Change `scripts/deploy_staging.sh` and `deploy/staging/env.staging.example` so
  `STAGING_LOG_OTP_CODES` defaults to and is reasserted as `false`; when Telegram OTP testing is
  enabled, deployment must reject a true value. Existing staging automation uses the separate
  `STAGING_ENABLE_DEV_LOGIN` path instead of extracting OTP values from logs.

Challenges and engineering decisions in this stage:

- `DT-09`: replace weak generation/false-positive relay success with secure randomness and one
  explicit request/delivery state machine.
- `DT-10`: implement the approved real async SMS.ir adapter, bounded timeouts, Redis due queue,
  atomic multi-worker claim, restart behavior, and no ambiguous same-OTP provider retry.
- `DT-14`: implement strict signed delivery, replay window, atomic foreign dedupe receipt, and
  explicit acknowledgement taxonomy.
- `DT-15`: remove staging OTP logging from both generated env and every-deploy enforcement, redact
  Telegram IDs, and keep OTP text out of durable notifications, sync, metrics, and responses.
- `DN-08`, `IN-04`: preserve existing provider operations/rate limits and add no cost/accounting or
  provider-specific alert work; validate only approved functional send behavior.
- `IT-08`: register the two approved jobs in the existing background leader with foreign/Iran
  server guards, singleton leadership, and atomic claims; add no worker service.
- `IT-13`: stale manual-resend calls use the same structured request/code/TTL, atomically no-op the
  scheduled send after SMS acceptance, and race safely with a due-job claim at the exact boundary;
  collect deprecation telemetry.
- `IT-15`, `IT-16`, `IN-03`: bound background-job concurrency, verify Redis/clock/provider dependencies,
  and alert on scheduler lag without harming market/sync latency.

Exit criteria:

- Telegram and SMS contain the same code;
- successful verify before 40 seconds prevents fallback;
- unverified request sends SMS at 40 seconds within accepted scheduler tolerance;
- Telegram failure sends SMS immediately;
- background leader/API/Redis restart tests preserve correct bounded behavior;
- OTP never enters logs, sync, or durable notification outbox;
- a fresh or repeated staging deploy leaves `STAGING_LOG_OTP_CODES=false`, real Telegram/SMS delivery
  is not short-circuited, and existing dev-login automation remains available for unrelated E2E;
- existing LoginView text is unchanged; Telegram-method timer is 40 seconds, no manual resend action
  appears at zero, and backend TTL remains 120 seconds.

### Stage 7 - WebApp And Bot UX Completion

- Make invitation landing choices truthful and role-aware.
- Show direct Telegram registration states.
- Route completed Telegram registrations opening a Web link to OTP login.
- Route Web-completed invitations opening in Telegram to safe same-user linking.
- Show Telegram-first OTP delivery and automatic SMS fallback status/countdown.
- Ensure all user-facing Web URLs use Iran.

Challenges and engineering decisions in this stage:

- `DT-08`, `DT-11`: mask public mobile, preserve admin-only full links, and render explicit
  availability/derived states/SMS outcomes from contract v2 while tolerating old aliases.
- `DT-17`, `DN-05`: show generated links to admins, make per-category SMS state truthful, and keep
  enabled accountant/Tier-2 template contracts unchanged without injecting a Telegram link.
- `DN-03`: use the stored two-day-policy expiry consistently on both surfaces.
- `DN-04`, `IN-05`: use existing login/linked-account/completion copy and approved pending/rejection
  states consistently; add no first-wins explanation screen.
- `IT-01`, `IT-04`: an already completed invitation routes to same-user linking or Iran OTP login,
  never a second registration, foreign session, or `home_server` mutation.
- `IT-05`: route later Web access through OTP without a password-setup interruption for users made
  by the new flow.
- `IT-06`: change no onboarding/welcome/channel copy or sequence; only hand the newly registered
  synced User into the existing flow.
- `IT-13`: leave existing PWA/service-worker compatibility behavior unchanged; in the current
  LoginView flow only change Telegram-method timer 30 to 40 and remove its zero-time manual action.

Exit criteria:

- no dead or foreign WebApp link exists;
- no UI claims registration is final while intent is pending;
- no duplicate registration form appears for an already completed invitation;
- mobile and desktop text/states fit existing design-system constraints;
- old/new client compatibility and single-countdown behavior pass on real mobile viewports.

### Stage 8 - Observability And Audit

- Add redacted audit events and metrics.
- Add OTP fallback health reporting without exposing codes.
- Extend existing health with both job heartbeats, last success/error, pending/oldest age, batch
  duration, and approved thresholds; add no dashboard or monitoring system.
- Keep the existing WebApp pending-invitation list unchanged; add no intent inspection, search,
  retry, diagnostic, or support action.

Challenges and engineering decisions in this stage:

- `DT-12`, `DN-07`: preserve Invitation/audit evidence under current database and backup behavior
  without adding an intent-specific purge policy.
- `IT-02`: do not modify or extend existing User lifecycle tooling.
- `DT-15`, `IT-07`: prove redaction, unique event identity, foreign dedupe, and first-terminal-only
  success messaging across retry, race, sync replay, and background-job restart.
- `DT-19`: prove there is no manual-review enum/route/UI/worker transition and all invalid states are
  terminally rejected without force-create/link.
- `DN-06`: implement the approved bounded rejection/new-invitation copy; no review SLA or escalation
  workflow is needed.
- `IN-03`: expose only the approved background-job timing/health signals through existing alerts;
  add no provider/cost monitoring under `DN-08`/`IN-04`.
- `IT-11`: update existing backup verification, staging reset, and fixture tooling without adding a
  production purge or parallel toolchain.
- `IN-01`: preserve the current pending-invitation UI and add no support tooling.
- `IN-02`: reuse existing outage operations unchanged and add only the approved user pending message
  plus registration-specific recovery regressions.

Exit criteria:

- every terminal intent has an explicit reason/outcome;
- pending/oldest-age alerts work;
- heartbeat, healthy-connectivity pending, OTP-lag, and market-p95 thresholds route through existing
  alerts without PII;
- no PII or secrets leak in logs/metrics;
- existing pending-invitation UI remains behaviorally unchanged and no new administrative action is
  exposed.

### Stage 9 - Automated Test Matrix

First implement and prove the missing test infrastructure, then implement focused unit,
integration, concurrency, and two-server tests listed below. The infrastructure is required product
work for this roadmap and cannot be deferred or replaced by a coverage waiver:

- pin `hypothesis` in a dedicated test dependency set for property, state-machine, malformed-input,
  and bounded fuzz generation;
- pin and configure `mutmut` for the critical invariant modules and maintain the explicit mutant
  target/ignore manifest required by this roadmap;
- build on the existing Python branch-coverage and frontend V8 coverage reports: enforce Python
  changed-line/changed-branch coverage with `diff-cover`, and add a repository script that maps the
  Git diff to frontend V8 statement/branch/function/line data;
- define an injectable async checkpoint protocol used only at the named service boundaries, with a
  production no-op implementation and a deterministic test barrier implementation. It has no env,
  HTTP, admin, or runtime control surface and cannot alter business results;
- add a traceability/state-transition artifact generator and CI verifier that compares decisions,
  registry IDs, scenario tuples, tests, results, coverage, and mutation evidence for set equality;
- add negative self-tests proving CI fails for a missing tuple, missing transition, surviving
  critical mutant, uncovered changed branch, skipped required test, or nonexistent test reference.

Challenges and engineering decisions in this stage:

- `DT-01` through `DT-19`: maintain a traceability row from every direct technical challenge to at
  least one positive, negative, race, migration, or outage test as applicable.
- `IT-01` through `IT-16`: run affected session, linking, relation/market, onboarding, sync-order,
  worker-placement, mixed-version, tooling, stale-client, and load regressions; new focused tests do
  not replace these indirect suites.
- `IT-12`: prove one end-to-end property example, fuzz rejection, deterministic two-party race,
  killed critical mutant, backend/frontend diff-coverage failure, and traceability failure before
  accepting the infrastructure as ready for the full matrix.
- `DN-01` through `DN-08` and `IN-01` through `IN-06`: identify which items require owner approval,
  policy, operational evidence, or external dependency rather than falsely marking them closed by
  an automated test.
- Build a challenge traceability report with columns `ID`, `decision record`, `test/evidence`,
  `remaining risk`, and `stage owner`.

Exit criteria:

- all matrix rows pass;
- existing auth, invitation, session, bot-access, sync, and onboarding regressions pass;
- migration, compile, frontend unit, and relevant E2E gates pass;
- all test-infrastructure self-tests pass and each required artifact is generated from the current
  commit rather than hand-authored;
- every registry ID has evidence or an explicit unresolved blocker assigned to a later gate.

### Stage 10 - Real Two-Server Staging Validation

- Deploy only to isolated staging.
- Validate actual Iran WebApp and foreign bot surfaces.
- Validate signed transport through the project's current configured two-server path with no cache,
  header stripping, body mutation, or redirect ambiguity.
- Measure registration commit-to-sync visibility latency.
- Simulate disconnect before contact, after contact, after intent ready, before Iran response, after
  Iran commit, and during OTP Telegram delivery.
- Verify SMS.ir staging/production-account IP allowlist and template acceptance before declaring OTP
  fallback ready.
- Run a fresh staging environment generation and a repeated deploy; both must leave
  `STAGING_LOG_OTP_CODES=false`, exercise the real Telegram/SMS delivery contract, and retain the
  separate dev-login mechanism for unrelated E2E setup.
- Assert the deployed effective invitation lifetime comes from `core.trading_settings`, is two days,
  and is copied exactly to each applicable pending Relation.

Challenges and engineering decisions in this stage:

- `DT-03`, `DT-06`, `DT-18`: exercise real two-node races, out-of-order projection gating, and all
  exact delayed-intent/revocation boundaries under clock synchronization.
- `DT-08`, `DT-14`, `DT-15`: inspect real Nginx/application logs and signed request behavior for secret
  leakage, replay, cache, header, and body-preservation failures.
- `DT-09`, `DT-10`: measure real Telegram acknowledgement, immediate SMS, 40-second claim jitter,
  event-loop latency, restart recovery, and one-code behavior.
- `DT-19`, `DN-06`: prove invalid/conflicting/late cases terminate with safe rejection codes and no
  review queue, admin mutation, or worker retry exists.
- `IN-03`: validate approved job-health thresholds; under `DN-08`/`IN-04`, perform only functional
  provider send/readiness checks with no new cost or provider alert baseline.
- `IT-08` through `IT-14`, `IT-16`: validate background-job placement, existing mixed-version/outage
  handling, migrations/parity, test-evidence tooling, backup/restore tooling, unchanged PWA
  behavior, sync reordering, and runtime dependencies.
- `IT-15`: validate approved batch/concurrency defaults, async yielding, no transaction across HTTP,
  independent job flags, market p50/p95, queue age, saturation, and recovery to baseline.
- `IN-02`: perform an Iran/foreign disconnect exercise against the existing recovery mechanism and
  validate pending/terminal user messages without a new incident workflow.

Exit criteria:

- zero duplicate users/invitations;
- zero unauthorized foreign User creation;
- zero false Telegram OTP success acknowledgements;
- OTP fallback timing and one-code behavior pass;
- sync backlog returns to zero;
- market latency/backlog remains within the accepted baseline;
- the current configured two-server path passes exact signed-transport validation;
- both staging deploy paths leave OTP logging disabled and real OTP delivery observable only through
  redacted state/outcomes;
- staging evidence bundle maps results back to every assigned challenge ID.

### Stage 11 - Owner-Led Manual Acceptance

The owner manually validates at least:

- Web-only registration;
- Telegram-only registration without WebApp;
- Telegram registration followed by Web OTP login;
- OTP received in Telegram and verified before 40 seconds;
- OTP received in Telegram, left unverified, then same code received by SMS;
- immediate SMS when foreign is unreachable;
- Web completion during a Telegram registration outage;
- reconnect reconciliation links existing user;
- wrong Telegram contact rejection;
- role-specific eligibility and Web-only behavior.

Challenges and engineering decisions in this stage:

The owner validates these challenge decisions explicitly:

- `DN-01`, `DN-02`: manager/police invitation and no-OTP behavior under the same universal identity
  and existing inviter controls as other eligible roles;
- `DN-03`: exact invitation/relation expiry equality, two-day copy, and no retroactive setting
  change;
- `DN-04`, `IN-05`: first-wins behavior and all Persian pending/success/rejection copy;
- `DN-05`: Standard/admin/Tier-1 disabled flags and manual sharing, plus unchanged enabled
  accountant/Tier-2 invitation SMS behavior;
- `DN-06`, `IN-02`: terminal rejection/new-invitation copy and outage handling without an override
  workflow;
- `IN-01`: unchanged existing pending-invitation UI and absence of new support tooling;
- `DN-07`: foreign-local no-sync intent retention and normal accepted-product sync behavior;
- `DN-08`, `IN-04`: unchanged provider/cost operations and functional staging readiness;
- `IN-03`: approved background-job health alerts;
- `DT-17`: complete kind/tier/source flag matrix, unchanged enabled templates, and manual-sharing
  workflow for disabled categories;
- `DT-18`, `DT-19`: exact 24-hour policy, terminal rejection, and absence of manual review;
- `IT-03` through `IT-06`, `IT-13`, `IT-16`: relation/market effects, Iran-only new Web
  sessions/home, the approved no-new-password behavior, onboarding gates, unchanged PWA behavior,
  and external dependencies;
- `IN-06`: approved staging order, per-feature rollback, no role restriction, and production gate.

Exit criteria:

- owner explicitly accepts the staging behavior;
- no unresolved severity-1 or severity-2 finding remains;
- any accepted lower-severity issue is documented with an owner decision;
- every non-technical challenge has an approval, runbook/evidence, or explicit production blocker.

### Stage 12 - Production Readiness And Explicit Gate

- Review migrations, feature flags, backups, sync health, Redis persistence, SMS.ir allowlist,
  Telegram gateway health, internal route behavior, observability, and rollback commands.
- Keep feature flags disabled during deploy until post-deploy schema/runtime health is proven.
- Enable narrowly with monitoring and a prepared fail-closed flag rollback.

Challenges and engineering decisions in this stage:

- Audit all `DT-*`, `DN-*`, `IT-*`, and `IN-*` registry entries. None may remain merely `Open`;
  each must be `Closed in Stage N` or explicitly accepted as a documented residual risk by the
  authorized owner.
- Reconfirm `IT-09`, `IT-10`, `IT-11`, `IT-12`: deploy order is backward-compatible,
  backups/restores and test-infrastructure gates were tested, queues are drained at the right
  boundary, and rollback does not delete authoritative data.
- Reconfirm `IT-15`, `IT-16`, `IN-03`: capacity, runtime dependencies, and approved job-health
  alerts are ready; `DN-08`/`IN-04` add no provider/cost operating layer.
- Reconfirm `DN-01`, `DN-02`, `DN-06`, `DN-07`, `IN-01`, `IN-02`, `IN-05`, `IN-06`: uniform role
  eligibility, privacy, no-new-support-surface, incident, copy, rollout, and rollback decisions
  remain valid.

Production must not run until the owner explicitly requests the exact action with the phrase
`production deploy` after all prior gates pass. The explicit phrase authorizes the production
action, not a waiver of an unresolved challenge or failed gate.

## Required Automated Test Matrix

### Meaning of 100 Percent Coverage

"100 percent" is a release gate, not an informal claim. Completion requires all of the following:

1. Every locked product decision `1..74` maps to at least one automated test or an explicitly named
   owner-led acceptance case.
2. Every remaining registry item (`DT-*`, `DN-*`, `IT-*`, `IN-*`) maps to implementation evidence
   and at least one positive, negative, failure, race, migration, security, or regression test where
   software can verify it.
3. Every new/changed Python module reaches 100% changed-line and changed-branch coverage.
4. Every new/changed TypeScript/Vue module reaches 100% changed statement, branch, function, and
   line coverage.
5. Every finite state in Invitation, Telegram FSM, durable intent, command receipt, projection gate,
   OTP delivery, invitation SMS policy, outbox, and background-job models is entered by a test.
6. Every legal state transition and every illegal transition is tested. Illegal transitions must
   fail without partial mutation.
7. Every bounded reason/outcome code is produced and contract-tested on Web and bot adapters.
8. Every database constraint, unique index, server guard, field-authority rule, feature flag, and
   compatibility alias is tested both allowed and denied.
9. Every external boundary has success, explicit failure, timeout, malformed response, disconnect,
   retry, duplicate, replay, restart, and recovery coverage where the boundary permits it.
10. Every unchanged behavior explicitly protected by this roadmap has a regression test: User
    lifecycle, PWA/service worker, current onboarding/tutorial/welcome flow, current provider/template
    contracts, existing outage/recovery, session behavior, Offer/request ownership, and market sync.
11. Mutation testing must cover critical invariants (identity matching, row lock/idempotency,
    expiry/+24h, source field policy, projection gate, OTP one-code/40s/120s, server placement, and
    no-manual-review). No surviving critical mutant is allowed.
12. No skipped, xfailed, quarantined, flaky-retried-to-green, or manually waived required test may
    satisfy this gate.

Required generated artifacts:

```text
tmp/test-evidence/registration-roadmap-traceability.json
tmp/test-evidence/registration-roadmap-coverage.json
tmp/test-evidence/registration-roadmap-state-transitions.json
tmp/test-evidence/registration-roadmap-mutation-report.json
tmp/test-evidence/registration-roadmap-two-server-staging.json
```

The traceability artifact has one row per decision, challenge, state, transition, reason code,
feature flag, schema constraint, and scenario below. A CI check fails if any row has no test/evidence,
if any referenced test does not exist, or if a test result is not passing.

### Exhaustive Parameter Spaces

Parameterized tests must execute the complete valid Cartesian product, not representative pairwise
sampling. Invalid combinations must also be enumerated and rejected explicitly.

| Model | Required dimensions |
|---|---|
| Invitation | kind `standard/accountant/customer/legacy_unknown` x derived state `pending/completed/revoked/expired` x source `web/bot` x creator role `watch/standard/police/middle_manager/super_admin` x current inviter-permission and relation-ownership result `allowed/denied` x existing User `none/exact/conflicting/deleted/inactive` x natural-key reservation `none/exact retry/mobile/account/both` |
| Customer/accountant | kind x customer tier `tier1/tier2` or accountant x Relation `missing/pending/active/expired/revoked/deleted` x arrival order x SMS category flag |
| Telegram identity | contact ownership `owned/forwarded/missing` x normalized mobile `exact/mismatch/malformed` x Telegram ID `free/same User/other User` x invitation eligibility |
| FSM | state `none/contact/address/confirm/ready/terminal` x input type x duplicate input x Redis present/lost x process restart x Invitation state change |
| Reconciliation | current User/Invitation/Relation/Telegram-ID combination x proof time boundary x receipt state x response loss x retry count |
| Sync | table x source server x allowed/disallowed field x version `missing/lower/equal/higher` x event order x duplicate/replay x required projection set |
| OTP | Telegram ID `absent/present` x Telegram result `sent/fail/timeout/malformed/duplicate` x verify time boundary x SMS result x API/job/Redis restart x duplicate request/worker claim |
| Invitation SMS | invitation category `standard/admin/tier1/accountant/tier2` x source surface x category flag x provider result |
| Transport | route server x source header x API key x timestamp boundary x signature x canonical body x command/hash reuse x unknown fields |
| UI | role/kind/state x viewport/mobile/desktop x current/legacy response aliases x timer boundary x network outcome x keyboard-only/screen-reader semantics |

### Deterministic Race And Failure Injection Points

Every listed point must support a deterministic test barrier/failpoint. Sleep-based race tests are
not accepted.

| Flow | Required barriers/failpoints |
|---|---|
| Invitation create | before advisory lock; after first natural-key lock; after User read; after reservation read; after Invitation flush; after Relation flush; after reservation insert; before commit; after commit before response |
| Registration complete | before row lock; after Invitation lock; after natural-key reads; after User flush; after Relation activation; after completion metadata; after receipt/outbox insert; before commit; after commit before HTTP response |
| Reconciliation | before forwarding mark; after foreign intent commit; before Iran receipt claim; concurrent same command; response lost; foreign result update failure; sync visible before/after result |
| Sync apply | User before Invitation; Invitation before User; Relation last; every pairwise table order; stale version before/after new; duplicate version; process stop between batch rows |
| OTP | before Telegram request; after Telegram acceptance before acknowledgement; verify at claim boundary; claim before verify; verify before claim; after SMS provider acceptance before Redis result; Redis/API/leader restart at each state |
| Outbox | before outbox insert; after insert before commit; commit then process crash; provider accept then response loss; duplicate delivery; dedupe receipt write failure |

Required two-party races:

- Web vs Web completion;
- Telegram vs Telegram same intent and distinct duplicate intents;
- Web vs Telegram completion;
- completion vs revocation;
- completion vs natural expiry boundary;
- exact retry vs conflicting invitation create;
- OTP verify vs fallback claim;
- two background-leader instances during lease handoff;
- sync new-version event vs stale replay;
- outbox delivery vs duplicate replay.

### Mandatory Test Suites And Stable Scenario IDs

Every row below is mandatory. Each scenario ID expands over every applicable dimension in the
Exhaustive Parameter Spaces table. The traceability report must list the concrete parameter tuple,
not only the parent scenario ID. A single broad test name cannot satisfy multiple rows unless its
machine-readable parameter output proves that every tuple ran and passed.

#### Schema, migration, and rollback

| ID | Required test and assertions |
|---|---|
| `MIG-001` | Upgrade an empty database and a production-shaped snapshot; verify every new table, column, enum/check constraint, foreign key, index, partial unique index, default, and nullability rule. |
| `MIG-002` | Run the upgrade twice and through the supported mixed-version deploy sequence; prove migration idempotency or the framework's deterministic already-applied behavior. |
| `MIG-003` | Backfill Invitation kind using relation evidence first and prefix fallback second for every unambiguous combination; conflicting or missing evidence must become `legacy_unknown`. |
| `MIG-004` | Backfill completion metadata for exact one-User evidence only; zero, multiple, deleted, inactive, mobile-conflicting, or account-conflicting matches remain conservative. |
| `MIG-005` | Backfill active identity reservations for every valid pending kind and reject/report every mobile, account, or combined collision without row-order winner selection. |
| `MIG-006` | Exercise all completion-field constraints with all-null, all-valid, and every partial-null combination; invalid rows must be rejected atomically. |
| `MIG-007` | Prove all new async creation paths use `get_trading_settings_async()` from `core.trading_settings`, whose effective accepted value is two days; the one-day `core.config` default and sync compatibility accessor cannot affect lifetime. Verify Invitation/Relation expiry equality, setting changes, legacy rows, leap-day/month/year boundaries, and UTC conversion using the existing project time standard. |
| `MIG-008` | Restore a backup containing each intent, receipt, outbox, reservation, invitation, relation, and OTP state; verify state, ownership, idempotency, and due-job recovery. |
| `MIG-009` | Execute the documented schema/feature rollback path after zero, partial, and full traffic; authoritative User/Invitation/Relation data must remain intact and old binaries must remain compatible. |
| `MIG-010` | Verify registration intent is explicitly absent from generic sync registry/parity requirements while accepted User/Invitation/Relation changes continue through existing sync. |

#### Invitation creation and SMS policy

| ID | Required test and assertions |
|---|---|
| `INV-001` | Execute every allowed and forbidden creator-role x invitation-kind x customer-tier x Web/bot source tuple; authorization and rejection must match the existing inviter rules. |
| `INV-002` | For every tuple, verify generated Web/bot links, token identity, explicit v2 fields, legacy aliases, masked public response, and full authenticated-admin response. |
| `INV-003` | Exact retry by the same authorized creator returns the same Invitation; changed owner, kind, tier, mobile, account, expiry, or payload produces a safe conflict without token disclosure. |
| `INV-004` | Concurrent creates at every lock barrier for same mobile, same account, both keys, and independent keys produce one deterministic reservation per natural key and no partial Relation. |
| `INV-005` | Pending, completed, revoked, expired, deleted/inactive-User, and legacy-ambiguous invitations are each accepted or rejected according to the locked state table. |
| `INV-006` | Starting from fixtures proving every category currently invokes SMS, Standard/admin and tier-1 send no SMS while their flag is false; accountant and tier-2 preserve the current template/provider behavior; each flag affects only its category. |
| `INV-007` | SMS policy covers success, explicit provider failure, timeout/ambiguous acceptance, malformed provider response, retry, duplicate command, restart, and outbox replay without duplicate logical sends. |
| `INV-008` | Completion, revocation, and expiry release reservation in the same transaction; injected failure at every write/commit boundary leaves either the complete old state or complete new state. |

#### Telegram registration FSM and identity proof

| ID | Required test and assertions |
|---|---|
| `BOT-001` | Deep links with valid, missing, empty, malformed, unknown, expired, revoked, consumed, legacy-ambiguous, and unauthorized tokens enter the exact allowed state or safe terminal response. |
| `BOT-002` | Contact proof covers sender-owned contact, forwarded contact, another user's contact, missing contact, typed text, Telegram payload without phone, and Telegram user-ID mismatch. |
| `BOT-003` | Mobile normalization covers `09`, `989`, `+989`, Persian/Arabic digits, spaces/separators, leading/trailing whitespace, too short/long, non-Iranian, and malformed input; bot and Web shared validation must agree. |
| `BOT-004` | Telegram ID free, already linked to the same User, linked to another active/inactive/deleted User, and concurrently claimed all return deterministic outcomes without leaking identity. |
| `BOT-005` | Address validation tests empty, non-text, 9 characters, exactly 10, over 10, whitespace/control/Unicode input, duplicate delivery, and direct adapter bypass against the exact existing Web rule. |
| `BOT-006` | Every FSM state receives every input/callback category, duplicate update, out-of-order update, stale callback, `/start` restart, process restart, and Redis loss; illegal transitions cause no durable mutation. |
| `BOT-007` | Crash before and after durable intent commit and before/after FSM clear proves commit-before-clear, one intent, resumability, and no foreign User/session creation. |
| `BOT-008` | Projection completion triggers the two current educational messages, acknowledgements, welcome message, channel link, WebApp link, and channel-join controls in the exact current order and conditions. |

#### Authoritative registration and reconciliation

| ID | Required test and assertions |
|---|---|
| `REG-001` | Web-only, Telegram-only, Web-then-Telegram, Telegram-then-Web, exact simultaneous completion, and distinct duplicate intents result in one Iran-owned User and one terminal Invitation outcome. |
| `REG-002` | Every existing-User state and mobile/account/Telegram-ID match or conflict tuple returns the bounded created/linked/idempotent/rejected reason code with no unauthorized overwrite. |
| `REG-003` | Standard/admin/police, tier-1 customer, accountant, tier-2 customer, inactive, and deleted role/tier combinations apply the approved bot eligibility and relation requirements. |
| `REG-004` | Every registration failpoint rolls back User, Invitation, Relation, reservation, receipt, and outbox together; after-commit response loss resolves through the same receipt. |
| `REG-005` | Same command and canonical payload returns prior result; same command with changed payload rejects; two commands for the same identity serialize to one authoritative outcome. |
| `REG-006` | Natural expiry boundaries at just before, exactly, and just after expiry and expiry+24h are tested with revocation before/at/after each point; revocation always wins. |
| `REG-007` | Iran unavailable before proof, after proof, after durable intent, during request, after commit, and during response updates produces only the approved pending/resume/terminal behavior. |
| `REG-008` | Web creates exact or conflicting identity during outage before every reconciliation barrier; reconnect links exact identity or rejects conflict without blind creation. |
| `REG-009` | Reconciliation job is constructed/runs only on foreign; duplicate leaders, lease handoff, bounded batch, backoff, poison item, restart, and starvation tests prove forward progress and isolation. |
| `REG-010` | Both service adapters pass through a literal `home_server=iran` assignment independent of Host, source headers, `_login_home_server`, and the model default; foreign creates no UserSession/JWT/refresh token and later Web login creates only the existing Iran-local session type. |

#### Sync, field authority, and projection gating

| ID | Required test and assertions |
|---|---|
| `SYN-001` | For every synced User field, test allowed source write and disallowed source write; disallowed full-row legacy and patch events are dropped/redacted without overwriting Iran authority. |
| `SYN-002` | Missing, lower, equal, and higher sync versions arrive in every order with duplicate/replay/restart; only the newest authorized value survives and onboarding progress never regresses. |
| `SYN-003` | User, Invitation, and each required Relation arrive in every permutation and with each item absent; activation occurs exactly once only when the kind/tier-specific projection is complete. |
| `SYN-004` | Standard/admin/police projection never waits for a nonexistent Relation; tier-1 customer projection always waits for its required Relation. |
| `SYN-005` | Generic User patches containing counters, market/accounting ownership, role/address, Telegram data, unknown fields, or no-op values obey their existing domain authority and emit no raw PII. |
| `SYN-006` | Sync interruption between every batch row, redelivery after commit, stale replay after newer event, and foreign dedupe-receipt loss produce no duplicate onboarding notification or logical enqueue. |
| `SYN-007` | Event builders, receiver apply, field policy, registry, parity comparators, reset/fixtures, backup verification, and the mixed-version flag are tested with old unversioned and new versioned peers; after the declared compatibility gate closes, unversioned events fail closed. Existing Offer/request home fields, market data, messenger exclusion, session ownership, and unrelated table parity remain unchanged. |

#### WebApp OTP delivery and fallback

| ID | Required test and assertions |
|---|---|
| `OTP-001` | Request with absent/present Telegram ID, foreign reachable/unreachable, acknowledged/failure/timeout/malformed Telegram response selects immediate SMS or scheduled fallback exactly as approved. |
| `OTP-002` | One cryptographically generated OTP and one original 120-second expiry are shared across Telegram and SMS; repeat requests during TTL create neither a new code nor extended expiry. |
| `OTP-003` | Verification at `t=0`, just before/at/just after 40 seconds, and just before/at/just after 120 seconds covers verify-before-claim, claim-before-verify, and simultaneous barrier races. |
| `OTP-004` | Scheduled fallback sends the same code once after 40 seconds when unverified; verified, expired, already-SMS-accepted, or non-Telegram deliveries become no-ops. |
| `OTP-005` | Legacy manual SMS endpoint before/at/after 40 seconds reads the same `otp:{mobile}` code-of-record and structured request/TTL, atomically suppresses later fallback, and races a sorted-set due claim at the exact same deterministic barrier without duplicate acceptance. |
| `OTP-006` | SMS success, explicit failure, timeout/ambiguous acceptance, malformed response, provider disconnect, Redis failure, API restart, and leader restart preserve fail-closed verification and duplicate policy. |
| `OTP-007` | Two Iran leader instances, lease handoff, duplicate due rows, and provider-accepted/response-lost barriers prove one atomic claim and no automatic ambiguous resend. |
| `OTP-008` | Job factory and execution are Iran-only; bot registration/login has no OTP path; no foreign OTP fallback job can mutate state. |
| `OTP-009` | Login UI preserves current copy, counts from 40 to zero without drift/reflow, exposes no manual resend action at zero, and fallback still occurs when page is hidden, closed, refreshed, or offline. Also cover the existing 429-detail digit-extraction path with Persian/ASCII digits, changed detail without digits, and the active-code response so copy changes cannot seed a wrong countdown. |
| `OTP-010` | Correct code verifies once; wrong, malformed, expired, replayed, cross-user, cross-request, and brute-force/rate-limited attempts fail without account or code leakage. |

#### Transport, API contracts, and security

| ID | Required test and assertions |
|---|---|
| `SEC-001` | Internal endpoints reject missing/wrong API key, source server, timestamp, signature, canonical body, content type, method, route, and unknown fields before mutation. |
| `SEC-002` | Timestamp just inside/at/outside replay window, duplicate signature, command replay, changed-body replay, body/header mutation, redirect, and proxy stripping have deterministic outcomes. |
| `SEC-003` | Registration authority route is absent/forbidden on foreign; Telegram delivery route and bot runtime are absent/forbidden on Iran; wrong-server background jobs cannot be constructed. |
| `SEC-004` | Public invitation lookup is rate-limited, non-cacheable, enumeration-resistant, and masked for found/not-found/conflict; authenticated authorized response alone exposes full links. |
| `SEC-005` | Logs, metrics, traces, exceptions, receipts, test artifacts, and alerts contain no raw OTP, token, mobile, address, Telegram ID, secret, signature, or provider credential. Fresh and repeated staging deploy tests prove `STAGING_LOG_OTP_CODES=false`, reject Telegram-OTP mode with it true, and prove real delivery is not short-circuited. |
| `SEC-006` | Contract v1 and v2 request/response fixtures cover every success/reason code, explicit availability/SMS fields, legacy aliases, omitted/extra/null fields, and mixed old/new peers. |
| `SEC-007` | Rate limits and authorization cover anonymous, user, inviter, manager, super-admin, cross-owner, inactive, and deleted principals without introducing a force-create/link/review path. |
| `SEC-008` | Empty, malformed, HTTP outside local test, Iran-host, and each configured foreign Web/API hostname value for `PUBLIC_WEBAPP_URL` are tested; only the canonical valid Iran HTTPS URL may generate user-facing links, and every invalid value fails closed without emitting a link. |

#### UI, regression, load, and real staging

| ID | Required test and assertions |
|---|---|
| `E2E-001` | Web and bot registration flows run for every eligible role/kind/tier and terminal state across supported desktop/mobile viewports and the project's supported browser matrix. |
| `E2E-002` | Keyboard-only, focus order, screen-reader labels/live timer, RTL layout, long Persian text, slow/offline network, refresh, back navigation, and duplicate click/update do not bypass or obscure a state. |
| `E2E-003` | Existing pending-invitation Web UI, PWA/service worker behavior, session reset/login, deletion/recovery/reinvite, channel gating, and all unchanged onboarding snapshots remain unchanged. |
| `OPS-001` | Feature flags independently enable/disable bot registration, reconciliation, OTP fallback, and each SMS category; startup defaults are fail-closed and rollback does not strand or delete state. |
| `OPS-002` | Registration and OTP jobs use the approved existing background leader with bounded batch/concurrency, async yielding, no transaction across HTTP, heartbeat, lag, and oldest-age signals. |
| `OPS-003` | Idle, expected, burst, backlog, provider-slow, sync-slow, and recovery loads measure market/sync p50/p95 and event-loop lag; over 10% market p95 degradation fails the gate. |
| `OPS-004` | Long soak with duplicate traffic, leader churn, API/bot/Redis/PostgreSQL restart, network partition, and reconnect ends with zero duplicate users/invitations, zero stuck claims, and drained backlog. |
| `STG-001` | Real two-server staging executes each disconnect and response-loss point, validates signed transport, clock boundaries, actual Telegram/SMS provider outcomes, and records redacted evidence. |
| `STG-002` | Mixed-version deploy, migration, flags-off validation, narrow enablement, rollback, backup/restore, and redeploy execute in the exact supported operational order. |

### Property, Fuzz, Mutation, And Regression Gates

- Property-based generators must produce valid and invalid mobile/account/token/address/command/time
  data, shrinking every failure to a reproducible fixture. The invariant is never more than one
  authoritative User, active natural-key reservation, terminal command result, logical outbox event,
  or accepted SMS fallback for the same identity/request.
- Stateful model-based tests must compare the implementation with the documented Invitation, FSM,
  durable-intent, projection, OTP, receipt, and outbox transition models across randomized legal and
  illegal sequences, including restart and replay between every action.
- Schema/API fuzzing must cover malformed JSON, oversized bounded inputs, unexpected Unicode/control
  characters, duplicate keys, null/omitted/unknown fields, incorrect types, and truncated payloads;
  it must remain bounded and must never expose stack traces, secrets, or partial writes.
- Mutation testing must delete/invert each critical guard, lock, state predicate, expiry comparison,
  source-field allowlist, server guard, dedupe key, OTP timing predicate, and transaction boundary.
  At least one required test must kill each mutant; surviving critical mutants block release.
- Existing full backend, frontend, bot, sync, auth/session, invitation, relation/customer, market,
  onboarding, PWA, migration, and two-server suites remain required. New focused tests supplement
  them and may not replace, skip, or weaken them.
- Flake verification reruns concurrency, timing, restart, and E2E suites under randomized execution
  order and the project's supported parallelism. Any nondeterministic result blocks the gate until
  the race is made deterministic; retries cannot convert failure to acceptance.

### Coverage Enforcement And Exit Evidence

- CI measures diff coverage against the branch point from `main` and fails below 100% for every
  changed executable line and branch. Generated, migration metadata-only, and framework bootstrap
  exclusions require an explicit line-level justification in the traceability artifact.
- Unit coverage alone is insufficient: every scenario ID must have the required integration,
  concurrency, E2E, migration, or real-staging evidence appropriate to its boundary.
- The state-transition artifact lists all model states and transitions, test IDs, observed outcomes,
  and whether each transition is legal. Set equality with the roadmap model is required.
- The final evidence bundle contains commands, immutable commit/image identifiers, server placement,
  migration versions, feature-flag values, sanitized logs, timings, coverage reports, mutation
  results, and checksums. Evidence from another commit cannot satisfy the gate.
- Release is blocked if any scenario tuple is absent, skipped, flaky, manually waived, only mocked
  where real staging is required, or inconsistent with current project rules.

### Registration core

| Scenario | Expected result |
|---|---|
| Web registration only | One Iran User, invitation consumed via Web, sync to foreign |
| Telegram registration only | One Iran User with Telegram ID, no Web session, sync to foreign |
| Telegram-created manager later opens WebApp | OTP login succeeds without forced password setup |
| Telegram-created User record | `home_server=iran`; no session/JWT/refresh token is created on foreign |
| Telegram-created User later logs into WebApp | Iran OTP login creates only an Iran-local UserSession |
| Existing User and Offer ownership after rollout | No automatic User-home migration; Offer/request home fields unchanged |
| Web and Telegram finalization race | One User; deterministic created/linked result |
| Duplicate Telegram command | Same receipt/result; no duplicate mutation |
| Lost Iran response then retry | Same result; no duplicate mutation |
| DB failure after User flush | Full rollback, invitation still pending |
| Sync delay after Iran commit | Bot remains pending until local User appears |
| User arrives before required relation | No bot/market activation until projection is complete |
| Standard/admin/police User and completed standard Invitation arrive | Activate without waiting for a nonexistent Relation |
| Tier-1 User arrives before customer Relation | Remain pending despite Standard User role |
| Stale/duplicate lower `sync_version` arrives | Ignore without overwriting newer row state |
| Stale foreign full-User event | Iran-owned fields are not overwritten |
| Foreign onboarding patch includes role/address | Unauthorized fields dropped and redacted security event recorded |
| Onboarding step 2 followed by stale step 1 | Stored completion remains step 2; no regression |
| Direct Telegram registration becomes projection-complete | Existing linked-account/panel and channel-join flow starts unchanged |
| Existing channel join and tutorial acknowledgements | Same two messages, order, conditions, callbacks, and completion behavior |
| Generic User patch contains trade counters | Counters ignored/rejected; existing domain authority remains unchanged |
| Foreign legacy link-token completion | Iran-authoritative link service decides; no foreign User mutation |
| Concurrent invitation creation across kinds/owners | Chosen uniqueness policy returns deterministic non-duplicate result |
| Same authorized creator retries identical invitation payload | Existing invitation returned idempotently |
| Same mobile/account collides with different owner/kind/payload | Safe conflict; no other token/link disclosed |
| Invitation completes/revokes/expires | Iran-local identity reservation is released transactionally |
| Customer/accountant invitation creation | Relation expiry exactly equals stored Invitation expiry |
| Expiry setting changes after creation | Existing Invitation/relation timestamps remain unchanged; new rows use the new value |
| Standard/admin invite from bot or Web | SMS category disabled; no SMS.ir request; admin receives links |
| Tier-1 customer invite from bot or Web | SMS category disabled; no SMS.ir request; same manual-sharing behavior |
| Accountant invite from Web | Existing accountant invitation SMS template is invoked |
| Tier-2 customer invite from Web | Existing customer invitation SMS template is invoked |
| Any category flag is toggled later | Only that category changes; other invitation SMS behavior is unaffected |

### Telegram identity and roles

| Scenario | Expected result |
|---|---|
| Sender-owned matching contact | Continue |
| Forwarded contact | Reject |
| Typed mobile instead of contact | Reject/ignore |
| Contact mobile mismatch | Reject |
| Telegram address shorter than 10 characters | Same rejection message and behavior as Web registration |
| Direct adapter bypasses client-side address check | Iran shared service rejects under the same minimum-10 rule |
| Telegram ID already on another User | Reject |
| Identity conflict response | Bounded `identity_conflict` copy; no other account identifier leaks |
| Standard/police/manager | Eligible only according to approved invitation permissions |
| Super admin | Eligible under the same valid-invitation and identity controls as other eligible roles |
| Tier-1 customer | Eligible |
| Watch/accountant/tier-2 | Web-only |
| Inactive/deleted | Reject |

### Outage reconciliation

| Scenario | Expected result |
|---|---|
| Iran unavailable before intent ready | Resume collection safely |
| Iran unavailable after intent ready | Durable pending intent, no User, no full access |
| Redis FSM lost before user confirmation | No durable intent/User exists; user safely restarts |
| Bot stops after intent commit but before FSM clear | Idempotent resume finds the one durable intent |
| Worker/bot restart with ready intent | PostgreSQL intent resumes without recollecting or duplicating User |
| Web creates same User during outage | Reconnect links Telegram to existing User |
| Web creates conflicting account during outage | Terminal reject with safe conflict code; no overwrite |
| Invitation revoked during outage | Reject |
| Valid intent arrives at/before natural expiry + 24h | Full reconciliation may auto-create/link if every check passes |
| Valid intent arrives after natural expiry + 24h | Terminal expired rejection; require a new invitation/contact proof |
| Invitation revoked inside the 24h grace | Reject; revocation always wins |
| Intent completed after expiry | Reject |
| Iran commits, response lost | Idempotent retry resolves prior result |

### OTP delivery

| Scenario | Expected result |
|---|---|
| No Telegram ID | Immediate SMS |
| WebApp login OTP delivery through Telegram acknowledged, verify at 20s | No SMS fallback |
| WebApp login OTP delivery through Telegram acknowledged, no verify at 40s | Same OTP sent by SMS |
| Telegram send explicit failure | Immediate SMS |
| Telegram send timeout/ambiguous | Immediate SMS; duplicate cross-channel delivery allowed |
| Stale client calls legacy SMS endpoint at 15s | Same code/TTL; accepted SMS makes scheduled fallback no-op |
| Repeat OTP request during TTL | No new code |
| Verify after SMS fallback | Same code accepted once |
| Verify replay | Reject |
| OTP expires before due background job handles it | No SMS |
| API restart at 20s | Fallback still runs if pending/unexpired |
| Leader failover/duplicate job instance | Atomic claim allows one SMS provider acceptance attempt |
| Verify races with fallback claim | Atomic state decision; no new/different OTP |
| Target Web UI after Telegram delivery | Existing copy; 40-second timer; no manual resend action at zero |
| Telegram-method UI timer reaches zero with page open/closed | Iran job sends same OTP independently of frontend |
| Blocking/slow SMS provider | API and due-job latency remain within accepted bounds |
| SMS.ir timeout/ambiguous result | Record ambiguous; do not automatically resend the same OTP SMS |
| Restart with unclaimed due OTP | Existing background leader resumes and atomically claims pending work |
| Restart after provider outcome became ambiguous | No automatic duplicate provider attempt |

### Surface and security guards

| Scenario | Expected result |
|---|---|
| Bot runtime on Iran | Startup fails closed |
| Telegram internal delivery endpoint on Iran | Route unavailable/forbidden |
| Registration authority endpoint on foreign | Route unavailable/forbidden |
| Missing/invalid signature | Reject |
| Replayed timestamp/command | Reject or return idempotent prior result as contract permits |
| Same command ID and canonical payload after lost response | Return prior committed result |
| Same command/idempotency identity with changed payload | Terminal reject without mutation |
| Receipt insert/finalization transaction fails | User/Invitation/Relation mutation fully rolls back |
| Registration commits but notification delivery fails | User remains committed; one outbox event retries |
| Lost response causes command retry | No second outbox event or bot success message |
| Sync replay redelivers notification event | Foreign dedupe receipt suppresses second logical enqueue |
| Current Nginx/internal path strips signed header or mutates body | Validation fails; staging gate blocks release |
| Foreign WebApp URL | Must not serve user-facing registration |
| Log scan for OTP/token/mobile/address/Telegram ID | No raw sensitive values |
| Public invitation lookup/validate | Mobile masked, `Cache-Control: no-store`, and bounded rate limit |
| Authenticated admin invitation response | Full generated links available for manual sharing |
| Contract-v1 client against v2 response | Legacy `link`/`short_link` aliases retain prior meaning |
| Contract-v2 client | Uses explicit links/availability/state/SMS fields only |
| Internal signed payload has unknown field | Strict schema rejects without mutation |
| Unknown internal-command field | Reject without mutation |
| Registration/OTP job evaluated on wrong server | Job factory is absent/disabled by server guard |
| Admin searches for review/force-create/link action | No review or mutation surface exists; authoritative rejection cannot be bypassed |

### Lifecycle, compatibility, and operations

| Scenario | Expected result |
|---|---|
| Legacy invitation with ambiguous kind/completion | Terminal `legacy_state_ambiguous` rejection; no fabricated truth |
| Pending invitation deletion | Row remains with `revoked_at`; history is not hard-deleted |
| Partial completion metadata write | Database constraint rejects the inconsistent state |
| Existing User deletion/recovery/reinvite flows | Behavior remains unchanged; no new bot lifecycle action exists |
| Existing PWA behavior after rollout | No service-worker/deprecation redesign; approved API aliases preserve required compatibility |
| New PWA with old API during mixed deploy | Feature remains disabled/safe until compatible peer is ready |
| Backup and restore with pending intents/receipts | State and idempotency outcomes survive as designed |
| Generic sync registry sees registration intent | Table is explicitly `no-sync`; accepted User/Invitation/Relation still sync normally |
| Registration/OTP load during market activity | Market sync latency/backlog stays within accepted baseline |
| Combined load exceeds 10% market p95 degradation | Staging gate fails and new jobs remain disabled |
| Registration backlog grows | Batch remains bounded; market job is not starved |

## Staging Performance And Timing Targets

- Online Telegram registration command acknowledgement: target under 2 seconds excluding user
  input.
- Iran commit to foreign User visibility: target about 2 seconds in healthy mode; record p50/p95.
- Bot activation after authoritative commit: only after synced state is confirmed.
- Telegram OTP delivery acknowledgement: short bounded timeout suitable for immediate SMS fallback.
- Automatic SMS fallback claim: 40 seconds from confirmed Telegram send, with a staging-measured
  scheduler tolerance target of no more than 2 seconds.
- OTP total expiry: 120 seconds from initial generation; no channel resets it.
- Delayed Telegram registration auto-reconciliation: no later than exactly 24 hours after natural
  invitation expiry, using the project's existing Iran UTC time helper and synchronized clocks.

Timing misses must be observable. They must not weaken identity, idempotency, or no-duplicate rules.

## Migration And Backfill Policy

- Invitation creation stops reading `core.config.settings.invitation_expiry_days`; the existing
  async `core.trading_settings.get_trading_settings_async()` value is the sole source for new stored
  expiry timestamps.
- New Invitation completion fields are nullable for legacy rows.
- Backfill explicit invitation kind from exact relation evidence first and prefix second; ambiguous
  rows become `legacy_unknown` and are terminally rejected for new completion.
- Populate Iran-local identity reservations only for unambiguous, currently pending invitations.
  Produce a collision report for duplicate active natural keys and keep the feature disabled until
  each collision has a deterministic terminal outcome; never choose a winner by row order alone.
- Backfill `registered_user_id` only when exact invitation mobile/account evidence identifies one
  non-deleted User without ambiguity.
- Ambiguous legacy used invitations remain unbackfilled and require conservative runtime handling.
- Do not fabricate `completed_via` for historical rows without evidence.
- Do not delete historical invitations, users, sessions, sync rows, or Telegram link tokens.
- New local-only tables must be explicitly excluded from sync and parity expectations.
- User sync changes must be additive and field-filtered so mixed versions cannot replay foreign-owned
  snapshots into Iran-owned identity fields.
- Migration rollback must not delete User/Invitation data created after feature enablement.

## Rollback Strategy

Rollback is feature-disable first, not data deletion.

1. Disable `TELEGRAM_DIRECT_REGISTRATION_ENABLED` to restore Web-only invitation completion.
2. Disable reconciliation intake while preserving already-ready intents as durable state.
3. Disable `TELEGRAM_LOGIN_OTP_ENABLED` to use immediate SMS-only delivery.
4. Disable the automatic fallback background job independently if it misbehaves; immediate SMS-only delivery
   remains. A legacy resend endpoint is a temporary compatibility path, not the target rollback UI.
5. Preserve invitation completion metadata, command receipts, intents, and users.
6. Do not unlink Telegram IDs or reverse completed registrations automatically.
7. Re-enable only after root cause and replay/idempotency safety are proven in staging.
8. Never re-enable raw OTP logging as a rollback mechanism; staging uses dev login for unrelated
   automation and real redacted delivery evidence for OTP acceptance.

## Operational Prerequisites

- Iran and foreign peer URLs point to the approved live sync/internal transport surface.
- TLS verification succeeds in both directions.
- Current Nginx/internal route rules keep registration and OTP endpoints non-cacheable and preserve
  signed headers/body exactly.
- Iran Redis persistence and health are verified for 120-second OTP/fallback state.
- Foreign Redis and PostgreSQL persistence are verified for FSM and ready intents.
- SMS.ir API key IP restrictions include the active Iran egress IP before OTP/SMS validation.
- Telegram Bot API access and central gateway are healthy on foreign.
- Both databases have zero relevant sync backlog before enabling each stage.
- Clocks are synchronized closely enough for signed timestamp validation and 40-second scheduling.
- Staging environment generation and redeploy both enforce `STAGING_LOG_OTP_CODES=false` before real
  OTP delivery tests.
- The pinned test-only dependencies and CI runners required by `IT-12` are available without being
  installed in production runtime images.
- Existing operational ownership covers incidents and alerts; current database/backup
  retention behavior is verified for the new local tables.

## Definition Of Done

This roadmap is complete only when all of the following are true:

1. Both invitation URLs complete their advertised registration flow.
2. Telegram-first eligible users never need to open the WebApp.
3. Telegram-first registration uses no OTP and requires sender-owned matching contact.
4. Every final User/invitation mutation is decided and committed on Iran.
5. Foreign never creates a provisional User during outage.
6. Reconnection reconciles current truth and links a Web-created user when appropriate.
7. A valid delayed intent auto-reconciles only through natural expiry plus 24 hours; later intents
   are terminally rejected and require a new invitation; explicit revocation always rejects.
8. No tested race/outage/retry creates duplicate users or overwrites authoritative fields.
9. Telegram-registered users later require OTP for Web login.
10. Telegram and SMS deliver one synchronized OTP with one TTL.
11. An unverified WebApp login OTP delivered through Telegram triggers same-code SMS fallback after
    40 seconds.
12. Telegram failure/ambiguity triggers immediate same-code SMS.
13. Verify success cancels/no-ops pending SMS fallback.
14. Existing LoginView copy remains unchanged; Telegram-method timer is 40 seconds, zero shows no
    manual resend action, and automatic SMS is independent of the page while OTP TTL remains 120s.
15. OTPs and sensitive registration data do not leak into logs, metrics, sync, or durable
    notification payloads.
16. User/relation/invitation projection ordering never grants premature bot, channel, or market
    access.
17. Every challenge registry entry has a decision record and closure evidence or an explicitly
    authorized residual-risk acceptance; no unresolved production blocker remains.
18. No manual-review state, queue, endpoint, admin action, worker retry, or force-create/link path
    exists; invalid/conflicting/ambiguous cases fail closed with safe terminal codes.
19. Automated two-server staging matrix passes.
20. Owner-led manual staging acceptance passes, including the current configured two-server path.
21. Production remains untouched until explicit final approval.
22. Every invitation creation path snapshots the accepted two-day value from
    `core.trading_settings`; the obsolete one-day config default cannot affect a new Invitation.
23. New registration notifications reuse `telegram_notification_outbox`; no parallel outbox or
    background-worker service exists.
24. Fresh and repeated staging deploys keep raw OTP logging disabled while real Telegram/SMS delivery
    and unrelated dev-login automation both remain testable through their separate paths.
25. Changed-code coverage, property/fuzz, deterministic race, mutation, state-transition, and
    traceability CI gates are executable and pass with no required-case waiver.
