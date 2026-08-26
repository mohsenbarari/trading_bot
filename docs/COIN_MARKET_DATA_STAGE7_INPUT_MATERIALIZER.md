# Gate Receipt — Stage 7 External Capture and Input Materializer

تاریخ: 2026-08-26

branch: `main`

release-under-test: `9db072c5157c1684314dea71f9b3b804d6778d75`

وضعیت: `PASS — SHADOW ONLY؛ بدون deploy، feed switch یا model authority`

## محدوده

- capture مستقل API برای Wallex USDT و proxy تأییدشده PAXG؛
- SQLite FULL outbox پیش از append/fsync و spool مستقل از دو حساب Telegram؛
- قرارداد کمینه `external_quote_event/1.0` بدون response خام، URL، header یا credential؛
- مصرف external spool با cursor مستقل و commit observation پیش از cursor؛
- نگهداری `BID/ASK/MID` هر poll موفق Wallex و انتخاب فقط `MID` برای ورودی مدل؛
- نگهداری PAXG به‌عنوان proxy صریح و نه XAU مستقیم؛
- محاسبه Decimal برای point/mean نودثانیه‌ای XAU/USDT؛
- roleهای 180 ثانیه USDT و 600 ثانیه regime فقط هنگام invocation؛
- input ledger immutable و reuse همان snapshot در چرخه‌های پنج‌ثانیه‌ای بدون تغییر؛
- افزودن source registry/migration برای PAXG بدون تغییر product DB.

## قواعد انتخاب

- هر quote مستقیم XAU در پنجره 90 ثانیه بر PAXG مقدم است؛
- PAXG فقط وقتی eligible است که دو book `PAXGUSDC/PAXGUSDT` هرکدام spread حداکثر
  0.5% و اختلاف میان دو midpoint حداکثر 0.5% داشته باشند؛
- اگر XAU مستقیم در 15 دقیقه اخیر وجود داشته باشد، فاصله proxy بیش از 2% fail-closed است؛
- quiet interval observation مصنوعی نمی‌سازد؛ اگر sample set و value عوض نشده باشد،
  inference بعدی به hash همان input snapshot ارجاع می‌دهد؛
- snapshot identity از event keyهای opaque، value، method و sample digest ساخته می‌شود،
  نه از متن خام یا زمان تکراری اجرای inference؛
- `point` و `mean` componentهای جدا و قابل ممیزی‌اند.

## Gateهای اجراشده

### تست و برابری مدل فعلی

- 112 تست pipeline/contract/capture/parser/materializer: `PASS`؛
- 81 تست estimator و external collector فعلی: `PASS`؛
- timestamp مشترک برای USDT و XAU، point و mean مسیر جدید با selection فعلی مدل برابر
  بود؛ محاسبه جدید با `Decimal` انجام شد؛
- optional roleها در base snapshot وجود نداشتند و فقط با invocation به ledger اضافه شدند؛
- restart outbox، duplicate، malformed dimension، quiet cycle و PAXG band تست شدند.

### Docker parser/materializer

release: `3267d6b31e3f5f26ae49aca24348b38e3d3c6cfb`

- `--network none`، 18 fact واجدشرایط، SQLite integrity برابر `ok`؛
- USDT point=`185200` و mean=`185150` از دو sample؛
- XAU point=`4631.2` و mean=`4630.65` از دو quote مستقیم همان پنجره؛
- فقط یک input snapshot در سه cycle و replay record برابر صفر؛
- چهار lifecycle خصوصی و دو گروه سکه همچنان پاس؛
- cleanup container/image/root کامل.

### Docker foundation و PostgreSQL

release: `3267d6b31e3f5f26ae49aca24348b38e3d3c6cfb`

- دو build بدون cache reproducible؛ runtime Python 3.11.16 و image حدود 147.422 MiB؛
- 8 service وب شامل database/migration و 6 runtime، 4 service بات؛
- migration 22 جدولی و اجرای دوم no-op؛ product DB untouched؛
- fixture transport، ACK/replay، owner locks، recreate state و rollback پاس؛
- secret scan و cleanup container/network/image/root کامل.

### Poll واقعی از داخل image

release: `9db072c5157c1684314dea71f9b3b804d6778d75`

- Wallex: یک poll موفق و سه رکورد `BID/ASK/MID`؛
- Binance PAXG: یک poll موفق و یک `MID` corroborated؛
- outbox نهایی صفر؛ response خام، URL، API key و Authorization در spool نبود؛
- فقط شمارنده و ابعاد contract گزارش شد و قیمت/payload خام چاپ نشد؛
- image و root موقت کامل پاک شدند.

## مرز promotion

Stage 7 از نظر کد، parity، Docker و live HTTP کامل است، اما اجرا هنوز shadow است. سرویس
external capture روی هیچ محیطی deploy نشده و input ledger اصلی مدل یا WebApp را تغییر
نمی‌دهد. PostgreSQL archive/outbox واقعی در Stage 8 و adapter مدل در Stage 9 فعال می‌شوند.
