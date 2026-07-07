# WebApp UI/UX Stage 7 - Profile and Account Contract

Stage 7 standardizes profile, public-profile, account, and operations surfaces after the shared primitives are stable.

This stage is medium risk because these screens enforce customer/accountant visibility, public-profile routing, avatar/address editing, admin user actions, limitations, blocking, trade-history navigation, and owner/accountant/customer relationship display.

## Branch and Deployment Guard

- All Stage 7 work must stay on `candidate/webapp-ui-ux-unification`.
- Check the current branch before every commit.
- Commit and push each safe slice separately.
- Do not deploy staging or production unless explicitly requested.

## Non-Negotiable Behavior Contracts

- Do not change customer visibility rules.
- Do not show customer tier, direct counterparty details, or restricted relationship details where current tests expect them hidden.
- Do not change accountant/customer display-name policy.
- Do not change profile/public-profile API endpoints, payload shape, or request timing.
- Do not change avatar upload semantics, file input refs, or `/api/auth/me/avatar` payloads.
- Do not change address edit validation, `/api/auth/me/address` payloads, or owner-only address editing.
- Do not change block, unblock, limitation, role, max-accountants, delete, or account-status mutation behavior.
- Do not change route/navigation events from `UserProfile.vue`, `PublicProfile.vue`, `ProfileView.vue`, or `AccountHubView.vue`.
- Do not change messenger behavior from profile/public-profile links.

## Safe Slice Order

1. Prepare primitives only when needed by profile migration.
2. Migrate one modal family at a time in `UserProfile.vue`, preserving legacy selectors used by tests.
3. Migrate `PublicProfile.vue` modal/dialog surfaces only after `UserProfile.vue` modal tests are stable.
4. Tokenize low-risk profile/account colors only after behavioral tests pass.
5. Keep `AccountHubView.vue` and `OperationsView.vue` as reference surfaces unless a specific inconsistency is found.

## First Safe Slice - Responsive Dialog Compatibility

The first Stage 7 slice adds optional compatibility hooks to `AppResponsiveDialog`:

- `backdropClass`
- `panelClass`
- `bodyClass`
- `actionsClass`

These props let profile modals preserve legacy selectors such as `.modal-overlay`, `.modal-content`, `.date-modal-content`, and action/body hooks while moving to the shared dialog primitive.

The default `AppResponsiveDialog` output remains unchanged for existing users in customer/accountant workspaces.

## Required Validation

Focused gates per slice:

- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts`
- Profile-specific tests touched by the slice.
- `git diff --check`

Broader gates before finishing Stage 7:

- `npm run test:unit:run -- src/components/UserProfile.test.ts src/components/PublicProfile.test.ts src/views/ProfileView.test.ts src/views/AccountHubView.test.ts src/views/OperationsView.test.ts src/views/PublicProfileView.test.ts src/views/SettingsView.test.ts`
- `npm run guard:ui`
- `npm run build`
- `npx playwright test e2e/customer-owner-flow.spec.ts e2e/accountant-owner-flow.spec.ts e2e/customer-chat-privacy.spec.ts e2e/trade-history-accountant.spec.ts --project=chromium --list`

## Rollback Plan

If profile/public-profile behavior regresses:

1. Revert only the latest Stage 7 safe-slice commit.
2. Confirm `UserProfile.vue` / `PublicProfile.vue` legacy selectors and modal state refs return to the previous behavior.
3. Rerun the focused tests for the reverted surface.

## Responsive Dialog Compatibility Slice Evidence

- `AppResponsiveDialog` now accepts optional `backdropClass`, `panelClass`, `bodyClass`, and `actionsClass` props.
- Existing default dialog markup remains unchanged when those props are not provided.
- The primitive test verifies that compatibility classes are applied to the backdrop, panel, body, and actions elements.
- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts`: passed, 9 tests.
- `npm run test:unit:run -- src/components/UserProfile.test.ts src/components/PublicProfile.test.ts src/views/ProfileView.test.ts src/views/AccountHubView.test.ts src/views/OperationsView.test.ts src/views/PublicProfileView.test.ts src/views/SettingsView.test.ts src/views/CustomerWorkspaceView.test.ts src/views/AccountantWorkspaceView.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 111 tests. Existing intentional stderr from `SettingsView.test.ts` covers the `clear-cache-failed` fallback path and remains non-fatal.
- `npm run guard:ui`: passed.
- `npm run build`: passed.
- `git diff --check`: passed.
- `npx playwright test e2e/customer-owner-flow.spec.ts e2e/accountant-owner-flow.spec.ts e2e/customer-chat-privacy.spec.ts e2e/trade-history-accountant.spec.ts --project=chromium --list`: blocked in the current sandbox because these specs access Docker during import (`spawnSync docker EPERM`). Escalated execution was not permitted by the environment policy.

## UserProfile Block Modal Dialog Slice Evidence

- `UserProfile.vue` block-duration modal now uses `AppResponsiveDialog`.
- Legacy selectors remain available through `backdropClass="modal-overlay"` and `panelClass="modal-content"`.
- Preserved block duration buttons, custom date trigger, custom block submit, cancel/back buttons, and all `blockUser`, `blockUserCustom`, `showBlockDateModal`, and `sendBlockRequest` behavior.
- Limitation and date-picker modals remain native in this slice.
- The UserProfile UI-controls test verifies the migrated modal has both shared dialog classes and legacy `.modal-content` / `.modal-overlay` classes.
- `npm run test:unit:run -- src/components/UserProfile.test.ts src/components/ui/AppPrimitives.test.ts`: passed, 25 tests.
