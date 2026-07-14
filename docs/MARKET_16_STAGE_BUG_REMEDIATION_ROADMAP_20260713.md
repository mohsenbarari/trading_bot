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

وضعیت Stage: بسته شد؛ پیاده‌سازی، تست هم‌زمانی واقعی PostgreSQL، deploy و پایش دو سرور staging کامل شده است.

### گزارش اجرای Stage ۶ - ۲۰۲۶-۰۷-۱۴

پیاده‌سازی نهایی در commit `328d29cec3f31f85a8192697754c5ccbced13ea3`:

- سرویس canonical ساخت آفر پس از validation و پیش از ساخت row، advisory lock همان transaction مربوط به transition بازار را می‌گیرد و سپس برنامه زمانی بازار را دوباره ارزیابی می‌کند. WebApp و بات هر دو این fence نهایی را فعال می‌کنند.
- اگر close زودتر lock را گرفته باشد، create پس از آزادشدن lock وضعیت بسته را می‌بیند و با همان پیام فعلی بسته‌بودن بازار رد می‌شود؛ row، Telegram publication، realtime، Web Push، notification و counter ساخته نمی‌شوند.
- اگر create زودتر lock را گرفته باشد، commit آن پیش از ادامه close انجام می‌شود و close همان آفر را مانند سایر آفرهای فعال منقضی می‌کند. lock فقط محلی است: Iran آفرهای Iran-home و foreign آفرهای foreign-home را با authority فعلی پروژه fence می‌کنند.
- انتظار create برای lock به‌صورت پیش‌فرض `5000ms` و در بازه `250..30000ms` محدود است. timeout به‌صورت fail-closed پاسخ داده می‌شود و caller transaction را rollback می‌کند.
- foreign هنگام reconciliation بسته‌شدن بازار نیز قبل از scan/expiry همان lock را می‌گیرد و transaction را حتی در نبود آفر فعال commit می‌کند تا fence آزاد شود.
- `register_market_offer_created` دیگر `market_runtime_state.is_open` را تغییر نمی‌دهد و در فاصله pending-open یا pending-close شمارنده را commit نمی‌کند؛ فقط transition رسمی وضعیت بازار را عوض می‌کند.

شواهد تست:

- suite متمرکز transition/schedule/canonical create/WebApp/Bot: `86` تست پاس.
- offer regression: `143`، trade regression: `242`، bot-create regression: `61` و publication regression: `41` تست پاس.
- دو تست واقعی PostgreSQL با دو connection ثابت کردند close-first باعث انتظار و سپس rejection می‌شود و create-first باعث انتظار close تا commit create می‌شود؛ دیتابیس موقت پس از تست حذف شد.
- `compileall`، `git diff --check` و single Alembic head برابر `d0b5e6f7a8c9` پاس شدند. این Stage migration یا تغییر schema ندارد.
- دو تست مستقیم دریافت و reconciliation runtime بازار پاس شدند. suite گسترده sync همان `9` failure از `16` تست را هم روی branch و هم checkout تمیز baseline `6ce1adbe` با نام‌های دقیقاً یکسان تکرار کرد؛ این failureها پیش از منطق Stage ۶ و مربوط به fixture/policy metadata موجود هستند و regression این Stage نیستند.

شواهد staging دو سرور:

- Iran و foreign هر دو با full `RELEASE_SHA=328d29cec3f31f85a8192697754c5ccbced13ea3`، environment برابر `staging`، server mode درست و Alembic head یکسان اجرا می‌شوند. hash چهار فایل runtime میان checkout و imageهای هر دو سرور دقیقاً برابر است.
- preflight فقط‌خواندنی `MARKET-STAGE6-ADMISSION-FENCE-20260714-R2` با deep parity پاس شد: ۲۳ جدول، business/critical/incomplete/duplicate drift صفر، پنج تفاوت صرفاً local/volatile، صف‌های outbound/retry و unsynced backlog صفر.
- اسکن پنج‌دقیقه‌ای Iran app/sync worker و foreign app/bot/sync worker صفر `ERROR`، `CRITICAL`، traceback، internal 500 یا unhandled exception داشت.
- هیچ سناریوی mutating full-matrix، migration جدید، production deploy/restart یا تغییر داده production انجام نشد.

ریسک و rollback:

- rollback runtime با revert commit `328d29ce` و recreate سرویس‌های staging انجام می‌شود و rollback schema لازم نیست.
- timeout کوتاه ممکن است در contention شدید یک create سالم را محافظه‌کارانه رد کند، اما آفر پس از close نمی‌پذیرد. متریک/لاگ rejection برای تفکیک close از unavailable ثبت شده است.
- Stage ۷ باید ترتیب lock تثبیت‌شده `market-admission fence → user/quota lock → Offer transaction` را رعایت کند؛ تغییر ترتیب بدون تست PostgreSQL رقابت و deadlock مجاز نیست.

---

## Stage ۷ - اتمیک‌کردن محدودیت‌های ثبت آفر (`MKT-08`)

### مشکل

کنترل تعداد آفر فعال و تعداد درخواست مجاز به‌صورت check-then-write انجام می‌شود. درخواست‌های همزمان از بات و WebApp که به یک PostgreSQL می‌رسند ممکن است هر دو کنترل را رد کنند و از سقف عبور کنند.

نام فیلد موجود `max_daily_requests` تاریخی است، اما رفتار فعلی پروژه reset تقویمی روزانه ندارد. `channel_messages_count` در دوره فعال محدودیت جمع می‌شود و فقط از مسیر reset موجود هنگام تغییر/رفع محدودیت صفر می‌شود. Stage ۷ نباید بدون تصمیم محصول این semantics را به reset روزانه تهران تغییر دهد.

### نتیجه مورد انتظار

- رزرو سهمیه و ثبت آفر یک تصمیم اتمیک باشند.
- بات و WebApp از یک منبع و قرارداد مشترک استفاده کنند.
- rollback ثبت آفر، سهمیه را به‌درستی آزاد یا اصلاح کند.
- این تضمین در هر PostgreSQL محلی برقرار باشد و availability فعلی هنگام قطع ارتباط دو سرور حفظ شود.

### محدوده تغییر

- شمارنده درخواست دوره محدودیت و query آفرهای فعال
- transaction/locking یا primitive اتمیک متناسب با ساختار فعلی
- هماهنگی lock-order با Stageهای ۴ و ۶؛ بدون ادغام policy معامله مشتری با quota ثبت آفر

### تصمیم dependency

- Stage ۷ وابستگی سخت functional به Stage ۴ ندارد؛ Stage ۴ policy اجرای معامله مشتری است و Stage ۷ quota ثبت آفر.
- Stage ۷ به قرارداد fencing Stage ۶ وابسته است، چون هر دو transaction پذیرش آفر را تغییر می‌دهند.
- reuse فقط در primitiveهای عمومی lock/observability مجاز است؛ ساخت quota ledger مشترک برای دو policy متفاوت ممنوع است.

### تصمیم معماری تأییدشده

- سهمیه به‌صورت اتمیک و محلی در هر PostgreSQL اعمال شود؛ authority آنلاین واحد، distributed lock و quota ledger بین دو سرور در این Stage ساخته نشود.
- WebApp و بات final admission را از سرویس canonical مشترک می‌گیرند، اما هرکدام روی دیتابیس محلی home خود serialize می‌شوند.
- در قطع ارتباط یا race واقعی دو دیتابیس، هر سرور اجازه دارد براساس وضعیت محلی درخواست را بپذیرد. بنابراین عبور موقت از سقف سراسری تا رسیدن sync یک ریسک آگاهانه و پذیرفته‌شده در برابر حفظ availability است.
- sync بعدی باید طبق مسیر فعلی convergence انجام شود، اما ادعا نمی‌شود که پذیرش قبلی سرور مقابل را به‌صورت retroactive خنثی می‌کند.
- ترتیب قفل ثابت است: `market-admission fence → User SELECT FOR UPDATE → local idempotency replay → user limits → active-offer count → Offer + counter → commit`.

### تست‌های اجباری

- دو درخواست همزمان در آخرین ظرفیت فقط یک موفقیت داشته باشند.
- رقابت WebApp و بات برای یک کاربر سقف مشترک را رعایت کند.
- خطای validation یا rollback سهمیه مصرف‌شده کاذب باقی نگذارد.
- retry idempotent سهمیه را دوبار کم نکند.
- درخواست کاربران مختلف بی‌دلیل یکدیگر را block نکنند.
- تغییر روز تقویمی counter فعلی را خودکار reset نکند؛ reset فقط از قرارداد موجود تغییر/رفع محدودیت انجام شود.
- listener واقعی `User` و `Offer` فعال باشد و commit یک event پایدار counter بسازد، اما rollback نه row، نه counter و نه event باقی بگذارد.

### معیار خروج

در اجرای کنترل‌شده همزمان روی یک PostgreSQL، مجموع موفقیت‌ها هرگز از سقف آفر فعال یا سقف درخواست دوره محدودیت عبور نکند. این معیار ادعای strict-global quota میان دو دیتابیس مستقل ندارد.

### ریسک و rollback

ریسک اصلی deadlock یا کاهش throughput است. ترتیب lock، timeout و metric انتظار باید قبل از staging مشخص شود. ریسک باقی‌مانده عبور موقت از سقف سراسری در race همزمان Iran/foreign یا sync outage است؛ حذف این ریسک نیازمند authority مشترک و کاهش availability خواهد بود و خارج از تصمیم این Stage است.

وضعیت Stage: بسته شد؛ پیاده‌سازی، تست رقابت واقعی PostgreSQL، deploy و پایش دو سرور staging با scope محلی تأییدشده کامل شده است.

### گزارش اجرای Stage ۷ - ۲۰۲۶-۰۷-۱۴

پیاده‌سازی نهایی در commit `9d801ec52442c57118848d772d656279478f32a8`:

- سرویس canonical ساخت آفر قرارداد `OfferCreationQuotaPolicy` را دریافت می‌کند و پس از fence نهایی Stage ۶، row همان کاربر را با `SELECT FOR UPDATE` قفل می‌کند. replay محلی idempotency، محدودیت‌های کاربر و تعداد آفر فعال همگی زیر همین قفل دوباره بررسی می‌شوند.
- ساخت `Offer` و افزایش `channel_messages_count` در یک transaction و یک commit انجام می‌شوند. rollback هر دو را برمی‌گرداند و retry idempotent موجود را بدون مصرف دوباره سهمیه بازمی‌گرداند.
- بررسی‌های اولیه WebApp و بات برای UX باقی مانده‌اند، اما تصمیم قطعی final admission در سرویس مشترک است. WebApp پاسخ final limit را `403`، unavailable شدن lock را `503` و admission conflict را `409` می‌دهد؛ بات همان خطاها را با پیام مناسب فعلی نمایش می‌دهد.
- افزایش counter قدیمی پس از Telegram publication از هر دو caller حذف شد تا double count رخ ندهد. sync/probe callerها چون `quota_policy` نمی‌فرستند، همان رفتار قبلی را حفظ کرده‌اند.
- این Stage migration یا تغییر schema ندارد و Alembic head همان `d0b5e6f7a8c9` است.

شواهد تست:

- suite خانواده Offer برابر `151 passed, 6 skipped`، خانواده Bot trade-create برابر `62 passed` و user-counter/core-utils/guard/market برابر `40 passed, 3 skipped` است.
- هفت تست واقعی PostgreSQL رقابت WebApp/Bot برای آخرین ظرفیت، رقابت counter، rollback کامل، replay همزمان idempotent، عدم block کاربران مستقل، عدم reset تقویمی و event واقعی `user_counter_event_v2` را پاس کردند. دیتابیس scratch محافظت‌شده پس از اجرا حذف شد.
- `compileall`، `git diff --check` و single Alembic head پاس شدند.
- اجرای گسترده backend شامل `3385` تست بود و `45 failure + 11 error + 72 skipped` ثبت کرد. failure/errorها در onboarding/sync/fixture محیطی و dependency اختیاری `hypothesis` بودند؛ هیچ failure از Offer یا Bot trade-create نبود و suiteهای مستقیم Stage ۷ همگی سبز ماندند.

شواهد staging دو سرور:

- Iran WebApp/API روی `staging.gold-trade.ir` و foreign API/bot روی `staging.362514.ir` هر دو با full `RELEASE_SHA=9d801ec52442c57118848d772d656279478f32a8`، mode درست و Alembic head یکسان اجرا می‌شوند. hash چهار فایل runtime میان checkout و imageهای هر دو سرور برابر است.
- preflight فقط‌خواندنی `MARKET-STAGE7-LOCAL-QUOTA-20260714-R2` با deep parity و بدون failed check پاس شد. تلاش اول فقط به‌دلیل release SHA کوتاه fail-close شد؛ هر دو runtime با SHA کامل recreate و همان کنترل تکرار شد.
- هیچ full-matrix جهشی، mutation تستی یا production deploy/restart انجام نشد.

ریسک و rollback:

- transaction محلی جلوی عبور race در یک دیتابیس را می‌گیرد، اما strict-global quota میان دو دیتابیس مستقل را تضمین نمی‌کند؛ این محدودیت مطابق تصمیم معماری بالا پذیرفته شده است.
- counter اکنون هنگام پذیرش و commit آفر مصرف می‌شود. اگر publication خارجی پس از commit شکست بخورد، درخواست پذیرفته‌شده همچنان در counter دوره محدودیت محاسبه می‌شود؛ failure پیش از commit هیچ مصرفی باقی نمی‌گذارد.
- rollback runtime با revert commit `9d801ec5` و recreate سرویس‌های staging انجام می‌شود و rollback schema لازم نیست.

---

## Stage ۸ - جداسازی قیمت نقد حاضر و فردایی (`MKT-07`)

### مشکل

منطق تشخیص قیمت رقابتی و هشدار قیمت، `settlement_type` را در مقایسه لحاظ نمی‌کند و آفر نقد حاضر ممکن است با آفر فردایی مقایسه شود.

### نتیجه مورد انتظار

- مقایسه قیمت فقط میان آفرهای هم‌کالا، هم‌جهت و هم‌نوع تسویه انجام شود.
- نقد حاضر و فردایی هیچ‌گاه هشدار یا نتیجه کنترل رقابتی یکدیگر را تغییر ندهند.
- نمایش بات و WebApp با همان تفکیک فعلی سازگار بماند.

### تصمیم اجرایی تأییدشده

- الگوریتم فعلی رد قیمت رقابتی، معیار قابل اتکای قیمت منصفانه نیست و تا بازطراحی بعدی با تنظیم `competitive_price_validation_enabled=false` تعلیق می‌شود. کد آن حذف نمی‌شود تا فعال‌سازی آینده فقط پس از اصلاح الگوریتم و تست مستقل ممکن باشد.
- هشدار قیمت و تأیید دوم رفتار مستقلی دارد و با تنظیم `offer_price_warning_enabled=true` فعال می‌ماند. بنابراین قیمت مشکوک کاربر را متوقف نمی‌کند، اما پیش از انتشار یک تأیید صریح دیگر می‌گیرد.
- هر دو تنظیم در `trading_settings` قرار می‌گیرند، پیش‌فرض امن آن‌ها به‌ترتیب `false` و `true` است و از پنل تنظیمات قابل مدیریت‌اند.
- حتی در حالت تعلیق کنترل رقابتی، query مشترک هشدار و کنترل آینده باید `settlement_type` را الزاماً دریافت کند تا نقد حاضر فقط با نقد حاضر و فردایی فقط با فردایی مقایسه شود.
- اعتبارسنجی پایه قیمت، شامل عدد صحیح، مثبت و طول مجاز، بدون تغییر فعال می‌ماند.

### محدوده تغییر

- query/service مشترک `competitive price` و `price warning`
- callerهای ثبت آفر در WebApp، بات و سرویس canonical ساخت آفر
- مدل، API و پنل `trading_settings` برای دو کلید مستقل
- بررسی کد نشان داد این مسیر best-price cache یا recalculation/event مستقل ندارد؛ بنابراین invalidation، warm-up و تغییر event خارج از محدوده واقعی Stage است.

### تست‌های اجباری

- آفر نقد فقط با نقد و آفر فردایی فقط با فردایی مقایسه شود.
- کالا، جهت خرید/فروش و وضعیت inactive همچنان درست فیلتر شوند.
- خاموش‌بودن کنترل رقابتی باید پیش از query، نتیجه مجاز بدهد و آفر را به علت الگوریتم تعلیق‌شده رد نکند.
- فعال‌بودن همزمان هشدار باید مستقل از کنترل رقابتی، query هم‌نوع تسویه را اجرا و payload تأیید دوم را تولید کند.
- خاموش‌بودن هشدار باید مستقل از کنترل رقابتی، بدون query و بدون تأیید دوم ادامه دهد.
- WebApp و بات باید `settlement_type` را صریح به هر دو کنترل بدهند و مسیر تأیید دوم فعلی را حفظ کنند.
- GET، PUT، reset و fallback تنظیمات باید مقادیر `false/true` را بدون تغییر نوع یا حذف کلید حفظ کنند.
- تست تعامل پنل تنظیمات و build فرانت‌اند پاس شود.

### معیار خروج

کنترل رقابتی در runtime به‌طور مستقل خاموش باشد، هشدار و تأیید دوم فعال بماند و هیچ مقایسه نقد/فردا در query مشترک رخ ندهد. این Stage ادعا نمی‌کند الگوریتم قیمت منصفانه اصلاح شده است؛ فقط آن را قابل برگشت تعلیق و آلودگی settlement را رفع می‌کند.

### ریسک و rollback

ریسک اصلی اشتباه در مقدار persisted تنظیمات یا خاموش‌شدن ناخواسته هشدار است. rollout باید مقدار نهایی `false/true` را روی هر دو runtime staging و مسیر sync تنظیمات را کنترل کند. rollback با برگرداندن commit runtime و recreate سرویس‌ها انجام می‌شود؛ schema یا migration جدیدی وجود ندارد.

وضعیت Stage: بسته شد؛ تعلیق قابل برگشت کنترل رقابتی، حفظ هشدار و تأیید دوم، جداسازی settlement، تست و deploy دو سرور staging کامل شده است.

### گزارش اجرای Stage ۸ - ۲۰۲۶-۰۷-۱۴

پیاده‌سازی نهایی در commit `548190ca9ad04ab11a32f7769ecbe93ff454e152`:

- دو flag مستقل `competitive_price_validation_enabled=false` و `offer_price_warning_enabled=true` به مدل، fallback JSON، API و پنل `trading_settings` اضافه شدند. کنترل رقابتی پیش از query مجاز برمی‌گردد، اما هشدار و تأیید دوم مستقل فعال می‌ماند.
- helper مشترک مقایسه قیمت اکنون `settlement_type` اجباری دارد و query فقط آفرهای همان نوع تسویه را می‌خواند. WebApp، بات و validation canonical همگی مقدار تسویه را صریح ارسال می‌کنند.
- اعتبارسنجی پایه عدد/مثبت/طول قیمت و مسیر فعلی تأیید دوم تغییر نکردند. migration یا schema جدیدی وجود ندارد و Alembic head هر دو staging برابر `d0b5e6f7a8c9` باقی ماند.

شواهد تست:

- خانواده Offer برابر `151 passed, 6 skipped`، خانواده Bot trade-create برابر `62 passed`، خانواده Trade برابر `244 passed` و تنظیمات runtime/router برابر `22 passed` است؛ در مجموع `479 passed, 6 skipped` در چهار suite مستقل ثبت شد.
- کل تست‌های واحد frontend برابر `1154 passed` در `129` فایل و تست متمرکز `TradingSettings.vue` برابر `7 passed` است. build کامل Vite، `compileall` و `git diff --check` نیز پاس شدند.
- تست‌ها صریحاً خاموش‌بودن کنترل رقابتی بدون DB query، فعال‌ماندن مستقل هشدار، خاموش‌شدن مستقل هشدار، وجود فیلتر settlement در SQL و ارسال `cash` از WebApp و بات را اثبات می‌کنند.

شواهد staging دو سرور:

- Iran WebApp/API روی `staging.gold-trade.ir` و foreign API/bot روی `staging.362514.ir` با full `RELEASE_SHA=548190ca9ad04ab11a32f7769ecbe93ff454e152` و نقش‌های `iran/foreign` درست اجرا می‌شوند. foreign public `/api/config` طبق guard برابر `404` و Iran WebApp با Basic Auth برابر `200` است.
- دو setting روی authority ایران persisted و از مسیر outbox موجود به foreign sync شدند؛ هر دو دیتابیس `17` setting دارند و runtimeهای Iran، foreign API و bot مقدار `false/true` یکسان خواندند.
- preflight اول `MARKET-STAGE8-SETTLEMENT-PRICE-CONTROLS-20260714-R1` fail-close شد، چون ثبت اولیه ابزار بدون startup listener دو row جدید را روی foreign نساخته بود. پس از ثبت مجدد با `setup_event_listeners()` و تحویل استاندارد outbox، preflight فقط‌خواندنی R2 با deep parity و بدون failed check پاس شد.
- پایش پنج سرویس `app/sync_worker` ایران و `foreign_app/bot/foreign_sync_worker` خارج پس از rollout هیچ `ERROR`، `CRITICAL` یا traceback جدیدی نشان نداد. production deploy/restart و full-matrix جهشی انجام نشد.

ریسک باقی‌مانده و rollback:

- الگوریتم قدیمی همچنان در کد وجود دارد و اگر flag اول بدون بازطراحی فعال شود، همان محدودیت‌های قبلی آن بازمی‌گردد؛ فعال‌سازی آینده نیازمند Stage جداگانه برای تعریف قیمت منصفانه و تست بازار است.
- rollback runtime با revert commit `548190ca` و recreate سرویس‌های staging انجام می‌شود. چون schema تغییر نکرده، rollback دیتابیس لازم نیست؛ در صورت rollback باید دو flag persisted نیز با تصمیم عملیاتی موردنظر هماهنگ شوند.

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

وضعیت Stage: بسته شد؛ پیاده‌سازی، تست رقابت واقعی PostgreSQL، deploy و پایش دو سرور staging و deep parity نهایی کامل شده است.

### گزارش اجرای Stage ۹ - ۲۰۲۶-۰۷-۱۴

پیاده‌سازی نهایی در commit `095d478b26c5afdb73cc3d454ea0b5e1c910ee15`:

- هر دو مسیر ترمیم publication و وضعیت پیام کانال فقط candidateهای due را با ترتیب قدیمی‌ترین مورد می‌خوانند. شکست یک ردیف با backoff نمایی و `next_retry_at` پایدار از بقیه batch جدا می‌شود و خطای دائمی پس از retry محدود به وضعیت پایدار terminal/set-aside می‌رسد.
- حافظه process-local وضعیت پیام حذف شد. retry وضعیت کانال metadata مستقل `channel_state_*` دارد و با retry انتشار اولیه مخلوط نمی‌شود؛ restart worker زمان retry و attempt قبلی را از دیتابیس ادامه می‌دهد.
- هر candidate transaction مستقل، PostgreSQL advisory lock و revalidation پس از lock دارد. بنابراین دو worker همزمان side effect تلگرام را دوبار اجرا نمی‌کنند و failure یک candidate transaction بقیه backlog را rollback نمی‌کند.
- پاسخ `429` ادامه batch را متوقف و cooldown محدود ایجاد می‌کند؛ خطای retryable و `400` terminal رفتار جدا دارند. فاصله فعلی بین فراخوانی‌های موفق تلگرام حفظ شده و batch size بدون افزایش باقی مانده است.
- observability اکنون backlog کل/due، سن قدیمی‌ترین کل/due، تعداد skipped-locked، response classها، rate-limit و failureهای retryable/terminal را گزارش می‌کند.
- schema یا migration جدیدی وجود ندارد. تغییر guard migration فقط نام ایزوله `market_stage9_*_test` را برای تست PostgreSQL scratch مجاز می‌کند.

شواهد تست:

- suite متمرکز worker/reconciliation/scratch guard برابر `41 passed` است و سناریوهای backlog `101`تایی، شکست دائمی اولین ردیف، restart، backoff، `429`، terminal `400`، observability و جلوگیری از side effect تکراری را پوشش می‌دهد.
- چهار تست واقعی PostgreSQL روی دیتابیس محافظت‌شده `market_stage9_repair_test` پاس شدند: guard نام دیتابیس، تخلیه `101` candidate در پنج cycle، عدم starvation بیست‌وپنج ردیف قدیمی در حضور صد ردیف جدید و اجرای دقیقاً یک provider edit توسط دو worker همزمان.
- regression خانواده Offer برابر `165 passed, 9 skipped` و Telegram channel/publication برابر `18 passed` است. `compileall` و `git diff --check` نیز پاس شدند.
- در اجرای گسترده sync، هشت failure ثبت شد: دو مورد در `test_sync_router_apply_item_success`، پنج مورد در `test_sync_router_receive_offer_publish` و یک مورد در `test_sync_guarantee_matrix`. هر هشت مورد با همان خروجی روی checkout موقت `main` بازتولید شدند و baseline قبلی‌اند؛ هیچ‌کدام از فایل‌های تغییرکرده Stage ۹ نیستند.

شواهد staging دو سرور:

- پنج سرویس روی میزبان خارج (`app`، `foreign_app`، `bot`، `sync_worker` و `foreign_sync_worker`) و دو سرویس روی میزبان staging ایران (`app` و `sync_worker`) با full `RELEASE_SHA=095d478b26c5afdb73cc3d454ea0b5e1c910ee15` اجرا شدند. checksum فایل runtime worker در هر هفت سرویس یکسان و Alembic هر دو دیتابیس برابر `d0b5e6f7a8c9` بود.
- worker خارجی شش وضعیت قدیمی کانال را در batch محدود خواند؛ دو failure انتقال موقت با retry پایدار ادامه یافت و backlog نهایی publication/channel هر دو صفر شد.
- preflight اولیه `MARKET-STAGE9-REPAIR-WORKER-FAIRNESS-20260714-R1` به‌درستی fail-close شد، چون پنج وضعیت publication تاریخی که در فاصله نسخه مختلط repair شده بودند در ایران اعمال نشده بودند. هیچ DB row به‌صورت مستقیم و کور تغییر نکرد؛ snapshot پشتیبان گرفته شد و پنج current-state replay امضاشده با manifest، natural identity و همان source sequenceهای اصلی `694/698/700/704/706` از ابزار رسمی `sync_repair_tool.py` اجرا شد. هر پنج مورد در مقصد `processed=1` و `Sync Item Applied` داشتند.
- preflight نهایی فقط‌خواندنی `MARKET-STAGE9-REPAIR-WORKER-FAIRNESS-20260714-R2` با deep parity پاس شد: `business_drift=0`، `critical_drift=0`، queueهای outbound/retry صفر، change log unsynced/quarantined صفر و تمام findingهای publication reconciliation صفر بودند. پنج اختلاف باقی‌مانده فقط local/volatile و مطابق قرارداد parity هستند.
- production deploy، restart، mutation تستی بازار و full-matrix جهشی انجام نشد.

ریسک باقی‌مانده و rollback:

- API تلگرام می‌تواند rate-limit یا outage داشته باشد، اما اکنون این وضعیت باعث loop سریع یا starvation بقیه backlog نمی‌شود و از طریق due backlog/oldest age قابل مشاهده است.
- rollback runtime با revert commit `095d478b` و recreate سرویس‌های staging انجام می‌شود. چون schema تغییر نکرده است rollback دیتابیس لازم نیست؛ metadata اضافه در JSON با runtime قبلی سازگار و قابل نادیده‌گرفتن است.

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

### گزارش اجرای Stage ۱۰ - ۲۰۲۶-۰۷-۱۴

وضعیت: پیاده‌سازی و دروازه تست سطح Stage تکمیل شد؛ پذیرش نهایی staging در full-matrix مشترک مراحل باقی‌مانده انجام می‌شود.

- runtime commit برابر `5a2d2578` است. سرویس canonical جدید `offer_cancel_all_service` فهرست active را snapshot می‌کند، هر آفر را با gate و transaction مستقل پردازش می‌کند و local mutation را فقط پس از lock سطری و authority/owner/status check انجام می‌دهد. در نتیجه lock/conflict یک آفر session آفر بعدی را poison یا rollback نمی‌کند.
- forwarding آفر remote از همان command identity و receipt Stage ۱۱ استفاده می‌کند. پاسخ هر آفر به `cancelled`، `already_inactive` یا `failed` نگاشت می‌شود و پاسخ API ضمن حفظ `cancelled_count`، شمارش‌های افزایشی، `complete`، `remaining_active_count` و result مستقل هر public ID را برمی‌گرداند.
- handler بات دیگر query، mutation یا forwarding جداگانه ندارد و فقط سرویس مشترک را فراخوانی، side effect آفرهای local واقعاً commit‌شده را اجرا و summary دقیق را render می‌کند. در حضور failure متن «تمام لفظ‌ها» تولید نمی‌شود.
- شمارنده active پس از batch از دیتابیس بازخوانی می‌شود و mirrorهای remote که success/already-inactive قطعی گرفته‌اند از شمارش موقت حذف می‌شوند؛ failure شمارنده بعد از commit، نتیجه لغو موفق را به خطای کاذب تبدیل نمی‌کند.
- ۳۹ تست focused، ۲۰۲ تست خانواده آفر با ۱۸ skip اختیاری، ۱۴۴ تست خانواده بات و ۴ تست واقعی PostgreSQL پاس شدند. تست PostgreSQL اثبات کرد row lock یک آفر مانع commit آفر مستقل نمی‌شود، معامله برنده lock با مانده صفر completed باقی می‌ماند و stale snapshot پس از re-read به already-inactive تبدیل می‌شود.
- تست PostgreSQL فقط روی دیتابیس guardدار `market_stage10_cancel_all_test` اجرا شد؛ allowlist ابزار migration فقط با prefix محدود `market_stage10_*` گسترش یافت و scratch database بلافاصله پس از تست حذف شد. schema runtime تغییر نکرد.

ریسک باقی‌مانده و rollback:

- production تا receiver-first rollout و فعال‌سازی کنترل‌شده receiptهای Stage ۱۱ نباید از semantics replay دقیق remote استفاده کند؛ flag کد همچنان default برابر false دارد و در این Stage production deploy یا restart انجام نشد.
- rollback runtime با `git revert 5a2d2578` و recreate سرویس‌های درگیر انجام می‌شود. چون migration یا داده ماندگار جدیدی وجود ندارد، rollback دیتابیس لازم نیست.

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

وضعیت Stage: بسته شد؛ قرارداد پایدار فرمان، receipt اتمیک، تست PostgreSQL و smoke واقعی دو سرور staging کامل شده است.

### گزارش اجرای Stage ۱۱ - ۲۰۲۶-۰۷-۱۴

پیاده‌سازی runtime در commit `ea3d0f9632dec7687efd2be392b9e4c061fb27fc`:

- payload canonical نسخه ۱ از `offer_public_id`، مالک/عامل، سطح و سرور مبدأ و علت انقضا ساخته می‌شود. `command_id` از UUIDv5 و `idempotency_key` از همان canonical payload مشتق می‌شوند؛ ID عددی آفر عمداً در fingerprint نیست تا اختلاف شناسه محلی دو دیتابیس replay را نشکند.
- schema افزایشی موجود `offer_expiry_command_receipts` با model و service خانه‌محور تکمیل شد. lockهای advisory روی command/key با ترتیب ثابت گرفته می‌شوند؛ exact replay فقط terminal outcome قبلی را برمی‌گرداند و key یا command یکسان با payload متفاوت `409` می‌گیرد.
- lookup آفر در مسیر جدید فقط با `offer_public_id` انجام می‌شود. mutation آفر و terminal شدن receipt در یک transaction commit می‌شوند؛ side effect فقط بعد از commit و فقط برای transition نخست با dedupe key مشتق از command/public-id/version dispatch می‌شود.
- HMAC، تطابق source header با payload، ممنوعیت source برابر target، home authority و owner check پیش از mutation حفظ شدند. فرمان مستقل روی آفر inactive همچنان `400` می‌گیرد و receipt ناقص ناشی از rollback باقی نمی‌ماند.
- قرارداد قدیمی بدون command identity برای rollout نسخه مختلط حفظ شده است. feature flag با پیش‌فرض `false` اضافه شد و rollout staging در دو مرحله انجام شد: ابتدا schema/receiver/caller روی هر دو peer، سپس فعال‌سازی همزمان flag و recreate فقط سرویس‌های stateless.
- receipt جدول `NO_SYNC` و home-local است؛ raw command/public ID و dedupe key در observability فقط به‌صورت hash ثبت می‌شوند. سیاست retention حداقل `365` روز است و cleanup primitive فقط terminal receiptهای قدیمی‌تر را مجاز می‌داند؛ incomplete receipt هرگز توسط آن حذف نمی‌شود. در این Stage timer حذف خودکار متصل نشده و بنابراین داده تا اجرای maintenance ایمن، بیشتر از حداقل retention نگه داشته می‌شود.

شواهد تست:

- suite متمرکز command/endpoint/forwarding/observability/sync policy برابر `62 passed` است.
- خانواده Offer برابر `193 passed, 15 skipped` و خانواده Bot trade برابر `143 passed` است.
- migration/scratch guard/sync registry/field policy برابر `52 passed` و transport/expiry/publication worker برابر `43 passed` است.
- هفت تست واقعی PostgreSQL روی دیتابیس guardدار `market_stage11_receipt_test` پاس شدند: دو درخواست همزمان یک mutation و یک side effect، replay بعد از engine restart، بی‌اثر بودن اختلاف ID عددی، rollback مشترک Offer/receipt، شکست side effect بعد از commit و repair توسط worker، رد فرمان مستقل روی inactive و دقیقاً یک Offer UPDATE outbox بدون receipt outbox. scratch database پس از اجرا حذف شد.
- `tests.test_core_events` روی candidate و همان لحظه روی `main` دقیقاً با یک failure و یک error یکسان بازتولید شد (`test_offer_trade_and_user_event_listeners` و `test_listener_sync_short_circuit_and_error_paths`)؛ این baseline از Stage ۱۱ مستقل است و هیچ فایل runtime مربوط به آن در این Stage تغییر نکرده است.
- `compileall` و single Alembic head پاس شدند. یک trailing whitespace تاریخی که به‌علت قرارگرفتن در hunk کل branch توسط `git diff --check` گزارش می‌شد، بدون تغییر رفتار حذف شد.

شواهد staging دو سرور:

- Iran WebApp/API و sync worker روی `staging.gold-trade.ir` و foreign API/bot/workers روی `staging.362514.ir` همگی با full `RELEASE_SHA=ea3d0f9632dec7687efd2be392b9e4c061fb27fc`، role صحیح و `OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED=true` اجرا می‌شوند. Alembic هر دو دیتابیس `d0b5e6f7a8c9` و جدول receipt در هر دو موجود است.
- smoke واقعی خارج به ایران با fixture یکتای staging اجرا شد. دو request همزمان با command یکسان و ID عددی متفاوت، یک پاسخ `replayed=false` و یک پاسخ `replayed=true` دادند؛ replay سوم نیز `200/replayed=true` بود. payload تغییرکرده با همان identity برابر `409` و command مستقل روی آفر inactive برابر `400` شد.
- دیتابیس خانه پس از smoke دقیقاً یک receipt terminal، یک Offer با status `expired` و یک Offer UPDATE outbox داشت. لاگ‌ها دقیقاً یک `offer_expiry_command.committed`، دو `replayed` و یک dispatch side effect نشان دادند و شناسه‌ها hash شده بودند. fixture، receipt و outbox آزمون حذف و نبود آن‌ها روی هر دو دیتابیس تأیید شد؛ sync worker ایران سپس دوباره راه‌اندازی شد.
- preflight نخست `MARKET-STAGE11-OFFER-EXPIRY-RECEIPTS-20260714-R1` fail-close شد، چون اجرای اولیه deploy محلی foreign public guard را موقتاً خاموش کرده بود و runner نام صحیح env کلید observability را دریافت نکرده بود. Nginx به حالت استاندارد `404` بازگردانده شد و secret یا داده بازار تغییر نکرد.
- preflight نهایی فقط‌خواندنی `MARKET-STAGE11-OFFER-EXPIRY-RECEIPTS-20260714-R2` با deep parity، binding کامل SHA، storage identity جدا، sync health سالم و بدون failed check پاس شد. production deploy، restart یا mutation انجام نشد.

ریسک باقی‌مانده و rollback:

- production همچنان flag را به‌صورت پیش‌فرض `false` دارد. rollout production باید همان ترتیب receiver-first و SHA parity را رعایت کند؛ rollback فوری با false کردن flag در هر دو peer و recreate سرویس‌های stateless انجام می‌شود.
- rollback کد با revert commit runtime انجام می‌شود، اما receipt table و داده terminal تا پایان retry/retention window حذف نمی‌شوند. endpoint قدیمی additive باقی مانده و schema downgrade هنگام وجود caller جدید مجاز نیست.

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

### گزارش اجرای Stage ۱۲ - ۲۰۲۶-۰۷-۱۴

وضعیت: پیاده‌سازی و دروازه تست سطح Stage تکمیل شد؛ پذیرش viewport و چندسروری در full-matrix نهایی staging انجام می‌شود.

- runtime commit برابر `e268210e5af70db10253bf88c64a6783728e1214` است. endpoint افزایشی `/api/offers/page` با cursor نسخه‌دار و مقید به فیلتر، ترتیب پایدار `(created_at DESC, id DESC)` و page size حداکثر ۱۰۰ اضافه شد. endpoint قدیمی `skip/limit` حذف یا تغییر قرارداد نداده و فقط tie-break شناسه به ordering آن افزوده شده است.
- فیلترهای جهت، نقد/فردا، کالا و «لفظ‌های شما» در backend اعمال می‌شوند. cursor فیلتر قبلی در tab یا فیلتر جدید پذیرفته نمی‌شود و با `400` fail-closed است. مجوز accountant و effective owner در هر صفحه دوباره اعمال می‌شود و cursor هیچ مجوز جدیدی ایجاد نمی‌کند.
- WebApp load-more، empty/end state، خطای initial/page و retry را دارد. merge صفحات بر اساس `offer_public_id` dedupe می‌شود؛ realtime نیز در صورت وجود public id همان هویت سراسری را ترجیح می‌دهد و id محلی WebApp را با id سرور مقابل جایگزین نمی‌کند. event producerهای اصلی public id را به‌صورت additive می‌فرستند و مصرف eventهای قدیمی فاقد آن همچنان پشتیبانی می‌شود.
- index partial جدید `ix_offers_active_created_id` به‌صورت `CREATE INDEX CONCURRENTLY` افزوده شد. migration روی دیتابیس scratch واقعی upgrade شد، downgrade تا `d0b5e6f7a8c9` و upgrade مجدد تا head پاس شد؛ Alembic دقیقاً یک head به نام `e0b5e6f7a8ca` دارد.
- تست PostgreSQL واقعی با ۱۳۷ آفر، timestampهای مساوی، pageهای ۵۱ و ۱۰۰، همه فیلترها و insert/expire میان صفحات در `4` تست پاس شد. scratch فقط با الگوی `market_stage12_*_test` مجاز بود و پس از تست حذف شد؛ دیتابیس runtime staging و production انتخاب یا mutation نشدند.
- `23` تست cursor/endpoint/migration guard، `203` تست خانواده آفر با `18` skip، `77` تست خانواده معاملات، `145` تست خانواده معامله بات و `80` تست frontend پاس شدند. build کامل frontend، `compileall`، `git diff --check` و Alembic single-head نیز پاس شدند.
- suiteهای قدیمی sync که روی Stage ۹ به بعد baseline failure دارند، برای اثبات event projectionهای تغییرکرده به تست‌های متمرکز تفکیک شدند؛ انتظارهای additive public id در تست‌های expiry به‌روز شد و failure تازه‌ای به Stage ۱۲ منتسب نماند.

ریسک باقی‌مانده و rollout/rollback:

- rollout باید API و migration را پیش از frontend انجام دهد؛ کلاینت جدید به endpoint additive وابسته است ولی کلاینت قدیمی با endpoint قبلی کار می‌کند. index concurrent از lock طولانی جدول جلوگیری می‌کند.
- silent refresh صفحه اول، pageهای بارگذاری‌شده را حفظ می‌کند و eventهای terminal آن‌ها را حذف می‌کنند. صحت نهایی این تعامل، عدم overlap کنترل‌ها در mobile/desktop و رفتار چند worker در full-matrix staging سنجیده می‌شود.
- rollback فوری frontend با بازگرداندن `MarketView`، `OffersList` و `useOffers` به endpoint قدیمی ممکن است. endpoint و index افزایشی می‌توانند بدون استفاده باقی بمانند؛ حذف index فقط پس از rollback همه callerهای جدید و با migration downgrade مجاز است.
- production deploy، restart و mutation انجام نشد.

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

### گزارش اجرای Stage ۱۳ - ۲۰۲۶-۰۷-۱۴

وضعیت: پیاده‌سازی و دروازه تست سطح Stage تکمیل شد؛ پذیرش deploy شده در ماتریس نهایی مشترک staging انجام می‌شود.

- runtime commit برابر `473d8952ea79f804abeec3e4a5845eb0ca3392ac` است. endpointهای افزایشی `/api/trades/my/page` و `/api/trades/with/{other_user_id}/page` با cursor پایدار `(created_at, id)` اضافه شدند و endpointهای قدیمی برای سازگاری نسخه مختلط باقی ماندند.
- cursor به viewer، target و همه فیلترها متصل است، اما مجوز ایجاد نمی‌کند؛ relation و دسترسی customer/super-admin در هر صفحه دوباره از دیتابیس بررسی می‌شوند. جهت خرید/فروش از دید موضوع همان تاریخچه محاسبه می‌شود.
- WebApp صفحه‌های ۵۰تایی را با dedup دریافت می‌کند، خطای ادامه صفحه رکوردهای قبلی را حذف نمی‌کند و retry همان cursor را تکرار می‌کند. فیلترهای کالا، جهت و تسویه در pagination و export یکسان اعمال می‌شوند.
- migration افزایشی `f0b5e6f7a8cb` دو index cursor برای `offer_user_id` و `responder_user_id` می‌سازد. create/drop به‌صورت concurrent است و downgrade به head قبلی و upgrade مجدد روی scratch PostgreSQL پاس شد.
- ۲۳ تست cursor/authorization/guard، ۵ تست واقعی PostgreSQL با ۱۲۸ معامله، ۷۷ تست خانواده معاملات، ۲۰ تست helper/export و ۶۳ تست frontend پاس شدند. build کامل Vite، `compileall`، single Alembic head و `git diff --check` نیز سبز هستند.
- artifact اصلی PostgreSQL با SHA-256 برابر `99f9cbf7157643beed3d91b2df35ab6214e73d38b9d515e41732766f5433b7dd` ثبت شد. دیتابیس scratch با نام محافظت‌شده `market_stage13_history_test` پس از تست حذف شد و هیچ داده staging runtime یا production تغییر نکرد.

ریسک و rollback:

- هر page حداکثر ۱۰۰ row می‌خواند و indexهای participant/order مانع scan کامل رایج می‌شوند. export فعلی همچنان کل نتیجه مجاز را تولید می‌کند و عمداً در این Stage تغییر معماری نکرده است.
- rollback runtime با revert commit `473d8952` انجام می‌شود. اگر rollback schema لازم باشد، ابتدا runtime قدیمی rollout و سپس migration به `e0b5e6f7a8ca` downgrade می‌شود؛ endpointهای قدیمی در تمام مدت قابل استفاده‌اند.

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

### گزارش اجرای Stage ۱۴ - ۲۰۲۶-۰۷-۱۴

وضعیت: پیاده‌سازی و دروازه تست سطح Stage تکمیل شد؛ پذیرش multi-instance واقعی در full-matrix نهایی staging انجام می‌شود.

- runtime commit برابر `53aabec40ac2c54c81588c86b43dda7414d5f00f` است. Redis transport اصلی رویدادهای عمومی است و direct local broadcast فقط در exception قطعی publish اجرا می‌شود؛ مقدار subscriber count برابر صفر fallback را فعال نمی‌کند.
- هر event عمومی یک شناسه پایدار دارد که در payload تخت Redis با کلید رزروشده و سازگار با listener قدیمی حمل می‌شود. listener جدید آن را به envelope WebSocket و `id` استاندارد SSE منتقل می‌کند و projection عمومی همچنان کلید رزروشده و داده‌های خصوصی را حذف می‌کند.
- dedup محدود ۵۱۲تایی هم در مرز هر WebSocket سرور و هم در WebApp اعمال می‌شود. cache سمت WebApp در reconnect همان صفحه باقی می‌ماند، event جدید را حذف نمی‌کند و پیام نسخه قدیمی بدون event id همچنان پذیرفته می‌شود. در publish مبهم که Redis event را پذیرفته ولی پاسخ خطا شده باشد، fallback همان event id را استفاده می‌کند.
- routing خصوصی Stage ۱، notificationهای user-scoped و ممنوعیت انتشار عمومی `trade:created` تغییر نکرده‌اند. sync-apply همچنان فقط side effect محلی realtime دارد و outbound sync تازه ایجاد نمی‌کند.
- ۲۵ تست backend realtime پاس شدند و حالت‌های Redis سالم، subscriber صفر، failure/fallback، دو manager مستقل شبیه دو worker، duplicate در یک connection، event جدید، SSE id و fail-closed خصوصی را پوشش دادند. ۴۲ تست frontend مربوط به WebSocket/Offers/Market و build کامل production نیز پاس شدند.
- یک تست نامرتبط sync با انتظار `ignored` مقدار `deferred` گرفت؛ همان failure به‌تنهایی و بدون تغییر روی commit والد `969f79b2` عیناً بازتولید شد، پس regression این Stage نیست. فایل‌های runtime آن مسیر در Stage ۱۴ تغییر نکرده‌اند.

ریسک باقی‌مانده و rollback:

- fallback هنگام outage کامل Redis فقط connectionهای worker ناشر را پوشش می‌دهد؛ پوشش سراسری چند worker بدون transport مشترک ممکن نیست. این رفتار عمداً degraded-local است و health Redis باید alert مستقل داشته باشد.
- rollback runtime با `git revert 53aabec4` انجام می‌شود و schema یا migration ندارد. قبل از release production، تست واقعی چند connection روی چند worker در staging و شمارش یک event به‌ازای هر subscriber باید در ماتریس نهایی پاس شود.

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

### گزارش اجرای Stage ۱۵ - ۲۰۲۶-۰۷-۱۴

وضعیت: پیاده‌سازی و دروازه تست سطح Stage تکمیل شد؛ پذیرش deploy شده و sync parity در ماتریس نهایی مشترک staging انجام می‌شود.

- runtime commit برابر `b214155e222dcbc58d242ffd843e3834fc868059` است. fingerprint نسخه ۱ در سرویس canonical از source/home، owner/actor، کالا، جهت، settlement، quantity، price، wholesale، lotهای مرتب‌شده، notes canonical و public identity منبع republish ساخته می‌شود.
- unique constraint سراسری `offers.idempotency_key` حفظ شد و lookup نیز سراسری شد. exact replay فقط پس از تطبیق fingerprint برمی‌گردد؛ همان key با intent متفاوت، برخورد key میان دو owner، metadata ناقص یا نسخه ناشناخته با `409` کنترل‌شده و بدون افشای آفر قبلی رد می‌شود.
- migration افزایشی `f1b6e7f8a9dc` دو ستون nullable `idempotency_fingerprint_version` و `idempotency_fingerprint` اضافه می‌کند. رکوردهای قدیمی بدون fingerprint فقط در صورت بازسازی دقیق همه فیلدهای پایدار replay می‌شوند؛ در حالت مبهم fail-closed هستند. fingerprint در payload داخلی sync حمل می‌شود و در projection عمومی بازار قرار نمی‌گیرد.
- exact replay قبل از guardهای متغیر باقی مانده، اما ابتدا payload match می‌شود. replay و race موفق سهمیه Stage ۷، publication کانال، realtime event و شمارنده را دوباره مصرف نمی‌کنند. conflict ناشی از unique race داخل سرویس canonical پس از rollback به replay یا mismatch قطعی تبدیل می‌شود.
- ۵۳ تست focused با ۵ skip مخصوص PostgreSQL، ۵ سناریوی واقعی concurrency PostgreSQL به‌همراه guard نام دیتابیس، ۷ تست واقعی regression سهمیه Stage ۷، ۲۱۵ تست خانواده Offer با ۲۳ skip اختیاری، ۱۴۵ تست خانواده Bot trade و ۳۳ تست MarketView پاس شدند. build کامل Vite، `compileall`، single Alembic head، migration downgrade/upgrade و `git diff --check` نیز سبز هستند.
- artifact اصلی PostgreSQL با SHA-256 برابر `4aedea3f2ef0bf968ced212977cecf6c2ee22cd6ece09d41feed18211704d588` ثبت شد. هر دو دیتابیس scratch پس از تست حذف شدند و production یا دیتابیس runtime staging تغییر نکرد.

ریسک و rollback:

- rollout باید additive باشد: migration روی هر دو دیتابیس، سپس receiver/sync runtime و بعد creatorهای WebApp/Bot. در نسخه مختلط، runtime قدیمی کلیدهای ناشناخته payload را حذف می‌کند؛ برای جلوگیری از parity موقت، creator جدید قبل از receiver جدید فعال نمی‌شود.
- rollback runtime با revert commit `b214155e` انجام می‌شود. حذف ستون‌ها فقط بعد از برگشت تمام runtimeها و تخلیه retry/outbox مجاز است؛ سپس migration به `f0b5e6f7a8cb` downgrade می‌شود.

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

### گزارش اجرای Stage ۱۶ - ۲۰۲۶-۰۷-۱۴

وضعیت: پیاده‌سازی و دروازه تست سطح Stage تکمیل شد؛ پذیرش نهایی UI/API/sync روی staging در full-matrix مشترک انجام می‌شود.

- runtime commit برابر `6da6b9a6dd87ba432c04b1c013f2232bc90ba825` است. helper مستقل `coalesce_offer_remaining_quantity` فقط در نبود واقعی مقدار (`None`) به quantity اولیه برمی‌گردد و صفر، مقدار جزئی و مقدار کامل را بدون تغییر نگه می‌دارد.
- occurrenceهای ناامن در serializer و event ثبت آفر، realtime آفر sync‌شده، suggestion معامله API، مسیر اجرای معامله بات، refresh پیشنهاد خصوصی بات، WebApp OffersList و ابزار probe اصلاح شدند. scaffold غیرفعال `src/core/services/offer_service.py` نیز برای جلوگیری از بازگشت باگ در فعال‌سازی آینده همان semantics صریح را دارد.
- WebApp برای صفر هیچ دکمه معامله‌ای نمی‌سازد و مقدار malformed را fail-closed برابر صفر در نظر می‌گیرد؛ فقط `null/undefined` اجازه fallback به quantity اولیه می‌دهند. merge رویداد realtime با مقدار صفر از قبل سالم بود و regression آن حفظ شد.
- canonical sync payload در `core/offer_sync_payload.py` و ساخت دکمه کانال در `telegram_offer_channel_service.py` از قبل صفر را صریح حفظ می‌کردند؛ runtime آن‌ها بازنویسی نشد و فقط contract test صفر اضافه شد.
- ۶۸ تست focused، ۱۴۵ تست خانواده بات، ۲۰۳ تست خانواده آفر با ۱۸ skip اختیاری، ۷۷ تست خانواده معاملات و ۷۱ تست frontend پاس شدند. build کامل frontend، `compileall` و جست‌وجوی هدفمند occurrenceهای fallback ناامن نیز پاس شدند.
- اجرای broad فایل `test_sync_router_receive_offer_publish` پنج failure قدیمی در بخش `receive_sync_data` داشت؛ همان پنج مورد قبلاً در گزارش Stage ۹ روی `main` بازتولید و baseline شده‌اند. دو تست projection sync مرتبط با این Stage، از جمله صفر، جداگانه سبز هستند و runtime receive/apply تغییر نکرده است.

ریسک باقی‌مانده و rollback:

- داده malformed از backend دیگر آفر را ظاهراً پُر نمی‌کند، اما باید در staging همراه با observability بررسی شود تا producer ناسالم پنهان نماند. schema یا migration وجود ندارد و rollback runtime با `git revert 6da6b9a6` انجام می‌شود.

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
