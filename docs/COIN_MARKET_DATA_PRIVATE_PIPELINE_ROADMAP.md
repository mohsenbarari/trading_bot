# Roadmap انتقال داده و Parse بازار روی شبکه خصوصی

وضعیت: مراحل 0 تا 11 تکمیل شده‌اند؛ Stage 12 از نظر offline کامل و در انتظار جلسه کامل
بازار باز است؛ Stage 13-A در staging با `PRIVATE_SHADOW` مستقر شده و cutover اصلی انجام
نشده است

تاریخ بازبینی: 2026-08-26

مبنای بازنگری Docker: `main@315f7e6a`

مبنای gate Capture مرحله 4: `main@2848b36560cfc8586dbd9759668d708625c16f2c`

مبنای gate Parser مرحله 5: `main@3cd136b2e94f1795cba388be3d98a2cb46e94cbc`

مبنای gate Parser/Lifecycle مرحله 6: `main@bbe93ed5af03f0d87738aa3d2d0b2a04e589e6f3`

مبنای gate External/Input Ledger مرحله 7: `main@9db072c5157c1684314dea71f9b3b804d6778d75`

مبنای gate Private Fact Lane مرحله 8: `main@0cbd008a`

مبنای gate Bot Adapter مرحله 9: `main@a491632b`

مبنای gate Snapshot Return مرحله 10: `main@fa4efd846d7f677e609b1173a1f447f50b561164`

مبنای gate History Backfill مرحله 11: `main@22e9fa5c97c2bceabb921399e496d135e1b74f40`

مبنای gate offline Shadow Parity مرحله 12: `main@b3fce43050df6ad0bdbb5034f1f7f79df47f9c1e`

مبنای استقرار Stage 13-A staging shadow: `main@7047ef005ce64c0266d7b55a7593ea977d65bfb1`

## 1. نتیجه نهایی مورد انتظار

سرور وب/داده `65.109.220.59` مالک دریافت، Parse، نرمال‌سازی، کنترل کیفیت و نگهداری تاریخچه بازار می‌شود. سرور بات فقط facts و featureهای نهایی و نسخه‌بندی‌شده را از مسیر شبکه خصوصی دریافت می‌کند و مدل‌های تخمین را اجرا می‌کند. خروجی نسخه‌بندی‌شده مدل از همان شبکه خصوصی به وب برمی‌گردد.

```text
Telegram/API
    │
    ▼
[Docker Capture] ──► [Docker Processor] ──► Persistent Market Store (Web/Data)
                                            │
                                            ▼
                                  [Durable Facts Outbox]
                                            │  private network
                                            ▼
                                  [Docker Receiver/Adapter]
                                            │
                                            ▼
                                      [Docker Estimator]
                                            │  private network
                                            ▼
                                  [Versioned Snapshot Relay]
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
17. تمام اجزای جایگزین Market Intelligence از ابتدا Docker-native و بخشی از deploy رسمی پروژه هستند.
18. یک image immutable و متصل به Git SHA/digest می‌تواند چند command داشته باشد، اما هر مسئولیت process/service مستقل دارد؛ یک کانتینر یکپارچه ساخته نمی‌شود.
19. کد و dependency داخل image است؛ database، spool، outbox، checkpoint، model artifact، Telegram session و secret داخل image نیست.
20. SQLite فقط روی volume محلی و با single writer مجاز است؛ SQLite مشترک روی network filesystem ممنوع است.
21. legacy host-native برای shadow و rollback موقت می‌ماند و فقط پس از container parity بازنشسته می‌شود.
22. یک Telegram session هرگز هم‌زمان توسط owner میزبان و owner کانتینری باز نمی‌شود.
23. migration و deployment از الگوی expand/contract، preflight، health gate و rollback به image digest قبلی پیروی می‌کنند.

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

## 7. قرارداد Docker و deployment

### 7.1 مرز image و service

یک image اصلی با source و dependency ثابت از همان commit ساخته می‌شود. serviceها command متفاوت همان image را اجرا می‌کنند؛ estimator می‌تواند در صورت نیاز image جدا با dependency سنگین‌تر داشته باشد، ولی lineage و Git SHA آن باید یکسان و قابل اثبات باشد.

| میزبان | service | مالکیت |
| --- | --- | --- |
| وب/داده | `market-capture-account1` | session و کانال‌های Account 1 |
| وب/داده | `market-capture-account2` | session و گروه‌های Account 2 |
| وب/داده | `market-processor` | Parse، lifecycle، quality و feature materialization |
| وب/داده | `market-fact-sync-worker` | outbox رفت روی شبکه خصوصی |
| وب/داده | `estimator-snapshot-receiver` | دریافت اتمیک خروجی مدل |
| وب/داده | `market-database` | فقط در صورت انتخاب engine کانتینری اختصاصی |
| بات | `market-fact-receiver` | validate، apply و ACK پایدار |
| بات | `market-store-adapter` | projection سازگار با estimator فعلی |
| بات | `coin-estimator` | main و shadow inference |
| بات | `estimator-snapshot-sender` | snapshot برگشت روی شبکه خصوصی |

Captureهای دو حساب جدا هستند. Parse، lifecycle و feature materialization در نسخه اول یک `market-processor` می‌مانند تا تعداد serviceها بدون نیاز عملی زیاد نشود؛ جداسازی آینده فقط با evidence منابع یا failure isolation انجام می‌شود.

### 7.2 Compose و deploy topology

- تعریف serviceها در Composeهای version-controlled و سازگار با deploy پروژه؛
- profile/override مستقل برای میزبان وب/داده و میزبان بات؛
- build یک‌باره، tag بر پایه Git SHA و verify همان image digest روی دو سرور؛
- نام project، network، volume و container قطعی و قابل inventory؛
- deployment receiver-first، سپس sender، shadow و در آخر authority switch؛
- migration به‌صورت one-shot container و قبل از شروع writer جدید؛
- systemd فقط می‌تواند Docker/Compose stack را در boot فراخوانی کند؛ اجرای مستقیم Python جدید روی میزبان ممنوع است؛
- host-native legacy تا پایان rollback window خارج از Compose باقی می‌ماند، اما هم‌زمان owner یک session یا writer یک store نمی‌شود.

### 7.3 داده و volume

- image و container filesystem disposable و ترجیحاً read-only؛
- database، WAL، spool، outbox، checkpoint و state روی volume/bind mount پایدار و جدا؛
- model artifact و static calibration read-only؛ mutable calibration/state جدا و writable؛
- Telegram session روی mount اختصاصی writable با permission محدود؛ credential از secret/env file خارج Git؛
- backup از pathهای میزبان و با consistency پایگاه داده انجام می‌شود، نه با export تصادفی container layer؛
- database یا SQLite file بین دو میزبان mount مشترک ندارد؛ انتقال فقط با قرارداد Market Facts/Snapshot است؛
- volumeهای staging و production نام و مسیر مجزا دارند.

### 7.4 امنیت و محدودیت منابع

- user غیر root، `read_only`, `no-new-privileges`, `cap_drop` و `tmpfs` محدود؛
- فقط receiverهای لازم روی private IP publish می‌شوند؛ capture و processor port عمومی ندارند؛
- resource limit/reservation جدا برای capture، processor، database، sync و estimator؛
- log rotation و byte limit؛ raw payload در stdout/stderr ممنوع؛
- healthcheck لایه‌ای: process، dependency، freshness و durable-write؛
- restart policy bounded و همراه alert؛ restart loop سبز تلقی نمی‌شود؛
- process lock روی volume پایدار علاوه بر `replicas=1` حفظ می‌شود.

### 7.5 چرخه release و rollback

1. source/tests/contract build؛
2. image build و ثبت SHA/digest/labels؛
3. secret/volume/private-network preflight؛
4. backup و migration rehearsal؛
5. deploy receiverها؛
6. deploy writerها بدون authority switch؛
7. shadow health/parity؛
8. switch با feature flag؛
9. postcheck و soak؛
10. ثبت release evidence.

Rollback کد با pin کردن digest قبلی انجام می‌شود. schema migration باید expand/contract باشد تا image قبلی در rollback window قابل اجرا بماند. rollback هرگز volume، outbox، checkpoint یا capture history را حذف نمی‌کند.

### 7.6 جابه‌جایی امن Telegram session

برای هر account:

1. checkpoint و آخرین durable append ثبت شود؛
2. owner میزبان stop و lock آن آزاد شود؛
3. container همان session mount را با owner واحد باز کند؛
4. reconciliation از checkpoint اجرا شود؛
5. gap/duplicate/heartbeat کنترل شود؛
6. سپس live-ready اعلام شود.

در rollback ابتدا container stop و lock آزاد می‌شود، بعد owner قبلی فعال می‌شود. اجرای overlap برای «کاهش downtime» ممنوع است؛ reconciliation راه پوشش فاصله است.

## 8. قرارداد انتقال روی شبکه خصوصی

### 8.1 lane رفت: Market Facts

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

### 8.2 lane برگشت: Estimator Snapshot

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

### 8.3 امنیت و شبکه

- endpointها فقط روی interface/IP خصوصی bind می‌شوند؛
- firewall فقط private IP دو میزبان و port مشخص را می‌پذیرد؛
- HMAC یا mTLS و timestamp/replay window حفظ می‌شود؛
- secret در env/secret file با mode محدود می‌ماند؛
- payload و response خام در log ثبت نمی‌شود؛
- public automatic fallback وجود ندارد؛
- health probe خصوصی از data endpoint جدا است؛
- clock skew قبل از cutover سنجیده و محدود می‌شود.

### 8.4 جداسازی از sync عمومی

- process، queue، outbox، checkpoint و metric مستقل؛
- connection pool مستقل؛
- rate/backpressure مستقل؛
- failure آن نباید offer/trade/user sync را متوقف کند؛
- کد مشترک فقط برای signing، TLS policy، retry primitives و structured logging مجاز است.

## 9. مراحل اجرایی

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

نتیجه اجرا در 2026-08-25:

- inventory روی هر دو میزبان فقط‌خواندنی اجرا شد؛
- هر دو گروه و هر پنج کانال allowlist‌شده در capture جدید resolve شدند؛
- مسیر authority فعلی و هدف برای spool، staging، canonical store و model state ثبت شد؛
- artifact گزارش هیچ متن خام، Telegram ID، session، credential یا env value ندارد؛
- اختلاف freshness مسیر قدیمی و جدید و کمبود metric پایدار duplicate/gap برای capture گروه‌ها به‌عنوان ورودی مراحل بعد ثبت شد؛
- هیچ service، timer، container، network، database یا دادهٔ زنده تغییر نکرد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE0_BASELINE.md](./COIN_MARKET_DATA_STAGE0_BASELINE.md)

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

نتیجه زیرمرحله provider network در 2026-08-26:

- هر دو میزبان به‌عنوان Hetzner Cloud Server در `hel1-dc2` تأیید شدند؛ vSwitch لازم نبود؛
- Network با نام `trading-bot-market-private`، بازه `10.240.0.0/16` و subnet نوع `cloud` با بازه `10.240.1.0/24` در `eu-central` ساخته شد؛
- IP ثابت خصوصی میزبان بات `10.240.1.10` و میزبان وب/داده `10.240.1.20` است؛
- هر دو رابط `enp7s0` به‌صورت خودکار با MTU برابر 1450 و prefix برابر `/32` بالا آمدند؛
- ping دوطرفه پنج‌تایی با packet loss صفر موفق شد؛ میانگین RTT مشاهده‌شده حدود 1.42ms در مسیر بات به وب و 0.48ms در مسیر وب به بات بود؛
- اتصال TCP از وب به SSH بات موفق بود؛ SSH وب عمداً روی IP خصوصی listen نمی‌کند و هیچ bind یا firewallی برای آن تغییر نکرد؛
- delete protection خود Hetzner برای Network فعال شد؛
- هیچ endpoint داده، route اضافه، public fallback، firewall rule، certificate، service یا sync عمومی در این زیرمرحله تغییر نکرد.

تکمیل gate مرحله 1 در 2026-08-26:

- endpoint مصنوعی دوطرفه فقط روی IP خصوصی با peer allowlist و firewall موقت دقیق اجرا شد؛
- TLS 1.3، CA/leaf validation، HMAC-SHA256 دوکلیدی، replay window، clock-skew و rotation پاس شدند؛
- 200 درخواست 64 KiB در هر جهت حداقل 25.297 MiB/s و حداکثر p95 برابر 3.446 ms ثبت کردند؛
- public exposure، bad credential، replay، route cut، firewall drop، packet loss، timeout، reconnect، restart و certificate rotation آزمایش شدند؛
- rollback تمام listenerها، ruleهای موقت و secretهای `/run` را حذف کرد و شبکه خصوصی سالم باقی ماند؛
- sync عمومی، serviceهای بازار و authority هیچ تغییری نکردند.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE1_PRIVATE_NETWORK.md](./COIN_MARKET_DATA_STAGE1_PRIVATE_NETWORK.md)

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

نتیجه اجرا در 2026-08-26:

- typed contract و شش JSON Schema برای capture، fact، batch، ACK، snapshot و source registry تثبیت شد؛
- fact stream از capture stream جدا و batch به یک stream با sequence پیوسته محدود شد؛
- PostgreSQL 15 اختصاصی Market Data انتخاب و migration chain آن از product DB/Alembic جدا شد؛
- 22 جدول برای capture، quarantine، curated facts/evidence/identity، outbox، input ledger، snapshot و review تعریف شد؛
- PII دائمی فقط رمز‌شده و با lookup HMAC روی وب مجاز است و هیچ PII در Market Fact به بات نمی‌رود؛
- source registry ده نقش، از جمله بورس reserved/disabled، را بدون source ID یا link runtime پوشش می‌دهد؛
- volume root خالی و root-only روی وب ایجاد شد؛ backup نهایی باید خارج failure domain روی Object Storage باشد؛
- rehearsal پنجاه‌هزار row در هر جدول اصلی، query benchmark، backup/restore، down migration و cleanup کامل را پاس کرد.

گزارش، ADR و gate receipt: [COIN_MARKET_DATA_STAGE2_CONTRACT_STORAGE.md](./COIN_MARKET_DATA_STAGE2_CONTRACT_STORAGE.md)

### مرحله 3 — Docker foundation و اتصال به deploy پروژه

اقدامات:

1. ساخت Dockerfile چندمرحله‌ای با runtime غیر root و image labels شامل Git SHA.
2. تعریف Compose پایه و override/profile جدا برای وب/داده و بات.
3. تعریف service commands طبق جدول بخش 7، بدون اجرای مستقیم Python جدید روی میزبان.
4. تعریف volumeها، ownership، mode و backup path بدون انتقال داده زنده.
5. تعریف secret/env contract و جلوگیری از ورود credential/session به image یا Git.
6. تعریف healthcheck، resource limits، log rotation و restart policy.
7. افزودن build/pull/digest verification، migration one-shot، preflight و postcheck به deploy رسمی.
8. ساخت image و اجرای smoke با fixture و network مصنوعی؛ هیچ capture زنده در این مرحله owner نمی‌شود.
9. آزمون rollback به digest قبلی و compatibility با schema expand-only.
10. تولید inventory machine-readable از image/service/volume/network برای هر میزبان.

Gate:

- image با source SHA یکسان reproducible و secret scan سبز؛
- containerها بدون root/privileged و با filesystem حداقلی اجرا می‌شوند؛
- state پس از recreate container باقی می‌ماند؛
- SQLite روی volume محلی و single-writer است؛
- serviceهای بدون نیاز inbound هیچ port منتشر نمی‌کنند؛
- receiver فقط قابلیت bind به private endpoint تنظیم‌شده دارد؛
- deploy و rollback rehearsal بدون data deletion موفق است؛
- legacy host service هنوز authority اصلی و بدون تداخل است.

نتیجه اجرا در 2026-08-26:

- image مستقل pipeline روی Python 3.11 slim Bookworm با base/frontend digest و dependency hash ثابت ساخته شد؛
- دو build بدون cache از commit یکسان byte-identical شدند و OCI revision با Git SHA تطبیق داده شد؛
- Compose پایه و override مستقل وب/داده و بات، به‌ترتیب هفت و چهار service، بدون port اضافی تثبیت شد؛
- تمام runtimeها non-root، read-only، بدون capability و با restart/log/resource محدود هستند؛
- secretها فقط از file mount با parent `root:root 0700` و فایل `root:10001 0440` خوانده می‌شوند؛ PostgreSQL فقط supplemental group لازم را دارد؛
- migration مستقل 22 جدولی و اجرای دوم no-op، ACK/replay fixture، snapshot اتمیک، recreate state و rollback image پاس شد؛
- مالک دوم Telegram session و writer دوم SQLite حتی با state path متفاوت روی resource مشترک fail-closed شدند؛
- filesystem/history secret scan سبز و cleanup container/network/image/temp کامل بود؛
- `MARKET_PIPELINE_MODE=live` عمداً تا مراحل بعدی با exit 78 مسدود است؛ هیچ deploy، owner switch یا دست‌کاری runtime زنده انجام نشد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE3_DOCKER_FOUNDATION.md](./COIN_MARKET_DATA_STAGE3_DOCKER_FOUNDATION.md)

### مرحله 4 — Capture پایدار و retention

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

نتیجه اجرا در 2026-08-26:

- engine مشترک هر دو حساب با SQLite FULL outbox، append/flush/fsync و ACK داخلی پس
  از durable append پیاده شد؛ parser هیچ dependency یا backpressure روی capture ندارد؛
- contractهای جاری `market_channel_event/1.0` و `coin_group_event/2.0`، متن دقیق،
  edit/delete/reply/topic و HMAC identity بدون افزودن فیلد اقتصادی حفظ شدند؛
- reconciliation کانال‌ها 30 دقیقه و گروه‌ها 6 ساعت است؛ اجداد reply فقط تا 2 ساعت
  و عمق 20 بازیابی می‌شوند و truncation به‌صورت health degraded fail-visible است؛
- retention دقیق سه‌روزه بر اساس `available_at_utc` با rewrite اتمیک، purge state و
  audit بدون raw payload پیاده شد؛ partial tail repair و corrupt middle fail-closed است؛
- heartbeat برای هر هفت source شامل created/edited/deleted/duplicate/quarantine،
  gap recovered، lag، sequence و آخرین durable append است؛
- rehearsal Docker با `network=none` هر دو crash window، restart/replay، duplicate،
  sequence بدون gap، مالک دوم، retention و نبود parser/session/product DB را پاس کرد؛
- gate کامل Stage 3 روی image جدید دوباره پاس شد: دو build مستقل برابر، Telethon 1.44.0
  hash-locked، image برابر 147.232 MiB، secret scan و cleanup کامل؛
- live فقط برای دو capture role شناخته می‌شود و بدون config allowlist، session 0600،
  HMAC موجود Account 2 و authority marker متصل به همان release fail-closed است؛
- هیچ deploy، Telegram login، session copy، owner switch یا رویداد live انجام نشد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE4_DURABLE_CAPTURE.md](./COIN_MARKET_DATA_STAGE4_DURABLE_CAPTURE.md)

### مرحله 5 — انتقال Parser دو گروه سکه

اقدامات:

1. انتقال parser آفر با grammar فعلی و temporal price resolver.
2. انتقال instrument inference برای آفر بدون نام کالا.
3. انتقال settlement rule تأییدشدهٔ production: marker نقد صریح = نقد؛ marker فردا
   صریح یا نبود marker = فردا، با تقدم marker فردا در syntaxهای مرکب.
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

نتیجه اجرا در 2026-08-26:

- parser آفر، resolver زمانی و reply/trade linker دو گروه در role مستقل Docker به
  Account 2 spool متصل شدند؛ role فقط shadow است و هیچ مدل یا outbox اصلی را تغییر نمی‌دهد؛
- production-shaped replay روی 5,926 پیام و 2,845 fact با حضور هر چهار ورودی علّی،
  اختلاف اقتصادی و provenance صفر و runtime failure صفر داشت؛
- 1,961 candidate بی‌نام در عبور اول شناسایی شد و prediction ledger به‌عنوان dependency
  اجباری تثبیت شد؛ نبود correction یا prediction snapshot در live، startup را fail می‌کند؛
- field-level evidence، parser version و correction corpus append-only بدون raw/identity
  اضافه شد؛ WebApp هر revision را به‌صورت رکورد مستقل در corpus نگه می‌دارد؛
- partial tail، invalid sibling، replay، restart، reply branch، قیمت/تعداد توافقی و
  instrument inference در Docker با network بسته پاس شدند؛ cleanup کامل بود؛
- SQLite sidecarها فقط snapshot اتمیک immutable هستند؛ DB زنده یا WAL مشترک mount نمی‌شود؛
- هیچ deploy، Telegram session، PostgreSQL، product DB، authority switch یا cutover انجام نشد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE5_COIN_GROUP_PARSER.md](./COIN_MARKET_DATA_STAGE5_COIN_GROUP_PARSER.md)

### مرحله 6 — Parser کانال‌ها و lifecycle آبشده خصوصی

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

نتیجه اجرا در 2026-08-26:

- `market-processor` هر دو spool را مستقل و با بودجه جدا مصرف می‌کند تا حجم XAU مانع
  پیشرفت گروه‌های سکه نشود؛ inventory سلامت هر هفت stream را پوشش می‌دهد؛
- ممیزی فقط‌خواندنی 75,900 رویداد جدید، بدون انتقال raw، خطای contract یا parser نشان
  نداد؛ کانال خصوصی 10,063 از 10,063 آفر و XAU هر 39,677 quote را Parse کرد؛
- قیمت و تعداد اولین revision آفر خصوصی immutable ماند و outcome در جدول جدا با
  `FULL/PARTIAL/NONE/AMBIGUOUS` ثبت شد؛ generic یا inconsistent edit معامله فرض نشد؛
- حذف پیام منقضی در کانال خصوصی fact اقتصادی تاریخی را retract نمی‌کند، زیرا حذف source
  بخشی از رفتار عادی کانال است؛ evidence bounded فقط برای audit حفظ می‌شود؛
- هر quote واقعی XAU حفظ و مسیر compaction دقیقه‌ای حذف شد؛ facts دو کانال عمومی آبشده
  پس از سه روز از store موقت purge و هرگز archive دائمی نمی‌شوند؛
- Docker gate با network بسته، هر هفت source، partial-tail/replay، lifecycle و cleanup
  کامل را از commit تمیز پاس کرد؛ هیچ deploy، session ownership یا model authority تغییر
  نکرد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE6_CHANNEL_LIFECYCLE.md](./COIN_MARKET_DATA_STAGE6_CHANNEL_LIFECYCLE.md)

### مرحله 7 — USDT/XAU materializer و input ledger

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

نتیجه اجرا در 2026-08-26:

- capture مستقل API با SQLite FULL outbox و spool fsynced برای Wallex و PAXG به image و
  Compose وب افزوده شد؛ هیچ session یا credential خصوصی ندارد و poll پیش‌فرض 10s است؛
- response خام، URL/header/credential حذف و فقط `external_quote_event/1.0` کمینه ثبت
  می‌شود؛ Wallex هر `BID/ASK/MID` واقعی را نگه می‌دارد و مدل فقط MID را مصرف می‌کند؛
- materializer با Decimal، point/mean نودثانیه، roleهای invoked 180/600 ثانیه و ledger
  immutable پیاده شد؛ quiet cycle با sample set ثابت snapshot تازه تولید نمی‌کند؛
- PAXG source دائمی و transfer-safe به registry افزوده شد، اما همیشه proxy می‌ماند؛
  direct XAU مقدم و guard دو book/فاصله 2% از XAU اخیر fail-closed است؛
- timestamp مشترک با selection فعلی مدل decimal-equal بود؛ 112 تست pipeline و 81 تست
  estimator/collector پاس شدند؛
- گیت Docker parser/materializer، گیت کامل reproducible foundation/PostgreSQL و poll
  واقعی Wallex/PAXG از داخل image همگی پاس و cleanup کامل داشتند؛
- هیچ deploy، PostgreSQL زنده، model feed یا authority تغییر نکرد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE7_INPUT_MATERIALIZER.md](./COIN_MARKET_DATA_STAGE7_INPUT_MATERIALIZER.md)

### مرحله 8 — Market Facts outbox و worker خصوصی

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

نتیجه اجرا در 2026-08-26:

- آرشیو fact، revision، projection تخصصی و outbox در یک transaction PostgreSQL ثبت
  می‌شوند و delivery cursor مستقل از source sequence امکان انتقال revision را می‌دهد؛
- sender با batch صدتایی/۷۶۸ KiB، flush ۲۵۰ms، backoff محدود، dead-letter قابل repair
  و head-of-stream blocking هیچ fact را حذف یا جابه‌جا نمی‌کند؛
- receiver با SQLite FULL، checkpoint مستقل stream، revision-aware apply و contiguous
  ACK، duplicate/gap/conflict را idempotent یا fail-closed مدیریت می‌کند؛
- mTLS اجباری، HMAC روی بایت دقیق body، nonce/replay/skew guard، peer allowlist و نبود
  public fallback تثبیت شد؛ raw و secret در log/heartbeat نیست؛
- گیت ۱۰۰۰ fact با ۱۰ batch، p95/p99 برابر 81.517ms، lost-ACK replay امن و حفظ کامل
  outbox هنگام قطع receiver پاس شد؛
- گیت Docker کامل، migration ۲۳ جدولی، persistence، rollback، secret scan و cleanup
  کامل پاس شدند؛ backlog این lane هیچ dependency به Product Sync ندارد؛
- هیچ deploy، endpoint زنده، model feed، authority switch یا cutover انجام نشد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE8_PRIVATE_FACT_LANE.md](./COIN_MARKET_DATA_STAGE8_PRIVATE_FACT_LANE.md)

### مرحله 9 — Adapter مصرف‌کننده روی سرور بات

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

نتیجه اجرا در 2026-08-26:

- receiver SQLite فقط‌خواندنی و Market Store با single writer مصرف می‌شوند؛ observation،
  projection، offer dimensions، rejection و checkpoint هر delivery اتمیک هستند؛
- قیمت project-thousand/Toman بدون تبدیل ثانویه عبور می‌کند و guard واحد، magnitude،
  currency، quantity pair و timestamp پیش از eligibility اعمال می‌شود؛
- معاملهٔ سکه ابعاد آفر ریشه و price/quantity توافقی را حفظ می‌کند؛ outcome آبشده قیمت
  immutable آفر را نگه می‌دارد؛ outcome غیرمعامله‌ای audit-only است؛
- ردیف malformed جدا رد و همان stream ادامه داده می‌شود؛ gap یا خطای storage fail-closed
  است؛ revision و restart idempotent هستند؛
- projection هرات پیش از اتصال اصلاح شد تا OFFER/TRADE، settlement، form و quantity را
  برخلاف quote ساده از دست ندهد؛
- سوییچ صریح `LEGACY/PRIVATE_SHADOW/PRIVATE_PRIMARY` با rollback مستقل از capture اضافه
  شد و حالت `AUTO` وجود ندارد؛
- estimator و snapshot publisher موجود بدون تغییر artifact روی feed خصوصی اجرا شدند؛
  input component از event key به fact/revision قابل ردیابی است؛
- گیت Docker کامل، ۲۷ آزمون متمرکز و تست restart/malformed/واحد پاس شدند؛ هیچ deploy یا
  تغییر model authority انجام نشد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE9_BOT_ADAPTER.md](./COIN_MARKET_DATA_STAGE9_BOT_ADAPTER.md)

### مرحله 10 — مسیر برگشت Snapshot به WebApp

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

نتیجه اجرا در 2026-08-26:

- estimator موجود در read transaction ثابت اجرا و snapshot دارای id/version/input hash
  به‌صورت اتمیک روی بات منتشر می‌شود؛ pending publish پس از crash با همان payload برمی‌گردد؛
- input trace شامل source fact/event/revision، point، mean، method، fallback، freshness و
  occurred/available/parsed/transferred است؛ inferred/received/published نیز ثبت می‌شوند؛
- sender فقط با mTLS/HMAC خصوصی و ACK منطبق checkpoint می‌دهد؛ lost ACK، restart و قطع
  مسیر بدون public fallback ایمن هستند؛
- receiver laneهای shadow/primary را جدا و یکنواخت نگه می‌دارد؛ regression/conflict رد و
  duplicate idempotent است؛
- پس از commit، web view، cache generation و realtime outbox با hash قطعی منتشر می‌شوند؛
  view هیچ نرخ مستقلی محاسبه نمی‌کند و route cut را `STALE` نشان می‌دهد؛
- hash bot، ACK و web view برابر بود؛ ۴۱ آزمون متمرکز، schema check و گیت کامل Docker با
  rollback/cleanup پاس شد؛
- پیش‌فرض `LEGACY` ماند و هیچ deploy، cache/realtime عملیاتی یا WebApp authority تغییر نکرد.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE10_SNAPSHOT_RETURN.md](./COIN_MARKET_DATA_STAGE10_SNAPSHOT_RETURN.md)

### مرحله 11 — Backfill و تجمیع تاریخچه

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

نتیجه اجرا در 2026-08-26:

- importer نسخه‌بندی‌شده با artifact hash، logical identity، source revision و سه جدول
  افزایشی lineage/item/quarantine ساخته شد؛ schema مستقل به version 2 و ۲۶ جدول رسید؛
- URL/credential/envelope/identity مستقیم پیش از commit رد می‌شود؛ raw و participant منتخب
  فقط به شکل ciphertext در archive وب می‌ماند و seed بات فقط current facts را می‌گیرد؛
- گیت ۶ source با ۱۰۰۶ رکورد، ۹۹۵ fact یکتا، ۱۰۰۰ revision، ۶ quarantine و import دوم
  no-op پاس شد؛ backup قبل/بعد restore و duplicate logical fact صفر بود؛
- گیت بازگشتی Docker/Compose، migration second-pass، recreate، rollback و cleanup پاس شد؛
- تاریخچه واقعی import و هیچ feed/authority/deploy تغییر نکرد؛ اجرای عملیاتی به cutover
  مجوزدار موکول است.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE11_HISTORY_BACKFILL.md](./COIN_MARKET_DATA_STAGE11_HISTORY_BACKFILL.md)

### مرحله 12 — Shadow parity و آزمون بازار باز

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

نتیجه پیاده‌سازی و گیت offline در 2026-08-26:

- collector فقط‌خواندنی و report امضاشده برای مقایسه capture/fact/feature/estimator/timing
  ساخته شد و اختلاف را در هفت دسته مصوب طبقه‌بندی می‌کند؛ parser/lifecycle بدون label
  انسانی تاییدشده قابل پذیرش نیست؛
- replay برابر ۱۰۰۰ event در هر lane، صفر loss/duplicate/gap، برابری XAU/USDT، صفر
  same-input estimator mismatch و p95 برابر ۵٫۸ ثانیه را ثبت کرد؛
- ماتریس منفی، HMAC/tamper، ۲۸ آزمون متمرکز و گیت کامل Docker/rollback/cleanup پاس شد؛
- این نتیجه جای جلسه واقعی بازار باز را نمی‌گیرد. هیچ deploy/cutover انجام نشد و توصیه رسمی
  `HOLD_LIVE_OPEN_MARKET_REQUIRED` است؛ تکمیل Stage 12 به مجوز استقرار shadow و live soak
  وابسته است.

گزارش و gate receipt: [COIN_MARKET_DATA_STAGE12_SHADOW_PARITY.md](./COIN_MARKET_DATA_STAGE12_SHADOW_PARITY.md)

### مرحله 13 — Cutover staging

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

نتیجه Stage 13-A در 2026-08-26:

- image متصل به commit روی هر دو میزبان در rootهای ایزوله staging مستقر شد؛ تمام اجزای
  capture، processor، archive، انتقال facts، adapter، estimator و بازگشت snapshot در
  `PRIVATE_SHADOW` سالم هستند؛
- اختیار زنده دو Telegram session به captureهای کانتینری تحویل شد و ownerهای میزبان بدون
  حذف unit، session یا امکان rollback متوقف ماندند؛ guard موقت systemd مانع شروع دوباره
  آن‌ها توسط timer قدیمی می‌شود؛
- صف facts در soak خالی، rejected/duplicate صفر و snapshot canonical دو میزبان برابر بود؛
  قطع receiver در هر جهت، restart دو capture و بازیابی صف/ACK موفق بود؛
- یک payload تاریخی نامعتبر، SQLite WAL فقط‌خواندنی و outcome یتیم جدا و fail-closed شدند
  تا هیچ‌کدام loop زنده را متوقف نکند؛
- این فقط استقرار shadow است: WebApp/product authority، `PRIVATE_PRIMARY`، production،
  sync عمومی و retirement legacy تغییر نکردند؛ Stage 12/13 تا جلسه کامل بازار باز و گزارش
  parity امضاشده باز می‌مانند.
- ممیزی overlap نشان داد lane قدیمی کانال خصوصی از روز قبل متوقف و cadence منابع بیرونی
  ناهم‌ارز است؛ بنابراین parity زنده بعدی باید eventهای یک capture owner را پس از capture به
  دو projection ایزوله fan-out کند و هرگز session تلگرام دوم نسازد.
- هارنس تک‌مالک version-pinned برای همین fan-out ساخته شد: یک prefix ثابت از spool زنده و
  یک SQLite seed سازگار به دو lane مستقل replay می‌شود؛ بعد از اعتبارسنجی کامل prefix فقط
  eventهای window با `now/as_of` ثابت مشترک وارد هر دو lane می‌شوند. final facts، snapshot
  و rate بدون ماندگاری raw/identity/price اختلاف مقایسه و report امضاشده تولید می‌شود. append-race،
  corrupt complete record، writer-lock، tamper، redaction و cleanup در تست پوشش داده شدند.
  اختلاف fact بر اساس source/instrument داخلی aggregate می‌شود و XAU/USDT value mismatch
  از metadata/cadence و value-schema mismatch جداست تا severity-1 کاذب ساخته نشود.
  این rehearsal همیشه `HOLD_STAGE12_LIVE_PARITY_REQUIRED` است و اجرای staging آن هیچ feed،
  owner یا cutover را تغییر نمی‌دهد.
- rehearsal نهایی تک‌مالک با `main@50aea41d` تعداد ۱٬۲۱۶ event window را در هر دو lane
  بدون duplicate/reject/stale پردازش کرد. candidate هر ۱٬۲۱۵ quote واقعی XAU را حفظ کرد،
  baseline قدیمی فقط cadence compactشده را داشت، unit/parser/lifecycle mismatch صفر و هر
  ۱۴ rate برابر بود. اختلاف XAU و schema جدید `mean_price` باعث ماندن gate در HOLD شد؛
  window آرام بازار جای session کامل گروه/آبشده/هرات را نمی‌گیرد.
- adapter پس از یک backlog واقعی به‌علت full-table sort/fetch و tmpfs محدود متوقف شد؛
  `main@fd665759` خواندن را به cursorهای per-stream و merge bounded پانصدتایی تبدیل کرد.
  preflight هشت‌مگابایتی پاس شد و ۱۰ stream زنده با ۵۱٬۲۸۹ delivery، lag/duplicate/rejection
  صفر بازیابی شدند.
- timeline اولیه یک race علّی میان timestamp ارزیابی و اولین SELECT نشان داد. اصلاح
  `main@d2b79298` cutoff زمان ورود محلی، pin شدن read snapshot پیش از تعیین زمان زنده،
  `generated_at` زیرثانیه و guard قطعی `transferred_at <= generated_at` را اضافه کرد؛ window
  اقتصادی anchor قدیمی نیز از knowledge cutoff فعلی جدا شد.
- چهار service بات با release جدید و همچنان `PRIVATE_SHADOW` healthy/restart-zero شدند؛ وب
  snapshot تازه را `FRESH` گرفت و هیچ authority یا feed اصلی تغییر نکرد.
- timeline امضاشده ده snapshot product و ۵۵ snapshot بدون version gap را ثبت کرد؛ p95
  pair skew برابر `4.114s` و transfer-to-snapshot برابر `6.788s` بود. XAU/USDT point و mean
  همگی حداکثر ۲۵ bps فاصله داشتند و هرات فردایی/سه aggregate دقیقاً برابر بودند، اما drift
  private-gold و نبود همهٔ نرخ‌های coin در بازار آرام، gate بازار باز را باز نگه داشت. نتیجه
  رسمی `HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED` و `cutover_performed=false` است.
- پنجره فعال بعدی در 2026-08-27 تعداد ۶۰ snapshot محصول و ۳۱۴ snapshot candidate را بدون
  version gap ثبت کرد؛ p95 انتقال `6.869s` بود و transport gate را پاس کرد. XAU حداکثر
  ۲۵ bps، USDT حداکثر ۵ bps و bookهای فعال Herat/آبشده عمومی بدون اختلاف بیش از ۱۰۰ bps
  بودند. lane
  قدیمی private-gold در تمام window stale ولی candidate فردایی زنده بود، پس baseline قدیمی
  برای parser/value آن oracle معتبر نیست. candidate فقط ۱۰ rate در برابر ۱۴ rate محصول
  داشت: `IMAM/CASH`، `HALF_BAHAR/CASH` و هر دو rate یک‌گرمی به‌علت نبود تاریخچهٔ هم‌زمان
  anchor سکه و underlying آبشده fail-closed شدند. پیش از هر cutover، تاریخچهٔ سکه و driverها
  باید point-in-time و دست‌کم در horizon هفت‌روزهٔ موتور نرخ به Store جدید backfill و سپس
  parser/lifecycle تک‌مالک و یک جلسه کامل بازار باز تکرار شود. recommendation همچنان
  `HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED` و cutover برابر false است.
- replay تک‌مالک بعدی با ۲٬۳۴۴ رکورد window، duplicate/partial-tail صفر و hash
  `8002b89e4f5e27ee4ab48fa222a80582a141b89b584f3d5be17c44627bfd05f4` اجرا شد.
  اختلاف‌های fact به cadence مصوب XAU و lifecycle private-gold محدود بود؛ دو parser mismatch
  فقط در projection دقیقه‌ای private-gold ثبت شد و هیچ mismatch دو گروه سکه وجود نداشت. هر
  ۱۴ rate برابر بود، اما XAU consumed-value و schema جدید همچنان gate را در HOLD نگه داشت.
- backfill نقطه‌زمانی هفت‌روزه با ۳۵۶٬۱۴۸ revision در ۱۸۲ bundle و manifest SHA-256 برابر
  `bb8c7b83d80fbd9e4e02aa9b3868ee570fc6cdd6c3f42c1d5fcebafcc2c58fa7` به staging
  وارد شد. import و replay دوم idempotent، failed/quarantine/dead-letter صفر و outbox پس از
  drain صفر بود؛ adapter/receiver روی ۱۰ stream بدون rejection یا duplicate باقی ماندند.
- timeline پس از backfill هر ۱۴ rate را در هر ۱۰ نمونه و بدون presence mismatch ثبت کرد، پس
  مشکل چهار خروجی مفقود رفع شد. بااین‌حال ۲۶ مورد از ۱۴۰ مقایسه بیرون ۱۰۰ bps بود؛ oracle
  قدیمی private-gold حدود ۴۷٫۷ ساعت stale و candidate تازه بود. p95 انتقال `10.673s` نیز
  گیت هفت‌ثانیه‌ای را پاس نکرد. گزارش امضاشده با hash
  `42132dba8cee21050d095eed53418ac45790a58ce38be20c689dc3d58fa1141c`،
  `HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED` و `cutover_performed=false` ثبت شد.
- snapshotها و image tar موقت پس از تایید import پاک شدند و backup/export محافظت‌شده روی
  میزبان وب باقی ماند. staging در تمام عملیات `PRIVATE_SHADOW` بود؛ WebApp/product authority،
  production و primary feed تغییر نکردند.
- probe سه‌ثانیه‌ای estimator زیر ساخت snapshot روی Store بزرگ false-negative شد؛ compose
  `main@eb66dfdd` آن را بدون تغییر image یا منطق مدل به هشت ثانیه افزایش داد. پنج probe
  پیاپی پاس، restart صفر و ۱۴ rate تازه پس از recreate ثبت شد.
- scheduler قدیمی پنج ثانیه را پس از پایان محاسبه صبر می‌کرد. `main@4e6e9278` cadence را
  start-to-start کرد؛ سپس `main@f01c797d` interval را با default پنج ثانیه قابل‌تنظیم و
  `main@0f1a534b` budget CPU estimator را مستقل کرد. staging با interval پنج ثانیه و ۱٫۵
  CPU در گزارش امضاشده
  `86dec80c934f12d4703eaf738fe1bb387e9f16ceac5be6564d0008c9f288fd2a` تعداد ۴۹
  snapshot بدون version gap و transfer p95 برابر `6.367s` ثبت کرد؛ گیت latency پاس شد.
  هر ۱۴ rate حاضر بود، اما ۲۵/۱۴۰ value بیرون ۱۰۰ bps و full market session ناقص ماند، پس
  recommendation همچنان `HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED` و cutover برابر false است.

رسید عملیاتی: [COIN_MARKET_DATA_STAGE13_STAGING_SHADOW.md](./COIN_MARKET_DATA_STAGE13_STAGING_SHADOW.md)

### مرحله 14 — Cutover production

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

### مرحله 15 — مهاجرت sync عمومی به شبکه خصوصی

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

### مرحله 16 — بازنشستگی مسیرهای قدیمی

فقط پس از retention window و تایید جداگانه:

- توقف collector/parser قدیمی روی بات؛
- حذف timer/service منسوخ؛
- archive فقط از artifactهای لازم برای audit؛
- حذف credential/session قدیمی با روش امن؛
- حذف public sync exposure بعد از پایان rollback window؛
- compact پایگاه‌های موقت فقط پس از backup/restore proof؛
- به‌روزرسانی runbook، diagram و inventory production.

هیچ data directory، database، tag یا artifact بدون ممیزی و اجازه حذف نمی‌شود.

## 10. Observability و SLO

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
- disk free/inode و DB checkpoint/backup age؛
- image SHA/digest drift، container restart/OOM و healthcheck failure؛
- volume permission/space و active owner identity هر Telegram session.

SLO اولیه:

- capture تا parsed fact: p95 ≤1s در حالت live سالم؛
- parsed commit تا bot durable ACK: p95 ≤1s و p99 ≤3s؛
- source event تا اولین estimator snapshot قابل استفاده: p95 ≤7s؛
- snapshot مدل تا نمایش وب: p95 ≤2s؛
- unresolved sequence gap: صفر؛
- silent forward-fill: صفر؛
- duplicate eligible fact: صفر.

SLOها بعد از baseline خصوصی می‌توانند فقط سخت‌گیرانه‌تر شوند؛ شل‌کردن آنها نیاز به تصمیم مستند دارد.

## 11. آزمون‌های اجباری ماتریسی

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

### Docker و deploy

- build تکرارپذیر و image secret scan؛
- recreate/upgrade/rollback container با حفظ volume؛
- اجرای non-root و failure صحیح permission؛
- جلوگیری از owner هم‌زمان host/container برای هر session؛
- migration one-shot دوباره‌پذیر و second-pass no-op؛
- digest mismatch بین دو میزبان که باید deploy را متوقف کند؛
- resource limit/OOM و recovery بدون corruption؛
- receiver private bind و اثبات نبود public listener.

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

## 12. Backup، بازیابی و retention

- raw spool: سه روز؛
- curated permanent facts: بدون حذف خودکار تا تصویب policy آینده؛
- rejected/quarantine: retention جدا و قابل تنظیم، بدون متن نامرتبط؛
- outbox delivered rows: bounded operational retention پس از checkpoint/backup؛
- model input bindings: برای replay و audit دائمی یا مطابق retention مصوب model ledger؛
- backup روی volume جدا از active database؛
- container layer و image جای backup داده نیستند؛
- backup بدون restore test معتبر نیست؛
- RPO/RTO اولیه در مرحله 2 تصویب شد و پس از اندازه‌گیری بار واقعی در مراحل live دوباره کالیبره می‌شود.

## 13. Rollback سراسری

Rollback هرگز capture یا archive وب را خاموش نمی‌کند. تنها authority مصرف مدل جابه‌جا می‌شود:

1. freeze promotion؛
2. مدل به آخرین feed سالم `LEGACY` برگردد؛
3. snapshot وب stale/degraded را شفاف نشان دهد؛
4. private outbox بدون حذف حفظ شود؛
5. checkpoint از ACK پایین آورده نشود؛
6. root cause با replay روی shadow بررسی شود؛
7. بازگشت به private primary فقط بعد از parity مجدد.

برای مهاجرت sync عمومی، rollback فقط peer URL/route را به transport قبلی برمی‌گرداند؛ schema، change log و source sequence دست‌کاری نمی‌شوند.

## 14. موارد خارج از محدوده

- انتقال خود مدل‌های اصلی به سرور وب؛
- بازطراحی الگوریتم قیمت بدون shadow/evaluation جدا؛
- صف ارسال پست تلگرام و multi-publisher؛
- UI/UX عمومی خارج از dashboard و review مورد نیاز این pipeline؛
- ذخیره تمام پیام‌های گروه‌ها به‌صورت دائمی؛
- ذخیره هرثانیه‌ای مصنوعی XAU/USDT؛
- bot-to-bot transport؛
- استفاده از sync عمومی به‌عنوان bulk market event bus؛
- بازنویسی و داکرایزکردن legacy صرفاً برای یکسان‌سازی ظاهری؛ replacement جدید Docker-native است؛
- افزودن بورس در این نسخه؛
- production deploy بدون تایید مستقل.

## 15. تصمیم‌های بسته‌شده در مراحل 2 تا 4

تصمیم‌های storage/contract در مرحله 2 با ADR، migration rehearsal و restore test بسته شدند:

1. archive دائمی PostgreSQL 15 اختصاصی و جدا از product DB/Alembic است؛ SQLite سمت مدل فقط projection محلی می‌ماند؛
2. bind-root جدا روی سرور وب/داده با ظرفیت فعلی کافی است؛ paid volume فقط با evidence رشد فضا اضافه می‌شود؛
3. receiverها روی private IP دقیق و port `9443` با CA داخلی، leafهای جدا و HMAC دوکلیدی کار می‌کنند؛ endpoint از env می‌آید؛
4. raw/quarantine به‌ترتیب 3/14 روز، input ledger دائمی، RPO حداکثر 5 دقیقه و RTO حداکثر 60 دقیقه است؛
5. thresholdهای اولیه در gate receipt مرحله 2 ثبت شده‌اند و در مرحله 12 فقط با evidence بازار باز کالیبره می‌شوند؛
6. rollback window برابر هفت روز کامل بازار باز است؛
7. Telegram identity و متن خام منتخب فقط رمز‌شده روی وب، با decrypt محدود و قابل ممیزی برای reviewer/admin نگهداری می‌شوند.

تصمیم image در مرحله 3 بسته شد: baseهای Python 3.11 slim Bookworm، Dockerfile frontend و PostgreSQL 15 Alpine با digest ثابت و dependencyهای Stage 3 با version/hash قفل شدند. application image برای هر release از commit تمیز و `SOURCE_DATE_EPOCH` همان commit ساخته می‌شود و manifest digest نهایی هنگام انتشار همان release روی هر دو میزبان pin و تطبیق داده خواهد شد.

تصمیم capture در مرحله 4 بسته شد: هر حساب owner و spool محلی جدا دارد، sequence داخل
همان حساب سراسری است، ACK داخلی فقط بعد از fsync انجام می‌شود و parser مصرف‌کننده مستقل
است. cutover باید HMAC فعال Account 2 را حفظ کند و authority marker را فقط بعد از توقف
owner میزبان بسازد؛ اجرای overlap ممنوع است.

جزئیات و شواهد: [COIN_MARKET_DATA_STAGE2_CONTRACT_STORAGE.md](./COIN_MARKET_DATA_STAGE2_CONTRACT_STORAGE.md)

## 16. تعریف Done نهایی

این roadmap فقط وقتی تمام است که:

- همه captureها روی وب/داده single-owner و پایدار باشند؛
- تمام replacement serviceها با image SHA/digest قابل اثبات و از deploy رسمی اجرا شوند؛
- هیچ Python process جدید این pipeline مستقیماً روی host اجرا نشود؛
- database/session/model/state روی volumeهای پایدار و امن، نه container layer، باشند؛
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
