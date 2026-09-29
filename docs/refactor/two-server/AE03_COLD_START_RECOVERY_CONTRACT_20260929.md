# AE03: قرارداد شروع دوباره پس از تغییر HEAD یا Assignment

وضعیت: **طراحی اجرایی؛ هنوز مجوز Start نیست.** این قرارداد فقط به چرخهٔ توسعهٔ دو‌سایته مربوط است و هیچ تغییری در `main`، تولید، DNS یا Web Writer نمی‌دهد.

## چرا چرخهٔ کنونی با Start ساده زنده نمی‌شود؟

در ممیزی ۲۹ سپتامبر ۲۰۲۶، Supervisor در `STOPPED` و Broker در `IDLE` بود. `integration.active_assignment_id` مقدار نداشت. تنها epoch موجود به تلاش سومِ terminal و HEAD قدیمی `908f66d` تعلق دارد؛ checkpoint پذیرفته‌شده به `f1ee209` تعلق دارد و با HEAD فعلی منطبق نیست. `validate_rescope_epoch_startup` به‌درستی اجازه نمی‌دهد از این اختلاف، پذیرشِ ساختگی برای کامیت‌های بعدی بسازیم. گیت مانیتور نیز انتظار رفرنس‌های قدیمی دارد؛ تصحیح زودهنگام آن ممکن است یک `RUNNING` کهنه را خودکار دوباره اجرا کند.

### سناریوی A: ادامهٔ مأموریت قدیمی

**وضعیت:** epoch قدیمی terminal و مأموریت آن بسته است. **رخداد:** همان Assignment یا task دوباره فعال شود، یا epoch/receipt آن حذف شود. **رفتار لازم:** توقف؛ هیچ Provider، Codex child، commit یا retry جدید آغاز نشود. تغییر نام task، زمان‌سنج و reset شمارنده مجوز تکرار نیست. تمام فایل‌های شاهد قدیمی باید دست‌نخورده بمانند.

### سناریوی B: مأموریت تازه، مبنای ممیزی‌شده

**وضعیت:** Codex یک Assignment تازه و محدود، روی HEAD تمیز کنونی صادر کرده است؛ کار آن از کار terminal قبلی مستقل است. **رخداد:** Supervisor می‌خواهد epoch تازه‌ای بسازد. **رفتار لازم:** ابتدا یک سند پذیرشِ مبنای جدید به‌صورت خصوصی، نسخه‌دار، canonical، حداکثر ۱۶ KiB و بدون overwrite ثبت شود؛ سند فقط «این HEAD مبنای شروع کار تازه است» را گواهی می‌کند، نه اینکه همهٔ کامیت‌های بین checkpoint قدیمی و HEAD کنونی را به‌عنوان خروجی یک چرخهٔ `CONTINUE` جعل کند. سند باید HEAD، سه protected ref، control blobها، شناسه و SHA محدودهٔ Assignment تازه، هش checkpoint قدیمی و نام و هش receipt نهایی epoch قبلی را به‌صورت دقیق bind کند. نام فایل مشتق‌شده از repository، شناسهٔ نسل تازه است. هیچ محتوای secret، prompt، دادهٔ کاربر یا خطای آزاد در آن قرار نگیرد.

پیش از انتشار سند، زیر همان قفل Supervisor باید احراز شود: مخزن تمیز است؛ فرایند Supervisor دیگری مالک اجرا نیست؛ STOP وجود ندارد؛ epoch قبلی با reader امن، پیوسته و terminal است؛ فایل‌های wait/claim/submission بدون نتیجهٔ نهاییِ معتبر یا درخواست مبهم وجود ندارند؛ Broker در وضعیت بدون اجرای فعال است؛ Assignment تازه از نظر مسیر با Assignment قبلی هم‌پوشانی ندارد. اگر بررسی هم‌پوشانی الگوهای مسیر قطعی نیست، صدور سند رد شود. task آینده نیز باید از reservation معنایی موجود عبور کند؛ سند پذیرش به‌تنهایی هیچ task یا Provider را مجاز نمی‌کند. هر وضعیت نامعلوم `BLOCKED` است، نه «احتمالاً امن».

Reader آغاز چرخه تنها سندی را می‌پذیرد که ۰۶۰۰، regular، single-link، بدون symlink/hardlink، با ساختار و بایت‌های canonical باشد و *اکنون* نیز تمام bindingهای مخزن، Assignment و شواهد قبلی‌اش دوباره برقرار باشند. انتشار سند یا ساخت epoch اگر در میانه قطع شود، نباید به retryِ Provider یا پذیرش ناقص تعبیر شود. سند فقط یک انتقال نسل را مجاز می‌کند و به‌تنهایی wake سهمیه، اجرای Provider یا پذیرش نتیجهٔ Codex نیست.

سند v1 در `autopilot/adoptions/<current-rescope-epoch-basename>.json` نگهداری می‌شود؛ نام فایل از هویت کامل repository تازه مشتق می‌شود، نه از ساعت یا نام task. کلیدهای دقیق آن عبارت‌اند از `schema_version="1.0"`، `kind="TERMINAL_EPOCH_COLD_ADOPTION"`، `new_repository` (نگاشت canonical شامل Assignment تازه)، `old_epoch_basename`، `old_repository_head`، `old_terminal_receipt_sha256`، `prior_checkpoint_sha256` و `issued_at_utc` با قالب UTC ثانیه‌ای `YYYY-MM-DDTHH:MM:SSZ`. هیچ فیلد `approved=true`، issuer خوداظهار، retry budget یا وضعیت mutable در سند مجوز محسوب نمی‌شود. codec باید HEAD متفاوت، protected refهای یکسان، شناسهٔ Assignment متفاوت و مسیرهای مجازِ literal و بدون هم‌پوشانی را کنترل کند؛ glob یا ابهام یعنی رد. Reader در زمان استفاده، تمام شواهد قبلی را دوباره از دیسک می‌خواند و با سند تطبیق می‌دهد.

### سناریوی C: تغییر معمول Assignment پس از یک `CONTINUE` معتبر

اگر checkpoint آخر واقعاً با HEAD، protected ref و controls کنونی منطبق است و فقط Assignment عوض شده، همان قرارداد انتقال نسل با شاهد پذیرش معتبر قبلی اجرا می‌شود. وضعیت mutable `status.json`، ساعت فایل یا HEAD به‌تنهایی کافی نیست. هر run یا claim پس از checkpoint که تعیین‌تکلیف نشده باشد، انتقال را مسدود می‌کند. این حالت باید آزمون جداگانه داشته باشد تا بعد از بازگرداندن چرخه، هر تعویض Assignment دوباره به بن‌بست نرسد.

## ترتیب پیاده‌سازی و معیار پذیرش

1. یک reader/validator محدود و فقط‌خواندنی برای شاهد epoch و سند انتقال بنویس؛ ابتدا آزمون‌های لینک، permission، اندازه، schema، SHA/HEAD/refs/scope drift و receipt نامعتبر را قرمز/سبز کن. در این مرحله هیچ Start مجاز نیست.
2. ناشر one-shot را زیر قفل موجود، با `O_EXCL`/no-follow و `fsync` فایل و دایرکتوری اضافه کن. تکرار انتشار یا شکست پس از fsync باید fail-closed باشد؛ سند قدیمی هرگز بازنویسی یا پاک نشود.
3. گیت startup را فقط برای سند معتبر انتقال گسترش بده؛ رفتار فعلیِ «بدون سند، checkpoint نامنطبق = BLOCKED» عیناً بماند. از receipt کهنه یا `status.json` مجوز بازپخش استخراج نکن.
4. ماتریس hermetic شامل terminal قدیمی، فقدان/خرابی checkpoint، Assignment جدید/تکراری، هم‌پوشانی مسیر، wait/claim/intent مبهم، STOP، رقابت دو Start، crash پیش/پس انتشار و تغییر رفرنس بین issuer و reader را اجرا کن. Gemini Worker پیاده‌سازی/آزمون و Reviewer مستقل بازبینی کنند؛ Codex بایت، معنا، تست، Git و مرزهای ایمنی را تأیید کند.
5. فقط سپس روی همین سرور، Assignment تازه و واحدی مستقل صادر کن، شواهد زنده را ممیزی و سند پذیرش مبنا را منتشر کن. مانیتور تا اثبات restart fence فقط گزارشگر باشد. پس از `--check` سالم، یک Start محدود و مشاهدهٔ کوتاه انجام شود؛ Broker/Provider نباید مأموریت قدیمی را اجرا کنند.

این قرارداد راه میان‌بُر برای تکمیل C5d/e نیست: wake، race بین STOP و شروع Provider، fault matrix، cohort و C4c همچنان گیت‌های جداگانه‌اند. تا اثبات گام‌های بالا، چرخهٔ unattended متوقف می‌ماند و کار توسعه می‌تواند فقط با Worker/Reviewer و تأیید نهایی Codex به‌صورت واحدهای مستقل جلو برود.

## شاهد پذیرفته‌شدهٔ گام ۱

ممیز فقط‌خواندنی `inspect_terminal_rescope_epoch` در `scripts/two_site_cold_start_epoch.py` ساخته شد. پیشنهاد Gemini پس از یک اصلاح آزمون/reader و اصلاح دو assertion ضعیف، با ۱۴ آزمون ایزوله، تأیید Reviewer مستقل و ۴۹ آزمون نهاییِ مرتبط پذیرفته شد. ممیزی فقط‌خواندنیِ شواهد واقعی، تنها epoch موجود را با HEAD `908f66d03b9e66d9aac6954b4e5265e07bb5e8c8` و SHA-256 receipt نهایی `f309e7e0cf00c511d518f39d32b5a8485900365f59dd9d30085c65d38da7a36d` معتبر یافت. در پوشهٔ runها فایل wait، claim، submission یا ack ادامهٔ سهمیه یافت نشد. این شاهد هنوز **مجوز bootstrap یا Start نیست**؛ گام‌های ۲ تا ۵ بازند.

codec خالص `scripts/two_site_cold_start_adoption_record.py` نیز با همان schema v1 اضافه شد. builder، validator، serializer و parser فقط نگاشت‌ها و بایت‌های canonical را بررسی می‌کنند؛ مقایسه با audit تازه، SHA checkpoint و هویت repository شرط پذیرش است. پیشنهاد Gemini پس از اصلاح یک assertion ناممکن و افزودن پوشش cross-binding، با ۱۰ آزمون ایزوله، تأیید Reviewer مستقل و ۵۹ آزمون نهاییِ مرتبط پذیرفته شد. این فایل نه سند را روی دیسک می‌نویسد و نه startup را باز می‌کند؛ ناشر one-shot، reader دیسکی و اتصال گیت همچنان بازند.

ذخیره‌سازی یک‌باره و reader سخت‌گیرانه در `scripts/two_site_cold_start_adoption_storage.py` اضافه شد: زیر قفل انحصاری موجود Supervisor، audit واقعی terminal و checkpoint معتبر را دوباره می‌خواند، سند را فقط یک بار در `adoptions/<epoch>.json` با مالکیت/مجوز خصوصی و `fsync` منتشر می‌کند و پس از لینک، هر شکست را مبهم اما بدون حذف سند نگه می‌دارد. reader نام قطعی را از repository تازه مشتق می‌کند و بایت‌های canonical را دوباره به شاهدهای دیسک bind می‌کند؛ نبود واقعی فایل `None` است، نه تأیید. پیاده‌سازی اولیهٔ Gemini در آزمون ایزوله به‌دلیل fixture ساختگی checkpoint و سپس استفاده از serializer فشرده به‌جای serializer واقعی پروژه رد شد؛ اصلاح نهایی Codex و آزمون drift بین دو خواندن انجام شد. ۱۰ آزمون خودِ ذخیره‌سازی و ۶۹ آزمون مرتبط سبزند. Gemini بازبینی مستقلِ نسخهٔ پیش از آخرین سخت‌گیری drift را VERIFIED گزارش کرد و Codex تغییر نهایی و آزمون آن را تأیید کرد. این واحد هنوز به startup متصل نیست و هیچ سندی برای HEAD زنده منتشر نشده است؛ گیت startup، Assignment تازه، fence مانیتور و Start محدود همچنان بازند.

اسکنِ اولیهٔ فقط‌خواندنیِ runها در `scripts/two_site_cold_start_pending_scan.py` نیز افزوده شد. زیر قفل Supervisor و با descriptorهای pin‌شده، حداکثر ۵۱۲ run و ۱۲۸ entry در هر run را به‌صورت streaming می‌پیماید؛ قبل از ساخت لیستِ بزرگ متوقف می‌شود. وجود هر فایل quota/wait/claim/submission/lineage/ack ــ حتی اگر ظاهراً نهایی باشد ــ برای این انتقال اولیه مانع است تا با شاهد معتبرِ جداگانه تعیین‌تکلیف شود. status یا زمان فایل مجوز محسوب نمی‌شود. پیشنهاد Gemini پس از اصلاحِ پیمایش نامحدود با ۱۰ آزمون ایزوله و Reviewer مستقل پذیرفته شد؛ ۷۹ آزمون مرتبط گذشت. ممیزی فقط‌خواندنیِ زنده روی ۲۰۶ run موجود، زیر قفل انحصاری Supervisor، `CLEAN` بود. این نتیجه نه quiescence بروکر، نه اعتبار Assignment تازه و نه گیت startup را ثابت نمی‌کند؛ سند پذیرش هنوز منتشر نشده است.
