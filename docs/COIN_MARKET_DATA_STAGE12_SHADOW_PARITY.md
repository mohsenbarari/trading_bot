# رسید گیت مرحله ۱۲ — Shadow parity

تاریخ اجرا: 2026-08-26

پیاده‌سازی و گیت offline: `main@b3fce43050df6ad0bdbb5034f1f7f79df47f9c1e`

## وضعیت

پیاده‌سازی، replay و failure-classification این مرحله کامل است، اما gate عملیاتی Stage 12
هنوز کامل نیست؛ یک جلسه کامل بازار باز روی pipeline مستقرشده لازم است. ابزار عمداً با شواهد
historical نتیجه `HOLD_LIVE_OPEN_MARKET_REQUIRED` می‌دهد و هیچ cutover انجام نمی‌دهد.

## قرارداد parity

- laneهای `LEGACY` و `PRIVATE_SHADOW` با window و model artifact یکسان مقایسه می‌شوند؛
- capture manifest مستقل از factهاست تا parser miss به‌اشتباه capture loss محسوب نشود؛
- event/fact، point/mean/sample/unit، estimator output و زمان source→snapshot مقایسه می‌شود؛
- اختلاف به `CAPTURE`, `PARSER`, `LIFECYCLE`, `UNIT`, `TIMING`, `TRANSPORT` یا
  `ESTIMATOR` طبقه‌بندی می‌شود؛
- اختلاف parser/lifecycle فقط با label انسانی هش‌شده و تاییدشده قابل پذیرش است؛
- report فاقد raw text و identity است، hash قطعی و HMAC signature دارد و tamper رد می‌شود؛
- collector فقط Market Store را read-only می‌خواند. نبود capture manifest کامل یا timeline
  snapshot، promotion را fail-closed مسدود می‌کند.

## نتایج replay

- ۱۰۰۰ event در هر lane و شش خانواده منبع؛
- capture loss، duplicate eligible fact و unresolved sequence gap: صفر؛
- mismatch مقدار مصرف‌شده XAU/USDT: صفر؛
- mismatch estimator با model artifact و input hash یکسان: صفر؛
- source event تا snapshot: p95 برابر `5.8s` در برابر سقف `7s`؛
- ماتریس تزریق خطا هر هفت دسته اختلاف را درست تشخیص داد؛
- parser mismatch بدون label severity-2 و همان اختلاف با label `PRIVATE_CORRECT` پذیرفته شد؛
- signature verify و tamper detection پاس شد؛ report هیچ raw Telegram/identity/URL نداشت؛
- ۲۸ آزمون متمرکز Stageهای 8 تا 12 سبز بود.

## گیت بازگشتی Docker

- image متصل به SHA با digest `sha256:8b6b1d18...` ساخته شد؛
- rehearsal کامل Compose image تکرارپذیر `sha256:3cb4aed1...`، Python `3.11.16` و
  اندازه `147.825 MiB` را ثبت کرد؛
- secret scan، migration second-pass no-op، schema ۲۶ جدولی، ۸/۴ service، دو receiver
  خصوصی، صفر port غیرمنتظره، recreate، single-owner و rollback پاس شد؛
- container، image، network و temporary rootهای rehearsal همگی پاک شدند.

## gate باقی‌مانده

پس از مجوز استقرار shadow باید capture manifest واقعی، timeline واقعی snapshot، یک جلسه کامل
بازار باز و failure soak واقعی جمع شود. promotion فقط وقتی مجاز است که severity-1/2 صفر،
XAU/USDT برابر، p95 حداکثر ۷ ثانیه، report امضاشده و rollback موفق باشد. در حال حاضر
`LEGACY` primary و توصیه رسمی `HOLD_LIVE_OPEN_MARKET_REQUIRED` است.
