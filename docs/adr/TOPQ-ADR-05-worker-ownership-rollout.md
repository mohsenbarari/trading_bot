# TOPQ-ADR-05 — topology feederها، ownership و rollout

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Backend + Operations
- چالش‌ها: `TOPQ-C16`, `TOPQ-C27`, `TOPQ-C28`, `TOPQ-C39`, `TOPQ-C40`, `TOPQ-C41`, `TOPQ-C59`, `TOPQ-C60`, `TOPQ-C69`, `TOPQ-N17`

## تصمیم

- شش feeder قطعی: ثبت/کنترل آفر، edit کانال، معامله/درخواست، مدیریتی/سیستمی، وضعیت بازار و عملیات زمان‌دار.
- feeder فقط eligibility، internal rank، freshness، dependency، coalescing و fan-out را تعیین می‌کند. main queue فقط priority نهایی، lease، limiter، retry و Bot API را مالک است.
- handoff با dedupe identity و رابطه child/main اتمیک یا outbox بازیاب‌پذیر است. feedback نتیجه main تنها راه terminal شدن child است.
- callback deadlineدار مستقیماً وارد main می‌شود. OTP از shared queue مستثناست و همان transport امضاشده و receipt کوتاه‌عمر Redis فعلی را حفظ می‌کند؛ enqueue پایدار آن fail-closed است.
- broadcast یک in-flight برای هر campaign و حداکثر دو campaign `M6` هم‌زمان دارد؛ round-robin بر `last_released_at` است. retryable/ambiguous همان campaign و خطای bot/gateway همه campaignها را متوقف می‌کند.
- gateway envelope `ok` را مرجع می‌داند، client مشترک lifecycle-safe دارد و فقط modeهای allowlisted `main/test` را می‌پذیرد.
- runtime worker فقط روی foreign و پشت flag پیش‌فرض خاموش شروع می‌شود. CI/static guard تمام call siteهای مستقیم غیرمجاز را رد می‌کند.
- چند credential به معنی چند execution owner نیست: owner واحد `queue-v1` دو runner/claim lane داخلی و هم‌زمان برای `primary` و `channel_editor` دارد. هر lane فقط `bot_identity` خود را claim می‌کند، ولی هر دو همان جدول job، repository، state machine، lease/fencing، retry و feedback را استفاده می‌کنند.
- runner editor یک صف پایدار، state machine، retry owner، feeder یا API poller مستقل نیست. استقلال آن فقط ظرفیت اجراست تا backlog و priority primary موجب head-of-line blocking editor نشود.

## rollout

1. migration افزایشی و code سازگار با flag خاموش روی هر دو peer.
2. بازنویسی pure contract و تست mixed-version.
3. smoke و benchmark editor در staging با flag مستقل خاموش در production.
4. shadow planner غیرقابل‌promote حداکثر ۲۴ ساعت بدون send.
5. توقف producer/direct owner قدیمی در نقطه اتمیک و انتقال کل کانال به `queue-v1`؛ canary درصدی ممنوع.
6. فعال‌سازی editor فقط پس از گیت `TOPQ-ADR-07` و کنترل‌های production در ۳۰ دقیقه، ۲ ساعت و ۲۴ ساعت با ترافیک طبیعی.

## گزینه‌های ردشده

- دو execution owner یا state machine هم‌زمان، queue/retry/poller API مستقل editor، dispatcher تک‌اسلاتی مشترک برای منابع دو bot role، bulk enqueue بزرگ feeder، retry فنی در child، worker روی Iran و canary درصدی direct/queue.

## اثر بر داده، sync و failure mode

child identity و main job relation مطابق ADR-02 پایدارند. sync فقط intent را منتقل می‌کند و execution state foreign را برنمی‌گرداند. crash میان enqueue/link با replay همان dedupe key ترمیم می‌شود؛ خطای feedback child را terminal نمی‌کند تا نتیجه main دوباره خوانده شود. feature flag یکتای execution owner فقط میان binaryهایی معتبر است که آن flag را می‌شناسند؛ binary قدیمی `main` آن را enforce نمی‌کند و به‌تنهایی از double-send نسخه مختلط جلوگیری نمی‌کند.

## تست و observability

inventory machine-readable، صفر bypass، handoff crash، feedback/cancel/restart هر feeder، mixed-version exact-once logical effect، fairness چند campaign و startup/shutdown foreign-only تست می‌شوند. lifecycle test باید شروع/توقف اتمیک owner و هر دو lane، claim فیلترشده bot identity و ادامه editor زیر backlog اشباع primary را اثبات کند؛ خاموش‌شدن یکی نباید owner یا retry مستقل پنهان بسازد.

## rollback

claim جدید هر دو lane متوقف، in-flight و ambiguous reconcile، ownership اتمیک برگردانده و jobهای پایدار حفظ می‌شوند. توقف فقط lane editor از مسیر flag اختصاصی مجاز است، اما pendingهای آن route عوض نمی‌کنند. migration در rollback حذف نمی‌شود.

## الحاقیه ممیزی `2026-07-18`

- production composition باید credential registry و تمام dependencyهای lifecycle را صریح به supervisor تزریق کند؛ factory خام صفرآرگومانی معیار code-ready نیست.
- cutover نسخه مختلط فقط با ownership epoch بادوام یا choreography فیزیکی و تست‌شده `stop-old → drain/reconcile → start-new` مجاز است. topology فعلی single bot container می‌تواند این روش را پشتیبانی کند، اما باید rehearsal و شاهد عدم overlap داشته باشد.
- audit callsite باید control-flow-aware باشد؛ وجود tokenهای runtime در متن یک تابع، guard معتبر محسوب نمی‌شود.
