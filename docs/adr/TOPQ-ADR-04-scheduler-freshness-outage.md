# TOPQ-ADR-04 — scheduler، fairness، freshness و outage

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Backend + Operations
- چالش‌ها: `TOPQ-C10`, `TOPQ-C12`, `TOPQ-C25`, `TOPQ-C26`, `TOPQ-C30`, `TOPQ-C31`, `TOPQ-C37`, `TOPQ-C42`, `TOPQ-C56`, `TOPQ-C57`, `TOPQ-C58`

## تصمیم

- صف اصلی تنها scheduler Bot API با اولویت `M0..M7` است. ترتیب `M0`: callback/OTP، اعلان معامله عبورکرده از پنج ثانیه، publication آفر.
- اعلان هر recipient معامله در `M1` شروع و مستقل در `trade_committed_at+5s` به `M0` ارتقا می‌یابد؛ cooldown و retry_after دور زده نمی‌شود.
- feeder edit ترتیب partial active، traded، expired، cancelled، سایر edit و repair را همیشه اعمال می‌کند. هر Offer یک edit مؤثر دارد و partial terminal‌شده reclassify می‌شود.
- stale edit بالای پنج دقیقه در stale bucket همان طبقه است. پس از هر ۲۰ edit تازه، یک stale واجد ارسال رزرو می‌شود؛ priority اصلی تغییر نمی‌کند.
- `M6/M7` فقط ظرفیت باقی‌مانده می‌گیرند. عبور max-age alert/SLO breach است، نه priority inversion.
- 429 مقدار خام `retry_after + safety_margin` را بدون cap اعمال می‌کند. safety margin اولیه `100ms` است و interval/safety فقط با Stage 4 تنظیم می‌شوند.
- limiter برای هر `bot_identity` بودجه token جدا و برای destination بودجه مشترک دارد: 429 ابتدا token/lane مقصد را می‌بندد؛ probe فقط مطابق state machine کنترل‌شده مجاز است. editor دوم تا پایان Stage 4 حق مستقل فرض‌کردن ظرفیت همان کانال را ندارد.
- هر retry پس از claim و بلافاصله قبل از side effect revalidate می‌شود. payload یا دکمه stale به `SUPERSEDED/EXPIRED_INTERACTION` می‌رود.

## گزینه‌های ردشده

- sleep یا limiter مستقل در feederها، retry هر `0.1s`، cap کردن retry_after، newest-first بدون catch-up، priority aging برای عبور از `M0` و ارسال payload snapshot قدیمی.

## داده، migration و sync

priority، internal rank، deadline، eligibility، `next_retry_at` خام، freshness version و cooldown در schema افزایشی ADR-02 ذخیره می‌شوند. deadline و state دامنه از authority sync می‌شوند؛ cooldown، lease و attempt فقط foreign هستند. migration هیچ مقدار interval production را فعال نمی‌کند.

## failure mode و degraded mode

- DB unavailable: commit انجام نمی‌شود و success نمایش داده نمی‌شود.
- Redis unavailable: send fail-closed و صف PostgreSQL محفوظ می‌ماند.
- Telegram outage: circuit-break، backlog و recovery probe/drain.
- destination cooldown مقصدهای آماده دیگر را جز probe safety متوقف نمی‌کند.

## تست و observability

تست matrix تمام priorityها، promotion پنج‌ثانیه‌ای، coalescing/reclassification، catch-up یک‌به‌بیست، 429 چندمقصد/چند bot role، retry_after بزرگ، freshness و outageهای DB/Redis/Telegram الزامی است. metric به تفکیک bot role/priority/method/destination-class کم‌کاردینال است.

## feature flag و rollback

limiter و scheduler فقط همراه execution-owner flag فعال می‌شوند. rollback claim را متوقف و cooldown/jobها را حفظ می‌کند؛ feeder حق شروع ارسال مستقیم هم‌زمان ندارد.
