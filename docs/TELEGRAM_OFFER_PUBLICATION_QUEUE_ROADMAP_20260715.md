# Roadmap صف پایدار انتشار آفر در تلگرام

## وضعیت سند

- تاریخ ایجاد: `2026-07-15`
- شاخه اختصاصی: `candidate/telegram-offer-publication-queue`
- مبنای شاخه: `main@ca6348af`
- وضعیت Roadmap: Stage 0 و Stage 1 ثبت شده‌اند. ممیزی closure در `2026-07-16` تمام تصمیم‌های اولیه challenge register را بست؛ شش ADR اولیه و قرارداد pure نهایی `M0` تا `M7` ابتدا با `44` تست قرارداد و `171` تست هدفمند/regression پاس شدند. همان روز ADR هفتم برای `channel_editor` پذیرفته و برش foundation مرحله ۳ ممیزی شد: پس از merge آخرین `main`، `316` تست unit/regression و `20` تست PostgreSQL واقعی، همراه upgrade→downgrade→upgrade migration پاس شدند. طراحی scheduler برای حذف head-of-line blocking بین ظرفیت primary و editor اصلاح و foundation دو execution lane مستقل و work-conserving آن پیاده شد؛ credential registry بدون fallback، limiter پایدار Redis، preflight هویت/کانال/permission، canonical publisher identity، freshness authoritative خانواده publication/edit آفر و اعلان بازار، handoff/feedback اتمیک نتیجه خصوصی معامله و broadcast مدیریتی، bridge منبع‌محور اعلان `project_user_joined` و bridge مستقل اعلان باز/بسته‌شدن بازار نیز پیاده شده‌اند. در `2026-07-18` شش کامیت قابلیت «تکرار آخرین آفر» از `main` به‌صورت انتخابی و بدون merge سراسری منتقل و دو پاسخ واقعی `sendMessage` آن به bridge منبع‌محور صف افزوده شد. در checkpoint بعدی همان روز، قرارداد و adapter منبع‌محور نه action خصوصی دیگر افزوده و مسیر واقعی `account_status` به outbox پایدار متصل شد؛ سپس `offer_success` با receipt اتمیک همان transaction ثبت آفر و edit منبع‌محور preview تکمیل شد. در برش بعد، ingress مستقیم و deadlineدار `answerCallbackQuery` برای `callback_deadline` و `offer_expiry_callback` تکمیل و تمام پاسخ‌های callback انقضای دستی در queue mode به آن متصل شدند. در checkpoint پنج action پایانی، `noncritical_market`, `timed_security`, `delayed_restriction`, `temporary_cleanup`, `cosmetic_cleanup` نیز source/freshness/lifecycle معتبر گرفتند؛ gap پوشش primary از `5` به `0` رسید و head migration به `faf6a7b8c9d0` رفت. ownership و state machine همچنان یکی است. runtime صف هنوز code-disabled و Stage 3 کامل نیست؛ هیچ deploy انجام نشده و فعال‌سازی منوط به تکمیل reconciliation عملیاتی، ممیزی/cutover باقی callsiteهای مستقیم، readback زنده و smoke/benchmark Stage 4 است. reconciliation تا `main@2c08da14` کامل است: patch همان تغییر بازیابی state ثبت آفر با patch-id معادل در کامیت کاندید `b51c46c8` وجود دارد و دو پاسخ Telegram افزوده‌شده آن در queue mode نیز به ingress مصوب متصل شده‌اند.
- checkpoint تکمیلی `2026-07-18`: producer اتمیک publication/edit آفر، قطع مسیر مستقیم کانال در queue mode، یک‌پیامه‌شدن success خصوصی Bot، terminal edit تک‌درخواستی، refresh منبع‌محور دکمه stale و coverage کامل lane ادیتور پیاده شد. fairness صف edit شامل حفظ سن اولین enqueue در coalescing، stale-tail پنج‌دقیقه‌ای و catch-up پایدار یک‌به‌بیست با state اتمیک PostgreSQL و policy صریح `NO_SYNC` تکمیل شد؛ head migration اکنون `faf6a7b8c9d0` است. router مرکب lifecycle و composition منبع‌محور نیز اضافه شد. runtime عمداً خاموش است؛ coverage منبع‌محور primary کامل است، اما این تکمیل به‌تنهایی مجوز فعال‌سازی یا اثبات cutover تمام callsiteهای مستقیم نیست.
- checkpoint ممیزی callsite در `2026-07-18`: اسکن AST قطعی `scripts/audit_telegram_delivery_calls.py` تمام مرزهای Telegram در `api/`, `bot/`, `core/` را با disposition و خروجی JSON ثبت می‌کند و `--check` هم callsite طبقه‌بندی‌نشده و هم رشد هر دسته باقی‌مانده نسبت به سقف بازبینی‌شده را رد می‌کند. مرز نهایی publication آفر و helperهای مستقیم قدیمی trade/core-utils در queue ownership fail-closed شدند. پیام راهنمای state قدیمی ثبت آفر از action `offer_validation_response` و پاسخ دکمه قدیمی آن از ingress مستقیم `callback_deadline` عبور می‌کنند. این checkpoint با `51` تست هدفمند پاس شد، اما اعداد inventory زیر ثابت می‌کنند Stage 3 هنوز معیار خروج ندارد.
- checkpoint account-control در `2026-07-18`: هر شش مرز `remaining_business_direct` بسته شدند. اعلان اعمال block/limitation با snapshot دقیق restriction، sync version، expiry و intent هم‌تراکنش با mutation کاربر از همان action `ACCOUNT_STATUS/M5` عبور می‌کند؛ اعلان حذف حساب محلی و sync با snapshot حذف authoritative و route پیش از حذف صفی شد و اگر آن route به حساب دیگری متصل شود پیش از handoff یا dispatch supersede می‌شود. تونل legacy notification نیز در هر دو سر sync فقط envelope دقیق OTP پنج‌رقمی جاری را می‌پذیرد و پیام عمومی را fail-closed رد می‌کند. schema یا migration جدیدی لازم نبود و runtime همچنان خاموش است.
- checkpoint نخست callback در `2026-07-18`: ممیزی AST اصلاح شد تا `callback.message.answer` با `answerCallbackQuery` اشتباه نشود؛ baseline معنایی واقعی `260` callback deadlineدار و `344` interaction پیام‌ساز است. middleware جدید زمان دریافت callback را در اولین مرز dispatcher و پیش از Auth/DB در ContextVar ایزوله ثبت و در `finally` پاک می‌کند. adapter مشترک در legacy شکل فراخوانی aiogram را حفظ و در queue mode بدون receipt لبه fail-closed می‌شود. دو outcome صفحه‌بندی کاتالوگ کالا نخستین خانواده‌ای هستند که از همین adapter به ingress مستقیم `CALLBACK_DEADLINE/M0` منتقل شدند؛ inventory جاری callback را به `258` رساند.
- checkpoint دوم callback در `2026-07-18`: هر `17` پاسخ callback خانواده تاریخچه معامله، شامل نمایش/فیلتر/بازگشت پروفایل، کنترل دسترسی و acknowledgement ساخت Excel/PDF، به همان adapter منتقل شد. پیام‌های جدید، editها و ارسال فایل این خانواده عمداً در دسته interaction باقی ماندند تا قرارداد result/anchor مرحله بعد دور زده نشود. inventory callback به `241` کاهش یافت و `44` تست کامل تاریخچه/خروجی بدون تغییر رفتار legacy پاس شدند.
- checkpoint سوم callback در `2026-07-18`: هشت پاسخ ساخت/لغو دعوت‌نامه مدیریتی و شانزده پاسخ منو، جست‌وجو، block/unblock و کنترل delegation به adapter مشترک منتقل شدند. mutationهای invitation/block و edit/messageهای همراه تغییری نکردند. `26` تست دامنه این دو خانواده پاس و inventory callback به `217` کاهش یافت.
- checkpoint چهارم callback در `2026-07-18`: هر `17` پاسخ جریان انتخاب گیرنده، گروه، تایید، لغو و validation پیام همگانی مدیریتی به adapter مشترک منتقل شد. coordinator/receipt اصلی broadcast، FSM، editها و پیام‌های ورودی تغییری نکردند. `30` تست handler/service/feeder/delivery/freshness سبز و inventory callback به `200` کاهش یافت.
- checkpoint پنجم callback در `2026-07-18`: دو پاسخ pre-auth contention برای تایید ضربه دوم و busy بودن آفر کانال از direct aiogram خارج شدند. چون `CallbackReceiptMiddleware` پیش از خود gate ثبت شده است، حتی این پاسخ‌های قبل از Auth نیز deadline را از اولین مرز update می‌گیرند. `17` تست gate/runtime/adapter/middleware پاس و inventory callback به `198` کاهش یافت.
- checkpoint ششم callback در `2026-07-18`: هر `22` پاسخ deadlineدار اجرای معامله کانال، شامل guardهای کاربر/آفر، تایید ضربه دوم، پیشنهاد لات جایگزین، موفقیت و خطای local و remote-home، به adapter مشترک منتقل شد. ثبت موفق local پیش از ساخت job پاسخ callback به‌صورت authoritative commit می‌شود و مسیر remote-home نیز فقط پس از پاسخ موفق سرور مالک acknowledgement می‌سازد؛ session کوتاه مستقل adapter هیچ transaction خواندنی handler را ناخواسته commit نمی‌کند. `30` تست اختصاصی اجرای معامله پاس و inventory callback به `176` کاهش یافت.
- checkpoint هفتم callback در `2026-07-18`: هر `25` پاسخ مسیرهای start شامل ثبت‌نام مستقیم، ویرایش آدرس، onboarding، مسیر تایید قدیمی معامله و انصراف به ingress مشترک منتقل شد. ثبت intent و onboarding موفق پیش از acknowledgement صف به‌صورت authoritative commit می‌شوند؛ پاسخ‌های رد نیز session دامنه را commit نمی‌کنند و adapter session مستقل خود را دارد. پیام‌های همراه ثبت‌نام، ویرایش tutorial و reply keyboard در دسته interaction باقی ماندند. `31` تست start پاس و inventory callback به `151` کاهش یافت.
- checkpoint هشتم callback در `2026-07-18`: هر `33` پاسخ پنل کاربر و مدیریت، شامل block list، مشتریان، دعوت سطح یک/دو، navigation و تنظیمات مدیریتی، به adapter مشترک منتقل شد. mutation رفع block و reset تنظیمات پیش از acknowledgement موفق انجام می‌شوند؛ پیام‌ها و editهای پنل در دسته interaction باقی مانده‌اند. `30` تست اختصاصی panel پاس و inventory callback به `118` کاهش یافت.
- checkpoint نهم callback در `2026-07-18`: هر `56` bypass واقعی wizard ثبت آفر، شامل نوع/تسویه/کالا/مقدار/لات، ویرایش، بازگشت، لغو، stale preview و flow آفر متنی به adapter مشترک منتقل شد. یازده callback داخل `_handle_trade_confirm_core` و helper stale قدیمی که پیش‌تر در queue mode شاخه مستقیم را می‌بستند عمداً به‌عنوان `legacy_mode_guarded` باقی ماندند تا ownership و transaction اتمیک publication دور زده نشود. `85` تست کامل `trade_create` پاس و inventory callback به `62` کاهش یافت.
- checkpoint دهم callback در `2026-07-18`: هر `62` پاسخ مستقیم مدیریت کاربران، شامل فهرست/پروفایل، role/status، هدایت حذف، ساخت محدودیت، تنظیم block و guardهای authority، به adapter مشترک منتقل شد. شش پاسخ unblock/unlimit که از checkpoint account-control در queue mode transaction و intent اختصاصی دارند عمداً `legacy_mode_guarded` باقی ماندند. همه mutationهای موفق این خانواده پیش از acknowledgement commit می‌شوند. `50` تست کامل `admin_users` پاس و `remaining_callback_direct` به صفر رسید.
- foundation نتیجه/anchor interaction در `2026-07-18`: قرارداد pure میان target با `message_id` واقعی و target وابسته به receipt ارسال تفکیک ایجاد کرد. pending و send مبهم منتظر dependency/reconciliation می‌مانند، شکست terminal وابستگی را supersede و `SENT` بدون message id واقعی را quarantine می‌کند. anchor فقط با `sendMessage` خصوصی authenticated، منوی persistent، capture اجباری message id و generation مثبت ساخته می‌شود؛ نتیجه دیررس id واقعی را ثبت می‌کند اما نسل جدیدتر anchor را جایگزین نمی‌کند. `sendDocument` تا طراحی ذخیره/retention فایل عمداً fail-closed و خارج cutover است. `65` تست قرارداد جدید و queue موجود پاس شدند؛ schema، inventory و runtime تغییری نکردند.
- checkpoint persistence نتیجه/anchor interaction در `2026-07-18`: outbox خصوصی موجود به‌عنوان receipt بادوام نتیجه `sendMessage` استفاده می‌شود و جدول foreign-local/`NO_SYNC` جدید `telegram_interaction_anchor_states` نسل مطلوب و anchor فعال هر chat را جدا نگه می‌دارد. تخصیص نسل زیر advisory lock تراکنشی، اتصال receipt و desired anchor در همان transaction و فعال‌سازی نتیجه همراه terminal شدن job/outbox انجام می‌شود. فقط نتیجه `SENT` با `message_id` واقعی و دقیقاً منطبق با نسل/receipt/logical key جاری فعال می‌شود؛ نسل قدیمی پیش از dispatch supersede و نتیجه دیررس هرگز anchor جدیدتر را بازنویسی نمی‌کند. relink هرگز intent لنگر chat قبلی را به chat جدید بازهدف‌گذاری نمی‌کند و persistent menu بدون metadata anchor در outbox عمومی رد می‌شود. migration افزایشی `fb07b8c9d0e1` هم رفت‌وبرگشت `fb07 → faf6 → fb07` و هم upgrade کامل دیتابیس scratch خالی را پاس کرد؛ `44` تست واحد، `5` تست PostgreSQL اختصاصی و `30` تست PostgreSQL regression bridge سبز شدند. suite گسترده Telegram نیز `564` تست با `150` skip محیطی را پاس کرد. هیچ callsite پیام‌ساز cutover نشده، inventory روی `remaining_interactive_direct=344` و runtime روی `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` باقی است.
- checkpoint نخست cutover پیام‌ساز interaction در `2026-07-18`: adapter محدود `answer_incoming_message_via_runtime` برای پاسخ به پیام ورودی authenticated افزوده شد. legacy همان `message.answer` و همان آرگومان/نتیجه را حفظ می‌کند؛ queue mode بدون انتظار provider فقط source id یکتای مبتنی بر پیام ورودی و user، route خصوصی منطبق، sync version، متن و markup canonical را در outbox پایدار ثبت می‌کند. استفاده‌ی callerهایی که به `Message` فوری نیاز دارند عمداً ممنوع و به dependency-result مرحله بعد موکول است. چهار پاسخ بدون return/dependency در جست‌وجوی block-management، شامل ورودی کوتاه، حساب delegated، نتیجه خالی و فهرست نتیجه، به `GENERAL_IMMEDIATE/M1` منتقل شدند. inventory جاری `remaining_interactive_direct=340` و total=`443` است. `14` تست کامل خانواده block، `49` تست adapter/contract/inventory، `6` تست PostgreSQL interaction و suite گسترده `570` تست Telegram با `152` skip محیطی پاس شدند. schema/head تغییر نکرد، runtime خاموش است و هیچ merge/deploy/push یا Telegram زنده انجام نشد.
- checkpoint دوم cutover پیام‌ساز interaction در `2026-07-18`: هر شش `message.answer` بدون return/dependency در flow کنترل پیام همگانی مدیریتی به همان adapter منتقل شدند: منوی شروع، نتیجه جست‌وجوی گیرنده، متن خالی/بلند، گیرندگان نامعتبر و preview تأیید. این پیام‌های کنترلی `GENERAL_IMMEDIATE/M1` هستند؛ خود campaign و receiptهای کاربران همچنان صف تابع `ADMIN_BROADCAST/M6` خود را دارند و هیچ اولویتی تغییر نکرد. ده edit/markup callback این فایل برای برش edit شناخته‌شده باقی ماندند. `49` تست خانواده admin-broadcast با `11` skip PostgreSQL محیطی، `10` تست adapter/inventory و `570` تست گسترده Telegram با `152` skip پاس شدند. inventory جاری total=`437` و `remaining_interactive_direct=334` است؛ schema، head و runtime تغییری نکردند.
- checkpoint سوم cutover پیام‌ساز interaction در `2026-07-18`: ده پاسخ خطای ترمینال سازنده آفر، شامل عدم دسترسی، restriction، بسته‌بودن بازار و validation تعداد/لات/قیمت/توضیحات، با source key مستقل به صف تابع کنترل آفر و action رسمی `OFFER_VALIDATION_RESPONSE/M1` منتقل شدند. هر مسیر بلافاصله return می‌کند و هیچ `message_id`، anchor یا edit بعدی به نتیجه آن وابسته نیست. پیام‌های مدیریت کالا و دعوت که نتیجه‌شان لنگر/ویرایش می‌شود یا با پنل بعدی ترتیب دارند عمداً برای dependency/order contract باقی ماندند. `37` تست متمرکز، هر `88` تست `trade_create` و suite گسترده `570` تست Telegram با `152` skip پاس شدند. inventory جاری total=`427` و `remaining_interactive_direct=324` است؛ schema، head، runtime و Telegram زنده تغییری نکردند.
- checkpoint چهارم cutover پیام‌ساز interaction در `2026-07-18`: سه پاسخ تک‌پیامی و مستقل پنل، یعنی خلاصه تنظیمات کاربری، placeholder تنظیمات ساده و اطلاعات پشتیبانی، به `GENERAL_IMMEDIATE/M1` منتقل شدند. inline keyboard تنظیمات در payload canonical حفظ می‌شود، هیچ persistent keyboard یا anchor جدیدی تولید نمی‌شود و کیبورد فعال کاربر حفظ می‌گردد. `31` تست کامل panel و `570` تست گسترده Telegram با `152` skip پاس شدند؛ inventory جاری total=`424` و `remaining_interactive_direct=321` است.
- checkpoint پنجم cutover پیام‌ساز interaction در `2026-07-18`: هفت پاسخ پایان‌دهنده دیگر پنل شامل قفل حساب، عدم مجوز لیست همکاران، خالی‌بودن تاریخچه اخیر، خطای ساخت PDF، مقدار نامعتبر تنظیم و رد authority به `GENERAL_IMMEDIATE/M1` منتقل شدند. خود `answer_document` عمداً باقی ماند، چون فایل موقت در `finally` پاک می‌شود و صفی‌سازی آن بدون storage/retention contract منجر به job بدون فایل می‌شود. `32` تست panel و `570` تست گسترده Telegram با `152` skip پاس شدند؛ inventory جاری total=`417` و `remaining_interactive_direct=314` است.
- checkpoint ششم cutover پیام‌ساز interaction در `2026-07-18`: سه منوی persistent اصلی پنل کاربر، پروفایل و پنل مدیریت اولین handlerهایی شدند که از `SET_CURRENT/CAPTURE_MESSAGE_ID` پایدار استفاده می‌کنند. در queue mode لنگر قبلی پیش از تحویل موفق منوی جدید حذف نمی‌شود و فقط feedback `SENT` با message id واقعی لنگر جدید را فعال می‌کند؛ legacy همچنان delayed cleanup و `set_anchor` حافظه‌ای فعلی را دارد. `33` تست panel، هر شش تست PostgreSQL anchor و `570` تست گسترده Telegram با `152` skip پاس شدند؛ دیتابیس scratch پاک شد و inventory جاری total=`414` و `remaining_interactive_direct=311` است.
- checkpoint هفتم cutover پیام‌ساز interaction در `2026-07-18`: adapter مستقل `answer_callback_message_via_runtime` افزوده شد تا پیام جدید ناشی از callback را با identity هش‌شده و update-specific ثبت کند. raw `callback.id` هرگز وارد source/logical key نمی‌شود، دو callback روی یک message collision ندارند و identity ناقص پیش از persistence fail-closed می‌شود. پیام اولیه تاریخچه و شش پاسخ empty/error خروجی Excel/PDF به `TRADE_NONCRITICAL/M5` منتقل شدند؛ خود `sendDocument` و فایل موقت تغییری نکردند. `30` تست تاریخچه، `13` تست adapter/inventory و `573` تست گسترده Telegram با `152` skip پاس شدند؛ inventory جاری total=`408` و `remaining_interactive_direct=304` است.
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
- صف پایدار، ownership، retry و state machine یکی است؛ اجرای آن دو claim lane هم‌زمان و work-conserving برای `primary` و `channel_editor` دارد. backlog یا priority بالاتر در lane بات اصلی، ظرفیت آزاد editor را رزرو یا مسدود نمی‌کند.
- gate نرخ/cooldown مقصد کانال فقط در جایی میان دو lane مشترک است که محدودیت واقعی همان مقصد یا شاهد cross-bot آن را ایجاب کند. این gate جایگزین dispatcher سراسری نیست؛ پیام خصوصی/مدیریتی primary هرگز editor را پشت صف خود نگه نمی‌دارد. جداسازی کامل gate یا افزایش rate فقط با شاهد Stage 4 مجاز است و وجود دو token به‌تنهایی اثبات افزایش ظرفیت کانال نیست.
- editor در کانال کمترین دسترسی لازم `can_edit_messages` را دارد و preflight باید نبود دسترسی‌های post/delete/promote اضافی را تا حدی که Telegram اجازه می‌دهد تأیید کند.
- Paid Broadcast راه‌حل این چالش در نظر گرفته نمی‌شود.
- هر آفر یک پیام مستقل در کانال دارد.
- انتشار دسته‌ای چند آفر در یک پیام رد شده است.
- ثبت آفر از اجرای side effect تلگرام جدا می‌شود.
- پس از ثبت پایدار آفر، کاربر از صف، `429` یا retry مطلع نمی‌شود. WebApp همان رفتار فعلی `main` یعنی بستن preview، پاک‌کردن draft و refresh بازار بدون success toast جدید را حفظ می‌کند.
- در Bot، اطلاعات دو پیام فعلی با یکدیگر ادغام می‌شوند: همان پیام preview/تأیید با یک `editMessageText` به متن موفقیت همان مسیر به‌اضافه بلوک «لفظ شما» و دکمه انقضا تبدیل می‌شود و `bot.send_message` خصوصی دوم حذف خواهد شد. `answerCallbackQuery` همچنان درخواست جدا و ضروری `M0` است؛ بنابراین عملیات محتوایی از دو به یک و پیام قابل‌مشاهده نهایی از دو به یک کاهش می‌یابد.
- قابلیت «تکرار آخرین آفر» یک ردیف به Reply Keyboard می‌افزاید و خود ردیف درخواست Bot API جدا نیست. بااین‌حال refresh منو با متن `منو با آخرین وضعیت به‌روزرسانی شد` و پاسخ دکمه قدیمی، هرکدام یک `sendMessage` خصوصی واقعی با Reply Keyboard جاری هستند و باید از action منبع‌محور `OFFER_REPEAT_RESPONSE` در `M1` عبور کنند؛ مسیر queue حق fallback مستقیم ندارد.
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
- ترتیب `M0` تأییدشده در shared queue عبارت است از callback deadlineدار، سپس اعلان معامله عبورکرده از پنج ثانیه و سپس انتشار آفر جدید. کد ورود محرمانه در مسیر تخصصی امضاشده و Redis کوتاه‌عمر فعلی می‌ماند و در PostgreSQL صف ذخیره نمی‌شود.
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

عدد کمتر در هر دو سطح به معنی اولویت بالاتر است. اولویت داخلی فقط مشخص می‌کند هر صف تابع کدام کار آماده خود را معرفی کند؛ اولویت `M0` تا `M7` ترتیب نهایی کارهای رقیب برای همان execution lane یا منبع مشترک را تعیین می‌کند. این اولویت یک قفل سراسری میان دو bot role نیست: وجود jobهای `M0` primary نباید claim و اجرای job واجد ظرفیت editor را متوقف کند.

### 3.1 اولویت صف اصلی

| اولویت | ترتیب میان کارهای هم‌سطح |
| --- | --- |
| `M0` | callback deadlineدار؛ اعلان معامله عبورکرده از پنج ثانیه؛ انتشار آفر جدید |
| `M1` | تبدیل preview به پیام موفقیت؛ اعلان عادی دو طرف معامله پیش از پنج ثانیه؛ پاسخ آفر نامعتبر/بازار بسته/محدودیت؛ پاسخ و refresh منوی «تکرار آخرین آفر»؛ سایر پاسخ‌های فوری بات |
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
| `5` | پاسخ درخواست تکراری یا آفر دیگر فعال‌نیست؛ refresh منوی «تکرار آخرین آفر» و پاسخ دکمه قدیمی آن | `M1` |
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

- callback deadlineدار و پاسخ تعاملی فوری مستقیماً وارد `M0` می‌شوند.
- OTP همان رفتار فعلی `main` را حفظ می‌کند: فرمان امضاشده، receipt کوتاه‌عمر Redis و بدون persistence کد در outbox یا `telegram_delivery_jobs`. مقدار enum آن فقط برای سازگاری mixed-version رزرو است و enqueue پایدار باید آن را رد کند.
- onboarding، اتصال حساب، منو و پاسخ فوری غیر deadlineدار وارد `M1` می‌شوند.
- reconciliation/ambiguous یک control plane است و صف ارسال جدا محسوب نمی‌شود.
- `429` اولویت را تغییر نمی‌دهد؛ همان job با `retry_after + safety_margin` می‌ماند.
- cooldown یک مقصد به‌تنهایی destination آماده دیگر را block نمی‌کند؛ استثنا فقط single-probe همان bot پس از 429 اول، cooldown اثبات‌شده bot-wide پس از 429 مقصد متمایز دوم، یا gate واقعاً مشترک مقصد است.
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

scheduler یک control plane و state machine مشترک، اما دو execution lane هم‌زمان دارد:

- lane `primary` فقط jobهای `bot_identity=primary` را claim می‌کند.
- lane `channel_editor` فقط jobهای `bot_identity=channel_editor` را claim می‌کند.
- هر lane limiter، semaphore/in-flight و claim loop خود را دارد و work-conserving است؛ backlog، sleep، retry یا priority lane دیگر ظرفیت آن را اشغال نمی‌کند.
- هر دو lane از همان repository، lease، fencing، retry classifier، audit و feedback مشترک استفاده می‌کنند و execution owner آن‌ها همچنان `queue-v1` است.

کنترل dispatch سه سطح مرتبط دارد:

1. بودجه مستقل هر `bot_identity`/token
2. gate مقصد، به‌خصوص همان کانال آفر، فقط در بازه‌ای که pacing/cooldown مشترک واقعاً لازم است
3. circuit-break سراسری gateway/config که هر دو credential را fail-closed متوقف می‌کند

scheduler باید resource-aware باشد: priority فقط میان jobهایی داوری می‌کند که برای همان lane و منبع محدودشده رقیب‌اند. job با priority بالاتر در primary مجاز نیست worker یا token budget editor را بیکار نگه دارد. gate محافظه‌کارانه مقصد کانال می‌تواند dispatch کانالی هر دو lane را تا موعد pacing/`retry_after` نگه دارد، اما claim/preparation و کارهای مقصدهای دیگر را متوقف نمی‌کند. افزایش worker یا افزودن editor ظرفیت همان مقصد را خودکار افزایش نمی‌دهد و ارسال موازی به کانال فقط با شاهد Stage 4 مجاز است. metric باید زمان انتظار ناشی از `bot_lane` را از `destination_gate` جدا کند تا head-of-line blocking نرم‌افزاری با محدودیت واقعی Telegram اشتباه نشود.

### 6.3.1 routing بات اصلی و ویرایشگر

- `primary` تنها ناشر پیام آفر و مالک تمام مقصدهای private/admin است.
- `channel_editor` فقط jobهایی را می‌پذیرد که destination آن کانال allowlisted، method آن edit allowlisted و canonical message identity آن متعلق به publication معتبر primary باشد.
- routing پیش از claim قطعی است و پس از `LEASED` تغییر نمی‌کند.
- credential resolver فقط روی foreign و بر پایه identity allowlisted عمل می‌کند؛ token هرگز در payload یا job قرار نمی‌گیرد.
- editor execution owner، صف پایدار، feeder دامنه‌ای یا API polling مستقل ندارد؛ سرویس main queue یک claim/execution lane مستقل برای editor و یک lane مستقل برای primary اجرا می‌کند و هر دو از همان state machine و repository استفاده می‌کنند.
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

صف اصلی Telegram تنها execution control plane است: claim نهایی، اولویت `M0` تا `M7`، بودجه bot/destination، اجرای Bot API، `retry_after`، خطاهای transport، lease ارسال و ثبت پاسخ Telegram فقط در این لایه انجام می‌شوند. این control plane برای منابع غیرقابل‌جایگزین primary و editor دو lane اجرایی مستقل دارد و یک dispatcher تک‌اسلاتی سراسری نیست.

تعداد credential این اصل را تغییر نمی‌دهد: primary و editor دو claim/execution lane زیر همان execution owner `queue-v1` هستند، نه دو state machine، retry owner یا صف پایدار مستقل.

صف تابع یک scheduler دوم Telegram نیست. این صف فقط منبع حقیقت و coordinator دامنه است و مسئول انتخاب business-ready job، ترتیب یا همگرایی داخلی، freshness، dependency، fan-out و مشاهده نتیجه صف اصلی است. هیچ صف تابعی حق sleep برای نرخ Telegram، retry فنی مستقل یا تماس مستقیم با Bot API ندارد.

قرارداد handoff:

1. هر رکورد تابع با dedupe key پایدار، نوع مبدأ، شناسه مبدأ و نسخه state به دقیقاً یک job منطقی صف اصلی نگاشت می‌شود.
2. ثبت handoff و رابطه `child_record -> main_job` باید اتمیک یا با outbox قابل‌بازیابی باشد؛ crash میان این دو نباید job را گم یا تکراری کند.
3. نتیجه صف اصلی به صف تابع بازتاب داده می‌شود، اما صف تابع status موفق را قبل از نتیجه قطعی Telegram ثبت نمی‌کند.
4. `429`، `5xx` و transport retry در صف اصلی باقی می‌مانند و صف تابع job جایگزین تولید نمی‌کند.
5. `AMBIGUOUS` پیشروی وابستگی همان جریان را تا reconciliation متوقف می‌کند.
6. cancel/pause صف تابع job درحال‌ارسال را بدون قرارداد cancellation/fencing از صف اصلی حذف نمی‌کند.
7. fairness میان چند صف تابع و چند campaign در scheduler اصلی اعمال می‌شود؛ priority داخلی صف تابع حق عبور از priority اصلی همان lane/منبع مشترک را ندارد و priority یک bot role حق بلااستفاده‌گذاشتن ظرفیت bot role دیگر را ندارد.

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
4. destination gate همان مقصد تا آن زمان بسته می‌شود.
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
- `429` رکورد را pending نگه می‌دارد، `retry_after + safety_margin` را اعمال می‌کند و destination gate همان مقصد را تا موعد retry می‌بندد؛ single-probe و bot-wide cooldown بعداً در قرارداد نهایی Stage 2/3 تکمیل شدند.
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
- scheduler resource-aware با یک state machine و دو claim/execution lane هم‌زمان و work-conserving برای `primary` و `channel_editor`
- limiter مستقل bot token، semaphore و in-flight هر lane؛ gate مشترک destination/circuit فقط برای منبع واقعاً مشترک و بدون قفل سراسری میان laneها
- credential registry خارجی و allowlisted برای `primary` و `channel_editor` بدون ذخیره token در job/log، به‌همراه preflight fingerprint و permission readback
- routing immutable بر پایه `bot_identity`؛ primary برای همه send/private/callbackها و editor فقط برای editهای allowlisted همان کانال با canonical message identity
- limiter با budget و ظرفیت اجرای جدا برای هر bot role و gate محافظه‌کارانه مقصد کانال فقط در بازه منبع مشترک؛ fallback خودکار و بازنویسی bot job پس از enqueue ممنوع
- حذف ارسال مستقیم کانال از handler و API
- تکمیل retry و error classification
- ادغام terminal edit
- تکمیل reconciliation و metrics
- ساخت adapter/feeder صف‌های تابع روی صف اصلی و حذف Bot API call، limiter و retry مستقل از workerهای تابع
- تبدیل worker broadcast مدیریتی به coordinator گیرندگان با handoff پایدار، پنجره in-flight و feedback نتیجه صف اصلی
- ساخت feeder مستقل ثبت/کنترل آفر و feeder مستقل edit کانال همراه dependency بر publication، supersession، coalescing و ترتیب newest-first پیش از enqueue به صف اصلی
- تعیین تکلیف `trade_delivery_worker` و `telegram_notification_outbox_worker` مطابق ownership matrix: feeder/adaptor یا producer مستقیم صف اصلی، با فقط یک consumer owner برای هر job
- اتصال عملیات زمان‌دار به مسیر runtime پروژه و حذف sleep-taskهای حافظه‌ای برای cleanupهای تحت قرارداد؛ source/feeder پایدار پنج action پایانی در برش `2026-07-18` روی همین شاخه متصل شد و ممیزی/cutover باقی cleanupهای مستقیم هنوز جزو خروج Stage 3 است
- اعمال سیاست authenticated keyboard/anchor بدون تغییر امنیت یا اعتبارسنجی server-side جریان ثبت‌نام و اتصال حساب

معیار خروج: تست‌های هدفمند، integration و regression پاس، feature flag و editor پیش‌فرض خاموش، و inventory runtime ثابت کند هیچ worker تابعی Telegram را مستقیم صدا نمی‌زند، هر side effect فقط یک consumer owner دارد، editor نمی‌تواند send/private/callback یا مقصد غیرمجاز اجرا کند و هیچ credentialی در DB/log/artifact نیست. همچنین تست اشباع باید ثابت کند در حضور backlog پایدار jobهای پر‌اولویت primary، lane editor تا وقتی gate واقعی مقصد باز است claim و dispatch را ادامه می‌دهد و زمان انتظار `bot_lane` و `destination_gate` قابل تفکیک است.

#### وضعیت برش foundation Stage 3 در `2026-07-16`

- مدل و migration افزایشی `telegram_delivery_jobs`، dedupe/upsert هم‌زمان، claim با `SKIP LOCKED`، lease/fencing، dispatch marker، recovery و ثبت خام `retry_after` در branch کاندید پیاده شده‌اند.
- execution state به‌صورت `NO_SYNC` و foreign-local ثبت شده و authority/startup guard مانع اجرای Iran یا هم‌زمانی legacy/queue می‌شود.
- queue runtime هنوز عمداً code-ready نیست: capability داخلی false است، freshness/feeder cutover کامل نشده، readback زنده staging انجام نشده و هیچ env یا feature flag به‌تنهایی نمی‌تواند side effect واقعی را فعال کند.
- `bot_identity` فقط roleهای `primary/channel_editor` را می‌پذیرد؛ route editor هم در service و هم constraint دیتابیس به editهای allowlisted کانال محدود و تغییر bot یک job موجود ممنوع شده است.
- envelope ناقص HTTP 200 موفقیت محسوب نمی‌شود، dispatch marker هر fence تک‌مصرف است، raw destination مهاجرت‌یافته redacted می‌شود و direct cycle خارج ownership رسمی پیش از DB متوقف می‌شود.
- credential registry خارجی برای `primary/channel_editor` پیاده شده است: هر lane فقط token صریح خودش را می‌گیرد، editor خاموش هیچ fallback به primary ندارد، tokenها در repr/fingerprint map ظاهر نمی‌شوند، token یکسان رد می‌شود و supervisor بدون registry پیش از ساخت task fail-closed است. closure هر gateway credential را غیرقابل override نگه می‌دارد.
- بازسازی limiter از شواهد PostgreSQL پیش از ساخت هر controller و claim اجباری است. supervisor برای هر bot یک controller مستقل می‌سازد؛ lane دارای `retry_after`، bot/gateway pause یا destination cooldown در حالت deferred می‌ماند، اما lane سالم، recovery و feeder ادامه می‌دهند. hard-pause کانال، primary را فقط پس از `getMe` هویتی برای ترافیک خصوصی آزاد می‌گذارد و editor را نگه می‌دارد؛ این identity-only readback مجوز resume کانال نیست. پس از پاک‌بودن gate، controller با اثبات نبود probe/cooldown در Redis، preflight کامل همان credential و rehydrate دوباره پس از تمام network readbackها lane را فعال می‌کند. bot ID و channel ID مورد انتظار باید صریح باشند؛ primary باید `can_post_messages + can_edit_messages` و editor فقط `can_manage_chat + can_edit_messages` داشته باشد؛ هر permission اضافه شناخته‌شده یا `can_*` جدید editor، anonymous admin، role/channel اشتباه یا پاسخ ناقص fail-closed است. log فقط fingerprint redacted و permission name دارد. اجرای زنده و ثبت artifact آن در Stage 4 باقی می‌ماند.
- `offer_publication_states.publisher_bot_identity` نگاشت ناشر canonical را صریح نگه می‌دارد: فقط `primary` برای سطح Telegram مجاز است، داده قدیمی Telegram در migration backfill و WebApp بدون bot identity باقی می‌ماند. `(publisher, chat_id, message_id)` پس از ثبت بی‌صدا بازنویسی نمی‌شود؛ state بر mirror قدیمی `offers.channel_message_id` اولویت دارد و reconciliation هر mismatch را از canonical state به mirror اصلاح می‌کند. مقدار missing فقط در مسیر repair صریح قابل backfill است و editor برای خواندن identity ناقص fail-closed می‌ماند.
- limiter توزیع‌شده با Lua اتمیک Redis پیاده شده است: cadence هر bot role مستقل، destination gate میان دو bot مشترک، کلید مقصد hashشده و outage/پاسخ نامعتبر Redis fail-closed است. انتظار عادی limiter پیش از dispatch marker بدون ثبت خطا lease را defer می‌کند؛ خرابی limiter پیش از Telegram lease را آزاد و supervisor را متوقف می‌کند.
- `429` ابتدا همان مقصد را تا مقدار خام `retry_after + safety` می‌بندد. پس از حاشیه probe، برای هر bot فقط یک probe هم‌زمان با مالک hashشده و lease اتمیک مجاز است؛ cancellation فقط توسط همان مالک requirement را rearm می‌کند، expiry دقیقاً یک replacement می‌سازد و نتیجه شناخته‌شده marker و requirement را پاک می‌کند. marker probe، recent-429، deadline مقصد و deadline سراسری bot در PostgreSQL ماندگار و با lock مشترک marker/result خطی‌سازی می‌شوند؛ 429 مقصد متمایز دوم در پنجره دوثانیه‌ای فقط همان bot را تا بیشترین deadline می‌بندد. preflight نیز 429 را از پاسخ redacted تشخیص می‌دهد، `retry_after + safety` رسمی را بدون cap روی bot اعمال و پس از همان موعد retry می‌کند. TTL Redis هرگز از cadence یا حتی retry_after بسیار بزرگ کوتاه‌تر نمی‌شود و polling صد میلی‌ثانیه‌ای وجود ندارد.
- revalidation authoritative خانواده آفر برای `offer_publish` و شش action ویرایش پیاده شده است: هر claim، Offer/status/version و publication identity را از PostgreSQL بازمی‌خواند، publisher یکتا، channel/message، route، hash و payload بازسازی‌شده با renderer فعلی را تطبیق می‌دهد؛ نسخه قدیمی به reconcile، نسخه جلوتر به انتظار dependency، identity یا payload ناسازگار به quarantine و terminal بدون publication به no-op قطعی می‌رود. freshness بلافاصله پس از limiter و پیش از dispatch marker برای بار دوم اجرا می‌شود تا race میان admission و تغییر Offer نیز بسته شود.
- freshness registry/router پوشش‌محور پیاده شده است: هر lane فقط از مسیر registry و فقط با پوشش کامل actionهای مجاز خودش ساخته می‌شود؛ ساخت مستقیم یا scoped وجود ندارد و lane mismatch، action ناشناخته، validator/decision نامعتبر و route مربوط به action غیرپایدار fail-closed هستند. `invalid_action_button_edit` اکنون به freshness آفر متصل است و lane ادیتور پوشش کامل دارد. registry و router مشابه lifecycle نیز اضافه شده و composition فعلی تطابق دقیق gapهای freshness/lifecycle را اجبار می‌کند. نه action خصوصی `account_status`, `general_announcement`, `general_immediate`, `offer_validation_response`, `targeted_admin_message`, `trade_alternative`, `trade_noncritical`, `trade_response`, `trade_unavailable`، action اختصاصی `offer_success`، دو action مستقیم `callback_deadline`, `offer_expiry_callback` و پنج action پایانی `cosmetic_cleanup`, `delayed_restriction`, `noncritical_market`, `temporary_cleanup`, `timed_security` در checkpointهای `2026-07-18` به registry منبع‌محور افزوده شدند. coverage اولیه primary و editor اکنون کامل است و composition هر دو lane ساخته می‌شود؛ runtime همچنان code-disabled است و این پوشش به معنی تکمیل cutover تمام callsiteها نیست.
- revalidation اعلان transition/correction بازار نیز بر پایه `market_runtime_state` و receipt محلی پیاده شده است: receipt با source version ثابت `1` هویت immutable transition است، مقصد/متن/hash/deadline از state و receipt بازسازی می‌شود، transition قدیمی supersede، receipt ارسال‌شده no-op و pending/failed میان transition/correction reclassify می‌شود. `noncritical_market` اکنون receipt زمان‌دار foreign-local، producer API محدود و freshness پنج‌دقیقه‌ای دارد؛ چون در رفتار فعلی پروژه producer واقعی متناظر وجود ندارد، هیچ پیام یا callsite مصنوعی به محصول اضافه نشده است.
- `invalid_action_button_edit` اکنون با کلیک روی دکمه stale از state canonical آفر تولید، از feeder معامله با اولویت مصوب handoff و فقط به lane ادیتور تحویل می‌شود؛ terminal شدن هم‌زمان آن را به edit نهایی reclassify می‌کند. gap پوشش actionهای primary/editor صفر است. کارهای باقی‌مانده Stage 3 شامل cutover واقعی producerهای عمومی غیرحساب و callbackهای غیرانقضا، ممیزی و تبدیل cleanupهای مستقیم باقی‌مانده و reconcile پایدار jobهای `AMBIGUOUS`/`PENDING_RECONCILE`/blocked است. broadcast انبوه مدیریتی، اعلان عضویت کاربر جدید، اعلان باز/بسته‌شدن بازار، پاسخ‌های «تکرار آخرین آفر»، پیام‌های واقعی وضعیت حساب، edit موفقیت preview، همه پاسخ‌های callback انقضای دستی و callsiteهای واقعی منتخب پنج action پایانی به صف اصلی متصل شدند. مسیر عملیاتی resume نیز در همان تاریخ بسته شد؛ `resume_*` صرفاً Redis همچنان مجوز فعال‌سازی نیست. OTP خارج shared queue باقی می‌ماند.
- claim repository اکنون `bot_identity` را اجباری و allowlisted می‌کند و index آماده claim با `bot_identity` آغاز می‌شود؛ lane editor نمی‌تواند job primary را lease کند و برعکس.
- supervisor واحد `queue-v1`، controllerهای مستقل primary/editor، lease recovery و feeder نتیجه معامله را ایجاد می‌کند. batch، sleep، gateway wait، preflight ناموفق و backoff یک lane در task lane دیگر barrier نمی‌سازد؛ هر controller پس از failure با backoff محدود در حالت deferred باقی می‌ماند. adapter freshness و gateway هر lane صریح و اجباری است و نبود هرکدام پیش از DB fail-closed می‌شود.
- editor یک feature flag مستقل با مقدار پیش‌فرض خاموش دارد. حتی با patch آزمایشی ownership، نبود adapter اختصاصی اجازه ساخت task یا side effect نمی‌دهد و gateway واقعی پیش‌فرض از worker حذف شده است.
- تست pure و PostgreSQL واقعی با `300` job primary و `100` job editor، باقی‌ماندن هر `300` primary هنگام claim کامل editor و ادامه editor در زمان انتظار gateway primary را اثبات کردند. cooldown bot در قرارداد reference نیز per-identity شد و destination gate مشترک باقی ماند.
- پس از merge آخرین `main`، `316` تست unit/regression و `20` تست PostgreSQL واقعی شامل enqueue هم‌زمان، `SKIP LOCKED`، route constraint، immutable routing، fencing، crash recovery، 429 خام و transaction boundary پاس شدند؛ migration نیز روی scratch واقعی full-upgrade، downgrade به `f1b6e7f8a9dc` و re-upgrade تا `f2c7d8e9a0bd` را پاس کرد.
- برای برش دو lane، `248` تست unit/regression مرتبط و `23` تست PostgreSQL واقعی پاس شدند؛ مجموعه PostgreSQL شامل index فیزیکی lane-aware، claim فیلترشده، سناریوی `300/100` و هم‌زمانی gateway دو lane بود. migration اصلاح‌شده نیز روی scratch موقت واقعی downgrade به `f1b6e7f8a9dc` و re-upgrade تا `f2c7d8e9a0bd` را دوباره پاس کرد.
- برای برش credential/limiter، `257` تست unit/regression مرتبط، `25` تست PostgreSQL واقعی و `6` تست Redis واقعی پاس شدند. Redis شامل 50 admission هم‌زمان، استقلال budget دو bot، gate مشترک destination، 429 اول/دوم، pause/resume و عدم حضور مقصد خام در keyspace بود؛ PostgreSQL نیز defer بدون خطا، fail-closed پیش از gateway و حفظ transaction/fencing را اثبات کرد.
- برای برش preflight، `270` تست unit/regression مرتبط پاس شدند. ماتریس شامل primary-only و primary+editor، اتصال دقیق هر readback به token همان lane، نبود config، token/bot/channel اشتباه، response/transport ناقص، membership ناسازگار، تمام permissionهای اضافه editor، permission ناشناخته آینده، redaction و اثبات صفر task در preflight ناموفق بود. هیچ تماس زنده Telegram یا deploy انجام نشد.
- برای برش canonical publisher identity، `283` تست publication/queue/regression و `82` تست sync/parity/migration پاس شدند. migration روی PostgreSQL واقعی با دو رکورد legacy، backfill فقط Telegram، رد `channel_editor` و publisher روی WebApp، downgrade به `f2c7d8e9a0bd` و re-upgrade تا `f3d8e9a0b1ce` آزموده شد و دیتابیس scratch پس از ثبت نتیجه حذف شد.
- برای برش Offer freshness در `2026-07-17`، `216` تست publication/queue/regression و `64` تست sync/parity پاس شدند؛ `28` تست PostgreSQL واقعی نیز persistence حالت quarantine، بازخوانی Offer/commodity/publication identity، تغییر version/remaining quantity، و revalidation دوم پس از limiter با صفر gateway call برای job stale را پوشش دادند. scratch نام‌دار پس از تست حذف و نبودنش تأیید شد؛ compile و `diff --check` نیز پاس شدند. این برش فقط خانواده publication/edit آفر است و هیچ تماس زنده Telegram، deploy یا فعال‌سازی runtime انجام نشد.
- در اصلاح scope امنیتی OTP در `2026-07-17`، `150` تست queue/runtime/Offer و مسیرهای واقعی Stage 6 و login OTP پاس شدند؛ `29` تست PostgreSQL واقعی نیز ثابت کردند `OTP_DEADLINE` پیش از insert رد و برای payload حساس هیچ row ساخته نمی‌شود. scratch نام‌دار حذف و نبودنش تأیید شد؛ مقدار enum فقط برای mixed-version حفظ شده و runtime صف همچنان غیرفعال است.
- برای برش freshness router، پس از سخت‌سازی نهایی `85` تست متمرکز freshness/worker/contract، `190` تست queue/publication و `18` تست مستقل پیام معامله پاس شدند؛ `30` تست PostgreSQL واقعی نیز router را در هر دو مرز pre-limiter و post-limiter worker اجرا و سپس یک gateway call موفق را ثبت کردند. تست‌های منفی incomplete coverage، direct/scoped bypass، cross-lane job، future action، non-durable action و decision نامعتبر را پیش از dispatch رد کردند. scratch حذف و نبودنش با count صفر تأیید شد و router هنوز به runtime نصب نشده است. اجرای گسترده‌تر شامل `core_events` دو شکست baseline داشت که بدون این delta روی checkout کنترل نیز عیناً بازتولید شد و به‌عنوان موفقیت این برش شمرده نشد.
- برای برش freshness بازار، `147` تست متمرکز freshness/router/worker/contract/market و `206` تست queue/publication پاس شدند؛ `31` تست PostgreSQL واقعی نیز تغییر transition از open به closed میان limiter و dispatch را با `SUPERSEDED` و صفر gateway call اثبات کردند. scratch نام‌دار حذف و نبودنش با count صفر تأیید شد؛ sender فعلی بازار و runtime صف تغییری نکردند.
- برای برش freshness نتیجه معامله، receipt مستقل هر گیرنده منبع authoritative شد: route فقط `primary/sendMessage/private`، متن از snapshot receipt، مقصد از اتصال جاری کاربر و source identity از receipt dedupe به‌اضافه fingerprint snapshot ساخته می‌شود؛ `sync_version` کاربر نسخه routing است. deadline دقیقاً `trade.created_at + 5s` است و فقط اولویت را `M1→M0` می‌کند، نه عمر پیام را. `429/next_retry_at` قدیمی دور زده نمی‌شود، receipt طرف ارسال‌شده `SENT_NOOP` و طرف pending مستقل `SEND` می‌شود، تغییر متن snapshot یا اتصال کاربر به reconciliation نسخه جدید می‌رود و trade/user/payload/hash/route ناسازگار پیش از gateway متوقف می‌شود. `356` تست گسترده queue/publication/market/trade و `33` تست PostgreSQL واقعی پاس شدند؛ دو تست PostgreSQL این خانواده پس از آخرین سخت‌سازی fingerprint نیز مستقل بازاجرا شدند و scratch نهایی حذف شد. چهار action تعاملی دیگر trade چون producer پایدار ندارند عمداً خارج coverage مانده‌اند و runtime همچنان غیرفعال است.
- برای برش handoff/feedback نتیجه معامله در `2026-07-17`، producer واقعی در queue mode فقط receipt پایدار را upsert می‌کند و حق claim/send مستقیم ندارد؛ receipt واجد شرایط و job صف اصلی در یک transaction ساخته و با marker محلی `NO_SYNC` در `worker_id` به هم متصل می‌شوند. claimهای legacy فقط receipt بدون marker/lease را می‌پذیرند و startup، cycle/loop و مرز نهایی side effect مسیر قدیمی مطابق runtime owner fail-closed شده‌اند. job هم‌هویتِ ازپیش‌موجود بدون marker هرگز در runtime خودکار adopt نمی‌شود؛ receipt آن با marker reconciliation ایزوله می‌شود تا job بعدی feeder پیشروی کند.
  `429` و خطای موقت فقط در main queue retry می‌شوند؛ pause به‌صورت hold پایدار می‌ماند و ambiguity بدون شاهد وارد reconciliation می‌شود. نتیجه شناخته‌شده provider تا سه بار فقط برای persistence دیتابیس retry می‌شود و تماس Telegram هرگز تکرار نمی‌شود؛ با تمام‌شدن این بودجه، dispatch-marked lease برای بازیابی ambiguous حفظ می‌شود. `SENT`، عدم‌دسترسی قطعی private و payload دائماً نامعتبر، job و receipt را در همان transaction terminal می‌کنند و شمار attempt نهایی برابر baseline legacy به‌اضافه claimهای queue است. reclassify اتصال نسخه قدیمی را اتمیک آزاد می‌کند؛ `WAIT/QUARANTINED` حتی با source/receipt/binding خراب transition امن job را rollback نمی‌کنند و job بعدی lane پیشروی می‌کند.
  transaction marker با isolation سریال‌شونده receipt/Trade/User و ردیف‌های موجود accountant/customer relation را قفل و access/payload را دوباره می‌خواند؛ تغییر هم‌زمان tier/status تا commit marker منتظر می‌ماند. فاصله crash میان ثبت نتیجه قطعی و مشاهده Redis برای 429، `BLOCKED_DESTINATION`، `BLOCKED_BOT` و `BLOCKED_GATEWAY` با gate بادوام و scope-aware PostgreSQL بسته است. deadline مستقل bot پس از دو 429 مقصد متمایز و شواهد recent-429 حتی پس از retry بعدی حفظ می‌شوند؛ startup ابتدا Redis را بازسازی می‌کند، سپس controller مستقل هر lane شاهد فعال را deferred نگه می‌دارد تا lane سالم، recovery و feeder متوقف نشوند.
  probe هر bot مالک hashشده و lease دارد، cancellation مالک آن را rearm می‌کند، expiry یک replacement یکتا می‌سازد، marker dispatch در DB ماندگار است و نتیجه شناخته‌شده probe را پاک می‌کند. preflight پس از اثبات gate پاک اجرا، پس از network readback دوباره rehydrate می‌شود و پاسخ 429 آن نیز `retry_after + safety` رسمی را روی همان bot حفظ می‌کند. پنج index محدود destination-gate، hard-pause، bot-cooldown، recent-rate-limit و bot-probe مسیر رشد جدول را پوشش می‌دهند.
  در بازاجرای نهایی این delta، `356` تست unit/regression مرتبط، `64` تست PostgreSQL واقعی و `15` تست Redis واقعی پاس شدند. assertionهای deadline زمان دریافت پاسخ provider را از زمان دیرتر linearization دیتابیس تفکیک می‌کنند تا lock latency نه safety margin را تغییر دهد و نه تست را flaky کند. افزون بر سناریوهای قبلی، crash واقعی بعد از commit و شکست `observe` با دقیقاً یک gateway call، restart و صفر resend، bot cooldown با deadline کوتاه/بلند، hard-pauseهای `401/403/404` همراه کنترل منفی scope، preflight/deferred activation مستقل laneها، single-probe در cancellation/expiry، شکست persistence cooldown به‌صورت fail-closed و recheck gate پس از preflight پوشش داده شدند.
  migrationهای `f4e9a0b1c2df` و `f5e0b1c2d3ea` روی scratch واقعی downgrade تا `f3d8e9a0b1ce` و re-upgrade تا head را پاس کردند؛ سپس scratch با شمار تأییدشده صفر حذف و Redis ایزوله تست متوقف شد. runtime همچنان code-disabled است؛ transition اتمیک resume کانال از DB evidence به full preflight و Redis activation یک blocker صریح باقی می‌ماند و هیچ تماس زنده Telegram یا deploy انجام نشد.
- برای برش resume عملیاتی در `2026-07-18`، جدول foreign-local و `NO_SYNC` با migration `f6f1c2d3e4fb` هر درخواست idempotent، اپراتور، fingerprint شواهد pause، roleهای bot، fingerprintهای full preflight و تاریخچه phase/attempt را نگه می‌دارد. transition به‌صورت saga پایدار `DB pause evidence → full preflight تمام roleهای فعال بدون identity-only → DB resume زیر advisory lock مشترک → پاک‌سازی اتمیک gate مقصد Redis → recheck نهایی DB → completed` اجرا می‌شود. هر عملیات `requested/database_applied/redis_applied` هم در startup rehydration و هم در durable dispatch guard یک blocker مقصد است؛ بنابراین crash پیش یا پس از Redis، شکست Redis و قطع DB هیچ پنجره activation ایجاد نمی‌کنند و retry همان request پس از full preflight تازه ادامه می‌یابد. تغییر یا ایجاد pause تازه حین preflight، cooldown/429 فعال، probe، ambiguity، bot pause یا gateway pause transition را fail-closed متوقف می‌کند. primary و editor باید دقیقاً با credential و permission مصوب readback شوند؛ preflight هویتی primary که برای ادامه private traffic استفاده می‌شود هرگز resume کانال نیست.
  ورودی break-glass فقط از `scripts/telegram_delivery_queue_resume.py`، روی foreign و با request-id، شناسه اپراتور و عبارت تأیید دقیق قابل اجرا است؛ مقصد فقط fingerprint می‌شود و token، پاسخ provider و مقصد خام در خروجی یا history ثبت نمی‌شوند. متد Redis-only سازگاری به‌تنهایی permission نیست و operation ناتمام/شاهد pause در PostgreSQL همچنان dispatch را می‌بندد.
  `357` تست گسترده `test_telegram*.py` پاس و `87` تست وابسته به fixture خارجی در همان اجرا skip شدند. `90` تست متمرکز resume/schema/worker/limiter/preflight/sync نیز پاس شدند. روی زیرساخت scratch واقعی، هشت تست PostgreSQL resume شامل success/replay، شکست preflight و retry، شکست Redis و restart همراه dispatch guard ناتمام، 429 فعال پیش از preflight، تغییر pause وسط preflight، pause تازه پس از Redis، رقابت دو اپراتور و constraint/index فیزیکی پاس شدند؛ تست نهم همین saga را با Redis واقعی اثبات کرد و همراه `15` تست limiter، مجموع `24` تست سبز شد. کل suite PostgreSQL فعلی صف با `46` تست پاس شد. migration جدید full-upgrade، downgrade به `f5e0b1c2d3ea` و re-upgrade تا head را پاس کرد؛ سپس هر دو کانتینر PostgreSQL/Redis scratch با `--rm` متوقف و نبودنشان تأیید شد. هیچ Telegram API زنده، deploy یا push انجام نشد و `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY` همچنان `False` است؛ blockerهای دیگر Stage 3 از جمله coverage و feeder/reconciliation باقی می‌مانند.
- برای برش feeder/handoff/feedback پیام انبوه مدیریتی در `2026-07-18`، receipt موجود منبع دامنه باقی ماند و با migration افزایشی `f7a2b3c4d5ec` یک binding اتمیک foreign-local و `NO_SYNC` به job صف اصلی گرفت؛ cursor عدالت campaign نیز foreign-local و `NO_SYNC` است تا release هر recipient تغییر sync-visible نسازد. feeder زیر advisory lock فقط eligibility و round-robin را اعمال می‌کند: یک گیرنده active برای هر campaign، حداکثر دو campaign هم‌زمان، و پس از terminal feedback انتقال ظرفیت به قدیمی‌ترین campaign آزاد. `429` خام، retry، pause و ambiguity همان job/binding را نگه می‌دارند و recipient بعدی همان campaign را آزاد نمی‌کنند؛ `SENT/SKIPPED/TERMINAL_FAILED` نتیجه job، receipt و aggregate broadcast را در یک transaction همگرا می‌کنند. تغییر content یا `users.sync_version` reclassify و job نسخه جاری می‌سازد؛ user حذف/قطع اتصال/عدم دسترسی skip می‌شود و route، payload، binding یا fingerprint ناسازگار پیش از dispatch quarantine می‌ماند. job هم‌هویت orphan هرگز خودکار adopt نمی‌شود و با marker reconciliation ایزوله می‌گردد.
  lifecycle callbackهای guard/freshness/result برای `ADMIN_BROADCAST` اجباری‌اند؛ worker و sender مستقیم legacy در queue mode پیش از DB/Telegram fail-closed می‌شوند و supervisor صف feeder را مستقل از laneها اجرا می‌کند. `85` تست متمرکز feeder/freshness/worker/schema/ownership و `381` تست گسترده `test_telegram*.py` پاس شدند؛ `98` مورد گسترده فقط به‌علت fixture خارجی skip شدند. `11` تست PostgreSQL اختصاصی شامل دو campaign، round-robin سه campaign، رقابت هشت handoff، sequential feedback، rollback، 429، relink، orphan و constraintهای فیزیکی پاس شد و suite یکپارچه صف به `84` تست پاس با یک skip رسید. revision جدید رفت‌وبرگشت `f7 → f6 → f7` و زنجیره کامل upgrade از دیتابیس خالی تا `f7` را پاس کرد؛ downgrade کامل تا base به‌عنوان شاهد این برش ادعا نمی‌شود، چون migration تاریخی `5061c56d11e7` در حذف unique بی‌نام مستقل از این delta متوقف می‌شود. scratch با `--rm` حذف شد. هیچ Telegram API زنده، deploy یا push انجام نشد و capability runtime همچنان `False` است؛ router/lifecycle مرکب و خانواده‌های منبع‌محور باقی‌مانده هنوز مانع activation هستند.
- برای برش اعلان عضویت کاربر جدید در `2026-07-18`، ممیزی producerها ثابت کرد تنها source واقعی `telegram_notification_outbox` برابر `project_user_joined` است. فقط همین source به action `NEW_USER_MEMBERSHIP` با feeder `ADMIN_SYSTEM` و مسیر ثابت `primary/sendMessage/private` نگاشت شد؛ متن outbox همان snapshot معتبر رویداد باقی ماند، مقصد از اتصال جاری گیرنده بازسازی و `users.sync_version` نسخه route شد. source ناشناخته یا payload عمومی هرگز برای انتخاب action تفسیر نمی‌شود و تا تعریف قرارداد authoritative بدون handoff باقی می‌ماند. گیرنده حذف‌شده، قطع اتصال، منع دسترسی یا customer جاری بدون side effect skip می‌شود؛ تغییر متن یا نسخه کاربر به reclassify می‌رود و route/hash/binding/source ناسازگار quarantine می‌شود.
  migration افزایشی `f8b3c4d5e6fd` binding اتمیک foreign-local و `NO_SYNC` میان outbox و job اصلی را اضافه کرد. feeder فقط eligibility را تعیین می‌کند و هیچ Telegram call، limiter یا retry مستقل ندارد؛ `429`، pause، ambiguity و retry همان job و binding را حفظ می‌کنند و `SENT/SKIPPED/TERMINAL_FAILED` هر دو رکورد را در یک transaction terminal می‌کنند. سه callback dispatch/freshness/result برای این action اجباری‌اند؛ worker و sender مستقیم legacy در queue mode پیش از DB/Telegram fail-closed می‌شوند و job orphan هرگز خودکار adopt نمی‌شود.
  `76` تست متمرکز، `402` تست گسترده `test_telegram*.py` با `107` skip وابسته به fixture، `328` تست sync و `51` تست authority/migration/schema پاس شدند. ماتریس PostgreSQL شامل `93` تست موفق با یک skip ازپیش‌موجود بود؛ `9` تست اختصاصی این bridge تحویل اتمیک، هشت feeder هم‌زمان، source ناشناخته، rollback، حفظ binding در 429، relink، callbackهای اجباری، منع adoption و constraint/index فیزیکی را پوشش داد. migration از دیتابیس خالی تا `f8` و رفت‌وبرگشت `f8 → f7 → f8` با runner محافظت‌شده پاس شد. runtime همچنان `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` است؛ هیچ Telegram API زنده، deploy یا push انجام نشد و همگام‌سازی drift جدید `main` در همان checkpoint برای دروازه تست نهایی باقی مانده بود؛ وضعیت نهایی آن در checkpoint ممیزی callsite بسته شده است.
- برای برش اعلان باز/بسته‌شدن بازار در `2026-07-18`، `market_channel_notice_receipts` منبع authoritative و foreign-local باقی ماند و با migration افزایشی `f9c4d5e6f7ae` یک binding اتمیک و یکتا به job صف اصلی گرفت. feeder مستقل فقط receiptهای `pending/failed` سررسیدشده را به‌ترتیب به `MARKET_TRANSITION/MARKET_STATUS_CORRECTION` با مسیر ثابت `primary/sendMessage/channel` تحویل می‌دهد؛ transition، متن، dedupe، مقصد، template/campaign و freshness deadline از قرارداد دامنه بازسازی می‌شوند. receipt منقضی پیش از handoff بدون ساخت job suppress می‌شود، transition قدیمی پس از handoff در هر دو مرز freshness متوقف می‌شود و job orphan هرگز خودکار adopt نمی‌شود. `429`، retry، pause و ambiguity همان binding را حفظ می‌کنند؛ ارسال موفق یا شکست قطعی، job و receipt را در یک transaction terminal می‌کند و مسیر retry مستقیم legacy در queue mode خاموش است.
  سه callback dispatch/freshness/result برای هر دو action بازار اجباری‌اند؛ dispatch guard زیر قفل `market_runtime_state` دوباره freshness را می‌سنجد، sender مستقیم پیش از gateway و feeder خارج ownership پیش از DB fail-closed می‌شوند و supervisor feeder بازار را مستقل از دو lane اجرا می‌کند. `100` تست متمرکز، `415` تست گسترده `test_telegram*.py` با `116` skip وابسته به fixture، `425` تست sync با یک skip و `51` تست authority/migration/schema پاس شدند. ماتریس PostgreSQL یکپارچه `102` تست موفق با یک skip ازپیش‌موجود داشت؛ `9` تست اختصاصی این bridge تحویل و rollback اتمیک، رقابت هشت handoff روی سه receipt، suppression stale، حفظ binding در `429`، callbackهای اجباری، رد route تغییرکرده پیش از dispatch، منع adoption و constraint/index فیزیکی را پوشش داد. زنجیره کامل upgrade از دیتابیس خالی تا `f9` و رفت‌وبرگشت محافظت‌شده `f9 → f8 → f9` پاس شد. اجرای اولیه sync با policyهای production فعال، شکست‌های baseline مسیر registration/receive را آشکار کرد؛ بازاجرای ایزوله با policyهای تستی مصوب همه `425` تست را سبز کرد. runtime همچنان `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` است؛ هیچ Telegram API زنده، deploy یا push انجام نشد و همگام‌سازی drift جدید `main` در همان checkpoint برای دروازه تست نهایی باقی مانده بود؛ وضعیت نهایی آن در checkpoint ممیزی callsite بسته شده است.
- برای برش «تکرار آخرین آفر» در `2026-07-18`، شش کامیت `b2fd3fcc..63d9c9e9` از `main` به‌صورت انتخابی منتقل شدند. ممیزی رفتار نهایی نشان داد خود ردیف repeat فقط در Reply Keyboard است، اما refresh عادی منو و پاسخ دکمه قدیمی دو نوع `sendMessage` واقعی هستند؛ هر دو با source پایدار `offer_repeat_response` به `OFFER_CONTROL/OFFER_REPEAT_RESPONSE/M1/primary/sendMessage/private` تحویل می‌شوند. queue mode فقط receipt پایدار می‌سازد و مستقیم به Telegram fallback نمی‌کند؛ legacy mode متن و رفتار دقیق `main` را حفظ می‌کند. payload در handoff و پیش از dispatch دوباره از اتصال جاری کاربر، دسترسی فعلی بات، آخرین آفر قابل‌تکرار و کیبورد persistent بازسازی می‌شود؛ تغییر state همان action را reclassify و قطع اتصال/دسترسی آن را supersede می‌کند. `429` همان job و binding را نگه می‌دارد، نتیجه terminal در یک lifecycle مشترک به outbox بازتاب می‌یابد و claim مستقیم legacy برای این source ممنوع است. debounce دوثانیه‌ای expiry مطابق `main` حفظ شد.
  migration شاخه repeat با revision `f2c7d8e9a0b1` و زنجیره صف با head `f9c4d5e6f7ae` در merge revision `faa1b2c3d4e5` همگرا شدند و migration افزایشی `fab2c3d4e5f6` index handoff را فقط برای دو source مصوب گسترش داد. upgrade از دیتابیس خالی تا head، رفت‌وبرگشت `fab → faa → fab` و رفت‌وبرگشت کامل شاخه صف/repeat `fab → f1b6e7f8a9dc → fab` روی PostgreSQL scratch پاس شد. پس از ممیزی race تغییر منو، source version از snapshot کامل کاربر، هویت آفر قابل‌تکرار و payload ساخته شد تا reclassify همیشه job متمایز بسازد؛ `42` تست نهایی متمرکز این سخت‌سازی را پوشش داد. اجرای نهایی گسترده شامل `430` تست `test_telegram*.py` با `121` skip fixture، `492` تست `test_bot*.py`، `329` تست sync و `44` تست authority/migration/schema بود. ماتریس PostgreSQL صف پیش از سخت‌سازی نهایی `106` تست با یک skip ازپیش‌موجود داشت و بازاجرای نهایی `14` تست اختصاصی repeat/notification، handoff اتمیک، enqueue هم‌زمان و idempotent، lifecycle، relink به job متمایز، `429` روی همان job، منع claim legacy و index فیزیکی را پوشش داد. compile و `diff --check` نیز سبز بودند. runtime همچنان `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` است؛ هیچ Telegram API زنده، deploy یا push انجام نشد. کامیت بعدی main یعنی `2c08da14` متعلق به بازیابی state ثبت آفر بود و بعداً با patch-id معادل در `b51c46c8` کاندید reconcile شد؛ اتصال queue پاسخ‌های افزوده‌شده آن در checkpoint ممیزی callsite ثبت شده است.
- برای checkpoint تکمیلی Stage 3 در `2026-07-18`، کامیت‌های `d47f1e95`, `f71c4e78`, `a66a77f1`, `5a9e5205`, `5192db96`, `921c2c26`, `3119f1f1` producer آفر، transaction batch، rank edit، refresh اقدام stale، fairness پایدار، lifecycle composition و مرزبندی `NO_SYNC` را ثبت کردند. `85` تست متمرکز fairness/contract، `46` تست composition/worker، `25` تست sync، ماتریس PostgreSQL صف و `9` تست PostgreSQL pause/resume سبز شدند. migration از دیتابیس خالی تا `fac3d4e5f6a7` و رفت‌وبرگشت `fac → fab → fac` پاس شد. هیچ deploy، merge، push یا تماس Telegram تغییردهنده انجام نشد.
- برای checkpoint actionهای خصوصی Stage 3 در `2026-07-18`، outbox موجود با allowlist صریح برای نه action `sendMessage/private` گسترش یافت. route، feeder، template، source identity، payload، اتصال فعلی Telegram و `users.sync_version` در handoff و دو مرز freshness دوباره اعتبارسنجی می‌شوند؛ نسخه عقب‌مانده foreign به‌جای skip تا رسیدن sync منتظر می‌ماند، تغییر state حساب supersede می‌شود و job/binding ناسازگار quarantine باقی می‌ماند. lifecycle callback برای هر نه action اجباری و claim مستقیم worker قدیمی ممنوع شد. فقط source واقعی `account_status` در این برش از مسیر مستقیم به outbox پایدار cutover شد؛ هشت producer عمومی دیگر API قرارداد را دارند، اما اتصال callsiteهای واقعی آن‌ها و reconciliation نهایی هنوز کار Stage 3 است. در queue mode غیرفعال‌سازی، فعال‌سازی و قفل سراسری حساب همان متن و رفتار فعلی را حفظ می‌کنند و reactivation فقط یک پیام Telegram می‌سازد؛ legacy mode بدون تغییر مستقیم می‌ماند.
  migration افزایشی `fad4e5f6a7b8` فقط partial index handoff را برای sourceهای مصوب گسترش داد؛ head یکتا، full upgrade از دیتابیس خالی و رفت‌وبرگشت `fad → fac → fad` روی PostgreSQL scratch پاس شدند. اجرای نهایی این برش شامل `56` تست unit متمرکز، `47` تست PostgreSQL کامل هسته صف و `21` تست PostgreSQL کامل bridge بود؛ compile و `diff --check` نیز سبز شدند و کانتینر scratch با `--rm` حذف شد. runtime همچنان `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` است؛ هیچ Telegram API زنده، deploy، merge یا push انجام نشد.
- برای checkpoint `offer_success` در `2026-07-18`، receipt اختصاصی `offer_success_preview` در همان outbox پایدار و همان transaction ثبت Offer/publication intent ساخته شد. در queue mode handler دیگر preview را مستقیم edit نمی‌کند؛ feeder آن را به `OFFER_CONTROL/OFFER_SUCCESS/M1/primary/editMessageText/private` تحویل می‌دهد و متن تأییدشده، بلوک «لفظ شما»، parse mode، شناسه همان preview و دکمه `expire_offer` را از Offer/User جاری بازسازی می‌کند. تغییر terminal آفر، حذف منبع، قطع دسترسی یا relink کاربر پیش از handoff/dispatch، edit دارای دکمه stale را suppress می‌کند و هرگز message id قدیمی را به chat جدید منتقل نمی‌کند. lifecycle سه‌مرحله‌ای اجباری است، worker قدیمی source را claim نمی‌کند و نتیجه edit با outbox/job در یک transaction terminal می‌شود؛ legacy mode رفتار فعلی را نگه می‌دارد.
  migration افزایشی `fae5f6a7b8c9` فقط partial index handoff را برای source جدید گسترش داد؛ full upgrade از دیتابیس خالی و رفت‌وبرگشت محافظت‌شده `fae → fad → fae` روی PostgreSQL scratch پاس شدند. اجرای نهایی شامل `62` تست unit/regression ثبت آفر، worker و composition، `25` تست PostgreSQL bridge، `47` تست PostgreSQL هسته صف، `9` تست PostgreSQL resume با یک skip ازپیش‌موجود و `21` تست sync بود. runtime همچنان `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` است؛ هیچ Telegram API زنده، deploy، merge یا push انجام نشد.
- برای checkpoint callback در `2026-07-18`، هر دو action `callback_deadline` و `offer_expiry_callback` بدون outbox یا hop feeder مستقیماً به `M0/primary/answerCallbackQuery` وارد می‌شوند؛ تفاوت feeder منطقی آن‌ها به‌ترتیب `DIRECT` و `OFFER_CONTROL` حفظ شده است. deadline فنی اولیه از زمان دریافت callback ده ثانیه است، اما SLO مصوب همچنان `p95<=1s` و `p99<=2s` می‌ماند؛ عبور deadline پیش از side effect به `EXPIRED_INTERACTION` می‌رود. شناسه callback در source/destination فقط با SHA-256 ثبت و مقدار خام صرفاً در payload لازم execution نگه‌داری می‌شود. route، template، payload/hash، deadline و scope در هر دو مرز freshness و dispatch دوباره کنترل می‌شوند و lifecycle سه‌مرحله‌ای اجباری است.
  تمام outcomeهای `handle_expire_offer` در queue mode، شامل موفقیت، تکرار، نبود آفر/مالکیت، محدودیت مصرف، lock busy و خطای forward، callback مستقیم aiogram ندارند و یک job اختصاصی می‌سازند؛ legacy mode بدون تغییر مستقیم باقی مانده است. در انقضای local، mutation Offer و job callback در یک transaction commit می‌شوند؛ مسیر remote-home از command receipt authoritative موجود استفاده و سپس callback foreign-local را commit می‌کند. ingress عمومی `callback_deadline` آماده است، اما cutover callsiteهای غیرانقضا هنوز جزو Stage 3 است. این برش schema جدید نداشت و head همان `fae5f6a7b8c9` باقی ماند.
  شواهد نهایی شامل `31` تست متمرکز، `113` تست قرارداد/worker مرتبط، `9` تست کامل handler انقضا، `21` تست sync با policy تستی مصوب، `51` تست PostgreSQL هسته صف، `25` تست PostgreSQL bridge و `9` تست PostgreSQL resume با یک skip ازپیش‌موجود بود. suite گسترده `test_telegram*.py` با `496` تست و `137` skip وابسته به fixture و suite دامنه expiry با `53` تست و `6` skip پاس شدند؛ classifier و تست واحد/PostgreSQL پاسخ واقعی `query is too old` نیز در همین شواهد پوشش دارند. runtime همچنان `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` است؛ هیچ Telegram API زنده، deploy، merge یا push انجام نشد.
- برای checkpoint پنج action پایانی Stage 3 در `2026-07-18`، `TIMED_SECURITY` و `DELAYED_RESTRICTION` با sourceهای allowlisted در outbox خصوصی موجود و `NONCRITICAL_MARKET`, `TEMPORARY_CLEANUP`, `COSMETIC_CLEANUP` با receipt مستقل `telegram_scheduled_operations` پیاده شدند. receipt جدید foreign-local و `NO_SYNC` است و `due_at`, cancel/revoke, source version, payload/hash canonical، route جاری، scope و binding صف را پایدار نگه می‌دارد. `DELAYED_RESTRICTION` زمان اجرا و state محدودیت/مسدودیت را دوباره می‌سنجد؛ `TIMED_SECURITY` پس از قفل حساب اجازه اعلان همان تغییر را بدون بازکردن دسترسی بات می‌دهد؛ cleanup موقت فقط `deleteMessage/private` و cleanup ظاهری فقط `editMessageReplyMarkup/private` را می‌پذیرد. پاسخ terminal به source بازتاب می‌یابد، route یا state ناسازگار پیش از dispatch متوقف می‌شود و worker قدیمی sourceهای جدید را claim نمی‌کند.
  callsite واقعی قفل سراسری حساب، رفع محدودیت/مسدودیت با تأخیر، حذف پیام موقت ورودی نامعتبر ادمین و پاک‌سازی کیبورد پیشنهاد معامله در queue mode به source پایدار منتقل شدند؛ legacy mode متن و رفتار فعلی را حفظ می‌کند. در مسیر تأخیری queue، task قدیمی فقط اعلان WebApp را نگه می‌دارد و Telegram مستقیم نمی‌فرستد. `NONCRITICAL_MARKET` producer API محدود و freshness پیش‌فرض پنج‌دقیقه‌ای دارد، اما چون رفتار فعلی پروژه producer واقعی متناظر ندارد، پیام مصنوعی اضافه نشد. migration افزایشی `faf6a7b8c9d0` جدول، constraintها و indexهای لازم را ساخت و partial index outbox را برای دو source زمان‌دار گسترش داد؛ full upgrade از دیتابیس خالی و رفت‌وبرگشت `faf → fae → faf` روی PostgreSQL scratch پاس شد.
  شواهد نهایی این checkpoint شامل `506` تست گسترده `test_telegram*.py` با `143` skip وابسته به fixture، `83` تست sync/domain/admin/trade و `127` تست PostgreSQL با یک skip ازپیش‌موجود بود؛ compile و `diff --check` نیز پاس شدند. اجرای اضافی همه `508` تست `test_bot*.py` تعداد `13` failure و `7` error در مسیرهای قدیمی registration/invitation/WebApp نشان داد؛ همان `54` تست در checkpoint قبل از این delta (`377d7472`) دقیقاً با همان `13/7` شکست بازتولید شدند، پس به‌عنوان baseline مستقل ثبت و موفقیت این برش شمرده نشدند. coverage freshness/lifecycle هر دو lane اکنون کامل است، اما تکمیل reconciliation و ممیزی/cutover باقی callsiteهای مستقیم هنوز blocker Stage 3 است. runtime همچنان `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY=False` است؛ هیچ Telegram API زنده، deploy، merge یا push انجام نشد.
- برای checkpoint account-control Stage 3 در `2026-07-18`، producerهای block/limitation قبل از commit تغییر user، intent متناظر را با همان `users.sync_version` و snapshot دقیق state ثبت می‌کنند؛ handoff و freshness علاوه بر برابری snapshot، منقضی‌نشدن restriction را می‌سنجند. حذف حساب محلی outbox را در transaction خود حذف می‌سازد و حذف دریافتی sync آن را پیش از commit همان batch می‌سازد. route پیش از حذف فقط برای source حذف معتبر است، access/unlinked عادی را دور نمی‌زند و reuse آن توسط حساب دیگر در دو مرز متوقف می‌شود. relay قدیمی `type=notification` فقط برای سازگاری mixed-version نامش حفظ شده و validator مستقل، purpose/parse-mode/text/chat را به OTP ورود جاری محدود می‌کند. inventory ثابت `705` مرز اکنون `remaining_business_direct=0` دارد. شواهد نهایی شامل `523` تست گسترده Telegram با `146` skip، `122` تست OTP/auth/sync/deletion، `22` تست restriction/user-update، کل `30` تست PostgreSQL bridge و بازاجرای `3` سناریوی PostgreSQL اختصاصی بود؛ compile، audit و `diff --check` نیز پاس شدند. هیچ migration، تماس زنده Telegram، deploy، merge یا push انجام نشد و runtime همچنان code-disabled است.
- برای checkpoint نخست callback Stage 3 در `2026-07-18`، timestamp لبه callback مستقل از زمان رسیدن handler ثبت می‌شود؛ ContextVar میان taskها نشت نمی‌کند و queue adapter بدون آن هیچ row یا fallback مستقیم نمی‌سازد. adapter همان ingress/freshness/lifecycle مصوب `answerCallbackQuery` را reuse می‌کند و callback خام فقط در payload اجرایی می‌ماند. کاتالوگ کالا هر دو پاسخ access-denied و acknowledgement عادی را از adapter می‌فرستد. `27` تست middleware/adapter/runtime/catalog/inventory، `55` تست callback-contract/freshness/worker/lifecycle و `525` تست گسترده Telegram با `146` skip پاس شدند؛ schema، migration و Telegram زنده در کار نبود و runtime خاموش ماند.
- برای checkpoint دوم callback Stage 3 در `2026-07-18`، فقط `callback.answer`های تاریخچه معامله cutover شدند و `callback.message.answer`, `edit_text` و `bot.send_document` برای برش interaction دست‌نخورده ماندند. `44` تست تاریخچه، query، pagination، format و export سبز شدند و audit شمار `remaining_callback_direct=241` را تأیید کرد. runtime و schema تغییری نکردند.
- برای checkpoint سوم callback Stage 3 در `2026-07-18`، خانواده invitation admin با `12` تست و block management با `14` تست regression سبز شدند. همه `24` پاسخ از receipt لبه یکسان استفاده می‌کنند و در queue mode فقط ingress `CALLBACK_DEADLINE/M0` را می‌سازند؛ side effectهای دامنه و interactionهای غیر-callback به stage خودشان موکول ماندند. audit مقدار `remaining_callback_direct=217` را تأیید کرد.
- برای checkpoint چهارم callback Stage 3 در `2026-07-18`، `17` پاسخ flow پیام همگانی بدون تغییر coordinator گیرندگان یا receiptهای broadcast cutover شد. `30` تست دامنه و صف broadcast پاس و audit مقدار `remaining_callback_direct=200` را ثبت کرد. هیچ migration یا تغییر runtime وجود نداشت.
- برای checkpoint پنجم callback Stage 3 در `2026-07-18`، دو خروجی pre-auth در queue mode به‌جای تماس مستقیم، session کوتاه خودشان را فقط برای ingress `M0` باز می‌کنند؛ legacy همچنان قبل از Auth همان پاسخ مستقیم را می‌دهد. ترتیب middleware در bootstrap و نبود fallback مستقیم با `17` تست تأیید و audit مقدار `remaining_callback_direct=198` را ثبت کرد.
- برای checkpoint ششم callback Stage 3 در `2026-07-18`، همه `22` پاسخ `trade_execute` بدون تغییر message edit، پیشنهاد لات یا اعلان نتیجه معامله cutover شدند. مسیر local ابتدا نتیجه authoritative را commit می‌کند و سپس ingress مستقل callback را می‌سازد؛ مسیر remote-home نیز success را فقط بعد از پاسخ موفق مالک acknowledge می‌کند. `30` تست guard، blocked، مقدار نامعتبر، pending/success محلی، remote-home، suggestion و markup پاس و audit مقدار `remaining_callback_direct=176` را تأیید کرد. runtime و schema تغییری نکردند.
- برای checkpoint هفتم callback Stage 3 در `2026-07-18`، همه `25` پاسخ `start` از receipt لبه dispatcher استفاده می‌کنند. intent ثبت‌نام و state موفق onboarding قبل از enqueue پاسخ commit می‌شوند؛ adapter با session مستقل باعث commit ناخواسته‌ی sessionهای خواندن invitation یا user نمی‌شود. `31` تست invitation، join request، profile token، registration address/contact، respond guards/success و مسیرهای legacy پاس و audit مقدار `remaining_callback_direct=151` را ثبت کرد. هیچ message/edit یا schema در این برش تغییر نکرد.
- برای checkpoint هشتم callback Stage 3 در `2026-07-18`، همه `33` پاسخ `panel` cutover شدند و access guardها، FSM، forwarding دعوت، mutationهای block/settings و keyboard/anchor تغییری نکردند. acknowledgement موفق mutationها پس از نتیجه دامنه ساخته می‌شود و پاسخ‌های پیشرفت فقط همان معنای فعلی را حفظ می‌کنند. `30` تست admin menu/settings، navigation، profile، standard actions و user settings پاس و audit مقدار `remaining_callback_direct=118` را تأیید کرد. runtime و schema بدون تغییر ماندند.
- برای checkpoint نهم callback Stage 3 در `2026-07-18`، تبدیل بر اساس disposition قطعی AST انجام شد، نه جایگزینی متنی همه‌ی `callback.answer`ها. بنابراین `56` callsite مستقیم wizard/text-offer cutover و `11` مرز transaction-aware موجود در `_handle_trade_confirm_core` و `_answer_stale_trade_creation_callback` دست‌نخورده ماندند. `85` تست commodity، quantity، lot، wizard، preview، confirmation، repeat و text-offer پاس و audit مقدار `remaining_callback_direct=62` را ثبت کرد. publication، پیام success، schema و runtime تغییری نکردند.
- برای checkpoint دهم callback Stage 3 در `2026-07-18`، تبدیل `admin_users` نیز disposition-aware بود: `62` bypass مستقیم حذف و شش callback unblock/unlimit اتمیک موجود حفظ شدند. role، account status، limit و block-setting موفق پیش از enqueue callback commit می‌شوند؛ redirect حذف و پاسخ‌های read-only session دامنه را commit نمی‌کنند. `50` تست block، delete، entry، limit، profile، role، search، settings، list و unblock/unlimit پاس شدند. audit اکنون `remaining_callback_direct=0` را enforce می‌کند؛ schema و runtime تغییری نکردند.
- برای foundation نتیجه/anchor interaction Stage 3 در `2026-07-18`، قرارداد executable پیش از هر migration یا تبدیل `message.answer` افزوده شد. target شناخته‌شده chat id علامت‌دار و message id مثبت می‌خواهد؛ target نتیجه‌ای فقط از receipt مثبت و `SENT+message_id` واقعی resolve می‌شود. stateهای nonterminal/ambiguous هیچ edit وابسته یا anchorی را آزاد نمی‌کنند و شکست/quarantine منبع رفتار downstream صریح دارد. flow خروج authenticated با keyboard موقت بدون persistent menu رد می‌شود و API عمداً هیچ گزینه حذف active anchor ندارد. `16` تست جدید همراه `49` تست قرارداد صف پاس شدند. persistence/feedback و adapterهای خانواده‌ای هنوز گام بعد و `remaining_interactive_direct=344` بدون تغییر است.
- برای checkpoint persistence نتیجه/anchor interaction Stage 3 در `2026-07-18`، metadata دقیق result در payload allowlisted outbox و fingerprint منبع ذخیره می‌شود؛ persistent menu authenticated نمی‌تواند خارج tracking anchor enqueue شود. سرویس `enqueue_private_interaction_once` ورودی foreign/private را fail-closed می‌سنجد، replay هم‌هویت را بدون عقب‌بردن desired generation برمی‌گرداند و برای `SET_CURRENT` با advisory lock chat-local نسل یکتا می‌سازد. freshness فقط desired receipt جاری را به Telegram می‌فرستد، route enqueue را در برابر relink ثابت نگه می‌دارد و feedback همان transaction terminal نتیجه را با قفل row به active anchor تبدیل می‌کند؛ ambiguous هیچ anchorی آزاد نمی‌کند و active قبلی تا موفقیت نسل جدید حفظ می‌شود. feedback sourceهای قدیمی membership/repeat/offer-success را parse نمی‌کند. جدول `telegram_interaction_anchor_states` عمداً foreign-local/`NO_SYNC` و بدون token یا متن پیام است و constraint فیزیکی آن active-outbox بدون active-anchor را رد می‌کند. migration `fb07b8c9d0e1`، رفت‌وبرگشت revision، full empty upgrade، پنج تست PostgreSQL lifecycle/concurrency/supersession/relink/constraint، هر `30` تست PostgreSQL regression bridge و `44` تست واحد پاس شدند؛ suite گسترده `564` تست Telegram با `150` skip محیطی نیز سبز شد. adapter/cutover خانواده‌های پیام‌ساز هنوز انجام نشده است؛ runtime خاموش و inventory `344` بدون تغییر باقی ماند.
- برای checkpoint نخست cutover پیام‌ساز interaction Stage 3 در `2026-07-18`، adapter پیام ورودی فقط در queue mode route `(user.id, user.telegram_id, user.sync_version, message.chat.id, message.message_id)` را کامل و منطبق می‌خواهد، reply markup مدل aiogram را به JSON تبدیل و source/logical identity bounded می‌سازد. پاسخ‌های این برش هیچ return فوری، persistent reply keyboard، flow-exit یا edit وابسته ندارند و بنابراین `PRESERVE_CURRENT/NONE` می‌گیرند؛ inline keyboard آن‌ها anchor نیست. تست PostgreSQL نشان داد مسیر واقعی adapter تا outbox→handoff→claim→SENT پیش می‌رود، receipt message id را نگه می‌دارد و anchor مصنوعی نمی‌سازد. چهار callsite block-search از direct خارج و budget ماشینی از `344` به `340` کاهش یافت؛ یک مرز مستقیم فقط داخل شاخه legacy خود adapter با disposition `legacy_mode_guarded` باقی است. `14+49` تست هدفمند، `6` تست PostgreSQL و `570` تست گسترده پاس شدند. runtime و head تغییری نکردند.
- برای checkpoint دوم cutover پیام‌ساز interaction Stage 3 در `2026-07-18`، شش پاسخ ورودی admin-broadcast source key مستقل بر اساس outcome دارند تا retry همان update idempotent باشد و دو outcome یک پیام ورودی collision نکنند. inline keyboardهای انتخاب/تأیید همراه payload صف می‌شوند ولی Reply Keyboard یا anchor جدیدی ایجاد نمی‌کنند. stateهای FSM با همان ترتیب legacy نسبت به ساخت پاسخ حفظ شدند و صف campaign/recipient موجود هیچ تغییری نکرد. شش bypass حذف شدند و budget از `340` به `334` رسید؛ `49+10` تست هدفمند و suite گسترده `570` تست سبز شدند.
- برای checkpoint سوم cutover پیام‌ساز interaction Stage 3 در `2026-07-18`، تنها ده validation پایان‌دهنده flow ثبت آفر منتقل شدند. source keyها outcome-specific و مبتنی بر identity پیام ورودی هستند، action هر ده مورد `OFFER_VALIDATION_RESPONSE` است و inline keyboard پیشنهاد لات به‌صورت canonical در payload می‌ماند. budget از `334` به `324` و total از `437` به `427` رسید. `37` تست متمرکز، `88` تست خانواده `trade_create` و `570` تست گسترده Telegram پاس شدند. خانواده‌های result-dependent برای مرحله dependency/order دست‌نخورده باقی ماندند.
- برای checkpoint چهارم cutover پیام‌ساز interaction Stage 3 در `2026-07-18`، سه پاسخ مستقل panel source key جداگانه و `PRESERVE_CURRENT/NONE` دارند. مسیر settings همچنان inline keyboard خود را حمل می‌کند، اما این markup به‌عنوان persistent menu طبقه‌بندی نمی‌شود. budget از `324` به `321` و total از `427` به `424` رسید؛ `31` تست panel و suite گسترده `570` تست Telegram سبز شدند.
- برای checkpoint پنجم cutover پیام‌ساز interaction Stage 3 در `2026-07-18`، هفت terminal feedback پنل source key مستقل و `PRESERVE_CURRENT/NONE` گرفتند. برش فقط `sendMessage` را پوشش می‌دهد؛ `sendDocument` تا قرارداد فایل بادوام fail-closed و خارج cutover است. budget از `321` به `314` و total از `424` به `417` رسید؛ `32` تست panel و `570` تست گسترده Telegram پاس شدند.
- برای checkpoint ششم cutover پیام‌ساز interaction Stage 3 در `2026-07-18`، سه منوی persistent panel با سه source key مستقل، generation تراکنشی و receipt پیام واقعی پوشش داده شدند. شاخه queue هیچ cleanup لنگر قبلی را پیش از activation انجام نمی‌دهد. budget از `314` به `311` و total از `417` به `414` رسید؛ `33` تست panel، شش تست PostgreSQL و `570` تست گسترده Telegram پاس شدند.
- برای checkpoint هفتم cutover پیام‌ساز interaction Stage 3 در `2026-07-18`، callback-message adapter پس از شاخه legacy تنها route خصوصی authenticated کامل را می‌پذیرد و digest ۳۲ کاراکتری SHA-256 را به‌جای raw callback id در source identity می‌گذارد. هفت پاسخ تاریخچه `PRESERVE_CURRENT/NONE` و action صریح `TRADE_NONCRITICAL` دارند. با افزوده‌شدن یک مرز legacy داخل adapter و حذف هفت bypass، budget از `311` به `304`، total از `414` به `408` و `legacy_mode_guarded` از `63` به `64` رسید. `30+13+573` تست هدفمند/گسترده سبز شدند.
- readback زنده و فقط‌خواندنی editor در staging، حق پیام `can_edit_messages=true` و همه حقوق post/delete/invite/restrict/promote/change-info/video/direct-message را false گزارش کرد؛ بااین‌حال `can_post_stories`, `can_edit_stories`, `can_delete_stories` هنوز true بودند. Bot API این سه را حق مستقل ادمین تعریف می‌کند، بنابراین preflight سخت‌گیرانه تا false شدن آن‌ها یا تصمیم صریح امنیتی جدید fail-closed باقی می‌ماند. این وضعیت به معنی مجازشدن editor برای ارسال پیام نیست؛ allowlist کد و constraint دیتابیس همچنان فقط edit کانال را می‌پذیرند.
- در ادامه همان روز مالک محصول اعلام کرد هر سه دسترسی Story از editor گرفته شده‌اند. این تغییر تا readback زنده بعدی staging «گزارش‌شده ولی تأییدنشده» است؛ Stage 4 باید پیش از هر smoke یا بار، `can_post_stories=false`, `can_edit_stories=false`, `can_delete_stories=false` را همراه سایر permissionهای ممنوع دوباره بخواند و در غیر این صورت fail-closed بماند.
- این foundation مجوز deploy یا شروع Stage 4 نیست و runtime عمداً غیرفعال باقی مانده است.

#### ممیزی ماشینی callsite و ترتیب قطعی ادامه Stage 3 در `2026-07-18`

فرمان مرجع ممیزی:

```bash
python3 scripts/audit_telegram_delivery_calls.py --check
python3 scripts/audit_telegram_delivery_calls.py --format json
```

baseline اولیه `705` مرز syntactic را ثبت کرد. در checkpoint callback، ۳۲ فراخوانی `callback.message.answer` که پیام جدید می‌سازند از callback deadlineدار جدا و به interaction منتقل شدند؛ سپس هر `260` callback واقعی با دو ingress اختصاصی موجود یا adapter legacy مشترک پوشش داده شد. پس از هفت cutover پیام‌ساز، خروجی جاری `408` مرز دارد و هیچ `remaining_callback_direct` ندارد. این ممیزی محافظه‌کارانه است: reachable بودن هر مسیر در یک deployment را ادعا نمی‌کند، اما هیچ فراخوانی را فقط به‌دلیل «احتمالاً بلااستفاده بودن» از inventory حذف نمی‌کند.

| disposition | تعداد | نتیجه |
| --- | ---: | --- |
| `queue_execution` | `1` | gateway credential-bound رسمی صف |
| `legacy_owner_guarded` | `10` | مرز مستقیم فقط زیر ownership قدیمی مجاز است |
| `legacy_mode_guarded` | `64` | شاخه queue پیش از فراخوانی مستقیم خارج می‌شود؛ شامل adapterهای مشترک callback، callback-message و پیام ورودی |
| `legacy_parameter_guarded` | `2` | caller در queue mode شاخه Telegram را صریحاً خاموش می‌کند |
| `durable_exempt` | `4` | فقط مرزهای transport و relay سخت‌گیرانه OTP کوتاه‌عمر مصوب |
| `non_message_control` | `2` | ban/unban عضویت و خارج از pacing پیام |
| `non_delivery_timer` | `1` | در queue mode فقط side effect غیرتلگرامی باقی می‌ماند |
| `remaining_business_direct` | `0` | بسته‌شده در checkpoint account-control |
| `remaining_callback_direct` | `0` | بسته‌شده؛ رشد دوباره با budget صفر رد می‌شود |
| `remaining_interactive_direct` | `304` | blocker Stage 3؛ شامل پیام‌های جدید داخل callback handler |
| `remaining_cleanup_direct` | `13` | blocker Stage 3 |
| `remaining_memory_timer` | `7` | blocker Stage 3 |

برش نخست این ترتیب بسته شد: restriction از قرارداد عمومی route استفاده نمی‌کند و snapshot دقیق state/expiry را در handoff و dispatch دوباره می‌سنجد؛ حذف حساب route پیش از حذف را فقط همراه snapshot حذف authoritative می‌پذیرد و reuse route را متوقف می‌کند؛ relay قدیمی هم در sender و receiver فقط OTP دقیق را قبول می‌کند. بنابراین دیگر تونل عمومی business خارج صف در این دسته وجود ندارد.

ترتیب قطعی برش‌های بعدی Stage 3:

1. **انجام شد:** قراردادهای source/freshness اعمال restriction و حذف حساب، cutover producerهای محلی/sync و محدودکردن relay قدیمی به OTP.
2. **انجام شد:** تبدیل callbackها به‌صورت خانواده‌ای با ingress مستقیم `M0` و deadline دریافت‌شده؛ budget مستقیم اکنون صفر است.
3. **foundation pure، persistence/feedback، adapter پایه و هفت خانواده انجام شد:** ادامه cutover پیام‌های خصوصی باید خانواده‌ای و بر اساس نیاز به return/anchor/dependency/order انجام شود. تبدیل مکانیکی `message.answer` بدون receipt بادوام، source identity و dependency resolution همچنان ممنوع است.
4. انتقال cleanupها و هفت timer حافظه‌ای به `telegram_scheduled_operations` با حفظ anchor و سیاست «عدم حذف پیام لنگر».
5. تکمیل reconciler عملیاتی `AMBIGUOUS`, `PENDING_RECONCILE`, blocked و اثبات inventory بدون remaining direct پیش از تغییر `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY`.

کاهش هر دسته بدون تغییر baseline مجاز است؛ افزایش آن یا callsite طبقه‌بندی‌نشده تست را fail می‌کند. صفرشدن coverage registry به معنی صفرشدن callsite مستقیم نیست و تا پایان پنج برش بالا Stage 3 باز می‌ماند.

### Stage 4 — deploy و آزمایش تکرارشونده staging

- deploy فقط با مسیر استاندارد staging
- ثبت `max_active_offers=10` فقط از مرجع مدیریتی Iran staging و تأیید sync/cache در foreign staging
- تأیید recreate شدن `bot`, `foreign_app`, `sync_worker`, `foreign_sync_worker`
- اجرای preflight و smoke واقعی primary-send/editor-edit پیش از load؛ شکست آن کل mode editor را fail-closed می‌کند
- اجرای A/B با trace یکسان برای `primary-only` و `primary+channel_editor` و ثبت metric به تفکیک bot role و gate مشترک مقصد کانال
- اجرای سناریوی head-of-line با backlog پایدار primary و editهای هم‌زمان؛ اثبات پیشروی editor مستقل از صف primary و تفکیک توقف مشروع destination gate از توقف نرم‌افزاری
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
| `TOPQ-C25` | بحرانی | DECIDED | scope پاسخ `429` می‌تواند destination یا bot-wide باشد | limiter دو‌سطحی؛ cooldown مقصد، حداکثر یک probe هم‌زمان برای هر bot با مالک hashشده/lease/rearm مالک و replacement یکتا پس از expiry، marker بادوام DB و cooldown همان bot پس از 429 مقصد متمایز دوم | تست 429 هم‌زمان چند مقصد، cancellation/expiry/restart و اثبات نبود retry storm |
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
| `TOPQ-C62` | بحرانی | DECIDED | دو bot credential می‌توانند دو execution owner یا retry مستقل بسازند | یک main queue، repository و ownership state machine؛ زیر owner واحد `queue-v1` دو claim/execution lane مستقل مجاز و الزامی‌اند، اما editor صف پایدار، retry owner، feeder یا API poller مستقل ندارد | inventory و concurrency test ثابت کند هر job دقیقاً یک claim/lease/fencing owner دارد و هر lane فقط bot identity خود را claim می‌کند |
| `TOPQ-C63` | بحرانی | DECIDED | تغییر bot پس از enqueue یا fallback می‌تواند duplicate و نتیجه مبهم بسازد | routing immutable پیش از claim؛ fallback و بازنویسی خودکار editor↔primary ممنوع، rollback پس از reconcile | outage/restart/fencing test با صفر fallback و duplicate |
| `TOPQ-C64` | بالا | DECIDED | داشتن دو token لزوماً ظرفیت همان کانال را افزایش نمی‌دهد و scope `429` قطعی نیست | budget و execution lane جدا برای هر bot role، همراه gate محافظه‌کارانه مشترک مقصد تا شاهد Stage 4؛ gate نباید dispatcher سراسری شود و افزایش rate فقط با A/B تکرارپذیر مجاز است | metric bot-role/destination/wait-reason و benchmark primary-only در برابر split |
| `TOPQ-C65` | بحرانی | DECIDED | editor ممکن است method یا مقصد خارج از وظیفه خود را اجرا کند | allowlist fail-closed: فقط editهای مصوب کانال، canonical message identity و منع send/private/callback/delete | table-driven authorization tests و negative destination/method tests |
| `TOPQ-C66` | بالا | DECIDED | editor updateهای primary را دریافت نمی‌کند و discovery/polling می‌تواند stale یا ناسازگار شود | انتقال canonical `(publisher_bot_identity, destination_identity, message_id)` از publication state؛ بدون Telegram/API polling | integration primary-send→state→editor-edit و quarantine روی mismatch |
| `TOPQ-C67` | بحرانی | DECIDED | token دوم، دسترسی بیش‌ازحد یا fingerprint اشتباه سطح حمله و خطر ارسال production را زیاد می‌کند | secret registry خارج DB، permission حداقلی `can_edit_messages`، fingerprint/readback و destination allowlist پیش از claim | secret scan، permission preflight و negative production fingerprint |
| `TOPQ-C68` | بحرانی | DECIDED | dispatcher یا priority سراسری می‌تواند صدها job پر‌اولویت primary را جلوتر از editها نگه دارد و ظرفیت مستقل editor را بی‌استفاده کند | scheduler resource-aware و work-conserving با دو claim/execution lane مستقل زیر همان state machine؛ priority فقط میان رقیبان همان lane/منبع مشترک و gate مقصد فقط هنگام pacing/cooldown واقعی اعمال می‌شود | اشباع حداقل ۳۰۰ job `M0/M1` primary که دست‌کم ۲۰۰ مورد آن private/admin و خارج gate کانال‌اند، همراه ۱۰۰ edit editor؛ editor هنگام باقی‌بودن backlog primary پیشروی کند، مگر با شاهد gate مقصد خودش، و duplicate/fallback صفر باشد |
| `TOPQ-C69` | بحرانی | DECIDED | ورود `OTP_DEADLINE` به صف پایدار، کد ورود محرمانه را در PostgreSQL نگه می‌دارد و رفتار امن فعلی `main` را تغییر می‌دهد | enum فقط برای mixed-version باقی می‌ماند؛ feeder mapping حذف و enqueue پایدار آن fail-closed است؛ مسیر امضاشده و receipt کوتاه‌عمر Redis مالک OTP می‌ماند | تست قرارداد rejection، تست PostgreSQL با شمار صفر row و regression کامل Stage 6 OTP |
| `TOPQ-C70` | بحرانی | DECIDED | `invalid_action_button_edit` بدون producer و منبع authoritative می‌تواند با اعتماد به payload stale دکمه پیام اشتباه را حذف کند | enum و mapping اولویت فقط رزرو می‌مانند اما producer/handoff واقعی، router و activation برای آن ممنوع است؛ هر producer آینده باید intent پایدار منبع‌محور، version/deadline و canonical chat/message identity تعریف کند | inventory با صفر producer، coverage ناقص عمدی و تست activation fail-closed تا تصویب قرارداد منبع |
| `TOPQ-C71` | بحرانی | DECIDED | receipt نتیجه معامله پیش از terminal شدن می‌تواند snapshot متن را اصلاح کند یا اتصال Telegram گیرنده تغییر کند؛ تکیه بر payload قدیمی می‌تواند متن/مقصد stale، collision همان logical identity یا consumer دوگانه بسازد | source identity شامل dedupe receipt و fingerprint snapshot است، `source_version` از `users.sync_version` می‌آید و deadline پنج‌ثانیه‌ای فقط priority است. producer queue-mode فقط receipt می‌سازد؛ handoff job/marker و feedback terminal job/receipt اتمیک‌اند و callbackهای trade در guard/freshness/result اجباری‌اند. marker محلی claim legacy را می‌بندد و runtime owner startup، API repair، cycle و مرز side effect مستقیم را متوقف می‌کند؛ orphan هم‌هویت فقط به reconciliation می‌رود و runtime آن را adopt نمی‌کند. 429/temporary retry، pause hold و ambiguity reconcile فقط مالک main queue دارند؛ persistence شناخته‌شده بدون تکرار Telegram bounded-retry می‌شود. gate بادوام مقصد در PostgreSQL فاصله provider-result/Redis را می‌بندد، بیشترین retry deadline را نگه می‌دارد و Redis پیش از هر claim از شواهد فعال بازسازی می‌شود. dispatch marker سریال‌شونده زیر lock دوباره Trade/receipt/User/relation/access/payload را می‌خواند. activation کل runtime همچنان تا تکمیل router مرکب و reconciliation عملیاتی fail-closed است | تست producer واقعی، تغییر snapshot/relink/relation، ۲۰ handoff هم‌زمان، rollback دوطرفه، orphan، bypass guard/feedback، poison isolation، sent/pending مستقل، مرز پنج ثانیه، 429 بدون legacy claim، crash window و startup replay، marker هم‌زمان مقصد، persistence retry و crash→ambiguous با صفر ارسال stale/دوگانه |

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
| [TOPQ-ADR-04](adr/TOPQ-ADR-04-scheduler-freshness-outage.md) | scheduler resource-aware، laneهای اجرایی، priority، `M0` deadline، freshness، edit ordering/catch-up و outage mode | `C10`, `C12`, `C25`, `C26`, `C30`, `C37`, `C56`, `C57`, `C58`, `C68`, `C71` |
| [TOPQ-ADR-05](adr/TOPQ-ADR-05-worker-ownership-rollout.md) | inventory gateway، topology صف‌های تابع، ownership workerهای موجود و rollout نسخه مختلط | `C16`, `C27`, `C28`, `C39`, `C40`, `C41`, `C59`, `C60`, `C69` |
| [TOPQ-ADR-06](adr/TOPQ-ADR-06-operations-ux-go-live.md) | RACI، copy نهایی، keyboard/anchor UX، incident response و go/no-go | `C61`, `N03`, `N05`, `N10`, `N14` |
| [TOPQ-ADR-07](adr/TOPQ-ADR-07-editor-bot-routing.md) | نقش editor، routing چند credential، lane مستقل اجرا، permission، limiter و گیت فعال‌سازی | `C62` تا `C68`, `N19` |

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
- ترتیب `M0` shared queue به‌طور صریح callback deadlineدار، اعلان معامله عبورکرده از پنج ثانیه و سپس publication آفر است. اعلان نتیجه معامله ابتدا `M1` است و فقط recipient ارسال‌نشده مستقل ارتقا می‌یابد. OTP محرمانه از این صف مستثنا و در transport کوتاه‌عمر فعلی باقی می‌ماند.
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
3. **ترتیب:** feeder ابتدا internal rank و freshness را اعمال می‌کند. صف اصلی سپس `M0..M7`، deadline و `enqueued_seq` افزایشی foreign را میان jobهای رقیب همان execution lane یا منبع مشترک اعمال می‌کند؛ این ترتیب یک barrier میان ظرفیت primary و editor نیست. ساعت یا ID محلی Iran/foreign مبنای FIFO مشترک نیست. `offer.created_at` فقط برای newest-first صف edit و natural identity برای replay به‌کار می‌رود.
4. **فشار و fairness:** `M0` strict است. کلاس‌های `M6/M7` فقط ظرفیت باقی‌مانده می‌گیرند و عبور سن آن‌ها alert/SLO breach است، نه مجوز عبور از `M0`. feeder edit سهم catch-up یک‌به‌بیست دارد. broadcastها یک in-flight برای هر campaign، سقف کلی دو campaign و round-robin دارند.
5. **limiter و 429:** budget bot هر lane مستقل و destination gate فقط برای منبع مشترک هم‌زمان اعمال می‌شوند. 429 ابتدا gate مقصد را تا مقدار خام `retry_after + safety_margin` می‌بندد. پس از حاشیه probe، برای هر bot فقط یک probe با مالک hashشده و lease مجاز است؛ cancellation مالک requirement را rearm، expiry یک replacement یکتا و نتیجه شناخته‌شده marker را پاک می‌کند. 429 مقصد متمایز دوم در پنجره دوثانیه‌ای cooldown همان bot را تا بیشترین موعد فعال می‌کند. خطای bot-specific lane دیگر را متوقف نمی‌کند؛ فقط شاهد gateway/config سراسری یا cooldown واقعاً مشترک مقصد می‌تواند هر دو را نگه دارد. preflight 429 نیز retry_after رسمی را روی bot حفظ می‌کند و polling صد میلی‌ثانیه‌ای وجود ندارد.
6. **lease و claim:** lease حداقل `request_timeout + 15s` است، با fencing token و heartbeat. claim در تراکنش کوتاه PostgreSQL انجام و HTTP خارج آن اجرا می‌شود. نتیجه worker با token قدیمی پذیرفته نمی‌شود.
7. **degraded mode:** نبود DB قبل از commit پاسخ موفق تولید نمی‌کند؛ نبود Redis ارسال را fail-closed و صف PostgreSQL را محفوظ نگه می‌دارد؛ outage Telegram circuit-break و backlog می‌سازد. recovery با probe و drain کنترل‌شده انجام می‌شود.
8. **ابهام ارسال:** send مبهم بدون شاهد قطعی resend نمی‌شود؛ operator نیز اجازه SQL یا retry دستی آن را ندارد. edit idempotent پس از revalidation قابل تکرار و callback دیرهنگام منقضی است.
9. **retention و privacy:** terminal jobها ۳۰ روز hot، payload و error body خامِ redacted حداکثر هفت روز، aggregate غیرهویتی ۱۸۰ روز و unresolvedها تا ۳۰ روز پس از resolution نگهداری می‌شوند. metric label شامل ID، متن یا PII نیست.
10. **rollout:** migration افزایشی و code سازگار ابتدا با flag خاموش روی دو peer deploy می‌شود؛ shadow planner حداکثر ۲۴ ساعت side effect ندارد. cutover کل کانال و ownership اتمیک است و canary درصدی direct/queue ممنوع است. rollback فقط پس از توقف claim و reconciliation in-flight/ambiguous انجام می‌شود.
11. **ایمنی حساب‌های تست:** pool هشتادحسابی و جریان کامل sender/receiver در Test DC است. main-DC staging فقط bot/channel و مقصدهای کنترل‌شده allowlist را برای کالیبراسیون نرخ با workload replay صف به‌کار می‌گیرد؛ هیچ کاربر production وارد تست نمی‌شود.
12. **عملیات:** pause/resume ابزار break-glass است. resume کانال باید یک transition اتمیک و auditپذیر `DB evidence → full getMe/getChat/getChatMember/permission preflight → Redis gate → activation` باشد؛ متدهای `resume_*` فعلی که فقط Redis را پاک می‌کنند مجوز فعال‌سازی نیستند. این مسیر با saga پایدار، gate عملیات ناتمام در startup/dispatch، advisory lock مشترک، recheck پس از Redis و CLI تأییدشده در برش `2026-07-18` پیاده و روی PostgreSQL/Redis scratch اثبات شد؛ اجرای زنده آن همچنان فقط در Stage 4 مجاز است. retry job permanent فقط پس از اصلاح علت و source version جدید مجاز است و ambiguous فقط با شاهد قطعی resolve می‌شود.
13. **بات ویرایشگر:** primary ناشر یکتا و مالک همه تعامل‌های خصوصی/callback است. `channel_editor` فقط editهای allowlisted همان کانال را زیر همان main queue، اما در claim/execution lane مستقل خود اجرا می‌کند؛ routing آن immutable، credential خارج DB، permission حداقلی و fallback خودکار ممنوع است. lane editor در backlog primary work-conserving می‌ماند و فقط gate واقعی مقصد یا circuit مشترک مجاز به توقف آن است. فعال‌شدن editor خروجی Stage 4 است و شکست گیت، mode امن `primary-only` را بدون تغییر schema نگه می‌دارد.

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
| `C62–C68` | Backend + Operations + Security | Stage 3 و 4 | editor خاموش، توقف claim lane آن، حفظ job و reconcile پیش از هر تغییر routing |
| `C69` | Backend + Security | Stage 3 | OTP خارج shared queue، حفظ transport فعلی و رد هر durable enqueue |
| `C70–C71` | Backend + Operations | Stage 3 و 4 | activation fail-closed تا منبع authoritative/feedback کامل؛ حفظ receipt/job و منع ارسال stale یا consumer دوگانه |
| `N01–N06` | Product + Operations | Stage 4 و 5 | `NO-GO`، بدون کاهش SLO یا تغییر semantics |
| `N07–N19` | Operations + Security | Stage 1، 4، 5 و 6 | توقف تست/release، حفظ artifact redacted، mode امن primary-only و عدم لمس production |

هیچ ردیفی صرفاً با نوشته‌شدن این تصمیم‌ها `IMPLEMENTED` یا `VERIFIED` محسوب نمی‌شود. ارتقای وضعیت فقط با commit کد/تست یا artifact staging انجام می‌شود.

## 14. معیار پذیرش نهایی

- پیش از شروع Stage 3، تصمیم‌های بخش 13.12 به ADRهای بخش 13.6 استخراج، قرارداد pure با `M0` تا `M7` بازنویسی و تمام تست‌های Stage 2 دوباره پاس شده باشند. گیت اولیه در `2026-07-16` با شش ADR، `44` تست قرارداد و `171` تست هدفمند پاس شد؛ ADR هفتم بعد از تصمیم editor افزوده و با چالش `C68` اصلاح شد و پیش از فعال‌سازی runtime باید تست‌های `C62..C68/N19` نیز پاس شوند.
- هر چالش فنی و غیرفنی owner، stage هدف، تست یا شاهد پذیرش و rollback داشته باشد؛ مورد بدون شاهد «بسته» محسوب نمی‌شود.
- ظرفیت/عمر آفر و معنای SLO publication به تصمیم صریح محصول رسیده باشد؛ backlog مورد انتظار نباید بعداً به‌عنوان باگ ناشناخته گزارش شود.
- WebApp با snapshot رفتار فعلی `main` یکسان باشد؛ در Bot هر مسیر فقط یک پیام ترکیبی موفقیت/«لفظ شما» با دکمه انقضا داشته باشد، `sendMessage` خصوصی دوم صفر باشد و خطای Telegram پس از commit هیچ copy غیرعادی به کاربر نشان ندهد.
- نرخ پذیرش حداقل سه آفر در ثانیه بدون ازدست‌رفتن رکورد صف تأیید شود.
- هر آفر یک پیام مستقل داشته باشد و batching وجود نداشته باشد.
- هیچ `429` به `FAILED` یا انقضای زودهنگام آفر منجر نشود و هیچ publication یا دکمه عملیاتی stale پس از پایان عمر کسب‌وکار ارسال نشود.
- همه retryها `retry_after` را رعایت کنند.
- اولویت `M0` تا `M7`، ترتیب انتقال feederها و تمام رتبه‌های داخلی بخش ۳ در تست اثبات شود.
- با حداقل ۳۰۰ job آماده `M0/M1` در lane primary که دست‌کم ۲۰۰ مورد آن private/admin و خارج gate کانال‌اند، و ۱۰۰ edit آماده در lane editor، editor باید در حالی که backlog primary هنوز صفر نشده claim و dispatch کند؛ تنها بازه‌های دارای شاهد gate مقصد خود editor یا circuit مشترک از این معیار مستثنا هستند و انتظار صرفاً به‌علت priority lane دیگر باید صفر باشد. زیرآزمون جداگانه publication کانال باید انتظار مشروع gate مشترک را از توقف نرم‌افزاری تفکیک کند.
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
- backlog، sleep، retry یا priority primary نباید token budget، semaphore یا worker editor را اشغال کند و برعکس؛ metric تفکیکی باید زمان انتظار ناشی از lane و destination gate را جدا نشان دهد.
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
