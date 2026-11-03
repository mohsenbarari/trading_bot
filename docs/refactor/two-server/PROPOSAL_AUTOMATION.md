# اتوماسیون Codex و Antigravity — قرارداد جاری

> Runtime migration 2026-11-03: active role models and reasoning are read from `docs/refactor/two-server/templates/automation-models.json`. Qoder owns coordination and final acceptance; Gemini owns proposal generation and independent first review. `gemini-runtime-contract.json` retains historical provenance and CLI/completion pins; its historical role-model fields do not select new runs. Source/proposal binding, native tests, independent sessions, serial Git and explicit STOP remain required.


به‌روز: ۲۰۲۶-۱۰-۱۹. مسیر پیش‌فرض توسعهٔ خودکار:
Gemini Worker → Gemini Reviewer مستقل → Codex review/apply/test/commit.
تماس provider در مسیر عادی و `submit-maintenance` فقط از Broker محلیِ محدود
انجام می‌شود؛ Codex یک درخواست فایل‌محور محلی می‌گذارد و به مجوز تودرتوی مدل
approval-review وابسته نیست. دو مسیر دستیِ محدود پایین مستقل از مسیر عادی‌اند:
`Root REVIEW_ONLY` با Gemini verifier، و جایگزین موقتِ دو عامل مستقل Codex هنگام
اختلال CLI. هرکدام تمام شروط بخش اختصاصی خود را لازم دارند؛ مسیر دوم هیچ تماس
provider یا تغییر Validatorهای unattended را مجاز نمی‌کند.
مسیر قدیمی gateway/native همچنان غیرفعال است. وضعیت اجرا را از فرایند زنده،
Git و شواهد همان چرخه بخوانید؛ عبارت RUNNING یا یک گزارش تاریخی اثبات سلامت نیست.

مدل جاری Supervisor: `gpt-6.1-sol` با `xhigh`.
مدل‌های Gemini از `templates/gemini-runtime-contract.json` می‌آیند:
Worker=`gemini-4-argon-medium/medium` و Reviewer=`gemini-4-argon-high/high`.
این انتخاب در ۲۰۲۶-۱۰-۱۷ پس از مشاهدهٔ هر دو مدل در فهرست همین حساب و آزمون
واقعیِ مستقل هر دو نقش انجام شد؛ آن مشاهدهٔ تاریخی با CLI 1.4.0 بود.
نسخه/SHA جاری CLI فقط از runtime contract می‌آیند. inventory منابعِ CLI
مجوز ابزار نیست؛ Agent و hookهای محلی باید دقیقاً همان نقش محدود آماده‌شده باشند.
این تفکیک از خروجی‌های غیرقابل‌تحویل
ناشی از reasoning بی‌حد Worker جلوگیری می‌کند، درحالی‌که بازبینی مستقل روی بالاترین
effort باقی می‌ماند. Runner پیش از مصرف provider سازگاری قرارداد و ارجاع‌هایش را
می‌سنجد. حذف review یا سبک‌کردن تست برای صرفه‌جویی مجاز نیست.

شاهد مستقل ۲۰۲۶-۱۰-۱۸، فقط qualification غیرproviderِ 713Q برای artifact رسمی و
غیرفعال CLI 1.4.2 با SHA085f43e0d0628a8a8dabe1e547067818fa2c0adc86cfac98a3c4492918f455ef بود:
version دو بار و profile تازهٔ Worker medium و Reviewer high با صفر step/tool سنجیده
شد؛ فرایند آماده‌سازی پیش از generation عمداً متوقف شد. این شاهد نه invocation یا
actual executor، نه خروج graceful پس از terminal result، سلامت Medium/High، رفع hang
و نه activation را ثابت می‌کند. RAWِ Root با SHA7012e5acb1586f104495a39006f3a446d63362f1cde11258091fdd768190da50
و Return مشاهده‌شده با SHA9268363c9eb06b96284de73c65ef70620c8859fd971681cd9c7f195ca7ef5fcd
در `.local/two-site-refactor/reviews/AE03_713Q_ACTUAL_NONPROVIDER_RAW_20261018.json`
و `AE03_713Q_NONPROVIDER_RETURN_OBSERVED_20261018.json` حفظ شده‌اند؛ این ارجاع تاریخی
مجوز اجرا یا شاهد سلامت جاری نیست.

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

### جایگزین موقت بازبینی هنگام اختلال CLI — مجوز ۱۹ اکتبر

مالک در پاسخ صریح به پرسشِ جایگزینی موقتِ نویسنده و بازبین Gemini با دو عامل
مستقل Codex، دخالت لازم را تأیید کرد. تا رفع اختلال CLI، Root می‌تواند در مسیر
دستی و Supervisor متوقف، کد، تست و مستندات لازم را به یک عامل Codex بسپارد،
بازبینی معناییِ تمام تغییرات را از عامل مستقلِ غیرنویسنده بگیرد و پذیرش نهایی
و Git را خودش انجام دهد. برای اجزای دارای نویسندگان متفاوت، استقلال باید برای
هر جزء جداگانه قابل اثبات باشد. بازبین حق خودتأییدی ندارد؛ Root نیز جای بازبین
مستقل را نمی‌گیرد.

این مسیر به invocation یا خروج موفق Gemini وابسته نیست و هیچ Worker، session،
`TASK_READY` یا receipt Gemini ساخته نمی‌شود. نام عامل واقعی، منشأ کد، Scope تازه،
HEAD، manifest کامل BEFORE/AFTER، یافته‌ها و نتیجهٔ بازبینی ثبت شوند. آزمون واقعیِ
hermetic و بدون شبکه قبل از اعمال و پس از اعمال، بررسی کامل Source/diff/mode،
چهار قفل Supervisor/Broker-client/pipeline/Runner، STOP، protected refs، و بازگشت
عادیِ holdها لازم‌اند. Root فقط bytes دقیقِ آزموده و مستقل‌بازبینی‌شده را به‌شکل
سریال و با یک parent ثبت می‌کند. خطاهای محیط، آزمون و بازبینی حفظ شوند.

این مجوز، جایگزین عمومی Validatorهای Runner/Supervisor یا فعال‌سازی خودکار
fallback نیست؛ مسیر unattended پیش‌فرض همچنان Gemini است. بودجه‌های انتقال به
Gemini تغییر نمی‌کنند و برای مسیر دستی نباید packet یا readiness جعلی ساخت.
رزروها، checkpoint، no-replay و شکست‌های قبلی حفظ می‌شوند؛ startup/adoption
هنوز گیت‌های خودش را دارد. main/محیط عملیاتی/deploy/DNS/Web Writer خارج دامنه‌اند.
پس از رفع قابل‌اثبات اختلال، بازگشت به مسیر عادی با بررسی bindingهای جاری انجام
شود؛ پذیرش این اصلاح به معنی سلامت چرخه یا پایان پلن نیست.

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

تغییر template/header در `GEMINI_TASK_PROMPT.md` باید چهار regression کاملِ
correction_prompt_contract، prompt_transport_contract، verifier_context_budget و
proposal_runner_effort و کل ماتریس۱۴ماژولیِ شامل همان چهار را اجرا کند. نام‌های
ماتریس زیر قالب `tests/test_two_site_<name>.py` هستند:
`proposal_runner_effort, autopilot_monitor, runtime_proposal_profile, profile_metadata_cardinality,
cli_artifact, cli_version_exit_integration, proposal_runner, cli_exit_pin,
worker_model_routing, runtime_profile_trust_deadline, prompt_transport_contract,
correction_prompt_contract, verifier_context_budget, autopilot_monitor_wiring`.
Goldenهای وابسته فقط از bytes کاملِ مستقل‌بازبینی‌شده به literal ثابت هم‌تراز شوند؛
clauseهای محافظت‌شده، assertionهای hash و همهٔ negative vectorها حفظ شوند. این یادآوریِ
فرایند و قراردادهای موجود است، نه runtime gate یا مجوز تازه.

قرارداد Runner دقیقاً شش کلید دارد:
`task_id`, `goal`, `context_files`, `allowed_files`, `requirements`, `test_selectors`.

سیاست جاری بسته‌بندی، ۳ نوامبر ۲۰۲۶: `P1-00B-BATCH-V1` قرارداد تاریخیِ
۲ تا ۴ handler از یک ماژول و یک فایل تست باقی می‌ماند و اعتبارسنجی آن تغییر نمی‌کند.
این قالب تنها شکل مجاز کار جدید نیست. یک Task عادی با همان شش کلید می‌تواند یک
نتیجهٔ رفتاری منسجم را با چند سناریوی مرتبط، از جمله persistence/retry/reload/origin،
پوشش دهد؛ به شرط زمینهٔ کامل، assertion و selector مستقل برای هر سناریو، بودجهٔ
واقعیِ ورودی/خروجی و بازبینی تمام candidate. برای چنین Taskهایی از scenario_key عادی
و توصیفی استفاده شود. شواهد، پذیرش اتمیک و Git سریال حفظ می‌شوند؛ هیچ شرط پوشش،
High یا پایان Stage سبک نمی‌شود و Task تاریخی یا reservation قبلی بازنویسی نمی‌شود.

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
چهار کلید بالا فقط raw transport مدل‌اند؛ هر change در proposal کانونیِ materializeشده
دقیقاً سه کلید `path`, `original_sha256`, `content` دارد. در مسیر دستی Codex، summary
کانونی باید با `CODEX_AUTHORED` آغاز شود؛ prefix و شکل سه‌کلیدی پیش از ساخت Task بررسی
شوند تا raw چهارکلیدی به validator کانونی داده نشود. این preflight نه TASK_READY است
و نه جایگزین Scope، Source/byte budget، native یا بازبینی مستقل.
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

1. Assignment تازه، معتبر و صادرشده، canonical `TASK_READY` واقعی (یا فقط readiness
   نام‌گذاری‌شدهٔ پروفایل محدود Runner در پایین، با تمام شروط آن)، integration root،
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

### پروفایل محدود Runner؛ تعریف قرارداد، نه فعال‌سازی

`ROOT_REVIEW_ONLY_AE03_RUNNER_TEXT_V1` فقط برای Scope تازهٔ
`WA-AE03-RUNNER-TEXT-STDIN-20261018-725B` و تغییر دو عبارت در
`scripts/two_site_proposal_runner.py:run` تعریف می‌شود: ورودی CLI از stream-json
به text و bytes ورودی از envelope به `prompt.encode("utf-8")` تبدیل شود.
هیچ print/prompt flag اضافه نمی‌شود؛ schema، output، مدل، profile، EOF، انتظار
خروج واقعی، timeout، termination، cleanup و دیگر bytes/رفتارها ثابت می‌مانند.
Source اولیه دقیقاً SHA55cd2cc5f0dc8489514db8843d3e3114532d2beced0566b0456e2cf87d5c8af4
و AFTER دقیقاً SHAd786e58266893481c9bbd8447d0228170b2037f91073ac2b0ad447bc1f59367f است.
این نگاشت اجازهٔ تغییر دیگری یا ادعای رفع hang واقعی نیست.

علت محدودیت: حتی snapshot تشخیصیِ Runner کامل با Task خالیِ غیرمرجع 162736 بایت
است؛ proposal کانونی نیز از160000 می‌گذرد. compact پس از admission ساخته می‌شود.
در این یک پروفایل، فقط aggregate کامل TaskSources و canonical-proposal/readback
حداکثر172000 بایت می‌شوند. خواندن هر Source خام، raw AFTER و مجموع raw AFTER
همچنان حداکثر160000 است. پیش‌فرض TaskSources/proposal160000 و Root187000 برای
تمام مسیرهای دیگر دست‌نخورده می‌ماند؛ اندازهٔ واقعی همهٔ دامنه‌ها پیش از اجرا ثبت شود.

پیش‌نیاز مستقل و مقدم، پذیرش کامل factory سازگار و آزمون‌هایش و witness هر دو
ورودی است؛ این بستهٔ آماده‌سازی با همان بودجه‌های عادی، Source کامل و review کامل
اجرا می‌شود. factory فقط دو زوج ثابت Source→run-AST را می‌شناسد: زوج فعلی
55cd…/90afa2143a2172212027a01092aa80107cd1b5f19cc6bdf3349671c9add0232b و زوج آیندهٔ
d786…/8951f6c504fdfeeaaa047340f4b5557b581514e7d25da2bcafb938d42fa3197d.
عضویت جداگانه در دو مجموعه کافی نیست؛ cross-pair، Source ناشناخته و code/debug
نامنطبق رد شوند. factory خراب یا suite حذف‌شده را به follow-up موکول نکنید.

فقط پس از پذیرش جداگانهٔ کد همان factory، پیاده‌سازی پین‌شده می‌تواند readiness
صریح `ROOT_RUNNER_TEXT_TASK_READY_V1` و review function ثابت این پروفایل را بدهد.
بدنهٔ همهٔ validationهای عادی، Scope/path/selector/secret/link/Source و کنترل‌های
مدل حفظ شود؛ تنها دامنه‌های aggregate بالا مجاز به تفاوت‌اند. private loader،
canonical validator و allowance خواندن proposal باید ثابت، byte/AST-bound و
مقید به همین Task/Scope/HEAD/path/Source باشند. خواندن Source خام در همان160000
بسته بماند. shared SDK/globals یا تابع برگشتیِ factory قبلی را تغییر ندهید؛
هیچ namespace/callback/encoder/cap دلخواه از درخواست یا محیط پذیرفته نشود.
قائم‌مقامیِ ثابتِ اضافی باید در factory مستقل‌بازبینی‌شده صریح باشد، نه monkeypatch
پنهان adapter. public check عادی همچنان ممکن است BUDGET_EXCEEDED بدهد؛ آن شکست
TASK_READY نامیده نشود. readiness جدید همهٔ شروط واقعی را اجرا و اندازه/هویت‌ها
را ثبت کند، نه اینکه خطای budget را بگیرد و موفقیت برگرداند.

برای همین دو عبارت، تنها استثنای full-file-before مدل، projection موجود و صادقانهٔ
compact است: کل تابع run با decorator/importها و تمام تعریف‌های وابستهٔ لازم،
diff کانونیِ بازسازندهٔ AFTER، manifest کامل، تمام requirements، schema، native
واقعی و Role کامل حفظ شوند. Gemini باید نشانگر
`EXCERPT_NOT_FULL_SOURCE_CODEX_FULL_REVIEW_REQUIRED` و محدودیت زمینه را ببیند؛
ادعای دیدن کل Runner ممنوع و نبود تعریف لازم CHANGES_REQUIRED است.
Root تمام Source اولیه و AFTER را کامل بررسی و byte-pin کند و با inverse دقیق
ثابت کند هیچ byte خارج دو عبارت تغییر نکرده است. این استثنا فقط بند full-file
مدل و bindings ثابتِ فوق را برای همین مورد supersede می‌کند، نه کیفیت/پذیرش جزئی.
compact material120000، prompt کامل compact160000، actual input-counter64000
و request192000، سقف ترکیبی خروجی و همهٔ گیت‌های CLI/session/executor ثابت‌اند.

مرز172000/172001 و چندبایتی، per-file160000/160001، cross-pair، هویت/Scope/path/
selector غلط، namespace اضافی، تغییر shared SDK و سازگاری تمام مسیرهای عادی تست
شوند. native شامل suite کامل fidelity و چهار suite725A، به‌علاوهٔ تست‌های profile
جدید و compact باشد. native، review مستقل High، Source/Git و qualification واقعی
خروج CLI هیچ‌کدام با سند جایگزین نمی‌شوند. تا پذیرش پیاده‌سازی، Scope تازه،
adapter ثابتِ مستقل‌بازبینی‌شده و intent تازه، این پروفایل اجرایی نیست؛ مجوز تماس،
restart/adoption، replay/reset یا Stage/سلامت/صرفه‌جویی از آن حاصل نمی‌شود.

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

نسخه و SHA جاری Antigravity CLI از `templates/gemini-runtime-contract.json` می‌آیند. منبع کانونی در
.local/tools/ زیر checkout مشترک، متعلق به UID جاری، single-link، read-only و دقیقاً
همان SHA است. هر اجرا فقط روی کپی disposable در پوشهٔ موقت 0700 انجام می‌شود.
AGY_CLI_DISABLE_AUTO_UPDATE=true؛ نسخه و SHA پیش و پس از اجرا بررسی می‌شوند.
کپی موقت قبل از آزادشدن lock در موفقیت/خطا/timeout جمع می‌شود؛ adapter 1.1.26 قدیمی است.


### آماده‌سازی مستقیم profile — ۲۲ اکتبر ۲۰۲۶

برای حذف وابستگی به منوی تعاملی و زمان‌بندی کلیدها، آماده‌سازی فقط با گزینهٔ
رسمی CLI پین‌شدهٔ 1.4.2، یعنی `--agent two-site-proposal-only`، آغاز می‌شود؛
`--add-dir` فقط مسیر دقیق همان workspace تازهٔ 0700 و متعلق به فراخوانی را
صریحاً به CLI می‌دهد، نه مسیر والد، ریپازیتوری اصلی یا دایرکتوری دیگری.
هیچ `/agents`، arrow-selection یا reselection اجرا نمی‌شود؛ متن UI مرجع انتخاب
یا پذیرش agent نیست. تنها پاسخ کنترل‌شدهٔ UI، trust دقیق همان workspace تازه
و متعلق به فراخوانی و پاسخ‌های ثابت terminal است؛ trust عمومی مجاز نیست.

metadata از آغاز آماده‌سازی بررسی می‌شود؛ پیش و پس از مشاهدهٔ READY، خروجی
موجود PTY خوانده می‌شود تا trust کامل یا نیمه‌رسیده دور زده نشود. فقط READY
مشاهده‌شده داخل deadline و پس از بررسی trust می‌تواند واجد پذیرش باشد؛ metadata
ایجادشده صرفاً هنگام cleanup کافی نیست. deadline پیش‌فرض۳۰ثانیه، دامنهٔ معتبر
۲۰–۴۵ثانیه و سقف تجمعی UI بدون تغییرند. پس از cleanup واقعی، validation نهایی
مستقلِ profile تازه و یکتا، Source/BODY/hooks و bindingهای مدل/effort/CLI همچنان
الزام است. نادیده‌گرفتن `--agent`، Default fallback، metadata نامعتبر یا مبهم،
session مصرف‌شده و خطای واقعی مجوز/auth/quota راه عبور یا retry کور ندارند.

qualification محدود ۲۲ اکتبر با همین CLI پین‌شده، دو profile تازهٔ Worker و
Reviewer را با validator اصلی، به‌ترتیب در۰٫۸ و۰٫۷ثانیه آماده تشخیص داد؛ هرکدام
فقط یک session، صفر ابزار و policy=off داشتند و cleanup عادی مشاهده شد.
شاهد خصوصی Root، `RUNTIME_772M_DIRECT_AGENT_R3_QUALIFICATION_ROOT_ACCEPTED_20261022.json`،
فقط سازگاری آماده‌سازی را اثبات می‌کند؛ نه generation، actual executor، علت
داخلی شکست C004 یا تضمین دائمی سلامت. probe ناموفق قبلی نیز محفوظ است.

این مسیر هیچ prompt کاری یا generation ندارد. generation فقط با `--conversation`
همان profile پذیرفته‌شده آغاز می‌شود و کنترل actual executor و دیگر گیت‌های
Runner/Reviewer/native/Codex/Git تغییر نمی‌کنند. Worker یا pipeline قبلی تکرار
نمی‌شود؛ reservation، checkpoint و همهٔ شکست‌های تاریخی حفظ‌اند. آزمون و
qualification تازه لازم‌اند؛ این قرارداد تضمین دسترسی دائمی Provider نیست.

### بازیابی محدودِ startup خالی — قرارداد 774M

سناریو: CLI اول پیش از ارسال هر prompt، تا پایان مهلت اصلی خود زنده است اما
فقط یک SQLite تازه و واقعاً خالی ساخته است. پس از مرگ واقعی همان فرایند و
cleanup بی‌خطای callback، selector و PTY، حداکثر یک initializer تازه در همان
`prepare_profile` و همان Runner/reservation مجاز است. setup، Git-root،
workspace0700، BODY و hooks فقط یک بار ساخته می‌شوند. موفقیت اول مستقیماً
برمی‌گردد؛ شکست دوم پایان قطعی دارد. این رفتار retry درخواست مدل، Worker،
Task، generation یا replay هیچ شکست تاریخی نیست.

مجوز دوم از hint قبلی گرفته نمی‌شود: diagnostic باید CAPTURED، UI دقیقاً
صفر بایت، فرایند پیش از cleanup واقعاً RUNNING و همه نشانه‌های
trust/auth/network/dependency/READY غایب باشد. سپس snapshot مستقلِ فقط‌خواندنی
و WAL-visible باید کل `trajectory_meta` را همان CID/type0/source0، کل
`trajectory_metadata_blob` را فقط main/blob خالی، و steps/executor را بدون
هیچ ردیف ثابت کند. DB و WAL با identity و hash محدود پیش و پس از خواندن
کنترل می‌شوند و تا پایان دوم ثابت می‌مانند. SHM فقط lock/index موقت است:
owner/path/regular/nlink/identity/size آن بررسی می‌شود، نه hash یا mtime آن.
نشست اول اگر دیر معتبر شود، چه تنها چه همراه دوم، هرگز پذیرفته نمی‌شود.
CID نهایی فقط عضو یگانه delta دوم است؛ roster سراسری و سقف۸ نیز حفظ می‌شوند.

fingerprint اختیاری فقط برای executable دقیقاً۲۲۱۲۸۶۶۵۶بایتی و SHA پین‌شده
CLI1.4.2 مجوز recovery می‌دهد. فقدان یا عدم اثبات pin صرفاً NO_RECOVERY
است، نه خطای مصنوعی startup اول. پس از آغاز hash، بودجهٔ مصرف‌شده فراموش
نمی‌شود؛ انقضای کل مجوز اجرای اول یا دوم نمی‌دهد. پیش از دوم همان CLI،
workspace، definition و hooks دوباره بررسی می‌شوند؛ کنترل version/hash
پیش/پس Runner همچنان مستقل و بدون تغییر است.

بودجهٔ منطقی کل۲۴۰ثانیه شامل setup/hash/SQLite/cleanup است؛ hash اختیاری
حداکثر۱۰ثانیه فرصت منطقی دارد، و timeout هر initializer همان۲۰–۴۵ثانیه با
پیش‌فرض۳۰ است. دوم فقط با فرصت کامل timeout اصلی+۱۰ثانیه cleanup آغاز می‌شود.
timeout معمول SQLite برای callerهای قدیمی۵ثانیه باقی است؛ deadline اختیاری
آن را فقط به باقیمانده محدود می‌کند. callback موجود Runner همان
SIGKILL+wait(timeout=10) است؛ hard-real-time برای callback دلخواه یا OS I/O
گیرکرده ادعا نمی‌شود. UI/metadata نامعتبر یا مبهم، session مصرف‌شده، lock/
read/schema error، late metadata در cleanup، CLI/Source drift، نشست ناشناخته،
کمبود بودجه و cleanup نامعلوم همگی fail-closed هستند. هیچ cap، مدل/effort،
no-tools، trust، strict metadata یا گیت generation/Review/native/Git ضعیف
نمی‌شود. علت upstream و اثربخشی عملی همچنان نیازمند شاهد واقعی‌اند؛ این
قرارداد تضمین دائمی Provider یا credit پیشرفت معماری نیست.

### تکمیل محدود recovery برای init رسمی headless — شاهد 791Q و اصلاح 793M

PRE واقعی792M با118 قبولی و7 شکست پذیرفته نشد؛ هیچ Source apply، POST، qualification
یا Git برای آن انجام نشد. شش شکست نشان دادند نام agent صحیح در frame، نشانهٔ
متنی fixed_agent_name_present را true می‌کند ولی شرط recovery قبلی همان را رد
می‌کرد. اصلاح793M فقط این تداخل تشخیص متنی/هویت ساختاری را می‌بندد و fixture
آزمونِ cleanup نامعلوم را از EIO ناشی از بستن master متعلق Source جدا می‌کند؛
هیچ شکست یا آزمون قبلی حذف یا به قبولی تعبیر نمی‌شود.

بخش شناخت frame مطابق شاهد واقعی791Q تصحیح می‌شود؛ اصلاح eligibility فقط
استثنای محدود boolean نام را دارد و سقف‌ها و دیگر گیت‌های recovery همان‌اند.
شکل چهارفیلدی قبلی787M/789M برای CLI واقعی کافی نبود:
در آزمون فیزیکی789M هر دو نقش UNEXPECTED_PAYLOAD داشتند؛ Worker با metadata واقعی
READY شد ولی Reviewer نشد و qualification کلی شکست خورد. Native124 قبلی شاهد
baseline است، نه پذیرش Source/Git یا اثبات درست‌بودن شناخت پروتکل؛ شکست حفظ می‌شود.

شاهد ساختاری واقعی بدون ورودی791Q در
`RUNTIME_791Q_SAFE_SCHEMA_ONLY_REVIEWER_ROOT_ACCEPTED_20261022.json` با SHA256
`15604ec2e5fbd660aba41dd0c39f6a11e667624746a1f0abd9a54050d71a0746`
envelope دقیق `event/conversation_id/init` و payload بستهٔ پنج‌فیلدی
`agent/cwd/model/permission_mode/tools` را نشان داد. چهار فیلد اول string و tools
فهرست60string بود؛ json_schema واقعاً غایب بود. مقادیر خام ذخیره نشدند و مقدار
agent یا برابری آن با agent درخواستی مشاهده/اثبات نشده است. نبود flag یا tag
omitempty به‌تنهایی برای استنتاج این شکل کافی نیست.

فقط یک frame کامل NDJSON با `event=init`، CID یگانهٔ تازهٔ همین فراخوانی،
مدل/workspace دقیق و permission_mode=`request-review` مجاز است. agent باید string
و دقیقاً برابر NAME ثابتِ درخواستی `two-site-proposal-only` باشد؛ این شرط محافظه‌کارانه
است، نه ادعای مقدار مشاهده‌شده. هر دو نقش واقعی باید پیش از پذیرش Source/Git زیر
همین شرط OWNED_INIT_ONLY شوند؛ metadataِ READY همراه UNEXPECTED_PAYLOAD کافی نیست.
tools فقط فهرست محدود، غیرتکراری و ASCIIِ advertised است، نه اجرای ابزار.

هر json_schema با هر مقدار، حتی null، و هر فیلد اضافه/ناقص، agent غایب/غلط/نوع‌غلط،
event دوم، user/control/step/result/error/auth/trust، کلید تکراری، NaN، UTF8 نامعتبر،
surrogate، خروجی ناقص، control/ANSI یا newline میانی رد می‌شود. تنها LF پایانی یا
تبدیل واقعی آن به CRLF توسط PTY مجاز است. init هرگز اثبات انتخاب agent یا READY نیست.

این شاخه فقط به فرایند هنوز RUNNING و cleanup/FD/reap واقعی و بی‌ابهام، بدون
هیچ invalid/multiple/READY یا نشانهٔ trust/auth/network/dependency اجازهٔ بررسی
می‌دهد. cwd دقیق همچنان owned_workspace_present=true می‌خواهد. در شاخهٔ
OWNED_INIT_ONLY، نام agent از JSON کامل و decodeشده دقیقاً با NAME تطبیق یافته؛
بنابراین fixed_agent_name_present فقط باید boolean باشد و حضور یا نبودن متن خام
نام مانع نیست: JSON می‌تواند حروف نام را با \uXXXX نمایش دهد. فقط همین دو نشانهٔ
نام/cwd از الزام false استثنا دارند؛ دیگر نشانه‌های خطر همگی false می‌مانند.
این استثنا برای خروجی خالی قدیمی یا banner/agent نامعتبر مجاز نیست. CID مشاهده‌شده
پیش از cleanup باید با delta نهایی
یکسان بماند. سپس همان snapshot واقعیِ مستقل، WAL-visible، کامل و pinشدهٔ
774M باید DB نخست را pristine و فاقد هر step/executor ثابت کند؛ خطای SQLite
پایدار، DB/WAL/roster/CLI/workspace drift یا metadata دیرهنگام همچنان مانع است.
hint گذرای SQLite فقط با این init متعلق و تمام شواهد قطعی بعدی غیرمرجع است.

سقف دو initializer، کل۲۴۰ثانیه، پیش‌فرض۳۰/دامنه۲۰–۴۵، cleanup ده‌ثانیه‌ای و
تمام validationهای metadata قبل و بعد از cleanup بدون تغییرند. enum ثابت
`init=...` صرفاً تشخیص خروجی ساختاری را نگه می‌دارد؛ payload/CID/path/UI ذخیره
نمی‌شود. این طراحی بایت‌های تاریخی C003 یا دیگر شکست‌ها را حدس یا بازپذیرش
نمی‌کند، replay مدل/Task/Worker/Reviewer مجاز نمی‌سازد و تضمین سلامت دائمی نیست.

### تکمیل generation در runtime contract 1.3 — ۲۰ اکتبر ۲۰۲۶

سیاست `FINAL_FRAME_RECOVERY_V1` فقط generationِ pinned Gemini در Runner را opt-in
می‌کند. capture پیش‌فرضِ identity probe، Broker، Codex و مسیر عادی Opus تغییر
نمی‌کند. خروج طبیعیِ صفر همچنان `NORMAL_EXIT` است؛ نتیجهٔ نوع‌دار
`RECOVERED_COMPLETED_RESULT` خروج صفر یا سلامت transport نیست: timeoutِ
post-final، درخواست واقعی SIGKILL و returncode واقعیِ غیرصفر را حفظ می‌کند.
این قرارداد جایگزین الزام خروج طبیعی فقط برای حالت محدود زیر است، نه مجوز
پذیرش پاسخ ناقص یا خاتمهٔ فرایند و ساخت READY.

- تمام input واقعاً نوشته و writer متعلق به Runner بسته شده باشد؛ مشاهدهٔ close
  پیش از *خواندن* terminal لازم است. این ترتیب، زمان emission یا ingestion در
  backend را اثبات نمی‌کند. یک init با CID/model دقیق، فقط یک terminal SUCCESS
  آخر با `num_turns` صحیحِ integer و برابر۱، schema/result معتبر، بدون tail
  ناقص/رویداد پس از result و بدون tool/subagent attempt یا denied action لازم است.
  فهرست advertised tools در init ابزارِ اجراشده نیست؛ effort از argv و پروفایل
  پشتیبانی‌شده bind می‌شود، نه از فیلد خیالی init.
- فقط CLI هنوز زنده پس از grace ثابت۵ثانیه‌ای post-final واجد بررسی است؛ طبیعی
  nonzero، parent-exited/inherited-pipe، EOF ناقص، stderr متناقض یا identity نامعلوم
  بازیابی نمی‌شوند. ورودی/خروجی همان سقف‌ها را دارند و deadline اصلی تمدید نمی‌شود.
  پس از cleanup، *تمام* stream دوباره بررسی می‌شود: reap فرزند مستقیم، نبود
  گروه PGID متعلق و pinشده و EOF واقعی هر دو pipe باید پیش از deadline محرز باشد.
  این primitive نبود همهٔ descendantهای escaped و بدون FD موروثی را اثبات نمی‌کند.
  cleanup شکست‌خورده یا نامعلوم همواره نامعتبر می‌ماند.
- profile/hooks و actual executor در workspace زنده بررسی می‌شوند؛ سپس context
  disposable باید واقعاً و به‌طور عادی خارج شود، شامل exit pin، disposal/temp
  cleanup و آزادسازی lock. تنها پس از آن result/delta/patch و proof خصوصی ساخته
  می‌شوند و status واجد شرایط *آخر* منتشر می‌شود. خطای اول با خطای cleanup پوشانده
  نمی‌شود؛ STOP یا Source/profile/CLI drift مجوز dispatch بعدی نیست.
- proof برای هر دو outcome اجباری است. Readerهای Runner `load_prior_worker`،
  Pipeline `validate_run` و Supervisor `_validate_gemini_chain` آن را از قرارداد
  جاری مطالبه و retained bytes/schema/CID/model/effort و typed observations را
  مستقل تطبیق می‌دهند؛ حذف هم‌زمان marker و pointer راه legacy باز نمی‌کند.
  در correction، raw delta، candidate sources و prior identity حفظ و rebase دقیق
  دوباره محاسبه می‌شود. proof به result/raw/snapshot bind است و status به SHA
  proof/result؛ وابستگی hash چرخه‌ای به خود status وجود ندارد. Reader مشاهدهٔ
  تاریخی OS را بازاجرا یا با دادهٔ جدید جایگزین نمی‌کند.

آزمون native و Gemini Reviewer تازه و مستقل، Scope، no-replay، quota، STOP،
پروفایل/CLI pin، sandbox و همهٔ گیت‌های پذیرش پابرجا هستند. helper stdlib جدید
`scripts/two_site_generation_completion.py` عضو بیست‌وششم closure واقعی SDK است؛
۲۵ عضو پیشین حذف یا تعویض نمی‌شوند. انتقال فیزیکی 1.2→1.3 به اعتبارسنجی
phase-aware دقیقِ قرارداد جدید و SDK26 تازه پس از Source نیاز دارد؛ validator
قدیمیِ بارگذاری‌شده حق ندارد با monkeypatch/reload ادعای پذیرش 1.3 کند.

746Q شکست تاریخی و مصرف‌شده می‌ماند: پاسخ کامل سپس hang مشاهده شد، اما
parent-return/PGID/EOF پیش از cleanup ثبت نشد، پس علت دقیق graceful-exit نامعلوم
است. این تغییر ادعای رفع مشاهده‌شدهٔ آن شکست، qualification زنده، adoption،
Start/Monitor، سلامت، صرفه‌جویی یا بسته‌شدن AE03/Stage/هدف کلی نیست. کار ضروریِ
آینده، از جمله شکست‌ها، باید outcome واقعی و هزینه/UNKNOWN را نگه دارد.

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

## 794M — برابری محدود WAL در آماده‌سازی بدون ورودی

این اصلاح فقط قرارداد pristine مربوط به initializer بدون رویداد را تصحیح می‌کند.
شاهد مصنوعیِ مستقل
`RUNTIME_793M_SYNTHETIC_SQLITE_READONLY_WAL_CUSTODY_ACTUAL_20261022.json`
با SHA-256
`70a5365be30e6ed71d811713e8d75848568beadfd7aeab5ee8079ed4ce972799`
در SQLite 3.45.1 هر چهار حالت «نویسندهٔ زنده/خارج‌شده × WAL صفر/غیرصفر»
را پوشش می‌دهد: اجرای SQL فقط‌خواندنی می‌تواند فقط ctime فایل WAL را تغییر دهد؛
بایت‌ها و هشت مؤلفهٔ دیگر WAL، کل هویت نُه‌تایی DB و شکل منطقیِ خالی ثابت می‌مانند.
این شاهد، سازوکار را اثبات می‌کند؛ علت دقیق شکست تاریخیِ 793M را عطف‌به‌ماسبق
قطعی نمی‌کند و جایگزین شواهد مصرف‌شده یا qualification جدید نیست.

دامنهٔ استثنا فقط مقایسهٔ WAL در دو سوی SQL فقط‌خواندنی و snapshotهای pristine
بعدی است: WAL به صورت `(identity[:8], sha256)` مقایسه می‌شود؛ نبود/وجود،
device، inode، mode، uid، gid، nlink، size، mtime و هش دقیق بایت‌ها همچنان باید برابر
باشند. این tuple یک هویت مقایسه‌ایِ هشت‌تایی است، نه گزارش جعلیِ full9.
فقط ctimeِ WAL از این برابریِ بین‌خواندن‌ها کنار گذاشته می‌شود. محدودکردن به WAL
صفر کافی نیست، زیرا همان سازوکار برای WAL غیرصفر نیز واقعاً مشاهده شده است.
`_persisted_snapshot` در طول تک‌تک خواندن‌های محدود، همچنان هر نُه مؤلفه را
پیش/پس از خواندن کنترل می‌کند؛ DB، CLI و همهٔ کنترل‌های فایل دیگر full9 می‌مانند.
بایت، اندازه، مالکیت، hardlink، symlink، journal، WALِ جدید/حذف‌شده، roster،
metadata کامل، صفر بودن step/executor و cleanup هیچ استثنای تازه‌ای ندارند.

برای جلوگیری از شکستِ دوباره با علت نامشخص، veto واقعیِ pristine فقط یک کد ثابت
و بسته را پیش از فراخوانی formatter به همان فیلد موجود `reason` می‌رساند؛
برای نمونه `PRISTINE_FIRST_LOGICAL_MAIN_CONTRACT`. نام عملیات و نوع ثابت خطا
از مقادیر کنترل‌شده ساخته می‌شوند؛ متن خطای زیرین، SQL row، CID، مسیر،
payload و اطلاعات احراز هویت وارد کد نمی‌شوند. reason حداکثر ۶۴ کاراکتر ASCII
و suffix همچنان حداکثر ۵۱۲ کاراکتر است. exception اصلیِ initializer اول با همان
identity دوباره بالا می‌رود. veto بی‌برچسب یا برچسب نامعتبر همان reason قدیمیِ
`PRISTINE` را حفظ می‌کند. observer موجود همان `phase/reason/attempts/pinned`
را در CALL واقعی دریافت می‌کند؛ schema یا پروتکل qualifier تغییر نمی‌کند.
این تشخیص، مجوز retry نیست و هیچ timeout، بودجه، تعداد initializer یا شرط پذیرشی
را تغییر نمی‌دهد.

آزمون‌های تازه باید SQLite واقعیِ چهارحالت، حفظ تمام فیلدهای باقی‌مانده و هش،
منفی‌های تغییر WAL/DB و ctime حین یک خواندن، بازیابی PTY بدون ورودی، حفظ
exception اصلی و fallback تشخیصی را پوشش دهند. همهٔ ۱۲۶ هویت آزمون قبلی و
گاردهای منفی حفظ می‌شوند. پذیرش عملی به Native تازه، qualification واقعیِ مستقل
هر دو نقش با frame شناخته‌شده و READY، cleanup عادی، گیت Git و شواهد واقعی Root
نیاز دارد؛ موفقیتِ synthetic یا متن این سند به‌تنهایی سلامت چرخه، پذیرش Source،
پیشرفت Stage یا صرفه‌جویی را اثبات نمی‌کند.

### 797R — complete terminal budget rejection is not provider silence

The consumed C004 Worker returned a complete SUCCESS envelope in about 115.096
seconds. Its new test was 14,714 UTF-8 bytes against an immutable 12,000-byte
limit. Input was fully written, stderr was empty, and stdout was retained, but
the final-frame callback suppressed that definite validation error and capture
waited for the 915-second deadline. That failed Task/reservation remains
unaccepted and immutable: this repair neither replays nor adopts its response.

The existing new-test byte gate now attaches private, in-memory provenance to
the same original ProposalError. Only the exact worker error, private token,
canonical new-file path, immutable cap and actual UTF-8 excess may escape the
strict final-frame callback. The original exception type, message and identity
are preserved. This marker is never serialized into a proposal, completion
observation or status contract. Unmarked/lookalike/subclass errors, existing or
unknown paths, non-worker roles, framing/CID/model/tool failures, partial output
and stderr do not gain recovery eligibility.

A fast rejection is not successful completion, EOF, recovery or acceptance.
The existing failure path still kills the owned process group, reaps the direct
child, closes every owned pipe/file, disposes the private CLI and publishes only
BLOCKED evidence. No result, proposal patch or accepted completion proof may be
published. Valid final results retain the real five-second shutdown grace and
all existing finality, cleanup and proof gates.

No byte cap, model, retry limit, completion schema/outcome, default capture or
Opus path changes. This budget error is still not an OUTPUT retry: avoiding a
known-wrong 915-second wait does not authorize automatic correction or replay.
Any next legitimate unit must independently produce an in-budget proposal and
pass review, Native tests, normal Git and SDK checkpoint acceptance.

## 800R — پایداری SHM موقت و تشخیص دقیق‌تر توقف آماده‌سازی

C014 پیش از تولید پاسخ مدل با علت ثبت‌شدهٔ
`PRE_SECOND / PRISTINE_RECHECK_SHM_AFTER_CONTRACT` متوقف شد. این شاهد
شرط جزئی شکست SHM را مشخص نمی‌کند؛ آن را عطف‌به‌ماسبق به race بی‌ضرر، تغییر
اندازه، حذف فایل یا مشکل سهمیه نسبت نمی‌دهیم. Task و reservation شکست‌خورده
دست‌نخورده می‌مانند و دوباره ارسال یا با نام دیگر بازپخش نمی‌شوند.

بازتولید مستقل با SQLite مصنوعیِ تحت مالکیت آزمایش نشان می‌دهد WAL index
می‌تواند از 32768 به 65536 بایت رشد کند، در حالی که device/inode/mode/uid/gid/nlink
ثابت‌اند؛ بستن آخرین اتصال هم می‌تواند SHM را حذف کند. آزمون رشد از نوشتن روی
جدول مصنوعی استفاده می‌کند و اثبات pristine بودن DB/WAL یا علت تاریخی C014 نیست.

کنترل SHM فقط این تغییرپذیری موقت را می‌پذیرد: اندازهٔ هر مشاهده باید همچنان
در حد مجاز باشد، اما دو اندازه لازم نیست برابر باشند. نوع regular، مالک جاری،
تک‌لینکی بودن، مسیر و parent canonical، و شش فیلد هویت امنیتی شیء موجود ثابت
می‌مانند. حذف تنها پس از بررسی یک شیء امن و تأیید دوبارهٔ نبود فایل مجاز است؛
symlink، hardlink، جایگزینی، مالک نامعتبر، اندازهٔ خارج حد و خطاهای دسترسی هرگز
به نبود فایل تعبیر نمی‌شوند. SHM خوانده یا hash نمی‌شود.

علت‌های ثابت و غیرمحرمانهٔ `BUDGET/TYPE/OWNER/LINKS/SIZE/PATH/IDENTITY/REAPPEARED`
در مرحلهٔ `SHM_BEFORE` یا `SHM_AFTER` ثبت می‌شوند؛ خطاهای قدیمی یا ناشناخته
همچنان fallback بستهٔ قبلی `OS/CONTRACT` را دارند. این تشخیص، مجوز recovery نیست.
اثبات کامل DB، مقایسهٔ محدود و قبلاً مصوب WAL، صفر بودن steps/executors،
readiness واقعی پیش از cleanup، کنترل process/FD/CLI و سقف 240 ثانیه/حداکثر دو
initializer بدون تغییر باقی می‌مانند. این بخش شرح تغییر پیشنهادی است، نه ادعای
قبولی Native، qualification زنده، استقرار یا پایان توقف.

## 802R — پایان پاک فرایند هم‌زمان با درخواست shutdown

در اجرای عادی801/C002، آماده‌سازی Worker و Reviewer موفق شد، اما Reviewer
با `generation shutdown exit race is ambiguous` متوقف شد. شواهد موجود نشان
می‌دهد درخواست SIGKILL واقعاً برگشته بود، فرایند مستقیم exit0 داشت، هر دو
کانال EOF بودند، کل ورودی نوشته و بسته شده بود و گروه متعلق به اجرا باقی
نمانده بود. این شواهد شکست قبلی را به پذیرش تبدیل نمی‌کند؛ Task، reservation
و خروجی C002 همچنان نامعتبر و دست‌نخورده می‌مانند.

قرارداد محدود جدید، فقط یک حالت صریح به همان
`RECOVERED_COMPLETED_RESULT` اضافه می‌کند: syscall درخواست سیگنال برگشته،
returncode دقیقاً int0 است و `timeout_kind=POST_FINAL_EXIT_RACE` ثبت می‌شود.
این حالت `NORMAL_EXIT` نیست؛ `normal_exit_observed=false` و
`signal_attempted=true` می‌مانند و `cleanup_signal=9` سیگنال درخواستی را
نشان می‌دهد، نه ادعای اینکه سیگنال علت exit0 بوده است.

حالت قبلی فقط زوج دقیق `-SIGKILL / POST_FINAL_SHUTDOWN` است و حالت جدید
فقط زوج دقیق `0 / POST_FINAL_EXIT_RACE`؛ تغییر یکی از دو مقدار، boolean به‌جای
int، ESRCH بدون بازگشت موفق syscall، خروج nonzero/unknown، گروه زنده یا نامعلوم،
ورودی ناقص، writer باز، terminal پیش از بسته‌شدن ورودی، EOF ناقص، stderr،
tail نامعتبر، frame ناسازگار یا انقضای deadline همچنان رد می‌شوند.

هر دو حالت از همان بررسی کامل frame و همان readiness/CLI/role/model/CID و
کنترل‌های فعلی Runner، Pipeline و Supervisor عبور می‌کنند. بودجهٔ کلی،
مهلت پنج‌ثانیه‌ای پس از terminal، cap ورودی/خروجی، retry و cohort مدل تغییر
نمی‌کند. این اصلاح مجوز ارسال دوباره یا پذیرش خروجی شکست‌خورده نیست.
آزمون مصنوعی واقعی فقط رفتار فرایندهای متعلق به تست را اثبات می‌کند؛
پذیرش Source و سلامت چرخه نیازمند شواهد واقعی مستقل Root هستند.
