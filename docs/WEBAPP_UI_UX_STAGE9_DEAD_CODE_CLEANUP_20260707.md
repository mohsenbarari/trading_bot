# WebApp UI/UX Stage 9 Dead Code Cleanup - 2026-07-07

## Scope

Stage 9 removes frontend components that are not mounted by the active router, not imported by active runtime views, and only referenced by their own tests or historical documentation.

No runtime route, backend API, Market offer flow, Telegram/Bot flow, Messenger internals, or production data path is changed by this stage.

## Active Router Evidence

`frontend/src/router/index.ts` mounts these active WebApp surfaces:

- `DashboardView.vue`
- `SetupPassword.vue`
- `LoginView.vue`
- `MarketView.vue`
- `OperationsView.vue`
- `CustomerWorkspaceView.vue`
- `AccountantWorkspaceView.vue`
- `AccountHubView.vue`
- `SettingsView.vue`
- `NotificationsView.vue`
- `MessengerView.vue`
- `PublicProfileView.vue`
- `ProfileView.vue`
- `AdminView.vue`
- `InviteLanding.vue`
- `WebRegister.vue`
- `ShareReceiveView.vue`

None of the removed Stage 9 components are imported by these router entries or their active runtime dependency tree.

## Removed Files

Removed as confirmed dead/static legacy components:

- `frontend/src/components/HomePage.vue`
- `frontend/src/components/PlaceholderView.vue`
- `frontend/src/components/UserSettings.vue`
- `frontend/src/components/MainMenu.vue`
- `frontend/src/components/MainMenu.test.ts`
- `frontend/src/components/HomeAnimation.vue`
- `frontend/src/components/HomeAnimation.test.ts`
- `frontend/src/components/StaticShellComponents.test.ts`

Removed as confirmed legacy/test-only trading component:

- `frontend/src/components/TradingView.vue`
- `frontend/src/components/TradingView.test.ts`

## TradingView Decision

`TradingView.vue` is not mounted by the active router. Historical project docs already note it as legacy/test-only:

- `docs/MARKET_SCHEDULE_ROADMAP.md` states that `TradingView.vue` is not the live Market route and current Web chatbox scope is `MarketView.vue`.
- Current active Market behavior lives in `frontend/src/views/MarketView.vue`, `frontend/src/components/OffersList.vue`, and dedicated Market/OffersList tests.
- Shared sorting logic remains in `frontend/src/composables/useTradingSort.ts` with its own tests.

The removal intentionally avoids touching `MarketView.vue`, `OffersList.vue`, `OfferPreviewModal.vue`, `useOffers`, trade APIs, websocket consumers, or customer visibility logic.

## Validation Plan

Required gates:

- `npm run test:unit:run -- src/views/MarketView.test.ts src/components/OffersList.test.ts src/composables/useTradingSort.test.ts`
- `npm run test:unit:run -- src/components/BottomNav.test.ts src/views/DashboardView.test.ts src/views/OperationsView.test.ts src/views/AccountHubView.test.ts`
- `npm run guard:ui`
- `npm run build`
- `git diff --check`

Optional browser gates when local webServer binding is available:

- `npx playwright test e2e/market-mutation-ux.spec.ts --project=chromium`
- `npx playwright test e2e/non-messenger-viewport.spec.ts --project=chromium`

## Local Validation Evidence

2026-07-07:

- Passed: `npm run test:unit:run -- src/views/MarketView.test.ts src/components/OffersList.test.ts src/composables/useTradingSort.test.ts` (`49` tests).
- Passed: `npm run test:unit:run -- src/components/BottomNav.test.ts src/views/DashboardView.test.ts src/views/OperationsView.test.ts src/views/AccountHubView.test.ts` (`29` tests).
- Passed: `npm run guard:ui`.
- Passed: `npm run build`.
- Passed: `git diff --check`.
- Reference scan after deletion found no remaining `TradingView`, `HomePage`, `MainMenu`, `PlaceholderView`, `UserSettings`, or `HomeAnimation` references under `frontend/src` or `frontend/e2e`.

## Rollback Plan

If a missed runtime import appears, restore only the deleted component/test files from the previous commit. Do not roll back Stage 0-8 primitive, Market, navigation, profile, account, admin, or operations changes.
