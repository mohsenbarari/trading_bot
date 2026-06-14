# Non-Messenger Visual Unification Handoff

Date: 2026-06-14

## Scope

این سند جمع‌بندی Stage 0 تا Stage 13 از roadmap زیر است:

- `docs/NON_MESSENGER_VISUAL_UNIFICATION_PLAN.md`

هدف این موج:

- یکپارچه‌سازی visual و UI/UX تمام سطح‌های غیرپیام‌رسان.
- حفظ رفتار فعلی business logic، permissionها، routeها، deep linkها و compatibility.
- کاهش drift ظاهری، emoji/text-symbol debt، local spinner/button/empty-state patternها و CSS مرده.
- افزودن verification قابل تکرار برای viewport، keyboard و route smoke.

Messenger internals عمداً خارج از scope بودند. فقط `MessengerView.test.ts` به‌عنوان shell mount smoke در gate نهایی Stage 12 استفاده شد.

Production deploy در این roadmap عمداً اجرا نشد، چون قانون roadmap این بود که deploy فقط با دستور صریح مالک پروژه انجام شود.

## Stage Summary

| Stage | وضعیت | خروجی اصلی |
| --- | --- | --- |
| Stage 0 | Complete | ایجاد roadmap فارسی، audit سطح‌ها، mapping مسیرها، guardrailها و ممنوعیت production deploy بدون دستور صریح. |
| Stage 1 | Complete | ایجاد primitive layer در `frontend/src/components/ui/` و tokenهای پایه در `frontend/src/assets/main.css`. |
| Stage 2 | Complete | استخراج لایه داده customer/accountant به `useOwnerCustomers.ts` و `useOwnerAccountants.ts` و تبدیل workspaceها به route-native overview/detail shell. |
| Stage 3 | Complete | بازسازی `CustomerWorkspaceView.vue` با metrics، filters، search، pending/manageable groups و detail tabs. |
| Stage 4 | Complete | بازسازی `AccountantWorkspaceView.vue` با ساختار مشابه customer workspace و sessions tab. |
| Stage 5 | Complete | پاکسازی emoji/text-symbol و icon drift از profile/public-profile، جایگزینی با lucide/shared states. |
| Stage 6 | Complete | هماهنگی `/admin` landing با shared primitives و حفظ permission/deep-link behavior. |
| Stage 7 | Complete | هماهنگی small surfaces شامل register، invite، share receive، PWA overlay و Jalali date picker. |
| Stage 8 | Complete | polish سطح‌های core شامل dashboard، operations، account، settings و notifications. |
| Stage 9 | Complete | هماهنگی visual بازار بدون تغییر trading create/parse/confirm/cancel/trade logic. |
| Stage 10 | Complete | responsive/PWA hardening، safe-area، touch targets و viewport matrix. |
| Stage 11 | Complete | keyboard/ARIA hardening برای notification tabs/rows و admin message mode switcher. |
| Stage 12 | Complete | verification gate نهایی با focused unit، build، route/deep-link، viewport e2e و auth smoke. |
| Stage 13 | Complete | این handoff و گزارش نهایی فارسی. |

## Main Files And Responsibilities

Design-system primitives:

- `frontend/src/components/ui/`
- `frontend/src/components/workspace/`
- `frontend/src/assets/main.css`

Route-level workspaces:

- `frontend/src/views/OperationsView.vue`
- `frontend/src/views/CustomerWorkspaceView.vue`
- `frontend/src/views/AccountantWorkspaceView.vue`
- `frontend/src/views/AccountHubView.vue`
- `frontend/src/views/AdminView.vue`

Customer/accountant data layer:

- `frontend/src/composables/useOwnerCustomers.ts`
- `frontend/src/composables/useOwnerAccountants.ts`

Compatibility-heavy owner managers:

- `frontend/src/components/OwnerCustomerManagerModal.vue`
- `frontend/src/components/OwnerAccountantManagerModal.vue`

Core surfaces polished:

- `frontend/src/views/DashboardView.vue`
- `frontend/src/views/MarketView.vue`
- `frontend/src/views/SettingsView.vue`
- `frontend/src/views/NotificationsView.vue`
- `frontend/src/views/ProfileView.vue`
- `frontend/src/views/PublicProfileView.vue`
- `frontend/src/components/PublicProfile.vue`
- `frontend/src/components/BottomNav.vue`
- `frontend/src/components/AdminMessagesView.vue`
- `frontend/src/components/TradingSettings.vue`

Small utility/public surfaces:

- `frontend/src/views/WebRegister.vue`
- `frontend/src/views/InviteLanding.vue`
- `frontend/src/views/ShareReceiveView.vue`
- `frontend/src/components/PWAInstallOverlay.vue`
- `frontend/src/components/JalaliDatePicker.vue`

Regression/viewport coverage:

- `frontend/e2e/non-messenger-viewport.spec.ts`
- `frontend/e2e/auth.spec.ts`
- focused unit tests in `frontend/src/**/*.test.ts`

## Compatibility Status

Compatibility intentionally remains in place:

- `/profile?workspace=customers` and `/profile?workspace=accountants` remain part of the legacy mental model, but primary owner flows now route through `/operations/customers` and `/operations/accountants`.
- `OwnerCustomerManagerModal.vue` and `OwnerAccountantManagerModal.vue` still support compatibility/full-management presentation. The new route workspace shells use them through `presentation="workspace"` where needed.
- `/settings?section=sessions` and `/settings?section=storage` still work through `SettingsView.vue`; primary account routes are `/account/security` and `/account/storage`.
- `/notifications` still works; `/account/notifications` is the account-hub entry.
- `/admin?section=...` compatibility remains while `/admin/*` route names drive the primary route-native admin experience.
- Backend remains authoritative for permissions. Frontend visibility changes are not treated as security boundaries.

## Route Map

| Area | Primary route |
| --- | --- |
| Dashboard | `/` |
| Market | `/market` |
| Operations hub | `/operations` |
| Customers | `/operations/customers`, `/operations/customers/:relationId` |
| Accountants | `/operations/accountants`, `/operations/accountants/:relationId` |
| Account hub | `/account` |
| Security/sessions | `/account/security` |
| Storage/cache | `/account/storage` |
| Notifications | `/account/notifications` and legacy `/notifications` |
| Admin invitations | `/admin/invitations` |
| Admin channels | `/admin/channels` |
| Admin users | `/admin/users`, `/admin/users/:id` |
| Admin commodities | `/admin/commodities` |
| Admin messages | `/admin/messages` |
| Admin system | `/admin/system` |

## Final Validation

Focused unit gate:

```bash
cd frontend
npm run test:unit:run -- AppPrimitives.test.ts WorkspacePrimitives.test.ts OperationsView.test.ts CustomerWorkspaceView.test.ts AccountantWorkspaceView.test.ts ProfileView.test.ts PublicProfileView.test.ts PublicProfile.test.ts AdminView.test.ts AdminPanel.test.ts TradingSettings.test.ts AccountHubView.test.ts SettingsView.test.ts NotificationsView.test.ts DashboardView.test.ts MarketView.test.ts BottomNav.test.ts AdminMessagesView.test.ts router/index.test.ts MessengerView.test.ts AppAuthenticatedShell.test.ts
```

Result:

- `21/21` files passed.
- `183/183` tests passed.

Production frontend build:

```bash
cd frontend
npm run build
```

Result:

- Passed.
- Existing chunk-size warnings remain. They are not introduced by this roadmap and remain separate bundle-splitting debt.

Chromium e2e smoke:

```bash
cd frontend
npx playwright test e2e/non-messenger-viewport.spec.ts e2e/auth.spec.ts --project=chromium --reporter=line
```

Result:

- `9/9` tests passed.
- Covered viewport widths: `360`, `390`, `430`, `768`, `1024`, `1440`.
- Covered non-messenger routes in viewport matrix: dashboard، operations، customers، accountants، account، security، notifications، market، admin.
- Covered auth smoke: unauthenticated redirect، dev quick login، authenticated protected-route bypass.

Whitespace gate:

```bash
git diff --check
```

Result:

- Passed.

## Known Release Debt

Not blockers for the first release:

- `OwnerCustomerManagerModal.vue` and `OwnerAccountantManagerModal.vue` are still large compatibility components. They are no longer the only primary experience, but a later split into smaller route-native sections is still recommended.
- `AdminView.vue` still orchestrates several admin subsections. Route names are now supported, but deeper child-route componentization remains future work.
- `SettingsView.vue` intentionally serves both legacy `/settings` and new `/account/security|storage` routes.
- `MarketView.vue` trading logic was intentionally not rewritten. Visual/safe-area/accessibility changed, but core trading behavior remains as-is.
- Bundle-size warnings remain and should be handled by a separate code-splitting plan, not mixed into this visual refactor.
- Real-device visual QA is still recommended after the next explicit production deploy, especially on iOS PWA and Telegram WebApp surfaces.

## Guardrails For Future Agents

- Do not remove compatibility routes until after release telemetry/manual QA confirms they are unused or safely redirected.
- Do not delete owner manager modal compatibility branches until customer/accountant detail pages are split into smaller route-native components.
- Do not treat frontend permission visibility as security. Backend checks remain required.
- Do not touch messenger internals as part of non-messenger visual work unless the task explicitly says so.
- Do not run production deploy unless the owner explicitly requests it.
- After every project change, update `.github/copilot-instructions.md`.

## Production Deploy Status

Production deploy was not run during Stage 0-13 of this visual unification roadmap.

The last production deploy belongs to the prior H10 heavy UI/UX roadmap and is documented separately in:

- `docs/NON_MESSENGER_HEAVY_UI_UX_REFACTOR_HANDOFF.md`

This Stage 13 closeout only documents and commits the handoff.
