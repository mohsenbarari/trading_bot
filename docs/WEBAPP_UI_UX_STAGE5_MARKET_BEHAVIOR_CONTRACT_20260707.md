# WebApp UI/UX Stage 5 - Market Behavior Contract

Date: 2026-07-07
Branch: `candidate/webapp-ui-ux-unification`

## Purpose

This contract records the current Market and OffersList behavior before Stage 5 changes the card structure. Stage 5 may improve the visual/component structure, but it must not change trading behavior, server requests, authorization visibility, offer state semantics, or the stabilized Stage 4.5 test selector contract.

## Protected Behavior

### Offer Creation

- `MarketView.vue` keeps the text-only market composer for allowed users.
- The text composer sends the input to `/api/offers/parse`.
- Parsed offers are shown through `OfferPreviewModal.vue`.
- The preview confirmation sends the final offer to `/api/offers/`.
- Warning flow remains a second confirmation inside the same preview modal.
- The composer is disabled when the market is closed or while submitting.

### Whole Offer Request

- `OffersList.vue` computes whole-offer buttons from the current remaining quantity when an offer is wholesale or has no valid lot sizes.
- Trade execution sends `POST /api/trades/` with `offer_id`, `quantity`, and an idempotency key.
- Network retry safety remains unchanged: retryable network errors keep the idempotency key; non-retryable server errors clear it.

### Lot Offer Request

- Retail lot buttons are derived from remaining quantity plus valid lot sizes, deduplicated and sorted ascending.
- If a lot is no longer available and the backend returns `TRADE_LOT_UNAVAILABLE`, the existing `TradeLotSuggestionAlert.vue` is shown.
- The suggestion modal must not show owner identity and must refresh or close when the source offer changes.

### Two-Tap Confirmation

- First tap on a trade button only sets the pending confirmation state for `offerId:amount`.
- The pending state expires after 3 seconds.
- Second tap within that window executes the trade.
- The Stage 4.5 pending selector must stay valid: `[data-test="trade-action-button"][data-state="pending"]`.

### Expired And Traded States

- Read-only history offers stay visible even when expired.
- `history_state="traded"` takes precedence over expired styling.
- Traded history rows show the original total quantity; partial traded rows show the traded quantity in `history-stamp`.
- Expired rows must not expose trade or cancel controls.
- Traded rows must not expose trade or cancel controls.
- `data-test="history-stamp"` must be preserved.

### Owner, Customer, And Accountant Visibility

- Own offers show the expire/cancel control instead of trade buttons.
- Non-own active offers show trade buttons when remaining quantity is positive.
- `viewer_effective_price` is the displayed price when present.
- Customer context badges are only shown when `offer.customer_badge_visible` is true.
- Customer management names use the existing `CustomerNameWithBadge` path.
- Tier badges preserve the existing `tier1` / `tier2` labels.

### Telegram/WebApp Publication Assumptions

- WebApp publishes offers locally and syncs them to the Telegram/bot side through the existing backend/sync path.
- Telegram-origin offers can appear in the WebApp list with the same active/read-only/traded/expired semantics.
- Stage 5 must not change publication, sync, channel-message, or trade-execution side effects.

## Stabilized Selector Contract

Stage 5 must preserve every selector documented in `docs/WEBAPP_UI_UX_STAGE4_5_MARKET_TEST_HOOKS_20260707.md`, including:

- `[data-test="offer-card"]`
- `[data-test="offer-price"]`
- `[data-test="trade-action-button"]`
- `[data-test="trade-action-button"][data-state="pending"]`
- `[data-test="history-stamp"]`
- `[data-test="customer-context-row"]`
- `[data-test="offer-preview-card"]`
- `[data-test="recent-offers-toggle"]`
- `[data-test="trade-suggestion-lot-button"]`

## Market Navigation Review

- The current bottom market action bar remains the safest model for this stage.
- No floating action button migration is included in Stage 5.
- The recent-offer dropdown keeps its existing Teleport-to-body behavior and z-index assertion.
- The current keyboard path remains textarea plus explicit send button; no shortcut or focus-order change is included.
- Mobile safe-area and bottom-navigation overlap are covered by the existing mutation UX test for the recent-offer toggle/dropdown.

## Rollback Plan

If any market regression is detected:

1. Revert the Stage 5 commit that introduces the Market card refactor.
2. Confirm `OffersList.vue` no longer imports `AppOfferCard`.
3. Re-run the focused unit tests for `OffersList.vue`, `MarketView.vue`, and `TradeLotSuggestionAlert.vue`.
4. Re-run the Playwright market selector discovery/list command where the environment allows it.
5. Do not continue with later Market DOM work until the regression cause is isolated.

## Runtime Review Requirement

The current sandbox cannot run browser E2E runtime because Vite cannot bind `127.0.0.1:5173` inside the sandbox. Before production promotion, a real staging/runtime review must confirm:

- active offer card clarity;
- expired offer clarity;
- traded and partially traded history clarity;
- whole-offer and lot-offer request flow;
- two-tap confirmation state;
- owner/customer/accountant visibility cases;
- no overlap between trade controls, recent-offer controls, and bottom navigation.

## Stage 5 Safe-Slice Evidence

- `AppOfferCard` now owns the offer-card wrapper state classes and `data-test="offer-card"` while preserving the existing inner slot content.
- `AppTradeActionButton` now owns the root trade action button class, side/pending/busy classes, `data-test="trade-action-button"`, and `data-state`.
- `AppOfferSideBadge` now owns the root `role-badge` buy/sell badge and label.
- `AppOfferQuantityBadge` now owns the root `quantity-badge` quantity display and exposes `data-test="offer-quantity"`.
- `AppOfferHistoryStamp` now owns the root `history-ribbon` rendering and preserves `data-test="history-stamp"`.
- `AppOfferPrice` now owns the root `price` display and preserves `data-test="offer-price"`.
- `AppOfferCustomerContext` now owns the customer context row, management-name badge path, fallback badge, tier label, and preserves `data-test="customer-context-row"`.
- `OffersList.vue` keeps the existing trade handlers, cancel handler, idempotency key management, lot suggestion state, price selection, customer-context visibility decision, and history stamp label/state decisions.
- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts src/components/OffersList.test.ts src/views/MarketView.test.ts src/components/TradeLotSuggestionAlert.test.ts`: passed, 61 tests.
- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts src/components/OffersList.test.ts src/views/MarketView.test.ts`: passed, 56 tests after the badge primitive slice.
- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts src/components/OffersList.test.ts src/views/MarketView.test.ts`: passed, 56 tests after the offer-detail primitive slice.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/market-offers.spec.ts e2e/lot-suggestion.spec.ts e2e/market-mutation-ux.spec.ts e2e/market-schedule.spec.ts --project=chromium --list`: passed, 13 tests discovered.
- `npx playwright test e2e/trade-history-accountant.spec.ts --project=chromium --list`: blocked by sandbox Docker socket access (`spawnSync docker EPERM`) during test import.
