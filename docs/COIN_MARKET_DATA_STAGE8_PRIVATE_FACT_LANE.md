# رسید گیت مرحله ۸ — مسیر خصوصی Market Facts

تاریخ اجرا: 2026-08-26

پیاده‌سازی: `main@633e761613d3dfb3d5713ac44c488aa4258a47b2`

آزمون تکمیلی قطع گیرنده: `main@0cbd008a`

## نتیجه

مرحله ۸ بدون deploy یا تغییر authority تکمیل شد. Market Fact در همان transaction
آرشیو PostgreSQL داخل outbox اختصاصی نوشته می‌شود، worker آن را فقط از مسیر خصوصی
مبتنی بر mTLS و HMAC می‌فرستد و receiver بات پس از apply پایدار ACK پیوسته می‌دهد.
این lane هیچ صف، checkpoint، Redis یا `change_log` مشترکی با Product Sync ندارد.

## تصمیم مهم قرارداد

`source_sequence` هویت ترتیبی fact منطقی است و در revision ثابت می‌ماند؛
`delivery_sequence` cursor یکنواخت انتقال هر revision است. بنابراین اصلاح parser همان
fact را با `fact_revision` جدید منتقل می‌کند، بدون آنکه به‌اشتباه fact تازه محسوب شود
یا ترتیب transport شکسته شود.

## تضمین‌های پیاده‌شده

- outbox، fact، revision و projection تخصصی به‌صورت اتمیک در PostgreSQL ثبت می‌شوند؛
- batch حداکثر ۱۰۰ event، حداکثر ۷۶۸ KiB و flush حداکثر ۲۵۰ ms دارد؛
- head هر stream قابل دورزدن نیست؛ retry، dead-letter یا repair ترتیب را نمی‌شکند؛
- backoff نمایی bounded با jitter، سقف ۳۰ ثانیه و alert از تلاش هشتم دارد؛
- ACK فقط برای batch، stream و sequence متناظر پذیرفته و checkpoint اتمیک می‌شود؛
- receiver با SQLite `FULL`، revision-aware، idempotent و gap/conflict fail-closed است؛
- TLS به client certificate معتبر نیاز دارد؛ HMAC بد، nonce تکراری، clock skew و peer
  خارج allowlist رد می‌شوند؛ raw payload و secret در خطا یا heartbeat ثبت نمی‌شود؛
- قطع receiver هیچ public fallbackی فعال نمی‌کند و outbox را دست‌نخورده نگه می‌دارد.

## نتایج گیت

آزمون ۱۰۰۰ fact:

- fact/outbox/ACK: `1000/1000/1000`؛
- ۱۰ batch و بازپخش ACK گم‌شده به‌صورت duplicate امن؛
- قطع receiver: صفر apply و صفر ACK؛ تمام outbox پس از بازگشت منتقل شد؛
- زمان publish کل: `8277.870 ms`؛
- ACK p50: `70.106 ms`؛ p95 و p99: `81.517 ms`؛
- container و volume موقت کاملاً پاک شدند.

گیت transport واقعی loopback نیز mTLS دوطرفه، رد گواهی نامعتبر، رد HMAC نامعتبر،
پایداری checkpoint پس از restart، duplicate ایمن و نبود raw در log را اثبات کرد.

گیت Docker کامل روی SHA پیاده‌سازی:

- image reproducible: `sha256:87428f...`، Python `3.11.16`، اندازه `147.591 MiB`؛
- secret scan، migration ۲۳ جدولی، اجرای دوم no-op، persistence و rollback سبز؛
- هشت service وب/داده و چهار service بات بدون port غیرمنتظره؛
- receiver fixture، replay duplicate، lockها و cleanup کامل پاس شدند.

۵۴ آزمون متمرکز Stage 8 و قراردادهای وابسته سبز بودند. اجرای کل suite به‌علت وسعت
و خطاهای قدیمی harness به‌عنوان گیت این مرحله استفاده نشد و این رسید ادعای سبز بودن
کل suite را ندارد.

## مرز عملیاتی

هیچ endpoint زنده، PostgreSQL زنده، feed مدل، staging/production deployment،
cutover یا تغییر Product Sync انجام نشد. مرحله ۹ تنها consumer محلی facts را به
Market Store خصوصی shadow متصل می‌کند؛ انتخاب آن به‌عنوان feed اصلی جداگانه است.
