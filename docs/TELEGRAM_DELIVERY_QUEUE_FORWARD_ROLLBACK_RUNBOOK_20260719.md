# Telegram delivery queue forward-rollback runbook

## تصمیم قطعی

Rollback این قابلیت با downgrade دیتابیس انجام نمی‌شود. migration تاریخی `5061c56d11e7` در حذف unique constraint بدون نام شکست می‌خورد و این مشکل پیش از برنچ صف وجود داشته است. علاوه بر آن، release قدیمی اگر migration stage خود را روی head جدید اجرا کند ممکن است revisionهای جدید را نشناسد.

دو مسیر مجاز وجود دارد:

1. **Forward rollback:** schema فعلی حفظ و application به release سازگار قبلی با مالک `legacy` برگردانده می‌شود؛ migration stage آن release اجرا نمی‌شود.
2. **Restore:** backup هم‌نسخه release قبلی در دیتابیس جدا restore و پس از verification مقصد جابه‌جا می‌شود. restore ناقص یا downgrade درجا مجاز نیست.

## ترتیب forward rollback

1. release SHA هدف، image immutable و checksum آن ثبت شود.
2. backup تازه همراه checksum manifest ساخته و امکان restore مستقل آن تأیید شود.
3. پذیرش producerهای queue متوقف شود؛ هیچ deployment جدید یا feeder جدید شروع نشود.
4. worker فعلی اجازه drain داشته باشد تا active queue job، lease، ambiguity، pending provider outcome، resume operation و runtime gate همگی صفر شوند.
5. checker فقط‌خواندنی اجرا شود و `decision=ready` بدهد.
6. تمام processهای candidate متوقف و نبود owner هم‌زمان اثبات شود.
7. config release هدف دقیقاً این مقادیر را داشته باشد:

```text
TELEGRAM_DELIVERY_EXECUTION_OWNER=legacy
TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED=false
TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY=false
```

8. migration service در rollout release قدیمی **skip** شود. دستورهای deploy عادی که به‌طور پیش‌فرض `manage.py`/`alembic upgrade head` اجرا می‌کنند برای این قدم قابل استفاده مستقیم نیستند.
9. application و bot release قبلی با schema فعلی شروع شوند؛ فقط workerهای legacy باید حاضر باشند.
10. smoke رفتار legacy و شمار executor اجرا و سپس producerها باز شوند.

شکست هر قدم برابر `NO-GO` است. در این حالت candidate دوباره با همان schema roll-forward می‌شود یا backup در دیتابیس جدا restore می‌شود؛ حذف table/column یا اجرای downgrade برای نجات rollout ممنوع است.

## checker مرجع

DB URL فقط از `TELEGRAM_QUEUE_ROLLBACK_DATABASE_URL` خوانده می‌شود. برای rehearsal synthetic:

```bash
python scripts/check_telegram_delivery_forward_rollback.py \
  --environment synthetic-test \
  --expected-database-name telegram_queue_stage3_<name>_test \
  --expected-schema-head <candidate-head> \
  --producer-quiesced \
  --migration-stage-skipped \
  --backup-manifest-sha256 <64-hex-checksum> \
  --report tmp/telegram-queue-evidence/rollback-readiness.json
```

checker نخست transaction را `READ ONLY` می‌کند، schema head و config legacy را می‌سنجد و فقط countهای aggregate را گزارش می‌دهد. هیچ gateway/provider import، row lock، mutation یا schema command ندارد. گزارش به‌صورت خودکار security scan می‌شود.

Production فقط با `--ack-production-read-only` پذیرفته می‌شود؛ این flag فقط اجازه inspection می‌دهد و هیچ action یا bypass readiness نمی‌سازد.

## blockerها

- `producer_quiescence_not_confirmed`
- `rollback_migration_stage_not_skipped`
- `rollback_backup_manifest_not_verified`
- `schema_head_mismatch`
- `legacy_runtime_config_invalid`
- `active_queue_jobs_present`
- `leased_queue_jobs_present`
- `unresolved_queue_jobs_present`
- `pending_provider_outcomes_present`
- `incomplete_resume_operations_present`
- `active_runtime_gates_present`

وجود حتی یک blocker مانع توقف candidate و شروع legacy است. terminal history می‌تواند در tableهای افزایشی باقی بماند؛ release legacy آن‌ها را نادیده می‌گیرد و retention بعد از بازگشت candidate ادامه می‌یابد.

## شواهد اجباری قبل از production

- rehearsal کامل stop-new/drain/stop-old/start-target روی staging با timestamp processها؛
- شمار executor برابر یک در تمام transition؛
- گزارش checker با security scan پاک و checksum؛
- اثبات skip migration stage release هدف؛
- smoke legacy با schema candidate؛
- تمرین restore backup در دیتابیس جدا؛
- تصمیم اپراتور و release SHAهای مبدأ/مقصد.
