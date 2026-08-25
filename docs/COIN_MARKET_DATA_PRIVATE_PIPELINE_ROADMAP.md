# Roadmap انتقال داده و Parse بازار روی شبکه خصوصی

وضعیت: طراحی مورد توافق؛ پیاده‌سازی و cutover هنوز شروع نشده است

تاریخ بازبینی: 2026-08-25

مبنای Git: `main@379b1a80`

## 1. نتیجه نهایی مورد انتظار

سرور وب/داده `65.109.220.59` مالک دریافت، Parse، نرمال‌سازی، کنترل کیفیت و نگهداری تاریخچه بازار می‌شود. سرور بات فقط facts و featureهای نهایی و نسخه‌بندی‌شده را از مسیر شبکه خصوصی دریافت می‌کند و مدل‌های تخمین را اجرا می‌کند. خروجی نسخه‌بندی‌شده مدل از همان شبکه خصوصی به وب برمی‌گردد.

```text
Telegram/API
    │
    ▼
Capture موقت ──► Parse/Normalize ──► Permanent Market Store (Web/Data)
                                            │
                                            ▼
                                  Durable Market Facts Outbox
                                            │  private network
                                            ▼
                                  Bot Market Store / Adapter
                                            │
                                            ▼
                                      Estimator Models
                                            │  private network
                                            ▼
                                  Versioned Estimator Snapshot
                                            │
                                            ▼
                                      WebApp Dashboard
```

این معماری دو هدف مستقل دارد:

1. منبع دائمی و قابل ممیزی داده بازار روی سرور وب/داده باشد.
2. مدل‌های روی سرور بات بدون دریافت متن خام، facts دقیق و کم‌حجم را در لحظه دریافت کنند.

## 2. تصمیم‌های قطعی

1. Parse و lifecycle analysis از سرور بات به سرور وب/داده منتقل می‌شود.
2. مدل‌های اصلی فعلاً روی سرور بات باقی می‌مانند.
3. متن خام عمومی و تمام Telegram envelopeها به سرور بات منتقل نمی‌شوند.
4. انتقال Market Facts از lane و worker اختصاصی استفاده می‌کند و وارد `change_log`، Redis queue یا worker عمومی sync محصول نمی‌شود.
5. انتقال بین دو سرور از شبکه خصوصی provider انجام می‌شود.
6. شبکه خصوصی جای HMAC/TLS، idempotency، sequence و ACK را نمی‌گیرد.
7. Market Facts اولین workload شبکه خصوصی است؛ sync عمومی فقط بعد از اثبات این مسیر مهاجرت می‌کند.
8. مسیر Market Facts هنگام قطع شبکه fail-closed است: outbox حفظ می‌شود و fallback خودکار به اینترنت عمومی ندارد.
9. raw capture عمومی سه روز نگهداری می‌شود؛ فقط مجموعه انتخاب‌شده وارد آرشیو دائمی می‌شود.
10. داده‌های اضافی مانند لینک کانال، لینک گروه و لینک پست وارد آرشیو دائمی نمی‌شوند.
11. ورودی اونس و تتر به‌صورت مصنوعی هر ثانیه ذخیره نمی‌شود؛ cadence واقعی منبع و مقدار دقیق مصرف‌شده مدل ملاک است.
12. اونس نباید برای مسیر اصلی به یک رکورد در دقیقه compact شود.
13. قیمت و تعداد اولیه آفر آبشده خصوصی immutable است و `final_price` یا `final_quantity` ندارد.
14. آفرهای دو گروه سکه می‌توانند معامله‌ای با تعداد و قیمت توافقی متفاوت داشته باشند؛ این outcome باید جدا و دائمی ثبت شود.
15. دو کانال عمومی آبشده برای مصرف زنده مدل قابل استفاده‌اند، اما تاریخچه دائمی نمی‌خواهند.
16. بورس در این نسخه خارج از محدوده است، ولی schema و source registry باید افزودن آن را بدون بازطراحی ممکن کند.

## 3. مرز مسئولیت دو سرور

### سرور وب/داده

- دریافت زنده Telegram و APIهای بیرونی؛
- spool موقت و reconciliation؛
- Parse آفر، معامله، reply graph، channel edit و delete؛
- نرمال‌سازی واحد، تاریخ، settlement و instrument؛
- کنترل کیفیت، ambiguity و quarantine؛
- نگهداری داده دائمی انتخاب‌شده؛
- ساخت featureهای دقیق ورودی مدل؛
- تولید outbox تراکنشی Market Facts؛
- ارائه داده بازبینی و خروجی مدل در WebApp.

### سرور بات

- دریافت idempotent facts و featureها؛
- حفظ Market Store محلی سازگار با مدل‌های فعلی؛
- اجرای مدل‌های CASH/TOMORROW و مدل‌های shadow؛
- کالیبراسیون و کنترل‌های قیمت مدل؛
- تولید snapshot اتمیک و نسخه‌بندی‌شده؛
- بازگرداندن snapshot به سرور وب از شبکه خصوصی.

### مواردی که نباید مخلوط شوند

- Product Sync و Market Facts صف و checkpoint مشترک ندارند.
- Telegram delivery queue حامل داده بازار نیست.
- raw capture پایگاه آموزش دائمی نیست.
- مدل shadow اجازه overwrite خروجی اصلی را ندارد.
- داده محلی Telegram مانند session، credential یا لینک پیام وارد Market Facts نمی‌شود.

## 4. ماتریس منابع، نگهداری و مصرف

| منبع | Capture زنده | Parse روی وب | آرشیو دائمی | انتقال به بات | مصرف اصلی |
| --- | --- | --- | --- | --- | --- |
| گروه سکه 1 | بله | آفر + reply/trade graph | بله | facts | تخمین سکه و کالیبراسیون |
| گروه سکه 2 | بله | آفر + reply/trade graph | بله | facts | تخمین سکه و کالیبراسیون |
| کانال خصوصی آبشده (`JUST IN TIME`) | بله | آفر + lifecycle معامله | بله | facts | لنگر آبشده و رژیم بازار |
| هرات (`طوفان هریرود`) | بله | quote/offer/trade normalization | بله | facts | USD/Herat input |
| اونس (`نرخ انس کهکشان`) | بله | XAU quote normalization | بله، event-driven | fact + consumed feature | XAU و regime |
| تتر Wallex | API | MID/quote normalization | بله، poll موفق | fact + consumed feature | trend/regime/fallback |
| نقدی بازار (`abshdh`) | بله | melted aggregate | خیر؛ raw سه‌روزه | facts زنده لازم | fallback/flow فعلی |
| نقدی پله (`NaghdP`) | بله | melted flow/trade | خیر؛ raw سه‌روزه | facts زنده لازم | fallback/order flow |
| بورس | بعداً | بعداً | بعداً | بعداً | خارج از این roadmap |

نام‌های نمایشی بالا فقط برای traceability طراحی‌اند. قرارداد runtime از `source_code` ثابت و allowlist‌شده استفاده می‌کند و وابسته به عنوان قابل تغییر کانال نیست.

## 5. قرارداد داده دائمی

### 5.1 قواعد مشترک

تمام رکوردهای دائمی حداقل این metadata را دارند:

- `event_key`: شناسه قطعی و یکتای منطقی؛
- `source_code`: کد allowlist‌شده منبع؛
- `source_sequence`: ترتیب یکنواخت همان stream؛
- `occurred_at_utc`: زمان رخداد در منبع؛
- `available_at_utc`: زمان دریافت قابل اتکا؛
- `persisted_at_utc`: زمان commit پایدار؛
- `schema_version`؛
- `parser_version`؛
- `fact_revision`؛
- `quality_state`: `ELIGIBLE`, `REVIEW`, `REJECTED`, `AUDIT_ONLY`؛
- `quality_reason_codes`؛
- `payload_hash`.

UTC مقدار canonical است. ساعت تهران فقط در query/UI مشتق می‌شود. Decimal/Numeric برای قیمت استفاده می‌شود؛ ذخیره پول با float ممنوع است.

### 5.2 آفرهای گروه‌های سکه

فیلدهای دائمی:

- متن خام آفر؛
- `group_code` برابر 1 یا 2؛
- نوع درخواست `BUY/SELL`؛
- settlement برابر `CASH/TOMORROW`؛
- trade form؛
- کالا/instrument؛
- تعداد و فی آفر؛
- نام و Telegram ID آفر‌دهنده؛
- زمان آفر؛
- وضعیت lifecycle؛
- confidence و evidence هر فیلد.

اطلاعات معامله در رکورد جدا ذخیره می‌شود:

- آفر مرجع؛
- نام و Telegram ID درخواست‌دهنده؛
- زمان تأیید معامله؛
- `agreed_quantity`؛
- `agreed_price`؛
- پیام تأییدکننده و branch evidence به‌صورت opaque identity، نه لینک؛
- نوع نتیجه: کامل، جزئی، ردشده، مبهم یا بدون معامله.

متن‌های غیرآفر/غیرمعامله بعد از پایان raw retention دائمی نمی‌شوند، مگر آنکه برای reply graph یک معامله انتخاب‌شده evidence لازم باشند.

### 5.3 کانال خصوصی آبشده

فیلدهای دائمی آفر:

- متن خام آفر؛
- اجزای Parse‌شده؛
- `offered_price`؛
- `offered_quantity`؛
- side، settlement و trade form؛
- زمان انتشار؛
- `trade_status`.

قیمت و تعداد آفر immutable هستند و schema فاقد `final_price` و `final_quantity` است. اگر معامله جزئی با evidence معتبر شناخته شود، `executed_quantity` یا `remaining_quantity` در رویداد معامله جدا ثبت می‌شود؛ آفر اولیه بازنویسی نمی‌شود.

عمر تجاری آفر 120 ثانیه از زمان انتشار است. freshness مدل 900 ثانیه سیاست دیگری است و نباید با عمر آفر مخلوط شود.

### 5.4 هرات

- متن خام پست؛
- قیمت نرمال‌شده Toman؛
- side/event type؛
- settlement و trade form؛
- quantity در صورت وجود؛
- زمان رخداد؛
- parser version و quality evidence.

تصحیح قیمت مبهم فقط با رنج زمانی و facts هم‌زمان مجاز است؛ افزودن عدد ثابت یا forward-fill ممنوع است.

### 5.5 اونس و تتر

اونس برای هر quote واقعی Parse‌شده ذخیره می‌شود. تتر برای هر poll موفق Wallex، با cadence پیش‌فرض 10 ثانیه ذخیره می‌شود. quiet-period row ساخته نمی‌شود.

علاوه بر observation، مقدار دقیق مصرف‌شده مدل نگهداری می‌شود:

- `inference_id`؛
- `model_version`؛
- `settlement`؛
- `feature_role`؛
- `consumed_value`؛
- `point_observation_event_key`؛
- `window_start_utc` و `window_end_utc`؛
- `sample_count`؛
- `selection_method`؛
- proxy/fallback metadata؛
- snapshot hash.

price row تکراری برای هر اجرای پنج‌ثانیه‌ای ساخته نمی‌شود. snapshot بدون تغییر یک بار ذخیره می‌شود و inferenceهای بعدی به همان snapshot ارجاع می‌دهند.

### 5.6 داده‌های ممنوع در آرشیو دائمی

- credential، session یا شماره تلفن حساب capture؛
- لینک گروه/کانال/پست؛
- username یا title منبع به‌عنوان identity عملیاتی؛
- Telegram envelope کامل؛
- peer access hash؛
- debug payload و exception خام؛
- متن پیام‌های نامرتبط با facts انتخاب‌شده؛
- row مصنوعی برای پرکردن فاصله زمانی.

## 6. معنای دقیق ورودی‌های فعال مدل

مدل اصلی و shadowها از snapshot ورودی مشترک استفاده می‌کنند:

- cadence inference: پیش‌فرض 5 ثانیه؛
- پنجره point/average بازار: 90 ثانیه؛
- point: آخرین event واقعی همان پنجره؛
- forward-fill: ممنوع؛
- USDT poll: پیش‌فرض 10 ثانیه؛
- USDT anchor trend: دو میانگین 180 ثانیه‌ای، فقط هنگام فعال‌شدن fallback مربوط؛
- market regime: سری 600 ثانیه‌ای؛
- freshness داخلی componentهای regime: حداکثر 180 ثانیه؛
- XAU اصلی: آخرین quote مستقیم در پنجره 90 ثانیه؛
- XAU fallback: فقط PAXG corroborated و مطابق guard فعلی؛
- one-per-minute XAU compaction: برای feed اصلی ممنوع.

در محاسبه intrinsic، XAU point وارد فرمول می‌شود. USDT جای مستقیم Herat نیست؛ برای trend/fallback، morning reopen و regime استفاده می‌شود. ledger باید همه roleهای واقعاً مصرف‌شده را ثبت کند، نه فقط آخرین quote را.

## 7. قرارداد انتقال روی شبکه خصوصی

### 7.1 lane رفت: Market Facts

`market_fact_sync_worker` روی سرور وب outbox را می‌خواند و batchهای نسخه‌بندی‌شده را به receiver سرور بات می‌فرستد.

Envelope پیشنهادی `market_fact_batch/1.0`:

- `batch_id`؛
- `schema_version`؛
- `stream_id`؛
- `first_sequence` و `last_sequence`؛
- `created_at_utc`؛
- `item_count`؛
- `items_hash`؛
- `sender_instance_id`؛
- items.

ACK فقط بعد از commit پایدار receiver صادر می‌شود و شامل موارد زیر است:

- `batch_id`؛
- `highest_contiguous_sequence`؛
- `accepted_count`؛
- duplicate count؛
- rejection reason codes؛
- receiver timestamp.

lost ACK باعث replay می‌شود و replay باید no-op باشد. gap اجازه advance checkpoint نمی‌دهد.

### 7.2 lane برگشت: Estimator Snapshot

خروجی مدل append-only market fact نیست و قرارداد جدا دارد:

- `snapshot_id` و `snapshot_version`؛
- `generated_at_utc`؛
- `input_snapshot_hash`؛
- `model_version`؛
- نرخ‌های CASH/TOMORROW؛
- bounds/confidence/method؛
- freshness و health؛
- SAFE_NO_DATA binding در صورت نیاز.

receiver وب snapshot را ابتدا در فایل/رکورد staging اعتبارسنجی می‌کند و سپس atomically آخرین نسخه را عوض می‌کند. نسخه قدیمی‌تر هرگز نسخه جدیدتر را overwrite نمی‌کند.

### 7.3 امنیت و شبکه

- endpointها فقط روی interface/IP خصوصی bind می‌شوند؛
- firewall فقط private IP دو میزبان و port مشخص را می‌پذیرد؛
- HMAC یا mTLS و timestamp/replay window حفظ می‌شود؛
- secret در env/secret file با mode محدود می‌ماند؛
- payload و response خام در log ثبت نمی‌شود؛
- public automatic fallback وجود ندارد؛
- health probe خصوصی از data endpoint جدا است؛
- clock skew قبل از cutover سنجیده و محدود می‌شود.

### 7.4 جداسازی از sync عمومی

- process، queue، outbox، checkpoint و metric مستقل؛
- connection pool مستقل؛
- rate/backpressure مستقل؛
- failure آن نباید offer/trade/user sync را متوقف کند؛
- کد مشترک فقط برای signing، TLS policy، retry primitives و structured logging مجاز است.

## 8. مراحل اجرایی

هر مرحله یک commit مستقل روی branch اجرای این roadmap دارد. deployment و promotion commit جدا از implementation است. عبور از gate هر مرحله پیش‌شرط مرحله بعد است.

### مرحله 0 — Baseline و inventory بدون تغییر

اقدامات:

1. ثبت SHA، branch و worktree دو میزبان.
2. inventory دقیق captureهای Account 1 و Account 2، processها، timerها و مسیرهای داده.
3. inventory جدول‌ها/JSONL/SQLiteهای تاریخی هر منبع.
4. ثبت row count، بازه زمانی، duplicate rate، gap و آخرین event هر stream.
5. ثبت وضعیت مدل اصلی و shadowها و input snapshot فعلی.
6. ثبت حجم روزانه واقعی برای طراحی storage و batch.

خروجی:

- baseline machine-readable؛
- source-to-storage map؛
- لیست history قابل بازیابی و gapهای غیرقابل بازیابی؛
- اثبات اینکه هیچ secret یا raw identity در artifact گزارش نیست.

Gate:

- همه منابع allowlist‌شده resolve شده‌اند؛
- مسیر authoritative هر داده مشخص است؛
- هیچ write یا delete عملیاتی انجام نشده است.

### مرحله 1 — آماده‌سازی شبکه خصوصی

اقدامات:

1. شناسایی private interface/IP و MTU دو سرور بدون ثبت secret در Git.
2. route و firewall دوطرفه با حداقل port.
3. DNS داخلی یا env-based endpoint؛ IP در کد hard-code نشود.
4. تست latency، packet loss، throughput و reconnect.
5. تست bind خصوصی و اثبات عدم دسترسی endpoint از public interface.
6. آماده‌سازی certificate/HMAC rotation و clock-sync check.

Failure drills:

- قطع route؛
- firewall drop؛
- packet loss؛
- timeout؛
- clock skew؛
- credential اشتباه؛
- restart یکی از میزبان‌ها.

Gate:

- ارتباط خصوصی پایدار و public exposure صفر؛
- health probe و authentication موفق؛
- rollback شبکه مستند و آزمایش‌شده؛
- هیچ sync عمومی هنوز تغییر نکرده است.

### مرحله 2 — تثبیت contract، schema و storage engine

اقدامات:

1. تعریف JSON Schema/typed contract برای capture، facts، batch و snapshot.
2. تعریف source registry و stream IDs.
3. تعریف جدول‌های curated، evidence، revisions، outbox و checkpoint.
4. انتخاب engine دائمی با ADR و benchmark.
5. ایجاد volume جدا برای داده بازار و backup مستقل.
6. تعریف migration forward/rollback بدون دست‌زدن به product DB.

توصیه engine:

- پایگاه دائمی market archive و outbox از product PostgreSQL و sync عمومی جدا باشد.
- PostgreSQL اختصاصی برای archive/query/review پیشنهاد می‌شود؛ adapter خروجی می‌تواند Market Store محلی مدل را همچنان با schema فعلی تغذیه کند.
- اگر SQLite انتخاب شود، single-writer، WAL، fsync، backup consistency و bounded writer pause باید با benchmark ثابت شود.

Gate:

- قراردادها versioned و fixture-backed؛
- field/retention/PII classification کامل؛
- migration rehearsal و restore backup موفق؛
- هیچ unresolved unit یا timestamp semantics وجود ندارد.

### مرحله 3 — Capture پایدار و retention

اقدامات:

1. یک process owner برای هر Telegram account با lock قطعی.
2. durable append قبل از ACK داخلی parser.
3. reconciliation محدود برای reconnect/restart.
4. edit/delete/reply metadata مطابق contract موجود.
5. raw retention سه‌روزه و purge قابل ممیزی.
6. disk-full و fsync failure به‌صورت fail-closed.
7. heartbeat per source شامل created/edited/deleted/duplicate/gap/lag.

Gate:

- duplicate و gap مصنوعی در تست صفر؛
- restart/reconnect بدون loss؛
- process دوم fail-fast؛
- raw بیش از retention باقی نمی‌ماند؛
- capture از parser کند یا unavailable متوقف نمی‌شود.

### مرحله 4 — انتقال Parser دو گروه سکه

اقدامات:

1. انتقال parser آفر با grammar فعلی و temporal price resolver.
2. انتقال instrument inference برای آفر بدون نام کالا.
3. انتقال settlement rules جدید (`خ` نقد، `خ ف` فردا و معادل فروش).
4. ساخت reply graph از oldest root و exact branch.
5. تشخیص reciprocal confirmation، چانه‌زنی چندکاربره، تعداد و فی توافقی.
6. ambiguity/review بدون متوقف‌کردن sibling eventها.
7. ثبت field-level evidence و parser version.
8. اتصال correctionهای WebApp به calibration corpus بدون overwrite تاریخچه.

مجموعه تست اجباری:

- fixtureهای تاریخی هر دو گروه؛
- آفرهای بی‌نام، قیمت‌های جداشده/اسلش/صفر و shorthand؛
- نیم/ربع بهار در برابر تاریخ پایین؛
- reply مستقیم، چندشاخه، چندطرفه و مذاکره قیمت/تعداد؛
- عدم معامله، رد معامله، overfill و confirmation مبهم؛
- replay و edit.

Gate:

- همه fixtureهای قطعی برابر label تاییدشده؛
- موارد مبهم به‌جای حدس وارد REVIEW؛
- parse موفق pending نمی‌ماند؛
- تفاوت با parser فعلی برای هر event reason code دارد.

### مرحله 5 — Parser کانال‌ها و lifecycle آبشده خصوصی

اقدامات:

1. انتقال parser هرات با temporal range normalization.
2. انتقال XAU event parser بدون minute compaction.
3. انتقال دو parser آبشده عمومی با retention موقت.
4. پیاده‌سازی private-gold offer lifecycle با عمر 120 ثانیه.
5. تشخیص full/partial/no-trade از edit/revisionهای معتبر.
6. جلوگیری از توقف ingest آفر هنگام تحلیل معامله.
7. حفظ offered price/quantity و ثبت outcome جدا.

Gate:

- ingest آفر و trade analysis هم‌زمان و مستقل؛
- generic edit به‌عنوان معامله ثبت نمی‌شود؛
- revision ناقص یا inconsistent به AMBIGUOUS می‌رود؛
- `final_price/final_quantity` در schema/code/API وجود ندارد؛
- دو کانال عمومی آبشده وارد archive دائمی نمی‌شوند.

### مرحله 6 — USDT/XAU materializer و input ledger

اقدامات:

1. Wallex poll موفق با cadence 10 ثانیه و quote-kind صحیح.
2. XAU event-driven storage.
3. محاسبه point/mean پنجره 90 ثانیه‌ای.
4. محاسبه featureهای 180 و 600 ثانیه‌ای فقط در role مربوط.
5. ثبت consumed snapshot و reference از inference به آن.
6. حفظ PAXG fallback guard و provenance.
7. حذف/غیرفعال‌کردن one-minute compaction از feed اصلی.
8. اصلاح comment/docهای قدیمی 30 ثانیه‌ای به مقدار واقعی 90 ثانیه.

Gate:

- برای timestamp یکسان، featureهای وب با مدل فعلی decimal-equal هستند؛
- quiet interval هیچ row مصنوعی نمی‌سازد؛
- missing direct XAU دقیقاً fallback/NO_DATA فعلی را می‌دهد؛
- point و mean در dashboard و audit قابل تفکیک‌اند.

### مرحله 7 — Market Facts outbox و worker خصوصی

اقدامات:

1. outbox در همان transaction ثبت fact نوشته شود.
2. worker batch، signing، compression اختیاری و retry bounded.
3. receiver validation، durable apply و contiguous ACK.
4. checkpoint مستقل برای هر stream.
5. duplicate/gap/quarantine و dead-letter قابل repair.
6. backpressure بدون حذف یا جابه‌جایی fact.
7. metricهای queue depth، oldest age، send/ACK latency، duplicate و rejection.

مقادیر اولیه قابل تنظیم برای benchmark:

- flush حداکثر 250ms؛
- batch حداکثر 100 event؛
- payload byte limit مستقل از event count؛
- retry با exponential backoff و jitter؛
- هیچ retry نامحدودِ بدون alert.

Gate:

- lost ACK، duplicate batch و out-of-order delivery بدون duplicate یا gap نهایی؛
- receiver down باعث حفظ کامل outbox؛
- Market Facts backlog اثری روی product sync ندارد؛
- p95 commit وب تا durable ACK بات در شبکه سالم حداکثر 1 ثانیه؛
- p99 حداکثر 3 ثانیه یا baseline مصوب سخت‌گیرانه‌تر.

### مرحله 8 — Adapter مصرف‌کننده روی سرور بات

اقدامات:

1. facts دریافتی به schema مورد انتظار estimator map شوند.
2. unit/magnitude/time guards قبل از eligible شدن.
3. transaction اتمیک observation + projection key + checkpoint.
4. malformed row جداگانه رد و checkpoint stream سالم ادامه یابد.
5. feature flag برای `LEGACY`, `PRIVATE_SHADOW`, `PRIVATE_PRIMARY`.
6. مدل و shadowها از یک immutable input snapshot استفاده کنند.

Gate:

- estimator بدون تغییر model artifact با feed جدید اجرا می‌شود؛
- هیچ Rial/Toman double conversion رخ نمی‌دهد؛
- restart adapter idempotent است؛
- snapshot model input قابل اتصال به source event است؛
- rollback به legacy feed بدون از دست رفتن capture ممکن است.

### مرحله 9 — مسیر برگشت Snapshot به WebApp

اقدامات:

1. publisher اتمیک snapshot روی بات.
2. انتقال نسخه‌بندی‌شده از شبکه خصوصی.
3. receiver وب با monotonic version guard.
4. cache invalidation و realtime publish پس از commit.
5. نمایش occurred/available/parsed/transferred/inferred/published times.
6. نمایش input source، point، mean، fallback و freshness دقیق.

Gate:

- WebApp و state مدل یک snapshot hash نشان می‌دهند؛
- snapshot قدیمی‌تر قابل overwrite نیست؛
- قطع مسیر برگشت stale state را واضح نشان می‌دهد؛
- هیچ query مستقل UI عددی متفاوت از snapshot مدل تولید نمی‌کند.

### مرحله 10 — Backfill و تجمیع تاریخچه

اقدامات:

1. import تمام تاریخچه قابل بازیابی دو گروه سکه.
2. import private gold، Herat، XAU و USDT موجود.
3. حذف فیلدهای ممنوع پیش از commit دائمی.
4. dedupe با logical identity و revision ordering.
5. ثبت source lineage و import batch، بدون URL/credential.
6. reconciliation count/range/hash میان منبع و archive.
7. quarantine رکورد ناسازگار بدون توقف batch.

قاعده انتقال به بات:

- archive وب می‌تواند متن خام مجاز و participant fields مورد توافق را نگه دارد؛
- seed سرور بات فقط facts، calibration و external history لازم را می‌گیرد؛
- raw Telegram history به بات منتقل نمی‌شود.

Gate:

- count و بازه زمانی هر source reconciled؛
- duplicate logical fact صفر؛
- rejected/quarantined گزارش‌شده و قابل بازبینی؛
- backup قبل و بعد import قابل restore؛
- import دوم no-op است.

### مرحله 11 — Shadow parity و آزمون بازار باز

اقدامات:

1. legacy capture/parser/feed همچنان primary می‌ماند.
2. pipeline وب→خصوصی→بات در shadow اجرا می‌شود.
3. مقایسه event-by-event و feature-by-feature.
4. مقایسه نرخ مدل با model artifact یکسان.
5. طبقه‌بندی اختلاف به capture، parser، lifecycle، unit، timing یا transport.
6. کالیبراسیون parser فقط با label تاییدشده.
7. اجرای soak در بازار باز؛ SAFE_NO_DATA بازار بسته کافی نیست.

معیارهای پذیرش:

- capture loss برای رویدادهای قابل دریافت: صفر؛
- duplicate eligible fact: صفر؛
- sequence gap حل‌نشده: صفر؛
- XAU/USDT consumed values برای timestamp مشترک: برابر؛
- اختلاف estimator ناشی از transport/schema: صفر؛
- اختلاف parser فقط وقتی پذیرفته است که label انسانی بهبود مسیر جدید را تایید کند؛
- source event تا snapshot بعدی مدل: p95 حداکثر 7 ثانیه؛
- حداقل یک جلسه کامل بازار باز و یک failure soak موفق.

Gate:

- گزارش parity امضا/هش‌شده و بدون raw sensitive output؛
- همه severity-1/2 بسته؛
- promotion recommendation صریح؛
- rollback rehearsal موفق.

### مرحله 12 — Cutover staging

ترتیب:

1. private network health gate؛
2. worker رفت و receiver بات؛
3. adapter در `PRIVATE_SHADOW`؛
4. snapshot return؛
5. تغییر feed مدل staging به `PRIVATE_PRIMARY`؛
6. حفظ legacy در shadow؛
7. soak و failure drill؛
8. ثبت evidence و تصمیم ادامه/rollback.

Rollback:

- مدل به `LEGACY` برمی‌گردد؛
- capture و archive وب ادامه دارد؛
- outbox/checkpoint حذف یا reset نمی‌شود؛
- snapshot آخر سالم حفظ و stale علامت‌گذاری می‌شود؛
- هیچ داده تاریخی پاک نمی‌شود.

Gate:

- WebApp و bot در event و snapshot هم‌زمان‌اند؛
- gap/duplicate صفر؛
- private route failure و recovery موفق؛
- disk-full/receiver restart/lost ACK آزمایش شده؛
- تایید صریح برای production وجود دارد.

### مرحله 13 — Cutover production

این مرحله خودکار و ضمنی نیست و authorization جدا می‌خواهد.

ترتیب:

1. backup و baseline جدید؛
2. deploy بدون switch؛
3. shadow و open-market gate؛
4. canary یک stream غیرحیاتی؛
5. XAU/USDT؛
6. Herat و آبشده؛
7. گروه 2؛
8. گروه 1؛
9. تثبیت تمام streams؛
10. legacy feed فقط shadow.

Stop conditions:

- هر sequence gap؛
- source lag بالاتر از threshold؛
- consumed-value mismatch؛
- unit/magnitude rejection غیرمنتظره؛
- model snapshot divergence بدون علت؛
- backlog رو به رشد؛
- خطای نمایش WebApp یا stale snapshot پنهان.

Gate نهایی:

- جلسه بازار باز کامل؛
- صفر data-loss/duplicate؛
- latency مطابق SLO؛
- restore/rollback آماده؛
- تایید اپراتور.

### مرحله 14 — مهاجرت sync عمومی به شبکه خصوصی

این مرحله فقط پس از تثبیت production Market Facts آغاز می‌شود و contract داده sync عمومی را تغییر نمی‌دهد.

اقدامات:

1. health/read-only probe endpoint خصوصی برای sync موجود.
2. dry-run payload validation بدون apply.
3. مقایسه public/private response و latency.
4. تغییر یک جهت sync به private peer URL با rollback فوری.
5. parity کامل و backlog drain.
6. تغییر جهت دوم.
7. soak، restart و partition drill.
8. حذف public route از مسیر فعال.
9. محدودسازی firewall؛ public configuration فقط rollback کنترل‌شده و مدت‌دار.

Gate:

- 23+ جدول sync‌شونده مطابق registry روز deployment parity دارند؛
- unsynced/quarantined غیرمنتظره صفر؛
- offer priority path و notificationها سالم؛
- rollback هر دو جهت آزموده شده؛
- Market Facts و product sync با وجود شبکه مشترک failure domain عملیاتی مستقل دارند.

### مرحله 15 — بازنشستگی مسیرهای قدیمی

فقط پس از retention window و تایید جداگانه:

- توقف collector/parser قدیمی روی بات؛
- حذف timer/service منسوخ؛
- archive فقط از artifactهای لازم برای audit؛
- حذف credential/session قدیمی با روش امن؛
- حذف public sync exposure بعد از پایان rollback window؛
- compact پایگاه‌های موقت فقط پس از backup/restore proof؛
- به‌روزرسانی runbook، diagram و inventory production.

هیچ data directory، database، tag یا artifact بدون ممیزی و اجازه حذف نمی‌شود.

## 9. Observability و SLO

Metricهای الزامی per stream:

- آخرین `occurred_at`, `available_at`, parse commit, send, ACK و model-consume؛
- capture lag؛
- parse lag؛
- outbox oldest age/depth؛
- send/ACK latency؛
- duplicate، gap، quarantine و rejection؛
- parser eligible/review/rejected ratio؛
- model input age و selection method؛
- snapshot age و WebApp publication age؛
- disk free/inode و DB checkpoint/backup age.

SLO اولیه:

- capture تا parsed fact: p95 ≤1s در حالت live سالم؛
- parsed commit تا bot durable ACK: p95 ≤1s و p99 ≤3s؛
- source event تا اولین estimator snapshot قابل استفاده: p95 ≤7s؛
- snapshot مدل تا نمایش وب: p95 ≤2s؛
- unresolved sequence gap: صفر؛
- silent forward-fill: صفر؛
- duplicate eligible fact: صفر.

SLOها بعد از baseline خصوصی می‌توانند فقط سخت‌گیرانه‌تر شوند؛ شل‌کردن آنها نیاز به تصمیم مستند دارد.

## 10. آزمون‌های اجباری ماتریسی

### صحت داده

- Unicode/Persian digits/ZWNJ/separators؛
- timezone و rollover تهران؛
- edit/delete/reply missing parent؛
- duplicate delivery و deterministic replay؛
- field ambiguity و partial correction؛
- unit/magnitude outlier؛
- private-gold partial/full/no-trade؛
- coin-group negotiated quantity/price.

### پایداری

- restart capture/parser/sender/receiver/model؛
- network partition و reconnect؛
- lost ACK؛
- duplicate و reordered batch؛
- corrupt middle/tail record؛
- disk-full/fsync failure؛
- DB lock/slow commit؛
- backlog بزرگ و drain؛
- clock skew؛
- secret rotation.

### امنیت

- public endpoint unreachable؛
- wrong source IP؛
- invalid/expired signature؛
- replay outside window؛
- oversized payload؛
- unknown source/schema؛
- raw text/identity absence in bot payload؛
- log secret/raw-data scan.

### مدل و WebApp

- identical input snapshot across main/shadows؛
- exact point/mean visibility؛
- stale/NO_DATA visible؛
- model output monotonic version؛
- WebApp hash equals bot snapshot hash؛
- rollback feed without deleting new facts.

## 11. Backup، بازیابی و retention

- raw spool: سه روز؛
- curated permanent facts: بدون حذف خودکار تا تصویب policy آینده؛
- rejected/quarantine: retention جدا و قابل تنظیم، بدون متن نامرتبط؛
- outbox delivered rows: bounded operational retention پس از checkpoint/backup؛
- model input bindings: برای replay و audit دائمی یا مطابق retention مصوب model ledger؛
- backup روی volume جدا از active database؛
- backup بدون restore test معتبر نیست؛
- RPO/RTO نهایی بعد از اندازه‌گیری حجم واقعی در مرحله 0 تصویب می‌شود.

## 12. Rollback سراسری

Rollback هرگز capture یا archive وب را خاموش نمی‌کند. تنها authority مصرف مدل جابه‌جا می‌شود:

1. freeze promotion؛
2. مدل به آخرین feed سالم `LEGACY` برگردد؛
3. snapshot وب stale/degraded را شفاف نشان دهد؛
4. private outbox بدون حذف حفظ شود؛
5. checkpoint از ACK پایین آورده نشود؛
6. root cause با replay روی shadow بررسی شود؛
7. بازگشت به private primary فقط بعد از parity مجدد.

برای مهاجرت sync عمومی، rollback فقط peer URL/route را به transport قبلی برمی‌گرداند؛ schema، change log و source sequence دست‌کاری نمی‌شوند.

## 13. موارد خارج از محدوده

- انتقال خود مدل‌های اصلی به سرور وب؛
- بازطراحی الگوریتم قیمت بدون shadow/evaluation جدا؛
- صف ارسال پست تلگرام و multi-publisher؛
- UI/UX عمومی خارج از dashboard و review مورد نیاز این pipeline؛
- ذخیره تمام پیام‌های گروه‌ها به‌صورت دائمی؛
- ذخیره هرثانیه‌ای مصنوعی XAU/USDT؛
- bot-to-bot transport؛
- استفاده از sync عمومی به‌عنوان bulk market event bus؛
- افزودن بورس در این نسخه؛
- production deploy بدون تایید مستقل.

## 14. تصمیم‌های باز که قبل از مرحله 2 باید بسته شوند

این موارد در گفتگو نهایی نشده‌اند و roadmap نباید پاسخ جعلی برایشان بسازد:

1. engine نهایی archive دائمی: PostgreSQL اختصاصی یا SQLite single-writer اثبات‌شده؛
2. حجم و نوع volume جدید روی سرور وب/داده؛
3. port، private hostname و certificate authority شبکه خصوصی؛
4. RPO/RTO و retention دقیق quarantine/input ledger؛
5. threshold نهایی alertها پس از baseline؛
6. مدت rollback window پیش از بازنشستگی legacy؛
7. سیاست نمایش نام/Telegram ID در WebApp و سطح دسترسی اپراتورها.

انتخاب هر مورد باید ADR کوتاه، تست و rollback داشته باشد.

## 15. تعریف Done نهایی

این roadmap فقط وقتی تمام است که:

- همه captureها روی وب/داده single-owner و پایدار باشند؛
- Parse آفر/معامله و lifecycle روی وب انجام شود؛
- آرشیو دائمی دقیقاً مطابق ماتریس retention باشد؛
- bot فقط facts/features لازم را از شبکه خصوصی بگیرد؛
- XAU/USDT دقیقاً با semantics مدل فعلی ذخیره و منتقل شوند؛
- main و shadowها input snapshot مشترک داشته باشند؛
- WebApp snapshot دقیق مدل و زمان تمام مراحل را نشان دهد؛
- تاریخچه قابل بازیابی import و reconcile شده باشد؛
- staging و production open-market gates پاس شده باشند؛
- rollback و failure drills موفق باشند؛
- sync عمومی نیز در مرحله مستقل روی شبکه خصوصی مهاجرت کرده باشد؛
- مسیرهای قدیمی فقط پس از تایید و backup/restore proof بازنشسته شده باشند؛
- مستندات، runbook، health check و MemoryCustodian به‌روز و سبز باشند.
