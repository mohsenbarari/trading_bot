# Bot/WebApp Cross-Server Policy And Challenges

Date: 2026-06-17

This document captures the target operating policy for the Telegram bot, Iran WebApp, and
cross-server sync. It is the working basis for the next design Q&A rounds.

## Non-Negotiable Policy

1. Iran server never connects to Telegram.
2. WebApp is served only from the Iran server.
3. Telegram bot runs only on the foreign server.
4. Foreign server never serves or connects users to the WebApp.
5. All non-messenger table data must sync between the two servers as close to real time as possible.
6. Messenger is excluded from cross-server sync and lives only on the Iran WebApp/server.
7. Offers created from the Telegram bot must appear on the WebApp immediately.
8. Offers created from the WebApp must appear in the Telegram bot/channel immediately.
9. Offers created from the Telegram bot have `offer_home_server=foreign`.
10. Offers created from the WebApp have `offer_home_server=iran`.
11. Users may be active in the WebApp and Telegram bot at the same time.
12. `Offer.home_server` is determined only by the source surface where that offer is published.
    `user.home_server`, active session state, or the user's previous platform must never decide
    offer authority.
13. Offer creation must carry an explicit `source_surface`. Confirmed values are `telegram_bot`,
    `webapp`, and `internal_sync`. `telegram_bot` maps to `offer_home_server=foreign`, `webapp`
    maps to `offer_home_server=iran`, and `internal_sync` must preserve the incoming
    `Offer.home_server` without recomputing it.
14. `chats` and `chat_members` are messenger-owned tables for cross-server policy purposes and
    must not be wholesale-synced. Direct chats, groups, optional channels, read/mute/pin/hide state,
    and message-linked fields are Iran/WebApp-local. The mandatory system channel is the only
    current non-messenger-like use on these tables and should become a local projection rebuilt from
    synced `users`, not a reason to sync all chat rows.
15. Deployment/config validation for this roadmap must prove that the Iran stack has no active bot
    service, has no Telegram credentials or Telegram outbound path, and that the foreign stack has
    the bot service. These checks must run before staging validation.
16. All Telegram side effects must go through one central gateway. The gateway must hard-fail on
    `server_mode=iran` because Iran-side Telegram connectivity is both prohibited by policy and
    operationally unavailable due to filtering. The foreign server is the only Telegram execution
    surface.
17. The foreign server must have a hard WebApp/static guard. It must not serve frontend assets,
    public WebApp routes, or user-facing WebApp entrypoints. Only explicitly allowed internal
    endpoints such as sync, health, and maintenance may remain reachable there, and this guard must
    be validated in staging.
18. When Iran applies synced market data that must be visible in the WebApp, it must publish a
    local post-commit WebApp realtime event without creating a new sync entry or echo. This covers
    bot-authored offer creation and market-impacting offer/trade/expiry updates received through
    sync.
19. `/api/chat` is user-facing messenger API and must fail closed on the foreign server. A reverse
    proxy block is useful but not sufficient by itself; the API must also guard user-facing chat
    requests. Any future foreign-side chat-related internal endpoint must be explicitly allowlisted
    by path and purpose.
20. Runtime/auth state is surface-local unless explicitly promoted later. WebApp sessions stay local
    to Iran, Bot FSM state stays local to foreign, and login/recovery request rows should not sync.
    `telegram_id`, user profile, account status, role, and limits are user/account product data and
    should sync.
21. Every `user.home_server` read and write must be audited and classified as session/auth
    authority, active login surface/runtime state, legacy compatibility, or an offer-authority bug.
    The offer-authority category is not allowed as final behavior and must be removed.
22. Offer creation from both WebApp and Telegram bot must route through one shared
    command/service. API routers and Bot handlers are only surface adapters. The shared service
    must receive `source_surface`, `actor_user`, `request_home_server`, and `offer_home_server`;
    run market/domain validation; create the offer; record sync/outbox state; and define
    post-commit side effects for Iran and foreign surfaces.
23. Trade/request execution from both WebApp and Telegram bot callbacks must route through one
    shared authoritative and idempotent command/service. If the current server is not the
    `offer_home_server`, it must forward the command to the offer home server and must not perform
    local trade or offer mutation. The authoritative service owns validation, trade-number
    allocation, offer quantity/status mutation, idempotency, sync/outbox recording, and
    post-commit side effects.
24. Every offer expire/cancel path must route through the shared `expire_offers` command/service:
    Bot cancel-all, API cancel-all, single-offer expiry, auto-expiry, market-close expiry, and
    remote-forwarded expiry. If the current server is not the `offer_home_server`, it must forward
    the command and must not perform local offer mutation. The service must mutate authoritative DB
    state first, commit, then run explicit post-commit side effects.
25. A formal sync registry is required before broad sync changes. Every model/table must have an
    explicit entry that declares `sync` or `no-sync`, write surfaces, authority, conflict rule, and
    side effects. Tests/CI must fail when a model/table or migration introduces a table without a
    registry entry.
26. Every bulk `update()`, bulk `delete()`, raw SQL write, and relationship side effect must be
    audited because these paths can bypass ORM listeners and sync/outbox recording. Each case must
    move to a sync-aware helper or explicitly record the required sync/outbox event.
27. The sync worker's durable source of truth must be committed `change_log` rows where
    `synced=false`. Redis queue messages and direct HTTP pushes should remain only as low-latency
    wake-up/acceleration paths; if they are missed or fail, the committed `change_log` row must
    still be drained and delivered by the worker.
28. Telegram publication must be idempotent independently of `channel_message_id`. Foreign-side
    publish must use a separate dedupe/outbox marker such as offer + target + action, must avoid
    duplicate Telegram posts across direct push and worker replay, and must sync the foreign
    publication result back to Iran without letting Iran call Telegram.

Policy note: item 5 and item 6 create an explicit exception. "All tables" means all product
tables except the messenger-owned data set. The confirmed messenger-owned set includes at least
`messages`, `conversations`, `chat_files`, `upload_batches`, `upload_sessions`, `chats`, and
`chat_members`.

## Branch And Environment Enforcement

This section is mandatory for all work derived from this document.

1. Bot/WebApp integration work belongs only on `candidate/bot-webapp-integration`.
2. `main` remains the protected baseline branch. It must not receive Bot/WebApp integration work
   unless the owner explicitly asks for a merge or direct commit.
3. WebApp-only bug fixes must use their own WebApp fix branch or branches. They must not be mixed
   into `candidate/bot-webapp-integration` unless the owner explicitly asks to combine the work.
4. Multiple agents may work in this repository and may switch branches without warning. Never rely
   on an earlier branch check.
5. Before every code change, documentation change, commit, push, staging deploy, or validation run
   that could affect this roadmap, the current branch must be checked with `git branch --show-current`.
6. Before every commit, the current branch must be checked again, even if it was checked earlier in
   the same turn.
7. If the current branch is not exactly `candidate/bot-webapp-integration`, work for this roadmap
   must stop until the branch is corrected.
8. All commits for this roadmap must be made on `candidate/bot-webapp-integration`.
9. A manual branch check is accepted as the required branch-policy guard for this roadmap. A new
   pre-commit hook, CI rule, or script is not required unless the owner asks for automation later.
10. Staging is the only allowed runtime validation environment for this roadmap unless the owner
   explicitly approves another target.
11. Staging validation must use isolated staging configuration, staging artifacts, staging data, and
   no production sync peer.
12. No change from this roadmap may be merged into `main`, any WebApp fix branch, any other candidate
   branch, or any release branch unless the owner explicitly says to merge it.
13. No change from this roadmap may be deployed to production, benchmarked against production,
    run against production data, or used to change production sync/Telegram/WebApp behavior unless
    the owner explicitly requests that production action.
14. Pushing `candidate/bot-webapp-integration` to remote is allowed for backup/review, but that push
    does not imply approval to merge, release, or deploy.

## Current Code Facts

- `docker-compose.yml` runs `app`, `bot`, `sync_worker`, DB, and Redis on the foreign stack.
- `docker-compose.iran.yml` runs `app`, `sync_worker`, DB, and Redis, and comments out the bot.
- `main.py` mounts all API routers, including `/api/chat`, on every API process.
- `core/events.py` writes sync entries to `change_log`, pushes Redis `sync:outbound`, and submits direct push.
- `core/sync_worker.py` consumes `sync:outbound` and `sync:retry`, then posts to `/api/sync/receive`.
- `api/routers/sync.py` applies a manual table allow-list/order. It currently includes `chats` and `chat_members`.
- `api/routers/sync.py` does not include `messages`, `conversations`, `chat_files`, or upload-session tables.
- `api/routers/offers.py` sets Web/API-created offer `home_server` from `owner_user.home_server` when present.
- `bot/handlers/trade_create.py` creates bot offers directly and currently relies on the model default for `home_server`.
- `api/routers/trades.py` forwards trade execution to the remote offer home server when needed.

## Implementation Status Snapshot

This snapshot is based on the current repository state on 2026-06-17. It is not a
final architecture approval; it only classifies what the code already does, what is
incomplete, and what is still missing relative to the policy above.

### Complete And No-Change Baseline

These items already match the target policy or provide a correct baseline behavior. They should
be kept and regression-tested, but they do not need redesign right now.

- Iran compose does not run the Telegram bot service. `docker-compose.iran.yml` documents the bot
  as disabled and only runs the API, sync worker, DB, Redis, and supporting jobs.
- The foreign compose runs the Telegram bot service. This already matches the "bot only on
  foreign" deployment split.
- Both foreign and Iran compose files run `sync_worker`. The service presence is correct; later
  work is about delivery semantics, not whether the worker should exist.
- The sync receiver uses a signed internal endpoint with API key, timestamp, and HMAC
  verification. This is the right trust boundary for cross-server writes.
- Synced writes use `execution_options(is_sync=True)`, and model listeners skip sync-originated
  changes. This echo-loop guard should stay as the base pattern.
- The sync receiver intentionally avoids overwriting `channel_message_id` from incoming offer
  payloads. This is correct because Telegram publication state must stay foreign-owned.
- Trade execution already has an authority-aware path: when an offer belongs to the other server,
  the trade command is forwarded to the offer home server instead of being applied independently.
- Direct messenger tables `messages` and `conversations` are not in the sync model map today.
  This matches the Iran-only messenger policy for those two tables.

### Working Foundations To Keep But Still Harden

These pieces are useful and should be preserved, but they are not complete enough to be called
"no-change" items.

- ORM-level listeners already exist for many non-messenger product tables: users, offers, trades,
  relations, commodities, aliases, trading settings, notifications, user blocks, invitations,
  market schedule/runtime state, and admin messages.
- Foreign-side publish of synced WebApp offers is partially protected against duplicate sends by
  checking `channel_message_id IS NULL` and using `SELECT ... FOR UPDATE SKIP LOCKED`.
- The sync receiver repairs sequences after applying remote rows. This is a useful safety step
  after receipt, but it does not solve simultaneous two-server insert collisions.
- The current direct push plus Redis queue design gives low-latency delivery when everything is
  healthy. It should be retained as an acceleration/wake-up path, while committed `change_log`
  draining becomes the reliability source.

### Bot-Specific Findings From External Review

The review in `tmp/bot.md` was cross-checked against the current code. The following points are
accepted as accurate and should influence the Bot roadmap.

#### Bot Strengths To Preserve

- `run_bot.py` has a clear Bot startup path: DB init, SQLAlchemy event listener registration,
  Redis FSM storage, auth/logging middleware, trade/admin routers, and the trade-suggestion event
  listener.
- The Persian text-offer UX is valuable. `bot/utils/offer_parser.py` normalizes Persian/Arabic
  numerals, detects buy/sell intent, parses quantity/price/lot structure, resolves commodities and
  aliases, and supports a practical market shorthand.
- Bot text-offer handlers already include useful user-facing guards: market closed, unknown user,
  watch-only role, account restrictions, active FSM state, price warnings, and active-offer caps.
- The channel-button and private-suggestion flow is a Bot-specific product strength. The Redis-backed
  suggestion records and listener that updates private suggestion messages should be preserved.
- There is focused test coverage around Bot text-offer parsing, warning flow, trade callback guards,
  remote-home forwarding, local pending state, and invalid lot suggestions.

#### Confirmed Bot Gaps

- Bot-created offers rely on the `Offer.home_server` model default of `foreign` instead of setting
  `home_server=foreign` explicitly at the Bot write surface.
- Bot local trade execution is still a separate money path from the API. It allocates trade numbers
  with `MAX(trade_number)+1`, mutates offer remaining quantity/lot/status directly, creates
  notifications, updates Telegram channel markup, and publishes realtime events inside the handler.
  The API path has stronger advisory-lock, idempotency, customer-chain, conflict-handling, and
  authoritative-forwarding behavior.
- Bot offer creation commits the offer first, sends the Telegram channel message second, and stores
  `channel_message_id` in a later commit. If Telegram publish or the second DB commit fails, DB,
  channel, and sync state can temporarily diverge.
- Bot cancel-all (`نشد`) performs Telegram HTTP calls, realtime publishes, and cache updates before
  the final DB commit. If the commit fails, side effects can get ahead of durable DB state.
- The target fix for Bot cancel-all is not a local Bot-only patch. It is the first rollout target
  for the confirmed shared `expire_offers` command/service, which must eventually cover every
  expire/cancel path with DB-first mutation, commit, authority forwarding, and explicit
  post-commit side effects.
- Telegram side effects are scattered across Bot handlers, API routers, and core helpers. A central
  gateway is needed so "Iran must never call Telegram" is enforced once instead of by convention.

#### Review Recommendations Accepted As Direction

- Add the confirmed source-surface concept with `telegram_bot`, `webapp`, and `internal_sync`,
  and use it to choose offer authority and side effects. `internal_sync` preserves the source
  server's incoming `Offer.home_server` and must not reclassify synced offers.
- Use the confirmed shared offer creation and shared trade execution command/services for both
  WebApp and Bot writes. Bot handlers become thin UX adapters and do not own market authority,
  trade-number allocation, customer-chain rules, or offer mutation logic.
- Use the confirmed shared `expire_offers` command/service for all expire/cancel paths: Bot
  cancel-all, API cancel-all, single-offer expiry, auto-expiry, market-close expiry, and
  remote-forwarded expiry. Non-authoritative servers forward to `offer_home_server` and do not
  mutate offers locally.
- Use the confirmed idempotent Telegram publication gateway/outbox model. The exact schema is still
  a design decision, but the needed behavior is clear: foreign-only execution, dedupe key independent
  of `channel_message_id`, retry state, durable status, and sync-back of publication result.
- Route every Telegram side effect through a central gateway. Confirmed covered actions include
  offer publication, channel message updates, expire/cancel channel updates, trade button/message
  updates, and Telegram-bound notifications. Iran-side calls must fail closed.
- Treat WebApp sessions, Bot FSM state, Telegram runtime state, login requests, and recovery
  requests as surface-scoped auth/runtime state. Confirmed synced user/account data includes
  `telegram_id`, profile fields, account status, role, and limits.
- Treat `user.home_server` as an overloaded legacy field until redesigned. Every read/write must be
  classified as session/auth authority, active login surface/runtime state, legacy compatibility, or
  an offer-authority bug. The offer-authority category must be removed from final behavior.

#### Review Points Not Adopted As Final Design Yet

- Specific folder names and table names proposed in the review, such as `telegram_outbox` or
  `mandatory_memberships`, are useful options, not final decisions.
- Blocking WebApp offer creation on foreign is correct for public WebApp/user traffic. Internal
  sync, health, and signed maintenance endpoints still need explicit allow rules.
- Excluding session/login/recovery runtime tables from sync is accepted as direction. Existing
  session authority behavior may still perform signed cross-server checks, but that does not mean
  those runtime rows should be general replicated product data.
- `chats` and `chat_members` are accepted as messenger-owned tables. Any short-term sync support for
  `is_system=true AND is_mandatory=true` rows is transitional only; the target design is a local
  mandatory-channel projection derived from synced `users`.

### Partially Implemented Or Ambiguous Policies

- "WebApp only on Iran" is operationally intended, but the shared API still contains frontend
  serving code and all routers on every API process. There is no hard code-level guard that
  refuses WebApp/static frontend service on foreign.
- "Foreign never connects users to WebApp" is not structurally enforced in the app. It depends
  on deployment, Nginx, and available `mini_app_dist` rather than an explicit foreign-side
  refusal.
- "Iran never connects to Telegram" is deployment-aligned because the bot service is disabled
  on Iran, but shared API code still contains Telegram side-effect helpers. The invariant is not
  centralized as a hard runtime guard.
- "All non-messenger tables sync immediately" is only partially true. Many tables have ORM
  listeners, but not every table/model has a declared sync policy, and bulk SQL updates can
  bypass listeners. The bulk/raw/relationship audit is now confirmed, and each bypassing mutation
  must move to a sync-aware helper or write explicit sync/outbox records.
- Messenger exclusion is incomplete. `messages` and `conversations` are excluded from the sync
  router, but `chats` and `chat_members` are still synced and have event listeners. These two tables
  are now confirmed as messenger-owned and should leave the general sync map after the mandatory
  system-channel projection is made local/rebuildable.
- Bot-created offers will usually get `home_server=foreign` through the model default, but the
  bot handler does not set that value explicitly from the bot surface.
- WebApp-created offers are not guaranteed to get `home_server=iran`; current API creation uses
  `owner_user.home_server` first, which conflicts with the surface-based offer-home policy.
- WebApp-created offers can be published on foreign after sync receive, but the confirmed
  independent Telegram publish idempotency/outbox model is not implemented yet; the current model is
  still tied mostly to `channel_message_id` and row locking.
- Bot-created offers can sync to Iran DB, but the confirmed Iran-side WebApp realtime publication
  after applying synced market changes is not explicitly encoded in the sync receive path.
- Session/auth state has cross-server logic in places, but the new policy allows simultaneous
  WebApp and bot activity. The policy now treats WebApp sessions, Bot FSM, login requests, and
  recovery requests as surface-local runtime/auth data; implementation still needs explicit guards
  and tests so they do not enter general sync by accident.
- `user.home_server` is overloaded. Auth code writes it from the login/request server, session
  authority reads it as the user's session home, and offer creation currently reads it as offer home.
  These meanings conflict once a user can use WebApp and Bot at the same time. The audit
  classification is now confirmed; only session/auth authority, active login surface/runtime state,
  and legacy compatibility may remain after offer-authority uses are removed.
- ID collision handling is only partial. The receiver repairs sequences after applying remote
  rows and has natural-key fallbacks for some tables, but independent two-way inserts can still
  create the same integer ID before either side receives the other row.

### Not Implemented Or Not Yet Encoded

- The confirmed sync registry does not exist yet. It must declare every table's `sync` or
  `no-sync` policy, write surfaces, authority server, conflict rule, and side effects, and it must
  fail tests/CI when a table is missing from the registry.
- The confirmed durable committed outbox drain is not implemented. `change_log` exists, but the
  worker consumes Redis queues; replaying committed `change_log WHERE synced=false` is currently a
  manual/resync path rather than the normal delivery loop.
- A server-mode Telegram gateway that hard-fails all Telegram calls on Iran does not exist, and
  Telegram side effects are not yet forced through a single shared gateway.
- The confirmed independent Telegram publish idempotency/outbox model is not implemented yet.
  Current foreign publish behavior still depends mostly on `channel_message_id` and row locking.
- A server-mode WebApp/static gateway that hard-fails frontend service, static asset service, and
  public user WebApp access on foreign does not exist. `/api/chat` is still mounted with the shared
  API and does not yet have a foreign-side user-facing block.
- Surface-based offer-home assignment is not implemented for WebApp creation.
- Explicit bot-side offer-home assignment is not implemented.
- A shared `expire_offers` command/service is not implemented. Bot cancel-all, API cancel-all,
  single-offer expiry, auto-expiry, market-close expiry, and remote-forwarded expiry still use
  separate paths with inconsistent side-effect timing and ownership behavior.
- Automated deployment/config assertions are not implemented yet. The project must verify the Iran
  compose has no active bot service, the foreign compose has the bot service, and Iran has no
  Telegram credentials/outbound path before staging validation.
- A complete messenger exclusion contract is not implemented. The excluded table list is now
  conceptually decided, but sync-router/event-listener changes and tests are still missing for
  `chats` and `chat_members`.
- A complete conflict policy for concurrent two-server writes is not implemented.
- Globally safe IDs or server-partitioned sequences are not implemented.
- The confirmed bulk/raw/relationship write audit is not implemented yet. Sync-aware helpers or
  explicit sync/outbox logging are not implemented consistently across the codebase.
- End-to-end tests for bot offer -> Iran WebApp realtime and WebApp offer -> foreign Telegram
  publish -> result sync back are not implemented.
- Cross-server outage behavior is not defined: the system does not yet declare whether local offer
  creation should continue, queue with pending state, or be blocked when the peer is unavailable.

### Challenge Roadmap From Easy To Hard

This ordering is about implementation difficulty and blast radius, not business priority.

#### Level 1 - Low-Risk Explicitness

1. Set `home_server=foreign` explicitly in Telegram bot offer creation instead of relying on the
   model default.
2. Set WebApp/API-created offer home from the write surface/server, not from `owner_user.home_server`.
3. Add a regression test proving the same user can create a Bot offer with `home_server=foreign`
   and then create a WebApp offer with `home_server=iran`, regardless of `user.home_server`.
4. Implement the confirmed source-surface enum/constant and use it in offer creation tests.
5. Add the confirmed offer switching acceptance matrix below as executable or at least reviewable
   test cases.
6. Add tests that assert messenger-owned tables are not accepted by the sync model map. This includes
   `messages`, `conversations`, `chat_files`, `upload_batches`, `upload_sessions`, `chats`, and
   `chat_members`.
7. Add deployment/config assertions that the Iran compose has no active bot service, the foreign
   compose has the bot service, and Iran has no Telegram credentials or outbound Telegram path. Run
   these checks before staging validation.
8. Keep the documented manual branch-check checklist as the accepted guard so roadmap commits are not
   made accidentally from the wrong branch. No extra script, hook, or CI rule is required for now.

#### Level 2 - Guardrails And Local Side Effects

1. Add the confirmed central Telegram side-effect gateway. Every Telegram call must pass through it,
   foreign is the only allowed execution surface, and `server_mode=iran` must fail closed with an
   operational/security log.
2. Add the confirmed server-mode WebApp/static guard so the foreign API cannot accidentally serve
   frontend assets, public WebApp routes, or user-facing WebApp entrypoints. Only explicitly allowed
   internal routes such as sync, health, and maintenance may remain reachable, and staging must
   validate the guard.
3. After synced market changes are applied on Iran, publish a confirmed local post-commit WebApp
   realtime event without creating a sync echo. This covers bot-authored offer creation plus
   offer/trade/expiry updates that affect WebApp market state.
4. Add the confirmed `/api/chat` foreign guard. User-facing chat requests must fail closed on
   foreign at the API layer, with reverse-proxy blocking as defense in depth. Any internal
   foreign-side chat exception must be explicitly allowlisted by path and purpose.
5. Build the confirmed shared `expire_offers` command/service foundation and route Bot cancel-all
   through it first. The service must mutate authoritative DB state first, commit, then run explicit
   post-commit side effects such as Telegram gateway updates, WebApp realtime events, cache updates,
   and notifications.
6. Implement the confirmed runtime/session state policy. WebApp sessions are Iran-local, Bot FSM is
   foreign-local, and login/recovery requests do not sync. `telegram_id`, user profile, account
   status, role, and limits sync as user/account product data.
7. Convert mandatory system-channel behavior into a local projection rebuilt from synced `users`.
   During transition, allow only a narrowly guarded `is_system=true AND is_mandatory=true`
   compatibility path if needed; do not keep wholesale `chats`/`chat_members` sync.
8. Implement the confirmed `user.home_server` audit. Classify each read/write as session/auth
   authority, active login surface/runtime state, legacy compatibility, or offer-authority bug.
   Offer-authority uses must be removed in this stage.

#### Level 3 - Sync Coverage And Delivery Reliability

1. Implement the confirmed shared offer creation command/service and make both WebApp and Bot call
   it. The service owns `source_surface`, `actor_user`, `request_home_server`,
   `offer_home_server`, market validation, offer row creation, sync/outbox recording, and
   post-commit side-effect selection.
2. Implement the confirmed shared trade/request execution command/service and make WebApp requests
   and Bot channel callbacks use the same authoritative/idempotent path. If the current server is
   not the offer home server, the command must be forwarded to `offer_home_server` and no local
   trade or offer mutation may run.
3. Complete the confirmed shared `expire_offers` command/service coverage for API cancel-all,
   single-offer expiry, auto-expiry, market-close expiry, and remote-forwarded expiry. It must
   enforce offer-home authority and forward commands to `offer_home_server` when the local server
   is not authoritative, without local offer mutation.
4. Implement the confirmed sync registry for every model/table with `sync` or `no-sync`, write
   surfaces, authority, conflict rule, and side effects. Add a test/CI gate that fails when a new
   model/table or migration introduces a table without a registry entry.
5. Implement the confirmed audit of all bulk `update()`, bulk `delete()`, raw SQL, and relationship
   side effects. Every bypassing mutation must move to a sync-aware helper or explicitly record the
   required sync/outbox event.
6. Implement the confirmed committed outbox drain: the sync worker's durable source must be
   committed `change_log WHERE synced=false`. Keep Redis/direct push only as wake-up/acceleration,
   so missed queue/direct events cannot lose a committed sync change.
7. Implement the confirmed Telegram publication idempotency/outbox model. It must use a dedupe key
   independent from only `channel_message_id`, prevent duplicate posts across direct push and worker
   replay, and sync the foreign publication result back to Iran.
8. Add end-to-end tests for Bot offer -> Iran WebApp realtime and WebApp offer -> foreign Telegram
   publication without duplicate channel posts.
9. Add acceptance gates for every switching scenario: DB state, WebApp realtime state, Telegram
   channel/user-message state, notification state, and sync backlog must all be asserted.

#### Level 4 - Recovery, Reconciliation, And Operations

1. Define reconciliation jobs for failed or partial flows: offer synced but Telegram publish failed,
   Telegram post exists but `channel_message_id` is missing, foreign publish result did not sync back
   to Iran, duplicate direct-push/worker delivery arrived, or a peer was down during local writes.
2. Add observability for sync lag, committed outbox backlog, retry backlog, Telegram publish pending
   and failed counts, orphan Telegram posts, stale WebApp market state, and conflict counts.
3. Add an operator runbook for staging incidents: how to inspect backlog, replay outbox rows, recover
   Telegram publication state, verify Iran never called Telegram, and verify foreign never served
   WebApp/messenger.
4. Define degraded-mode behavior per surface: whether Bot/WebApp offer creation continues, becomes
   pending, or is blocked when peer sync, Telegram API, Redis, or DB connectivity is degraded.

#### Level 5 - Core Distributed-System Decisions

1. Choose a globally safe ID strategy: UUID/public IDs or server-partitioned integer sequences.
2. Define per-table conflict policy for concurrent writes: owner, natural key, merge rule, version
   check, and allowed write surfaces.
3. Redesign session/auth semantics for simultaneous WebApp and bot activity without letting
   `user.home_server` control offer authority. Strong candidate direction: replace or narrow
   `user.home_server` into explicit session-authority/surface-scoped fields so a WebApp login cannot
   flip Bot offer authority and a Bot login cannot flip WebApp offer authority.
4. Define migration and rollout for existing data: current integer IDs, PostgreSQL sequences, old
   offers, existing `channel_message_id` values, user sessions, Telegram bindings, and any partially
   synced rows.
5. Define rollback criteria and rollback mechanics for staging and later production promotion.
6. Define final production acceptance gates, but do not run them or promote this work until the owner
   explicitly requests production action.

## Confirmed Offer Switching Acceptance Matrix

These scenarios are accepted as the first official test-writing target for Bot/WebApp coexistence.
They intentionally avoid using persistent `User.home_server` as offer authority. In this section,
`acting_surface` is the product surface where the user action starts, and `request_home_server` is
the server handling that action. `Offer.home_server` remains the authoritative offer server.

### Scenario 1 - Bot-Authored Offer

Initial action:

- `acting_surface=telegram_bot`
- `request_home_server=foreign`
- `offer_home_server=foreign`
- the offer is created on the foreign server and appears on Telegram/Bot;
- the offer syncs to Iran and appears on the WebApp as close to real time as possible.

Owner expiry:

- if the owner expires the offer from Bot, foreign applies the mutation, updates Telegram/Bot state,
  and syncs the result to Iran;
- if the owner expires the offer from WebApp, Iran forwards the command to foreign; Iran must not
  perform an independent local-only expiry for a foreign-home offer;
- after either path, both Bot/Telegram and WebApp show the offer as expired as close to real time as
  possible.

Requests/trades from other users:

- if another user requests/trades from Bot, foreign is already the authoritative server and applies
  the command there;
- if another user requests/trades from WebApp, Iran forwards the command to foreign;
- remaining quantity, lot state, offer status, trade rows, notifications, WebApp realtime state, and
  Telegram/Bot state must converge from the foreign authoritative result.

Forbidden outcomes:

- Iran must not call Telegram directly;
- Iran must not permanently diverge by expiring or trading a foreign-home offer locally only;
- sync replay must not create duplicate trades, duplicate notifications, or duplicate Telegram posts.

### Scenario 2 - WebApp-Authored Offer

Initial action:

- `acting_surface=webapp`
- `request_home_server=iran`
- `offer_home_server=iran`
- the offer is created on the Iran server and appears on the WebApp;
- the offer syncs to foreign and only the foreign server publishes it to Telegram/Bot;
- the foreign Telegram publication result, including `channel_message_id`, syncs back to Iran.

Owner expiry:

- if the owner expires the offer from WebApp, Iran applies the mutation and syncs the result to
  foreign; foreign updates Telegram/Bot state from that authoritative result;
- if the owner expires the offer from Bot, foreign forwards the command to Iran; foreign must not
  perform an independent local-only expiry for an Iran-home offer;
- after either path, both WebApp and Bot/Telegram show the offer as expired as close to real time as
  possible.

Requests/trades from other users:

- if another user requests/trades from WebApp, Iran is already the authoritative server and applies
  the command there;
- if another user requests/trades from Bot, foreign forwards the command to Iran;
- remaining quantity, lot state, offer status, trade rows, notifications, WebApp realtime state, and
  Telegram/Bot state must converge from the Iran authoritative result.

Forbidden outcomes:

- Iran must not call Telegram directly;
- foreign must not permanently diverge by expiring or trading an Iran-home offer locally only;
- sync replay must not create duplicate trades, duplicate notifications, or duplicate Telegram posts.

### Matrix Expansion Cases For Tests

- the same owner creates a Bot offer and a WebApp offer minutes apart; each offer keeps the home
  server of its creation surface regardless of `User.home_server`;
- the owner sends expiry from Bot and WebApp at nearly the same time; the result is idempotent and
  both surfaces converge;
- two different users request/trade the same offer at nearly the same time from different surfaces;
  only the offer home server decides the accepted order and remaining quantity;
- partial fill and full fill update both surfaces consistently;
- direct push plus worker replay of the same sync event does not duplicate side effects;
- peer outage behavior is still a separate Level 4 decision: either reject, queue/pending, or allow
  local creation with explicit degraded state, but the final choice must be tested.

## Main Architecture Tensions

### 1. User `home_server` cannot be the source for offer home

The target policy says offer home is determined by write surface:

- bot write -> `offer_home_server=foreign`
- WebApp write -> `offer_home_server=iran`

Current API offer creation uses `owner_user.home_server` first. That conflicts with the new policy
because users may be active on both surfaces at the same time. A user whose session home is `foreign`
can still create a WebApp offer on Iran, and that offer must be Iran-home.

Required direction: split the concepts:

- `user.home_server` or session authority, if still needed, is about authentication/session routing.
- `offer_home_server` is about where the offer was authored and where authoritative trade mutation occurs.
- offer creation must set home from the current write surface/server, not from the user's current session home.
- Every `user.home_server` use must be classified; any use that decides `Offer.home_server` is an
  offer-authority bug and must be removed.
- WebApp and Bot offer creation must call the same shared command/service instead of duplicating
  domain writes inside routers or handlers.

### 2. IDs can collide under true two-way writes

Both servers have independent PostgreSQL sequences. Sync applies explicit integer IDs and then repairs
sequences to `MAX(id)`. That helps after a row arrives, but it does not prevent both servers from
creating row `id=123` independently before either syncs.

This is critical if "all tables sync in the moment" includes two-way writes for users, offers, trades,
notifications, settings, invitations, and admin data.

Required direction:

- Use globally safe IDs, or partition integer sequences by server, or introduce stable UUID/public IDs.
- Define natural-key merge only as a fallback, not as the main collision strategy.
- Treat trade numbers separately because they are user-facing and must remain globally unique.

### 3. Current sync is queue-accelerated, not a true committed outbox drain

`log_change()` inserts `change_log` and immediately pushes Redis/direct HTTP from inside the event
listener. That is fast, but the reliable source of truth should be committed DB state.

Current risks:

- a queue item can be emitted before the outer transaction is fully committed;
- Redis push failure leaves a `change_log` row that only manual resync/recovery will replay;
- direct push success does not mark `change_log.synced`; the queued worker still replays later.

Confirmed direction:

- Make `change_log` the durable outbox.
- Let worker drain committed `change_log WHERE synced=false`, using Redis only as a wake-up/acceleration path.
- Keep delivery idempotent because direct push and worker replay may both deliver the same logical change.

### 4. Bulk updates bypass SQLAlchemy event sync

Several important mutations use SQLAlchemy bulk `update()` and do not fire ORM model listeners:

- offer auto-expiry in `core/offer_expiry.py`;
- user counters in `core/utils.py`;
- user-delete offer/trade cleanup in `core/services/user_deletion_service.py`;
- selected admin/message service updates.

If all non-messenger tables must sync immediately, these paths need explicit outbox logging or must move
through sync-aware service helpers. This audit is now confirmed as required.

Required direction:

- Add a small sync-aware mutation helper for bulk updates.
- Add tests that each important mutation creates a `change_log` entry.
- Audit all `update(...)`, `delete(...)`, raw SQL, and direct relationship side effects.
- For every bypassing mutation, either move it to a sync-aware helper or explicitly record the
  sync/outbox event.

### 5. Messenger exclusion is not fully encoded

The target says messenger is Iran-only and not synced. Current code already excludes `messages` and
`conversations` from the sync router, but it still syncs `chats` and `chat_members`. Code review
confirmed that `chats` and `chat_members` primarily represent messenger rooms and membership state:
direct chats, groups, optional channels, last/pinned message links, read state, mute state, pin state,
and hide state. They are therefore messenger-owned.

There is one mixed use today: the mandatory system channel is stored in `chats` and `chat_members`.
That should not make the whole chat model a synced product surface. The mandatory channel can be
rebuilt locally from synced `users`, and the current sync special-cases for mandatory channel rows
should be treated as transitional compatibility, not target architecture.

Required direction:

- Exclude `messages`, `conversations`, `chat_files`, `upload_batches`, `upload_sessions`, `chats`,
  and `chat_members` from general cross-server sync.
- Make mandatory system-channel rows a local projection derived from synced `users`, or move any
  truly non-messenger membership metadata to a separate non-messenger table.
- Block user-facing `/api/chat` on foreign at the API layer and at the reverse-proxy layer so
  accidental foreign writes cannot happen. Do not leave the whole router open; allowlist only a
  future explicitly documented internal endpoint if one becomes necessary.
- Add sync tests proving messenger tables never enter `change_log` or `/api/sync/receive`.

### 6. Iran must have hard Telegram side-effect guards

Policy says Iran never connects to Telegram. This is also an operational constraint because Telegram
is filtered in Iran, so an Iran-side Telegram path is not dependable even if credentials accidentally
exist. Current shared API code has Telegram side-effect helpers such as offer channel publishing and
trade message/button updates. These helpers check for token/channel, but they do not encode "Iran
must never call Telegram" as a hard invariant.

Required direction:

- Ensure Iran env has no `BOT_TOKEN` and no Telegram outbound path.
- Add deployment/config checks that fail before staging validation if Iran has an active bot service
  or Telegram credentials, or if the foreign stack is missing the bot service.
- Add the confirmed central Telegram side-effect gateway that refuses to call Telegram when
  `server_mode=iran` and logs the blocked attempt for operations/security review.
- Route offer publication, channel message updates, expire/cancel channel updates, trade
  button/message updates, and Telegram-bound notifications through that gateway only.
- For WebApp-created offers, Iran should create the offer and sync it to foreign; foreign publishes to Telegram.
- For notifications that must reach Telegram users, Iran should relay an internal event/change to foreign, not call Telegram.

### 7. WebApp realtime after synced market changes needs explicit publish

Bot-created offers sync foreign -> Iran. Applying the row on Iran is not enough for "appears immediately"
unless the Iran WebApp gets a realtime event or the frontend is polling aggressively.

Current `is_sync=True` application suppresses normal SQLAlchemy event listeners. That avoids echo loops,
but it also means synced offers may not publish the same local realtime events as native writes.

Required direction:

- After `/api/sync/receive` applies market-impacting offer/trade/expiry changes on Iran, publish a
  local WebApp market event after the DB commit.
- Keep this event local to the Iran WebApp runtime. It must not write `change_log`, enqueue outbound
  sync, call Telegram, or run on the foreign WebApp surface.
- Add e2e or service tests for bot-offer -> Iran DB -> WebApp event and for synced trade/expiry
  updates reaching WebApp without sync echo.

### 8. Telegram publish after WebApp-created offer needs an idempotent foreign side effect

WebApp-created offers sync Iran -> foreign, and foreign should publish them to Telegram. Current sync receive
already tries to publish new synced offers on non-Iran servers and skips already-published offers by checking
`channel_message_id`.

Confirmed concerns:

- `channel_message_id` is intentionally not overwritten from sync, so the authoritative channel message id lives on foreign.
- If the foreign publish succeeds but the subsequent DB update or reverse sync fails, Iran may not learn the channel message id.
- Duplicate delivery from direct push and worker replay must never duplicate Telegram channel posts.

Confirmed direction:

- Keep publish side effect only on foreign.
- Use a separate idempotency marker for Telegram publication, not only `channel_message_id`.
- Sync foreign's channel publish result back to Iran without letting Iran call Telegram.

### 9. Concurrent activity needs per-table conflict policy

Users can act in WebApp and bot at the same time. Simple last-write-wins upsert is not enough for:

- user profile/session fields;
- offer remaining quantities and status;
- counters and limits;
- notifications and unread counters;
- admin settings;
- trade creation and trade numbers.

Required direction:

- For each synced table, define owner, conflict key, merge rule, and allowed write surfaces.
- Use optimistic version checks where a table is updated on both sides.
- Treat offer/trade mutation as command forwarding to the offer home server, not independent mutation on both servers.

### 10. Session model conflicts with simultaneous WebApp and bot activity

The current session architecture has a single `user.home_server` concept and previously treated some session
state as local to the home server. The new policy says users can be active in both WebApp and bot at the same
time, but runtime/auth state is not the same as product data.

Confirmed policy:

- WebApp sessions stay local to Iran.
- Bot FSM state stays local to foreign.
- `user_sessions`, `session_login_requests`, and recovery request rows should not be part of general
  cross-server sync.
- Existing signed session-authority checks may still query the authoritative server when needed.
- `telegram_id`, user profile fields, account status, role, and limits are user/account data and
  should sync.

Required direction:

- Separate "surface session" from "data authority".
- Do not let a WebApp login flip the meaning of bot offer authority, or vice versa.
- Add tests/registry entries proving runtime/auth tables are excluded unless explicitly promoted
  later.
- Keep any remaining `user.home_server` use inside the audited session/auth, runtime-surface, or
  legacy-compatibility categories; do not use it for offer authority.

## Required Sync Registry

Before broad sync changes, create a registry for every model/table. This registry is now a
confirmed requirement, not an optional design note:

| Table class | Sync policy | Write surfaces | Authority | Conflict rule | Realtime side effects |
| --- | --- | --- | --- | --- | --- |
| `offers` | sync | bot, WebApp | `offer_home_server` | command-forward for mutations | WebApp event on Iran, Telegram publish on foreign |
| `trades` | sync | bot, WebApp | offer home server | shared idempotent command; forward to `offer_home_server` when remote | notifications, offer update event |
| `users` | sync | admin/auth/bot link/WebApp | TBD | natural key + field-level merge | profile/account events |
| `messages` | no sync | WebApp only | Iran | n/a | Iran realtime only |
| `conversations` | no sync | WebApp only | Iran | n/a | Iran realtime only |
| `chats` | no sync target; transitional mandatory-only compatibility if needed | WebApp/local system | Iran/local projection | no cross-server merge | Iran realtime only |
| `chat_members` | no sync target; transitional mandatory-only compatibility if needed | WebApp/local system | Iran/local projection | no cross-server merge | Iran realtime only |
| `user_sessions` | no sync | WebApp/auth local runtime | local surface | n/a | local session events |
| `session_login_requests` | no sync | WebApp/auth local runtime | local surface | n/a | local login flow |
| `single_session_recovery_requests` | no sync | WebApp/auth local runtime | local surface | n/a | local recovery flow |

The implementation must fail tests/CI when a new model/table or migration introduces a table
without a registry entry.

## Immediate Questions For Next Round

1. Do we keep integer IDs with server-partitioned ranges, or move synced tables to UUID/public IDs?
2. What is the acceptable latency target for "in the moment": under 1s, under 3s, or eventual with visible pending state?
3. During a cross-server outage, should users still be allowed to create offers on the available local surface?
