# Roadmap صف پایدار انتشار آفر در تلگرام

## وضعیت سند

- تاریخ ایجاد: `2026-07-15`
- شاخه اختصاصی: `candidate/telegram-offer-publication-queue`
- مبنای شاخه: `main@ca6348af`
- وضعیت Roadmap: Stage 0 و Stage 1 ثبت شده‌اند. ممیزی closure در `2026-07-16` تمام تصمیم‌های اولیه challenge register را بست؛ شش ADR اولیه و قرارداد pure نهایی `M0` تا `M7` ابتدا با `44` تست قرارداد و `171` تست هدفمند/regression پاس شدند. همان روز ADR هفتم برای `channel_editor` پذیرفته و برش foundation مرحله ۳ ممیزی شد: `223` تست unit/regression و `20` تست PostgreSQL واقعی، همراه upgrade→downgrade→upgrade migration پاس شدند. runtime صف همچنان code-disabled و Stage 3 هنوز کامل نیست؛ هیچ deploy انجام نشده و فعال‌شدن editor منوط به تکمیل کد و smoke/benchmark Stage 4 است.
- این سند مجوز deploy به staging یا production نیست.
- تمام مستندات، تست‌ها و کدنویسی بعدی این موضوع باید در همین شاخه مستقل ادامه پیدا کنند، مگر اینکه مالک محصول صریحاً مسیر دیگری تعیین کند.

این Roadmap مسیر رفع خطاهای انتشار آفر در کانال تلگرام را از ثبت پایدار آفر تا صف، اولویت‌بندی، retry، آزمایش تکرارشونده staging و آمادگی انتشار مشخص می‌کند.

## 1. هدف

سیستم باید بتواند در زمان پیک حداقل `3` آفر در ثانیه را بدون ازدست‌دادن رکورد بپذیرد، هر آفر واجد انتشار را پیش از deadline کسب‌وکار به‌صورت یک پیام مستقل در همان کانال فعلی منتشر کند و خطاهای موقت تلگرام را بدون نمایش وضعیت غیرعادی به کاربر مدیریت کند. مسیر پایه primary-only و مسیر کاندید primary+channel-editor با همان trace در staging سنجیده می‌شوند. اگر یک کانال و عمر فعلی آفر معیارها را در هیچ مسیر امنی برآورده نکنند، نتیجه release برابر `NO-GO` است؛ صف مجاز نیست شکست ظرفیت را با انتشار stale، تغییر پنهانی expiry یا اعلام موفقیت کاذب بپوشاند.

تفکیک دو ظرفیت الزامی است:

1. **ظرفیت پذیرش:** ثبت پایدار حداقل سه آفر در ثانیه در دیتابیس و صف.
2. **ظرفیت تحویل کانال:** سرعتی که تلگرام در آزمایش تکرارشونده برای همان بات و کانال staging واقعاً می‌پذیرد.

صف اختلاف کوتاه‌مدت این دو ظرفیت را بدون حذف یا fail اشتباه نگهداری می‌کند. صف تضمین نمی‌کند کانال بتواند سه پیام در ثانیه را دائماً بپذیرد؛ عدد تحویل فقط با شواهد staging تعیین می‌شود و ناتوانی در تحویل همه آفرهای واجد انتشار پیش از deadline، گیت production را رد می‌کند.

## 2. تصمیم‌های قطعی

موارد زیر در این گفتگو تأیید شده‌اند و تغییر آن‌ها نیازمند تصمیم جدید مالک محصول است:

- معماری محصول یک کانال باقی می‌ماند و دو bot role در execution plane پیش‌بینی می‌شود: `primary` و `channel_editor`.
- کانال جدید اضافه نمی‌شود.
- publisher، بات واسط، API poller یا بات کاربرمحور دوم اضافه نمی‌شود؛ `channel_editor` فقط editهای کانال را زیر مالکیت main queue اجرا می‌کند.
- primary همه publicationها، callbackها، پیام‌های خصوصی، معامله و مدیریت را اجرا می‌کند. editor فقط methodهای edit کانال allowlisted را دارد و حق send/private interaction ندارد.
- editor برای کشف پیام به update بات اصلی یا API polling متکی نیست؛ `chat_id/message_id` canonical از publication state به job می‌رسد.
- fallback خودکار میان primary و editor ممنوع است. outage یا revoke editor، job edit را pending/blocked نگه می‌دارد و primary همان job را تصاحب نمی‌کند.
- tokenها در DB/job/log ذخیره نمی‌شوند؛ `bot_identity` immutable job فقط به credential allowlisted foreign نگاشت می‌شود.
- lane مقصد کانال در شروع میان هر دو bot role مشترک است. جداسازی یا افزایش rate فقط با شاهد Stage 4 مجاز است؛ وجود دو token به‌تنهایی اثبات افزایش ظرفیت کانال نیست.
- editor در کانال کمترین دسترسی لازم `can_edit_messages` را دارد و preflight باید نبود دسترسی‌های post/delete/promote اضافی را تا حدی که Telegram اجازه می‌دهد تأیید کند.
- Paid Broadcast راه‌حل این چالش در نظر گرفته نمی‌شود.
- هر آفر یک پیام مستقل در کانال دارد.
- انتشار دسته‌ای چند آفر در یک پیام رد شده است.
- ثبت آفر از اجرای side effect تلگرام جدا می‌شود.
- پس از ثبت پایدار آفر، کاربر از صف، `429` یا retry مطلع نمی‌شود. WebApp همان رفتار فعلی `main` یعنی بستن preview، پاک‌کردن draft و refresh بازار بدون success toast جدید را حفظ می‌کند.
- در Bot، اطلاعات دو پیام فعلی با یکدیگر ادغام می‌شوند: همان پیام preview/تأیید با یک `editMessageText` به متن موفقیت همان مسیر به‌اضافه بلوک «لفظ شما» و دکمه انقضا تبدیل می‌شود و `bot.send_message` خصوصی دوم حذف خواهد شد. `answerCallbackQuery` همچنان درخواست جدا و ضروری `M0` است؛ بنابراین عملیات محتوایی از دو به یک و پیام قابل‌مشاهده نهایی از دو به یک کاهش می‌یابد.
- خطای موقت تلگرام نباید آفر فعال را منقضی کند.
- پاسخ `429` هرگز publication را `FAILED` نمی‌کند.
- پیام دریافت‌کننده `429` در صف می‌ماند و پس از `retry_after` به‌اضافه حاشیه کوچک اطمینان دوباره ارسال می‌شود.
- آزمایش مجدد هر `0.1` ثانیه در زمان `retry_after` مجاز نیست.
- فاصله نهایی ارسال از قبل hardcode نمی‌شود و فقط با آزمایش‌های پرتکرار staging انتخاب می‌شود.
- ارسال پیام، ویرایش متن و ویرایش دکمه‌های کانال از یک سهم مشترک مقصد استفاده می‌کنند.
- علامت معامله/انقضا و حذف دکمه‌های همان پیام باید با یک درخواست `editMessageText` انجام شوند.
- حذف دکمه پیش‌نمایش خصوصی کاربر یک عملیات جدا در گفت‌وگوی خصوصی است و از سهم اختصاصی کانال مصرف نمی‌کند.
- صف اصلی تنها مالک اجرای Bot API، limiter، cooldown، retry فنی و اولویت نهایی `M0` تا `M7` است. صف‌های دامنه‌ای موجود یا آینده فقط به‌عنوان صف تابع/feeder تصمیم می‌گیرند کدام کار منطقی آماده ورود به صف اصلی است و حق ارسال مستقیم به Telegram ندارند.
- صف گیرندگان broadcast مدیریتی حفظ، ولی worker مستقیم آن به feeder تبدیل می‌شود. در شروع هر broadcast فقط یک گیرنده به‌صورت in-flight به صف اصلی آزاد می‌شود؛ پیشروی پس از نتیجه terminal مجاز، و توقف روی retryable، ambiguous یا خطای عمومی campaign انجام می‌شود.
- ثبت/انتشار آفر و edit آفرهای موجود در کانال دو صف تابع مستقل‌اند. استقلال آن‌ها dependency را حذف نمی‌کند: edit تا وجود `channel_message_id` واجد ارسال نیست و اگر آفر پیش از انتشار terminal شود، انتشار فعال و edit میانی هر دو با وضعیت نهایی supersede می‌شوند.
- ترتیب داخلی شش صف تابع در بخش ۳ همیشه فعال است و فقط به حالت ازدحام بیش از ۳۰ آفر محدود نمی‌شود.
- ترتیب `M0` تأییدشده عبارت است از callback و کد ورود deadlineدار، سپس اعلان معامله عبورکرده از پنج ثانیه و سپس انتشار آفر جدید.
- برای کاربر ثبت‌نام‌شده، بات نباید با حذف پیام یا `ReplyKeyboardRemove` او را مجبور کند برای بازیابی منوی اصلی دوباره `/start` بفرستد. پیام‌های بات، به‌خصوص پیام لنگر، تا حد امکان حفظ می‌شوند؛ کیبورد موقت ثبت‌نام/اتصال حساب در پایان جریان با منوی اصلی persistent جایگزین می‌شود. این تصمیم شامل inline keyboard آفر که باید در معامله/انقضا حذف شود نیست.
- داده production فقط به‌صورت read-only برای انتخاب fixture و ترکیب بار استفاده می‌شود؛ تست نباید production را mutate کند.
- تمام پاسخ‌های طبیعی Telegram که در اثر آفر صحیح، آفر ناصحیح، معامله، رقابت، انقضا و پیام مدیریتی به کاربر یا کانال می‌رسند باید واقعاً از Telegram آزمایشی ارسال، در مقصد دریافت و با ledger تطبیق داده شوند؛ mock به‌تنهایی مدرک این معیار نیست.
- ماتریس فنی خطاهای Bot API و transport نیز جداگانه تست می‌شود، اما منظور مالک محصول از «تمام پاسخ‌های Telegram» این ماتریس خطا نبود.
- هر دور ترکیبی ده‌دقیقه‌ای شامل `1800` آفر معتبر به‌اضافه دقیقاً `400` تلاش نامعتبرِ تصادفی و تکرارپذیر است؛ در مجموع `2200` تلاش ثبت.
- الگوی آفرهای معتبر از آمار و نمونه‌های پاک‌سازی‌شده جدول آفرهای production با دسترسی read-only ساخته می‌شود؛ شناسه کاربر، متن واقعی توضیحات، شناسه پیام و سایر داده‌های هویتی کپی نمی‌شوند.
- فقط در دیتابیس ایزوله staging، مقدار `max_active_offers` از `4` به `10` افزایش می‌یابد. این مقدار جایگزین تصمیم قبلی `50` است.
- مقدار پیش‌فرض کد، فایل عمومی تنظیمات و production روی `4` باقی می‌ماند؛ مقدار `10` باید از مرجع Iran staging ثبت، به foreign staging sync و در cache هر دو سمت تأیید شود.
- داده و اجرای تلگرام همچنان تابع مرزبندی فعلی است: اجرای Telegram فقط روی سرور foreign انجام می‌شود.
- تصمیم editor در `TOPQ-ADR-07` فقط بند تعداد credential را اصلاح می‌کند؛ یک کانال، یک پیام برای هر آفر، no-batching، SLO و تمام اولویت‌های این سند بدون تغییرند.

## 3. اولویت قطعی صف اصلی و صف‌های تابع

عدد کمتر در هر دو سطح به معنی اولویت بالاتر است. اولویت داخلی فقط مشخص می‌کند هر صف تابع کدام کار آماده خود را معرفی کند؛ اولویت `M0` تا `M7` ترتیب نهایی صف اصلی را تعیین می‌کند.

### 3.1 اولویت صف اصلی

| اولویت | ترتیب میان کارهای هم‌سطح |
| --- | --- |
| `M0` | callback و کد ورود deadlineدار؛ اعلان معامله عبورکرده از پنج ثانیه؛ انتشار آفر جدید |
| `M1` | تبدیل preview به پیام موفقیت؛ اعلان عادی دو طرف معامله پیش از پنج ثانیه؛ پاسخ آفر نامعتبر/بازار بسته/محدودیت؛ سایر پاسخ‌های فوری بات |
| `M2` | edit آفر لات‌بندی‌شده فعال با معامله جزئی؛ اعلان عضویت کاربر جدید |
| `M3` | edit آفر کاملاً معامله‌شده |
| `M4` | اعلان باز/بسته‌شدن بازار؛ edit آفر منقضی؛ edit آفر لغوشده؛ حذف دکمه‌ای که اقدام نامعتبر را ممکن می‌کند |
| `M5` | اعمال/رفع محدودیت یا مسدودیت، فعال/غیرفعال/حذف حساب و اعلان فردی سیستمی |
| `M6` | broadcast مدیریتی، اعلان عمومی و پیام غیرضروری بازار |
| `M7` | حذف پیام موقت و cleanup صرفاً ظاهری |

در یک ردیف، ترتیب صریح جدول مقدم است؛ بعد deadline و سپس زمان ورود/FIFO ملاک است. صف اصلی کار را از head آماده صف‌های تابع pull می‌کند و feeder حق ندارد تعداد بزرگی job را یک‌جا وارد main کند.

«دریافت» اعلان معامله یعنی Bot API برای همان job پاسخ موفق `ok=true` داده باشد. `delivery_deadline_at = trade_committed_at + 5s` است و فقط پیام هر گیرنده که هنوز پاسخ موفق نگرفته از `M1` به `M0` ارتقا می‌یابد. این ارتقا `retry_after` یا cooldown مقصد را دور نمی‌زند.

### 3.2 صف تابع ثبت و کنترل آفر

| داخلی | کار | اصلی |
| ---: | --- | --- |
| `0` | انتشار آفر تأییدشده Bot یا WebApp در کانال | `M0` |
| `1` | تبدیل preview به پیام موفقیت همراه «لفظ شما» و دکمه انقضا | `M1` |
| `2` | پاسخ آفر نامعتبر، بازار بسته یا عبور از محدودیت آفر فعال | `M1` |
| `3` | پاسخ فوری درخواست انقضا | مستقیم `M0` |
| `4` | حذف دکمه انقضا از preview خصوصی | `M4` |
| `5` | پاسخ درخواست تکراری یا آفر دیگر فعال‌نیست | `M1` |
| `6` | repair/reconciliation موارد نامشخص | `M6` |

پاسخ callback منتظر نوبت feeder نمی‌ماند. شکست side effect تلگرام پس از commit آفر، متن غیرعادی صف یا retry به کاربر نشان نمی‌دهد.

### 3.3 صف تابع ویرایش آفرهای کانال

| داخلی | کار | اصلی |
| ---: | --- | --- |
| `0` | جدیدترین آفر لات‌بندی‌شده active که بخشی از آن معامله شده | `M2` |
| `1` | جدیدترین آفر کاملاً معامله‌شده | `M3` |
| `2` | جدیدترین آفر منقضی‌شده | `M4` |
| `3` | جدیدترین آفر لغوشده یا غیرفعال‌شده | `M4` |
| `4` | سایر اصلاحات مقدار، متن یا دکمه آفر active | `M5` |
| `5` | edit ترمیمی و reconciliation | `M6` |

قواعد این صف همیشه فعال‌اند:

1. «جدیدترین» بر اساس `offer.created_at` است، نه زمان ساخت یا supersede شدن job.
2. هر Offer فقط یک edit مؤثر دارد؛ coalescing نباید `first_edit_enqueued_at` را reset کند.
3. اگر آفر ردیف داخلی `0` پیش از dispatch کاملاً معامله شود، edit جزئی حذف و با edit ردیف `1` جایگزین می‌شود.
4. edit بدون `channel_message_id` ارسال نمی‌شود و تا تعیین تکلیف publication وابسته می‌ماند.
5. edit با سن بیش از پنج دقیقه در stale bucket انتهای همان طبقه قرار می‌گیرد؛ newest-first در هر bucket حفظ می‌شود.
6. برای جلوگیری از starvation، پس از هر `20` edit تازه و موفق در feeder کانال، اگر stale edit واجد ارسال وجود داشته باشد، نوبت بعدی همان feeder برای یک stale edit رزرو می‌شود. این رزرو اولویت اصلی `M0` تا `M7`، deadline یا cooldown را دور نمی‌زند. عدد `20` مقدار اولیه قطعی است و Stage 4 فقط مجاز است با شاهد آن را تنظیم کند.

### 3.4 صف تابع پیام‌های معاملاتی و درخواست معامله

| داخلی | کار | اصلی |
| ---: | --- | --- |
| `0` | اعلان نتیجه برای خریدار و فروشنده | `M1` و پس از پنج ثانیه مستقل به `M0` |
| `1` | پاسخ فوری تأیید، رد یا رقابت درخواست | callback در `M0`، پیام عادی در `M1` |
| `2` | پیشنهاد لات یا مقدار جایگزین | `M1` |
| `3` | پاسخ عدم دسترسی به درخواست‌کننده بازنده | `M1` |
| `4` | اصلاح/حذف دکمه پیشنهاد خصوصی قدیمی | `M4` |
| `5` | سایر اعلان‌های معاملاتی غیرضروری | `M5` |

دو پیام نتیجه معامله هم‌زمان eligible و مستقل‌اند؛ موفقیت یا شکست یک طرف، طرف دیگر را block نمی‌کند.

### 3.5 صف تابع پیام‌های مدیریتی و سیستمی

| داخلی | کار | اصلی |
| ---: | --- | --- |
| `0` | اعلان عضویت کاربر جدید | `M2` |
| `1` | محدودیت/مسدودیت/رفع آن و فعال/غیرفعال/حذف حساب | `M5` |
| `2` | پیام فوری مدیر برای یک کاربر مشخص | `M5` |
| `3` | broadcast دستی مدیر | `M6` |
| `4` | اعلان عمومی یا سیستمی غیر فوری | `M6` |
| `5` | گزارش یا یادآوری کم‌اهمیت | `M7` |

هر campaign، از جمله fan-out عضویت کاربر جدید، ابتدا فقط یک گیرنده in-flight دارد. برای broadcastهای `M6` در مجموع حداکثر `2` گیرنده متعلق به campaignهای متفاوت in-flight می‌شوند و campaignها پس از هر نتیجه terminal به‌صورت round-robin بر پایه قدیمی‌ترین `last_released_at` نوبت می‌گیرند. نتیجه retryable یا ambiguous فقط همان campaign را نگه می‌دارد؛ خطای سراسری bot/gateway circuit-break همه campaignها را متوقف می‌کند.

### 3.6 صف تابع اعلان وضعیت بازار

| داخلی | کار | اصلی |
| ---: | --- | --- |
| `0` | باز یا بسته‌شدن بازار | `M4` |
| `1` | اصلاح اعلان وضعیت جاری | `M4` |
| `2` | یادآوری یا اعلان عمومی زمان‌بندی | `M6` |
| `3` | اعلان stale و ناسازگار با وضعیت جاری | ارسال نمی‌شود |

در `M4` اعلان وضعیت بازار پیش از edit انقضا انتخاب می‌شود تا پیام بسته‌شدن پشت موج editها نماند.

### 3.7 صف تابع عملیات زمان‌دار بات

فقط کارهای رسیده به `due_at` eligible هستند:

| داخلی | کار | اصلی |
| ---: | --- | --- |
| `0` | حذف دکمه‌ای که اقدام نامعتبر ایجاد می‌کند | `M4` |
| `1` | عملیات زمان‌دار امنیتی یا حساب | `M5` |
| `2` | اعلان تأخیری رفع محدودیت پس از revalidation | `M5` |
| `3` | حذف پیام خطا یا پیام موقت | `M7` |
| `4` | cleanup صرفاً ظاهری | `M7` |

### 3.8 ورودی‌های مستقیم صف اصلی و قواعد مشترک

- کد ورود، callback deadlineدار و پاسخ تعاملی فوری مستقیماً وارد `M0` می‌شوند.
- onboarding، اتصال حساب، منو و پاسخ فوری غیر deadlineدار وارد `M1` می‌شوند.
- reconciliation/ambiguous یک control plane است و صف ارسال جدا محسوب نمی‌شود.
- `429` اولویت را تغییر نمی‌دهد؛ همان job با `retry_after + safety_margin` می‌ماند.
- cooldown یک مقصد، مقصد آماده دیگر را block نمی‌کند.
- صف تابع retry فنی، limiter یا Bot API call مستقل ندارد.

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
- به‌روزرسانی پیام کانال یک عملیات مستقل `M4` است.

### انقضای خودکار

- عملیات خصوصی کاربر وجود ندارد.
- فقط وضعیت دیتابیس و یک عملیات ترکیبی کانال برای علامت انقضا و حذف دکمه‌ها ثبت می‌شود.

### پایداری منوی اصلی و پیام لنگر

- هدف این نیست که Telegram نتواند کیبورد را به انتخاب کاربر جمع کند؛ هدف این است که کاربر ثبت‌نام‌شده برای بازیابی منوی بات مجبور به ارسال دوباره `/start` نباشد.
- پیام‌های بات به‌صورت پیش‌فرض حذف نمی‌شوند. حذف خودکار فقط با علت محصولی روشن، TTL و مالکیت مشخص مجاز است.
- پیام لنگر تا وقتی همان لنگر فعال است حذف نمی‌شود و جایگزینی لنگر باید ابتدا منوی جایگزین را پایدار کند و سپس در صورت نیاز پیام قبلی را تعیین تکلیف کند.
- در مسیرهای authenticated، ارسال `ReplyKeyboardRemove` بدون جایگزینی فوری با کیبورد persistent ممنوع است.
- کیبوردهای موقت ثبت‌نام یا اتصال حساب می‌توانند محتوای context-specific داشته باشند، اما پایان موفق، خطا، cancel و timeout جریان باید منوی persistent مناسب نقش کاربر را بازگرداند.
- inline keyboard آفر، معامله و پیشنهاد از این قاعده مستثناست و هنگام انقضا، معامله یا از‌دست‌رفتن اعتبار باید حذف شود.
- `is_persistent=True` درخواست نمایش پایدار به Telegram client است؛ برنامه تضمین نمی‌کند کاربر یا client هرگز کیبورد را به‌صورت دستی جمع نکند.

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

- bot identity/role بدون token یا secret
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

ممیزی Stage 2 نشان داده است جدول‌های فعلی قرارداد عمومی را پوشش نمی‌دهند؛ بنابراین migration افزایشی جدول `telegram_delivery_jobs` قطعی است. جدول‌های موجود حذف نمی‌شوند و پشت feature flag به feeder/projection سازگار تبدیل می‌شوند تا rollout و rollback بدون از‌دست‌رفتن state انجام شود.

### 6.3 scheduler مشترک

scheduler سه سطح مرتبط دارد:

1. بودجه مستقل هر `bot_identity`/token
2. بودجه مشترک مقصد، به‌خصوص همان کانال آفر
3. circuit-break سراسری gateway/config که هر دو credential را fail-closed متوقف می‌کند

همه send/editهای کانال در شروع از یک lane مقصد مشترک عبور می‌کنند. افزایش worker یا افزودن editor ظرفیت همان lane را خودکار افزایش نمی‌دهد و نباید بدون شاهد staging باعث ارسال موازی به کانال شود. token budget primary و editor جدا اندازه‌گیری می‌شود تا معلوم شود جداسازی credential خطا/backlog را کاهش می‌دهد یا نه.

### 6.3.1 routing بات اصلی و ویرایشگر

- `primary` تنها ناشر پیام آفر و مالک تمام مقصدهای private/admin است.
- `channel_editor` فقط jobهایی را می‌پذیرد که destination آن کانال allowlisted، method آن edit allowlisted و canonical message identity آن متعلق به publication معتبر primary باشد.
- routing پیش از claim قطعی است و پس از `LEASED` تغییر نمی‌کند.
- credential resolver فقط روی foreign و بر پایه identity allowlisted عمل می‌کند؛ token هرگز در payload یا job قرار نمی‌گیرد.
- editor execution plane جدا، feeder جدا یا API polling ندارد؛ همان worker صف اصلی method را با credential متناظر اجرا می‌کند.
- اگر editor غیرفعال باشد، producer از ابتدا jobهای edit را primary-route می‌کند. job ایجادشده برای editor در outage خودکار به primary بازنویسی نمی‌شود.

### 6.4 حالت‌های execution و projection دامنه

حالت‌های صف اصلی:

- `PENDING`: واجد claim یا منتظر زمان eligibility
- `LEASED`: claim شده با fencing token معتبر
- `PENDING_RETRY`: retry امن در `next_retry_at`
- `PENDING_RECONCILE`: edit idempotent نیازمند بازخوانی state
- `AMBIGUOUS`: send با نتیجه نامشخص و ممنوعیت retry کور
- `AMBIGUOUS_UNRESOLVED`: پنجره reconciliation تمام شده ولی شاهد قطعی وجود ندارد
- `SENT` و `SENT_NOOP`: نتیجه موفق واقعی یا no-op idempotent اثبات‌شده
- `SUPERSEDED` و `EXPIRED_INTERACTION`: payload یا تعامل دیگر معتبر نیست
- `PERMANENT_UNDELIVERABLE`, `TERMINAL_FAILED`, `QUARANTINED`: پایان غیرموفق با علت طبقه‌بندی‌شده
- `BLOCKED_DESTINATION`, `BLOCKED_BOT`, `BLOCKED_GATEWAY`: توقف کنترل‌شده lane یا executor تا preflight و resume

projection publication آفر علاوه بر `PENDING/SENT`، حالت `LAGGED` برای عبور از SLO و `DISABLED` برای آفر terminal پیش از publication را نگه می‌دارد. `FAILED` legacy فقط به `TERMINAL_FAILED` طبقه‌بندی‌شده نگاشت می‌شود. `429`، `5xx` و خطای شبکه به‌تنهایی هیچ‌کدام را terminal نمی‌کنند.

### 6.5 صف اصلی و صف‌های تابع دامنه‌ای

صف اصلی Telegram تنها execution plane است: claim نهایی، اولویت `M0` تا `M7`، بودجه bot/destination، اجرای Bot API، `retry_after`، خطاهای transport، lease ارسال و ثبت پاسخ Telegram فقط در این لایه انجام می‌شوند.

تعداد credential این اصل را تغییر نمی‌دهد: primary و editor دو lane credential زیر همان execution owner `queue-v1` هستند، نه دو consumer owner مستقل.

صف تابع یک scheduler دوم Telegram نیست. این صف فقط منبع حقیقت و coordinator دامنه است و مسئول انتخاب business-ready job، ترتیب یا همگرایی داخلی، freshness، dependency، fan-out و مشاهده نتیجه صف اصلی است. هیچ صف تابعی حق sleep برای نرخ Telegram، retry فنی مستقل یا تماس مستقیم با Bot API ندارد.

قرارداد handoff:

1. هر رکورد تابع با dedupe key پایدار، نوع مبدأ، شناسه مبدأ و نسخه state به دقیقاً یک job منطقی صف اصلی نگاشت می‌شود.
2. ثبت handoff و رابطه `child_record -> main_job` باید اتمیک یا با outbox قابل‌بازیابی باشد؛ crash میان این دو نباید job را گم یا تکراری کند.
3. نتیجه صف اصلی به صف تابع بازتاب داده می‌شود، اما صف تابع status موفق را قبل از نتیجه قطعی Telegram ثبت نمی‌کند.
4. `429`، `5xx` و transport retry در صف اصلی باقی می‌مانند و صف تابع job جایگزین تولید نمی‌کند.
5. `AMBIGUOUS` پیشروی وابستگی همان جریان را تا reconciliation متوقف می‌کند.
6. cancel/pause صف تابع job درحال‌ارسال را بدون قرارداد cancellation/fencing از صف اصلی حذف نمی‌کند.
7. fairness میان چند صف تابع و چند campaign در scheduler اصلی اعمال می‌شود؛ priority داخلی صف تابع حق عبور از priority اصلی را ندارد.

توپولوژی قطعی:

| صف تابع | نقش داخلی | نحوه ورود به صف اصلی | وضعیت تصمیم |
| --- | --- | --- | --- |
| ثبت و کنترل آفر | publication، success preview، validation response و فرمان انقضا | مطابق بخش 3.2، از `M0` تا `M6` | DECIDED |
| ویرایش آفرهای کانال | partial/terminal edit، dependency بر `message_id`، coalescing، newest-first و catch-up یک‌به‌بیست | مطابق بخش 3.3، از `M2` تا `M6` | DECIDED |
| پیام معامله و درخواست | fan-out مستقل دو طرف، deadline هر گیرنده، پیشنهاد جایگزین و private edit | مطابق بخش 3.4؛ اعلان نتیجه `M1` و پس از deadline مستقل به `M0` | DECIDED |
| پیام مدیریتی و سیستمی | fan-out، پیام حساب، pause/cancel campaign، سقف دو broadcast و گزارش نتیجه | مطابق بخش 3.5، از `M2` تا `M7` | DECIDED |
| اعلان وضعیت بازار | transition، freshness و suppression اعلان stale | مطابق بخش 3.6، `M4` یا `M6` | DECIDED |
| عملیات زمان‌دار بات | `due_at`، cancel، revalidation و coalescing حذف/edit | مطابق بخش 3.7، `M4`, `M5` یا `M7` | DECIDED |

reconciliation/ambiguous یک control plane است، نه صف تابع ارسال؛ فقط با شاهد قطعی job موجود را resolve یا دوباره eligible می‌کند. `answerCallbackQuery` و پاسخ‌های تعاملی deadlineدار نیز مستقیماً وارد صف اصلی می‌شوند تا hop اضافی نسازند.

## 7. قرارداد retry

### `429`

1. رکورد execution در `PENDING_RETRY` و projection publication در `PENDING` باقی می‌ماند.
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

Bot API تضمین exactly-once ندارد. `sendMessage` که احتمال پذیرش آن وجود دارد به `AMBIGUOUS` می‌رود و خودکار یا دستی دوباره ارسال نمی‌شود، مگر شاهد قطعی نبودن side effect ثبت شود. شاهد مثبت observer آن را `SENT` می‌کند؛ نبود شاهد مثبت به‌تنهایی اثبات عدم ارسال نیست و پس از پنجره reconciliation به `AMBIGUOUS_UNRESOLVED` و هشدار عملیاتی می‌رسد. edit idempotent پس از بازخوانی state قابل retry است و callback عبورکرده از deadline به `EXPIRED_INTERACTION` می‌رود. این محدودیت ذاتی Telegram در reconciliation و گزارش release صریح باقی می‌ماند.

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

- tokenهای `primary` و `channel_editor` و channel id در staging مستقل از production باشند؛ مقدار secret در manifest یا log ثبت نشود.
- پیش از benchmark، bot اصلی باید پیام را در کانال staging منتشر کند و editor همان `chat_id/message_id` را با دسترسی فقط `can_edit_messages` ویرایش کند؛ observer باید متن و markup نهایی را ببیند.
- هر trace در دو mode بازپخش شود: baseline برابر `primary-only` و candidate برابر `primary+channel_editor`؛ تفاوت mode نباید تعداد یا ترتیب business eventها را عوض کند.
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

send-only در هر دو mode یکسان و فقط با primary اجرا می‌شود. edit-only و بار ترکیبی با trace یکسان در modeهای primary-only و primary+editor اجرا می‌شوند. بار ترکیبی مبنای انتخاب عدد production و تصمیم فعال‌سازی editor است.

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

### 10.6 گیت مستقل بات ویرایشگر

- `getMe` و readback عضویت/دسترسی هر دو bot باید fingerprint مورد انتظار محیط را تأیید کنند؛ editor حق post/delete/promote یا تعامل خصوصی ندارد.
- routing هر job پیش از claim immutable است و ledger باید `bot_role` مورد انتظار و واقعی را تطبیق دهد.
- خاموشی، revoke، `401/403/429` و restart editor نباید باعث fallback خودکار، duplicate یا تصاحب job توسط primary شود؛ publication و پیام‌های خصوصی primary مستقل ادامه می‌یابند.
- limiter بودجه tokenها را جدا و cooldown مقصد کانال را تا وقتی شاهد خلاف آن وجود ندارد مشترک اعمال می‌کند.
- فعال‌سازی editor فقط وقتی مجاز است که cross-bot receipt کامل، صفر fallback/duplicate/stale و بهبود تکرارپذیر goodput یا backlog بدون نقض SLO اثبات شود. در غیر این صورت mode نهایی `primary-only` می‌ماند.

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
- تست اولویت اصلی `M0` تا `M7`، ترتیب انتقال میان feederها و تمام اولویت‌های داخلی بخش ۳
- تست `429/retry_after`
- تست idempotency و concurrency
- تست ادغام terminal edit
- تست restart و lease recovery
- تست عدم انقضای آفر روی خطای موقت
- تست قرارداد handoff صف تابع به صف اصلی: dedupe، crash میان دو صف، بازتاب نتیجه و نبود retry دوگانه
- تست پنجره یک in-flight برای broadcast مدیریتی و رفتار `sent/skipped/retryable/ambiguous/systemic`
- تست استقلال همراه dependency صف ثبت آفر و صف edit تا edit هرگز پیش از publish یا با نسخه superseded آزاد نشود
- تست پایداری منوی کاربر ثبت‌نام‌شده، حفظ پیام لنگر و بازگشت کیبورد اصلی پس از success/error/cancel/timeout جریان موقت

معیار خروج: تمام failure injectionها بدون پیام گم‌شده و duplicate کنترل‌نشده پاس شوند.

#### نتیجه اجرای Stage 2 در `2026-07-15`

- قرارداد pure و بدون side effect در `core/telegram_delivery_queue_contract.py` ایجاد شد تا مستقیماً مبنای adapter پایدار Stage 3 باشد.
- اولویت قدیمی `P0` تا `P3` و ترتیب داخلی `P1` در قرارداد اولیه تست شد، اما تصمیم قطعی جدید `M0` تا `M7`، شش feeder، newest-first همیشگی و ارتقای `M1 -> M0` اعلان معامله آن قرارداد را supersede کرده است؛ قرارداد pure و تست Stage 2 باید پیش از Stage 3 بازنویسی و دوباره اجرا شوند.
- `429` رکورد را pending نگه می‌دارد، `retry_after + safety_margin` را اعمال می‌کند و فقط lane همان مقصد را تا موعد retry می‌بندد.
- `5xx` و خطاهای transport قابل retry با backoff محدود مدل شدند؛ خطای payload معیوب `400` terminal و `403` موجب pause مقصد می‌شود.
- dedupe هم‌زمان، collision یک dedupe key با payload متفاوت، claim هم‌زمان، مالکیت lease، نتیجه دیررس worker قبلی و بازیابی پس از restart تست شدند.
- timeout مبهم `sendMessage` retry کور نمی‌شود و در وضعیت `AMBIGUOUS` می‌ماند؛ فقط شاهد صریح reconciliation می‌تواند آن را `SENT` یا «قطعاً غایب و قابل retry» کند. اگر شاهد قطعی فراهم نشود، قرارداد نهایی `AMBIGUOUS_UNRESOLVED`، هشدار عملیاتی و ممنوعیت resend کور است.
- قرارداد terminal edit دقیقاً یک `editMessageText` با متن نهایی و `reply_markup={"inline_keyboard": []}` تولید می‌کند.
- تصمیم‌های خطای موقت هیچ mutation برای Offer برنمی‌گردانند؛ اتصال این اصل به مسیر واقعی ثبت آفر در Stage 3 انجام می‌شود.
- `18` تست قرارداد جدید و در مجموع `125` تست هدفمند همراه regressionهای publication، expiry، channel edit و notification outbox فعلی پاس شدند.
- قرارداد صف‌های تابع پس از اجرای اولیه Stage 2 ثبت شد؛ بنابراین تست‌های handoff، شش feeder، priority transfer، keyboard/anchor و dependency مستقل publish/edit باید پیش از Stage 3 به قرارداد pure و مجموعه تست Stage 2 افزوده و دوباره پاس شوند.

#### نتیجه بازاجرای Stage 2 در `2026-07-16`

- قرارداد pure از چهار اولویت قدیمی به `M0` تا `M7` و ترتیب درون‌سطحی قطعی بازنویسی شد؛ ارتقای مستقل هر recipient معامله در مرز پنج ثانیه تست شد.
- ماتریس internal rank هر شش feeder، dedupe identity نسخه‌دار، handoff بازیاب‌پذیر پس از crash و feedback یکتای child/main تست شد.
- feeder edit، newest-first بر `offer.created_at`، حفظ `first_enqueued_at` در coalescing، reclassification partial-to-terminal، dependency بر message id و catch-up یک‌به‌بیست تست شد.
- feeder broadcast با یک in-flight هر campaign، سقف کلی دو campaign، round-robin، توقف مستقل retryable و circuit-break سراسری تست شد.
- `retry_after` خام و بدون cap، safety margin، probe مقصد دوم، cooldown سراسری پس از 429 دوم، lease با margin پانزده‌ثانیه‌ای، heartbeat و fencing تست شد.
- جدول پاسخ Telegram به‌صورت method/destination-aware شامل `ok=false` در HTTP 200، no-op edit، `400/401/403/404/409/418/429/5xx`، transport، method mismatch و send بدون message id تست شد.
- freshness برای callback، publication، partial/terminal edit، اعلان معامله، admin TTL و cleanup allowlist و نیز قرارداد `AMBIGUOUS_UNRESOLVED` بدون retry کور تست شد.
- پیام success Bot با یک `editMessageText` و دکمه انقضا، terminal edit ترکیبی، حفظ anchor و بازگرداندن منوی persistent در success/error/cancel/timeout به قرارداد pure افزوده شد.
- `44` تست قرارداد جدید و در مجموع `171` تست هدفمند شامل workerهای publication/notification/trade، reconciliation، channel edit و expiry پاس شدند.
- اولین اجرای regression بدون env استاندارد پیش از collection متوقف شد؛ اجرای نهایی با env مصنوعی `test` و بدون اتصال production پاس شد. تست PostgreSQL واقعی و staging جزو Stage 3/4 باقی می‌مانند.

مرز این مرحله:

- `core/offer_publication_worker.py` و `core/telegram_notification_outbox_worker.py` همچنان worker صف مشترک نیستند.
- هنوز جدول/مدل صف مشترک، migration، PostgreSQL claim با `SKIP LOCKED`، limiter توزیع‌شده، feature flag، startup task و جایگزینی مسیرهای مستقیم Telegram ساخته نشده است.
- بررسی schema نشان داد `offer_publication_states` فقط publication آفر و `telegram_notification_outbox` فقط پیام خصوصی را مدل می‌کنند؛ هیچ‌کدام به‌تنهایی method/payload عمومی، `M0` تا `M7`، feeder identity/internal rank و cooldown مشترک مقصد را با قرارداد کامل نگه نمی‌دارند. Stage 3 باید adapter پایدار و migration حداقلی را بر همین اساس اضافه کند.

### Stage 2.5 — ممیزی چالش‌ها و گیت پیش از کدنویسی worker

هدف این Stage جلوگیری از کشف دیرهنگام مسئله در توسعه، staging یا production است. challenge register در ممیزی closure مورخ `2026-07-16` از نظر تصمیم طراحی بسته شده است؛ اجرای تصمیم‌ها و جمع‌آوری شاهد همچنان در Stageهای مقصد انجام می‌شود.

خروجی‌های اجباری:

- ثبت چالش‌های فنی و غیرفنی با failure mode، کنترل پیشگیرانه و شاهد بسته‌شدن
- تفکیک blockerهای قبل از کدنویسی از مواردی که در Stage 3 یا Stage 4 قابل حل‌اند
- ثبت ADR برای ظرفیت/عمر آفر، منبع حقیقت صف، مرز Iran/foreign، timeout مبهم، limiter و rollout نسخه مختلط
- تعیین SLOهای تجربه کاربر، زمان publication، backlog، callback `M0` و incident response
- تعیین owner عملیاتی، مسیر escalation، مجوز go/no-go و نگهداری حساب‌ها و credentialهای staging
- افزودن تست یا preflight متناظر برای هر چالش قابل‌آزمایش
- ثبت topology و ownership matrix صف‌های تابع، سیاست handoff، سقف in-flight، fairness و رفتار pause/cancel/restart برای هر feeder

معیار خروج:

- هیچ تصمیم طراحی حل‌نشده‌ای باقی نماند.
- هر تصمیم دارای owner، stage اجرا، تست پذیرش و rollback باشد.
- تعارض میان «پذیرش ۳ آفر بر ثانیه»، ظرفیت واقعی یک کانال و عمر دو دقیقه‌ای آفر صریحاً تعیین تکلیف شود.
- طراحی migration و rollout ثابت کند در هیچ بازه‌ای direct sender و worker جدید یک job منطقی را دو بار ارسال نمی‌کنند.
- مالک محصول و مالک عملیات گزارش challenge closure را پیش از شروع Stage 3 تأیید کنند.

### Stage 3 — پیاده‌سازی پشت feature flag

- producer اتمیک publication intent
- scheduler و limiter مشترک
- credential registry خارجی و allowlisted برای `primary` و `channel_editor` بدون ذخیره token در job/log، به‌همراه preflight fingerprint و permission readback
- routing immutable بر پایه `bot_identity`؛ primary برای همه send/private/callbackها و editor فقط برای editهای allowlisted همان کانال با canonical message identity
- limiter با budget جدا برای هر bot role و lane مشترک مقصد کانال؛ fallback خودکار و بازنویسی bot job پس از enqueue ممنوع
- حذف ارسال مستقیم کانال از handler و API
- تکمیل retry و error classification
- ادغام terminal edit
- تکمیل reconciliation و metrics
- ساخت adapter/feeder صف‌های تابع روی صف اصلی و حذف Bot API call، limiter و retry مستقل از workerهای تابع
- تبدیل worker broadcast مدیریتی به coordinator گیرندگان با handoff پایدار، پنجره in-flight و feedback نتیجه صف اصلی
- ساخت feeder مستقل ثبت/کنترل آفر و feeder مستقل edit کانال همراه dependency بر publication، supersession، coalescing و ترتیب newest-first پیش از enqueue به صف اصلی
- تعیین تکلیف `trade_delivery_worker` و `telegram_notification_outbox_worker` مطابق ownership matrix: feeder/adaptor یا producer مستقیم صف اصلی، با فقط یک consumer owner برای هر job
- اتصال feeder اعلان وضعیت بازار و عملیات زمان‌دار به main و حذف sleep-taskهای حافظه‌ای برای cleanupهای تحت قرارداد
- اعمال سیاست authenticated keyboard/anchor بدون تغییر امنیت یا اعتبارسنجی server-side جریان ثبت‌نام و اتصال حساب

معیار خروج: تست‌های هدفمند، integration و regression پاس، feature flag و editor پیش‌فرض خاموش، و inventory runtime ثابت کند هیچ worker تابعی Telegram را مستقیم صدا نمی‌زند، هر side effect فقط یک consumer owner دارد، editor نمی‌تواند send/private/callback یا مقصد غیرمجاز اجرا کند و هیچ credentialی در DB/log/artifact نیست.

#### وضعیت برش foundation Stage 3 در `2026-07-16`

- مدل و migration افزایشی `telegram_delivery_jobs`، dedupe/upsert هم‌زمان، claim با `SKIP LOCKED`، lease/fencing، dispatch marker، recovery و ثبت خام `retry_after` در branch کاندید پیاده شده‌اند.
- execution state به‌صورت `NO_SYNC` و foreign-local ثبت شده و authority/startup guard مانع اجرای Iran یا هم‌زمانی legacy/queue می‌شود.
- queue runtime هنوز عمداً code-ready نیست: capability داخلی false است، gateway/limiter/feeder cutover کامل نشده و هیچ env یا feature flag به‌تنهایی نمی‌تواند side effect واقعی را فعال کند.
- `bot_identity` فقط roleهای `primary/channel_editor` را می‌پذیرد؛ route editor هم در service و هم constraint دیتابیس به editهای allowlisted کانال محدود و تغییر bot یک job موجود ممنوع شده است.
- envelope ناقص HTTP 200 موفقیت محسوب نمی‌شود، dispatch marker هر fence تک‌مصرف است، raw destination مهاجرت‌یافته redacted می‌شود و direct cycle خارج ownership رسمی پیش از DB متوقف می‌شود.
- credential resolver، canonical publisher identity در publication state، permission/fingerprint preflight، limiter چند credential، revalidation payload و feeder cutover باید در ادامه Stage 3 تکمیل شوند.
- `223` تست unit/regression و `20` تست PostgreSQL واقعی شامل enqueue هم‌زمان، `SKIP LOCKED`، route constraint، immutable routing، fencing، crash recovery، 429 خام و transaction boundary پاس شدند؛ migration نیز روی scratch واقعی full-upgrade، downgrade به `f1b6e7f8a9dc` و re-upgrade تا `f2c7d8e9a0bd` را پاس کرد.
- این foundation مجوز deploy یا شروع Stage 4 نیست و runtime عمداً غیرفعال باقی مانده است.

### Stage 4 — deploy و آزمایش تکرارشونده staging

- deploy فقط با مسیر استاندارد staging
- ثبت `max_active_offers=10` فقط از مرجع مدیریتی Iran staging و تأیید sync/cache در foreign staging
- تأیید recreate شدن `bot`, `foreign_app`, `sync_worker`, `foreign_sync_worker`
- اجرای preflight و smoke واقعی primary-send/editor-edit پیش از load؛ شکست آن کل mode editor را fail-closed می‌کند
- اجرای A/B با trace یکسان برای `primary-only` و `primary+channel_editor` و ثبت metric به تفکیک bot role و lane مشترک کانال
- fault/restart/revoke editor بدون تغییر credential زنده و اثبات نبود fallback، duplicate و توقف publication/private primary
- اجرای ماتریس intervalها
- اجرای endurance و burst
- ثبت raw evidence و گزارش مقایسه‌ای

معیار خروج: یک interval پایه و safety margin با شواهد تکرارپذیر انتخاب و تصمیم روشن `editor_enabled=true|false` با شاهد cross-bot، امنیت دسترسی و بهبود قابل‌اندازه‌گیری ثبت شود.

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

- `DECIDED`: قرارداد طراحی بسته شده است، اما این برچسب به معنی پیاده‌سازی یا اثبات در staging نیست.
- `IMPLEMENTED`: کد و تست محلی متناظر تکمیل شده است.
- `VERIFIED`: رفتار در staging با artifact و شاهد قابل بازتولید تأیید شده است.

پس از ممیزی `2026-07-16` همه ردیف‌های challenge register در سطح تصمیم `DECIDED` هستند. کار باقی‌مانده با ستون «شاهد بسته‌شدن»، Stage مقصد و گیت‌های بخش ۱۴ پیگیری می‌شود؛ نبود پیاده‌سازی یا شاهد نباید دوباره به‌عنوان ابهام طراحی گزارش شود.

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

صف از گم‌شدن رکورد جلوگیری می‌کند، اما کمبود ظرفیت کانال را برطرف نمی‌کند. قرارداد نهایی بدون تغییر رفتار کسب‌وکار چنین است:

1. expiry همچنان از زمان ثبت موفق محاسبه می‌شود و pending بودن publication آن را تمدید نمی‌کند.
2. پذیرش `3 valid offer/s` حفظ می‌شود و backpressure پنهان یا ردکردن آفر معتبر برای جبران ظرفیت مجاز نیست.
3. publication باید حداکثر تا `expires_at - 5s` پاسخ موفق Telegram گرفته باشد؛ پس از آن job بدون ارسال stale به `SUPERSEDED/DISABLED` می‌رود.
4. گیت production نیازمند `100%` publication آفرهای واجد انتشار پیش از deadline و صفر `DISABLED` صرفاً ناشی از backlog در دور ترکیبی مرجع است. هدف تأخیر `p95 <= 10s`، `p99 <= 30s` و سقف سخت همان deadline است.
5. اگر staging این گیت را با هیچ‌یک از دو mode امن `primary-only` یا `primary+channel_editor` در همان یک کانال پاس نکند، نتیجه `NO-GO` است. expiry، SLO، کانال، batching یا ناشر دوم بدون تصمیم تازه مالک محصول تغییر نمی‌کند و سیستم با semantics ضعیف‌تر deploy نمی‌شود. editor ناشر نیست و فقط editهای همان پیام‌های primary را اجرا می‌کند.

### 13.3 تصمیم‌های قبلی و چالش‌های پایه

| شناسه | نوع | اولویت | وضعیت | چالش | خروجی لازم |
| --- | --- | --- | --- | --- | --- |
| `TOPQ-C01` | محصول | P0 | DECIDED | یک کانال و یک پیام مستقل برای هر آفر؛ primary ناشر یکتا و editor فقط ویرایشگر اختیاری | حفظ معماری بدون batching، کانال یا publisher اضافه؛ routing مطابق ADR-07 |
| `TOPQ-C02` | فنی | P0 | DECIDED | پذیرش پایدار آفر مستقل از Telegram side effect | تراکنش Offer + intent پایدار |
| `TOPQ-C03` | UX | P0 | DECIDED | تجربه عادی کاربر در خطای موقت Telegram و مصرف دو عملیات محتوایی private | ادغام success و «لفظ شما» در یک edit با دکمه انقضا، حفظ رفتار WebApp و عدم نمایش queue/error |
| `TOPQ-C04` | محصول/فنی | P0 | DECIDED | اولویت پیام‌ها | `M0` تا `M7`، ترتیب انتقال feederها و اولویت داخلی شش صف مطابق بخش ۳ |
| `TOPQ-C05` | فنی | P0 | DECIDED | رفتار `429` | pending، cooldown طبق retry_after و retry بدون fail |
| `TOPQ-C06` | فنی | P0 | DECIDED | کاهش دو edit terminal به یک API call | متن نهایی و حذف دکمه در یک editMessageText |
| `TOPQ-C07` | فنی | P0 | DECIDED | timeout با نتیجه نامشخص و نبود exactly-once در Bot API | قرارداد `AMBIGUOUS/AMBIGUOUS_UNRESOLVED`، منع retry کور و reconciliation مبتنی بر شاهد |
| `TOPQ-C08` | تست/عملیات | P0 | DECIDED | سناریوی کامل staging | سند سناریو، load generator، stop condition و گزارش واقعی بخش‌های ۱۰ و 13.12 |
| `TOPQ-C09` | فنی/عملیات | P0 | DECIDED | انتخاب interval نهایی | انتخاب کوتاه‌ترین interval پایدار بر پایه goodput دور ترکیبی؛ مقدار عددی فقط پس از Stage 4 وارد config می‌شود |
| `TOPQ-C10` | معماری | P0 | DECIDED | scheduler مشترک در برابر مسیرهای مستقیم و workerها | main queue تنها execution plane؛ inventory و CI guard باید bypass را به صفر برسانند |
| `TOPQ-C11` | محصول/فنی | P1 | DECIDED | آفر terminal پیش از publication | بازخوانی وضعیت و تبدیل به DISABLED بدون ارسال stale |
| `TOPQ-C12` | فنی/عملیات | P1 | DECIDED | سیاست newest-first و stale-tail ممکن است starvation بسازد | یک edit مؤثر، سن بدون reset، stale metrics و سهم catch-up یک‌به‌بیست بدون تغییر priority اصلی |
| `TOPQ-C13` | عملیات | P1 | DECIDED | reconciliation دقیق active/history | گزارش بخش ۱۱ با شناسه، علت، retry و تفکیک خطای فعال، تاریخی و ambiguous |
| `TOPQ-C14` | تست | P1 | DECIDED | پوشش تمام کالاها و حالات تجارت | acceptance matrix و fixture read-only production مطابق سند Stage 1 |
| `TOPQ-C15` | معماری | P1 | DECIDED | مرز اجرای Telegram | foreign-only execution و عدم اجرای مستقیم روی Iran |
| `TOPQ-C16` | انتشار | P0 | DECIDED | rollout و rollback بدون گم‌شدن pending یا double-send | migration افزایشی و flag خاموش، shadow بدون ارسال، انتقال اتمیک owner و rollback پس از reconcile |
| `TOPQ-C17` | تست | P0 | DECIDED | دریافت واقعی همه پاسخ‌های طبیعی Telegram | observer سمت گیرنده و تطبیق ledger |
| `TOPQ-C18` | محصول/تست | P0 | DECIDED | نسبت بار معتبر و نامعتبر | `1800` آفر معتبر + `400` تلاش نامعتبر |
| `TOPQ-C19` | حریم خصوصی | P1 | DECIDED | استفاده امن از الگوی آفر production | sampler فقط‌خواندنی، حذف هویت و بازسازی staging |
| `TOPQ-C20` | عملیات تست | P0 | DECIDED | pool کاربری واقعی بدون تغییر production | `max_active_offers=10` فقط staging، ۸۰ حساب و guard عدم نشت |

### 13.4 چالش‌های فنی تفصیلی

| شناسه | شدت | وضعیت | چالش و failure mode | کنترل یا تصمیم لازم | شاهد بسته‌شدن |
| --- | --- | --- | --- | --- | --- |
| `TOPQ-C21` | بحرانی | DECIDED | ظرفیت کانال کمتر از ورودی و expiry دو دقیقه‌ای | حفظ expiry و نرخ پذیرش، deadline پنج‌ثانیه پیش از expiry و `NO-GO` در صورت هر backlog-caused miss | مدل ظرفیت + دور ترکیبی با publication ratio صددرصد پیش از deadline |
| `TOPQ-C22` | بحرانی | DECIDED | منبع حقیقت میان stateهای فعلی و صف عمومی | `telegram_delivery_jobs` منبع حقیقت execution؛ جدول‌های دامنه feeder/state هستند و هر job یک dedupe identity دارد | ERD، migration افزایشی و تست unique/concurrent enqueue |
| `TOPQ-C23` | بحرانی | DECIDED | Offer روی home server ثبت و Telegram فقط foreign اجرا می‌شود | Offer و intent در یک تراکنش مرجع؛ sync durable intent و ساخت اتمیک main job فقط روی foreign | تست قطع peer پس از commit و تحویل دقیق پس از recovery |
| `TOPQ-C24` | بحرانی | DECIDED | crash پس از پذیرش Telegram و پیش از ثبت `message_id` | `AMBIGUOUS`، fencing و reconciliation؛ بدون شاهد قطعی resend انجام نمی‌شود | fault test در مرز send/commit بدون retry کور |
| `TOPQ-C25` | بحرانی | DECIDED | scope پاسخ `429` می‌تواند destination یا bot-wide باشد | limiter دو‌سطحی؛ cooldown مقصد، یک probe کنترل‌شده به مقصد دیگر و cooldown سراسری پس از 429 مقصد دوم | تست 429 هم‌زمان چند مقصد و اثبات نبود retry storm |
| `TOPQ-C26` | بحرانی | DECIDED | `answerCallbackQuery` زمان‌حساس است | ورود مستقیم به `M0`، `p95<=1s`، `p99<=2s` و تبدیل retry دیرهنگام به `EXPIRED_INTERACTION` | latency histogram و تست query-too-old |
| `TOPQ-C27` | بحرانی | DECIDED | call site مستقیم می‌تواند scheduler را دور بزند | gateway اجباری، allowlist محدود و static/runtime guard با هدف صفر bypass | inventory machine-readable و CI guard |
| `TOPQ-C28` | بحرانی | DECIDED | نسخه مختلط می‌تواند یک event را مستقیم و صفی بفرستد | ownership flag یکتا، shadow غیرقابل‌ارسال و cutover اتمیک بدون canary درصدی | تست mixed-version و شمار دقیق یک side effect |
| `TOPQ-C29` | بالا | DECIDED | ID یا ساعت محلی بین Iran/foreign ترتیب پایدار نمی‌دهد | internal rank دامنه، deadline و سپس `enqueued_seq` تولیدشده روی foreign؛ tie-breaker طبیعی مبدأ فقط برای replay | property test reorder/sync delay/clock skew |
| `TOPQ-C30` | بالا | DECIDED | strict priority می‌تواند کلاس پایین را در overload گرسنه کند | `M0` هرگز تنزل نمی‌کند؛ feeder edit سهم catch-up یک‌به‌بیست دارد و `M6/M7` فقط ظرفیت باقی‌مانده می‌گیرند؛ عبور max-age یک SLO breach است نه priority inversion | endurance با backlog، max-age و drain کامل |
| `TOPQ-C31` | بالا | DECIDED | publish و editهای یک Offer ممکن است reorder شوند | dependency بر canonical message identity، coalescing و supersession بر پایه offer version | تست publish→partial→terminal با تمام reorderها |
| `TOPQ-C32` | بالا | DECIDED | snapshot stale و rebuild کامل وابسته به state است | identity و قالب نسخه‌دار immutable ذخیره، payload درست پیش از dispatch از آخرین state معتبر بازسازی و hash می‌شود | تست تغییر قیمت/لات/status قبل از claim |
| `TOPQ-C33` | بالا | DECIDED | message ID در دو منبع ممکن است ناسازگار باشد | publication state نگاشت canonical مقصد/message است؛ Offer فقط mirror سازگاری و هر edit مقصد را validate می‌کند | reconciliation دو منبع و fault message-not-found |
| `TOPQ-C34` | بالا | DECIDED | طبقه‌بندی خطا باید method و destination-aware باشد؛ `403` کانال با blocked private user یکسان نیست | قرارداد جدول بخش 13.9 برای envelope، کدها و transport | table-driven tests برای تمام response classها |
| `TOPQ-C35` | بالا | DECIDED | lease کوتاه‌تر از HTTP timeout ممکن است دو worker فعال بسازد | lease حداقل `request_timeout + 15s`، fencing token، heartbeat و HTTP خارج تراکنش DB | restart/slow-network test با دو worker و یک send |
| `TOPQ-C36` | بالا | DECIDED | claim بدون index مناسب contention می‌سازد | claim کوتاه با `SKIP LOCKED` و partial index روی priority/eligibility/sequence؛ HTTP خارج transaction | `EXPLAIN ANALYZE` و PostgreSQL concurrency test |
| `TOPQ-C37` | بالا | DECIDED | outageهای DB، Redis و Telegram رفتار متفاوت دارند | DB: request fail پیش از success؛ Redis: send fail-closed و صف DB محفوظ؛ Telegram: circuit-break/backlog و drain کنترل‌شده | chaos test هر outage و recovery |
| `TOPQ-C38` | بالا | DECIDED | sync ممکن است lease/status محلی foreign را overwrite کند | intent و natural identity قابل sync؛ lease، claim، attempt و provider result فقط local foreign | sync parity و bidirectional replay tests |
| `TOPQ-C39` | بالا | DECIDED | workerهای فعلی retry متفاوت دارند | همه به feeder/adapter تبدیل می‌شوند؛ main تنها مالک retry و هر job فقط یک consumer owner دارد | ownership matrix و تست نبود claim/retry دوگانه |
| `TOPQ-C40` | بالا | DECIDED | worker جدید باید در authority و lifecycle درست ثبت شود | feature flag پیش‌فرض خاموش، execution فقط foreign و shutdown با توقف claim و پایان محدود in-flight | startup surface tests و task recreation evidence |
| `TOPQ-C41` | بحرانی | DECIDED | gateway ممکن است `HTTP 200 + ok=false` را success ببیند، connection pooling ندارد و Test endpoint hardcode است | parse envelope `ok/result/error`, client مشترک lifecycle-safe و mode محدود `main/test` با URL allowlisted | contract tests envelope/body/TLS و smoke Test Bot API |
| `TOPQ-C42` | بالا | DECIDED | truncation `retry_after` قرارداد را می‌شکند | مقدار خام بدون cap ذخیره؛ safety margin config مستقل با مقدار اولیه `100ms` و تنظیم فقط با Stage 4 | تست retry_after بزرگ/مفقود و زمان‌بندی دقیق |
| `TOPQ-C43` | متوسط | DECIDED | backlog/audit موجب bloat می‌شود | terminalها `30d` hot، payload/error body خام حداکثر `7d`، aggregate غیرهویتی `180d` و unresolved تا سی روز پس از resolution | load test horizon، vacuum و size/query SLO |
| `TOPQ-C44` | بالا | DECIDED | metrics پرکاردینال یا PII داده را افشا می‌کند | label فقط method/priority/destination-class/status/error-class؛ identity در log hash و artifact امن | dashboard/alert test و privacy review |
| `TOPQ-C45` | بالا | DECIDED | worker foreign نباید Offer مرجع Iran را mutate کند | worker فقط delivery state محلی را تغییر می‌دهد و state/market authority را پیش از dispatch بازخوانی می‌کند | test home_server/peer outage/market-close reorder |
| `TOPQ-C46` | بالا | DECIDED | terminal text و حذف button اگر دوباره دو call شوند state نیمه‌کاره می‌سازند | یک `editMessageText` با empty inline keyboard و idempotent no-op | یک gateway call در test و receiver observation |
| `TOPQ-C47` | بالا | DECIDED | ۴۰۰ ورودی نامعتبر یا replay conflict ممکن است اشتباهاً intent بسازند | producer فقط پس از validation/admission و در transaction موفق | شمار صفر Offer/job برای تمام invalid manifest |
| `TOPQ-C48` | بحرانی | DECIDED | token/channel/base URL اشتباه می‌تواند load staging را به production بفرستد یا داده تست پس از اجرا باقی بماند | environment fingerprint، allowlist مقصد، preflight fail-closed و cleanup محدود به `run_id` پس از export شواهد | negative test با production token/channel، refusal log و گزارش cleanup هر دو peer |
| `TOPQ-C49` | بالا | DECIDED | observer ممکن است update را از دست بدهد | checkpoint پایدار، dedupe بر chat/message/edit-version و سه‌جانبه sender/API/receiver؛ gap غیرقابل‌بازیابی run را invalid می‌کند | restart observer test بدون gap/duplicate |
| `TOPQ-C50` | بالا | DECIDED | تولید زنده 401/403 یا خراب‌کردن token/channel می‌تواند محیط staging را مختل کند | fault adapter برای خطاهای مخرب و جداسازی natural-response run | test matrix با عدم تغییر credential زنده |
| `TOPQ-C51` | بحرانی | DECIDED | migration graph یا head دو دیتابیس ممکن است متفاوت باشد و rollback schema pendingها را orphan کند | preflight graph/head، migration additive و rollback forward-compatible | upgrade/downgrade scratch + head equality هر دو staging DB |
| `TOPQ-C52` | بالا | DECIDED | generator ممکن است bottleneck شود یا burst را صاف کند | trace از پیش hash‌شده، barrier concurrency، شمار دقیق مشتقات و self-metrics؛ انحراف trace نتیجه run را invalid می‌کند | تطبیق دقیق expected/actual و صفر event گم‌شده generator |
| `TOPQ-C53` | بحرانی | DECIDED | مقدار staging `max_active_offers=10` یا cache آن ممکن است به production نشت کند | authority Iran staging، environment guard و readback دو peer | preflight ثابت کند staging=10 و default/production=4 |
| `TOPQ-C54` | بالا | DECIDED | payload، error body، bot token، MTProto session و fixture ممکن است PII/secret افشا کنند | کمینه‌سازی payload، redaction، encryption/permission و secret rotation | secret scan، log review و دسترسی حداقلی |
| `TOPQ-C55` | متوسط | DECIDED | job permanent/ambiguous به ابزار عملیاتی نیاز دارد | command audited با inspect/dry-run/pause/resume/cancel؛ retry ambiguous بدون شاهد و SQL مستقیم ممنوع | runbook exercise روی job مصنوعی |
| `TOPQ-C56` | بحرانی | DECIDED | retry ممکن است payload منقضی را دیرهنگام ارسال کند | revalidation اجباری و قرارداد freshness قطعی بخش 13.8 | fault test با retry_after بزرگ و شمار صفر پیام stale |
| `TOPQ-C57` | بالا | DECIDED | پیام نتیجه معامله یکی از دو طرف ممکن است در `M1` بیش از پنج ثانیه معطل بماند | deadline از commit معامله، ارتقای مستقل هر recipient به `M0` و حفظ `retry_after`/cooldown | تست یک طرف sent و طرف دیگر pending، مرز دقیق پنج ثانیه و صفر bypass محدودیت Telegram |
| `TOPQ-C58` | بحرانی | DECIDED | newest-first و stale-tail می‌تواند edit قدیمی را گرسنه کند | یک edit مؤثر و سهم catch-up قطعی یک stale پس از هر بیست edit تازه، بدون عبور از priority اصلی | coalescing، reclassification و تخلیه نهایی staleها |
| `TOPQ-C59` | بحرانی | DECIDED | اتصال broadcast فعلی می‌تواند duplicate، توقف یا بی‌عدالتی بسازد | feeder بدون Bot API، یک in-flight هر campaign، سقف کلی دو broadcast، round-robin و circuit-break سراسری | fault/concurrency test چند campaign و شمار دقیق یک ارسال |
| `TOPQ-C60` | بحرانی | DECIDED | شش feeder ممکن است retry/lease/status را دوبار اعمال کنند | handoff اتمیک با unique child identity؛ feeder فقط eligibility/rank، main فقط priority نهایی/retry/lease/Bot API | contract test handoff/feedback/cancel/restart هر شش feeder |
| `TOPQ-C61` | بالا | DECIDED | حذف خودکار پیام، حذف لنگر یا `ReplyKeyboardRemove` در مسیر authenticated می‌تواند منوی کاربر را ناپدید و او را مجبور به `/start` کند | حفظ پیام‌ها تا حد امکان، ممنوعیت حذف لنگر فعال، جایگزینی context keyboard با منوی persistent در تمام خروجی‌های جریان و عدم تعمیم این قاعده به inline keyboardهای منقضی | تست success/error/cancel/timeout و restart برای ثبت‌نام/اتصال حساب/منو؛ کاربر authenticated بدون `/start` منوی مناسب را بازیابد |
| `TOPQ-C62` | بحرانی | DECIDED | دو bot credential می‌توانند دو execution owner یا retry مستقل بسازند | یک main queue و یک ownership state machine؛ `bot_identity` فقط routing executor است و editor worker/poller/feeder مستقل ندارد | inventory و concurrency test ثابت کند هر job دقیقاً یک claim/lease/fencing owner دارد |
| `TOPQ-C63` | بحرانی | DECIDED | تغییر bot پس از enqueue یا fallback می‌تواند duplicate و نتیجه مبهم بسازد | routing immutable پیش از claim؛ fallback و بازنویسی خودکار editor↔primary ممنوع، rollback پس از reconcile | outage/restart/fencing test با صفر fallback و duplicate |
| `TOPQ-C64` | بالا | DECIDED | داشتن دو token لزوماً ظرفیت همان کانال را افزایش نمی‌دهد و scope `429` قطعی نیست | budget جدا برای هر bot role و lane محافظه‌کارانه مشترک مقصد تا شاهد Stage 4؛ افزایش rate فقط با A/B تکرارپذیر | metric bot-role/destination و benchmark primary-only در برابر split |
| `TOPQ-C65` | بحرانی | DECIDED | editor ممکن است method یا مقصد خارج از وظیفه خود را اجرا کند | allowlist fail-closed: فقط editهای مصوب کانال، canonical message identity و منع send/private/callback/delete | table-driven authorization tests و negative destination/method tests |
| `TOPQ-C66` | بالا | DECIDED | editor updateهای primary را دریافت نمی‌کند و discovery/polling می‌تواند stale یا ناسازگار شود | انتقال canonical `(publisher_bot_identity, destination_identity, message_id)` از publication state؛ بدون Telegram/API polling | integration primary-send→state→editor-edit و quarantine روی mismatch |
| `TOPQ-C67` | بحرانی | DECIDED | token دوم، دسترسی بیش‌ازحد یا fingerprint اشتباه سطح حمله و خطر ارسال production را زیاد می‌کند | secret registry خارج DB، permission حداقلی `can_edit_messages`، fingerprint/readback و destination allowlist پیش از claim | secret scan، permission preflight و negative production fingerprint |

### 13.5 چالش‌های غیرفنی و عملیاتی

| شناسه | شدت | وضعیت | چالش و اثر | تصمیم یا کنترل لازم | شاهد بسته‌شدن |
| --- | --- | --- | --- | --- | --- |
| `TOPQ-N01` | بحرانی | DECIDED | SLO دیده‌شدن در کانال و سقف lag | `p95<=10s`، `p99<=30s`، سقف `expires_at-5s` و publication ratio صددرصد در دور مرجع | alert threshold و گزارش Stage 4 |
| `TOPQ-N02` | بحرانی | DECIDED | ظرفیت یک کانال در برابر expiry دو دقیقه‌ای | expiry و پذیرش بدون تغییر؛ هر backlog-caused miss برابر `NO-GO` و نه تغییر پنهانی semantics | acceptance بخش 13.2 و ADR-01 |
| `TOPQ-N03` | بالا | DECIDED | ادغام دو پیام Bot نباید اطلاعات، دکمه انقضا یا تفاوت copy مسیر legacy/text را از بین ببرد | یک پیام ترکیبی با success همان مسیر، متن «لفظ شما» و دکمه انقضا؛ WebApp بدون تغییر | نمونه متن در `2026-07-16` تأیید شد؛ snapshot هر دو مسیر Bot و اثبات صفر `sendMessage` خصوصی دوم |
| `TOPQ-N04` | بالا | DECIDED | کاربر خطای داخلی نمی‌بیند ولی support باید publication را پیگیری کند | جست‌وجوی admin-only با public offer id، state/age/error class؛ پاسخ کاربر همان رفتار عادی و escalation مورد بحرانی حداکثر ۱۵ دقیقه | سناریوی support بدون افشای queue/Telegram internals |
| `TOPQ-N05` | بحرانی | DECIDED | alert owner و pause/resume عملیاتی | RACI نقش‌محور بخش 13.12؛ stop/circuit-break خودکار و pause/resume فقط break-glass با حفظ صف | تمرین alert، ack ده‌دقیقه‌ای و pause/resume بدون job گم‌شده |
| `TOPQ-N06` | بالا | DECIDED | نگهداری ۸۰ حساب، channel و observer | مالک credential نقش Operations، نگهداری در vault، inventory بدون secret، health preflight و rotation/rebuild | health report دوره‌ای و recovery drill |
| `TOPQ-N07` | بالا | DECIDED | نمونه‌برداری production حتی read-only ریسک حریم خصوصی و دسترسی بیش از نیاز دارد | query allowlist، anonymization و retention کوتاه | privacy review و artifact غیرهویتی |
| `TOPQ-N08` | متوسط | DECIDED | load ده‌دقیقه‌ای و endurance می‌تواند staging مشترک را برای تیم‌های دیگر غیرقابل‌استفاده کند | پنجره تست، رزرو محیط و اعلان شروع/پایان | تقویم اجرا و sign-off مسئول staging |
| `TOPQ-N09` | متوسط | DECIDED | raw evidence حجیم است و بدون retention/versioning قابل بازبینی نیست | artifactهای نسخه‌دار و AI-readable طبق بخش 13.7، retention و checksum | manifest و گزارش reconciliation قابل بازتولید و تحلیل ماشینی |
| `TOPQ-N10` | بالا | DECIDED | اختیار go/no-go، stop test، rollback و production canary مبهم است | gate owner و stop conditions غیرقابل‌چشم‌پوشی | checklist امضاشده قبل از هر deploy |
| `TOPQ-N11` | متوسط | DECIDED | scope creep به کانال دوم، batching، paid broadcast، publisher یا poller دوم | حفظ یک کانال و primary به‌عنوان publisher یکتا؛ editor فقط executor ویرایش طبق ADR-07 | review scope و تطبیق fingerprint هر دو bot/کانال در هر run |
| `TOPQ-N12` | بالا | DECIDED | رفتار و محدودیت Telegram تغییرپذیر است؛ interval امروز قرارداد دائمی نیست | بازبینی دوره‌ای docs و recalibration پس از رشد `429` یا تغییر Bot API | schedule و trigger ثبت‌شده |
| `TOPQ-N13` | بالا | DECIDED | automation حساب‌ها می‌تواند flood یا compromise بسازد | pool هشتادحسابی فقط Test DC برای E2E؛ کالیبراسیون main-DC با مقصدهای allowlist و workload replay صف، بدون کاربران production | account-safety runbook و توقف روی flood/session alert |
| `TOPQ-N14` | متوسط | DECIDED | retry دستی ambiguous/permanent می‌تواند duplicate بسازد | فقط ابزار audited؛ retry پس از اصلاح علت و version جدید، ambiguous فقط با شاهد قطعی، SQL مستقیم ممنوع | tabletop exercise و audit trail |
| `TOPQ-N15` | بالا | DECIDED | Test DC عمومی‌تر است، داده‌ها دوره‌ای پاک می‌شوند و flood limit می‌تواند سخت‌گیرانه‌تر باشد | عدم ذخیره داده مهم، bootstrap تکرارپذیر و جداسازی هدف correctness از rate calibration | rebuild کامل pool و اجرای smoke از صفر |
| `TOPQ-N16` | متوسط | DECIDED | cleanup هزاران پیام خود rate مصرف می‌کند و دور بعدی را آلوده می‌کند | cleanup همه داده‌ها و پیام‌های ساخته‌شده همان `run_id` خارج measurement و پس از export شواهد؛ حفظ فقط گزارش غیرهویتی | شمار صفر داده runtime متعلق به run و cooldown evidence قبل از run بعدی |
| `TOPQ-N17` | بالا | DECIDED | production canary بار مصنوعی ندارد | ده دور کامل + endurance staging، shadow planner حداکثر ۲۴ساعت بدون send، cutover اتمیک و کنترل‌های ۳۰دقیقه/۲ساعت/۲۴ساعت | matrix قطعی بخش 13.11 و stop threshold صفر critical |
| `TOPQ-N18` | متوسط | DECIDED | alert زیاد یا backlog اولویت‌های `M6/M7` ممکن است alert fatigue بسازد | severity، aggregation و dedupe alert | تمرین incident با alert قابل‌اقدام |
| `TOPQ-N19` | بالا | DECIDED | credential و عملیات بات دوم هزینه rotation، revoke، incident و خطای پیکربندی را افزایش می‌دهد | owner واحد Operations، vault/rotation مستقل، runbook revoke و mode امن primary-only؛ فعال‌سازی فقط با سود عملیاتی اثبات‌شده | rotation/revoke drill، RACI و تصمیم Stage 4 درباره فعال یا غیرفعال ماندن editor |

### 13.6 ADRهای اجباری پیش از Stage 3

| ADR | موضوع | چالش‌های پوشش‌داده‌شده |
| --- | --- | --- |
| [TOPQ-ADR-01](adr/TOPQ-ADR-01-capacity-slo-expiry.md) | ظرفیت کانال، SLO publication و semantics expiry در backlog | `C21`, `N01`, `N02` |
| [TOPQ-ADR-02](adr/TOPQ-ADR-02-queue-source-sync.md) | schema، منبع حقیقت، dedupe و مرز atomic producer/sync | `C22`, `C23`, `C29`, `C38` |
| [TOPQ-ADR-03](adr/TOPQ-ADR-03-ambiguity-lease-reconciliation.md) | timeout/crash ambiguity، lease و reconciliation | `C07`, `C24`, `C35`, `C55` |
| [TOPQ-ADR-04](adr/TOPQ-ADR-04-scheduler-freshness-outage.md) | scheduler دو‌سطحی، priority، `M0` deadline، freshness، edit ordering/catch-up و outage mode | `C10`, `C12`, `C25`, `C26`, `C30`, `C37`, `C56`, `C57`, `C58` |
| [TOPQ-ADR-05](adr/TOPQ-ADR-05-worker-ownership-rollout.md) | inventory gateway، topology صف‌های تابع، ownership workerهای موجود و rollout نسخه مختلط | `C16`, `C27`, `C28`, `C39`, `C40`, `C41`, `C59`, `C60` |
| [TOPQ-ADR-06](adr/TOPQ-ADR-06-operations-ux-go-live.md) | RACI، copy نهایی، keyboard/anchor UX، incident response و go/no-go | `C61`, `N03`, `N05`, `N10`, `N14` |
| [TOPQ-ADR-07](adr/TOPQ-ADR-07-editor-bot-routing.md) | نقش editor، routing چند credential، permission، limiter و گیت فعال‌سازی | `C62` تا `C67`, `N19` |

هر ADR باید حداقل شامل گزینه‌های ردشده، دلیل انتخاب، اثر روی داده و sync، failure mode، feature flag، migration، تست، observability و rollback باشد.

### 13.7 تصمیم‌های دور نخست حل چالش‌ها — `2026-07-16`

- WebApp باید snapshot دقیق `main` بماند. در Bot، محتوای success و preview فعلی در یک پیام ادغام می‌شود: یک `editMessageText` همراه inline keyboard انقضا جای `editMessageText + sendMessage` را می‌گیرد؛ `answerCallbackQuery` همچنان جدا اجرا می‌شود. متن نهایی ترکیبی پایین همین بخش در `2026-07-16` تأیید شده است.
- حد staging از `50` به `10` اصلاح شد و production/default روی `4` می‌ماند.
- علامت terminal و حذف دکمه یک `editMessageText`، کنترل foreign-only، نبود intent برای ورودی نامعتبر، sampler غیرهویتی production، fault adapter، migration preflight و redaction تأیید شدند.
- runner نباید برای اجرای عادی به pause/resume نیاز داشته باشد. stop condition و circuit breaker خودکار اجرای ناسالم را متوقف می‌کنند؛ pause/resume فقط ابزار break-glass است و در حالت pause claim جدید متوقف، in-flight محدود تکمیل و تمام jobها پایدار می‌مانند.
- staging از بات و کانال موجود و متصل خود استفاده می‌کند. preflight باید fingerprint هر دو را قبل از اولین side effect با allowlist staging تطبیق دهد.
- پس از export و checksum شواهد، تمام داده runtime ساخته‌شده با `run_id` از DB و cache هر دو staging peer و پیام‌های Telegram همان run پاک می‌شوند. cleanup از مسیر authority و API دامنه انجام و سپس sync/readback تأیید می‌شود؛ SQL حذف مستقیم بین دو peer مجاز نیست. کاربر، بات، کانال یا تنظیم pre-existing حذف نمی‌شود؛ اگر runner خودش کاربر آزمایشی ساخته باشد، همان کاربر نیز run-scoped و قابل حذف است.
- artifact استاندارد هر run شامل `manifest.json`, `events.jsonl`, `errors.jsonl`, `reconciliation.json` و `summary.md` است. همه فایل‌ها `schema_version`, `run_id`, seed، commit، fingerprint محیط، config مؤثر، زمان UTC، شناسه job/dedupe، expected/actual و checksum دارند و token، PII و متن production در آن‌ها redacted است تا agentهای AI و انسان یک ورودی واحد و قابل بازتولید داشته باشند.
- صف اصلی Telegram execution plane یکتا با اولویت `M0` تا `M7` است و صف‌های دامنه‌ای فقط feeder/coordinator آن هستند. شش feeder قطعی شامل ثبت/کنترل آفر، edit کانال، معامله/درخواست، مدیریتی/سیستمی، وضعیت بازار و عملیات زمان‌دار است. publish و edit صف‌های مستقل ولی دارای dependency هستند؛ fairness/catch-up و handoff در `C58/C59/C60` با قراردادهای بخش‌های 3، 6.5 و 13.12 بسته شده‌اند.
- ترتیب داخلی صف edit همیشه فعال است: active lot-partial، traded، expired، cancelled، سایر اصلاحات و repair؛ آفر partial که پیش از dispatch کامل شود به طبقه traded reclassify می‌شود. newest-first بر `offer.created_at` تکیه دارد.
- ترتیب `M0` به‌طور صریح callback/OTP deadlineدار، اعلان معامله عبورکرده از پنج ثانیه و سپس publication آفر است. اعلان نتیجه معامله ابتدا `M1` است و فقط recipient ارسال‌نشده مستقل ارتقا می‌یابد.
- برای کاربر authenticated، پیام و به‌خصوص anchor تا حد امکان حذف نمی‌شود و بات نباید او را برای بازگرداندن منو مجبور به `/start` کند. context keyboard در پایان success/error/cancel/timeout با منوی persistent جایگزین می‌شود؛ inline keyboardهای business-stale همچنان حذف می‌شوند.

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
| `answerCallbackQuery` و پاسخ تعاملی `M0` | فقط تا deadline کوتاه همان تعامل قابل retry است | پس از deadline ارسال نمی‌شود، به `EXPIRED_INTERACTION` می‌رود و breach ثبت می‌شود |
| انتشار آفر جدید در کانال | Offer، status، version و `expires_at` دوباره خوانده می‌شوند | آفر terminal یا منقضی `SUPERSEDED` می‌شود و هرگز دیرهنگام منتشر نمی‌شود |
| edit معامله جزئی آفر | آخرین مقدار باقی‌مانده و version بازسازی می‌شود | اگر آفر terminal شده باشد، edit جزئی supersede و terminal edit جایگزین می‌شود |
| edit نهایی معامله، انقضا یا لغو | اگر پیام کانال وجود دارد، آخرین متن terminal و حذف دکمه دوباره اعمال می‌شود | اگر آفر هرگز منتشر نشده یا پیام قطعاً وجود ندارد، job با شاهد به no-op نهایی تبدیل می‌شود |
| پیش‌نمایش خصوصی آفر با دکمه انقضا | فعال‌بودن آفر و معتبر‌بودن دکمه بازبینی می‌شود | preview دارای دکمه stale ارسال نمی‌شود؛ در صورت نیاز فقط وضعیت نهایی فعلی ارسال می‌شود |
| درخواست معاملاتی دارای دکمه قبول یا رد | request و trade state بازخوانی می‌شوند | دکمه stale ارسال نمی‌شود و notification وضعیت نهایی جایگزین آن می‌شود |
| اعلان نهایی و غیرقابل‌تغییر معامله به دو طرف | از receipt immutable و وضعیت نهایی بازسازی می‌شود | چون نتیجه هنوز صحیح و لازم است، بدون دکمه stale تحویل ادامه می‌یابد |
| پیام مدیریتی یا bulk | TTL خود job بازخوانی می‌شود | پس از TTL به `SUPERSEDED` می‌رود و دیرهنگام ارسال نمی‌شود |
| حذف پیام‌های تست | تعلق پیام به همان `run_id` و allowlist staging دوباره کنترل می‌شود | حذف متوقف و مورد برای cleanup بعدی ثبت می‌شود؛ مقصد دیگری لمس نمی‌شود |

رفتار همه ردیف‌ها قطعی است: پیام یا دکمه stale ارسال نمی‌شود و در صورت نیاز فقط وضعیت نهایی فعلی جایگزین می‌شود. stateهای `SUPERSEDED` و `EXPIRED_INTERACTION` جزو قرارداد ADR-04 هستند. مقدار خام `retry_after` بدون truncation ذخیره می‌شود؛ cap فعلی ۱۲۰، ۳۰۰ یا ۲۴ ساعت مبنای dispatch نخواهد بود.

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
| `429` با `retry_after` | `PENDING_RETRY` | بله | مقدار خام ذخیره و تا `retry_after + safety_margin` هیچ تلاش انجام نمی‌شود؛ سپس قرارداد freshness بخش 13.8 اجرا می‌شود |
| `429` بدون `retry_after` | `PENDING_RETRY` | بله | backoff محافظه‌کارانه با jitter و alert نقص پاسخ؛ retry سریع ممنوع است |
| `500` تا `599` برای edit یا عملیات idempotent | `PENDING_RETRY` | بله | backoff نمایی محدود با jitter؛ پیش از هر retry revalidation انجام می‌شود |
| `500` تا `599` برای `sendMessage` پس از پذیرش احتمالی request | `AMBIGUOUS` | کورکورانه خیر | تا اثبات وجود یا نبود پیام توسط observer/reconciliation دوباره send نمی‌شود |
| خطای DNS، connect یا pool پیش از ارسال request | `PENDING_RETRY` | بله | safe retry با backoff و circuit breaker انجام می‌شود |
| timeout یا قطع ارتباط پس از احتمال پذیرش `sendMessage` | `AMBIGUOUS` | کورکورانه خیر | observer و reconciliation باید وجود یا نبود پیام را ثابت کنند تا duplicate ساخته نشود |
| timeout در edit idempotent | `PENDING_RECONCILE` | پس از بازخوانی | ابتدا وضعیت پیام و domain بازخوانی و فقط آخرین edit معتبر تکرار می‌شود |
| بدنه نامعتبر یا غیر JSON با HTTP موفق | برای send برابر `AMBIGUOUS` | کورکورانه خیر | protocol alert ایجاد می‌شود؛ edit پس از revalidation قابل retry است |
| `4xx` ناشناخته | `QUARANTINED` | خودکار خیر | error جدید برای تصمیم انسانی و افزودن fixture ثبت می‌شود؛ هیچ mutation کسب‌وکار رخ نمی‌دهد |

### 13.10 توضیح چالش gateway — `TOPQ-C41`

این چالش سه نقص مستقل ولی محدود در gateway فعلی دارد و راه‌حل آن در `2026-07-16` توسط مالک محصول تأیید شد:

1. تابع فعلی هر `HTTP 200` را موفق می‌داند، حتی اگر Telegram یا fault adapter بدنه `{"ok": false}` برگرداند. قرارداد رسمی Bot API فیلد `ok` را مرجع می‌داند؛ بنابراین gateway باید ابتدا envelope را parse و بعد نتیجه را تعیین کند.
2. مسیر async برای هر پیام یک `httpx.AsyncClient` جدید می‌سازد. در بار بالا، connection و TLS مرتب از نو ساخته می‌شوند و احتمال timeout و مصرف resource بیشتر می‌شود. قرارداد نهایی، یک client مشترک با connection pool، timeoutهای جدا و lifecycle روشن startup/shutdown است.
3. endpoint به production Bot API hardcode شده است. قرارداد نهایی، mode محدود `main` یا `test` است که URL را خود برنامه از allowlist می‌سازد؛ URL دلخواه از env پذیرفته نمی‌شود. بات و کانال staging موجود همچنان از mode اصلی Telegram استفاده می‌کنند و `/test/` فقط برای ماتریس جداگانه Test DC است، نه تنظیم نرخ staging.

این تغییر قراردادی هیچ متن یا رفتار محصولی را عوض نمی‌کند؛ فقط تشخیص نتیجه، پایداری connection و جداسازی محیط را درست می‌کند.

### 13.11 قرارداد production canary — `TOPQ-N17`

قرارداد قطعی canary:

1. پیش از production، حداقل ده اجرای کامل ده‌دقیقه‌ای با seedهای متفاوت در چند پنجره زمانی و یک endurance شصت‌دقیقه‌ای در staging انجام شود. نقض critical باید صفر باشد.
2. پیش از تحویل مالکیت، یک shadow planner/auditor ایزوله برای یک چرخه کامل بازار و حداکثر ۲۴ ساعت در production اجرا شود. این جزء صف قابل‌ارسال نیست، job آن هرگز قابل promote نیست و فقط intent فرضی، priority، eligibility، payload hash، sync delay و lag را با side effect واقعی مسیر فعلی مقایسه می‌کند.
3. چون فقط یک کانال داریم، canary درصدی بین direct sender و queue ممنوع است. مالکیت ارسال باید اتمیک و برای کل کانال در یک پنجره کم‌ترافیک منتقل شود.
4. پس از فعال‌سازی، بازه‌های کنترل ۳۰ دقیقه، ۲ ساعت و ۲۴ ساعت اجرا شوند و فقط ترافیک طبیعی production مشاهده شود؛ بار مصنوعی ممنوع است.
5. مشاهده حتی یک duplicate، انتشار آفر stale، مقصد production/staging اشتباه، از‌دست‌رفتن job یا اختلاف قطعی DB/sync موجب stop خودکار می‌شود. عبور backlog، `M0` latency یا oldest-job از SLO مصوب نیز stop condition است.
6. rollback نباید pending و ambiguous را رها و direct sender را فوراً روشن کند؛ ابتدا claim متوقف، jobهای in-flight و ambiguous reconcile و سپس ownership با runbook اتمیک برگردانده می‌شود.

### 13.12 ثبت نهایی closure، owner و rollback — `2026-07-16`

ممیزی نهایی میان بخش‌های ۱ تا ۱۴ و سند سناریوی staging انجام شد. ابهام طراحی باقیمانده صفر است؛ مقدار interval نهایی، نتیجه benchmark و شواهد اجرا «خروجی Stage» هستند، نه تصمیم طراحی حل‌نشده.

#### قراردادهای تکمیلی قطعی

1. **منبع حقیقت:** جدول جدید `telegram_delivery_jobs` منبع حقیقت execution، lease، attempt، نتیجه provider و اولویت اصلی است. `offer_publication_states` نگاشت canonical آفر به `(bot_identity, destination_identity, message_id)` و state دامنه publication است؛ `offers.channel_message_id` فقط mirror سازگاری است. outboxها و workerهای فعلی پس از flag فقط feeder هستند.
2. **هویت و handoff:** کلید یکتا از `feeder_kind + source_natural_id + source_version + action_kind + destination_identity` ساخته می‌شود. روی Iran، mutation دامنه و intent در یک تراکنش ثبت می‌شوند؛ intent sync می‌شود و فقط foreign رابطه child/main را در یک تراکنش یا outbox بازیاب‌پذیر ایجاد می‌کند. lease و نتیجه Telegram sync برگشتی نمی‌شوند.
3. **ترتیب:** feeder ابتدا internal rank و freshness را اعمال می‌کند. صف اصلی سپس `M0..M7`، deadline و `enqueued_seq` افزایشی foreign را اعمال می‌کند. ساعت یا ID محلی Iran/foreign مبنای FIFO مشترک نیست. `offer.created_at` فقط برای newest-first صف edit و natural identity برای replay به‌کار می‌رود.
4. **فشار و fairness:** `M0` strict است. کلاس‌های `M6/M7` فقط ظرفیت باقی‌مانده می‌گیرند و عبور سن آن‌ها alert/SLO breach است، نه مجوز عبور از `M0`. feeder edit سهم catch-up یک‌به‌بیست دارد. broadcastها یک in-flight برای هر campaign، سقف کلی دو campaign و round-robin دارند.
5. **limiter و 429:** budget bot و destination هم‌زمان اعمال می‌شوند. 429 ابتدا lane مقصد را تا مقدار خام `retry_after + safety_margin` می‌بندد. پس از حاشیه probe، فقط یک مقصد دیگر probe می‌شود؛ 429 مقصد دوم در پنجره دوثانیه‌ای cooldown کل bot را تا بیشترین موعد فعال می‌کند. polling صد میلی‌ثانیه‌ای وجود ندارد.
6. **lease و claim:** lease حداقل `request_timeout + 15s` است، با fencing token و heartbeat. claim در تراکنش کوتاه PostgreSQL انجام و HTTP خارج آن اجرا می‌شود. نتیجه worker با token قدیمی پذیرفته نمی‌شود.
7. **degraded mode:** نبود DB قبل از commit پاسخ موفق تولید نمی‌کند؛ نبود Redis ارسال را fail-closed و صف PostgreSQL را محفوظ نگه می‌دارد؛ outage Telegram circuit-break و backlog می‌سازد. recovery با probe و drain کنترل‌شده انجام می‌شود.
8. **ابهام ارسال:** send مبهم بدون شاهد قطعی resend نمی‌شود؛ operator نیز اجازه SQL یا retry دستی آن را ندارد. edit idempotent پس از revalidation قابل تکرار و callback دیرهنگام منقضی است.
9. **retention و privacy:** terminal jobها ۳۰ روز hot، payload و error body خامِ redacted حداکثر هفت روز، aggregate غیرهویتی ۱۸۰ روز و unresolvedها تا ۳۰ روز پس از resolution نگهداری می‌شوند. metric label شامل ID، متن یا PII نیست.
10. **rollout:** migration افزایشی و code سازگار ابتدا با flag خاموش روی دو peer deploy می‌شود؛ shadow planner حداکثر ۲۴ ساعت side effect ندارد. cutover کل کانال و ownership اتمیک است و canary درصدی direct/queue ممنوع است. rollback فقط پس از توقف claim و reconciliation in-flight/ambiguous انجام می‌شود.
11. **ایمنی حساب‌های تست:** pool هشتادحسابی و جریان کامل sender/receiver در Test DC است. main-DC staging فقط bot/channel و مقصدهای کنترل‌شده allowlist را برای کالیبراسیون نرخ با workload replay صف به‌کار می‌گیرد؛ هیچ کاربر production وارد تست نمی‌شود.
12. **عملیات:** pause/resume ابزار break-glass است. resume پس از preflight انجام می‌شود؛ retry job permanent فقط پس از اصلاح علت و source version جدید مجاز است و ambiguous فقط با شاهد قطعی resolve می‌شود.
13. **بات ویرایشگر:** primary ناشر یکتا و مالک همه تعامل‌های خصوصی/callback است. `channel_editor` فقط editهای allowlisted همان کانال را زیر همان main queue اجرا می‌کند؛ routing آن immutable، credential خارج DB، permission حداقلی و fallback خودکار ممنوع است. فعال‌شدن editor خروجی Stage 4 است و شکست گیت، mode امن `primary-only` را بدون تغییر schema نگه می‌دارد.

#### RACI و زمان پاسخ

| نقش | مسئولیت | زمان پاسخ |
| --- | --- | --- |
| مالک محصول | حفظ semantics آفر، SLO و تصمیم نهایی go/no-go | تأیید گزارش Stage 4/5 پیش از release |
| مالک Backend | schema، state machine، gateway، feederها، تست و rollback فنی | triage هشدار بحرانی حداکثر ۱۰ دقیقه |
| مالک Operations/on-call | credentialهای primary/editor، permission و rotation، staging pool، dashboard، pause/resume و incident command | دریافت alert حداکثر ۱ دقیقه و acknowledge حداکثر ۱۰ دقیقه |
| مالک Security/Privacy | allowlist، secret/PII، sampler و retention | پیش از تست زنده و هر تغییر credential |
| مسئول staging run | manifest، preflight، stop condition، export/checksum و cleanup | در تمام طول run حاضر و مجاز به stop فوری |

نام فرد هر نقش در manifest همان run و checklist release ثبت می‌شود؛ نبود نام یا عدم acknowledge، اجرای زنده را fail-closed متوقف می‌کند.

#### نگاشت اجرا و بازگشت

| دامنه چالش | owner اصلی | Stage شاهد | rollback/رفتار امن |
| --- | --- | --- | --- |
| `C01–C20` | Product + Backend | Stage 2 و 4 | flag خاموش، عدم side effect و بازگشت به رفتار snapshotشده `main` |
| `C21–C45` | Backend + Operations | Stage 2، 3 و 4 | توقف claim، حفظ job پایدار، fail-closed limiter و migration افزایشی |
| `C46–C61` | Backend + staging run | Stage 2 و 4 | cancel فقط پیش از claim، supersession نسخه‌دار و cleanup محدود به `run_id` |
| `C62–C67` | Backend + Operations + Security | Stage 3 و 4 | editor خاموش، توقف claim آن، حفظ job و reconcile پیش از هر تغییر routing |
| `N01–N06` | Product + Operations | Stage 4 و 5 | `NO-GO`، بدون کاهش SLO یا تغییر semantics |
| `N07–N19` | Operations + Security | Stage 1، 4، 5 و 6 | توقف تست/release، حفظ artifact redacted، mode امن primary-only و عدم لمس production |

هیچ ردیفی صرفاً با نوشته‌شدن این تصمیم‌ها `IMPLEMENTED` یا `VERIFIED` محسوب نمی‌شود. ارتقای وضعیت فقط با commit کد/تست یا artifact staging انجام می‌شود.

## 14. معیار پذیرش نهایی

- پیش از شروع Stage 3، تصمیم‌های بخش 13.12 به ADRهای بخش 13.6 استخراج، قرارداد pure با `M0` تا `M7` بازنویسی و تمام تست‌های Stage 2 دوباره پاس شده باشند. گیت اولیه در `2026-07-16` با شش ADR، `44` تست قرارداد و `171` تست هدفمند پاس شد؛ ADR هفتم بعد از تصمیم editor افزوده شد و پیش از فعال‌سازی runtime باید تست‌های `C62..C67/N19` نیز پاس شوند.
- هر چالش فنی و غیرفنی owner، stage هدف، تست یا شاهد پذیرش و rollback داشته باشد؛ مورد بدون شاهد «بسته» محسوب نمی‌شود.
- ظرفیت/عمر آفر و معنای SLO publication به تصمیم صریح محصول رسیده باشد؛ backlog مورد انتظار نباید بعداً به‌عنوان باگ ناشناخته گزارش شود.
- WebApp با snapshot رفتار فعلی `main` یکسان باشد؛ در Bot هر مسیر فقط یک پیام ترکیبی موفقیت/«لفظ شما» با دکمه انقضا داشته باشد، `sendMessage` خصوصی دوم صفر باشد و خطای Telegram پس از commit هیچ copy غیرعادی به کاربر نشان ندهد.
- نرخ پذیرش حداقل سه آفر در ثانیه بدون ازدست‌رفتن رکورد صف تأیید شود.
- هر آفر یک پیام مستقل داشته باشد و batching وجود نداشته باشد.
- هیچ `429` به `FAILED` یا انقضای زودهنگام آفر منجر نشود و هیچ publication یا دکمه عملیاتی stale پس از پایان عمر کسب‌وکار ارسال نشود.
- همه retryها `retry_after` را رعایت کنند.
- اولویت `M0` تا `M7`، ترتیب انتقال feederها و تمام رتبه‌های داخلی بخش ۳ در تست اثبات شود.
- اعلان معاملاتی pending هر recipient در مرز پنج ثانیه از `M1` به `M0` ارتقا یابد، بدون اینکه `retry_after` یا cooldown را دور بزند.
- ترتیب edit در تمام عمق‌های صف فعال باشد؛ active lot-partial، traded، expired، cancelled و سایر editها را با newest-first انتخاب و editهای بالای پنج دقیقه را در stale tail نگه دارد.
- آفر partial که پیش از dispatch کامل می‌شود هیچ edit جزئی stale نسازد و فقط terminal edit متناظر را نگه دارد.
- چند edit یک Offer به یک state نهایی coalesce شوند و catch-up ثابت کند پس از پایان بار هیچ stale edit دائمی باقی نمی‌ماند.
- علامت terminal و حذف دکمه کانال یک API call باشند.
- هیچ مسیر مستقیم send/edit کانال limiter را دور نزند.
- primary تنها publisher باشد و editor فقط editهای allowlisted همان کانال را با canonical message identity اجرا کند؛ send/private/callback/delete یا مقصد دیگر برای editor fail-closed باشد.
- routing هر job پس از enqueue تغییر نکند، outage/revoke/429 editor fallback خودکار به primary نسازد و pending/ambiguous پیش از هر rollback reconcile شود.
- fingerprint، permission readback و secret scan ثابت کنند editor فقط `can_edit_messages` لازم را دارد و هیچ token در DB، log یا artifact نیست.
- Stage 4 با trace یکسان نتیجه primary-only و primary+editor را مقایسه و تصمیم صریح فعال‌سازی editor را ثبت کند؛ نبود بهبود یا شکست cross-bot smoke به mode primary-only منجر شود، نه کاهش SLO.
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
- هیچ مسیر authenticated برای بازیابی منوی اصلی به `/start` وابسته نباشد؛ anchor فعال خودکار حذف نشود و تمام خروجی‌های success/error/cancel/timeout جریان موقت، کیبورد persistent مناسب را بازگردانند.
- artifactهای AI-readable پیش از cleanup export و checksum شوند و سپس هیچ داده runtime یا پیام Telegram متعلق به `run_id` در staging باقی نماند.
- هیچ داده یا کانال production در آزمایش staging تغییر نکند.
- production deploy فقط در Stage جدا و با دستور صریح انجام شود.

## 15. منابع پایه

- Telegram Bots FAQ: `https://core.telegram.org/bots/faq`
- Telegram Bot API response envelope: `https://core.telegram.org/bots/api#making-requests`
- Telegram Bot API `ResponseParameters.retry_after`: `https://core.telegram.org/bots/api#responseparameters`
- Telegram Bot API `answerCallbackQuery`: `https://core.telegram.org/bots/api#answercallbackquery`
- Telegram Bot API `editMessageText`: `https://core.telegram.org/bots/api#editmessagetext`
- Telegram Bot API `getChatMember`: `https://core.telegram.org/bots/api#getchatmember`
- Telegram `ChatAdministratorRights`: `https://core.telegram.org/bots/api#chatadministratorrights`
- Telegram reply keyboard persistence: `https://core.telegram.org/constructor/replyKeyboardMarkup`
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
- `bot/keyboards.py`
- `bot/message_manager.py`
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
