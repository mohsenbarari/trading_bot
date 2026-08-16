# نقشه‌راه انتشار چندناشرهٔ تلگرام با B2B

> وضعیت عملیاتی جاری: قابلیت multi-publisher و B2B در کد `main` پیاده شده و روی runtime بات staging با پنج lane فعال است؛ مالکیت اجرا هنوز باید از مسیر cutover رسمی به Queue-v1 منتقل شود. جزئیات در `docs/TELEGRAM_DELIVERY_QUEUE_CURRENT_STATE_20260816.md`.
> بندهای زیر طرح مصوب تاریخی‌اند و حذف نمی‌شوند.
> محدودهٔ تاریخی: branch `candidate/offer-overtime`
> هدف: جداکردن بات مرکزیِ دریافت‌کنندهٔ درخواست از پنج بات ناشر کانال، بدون از دست‌دادن idempotency، قابلیت بازیابی، یا مالکیت پیام.

## نتیجهٔ موردنظر

بات مرکزی تنها ورودی درخواست‌های کاربر و ارکستراتور است. هر آفرِ جدید، پیش از نخستین ارسال، به یکی از پنج `publisher` سالم تخصیص می‌یابد. همان ناشر تا پایان عمر پست مسئول `sendMessage`، دکمه‌ها، callback، ویرایش، حذف دکمه و عملیات پایانی است.

```text
کاربر → central bot → رکورد durable / صف موجود → router
                                              ↓
                         B2B command کم‌حجم → publisher-1 ... publisher-5
                                              ↓
                                            کانال
                                              ↓
                 callback / edit / terminal action با همان publisher
```

B2B یک **کانال فرمان و receipt** است، نه منبع حقیقت. دادهٔ آفر، وضعیت صف، نسخهٔ آفر و نتیجهٔ provider فقط در PostgreSQL و سازوکار queue موجود می‌مانند. پیام B2B صرفاً به یک command غیرحساس و قابل‌ردیابی اشاره می‌کند.

## اصول غیرقابل‌مذاکره

1. **مالکیت چسبنده:** `publisher_bot_identity` فقط یک‌بار، قبل از اولین publish، تعیین می‌شود و بعد از ثبت `message_id` هرگز تغییر نمی‌کند.
2. **همان ناشر، همان چرخهٔ عمر:** ناشر پست، تنها بازیگر مجاز برای callback، `answerCallbackQuery`، edit متن، edit/remove keyboard، حذف و عملیات terminal آن پست است. بات دیگری جای او را نمی‌گیرد.
3. **B2B بدون payload تجاری:** پیام B2B نباید token، دادهٔ کاربر، متن کامل آفر، قیمت، یا دادهٔ حساس داشته باشد؛ فقط `command_id` تصادفی، نسخهٔ قرارداد، شمارهٔ ترتیبی و زمان UTC را حمل می‌کند.
4. **DB قبل از Telegram:** assignment و command باید در همان تراکنش durable ساخته شوند؛ پاسخ Telegram تنها state آن‌ها را جلو می‌برد. timeout یا پاسخ مبهم هرگز به retry کور یا تغییر ناشر منجر نمی‌شود.
5. **محدودیت کانال مشترک است:** پنج توکن، ظرفیت مقصد واحد را پنج‌برابر فرض نمی‌کنند. gate فعلی `destination_next` باید مشترک باقی بماند؛ هر bot فقط cadence مخصوص خودش را دارد.
6. **مهاجرت پیام فعال ممنوع:** پستی که با `primary` یا یک publisher منتشر شده، تا terminal شدن روی همان lane می‌ماند. failover فقط برای jobی مجاز است که هنوز هیچ `message_id` ندارد.

## قرارداد عملیاتی دقیق

### نقش‌ها

| نقش | هویت پیشنهادی | مسئولیت |
|---|---|---|
| ingress/orchestrator | `primary` | تعامل کاربر، ساخت intent، انتخاب lane، ارسال command و دریافت receipt |
| publisher | `publisher_1` تا `publisher_5` | publish کانال، callback، edit و عملیات terminal پیام‌های خودش |
| legacy editor | `channel_editor` | برای lifecycle آفر استفاده نمی‌شود؛ تا زمان حذف امن فقط مسیرهای غیرآفرِ صریح آن باقی می‌ماند |

### رکوردهای durable

`telegram_delivery_jobs` رکورد اجرای provider می‌ماند. یک outbox جدید با نام پیشنهادی `telegram_publisher_dispatch_commands` افزوده می‌شود:

| فیلد | قرارداد |
|---|---|
| `command_id` | UUID/ULID تصادفی و یکتا؛ تنها شناسه‌ای که وارد B2B می‌شود |
| `job_id` | کلید خارجی job تخصیص‌یافته |
| `publisher_bot_identity` | lane منتخب و immutable |
| `state` | `pending`, `sent`, `acknowledged`, `retry_due`, `failed`, `superseded` |
| `attempt_count`, `next_retry_at`, `lease_*` | recovery و retry fenced |
| `sent_at`, `acknowledged_at` | مشاهده‌پذیری بدون ذخیرهٔ متن B2B |

مسیر state:

```text
intent committed
  → job assigned to publisher
  → dispatch command pending
  → B2B command sent
  → worker receipt persisted
  → publisher claims its job
  → provider result / publication state persisted
```

timeout فرمان، command را `retry_due` می‌کند؛ retry همان `command_id` و همان publisher را دارد. worker receipt را idempotent می‌نویسد. اگر Telegram پاسخ ارسال را مبهم کرد، command ابتدا reconcile می‌شود و دوباره با command تازه یا lane دیگر منتشر نمی‌شود.

### envelope B2B

متن command و receipt باید کوتاه، versioned و strict باشد:

```text
tbq1|dispatch|<command_id>|<sequence>|<enqueued_at_utc>
tbq1|ack|<command_id>|<sequence>|<received_at_utc>|<ack_sent_at_utc>
```

- worker فقط `dispatch` با فرستندهٔ `primary` allowlisted، schema معتبر و command تخصیص‌یافته به خودش را می‌پذیرد.
- central فقط `ack` از یکی از پنج publisher allowlisted را می‌پذیرد.
- `ack` هرگز دوباره وارد router یا صف انتشار نمی‌شود؛ loop باید ناممکن باشد.
- زمان‌های envelope برای telemetry هستند؛ زمان authoritative state transition همچنان PostgreSQL/Redis است.

## شکاف‌های فعلی که باید رفع شوند

| سطح | وضعیت کنونی | کار لازم |
|---|---|---|
| identity | `SUPPORTED_TELEGRAM_BOT_IDENTITIES` فقط `primary/channel_editor` است | registry ثابت و allowlisted برای پنج publisher اضافه شود |
| credential | `TelegramDeliveryCredentialRegistry` حداکثر primary/editor را می‌سازد | registry configuration-driven با fingerprint یکتا، username/id مورد انتظار و preflight همهٔ laneها |
| job schema | check constraint هویت و editor route را hard-code کرده است | migration برای laneهای publisher و قواعد method/action مناسب |
| publication state | `publisher_bot_identity` فقط `primary` را می‌پذیرد | publisher lane را immutable و برای surface کانال معتبر کند |
| offer freshness | publish فقط primary و edit primary/editor را می‌پذیرد | publish/edit/callback را با publisher state تطبیق دهد |
| limiter | gate مقصد مشترک است، اما resume دو lane را hard-code کرده است | shared destination gate حفظ و تمام publisher laneها در resume/recovery پشتیبانی شوند |
| runtime | workerهای فعلی credentialهای دو lane را در یک composition می‌سازند | runtime مستقل یا lane-scoped برای هر publisher، با ownership روشن |

فایل‌های شروع بررسی: `core/services/telegram_delivery_queue_service.py`، `core/telegram_delivery_credentials.py`، `core/telegram_delivery_queue_limiter.py`، `models/telegram_delivery_job.py`، `models/offer_publication_state.py`، `core/services/offer_publication_state_service.py`، `core/telegram_delivery_offer_freshness.py` و `core/services/telegram_offer_queue_service.py`.

## مراحل اجرا

### مرحلهٔ 0 — قرارداد، baseline و safety rail

- inventory تمام actionهای کانال و تعیین اینکه هرکدام publish، active edit، terminal edit، delete یا callback هستند.
- ثبت contract B2B، state machine command، reason codeها و metric schema پیش از تغییر runtime.
- feature flagهای fail-closed: `TELEGRAM_MULTI_PUBLISHER_ENABLED` و `TELEGRAM_B2B_DISPATCH_ENABLED`، هر دو پیش‌فرض `false`.
- preflight برای هر publisher: token معتبر و متمایز، identity مورد انتظار، B2B فعال، دسترسی post/edit/delete کانال و دریافت callback.

**خروجی:** قرارداد تست‌شده، checklist عملیاتی و هیچ تغییر رفتار production.

**gate:** نبود credential در log/DB/commit، و failure هر preflight باید همهٔ laneهای جدید را غیرفعال کند.

### مرحلهٔ 1 — registry و مدل lane

- تعریف `TelegramPublisherLane` شامل identity، credential، bot id/username مورد انتظار، قابلیت‌ها و health state.
- جایگزینی ثابت دو هویت با allowlist پنج‌ناشره؛ `primary` برای پیام‌های legacy موجود باقی می‌ماند.
- حذف انتخاب `channel_editor` برای lifecycle آفر؛ مسیر legacy باید صریحاً primary باشد تا پیش از مهاجرت cross-edit رخ ندهد.
- به‌روزرسانی preflight، runtime composition، worker ID و concurrency تا هر lane فقط توکن خودش را مصرف کند.

**تست‌ها:** token تکراری، username/id نامنطبق، lane غیرفعال، capability ناقص، و جلوگیری از استفادهٔ lane برای method غیرمجاز.

**gate:** اجرای فعلی primary بدون تغییر رفتار، و ساخت runtime برای پنج lane بدون افشای secret.

### مرحلهٔ 2 — migration durable و مالکیت immutable

- migration check constraintهای `telegram_delivery_jobs` و `offer_publication_states` برای `primary` و `publisher_1..publisher_5`.
- ایجاد جدول outbox command و receipt/idempotency/lease/indexهای recovery آن.
- افزودن invariant: یک state کانال پس از set شدن `publisher_bot_identity` قابل تعویض نیست؛ set `telegram_message_id` فقط با همان publisher پذیرفته می‌شود.
- backfill همهٔ publicationهای موجود به `primary` بدون تغییر پیام یا queue آن‌ها.
- به‌روزرسانی sync contract تا publisher identity sync-safe و immutable بماند.

**تست‌ها:** upgrade/downgrade migration، backfill، race تخصیص lane، replay command، job بدون message_id و رد lane swap پس از publish.

**gate:** migration روی snapshot staging بدون job orphan، publication mismatch یا تغییر پیام فعال کامل شود.

### مرحلهٔ 3 — router مرکزی و dispatch B2B

- در enqueue اولین publish، router یک publisher سالم انتخاب و همان identity را روی publication state، job و command ثبت می‌کند.
- انتخاب اولیه: `least_in_flight` میان laneهای healthy، با tie-break round-robin پایدار. انتخاب نباید به latency پاسخ B2B یا queue موقت متکی باشد.
- dispatcher commandهای outbox را با limiter مستقل B2B به private chat publisher می‌فرستد؛ payload آفر از DB خوانده می‌شود، نه از Telegram.
- worker command را validate، receipt را durable و سپس فقط job lane خودش را claim می‌کند.
- dispatcher sweeper commandهای ackنشده را fenced retry/reconcile می‌کند؛ receipt تکراری harmless است.

**تست‌ها:** 120 command در دقیقه با تقسیم یکنواخت، restart central، restart worker، B2B duplicate، sender جعلی، ack با command اشتباه، timeout مبهم و worker unhealthy.

**gate:** همهٔ commandها دقیقاً یک receipt معتبر می‌گیرند؛ هیچ ackی دوباره dispatch نمی‌شود؛ command بدون payload تجاری است.

### مرحلهٔ 4 — publish، callback و edit مالک‌محور

- publish job تنها در lane assigned اجرا و `message_id` همراه همان publisher ثبت می‌شود.
- callback ingress هر publisher با توکن خودش کار می‌کند؛ domain intent را durable می‌کند و همان lane `answerCallbackQuery`/edit را انجام می‌دهد.
- active/terminal edits فقط وقتی claim می‌شوند که `job.bot_identity == publication.publisher_bot_identity` باشد.
- freshness/reconciliation برای هر lane state تازه را می‌خواند و mismatch identity را quarantine می‌کند.
- کاربرهای legacy با publisher `primary` تا terminal شدن در primary باقی می‌مانند.

**تست‌ها:** publish→callback→active edit، publish→terminal edit با حذف keyboard، expiry، trade، replay callback، و تلاش cross-bot edit که باید پیش از provider call quarantine شود.

**gate:** هیچ پیام interactive با ناشر متفاوت edit نمی‌شود و هیچ callback برای central به اشتباه route نمی‌شود.

### مرحلهٔ 5 — rate limit، fairness و observability

- gate مشترک `destination_next` برای یک کانال بدون تغییر باقی می‌ماند؛ publisherهای متعدد فقط `bot_next` جدا دارند.
- بودجهٔ کانال بر حسب **کل method call** تنظیم می‌شود، نه فقط publish. publish و terminal edit باید در یک budget مشترک و با priority terminal/edit اجرا شوند.
- limiter B2B جداگانه برای central→publisher و publisher→central با key هر private peer اضافه می‌شود؛ مقدار production با config و benchmark تعیین می‌شود، نه hard-code.
- ثبت metric و outcome immutable برای `lane`, `command_id`, `job_id`, destination, method, 429, `retry_after`, command/receipt lag، callback lag، queue depth و lane health.
- resume/recovery برای همهٔ laneها generic می‌شود؛ `clear_destination_gate_after_database_resume` دیگر نام primary/editor را hard-code نمی‌کند.

**تست‌ها:** 429 مصنوعی در یک lane و در destination مشترک، Redis restart، DB recovery، probe/retry، fairness بین publish/edit و reconciler.

**gate:** 429 از log پنهان نمی‌ماند، cooldown درست scope دارد و failure یک lane rate کل destination را نادقیق آزاد نمی‌کند.

### مرحلهٔ 6 — staging channel acceptance

- پنج publisher با دسترسی صحیح در کانال staging و B2B فعال؛ هیچ credentialی در repository ثبت نمی‌شود.
- آزمون A: command/ack end-to-end در نرخ هدف، با payload مرجع و receipt idempotent.
- آزمون B: publish و lifecycle edit روی همان publisher برای هر lane؛ callback و حذف keyboard بررسی شود.
- آزمون C: ظرفیت کانال با **تعداد کل callهای send/edit** اندازه‌گیری شود. ابتدا بار مختلط (publish + terminal edit) اجرا و فقط پس از ثبت `retry_after`، budget production تعیین شود.
- آزمون D: failure drill؛ یک worker متوقف، command receipt عقب‌افتاده، job منتشرنشده recover و job منتشرشده sticky باقی بماند.

**gate:** صفر cross-owner edit، صفر loss/duplicate، evidence کامل هر 429، و budget کانال مبتنی بر دادهٔ staging.

### مرحلهٔ 7 — rollout و rollback

1. **shadow assignment:** برای آفرهای جدید lane انتخاب و metric ثبت شود، ولی publish همچنان primary باشد.
2. **canary:** فقط آفرهای جدید با یک publisher؛ postهای قبلی primary هستند.
3. **ramp:** دو publisher، سپس پنج publisher، با سقف lane و سقف aggregate کانال ثابت.
4. **steady state:** فعال‌سازی کامل فقط پس از گذر همهٔ gateها و تأیید صریح.

Rollback باید فقط آفرهای publish‌نشده را به primary بازگرداند. برای هر آفر دارای `message_id`، rollback به‌معنای ادامهٔ lifecycle روی publisher فعلی است؛ انتقال ownership ممنوع است. توقف B2B dispatcher نباید job durable یا provider evidence را حذف کند.

## معیار پذیرش نهایی

- هر آفر جدید دقیقاً یک publisher owner دارد و owner پس از publish تغییر نمی‌کند.
- publish، callback و همهٔ editهای آن پست با همان token اجرا می‌شوند.
- command و ack B2B idempotent، sender-validated و بدون payload حساس‌اند.
- destination limiter برای یک کانال بین همهٔ publisherها مشترک است.
- dashboard/metric امکان تفکیک 429 و lag به lane و destination را دارد.
- migration و rollback هیچ پست فعال یا evidence provider را حذف/overwrite نمی‌کنند.
- تست staging مبتنی بر مجموع callهای کانال، budget release را تثبیت کرده است.

## خارج از محدوده

- استفاده از B2B به‌عنوان database یا انتقال متن/دادهٔ کامل آفر.
- افزایش خودکار نرخ کانال صرفاً به‌دلیل اضافه‌شدن توکن‌ها.
- انتقال پست active از یک بات به بات دیگر.
- ثبت credential، username خصوصی، شناسهٔ کانال یا دادهٔ آزمون در repository یا حافظهٔ پروژه.
