# Production Full Matrix Scenario Catalog

Date: 2026-06-24

Purpose: define the complete scenario space that must be represented before a
large production full matrix can be considered meaningful. This catalog is
derived from the current market, bot, sync, publication, customer-chain, block,
and trade-delivery code paths. It is a coverage contract, not an authorization
to run production tests by itself.

## Source Of Truth Reviewed

- `scripts/run_bot_webapp_comprehensive_load_matrix.py`
- `scripts/report_trade_notification_delivery_matrix.py`
- `scripts/run_trade_delivery_targeted_join_matrix.py`
- `scripts/report_bot_webapp_integration_matrix.py`
- `api/routers/offers.py`
- `api/routers/trades.py`
- `bot/handlers/trade_execute.py`
- `core/services/offer_creation_service.py`
- `core/services/offer_expiry_service.py`
- `core/services/trade_service.py`
- `core/services/trade_notification_audience_service.py`
- `core/services/trade_delivery_receipt_service.py`
- `core/services/telegram_offer_publication_service.py`
- `core/services/offer_publication_state_service.py`
- `core/services/customer_relation_service.py`
- `core/services/accountant_relation_service.py`
- `core/services/block_service.py`
- `core/services/bot_access_policy.py`
- `core/sync_worker.py`
- `api/routers/sync.py`
- `core/sync_registry.py`
- `core/sync_outbox_guard.py`
- `models/offer.py`
- `models/trade.py`
- `models/offer_request.py`
- `models/customer_relation.py`
- `models/accountant_relation.py`
- `models/user_block.py`
- `models/user.py`

## Non-Negotiable Boundaries

- Telegram is foreign-only. Iran must not call Telegram.
- WebApp is Iran-only. Foreign must not serve the WebApp.
- Messenger data is not part of cross-server sync.
- `Offer.home_server` is determined by the publication platform:
  WebApp-created offers are Iran-home, Telegram-created offers are
  foreign-home.
- Trade execution authority follows `offer.home_server`; an edge server must
  forward to the authoritative server instead of mutating a remote-home offer
  locally.
- Production tests must run under production isolation with a prefixed
  synthetic cohort and must leave no prefixed rows after cleanup.
- The production full matrix must not require thousands of real Telegram
  accounts. High-volume Telegram paths should use aiogram Dispatcher/fake Bot
  API transport. A small manual real-Telegram pass is separate evidence.

## Existing Executable Catalogs

| Catalog | Count | Purpose |
| --- | ---: | --- |
| Comprehensive market/load matrix | 228 | Offer creation, trading, expiry, read views, history, and contention for WebApp and Telegram origins. |
| Trade notification delivery matrix | 204 | Actor pair, surface pair, and outage-class coverage for WebApp and Telegram delivery. |
| Targeted trade-delivery join matrix | 204 total, 108 policy-supported, 96 policy-unsupported | Joins actor/surface/outage scenarios with real policy constraints. |

The 96 unsupported targeted scenarios are expected negative cases, not missing
functionality:

- 72 include a tier2 customer as offer creator. Tier2 customers cannot create
  offers.
- 36 include a tier2 customer as Telegram requester. Tier2 customers cannot use
  Telegram.
- Some scenarios have both reasons, so the unique unsupported count is 96.

## Core Surface And Authority Quadrants

Every positive trade scenario must be mapped to one of these four quadrants.

| Quadrant | Offer surface | Offer home | Request surface | Request source | Authority path |
| --- | --- | --- | --- | --- | --- |
| Iran to Iran | WebApp | Iran | WebApp | Iran | Local Iran execution. |
| Iran to foreign | WebApp | Iran | Telegram | Foreign | Foreign edge forwards to Iran authority. |
| Foreign to Iran | Telegram | Foreign | WebApp | Iran | Iran edge forwards to foreign authority. |
| Foreign to foreign | Telegram | Foreign | Telegram | Foreign | Local foreign execution. |

Required assertions for every quadrant:

- one authoritative trade mutation path only;
- one completed offer-request ledger path for winners;
- losing concurrent requests receive terminal rejected statuses;
- no double trade from duplicate retry/idempotency replay;
- WebApp notifications are created on Iran for required recipients;
- Telegram delivery receipts are created/sent on foreign for eligible linked
  users;
- offer publication state is updated on both surfaces;
- channel post buttons are removed after terminal offer states;
- completed or partially completed channel posts are edited with the traded
  marker;
- expired channel posts are edited with the expired marker.

## Offer Type And Shape Matrix

Every core quadrant must cover both offer types:

- buy offer;
- sell offer.

Every core quadrant must cover these offer shapes:

- wholesale full-fill offer: one winner consumes the whole quantity;
- retail two-lot offer: two winners consume all lots;
- retail three-lot offer: three winners consume all lots;
- retail same-lot contention: several users request the same lot concurrently
  and only one wins that lot;
- retail unavailable-lot rejection: late request receives
  `rejected_lot_unavailable` and, on Telegram, a safe suggestion path if
  applicable;
- partial completion followed by expiry: traded quantity remains visible in
  history and terminal channel/WebApp tags.

## Market Action Families

These families are already represented in the comprehensive matrix and must
remain present in the production matrix:

- `create_offer`;
- `trade_concurrent`;
- `trade_non_concurrent`;
- `manual_expire_contention`;
- `manual_expire_non_concurrent`;
- `time_expiry`;
- `after_completed_reject`;
- `after_manual_expiry_reject`;
- `after_time_expiry_reject`;
- `active_view`;
- `public_detail_view`;
- `market_history_view`.

Additional production-only or production-critical families that must be
included around those actions:

- cancel-all from WebApp for local-home and remote-home active offers;
- Telegram cancel-all for Telegram-created active offers;
- republish from WebApp, including source-offer expiry and
  `republished_offer_id`;
- market-closed auto expiry;
- user-deleted expiry;
- Telegram publication failure and retry/disabled publication state;
- manual expiry after sync delay;
- terminal-state replay after sync recovery.

## Actor Pair Matrix

The delivery matrix defines 17 actor-pair families. The production full matrix
must use the same names so artifacts can be compared across scripts.

| Pair id | Meaning | Positive execution? |
| --- | --- | --- |
| `user__user` | Standard user with standard user. | Yes |
| `user__tier1_same_owner` | User with same-owner tier1 customer. | Yes |
| `user__tier2_same_owner` | User with same-owner tier2 customer. | WebApp yes; Telegram request by tier2 no |
| `user__tier1_other_owner` | User with other-owner tier1 customer. | Yes |
| `user__tier2_other_owner` | User with other-owner tier2 customer. | WebApp yes; Telegram request by tier2 no |
| `tier1__user_same_owner` | Tier1 customer creates offer for same-owner user. | Yes |
| `tier2__user_same_owner` | Tier2 customer creates offer for same-owner user. | No, tier2 cannot create offer |
| `tier1__user_other_owner` | Tier1 customer creates offer for other user. | Yes |
| `tier2__user_other_owner` | Tier2 customer creates offer for other user. | No, tier2 cannot create offer |
| `tier1__tier1_same_owner` | Tier1 customer with same-owner tier1 customer. | Yes |
| `tier1__tier2_same_owner` | Tier1 customer with same-owner tier2 customer. | WebApp yes; Telegram request by tier2 no |
| `tier2__tier1_same_owner` | Tier2 customer creates offer for same-owner tier1 customer. | No, tier2 cannot create offer |
| `tier2__tier2_same_owner` | Tier2 customer creates offer for same-owner tier2 customer. | No, tier2 cannot create offer |
| `tier1__tier1_other_owner` | Tier1 customer with other-owner tier1 customer. | Yes |
| `tier1__tier2_other_owner` | Tier1 customer with other-owner tier2 customer. | WebApp yes; Telegram request by tier2 no |
| `tier2__tier1_other_owner` | Tier2 customer creates offer for other-owner tier1 customer. | No, tier2 cannot create offer |
| `tier2__tier2_other_owner` | Tier2 customer creates offer for other-owner tier2 customer. | No, tier2 cannot create offer |

Required customer-chain assertions:

- all tier1 and tier2 customer trades route through the active owner;
- customers never see the real non-owner market counterparty as their direct
  counterparty;
- the owner and active accountants receive WebApp notifications for customer
  trades;
- eligible linked users receive Telegram messages on foreign;
- accountants never receive Telegram messages and never access market actions;
- tier2 customers never use Telegram;
- tier1 customers may use Telegram if linked and allowed by bot access policy;
- soft-deleted, revoked, expired, or deleted customer relations are not treated
  as active trading relations.

## User State And Access Guards

The full matrix must include positive and negative cases for:

- standard active user;
- superadmin active user;
- middle manager active user where allowed by the business path;
- watch-role user blocked from market actions;
- inactive/deactivated user blocked from market actions and bot access;
- soft-deleted user blocked from market and bot access;
- user with trading restriction active;
- user over daily trade limit;
- user over daily request limit;
- user over active commodity limit;
- accountant blocked from market and Telegram;
- tier2 customer blocked from offer creation and Telegram;
- unlinked Telegram user;
- linked Telegram user;
- linked but unreachable Telegram id, which must not crash processing and must
  not leave an infinite pending message;
- user missing on receiving server during remote execution, which must reject
  safely.

## Block Matrix

Blocking must be tested before and during trade execution:

- no block, trade succeeds if all other rules allow it;
- requester blocked offer owner, trade blocked;
- offer owner blocked requester, trade blocked;
- owner-to-owner block blocks trades for their customers as well;
- non-group users cannot directly block another user's customer;
- blocking an owner effectively blocks the owner's customer chain for market
  trading;
- a block changed during high-contention requests cannot create partial
  inconsistent trades.

## Offer Request Ledger Outcomes

The production matrix must observe and count terminal ledger statuses:

- `received`;
- `authorized`;
- `completed_trade`;
- `duplicate_replay`;
- `rejected_business_rule`;
- `rejected_offer_expired`;
- `rejected_lot_unavailable`;
- `rejected_conflict`;
- `failed_internal`.

The matrix must verify that terminal ledger rows sync between Iran and foreign
and that retries do not create a second completed trade for the same
idempotency key.

## Concurrency And Load Profile

The production load profile must include:

- at least 1000 synthetic users;
- about 60 percent Telegram-side traffic and 40 percent WebApp-side traffic;
- more than 600 business requests per second during the load window;
- hot-offer contention with several dozen concurrent requests on one wholesale
  offer;
- hot-lot contention with several dozen concurrent requests on the same retail
  lot;
- mixed-lot contention with concurrent requests across all retail lots;
- non-concurrent baseline trades for latency comparison;
- duplicate idempotency-key replay under load;
- manual expiry racing with trade requests;
- time-limit expiry racing with trade requests;
- read traffic during writes: active market list, public offer detail, market
  history, my trades, my offers, and market state.

Expected invariants under load:

- completed quantity never exceeds offer quantity;
- only one wholesale winner exists for a full-fill wholesale offer;
- retail winners do not exceed available lot count;
- `remaining_quantity` and `lot_sizes` match completed trades after commit;
- no active offer remains interactive after completed, cancelled, or expired
  state;
- WebApp realtime and Telegram channel edits are eventually consistent with the
  authoritative terminal state.

## Outage Matrix

The outage classes are code-defined as:

| Outage id | Meaning | Required result |
| --- | --- | --- |
| `stable` | Normal Iran/foreign connectivity. | All required local and remote WebApp/Telegram deliveries must be sent. |
| `short_under_2m` | Short cross-server outage below two minutes. | Remote delivery may wait for sync visibility but must still be sent. |
| `medium_around_60m` | Medium outage around one hour. | Old opposite-server delivery is skipped without stale user-facing messages. |

The production matrix must simulate real two-server disconnection, not only
mocked exceptions, for:

- Iran-home offer with foreign Telegram request during outage;
- foreign-home offer with Iran WebApp request during outage;
- offer created shortly before outage and traded during outage;
- offer created during outage and reconciled after recovery;
- manual expiry during outage;
- time expiry during outage;
- sync recovery of terminal state after outage;
- publication gate preventing old active offers from being published as active
  after medium recovery;
- cleanup of local-only active test offers after recovery.

Long outages do not require stale old user-facing trade messages to be sent.
They do require safe recovery, no duplicate trades, no active stale
publication, and clear cleanup artifacts.

## Notification And Delivery Matrix

For every completed trade, delivery must be checked for these recipient groups:

- offer-side principal user;
- responder-side principal user;
- owner of a tier1 or tier2 customer on either side;
- active accountants of each relevant owner;
- eligible linked Telegram users;
- WebApp-only users;
- users with broken/unreachable Telegram ids;
- users not yet visible on the opposite server during short outage.

Channel requirements:

- WebApp notification is required for every required recipient on Iran.
- Telegram message is required on foreign only for users allowed by bot policy
  and linked to Telegram.
- Accountants are WebApp-only.
- Tier2 customers are WebApp-only.
- Telegram send errors must not crash trade processing.
- Medium/long opposite-server deliveries may be terminal skipped without
  sending stale messages.
- Receipt dedupe must prevent duplicate messages where the first send is
  confirmed.
- If a duplicate message is safer than no message after an ambiguous worker
  failure, the second message must be identifiable as a duplicate-safe resend
  according to the delivery policy.

## Publication And Terminal Offer States

Every offer created on one surface must become visible on the other surface
through the permitted server:

- WebApp-created active offer appears on WebApp immediately and on Telegram
  through foreign publication.
- Telegram-created active offer appears in the Telegram channel and syncs to
  WebApp.
- Iran must never call Telegram directly.
- Foreign must never serve WebApp frontend routes.

Terminal publication assertions:

- completed full offer removes interactive Telegram buttons and shows the
  completed marker;
- partially completed then expired offer removes buttons and preserves traded
  quantity in history/tagging;
- manually expired offer removes buttons and shows the expired marker;
- time-expired offer removes buttons and shows the expired marker;
- failed Telegram publish records `failed` or `lagged` state and does not mark
  a false `sent`;
- already-published state is idempotent and does not send duplicate channel
  posts.

## Read And History Surfaces

Read paths must be verified during and after writes:

- active market list;
- public offer detail by `offer_public_id`;
- admin public detail with offer request ledger and publication states;
- owner public detail;
- requester public detail with requester-visible ledger only;
- market history for completed offers;
- market history for partially traded expired offers;
- market history for stale active offers whose lifetime elapsed;
- expired-offers list for natural time-limit expiries;
- my offers with active, completed, cancelled, and expired filters;
- my trades;
- trades with a specific user;
- trading settings market state.

Customer visibility invariants:

- customers do not receive general market history where the code intentionally
  hides it;
- customer-facing trade detail never exposes the real non-owner counterparty;
- superadmin user-management visibility is separate from market visibility.

## Sync And Data Integrity

The matrix must verify cross-server sync for all market-related synced tables:

- `users`;
- `offers`;
- `offer_requests`;
- `trades`;
- `notifications`;
- `trade_delivery_receipts`;
- `offer_publication_states`;
- `customer_relations`;
- `accountant_relations`;
- `user_blocks`;
- supporting synced market configuration tables.

Required sync checks:

- every write path emits `change_log` through the guarded ORM path;
- sync worker catches up on both servers;
- no market table write bypasses `sync_outbox_guard`;
- HMAC-signed internal routes reject bad signature, wrong source server,
  replay, and wrong authoritative server;
- completed trade protected fields are not overwritten by stale sync;
- terminal offer status is not reverted to active by stale sync;
- active publication gate is honored during recovery.

## Negative Business-Rule Scenarios

These must be explicit test cases, not accidental failures:

- user tries to request own offer;
- request amount is invalid;
- retail request does not match an available lot;
- offer is already completed;
- offer is manually expired;
- offer is time expired;
- market is closed;
- offer owner is inactive or deleted;
- requester is inactive or deleted;
- actor is not allowed to act for effective owner;
- request is sent to non-authoritative server and forward fails safely;
- remote authoritative server returns 504 and recovery does not duplicate;
- stale button callback on Telegram after terminal state;
- public detail for missing or invalid offer id;
- cleanup dry-run finds rows outside the test prefix and stops.

## Minimum Production Scenario Count Guidance

The base cross-product for positive trade delivery is:

`4 surface pairs * 17 actor pairs * 3 outage classes = 204`

After policy filtering, the current targeted executable set is 108 positive
actor/surface/outage scenarios. The unsupported 96 are required negative
assertions and must not be silently removed from evidence.

The behavior/load catalog adds:

`2 offer types * 3 offer shapes * 38 behavior families = 228`

Combining every behavior family with every actor/outage class would be very
large and would hide failures in noise. The production executor should therefore
use layered coverage:

- full 204 actor/surface/outage catalog for delivery and policy evidence;
- all 228 behavior/load families for market mechanics and latency;
- targeted joins for high-risk intersections:
  - all four surface quadrants;
  - every executable actor pair;
  - wholesale hot-offer contention;
  - retail same-lot contention;
  - short outage;
  - medium outage;
  - customer-chain trades;
  - block-affected trades;
  - notification delivery for owners, accountants, direct users, linked
    Telegram users, and WebApp-only users.

If the owner requests a literal full Cartesian run, the executor must print the
planned count before starting and must require a second confirmation because
the matrix size grows quickly:

`4 surface pairs * 17 actor pairs * 3 outage classes * 2 offer types * 3 offer shapes = 1224`

This 1224 count is only the base trade-delivery shape. It does not include
expiry races, invalid states, duplicate replay, read views, publication
failures, or cleanup verification.

## Production Executor Gap

The current staging comprehensive runner is not enough for the requested
production full matrix because it is staging-bound and patches several
boundaries for repeatability. A production executor must be built or approved
before the large run. It must:

- consume this catalog and emit a scenario manifest before writes begin;
- use one exact prefixed cohort;
- coordinate Iran and foreign workers;
- use real production sync and routing;
- use aiogram Dispatcher/fake Telegram transport for high-volume synthetic
  Telegram actions;
- optionally reserve a small owner-driven real Telegram E2E slice;
- simulate short and medium two-server outages in a reversible way;
- collect per-scenario artifacts;
- stop immediately on safety-contract violations;
- run two-sided cleanup dry-run and hard-delete verification.

Until that executor exists, `make production-full-matrix-plan` is a safe
planning command only; it is not the production matrix runner.
