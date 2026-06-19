# Market Traded Offer History Promotion Review

Date: 2026-06-18

Branch: `candidate/market-traded-history`

Status: promotion-readiness review complete. Merge to `main`, production deploy,
production sync, and production data mutation require a new explicit owner
command.

## Decision Summary

The branch is ready for owner review before merge. It is a focused market-history
candidate that adds read-only two-day history for completed and partially traded
offers while keeping active-market behavior unchanged.

Recommended promotion condition:

- Owner explicitly approves merging `candidate/market-traded-history` to `main`.
- Owner separately approves any production deployment.
- If production deployment is approved, use the normal production release path
  and keep the Iran shared-data guard decision explicit.

## Scope Promoted By This Candidate Branch

Backend market read model:

- Adds `GET /api/offers/market-history` for terminal market history rows.
- Includes completed offers, pure `expire_reason=time_limit` expired offers, and
  expired offers with completed trade quantity even when the remaining quantity
  was manually expired.
- Aggregates completed trade quantity from source-offer `trades.offer_id` rows.
- Returns explicit read-only history metadata:
  `history_state`, `history_label`, `traded_quantity`,
  `is_partially_traded`, `is_read_only`, and `history_event_at`.

Backend sync and Telegram convergence:

- Publishes local terminal-offer realtime refreshes after synced terminal offers.
- Also refreshes when synced completed trades arrive after the terminal offer.
- Adds a foreign-only Telegram channel offer gateway for completed and partially
  traded-expired tags.
- Keeps Iran from calling Telegram side effects directly.

Frontend market UI:

- Switches the read-only history area from `/api/offers/expired` to
  `/api/offers/market-history`.
- Keeps history appended only on the all-market tab.
- Keeps buyer, seller, and my-offers tabs limited to active offers.
- Renders:
  - pure expired rows as `منقضی`,
  - completed rows as `معامله‌شده`,
  - partial traded-expired rows as `معامله‌شده · {traded_quantity} عدد`.
- Removes trade/cancel controls from all read-only history rows.

Database/indexing:

- Adds Alembic revision `f5b6c7d8e9a0_add_trade_history_offer_index.py`.
- Adds partial index `ix_trades_completed_offer_history` on completed source
  offer trades to support the aggregate query.

## Main Files Touched

Backend:

- `api/routers/offers.py`
- `api/routers/sync.py`
- `api/routers/trades.py`
- `bot/handlers/trade_execute.py`
- `core/offer_expiry.py`
- `core/services/telegram_offer_channel_service.py`
- `models/trade.py`
- `migrations/versions/f5b6c7d8e9a0_add_trade_history_offer_index.py`

Frontend:

- `frontend/src/views/MarketView.vue`
- `frontend/src/components/OffersList.vue`
- `frontend/src/composables/useOffers.ts`

Tests and docs:

- `tests/test_offers_router_helpers.py`
- `tests/test_sync_router_receive_basic.py`
- `tests/test_sync_router_receive_offer_publish.py`
- `tests/test_sync_router_apply_item_success.py`
- `tests/test_sync_router_remaining_paths.py`
- `tests/test_telegram_offer_channel_service.py`
- `tests/test_offer_expiry.py`
- `tests/test_bot_trade_execute_update_markup.py`
- `frontend/src/views/MarketView.test.ts`
- `frontend/src/components/OffersList.test.ts`
- `frontend/src/composables/useOffers.test.ts`
- `docs/MARKET_TRADED_OFFER_HISTORY_ROADMAP.md`

## Validation Evidence

Post-review product correction:

- On 2026-06-19, the history query was corrected so partially traded expired
  offers are included even when the remaining quantity was manually expired.
  Pure manual-expired offers with no completed trade quantity remain hidden.
- Focused validation: `python3 -m unittest tests.test_offers_router_helpers`
  passed with `8 tests OK`.
- The broader market-history backend matrix was re-run and passed with
  `69 tests OK`; `python3 -m py_compile api/routers/offers.py` and
  `git diff --check` also passed.

Backend focused gate:

```bash
python3 -m unittest \
  tests.test_sync_router_receive_basic \
  tests.test_sync_router_receive_offer_publish \
  tests.test_sync_router_apply_item_success \
  tests.test_sync_router_remaining_paths \
  tests.test_offers_router_helpers \
  tests.test_telegram_offer_channel_service \
  tests.test_trades_router_authoritative_success \
  tests.test_trades_router_authoritative_guards \
  tests.test_bot_trade_execute_remaining_paths
```

Result: `69 tests OK`.

Frontend focused gate:

```bash
npm run test:unit:run -- \
  src/views/MarketView.test.ts \
  src/components/OffersList.test.ts \
  src/composables/useOffers.test.ts
```

Result: `52 tests OK`.

Frontend build:

```bash
npm run build
```

Result: passed. Existing bundle-size warnings remain non-blocking.

Diff hygiene:

```bash
git diff --check
```

Result: passed.

Staging validation:

```bash
scripts/deploy_staging.sh check
scripts/deploy_staging.sh deploy
scripts/deploy_staging.sh ps
scripts/deploy_staging.sh health
```

Result:

- Staging app container was `healthy`.
- `https://staging.362514.ir/api/config` returned:

```json
{"bot_username":"staging_bot_placeholder","frontend_url":"https://staging.362514.ir"}
```

## Promotion Safety Checks

- `origin/main` is an ancestor of `candidate/market-traded-history`, so the
  branch is structurally ready for a fast-forward style promotion if `main` has
  not moved.
- During TH7/TH8 execution, no production deploy command, production sync
  command, or production data mutation was run from this candidate branch.
- Staging deploy used the staging lifecycle script only.
- Runtime behavior changes were validated on staging before promotion review.

## Rollback Path

If the branch is merged but not yet deployed to production:

1. Revert the merge commit or reset `main` according to the repository's normal
   protected-branch policy.
2. Push the corrected `main`.
3. Leave production untouched.

If the branch is deployed to production and must be rolled back:

1. Identify the previous known-good `main` commit before the merge.
2. Revert the merge or create a hotfix rollback commit.
3. Run the normal production release flow only after explicit owner approval.
4. Confirm both public config endpoints after release.
5. Check active market, all-market history, buyer/seller/my filters, and
   Telegram channel offer buttons/tags.

Database rollback note:

- The branch adds a partial index only. Rolling back code can leave the index in
  place safely because it does not change table semantics.
- If an Alembic downgrade is explicitly required, use the revision downgrade for
  `f5b6c7d8e9a0` to drop `ix_trades_completed_offer_history`.

## Accepted Risks

1. The history state is derived at read time from `offers` and `trades`.

   This avoids a separate materialized state that could drift, but it means the
   history tag depends on both offer and completed trade sync convergence.

2. Out-of-order cross-server sync can briefly show an incomplete tag.

   The branch mitigates this by refreshing history on both terminal offer sync
   and completed trade sync. Final convergence is covered by focused tests.

3. Production data volume may differ from staging.

   The partial completed-trade index reduces query risk, but production latency
   should still be watched after deployment.

4. Telegram side effects remain foreign-owned.

   This is intentional. Iran WebApp changes must converge through sync; Iran
   must not directly edit Telegram channel posts.

## Recommendation

I recommend owner review and then explicit merge approval if the desired product
behavior is confirmed:

- Active offers remain the only interactive market cards.
- The all-market tab shows read-only two-day terminal history.
- Completed and partially traded-expired offers are visually distinct from pure
  expired offers.
- Buyer/seller/my tabs stay active-only.

Do not deploy to production until the owner explicitly requests it after merge.
