# TOPQ-ADR-04 — scheduler، fairness، freshness و outage

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Backend + Operations
- چالش‌ها: `TOPQ-C10`, `TOPQ-C12`, `TOPQ-C25`, `TOPQ-C26`, `TOPQ-C30`, `TOPQ-C31`, `TOPQ-C37`, `TOPQ-C42`, `TOPQ-C56`, `TOPQ-C57`, `TOPQ-C58`, `TOPQ-C68`

## تصمیم

- صف اصلی تنها scheduler/control plane Bot API در scope این Roadmap با اولویت `M0..M7` است، اما dispatcher تک‌اسلاتی سراسری نیست. زیر owner واحد `queue-v1` دو claim/execution lane هم‌زمان و work-conserving برای `primary` و `channel_editor` وجود دارد. ترتیب `M0`: callback deadlineدار، اعلان معامله عبورکرده از پنج ثانیه، publication آفر. OTP به‌علت محرمانگی payload در مسیر امضاشده و Redis کوتاه‌عمر فعلی می‌ماند و وارد PostgreSQL صف نمی‌شود.
- هر lane فقط `bot_identity` خود را claim می‌کند و limiter، semaphore/in-flight و token budget مستقل دارد. هر دو lane همان repository، state machine، lease، fencing، retry classifier و feedback را استفاده می‌کنند.
- priority فقط میان jobهای رقیب همان lane یا منبع مشترک اعمال می‌شود. backlog، sleep، retry یا `M0` در primary حق بیکار نگه‌داشتن ظرفیت editor را ندارد و برعکس.
- اعلان هر recipient معامله در `M1` شروع و مستقل در `trade_committed_at+5s` به `M0` ارتقا می‌یابد؛ cooldown و retry_after دور زده نمی‌شود.
- feeder edit ترتیب partial active، traded، expired، cancelled، سایر edit و repair را همیشه اعمال می‌کند. هر Offer یک edit مؤثر دارد و partial terminal‌شده reclassify می‌شود.
- stale edit بالای پنج دقیقه در stale bucket همان طبقه است. پس از هر ۲۰ edit تازه، یک stale واجد ارسال رزرو می‌شود؛ priority اصلی تغییر نمی‌کند.
- `M6/M7` فقط ظرفیت باقی‌مانده می‌گیرند. عبور max-age alert/SLO breach است، نه priority inversion.
- 429 مقدار خام `retry_after + safety_margin` را بدون cap اعمال می‌کند. safety margin اولیه `100ms` است و interval/safety فقط با Stage 4 تنظیم می‌شوند.
- limiter برای هر `bot_identity` بودجه token جدا و برای destination یک gate محافظه‌کارانه دارد: 429 ابتدا token/gate مقصد مرتبط را می‌بندد؛ probe فقط مطابق state machine کنترل‌شده مجاز است. gate مشترک مقصد فقط admission به منبع واقعاً مشترک را نگه می‌دارد و نباید به قفل claim یا dispatcher سراسری تبدیل شود. editor دوم تا پایان Stage 4 حق مستقل فرض‌کردن ظرفیت همان کانال را ندارد.
- هر retry پس از claim و بلافاصله قبل از side effect revalidate می‌شود. payload یا دکمه stale به `SUPERSEDED/EXPIRED_INTERACTION` می‌رود.

## گزینه‌های ردشده

- sleep یا limiter مستقل در feederها، یک dispatcher سراسری برای دو bot role، retry هر `0.1s`، cap کردن retry_after، newest-first بدون catch-up، priority aging برای عبور از `M0` و ارسال payload snapshot قدیمی.

## داده، migration و sync

priority، internal rank، deadline، eligibility، `next_retry_at` خام، freshness version و cooldown در schema افزایشی ADR-02 ذخیره می‌شوند. deadline و state دامنه از authority sync می‌شوند؛ cooldown، lease و attempt فقط foreign هستند. migration هیچ مقدار interval production را فعال نمی‌کند.

## failure mode و degraded mode

- DB unavailable: commit انجام نمی‌شود و success نمایش داده نمی‌شود.
- Redis unavailable: send fail-closed و صف PostgreSQL محفوظ می‌ماند.
- Telegram outage: circuit-break، backlog و recovery probe/drain.
- destination cooldown مقصدهای آماده دیگر را جز probe safety متوقف نمی‌کند؛ خطای bot-specific lane دیگر را متوقف نمی‌کند و فقط circuit gateway/config یا gate مقصد دارای شاهد می‌تواند هر دو lane را نگه دارد.

## تست و observability

تست matrix تمام priorityها، promotion پنج‌ثانیه‌ای، coalescing/reclassification، catch-up یک‌به‌بیست، 429 چندمقصد/چند bot role، retry_after بزرگ، freshness و outageهای DB/Redis/Telegram الزامی است. تست head-of-line باید حداقل ۳۰۰ job `M0/M1` primary را که دست‌کم ۲۰۰ مورد آن private/admin و خارج gate کانال‌اند، همراه ۱۰۰ edit editor نگه دارد و ثابت کند editor پیش از صفرشدن backlog primary جلو می‌رود، مگر در بازه‌ای که gate مقصد خودش با شاهد فعال است؛ زیرآزمون جداگانه publication کانال انتظار gate مشترک را اندازه می‌گیرد و تست معکوس نیز نباید primary را پشت backlog editor نگه دارد. metric به تفکیک bot role/priority/method/destination-class و علت انتظار `bot_lane|destination_gate|circuit` کم‌کاردینال است.

## feature flag و rollback

limiter و scheduler فقط همراه execution-owner flag فعال می‌شوند و lifecycle هر دو lane زیر همان flag/owner است. rollback claim هر دو lane را متوقف و cooldown/jobها را حفظ می‌کند؛ feeder حق شروع ارسال مستقیم هم‌زمان ندارد.

## الحاقیه ممیزی `2026-07-18`

- claim باید resource-aware باشد و job مقصد channel که gate آن بسته است batch را اشغال نکند؛ private primary آماده باید در همان cycle قابل dispatch بماند.
- در startup، channel cooldown editor فقط channel work را defer می‌کند. primary پس از identity-only preflight می‌تواند private work را اجرا کند، ولی publication تا full channel preflight و gate پاک متوقف می‌ماند.
- `429` preflight قبل از هر sleep در PostgreSQL ثبت و پس از Redis loss rehydrate می‌شود.
- Lua limiter از Redis `TIME` استفاده می‌کند. config باید `0 < base_backoff <= max_backoff`، مقادیر finite/bounded و jitter کنترل‌شده را enforce کند؛ provider attempt از claim/admission attempt جداست.
- newest-first edit باید در کل backlog قابل‌اجرا باشد، نه فقط در انتخاب هر cycle feeder.
