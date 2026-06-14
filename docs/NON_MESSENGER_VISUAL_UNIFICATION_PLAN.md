# نقشه بازطراحی واقعی UI/UX بخش‌های غیرپیام‌رسان

آخرین به‌روزرسانی: 2026-06-14
وضعیت: Stage 5 تکمیل شد؛ اجرای Stage 6 به بعد هنوز شروع نشده است.
قاعده مهم: production deploy در این roadmap ممنوع است مگر مالک پروژه صریحاً درخواست کند.

## 1. هدف محصول

هدف این سند خروج از وضعیت «route migration با ظاهر قدیمی» و رسیدن به یک وب‌اپ فارسی RTL یکپارچه، حرفه‌ای، زیبا، mobile-first و قابل استفاده روی desktop است. refactor قبلی H0-H10 مسیرها و workspaceهای اولیه را ساخت، اما هنوز در چند سطح مهم، UI اصلی از همان کامپوننت‌های قدیمی، accordion-heavy و modal-oriented تغذیه می‌شود.

تعریف موفقیت:
- کاربر حس نکند منوها و زیرمنوها فقط داخل route جدید گذاشته شده‌اند.
- Customer Workspace و Accountant Workspace دیگر ظاهر modal قدیمی نداشته باشند.
- Profile، Public Profile، Admin، Settings، Notifications، Auth/Utility routes و Market shell زبان بصری مشترک داشته باشند.
- تمام actionهای فعلی، permissionها، deep linkها و API contractها حفظ شوند.
- پیام‌رسان داخلی دست‌نخورده بماند.

## 2. محدوده و ممنوعیت‌ها

داخل محدوده:
- Dashboard
- Market shell و wrapperهای UI بازار، بدون تغییر core trading logic
- Operations
- Customer Workspace
- Accountant Workspace
- Account Hub
- Settings، sessions، storage
- Notifications
- Profile و Public Profile
- Admin workspace و تمام subviewها
- Trading/System settings
- User management
- Commodity management
- Invitation creation
- Channel creation
- Admin messages
- Login
- Setup password
- Invite landing
- Web register
- Share receive
- PWA install overlay
- App toasts
- Loading، empty، error، success و destructive states
- Forms، validation، buttons، badges، chips، tabs، filters، cards، lists و detail panels

خارج از محدوده:
- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/**`
- `frontend/src/composables/chat/**`
- `frontend/src/services/chat/**`
- message list، composer، search، room UI، upload/download/media، virtualization و messenger event handling

کار مجاز مرتبط با پیام‌رسان:
- حفظ route `/chat`
- حفظ آیتم پیام‌رسان در navigation
- حفظ badgeهای unread/mention
- smoke test برای mount شدن shell پیام‌رسان

## 3. وضعیت واقعی فعلی پس از H0-H10

شواهد اصلی از ممیزی فعلی:

| مورد | وضعیت فعلی | نتیجه |
| --- | --- | --- |
| `CustomerWorkspaceView.vue` | حدود 93 خط؛ فقط `WorkspaceShell` و `OwnerCustomerManagerModal presentation="workspace"` را mount می‌کند | workspace واقعی نیست؛ wrapper است |
| `AccountantWorkspaceView.vue` | حدود 93 خط؛ فقط `WorkspaceShell` و `OwnerAccountantManagerModal presentation="workspace"` را mount می‌کند | workspace واقعی نیست؛ wrapper است |
| `OwnerCustomerManagerModal.vue` | حدود 2692 خط؛ create/list/detail/trades/stats/sessions/danger و CSS محلی زیاد | باید به route-native components و composable split شود |
| `OwnerAccountantManagerModal.vue` | حدود 1967 خط؛ create/list/detail/sessions/danger و CSS محلی زیاد | باید split شود |
| `PublicProfile.vue` | حدود 2909 خط؛ profile، directory، history، relation cards، owner/admin/visitor actions و accordionهای متعدد | نیاز به split و حذف emoji/action drift دارد |
| `DashboardView.vue` | حدود 1483 خط؛ از H8 بهتر شده اما CSS و layout محلی زیاد دارد | نیاز به polish و token alignment |
| `MarketView.vue` | حدود 1746 خط؛ trading logic حساس و UI shell ترکیبی | فقط visual harmonization کم‌ریسک |
| `AdminView.vue` + admin components | route-driven شده اما چند subview همچنان old component inside route است | نیاز به visual redesign route-native |
| Design system | `frontend/src/assets/main.css` tokenهای `--ds-*` و چند workspace primitive دارد | هنوز `components/ui/` کامل وجود ندارد |
| Small surfaces | Login، Invite، Register، Share، Setup Password و PWA overlay هرکدام استایل محلی دارند | باید وارد design system شوند |

## 4. مشکلات بصری مشترک

- استفاده بیش از حد از `ds-accordion` برای detail navigation، مخصوصاً customer/accountant/profile/settings/admin.
- وجود actionهای متنی/emoji مثل `👤`، `💬`، `⚙`، `🔔`، `❌`، `📦` و `👥` در سطوح غیرپیام‌رسان.
- hard-coded colorها و shadowها در feature componentها به جای semantic tokens.
- کلاس‌های محلی برای button/input/card که رفتار یکسان ندارند.
- desktop در بعضی مسیرها هنوز حس mobile-centered narrow layout دارد.
- loading/empty/error/success stateها در مسیرهای کوچک یکپارچه نیستند.
- destructive actions همیشه در danger zone واحد و قابل پیش‌بینی نیستند.
- form label/hint/error spacing در صفحات مختلف یکسان نیست.
- safe-area پایین و fixed/bottom UI باید روی PWA موبایل مجدد بررسی شود.

## 5. ممیزی سطح‌به‌سطح و معیار پذیرش

| سطح | فایل‌های فعلی | مشکل فعلی | مسیر old-to-new | معیار پذیرش |
| --- | --- | --- | --- | --- |
| Dashboard | `frontend/src/views/DashboardView.vue` | ظاهر بهتر شده اما CSS محلی و کارت‌های task/status هنوز کاملاً design-system نیستند | حفظ route `/`، تبدیل overview/shortcuts/trades به `AppPage`، `AppMetricCard` و `AppActionCard` | در 360 تا 1440 بدون overflow؛ shortcuts واضح؛ today trades خوانا |
| Market shell | `frontend/src/views/MarketView.vue`, `OffersList.vue`, `TradingView.vue` | UI بازار ترکیبی از استایل‌های قدیم و جدید؛ emoji در `TradingView.vue` | حفظ trading runtime؛ فقط header/tabs/input/recent/errors با shared components | create/parse/confirm/cancel بدون تغییر؛ filter tabs keyboard-friendly |
| Operations | `frontend/src/views/OperationsView.vue` | hub بهتر شده اما هنوز final command center نیست | cards role-aware با `AppWorkspace` و `AppActionCard` | بدون متن transitional؛ middle/admin/superadmin visibility درست |
| Customer Workspace | `CustomerWorkspaceView.vue`, `OwnerCustomerManagerModal.vue` | wrapper around old modal؛ nested accordions primary navigation | split به components route-native و composable؛ list/detail master-detail؛ tabs در detail | `presentation="workspace"` core نباشد؛ create/list/pending/detail/trades/stats/sessions/danger حفظ شود |
| Accountant Workspace | `AccountantWorkspaceView.vue`, `OwnerAccountantManagerModal.vue` | wrapper around old modal؛ nested accordions | split route-native؛ master-detail؛ tabs مشخصات/شرح وظیفه/نشست‌ها/اقدامات حساس | add/pending/copy/cancel/edit duty/session/unlink حفظ شود |
| Account Hub | `AccountHubView.vue` | accordion برای سه گروه؛ قابل قبول ولی هنوز design-system primitive محدود | تبدیل به cards و route-native summaries؛ accordion فقط اگر واقعاً مناسب باشد | accountant restrictions واضح؛ sessions/logout برای accountant پنهان/غیرفعال طبق قانون |
| Settings/Sessions/Storage | `SettingsView.vue` | accordion-heavy و legacy route/shared | sectionهای route-aware با shared form/card/toast | save/delete feedback viewport-level؛ sessions polished؛ storage state واضح |
| Notifications | `NotificationsView.vue` | tabها بهتر شده اما list/action states هنوز محلی است | `AppTabs`/`AppFilterChips`، list item واحد، empty per filter | unread/read hierarchy، delete/clear confirm، route open محفوظ |
| Profile self | `ProfileView.vue`, `UserProfile.vue`, `PublicProfile.vue` | emoji icons، action styles پراکنده، accordionهای متعدد | action grid با icon components؛ owner actions به workspace route | settings/message/history/project users/relation actions حفظ شود |
| Public Profile | `PublicProfileView.vue`, `PublicProfile.vue` | فایل بزرگ و accordion-heavy؛ visitor/admin/owner modes در یک بدنه | split به header/actions/stats/relations/history/directory panels | public/self/customer context یک زبان بصری داشته باشند |
| Admin Workspace | `AdminView.vue`, `AdminPanel.vue` | route-driven شده اما subviewها old component hosted هستند | route-native admin shell، breadcrumbs، section cards | create channel visible where allowed؛ middle manager scope درست |
| User management | `UserManager.vue` | جدول/لیست و actionهای محلی | `AdminUsersList`, `AdminUserDetail`, `AppToolbar`, `AppListItem` | search/filter/actions قابل اسکن و permission-safe |
| Commodity management | `CommodityManager.vue` | emoji و local buttons/errors | shared cards/forms/danger | create/edit/delete aliases readable و destructive جدا |
| Trading/System settings | `TradingSettings.vue` | accordion زیاد و form density قدیمی | tabs/section cards برای دعوت، offer، expiry، security، schedule، calendar | تمام default markers و save behavior حفظ شود |
| Invitation creation | `CreateInvitationView.vue` | success/error با emoji، فرم محلی | shared form، result card، copy action | validation messages استاندارد؛ لینک دعوت copyable |
| Channel creation | `CreateChannelView.vue` | فایل بزرگ، modal/form local، upload/avatar flow حساس | route-native admin create-channel form با shared upload field | entry point visible؛ upload/avatar بدون regression |
| Admin messages | `AdminMessagesView.vue`, `AdminBroadcastModal.vue` | history accordion و modal محلی | broadcast panel، history list، responsive dialog | send/history/delete/access preserved |
| Login | `LoginView.vue` | صفحه بزرگ با style محلی و micro-stateهای جدا | auth shell مشترک، form field و CTA واحد | OTP/login/error/loading mobile-safe |
| Setup password | `SetupPassword.vue` | فرم کوچک اما جدا از auth shell | shared auth page/form | validation/readability حفظ شود |
| Invite landing | `InviteLanding.vue` | error emoji و stateهای ساده | public utility shell، status card | loading/error/expired/success واضح |
| Web register | `WebRegister.vue` | error emoji و form محلی | public utility shell، form/shared validation | register flow حفظ شود |
| Share receive | `ShareReceiveView.vue` | utility route محلی | compact utility state با shared loading/error/action | share target behavior حفظ شود |
| PWA install overlay | `PWAInstallOverlay.vue` | overlay مستقل | `AppResponsiveDialog` یا bottom sheet shared | iOS/Android copy و safe-area درست |
| App toasts | `AppToasts.vue` | toast مستقل | migrate به `AppToast` با semantic tone | viewport-level feedback یکسان |
| Loading skeletons | `LoadingSkeleton.vue`, global `.ds-loading-state` | سطحی و پراکنده | `AppLoadingState` و skeleton tokens | per surface consistent |
| Empty/error/destructive states | پراکنده | copy/style/action متفاوت | `AppEmptyState`, `AppErrorState`, `AppDangerZone`, `AppConfirmDialog` | هیچ action حساس بدون confirm واضح نباشد |

## 6. فایل‌ها و کامپوننت‌هایی که باید split شوند

### Customer Workspace

مقصد پیشنهادی: `frontend/src/components/customer-workspace/` و `frontend/src/composables/useOwnerCustomers.ts`

تقسیم لازم:
- `CustomerWorkspaceHeader.vue`
- `CustomerOverviewStats.vue`
- `CustomerCreatePanel.vue`
- `CustomerPendingInvitations.vue`
- `CustomerList.vue`
- `CustomerCard.vue`
- `CustomerDetailView.vue`
- `CustomerDetailHeader.vue`
- `CustomerDetailTabs.vue`
- `CustomerProfilePanel.vue`
- `CustomerLimitsForm.vue`
- `CustomerTradesPanel.vue`
- `CustomerStatsPanel.vue`
- `CustomerSessionsPanel.vue`
- `CustomerDangerZone.vue`

`OwnerCustomerManagerModal.vue` بعد از مهاجرت باید فقط compatibility wrapper برای modal/deep legacy باشد.

### Accountant Workspace

مقصد پیشنهادی: `frontend/src/components/accountant-workspace/` و `frontend/src/composables/useOwnerAccountants.ts`

تقسیم لازم:
- `AccountantWorkspaceHeader.vue`
- `AccountantOverviewStats.vue`
- `AccountantCreatePanel.vue`
- `AccountantPendingInvitations.vue`
- `AccountantList.vue`
- `AccountantCard.vue`
- `AccountantDetailView.vue`
- `AccountantDetailHeader.vue`
- `AccountantDetailTabs.vue`
- `AccountantProfilePanel.vue`
- `AccountantDutyForm.vue`
- `AccountantSessionsPanel.vue`
- `AccountantDangerZone.vue`

`OwnerAccountantManagerModal.vue` بعد از مهاجرت باید فقط compatibility wrapper باشد.

### Profile/Public Profile

تقسیم پیشنهادی:
- `ProfileHeaderCard.vue`
- `ProfileActionGrid.vue`
- `ProfileRelationSummary.vue`
- `ProfileTradeHistoryPanel.vue`
- `ProfileProjectUsersPanel.vue`
- `ProfileCustomerContextBanner.vue`
- `ProfileAvatarAddressForm.vue`
- `PublicProfileVisitorActions.vue`
- `PublicProfileAdminActions.vue`

### Admin

تقسیم پیشنهادی:
- `AdminWorkspaceShell.vue`
- `AdminSectionHeader.vue`
- `AdminInvitationsPanel.vue`
- `AdminChannelsPanel.vue`
- `AdminUsersPanel.vue`
- `AdminUserDetailPanel.vue`
- `AdminCommoditiesPanel.vue`
- `AdminMessagesPanel.vue`
- `AdminSystemSettingsPanel.vue`

## 7. Design System مورد نیاز

مسیر پیشنهادی: `frontend/src/components/ui/`

فقط کامپوننت‌هایی ساخته شوند که در همان stage واقعاً مصرف می‌شوند.

اولویت ساخت:
- `AppPage`
- `AppPageHeader`
- `AppWorkspace`
- `AppWorkspaceHeader`
- `AppWorkspaceBody`
- `AppMasterDetail`
- `AppTabs`
- `AppSegmentedControl`
- `AppToolbar`
- `AppSearchField`
- `AppFilterChips`
- `AppCard`
- `AppSectionCard`
- `AppActionCard`
- `AppMetricCard`
- `AppListItem`
- `AppStatusBadge`
- `AppButton`
- `AppIconButton`
- `AppFormField`
- `AppInput`
- `AppSelect`
- `AppTextarea`
- `AppNumberStepper`
- `AppEmptyState`
- `AppLoadingState`
- `AppErrorState`
- `AppToast`
- `AppDangerZone`
- `AppConfirmDialog`
- `AppResponsiveDialog`

Tokenهای لازم در `frontend/src/assets/main.css` یا فایل token جدا:
- semantic colors و role/tone colors
- typography scale مخصوص فارسی
- spacing scale
- radius scale
- elevation/shadow scale
- focus ring
- z-index layers
- safe-area variables
- page/workspace widths
- master/detail widths
- mobile bottom nav height
- touch target sizes
- transition durations
- reduced-motion behavior

## 8. CSS محلی که باید حذف یا محدود شود

اولویت حذف/جایگزینی:
- button/input/card styles داخل `OwnerCustomerManagerModal.vue`
- button/input/card styles داخل `OwnerAccountantManagerModal.vue`
- `.menu-button-icon` و action styles داخل `PublicProfile.vue` و `UserProfile.vue`
- admin action و accordion styles داخل `AdminPanel.vue`
- form/action styles داخل `TradingSettings.vue`
- emoji/status styles داخل `CommodityManager.vue`, `CreateInvitationView.vue`, `InviteLanding.vue`, `WebRegister.vue`
- local auth card/layout styles داخل `LoginView.vue`
- local toast/overlay styles داخل `AppToasts.vue` و `PWAInstallOverlay.vue`

قاعده:
- CSS محلی featureها فقط layout خاص همان feature را نگه دارد.
- رنگ، border، shadow، button/input/card/tabs/list/badge باید از token و shared component بیاید.

## 9. Emoji replacement plan

موارد قطعی که باید با icon component جایگزین شوند:
- Profile/PublicProfile: `👤`, `💬`, `⚙`, `🔔`, `👥`
- Customer/accountant manager: symbolهای دستی در `.menu-button-icon`
- Trading/Market wrapper: `📊`, `❌`, `📦`, `👤`
- Commodity manager: `📦`, `❌`
- Invite/Register/Login utility states: `✅`, `❌`
- MainMenu: `👤`, `💬`, `⚙️`
- JalaliDatePicker: `☉`, `⌄` اگر در UI نهایی visible باشد

کتابخانه ترجیحی: `lucide-vue-next` چون پروژه قبلاً از icon componentها استفاده کرده است.

## 10. Breakpoint و PWA QA matrix

Mobile viewportها:
- 360
- 375
- 390
- 414
- 430

Tablet/Desktop:
- 768
- 1024
- 1280
- 1440

موارد بررسی:
- هیچ horizontal overflow نباشد.
- bottom navigation و fixed bars با `env(safe-area-inset-bottom)` تداخل نداشته باشند.
- keyboard موبایل form CTAها را نپوشاند.
- touch target حداقل 44px و ترجیحاً 48px باشد.
- Persian text clip نشود.
- desktop برای customer/accountant/admin از master-detail/split layout استفاده کند.
- reduced motion محترم شمرده شود.

## 11. Tiny surfaces که نباید فراموش شوند

- Login loading/error/OTP states
- Setup password validation states
- Invite landing expired/error/success states
- Web register error/success states
- Share receive loading/error/empty states
- PWA install overlay
- App toasts
- HelpPopover
- JalaliDatePicker shell
- Offer preview modal
- Admin broadcast modal
- Session approval modal
- Loading skeletons
- Empty states
- Error states
- Confirmation dialogs
- Inline hints و validation messages
- Disabled states

## 12. Stage roadmap اجرایی

### Stage 0 - Visual Audit And Plan

وضعیت: completed on 2026-06-14.

خروجی:
- همین سند.
- ممیزی سطح‌ها، legacy components، mapping، split plan، CSS debt، emoji debt، breakpoints و acceptance criteria.

### Stage 1 - Design System Completion

وضعیت: completed on 2026-06-14.

هدف:
- ساخت `frontend/src/components/ui/` با primitiveهای واقعاً مصرف‌شده.
- تکمیل tokens برای safe-area، z-index، page widths، focus و reduced-motion.
- اضافه کردن story/test سبک برای shared primitives در صورت وجود الگوی تست محلی.

خروجی انجام‌شده:
- مسیر `frontend/src/components/ui/` ساخته شد.
- primitiveهای پایه اضافه شدند: `AppButton`, `AppIconButton`, `AppCard`, `AppSectionCard`, `AppActionCard`, `AppMetricCard`, `AppStatusBadge`, `AppTabs`, `AppFormField`, `AppInput`, `AppSelect`, `AppTextarea`, `AppEmptyState`, `AppErrorState`, `AppLoadingState`, `AppDangerZone`, `AppListItem`, `AppConfirmDialog`.
- export مرکزی `frontend/src/components/ui/index.ts` اضافه شد.
- tokenهای safe-area، z-index، touch target، bottom nav height و transition duration به `frontend/src/assets/main.css` اضافه شدند.
- کلاس‌های shared `ui-*` برای button/card/action/metric/badge/tabs/form/empty/error/loading/danger/list/dialog اضافه شدند و همه از tokenهای موجود `--ds-*` استفاده می‌کنند.
- `frontend/src/views/OperationsView.vue` به‌عنوان اولین surface واقعی از `AppButton`, `AppActionCard`, `AppMetricCard`, `AppStatusBadge` استفاده می‌کند.
- متن transitional «مسیر مهاجرت» از aside عملیات حذف شد و با badgeهای وضعیت دسترسی جایگزین شد.
- تست `frontend/src/components/ui/AppPrimitives.test.ts` اضافه شد.

پذیرش:
- حداقل button/card/input/tabs/badge/list/empty/error/danger/dialog primitives آماده و در یک surface مصرف شوند.
- `git diff --check` و تست shared primitives پاس شود.

اعتبارسنجی Stage 1:
- `npm run test:unit:run -- AppPrimitives.test.ts OperationsView.test.ts` پاس شد: `2` فایل، `8/8` تست.
- `npm run build` پاس شد؛ warningهای chunk-size موجود همچنان debt جداگانه هستند.

مراحل باقی‌مانده بعد از Stage 5:
- Stage 6: بازطراحی Admin Workspace.
- Stage 7: یکپارچه‌سازی small surfaces و micro UI.
- Stage 8: polish نهایی Dashboard/Operations/Account/Settings/Notifications.
- Stage 9: هماهنگی visual بازار بدون ریسک trading logic.
- Stage 10: responsive و PWA quality.
- Stage 11: accessibility و interaction quality.
- Stage 12: testing و visual verification.
- Stage 13: گزارش نهایی فارسی.

### Stage 2 - Remove Workspace Wrapper Strategy

هدف:
- انتقال state/API customer/accountant از giant modalها به composableهای مستقل.
- `CustomerWorkspaceView` و `AccountantWorkspaceView` دیگر فقط wrapper نباشند.
- modalهای قدیمی به compatibility adapter تبدیل شوند.

پذیرش:
- `presentation="workspace"` core strategy نباشد.
- tests فعلی customer/accountant workspace بر اساس route-native UI آپدیت شوند.

خروجی انجام‌شده:
- `frontend/src/composables/useOwnerCustomers.ts` اضافه شد و state، form factoryها، normalization، payload builderها و API سطح مالک برای مشتریان را از modal بزرگ جدا کرد.
- `frontend/src/composables/useOwnerAccountants.ts` اضافه شد و state، form factoryها، normalization و API سطح مالک برای حسابداران را از modal بزرگ جدا کرد.
- `OwnerCustomerManagerModal.vue` و `OwnerAccountantManagerModal.vue` دیگر مستقیماً `apiFetch` را صدا نمی‌زنند و از composable/data-layer جدید استفاده می‌کنند.
- `CustomerWorkspaceView.vue` و `AccountantWorkspaceView.vue` از حالت wrapper کامل خارج شدند: summary، metrics، list، detail placeholder و aside actionها route-native هستند.
- managerهای قدیمی فقط به عنوان compatibility/full-management panel باقی مانده‌اند تا add/pending/manage/detail/trades/stats/sessions/danger بدون regression حفظ شود.
- تست `frontend/src/composables/useOwnerRelations.test.ts` اضافه شد و تست‌های workspace با رفتار route-native جدید به‌روزرسانی شدند.

اعتبارسنجی Stage 2:
- `npm run test:unit:run -- useOwnerRelations.test.ts OwnerCustomerManagerModal.test.ts OwnerAccountantManagerModal.test.ts CustomerWorkspaceView.test.ts AccountantWorkspaceView.test.ts` پاس شد: `5` فایل، `39/39` تست.
- `npm run build` پاس شد؛ warningهای chunk-size موجود همچنان debt جداگانه هستند.
- production deploy طبق قاعده این roadmap اجرا نشد.

مراحل باقی‌مانده بعد از Stage 2:
- Stage 3: بازطراحی واقعی Customer Workspace.
- Stage 4: بازطراحی واقعی Accountant Workspace.
- Stage 5: پاکسازی Profile و Public Profile.
- Stage 6: بازطراحی Admin Workspace.
- Stage 7: یکپارچه‌سازی small surfaces و micro UI.
- Stage 8: polish نهایی Dashboard/Operations/Account/Settings/Notifications.
- Stage 9: هماهنگی visual بازار بدون ریسک trading logic.
- Stage 10: responsive و PWA quality.
- Stage 11: accessibility و interaction quality.
- Stage 12: testing و visual verification.
- Stage 13: گزارش نهایی فارسی.

### Stage 3 - Customer Workspace Redesign

هدف:
- `/operations/customers` با summary، segmented sections، search/filter، list cards و pending invitations.
- `/operations/customers/:relationId` با detail header و tabs: مشخصات، محدودیت‌ها، معاملات، آمار، نشست‌ها، اقدامات حساس.
- desktop master-detail.

پذیرش:
- تمام add/edit/pending/copy/cancel/trades/stats/session/unlink flows حفظ شوند.
- nested accordion primary navigation حذف شود.

خروجی انجام‌شده:
- `CustomerWorkspaceView.vue` از placeholder Stage 2 به workspace واقعی‌تر تبدیل شد: summary پنج‌تایی، جستجو، فیلتر segmented، گروه‌بندی دعوت‌های در انتظار و مشتریان قابل مدیریت، و highlight مشتری انتخاب‌شده.
- مسیر detail `/operations/customers/:relationId` اکنون یک پرونده tabدار دارد: مشخصات، محدودیت‌ها، معاملات، آمار، نشست‌ها و اقدامات حساس.
- tabهای معاملات، آمار و نشست‌ها به APIهای route-native وصل شدند و فقط هنگام نیاز load می‌شوند تا ورود اولیه به workspace سنگین نشود.
- ویرایش سطح/محدودیت‌ها، مدیریت نشست‌ها و اقدامات حساس همچنان از compatibility/full-management panel باز می‌شوند تا confirmationها، permissionها و رفتارهای قبلی add/edit/pending/copy/cancel/trades/stats/session/unlink حفظ شوند.
- navigation قبلی و deep-link queryها حفظ شدند؛ تغییر tab نیز از route query استفاده می‌کند.
- تست `CustomerWorkspaceView.test.ts` با رفتار جدید آپدیت شد و سناریوی load شدن آمار route-native اضافه شد.

اعتبارسنجی Stage 3:
- `npm run test:unit:run -- CustomerWorkspaceView.test.ts` پاس شد: `1` فایل، `5/5` تست.
- `npm run test:unit:run -- CustomerWorkspaceView.test.ts OwnerCustomerManagerModal.test.ts useOwnerRelations.test.ts` پاس شد: `3` فایل، `26/26` تست.
- `npm run build` پاس شد؛ warningهای chunk-size موجود همچنان debt جداگانه هستند.
- production deploy طبق قاعده این roadmap اجرا نشد.

مراحل باقی‌مانده بعد از Stage 3:
- Stage 4: بازطراحی واقعی Accountant Workspace.
- Stage 5: پاکسازی Profile و Public Profile.
- Stage 6: بازطراحی Admin Workspace.
- Stage 7: یکپارچه‌سازی small surfaces و micro UI.
- Stage 8: polish نهایی Dashboard/Operations/Account/Settings/Notifications.
- Stage 9: هماهنگی visual بازار بدون ریسک trading logic.
- Stage 10: responsive و PWA quality.
- Stage 11: accessibility و interaction quality.
- Stage 12: testing و visual verification.
- Stage 13: گزارش نهایی فارسی.

### Stage 4 - Accountant Workspace Redesign

هدف:
- `/operations/accountants` و detail route با همان quality bar customer.
- tabs: مشخصات، شرح وظیفه، نشست‌ها، اقدامات حساس.

پذیرش:
- add/pending/copy/cancel/edit duty/session terminate/unlink حفظ شود.
- accountant خودش session/logout management نبیند.

خروجی انجام‌شده:
- `AccountantWorkspaceView.vue` از placeholder Stage 2 به workspace route-native واقعی‌تر تبدیل شد: summary چهارتایی، جستجو، فیلتر segmented، گروه‌بندی دعوت‌های در انتظار و حسابداران قابل مدیریت، و highlight حسابدار انتخاب‌شده.
- مسیر detail `/operations/accountants/:relationId` اکنون یک پرونده tabدار دارد: مشخصات، شرح وظیفه، نشست‌ها و اقدامات حساس.
- tab نشست‌ها به API route-native `fetchOwnerAccountantSessions` وصل شد و فقط هنگام نیاز load می‌شود.
- ویرایش شرح وظیفه، مدیریت نشست‌ها و اقدامات حساس همچنان از compatibility/full-management panel باز می‌شوند تا confirmationها، permissionها و رفتارهای قبلی add/pending/copy/cancel/edit duty/session terminate/unlink حفظ شوند.
- navigation قبلی و deep-link queryها حفظ شدند؛ تغییر tab نیز از route query استفاده می‌کند.
- تست `AccountantWorkspaceView.test.ts` با رفتار جدید آپدیت شد و سناریوی load شدن نشست‌های route-native اضافه شد.

اعتبارسنجی Stage 4:
- `npm run test:unit:run -- AccountantWorkspaceView.test.ts` پاس شد: `1` فایل، `5/5` تست.
- `npm run test:unit:run -- AccountantWorkspaceView.test.ts OwnerAccountantManagerModal.test.ts useOwnerRelations.test.ts` پاس شد: `3` فایل، `18/18` تست.
- `npm run build` پاس شد؛ warningهای chunk-size موجود همچنان debt جداگانه هستند.
- production deploy طبق قاعده این roadmap اجرا نشد.

مراحل باقی‌مانده بعد از Stage 4:
- Stage 5: پاکسازی Profile و Public Profile.
- Stage 6: بازطراحی Admin Workspace.
- Stage 7: یکپارچه‌سازی small surfaces و micro UI.
- Stage 8: polish نهایی Dashboard/Operations/Account/Settings/Notifications.
- Stage 9: هماهنگی visual بازار بدون ریسک trading logic.
- Stage 10: responsive و PWA quality.
- Stage 11: accessibility و interaction quality.
- Stage 12: testing و visual verification.
- Stage 13: گزارش نهایی فارسی.

### Stage 5 - Profile And Public Profile Cleanup

هدف:
- حذف emoji icons.
- action grid shared.
- relation cards، trade history، project users، avatar/address edit با shared components.

پذیرش:
- owner/admin/visitor modes actionهای قبلی را حفظ کنند.
- Profile و PublicProfile زبان بصری واحد داشته باشند.

خروجی انجام‌شده:
- emojiهای قابل مشاهده از header، error state، action gridهای visitor/admin/owner در `PublicProfile.vue` حذف و با icon componentهای `lucide-vue-next` جایگزین شدند.
- `ProfileActionCard` دیگر icon متنی نگه نمی‌دارد؛ iconها از روی `action.key` resolve می‌شوند تا رفتار actionها تغییر نکند.
- iconهای متنی/emoji در منوها، وضعیت‌ها، customer context، limitation/block modalها و user action panel داخل `UserProfile.vue` با icon component یا متن خالص جایگزین شدند.
- متن‌های وضعیت حساب و محدودیت از emoji جدا شدند تا با زبان بصری جدید هم‌راستا باشند.
- fallback loading در `ProfileView.vue` از primitive مشترک `AppLoadingState` استفاده می‌کند و کلاس wrapper قبلی برای سازگاری تست/layout حفظ شد.
- جستجوی هدفمند روی محدوده Profile/PublicProfile نشان داد emojiهای هدف‌گذاری‌شده باقی نمانده‌اند.

اعتبارسنجی Stage 5:
- `npm run test:unit:run -- PublicProfile.test.ts UserProfile.test.ts ProfileView.test.ts PublicProfileView.test.ts` پاس شد: `4` فایل، `70/70` تست.
- `npm run build` پاس شد؛ warningهای chunk-size موجود همچنان debt جداگانه هستند.
- production deploy طبق قاعده این roadmap اجرا نشد.

مراحل باقی‌مانده بعد از Stage 5:
- Stage 6: بازطراحی Admin Workspace.
- Stage 7: یکپارچه‌سازی small surfaces و micro UI.
- Stage 8: polish نهایی Dashboard/Operations/Account/Settings/Notifications.
- Stage 9: هماهنگی visual بازار بدون ریسک trading logic.
- Stage 10: responsive و PWA quality.
- Stage 11: accessibility و interaction quality.
- Stage 12: testing و visual verification.
- Stage 13: گزارش نهایی فارسی.

### Stage 6 - Admin Workspace Redesign

هدف:
- admin index و subroutes با route-native panels.
- create channel visible where allowed.
- user/commodity/invitation/channel/messages/system settings با cards/forms/list states مشترک.

پذیرش:
- old query deep links هنوز کار کنند.
- middle manager فقط allowed tools را ببیند.

### Stage 7 - Small Surfaces And Micro UI

هدف:
- auth/public/utility routes، toast، loading، empty، error، confirm، overlay، help/date picker shell را unify کند.

پذیرش:
- هیچ صفحه کوچک با emoji/error خام یا button محلی قدیمی باقی نماند.

### Stage 8 - Dashboard, Operations, Account, Settings, Notifications Polish

هدف:
- polish نهایی روی سطوحی که قبلاً route-level شدند.
- حذف wordingهای transitional.
- settings/security/storage و notifications کاملاً native حس شوند.

پذیرش:
- mobile/desktop spacing و action hierarchy یکسان باشد.

### Stage 9 - Market Visual Harmonization

هدف:
- market header، tabs، status cards، input/action bar، recent offers، notices و states هماهنگ شوند.
- core trading logic دست‌نخورده بماند.

پذیرش:
- offer create/parse/confirm/cancel/trade flow tests پاس شود.

### Stage 10 - Responsive And PWA Quality

هدف:
- viewport matrix اجرا و overflow/safe-area/fixed-bar/touch-target مشکلات رفع شود.

پذیرش:
- 360/390/430/768/1024/1440 smoke screenshots یا viewport checks پاس شود.

### Stage 11 - Accessibility And Interaction Quality

هدف:
- focus-visible، real buttons، keyboard tabs، aria contractها، contrast، reduced motion.

پذیرش:
- مهم‌ترین tab/segmented/list/action controls با keyboard قابل استفاده باشند.

### Stage 12 - Testing And Visual Verification

هدف:
- اجرای checks موجود بدون اختراع script جدید.

حداقل‌ها:
- `git diff --check`
- focused unit tests برای changed components
- route tests برای old/new deep links
- `npm run build`
- e2e smoke برای customer/accountant/admin/settings/notifications/dashboard/market و messenger shell فقط mount
- viewport checks در اندازه‌های مشخص

### Stage 13 - Final Persian Report

هدف:
- گزارش نهایی فارسی با mapping، وضعیت compatibility، تست‌ها، build/e2e، risks و تصریح اینکه production deploy اجرا نشده مگر مالک پروژه درخواست کرده باشد.

## 13. old-to-new mapping منوها و زیرمنوها

| مسیر/منوی قدیمی | مقصد جدید | وضعیت مهاجرت |
| --- | --- | --- |
| `/profile?workspace=customers` | `/operations/customers` | باید adapter بماند، UI اصلی جدید شود |
| `/profile?workspace=accountants` | `/operations/accountants` | باید adapter بماند، UI اصلی جدید شود |
| Customer accordion: افزودن مشتری | `/operations/customers?section=create` یا panel route-native | باید به create panel تبدیل شود |
| Customer accordion: مدیریت مشتریان | `/operations/customers?section=active` | باید list/cards و master-detail شود |
| Customer detail accordion: مشخصات/محدودیت‌ها | `/operations/customers/:relationId?tab=limits` | باید tab شود |
| Customer detail accordion: معاملات | `/operations/customers/:relationId?tab=trades` | باید tab/panel شود |
| Customer detail accordion: آمار | `/operations/customers/:relationId?tab=stats` | باید tab/panel شود |
| Customer detail accordion: نشست‌ها | `/operations/customers/:relationId?tab=sessions` | باید tab/panel شود |
| Customer detail accordion: اقدامات حساس | `/operations/customers/:relationId?tab=danger` | باید `AppDangerZone` شود |
| Accountant accordion: افزودن حسابدار | `/operations/accountants?section=create` | باید create panel شود |
| Accountant accordion: مدیریت حسابداران | `/operations/accountants?section=active` | باید list/cards و master-detail شود |
| Accountant detail accordion: شرح وظیفه | `/operations/accountants/:relationId?tab=duty` | باید tab/form شود |
| Accountant detail accordion: نشست‌ها | `/operations/accountants/:relationId?tab=sessions` | باید tab/panel شود |
| Accountant detail accordion: اقدامات حساس | `/operations/accountants/:relationId?tab=danger` | باید `AppDangerZone` شود |
| `/admin?section=create_invitation` | `/admin/invitations` | adapter حفظ شود، UI route-native شود |
| `/admin?section=create_channel` | `/admin/channels` | action visible و route-native |
| `/admin?section=manage_users` | `/admin/users` | adapter حفظ شود |
| `/admin?section=user_profile&user_id=...` | `/admin/users/:id` | adapter حفظ شود |
| `/admin?section=manage_commodities` | `/admin/commodities` | adapter حفظ شود |
| `/admin?section=admin_messages` | `/admin/messages` | adapter حفظ شود |
| `/admin?section=settings` | `/admin/system` | adapter حفظ شود |
| `/settings?section=sessions` | `/account/security` | adapter حفظ شود |
| `/settings?section=storage` | `/account/storage` | adapter حفظ شود |
| `/notifications` | `/account/notifications` یا standalone notifications | هر دو کار کنند |

## 14. ریسک‌ها و guardrailها

- Customer/accountant API logic حساس است؛ ابتدا composable استخراج شود، سپس UI عوض شود.
- Market نباید در Stage 9 core trading behavior را rewrite کند.
- Admin permission visibility نباید فقط frontend-side assumed شود؛ backend همچنان authoritative است.
- حذف accordion در customer/accountant detail باید همراه با route/query state باشد تا deep link و back behavior خراب نشود.
- shared components نباید overengineered شوند؛ هر primitive باید مصرف واقعی در همان stage داشته باشد.
- deploy production ممنوع است مگر دستور صریح مالک پروژه.

## 15. تست‌های مورد انتظار در کل roadmap

- `git diff --check`
- focused unit tests هر stage
- route/deep-link tests برای legacy و new routes
- `npm run build`
- e2e smokeهای غیرپیام‌رسان
- messenger shell smoke فقط برای mount
- viewport checks در 360، 390، 430، 768، 1024، 1440

## 16. معیار توقف

این roadmap زمانی قابل بستن است که:
- customer/accountant workspace واقعاً route-native و split شده باشند.
- `OwnerCustomerManagerModal.vue` و `OwnerAccountantManagerModal.vue` دیگر primary UI نباشند.
- Profile emoji/action drift حذف شده باشد.
- Admin، settings، notifications، auth/utility و small states یک زبان بصری داشته باشند.
- desktop layoutها intentional باشند.
- old routes و deep links حفظ شده باشند.
- build/tests پاس شوند.
- production deploy فقط در صورت درخواست صریح انجام شده باشد.
