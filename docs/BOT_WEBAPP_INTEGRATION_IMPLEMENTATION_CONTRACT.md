# Bot/WebApp Integration Implementation Contract

Date: 2026-06-18
Last updated: 2026-06-19

Branch: `candidate/bot-webapp-integration`

This contract turns `docs/BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md` into an implementation
sequence. The roadmap is the source of policy and risk; this file is the execution contract for
coding, tests, commits, staging validation, and stop/go gates.

## Contract Status

- Sections 16 and 17 of `BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md` are accepted as part of
  the implementation baseline.
- The original `BOT_WEBAPP_INTEGRATION_PREFLIGHT_FRESHNESS_REPORT.md` is complete but must be
  refreshed as Step 0A because `main` has moved since the first report.
- The candidate branch must be refreshed from current `main` before code implementation starts.
- Refresh method recommended by the preflight report: merge `main` into
  `candidate/bot-webapp-integration`.
- The owner approved starting Step 0 on 2026-06-19. Proceed to the merge only if Step 0A confirms
  the branch, worktree, and merge safety checks are still acceptable.
- The owner-approved offer link and request metadata requirement is now part of this same contract,
  not a parallel branch. Implementation must not create a separate offer-link path that bypasses the
  Bot/WebApp authority, sync, outbox, publication, and staging gates in this document.

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

## Accepted Offer Link And Request Ledger Contract

This contract extends the original Bot/WebApp integration plan with the accepted requirement that
every offer has its own stable link and a complete request ledger.

Required product truth:

- Use these names consistently: Offer Identity Metadata, Offer Link Metadata, Offer Request Ledger,
  Offer Expiry Metadata, Offer Publication State, and Offer Detail Visibility Policy.
- Every offer must receive a stable, opaque, non-enumerable public identifier at authoritative
  creation time. Integer `offers.id` remains an internal/local implementation detail and must not be
  the public link identity.
- `offer_public_id` is the canonical cross-server offer reference. Local database rows may keep
  integer foreign keys, but sync payloads and forwarded commands must carry `offer_public_id`, and a
  receiving peer must resolve its local `offers.id` from that public identity.
- The canonical offer link must resolve through the Iran WebApp surface. Foreign/Bot may render or
  send the URL as text, but foreign must not serve WebApp pages and Iran must not call Telegram.
- The public offer identifier is not an authorization secret. Having the link grants access only to
  safe public market fields. A future `offer_share_token` may be introduced as a separate
  rotatable/revokable URL token if link revocation becomes a product requirement.
- Offer link metadata is synchronized product data. Publication state remains separate and local or
  surface-specific where required.
- Every confirmed backend request attempt against an offer must be recorded in a durable
  `offer_requests` ledger, even when it does not become a trade because the offer changed, expired,
  the lot became unavailable, or the request was rejected by business rules.
- First-tap UI pending states are not product requests. Record the ledger entry only after the user
  has confirmed the request and it reaches the backend command boundary.
- The request ledger must capture requester, actor, request source surface, request source server,
  requested quantity, edge receipt time, authoritative decision time, result status, failure reason
  where safe, idempotency key, and the resulting trade id when a trade is created.
- If the requester acts through a customer relation, the ledger must store the relation id and a
  snapshot of the relation context at request time, including owner user, customer tier, management
  name, and commission-related data needed for audit.
- Offer expiry metadata must capture whether expiry came from lifetime expiry, owner/user action,
  cancel-all, market close, recovery finalization, or administrative action. User-initiated expiry
  must also capture the actor/user and source surface/server that performed it.
- Sensitive metadata from offer links and request ledgers must be protected by the field-level
  sensitive data policy. Public link access does not imply public access to requester identities,
  mobile numbers, customer relation details, or operational failure reasons.

This requirement is a dependency for Step 5C-0, Step 5C, Step 5D, Step 7C, Step 8B, Step 11, and
Step 12. Do not implement the shared trade/request command without the ledger contract.

## Step 0 - Refresh Candidate From Main

Goal: make implementation start from current code instead of the stale roadmap-only branch.

Step 0 is split so the branch refresh is auditable before and after the merge.

### Step 0A - Re-run Freshness Report Against Current Main

Start condition:

- Current branch is `candidate/bot-webapp-integration`.
- Worktree is clean before collecting freshness data.
- Owner has approved starting Step 0.

Required actions:

- Run `git fetch origin`.
- Record local/remote SHAs for `main` and `candidate/bot-webapp-integration`.
- Record merge base and `git rev-list --left-right --count main...HEAD`.
- Run `git merge-tree --write-tree main HEAD` or the current equivalent merge simulation.
- List changed implementation areas from `HEAD..main`, with special attention to offers, trades,
  bot handlers, sync receive/worker, expiry, Telegram publication, frontend market history, and
  deploy/release guards.
- Update `BOT_WEBAPP_INTEGRATION_PREFLIGHT_FRESHNESS_REPORT.md`.

Exit criteria:

- Freshness report reflects current `main`, not the old 2026-06-18 snapshot.
- The report states whether Step 0B can proceed or must stop for owner decision.

### Step 0B - Owner-Approved Merge Main Into Candidate

Start condition:

- Step 0A is complete.
- Current branch is `candidate/bot-webapp-integration`.
- Worktree is clean.
- The owner-approved default method remains a normal merge from `main`.
- Merge simulation did not show textual conflicts. If this condition fails, stop and report.

Default action:

- Merge `main` into `candidate/bot-webapp-integration`.

Why merge, not rebase:

- The candidate branch is already pushed.
- The roadmap has a visible decision history that should not be rewritten without a specific reason.

Exit criteria:

- Merge commit or documented no-op if branch state changes before execution.
- No conflict markers remain.
- Worktree is clean after any required report commit.

### Step 0C - Post-Merge Semantic Drift Report

Required actions:

- Review the refreshed code for semantic drift in at least:
  `api/routers/offers.py`, `api/routers/trades.py`, `core/offer_expiry.py`,
  `core/services/telegram_offer_channel_service.py` or current equivalent,
  `models/offer.py`, `models/trade.py`, sync receive/worker code, frontend market-history, and
  deploy/release guards.
- Treat the current `main` market-history endpoint, Telegram terminal offer markers, and production
  release guards as baseline behavior after refresh. Do not reimplement them unless tests show they
  violate the shared gateway, publication-state, metadata, or sync contracts.
- Record any required contract adjustment before Step 1 starts.

Verification:

- `git branch --show-current`
- `git status --short`
- `git rev-list --left-right --count main...HEAD`
- `git diff --check`
- Run the smallest existing backend test subset that exercises offers/trades/sync imports after the
  merge. Exact test names may change after refresh; choose the current equivalent tests.

Step 0 exit criteria:

- Branch is refreshed from current `main`.
- Freshness and post-merge drift reports are up to date.
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
- New offer-link and request-ledger tables introduced by this contract are classified before their
  migrations land. They must not appear as unregistered sync tables after implementation.
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

This step is intentionally split into four implementation steps because each command has different
side effects and failure modes, and the offer request ledger must exist before trade/request
adapters rely on it.

### Step 5A - Shared Offer Creation Command And Offer Link Identity

Required behavior:

- Bot and WebApp offer creation call one shared command/service.
- The service owns `source_surface`, actor, request server, `offer_home_server`, market validation,
  offer row creation, and durable sync/outbox recording.
- The service assigns a stable opaque offer public identifier/share token at authoritative creation
  time. The token is synced and preserved; it is not recomputed by receiving peers.
- Offer links are derived from the public identifier, never from integer `offers.id`.
- Sync/internal creation preserves incoming `home_server` and incoming public identifier.
- Post-commit side effects are declared, not hidden inside adapters.

Required tests:

- WebApp adapter creates Iran-home offer through the service.
- Bot adapter creates foreign-home offer through the service.
- Sync/internal creation preserves incoming authority.
- New offers get a unique public identifier suitable for links.
- Synced offers preserve their public identifier.
- Public link generation does not expose integer ids.
- Outbox/change-log recording is created for synced offer writes.
- Side effects run only after commit or are recorded for durable retry.

Exit criteria:

- No new direct offer creation path bypasses the service.
- Every new offer has a stable public link identity available to later request, callback, and WebApp
  detail flows.

Implementation decisions:

- The canonical stored field is `Offer.offer_public_id`; integer `offers.id` remains local/internal.
- The initial public link shape is a relative WebApp path: `/market?offer={offer_public_id}`. Step 7C
  may replace this with a richer offer detail route, but it must keep deriving the route from
  `offer_public_id`.
- WebApp and Bot creation adapters must call `create_authoritative_offer`; direct runtime
  `Offer(...)` creation is limited to the shared service and non-runtime fixtures/tests.
- Offer sync/change-log payloads must include `offer_public_id`, and sync receive must preserve it.

### Step 5B - Shared Expire/Cancel Command

Required behavior:

- API cancel-all, single-offer expiry, auto-expiry, market-close expiry, Bot cancel-all, and
  remote-forwarded expiry use one shared command/service.
- Non-authoritative servers forward to `offer_home_server` and do not mutate locally.
- Authoritative mutation commits before post-commit side effects.
- Expire reason supports normal expiry, owner cancel, market close, recovery finalization, and
  administrative action.
- User/owner/admin initiated expiry records `expired_by_user_id`, `expired_by_actor_user_id`,
  `expire_source_surface`, and `expire_source_server` or equivalent strongly named fields.
- Automatic expiry records an automatic/system source without pretending that a user performed it.

Required tests:

- Owner expiry from WebApp and Bot reaches the authoritative server.
- Non-authoritative local mutation is rejected or forwarded without local DB mutation.
- Cancel-all cannot execute side effects before DB commit.
- Recovery-finalization expiry is distinguishable by reason.
- WebApp owner expiry records WebApp/Iran source metadata.
- Bot owner expiry records Telegram/foreign source metadata.
- Auto-expiry records lifetime/system metadata and no false user actor.

Exit criteria:

- Expiry/cancel paths have one authority rule and one side-effect contract.
- Offer expiry metadata is sufficient for the offer detail link and audit views.

Implementation decisions:

- The shared command is `OfferExpiryCommand` in `core.services.offer_expiry_service`; WebApp,
  Telegram Bot, auto-expiry, market-close expiry, remote-forwarded expiry, and cancel-all adapters
  must use this command/service instead of setting `Offer.status`, `Offer.expired_at`, or
  `Offer.expire_reason` directly.
- The canonical audit columns are nullable on `offers`: `expired_by_user_id`,
  `expired_by_actor_user_id`, `expire_source_surface`, and `expire_source_server`. Existing
  historical rows are not backfilled in Step 5B; Step 5C-0 owns the public-link/request-ledger field
  policy and any later historical presentation rules.
- Non-authoritative owner expiry forwards a signed internal request to
  `POST /api/offers/internal/expire` on the offer home server. A successful forward returns success
  to the initiating surface without mutating the local replica; the local replica updates only after
  normal cross-server sync receives the authoritative row.
- Authoritative expiry commits before Telegram channel edits, realtime events, and active-offer
  cache updates. Tests must assert this ordering for cancel-all flows because those flows have the
  largest side-effect fanout.
- Automatic/system expiry uses `expire_source_surface=system` and leaves user/actor expiry ids
  empty. Owner actions use the initiating surface (`webapp` or `telegram_bot`) and record both owner
  and actor ids.
- User deletion is the only accepted Step 5B bulk-update exception because it is part of the wider
  account deletion transaction. It must still write equivalent system expiry metadata and must not
  invent a user actor.
- Legacy `expire_reason` string values remain stable for compatibility; new standardized reason
  mapping can be addressed after Step 5C-0 if the offer detail/audit UI needs a stricter enum.

### Step 5C-0 - Minimum Field Policy For Offer Link And Request Ledger

Required behavior:

- Freeze the minimum field-level policy before adding the request ledger schema. Step 9C may broaden
  the project-wide sensitive-data registry later, but Step 5C cannot create offer-link or
  request-ledger fields without this minimum policy.
- `offer_public_id` is sync-critical, visible as the route key, and visible to authorized users. It
  is not an authorization secret.
- `requester_user_id` is synced product data but hidden from public link viewers. It is visible only
  to the requester where allowed, the offer owner where allowed, admin, or audit roles.
- `actor_user_id` is synced product data but hidden from public link viewers. It is visible only to
  owner/admin/audit roles where allowed.
- `request_source_surface` is synced product data. Public views may show only a safe summary if the
  product needs it; detailed source data is owner/admin/audit visibility.
- `request_source_server` is synced product data but hidden from public views and limited to
  admin/operator/audit visibility.
- `requested_quantity` is synced product data. Public views may show only safe aggregate state;
  requester/owner/admin views may show detailed rows according to permissions.
- `result_status` is synced product data. Public views may show only safe status labels; detailed
  status is permission-gated.
- Failure data is split into `public_failure_code`/optional safe public message and
  `internal_failure_code`/`internal_failure_context`. Internal failure context must be hidden from
  public views, redacted from normal logs, and limited to admin/operator/audit visibility or
  encrypted/derived storage if the implementation requires it.
- Customer relation metadata is synced only as explicitly classified audit data:
  `customer_relation_id`, owner user id, customer tier snapshot, management name snapshot, and
  commission context are hidden from public views and visible only to owner/admin/audit roles where
  allowed.
- `mobile_number` must not be added to `offer_requests` unless explicitly classified first. Existing
  `trades` mobile snapshots keep their legacy/current policy until Step 9C reviews them; do not
  silently expand raw mobile sync through the new ledger.
- Define the legacy/current `Offer.expire_reason` mapping before backfill:
  `time_limit` -> `lifetime_expiry` with system/automatic source;
  `manual` -> `owner_action`;
  `cancel_all` and `bot_cancel_all` -> `owner_bulk_action`;
  `republished` -> `owner_republish`;
  `market_closed`/`market_close` -> `market_close`;
  `telegram_send_failed` -> `publication_failure` or fail-closed publish rollback, not ordinary
  user expiry;
  `user_deleted` -> account cleanup;
  future `recovery_finalization` -> recovery finalization;
  future `admin`/`admin_action` -> admin action.
- Historical rows with unknown actor/source must keep the old reason and use `legacy_unknown` for
  missing metadata. Backfill must not invent user, actor, surface, or server values.

Required tests:

- Field-policy tests prove public offer-link responses cannot expose requester, customer relation,
  mobile, internal failure, or raw source/server data.
- Authorized detail tests prove owner/admin/audit viewers can see the fields they are allowed to see.
- Expiry metadata mapping tests cover current known reason values and `legacy_unknown` backfill.
- Log/redaction tests cover internal failure context and sensitive request metadata where practical.

Exit criteria:

- Step 5C schema work has an accepted field policy and cannot accidentally turn public links into
  sensitive audit views.
- Expiry metadata migration/backfill has a deterministic mapping and a no-fabrication rule.

Implementation decisions:

- The minimum field policy lives in `core.offer_request_policy`. Public link payloads may expose
  only safe aggregate/request outcome fields and must not expose requester ids, actor ids, customer
  relation details, raw source/server data, mobile numbers, idempotency keys, or internal failure
  context.
- Owner/requester/admin/audit visibility is explicit and role-gated. Internal failure context is
  admin/audit-only; owner/detail views must not receive it by default.
- Legacy `Offer.expire_reason` mapping is deterministic and uses `legacy_unknown` when historical
  actor/source metadata is missing. Backfill or presentation code must not fabricate user, actor,
  surface, or server values.
- Step 9C may broaden the global sensitive-field registry, but Step 5C and Step 7C must already use
  this local field policy for offer-link/request-ledger data.

### Step 5C - Offer Request Ledger And Metadata Schema

Required behavior:

- Add an `offer_requests` model/table, or an equivalent strongly named ledger table, for durable
  request attempts against offers.
- The ledger is authoritative on `offer_home_server`; forwarded requests carry source metadata to
  the authoritative command rather than mutating the peer locally.
- The ledger stores both `local_offer_id` as the local database foreign key and `offer_public_id` as
  the canonical cross-server offer identity. Sync and forwarded command payloads must not require
  matching integer offer IDs across servers.
- The ledger records at least: local offer id, offer public id, requester user id, actor user id,
  request source surface, request source server, requested quantity, idempotency key, request
  received time, authoritative decision time, status/result, public failure code/message, internal
  failure code/context where allowed, resulting trade id, and archived/version fields needed for
  sync.
- The ledger snapshots customer relation context at request time when the requester is a customer:
  relation id, owner user id, customer tier, management name, and commission context needed to
  explain price/trade outcomes later.
- Request statuses define a lifecycle state machine with at least: `received`, `authorized`,
  `rejected_business_rule`, `rejected_offer_expired`, `rejected_lot_unavailable`,
  `rejected_conflict`, `completed_trade`, `duplicate_replay`, and `failed_internal`.
- Terminal states are immutable except safe annotation/finalization fields. Duplicate replay returns
  the existing terminal state instead of creating a new request row.
- Enforce idempotency with an explicit uniqueness rule. The preferred baseline is a unique partial
  constraint over non-null idempotency keys in the authoritative request namespace, such as
  `UNIQUE(request_home_server, idempotency_key)` where `idempotency_key IS NOT NULL`. If the
  implementation chooses a different namespace, document why before migration.
- Telegram confirmed callbacks should use `telegram_callback:<callback_id>` as the idempotency key
  where the callback id is reliable. Compatibility fallbacks must be deterministic and avoid broad
  time buckets unless no safer data exists.
- Ledger rows are append-only audit records except for safe status finalization fields.
- Index at minimum by `offer_public_id`, requester user id, actor user id, created/received time,
  idempotency key, and result status.
- Define retention/archive behavior before production. Archival may move old rows to cheaper
  storage, but audit-critical history must not be deleted without explicit owner approval.
- Ledger entries are synced as product data. They are not messenger data.
- Public offer link responses must be able to join offer metadata, expiry metadata, request ledger
  rows, and resulting trades without inferring missing request attempts from trades alone.
- Sensitive ledger fields are display-gated by viewer permissions and the field-level policy.

Required tests:

- Migration/model tests cover the required columns, indexes, foreign keys, and enum/status values.
- Constraint tests prove duplicate non-null idempotency keys cannot create duplicate ledger rows in
  the authoritative request namespace.
- State-machine tests prove terminal rows are not mutated into contradictory outcomes.
- Registry tests classify `offer_requests` as synced product data.
- A confirmed WebApp request creates a ledger row with WebApp/Iran source metadata.
- A confirmed Bot/channel request creates a ledger row with Telegram/foreign source metadata.
- A forwarded non-home request creates the ledger row on the authoritative server with original
  source surface/server preserved.
- A customer request stores the relation snapshot visible to authorized audit/detail views.
- A request that does not become a trade still has a durable ledger row and safe result status.
- Duplicate idempotency key replays return the same ledger/trade outcome without creating duplicate
  request rows.
- Pagination tests cover offer detail ledger reads so public/detail endpoints do not require
  unbounded request history loads.

Exit criteria:

- Offer request history can be reconstructed from the ledger without relying only on `trades`.
- The ledger is available to Step 5D shared command work and Step 7C offer detail/link responses.

Implementation decisions:

- The durable table is `offer_requests`; it is synced product data and is no longer a planned-only
  registry entry.
- The ledger stores both `local_offer_id` and `offer_public_id`. `offer_public_id` is the canonical
  cross-server identity; Step 5D forwarded commands must resolve local `offers.id` from it instead
  of assuming integer ids match across servers.
- Idempotency is scoped by `(request_home_server, idempotency_key)` with a partial unique index for
  non-null keys. Duplicate idempotency replays must return the existing ledger outcome rather than
  creating a second row.
- Runtime request/trade adapters are not fully migrated in Step 5C. Step 5C provides the model,
  migration, sync/event plumbing, field policy, append-only ledger service, terminal-state guard,
  customer-relation snapshot support, and paginated history query helper. Step 5D owns wiring the
  existing WebApp and Telegram trade execution paths into this ledger-backed shared command.
- Terminal ledger outcomes are immutable except safe annotation/finalization fields. Service tests
  must prevent a completed/rejected/failed row from changing into a contradictory terminal outcome.
- The ledger event listener writes change-log/sync payloads only; it does not publish public
  realtime payloads directly because display must go through the Step 5C-0 visibility policy.

### Step 5D - Shared Trade/Request Command

Required behavior:

- WebApp requests and Bot channel callbacks use one authoritative/idempotent command/service.
- Non-authoritative servers forward to `offer_home_server` and do not mutate locally.
- Forwarded request commands carry `offer_public_id`, original source surface/server, edge receipt
  time, actor/requester identities, requested quantity, and idempotency key. The receiving home
  server resolves its local `offers.id` from `offer_public_id`.
- The service owns validation, trade-number allocation, offer quantity/status mutation,
  offer-request ledger recording, idempotency, sync/outbox recording, and post-commit side effects.
- The command records the confirmed backend request attempt and its final outcome atomically with the
  authoritative business mutation, or fails visibly without reporting success.
- Request edge time and source surface/server are preserved across forwarding so later audit can tell
  where and when the request originated.

Required tests:

- Request/trade from WebApp against Iran-home offer mutates on Iran and links to an offer request row.
- Request/trade from Bot against foreign-home offer mutates on foreign and links to an offer request
  row.
- Request/trade from the non-home surface forwards and does not mutate locally.
- Forwarded command tests prove integer offer IDs are not trusted across servers; the home server
  resolves by `offer_public_id`.
- Duplicate request/callback is idempotent across both trade and offer-request records.
- Near-simultaneous request/expiry resolves by authority and version/lock behavior and leaves a
  truthful request result.
- Lot unavailable and stale-state responses update or create the ledger with a non-trade result.

Implementation decisions captured during Step 5D:

- `TradeCreate` now accepts optional `offer_public_id`, and internal forwarded execution requires
  `offer_public_id` plus `source_surface`. The home server resolves its own local `offers.id` by
  `offer_public_id`; the incoming integer `offer_id` is kept only as compatibility/debug context and
  is not trusted as cross-server identity.
- WebApp requests enter the shared authoritative path with `request_source_surface=webapp`; Telegram
  channel callbacks enter the same path with `request_source_surface=telegram_bot`.
- Bot local-home confirmed callbacks delegate to the shared authoritative command instead of creating
  `Trade` rows and mutating `Offer` directly in the handler. Bot remote-home callbacks forward the
  same metadata contract to the home server.
- The authoritative command creates an `offer_requests` ledger row after the offer is resolved and
  locked. Successful trades finalize that row as `completed_trade` in the same transaction as the
  offer/trade mutation. Lot-unavailable and invalid-quantity rejections are finalized as non-trade
  outcomes before returning the user-visible rejection.
- Duplicate idempotent trade replay returns the existing trade response and records/finalizes the
  request ledger without mutating the offer again.
- ORM sync events already added in Step 5C make `offer_requests` syncable product data. Step 6 still
  owns the stronger durable outbox guarantee for synced-table writes.
- Existing integer Telegram callback payloads remain compatible for now because the foreign-local
  offer row is still used to discover the canonical `offer_public_id`. Step 8B still owns the
  versioned/new callback payload migration so new callbacks do not depend on cross-server integer
  offer IDs.

Exit criteria:

- All user-facing trade/request adapters call the shared command.
- Every backend-confirmed request attempt has a durable, synced, permission-gated audit record.

## Step 6 - Durable Outbox, Ordering, And Sync Delivery

Goal: make committed product writes reliably and safely cross servers.

This is split because transactional recording, worker delivery, and stale-event rejection are
separate reliability layers.

### Step 6A - Mandatory Transactional Outbox For Synced Tables

Required behavior:

- Synced-table authoritative writes cannot silently commit without a durable outbox/change-log row.
- Listener-based logging may remain only as transitional coverage.
- Business write failure and outbox failure are not reported as success.
- Durable outbox means the local `change_log` insert inside the same DB transaction. Redis
  `sync:outbound` push and direct HTTP push are acceleration paths only and remain non-fatal after
  the durable row exists; Step 6B owns committed-row draining and retry.
- `Session` flushes that write active `SyncPolicy.SYNC` ORM tables must prove that the matching
  `change_log` row was recorded during the same flush. Peer apply paths using `is_sync=True` are
  exempt to avoid sync echo.
- Bulk ORM writes and raw SQL writes to active synced tables are blocked unless they are a peer
  apply path with `is_sync=True`; product code must use ORM row changes that trigger listeners or
  an explicitly designed outbox-aware path in a later step.
- `user_notification_preferences` is an active synced table and must have mapper listeners before
  the guard is active.
- Existing product bulk writes to synced tables must be refactored before enabling the guard:
  admin market-message deactivation, user deletion trade/offer cleanup, and user counter updates
  now use ORM writes with row locking where atomicity matters.

Required tests:

- Simulated payload/outbox failure blocks or visibly fails a synced-table write.
- Non-synced runtime tables do not require product sync outbox.
- Bulk update/delete/raw SQL paths are audited or blocked from bypassing sync recording.
- Regression coverage must include SQL insert failure in `log_change`, successful marker
  verification, missing marker failure, no-sync exemption, `is_sync=True` exemption,
  `trading_settings` key identity, and raw/ORM bulk bypass blocking.

Exit criteria:

- Missing outbox row for a synced authoritative write is no longer a silent failure.
- Focused verification passes for sync outbox guard, event listeners, core counter helpers, sync
  registry/receiver policy, notification preferences, user deletion, and admin message routes.

### Step 6B - Committed Outbox Drain And Retry Contract

Required behavior:

- Sync worker drains committed `change_log WHERE synced=false`.
- Redis/direct push remains only wake-up/acceleration.
- Missed Redis/direct events cannot lose a committed sync change.
- Rejected peer responses do not mark rows synced.
- Worker must poll Redis first for low-latency wake-ups/retries, but on an empty `BLPOP` timeout it
  must read the oldest committed unsynced `change_log` row directly from the database and send that
  row as a standard sync payload with `change_log_id`.
- Queue-origin failures may be returned to `sync:retry`; database-origin failures must not need a
  Redis requeue because the original `change_log.synced=false` row remains the durable retry source.
- A peer response counts as delivered only when HTTP status is 200, response JSON status is
  `success` or `ok`, and response error count is zero. Partial/rejected peer responses leave the
  local row unsynced and therefore operator-visible in sync health.

Required tests:

- Worker sends committed unsynced row without Redis wake-up.
- Direct push failure still leaves row for worker retry.
- Peer rejection keeps row unsynced or terminally blocked with operator-visible state.
- ChangeLog-to-sync payload conversion includes decoded data and `change_log_id`.
- Redis empty timeout with a committed DB row sends and marks the row only after peer acceptance.
- Redis empty timeout with peer rejection does not call the delivered marker and does not require
  Redis requeue.

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

Implemented in Step 6C:

- Sync payloads produced by event listeners, committed outbox worker drains, and manual resync now
  include `sync_meta` with aggregate table/id, authority server, authoritative version or outbox
  sequence, outbox id, and command idempotency id where present.
- The sync receiver compares incoming offer `status`/`version_id` against the existing local offer
  before upsert. Older versions, non-terminal states over existing terminal offers, and conflicting
  same-version terminal states are ignored with `sync.stale_offer_ignored` audit metadata.
- Offer upserts also carry an atomic `ON CONFLICT DO UPDATE WHERE` guard so the same stale-event
  rules still hold if a newer terminal state commits between the receiver's read and write.
- Ignored stale offer events return receiver result `ok` so the sender can mark the outbox row
  delivered instead of retrying an event that must never be applied.
- Duplicate terminal-state replay with the same version remains idempotent, and a newer terminal
  version can still win over an older active state.

Verification:

- `tests.test_sync_metadata`
- `tests.test_core_events`
- `tests.test_sync_worker`
- `tests.test_sync_router_stale_events`
- Existing sync router apply, receive, resync, parsing, and fail-closed policy tests.

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

Implemented in Step 7A:

- Added `offer_publication_states` as the explicit publication-state table for offer surfaces.
  It is separate from `offers.status` and records the surface, publication owner server, state,
  dedupe key, resource/message ids, last attempt/success, retry/lag/disabled timestamps, error
  code/message, and the offer status/version snapshot used by the publication side effect.
- The table is active `SyncPolicy.SYNC`, is included in the sync receiver model map, sequence
  repair, natural-key fallback by `dedupe_key`, sync metadata authority, event listeners, and the
  durable outbox guard path.
- Added service helpers for Telegram-channel and WebApp-market publication state creation,
  terminal-offer conversion to `disabled`, stale offer-version rejection, and pending-to-`lagged`
  detection when the healthy publication target is exceeded.
- This step does not yet send Telegram messages, emit WebApp realtime, or reconcile publication
  workers. Those behaviors remain in Steps 7B and 7C.

Verification:

- `tests.test_offer_publication_state_service`
- `tests.test_sync_metadata`
- `tests.test_sync_registry`
- `tests.test_sync_router_apply_item_success`
- `tests.test_core_events`
- Existing sync router/outbox guard tests covering receiver mapping, table policy, and outbox
  listener registration.

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

Implemented in Step 7B:

- Added `telegram_offer_publication_service` as the idempotent Telegram-channel publication gate.
  It locks/creates the `offer_publication_states` row by the Telegram surface dedupe key derived
  from `offer_public_id`, so worker replay and direct-push replay are not protected only by
  `Offer.channel_message_id`.
- WebApp/API offer creation, direct Bot offer creation, and synced-offer publication on the foreign
  server now publish through the same `publish_offer_to_telegram_channel_once(...)` path. Successful
  publication records the Telegram chat/message id, surface resource id, success timestamp, and
  local legacy `Offer.channel_message_id`; failed publication records a retryable `failed`
  publication state.
- Foreign sync receive no longer skips publication solely because `channel_message_id` is present.
  If legacy `channel_message_id` already exists, the service backfills a `sent` publication state
  instead of creating another Telegram post.
- Terminal Telegram channel state can use either `Offer.channel_message_id` or the synced
  Telegram publication state's `telegram_message_id`, then applies the existing canonical
  `editMessageText` path with `reply_markup=None`.
- Completed offers append `🤝 ✅`, partially traded expired/history offers append
  `🤝 {traded_quantity} تا ✅`, and purely expired offers append `❌`; replaying unchanged terminal
  text remains idempotent because Telegram "message is not modified" is treated as success.
- Manual expiry, auto-expiry, Bot cancel-all, WebApp cancel-all, synced terminal offers, and
  market-close expiry now route through `apply_offer_channel_state(...)` for terminal Telegram
  channel state instead of using separate remove-buttons-only paths.

Verification:

- `tests.test_telegram_offer_publication_service`
- `tests.test_telegram_offer_channel_service`
- `tests.test_sync_router_receive_offer_publish`
- `tests.test_sync_router_remaining_paths`
- `tests.test_market_transition_service`
- `tests.test_offers_router_create_success`
- `tests.test_offer_limit_cross_surface_smoke`
- `tests.test_offer_expiry`
- `tests.test_offers_router_expire`
- `tests.test_bot_trade_create_helper_cancel`
- `python3 -m unittest discover -s tests -p 'test_bot_trade_create*.py'`
- `tests.test_bot_trade_manage_success`
- `tests.test_bot_trade_execute_update_markup`
- Regression coverage from `tests.test_offer_publication_state_service`, `tests.test_sync_metadata`,
  `tests.test_sync_registry`, `tests.test_core_events`, and
  `tests.test_sync_router_apply_item_success`.

### Step 7C - WebApp Realtime Visibility From Synced Market Changes

Required behavior:

- Iran emits local WebApp realtime after applying synced market data.
- Realtime emit does not create a new outbound sync echo.
- Bot-authored offer create/update/expiry/trade becomes visible on WebApp with the expected
  pending/lagged/final states.
- WebApp exposes the canonical offer link route by public identifier. Prefer explicit non-integer
  paths such as `GET /api/offers/public/{offer_public_id}` for safe public data and
  `GET /api/offers/public/{offer_public_id}/detail` for authenticated/authorized detail data, so
  the route cannot be confused with legacy integer offer endpoints.
- Safe public response fields are limited to market-safe data such as `offer_public_id`, status,
  offer type, commodity name, public quantity/price fields, creation time, safe public state label,
  and interaction availability. Do not include requester identity, mobile numbers, customer
  relation details, raw source server, internal failure context, or operator-only publication data.
- Public link access shows only safe market information by default; authenticated authorized users
  can see request ledger, expiry actor/source metadata, and customer-relation context according to
  field policy.
- Authorized detail responses may include request ledger rows, expiry metadata, trade outcomes,
  customer relation snapshots, and safe publication state. Publication state is operator/admin-only
  unless Step 7A explicitly marks a subset as safe for owners.
- Foreign/Bot-generated Telegram text may include the Iran WebApp offer link, but foreign must not
  serve the WebApp page or call Iran WebApp to render it.

Required tests:

- Bot offer sync apply on Iran emits WebApp market event.
- Foreign trade/expiry sync apply on Iran updates WebApp state.
- Offer detail route resolves by public identifier for synced Iran-home and foreign-home offers.
- Public link route returns only the safe public field set for unauthenticated viewers.
- Unauthorized link viewer cannot see requester identities, customer relation details, or sensitive
  failure reasons.
- Authorized owner/admin/detail viewer can see request ledger rows with source platform, request
  time, relation snapshot, and trade outcome.
- Offer detail ledger responses are paginated and do not load unbounded history.
- Realtime failure does not corrupt DB state and is either best-effort-safe or durably repairable
  according to the side-effect classification.

Exit criteria:

- WebApp market view can reflect foreign-home offer changes without Iran calling Telegram.
- Offer links and offer detail metadata are consistent with synced DB truth, not publication state.

Implemented in Step 7C on 2026-06-19:

- `GET /api/offers/public/{offer_public_id}` returns only safe public offer fields by stable public
  identifier.
- `GET /api/offers/public/{offer_public_id}/detail` returns authenticated detail data with
  field-level ledger visibility for owner/requester/admin and admin-only publication state.
- Iran sync receive emits WebApp realtime for synced active offer creation, offer updates, completed
  trade updates, and expiry events with `source=sync_apply` so realtime fanout does not create an
  outbound sync echo.
- Foreign sync receive does not emit WebApp realtime; it remains responsible for Telegram channel
  publication/state side effects only.
- Realtime side-effect failures are logged and isolated from committed sync DB state.

Step 7C verification run:

- `tests.test_offers_public_routes`
- `tests.test_sync_router_receive_basic`
- `tests.test_sync_router_receive_offer_publish`
- `tests.test_sync_router_apply_item_success`
- `tests.test_realtime_router_publish_event`
- `tests.test_offer_request_policy`

## Step 8 - Identity, Callback Compatibility, And Protocol Versioning

Goal: move cross-server identity toward stable public identifiers without breaking existing local
integer references.

This step is large and must be split.

### Step 8A - Public Identity For Synced Records

Required behavior:

- Synced tables get stable `public_id`/UUID identifiers.
- Offer public identity is introduced no later than Step 5A because offer links, request ledgers, and
  callback compatibility depend on it. Step 8A generalizes the pattern to other synced records.
- Integer IDs remain local/internal during migration.
- Cross-server sync payloads and command-forwarding contracts use public identities as canonical
  references where available. Local integer foreign keys may remain inside each database after the
  receiver resolves the public identity.
- Server-partitioned integer sequences are used as a migration guard until cross-server commands no
  longer rely on integers.

Required tests:

- Synced payloads identify records by public identity.
- Peer apply tests resolve local rows from public identity and do not assume matching integer IDs.
- Existing integer-id local reads continue to work.
- Duplicate/colliding integer IDs across servers do not corrupt cross-server resolution.

Exit criteria:

- New cross-server command/sync contracts use public identity where implemented.

Implemented in Step 8A on 2026-06-19:

- Sync payloads now include an explicit `public_identity` object when a stable public/natural
  cross-server key is available.
- Worker replay, direct event push, and manual resync all build the same public identity metadata.
- Offer sync apply uses `offer_public_id` as the conflict key and does not depend on the incoming
  remote integer id for insert/update.
- Trade sync payloads carry `offer_public_id`; the receiver resolves that to the local `offers.id`
  before applying the trade and defers trade apply when the referenced offer is not locally present.
- Offer publication state sync uses `dedupe_key` and localizes `offer_id` from `offer_public_id`.
- Offer request ledger sync uses `request_home_server + idempotency_key` when available and keeps
  `offer_public_id` as the canonical offer reference; unresolved local offer FK is nulled instead
  of trusting the remote integer id.
- Sequence alignment now partitions generated integer ids by server (`iran` even, `foreign` odd) as
  a migration guard while public identity rollout continues.

Step 8A verification run:

- `tests.test_sync_metadata`
- `tests.test_sync_worker`
- `tests.test_core_events`
- `tests.test_sync_router_apply_item_success`
- `tests.test_sync_router_receive_basic`
- `tests.test_sync_router_receive_offer_publish`
- `tests.test_offer_creation_service`
- `tests.test_offer_request_ledger_service`
- `tests.test_offer_request_ledger_model`
- `tests.test_trades_router_authoritative_success`
- `tests.test_trades_router_execution_wrappers`
- `tests.test_bot_trade_execute_local_success`
- `tests.test_bot_trade_manage_success`
- `tests.test_sync_registry`
- `tests.test_sync_outbox_guard`
- `tests.test_trading_production_contract_matrix`

### Step 8B - Telegram Callback Compatibility

Required behavior:

- Existing Telegram callbacks that carry integer offer IDs have a compatibility path.
- New callback payloads are versioned or mapped to canonical offer public identity.
- Old channel messages have a defined behavior after migration.
- Callback handling must create or reuse the same authoritative offer request ledger path as WebApp
  requests after the user confirms the action.
- New callback payloads must not depend on cross-server integer offer IDs. They must resolve to
  `offer_public_id` directly or through a foreign-local compatibility mapping before invoking the
  shared request command.

Allowed designs:

- Versioned callback payloads.
- Short public tokens.
- Callback mapping rows.
- Foreign-local integer resolution to canonical public IDs.

Required tests:

- Old callback payload still resolves or fails visibly with a safe user message.
- New callback payload resolves to the correct canonical offer.
- Callback compatibility tests cover an old integer callback resolving through the local mapping to
  the canonical public offer identity.
- Callback replay remains idempotent.
- New callback request outcomes are visible in the offer request ledger.

Exit criteria:

- Identity migration does not invalidate active Telegram channel interactions without a planned
  compatibility behavior.
- Callback compatibility does not bypass request metadata recording.

Implemented in Step 8B on 2026-06-19:

- New Telegram trade buttons use the short versioned callback payload `ct2:{offer_public_id}:{amount}`.
- Legacy Telegram trade callbacks in the old `channel_trade:{local_offer_id}:{amount}` shape remain
  accepted. The Bot resolves the local foreign offer row first, then uses the row's
  `offer_public_id` before invoking the shared trade/request command.
- Telegram channel publish, channel button refresh, Bot-created offer buttons, and private
  lot-unavailable suggestion buttons all build callback payloads through one shared helper.
- Callback handling resolves public callbacks by `Offer.offer_public_id`; it resolves legacy
  callbacks by local `Offer.id` only as a compatibility mapping.
- Missing or stale callback targets now fail visibly with a safe user message instead of silently
  acknowledging the callback.
- Confirmed Telegram callback commands use `telegram_callback:<callback_id>` as the idempotency key,
  with a deterministic bounded fallback only when the callback id is unavailable.
- Confirmed callback execution still enters `_execute_trade_authoritatively(...)` with
  `request_source_surface=telegram_bot`, so request outcomes continue to be recorded in the offer
  request ledger and synced through the existing ledger path.

Step 8B verification run:

- `tests.test_bot_trade_suggestion_messages`
- `tests.test_bot_trade_execute_local_success`
- `tests.test_bot_trade_execute_offer_guards`
- `tests.test_bot_trade_execute_remote_home`
- `tests.test_bot_trade_execute_update_markup`
- `tests.test_bot_trade_create_confirm_success_retail`
- `tests.test_offers_router_helpers`
- `tests.test_trade_service_validation_and_payloads`
- `tests.test_bot_trade_execute_basic_guards`
- `tests.test_bot_trade_execute_blocked`
- `tests.test_bot_trade_execute_invalid_amount`
- `tests.test_bot_trade_execute_local_pending`
- `tests.test_bot_trade_execute_remaining_paths`
- `tests.test_bot_trade_execute_suggestion_message`
- `tests.test_bot_trade_manage_success`
- `tests.test_trades_router_helpers`
- `tests.test_trades_router_authoritative_success`
- `tests.test_trades_router_execution_wrappers`
- `tests.test_telegram_offer_channel_service`
- `tests.test_telegram_offer_publication_service`

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

Implemented in Step 8C on 2026-06-19:

- New DB sync payloads now include a top-level `sync_protocol` object with protocol version,
  compatible minimum consumer protocol version, payload schema version, registry version,
  registry fingerprint, and producer server identity.
- Direct event push, committed change-log worker replay, and manual `/api/sync/resync` all attach
  the same protocol metadata.
- The receiver validates protocol metadata before table policy checks and before `_apply_item(...)`.
  Unsupported future protocol/schema/registry versions, producer-declared minimum consumer versions
  above local support, missing current-registry fingerprints, and current-version registry
  fingerprint mismatches are rejected as partial sync failures.
- Missing `sync_protocol` metadata remains accepted as a legacy-compatible payload so already queued
  pre-Step-8C change-log rows can still drain during a rolling deploy.
- Declared legacy-compatible protocol version `1` remains accepted when its minimum consumer version
  is within the local supported range.
- Rejected protocol items are returned in `error_items` with version details and are logged with
  `event=sync.protocol_rejected`; the peer sees a partial response, so the worker/resync path does
  not mark the source item as synced.

Step 8C verification run:

- `tests.test_sync_metadata`
- `tests.test_sync_worker`
- `tests.test_core_events`
- `tests.test_sync_router_fail_closed_policy`
- `tests.test_sync_router_resync`
- `tests.test_sync_router_receive_basic`
- `tests.test_sync_router_receive_offer_publish`
- `tests.test_sync_router_receive_sequences`
- `tests.test_sync_router_receive_settings_cache`
- `tests.test_sync_router_receive_errors`
- `tests.test_sync_router_remaining_paths`
- `tests.test_sync_registry`
- `tests.test_sync_outbox_guard`
- `python3 -m py_compile core/sync_protocol.py core/sync_metadata.py core/sync_worker.py core/events.py api/routers/sync.py`

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

Implemented in Step 9A on 2026-06-19:

- Shared admin/product writes now use a single-writer rule: `iran` is the authoritative admin-write
  server for `trading_settings`, `market_schedule_overrides`, `commodities`, `commodity_aliases`,
  admin market/broadcast message rows, and admin-owned `users` fields such as role, account status,
  trade restrictions, user limits, and block capability settings.
- The conflict rule is explicit fail-closed rejection on non-authoritative servers. This is deliberate:
  `commodities` and `commodity_aliases` do not currently carry a reliable row version/timestamp, so
  accepting writes from both servers would create silent forks that sync cannot deterministically merge.
- FastAPI write routes for commodities, trading settings, market schedule overrides, users, and admin
  messages now depend on shared admin-write authority and return HTTP `409` with
  `admin_write_not_authoritative` metadata when the current server is not authoritative.
- Telegram bot admin handlers that still mutate shared admin data directly now reject before commit
  when running on a non-authoritative server. This covers bot system settings, user role/status,
  trading restrictions, user limits, soft deletion, and block capability settings.
- Bot commodity management already writes through the commodities API, so the API authority gate is the
  enforcement point for commodity and alias writes from Telegram admin flows.
- Sync registry entries for the covered admin-mutated tables now declare write surfaces, `iran` admin
  authority, and the single-writer conflict rule. The sync registry version is bumped because the active
  registry fingerprint changed.

Step 9A verification run:

- `tests.test_admin_authority`
- `tests.test_sync_metadata`
- `tests.test_sync_worker`
- `tests.test_core_events`
- `tests.test_sync_router_fail_closed_policy`
- `tests.test_sync_router_resync`
- `tests.test_sync_router_receive_basic`
- `tests.test_sync_router_receive_offer_publish`
- `tests.test_sync_router_receive_sequences`
- `tests.test_sync_router_receive_settings_cache`
- `tests.test_sync_router_receive_errors`
- `tests.test_sync_router_remaining_paths`
- `tests.test_sync_registry`
- `tests.test_sync_outbox_guard`
- `tests.test_trading_settings_router_update`
- `tests.test_trading_settings_router_overrides`
- `tests.test_admin_messages_router`
- `tests.test_users_router_update_basic`
- `tests.test_users_router_delete`
- `tests.test_users_router_update_limits`
- `tests.test_users_router_delayed_removal`
- `tests.test_bot_panel_admin_menu`
- `tests.test_bot_panel_admin_settings_entry`
- `tests.test_bot_panel_settings_helpers`
- `tests.test_bot_panel_settings_new_value`
- `tests.test_bot_panel_settings_reset`
- `tests.test_bot_panel_simple_settings`
- `tests.test_bot_panel_user_settings`
- `tests.test_bot_admin_users_unblock_unlimit`
- `tests.test_bot_admin_users_limit_flow`
- `tests.test_bot_admin_users_limit_start`
- `tests.test_bot_admin_users_role_actions`
- `tests.test_bot_admin_users_block_actions`
- `tests.test_bot_admin_users_block_custom`
- `tests.test_bot_admin_users_block_settings`
- `tests.test_bot_admin_users_bot_access`
- `tests.test_bot_admin_users_delete_flow`
- `tests.test_bot_admin_users_entry_navigation`
- `tests.test_bot_admin_users_helpers`
- `tests.test_bot_admin_users_profile_text`
- `tests.test_bot_admin_users_search_entry_cancel`
- `tests.test_bot_admin_users_search_process`
- `tests.test_bot_admin_users_settings_menu`
- `tests.test_bot_admin_users_show_list`
- `tests.test_bot_admin_commodities_add_aliases_create`
- `tests.test_bot_admin_commodities_add_create`
- `tests.test_bot_admin_commodities_add_flow`
- `tests.test_bot_admin_commodities_alias_add`
- `tests.test_bot_admin_commodities_alias_edit`
- `tests.test_bot_admin_commodities_alias_delete`
- `tests.test_bot_admin_commodities_commodity_edit`
- `tests.test_bot_admin_commodities_delete_cancel`
- `tests.test_bot_admin_commodities_entry_points`
- `tests.test_bot_admin_commodities_helpers`
- `tests.test_bot_admin_commodities_message_helpers`
- `tests.test_bot_admin_commodities_show_aliases`
- `tests.test_bot_admin_commodities_show_list`
- `python3 -m py_compile core/admin_authority.py api/admin_authority.py core/sync_registry.py core/sync_protocol.py api/routers/commodities.py api/routers/trading_settings.py api/routers/users.py api/routers/admin_messages.py bot/handlers/panel.py bot/handlers/admin_users.py`

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

Implementation decisions for Step 9B:

- The code-owned matrix lives in `core/background_job_authority.py`; documentation alone is not
  sufficient for this step.
- API background scheduling must pass through `filter_allowed_background_job_factories()`.
- `offer_expiry` is allowed on both servers, but it may only mutate local-home offers and must use
  `expire_offers_authoritatively`.
- `market_schedule` is allowed on both servers because each side must close its own local-home
  offers when the market closes; offer mutations from this job must still use
  `expire_offers_authoritatively`.
- `session_expiry` is a local runtime/no-sync cleanup for `user_sessions` and is allowed on both
  servers.
- `user_account_status` is Iran-only because it mutates shared account-status fields and produces
  synced notification/account effects.
- `connectivity_monitor` is Iran-only and writes only local Redis runtime state.
- `sync_worker` is also declared in the matrix as a recurring committed-outbox delivery worker; it
  may run on both servers and only mutates local internal `change_log` delivery state.

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

Implementation decisions for Step 9C:

- The code-owned field matrix lives in `core/sync_field_policy.py`; it classifies fields as
  `sync`, `no-sync`, `hash-only`, or `encrypted/derived`.
- `users.admin_password_hash` and `users.must_change_password` are local WebApp auth state and must
  be dropped from sync payloads.
- `users.avatar_file_id` is a raw FK to no-sync `chat_files`; it is explicitly no-sync and must be
  dropped rather than sent as a cross-server FK.
- Web Push subscription material is Iran-local runtime data. Raw `endpoint`, `p256dh`, `auth`,
  `user_agent`, `platform`, and `last_error` must not cross servers; endpoint may only be represented
  as a hash if a diagnostic payload ever needs it.
- Producer paths (`log_change`), committed change-log drain, manual resync, and receiver parsing must
  all apply the same field policy so legacy dirty rows or malicious peer payloads cannot bypass it.
- The sync registry fingerprint includes the field policy fingerprint; field-policy drift between
  servers must fail visibly instead of being accepted silently.

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

Implementation decisions for Step 10A:

- Publication reconciliation lives in `core/services/offer_publication_reconciliation_service.py`.
- `/api/sync/health` now includes `publication_reconciliation` with state counts and finding counts
  for Telegram publication drift, WebApp market publication drift, and unsynced offer/publication
  backlogs.
- `scripts/sync_probe_worker.py reconcile-publications` is the operator entry point. It is dry-run by
  default; `--repair` is required before any mutation or Telegram send can occur.
- On `foreign`, reconciliation can report/repair active offers without Telegram state, failed or
  lagged Telegram publication states, Telegram posts whose state row is incomplete, and sent
  publication states whose legacy `Offer.channel_message_id` is missing.
- On `iran`, reconciliation can report/repair missing or stale WebApp market publication state. It
  never calls Telegram from Iran.
- Sync conflict observability is metric-backed through `trading_bot_sync_conflicts_total`; publication
  state/finding gauges are exposed as `trading_bot_offer_publication_states` and
  `trading_bot_offer_publication_reconciliation_findings`.

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
- Each offer gets a stable link/public identifier and the link resolves on Iran WebApp.
- Owner expiry from WebApp and Bot.
- User-triggered expiry records source platform/server and actor metadata.
- Auto-expiry records lifetime/system metadata.
- Telegram channel terminal markers for fully traded (`🤝 ✅`), partially traded
  (`🤝 {traded_quantity} تا ✅`), and expired (`❌`) offers.
- Telegram channel terminal edits remove inline buttons for traded and expired offers.
- Request/trade from WebApp and Bot against both Iran-home and foreign-home offers.
- Request ledger rows for successful trade, rejected request, lot unavailable, duplicate replay,
  stale/conflict, and request after expiry.
- Request ledger state-machine transitions for `received`, authorized/completed, every rejected
  terminal state, duplicate replay, and failed-internal outcomes.
- Public failure code/message visibility separated from internal failure code/context visibility.
- Customer requester ledger rows include relation snapshots and authorized-only visibility.
- Offer detail link shows safe public fields to unauthenticated/unauthorized viewers and full
  permitted metadata to authorized viewers.
- Offer detail ledger pagination and retention/archive assumptions.
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
- Existing offer rows are backfilled or verified to have public identifiers before link/callback
  flows are enabled.
- Offer request ledger migrations are non-destructive and preserve existing trade history.
- Historical request ledger backfill is not attempted from old trades unless explicitly approved.
  Existing trades may be linked to offers where local foreign keys are reliable, but missing request
  attempts remain `unknown_legacy` rather than being invented.
- Backfill report includes at least: total offers, offers with public identifiers before and after
  migration, offers still missing public identity, trades linked to offers, old channel message
  bindings, and any rows carrying `legacy_unknown` expiry/source metadata.
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

## Milestone Review Groups

These groups are only review boundaries. They do not replace the step-by-step completion rules.

- Milestone A - Safety Foundation: Step 0, Step 1, Step 2A, Step 2B, Step 3A, Step 3B, Step 4A.
- Milestone B - Offer Metadata Foundation: Step 5A, Step 5B, Step 5C-0, Step 5C.
- Milestone C - Shared Command Migration: Step 5D, Step 6A, Step 6B, Step 6C.
- Milestone D - Publication, Detail, Callback: Step 7A, Step 7B, Step 7C, Step 8A, Step 8B,
  Step 8C.
- Milestone E - Operations And Cutover: Step 9A, Step 9B, Step 9C, Step 10A, Step 10B, Step 10C,
  Step 11, Step 12.

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
10. Step 5A - Shared Offer Creation Command And Offer Link Identity
11. Step 5B - Shared Expire/Cancel Command
12. Step 5C-0 - Minimum Field Policy For Offer Link And Request Ledger
13. Step 5C - Offer Request Ledger And Metadata Schema
14. Step 5D - Shared Trade/Request Command
15. Step 6A - Mandatory Transactional Outbox For Synced Tables
16. Step 6B - Committed Outbox Drain And Retry Contract
17. Step 6C - Aggregate Ordering And Stale-Event Rejection
18. Step 7A - Surface Publication State Model
19. Step 7B - Telegram Publication Idempotency And Result Sync-Back
20. Step 7C - WebApp Realtime Visibility From Synced Market Changes
21. Step 8A - Public Identity For Synced Records
22. Step 8B - Telegram Callback Compatibility
23. Step 8C - Registry And Protocol Version Compatibility
24. Step 9A - Admin Surface Authority
25. Step 9B - Background Job Authority Matrix
26. Step 9C - Field-Level Sensitive And No-Sync Reference Policy
27. Step 10A - Reconciliation Jobs And Observability
28. Step 10B - Short Outage Behavior Up To 2 Minutes
29. Step 10C - Medium And Long Outage Recovery Finalization
30. Step 11 - Complete Scenario Matrix And Staging Validation
31. Step 12 - Cutover Readiness And Production Gate Preparation
