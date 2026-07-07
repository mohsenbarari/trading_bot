# WebApp UI/UX Stage 8 Navigation and PWA Contract - 2026-07-07

## Scope

Stage 8 resolves the remaining navigation/PWA decisions from `docs/WEBAPP_UI_UX_UNIFICATION_ROADMAP_20260707.md`.

This stage must stay outside offer publishing, trade execution, offer-card rendering, request confirmation, and notification delivery logic. Production and staging deploys remain blocked unless explicitly requested.

## Evidence From Current Code

- `frontend/src/components/BottomNav.vue` renders the normal bottom navigation on most authenticated routes.
- The same component switches to a compact FAB menu on `market` and `messenger` routes.
- `frontend/src/views/MarketView.vue` owns a fixed `.market-action-bar` at the bottom for offer text input, recent-offers access, and submit.
- Existing Playwright coverage in `frontend/e2e/market-mutation-ux.spec.ts` already protects the market recent-offers toggle and dropdown from FAB overlap regressions.
- `frontend/e2e/non-messenger-viewport.spec.ts` covers bottom chrome bounds and mobile viewport clearance across non-messenger routes.
- `frontend/src/components/PWAInstallOverlay.vue` currently positions the install prompt with a fixed `bottom: 100px`, which is less explicit than the shared bottom-nav/safe-area variables.
- `frontend/src/views/ShareReceiveView.vue` delegates the main picker to Messenger's `ChatForwardModal`; Stage 8 must not refactor Messenger internals.

## Decisions

1. Market keeps the existing FAB navigation model for this roadmap.
   - Reason: `MarketView.vue` already has a behavior-critical fixed action bar. Adding the full bottom navigation would compete with the offer composer and increase the risk on the protected Market surface.
2. Messenger keeps the existing FAB navigation model.
   - Reason: chat routes have their own full-screen layout and unread/mention badge behavior. Stage 8 should not change Messenger internals.
3. FAB remains draggable, but Market route drag bounds must reserve the bottom composer area.
   - Reason: existing tests and user behavior already cover draggable FAB. The low-risk fix is to prevent a dragged or persisted FAB from landing on the market offer composer.
4. Safe-area policy:
   - normal routes: bottom navigation owns `--ds-bottom-nav-height` and `--ds-safe-area-bottom`;
   - Market route: `.market-action-bar` owns the bottom edge and FAB must stay above the reserved composer clearance;
   - PWA install overlay must sit above shared bottom chrome using the same design-system safe-area variables;
   - Share Receive stays full-screen and must not introduce separate bottom chrome.
5. Desktop width policy remains unchanged in this stage.
   - Reason: operational screens already use their own constrained workspace layouts. Broad desktop widening would be visual/product work, not a low-risk navigation fix.

## Implementation Rules

- Do not replace the Market FAB with bottom navigation in this stage.
- Do not change `MarketView.vue` offer submission, parse, preview, trade request, recent-offer API, or list refresh behavior.
- Do not touch Messenger route internals unless a shared navigation change directly requires a smoke check.
- Preserve the existing `fab_position` localStorage key for backward compatibility.
- Clamp existing persisted FAB coordinates at runtime instead of clearing user storage.
- If `BottomNav.vue` changes, run both normal-route and market/messenger FAB unit coverage.

## Validation Plan

Required local gates:

- `npm run test:unit:run -- src/components/BottomNav.test.ts src/components/PWAInstallOverlay.test.ts src/components/AppAuthenticatedShell.test.ts`
- `npm run test:unit:run -- src/views/MarketView.test.ts src/components/BottomNav.test.ts`
- `npm run guard:ui`
- `npm run build`
- `git diff --check`

Runtime/browser gates when available:

- `npx playwright test e2e/market-mutation-ux.spec.ts --project=chromium`
- `npx playwright test e2e/non-messenger-viewport.spec.ts --project=chromium`
- If `BottomNav.vue` behavior changes, run a Messenger smoke path that verifies FAB menu, `/chat`, unread badge, and mention badge behavior.

Current sandbox note: Playwright browser execution may be blocked locally by Docker/browser server restrictions. If blocked, record the exact blocker and leave staging/runtime verification for the next deploy validation.

## Local Validation Evidence

2026-07-07T14:58:45Z:

- Passed: `npm run test:unit:run -- src/components/BottomNav.test.ts src/components/PWAInstallOverlay.test.ts src/components/AppAuthenticatedShell.test.ts` (`18` tests).
- Passed: `npm run test:unit:run -- src/views/MarketView.test.ts src/components/BottomNav.test.ts` (`41` tests).
- Passed: `npm run test:unit:run -- src/views/ShareReceiveView.test.ts src/components/PWAInstallOverlay.test.ts src/components/BottomNav.test.ts` (`24` tests).
- Passed: `npm run guard:ui`.
- Passed: `npm run build`.
- Passed: `git diff --check`.
- Passed discovery: `npx playwright test e2e/market-mutation-ux.spec.ts e2e/non-messenger-viewport.spec.ts --project=chromium --list` found `10` Chromium tests.
- Blocked actual browser execution: `npx playwright test e2e/market-mutation-ux.spec.ts e2e/non-messenger-viewport.spec.ts --project=chromium` failed before tests because Playwright's configured Vite webServer could not bind `127.0.0.1:5173` in the local sandbox (`listen EPERM`). The required escalation for the same command was rejected by environment policy, so browser execution remains a staging/runtime validation gate.

## Rollback Plan

Rollback only the Stage 8 navigation/PWA diff:

- `frontend/src/components/BottomNav.vue`
- `frontend/src/components/BottomNav.test.ts`
- `frontend/src/components/PWAInstallOverlay.vue`
- `frontend/src/components/PWAInstallOverlay.test.ts`
- Stage 8 documentation notes

This rollback must not touch Stage 0-7 primitive, Market offer-card, Profile, Account, Admin, or Operations changes.
