# Stage 0B — برنامه سناریومحور نمونه‌های «مالی مدرن»

تاریخ: ۲۰۲۶-۰۷-۱۸

وضعیت: مالک محصول `0B-1..0B-6` را تأیید کرده و در `2026-08-08T20:57:28.073Z` پیشروی بی‌وقفه تا تکمیل roadmap یا توقف صریح را مجاز کرده است. closure فنی/Sites `0B-6`، Stage 1 و Stage 2 بسته شده‌اند؛ Stage 3 `authorized_not_started` است.

جهت مصوب: **مالی مدرن**

## هدف

پیش از کدنویسی، جهت منتخب روی سناریوهای واقعی و پرتراکم تمام خانواده‌های اصلی وب‌اپ آزمایش شود. هر بسته ابتدا در موبایل طراحی و تأیید می‌شود و سپس در صورت نیاز رفتار adaptive دسکتاپ آن نشان داده می‌شود.

داده پرتراکم فقط برای آزمون تحمل layout، متن بلند و stateهای واقعی است؛ به معنی نمایش هم‌زمان همه metadata نیست. همه checkpointها تابع [سیاست خلوت‌بودن و ارزش اطلاعات](WEBAPP_UI_UX_REDESIGN_V2_CONTENT_MINIMALISM_POLICY_20260808.md) هستند و باید همراه نمونه، inventory محتوای `Keep / On demand / Remove` ارائه دهند.

## ترتیب گام‌به‌گام

### 0B-1 — ورود، دعوت و ثبت‌نام

وضعیت: نمونه‌ها، state atlas، حالت‌های کیبورد و adaptive desktop در `docs/uiux-stage0b-auth/` ثبت شده‌اند. مالک محصول ادامه به 0B-2 را مجاز کرده است؛ این مجوز، گزارش‌های خارجی قبلی را false-approved نمی‌کند و مجوز runtime نیست.

کاربر از دعوت یا ورود عادی می‌آید، مرحله فعلی را می‌فهمد، OTP و اطلاعات ثبت‌نام را بدون گم‌کردن داده طی می‌کند و در خطا مسیر ادامه روشن دارد.

خروجی تصمیم: shell عمومی، فرم، step indicator، خطای فیلد، loading، بازگشت، رفتار کیبورد موبایل و حذف metadata/توضیح تکراری مسیر از قاب‌های محصول.

### 0B-2 — خانه و پوسته احراز‌شده

وضعیت: تکمیل و تأیید مالک محصول برای ادامه به 0B-3. صفحه مستقل Figma شامل ۹ قاب خانه، modal نشست، قرارداد route/layer، پنج proof responsive و proof دقیق دسکتاپ است؛ شش screenshot مستقیم و harness ۱۹/۱۹ نیز ثبت شده‌اند. مرجع جزئیات: `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_HOME_SHELL_CHECKPOINT_20260808.md`.

کاربر در چند ثانیه فقط هویت لازم، وضعیت نیازمند توجه و اقدام بعدی را می‌فهمد. رویداد یا عددی که اقدام ایجاد نمی‌کند در نمای پیش‌فرض دیده نمی‌شود. جایگاه ویجت بازار فقط به‌صورت قفل‌شده و بدون بازطراحی داخلی نمایش داده می‌شود.

خروجی تصمیم: header، bottom navigation، اولویت اطلاعات، کارت/گروه‌بندی، وضعیت offline/stale، محل غیرمزاحم پیشنهاد نصب PWA پس از ورود و inventory ضرورت همه واحدهای محتوای پیش‌فرض.

### 0B-3 — عملیات و workspace مشتری/حسابدار

وضعیت: طراحی و evidence فنی تکمیل و با تأیید مالک محصول برای ادامه بسته شده است. صفحه `55:2` در Figma رسمی شامل ۱۰ root موبایل، state atlas، پنج عرض مرجع و master/detail دقیق ۱۴۴۰×۹۰۰ است. Foundations و component catalog نیز روی صفحه‌های `41:2` و `46:2` ثبت شده‌اند. مرجع جزئیات: `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_OPERATIONS_WORKSPACES_CHECKPOINT_20260808.md`.

مالک رابطه را پیدا می‌کند، وارد پرونده می‌شود، اقدام را انجام می‌دهد و نتیجه را در همان context می‌بیند. موبایل context را حفظ می‌کند و دسکتاپ list/detail هم‌زمان ارائه می‌دهد.

خروجی تصمیم: عملیات بدون مقصد مرده، list XOR detail در موبایل با بازیابی query/filter/scroll، جست‌وجو، دعوت pending، ویرایش مالی before/after با اثر future-only، شرح وظیفه، feedback همان‌جا، پایان نشست، حذف حساب با cascade واقعی، state/recovery و master/detail بدون افزودن واقعیت تازه. تعداد کل روابط badge نمی‌شود؛ count فقط برای صف نیازمند اقدام مجاز است.

### 0B-4 — مدیریت کاربران و دعوت‌ها

وضعیت: تکمیل و بسته‌شده با تأیید صریح بصری مالک محصول در ۲۰۲۶-۰۸-۰۸. Phase 0، صفحه و nodeهای Figma، evidence محلی و پیش‌نمایش خصوصی Sites پاس شده‌اند. قرارداد ۱۰ root موبایل، state/permission atlas، پنج width proof و یک user master/detail دقیق ۱۴۴۰×۹۰۰ قطعی است. مرجع نهایی: `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ADMIN_USERS_INVITATIONS_CHECKPOINT_20260808.md`.

ادمین کاربر را پیدا می‌کند، وضعیت و ریسک اقدام را می‌بیند، تأیید معنی‌دار می‌گیرد و نتیجه موفق یا ناموفق را از دست نمی‌دهد.

خروجی تصمیم: ورودی خلوت مدیریت، جست‌وجوی پایدار بدون role/status filter ناقص، فقط metadata مؤثر بر تصمیم، جدایی محدودیت معامله/سهمیه تعدادی/غیرفعال‌سازی/حذف حساب، confirm و feedback همان‌جا، دعوت استاندارد بدون نقش مدیر ارشد یا ادعای delivery Telegram؛ تعداد دسته‌ها یا ابزارها KPI نیست.

### 0B-5 — حساب، پروفایل، امنیت و اعلان‌ها

وضعیت: تکمیل و بسته‌شده با تأیید صریح بصری مالک محصول در ۲۰۲۶-۰۸-۰۸. صفحه canonical `117:2` شامل ۱۰ root موبایل، state/route/visibility/push matrix، پنج width proof و desktop دقیق `1440×900` است. audit مستقیم schema 2 و harness محلی هر دو `27/27` و preview خصوصی Sites source-bound پاس شده‌اند. این تأیید مجوز runtime نیست. مرجع نهایی: `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ACCOUNT_PROFILE_SECURITY_NOTIFICATIONS_CHECKPOINT_20260808.md`.

کاربر مقصد canonical را پیدا می‌کند، نشست یا حافظه را مدیریت می‌کند و loading، empty و error را با هم اشتباه نمی‌گیرد.

خروجی تصمیم: account hub خلوت و نقش‌محور، پروفایل شخصی و عمومی با افشای تدریجی، نشست‌های واقعی و «پایان همه نشست‌های دیگر»، پاک‌سازی حافظه محلی، مرکز اعلان بدون count ساختگی، Push فقط در حالت واقعاً قابل اقدام، empty/error/retry متمایز، مقصدهای canonical و حذف هدر، توضیح، مقصد و نشانه وضعیت تکراری.

### 0B-6 — قرارداد نهایی سیستم و پذیرش

وضعیت: **تکمیل و بسته‌شده.** `ownerSystemContractApproval.status: approved`، `continuousProgressionAuthorized: true` و `runtimeImplementationAuthorized: true` ثبت شده است؛ Stage 1 پس از closure اجرا و بسته شد و Stage 2 پس از آن آغاز شده است. مرجع نهایی Stage 0: [checkpoint قرارداد نهایی](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_FINAL_SYSTEM_CONTRACT_CHECKPOINT_20260808.md)؛ مرجع Stage 1: [checkpoint Stage 1](WEBAPP_UI_UX_REDESIGN_V2_STAGE1_TRUST_CONTINUITY_CHECKPOINT_20260808.md)؛ وضعیت جاری: [checkpoint Stage 2](WEBAPP_UI_UX_REDESIGN_V2_STAGE2_PROTECTED_DESIGN_SYSTEM_CHECKPOINT_20260809.md).

نمونه‌های تأییدشده کنار هم قرار می‌گیرند تا رنگ، typography، spacing، radius، navigation، stateها و motion در همه آن‌ها یک قرارداد واحد داشته باشد.

خروجی تصمیم: design contract نهایی `SYS-01..SYS-14`، ماتریس ضرورت محتوای همه خانواده‌ها، inventory دقیق ۲۹ route و catch-all system-owned، ۳۲ assertion fail-closed و traceability مرحله‌های 1 تا 8. Auth canonicalization، Home binding audit/rebind و رفع بدهی ناوبری Operations در Figma بسته شده‌اند. صفحه `168:1974`، ۹ export مستقیم و audit `32/32`، harness نهایی `32/32` با semantic hardening، baseline `35/35` فایل و `322/322` تست، build/guard و Sites خصوصی owner-only source-bound پاس هستند. Stage 1 پس از این closure اجرا و با گیت فنی خودش بسته شد؛ progression بدون تأیید جداگانه هر Stage اما با گیت فنی خود آن Stage ادامه دارد.

## قواعد توقف و تأیید

- هر گام checkpoint فنی مستقل دارد؛ اصلاح blocker همان گام پیش از رفتن به گام بعد انجام می‌شود، اما تأیید جداگانه مالک لازم نیست مگر مالک صریحاً توقف/اصلاح بخواهد.
- هیچ checkpoint با واحد محتوای همیشه‌نمایانِ بدون اثر مشخص بر تصمیم، اقدام، وضعیت ضروری یا ریسک پذیرفته نمی‌شود.
- نبود overflow، fit مناسب یا فضای سفید کافی جای content-necessity audit را نمی‌گیرد.
- هیچ نمونه‌ای اجازه تغییر UI یا رفتار داخلی بازار و پیام‌رسان را ندارد.
- تأیید مالک برای قرارداد و ادامه کل roadmap ثبت شده است؛ تأیید جداگانه هر Stage لازم نیست مگر مالک صریحاً توقف/تغییر مسیر بدهد.
- Stage 1 با pass شدن Sites و final source binding `0B-6` مجاز شده است؛ هر Stage بعدی نیز گیت فنی، test و protected diff خودش را دارد.

## گام بعدی

Stage 1 — اعتماد و تداوم کار و **Stage 2 — Design System V2 محافظت‌شده** هر دو **`complete`** هستند. `nextAuthorizedRuntimeStage = Stage 3`، `stage3RuntimeImplementationAuthorized = true` و `stage3RuntimeWorkStarted = false` است. progression پس از عبور گیت هر Stage تا تکمیل roadmap یا توقف صریح مالک ادامه می‌یابد.
