# رسید گیت مرحله ۹ — آداپتر Market Store روی سرور بات

تاریخ اجرا: 2026-08-26

پیاده‌سازی: `main@75b87cf117fea45785c83b5cef5fbc9353605469`

گیت سازگاری estimator: `main@a491632b`

## نتیجه

مرحله ۹ بدون deploy یا تغییر مرجع مدل تکمیل شد. receiver پایدار Stage 8 به‌صورت
read-only مصرف می‌شود و آداپتر تنها writer پایگاه خصوصی Market Store است. observation،
projection fact، offer dimensions، rejection و checkpoint هر delivery در یک transaction
SQLite ثبت می‌شوند.

در ممیزی پیش از اتصال، projection هرات اصلاح شد: هرات دیگر quote ساده محسوب نمی‌شود و
`OFFER/TRADE`، settlement، trade form، side و quantity آن در `OBSERVATION` حفظ می‌شود.

## رفتار مصرف

- قیمت Toman یا project-thousand بدون تبدیل ثانویه وارد Market Store می‌شود؛
- guard موجود Market Store واحد، currency، magnitude، timestamp و quantity pair را پیش
  از eligible شدن کنترل می‌کند؛ timestamp بیش از ۳۰ ثانیه در آینده نیز رد می‌شود؛
- offer fact ابعاد اقتصادی مرجع را نگه می‌دارد؛ معاملهٔ سکه price/quantity توافقی را با
  instrument، side، settlement و trade form آفر ریشه ترکیب می‌کند؛
- نتیجهٔ آبشده خصوصی قیمت immutable آفر را نگه می‌دارد و فقط quantity اجراشده را از
  outcome جدا می‌گیرد؛ `final_price/final_quantity` ساخته نمی‌شود؛
- outcome غیرمعامله‌ای معتبر `AUDIT_ONLY` است، نه observation اقتصادی؛
- revision همان event key را upsert می‌کند و fact/source sequence را قابل ردیابی نگه
  می‌دارد؛ restart دوباره‌نویسی یا تبدیل واحد ایجاد نمی‌کند؛
- fact معتبرِ wire که mapping اقتصادی ناسالم دارد در ledger کمینه رد می‌شود، checkpoint
  همان stream در همان transaction جلو می‌رود و delivery سالم بعدی مصرف می‌شود؛ خطای
  database یا sequence gap fail-closed است.

## سوییچ feed و rollback

سه حالت صریح وجود دارد:

- `LEGACY`: مرجع اصلی legacy است؛ inbox خصوصی حفظ می‌شود؛
- `PRIVATE_SHADOW`: مرجع اصلی legacy و ورودی shadow پایگاه خصوصی است؛
- `PRIVATE_PRIMARY`: مدل اصلی و shadowها پایگاه خصوصی واحد را می‌خوانند.

هیچ حالت `AUTO` یا fallback عمومی وجود ندارد. انتخاب feed از capture/receiver مستقل است؛
بنابراین rollback به `LEGACY` دریافت و نگهداری facts را از بین نمی‌برد. componentهای
input snapshot با event key به projection و از آنجا به fact/revision منبع متصل‌اند.

## نتایج گیت

- پنج آزمون یکپارچهٔ Stage 9: offer/trade توافقی، revision/restart، malformed sibling،
  feed rollback، input snapshot و estimator سازگار؛ همگی سبز؛
- existing `coin-rate-engine-v8` بدون تغییر artifact روی Market Store تولیدشده اجرا و
  snapshot اتمیک موجود با وضعیت `PUBLISHED` ساخته شد؛
- هیچ تبدیل دوبارهٔ `187500 PROJECT_THOUSAND_TOMAN` رخ نداد؛
- مجموعه ۲۷ آزمون Stage 3/8/9 و rehearsal contract سبز بود؛
- گیت Docker کامل روی SHA پیاده‌سازی پاس شد: image تکرارپذیر
  `sha256:78a3999a...`، Python `3.11.16`، اندازه `147.656 MiB`، secret scan، Compose،
  migration ۲۳ جدولی، persistence، مالک دوم fail-closed، rollback و cleanup کامل.

## مرز عملیاتی

Compose در حالت پیش‌فرض `LEGACY` می‌ماند. هیچ receiver یا adapter زنده، feed مدل،
staging/production deployment، authority switch یا cutover انجام نشد.
