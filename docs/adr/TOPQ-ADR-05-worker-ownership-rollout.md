# TOPQ-ADR-05 — topology feederها، ownership و rollout

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Backend + Operations
- چالش‌ها: `TOPQ-C16`, `TOPQ-C27`, `TOPQ-C28`, `TOPQ-C39`, `TOPQ-C40`, `TOPQ-C41`, `TOPQ-C59`, `TOPQ-C60`, `TOPQ-N17`

## تصمیم

- شش feeder قطعی: ثبت/کنترل آفر، edit کانال، معامله/درخواست، مدیریتی/سیستمی، وضعیت بازار و عملیات زمان‌دار.
- feeder فقط eligibility، internal rank، freshness، dependency، coalescing و fan-out را تعیین می‌کند. main queue فقط priority نهایی، lease، limiter، retry و Bot API را مالک است.
- handoff با dedupe identity و رابطه child/main اتمیک یا outbox بازیاب‌پذیر است. feedback نتیجه main تنها راه terminal شدن child است.
- callback/OTP deadlineدار مستقیماً وارد main می‌شود.
- broadcast یک in-flight برای هر campaign و حداکثر دو campaign `M6` هم‌زمان دارد؛ round-robin بر `last_released_at` است. retryable/ambiguous همان campaign و خطای bot/gateway همه campaignها را متوقف می‌کند.
- gateway envelope `ok` را مرجع می‌داند، client مشترک lifecycle-safe دارد و فقط modeهای allowlisted `main/test` را می‌پذیرد.
- runtime worker فقط روی foreign و پشت flag پیش‌فرض خاموش شروع می‌شود. CI/static guard تمام call siteهای مستقیم غیرمجاز را رد می‌کند.

## rollout

1. migration افزایشی و code سازگار با flag خاموش روی هر دو peer.
2. بازنویسی pure contract و تست mixed-version.
3. shadow planner غیرقابل‌promote حداکثر ۲۴ ساعت بدون send.
4. توقف producer/direct owner قدیمی در نقطه اتمیک و انتقال کل کانال به `queue-v1`؛ canary درصدی ممنوع.
5. کنترل‌های production در ۳۰ دقیقه، ۲ ساعت و ۲۴ ساعت فقط با ترافیک طبیعی.

## گزینه‌های ردشده

- دو execution owner هم‌زمان، bulk enqueue بزرگ feeder، retry فنی در child، worker روی Iran و canary درصدی direct/queue.

## اثر بر داده، sync و failure mode

child identity و main job relation مطابق ADR-02 پایدارند. sync فقط intent را منتقل می‌کند و execution state foreign را برنمی‌گرداند. crash میان enqueue/link با replay همان dedupe key ترمیم می‌شود؛ خطای feedback child را terminal نمی‌کند تا نتیجه main دوباره خوانده شود. feature flag یکتای execution owner از double-send نسخه مختلط جلوگیری می‌کند.

## تست و observability

inventory machine-readable، صفر bypass، handoff crash، feedback/cancel/restart هر feeder، mixed-version exact-once logical effect، fairness چند campaign و startup/shutdown foreign-only تست می‌شوند.

## rollback

claim جدید متوقف، in-flight و ambiguous reconcile، ownership اتمیک برگردانده و jobهای پایدار حفظ می‌شوند. migration در rollback حذف نمی‌شود.
