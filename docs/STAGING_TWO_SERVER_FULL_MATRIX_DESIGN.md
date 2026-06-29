# Staging Two-Server Full Matrix Design

Date: 2026-06-29

Branch: `candidate/sync-parity-hardening`

Status: design contract only. Do not merge, release, or run production from this
document. Security hardening is still active in a parallel workflow, so this
document intentionally avoids changing existing staging scripts until that work
is settled.

## Goal

Build a complete, controlled full matrix for the Bot/WebApp release gate that
runs on real staging Iran and real staging foreign deployments. The matrix must
cover all important business states for offers, trades, customer routing,
notifications, Telegram publication, WebApp visibility, sync, and outage
recovery without running pressure/load tests.

This replaces the old "single-host staging with Iran/foreign roles in one
compose project" as the final release-gate evidence. The old compose profile can
remain useful for fast local/staging development, but the final pre-production
matrix must exercise real peer URLs, real TLS, real routing, real sync workers,
and separate staging databases.

## Non-Negotiable Boundaries

- Production must not be touched by this matrix.
- The runner must stop if either staging peer points to a production domain,
  production DB, production Redis, or production Telegram bot/channel.
- Iran staging must never call Telegram or contain Telegram credentials.
- Foreign staging must not serve the WebApp/frontend surface.
- The WebApp surface is Iran staging only.
- The Telegram bot/channel surface is foreign staging only.
- Messenger data is out of scope for cross-server sync validation.
- Every synthetic row must use one exact run prefix and must be hard-deletable
  from both staging databases.
- The matrix must be controlled: no 1000-user load, no 600 RPS pressure, no
  broad stress campaign. Use deterministic concurrency only where the business
  invariant requires it.
- Artifacts must not contain secrets, tokens, OTPs, phone numbers, raw Telegram
  tokens, full env files, or private user data.
- Git must stay on the approved candidate branch and match the deployed staging
  release SHA before execution starts.

## Required Topology

The final matrix requires two real staging deployments:

| Role | Host | Public surface | Internal role |
| --- | --- | --- | --- |
| Iran staging | Iran server | `https://staging.gold-trade.ir` | WebApp/API, Iran sync worker, Iran DB/Redis |
| Foreign staging | Foreign server | staging foreign API host, for example `https://staging.362514.ir` or a dedicated foreign staging API domain | Telegram bot, foreign API, foreign sync worker, foreign DB/Redis |

The exact foreign staging hostname can be chosen operationally, but it must be a
staging-only hostname with normal TLS verification. It must not be the
production foreign URL.

Runtime identity requirements:

- Iran staging:
  - `SERVER_MODE=iran`
  - `FRONTEND_URL=https://staging.gold-trade.ir`
  - `IRAN_SERVER_URL=https://staging.gold-trade.ir`
  - `FOREIGN_SERVER_URL=<foreign staging HTTPS URL>`
  - no `BOT_TOKEN`
  - no Telegram channel id
- Foreign staging:
  - `SERVER_MODE=foreign`
  - `FRONTEND_URL` must not point to a served foreign WebApp surface
  - `IRAN_SERVER_URL=https://staging.gold-trade.ir`
  - `FOREIGN_SERVER_URL=<foreign staging HTTPS URL>`
  - staging `BOT_TOKEN` only
  - staging `CHANNEL_ID` only

Each side must have its own Postgres and Redis. Sharing one staging DB between
Iran and foreign is not acceptable for the final evidence because it bypasses
the sync layer and can hide row-parity and receiver bugs.

## Existing Inputs To Reuse

Do not invent a parallel checklist when the repo already has code-owned
catalogs. The two-server matrix should reuse and extend:

- `docs/BOT_WEBAPP_CANDIDATE_FULL_MATRIX_DESIGN.md`
- `docs/PRODUCTION_FULL_MATRIX_SCENARIO_CATALOG.md`
- `docs/PRODUCTION_FULL_MATRIX_MANIFEST.md`
- `scripts/report_bot_webapp_integration_matrix.py`
- `scripts/run_bot_webapp_comprehensive_load_matrix.py`
- `scripts/report_trade_notification_delivery_matrix.py`
- `scripts/run_trade_delivery_targeted_join_matrix.py`
- `scripts/build_production_full_matrix_manifest.py`
- `scripts/run_production_full_matrix.py`
- `scripts/trading_core_probe_worker.py`

The production manifest has the broadest scenario contract. For staging, reuse
its axes and assertions, but run against staging endpoints and staging cleanup.

## Matrix Profile

Use a "complete but controlled" profile:

- synthetic users: enough to cover all roles and relation states, not 1000;
- target RPS: disabled or low; no pressure target;
- deterministic concurrency:
  - wholesale hot offer: 8 to 12 parallel requests;
  - retail same-lot contention: 8 to 12 parallel requests on one lot;
  - retail mixed-lot contention: one request per lot plus several losing
    contenders;
  - manual-expire/trade race: 4 to 8 contenders;
  - time-expire/trade race: 4 to 8 contenders;
- attempts per scenario: one canonical pass plus explicit duplicate/replay
  attempts where idempotency is the assertion;
- no broad soak test;
- no capacity claim. Capacity was tested separately and is not part of this
  release gate.

## Controlled Coverage Model

"Full matrix" in this document means complete coverage of business invariants
and critical cross-products. It does not mean a raw Cartesian product of every
axis, because that would produce thousands of redundant staging mutations
without increasing confidence.

The manifest builder must therefore classify scenarios with explicit coverage
tags and fail generation when a mandatory coverage cell is empty.

Mandatory axes:

| Axis | Required values |
| --- | --- |
| Offer surface | WebApp/Iran, Telegram/foreign |
| Request surface | WebApp/Iran, Telegram/foreign |
| Authority quadrant | Iran->Iran, Iran->foreign, foreign->Iran, foreign->foreign |
| Offer direction | buy, sell |
| Quantity shape | wholesale, retail two-lot, retail three-lot |
| Terminal path | completed, partial, manual expired, time expired, market-close expired, rejected |
| Actor relation | direct user, same-owner customer route, other-owner customer route, tier1, tier2, accountant/watch/inactive/deleted negative |
| Notification route | WebApp only, Telegram only when eligible, both WebApp and Telegram, accountant WebApp only, owner/customer-chain fanout |
| Sync class | stable, short outage, medium outage, replay/recovery |

Mandatory cross-product rules:

- all four authority quadrants must run buy and sell offers;
- all four authority quadrants must run wholesale full-fill and retail
  completion;
- each retail shape must include at least one same-lot contention case and one
  losing contender case;
- every actor-pair family from the notification delivery matrix must be used at
  least once in a completed-trade scenario;
- customer privacy must be asserted in WebApp history, WebApp notification,
  Telegram private message, and Telegram channel/public state where applicable;
- tier2 customers must be tested as request-only actors and as Telegram-denied
  actors;
- accountants must be tested as WebApp notification recipients and as
  market-action denied actors;
- every rejection guard must prove zero trade creation, zero quantity mutation,
  and no non-terminal request ledger state;
- every successful trade must prove WebApp notification durability and Telegram
  delivery durability according to recipient eligibility;
- short outage must cover at least one local-authority trade, one remote-authority
  forwarded trade, one Telegram delivery delay/retry, and one market schedule
  transition;
- medium outage must cover stale delivery suppression, stale active-offer
  handling, and replay without duplicates.

The manifest should keep the stable path broad and the outage path
representative. Stable connectivity is where the full actor and trade matrix
runs. Outage scenarios should focus on the invariants that change under
disconnect/reconnect: retry, suppression, terminal-state convergence, market
schedule autonomy, and duplicate prevention.

## Minimum Release-Gate Selection

The first implementation should produce at least these logical groups:

- topology/preflight: one hard gate per side plus cleanup baseline;
- offer publication: WebApp and Telegram, buy and sell, wholesale and retail,
  alias/canonical display, invalid input, denied role;
- stable trades: all four quadrants, buy and sell, wholesale and retail
  completion;
- actor routing: every current actor-pair family from
  `report_trade_notification_delivery_matrix.py`;
- concurrency: same-lot retail contention, mixed-lot retail contention,
  duplicate replay, trade-vs-expire race;
- delivery: linked eligible, unlinked eligible, broken Telegram id,
  accountant WebApp-only, owner/customer-chain fanout, duplicate repair;
- expiry: manual, time, market-close, local-home, remote-home;
- guard negatives: blocks, inactive/deleted, market closed, limit exceeded,
  bad signature, wrong authority, stale Telegram button;
- read views: offers, offer detail, market history, expired offers, my offers,
  my trades, trades with user, market state;
- market schedule: open notice, close notice, stale suppression, foreign local
  protective side effects under short outage;
- sync/parity: before/after health, queue/backlog counts, parity snapshot,
  worker-to-remote-receiver HTTPS/TLS probe.

The runner summary must report selected scenario counts by these groups. A run
is invalid if a required group has zero executed scenarios, even when all
executed scenarios pass.

## Branch-Change Regression Overlay

The full matrix for this branch must explicitly prioritize the code changed on
`candidate/sync-parity-hardening`, not only the older Bot/WebApp integration
paths. The manifest generator must emit a `branch_change_area` tag for every
scenario that validates one of the changed subsystems below. The final summary
must include pass/fail counts per `branch_change_area`.

The run is invalid if any branch-change area below has no executed positive or
negative scenario.

### Sync Receiver, Worker, And Parity

Changed areas include `api/routers/sync.py`, `core/sync_worker.py`,
`core/sync_push.py`, `core/sync_metadata.py`, `core/sync_registry.py`,
`core/sync_field_policy.py`, `core/sync_parity.py`,
`core/sync_parity_observability.py`, `core/sync_repair.py`,
`core/sync_transport.py`, sync watermarks, repair tooling, parity status, and
sync scripts.

Required matrix coverage:

- normal apply from Iran to foreign and foreign to Iran;
- duplicate/replay apply;
- stale/out-of-order apply;
- partial batch failure and retry;
- single-item terminal policy rejection that must not block later queue items;
- invalid source authority rejection;
- bad/missing signature rejection;
- bad timestamp rejection;
- receiver parsing errors with no partial mutation;
- apply watermark advance, no advance on failed mutation, and no aggregate
  identity collision;
- parity snapshot clean, drift, incomplete/truncated, non-business local-field
  drift, and critical business drift;
- parity status endpoint fresh, stale, missing, failed, and clean;
- repair dry-run, manifest-required apply, row-count mismatch rejection, and
  before/after parity evidence;
- worker-to-remote-receiver probe through real staging HTTPS/TLS/routing, not a
  direct receiver shortcut.

### Market Schedule, Runtime State, Notices, And Foreign Autonomy

Changed areas include `core/market_schedule_loop.py`,
`core/services/market_schedule_service.py`,
`core/services/market_transition_service.py`,
`models/market_channel_notice_receipt.py`, notice retry/stale suppression, and
foreign short-outage autonomy.

Required matrix coverage:

- fresh open transition;
- fresh close transition;
- stale open notice suppressed;
- stale close notice suppressed;
- failed notice retry;
- duplicate notice replay does not send twice;
- foreign does not create authoritative `market_runtime_state`;
- foreign receives synced Iran close and expires foreign-home active offers
  exactly once;
- foreign sends/updates Telegram local side effects after the configured grace
  during short outage;
- trade request from Telegram is rejected after synced schedule close, even if
  Iran runtime state is delayed;
- reconnect after short outage does not duplicate market notices, expiry, or
  publication-state updates;
- market-open recovery does not reactivate terminal offers.

### Offer Publication, Public Id, And Commodity Canonicalization

Changed areas include `core/offer_sync_payload.py`,
`scripts/report_offer_public_id_drift.py`,
`api/routers/commodities.py`, offer publication sync policy, and public-id
drift checks.

Required matrix coverage:

- WebApp-created offer public id appears unchanged after foreign sync;
- Telegram-created offer public id appears unchanged after Iran sync;
- publication state is idempotent after duplicate sync;
- missing/drifted public id is detected by the drift report;
- commodity alias is accepted only at input time;
- canonical commodity name is used for offer display, trade creation, trade
  history, WebApp notifications, Telegram messages, and exported history;
- invalid commodity alias/name is rejected without creating an offer;
- offer warning/metadata sync fields are preserved.

### Trade Forwarding And Authority Routing

Changed areas include `core/server_routing.py`, `core/trade_forwarding.py`,
sync authority policy, and remote-authority handling.

Required matrix coverage:

- Iran-home offer requested from WebApp stays local Iran;
- Iran-home offer requested from Telegram forwards to Iran;
- foreign-home offer requested from WebApp forwards to foreign;
- foreign-home offer requested from Telegram stays local foreign;
- remote authority unavailable produces explicit no-duplicate-safe failure;
- remote authoritative success creates one trade and one terminal request state;
- forwarding preserves actor identity, customer owner route, request platform,
  idempotency key, and notification fanout;
- retry of an unknown/ambiguous remote result does not create a second trade.

### Notification Durability And Sync Side Effects

Changed areas include notification helpers, sync side-effect receipts, market
notice receipts, and committed-outbox behavior.

Required matrix coverage:

- notification rows are committed before workers process delivery;
- committed `change_log` outbox is the sync source of truth;
- WebApp notification recipient matrix covers owner, customer, linked user,
  unlinked user, active accountant, inactive accountant, and denied accountant;
- Telegram delivery covers linked eligible user, unlinked eligible user
  `not_required`, broken Telegram id skipped safely, and next-trade recovery;
- duplicate worker repair does not duplicate visible messages;
- Web Push or Telegram send failure does not roll back trade creation;
- stale opposite-server delivery is suppressed according to outage policy.

### Auth, Session, Security, And Deployment Surface

Changed areas include OTP verification throttling, dev-login staging limits,
session authority, request logging trusted-proxy behavior, CORS defaults,
secret comparison helpers, chat file authorization, production data hygiene
guard, and deployment-surface identity guard.

Required matrix coverage:

- staging dev login works only on staging and is unavailable in production-like
  mode;
- OTP verify wrong-code throttle locks after the configured threshold;
- OTP per-IP attribution uses trusted proxy configuration in staging;
- empty environment does not allow localhost CORS;
- chat file download requires object-level authorization for direct, group, and
  channel contexts;
- unauthorized chat file download is denied without leaking file existence;
- deployment-surface guard rejects production identities in staging full matrix;
- retired peer URL, mismatched URL/domain, project-root env source, production
  DB, production Redis, production bot token, and production channel id all stop
  the runner before mutation;
- production data hygiene guard remains read-only.

### Staging Topology And Runtime Config

Changed areas include staging compose/profile configuration, staging nginx,
staging env generation, staging trusted proxy CIDRs, release metadata, and sync
worker staging profiles.

Required matrix coverage:

- Iran staging `/api/config` reports Iran role and candidate release metadata;
- foreign staging `/api/config` reports foreign role and candidate release
  metadata;
- Iran staging has no Telegram token/channel in runtime config;
- foreign staging has no served WebApp surface;
- both sync workers expose matching release metadata;
- staging trusted proxy CIDRs are applied in generated runtime env;
- real staging peer URL/TLS works in both directions;
- single-host shared-DB staging is rejected as final evidence.

## Required Scenario Families

### 1. Preflight And Topology

Stop immediately if any preflight fails:

- branch and commit are recorded;
- deployed Iran staging and foreign staging release SHAs match the candidate
  SHA being tested;
- both staging `/api/config` endpoints are healthy;
- both sync workers are running;
- both sides can reach the peer URL with normal TLS verification;
- Iran staging logs/config prove no Telegram credentials and no Telegram calls;
- foreign staging logs/config prove no WebApp/frontend serving;
- staging bot is polling the staging bot only;
- staging channel id is not production;
- cleanup dry-run for the selected prefix returns zero rows on both DBs;
- sync backlog is empty or documented before the run;
- parity snapshot baseline is recorded before mutations.

### 2. Offer Creation And Publication

Cover:

- WebApp-created buy offer, sell offer;
- Telegram-created buy offer, sell offer;
- wholesale full quantity;
- retail two-lot;
- retail three-lot;
- offer with description;
- offer with commodity alias input but canonical commodity display;
- invalid commodity/quantity/price rejection;
- tier2 customer offer creation rejection;
- accountant/watch/inactive/deleted user offer creation rejection.

Assertions:

- `Offer.home_server=iran` for WebApp-created offers;
- `Offer.home_server=foreign` for Telegram-created offers;
- WebApp-created offers appear on the Telegram staging channel after sync;
- Telegram-created offers appear on the WebApp staging market after sync;
- publication state rows converge on both staging DBs;
- Telegram channel posts use staging channel only;
- no Telegram API call occurs from Iran staging.

### 3. Trade Quadrants

Every positive trade scenario must map to one of the four authority quadrants:

| Quadrant | Offer surface | Request surface | Authority |
| --- | --- | --- | --- |
| Iran to Iran | WebApp | WebApp | local Iran |
| Iran to foreign | WebApp | Telegram | foreign edge forwards to Iran |
| Foreign to Iran | Telegram | WebApp | Iran edge forwards to foreign |
| Foreign to foreign | Telegram | Telegram | local foreign |

For each quadrant cover:

- buy and sell;
- wholesale full-fill;
- retail two-lot partial and completion;
- retail three-lot multi-partial and completion;
- duplicate request/idempotency replay;
- same-lot retail contention;
- mixed-lot retail contention;
- request after completed offer;
- request after manual expiry;
- request after time expiry.

Assertions:

- exactly one authoritative mutation path;
- completed quantity never exceeds original quantity;
- `remaining_quantity` and `lot_sizes` match the completed trades;
- offer requests end in explicit terminal ledger states;
- duplicate replay creates no second trade;
- terminal states sync to both DBs;
- WebApp market/history shows original quantity and traded quantity correctly;
- Telegram buttons are removed after completed/expired state;
- completed/partial Telegram posts are edited with the accepted traded marker;
- expired Telegram posts are edited with the accepted expired marker.

### 4. Customer And Accountant Routing

Use the existing 17 actor-pair families from the delivery matrix:

- user with user;
- user with tier1/tier2 customer, same owner and other owner;
- tier1/tier2 customer with user, same owner and other owner;
- tier1/tier2 customer with tier1/tier2 customer, same owner and other owner.

Required assertions:

- all tier1 and tier2 customer trades route through the active owner;
- customers never see the real non-owner market counterparty as direct
  counterparty;
- customer-facing WebApp history and Telegram/WebApp messages show the owner
  route;
- owner receives required WebApp notification;
- active accountants of relevant owners receive WebApp notifications;
- accountants never receive Telegram messages;
- accountants cannot perform market actions;
- tier2 customers cannot use Telegram and cannot create offers;
- tier1 customers can use Telegram only if linked and allowed;
- revoked/expired/soft-deleted customer/accountant relations are ignored.

### 5. Notification And Delivery

For every completed trade, verify durable evidence, not only visible UI:

- `notifications` rows on Iran for all required WebApp recipients;
- `trade_delivery_receipts` rows for WebApp and Telegram channels;
- Telegram receipts live on foreign and are processed by foreign workers;
- WebApp delivery lives on Iran and is processed by Iran workers;
- linked eligible users receive both WebApp and Telegram delivery;
- unlinked eligible users receive WebApp only and Telegram is `not_required`;
- broken/unreachable Telegram id is skipped safely and does not leave an
  indefinite pending backlog;
- re-linked/fixed Telegram account does not reopen old skipped receipts; the
  next trade sends only the new trade message;
- duplicate worker repair does not duplicate visible notifications;
- Web Push failure does not roll back durable notification history.

### 6. Expiry And Cancel Paths

Cover:

- manual expiry from WebApp for Iran-home offer;
- manual expiry from WebApp for foreign-home offer through forwarding;
- manual expiry from Telegram for foreign-home offer;
- manual expiry contention with trade request;
- time expiry on Iran-home offer;
- time expiry on foreign-home offer;
- market-close expiry on both staging surfaces;
- user-deleted/deactivated expiry where applicable;
- cancel-all paths for local-home and remote-home active offers;
- republish path if enabled by current product scope.

Assertions:

- terminal state cannot reactivate through sync replay;
- expiry reason/source is preserved;
- channel/WebApp publication state is terminal and idempotent;
- expired offers do not accept new requests;
- stale active offers are not published as active after recovery.

### 7. Block And Access Guards

Cover:

- no block, trade allowed when all other rules allow;
- requester blocked owner;
- owner blocked requester;
- owner-to-owner block applies to customer chains;
- non-group user cannot directly block another user's customer;
- block changed during contention cannot create partial inconsistent trades;
- market closed rejection;
- inactive/deactivated/deleted users;
- trading restriction;
- daily trade/request limits;
- active commodity limit;
- stale Telegram button;
- missing public offer id;
- wrong authoritative server;
- bad internal signature;
- remote authority unavailable.

Assertions:

- rejection is explicit;
- no partial trade/quantity/publication mutation on rejection;
- offer request ledger records a terminal rejected/failed state where expected;
- both DBs converge after sync.

### 8. Read Views And History

During and after writes, verify:

- `GET /api/offers`;
- public offer detail;
- market history;
- expired offers;
- my offers;
- my trades;
- trades with user;
- market state;
- Telegram channel post state.

Assertions:

- read views are internally consistent with authoritative state;
- customer counterparty privacy is preserved;
- traded/expired tags are distinguishable and correct;
- history shows original offer quantity and traded quantity.

### 9. Market Schedule And Channel Notices

This branch added stale market notice suppression and foreign short-outage
autonomy. The full matrix must explicitly cover:

- fresh open notice on foreign staging sends once;
- fresh close notice on foreign staging sends once;
- duplicate/replay does not send a duplicate notice;
- stale open/close transition is recorded as `suppressed_stale`, not sent;
- foreign does not write authoritative `market_runtime_state`;
- foreign blocks Telegram trade requests based on synced schedule at close
  time;
- foreign runs local Telegram-side close/open side effects after configured
  grace if Iran runtime transition has not arrived;
- reconnect/replay does not duplicate expiry or notices.

### 10. Sync, Parity, And Transport

Before and after the matrix:

- capture sync health from both staging sides;
- capture Redis queue counts;
- capture unsynced `change_log` counts;
- run parity snapshot/compare Iran -> foreign and foreign -> Iran;
- verify `/api/sync/parity/status` is fresh if enabled for staging;
- verify no critical/business drift remains;
- verify worker-to-remote-receiver probe through real staging HTTPS/TLS once
  the transport probe is implemented.

## Outage Matrix

The staging matrix must cover stable, short, and medium outage classes. The
outage must affect the real staging peer path, not only a mocked function.

### Stable

- normal peer connectivity;
- all remote deliveries and sync side effects must complete within the accepted
  lag window.

### Short Outage Under Two Minutes

Controlled options, in preferred order:

1. staging-only firewall rule blocking the staging peer HTTPS host/port for the
   sync/app containers;
2. stopping only staging sync workers on the affected side while app/API stays
   up, if firewall isolation is too risky;
3. temporary staging peer URL blackhole in container env only if the deployment
   tool can restore it deterministically.

Required behavior:

- local authoritative operations continue;
- opposite-server delivery waits/retries;
- after recovery, remote WebApp/Telegram deliveries are sent;
- no stale skip for short outage;
- final parity is clean.

### Medium Outage Around One Hour

The matrix should not actually wait an hour unless needed. Use a controlled
time-compression mechanism only if the code supports it safely in staging, or a
documented staging-only clock/threshold override. The assertion is policy-level:

- old opposite-server user-facing delivery is skipped/suppressed when outside
  the approved freshness window;
- local authoritative rows remain valid;
- stale active offers are expired or safely hidden after recovery;
- no duplicate trades or notifications after replay;
- parity and cleanup converge.

## Runner Architecture

Recommended implementation after security hardening settles:

1. Add a new staging manifest builder, or extend the production manifest with
   `--environment staging-two-server`.
2. Add a two-server staging runner that consumes the manifest and has explicit
   drivers for:
   - Iran WebApp HTTP actions against `https://staging.gold-trade.ir`;
   - foreign Telegram action simulation through aiogram Dispatcher/fake Bot API
     on the foreign staging host;
   - foreign API health and internal Telegram publication checks;
   - sync/parity probes on both sides;
   - outage control and recovery;
   - prefix-scoped cleanup on both DBs.
3. Keep high-volume Telegram tests fake/in-process, but require a small manual
   real Telegram pass after automated matrix completion.
4. Store every scenario result by `manifest_id` in JSONL so interrupted runs can
   resume and failed scenarios can be inspected without rerunning the whole
   matrix.
5. The runner must stop on the first business assertion failure by default.
   Continue-on-failure is allowed only for evidence gathering after a failure is
   already accepted as non-release-blocking.

## Artifact Contract

Each run writes one directory under `tmp/` or an operator-provided artifact
path:

- `run-metadata.json`
- `manifest.json`
- `preflight.json`
- `scenario-results.jsonl`
- `summary.json`
- `sync-health-before.json`
- `sync-health-after.json`
- `parity-before.json`
- `parity-after.json`
- `cleanup-dry-run-before-iran.json`
- `cleanup-dry-run-before-foreign.json`
- `cleanup-dry-run-after-iran.json`
- `cleanup-dry-run-after-foreign.json`
- sanitized app/bot/sync logs for the run window

For agent review, every real run must also publish detailed sanitized log
bundles under both of these paths:

- `tmp/claude/full_matrix_logs/<run-id>/`
- `tmp/chatgpt/full_matrix_logs/<run-id>/`

Both directories must contain equivalent evidence so either external reviewer
can analyze the run without asking for extra context. The Claude directory is
optimized for local server inspection. The ChatGPT directory is optimized for
upload/export and must avoid symlinks or host-local absolute dependencies.

Each reviewer log directory must include at least:

- `README.md` with branch, commit, deployed Iran/foreign SHAs, run prefix,
  topology, and how to interpret pass/fail status;
- `manifest.json` and a human-readable `manifest-summary.md`;
- `scenario-results.jsonl` with one JSON object per scenario, including
  scenario id, coverage tags, branch-change area, actors, surfaces, expected
  authority, started/finished timestamps, result, failure reason, and artifact
  references;
- `summary.json` and `summary.md` with pass/fail/skipped counts by scenario
  group, authority quadrant, actor relation, outage class, and
  `branch_change_area`;
- `preflight.json` and `preflight.md`;
- `sync-health-before.json`, `sync-health-after.json`, and queue/backlog
  snapshots for both staging peers;
- `parity-before.json`, `parity-after.json`, and comparison summaries;
- `cleanup-dry-run-before-iran.json`,
  `cleanup-dry-run-before-foreign.json`,
  `cleanup-dry-run-after-iran.json`, and
  `cleanup-dry-run-after-foreign.json`;
- sanitized `app.log`, `sync-worker.log`, `bot.log`, `nginx.log`, and
  database error snippets for the run window, split by `iran/` and `foreign/`
  where applicable;
- outage control logs showing when the staging link was blocked/restored;
- Telegram publication/message evidence with tokens, chat ids, phone numbers,
  and private user identifiers redacted;
- WebApp notification evidence with private user identifiers redacted;
- `redaction-report.json` listing the redaction patterns applied and whether
  any forbidden secret-like value was detected.

The runner must write enough structured detail for an agent to answer:

- which exact scenario failed;
- which branch-change area it covers;
- which server was authoritative;
- whether the mutation happened locally or through forwarding;
- what rows were expected and observed on each staging DB;
- what notification and Telegram deliveries were expected and observed;
- whether the final state converged after sync/recovery;
- whether cleanup removed all prefixed test data on both sides.

The summary must include:

- total selected scenarios;
- passed/failed/skipped counts;
- unsupported-policy negative count;
- offer/trade/notification/receipt counts;
- average and max stable sync lag;
- stale/suppressed notice counts;
- cleanup planned counts before and after hard delete;
- final release gate status.

Release gate status can be `passed` only when:

- all selected required scenarios pass;
- cleanup after hard delete reports zero prefixed rows on both DBs;
- no production identity appears in artifacts;
- final staging sync/parity evidence is clean or every non-business drift is
  documented;
- manual real-Telegram smoke evidence is attached or explicitly deferred by the
  owner.

## Cleanup Contract

Cleanup must be implemented before running mutating staging scenarios.

Rules:

- exact prefix only;
- dry-run before mutation;
- hard delete only after dry-run artifact is stored;
- run on both staging DBs;
- include users, sessions, offers, offer requests, trades, notifications,
  delivery receipts, publication states, relations, blocks, Telegram link
  tokens, and related Redis keys;
- cleanup must not rely on sync to clean the opposite side;
- final dry-run must report zero prefixed rows on both sides.

## Manual Real-Telegram Smoke After Automation

Automated high-volume Telegram paths may use aiogram Dispatcher/fake Bot API to
avoid thousands of real accounts. Before production release, run a small manual
real Telegram staging smoke:

- one WebApp-created offer appears on staging Telegram channel;
- one Telegram-created offer appears on staging WebApp;
- WebApp request on Telegram-created offer completes;
- Telegram request on WebApp-created offer completes;
- completed marker and button removal appear on channel;
- expired marker and button removal appear on channel;
- both sides receive expected private/WebApp trade messages for linked users.

This smoke is not a replacement for the automated matrix. It proves only the
real Bot API/channel boundary.

## Implementation Phases

### Phase A - Readiness Design

- Finalize this document.
- Wait for parallel security hardening to finish.
- Rebase/merge only after Git is stable and branch conflicts are checked.

### Phase B - Staging Topology

- Bring up Iran staging on `staging.gold-trade.ir`.
- Keep foreign staging on a staging-only foreign hostname.
- Configure real TLS and real peer URLs.
- Verify no production identity, DB, Redis, bot, or channel is referenced.

### Phase C - Manifest And Runner

- Add staging-two-server manifest mode.
- Add preflight, cleanup, and artifact writer.
- Add driver skeletons for WebApp, Telegram, sync/parity, outage, and cleanup.
- Add unit tests for manifest count and driver coverage.

### Phase D - Deterministic Execution

- Run no-pressure full matrix.
- Fix failures.
- Repeat until all required scenarios pass.
- Store artifacts for external-agent review.

### Phase E - Manual Smoke And Release Decision

- Run small real Telegram staging smoke.
- Review artifacts and logs.
- Only then consider merge to `main` and production release.

## Open Questions Before Coding

- What exact foreign staging hostname will be used for the foreign API peer?
- Will the staging bot/channel remain the current staging credentials or be
  rotated before final evidence?
- Should outage control use firewall rules or sync-worker pause for the first
  implementation?
- Is a staging-only freshness threshold override acceptable for medium-outage
  time compression, or should the medium case be validated with real elapsed
  time once?
- Which artifact directory should be retained for release evidence and external
  agent review?
