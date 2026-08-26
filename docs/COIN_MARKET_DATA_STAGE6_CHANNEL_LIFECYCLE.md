# Gate Receipt — Stage 6 Channel Parsers and Private-Gold Lifecycle

تاریخ: 2026-08-26

branch: `main`

release-under-test: `bbe93ed5af03f0d87738aa3d2d0b2a04e589e6f3`

وضعیت: `PASS — SHADOW ONLY؛ بدون deploy، انتقال authority یا cutover`

## محدوده

- اتصال role مستقل `market-processor` به spoolهای Account 1 و Account 2؛
- Parse هرات، XAU، دو منبع عمومی آبشده و کانال خصوصی آبشده؛
- حفظ هر quote واقعی XAU بدون compaction دقیقه‌ای؛
- lifecycle مستقل 120 ثانیه‌ای کانال خصوصی با outcomeهای
  `PENDING/FULL/PARTIAL/NONE/AMBIGUOUS`؛
- تثبیت قیمت و تعداد اولیه آفر و ثبت نتیجه معامله در projection جدا؛
- نگهداری سه‌روزه facts دو کانال عمومی آبشده و عدم ورود آنها به آرشیو دائمی؛
- حفظ ingestion آفر هنگام تحلیل lifecycle و fail-closed شدن ناسازگاری.

## نتیجه ممیزی فقط‌خواندنی ورودی جدید

ممیزی روی سرور وب/داده و در محل spool انجام شد؛ داده خام به سرور بات منتقل نشد و فقط
شمارنده‌های aggregate و فاقد متن/هویت خارج شدند. هر دو capture در زمان ممیزی فعال، متصل
و بدون outbox، quarantine یا gap حل‌نشده بودند.

| منبع/سنجه | نتیجه |
|---|---:|
| Account 1 event پذیرفته‌شده | 74,990 |
| Account 2 event پذیرفته‌شده | 910 |
| خطای contract | 0 |
| Group 1 create/edit/delete | 200 / 4 / 5 |
| Group 2 create/edit/delete | 683 / 10 / 8 |
| آفر خصوصی آبشده Parse‌شده | 10,063 از 10,063 |
| lifecycle جزئی | 47 |
| lifecycle صریح بدون معامله | 410 |
| پایان پنجره بدون evidence معامله | 9,536 |
| lifecycle pending | 70 |
| baseline مفقود | 1، فقط مرز نیمه‌شب |
| XAU Parse‌شده | 39,677 از 39,677 |
| هرات Parse‌شده | 1,357 از 1,367 پیام متنی |
| آبشده عمومی flow | 3,904 از 3,906 |
| آبشده عمومی aggregate | 3,682 از 3,787 |
| failure parser | 0 |

اختلاف تعداد هرات و دو کانال عمومی ناشی از patternهای عمداً نادیده‌گرفته‌شده بود، نه
exception یا توقف stream. تکرار lifecycle نتیجه یکسان داد. انتقال raw با pipe توسط کنترل
امنیتی محیط رد شد و دور زده نشد.

در ممیزی freshness، capture جدید Group 1، Group 2 و کانال خصوصی آبشده زنده بود، اما
ورودی متناظر مسیر قدیمی روی سرور مدل حوالی `2026-08-25 09:30 UTC` متوقف شده بود؛ خود
predictionها ادامه داشتند. بنابراین تولید prediction جدید به‌تنهایی اثبات freshness ورودی
نیست و تا Stage 8/9 این مسیر حق primary شدن ندارد.

## Gateهای تست

- 48 تست متمرکز processor، adapter، parserهای عمومی/خصوصی، trade revisions و rehearsal:
  `PASS`؛
- مجموعه قبلی 60 تست منتخب pipeline و 29 تست parser/database estimator: `PASS`؛
- Docker rehearsal از commit تمیز با `--network none`: `PASS`؛
- fixture هم‌زمان دو account شامل دو quote اونس در یک دقیقه، هرات، دو کانال عمومی،
  آفر/معامله سکه و چهار lifecycle خصوصی بود؛
- 15 fact واجدشرایط، صفر pending/rejected، دو quote مستقل XAU و SQLite integrity برابر
  `ok`؛
- outcomeهای `FULL`, `PARTIAL`, `NONE`, `AMBIGUOUS` هرکدام دقیقاً یک مورد؛
- چهار آفر immutable و فقط دو trade دارای evidence؛ حذف source آفرِ منقضی، fact اقتصادی
  تاریخی را retract نکرد؛
- نبود `final_price` و `final_quantity` در outcome schema کنترل شد؛
- partial-tail resume موفق، replay نهایی صفر و cleanup container/image/root کامل بود؛
- نبود prediction یا feedback sidecar در live به exit 78 و fail-closed منجر شد.

## مرز promotion

Stage 6 در implementation، تست ایزوله و audit ورودی لایو کامل است. هیچ سرویس جدیدی روی
دو سرور deploy نشده، session owner تغییر نکرده، PostgreSQL یا Facts outbox زنده نوشته
نشده و مدل اصلی هنوز از مسیر جدید تغذیه نمی‌شود. materializer و input ledger در Stage 7،
انتقال Facts در Stage 8 و adapter مدل در Stage 9 تکمیل می‌شوند.
