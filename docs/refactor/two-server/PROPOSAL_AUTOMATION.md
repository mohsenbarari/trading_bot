# اتوماسیون Codex و Antigravity — قرارداد جاری

به‌روز: ۲۰۲۶-۱۰-۱۷. مسیر توسعهٔ خودکار:
Gemini Worker → Gemini Reviewer مستقل → Codex review/apply/test/commit.
تماس provider در مسیر عادی و `submit-maintenance` فقط از Broker محلیِ محدود
انجام می‌شود؛ Codex یک درخواست فایل‌محور محلی می‌گذارد و به مجوز تودرتوی مدل
approval-review وابسته نیست. تنها استثنا، مسیر دستی و متوقفِ `Root REVIEW_ONLY`
برای بازبینی مستقل کد Codex است که تمام شروط بخش اختصاصی پایین را لازم دارد.
مسیر قدیمی gateway/native همچنان غیرفعال است. وضعیت اجرا را از فرایند زنده،
Git و شواهد همان چرخه بخوانید؛ عبارت RUNNING یا یک گزارش تاریخی اثبات سلامت نیست.

مدل جاری Supervisor: `gpt-6.1-sol` با `xhigh`.
مدل‌های Gemini از `templates/gemini-runtime-contract.json` می‌آیند:
Worker=`gemini-4-argon-medium/medium` و Reviewer=`gemini-4-argon-high/high`.
این انتخاب در ۲۰۲۶-۱۰-۱۷ پس از مشاهدهٔ هر دو مدل در فهرست همین حساب و آزمون
واقعیِ مستقل هر دو نقش انجام شد؛ CLI پین‌شده 1.4.0 است. inventory منابعِ CLI
مجوز ابزار نیست؛ Agent و hookهای محلی باید دقیقاً همان نقش محدود آماده‌شده باشند.
این تفکیک از خروجی‌های غیرقابل‌تحویل
ناشی از reasoning بی‌حد Worker جلوگیری می‌کند، درحالی‌که بازبینی مستقل روی بالاترین
effort باقی می‌ماند. Runner پیش از مصرف provider سازگاری قرارداد و ارجاع‌هایش را
می‌سنجد. حذف review یا سبک‌کردن تست برای صرفه‌جویی مجاز نیست.

## سناریوی یک چرخه، بدون واسطه‌گری مالک

مثلاً یک رفتار خطای ثبت پیام در Stage فعال هنوز تست مشخص ندارد:

1. Codex یک بار Git، حافظهٔ مسیریابی‌شده، Stage و Assignment فعال را بررسی می‌کند.
   next_action چرخهٔ موفق قبلی محل دقیق کار بعدی را نشان می‌دهد؛ این خلاصه فقط وقتی
   HEAD هنوز همان است منتقل می‌شود و جای مجوز، شواهد پذیرش یا بررسی شکاف واقعی را نمی‌گیرد.
2. یک زیرکار مرتبط با شکاف مشخص انتخاب می‌شود. دو یا سه سناریوی نزدیک برای همان
   تابع می‌توانند یک بسته باشند؛ تغییر Stageها یا رفتارهای مستقل یک بسته نیست.
   هدف تکمیل نیازمندی است، نه زیادکردن تعداد تست و commit.
3. Gemini Worker تمام کد، تست و مستندات لازم را به شکل bytes نهایی تولید می‌کند؛
   صرفاً پیشنهاد ایده نمی‌دهد. ابزار، shell، Git و نوشتن مستقیم روی worktree ندارد.
4. pipeline ابتدا bytes نهایی Worker را محلی materialize و اعتبارسنجی می‌کند و
   `test_selectors` همان task را در workspace کاندیدای ایزوله و بدون شبکه اجرا می‌کند.
   فقط PASS شدن این گیت اجازهٔ مصرف Reviewer را می‌دهد؛ شکست، شاهد محدود و دقیق را
   برای اصلاح برمی‌گرداند.
5. نشست تازهٔ Gemini Reviewer همان task، snapshot و proposal کامل را می‌گیرد.
   conversation ID باید متفاوت باشد؛ proposal SHA دقیق، VERIFIED و findings خالی لازم‌اند.
   Reviewer حق تولید تغییر ندارد و مستقیماً با Worker صحبت نمی‌کند.
6. Codex کد واقعی و رفتار را کامل review می‌کند. اگر پذیرفت، فقط bytes همان proposal
   را اعمال، تست‌های لازم را اجرا، diff کامل را بررسی و فایل‌های دقیق را commit می‌کند.
   بررسی مکانیکی شواهد جای بازبینی معنایی یا تست واقعی را نمی‌گیرد.
7. Supervisor مستقل از متن مدل، زنجیرهٔ Worker/Reviewer، یک commit با یک parent،
   برابری path/mode/bytes، ancestry، worktree تمیز و ثبات refs/controlها را می‌سنجد.
   فقط سپس CONTINUE پذیرفته می‌شود. پذیرش واحد به معنی COMPLETE شدن Stage نیست.

مسیر پیش‌فرض توسعهٔ بدون‌نظارت همچنان Gemini Worker → Reviewer مستقل → Codex است.
طبق مجوز دائمی مالک در ۲۰۲۶-۱۰-۱۲، تا پایان ریفکتور، Codex هرجا لازم باشد می‌تواند
کد، آزمون یا مستندات بنویسد و اصلاح کند؛ تأیید جداگانه لازم نیست. ممنوعیت «فقط Gemini
نویسنده باشد» و محدودیت استثناهای یک‌بارمصرف قبلی در این موضوع دیگر حاکم نیستند.
منشأ Codex باید صریح و مقید به bytes و SHA واقعی ثبت شود؛ ساختن Worker یا نشست
Gemini ساختگی ممنوع است. بازبینی مستقل، آزمون واقعی، کنترل دقیق scope/HEAD/bytes
و پذیرش نهایی Codex/Git حفظ می‌شوند. اصلاح صرفاً دستورالعملیِ مسیرهای محافظت‌شده
می‌تواند بازبینی مستقل Codex داشته باشد؛ پذیرش کد با خودتأییدی مجاز نیست.
این مجوز به‌تنهایی Validatorهای فعلی Runner/Supervisor را برای CODEX_AUTHORED تغییر
نمی‌دهد و اثبات سلامت یا راه‌اندازی چرخه نیست. سقف‌ها، STOP و منع replay/reset باقی‌اند؛
این ابزار برای تغییر main/محیط عملیاتی، deploy، DNS یا Web Writer مجوز تازه‌ای نمی‌دهد.

### بازنگری فنیِ بودجه بدون انتظار برای مالک

مالک در ۲۰۲۶-۱۰-۱۷، در پاسخ مشخص به درخواست بازنگری سقف حجم بسته‌ها، تأکید کرد
این کارهای لازم تا پایان ریفکتور به تأیید مجدد او وابسته نباشند. Codex مسئول
رفع گلوگاه است، نه تکرار درخواست مجوز یا تلاش ناموفق برای همان بستهٔ بزرگ.
این مجوز به معنی افزایش خودکار سقف‌ها یا پذیرفتن خروجی قبلاً ردشده نیست.

ابتدا اندازهٔ واقعی UTF-8 بستهٔ کامل Task/Source، proposal، بازبینیِ تمام AFTER
و prompt نهایی همراه schema جداگانه ثبت شود؛ جمع حجم فایل‌ها کافی نیست.
اگر تقسیم منطقی یا حذف تکرارِ بی‌اتلاف کافی نبود، کمترین بازنگریِ محدود و موجه
با سقف قبلی/جدید، مسیرهای دقیق، Scope/HEAD و نسخهٔ قرارداد ثبت شود. پیاده‌سازی
باید همهٔ گیت‌های دریافت، بازخوانی، correction و پذیرش همان مسیر را پوشش دهد؛
آزمون مرز و یک بایت بیشتر، سازگاری مسیر عادی و بازبینی مستقل پیش از پذیرش لازم‌اند.
پیش‌فرض مسیرهای دیگر تغییر نکند؛ override دلخواه مدل/محیط، سقف نامحدود، حذف
Source/سناریو/assertion یا سبک‌کردن review مجاز نیست. سقف‌های عددی فعلی تا پذیرش
اصلاح متناظر همچنان اجرا می‌شوند. این تصمیم رزرو/شکست/هزینه را reset نمی‌کند،
مجوز replay یا دورزدن STOP/permission نیست و سلامت یا فعال‌شدن Supervisor را ثابت نمی‌کند.

## آماده‌سازی استاندارد و کم‌هزینهٔ Task

### کاهش correction بدون ضعیف‌کردن review

مثلاً در تست دکمهٔ «معامله‌ای»، حذف نیم‌فاصله پاسخ متفاوتی تولید می‌کند. Runner از
منبع اصلی، شناسه‌های دقیق و literalهای دارای نویسهٔ نامرئی را به شکل escaped در
`preparation_notes` می‌گذارد؛ این راهنما محدود و مشورتی است و snapshot یا مجوز نیست.
Codex پیش از ارسال باید تعریف وابستگی، شناسهٔ واقعی blocker، fixture معتبر و سناریوی
قبلاً پوشش‌داده‌شده را مشخص کند. Worker مقدار مورد انتظار را از تابع تحت تست تولید
نمی‌کند؛ Reviewer حتی با PASS شدن تست، شکل کامل آرگومان‌ها، ترتیب، await و Unicode را
بررسی می‌کند. راهنما جای اطلاعات ناقص یا review معنایی را نمی‌گیرد.

مثلاً به‌جای سه چرخهٔ جدا برای پیام و callback یک پنل، دو یا سه handler هم‌بسته با
سناریوهای مستقل در یک واحد محدود قرار می‌گیرند. کیفیت، حدود context و review کامل
حفظ می‌شود. پس از حداکثر پنج واحد رفتاریِ پذیرفته‌شده، فهرست شواهد تطبیق می‌شود؛
`units_since_reconciliation` در next_action با restart صفر نمی‌شود. مرجع شروع بدون hint،
`section-1/inventory/recent-characterization.json` است؛ اگر به‌روز است کارش تکرار نمی‌شود.
این فهرست PARTIAL صرفاً محل شواهد است: تست بیشتر معادل بسته‌شدن seed یا Stage نیست.

سنجهٔ جدید `provider_metrics` تلاش‌های هم‌موضوع Gemini، correction موفق، retry ناموفق
و مصرف قابل مشاهده را پیش از پاکسازی شواهد به ledger موجود اضافه می‌کند. ورودی ناقص
به‌صورت UNKNOWN/UNAVAILABLE و مقدار null ثبت می‌شود، نه مصرف صفر. thinking دوباره به
output اضافه نمی‌شود. `provider_reported` زمان گزارش‌شدهٔ provider و
`outside_provider_unattributed` باقی‌ماندهٔ هنوز پروفایل‌نشده است، نه زمان تلف‌شده.
تعداد requirement و selector صرفاً اندازهٔ اعلام‌شدهٔ بسته است؛ برای مقایسهٔ کار/توکن
باید سناریوهای واقعاً پذیرفته‌شده و پیچیدگی مشابه بررسی شوند. صرفه‌جویی هنوز اثبات نشده.

قرارداد Runner دقیقاً شش کلید دارد:
`task_id`, `goal`, `context_files`, `allowed_files`, `requirements`, `test_selectors`.

برای فریز P1-00b، `P1-00B-BATCH-V1` تنها قالب مجاز برای بسته‌کردن کارهای مستقل است:
۲ تا ۴ handler از **یک** ماژول با fixture واقعاً مشترک و hermetic در یک فایل test. هر
requirement به‌صورت مستقل handler، scenario و selector را ثبت می‌کند و selectorهای task باید
دقیقاً با همان‌ها برابر باشند؛ پس دریافت یک commit بسته‌ای هرگز شاهد یکپارچه یا Stage closure
نمی‌سازد. مسیرهای origin/persistence/retry/session/reload/cross-module بسته نمی‌شوند. builder
پیش از تماس با مدل، قالب ناقص یا بزرگ‌شده را fail-closed رد می‌کند.
فهرست‌ها یکتا، غیرخالی و حداکثر ۳۲ عضو هستند. شمارهٔ نیازمندی از ۱ مطابق ترتیب فهرست.
فایل‌های مجازِ موجود کامل فرستاده می‌شوند؛ فایل جدید content/hash اولیهٔ null دارد.

فرمان آماده‌سازی محلی، بدون تماس با مدل:

```bash
python3 scripts/two_site_cycle_tools.py build \
  --spec .local/two-site-refactor/tasks/<spec>.json \
  --output .local/two-site-refactor/tasks/<fresh-task>.json
python3 scripts/two_site_proposal_runner.py check \
  --task .local/two-site-refactor/tasks/<fresh-task>.json
```

spec دقیقاً شامل `task` با همان شش کلید، `snippets`، `gap_reference` و
`scenario_key` است.
نمونهٔ snippets: `[{"path":"مسیر واقعی فایل Python","symbols":["نام تابع","نام ثابت"]}]`.
برای بستهٔ کامل بدون excerpt مقدار `[]` بدهید.
gap_reference مسیر مستند tracked و در صورت نیاز #شناسهٔ شکاف است؛ این برچسب به‌تنهایی
اثبات بازبودن شکاف نیست. Codex باید آن را با Stage و پوشش موجود تطبیق دهد.

### هویت معنایی و جلوگیری از دوباره‌کاری

builder در goal یک marker می‌گذارد که HEAD، gap_reference، excerptها و هویت معنایی
را به هم متصل می‌کند. fingerprint هویت از Stage، gap نرمال‌شده، مجموعهٔ symbolهای
مرجع، scenario_key و manifest مربوط به SHA منابع ساخته می‌شود. این هویت منشأ و موضوع
تلاش را ثابت می‌کند، اما به‌تنهایی اثبات نمی‌کند که شکاف هنوز باز است.

pipeline پس از کنترل Git و task و پیش از ساخت پوشهٔ evidence یا تماس با provider،
marker را اعتبارسنجی و برای هر fingerprint دقیقاً یک attempt محلی و تغییرناپذیر را
به‌صورت اتمیک رزرو می‌کند. تغییر task_id یا run-prefix راه دورزدن آن نیست. تلاش واقعاً
جدید باید دست‌کم یکی از source SHA، Stage، gap، مجموعهٔ symbolها یا scenario را به‌طور
معنادار تغییر دهد؛ در غیر این صورت فقط تصمیم صریح و ممیزی‌شدهٔ نگهداری محلی مجاز است.
این قرارداد TTL، حذف خودکار، lease، هماهنگی remote یا آزادسازی خودکار ندارد.

excerpt فقط برای وابستگی read-only است، نه فایل قابل تغییر یا context تکراری.
تعریف کامل سطح‌بالای تابع/کلاس/ثابت، decorator و importها همراه SHA کامل فایل،
HEAD و بازهٔ خط در goal بسته‌بندی می‌شود. هنگام check، ارسال و بررسی نهایی،
منشأ و متن دوباره محلی سنجیده می‌شوند؛ snapshot قدیمی یا متن دستکاری‌شده رد می‌شود.
وابستگی‌های انتقالی و تعریف enum/signature باید کامل باشند؛ ابزار صرفاً منشأ را ثابت
می‌کند، نه کامل‌بودن زمینه را. اگر انتخاب چند تعریف کافی نیست، فایل کامل ارسال شود.

فایل اصلی باید tracked، داخل root و بدون symlink/hardlink باشد. هرگز فایل ساختگی
در tmp، .local یا .txt را context_files نگذارید. task خروجی فقط در tasks/ محلی،
با نام تازه و mode 0600 ساخته می‌شود و فایل موجود بازنویسی نمی‌شود.
ساخت ناموفق فقط خروجی تازهٔ همان فراخوانی را جمع می‌کند.

کامل بودن زمینهٔ persistence-model صرفاً برای assertionهای صریح مدل ماندگار یک گیت سخت پیش از provider است؛ همچنین بودجه‌های پاسخ test-only (با سقفهای اجرایی max_provider_output_tokens=60000 tokens، max_test_only_transport_bytes=64000 bytes و max_new_test_file_utf8_bytes=24000 bytes) و فایل تازه در کنار rescope مختصر BLOCKED قرارداد ردیابی‌شدهٔ Worker medium / Reviewer high را بدون فرسایش حفظ می‌کنند. وضعیت TASK_READY واقعی پیش‌شرط تماس با provider است. خطای این مرحله محلی اصلاح می‌شود و
نباید مصرف Gemini ایجاد کند. سقف سخت بستهٔ task/sources و proposal استاندارد هرکدام
۱۶۰٬۰۰۰ بایت و سقف prompt کامل ۳۲۰٬۰۰۰ بایت است. اهداف پیشنهادی: task حدود ۸۰٬۰۰۰
بایت یا کمتر و بستهٔ کامل Reviewer زیر حدود ۱۶۰٬۰۰۰ بایت.
این اهداف گیت سخت جدید نیستند. Reviewer علاوه بر زمینه، فایل کامل خروجی را دریافت
می‌کند؛ کم‌بودن Worker input یا استفاده از replacement به‌تنهایی کافی نیست.

گیت persistence اکنون علاوه بر importهای موجود، نام هر کلاس واقعی زیر `models/` را که
در goal/requirements صریحاً آمده با یک index محلی و محدود کشف می‌کند. نبود فایل کامل آن
مدل پیش از تماس با Gemini رد می‌شود. `model_contract_hints` نیز برای مدل‌های حاضر، فیلدهای
بدون default، نوع Date/DateTime و شیوهٔ ذخیرهٔ Enum در دیتابیس را فشرده می‌کند؛ این metadata
جای source یا review را نمی‌گیرد. خروجی Python نامعتبر نیز پیش از اجرای pytest به‌عنوان
output defect محدود شناسایی می‌شود و از همان سقف retry خروجی عبور می‌کند.

برای characterization تستی که مسیر `live/real/authoritative` را با Session adapter سفارشی
اجرا می‌کند، `check` زنجیرهٔ محدود و ایستای فراخوانی‌های دارای `db/session` را از entrypoint
صریح تا عملیات دیتابیس می‌سازد. اعلان test-only و بدون نقل‌قول `session_context:` بدنه‌ای پایان‌یافته با سمی‌کولن یا خط جدید، `exact entrypoint` اختیاری و یک نماد کانونی `api|bot|core|scripts/...py:symbol` یا نماد نقطه‌دار دارد؛ اعلان‌های هم‌ریشه deduplicate می‌شوند و ریشه‌های معتبر متمایز پیش از حل مبهم هستند؛ موارد malformed، خارج از پروژه، فاقد adapter یا closure، حل‌نشده، حذف‌شده و بدون نتیجهٔ کوئری پیش از provider با شکست مواجه می‌شوند؛ aliasهای محلی و محدود `async with AsyncSessionLocal() as session` شناسایی می‌شوند؛ و taskهای بدون اعلان صریح heuristic قدیمی را حفظ می‌کنند. همچنین `session_context` باید تعریف تمام `path:symbol`های این زنجیره
و قرارداد هر `execute/scalar/scalars/get` و API نتیجه مانند `scalar_one` یا `scalars().all()`
را پیش از تماس با Gemini داشته باشد. gate تمام کمبودهای قابل‌نمایش را یک‌جا گزارش می‌کند؛
نبود entrypoint/adapter، وابستگی حل‌نشده، حذف hint یا عبور closure از سقف، همگی fail-closed
هستند. در حالت بزرگ، Coordinator باید سناریو را تقسیم کند یا ابتدا helper کانونی مبتنی بر
AsyncSession واقعی بسازد/بازاستفاده کند. صف ترتیبی نتیجه و fallback عمومی `owner/None` مجاز
نیستند، چون تغییر یک کوئری می‌تواند نتیجهٔ معتبرنما را به مصرف‌کنندهٔ اشتباه تحویل دهد.

شکست pytest با receipt خام و تغییرناپذیر نگهداری می‌شود، اما correction دیگر traceback و
warningهای طولانی را دوباره به مدل نمی‌فرستد. نام تست شکست‌خورده، exception و AST نرمال‌شدهٔ
assertion به‌صورت bounded ارسال و برای تشخیص ریشهٔ تکراری استفاده می‌شوند؛ تفاوت نام متغیر
محلی یا شمارهٔ خط نباید همان فرض غلط را correction تازه جلوه دهد. برای تست تازه‌ای که به
session/table/patch سفارشی سنگین نیاز دارد، Coordinator آن را حداکثر به چهار requirement و
یک outcome محدود می‌کند یا ابتدا helper موجود را بازاستفاده می‌کند؛ سقف‌های کیفیت ثابت‌اند.

برای فایل‌های تازهٔ `tests/test_p1_00*_characterization_*.py` که application import دارند،
گیت قطعیِ پیش از sandbox-test، مقدار `APP_ENV_FILE`، ترتیب bootstrap و استفاده از
`install_p1_00_network_fence()` را بررسی می‌کند. خطا با feedback محدودِ `test-gate` به Worker
برمی‌گردد و تا رفع آن نه pytest، نه Reviewer و نه بررسی نهایی Codex اجرا نمی‌شوند. این گیت
فقط قرارداد پایدار hermetic-test را بررسی می‌کند و جای review معنایی یا تست واقعی را نمی‌گیرد.

اگر محتوای نهایی UTF-8 یک فایل موجود حداقل ۸۱۹۲ بایت است، transport حتماً باید از
replacement دقیق استفاده کند؛ برای فایل موجود کوچک‌تر نیز replacement مجاز است.
فایل تازه همیشه با content کامل منتقل می‌شود.
قالب چهارکلیدی raw change: path، original_sha256، content و replacements.
حالت کامل content کامل + replacements خالی؛ حالت replacement مقدار content=null و
۱ تا ۳۲ {old,new} یکتا و غیرهم‌پوشان، مجموع حداکثر ۶۵۵۳۶ بایت UTF-8.
Runner پیش از Reviewer bytes کامل را materialize می‌کند؛ Reviewer و Codex همواره
محتوای نهایی کامل را می‌بینند، پس فشرده‌سازی transport عمق review را کم نمی‌کند.
مدل باید import/signature/enum، سناریوی منفی و پوشش نیازمندی‌ها را قبل از ارسال بررسی کند.
نبود تعریف لازم باید با path/symbol دقیق گزارش شود، نه ادعای انجام کار یا خروجی ناقص.

## اجرا، review مکانیکی و اصلاح

مسیر عادی دقیقاً یک درخواست broker برای هر واحد:

```bash
python3 scripts/two_site_provider_broker.py submit \
  --task .local/two-site-refactor/tasks/<fresh-task>.json \
  --run-prefix <unique> --timeout 900
```

این فرمان محلی بدون escalation اجرا می‌شود. Broker از پیش بیرون sandbox در نشست مستقل
اجرا شده و تنها مجاز به ساخت فرمان ثابت pipeline با همان task، HEAD، Assignment و
controller_run_id است؛ shell، executable، آرگومان CLI، عملیات Git یا مسیر دلخواه
نمی‌پذیرد. درخواست و پاسخ اتمیک، محدود و تک‌مصرف‌اند. Broker هم‌زمان فقط یک درخواست
را اجرا می‌کند و پس از restart درخواست claim‌شدهٔ مبهم را تکرار نمی‌کند، بلکه آن را
با خطای صریح می‌بندد؛ پاسخ orphan حداکثر پس از ۲۴ ساعت با کار محدود پاک می‌شود.
نبود Broker مجوز تماس مستقیم با provider نیست.

برای نگهداریِ موردیِ تأییدشده، مسیر جداگانهٔ `submit-maintenance` وجود دارد؛
این مسیر چرخهٔ Supervisor را جعل نمی‌کند. نخست باید چرخهٔ جاری در مرز امن drain
شده، وضعیت Supervisor واقعاً `STOPPED`، قفلش آزاد، و assignment موقتِ محدود و
معتبر فعال باشد. نشانگر `STOP` فقط بعد از تأیید این وضعیت برای همان عملیات
صریحاً برداشته می‌شود؛ Broker در نشست مستقل اجرا می‌شود، نه با `tmux.sh start`
که Supervisor را هم راه می‌اندازد. سپس:

```bash
python3 scripts/two_site_provider_broker.py submit-maintenance \
  --task .local/two-site-refactor/tasks/<maintenance-task>.json \
  --run-prefix <unique> --timeout 900
```

شناسهٔ `maint-...` را خود Broker ایجاد و در یک رکورد تغییرناپذیر به request،
task SHA، HEAD و assignment وصل می‌کند. Broker پیش و هنگام اجرای pipeline
`STOPPED` و قفل انحصاری Supervisor را کنترل می‌کند؛ درخواست بی‌رکورد یا دارای
binding ناسازگار رد می‌شود. Runner این شناسه را فقط از
`TWO_SITE_MAINTENANCE_RUN_ID` می‌گیرد؛ `cycle-...` همچنان فقط متعلق به Supervisor
است. رکورد نگهداری فقط metadata محدود دارد و پس از ۱۰ روز با پاکسازی محدود
حذف می‌شود. پایان کار: بازبینی معنایی Codex، آزمون، commit دقیق، بازگرداندن
assignment اصلی و راه‌اندازی عادی؛ این مسیر مجوز دورزدن review یا تغییر
production نیست.

### Root REVIEW_ONLY — استثنای محدود برای بازبینی کد Codex

سناریو: Codex طبق مجوز دائمی مالک یک بستهٔ کامل نوشته، ولی Worker واقعی وجود
ندارد. برای گرفتن بازبینی مستقل نباید Worker یا conversation ساختگی ساخت.
فقط در حالت دستیِ Root و Supervisor متوقف، یک Gemini verifier تازه می‌تواند
proposal دقیق و هنوز اعمال‌نشدهٔ `CODEX_AUTHORED` را با سازوکار Runner پین‌شده
بازبینی کند. این استثنا مسیر unattended، نویسندگی/اصلاح، جایگزین Broker خراب،
یا راه دورزدن رد نهایی، سهمیه، مجوز محیط یا failure budget نیست. مسیرهای عادی
و `submit-maintenance` همچنان فقط Broker هستند؛ عملیات تازه‌ای به Broker اضافه نشده است.

هر فراخوانی این مسیر تمام شروط زیر را لازم دارد:

1. Assignment تازه، معتبر و صادرشده، canonical `TASK_READY` واقعی، integration root،
   HEAD، scope digest و مسیرهای دقیق باید یکسان باشند. هر سه متغیر
   `TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED=1`، `TWO_SITE_ASSIGNMENT_ID` و
   `TWO_SITE_ASSIGNMENT_SHA256` دقیقاً از همان Scope گرفته شوند. یک intent محلی،
   تازه، تغییرناپذیر و تک‌مصرف، SHA خام Task، manifest کامل Source اولیه، SHA خام
   proposal کامل، manifest کامل AFTER و SHA خام رسید native واقعی کاندیدا را
   متصل کند. رسید باید `PASSED`، selectorهای دقیق Task و `unsupported_specs=[]`
   داشته باشد؛ RAM/diagnostic به‌جای این گیت پذیرفته نیست. هویت معنایی و رزروهای
   موجود رعایت شوند؛ intent یا نام تازه مجوز retry معادل یا آزادسازی رزرو نیست.
2. Supervisor واقعاً `STOPPED` و بدون active assignment باشد. هیچ‌کدام از STOPهای
   root/autopilot نباید وجود داشته باشد؛ خطای stat نیز مانع است و STOP خودکار پاک
   نمی‌شود. Root قفل Supervisor، hold موجودِ Broker با IDLE تازه/صف‌های خالی و قفل
   client، و قفل pipeline را تا پایان نگه دارد. خود Runner قفل nonblocking runner
   را در تمام probe، profile، streaming و cleanup نگه می‌دارد؛ آن را دوبار نگیرید
   یا دور نزنید. هویت‌ها، STOP، Scope/HEAD/refs/controls و همهٔ bytes پیش از dispatch
   و در finally، از جمله مسیر خطا/BaseException، دوباره کنترل شوند.
3. Root پیش از اجرا، کد کامل adapter محلی و وابستگی‌های استفاده‌شده را مستقل review
   و byte-pin کند. اتصال خصوصی و محدودِ prior-loader برای همان Task/Source/HEAD/proposal
   باید صریحاً بدون lineageِ Worker باشد؛ helper تاریخی مجوز یا API آمادهٔ عمومی نیست.
   هر role-template یا projection بی‌اتلافِ prompt باید صریح، پین‌شده، مستقل بازبینی‌شده
   و بازسازندهٔ محتوای کامل review باشد. تغییر shared SDK globals یا جایگزینی validation،
   locks، parsers، profile، model، caps و permission checks ممنوع است. executable، shell،
   CLI args، callback یا namespace/permission override عمومیِ دریافتی از درخواست مجاز نیست.
4. تمام ورودی‌های مدل با reader موجودِ root-confined و bounded، regular/single-link،
   بدون symlink و با هویت ثابت خوانده شوند. سقف Source/Task/proposal و خروجی حفظ شود؛
   حد پیش‌فرض بستهٔ کامل Reviewer همچنان ۱۸۷٬۰۰۰ بایت و سقف SDK برای prompt کامل
   ۳۲۰٬۰۰۰ بایت است. تنها پروفایل نسخه‌دار `ROOT_REVIEW_ONLY_AE04_FULL_V1` سقف
   ۲۲۴٬۰۰۰ بایت UTF-8، شامل schema و رسید native واقعی، دارد: فقط برای اصلاح کامل
   انتخاب‌گر AE04 با هر هفت requirement، هشت selector اصلی و دقیقاً سه مسیر
   `scripts/two_site_cycle_tools.py`، `tests/test_two_site_canonical_selector_evidence.py`
   و `tests/test_two_site_canonical_selector_support.py`. اندازهٔ واقعی بستهٔ قبلی
   حتی بدون native برابر ۲۰۰٬۹۳۲ بایت بود؛ این افزایش محدود مانع حذف زمینه می‌شود.
   پروفایل فقط پس از Scope تازه، TASK_READY واقعی، adapter محدود و مستقل‌بازبینی‌شده
   با همین نسخهٔ ثابت، آزمون مرز ۲۲۴٬۰۰۰/۲۲۴٬۰۰۱ و UTF-8 چندبایتی، native کامل PASS
   و intent تازه قابل اجراست؛ تنظیم دلخواه مدل/محیط یا تعمیم آن به واحد دیگر ممنوع است.
   تمام Source اولیه و AFTER بازساختنی از diff کانونی بدون تغییر، زمینه، requirement
   و آزمون‌ها حفظ شوند. TaskSources/proposal همچنان هرکدام حداکثر ۱۶۰٬۰۰۰ بایت‌اند؛
   مسیرهای دیگر همان حد پیش‌فرض را دارند. این تصمیم پذیرش کد یا اجازهٔ retry نیست.
   حذف AFTER، review جزئی، Source مبهم/encoded، cap waiver یا رسید مصنوعی ممنوع است.
   پیش از dispatch، CLI version/SHA و fresh selected/resolved profile سنجیده شوند؛
   پس از اجرا، actual executor و مدل/session/tool-free واقعی و CLI identity سنجیده شوند.
   پیش از وقوع invocation ادعای اجرای executor نشود. سقف ترکیبی خروجی حین stream،
   timeout، process-group termination و cleanup نسخهٔ disposable حفظ شوند. فقط خود
   CLI به احراز هویت خودش دسترسی دارد؛ secret/credential/user/production data خارج دامنه‌اند.
5. verifier run/session تازه و یکتا باشد. `cycle-` و Broker `maint-` جعل نشوند؛ هیچ
   Worker status/conversation یا prior-worker tuple ساخته نشود. منشأ `CODEX_AUTHORED`
   و نشست واقعی Reviewer ثبت شوند. SUCCESS، مدل/session/tool-free lifecycle، schema
   کانونی verifier، VERIFIED با findings خالی و همهٔ bindingهای proposal/Task/native/
   Source/prompt لازم‌اند. تمام شکست‌ها و هزینه‌ها حفظ شوند. dispatch مبهم، crash،
   timeout، خروجی بدفرمت، auth/quota/security/runtime drift یا STOP دیرهنگام، مجوز
   replay/resume/retry یا پذیرش جزئی نیست؛ شواهد همان تلاش تعیین تکلیف شوند. پرداخت
   تازه فقط با تصمیم صریح، محدود و تک‌مصرف تازه در همان قواعد no-equivalent-retry
   مجاز است؛ تأیید روتین دوباره از مالک لازم نیست و مجوز محیط مستقل باقی می‌ماند.
6. تأیید Reviewer صرفاً شاهد است. Root همچنان بازبینی معنایی کامل، native کامل،
   کنترل دقیق Source/mode/diff و پذیرش سریال Git با یک parent را انجام می‌دهد؛ فقط
   bytes دقیقِ بازبینی‌شده اعمال/commit شوند. این مسیر BINDINGS_VALID/CONTINUE/checkpoint
   ساختگی یا پذیرش خودکار Supervisor تولید نمی‌کند. Supervisor متوقف می‌ماند؛ restart/
   adoption/activation، تغییر main/protected refs، production/deploy/DNS/Web Writer، حذف
   evidence یا reset رزرو/checkpoint و اعتبار کسب‌وکاریِ Stage/goal از آن مجاز نمی‌شود.

Scope، adapter، رسید native و intent تازه برای هر واحد لازم‌اند. دستورالعمل فوق
به‌تنهایی مجوز یک فراخوانی مشخص نیست؛ Scope/receiptهای تاریخی 685/689 نیز استثنای
قابل‌بازاستفاده نمی‌سازند. همهٔ شروط باید برای همان bytes و همان تلاش برقرار باشند.

مالک در ۲۰۲۶-۰۹-۰۹
ارسال source محدود و غیرمحرمانه، tests، docs، task، proposal، review/findings و
test evidence به Google Antigravity/Gemini را دائماً و بدون تأیید جداگانه مجاز کرده است.
مجوز شامل دسترسی خود CLI به وضعیت احراز هویت خودش و listener موقت localhost است.
Codex نباید credential را بخواند، چاپ کند، کپی کند یا hash کند.
secret، اطلاعات هویتی/اعتباری، دادهٔ کاربر، دادهٔ عملیاتی/تولید، deploy، DNS،
Writer، fetch/push و تغییر سرویس نامرتبط خارج دامنه‌اند. Broker این مجوز را گسترش نمی‌دهد.

در مسیر عادی، خروجی READY_FOR_CODEX مسیر دقیق Worker، Reviewer و proposal SHA را می‌دهد.
بدون اسکن شواهد قدیمی، قبل از اعمال:

```bash
python3 scripts/two_site_cycle_tools.py review --task <task> \
  --worker <worker-id> --reviewer <reviewer-id> --sha <proposal-sha>
```

BINDINGS_VALID یعنی اتصال دقیق task/source/head/hash و دو نشست بررسی شده، نه اینکه
کد درست است. Codex تمام تغییرات و شواهد را review می‌کند. پس از اعمال bytes دقیق،
همان فرمان با `--applied` و پیش از staging اجرا می‌شود؛ BYTES_VALID اختلاف مسیر،
متن، mode یا فایل نامرتبط را رد می‌کند، ولی تستی اجرا نمی‌کند.
در correction، helper قرارداد parsed feedback و proposal قبلی را می‌سنجد؛ hash متن
خام feedback را از سریال‌سازی مجدد حدس نمی‌زند. binding خام در Runner/Pipeline حفظ می‌شود.
هر round همان proposal ردشده را همراه findings و test evidence فشرده و مقید به SHA
به Worker تازه می‌دهد و سپس Reviewer تازه لازم است؛ context تکراری مخزن دوباره ارسال
نمی‌شود و بخش‌های پذیرفته‌شدهٔ نامرتبط باید ثابت بمانند.
اگر path در snapshot اصلی وجود نداشته باشد، overlay شدن proposal قبلی آن را به فایل موجود
برای انتخاب transport تبدیل نمی‌کند: Runner فهرست `full_content_required_paths` را از
snapshot اصلی می‌سازد و correction برای آن path فقط محتوای کامل، غیرخالی و واقعاً متفاوت
با SHA همان candidate و `replacements=[]` می‌پذیرد. schema، prompt و کنترل پس از پاسخ
همگی به همان فهرست متصل‌اند.

رد Reviewer یا تست داخل pipeline با چهار کلید دقیق برمی‌گردد:
`reviewer` (gemini یا codex)، `reviewed_proposal_sha256`،
`findings` و `test_evidence`.
finding باید محل، ورودی محرک، رفتار واقعی/مورد انتظار و اصلاح لازم را روشن کند.
Worker تازه proposal ردشده و feedback را می‌گیرد؛ سپس Reviewer تازه الزامی است.
بخش‌های صحیح نباید بی‌دلیل بازنویسی شوند. شکست مشابه را با عبارت جدید دوباره نفرستید.
tool-attempt، function-call بدفرمت یا پاسخ `SUCCESS` با transport خالی/نامعتبر/بدون تغییر
فقط از مسیر retry محدود سه‌نشستی با شناسه و session تازه عبور می‌کند؛ خطای ثبت‌شده کوتاه
است و source را تکرار نمی‌کند. این retry جای correction معنایی نیست. خطای سهمیه/شبکه/auth،
truncation، scope/security/Git/runtime و پوشش معنایی requirements وارد این مسیر نمی‌شوند.
correction معنایی همچنان حداکثر دو بار است و رد معادل باید به rescope واقعی منجر شود.
اگر Codex در گیت نهایی ایراد معنایی پیدا کند، همان واحد با Git تمیز و یافتهٔ دقیق
BLOCKED/RESCOPE می‌شود؛ Codex runner مستقیم provider اجرا نمی‌کند و واحد بعدی باید
کوچک‌تر و با context اصلاح‌شده ساخته شود. استثنای دستی `Root REVIEW_ONLY` فقط با
تمام شروط بخش اختصاصی خودش است و راه تبدیل همان retry ردشده به فراخوانی مستقیم نیست.

حداکثر دو اصلاح متمایز در یک واحد؛ تمام‌شدن بودجه با Git تمیز و بدون commit یعنی
BLOCKED/RESCOPE INTERNAL_REJECTION و یک واحد کوچک‌تر/کامل‌تر جدید.
خروجی ناقص، MODEL_OUTPUT_TRUNCATED یا finish نامعتبر مجوز پذیرش bytes ناقص نیست.
حداکثر سه RESCOPE پیاپی در Supervisor وجود دارد؛ پس از دومین RESCOPE، واحد قبلی در context
صریحاً deferred می‌شود و چرخهٔ بعد باید یک رفتار مستقل و dependency-ready را برگزیند.
اگر چنین واحدی وجود نداشته باشد HALT لازم است و تلاش سومِ ناموفق همچنان BLOCKED می‌شود.
تغییر مبهم یا gate بیرونی retry نمی‌شود.
خطای provider/auth/مجوز، dirty tree یا drift کنترل‌ها BLOCKED/HALT EXTERNAL_GATE است.
شکست تست ابتدا یک بار با baseline تمیز مقایسه می‌شود؛ خطای قدیمی نامرتبط جدا می‌ماند.

پس از review و تست، staging فقط نام تک‌تک فایل‌های proposal و یک commit غیرتعاملی دارد.
metadata مشترک Git بیرون writable worktree است؛ اولین add/commit با escalation محدود،
بدون یک تلاش ناموفق قابل‌پیش‌بینی. add کلی، amend، reset/checkout، merge/rebase،
تغییر branch/tag، bypass hook/signing، fetch/push و عملیات محصول ممنوع‌اند.

## پایداری و کنترل منابع

Antigravity CLI نسخهٔ 1.4.0 و SHA از runtime contract می‌آید. منبع کانونی در
.local/tools/ زیر checkout مشترک، متعلق به UID جاری، single-link، read-only و دقیقاً
همان SHA است. هر اجرا فقط روی کپی disposable در پوشهٔ موقت 0700 انجام می‌شود.
AGY_CLI_DISABLE_AUTO_UPDATE=true؛ نسخه و SHA پیش و پس از اجرا بررسی می‌شوند.
کپی موقت قبل از آزادشدن lock در موفقیت/خطا/timeout جمع می‌شود؛ adapter 1.1.26 قدیمی است.

Codex CLI نسخهٔ 0.159.2 با SHA ثبت‌شده در Supervisor، از مسیر مستقل این integration:
`.local/tools/codex-0.159.2-1748767b230ebfc3/codex`.
این مسیر به پوشهٔ نسخه‌دار افزونه وابسته نیست؛ fallback به PATH یا نسخهٔ تأییدنشده ندارد.
نسخه/SHA/login پیش از اجرا و SHA پس از اجرا کنترل می‌شوند.
این تغییر خطر حذف مسیر توسط update افزونه را رفع می‌کند، نه همهٔ خطاهای احتمالی میزبان.
بستهٔ قبلی 0.153.4 برای rollback حفظ شده و bundle جدید در پوشهٔ نسخه‌دار جداگانه قرار دارد.
بستهٔ مستقل باید کامل باشد: codex-code-mode-host، codex-package.json، rg و منابع
codex-path/codex-resources از همان نسخه، با SHAهای CODEX_RUNTIME_FILES در Supervisor.
این فایل‌ها read-only و single-link هستند؛ پیش و پس از readiness و پس از چرخه بررسی
می‌شوند. وجود باینری اصلی و موفقیت login به‌تنهایی اثبات قابلیت اجرای ابزار نیست.

ورودی فایل‌محور با descriptor و limit+1 محدود خوانده می‌شود؛ root confinement،
regular بودن، رد symlink/hardlink و ثبات inode/اندازه/timestamp لازم‌اند.
stdout+stderr Gemini حین stream سقف مشترک ۲ مگابایت دارند؛ خروجی Codex سقف ۱۶ مگابایت.
عبور از سقف یا timeout process group همان اجرا را متوقف می‌کند.
Runner و Supervisor قفل انحصاری nonblocking مستقل دارند؛ اجرای هم‌زمان دوم مجاز نیست.
نشست Codex ephemeral است؛ nested Codex reviewer اجرا نمی‌شود.
Broker نیز قفل server و client مستقل دارد، صف را حداکثر تا ۳۲ ورودی بررسی می‌کند،
هر سند را با سقف byte و رد symlink/hardlink می‌خواند، خروجی pipeline را هنگام stream
در ۲ مگابایت متوقف می‌کند و فقط پاسخ همان request/head را به client تحویل می‌دهد.
سلامت بین PID namespaceها از heartbeat فایل‌محور تازه و مقید به request سنجیده می‌شود،
نه مشاهدهٔ PID میزبان از داخل sandbox.

Supervisor پس از اعتبارسنجی runtime، Git، protected refs، control blobs، registry و Assignment
فلگ `startup_validation=true` را در context همان چرخه می‌گذارد. Codex این کنترل‌های مکانیکی
آغاز چرخه را دوباره اجرا نمی‌کند و فقط پس از mutation/drift یا در بازرسی نهایی probe لازم را
تکرار می‌کند؛ review معنایی، تست و کنترل نهایی دقیق bytes/refs حذف نشده‌اند.

```bash
scripts/two_site_autopilot_tmux.sh start
scripts/two_site_autopilot_tmux.sh status
```

راه‌انداز دو نشست دقیق `two-site-provider-broker` و `two-site-autopilot` را ایجاد می‌کند
و Supervisor را فقط پس از سلامت Broker بالا می‌آورد. tmux گفتگوی باز نمی‌خواهد.
STOP آغاز چرخهٔ بعدی را در مرز چرخه منع می‌کند؛ Broker تا ثبت `STOPPED` توسط Supervisor
در دسترس می‌ماند تا درخواست محدودِ چرخهٔ درحال‌بستن نیمه‌کاره نشود. stop-now هر دو نشست را متوقف می‌کند.
راه‌انداز خود تضمین reboot یا دسترسی همیشگی provider نیست. مانیتور نصب‌شدهٔ محلی
هر ۱۵ دقیقه جدا وضعیت فرایند/شواهد و دقیقاً یک Broker را می‌سنجد، Broker گم‌شده را
در وضعیت امن بازمی‌گرداند و STOP عمدی را نباید دور بزند. blocker تاریخیِ ظرفیت
approval-review پس از این مهاجرت قابل بازیابی است؛ رد واقعی authority همچنان fail-closed است.
Supervisor در اجرای زنده heartbeat و phase بدون source/prompt منتشر می‌کند. مانیتور phase مشاهده‌شده،
عمر phase و تازگی heartbeat را گزارش می‌کند؛ heartbeat کهنه فقط `STALE` و گزارش بررسی می‌سازد و هرگز
به‌تنهایی مجوز kill/restart یک Supervisor زنده نیست.
حد اجرای Supervisor: ۲۰۰ چرخه، timeout هر چرخه سه ساعت، retry خطای گذرای بی‌تغییر حداکثر دو بار.

## مصرف توکن، نگهداری و مرز ادعا

پیش از prune هر run، حتی خطای اجرا، metrics کوچک ذخیره می‌شود:
مدل/effort، prompt SHA، HEADها، outcome، زمان، شمارش command، ورودی، cached،
uncached و output. reasoning اگر واقعاً موجود باشد زیرمجموعهٔ output است و دوباره
جمع نمی‌شود. دادهٔ ناقص/نامعتبر UNKNOWN/null است، نه مصرف صفر.
شناسه/هش Task و gap_reference فقط وقتی از snapshot همان چرخه در دسترس باشند ثبت می‌شوند.
هیچ prompt، source، command body یا متن خصوصی در metrics نگهداری نمی‌شود.

شناسهٔ controller_run_id تمام تلاش‌های آتی از جمله شکست‌ها را همبسته می‌کند؛ تلاش‌های ناموفق با مصرف معتبر provider نیز در تله‌متری ثبت می‌شوند، اما چرخه‌های تاریخی پیش از tag عطف‌به‌ماسبق ادعا نمی‌شوند. این تله‌متریِ مشاهده‌ای، محدود و غیرمرجع هرگز متن prompt، source یا error را ذخیره نمی‌کند و شواهد ناقص به‌جای صفر با عنوان PARTIAL/UNAVAILABLE ثبت می‌شوند. تا زمان در دسترس بودن نمونه‌های پس از تغییرِ قابل‌مقایسه، نباید ادعای صرفه‌جویی سنجیده‌شده داشت. فایل .local/two-site-refactor/autopilot/metrics.json حداکثر ۲۰۰۰ رکورد و ۲ مگابایت دارد؛
بعد از prune خروجی‌های خام باقی می‌ماند. پرشدن حلقه قدیمی‌ترین رکورد را کنار می‌گذارد.
خطای telemetry تشخیص‌پذیر است ولی commit معتبر را متوقف نمی‌کند.
۲۰ run عادی آخر نگهداری می‌شود؛ BLOCKED برای بررسی باقی می‌ماند.
proposalها retain_until هفت‌روزه دارند؛ این تاریخ به‌تنهایی ادعای حذف خودکار نیست.
شواهد فعال/ایراد باز بدون تعیین تکلیف پاک نمی‌شوند؛ backup دائمی نسازید.

بستهٔ سوم به هر `provider_metrics` یک `rework_taxonomy` چهارکلیدی و کاملاً enum-محور
می‌افزاید: کامل‌بودن مشاهده، نتیجهٔ آغاز، مسیر rework و نتیجهٔ نهایی. برچسب‌ها فقط از
role، state پایانی، پیوند SHA-شکلِ correction و topology شناسهٔ run ساخته می‌شوند؛ برای
نمونه `FIRST_PASS`، `REVIEWER_CHANGES_REQUIRED`، `ATTEMPT_BLOCKED`، `MIXED` و
`UNCLASSIFIED_REWORK`. بنابراین این طبقه‌بندی علتِ معناییِ یک finding یا شکست را حدس
نمی‌زند و هیچ finding، error، prompt، command، source یا متن task را وارد ledger نمی‌کند.
شاهد ناقص نیز فقط `UNKNOWN` است. این بسته هنوز context اضافه نمی‌کند: فقط پس از چند
مشاهدهٔ کامل و هم‌خانواده با برچسب ساختاری یکسان، طراحی یک context-profile بسیار محدود
و reviewشدنی مجاز است؛ افزایش عمومی prompt یا تعویض review هرگز راه‌حل تلقی نمی‌شود.

مقایسه باید بر پایهٔ نیازمندی واقعاً پذیرفته‌شده و با احتساب تلاش‌های ناموفق باشد،
نه فقط تعداد commit یا ساعت. ورودی cached و uncached جدا گزارش شوند.
baseline تاریخی Sol: ۱۴ چرخهٔ موفق؛ میانگین ورودی 1,521,948، uncached برابر84,471،
output برابر13,384، زمان652ثانیه. ممیزی ۹ سپتامبر پیش از این بهینه‌سازی:
۱۹ چرخهٔ موفق Astra؛ میانگین ورودی1,882,351، uncached برابر92,911،
output برابر9,558 و زمان470.4ثانیه. نوع کارها یکسان نیست؛ این اعداد آزمایش کنترل‌شده،
هزینهٔ قطعی حساب یا درصد صرفه‌جویی تغییرات امروز نیستند. نمونهٔ تازه برای نتیجه‌گیری لازم است.

شاهد ۵ سپتامبر فقط تاریخچهٔ انتقال از Worker→Codex بود:
[گزارش تاریخی](reviews/AUTOMATION_PROPOSAL_LIVE_20260905.md).
شاهد جاری هر اجرا همان SHAها، test evidence و transition اعتبارسنجی‌شده است.
تست آفلاین قرارداد ابزار را می‌سنجد؛ سلامت live، Stage COMPLETE، deploy و آمادگی
عملیاتی هرکدام شواهد مخصوص خود را می‌خواهند. هیچ‌کدام از متن این سند نتیجه نمی‌شوند.
