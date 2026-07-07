# WebApp UI/UX Stage 6 - Admin Surface Contract

Date: 2026-07-07
Branch: `candidate/webapp-ui-ux-unification`

## Purpose

Stage 6 standardizes Admin UI structure without changing management behavior. The Admin surface controls invitations, users, commodities, channels, admin messages, and system settings, so this stage must be visually conservative and behavior-preserving.

## Protected Behavior

- Admin route names and legacy query deep links must continue to work:
  - `/admin`
  - `/admin/invitations`
  - `/admin/channels`
  - `/admin/commodities`
  - `/admin/users`
  - `/admin/users/:id`
  - `/admin/messages`
  - `/admin/system`
  - legacy `?section=...` and `?section=user_profile&user_id=...`
- `SUPER_ADMIN` keeps access to all admin tools.
- `MIDDLE_MANAGER` keeps access only to invitations and users.
- Other currently allowed limited admin roles keep only their existing allowed tools.
- Direct route access to forbidden admin sections must still fall back to the admin menu.
- User profile route loading must still use `/api/users/{id}` through the existing `apiFetch` path.
- Child components keep their existing API, permissions, API calls, and emitted events:
  - `AdminPanel.vue`
  - `CreateInvitationView.vue`
  - `CreateChannelView.vue`
  - `CommodityManager.vue`
  - `UserManager.vue`
  - `AdminMessagesView.vue`
  - `UserProfile.vue`
  - `TradingSettings.vue`

## Stage 6 Safe Slice

The first Stage 6 implementation slice may refactor only the `AdminView.vue` subview shell:

- Replace bespoke subview header/card wrappers with existing shared primitives where possible.
- Preserve the visible title and description from `sectionMetaByKey`.
- Preserve the return action behavior: `handleNavigate('admin_panel')`.
- Preserve back-stack behavior, route pushes/replaces, and route profile loading.
- Keep the existing `.admin-subview-return` selector usable for current tests and styling.
- Do not alter child component markup or internal workflows in this slice.

## Validation Gates

Focused gates for the first Stage 6 slice:

- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/ui/AppPrimitives.test.ts`
- `npm run guard:ui`
- `npm run build`
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`
- `git diff --check`

Runtime browser execution remains a staging concern if the sandbox cannot bind the local Vite server.

## Rollback Plan

If an Admin regression appears:

1. Revert the Stage 6 Admin shell commit.
2. Confirm `AdminView.vue` no longer imports the Stage 6-added primitive(s).
3. Re-run `AdminView.test.ts` and `AdminPanel.test.ts`.
4. Do not continue into child Admin forms/tables/modals until route and permission behavior is clean.

## Stage 6 Safe-Slice Evidence

- `AdminView.vue` subviews now use `AppSectionCard` for the shared section/card shell.
- The subview return action now uses `AppIconButton` while preserving `.admin-subview-return`, the visible icon, and `handleNavigate('admin_panel')`.
- `sectionMetaByKey` remains the single title/description source for subview headers.
- Child admin components remain unchanged in this slice.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 28 tests after the Admin shell primitive slice.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/CreateChannelView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 71 tests.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Create Invitation Form Slice Evidence

- `CreateInvitationView.vue` invite form fields now use `AppFormField`, `AppInput`, and `AppSelect`.
- The primary submit and reset actions now use `AppButton` while preserving `button[type="submit"]`, `button.secondary`, loading/disabled behavior, and the existing reset handler.
- Mobile normalization, invite API payloads, role allow-list behavior, generated Telegram/Web links, copy fallbacks, pending invitation list, and delete behavior remain unchanged.
- `npm run test:unit:run -- src/components/CreateInvitationView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 40 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 43 tests.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.
