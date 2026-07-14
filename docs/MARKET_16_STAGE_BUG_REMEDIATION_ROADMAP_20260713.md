# Roadmap رفع باگ‌های بازار در ۱۶ مرحله

## وضعیت سند

- وضعیت: roadmap اجرایی بازبینی‌شده؛ تأیید مشروط به رعایت dependency و gateهای همین سند
- تاریخ ایجاد: ۲۰۲۶-۰۷-۱۳
- آخرین بازبینی مستقل: ۲۰۲۶-۰۷-۱۴
- baseline کشف و اعتبارسنجی findingها: `main@6dfde01f07bf7ae104e8a5dc4c1a8d075d1281a2`
- برنچ اجرای remediation: `candidate/market-16-stage-remediation`
- محل نگهداری: `docs/MARKET_16_STAGE_BUG_REMEDIATION_ROADMAP_20260713.md` و tracked در Git
- دامنه: بازار، ثبت و اجرای معامله، همگام‌سازی دو سرور، اعلان‌های بازار و صفحات مرتبط در WebApp

این roadmap دقیقاً ۱۶ stage دارد. در هر stage فقط یک finding بسته می‌شود. اگر هنگام اجرا ایراد جدیدی کشف شود، در گزارش همان stage ثبت می‌شود اما بدون تأیید جداگانه به دامنه stage اضافه نخواهد شد.

## تفکیک baseline و وضعیت اجرا

- verdict مربوط به وجود هر باگ فقط از کد `main@6dfde01f` گرفته شده است.
- عبارت «بسته شد» در Stageهای ۱ تا ۴ به معنی پیاده‌سازی، تست و deploy روی staging در برنچ candidate است؛ به معنی وجود fix روی `main` نیست.
- گزارش‌های اجرای Stageهای بسته‌شده بخشی از تاریخچه اجرایی همین roadmap هستند و حذف یا با baseline اصلی مخلوط نمی‌شوند.
- production deploy، merge به `main` و صحت نسخه production همچنان gateهای مستقل هستند.

## بازبینی مستقل سه ایجنت

سه گزارش فقط‌خواندنی، با تمرکز بر همین ۱۶ finding و بر مبنای `main@6dfde01f` بررسی شدند:

| بازبین | فایل محلی گزارش | SHA-256 | نتیجه اعلامی |
|---|---|---|---|
| Gemini | `tmp/gemini/market-16-stage-roadmap-main-review.md` | `0306fcaac52be67869900ab14676b43a8781ab222002e91f707e203a25e7e74f` | تأیید ۱۶ finding با اصلاحات |
| Claude | `tmp/claude/market-16-stage-roadmap-main-review.md` | `bb251fe68b6d2a2ac5efef67d6cf581723c36e9fb927d6afc0d70b168c5045bc` | تأیید ۱۶ finding با اصلاح dependency و scope |
| ChatGPT | `tmp/chatgpt/1-market-16-stage-roadmap-main-chatgpt-review.md` | `7faef9993895be753dd1dad45f7f6ec948be3d21ccab3e7a897007dcf6681686` | تأیید ۱۵ finding و تأیید جزئی Stage ۱۶ |

### تصمیم نهایی پس از راستی‌آزمایی

- findingهای ۱ تا ۱۵ روی `main` تأیید می‌شوند.
- Stage ۱۶ نیز باگ واقعی است، اما دامنه آن محدود شد: API، بعضی eventها، frontend و چند مسیر بات unsafe هستند؛ canonical sync payload و Telegram channel publication از قبل صفر را درست حفظ می‌کنند و فقط regression test می‌گیرند.
- تناقض داخلی گزارش Gemini در Stageهای ۱ و ۴ ــ `NO` در متن ولی `YES` در استدلال و جدول نهایی ــ رأی مستقل محسوب نشد؛ شواهد کد و جمع‌بندی نهایی آن گزارش با تأیید باگ همسو است.
- پیشنهاد تغییر `home_server` آفر جایگزین به home آفر قدیمی رد شد. invariant پروژه حفظ می‌شود: آفر WebApp، `Iran-home` و آفر بات، `foreign-home` است.
- پیشنهاد یک quota ledger مشترک برای Stageهای ۴ و ۷ رد شد؛ این دو stage policy متفاوت و endpoint متفاوت دارند. فقط قرارداد ترتیب قفل و جلوگیری از deadlock باید مشترک باشد.
- ادعای اینکه close آفرها فقط روی foreign انجام می‌شود رد شد. Iran transition آفرهای Iran-home را می‌بندد و foreign پس از sync/autonomy آفرهای foreign-home را reconcile می‌کند؛ fencing باید برای هر home محلی و بدون distributed DB lock طراحی شود.
- پیشنهادهای مربوط به public identity در فرمان‌های بین‌سروری، receipt پایدار، fail-closed، rollout additive، dependency Stageهای ۱۰/۱۱ و محدودسازی Stage ۱۶ پذیرفته شدند.

## هدف

رفع ۱۶ مشکل شناسایی‌شده بدون بازنویسی معماری بازار و بدون آسیب به رفتارهای تثبیت‌شده بات، WebApp، همگام‌سازی و مالکیت داده بین دو سرور.

## اصول غیرقابل تغییر

- WebApp و API اصلی کاربران ایران همچنان در سمت ایران و بات تلگرام در سمت خارج باقی می‌مانند.
- هر mutation باید بر اساس `home_server` و مرجع واقعی داده انجام شود؛ mirror سرور مقابل مرجع نوشتن نیست.
- idempotency نباید فقط جلوی درخواست تکراری را بگیرد؛ درخواست تکراری با payload متفاوت باید صریحاً رد شود.
- تست خودکار نباید روی production معامله، آفر یا اعلان واقعی ایجاد کند.
- هر stage باید تغییر مستقل، تست مستقل، گزارش مستقل و مسیر rollback مشخص داشته باشد.
- production deploy بخشی از بسته‌شدن خودکار stage نیست و فقط پس از تأیید صریح انجام می‌شود.
- تغییرات UI نباید ضعف backend را پنهان کنند؛ کنترل مالی و امنیتی در backend قطعی است.
- زمان و مرز روز مطابق استاندارد timezone فعلی پروژه باقی می‌ماند.

## دروازه مشترک بسته‌شدن هر Stage

هر stage فقط زمانی بسته است که همه موارد مرتبط زیر انجام شده باشد:

1. تست‌های متمرکز همان finding نوشته و سبز شده باشند.
2. تست‌های regression نزدیک به بخش تغییرکرده اجرا و سبز شده باشند.
3. در تغییرات frontend، build و تست تعامل کاربر پاس شده باشد.
4. در تغییرات دو سروری، سناریوی home/remote، قطع ارتباط، retry و رسیدن دیرهنگام sync پوشش داده شده باشد.
5. migration احتمالی دارای مسیر upgrade، سازگاری نسخه مختلط و rollback بررسی‌شده باشد.
6. لاگ‌ها شامل secret، شماره تلفن، payload حساس یا جزئیات معامله غیرمجاز نباشند.
7. staging smoke test بدون خطای جدید، معامله تکراری یا اعلان تکراری تمام شده باشد.
8. گزارش stage شامل فایل‌های تغییرکرده، تست‌ها، نتایج، ریسک باقیمانده و روش rollback باشد.
9. هر command بین‌سروری دارای `command_id`، fingerprint canonical، receipt پایدار، retention مشخص و رفتار نسخه مختلط باشد.
10. rolloutهای دارای schema ابتدا additive باشند و حذف schema یا receipt تا پایان retry/drain window ممنوع باشد.
11. گزارش بسته‌شدن فقط تعداد تست را ذکر نکند؛ test ID یا suite دقیق، commit SHA و hash artifactهای اصلی را نیز ثبت کند.
12. در peer unavailable، capability mismatch یا پاسخ مبهم، mutation مالی و authority-sensitive باید fail-closed و قابل retry باشد.

## ترتیب و وابستگی‌ها

| Stage | Finding | موضوع | ریسک | وابستگی |
|---:|---|---|---|---|
| ۱ | `MKT-01` | جلوگیری از نشت اطلاعات realtime | بحرانی | ندارد |
| ۲ | `MKT-03` | نگهداری کلید معامله در پاسخ مبهم | بحرانی | Stage ۳ برای production release |
| ۳ | `MKT-09` | replay معامله قبل از guardهای متغیر | بحرانی | Stage ۲ |
| ۴ | `MKT-02` | اعمال واقعی محدودیت‌های مشتری | بالا | ندارد |
| ۵ | `MKT-04` | republish مستقل در نشست جاری بازار | بالا | ندارد |
| ۶ | `MKT-06` | بستن race ثبت آفر و بسته‌شدن بازار | بالا | ندارد |
| ۷ | `MKT-08` | اتمیک‌کردن محدودیت‌های ثبت آفر | بالا | Stage ۶؛ هماهنگی lock-order با Stage ۴ |
| ۸ | `MKT-07` | جداسازی مقایسه قیمت نقد و فردا | بالا | ندارد |
| ۹ | `MKT-10` | جلوگیری از گرسنگی صف workerهای ترمیم | بالا | ندارد |
| ۱۰ | `MKT-11` | cancel-all دقیق و مقاوم در رقابت | بالا | Stage ۱۱ برای production release |
| ۱۱ | `MKT-13` | idempotency انقضای forwarded offer | بالا | schema افزایشی موجود؛ runtime مستقل از Stage ۵ |
| ۱۲ | `MKT-05` | pagination کامل بازار فعال | متوسط | Stage ۱۴ و Stage ۱۶ برای release |
| ۱۳ | `MKT-12` | pagination کامل تاریخچه معاملات | متوسط | Stage ۱۶ برای release |
| ۱۴ | `MKT-14` | حذف تحویل دوگانه realtime | متوسط | Stage ۱ |
| ۱۵ | `MKT-15` | تطبیق payload در idempotency ثبت آفر | بالا | Stageهای ۵، ۶، ۷ و ۸ |
| ۱۶ | `MKT-16` | حفظ مقدار صفر در serialization | متوسط | ندارد |

شماره Stage شناسه ثابت finding است و الزاماً ترتیب زمانی اجرا نیست. ترتیب کم‌ریسک پیشنهادی برای مراحل باقی‌مانده:

`۵ → ۶ → ۷ → ۸ → ۹ → ۱۱ → ۱۰ → ۱۴ → ۱۶ → ۱۲ → ۱۳ → ۱۵`

- Stage ۵ دیگر command انقضا یا receipt برای republish نمی‌سازد، چون آفر منبع را اصلاً mutation نمی‌کند. migration افزایشی receipt که قبلاً روی staging اعمال شده برای حفظ Alembic و استفاده احتمالی Stage ۱۱ نگه داشته می‌شود، اما runtime Stage ۵ به آن وابسته نیست.
- Stage ۱۰ تا بسته‌شدن Stage ۱۱ release نمی‌شود.
- Stageهای ۱۲ و ۱۳ می‌توانند توسعه یابند، اما release آن‌ها پس از اصلاح realtime/zero semantics انجام می‌شود.

---

## Stage ۱ - بستن نشت اطلاعات Realtime (`MKT-01`)

### مشکل

مسیر عمومی SSE و broadcast سراسری WebSocket می‌تواند جزئیات معامله را به کاربر ناشناس یا کاربری که طرف معامله نیست برساند.

### نتیجه مورد انتظار

- هیچ کاربر ناشناس به رویداد معامله دسترسی نداشته باشد.
- رویداد خصوصی معامله فقط به کاربران مجاز همان معامله و نقش‌های صریحاً مجاز برسد.
- رویداد عمومی بازار فقط داده‌ای را حمل کند که همین حالا از API عمومی بازار قابل مشاهده است.

### محدوده تغییر

- سیاست احراز هویت و authorization در `api/routers/realtime.py`
- شیوه انتشار `trade:created` و رویدادهای مرتبط در `api/routers/trades.py`
- recipient routing در SSE، WebSocket و مسیر Redis/multi-worker
- حذف فیلدهای خصوصی از eventهای عمومی به‌جای اتکا به مخفی‌سازی frontend

### تست‌های اجباری

- اتصال ناشناس به stream خصوصی با `401/403` رد شود.
- خریدار، فروشنده و نقش‌های مجاز فقط داده مجاز خود را ببینند.
- کاربر عادی دیگر، مشتری نامرتبط و کاربر مسدود جزئیات معامله را نبینند.
- reconnect و resume stream باعث replay اطلاعات غیرمجاز نشود.
- انتشار از worker دیگر از طریق Redis همان فیلتر دسترسی را حفظ کند.
- رویدادهای عمومی آفر همچنان برای بازار کار کنند و فیلد خصوصی نداشته باشند.

### معیار خروج

در تست چندکاربره و multi-worker هیچ trade payload به recipient غیرمجاز نرسد و رفتار realtime عمومی بازار حفظ شود.

### ریسک و rollback

ریسک اصلی قطع‌شدن ناخواسته refresh بازار است. rollback باید فقط routing جدید realtime را برگرداند و به منطق ثبت معامله دست نزند.

### تحلیل اثر پیش از اجرا - ۲۰۲۶-۰۷-۱۳

وضعیت Stage: بسته شد؛ پیاده‌سازی، تست محلی، deploy و smoke دو سرور staging کامل شده است.

یافته قطعی:

- رویداد عمومی `trade:created` شامل شماره و شناسه معامله، قیمت، تعداد، شناسه و نام طرفین، اطلاعات پروفایل، مسیر مشتری و فهرست audience است.
- `sanitize_payload` فعلی فقط چند نام فیلد مشخص را در سطح اول حذف می‌کند و این اطلاعات تجاری/هویتی را از payload عمومی حذف نمی‌کند.
- تمام WebSocketهای احرازشده کانال عمومی معامله را subscribe می‌کنند و SSE اختیاری فعلی اتصال ناشناس را نیز می‌پذیرد.
- WebApp هیچ listener فعالی برای `trade:created` ندارد. به‌روزرسانی بازار از رویدادهای آفر و polling یک‌ثانیه‌ای انجام می‌شود.
- اعلان قابل مشاهده معامله از کانال خصوصی `notifications:{user_id}` و delivery receiptهای WebApp می‌آید؛ بات تلگرام نیز مسیر جداگانه خود را دارد.
- payloadهای عمومی آفر که از SQLAlchemy event تولید می‌شوند، payload کامل sync را حمل می‌کنند و علاوه بر داده بازار شامل فیلدهای داخلی authority/publication هستند؛ denylist فعلی برای این مسیر کافی نیست.

محدوده امن پیشنهادی برای پیاده‌سازی:

1. انتشار عمومی payload کامل `trade:created` متوقف شود؛ رویداد خصوصی همان معامله فقط برای audience معتبر باقی بماند.
2. کانال `events:trade:created` از subscription عمومی WebSocket و SSE حذف شود.
3. `offer:updated` عمومی حفظ شود، چون بازار برای حذف/اصلاح کارت آفر به `id`، `status`، `remaining_quantity` و `lot_sizes` نیاز دارد.
4. `offer:created` عمومی فقط projection مجاز بازار را منتشر کند؛ داده sync، authority، actor، channel binding و idempotency وارد realtime عمومی نشوند.
5. SSE خصوصی احراز هویت اجباری داشته باشد و token نامعتبر به اتصال ناشناس downgrade نشود. endpoint احرازشده اعلان‌ها و WebSocket فعلی حفظ شوند.
6. ConnectionManager، اتصال مشترک WebSocket و کانال‌های `message`، `chat:message` و session در این stage بازطراحی نشوند.
7. دوگانگی Redis + direct broadcast در Stage ۱ بازطراحی نشود؛ آن موضوع مستقل `MKT-14` در Stage ۱۴ است.

ارزیابی ریسک:

| بخش | ریسک با تغییر محدود پیشنهادی | دلیل |
|---|---|---|
| ثبت و commit معامله | بسیار کم | تغییر بعد از commit و فقط در delivery event است. |
| بازار فعال | کم | frontend به `trade:created` وابسته نیست و polling یک‌ثانیه‌ای fallback دارد. |
| باز/بسته‌شدن بازار | متوسط در صورت تغییر transport؛ کم در طرح پیشنهادی | eventهای market باید بدون تغییر باقی بمانند. |
| اعلان WebApp معامله | متوسط | کانال خصوصی و audience حسابداران/مشتریان نباید حذف یا عمومی شوند. |
| بات تلگرام | بسیار کم | مسیر پیام و delivery بات از realtime WebApp جداست. |
| چت و ابطال نشست | متوسط در صورت بازطراحی WebSocket؛ کم در طرح پیشنهادی | همه این قابلیت‌ها از WebSocket مشترک استفاده می‌کنند. |
| sync دو سرور | بسیار کم | realtime طبق قرارداد فعلی local side effect است و change log مستقل می‌ماند. |
| کلاینت‌های ناشناخته SSE | متوسط | در repo مصرف‌کننده frontend وجود ندارد، اما احراز هویت اجباری ممکن است کلاینت خارجی ثبت‌نشده را متوقف کند. |

نتیجه تحلیل:

- اصلاح عجولانه ConnectionManager یا احراز هویت کل WebSocket می‌تواند بازار، اعلان‌ها، چت و session را همزمان مختل کند و مجاز نیست.
- اجرای محدود بالا اختلالی در منطق مالی بازار ایجاد نمی‌کند. بدترین اثر قابل انتظار در failure رویداد آفر، تأخیر نمایشی حدود یک polling interval است؛ backend همچنان مرجع قطعی معامله و وضعیت بازار باقی می‌ماند.
- قبل از بسته‌شدن Stage باید تست چندکاربره، حسابدار فعال/غیرفعال، زنجیره مشتری، SSE ناشناس/معتبر، multi-worker Redis و regression بازار اجرا شود.

### گزارش اجرای Stage ۱ - ۲۰۲۶-۰۷-۱۳

پیاده‌سازی محدود و کم‌ریسک:

- برنچ مستقل `candidate/market-16-stage-remediation` از `main@6dfde01f` ساخته شد.
- انتشار عمومی `trade:created` حذف شد؛ recipientها و payloadهای خصوصی فعلی بدون تغییر در محاسبه audience روی `notifications:{user_id}` باقی ماندند.
- subscription عمومی `events:trade:created` از WebSocket و SSE حذف شد.
- SSE از حالت optional به احراز هویت اجباری تغییر کرد و اتصال ناشناس با `401` رد می‌شود.
- رویدادهای عمومی realtime بر اساس allowlist مستقل هر event project می‌شوند؛ event خصوصی یا ناشناخته روی کانال عمومی fail-closed است.
- ورودی JSON خراب یا غیر-object از Redis به WebSocket/SSE باعث نشت raw payload یا توقف listener نمی‌شود.
- ConnectionManager، منطق مالی و commit معامله، محاسبه audience، delivery receipt، بات تلگرام و sync log تغییر نکردند.

شواهد تست:

- focused realtime/trade suite: `50` تست پاس.
- regression نزدیک آفر، معامله، اعلان، WebSocket و delivery: `142` تست پاس.
- `py_compile` و `git diff --check`: پاس.
- اجرای سه suite قدیمی `test_core_events`، `test_sync_router_receive_basic` و `test_sync_router_receive_offer_publish` روی branch و checkout تمیز `main@6dfde01f` در هر دو حالت دقیقاً `15 failure + 1 error` داشت؛ این baseline failureها به Stage ۱ منتسب نیستند و خارج از محدوده این stage باقی ماندند.
- frontend تغییر نکرده است و مصرف‌کننده‌ای برای `trade:created` یا SSE در repo وجود ندارد؛ اجرای unit frontend به علت نبود `frontend/node_modules` در checkout فعلی انجام نشد.
- نسخه `0a89ed498271` روی WebApp/API ایران در `staging.gold-trade.ir` و سرویس‌های staging خارجی در `staging.362514.ir` deploy شد؛ `app`های هر دو سمت healthy و workerهای sync و bot مربوط به staging در حال اجرا هستند.
- `/api/config` ایران `200`، guard سطح عمومی خارجی `404`، SSE با bearer نامعتبر `401` و SSE با کاربر معتبر staging `200` پاسخ داد.
- policy داخل کانتینرهای اجراشده تأیید کرد `trade:created` خصوصی است، WebSocket/SSE کانال عمومی آن را subscribe نمی‌کنند و projection آفر عمومی فیلدهای خصوصی را حذف می‌کند.
- `/api/sync/health` در هر دو سمت mode صحیح را گزارش داد؛ `unsynced_change_log_count` و صف‌های `sync:outbound` و `sync:retry` همگی صفر بودند.
- از زمان deploy در `app`، `sync_worker` و `bot`های staging هیچ لاگ با سطح `ERROR/CRITICAL` و هیچ traceback ثبت نشد.
- کانتینرهای production در هر دو سرور بازسازی یا restart نشدند و deploy فقط compose projectهای staging را تغییر داد.

ریسک باقیمانده و rollback:

- کلاینت SSE ثبت‌نشده‌ای که بیرون از repo بدون bearer token متصل می‌شده، دیگر دسترسی ندارد؛ این شکست عمدی امنیتی است.
- دوگانگی Redis و direct broadcast طبق scope به Stage ۱۴ موکول شده و در این stage دست‌کاری نشده است.
- rollback محدود به بازگرداندن تغییرات `api/routers/realtime.py` و بخش انتشار realtime در `api/routers/trades.py` است؛ schema، migration و داده‌ای تغییر نکرده‌اند.
- Stage با تست‌های چندکاربره و Redis/multi-worker محلی و smoke احرازشده دو سرور staging بسته شد؛ هیچ schema، migration یا داده production تغییر نکرد.

---

## Stage ۲ - حفظ Idempotency Key معامله در پاسخ‌های مبهم (`MKT-03`)

**وضعیت: بسته شد روی staging - commit `4ac3e2b76c0a`**

### مشکل

WebApp کلید idempotency را فقط در حافظه صفحه نگه می‌داشت. هرچند `5xx`های تبدیل‌شده به `NetworkError` در همان اجرای صفحه key را حفظ می‌کردند، refresh/remount، `AbortError`، حذف موقت offer از فهرست یا پاسخ دیرهنگام context قبلی می‌توانستند intent مبهم را گم یا اشتباه پاک کنند. در این وضعیت ممکن است معامله در backend ثبت شده باشد اما retry با کلید جدید معامله دوم بسازد.

### نتیجه مورد انتظار

- تا زمانی که نتیجه قطعی درخواست مشخص نشده، همان key و همان payload حفظ شود.
- timeout، قطع شبکه و خطاهای موقت key جدید نسازند.
- key فقط پس از موفقیت قطعی یا کنارگذاشتن صریح عملیات پاک شود.

### محدوده تغییر

- state درخواست معامله در `frontend/src/components/OffersList.vue`
- طبقه‌بندی پاسخ‌های قطعی و مبهم
- جلوگیری از double-click و retry موازی برای یک intent

### تست‌های اجباری

- پس از timeout و `500/502/503` retry دقیقاً همان key و payload را بفرستد.
- پاسخ دیرهنگام درخواست اول و retry همزمان فقط یک نتیجه قابل نمایش ایجاد کند.
- موفقیت قطعی key را ببندد و درخواست معامله جدید key تازه بگیرد.
- خطای validation قطعی، تغییر مقدار توسط کاربر و لغو صریح intent رفتار مشخص داشته باشند.
- refresh یا بازشدن دوباره modal در فاصله نتیجه مبهم، intent قبلی را تصادفی تکثیر نکند.

### معیار خروج

در شبیه‌سازی «commit موفق + پاسخ گمشده»، WebApp فقط همان درخواست را replay کند و هیچ key تازه‌ای برای همان intent نسازد.

### ریسک و rollback

ریسک اصلی گیرکردن key قدیمی برای عملیات واقعاً جدید است. مرز پایان intent باید تست‌پذیر و قابل مشاهده باشد.

### گزارش اجرا

- بررسی baseline نشان داد `apiFetch` خطاهای `5xx` را از قبل به `NetworkError` تبدیل می‌کند و در همان اجرای صفحه key حفظ می‌شد؛ شکاف‌های واقعی شامل `AbortError`، refresh/remount، حذف موقت offer از لیست و پاسخ دیرهنگام context قبلی بود.
- intent حل‌نشده اکنون با `offer_id`، مقدار، idempotency key، وضعیت و زمان‌ها در `sessionStorage` و با کلید مستقل هر کاربر نگهداری می‌شود؛ scope خود مرورگر نیز محیط‌های staging و production را از هم جدا می‌کند.
- پاسخ‌های `408/425/429`، همه `5xx`ها، timeout، قطع شبکه و `AbortError` نتیجه مبهم محسوب می‌شوند و retry دقیقاً همان key و payload را می‌فرستد.
- موفقیت قطعی، validation قطعی یا ناموجودشدن قطعی lot intent را می‌بندد؛ ناپدیدشدن موقت offer از فهرست، refresh و بازشدن دوباره component آن را حذف یا تکثیر نمی‌کنند.
- برای یک offer با intent حل‌نشده، مقدار متفاوت و retry موازی رد می‌شود. پاسخ دیرهنگام component قبلی یا کاربر قبلی نیز اجازه تغییر state جاری یا نمایش نتیجه تکراری ندارد.
- حذف دستی و چشم‌بسته intent مبهم اضافه نشد؛ تا روشن‌شدن نتیجه، WebApp عملیات جدیدی با key تازه برای همان offer نمی‌سازد.
- backend، schema، migration، sync، محاسبات بازار، اعلان‌ها و بات در این stage تغییر نکردند.

شواهد تست و استقرار:

- focused `OffersList`: تعداد `32` تست پاس، شامل ماتریس `408/425/429/500/502/503/504`، network/abort، refresh/remount، پاسخ دیرهنگام، حذف موقت offer، تغییر مقدار، storage failure و داده ذخیره‌شده خراب.
- کل frontend unit suite: تعداد `1153` تست در `129` فایل پاس.
- regression backend معامله، idempotency و forwarding: تعداد `99` تست پاس.
- `vue-tsc --noEmit`، build تولیدی Vite/PWA و `git diff --check`: پاس.
- نسخه Stage ۲ روی staging خارج و WebApp/API ایران در `staging.gold-trade.ir` deploy شد. SHA فایل `index.html` محلی، فایل منتقل‌شده و پاسخ سرو‌شده برابر `8efaf2d8a69db557f29e5b3c635cd8d6c76331046bf981f8da6fcdbb5b35c989` است و bundle سرو‌شده marker مربوط به intent جدید را دارد.
- کانتینرهای staging ایران SHA کامل `4ac3e2b76c0a7a1154d1c98a76977e7b405f5a6d` و کانتینرهای staging خارج SHA کوتاه متناظر `4ac3e2b76c0a` را گزارش کردند.
- `/api/config` ایران `200`، guard عمومی خارج `404` و `/api/sync/health` هر دو سمت `status=ok` با `unsynced_change_log_count=0` و صف‌های `sync:outbound=0` و `sync:retry=0` بود. از زمان deploy هیچ `ERROR/CRITICAL` یا traceback تازه‌ای در سرویس‌های staging ثبت نشد.

ریسک باقیمانده:

- Stage ۲ از ساخت معامله دوم با key تازه جلوگیری می‌کند، اما تا پیش از Stage ۳ ممکن است replay همان key بعد از تغییر guardهای بازار به‌جای نتیجه قطعی قبلی خطا بگیرد و intent حل‌نشده بماند. بنابراین Stage ۲ به‌تنهایی کاندید انتشار production نیست و Stage ۳ باید پیش از release تکمیل شود.
- rollback فقط revert تغییرات `OffersList.vue` و تست آن است؛ داده server-side و migration برای rollback وجود ندارد.

---

## Stage ۳ - Replay معامله پیش از Guardهای متغیر (`MKT-09`)

**وضعیت: بسته شد روی staging - commit `3eb7b736d269f3068652128a6ec0a6c9e7472483`**

### مشکل

backend نتیجه idempotent قبلی را بعد از کنترل‌هایی مثل بازبودن بازار یا محدودیت روزانه بررسی می‌کند. retry یک معامله موفق ممکن است فقط به‌دلیل تغییر وضعیت بازار یا سهمیه با خطا پاسخ بگیرد.

### نتیجه مورد انتظار

- پس از احراز هویت، تعیین actor و تطبیق payload، نتیجه قطعی قبلی قبل از guardهای متغیر برگردد.
- replay هیچ mutation، اعلان، کمیسیون یا sync جدیدی ایجاد نکند.
- درخواست با همان key و payload متفاوت همچنان conflict قطعی باشد.

### محدوده تغییر

- ترتیب guardها و lookup idempotency در `api/routers/trades.py`
- قرارداد پاسخ replay و ثبت fingerprint درخواست
- جلوگیری از تکرار side effectهای پس از commit

### تست‌های اجباری

- معامله commit شود، بازار بسته شود و retry همان پاسخ موفق قبلی را بگیرد.
- پس از پرشدن سهمیه روزانه، replay معامله قبلی موفق بماند.
- تغییر وضعیت offer بعد از commit مانع replay پاسخ قبلی نشود.
- کاربر غیرمجاز نتواند با دانستن key پاسخ معامله دیگری را بگیرد.
- payload متفاوت با key قبلی `409` بگیرد.
- replay اعلان، پیام حسابدار، sync یا trade row دوم نسازد.

### معیار خروج

replay معتبر مستقل از وضعیت متغیر بعدی سیستم باشد، اما authentication، ownership و payload matching را دور نزند.

### ریسک و rollback

جابجایی بیش‌ازحد guardها می‌تواند authorization را دور بزند. احراز actor و تطبیق مالکیت باید همیشه قبل از replay باقی بماند.

### گزارش اجرا

- lookup فقط‌خواندنی ledger با دامنه `home_server + idempotency_key` پس از guardهای ثابت امنیتی و پیش از guardهای متغیر بازار، محدودیت روزانه و وضعیت offer قرار گرفت.
- پیش از lookup، advisory lock تراکنشی همان idempotency key گرفته می‌شود تا replay هم‌زمان با تکمیل درخواست اصلی نتیجه نیمه‌کاره نبیند.
- فقط ledger با وضعیت `COMPLETED_TRADE` اجازه replay دارد. درخواست تازه، ledger ردشده یا ledger ناتمام همچنان تمام guardهای قبلی را طی می‌کند.
- تطبیق سخت ledger شامل سرور مرجع، offer داخلی و عمومی، requester، actor، quantity و idempotency key است. نتیجه معامله نیز از نظر trade id، offer، responder، actor، quantity و key دوباره کنترل می‌شود؛ هر اختلاف `409` می‌دهد.
- اگر ledger تکمیل‌شده به trade معتبر اشاره نکند، مسیر fail-closed با `503` متوقف می‌شود و معامله جدید ساخته نمی‌شود. Stage ۲ این پاسخ مبهم را نگه می‌دارد تا کاربر با همان key retry کند.
- replay از مسیر موجود ترمیم delivery receipt استفاده می‌کند، اما trade، offer، شمارنده‌ها، کمیسیون، sync یا اعلان جدید ایجاد نمی‌کند. eager-load شدن offer باعث شد `offer_notes` پاسخ قبلی نیز حفظ شود.
- یک خطای قدیمی در ثبت رخداد mismatch که می‌توانست به‌دلیل آرگومان پشتیبانی‌نشده خودش exception بسازد، در همان مسیر با فیلد استاندارد logger اصلاح شد.
- schema، migration، frontend، قرارداد sync و منطق درخواست تازه در این stage تغییر نکردند.

شواهد تست و استقرار:

- تست‌های focused ترتیب guard، replay و ledger: `64` پاس؛ شامل بازار بسته، سهمیه پر، offer تغییرکرده، WATCH/inactive، ماتریس mismatch، trade مفقود، side-effect صفر و حفظ notes.
- مجموعه کنترل‌شده `test_trade*.py`: تعداد `240` تست پاس.
- مجموعه ledger، forwarding و اجرای معامله بات: تعداد `53` تست پاس.
- تست‌های delivery، contention و notification: تعداد `94` تست پاس؛ regression گسترده authoritative/forwarding/bot نیز `117` تست پاس.
- تست frontend مرتبط با حفظ intent در پاسخ `503`: تعداد `32` تست پاس. تست observability تعداد `5` پاس؛ `py_compile` و `git diff --check` نیز پاس شدند.
- staging خارج و ایران با SHA کامل `3eb7b736d269f3068652128a6ec0a6c9e7472483` اجرا شدند. introspection در هر دو runtime تأیید کرد lookup replay پیش از guard بازار است.
- سلامت sync هر دو سمت `ok`، صف‌های `sync:outbound` و `sync:retry` صفر و شمار unsynced ایران صفر بود. لاگ سرویس‌های تازه‌شده هیچ traceback، exception، critical یا پاسخ `500` نشان نداد.
- WebApp ایران پشت احراز هویت Nginx پاسخ `401` و سطح عمومی خارج طبق guard پاسخ `404` داد. کانتینرهای production هر دو سرور بازسازی نشدند و فقط staging deploy شد.
- هشدار reconciliation ایران به دو offer فعال staging بدون webapp publication state مربوط است؛ backlog سینک و خطای Telegram صفر است و این stage هیچ publication state یا داده بازار را تغییر نداده است.

ریسک باقیمانده:

- contention gate بیرونی offer عمداً در این stage جابه‌جا نشد. بنابراین replay در بازه کوتاهی که همان offer توسط عملیات دیگری قفل است ممکن است موقتاً `409` بگیرد، اما نتیجه قطعی از بین نمی‌رود و retry بعدی با همان key قابل انجام است. تغییر این gate بدون تحلیل جداگانه می‌توانست دامنه قفل و رفتار درخواست‌های هم‌زمان را ناخواسته عوض کند.
- rollback کد با revert همین commit ممکن است و migration یا داده جدیدی برای بازگردانی وجود ندارد.

---

## Stage ۴ - اعمال واقعی محدودیت‌های مشتری (`MKT-02`)

**وضعیت: بسته شد روی staging - commit `25af78c6b911e5980c6ec3a0c9010bd28cba1655`**

### مشکل

محدودیت‌های اختصاصی مشتری تعریف و قابل تنظیم هستند، اما validation مربوط به آن‌ها در مسیر واقعی انجام معامله به‌صورت کامل اجرا نمی‌شود.

### نتیجه مورد انتظار

- حداقل/حداکثر مقدار، حجم روزانه، تعداد معامله و محدودیت فعال مشتری در مسیر واقعی enforce شود.
- رفتار WebApp و بات یکسان باشد.
- پیام خطا روشن باشد و اطلاعات داخلی relation را افشا نکند.

### محدوده تغییر

- اتصال `validate_customer_trade_limits` یا سرویس canonical معادل به مسیر اجرای معامله
- پوشش هر دو نقش مشتری در معامله در صورت مجازبودن مدل کسب‌وکار
- استفاده از relation معتبر و فعال در سرور مرجع

### تست‌های اجباری

- مقادیر کمتر، برابر و بیشتر از min/max بررسی شوند.
- سقف تعداد و حجم روزانه در مرز دقیق و یک واحد بیشتر بررسی شود.
- relation منقضی، غیرفعال، نامرتبط و tierهای مختلف بررسی شوند.
- مسیر بات و WebApp نتیجه یکسان داشته باشند.
- تغییر روز بر اساس timezone فعلی پروژه شمارنده را درست جدا کند.
- کاربر عادی بدون relation تحت محدودیت مخصوص مشتری قرار نگیرد.

### معیار خروج

هیچ معامله مشتری خارج از policy ذخیره نشود و تمام مسیرهای ثبت معامله از یک validation canonical عبور کنند.

### ریسک و rollback

ریسک اصلی مسدودشدن معاملات مجاز به‌دلیل relation اشتباه یا داده قدیمی است. قبل از production باید داده واقعی فقط به‌صورت read-only audit شود.

### گزارش اجرا

Audit فقط‌خواندنی production پیش از تغییر:

- در هر دو سرور دقیقاً دو relation فعال، چهار relation حذف‌شده و یک دعوت pending وجود داشت.
- هر دو مشتری فعال هیچ Trade موفقی نداشتند و هر چهار policy شامل min/max، تعداد روزانه و حجم روزانه برای آن‌ها `NULL` بود.
- شناسه محلی relation بین دو سرور الزاماً یکسان نیست، اما وضعیت، tier، policy و سابقه معامله متناظر بود. هیچ داده production تغییر نکرد.

پیاده‌سازی کم‌ریسک:

- validator موجود به یک evaluator canonical با reason codeهای ثابت متصل شد؛ قرارداد قبلی `validate_customer_trade_limits` و پیام‌های داخلی آن برای callerهای موجود حفظ شد.
- مسیر authoritative معامله پس از معتبرشدن مقدار واقعی آفر و پیش از ساخت execution plan، Trade، تغییر آفر، کمیسیون، sync و اعلان، policy مشتری را enforce می‌کند.
- relation فعال مشتری پاسخ‌دهنده و مشتری صاحب آفر هر دو پوشش داده می‌شوند. در معامله customer-to-customer، policy هر مشتری مستقل بررسی می‌شود؛ کاربر عادی بدون relation بدون query یا محدودیت customer عبور می‌کند.
- تمام relationهای درگیر با `SELECT ... FOR UPDATE` و ترتیب صعودی ثابت قفل می‌شوند. درخواست‌های هم‌زمان روی آفرهای متفاوت یک مشتری نیز روی همان relation سریال می‌شوند و query مصرف روزانه پس از گرفتن قفل اجرا می‌شود.
- فقط Tradeهای `COMPLETED` که مشتری در یکی از دو سر واقعی آن‌هاست شمرده می‌شوند. به این ترتیب legهای واسط owner در chain به‌عنوان معامله اضافه مشتری محاسبه نمی‌شوند.
- مرز روز از timezone همان ارزیابی بازار گرفته و در UTC روی `Trade.created_at` اعمال می‌شود. مقدار دقیق سقف مجاز است و فقط درخواست بعد از سقف رد می‌شود.
- relation بدون policy همچنان آزاد است؛ تنها قفل کوتاه consistency گرفته می‌شود و query مصرف روزانه ندارد. این همان وضعیت فعلی دو مشتری production است.
- replay موفق Stage ۳ پیش از این guard برمی‌گردد و دوباره شمرده یا محدود نمی‌شود. رد policy در ledger به‌صورت terminal و idempotent ثبت می‌شود و هیچ Trade یا mutation آفر ایجاد نمی‌کند.
- برای مشتری پاسخ‌دهنده پیام فارسی روشن نمایش داده می‌شود. اگر policy متعلق به صاحب آفر باشد، طرف مقابل فقط پیام عمومی می‌گیرد و نوع یا مقدار محدودیت relation افشا نمی‌شود.
- schema، migration، frontend، جریان مستقل بات، مدل chain، کمیسیون، قرارداد sync و notification تغییر نکردند. بات و WebApp هر دو از همان command authoritative استفاده می‌کنند.

شواهد تست و استقرار:

- baseline پیش از تغییر: `100` تست سرویس relation، guard، success، execution seam و ledger پاس.
- ماتریس کنترل‌شده `test_trade*.py`: تعداد `242` تست پاس.
- تمام تست‌های customer: تعداد `82` تست پاس.
- تست‌های اجرای معامله بات: تعداد `25` پاس؛ ledger/forwarding/production-contract/observability تعداد `40` پاس.
- delivery تعداد `46` و notification تعداد `14` تست پاس.
- ماتریس sync تعداد `56` تست پاس و یک failure قدیمی داشت. همان failure `test_receiver_reorders_full_sync_batch_before_apply` روی snapshot تمیز commit قبلی `3eb7b736` عیناً تکرار شد و به Stage ۴ منتسب نیست.
- `py_compile` و `git diff --check` پاس شدند.
- staging خارج و ایران SHA متناظر `25af78c6b911...` را اجرا می‌کنند. introspection runtime تأیید کرد guard محدودیت پیش از execution plan قرار دارد و relation lock در کد اجراشده موجود است.
- smoke واقعی PostgreSQL در هر دو staging با user/relation موقت اجرا شد: relation بدون policy مجاز ماند، `max_daily_trades=0` همان transaction را رد کرد و rollback کامل عدم ماندگاری relation موقت را تأیید کرد.
- سلامت sync هر دو سمت `ok`، unsynced و صف‌های outbound/retry صفر و لاگ تمام سرویس‌های تازه‌شده بدون traceback، exception، critical یا `500` بود.
- endpoint ایران همچنان پشت احراز هویت Nginx با `401` و سطح عمومی خارج با `404` محافظت می‌شود. کانتینرهای production هر دو سرور بازسازی یا restart نشدند.

ریسک باقیمانده:

- قفل relation در محدوده یک PostgreSQL قطعی است. اگر ارتباط دو سرور کاملاً قطع باشد و یک مشتری هم‌زمان روی آفرهای home متفاوت در هر دو سرور معامله کند، مصرف روزانه سرور مقابل تا رسیدن sync قابل مشاهده نیست. حل سخت‌گیرانه این حالت نیازمند authority واحد آنلاین یا quota توزیع‌شده است و عمداً در این stage معماری موجود قطع ارتباط بازنویسی نشد. مسیر forwarding، retry و convergence فعلی بدون تغییر باقی مانده است.
- در صورت رسیدن مشتری صاحب آفر به سقف روزانه، آفر ممکن است همچنان در بازار دیده شود ولی execution آن با پیام عمومی رد می‌شود؛ expire خودکار سایر آفرهای مشتری خارج از scope این stage است.
- rollback با revert commit `25af78c6` انجام می‌شود. migration، داده جدید یا عملیات جبرانی برای rollback وجود ندارد.

---

## Stage ۵ - Republish مستقل در نشست جاری بازار (`MKT-04`)

**وضعیت: بسته شد روی staging - runtime commit `783374e93721adede18f071460172b7ac44ce145`**

### مشکل

رفتار قدیمی هنگام republish، آفر منبع را تغییر می‌داد و حتی می‌توانست با `require_authority=False` روی mirror سرور غیرمرجع mutation ایجاد کند. علاوه بر ریسک authority، آفر جدید به lifecycle آفر قبلی وابسته می‌شد و در quantity خُرد ممکن بود مقدار اولیه به‌جای مانده واقعی تکرار شود.

### نتیجه مورد انتظار

- آفر منبع در republish به‌هیچ‌وجه mutation، expire یا forward نشود؛ آفر جدید یک آفر مستقل با lifecycle، public ID، idempotency و side effectهای خودش باشد.
- فقط leaf منقضی‌شده‌ی متعلق به همان کاربر، از نشست فعلی بازار و حداکثر یک ساعت اخیر قابل تکرار باشد.
- فقط `time_limit` و لغو تکی `manual` قابل تکرار باشند. `cancel_all`، دستور بات «نشد» (`bot_cancel_all`)، `market_closed`، آفر fully-completed، مانده صفر و آفر archived قابل تکرار نباشند.
- آفر خُرد فقط با `remaining_quantity` و `lot_sizes` فعلی تکرار شود؛ `quantity` و `original_lot_sizes` منبع مجاز به بازگرداندن مقدار معامله‌شده نیستند.
- بعد از بسته‌شدن یا بازشدن بازار هیچ آفر نشست قبلی قابل تکرار نباشد و WebApp پیش‌نمایش تکرار باقی‌مانده از نشست قبل را باطل کند.
- زنجیره به‌شکل child-owned و immutable با `republished_from_offer_public_id` ثبت شود. وجود child، منبع را برای همیشه از فهرست leafها خارج کند؛ اگر child منقضی شد فقط همان child دیده شود و اگر child معامله شد هیچ موردی از آن زنجیره دیده نشود.

### محدوده تغییر

- endpoint خواندن leafهای قابل تکرار و مسیر create در `api/routers/offers.py`
- policy/service واحد برای session boundary، reason allowlist، مانده و current lot snapshot
- provenance افزایشی و یکتا بر اساس `offer_public_id`، بدون اتکا به peer-local integer ID
- sync payload و migration/backfill سازگار با lineage موقت Stage ۵ قبلی
- WebApp فقط برای بازار باز، با source public ID و idempotency پایدار

### تصمیم معماری تأییدشده

- source فقط خوانده و با row lock دوباره اعتبارسنجی می‌شود؛ هیچ فیلدی روی آن تغییر نمی‌کند. آفر جدید از surface جاری مالک home مستقل خود را می‌گیرد.
- public ID منبع روی child ذخیره می‌شود و unique constraint اجازه نمی‌دهد دو درخواست هم‌زمان از یک source دو child بسازند. retry با همان idempotency key همان نتیجه را می‌گیرد و key متفاوت conflict می‌گیرد.
- payload اقتصادی ارسالی باید دقیقاً با snapshot مجاز منبع برابر باشد تا client نتواند republish را به مسیر ثبت آفر دلخواه تبدیل کند.
- Stage ۵ فقط WebApp republish فعلی را پوشش می‌دهد. افزودن republish در بات در آینده نیازمند تصمیم جداگانه برای uniqueness بین دو home است.
- side effectهای Telegram، realtime، counter و Web Push همان مسیر canonical ثبت آفر جدید را طی می‌کنند؛ source هیچ channel edit یا event انقضای تازه دریافت نمی‌کند.

### تست‌های اجباری

- آفر منقضی `time_limit` و لغو تکی `manual` در نشست جاری قابل تکرار باشند.
- `cancel_all`، `bot_cancel_all`، `market_closed`، statusهای active/completed/cancelled، archived و مانده صفر در API و service رد شوند.
- آفر partial با مانده ۸ دقیقاً child مستقل با quantity و lotهای فعلی ۸ بسازد و source بدون تغییر بماند.
- A→B پس از ساخت B فقط B را در صورت انقضا نمایش دهد؛ completed شدن B کل زنجیره را از فهرست حذف کند.
- close و open جدید، لیست و پیش‌نمایش نشست قبلی را حذف کنند؛ source ساخته‌شده پیش از `last_transition_at` حتی اگر تازه update شده باشد برنگردد.
- payload دستکاری‌شده، source متعلق به کاربر دیگر، local ID ناسازگار و source public ID نامعتبر قبل از side effect رد شوند.
- دو درخواست هم‌زمان با keyهای متفاوت برای یک source حداکثر یک child بسازند؛ retry همان key counter، اعلان و Web Push را دوباره اجرا نکند.
- migration از صفر یک head بسازد، lineage قبلی را backfill کند و sync field جدید را بدون وابستگی به ID عددی منتقل کند.
- رفتار max-active quota، price validation، publication و realtime برای child دقیقاً مانند آفر جدید مستقل باشد.

### معیار خروج

در تمام سناریوها source بدون تغییر بماند، حداکثر یک child مستقل از آن ساخته شود، فقط leaf مجاز نشست جاری در WebApp دیده شود و lineage پس از sync روی هر دو سرور با public identity همگرا باشد.

### ریسک و rollback

ریسک باقیمانده به نسخه مختلط schema و stale mirror محدود می‌شود. rollout باید migration-first باشد؛ نسخه قدیمی ستون جدید را نادیده می‌گیرد و نسخه جدید در نبود runtime state معتبر fail-closed می‌شود. rollback کد با نگه‌داشتن ستون افزایشی امن است؛ حذف ستون تا پایان rollout و retry window ممنوع است.

### گزارش اجرای Stage ۵ - طراحی مستقل ۲۰۲۶-۰۷-۱۴

تصمیم قبلی Stage ۵ به‌صورت غیرمخرب revert شد و پیاده‌سازی از baseline تمیز و آخرین `main` بازسازی شد. migration قبلی `c9a4e7b2d615` به‌دلیل اعمال‌شدن روی staging از تاریخچه Alembic حذف نشد؛ runtime مربوط به mutate/forward/receipt قبلی حذف و migration merge افزایشی `d0b5e6f7a8c9` ایجاد شد.

پیاده‌سازی نهایی:

- policy واحد `offer_republish_service` فقط leafهای `EXPIRED` با reasonهای `time_limit` و `manual`، مانده مثبت، غیرآرشیوی، متعلق به کاربر و ساخته/منقضی‌شده در نشست باز فعلی را می‌پذیرد.
- create source را با public identity و row lock دوباره اعتبارسنجی می‌کند، payload اقتصادی را با مانده و lotهای فعلی تطبیق می‌دهد و سپس child مستقل می‌سازد. source، channel post و terminal event آن تغییر نمی‌کنند.
- unique provenance روی `republished_from_offer_public_id` رقابت دو child را در PostgreSQL می‌بندد. lineage در sync و full-seed با public ID حمل می‌شود و به ID عددی peer وابسته نیست.
- endpoint اختصاصی `/api/offers/my/repeatable` جای query عمومی قبلی را گرفت. WebApp toggle را در بازار بسته پنهان و preview تکرار را در هر transition بازار باطل می‌کند؛ انتخاب «ویرایش متن» provenance تکرار را پاک می‌کند و متن را مانند آفر عادی جدید پردازش می‌کند.
- quota، validation، Telegram publication، realtime، counter و Web Push child همان مسیر آفر جدید عادی را طی می‌کنند؛ آفر تکرارشده جای یک آفر فعال را رایگان نمی‌گیرد.

شواهد تست و migration:

- focused policy/API/model/sync/migration: `60` تست پاس.
- offer regression: `140`، trade regression: `242`، market transition/production contract: `43`، bot offer: `19` و Telegram/publication: `41` تست پاس.
- MarketView: `33` تست پاس؛ frontend build پاس شد. اجرای کل frontend `1153` تست پاس و یک timeout نامرتبط در `PublicProfile` داشت؛ همان فایل بلافاصله به‌تنهایی `42/42` پاس شد.
- `compileall`، `git diff --check` و single Alembic head پاس شدند.
- migration از دیتابیس خالی تا head، upgrade از lineage قبلی و backfill `source → child` روی PostgreSQL موقت پاس شد. درج child دوم برای همان source با unique constraint رد شد و فقط یک child باقی ماند؛ دیتابیس‌های موقت سپس حذف شدند.
- sync/event regression همان سه failure و یک error ثبت‌شده baseline را بدون failure جدید تکرار کرد؛ تست جدید upsert provenance و seed payload سبز است.

شواهد staging دو سرور:

- rollout به‌شکل migration-first انجام شد: ابتدا دیتابیس ایران و خارج به `d0b5e6f7a8c9` رسیدند، سپس runtimeها recreate شدند. در تمام فاصله نسخه قدیمی با schema افزایشی سالم ماند.
- Iran و foreign هر دو `RELEASE_SHA=783374e93721adede18f071460172b7ac44ce145`، environment برابر `staging` و server mode درست دارند. route جدید در هر دو image ثبت است و smoke بدون session روی endpoint ایران به `401` مورد انتظار رسید.
- error scan پنج دقیقه پس از deploy برای Iran app/sync worker و foreign app/bot/sync worker صفر match برای traceback، critical، internal 500 و unhandled error داشت.
- preflight read-only دو سرور با deep parity پاس شد: runtime identity، TLS، internal ingress، storage separation، sync health و manifest همگی سبز؛ ۲۳ جدول با business drift و critical drift صفر، پنج تفاوت صرفاً non-business، صف outbound/retry و unsynced backlog صفر بودند.
- هیچ full-matrix جهشی، mutation تستی یا production deploy/restart انجام نشد.

ریسک و handoff:

- race نهایی create با transition بسته‌شدن بازار عمداً برای Stage ۶ باقی است و Stage ۵ آن را پنهان یا بازنویسی نمی‌کند.
- exact replay با idempotency key یکسان و payload متفاوت finding مستقل Stage ۱۵ است. UI برای هر intent key تازه می‌سازد و unique provenance در Stage ۵ از child دوم جلوگیری می‌کند، اما قرارداد conflict کامل در Stage ۱۵ بسته می‌شود.
- republish فعلی فقط WebApp است. افزودن دکمه مشابه در بات بدون طراحی uniqueness بین دو home مجاز نیست.
- rollback کد با revert commit `783374e9` و بازگشت frontend انجام می‌شود. ستون child-owned، جدول receipt قبلی و merge migration additive در rollback فوری حذف نمی‌شوند.

---

## Stage ۶ - حذف Race بین ثبت آفر و بسته‌شدن بازار (`MKT-06`)

### مشکل

ممکن است بررسی «بازبودن بازار» انجام شود، سپس بازار بسته شود و آفر بعد از عملیات بستن بازار commit شود؛ در نتیجه آفر فعال خارج از زمان بازار باقی می‌ماند.

### نتیجه مورد انتظار

- تصمیم ثبت آفر و transition بسته‌شدن بازار نسبت به یکدیگر ترتیب قطعی داشته باشند.
- آفر یا پیش از transition کاملاً ثبت و سپس طبق close پردازش شود، یا پس از close قطعی رد شود.
- آفر ردشده هیچ پیام کانال یا event عمومی نسازد.
- Iran authority وضعیت runtime بازار را حفظ کند و هر home server فقط admission/expiry آفرهای home خودش را fence کند.

### محدوده تغییر

- guard نهایی ثبت آفر و هماهنگی آن با market schedule transition
- lock/fencing محلی و سازگار با authority فعلی بازار؛ هیچ distributed database lock بین دو سرور ساخته نشود
- هر دو مسیر ایجاد آفر در بات و WebApp

### تصحیح دامنه پس از بازبینی

- transition اصلی runtime روی Iran authoritative است و هنگام close آفرهای Iran-home را در همان مسیر می‌بندد.
- foreign پس از دریافت state همگام‌شده یا پس از autonomy grace، side effect و انقضای آفرهای foreign-home را reconcile می‌کند.
- بنابراین هر دو home در دامنه هستند، اما fencing روی هر دیتابیس محلی انجام می‌شود. foreign نباید صرفاً به runtime state دیررس تکیه کند و پس از مرز قطعی schedule آفر جدید بپذیرد.
- ترتیب قفل مشترک Stageهای ۶ و ۷: market-admission fence، سپس user/quota lock، سپس Offer transaction. ترتیب متفاوت بدون تحلیل deadlock مجاز نیست.

### تصمیم نهایی تأییدشده

- بسته‌شدن بازار اولویت دارد: اگر close پیش از commit نهایی ساخت آفر، fence محلی را گرفته باشد، create باید پس از آزادشدن fence وضعیت بازار را دوباره ارزیابی و بدون ساخت row یا side effect رد شود.
- اگر create زودتر fence را بگیرد و commit کند، close منتظر می‌ماند و سپس همان آفر را همراه سایر آفرهای فعال منقضی می‌کند؛ بنابراین هیچ آفر فعال پس از close باقی نمی‌ماند.
- بررسی اولیه WebApp/بات فقط برای پاسخ سریع است و معیار نهایی نیست. بررسی قطعی در سرویس canonical ساخت آفر، بعد از validation و زیر همان transaction/fence انجام می‌شود.
- انتظار create برای fence محدود و fail-closed است؛ timeout نباید row، پیام کانال، realtime، counter یا Web Push بسازد.
- تابع ثبت آمار نشست بازار حق تغییر `market_runtime_state.is_open` ندارد. فقط transition رسمی می‌تواند وضعیت بازار را عوض کند تا مسیر expire و اعلان close دور زده نشود.

### تست‌های اجباری

- interleavingهای «create قبل از close»، «create همزمان با close» و «create بعد از close» اجرا شوند.
- WebApp/Iran-home، Bot/foreign-home، تأخیر sync و autonomy grace جداگانه بررسی شوند.
- آفر ردشده row فعال، پیام کانال، notification یا request counter اشتباه نسازد.
- بازشدن بعدی بازار آفر ردشده را زنده نکند.
- contention و lock timeout با Stage ۷ باعث deadlock یا پذیرش آفر بعد از close نشود.
- تست واقعی PostgreSQL با دو connection ثابت کند برنده fence اول اجرا می‌شود: close-first به rejection و create-first به commit-before-close منجر شود.
- close نهایی در WebApp و بات قبل از ساخت row، publication، notification، realtime، counter و Web Push رد شود.
- ثبت آمار آفر در فاصله create/close وضعیت runtime را تغییر ندهد و close رسمی همچنان همه آفرهای local-home را منقضی کند.

### معیار خروج

هیچ ترتیب زمانی قابل بازتولیدی نتواند بعد از نهایی‌شدن close یک آفر فعال جدید باقی بگذارد.

### ریسک و rollback

lock اشتباه می‌تواند ثبت آفر یا transition بازار را متوقف کند. timeout و ترتیب lockها باید صریح و تست‌شده باشند.

---

## Stage ۷ - اتمیک‌کردن محدودیت‌های ثبت آفر (`MKT-08`)

### مشکل

کنترل تعداد آفر فعال و تعداد درخواست روزانه به‌صورت check-then-write انجام می‌شود. درخواست‌های همزمان از بات و WebApp ممکن است هر دو کنترل را رد کنند و از سقف عبور کنند.

### نتیجه مورد انتظار

- رزرو سهمیه و ثبت آفر یک تصمیم اتمیک باشند.
- بات و WebApp از یک منبع و قرارداد مشترک استفاده کنند.
- rollback ثبت آفر، سهمیه را به‌درستی آزاد یا اصلاح کند.

### محدوده تغییر

- شمارنده درخواست روزانه و query آفرهای فعال
- transaction/locking یا primitive اتمیک متناسب با ساختار فعلی
- هماهنگی lock-order با Stageهای ۴ و ۶؛ بدون ادغام policy معامله مشتری با quota ثبت آفر

### تصمیم dependency

- Stage ۷ وابستگی سخت functional به Stage ۴ ندارد؛ Stage ۴ policy اجرای معامله مشتری است و Stage ۷ quota ثبت آفر.
- Stage ۷ به قرارداد fencing Stage ۶ وابسته است، چون هر دو transaction پذیرش آفر را تغییر می‌دهند.
- reuse فقط در primitiveهای عمومی lock/observability مجاز است؛ ساخت quota ledger مشترک برای دو policy متفاوت ممنوع است.

### تست‌های اجباری

- دو درخواست همزمان در آخرین ظرفیت فقط یک موفقیت داشته باشند.
- رقابت WebApp و بات برای یک کاربر سقف مشترک را رعایت کند.
- خطای validation یا rollback سهمیه مصرف‌شده کاذب باقی نگذارد.
- retry idempotent سهمیه را دوبار کم نکند.
- درخواست کاربران مختلف بی‌دلیل یکدیگر را block نکنند.
- مرز تغییر روز مطابق timezone فعلی تست شود.

### معیار خروج

در اجرای کنترل‌شده همزمان، مجموع موفقیت‌ها هرگز از سقف فعال یا روزانه عبور نکند.

### ریسک و rollback

ریسک اصلی deadlock یا کاهش throughput است. ترتیب lock، timeout و metric انتظار باید قبل از staging مشخص شود.

---

## Stage ۸ - جداسازی قیمت نقد حاضر و فردایی (`MKT-07`)

### مشکل

منطق تشخیص قیمت رقابتی و هشدار قیمت، `settlement_type` را در مقایسه لحاظ نمی‌کند و آفر نقد حاضر ممکن است با آفر فردایی مقایسه شود.

### نتیجه مورد انتظار

- مقایسه قیمت فقط میان آفرهای هم‌کالا، هم‌جهت و هم‌نوع تسویه انجام شود.
- نقد حاضر و فردایی هیچ‌گاه هشدار یا رتبه رقابتی یکدیگر را تغییر ندهند.
- نمایش بات و WebApp با همان تفکیک فعلی سازگار بماند.

### محدوده تغییر

- query/service مربوط به best price، competitive price و warning
- cache keyهای قیمت در صورت وجود
- event/recalculation پس از ایجاد، معامله، cancel و expire

### تست‌های اجباری

- آفر نقد فقط با نقد و آفر فردایی فقط با فردایی مقایسه شود.
- کالا، جهت خرید/فروش و وضعیت inactive همچنان درست فیلتر شوند.
- تغییر یک آفر نقد هیچ هشدار فردایی را عوض نکند و برعکس.
- cache گرم و سرد نتیجه یکسان بدهند.
- bot و WebApp متن/علامت سازگار نشان دهند.

### معیار خروج

ماتریس قیمت با محورهای کالا، جهت و settlement هیچ cross-contamination نداشته باشد.

### ریسک و rollback

ریسک اصلی خالی‌شدن موقت best price در cacheهای قدیمی است. invalidation و warm-up باید بررسی شود.

---

## Stage ۹ - جلوگیری از گرسنگی صف Workerهای ترمیم (`MKT-10`)

### مشکل

workerهای ترمیم پیام کانال و نمایش stale هر بار batch ابتدایی/جدید را می‌خوانند. خطاهای دائمی یا رکوردهای جدید می‌توانند باعث شوند رکوردهای قدیمی هیچ‌وقت پردازش نشوند.

### نتیجه مورد انتظار

- تمام backlog با ترتیب منصفانه و قابل ادامه پیمایش شود.
- restart worker باعث شروع بی‌پایان از همان batch نشود.
- خطای دائمی یک رکورد، بقیه صف را متوقف نکند.

### محدوده تغییر

- worker ترمیم پیام‌های کانال و worker اصلاح presentationهای stale
- keyset pagination/cursor پایدار، retry metadata و backoff
- observability برای اندازه backlog، سن قدیمی‌ترین مورد و تعداد خطا

### تست‌های اجباری

- backlog بیشتر از ۲۵ و بیشتر از ۱۰۰ مورد تا انتها پردازش شود.
- خراب‌بودن دائمی اولین رکورد مانع پردازش رکوردهای بعدی نشود.
- ورود پیوسته رکورد جدید، رکورد قدیمی را گرسنه نگذارد.
- restart وسط batch از نقطه امن ادامه دهد.
- اجرای همزمان دو worker یک side effect را دوبار انجام ندهد.
- remote outage باعث retry کنترل‌شده و رشد قابل مشاهده backlog شود.

### معیار خروج

در تست backlog مصنوعی، همه رکوردهای قابل اصلاح نهایتاً پردازش شوند و سن قدیمی‌ترین مورد فقط به‌دلیل ترتیب query ثابت نماند.

### ریسک و rollback

تغییر صف می‌تواند فشار API تلگرام یا sync را بالا ببرد. batch size، rate limit و backoff باید محدود بمانند.

---

## Stage ۱۰ - Cancel-All دقیق و مقاوم در برابر رقابت (`MKT-11`)

### مشکل

cancel-all برای هر آفر locking و مدیریت conflict کافی ندارد و بات ممکن است حتی در شکست بخشی از آفرهای remote اعلام کند همه آفرها لغو شده‌اند.

### نتیجه مورد انتظار

- هر آفر با authority و lock مناسب cancel شود.
- رقابت معامله، expire، republish و cancel نتیجه معتبر و قابل توضیح داشته باشد.
- پیام نهایی تعداد موفق، ازقبل غیرفعال و ناموفق را دقیق اعلام کند.

### محدوده تغییر

- یک service canonical لغو گروهی مشترک برای API و بات؛ منطق مستقل و تکراری در handler بات باقی نماند
- per-offer result، `StaleDataError`/conflict handling و forwarding
- متن نتیجه بدون ادعای موفقیت کاذب

### dependency اصلاح‌شده

- Stage ۱۰ برای remote cancel به exact replay فرمان expiry وابسته است و تا بسته‌شدن Stage ۱۱ release نمی‌شود.
- backend ابتدا result contract افزایشی per-offer را ارائه می‌کند و سپس بات فقط همان contract را render می‌کند.

### تست‌های اجباری

- معامله و cancel همزمان، آفر را در وضعیت غیرممکن قرار ندهند.
- آفر local و remote در یک درخواست نتیجه مستقل و درست داشته باشند.
- قطع ارتباط remote به‌صورت partial failure گزارش شود.
- retry آفر ازقبل cancel‌شده را موفقیت idempotent یا already-inactive گزارش کند.
- failure یک آفر transaction آفرهای مستقل دیگر را خراب نکند.
- تعداد و متن پیام بات دقیقاً با نتیجه backend برابر باشد.

### معیار خروج

هیچ پیام «همه لغو شدند» در حضور failure وجود نداشته باشد و race با معامله باعث rollback شکسته یا وضعیت متناقض نشود.

### ریسک و rollback

ریسک اصلی تغییر semantics پاسخ بات است. قرارداد نتیجه باید backward-compatible یا همزمان در backend و bot تغییر کند.

---

## Stage ۱۱ - Idempotency انقضای Forwarded Offer (`MKT-13`)

### مشکل

اگر سرور مرجع آفر را منقضی کند اما پاسخ به سرور درخواست‌دهنده گم شود، replay همان فرمان ممکن است به‌دلیل inactive بودن آفر `400` بگیرد؛ درحالی‌که عملیات قبلاً موفق بوده است.

### نتیجه مورد انتظار

- فرمان forwarded expire شناسه یکتا و نتیجه پایدار داشته باشد.
- replay همان فرمان نتیجه موفق قبلی را برگرداند.
- فرمان متفاوت روی آفر inactive همچنان طبق قوانین فعلی رد یا طبقه‌بندی شود.

### محدوده تغییر

- internal command contract و endpoint انقضا
- receipt پایدار روی home server شامل `command_id`، request hash، `offer_public_id` و terminal outcome
- استفاده از schema افزایشی receipt موجود و تکمیل model/service آن در همین Stage؛ Stage ۵ جدید runtime receipt یا ledger موازی ندارد
- جلوگیری از تکرار sync، notification و channel edit

### قرارداد اجرایی

1. HMAC، source و runtime authority بررسی شوند.
2. lock بر اساس command identity گرفته شود.
3. exact replay نتیجه قبلی را برگرداند و same key/different payload با `409` رد شود.
4. Offer با public identity resolve و روی home قفل شود.
5. mutation و terminal receipt در یک transaction commit شوند.
6. side effect بعد از commit و با dedupe key مشتق از command/offer/version اجرا شود.

### تست‌های اجباری

- commit موفق + پاسخ گمشده + replay، موفقیت قبلی را برگرداند.
- دو replay همزمان فقط یک mutation و یک side effect بسازند.
- key یکسان با offer یا payload متفاوت conflict بدهد.
- expire ازقبل انجام‌شده با فرمان مستقل رفتار تعریف‌شده داشته باشد.
- replay پس از restart هر دو سرور همچنان معتبر باشد.
- تفاوت ID عددی Offer بین دو سرور نتیجه را تغییر ندهد.
- rollback mutation و receipt با هم انجام شود و side-effect failure توسط reconciliation ترمیم شود.

### معیار خروج

retry شبکه‌ای هیچ‌گاه یک عملیات موفق را به failure کاذب تبدیل نکند و side effect تکراری نسازد.

### ریسک و rollback

اگر نتیجه فقط در حافظه نگهداری شود با restart از بین می‌رود؛ persistence و retention باید متناسب با retry window واقعی باشد.

---

## Stage ۱۲ - Pagination کامل بازار فعال (`MKT-05`)

### مشکل

WebApp فقط batch پیش‌فرض ۵۰ آفر را دریافت می‌کند و راهی برای دیدن ادامه بازار ندارد؛ فیلترهای client-side نیز فقط همین بخش ناقص را فیلتر می‌کنند.

### نتیجه مورد انتظار

- کاربر بتواند تمام آفرهای مجاز را با pagination یا load-more ببیند.
- filter، sort و realtime روی dataset صفحه‌بندی‌شده رفتار قابل پیش‌بینی داشته باشند.
- آفر تکراری یا گمشده میان صفحات ایجاد نشود.

### محدوده تغییر

- تکمیل offset primitive موجود با قرارداد additive cursor پایدار `(created_at, id)`
- state و UI در Market/OffersList
- ordering پایدار، server-side filter و ادغام eventهای realtime با dedupe بر اساس `offer_public_id`

### dependency انتشار

- API فعلی `skip/limit` دارد، اما WebApp فقط batch اول را می‌گیرد؛ finding در سطح محصول تأیید است، نه فقدان کامل primitive API.
- Stage ۱۴ باید پیش از release pagination، dual delivery را حذف کند و Stage ۱۶ zero semantics را تثبیت کند.

### تست‌های اجباری

- با ۵۱، ۱۰۰ و بیش از ۱۰۰ آفر، همه موارد قابل دسترسی باشند.
- فیلتر نقد/فردا، کالا و خرید/فروش نتیجه درست در کل dataset بدهد.
- ایجاد/حذف/معامله آفر میان load-more باعث duplicate یا gap آشکار نشود.
- timestamp برابر با tie-break شناسه پایدار تست شود.
- تغییر tab و بازگشت، cursor اشتباه را reuse نکند.
- empty، last page و خطای شبکه قابل retry باشند.
- mobile و desktop کنترل‌ها بدون overlap کار کنند.

### معیار خروج

هیچ آفر مجازی صرفاً به‌دلیل قرارگرفتن بعد از رکورد ۵۰ از دسترس کاربر خارج نباشد.

### ریسک و rollback

offset pagination زیر تغییرات realtime مستعد duplicate/gap است؛ cursor پایدار ترجیح دارد و fallback باید روشن باشد.

---

## Stage ۱۳ - Pagination کامل تاریخچه معاملات (`MKT-12`)

### مشکل

تاریخچه WebApp تنها یک صفحه محدود را نشان می‌دهد؛ در نتیجه معاملات قدیمی‌تر کاربر یا مشتری قابل مشاهده نیستند.

### نتیجه مورد انتظار

- تمام تاریخچه مجاز با load-more/pagination قابل دسترسی باشد.
- محدودیت دسترسی مشتری، مدیر و کاربر عادی در تمام صفحات ثابت بماند.
- ترتیب زمانی و نمایش نقد حاضر/فردایی حفظ شود.

### محدوده تغییر

- تکمیل offset primitive موجود در endpointهای history با cursor/metadata افزایشی
- UI تاریخچه و recent trades در صورت اشتراک data source
- total/has_more/cursor و loading/error states

### dependency انتشار

- endpointهای فعلی `/my` و `/with/{other_user_id}` به‌ترتیب limit پیش‌فرض ۵۰ و ۲۰ دارند، اما UI continuation ندارد.
- Stage ۱۶ باید پیش از release این Stage بسته شود تا page merge و history روی مقدار مانده اشتباه بنا نشوند.
- authorization و relation visibility در هر page دوباره اعمال شود؛ cursor مجوز دسترسی ایجاد نمی‌کند.

### تست‌های اجباری

- بیش از ۵۰ معامله شخصی و بیش از ۲۰ معامله customer view قابل پیمایش باشند.
- صفحه بعدی داده تکراری یا خارج از دسترسی نداشته باشد.
- filter تاریخ، نوع معامله، کاربر و settlement روی تمام صفحات صحیح باشد.
- معامله جدید هنگام پیمایش ترتیب پایدار را نشکند.
- نقش نامجاز نتواند با تغییر cursor یا user id تاریخچه دیگری را ببیند.
- empty و end-of-list درست نمایش داده شوند.

### معیار خروج

کاربر مجاز بتواند بدون محدودیت hard-coded پنهان، کل تاریخچه تعریف‌شده محصول را ببیند.

### ریسک و rollback

query تاریخچه بزرگ ممکن است سنگین شود. ordering/index و سقف منطقی page size باید حفظ شود.

---

## Stage ۱۴ - حذف تحویل دوگانه Realtime (`MKT-14`)

### مشکل

یک رویداد عمومی می‌تواند هم از Redis و هم با broadcast مستقیم تحویل شود و در نتیجه frontend یک تغییر را دو بار دریافت و refresh کند.

### نتیجه مورد انتظار

- هر event منطقی در هر subscriber فقط یک بار پردازش شود.
- در خرابی Redis، fallback کنترل‌شده realtime محلی را حفظ کند.
- راه‌حل با چند worker و چند instance درست باشد.

### محدوده تغییر

- Redis به‌عنوان transport اصلی و direct local broadcast فقط در failure قطعی publish
- event id پایدار و dedup در مرز مناسب
- publisher/subscriber lifecycle در realtime router
- تفکیک صریح رفتار WebSocket و SSE؛ duplicate اصلی در مسیر WebSocket فعلی است

### تست‌های اجباری

- در حالت سالم Redis هر subscriber فقط یک event بگیرد.
- شکست publish در Redis fallback محلی را فعال کند، نه هر دو را.
- publish موفق با subscriber صفر نباید direct fallback را فعال کند.
- چند worker و چند اتصال یک کاربر duplicate نسازند.
- reconnect event قدیمی را بدون قرارداد replay دوباره اعمال نکند.
- dedup یک event واقعاً جدید را حذف نکند.
- routing خصوصی Stage ۱ همچنان حفظ شود.

### معیار خروج

شمار eventهای دریافت‌شده در ماتریس single/multi-worker دقیقاً با شمار eventهای منطقی برابر باشد.

### ریسک و rollback

حذف broadcast مستقیم بدون fallback می‌تواند realtime را هنگام خرابی Redis قطع کند؛ health و fallback باید جداگانه اثبات شوند.

---

## Stage ۱۵ - تطبیق Payload در Idempotency ثبت آفر (`MKT-15`)

### مشکل

replay ثبت آفر فقط owner و key را بررسی می‌کند. اگر همان key تصادفاً با کالا، قیمت، تعداد یا نوع تسویه دیگری ارسال شود، ممکن است آفر قبلی به‌عنوان پاسخ برگردد.

### نتیجه مورد انتظار

- درخواست تکراری دقیقاً یکسان، پاسخ آفر قبلی را بگیرد.
- همان key با هر payload اقتصادی متفاوت، conflict روشن بگیرد.
- comparison بر پایه representation canonical باشد، نه تفاوت ظاهری JSON.

### محدوده تغییر

- fingerprint/canonical payload ثبت آفر
- ذخیره یا بازسازی مطمئن fingerprint
- قرارداد `409` و رفتار frontend/bot هنگام mismatch
- هم‌راستاکردن unique constraint دیتابیس با lookup semantics؛ وضعیت فعلی key را global unique ولی replay را owner-scoped می‌خواند

### dependency و canonicalization

- fingerprint باید source/home، owner/actor semantic identity، کالا، جهت، settlement، quantity، lotهای canonical، قیمت، notes policy-relevant و republish source public identity را پوشش دهد.
- Stageهای ۵، ۶، ۷ و ۸ ابتدا semantics نهایی republish، admission/quota و settlement را تثبیت می‌کنند.
- helper fingerprint در همین Stage باید یک‌بار، versioned و قابل reuse ساخته شود؛ Stage ۵ جدید عمداً fingerprint عمومی command ایجاد نمی‌کند.

### تست‌های اجباری

- replay دقیق با key یکسان همان offer id را برگرداند.
- تغییر هرکدام از کالا، جهت، settlement، quantity، lot sizes، price و توضیحات policy-relevant جداگانه تست شود.
- ترتیب متفاوت فیلدها یا representation معادل conflict کاذب نسازد.
- دو درخواست همزمان با key و payload یکسان فقط یک آفر بسازند.
- دو درخواست همزمان با key یکسان و payload متفاوت حداکثر یک آفر و یک conflict بسازند.
- replay سهمیه Stage ۷ و اعلان را دوبار مصرف/ارسال نکند.

### معیار خروج

هیچ key نتواند دو intent اقتصادی متفاوت را به یک پاسخ موفق مبهم تبدیل کند.

### ریسک و rollback

افزودن fingerprint باید با رکوردهای قدیمی سازگار باشد. رفتار legacy keyهای فاقد fingerprint باید صریح و محافظه‌کارانه تعریف شود.

---

## Stage ۱۶ - حفظ مقدار صفر در Serialization (`MKT-16`)

### مشکل

استفاده از fallback مبتنی بر truthy برای `remaining_quantity` باعث می‌شود مقدار معتبر صفر با quantity اولیه جایگزین و آفر معامله‌شده ظاهراً دوباره پُر نمایش داده شود.

### نتیجه مورد انتظار

- صفر به‌عنوان مقدار معتبر در API، event، history، frontend و مسیرهای affected بات حفظ شود.
- fallback فقط برای `None` یا نبود واقعی مقدار استفاده شود.
- frontend و bot آفر تکمیل‌شده را با مانده اشتباه نشان ندهند.
- مسیرهای سالم canonical sync و Telegram channel publication بدون بازنویسی حفظ شوند.

### محدوده تغییر

- occurrenceهای unsafe در API serializer، realtime payload، read modelها، frontend `||` و handlerهای affected بات
- تست قرارداد JSON برای صفر
- regression-only برای `core/offer_sync_payload.py` و `telegram_offer_channel_service.py` که اکنون `None` را صریح مدیریت می‌کنند

### تصحیح دامنه پس از بازبینی

- finding به‌عنوان باگ واقعی حفظ می‌شود، اما ادعای خرابی همه مسیرها رد شد.
- canonical sync payload مقدار `remaining_quantity` را مستقیم حمل می‌کند و Telegram channel publication از fallback صریح `None` استفاده می‌کند.
- replacement سراسری و چشم‌بسته `or`/`||` ممنوع است؛ هر occurrence باید بر اساس semantics همان مقدار بررسی شود.

### تست‌های اجباری

- `remaining_quantity=0` در API دقیقاً صفر بماند.
- مقدار `None` فقط طبق قرارداد فعلی fallback شود.
- partial quantity، quantity کامل و صفر در همه serializerها بررسی شوند.
- event realtime و sync مقدار صفر را حفظ کنند.
- WebApp، بات، تاریخچه و معاملات اخیر وضعیت completed را درست نشان دهند.
- Telegram channel برای مانده صفر دکمه معامله نسازد و sync regression همچنان صفر را بدون تغییر منتقل کند.

### معیار خروج

جست‌وجوی هدفمند و تست contract ثابت کند هیچ مسیر affected مقدار صفر را تغییر نمی‌دهد و مسیرهای سالم sync/Telegram بدون regression باقی مانده‌اند.

### ریسک و rollback

ریسک این stage پایین‌تر است اما blast radius serializer مشترک می‌تواند زیاد باشد؛ تغییر باید محدود و همراه با snapshot/contract test باشد.

---

## ماتریس پذیرش نهایی

| Finding | فقط در Stage | اثبات اصلی |
|---|---:|---|
| `MKT-01` | ۱ | عدم مشاهده trade event توسط recipient غیرمجاز |
| `MKT-02` | ۴ | رد قطعی معامله مشتری خارج از policy |
| `MKT-03` | ۲ | reuse همان key پس از پاسخ مبهم |
| `MKT-04` | ۵ | source بدون mutation، فقط یک child مستقل و فقط leaf نشست جاری قابل تکرار |
| `MKT-05` | ۱۲ | دسترسی به آفرهای بعد از رکورد ۵۰ |
| `MKT-06` | ۶ | نبود آفر فعال جدید پس از close قطعی |
| `MKT-07` | ۸ | عدم مقایسه نقد حاضر با فردایی |
| `MKT-08` | ۷ | عدم عبور درخواست‌های همزمان از سقف |
| `MKT-09` | ۳ | replay موفق پس از تغییر guardهای متغیر |
| `MKT-10` | ۹ | تخلیه منصفانه backlog بزرگ |
| `MKT-11` | ۱۰ | نتیجه دقیق cancel-all در partial failure/race |
| `MKT-12` | ۱۳ | پیمایش کامل تاریخچه مجاز |
| `MKT-13` | ۱۱ | موفقیت replay پس از گم‌شدن پاسخ expire |
| `MKT-14` | ۱۴ | یک تحویل برای هر event منطقی |
| `MKT-15` | ۱۵ | conflict برای key یکسان و payload متفاوت |
| `MKT-16` | ۱۶ | حفظ صفر در مسیرهای affected و عدم regression در sync/Telegram سالم |

## معیار بسته‌شدن کل Roadmap

- هر ۱۶ stage با گزارش و شواهد مستقل بسته شده باشند.
- هیچ finding بین stageها گم، تکرار یا بدون مالک باقی نمانده باشد.
- تست‌های متمرکز بازار، idempotency، authority، sync، realtime و UI همگی سبز باشند.
- staging شامل smoke دو سروری، قطع و وصل ارتباط، retryهای مبهم و role isolation باشد.
- خطاهای production فعلی در تست‌های regression بازتولید و سپس رفع‌شدنشان اثبات شده باشد.
- پیش از production deploy، diff نهایی، migrationها، rollback و ریسک نسخه مختلط جداگانه بازبینی شوند.
- release gateهای رو‌به‌جلو در جدول dependency، حتی اگر شماره Stage کوچک‌تر باشد، بسته شده باشند.
- هیچ receipt یا fingerprint موازی برای یک command family باقی نمانده باشد.

## خروجی الزامی هر Stage

- یک تغییر محدود به finding همان stage
- تست‌های جدید و نتایج تست‌های regression
- نام دقیق suite/test ID، commit SHA و hash artifactهای قابل انتقال
- گزارش سناریوهای پاس/رد و ریسک باقیمانده
- فهرست فایل‌ها و migrationهای تغییرکرده
- دستور rollback و شرایط توقف deploy
- وضعیت stage بعدی و وابستگی‌های باز
