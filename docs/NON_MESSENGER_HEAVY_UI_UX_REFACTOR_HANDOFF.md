# Non-Messenger Heavy UI/UX Refactor Handoff

Date: 2026-06-14

## Scope

This handoff summarizes the H0-H10 heavy UI/UX refactor for all non-messenger surfaces. Messenger internals were intentionally excluded except for smoke validation that the shell route still mounts.

Primary goal:
- Move heavy non-messenger workflows from scattered screens and large owner modals toward route-level workspaces.
- Preserve current business behavior, permissions, realtime trading behavior, and legacy deep links.
- Improve scanability, keyboard access, focus states, route stability, and regression coverage before release.

## New Route-Level Workspaces

| Area | Current route | Main files | Notes |
| --- | --- | --- | --- |
| Dashboard | `/` | `frontend/src/views/DashboardView.vue` | Task-focused account/market/today overview, warnings, shortcuts, today's trades. |
| Market | `/market` | `frontend/src/views/MarketView.vue`, `frontend/src/components/OffersList.vue` | Shell/status/filter accessibility improved. Trading create/parse/confirm/cancel/trade logic preserved. |
| Operations hub | `/operations` | `frontend/src/views/OperationsView.vue` | Route-neutral hub using workspace primitives. |
| Customer workspace | `/operations/customers`, `/operations/customers/:relationId` | `frontend/src/views/CustomerWorkspaceView.vue`, `frontend/src/components/OwnerCustomerManagerModal.vue` | Customer add/pending/manage/detail/trades/stats/sessions/danger now has a route-level surface. |
| Accountant workspace | `/operations/accountants`, `/operations/accountants/:relationId` | `frontend/src/views/AccountantWorkspaceView.vue`, `frontend/src/components/OwnerAccountantManagerModal.vue` | Accountant add/pending/manage/detail/sessions/danger now has a route-level surface. |
| Account hub | `/account` | `frontend/src/views/AccountHubView.vue` | Profile/settings/security/storage/notifications grouped by task. |
| Account security | `/account/security` | `frontend/src/views/SettingsView.vue` | Direct route into sessions/security section. Accountant restriction preserved. |
| Account storage | `/account/storage` | `frontend/src/views/SettingsView.vue` | Direct route into storage/cache section. |
| Account notifications | `/account/notifications` | `frontend/src/views/NotificationsView.vue` | Direct route into notification list with read-state filters. |
| Admin sections | `/admin/*` | `frontend/src/views/AdminView.vue`, admin child components | Admin route names now drive section state while old query handoffs still work. |

## Compatibility Still Present

Keep these until after the first release has had enough real traffic and rollback confidence:

- `/settings` still mounts `SettingsView.vue` and accepts legacy `?section=sessions` / `?section=storage`.
- `/notifications` still mounts `NotificationsView.vue`; `/account/notifications` is the new account entry.
- `/admin?section=...` and `/admin?section=user_profile&user_id=...` still map into the route-driven admin workspace.
- `OwnerCustomerManagerModal.vue` and `OwnerAccountantManagerModal.vue` still support `presentation="modal"` as a compatibility mode, while route workspaces render them with `presentation="workspace"`.
- Profile/public-profile owner actions now route to `/operations/customers` and `/operations/accountants`, but the large manager components remain shared until a later split into smaller domain components.

Post-release removal path:
- First remove legacy profile/query entry points only after analytics/manual checks confirm users are using workspace routes.
- Then split customer/accountant manager components into smaller route-native sections.
- Finally remove `presentation="modal"` branches from owner manager components.

## Key Files Changed Across H Stages

Shared primitives:
- `frontend/src/components/workspace/WorkspaceShell.vue`
- `frontend/src/components/workspace/WorkspaceSection.vue`
- `frontend/src/components/workspace/WorkspaceActionTile.vue`
- `frontend/src/components/workspace/WorkspaceNotice.vue`
- `frontend/src/components/workspace/WorkspaceStatTile.vue`
- `frontend/src/components/workspace/WorkspaceDangerZone.vue`
- `frontend/src/assets/main.css`

Route and shell files:
- `frontend/src/router/index.ts`
- `frontend/src/components/BottomNav.vue`
- `frontend/src/views/OperationsView.vue`
- `frontend/src/views/CustomerWorkspaceView.vue`
- `frontend/src/views/AccountantWorkspaceView.vue`
- `frontend/src/views/AccountHubView.vue`
- `frontend/src/views/AdminView.vue`
- `frontend/src/views/SettingsView.vue`
- `frontend/src/views/NotificationsView.vue`
- `frontend/src/views/ProfileView.vue`
- `frontend/src/views/PublicProfileView.vue`
- `frontend/src/views/DashboardView.vue`
- `frontend/src/views/MarketView.vue`

Compatibility-heavy components:
- `frontend/src/components/OwnerCustomerManagerModal.vue`
- `frontend/src/components/OwnerAccountantManagerModal.vue`
- `frontend/src/components/PublicProfile.vue`
- `frontend/src/components/AdminPanel.vue`
- `frontend/src/components/TradingSettings.vue`

## Validation Summary

H9 final focused regression:

```bash
cd frontend
npm run test:unit:run -- DashboardView.test.ts MarketView.test.ts AccountHubView.test.ts SettingsView.test.ts NotificationsView.test.ts OperationsView.test.ts CustomerWorkspaceView.test.ts AccountantWorkspaceView.test.ts AdminView.test.ts PublicProfileView.test.ts MessengerView.test.ts router/index.test.ts BottomNav.test.ts
```

Result:
- `13` test files passed.
- `107/107` tests passed.

Production build:

```bash
cd frontend
npm run build
```

Result:
- Passed.
- Existing chunk-size warnings remain for large app chunks; no H-stage blocker.

E2E smoke:

```bash
cd frontend
npx playwright test auth.spec.ts --project=chromium --reporter=line
```

Result:
- `3/3` tests passed.
- In the Codex sandbox this required elevated execution because the sandbox blocked binding the local Vite dev server on `127.0.0.1:5173`.

## Release Debt

Not blockers for the first release:

- Owner customer/accountant manager components remain large compatibility components. They are now route-mounted, but still need a later component split.
- Admin route sections are route-driven, but `AdminView.vue` still acts as the section orchestrator.
- `SettingsView.vue` serves both legacy `/settings` and new `/account/security|storage` routes.
- Full visual QA should still be repeated on real mobile devices after production deploy.
- Large bundle warnings remain unrelated to this H-stage and should be handled separately with code-splitting policy.

## Production Deploy

H10 production deploy should be recorded in `docs/NON_MESSENGER_HEAVY_UI_UX_REFACTOR_PLAN.md` and `.github/copilot-instructions.md` after the final `make production-release` run completes.
