# وضعیت جاری تحویل تلگرام Queue-v1

این سند منبع عملیاتی جاری است. checkpointها و handoffهای ژوئیهٔ ۲۰۲۶ شواهد تاریخی‌اند و حذف نمی‌شوند.

## قابلیت کد در برابر مالکیت اجرا

- در کد، `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=True` فقط یعنی قابلیت بررسی‌شده حاضر است.
- این پرچم به‌تنهایی مالکیت اجرا را عوض نمی‌کند.
- مالکیت اجرا فقط با هر سه کنترل صریح `TELEGRAM_DELIVERY_EXECUTION_OWNER`، `TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED` و `TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY` روی runtime بات فعال می‌شود.
- API/WebApp فقط قرارداد producer و expected-owner را می‌گیرند؛ token و worker اجرا روی آن‌ها ممنوع است.

## معماری فیزیکی جاری

- یک بات مرکزی و پنج publisher lane.
- API و WebApp روی سرور ایران staging.
- central bot و publisherها روی سرور بات.
- deploy رسمی هر دو سرور: `scripts/deploy_staging.sh`.
- head مهاجرت کد: `fb1c2d3e4f5a`.
- تست‌های PostgreSQL scratch صف باید همین head را انتظار داشته باشند؛ پین تاریخی `b986c7d8e0f1` دیگر در درخت migrations وجود ندارد.

## ارکستراسیون cutover

- اسکریپت رسمی: `scripts/cutover_telegram_delivery_queue_staging.py`
- دستورهای فعلی: `plan`, `status`, `backup`, `restore-probe`, `rehearse-rollback`, `apply`, `rollback`
- قرارداد نقش process در `core/telegram_delivery_cutover_contract.py` است
- سرویس‌های API/sync در `deploy/staging/docker-compose.staging.yml` token و worker اجرا را از env مشترک خالی می‌کنند
- `apply` فقط با عبارت تأیید، worktree پاک، و `main == origin/main` اجرا می‌شود؛ deploy رسمی هر دو سرور را به یک SHA می‌رساند
- اگر قفل کانال Queue-v1 خالی باشد، از `CHANNEL_ID` موجود کپی می‌شود؛ مقدار در گزارش چاپ نمی‌شود

## ماتریس زندهٔ authoritative

- منبع حقیقت پس از cutover: `scripts/run_telegram_publisher_live_matrix.py` با ۵۰۰ آفر.
- ماتریس B2B در `scripts/run_telegram_publisher_b2b_matrix.py` فقط حمل‌ونقل فرمان/رسید است.
- workload قدیمی Stage 4 با `1800 valid + 400 invalid` تاریخی است و برای این cutover تکرار نمی‌شود.

## ممیزی callsite

- اسکریپت: `scripts/audit_telegram_delivery_calls.py`
- شمار جاری: `103` callsite، بدون دستهٔ ناشناخته و بدون `durable_exempt`.
- اثر انگشت بازبینی‌شدهٔ فعلی: `18f80e229bdad8eff47b9aae9316b43cf3ccaeb4f143ebdc18a276306e876fdc`
- اثر انگشت پیش از سخت‌سازی صف OTP: `b106a19f532e7f6c6ecfc4e5f3a20e81a8f29427e77e9ba680dffd095153b451` (همان ۱۰۳ callsite؛ فقط جابه‌جایی شماره خط)
- دسته‌های جدید: `ephemeral_queue_execution=1` (ارسال OTP فقط در bot) و `operational_control=3` (ساخت Bot در `run_bot` و لینک عضویت بات).

## اندازه‌گیری staging در شروع مأموریت cutover

سرور بات / foreign:

- producer و execution owner برابر `legacy`
- workerهای legacy فعال؛ Queue-v1 خاموش؛ cutover ready خاموش
- multi-publisher و B2B فعال؛ هر پنج lane فعال و identity-bound
- health پیش از reconciliation: `stop` به‌خاطر `oldest_ready_age_stop`
- دو job آماده از نوع `overtime_owner_approval`؛ دامنهٔ هر دو terminal بود (`overtime_cancelled_by_requester` و `overtime_invalidated`) و آفرها `expired` بودند
- پس از مسیر رسمی `reconcile_telegram_delivery_ready_jobs.py` هر دو `expired_interaction` شدند و health برابر `continue` شد
- ارسال تلگرام و حذف SQL انجام نشد

سرور ایران / API:

- تصویر Docker با سرور بات یکی نیست؛ `RELEASE_SHA` خالی است
- head مهاجرت با سرور بات یکی است: `fb1c2d3e4f5a`
- producer و expected-owner و execution-owner روی `queue-v1` تنظیم شده‌اند، در حالی که اجرا روی سرور بات هنوز `legacy` است
- token ناشر و token مرکزی روی ایران نیست؛ یک token ویرایشگر کانال روی ایران دیده شد و باید قبل از cutover حذف شود
- این ترکیب split-brain قراردادی است و تا اصلاح، cutover `NO-GO` می‌ماند

production در این سند تغییر نمی‌کند و مجوز جداگانه می‌خواهد.

## وضعیت پس از cutover staging

اندازه‌گیری شروع مأموریت در بخش بالا حفظ شده است. وضعیت جاری پس از apply و ماتریس زنده:

- شاخه `main`، HEAD `4e79d1c6`، هم‌تراز با `origin/main`
- هر دو سرور staging روی همین SHA و schema `fb1c2d3e4f5a`
- بات foreign: producer و execution owner برابر `queue-v1`؛ worker و cutover gate روشن؛ multi-publisher و B2B روشن
- API ایران و API foreign: producer/expected برابر `queue-v1`؛ execution owner برابر `legacy` و worker خاموش (قرارداد API)
- token مرکزی، ناشر، ویرایشگر و پایش روی ایران غایب است
- health برابر `continue`؛ job باز غیرپایانی صفر؛ فرمان B2B باز صفر
- ماتریس زندهٔ ۵۰۰تایی `telegram-live-matrix-20260816t231306z-08c5c6b74635` پاس شد؛ هر پنج lane استفاده شد
- مهلت انقضای ماتریس پس از پاک‌سازی رسمی به ۲ دقیقه برگشت
- outbox بدون recipient اکنون در health یک stop reason است؛ فقط intent بدون handoff یا متصل به job ناموفقِ terminal با `scripts/reconcile_telegram_notification_outbox_orphans.py` به terminal `skipped` آشتی می‌شود و سایر ناسازگاری‌ها fail-closed می‌مانند
- پاک‌سازی fixture رسمی، `telegram_notification_outbox` و change-logهای همان شناسه‌ها را با هم حذف می‌کند تا residue مصنوعی به quarantine همگام‌سازی تبدیل نشود
- production دست‌نخورده ماند و فعال‌سازی Queue-v1 در آن مجوز جداگانه می‌خواهد

## وضعیت پس از سخت‌سازی صف OTP و بازاعتبار ۱۰۰ آفره

شواهد تاریخی cutover و ماتریس ۵۰۰تایی بالا حفظ می‌شوند. ماتریس ۱۰۰تایی بازاعتبار همین اصلاح است، نه جایگزین rollout قبلی.

- شاخه `main`، HEAD `6bc94b410e017c28bed9f97158f760c75cead573`، هم‌تراز با `origin/main`
- هر دو سرور staging روی همین SHA و schema `fb1c2d3e4f5a`
- بات foreign: owner=`queue-v1`؛ worker و cutover روشن؛ پنج lane حاضر
- API ایران و API foreign: owner=`producer-only`؛ expected=`queue-v1`؛ worker خاموش؛ Bot token غایب
- endpoint منسوخ `/api/auth/webapp-login` همچنان `410 Gone` است
- صف OTP: پس از نتیجهٔ نهایی ACK+DELETE؛ `XLEN=0` و `XPENDING=0`؛ health `pending_count=0` و oldest خالی
- max-deliveries از `XPENDING RANGE` / `times_delivered` خوانده می‌شود؛ quarantine مصنوعی یک‌بار و بدون payload
- SMS staging: `BLOCKED — VERIFIED STAGING CREDENTIAL ABSENT`؛ fallback روی ایران و foreign صریحاً false
- callsite inventory: `103`؛ اثر انگشت جاری `18f80e229bdad8eff47b9aae9316b43cf3ccaeb4f143ebdc18a276306e876fdc`
- ماتریس بازاعتبار `telegram-live-matrix-20260817t152138z-4053a0a01a8e` با پروفایل `revalidation-100` پاس شد: ۱۰۰/۱۰۰ صف، ارسال کانال، ack، terminal و WebApp؛ completed ۲۳ / expired ۷۷؛ هر پنج lane استفاده شد
- ماتریس تاریخی ۵۰۰تایی `telegram-live-matrix-20260817t121645z-e4215158d505` و `telegram-live-matrix-20260816t231306z-08c5c6b74635` حذف یا بازنویسی نشد
- مهلت انقضا هنگام ماتریس ۱۰۰تایی ۶ دقیقه بود و پس از پاک‌سازی رسمی به ۲ دقیقه برگشت
- سرویس جداگانهٔ estimator برای staging وجود ندارد؛ داشبورد `estimator-live` تولید است و در این مأموریت restart نشد
- production همچنان Legacy است و Queue-v1 در آن بدون مجوز جداگانهٔ مالک ممنوع است
