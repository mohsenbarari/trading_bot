# Runbook انتقال Blue/Green خط خصوصی بازار

وضعیت: ابزار آماده و تست‌شده؛ اجرای زنده فقط با رسیدهای دقیق هر مرحله.

این runbook برای انتقال stack قدیمی `PRIVATE_SHADOW` به پروژهٔ Compose جداگانهٔ
`PRIVATE_PRIMARY` است. پایگاه، sessionها، spoolها، outboxها و checkpointها همان bind mountهای
پایدار را نگه می‌دارند. کانتینرهای قبلی حذف نمی‌شوند؛ متوقف می‌مانند تا rollback دقیق به همان
شناسه، image و restart policy ممکن باشد.

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

## ابزار gate

`scripts/upgrade_market_pipeline_bluegreen.py` این transitionها را fail-closed نگه می‌دارد:

1. `plan`: ثبت شناسه/image/revision/restart policy تمام کانتینرهای قدیم، digest envها، markerها
   و خالی بودن پروژهٔ جدید؛
2. `quiesce-workload`: توقف فقط workloadهای ثبت‌شده با ترتیب امن؛
3. `quiesce-database`: پذیرش فقط backup receipt سالم و سپس توقف دیتابیس قدیم؛
4. `authorize-captures`: بررسی سلامت base جدید و انتقال اتمیک markerها؛
5. `start-captures`: شروع captureها به ترتیب external، account1 و account2؛
6. `verify`: اثبات نبود owner قدیم، inventory دقیق جدید، image/release دقیق و markerهای درست؛
7. `rollback`: حذف فقط کانتینرهای پروژهٔ جدید و بازگرداندن markerها و همان کانتینرهای قدیم.

تأیید لفظی ابزار دقیقاً `upgrade-market-pipeline-bluegreen` است. journal و env ورودی باید فایل
معمولی، تک‌لینک، متعلق به کاربر اجرا و با mode `0600` باشند. مسیرهای امن و digestها در گزارش
عملیاتی ثبت می‌شوند؛ secret یا payload در گزارش و مخزن قرار نمی‌گیرد.

## ترتیب اجرای رسمی

### A. Binding و preflight

- شاخه، HEAD، tree، release SHA، image ID مستقل هر میزبان و digest env pair ثبت شود.
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
- رشد sequence، تخلیه outbox، عدم duplicate/gap/rejection و single-owner جداگانه بررسی شود.

### E. اثبات end-to-end و promotion محصول

- Fact روی private IP و mTLS از web به bot منتقل و ACK پایدار شود.
- parser/lifecycle روی web اجرا شود و bot فقط fact/features قرارداد را دریافت کند.
- snapshot برگشتی روی lane `PRIVATE_PRIMARY` دریافت شود.
- همهٔ ۱۴ rate حاضر، snapshot تازه، source binding دقیق و وضعیت مدل `OK` باشد.
- digest snapshot روی bot و web برابر باشد.
- فقط پس از تمام موارد بالا، ابزار رسمی تغییر source ابتدا روی staging و پس از soak روی production
  اجرا شود.
- اگر بازار بسته یا snapshot تازه و معتبر موجود نیست، promotion انجام نمی‌شود و feed محصول
  روی legacy می‌ماند؛ دادهٔ ساختگی مجاز نیست.

### F. soak و بازنشستگی legacy

- restart count، queue lag، ACK latency، capture/parse lag، model age، unknown schema، gap،
  duplicate و disk/RAM پایش شود.
- rollback window کامل حفظ شود.
- legacy فقط بعد از اثبات پایدار PRIVATE_PRIMARY از authority خارج می‌شود؛ داده و backup آن در
  این مرحله پاک نمی‌شود.

## rollback

rollback باید پیش از تغییر authority محصول نیز قابل اجرا باشد. gate:

1. restart policy و اجرای سرویس‌های جدید را متوقف می‌کند؛
2. فقط کانتینرهای پروژهٔ جدید را حذف می‌کند و bind mount را دست نمی‌زند؛
3. markerهای قبلی را با payload و release قبلی بازمی‌گرداند؛
4. همان container IDهای قدیم را با restart policy ثبت‌شده شروع می‌کند؛
5. سلامت و inventory قدیم و خالی شدن پروژهٔ جدید را الزام می‌کند.

اگر authority محصول قبلاً تغییر کرده باشد، ابتدا source محصول با ابزار رسمی به snapshot معتبر
قبلی برمی‌گردد، سپس rollback runtime انجام می‌شود. پایین آوردن checkpoint، حذف outbox یا پاک‌کردن
state برای رفع خطا ممنوع است.

## معیار Done

- تست‌های واحد gate و ابزارهای backup/migration/rollout سبز؛
- backup تازه، restore-smoke و off-host encrypted receipt سبز؛
- schema `[1,2,3]` و ۲۸ جدول با fact count بدون افت؛
- یک owner دقیق برای هر capture و هر endpoint؛
- fact، ACK و snapshot end-to-end روی شبکه خصوصی؛
- ۱۴/۱۴ rate تازه و معتبر؛
- staging و production product source با receipts جدا؛
- soak بدون gap/duplicate/rejection و با restart صفر؛
- rollback proof بدون حذف state؛
- اسناد و حافظهٔ پروژه به‌روز.
