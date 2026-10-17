# پلن اجرایی بهینه‌سازی چرخهٔ توسعه

- شناسه: `AE-20260919`؛ تاریخ بررسی: `2026-09-19 UTC`.
- وضعیت هنگام نگارش: `PLANNED_NOT_IMPLEMENTED`. وضعیت جاری: AE01 از نظر فنی پذیرفته شده؛ مسیر checkpoint و warm restart در AE02 با [شواهد ۲۳ سپتامبر](AE02_EVALUATION_20260923.md) تأیید شده‌اند، اما اثر مصرف/سرعت `UNKNOWN` و بازیابی پایدار RESCOPE در AE03 باز است.
- محل اجرا: شاخهٔ `refactor/two-site-architecture`؛ بدون تغییر `main`، محیط عملیاتی، DNS یا Writer.
- مبنای کد هنگام ثبت: `3a459e38`؛ شواهد تحلیلی زیر مربوط به پنجرهٔ مشخص‌شده‌اند، نه تمام تاریخ پروژه.
- مرجع حاکم فعلی: [PROPOSAL_AUTOMATION.md](PROPOSAL_AUTOMATION.md). این سند مجوز اجرای خودکار مراحل جدید یا جایگزین قرارداد فعلی نیست. در قدم بعد، تغییر قرارداد فقط همراه پیاده‌سازی، تست و پذیرش متناظر انجام می‌شود.
- توضیح مالک، ۲۰۲۶-۱۰-۱۷: بازنگری فنیِ لازم در بودجهٔ بسته‌ها تا پایان ریفکتور به تأیید مجدد او نیاز ندارد. ممنوعیت افزایش سقف در طراحی اولیه، مانع بازنگریِ محدود، اندازه‌گیری‌شده، نسخه‌دار و مستقل بازبینی‌شده نیست؛ نه مجوز افزایش عمومی یا override است و نه کاهش کیفیت. قواعد [بازنگری بودجه](PROPOSAL_AUTOMATION.md#بازنگری-فنی-بودجه-بدون-انتظار-برای-مالک) حاکم‌اند؛ اعداد فعلی تا پذیرش پیاده‌سازی متناظر معتبر می‌مانند.
- این شماره‌گذاری مستقل از «بستهٔ سوم» بهینه‌سازی‌های قبلی است؛ آن بسته و telemetry موجود حذف یا از نو ساخته نمی‌شوند.

- 2026-09-20: AE01-00 documentation/scope is under final acceptance, exact evidence—not HEAD alone—resolved the capacity-interrupted P1 receipt, and AE01-10 is next after this documentation commit. No AE01 code package is implemented; plan details in [AE01_IMPLEMENTATION_PLAN.md](AE01_IMPLEMENTATION_PLAN.md).
- 2026-09-23: آزمون زندهٔ CLI پین‌شدهٔ `0.153.4` با حساب فعلی، `gpt-6-sol/xhigh` را با HTTP 400 رد کرد؛ تنظیم عملیاتی به `gpt-5.6-sol/xhigh` که آزمون زندهٔ خواندنی را گذراند برگشت. `--check` به‌تنهایی اثبات دسترسی مدل نیست؛ تغییر مدل فقط پس از smoke کوتاه و یک‌باره مجاز است، نه probe در هر چرخه.
- 2026-09-23: آزمون همسان با همان ورود ChatGPT در CLI پایدار `0.156.1` برای `gpt-6-sol/xhigh` و code-mode موفق شد. پس نسبت‌دادن HTTP 400 نسخهٔ قبلی به فقدان دسترسی حساب نادرست بود؛ تفاوت مشاهده‌شده نسخهٔ CLI است. پین جدید مستقل از افزونه و قابل rollback است؛ اثر مصرف/سرعت همچنان باید با واحدهای هم‌خانواده سنجیده شود.

## ۱. هدف و تصمیم اصلی

تعریف موفقیت مالک: **کار پذیرفته‌شدهٔ بیشتر با مصرف و زمان برابر یا کمتر، یا همان کار با مصرف و زمان کمتر؛ با حفظ کیفیت یا بهبود آن.**

اولویت، کاهش کار تکراری Codex در انتخاب کار، آماده‌سازی context، ساخت task و بازیابی پس از توقف است. افزایش تعداد Worker قدم اول نیست: در نمونهٔ بررسی‌شده، بیشتر زمان خارج از فراخوانی Gemini مصرف شده است. هیچ درصد بهبود یا نبود اثر منفی، قبل از اندازه‌گیری و کنترل کیفیت اثبات‌شده تلقی نمی‌شود.

ترتیب وابستگی:

`AE-00 سنجش → AE-01 آماده‌سازی → AE-02 checkpoint → AE-03 بازیابی → AE-04 شواهد → AE-05 آماده‌سازی هم‌پوشان → AE-06 تصمیم موازی‌سازی Worker`

هر بسته جداگانه تست، review، commit و ارزیابی می‌شود. تکمیل تمام بسته‌ها شرط ادامهٔ توسعهٔ P1 نیست؛ بین بسته‌ها چرخهٔ سالم باید به کار عادی برگردد. اگر بسته‌ای ارزش قابل‌اندازه‌گیری ندارد، متوقف می‌شود؛ صرف وجود آن در این پلن دلیل ساختنش نیست.

## ۲. شواهد و محدودیت استنتاج

| مشاهده | شاهد ثبت‌شده | نتیجهٔ قابل‌اتکا |
| --- | --- | --- |
| زمان Gemini سهم اصلی نیست | ۱۹ چرخهٔ موفق تا `cycle-0001-20260919T081117Z-2174794`: مجموع زمان چرخه‌ها `13068.2s` و Provider برابر `1959.7s`، تقریباً ۱۵٪ | ابتدا زمان خارج Provider را تفکیک کنیم؛ ۸۵٪ باقی‌مانده تماماً اتلاف نیست و شامل تست و review ضروری هم هست. |
| آماده‌سازی پرهزینه است | همان چرخهٔ پایانی، commit `1d2b8ce0`: `844.291s`، Provider `103.565s`، ورودی uncached Codex برابر `168917`، cached برابر `6900864` و خروجی `28047` | این یک نمونه است، نه میانگین یا صورت‌حساب. بهینه‌سازی باید کار Coordinator را هدف بگیرد. |
| خطای قابل‌پیشگیری قبل از Provider | در همان چرخه ۷۳ فرمان، چهار بار build؛ سه شکست اولیه: نبود assignment token، نبود context مدل و نبود context session؛ چهار خطای `jq` ناشی از حدس schema/دسترسی نامناسب | ابزار آماده‌سازی schema-aware و گزارش یک‌جای نقص‌ها ارزش آزمایش دارد. اسکن assignment خروجی حدود ۶۱۶KB از نام مسیرها تولید کرده بود، نه خروجی موردنیاز برای تصمیم. |
| رشد context هزینه‌ساز است | چرخهٔ ۸ مورخ ۱۷ سپتامبر: ۱۳ تست Worker موفق، ولی بستهٔ Reviewer از سقف ۱۶۰KB عبور کرد؛ چند شکست دیگر transport/requirements در reconciliation | فقط کوچک‌بودن ورودی Worker کافی نیست؛ مسیر Reviewer هم از ابتدا بودجه‌بندی شود. سقف فعلی افزایش نیابد. |
| review نهایی ضروری است | `cycle-0002-20260917T094312Z-3744262`: Gemini تأیید کرد، Codex نبود assertion صریح جلوگیری از ساخت `InlineKeyboardMarkup` را یافت | عبور تست‌ها و Gemini به‌تنهایی کافی نیست؛ review معنایی نهایی باقی می‌ماند. |
| checkpoint شکننده است | `persisted_continuation()` فقط `status.json` را می‌خواند؛ همان فایل با RUNNING/BLOCKED/STOPPED جایگزین می‌شود | بعد از restart/quota ممکن است hint موفق قبلی از دست برود و جهت‌یابی تکرار شود. |
| دادهٔ توقف کامل نیست | ۴۵ رکورد شکست پس از quota فاقد usage؛ از streamهای باقی‌مانده ۱۹ مورد صریحاً usage-limit، ۲۶ مورد بدون شاهد محفوظ کافی | usage نامعلوم، صفر نیست؛ تمام ۴۵ مورد را نباید quota قطعی نامید. |
| آمار commit به‌تنهایی گمراه‌کننده است | دو پنجرهٔ ۲۴تایی Sol: قبل ۲۳ commit با حدود `162284` uncached/commit؛ بعد ۱۹ commit با حدود `170535`؛ نوع کارها متفاوت | ادعای بهبود قطعی از تعداد commit یا فقط چرخه‌های موفق پذیرفته نیست؛ denominator باید کار معنایی پذیرفته‌شده باشد. |
| زیرساخت بخشی از راه‌حل را دارد | batchهای ۲–۴تایی، semantic reservation، continuation inventory و fix اتمیک Broker موجودند | این‌ها بازنویسی نشوند؛ نقص مشخص آن‌ها اصلاح شود. |

Snapshot دیسک هنگام برنامه‌ریزی: حدود `31/38GB` مصرف، `6.7GB` آزاد، ۸۲٪. این عدد شرط ثابت آینده نیست؛ قبل از هر آزمایش دوباره بررسی شود. ایجاد clone، image، cache یا worktree سنگین جزو این پلن نیست.

مقایسهٔ تاریخی هم‌خانواده‌ها هنوز محدود است. اختلاف مدل، اندازهٔ batch، doc-only بودن کار، warm/cold بودن شروع، شکست‌های بدون usage و تغییر scope می‌توانند نتیجه را جابه‌جا کنند. زمان‌های فاز موجود نیز پیش از جمع‌کردن باید از نظر نقطهٔ شروع و هم‌پوشانی بررسی شوند؛ صرف داشتن فیلد timing آن‌ها را اجزای جمع‌پذیر یک کل نمی‌کند.

## ۳. شروط غیرقابل‌حذف

1. Gemini Worker نویسندهٔ تغییرات چرخهٔ unattended؛ Gemini Reviewer مستقل و fresh؛ Codex بررسی معنایی نهایی و تنها عامل Git. نگاشت requirement به assertion و رفتار واقعی باید قابل‌بررسی بماند.
2. تست hermetic، network fence، binding به SHA/HEAD/task/assignment، حفاظت branchها، بررسی CLI واقعی، سقف ورودی/خروجی، کنترل لینک‌ها و قفل اجرای هم‌زمان حفظ می‌شوند.
3. خروجی ناقص، receipt نامعتبر، تغییر مبهم HEAD، task دارای اثر نامعلوم، یا شکست کیفیت هرگز برای «زنده‌ماندن چرخه» موفق تلقی نمی‌شود.
4. semantic reservation قبل از Provider و بدون آزادسازی خودکار با گذشت زمان است. نام تازه، counter reset یا restart راه دورزدن failure budget نیست.
5. مدل و effort فعلی در این آزمایش عوض نمی‌شوند؛ مخلوط‌کردن تغییر مدل با تغییر orchestration امکان سنجش را خراب می‌کند.
6. پیشنهاد آماده یا checkpoint، evidence پذیرش نیست. فایل کامل نهایی در تست و Reviewer دیده می‌شود؛ حذف context حیاتی برای جا‌شدن در سقف ممنوع است.
7. قواعد فعلی batch و reconciliation تا پذیرش تغییر متناظر برقرارند؛ origin/session/persistence/retry/reload/cross-module خودکار وارد batch ساده نمی‌شوند.
8. مصرف کمتر با حذف خطای ثبت‌شده، حذف review، تست‌های ضعیف‌تر، کم‌کردن coverage، انتخاب دائمی کارهای آسان یا عقب‌انداختن dependency سخت پذیرفته نیست.
9. هیچ daemon یا Supervisor مدل‌محور جدید ساخته نشود. helperهای deterministic موجود گسترش یابند؛ telemetry حاوی source، prompt، command، error prose یا دادهٔ عملیاتی نباشد.

## ۴. AE-00 — خط مبنای منصفانه و قابل‌بازتولید

**خروجی:** گزارش bounded و source-free از کار پذیرفته‌شده، هزینهٔ تمام تلاش‌های مربوط و زمان فعال/تقویمی. ابتدا با دادهٔ موجود؛ فقط دادهٔ واقعاً مفقود instrument شود.

گام‌ها:

1. از ledger فعلی، receiptهای معتبر و manifestهای پذیرش، واحد رفتاری را تعریف کن: gap مستند + symbol/handler + scenario + source manifest + selector و receipt پذیرش متناظر. تعداد requirementهای ادعایی، نام task و تعداد commit معیار تکمیل نیستند.
2. دو کلید نگه دار: هویت semantic attempt فعلی برای جلوگیری از تکرار؛ lineage کار برای وصل‌کردن retry/correction و source revisionهای همان نیاز. با تغییر SHA همان رفتار دوباره به‌عنوان پیشرفت جدید شمرده نشود؛ تغییر واقعی رفتار باید delta مستند داشته باشد.
3. همهٔ تلاش‌های Worker/Reviewer/Codex، اصلاحات، شکست‌های task و reconciliation مرتبط را در هزینهٔ آن lineage بیاور. سربار مشترک نیز با قاعدهٔ ثابت به cohort مربوط تخصیص یابد و جدا گزارش شود؛ هزینهٔ کار ردشده از گزارش حذف نشود.
4. Codex uncached، cached و output جدا؛ Gemini input/output/cache فقط در صورت مشاهدهٔ واقعی. `null` و coverage تعداد رکوردهای دارای usage را نمایش بده؛ هزینهٔ پولی/credit از شمار token بدون تعرفه و دادهٔ معتبر استنتاج نشود.
5. سنجه‌های اصلی: token/accepted behavior، active seconds/accepted behavior، accepted behaviors/elapsed day، first-pass rate، correction per accepted behavior، retry/repeated-failure rate و review escape. commit/day صرفاً اطلاعات جانبی است.
6. زمان انتظار quota و توقف برنامه‌ریزی‌شده جدا از زمان فعال باشد، اما از زمان تقویمی پیشرفت حذف نشود. فازها با ساعت monotonic و تعریف روشن ثبت شوند؛ برچسب heuristic فرمان‌ها برای تحلیل است نه اثبات زمان یا کیفیت.
7. cohort قبل/بعد با مدل/effort، خانوادهٔ رفتار، ریسک، اندازهٔ batch و warm/cold مشابه مقایسه شود. بازاجرای Provider برای ساخت baseline ممنوع؛ از شواهد موجود و کار لازم آینده استفاده شود.

**سناریو:** Worker دو بار اصلاح می‌کند و بار سوم یک رفتار پذیرفته می‌شود. گزارش باید هزینهٔ هر سه تلاش و review را بر یک رفتار تقسیم کند، نه فقط تلاش موفق را. رکورد بدون usage با «نامعلوم» و پوشش اندازه‌گیری گزارش شود.

**پذیرش:** ورودی ثابت → گزارش ثابت؛ رکورد تکراری → بدون افزایش پیشرفت؛ تغییر نام task → همان lineage؛ `null` → نه صفر؛ commit اسناد → نه واحد رفتاری ساختگی. گزارش از ledgerهای محدود فعلی خوانده شود، نه اسکن نامحدود streamها.

**فایل‌های هدف:** `scripts/two_site_cycle_metrics.py`، `scripts/two_site_provider_metrics.py` و در صورت نیاز helper گزارش کنار آن‌ها. schema موجود backwards-compatible بماند.

## ۵. AE-01 — آماده‌سازی یک‌مرحله‌ای و context درست از اولین تلاش

**خروجی:** گسترش `two_site_cycle_tools.py` برای آماده‌سازی deterministic؛ نه Coordinator جدید و نه LLM اضافه.

گام‌ها:

1. assignment فعال از مسیر canonical و Git common-dir معتبر resolve شود؛ شناسه و SHA از همان منبع معتبر دریافت شود. اسکن recursive `.local`، حدس نام assignment و query بداهه روی schema حذف شود.
2. accessor نسخه‌دار برای inventory واقعی بساز؛ root object/`entries`/continuation را صریح validate کند و summary محدود از gapهای باقی‌مانده بدهد. schema ناشناخته باید خطای روشن بدهد، نه لیست خالیِ ظاهراً موفق.
3. اولویت انتخاب: بستن dependency و gap لازم برای خروج از Stage؛ سپس batch هم‌خانوادهٔ ۲–۴تایی با شرط‌های موجود. حذف مانع Stage بر تولید تعداد زیاد تست ساده مقدم است.
4. از source واقعی، symbol کامل، decorator، import و dependencyهای لازم از جمله session/model closure را SHA-bound جمع کن. typed classification فقط وقتی معتبر است که از شواهد source تأیید شود؛ تغییر واژه‌های goal برای دورزدن gate فعلی ممنوع است. حالت unknown محافظه‌کارانه باقی بماند.
5. پس از عبور path/scope/link/size safety، نقص‌های مستقل preflight را یک‌جا گزارش کن: assignment، فایل/نماد، bootstrap، selector، requirement، context و بودجه. خطای امنیتی/ساختاری اجازهٔ ادامهٔ خواندن فایل‌های نامعتبر برای «جمع‌آوری همهٔ خطاها» نمی‌دهد.
6. قبل از Worker، بودجهٔ task + source + قالب + evidence و مسیر احتمالی Reviewer را برآورد کن. رشد خروجی کاملاً پیش‌بینی‌پذیر نیست؛ actual materialized Reviewer payload نیز بعد از Worker الزاماً gate شود. batch کم یا context غیرضروری حذف شود؛ نه سقف ۱۶۰KB زیاد و نه context حیاتی حذف.
7. prompt باید برای هر requirement، scenario، selector، assertion مثبت/منفی، side effect ممنوع، fixture و خروجی لازم را روشن کند. عبارت‌هایی مثل «تست کافی بنویس» جای این قرارداد را نمی‌گیرند. Worker باید پوشش واقعی بدهد، نه صرفاً requirements را تکرار کند.
8. Reviewer مستقل روی code و assertion نهایی قضاوت کند؛ mapping پیشنهادی Worker صرفاً راهنماست. برای رفتارهایی مثل «نباید keyboard ساخته شود»، assertion عدم فراخوانی constructor لازم است؛ فقط عدم ارسال پیام کافی نیست. تست باید با شکستن هدفمند همان guard واقعاً fail شود، هرجا اجرای این کنترل ایمن و متناسب است.

**سناریو:** چهار handler ساده و مجاز یک ماژول، با fixture مشترک، در یک task با چهار selector مستقل آماده می‌شوند. اگر کار انتخاب‌شده واقعاً به مسیر session یا persistence مربوط باشد، آماده‌ساز آن را از این batch خارج و مستقل آماده می‌کند؛ adapter و closure لازم همان بار اول در context قرار می‌گیرند. نبود adapter یک خطای preflight روشن است، نه سه دور استنتاج و build توسط Codex. صرف وجود import مربوط به session نیز به‌تنهایی مجوز حذف آن از context یا تغییر طبقه‌بندی بدون بررسی رفتار نیست.

**پذیرش:** replay آفلاین نقص‌های نمونهٔ `1d2b8ce0` همگی پیش از Provider تشخیص داده شوند؛ یک build معتبر؛ خروجی helper محدود؛ batch نامجاز رد؛ assertion ضعیف از quality gate عبور نکند. source خوانده‌شده یا تکالیف بیشتر از scope نمی‌شود.

**فایل‌های هدف:** `two_site_cycle_tools.py`، `two_site_semantic_task_builder.py`، `two_site_task_context.py`، `two_site_assignment_scope.py` و promptهای مربوط فقط به‌اندازهٔ لازم.

## ۶. AE-02 — حفظ آخرین نقطهٔ پذیرفته‌شده بدون اعتماد به وضعیت زنده

**خروجی:** checkpoint کوچک و مستقل از `status.json`، مثلاً `last-accepted.json` در state root فعلی؛ schema نسخه‌دار، حداکثر ۱۶KiB، private و atomic با reader ایمن موجود.

گام‌ها:

1. فقط پس از عبور تمام کنترل‌های فعلیِ نتیجه، commit، clean tree، protected refs و receipts، checkpoint بنویس؛ حداقل accepted HEAD، stage/assignment binding، control/config identity، receipt references و next action محدود.
2. RUNNING/BLOCKED/STOPPED فقط وضعیت زنده را عوض کنند و checkpoint موفق قبلی را پاک نکنند. counterهای reconciliation/semantic attempt نیز با restart صفر نشوند.
3. در startup ابتدا درخواست in-flight و semantic reservation تعیین تکلیف شود. checkpoint مجوز اجرای دوبارهٔ درخواست مبهم نیست. سپس current HEAD، protected refs، assignment و controls را دوباره validate کن؛ فقط در تطابق کامل hint جهت‌یابی قابل‌استفاده است.
4. HEAD جدید، تغییر controls یا assignment متفاوت → cold path امن؛ hint هرگز substitute شواهد جدید نیست. می‌توان checkpoint قدیمی را برای مشاهده نگه داشت، اما از آن برای fast path استفاده نکرد.
5. crash بعد از commit و قبل از checkpoint فقط با receipt کامل و معتبر موجود قابل‌بازیابی است؛ Git HEAD به‌تنهایی acceptance نمی‌سازد. فایل خراب/بزرگ/linked یا schema ناشناخته نیز fast path نمی‌دهد.

**سناریو:** یک کار کامل commit شده؛ اجرای بعدی به quota می‌خورد. بعد از reset، آخرین پیشرفت معتبر بازیابی می‌شود و جهت‌یابی غیرضروری تکرار نمی‌شود، ولی task تازه و reviewها همچنان کامل اجرا می‌شوند.

**پذیرش:** تست restart بعد از RUNNING/BLOCKED/STOPPED؛ interruption دارای reservation؛ crash در دو سوی commit/checkpoint؛ stale HEAD/controls؛ counter reconciliation؛ atomic replacement. هیچ receipt قبلی بازنویسی یا برای HEAD جدید جعل نشود.

**فایل‌های هدف:** `two_site_codex_autopilot.py` و در صورت نیاز reader مشترک موجود. startup performance با warm/cold جدا سنجیده شود.

## ۷. AE-03 — بازیابی هدفمند، بدون restartهای بی‌نتیجه

**خروجی:** طبقه‌بندی ثابت شکست و انتظار پایدار در Supervisor/monitor موجود؛ بدون حلقهٔ جدید و بدون حذف hard stopها.

| رخداد | رفتار مجاز |
| --- | --- |
| quota قطعی و reset با تاریخ/timezone معتبر | ثبت `WAITING_QUOTA` برای همان Provider؛ عدم شروع Coordinator/Provider تا زمان مقرر؛ بیداری یک‌باره تحت قفل موجود و اعتبارسنجی دوباره |
| quota با پیام مبهم مثل «8:09 AM» | حدس UTC ممنوع؛ backoff محدود و مستند، زمان بعدی قابل‌مشاهده، بدون سه اجرای کامل Codex در هر probe |
| خطای transport مشخص و بدون اثر مبهم | retry در budget فعلی و با همان semantic identity؛ نه task alias تازه |
| نتیجهٔ terminal معتبر موجود ولی client منقطع | بازیابی همان پاسخ با تمام bindingها؛ نه اجرای مجدد مدل |
| in-flight/claim یا نتیجهٔ نامعلوم | توقف همان کار برای تعیین تکلیف؛ موفق‌سازی یا آزادسازی خودکار reservation ممنوع |
| خطای task/context/test قابل‌اصلاح | بازخورد محدود و دقیق به Worker، در correction budget موجود؛ تکرار هم‌علت → اصلاح context/دامنه، نه retry نامحدود |
| auth، permission، contract، protected refs یا اثر Git مبهم | hard stop قابل‌مشاهده؛ وجود تأیید کلی مالک، gate فنی یا scope جدید را دور نمی‌زند |
| heartbeat قدیمی ولی process زنده | گزارش و بررسی؛ به‌تنهایی مجوز kill/restart نیست |

گام‌ها:

1. classification از شواهد معتبر موجود با label ثابت باشد؛ صرف exit code برابر ۱ یا وجود واژه در error آزاد برای تصمیم کافی نیست.
2. state انتظار، provider، reset قطعی/نامعلوم، retry budget و زمان بررسی بعدی محدود و restart-safe ذخیره شود؛ monitor و supervisor یک وضعیت را رعایت کنند.
3. `STOP` صریح بر بیداری خودکار مقدم باشد. reset یا restart هم‌زمان دو مالک تولید نکند. failure budgetهای فعلی تغییر نکنند مگر تغییر جداگانه با شواهد و تست.
4. توقف موضعی فقط وقتی اجازهٔ کار مستقل می‌دهد که DAG وابستگی و Stage scope آن را مجاز بداند؛ نمی‌توان با کارهای آسانِ بی‌ربط مانع واقعی Stage را پنهان کرد.
5. گزارش مختصر بگوید چرخه در حال کار است، منتظر سهمیه است، یا hard-block دارد؛ «alive process» مساوی «پیشرفت سالم» نیست.

**سناریو:** سهمیه تمام می‌شود؛ یک بار ثبت معتبر، بدون توکن‌سوزی مکرر انتظار می‌کشد. پس از reset معتبر، تنها یک اجرا ادامه می‌یابد. اگر کار قبلی پاسخ کامل داده باشد همان پاسخ بررسی می‌شود؛ اگر اثرش مبهم باشد از نو ارسال نمی‌شود.

**پذیرش:** fake clock و fake provider برای quota/reset/timezone/clock jump؛ STOP حین انتظار؛ restart monitor؛ رقابت دو wakeup؛ terminal response و ambiguous claim؛ budget exhausted؛ heartbeat کهنهٔ process زنده. این تست‌ها هیچ سهمیهٔ واقعی مصرف نمی‌کنند.

**فایل‌های هدف:** `two_site_codex_autopilot.py`، `two_site_autopilot_monitor.sh`، `two_site_provider_broker.py`؛ recovery فایل اتمیک Broker موجود دوباره بازطراحی نشود.

## ۸. AE-04 — شواهد و reconciliation کوچک ولی کامل

**خروجی:** تکمیل continuation فعلی با index محدود و schema روشن؛ نه ارسال مجدد تمام inventory به مدل و نه حذف evidence لازم.

گام‌ها:

1. base inventory ثابت و continuation فعلی حفظ شوند. رشد و تمام readerهای آن‌ها شناسایی شود؛ فایل‌های قدیمی فقط به‌علت بزرگی بازنویسی نشوند.
2. از receipts پذیرفته‌شده، index مکانیکیِ مرتب و deduplicated بساز: unit/gap/symbol/scenario، source/commit SHA، selector، evidence reference و وضعیت. این index فقط مشتق قابل‌بازسازی است، نه مرجع پذیرش تازه یا نویسندهٔ نتیجه‌گیری معنایی به‌جای Gemini.
3. اگر continuation بزرگ شد، shardهای محدود و index نسخه‌دار با compatibility reader ایجاد کن؛ migration و rollback باید همزمان همهٔ consumerها را پوشش دهند. missing/stale/conflicting evidence وضعیت نامعلوم/نامعتبر بدهد، نه پوشش موفق.
4. reconciliation همچنان حداکثر پس از پنج واحد طبق قرارداد فعلی انجام شود؛ pending count در checkpoint بازیابی شود. batch دلیل عقب‌انداختن reconciliation نیست.
5. فقط شواهد مرتبط به بسته بروند، با مسیر بازیابی بخش لازم؛ Reviewer برای تغییر مورد بررسی full materialized diff و dependencies لازم را ببیند. بودجهٔ واقعی transport مستقل validate شود.

**سناریو:** پنج رفتار جدید پذیرفته شده‌اند. Gemini همان پنج رکورد، مرجع base و context لازم را می‌بیند؛ لازم نیست کل inventory تقریباً ۱۴۰KB را برای یک تغییر کوچک بازتولید کند. خرابی یک reference به‌جای حذف بی‌صدا، مانع پذیرش همان پوشش می‌شود.

**پذیرش:** rebuild همان index؛ کشف duplicate/conflict؛ resolve همهٔ شواهد؛ migration قدیم/جدید؛ ورودی نزدیک سقف؛ crash هنگام نوشتن؛ reconciliation پس از restart؛ عدم افزایش واحد پذیرفته‌شده صرفاً با split فایل.

**فایل‌های هدف:** inventoryهای `section-1/inventory/` و readerهای کشف‌شده، `two_site_cycle_tools.py`، runner transport فقط در صورت نیاز واقعی. محافظت‌های full review و replacements فعلی حفظ شوند.

## ۹. AE-05 — هم‌پوشانی آماده‌سازی، بدون دو Worker هم‌زمان

**پیش‌شرط:** AE-01 و AE-02 پایدار، telemetry نشان‌دهندهٔ زمان آماده‌سازی باقی‌مانده، دیسک و حافظهٔ کافی. اگر آماده‌سازی دیگر گلوگاه نیست اجرا نشود.

گام‌ها:

1. هنگام انتظار Provider جاری، فقط یک draft بعدی با helper deterministic از snapshot خواندنی و immutable آماده شود؛ بدون LLM تازه، رزرو semantic، تغییر Git یا Provider دوم.
2. draft فقط metadata محدود و referenceها را نگه دارد؛ سقف پیشنهادی اولیهٔ serialized metadata برابر ۳۲KiB، صف فقط یک مورد. context اصلی از reader محدود فعلی و هنگام نیاز ساخته شود؛ clone یا cache نامحدود ممنوع.
3. draft شامل base/source/assignment/control fingerprints باشد. پس از پذیرش یا رد کار جاری، همهٔ bindingهای مؤثر بررسی شوند؛ draft stale بازسازی شود. HEAD نهایی فقط بعد از revalidation به task واقعی bind شود؛ receipt یا task قدیمی ویرایش نشود.
4. اگر draft dependency بر نتیجهٔ کار جاری دارد، آماده‌سازی آن speculative تلقی و تا روشن‌شدن نتیجه متوقف شود. رزرو و submission فقط در مسیر serial اصلی انجام شود.
5. روی کمبود منابع یا failure helper، فقط draft کنار گذاشته و مسیر serial عادی ادامه یابد. draft مصرف‌شده یا superseded حذف شود؛ evidence و semantic reservation جزو cache نیستند.
6. اجرای هم‌پوشان تست‌های نهایی مستقل و review فقط اگر profiling فایده نشان داد، در sandbox جدا بررسی شود؛ commit تا پایان هر دو ممنوع. این گزینه نباید به حذف تست pre-review یا final validation منجر شود.

**سناریو:** Gemini روی خانوادهٔ A کار می‌کند؛ helper فقط مشخصات خانوادهٔ مستقل B را آماده می‌کند. اگر A فایل مشترک را تغییر داد، B قبل از submission دوباره ساخته می‌شود. هیچ کد یا نتیجهٔ stale وارد شاخه نمی‌شود.

**پذیرش:** provider calls هم‌زمان همچنان حداکثر یک؛ stale/dependent draft هرگز submit نشود؛ clean tree حفظ شود؛ failure/کمبود فضا چرخهٔ اصلی را قطع نکند؛ زمان ذخیره‌شده از هزینهٔ آماده‌سازی دورریختنی بیشتر باشد.

**فایل‌های هدف:** helper آماده‌سازی در `two_site_cycle_tools.py` و orchestration موجود در `two_site_codex_autopilot.py`/`two_site_proposal_pipeline.py`؛ محل دقیق hook فقط پس از profiling انتخاب شود. Broker مجوز Provider دوم نمی‌گیرد.

## ۱۰. AE-06 — تصمیم مشروط دربارهٔ دو Worker

این مرحله **فعلاً طراحی و تصمیم‌گیری است، نه فعال‌سازی خودکار موازی‌کاری**. حذف قفل موجود راه‌حل نیست: pipeline و Broker فعلی task را به HEAD مشترک و clean tree فعلی bind می‌کنند و تغییر HEAD وسط کار را رد می‌کنند.

بعد از AE-01..05، سهم باقی‌ماندهٔ زمان Provider و نرخ conflict را بسنج. با فرض سادهٔ سهم ۱۵٪ و نصف‌شدن کامل همان بخش، سقف نظری افزایش throughput فقط حدود ۸٪ است، آن هم بدون هزینهٔ اضافه؛ این پیش‌بینی عملکرد واقعی نیست. اگر سود محتمل از overhead/rework کمتر است، یک Worker باقی بماند.

در صورت توجیه، قبل از اجرا باید زیرپلن سازگاری قرارداد کامل شود:

1. حداکثر دو Stage worktree ثبت‌شده و bounded؛ بدون کپی dependency/cache/دادهٔ تولیدی. head/base هر lane immutable و sourceهای قابل‌خواندن/نوشتن معلوم باشند.
2. scheduler بر dependency closure واقعی و read/write conflict تصمیم بگیرد؛ فقط جدا بودن نام فایل کافی نیست. dependency نامعلوم یا shared fixture/model/contract → serial.
3. semantic reservation سراسری و atomic پیش از هر lane؛ دو نام متفاوت نتوانند همان کار را هم‌زمان اجرا کنند. quota/reset و recovery نیز مشترک و بدون دوباره‌ارسالی باشد.
4. Gemini review هر lane مستقل؛ integration و Git همچنان یک مالک. بعد از commit اول، lane دوم روی HEAD تازه دوباره validate/test شود؛ receipt قدیمی برای HEAD تازه پذیرفته نشود. تغییر dependency/diff مستلزم context و review تازه است.
5. Broker/pipeline/task schema باید صریحاً immutable base را پشتیبانی کنند؛ کنترل HEAD فعلی صرفاً خاموش نشود. تطابق نسخه‌های producer/consumer و rollback دوطرفه الزامی است.
6. سناریوهای duplicate task، write/write و read/write conflict، shared import، quota وسط دو lane، crash، stale receipt، STOP و کمبود دیسک اثبات شوند.

**دروازهٔ تصمیم:** برآورد و سپس pilot باید هزینهٔ همهٔ discarded drafts، rebase/retest/reviewها را حساب کند. بدون کیفیت ثابت و بهبود خالص، به serial برگرد. افزایش پیچیدگی صرفاً برای شلوغ‌ترشدن چرخه ممنوع است.

## ۱۱. نگاشت تست و ترتیب اجرایی قدم بعد

نام فایل‌ها در baseline موجودند؛ selector جدید باید مطابق رفتار افزوده‌شده نوشته شود، نه فرض شود از قبل موجود است.

| بسته | تست‌های پایهٔ مرتبط |
| --- | --- |
| AE-00 | `test_two_site_cycle_efficiency.py`، `test_two_site_failed_cycle_metrics.py`، `test_two_site_controller_provider_metrics.py`، `test_two_site_session_gate_metrics.py` |
| AE-01 | `test_two_site_cycle_tools.py`، `test_two_site_semantic_task_builder.py`، `test_two_site_proposal_task_preflight.py`، `test_two_site_task_context.py`، `test_two_site_context_output_runner_gates.py` |
| AE-02 | `test_two_site_codex_autopilot.py`، `test_two_site_cycle_efficiency.py`، `test_two_site_semantic_attempts.py` |
| AE-03 | `test_two_site_autopilot_monitor.py`، `test_two_site_provider_broker.py`، `test_two_site_codex_autopilot.py`، `test_two_site_failed_cycle_metrics.py` |
| AE-04 | `test_p1_00b_characterization_recent_inventory_continuation.py`، `test_two_site_cycle_tools.py`، `test_two_site_cycle_packet_controls.py` و تمام consumerهای inventory |
| AE-05/06 | تست‌های pipeline/Broker/semantic attempts و تست تازه برای isolation، stale snapshot، recovery و صف محدود |

همه در `tests/` هستند. الگوی اجرای محدود و hermetic:

```sh
APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  pytest -q tests/test_two_site_cycle_tools.py tests/test_two_site_task_context.py
```

این فقط نمونهٔ بستهٔ AE-01 است، نه جایگزین تمام ماتریس بالا. قبل از import برنامه، bootstrap و network fence استاندارد برقرار باشد. تست‌ها نباید `.env` زنده، network واقعی یا Provider واقعی بخوانند/فراخوانی کنند.

چک‌لیست شروع قدم بعد:

1. همین سند و حافظهٔ مرتبط را بخوان؛ وضعیت/heartbeat، assignment expiry، HEAD و protected refs، dirty files و دیسک را تازه ثبت کن. آمار امروز را فرض نکن.
2. چرخهٔ فعال را فقط در پایان واحد جاری drain کن. STOPPED واقعی، پایان Provider و tree تمیز را تأیید کن. تغییر worker زنده را هرگز stage/reset نکن.
3. حدود maintenance را از unattended P1-00b جدا نگه دار. assignment فعلی P1 مجوز تغییر scripts/controls/memory نیست؛ برای کار بهینه‌سازی، قرارداد/assignment scoped متناظر لازم است. هیچ widening خاموش یا مجوز production از این سند استنتاج نشود.
4. AE-00 و سپس AE-01 را اول پیاده کن؛ هرکدام diff کوچک، تست‌های مثبت/منفی، Gemini review مستقل و final Codex review. اگر telemetry کافی موجود است، AE-00 را به گزارش و تست صحت داده محدود کن.
5. AE-02 و AE-03 به‌ترتیب، پس از تست recovery آفلاین. هر commit مستقل و امکان rollback بررسی شود؛ main و candidate تغییر نکنند.
6. بعد از هر بسته، چرخهٔ موجود را فعال کن؛ یک کار واقعی لازم را end-to-end مشاهده کن، سپس cohort کوچک مشابه جمع کن. بررسی heartbeat به‌تنهایی کافی نیست: receipt معتبر، commit پذیرفته، no duplicate و پیشرفت Stage بررسی شوند.
7. AE-04 فقط به‌اندازهٔ رشد واقعی/خطای بازبودن transport؛ AE-05 و AE-06 فقط با gate شواهد خودشان. برای پایان پلن، تمام گزینه‌ها را بدون ارزش‌سنجی اجرا نکن.

## ۱۲. پذیرش، پایش، توقف آزمایش و rollback

- ترتیب شواهد: تست آفلاین → یک چرخهٔ واقعی لازم → بررسی مقدماتی ۳ کار مشابه → مقایسهٔ حداقل ۱۰ واحد هم‌خانواده در صورت دسترسی به baseline معتبر → گسترش مشاهده تا ۲۰ واحد برای اطمینان عملی بیشتر. این تعدادها تضمین آماری یا اثبات نبود عیب نیستند؛ baseline نامقایسه‌پذیر نتیجه را «نامشخص» می‌کند، نه موفق.
- هدف اولیهٔ ارزش‌سنجی، نه وعده: حداقل ۱۰٪ کاهش uncached/accepted unit با عدم افزایش معنادار زمان و دیگر هزینه‌ها، یا حداقل ۱۰٪ کاهش زمان فعال با token/accepted unit برابر یا کمتر. cached/output/Gemini و کار دورریخته‌شده نیز جدا دیده شوند؛ انتقال هزینه از یک ستون به دیگری صرفه‌جویی اعلام نشود.
- شرط کیفیت مستقل از درصد صرفه‌جویی است: همهٔ gateها پابرجا؛ regression صفر در تست‌های لازم؛ بدون defect جدید تأییدشده از جنس receipt نادرست، تکرار اثر، پوشش کاذب یا حذف assertion. هر مورد چنین، pilot را فوراً متوقف و مسیر قبلی را بازیابی می‌کند.
- correction کمتر باید همراه کیفیت assertion، independent review و escape rate دیده شود؛ Reviewer آسان‌گیرتر کاهش خطا محسوب نمی‌شود. علت‌های تکراری با label ثابت و denominator روشن گزارش شوند.
- اگر دادهٔ token ناقص است، coverage و بازهٔ عدم‌قطعیت را بنویس؛ عدد صفر یا درصد دقیق صرفه‌جویی نساز. اگر بهبود اثبات نشد، پیچیدگی جدید به‌صورت پیش‌فرض فعال نماند.
- rollback فقط در مرز امن و روی commit مشخصِ همین بسته، با diff/revert بررسی‌شده و تست سازگاری state؛ نه `reset --hard`، پاک‌کردن receipt یا reservation. checkpoint/cache ناسازگار fast path را غیرفعال می‌کند، ولی evidence اصلی باقی می‌ماند.
- runtime telemetry و draftها retention و سقف موجود/مصوب داشته باشند؛ active run، شواهد incident لازم و آخرین rollback قابل‌استفاده حذف نشوند. پاکسازی حجیم یا تغییر policy ذخیره‌سازی بازار خارج این پلن است.
- پس از هر بسته گزارش کوتاه بده: accepted behaviors، مدل/effort، Codex سه ستون، Gemini مشاهده‌شده، زمان فعال/تقویمی، correction/retry، سهم دادهٔ نامعلوم، gateهای کیفیت و تصمیم ادامه/rollback.

## ۱۳. وضعیت تحویل این سند

در این نوبت فقط پلن و مسیر بازیابی آن در حافظه ثبت می‌شود. هیچ یک از AE-00..06 پیاده یا فعال نشده است. توقف کوتاه در مرز چرخه صرفاً برای ثبت امن اسناد است؛ پس از ثبت باید چرخهٔ قبلی از همان launcher معتبر دوباره فعال و وضعیت آن بررسی شود. اجرای واقعی این پلن مربوط به قدم بعدی است.
