# رسید گیت مرحله ۱۱ — Backfill و تجمیع تاریخچه

تاریخ اجرا: 2026-08-26

پیاده‌سازی: `main@16fc8585972a6c214fab9ef35d96792138fae8b1`

گیت نهایی: `main@22e9fa5c97c2bceabb921399e496d135e1b74f40`

## نتیجه

مسیر import تاریخچه برای دو گروه سکه، کانال خصوصی آبشده، هرات، XAU و USDT به
صورت idempotent و fail-closed پیاده شد. هیچ داده‌ای در staging یا production وارد نشد و
هیچ authority یا feed فعالی تغییر نکرد؛ اجرای واقعی تاریخچه فقط پس از backup و مجوز
cutover انجام می‌شود.

## قرارداد و حریم خصوصی

- هر bundle فقط یک source، artifact hash قطعی و شناسه‌های opaque دارد؛ URL، لینک پست،
  credential/session، envelope تلگرام و identity مستقیم عضو قرارداد نیستند.
- متن خام منتخب و participant فقط اگر پیش از مرز import رمز شده باشند در جدول‌های جداگانه
  وب ذخیره می‌شوند؛ plaintext از این مرز عبور نمی‌کند.
- سه جدول افزایشی برای batch lineage، revision item و quarantine digest-only اضافه شد؛
  schema مستقل Market Data اکنون version 2 و دارای ۲۶ جدول است.
- logical identity و source revision جدا هستند؛ ترتیب revision حفظ، content conflict و
  regression قرنطینه و خطای storage/connection به‌جای ادامهٔ ظاهراً موفق fail-closed می‌شود.
- هر batch با count، min/max time و hash منبع و archive تطبیق داده می‌شود. import دوبارهٔ
  همان artifact بدون fact/revision/outbox جدید no-op است.
- seed بات فقط current Market Facts دائمی، از جمله تاریخچه external، را دارد. query آن هیچ
  join با raw text یا participant table ندارد.

## نتایج گیت تاریخچه

- ۶ source و ۱۰۰۶ رکورد ورودی؛
- ۹۹۵ logical fact یکتا و صفر duplicate logical fact؛
- ۱۰۰۰ revision و ۱۰۰۰ outbox item؛
- ۶ رکورد ناسازگار عمداً quarantine شد و پنج رکورد بعدی XAU revision دوم بودند؛
- هر ۶ batch با count/range/hash تطبیق داشت؛
- import دوم هر ۶ bundle کاملاً no-op بود؛
- ۲ raw ciphertext و ۱ participant ciphertext فقط در archive وب باقی ماند؛
- bot seed شامل ۹۹۵ fact و صفر raw Telegram/participant identity بود؛
- backup قبل و بعد import هر دو restore شد: شمار fact در restore قبل `0` و بعد `995`.

## گیت بازگشتی Docker

- ۱۹ آزمون متمرکز Stageهای 8 تا 11 و ۵ آزمون migration قبلی سبز بود؛
- rehearsal کامل Compose روی SHA نهایی پاس شد: image تکرارپذیر
  `sha256:0b31c521...`، Python `3.11.16` و اندازه `147.796 MiB`؛
- secret scan filesystem/history، migration second-pass no-op، ۸ service وب و ۴ service
  بات، دو receiver خصوصی و صفر port غیرمنتظره پاس شد؛
- recreate، second-owner fail-closed و rollback با schema version 2 و state پایدار پاس شد؛
- همهٔ container، network، image و temporary rootهای rehearsal پاک شدند.

## مرز عملیاتی

تاریخچه واقعی هنوز import نشده است. انتخاب artifactهای واقعی، backup عملیاتی، اجرای import،
انتقال seed و هر deploy/cutover جزو مراحل مجوزدار بعدی است. `LEGACY` همچنان primary است.
