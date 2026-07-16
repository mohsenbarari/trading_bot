# سناریوی آزمایش staging صف انتشار آفر تلگرام

## وضعیت سند

- تاریخ: `2026-07-15`
- شاخه: `candidate/telegram-offer-publication-queue`
- Roadmap مرجع: `docs/TELEGRAM_OFFER_PUBLICATION_QUEUE_ROADMAP_20260715.md`
- وضعیت: قرارداد اجرایی Stage 1 به‌روزشده با `M0` تا `M7`، شش feeder، `max_active_offers=10`، closure چالش‌ها، قرارداد keyboard/anchor و گیت primary/channel-editor تأییدشده در `2026-07-16`
- این سند مجوز deploy به staging یا production نیست.
- هیچ بخش این آزمایش اجازه خواندن و نوشتن هم‌زمان در production یا ارسال پیام به کانال production را نمی‌دهد.

## 1. هدف و قرارداد بار

هدف هر دور اصلی، بازسازی ده دقیقه رفتار طبیعی بازار با حفظ بار پذیرش صف است:

| ورودی | تعداد در ۱۰ دقیقه | نرخ میانگین |
| --- | ---: | ---: |
| آفر معتبر و پذیرفته‌شده | `1800` | `3 valid offer/s` |
| تلاش ثبت آفر ناصحیح | `400` | `0.67 invalid attempt/s` |
| کل تلاش‌های ثبت | `2200` | `3.67 attempt/s` |

قواعد قطعی:

- `400` آفر ناصحیح علاوه بر `1800` آفر معتبر هستند، نه بخشی از آن‌ها.
- جریان ورودی در ثانیه `600` متوقف می‌شود، اما اندازه‌گیری تا تخلیه صف یا timeout سخت ادامه دارد.
- تمام رویدادها با `run_id`، seed و شناسه منطقی یکتا قابل ردیابی هستند.
- trace ورودی پیش از اجرا ساخته و hash می‌شود؛ تغییر تطبیقی trace در میانه یک دور مجاز نیست.
- یک trace یکسان برای مقایسه همه intervalهای کاندید بازپخش می‌شود.

## 2. شکل طبیعی ورود و پیک

میانگین ده‌دقیقه‌ای نباید به نرخ ثابت در هر ثانیه تبدیل شود. generator از سه رژیم آرام، عادی و پیک استفاده می‌کند:

- چند بازه پیک اجباری `3` تا `8` ثانیه‌ای وجود دارد.
- در پیک، نرخ آفر **معتبر** بین `8` تا `12` آفر در ثانیه است.
- چند پیک متوالی در نیمه اول و نیمه دوم تست قرار می‌گیرند.
- بازه‌های آرام، افزایش پیک را جبران می‌کنند تا جمع آفر معتبر دقیقاً `1800` بماند.
- `400` تلاش نامعتبر مستقل از trace معتبر و با seed ثابت در موقعیت‌های تصادفی میان کل ده دقیقه پخش می‌شوند؛ تلاش نامعتبر در پیک نیز مجاز است.
- quotaهای نهایی پس از shuffle دوباره کنترل می‌شوند؛ تصادفی‌سازی نباید تعدادها را تقریبی کند.

برای کاهش اثر شانس، حداقل سه trace ثابت با سه seed متفاوت ساخته می‌شود. همان سه trace در تمام مقایسه‌ها استفاده می‌شوند.

## 3. ترکیب ۱۸۰۰ آفر معتبر

### 3.1 شکل آفر

| شکل | تعداد هدف |
| --- | ---: |
| یکجا و بدون لات | `900` |
| لات‌بندی دو قسمتی | `450` |
| لات‌بندی سه قسمتی | `450` |

لات‌ها باید از قواعد runtime همان دور پیروی کنند:

- جمع لات‌ها دقیقاً برابر quantity باشد.
- هر لات حداقل اندازه مجاز runtime را داشته باشد.
- quantity و price از اعتبارسنجی واقعی پروژه عبور کنند.
- generator اجازه ندارد validator را bypass کند.

### 3.2 کالا، نوع معامله و تسویه

- تمام کالاهای فعال staging باید حداقل سهم مشخص داشته باشند.
- هر کالا، در صورت مجازبودن داده، با هر دو نوع `buy` و `sell` پوشش داده می‌شود.
- هر دو تسویه `cash` و `tomorrow` پوشش داده می‌شوند.
- توزیع غالب از production گرفته می‌شود، اما برای ترکیب‌های کم‌تکرار حداقل quota رزرو می‌شود تا هیچ کالایی حذف نشود.
- اگر ترکیبی در production نمونه نداشته باشد، fixture معتبر آن با قواعد جاری staging ساخته و در گزارش با `synthetic_gap_fill` علامت‌گذاری می‌شود.

### 3.3 توضیحات و هشدار قیمت

- متن واقعی notes کاربران production هرگز کپی نمی‌شود.
- فقط وجود یا عدم وجود notes و توزیع طول آن الگو گرفته می‌شود؛ متن staging مصنوعی است.
- سناریوی price warning و acknowledgment در quota جدا ثبت می‌شود.
- قیمت برگرفته از production بدون اعتبارسنجی دوباره replay نمی‌شود؛ stale price نباید به‌اشتباه سهم معتبر را کاهش دهد.

## 4. ساخت fixture معتبر از production به‌صورت read-only

### 4.1 مرز ایمنی

sampler فقط یک artifact غیرهویتی می‌سازد. اتصال production در خود runner محیط staging وجود ندارد.

فرایند مجاز:

1. اجرای sampler مستقل با تراکنش `READ ONLY` و `statement_timeout` محدود.
2. خواندن آمار و نمونه طبقه‌بندی‌شده از `offers` و نام کالا از `commodities`.
3. حذف فیلدهای هویتی و تولید artifact پاک‌سازی‌شده.
4. انتقال artifact به ورودی محلی تست staging.
5. ساخت offer جدید با کاربران، شناسه‌ها، idempotency key و زمان‌های مخصوص staging.

هیچ دستور `INSERT`، `UPDATE`، `DELETE`، DDL، قفل صریح یا cleanup روی production مجاز نیست.

### 4.2 فیلدهای مجاز در artifact

- نام یا کلید غیرهویتی کالا
- `offer_type`
- `settlement_type`
- `quantity`
- bucket یا percentile قیمت و در صورت نیاز قیمت نمونه
- `is_wholesale`
- تعداد لات و نسبت هر لات به quantity
- وجود notes و طول آن، بدون متن notes
- نوع هشدار قیمت، در صورت وجود

فیلدهای ممنوع:

- `user_id` و `actor_user_id`
- نام، شماره تلفن، telegram id یا هر شناسه کاربر
- متن واقعی notes
- `channel_message_id`
- `offer_public_id` و id داخلی production
- idempotency key واقعی

### 4.3 الگوریتم بازسازی staging

- نمونه‌گیری طبقه‌بندی‌شده است، نه `ORDER BY random()` سنگین روی production.
- توزیع commodity/type/settlement/wholesale/lot-count از production محاسبه می‌شود.
- template با seed انتخاب و به commodity فعال staging نگاشت می‌شود.
- quantity و شکل لات حفظ یا با قواعد runtime نرمال می‌شود.
- price با وضعیت جاری staging و validator رقابتی سازگار می‌شود؛ قیمت تاریخی کورکورانه استفاده نمی‌شود.
- notes مصنوعی با طول مشابه ساخته می‌شود.
- همه payloadها پیش از ورود به trace، یک بار با validator واقعی بررسی می‌شوند.
- hash artifact، زمان snapshot، بازه زمانی داده و تعداد نمونه‌ها در گزارش ثبت می‌شود؛ خود artifact خام در Git commit نمی‌شود.

## 5. ماتریس ۴۰۰ تلاش ثبت ناصحیح

این تلاش‌ها باید از مسیر واقعی Telegram bot وارد شوند، پاسخ طبیعی همان handler را واقعاً دریافت کنند و هیچ Offer، publication intent یا پیام publication با اولویت `M0` نسازند.

| خانواده خطا | تعداد | پاسخ‌هایی که باید پوشش داده شوند |
| --- | ---: | --- |
| بلوک نوع معامله و تسویه | `70` | بلوک ناقص، تکراری، متناقض یا شناسه نامعتبر خرید/فروش و نقد/فردایی |
| کالا | `50` | کالای ناشناخته، مبهم یا عبارت غیرقابل نگاشت |
| quantity | `60` | مفقود، صفر، منفی، کمتر از حد، بیشتر از سقف یا بدون واحد قابل تشخیص |
| price | `60` | مفقود، چند قیمت، صفر/منفی یا تعداد رقم نامعتبر |
| لات | `120` | خالی، لات کوچک، جمع نابرابر، تعداد بیش از سقف یا ترکیب غیرعددی |
| متن و notes | `40` | کاراکتر/ساختار غیرقابل پردازش و notes بیش از ۲۰۰ کاراکتر |
| **جمع** | **`400`** |  |

قواعد:

- ۴۰۰ موقعیت یکتا با seed از میان timeline انتخاب می‌شوند.
- هر متن نامعتبر فقط یک علت اصلی هدف دارد تا پاسخ واقعی و شاخه handler قابل تشخیص باشد.
- متن خطا، پیشنهاد فرمت صحیح، reply markup و هر callback answer مرتبط از سمت گیرنده ثبت می‌شود.
- هر خانواده تمام زیرشاخه‌های پاسخ موجود در parser/validator را حداقل یک بار پوشش می‌دهد؛ quota باقیمانده با الگوی production و seed توزیع می‌شود.
- اگر یک متن اصلاً به handler آفر route نشود، به‌عنوان پوشش موفق پاسخ validation پذیرفته نمی‌شود؛ ورودی نامعتبر باید نشانگر لازم برای ورود به جریان آفر را داشته باشد.
- competitive-price rejection و price-warning فقط وقتی در این quota می‌آیند که در runtime فعال و واقعاً قابل تولید باشند؛ در غیر این صورت quota با خطای محتوایی قطعی جایگزین می‌شود.
- هیچ side effect انتشار کانال برای درخواست ردشده مجاز نیست.
- نتیجه idempotency replay صحیح، آفر ناصحیح محسوب نمی‌شود و در سناریوی جداگانه concurrency سنجیده می‌شود.

## 6. کاربران و محدودیت‌های کسب‌وکار

با expiry دو دقیقه‌ای حدود ۳۶۰ آفر می‌تواند هم‌زمان فعال باشد. برای کاهش تعداد حساب‌های آزمایشی، محدودیت فقط در staging به شکل زیر تنظیم می‌شود:

```text
staging max_active_offers = 10
production/default max_active_offers = 4
```

- حداقل نظری ownerهای هم‌زمان با سقف ۱۰ برابر `ceil(360 / 10) = 36` است.
- استفاده از فقط ۳۶ حساب، پاسخ‌های خصوصی را روی هر chat بیش از حد متراکم و غیرطبیعی می‌کند.
- baseline از `80` حساب واقعی Telegram test استفاده می‌کند.
- در هر دور حداقل `70` حساب باید واقعاً مالک یک یا چند آفر شوند؛ بقیه می‌توانند requester-dominant، کم‌فعال یا گیرنده پیام مدیریتی باشند.
- همه حساب‌ها می‌توانند روی آفر حساب دیگری درخواست معامله بزنند، اما self-trade ممنوع است.
- رفتار کاربران یکنواخت نیست: توزیع فعالیت ownerها تا حد امکان از percentileهای read-only production الگو می‌گیرد و شامل کاربران پرتکرار، متوسط و گهگاهی است.
- مقدار `10` سقف قطعی staging است؛ generator باید آفرها را میان حداقل ۷۰ owner توزیع کند تا هیچ owner از آن عبور نکند و این limit عامل مصنوعی رد آفر معتبر نشود.
- مقدار `10` از مسیر مدیریتی مرجع Iran staging ثبت می‌شود؛ update مستقیم foreign staging مجاز نیست.
- اجرای تست تا وقتی sync و cache هر دو سمت Iran/foreign staging مقدار `10` را نشان ندهند شروع نمی‌شود.
- default مدل و production نباید تغییر کنند و باید همچنان `4` گزارش شوند.
- limitها و counterهای runtime پیش از هر دور فقط در staging بررسی و به وضعیت تکرارپذیر برگردانده می‌شوند.
- هیچ شناسه یا حساب production برای ارسال پیام staging استفاده نمی‌شود.

برای معیار «دریافت واقعی همه پاسخ‌ها»، transport ساختگی یا مقصد منطقی کافی نیست. sender/receiver harness باید با حساب‌های آزمایشی واقعی Telegram پیام را بفرستد، دکمه را بزند و پیام یا edit دریافتی را مشاهده کند.

قرارداد فراهم‌کردن تعداد حساب کافی، استفاده از محیط تست رسمی و جداگانه Telegram است:

- bot و کاربران محیط test از محیط اصلی Telegram جدا هستند.
- userهای تست با شماره‌های رزروشده Test DC ساخته می‌شوند.
- درخواست Bot API از endpoint محیط test انجام می‌شود.
- observer سمت کاربر با MTProto/TDLib، پیام خصوصی، پیام کانال، edit و پاسخ callback را ثبت می‌کند.

محدودیت‌های flood محیط test ممکن است با محیط اصلی یکسان نباشد؛ بنابراین نتیجه آن برای **کامل‌بودن پاسخ‌ها و صحت جریان** استفاده می‌شود، نه برای انتخاب interval نهایی production. کالیبراسیون نرخ همچنان روی bot/channel واقعی و مستقل staging در محیط معمول Telegram انجام می‌شود.

اگر زیرساخت Test DC یا pool گیرنده واقعی آماده نباشد، اجرای A2 آغاز نمی‌شود و نتیجه mock هرگز به‌عنوان pass ثبت نمی‌شود. برای تکمیل pool نیز نباید به کاربران production پیام آزمایشی ارسال شود.

## 7. معامله، concurrency و انقضا

### 7.1 معامله

- `540` آفر معتبر، یعنی حدود `30%`، هدف حداقل یک معامله موفق هستند.
- انتخاب این آفرها seedدار و شامل عمده، دو لات و سه لات است.
- وضعیت نهایی کامل‌شده و معامله جزئی جداگانه گزارش می‌شوند.
- هر trade row موفق دقیقاً دو اعلان خصوصی اولیه `M1`، یکی برای هر طرف، تولید می‌کند.
- تعداد اعلان‌ها از تعداد **معامله‌های موفق واقعی** محاسبه می‌شود، نه صرفاً تعداد آفر هدف؛ هر recipient ارسال‌نشده پس از پنج ثانیه مستقل به `M0` ارتقا می‌یابد.

ترکیب پیشنهادی ۵۴۰ آفر:

- `270` آفر با مسیر عادی و بدون رقابت تکمیل می‌شوند.
- `162` آفر سناریوی درخواست هم‌زمان دارند.
- `108` آفر ابتدا معامله جزئی و سپس تکمیل می‌شوند.

### 7.2 درخواست هم‌زمان

برای `162` آفر، درخواست‌ها با barrier مشترک آزاد می‌شوند تا واقعاً هم‌زمان باشند:

| تعداد درخواست روی یک آفر | تعداد آفر |
| --- | ---: |
| ۲ درخواست | `65` |
| ۳ درخواست | `49` |
| ۴ درخواست | `32` |
| ۵ درخواست | `16` |
| **جمع** | **`162`** |

- تقریباً نیمی عمده و نیمی لات‌بندی‌شده هستند.
- در عمده فقط یک درخواست می‌تواند برنده شود.
- در لات‌بندی، هم برخورد چند درخواست روی یک لات و هم درخواست روی لات‌های متفاوت وجود دارد.
- quantity باقیمانده هرگز منفی نمی‌شود.
- هر درخواست دقیقاً یک نتیجه نهایی دارد و duplicate trade ساخته نمی‌شود.
- retry یا replay با همان idempotency key نتیجه کسب‌وکار را تکرار نمی‌کند.

### 7.3 انقضای دستی

- دقیقاً `180` فرمان انقضای دستی در timeline قرار می‌گیرد.
- `162` فرمان روی آفرهایی قرار می‌گیرد که برای معامله هم‌زمان انتخاب نشده‌اند.
- `18` فرمان عمداً با درخواست معامله race می‌کنند.
- در race فقط یک transition نهایی معتبر است؛ نتیجه واقعی برنده ثبت می‌شود.
- `180` تعداد فرمان است، نه تضمین `180` انقضای موفق.

### 7.4 انقضای خودکار

- مقدار runtime `offer_expiry_minutes` پیش از هر دور ثبت می‌شود و baseline مورد انتظار `2` دقیقه است.
- از ثانیه `120`، آفرهای قدیمی واجد شرایط انقضا می‌شوند.
- آفر منتشرشده با یک `editMessageText` ترکیبی، علامت انقضا و حذف دکمه را دریافت می‌کند.
- آفر منقضی‌شده قبل از publication به `DISABLED` می‌رود و نباید بعداً به‌عنوان آفر فعال منتشر شود.
- آفرهای ساخته‌شده در دو دقیقه آخر، در مرحله drain نیز تا تعیین تکلیف دنبال می‌شوند.

## 8. پیام‌های مدیریتی در پیک

baseline شامل پنج burst مدیریتی `M6` است:

- هر burst معادل `25` job مقصد است؛ مجموع baseline برابر `125` job `M6`.
- burstها داخل پیک‌های مختلف قرار می‌گیرند.
- `M6` فقط از ظرفیت باقی‌مانده استفاده می‌کند.
- `M6` نباید deadlineهای `M0` یا کارهای `M1` تا `M4` را گرسنه نگه دارد.
- فرمان مدیریتی و تعداد jobهای fan-out جداگانه شمرده می‌شوند.

## 9. catalog پاسخ‌های طبیعی Telegram

منظور از «پاسخ» در معیار محصول، هر خروجی واقعی Telegram است که کاربر، مدیر یا کانال در نتیجه سناریو دریافت می‌کند؛ شامل پیام جدید، edit، حذف دکمه و پاسخ callback.

برای هر business event، خروجی مورد انتظار پیش از اجرا در ledger نوشته و بعد با observer سمت گیرنده تطبیق داده می‌شود:

| رویداد | پاسخ‌های طبیعی که باید واقعاً دریافت شوند |
| --- | --- |
| متن آفر صحیح | preview تأیید با دکمه‌های تأیید/انصراف |
| تأیید آفر صحیح | callback answer، یک `editMessageText` که همان preview را به پیام ترکیبی موفقیت + «لفظ شما» با دکمه انقضا تبدیل می‌کند، و یک پیام مستقل کانال؛ `sendMessage` خصوصی دوم وجود ندارد |
| آفر صحیح دارای هشدار قیمت | پیام هشدار و دکمه تأیید هشدار، callback answer و سپس مسیر موفق معمول |
| آفر ناصحیح | متن خطای دقیق parser/validator همراه پیشنهاد فرمت صحیح و markup مرتبط، بدون پیام کانال |
| انتخاب/دکمه منقضی یا stale | alert طبیعی همان شاخه مانند فرآیند منقضی، انتخاب نامعتبر یا offer غیر فعال |
| معامله موفق | callback موفق، update دکمه/متن کانال و دو پیام خصوصی معامله برای دو طرف |
| معامله جزئی | callback موفق، edit دکمه‌های مقدار باقی‌مانده و دو پیام خصوصی معامله |
| درخواست بازنده عمده | alert عدم امکان/عدم موجودی، بدون پیام موفقیت معامله |
| درخواست بازنده لات | پیام یا edit پیشنهاد لات‌های باقی‌مانده، callback اطلاع پیشنهاد، و cleanup/به‌روزرسانی دکمه پیشنهاد |
| انقضای دستی موفق | حذف دکمه preview خصوصی، callback موفق و terminal edit ترکیبی کانال |
| انقضای دستی ناموفق یا race بازنده | callback طبیعی «فعال نیست/قفل است/یافت نشد» بدون terminal transition دوم |
| انقضای خودکار | terminal edit ترکیبی کانال، بدون پیام خصوصی جدید |
| انقضا قبل از publication | بدون پیام stale کانال و وضعیت `DISABLED` در ledger |
| فرمان مدیریتی | پاسخ preview/تأیید مدیر و پیام `M6` واقعی برای همه گیرندگان آزمایشی |

catalog نهایی از call siteهای واقعی `trade_create`, `trade_execute`, `trade_manage`, پیشنهاد لات، expiry و admin broadcast استخراج می‌شود. هر branch پاسخ یک `response_catalog_id` دارد و باید حداقل یک بار در manifest یکی از `1800` آفر صحیح، `400` آفر ناصحیح یا رویدادهای دنباله‌دار آن‌ها تخصیص یابد.

routing مورد انتظار نیز جزئی از ledger است: publication کانال، پیام خصوصی، callback، معامله و مدیریت فقط با `primary` اجرا می‌شوند. در mode کاندید، editهای allowlisted آفر کانال با `channel_editor` اجرا می‌شوند؛ در baseline همان editها از ابتدا به primary route می‌شوند. هیچ job موجودی در میانه اجرا bot خود را تغییر نمی‌دهد.

متدهای اجباری مورد پوشش:

- `sendMessage`
- `editMessageText`
- `editMessageReplyMarkup`
- `answerCallbackQuery`

cleanup با `deleteMessage` پس از پایان cooldown انجام می‌شود و جزو پنجره اندازه‌گیری نیست.

### 9.1 سناریوی کیبورد و پیام لنگر

برای یک کاربر authenticated واقعی، مسیرهای معامله، پنل کاربر، پنل مدیر، ثبت‌نام/اتصال مجدد حساب، success، validation error، cancel، timeout و restart بات اجرا می‌شوند:

- کاربر برای بازیابی منوی اصلی نباید `/start` بفرستد.
- anchor فعال نباید توسط cleanup زمان‌دار حذف شود.
- اگر context keyboard مانند ارسال شماره تماس نمایش داده شد، پایان هر شاخه باید منوی persistent مناسب نقش را بازگرداند.
- پیام‌های عادی بات فقط وقتی حذف می‌شوند که manifest برای همان `response_catalog_id` علت و TTL صریح داشته باشد.
- inline keyboard منقضی آفر/معامله باید طبق business state حذف شود و با reply keyboard اصلی اشتباه گرفته نشود.
- observer وجود reply keyboard را از update قابل مشاهده و ادامه موفق تعامل با دکمه اصلی بدون `/start` اثبات می‌کند.

## 10. اثبات دریافت واقعی پاسخ‌ها

### 10.1 سه شناسه برای هر پاسخ

هر پاسخ باید این سه شاهد را داشته باشد:

1. `business_event_id` یا شناسه درخواست/آفر/معامله
2. شناسه job و نتیجه Bot API
3. receipt سمت گیرنده شامل chat، message id، edit version یا callback result

صرف `ok=true` از Bot API برای معیار محصول کافی نیست؛ observer سمت گیرنده باید پیام یا edit را در Telegram ببیند. منظور از receipt، مشاهده در chat است و به معنی اثبات خواندن انسانی پیام نیست، چون Bot API read receipt عمومی برای این کاربرد نمی‌دهد.

### 10.2 sender/receiver harness

- sender با حساب‌های آزمایشی متن آفر را برای bot می‌فرستد و callbackها را واقعاً اجرا می‌کند.
- observer خصوصی پیام‌ها و editهای هر حساب آزمایشی را ثبت می‌کند.
- observer کانال publication، partial edit و terminal edit را ثبت می‌کند.
- پاسخ `answerCallbackQuery` از نتیجه همان درخواست callback سمت user client ثبت می‌شود.
- متن، markup، شناسه پیام، ترتیب و زمان دریافت با catalog مقایسه می‌شوند.
- برای هر response catalog id شمارنده `expected`, `bot_api_accepted`, `receiver_observed`, `duplicate` و `missing` ثبت می‌شود.

### 10.3 محیط تست رسمی Telegram

برای پوشش کامل پاسخ‌ها از Test DC رسمی Telegram با bot، کانال و userهای مخصوص test استفاده می‌شود. این محیط امکان ساخت userهای آزمایشی و مشاهده سمت گیرنده را بدون تماس با کاربران production می‌دهد.

نتیجه Test DC فقط برای این موارد معتبر است:

- کامل‌بودن catalog پاسخ‌ها
- صحت متن، markup و transition پیام
- هم‌بستگی end-to-end sender تا receiver
- concurrency کسب‌وکار و عدم duplicate

محدودیت flood در Test DC می‌تواند متفاوت یا سخت‌گیرانه‌تر از محیط اصلی باشد؛ بنابراین interval نهایی از آن انتخاب نمی‌شود. انتخاب interval روی bot/channel مستقل staging در محیط معمول Telegram و با همان trace کانال انجام می‌شود.

### 10.4 ماتریس فنی تکمیلی Bot API

این بخش مستقل از منظور محصول درباره «همه پاسخ‌ها» است، اما برای تاب‌آوری صف باقی می‌ماند:

- موفقیت واقعی `ok=true` با result متناسب method
- `HTTP 200 + ok=false` و result مفقود
- `400` و no-op edit
- `401/403/404/409`
- `429` با و بدون `retry_after`
- `5xx`
- body نامعتبر و status/envelope ناسازگار
- DNS/connect/TLS failure و timeout با نتیجه نامعلوم

خطاهای مخرب configuration با fault adapter آزمایش می‌شوند و برای تولیدشان token یا دسترسی کانال زنده خراب نمی‌شود. در دور کالیبراسیون interval نیز fault مصنوعی تزریق نمی‌شود؛ فقط پاسخ طبیعی Telegram اندازه‌گیری می‌شود.

### 10.5 گیت cross-bot و permission

این گیت پیش از هر load با editor اجرا می‌شود:

1. fingerprint `getMe`، کانال allowlisted و محیط هر دو token بدون چاپ secret تأیید می‌شود.
2. readback عضویت ثابت می‌کند primary publisher و editor فقط دارای `can_edit_messages` لازم است؛ دسترسی post/delete/promote اضافی نتیجه را fail می‌کند، مگر محدودیت صریح Telegram در manifest مستند شده باشد.
3. primary یک پیام fixture را می‌فرستد؛ canonical publisher/destination/message id در publication state ثبت می‌شود؛ editor همان پیام را یک بار با متن و markup مصوب ویرایش می‌کند.
4. observer receipt انتشار primary و edit editor را با همان message id تأیید می‌کند.
5. negativeها با adapter/preflight شامل method غیر-edit، private chat، channel اشتباه، message identity ناسازگار، token fingerprint اشتباه و revoke شبیه‌سازی‌شده‌اند و همگی باید پیش از dispatch fail-closed شوند.
6. خاموشی/429 editor job را pending نگه می‌دارد و نباید route آن را به primary تغییر دهد؛ هم‌زمان publication و پیام خصوصی primary باید ادامه یابند.

شکست هر بند، تمام اجراهای mode editor را متوقف می‌کند؛ baseline primary-only می‌تواند برای تشخیص ادامه یابد، اما editor تا اصلاح و تکرار کامل preflight فعال نمی‌شود.

## 11. خانواده اجراها

### A. پیش‌پرواز قطعی

- validation تمام ۴۰۰ ورودی نامعتبر
- state machine و idempotency
- کامل‌بودن response catalog بخش ۹
- ماتریس فنی بخش ۱۰.۴
- priority، `enqueued_seq` قطعی و newest-first/catch-up صف edit
- race معامله/انقضا
- restart و lease recovery
- authorization/routing matrix هر دو bot role، immutability، نبود fallback و secret scan

### A3. قابلیت واقعی cross-bot

- اجرای کامل گیت بخش ۱۰.۵ روی bot و کانال مستقل staging
- primary-send و editor-edit روی همان canonical message id با receiver observation
- permission readback، revoke/outage مصنوعی و اثبات ادامه مسیرهای مستقل primary
- این اجرا شرط ورود mode editor به benchmark است و به‌تنهایی اثبات افزایش ظرفیت نیست

### A2. دریافت کامل end-to-end

- اجرای `1800` آفر صحیح و `400` آفر ناصحیح از user client واقعی محیط test
- دریافت واقعی همه پیام‌ها، editها و callback answerهای catalog
- تطبیق sender، Bot API، receiver و business ledger
- این اجرا معیار انتخاب interval نیست

### B. سقف خام send

- فقط پیام‌های مستقل کانال
- trace و interval کنترل‌شده
- بدون edit یا اعلان‌های `M1/M6`
- هدف: مشاهده سقف send، نه انتخاب نهایی production
- فقط primary اجرا می‌کند و بین دو mode تفاوتی ندارد

### C. سقف خام edit

- pool از پیام‌های از قبل منتشرشده staging
- editهای متن، markup و terminal edit ترکیبی
- pool برای هر interval یکسان است تا کندی publication تعداد editها را تغییر ندهد
- با trace/seed یکسان یک‌بار primary-only و یک‌بار editor اجرا می‌شود

### D. بار ترکیبی اصلی

- `1800` آفر معتبر + `400` نامعتبر
- معامله، concurrency، انقضای دستی و خودکار
- `M0` تا `M7` و شش feeder بخش ۳ Roadmap
- trace کسب‌وکار پس از اثبات handlerها در A/A2، از staging-only workload replay به همان transaction/feeder contract وارد می‌شود؛ این replay جایگزین شاهد واقعی parser/receiver در A2 محسوب نمی‌شود.
- Telegram واقعی برای lane کانال staging و ثبت همه خروجی‌های قابل مشاهده این محیط
- مبنای انتخاب interval
- هر interval/seed در دو mode primary-only و primary+editor با همان trace اجرا می‌شود؛ ترتیب mode تصادفی است

### E. تاب‌آوری

- fault injection پاسخ‌ها و transport
- restart worker در backlog
- بررسی عدم گم‌شدن، عدم duplicate و drain پس از بازیابی
- outage، `401/403/429` و restart editor، shared destination cooldown و اثبات نبود fallback خودکار

### F. endurance گزینه منتخب

- حداقل `30` دقیقه با profile تکرارشونده و بدون تغییر قرارداد نسبی
- فقط پس از انتخاب کاندید نهایی در دورهای ده‌دقیقه‌ای

## 12. جست‌وجوی interval و تکرار

کاندیدهای اولیه Roadmap:

`0.25`, `0.30`, `0.35`, `0.40`, `0.50`, `0.65`, `0.80`, `1.00`, `1.05` ثانیه.

برای `429` مقدار خام `retry_after` بدون cap استفاده و safety margin اولیه `0.10s` ثبت می‌شود. Stage 4 می‌تواند safety margin و interval پایه را فقط با مقایسه تکرارشونده تغییر دهد؛ retry صد‌میلی‌ثانیه‌ای تا پایان `retry_after` وجود ندارد.

روش قطعی:

1. غربال اولیه همه intervalها با سه seed ثابت، ترتیب تصادفی و اجرای جفت‌شده primary-only/primary+editor برای هر trace.
2. انتخاب سه گزینه برتر بر اساس goodput، 429، backlog و drain.
3. تکرار هر گزینه نهایی حداقل سه بار برای هر seed.
4. اجرای endurance برای گزینه منتخب.
5. cooldown کافی پس از هر دور، به‌خصوص پس از `429`.

goodput شامل cooldown و زمان drain است:

```text
goodput = terminally_delivered_jobs / total_wall_clock_including_cooldown_and_drain
```

کوتاه‌ترین interval فقط وقتی انتخاب می‌شود که در تکرارها goodput پایدارتر، retry کمتر و backlog قابل کنترل‌تری داشته باشد.

## 13. معیار پذیرش

### 13.1 ورودی و اعتبارسنجی

- دقیقاً `1800` آفر معتبر پذیرفته شود.
- دقیقاً `400` تلاش نامعتبر رد شود.
- هیچ تلاش نامعتبر Offer یا publication intent نسازد.
- تمام quotaهای کالا، buy/sell، تسویه و شکل لات برقرار باشند.
- هر شاخه پاسخ validation در response catalog حداقل یک receipt واقعی داشته باشد.
- حداقل `70` owner یکتا در هر دور مشارکت کنند، هیچ owner از soft guard یعنی `10` آفر فعال عبور نکند و هیچ پذیرش معتبر به‌علت quota کاربر رد نشود.

### 13.2 صحت کسب‌وکار

- هیچ quantity باقیمانده منفی نشود.
- هیچ over-trade یا duplicate trade وجود نداشته باشد.
- هر درخواست هم‌زمان یک نتیجه نهایی قابل ردیابی داشته باشد.
- هر آفر فقط یک terminal transition معتبر داشته باشد.
- آفر منقضی یا تکمیل‌شده دیرهنگام منتشر نشود.

### 13.3 صحت Telegram و صف

- همه jobهای ledger به یک وضعیت نهایی مجاز برسند.
- همه پاسخ‌های طبیعی catalog که انتظار ارسال دارند در receiver واقعی مشاهده شوند.
- `missing_receipt = 0` و duplicate مشاهده‌شده خارج از قرارداد `0` باشد.
- هیچ `429` مستقیماً job را `FAILED` یا Offer را منقضی نکند؛ اگر عمر طبیعی Offer هنگام انتظار تمام شد، publication فقط طبق freshness به `SUPERSEDED/DISABLED` برود و stale ارسال نشود.
- تمام retryها `retry_after + safety_margin` را رعایت کنند.
- timeout نامعلوم duplicate کنترل‌نشده نسازد.
- terminal marker و حذف دکمه کانال یک API call باشند.
- deadlineهای `M0` و عملیات `M1` تا `M4` زیر بار `M6/M7` گرسنه نمانند.
- ترتیب transfer میان شش feeder و ترتیب داخلی هر feeder با matrix بخش ۳ Roadmap یکسان باشد.
- پس از هر ۲۰ edit تازه موفق، در صورت وجود stale edit واجد ارسال، نوبت بعدی feeder edit به یک stale edit برسد، بدون عبور از priority اصلی یا cooldown.
- هر broadcast فقط یک گیرنده in-flight و مجموع broadcastهای `M6` حداکثر دو گیرنده متعلق به campaignهای متفاوت داشته باشند؛ round-robin و توقف مستقل campaign در ledger اثبات شود.
- `100%` آفرهای واجد انتشار پیش از `expires_at-5s` پاسخ موفق Telegram بگیرند، `DISABLED` صرفاً ناشی از backlog صفر باشد و latency publication به `p95<=10s` و `p99<=30s` برسد؛ نقض هرکدام نتیجه release را `NO-GO` می‌کند.
- callbackهای `M0` به `p95<=1s` و `p99<=2s` برسند؛ callback عبورکرده از deadline ارسال نشود و `EXPIRED_INTERACTION` ثبت کند.
- کاربر authenticated پس از success/error/cancel/timeout و restart بدون `/start` به منوی persistent مناسب دسترسی داشته باشد و anchor فعال خودکار حذف نشود.
- `HTTP 200 + ok=false` موفقیت محسوب نشود.
- send موفق بدون message id ثبت نشود.
- primary تنها bot مجاز برای send/private/callback باشد و editor فقط editهای allowlisted کانال را روی canonical message id اجرا کند.
- در mode editor، cross-bot receipt کامل، method/destination mismatch برابر fail-closed و fallback یا بازنویسی bot job برابر صفر باشد.
- outage/revoke/429 editor publication و پیام‌های خصوصی primary را متوقف نکند؛ editها بدون دورزدن cooldown یا تغییر route پایدار بمانند.
- هر دو mode معیار publication/SLO یکسان را پاس کنند؛ فعال‌سازی editor فقط با بهبود تکرارپذیر goodput/backlog و بدون افزایش duplicate/stale/429 مجاز است. در غیر این صورت نتیجه mode برابر primary-only است.

### 13.4 سنجه‌های اجباری

- نرخ attempt، accepted و rejected
- goodput به تفکیک method، priority و destination class
- goodput، attempt، outcome، latency و `429` به تفکیک `bot_role=primary|channel_editor` بدون bot id یا token واقعی
- cooldown و backlog lane مشترک channel destination در کنار budget جداگانه هر bot role
- p50/p95/p99 تأخیر queue و delivery
- backlog در ثانیه ۶۰۰
- سن قدیمی‌ترین job
- زمان drain
- تعداد و درصد `429`
- توزیع `retry_after`
- 4xx/5xx/transport/unknown response counts
- retry count و retry success
- duplicate prevented و duplicate observed
- تعداد `DISABLED` پیش از publication
- تعداد send، edit، callback و اعلان معامله واقعی به تفکیک `M0` تا `M7`، feeder و response catalog id
- زمان Bot API acceptance تا receiver observation
- تفاوت ledger مورد انتظار با outbox، Telegram result و receiver receipt

## 14. ایزوله‌سازی و cleanup

- tokenهای primary/editor و channel id staging پیش از اجرا با production فقط از نظر نابرابری و fingerprint مقایسه می‌شوند؛ مقدار secret در DB، manifest، artifact یا log چاپ نمی‌شود.
- editor فقط عضو همان کانال staging و فقط دارای permission لازم برای edit است؛ هیچ مقصد production در allowlist هیچ‌یک از دو role نیست.
- اجرای Telegram فقط روی foreign staging مجاز است.
- تمام رکوردهای تست prefix و `run_id` مشخص دارند.
- cleanup در پنجره اندازه‌گیری اجرا نمی‌شود.
- حذف پیام‌ها پس از cooldown انجام و از metrics کنار گذاشته می‌شود.
- اگر cleanup نیاز به deleteهای متعدد دارد، همه آن‌ها با `M7` از صف اصلی و limiter مشترک عبور می‌کنند و پس از drain/cooldown دور اندازه‌گیری اجرا می‌شوند.
- production DB، کانال production و کاربران production هیچ mutation یا پیام آزمایشی دریافت نمی‌کنند.

## 15. خروجی هر دور

هر اجرا باید این artifactها را بسازد:

- manifest شامل seed، trace hash، interval، config runtime و fixture hash
- mode، fingerprint غیرحساس bot roleها، permission readback و routing policy hash
- event trace ورودی
- business outcome ledger
- Telegram request/response ledger با secrets حذف‌شده
- queue metrics time series
- reconciliation report
- acceptance report با وضعیت pass/fail هر معیار
- خلاصه مقایسه با دورهای همان seed

artifact خام دارای داده حساس commit نمی‌شود. فقط گزارش پاک‌سازی‌شده و قرارداد سناریو می‌تواند وارد Git شود.

## 16. پیش‌نیازهای Stage 4

- پیاده‌سازی sampler read-only و redaction test
- پیاده‌سازی generator seedدار و quota validator
- آماده‌سازی sender/receiver harness واقعی و response catalog استخراج‌شده از کد
- آماده‌سازی bot، کانال و pool کاربران Test DC برای تست کامل دریافت پاسخ‌ها، پس از تأیید زیرساخت test
- fault adapter برای ماتریس فنی بخش ۱۰.۴
- پیاده‌سازی ledger و reconciliation مستقل
- feature flag پیش‌فرض خاموش
- آماده‌سازی دو bot مستقل staging: primary ناشر/تعامل و channel-editor فقط ویرایشگر؛ هر دو جدا از production
- credential registry، destination/method allowlist، permission/fingerprint preflight و secret scan
- تأیید smoke واقعی primary-send/editor-edit و observer receipt پیش از ورود editor به load
- امکان اجرای baseline primary-only با همان schema و trace، بدون بازنویسی jobهای موجود یا fallback
- تأیید bot/channel مستقل staging
- ثبت `max_active_offers=10` فقط در Iran staging و تأیید sync/cache آن در foreign staging
- guard خودکار که default و production را همچنان `4` تأیید کند
- تأیید runtime expiry دو دقیقه و سایر limitهای کاربران
- تست محلی کامل پیش از اولین پیام زنده staging
