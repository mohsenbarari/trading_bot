# WebApp UI/UX Stage 4 - Dashboard Refactor

Date: 2026-07-07
Branch: `candidate/webapp-ui-ux-unification`

## Scope

Stage 4 refactors the first authenticated WebApp screen to use the canonical UI primitive family while preserving dashboard data behavior.

This stage does not change:

- `/api/auth/me` loading;
- `/api/trades/my` loading or perspective filtering;
- commodity lazy loading;
- project-user lazy loading/search/pagination;
- Telegram connect request behavior;
- market navigation behavior;
- notification/profile/logout route targets.

## Implemented

### New Primitives

Added `AppDisclosure` for controlled accordion/disclosure sections:

- controlled `open` prop;
- explicit `toggle` event;
- stable `aria-expanded`;
- stable `aria-controls`;
- optional caller-provided `titleId` and `panelId`;
- `leading` and `meta` slots.

Added `AppChip` for compact non-interactive labels that are not full status badges.

Both are exported from `frontend/src/components/ui/index.ts` and covered by `AppPrimitives.test.ts`.

### Dashboard Primitive Migration

`DashboardView.vue` now uses:

- `AppCard` for the header surface;
- `AppToast` for inactive/restricted account alerts;
- `AppStatusBadge` for dashboard status labels and market status;
- `AppButton` for refresh/search/load-more actions;
- `AppLoadingState`, `AppErrorState`, and `AppEmptyState` for dashboard state blocks;
- `AppDisclosure` for project-user and commodity collapsible sections;
- `AppCard` and `AppChip` for commodity alias cards/chips.

Existing lazy-load handlers remain unchanged:

- `toggleProjectUsersDirectory()`;
- `toggleAllowedCommodities()`;
- `loadProjectUsersDirectory()`;
- `loadAllowedCommodities()`.

### Version Display

The hardcoded dashboard footer `نسخه ۲.۵.۰` was removed because no reliable build/runtime version source exists in the current dashboard contract. Keeping a manual version string would continue visual and release drift.

## Accessibility Contract

The dashboard disclosure sections preserve the existing public IDs:

- `dashboard-project-users-title`;
- `dashboard-project-users-panel`;
- `dashboard-commodities-title`;
- `dashboard-commodities-panel`.

The toggle buttons keep stable `aria-expanded` and `aria-controls` through `AppDisclosure`.

## Validation

Executed locally before documenting:

- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts src/views/DashboardView.test.ts`
- `npm run guard:ui`
- `git diff --check`

All passed.

Executed after documentation:

- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts src/views/DashboardView.test.ts src/components/AppToasts.test.ts src/components/AppAuthenticatedShell.test.ts src/composables/useNotificationRuntime.test.ts`
- `npm run build`
- `npx playwright test e2e/non-messenger-visual-baseline.spec.ts --project=chromium --list`

The listed checks passed. The Playwright command only listed the visual baseline matrix; it did not execute browser screenshots.

## Screenshot Note

The roadmap requests mobile and desktop screenshot review. As in previous stages, local Playwright runtime execution depends on starting a dev server. In this managed sandbox, dev-server binding has previously failed with `EPERM`, so screenshot capture remains deferred unless a permitted runtime server is available.

## Deferred

- No Market/OffersList DOM refactor in Stage 4.
- No version replacement until a reliable build/runtime version source is introduced.
- No Dashboard data contract changes.
