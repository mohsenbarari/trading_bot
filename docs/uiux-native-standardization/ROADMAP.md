# استانداردسازی بومی وب‌اپ — Native App UI/UX

این track مستقل از Stage 8 و از UIUX V3 است. رسیدهای تاریخی را overwrite
نمی‌کند. کار روی همین شاخه اجرا می‌شود.

## هویت برنامه

- نام: Native App Standardization
- شاخه: `candidate/webapp-native-controls-v1`
- worktree: `/root/trading-bot/webapp-native-controls-v1`
- مبنای اولیه: `7485f730`؛ همگام‌سازی نهایی با `origin/main`: merge commit
  `0dae62c7`
- کنترل بومی اولیه: `c8239d6c` — توکن `--ds-control-*` و هندسهٔ فیلد leftover
- مرجع رفتار پیام‌رسان: `docs/MESSENGER_REFACTORING_ROADMAP.md` و
  `docs/messenger-surface-manifest.json`
- مرجع سطح محصول V3: `docs/uiux-unification/` فقط خواندنی است
- Figma تاریخی: fileKey `z8jgJxST4O2APzWnlyP9gv` دست‌نخورده می‌ماند
- Sites، staging، production، merge به `main` و push خارج از این سند است

## هدف

کل وب‌اپ به‌جز Market مثل یک اپ استاندارد iOS/Android حس شود:

- فارسی و RTL-first با Vazirmatn
- هدف لمسی ۴۸ پیکسل، ورودی inset یکدست، فهرست گروه‌بندی‌شده
- عنوان صفحه، برگشت، sheet و صفحهٔ کلید مثل اپ بومی
- safe-area، بدون overflow افقی، بدون CTA پوشیده
- حالت‌های loading / empty / error / retry / offline / stale یک ریشه دارند
- WCAG 2.2 AA
- پیام‌رسان کامل (`M01`–`M14`) داخل برنامه است
- Market کاملاً خارج است

V3 پوسته و بسیاری از فرم‌ها را یکدست کرد؛ این برنامه همان کار را تا حس اپ
بومی ادامه می‌دهد. فرانت پیام‌رسان کاملاً آزاد است و بین فازها توقف یا
تأیید مالک لازم نیست. بازبینی مالک فقط در staging و در پایان است.

## مرز قطعی

### خارج

- مسیر `/market` و هر overlay معامله
- `home-market-widget`
- `trading-settings-market-controls`
- `admin-messages-market-delivery`
- کارت compact، meter، hourglass، two-tap، overtime روی فید بازار
- تغییر backend، دیتابیس، authorization، business logic
- تغییر rollout پیش‌فرض پیام‌رسان یا حذف مسیر legacy
- تشخیص آلبوم جز با `album_id` + `album_index`
- تغییر schema / websocket / upload / cache فقط برای restyle
- revival تلگرام Mini App
- ورود developer به‌عنوان محصول کاربر
- merge، rebase مخرب، push، deploy مگر با دستور جدا
- اعلام owner-approved یا production-ready

### داخل

- ۲۹ مسیر غیر بازار و همهٔ زیرسطح زنده‌شان
- `/chat`، `/share-receive`، `/admin/channels`
- `M01` تا `M14` از نظر زبان بصری و UX
- composer، جستجو، viewer، room manager، CreateChannel، ShareReceive
- پوسته، فرم، overlay، خانهٔ غیر بازار، حساب، پروفایل، عملیات، مدیریت، احراز
- `admin-messages-messenger-delivery`
- بخش غیر بازار `/admin/system`

## مرجع رفتار که بازنویسی نمی‌شود

- Market A+C فقط به‌عنوان سطح خارج‌مانده
- `M01`–`M14` مرجع عملکرد پیام‌رسان است؛ این برنامه آن‌ها را دوباره
  اختراع نمی‌کند
- مسیر legacy پیام‌رسان rollback باقی می‌ماند
- `account_status` مرجع دسترسی است؛ `has_bot_access` برای gating نیست
- overtime preference فقط در حساب/تنظیمات است
- confirm تقویم TradingSettings بومی می‌ماند مگر disposition جدا:
  `if (!confirm('آیا از حذف این استثنای تقویمی مطمئن هستید؟'))`

## زبان بومی اجباری

هر سطح داخل برنامه باید این قرارداد را پاس کند:

1. هدف لمسی حداقل ۴۸ پیکسل
2. فیلد ورود از `--ds-control-*` یا `AppInput` / `AppSelect` / `AppTextarea` /
   `AppSearchField` / `AppFormField`
3. برچسب بالا، فاصلهٔ ۰.۴۵rem، فوکوس مرئی حداقل ۳:۱
4. فهرست شبیه Settings بومی: گروه inset، جداکننده، disclosure
5. یک scroller مالک در هر مسیر
6. CTA نهایی بعد از اسکرول کامل بالاتر از BottomNav
7. sheet یا dialog برای تأیید مخرب؛ Escape/انصراف mutation ندارد
8. safe-area و صفحهٔ کلید نرم محتوا را نمی‌پوشاند
9. back مرورگر/دستگاه یک رفتار واحد دارد
10. حالت‌های loading / empty / error / retry / offline / stale یک ریشه دارند
11. متن فارسی می‌شکند؛ رشتهٔ LTR عددی در فیلد ltr است
12. reduced-motion و زوم ۲۰۰٪ overflow افقی نمی‌سازند
13. CSS جدید `--ui-v2-*` / `.ui-v2-` / `data-ui-system` نمی‌آورد مگر واقعاً
    کاتالوگ V2 باشد

## مالکیت مسیرها

`/market` در جدول نیست. interiors بازار حتی روی مسیر داخل‌برنامه دست
نمی‌خورند.

| مسیر | فاز اصلی | یادداشت |
|---|---|---|
| `/` | ۳ و ۴ | فقط پوسته و بخش غیر بازار؛ ویجت بازار قفل |
| `/setup-password` | ۸ | |
| `/login` | ۸ | OTP عوض نمی‌شود |
| `/operations` | ۶ | |
| `/operations/customers` | ۶ | |
| `/operations/customers/:relationId` | ۶ | |
| `/operations/accountants` | ۶ | |
| `/operations/accountants/:relationId` | ۶ | |
| `/account` | ۴ | |
| `/account/security` | ۴ | |
| `/account/storage` | ۴ | |
| `/account/notifications` | ۴ | |
| `/chat` | ۹ | کل `M01`–`M14` |
| `/users/:id` | ۵ | |
| `/profile` | ۵ | |
| `/settings` | ۴ | overtime اینجا می‌ماند |
| `/admin` | ۷ | |
| `/admin/invitations` | ۷ | |
| `/admin/channels` | ۹ | `M11` |
| `/admin/users` | ۷ | |
| `/admin/users/:id` | ۵ و ۷ | |
| `/admin/commodities` | ۷ | |
| `/admin/messages` | ۷ و ۹ | پیام‌رسان داخل؛ تحویل بازار قفل |
| `/admin/system` | ۷ | کنترل بازار قفل؛ confirm تقویم قفل |
| `/i/:code` | ۸ | |
| `/register` | ۸ | |
| `/notifications` | ۴ و ۸ | alias حساب |
| `/share-receive` | ۹ | `M11` |
| `/:pathMatch(.*)*` | ۸ | SystemRecovery fill سراسری نمی‌گیرد |

## نقشهٔ پیام‌رسان

عملکرد از جدول مرجع `M01`–`M14` می‌آید. این برنامه فقط زبان بصری و UX
بومی را روی همان سطح‌ها اعمال می‌کند.

| شناسه | حوزه | کار این track | کار ممنوع |
|---|---|---|---|
| `M01` | پوسته، bootstrap، ناوبری | ارتفاع کامل، safe-area، back، loading بومی، Vazirmatn | عوض کردن feature flag یا default=`legacy` |
| `M02` | فهرست گفتگو | ردیف استاندارد، جستجوی هم‌هندسه، empty/loading/error | تغییر pin/mute/unread/mandatory |
| `M03` | اتاق مستقیم | header، حباب، وضعیت ارسال | تغییر send/edit/delete/read |
| `M04` | گروه | همان زبان بصری اتاق | تغییر عضویت و ادمین |
| `M05` | کانال | composer فقط از نظر ظاهر | تغییر gating اجباری/اختیاری |
| `M06` | composer و overlay | ارتفاع ۴۸، sheet، keyboard-safe | تغییر schema پیام |
| `M07` | رندر پیام | فاصله و خانوادهٔ حباب | آلبوم جز با `album_id` + `album_index` |
| `M08` | اکشن و انتخاب گروهی | context menu و confirm به‌صورت sheet | تغییر قرارداد حذف/فوروارد |
| `M09` | جستجو و viewer | overlay مشترک، lightbox بومی | تغییر scroll-to-target |
| `M10` | رسانه و cache | نمایش پیشرفت با رویداد موجود | بازنویسی upload/download/cache |
| `M11` | مدیریت اتاق و پروفایل | CreateChannel، Group manager، ShareReceive | تغییر projection پروفایل |
| `M12` | هویت و مجوز | فقط نمایش درست وضعیت موجود | تغییر block/visibility |
| `M13` | realtime و اعلان | لیبل و toast موجود | تغییر websocket/SSE |
| `M14` | پایداری | RTL، reduced-motion، weak-device، bundle | کاهش پوشش تست |

فرانت پیام‌رسان از همین لحظه آزاد است. قفل Stage 8 با disposition فنی
`native-app-messenger-visual-v1` باز می‌شود؛ رسید تاریخی overwrite نمی‌شود.

## قفل منبع و disposition

رسید Stage 8 و hash تاریخی read-only می‌مانند. restyle فرانت با
`native-app-messenger-visual-v1` مجاز است و دیگر منتظر تأیید جدا نمی‌ماند.
پیام‌های مدیریت، تنظیمات سیستم و تقویم جلالی هم با
`native-app-admin-messages-visual-v1` و `native-app-trading-settings-visual-v1`
به همان زبان ۴۸ پیکسل می‌پیوندند؛ confirm تقویم و interiors بازار دست
نمی‌خورند.

فایل‌های محتمل برای همان disposition:

- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/ChatView.vue`
- خانوادهٔ `frontend/src/components/chat/`
- `frontend/src/components/CreateChannelView.vue`
- `frontend/src/views/ShareReceiveView.vue`
- توکن‌های messenger در CSS محصول

درس V3: `--ui-v2-*` روی پیام‌رسان گارد را می‌شکند؛ restyle فقط با `--ds-*`
و توکن‌های messenger.

## ماندهٔ شناخته‌شده در شروع شاخه

این‌ها نقطهٔ شروع فاز ۲ و ۹ هستند، نه موجودی کامل.

- `OwnerCustomerManagerModal` و `OwnerAccountantManagerModal`: input خام
- `LoginView`: input خام OTP/ورود
- جستجو و عنوان در `ChatGroupManagerModal`، `ChatNewConversationModal`،
  `ChatForwardModal`، `ChatHeader`، `AttachmentMenu`
- composer و lightbox پیام‌رسان
- CreateChannel و ShareReceive هنوز زبان بومی نگرفته‌اند
- پوستهٔ `/chat` هنوز حس اپ بومی ندارد

`c8239d6c` فقط هندسهٔ leftover مشتری/حسابدار/پروفایل/فهرست را به ۴۸ پیکسل
رساند.

## فازها

هر فاز پس از gate واقعی commit مستقل می‌گیرد. بین فازها توقف یا تأیید
مالک مجاز نیست؛ کار تا پذیرش شاخه ادامه دارد.

### فاز ۰ — قرارداد، موجودی، خط مبنا

کار:

- موجودی ماشین‌خوان همهٔ سطح‌های غیر بازار
- پیوند صریح هر سطح پیام‌رسان به `M01`–`M14`
- ثبت فایل منجمد و فاز بازکننده
- جداکردن کمبود واقعی از false positive
- هیچ restyle محصولی در این فاز، جز اسناد

خروجی پیشنهادی در همین پوشه:

- `SURFACE_INVENTORY.json`
- `MESSENGER_SURFACE_MAP.json`
- `EXCLUSIONS.md`

Gate:

- ۲۹ مسیر غیر بازار owner دارند
- `/market` و interiors بازار `excluded` هستند نه `unknown`
- هر `M01`–`M14` حداقل یک سطح زنده دارد
- `git diff --check` سبز
- هیچ اختلاف منبع Market نسبت به مبنای شاخه

### فاز ۱ — foundation بومی

کار:

- تکمیل `--ds-control-*` و در صورت نیاز `--ds-native-list-*` /
  `--ds-native-title-*` فقط با توکن `--ds-*`
- یکدست کردن `AppInput`، `AppSelect`، `AppTextarea`، `AppSearchField`،
  `AppNumberStepper`، `AppFormField`
- الگوی فهرست گروهی، عنوان بزرگ صفحه، ردیف Settings
- بدون افزودن `data-ui-system=v2` فقط برای اعلام کامل بودن

Gate:

- `npm run guard:ui` سبز
- فیلد استاندارد و leftover باقی‌مانده هندسهٔ یکسان دارند
- کنتراست متن ۴.۵:۱ و فوکوس ۳:۱
- تست primitiveهای فرم سبز

### فاز ۲ — مهاجرت فرم‌های غیر پیام‌رسان

کار:

- تبدیل input خام مالک مشتری/حسابدار به `App*`
- یکدست‌سازی دعوت، کالا، پروفایل مدیر، تنظیمات غیر بازار
- ردیف جستجو و دکمه هم‌ارتفاع
- TradingSettings و تقویم جلالی همان زبان ۴۸ پیکسل؛ confirm تقویم دست نمی‌خورد

Gate:

- در مسیرهای غیر بازار و غیر پیام‌رسان، input خام دیده‌شده صفر است
  مگر file hidden یا تقویم قفل‌شده
- تست Customer/Accountant/UserProfile/Invitation/Commodity سبز
- هیچ اختلاف Market

### فاز ۳ — پوسته، ناوبری، overlay مشترک

کار:

- `AppAuthenticatedShell`، `AppPage`، `AppPageHeader`، BottomNav،
  منوی هویت
- یک scroller، safe-area، back واحد، focus return
- toast / banner / dialog / bottom sheet با زبان بومی
- خانهٔ بازار را بازطراحی نکن؛ فقط پوستهٔ اطراف ویجت

Gate:

- document overflow صفر روی ۲۹ مسیر در ۳۹۰ و ۱۴۴۰
- CTA پوشیده صفر
- unnamed / nested interactive صفر در سناریوهای اجراشده
- keyboard Tab و Escape روی overlay نمونه پاس

### فاز ۴ — خانه، حساب، تنظیمات

کار:

- هویت، معاملات امروز، همکاران، کالا/alias، PWA، اعلان
- جدول معاملات امروز تک‌سطری می‌ماند
- Account Hub، امنیت، ذخیره، اعلان، تلگرام، نشست، خروج، overtime
- ویجت بازار دست‌نخورده

Gate:

- نقش‌های قابل‌اعمال خانه/حساب
- privacy معاملات و همکاران بدون تغییر
- empty/error/loading/offline پاس

### فاز ۵ — پروفایل و هویت

کار:

- self / public / admin از یک زبان بومی
- `ProfileIdentityHeader` و sectionهای موجود حفظ می‌شوند
- مجوزها ادغام نمی‌شوند
- فیلد موبایل/آدرس طبق قرارداد فعلی سرور

Gate:

- جابه‌جایی `/profile` ↔ `/users/:id` دوباره mount نمی‌شود
- regression دسترسی صفر
- avatar / no-avatar / online / offline / زوم ۲۰۰٪

### فاز ۶ — عملیات مشتری و حسابدار

کار:

- hub / list / detail / tabs / settings / sessions / danger
- فرم‌ها keyboard-safe
- `expected_action` روی DELETE قفل می‌ماند
- لینک پروفایل به projection درست

Gate:

- چهار مسیر عملیات و زیرstateها
- overflow و CTA پوشیده صفر
- cancel/Escape mutation ندارد

### فاز ۷ — مدیریت

کار:

- AdminPanel، UserManager، UserProfile، Commodity، Invitation،
  پیام مدیریت غیر بازار، تنظیمات غیر بازار
- CreateChannel فقط هماهنگی بصری اولیه؛ عملکرد کانال در فاز ۹ قفل است
- مدیر میانی و ارشد طبق قرارداد سرور
- interior بازار در System و Messages دست نمی‌خورد

Gate:

- کنترل غیرمجاز صفر
- destructive با dialog مشترک
- تست‌های مدیریت سبز

### فاز ۸ — احراز و مسیرهای عمومی

کار:

- Login، Register، Invite، SetupPassword، SystemRecovery،
  `/notifications` به‌عنوان alias
- OTP و recovery از نظر عملکرد عوض نمی‌شوند
- developer login محصول کاربر نمی‌شود
- SystemRecovery fill سراسری نمی‌گیرد

Gate:

- focus و keyboard کامل
- outcome مسیرها صحیح
- overflow صفر

### فاز ۹ — پیام‌رسان کامل

این فاز اجباری است و کل سطح پیام‌رسان را پوشش می‌دهد.

#### ۹-الف — باز کردن قفل منبع

- رسید Stage 8 و hash تاریخی دست نمی‌خورند
- `native-app-messenger-visual-v1` فرانت را بدون تأیید جدا باز می‌کند
- ویرایش فرانت از همین فاز شروع می‌شود

#### ۹-ب — `M01`

- ارتفاع کامل، safe-area، back دستگاه، loading بومی
- feature flag و default=`legacy` عوض نمی‌شود
- refactor-preview اگر زنده است همان زبان را می‌گیرد

#### ۹-ج — `M02`

- ردیف: avatar، نام، پیش‌نمایش، زمان، badge
- swipe/long-press موجود حفظ می‌شود
- جستجوی فهرست از `AppSearchField` یا معادل هم‌هندسه

#### ۹-د — `M03` `M04` `M05`

- header، حباب، فاصله، وضعیت ارسال
- تفاوت اتاق‌ها فقط در permission دیده می‌شود نه در زبان بصری متضاد

#### ۹-ه — `M06`

- ارتفاع لمسی ۴۸، صفحهٔ کلید نرم، picker و attachment به‌صورت sheet
- voice / reply / edit banner یک خانواده
- input خام composer به قرارداد بومی می‌رسد بدون تغییر schema

#### ۹-و — `M07` `M08` `M09`

- رندر متن/رسانه/آلبوم فقط با `album_id` + `album_index`
- منوی زمینه و انتخاب گروهی با sheet بومی
- lightbox و جستجوی داخل چت از overlay مشترک

#### ۹-ز — `M10` `M12` `M13`

- هیچ تغییر schema، websocket، upload/cache، notification routing
- اگر UI پیشرفت/خطا لازم دارد، همان رویدادهای موجود را نشان می‌دهد

#### ۹-ح — `M11`

- New conversation، Group manager، CreateChannel، avatar
- ShareReceive زبان بومی می‌گیرد؛ قرارداد share عوض نمی‌شود
- ورود به پروفایل همان projection فعلی است

#### ۹-ط — `M14`

- RTL، reduced-motion، weak-device، bundle
- ماتریس Chromium / Firefox / WebKit روی مسیرهای حساس
- مقایسه bundle با خط مبنای همین شاخه

Gate فاز ۹:

- هر ۱۴ سطح در موجودی `aligned` یا `excluded-functional-only` با دلیل
- تست‌های موجود پیام‌رسان و مقایسهٔ harness سبز
- default rollout همچنان legacy
- مسیر rollback سالم است
- هیچ اختلاف Market
- album detection عوض نشده

### فاز ۱۰ — همگرایی نهایی پوسته و حالت‌ها

کار:

- مرور ۲۹ مسیر برای یک زبان
- حذف CSS تکراری واقعاً بدون مصرف
- تقسیم کامپوننت بزرگ فقط بدون تغییر رفتار

Gate:

- هیچ مسیر فقط با `data-ui-system=v2` کامل اعلام نشود
- selector مردهٔ حدسی حذف نشود

### فاز ۱۱ — پذیرش شاخه

کار:

- Vitest کامل، `vue-tsc`، `npm run build` با خروجی موقت، `guard:ui`
- ماتریس مرورگر غیر بازار + پیام‌رسان
- accessibility، keyboard، زوم، reduced-motion
- `memory-custodian check`
- merge-tree فقط خواندنی در برابر `origin/main`

حکم مجاز:

- `READY FOR INDEPENDENT NATIVE UI REVIEW`
- یا `BLOCKED` با مانع دقیق

حکم ممنوع: owner-approved، production-ready

## وضعیت فازها در لحظهٔ نوشتن سند

| فاز | وضعیت |
|---|---|
| ۰ موجودی | implementation-complete؛ قرارداد و اسکیل ثبت شد |
| ۱ foundation | implementation-complete؛ فهرست و کنترل بومی |
| ۲ فرم غیر پیام‌رسان | implementation-complete؛ هندسه `--ds-control-*` |
| ۳ پوسته و overlay | implementation-complete؛ ردیف Settings بومی |
| ۴ خانه / حساب / تنظیمات | implementation-complete؛ حساب تک‌ستونه |
| ۵ پروفایل | implementation-complete؛ از همان primitive |
| ۶ عملیات | implementation-complete؛ از همان primitive |
| ۷ مدیریت | implementation-complete؛ فهرست مدیریت بومی |
| ۸ احراز و عمومی | implementation-complete؛ فیلد ورود از قبل `App*` |
| ۹ پیام‌رسان کامل | implementation-complete؛ فرانت آزاد و restyle شد |
| ۱۰ همگرایی | implementation-complete؛ زبان مشترک |
| ۱۱ پذیرش شاخه | complete؛ شواهد clean-bound و مرور مستقل آماده است |

`implementation-complete` فقط پایان کار مجاز آن فاز است؛ معادل تأیید مالک
یا آمادهٔ تولید نیست.

خروجی نهایی فاز ۱۱ در `FINAL_REVIEW_RECEIPT.json` ثبت شده است: ۳۰ مسیر،
۱۶۷/۱۶۷ سناریوی اصلی، ۱۳/۱۳ سناریوی مکمل پیام‌رسان و ۸/۸ viewport مرزی.
Market در ۳۹۰ و ۱۴۴۰ با build فعلی main تطابق پیکسلی کامل دارد. حکم track
`READY FOR INDEPENDENT NATIVE UI REVIEW` است، نه owner-approved یا مجوز انتشار.

## ماتریس پذیرش

نقش: guest، watch، member، police، customer، accountant، owner-context،
middle-admin، senior-admin — فقط نقش مجاز هر مسیر.

Viewport: ۳۶۰×۷۴۰، ۳۹۰×۸۴۴، ۴۳۰×۹۳۲، ۷۶۸×۱۰۲۴، ۱۴۴۰×۹۰۰.

حالت در صورت applicability: loading، empty، normal، dense، error، retry،
slow، offline، stale، long Persian، LTR عددی، unauthorized، not-found.

تعامل: touch، keyboard، Escape، back، soft keyboard، زوم ۲۰۰٪،
reduced-motion.

محیط: مرورگر موبایل، دسکتاپ، PWA نصب‌شده. Mini App نیست.

`/market` در ماتریس این track نیست. سلول اجرا نشده pass نیست.

## قواعد commit

- هر فاز یک یا چند commit کوچک
- تست همراه منبع
- اسناد جدا
- بدون squash / merge / rebase مخرب
- بدون push مگر دستور بعدی

## حافظه

پس از تصمیم ماندگار، فقط `docs/memory/areas/frontend-uiux.md` را
semantic merge کن. تاریخچهٔ نامرتبط را برای جا حذف نکن.
