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
- شمار جاری: `100` callsite، بدون دستهٔ ناشناخته.
- اثر انگشت بازبینی‌شدهٔ فعلی: `e15b604e99e012f1156e5f0d93070470d58d6b7ea97f3f48853f567db68c1995`
- سه هویت فقط به‌خاطر جابه‌جایی خط بعد از `03be742b` حرکت کردند؛ طبقه و callee عوض نشد.

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
