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
  - client خصوصی Telegram، raw staging، retention سه‌روزه، worker،
    checkpoint/health/schedule و ingest واقعی ایجاد یا فعال نشد؛
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
  - explicit group offer به `COIN_<canonical-code>` و unit
    `PROJECT_THOUSAND_TOMAN` وارد canonical fact table می‌شود؛
  - کالای بی‌نام عمداً `COIN_UNRESOLVED/PENDING_REVIEW` است، نه default Imam؛
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
- رفتار rollback آزموده‌شده: input ناقص/استثنایی fact نمی‌سازد؛ unnamed row
  eligible نیست؛ هیچ مسیر Offer/Trade محصول تغییر نکرده است.
- ریسک‌های باقیمانده و مالک/تاریخ پیگیری:
  - default امام و نام explicit هنوز باید با Snapshot strictly-prior validate
    شوند؛ P2-C-B/P5 مالک؛
  - معامله و قیمت توافقی/partial fill هنوز parse نشده‌اند؛ P2-C-B مالک؛
  - raw three-day staging و live transport نیازمند deployment policy جداست.
- تصمیم مرحلهٔ بعد و تأیید لازم: P2-C-B باید پیش از activation گروه‌ها
  انجام شود؛ P4-B می‌تواند فقط از factهای ELIGIBLE explicit استفاده کند.

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
