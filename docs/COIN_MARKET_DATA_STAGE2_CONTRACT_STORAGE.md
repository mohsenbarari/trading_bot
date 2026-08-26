# Gate receipt مرحله ۲ قرارداد و ذخیره‌سازی بازار

تاریخ اجرا: 2026-08-26

وضعیت gate: **PASS برای contract/schema/storage design؛ بدون deployment یا cutover**

## 1. تصمیم ADR: engine و مرز پایگاه داده

تصمیم: archive دائمی، review، input ledger و outbox روی PostgreSQL 15 اختصاصی Market Data روی سرور وب/داده قرار می‌گیرد. این database و migration chain از product PostgreSQL، Alembic محصول، sync عمومی و SQLite مدل جدا است.

دلایل:

- داده دائمی، review و query هم‌زمان به transaction، index و backup پایدار نیاز دارند؛
- fact و outbox باید در یک transaction commit شوند؛
- SQLite فعلی روی سرور بات فقط projection محلی و single-writer سازگار با estimator می‌ماند؛
- PostgreSQL 15 با image و عملیات backup فعلی پروژه سازگار است و migration مستقل از product DB آزمایش شد.

Rollback runtime هرگز down migration را اجرا نمی‌کند. image قبلی با schema expand-only ادامه می‌دهد. فایل down فقط برای rehearsal روی database disposable یا حذف آگاهانه یک محیط هنوز cutoverنشده است.

## 2. قراردادهای نسخه‌بندی‌شده

مرجع typed: `core/market_intelligence/private_pipeline_contracts.py`

JSON Schemaهای deterministic زیر از همان typed contract تولید و با `--check` کنترل می‌شوند:

- `market_capture_record/1.0`؛
- `market_fact/1.0`؛
- `market_fact_batch/1.0`؛
- `market_fact_ack/1.0`؛
- `estimator_snapshot/1.0`؛
- `market_source_registry/1.0`.

قواعد اصلی:

- UTC aware timestamp تنها زمان wire/database است؛ Tehran فقط در query/UI مشتق می‌شود؛
- `occurred_at_utc <= available_at_utc <= persisted_at_utc`؛
- زمان قابل استفاده مدل `available_at_utc` است؛
- قیمت و تعداد روی wire رشته decimal و در PostgreSQL از نوع `NUMERIC` است؛ float اقتصادی ممنوع است؛
- واحد داخلی ایران Toman و واحد هر instrument صریح است؛
- `source_sequence` ترتیب رکورد در همان `stream_id` است؛ fact stream از capture stream جداست؛
- batch فقط یک stream و sequence کاملاً پیوسته دارد؛ منشأ fact با `origin_event_key` حفظ می‌شود؛
- gap اجازه advance checkpoint نمی‌دهد و duplicate همان fact no-op است؛
- هیچ متن خام، Telegram ID، نام، username یا link در Market Fact قابل انتقال به بات نیست؛
- private-gold offer فقط offered price/quantity دارد و هیچ `final_price/final_quantity` در contract نیست؛
- coin trade می‌تواند agreed price/quantity متفاوت از offer اولیه داشته باشد.

Fixtureهای مثبت و منفی source binding، hash، timestamp، سه‌روز retention، sequence، unit، PII، outcome و SAFE_NO_DATA را پوشش می‌دهند.

## 3. source registry

Registry ده source/role دارد:

- `GROUP_1`, `GROUP_2`؛
- `PRIVATE_GOLD_CHANNEL` و projection مشتق `PRIVATE_GOLD_PAPER_MINUTE`؛
- `USD_HERAT`, `XAUUSD`, `WALLEX_PUBLIC_API`؛
- `MELTED_AGGREGATE`, `MELTED_FLOW`؛
- `IME_REALTIME_BOARD` به‌صورت reserved و disabled برای افزودن آینده بورس.

دو کانال عمومی آبشده capture و fact زنده دارند ولی archive دائمی ندارند. source ID واقعی Telegram، title و username در Registry Git نیست؛ binding آن‌ها فقط در secret/runtime config وب نگهداری می‌شود.

هر source یک capture stream و fact stream جدا دارد. sourceهای derived/reserved capture stream ندارند. seed migration و JSON registry در rehearsal byte-for-field مقایسه شدند.

## 4. schema دائمی و PII

Migration مستقل `deploy/market-data/migrations/0001_market_archive.up.sql` یک schema اختصاصی با 22 table می‌سازد:

- source registry، stream sequences و capture سه‌روزه؛
- quarantine رمز‌شده؛
- facts، revisions و field/branch evidence؛
- متن خام انتخاب‌شده و actor identity رمز‌شده؛
- coin offers/trade outcomes؛
- private-gold offers/outcomes؛
- input snapshots/components و inference uses؛
- transactional fact outbox و delivery checkpoints؛
- estimator snapshots؛
- review items و parser corrections.

Telegram ID و display name که بنا بر نیاز کاربر برای offerer/requester نگهداری می‌شوند، plaintext column ندارند: ciphertext با key خارج database و lookup HMAC جدا ذخیره می‌شود. متن خام انتخاب‌شده نیز encrypted-at-rest است. فقط نقش market reviewer/admin در WebApp حق decrypt دارد و دسترسی باید audit شود. هیچ PII به بات منتقل نمی‌شود.

## 5. retention، RPO/RTO و rollback window

| داده | retention اولیه |
| --- | --- |
| raw capture همه منابع | دقیقاً 3 روز |
| quarantine رمز‌شده | 14 روز |
| دو کانال عمومی آبشده | fact زنده 3 روز؛ بدون archive دائمی |
| گروه‌ها، آبشده خصوصی، هرات، XAU، Wallex | دائمی |
| raw منتخب، identity و correction corpus | دائمی و رمز‌شده |
| input snapshots و inference-use ledger | دائمی |
| outbox ACKشده | 7 روز؛ unacked بدون expiry |
| estimator snapshot history | 365 روز |

هدف اولیه archive/outbox: RPO حداکثر 5 دقیقه با WAL/archive backup و RTO حداکثر 60 دقیقه. commit fact+outbox اتمیک است؛ بنابراین crash process نباید fact committed بدون outbox بسازد. rollback window پس از production cutover هفت روز کامل بازار باز است؛ legacy پیش از پایان آن حذف نمی‌شود.

## 6. volume و backup

روی سرور وب/داده bind-root خالی `/srv/trading-bot/market-data` با mode `0700` ساخته شد و subpathهای `postgres`, `capture`, `state`, `sessions` و `backups-staging` جدا هستند. حدود 91.9 GB فضای آزاد روی filesystem موجود بود؛ با raw retention سه‌روزه فعلاً paid volume جدید توجیه ندارد. Stage 3 مالکیت UID/GID کانتینر و quota/health gate را اعمال می‌کند.

`backups-staging` مقصد نهایی backup نیست، چون روی همان failure domain قرار دارد. مقصد نهایی backup/WAL، Object Storage آروان با credential جدا و رمزگذاری است؛ write/delete و lifecycle آن پیش از deployment باید با probe برگشت‌پذیر و مجوز همان مرحله تأیید شود.

## 7. شبکه و image decisions

- receiver هر دو جهت روی port نهایی `9443` و IP خصوصی دقیق bind می‌شود؛ endpoint فقط از env/deploy config می‌آید و در کد hard-code نمی‌شود؛
- CA داخلی offline، leaf جدا برای هر service، IP SAN و overlap rotation استفاده می‌شود؛ HMAC دوکلیدی و replay/skew مستقل باقی می‌ماند؛
- base runtime کاندید Stage 3 برابر Python 3.11 slim Bookworm است تا major فعلی پروژه حفظ و Bullseye بازنشسته شود؛ digest فقط بعد از build/compatibility benchmark Stage 3 pin می‌شود؛
- PostgreSQL برابر `postgres:15-alpine` است و digest آن در Stage 3 pin می‌شود.

## 8. benchmark و migration/restore rehearsal

PostgreSQL disposable فقط روی loopback و volume یکتای موقت اجرا شد. product DB، Alembic و داده واقعی در دسترس rehearsal نبودند.

اجرای نهایی 50,000 row در هر یک از capture/fact/outbox روی schema فعلی 22 جدولی:

- ingest کل سه جدول: 8,997.4 row/s؛
- database: 86.046 MiB؛ backup فشرده: 18.884 MiB؛
- snapshot query p50/p95: 2.961/5.124 ms؛
- outbox per-stream claim p50/p95: 1.356/2.943 ms؛
- restore: 6.222 s و count هر سه جدول دقیقاً برابر؛
- source registry دقیقاً برابر JSON؛
- down migration schema را کامل برداشت؛
- container و volume موقت کامل حذف شدند.

## 9. alertهای اولیه

- source→durable persist: warning در p95 بالای 2s، critical بالای 5s؛
- oldest unacked outbox: warning بالای 7s، critical بالای 30s؛
- sequence gap: فوری warning، پس از 30s critical؛
- clock skew: warning بالای 10s، request rejection بالای 30s؛
- source→snapshot: SLO p95 حداکثر 7s؛
- disk: warning در 70٪ و critical در 85٪؛
- restart loop یا durable-write failure: فوری critical؛
- freshness هر source مستقل و مطابق پنجره مدل؛ aggregate health اجازه پنهان‌کردن stale group/XAU/USDT را ندارد.

thresholdها در Stage 12 با بازار باز بازبینی می‌شوند، اما ضعیف‌ترشدن آن‌ها بدون evidence مجاز نیست.

## 10. Gate و قدم بعد

- typed/JSON contracts versioned و fixture-backed: **PASS**؛
- source registry و stream semantics: **PASS**؛
- field، retention و PII classification: **PASS**؛
- unit و timestamp semantics بدون ابهام: **PASS**؛
- PostgreSQL ADR و product isolation: **PASS**؛
- migration، backup/restore و down rehearsal: **PASS**؛
- volume root جدا: **PASS**؛
- deployment، owner switch و cutover: **انجام نشد**.

مرحله بعد، Stage 3 یعنی Docker foundation، Compose profileهای دو میزبان، secret/volume contract، healthcheck و deploy/rollback rehearsal است.
