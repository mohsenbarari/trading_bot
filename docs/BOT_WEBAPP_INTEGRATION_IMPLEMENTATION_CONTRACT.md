# Bot/WebApp Integration Implementation Contract

Date: 2026-06-18

Branch: `candidate/bot-webapp-integration`

This contract turns `docs/BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md` into an implementation
sequence. The roadmap is the source of policy and risk; this file is the execution contract for
coding, tests, commits, staging validation, and stop/go gates.

## Contract Status

- Sections 16 and 17 of `BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md` are accepted as part of
  the implementation baseline.
- `BOT_WEBAPP_INTEGRATION_PREFLIGHT_FRESHNESS_REPORT.md` is complete.
- The candidate branch must be refreshed from current `main` before code implementation starts.
- Refresh method recommended by the preflight report: merge `main` into
  `candidate/bot-webapp-integration`.
- Branch refresh still requires explicit owner approval before it is performed.

## Global Rules For Every Step

1. Work only on `candidate/bot-webapp-integration`.
2. Check the branch with `git branch --show-current` before every edit, test run with side effects,
   commit, push, staging deploy, or validation action.
3. Do not merge into `main`, any WebApp fix branch, any other candidate branch, or any release
   branch unless the owner explicitly requests that merge.
4. Do not run production actions. Production deploy requires all final gates and the exact owner
   approval phrase documented in the roadmap.
5. Keep each implementation step independently reviewable. A step must end with a clean worktree,
   a focused commit, pushed branch, and a short verification note.
6. Do not mix unrelated WebApp fixes, market-history work, messenger work, or production cleanup into
   this branch.
7. Prefer test-first changes for policy/guard behavior. If a behavior cannot be safely tested first,
   document the reason in the commit summary or follow-up report.
8. Any step that discovers a policy conflict must stop at a documented finding. Do not resolve policy
   conflicts by assumption.
9. Any schema migration must be non-destructive unless the owner explicitly approves a destructive
   cleanup.
10. Staging is the only runtime validation target for this contract until a future explicit
    production approval.

## Step Completion Definition

A step is complete only when all of these are true:

- Scope stayed inside the step boundaries below.
- Branch was checked before edits and before commit.
- Tests listed for the step were run, or the reason for not running them is documented.
- `git diff --check` passes.
- No unrelated files are staged.
- Commit message names the step intent.
- Branch is pushed to `origin/candidate/bot-webapp-integration`.
- The next step's start condition is still true.

## Step 0 - Refresh Candidate From Main

Goal: make implementation start from current code instead of the stale roadmap-only branch.

Start condition:

- Owner explicitly approves merging or rebasing current `main` into
  `candidate/bot-webapp-integration`.

Default action:

- Merge `main` into `candidate/bot-webapp-integration`.

Why merge, not rebase:

- The candidate branch is already pushed.
- The roadmap has a visible decision history that should not be rewritten without a specific reason.

Deliverables:

- Merge commit or documented no-op if branch state changes before execution.
- Updated freshness status after the merge.
- Conflict notes if any semantic or textual conflict appears.

Verification:

- `git branch --show-current`
- `git status --short`
- `git rev-list --left-right --count main...HEAD`
- `git diff --check`
- Run the smallest existing backend test subset that exercises offers/trades/sync imports after the
  merge. Exact test names may change after refresh; choose the current equivalent tests.

Exit criteria:

- Branch is refreshed from current `main`.
- Worktree is clean.
- No conflict markers remain.
- Push completed.

Do not start Step 1 before Step 0 is complete.

## Step 1 - Source Surface And Offer Home Server Contract

Goal: make offer authority explicit and testable before shared service refactors.

This is one implementation step because it is narrow, high-value, and low blast radius.

Required behavior:

- Bot-created offers always use `offer_home_server=foreign`.
- WebApp/API-created offers always use `offer_home_server=iran`.
- Sync-created offers preserve the incoming `Offer.home_server` and never recompute it from local
  runtime state.
- `user.home_server` must not decide `Offer.home_server`.
- The same user can create a Bot offer and then a WebApp offer with different offer home servers.

Likely code areas:

- `api/routers/offers.py`
- `bot/handlers/trade_create.py`
- `models/offer.py`
- `schemas.py`
- new or existing constants/enums for `source_surface`
- existing offer creation tests

Required tests:

- WebApp offer creation sets `home_server=iran` even when `owner_user.home_server=foreign`.
- Bot offer creation sets `home_server=foreign` even when the user row says `home_server=iran`.
- Same-user dual-surface scenario: one Bot offer and one WebApp offer keep different home servers.
- Sync/internal creation preserves incoming `home_server`.
- Invalid or missing `source_surface` fails at the service/adapter boundary where practical.

Exit criteria:

- `source_surface` is represented by a shared constant/enum or equivalent strongly named contract.
- Existing behavior still passes for normal WebApp offer creation.
- Tests fail before the behavior change and pass after it where feasible.

## Step 2 - Sync Registry Starter And Inventory Gate

Goal: make sync coverage explicit before broad sync behavior changes.

This step is large enough to split into two sub-steps. Complete 2A before 2B.

### Step 2A - Registry Skeleton And Inventory Test

Required behavior:

- Every SQLAlchemy model table is classified as `sync`, `no-sync`, or `internal-bookkeeping`.
- The registry records at least: table name, policy, write surfaces, authority, conflict rule, and
  side-effect classification.
- A test fails when a model table exists without a registry entry.

Likely code areas:

- new `core/sync_registry.py` or existing sync policy module
- `models/`
- sync tests

Required tests:

- Inventory test enumerates model `__tablename__` values.
- Registry coverage test fails for missing model tables.
- Registry includes messenger-owned tables as `no-sync`.
- Registry includes sync bookkeeping tables as `internal-bookkeeping`, not product sync.

Exit criteria:

- All current model tables have an explicit starter policy.
- Unknown future tables are blocked by a failing test.

### Step 2B - Sync Receiver Fail-Closed Policy

Required behavior:

- `/api/sync/receive` rejects unknown, unregistered, or policy-forbidden tables.
- A peer must not return success for skipped unknown work.
- The source must not mark a change synced when the peer rejected or skipped it.
- Messenger-owned tables are rejected by sync receive unless a specific transitional compatibility
  exception is explicitly documented and tested.

Likely code areas:

- `api/routers/sync.py`
- `core/sync_worker.py`
- `core/events.py`
- sync receive tests

Required tests:

- Unknown table returns error or partial failure, not success.
- Policy-forbidden messenger table returns error or partial failure.
- Mixed batch with valid and invalid items reports the invalid item visibly.
- Sync worker does not mark rejected rows as synced.

Exit criteria:

- No silent-success path remains for unregistered sync items.
- Operator-visible error data is available in response/logs.

## Step 3 - Runtime Surface Guards

Goal: make the Iran/foreign split enforceable at runtime, not only by compose intent.

This step is split because Bot startup and API/WebApp routing have different blast radii.

### Step 3A - Bot Runtime And Deployment Guard

Required behavior:

- `run_bot.py` refuses to start unless the process is explicitly on the foreign bot surface.
- `server_mode=iran` is a hard failure for Bot runtime even if `BOT_TOKEN` exists.
- Compose/deployment tests prove Iran does not run the bot service and does not require Telegram
  credentials.

Likely code areas:

- `run_bot.py`
- config/settings module
- `docker-compose.yml`
- `docker-compose.iran.yml`
- deployment/config tests

Required tests:

- Bot startup with Iran mode fails closed.
- Bot startup with missing/ambiguous surface fails closed.
- Foreign mode with required bot config reaches the normal startup path using mocks.
- Iran compose has no active bot service.
- Foreign compose has bot service.

Exit criteria:

- Iran cannot accidentally start Bot runtime through the normal entrypoint.
- The failure is explicit enough for operations to diagnose.

### Step 3B - Foreign WebApp, Static, And Chat Guard

Required behavior:

- Foreign API does not serve WebApp frontend assets.
- Foreign API does not expose user-facing WebApp entrypoints.
- `/api/chat` user-facing messenger routes fail closed on foreign.
- Internal routes such as sync, health, and maintenance remain available only when explicitly
  allowlisted.

Likely code areas:

- `main.py`
- API router registration or middleware
- `api/routers/chat*`
- server routing/config module
- route tests

Required tests:

- Foreign mode rejects frontend/static WebApp routes.
- Foreign mode rejects user-facing chat routes.
- Iran mode still serves WebApp routes normally.
- Sync/health endpoints remain available in the intended mode.

Exit criteria:

- Foreign cannot become a WebApp surface because files or routes exist in the image.
- Chat remains Iran/WebApp-local.

## Step 4 - Side-Effect Gateways And Local Runtime Policies

Goal: centralize side effects before shared command services depend on them.

This step is split because Telegram, Web Push, and account/session behavior are separate policy
surfaces.

### Step 4A - Telegram Gateway

Required behavior:

- Every Telegram side effect goes through one central gateway or adapter.
- The gateway hard-fails on Iran mode.
- Foreign mode remains the only Telegram execution surface.
- Business-visible Telegram effects expose idempotency hooks for later publication outbox work.

Likely code areas:

- `bot/`
- Telegram send/edit/delete helpers
- channel publication service
- notification code that calls Telegram directly

Required tests:

- Iran mode Telegram call fails closed.
- Foreign mode delegates to the Telegram client through the gateway.
- Direct Telegram calls are either removed or listed as temporary exceptions with tests.

Exit criteria:

- New code has one approved path to Telegram.
- Existing direct-call exceptions are visible and scheduled for removal.

### Step 4B - Web Push, WebApp Realtime, And Notification Policy

Required behavior:

- Web Push execution is Iran/WebApp runtime behavior by default.
- Foreign does not execute browser Web Push directly.
- Foreign business events sync durable notification intent or product data to Iran.
- Notification rows and preference fields have an explicit sync policy before broad replication.
- WebApp realtime events emitted after sync apply on Iran do not create sync echoes.

Likely code areas:

- `core/web_push.py`
- `api/routers/notifications.py`
- realtime/event modules
- sync apply path

Required tests:

- Foreign mode Web Push execution is blocked or converted to durable intent.
- Iran mode Web Push behavior remains available.
- Synced market apply on Iran emits local realtime without writing a new outbound sync event.
- Notification preference fields are covered by the registry.

Exit criteria:

- Telegram, Web Push, WebApp realtime, and notification rows have one documented side-effect
  classification.

### Step 4C - Runtime Session And Bot Account Freshness Policy

Required behavior:

- WebApp sessions stay Iran-local.
- Bot FSM/runtime state stays foreign-local.
- Login/recovery request rows do not sync.
- `telegram_id`, user profile, account status, role, and limits are user/account product data and
  can sync subject to field policy.
- Bot account-linking and channel-join approval have explicit pending/retry/fail-visible behavior
  when sync lag hides a recent user/account update.

Likely code areas:

- auth/session routers and models
- Bot `/start` and join approval handlers
- user/account status services
- sync registry

Required tests:

- Runtime/session tables are classified `no-sync`.
- Account product fields are classified separately from session runtime rows.
- Bot join approval fails closed for blocked/inactive users.
- Bot join/account-link flow has a deterministic response when required user data is not synced yet.

Exit criteria:

- `user.home_server` is no longer treated as current active surface in new logic.
- Account-linking sync-lag behavior is user-visible and testable.

## Step 5 - Shared Offer, Expire, And Trade Commands

Goal: make Bot and WebApp adapters call the same authority-aware business commands.

This step is intentionally split into three implementation steps because each command has different
side effects and failure modes.

### Step 5A - Shared Offer Creation Command

Required behavior:

- Bot and WebApp offer creation call one shared command/service.
- The service owns `source_surface`, actor, request server, `offer_home_server`, market validation,
  offer row creation, and durable sync/outbox recording.
- Post-commit side effects are declared, not hidden inside adapters.

Required tests:

- WebApp adapter creates Iran-home offer through the service.
- Bot adapter creates foreign-home offer through the service.
- Sync/internal creation preserves incoming authority.
- Outbox/change-log recording is created for synced offer writes.
- Side effects run only after commit or are recorded for durable retry.

Exit criteria:

- No new direct offer creation path bypasses the service.

### Step 5B - Shared Expire/Cancel Command

Required behavior:

- API cancel-all, single-offer expiry, auto-expiry, market-close expiry, Bot cancel-all, and
  remote-forwarded expiry use one shared command/service.
- Non-authoritative servers forward to `offer_home_server` and do not mutate locally.
- Authoritative mutation commits before post-commit side effects.
- Expire reason supports normal expiry, owner cancel, market close, recovery finalization, and
  administrative action.

Required tests:

- Owner expiry from WebApp and Bot reaches the authoritative server.
- Non-authoritative local mutation is rejected or forwarded without local DB mutation.
- Cancel-all cannot execute side effects before DB commit.
- Recovery-finalization expiry is distinguishable by reason.

Exit criteria:

- Expiry/cancel paths have one authority rule and one side-effect contract.

### Step 5C - Shared Trade/Request Command

Required behavior:

- WebApp requests and Bot channel callbacks use one authoritative/idempotent command/service.
- Non-authoritative servers forward to `offer_home_server`.
- The service owns validation, trade-number allocation, offer quantity/status mutation,
  idempotency, sync/outbox recording, and post-commit side effects.

Required tests:

- Request/trade from WebApp against Iran-home offer mutates on Iran.
- Request/trade from Bot against foreign-home offer mutates on foreign.
- Request/trade from the non-home surface forwards and does not mutate locally.
- Duplicate request/callback is idempotent.
- Near-simultaneous request/expiry resolves by authority and version/lock behavior.

Exit criteria:

- All user-facing trade/request adapters call the shared command.

## Step 6 - Durable Outbox, Ordering, And Sync Delivery

Goal: make committed product writes reliably and safely cross servers.

This is split because transactional recording, worker delivery, and stale-event rejection are
separate reliability layers.

### Step 6A - Mandatory Transactional Outbox For Synced Tables

Required behavior:

- Synced-table authoritative writes cannot silently commit without a durable outbox/change-log row.
- Listener-based logging may remain only as transitional coverage.
- Business write failure and outbox failure are not reported as success.

Required tests:

- Simulated payload/outbox failure blocks or visibly fails a synced-table write.
- Non-synced runtime tables do not require product sync outbox.
- Bulk update/delete/raw SQL paths are audited or blocked from bypassing sync recording.

Exit criteria:

- Missing outbox row for a synced authoritative write is no longer a silent failure.

### Step 6B - Committed Outbox Drain And Retry Contract

Required behavior:

- Sync worker drains committed `change_log WHERE synced=false`.
- Redis/direct push remains only wake-up/acceleration.
- Missed Redis/direct events cannot lose a committed sync change.
- Rejected peer responses do not mark rows synced.

Required tests:

- Worker sends committed unsynced row without Redis wake-up.
- Direct push failure still leaves row for worker retry.
- Peer rejection keeps row unsynced or terminally blocked with operator-visible state.

Exit criteria:

- Queue loss and direct-push loss are recoverable.

### Step 6C - Aggregate Ordering And Stale-Event Rejection

Required behavior:

- Outbox/sync payloads carry enough metadata for stale-event rejection: aggregate identity,
  authority server, event sequence or authoritative version, outbox id, and command/idempotency id.
- Old create/update events cannot overwrite newer terminal states such as `expired` or `traded`.

Required tests:

- Out-of-order offer update after expiry does not reactivate the offer.
- Duplicate event replay is idempotent.
- Older state from peer is rejected visibly or ignored with audit metadata.

Exit criteria:

- Latest authoritative terminal state wins.

## Step 7 - Publication State And Cross-Surface Visibility

Goal: make business-visible publication explicit, idempotent, and repairable.

This step is split because WebApp realtime and Telegram publication have different execution
surfaces.

### Step 7A - Surface Publication State Model

Required behavior:

- Business state such as `offer.status` is separate from publication state.
- Publication state records pending, visible/sent, failed, disabled, lagged, last attempt, and error
  code where the side effect is business-visible.
- Publication state is modeled for both Telegram and WebApp visibility.

Required tests:

- Offer can be valid in DB while Telegram publication is pending/failed.
- Offer can be expired/traded while old publication event is ignored or converted to final-state
  update.
- Lagged state appears when the healthy sync/publish target is exceeded.

Exit criteria:

- Operators can distinguish DB truth from surface publication state.

### Step 7B - Telegram Publication Idempotency And Result Sync-Back

Required behavior:

- Foreign-side Telegram publish uses a dedupe key independent of only `channel_message_id`.
- Worker replay/direct push cannot create duplicate channel posts.
- Foreign publication result syncs back to Iran.
- Terminal Telegram channel state uses one central edit path that removes interactive controls and
  rewrites the canonical channel post text.
- Fully traded offers edit the Telegram post and append `🤝 ✅` on a new line.
- Partially traded offers edit the Telegram post and append `🤝 {traded_quantity} تا ✅` on a new
  line.
- Expired offers edit the Telegram post and append `❌` on a new line; interactive buttons are
  removed at the same time.
- Terminal post edits are idempotent: replaying the same final state must not duplicate tags or
  create extra Telegram posts.

Required tests:

- Duplicate publish attempt creates one Telegram post.
- Publish success stores/syncs the Telegram publication result.
- Publish failure is retryable and visible.
- Fully traded offer uses `editMessageText`, appends `🤝 ✅`, and sends `reply_markup=None`.
- Partially traded offer uses `editMessageText`, appends `🤝 {traded_quantity} تا ✅`, and sends
  `reply_markup=None`.
- Expired offer uses `editMessageText`, appends `❌`, and sends `reply_markup=None`.
- Manual expiry, auto-expiry, Bot cancel-all, WebApp cancel-all, and recovery-finalization expiry
  all reach the same terminal Telegram channel-state path.
- Replaying a terminal channel-state update is treated as success when Telegram returns
  "message is not modified".

Exit criteria:

- Telegram channel state can be reconciled with DB and publication state, including the exact
  terminal marker and removal of interactive controls.

### Step 7C - WebApp Realtime Visibility From Synced Market Changes

Required behavior:

- Iran emits local WebApp realtime after applying synced market data.
- Realtime emit does not create a new outbound sync echo.
- Bot-authored offer create/update/expiry/trade becomes visible on WebApp with the expected
  pending/lagged/final states.

Required tests:

- Bot offer sync apply on Iran emits WebApp market event.
- Foreign trade/expiry sync apply on Iran updates WebApp state.
- Realtime failure does not corrupt DB state and is either best-effort-safe or durably repairable
  according to the side-effect classification.

Exit criteria:

- WebApp market view can reflect foreign-home offer changes without Iran calling Telegram.

## Step 8 - Identity, Callback Compatibility, And Protocol Versioning

Goal: move cross-server identity toward stable public identifiers without breaking existing local
integer references.

This step is large and must be split.

### Step 8A - Public Identity For Synced Records

Required behavior:

- Synced tables get stable `public_id`/UUID identifiers.
- Integer IDs remain local/internal during migration.
- Server-partitioned integer sequences are used as a migration guard until cross-server commands no
  longer rely on integers.

Required tests:

- Synced payloads identify records by public identity.
- Existing integer-id local reads continue to work.
- Duplicate/colliding integer IDs across servers do not corrupt cross-server resolution.

Exit criteria:

- New cross-server command/sync contracts use public identity where implemented.

### Step 8B - Telegram Callback Compatibility

Required behavior:

- Existing Telegram callbacks that carry integer offer IDs have a compatibility path.
- New callback payloads are versioned or mapped to canonical public identity.
- Old channel messages have a defined behavior after migration.

Allowed designs:

- Versioned callback payloads.
- Short public tokens.
- Callback mapping rows.
- Foreign-local integer resolution to canonical public IDs.

Required tests:

- Old callback payload still resolves or fails visibly with a safe user message.
- New callback payload resolves to the correct canonical offer.
- Callback replay remains idempotent.

Exit criteria:

- Identity migration does not invalidate active Telegram channel interactions without a planned
  compatibility behavior.

### Step 8C - Registry And Protocol Version Compatibility

Required behavior:

- Sync payloads carry schema/registry/protocol version metadata.
- Peers reject unsupported versions visibly.
- Rolling deploys do not silently apply partial or incompatible work.

Required tests:

- Unsupported producer version is rejected.
- Supported older compatible version still applies where declared compatible.
- Rejection leaves source work unsynced or blocked with operator-visible status.

Exit criteria:

- Staggered deploys fail visibly instead of corrupting or silently skipping data.

## Step 9 - Admin, Background Jobs, And Field-Level Policies

Goal: close non-user-surface write paths that can bypass shared command authority.

This step is split by ownership surface.

### Step 9A - Admin Surface Authority

Required behavior:

- Bot admin and WebApp admin writes have one authority and conflict policy.
- Covered data includes settings, commodities, commodity aliases, user account status, user limits,
  and similar shared product/admin data.

Required tests:

- Each admin-mutated table is in the registry with write surfaces and authority.
- Conflicting admin writes resolve by the documented rule.
- Non-authoritative admin writes forward or fail visibly.

Exit criteria:

- Admin surfaces cannot silently fork shared product configuration.

### Step 9B - Background Job Authority Matrix

Required behavior:

- Every recurring job declares mutated tables, allowed server(s), authority rule, outage behavior,
  and sync/outbox behavior.
- At minimum cover `offer_expiry`, `market_schedule`, `session_expiry`, `user_account_status`, and
  `connectivity_monitor`.

Required tests:

- Jobs refuse to mutate tables on disallowed servers.
- Offer-impacting jobs use shared authoritative commands.
- Local runtime jobs remain local and no-sync where appropriate.

Exit criteria:

- Background jobs cannot bypass command authority or sync/outbox policy.

### Step 9C - Field-Level Sensitive And No-Sync Reference Policy

Required behavior:

- Sensitive fields are classified as `sync`, `no-sync`, `hash-only`, or `encrypted/derived`.
- Logs do not expose sensitive raw payloads.
- Fields referencing no-sync tables, such as `users.avatar_file_id -> chat_files.id`, have explicit
  null/derived/no-sync behavior.

Required tests:

- User sync payload follows field policy.
- Notification/Web Push sensitive fields follow policy.
- No raw FK to no-sync data crosses servers unless explicitly allowed.
- Logs redact or omit sensitive payload fields.

Exit criteria:

- Registry-driven sync cannot leak sensitive or locally invalid references by default.

## Step 10 - Recovery, Reconciliation, And Outage Modes

Goal: make degraded operation safe and recoverable.

This step is split because short outage replay and medium/long outage finalization differ.

### Step 10A - Reconciliation Jobs And Observability

Required behavior:

- Reconciliation covers: offer synced but Telegram publish failed, Telegram post exists but local
  publication id is missing, foreign publication result did not sync back to Iran, duplicate
  delivery arrived, and peer was down during local writes.
- Observability covers sync lag, committed outbox backlog, retry backlog, Telegram publish pending
  and failed counts, orphan Telegram posts, stale WebApp state, and conflict counts.

Required tests:

- Reconciliation can repair or report each partial flow.
- Metrics/logs expose backlog and failed publication state.
- Reconciliation is idempotent.

Exit criteria:

- Operators have a repeatable repair path for partial cross-surface flows.

### Step 10B - Short Outage Behavior Up To 2 Minutes

Required behavior:

- Local-home create/mutate continues with remote sync/publish pending.
- Remote-home mutate is rejected by default or explicitly queued as non-final if a future design
  chooses pending commands.
- After reconnect, replay uses latest authoritative state.

Required tests:

- Short outage create replays and publishes latest state after reconnect.
- Remote-home mutation during outage does not locally mutate.
- Create followed by local expire before reconnect syncs final expired state, not active state.
- Create followed by local trade before reconnect syncs final traded state, not active state.

Exit criteria:

- Short outage replay cannot publish stale active offers.

### Step 10C - Medium And Long Outage Recovery Finalization

Required behavior:

- Medium outage: over 2 minutes and up to 1 hour.
- Long outage: over 1 hour.
- Cross-surface active publication remains gated until full catch-up is proven.
- Active local-only offers created or kept visible before full recovery are expired by their home
  server and synced as final expired state.
- Expiry includes `expire_reason`, owner notification, and admin/operator report.

Required tests:

- Medium outage recovery gates active publication until catch-up complete.
- Long outage recovery gates active publication until catch-up complete.
- Local-only active offers are expired, not active-published to the peer.
- Owner notification and operator report are generated.

Exit criteria:

- Recovery favors data safety over late active publication.

## Step 11 - Complete Scenario Matrix And Staging Validation

Goal: prove Bot and WebApp can coexist before any production consideration.

Required scenario groups:

- Bot offer create -> Iran WebApp visibility.
- WebApp offer create -> foreign Telegram visibility.
- Owner expiry from WebApp and Bot.
- Telegram channel terminal markers for fully traded (`🤝 ✅`), partially traded
  (`🤝 {traded_quantity} تا ✅`), and expired (`❌`) offers.
- Telegram channel terminal edits remove inline buttons for traded and expired offers.
- Request/trade from WebApp and Bot against both Iran-home and foreign-home offers.
- Same user active on both surfaces.
- Near-simultaneous trade/expiry/update.
- Duplicate callback, duplicate sync delivery, worker replay, direct push failure.
- Telegram publish failure and retry.
- WebApp realtime failure and recovery/visibility policy.
- Unknown table, forbidden table, unsupported version, and sensitive-field rejection.
- Short, medium, and long outage flows.
- Migration/cutover with zero active offers.
- Rollback/fail-closed behavior without destructive data cleanup.

Required test layers:

- Unit tests for pure policy helpers.
- Service tests for shared commands.
- API tests for WebApp routes and sync receive.
- Bot handler tests with Telegram mocked.
- Telegram channel-state tests for full trade, partial trade, manual expiry, auto-expiry,
  Bot cancel-all, WebApp cancel-all, duplicate replay, and "message is not modified" responses.
- Worker tests for outbox/retry/replay.
- Integration or E2E tests for two-surface flows where practical.
- Staging manual validation with logs and sync-health review.

Exit criteria:

- Automated tests for the accepted scenario matrix pass.
- Staging validation passes without production data or production peers.
- Owner-led manual staging validation is complete.
- Logs, sync-health, DB state, Telegram publication state, WebApp realtime state, and session
  surface behavior are reviewed.

## Step 12 - Cutover Readiness And Production Gate Preparation

Goal: prepare for a future production decision without performing it.

Required behavior:

- Migration/cutover remains conservative and non-destructive.
- Active offers are verified as zero on both servers before cutover.
- Backlog and partial sync state are reconciled to a known safe state.
- Rollback/fail-closed plan is ready.
- Observability and backups/snapshots are ready.

Required checks:

- Active offers count on both servers is zero.
- Sync backlog is safe.
- No unresolved partial publication state blocks cutover.
- Runtime guards prove Iran cannot call Telegram.
- Runtime guards prove foreign cannot serve WebApp/chat user surfaces.
- Registry coverage is complete.
- Sensitive-field policy is complete.
- Rollback disables new behavior without deleting synced or migrated data.

Exit criteria:

- A production readiness report can be produced for owner review.
- No production deploy or production data action occurs from this step.

## Stop Conditions

Stop immediately and ask for owner decision if any of these happen:

- Current branch is not `candidate/bot-webapp-integration`.
- A required merge/rebase choice is needed.
- A policy conflict appears between roadmap, implementation contract, and current code.
- A step requires production access or production data.
- A test exposes destructive data behavior not explicitly approved by the owner.
- A sync/security decision would expose sensitive fields without an accepted field policy.
- A change would require touching unrelated branch work.

## Commit And Reporting Format

Each implementation step should end with a concise report:

- Branch and commit hash.
- Step completed.
- Main files changed.
- Tests run and result.
- Any skipped tests and reason.
- Any newly discovered risk.
- Next step recommendation.

Suggested commit message pattern:

```text
<area>: <step outcome>
```

Examples:

```text
sync: fail closed on unregistered tables
bot: enforce foreign-only runtime startup
offers: route creation through source surface contract
docs: record bot webapp implementation contract
```

## Execution Order Summary

1. Step 0 - Refresh Candidate From Main
2. Step 1 - Source Surface And Offer Home Server Contract
3. Step 2A - Registry Skeleton And Inventory Test
4. Step 2B - Sync Receiver Fail-Closed Policy
5. Step 3A - Bot Runtime And Deployment Guard
6. Step 3B - Foreign WebApp, Static, And Chat Guard
7. Step 4A - Telegram Gateway
8. Step 4B - Web Push, WebApp Realtime, And Notification Policy
9. Step 4C - Runtime Session And Bot Account Freshness Policy
10. Step 5A - Shared Offer Creation Command
11. Step 5B - Shared Expire/Cancel Command
12. Step 5C - Shared Trade/Request Command
13. Step 6A - Mandatory Transactional Outbox For Synced Tables
14. Step 6B - Committed Outbox Drain And Retry Contract
15. Step 6C - Aggregate Ordering And Stale-Event Rejection
16. Step 7A - Surface Publication State Model
17. Step 7B - Telegram Publication Idempotency And Result Sync-Back
18. Step 7C - WebApp Realtime Visibility From Synced Market Changes
19. Step 8A - Public Identity For Synced Records
20. Step 8B - Telegram Callback Compatibility
21. Step 8C - Registry And Protocol Version Compatibility
22. Step 9A - Admin Surface Authority
23. Step 9B - Background Job Authority Matrix
24. Step 9C - Field-Level Sensitive And No-Sync Reference Policy
25. Step 10A - Reconciliation Jobs And Observability
26. Step 10B - Short Outage Behavior Up To 2 Minutes
27. Step 10C - Medium And Long Outage Recovery Finalization
28. Step 11 - Complete Scenario Matrix And Staging Validation
29. Step 12 - Cutover Readiness And Production Gate Preparation
