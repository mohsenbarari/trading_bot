# Stage 0B-4 — Admin users and standard invitations evidence

وضعیت: **تکمیل و با تأیید صریح بصری مالک محصول در ۲۰۲۶-۰۸-۰۸ بسته شده است.** Phase 0، بسته رسمی Figma، local evidence و Sites خصوصی ثبت شده‌اند.

منبع editable اصلی این checkpoint صفحه `03 — Stage 0B-4 Admin Users & Invitations` با شناسه `75:2` در [فایل Figma پروژه](https://www.figma.com/design/z8jgJxST4O2APzWnlyP9gv) است. freeze نهایی در `2026-08-08T13:43:05.564Z`، audit schema 7 پاس در `2026-08-08T13:44:57.691Z` و capture مستقیم در `2026-08-08T13:45:57.660Z` ثبت شد. Foundations روی `41:2` و component catalog روی `46:2` قرار دارند.

این پوشه بسته evidence versioned و harness مشتق‌شده Stage است و کد runtime محصول نیست.

## ترتیب مرجع

در صورت اختلاف شواهد:

1. checkpoint الزام‌آور و سیاست خلوتی هدفمند؛
2. nodeهای editable نهایی Figma که با همان قرارداد هم‌راستا هستند؛
3. `FIGMA_SNAPSHOT_MANIFEST.json`؛
4. PNGهای مستقیم Figma؛
5. پیش‌نمایش تعاملی Sites؛
6. harness محلی و PNGهای مشتق‌شده.

Sites و harness برای مرور، fit، state و responsive validation هستند؛ هیچ‌کدام منبع canonical طراحی یا اثبات رفتار runtime نیستند.

## خروجی‌های مستقیم Figma — ثبت‌شده

- `assets/figma-admin-entry-directory-scenarios.png`
- `assets/figma-admin-user-decision-scenarios.png`
- `assets/figma-standard-invitation-scenarios.png`
- `assets/figma-admin-state-permission-atlas.png`
- `assets/figma-responsive-and-desktop-proofs.png`
- `assets/figma-desktop-user-master-detail-1440x900.png`
- `assets/figma-stage0b4-audit-metrics.json`

source node، timestamp و SHA-256 نهایی همه خروجی‌ها در manifest ثبت شده‌اند.

## پیش‌نمایش Sites — منتشرشده و خصوصی

پیش‌نمایش مشتق‌شده با عنوان `Trading Bot UI/UX — Stage 0B-4` در [URL خصوصی مالک](https://trading-bot-uiux-stage0b4.mohsenbarari235.chatgpt.site) منتشر شده است. Figma با file key برابر `z8jgJxST4O2APzWnlyP9gv` همچنان منبع canonical است؛ Sites فقط derivative خصوصی است.

- project: `appgprj_6a772e6ffef481918c82f9c70b4c71c8`
- slug: `trading-bot-uiux-stage0b4`
- source commit: `0874c43781805bf1404226cc1948485ebdbb04f1`
- version: `3` / `appgprj_6a772e6ffef481918c82f9c70b4c71c8~appgver_c31913427d2081918c4e4b3a290620cb`
- deployment: `appgdep_6a77396e6a5c8191b9945deaf884107d`، موفق در `2026-08-08T14:13:17.018345Z`
- archive: `901120` بایت، `28` فایل، SHA-256 `eab92d01cea68f922c80bd90c0229aa16088367f55a7d8701a457498ce0a85ce`

دسترسی پیش و پس از deploy به‌صورت `custom` و owner-only راستی‌آزمایی شد: یک کاربر مجاز، صفر گروه و صفر بازدیدکننده خارجی. probe ناشناس در `2026-08-08T14:13:54Z` با `HTTP 401` و صفحه `Sign in required` متوقف شد؛ render ناشناس ادعا نمی‌شود. signed-in live content نیز بدون bypass token واکشی نشد؛ مالک محصول طراحی را در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح تأیید کرد.

این preview نباید:

- قابلیت یا state خارج از قرارداد Figma بسازد؛
- delivery، permission یا mutation واقعی را شبیه اثبات runtime نمایش دهد؛
- UI داخلی بازار یا پیام‌رسان را بازطراحی کند؛
- با افزودن KPI، نمودار یا متن توضیحی، تراکم طراحی canonical را تغییر دهد.

build production، audit سطح high با صفر vulnerability، Worker/ASSETS و بسته hosting پاس شده‌اند. HTML منبع و built archive با SHA-256 `ca44c01da79ce479d34efb22f89e39c3b8f1c1dda9008c42fc7c3658c7178ec7` byte-identical و چهار فونت محلی Vazirmatn حاضر و byte-identical هستند؛ query خطاهای Worker برای deployment نهایی در پنجره ۱۵ دقیقه‌ای صفر event داشت. drift review فنی artifact/source-bound پاس است.

Figma، exportهای مستقیم، Sites و harness فقط از نام، هویت و شماره تلفن synthetic استفاده می‌کنند؛ داده واقعی کاربر/production وارد artifact طراحی نمی‌شود.

## خروجی‌های محلی مشتق‌شده — ثبت‌شده

- `assets/local-evidence/local-admin-entry-user-directory.png`
- `assets/local-evidence/local-user-decision-flow.png`
- `assets/local-evidence/local-standard-invitation-flow.png`
- `assets/local-evidence/local-admin-users-state-atlas.png`
- `assets/local-evidence/local-admin-users-permission-matrix.png`
- `assets/local-evidence/local-admin-users-responsive-sweep.png`
- `assets/local-evidence/local-admin-user-master-detail-1440x900.png`
- `assets/local-evidence/local-admin-users-invitations-validation-metrics.json`

فایل‌های `admin-users-invitations-evidence.html` و `capture-evidence.cjs` بسته مشتق‌شده را ساخته‌اند. اجرای نهایی `2251888-1786196776466` در `2026-08-08T13:46:25.656Z` هر `25 / 25` assertion را با صفر failure و صفر page error پاس کرده است؛ هفت PNG و metrics محلی checksum نهایی دارند و pre/post assertionها یکسان و canonical DOM هنگام capture بدون تغییر بوده است. این شواهد ثانویه رفتار runtime را اثبات نمی‌کنند.

## قرارداد bounded این بسته

- ۱۰ root موبایل مصوب؛
- پنج proof از فهرست کاربر در عرض‌های ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰؛
- یک master/detail دقیق ۱۴۴۰×۹۰۰؛
- state atlas و permission matrix؛
- شش ردیف permission صریح، شامل `مدیر میانی → خودش` به‌صورت visible/read-only و `مدیر میانی → هر مدیر دیگر` به‌صورت hidden؛
- recovery محافظت‌شده `106:743` و reuse دقیق navigation دسکتاپ `108:743` در root `89:758`؛
- هدف شخصی برای همه ادمین‌ها و مدیر ارشد هم‌سطح برای تمام actionهای حساس read-only؛ مدیر میانی بدون target از **ادمین دیگر** و با self visible/read-only؛
- نشان pending فقط غیرعددی «نیازمند رسیدگی»، بدون ساخت total از endpoint محدود به ۱۰۰ مورد؛
- چهار خانواده اقدام جدا: محدودیت معامله، محدودیت تعدادی، غیرفعال‌سازی و حذف حساب؛
- deadline `M04/M05` برابر `۲۲ مرداد ۱۴۰۵، ۱۴:۳۰`، deadline `M06` برابر `۱۹ مرداد ۱۴۰۵، ۱۴:۳۰` و سهمیه کالای `M05` بر حسب تعداد item، نه گرم؛
- غیرفعال‌سازی بدون expire/collect آفر؛ cascade آفر فقط در حذف حساب؛
- نقش دعوت مدیر میانی دقیقاً `watch/standard` و مدیر ارشد دقیقاً `watch/standard/police/middle`، هرگز `super`؛
- مدیر میانی فقط pendingهای خودش و مدیر ارشد همه pendingها را می‌بیند/revoke می‌کند؛
- URL خام همیشه پنهان و بدون fallback محصول؛ بدون resend/history/bulk یا ادعای delivery Telegram؛
- صفر تغییر runtime و صفر بازطراحی interior بازار/پیام‌رسان.

جزئیات قرارداد در `../WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ADMIN_USERS_INVITATIONS_CHECKPOINT_20260808.md` و ممیزی محتوا در `CONTENT_NECESSITY_AUDIT.md` ثبت شده است.
