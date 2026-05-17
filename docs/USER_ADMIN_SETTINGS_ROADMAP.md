# سند راهبردی تنظیمات کاربر برای ادمین

وضعیت: فاز 1 تکمیل و اعتبارسنجی شد؛ فاز 2 هنوز شروع نشده است

هدف این سند بستن scope، seamهای واقعی موجود در کد، dependencyها، و سوال های باز برای توسعه بخش «تنظیمات کاربر» در پروفایل عمومی است. این فایل عمداً roadmap اجرایی است، نه تحلیل کلی محصول.

راهنمای وضعیت:
- `[x]` موردی که از روی درخواست شما و وضعیت فعلی کد برای من شفاف شده است
- `[ ]` موردی که challenge یا decision blocker است و باید قبل از اجرا بسته شود

## 1. خواسته های قطعی

- [x] ادمین باید تقریباً تمام دسترسی های فعلی سوپرادمین در تنظیمات کاربر را داشته باشد، به جز تغییر `role`.
- [x] فقط سوپرادمین باید بتواند نقش همه کاربران را تغییر دهد.
- [x] سوپرادمین باید روی تمام کاربران قابلیت مدیریت کامل داشته باشد.
- [x] فقط سوپرادمین باید بتواند مدیر میانی یا سوپرادمین را اضافه یا حذف کند.
- [x] سیستم ادمین دو سطح دارد: `super admin` و `middle admin`.
- [x] `super admin` روی تمام کاربران (از جمله `middle admin`) مدیریت کامل دارد.
- [x] `middle admin` همه دسترسی های مدیریتی را روی کاربران غیرادمین دارد، به جز هرگونه تغییر نقش به/از سطح ادمین.
- [x] اعمال تنظیمات/محدودیت روی `middle admin` فقط باید توسط `super admin` انجام شود.
- [x] در مرحله دعوت کاربر، ادمین فقط باید نقش های `تماشا` و `عادی` را ببیند و انتخاب کند.
- [x] `middle admin` در زمان دعوت کاربر فقط باید roleهای `عادی` و `تماشا` را ببیند و به `پلیس`/`مدیر میانی`/`مدیر ارشد` دسترسی نداشته باشد.
- [x] ادمین باید بتواند همه نشست های فعال یک کاربر را ببیند.
- [x] ادمین باید بتواند هر نشست فعال کاربر را به صورت تکی حذف کند.
- [x] در لیست نشست ها باید دستگاه اصلی مشخص باشد.
- [x] در لیست نشست ها باید برای نشست آنلاین، تگ `آنلاین` و برای نشست غیرآنلاین، آخرین زمان فعالیت نمایش داده شود.
- [x] آستانه فعلی موردنظر برای تگ `آنلاین` برابر `3 دقیقه` است.
- [x] گزینه `has_bot_access` باید از UI و منطق مدیریتی حذف شود.
- [x] به جای آن باید مفهوم `فعال / غیرفعال` برای کاربر اضافه شود.
- [x] در لحظه غیرفعال سازی، کاربر باید notification با این متن بگیرد: «حساب کاربری شما غیرفعال شد. دسترسی شما به بازار بسته و دسترسی شما به پیامرسان در صورت عدم فعالسازی حساب شما؛ بعداز 2 روز مسدود خواهد شد».
- [x] در حالت غیرفعال، کاربر نباید اجازه معامله داشته باشد.
- [x] در حالت غیرفعال، کاربر نباید به صفحه بازار وب اپ دسترسی داشته باشد.
- [x] کاربر غیرفعال شده باید بتواند در حساب خود باقی بماند.
- [x] اگر کاربر تا 2 روز فعال نشود، دسترسی پیامرسان برای خود کاربر و حسابداران وابسته اش باید مسدود شود.
- [x] در بازه 2 روزه قبل از مسدودسازی پیامرسان، هیچ محدودیتی روی پیامرسان اعمال نشود تا کاربر بتواند وضعیت معامله/تسویه را مشخص کند یا با ادمین ارتباط بگیرد.
- [x] در دوره غیرفعال بودن، session کاربر و notification باید فعال بمانند تا تغییر وضعیت ها به کاربر اطلاع داده شود.
- [x] در غیرفعالسازی، کاربر از کانال حذف شود.
- [x] در فعالسازی مجدد، نوتیفیکیشن فعالسازی هم در وب اپ و هم در بات برای کاربر ارسال شود.
- [x] نوتیفیکیشن فعالسازی مجدد در بات باید شامل لینک join کانال باشد.
- [x] customer در این فاز کاملاً out-of-scope است و هیچ UI/behavior اجرایی برای آن پیاده نمی شود.
- [x] ظرفیت های قابل تنظیم کاربر باید در تنظیمات کاربر برای ادمین قابل مشاهده و ویرایش باشند.
- [x] `account_name` و `mobile_number` نباید توسط ادمین قابل ویرایش باشند.
- [x] `full_name` و `address` فقط باید توسط خود کاربر قابل ویرایش باشند.
- [x] UI این بخش باید با زبان طراحی فعلی پروژه هم راستا باشد و در همان الگوی card / sheet / grouped settings پیاده شود.

## 2. واقعیت فعلی کد که روی roadmap اثر می گذارد

### 2.1 تنظیمات کاربر الان کجا هستند

- [x] تنظیمات مدیریتی کاربر در وب الان داخل `frontend/src/components/UserProfile.vue` پیاده شده است، نه داخل پروفایل عمومی.
- [x] پروفایل عمومی در `frontend/src/components/PublicProfile.vue` فعلاً یک surface نمایشی است و داده اش را از `api/users-public` می گیرد.
- [x] `users-public` برای نمایش عمومی طراحی شده و محل مناسبی برای حمل داده های mutable و admin-only نیست.
- [x] در نتیجه، بخش جدید «تنظیمات کاربر» باید از پروفایل عمومی باز شود ولی داده و mutationهایش را از surfaceهای privileged بگیرد، نه از `users-public`.

### 2.2 سطح دسترسی فعلی backend

- [x] Router مدیریت کاربر در `api/routers/users.py` اکنون از `verify_admin_or_dev_key` استفاده می کند و restrictionهای حساس را در سطح route/business rule enforce می کند.
- [x] API دعوت کاربر در `api/routers/invitations.py` اکنون `verify_admin_user` را می پذیرد.
- [x] permission split واقعی backend برای requirement این فاز انجام شده است و صرفاً به hide/show فرانت محدود نیست.
- [x] این split اکنون عملاً سه policy جدا را enforce می کند:
  - `super_admin_only` برای role escalation/de-escalation ادمین ها
  - `admin_manage_user` برای سایر تنظیمات کاربر
  - `admin_invite_standard_watch_only` برای محدودسازی roleهای دعوت

### 2.3 وضعیت فعلی invitation UI

- [x] فرم دعوت وب در `frontend/src/components/CreateInvitationView.vue` اکنون roleها را بر اساس `adminAccess.ts` فیلتر می کند.
- [x] برای `middle admin` فقط `تماشا` و `عادی` در UI قابل انتخاب است.
- [x] backend نیز همین restriction را enforce می کند و escalation از UI bypass نمی شود.
- [x] bot invitation flow هم با همین policy هم راستا شده است.

### 2.4 وضعیت فعلی session management

- [x] مدل `UserSession` همین الان این داده ها را دارد: `device_name`، `device_ip`، `platform`، `home_server`، `is_primary`، `is_active`، `created_at`، `last_active_at`.
- [x] endpoint فعلی `GET /api/sessions/active` فقط نشست های کاربر جاری را برمی گرداند.
- [x] endpoint فعلی `DELETE /api/sessions/{session_id}` فقط نشست های کاربر جاری را terminate می کند.
- [x] تنها capability مدیریتی موجود الان `POST /api/users/{user_id}/sessions/terminate-all` است.
- [x] پس «نمایش همه نشست های کاربر برای ادمین» و «حذف تکی نشست هدف برای ادمین» الان seam آماده ندارند و باید endpoint جدید بگیرند.

### 2.5 وضعیت فعلی online/offline نشست

- [x] برای هر نشست فقط `last_active_at` در مدل موجود است.
- [x] از روی کد فعلی، `last_active_at` به صورت قابل اتکا روی همه درخواست های احرازشده touch نمی شود.
- [x] نمونه قطعی موجود: در `api/routers/auth.py` روی refresh token، `last_active_at` آپدیت می شود.
- [x] بنابراین نمایش `آنلاین` یا `آخرین بازدید نشست` بدون یک touch policy سراسری، دقیق و پایدار نخواهد بود.

### 2.6 وضعیت فعلی `has_bot_access`

- [x] `has_bot_access` هنوز در مدل `models/user.py` وجود دارد.
- [x] `has_bot_access` هنوز در `schemas.UserRead` و `schemas.UserUpdate` وجود دارد.
- [x] `has_bot_access` هنوز در `api/routers/users.py` update می شود و برایش notification مستقل ارسال می شود.
- [x] `has_bot_access` هنوز در `frontend/src/components/UserProfile.vue` برای toggle و نمایش استفاده می شود.
- [x] `has_bot_access` هنوز در `frontend/src/views/DashboardView.vue` برای نمایش blocked state استفاده می شود.
- [x] `has_bot_access` هنوز در `bot/middlewares/auth.py` gate اصلی دسترسی بات است.
- [x] `has_bot_access` هنوز در `bot/handlers/panel.py` و `bot/handlers/admin_users.py` استفاده می شود.
- [x] بنابراین جایگزینی این مفهوم با `active / inactive` یک refactor سراسری است، نه یک rename ساده.

### 2.7 وضعیت فعلی active/inactive کاربر

- [x] در مدل `User` الان فقط `is_deleted` و `deleted_at` برای soft delete وجود دارد.
- [x] soft delete فعلی irreversible business action است و namespace حساب را هم آزاد می کند.
- [x] requirement جدید شما reversible است، پس از نظر طراحی نباید با `is_deleted` یکی در نظر گرفته شود.

### 2.8 وضعیت فعلی بازار و گیت دسترسی وب

- [x] route `/market` در router فعلی فقط `requiresAuth` دارد.
- [x] `authGuard` فعلی market access را بر اساس inactive/restricted policy کنترل نمی کند.
- [x] `DashboardView.vue` فقط هشدار نشان می دهد، اما route-level gate واقعی برای market ندارد.
- [x] بنابراین بستن دسترسی بازار فقط با تغییر UI کافی نیست و باید در route/UI و ideally در backend policy هم enforce شود.

### 2.9 وضعیت فعلی پروفایل خود کاربر

- [x] در کد فعلی endpoint مشخصی برای ویرایش `address` توسط خود کاربر وجود ندارد.
- [x] الان `address` فقط در ثبت نام وب و تکمیل لینک بات گرفته می شود.
- [x] پس requirement ویرایش آدرس توسط خود کاربر هم seam آماده ندارد و باید بخشی از roadmap باشد.

### 2.10 وضعیت فعلی مشتری ها

- [x] فیچر مشتری هنوز در فاز checklist محصولی است و در کد اصلی مدل و surface اجرایی کامل ندارد.
- [x] در `PublicProfile.vue` دکمه `مشتریان` فعلاً placeholder است و alert نشان می دهد.
- [x] در مدل های اصلی، serviceهای core و routerهای اصلی، customer capability اجرایی هم ارز accountant فعلاً وجود ندارد.
- [x] بنابراین هر بندی که روی «کاربر و مشتریان کاربر» اثر می گذارد، dependency مستقیم به فیچر customer دارد.

### 2.11 وضعیت فعلی کانال تلگرام

- [x] flow فعلی کانال تلگرام بر پایه ساخت invite link از نوع `creates_join_request=True` و approve کردن درخواست کاربر است.
- [x] helper فعلی `bot/utils/channel_invites.py` فقط link می سازد و user را مستقیماً عضو channel نمی کند.
- [x] حذف کاربر از کانال الان در `core/services/user_deletion_service.py` با `ban/unban` انجام می شود.
- [x] طبق داک رسمی Telegram Bot API، `banChatMember` در supergroup/channel مانع بازگشت کاربر با invite link می شود مگر این که اول `unbanChatMember` انجام شود.
- [x] طبق همان داک، `unbanChatMember` فقط ban را برمی دارد و کاربر را خودکار به گروه/کانال برنمی گرداند؛ کاربر باید دوباره از طریق link / join request عضو شود.
- [x] بنابراین برای فعالسازی مجدد، مسیر عملی تاییدشده ارسال notification بات همراه لینک join است، نه restore خودکار silent.

### 2.12 وضعیت فعلی admin guard در frontend

- [x] `frontend/src/utils/auth.ts` دیگر `isAdmin()` را به صورت blanket-true برنمی گرداند.
- [x] guard ادمین اکنون از cache/fetch خلاصه نقش کاربر استفاده می کند و non-adminها را از routeهای مدیریتی دور نگه می دارد.
- [x] برای visibility سطح UI نیز helper جدید `frontend/src/utils/adminAccess.ts` منبع واحد تصمیم گیری شده است.

### 2.13 وضعیت فعلی `full_name`

- [x] در ثبت نام وب (`register-complete`) مقدار `full_name` فعلاً موقتاً برابر `account_name` ذخیره می شود.
- [x] در ثبت نام از مسیر دعوت ربات، `full_name` از `message.from_user.full_name` تلگرام پر می شود.
- [x] در flow `/link` اگر `full_name` فعلی هنوز همان placeholder باشد، با `full_name` تلگرام جایگزین می شود.
- [x] در ساخت برخی کاربران از CLI هم `full_name` از ورودی اسکریپت یا عملاً معادل `account_name` می آید؛ پس امروز منبع این فیلد یکدست نیست.

## 3. موارد custom per-user که الان در کد وجود دارند

این ها همین الان در مدل/اسکیما/تنظیمات کاربر موجودند و باید برای ادمین در همان surface جدید دیده شوند:

- [x] `trading_restricted_until`
- [x] `max_daily_trades`
- [x] `max_active_commodities`
- [x] `max_daily_requests`
- [x] `limitations_expire_at`
- [x] `max_sessions`
- [x] `max_accountants`
- [x] `can_block_users`
- [x] `max_blocked_users`

مواردی که فعلاً وجود ندارند ولی از نظر requirement جدید باید بررسی شوند:

- [ ] `is_active` یا `account_status` reversible برای خود کاربر
- [ ] `max_customers` برای ownerها، چون فعلاً customer feature اجرایی نشده است
- [ ] اگر customer runtime بعداً وارد scope شد، cascade policy برای customerها در زمان inactive/active شدن owner

مواردی که الان وجود دارند ولی طبق requirement جدید باید از surface مدیریتی حذف یا بازطراحی شوند:

- [x] `has_bot_access`
- [x] ویرایش مستقیم `role` برای غیرسوپرادمین

## 4. پیشنهاد ساختار UX برای «تنظیمات کاربر»

- [x] entry point این بخش بهتر است از داخل `PublicProfile.vue` و فقط برای admin/super-admin باز شود.
- [x] بهتر است این بخش به صورت sheet / panel مستقل باز شود، نه اینکه `users-public` را mutate کند.
- [x] ترتیب پیشنهادی UI:
  - وضعیت حساب
  - ظرفیت ها و سقف ها
  - محدودیت های معاملاتی
  - نشست های فعال
  - عملیات حساس
- [x] فیلدهای فقط نمایشی برای ادمین:
  - `account_name`
  - `mobile_number`
  - `full_name`
  - `address`
- [x] role باید برای ادمین قابل مشاهده باشد ولی فقط برای سوپرادمین editable بماند.

## 5. فازبندی پیشنهادی اجرا

### فاز 1: بازطراحی permissionها

- [x] تعریف dependency جدید backend برای `admin_or_super_admin`.
- [x] حفظ dependency سوپرادمین-only برای role mutation و admin-grade role management.
- [x] محدود کردن invitation API برای ادمین به دو role: `تماشا` و `عادی`.
- [x] محدود کردن invitation UI برای ادمین به همان دو role.
- [x] هم راستاسازی bot admin flows با همین policy جدید.
- [x] افزودن matrix تست صریح برای policy roleها:
  - super admin: full manage + role mutate
  - middle admin: manage فقط روی کاربران غیرادمین + بدون role mutate
  - middle admin target: فقط super admin اجازه اعمال تنظیمات/محدودیت دارد
  - middle admin invite: فقط watch/standard

### فاز 2: مدل reversible برای وضعیت کاربر

- [ ] افزودن فیلد جدید reversible برای active/inactive user state.
- [ ] خارج کردن `has_bot_access` از مدل تصمیم گیری اصلی.
- [ ] تعریف cascade policy برای inactive شدن:
  - بستن market access
  - بستن trade capability
  - ارسال notification دقیق inactive
  - حفظ session و notification فعال در دوره غیرفعال بودن
  - اخراج از کانال در لحظه غیرفعال سازی
  - schedule کردن deadline دو روزه برای قطع messenger
  - عدم اعمال محدودیت messenger در بازه دو روزه grace period
- [ ] تعریف policy برای re-activation و cancel شدن deadline دو روزه:
  - ارسال notification فعالسازی در وب اپ
  - ارسال notification فعالسازی در بات همراه لینک join کانال
- [ ] پیاده سازی deadline runner با `periodic job` هر 5 دقیقه (تصمیم نهایی این فاز) به همراه design strategy-based برای مهاجرت/گسترش آینده.

### فاز 3: نشست های مدیریتی

- [ ] افزودن endpoint برای list active sessions یک user target توسط ادمین.
- [ ] افزودن endpoint برای terminate single session یک user target توسط ادمین.
- [ ] افزودن fieldهای لازم برای UX: `is_primary`, `platform`, `device_name`, `device_ip`, `home_server`, `last_active_at`.
- [ ] اضافه کردن touch policy قابل اتکا برای `last_active_at` روی درخواست های احراز شده و/یا heartbeat websocket.
- [ ] تعریف rule برای online badge با آستانه فعلی `3 دقیقه` مگر این که بعداً تصمیم جدیدی گرفته شود.

### فاز 4: surface جدید تنظیمات کاربر در پروفایل عمومی

- [ ] افزودن action admin-only در `PublicProfile.vue` برای باز کردن «تنظیمات کاربر».
- [ ] reuse منطق موجود `UserProfile.vue` به جای ساختن logic تکراری.
- [ ] جدا کردن state نمایشی public profile از state مدیریتی privileged.
- [ ] اضافه کردن بخش نشست ها در همان surface.
- [ ] حذف toggle `has_bot_access` از UI و جایگزینی با status control جدید.

### فاز 4.1: جانمایی دکمه ها (UI Control Placement)

- [ ] در پروفایل عمومی (admin-only): دکمه `تنظیمات کاربر` به صورت primary action نزدیک هدر پروفایل.
- [ ] داخل تنظیمات کاربر: سوییچ `فعال/غیرفعال` در سکشن وضعیت حساب.
- [ ] داخل تنظیمات کاربر: سکشن `نشست های فعال` با اکشن `حذف نشست` برای هر آیتم و `حذف همه نشست ها`.
- [ ] داخل تنظیمات کاربر: سکشن `محدودیت ها و ظرفیت ها` برای فیلدهای custom per-user.
- [ ] در پروفایل خود کاربر: دکمه/فرم ویرایش `full_name` و `address` (self-only).

### فاز 5: گیت های بازار، وب اپ و بات

- [ ] enforce کردن inactive policy در backend auth/runtime.
- [ ] enforce کردن inactive policy در route-level access برای `/market`.
- [ ] enforce کردن inactive policy در component-level navigation مثل dashboard/bottom nav.
- [ ] جایگزین کردن gateهای `has_bot_access` در bot middleware و handlerها با status جدید.
- [ ] هم راستاسازی mandatory messenger channel state با status جدید در صورت نیاز.

### فاز 6: ویرایش آدرس توسط خود کاربر

- [ ] افزودن endpoint self-service برای ویرایش `address` و `full_name`.
- [ ] افزودن UI مناسب در profile/settings خود کاربر برای هر دو فیلد.
- [ ] جلوگیری صریح از ویرایش این دو فیلد توسط ادمین.

### فاز 6.1: آمادگی فنی برای customer (بدون پیاده سازی در این فاز)

- [ ] customer در این فاز پیاده سازی نمی شود (نه UI و نه enforcement).
- [ ] contract آماده برای آینده: hookهای خالی در status-transition service برای `customer_policy` تا بعداً با feature flag فعال شوند.

### فاز 7: تست و non-regression

- [ ] backend unit tests برای permission split، status transitions و admin session routes.
- [ ] bot tests برای middleware و admin user management.
- [ ] frontend unit tests برای public-profile settings surface و role gating.
- [ ] Playwright flows برای admin invitation role restriction، session revoke، inactive/active transitions و market denial.

## 6. سوال ها و challengeهای باز

### 6.1 محدوده قدرت ادمین روی کاربران مدیریتی

- [x] بسته شد: `super admin` روی همه کاربران (از جمله middle admin) مدیریت کامل دارد.
- [x] بسته شد: `middle admin` فقط روی کاربران غیرادمین مدیریت دارد و نمی تواند روی middle admin تنظیمات/محدودیت اعمال کند.

### 6.2 تعریف دقیق inactive در وب اپ

- [x] در 2 روز اول غیرفعال سازی، پیامرسان بدون محدودیت باقی می ماند.
- [x] در دوره غیرفعال بودن، session و notification فعال می ماند.

### 6.3 بازگشت به کانال تلگرام بدون لینک

- [x] با توجه به داک رسمی، restore silent ممکن نیست و مسیر تاییدشده ارسال notification فعالسازی در بات همراه لینک join است.

### 6.4 تعریف دقیق online session

- [x] آستانه فعلی توافق شده برای online session برابر `3 دقیقه` است.
- [ ] challenge باقی مانده این بخش فقط touch policy سراسری برای `last_active_at` است.

### 6.5 dependency فیچر مشتری

- [x] customer در این فاز کاملاً ignore می شود.
- [x] فقط آمادگی فنی حداقلی برای اضافه شدن آینده نگه داشته می شود.

### 6.6 editable fieldهای خود کاربر

- [x] تصمیم نهایی: `full_name` و `address` فقط self-service باشند.

### 6.7 چالش اجرایی باقی مانده در این بخش

- [x] سطح اختیار `super admin` و `middle admin` بسته شد.
- [x] برای deadline دو روزه، runner این فاز `periodic job` با cadence پنج دقیقه انتخاب شد؛ source of truth در DB نگه داشته می شود.
- [x] customer در این فاز کاملاً خارج از scope است.
- [ ] باید از الان transition engine را extensible نگه داریم تا customer policy بعداً با کمترین تغییر اضافه شود (strategy/hook + feature flag).

## 7. جمع بندی تصمیم های اجرایی من

- [x] این کار یک تغییر صرفاً UI نیست؛ permission، session runtime، auth policy، bot gate، router guard و Telegram channel flow را همزمان درگیر می کند.
- [x] حذف `has_bot_access` باید سراسری و مرحله ای انجام شود، نه موضعی.
- [x] نشست های مدیریتی seam آماده ندارند و باید backend-first اضافه شوند.
- [x] requirementهای مرتبط با customer فعلاً dependency خارجی این roadmap هستند.
- [x] بهترین seam برای UI جدید، reuse منطق `UserProfile.vue` در دل entry point جدید از `PublicProfile.vue` است.