# WebApp UI/UX Stage 4.5 - Market Test Hook Hardening

Date: 2026-07-07
Branch: `candidate/webapp-ui-ux-unification`

## Purpose

Stage 4.5 stabilizes the Market and OffersList E2E selector contract before any DOM-level visual refactor in Stage 5. This stage intentionally adds invisible test hooks only. It must not change offer creation, trade execution, customer visibility, price display, realtime behavior, or any market business rule.

## Guardrails

- Keep existing CSS classes and visual styling intact.
- Add only stable attributes such as `data-test` and `data-state`.
- Do not modify API calls, composables, state transitions, or event handlers.
- Do not weaken, delete, skip, or loosen market/trade assertions.
- Preserve `data-test="history-stamp"`.
- Include every E2E file that depends on the same Market selector contract, even if the original Stage 4.5 list did not name it explicitly.

## Stable Test Contract

| Surface | Stable selector |
|---|---|
| Offer card wrapper | `[data-test="offer-card"]` |
| Offer price | `[data-test="offer-price"]` |
| Trade action button | `[data-test="trade-action-button"]` |
| Pending trade action | `[data-test="trade-action-button"][data-state="pending"]` |
| Customer context row | `[data-test="customer-context-row"]` |
| Customer name badge | `[data-test="customer-context-name-badge"]` |
| Customer fallback badge | `[data-test="customer-context-badge"]` |
| Customer tier badge | `[data-test="customer-context-tier"]` |
| History stamp | `[data-test="history-stamp"]` |
| Market text offer input | `[data-test="market-text-offer-input"]` |
| Market send button | `[data-test="market-send-button"]` |
| Offer preview card | `[data-test="offer-preview-card"]` |
| Offer preview confirm | `[data-test="offer-preview-confirm"]` |
| Offer preview warning | `[data-test="offer-preview-warning"]` |
| Offer preview error | `[data-test="offer-preview-error"]` |
| Offer preview close | `[data-test="offer-preview-close"]` |
| Recent offers toggle | `[data-test="recent-offers-toggle"]` |
| Recent offers dropdown | `[data-test="recent-offers-dropdown"]` |
| Recent offer item | `[data-test="recent-offer-item"]` |
| Lot suggestion button | `[data-test="trade-suggestion-lot-button"]` |
| Pending lot suggestion | `[data-test="trade-suggestion-lot-button"][data-state="pending"]` |

## Files Updated

- `frontend/src/components/OffersList.vue`
- `frontend/src/views/MarketView.vue`
- `frontend/src/components/OfferPreviewModal.vue`
- `frontend/src/components/TradeLotSuggestionAlert.vue`
- `frontend/e2e/market-offers.spec.ts`
- `frontend/e2e/lot-suggestion.spec.ts`
- `frontend/e2e/market-mutation-ux.spec.ts`
- `frontend/e2e/trade-history-accountant.spec.ts`
- `frontend/e2e/market-schedule.spec.ts`

## E2E Migration Scope

The following brittle CSS selectors were removed from the migrated E2E files:

- `.offer-card-wrap`
- `.trade-btn`
- `.price`
- `.text-offer-input`
- `.send-btn`
- `.offer-preview-card`
- `.offer-preview-confirm`
- `.offer-preview-warning`
- `.offer-preview-error`
- `.offer-preview-close`
- `.recent-offers-toggle`
- `.recent-offers-dropdown`
- `.recent-offer-item`
- `.trade-suggestion-lot-btn`
- `.customer-context-row`

`market-schedule.spec.ts` was included because it uses the same offer input and preview modal selector contract. Leaving it on CSS selectors would make Stage 5 partially unsafe.

## Validation Checklist

- `rg` confirms the migrated E2E files no longer use the brittle CSS selectors listed above.
- TypeScript/build validation must pass after the selector migration.
- The market E2E subset should be listed or run before Stage 5 starts:
  - `frontend/e2e/market-offers.spec.ts`
  - `frontend/e2e/lot-suggestion.spec.ts`
  - `frontend/e2e/market-mutation-ux.spec.ts`
  - `frontend/e2e/trade-history-accountant.spec.ts`
  - `frontend/e2e/market-schedule.spec.ts`

## Validation Evidence

- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npm run test:unit:run -- src/components/OffersList.test.ts src/components/TradeLotSuggestionAlert.test.ts src/views/MarketView.test.ts`: passed, 52 tests.
- `npx playwright test e2e/market-offers.spec.ts e2e/lot-suggestion.spec.ts e2e/market-mutation-ux.spec.ts e2e/market-schedule.spec.ts --project=chromium --list`: passed, 13 tests discovered.
- `frontend/e2e/trade-history-accountant.spec.ts` Playwright discovery could not run inside the sandbox because the file resolves the app container through Docker during import and Docker socket access is blocked.
- Runtime Playwright execution could not run inside the sandbox because Vite cannot bind `127.0.0.1:5173` (`listen EPERM`). Unsandboxed execution was requested and rejected by environment policy.

## Stage 5 Handoff

Stage 5 may refactor Market/OffersList DOM and primitives only if it preserves the stable selectors above. Any selector removal or rename must be treated as a breaking change and must update this document, the roadmap, and all affected tests in the same commit.
