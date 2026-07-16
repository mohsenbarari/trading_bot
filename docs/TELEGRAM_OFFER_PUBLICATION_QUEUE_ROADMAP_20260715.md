# Roadmap صف پایدار انتشار آفر در تلگرام

## وضعیت سند

- تاریخ ایجاد: `2026-07-15`
- شاخه اختصاصی: `candidate/telegram-offer-publication-queue`
- مبنای شاخه: `main@ca6348af`
- وضعیت Roadmap: Stage 0 و Stage 1 ثبت شده‌اند و قرارداد اجرایی Stage 2 پاس شده است. ممیزی فنی/غیرفنی Stage 2.5 در `2026-07-15` challenge register را ایجاد کرد و تصمیم‌های دور نخست در `2026-07-16` ثبت شدند؛ Stage 3 تا تبدیل همه موارد `BLOCKER` به تصمیم ثبت‌شده مجاز نیست. worker پایدار و اتصال runtime هنوز ساخته نشده‌اند.
- این سند مجوز deploy به staging یا production نیست.
- تمام مستندات، تست‌ها و کدنویسی بعدی این موضوع باید در همین شاخه مستقل ادامه پیدا کنند، مگر اینکه مالک محصول صریحاً مسیر دیگری تعیین کند.

این Roadmap مسیر رفع خطاهای انتشار آفر در کانال تلگرام را از ثبت پایدار آفر تا صف، اولویت‌بندی، retry، آزمایش تکرارشونده staging و آمادگی انتشار مشخص می‌کند.

## 1. هدف

سیستم باید بتواند در زمان پیک حداقل `3` آفر در ثانیه را بدون ازدست‌دادن آفر بپذیرد، هر آفر را به‌صورت یک پیام مستقل در همان کانال فعلی منتشر کند و خطاهای موقت تلگرام را بدون نمایش وضعیت غیرعادی به کاربر مدیریت کند.

تفکیک دو ظرفیت الزامی است:

1. **ظرفیت پذیرش:** ثبت پایدار حداقل سه آفر در ثانیه در دیتابیس و صف.
2. **ظرفیت تحویل کانال:** سرعتی که تلگرام در آزمایش تکرارشونده برای همان بات و کانال staging واقعاً می‌پذیرد.

صف اختلاف این دو ظرفیت را بدون حذف، fail اشتباه یا انقضای ناشی از خطای موقت نگهداری می‌کند. صف تضمین نمی‌کند کانال بتواند سه پیام در ثانیه را دائماً بپذیرد؛ عدد تحویل فقط با شواهد staging تعیین می‌شود.

## 2. تصمیم‌های قطعی

موارد زیر در این گفتگو تأیید شده‌اند و تغییر آن‌ها نیازمند تصمیم جدید مالک محصول است:

- معماری محصول یک بات و یک کانال باقی می‌ماند.
- کانال جدید اضافه نمی‌شود.
- بات واسط یا ناشر دوم جزو این Roadmap نیست.
- Paid Broadcast راه‌حل این چالش در نظر گرفته نمی‌شود.
- هر آفر یک پیام مستقل در کانال دارد.
- انتشار دسته‌ای چند آفر در یک پیام رد شده است.
- ثبت آفر از اجرای side effect تلگرام جدا می‌شود.
- پس از ثبت پایدار آفر، کاربر از صف، `429` یا retry مطلع نمی‌شود. WebApp همان رفتار فعلی `main` یعنی بستن preview، پاک‌کردن draft و refresh بازار بدون success toast جدید را حفظ می‌کند.
- در Bot، اطلاعات دو پیام فعلی با یکدیگر ادغام می‌شوند: همان پیام preview/تأیید با یک `editMessageText` به متن موفقیت همان مسیر به‌اضافه بلوک «لفظ شما» و دکمه انقضا تبدیل می‌شود و `bot.send_message` خصوصی دوم حذف خواهد شد. `answerCallbackQuery` همچنان درخواست جدا و ضروری P0 است؛ بنابراین عملیات محتوایی از دو به یک و پیام قابل‌مشاهده نهایی از دو به یک کاهش می‌یابد.
- خطای موقت تلگرام نباید آفر فعال را منقضی کند.
- پاسخ `429` هرگز publication را `FAILED` نمی‌کند.
- پیام دریافت‌کننده `429` در صف می‌ماند و پس از `retry_after` به‌اضافه حاشیه کوچک اطمینان دوباره ارسال می‌شود.
- آزمایش مجدد هر `0.1` ثانیه در زمان `retry_after` مجاز نیست.
- فاصله نهایی ارسال از قبل hardcode نمی‌شود و فقط با آزمایش‌های پرتکرار staging انتخاب می‌شود.
- ارسال پیام، ویرایش متن و ویرایش دکمه‌های کانال از یک سهم مشترک مقصد استفاده می‌کنند.
- علامت معامله/انقضا و حذف دکمه‌های همان پیام باید با یک درخواست `editMessageText` انجام شوند.
- حذف دکمه پیش‌نمایش خصوصی کاربر یک عملیات جدا در گفت‌وگوی خصوصی است و از سهم اختصاصی کانال مصرف نمی‌کند.
- داده production فقط به‌صورت read-only برای انتخاب fixture و ترکیب بار استفاده می‌شود؛ تست نباید production را mutate کند.
- تمام پاسخ‌های طبیعی Telegram که در اثر آفر صحیح، آفر ناصحیح، معامله، رقابت، انقضا و پیام مدیریتی به کاربر یا کانال می‌رسند باید واقعاً از Telegram آزمایشی ارسال، در مقصد دریافت و با ledger تطبیق داده شوند؛ mock به‌تنهایی مدرک این معیار نیست.
- ماتریس فنی خطاهای Bot API و transport نیز جداگانه تست می‌شود، اما منظور مالک محصول از «تمام پاسخ‌های Telegram» این ماتریس خطا نبود.
- هر دور ترکیبی ده‌دقیقه‌ای شامل `1800` آفر معتبر به‌اضافه دقیقاً `400` تلاش نامعتبرِ تصادفی و تکرارپذیر است؛ در مجموع `2200` تلاش ثبت.
- الگوی آفرهای معتبر از آمار و نمونه‌های پاک‌سازی‌شده جدول آفرهای production با دسترسی read-only ساخته می‌شود؛ شناسه کاربر، متن واقعی توضیحات، شناسه پیام و سایر داده‌های هویتی کپی نمی‌شوند.
- فقط در دیتابیس ایزوله staging، مقدار `max_active_offers` از `4` به `10` افزایش می‌یابد. این مقدار جایگزین تصمیم قبلی `50` است.
- مقدار پیش‌فرض کد، فایل عمومی تنظیمات و production روی `4` باقی می‌ماند؛ مقدار `10` باید از مرجع Iran staging ثبت، به foreign staging sync و در cache هر دو سمت تأیید شود.
- داده و اجرای تلگرام همچنان تابع مرزبندی فعلی است: اجرای Telegram فقط روی سرور foreign انجام می‌شود.

## 3. اولویت قطعی پیام‌ها

### `P0` — پاسخ تعاملی کاربر

- `answerCallbackQuery`
- تأیید ثبت، معامله یا انقضا
- پاسخ عملیاتی که کاربر در همان لحظه منتظر آن است
- هر اعلان معاملاتی خصوصی به هر یک از دو طرف که تا `5` ثانیه پس از commit معامله پاسخ موفق Telegram نگرفته باشد، به‌صورت مستقل از `P2` به `P0` ارتقا می‌یابد.

ترتیب داخلی `P0`:

1. callback و پاسخ تعاملی دارای deadline
2. اعلان معاملاتی ارتقایافته پس از `5` ثانیه
3. سایر پاسخ‌های P0 بر پایه FIFO

«دریافت» در این قرارداد یعنی Bot API برای همان job پاسخ موفق `ok=true` و `message_id` داده باشد. Bot API خوانده‌شدن پیام روی دستگاه کاربر را گزارش نمی‌کند. `delivery_deadline_at` از `trade_committed_at + 5s` ساخته و فقط وقتی `now > delivery_deadline_at` باشد ارتقا انجام می‌شود. اگر پیام یک طرف ارسال شده و پیام طرف دیگر pending باشد، فقط job طرف دوم ارتقا می‌یابد. ارتقا به P0 اجازه عبور از `retry_after` یا cooldown Telegram را نمی‌دهد.

### `P1` — عملیات آفر در کانال

ترتیب داخلی `P1`:

1. نهایی‌کردن آفر: تکمیل، انقضا، لغو، ثبت علامت نهایی و حذف دکمه‌ها
2. به‌روزرسانی معامله جزئی و مقادیر قابل معامله
3. پیام‌های حیاتی بازار، به‌خصوص بسته‌شدن بازار
4. انتشار آفر جدید

#### حالت ازدحام صف ادیت کانال

این حالت فقط زمانی فعال می‌شود که بیش از `30` آفر یکتا، نه raw job، برای edit کانال pending و unsuperseded باشند. این قواعد فقط ترتیب انتخاب میان editهای کانال را تغییر می‌دهند و اولویت کلی P0 تا P3 یا جایگاه پیام‌های غیر-edit را تغییر نمی‌دهند.

1. هر Offer فقط یک edit مؤثر دارد؛ editهای قدیمی همان Offer با آخرین state همگرا و supersede می‌شوند. coalescing نباید `first_edit_enqueued_at` را reset کند.
2. تمام editهایی که حداکثر `5` دقیقه در صف بوده‌اند جلوتر از editهای stale قرار می‌گیرند.
3. در هر bucket زمانی، آفر active و لات‌بندی‌شده‌ای که بخشی از آن معامله شده است بالاترین اولویت edit را دارد تا مقدار معامله‌شده و لات‌های دیگر قابل درخواست نباشند.
4. بعد از آن، آفر کاملاً معامله‌شده برای متن نهایی و حذف دکمه‌ها قرار می‌گیرد.
5. سپس سایر editها مانند انقضا یا لغو قرار می‌گیرند.
6. در هر طبقه، Offer با `created_at` جدیدتر زودتر انتخاب می‌شود؛ زمان ساخت یا supersede شدن job نباید یک Offer قدیمی را ظاهراً جدید کند.
7. edit با `now > first_edit_enqueued_at + 5m` به bucket انتهای صف منتقل می‌شود و در آن bucket نیز Offer جدیدتر جلوتر از Offer قدیمی‌تر است.
8. وقتی تعداد آفرهای یکتای منتظر edit به `30` یا کمتر بازگشت، حالت ازدحام خاموش و ترتیب عادی P1 دوباره اعمال می‌شود.

کلید مرتب‌سازی قطعی در حالت ازدحام:

```text
(
  stale_bucket,          # 0 تا پنج دقیقه، 1 بیشتر از پنج دقیقه
  edit_business_class,   # 0 active lot-partial، 1 traded-terminal، 2 سایر
  offer_created_at DESC,
  offer_public_id
)
```

این سیاست عمداً FIFO صف edit را در حالت ازدحام کنار می‌گذارد. چون ورودی مداوم می‌تواند editهای قدیمی را برای همیشه عقب نگه دارد، Stage 3 به catch-up/reconciliation جدا در ظرفیت آزاد یا پس از بسته‌شدن بازار و alert برای stale backlog نیاز دارد؛ این مسیر حق ندارد ترتیب live edit را تغییر دهد.

### `P2` — اعلان‌های معاملاتی خصوصی

- اطلاع‌رسانی به خریدار، فروشنده یا افراد مرتبط
- اعلان‌های تراکنشی که کاربر منتظر پاسخ تعاملی آن‌ها نیست
- فقط job نتیجه معامله برای خریدار و فروشنده پس از بیش از `5` ثانیه بدون پاسخ موفق Telegram، طبق قرارداد P0 ارتقا می‌یابد؛ اعلان سایر افراد مرتبط خودکار ارتقا نمی‌یابد.

### `P3` — اعلان‌های انبوه و کم‌فوریت

- broadcastهای مدیریتی
- اعلان‌های عمومی
- اعلان عضویت یا رویدادهای غیر فوری

یادداشت `2026-07-16`: شرایط جدید ارتقای پنج‌ثانیه‌ای اعلان معامله و ازدحام edit ثبت شد. قرارداد catch-up برای جلوگیری از starvation editهای قدیمی هنوز باید پیش از Stage 3 بسته شود.

قواعد مشترک:

- در یک سطح اولویت، ترتیب FIFO رعایت می‌شود؛ استثناها فقط ارتقای پنج‌ثانیه‌ای پیام معامله و حالت ازدحام edit کانال هستند.
- `429` اولویت اصلی رکورد را تغییر نمی‌دهد.
- هنگام cooldown مقصد، هیچ عملیات دیگری به همان مقصد ارسال نمی‌شود.
- پس از cooldown، scheduler بالاترین اولویت آماده را انتخاب می‌کند؛ رکورد `429` همچنان pending و قابل retry باقی می‌ماند.
- `P3` فقط از ظرفیت باقی‌مانده استفاده می‌کند و نباید `P0` یا `P1` را عقب بیندازد.

## 4. تجربه کاربر

### ثبت موفق در دیتابیس

1. آفر و intent انتشار در یک تراکنش پایدار ثبت می‌شوند.
2. کاربر پاسخ عادی موفقیت را دریافت می‌کند.
3. پیش‌نمایش آفر و دکمه منقضی‌کردن مطابق تجربه عادی نمایش داده می‌شوند.
4. worker انتشار را مستقل از request کاربر ادامه می‌دهد.

### شکست واقعی ثبت

اگر تراکنش اصلی دیتابیس commit نشود، آفر ثبت نشده است و نمایش خطای معمول ثبت مجاز است. پنهان‌کردن خطا فقط مربوط به شکست side effect تلگرام پس از پذیرش پایدار آفر است.

### انقضای دستی

- mutation انقضا در دیتابیس پیام تلگرامی محسوب نمی‌شود.
- حذف دکمه پیش‌نمایش خصوصی کاربر یک `editMessageReplyMarkup` در private chat است.
- پاسخ کوتاه callback یک عملیات `answerCallbackQuery` جداست و پیام ماندگار جدید نیست.
- به‌روزرسانی پیام کانال یک عملیات مستقل `P1` است.

### انقضای خودکار

- عملیات خصوصی کاربر وجود ندارد.
- فقط وضعیت دیتابیس و یک عملیات ترکیبی کانال برای علامت انقضا و حذف دکمه‌ها ثبت می‌شود.

## 5. وضعیت فعلی کد

- `bot/handlers/trade_create.py` پس از ساخت آفر مستقیماً `bot.send_message` را صدا می‌زند و در بعضی خطاهای انتشار، آفر را منقضی و به کاربر خطا نمایش می‌دهد.
- `api/routers/offers.py` نیز پس از ثبت آفر تلاش هم‌زمان برای انتشار کانال دارد.
- `offer_publication_states` همین حالا `dedupe_key`، `next_retry_at`، خطا، زمان تلاش و شناسه پیام تلگرام را نگه می‌دارد.
- `core/offer_publication_worker.py` و reconciliation پایه لازم برای ترمیم publication را دارند.
- فاصله فعلی send و edit در `core/config.py` برابر `0.35` ثانیه است، اما دو مسیر مستقل و برخی ارسال‌های مستقیم می‌توانند limiter را دور بزنند.
- `core/services/telegram_offer_channel_service.py` برای آفر تکمیل یا منقضی‌شده اکنون ابتدا متن را ویرایش و سپس با درخواست دوم دکمه‌ها را حذف می‌کند.
- `core/telegram_gateway.py` از هم‌اکنون اجازه می‌دهد `editMessageText` متن و `reply_markup` را در یک درخواست بفرستد.
- worker اعلان عمومی و مسیرهای مستقیم aiogram یک نرخ‌دهنده مشترک واحد ندارند.
- Telegram side effect فقط روی foreign مجاز است و این مرز باید حفظ شود.

## 6. معماری مقصد

### 6.1 ثبت اتمیک intent

در همان تراکنشی که آفر ثبت می‌شود، وضعیت publication با `PENDING` و dedupe key پایدار ساخته می‌شود. commit موفق به معنی پذیرش قطعی آفر و مسئولیت سیستم برای تحویل یا تعیین تکلیف آن است.

### 6.2 صف پایدار

PostgreSQL منبع حقیقت صف است. Redis فقط برای limiter و هماهنگی توزیع‌شده استفاده می‌شود و قطع Redis نباید رکورد صف را حذف کند.

هر کار قابل ارسال باید حداقل این اطلاعات را داشته باشد:

- مقصد و method تلگرام
- اولویت
- زمان ایجاد و ترتیب FIFO
- dedupe key
- شناسه آفر یا موجودیت مبدأ
- `next_retry_at`
- تعداد تلاش‌ها
- آخرین کلاس خطا و `retry_after`
- payload یا اطلاعات لازم برای بازسازی امن payload
- وضعیت نهایی و `telegram_message_id`

تا حد ممکن از جدول‌ها و stateهای موجود استفاده می‌شود. migration جدید فقط وقتی مجاز است که قرارداد صف با فیلدهای فعلی قابل پیاده‌سازی قابل‌اعتماد نباشد.

### 6.3 scheduler مشترک

scheduler دو سطح محدودیت دارد:

1. بودجه کلی همان bot token
2. بودجه اختصاصی هر مقصد، به‌خصوص کانال آفر

همه send/editهای کانال باید از یک lane مشترک عبور کنند. افزایش تعداد worker ظرفیت همان lane را افزایش نمی‌دهد و نباید باعث ارسال موازی به کانال شود.

### 6.4 حالت‌های publication

- `PENDING`: آماده یا منتظر retry
- `SENT`: پیام با message id معتبر ثبت شده است
- `LAGGED`: publication از SLO عبور کرده ولی همچنان retryable است
- `FAILED`: فقط خطای دائمی و غیرقابل‌اصلاح payload پس از طبقه‌بندی روشن
- `DISABLED`: آفر قبل از انتشار دیگر قابل انتشار نیست

`429`، `5xx` و خطای شبکه نباید به‌تنهایی publication را `FAILED` کنند.

## 7. قرارداد retry

### `429`

1. رکورد در `PENDING` باقی می‌ماند.
2. `retry_after` از پاسخ تلگرام خوانده می‌شود.
3. `next_retry_at = now + retry_after + safety_margin` ثبت می‌شود.
4. lane همان مقصد تا آن زمان بسته می‌شود.
5. همان کار منطقی دوباره اجرا می‌شود؛ اگر آفر هنوز فعال باشد payload از آخرین وضعیت معتبر ساخته می‌شود.
6. تعداد retry به‌تنهایی پیام را terminal نمی‌کند.

اگر `retry_after` در پاسخ وجود نداشت، fallback کنترل‌شده با backoff استفاده می‌شود؛ polling صد میلی‌ثانیه‌ای مجاز نیست.

### `5xx` و خطای شبکه قطعی

- retry با exponential backoff محدود و jitter
- نگهداری رکورد در صف
- هشدار عملیاتی در صورت عبور از SLO

### timeout با نتیجه نامشخص

Bot API تضمین exactly-once ندارد. timeoutی که ممکن است بعد از پذیرش پیام رخ داده باشد نباید کورکورانه و فوری retry شود. قرارداد نهایی این حالت باید پیش از کدنویسی در سناریوی staging بسته شود.

### `400`

payload نامعتبر پس از ثبت اطلاعات کافی برای تشخیص، terminal محسوب می‌شود و هشدار اپراتوری می‌سازد.

### `403` یا مشکل دسترسی کانال

lane کانال pause می‌شود، هشدار بحرانی ساخته می‌شود و بررسی دسترسی با فاصله کنترل‌شده انجام می‌شود؛ retry سریع و بی‌پایان مجاز نیست.

## 8. ادغام ویرایش‌های وضعیت آفر

برای تکمیل یا انقضای آفر، تغییر متن و حذف دکمه باید اتمیک از دید Telegram و با یک API call انجام شود:

```python
editMessageText(
    chat_id=channel_id,
    message_id=message_id,
    text=updated_text,
    reply_markup={"inline_keyboard": []},
)
```

نتیجه مورد انتظار:

- وضعیت فعلی: دو درخواست کانال برای terminal state
- وضعیت مقصد: یک درخواست کانال
- حذف فاصله‌ای که در آن علامت نهایی ثبت شده ولی دکمه هنوز فعال است
- یک رکورد retry و یک مصرف از lane کانال

برای معامله جزئی که فقط دکمه‌ها تغییر می‌کنند، `editMessageReplyMarkup` همچنان یک درخواست کافی است.

## 9. ظرفیت و backlog

بار طراحی ورودی:

```text
3 offer/s = 180 offer/min
```

ظرفیت واقعی تحویل تابع نتیجه staging است. اگر فاصله مؤثر پذیرفته‌شده `d` ثانیه باشد:

```text
delivery_rate = 1 / d
backlog_growth = max(0, 3 - delivery_rate)
```

dashboard عملیاتی باید این موارد را نمایش دهد:

- عمق صف به تفکیک اولویت و مقصد
- سن قدیمی‌ترین پیام
- نرخ ورود و goodput واقعی
- زمان تخمینی تخلیه صف
- تعداد و درصد `429`
- توزیع `retry_after`
- تعداد retry، duplicate پیشگیری‌شده و terminal failure
- آفرهایی که زمان انتظارشان از عمر باقی‌مانده عبور کرده است

پیش از هر ارسال، وضعیت آفر دوباره خوانده می‌شود. آفر تکمیل، لغو یا منقضی‌شده نباید به‌عنوان آفر فعال دیرهنگام منتشر شود و با `DISABLED` تعیین تکلیف می‌شود.

## 10. برنامه آزمایش staging

پیش‌نویس سناریوی اجرایی دقیق در سند زیر ثبت شده است:

`docs/TELEGRAM_OFFER_PUBLICATION_STAGING_TEST_SCENARIOS_20260715.md`

حداقل الزامات آن سند:

### 10.1 ایزوله‌بودن

- bot token و channel id staging مستقل از production باشند.
- هیچ پیام آزمایشی به کانال production نرسد.
- هیچ داده production نوشته یا تغییر داده نشود.

### 10.2 جست‌وجوی فاصله مناسب

فاصله‌های اولیه قابل آزمایش، نه مقادیر قطعی:

`0.25`, `0.30`, `0.35`, `0.40`, `0.50`, `0.65`, `0.80`, `1.00`, `1.05` ثانیه.

- هر فاصله در چند دور مستقل و با ترتیب تصادفی اجرا شود.
- میان دورها cooldown کافی اعمال شود تا نتیجه دور قبلی آزمایش بعدی را آلوده نکند.
- پس از `429` فقط `retry_after + safety_margin` ملاک retry باشد.
- دوری که وارد retry storm می‌شود متوقف و ثبت شود.

### 10.3 سه خانواده تست

1. send-only برای سقف خام انتشار
2. edit-only برای سقف ویرایش
3. بار ترکیبی واقعی شامل انتشار، معامله جزئی، تکمیل و انقضا

بار ترکیبی مبنای انتخاب عدد production است.

### 10.4 بار و تکرار

- هر دور کالیبراسیون پایه `10` دقیقه طول می‌کشد.
- بار ترکیبی: `1800` آفر معتبر به‌اضافه `400` تلاش نامعتبر، در مجموع `2200` تلاش ثبت.
- میانگین پذیرش آفر معتبر `3 valid offer/s` و میانگین کل تلاش‌های ثبت حدود `3.67 attempt/s` است.
- trace ورود باید شامل پیک‌های متوالی `8` تا `12` تلاش ثبت در ثانیه باشد و با seed ثابت بازپخش شود.
- اندازه‌گیری بعد از توقف ورودی تا تخلیه صف یا timeout سخت ادامه پیدا می‌کند.
- اجرای طولانی حداقل `30` دقیقه فقط برای interval منتخب انجام می‌شود.
- ری‌استارت worker در حین backlog
- همه پیام‌ها، editها، callback answerها، پیشنهادهای جایگزین لات، اعلان‌های دو طرف معامله و پاسخ‌های validation که جریان واقعی کسب‌وکار تولید می‌کند
- ماتریس فنی جداگانه شامل `429`، `5xx`، پاسخ نامعتبر، قطع شبکه و timeout
- همه کالاهای فعال، خرید/فروش، تمام انواع تسویه، عمده/خرد، لات‌ها، توضیحات و هشدار قیمت
- fixtureهای تصادفی و طبقه‌بندی‌شده production فقط با تراکنش read-only و خروجی پاک‌سازی‌شده

### 10.5 معیار انتخاب فاصله

کوتاه‌ترین interval لزوماً بهترین نیست. معیار اصلی:

```text
goodput = SENT / wall_clock_time_including_cooldowns
```

عدد منتخب باید در تکرارها پایدار باشد، retry storm یا رشد `retry_after` نسازد، پیام گم‌شده یا تکراری نداشته باشد و بالاترین goodput قابل تکرار را در بار ترکیبی ارائه کند.

## 11. reconciliation و گزارش سلامت

گزارش باید بین موارد زیر تفاوت بگذارد:

- pending عادی و هنوز در SLO
- pending آماده retry
- lagged فعال
- retryable error
- نتیجه نامشخص
- خطای دائمی فعال
- خطای تاریخی آفر terminal
- disabled به‌علت پایان عمر آفر پیش از انتشار

خطاهای تاریخی آفرهای terminal نباید به‌تنهایی وضعیت جاری سیستم را `action_required` کنند. گزارش باید offer id، public id، آخرین خطا، تعداد تلاش، `next_retry_at` و سن صف را برای موارد فعال نشان دهد.

## 12. مراحل اجرا

### Stage 0 — ثبت قرارداد و Roadmap

- ایجاد شاخه مستقل از main
- ثبت تصمیم‌های قطعی گفتگو
- ثبت محدوده و چالش‌ها

معیار خروج: این سند روی شاخه candidate commit شده باشد.

### Stage 1 — ثبت سناریوی دقیق staging

- تعریف load generator
- تعریف fixtureها و همه حالت‌های کالا
- تعریف دورهای تکرار، cooldown و stop condition
- تعریف قالب گزارش مقایسه intervalها
- تعریف cleanup پیام‌های staging خارج از پنجره اندازه‌گیری

معیار خروج: سناریوها قابل اجرا، قابل تکرار و مورد تأیید مالک محصول باشند.

### Stage 2 — تست‌های قطعی قبل از side effect واقعی

- unit test state machine صف
- تست اولویت P0 تا P3
- تست `429/retry_after`
- تست idempotency و concurrency
- تست ادغام terminal edit
- تست restart و lease recovery
- تست عدم انقضای آفر روی خطای موقت

معیار خروج: تمام failure injectionها بدون پیام گم‌شده و duplicate کنترل‌نشده پاس شوند.

#### نتیجه اجرای Stage 2 در `2026-07-15`

- قرارداد pure و بدون side effect در `core/telegram_delivery_queue_contract.py` ایجاد شد تا مستقیماً مبنای adapter پایدار Stage 3 باشد.
- اولویت `P0` تا `P3`، ترتیب داخلی `P1` و FIFO در قرارداد اولیه قطعی شد. تصمیم‌های جدید `2026-07-16` ارتقای زمانی P2 به P0 و حالت non-FIFO ازدحام edit را اضافه کردند؛ قرارداد و تست Stage 2 باید پس از بسته‌شدن catch-up policy و پیش از Stage 3 بازنگری شوند.
- `429` رکورد را pending نگه می‌دارد، `retry_after + safety_margin` را اعمال می‌کند و فقط lane همان مقصد را تا موعد retry می‌بندد.
- `5xx` و خطاهای transport قابل retry با backoff محدود مدل شدند؛ خطای payload معیوب `400` terminal و `403` موجب pause مقصد می‌شود.
- dedupe هم‌زمان، collision یک dedupe key با payload متفاوت، claim هم‌زمان، مالکیت lease، نتیجه دیررس worker قبلی و بازیابی پس از restart تست شدند.
- timeout مبهم `sendMessage` retry کور نمی‌شود و در وضعیت `AMBIGUOUS` می‌ماند؛ فقط شاهد صریح reconciliation می‌تواند آن را `SENT` یا «قطعاً غایب و قابل retry» کند. روش تولید این شاهد برای production همچنان چالش باز `TOPQ-C07` است.
- قرارداد terminal edit دقیقاً یک `editMessageText` با متن نهایی و `reply_markup={"inline_keyboard": []}` تولید می‌کند.
- تصمیم‌های خطای موقت هیچ mutation برای Offer برنمی‌گردانند؛ اتصال این اصل به مسیر واقعی ثبت آفر در Stage 3 انجام می‌شود.
- `18` تست قرارداد جدید و در مجموع `125` تست هدفمند همراه regressionهای publication، expiry، channel edit و notification outbox فعلی پاس شدند.

مرز این مرحله:

- `core/offer_publication_worker.py` و `core/telegram_notification_outbox_worker.py` همچنان worker صف مشترک نیستند.
- هنوز جدول/مدل صف مشترک، migration، PostgreSQL claim با `SKIP LOCKED`، limiter توزیع‌شده، feature flag، startup task و جایگزینی مسیرهای مستقیم Telegram ساخته نشده است.
- بررسی schema نشان داد `offer_publication_states` فقط publication آفر و `telegram_notification_outbox` فقط پیام خصوصی را مدل می‌کنند؛ هیچ‌کدام به‌تنهایی method/payload عمومی، P0 تا P3، ترتیب داخلی P1 و cooldown مشترک مقصد را با قرارداد کامل نگه نمی‌دارند. Stage 3 باید adapter پایدار و migration حداقلی را بر همین اساس اضافه کند.

### Stage 2.5 — ممیزی چالش‌ها و گیت پیش از کدنویسی worker

هدف این Stage جلوگیری از کشف دیرهنگام مسئله در توسعه، staging یا production است. ایجاد challenge register به معنی حل چالش‌ها نیست؛ موارد `BLOCKER` باید قبل از اولین migration یا worker پایدار به ADR، SLO، owner و تست پذیرش قابل اجرا تبدیل شوند.

خروجی‌های اجباری:

- ثبت چالش‌های فنی و غیرفنی با failure mode، کنترل پیشگیرانه و شاهد بسته‌شدن
- تفکیک blockerهای قبل از کدنویسی از مواردی که در Stage 3 یا Stage 4 قابل حل‌اند
- ثبت ADR برای ظرفیت/عمر آفر، منبع حقیقت صف، مرز Iran/foreign، timeout مبهم، limiter و rollout نسخه مختلط
- تعیین SLOهای تجربه کاربر، زمان publication، backlog، P0 callback و incident response
- تعیین owner عملیاتی، مسیر escalation، مجوز go/no-go و نگهداری حساب‌ها و credentialهای staging
- افزودن تست یا preflight متناظر برای هر چالش قابل‌آزمایش

معیار خروج:

- هیچ مورد `BLOCKER` بدون تصمیم باقی نماند.
- هر تصمیم دارای owner، stage اجرا، تست پذیرش و rollback باشد.
- تعارض میان «پذیرش ۳ آفر بر ثانیه»، ظرفیت واقعی یک کانال و عمر دو دقیقه‌ای آفر صریحاً تعیین تکلیف شود.
- طراحی migration و rollout ثابت کند در هیچ بازه‌ای direct sender و worker جدید یک job منطقی را دو بار ارسال نمی‌کنند.
- مالک محصول و مالک عملیات گزارش challenge closure را پیش از شروع Stage 3 تأیید کنند.

### Stage 3 — پیاده‌سازی پشت feature flag

- producer اتمیک publication intent
- scheduler و limiter مشترک
- حذف ارسال مستقیم کانال از handler و API
- تکمیل retry و error classification
- ادغام terminal edit
- تکمیل reconciliation و metrics

معیار خروج: تست‌های هدفمند، integration و regression پاس و feature flag پیش‌فرض خاموش باشد.

### Stage 4 — deploy و آزمایش تکرارشونده staging

- deploy فقط با مسیر استاندارد staging
- ثبت `max_active_offers=10` فقط از مرجع مدیریتی Iran staging و تأیید sync/cache در foreign staging
- تأیید recreate شدن `bot`, `foreign_app`, `sync_worker`, `foreign_sync_worker`
- اجرای ماتریس intervalها
- اجرای endurance و burst
- ثبت raw evidence و گزارش مقایسه‌ای

معیار خروج: یک interval پایه و safety margin با شواهد تکرارپذیر انتخاب شود.

### Stage 5 — تثبیت تنظیم و مرور آمادگی production

- قرار دادن مقدار منتخب staging در config
- حفظ limiter تطبیقی و رعایت `retry_after`
- اجرای کل acceptance matrix
- تهیه rollback/runbook
- dry-run reconciliation روی داده production با دسترسی read-only

معیار خروج: گزارش آمادگی و ریسک‌های باقیمانده برای تصمیم مالک محصول آماده باشد.

### Stage 6 — production canary و انتشار

این Stage فقط با دستور صریح جداگانه مجاز است. هیچ بار مصنوعی در کانال production تولید نمی‌شود. رفتار با ترافیک واقعی، metrics و stop conditionهای از پیش ثبت‌شده بررسی خواهد شد.

## 13. فهرست چالش‌ها

راهنمای وضعیت:

- `BLOCKER`: شروع Stage 3 تا ثبت تصمیم و معیار پذیرش این مورد ممنوع است.
- `OPEN`: قرارداد هنوز بسته نشده است.
- `DECIDED`: تصمیم گرفته شده ولی پیاده‌سازی نشده است.
- `IMPLEMENTED`: کد و تست تکمیل شده است.
- `VERIFIED`: در staging با شواهد تأیید شده است.

### 13.1 شواهد ممیزی پیش از کدنویسی

- جست‌وجوی ایستا در `bot/`, `api/` و `core/` حدود `340` محل متنی مرتبط با `send_message`, edit و callback answer پیدا کرد. این عدد شامل wrapper و تعریف متد نیز هست و شمار side effect واقعی نیست، اما نشان می‌دهد جایگزینی فقط دو مسیر ساخت آفر برای یک limiter مشترک کافی نیست.
- مسیر Bot اکنون `bot.send_message` کانال را داخل جریان publication اجرا می‌کند و شکست آن می‌تواند بعد از ثبت Offer به exception کاربرپسند نامناسب منجر شود.
- API نیز publication را در request آفر اجرا می‌کند؛ در نتیجه direct producer، repair worker و مسیرهای edit می‌توانند هم‌زمان مالک یک side effect منطقی شوند.
- `run_bot.py` هم‌اکنون workerهای publication، trade delivery، admin broadcast و notification outbox را کنار هم شروع می‌کند؛ worker جدید بدون برنامه مالکیت باعث رقابت یا duplicate می‌شود.
- `core/telegram_gateway.py` در وضعیت فعلی `HTTP 200` را بدون کنترل صریح `response_json.ok` موفق می‌داند، برای هر درخواست client جدید می‌سازد و endpoint اصلی Telegram را hardcode کرده است. بنابراین `HTTP 200 + ok=false`، connection churn و Test Bot API نیازمند اصلاح طراحی هستند.
- برخی مسیرهای فعلی `retry_after` را تا `120` یا `300` ثانیه محدود می‌کنند؛ قرارداد جدید باید مقدار اعلامی Telegram را به‌اضافه safety margin رعایت کند و truncation فقط با دلیل و alert مجاز باشد.
- `offer_publication_states` method/payload/priority/lease عمومی ندارد و `telegram_notification_outbox` برای پیام خصوصی طراحی شده است. استفاده هم‌زمان از هر دو به‌عنوان منبع حقیقت صف مبهم است.
- sync فعلی فیلدهای provider و lease را local-only نگه می‌دارد. intent قابل‌ساخت روی Iran و اجرای foreign باید بدون sync loop و بدون انتقال lease پیاده شود.
- Alembic در checkout فعلی یک head با شناسه `f1b6e7f8a9dc` دارد، اما این پروژه سابقه revisionهای اجراشده از شاخه دیگر را دارد؛ برابری graph فایل‌ها و head واقعی هر دو دیتابیس بخشی از preflight migration است.

### 13.2 هشدار ظرفیت و عمر آفر

این یک مثال تحلیلی است، نه پیش‌بینی نتیجه staging. اگر ورودی آفر `λ=3/s`، ظرفیت خام publication کانال `μ=1/s` و عمر آفر `L=120s` باشد، حتی بدون هیچ edit دیگری، انتظار تقریبی آفر ثبت‌شده در زمان `t` برابر `(λ/μ - 1) × t = 2t` ثانیه می‌شود. از حدود ثانیه `60` به بعد، انتظار از عمر باقی‌مانده آفر عبور می‌کند؛ در این مثال فقط حدود `180` آفر ابتدای دور ده‌دقیقه‌ای فرصت publication پیش از expiry دارند. terminal edit، partial edit و پیام بازار ظرفیت publication جدید را کمتر می‌کنند.

صف از گم‌شدن رکورد جلوگیری می‌کند، اما کمبود ظرفیت کانال را برطرف نمی‌کند. بنابراین یکی از این قراردادها باید پیش از کدنویسی انتخاب شود:

1. expiry همچنان از زمان ثبت محاسبه شود و پذیرفته شود که بخشی از آفرهای پیک هرگز در کانال ظاهر نمی‌شوند؛
2. زمان expiry برای pending publication تغییر کند؛ این گزینه semantics بازار و تازگی قیمت را عوض می‌کند؛
3. admission/backpressure با ظرفیت کانال هماهنگ شود؛ این گزینه پذیرش سه آفر بر ثانیه را محدود می‌کند؛
4. SLO یا محدودیت معماری تغییر کند؛ افزودن کانال، batching و ناشر دوم فعلاً طبق تصمیم محصول خارج از scope هستند.

### 13.3 تصمیم‌های قبلی و چالش‌های پایه

| شناسه | نوع | اولویت | وضعیت | چالش | خروجی لازم |
| --- | --- | --- | --- | --- | --- |
| `TOPQ-C01` | محصول | P0 | DECIDED | یک بات، یک کانال و یک پیام مستقل برای هر آفر | حفظ معماری بدون batching یا کانال اضافه |
| `TOPQ-C02` | فنی | P0 | DECIDED | پذیرش پایدار آفر مستقل از Telegram side effect | تراکنش Offer + intent پایدار |
| `TOPQ-C03` | UX | P0 | DECIDED | تجربه عادی کاربر در خطای موقت Telegram و مصرف دو عملیات محتوایی private | ادغام success و «لفظ شما» در یک edit با دکمه انقضا، حفظ رفتار WebApp و عدم نمایش queue/error |
| `TOPQ-C04` | محصول/فنی | P0 | DECIDED | اولویت پیام‌ها | P0 تا P3 و ترتیب داخلی P1 مطابق این سند |
| `TOPQ-C05` | فنی | P0 | DECIDED | رفتار `429` | pending، cooldown طبق retry_after و retry بدون fail |
| `TOPQ-C06` | فنی | P0 | DECIDED | کاهش دو edit terminal به یک API call | متن نهایی و حذف دکمه در یک editMessageText |
| `TOPQ-C07` | فنی | P0 | BLOCKER | timeout با نتیجه نامشخص و نبود exactly-once در Bot API | ADR جلوگیری از retry کور، وضعیت AMBIGUOUS و reconciliation قابل‌اثبات |
| `TOPQ-C08` | تست/عملیات | P0 | OPEN | سناریوی کامل staging | load generator، stop condition و گزارش واقعی |
| `TOPQ-C09` | فنی/عملیات | P0 | OPEN | انتخاب interval نهایی | آزمایش پرتکرار و goodput پایدار در بار ترکیبی |
| `TOPQ-C10` | معماری | P0 | BLOCKER | scheduler مشترک در برابر مسیرهای مستقیم و workerها | inventory مالکیت و طرح حذف bypass پیش از تغییر producer |
| `TOPQ-C11` | محصول/فنی | P1 | DECIDED | آفر terminal پیش از publication | بازخوانی وضعیت و تبدیل به DISABLED بدون ارسال stale |
| `TOPQ-C12` | فنی/عملیات | P1 | BLOCKER | سیاست جدید newest-first و انتقال editهای بالای پنج دقیقه به انتهای صف می‌تواند starvation دائمی و backlog نامرئی بسازد | شمار distinct Offer، coalescing، stale metrics و catch-up/reconciliation خارج live ordering |
| `TOPQ-C13` | عملیات | P1 | OPEN | reconciliation دقیق active/history | گزارش شناسه، علت، retry و تفکیک خطای تاریخی |
| `TOPQ-C14` | تست | P1 | OPEN | پوشش تمام کالاها و حالات تجارت | acceptance matrix و fixture read-only production |
| `TOPQ-C15` | معماری | P1 | DECIDED | مرز اجرای Telegram | foreign-only execution و عدم اجرای مستقیم روی Iran |
| `TOPQ-C16` | انتشار | P0 | BLOCKER | rollout و rollback بدون گم‌شدن pending یا double-send | versioned flag، drain/pause، ترتیب receiver-first و runbook |
| `TOPQ-C17` | تست | P0 | DECIDED | دریافت واقعی همه پاسخ‌های طبیعی Telegram | observer سمت گیرنده و تطبیق ledger |
| `TOPQ-C18` | محصول/تست | P0 | DECIDED | نسبت بار معتبر و نامعتبر | `1800` آفر معتبر + `400` تلاش نامعتبر |
| `TOPQ-C19` | حریم خصوصی | P1 | DECIDED | استفاده امن از الگوی آفر production | sampler فقط‌خواندنی، حذف هویت و بازسازی staging |
| `TOPQ-C20` | عملیات تست | P0 | DECIDED | pool کاربری واقعی بدون تغییر production | `max_active_offers=10` فقط staging، ۸۰ حساب و guard عدم نشت |

### 13.4 چالش‌های فنی تفصیلی

| شناسه | شدت | وضعیت | چالش و failure mode | کنترل یا تصمیم لازم | شاهد بسته‌شدن |
| --- | --- | --- | --- | --- | --- |
| `TOPQ-C21` | بحرانی | BLOCKER | ظرفیت کانال کمتر از ورودی و expiry دو دقیقه‌ای؛ Offer پذیرفته می‌شود ولی قبل از publication terminal می‌شود | ADR ظرفیت/عمر آفر و حداقل channel-goodput قابل‌قبول | مدل ظرفیت برای بار ترکیبی + تصمیم مالک محصول + تست backlog/expiry |
| `TOPQ-C22` | بحرانی | BLOCKER | نامشخص‌بودن منبع حقیقت میان جدول جدید، `offer_publication_states` و outboxهای فعلی | ADR schema و مالک واحد هر job؛ یک dedupe identity و state machine | ERD، migration plan و تست unique/concurrent enqueue |
| `TOPQ-C23` | بحرانی | BLOCKER | Offer روی home server ثبت می‌شود ولی Telegram فقط foreign اجرا می‌شود؛ intent ممکن است بین commit و sync گم یا دیر برسد | ثبت Offer و intent در یک تراکنش محلی و sync durable بر پایه natural identity | تست قطع peer پس از commit و تحویل دقیق پس از recovery |
| `TOPQ-C24` | بحرانی | BLOCKER | crash بعد از پذیرش Telegram و قبل از ثبت `message_id` می‌تواند duplicate یا پیام گم‌شده بسازد | قرارداد AMBIGUOUS، timeout/lease fencing و reconciliation؛ idempotency key داخلی تضمین Telegram نیست | fault test در مرز send/commit بدون retry کور |
| `TOPQ-C25` | بحرانی | BLOCKER | scope پاسخ `429` صریحاً global یا chat-local اعلام نمی‌شود؛ pause فقط مقصد ممکن است storm را به مقصدهای دیگر منتقل کند | limiter دو‌سطحی bot/destination با رفتار محافظه‌کارانه و probe کنترل‌شده | تست 429 هم‌زمان چند مقصد و اثبات نبود retry storm |
| `TOPQ-C26` | بحرانی | BLOCKER | `answerCallbackQuery` زمان‌حساس است و queue طولانی progress bar کاربر را باز نگه می‌دارد یا query را stale می‌کند | SLO جدا برای P0، deadline/TTL و terminalization بدون retry دیرهنگام | p95/p99 callback latency و تست query-too-old |
| `TOPQ-C27` | بحرانی | BLOCKER | صدها call site مستقیم aiogram/raw HTTP می‌توانند scheduler را دور بزنند | inventory machine-readable، gateway اجباری و static guard در CI | صفر call site غیرمجاز در allowlist و تست runtime bypass |
| `TOPQ-C28` | بحرانی | BLOCKER | نسخه مختلط: producer قدیمی مستقیم می‌فرستد و producer/worker جدید همان event را enqueue می‌کند | rollout دو‌فازی با ownership flag و producer/consumer compatibility matrix | تست mixed-version و شمار دقیق یک side effect |
| `TOPQ-C29` | بالا | OPEN | FIFO با ID محلی یا timestamp بین Iran/foreign و در clock skew پایدار نیست | ترتیب پایدار با source identity/sequence و tie-breaker قطعی | property test reorder/sync delay/clock skew |
| `TOPQ-C30` | بالا | OPEN | strict priority می‌تواند P2/P3 یا حتی publication جدید P1 را برای همیشه گرسنه کند | aging، reserved capacity و max-wait per class بدون شکستن P0/P1 | endurance با backlog و max-age هر priority |
| `TOPQ-C31` | بالا | OPEN | publish، partial edit و terminal edit یک Offer ممکن است out-of-order یا هم‌زمان شوند | dependency/supersession و coalescing بر پایه offer version | تست publish→partial→terminal با تمام reorderها |
| `TOPQ-C32` | بالا | OPEN | snapshot payload stale می‌شود؛ rebuild کامل نیز ممکن است به داده sync‌نشده یا حذف‌شده وابسته باشد | قرارداد hybrid: identity + immutable snapshot + rebuild از آخرین state معتبر | تست تغییر قیمت/لات/status قبل از claim |
| `TOPQ-C33` | بالا | OPEN | message ID در Offer و publication state می‌تواند ناسازگار، مفقود یا متعلق به مقصد اشتباه باشد | canonical message identity و validation chat/message before edit | reconciliation دو منبع و fault message-not-found |
| `TOPQ-C34` | بالا | DECIDED | طبقه‌بندی خطا باید method و destination-aware باشد؛ `403` کانال با blocked private user یکسان نیست | قرارداد جدول بخش 13.9 برای envelope، کدها و transport | table-driven tests برای تمام response classها |
| `TOPQ-C35` | بالا | OPEN | lease کوتاه‌تر از HTTP timeout یا recovery هم‌زمان با send باعث دو worker فعال می‌شود | lease > timeout+margin، heartbeat/fencing و recheck پیش از side effect | restart/slow-network test با دو worker و یک send |
| `TOPQ-C36` | بالا | OPEN | claim با `SKIP LOCKED` view ناسازگار می‌دهد و بدون index مناسب scan/lock contention می‌سازد | query/index بر priority,next_retry,id و transaction کوتاه؛ HTTP خارج transaction | `EXPLAIN ANALYZE` و PostgreSQL concurrency test |
| `TOPQ-C37` | بالا | OPEN | رفتار DB، Redis و Telegram outage متفاوت است؛ fail-open limiter می‌تواند burst ناامن بسازد | matrix degraded mode: DB fail request، Redis fail-closed send، Telegram backlog | chaos test هر outage و drain پس از recovery |
| `TOPQ-C38` | بالا | OPEN | sync worker ممکن است statusهای execution را loop کند یا lease foreign را overwrite کند | field policy shared intent/local lease، receiver guard و natural-key upsert | sync parity و bidirectional replay tests |
| `TOPQ-C39` | بالا | OPEN | workerهای publication، trade، broadcast و notification semantics retry متفاوت دارند | migration map برای ادغام یا adapter؛ هر job فقط یک consumer owner | ownership matrix و تست نبود claim متقاطع |
| `TOPQ-C40` | بالا | OPEN | worker جدید باید در background authority، startup/shutdown و feature flag درست ثبت شود | flag پیش‌فرض خاموش، foreign-only guard و graceful cancellation | startup surface tests و task recreation evidence |
| `TOPQ-C41` | بحرانی | DECIDED | gateway ممکن است `HTTP 200 + ok=false` را success ببیند، connection pooling ندارد و Test endpoint hardcode است | parse envelope `ok/result/error`, client مشترک lifecycle-safe و mode محدود `main/test` با URL allowlisted | contract tests envelope/body/TLS و smoke Test Bot API |
| `TOPQ-C42` | بالا | OPEN | truncation فعلی `retry_after` و safety margin ناسازگار با قرارداد مالک محصول است | ذخیره مقدار خام، cap فقط با alert و config جدا برای safety margin | تست retry_after بزرگ/مفقود و زمان‌بندی دقیق |
| `TOPQ-C43` | متوسط | OPEN | backlog و audit rowها باعث رشد table/index، bloat و query degradation می‌شوند | retention/archival، index partial و vacuum capacity plan | load test چندبرابر horizon و size/query SLO |
| `TOPQ-C44` | بالا | OPEN | metrics process-local، high-cardinality یا حاوی PII می‌تواند alert را گم یا داده را افشا کند | metrics مشترک کم‌کاردینال، hash identity و ledger امن | dashboard/alert test و privacy review |
| `TOPQ-C45` | بالا | OPEN | worker foreign نباید Offer مرجع Iran را expire/mutate کند؛ market close نیز ممکن است هنگام backlog برسد | re-read state و market authority؛ فقط delivery state روی foreign | test home_server/peer outage/market-close reorder |
| `TOPQ-C46` | بالا | DECIDED | terminal text و حذف button اگر دوباره دو call شوند state نیمه‌کاره می‌سازند | یک `editMessageText` با empty inline keyboard و idempotent no-op | یک gateway call در test و receiver observation |
| `TOPQ-C47` | بالا | DECIDED | ۴۰۰ ورودی نامعتبر یا replay conflict ممکن است اشتباهاً intent بسازند | producer فقط پس از validation/admission و در transaction موفق | شمار صفر Offer/job برای تمام invalid manifest |
| `TOPQ-C48` | بحرانی | DECIDED | token/channel/base URL اشتباه می‌تواند load staging را به production بفرستد یا داده تست پس از اجرا باقی بماند | environment fingerprint، allowlist مقصد، preflight fail-closed و cleanup محدود به `run_id` پس از export شواهد | negative test با production token/channel، refusal log و گزارش cleanup هر دو peer |
| `TOPQ-C49` | بالا | OPEN | observer ممکن است update را از دست بدهد و delivery سالم را missing گزارش کند | offset/session checkpoint، receiver dedupe و سه‌جانبه sender/API/receiver ledger | restart observer test بدون gap/duplicate |
| `TOPQ-C50` | بالا | DECIDED | تولید زنده 401/403 یا خراب‌کردن token/channel می‌تواند محیط staging را مختل کند | fault adapter برای خطاهای مخرب و جداسازی natural-response run | test matrix با عدم تغییر credential زنده |
| `TOPQ-C51` | بحرانی | DECIDED | migration graph یا head دو دیتابیس ممکن است متفاوت باشد و rollback schema pendingها را orphan کند | preflight graph/head، migration additive و rollback forward-compatible | upgrade/downgrade scratch + head equality هر دو staging DB |
| `TOPQ-C52` | بالا | OPEN | load generator خود می‌تواند bottleneck شود یا burst طبیعی را صاف کند؛ تعداد job بسیار بیشتر از تعداد Offer است | seed/trace replay، barrier concurrency و ledger مشتقات P0-P3 | generator self-metrics و تطبیق دقیق expected/actual |
| `TOPQ-C53` | بحرانی | DECIDED | مقدار staging `max_active_offers=10` یا cache آن ممکن است به production نشت کند | authority Iran staging، environment guard و readback دو peer | preflight ثابت کند staging=10 و default/production=4 |
| `TOPQ-C54` | بالا | DECIDED | payload، error body، bot token، MTProto session و fixture ممکن است PII/secret افشا کنند | کمینه‌سازی payload، redaction، encryption/permission و secret rotation | secret scan، log review و دسترسی حداقلی |
| `TOPQ-C55` | متوسط | OPEN | job permanent/ambiguous بدون ابزار pause، inspect، retry امن یا cancel عملیاتی گیر می‌کند | operator command audited با dry-run و dedupe guard | runbook exercise روی job مصنوعی |
| `TOPQ-C56` | بحرانی | BLOCKER | retry بعد از `retry_after` ممکن است publication یا پیام عملیاتی منقضی و نادرست را دیرهنگام ارسال کند | revalidation اجباری درست پیش از dispatch و قرارداد freshness به تفکیک نوع job در بخش 13.8 | fault test با `retry_after` بزرگ‌تر از عمر آفر/درخواست و شمار صفر پیام stale |
| `TOPQ-C57` | بالا | DECIDED | پیام نتیجه معامله یکی از دو طرف ممکن است در P2 بیش از پنج ثانیه معطل بماند | deadline از commit معامله، ارتقای مستقل هر recipient به P0 و حفظ `retry_after`/cooldown | تست یک طرف sent و طرف دیگر pending، مرز دقیق پنج ثانیه و صفر bypass محدودیت Telegram |
| `TOPQ-C58` | بحرانی | BLOCKER | حالت ازدحام edit با newest-first و stale-tail ممکن است edit قدیمی را هرگز اجرا نکند یا raw job count آستانه را اشتباه فعال کند | آستانه بیش از ۳۰ Offer یکتا، یک edit مؤثر برای هر Offer و catch-up در ظرفیت آزاد/پس از بازار | تست ۳۰ در برابر ۳۱ Offer، coalescing چند edit و تخلیه نهایی تمام staleها |

### 13.5 چالش‌های غیرفنی و عملیاتی

| شناسه | شدت | وضعیت | چالش و اثر | تصمیم یا کنترل لازم | شاهد بسته‌شدن |
| --- | --- | --- | --- | --- | --- |
| `TOPQ-N01` | بحرانی | BLOCKER | تعریف «ثبت موفق» روشن است، اما SLO دیده‌شدن در کانال و سقف lag هنوز مشخص نیست | SLO publication/backlog و تعریف حالت breach | تأیید مالک محصول و alert threshold ثبت‌شده |
| `TOPQ-N02` | بحرانی | BLOCKER | تعارض ظرفیت یک کانال با expiry دو دقیقه‌ای می‌تواند انتظار کاربر را نقض کند | انتخاب صریح یکی از چهار سیاست بخش 13.2 | ADR امضاشده و acceptance test متناظر |
| `TOPQ-N03` | بالا | DECIDED | ادغام دو پیام Bot نباید اطلاعات، دکمه انقضا یا تفاوت copy مسیر legacy/text را از بین ببرد | یک پیام ترکیبی با success همان مسیر، متن «لفظ شما» و دکمه انقضا؛ WebApp بدون تغییر | نمونه متن در `2026-07-16` تأیید شد؛ snapshot هر دو مسیر Bot و اثبات صفر `sendMessage` خصوصی دوم |
| `TOPQ-N04` | بالا | OPEN | کاربر نباید خطای داخلی ببیند، اما support باید offer منتشرنشده را قابل پیگیری بداند | شناسه داخلی، admin view و پاسخ پشتیبانی بدون افشای جزئیات | سناریوی support و زمان پاسخ مشخص |
| `TOPQ-N05` | بحرانی | BLOCKER | owner alert و on-call هنوز مشخص نیست؛ pause/resume نباید مسیر عادی تست باشد | runner با stop condition و circuit breaker خودکار؛ pause/resume فقط break-glass و با حفظ صف، به‌علاوه RACI محصول/backend/عملیات/امنیت | نام roleها، escalation، زمان پاسخ و تمرین pause/resume بدون گم‌شدن job |
| `TOPQ-N06` | بالا | OPEN | ساخت و نگهداری ۸۰ حساب، channel و observer هزینه و کار عملیاتی دارد | بودجه، owner credential و برنامه rotation/recovery | inventory بدون secret و health check دوره‌ای |
| `TOPQ-N07` | بالا | DECIDED | نمونه‌برداری production حتی read-only ریسک حریم خصوصی و دسترسی بیش از نیاز دارد | query allowlist، anonymization و retention کوتاه | privacy review و artifact غیرهویتی |
| `TOPQ-N08` | متوسط | DECIDED | load ده‌دقیقه‌ای و endurance می‌تواند staging مشترک را برای تیم‌های دیگر غیرقابل‌استفاده کند | پنجره تست، رزرو محیط و اعلان شروع/پایان | تقویم اجرا و sign-off مسئول staging |
| `TOPQ-N09` | متوسط | DECIDED | raw evidence حجیم است و بدون retention/versioning قابل بازبینی نیست | artifactهای نسخه‌دار و AI-readable طبق بخش 13.7، retention و checksum | manifest و گزارش reconciliation قابل بازتولید و تحلیل ماشینی |
| `TOPQ-N10` | بالا | DECIDED | اختیار go/no-go، stop test، rollback و production canary مبهم است | gate owner و stop conditions غیرقابل‌چشم‌پوشی | checklist امضاشده قبل از هر deploy |
| `TOPQ-N11` | متوسط | DECIDED | scope creep به کانال دوم، batching، paid broadcast یا bot واسط | حفظ one-bot/one-channel؛ staging از بات و کانال موجود و متصل خود استفاده می‌کند | review scope و تطبیق fingerprint بات/کانال در هر run |
| `TOPQ-N12` | بالا | DECIDED | رفتار و محدودیت Telegram تغییرپذیر است؛ interval امروز قرارداد دائمی نیست | بازبینی دوره‌ای docs و recalibration پس از رشد `429` یا تغییر Bot API | schedule و trigger ثبت‌شده |
| `TOPQ-N13` | بالا | OPEN | automation حساب‌های واقعی، flood یا session compromise می‌تواند حساب‌ها را محدود/مسدود کند | استفاده Test DC برای coverage، نرخ کنترل‌شده در main DC و review شرایط استفاده | risk acceptance و account safety runbook |
| `TOPQ-N14` | متوسط | OPEN | اپراتور ممکن است ambiguous/permanent job را با retry دستی ناامن duplicate کند | آموزش و ابزار audited؛ ممنوعیت SQL update مستقیم | tabletop exercise و audit trail |
| `TOPQ-N15` | بالا | DECIDED | Test DC عمومی‌تر است، داده‌ها دوره‌ای پاک می‌شوند و flood limit می‌تواند سخت‌گیرانه‌تر باشد | عدم ذخیره داده مهم، bootstrap تکرارپذیر و جداسازی هدف correctness از rate calibration | rebuild کامل pool و اجرای smoke از صفر |
| `TOPQ-N16` | متوسط | DECIDED | cleanup هزاران پیام خود rate مصرف می‌کند و دور بعدی را آلوده می‌کند | cleanup همه داده‌ها و پیام‌های ساخته‌شده همان `run_id` خارج measurement و پس از export شواهد؛ حفظ فقط گزارش غیرهویتی | شمار صفر داده runtime متعلق به run و cooldown evidence قبل از run بعدی |
| `TOPQ-N17` | بالا | OPEN | production canary اجازه بار مصنوعی ندارد، پس بعضی failure modeها آنجا قابل اثبات نیستند | تعیین اینکه چه شواهد staging برای go-live کافی است و چه چیزی فقط monitor می‌شود | canary matrix و stop threshold |
| `TOPQ-N18` | متوسط | DECIDED | alert زیاد یا P3 backlog ممکن است alert fatigue بسازد | severity، aggregation و dedupe alert | تمرین incident با alert قابل‌اقدام |

### 13.6 ADRهای اجباری پیش از Stage 3

| ADR | موضوع | چالش‌های پوشش‌داده‌شده |
| --- | --- | --- |
| `TOPQ-ADR-01` | ظرفیت کانال، SLO publication و semantics expiry در backlog | `C21`, `N01`, `N02` |
| `TOPQ-ADR-02` | schema، منبع حقیقت، dedupe و مرز atomic producer/sync | `C22`, `C23`, `C29`, `C38` |
| `TOPQ-ADR-03` | timeout/crash ambiguity، lease و reconciliation | `C07`, `C24`, `C35`, `C55` |
| `TOPQ-ADR-04` | scheduler دو‌سطحی، priority، P0 deadline، freshness، edit congestion و outage mode | `C10`, `C12`, `C25`, `C26`, `C30`, `C37`, `C56`, `C57`, `C58` |
| `TOPQ-ADR-05` | inventory gateway، ownership workerهای موجود و rollout نسخه مختلط | `C16`, `C27`, `C28`, `C39`, `C40`, `C41` |
| `TOPQ-ADR-06` | RACI، copy نهایی، incident response و go/no-go | `N03`, `N05`, `N10`, `N14` |

هر ADR باید حداقل شامل گزینه‌های ردشده، دلیل انتخاب، اثر روی داده و sync، failure mode، feature flag، migration، تست، observability و rollback باشد.

### 13.7 تصمیم‌های دور نخست حل چالش‌ها — `2026-07-16`

- WebApp باید snapshot دقیق `main` بماند. در Bot، محتوای success و preview فعلی در یک پیام ادغام می‌شود: یک `editMessageText` همراه inline keyboard انقضا جای `editMessageText + sendMessage` را می‌گیرد؛ `answerCallbackQuery` همچنان جدا اجرا می‌شود. متن نهایی ترکیبی پیش از کدنویسی باید توسط مالک محصول تأیید شود.
- حد staging از `50` به `10` اصلاح شد و production/default روی `4` می‌ماند.
- علامت terminal و حذف دکمه یک `editMessageText`، کنترل foreign-only، نبود intent برای ورودی نامعتبر، sampler غیرهویتی production، fault adapter، migration preflight و redaction تأیید شدند.
- runner نباید برای اجرای عادی به pause/resume نیاز داشته باشد. stop condition و circuit breaker خودکار اجرای ناسالم را متوقف می‌کنند؛ pause/resume فقط ابزار break-glass است و در حالت pause claim جدید متوقف، in-flight محدود تکمیل و تمام jobها پایدار می‌مانند.
- staging از بات و کانال موجود و متصل خود استفاده می‌کند. preflight باید fingerprint هر دو را قبل از اولین side effect با allowlist staging تطبیق دهد.
- پس از export و checksum شواهد، تمام داده runtime ساخته‌شده با `run_id` از DB و cache هر دو staging peer و پیام‌های Telegram همان run پاک می‌شوند. cleanup از مسیر authority و API دامنه انجام و سپس sync/readback تأیید می‌شود؛ SQL حذف مستقیم بین دو peer مجاز نیست. کاربر، بات، کانال یا تنظیم pre-existing حذف نمی‌شود؛ اگر runner خودش کاربر آزمایشی ساخته باشد، همان کاربر نیز run-scoped و قابل حذف است.
- artifact استاندارد هر run شامل `manifest.json`, `events.jsonl`, `errors.jsonl`, `reconciliation.json` و `summary.md` است. همه فایل‌ها `schema_version`, `run_id`, seed، commit، fingerprint محیط، config مؤثر، زمان UTC، شناسه job/dedupe، expected/actual و checksum دارند و token، PII و متن production در آن‌ها redacted است تا agentهای AI و انسان یک ورودی واحد و قابل بازتولید داشته باشند.

نمونه copy ترکیبی تأییدشده در `2026-07-16`:

```text
✅ لفظ شما با موفقیت در کانال ارسال شد!

لفظ شما:

{متن کامل آفر}

[❌ منقضی کردن]
```

در مسیر آفر متنی فقط جمله اول مطابق copy فعلی به `✅ لفظ شما با موفقیت در کانال منتشر شد!` تغییر می‌کند. دکمه بخشی از inline keyboard همان پیام است، نه متن پیام. طول نهایی باید در بیشینه payload تست شود و از محدودیت `editMessageText` عبور نکند.

### 13.8 قرارداد freshness پس از `retry_after` — `TOPQ-C56`

Telegram، `retry_after` را برای درخواست ناموفق ناشی از flood control تعریف می‌کند؛ این پاسخ به publication محدود نیست و send، edit، callback و cleanup همگی باید برای آن آماده باشند. رسیدن زمان retry فقط job را دوباره «واجد بررسی» می‌کند، نه اینکه ارسال payload قدیمی را مجاز کند. revalidation باید بلافاصله پیش از side effect و بعد از claim انجام شود.

| نوع job | رفتار پس از پایان `retry_after` | اگر دیگر معتبر نبود |
| --- | --- | --- |
| `answerCallbackQuery` و پاسخ تعاملی P0 | فقط تا deadline کوتاه همان تعامل قابل retry است | پس از deadline ارسال نمی‌شود، به `EXPIRED_INTERACTION` می‌رود و breach ثبت می‌شود |
| انتشار آفر جدید در کانال | Offer، status، version و `expires_at` دوباره خوانده می‌شوند | آفر terminal یا منقضی `SUPERSEDED` می‌شود و هرگز دیرهنگام منتشر نمی‌شود |
| edit معامله جزئی آفر | آخرین مقدار باقی‌مانده و version بازسازی می‌شود | اگر آفر terminal شده باشد، edit جزئی supersede و terminal edit جایگزین می‌شود |
| edit نهایی معامله، انقضا یا لغو | اگر پیام کانال وجود دارد، آخرین متن terminal و حذف دکمه دوباره اعمال می‌شود | اگر آفر هرگز منتشر نشده یا پیام قطعاً وجود ندارد، job با شاهد به no-op نهایی تبدیل می‌شود |
| پیش‌نمایش خصوصی آفر با دکمه انقضا | فعال‌بودن آفر و معتبر‌بودن دکمه بازبینی می‌شود | preview دارای دکمه stale ارسال نمی‌شود؛ در صورت نیاز فقط وضعیت نهایی فعلی ارسال می‌شود |
| درخواست معاملاتی دارای دکمه قبول یا رد | request و trade state بازخوانی می‌شوند | دکمه stale ارسال نمی‌شود و notification وضعیت نهایی جایگزین آن می‌شود |
| اعلان نهایی و غیرقابل‌تغییر معامله به دو طرف | از receipt immutable و وضعیت نهایی بازسازی می‌شود | چون نتیجه هنوز صحیح و لازم است، بدون دکمه stale تحویل ادامه می‌یابد |
| پیام مدیریتی یا bulk | TTL خود job بازخوانی می‌شود | پس از TTL به `SUPERSEDED` می‌رود و دیرهنگام ارسال نمی‌شود |
| حذف پیام‌های تست | تعلق پیام به همان `run_id` و allowlist staging دوباره کنترل می‌شود | حذف متوقف و مورد برای cleanup بعدی ثبت می‌شود؛ مقصد دیگری لمس نمی‌شود |

این بخش هنوز `BLOCKER` است تا مالک محصول رفتار دو ردیف «پیش‌نمایش خصوصی» و «درخواست معاملاتی» را تأیید کند و stateهای `SUPERSEDED` و `EXPIRED_INTERACTION` وارد ADR-04 شوند. مقدار خام `retry_after` باید بدون truncation ذخیره شود؛ cap فعلی ۱۲۰، ۳۰۰ یا ۲۴ ساعت مبنای dispatch نخواهد بود.

### 13.9 جدول رفتار برنامه در پاسخ‌های Telegram — `TOPQ-C34`

فیلد JSON به نام `ok` مرجع موفقیت است؛ HTTP status به‌تنهایی موفقیت را اثبات نمی‌کند. `error_code` برای routing اولیه و `description` و `parameters` برای زیرطبقه و اقدام استفاده می‌شوند.

| پاسخ یا خطا | وضعیت job | retry | رفتار برنامه |
| --- | --- | --- | --- |
| HTTP موفق و `ok=true` | `SENT` | خیر | result و `message_id` ثبت، lease آزاد و metric موفقیت افزوده می‌شود |
| HTTP موفق ولی `ok=false` | براساس `error_code` | براساس ردیف متناظر | هرگز success محسوب نمی‌شود؛ envelope کامل طبقه‌بندی می‌شود |
| `400` با `message is not modified` فقط برای edit | `SENT_NOOP` | خیر | چون وضعیت مطلوب از قبل برقرار است، عملیات idempotent موفق محسوب می‌شود |
| `400` با payload، parse mode، entity یا طول نامعتبر | `TERMINAL_FAILED` | خیر | job قرنطینه، payload redacted ثبت و alert توسعه ایجاد می‌شود؛ domain object تغییر نمی‌کند |
| `400` با message not found یا message cannot be edited | `PERMANENT_UNDELIVERABLE` | خیر | message identity reconcile و stale channel state هشدار داده می‌شود؛ retry کور انجام نمی‌شود |
| پاسخ دارای `migrate_to_chat_id` | `BLOCKED_DESTINATION` | پس از اصلاح مقصد | lane مقصد pause، شناسه جدید فقط با validation و audit اعمال و سپس jobها revalidate می‌شوند |
| `401` | `BLOCKED_BOT` | خودکار خیر | تمام side effectهای همان bot متوقف، alert بحرانی credential صادر و resume فقط پس از preflight موفق انجام می‌شود |
| `403` در private chat | `PERMANENT_UNDELIVERABLE` | خیر | مقصد private مسدود علامت می‌خورد؛ سایر مقصدها ادامه می‌دهند |
| `403` در channel یا admin action | `BLOCKED_DESTINATION` | خودکار خیر | lane کانال pause و دسترسی bot/admin بررسی می‌شود؛ pendingها حذف یا failed نمی‌شوند |
| `404` برای method یا endpoint | `BLOCKED_GATEWAY` | خودکار خیر | gateway متوقف و deployment/config alert ایجاد می‌شود |
| `404` برای destination یا resource مشخص | `PERMANENT_UNDELIVERABLE` | خیر | فقط همان مقصد/job متوقف و identity آن reconcile می‌شود؛ bot سراسری pause نمی‌شود |
| `409` | `BLOCKED_BOT` | پس از رفع conflict | تضاد executor، webhook یا polling بررسی می‌شود؛ loop retry مجاز نیست |
| `429` با `retry_after` | `PENDING` | بله | مقدار خام ذخیره و تا `retry_after + safety_margin` هیچ تلاش انجام نمی‌شود؛ سپس قرارداد freshness بخش 13.8 اجرا می‌شود |
| `429` بدون `retry_after` | `PENDING` | بله | backoff محافظه‌کارانه با jitter و alert نقص پاسخ؛ retry سریع ممنوع است |
| `500` تا `599` برای edit یا عملیات idempotent | `PENDING` | بله | backoff نمایی محدود با jitter؛ پیش از هر retry revalidation انجام می‌شود |
| `500` تا `599` برای `sendMessage` پس از پذیرش احتمالی request | `AMBIGUOUS` | کورکورانه خیر | تا اثبات وجود یا نبود پیام توسط observer/reconciliation دوباره send نمی‌شود |
| خطای DNS، connect یا pool پیش از ارسال request | `PENDING` | بله | safe retry با backoff و circuit breaker انجام می‌شود |
| timeout یا قطع ارتباط پس از احتمال پذیرش `sendMessage` | `AMBIGUOUS` | کورکورانه خیر | observer و reconciliation باید وجود یا نبود پیام را ثابت کنند تا duplicate ساخته نشود |
| timeout در edit idempotent | `PENDING_RECONCILE` | پس از بازخوانی | ابتدا وضعیت پیام و domain بازخوانی و فقط آخرین edit معتبر تکرار می‌شود |
| بدنه نامعتبر یا غیر JSON با HTTP موفق | برای send برابر `AMBIGUOUS` | کورکورانه خیر | protocol alert ایجاد می‌شود؛ edit پس از revalidation قابل retry است |
| `4xx` ناشناخته | `QUARANTINED` | خودکار خیر | error جدید برای تصمیم انسانی و افزودن fixture ثبت می‌شود؛ هیچ mutation کسب‌وکار رخ نمی‌دهد |

### 13.10 توضیح چالش gateway — `TOPQ-C41`

این چالش سه نقص مستقل ولی محدود در gateway فعلی دارد و راه‌حل آن در `2026-07-16` توسط مالک محصول تأیید شد:

1. تابع فعلی هر `HTTP 200` را موفق می‌داند، حتی اگر Telegram یا fault adapter بدنه `{"ok": false}` برگرداند. قرارداد رسمی Bot API فیلد `ok` را مرجع می‌داند؛ بنابراین gateway باید ابتدا envelope را parse و بعد نتیجه را تعیین کند.
2. مسیر async برای هر پیام یک `httpx.AsyncClient` جدید می‌سازد. در بار بالا، connection و TLS مرتب از نو ساخته می‌شوند و احتمال timeout و مصرف resource بیشتر می‌شود. پیشنهاد، یک client مشترک با connection pool، timeoutهای جدا و lifecycle روشن startup/shutdown است.
3. endpoint به production Bot API hardcode شده است. پیشنهاد، mode محدود `main` یا `test` است که URL را خود برنامه از allowlist می‌سازد؛ URL دلخواه از env پذیرفته نمی‌شود. بات و کانال staging موجود همچنان از mode اصلی Telegram استفاده می‌کنند و `/test/` فقط برای ماتریس جداگانه Test DC است، نه تنظیم نرخ staging.

تغییر پیشنهادی هیچ متن یا رفتار محصولی را عوض نمی‌کند؛ فقط تشخیص نتیجه، پایداری connection و جداسازی محیط را درست می‌کند.

### 13.11 پیشنهاد production canary — `TOPQ-N17`

این پیشنهاد هنوز تصمیم قطعی نیست:

1. پیش از production، حداقل ده اجرای کامل ده‌دقیقه‌ای با seedهای متفاوت در چند پنجره زمانی و یک endurance شصت‌دقیقه‌ای در staging انجام شود. نقض critical باید صفر باشد.
2. پیش از تحویل مالکیت، یک shadow planner/auditor ایزوله برای یک چرخه کامل بازار و حداکثر ۲۴ ساعت در production اجرا شود. این جزء صف قابل‌ارسال نیست، job آن هرگز قابل promote نیست و فقط intent فرضی، priority، eligibility، payload hash، sync delay و lag را با side effect واقعی مسیر فعلی مقایسه می‌کند.
3. چون فقط یک کانال داریم، canary درصدی بین direct sender و queue ممنوع است. مالکیت ارسال باید اتمیک و برای کل کانال در یک پنجره کم‌ترافیک منتقل شود.
4. پس از فعال‌سازی، بازه‌های کنترل ۳۰ دقیقه، ۲ ساعت و ۲۴ ساعت اجرا شوند و فقط ترافیک طبیعی production مشاهده شود؛ بار مصنوعی ممنوع است.
5. مشاهده حتی یک duplicate، انتشار آفر stale، مقصد production/staging اشتباه، از‌دست‌رفتن job یا اختلاف قطعی DB/sync موجب stop خودکار می‌شود. عبور backlog، P0 latency یا oldest-job از SLO مصوب نیز stop condition است.
6. rollback نباید pending و ambiguous را رها و direct sender را فوراً روشن کند؛ ابتدا claim متوقف، jobهای in-flight و ambiguous reconcile و سپس ownership با runbook اتمیک برگردانده می‌شود.

## 14. معیار پذیرش نهایی

- پیش از شروع Stage 3، همه موارد `BLOCKER` به `DECIDED` تبدیل و ADRهای بخش 13.6 تأیید شده باشند.
- هر چالش فنی و غیرفنی owner، stage هدف، تست یا شاهد پذیرش و rollback داشته باشد؛ مورد بدون شاهد «بسته» محسوب نمی‌شود.
- ظرفیت/عمر آفر و معنای SLO publication به تصمیم صریح محصول رسیده باشد؛ backlog مورد انتظار نباید بعداً به‌عنوان باگ ناشناخته گزارش شود.
- WebApp با snapshot رفتار فعلی `main` یکسان باشد؛ در Bot هر مسیر فقط یک پیام ترکیبی موفقیت/«لفظ شما» با دکمه انقضا داشته باشد، `sendMessage` خصوصی دوم صفر باشد و خطای Telegram پس از commit هیچ copy غیرعادی به کاربر نشان ندهد.
- نرخ پذیرش حداقل سه آفر در ثانیه بدون ازدست‌رفتن رکورد صف تأیید شود.
- هر آفر یک پیام مستقل داشته باشد و batching وجود نداشته باشد.
- هیچ `429` به `FAILED` یا انقضای زودهنگام آفر منجر نشود و هیچ publication یا دکمه عملیاتی stale پس از پایان عمر کسب‌وکار ارسال نشود.
- همه retryها `retry_after` را رعایت کنند.
- اولویت P0 تا P3 و ترتیب داخلی P1 در تست اثبات شود.
- اعلان معاملاتی pending هر recipient در مرز پنج ثانیه به P0 ارتقا یابد، بدون اینکه `retry_after` یا cooldown را دور بزند.
- حالت ازدحام edit فقط در `31` Offer یکتای pending فعال شود؛ active lot-partial سپس traded-terminal و سپس سایر editها را با newest-first انتخاب کند و تمام editهای بالای پنج دقیقه را در stale tail نگه دارد.
- چند edit یک Offer به یک state نهایی coalesce شوند و catch-up ثابت کند پس از پایان بار هیچ stale edit دائمی باقی نمی‌ماند.
- علامت terminal و حذف دکمه کانال یک API call باشند.
- هیچ مسیر مستقیم send/edit کانال limiter را دور نزند.
- هیچ duplicate کنترل‌نشده در restart، concurrency یا retry تولید نشود.
- تمام کالاها و حالت‌های معنادار معامله پوشش داده شوند.
- دقیقاً `400` ورودی نامعتبر در موقعیت‌های تصادفیِ seedدار میان جریان بازار پخش شوند و هیچ‌کدام publication intent نسازند.
- در هر دور دقیقاً `1800` آفر معتبر پذیرفته شود و نرخ پذیرش `3 valid offer/s` حفظ شود.
- تمام پاسخ‌های طبیعی Telegram که سناریوی کسب‌وکار تولید می‌کند واقعاً در مقصد آزمایشی دریافت و با event ledger تطبیق داده شوند.
- خطاهای فنی Bot API و transport در ماتریس تاب‌آوری جداگانه به رفتار مورد انتظار نگاشت شوند.
- fixture برگرفته از production فقط شامل الگوی غیرهویتی باشد و runner محیط staging هیچ اتصال مستقیمی به production نداشته باشد.
- preflight مقدار `max_active_offers=10` را در هر دو سطح Iran/foreign staging تأیید کند و هم‌زمان ثابت بماند که default/production همچنان `4` است.
- interval نهایی با چندین دور staging و گزارش goodput انتخاب شود.
- backlog، سن قدیمی‌ترین پیام و زمان تخلیه قابل مشاهده باشند.
- گزارش reconciliation خطاهای فعال را از خطاهای تاریخی جدا کند.
- artifactهای AI-readable پیش از cleanup export و checksum شوند و سپس هیچ داده runtime یا پیام Telegram متعلق به `run_id` در staging باقی نماند.
- هیچ داده یا کانال production در آزمایش staging تغییر نکند.
- production deploy فقط در Stage جدا و با دستور صریح انجام شود.

## 15. منابع پایه

- Telegram Bots FAQ: `https://core.telegram.org/bots/faq`
- Telegram Bot API response envelope: `https://core.telegram.org/bots/api#making-requests`
- Telegram Bot API `ResponseParameters.retry_after`: `https://core.telegram.org/bots/api#responseparameters`
- Telegram Bot API `answerCallbackQuery`: `https://core.telegram.org/bots/api#answercallbackquery`
- Telegram Bot API `editMessageText`: `https://core.telegram.org/bots/api#editmessagetext`
- Telegram dedicated test environment: `https://core.telegram.org/bots/features#dedicated-test-environment`
- Telegram Test DC accounts and data warning: `https://core.telegram.org/api/auth#test-accounts`
- PostgreSQL `SKIP LOCKED` queue semantics: `https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE`
- `models/offer_publication_state.py`
- `models/telegram_notification_outbox.py`
- `core/services/telegram_offer_publication_service.py`
- `core/services/telegram_offer_channel_service.py`
- `core/services/offer_publication_reconciliation_service.py`
- `core/offer_publication_worker.py`
- `core/telegram_notification_outbox_worker.py`
- `core/telegram_admin_broadcast_worker.py`
- `core/trade_delivery_worker.py`
- `core/telegram_gateway.py`
- `core/background_job_authority.py`
- `core/sync_field_policy.py`
- `core/events.py`
- `api/routers/sync.py`
- `bot/handlers/trade_create.py`
- `api/routers/offers.py`
- `run_bot.py`
