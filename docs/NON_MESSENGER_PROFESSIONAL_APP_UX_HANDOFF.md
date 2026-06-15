# Non-Messenger Professional App UX Handoff

Date: 2026-06-15

## Scope

این سند جمع‌بندی Stage 1 تا Stage 13 از roadmap زیر است:

- `docs/NON_MESSENGER_PROFESSIONAL_APP_UX_PLAN.md`

هدف این موج:

- حرفه‌ای‌سازی کامل UI/UX تمام سطح‌های غیرپیام‌رسان با حفظ RTL، mobile-first، app-like behavior و یکپارچگی بصری.
- کاهش فاصله بین shellها، workspaceها، overlayها، typography، spacing، feedback stateها و route behavior.
- بستن شکاف‌های accessibility، obstruction، و selector drift در surfaceهای non-messenger.
- حفظ behavior فعلی routeها، deep-linkها، permission visibility، business contractها و compatibilityهای لازم تا بعد از release.

Messenger internals عمداً خارج از scope بودند. فقط `MessengerView.test.ts` به‌عنوان shell smoke در gateهای نهایی استفاده شد.

Production deploy در این roadmap عمداً اجرا نشد. این قاعده در خود roadmap صریح بود و در Stage 13 هم حفظ شد.

## Stage Summary

| Stage | وضعیت | خروجی اصلی |
| --- | --- | --- |
| Stage 1 | Complete | تکمیل shared UI primitives، page/workspace/dialog/bottom-sheet/toast/input/search/filter/stepper primitives و tokenهای پایه. |
| Stage 2 | Complete | audit و alignment route/native shellها، deep-link normalization و پایه‌گذاری route-first non-messenger structure. |
| Stage 3 | Complete | route-native customer workspace و ساختارهای list/detail/action به‌جای اتکای کامل به manager قدیمی. |
| Stage 4 | Complete | route-native accountant workspace و detail/session/action flowهای هم‌راستا با customer workspace. |
| Stage 5 | Complete | جمع‌کردن visual drift و iconography/text-symbol debt در surfaceهای profile/public/admin/customer/accountant. |
| Stage 6 | Complete | هماهنگی admin workspace، subview shell، route aliases و بخش‌های مدیریتی با shared system. |
| Stage 7 | Complete | polish سطح‌های profile/public-profile و alignment رفتار/spacing/help/meta rows. |
| Stage 8 | Complete | polish notification/account shellها و surfaceهای مرتبط با settings/security/storage/account hub. |
| Stage 9 | Complete | polish dashboard، operations و market shell بدون دست‌زدن به منطق اصلی trading. |
| Stage 10 | Complete | هماهنگی auth/public utility pages و overlay/help/toast surfaceها. |
| Stage 11 | Complete | hardening accessibility و overlay keyboard/focus/scroll-lock behavior. |
| Stage 11.1 | Complete | audit و استانداردسازی typography برای eyebrow/badge/meta/helper/tag copy در non-messenger surfaces. |
| Stage 12 | Complete | visual QA نهایی با build، unit broad، smoke e2e و viewport matrix. |
| Stage 13 | Complete | این handoff نهایی فارسی و بستن roadmap. |

## Main Files And Responsibilities

Shared UI/design system:

- `frontend/src/components/ui/`
- `frontend/src/assets/main.css`

Route-level non-messenger shells:

- `frontend/src/views/DashboardView.vue`
- `frontend/src/views/OperationsView.vue`
- `frontend/src/views/CustomerWorkspaceView.vue`
- `frontend/src/views/AccountantWorkspaceView.vue`
- `frontend/src/views/AccountHubView.vue`
- `frontend/src/views/SettingsView.vue`
- `frontend/src/views/NotificationsView.vue`
- `frontend/src/views/ProfileView.vue`
- `frontend/src/views/PublicProfileView.vue`
- `frontend/src/views/AdminView.vue`
- `frontend/src/views/MarketView.vue`

Key shared feature components:

- `frontend/src/components/PublicProfile.vue`
- `frontend/src/components/AdminMessagesView.vue`
- `frontend/src/components/CreateInvitationView.vue`
- `frontend/src/components/CreateChannelView.vue`
- `frontend/src/components/TradingSettings.vue`
- `frontend/src/components/CommodityManager.vue`
- `frontend/src/components/BottomNav.vue`

Compatibility-heavy components still present:

- `frontend/src/components/OwnerCustomerManagerModal.vue`
- `frontend/src/components/OwnerAccountantManagerModal.vue`

Core verification files used at the end:

- `frontend/e2e/auth.spec.ts`
- `frontend/e2e/admin-smoke.spec.ts`
- `frontend/e2e/notifications.spec.ts`
- `frontend/e2e/non-messenger-viewport.spec.ts`
- focused Vitest files under `frontend/src/**/*.test.ts`

## Compatibility Status

Compatibility intentionally remains in place:

- legacy owner flows هنوز می‌توانند از managerهای قدیمی استفاده کنند، اما surface اصلی کاربر route-native شده است.
- `OwnerCustomerManagerModal.vue` و `OwnerAccountantManagerModal.vue` هنوز برای compatibility/fallback باقی مانده‌اند و حذف نشده‌اند.
- `SettingsView.vue` هم مسیرهای جدید account-hub و هم مسیرهای legacy `/settings` را پوشش می‌دهد.
- `AdminView.vue` هم route-native `/admin/*` و هم alias قدیمی query-based مثل `?section=system_settings` را سالم نگه می‌دارد.
- visibility فرانت هرگز مرز امنیتی در نظر گرفته نشده و backend همچنان authoritative است.

## Route Map

| Area | Primary route |
| --- | --- |
| Dashboard | `/` |
| Market | `/market` |
| Operations hub | `/operations` |
| Customers | `/operations/customers`, `/operations/customers/:relationId` |
| Accountants | `/operations/accountants`, `/operations/accountants/:relationId` |
| Account hub | `/account` |
| Security | `/account/security` |
| Storage | `/account/storage` |
| Notifications | `/account/notifications` و legacy `/notifications` |
| Admin invitations | `/admin/invitations` |
| Admin channels | `/admin/channels` |
| Admin users | `/admin/users`, `/admin/users/:id` |
| Admin commodities | `/admin/commodities` |
| Admin messages | `/admin/messages` |
| Admin system | `/admin/system` |

## What Changed In Stage 12-13 Specifically

Stage 12:

- duplicate heading در invitation admin surface حذف شد تا shell heading تنها مرجع semantic page title باشد.
- smoke selectorها برای auth/admin/notifications بر اساس surface واقعی امروز هم‌راستا شدند و از locatorهای شکننده یا ambiguous فاصله گرفتند.
- test budget موضعی برای چند baseline test سنگین بالا رفت تا broad gate روی regression واقعی fail شود، نه روی default timeout.
- viewport matrix non-messenger بدون ساخت harness جدید اجرا و تأیید شد.

Stage 13:

- این handoff ساخته شد.
- roadmap نهایی بسته شد.
- `.github/copilot-instructions.md` با گزارش Stage 13 به‌روزرسانی شد.

## Final Validation

Broad non-messenger unit gate:

```bash
cd frontend
npm run test:unit:run -- src/router/index.test.ts src/components/BottomNav.test.ts src/views/MessengerView.test.ts src/views/DashboardView.test.ts src/views/OperationsView.test.ts src/views/AccountHubView.test.ts src/views/SettingsView.test.ts src/views/NotificationsView.test.ts src/views/CustomerWorkspaceView.test.ts src/views/AccountantWorkspaceView.test.ts src/views/AdminView.test.ts src/views/ProfileView.test.ts src/views/PublicProfileView.test.ts src/views/MarketView.test.ts src/components/PublicProfile.test.ts src/components/AdminMessagesView.test.ts src/components/CreateChannelView.test.ts src/components/CreateInvitationView.test.ts src/components/CommodityManager.test.ts src/components/TradingSettings.test.ts
```

Result:

- `20/20` files passed
- `204/204` tests passed

Frontend production build:

```bash
cd frontend
npm run build
```

Result:

- Passed
- existing chunk-size warnings remain as pre-existing bundle debt

Chromium auth smoke:

```bash
cd frontend
npm run test:e2e -- e2e/auth.spec.ts --project=chromium --reporter=line
```

Result:

- `3/3` passed

Chromium admin smoke:

```bash
cd frontend
npm run test:e2e -- e2e/admin-smoke.spec.ts --project=chromium --reporter=line
```

Result:

- `4/4` passed

Chromium notifications non-messenger subset:

```bash
cd frontend
npm run test:e2e -- e2e/notifications.spec.ts --project=chromium --reporter=line --grep "dashboard notifications button opens the notification center|websocket heartbeat pong does not emit JSON parse errors|session approval modal shows a pending request and reject clears it"
```

Result:

- `3/3` passed

Chromium non-messenger viewport matrix:

```bash
cd frontend
npm run test:e2e -- e2e/non-messenger-viewport.spec.ts --project=chromium --reporter=line
```

Result:

- `8/8` passed

Whitespace gate:

```bash
git diff --check
```

Result:

- Passed

## Screenshots / Visual Evidence

- در Stage 13 screenshot artifact جدید ساخته نشد.
- verification تصویری نهایی بر پایه `non-messenger-viewport.spec.ts` و smoke route coverage انجام شد.
- اگر بعد از deploy واقعی نیاز به visual sign-off انسانی باشد، باید روی deviceهای واقعی و PWA/Telegram WebView دوباره انجام شود.

## Known Remaining Debt

این‌ها blocker این roadmap نیستند، اما باقی مانده‌اند:

- `OwnerCustomerManagerModal.vue` و `OwnerAccountantManagerModal.vue` هنوز compatibility-heavy هستند و در refactor بعدی بهتر است بیشتر split شوند.
- `AdminView.vue` هنوز orchestration بالایی دارد؛ route-native است، اما subroute decomposition عمیق‌تر هنوز ممکن است.
- bundle-size warningهای build باقی مانده‌اند و باید در برنامه جداگانه code-splitting بررسی شوند.
- بخشی از notification e2eها که عمیقاً وارد سناریوهای پیام‌رسان یا trade mutation می‌شوند، عمداً خارج از gate نهایی Stage 12 نگه داشته شدند چون این roadmap non-messenger بود.
- smoke selectorها با UI فعلی هم‌راستا شدند؛ اگر بعداً labelهای shell تغییر کنند باید smokeها دوباره align شوند.

## Guardrails For Future Agents

- messenger internals را در refactorهای non-messenger دست نزنید مگر task صریحاً بخواهد.
- compatibility routeها و owner managerها را قبل از post-release validation حذف نکنید.
- اگر test flake از جنس duplicate DOM/transition بود، اول بررسی کنید issue واقعی UI است یا فقط harness ambiguity.
- broad gateهای non-messenger را با matrix سنگین پیام‌رسان قاطی نکنید.
- بعد از هر تغییر، `.github/copilot-instructions.md` را به‌روز کنید.

## Production Deploy Status

Production deploy در Stage 1 تا Stage 13 این roadmap اجرا نشد.

این handoff فقط تغییرات کد، تست و مستندسازی non-messenger professional UX را می‌بندد و هیچ release/deploy/server actionی در آن انجام نشده است.
