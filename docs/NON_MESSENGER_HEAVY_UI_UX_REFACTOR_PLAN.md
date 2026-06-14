# Non-Messenger Heavy UI/UX Refactor Plan

Last updated: 2026-06-14
Status: Stage H1 foundation completed; route/page migration stages are pending.

## 1. هدف و اصل اجرایی

این سند قرارداد اجرایی بازطراحی سنگین UI/UX تمام بخش‌های خارج از پیام‌رسان است. هدف فقط تغییر رنگ، فاصله، کارت و آیکن نیست؛ هدف این است که ساختار کاری برنامه از حالت «صفحه‌های پراکنده و modalهای بزرگ» به «workspaceهای واضح، route-level، قابل bookmark، قابل تست و قابل نگهداری» تبدیل شود.

تعریف موفقیت:
- هیچ قابلیت فعلی حذف یا گم نشود.
- مسیرهای قدیمی و deep linkها تا پایان مهاجرت کار کنند.
- هر عمل فعلی در نقشه old-to-new مقصد مشخص داشته باشد.
- پیام‌رسان داخلی دست‌نخورده بماند.
- هر stage کوچک، قابل تست، قابل rollback و دارای معیار پذیرش باشد.

## 2. محدوده

داخل محدوده:
- Global authenticated shell و bottom navigation
- Dashboard/home
- Operations
- Account hub
- Profile و public profile
- Customer management
- Accountant management
- Admin panel و ابزارهای مدیریتی
- Settings، sessions، storage، logout restrictions
- Notifications
- Market visual shell و هماهنگی UX بدون دستکاری ریسکی core trading
- Invite/register/setup-password/share utility routes
- Design system foundation برای non-messenger surfaces
- Responsive desktop/tablet/mobile
- Accessibility و keyboard/focus contracts

خارج از محدوده:
- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/**`
- `frontend/src/composables/chat/**`
- `frontend/src/services/chat/**`
- chat stores، chat event gateway، message timeline، media renderer، upload/download/search/virtualization internals
- messenger benchmark/e2e internals مگر فقط برای اطمینان از عدم regression

سازگاری پیام‌رسان که همچنان مجاز است:
- آیتم `/chat` در bottom navigation
- badgeهای unread/mention در `BottomNav.vue`
- shell/global layout که route پیام‌رسان را host می‌کند

## 3. ممیزی routeهای فعلی

| Route | Name | فایل فعلی | وضعیت فعلی | درمان در refactor |
|---|---|---|---|---|
| `/` | `home` | `frontend/src/views/DashboardView.vue` | landing با market entry، معاملات امروز، notification/profile shortcuts، account switcher توسعه | حفظ به‌عنوان Home، سبک‌تر و task-focused |
| `/setup-password` | `setup-password` | `frontend/src/views/SetupPassword.vue` | مسیر احراز/تکمیل رمز | فقط هماهنگی visual و accessibility |
| `/login` | `login` | `frontend/src/views/LoginView.vue` | ورود | خارج از refactor سنگین مگر shell consistency |
| `/market` | `market` | `frontend/src/views/MarketView.vue` | بازار realtime با offer input، tabs، recent offers و TradingView | هماهنگی visual، حفظ trading behavior |
| `/operations` | `operations` | `frontend/src/views/OperationsView.vue` | hub accordion برای رابطه‌ها/مدیریت/میانبرها | تبدیل به workspace index و entry point route-level |
| `/account` | `account` | `frontend/src/views/AccountHubView.vue` | hub accordion برای profile/settings/security/notifications | تبدیل به account workspace با subroutes یا section router |
| `/chat` | `messenger` | `frontend/src/views/MessengerView.vue` | پیام‌رسان | route حفظ شود؛ internals ممنوع |
| `/users/:id` | `public-profile` | `frontend/src/views/PublicProfileView.vue` | پروفایل عمومی/مشتری/کاربر | حفظ، اما actions مدیریتی به workspaceهای جدید لینک شوند |
| `/profile` | `profile` | `frontend/src/views/ProfileView.vue` | self profile و owner actions؛ query workspace modal باز می‌کند | profile تمیزتر؛ `workspace` query به route جدید adapter شود |
| `/settings` | `settings` | `frontend/src/views/SettingsView.vue` | sessions/storage/logout accordion | تبدیل به account/security workspace؛ queryهای قدیمی حفظ شوند |
| `/admin` | `admin` | `frontend/src/views/AdminView.vue` | stateful sub-sections با query `section` | تبدیل به admin workspace route-level؛ queryهای قدیمی adapter |
| `/i/:code` | `invite-landing` | `frontend/src/views/InviteLanding.vue` | invite landing عمومی | هماهنگی visual و mobile polish |
| `/register` | `web-register` | `frontend/src/views/WebRegister.vue` | ثبت‌نام وب | هماهنگی visual و error states |
| `/notifications` | `notifications` | `frontend/src/views/NotificationsView.vue` | notification center | workspace کوچک با filter/empty/error بهتر |
| `/share-receive` | `share-receive` | `frontend/src/views/ShareReceiveView.vue` | utility route | حفظ و هماهنگی visual حداقلی |

## 4. ممیزی navigation و menuهای فعلی

### Bottom navigation

فایل: `frontend/src/components/BottomNav.vue`

آیتم‌های فعلی:
- `خانه` -> `/`
- `بازار` -> `/market`; برای accountant مخفی است.
- `پیام‌رسان` -> `/chat`; دارای unread/mention badge است.
- `عملیات` -> `/operations`; routeهای active شامل `operations` و `admin`.
- `حساب` -> `/account`; routeهای active شامل `account`, `profile`, `settings`, `notifications`, `public-profile`.

ریسک:
- بعد از اضافه شدن subrouteهای جدید باید active stateها توسعه پیدا کند تا کاربران در workspaceهای nested مسیر خود را گم نکنند.
- messenger badge نباید با تغییر shell شکسته شود.

### Operations

فایل: `frontend/src/views/OperationsView.vue`

گروه‌های فعلی:
- `relations`: مشتریان و حسابداران، با route به `/profile?workspace=customers/accountants`
- `management`: create invitation, manage users, manage commodities, admin messages, settings
- `shortcuts`: notifications و admin panel

ریسک:
- Operations هنوز فقط hub است و کار واقعی را به profile modal یا admin section پاس می‌دهد.
- customer/accountant management باید از modal خارج شود و workspace مستقل داشته باشد.

### Account

فایل: `frontend/src/views/AccountHubView.vue`

گروه‌های فعلی:
- profile/settings
- sessions/storage
- notifications

ریسک:
- Settings همچنان صفحه accordion مستقل است.
- accountant restriction برای sessions/logout باید هم در UI و هم backend حفظ شود.

### Admin

فایل‌ها:
- `frontend/src/views/AdminView.vue`
- `frontend/src/components/AdminPanel.vue`

sectionهای پشتیبانی‌شده توسط AdminView:
- `create_invitation`
- `create_channel`
- `manage_commodities`
- `manage_users`
- `admin_messages`
- `settings`
- `user_profile`

نکته مهم:
- `create_channel` در `AdminView.vue` route section دارد اما در `AdminPanel.vue` به‌عنوان action قابل مشاهده در landing فعلی دیده نمی‌شود. این یک orphan action است و در workspace جدید باید مقصد و سطح دسترسی روشن داشته باشد.

## 5. ممیزی سطح‌های فعلی

### Dashboard

فایل: `frontend/src/views/DashboardView.vue`

قابلیت‌های فعلی:
- نمایش کاربر و نام/اکانت
- shortcut اعلان‌ها
- shortcut پروفایل
- logout برای non-accountant
- market entry و وضعیت open/closed
- warningهای restricted/inactive/global lock
- معاملات امروز از `/api/trades/my`
- dev/super-admin account switcher از `/api/auth/dev-switch/users` و `/api/auth/dev-switch/:id`

درمان:
- حفظ Home به‌عنوان صفحه وضعیت و شروع کار.
- حذف تراکم operational از Home؛ لینک‌ها به workspaces منتقل شوند.
- account switcher فقط برای همان نقش/claim فعلی و بدون promotion عمومی بماند.

### Profile / Public Profile

فایل‌ها:
- `frontend/src/views/ProfileView.vue`
- `frontend/src/views/PublicProfileView.vue`
- `frontend/src/components/PublicProfile.vue`

قابلیت‌های فعلی:
- avatar update از `/api/auth/me/avatar`
- address update از `/api/auth/me/address`
- profile/public profile loading از `/api/users-public/:id`
- project users directory از `/api/users-public/:id/project-users`
- own/public trade history از `/api/trades/my` یا `/api/trades/with/:id`
- block status/actions از `/api/blocks/check/:id`, `/api/blocks/status`, `/api/blocks/:id`
- owner actions: add/manage customers/accountants
- admin action: user profile/admin settings
- visitor action: message/block

ریسک:
- `PublicProfile.vue` بیش از حد مسئولیت دارد و modalهای رابطه‌ای را mount می‌کند.
- `initialOwnerWorkspace` با query قدیمی modal باز می‌کند. در refactor جدید باید adapter شود، نه اینکه modal primary باقی بماند.

### Customer management

فایل فعلی: `frontend/src/components/OwnerCustomerManagerModal.vue`

قابلیت‌های فعلی:
- ایجاد رابطه مشتری: `POST /api/customers/owner-relations`
- لیست روابط: `GET /api/customers/owner-relations`
- ویرایش سطح/کمیسیون/محدودیت‌ها: `PATCH /api/customers/owner-relations/:id`
- لغو/قطع رابطه: `DELETE /api/customers/owner-relations/:id`
- pending invitation list، copy registration link، cancel/expire
- detail page داخلی modal
- trade history: `GET /api/trades/with/:customer_user_id?limit=20`
- trade stats: `GET /api/customers/owner-relations/:id/trade-stats?days=...`
- session list: `GET /api/customers/owner-relations/:id/sessions`
- terminate session: `DELETE /api/customers/owner-relations/:id/sessions/:session_id`
- commission preview با درصد و مبنای 100 میلیون

درمان:
- تبدیل به `/operations/customers` و `/operations/customers/:relationId`.
- modal فعلی در مرحله migration به adapter/compatibility تبدیل شود.
- تمام زیرمنوهای فعلی به tabs/sections route-level منتقل شوند.

### Accountant management

فایل فعلی: `frontend/src/components/OwnerAccountantManagerModal.vue`

قابلیت‌های فعلی:
- ایجاد رابطه حسابدار: `POST /api/accountants/owner-relations`
- لیست روابط: `GET /api/accountants/owner-relations`
- ویرایش duty description: `PATCH /api/accountants/owner-relations/:id`
- لغو/قطع رابطه: `DELETE /api/accountants/owner-relations/:id`
- pending invitations، copy registration link، cancel/expire
- session list: `GET /api/accountants/owner-relations/:id/sessions`
- terminate session: `DELETE /api/accountants/owner-relations/:id/sessions/:session_id`

درمان:
- تبدیل به `/operations/accountants` و `/operations/accountants/:relationId`.
- session termination فقط برای owner/authorized role؛ accountant خودش نباید session management داشته باشد.

### Settings

فایل: `frontend/src/views/SettingsView.vue`

قابلیت‌های فعلی:
- active sessions از `/api/sessions/active`
- terminate session از `DELETE /api/sessions/:id`
- logout all از `POST /api/sessions/logout-all`
- clear storage/cache با `useChatFileHandler().clearFileCache()`
- logout/forceLogout برای non-accountant
- accountant restriction برای sessions/logout

درمان:
- تبدیل به account/security workspace با cards و sections واضح.
- چون storage cache از chat file handler استفاده می‌کند، این فقط مصرف global utility است و نباید وارد messenger internals شود.

### Notifications

فایل‌ها:
- `frontend/src/views/NotificationsView.vue`
- `frontend/src/stores/notifications.ts`

قابلیت‌های فعلی:
- history fetch از `/api/notifications/?limit=50&offset=0`
- mark all read از `/api/notifications/mark-all-read`
- toggle read از `/api/notifications/:id/read`
- delete one از `DELETE /api/notifications/:id`
- clear all از `DELETE /api/notifications/`
- route-based open action
- chat unread counters در همان store

درمان:
- هماهنگی visual، filters، empty/loading/error states.
- chat counters نباید دستکاری شوند مگر فقط برای shell compatibility.

### Market

فایل‌ها:
- `frontend/src/views/MarketView.vue`
- `frontend/src/components/TradingView.vue`

قابلیت‌های حساس:
- offers list/load از `/api/offers/`
- my offers از `/api/offers/my`
- my trades از `/api/trades/my`
- offer parse از `/api/offers/parse`
- create offer از `POST /api/offers/`
- cancel offer از `DELETE /api/offers/:id`
- cancel all از `POST /api/offers/cancel-all`
- create trade از `POST /api/trades/`
- realtime offer/trade events
- market state/admin current message

درمان:
- در refactor سنگین، market core trading را بازنویسی نکن.
- فقط visual shell، empty/error states، responsive polish، input affordance و consistency انجام شود مگر stage جداگانه benchmark-gated تعریف شود.

## 6. Old-to-New Mapping

| قابلیت فعلی | ورودی فعلی | مقصد جدید | الزام compatibility |
|---|---|---|---|
| مدیریت مشتریان مالک | `/profile?workspace=customers` یا Operations action | `/operations/customers` | query قدیمی redirect یا adapter شود |
| افزودن مشتری | داخل `OwnerCustomerManagerModal` | `/operations/customers?panel=create` | فرم و validation فعلی حفظ شود |
| دعوت‌های pending مشتری | داخل customer modal | `/operations/customers?panel=pending` | copy/cancel لینک‌ها حفظ شود |
| تنظیمات مشتری | detail داخلی modal | `/operations/customers/:relationId` | تمام sections فعلی منتقل شود |
| ویرایش سطح/کمیسیون/limits مشتری | customer detail modal | `/operations/customers/:relationId?section=limits` | نرخ تاریخی کمیسیون backend حفظ شود |
| معاملات مشتری | customer detail modal | `/operations/customers/:relationId?section=trades` | از همان endpoint فعلی استفاده شود |
| آمار مشتری | customer detail modal | `/operations/customers/:relationId?section=stats` | بازه‌های 1/3/7/30/90/180 روز حفظ شود |
| نشست مشتری | customer detail modal | `/operations/customers/:relationId?section=sessions` | owner-only permission حفظ شود |
| قطع رابطه مشتری | customer detail modal | `/operations/customers/:relationId?section=danger` | confirmation واضح و destructive styling |
| مدیریت حسابداران مالک | `/profile?workspace=accountants` یا Operations action | `/operations/accountants` | query قدیمی redirect یا adapter شود |
| افزودن حسابدار | accountant modal | `/operations/accountants?panel=create` | فرم فعلی حفظ شود |
| دعوت‌های pending حسابدار | accountant modal | `/operations/accountants?panel=pending` | copy/cancel حفظ شود |
| تنظیمات حسابدار | detail داخلی modal | `/operations/accountants/:relationId` | duty/session/danger sections حفظ شود |
| نشست حسابدار | accountant detail modal | `/operations/accountants/:relationId?section=sessions` | فقط سرگروه/authorized، نه خود حسابدار |
| ایجاد دعوت | `/admin?section=create_invitation` | `/admin/invitations` | query قدیمی adapter شود |
| ایجاد کانال | `/admin?section=create_channel` hidden support | `/admin/channels` | action باید در Admin workspace قابل مشاهده شود |
| مدیریت کاربران | `/admin?section=manage_users` | `/admin/users` | user profile subroute حفظ شود |
| پروفایل کاربر در admin | `/admin?section=user_profile&user_id=...` | `/admin/users/:id` | query قدیمی adapter شود |
| مدیریت کالاها | `/admin?section=manage_commodities` | `/admin/commodities` | super/admin gate حفظ شود |
| پیام مدیریت | `/admin?section=admin_messages` | `/admin/messages` | super-admin gate حفظ شود |
| تنظیمات سیستم/معاملات | `/admin?section=settings` | `/admin/system` | toast ذخیره و validation حفظ شود |
| نشست‌های حساب | `/settings?section=sessions` | `/account/security/sessions` یا `/account?section=sessions` | accountant hidden/restricted حفظ شود |
| storage/cache | `/settings?section=storage` | `/account/storage` | messenger internals دست‌نخورده |
| اعلان‌ها | `/notifications` | `/notifications` با workspace polish | route فعلی حفظ شود |
| پروفایل خود کاربر | `/profile` | `/profile` | relation manager links به route جدید |
| پروفایل عمومی | `/users/:id` | `/users/:id` | actions حفظ و style هماهنگ |
| بازار | `/market` | `/market` | core trading behavior حفظ شود |

## 7. Role And State Matrix

| نقش/حالت | مشتریان | حسابداران | Admin | Settings/Sessions | Market | Notes |
|---|---|---|---|---|---|---|
| کاربر عادی مالک | مشاهده/ایجاد/مدیریت مشتریان خود | مشاهده/ایجاد/مدیریت حسابداران خود | ندارد مگر role جدا | sessions/logout مجاز | مجاز طبق market access | primary owner workspace |
| حسابدار | نباید owner customer management خودش را ببیند مگر backend اجازه خاص داده باشد | نباید خودمدیریتی حسابدار داشته باشد | ندارد مگر role جدا | sessions/logout ممنوع | market tab مخفی است | session termination فقط توسط سرگروه |
| مشتری سطح 1 | owner tools مخفی | owner tools مخفی | ندارد | طبق user policy | طبق market access | محدودیت‌های معاملاتی ممکن است اعمال شود |
| مشتری سطح 2 | owner tools مخفی | owner tools مخفی | ندارد | طبق user policy | طبق market access؛ ممکن است offer create محدود باشد | commission context در owner workspace |
| مدیر میانی | اگر هم owner است، owner tools | اگر هم owner است، owner tools | invitations/users | sessions طبق non-accountant | طبق policy | admin workspace محدود |
| admin/super-admin | اگر هم owner است، owner tools | اگر هم owner است، owner tools | full or gated sections | sessions طبق non-accountant | طبق policy | create_channel orphan باید روشن شود |
| inactive/restricted | actions حساس disabled یا با پیام علت | disabled یا با پیام علت | طبق backend | ممکن است logout/profile بماند | blocked | dashboard warnings حفظ شود |
| market closed | بی‌اثر | بی‌اثر | بی‌اثر | بی‌اثر | offer input disabled، read views باقی | messaging unrelated |
| empty state | راهنمای action بعدی | راهنمای action بعدی | توضیح نبود دسترسی | توضیح نبود session/cache | empty offers/trades واضح | بدون skeleton بی‌پایان |
| loading/error | skeleton و retry | skeleton و retry | skeleton و retry | inline error/toast | non-blocking retry | همه خطاها قابل فهم فارسی |

## 8. معماری هدف

### Route-level workspaces

Workspaceهای جدید باید route داشته باشند، نه modal primary:
- `/operations/customers`
- `/operations/customers/:relationId`
- `/operations/accountants`
- `/operations/accountants/:relationId`
- `/admin/invitations`
- `/admin/channels`
- `/admin/users`
- `/admin/users/:id`
- `/admin/commodities`
- `/admin/messages`
- `/admin/system`
- `/account/security`
- `/account/storage`

تا پایان مهاجرت:
- routeهای قدیمی حذف نمی‌شوند.
- queryهای قدیمی به مسیرهای جدید هدایت یا داخل adapter باز می‌شوند.
- componentهای قدیمی می‌توانند موقتاً به‌عنوان compatibility wrapper باقی بمانند، اما نباید UX نهایی باشند.

### Data/composable policy

- منطق API فعلی بدون rewrite پرریسک حفظ شود.
- هر workspace جدید یک composable کوچک برای load/mutate/state داشته باشد.
- shared list/card/accordion components از design system استفاده کنند.
- permission visibility از backend جدا نیست؛ UI فقط نمایش را محدود می‌کند، backend authoritative می‌ماند.

## 9. Design System Foundation

توکن‌های فعلی در `frontend/src/assets/main.css` موجودند:
- رنگ‌ها: `--ds-primary-*`, `--ds-success-*`, `--ds-danger-*`, `--ds-warning-*`, `--ds-info-*`
- typography scale: `--ds-font-*`
- spacing: `--ds-page-padding`, `--ds-card-padding`, `--ds-section-gap`
- radius/shadow: `--ds-radius-*`, `--ds-shadow-*`
- primitives: `ds-page`, `ds-page-content`, `ds-accordion`, buttons, fields, loading/empty states

نیازهای heavy refactor:
- تعریف contract برای workspace layout: header، toolbar، content rail، detail panel.
- تعریف `RelationCard`, `ManagementList`, `WorkspaceHeader`, `ActionToolbar`, `InlineNotice`, `StatTile`, `DangerZone`.
- حذف تدریجی CSS پراکنده داخل modalهای بزرگ.
- desktop breakpoint واقعی: روی نمایشگرهای بزرگ عرض محتوا نباید موبایلی و بسیار باریک بماند؛ workspaceها باید master-detail شوند.

## 10. Roadmap مرحله‌ای

### Stage H0 - Baseline Audit And Migration Contract

وضعیت: completed in this document.

خروجی:
- ممیزی route/menu/action
- mapping old-to-new
- role/state matrix
- messenger exclusion list
- modal-to-route migration plan
- acceptance criteria

بدون code runtime.

### Stage H1 - Design System And Workspace Primitives

وضعیت: completed on 2026-06-14.

تغییرات:
- اضافه کردن primitives مشترک غیرپیام‌رسان.
- تعریف workspace responsive layout.
- تعریف shared cards/buttons/toasts/forms برای customer/accountant/admin/account.
- استخراج minimal CSS از صفحات سنگین به shared classes.
- خروجی این stage:
  - `frontend/src/components/workspace/WorkspaceShell.vue`
  - `frontend/src/components/workspace/WorkspaceSection.vue`
  - `frontend/src/components/workspace/WorkspaceActionTile.vue`
  - `frontend/src/components/workspace/WorkspaceNotice.vue`
  - `frontend/src/components/workspace/WorkspaceStatTile.vue`
  - `frontend/src/components/workspace/WorkspaceDangerZone.vue`
  - `frontend/src/components/workspace/index.ts`
  - `frontend/src/components/workspace/WorkspacePrimitives.test.ts`
  - workspace layout/tone/focus/desktop classes in `frontend/src/assets/main.css`

پذیرش:
- هیچ route رفتاری تغییر نکند.
- build و focused unit tests پاس شوند.
- صفحات فعلی با primitives جدید قابل mount باشند.
- هیچ صفحه فعلی در H1 به primitiveهای جدید migrate نشد؛ این تصمیم عمدی است تا behavior change به Stage H2/H3 منتقل شود.

### Stage H2 - Navigation And Compatibility Adapters

وضعیت: completed on 2026-06-14.

تغییرات:
- توسعه router برای routeهای جدید.
- حفظ deep linkهای قدیمی با redirect/adapter.
- اصلاح active state در BottomNav.
- افزودن breadcrumbs/back behavior برای nested workspaces.
- خروجی این stage:
  - routeهای آینده customer/accountant/account/admin به router اضافه شدند.
  - تا قبل از ساخت workspaceهای واقعی، routeهای جدید به سطح‌های فعلی کارکرددار redirect می‌شوند:
    - `/operations/customers` -> `/profile?workspace=customers`
    - `/operations/accountants` -> `/profile?workspace=accountants`
    - `/account/security` -> `/settings?section=sessions`
    - `/account/storage` -> `/settings?section=storage`
    - `/admin/*` routeهای جدید -> `/admin?section=...`
  - `BottomNav.vue` برای route names جدید active-state آماده شد.
  - routeهای قدیمی حذف یا redirect نشدند؛ تغییر جهت adapter به old-to-new بعد از ساخت H4/H5/H6 انجام می‌شود.

پذیرش:
- `/profile?workspace=customers/accountants` همچنان مسیر فعلی کارکرددار را باز می‌کند؛ flip نهایی به مقصد جدید بعد از H4/H5 انجام می‌شود.
- routeهای جدید customer/accountant/account/admin قابل resolve هستند و به مسیر فعلی کارکرددار هدایت می‌شوند.
- `/admin?section=...` همچنان کار می‌کند.
- `/chat` و badgeهای آن دست‌نخورده‌اند.
- در H2 هیچ production deploy انجام نشد؛ این stage با تست و build محلی validate شد تا UI نیمه‌مهاجرت‌شده روی کاربران production منتشر نشود.

### Stage H3 - Operations Workspace Index

وضعیت: completed on 2026-06-14.

تغییرات:
- تبدیل `/operations` از hub ساده به workspace index.
- نمایش role-aware entry cards برای customers/accountants/admin/notifications.
- empty states دقیق برای customer/accountant/admin roles.
- خروجی این stage:
  - `frontend/src/views/OperationsView.vue` از `WorkspaceShell`, `WorkspaceSection`, `WorkspaceActionTile`, `WorkspaceNotice`, و `WorkspaceStatTile` استفاده می‌کند.
  - کارت‌های مشتری/حسابدار به routeهای adapter جدید H2 (`operations-customers`, `operations-accountants`) هدایت می‌شوند.
  - کارت‌های مدیریتی به routeهای adapter جدید H2 (`admin-invitations`, `admin-users`, `admin-commodities`, `admin-messages`, `admin-system`) هدایت می‌شوند.
  - خلاصه وضعیت دسترسی نقش فعلی در aside اضافه شد.
  - ساختار accordion قدیمی فقط در این صفحه حذف شد؛ مسیرها و قابلیت‌های فعلی حفظ شدند.

پذیرش:
- تمام actions فعلی Operations مقصد دارند.
- هیچ action برای نقش غیرمجاز فعال نمی‌شود.
- mobile و desktop layout پایدار است.
- production deploy برای H3 عمداً defer شد تا زنجیره H2-H3-H4 به شکل کنترل‌شده‌تر منتشر شود.

### Stage H4 - Customer Workspace

وضعیت: completed on 2026-06-14.

تغییرات:
- ساخت `/operations/customers`.
- ساخت `/operations/customers/:relationId`.
- انتقال add/pending/manage/detail/trades/stats/sessions/danger از modal به route.
- modal قدیمی فقط compatibility wrapper یا حذف مرحله‌ای بعد از test کامل.
- خروجی این stage:
  - routeهای `operations-customers` و `operations-customers-detail` از redirect موقت H2 خارج و به `CustomerWorkspaceView.vue` وصل شدند.
  - `OwnerCustomerManagerModal.vue` دوحالته شد: modal قدیمی برای profile compatibility حفظ شد و presentation جدید `workspace` بدون backdrop/header داخلی در route مستقل render می‌شود.
  - انتخاب مشتری در workspace به `/operations/customers/:relationId` هدایت می‌شود و بازگشت detail به `/operations/customers` برمی‌گردد.
  - add customer، pending invitation copy/cancel، edit limits/commission، stats، trades، sessions، unlink/cancel و viewport toast از همان منطق تست‌شده قبلی استفاده می‌کنند.
  - production deploy برای H4 هم عمداً defer شد تا H2-H4 به‌صورت گروهی و بعد از validation گسترده‌تر منتشر شود.

پذیرش:
- add customer با tier1/tier2 و commission preview حفظ شود.
- pending invitation copy/cancel حفظ شود.
- edit limits/commission ذخیره و toast viewport-level نشان دهد.
- stats با نرخ کمیسیون تاریخی معامله محاسبه شود؛ فرمت هزار/میلیون درست باشد.
- session termination و unlink/cancel حفظ شود.
- list با 500 رابطه قابل استفاده باشد؛ pagination/search/virtualization اگر لازم شد اضافه شود.

### Stage H5 - Accountant Workspace

وضعیت: completed on 2026-06-14.

تغییرات:
- ساخت `/operations/accountants`.
- ساخت `/operations/accountants/:relationId`.
- انتقال add/pending/manage/detail/sessions/danger از modal به route.
- account/session restrictions روشن و backend-compatible.
- خروجی این stage:
  - routeهای `operations-accountants` و `operations-accountants-detail` از redirect موقت H2 خارج و به `AccountantWorkspaceView.vue` وصل شدند.
  - `OwnerAccountantManagerModal.vue` دوحالته شد: modal قدیمی برای profile compatibility حفظ شد و presentation جدید `workspace` بدون backdrop/header داخلی در route مستقل render می‌شود.
  - انتخاب حسابدار در workspace به `/operations/accountants/:relationId` هدایت می‌شود و بازگشت detail به `/operations/accountants` برمی‌گردد.
  - add accountant، pending invitation copy/cancel، duty edit، session termination و unlink/cancel از همان endpointها و permission flow فعلی استفاده می‌کنند.
  - production deploy برای H5 هم عمداً defer شد تا H2-H5 بعد از validation گروهی منتشر شود.

پذیرش:
- add accountant، pending copy/cancel، duty edit، session termination و unlink حفظ شود.
- حسابدار به self session/logout access پیدا نکند.
- relation cards با customer workspace هم‌خانواده باشند.

### Stage H6 - Admin Workspace

وضعیت: completed on 2026-06-14.

تغییرات:
- route-level admin sections.
- visible action برای create channel.
- تبدیل `AdminView` state machine به route-driven workspace.
- حفظ `section` queryهای قدیمی.
- خروجی این stage:
  - routeهای `/admin/invitations`, `/admin/channels`, `/admin/users`, `/admin/users/:id`, `/admin/commodities`, `/admin/messages`, و `/admin/system` از redirect موقت خارج و مستقیم به `AdminView.vue` وصل شدند.
  - `AdminView.vue` اکنون route name/params را منبع اصلی section می‌داند و queryهای legacy مانند `?section=create_invitation` و `?section=user_profile&user_id=...` همچنان کار می‌کنند.
  - navigation داخلی admin برای sectionهای route-level از `router.push()` به routeهای جدید استفاده می‌کند و profile کاربر منتخب به `/admin/users/:id` منتقل شد.
  - action «ساخت کانال» به منوی مدیر ارشد اضافه شد؛ مدیر میانی فقط دعوت‌نامه و مدیریت کاربران را می‌بیند و routeهای super-admin-only برای او به menu برمی‌گردند.
  - production deploy برای H6 هم عمداً defer شد تا H2-H6 بعد از validation گروهی منتشر شود.

پذیرش:
- middle manager فقط invitations/users ببیند.
- super admin system/settings/messages/commodities/channels را مطابق policy ببیند.
- admin user profile subroute جایگزین state داخلی شود.
- TradingSettings save/delete toast viewport-level داشته باشد.

### Stage H7 - Account, Settings, Notifications, Profile Polish

وضعیت: completed on 2026-06-14.

تغییرات:
- Account security/storage route یا workspace sections.
- Settings قدیمی به adapter تبدیل شود.
- Notifications با filter، state و action hierarchy بهتر.
- PublicProfile از owner-management modal dependency آزاد شود.
- خروجی این stage:
  - routeهای `/account/security`, `/account/storage`, و `/account/notifications` از redirect موقت خارج و مستقیم به `SettingsView.vue` / `NotificationsView.vue` وصل شدند.
  - `AccountHubView` برای امنیت، حافظه/داده، و اعلان‌ها به routeهای جدید H2 هدایت می‌کند.
  - `SettingsView.vue` هم route nameهای جدید و هم query legacy مثل `?section=storage` / `?section=sessions` را پشتیبانی می‌کند و عنوان صفحه را براساس workspace فعال نمایش می‌دهد.
  - `NotificationsView.vue` فیلترهای همه/خوانده‌نشده/خوانده‌شده و empty state مربوط به فیلتر گرفت، در حالی که read/delete/clear/open route حفظ شد.
  - `PublicProfile.vue` از dependency مستقیم به modalهای owner customer/accountant آزاد شد و owner actionها را به workspaceهای H4/H5 emit می‌کند.
  - `ProfileView.vue` و `PublicProfileView.vue` این actionها را به routeهای `/operations/customers` و `/operations/accountants` هدایت می‌کنند.
  - handoff تنظیمات admin از profile عمومی به route جدید `/admin/users/:id` منتقل شد.
  - production deploy برای H7 هم عمداً defer شد تا validation/deploy گروهی بعد از stageهای بعدی انجام شود.

پذیرش:
- accountant restrictions حفظ شود.
- storage/cache action کار کند.
- notification read/delete/clear/open route حفظ شود.
- profile/public profile actions حفظ شوند.

### Stage H8 - Dashboard And Market Shell Harmonization

وضعیت: completed on 2026-06-14.

تغییرات:
- Dashboard task-focused و سبک‌تر.
- Market shell visual harmonization، empty/error/loading polish.
- core trading mutation/realtime بدون rewrite.
- خروجی این stage:
  - `DashboardView.vue` یک خلاصه سه‌گانه و قابل اسکن برای وضعیت حساب، وضعیت بازار، و کار امروز گرفت تا کاربر در نگاه اول وضعیت عملیاتی خود را ببیند.
  - هشدارهای account inactive / trading restricted و جدول معاملات امروز بدون تغییر رفتاری حفظ شدند.
  - `MarketView.vue` یک shell هدر استانداردتر برای وضعیت بازار، تعداد لفظ‌های فیلترشده، و توضیح وضعیت باز/بسته گرفت.
  - tabهای بازار از نظر `role="tablist"` / `role="tab"` / `aria-selected` استانداردتر شدند و توضیحات کوتاه فیلترها اضافه شد.
  - action bar بازار برای recent offers و ارسال لفظ `aria-expanded` / `aria-controls` / `aria-label` گرفت.
  - مسیرهای create/parse/confirm/cancel/trade و WebSocket runtime events بدون rewrite حفظ شدند.
  - production deploy برای H8 هم عمداً defer شد تا بعد از H9/H10 validation گروهی انجام شود.

پذیرش:
- offer create/parse/cancel/trade realtime behavior تغییر نکند.
- market closed/open state درست باشد.
- dashboard warnings و today's trades حفظ شوند.

### Stage H9 - Responsive, Accessibility, Regression

وضعیت: completed on 2026-06-14.

تغییرات:
- desktop/tablet/mobile QA.
- keyboard navigation و focus-visible.
- ARIA برای accordions/tabs/menus.
- no overlap/no clipped text/no tiny tap targets.
- focused unit/e2e regression.
- خروجی این stage:
  - کنترل profile در `DashboardView.vue` از `div` کلیک‌پذیر به `button` واقعی با `aria-label` تبدیل شد تا keyboard/focus behavior استاندارد شود.
  - دکمه‌های top bar، hero، و modal switcher در dashboard `type`/`aria-label` کامل‌تر و focus-visible محلی گرفتند.
  - tabهای فیلتر بازار در `MarketView.vue` علاوه بر `role="tab"`، navigation با `ArrowLeft` / `ArrowRight` / `ArrowUp` / `ArrowDown` / `Home` / `End` گرفتند.
  - کنترل‌های جدید بازار و recent-offers focus-visible محلی گرفتند و tap targetهای فیلتر بازار حفظ شدند.
  - focused regression شامل Dashboard, Market, Account, Settings, Notifications, Operations, Customer/Accountant/Admin workspaces, PublicProfileView, route index, BottomNav و یک smoke برای Messenger shell اجرا شد.
  - e2e smoke کوتاه `auth.spec.ts` روی Chromium اجرا شد تا protected-route redirect، dev-login dashboard، و deep-linkهای profile/settings با session موجود پوشش داده شود.
  - production deploy برای H9 هم defer شد؛ deploy نهایی در H10 انجام می‌شود.

پذیرش:
- تمام routeهای non-messenger در mobile و desktop inspect شوند.
- old deep links پاس شوند.
- messenger route smoke فقط برای عدم شکست shell اجرا شود، نه refactor داخلی.

### Stage H10 - Final Handoff And Production Readiness

تغییرات:
- گزارش نهایی old/new مسیرها.
- لیست فایل‌های قدیمی compatibility که هنوز باقی‌اند.
- benchmark/test summary.
- debtها و post-release tasks.

پذیرش:
- مسیر حذف compatibility wrappers بعد release روشن باشد.
- docs و copilot summary کامل باشند.
- production build و deploy طبق قاعده پروژه انجام شود.

## 11. Acceptance Criteria By Surface

### Customer workspace

- فرم افزودن مشتری یک مسیر واضح و مستقل دارد.
- مشتری سطح 2 commission field، step controls و preview دارد.
- pending invitations جدا از active customers دیده می‌شوند.
- شماره موبایل کامل و قابل خواندن است.
- هر مشتری detail route دارد.
- edit/save feedback viewport-level است، نه فقط بالای صفحه.
- trade stats فرمت مبلغ درست دارد.
- destructive actions جدا و قابل تشخیص‌اند.

### Accountant workspace

- create/manage/pending/detail/sessions/danger از customer workspace الگو می‌گیرد.
- هیچ duplicate role tag یا duplicate metadata دیده نمی‌شود.
- سرگروه می‌تواند نشست حسابدار را terminate کند.
- حسابدار خودش session/logout management نمی‌بیند.

### Admin workspace

- هیچ section پنهان بدون entry point نماند.
- role gates در UI و backend هم‌راستا باشند.
- create invitation/channel/users/commodities/messages/settings مسیر واضح دارند.
- user profile management deep link stable است.

### Account/settings

- sessions برای accountant مخفی/محدود است.
- logout برای accountant در UI نمایش داده نشود.
- storage/cache پیام موفق/خطا دارد.
- network/loading/error states قابل فهم‌اند.

### Notifications

- list، unread، read/unread، delete، clear و open route حفظ شود.
- empty state و loading state استاندارد باشد.
- unread state visual واضح باشد.

### Dashboard/Market

- dashboard در نگاه اول وضعیت حساب، وضعیت بازار و کار امروز را نشان دهد.
- market actions تغییری در business logic ندهند.
- offer/trade modals اگر باقی ماندند باید accessibly labeled باشند.

## 12. Testing Plan

حداقل تست هر stage:
- `git diff --check`
- focused unit tests مربوط به فایل‌های تغییر یافته
- `npm run build` در frontend برای stages دارای frontend runtime change
- e2e smoke برای deep linkهای مهم بعد از route migration

تست‌های کلیدی نهایی:
- normal owner: customer/accountant create/manage/detail
- accountant: عدم دسترسی به sessions/logout و market tab
- middle manager: admin limited sections
- super admin: full admin sections و create channel visible
- customer tier1/tier2: profile/market restrictions
- notifications read/delete/clear
- old deep links:
  - `/profile?workspace=customers`
  - `/profile?workspace=accountants`
  - `/settings?section=sessions`
  - `/settings?section=storage`
  - `/admin?section=create_invitation`
  - `/admin?section=create_channel`
  - `/admin?section=manage_users`
  - `/admin?section=manage_commodities`
  - `/admin?section=admin_messages`
  - `/admin?section=settings`
  - `/admin?section=user_profile&user_id=...`

## 13. ریسک‌ها و کنترل‌ها

| ریسک | کنترل |
|---|---|
| گم شدن actionهای قدیمی | old-to-new mapping قبل از هر stage چک شود |
| شکستن messenger shell | messenger internals ممنوع؛ فقط smoke route |
| ایجاد UI زیبا اما کند | listهای بزرگ با pagination/virtualization و benchmark محدود |
| dual-state بین modal قدیمی و route جدید | adapterها کوتاه‌مدت؛ source of truth یکی باشد |
| role leak در UI | frontend visibility + backend authoritative checks |
| route migration شکستن bookmarkها | redirect/adapter تا پایان release |
| CSS drift | primitives مشترک و محدود کردن CSS محلی |

## 14. فایل‌های اصلی درگیر در مراحل بعد

Non-messenger frontend:
- `frontend/src/router/index.ts`
- `frontend/src/components/BottomNav.vue`
- `frontend/src/assets/main.css`
- `frontend/src/views/OperationsView.vue`
- `frontend/src/views/AccountHubView.vue`
- `frontend/src/views/ProfileView.vue`
- `frontend/src/views/PublicProfileView.vue`
- `frontend/src/components/PublicProfile.vue`
- `frontend/src/components/OwnerCustomerManagerModal.vue`
- `frontend/src/components/OwnerAccountantManagerModal.vue`
- `frontend/src/views/AdminView.vue`
- `frontend/src/components/AdminPanel.vue`
- `frontend/src/views/SettingsView.vue`
- `frontend/src/views/NotificationsView.vue`
- `frontend/src/views/DashboardView.vue`
- `frontend/src/views/MarketView.vue`
- `frontend/src/components/TradingView.vue`

Backend/API ممکن است فقط برای حفظ actionها یا data shapeهای workspace لازم شود:
- `api/routers/customers.py`
- `api/routers/accountants.py`
- `api/routers/users.py`
- `api/routers/notifications.py`
- `api/routers/admin*.py`
- `api/routers/trading*.py`
- schemas مرتبط با relation/session/stats

Messenger files intentionally excluded:
- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/**`
- `frontend/src/composables/chat/**`
- `frontend/src/services/chat/**`

## 15. Stage 0 نتیجه

Stage 0 فقط ممیزی و برنامه‌ریزی است. هیچ کد runtime، route، component یا پیام‌رسان در این stage تغییر نمی‌کند. مرحله بعدی منطقی Stage H1 است: ایجاد foundation مشترک design/workspace primitives، بدون تغییر رفتاری.
