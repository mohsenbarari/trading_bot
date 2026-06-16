# Trading Production-Grade Roadmap

Status: `TG7` complete on `candidate/trading-production-grade`; `TG8` is next.

Last updated: 2026-06-16

This roadmap covers the production-grade hardening pass for the trading money
path: offers, trade execution, trade history, customer/accountant-mediated
trades, cross-server offer authority, notifications, realtime events, and the
market UI flows that create or execute trades.

The work must follow `.github/staging-instructions.md`. Production is protected:
no production deploy, production benchmark, production data mutation, or live
server tuning is allowed unless the user explicitly requests it in the same
turn.

## Current Audit Snapshot

The trading domain currently spans these high-risk surfaces:

| Surface | Main files |
|---|---|
| Offer creation/list/expire/cancel/parse | `api/routers/offers.py`, `models/offer.py`, `core/offer_expiry.py`, `core/services/trade_service.py` |
| Trade execution/history/export | `api/routers/trades.py`, `models/trade.py`, `core/services/trade_history_export_service.py` |
| Customer commission and relation-mediated trades | `api/routers/customers.py`, `core/services/customer_relation_service.py`, `models/customer_relation.py` |
| Accountant context and audiences | `api/deps.py`, `core/services/accountant_relation_service.py`, `core/services/accountant_chat_contract.py` |
| Cross-server authority | `core/trade_forwarding.py`, `core/server_routing.py`, `api/routers/sync.py` |
| Frontend market UX | `frontend/src/views/MarketView.vue`, `frontend/src/components/OffersList.vue`, `frontend/src/views/DashboardView.vue` |
| Existing focused tests | `tests/test_offers_router_*`, `tests/test_trades_router_*`, `tests/test_trade_service_*`, `frontend/e2e/market-offers.spec.ts`, `frontend/e2e/trade-history-accountant.spec.ts` |

Important current properties:

- Offer and trade models use optimistic locking through `version_id`.
- Trade execution locks the owner user row and the target offer row.
- Remote-home offers are forwarded to `/api/trades/internal/execute`.
- Customer-mediated trades can create a chain of trade rows.
- `actor_user_id` is audit/initiator metadata only and must not define trade
  history ownership.
- Accountants are blocked from market access.
- Production release readiness already has P7 trading benchmark evidence, but
  this roadmap is narrower: correctness, recovery, staging validation, and
  maintainability of the money path.

## Non-Negotiable Invariants

- No user can trade against their own effective owner offer.
- No accountant can create offers or execute trades, in frontend or backend.
- WATCH users and market-blocked/deleted/inactive users cannot trade.
- Closed-market guards must apply to offer creation and trade execution.
- Retail lots must be consumed exactly once under concurrent requests.
- Wholesale remaining quantity must never go below zero.
- Idempotency keys must never create duplicate economic trades.
- Cross-server offers must execute only on the authoritative `home_server`.
- Trade history visibility must be based on economic participants
  (`offer_user_id`, `responder_user_id`), not actor-only metadata.
- Customer Tier 2 commission must be computed from the relation state that is
  valid for that trade, not from a later edited relation value.
- Notifications, realtime events, channel buttons, and counters must not make a
  failed trade look successful.
- Staging validation must use isolated synthetic data and no production sync.

## Risks To Validate Before Code Refactor

These are not declared bugs until a focused test proves them. They are audit
targets:

- `api/routers/trades.py` is a large orchestration layer that mixes validation,
  DB mutation, response projection, notifications, Telegram, and realtime
  publishing.
- Trade number allocation uses `max(trade_number) + 1`; concurrent execution
  must be proved safe or hardened.
- Idempotency is currently checked inside the execution flow and only one chain
  leg carries the idempotency key; cross-server retry behavior must be proved.
- Side-effect failures are often tolerated after commit; this is good for user
  response latency, but observability and retry/reconciliation expectations must
  be explicit.
- Some external HTTP paths use broad exception logging or `verify=False`; these
  must be reviewed for production-grade network policy and redaction.
- Offer creation commits before channel-send/update side effects; cache/event
  consistency must be tested around partial side-effect failure.
- Customer commission reporting and trade execution must preserve historical
  price/commission semantics even when relations change later.

## Stage Plan

| Stage | Status | Scope |
|---|---|---|
| `TG0` | Complete | Branch safety, staging guardrails on this candidate branch, trading audit, and roadmap. |
| `TG1` | Complete | Build the executable trading contract matrix with focused backend tests before behavior changes. |
| `TG2` | Complete | Extract pure planning/serialization seams from trade execution without changing behavior. |
| `TG3` | Complete | Harden atomic trade execution: idempotency, trade-number allocation, lot/quantity concurrency, and rollback behavior. |
| `TG4` | Complete | Harden cross-server trade authority and failure semantics. |
| `TG5` | Complete | Harden offer lifecycle consistency: create, republish, expire, cancel, active-count cache, and channel side effects. |
| `TG6` | Complete | Harden customer/accountant economic semantics, commission snapshots, history visibility, and notification audiences. |
| `TG7` | Complete | Harden frontend market mutation UX: submit locks, idempotency keys, conflict handling, and visible recovery states. |
| `TG8` | Pending | Add trading observability/audit signals and redacted structured logs. |
| `TG9` | Pending | Run staging validation with isolated synthetic fixtures and no production sync. |
| `TG10` | Pending | Run targeted benchmark/load proof only after TG1-TG9 pass; production run requires explicit user approval. |
| `TG11` | Pending | Final production-readiness review, rollback notes, accepted-risk table, and promotion decision. |

## Stage TG0 - Audit And Roadmap

Goal:

- Move work off staging-only branches.
- Make the candidate branch compliant with staging guardrails.
- Create a precise, staged contract for production-grade trading work.

Tasks:

- Create `candidate/trading-production-grade` from `main`.
- Add `.github/staging-instructions.md` to this candidate branch because `main`
  did not yet contain the dedicated staging guardrail file.
- Audit current trading files, test coverage, and production benchmark docs.
- Record the staged roadmap in this document.

Acceptance:

- Branch name is `candidate/trading-production-grade`.
- Worktree contains the staging guardrail document.
- No runtime behavior is changed.
- `git diff --check` passes.

## Stage TG1 - Executable Trading Contract Matrix

Goal:

- Freeze expected behavior before touching the execution internals.
- Prevent regressions in the money path while later refactors happen.

Add or extend focused tests for:

- Standard user creates offer and another standard user executes it.
- Self-trade is rejected.
- WATCH, inactive, deleted, market-blocked, restricted, and closed-market users
  are rejected.
- Accountant context is denied for offer creation and trade execution.
- Tier 1 and Tier 2 customer paths produce the correct chain legs.
- Customer-mediated actor-only rows do not appear in actor-only trade history.
- Retail lots reject duplicate concurrent consumption and return suggestion
  payloads when the chosen lot is gone.
- Wholesale quantity cannot overfill or go negative.
- Same idempotency key returns the existing trade instead of creating a second
  economic trade.
- Remote-home offer execution forwards once and local non-authoritative servers
  cannot execute the trade.
- Notification audiences include expected owners/accountants/customers without
  leaking Tier 2 counterparty names where the current contract hides them.

Acceptance:

- Focused backend tests pass for offers, trades, customers, server routing, and
  forwarding.
- No production deploy.
- If any test exposes a real bug, fix that bug before moving to TG2.

Completion notes:

- Added `tests/test_trading_production_contract_matrix.py` as the executable
  TG1 contract matrix.
- The matrix maps money-path invariants to the focused backend test files that
  own them, so later refactors have an explicit regression surface.
- Added direct contract assertions for DB/model safety constraints,
  idempotent replay with no offer mutation or extra commit, and remote-home
  forwarding with delegated `actor_user_id` preserved.
- Isolated existing cross-server routing coverage from machine env aliases so
  the test proves configured authority behavior deterministically.
- No runtime behavior, production deploy, production benchmark, or production
  data mutation was performed in TG1.

## Stage TG2 - Trade Execution Service Seams

Goal:

- Reduce the large `trades.py` orchestration risk without a behavior rewrite.

Allowed changes:

- Extract pure helpers for execution plan construction, participant chain
  planning, notification payload construction, and response projection.
- Keep route signatures and response schemas unchanged.
- Keep SQLAlchemy transaction semantics unchanged unless TG1 proves a bug.

Acceptance:

- TG1 tests still pass.
- Helper-level tests cover extracted pure functions.
- Diff is behavior-neutral except for bug fixes explicitly found in TG1.

Completion notes:

- Extracted pure execution-chain planning helpers in `api/routers/trades.py`:
  `TradeExecutionNode`, `TradeExecutionPlan`, `_build_trade_execution_plan`,
  and related validation error handling.
- Extracted pure trade notification/message formatting helpers:
  `_build_trade_notification_message`, `_build_trade_message_bundle`, and
  `_recipient_is_tier2_customer`.
- Added `tests/test_trade_execution_seams.py` for direct helper-level coverage
  of standard direct trades, customer-chain planning, invalid node rejection,
  Tier 2 notification privacy, and Telegram/notification text contracts.
- Route signatures, response schemas, SQLAlchemy locking, commit/rollback
  boundaries, and DB mutation order were intentionally left unchanged.

## Stage TG3 - Atomic Execution Hardening

Goal:

- Make duplicate/race/failure behavior explicit and deterministic.

Investigation targets:

- Trade number allocation under concurrent commits.
- Idempotency check placement and cross-server retry behavior.
- Retail lot and wholesale remaining-quantity race coverage.
- Commit/rollback boundaries around chain trades.

Allowed outcomes:

- Keep current logic if tests prove it safe enough.
- Add a low-risk retry or DB-backed allocation strategy if `max + 1` can race.
- Add stricter idempotency conflict handling if duplicate economic trades are
  possible.

Acceptance:

- Concurrency tests prove no negative remaining quantity and no duplicate lot
  consumption.
- Idempotent replay returns the same economic response.
- Rollback leaves no partial chain.

Completion notes:

- Added PostgreSQL transaction-scoped advisory locks for trade-number
  allocation and idempotency-key execution paths, with non-PostgreSQL no-op
  behavior for local/unit test contexts.
- Added stricter idempotent replay validation so a reused idempotency key must
  match the current economic request instead of silently returning an unrelated
  trade.
- Centralized offer quantity/lot mutation with explicit guards for negative
  remaining quantity and missing retail lots.
- Centralized commit handling so stale/unique conflicts map to deterministic
  `409` responses and unknown commit failures roll back before re-raising.
- Added `tests/test_trade_atomicity_hardening.py` covering advisory lock
  behavior, idempotency conflicts, quantity/lot guards, and rollback behavior.

## Stage TG4 - Cross-Server Authority Hardening

Goal:

- Ensure Iran/foreign asymmetric trade execution is safe and diagnosable.

Tasks:

- Verify behavior for foreign user on Iran-home offer and Iran user on
  foreign-home offer.
- Confirm internal signature, timestamp skew, source server, and target
  `home_server` checks.
- Review `verify=False` in `core/trade_forwarding.py` and decide whether it
  should become env-controlled, certificate-pinned, or explicitly documented as
  temporary.
- Ensure remote timeout/503/504 responses are user-safe and do not create local
  partial state.

Acceptance:

- Focused tests cover both forwarding directions, timeout, bad signature, wrong
  authoritative server, and idempotent remote retry.
- Structured logs identify source/target server and offer id without exposing
  tokens, signatures, or mobile numbers.

Completion notes:

- Added env-controlled trade-forward TLS verification through
  `TRADE_FORWARD_VERIFY_TLS` and optional `TRADE_FORWARD_CA_BUNDLE`, while
  preserving the current default behavior until internal certificates are
  promoted.
- Added redacted structured logs for unavailable peers, timeouts, request
  errors, invalid upstream JSON, remote 5xx responses, and rejected internal
  trade execution attempts.
- Hardened `/api/trades/internal/execute` so payload and `X-Source-Server`
  must resolve to the same known remote server and cannot equal the local
  authoritative target.
- Kept remote timeout/503/504 responses as forwarded responses before local
  authoritative execution, preventing local partial state when the authoritative
  peer is unavailable.
- Added focused coverage for both Iran-to-foreign and foreign-to-Iran
  forwarding, TLS verify options, bad signature/source checks, wrong
  authoritative server rejection, redacted logs, and idempotent remote retry
  payload preservation.

## Stage TG5 - Offer Lifecycle Consistency

Goal:

- Make offer creation, republish, expire, cancel, active-count cache, channel
  buttons, and realtime events consistent under partial side-effect failures.

Tasks:

- Test offer creation when Telegram/channel send fails.
- Test republish from active offer and ensure old/new offer state is consistent.
- Test cancel-all/expire cache decrement and event publication.
- Review `active_offer_count` cache repair paths.
- Verify auto-expiry only touches locally authoritative offers.

Acceptance:

- Cache and DB do not diverge after create/cancel/expire scenarios.
- Failed Telegram/channel side effects are visible in logs but do not corrupt DB
  state.
- Realtime `offer:*` events match final DB state.

Completion notes:

- Replaced post-create incremental active-offer cache updates with exact
  cache writes derived from the authoritative lifecycle outcome, including the
  active republish case where the old active offer expires and the new offer
  keeps the final active count unchanged.
- Hardened active republish at the max-offer limit: the old active offer is
  excluded from the limit calculation, linked only when it belongs to the
  owner, emits `offer:expired`, and has channel buttons removed best-effort
  after the DB state is committed.
- Moved `cancel-all` side effects after the DB commit so realtime `offer:*`
  events cannot announce a state that failed to persist.
- Wrapped active-count cache writes, realtime offer events, and channel button
  removal in visible best-effort logging so these side-effect failures do not
  corrupt committed offer state or fail the user response.
- Added focused lifecycle coverage for active republish at limit, post-commit
  cache/realtime failure tolerance, exact cache writes for create/expire/cancel,
  and the existing local-authority auto-expiry coverage.

## Stage TG6 - Customer, Accountant, And Commission Semantics

Goal:

- Make mediated trading economically correct and explainable.

Tasks:

- Confirm Tier 2 commission execution price and customer profit reporting use
  the correct historical trade price.
- If needed, add a commission snapshot field/migration only after a clear
  failing test proves relation edits can corrupt historical reporting.
- Verify accountant audiences and owner audiences are independent and correctly
  notified.
- Verify trade history for owner, accountant, customer, and super admin.

Acceptance:

- Historical reports remain correct after commission-rate edits.
- No actor-only leakage into trade history.
- Tests cover customer relation status changes after trades.

Completion notes:

- Confirmed the Tier 2 commission model does not need a new snapshot column at
  this stage: the executed customer leg price is already persisted on the
  `trades.price` row, and owner profit reporting is derived from historical
  trade-leg prices rather than the current customer relation commission rate.
- Relaxed customer trade statistics from active-only relation status to
  registered-customer history, while keeping session/update management
  active-only. Inactive relation reports are bounded by the relation historical
  window when timestamps are present, so post-revoke/post-delete trades do not
  leak into a previous owner's report.
- Added a historical customer-relation resolver for trade-history access. It
  prefers active relations, falls back to revoked/expired/deleted historical
  relations, and applies relation date bounds when showing target-customer
  history.
- Strengthened trade execution audience coverage so responder-owner and
  offer-owner audiences can independently include their accountants, and
  recipient-specific realtime events are emitted to each computed audience
  member.
- Added production-contract coverage for historical commission reporting after
  relation status/rate changes, customer-history access after relation status
  changes, owner/accountant audience independence, and existing actor-only
  leakage protection.

## Stage TG7 - Frontend Market Mutation UX

Goal:

- Prevent double-submit and ambiguous mutation states in the market UI.

Tasks:

- Verify offer publish and trade execute buttons generate stable idempotency
  keys where needed.
- Ensure mutation buttons lock during in-flight requests and recover on network
  failure.
- Make 409 lot suggestion, market closed, self-trade, and blocked-trade errors
  visibly understandable without page refresh.
- Keep mutations opt-out of indefinite global network retry loops where a user
  action needs a bounded result.

Acceptance:

- Focused Vitest and Playwright coverage for publish/execute conflict states.
- No hidden duplicate request on rapid taps.

Completion notes:

- Offer publish now sends a stable mutation idempotency key, disables duplicate
  in-flight submit attempts, opts out of unbounded global network retry, and
  reuses the same key across warning-confirm retry paths.
- Offer creation can replay an existing offer for the same owner/idempotency
  key without repeating side effects, and handles insert-time uniqueness races
  by rolling back and returning the existing offer response when present.
- Trade execution buttons now lock while a trade mutation is in flight, attach
  a stable idempotency key per offer/quantity tap, preserve that key after
  ambiguous network failures, and clear it after success or deterministic
  validation/conflict errors.
- Market publish/cancel and trade execute mutations now request bounded
  responses with `retryNetwork: false`, so user-visible mutation states do not
  get trapped behind indefinite background retries.
- Added focused backend, Vitest, and Playwright coverage for idempotent offer
  replay, duplicate rapid taps, publish/trade conflict visibility, and stable
  retry keys after ambiguous network failure.

## Stage TG8 - Observability And Audit

Goal:

- Make trading incidents diagnosable without leaking sensitive data.

Tasks:

- Add structured, redacted log fields for trade execution attempts, accepted
  trades, remote forwards, idempotent replays, and side-effect failures.
- Add low-cardinality metrics/counters only if they fit the existing
  observability contract.
- Document which fields are safe: offer id, trade id/number, normalized server,
  error class, status code. Do not log mobile numbers, OTPs, JWTs, signatures,
  raw request bodies, or full notification text.

Acceptance:

- Focused logging tests or snapshot assertions prove redaction.
- No raw exception/body text is added to hot-path logs.

## Stage TG9 - Staging Validation

Goal:

- Prove the trading money path on staging before production promotion.

Tasks:

- Run `scripts/deploy_staging.sh check`.
- Deploy only the isolated staging stack if runtime behavior changed.
- Seed isolated synthetic users/offers/trades.
- Run focused backend tests, frontend market tests, and staging smoke.
- Verify no production sync peer is configured in staging.

Acceptance:

- Staging health passes.
- Synthetic fixture cleanup passes.
- No production deploy or production data mutation occurs.

## Stage TG10 - Targeted Benchmark / Load Proof

Goal:

- Confirm trading hardening did not regress latency or capacity.

Default:

- Run local/staging targeted tests first.
- Production benchmark is not allowed unless the user explicitly asks for it.

If approved for production:

- Use existing production benchmark harnesses and isolated synthetic fixtures.
- Capture sync-health before/after.
- Do not run full matrix unless short targeted evidence justifies it.

Acceptance:

- Trading targeted benchmark passes or the regression is classified with a
  rollback recommendation.

## Stage TG11 - Promotion Review

Goal:

- Decide whether `candidate/trading-production-grade` can be merged to `main`.

Deliverables:

- Final summary of changed files and behavior.
- Test and staging evidence.
- Rollback plan.
- Accepted risks, if any.
- Explicit user approval before merge or production deploy.

Acceptance:

- No unreviewed staging-only helpers.
- No production deploy hidden inside the refactor.
- User explicitly approves promotion.
