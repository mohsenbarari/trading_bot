# WebApp UI/UX Stage 2 Global Shell, Toast, Brand - 2026-07-07

## Scope

This file records Stage 2 of `docs/WEBAPP_UI_UX_UNIFICATION_ROADMAP_20260707.md`.

Branch:

- `candidate/webapp-ui-ux-unification`

Stage 2 changed only frontend UI/style surfaces. It did not change backend behavior, trade execution, offer mutation APIs, customer visibility policy, notifications delivery semantics, cross-server sync, or database code.

## Implemented Changes

### Global Shell Background

`frontend/src/App.vue` no longer hardcodes the app background gradient inline.

The shell now uses:

- `--ds-app-background`

Defined in:

- `frontend/src/assets/main.css`

### Toast Host Unification

`frontend/src/components/AppToasts.vue` now renders the live toast copy through the existing `AppToast` primitive visual language while preserving:

- click-to-open route behavior;
- close button behavior;
- swipe-to-dismiss behavior;
- notification icon rendering;
- chat/app notification routing from `useNotificationRuntime`.

`frontend/src/components/AppToasts.test.ts` now covers the host-to-primitive tone mapping.

Toast tone mapping:

- `success` -> `AppToast` success
- `warning` -> `AppToast` warning
- `error` -> `AppToast` danger
- `info` and `chat` -> `AppToast` info
- `system` -> `AppToast` neutral

### Telegram Brand Tokens

Telegram colors are now tokenized in `frontend/src/assets/main.css`:

- `--ds-telegram-400`
- `--ds-telegram-500`
- `--ds-telegram-600`
- `--ds-telegram-700`
- `--ds-telegram-border`
- `--ds-telegram-focus`
- `--ds-telegram-shadow`
- `--ds-telegram-shadow-strong`
- `--ds-telegram-gradient`

Applied to:

- `frontend/src/components/account/TelegramConnectPanel.vue`
- `frontend/src/views/InviteLanding.vue`
- `frontend/src/components/CreateInvitationView.vue`

### Trade and Market Semantic Colors

The Stage 0 hardcoded trade-side color guard now passes.

New semantic tokens include:

- `--ds-trade-buy-bg`
- `--ds-trade-buy-text`
- `--ds-trade-buy-shadow`
- `--ds-trade-sell-bg`
- `--ds-trade-sell-text`
- `--ds-trade-sell-shadow`
- `--ds-market-open-bg`
- `--ds-market-open-text`
- `--ds-market-open-border`
- `--ds-market-closed-bg`
- `--ds-market-closed-border`

Applied to:

- `frontend/src/views/DashboardView.vue`
- `frontend/src/views/MarketView.vue`
- `frontend/src/components/OffersList.vue`

`OffersList.vue` changes were CSS-value-only. No DOM shape, class names, selectors, trade action handlers, request flow, or mounted component boundaries were changed.

## Guard Refinement

`frontend/scripts/check-ui-ux-guards.mjs` now scans Vue `<style>` blocks for hardcoded trade-side colors instead of scanning the entire Vue file. This prevents unrelated template utility classes, such as the existing inline market error toast, from being incorrectly classified as trade-side CSS drift.

## Deferred Item

The roadmap listed "remove the inline OffersList error toast and route it through the central toast system."

This was not implemented in Stage 2 because the same roadmap's Market Protected Surface Rule says Stage 2 may touch `MarketView.vue` and `OffersList.vue` only as CSS-value-only changes.

The inline OffersList error toast remains unchanged and should be handled only after Stage 4.5 test-hook hardening or after explicit approval for a DOM/script-level Market surface change.

## Validation

Commands run:

```bash
npm run guard:ui
npm run test:unit:run -- src/components/AppToasts.test.ts src/components/ui/AppPrimitives.test.ts src/components/OffersList.test.ts src/views/DashboardView.test.ts src/views/MarketView.test.ts src/views/InviteLanding.test.ts src/components/CreateInvitationView.test.ts src/components/AppAuthenticatedShell.test.ts src/composables/useNotificationRuntime.test.ts
npm run build
npx playwright test e2e/non-messenger-visual-baseline.spec.ts --project=chromium --list
```

Results:

- `npm run guard:ui`: pass.
- Relevant unit/component tests: 9 files, 100 tests passed.
- `npm run build`: pass.
- Visual baseline listing: pass, 26 tests.

## Screenshot Evidence Status

Full screenshot execution is still pending because the current managed sandbox rejects the Playwright web server bind on `127.0.0.1:5173` with `EPERM`.

No claim of final visual approval is made until the baseline screenshots are run in an environment that can start the frontend dev/preview server.

## Remaining Risk

Stage 2 changes are visually observable because global shell, toast, Telegram connect, and semantic market status colors now use shared tokens. The unit/build guards passed, but route-by-route screenshot review should still happen before broader visual refactors.
