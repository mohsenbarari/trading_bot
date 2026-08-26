# Gate Receipt — Stage 5 Coin-Group Parser

تاریخ: 2026-08-26

branch: `main`

release-under-test: `3cd136b2e94f1795cba388be3d98a2cb46e94cbc`

وضعیت: `PASS — SHADOW ONLY؛ بدون deploy یا cutover`

## محدوده

- انتقال parser آفر و linker معامله دو گروه به role مستقل `market-processor`؛
- مصرف JSONL پایدار Account 2 بدون بازکردن Telegram session؛
- staging خام سه‌روزه و Market Store جدا روی volume محلی؛
- inference کالای بی‌نام فقط با لنگرهای علّی `MAIN_ONLINE` و correctionهای در دسترس؛
- ثبت parser version و evidence جدا برای هر field؛
- corpus اصلاحات WebApp با revisionهای append-only و بدون متن/هویت؛
- حفظ کامل shadow boundary: بدون PostgreSQL، Facts outbox، مدل اصلی یا product DB.

## ممیزی ساختار فعلی production

ممیزی فقط‌خواندنی چهار ورودی production-shaped را در یک snapshot سازگار بازپخش کرد:
staging خام، Market Store، correction sidecar و prediction ledger. هیچ متن خام، شناسه
پیام، event key یا هویت فرستنده چاپ یا کپی نشد.

| سنجه | نتیجه |
|---|---:|
| پیام خام دو گروه | 5,926 |
| reply | 2,167 |
| edit | 3 |
| خطای runtime parser | 0 |
| offer candidate عبور اول | 2,624 |
| candidate با کالای صریح | 663 |
| candidate بی‌نام پیش از resolver | 1,961 |
| correction موجود | 45 |
| prediction row بررسی‌شده | 47,090 |
| لنگر علّی پذیرفته‌شده | 21,914 |
| fact مقایسه‌شده | 2,845 |
| اختلاف اقتصادی | 0 |
| اختلاف provenance | 0 |
| offer واجد شرایط | 2,363 |
| trade واجد شرایط | 176 |
| offer در review/rejected | 268 |
| trade در review/rejected | 38 |

`source_file_signatures_stable=false` فقط نشان می‌داد writer production در طول ممیزی
فعال بوده است. connectionها `mode=ro` و `query_only` بودند، `total_changes=0` ماند و
Market Store در یک read transaction ثابت خوانده شد؛ ممیزی هیچ write نداشت.

برابری فقط وقتی برقرار شد که prediction ledger و correction sidecar نیز حاضر بودند.
حذف لنگرها در آزمایش اولیه صدها اختلاف instrument/quality ایجاد کرد؛ بنابراین این دو
فایل جزو قرارداد علّی parser هستند، نه dependency اختیاری.

## قاعده settlement تأییدشده

داده جاری production و تست‌های قطعی قاعده زیر را تثبیت کردند:

- `خ ن ف`، `ف ن ف`، `خ ف` و `ف ف` یا نبود marker تسویه: `TOMORROW`؛
- `خ ن`، `ف ن`، `نق`، `نقد` و `نقدی`: `CASH`؛
- marker صریح فردا بر marker نقد مقدم است.

در داده ممیزی‌شده 2,210 candidate فردایی و 414 candidate نقدی وجود داشت. تغییر جمعی
آفرهای marker-less به نقدی با ساختار فعلی production ناسازگار است و بدون corpus برچسب
قطعی مجاز نیست.

## Gateهای تست

- 92 تست parser، resolver، reply graph، feedback، corpus، capture adapter و processor:
  `PASS`؛
- 67 تست کامل estimator/WebApp، از جمله ثبت correction در corpus: `PASS`؛
- production-shaped audit: 2,845 از 2,845 fact برابر، تمام diffها reason-code capable؛
- Docker rehearsal با `--network none`: `PASS`؛
- fixture Docker: هر دو گروه، invalid sibling، partial tail، restart/replay، آفر بی‌نام
  با دو لنگر زمانی، reply branch با قیمت توافقی، healthcheck و SQLite integrity؛
- replay نهایی: صفر record تکراری؛ cleanup container، image و root موقت: کامل.

در rehearsal، فایل‌های calibration به‌صورت snapshot اتمیک و `immutable=1` خوانده شدند.
این قرارداد از نیاز SQLite به ساخت `-shm` روی mount فقط‌خواندنی جلوگیری می‌کند و خواندن
DB در حال تغییر را نیز ممنوع می‌سازد. heartbeat تعداد لنگرهای آخرین projection را در
چرخه idle و restart حفظ می‌کند تا idle با قطع ورودی اشتباه نشود.

## خروجی و مرز promotion

Stage 5 کامل است، اما فقط در سطح implementation و shadow gate. هیچ سرویس جدید deploy
نشده، authority تغییر نکرده و مدل اصلی هنوز از این Market Store تغذیه نمی‌شود. انتقال
Facts به سرور بات مربوط به Stage 8 و adapter مصرف‌کننده مربوط به Stage 9 است؛ تا عبور
آن مراحل، این processor حق تبدیل‌شدن به منبع اصلی مدل را ندارد.
