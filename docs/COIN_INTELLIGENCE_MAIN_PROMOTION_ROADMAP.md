# نقشهٔ راه انتقال «تشخیص کالا از قیمت» به `main`

## وضعیت، تصمیم و مرز کار

**وضعیت:** `APPROVED ROADMAP — NOT IMPLEMENTED`

**برنچ پژوهش و منبع فعلی:** `candidate/coin-price-intelligence`
**هدف این سند:** انتقال کنترل‌شدهٔ قابلیت تشخیص کالای آفر از قیمت به
`main`، بدون merge کردن کل برنچ پژوهشی و بدون فعال‌سازی پیش‌بینی
چندافقِ آبشده.

هدف محصول در این مرحله مشخص است:

1. کاربر بتواند در بات یا WebApp آفر را بدون نام کالا ثبت کند.
2. سیستم، قیمت را با بازه‌های معتبر و لحظه‌ای کالاهای canonical مقایسه کند.
3. فقط اگر نتیجه یکتا، تازه و با اطمینان کافی بود، کالای متناظر را انتخاب
   کند؛ در غیر این صورت از کاربر تأیید کوتاه بگیرد.
4. پس از ثبت، آفرها و معاملات واقعی خود پروژه، همراه با منابع بیرونی،
   ورودیِ ساخت بازهٔ بعدی شوند.
5. تا زمانی که نقدشوندگی داخلی کافی نیست، گروه‌های معاملاتی سکه و منابع
   آبشده/دلار/تتر/اونس/بورس به موازات دادهٔ پروژه دریافت و با provenance
   مستقل استفاده شوند.

این سند **اجازهٔ تغییر مستقیم رفتار محصول، زمان‌بندی worker، Collector،
تنظیمات Production، یا معماری سه‌سروره را نمی‌دهد**. هر مرحله فقط پس از
عبور از گیت همان مرحله و ثبت یادداشت اجراییِ آن قابل شروع است.

## تصمیم‌های معماریِ ثابت

### 1. `main` کل برنچ را merge نمی‌کند

این برنچ شامل Shadow research، Gemma، CatBoost/PySR، رابط اپراتوری مستقل،
تغییرات سه‌سروره و تغییرات نامرتبط صف تلگرام نیز هست. انتقال باید از یک
برنچ promotion تازه که از `main` روز ساخته شده انجام شود؛ تغییرهای لازم از
این برنچ به شکل patchهای کوچک و بازبینی‌شده منتقل می‌شوند.

نام پیشنهادی برنچ اجرای واقعی:

```text
candidate/coin-commodity-inference-promotion
```

### 2. دادهٔ داخلی و خارجی یک قرارداد دارند، نه یک وزن

همهٔ ورودی‌ها به قرارداد واحد `NormalizedMarketObservation` وارد می‌شوند،
اما منشأ، کیفیت، نوع رویداد و وزن آن‌ها حفظ می‌شود. `PROJECT`، `GROUP_1`،
`GROUP_2`، آبشدهٔ جدید، `MELTED_AGGREGATE`، `MELTED_FLOW`، `USD_HERAT`,
`USDT_IRT`، `XAUUSD` و IME نباید در یک میانگین بی‌نام ادغام شوند.

معاملهٔ تأییدشدهٔ پروژه بالاترین وزن را دارد. معاملهٔ تأییدشدهٔ گروه، آفر
پروژه، آفر گروه، و دادهٔ مرجع خارجی هر کدام policy وزن و افت زمانی مستقل
دارند. منبع خارجی هیچ‌وقت به نام منبع دیگری بازبرچسب‌گذاری نمی‌شود؛ به‌ویژه
تتر جایگزین عددی هرات نیست و فقط می‌تواند روندِ لنگر واقعی هرات را با
برچسب `BRIDGED` جابه‌جا کند.

### 3. آفرها و معاملات پروژه Parser متنی نمی‌خواهند

`Offer` و `Trade` اجزای اقتصادی لازم را به‌صورت قطعی دارند: کالا، قیمت،
تعداد، خرید/فروش، نقدی/فردایی، وضعیت و زمان. این داده‌ها باید بعد از commit
به adapter مستقیم منتقل شوند، نه اینکه دوباره از متن کانال استخراج شوند.
متن/یادداشت تنها برای قابلیت‌های آیندهٔ attribute extraction است و ورودی
اجباریِ این مرحله نیست.

### 4. لایه‌های نگهداری داده

| لایه | محل/هدف | ماندگاری |
| --- | --- | --- |
| تراکنش محصول | PostgreSQL پروژه | طبق سیاست محصول |
| Outbox رویداد پروژه | PostgreSQL، پس از commit | تا تحویل idempotent |
| بازار داغ نرمال‌شده | SQLite روی volume محافظت‌شده | سیاست چرخشی بازار؛ نه checkout و نه `/tmp` |
| پیام خام و staging خصوصی | volume محافظت‌شده، جدا از مدل | حداکثر سه روز، مگر مورد بازبینی‌شده |
| Snapshot نرخ و bundle | artifact نسخه‌بندی‌شده و immutable | نسخهٔ فعال + rollback محدود |
| audit تصمیم تشخیص کالا | PostgreSQL، بدون متن خام یا شناسهٔ تلگرام | طبق retention محصول |

SQLite برای feed پرحجم و خواندن Snapshot محلی حفظ می‌شود، زیرا موتور فعلی
بر همین قرارداد ساخته شده است. PostgreSQL منبع قطعی تراکنش‌های پروژه و
outbox تحویل‌پذیر باقی می‌ماند. هیچ worker نباید به دلیل قطع SQLite یا
Collector، commit آفر یا معاملهٔ محصول را rollback کند.

### 5. مرز فعلی سه‌سروره

در این roadmap، ارتباط و استقرار سه‌سروره پیاده‌سازی نمی‌شود. فقط قرارداد
artifact باید از ابتدا local-first باشد: bundle و Snapshot با نسخه و checksum
خوانده شوند و هیچ درخواست ثبت آفر برای inference به سرویس راه‌دور وابسته
نباشد. سیاست sync بین `wa-fl` و `wa-ir` در مرحله‌ای جداگانه و پس از تکمیل
معماری سه‌سروره اجرا می‌شود.

## موجودی انتقال از برنچ فعلی

### مؤلفه‌های قابل انتقال با بازبینی

| حوزه | فایل‌ها/مؤلفه‌های کاندید | دلیل |
| --- | --- | --- |
| قرارداد و Ranker | `contracts.py`, `ranker.py`, `bundle.py`, `snapshot.py`, `service.py` | مقایسهٔ قیمت با بازه، validation bundle و abstention |
| ساخت Snapshot | `producer.py`, `pipeline.py`, `anchor_transfer.py`, `low_date.py`, `regime.py` | لنگر، حباب، تاریخ پایین، هرات/تتر و رژیم بازار |
| Collector عمومی | `telegram_collector/*`, `collect_coin_market_telegram.py` | `abshdh`، `NaghdP`، هرات و اونس |
| ingest خصوصی | `scripts/coin_intelligence_private_ingest/*` و parserهای گروه/طلا | گروه‌های سکه و کانال جدید آبشده؛ نیازمند production refactor |
| تست‌ها | تست‌های unit مربوط به موارد بالا | جلوگیری از تغییر قراردادهای عددی |

### مؤلفه‌های خارج از promotion اولیه

- `online_residual_v1.py`، `residual_research.py`، CatBoost و PySR؛
- `coin_relationship_challenger.py` و `melted_relationship_challenger.py`؛
- Gemma و `Dockerfile.coin-intelligence-gemma`؛
- داشبورد مستقل `apps/coin_rate_estimator` و تحلیل‌های اپراتوری آن؛
- همهٔ migration/tableهای صرفاً Shadow، مگر در صورتی که یک migration کوچک
  و مستقل برای audit محصول در مرحلهٔ مربوطه لازم شود؛
- قابلیت پیش‌بینی آیندهٔ آبشده و
  `MELTED_MULTI_HORIZON_FORECASTING_ROADMAP.md`؛
- تغییرات سه‌سروره و صف تلگرام که برای این قابلیت لازم نیستند.

## ترتیب اجرایی مرحله‌ها

هر مرحله یک یا چند commit کوچکِ خودبسنده دارد. هیچ مرحله‌ای با تغییر
uncommitted وارد مرحلهٔ بعد نمی‌شود.

---

## مرحلهٔ P0 — پایهٔ Promotion و انجماد scope

### هدف

ایجاد نقطهٔ شروع تمیز از `main` و جلوگیری از ورود ناخواستهٔ کدهای پژوهشی یا
سه‌سروره.

### کارها

1. `main` و migration head آن را به‌روزرسانی و worktree را پاک بررسی کن.
2. برنچ promotion را از همان commit `main` بساز.
3. یک manifest انتقال ایجاد کن که برای هر فایل وضعیت `INCLUDE`، `REWRITE`،
   `DEFER` یا `EXCLUDE` داشته باشد.
4. dependency graph کوچکِ inference، Snapshot، Collector و adapter پروژه را
   ثبت کن.
5. baseline تست‌های parser، creation service، API offers/trades و migration
   را پیش از هر تغییر اجرا کن.

### گیت خروج

- merge مستقیم `candidate/coin-price-intelligence` ممنوع اعلام شده باشد.
- هیچ فایل سه‌سروره، Gemma، dashboard مستقل یا research challenger در diff
  promotion نباشد.
- baseline `main` سبز و hash آن در یادداشت مرحله ثبت شده باشد.

### rollback

حذف برنچ promotion؛ هنوز هیچ migration یا runtime effect وجود ندارد.

---

## مرحلهٔ P1 — قرارداد canonical دادهٔ بازار

### هدف

تعریف یک قرارداد versioned که همهٔ منابع بتوانند بدون از دست‌دادن معنای
اقتصادی به آن وارد شوند.

### کارها

1. schema مشترک observation را تعیین کن. حداقل فیلدها:
   - `event_id`/idempotency key غیرقابل‌افشا؛
   - `source_code` و `source_family`؛
   - `event_time_utc` و `available_at_utc`؛
   - Tehran datetime/minute/day/weekday؛
   - instrument، unit، currency، قیمت و quantity؛
   - `settlement_term`، `trade_form`، `event_type` و `side`؛
   - `parser_version`، `parse_confidence`، quality state و policy version؛
   - conditional/normal/reverse/swim و دیگر attributeهای لازم برای آبشده.
2. جدول/قرارداد `external_market_observations` را رسماً جزو Market Store کن.
   Snapshot فعلی برای `USDT_IRT` و IME به آن نیاز دارد، اما Collector عمومی
   فعلی آن را ایجاد نمی‌کند.
3. مرز privacy تعریف کن: شناسهٔ تلگرام، متن خام، نام مشارکت‌کننده و لینک
   کانال در feature/model rows قرار نگیرند؛ فقط در raw staging کوتاه‌مدت و
   محافظت‌شده موجود باشند.
4. catalog unit و conversion را تثبیت کن: آبشده `IRT_PER_MESGHAL_750`،
   قیمت پروژه `PROJECT_THOUSAND_TOMAN`، تتر/هرات و استاندارد IME.
5. migration/upgrade path بین schema سادهٔ `telegram_collector` و schema
   قدیمی‌تر `apps/coin_rate_estimator` را طراحی و با fixture تست کن؛ دو
   schema موازی در Production مجاز نیست.

### گیت خروج

- تمام منابع زیر بدون overload معنایی در schema جا بگیرند: گروه‌های ۱/۲،
  کانال آبشدهٔ جدید، `abshdh`، `NaghdP`، هرات، تتر، اونس، IME و رویداد پروژه.
- price selection با واحد اشتباه یا واگذاری `PAPER` به `PHYSICAL` fail closed
  شود.
- testهای timestamp، timezone تهران، unit، deduplication و schema upgrade
  سبز باشند.

### rollback

schema جدید ابتدا فقط در Store جدا و بدون consumer محصول ساخته می‌شود؛
حذف آن دادهٔ تراکنش محصول را تحت تأثیر قرار نمی‌دهد.

---

## مرحلهٔ P2 — adapterهای دریافت منبع خارجی

### هدف

ورود تمام داده‌های لازم برای لنگر به Market Store واحد، با checkpoint و
provenance مستقل.

### P2-A — کانال‌های عمومی تلگرام

انتقال و سازگارکردن Collectorهای زیر:

- `MELTED_AGGREGATE` از `@abshdh`؛
- `MELTED_FLOW` از `@NaghdP`؛
- `USD_HERAT` از `@ToofanHarirodOfficial`؛
- `XAUUSD` از `@qheimat_ounce`.

قواعد ثابت: قیمت گرم و خلاصه‌های ساعت‌به‌ساعت آبشده/سکه ignored، اونس در
حد یک observation در دقیقه compact، و معاملهٔ بدون سمت NaghdP فقط با offer
سازگارِ پیشین و در بازهٔ محدود سمت می‌گیرد.

### P2-B — کانال جدید آبشده

pipeline خصوصی `account1` به worker محصولی تبدیل می‌شود:

- physical غیرشرطی را به‌صورت رویدادهای منفرد حفظ می‌کند؛
- physical شرطی را نگه می‌دارد، اما تا عبور از quality gate مرجع مستقیم
  قیمت نمی‌کند؛
- paper عادی/معکوس/شنا و امروز/فردا را جدا نگه می‌دارد؛
- در minute aggregation کاغذی، معاملهٔ تأییدشده وزن بالاتر از آفر دارد؛
- edit-time فقط وقتی evidence تأیید معامله است، زمان معامله محسوب می‌شود؛
- پیام offer و verifier update با idempotency و ترتیب معکوس قابل ادغام‌اند.

### P2-C — گروه‌های معاملاتی سکه

جریان‌های JSON خصوصی (یک رویداد یا چند رویداد در یک پیام) باید:

1. در raw کوتاه‌مدت با cursor و checksum ذخیره شوند؛
2. پیام نامرتبط را با precision policy حذف کنند؛
3. آفر را از درخواست معامله و reply-chain جدا کنند؛
4. کالا، قیمت، تعداد، سمت و تسویه را با قیمت‌های strictly-prior و سازگار
   اعتبارسنجی کنند؛
5. معاملهٔ با قیمت توافقی متفاوت از قیمت اولیهٔ آفر را ثبت کنند؛
6. partial fillهای یک economic chain را دوباره full-weight نشمارند؛
7. پیام مبهم یا ۴۰۳/۴۰۴/پنجشنبهٔ کشیک/نامعتبر را model-ineligible کنند.

### P2-D — تتر و IME

adapter مستقل external market باید از کد legacy استخراج و به Market Store
واحد منتقل شود:

- `USDT_IRT` با `MID`/`LAST` و زمان observation واقعی؛
- `IME_GOLD_BAR` با تبدیل روشن به ۷۵۰ و مثقال در جایی که لازم است؛
- `IME_GOLD_COIN_IMAM` با واحد و نوع quote صریح؛
- source error یا توقف بورس/تتر نباید timestamp دریافت را به‌جای timestamp
  بازار ثبت کند.

### گیت خروج P2

- هر adapter restart-safe، idempotent و دارای health/freshness جداگانه باشد.
- secret، session، raw export و channel identifier در repository یا log
  وجود نداشته باشد.
- یک replay با نمونه‌های چندپیامی، edit، reply و duplicate، خروجی برابر با
  اجرای نخست داشته باشد.
- قطع یک منبع فقط همان منبع را `STALE`/`MISSING` کند؛ هیچ منبع دیگری جای آن
  نام‌گذاری نشود.

### rollback

هر adapter با feature flag و checkpoint مستقل خاموش می‌شود. Store قبلی
read-only باقی می‌ماند و Snapshot فعال جایگزین نمی‌شود.

---

## مرحلهٔ P3 — adapter مستقیم آفر و معاملهٔ پروژه

### هدف

استفاده از آفر/معاملهٔ واقعی پروژه بدون Parser متن و بدون تأثیر بر transaction
اصلی.

### کارها

1. listener پس از commit فعلی را از Shadow best-effort به outbox پایدار
   محصولی ارتقا بده. event transaction نباید model work را await کند.
2. eventهای زیر را ثبت کن:
   - `PROJECT_OFFER_OPENED`؛
   - `PROJECT_OFFER_EXPIRED` یا `PROJECT_OFFER_CANCELLED`؛
   - `PROJECT_TRADE_COMPLETED` و partial fill؛
   - در صورت وجود، تصحیح قانونی یا تغییر وضعیت مؤثر.
3. worker outbox، row قطعی را از PostgreSQL بخواند و observation نرمال‌شده
   بسازد. قیمت معامله از `Trade.price`، نه الزاماً `Offer.price`، گرفته شود.
4. `trade_form=PHYSICAL` تا زمان اضافه‌شدن مدل محصولی کاغذی، صریح و ثابت
   باشد؛ `cash` و `tomorrow` مستقل باقی می‌مانند.
5. آفر منقضی/لغو‌شده از book زنده کنار برود، اما evidence تاریخی آن طبق
   policy افت وزن و آموزش نگهداری شود.
6. foreign key و commit ordering در برابر retry/duplicate/partial trade
   تست شوند.

### گیت خروج

- Offer یا Trade شکست‌خورده/rollback شده هیچ observation بازار تولید نکند.
- تحویل outbox حداقل یک‌بار باشد ولی اثر Market Store دقیقاً یک‌بار بماند.
- ایجاد آفر و تکمیل معامله در نبود Collector یا SQLite همچنان موفق شود.
- payload هیچ نام کاربر، شماره، متن یا شناسهٔ عمومی آفر را به feature store
  منتقل نکند.

### rollback

outbox worker خاموش می‌شود؛ رویدادهای تحویل‌نشده برای replay باقی می‌مانند و
مسیر اصلی ثبت آفر/معامله تغییر نمی‌کند.

---

## مرحلهٔ P4 — Snapshot، لنگر و رژیم بازارِ قابل اتکا

### هدف

ساخت Snapshot محلیِ کامل و atomically published که Ranker بتواند به آن
اعتماد کند.

### کارها

1. موتورهای `producer`, `pipeline`, `anchor_transfer`, `low_date` و `regime`
   را با schema P1 سازگار کن.
2. مرجع‌های زیر را source-separated نگه دار: آبشده فیزیکال، آبشدهٔ کاغذی
   تفکیک‌شده، هرات، تتر، اونس، IME، generic coin و گروه‌های سکه.
3. قواعد لنگر را تثبیت کن:
   - برای تاریخ پایین ابتدا آبشدهٔ فیزیکال، سپس کاغذی compatible و bridge
     صریح؛
   - برای سکه‌های premium، آخرین لنگر همان کالا و همان settlement در کنار
     تغییر underlyingها؛
   - نقدی/فردایی با لنگر و basis جدا؛
   - هرات کهنه فقط با حرکت نسبی تتر از زمان همان لنگر bridge می‌شود؛
   - نبود coin offer چند دقیقه/ساعت نباید تخمین را متوقف کند، اما confidence
     و interval باید با سن لنگر و coverage تغییر کند.
4. Snapshot شامل generated time، source freshness، method، confidence،
   lower/center/upper، bundle version و reason code باشد.
5. Snapshot فقط در صورت کامل‌بودن قرارداد، سلامت schema و عبور از anomaly
   gate، جایگزین snapshot قبلی شود.

### گیت خروج

- price و range برای همهٔ کالاهای canonical و CASH/TOMORROW قابل تولید یا با
  دلیل مشخص abstain شوند.
- replay تاریخی بدون leakage آینده، نرخ و روش یکسان تولید کند.
- تست‌های بازگشایی، تعطیلی، نبود سکه، هرات کهنه، نبود تتر و تاریخ پایین
  پاس شوند.
- Snapshot خراب، کهنه یا ناقص هرگز جای نسخهٔ سالم قبلی را نگیرد.

### rollback

خواننده‌ها فقط آخرین Snapshot معتبر را می‌خوانند. نسخهٔ قبلی با checksum و
زمان تولید برای rollback سریع باقی می‌ماند.

---

## مرحلهٔ P5 — Promotion کنترل‌شدهٔ Ranker به تصمیم محصول

### هدف

تبدیل خروجی Shadow از «مشاهده» به پیشنهاد/انتخاب قابل استفاده در محصول، با
حفظ abstention و بدون تبدیل شناسهٔ catalog به PostgreSQL ID.

### کارها

1. یک سرویس محصولی مستقل بساز؛ نام آن نباید `Shadow` باشد. این سرویس فقط
   canonical commodity name، confidence، range، method، snapshot version و
   reason را برگرداند.
2. bundle محصولی جدا از bundle با `SHADOW_NOT_PROMOTED` ایجاد کن. Loader فعلی
   عمداً bundle Shadow را authoritative نمی‌داند؛ این invariant نباید با یک
   flag سست دور زده شود.
3. خروجی canonical name را فقط با `commodities.name` محلی و exact match به
   `commodity_id` همان site نگاشت کن. alias برای input parsing است، نه
   mapping خروجی مدل. عدم تطابق یا چندتطابق = abstain.
4. policy سه‌حالته تعریف کن:
   - `AUTO_SELECT`: نتیجه یکتا، Snapshot تازه و threshold کامل؛
   - `CONFIRM`: چند کالای نزدیک یا confidence ناکافی؛
   - `ABSTAIN`: دادهٔ کهنه/ناقص، قیمت خارج از محدوده یا خطای artifact.
5. offer جدید نباید خودش پیش از commit به عنوان evidence Snapshot خودش وارد
   شود. inference از strict-cutoff پیش از offer انجام می‌شود؛ offer پس از
   commit فقط برای تصمیم‌های بعدی به Market Store می‌رود.
6. audit مینیمال و append-only تصمیم بساز: versionها، status، reason و
   commodity نهایی؛ بدون متن خام یا شناسه‌های خصوصی.

### گیت خروج

- نام کالا با قیمت اشتباه یا دادهٔ کهنه به‌صورت خودکار ثبت نشود.
- canonical-name-to-database mapping در سایت‌های دارای ID متفاوت صحیح باشد.
- behavior legacy در حالت flag خاموش بدون تغییر بماند.
- تست‌های ambiguity، overlap، stale snapshot، range بیرونی، alias و
  idempotency سبز باشند.

### rollback

feature flag تصمیم محصول خاموش می‌شود؛ API/بات به rule فعلی بازمی‌گردند و
audit فقط read-only می‌ماند.

---

## مرحلهٔ P6 — اتصال بات، API و WebApp

### هدف

یک رفتار برابر برای همهٔ سطح‌های ثبت آفر.

### کارها

1. shared parser بات را طوری تغییر بده که در نبود نام کالا، ابتدا سرویس P5
   را صدا بزند؛ default امام فقط fallback policy صریح باشد، نه نتیجهٔ پنهان.
2. `/api/offers/parse`، preview و `OfferCreate` را توسعه بده:
   - client نمی‌تواند صرفاً یک `commodity_id` حدسی و بدون receipt معتبر به
     سرور تحمیل کند؛
   - API نتیجهٔ inference و گزینه‌های confirm را برمی‌گرداند؛
   - server در زمان submit دوباره freshness/receipt را کنترل می‌کند.
3. WebApp در preview نام انتخاب‌شده، سطح اطمینان و حالت تأیید را روشن نشان
   دهد. کاربر در حالت ابهام فقط از گزینه‌های canonical مجاز انتخاب می‌کند.
4. پیام موفقیت و متن منتشرشدهٔ آفر همواره نام کالا را صریح نشان دهند.
5. idempotency fingerprint شامل commodity نهاییِ تأییدشده باشد تا retry با
   Snapshot بعدی آفر متفاوت تولید نکند.

### گیت خروج

- یک ورودی بدون نام کالا در بات و WebApp به نتیجهٔ یکسان برسد.
- retry شبکه، تغییر Snapshot و submit همزمان موجب تغییر بی‌اجازهٔ کالا نشود.
- مسیر دارای نام explicit همچنان parser فعلی را حفظ کند، اما conflict شدید
  نام/قیمت را طبق policy هشدار یا abstain کند.
- تست unit، API contract، browser/E2E و testهای bot handler سبز باشند.

### rollback

فقط قابلیت inference در input غیرفعال می‌شود؛ آفرهای ثبت‌شده معتبر هستند و
offer create معمولی با کالای صریح باقی می‌ماند.

---

## مرحلهٔ P7 — Shadow parallel، سنجش و Release تدریجی

### هدف

اثبات عملکرد واقعی پیش از تبدیل `AUTO_SELECT` به رفتار گسترده.

### کارها

1. ابتدا P5/P6 با `CONFIRM` یا shadow-visible فعال شود؛ تصمیم پیشنهادی و
   انتخاب نهایی کاربر به‌صورت privacy-minimized مقایسه شوند.
2. معیارها را به تفکیک کالا، CASH/TOMORROW، ساعت تهران، سن Snapshot، منشأ
   غالب داده و وضعیت بازار گزارش کن:
   - درصد auto/confirm/abstain؛
   - اختلاف پیشنهاد با انتخاب نهایی و تصحیح اپراتور؛
   - نرخ خطای کالا و نرخ conflict نام/قیمت؛
   - freshness، lag Collector، missing-source و coverage interval.
3. auto-selection ابتدا برای سلول‌های پرنمونه و فاصله‌دار فعال شود؛ کالاها و
   بازارهای کم‌داده در `CONFIRM` باقی بمانند.
4. freeze switch، rollback snapshot/bundle و playbook incident را تمرین کن.

### گیت خروج برای Release گسترده

- معیار accuracy و abstention برای هر cell از threshold مصوب owner عبور کند؛
- هیچ افزایش معنی‌دار در خطاهای ثبت آفر، latency یا خطاهای معامله دیده نشود؛
- source health و recovery از restart واقعی آزموده شده باشد؛
- owner به‌صراحت promotion هر cell را تأیید کند. auto-promotion ممنوع است.

### rollback

در لحظه، `AUTO_SELECT` به `CONFIRM` یا `ABSTAIN` تغییر می‌کند؛ Snapshot و
bundle قبلی قابل انتخاب‌اند و Collectorها همچنان فقط داده جمع می‌کنند.

---

## مرحلهٔ P8 — کاهش تدریجی وابستگی به گروه‌های خارجی

### هدف

نه حذف ناگهانی، بلکه انتقال تدریجی مرجع اصلی از گروه‌ها به نقدشوندگی واقعی
پروژه.

### کارها

1. برای هر کالا/settlement، حجم آفر فعال، معاملات تأییدشده، spread، سن لنگر
   و کیفیت دادهٔ پروژه را اندازه بگیر.
2. policy وزن را بر اساس cell و evidence تنظیم کن؛ data پروژه در صورت کیفیت
   کافی غالب می‌شود ولی دادهٔ گروه ناگهان صفر نمی‌شود.
3. group source در صورت افت کیفیت/قطع ارتباط، با freshness و confidence
   پایین‌تر نمایش داده می‌شود، نه با دادهٔ ساختگی.
4. معیار خروج هر گروه از مسیر realtime را به‌صورت جداگانه ثبت و مالک آن را
   مشخص کن. تاریخچهٔ نرمال‌شدهٔ واجدشرایط برای آموزش نگهداری می‌شود.

### گیت خروج

- تغییر وزن بهبود یا دست‌کم عدم افت قابل‌توضیح در ارزیابی chronological نشان
  دهد.
- حذف هر منبع با replay و rollback قابل بازگردانی باشد.

---

## موارد صریحاً Deferred

- پیش‌بینی دقیقه/ساعت/روز آیندهٔ آبشده؛
- ارتقای Gemma به مسیر محصول؛
- CatBoost، PySR، residual calibrator و رابطه‌یابی خودکار به عنوان تصمیم‌گیر
  آنلاین؛
- sync مدل/داده بین `wa-fl` و `wa-ir` و رفتار زمان قطع اینترنت ایران؛
- تغییر مدل محصول برای trade form کاغذی.

این‌ها فقط پس از تکمیل P0 تا P7، دادهٔ زمانی کافی و تصمیم جدید مالک باز
می‌شوند.

## الزامات مستندسازی پس از هر مرحله

پایان هر مرحله بدون افزودن یادداشت زیر به همین فایل ناقص است. یادداشت باید
در بخش «گزارش اجرای مرحله‌ها» افزوده و در همان commit یا commit پایان مرحله
ثبت شود؛ نه در chat، log موقت یا فایل خارج از repository.

```markdown
### P<شماره> — <عنوان> — <YYYY-MM-DD> — <COMPLETE | BLOCKED | PARTIAL>

- Base/main commit:
- Promotion branch commit(s):
- Scope انجام‌شده و فایل‌های تغییرکرده:
- موارد عمداً انجام‌نشده:
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
- Migration و نتیجهٔ upgrade/downgrade (در صورت وجود):
- Test commands و نتیجهٔ دقیق:
- داده/fixture استفاده‌شده و محل امن آن (بدون secret یا raw PII):
- نتیجهٔ health/freshness/replay (در صورت مرتبط‌بودن):
- رفتار rollback آزموده‌شده:
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
- تصمیم مرحلهٔ بعد و تأیید لازم:
```

## گزارش اجرای مرحله‌ها

### P4-A — تحویل Outbox و Snapshot نقطه‌زمانی — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P4-A شامل consumer، artifact Snapshot،
  تست‌ها و این یادداشت روی `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/project_outbox_consumer.py`: claim دارای lease،
    projection مستقیم و idempotent به Market Store، complete/failure/retry
    محدود؛
  - `core/market_intelligence/market_snapshot.py`: Snapshot source-separated
    با cutoff دوگانهٔ event/available time و publish/load اتمیک؛
  - `docs/COIN_INTELLIGENCE_P4_SNAPSHOT_OUTBOX.md` و testهای اختصاصی.
- موارد عمداً انجام‌نشده:
  - هیچ worker، task lifespan، cron، config runtime یا اتصال به SQLite/Telegram
    واقعی فعال یا ثبت نشد؛
  - P2-B/C/D، producer عددی کامل، low-date/range/anchor، ranker محصولی و API
    inference منتقل نشده‌اند؛
  - هیچ code/config مرتبط با معماری سه‌سروره وارد نشده است.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - `PROJECT_OUTBOX_CONSUMER_VERSION = project-outbox-consumer-v1`؛
  - `MARKET_SNAPSHOT_SCHEMA_VERSION = 1`؛ Snapshot فقط روی
    `MARKET_STORE_CONTRACT_VERSION = 1` معتبر است؛
  - event پروژه با opaque key و quality مستقیم وارد همان table canonical
    `market_observations` می‌شود؛ جدول موازی جدیدی ساخته نشد.
- Migration و نتیجهٔ upgrade/downgrade: migration جدیدی در P4-A ساخته نشد.
  consumer به table P3 وابسته است و Snapshot صرفاً artifact file محلی است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_project_outbox_consumer
    tests.test_coin_intelligence_market_snapshot` با pycache موقت اجرا شد؛
    نتیجه: `Ran 7 tests ... OK`.
  - suite ترکیبی P1 تا P4-A به‌علاوهٔ baseline آفر/معامله/API/migration با
    environment ساختگی اجرا شد؛ نتیجه: `Ran 177 tests ... OK`. logهای اتصال
    ناموفق به PostgreSQL/Redis ساختگی expected بودند و هیچ endpoint واقعی
    استفاده نشد.
- داده/fixture استفاده‌شده و محل امن آن: فقط قیمت‌های synthetic داخل test و
  SQLite موقت process-local؛ هیچ raw message، credential، شماره یا دادهٔ بازار
  واقعی استفاده نشد.
- نتیجهٔ health/freshness/replay:
  - replay یک claim همان یک observation را نگه می‌دارد؛
  - payload نامعتبر fail-closed است؛ خطای Store retry می‌شود؛
  - دادهٔ event که در زمان `as_of` هنوز available نبوده، در Snapshot دیده
    نمی‌شود؛ تتر و هرات با واحد/منشأ مستقل دیده می‌شوند.
- رفتار rollback آزموده‌شده: شکست Store row را `PENDING` نگه می‌دارد؛
  publish نامعتبر Snapshot معتبر قبلی را replace نمی‌کند؛ هنوز هیچ runtime
  reader یا writer خودکار وجود ندارد.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - consumer در runtime register نشده و path volume/config آن هنوز policy
    عملیاتی ندارد؛ P4-B/P7 مالک آن است؛
  - Snapshot فعلی `PARTIAL_UNDERLYING_STATE` است و rate کالا تولید نمی‌کند؛
    P4-B مالک آن است؛
  - sourceهای خصوصی/IME/USDT product adapter هنوز موجود نیستند؛ P2-B/C/D
    مالک آن است.
- تصمیم مرحلهٔ بعد و تأیید لازم: P4-B فقط پس از انتقال حداقل adapterهای
  لازم، برای لنگر/rangeهای canonical و regression تاریخی شروع می‌شود.

### P0 — پایهٔ Promotion و انجماد scope — 2026-08-04 — COMPLETE

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`
- Promotion branch commit(s): the commit containing this note
  (`docs(coin-intelligence): record promotion baseline`).
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - worktree مستقل `/root/trading-bot/coin-commodity-inference-promotion`
    از `main` ساخته شد؛
  - [COIN_INTELLIGENCE_MAIN_PROMOTION_MANIFEST.md](COIN_INTELLIGENCE_MAIN_PROMOTION_MANIFEST.md)
    اضافه شد؛
  - این roadmap به برنچ Promotion منتقل شد تا گزارش مرحله‌ها همراه کد بماند.
- موارد عمداً انجام‌نشده:
  - هیچ کد inference، Collector، worker، migration، config runtime یا رفتار
    بات/WebApp تغییر نکرد؛
  - هیچ کد یا config معماری سه‌سروره از برنچ پژوهشی منتقل نشد.
- قرارداد/schema/versionهای افزوده یا تغییرکرده: هیچ‌کدام؛ manifest فقط
  inventory و dependency graph انتقال است.
- Migration و نتیجهٔ upgrade/downgrade: migration جدیدی ساخته یا اجرا نشد.
  `tests.test_migration_smoke` در baseline سبز بود؛ migrationهای موجود main
  فقط compatibility constraint باقی می‌مانند.
- Test commands و نتیجهٔ دقیق:
  - baseline زیر با environment ساختگی و فاقد credential/endpoint واقعی اجرا
    شد: `python3 -m unittest -q tests.test_manual_offer_validation
    tests.test_offer_creation_service tests.test_offers_router_create_guards
    tests.test_offers_router_create_success tests.test_offers_router_reads
    tests.test_offers_router_expire tests.test_trades_router_authoritative_guards
    tests.test_trades_router_authoritative_success
    tests.test_bot_trade_create_text_offer_parse_flow tests.test_migration_smoke`;
  - نتیجه: `Ran 146 tests ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط fixtureهای repository و env
  ساختگیِ process-local؛ هیچ دادهٔ بازار، پیام خام، session یا secret واقعی
  خوانده یا نوشته نشد.
- نتیجهٔ health/freshness/replay: خارج از scope P0؛ هیچ Collector یا Snapshot
  اجرا نشد.
- رفتار rollback آزموده‌شده: Promotion worktree از `main` جداست و حذف آن هیچ
  commit یا runtime اثرگذار بر `main` ندارد. branch source پژوهشی نیز تغییر
  نکرده است.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - schema عمومی Collector و schema legacy external observations هنوز یکی
    نشده‌اند؛ P1 مالک آن است؛
  - تتر/IME هنوز adapter محصولی ندارند؛ P2-D مالک آن است؛
  - listener پروژه هنوز Shadow best-effort است و outbox محصولی ندارد؛ P3
    مالک آن است.
- تصمیم مرحلهٔ بعد و تأیید لازم: ورود به P1، فقط برای طراحی و پیاده‌سازی
  قرارداد canonical Market Store و migration سازگار. پیش از P2، review
  قرارداد و retention لازم است.

### P1 — قرارداد canonical دادهٔ بازار — 2026-08-04 — COMPLETE

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P1 شامل contract، SQLite Store، تست‌ها و
  این یادداشت روی `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/market_contracts.py`: قرارداد immutable
    `MarketObservation` و representation نرمال‌شده؛
  - `core/market_intelligence/market_store.py`: SQLite Market Store versioned،
    idempotent write، compatibility view و explicit legacy import؛
  - `docs/COIN_INTELLIGENCE_MARKET_STORE_CONTRACT.md`: مرز privacy، unit و
    migration policy؛
  - `tests/test_coin_intelligence_market_store.py`: contract، timezone، unit،
    deduplication، privacy و upgrade tests.
- موارد عمداً انجام‌نشده:
  - هیچ Telegram/تتر/IME Collector یا scheduler اضافه/فعال نشد؛
  - هیچ event پروژه، callback ORM، Postgres outbox، API، Bot یا WebApp تغییر
    نکرد؛
  - هیچ runtime path، config، secret، volume policy یا sync مربوط به معماری
    سه‌سروره منتقل نشد؛
  - دادهٔ واقعی یا SQLite عملیاتی به branch وارد/مهاجرت داده نشد.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - `MARKET_STORE_CONTRACT_VERSION = 1` و
    `MARKET_STORE_SCHEMA_VERSION = 1` افزوده شد؛
  - تنها table قابل‌نوشتن `market_observations` است؛ تمام source familyها
    (`PROJECT`، group، عمومی/خصوصی، external و manual review) از یک قرارداد
    می‌گذرند؛
  - `external_market_observations` فقط view read-only برای
    `EXTERNAL_MARKET` است؛ schema خارجی دوم ساخته نشد؛
  - event key فقط bytes digest opaque است. raw text/ID/name/link/phone در
    contract و attributeهای آن ممنوع است؛
  - UTC aware اجباری است و Tehran datetime/date/minute/weekday فقط از
    `event_time_utc` مشتق می‌شود؛
  - unitهای canonical صریح‌اند، و mismatchهای هرات/تتر/اونس/آبشده/IME/coin
    fail-closed می‌شوند؛ هیچ conversion یا bridge پنهان وجود ندارد.
- Migration و نتیجهٔ upgrade/downgrade:
  - migration جدید PostgreSQL/Alembic ساخته نشد. P1 به‌صورت عمدی SQLite
    مستقل از migrationهای باقی‌ماندهٔ `main` است؛ Postgres در P3 فقط outbox
    محصول خواهد گرفت؛
  - `initialize_market_store` روی schema قدیمی به‌جای DDL in-place،
    `MarketStoreMigrationRequired` می‌دهد؛
  - `upgrade_legacy_market_store(source_path, destination_path)` source را
    با SQLite read-only باز و فقط factهای دارای unit نرمال‌شده را به مقصد
    جدا کپی می‌کند. source حذف/ویرایش نمی‌شود؛ unit نامعلوم skip می‌شود؛
    raw text/identity کپی نمی‌شود؛
  - upgrade و rollback فقط با fixture SQLite جدا آزموده شد؛ migration دادهٔ
    واقعی یا downgrade destructive انجام نشد.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -v tests.test_coin_intelligence_market_store` →
    `Ran 7 tests ... OK`؛
  - baseline P0 به‌علاوهٔ P1 با env ساختگیِ process-local اجرا شد:
    `python3 -m unittest -q tests.test_coin_intelligence_market_store
    tests.test_manual_offer_validation tests.test_offer_creation_service
    tests.test_offers_router_create_guards tests.test_offers_router_create_success
    tests.test_offers_router_reads tests.test_offers_router_expire
    tests.test_trades_router_authoritative_guards
    tests.test_trades_router_authoritative_success
    tests.test_bot_trade_create_text_offer_parse_flow tests.test_migration_smoke`
    → `Ran 153 tests in 4.169s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن:
  - فقط temporary SQLite fixture در test process؛ یک raw text نمونه صرفاً در
    legacy source fixture برای اثبات عدم انتقال وجود دارد و در مقصد هرگز
    ذخیره/چاپ نمی‌شود؛
  - API key، session، کانال، پیام واقعی، نام واقعی، model artifact و دیتابیس
    عملیاتی استفاده نشد.
- نتیجهٔ health/freshness/replay:
  - replay برای همان opaque event key یک ردیف را update می‌کند و duplicate
    نمی‌سازد؛
  - health/freshness adapterها خارج از P1 است، زیرا هیچ adapter اجرا نمی‌شود.
- رفتار rollback آزموده‌شده:
  - P1 هیچ مصرف‌کنندهٔ runtime ندارد؛ حذف/عدم استفاده از Store اثر ثبت
    Offer/Trade را تغییر نمی‌دهد؛
  - legacy import فقط به مقصد جدا می‌نویسد و source read-only است، بنابراین
    rollback برابر عدم سوییچ adapter به مقصد است.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - جدول external legacy واقعی ممکن است unitهای غیرcanonical داشته باشد؛
    P2-D باید conversion policy قابل‌آزمون و migration report واقعی تعریف
    کند؛
  - path حفاظت‌شدهٔ SQLite، retention سه‌روزهٔ raw staging و health endpoint
    در P2 تعیین می‌شوند؛
  - semantic mapping تمام source codeها به `source_family` و تمام attributeهای
    آبشده در P2-A/B/C تکمیل می‌شود؛
  - Projection قطعی Offer/Trade پروژه و lifecycle آن در P3 مالک دارد.
- تصمیم مرحلهٔ بعد و تأیید لازم:
  - P2 و P3 از نظر dependency آمادهٔ توسعهٔ موازی هستند، اما مرحلهٔ بعدی
    این branch P2-A است: انتقال Collectorهای عمومی به adapter بدون فعال‌سازی
    runtime. پیش از شروع، policy volume/runtime path و retention باید در همان
    مرحله ثبت شود.

### P2-A — adapter کانال‌های عمومی تلگرام — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P2-A شامل parser/ingest/transport
  اختیاری، schema additive و این یادداشت روی
  `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/public_telegram/`: allowlist، parser pure،
    ingest idempotent و transport صریحِ optional برای چهار source عمومی؛
  - `core/market_intelligence/market_store.py`: schema `1 → 2` با جدول کوچک
    `market_source_checkpoints` و API cursor؛
  - `docs/COIN_INTELLIGENCE_PUBLIC_TELEGRAM_ADAPTER.md`: policy source،
    conversion، runtime boundary و credential boundary؛
  - `tests/test_coin_intelligence_public_telegram.py`: parser، ingest،
    checkpoint، replay، minute compaction، side linking و fake transport.
- موارد عمداً انجام‌نشده:
  - Telethon به dependency سراسری یا runtime production اضافه نشد؛
  - هیچ session/API key/phone واقعی خوانده نشد؛ هیچ کانال واقعی، network،
    daemon، cron، startup hook یا volume path فعال نشد؛
  - sourceهای خصوصی آبشده، گروه‌های سکه، تتر و IME هنوز منتقل نشده‌اند؛
  - Snapshot/producer یا inference هنوز consumer این observationها نیست.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - `MARKET_STORE_SCHEMA_VERSION = 2`; upgrade از v1 فقط cursor source را
    اضافه می‌کند و factهای موجود را بازنویسی نمی‌کند؛
  - متن و message ID در `market_observations` ذخیره نمی‌شوند. message ID فقط
    در checkpoint برای resume است و event key fact یک digest opaque است؛
  - آبشده/هراتِ این sourceها از تومان به `IRT` (`×10`) تبدیل می‌شود؛ اونس
    USD/spot باقی می‌ماند؛
  - آبشدهٔ NaghdP `PAPER_NORMAL` است و trade بدون side فقط با آفر strictly
    prior هم‌قیمت/هم‌settlement تا ۱۸۰ ثانیه enrich می‌شود؛
  - قیمت گرم و summaryهای ساعت‌به‌ساعت حذف می‌شوند؛ XAUUSD فقط آخرین quote
    همان دقیقه را نگه می‌دارد؛ هر source provenance مستقل دارد.
- Migration و نتیجهٔ upgrade/downgrade:
  - PostgreSQL/Alembic تغییر نکرد؛ migrationهای باقی‌ماندهٔ main دست‌نخورده
    هستند؛
  - SQLite v1→v2 در temporary fixture تست شد و فقط
    `market_source_checkpoints` را ساخت؛ rollback برابر عدم شروع adapter
    جدید است، چون P2-A هنوز consumer/runtime فعال ندارد.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -v tests.test_coin_intelligence_market_store
    tests.test_coin_intelligence_public_telegram` → `Ran 19 tests ... OK`؛
  - baseline کامل P0/P1/P2-A با env ساختگیِ process-local اجرا شد →
    `Ran 165 tests in 4.519s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن:
  - فقط fixtureهای synthetic داخل test process و SQLite موقت؛ هیچ متن واقعی
    کانال، credential، session، API call یا دیتابیس عملیاتی استفاده نشد.
- نتیجهٔ health/freshness/replay:
  - replay message edited به همان opaque event key update می‌شود؛ forwarded
    یا پیام ignored checkpoint را جلو می‌برد ولی fact نمی‌سازد؛
  - message قدیمی‌تر اونس در همان دقیقه نمی‌تواند quote جدیدتر را overwrite
    کند؛ health/freshness شبکه تا شروع عملیاتی collector deferred است.
- رفتار rollback آزموده‌شده:
  - adapter فقط library است و به startup متصل نیست؛ حذف/عدم فراخوانی آن روی
    Offer/Trade یا API فعلی اثری ندارد؛
  - schema upgrade additive است و دادهٔ source واقعی نداشت.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - نصب optional dependency، path دقیق volume، health/alert و schedule باید
    در deployment review پایان P2 تصویب شوند؛
  - conversion تومان→IRT با replay دادهٔ واقعی و cross-check منبع در P4
    دوباره سنجیده می‌شود؛
  - parser عمومی هنوز صرفاً rule-based است و موارد مبهم را emit نمی‌کند؛
  - P2-B/C/D و P3 همچنان لازم‌اند.
- تصمیم مرحلهٔ بعد و تأیید لازم:
  - P2-A از نظر کد و تست offline کامل است، ولی P2 کلان تا انجام P2-B/C/D
    `PARTIAL` می‌ماند؛ گام بعدی P3 (outbox پروژه) می‌تواند مستقل از collector
    توسعه یابد و به هیچ معماری سه‌سروره متصل نخواهد شد.

### P2-A1 — فرمان دستی و session-safe برای کانال‌های عمومی — 2026-08-04 — COMPLETE (manual only)

- Scope انجام‌شده:
  - `scripts/collect_coin_market_telegram.py` اجرای one-shot چهار source
    allowlisted را به‌شکل صریح فراهم می‌کند. مسیر Store و session باید زیر
    یک runtime root موجود باشند؛ خروجی JSON فقط counterهای source عمومی را
    دارد و مسیر، متن یا credential را چاپ نمی‌کند.
  - transport دیگر به‌صورت پیش‌فرض login تعاملی شروع نمی‌کند. اگر session
    Telegram مجاز نباشد، collector با
    `public_telegram_session_authorization_required` متوقف می‌شود. bootstrap
    فقط با `--bootstrap-session` و TTY مجاز است.
  - `--replay-window` تنها checkpoint را برای بازهٔ bounded انتخاب‌شده کنار
    می‌گذارد؛ run معمولی فقط پس از checkpoint ادامه می‌دهد.
- Test command و نتیجه:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -q
    tests.test_collect_coin_market_telegram
    tests.test_coin_intelligence_public_telegram
    tests.test_publish_coin_intelligence_snapshot
    tests.test_coin_intelligence_snapshot_publisher
    tests.test_coin_intelligence_market_snapshot
    tests.test_coin_intelligence_market_store` → `Ran 33 tests ... OK`.
- مرز عملیاتی و گیت باقی‌مانده:
  - Telethon همچنان dependency اختیاری است و به image/runtime اضافه نشده؛
    این commit نه session واقعی می‌سازد، نه API/phone می‌خواند و نه network
    call انجام می‌دهد.
  - پیش از staging باید dependency در image مخصوص collector، runtime root
    خارج از checkout، مجوز session و مالک اجرای one-shot تصویب شود. پس از
    آن publish/check P4-D می‌تواند از همان Market Store استفاده کند؛ cron
    یا worker خودکار هنوز مرحلهٔ جداگانه است.

### P2-B — adapter آبشدهٔ خصوصی — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P2-B شامل parser خالص، projection
  canonical، minute aggregation، Snapshot extension، test و این یادداشت روی
  `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/private_gold.py`: تفکیک physical/paper،
    today/tomorrow، normal/reverse/swim، conditional، edit-trade و projection
    بدون raw text؛
  - `core/market_intelligence/market_snapshot.py`: signalهای جدا برای دو
    physical cell و شش paper cell خصوصی؛
  - `docs/COIN_INTELLIGENCE_PRIVATE_GOLD_ADAPTER.md` و testهای offline.
- موارد عمداً انجام‌نشده:
  - client خصوصی Telegram، worker، checkpoint/health/schedule و ingest واقعی
    ایجاد یا فعال نشد؛
  - در نتیجه P2-B و کل P2 همچنان `PARTIAL` هستند.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - `PRIVATE_GOLD_PARSER_VERSION = private-gold-rules-v1`؛
  - قیمت source که تومان است فقط با conversion صریح `×10` وارد
    `IRT_PER_MESGHAL_750` می‌شود؛
  - physical non-conditional raw می‌ماند؛ paper minute quote derived با وزن
    offer=`1` و trade=`3` و cellهای جدا ساخته می‌شود؛
  - conditional fact حفظ می‌شود اما در quote عادی کنار گذاشته می‌شود؛
  - `edited_at` زمان معامله است و `PARTIAL` بدون quantity معاملهٔ کامل
    تولید نمی‌کند.
- Migration و نتیجهٔ upgrade/downgrade: Alembic یا schema جدیدی ایجاد نشد؛
  فقط همان canonical `market_observations` P1 نوشته می‌شود. rollback برابر
  عدم فراخوانی adapter است؛ runtime خودکاری وجود ندارد.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_private_gold
    tests.test_coin_intelligence_market_snapshot` با pycache موقت اجرا شد؛
    نتیجه: `Ran 12 tests ... OK`.
  - suite ترکیبی P1 تا P4-A به‌علاوهٔ P2-B و baseline آفر/معامله/API/migration
    با environment ساختگی اجرا شد؛ نتیجه: `Ran 186 tests ... OK`. logهای
    endpointهای ساختگی expected بودند و هیچ service واقعی استفاده نشد.
- داده/fixture استفاده‌شده و محل امن آن: تمام textها و priceها synthetic و
  فقط داخل test process/SQLite موقت هستند؛ هیچ event، channel، identity یا
  credential واقعی استفاده نشد.
- نتیجهٔ health/freshness/replay:
  - same opaque source-event/role با upsert تکراری fact دوم ایجاد نمی‌کند؛
  - Snapshot paper variantها را merge نمی‌کند و conditional را در aggregate
    عادی وارد نمی‌کند؛ health شبکه تا ساخته‌شدن transport وجود ندارد.
- رفتار rollback آزموده‌شده: unmarked input abstain می‌شود؛ partial trade
  بدون quantity فقط offer می‌ماند؛ هیچ background writer به product متصل
  نیست و حذف/عدم استفاده از library روی آفر/معاملهٔ پروژه اثر ندارد.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - معنی تومان source باید با replay محدود production پیش از activation
    cross-check شود؛ P2-B deployment owner؛
  - lifecycle واقعی edit/verifier و raw retention باید در worker جداگانه
    آزموده شود؛ P2-B completion owner؛
  - P2-C group coin و P2-D USDT/IME همچنان لازم‌اند.
- تصمیم مرحلهٔ بعد و تأیید لازم: ابتدا P2-C/P2-D یا حداقل adapterهای
  underlying موردنیاز باید offline منتقل شوند، سپس P4-B anchor/range با
  replay historical شروع می‌شود.

### P2-B1 — raw staging و reconciliation آبشدهٔ خصوصی — 2026-08-04 — COMPLETE (library only)

- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/private_gold_staging.py`: SQLite موقت با
    retention دقیق سه‌روزه، مسیر محافظت‌شده خارج از checkout، deduplication
    و ادغام causal آفر/verification با کلید message؛
  - `core/market_intelligence/private_gold.py`: editِ بدون نتیجهٔ صریحِ
    مخالف، طبق convention source، معاملهٔ کامل است؛ `NONE` صریح مقدم است؛
  - `tests/test_coin_intelligence_private_gold_staging.py`: ترتیب عادی و
    معکوس، partial بدون quantity، edit-only، retention و guard path.
- مرز قطعی و رفتار ایمنی:
  - متن خام فقط در staging موقت می‌ماند؛ Market Store فقط factهای اقتصادی با
    opaque event key دریافت می‌کند؛
  - update معامله—even اگر قبل از آفر برسد—تا رسیدن متن آفر fact نمی‌سازد؛
    آفر و معامله availability مستقل خود را حفظ می‌کنند؛
  - worker، Telethon، session، network، cron، config runtime و اتصال به
    دادهٔ واقعی اضافه یا فعال نشده‌اند.
- قرارداد/schema افزوده:
  - `PRIVATE_GOLD_STAGING_SCHEMA_VERSION = 1` و retention=`3 days`؛
  - جدول staging فاقد sender/link/channel است و فقط message id، متن، زمان و
    نتیجهٔ verification لازم برای reconciliation را کوتاه‌مدت نگه می‌دارد.
- Test command و نتیجه:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -v
    tests.test_coin_intelligence_private_gold_staging
    tests.test_coin_intelligence_private_gold
    tests.test_coin_intelligence_market_store
    tests.test_coin_intelligence_market_snapshot` → `Ran 28 tests ... OK`؛
  - تمام `test_coin_intelligence_*.py` با environment ساختگیِ فقط برای
    import config اجرا شد؛ `Ran 119 tests ... OK` (هیچ اتصال DB/Redis/Telegram
    واقعی انجام نشد)؛
  - `compileall` چهار فایلِ تغییرکرده نیز با موفقیت اجرا شد.
- گیت مرحلهٔ بعد:
  - runner دستیِ one-shot با runtime root مصوب، health و metric ترتیب
    رویداد، بدون activation خودکار ساخته می‌شود.

### P2-B2 — decoder کانال‌های آفر/تأیید آبشده — 2026-08-04 — COMPLETE (library only)

- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/private_gold_payloads.py`: decoder سخت‌گیر
    object/list/batch delimiter برای envelope نسخهٔ `1.0` و route جداگانهٔ
    `OFFER`/`TRADE`؛
  - `tests/test_coin_intelligence_private_gold_payloads.py`: route مخالف،
    payload ناقص، batch، duplicate/conflict و staging-rejection را با fixture
    synthetic بررسی می‌کند.
- مرز قطعی و رفتار ایمنی:
  - stream از channel بیرونیِ trusted collector می‌آید، اما inner event نیز
    باید `market=gold`، `source_key=account1_channel` و `event_type` سازگار
    داشته باشد؛ عدم تطابق وارد staging نمی‌شود؛
  - `offer_verified` با `verification.result=no_trade` و بدون شیء `trade`
    صراحتاً `NONE` است و edit را override می‌کند؛ نتیجهٔ دیگر بدون trade
    قابل‌تأیید reject می‌شود؛
  - decoder هیچ channel ID، نام، متن یا identifierی را در report/Market Store
    نمی‌نویسد؛ فقط متن موقتِ معتبر را برای staging برمی‌گرداند؛
  - هیچ client، config واقعی، network، listener، worker یا scheduler اضافه
    یا فعال نشده است.
- Test command و نتیجه:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -v
    tests.test_coin_intelligence_private_gold_payloads
    tests.test_coin_intelligence_private_gold_staging
    tests.test_coin_intelligence_private_gold` → `Ran 23 tests ... OK`.
  - تمام `test_coin_intelligence_*.py` با environment ساختگیِ فقط برای
    import config اجرا شد؛ `Ran 125 tests ... OK` و هیچ اتصال واقعی انجام
    نشد.
- گیت مرحلهٔ بعد:
  - runner دستی باید mapping channel→stream را فقط از config runtime بگیرد،
    pipeline را در commit/retry کنترل‌شده اجرا کند و health/counter ترتیب
    event را بدون raw data ارائه دهد.

### P2-B3 — pipeline محلی آبشدهٔ خصوصی — 2026-08-04 — COMPLETE (library only)

- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/private_gold_pipeline.py`: اتصال caller-driven
    decode → staging → fact promotion → refresh quote دقیقه‌ای کاغذی؛
  - `tests/test_coin_intelligence_private_gold_pipeline.py`: ترتیب معکوس
    verifier/offer، quote بسته‌شده، دقیقهٔ باز و idempotency را بررسی می‌کند.
- مرز قطعی و رفتار ایمنی:
  - فقط paper non-conditional به quote دقیقه‌ای می‌رود؛ physical همچنان
    fact منفرد است؛ update دیررسِ حداکثر سه‌روزه می‌تواند quote همان دقیقه را
    با event key ثابت اصلاح کند؛
  - commit دو SQLite عمداً به caller سپرده شده است: ابتدا staging قابل retry
    حفظ می‌شود و factهای opaque idempotent هستند؛ بنابراین failure میان دو
    commit سبب دوباره‌شماری نمی‌شود؛
  - هیچ runner، file reader، config، Telegram/Telethon، worker یا scheduler
    فعال نشده است.
- Test command و نتیجه:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -v
    tests.test_coin_intelligence_private_gold_pipeline
    tests.test_coin_intelligence_private_gold_payloads
    tests.test_coin_intelligence_private_gold_staging
    tests.test_coin_intelligence_private_gold` → `Ran 25 tests ... OK`.
  - تمام `test_coin_intelligence_*.py` با environment ساختگیِ فقط برای
    import config اجرا شد؛ `Ran 127 tests ... OK` و هیچ اتصال واقعی انجام
    نشد.
- گیت مرحلهٔ بعد:
  - اتصال مستقیم Telegram و scheduler پس از approval جدا و پس از مشاهدهٔ
    telemetry runner روی staging انجام می‌شود.

### P2-B4 — runner دستیِ spool آبشدهٔ خصوصی — 2026-08-04 — COMPLETE (manual only)

- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `scripts/ingest_private_gold_event_spool.py`: one-shot runner برای فایل
    JSONL محلی، با spoolهای جداگانهٔ `--offer-spool` و `--trade-spool`؛
  - `tests/test_ingest_private_gold_event_spool.py`: path guard، record بد،
    retry/idempotency و خروجی redacted را تست می‌کند.
- مرز قطعی و رفتار ایمنی:
  - هر record فقط `published_at_utc` و `payload_text` می‌خواند؛ stream از
    گزینهٔ CLI تعیین و در decoder دوباره با inner event verify می‌شود؛ فایل
    spool هرگز حذف، rename یا rewrite نمی‌شود؛
  - staging ابتدا commit می‌شود. اگر Market Store commit نشود، runner در
    اجرای بعدی همان factهای opaque را بدون دوباره‌شماری rebuild می‌کند؛
  - timestamp بیرونیِ spool فقط metadata validate می‌شود؛ availability fact
    زمان دریافت محافظه‌کارانهٔ runner است. این قاعده backfillهایی را که
    timestamp انتشارشان از زمان رویداد عقب‌تر است بدون look-ahead می‌پذیرد؛
  - lock غیرمسدودکنندهٔ staging از دو writer محلی جلوگیری می‌کند؛ خروجی فقط
    counter و status دارد، نه متن، شناسه یا path؛
  - Telegram/Telethon، credential، listener، worker، cron و startup hook
    اضافه یا فعال نشده‌اند.
- Test command و نتیجه:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -v
    tests.test_ingest_private_gold_event_spool
    tests.test_coin_intelligence_private_gold_pipeline
    tests.test_coin_intelligence_private_gold_payloads
    tests.test_coin_intelligence_private_gold_staging
    tests.test_coin_intelligence_private_gold` → `Ran 28 tests ... OK`.
  - تمام `test_coin_intelligence_*.py` با environment ساختگیِ فقط برای
    import config اجرا شد؛ `Ran 127 tests ... OK`؛ command runner نیز جداگانه
    `Ran 3 tests ... OK` بود. هیچ اتصال واقعی انجام نشد.
- گیت مرحلهٔ بعد:
  - پیش از transport Telegram، باید owner اجرایی، mapping واقعی channel→spool
    و retention/health telemetry در staging تصویب شوند. هیچ scheduler بدون
    این approval ساخته یا فعال نمی‌شود.

### P2-C-A — فیلتر نخست گروه‌های سکه — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P2-C-A شامل parser خالص، projection
  canonical، test و این یادداشت روی
  `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_groups.py`: rejection policy، parse
    offerهای explicit، unit project، settlement/trade form و row unresolved؛
  - `docs/COIN_INTELLIGENCE_COIN_GROUPS_FIRST_PASS.md` و testهای synthetic.
- موارد عمداً انجام‌نشده:
  - raw staging/retention، Telegram transport، reply-chain، trade detection،
    partial fill، edit reconciliation و contextual commodity resolution
    پیاده/فعال نشده‌اند؛ P2-C همچنان `PARTIAL` است.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - offer گروه به `COIN_<canonical-code>` (یا `COIN_UNRESOLVED`) و unit
    `PROJECT_THOUSAND_TOMAN` وارد canonical fact table می‌شود، اما همهٔ
    آن‌ها تا P2-C-B `PENDING_REVIEW` هستند؛
  - کالای بی‌نام عمداً `COIN_UNRESOLVED` است، نه default Imam؛ نام صریح هم
    تا سنجش strictly-prior بازار حق ورود به مدل ندارد؛
  - 403/404/کشیک و ساختار ناقص fact ندارند؛ هر خط multi-line مستقل است؛
  - source identity/text در event key opaque می‌شوند و در column/attribute
    ذخیره نمی‌شوند.
- Migration و نتیجهٔ upgrade/downgrade: migration جدیدی ندارد و worker
  خودکار ندارد. rollback برابر عدم فراخوانی library است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_coin_groups` با
    pycache موقت اجرا شد؛ نتیجه: `Ran 6 tests ... OK`.
  - baseline ترکیبیِ P0 تا P4-A/P2-B/P2-D/P2-C-A و guardهای Offer/Trade/
    migration با env ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 197 tests in 5.067s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط text/price synthetic در test
  process و SQLite موقت؛ هیچ گروه، هویت، message ID یا export واقعی استفاده
  نشد.
- نتیجهٔ health/freshness/replay: first-pass یک parser pure است؛ transport و
  replay edited message intentionally deferred هستند. event key برای همان
  message/line deterministic است.
- رفتار rollback آزموده‌شده: input ناقص/استثنایی fact نمی‌سازد؛ هیچ row
  گروهی پیش از validation eligible نیست؛ هیچ مسیر Offer/Trade محصول تغییر
  نکرده است.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - default امام و نام explicit هنوز باید با Snapshot strictly-prior validate
    شوند؛ P2-C-B/P5 مالک؛
  - معامله و قیمت توافقی/partial fill هنوز parse نشده‌اند؛ P2-C-B مالک؛
  - raw three-day staging و live transport نیازمند deployment policy جداست.
- تصمیم مرحلهٔ بعد و تأیید لازم: P2-C-B باید پیش از activation گروه‌ها
  انجام شود؛ P4-B فقط پس از validation می‌تواند fact گروه را مصرف کند.

### P2-C-B1 — raw staging محدود گروه‌های سکه — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit B1 شامل staging boundary، hardening
  eligibility مرحلهٔ A، test و این یادداشت روی
  `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_group_staging.py`: SQLite جدا با raw text
    کوتاه‌مدت، reply graph، sender digest، content-digest idempotency، edit
    revision و purge سه‌روزه؛
  - hardening در `coin_groups.py`: هیچ offer گروه، حتی نام صریح، پیش از
    validation علّی `ELIGIBLE` نیست؛
  - `docs/COIN_INTELLIGENCE_COIN_GROUP_STAGING.md` و testهای synthetic.
- موارد عمداً انجام‌نشده:
  - transport Telegram، split payload چند-event، collector/checkpoint،
    contextual price resolver، trade detection و promotion به Store فعال یا
    پیاده نشده‌اند.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - schema جداگانهٔ `coin_group_staged_messages` فقط در volume runtime خارج
    از checkout مجاز است؛ مسیر زیر repository fail-closed رد می‌شود؛
  - کلید موقت `(group_number,message_id)` است؛ replay برابر no-op و edit
    واقعی فقط current version را با revision بیشتر جایگزین می‌کند؛
  - sender plaintext ذخیره نمی‌شود؛ متن و reply بعد از سه روز حذف می‌شوند؛
  - Store/Model فقط از fact نهایی privacy-minimized استفاده خواهد کرد.
- Migration و نتیجهٔ upgrade/downgrade: SQLite staging schema v1 مستقل است؛
  migration application ندارد و upgrade implicit ممنوع است. rollback برابر
  توقف caller و حذف صرفاً staging تازه‌ساخته‌شده طبق runbook بعدی است، نه
  حذف Market Store.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_coin_groups
    tests.test_coin_intelligence_coin_group_staging` با pycache موقت اجرا
    شد؛ نتیجه: `Ran 11 tests in 0.121s ... OK`.
  - baseline ترکیبیِ P0 تا P4-A/P2-B/P2-D/P2-C-B1 و guardهای Offer/Trade/
    migration با env ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 202 tests in 4.823s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط fixture synthetic در
  `TemporaryDirectory`؛ هیچ متن، نام، شناسه، channel یا export واقعی در
  checkout/staging پایدار ساخته نشد.
- نتیجهٔ health/freshness/replay: duplicate replay revision را زیاد نمی‌کند؛
  edit revision را یک‌بار زیاد می‌کند؛ expiry دقیق سه روز پس از
  `available_at_utc` است. scheduler و health endpoint هنوز وجود ندارند.
- رفتار rollback آزموده‌شده: مسیر repository، ID/timestamp نامعتبر و متن
  بیش‌ازحد fail-closed هستند؛ purge فقط جدول staging را حذف می‌کند.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - activation path باید runtime root، encryption/backup، lock و metricهای
    privacy-safe را پیش از live data تعیین کند؛ P2-C deployment owner؛
  - rowهای PENDING هنوز به resolver strictly-prior و linking معامله نیاز
    دارند؛ P2-C-B2/B3/B4 مالک؛
  - هیچ پیکربندی سه‌سروره یا sync در این stage افزوده نشده است.
- تصمیم مرحلهٔ بعد و تأیید لازم: B2 باید input JSON یک‌رویدادی/چندرویدادی را
  بدون duplication به staging map کند؛ B3 فقط پس از آن resolution کالای
  explicit/unnamed را با snapshot strictly-prior اضافه می‌کند.

### P2-C-B2 — decoder JSON تک/چندرویدادی گروه‌ها — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit B2 شامل decoder pure، test و این
  یادداشت روی `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_group_payloads.py`: decode object، array
    و divider افقی مستند؛ routing strict برای دو گروه؛ map به staging؛
  - `docs/COIN_INTELLIGENCE_COIN_GROUP_PAYLOADS.md` و fixtureهای synthetic.
- موارد عمداً انجام‌نشده:
  - Telethon listener، archive file، cursor، scheduler و config runtime
    اضافه/فعال نشده‌اند؛ text فقط از input گذرا به B1 منتقل می‌شود.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - availability صرفاً timestamp trusted outer collector است و event time
    داخلی نمی‌تواند آن را عقب ببرد؛
  - source غیر `account2_group1/2`، market غیر coin، type نامعتبر، ID/date
    ناقص و reply parent مبهم fail-closed می‌شوند؛
  - duplicate دقیق یک بار، update با edit time اکیداً جدیدتر یک نسخه و
    conflict هم‌زمان/بی‌ترتیب هیچ نسخه‌ای ندارد.
- Migration و نتیجهٔ upgrade/downgrade: migration ندارد؛ decoder/statging
  library بدون invocation اثر عملیاتی ندارد. rollback برابر عدم فراخوانی آن
  است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_coin_groups
    tests.test_coin_intelligence_coin_group_staging
    tests.test_coin_intelligence_coin_group_payloads` با pycache موقت اجرا
    شد؛ نتیجه: `Ran 16 tests in 0.208s ... OK`.
  - baseline ترکیبیِ P0 تا P4-A/P2-B/P2-D/P2-C-B2 و guardهای Offer/Trade/
    migration با env ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 207 tests in 5.169s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: JSON و متن synthetic فقط در test
  process/TemporaryDirectory؛ channel ID، Telegram payload واقعی، credential
  یا runtime log استفاده نشده است.
- نتیجهٔ health/freshness/replay: batch malformed sibling را از sibling درست
  جدا می‌کند؛ replays idempotent B1 می‌مانند؛ telemetry فعلاً فقط counter
  result است و endpoint/scheduler ندارد.
- رفتار rollback آزموده‌شده: cross-route، message conflict و reply مبهم row
  staging نمی‌سازند و Market Store دست‌نخورده می‌ماند.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - contract sender باید با fixture واقعیِ scrubbed از crawler پیش از
    activation replay شود؛ P2-C deployment owner؛
  - B3 causal commodity validation و B4 trade linking همچنان لازم‌اند؛
  - سه‌سروره/transport این feature عمداً defer شده است.
- تصمیم مرحلهٔ بعد و تأیید لازم: P2-C-B3 باید فقط با evidence strictly-prior
  و same-book، explicit price conflict و کالای unnamed را resolve یا reject
  کند؛ بدون default امام در data pipeline.

### P2-C-B3 — resolution علّی کالای گروه — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit B3 شامل resolver pure، projection
  privacy-minimized، test و این یادداشت روی
  `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_group_resolution.py`: strict-prior
    same-book anchor policy، abstention، reject explicit conflict و projection
    با availability صحیح reconciliation؛
  - `docs/COIN_INTELLIGENCE_COIN_GROUP_RESOLUTION.md` و fixtureهای synthetic.
- موارد عمداً انجام‌نشده:
  - provider anchor از Store/Snapshot، re-evaluation scheduling، write
    transaction، trade linking و هرگونه default امام وجود ندارد.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - حداقل دو anchor `ELIGIBLE` با unit از پیش تبدیل‌شدهٔ project لازم است؛
    anchor آینده، دیررس، ناهم‌settlement/form یا فاقد quality قابل استفاده
    نیست؛
  - کالای بی‌نام فقط با winner نزدیک و margin کافی `ELIGIBLE` می‌شود؛
    نام explicit ناسازگار `REJECTED` می‌شود و هرگز rewrite نمی‌شود؛
  - زمان event fact همان پیام اصلی و `available_at` آن زمان واقعی resolution
    است؛ بنابراین backtest/snapshot به آینده leak نمی‌کند.
- Migration و نتیجهٔ upgrade/downgrade: migration یا worker ندارد. rollback
  برابر عدم فراخوانی resolver است و factهای PENDING قبلی را تغییر نمی‌دهد.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q
    tests.test_coin_intelligence_coin_group_resolution` با pycache موقت اجرا
    شد؛ نتیجه: `Ran 5 tests in 0.008s ... OK`.
  - baseline ترکیبیِ P0 تا P4-A/P2-B/P2-D/P2-C-B3 و guardهای Offer/Trade/
    migration با env ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 212 tests in 5.365s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط anchor/text synthetic در process
  test؛ Store، Telegram، فایل staging واقعی یا دادهٔ کاربر خوانده نشد.
- نتیجهٔ health/freshness/replay: explicit typo در صورت winner متفاوت reject
  می‌شود؛ future/wrong-book/thin evidence pending می‌ماند؛ resolved fact تا
  resolution timestamp برای Snapshot در دسترس نیست.
- رفتار rollback آزموده‌شده: نبود/ابهام anchor هیچ offer را eligible نمی‌کند
  و projection فاقد text/message/sender/reply است.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - anchor provider باید source/unit/conversion را جدا و fail-closed ثابت
    کند؛ P2-C-B3 integration owner؛
  - trade agreement، partial fill و source edit chain هنوز B4 هستند؛
  - P4-B rate producer تا data flow کامل فعال نمی‌شود.
- تصمیم مرحلهٔ بعد و تأیید لازم: P2-C-B4 باید با reply graph موقت فقط
  confirmationهای قطعی را trade کند، قیمت توافقی را بر offer مقدم بداند و
  پس از این quality gate به canonical Store بنویسد.

### P2-C-B4 — linking محافظه‌کار trade گروه — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit B4 شامل reply linker pure، projection
  trade، hardening هویت staging، test و این یادداشت روی
  `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_group_trades.py`: root-link حداکثر ۱۲
    reply، confirmation ownership، قیمت توافقی، partial fill و overfill
    gate؛
  - B2 اکنون peer identity را پیش از display name به digest گذرا تبدیل
    می‌کند؛
  - `docs/COIN_INTELLIGENCE_COIN_GROUP_TRADES.md` و testهای synthetic.
- موارد عمداً انجام‌نشده:
  - writer transaction که offer/trade resolved را atomically به Store ببرد،
    reprocessing worker، metric/alert و LLM second-opinion پیاده/فعال نشده‌اند.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - bare request معامله نیست؛ confirmation مالک یا declaration صریح طرف
    مقابل در زنجیرهٔ قطعی لازم است؛ هویت گذرا برای هر دو سمت required است؛
  - negotiated reply price فقط نزدیک به آفر و با parse قطعی جایگزین price
    آفر می‌شود؛
  - fillهای معمول مجموعاً از quantity آفر عبور نمی‌کنند؛ aggregate بیش از
    offer حفظ اما `PENDING_REVIEW` است؛
  - projection trade فاقد message/reply/sender/counterparty است و فقط key
    opaque دارد.
- Migration و نتیجهٔ upgrade/downgrade: migration/worker ندارد و بدون call
  هیچ اثر ندارد. rollback برابر عدم فراخوانی linker/projection است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_coin_group_payloads
    tests.test_coin_intelligence_coin_group_trades` با pycache موقت اجرا شد؛
    نتیجه: `Ran 12 tests in 0.136s ... OK`.
  - baseline ترکیبیِ P0 تا P4-A/P2-B/P2-D/P2-C-B4 و guardهای Offer/Trade/
    migration با env ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 219 tests in 4.755s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط متن/هویت bytes synthetic در
  memory و SQLite temporary؛ هیچ chain، نام، ID یا پیام واقعی پردازش نشد.
- نتیجهٔ health/freshness/replay: price توافقی 182900 با تأیید offerer به
  trade تبدیل شد؛ request بدون confirm، parent مبهم، هویت غایب و overfill
  market fact نساختند.
- رفتار rollback آزموده‌شده: aggregate بزرگ‌تر از offer ثبت audit-safe اما
  model-ineligible است؛ final fact هیچ identity خصوصی ندارد.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - قرارداد quality برای declaration یک‌طرفه باید با corpus scrubbed و
    precision report بازبینی شود؛ P2-C validation owner؛
  - transaction orchestrator باید resolution و trade را idempotent و با
    availability واقعی Store کند؛ P2-C-B5 owner؛
  - P4-B فقط پس از B5 می‌تواند group trades را بخواند.
- تصمیم مرحلهٔ بعد و تأیید لازم: P2-C-B5 باید یک orchestrator local، بدون
  collector خودکار، برای read staging → resolve → link → upsert atomic و
  replay-safe بسازد؛ سپس fixture scrubbed برای acceptance لازم است.

### P2-C-B5 — orchestrator محلی staging تا Market Store — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit B5 شامل orchestration pure/caller-driven,
  test و این یادداشت روی `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_group_pipeline.py`: read bounded staging,
    strict unit-safe anchor read، resolve، link و upsert idempotent؛
  - `docs/COIN_INTELLIGENCE_COIN_GROUP_PIPELINE.md` و end-to-end SQLite tests.
- موارد عمداً انجام‌نشده:
  - هیچ collector/scheduler/startup hook، config/runtime path، API، worker یا
    deployment فعال نشده است؛ caller transaction را خودش commit/rollback
    می‌کند.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - فقط anchorهای `ELIGIBLE`, non-conditional, `PROJECT_THOUSAND_TOMAN` و
    integer-exact خوانده می‌شوند؛ conversion یا float truncation ممنوع است؛
  - هر staging replay با event key opaque upsert است؛ fact جدید duplicate
    نمی‌شود؛
  - ریشهٔ چند-offer eligible به linker داده نمی‌شود تا trade به کالای مبهم
    نسبت داده نشود؛
  - بدون anchor کافی offer pending و trade آن absent می‌ماند.
- Migration و نتیجهٔ upgrade/downgrade: migration ندارد و SQLite schemaهای
  P1/B1 را reuse می‌کند. rollback برابر rollback caller transaction است؛
  scheduler/background mutation وجود ندارد.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_coin_group_pipeline`
    با pycache موقت اجرا شد؛ نتیجه: `Ran 3 tests in 0.139s ... OK`.
  - baseline ترکیبیِ P0 تا P4-A/P2-B/P2-D/P2-C-B5 و guardهای Offer/Trade/
    migration با env ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 222 tests in 5.622s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: SQLite موقت و offer/trade/anchor
  کاملاً synthetic؛ هیچ staging، DB یا پیام واقعی باز/تغییر نکرد.
- نتیجهٔ health/freshness/replay: دو anchor prior آفر امام و trade توافقی را
  eligible کرد؛ replay شمار Store را زیاد نکرد؛ anchor غیرinteger هرگز به
  int truncate نشد؛ بدون anchor trade ایجاد نشد.
- رفتار rollback آزموده‌شده: absence/error-safe anchor result pending است و
  writer خودکار/خارج از transaction ندارد؛ attributes نهایی فاقد identity
  خصوصی‌اند.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - production caller باید lock/transaction boundary و retry policy روشن
    داشته باشد؛ P2-C runtime owner؛
  - fixture scrubbed واقعی برای acceptance و precision trade لازم است؛
    P2-C validation owner؛
  - health/lag/retention scheduler و collector production هنوز defer شده‌اند.
- تصمیم مرحلهٔ بعد و تأیید لازم: P2-C functional core اکنون کامل اما inactive
  است. پیش از activation فقط fixture scrubbed، deployment policy و health
  checks باید جدا تصویب شوند؛ سپس P4-B می‌تواند از factهای eligible گروه
  برای anchor/range استفاده کند.

### P4-B-A — engine ساختاری range سکه — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P4-B-A شامل rate engine pure، test و
  این یادداشت روی `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_rate_engine.py`: انتخاب source-separated
    آبشده، intrinsic تاریخ‌پایین، transfer لنگر همان کالا/settlement، IME
    امام نقدی و interval bounded؛
  - `docs/COIN_INTELLIGENCE_COIN_RATE_ENGINE.md` و fixtureهای synthetic.
- موارد عمداً انجام‌نشده:
  - Snapshot publication P4-A هنوز rateها را embed نمی‌کند؛ calendar/ساعت
    بانکی، Herat↔USDT bridge، learned residual/model و P5 selector افزوده
    نشده‌اند.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - تنها conversion این module: `IRT_PER_MESGHAL_750 / 10,000` به واحد
    project و coefficientهای صریح 2.253/2/4/8.130؛
  - CASH و TOMORROW source/lenght/anchor جدا دارند؛ fallback کاغذی visible
    است؛
  - تاریخ‌پایین بدون offer از intrinsic آبشده قابل تولید است؛ premium coin
    بدون لنگر همان کالا abstain می‌کند، جز امام نقدی با IME fresh؛
  - range حداکثر ۲٪ است و فقط spread، سن لنگر و رژیم کاغذی آن را تغییر
    می‌دهند.
- Migration و نتیجهٔ upgrade/downgrade: schema/migration/write ندارد و تنها
  reader pure است؛ rollback برابر عدم فراخوانی engine است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_coin_rate_engine`
    با pycache موقت اجرا شد؛ نتیجه: `Ran 4 tests in 0.084s ... OK`.
  - baseline ترکیبیِ P0 تا P4-A/P2-B/P2-D/P2-C-B5/P4-B-A و guardهای
    Offer/Trade/migration با env ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 226 tests in 6.105s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط SQLite موقت و قیمت synthetic؛
  market DB، Telegram، API یا service واقعی استفاده نشد.
- نتیجهٔ health/freshness/replay: تاریخ‌پایین با آبشدهٔ فیزیکال تازه و بدون
  offer سکه rate گرفت؛ transfer امام با تغییر آبشده حرکت کرد؛ paper fallback
  برچسب خورد؛ quote stale/no-anchor fail-closed شد.
- رفتار rollback آزموده‌شده: سکه premium بدون لنگر `NO_DATA` می‌دهد، نه حباب
  ثابت؛ range bounded است و قیمت اعشاری/واحد دیگر به‌طور ضمنی convert
  نمی‌شود.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - P4-B-B باید engine output را در snapshot atomic publish کند و canonical
    completeness/version را enforce کند؛
  - source-policy آبشده public/private باید با replay scrubbed واقعی بررسی
    شود؛
  - رفتار نقدی بعد از ساعت بانکی، تعطیلی و bridge هرات هنوز deferred است.
- تصمیم مرحلهٔ بعد و تأیید لازم: P4-B-B snapshot integration و test
  no-leakage لازم است؛ سپس P5 فقط snapshot published را می‌خواند.

### P4-B-B — انتشار atomic rate در Snapshot — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P4-B-B شامل integration Snapshot،
  validation و test روی `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `market_snapshot.py` اکنون output P4-B-A را زیر `rates` همراه engine
    version و شمار estimated/no-data می‌سازد؛
  - validator interval و complete matrix همهٔ ۷ کالا × CASH/TOMORROW را پیش
    از publish atomic enforce می‌کند؛
  - test low-date range در Snapshot افزوده شد.
- موارد عمداً انجام‌نشده:
  - publisher schedule/runtime root و consumer P5 اضافه نشده‌اند؛ build فقط
    با call صریح قبلی کار می‌کند.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - `rates` دیگر map خالی نیست و شامل `engine_version`, `items`,
    `estimated_count`, `no_data_count` است؛
  - هر سلول فقط `ESTIMATED` با lower≤center≤upper integer یا `NO_DATA` با
    هیچ قیمت است؛ duplicate/missing/code/settlement نامعتبر artifact را رد
    می‌کند؛
  - Snapshot قدیمی P4-A با `rates={}` فقط برای read compatibility معتبر است
    ولی P5 بعدی آن را rate-ready نمی‌داند.
- Migration و نتیجهٔ upgrade/downgrade: schema Store و migration ندارد؛
  snapshot invalid فایل قبلی را replace نمی‌کند و rollback همان artifact
  معتبر قبلی است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_market_snapshot
    tests.test_coin_intelligence_coin_rate_engine` با pycache موقت اجرا شد؛
    نتیجه: `Ran 8 tests in 0.174s ... OK`.
  - baseline ترکیبیِ P0 تا P4-B-B و guardهای Offer/Trade/migration با env
    ساختگی و pycache موقت اجرا شد؛ نتیجه:
    `Ran 227 tests in 5.625s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: تنها SQLite TemporaryDirectory و
  قیمت synthetic؛ هیچ artifact/DB/خدمت واقعی mutate نشد.
- نتیجهٔ health/freshness/replay: physical melted تازه، low-date CASH را در
  Snapshot بدون offer سکه estimated کرد؛ validator unit/range/policy را
  پیش از publish کنترل می‌کند.
- رفتار rollback آزموده‌شده: malformed rate matrix publish نمی‌شود و
  provider فایل معتبر قبلی را همچنان می‌خواند.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - freshness/health runtime و writer ownership هنوز P4 deployment owner؛
  - P5 باید rate-ready snapshot را جدا از P4-A legacy لازم بداند؛
  - calendar, banking session, Herat bridge و learned model substageهای
    بعدی P4 باقی مانده‌اند.
- تصمیم مرحلهٔ بعد و تأیید لازم: P5-A reader/ranker باید فقط Snapshot
  atomically published و rate-ready را بخواند و commodity result را با
  catalog canonical name (نه ID) برگرداند.

### P2-D — adapter external تتر و IME — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P2-D شامل adapter unit-safe، Snapshot
  extension، test و این یادداشت روی
  `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/external_markets.py`: input transient و
    projection canonical برای `USDT_IRT`, `IME_GOLD_BAR` و
    `IME_GOLD_COIN_IMAM`؛
  - Snapshot source-separated برای دو instrument رسمی IME؛
  - `docs/COIN_INTELLIGENCE_EXTERNAL_MARKETS_ADAPTER.md` و testهای offline.
- موارد عمداً انجام‌نشده:
  - هیچ client/HTTP request، API key، session، collector، retry loop،
    historical backfill، collector ایران یا sync سه‌سروره فعال نشد؛
  - P2-D تا deployment transport و health/freshness واقعی `PARTIAL` است.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - تترِ ورودی تومان فقط با conversion صریح `×10` به
    `IRT_PER_USDT` می‌رسد و جای هرات نام‌گذاری نمی‌شود؛
  - IME certificate `0.1g/995` با تبدیل وزن و عیار صریح به
    `IRT_PER_MESGHAL_750` می‌رسد؛ IME امامِ `IRR_PER_COIN` identity است؛
  - event time و available time هر دو required و ترتیب معکوس fail-closed
    است؛ quote kind نامعتبر یا price غیرمثبت هم reject می‌شود.
- Migration و نتیجهٔ upgrade/downgrade: migration جدیدی ندارد و فقط fact
  canonical P1 را می‌سازد. rollback برابر عدم فراخوانی library است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_external_markets
    tests.test_coin_intelligence_market_snapshot` با pycache موقت اجرا شد؛
    نتیجه: `Ran 8 tests ... OK`.
  - suite ترکیبی P1 تا P4-A، P2-B/D و baseline آفر/معامله/API/migration با
    environment ساختگی اجرا شد؛ نتیجه: `Ran 191 tests ... OK`. logهای
    endpointهای ساختگی expected بودند و هیچ service واقعی استفاده نشد.
- داده/fixture استفاده‌شده و محل امن آن: فقط input synthetic در test process
  و SQLite موقت؛ network، endpoint، raw response، credential یا بازار واقعی
  استفاده نشد.
- نتیجهٔ health/freshness/replay:
  - Snapshot شمش IME و سکه IME را مستقل و با unit متفاوت نشان می‌دهد؛
  - dedupe key opaque است و health/provider runtime عمداً deferred است.
- رفتار rollback آزموده‌شده: conversion یا timestamp نامعتبر پیش از write
  fail-closed است؛ هیچ startup/write خودکار ندارد.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - صحت endpoint و fieldهای live IME/Tether، نرخ/تاخیر و تاریخچه باید در
    deployment/replay جدا بررسی شود؛ owner P2-D deployment؛
  - P2-C گروه‌های سکه برای anchorهای خارجی هنوز لازم است.
- تصمیم مرحلهٔ بعد و تأیید لازم: P2-C parser گروه‌های سکه یا P4-B producer
  فقط پس از داشتن fixture و policy صریح raw-retention شروع شود.

### P3 — Outbox پایدار Offer/Trade پروژه — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): commit P3 شامل model، migration، listener،
  test و این یادداشت روی `candidate/coin-commodity-inference-promotion`.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `models/coin_intelligence_market_outbox.py`: outbox product-owned با
    idempotency، status، lease و payload privacy-minimized؛
  - `core/market_intelligence/project_outbox.py`: listener transaction-local
    برای lifecycle Offer/Trade؛
  - `migrations/versions/b2d4e6f8a0c2_add_coin_intelligence_market_outbox.py`:
    migration مستقل از head فعلی main؛
  - `tests/test_coin_intelligence_project_outbox.py`: transaction lifecycle و
    rollback tests؛
  - `docs/COIN_INTELLIGENCE_PROJECT_OUTBOX.md`: contract، deployment order و
    rollback policy.
- موارد عمداً انجام‌نشده:
  - هیچ worker، schedule، SQLite write، Snapshot rebuild، network call یا
    inference بعد از commit فعال نشد؛
  - هیچ parser متنی یا دادهٔ گروه/کانال در مسیر eventهای native پروژه نیست؛
  - هیچ sync/runtime سه‌سروره اضافه یا تغییر نکرد.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - table `coin_intelligence_market_outbox` eventهای `OFFER_OPENED`،
    `OFFER_PARTIAL`، `OFFER_COMPLETED`، `OFFER_CANCELLED`، `OFFER_EXPIRED` و
    `TRADE_COMPLETED` را نگه می‌دارد؛
  - idempotency key از subject/event/version تولید می‌شود؛
  - payload فقط fact اقتصادی normalized دارد؛ identity، mobile، notes و raw
    text در آن نیست؛
  - آفر `exclude_from_competitive_price` نگه‌داری می‌شود، ولی `model_eligible`
    آن false است؛
  - listener در همان session/transaction row را اضافه می‌کند؛ rollback هم
    Offer/Trade و هم outbox event را برمی‌گرداند.
- Migration و نتیجهٔ upgrade/downgrade:
  - migration PostgreSQL جدید additive و مستقل از migrationهای به‌جاماندهٔ
    معماری پیشین است؛ Offer/Trade schema تغییر نکرد؛
  - downgrade fail-closed است: تا outbox drain/archive نشده باشد table حذف
    نمی‌شود؛
  - test compile و Alembic head روی graph جدید سبز است. Migration واقعی روی
    دیتابیس production اجرا نشده است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -v tests.test_coin_intelligence_project_outbox
    tests.test_coin_intelligence_market_store
    tests.test_coin_intelligence_public_telegram tests.test_migration_smoke`
    → `Ran 33 tests ... OK`؛
  - baseline کامل P0 تا P3 با env ساختگیِ process-local اجرا شد →
    `Ran 170 tests in 4.686s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن:
  - فقط three-table SQLite in-memory برای Offer/Trade/outbox و fixtureهای
    synthetic؛ هیچ PostgreSQL، آفر واقعی، identity، API key یا session واقعی
    استفاده نشد.
- نتیجهٔ health/freshness/replay:
  - همان optimistic version همان event key را duplicate نمی‌کند؛ partial و
    terminal lifecycle جدا هستند؛
  - health/lease consumer خارج از scope فعلی است، چون consumer هنوز شروع
    نشده است.
- رفتار rollback آزموده‌شده:
  - flush و سپس rollback نه Offer و نه outbox event بر جا نمی‌گذارد؛
  - downgrade migration اگر حتی یک row موجود باشد fail-closed است.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - app code باید فقط بعد از migration P3 deploy شود؛
  - claim/lease/retry worker و projection outbox به SQLite در P4 مالک دارد؛
  - هر mutation path که ORM flush را bypass کند باید در P3 acceptance audit
    بررسی شود؛
  - P2-B/C/D هنوز برای کامل شدن feedهای خارجی باقی است.
- تصمیم مرحلهٔ بعد و تأیید لازم:
  - P3 producer کامل است اما تا consumer idempotent P4 به `PARTIAL` می‌ماند.
    گام بعد P4: projection Outbox به Market Store و Snapshot/bundle local-first
    بدون تغییر API/Bot فعلی.

### P5-A — Ranker محصولیِ کالا از Snapshot منتشرشده — 2026-08-04 — COMPLETE (library only)

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): این commit شامل ranker، test و مستند قرارداد روی
  `candidate/coin-commodity-inference-promotion` است.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_inference.py`: reader/ranker فقط-خواندنی
    که دقیقاً یک Snapshot اتمیک و rate-ready را می‌گیرد و برای یک قیمت و
    settlement، گزینه‌های کالای سکه را رتبه‌بندی می‌کند؛
  - `docs/COIN_INTELLIGENCE_PRODUCT_RANKER.md`: قرارداد مرز محصول و قانون
    canonical-name-only؛
  - `tests/test_coin_intelligence_coin_inference.py`: آزمون انتخاب یکتا،
    overlap، confidence پایین و stale/outside range.
- موارد عمداً انجام‌نشده:
  - هیچ API، بات، WebApp، مسیر `OfferCreate`، database lookup، feature flag
    runtime، worker، collector یا network call تغییر نکرده یا فعال نشده است؛
  - هیچ `commodity_id`، alias، متن خام، هویت، آفر یا معامله به ranker وارد
    نمی‌شود؛
  - audit append-only و exact catalog mapping عمداً P5-B/P6 باقی می‌ماند.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - `coin-inference-v1` فقط `commodity_code` و نام canonical (`امام`، `بهار`
    و ...) را برمی‌گرداند، هرگز شناسهٔ PostgreSQL را نه؛
  - `AUTO_SELECT` فقط برای یک range یکتای HIGH/MEDIUM، Snapshot معتبر و تازه
    ممکن است؛ چند range یا LOW paper fallback به `CONFIRM` می‌رود؛
  - Snapshot ناموجود/خراب/legacy، زمان آینده/کهنه یا قیمت بیرون بازه به
    `ABSTAIN` می‌رسد؛ default پنهان امام وجود ندارد؛
  - receipt از SHA-256 canonical Snapshot و generated timestamp برگردانده
    می‌شود تا P6 بتواند هنگام submit همان snapshot/freshness را دوباره
    کنترل کند.
- Migration و نتیجهٔ upgrade/downgrade: schema و migration ندارد؛ rollback
  برابر حذف caller این library است و هیچ داده‌ای نوشته نشده است.
- Test commands و نتیجهٔ دقیق:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -q
    tests.test_coin_intelligence_coin_inference` → `Ran 4 tests ... OK`؛
  - regression ترکیبی P0 تا P5-A و guardهای Offer/Trade/migration با env
    ساختگی و pycache موقت → `Ran 231 tests in 5.034s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط Snapshot synthetic و SQLite
  موقت در test process؛ هیچ دادهٔ بازار، credential، API یا service واقعی
  استفاده یا mutate نشد.
- نتیجهٔ health/freshness/replay: ranker فقط artifact اتمیک را یک بار load
  می‌کند؛ age منفی یا بیش از policy fail-closed است و receipt به artifact
  دقیق تصمیم متصل می‌ماند.
- رفتار rollback آزموده‌شده: malformed یا stale Snapshot و قیمت خارج از range
  abstain می‌کند؛ overlap هرگز به کالای پیش‌فرض یا انتخاب خودکار تبدیل
  نمی‌شود.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - P5-B باید نام canonical را با `commodities.name` محلی، exact و یکتا به
    ID همان site نگاشت کند؛ صفر یا چند match باید abstain باشد؛
  - P6 باید receipt/freshness را در submit دوباره validate، تصمیم را minimal
    audit و مسیرهای bot/web را با feature flag shadow-first متصل کند؛
  - کیفیت rate/interval و calendar/Herat bridge همچنان مالک مراحل P4
    research/deployment است و ranker آن‌ها را جبران نمی‌کند.
- تصمیم مرحلهٔ بعد و تأیید لازم: P5-B mapping محلی و audit contract فقط پس
  از بررسی دقیق مدل `Commodity` و mutation pathهای موجود انجام شود؛ تا آن
  زمان هیچ کاربر یا آفر پروژه از این ranker استفاده نمی‌کند.

### P5-B — نگاشت fail-closed catalog محلی — 2026-08-04 — COMPLETE (library only)

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): این commit شامل mapper، test و مستند مرز
  catalog روی `candidate/coin-commodity-inference-promotion` است.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `core/market_intelligence/coin_catalog.py`: projection جدا از ranker که
    candidateهای P5-A را فقط با catalog محلی resolve می‌کند؛
  - `docs/COIN_INTELLIGENCE_CATALOG_MAPPING.md`: قرارداد exact-name و
    ممنوعیت alias/fuzzy/default؛
  - `tests/test_coin_intelligence_coin_catalog.py`: guardهای catalog.
- موارد عمداً انجام‌نشده:
  - API، بات، WebApp، `OfferCreate`، parser، scheduler، feature flag، audit
    persistence و هرگونه write به database تغییر نکرده یا فعال نشده است؛
  - mapper هرگز کالا/alias جدید نمی‌سازد و به هیچ دادهٔ market دسترسی ندارد.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - `coin-catalog-resolution-v1` تنها query مجاز
    `commodities.name == canonical_name` را انجام می‌دهد؛ equality دقیق است؛
  - `commodity_aliases`، case/fuzzy normalization، order catalog و fallback
    امام همگی ممنوع‌اند؛
  - همهٔ candidateهای `AUTO_SELECT` و `CONFIRM` باید دقیقاً یک row معتبر با
    ID مثبت بگیرند؛ صفر/چند/نام نابرابر = کل تصمیم
    `ABSTAIN / CATALOG_CANONICAL_NAME_UNAVAILABLE`؛
  - ranker از پیش abstain‌شده اصلاً catalog را query نمی‌کند و reason اصلی
    خود را حفظ می‌کند.
- Migration و نتیجهٔ upgrade/downgrade: migration/schema/write ندارد؛ mapper
  library محلی و read-only است، پس rollback برابر عدم فراخوانی آن است.
- Test commands و نتیجهٔ دقیق:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -q
    tests.test_coin_intelligence_coin_catalog` → `Ran 4 tests ... OK`؛
  - regression کامل P0 تا P5-B و guardهای Offer/Trade/migration با env
    ساختگی و pycache موقت → `Ran 235 tests in 5.765s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط commodityهای synthetic با
  IDهای ساختگی در memory؛ دیتابیس/alias/کاربر/آفر واقعی خوانده یا mutate نشد.
- نتیجهٔ health/freshness/replay: catalog mapping هیچ freshness را تمدید یا
  receipt را تغییر نمی‌دهد؛ timestamp و receipt P5-A عیناً carry می‌شوند تا
  submit-time validation در P6 ممکن بماند.
- رفتار rollback آزموده‌شده: نام alias‌مانند یا catalog فرضاً duplicate به
  جای انتخاب اشتباه abstain می‌دهد؛ `CONFIRM` نیمه‌قابل‌نمایش ایجاد نمی‌شود.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - uniqueness database فعلی از duplicate جلوگیری می‌کند، اما guard mapper
    برای importهای معیوب و test double عمداً باقی می‌ماند؛
  - P5-C/P6 باید audit append-only، receipt replay و submit-time freshness
    را بدون ذخیرهٔ متن یا identity اضافه کند؛
  - P6 باید فقط بعد از confirmation کاربر `commodity_id` را به command
    موجود بدهد و semantic idempotency را حفظ کند.
- تصمیم مرحلهٔ بعد و تأیید لازم: پیش از اتصال HTTP، contract تصمیم/audit و
  policy shadow-first باید جدا طراحی و تست شود؛ تا آن زمان mapper هیچ اثر
  کاربرمحور یا عملیاتی ندارد.

### P5-C — audit append-only تصمیم inference — 2026-08-04 — PARTIAL (shadow preview only)

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): این commit شامل مدل، migration، writer library،
  test و مستند audit روی `candidate/coin-commodity-inference-promotion` است.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `models/coin_intelligence_inference_audit.py` و migration
    `d3f7a1c9e4b5`: table تصمیم‌های inference با check constraint و trigger
    PostgreSQL برای منع UPDATE/DELETE؛
  - `core/market_intelligence/coin_inference_audit.py`: writer صریح و
    idempotent که transaction/commit را به caller واگذار می‌کند؛
  - `docs/COIN_INTELLIGENCE_INFERENCE_AUDIT.md` و testهای contract/storage.
- موارد عمداً انجام‌نشده:
  - فقط `P6-A` و فقط با feature flag روشن writer را برای preview فراخوانی
    می‌کند؛ بات، WebApp، `OfferCreate`، parser، worker و مسیر submit هنوز آن
    را call نمی‌کنند؛ migration روی دیتابیس واقعی اجرا نشده است؛
  - audit به Offer/Trade/user/message/text/note/Telegram ID متصل نیست و
    جایگزین audit اصلی محصول نمی‌شود.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - فقط decision key opaque، source surface، قیمت پروژه، settlement، status،
    reason-code، count، کالای canonical منتخب در AUTO، نسخه‌های inference/
    catalog و receipt/timestamp snapshot ذخیره می‌شوند؛
  - نام/شناسهٔ کاربر، متن خام، note، chat/channel/message/Telegram ID، mobile
    و reference آفر/معامله column ندارند؛
  - key hex-64 exact replay را برمی‌گرداند؛ reuse همان key با اقتصاد/نتیجه
    متفاوت conflict می‌دهد؛
  - AUTO دقیقاً یک candidate و کالا دارد؛ CONFIRM/ABSTAIN هیچ کالای پنهان
    selected ندارند؛ reason آزاد یا candidate با code/name غیرcanonical پیش
    از write reject می‌شود؛
  - migration downgrade اگر row وجود داشته باشد fail-closed است.
- Migration و نتیجهٔ upgrade/downgrade:
  - revision `d3f7a1c9e4b5` بعد از P3 head است و `alembic heads` همان یک head
    را گزارش کرد؛ migration production اجرا نشده است؛
  - trigger PostgreSQL هر UPDATE/DELETE را reject می‌کند؛ downgrade فقط پس از
    archive/drain جدول ممکن است.
- Test commands و نتیجهٔ دقیق:
  - `python3 -m unittest -q tests.test_coin_intelligence_inference_audit
    tests.test_migration_smoke` → `Ran 15 tests ... OK`؛
  - regression کامل P0 تا P5-C و guardهای Offer/Trade/migration با env
    ساختگی و pycache موقت → `Ran 241 tests in 5.773s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط ID، key، snapshot و قیمت
  synthetic و SQLite in-memory؛ database/service/credential/data واقعی
  استفاده یا mutate نشد.
- نتیجهٔ health/freshness/replay: writer freshness را نمی‌سازد و receipt را
  تغییر نمی‌دهد؛ AUTO/CONFIRM بدون provenance snapshot reject می‌شوند و
  ABSTAIN علت fail-closed را بدون query یا انتخاب کالا نگه می‌دارد.
- رفتار rollback آزموده‌شده: SQLite schema شکل AUTO ناقص را reject کرد؛
  migration PostgreSQL downgrade با هر row موجود fail-closed تعریف شده است.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - اجرای واقعی upgrade/trigger و race یکتایی باید در PostgreSQL scratch gate
    پیش از deployment آزمایش شود؛ owner P6/deployment؛
  - P6 باید audit را داخل همان transaction preview/submit و با handling
    `IntegrityError` فراخواند؛
  - برای confirmation کاربر یک event تصمیم دوم یا receipt submit باید در P6
    طراحی شود؛ row پیشنهادی نباید به‌تنهایی acceptance کاربر تلقی شود.
- تصمیم مرحلهٔ بعد و تأیید لازم: اتصال P6 فقط shadow-first و با submit-time
  recomputation/receipt validation شروع شود؛ پیش از آن migration P5-C نباید
  روی production اجرا شود.

### P6-A — API پیش‌نمایش shadow و feature flag — 2026-08-04 — PARTIAL

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): این commit شامل endpoint preview، config، test
  و مستند API روی `candidate/coin-commodity-inference-promotion` است.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `POST /api/offers/inference-preview` با input محدود به `price` در واحد
    پروژه و `cash|tomorrow`؛
  - دو setting خاموشِ پیش‌فرض
    `COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED=false` و
    `COIN_INTELLIGENCE_INFERENCE_SNAPSHOT_PATH`؛
  - این endpoint یک Snapshot local atomic را با P5-A/B می‌خواند، سپس P5-C
    را پیش از پاسخ append می‌کند؛
  - `docs/COIN_INTELLIGENCE_SHADOW_PREVIEW_API.md` و test API مستقیم.
- موارد عمداً انجام‌نشده:
  - `POST /api/offers/`، `OfferCreate`، parser بات، parser متن WebApp، UI،
    worker، collector، Snapshot publisher، Telegram و sync سه‌سروره تغییر
    نکرده‌اند؛
  - پیش‌نمایش هرگز Offer ایجاد/ویرایش/لغو یا commodity پیش‌فرض تعیین نمی‌کند؛
  - migration P5-C یا flag روی runtime/production فعال نشده است.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - endpoint authenticated است، key opaque server-generated برمی‌گرداند و
    فقط result `AUTO_SELECT|CONFIRM|ABSTAIN` و candidateهای catalog-resolved
    را نشان می‌دهد؛
  - flag خاموش `404`، نبود path `503`، و خطای catalog/audit `503` با rollback
    می‌دهد؛ هیچ خطا به امام fallback نمی‌شود؛
  - `AUTO_SELECT` پاسخ shadow است نه اجازهٔ ثبت آفر؛ submit-time recompute و
    confirmation هنوز لازم است.
- Migration و نتیجهٔ upgrade/downgrade: migration جدیدی در P6-A ندارد؛ این
  endpoint تا اجرای کنترل‌شدهٔ migration P5-C در محیط target فقط fail-closed
  خواهد بود.
- Test commands و نتیجهٔ دقیق:
  - command محیط ساختگی با
    `python3 -m unittest -q tests.test_coin_intelligence_preview_api` →
    `Ran 4 tests ... OK`؛
  - regression کامل P0 تا P6-A و guardهای Offer/Trade/migration با env
    ساختگی و pycache موقت → `Ran 245 tests in 5.398s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: فقط Snapshot/catalog/audit synthetic
  و fake DB؛ endpoint واقعی، user واقعی، snapshot واقعی یا service بیرونی
  استفاده یا mutate نشد.
- نتیجهٔ health/freshness/replay: P5-A age/receipt را validate می‌کند؛ P6-A
  آن‌ها را فقط carry/audit می‌کند. هر استثنا rollback می‌شود و پاسخ unavailable
  می‌گیرد، نه دادهٔ stale یا انتخاب پنهان.
- رفتار rollback آزموده‌شده: failure catalog/audit یک commit صفر و rollback
  واحد دارد؛ ABSTAIN معتبر به‌عنوان پاسخ سالم بدون candidate برمی‌گردد.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - endpoint تنها پس از publisher/permission deployment و migration P5-C
    می‌تواند برای shadow telemetry فعال شود؛ owner deployment؛
  - P6-B باید parserهای بات/WebApp و P6-C باید submit-time receipt/recompute,
    confirmation و idempotency fingerprint را بدون broadening بی‌اجازه اضافه
    کنند؛
  - replay/race واقعی audit باید در PostgreSQL scratch gate سنجیده شود.
- تصمیم مرحلهٔ بعد و تأیید لازم: تا زمان عبور از P6-B/C و E2E، preview فقط
  ابزار مشاهده است و هدف اولیهٔ «ثبت آفر بدون نام کالا» هنوز فعال نشده است.

### P4-C — publisher صریح Snapshot سایه — 2026-08-04 — COMPLETE (library only)

- Base/main commit: `540b2c0c933406368866ffce17a58f5124bfbef8`.
- Promotion branch commit(s): این commit شامل publisher، read-only Store
  guard، test و مستند runtime boundary روی
  `candidate/coin-commodity-inference-promotion` است.
- Scope انجام‌شده و فایل‌های تغییرکرده:
  - `connect_market_store_read_only()` و verification بدون upgrade در
    `market_store.py`؛
  - `snapshot_publisher.py` با `publish_rate_ready_snapshot()`؛
  - `docs/COIN_INTELLIGENCE_SNAPSHOT_PUBLISHER.md` و testهای artifact.
- موارد عمداً انجام‌نشده:
  - هیچ scheduler، worker، cron، lifespan، collector، Telegram/API client،
    setting runtime، volume path، health endpoint یا deployment فعال نشده
    است؛
  - publisher هیچ row در SQLite یا PostgreSQL نمی‌نویسد و outbox را consume
    نمی‌کند؛ caller آینده باید آن مرزها را جداگانه مالک شود.
- قرارداد/schema/versionهای افزوده یا تغییرکرده:
  - Store باید file موجود، schema و contract version دقیق داشته باشد؛ v1 یا
    Store ناقص به‌جای migration خودکار reject می‌شود؛
  - مسیر Store و target Snapshot نباید یکی باشند؛ publisher هرگز Store غایب
    را ایجاد نمی‌کند؛
  - فقط Snapshot با حداقل یک نرخ canonical `ESTIMATED` atomically replace
    می‌شود؛ `NOT_RATE_READY` با صفر نرخ، artifact سالم قبلی را دست‌نخورده
    نگه می‌دارد؛
  - نتیجهٔ publisher فقط status، digest، generated time و countهای rate است
    و دادهٔ خام/identity ندارد.
- Migration و نتیجهٔ upgrade/downgrade: migration/schema جدید ندارد؛ helper
  read-only صراحتاً migration را انجام نمی‌دهد. rollback برابر عدم فراخوانی
  publisher است و Snapshot قبلی پابرجا می‌ماند.
- Test commands و نتیجهٔ دقیق:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -q
    tests.test_coin_intelligence_snapshot_publisher
    tests.test_coin_intelligence_market_store
    tests.test_coin_intelligence_market_snapshot` →
    `Ran 14 tests ... OK`؛
  - regression کامل P0 تا P6-A/P4-C و guardهای Offer/Trade/migration با env
    ساختگی و pycache موقت → `Ran 248 tests in 6.006s ... OK`.
- داده/fixture استفاده‌شده و محل امن آن: SQLite و Snapshot موقت با قیمت
  synthetic؛ هیچ Store/live credential/API یا فایل volume واقعی استفاده یا
  mutate نشد.
- نتیجهٔ health/freshness/replay: builder نقطه‌زمانی و atomic writer موجود
  را حفظ می‌کند؛ publisher صرفاً جلوی replace ناشی از evidence تهی را اضافه
  می‌کند. snapshot stale بعداً در P5 همچنان abstain می‌شود.
- رفتار rollback آزموده‌شده: Store غایب artifact/SQLite جدید نمی‌سازد؛ Store
  خالی `NOT_RATE_READY` می‌دهد و Snapshot منتشرشدهٔ قبل قابل خواندن می‌ماند.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - single-writer lock، زمان‌بندی، path/permission volume، health sidecar و
    alert باید در deployment stage مشخص شوند؛
  - publisher بدون ingestion/outbox consumer دادهٔ جدید تولید نمی‌کند؛
  - P6-B parser shadow فقط پس از تعیین همین runtime ownership باید به
    artifact متصل شود.
- تصمیم مرحلهٔ بعد و تأیید لازم: PostgreSQL scratch gate برای migration
  P5-C و سپس runtime planِ بدون معماری سه‌سروره لازم است؛ هیچ activation
  خودکاری از این commit مجاز نیست.

### P4-D — فرمان دستیِ محافظت‌شده برای publish/check Snapshot — 2026-08-04 — COMPLETE (manual only)

- Scope انجام‌شده:
  - `scripts/publish_coin_intelligence_snapshot.py` یک فرمان صریح با دو
    حالت `publish` و `check` اضافه می‌کند؛ هیچ scheduler، cron، worker،
    collector، API route یا feature flag جدیدی ثبت نمی‌کند.
  - هر دو path باید زیر یک `runtime-root` موجود باشند. Store غایب، path
    خارج از root، parent غایب Snapshot و root غایب پیش از هر write رد
    می‌شوند؛ publisher همچنان Store را فقط read-only باز می‌کند.
  - publish با lock غیرمسدودکنندهٔ کنار artifact انجام می‌شود. خروجی صرفاً
    JSON privacy-safe شامل status/freshness/count/digest است؛ متن پیام،
    شناسهٔ کاربر، مسیر و credential چاپ نمی‌شوند.
  - check همان artifact اتمیک را بدون write می‌خواند و با سقف پیش‌فرض ۱۲۰
    ثانیه، `FRESH`، `STALE` یا `UNAVAILABLE` را اعلام می‌کند.
- Test command و نتیجه:
  - `PYTHONPYCACHEPREFIX=/tmp/coin-intelligence-pycache python3 -m unittest -q
    tests.test_publish_coin_intelligence_snapshot
    tests.test_coin_intelligence_snapshot_publisher
    tests.test_coin_intelligence_market_snapshot
    tests.test_coin_intelligence_market_store` → `Ran 18 tests ... OK`.
- مرز عملیاتی و rollback:
  - این فرمان در staging/production خودکار اجرا نشده و هیچ volume/compose
    mount یا setting runtime تغییر نکرده است؛ rollback کد یعنی عدم فراخوانی
    فرمان و artifact قبلی به‌دلیل atomic publish باقی می‌ماند.
  - برای staging باید root محافظت‌شده به‌طور مشترک برای writer/publisher
    (write) و API/Bot (read-only) mount شود، مالک تک‌نویسنده و monitor خروجی
    JSON تعیین شود. این‌ها release gate هستند، نه بخشی از این commit.

### P5-D — guard اجرای migration روی PostgreSQL scratch — 2026-08-04 — COMPLETE

- Scope انجام‌شده:
  - `scripts/run_guarded_scratch_alembic.py` فقط namespace محدود
    `coin_intelligence_[a-z0-9_]+` را علاوه بر namespaceهای قبلی برای target
    scratch می‌پذیرد؛ runtime databaseها همچنان صراحتاً deny هستند؛
  - `tests/test_guarded_scratch_alembic.py` پذیرش target نمونهٔ
    `coin_intelligence_audit_test` را کنترل می‌کند.
- قرارداد ایمنی:
  - `TRADING_BOT_MIGRATION_MODE=scratch`، URL sync/app یکسان، checkout دقیق
    و یک Alembic head همچنان اجباری‌اند؛
  - نام‌هایی مانند `trading_bot` یا `trading_bot_db` حتی با این تغییر قابل
    قبول نیستند؛ namespace جدید فقط اجازهٔ آزمایش isolated را می‌دهد.
- Test command و نتیجه:
  - `python3 -m unittest -q tests.test_coin_intelligence_inference_audit
    tests.test_guarded_scratch_alembic tests.test_migration_smoke` → `Ran 37
    tests ... OK`.
- وضعیت اجرای واقعی:
  - روی یک PostgreSQL disposable محلی با نام
    `coin_intelligence_audit_20260804_p6c`، `upgrade head` واقعی اجرا شد؛
    سپس insert یک audit، rejection واقعی `UPDATE` توسط trigger append-only
    و rejection واقعی `downgrade base` با وجود audit آزموده شد؛ همه سبز بودند.
  - پیش از اجرای نهایی، این gate یک نام CheckConstraint با طول بیش از حد
    PostgreSQL را کشف کرد. نام در model و migration به
    `ck_coin_infer_audit_selected_commodity_positive` کوتاه شد و test
    دائمی سقف ۶۳ کاراکتر برای نام‌های schema مدل اضافه شد.
  - container و database آزمایشی در پایان هر اجرا خودکار حذف شدند؛ production،
    main و runtime هیچ تغییری نکردند.

### P6-B — metadata سایه در parser Web — 2026-08-04 — PARTIAL

- Scope انجام‌شده:
  - `ParsedOffer` می‌تواند match kind را گزارش کند: `EXPLICIT`,
    `IMPLICIT_DEFAULT` یا `UNRESOLVED`؛ رفتار پیش‌فرض parser و default امام
    برای callerهای قدیمی بدون تغییر است؛
  - فقط وقتی flag preview روشن است، `/api/offers/parse` برای
    `IMPLICIT_DEFAULT` همان Snapshot/ranker/catalog/audit را به‌صورت shadow
    اجرا و `commodity_inference` را به پاسخ اضافه می‌کند؛
  - کالا/شناسهٔ اصلی response عمداً همان Imam legacy می‌ماند؛ metadata شامل
    `mode=SHADOW_ONLY`، status، receipt، reason و candidateهای catalog است.
- موارد عمداً انجام‌نشده:
  - `OfferCreate`، submit، idempotency آفر، بات، WebApp UI، کالای explicit و
    هر انتخاب/confirm کاربر تغییر نکرده‌اند؛
  - flag خاموش response فعلی `/parse` را byte-for-byte از نظر فیلدها حفظ
    می‌کند؛ snapshot path غایب نیز فقط ABSTAIN shadow می‌دهد.
- Test command و نتیجه:
  - test parser، router-read و preview با env ساختگی →
    `Ran 43 tests ... OK`.
  - regression کامل P0 تا P6-B/P4-C/P5-D و guardهای Offer/Trade/migration →
    `Ran 271 tests in 5.384s ... OK`.
- ریسک و گیت بعدی:
  - WebApp باید metadata را بدون auto-select نمایش دهد؛ بات نیز باید همین
    contract را در preview خودش مصرف کند؛
  - پس از replay/telemetry کافی، فقط P6-C می‌تواند receipt تازه و انتخاب
    explicit کاربر را در submit به commodity نهایی تبدیل کند.

### P6-B1 — نمایش metadata سایه در WebApp — 2026-08-04 — COMPLETE

- Scope انجام‌شده:
  - `OfferPreviewModal` در پاسخ parse فقط وقتی `commodity_inference.mode`
    برابر `SHADOW_ONLY` باشد، بخش «تشخیص آزمایشی کالا» را نمایش می‌دهد؛
  - نتیجهٔ `AUTO_SELECT` فقط نام candidate مدل را در کنار کالای فعلی parser
    نشان می‌دهد؛ `CONFIRM` و `ABSTAIN` نیز صریحاً عدم انتخاب خودکار را
    اعلام می‌کنند؛
  - متن UI می‌گوید «در ثبت آفر اثری ندارد» و هیچ control برای پذیرش یا
    انتخاب candidate ندارد.
- مرز قطعی و رفتار ایمنی:
  - `buildOfferCreatePayload` تغییر نکرده است؛ بنابراین `commodity_id` و
    `commodity_name` ارسالی همان نتیجهٔ legacy parser باقی می‌ماند، حتی اگر
    candidate سایه کالای دیگری باشد؛
  - receipt/key/candidateهای metadata به payload ثبت آفر یا متن آفر اضافه
    نمی‌شوند؛ این مرحله فقط مشاهده‌پذیری کاربر را اضافه می‌کند؛
  - پاسخ parse بدون metadata و همهٔ مسیرهای عادی پیش‌نمایش دقیقاً UI پیشین
    را دارند.
- Test command و نتیجه:
  - `MarketView.test.ts` با پاسخ `AUTO_SELECT` متناقض با کالای legacy اجرا
    شد و اثبات می‌کند UI هر دو را نمایش می‌دهد، اما POST نهایی همچنان همان
    `commodity_id` legacy را می‌فرستد؛ `34 tests ... OK`؛
  - production build و `vue-tsc --noEmit` در sandbox موقت و با dependency
    موجود اجرا شدند و موفق بودند؛ وابستگی جدیدی نصب نشد.
- گیت مرحلهٔ بعد:
  - این تغییر هنوز به معنی فعال بودن flag یا publisher/collector نیست؛
    snapshot واقعی، migration audit در PostgreSQL scratch، و telemetry
    shadow باید پیش از P6-C فراهم شوند؛
  - P6-C تنها پس از بازبینی دادهٔ سایه می‌تواند confirmation صریح کاربر،
    recompute در submit-time و receipt binding را طراحی کند. تا آن زمان
    هیچ انتخاب خودکار کالا مجاز نیست.

### P6-B2 — قرارداد مشترک shadow برای Web و بات — 2026-08-04 — COMPLETE (inactive by default)

- Scope انجام‌شده:
  - `core/market_intelligence/coin_inference_shadow.py` مالک مشترکِ یک
    observation است: Snapshot را rank می‌کند، catalog را exact resolve و
    audit حداقلی append می‌کند، اما commit/rollback/config/HTTP/Telegram و
    هرگونه mutation آفر را به caller واگذار می‌کند؛
  - endpoint Web و parse Web از همین library استفاده می‌کنند؛ بنابراین
    یک مسیر یکتا برای rank/catalog/audit دارند؛
  - بات فقط وقتی preview flag روشن باشد parser را با
    `capture_commodity_resolution=True` فرا می‌خواند و فقط برای
    `IMPLICIT_DEFAULT` نتیجهٔ shadow را در متن پیش‌نمایش نشان می‌دهد.
- مرز قطعی و رفتار ایمنی:
  - بات همچنان `commodity_id`/`commodity_name` legacy parser را در FSM
    می‌گذارد و همان‌ها را هنگام ثبت ارسال می‌کند؛ candidate مدل صرفاً متن
    اطلاع‌رسانی است؛
  - flag پیش‌فرض خاموش است؛ در حالت پیش‌فرض signature فراخوانی parser بات،
    state، متن preview و مسیر ثبت تغییر نمی‌کنند؛
  - Snapshot غایب یا هر failure مدل، preview عادی را مسدود نمی‌کند، تصمیم
    پنهان تولید نمی‌کند و فقط «عدم نتیجهٔ قابل اتکا» را نشان می‌دهد؛
  - audit فاقد متن/کاربر/Telegram ID است و تنها در transaction مستقلی که
    caller آن را commit می‌کند نوشته می‌شود.
- Test command و نتیجه:
  - unitهای shared observation، endpoint/parse Web، parser و preview بات با
    محیط DB/Redis ساختگی اجرا شدند: `Ran 47 tests ... OK`؛
  - test بات اثبات می‌کند حتی `AUTO_SELECT` مخالف با کالای legacy، state و
    POST آفر را تغییر نمی‌دهد؛
  - تلاش برای `unittest discover` کامل به‌دلیل زمان اجرای suite پیش از
    رسیدن به summary پایان یافت؛ failure‌ای گزارش نشد، اما به‌عنوان موفقیت
    regression کامل ثبت نمی‌شود.
- گیت مرحلهٔ بعد:
  - تا اجرای migration P5-C روی PostgreSQL scratch و فراهم شدن publisher
    و telemetry واقعی، هیچ flag یا runtime جدیدی فعال نمی‌شود؛
  - P6-C به تأیید مستقل مالک نیاز دارد: طراحی confirmation صریح کاربر،
    recompute/receipt تازه در لحظهٔ submit و testهای replay/idempotency.

### P5-A1 — قید خانوادهٔ سکه در ranker — 2026-08-04 — COMPLETE (library only)

- Scope انجام‌شده:
  - ranker یک خانوادهٔ صریح برای هر کالا دارد: `FULL` (امام/بهار)، `HALF`
    (نیم بهار/نیم تاریخ پایین)، `QUARTER` (ربع بهار/ربع تاریخ پایین) و
    `ONE_GRAM`؛
  - اگر یک Snapshot ناقص یا ناسالم به‌اشتباه هم‌زمان نامزدهایی از دو خانواده
    با وزن متفاوت بسازد، نتیجه `ABSTAIN` با reason
    `CROSS_DENOMINATION_CANDIDATES` است، نه فهرست انتخاب نامعقول؛
  - تردید معتبر میان تاریخ پایین و غیرتاریخ پایینِ همان وزن حفظ شده است؛
    از جمله امام/بهار و نیم بهار/نیم تاریخ پایین.
- مرز قطعی و رفتار ایمنی:
  - این تغییر parser، کالای پیش‌فرض امام، API ثبت آفر، UI یا runtime را
    تغییر نمی‌دهد؛ فقط خروجی library ranker را fail-closed می‌کند؛
  - نسخهٔ تصمیم از `coin-inference-v1` به `coin-inference-v2` افزایش یافت
    تا audit هر نتیجه را با قاعدهٔ خانوادهٔ اعمال‌شده قابل تفکیک نگه دارد.
- Test command و نتیجه:
  - test ranker، audit، catalog، shared observation، API/parse و preview بات
    با محیط ساختگی اجرا شدند: `Ran 37 tests ... OK`؛
  - testها هم تردید مجاز میان دو نیم‌سکه را و هم reject شدن overlap مصنوعی
    میان امام و نیم‌سکه را اثبات می‌کنند.
- گیت مرحلهٔ بعد:
  - سیاست UI انتخاب کاربر، parser بدون نام کالا و submit-time validation
    عمداً هنوز انجام نشده‌اند و پیش از آن توضیحات تکمیلی مالک لازم است.

### P6-B3 — قرارداد کوتاه parser بدون پیش‌فرض امام — 2026-08-04 — COMPLETE (selection deferred)

- Scope انجام‌شده:
  - parser مشترک بات/WebApp دیگر در نبود نام کالا `IMPLICIT_DEFAULT` یا
    امام تولید نمی‌کند؛ به‌جای آن `commodity_id/name=null` و یکی از
    `OMITTED`، `UNRESOLVED` یا `LOW_DATE_HINT` را نگه می‌دارد؛
  - grammar استاندارد به `خ`/`ف` برای نقد و `خ ف`/`ف ف` برای فردا تغییر
    کرد. شکل‌های چسبیده و نیم‌فاصلهٔ فردایی (`خف`/`خ‌ف` و `فف`/`ف‌ف`) و
    فرم‌های کامل معتبرند؛ marker قدیمی نقد به‌صورت مستقل معتبر نیست؛
  - `پ` مستقل به‌عنوان قید اختیاری تاریخ پایین ثبت می‌شود. aliasهای صریح
    پایین مانند `ربع پ` و `ت پ` همچنان کالا را قطعی map می‌کنند؛
  - API parse metadata `commodity_resolution` و `low_date_hint` را برمی‌گرداند
    و متن‌های تولیدشدهٔ بات/WebApp از grammar جدید استفاده می‌کنند؛
  - قرارداد کامل ورودی‌ها، تمام نگارش‌های معامله و تمام فیلدهای parser در
    `COIN_INTELLIGENCE_OFFER_PARSER_CONTRACT.md` ثبت شد.
- مرز قطعی و رفتار ایمنی:
  - Bot و WebApp تا P6-C با `commodity_id=null` آفر منتشر نمی‌کنند؛ parser
    داده را می‌خواند اما مسیر submit fail-closed است؛
  - ranking سایه فقط برای `OMITTED` باقی می‌ماند و برای `پ` بدون mapping
    صریح تا افزوده‌شدن filter تاریخ پایین در selector، انتخابی پیشنهاد
    نمی‌دهد؛
  - هیچ feature flag، migration، collector، worker یا معماری سه‌سروره در
    این مرحله فعال یا تغییر داده نشده است.
- Test command و نتیجه:
  - `tests.test_manual_offer_validation` با catalog کامل، جابه‌جایی همهٔ
    بلوک‌ها، فرم چسبیده/نیم‌فاصله، compatibility و `پ` اجرا شد؛ همراه
    router/bot/tutorial/probe/suggestion، `57` تست backend سبز شد؛
  - `src/utils/settlementType.test.ts` و `src/views/MarketView.test.ts`
    با `38` تست سبز، `vue-tsc --noEmit` سبز و build Vite نیز در sandbox
    موقت اجرا شد؛ وابستگی جدیدی نصب نشد.
- گیت مرحلهٔ بعد:
  - P6-C باید snapshot/receipt را در submit دوباره محاسبه کند، نتیجهٔ
    یکتا را فقط از همان خانوادهٔ وزنی انتخاب کند، گزینه‌های مبهم را به کاربر
    نشان دهد و پس از تأیید، شناسهٔ کالا را به command نهایی متصل کند.

### P6-C — انتخاب کالا و revalidation در ثبت نهایی — 2026-08-04 — COMPLETE (inactive by default)

- Scope انجام‌شده:
  - یک flag مستقل و پیش‌فرض خاموش به نام
    `coin_intelligence_inference_selection_enabled` اضافه شد. flag قدیمی
    preview همچنان فقط مشاهده‌پذیری shadow است؛ هیچ‌کدام collector، worker
    یا ارتباط شبکه‌ای جدیدی را فعال نمی‌کنند.
  - ranker قید تصمیم `ALL` یا `LOW_DATE_ONLY` را می‌پذیرد. `پ` مستقل فقط
    `بهار`، `نیم تاریخ پایین` و `ربع تاریخ پایین` را به selector می‌دهد و
    هر overlap میان وزن‌های متفاوت همچنان `ABSTAIN` است.
  - `/api/offers/parse` در صورت فعال‌بودن flag، تصمیم audit‌شدهٔ
    `SELECTABLE` می‌سازد: `AUTO_SELECT` نام/شناسهٔ canonical را فقط برای
    پیش‌نمایش پر می‌کند و `CONFIRM` گزینه‌های هم‌خانواده را بدون default به
    WebApp می‌دهد. Snapshot غایب یا نتیجهٔ نامطمئن هیچ آفر ایجاد نمی‌کند.
  - WebApp برای `CONFIRM` modal انتخاب دارد؛ انتخاب همراه با `decision_key`
    و شناسهٔ همان گزینه به `OfferCreate` می‌رود. Bot نیز برای همان تصمیم
    keyboard گزینه‌های current را نشان می‌دهد و فقط candidate ذخیره‌شده در
    FSM را می‌پذیرد.
  - `POST /api/offers/` و تایید نهایی Bot پیش از validation/creation، receipt
    را با surface، قیمت، settlement و candidate scope می‌خوانند و rank/catalog
    را با Snapshot محلیِ تازه دوباره اجرا می‌کنند. تغییر candidate، receipt
    نامعتبر، Snapshot stale/unavailable یا flag خاموش fail-closed است.
  - audit append-only اکنون `candidate_scope` را نیز نگه می‌دارد؛ migration
    اولیهٔ audit در PostgreSQL scratch ایزوله با موفقیت اجرا شده، اما هنوز
    روی هیچ دیتابیس runtime اجرا نشده است.
- مرز قطعی و رفتار ایمنی:
  - تصمیم قبلی هرگز فرمان ایجاد Offer نیست. client نمی‌تواند با یک
    `commodity_id` حدسی یا receipt متعلق به price/settlement/surface دیگر
    آفر بسازد؛ idempotency replay موجود نیز پیش از revalidation با command
    اولیه تطبیق داده می‌شود.
  - failure مدل یا catalog هیچ fallback به امام یا alias ندارد. مسیر کالای
    explicit بدون تغییر می‌ماند و در flag خاموش، نبود کالا همان fail-closed
    P6-B3 است.
  - تصمیم‌ها فقط از Snapshot فایل محلی و catalog اصلی همان deployment
    ساخته می‌شوند؛ هنوز inference مشترک شبکه‌ای یا کاری برای معماری سه‌سروره
    اضافه نشده است.
- Test command و نتیجه:
  - unitهای ranker/catalog/audit/observation/revalidation، parse API، parser
    و مسیر preview بات با env ساختگی اجرا شدند: `Ran 75 tests ... OK`.
  - `src/views/MarketView.test.ts`: `36` test سبز؛ `vue-tsc --noEmit` و
    `npm run build` نیز در sandbox موقت سبز شدند. هیچ dependency جدیدی نصب
    نشد.
- گیت release/staging:
  - migration append-only audit در PostgreSQL scratch با upgrade، trigger
    immutability و downgrade fail-closed آزموده شده است؛ اجرای migration در
    staging/runtime همچنان فقط در release gate مجاز است.
  - publisher محلی Snapshot، permission/path و freshness telemetry باید پیش
    از روشن‌کردن flag فراهم باشند؛ شروع با `CONFIRM` و telemetry shadow-first
    ضروری است. activation production، collectorها و معماری سه‌سروره خارج از
    این مرحله‌اند.
