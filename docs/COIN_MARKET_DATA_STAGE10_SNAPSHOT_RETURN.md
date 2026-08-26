# رسید گیت مرحله ۱۰ — بازگشت Snapshot به WebApp

تاریخ اجرا: 2026-08-26

پیاده‌سازی و گیت: `main@fa4efd846d7f677e609b1173a1f447f50b561164`

## نتیجه

مسیر کامل snapshot از Market Store خصوصی بات تا projection فقط‌خواندنی WebApp پیاده شد،
اما deploy و تغییر authority انجام نشد. estimator موجود در یک read transaction ثابت
اجرا می‌شود، snapshot دارای hash/version در فایل اتمیک منتشر می‌شود و sender آن را فقط
با mTLS/HMAC روی شبکه خصوصی به receiver وب می‌فرستد.

## قرارداد و trace

هر snapshot علاوه بر نرخ‌ها و health شامل این موارد است:

- `feed_mode` مستقل برای `PRIVATE_SHADOW` و `PRIVATE_PRIMARY`؛
- `input_snapshot_hash` و hash خود snapshot؛
- source fact/event و revision برای هر component؛
- زمان‌های دقیق `occurred`, `available`, `parsed`, `transferred`, `inferred` و در web
  view زمان‌های `received` و `published`؛
- point، mean واقعی همان پنجره، unit، sample count، selection method، fallback و
  freshness هر component.

projection آداپتر زمان‌های منبع تا انتقال را نگه می‌دارد؛ component بدون trace کامل
اجازهٔ انتشار observed ندارد. snapshot وب هیچ محاسبهٔ قیمت مستقلی انجام نمی‌دهد و rates
آن بایت‌به‌بایت از artifact مدل می‌آیند.

## پایداری و ترتیب

- allocator نسخه و payload pending در SQLite `FULL` بات ذخیره می‌شوند؛ crash میان commit
  و rename با همان version/payload بازیابی می‌شود؛
- sender فقط پس از ACK منطبق با id/version/hash checkpoint می‌دهد؛ lost ACK با duplicate
  امن ترمیم می‌شود و قطع مسیر public fallback ندارد؛
- receiver نسخه را برای هر lane یکنواخت می‌کند؛ نسخهٔ قدیمی و conflict هم‌نسخه رد می‌شود؛
- payload ابتدا در DB پایدار، سپس web view و cache generation اتمیک نوشته و realtime event
  با event ID قطعی در outbox ثبت می‌شود؛
- `PRIVATE_SHADOW` هرگز فایل `PRIVATE_PRIMARY` را overwrite نمی‌کند؛
- اگر بازگشت snapshot قطع شود، reader بر اساس زمان تولید `STALE` نشان می‌دهد و نرخ جدید
  یا query جایگزین نمی‌سازد.

## نتایج گیت

- سه آزمون یکپارچه end-to-end و ۴۱ آزمون متمرکز مراحل 3/8/9/10 سبز بود؛
- hash bot state، ACK و web view یکسان ماند؛
- cache generation و realtime event فقط برای همان snapshot committed ساخته شدند؛
- regression، conflict، duplicate، lost ACK، restart pending و stale route-cut پاس شدند؛
- schema تولیدشده current است؛
- گیت Docker کامل پاس شد: image تکرارپذیر `sha256:48dbc4c6...`، Python `3.11.16`،
  اندازه `147.764 MiB`، secret scan، Compose ۸/۴ service، migration ۲۳ جدولی،
  persistence، rollback و cleanup کامل.

## مرز عملیاتی

حالت پیش‌فرض همچنان `LEGACY` است. endpoint زنده، reverse proxy، WebApp authority، cache
عملیاتی، realtime عملیاتی، staging/production deploy و cutover تغییر نکردند. اتصال view
آماده‌شده به UI عملیاتی فقط در cutover مجاز انجام می‌شود.
