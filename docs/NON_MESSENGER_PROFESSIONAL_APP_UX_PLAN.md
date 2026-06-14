# نقشه حرفه‌ای‌سازی کامل UI/UX بخش‌های غیرپیام‌رسان

آخرین به‌روزرسانی: 2026-06-14
وضعیت: Stage 1 تا Stage 3 تکمیل شدند؛ Stage 4 مرحله بعدی است.
قاعده قطعی: production deploy، release، server deploy و `make production-release` در این roadmap ممنوع است مگر مالک پروژه صریحاً درخواست کند.

## 1. تأیید محدوده

هدف این roadmap تبدیل تمام سطح‌های غیرپیام‌رسان به یک وب‌اپ فارسی RTL حرفه‌ای، mobile-first، app-like، یکپارچه و قابل اتکا است. معیار موفقیت فقط «کار کردن routeها» نیست؛ کاربر باید در Dashboard، Operations، بازار، حساب کاربری، پروفایل، مدیریت مشتریان/حسابداران، ادمین و صفحات کوچک حس یک محصول polished و استاندارد را بگیرد.

این موج باید تمام رفتار فعلی را حفظ کند:

- routeها و deep linkهای قدیمی و جدید
- منوها، زیرمنوها و actionهای فعلی
- role visibility و permissionهای frontend
- محدودیت‌های backend-authoritative
- contractهای API
- loading، empty، error، success و destructive states
- رفتار بازار و معاملات
- رفتار auth/public utility flows

## 2. خروج پیام‌رسان از محدوده

در این roadmap بازطراحی یا refactor داخلی پیام‌رسان ممنوع است. فایل‌ها و دامنه‌های زیر نباید به قصد redesign/restyle/restructure تغییر کنند:

- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/**`
- `frontend/src/composables/chat/**`
- `frontend/src/services/chat/**`
- message list، composer، search، room UI، upload/download/media UI
- messenger-specific storeها، event handling، virtualization و e2e behavior

کار مجاز فقط در حد زیر است:

- حفظ route `/chat`
- حفظ آیتم پیام‌رسان در navigation
- حفظ badgeهای unread/mention
- smoke test برای mount شدن shell پیام‌رسان
- لمس shell/nav مشترک فقط اگر برای سازگاری global لازم باشد و دلیل آن در گزارش فارسی ثبت شود

## 3. سطح‌های غیرپیام‌رسان داخل محدوده

| سطح | فایل‌های اصلی | وضعیت فعلی |
| --- | --- | --- |
| Dashboard | `frontend/src/views/DashboardView.vue` | بهتر شده اما هنوز CSS محلی و چند الگوی صفحه‌ای مستقل دارد. |
| Operations | `frontend/src/views/OperationsView.vue` | به design primitives نزدیک شده، ولی باید command center نهایی بماند و wording داخلی نداشته باشد. |
| Customer Workspace | `frontend/src/views/CustomerWorkspaceView.vue`, `OwnerCustomerManagerModal.vue` | route-native list/detail دارد، اما create/edit/session/danger هنوز primary fallback به compatibility manager دارد. |
| Accountant Workspace | `frontend/src/views/AccountantWorkspaceView.vue`, `OwnerAccountantManagerModal.vue` | مشابه customer؛ هنوز actionهای مهم به manager قدیمی تکیه دارند. |
| Account Hub | `frontend/src/views/AccountHubView.vue` | کارت‌ها بهتر شده‌اند، ولی settings center باید premiumتر و کمتر accordion محور شود. |
| Settings/Security/Storage | `frontend/src/views/SettingsView.vue` | routeهای جدید و legacy را پوشش می‌دهد، اما session/storage/logout باید با shared states و safe-area دقیق‌تر بررسی شود. |
| Notifications | `frontend/src/views/NotificationsView.vue` | keyboard/filter بهتر شده، اما list/action/clear/delete باید کاملاً notification-center حس شود. |
| Profile | `frontend/src/views/ProfileView.vue`, `UserProfile.vue`, `PublicProfile.vue` | emojiهای اصلی کم شده‌اند، ولی فایل profile/public profile هنوز بزرگ و چند-مسئولیتی است. |
| Public Profile | `frontend/src/views/PublicProfileView.vue`, `PublicProfile.vue` | ظاهر بهتر شده اما visitor/admin/owner context هنوز در یک بدنه بزرگ است. |
| Admin landing | `frontend/src/views/AdminView.vue`, `AdminPanel.vue` | landing بهتر شده؛ subviewها باید route-native و professional شوند. |
| Admin invitations/channel/users/profile/commodities/messages/system | `CreateInvitationView.vue`, `CreateChannelView.vue`, `UserManager.vue`, `CommodityManager.vue`, `AdminMessagesView.vue`, `TradingSettings.vue` | چند بخش هنوز local CSS، emoji یا form/table wall دارد. |
| Market shell/filter/input/action/recent/preview | `frontend/src/views/MarketView.vue`, `TradingView.vue`, `OffersList.vue`, `OfferPreviewModal.vue` | shell بهتر شده، اما trading subcomponents هنوز hard-coded visual debt دارند. Core trading logic نباید rewrite شود. |
| Auth/public utility | `LoginView.vue`, `SetupPassword.vue`, `InviteLanding.vue`, `WebRegister.vue`, `ShareReceiveView.vue` | بعضی صفحات shared states دارند، ولی باید shell و keyboard/safe-area یکدست‌تر شود. |
| Small surfaces | `PWAInstallOverlay.vue`, `JalaliDatePicker.vue`, `HelpPopover.vue`, `AppToasts.vue`, confirm/loading/empty/error/destructive states | باید همه به یک language مشترک برسند. |
| Global primitives | `frontend/src/components/ui/**`, `frontend/src/components/workspace/**`, `frontend/src/assets/main.css` | پایه وجود دارد؛ چند primitive اجباری هنوز ناقص یا غایب است. |

## 4. مشکلات فعلی سطح‌به‌سطح

| سطح | مشکل UX/visual فعلی | ریسک |
| --- | --- | --- |
| Dashboard | فایل بزرگ، الگوهای محلی و padding مستقل | صفحه اول محصول ممکن است کمتر premium و ناپایدار روی موبایل حس شود. |
| Operations | هنوز باید همه مسیرهای role-aware را واضح و بدون متن داخلی نگه دارد | کاربر ممکن است نداند مشتری/حسابدار/ادمین را از کجا مدیریت کند. |
| Customer Workspace | متن‌های user-facing مثل «مدیریت کامل» و «تا پایان Stage 3» هنوز دیده می‌شود؛ actionهای create/edit/session/danger manager قدیمی را باز می‌کنند | کاربر حس می‌کند route جدید فقط پوسته‌ای برای UI قدیمی است. |
| Accountant Workspace | همان مشکل customer با «تا پایان Stage 4» و fallback manager | تجربه حسابدار نسبت به مشتری پایین‌تر و legacy حس می‌شود. |
| Account/Settings | بخشی از ساختار هنوز accordion-heavy است و CTAهای logout/session/storage باید safe-area-proof شوند | actionهای حساس ممکن است از نظر بصری گم یا زیر bottom nav نزدیک شوند. |
| Notifications | برخی styleهای list/delete/toggle محلی هستند؛ padding پایین هنوز عدد بزرگ محلی دارد | مرکز اعلان‌ها ممکن است با بقیه app متفاوت و غیرقابل پیش‌بینی شود. |
| Profile/Public Profile | `PublicProfile.vue` حدود 2927 خط است؛ owner/admin/visitor modes هنوز در یک فایل سنگین هستند | تغییرات آینده سخت و ریسک visual drift زیاد است. |
| Admin | `CreateChannelView.vue`، `AdminMessagesView.vue`، `TradingSettings.vue` و `CommodityManager.vue` هنوز visual systemهای محلی دارند | پنل مدیریت professional control center حس نمی‌شود. |
| Market | `TradingView.vue` حدود 2052 خط با emoji، hard-coded colors و fixed bottom UI است | بهبود visual بازار ممکن است با trading behavior قاطی شود؛ باید scoped و کم‌ریسک باشد. |
| Auth/Public | بعضی routeها `100vh`، style local یا feedback غیرمشترک دارند | keyboard/safe-area و حس app-like در موبایل ضعیف می‌شود. |
| Small surfaces | toast/overlay/help/date picker هنوز کاملاً در primitive layer حل نشده‌اند | تجربه کاربر در edge stateها ناهمگون می‌شود. |

## 5. ریسک‌های پنهان شدن زیر bottom nav و fixed bars

موارد قطعی یا نیازمند تست viewport:

- `BottomNav.vue` fixed است و height/safe-area token دارد؛ تمام صفحات باید padding پایین مطابق آن داشته باشند.
- `MarketView.vue` fixed action bar دارد و content padding باید با ارتفاع واقعی action bar همگام باشد.
- `TradingView.vue` fixed bottom UI و padding مستقل `100px` دارد؛ خطر drift بین shell بازار و trading panel وجود دارد.
- `NotificationsView.vue` padding پایین `12rem` محلی دارد؛ باید به token مرکزی تبدیل شود.
- `AccountHubView.vue` و `OperationsView.vue` padding پایین محلی دارند.
- `DashboardView.vue` padding پایین مستقل دارد و باید با shell اصلی هم‌خوان بماند.
- `AdminMessagesView.vue` padding پایین fixed/action style محلی دارد.
- `OwnerCustomerManagerModal.vue` و `OwnerAccountantManagerModal.vue` fixed toast/overlay دارند؛ تا زمان باقی بودن compatibility باید obstruction تست شوند.
- public/auth pages با `100vh` باید روی موبایل و keyboard بررسی شوند.
- `PWAInstallOverlay.vue`، confirm dialogها و bottom-sheetهای آینده باید actionها را بالای safe-area نگه دارند.

قاعده اجرایی Stage 2: هیچ page scroll container نباید با padding عددی تصادفی بسته شود؛ باید از tokenهای مرکزی مثل `--ds-bottom-nav-height`، `--ds-safe-area-bottom` و برای fixed action bar از token height همان bar استفاده کند.

## 6. legacy/compatibility components هنوز visible

| component | وضعیت | تصمیم |
| --- | --- | --- |
| `OwnerCustomerManagerModal.vue` | هنوز برای create، pending/manage، edit limits، sessions و danger از workspace باز می‌شود | در Stage 3 فقط fallback شود؛ primary UI باید route-native شود. |
| `OwnerAccountantManagerModal.vue` | مشابه customer برای create، pending/manage، duty، sessions و danger | در Stage 4 فقط fallback شود. |
| `PublicProfile.vue` | فایل بزرگ چند-contextی | در Stage 7 به panelهای کوچک‌تر یا حداقل shared primitives عمیق‌تر منتقل شود. |
| `CreateChannelView.vue` | visual debt با messenger-style tokens و local CSS زیاد | در Stage 6 admin-native شود. |
| `CommodityManager.vue` | emoji/action local | در Stage 6 پاکسازی شود. |
| `TradingSettings.vue` | فرم‌های سنگین و accordion زیاد | در Stage 6 با section cards/tabs استاندارد شود. |
| `TradingView.vue` | trading UI سنگین و hard-coded | Stage 9 فقط visual harmonization کم‌ریسک، بدون rewrite logic. |
| `MainMenu.vue` و `HomePage.vue` | emoji/hard-coded style باقی دارد | اگر هنوز visible است در Stage 10 یا Stage 1 inventory تکلیفش روشن شود. |

## 7. hard-coded CSS و local style debt

ممیزی با `rg` نشان داد هنوز debtهای زیر وجود دارد:

- hard-coded color/shadow/radius در `TradingView.vue`, `CreateChannelView.vue`, `AdminMessagesView.vue`, `TradeLotSuggestionAlert.vue`, `OffersList.vue`, `AppToasts.vue`, `MainMenu.vue`, `HomePage.vue`.
- در primitiveهای جدید هم هنوز shadow/color hard-code محدود باقی مانده است؛ مثل info tone و shadowهای overlay/dialog/sheet که باید در stageهای بعدی به token تبدیل شوند.
- emoji/text-symbol در `TradingView.vue`, `CommodityManager.vue`, `CreateInvitationView.vue`, `OffersList.vue`, `MainMenu.vue`.
- fixed/sticky UI و padding محلی در `TradingView.vue`, `MarketView.vue`, `BottomNav.vue`, `NotificationsView.vue`, `AdminMessagesView.vue`, `OwnerCustomerManagerModal.vue`, `OwnerAccountantManagerModal.vue`, `PublicProfile.vue`.
- legacy utility classes در `frontend/src/assets/main.css` مثل `.btn-primary` و `.input-premium` که باید یا به primitiveها migrate شوند یا فقط backward compatibility محدود بمانند.

قاعده: local CSS فقط برای layout خاص همان feature مجاز است. هویت بصری button/card/input/list/badge/tab/toast/dialog باید از shared primitive و `--ds-*` بیاید.

## 8. primitiveهای موجود و gaps

موجود در `frontend/src/components/ui/index.ts`:

- `AppActionCard`
- `AppButton`
- `AppCard`
- `AppDangerZone`
- `AppEmptyState`
- `AppErrorState`
- `AppFormField`
- `AppIconButton`
- `AppInput`
- `AppListItem`
- `AppLoadingState`
- `AppMetricCard`
- `AppSectionCard`
- `AppSelect`
- `AppStatusBadge`
- `AppTabs`
- `AppTextarea`
- `AppConfirmDialog`

gaps نسبت به درخواست جدید:

- `AppPage`
- `AppPageHeader`
- `AppWorkspace`
- `AppMasterDetail`
- `AppSegmentedControl`
- `AppToolbar`
- `AppSearchField`
- `AppFilterChips`
- `AppNumberStepper`
- `AppToast`
- `AppBottomSheet`
- `AppResponsiveDialog`

این primitiveها فقط وقتی ساخته شوند که در همان Stage واقعاً مصرف می‌شوند. هدف ساخت کتابخانه بی‌مصرف نیست؛ هدف حذف visual duplication است.

قید تکمیلی:

- بسته شدن Stage 1 فقط با ساخت primitive کافی نیست؛ primitiveهای کلیدی مثل `AppMasterDetail`, `AppFilterChips`, `AppNumberStepper`, `AppBottomSheet`, `AppResponsiveDialog` باید در stageهای feature-level مصرف واقعی پیدا کنند.
- `AppPage`, `AppWorkspace`, `AppMasterDetail` در stageهای بعدی باید behavior بیشتری بگیرند؛ از جمله variantهای safe-area/full-height/scrollable و modeهای desktop/mobile.

## 9. mapping قدیم به جدید برای منوها، زیرمنوها و actionها

| منو/مسیر/Action فعلی | مقصد نهایی | وضعیت مطلوب |
| --- | --- | --- |
| `/profile?workspace=customers` | `/operations/customers` | legacy adapter باقی بماند؛ UI اصلی route-native باشد. |
| `/profile?workspace=accountants` | `/operations/accountants` | legacy adapter باقی بماند؛ UI اصلی route-native باشد. |
| Customer: افزودن مشتری | `/operations/customers?section=create` یا panel در همان route | بدون باز کردن manager قدیمی؛ form native با `AppFormField`/`AppNumberStepper`. |
| Customer: دعوت‌های در انتظار | `/operations/customers?section=pending` | panel native با cancel/expire و copy link. |
| Customer: مدیریت مشتریان | `/operations/customers` و `/operations/customers/:relationId` | master-detail/list cards، بدون «مدیریت کامل» به‌عنوان مفهوم اصلی. |
| Customer: مشخصات | `?tab=profile` | detail card native. |
| Customer: محدودیت‌ها/کمیسیون | `?tab=limits` | form native، feedback سطح صفحه، حفظ API. |
| Customer: معاملات | `?tab=trades` | history panel native، بدون تغییر endpoint. |
| Customer: آمار | `?tab=stats` | metric cards و period controls. |
| Customer: نشست‌ها | `?tab=sessions` | session cards و terminate confirm. |
| Customer: قطع رابطه/خطر | `?tab=danger` | `AppDangerZone` + `AppConfirmDialog`. |
| Accountant: افزودن حسابدار | `/operations/accountants?section=create` | form native. |
| Accountant: دعوت‌های در انتظار | `/operations/accountants?section=pending` | panel native با cancel/copy. |
| Accountant: مدیریت حسابداران | `/operations/accountants` و `/operations/accountants/:relationId` | list/detail native. |
| Accountant: شرح وظیفه | `?tab=duty` | form native. |
| Accountant: نشست‌ها | `?tab=sessions` | session cards و terminate confirm فقط برای سرگروه. |
| Accountant: قطع رابطه/خطر | `?tab=danger` | danger zone native. |
| `/settings?section=sessions` | `/account/security` | old deep link حفظ؛ UI settings center shared. |
| `/settings?section=storage` | `/account/storage` | old deep link حفظ؛ storage card/action shared. |
| `/notifications` | `/account/notifications` | legacy route حفظ؛ notification center shared. |
| `/admin?section=create_invitation` | `/admin/invitations` | deep link adapter حفظ؛ subview native. |
| `/admin?section=create_channel` | `/admin/channels` | channel creation native و فقط برای نقش مجاز visible. |
| `/admin?section=manage_users` | `/admin/users` | user list/search/detail native. |
| `/admin?section=user_profile&user_id=...` | `/admin/users/:id` | profile management route-native. |
| `/admin?section=manage_commodities` | `/admin/commodities` | commodity cards/forms/danger shared. |
| `/admin?section=admin_messages` | `/admin/messages` | broadcast/history native. |
| `/admin?section=settings` | `/admin/system` | deep link واقعی فعلی؛ باید به section داخلی `settings` وصل بماند. |
| `/admin?section=system_settings` | `/admin/system` | اگر در نسخه‌های قدیمی‌تر استفاده شده باشد باید به عنوان alias compatibility پوشش داده شود. |
| trading/system settings | `/admin/system` | settings tabs/cards native. |
| Market filters | `/market` internal state | tabs/filter chips shared؛ trading logic دست‌نخورده. |
| Market input/action bar | `/market` | fixed bar safe-area-aware با matching content padding. |
| Recent offers | `/market` recent menu | list/empty/loading shared، keyboard-friendly. |
| Offer preview modal | `OfferPreviewModal.vue` | responsive dialog shared، بدون تغییر trade decision flow. |
| Login/setup/register/invite/share | routeهای فعلی | auth/public shell مشترک، keyboard و safe-area safe. |
| Toast/confirm/loading/empty/error/destructive | global/shared | primitive واحد در همه سطح‌ها. |

## 10. برنامه جایگزینی الگوهای قدیمی

1. ابتدا design-system gaps را با primitiveهای مصرف‌شونده ببندیم، نه با abstraction اضافه.
2. safe-area و fixed-bottom را در tokenهای مرکزی و e2e obstruction checks enforce کنیم.
3. customer workspace را از compatibility manager جدا کنیم؛ manager فقط fallback/debug compatibility بماند.
4. accountant workspace را با همان استاندارد customer جدا کنیم.
5. account/settings/session/storage را از accordion-only به settings center حرفه‌ای تبدیل کنیم.
6. admin subviewها را از old component hosted inside route به route-native panels نزدیک کنیم.
7. profile/public profile را به action cards، metric cards، relation lists و danger/confirm مشترک وصل کنیم.
8. notifications را به inbox حرفه‌ای با tabs/filter/list/action shared تبدیل کنیم.
9. dashboard/operations/market را polish کنیم؛ در بازار فقط shell/visual را تغییر دهیم، نه business logic.
10. auth/public/small surfaces را با shell/form/state مشترک ببندیم.
11. accessibility و interaction را روی همه interactive controlهای مهم enforce کنیم.
12. visual/viewport/e2e gate را اجرا کنیم و گزارش فارسی نهایی بدهیم.

## 10.1. عبارات ممنوع در UI نهایی

این عبارت‌ها زبان داخلی توسعه هستند و نباید در UI نهایی کاربر دیده شوند:

- `Stage`
- `migration`
- `compatibility`
- `fallback`
- `legacy`
- `adapter`
- `مدیریت کامل`
- `مسیر مهاجرت`
- `تا پایان Stage`
- `سازگاری موقت`
- `نسخه قدیمی`
- `مهاجرت`

استثنا: این عبارت‌ها فقط در مستندات فنی، تست‌ها، نام متغیر داخلی یا commentهای غیر user-facing مجاز هستند. هر متن visible باید با زبان محصول جایگزین شود؛ مثل «تنظیمات مشتری»، «اقدامات حساس»، «دعوت‌های در انتظار»، «مدیریت نشست‌ها».

## 11. معیار پذیرش سطح‌به‌سطح

| سطح | معیار پذیرش |
| --- | --- |
| Dashboard | first screen premium، بدون clutter، shortcuts واضح، بدون overflow یا hidden CTA. |
| Operations | role-aware actions واضح، بدون wording داخلی، مسیر customer/accountant/admin مستقیم و قابل فهم. |
| Customer Workspace | create/edit/session/danger دیگر primary dependency به manager قدیمی نداشته باشد؛ desktop master-detail؛ mobile بدون hidden action. |
| Accountant Workspace | مشابه customer؛ duty/session/unlink native؛ restrictionهای حسابدار حفظ شود. |
| Account/Settings | account center حرفه‌ای، sessions/storage/logout واضح، old deep links کار کنند. |
| Notifications | inbox native با read/unread hierarchy، empty per filter، clear/delete امن. |
| Profile/Public Profile | no emoji، action grid/card shared، owner/admin/visitor modes هم‌زبان. |
| Admin | landing و subviewها control center حس شوند؛ permissionها و old query links حفظ شوند. |
| Market | shell/filter/action bar/recent/preview هم‌زبان؛ trading create/parse/confirm/cancel/trade بدون regression. |
| Auth/Public | keyboard/safe-area friendly، shared form/state، no neglected standalone page. |
| Small surfaces | toast/confirm/help/date-picker/loading/empty/error/destructive یکپارچه. |

## 12. چک‌لیست mobile viewport

باید در عرض‌های زیر بررسی شود:

- 360
- 375
- 390
- 414
- 430
- 768

چک‌ها:

- هیچ horizontal overflow نباشد.
- آخرین item/list/control در هر صفحه قابل رسیدن و قابل دیدن باشد.
- primary CTA زیر bottom nav، fixed bar، safe area یا keyboard پنهان نشود.
- touch targetها حداقل 44px و ترجیحاً 48px باشند.
- متن فارسی clip نشود و line-height خوانا بماند.
- bottom sheets/dialogها actionهای خود را بالای safe-area نگه دارند.
- market action bar آخرین offer/control را نپوشاند.
- customer/accountant danger/session actions زیر nav پنهان نشوند.
- در هر route کلیدی، آخرین `button`, `a`, `input`, `textarea`, `select`, `[role=button]`, `[tabindex]` visible که داخل bottom chrome نیست پیدا شود.
- صفحه و scroll containerهای داخلی تا انتها scroll شوند.
- bounding box آخرین کنترل با bounding boxهای bottom nav و fixed/sticky bottom bar مقایسه شود.
- اگر overlap وجود داشت یا فاصله آخرین کنترل با بالای bottom chrome کمتر از `12px` بود، تست باید fail شود.
- این obstruction check روی عرض‌های موبایل `360`, `375`, `390`, `414`, `430` اجباری است.
- در Stage 12 این coverage باید به `customer detail`, `accountant detail`, `admin` subroutes, legacy settings deep links و auth/public pages هم گسترش یابد.
- اگر fixed/sticky bottom chrome با wrapperهای nested یا transform ساخته شده باشد، detection تست باید برای آن هم تقویت شود.

## 13. چک‌لیست desktop viewport

باید در عرض‌های زیر بررسی شود:

- 1024
- 1280
- 1440

چک‌ها:

- customer/accountant/admin فقط centered mobile column نباشند.
- master-detail یا two-column layout در مسیرهای سنگین استفاده شود.
- form wallهای قدیمی به section cards قابل اسکن تبدیل شوند.
- table/list/action density برای desktop منطقی باشد.
- modal/dialog width و max-height کنترل شود.
- admin subviewها narrow و فشرده حس نشوند.

## 14. چک‌لیست safe-area و fixed-bottom

- `--ds-bottom-nav-height` و `--ds-safe-area-bottom` منبع اصلی باشند.
- هر fixed action bar height token یا class مستند داشته باشد.
- scroll container همان صفحه padding پایین مطابق nav/action bar داشته باشد.
- `100vw` برای body/page width استفاده نشود مگر با دلیل دقیق.
- `100vh` در auth/public pages به `100dvh` یا الگوی امن‌تر تبدیل شود.
- `env(safe-area-inset-bottom)` در bottom nav، market bar، bottom sheet و dialog actions استفاده شود.
- e2e باید تا bottom scroll کند و last focusable/control را از نظر obstruction چک کند.

## 15. برنامه تست

حداقل‌های هر Stage:

- `git diff --check`
- unit tests متمرکز روی componentهای تغییرکرده
- route/deep-link tests برای مسیرهای قدیم و جدید اگر route behavior تغییر کند
- `npm run build` پس از stageهای runtime مهم
- e2e viewport/obstruction برای stageهای layout/safe-area

Gate نهایی Stage 12:

- focused unit gate برای تمام سطح‌های غیرپیام‌رسان تغییرکرده
- build
- e2e viewport matrix روی 360/375/390/414/430/768/1024/1440
- smoke برای dashboard، operations، customers، customer detail، accountants، accountant detail، account، settings/security/storage، notifications، admin routes، market، auth/public utilityها در حد feasible
- messenger shell فقط mount/smoke، بدون تست internals
- اگر Playwright screenshot در محیط قابل اجرا بود، مسیر screenshots در گزارش نهایی ثبت شود

## 16. مراحل اجرایی roadmap

| Stage | عنوان | وضعیت |
| --- | --- | --- |
| Stage 0 | Audit قبل از coding و ایجاد همین سند | Completed |
| Stage 1 | Design system completion and enforcement | Completed |
| Stage 2 | Global layout, safe-area and fixed UI hardening | Completed |
| Stage 3 | Customer Workspace: remove remaining legacy feel | Completed |
| Stage 4 | Accountant Workspace: remove remaining legacy feel | Next |
| Stage 5 | Account, Settings, Session and Storage UX | Pending |
| Stage 6 | Admin workspace and admin subviews | Pending |
| Stage 7 | Profile and Public Profile final polish | Pending |
| Stage 8 | Notifications final polish | Pending |
| Stage 9 | Dashboard, Operations and Market polish | Pending |
| Stage 10 | Small surfaces and auth/public utility pages | Pending |
| Stage 11 | Micro-interaction and accessibility | Pending |
| Stage 12 | Visual QA and testing | Pending |
| Stage 13 | Final Persian report | Pending |

## 17. جزئیات Stageهای بعدی

### Stage 1 - Design system completion and enforcement

وضعیت: Completed on 2026-06-14.

خروجی انجام‌شده:

- primitiveهای غایب با API کوچک و قابل مصرف اضافه شدند: `AppPage`, `AppPageHeader`, `AppWorkspace`, `AppMasterDetail`, `AppToolbar`, `AppSearchField`, `AppFilterChips`, `AppNumberStepper`, `AppToast`, `AppBottomSheet`, `AppResponsiveDialog`.
- export مرکزی `frontend/src/components/ui/index.ts` به‌روز شد.
- CSS shared برای page/workspace/header/master-detail/toolbar/search/filter/number-stepper/toast/bottom-sheet/responsive-dialog به `frontend/src/assets/main.css` اضافه شد.
- `sr-only`، focus-within/focus-visible، touch target و safe-area padding برای primitiveهای جدید تعریف شد.
- `AppPrimitives.test.ts` برای primitiveهای جدید گسترش یافت.

پذیرش:

- primitiveها آماده مصرف در Stage 3 به بعد هستند.
- duplicated visual system هنوز در featureها کامل حذف نشده؛ حذف واقعی در Stageهای سطحی بعدی انجام می‌شود.

اعتبارسنجی Stage 1:

- `npm run test:unit:run -- AppPrimitives.test.ts AppAuthenticatedShell.test.ts AccountHubView.test.ts AdminView.test.ts` پاس شد: `4` فایل، `29/29` تست.
- `npm run build` پاس شد؛ warningهای chunk-size موجود همچنان debt جداگانه هستند.

### Stage 2 - Global layout, safe-area and fixed UI hardening

وضعیت: Completed on 2026-06-14.

خروجی انجام‌شده:

- viewport matrix موبایل از `360/390/430` به `360/375/390/414/430` گسترش یافت.
- `frontend/e2e/non-messenger-viewport.spec.ts` با obstruction check سخت‌تر به‌روز شد.
- تست جدید در هر route کلیدی صفحه و scroll containerها را تا bottom می‌برد، آخرین کنترل focusable/interactive visible را پیدا می‌کند، کنترل‌های داخل bottom chrome را exclude می‌کند و فاصله کنترل نهایی با bottom nav/fixed bar را اندازه می‌گیرد.
- اگر آخرین کنترل با bottom nav/fixed bar overlap داشته باشد یا فاصله کمتر از `12px` باشد، تست fail می‌شود.
- fixed bottom chrome شامل `.bottom-nav-bar`, `.market-action-bar` و fixed/sticky elementهای چسبیده به پایین viewport است.

پذیرش:

- gate دقیق برای مشکل hidden-under-bottom-nav قبل از refactorهای بزرگ customer/accountant فعال شد.
- اصلاح visual/layout عمیق هر route در Stageهای بعدی انجام می‌شود، اما معیار fail شدن از همین Stage وجود دارد.

اعتبارسنجی Stage 2:

- اجرای اول e2e سخت‌تر چند obstruction واقعی را در Dashboard، Operations، Admin و Account آشکار کرد.
- route scroll container عمومی، shared workspace padding، `AdminView.vue` و `AccountHubView.vue` به padding/scroll-padding token-based با clearance محافظه‌کارانه مجهز شدند.
- `npx playwright test e2e/non-messenger-viewport.spec.ts --project=chromium --reporter=line` پس از اصلاحات پاس شد: `8/8`.
- `git diff --check` پاس شد.

### Stage 3 - Customer Workspace

وضعیت: Completed on 2026-06-14.

خروجی انجام‌شده:

- `CustomerWorkspaceView.vue` از wrapper حداقلی به یک workspace route-native واقعی تبدیل شد: summary metrics، جستجو، filter chips، master-detail، گروه دعوت‌های در انتظار، گروه مشتریان قابل مدیریت، selected-state و detail tabs.
- create customer با `AppBottomSheet` در mobile و `AppResponsiveDialog` در desktop route-native شد و دیگر create primary path به manager قدیمی وابسته نیست.
- limits edit با `AppSelect`, `AppNumberStepper`, `AppInput` و action ذخیره مستقیم route-native شد.
- trades/stats/sessions/danger flows در همان route نگه داشته شدند و manager قدیمی فقط fallback سازگاری باقی ماند.
- `AppSearchField`, `AppFilterChips`, `AppNumberStepper`, `AppMasterDetail`, `AppBottomSheet`, `AppResponsiveDialog` و `AppActionCard` مصرف واقعی گرفتند.
- متن‌های user-facing مربوط به migration/stage از UI این صفحه حذف شدند.
- success feedback ذخیره محدودیت‌ها روی همان صفحه پایدار شد و توسط watcher همان relation پاک نمی‌شود.
- mobile safe-area پایین این route برای stacked workspace حالت خالی/short-list تقویت شد تا obstruction gate Stage 2 در عرض `414px` دوباره پاس شود.

اعتبارسنجی Stage 3:

- `npm run test:unit:run -- CustomerWorkspaceView.test.ts AppPrimitives.test.ts` پاس شد: `14/14`.
- `npm run test:unit:run -- CustomerWorkspaceView.test.ts` بعد از اصلاح notice و route expectations پاس شد: `6/6`.
- `npm run build` پاس شد.
- `npm run test:e2e -- e2e/non-messenger-viewport.spec.ts --project=chromium --reporter=line` پاس شد: `8/8`.
- `git diff --check` پاس شد.

### Stage 4 - Accountant Workspace

- create accountant، pending invitations، duty edit، sessions، danger، copy link و unlink را route-native کن.
- `OwnerAccountantManagerModal.vue` فقط compatibility fallback بماند.

### Stage 5 - Account/Settings

- Account Hub را به settings center حرفه‌ای تبدیل کن.
- sessions/storage/logout را polish و safe-area-proof کن.
- `/settings?section=...` همچنان کار کند.

### Stage 6 - Admin

- subviewهای admin را روی shared page/header/card/form/list/danger patterns بیاور.
- `/admin?section=...` و `/admin/*` همزمان سالم بمانند.

### Stage 7 - Profile/Public Profile

- `PublicProfile.vue` را از نظر action areas، relation cards، trade history، project users و admin/owner actions هم‌زبان کن.
- owner actions به workspace routeها بروند.

### Stage 8 - Notifications

- tabs/filter/list/read/delete/clear all را inbox-native کن.
- final list item زیر nav پنهان نشود.

### Stage 9 - Dashboard/Operations/Market

- Dashboard و Operations را polish نهایی کن.
- Market shell/filter/action bar/recent/preview را هماهنگ کن؛ trading logic دست‌نخورده بماند.

### Stage 10 - Small surfaces

- Login، Setup password، Invite landing، Web register، Share receive، PWA overlay، Jalali date picker، Help popovers، Toasts و Confirm dialogs را unified کن.

### Stage 11 - Accessibility

- real button، keyboard navigation، aria contracts، focus-visible، reduced-motion و semantic headings را enforce کن.
- `AppBottomSheet` و `AppResponsiveDialog` در این stage باید Escape close، focus management، focus restore، scroll lock و keyboard-safe behavior بگیرند.
- primitive overlayها نباید فقط render shell باشند؛ باید accessibility-grade شوند.

### Stage 11.1 - Typography Consistency Audit

- تمام badge/tag/caption/helper/eyebrow/meta textها از نظر اندازه، weight و کاربرد audit شوند.
- نوشته‌های role/tag/status نباید بزرگ‌تر از اهمیت بصری خود باشند.
- heading/body/meta/badge scale باید به semantic typography tokens متکی شود، نه مقادیر feature-level پراکنده.

### Stage 12 - Visual QA

- تست‌های موجود را اجرا کن؛ script جدید بدون نیاز نساز.
- e2e viewport/obstruction و screenshot artifact در صورت امکان.

### Stage 13 - Final report

- گزارش فارسی شامل تغییرات، mapping، compatibility، تست‌ها، screenshots، موارد باقی‌مانده و تصریح عدم production deploy.

## 18. ممنوعیت production deploy

در این roadmap اجرای production deploy ممنوع است مگر مالک پروژه صریحاً درخواست کند. اجرای local build، unit test و e2e مجاز است. گزارش نهایی هر stage باید صریح بگوید production deploy اجرا شده یا نشده است.

## 19. خروجی Stage 0

Stage 0 با این خروجی بسته می‌شود:

- محدوده و exclusion پیام‌رسان مشخص شد.
- تمام سطح‌های غیرپیام‌رسان فهرست شد.
- debtهای اصلی فعلی از روی repo ثبت شد.
- ریسک‌های bottom nav/fixed bar ثبت شد.
- legacy/compatibility components مشخص شد.
- gaps طراحی سیستم مشخص شد.
- mapping قدیم به جدید برای menu/submenu/action نوشته شد.
- Stage 1 تا Stage 13 به‌عنوان مسیر اجرایی تعریف شد.
- هیچ فایل runtime، هیچ messenger internal و هیچ production deploy در Stage 0 تغییر/اجرا نشد.
