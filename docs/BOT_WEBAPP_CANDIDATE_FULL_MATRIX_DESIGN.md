# Bot/WebApp Candidate Full Matrix Design

Date: 2026-06-23

Branch: `candidate/bot-webapp-integration`

Status: design gate before owner-led manual staging tests.

This document defines the full staging matrix required before manual testing of
the Bot/WebApp integration candidate. It combines the existing market behavior
matrix, the trade notification delivery matrix, and outage/recovery validation
into one release-candidate evidence plan.

## Scope And Hard Rules

- Run only on `candidate/bot-webapp-integration` unless the owner explicitly
  approves another branch.
- Use staging and synthetic data only.
- Do not use production peers, production Telegram channels, or production data.
- Iran must never connect to Telegram.
- The WebApp remains Iran-only.
- The Telegram bot remains foreign-only.
- Foreign must not serve the WebApp/frontend surface.
- Messenger data is not part of cross-server sync validation.
- Production remains blocked until the owner manually validates staging evidence.

## Current Code-Owned Inputs

The matrix must reuse these existing code-owned gates instead of introducing a
parallel ad hoc checklist:

- `scripts/report_bot_webapp_integration_matrix.py`
  - Existing Step 11 policy/integration catalog.
  - Current catalog size: 27 scenarios.
- `scripts/run_bot_webapp_comprehensive_load_matrix.py`
  - Existing executable market behavior matrix.
  - Current catalog size: 228 logical scenarios.
- `scripts/run_staging_comprehensive_load_matrix.sh`
  - Staging wrapper for the 228-scenario market matrix through the
    `staging-load` compose profile.
- `scripts/report_trade_notification_delivery_matrix.py`
  - Current deterministic trade delivery audience catalog.
  - Current catalog size: 17 actor pairs x 4 surface pairs x 3 outage classes =
    204 scenarios.
- `scripts/report_trade_delivery_staging_validation.py`
  - Stage 11 delivery validation contract and artifact validator.
  - Current validation matrix size: 16 scenarios.
- `scripts/run_bot_webapp_candidate_full_matrix.py`
  - Candidate wrapper that ties the market matrix, notification-delivery matrix,
    and Stage 11 validation matrix into one artifact directory.
- `scripts/run_trade_delivery_targeted_join_matrix.py`
  - Executable staging join matrix for real synthetic offers, trades, delivery
    receipts, and WebApp/Telegram delivery evidence.
  - Dry-run mode writes the complete 204-scenario catalog without database,
    sync, WebApp, or Telegram side effects.
  - Execution mode uses a fake injected Telegram gateway so the Telegram
    delivery receipt path is tested without making any real Telegram network
    call.

## Matrix Layers

The release-candidate full matrix is intentionally layered. A full Cartesian
product across market shape, actor relation, channel, outage, link state,
receipt state, and crash mode would create thousands of redundant or impossible
runtime cases. The accepted design is therefore:

1. Use the 228-scenario market matrix to prove market behavior breadth.
2. Use the 204-scenario delivery audience matrix to prove role, relation,
   surface, offer-home, and outage policy breadth.
3. Use Stage 11 runtime validation to prove receipt workers, dedupe, logs,
   latency, crash recovery, and outage behavior on staging.
4. Use targeted join scenarios to prove that the market matrix and delivery
   matrix meet on real completed trades, not only in isolated unit tests.

## Layer 0: Preflight

Purpose: prevent false evidence from a wrong branch, stale deploy, dirty
database, or wrong runtime topology.

Required checks:

- `git branch --show-current` is `candidate/bot-webapp-integration`.
- `git status -sb` is clean before starting a run.
- Staging app health passes.
- Staging bot is running on foreign mode and polling the staging bot.
- Staging load services are profile-gated and role-bound:
  `load_telegram_foreign` must run as foreign and `load_webapp_iran` must run as
  Iran.
- Current deploy SHA is recorded in the artifact.
- A unique synthetic prefix is generated.
- Previous synthetic data for the selected prefix is absent or cleaned by the
  prefix-scoped cleanup command.
- Sync health is captured before the run.

Stop if:

- The branch is wrong.
- The artifact references production.
- Iran logs show Telegram gateway calls.
- Foreign logs show WebApp/frontend serving.
- Cleanup target prefix is empty, broad, or not run-scoped.

## Layer 1: Deterministic Baseline Gates

Purpose: prove that the static/pure policy catalogs and required test references
still exist before mutating staging.

Required commands:

```bash
python3 scripts/report_bot_webapp_integration_matrix.py --check --json
python3 scripts/report_trade_notification_delivery_matrix.py --check --output /tmp/<run>/trade-notification-delivery-matrix.json
python3 scripts/run_trade_delivery_targeted_join_matrix.py --dry-run --check --output /tmp/<run>/trade-delivery-targeted-join-matrix.json
python3 scripts/report_trade_delivery_staging_validation.py matrix --repo-root "$PWD" --output /tmp/<run>/trade-delivery-stage11-matrix.json
python3 -m unittest \
  tests.test_bot_webapp_integration_matrix \
  tests.test_bot_webapp_candidate_full_matrix \
  tests.test_bot_webapp_comprehensive_load_matrix \
  tests.test_trade_notification_delivery_matrix \
  tests.test_trade_delivery_targeted_join_matrix \
  tests.test_trade_delivery_receipt_service \
  tests.test_trade_delivery_worker \
  tests.test_trade_webapp_delivery_service \
  tests.test_trade_telegram_delivery_service
```

Required outcome:

- All commands pass.
- Scenario catalogs are parseable JSON.
- Production gate remains `blocked_until_owner_staging_validation`.

## Layer 2: Market Behavior Matrix

Code owner: `scripts/run_bot_webapp_comprehensive_load_matrix.py`

Current size: 228 logical scenarios.

Dimensions:

- Offer origin: `webapp`, `bot`.
- Offer home server:
  - WebApp-created offer: `iran`.
  - Bot-created offer: `foreign`.
- Offer type: `buy`, `sell`.
- Offer shape:
  - `wholesale_full`: one request can fully complete the offer.
  - `retail_two_lot`: two lots, partial completion path.
  - `retail_three_lot`: three lots, multi-partial completion path.
- Request/action surface: `webapp`, `telegram`.
- Terminal state: `active`, `completed`, `manual_expired`, `time_expired`.
- Families:
  - `create_offer`
  - `active_view`
  - `public_detail_view`
  - `market_history_view`
  - `trade_concurrent`
  - `trade_non_concurrent`
  - `manual_expire_non_concurrent`
  - `manual_expire_contention`
  - `time_expiry`
  - `after_completed_reject`
  - `after_manual_expiry_reject`
  - `after_time_expiry_reject`

Required assertions:

- `Offer.home_server` follows the publishing platform, not the user's home.
- Created offers appear on the peer surface within the accepted lag window under
  stable sync.
- Concurrent trades never over-trade an offer.
- `remaining_quantity` never becomes negative.
- Retail lots cannot be sold twice.
- Duplicate WebApp idempotency keys do not create duplicate trades.
- Duplicate Telegram callbacks do not create duplicate trades.
- Completed and expired offers never reactivate.
- Manual expiry from WebApp and Telegram converges on both surfaces.
- Time expiry converges on both surfaces.
- Requests after completed/manual-expired/time-expired terminal states are
  rejected without new trades.
- WebApp history shows original quantity and traded quantity correctly.
- Telegram terminal posts remove interactive buttons after completed or expired
  states.
- Telegram completed/partially completed posts are edited with the accepted
  terminal marker.
- Telegram expired posts are edited with the accepted expired marker and no
  interactive buttons.

Execution profile before manual testing:

- No pressure profile is acceptable for owner-pretest evidence:
  200 synthetic users, 5 attempts per scenario, 20 target RPS, 60/40
  Telegram/WebApp mix, and write concurrency cap 4.
- A later capacity run can raise this to 1000 users and 600 RPS, but the user
  explicitly excluded pressure testing for the current pre-manual-test phase.

## Layer 3: Trade Notification Delivery Audience Matrix

Code owner: `scripts/report_trade_notification_delivery_matrix.py`

Current size: 204 scenarios:

- 17 actor pairs.
- 4 surface pairs:
  - WebApp offer, WebApp request.
  - WebApp offer, Telegram request.
  - Telegram offer, WebApp request.
  - Telegram offer, Telegram request.
- 3 outage classes:
  - `stable`
  - `short_under_2m`
  - `medium_around_60m`

Actor pairs:

- user with user.
- user with tier-1 customer, same owner.
- user with tier-2 customer, same owner.
- user with tier-1 customer, other owner.
- user with tier-2 customer, other owner.
- tier-1 customer with user, same owner.
- tier-2 customer with user, same owner.
- tier-1 customer with user, other owner.
- tier-2 customer with user, other owner.
- tier-1 customer with tier-1 customer, same owner.
- tier-1 customer with tier-2 customer, same owner.
- tier-2 customer with tier-1 customer, same owner.
- tier-2 customer with tier-2 customer, same owner.
- tier-1 customer with tier-1 customer, other owner.
- tier-1 customer with tier-2 customer, other owner.
- tier-2 customer with tier-1 customer, other owner.
- tier-2 customer with tier-2 customer, other owner.

Required audience rules:

- Every eligible direct participant receives WebApp delivery.
- Every eligible participant with a linked Telegram account receives Telegram
  delivery.
- Users/admins and tier-1 customers may receive Telegram if linked and eligible.
- Tier-2 customers never receive Telegram.
- Accountants never receive Telegram.
- Accountants receive WebApp notifications for monitored owner/customer trades.
- Customer-facing messages for both tier-1 and tier-2 customers must show the
  customer's owner as the counterparty.
- Customer-facing messages must not expose the non-owner direct market
  counterparty.
- Owner/customer-chain delivery must follow the existing owner-routed trade
  plan. The matrix must not invent a separate direct customer trade path.
- Offer home server must not change audience rules.
- Trade description must be included in WebApp and Telegram trade messages.

Link-state submatrix:

- Eligible linked user: WebApp notification and Telegram message are required.
- Eligible unlinked user: WebApp notification is required; Telegram receipt is
  `not_required`.
- Eligible user with unreachable/broken Telegram account: WebApp notification is
  required; Telegram send failure is skipped without crashing and without
  leaving an indefinite pending backlog.
- Re-linked/fixed Telegram account: old skipped receipts are not reopened; the
  next new trade sends only that new trade's Telegram message.
- Denied bot user states, including accountant, tier-2 customer, inactive,
  deleted, market-blocked, and watch-only, must never require Telegram.

## Layer 4: Runtime Delivery Receipt Matrix

Code owner: `scripts/report_trade_delivery_staging_validation.py`

Purpose: verify that durable receipts and workers behave correctly in staging.

Required receipt states:

- `pending`
- `processing`
- `retry_pending`
- `sent`
- `skipped`
- `not_required`
- `permanent_failed`

Important clarification:

- The status histogram must know all receipt statuses.
- A broken or unreachable Telegram account must not become a permanent failed
  user backlog. It must be skipped for that trade and retried naturally on later
  new trades if the user fixes/relinks Telegram.

Required runtime assertions:

- Required WebApp notifications are durable rows with dedupe keys.
- Required Telegram deliveries are durable receipts on the foreign side.
- WebApp delivery runs on Iran only.
- Telegram delivery runs on foreign only.
- Immediate delivery and worker repair use the same receipt-backed path.
- A WebApp receipt cannot be marked `sent` without durable notification history.
- Duplicate repair loads the existing notification instead of creating another
  visible notification.
- Web Push failure does not roll back durable WebApp notification history.
- Telegram success stores the Telegram message id.
- Telegram unreachable/user-not-found style errors do not crash the worker.
- Worker crash before send is recoverable and eventually sends once.
- Worker crash after Telegram send but before marking `sent` is the only
  accepted ambiguity; if duplicated, the artifact must isolate and explain it.

## Layer 5: Short Outage Matrix

Definition: cross-server interruption under 2 minutes.

Required behavior:

- Each server continues to accept and complete its own authoritative local
  operations.
- Local-side trade notifications/messages are sent by the local authoritative
  delivery channel.
- Opposite-server delivery remains pending/retryable while the peer side is not
  visible.
- After recovery inside the short-outage window, opposite-server WebApp and
  Telegram deliveries are still sent.
- No stale skip is allowed for short outage.
- Final sync health must be clean.
- Final offer/trade/request-ledger state must match on both servers.

Required directions:

- WebApp-created Iran-home offer, WebApp request.
- WebApp-created Iran-home offer, Telegram request.
- Telegram-created foreign-home offer, WebApp request.
- Telegram-created foreign-home offer, Telegram request.

Required recipient coverage:

- user/user linked.
- user/tier-1 linked.
- user/tier-2.
- tier-1/user linked.
- tier-2/user.
- one customer/customer same-owner case.
- one customer/customer other-owner case.
- accountant monitoring recipient.

Required evidence:

- Receipt rows before recovery: remote delivery not terminal-sent yet.
- Receipt rows after recovery: required remote deliveries are `sent`.
- No `expired_delivery_after_outage` reason for short outage.
- No duplicate visible notifications.
- Sanitized app, bot, sync, DB, and worker logs.

## Layer 6: Medium Outage Matrix

Definition: cross-server interruption beyond the short-outage window and up to
the medium policy window. The current code classifies medium delivery age as
greater than 120 seconds and up to 3600 seconds.

Required behavior:

- Each server continues to accept and complete its own authoritative local
  operations.
- Each server sends only the local messages/notifications it can send safely.
- Old opposite-server trade delivery is skipped after recovery to avoid stale
  user-facing messages.
- Skipped remote receipts must carry the outage skip reason.
- No old WebApp notification, Telegram message, or user-facing stale trade
  report is sent after medium outage recovery.
- Offers that were active before full recovery and full sync must not be
  published as active to the other server after recovery. The low-cost policy is
  to expire those pre-recovery active local-only offers.
- Final sync health must be clean before returning to normal near-real-time sync.

Required directions:

- Same four surface directions as the short-outage matrix.
- At least one completed trade and one locally expired offer per home server.
- At least one active pre-recovery offer per home server that is expired during
  recovery finalization instead of being published active to the peer.

Required evidence:

- Receipt rows show `skipped` for old remote deliveries.
- Skip reason is `expired_delivery_after_outage`.
- `unexpected_old_remote_delivery_count` is zero.
- No active pre-recovery local-only offers are published active to the peer.
- Sync health is clean after recovery finalization.

## Layer 7: Long Outage Regression

The owner specifically asked for short and medium outage coverage before manual
testing. Long outage is still part of the project policy and must remain in the
Stage 11 validation contract.

Required minimum:

- Simulated long-outage receipt classification is validated.
- Old remote delivery is skipped.
- No user-facing stale message is sent.
- Recovery finalization policy matches the medium-outage behavior for
  pre-recovery active local-only offers.

## Layer 8: Targeted Join Scenarios

Purpose: prove that market execution and delivery audience rules meet on real
completed trades.

The executable owner-pretest join runner is
`scripts/run_trade_delivery_targeted_join_matrix.py`. It is intentionally based
on the same 204-scenario catalog as Layer 3:

- 17 actor pairs.
- 4 surface pairs.
- 3 outage classes.

Scenarios that are impossible by product policy are not omitted. They are
recorded as `policy_unsupported` with explicit reasons. The current unsupported
classes are:

- `tier2_cannot_create_offer`
- `tier2_cannot_use_telegram_request`

All policy-supported scenarios must be executable on staging with synthetic
data. The runner creates real synthetic users, customer relations, accountant
relations, offers, and trades, then repairs delivery through the real WebApp and
Telegram receipt services. Telegram network calls are disabled by an injected
gateway that returns a successful `TelegramGatewayResult`.

Each join scenario must assert:

- The trade is persisted with the expected owner-routed trade legs.
- The offer terminal state and remaining quantity are correct.
- The offer request ledger has the accepted/rejected/duplicate outcome expected
  for that scenario.
- WebApp notification recipients are exactly the required recipients.
- Telegram recipients are exactly the required linked eligible recipients.
- Accountants receive WebApp only.
- Tier-2 customers receive WebApp only.
- Customer-facing counterparty text is the owner, not the direct non-owner
  counterparty.
- No duplicate visible notification/message exists for one trade, recipient, and
  channel.
- Stable and short-outage scenarios do not create
  `expired_delivery_after_outage` skips.
- Medium-outage scenarios skip old opposite-server required delivery without
  sending stale user-facing messages.

## Layer 9: UI And Publication Evidence

Required WebApp evidence:

- Active offer appears in market list/detail.
- Completed offer appears in market history/detail.
- Partially completed offer shows original quantity and traded quantity.
- Manually expired offer appears muted as expired.
- Time-expired offer appears muted as expired.
- Notification center shows the trade notification with route/metadata.
- Realtime state does not disappear after trade/expiry.

Required Telegram evidence:

- Active channel post has interactive buttons.
- Fully completed offer post gets the accepted completed marker and no
  interactive buttons.
- Partially completed offer post gets the accepted partial completed marker with
  traded quantity and no wrong punctuation.
- Expired offer post gets the accepted expired marker and no interactive
  buttons.
- Trade message sent to users uses the accepted detailed layout and includes
  offer description.

## Layer 10: Artifact Contract

The final full matrix artifact directory must include:

- `candidate-full-matrix-summary.json`
- `comprehensive-matrix.json`
- `trade-notification-delivery-matrix.json`
- `trade-delivery-targeted-join-matrix.json`
- `trade-delivery-stage11-matrix.json`
- `trade-delivery-stage11-validation-report.json`
- `sync-health-before.json`
- `sync-health-after.json`
- `outage-summary.json`
- `receipt-metrics.json`
- sanitized app logs
- sanitized bot logs
- sanitized sync logs
- sanitized DB logs or DB query evidence
- worker logs
- cleanup dry-run artifact
- cleanup final artifact

The Stage 11 validation artifact must satisfy
`scripts/report_trade_delivery_staging_validation.py validate`.

## Pass/Fail Rules

Immediate failure:

- Wrong branch or wrong deploy SHA.
- Any production data or production service is touched.
- Iran calls Telegram.
- Foreign serves WebApp/frontend routes.
- Any stable-connectivity required WebApp notification is missing.
- Any stable-connectivity required Telegram message is missing.
- Any duplicate visible WebApp notification exists for one trade/recipient.
- Any duplicate visible Telegram message exists outside the isolated accepted
  crash-after-send ambiguity.
- Any customer-facing message exposes the non-owner direct counterparty.
- Any accountant receives Telegram.
- Any tier-2 customer receives Telegram.
- Any offer over-trades or has negative remaining quantity.
- Any completed/expired offer returns to active.
- Any medium-outage old remote delivery sends a stale user-facing message.
- Any pre-recovery active local-only offer is published active to the peer after
  medium recovery.

Acceptable warning only with explicit explanation:

- Stable delivery latency exceeds the 1-2 second target but every message is
  delivered and the miss is explained.
- Staging capacity forces the no-pressure profile to run below the later
  1000-user/600-rps target.
- The accepted Telegram crash-after-send ambiguity is reproduced and isolated.

## Required Work Before Manual Testing

The repository now includes the baseline catalogs, market runner, candidate
wrapper, and executable targeted join matrix runner. The remaining pre-manual
testing work is operational:

1. Run the deterministic baseline gates.
2. Run `scripts/run_bot_webapp_candidate_full_matrix.py` on staging.
3. Review `trade-delivery-targeted-join-matrix.json`, especially any `failed`
   policy-supported scenario.
4. Validate the artifact directory.
5. Clean synthetic data by the run-scoped prefix.
6. Start owner-led manual staging tests only after the automated artifact is
   clean.

## Suggested Execution Order

1. Preflight and deploy health.
2. Deterministic baseline gates.
3. Candidate full matrix dry-run.
4. No-pressure market behavior matrix.
5. Runtime delivery targeted join matrix under stable, short-outage, and
   medium-outage classes.
6. Stage 11 validation report.
7. Cleanup dry-run.
8. Cleanup.
9. Owner-led manual staging test.
