# Trading Production-Grade Promotion Review

Date: 2026-06-17

Branch: `candidate/trading-production-grade`

Status: pre-merge review complete. Merge, production deploy, production
benchmark, production sync, and production data mutation require a new explicit
owner command.

## Decision Summary

The trading hardening work is ready for owner review before merge. The money
path has focused contract coverage, staging validation, and a targeted staging
load proof. I do not recommend merging automatically because this candidate
branch also carries Web Push changes and migrations from the web-push candidate
history, so promotion of this branch is not trading-only.

Recommended promotion condition:

- Owner explicitly accepts that merging this branch promotes both the trading
  hardening work and the Web Push notification/migration scope.
- Owner explicitly requests merge.
- Owner separately requests any production deploy.

## Scope Promoted By This Candidate Branch

Trading hardening:

- Offer create/list/expire/cancel and active-offer consistency.
- Atomic trade execution and concurrency semantics.
- Trade idempotency and replay behavior.
- Cross-server authoritative trade forwarding.
- Customer/accountant economic semantics, relation history, and commission
  snapshot reporting.
- Market frontend mutation UX for duplicate submit, idempotency keys,
  conflict/network recovery, and visible user feedback.
- Bounded trading observability with redacted structured logs and
  low-cardinality metrics.
- Staging validation and targeted load proof using isolated synthetic data.

Additional scope present in this branch:

- Web Push notification subscriptions, preferences, frontend service worker
  wiring, VAPID helper, and related migrations.
- Staging deployment guardrail and staging deploy helper updates.

## Main Files Touched

Backend trading:

- `api/routers/offers.py`
- `api/routers/trades.py`
- `api/routers/customers.py`
- `core/trade_forwarding.py`
- `core/trading_observability.py`
- `core/services/trade_service.py`
- `scripts/trading_core_probe_worker.py`

Frontend trading:

- `frontend/src/views/MarketView.vue`
- `frontend/src/components/OffersList.vue`
- `frontend/e2e/market-mutation-ux.spec.ts`

Web Push / notifications:

- `core/web_push.py`
- `api/routers/notifications.py`
- `models/push_subscription.py`
- `models/user_notification_preference.py`
- `frontend/src/services/webPush.ts`
- `frontend/public/push-notifications-sw.js`
- `migrations/versions/c0d1e2f3a4b5_add_push_subscriptions.py`
- `migrations/versions/d1e2f3a4b5c6_add_user_notification_preferences.py`

Operational/staging:

- `.github/staging-instructions.md`
- `deploy/staging/*`
- `scripts/deploy_staging.sh`
- `docs/TRADING_PRODUCTION_GRADE_ROADMAP.md`

## Validation Evidence

Backend focused promotion gate:

```bash
python3 -m unittest \
  tests.test_trading_production_contract_matrix \
  tests.test_trade_atomicity_hardening \
  tests.test_trade_execution_seams \
  tests.test_server_routing_and_trade_forwarding \
  tests.test_offers_router_create_success \
  tests.test_offers_router_expire \
  tests.test_trades_router_authoritative_success \
  tests.test_trades_router_authoritative_guards \
  tests.test_trades_router_execution_wrappers \
  tests.test_trades_router_helpers \
  tests.test_trades_router_reads \
  tests.test_customers_router \
  tests.test_trading_observability \
  tests.test_trading_core_probe_worker \
  tests.test_web_push \
  tests.test_notifications_preferences
```

Result: `140 tests OK`.

Syntax gate:

```bash
python3 -m py_compile \
  api/routers/offers.py \
  api/routers/trades.py \
  api/routers/customers.py \
  api/routers/notifications.py \
  core/trade_forwarding.py \
  core/trading_observability.py \
  core/web_push.py \
  scripts/trading_core_probe_worker.py
```

Result: passed.

Frontend focused gate:

```bash
npm run test:unit:run -- \
  src/views/MarketView.test.ts \
  src/components/OffersList.test.ts \
  src/composables/useNotificationRuntime.test.ts
```

Result: `48 tests passed`.

Frontend market e2e:

```bash
npm run test:e2e -- e2e/market-mutation-ux.spec.ts --project=chromium --reporter=line
```

Result: `1 passed`.

Frontend production build:

```bash
npm run build
```

Result: passed. Existing large-chunk warnings remain non-blocking.

Staging health:

```bash
scripts/deploy_staging.sh health
```

Result:

```json
{"bot_username":"staging_bot_placeholder","frontend_url":"https://staging.362514.ir"}
```

Staging log scan:

- Recent staging app logs were scanned for `Traceback`, `CancelledError`,
  `ERROR`, `CRITICAL`, callback exceptions, `QueuePool limit`, `TimeoutError`,
  and Web Push background failures.
- Result: no matches after the accepted TG10 run.

## Staging Benchmark Evidence

TG9 staging probe:

- Isolated `TG9_STAGE_*` synthetic users/offers/trades.
- Covered parser, bot text offer handling, offer create/list/expire, trade
  execution, notification fanout, and retail-lot race.
- Race produced exactly one persisted winning trade and a completed
  zero-remaining offer.
- Cleanup verified zero synthetic leftovers.

TG10 targeted staging load proof:

- Accepted profile:
  `parse=200`, `bot=12`, `create=4`, `list=60`, `expire=2`, `trade=20`,
  `notification=20`, `race-concurrency=6`.
- Accepted result:
  - parser p95: `2.811ms`
  - bot handler p95: `168.844ms`
  - offer create p95: `456.273ms`
  - offer list p95: `28.977ms`
  - offer expire p95: `29.522ms`
  - trade execute p95: `244.512ms`
  - notification fanout p95: `62.543ms`
  - race p95: `487.631ms`
- Race result:
  one success, five clean `400` rejections, zero unexpected errors, one
  persisted trade, zero remaining quantity, completed offer status.
- Cleanup verified zero synthetic leftovers.

Invalid/over-capacity TG10 profiles:

- `create-iterations=18` violated the active-offer cap of 4.
- `expire-iterations=3` violated the expire rate-limit of 2 per minute.
- `race-concurrency=12` produced staging DB pool pressure and timeout/long-tail
  race attempts. This is documented as staging over-capacity evidence, not as
  accepted promotion evidence.

## Accepted Risks

1. This candidate branch is not trading-only.

   Web Push code, migrations, and frontend service-worker wiring are included.
   If the owner wants trading-only promotion, this branch should not be merged
   as-is.

2. No production benchmark was run in TG10.

   This follows the staging rules. Production benchmark requires a separate
   explicit owner command.

3. Staging race concurrency 12 exceeded the staging DB pool envelope.

   The accepted profile is clean at concurrency 6. If production traffic is
   expected to create heavier same-offer contention, run an owner-approved
   production targeted benchmark before deploy.

4. `api/routers/trades.py` remains a large orchestration module.

   TG2 created seams and tests around the critical money path, but this was not
   a broad router rewrite. A deeper structural split should be a later
   refactor, not part of this promotion.

5. Existing datetime deprecation warnings remain in focused tests.

   They are not new TG11 blockers, but should remain in the technical debt
   backlog.

6. Frontend build still reports large chunk warnings.

   The build passes. Chunk splitting remains a broader frontend performance
   task, not a blocker for this trading hardening promotion.

## Post-Review Web Push Hardening

After the initial pre-merge review, the Web Push promotion gate was tightened:

- `scripts/render_runtime_envs.py` now carries the production `WEB_PUSH_*`
  settings into both foreign and Iran runtime env files. The safe default is
  `WEB_PUSH_ENABLED=false` with blank VAPID keys, so production does not request
  or deliver Web Push unless the operator deliberately provides VAPID values and
  enables it.
- `frontend/src/services/webPush.ts` now fetches
  `/api/notifications/push/public-key` before requesting browser notification
  permission in the automatic first-interaction bootstrap path. If the server
  reports Web Push disabled, the browser permission prompt is skipped.
- Focused regression coverage was added for both runtime env rendering and the
  frontend Web Push prompt ordering.

## Rollback Plan

Before merge:

- Do not merge `candidate/trading-production-grade`.
- Continue work on the candidate branch or create a narrower candidate branch
  if Web Push should be excluded from promotion.

After merge but before production deploy:

- Revert the merge commit or reset the staging/release branch back to the last
  known good `main` commit.
- Do not run migrations or production deploy until the revert decision is
  settled.

After production deploy:

- Stop new deploys.
- Capture app, worker, DB, Redis, and Nginx logs.
- Check trading-specific metrics/logs for `trade_execute`, `trade_forward`,
  `offer_create`, and `trading_side_effect` failures.
- If the issue is code-only and migrations are backward compatible, redeploy
  the previous production image/commit.
- If Web Push migrations are already applied, do not manually drop tables during
  an incident. Treat schema rollback as a separate planned database operation.
- If trading data corruption is suspected, stop market mutations first, preserve
  DB snapshots, and use backup/restore runbooks before any data correction.

## Promotion Recommendation

I would not block promotion on the trading hardening evidence. The trading money
path has materially better coverage, concurrency proof, idempotency behavior,
cross-server authority handling, staging validation, and observability.

I would block automatic merge until the owner explicitly accepts the branch
scope. The merge decision is primarily about whether Web Push should be promoted
together with trading hardening.

Final state: ready for explicit owner approval to merge, not merged.
