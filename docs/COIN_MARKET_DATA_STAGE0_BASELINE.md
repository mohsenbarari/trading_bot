# Baseline مرحله 0 خط لوله داده بازار

تاریخ اجرا: 2026-08-25، بازه ممیزی نهایی 17:16 تا 17:20 UTC

وضعیت gate: **PASS برای پایان inventory؛ بدون مجوز cutover یا تغییر runtime**

ابزار بازتولیدپذیر: `scripts/audit_coin_market_pipeline_stage0.py`

## 1. محدوده و تضمین ایمنی

این ممیزی فقط‌خواندنی روی میزبان وب/داده و میزبان بات اجرا شد. در طول آن هیچ service، timer، container، network، database، spool یا داده زنده تغییر نکرد. خروجی ابزار فقط شامل شمارش، حجم، بازه زمانی، وضعیت process و معیارهای allowlist‌شده سلامت است و موارد زیر را عمداً خارج می‌کند:

- متن پیام، متن آفر، reply و entity؛
- Telegram ID، message ID، event ID و هویت کاربر؛
- session، credential، env value و secret؛
- payload جدول‌های SQLite یا JSONL؛
- مسیر model artifact و جزئیات آزاد خطا که ممکن است داده حساس داشته باشد.

پنج تست خودکار ثابت می‌کنند payload و identity حتی در fixture آلوده وارد خروجی نمی‌شود. JSON نقطه‌ای کامل در Git نگهداری نمی‌شود، چون بلافاصله stale خواهد شد؛ ابزار versioned منبع بازتولید آن است.

## 2. inventory اجرا

### میزبان وب/داده

- `coin-capture.service`: فعال، running و enabled؛
- `market-channel-capture.service`: فعال، running و enabled؛
- capture و shadow فعلی هنوز host-native هستند؛
- دایرکتوری release موجود Git metadata ندارد، بنابراین SHA دقیق deployment فعلی از خود میزبان اثبات‌پذیر نیست؛ این خلأ در Docker foundation با image label و digest اجباری رفع می‌شود؛
- فضای root هنگام ممیزی 38.6٪ مصرف و حدود 92.2 GB آزاد داشت؛
- حجم rootهای مرتبط: capture گروه‌ها حدود 88.8 MB، capture کانال‌ها حدود 385.4 MB و shadow حدود 2.09 GB.

### میزبان بات

- branch ممیزی: `main`؛ مبنای پیش از commit مرحله 0: `7ffc9e51a41920c3307ec8cf4bfee350e0609046`؛
- سرویس اصلی public market و dashboard فعال و running بودند؛ collector گروه‌ها timer-driven است؛
- containerهای اصلی production و staging فعال‌اند، ولی Market Intelligence فعلی خارج Docker اجرا می‌شود؛
- فضای root هنگام ممیزی 74.4٪ مصرف و حدود 9.8 GB آزاد داشت؛ volume داده 40.2٪ مصرف و حدود 58.4 GB آزاد داشت؛
- حجم `coin-intelligence` حدود 5.88 GB بود.

نتیجه ظرفیت: داده باید روی volume پایدار بماند و image/layer/build cache نباید روی root محدود میزبان بات بی‌ضابطه رشد کند.

## 3. صحت و تازگی capture جدید

### دو گروه سکه

| معیار | مقدار |
| --- | ---: |
| منابع resolve‌شده | 2 |
| رویدادهای durable در دو فایل روزانه | 12,467 |
| created / edited / deleted | 12,335 / 79 / 53 |
| رکورد JSONL خراب | 0 |
| آخرین رویداد هنگام ممیزی | 2026-08-25 17:14:42 UTC |
| outbox مانده در state DB | 0 |

حجم روزانه موجود:

- 2026-08-24: تعداد 9,702 رویداد و 7,065,809 بایت؛
- 2026-08-25 تا لحظه ممیزی: تعداد 2,765 رویداد و 2,010,763 بایت.

shadow staging تعداد 11,733 پیام گروهی داشت و آخرین زمان آن دقیقاً 17:14:42 UTC بود. پس capture و ingestion جدید گروه‌ها در لحظه ممیزی هم‌زمان و جاری بودند.

محدودیت مشاهده‌پذیری: spool durable نرخ duplicate و gap را encode نمی‌کند و capture گروه‌ها heartbeat پایدار مشابه capture کانال‌ها ندارد. صفر بودن رکورد خراب و outbox اثبات سلامت durability است، اما اثبات مستقل duplicate/gap نیست. heartbeat per-group باید پیش از cutover به قرارداد Docker اضافه شود.

### پنج کانال بازار

| معیار | مقدار |
| --- | ---: |
| منابع resolve‌شده | 5 |
| رویدادهای durable در دو فایل روزانه | 202,301 |
| created / edited / deleted / snapshot | 142,600 / 9,524 / 36,421 / 13,756 |
| backfill/recovered envelope | 13,801 |
| رکورد JSONL خراب | 0 |
| آخرین رویداد هنگام ممیزی | 2026-08-25 17:16:24 UTC |
| outbox مانده | 0 |

حجم روزانه موجود:

- 2026-08-24: تعداد 37,219 رویداد و 45,958,958 بایت؛
- 2026-08-25 تا لحظه ممیزی: تعداد 165,082 رویداد و 188,068,459 بایت.

heartbeat کانال‌ها `connected=true` و `reconcile_complete=true` بود. آخرین lag لایو پنج منبع بین 0.371 و 3.052 ثانیه قرار داشت. شمارنده cumulative duplicate فقط برای یک منبع 1 و برای چهار منبع دیگر صفر بود؛ این duplicate وارد outbox نمانده است.

shadow staging در همان بازه تا 17:16:18 UTC و canonical shadow تا 17:16:20 UTC پیش رفته بود. همه source codeهای مورد انتظار شامل گروه‌ها، آبشده خصوصی، آبشده عمومی، هرات، اونس و تتر در canonical shadow رکورد تازه همان روز داشتند.

## 4. اختلاف مسیر جدید و مسیر فعلی مدل

در زمان ممیزی، مسیر جدید وب جاری بود ولی هنوز به‌عنوان authority ورودی مدل روی بات cutover نشده بود:

| منبع | آخرین داده مسیر فعلی بات | آخرین داده مسیر جدید وب | نتیجه |
| --- | --- | --- | --- |
| گروه 1 | 09:28:53 UTC | 17:14:42 UTC | مسیر قدیمی حدود 7 ساعت و 46 دقیقه عقب |
| گروه 2 | 09:31:01 UTC | 17:13:53 UTC | مسیر قدیمی حدود 7 ساعت و 43 دقیقه عقب |
| آبشده خصوصی | 09:29:47 UTC | 17:17:08 UTC | مسیر قدیمی حدود 7 ساعت و 47 دقیقه عقب |
| آبشده عمومی/flow | 17:15 UTC | 17:17 UTC | هر دو جاری |
| هرات | 17:15:00 UTC | 17:15:00 UTC | هم‌زمان |
| اونس | 17:15–17:19 UTC | 17:17 UTC | هر دو جاری |
| تتر | 17:19 UTC | 17:17 UTC | هر دو جاری |

این اختلاف نقص capture جدید نیست؛ نتیجه نبود transport/cutover بین دو میزبان است و یکی از دلایل اصلی اجرای roadmap محسوب می‌شود.

state مدل در 17:19:56 UTC از نظر aggregate `HEALTHY` بود، اما وضعیت `coin_groups` را `HISTORICAL_ONLY` با age برابر 28,104 ثانیه و `live_commodity_count=0` گزارش می‌کرد. چون coin groups فعلاً `OPPORTUNISTIC` طبقه‌بندی شده، سلامت کلی با وجود این تأخیر سبز مانده است. همچنین `canonical_store_available=false` و روش regime برابر legacy fallback بود. در contract جدید، freshness هر ورودی حیاتی باید مستقل و در WebApp قابل مشاهده باشد و سلامت aggregate نباید stale بودن ورودی مهم را پنهان کند.

هر سه مدل shadow فعال، `OK` و بدون authoritative override بودند؛ مدل اصلی `RUNNING` بود.

## 5. source-to-storage map فعلی و هدف

| خانواده | authority دریافت فعلی | پردازش shadow جدید | authority فعلی مدل بات | authority هدف |
| --- | --- | --- | --- | --- |
| دو گروه سکه | spool مشترک روزانه + state DB روی وب | capture staging + canonical shadow روی وب | conversation DB، coin staging و canonical محلی بات | archive/facts/outbox وب → receiver/store محلی مدل |
| پنج کانال | spool مشترک روزانه + state DB روی وب | capture staging + canonical shadow روی وب | public/private staging و canonical محلی بات | archive/facts/outbox وب → receiver/store محلی مدل |
| تتر | collector/API و external observations | canonical shadow وب | external observations و canonical بات | fact cadence واقعی روی وب → receiver مدل |
| خروجی مدل | state/snapshot محلی بات | shadow فقط مقایسه‌ای | snapshot مدل بات | snapshot versioned بات → وب روی شبکه خصوصی |

SQLite هیچ‌گاه بین دو میزبان share نمی‌شود. انتقال فقط از facts/outbox نسخه‌بندی‌شده انجام خواهد شد.

## 6. تاریخچه و gapهای شناخته‌شده

- raw spool جدید فقط retention کوتاه دارد؛ در snapshot ممیزی دو فایل روزانه 24 و 25 اوت موجود بود؛
- تاریخچه قدیمی‌تر در storeهای پردازش‌شده فعلی موجود است: conversation از 2026-06-09، public market از 2026-06-21 و canonical archive از 2026-06-09؛
- shadow جدید بازه جاری خود را پوشش می‌دهد و جایگزین خودکار تاریخچه قدیمی نیست؛ import تاریخی باید idempotent و جدا از live tail اجرا شود؛
- حذف‌هایی که تلگرام در زمان آفلاین capture قابل بازیابی نمی‌کند، ذاتاً gap غیرقابل بازیابی‌اند؛
- duplicate/gap per-group تا اضافه‌شدن heartbeat پایدار قابل اثبات عددی نیست؛
- provenance دقیق releaseهای host-native وب به علت نبود Git metadata قطعی نیست.

## 7. Gate receipt و قدم بعد

شرایط پایان مرحله 0:

- هر دو گروه و هر پنج کانال allowlist‌شده resolve شدند: **PASS**؛
- مسیرهای authority فعلی و هدف مشخص شدند: **PASS**؛
- گزارش فاقد raw/identity/secret است: **PASS، همراه پنج تست**؛
- عدم write/delete عملیاتی: **PASS**؛
- cutover، deployment و network mutation: **انجام نشد**.

یافته‌هایی که وارد مراحل بعد می‌شوند:

1. heartbeat و معیار durable duplicate/gap برای دو گروه در مرحله 4 اجباری است؛
2. freshness گروه‌ها باید در health gate مدل critical یا دست‌کم مستقل و fail-visible شود؛
3. exact Git SHA/digest استقرار در مرحله 3 اجباری می‌شود؛
4. import تاریخچه، live tail و facts outbox باید checkpoint مستقل داشته باشند؛
5. به دلیل محدودیت root میزبان بات، build cache و image retention باید bounded باشد.

مرحله بعدی roadmap، آماده‌سازی شبکه خصوصی است. inventory خواندنی آن می‌تواند آغاز شود، اما هر route/firewall/bind/certificate mutation نیازمند مجوز اجرایی جداست.
