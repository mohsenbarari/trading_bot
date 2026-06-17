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

Policy note: item 5 and item 6 create an explicit exception. "All tables" means all product
tables except the messenger-owned data set. The exact messenger-owned table list must be made
explicit before implementation.

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
  healthy. It should be retained as an acceleration path even if committed outbox draining becomes
  the reliability source.

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
- Telegram side effects are scattered across Bot handlers, API routers, and core helpers. A central
  gateway is needed so "Iran must never call Telegram" is enforced once instead of by convention.

#### Review Recommendations Accepted As Direction

- Add an explicit source-surface concept, for example `telegram_bot`, `webapp`, and `internal_sync`,
  and use it to choose offer authority and side effects.
- Extract shared offer creation and trade execution commands/services so Bot handlers become thin
  UX adapters and do not own market authority, trade-number allocation, customer-chain rules, or
  offer mutation logic.
- Move Telegram publication toward an idempotent gateway/outbox model. The exact schema is still a
  design decision, but the needed behavior is clear: foreign-only execution, dedupe key, retry state,
  durable status, and sync-back of publication result.
- Treat WebApp sessions, Bot FSM state, and Telegram runtime state as surface-scoped auth/runtime
  state unless a later decision explicitly promotes specific metadata into the product sync set.
  User profile/account status remains product data and should sync.

#### Review Points Not Adopted As Final Design Yet

- Specific folder names and table names proposed in the review, such as `telegram_outbox` or
  `mandatory_memberships`, are useful options, not final decisions.
- Blocking WebApp offer creation on foreign is correct for public WebApp/user traffic. Internal
  sync, health, and signed maintenance endpoints still need explicit allow rules.
- Excluding all session tables from sync is a strong candidate direction, but it needs a separate
  auth/session policy decision because existing session authority behavior is already cross-server
  aware in places.

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
  bypass listeners.
- Messenger exclusion is incomplete. `messages` and `conversations` are excluded from the sync
  router, but `chats` and `chat_members` are still synced and have event listeners.
- Bot-created offers will usually get `home_server=foreign` through the model default, but the
  bot handler does not set that value explicitly from the bot surface.
- WebApp-created offers are not guaranteed to get `home_server=iran`; current API creation uses
  `owner_user.home_server` first, which conflicts with the surface-based offer-home policy.
- WebApp-created offers can be published on foreign after sync receive, but the idempotency model
  is still tied mostly to `channel_message_id` and row locking.
- Bot-created offers can sync to Iran DB, but the Iran-side WebApp realtime publication after
  applying a synced offer is not explicitly encoded in the sync receive path.
- Session/auth state has cross-server logic in places, but the new policy allows simultaneous
  WebApp and bot activity. It is not yet clear whether session tables sync, stay local, or become
  a documented exception to "all non-messenger tables".
- ID collision handling is only partial. The receiver repairs sequences after applying remote
  rows and has natural-key fallbacks for some tables, but independent two-way inserts can still
  create the same integer ID before either side receives the other row.

### Not Implemented Or Not Yet Encoded

- A single registry that declares every table's sync policy, write surfaces, authority server,
  conflict rule, and side effects does not exist.
- A durable committed outbox drain is not implemented. `change_log` exists, but the worker consumes
  Redis queues; replaying committed `change_log WHERE synced=false` is a manual/resync path rather
  than the normal delivery loop.
- A server-mode Telegram gateway that hard-fails all Telegram calls on Iran does not exist.
- A server-mode WebApp gateway that hard-fails frontend service/user WebApp access on foreign does
  not exist.
- Surface-based offer-home assignment is not implemented for WebApp creation.
- Explicit bot-side offer-home assignment is not implemented.
- A complete messenger exclusion contract is not implemented. The exact excluded table list and
  tests are missing, especially around `chats` and `chat_members`.
- A complete conflict policy for concurrent two-server writes is not implemented.
- Globally safe IDs or server-partitioned sequences are not implemented.
- Sync-aware helpers for bulk updates/deletes are not implemented consistently across the codebase.
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
3. Add a small source-surface enum/constant and use it in offer creation tests.
4. Add tests that assert `messages` and `conversations` are not accepted by the sync model map.
5. Add deployment/config assertions that the Iran compose has no bot service and the foreign compose
   has the bot service.

#### Level 2 - Guardrails And Local Side Effects

1. Add a central Telegram side-effect gateway that refuses all Telegram calls when
   `server_mode=iran`.
2. Add a server-mode WebApp/static guard so the foreign API cannot accidentally serve the WebApp.
3. After synced bot-created offers are applied on Iran, publish a local WebApp realtime event without
   creating a sync echo.
4. Decide the narrow fate of `/api/chat` on foreign: block the router entirely, block at reverse proxy,
   or allow only explicitly non-messenger internal operations.
5. Move Bot cancel-all side effects after the DB commit, or route it through a shared expire-offers
   command that has explicit post-commit side effects.

#### Level 3 - Sync Coverage And Delivery Reliability

1. Extract shared offer creation command/service and make both WebApp and Bot call it.
2. Extract shared trade execution command/service and make Bot channel callbacks use the same
   authoritative/idempotent path as the API.
3. Create a sync registry for every table with sync policy, write surfaces, authority, conflict rule,
   and side effects.
4. Audit all bulk `update()`, bulk `delete()`, raw SQL, and relationship side effects; move them to
   sync-aware helpers or explicit outbox logging.
5. Replace the current "Redis queue as worker source" behavior with a committed outbox drain from
   `change_log WHERE synced=false`, while keeping Redis/direct push as wake-up/acceleration.
6. Make Telegram publication idempotency independent from only `channel_message_id`, then sync the
   foreign publication result back to Iran.
7. Add end-to-end tests for Bot offer -> Iran WebApp realtime and WebApp offer -> foreign Telegram
   publication without duplicate channel posts.

#### Level 4 - Core Distributed-System Decisions

1. Choose a globally safe ID strategy: UUID/public IDs or server-partitioned integer sequences.
2. Define per-table conflict policy for concurrent writes: owner, natural key, merge rule, version
   check, and allowed write surfaces.
3. Redesign session/auth semantics for simultaneous WebApp and bot activity without letting
   `user.home_server` control offer authority.
4. Define degraded-mode behavior for peer outage, Telegram outage, DB lag, duplicate delivery, and
   partial publish success.

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

Required direction:

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
through sync-aware service helpers.

Required direction:

- Add a small sync-aware mutation helper for bulk updates.
- Add tests that each important mutation creates a `change_log` entry.
- Audit all `update(...)`, `delete(...)`, raw SQL, and direct relationship side effects.

### 5. Messenger exclusion is not fully encoded

The target says messenger is Iran-only and not synced. Current code already excludes `messages` and
`conversations` from the sync router, but it still syncs `chats` and `chat_members`. Those tables are
used by the generic messenger foundation and by mandatory/system channel rollout behavior.

Required direction:

- Define the exact messenger exclusion set.
- Decide whether `chats`/`chat_members` are fully Iran-only or whether mandatory/system membership must move to separate non-messenger tables.
- Block `/api/chat` on foreign at the API/reverse-proxy level so accidental foreign writes cannot happen.
- Add sync tests proving messenger tables never enter `change_log` or `/api/sync/receive`.

### 6. Iran must have hard Telegram side-effect guards

Policy says Iran never connects to Telegram. Current shared API code has Telegram side-effect helpers such as
offer channel publishing and trade message/button updates. These helpers check for token/channel, but they do
not encode "Iran must never call Telegram" as a hard invariant.

Required direction:

- Ensure Iran env has no `BOT_TOKEN` and no Telegram outbound path.
- Add a central Telegram side-effect gateway that refuses to call Telegram when `server_mode=iran`.
- For WebApp-created offers, Iran should create the offer and sync it to foreign; foreign publishes to Telegram.
- For notifications that must reach Telegram users, Iran should relay an internal event/change to foreign, not call Telegram.

### 7. WebApp realtime after bot-created offer needs explicit publish

Bot-created offers sync foreign -> Iran. Applying the row on Iran is not enough for "appears immediately"
unless the Iran WebApp gets a realtime event or the frontend is polling aggressively.

Current `is_sync=True` application suppresses normal SQLAlchemy event listeners. That avoids echo loops,
but it also means synced offers may not publish the same local realtime events as native writes.

Required direction:

- After `/api/sync/receive` applies an offer on Iran, publish a local WebApp market event.
- Ensure this local realtime event does not create a new sync echo.
- Add e2e or service tests for bot-offer -> Iran DB -> WebApp event.

### 8. Telegram publish after WebApp-created offer needs an idempotent foreign side effect

WebApp-created offers sync Iran -> foreign, and foreign should publish them to Telegram. Current sync receive
already tries to publish new synced offers on non-Iran servers and skips already-published offers by checking
`channel_message_id`.

Open concerns:

- `channel_message_id` is intentionally not overwritten from sync, so the authoritative channel message id lives on foreign.
- If the foreign publish succeeds but the subsequent DB update or reverse sync fails, Iran may not learn the channel message id.
- Duplicate delivery from direct push and worker replay must never duplicate Telegram channel posts.

Required direction:

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
time and all non-messenger tables should sync.

Open questions:

- Should `user_sessions`, `session_login_requests`, and recovery tables sync?
- If they sync, how do max-session and active-session checks distinguish WebApp vs bot sessions?
- If they do not sync, they become an explicit exception to item 5 and must be documented.

Required direction:

- Separate "surface session" from "data authority".
- Do not let a WebApp login flip the meaning of bot offer authority, or vice versa.

## Proposed Sync Registry

Before changing code, create a registry for every model/table:

| Table class | Sync policy | Write surfaces | Authority | Conflict rule | Realtime side effects |
| --- | --- | --- | --- | --- | --- |
| `offers` | sync | bot, WebApp | `offer_home_server` | command-forward for mutations | WebApp event on Iran, Telegram publish on foreign |
| `trades` | sync | bot, WebApp | offer home server | idempotent command | notifications, offer update event |
| `users` | sync | admin/auth/bot link/WebApp | TBD | natural key + field-level merge | profile/session events |
| `messages` | no sync | WebApp only | Iran | n/a | Iran realtime only |
| `conversations` | no sync | WebApp only | Iran | n/a | Iran realtime only |
| `chats` | TBD | WebApp/system | TBD | TBD | TBD |
| `chat_members` | TBD | WebApp/system | TBD | TBD | TBD |
| `user_sessions` | TBD | WebApp/auth | TBD | TBD | session events |

The implementation should fail CI when a new model or migration introduces a table without a registry entry.

## Immediate Questions For Next Round

1. Should `user_sessions` and login-request tables sync, or are they an explicit exception?
2. Are `chats` and `chat_members` messenger-only, or do they also carry non-messenger mandatory-channel state?
3. Do we keep integer IDs with server-partitioned ranges, or move synced tables to UUID/public IDs?
4. Should offer creation from WebApp use `current_server()` unconditionally for `offer_home_server`?
5. Should bot offer creation move through a shared service/API command instead of directly creating `Offer`?
6. What is the acceptable latency target for "in the moment": under 1s, under 3s, or eventual with visible pending state?
7. During a cross-server outage, should users still be allowed to create offers on the available local surface?
