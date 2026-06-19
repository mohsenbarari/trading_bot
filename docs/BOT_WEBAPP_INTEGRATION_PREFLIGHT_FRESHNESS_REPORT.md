# Bot/WebApp Integration Preflight Freshness Report

Date: 2026-06-19

Purpose: record Step 0A, the refreshed pre-code freshness check for
`candidate/bot-webapp-integration` before implementation starts. This report is an operational
gate only. It does not authorize merge into `main`, production deploy, or any cross-branch
promotion.

## Branch And Remote State

- Checked branch: `candidate/bot-webapp-integration`
- Worktree used for the check: `/tmp/trading-bot-bot-webapp-integration`
- Working tree state before collecting freshness data: clean
- `git fetch origin` completed before collecting SHAs.
- Local `main`: `63c643ced0c61a290feeb0598fa8e71ebe933cc0`
- `origin/main`: `63c643ced0c61a290feeb0598fa8e71ebe933cc0`
- Local `candidate/bot-webapp-integration`:
  `b398f096bbe3798687dbfc32cef41a35668cfacd`
- `origin/candidate/bot-webapp-integration`:
  `b398f096bbe3798687dbfc32cef41a35668cfacd`
- Merge base between `origin/main` and `candidate/bot-webapp-integration`:
  `136eff94e66a078b2311b9c48dc8090a619d6f5a`

## Divergence

`git rev-list --left-right --count origin/main...HEAD` returned:

```text
58 39
```

Interpretation:

- `origin/main` has 58 commits that are not in `candidate/bot-webapp-integration`.
- `candidate/bot-webapp-integration` has 39 commits that are not in `origin/main`.
- The candidate branch is up to date with its remote tracking branch.
- Local `main` and `origin/main` are aligned.

## Candidate-Side Diff Surface

From the merge base to `candidate/bot-webapp-integration`, the candidate branch changes only
documentation:

- `docs/BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md`
- `docs/BOT_WEBAPP_INTEGRATION_IMPLEMENTATION_CONTRACT.md`
- `docs/BOT_WEBAPP_INTEGRATION_PREFLIGHT_FRESHNESS_REPORT.md`

The current candidate-side diff stat against the merge base is:

```text
3 files changed, 2578 insertions(+), 126 deletions(-)
```

This confirms the candidate branch is still a planning/contract branch before Step 1 code work.

## Main-Side Diff Surface

`HEAD..origin/main` shows 65 changed files. The changed areas that matter for this roadmap include:

- Offer API and market history: `api/routers/offers.py`
- Trade/request execution: `api/routers/trades.py`
- Sync receive/apply paths: `api/routers/sync.py`
- Bot offer creation, execution, and management handlers:
  `bot/handlers/trade_create.py`, `bot/handlers/trade_execute.py`, `bot/handlers/trade_manage.py`
- Offer expiry and market close behavior:
  `core/offer_expiry.py`, `core/services/market_transition_service.py`
- Telegram terminal offer channel state:
  `core/services/telegram_offer_channel_service.py`
- Routing, events, observability, and Web Push:
  `core/server_routing.py`, `core/events.py`, `core/trading_observability.py`, `core/web_push.py`
- Offer/trade models and schemas: `models/offer.py`, `models/trade.py`, `schemas.py`
- Frontend market/history display:
  `frontend/src/components/OffersList.vue`, `frontend/src/views/MarketView.vue`,
  `frontend/src/views/DashboardView.vue`, `frontend/src/views/NotificationsView.vue`
- Market-history and terminal marker tests:
  `tests/test_telegram_offer_channel_service.py`, `tests/test_offers_router_helpers.py`,
  `tests/test_offer_expiry.py`, `tests/test_sync_router_receive_offer_publish.py`
- Production deploy/release guards:
  `scripts/production_deploy_online.sh`, `deploy/production/online.env.example`,
  `docs/PRODUCTION_DEPLOYMENT_ONLINE.md`

Main also contains market-history roadmap/review docs and migrations for expired/traded offer
history fields and indexes. After refresh, these are treated as baseline behavior for this
candidate branch.

## Merge Simulation

`git merge-tree --write-tree origin/main HEAD` completed successfully and returned:

```text
76b1e445a2b5c9a8684552106cab0f69e4f0d267
```

The sandboxed first attempt failed to create a temporary object, so the same command was rerun with
limited approval for `git merge-tree`. The rerun succeeded and did not modify the branch.

Interpretation:

- Git did not detect a textual merge conflict for combining current `origin/main` with current
  `candidate/bot-webapp-integration`.
- Textual merge risk is low at this snapshot.
- Semantic drift risk remains medium to high because `main` changed offer, trade, expiry, sync,
  Telegram publication, frontend market-history, and release-guard behavior after the branch point.

## Baseline Behavior After Refresh

After Step 0B refresh, the following current-main behavior must be treated as baseline and not
reimplemented from scratch unless tests show it violates the Bot/WebApp integration contract:

- `/api/offers/market-history` and the WebApp/frontend history behavior.
- Telegram terminal offer markers for fully traded, partially traded, and expired offers.
- Removal of Telegram inline controls for terminal offers.
- Production release/deploy guards that currently validate the market-history behavior.
- Existing `expired_at`, trade-history, and market-history migrations and tests.

## Step 0A Result

Step 0A can proceed to Step 0B under the contract because:

- The checked branch is correct.
- The worktree was clean before collecting freshness data.
- Local `main` and `origin/main` are aligned.
- Candidate and origin candidate are aligned.
- Merge simulation succeeded without textual conflicts.

Step 0B should still stop if a new branch/status check is not clean immediately before merge.

## Recommendation

Proceed with the owner-approved normal merge from `main` into
`candidate/bot-webapp-integration`, then write the Step 0C semantic drift report before Step 1 code
implementation starts.

No production action is allowed from this roadmap without the final documented gates and the exact
owner approval phrase.
