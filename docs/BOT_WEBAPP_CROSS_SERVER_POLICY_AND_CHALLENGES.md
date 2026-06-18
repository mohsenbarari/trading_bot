# Bot/WebApp Cross-Server Policy And Challenges

Date: 2026-06-17

This document captures the target operating policy for the Telegram bot, Iran WebApp, and
cross-server sync. It is the working basis for the next design Q&A rounds.

Last updated: 2026-06-18

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
21. Every `user.home_server` read and write must be audited and classified as transitional
    session/auth compatibility, legacy/account-origin compatibility, or a bug. The offer-authority
    category is not allowed as final behavior and must be removed. Active login surface/runtime
    state must not remain on `user.home_server`; it must move to explicit surface-scoped session
    state.
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
29. Test coverage for this roadmap must be complete, precise, and scenario-matrix based. The test
    suite must cover all confirmed Bot/WebApp coexistence scenarios, authority forwarding paths,
    near-simultaneous actions, retry/replay/idempotency paths, realtime side effects, notification
    side effects, sync backlog state, and forbidden outcomes. Happy-path-only tests are not enough.
30. Cross-server outage behavior is mixed by authority. Local-home offer creation and mutation may
    proceed on the authoritative local server, while remote sync/publish remains pending. Remote-home
    mutations must be temporarily rejected by default; any future pending-command path must be
    explicitly non-final. The non-authoritative server must not mutate locally. After reconnect,
    remote publication must use the latest authoritative offer state, not just the original create
    event.
31. Outage recovery has three modes: short outage up to 2 minutes, medium outage over 2 minutes and
    up to 1 hour, and long outage over 1 hour. Short outages may use normal pending replay with
    latest authoritative state. For medium and long outages, cross-surface active publication must
    stay gated until full recovery/catch-up is proven complete.
32. For medium and long outage recovery, active offers that were created or remained visible only on
    their home surface before full recovery must not be published as active offers on the peer
    surface after recovery. The low-cost default is for the offer home server to expire those
    still-active local-only offers during recovery finalization, then sync the expired/final state
    to the peer.
33. Synced tables must move to a hybrid identity strategy. A stable `public_id`/UUID is the
    canonical cross-server identity for synced records and command forwarding. Existing integer IDs
    may remain as local/internal database keys, but PostgreSQL sequences for synced tables must also
    be server-partitioned during migration to reduce collision risk until all sync/command paths use
    the public identity.
34. Every synced table must have an explicit conflict policy. Atomic database transactions are
    required for each authoritative mutation on its home/authority server, but a local PostgreSQL
    transaction does not solve distributed conflicts between two independent servers. Cross-server
    two-phase commit or distributed transactions must not be the normal design. The target model is:
    route or forward writes to the authoritative server, execute the command there atomically with
    locks/idempotency/version checks where needed, then replicate the committed result through the
    durable outbox/change-log path.
35. In healthy connected mode, offer DB commit and durable outbox/change-log recording must happen
    immediately on the `offer_home_server`; sync delivery must start immediately. User-facing
    publication on the home surface may be delayed by about 1 second to improve cross-surface
    simultaneity. The normal healthy target is for the peer surface to receive and publish/apply the
    offer/update in about 2 seconds. Passing the 2 second target marks the item as lagged or
    sync-pending for observability; it does not by itself forbid later peer publication. Late peer
    publication during a short outage is allowed only after checking the latest authoritative state.
    Medium/long outage recovery still follows the confirmed rule: do not active-publish old
    local-only offers after catch-up; expire them on their home server and sync final state.
36. `user.home_server` must not represent the user's currently active surface. WebApp login,
    Telegram bot activity, logout, session reset, and recovery flows must not flip a persistent user
    field between Iran and foreign. Runtime/session authority must be explicit and surface-scoped:
    `webapp` sessions live on Iran, Telegram Bot/FSM runtime lives on foreign, and operations such
    as reset/logout/check must target `webapp`, `telegram_bot`, or `all_surfaces` deliberately.
37. Migration and cutover to the new Bot/WebApp architecture must be conservative, non-destructive,
    and data-protective. Historical data must be preserved unless the owner explicitly approves a
    destructive cleanup. The owner guarantees that no active offers will exist during migration and
    switch to the new architecture; the rollout must still verify `active offers = 0` on both
    servers before cutover and abort if any active offer is found. Existing `channel_message_id`
    values remain foreign-owned, existing sessions remain local runtime state, and partially synced
    rows or sync backlog must be reconciled to a known safe state before enabling live two-surface
    behavior.
38. Rollback planning is mandatory even though the target is to avoid rollback through complete
    staging validation. Staging tests must be precise, scenario-matrix based, and broad enough to
    cover every confirmed Bot/WebApp coexistence path before any production consideration. The
    second validation stage is owner-led manual testing, with the assistant supporting log reading,
    sync-health inspection, and correctness checks. If rollback is ever required, the default
    rollback must fail closed or disable the new behavior/feature flags first while preserving data;
    synced or migrated data must not be deleted unless the owner explicitly approves that exact
    cleanup.
39. Production action has its own final gate and exact approval phrase. No deploy, benchmark,
    production sync action, production Telegram action, production WebApp behavior change, or
    production data operation may run from this roadmap until implementation is complete, automated
    staging scenario tests pass, owner-led manual staging validation passes, final production
    acceptance gates are checked, and the owner then explicitly requests the action with the exact
    phrase `production deploy`. Mentioning `production deploy` while defining this policy or before
    the gates pass is not deployment approval; it is only the recorded approval wording for the
    future.
40. Sync receive must fail closed for unknown, unregistered, or policy-forbidden tables. A peer must
    never return success for a sync item that it did not apply or explicitly reject as an error. The
    source server must not mark a `change_log` row synced when the peer skipped the item because the
    table was unknown, unsupported, or forbidden by the sync registry.
41. Sync/outbox recording for tables whose policy is `sync` must be mandatory, not best-effort.
    ORM listener errors, JSON/payload construction errors, outbox insertion failures, and explicit
    sync-aware helper failures must either fail the authoritative business transaction or leave a
    clearly operator-visible blocked state. A synced-table business write must not silently commit
    without a durable sync/outbox record.
42. Cross-server internal transport must be production-secure. HMAC is required but not sufficient
    by itself. Production sync, seed, resync, command forwarding, and maintenance calls must use a
    verified private/TLS path or an explicitly approved secure tunnel, must not use `verify=False`
    by default, and must have replay/idempotency defenses beyond only a timestamp window where a
    replay can trigger side effects.
43. Synced payloads must have a sensitive-field policy. Fields such as mobile numbers, addresses,
    Telegram IDs, password hashes, tokens, and operational secrets must be classified as `sync`,
    `no-sync`, `hash-only`, or `encrypted/derived` before broad replication. Logs and observability
    must never expose sensitive raw payloads.
44. Runtime entrypoints must enforce the deployment surface, not just compose files. `run_bot.py`
    must fail closed unless it is running on the foreign bot surface. The API process must fail
    closed for foreign WebApp/static/chat user surfaces even if frontend files, bot code, or
    credentials accidentally exist in the image.

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
    the owner explicitly requests that production action after all final gates pass with the exact
    phrase `production deploy`.
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
- `/api/sync/receive` currently logs unknown tables and continues processing; existing tests
  currently expect a batch containing only an unknown table to return `success`.
- `core/events.py` and many individual event listeners catch and log sync/logging failures instead
  of making synced-table writes fail closed.
- `core/sync_push.py` creates the direct-push HTTP client with `verify=False`; manual seed/resync
  helpers also contain unverified HTTP client paths, and trade forwarding defaults TLS verification
  to off unless configured otherwise.
- The current user sync payload includes sensitive fields such as mobile number, address,
  `telegram_id`, and `admin_password_hash`.
- Current SQLAlchemy model inventory includes additional tables that are not in the starter
  registry table yet, including `push_subscriptions`, `user_notification_preferences`,
  `sync_blocks`, and `single_session_recovery_admin_targets`.
- `main.py` starts a background leader that can run `offer_expiry`, `market_schedule`,
  `session_expiry`, and `user_account_status` jobs on the API process; only
  `connectivity_monitor` is currently gated to `server_mode=iran`.
- Web Push runtime exists in the shared API/backend code. `/api/notifications/push/*` stores
  browser subscriptions, `core/web_push.py` sends market-offer and notification pushes, and these
  paths do not yet have a documented Iran-only execution gateway.
- Bot account-linking and channel-join approval currently depend on the foreign Bot DB seeing the
  relevant `users.telegram_id` row. A sync-lagged WebApp-created user can therefore be invisible to
  the Bot until user/account sync catches up.
- Telegram channel callback payloads currently embed integer offer IDs, for example
  `channel_trade:{offer_id}:{amount}`. This is a compatibility concern for the confirmed future
  `public_id`/UUID identity strategy.
- Bot admin surfaces can still mutate shared product data such as trading settings, commodities,
  commodity aliases, user status, and user limits. These admin writes need the same authority and
  conflict-policy treatment as WebApp admin writes.
- `users.avatar_file_id` references `chat_files.id`; `users` is a sync candidate while `chat_files`
  is messenger/media no-sync. Fields that reference no-sync tables need individual sync/projection
  policy.

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
- The existing deployment-surface guard is useful for preventing hardcoded production
  IP/domain drift. It should be kept, but it is not a complete Bot/WebApp surface-enforcement
  guard by itself.

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
- Existing HMAC signing for sync and internal trade forwarding is a useful baseline. It must be
  kept, but production security still needs verified transport, replay protection, key scoping, and
  sensitive-field policy.

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
- Treat `user.home_server` as an overloaded legacy/account-origin compatibility field until it is
  narrowed or removed. It must not represent the currently active surface, and login/auth flows must
  not flip it between Iran and foreign. Every read/write must be classified as transitional
  session/auth compatibility, legacy/account-origin compatibility, or a bug.

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
  These meanings conflict once a user can use WebApp and Bot at the same time. The confirmed target
  is stricter than the initial audit: `user.home_server` must not remain as active session surface.
  Transitional session/auth reads may exist only while migrating to explicit surface-scoped session
  state; offer-authority uses and login-time flips must be removed.
- ID collision handling is only partial. The receiver repairs sequences after applying remote
  rows and has natural-key fallbacks for some tables, but independent two-way inserts can still
  create the same integer ID before either side receives the other row. The confirmed target is a
  hybrid identity strategy: UUID/public IDs for cross-server identity and server-partitioned integer
  sequences as a migration guard.
- Sync failure detection is incomplete. Unknown tables can currently be skipped by the receiver
  without producing a peer error, and a source worker can then incorrectly treat delivery as
  successful if the peer returns `success`. This creates the exact risk that a table stops syncing
  and both servers fail to notice.
- Sync/outbox recording is not yet a hard invariant. Some listener and `log_change` failures are
  caught and logged, and some payload construction failures can happen before an outbox row exists.
  The final design must make synced-table outbox creation transactional and mandatory.
- Cross-server transport security is partially implemented. HMAC signing exists, but direct push,
  seed, or resync paths can use unverified TLS, and replay defense is timestamp-window based rather
  than nonce/dedupe based for every side-effecting path.
- The starter sync registry table does not yet enumerate every current model/table. Registry
  implementation must begin from the actual model inventory, not only the tables currently present
  in `/api/sync/receive`.

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
- The confirmed conflict policy for concurrent two-server writes is not implemented. Atomic
  transactions are not yet consistently enforced around authoritative multi-row commands, and the
  required per-table owner, allowed write surfaces, merge/version rule, and rejection/forwarding
  behavior are not encoded yet.
- The confirmed surface-scoped session/auth model is not implemented yet. Current auth/session code
  still has paths that treat `user.home_server` as session home or mutate it from the login/request
  server. These must move to explicit `webapp`, `telegram_bot`, or `all_surfaces` session commands
  and runtime state.
- The confirmed hybrid identity strategy is not implemented. Synced tables do not yet have
  UUID/public IDs as the canonical cross-server identity, and synced-table integer sequences are not
  yet server-partitioned as a migration guard.
- The confirmed conservative migration/cutover policy is not implemented yet. There is no automated
  preflight that verifies active offers are zero on both servers, no dry-run report for existing
  IDs/sequences/old offers/Telegram bindings/sessions/partially synced rows, and no abort gate for
  unsafe backlog or ambiguous active market state.
- The confirmed bulk/raw/relationship write audit is not implemented yet. Sync-aware helpers or
  explicit sync/outbox logging are not implemented consistently across the codebase.
- Complete scenario-matrix tests are not implemented. Missing coverage includes bot offer -> Iran
  WebApp realtime, WebApp offer -> foreign Telegram publish -> result sync back, authority
  forwarding, replay/idempotency, near-simultaneous actions, forbidden outcomes, and sync backlog
  assertions.
- The confirmed owner-led staging validation phase is not implemented yet. There is no formal
  staging checklist that pairs automated scenario-matrix results with manual owner testing, log
  review, sync-health review, Telegram/WebApp publication checks, and explicit sign-off.
- The confirmed cross-server outage policy is not implemented yet. Local-home create/mutate should
  proceed with remote sync/publish pending, remote-home mutation should be rejected by default
  without local mutation, and any future pending-command path must be explicitly non-final.
  Post-reconnect publication must respect the latest authoritative offer state. Medium/long outage
  recovery must also expire active local-only offers instead of publishing them as active on the
  peer after full catch-up.
- Fail-closed sync receive for unknown, unregistered, or forbidden tables is not implemented. The
  receiver can currently return `success` after skipping unknown work, which can hide missing sync
  coverage.
- Mandatory synced-table outbox creation is not implemented. A business write can still depend on
  best-effort event listener logging instead of an explicit transactional outbox/write contract.
- Production-grade internal transport security is not implemented. Verified TLS/private tunnel
  enforcement, replay nonce/dedupe, key scoping, and sensitive-field policy are not yet encoded as
  staging/production gates.
- Runtime entrypoint surface guards are incomplete. Compose disables the Iran bot service, but
  `run_bot.py` itself does not fail closed by `server_mode`, and the shared image can still contain
  bot code, frontend assets, and API routes unless guarded at runtime.

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
7. Make `/api/sync/receive` fail closed for unknown, unregistered, or policy-forbidden tables, and
   update tests so skipped unknown work returns an error/partial result instead of `success`.
8. Add a model-inventory test that extracts every SQLAlchemy `__tablename__` and verifies each one
   is present in the sync registry as `sync`, `no-sync`, or internal bookkeeping.
9. Add a pre-implementation freshness report before code work starts: compare
   `candidate/bot-webapp-integration` with current `main`, record both commit SHAs, and decide
   whether to merge/rebase before implementation. This report is a planning gate, not permission to
   merge without owner approval.
10. Add deployment/config assertions that the Iran compose has no active bot service, the foreign
   compose has the bot service, and Iran has no Telegram credentials or outbound Telegram path. Run
   these checks before staging validation.
11. Add a `run_bot.py` runtime guard that refuses to start when `server_mode=iran` or when the
    process is not explicitly on the foreign bot surface.
12. Keep the documented manual branch-check checklist as the accepted guard so roadmap commits are not
   made accidentally from the wrong branch. No extra script, hook, or CI rule is required for now.

#### Level 2 - Guardrails And Local Side Effects

1. Add the confirmed central Telegram side-effect gateway. Every Telegram call must pass through it,
   foreign is the only allowed execution surface, and `server_mode=iran` must fail closed with an
   operational/security log.
2. Add the confirmed server-mode WebApp/static guard so the foreign API cannot accidentally serve
   frontend assets, public WebApp routes, or user-facing WebApp entrypoints. Only explicitly allowed
   internal routes such as sync, health, and maintenance may remain reachable, and staging must
   validate the guard.
3. Add production transport guardrails for internal cross-server traffic: verified TLS/private
   tunnel requirement outside isolated test/staging exceptions, no `verify=False` production path,
   source/target key scoping, replay nonce or delivery-dedupe checks, and safe failure behavior.
4. Define the sensitive-field policy for synced payloads and logs. Explicitly classify user profile,
   mobile/address, Telegram IDs, admin password hashes, tokens, Web Push subscriptions, and
   operational metadata before implementing broad registry-driven sync.
5. Add a Web Push side-effect gateway and policy. Web Push subscription storage and browser push
   execution are WebApp runtime behavior and must be Iran-only unless explicitly promoted later.
   Foreign-side business events must sync or forward durable notification intent to Iran instead of
   using Web Push directly.
6. Define a central notification side-effect policy: Telegram channel/private messages are
   foreign-only, Web Push and WebApp realtime are Iran-only, notification rows have their own sync
   policy, and preference fields must be classified before broad sync.
7. After synced market changes are applied on Iran, publish a confirmed local post-commit WebApp
   realtime event without creating a sync echo. This covers bot-authored offer creation plus
   offer/trade/expiry updates that affect WebApp market state.
8. Add the confirmed `/api/chat` foreign guard. User-facing chat requests must fail closed on
   foreign at the API layer, with reverse-proxy blocking as defense in depth. Any internal
   foreign-side chat exception must be explicitly allowlisted by path and purpose.
9. Define Bot account-linking and channel-join freshness behavior under sync lag. The Bot must have
   an explicit pending/retry/fail-visible policy when `telegram_id` or account status has not
   reached foreign yet, and join approval must fail closed for blocked/inactive users.
10. Build the confirmed shared `expire_offers` command/service foundation and route Bot cancel-all
   through it first. The service must mutate authoritative DB state first, commit, then run explicit
   post-commit side effects such as Telegram gateway updates, WebApp realtime events, cache updates,
   and notifications.
11. Implement the confirmed runtime/session state policy. WebApp sessions are Iran-local, Bot FSM is
   foreign-local, and login/recovery requests do not sync. `telegram_id`, user profile, account
   status, role, and limits sync as user/account product data.
12. Convert mandatory system-channel behavior into a local projection rebuilt from synced `users`.
   During transition, allow only a narrowly guarded `is_system=true AND is_mandatory=true`
   compatibility path if needed; do not keep wholesale `chats`/`chat_members` sync.
13. Implement the confirmed `user.home_server` audit. Classify each read/write as transitional
   session/auth compatibility, legacy/account-origin compatibility, or a bug. Offer-authority uses,
   active-surface uses, and login-time flips must be removed in this stage or explicitly isolated as
   short-lived migration compatibility.

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
5. Add a field-level policy for synced rows that reference no-sync tables. Raw FKs such as
   `users.avatar_file_id -> chat_files.id` must not cross servers unless a derived/public
   projection or explicit null/no-sync behavior exists.
6. Define admin surface authority and parity for Bot admin and WebApp admin writes. Trading
   settings, commodities, commodity aliases, user account status, user limits, and similar shared
   product/admin data must have one authority and conflict rule before both surfaces can mutate
   them freely.
7. Add a background job authority matrix for every recurring job. Each job must declare mutated
   tables, allowed server(s), authority rule, outage behavior, and sync/outbox behavior. At minimum
   cover `offer_expiry`, `market_schedule`, `session_expiry`, `user_account_status`, and
   `connectivity_monitor`.
8. Implement the confirmed audit of all bulk `update()`, bulk `delete()`, raw SQL, and relationship
   side effects. Every bypassing mutation must move to a sync-aware helper or explicitly record the
   required sync/outbox event.
9. Make synced-table outbox recording mandatory inside authoritative write paths. Listener-based
   recording may remain as a transition mechanism, but a synced-table mutation must not silently
   commit if the durable outbox/change-log record cannot be created.
10. Implement the confirmed committed outbox drain: the sync worker's durable source must be
   committed `change_log WHERE synced=false`. Keep Redis/direct push only as wake-up/acceleration,
   so missed queue/direct events cannot lose a committed sync change.
11. Implement aggregate-aware outbox ordering and stale-event rejection. Sync payloads or outbox
   rows must carry enough metadata, such as aggregate identity, authority server, event sequence or
   authoritative version, outbox id, and command/idempotency id, so old create/update events cannot
   overwrite newer authoritative terminal state.
12. Implement the confirmed Telegram publication idempotency/outbox model. It must use a dedupe key
   independent from only `channel_message_id`, prevent duplicate posts across direct push and worker
   replay, and sync the foreign publication result back to Iran.
13. Define explicit surface publication state for business-visible publication side effects. Business
   state such as `offer.status` must be separate from Telegram/WebApp publication state such as
   pending, visible/sent, failed, disabled, lagged, last attempt, and error code.
14. Implement complete end-to-end and service tests for the confirmed scenario matrix. Required
   coverage includes Bot offer -> Iran WebApp realtime, WebApp offer -> foreign Telegram publish ->
   result sync back, owner expiry from both surfaces, requests/trades from both surfaces,
   near-simultaneous actions, retry/replay/idempotency, and duplicate-side-effect prevention.
15. Add acceptance gates for every switching scenario. DB state, WebApp realtime state, Telegram
   channel/user-message state, notification state, sync backlog, command-forwarding behavior, and
   forbidden outcomes must all be asserted.

#### Level 4 - Recovery, Reconciliation, And Operations

1. Define reconciliation jobs for failed or partial flows: offer synced but Telegram publish failed,
   Telegram post exists but `channel_message_id` is missing, foreign publish result did not sync back
   to Iran, duplicate direct-push/worker delivery arrived, or a peer was down during local writes.
2. Add observability for sync lag, committed outbox backlog, retry backlog, Telegram publish pending
   and failed counts, orphan Telegram posts, stale WebApp market state, and conflict counts.
3. Add an operator runbook for staging incidents: how to inspect backlog, replay outbox rows, recover
   Telegram publication state, verify Iran never called Telegram, and verify foreign never served
   WebApp/messenger.
4. Implement the confirmed cross-server outage/degraded-mode behavior. Local-home create/mutate is
   allowed with remote sync/publish pending; remote-home mutate is temporarily rejected by default
   with no local mutation; any future pending-command path must be explicitly non-final; reads
   remain allowed with stale/pending state when detectable; post-reconnect publish/update must use
   the latest authoritative offer state. Split behavior into short outage up to 2 minutes, medium
   outage over 2 minutes and up to 1 hour, and long outage over 1 hour.
5. Implement the confirmed medium/long outage recovery finalization rule. After full catch-up is
   proven, active local-only offers created or kept visible before recovery must be expired by their
   offer home server and synced as expired/final state, not active-published to the peer surface.

#### Level 5 - Core Distributed-System Decisions

1. Implement the confirmed hybrid identity strategy. Add stable `public_id`/UUID identifiers for
   synced tables and move cross-server sync/commands to that identity, while server-partitioning
   existing integer sequences as a migration guard until integer IDs are no longer used for
   cross-server authority.
2. Define Telegram callback identity compatibility before switching command/sync identity to
   `public_id`/UUID. Current channel callbacks contain integer offer IDs, so the migration must
   decide between versioned callback payloads, short public tokens, callback mapping rows, or
   foreign-local integer resolution to canonical public IDs. Old channel messages must have an
   explicit compatibility behavior.
3. Add rolling-deployment and registry/protocol version compatibility. Sync payloads should carry
   enough version metadata, such as schema or registry version and producer identity, for peers to
   reject unsupported changes visibly instead of silently accepting partial work during staggered
   deploys.
4. Implement the confirmed per-table conflict policy for concurrent writes: owner/authority,
   allowed write surfaces, conflict key, merge or reject/forward rule, version check, and
   idempotency behavior. Authoritative commands must run in atomic DB transactions on the authority
   server with row/advisory locks where needed; non-authoritative servers must forward commands or
   apply replicated committed results, not perform competing local mutations.
5. Implement the confirmed surface-scoped session/auth model for simultaneous WebApp and bot
   activity. `user.home_server` must be narrowed to legacy/account-origin compatibility or removed
   from runtime authority. WebApp login, Bot activity, logout, reset, and recovery must not flip a
   persistent user field between Iran and foreign; each operation must target `webapp`,
   `telegram_bot`, or `all_surfaces` explicitly.
6. Implement the confirmed conservative migration and rollout policy for existing data. Preserve
   historical rows, add non-destructive schema/backfill steps first, verify active offers are zero
   on both servers before cutover, preserve foreign-owned `channel_message_id` values, keep existing
   sessions as local runtime state, reconcile partially synced rows/backlog to a known safe state,
   and abort the cutover if any data-protection preflight fails.
7. Implement the confirmed rollback criteria and rollback mechanics. The default rollback must
   disable/fail-close new Bot/WebApp integration behavior first, preserve data, keep audit trails,
   and avoid destructive cleanup unless the owner explicitly approves the exact action. Staging must
   include automated scenario-matrix coverage plus an owner-led manual validation phase where logs,
   sync health, WebApp state, Telegram state, and session-surface behavior are reviewed before
   sign-off.
8. Implement the confirmed final production acceptance gates, but do not run production promotion or
   touch production until implementation is complete, automated staging scenario tests pass,
   owner-led manual staging validation passes, the gates below are checked, and the owner then uses
   the exact approval phrase `production deploy`.

## Confirmed Offer Switching Acceptance Matrix

These scenarios are accepted as the first official test-writing target for Bot/WebApp coexistence.
They are a required scenario matrix, not a small smoke-test list. Tests must cover the happy path,
near-simultaneous actions, replay/retry behavior, forbidden outcomes, realtime effects, notification
effects, and sync backlog state with enough precision to catch cross-server divergence. They
intentionally avoid using persistent `User.home_server` as offer authority. In this section,
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

- the core two-way flows must be tested end to end: Bot-authored offer reaches Iran WebApp
  realtime, and WebApp-authored offer reaches foreign Telegram with publish result synced back;
- the same owner creates a Bot offer and a WebApp offer minutes apart; each offer keeps the home
  server of its creation surface regardless of `User.home_server`;
- the owner sends expiry from Bot and WebApp at nearly the same time; the result is idempotent and
  both surfaces converge;
- two different users request/trade the same offer at nearly the same time from different surfaces;
  only the offer home server decides the accepted order and remaining quantity;
- partial fill and full fill update both surfaces consistently;
- direct push plus worker replay of the same sync event does not duplicate side effects;
- failed/late Telegram publication must not duplicate posts after retry and must eventually sync the
  foreign publication result back to Iran;
- tests must assert DB state, WebApp realtime state, Telegram/channel state, user-visible Bot state,
  notifications, sync backlog, and absence of forbidden side effects;
- peer outage tests must cover the confirmed mixed policy: local-home create/mutate with remote
  pending state, remote-home mutation rejection by default, any explicitly non-final pending-command
  path added later, stale/pending read state, and post-reconnect publish/update based on latest
  authoritative state.
- medium/long outage tests must prove that after full catch-up, active local-only offers created or
  kept visible before recovery are expired by the offer home server and sync as expired/final state
  instead of being active-published to the peer surface.

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

Confirmed direction:

- Use stable `public_id`/UUID values as the canonical identity for synced records and command
  forwarding between Iran and foreign.
- Keep integer IDs as local/internal database keys where needed.
- Server-partition integer sequences for synced tables during migration to reduce collision risk
  while legacy integer-ID paths still exist.
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

### 9. Healthy-mode publication timing must balance simultaneity and safety

The confirmed target is not to delay the authoritative DB write. The authoritative server should
validate, commit, and record durable outbox/change-log state immediately. The small delay belongs to
user-facing publication side effects, not to data durability.

Confirmed direction:

- Start sync immediately after the authoritative write path records its outbox/change-log state.
- Delay home-surface public visibility by about 1 second when that helps Bot/WebApp publication land
  closer together.
- Treat 2 seconds as the healthy-mode target for peer receive/apply/publish. If the peer has not
  applied or published by then, the offer/update becomes lagged or sync-pending for observability.
- Do not treat the 2 second mark as a hard "never publish on peer" rule. If the peer receives the
  item after 2 seconds during a short outage or transient lag, it may still publish/apply only after
  checking the latest authoritative offer state.
- If the latest authoritative state is no longer active because the offer was traded, expired, or
  cancelled before peer receipt, the peer must apply that final state and must not active-publish
  the old create event.
- For medium/long outage recovery, keep the previously confirmed stricter rule: active local-only
  offers from before full recovery are expired by their home server and synced as final state rather
  than newly active-published to the peer.

### 10. Concurrent activity needs per-table conflict policy

Users can act in WebApp and bot at the same time. Simple last-write-wins upsert is not enough for:

- user profile/session fields;
- offer remaining quantities and status;
- counters and limits;
- notifications and unread counters;
- admin settings;
- trade creation and trade numbers.

Confirmed direction:

- For each synced table, define owner, conflict key, merge rule, and allowed write surfaces.
- Use atomic DB transactions on the authoritative server for every multi-row command. For example,
  offer/trade execution must allocate the trade number, create the trade, update offer quantity and
  status, record outbox/change-log rows, and schedule post-commit side effects as one authoritative
  transaction.
- Use row locks, advisory locks, idempotency keys, and optimistic version checks where a table or
  command can be retried or touched from both surfaces.
- Do not rely on distributed transactions/two-phase commit between Iran and foreign as the normal
  architecture; they would make the system dependent on cross-border connectivity and contradict
  the confirmed outage/degraded-mode behavior.
- Treat offer/trade mutation as command forwarding to the offer home server, not independent mutation on both servers.

### 11. Session model conflicts with simultaneous WebApp and bot activity

The current session architecture has a single `user.home_server` concept and previously treated some session
state as local to the home server. The new policy says users can be active in both WebApp and bot at the same
time, but runtime/auth state is not the same as product data.

Confirmed policy:

- WebApp sessions stay local to Iran.
- Bot FSM state stays local to foreign.
- `user_sessions`, `session_login_requests`, and recovery request rows should not be part of general
  cross-server sync.
- Existing signed session-authority checks may still query the relevant surface while migrating, but
  the target operation must be explicit rather than inferred from `user.home_server`.
- `user.home_server` is legacy/account-origin compatibility only. It must not mean "where the user
  is currently active" and must not be updated by WebApp login or Bot activity.
- `telegram_id`, user profile fields, account status, role, and limits are user/account data and
  should sync.

Required direction:

- Separate "surface session" from "data authority".
- Do not let WebApp login, Bot activity, logout, reset, or recovery flip a persistent user field
  between Iran and foreign.
- Make session operations target `webapp`, `telegram_bot`, or `all_surfaces` explicitly. For
  example, WebApp logout closes only the Iran WebApp session unless the command explicitly asks for
  all surfaces; Bot state remains foreign-local unless explicitly revoked.
- Account/product locks such as suspend, account status, role, and limits still sync because they
  are product data and must affect both surfaces.
- Add tests/registry entries proving runtime/auth tables are excluded unless explicitly promoted
  later.
- Keep any remaining `user.home_server` use inside audited transitional compatibility only; do not
  use it for offer authority or active runtime/session authority.

### 12. Outage behavior must follow offer authority

When Iran and foreign cannot communicate, each surface may still be up. The confirmed policy is not
"allow everything locally" and not "block everything". It depends on the offer authority:

- If the current server is the `offer_home_server`, local-home create/mutate is allowed. The remote
  sync, Telegram publish, or WebApp update stays pending until connectivity returns.
- If the current server is not the `offer_home_server`, remote-home mutate must not run locally.
  The user-facing action should be temporarily rejected by default. Any future queued/pending
  command path must be explicit, visible to the user, and non-final until the command reaches the
  offer home server.
- Reads/views may remain available, but should show stale or pending state when the system can
  detect degraded connectivity.

Outage modes:

- Short outage, up to 2 minutes: use normal pending replay with latest authoritative state.
- Medium outage, over 2 minutes and up to 1 hour: keep cross-surface active publication gated until
  full catch-up is proven.
- Long outage, over 1 hour: use the same gate as medium outage, with stronger operator
  reconciliation and observability before returning to live sync.

Scenario A details for WebApp-created Iran-home offers during short outage:

- If the offer is created on Iran and fully traded before reconnect, foreign syncs the final offer
  state and trades after reconnect. It must not publish a new active Telegram post for an already
  closed offer.
- If the offer is partially traded before reconnect and remains active, foreign may publish/update
  Telegram after reconnect with the latest authoritative remaining quantity, not the original
  quantity.
- If the owner expires the offer before reconnect, or auto-expiry closes it before reconnect,
  foreign must sync the expired state and must not publish it as an active Telegram opportunity.
- If a Telegram post somehow already exists, foreign must update/disable that same post instead of
  creating a duplicate.

Medium/long outage recovery finalization:

- During the outage, Iran may publish Iran-home offers only on WebApp, and foreign may publish
  foreign-home offers only on Bot/Telegram.
- After connectivity returns, both peers continue catch-up until backlog, retries, and failed sync
  records are resolved enough to declare full recovery.
- Active local-only offers that were created or kept visible before full recovery must not become
  active offers on the peer surface.
- The offer home server expires those still-active local-only offers during recovery finalization and
  syncs the expired/final state to the peer.
- Filled, cancelled, or already expired offers only sync their final state.

Required tests:

- local-home create with remote publish pending;
- local-home trade/expiry before reconnect;
- remote-home trade/expiry rejected by default during outage;
- any later explicit pending-command path remains non-final until accepted by the offer home server;
- reconnect replay using latest authoritative state;
- no active Telegram publish for offers already fully traded or expired before reconnect.
- medium/long recovery expires active local-only offers before peer active publication;
- medium/long recovery syncs only expired/final state for those recovery-expired offers.

### 13. Migration and rollout must be conservative and data-protective

The owner confirmed that data must be handled with maximum caution. The owner also guarantees that
there will be no active offers during migration and switch to the new architecture. That guarantee
reduces the hardest market-state ambiguity, but the system must still verify it before cutover.

Confirmed policy:

- Do not rewrite or delete historical data destructively unless the owner explicitly approves that
  exact cleanup.
- Run migration as schema-first and compatibility-first: add `public_id`/UUID fields, sequence
  partitioning, indexes, and compatibility code before changing live authority behavior.
- Backfill stable identifiers and metadata in a reversible/idempotent way with before/after counts.
- Verify active offers are zero on both Iran and foreign immediately before cutover. If any active
  offer exists, abort cutover and do not auto-expire it unless the owner explicitly approves that
  operational action for that run.
- Preserve old/closed offers as historical records. If an old offer's authority is ambiguous, mark
  it for audit/reporting rather than changing historical business meaning.
- Treat existing `channel_message_id` values as foreign-owned Telegram publication state. Iran must
  not overwrite them during migration.
- Keep existing WebApp sessions, Bot FSM state, login requests, and recovery rows local/runtime.
  Do not try to cross-sync old session rows as product data.
- Reconcile sync backlog, retry queues, failed direct pushes, and partially synced rows to a known
  safe state before enabling live two-surface behavior.
- Produce a migration dry-run report for IDs/sequences, old offers, Telegram bindings, sessions,
  sync backlog, and ambiguous rows before running a real cutover.

### 14. Rollback must preserve data and staging validation must be exhaustive

The owner confirmed the rollback direction but expects the staging test plan to be strong enough
that rollback should not be needed. This means rollback remains a safety mechanism, not a substitute
for validation.

Confirmed policy:

- Automated staging tests must cover the full scenario matrix, not just happy paths. Coverage must
  include offer creation from both surfaces, trade/request execution from both surfaces, expiry from
  both surfaces, same-user simultaneous WebApp/Bot activity, near-simultaneous commands,
  retry/replay, outage modes, idempotent Telegram publication, WebApp realtime, notification side
  effects, sync backlog, forbidden outcomes, and data-protection preflights.
- Staging validation has a second manual phase led by the owner. During that phase, the assistant's
  role is to help inspect logs, sync health, DB state, Telegram publication state, WebApp realtime
  state, session-surface behavior, and any suspicious lag/backlog before owner sign-off.
- Rollback criteria must be explicit before cutover. Examples include wrong `offer_home_server`,
  duplicate Telegram posts, Iran-side Telegram attempts, foreign-side WebApp exposure, lost or
  conflicting offer/trade state, non-zero unrecoverable sync backlog, incorrect session-surface
  behavior, or failed data-protection gates.
- Default rollback means disabling or fail-closing the new behavior first: feature flags, new
  publication paths, command forwarding, or Bot/WebApp integration entrypoints. Do not delete synced
  rows or migrated fields as the default rollback.
- Any destructive cleanup after rollback requires an explicit owner request for that exact cleanup.
- Rollback must leave enough audit/log evidence to diagnose the issue and decide whether to retry
  staging after a fix.

### 15. Production requires final gates and the exact approval phrase

The owner defined `production deploy` as the exact approval wording for a future production action.
That phrase is a required final approval phrase, not an automatic deployment trigger. If it is used
while discussing policy, before implementation, before staging validation, or before the final gates
below pass, it must be recorded as policy only and production must not be touched.

Required final gates before any production action:

- Confirm the current branch and commit source. Production promotion must come only from the intended
  approved branch/merge state; accidental worktree or branch switches by other agents must be ruled
  out immediately before the action.
- Confirm the owner has explicitly approved merge/promotion order. A push to
  `candidate/bot-webapp-integration` or a staging pass does not imply permission to merge to `main`
  or deploy.
- Confirm automated staging scenario-matrix tests pass for all accepted Bot/WebApp coexistence
  scenarios: offer creation, publication, request/trade execution, expiry/cancel, retries, replay,
  outage recovery, idempotent Telegram publish, WebApp realtime, side effects, and forbidden
  outcomes.
- Confirm the owner-led manual staging validation phase is complete. Logs, sync-health, DB state,
  Telegram publication state, WebApp realtime state, and session-surface behavior must be reviewed
  before sign-off.
- Confirm data-protection preflights pass on both servers. The migration/cutover check must verify
  there are zero active offers on both servers, no unsafe sync backlog, no unresolved partial sync
  rows, and no destructive cleanup pending.
- Confirm deployment/config guards pass. Iran must have no Telegram bot runtime or Telegram outbound
  behavior. Foreign must not expose or run the WebApp. Messenger data must remain Iran/WebApp local
  and outside cross-server sync.
- Confirm sync registry coverage is complete. Every table/model must have a reviewed sync policy,
  write surfaces, authority, conflict rule, and realtime side-effect policy before production.
- Confirm the rollback/fail-closed plan is ready. New Bot/WebApp integration behavior must be
  disableable without deleting synced or migrated data, and rollback audit evidence must remain
  available.
- Confirm observability is ready for the production window: outbox/backlog lag, sync health,
  rejected remote-home mutations, duplicate publication prevention, Telegram publication state,
  WebApp realtime delivery, and error queues must be visible enough to inspect during rollout.
- Confirm backups or snapshots are current and restorable for the production data involved in the
  cutover.

Only after all gates above pass may the owner use the exact phrase `production deploy` to authorize
the specific production action. The authorization must be interpreted narrowly for that action only;
it does not authorize unrelated production changes, destructive cleanup, or future deployments.

### 16. Second-pass audit findings before implementation

This section records additional risks found during a deeper pass over the current code on
2026-06-18. These are not a replacement for the earlier roadmap. They are the extra constraints that
should be handled before or during the early implementation levels so the later architecture is not
built on weak assumptions.

#### 16.1 Unknown sync tables can be silently accepted today

Current behavior:

- `_parse_item()` returns `None` for an unknown table.
- `receive_sync_data()` logs `sync.unknown_table` and continues.
- Existing tests currently expect a batch containing an unknown table to return
  `{"status": "success", "processed": 0}`.

Risk:

- If a new model/table starts writing `change_log` entries but the peer's sync receiver does not
  know the table, the peer can skip the item and still return success.
- The source sync worker can then mark the local `change_log` row as synced even though the peer did
  not apply it.
- This is the exact class of failure where part of the data stops syncing and both servers may not
  notice quickly enough.

Required direction:

- Unknown, unregistered, or registry-forbidden tables must return an error/partial result.
- The source must only mark a change as synced after the peer applied it or after an explicit,
  operator-visible terminal rejection policy.
- Tests must change from "unknown table is success" to "unknown table fails closed".

#### 16.2 Sync/outbox creation is still partly best-effort

Current behavior:

- `log_change()` catches broad exceptions.
- Many model event listeners catch their own errors and only log.
- Redis/direct push failures are intentionally non-fatal, which is acceptable only if the committed
  outbox row is guaranteed to exist.
- Some failures can happen before a durable outbox/change-log row is created.

Risk:

- A business write on a synced table can become durable without an equally durable replication
  record if payload construction or listener logic fails before outbox insertion.
- Later health checks may show no queue backlog for that missing event because there is no event to
  replay.

Required direction:

- For tables with sync policy `sync`, authoritative write services must create the durable outbox
  record transactionally and treat outbox creation failure as a failed business write.
- Best-effort listener logging can exist only as a transition layer, not as the final reliability
  contract.
- Observability must distinguish "peer lag" from "local write blocked because outbox cannot be
  recorded".

#### 16.3 Internal transport security needs a production-grade policy

Current behavior:

- Sync and internal command forwarding use HMAC signatures and timestamp checks.
- Direct sync push currently builds an HTTP client with TLS verification disabled.
- Seed/resync helper paths also contain unverified HTTP client usage.
- Trade forwarding can verify TLS, but the default setting is currently false unless configured.

Risk:

- HMAC protects integrity if the secret stays private, but it does not replace a verified transport
  path for sensitive business and user data.
- A captured signed request may be replayable inside the timestamp window unless every path also has
  nonce/dedupe/idempotency protection for the specific side effect.
- Sensitive fields such as mobile numbers, addresses, Telegram IDs, password hashes, and tokens need
  a conscious sync/logging policy before broad replication.

Required direction:

- Production must use verified TLS, a private network tunnel, mTLS, or another explicitly approved
  secure transport. `verify=False` must be test/staging-only and must fail production gates.
- Cross-server signed requests need replay protection tied to outbox id, nonce, sequence, or
  idempotency marker, not only wall-clock timestamp.
- Internal keys should be scoped by purpose where practical: sync apply, command forwarding,
  observability, and maintenance should not all rely on one broad credential forever.
- Sensitive fields must be classified in the sync registry and excluded, hashed, encrypted, or
  synced only when there is a clear product need.

#### 16.4 The registry must cover the actual model inventory

Current behavior:

- The starter registry table in this document covers the most important product and runtime tables.
- The real model inventory also contains tables such as `push_subscriptions`,
  `user_notification_preferences`, `sync_blocks`, `single_session_recovery_admin_targets`,
  `change_log`, and other bookkeeping/runtime tables.

Risk:

- A human-written registry can drift from the codebase.
- A migration can add a table that neither syncs nor explicitly opts out, and nobody notices until
  runtime.

Required direction:

- The registry implementation must scan or otherwise enumerate all SQLAlchemy model tables and all
  Alembic-created tables.
- Every table must be classified as `sync`, `no-sync`, or `internal-bookkeeping`, with write
  surfaces, authority, conflict rule, and side-effect policy where applicable.
- CI/tests must fail when the model/migration inventory and registry diverge.

#### 16.5 Surface enforcement must be runtime-enforced, not only deployment-intended

Current behavior:

- Iran compose does not start the bot service.
- Foreign compose starts the bot service.
- The shared images contain both API and bot code, and both Dockerfiles copy frontend assets.
- `run_bot.py` currently returns only when `BOT_TOKEN` is missing, not because `server_mode=iran`.
- `main.py` serves frontend files when `mini_app_dist` exists.

Risk:

- A misconfigured process, manual command, wrong env file, or future compose edit can violate the
  non-negotiable split even if the intended compose file is correct.
- Foreign can accidentally expose WebApp/static behavior if frontend files are present and routing
  is not guarded.
- Iran can accidentally start bot code if credentials appear or a command is run manually.

Required direction:

- `run_bot.py` must fail closed outside the foreign bot surface.
- API startup or route handling must enforce the foreign WebApp/static/chat guard.
- Deployment checks remain useful, but runtime checks are the final safety net.

#### 16.6 Side-effect durability needs explicit classification

Current behavior:

- Some side effects are safe best-effort UX updates, such as clearing a local private suggestion
  keyboard.
- Some side effects are business-visible and must be durable/idempotent, such as Telegram channel
  offer publication, channel button state, WebApp market realtime, notifications, and sync-back of
  foreign publication result.
- Current code mixes these categories inside routers, handlers, background tasks, and event
  listeners.

Risk:

- Retrying a business-visible side effect can duplicate Telegram posts or notifications.
- Losing a business-visible side effect can make a correct DB state invisible or misleading on one
  surface.
- Treating all side effects as equally best-effort hides which failures require repair.

Required direction:

- Each shared command must declare its post-commit side effects as durable/idempotent or best-effort.
- Durable/idempotent side effects need their own outbox/dedupe/retry state.
- Best-effort side effects must be safe to lose and must not be used as proof that the business
  command succeeded.

### 17. Third-pass external review decisions before implementation

This section records the decisions from the review in `tmp/bot-webapp-integration.md`, checked
against the current code and the already-updated roadmap on 2026-06-18. The review was useful, but
not every recommendation needed to become a new requirement because several points were already
covered in sections 10-16 and in the Level roadmap above.

#### 17.1 Accepted as already covered

The following review points are accepted as correct, but they do not need a new standalone roadmap
section because they are already encoded:

- Offer authority must come from `source_surface`, not `user.home_server`. This is covered by
  Non-Negotiable Policy items 12-13 and Level 1.
- Bot and WebApp must become surface adapters over shared offer/trade/expire commands. This is
  covered by Policy items 22-24 and Levels 2-3.
- Messenger-owned data, including `chats` and `chat_members`, must not remain in wholesale sync.
  This is covered by Policy item 14, Level 1 messenger sync tests, Level 2 mandatory-channel
  projection work, and the registry table.
- Durable committed outbox, fail-closed unknown-table sync, internal transport hardening,
  sensitive-field policy, migration/cutover, rollback, and production gates are already covered by
  Policy items 27, 37-43 and section 16.
- The current sync worker/receiver still need idempotency, publication dedupe, and result sync-back
  hardening. This remains covered by Level 3 and Level 4.

#### 17.2 Accepted as new or under-specified work

The following review points are accepted and have been added to the roadmap levels above:

- Background Job Authority Matrix: `main.py` can run `offer_expiry`, `market_schedule`,
  `session_expiry`, and `user_account_status` on the API background leader. Each job must declare
  authority, allowed server(s), outage behavior, and sync/outbox behavior so background mutations do
  not bypass shared commands.
- Web Push side-effect gateway: Web Push is WebApp/browser runtime behavior. Subscription storage,
  preference reads/writes, market-offer Web Push, and notification Web Push should be Iran-only
  unless explicitly promoted later. Foreign should sync durable notification intent or product data,
  not execute browser Web Push directly.
- Central Notification Side-Effect Policy: Telegram channel/private messages, Web Push, WebApp
  realtime, notification rows, and notification preferences must be classified together. This
  prevents Telegram from being over-specified while Web Push and notification duplicates remain
  ambiguous.
- Bot account-linking and channel-join freshness: the Bot currently decides join approval from the
  local foreign `users.telegram_id` row. A WebApp-created or recently linked user can be invisible
  during sync lag, so the Bot needs explicit pending/retry/fail-visible behavior and fail-closed
  handling for inactive or blocked accounts.
- Telegram callback identity compatibility: current callback data carries integer offer IDs. Before
  cross-server commands move to `public_id`/UUID authority, callback payload versioning, short public
  tokens, mapping rows, or foreign-local integer resolution must be chosen, including old message
  compatibility.
- Surface publication state: business state such as `offer.status` is not enough to audit
  publication. Telegram/WebApp publication needs explicit pending/sent or visible/failed/disabled
  state, lag markers, last attempt, and error code where the side effect is business-visible.
- Outbox causal ordering and stale-event rejection: the roadmap already requires latest
  authoritative state, but implementation also needs aggregate-aware metadata so older create/update
  deliveries cannot overwrite a newer completed/expired state.
- Admin surface authority and parity: Bot admin and WebApp admin can both mutate shared product
  configuration such as settings, commodities, user status, and user limits. These writes need
  authority and conflict rules before both surfaces remain enabled.
- Synced rows referencing no-sync tables: fields such as `users.avatar_file_id`, which references
  `chat_files`, must be classified separately because `users` syncs while `chat_files` is no-sync.
- Field-level sensitive-data policy: the broad sensitive-field policy is accepted, but `users` and
  notification-related tables need field-by-field decisions before registry-driven sync.
- Rolling deployment and registry/protocol compatibility: peers need enough version metadata to
  reject unsupported registry/schema changes visibly during staggered deploys.

#### 17.3 Accepted with product or operational constraints

- Branch freshness: the review correctly noted that this branch had diverged from `main` at review
  time. Before code implementation starts, produce a freshness report and decide whether to merge or
  rebase. This does not authorize a merge/rebase by itself; owner approval is still required.
- Medium/long outage local-only active offer expiry: the safety policy remains accepted. The review
  is correct that recovery-expired offers need an explicit `expire_reason`, owner notification, and
  admin/operator report. Operator approval for large batches is a separate product/operations
  decision and is not automatically required by this roadmap yet.
- Manual branch checks: the review is correct that automated branch/scope guards would be stronger.
  For now, the owner-approved policy remains manual branch checks before edits and commits. No hook
  or CI branch gate is added unless the owner explicitly asks for that automation later.

#### 17.4 Not adopted as standalone requirements right now

- Reordering Level 1 so registry skeleton must precede every offer-home fix is not adopted. The
  explicit Bot/WebApp `home_server` assignments remain low-risk and can be tested early, while the
  registry skeleton and inventory test also stay in Level 1.
- Duplicating every feature-specific detail from other roadmap branches into this document is not
  required. Feature branches may carry their own concrete contracts, while this document keeps the
  cross-server baseline and references the generic side-effect/sync rules.

## Required Sync Registry

Before broad sync changes, create a registry for every model/table. This registry is now a
confirmed requirement, not an optional design note:

| Table class | Sync policy | Write surfaces | Authority | Conflict rule | Realtime side effects |
| --- | --- | --- | --- | --- | --- |
| `offers` | sync | bot, WebApp | `offer_home_server` | command-forward for mutations; migration cutover requires zero active offers on both servers | WebApp event on Iran, Telegram publish on foreign |
| `trades` | sync | bot, WebApp | offer home server | shared idempotent command; forward to `offer_home_server` when remote | notifications, offer update event |
| `users` | sync | admin/auth/bot link/WebApp | TBD | natural key + field-level merge | profile/account events |
| `messages` | no sync | WebApp only | Iran | n/a | Iran realtime only |
| `conversations` | no sync | WebApp only | Iran | n/a | Iran realtime only |
| `chats` | no sync target; transitional mandatory-only compatibility if needed | WebApp/local system | Iran/local projection | no cross-server merge | Iran realtime only |
| `chat_members` | no sync target; transitional mandatory-only compatibility if needed | WebApp/local system | Iran/local projection | no cross-server merge | Iran realtime only |
| `chat_files` | no sync | WebApp/messenger/upload only | Iran | n/a | Iran media/runtime only |
| `upload_batches` | no sync | WebApp/messenger/upload only | Iran | n/a | Iran upload runtime only |
| `upload_sessions` | no sync | WebApp/messenger/upload only | Iran | n/a | Iran upload runtime only |
| `push_subscriptions` | no sync by default | WebApp browser runtime | Iran | n/a | Iran Web Push runtime only |
| `user_notification_preferences` | sync candidate | WebApp/account settings | user/account authority TBD | updated_at/version merge TBD | notification routing policy |
| `user_sessions` | no sync | WebApp/auth local runtime | local surface | n/a | local session events |
| `session_login_requests` | no sync | WebApp/auth local runtime | local surface | n/a | local login flow |
| `single_session_recovery_requests` | no sync | WebApp/auth local runtime | local surface | n/a | local recovery flow |
| `single_session_recovery_admin_targets` | no sync | WebApp/auth local runtime | local surface | n/a | local recovery flow |
| `change_log` | internal bookkeeping; no cross-sync | sync worker/outbox | local server | n/a | sync observability only |
| `sync_blocks` | internal bookkeeping; no cross-sync | sync/operations | local server | n/a | sync observability only |

The implementation must fail tests/CI when a new model/table or migration introduces a table
without a registry entry. The table above is still a starter contract, not the final exhaustive
registry. The implementation must derive the final list from the actual SQLAlchemy model and
migration inventory.

## Immediate Questions For Next Round

1. Are the second-pass audit additions in section 16 and the third-pass external-review decisions
   in section 17 accepted as part of the implementation baseline? If yes, implementation can start
   from Level 1 on `candidate/bot-webapp-integration` after a fresh branch check.
