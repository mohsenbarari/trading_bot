# WebApp UI/UX Unification Roadmap - 2026-07-07

## Purpose

This roadmap turns the static Claude audit in `tmp/claude/webapp-ui-ux-unification-audit.md` into an implementation plan for unifying and improving the WebApp UI/UX, excluding Messenger.

The problem is not a lack of UI primitives. The current WebApp already has useful primitives in `frontend/src/components/ui/` and `frontend/src/components/workspace/`. The main problem is inconsistent adoption:

- undefined `--ds-*` tokens are used across important screens;
- two overlapping primitive families exist (`ui-*` and `ds-*`);
- high-traffic screens such as Dashboard, Market, OffersList, and Admin still use bespoke layout, card, button, badge, toast, loading, empty-state, and modal patterns;
- visual behavior is not consistently validated by runtime screenshots or visual regression checks.

## Scope

In scope:

- All non-Messenger Vue routes and views.
- Global app shell, navigation, toast host, and shared UI primitives.
- Dashboard, Market, OffersList, Admin, Account/Profile, Operations, customer/accountant workspaces, login/register/invite flows, notifications, public profile, settings, and share receive surfaces.
- Persian RTL, mobile/PWA behavior, desktop layout consistency, accessibility, and visual regression coverage.

Out of scope:

- Messenger route `/chat`.
- `frontend/src/views/MessengerView.vue`.
- `frontend/src/components/chat/`.
- `frontend/src/components/messenger-v2/`.
- `frontend/src/services/chat/`.
- `frontend/src/stores/chat/`.

Messenger may only be touched if a global primitive, app shell, navigation element, or token change affects non-Messenger surfaces and needs compatibility preservation.

## Execution Rules

- Work must happen on a dedicated candidate branch for this UI/UX effort, not directly on `main`.
- Before each implementation commit, verify the current branch.
- Avoid large visual rewrites without stage-specific tests and screenshot evidence.
- Do not change trading behavior, access policy, customer visibility policy, notification delivery semantics, or cross-server sync behavior while doing visual work.
- Market and OffersList changes are high-risk because they affect offer creation and trade request workflows. They must be implemented only after the shared primitives and tests are ready.
- Runtime visual validation is required before claiming this roadmap is complete. A static code audit alone is not enough.
- Do not weaken, delete, or loosen E2E assertions just to make a visual refactor pass. Stabilize selectors and fix code instead.
- Every implementation stage must define its gate before code changes. The default gate is:
  - frontend build;
  - relevant unit/component tests;
  - relevant Playwright E2E tests;
  - stage-specific screenshots where visual behavior changes.
- No staging or production deploy is part of this roadmap unless explicitly requested for a completed implementation stage.
- Shared shell changes must include Messenger smoke checks when they touch `App.vue`, `AppToasts.vue`, `BottomNav.vue`, shared primitives, or global tokens.

## Market Protected Surface Rule

The Market surface is a protected surface for this roadmap. The project must treat `MarketView.vue`, `OffersList.vue`, offer preview flows, lot suggestion flows, offer status rendering, and all trade-request controls as behavior-critical.

Protected files and flows:

- `frontend/src/views/MarketView.vue`
- `frontend/src/components/OffersList.vue`
- offer creation UI
- whole-offer request UI
- lot-offer request UI
- two-tap trade confirmation
- pending trade state
- expired/traded/active visual states
- customer/accountant/owner visibility in market cards
- market navigation controls that can overlap trade actions

Rules:

1. No DOM-level Market or OffersList refactor may start before Stage 4.5 is complete.
2. No visual/structural Market refactor may start without explicit product approval for that specific stage.
3. Before any Market refactor, capture baseline behavior and screenshots for:
   - active offer;
   - expired offer;
   - fully traded offer;
   - partially traded lot offer;
   - whole offer request;
   - lot offer request;
   - two-tap confirmation;
   - owner view;
   - requester view;
   - customer/accountant visibility cases.
4. Stage 2 is allowed to touch Market/OffersList only as CSS-value-only token replacement. It must not change DOM shape, class names, selectors, action handlers, request flow, or mounted component boundaries.
5. Any Market change must preserve the complete stabilized testid contract from Stage 4.5.
6. Any Market change must pass the mandatory market and role/visibility E2E gates before it can be considered complete.
7. If a market regression is detected, the stage must stop immediately and the market-related diff must be reverted before continuing.
8. A Market stage is not complete until manual/runtime review confirms no negative impact on trading speed, clarity, tap targets, confirmation flow, or offer state visibility.

## Sources

- Claude audit: `tmp/claude/webapp-ui-ux-unification-audit.md`
- Claude roadmap review: `tmp/claude/webapp-ui-ux-roadmap-review.md`
- Router: `frontend/src/router/index.ts`
- Current design tokens: `frontend/src/assets/main.css`
- Shared primitives:
  - `frontend/src/components/ui/`
  - `frontend/src/components/workspace/`
  - `frontend/src/components/account/TelegramConnectPanel.vue`
- Main target views:
  - `frontend/src/views/DashboardView.vue`
  - `frontend/src/views/MarketView.vue`
  - `frontend/src/components/OffersList.vue`
  - `frontend/src/views/AdminView.vue`
  - `frontend/src/views/AccountHubView.vue`
  - `frontend/src/views/ProfileView.vue`
  - `frontend/src/views/PublicProfileView.vue`
  - `frontend/src/views/OperationsView.vue`
  - `frontend/src/views/CustomerWorkspaceView.vue`
  - `frontend/src/views/AccountantWorkspaceView.vue`
  - `frontend/src/views/LoginView.vue`
  - `frontend/src/views/WebRegister.vue`
  - `frontend/src/views/InviteLanding.vue`
  - `frontend/src/views/NotificationsView.vue`
  - `frontend/src/views/SettingsView.vue`
  - `frontend/src/views/ShareReceiveView.vue`

## Target Design Principles

The WebApp is an operational Persian trading product, not a marketing page.

- Persian RTL first.
- Mobile/PWA first, with clean desktop behavior.
- Dense but readable information.
- Calm, professional visual style.
- Strong hierarchy for offers, trades, users, notifications, and admin tasks.
- Consistent color semantics:
  - buy/success must consistently map to the same success tone;
  - sell/danger must consistently map to the same danger tone;
  - warnings, errors, disabled states, and Telegram brand actions must be tokenized.
- All user actions must have consistent focus, disabled, loading, error, and empty states.
- Shared primitives must be preferred over view-local CSS.

## Stage 0 - Baseline Evidence and Static Guards

Goal: make the current inconsistency measurable before changing visuals.

Tasks:

1. Add or document a static token audit that fails when a `var(--ds-*)` token is used but not defined in the canonical token source.
2. Add or document a static guard for hardcoded trade-side colors in Dashboard, Market, and OffersList.
3. Add or document a static guard for new bespoke modal overlays outside an explicit allowlist.
4. Capture baseline screenshots for these routes at mobile and desktop widths:
   - `/`
   - `/market`
   - `/operations`
   - `/operations/customers`
   - `/operations/accountants`
   - `/account`
   - `/profile`
   - `/notifications`
   - `/admin/users`
   - `/admin/commodities`
   - `/login`
   - `/register`
   - `/i/:code` with a controlled test invite if possible
5. Record known static-only limitations and any route that cannot be rendered without test data.
6. Build the screenshot harness on top of the existing non-Messenger viewport coverage where possible, especially `frontend/e2e/non-messenger-viewport.spec.ts`.
7. Make screenshots deterministic before using them as regression evidence:
   - disable CSS animations and transitions;
   - freeze time and timezone-sensitive values where possible;
   - mask or stub live offer countdown timers;
   - mask or stub relative-time text;
   - control Dashboard greeting/time-dependent text;
   - document acceptable image-diff thresholds.
8. Add automated accessibility checks where practical for refactored routes, especially modals and keyboard-focused admin/market controls.

Exit criteria:

- Token drift is reproducible by a command or test.
- Undefined-token detection is a hard failing guard, not only documentation.
- Baseline visual evidence exists before UI changes.
- Baseline screenshot evidence is deterministic enough to be useful.
- No source UI change is made in this stage except test/guard infrastructure and documentation.

## Stage 1 - Token Repair and Design Token Canonicalization

Goal: remove the most visible low-risk breakage caused by undefined design tokens.

Tasks:

1. Add compatibility aliases for the 22 currently-used but undefined `--ds-*` tokens in `frontend/src/assets/main.css`.
2. Map old token names to canonical tokens rather than introducing a third token family.
3. Decide the Tailwind v4 strategy:
   - either load the existing config explicitly if it is still needed;
   - or migrate required values into CSS `@theme`/design tokens and retire unused config values later.
4. Add regression tests or scripts for undefined token detection.
5. Re-run baseline screenshots for the same routes from Stage 0.

Exit criteria:

- No undefined `--ds-*` token remains in non-Messenger frontend code.
- Visual regressions are reviewed route-by-route.
- Dashboard, Market, Login, Admin, and workspace views no longer rely on invalid token fallback behavior.

## Stage 2 - Global Shell, Toast, Brand, and Semantic Color Unification

Goal: fix cross-app inconsistencies that do not require rewriting major screens.

Tasks:

1. Replace the live toast host behavior with the existing `AppToast` visual language, including tone support for success, warning, danger, info, and neutral notifications.
2. Remove the inline OffersList error toast and route it through the central toast system.
3. Introduce or standardize a trade-side badge/chip primitive so buy/sell colors are consistent everywhere.
4. Replace bespoke link/ghost buttons with `AppButton` variants where behavior is simple and low risk.
5. Tokenize Telegram brand colors and use them consistently in:
   - `TelegramConnectPanel.vue`
   - `InviteLanding.vue`
   - any other Telegram connect entry point.
6. Tokenize the app background decision instead of hardcoding a gradient in `App.vue`.
7. Preserve PWA/mobile safe-area behavior.
8. If Stage 2 touches Market or OffersList, it must be CSS-value-only:
   - no DOM restructure;
   - no class-name removal;
   - no selector rename;
   - no `trade-btn`, `offer-card-wrap`, `price`, or preview/recent-offer hook change.
9. Preserve Messenger behavior when touching global shell surfaces:
   - chat toasts still render correctly through `AppToasts`;
   - `/chat` bottom navigation or FAB behavior does not regress;
   - chat unread and mention badges still render.

Exit criteria:

- Toasts look and behave consistently across WebApp routes.
- Buy/sell colors are not hardcoded in Dashboard, Market, or OffersList.
- Telegram connect visuals are consistent.
- App shell background is token-driven.
- Existing notification and trade behavior remains unchanged.
- Messenger smoke checks pass for shared toast/nav/background behavior.

## Stage 3 - Primitive Family Consolidation

Goal: reduce maintenance and visual drift between the `ds-*` and `ui-*` primitive families.

Tasks:

1. Decide the canonical primitive direction:
   - keep `WorkspaceShell` as a layout primitive if still needed;
   - migrate or wrap `WorkspaceSection`, `WorkspaceStatTile`, `WorkspaceActionTile`, and `WorkspaceNotice` around `ui-*` primitives.
2. Add missing primitives only where they remove real duplication:
   - `AppChip`
   - `AppAccordion` or `AppDisclosure`
   - `AppSkeleton`
   - `AppDataTable` or a lighter list/table primitive
   - `AppOfferCard` only after the Market/OffersList contract is understood.
3. Update primitive tests:
   - `AppPrimitives.test.ts`
   - `WorkspacePrimitives.test.ts`
4. Define a width/layout contract for:
   - narrow mobile-first pages;
   - wider workspace/master-detail pages;
   - admin pages;
   - Market.

Exit criteria:

- New UI work has one obvious primitive choice.
- Workspace primitives no longer diverge visually from `ui-*` primitives.
- Layout width decisions are documented and testable.

## Stage 4 - Dashboard Refactor

Goal: make the first screen consistent with the canonical UI system without changing dashboard data behavior.

Tasks:

1. Replace bespoke dashboard cards with shared card/section primitives.
2. Migrate dashboard accordion behavior to `AppAccordion`/`AppDisclosure` while preserving current accessibility attributes.
3. Replace bespoke chips and status labels with canonical chip/status primitives.
4. Standardize dashboard empty/loading/error states.
5. Replace hardcoded version display with a build/runtime source or remove it if no reliable source exists.
6. Preserve existing navigation targets and data loading behavior.

Exit criteria:

- Dashboard uses shared primitives for cards, sections, chips, loading, empty, and action states.
- No business/data behavior changes.
- Mobile and desktop screenshots are reviewed.
- Dashboard tests are updated.

## Stage 4.5 - Market Test Hook Hardening

Goal: stabilize the trading E2E selector contract before any DOM-level Market or OffersList refactor.

Reason:

The current Market/OffersList E2E suite is coupled to bespoke CSS classes such as `.offer-card-wrap`, `.trade-btn`, `.price`, `.send-btn`, `.text-offer-input`, `.offer-preview-*`, `.recent-offer*`, and `.trade-suggestion-lot-btn`. Only preserving `data-test="history-stamp"` is not enough. A visual refactor that replaces offer cards or buttons with shared primitives can break tests without proving a behavior regression.

Tasks:

1. Add stable test ids to the current implementation before changing DOM structure:
   - offer card wrapper;
   - price text;
   - trade action button;
   - pending trade state;
   - text offer input;
   - send button;
   - offer preview card;
   - offer preview confirm/error/close controls;
   - recent-offer toggle/dropdown/item;
   - lot suggestion button;
   - customer-context row and badge areas;
   - existing `history-stamp`.
2. Migrate these E2E tests from CSS-class selectors to the stabilized test ids:
   - `frontend/e2e/market-offers.spec.ts`
   - `frontend/e2e/lot-suggestion.spec.ts`
   - `frontend/e2e/market-mutation-ux.spec.ts`
   - `frontend/e2e/trade-history-accountant.spec.ts`
3. Keep all existing assertions semantically equivalent or stronger.
4. Do not weaken, delete, skip, or loosen test assertions during this migration.
5. Run the market/trade E2E subset before and after selector migration.

Exit criteria:

- Market/trade E2E tests no longer depend on the bespoke CSS classes that will be refactored in Stage 5.
- Existing trade, lot, preview, recent-offer, and history assertions remain covered.
- No visual redesign is included in this stage except adding invisible/stable test hooks.

## Stage 5 - Market and OffersList Refactor

Goal: unify the highest-risk trading surface after primitives and tests are ready.

Tasks:

0. Do not start DOM-level Market/OffersList refactor until Stage 4.5 is complete and the Market Protected Surface Rule has been explicitly satisfied.
1. Document current Market and OffersList behavior before changes:
   - offer creation;
   - whole offer request;
   - lot offer request;
   - two-tap trade confirmation;
   - expired/traded visual states;
   - owner/customer/accountant visibility rules;
   - Telegram/WebApp publication assumptions that surface in the UI.
2. Introduce `AppOfferCard` only after the behavior contract is documented.
3. Migrate offer cards, quantity badges, trade buttons, expired/traded states, loading, empty, and error states to shared primitives.
4. Preserve the two-tap confirmation behavior.
5. Preserve the full stabilized testid contract from Stage 4.5, including `history-stamp`.
6. Re-evaluate the Market navigation model:
   - bottom bar vs floating action button;
   - mobile safe area;
   - keyboard accessibility;
   - overlap with the market action bar.
7. Prepare a minimal rollback plan for the Market-specific diff before implementation starts.

Exit criteria:

- The Market Protected Surface Rule is satisfied.
- Market and OffersList look consistent with the rest of the WebApp.
- All offer/trade E2E tests pass.
- No regression in trade creation/request flows.
- Mandatory market and role/visibility gates pass unchanged:
  - `frontend/e2e/market-offers.spec.ts`
  - `frontend/e2e/lot-suggestion.spec.ts`
  - `frontend/e2e/market-mutation-ux.spec.ts`
  - `frontend/e2e/trade-history-accountant.spec.ts`
  - `frontend/e2e/customer-owner-flow.spec.ts`
  - `frontend/e2e/accountant-owner-flow.spec.ts`
  - `frontend/e2e/customer-chat-privacy.spec.ts`
- Mobile and desktop screenshots are reviewed.

## Stage 6 - Admin Surface Refactor

Goal: bring Admin subsections into the same design system without weakening management workflows.

Tasks:

1. Migrate admin tables/lists to a shared table/list primitive.
2. Migrate admin forms to `AppFormField`, `AppInput`, `AppSelect`, and `AppButton`.
3. Replace bespoke admin modals with `AppResponsiveDialog` where behavior allows.
4. Add consistent focus-visible behavior for all admin actions.
5. Keep admin route behavior and user/customer/accountant management policy unchanged.
6. Update tests for:
   - user list;
   - invitations;
   - commodities;
   - channels;
   - admin messages;
   - system/trading settings.
7. Run mandatory admin and role/visibility gates:
   - `frontend/e2e/admin-smoke.spec.ts`
   - `frontend/e2e/customer-owner-flow.spec.ts`
   - `frontend/e2e/accountant-owner-flow.spec.ts`

Exit criteria:

- Admin sections no longer feel visually separate from the rest of the WebApp.
- Management actions keep their existing permissions and behavior.
- Keyboard/focus behavior is consistent.

## Stage 7 - Profile, Public Profile, Account, and Operations Cleanup

Goal: standardize medium-risk screens after global primitives are stable.

Tasks:

1. Migrate `PublicProfile.vue` and `UserProfile.vue` modals to `AppResponsiveDialog`.
2. Standardize account/profile headers and action lists.
3. Preserve customer/accountant visibility and display-name policies.
4. Keep `OperationsView.vue` and `AccountHubView.vue` as reference patterns unless the Stage 3 layout decision changes them.
5. Align customer/accountant workspaces with the consolidated primitive family.
6. Run mandatory profile and role/visibility gates:
   - `frontend/e2e/customer-owner-flow.spec.ts`
   - `frontend/e2e/accountant-owner-flow.spec.ts`
   - `frontend/e2e/customer-chat-privacy.spec.ts`
   - `frontend/e2e/trade-history-accountant.spec.ts`

Exit criteria:

- Profile and public profile screens are visually aligned with account and operations screens.
- Customer/accountant visibility rules remain unchanged.
- Existing profile and workspace tests pass.

## Stage 8 - Navigation and PWA Interaction Decision

Goal: resolve the remaining product-level UX questions with evidence.

Open decisions:

1. Should Market use the same bottom navigation model as most routes, or keep a floating action button?
2. If FAB remains, should it be draggable or fixed?
3. What is the safe-area and overlap policy for Market action controls?
4. Should desktop pages remain narrow by default, or should operational screens use wider constrained layouts?

Tasks after decisions:

1. Implement the selected navigation pattern consistently.
2. Add keyboard and screen-reader coverage for the selected navigation behavior.
3. Verify PWA install, share receive, safe-area, and mobile viewport behavior.
4. If `BottomNav.vue` or shared navigation behavior changes, verify `/chat` still keeps its expected FAB/bottom-nav and unread/mention badge behavior.

Exit criteria:

- Navigation is predictable across WebApp routes.
- No important action is hidden, overlapped, or hard to reach on mobile.
- Messenger navigation smoke checks pass when shared navigation is touched.

## Stage 9 - Dead Code and Legacy Cleanup

Goal: reduce future confusion after active surfaces are stabilized.

Tasks:

1. Re-check suspected dead components:
   - `TradingView.vue`
   - `HomePage.vue`
   - `MainMenu.vue`
   - `PlaceholderView.vue`
   - `UserSettings.vue`
   - `HomeAnimation.vue`
2. Confirm they are not dynamically imported or referenced by tests that still represent desired behavior.
3. Delete confirmed dead components and their obsolete tests.
4. Keep any component that still documents a behavior contract that has not yet been replaced.

Exit criteria:

- Dead code is removed only after active surfaces are stable.
- Build and tests pass.

## Stage 10 - Final Visual Acceptance Matrix

Goal: prove that the WebApp UI/UX is actually unified after implementation.

Required checks:

- Mobile and desktop screenshots for every non-Messenger route.
- At least one narrow mobile viewport around 390-430px.
- At least one desktop viewport around 1024-1440px.
- RTL text, numbers, mobile numbers, and mixed Persian/Latin content.
- Loading, empty, error, disabled, success, warning, and danger states.
- Login/register/invite flows.
- Market offer creation and request flows.
- Customer/accountant/admin role surfaces.
- Notification and toast states.
- PWA install and share receive surfaces.
- Keyboard focus and modal accessibility.

Exit criteria:

- All stage tests pass.
- Visual review shows consistent typography, spacing, card shape, button behavior, status badges, toast behavior, modal behavior, and page layout.
- Any remaining inconsistency is explicitly documented as intentional.

## Does This Roadmap Fully Solve the UI/UX Inconsistency?

Yes, if all stages are implemented and validated, this roadmap should resolve the current structural UI/UX inconsistency in the non-Messenger WebApp.

The reason is that it addresses the causes, not only the symptoms:

- token drift is fixed first;
- global shell and toast inconsistencies are fixed early;
- duplicated primitive families are consolidated before large refactors;
- high-risk trading UI is delayed until primitives and tests are ready;
- Admin/Profile/Operations cleanup is staged after the shared system stabilizes;
- final completion requires runtime screenshots and behavioral tests, not just static code changes.

However, this roadmap alone is not enough if implementation skips visual evidence. The current Claude audit was static. Final confidence requires Playwright/manual screenshots and route-by-route review after each major stage.

## Open Product Decisions

These should be answered before or during the relevant stages:

1. Market navigation: bottom bar, fixed FAB, or draggable FAB?
2. Desktop width policy: keep mobile-first narrow pages or widen operational pages?
3. App background: neutral `--ds-bg-page` or a tokenized brand background?
4. Loading standard: spinner, skeleton, or both with clear usage rules?
5. Primitive consolidation: should `WorkspaceShell` remain the only `ds-*` layout primitive?
6. Version source: should Dashboard show a version, and if yes, from what build/runtime source?

## Recommended First Implementation Slice

The first implementation slice should be:

1. Stage 0 static guards and baseline screenshots.
2. Stage 1 token alias repair.
3. A small screenshot review of Dashboard, Market, Login, Admin, and customer/accountant workspaces.

This gives immediate visual improvement with low behavioral risk and creates the guardrails needed for the larger refactors.
