# رسید گیت مرحله ۱۲ — Shadow parity

تاریخ بازنگری: 2026-08-27

مبنای تاریخی پیاده‌سازی offline: `main@b3fce43050df6ad0bdbb5034f1f7f79df47f9c1e`

## وضعیت

هارنس offline و fail-closed شده، اما gate عملیاتی Stage 12 بسته است. live
legacy برای `GROUP_1`، `GROUP_2` و `PRIVATE_GOLD_CHANNEL` ناموجود
است و oracle، gate یا rollback زنده نیست. capture تک‌مالک جدید تنها
live authority است. این بازنگری هیچ deploy، promotion یا cutoverی انجام
نمی‌دهد.

## قرارداد parity زنده

- پیش از ساعت رسمی بازشدن، ابتدای prefix با session، release، owner و
  schedule پین و پس از بسته‌شدن، پایان byte-range و manifest آن seal می‌شود؛
- همان prefix immutable به دو projection ایزوله و version-pinned می‌رود:
  `REFERENCE_PROJECTION` و candidate با lane عملیاتی `PRIVATE_SHADOW`؛
- Telegram collector دوم وجود ندارد. `LEGACY` فقط برای historical compatibility
  پذیرفته می‌شود و evidence live را fail-closed رد می‌کند؛
- old DB، dump و snapshot فقط historical seed/regression هستند؛ freshness،
  capture loss یا parity زنده را اثبات نمی‌کنند؛
- capture manifest/inventory مستقل از fact است تا parser miss به‌اشتباه
  capture loss حساب نشود. هر منبع فعال باید event داشته باشد یا دلیل
  امضاشده صفربودن آن ثبت شود؛
- event/fact، point/mean/sample/unit، XAU/USDT consumed value، snapshot timeline و شبکه
  دقیق 14 نرخ با وضعیت `ESTIMATED/NO_DATA` و reason مقایسه می‌شوند؛
- duplicate صفر فقط با delivery-ledger receipt معتبر است. sequence/checkpoint،
  snapshot version و source→snapshot trace باید پیوسته باشند؛
- report فاقد raw text، identity و مقدار حساس است، hash قطعی و HMAC signature دارد
  و tamper را رد می‌کند.

## گیت پذیرش

- یک جلسه کامل بازار با schedule رسمی، pre-open pin و post-close seal؛
- capture loss، duplicate eligible fact، unresolved sequence gap و severity-1/2 برابر صفر؛
- XAU/USDT برابر، exact 14-rate grid کامل و same-input estimator mismatch صفر؛
- source event تا snapshot بعدی: p95 حداکثر 7 ثانیه؛
- receipt معتبر برای receiver restart، route partition، lost ACK، disk failure و rollback؛
- اختلاف parser/lifecycle فقط با label انسانی هش‌شده و تاییدشده قابل پذیرش است.

علاوه بر معیارهای بالا، receiptهای schedule، failure drill، transport و
model artifact باید توسط verifier مستقل و release-bound اعتبارسنجی شوند. این
verifier هنوز در repository وجود ندارد؛ بنابراین پیاده‌سازی فعلی عمداً قادر
نیست برای evidence زنده خروجی
`READY_FOR_EXPLICIT_PROMOTION_APPROVAL` بسازد. حتی پس از افزودن verifier نیز
این خروجی فقط درخواست مجوز است و promotion خودکار نیست.

## شواهد موجود و مانع باقی‌مانده

ریپلی هزاررکوردی، classification هفت‌گانه، HMAC/tamper و rehearsalهای
Docker شواهد regression مفیدند، اما جای پنجره live را نمی‌گیرند. Stage 13-A
نیز استقراری دستی/خارج از deploy رسمی بود. موارد باقی‌مانده:

1. پیاده‌سازی verifier مستقل و release-bound برای schedule، model artifact،
   transport و failure-drill receiptها؛
2. اجرای pre-open pin تا post-close seal در یک جلسه واقعی بازار؛
3. جمع‌آوری receiptهای واقعی timeline، duplicate ledger و failure soak؛
4. بستن gap اتصال market-data image/Compose/migration/receipt/rollback به deploy رسمی
   staging و production، بدون اجرای deploy تا زمان مجوز صریح.

برای replay آفلاین توصیه فعلی `HOLD_LIVE_OPEN_MARKET_REQUIRED` است؛ هر evidence
زنده تا زمان وجود verifier معتبر با `HOLD_BLOCKING_PARITY_FINDINGS` و کد
`TRUSTED_LIVE_ATTESTATION_UNAVAILABLE` متوقف می‌شود. `cutover_performed=false` است.

## Rollback

rollback زنده باید به image digest، snapshot و authority marker پین‌شده و سالم
قبلی private pipeline برگردد، نه collector یا snapshot legacy کهنه. اگر هیچ
خروجی تازه و سالمی وجود ندارد، محصول باید `STALE/NO_DATA` را fail-closed
نشان دهد. unitهای host-native `coin-capture`/`market-channel-capture` نسخه
میزبانی همان سیستم جدیدند؛ auto-return یا guard موقت `/run` rollback پایدار
محسوب نمی‌شود و پس از reboot/ازدست‌رفتن guard خطر owner overlap دارد.
