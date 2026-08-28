# Runbook انتقال Blue/Green خط خصوصی بازار

وضعیت: ابزار آماده و تست‌شده؛ اجرای زنده فقط با رسیدهای دقیق هر مرحله.
authority محصول تا تکمیل تمام گیت‌ها و CAS نهایی دقیقاً `LEGACY` می‌ماند.

## مجوز فوری مالک — 2026-08-28

به‌علت قطع تغذیهٔ Product از مسیر legacy، مالک انتقال مستقیم production به
`PRIVATE_PRIMARY` را تصویب کرده است. این تصمیم، پیش‌نیاز staging، مشاهدهٔ چند جلسهٔ بازار و
soak طولانی را برای همین cutover حذف می‌کند؛ اما هیچ‌یک از گیت‌های صحت حذف نشده است: release
یکتا، reconciliation بدون حذف، backup/restore تازه و نسخهٔ رمز‌شدهٔ off-host، migration
دوباره‌پذیر، single-owner، نبود gap داخلی/duplicate/rejection، catch-up چندروز اخیر، snapshot V2 با
grid کامل و status=`OK`، ACK و view یکسان، تغییر CAS منبع در آخر، rollback آماده و
postcheck کوتاه همچنان اجباری‌اند. این بند برای این cutover بر بندهای تاریخی Stage 12/13/14 و
الزام full-session مقدم است؛ مجوز ساخت داده یا پذیرش snapshot کهنه/ناقص نیست.

مرز catch-up مالک برابر `2026-08-25T09:33:00Z` است. فاصلهٔ واقعی در تاریخچهٔ upstream
مانع نیست و نباید با دادهٔ ساختگی پر شود. برای هر رویداد واقعاً قابل‌بازیابی از `GROUP_1`،
`GROUP_2` و `MELTED_PRIMARY_FLOW` در این بازه باید یک پایان ماندگارِ `parsed` یا فیلترِ مجاز
ثبت شود و پیوستگی sequence حمل داخلی حفظ شود. چون بعضی متن‌ها ذاتاً fact مدل‌پذیر تولید
نمی‌کنند، این گیت با اثبات جداگانهٔ fact تکمیل می‌شود: هر کدام از ۹ منبع الزامی باید دست‌کم یک fact پذیرفته‌شده،
archive و ACK یکسان، حضور در Market Store بات و trace واقعی در ورودی هش‌شدهٔ snapshot اصلی
داشته باشند. وجود raw، health یا تنظیم منبع به‌تنهایی پاس نیست.
این نه منبع یک subset الزامی از ورودی‌های واقعی هستند، نه شرط `total_sources == 9`؛
منبع مجاز اضافی ممنوع نیست و تمام کدهای این subset باید یکتا باشند.

horizon نقطه‌زمانی هفت‌روزهٔ موتور نرخ برای anchorهای سکه و driverها همچنان الزامی است.
مرز catch-up بالا این horizon را waive نمی‌کند. تنها `COIN_ONE_GRAM/CASH` و
`COIN_ONE_GRAM/TOMORROW` می‌توانند در نبود طبیعی anchor هم‌کالا با method دقیق
`ABSTAIN_NO_SAFE_SAME_COMMODITY_ANCHOR` و reason دقیق
`NO_SAFE_SAME_COMMODITY_ANCHOR`، در حالی که melted همان settlement تازه است، `NO_DATA`
باشند. نبود driver، کهنگی underlying یا هر gap/quarantine/rejection همچنان fail-closed است.

`occurrences` شمارندهٔ دفعات مشاهدهٔ یک marker تکراری است، نه cardinality رویدادهای
یکتا. fixture رسمی ۴۸۹ occurrence را برای پنج رویداد یکتا دارد؛ پس ۴۸۹ نه شمار رویداد است
و نه حداقلی که باید با replay ساخته شود.

سامانهٔ legacy در وضعیت جاری برای هر سه ورودی `GROUP_1`، `GROUP_2` و
`MELTED_PRIMARY_FLOW` دقیقاً `NONE` است؛ بنابراین مرجع داده،
oracle مقایسه و rollback داده‌ای نیست. rollback آن صرفاً یک مسیر فنی برای بازگرداندن سرویس محصول
است. سامانهٔ جدید باید سلامت هر کدام از نه ورودی الزامی زیر را مستقل ثابت کند:
`MELTED_PRIMARY_FLOW`، `GROUP_1`، `GROUP_2`، `MELTED_AGGREGATE`، `MELTED_FLOW`،
`USD_HERAT`، `XAUUSD`، `WALLEX_PUBLIC_API` و `BINANCE_PAXG_PUBLIC_API`.

این runbook برای انتقال stack قدیمی `PRIVATE_SHADOW` به پروژهٔ Compose جداگانهٔ
`PRIVATE_PRIMARY` است. پایگاه، sessionها، spoolها، outboxها و checkpointها همان bind mountهای
پایدار را نگه می‌دارند. کانتینرهای قبلی حذف نمی‌شوند؛ تا پیش از
`PRIMARY_COMMITTED` متوقف می‌مانند تا rollback دقیق به همان شناسه، image و restart policy
ممکن باشد؛ پس از آن commit، اجرای مجددشان ممنوع است.

## اصول غیرقابل‌چشم‌پوشی

- پروژهٔ قدیم و جدید نام یکسان ندارند.
- پیش از توقف دیتابیس، workloadهای قدیم متوقف و یک backup تازه با restore-smoke دقیق ساخته
  می‌شود.
- backup تازه به‌صورت رمز‌شده روی میزبان دیگر نیز نگه‌داری و digest متنِ بازیابی‌شده با مبدا
  تطبیق داده می‌شود.
- دیتابیس فقط پس از receipt سبز backup متوقف می‌شود.
- migration با ابزار release-bound اجرا می‌شود و اجرای دوم باید no-op باشد.
- receiverها پیش از sender/processor و captureها آخر از همه بالا می‌آیند.
- marker اختیار capture فقط بعد از توقف تمام ownerهای قدیم و سلامت base جدید عوض می‌شود.
- owner هم‌زمان قدیم و جدید ممنوع است.
- تغییر feed محصول آخرین گام است؛ استقرار pipeline به‌تنهایی authority محصول را عوض نمی‌کند.
- هیچ volume، data root، session، checkpoint، outbox، image یا کانتینر قدیمی حذف نمی‌شود.
- دادهٔ ساختگی، waiver گیت، حذف/quarantine-پاک‌کنی برای سبزکردن شمارش، reset
  شمارنده یا checkpoint، پایین‌آوردن ACK و حذف outbox/raw/fact ممنوع است.
- transport ات‌لیست‌وانس ممکن است یک delivery را برای retry تکرار کند؛ duplicate
  application رویداد/fact ممنوع است. گیت باید این دو معنا را جدا بشمارد.

## ابزار gate

`scripts/upgrade_market_pipeline_bluegreen.py` این transitionها را fail-closed نگه می‌دارد:

1. `plan`: ثبت شناسه/image/revision/restart policy تمام کانتینرهای قدیم، digest envها، markerها
   و خالی بودن پروژهٔ جدید؛
2. `quiesce-workload`: توقف فقط workloadهای ثبت‌شده با ترتیب امن؛
3. `quiesce-database`: پذیرش فقط backup receipt سالم و سپس توقف دیتابیس قدیم؛
4. `authorize-captures`: بررسی سلامت base جدید و انتقال اتمیک markerها؛
5. `start-captures`: شروع captureها به ترتیب external، account1 و account2؛
6. `verify`: اثبات نبود owner قدیم، inventory دقیق جدید، image/release دقیق و markerهای درست؛
7. `rollback`: فقط پیش از `PRIMARY_COMMITTED`، حذف کانتینرهای پروژهٔ جدید و
   بازگرداندن markerها و همان کانتینرهای قدیم.

تأیید لفظی ابزار دقیقاً `upgrade-market-pipeline-bluegreen` است. journal و env ورودی باید فایل
معمولی، تک‌لینک، متعلق به کاربر اجرا و با mode `0600` باشند. مسیرهای امن و digestها در گزارش
عملیاتی ثبت می‌شوند؛ secret یا payload در گزارش و مخزن قرار نمی‌گیرد.

## ترتیب اجرای رسمی

### A. Binding و preflight

- checkout باید `main` تمیز و دقیقاً برابر `origin/main` باشد؛ dirty tree، commit فقط
  محلی، override محتوایی یا release تقریبی ممنوع است.
- HEAD، tree، release SHA، source signature، content ID، OCI label، image ID مستقل
  هر میزبان و digest env pair ثبت شود. release manifest، control payload، migration
  receipt، image label و promotion receipt باید به همان exact release بایت‌به‌بایت متصل باشند.
- image application از نظر Config/RootFS/label با هر دو میزبان تطبیق داده شود؛ image ID محلی
  می‌تواند متفاوت باشد.
- preflight رسمی web و bot باید `PASS` باشد.
- پروژهٔ قدیم دقیقاً inventory انتظار را داشته باشد و پروژهٔ جدید کاملاً خالی باشد.
- `plan` روی هر دو میزبان اجرا و journalهای `0600` ثبت شود.

### B. Quiesce و backup تازه

- workload وب با captureها در ابتدا و workload بات با snapshot sender در ابتدا متوقف شود.
- دیتابیس قدیم هنوز روشن بماند.
- backup تازه ساخته و روی restore database موقت بازیابی شود؛ schema versions، تعداد جدول و
  تعداد facts باید دقیقاً برابر باشد.
- backup رمز‌شده به میزبان مقابل انتقال یابد و decrypt-stream digest با digest فایل مبدا برابر
  شود.
- فقط پس از این receipts، `quiesce-database` اجرا شود.

### C. Migration و base جدید

- migration release-bound، receipt را با `backup-env-file` پروژهٔ قدیم و target را با env
  `PRIVATE_PRIMARY` پروژهٔ جدید اعتبارسنجی می‌کند؛ دو پروژه باید متفاوت و data root و هویت
  PostgreSQL باید دقیقاً یکسان باشند.
- pass اول فقط `applied` یا `already_current` و pass دوم فقط `already_current` مجاز است.
- فقط دیتابیس جدید باید در این نقطه روشن باشد.
- rollout receiver-first برای bot و web اجرا شود؛ captureها هنوز ممنوع‌اند.
- inventory پایهٔ وب: database، snapshot receiver، processor و fact-sync worker.
- inventory پایهٔ بات: fact receiver، adapter، estimator و snapshot sender.

### D. انتقال اختیار capture

- هیچ سرویس پروژهٔ قدیم روی هیچ نقش در حال اجرا نباشد.
- base جدید healthy و دارای image/release دقیق باشد.
- `authorize-captures` markerهای account1/account2 را اتمیک به release جدید می‌برد.
- `start-captures` هر سه capture را بالا می‌آورد.
- رشد sequence، تخلیه outbox، نبود gap/rejection و single-owner جداگانه بررسی شود. تحویل تکراری
  در حمل at-least-once مجاز است، اما اعمال تکراری ممنوع و باید با checkpoint پیوسته اثبات شود.

### E. اثبات end-to-end و promotion محصول

- در تمام capture، replay، backfill، audit و readiness، source محصول دقیقاً
  `LEGACY` می‌ماند؛ هیچ گام میانی حق نوشتن `PRIVATE_PRIMARY` در authority محصول را ندارد.
- Fact روی private IP و mTLS از web به bot منتقل و ACK پایدار شود.
- parser/lifecycle روی web اجرا شود و bot فقط fact/features قرارداد را دریافت کند.
- snapshot برگشتی روی lane `PRIVATE_PRIMARY` دریافت شود.
- هر ۱۴ سلول حاضر، snapshot تازه، source binding دقیق و وضعیت مدل `OK` باشد. نرخ‌های
  داده‌دار `ESTIMATED` و حداکثر دو سلول یک‌گرمی با قرارداد محدود بالا `NO_DATA` باشند.
- digest snapshot روی bot و web برابر باشد.
- فقط پس از تمام موارد بالا، ابزار رسمی تغییر source با receipt مستقل روی production اجرا شود.
- بسته‌بودن بازار به‌تنهایی مانع نیست؛ بااین‌حال snapshot باید در همان لحظه تازه و
  status=`OK` باشد. snapshot کهنه، snapshot سراسری `SAFE_NO_DATA`، `NO_DATA` خارج از
  استثنای محدود یک‌گرمی یا grid ناقص promotion را متوقف می‌کند و دادهٔ ساختگی مجاز نیست.

### F. postcheck کوتاه و بازنشستگی legacy

- restart count، queue lag، ACK latency، capture/parse lag، model age، unknown schema، gap،
  duplicate و disk/RAM پایش شود.
- برای این cutover فوری چند چرخهٔ متوالی و یک restart proof کافی است؛ rollback window کامل
  حفظ شود.
- legacy فقط بعد از اثبات پایدار PRIVATE_PRIMARY از authority خارج می‌شود؛ داده و backup آن در
  این مرحله پاک نمی‌شود.

## rollback

rollback دو مسیر متفاوت دارد و مرز آن commit اتمی `PRIMARY_COMMITTED` است:

### پیش از `PRIMARY_COMMITTED`

1. restart policy و اجرای سرویس‌های جدید متوقف می‌شود؛
2. فقط کانتینرهای پروژهٔ جدید حذف می‌شوند و bind mount دست‌نخورده می‌ماند؛
3. markerها با payload و release قبلی برمی‌گردند؛
4. همان container ID/restart policyهای ثبت‌شدهٔ قبلی بازمی‌گردند؛
5. inventory و سلامت پروژهٔ قدیم و خالی‌بودن پروژهٔ جدید اثبات می‌شود.

### پس از `PRIMARY_COMMITTED`

1. rollback اجرایی به runtime یا collectorهای قدیمی ممنوع است؛ capture/archive
   `PRIVATE_PRIMARY` owner زنده می‌ماند و runtime فقط forward-repair می‌شود.
2. ابتدا مقدار authority محصول با CAS به بایت‌های دقیق پیشین برمی‌گردد؛ mismatch
   یعنی توقف fail-closed، نه force-write.
3. سپس محصول به‌صورت محدود روی `LEGACY` قرار می‌گیرد و inference خاموش می‌شود؛
   legacy برای سه منبع فوق oracle یا feed جایگزین نیست.
4. پایین‌آوردن checkpoint/ACK، حذف outbox/raw/fact/state، reset شمارنده،
   waiver یا ساخت داده برای رفع خطا در هر دو مسیر ممنوع است.

## گیت رسمی تست

`unittest discover` فایل‌های pytest-style سازنده/کنترلر/outbox/promotion را
جمع نمی‌کند. هر دو فرمان زیر روی همان commit اجباری‌اند؛ حذف pytest یا اتکا
فقط به discover پوشش را پنهان می‌کند.

ابزارهای `PRIVATE_PRIMARY` و تست‌های pytest-style آن‌ها باید با
`PYTHONWARNINGS=error` اجرا شوند. سوئیت کامل `unittest discover` به‌خاطر
هشدار شخص‌ثالث `python_multipart` در Starlette/FastAPI با
`PYTHONWARNINGS=error` در ورود ماژول می‌شکند؛ آن سوئیت با
`APP_ENV_FILE=config/unit-test.env.example` و بدون تبدیل همهٔ هشدارها به
خطا اجرا می‌شود.

```bash
APP_ENV_FILE=config/unit-test.env.example \
  python3 -m unittest discover -s tests -t . -p 'test_*.py'

PYTHONWARNINGS=error APP_ENV_FILE=config/unit-test.env.example \
  python3 -m pytest -q \
    tests/test_build_production_private_primary_choreography_plan.py \
    tests/test_production_private_primary_choreography.py \
    tests/test_promote_production_private_primary_product.py \
    tests/test_reconcile_estimator_snapshot_publication_outbox.py \
    tests/test_verify_production_private_primary_promotion.py
```

CI همین دو اجرا را در `.github/workflows/coverage-report.yml` الزامی کرده است.

## معیار Done

- تست‌های واحد gate و ابزارهای backup/migration/rollout سبز؛
- backup تازه، restore-smoke و off-host encrypted receipt سبز؛
- schema `[1,2,3]` و ۲۸ جدول با fact count بدون افت؛
- یک owner دقیق برای هر capture و هر endpoint؛
- fact، ACK و snapshot end-to-end روی شبکه خصوصی؛
- grid کامل نرخ تازه و معتبر، با `NO_DATA` محدود یک‌گرمی فقط تحت قرارداد اثبات‌شدهٔ بالا؛
- production product source با receipt مستقل و CAS-bound؛
- postcheck کوتاه بدون gap/rejection/duplicate application و با restart سالم؛
- rollback proof بدون حذف state؛
- اسناد و حافظهٔ پروژه به‌روز.
