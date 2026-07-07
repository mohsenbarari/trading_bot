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

## Create Invitation Action Button Slice Evidence

- `CreateInvitationView.vue` link-copy, pending-refresh, pending-copy, and pending-delete actions now use `AppButton`.
- Existing button classes remain in place: `.copy-btn`, `.copy-btn.web`, `.pending-refresh-btn`, `.pending-copy-btn`, and `.delete-pending-btn`.
- Clipboard success/failure fallbacks, refresh loading, delete confirmation, and pending invitation removal behavior remain unchanged.
- `npm run test:unit:run -- src/components/CreateInvitationView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 40 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 43 tests.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Commodity Manager Icon Button Slice Evidence

- `CommodityManager.vue` already used the shared form/list/card primitives for its primary flows.
- The remaining raw alias-management icon controls now use `AppIconButton` while preserving `.commodity-back-control`, `.commodity-icon-control.edit`, and `.commodity-icon-control.delete`.
- Commodity list loading, alias management, locked Imam restrictions, add/edit/delete commodity flows, add/edit/delete alias flows, and validation/error behavior remain unchanged.
- `npm run test:unit:run -- src/components/CommodityManager.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 34 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 52 tests.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## User Manager Search State Slice Evidence

- `UserManager.vue` search toggle and search-submit controls now use `AppButton`.
- The user search field now uses `AppInput`.
- The fetch-failure state now uses `AppErrorState`; the no-results state now uses `AppEmptyState`.
- Existing selectors remain in place for tests and styling: `.search-toggle-btn`, `.user-search-input`, `.search-submit-btn`, `.ds-message.danger`, and `.no-results`.
- User list rendering, customer/accountant relation badges, display-name policy, `/api/users/` and `/api/users/?search=...` fetch behavior, search trimming/encoding, and `navigate('user_profile', user)` are unchanged.
- This slice intentionally does not change user-list grouping, customer/accountant visibility, profile navigation, user deletion, role management, or any backend route.
- `npm run test:unit:run -- src/components/UserManager.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 30 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 57 tests.
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

## Create Invitation Pending State Slice Evidence

- `CreateInvitationView.vue` pending-invitation loading, empty, and error states now use `AppLoadingState`, `AppEmptyState`, and `AppErrorState`.
- Existing pending-state selectors remain in place: `.pending-error`, `.pending-state`, and `.pending-state.empty`.
- Pending invitation row structure, readonly Web registration link, copy behavior, delete confirmation, delete API call, refresh API call, and generated invite behavior are unchanged.
- `npm run test:unit:run -- src/components/CreateInvitationView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 41 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 58 tests.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Channel Manager Search Input Slice Evidence

- `CreateChannelView.vue` member, admin, and add-member search fields now use `AppInput`.
- Existing `.search-input` selectors remain in place for styling and tests.
- Search `v-model` bindings, disabled behavior for select-all member invites, candidate/member/admin filtering, channel membership mutations, profile routing, and channel create/update/delete flows are unchanged.
- `npm run test:unit:run -- src/components/CreateChannelView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 33 tests. The existing jsdom navigation warning remains non-fatal.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 66 tests. The existing jsdom navigation warning remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Channel Manager Action Button Slice Evidence

- `CreateChannelView.vue` member profile/remove, admin demote, promotable-member promote, and add-selected-members controls now use `AppButton`.
- Existing action selectors remain in place: `.channel-member-action`, `.channel-member-action--danger`, `.channel-member-action--primary`, and `.primary-chip`.
- Click handlers, `.stop` behavior, disabled conditions, selection count behavior, membership mutation APIs, profile routing, and channel create/update/delete flows are unchanged.
- `npm run test:unit:run -- src/components/CreateChannelView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 33 tests. The existing jsdom navigation warning remains non-fatal.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 66 tests. The existing jsdom navigation warning remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Admin Messages Primary Button Slice Evidence

- `AdminMessagesView.vue` message-mode tabs, clear-market-pin action, market publish action, and chat broadcast publish action now use `AppButton`.
- Existing selectors and attributes remain in place: `.message-mode-button`, `.primary-action`, `.secondary-action`, `data-test="message-mode-market"`, `data-test="message-mode-chat"`, and `data-test="clear-market-pin"`.
- Tab ARIA attributes, keyboard navigation, publish API calls, clear-pin API call, loading/disabled conditions, and history reuse behavior are unchanged.
- `npm run test:unit:run -- src/components/AdminMessagesView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 29 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 70 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Trading Settings Footer Button Slice Evidence

- `TradingSettings.vue` footer save and reset controls now use `AppButton`.
- Existing selectors remain in place: `.settings-button.settings-button--primary.footer-control` and `.settings-button.settings-button--danger.footer-control`.
- Save/reset click handlers, confirmation behavior, disabled state, payload creation, schedule fields, calendar override flows, and market-state refresh behavior are unchanged.
- `npm run test:unit:run -- src/components/TradingSettings.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 32 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 77 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Trading Settings Calendar Override Button Slice Evidence

- `TradingSettings.vue` calendar override save, cancel, edit, and delete controls now use `AppButton`.
- Existing selectors remain in place: `data-testid="override-save"`, `data-testid="override-cancel"`, `data-testid="override-edit-*"`, `data-testid="override-delete-*"`, `.settings-button.settings-button--primary`, `.settings-button.settings-button--secondary`, `.mini-footer-control`, and `.mini-footer-control.danger`.
- Override create/edit/delete handlers, confirmation, disabled/loading state, payload shape, JalaliDatePicker flow, schedule settings, footer save/reset, and market-state refresh behavior are unchanged.
- `npm run test:unit:run -- src/components/TradingSettings.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 32 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 77 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Create Invitation Readonly Link Input Slice Evidence

- `CreateInvitationView.vue` generated Telegram link, generated Web link, and pending invitation Web link inputs now use `AppInput`.
- Existing DOM selectors remain usable because `AppInput` renders a root `<input>`: `.success-box input[readonly]`, `.pending-link-row input[readonly]`, `.copy-container input[type="text"]`, and `.pending-link-row input[type="text"]`.
- Link values, local link conversion, readonly behavior, click-to-copy handlers, clipboard fallback behavior, pending invitation refresh/delete behavior, and generated invite API payloads are unchanged.
- `npm run test:unit:run -- src/components/CreateInvitationView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 41 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 77 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Admin Messages Secondary Control Slice Evidence

- `AdminMessagesView.vue` market pin expand, market history toggle, market history edit, broadcast composer textarea, and broadcast history reuse controls now use shared primitives.
- `AppButton` is used for the ghost-link text actions; scoped `.ghost-link` overrides keep the legacy compact text-link sizing.
- `AppIconButton` is used for the market history toggle and edit icon controls while preserving `data-test="market-history-toggle"`, `data-test="market-history-edit-*"`, `aria-expanded`, dynamic labels, and click handlers.
- `AppTextarea` is used only for the broadcast composer textarea. The market composer textarea remains native in this slice because it is directly referenced for scroll/focus after editing a market history item.
- Market pin expansion, market history accordion, edit-to-composer focus behavior, market publish payloads, chat broadcast payloads, selected target groups, history reuse, and clear-pin behavior are unchanged.
- `npm run test:unit:run -- src/components/AdminMessagesView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 29 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 77 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Admin Panel Accordion Toggle Slice Evidence

- `AdminPanel.vue` section accordion toggles now use `AppButton`.
- Existing IDs, selectors, and accessibility attributes remain in place: `#admin-*-header`, `.admin-accordion-toggle`, `aria-expanded`, `aria-controls`, and the region `aria-labelledby` links.
- Scoped `.admin-accordion-toggle` overrides keep the compact header-control sizing instead of adopting the full button height.
- Section open/close behavior, visible action counts, middle-manager action restrictions, super-admin action set, action navigation emits, and all permission decisions are unchanged.
- `npm run test:unit:run -- src/components/AdminPanel.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 28 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 77 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Admin Messages Market Composer Textarea Slice Evidence

- `AppTextarea` now exposes `focus()` and `scrollIntoView()` methods that delegate to its root `<textarea>`.
- `AdminMessagesView.vue` market composer textarea now uses `AppTextarea`.
- Existing selectors and attributes remain in place: `.message-textarea`, `data-test="market-composer-input"`, `rows="7"`, and the placeholder text.
- The edit-history path still writes the selected market message into the composer, collapses the history list, scrolls the composer into view, and focuses it.
- Market publish payloads, chat broadcast payloads, selected target groups, history reuse, clear-pin behavior, and the previous broadcast textarea primitive behavior are unchanged.
- `npm run test:unit:run -- src/components/AdminMessagesView.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 29 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 77 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.

## Trading Settings Accordion Header Slice Evidence

- `TradingSettings.vue` settings-section accordion headers now use `AppButton`.
- Existing IDs, selectors, and accessibility attributes remain in place: `trading-settings-*-header`, `.settings-section__header`, `aria-expanded`, `aria-controls`, and region `aria-labelledby` links.
- Scoped header styles keep the full-width right-aligned header layout and make the shared button label carry the existing icon/title/chevron row.
- Section open/close state, visible section count, form inputs, schedule controls, market-state card, market override rows, footer save/reset controls, and API payload behavior are unchanged.
- `npm run test:unit:run -- src/components/TradingSettings.test.ts src/views/AdminView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 32 tests.
- `npm run test:unit:run -- src/views/AdminView.test.ts src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/UserManager.test.ts src/components/CreateChannelView.test.ts src/components/AdminMessagesView.test.ts src/components/TradingSettings.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 77 tests. The existing jsdom navigation warning from `CreateChannelView.test.ts` remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `npx playwright test e2e/admin-smoke.spec.ts --project=chromium --list`: passed, 4 tests discovered.
- `git diff --check`: passed.
