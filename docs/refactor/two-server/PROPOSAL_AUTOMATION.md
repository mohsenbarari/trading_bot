# اتوماسیون Codex و Antigravity — قرارداد جاری

به‌روز: ۲۰۲۶-۰۹-۱۲. مسیر توسعهٔ خودکار:
Gemini Worker → Gemini Reviewer مستقل → Codex review/apply/test/commit.
مسیر قدیمی gateway/native همچنان غیرفعال است. وضعیت اجرا را از فرایند زنده،
Git و شواهد همان چرخه بخوانید؛ عبارت RUNNING یا یک گزارش تاریخی اثبات سلامت نیست.

مدل جاری Supervisor: `gpt-5.6-sol` با `xhigh`.
مدل Gemini از `templates/gemini-runtime-contract.json`: `gemini-3.8-flash-high`
با `high`. Runner پیش از مصرف provider سازگاری این قرارداد و ارجاع‌هایش را می‌سنجد.
هیچ کاهش effort، حذف review یا سبک‌کردن تست برای صرفه‌جویی مجاز نیست.

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

در توسعهٔ بدون‌نظارت، Gemini تنها نویسندهٔ source/test/project-doc و Codex بازبین نهایی
و تنها مالک Git است. استثنای موردی مالک برای نگهداری زیرساخت، مجوز تغییر این نقش‌ها در
چرخه‌های بعدی نیست. هیچ منطق محصول، deploy، DNS یا Web Writer از این ابزار مجوز نمی‌گیرد.

## آماده‌سازی استاندارد و کم‌هزینهٔ Task

قرارداد Runner دقیقاً شش کلید دارد:
`task_id`, `goal`, `context_files`, `allowed_files`, `requirements`, `test_selectors`.
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

TASK_READY واقعی پیش‌شرط تماس با provider است. خطای این مرحله محلی اصلاح می‌شود و
نباید مصرف Gemini ایجاد کند. سقف سخت بستهٔ task/sources و proposal استاندارد هرکدام
۱۶۰٬۰۰۰ بایت و سقف prompt کامل ۳۲۰٬۰۰۰ بایت است. اهداف پیشنهادی: task حدود ۸۰٬۰۰۰
بایت یا کمتر و بستهٔ کامل Reviewer زیر حدود ۱۶۰٬۰۰۰ بایت.
این اهداف گیت سخت جدید نیستند. Reviewer علاوه بر زمینه، فایل کامل خروجی را دریافت
می‌کند؛ کم‌بودن Worker input یا استفاده از replacement به‌تنهایی کافی نیست.

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

مسیر عادی دقیقاً یک pipeline برای هر واحد:

```bash
python3 scripts/two_site_proposal_pipeline.py \
  --task .local/two-site-refactor/tasks/<fresh-task>.json \
  --run-prefix <unique> --timeout 900
```

فرمان باید از اولین تلاش با escalation محدود اجرا شود؛ مالک در ۲۰۲۶-۰۹-۰۹
ارسال source محدود و غیرمحرمانه، tests، docs، task، proposal، review/findings و
test evidence به Google Antigravity/Gemini را دائماً و بدون تأیید جداگانه مجاز کرده است.
مجوز شامل دسترسی خود CLI به وضعیت احراز هویت خودش و listener موقت localhost است.
Codex نباید credential را بخواند، چاپ کند، کپی کند یا hash کند.
secret، اطلاعات هویتی/اعتباری، دادهٔ کاربر، دادهٔ عملیاتی/تولید، deploy، DNS،
Writer، fetch/push و تغییر سرویس نامرتبط خارج دامنه‌اند. رد مجوز دور زده نمی‌شود.

خروجی READY_FOR_CODEX مسیر دقیق Worker، Reviewer و proposal SHA را می‌دهد.
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

رد Reviewer، Codex یا تست با چهار کلید دقیق برمی‌گردد:
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

Antigravity CLI نسخهٔ 1.1.27 و SHA از runtime contract می‌آید. منبع کانونی در
.local/tools/ زیر checkout مشترک، متعلق به UID جاری، single-link، read-only و دقیقاً
همان SHA است. هر اجرا فقط روی کپی disposable در پوشهٔ موقت 0700 انجام می‌شود.
AGY_CLI_DISABLE_AUTO_UPDATE=true؛ نسخه و SHA پیش و پس از اجرا بررسی می‌شوند.
کپی موقت قبل از آزادشدن lock در موفقیت/خطا/timeout جمع می‌شود؛ adapter 1.1.26 قدیمی است.

Codex CLI نسخهٔ 0.153.4 با SHA ثبت‌شده در Supervisor، از مسیر مستقل این integration:
`.local/tools/codex-0.153.4-56ef98ab4032d317.pin`.
این مسیر به پوشهٔ نسخه‌دار افزونه وابسته نیست؛ fallback به PATH یا نسخهٔ تأییدنشده ندارد.
نسخه/SHA/login پیش از اجرا و SHA پس از اجرا کنترل می‌شوند.
این تغییر خطر حذف مسیر توسط update افزونه را رفع می‌کند، نه همهٔ خطاهای احتمالی میزبان.
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

```bash
scripts/two_site_autopilot_tmux.sh start
scripts/two_site_autopilot_tmux.sh status
```

tmux گفتگوی باز نمی‌خواهد. STOP در مرز چرخه رعایت می‌شود؛ stop-now برای توقف فوری است.
راه‌انداز خود تضمین reboot یا دسترسی همیشگی provider نیست. مانیتور نصب‌شدهٔ محلی
هر ۱۵ دقیقه جدا وضعیت فرایند/شواهد را می‌سنجد و STOP عمدی را نباید دور بزند.
حد اجرای Supervisor: ۲۰۰ چرخه، timeout هر چرخه سه ساعت، retry خطای گذرای بی‌تغییر حداکثر دو بار.

## مصرف توکن، نگهداری و مرز ادعا

پیش از prune هر run، حتی خطای اجرا، metrics کوچک ذخیره می‌شود:
مدل/effort، prompt SHA، HEADها، outcome، زمان، شمارش command، ورودی، cached،
uncached و output. reasoning اگر واقعاً موجود باشد زیرمجموعهٔ output است و دوباره
جمع نمی‌شود. دادهٔ ناقص/نامعتبر UNKNOWN/null است، نه مصرف صفر.
شناسه/هش Task و gap_reference فقط وقتی از snapshot همان چرخه در دسترس باشند ثبت می‌شوند.
هیچ prompt، source، command body یا متن خصوصی در metrics نگهداری نمی‌شود.

.local/two-site-refactor/autopilot/metrics.json حداکثر ۲۰۰۰ رکورد و ۲ مگابایت دارد؛
بعد از prune خروجی‌های خام باقی می‌ماند. پرشدن حلقه قدیمی‌ترین رکورد را کنار می‌گذارد.
خطای telemetry تشخیص‌پذیر است ولی commit معتبر را متوقف نمی‌کند.
۲۰ run عادی آخر نگهداری می‌شود؛ BLOCKED برای بررسی باقی می‌ماند.
proposalها retain_until هفت‌روزه دارند؛ این تاریخ به‌تنهایی ادعای حذف خودکار نیست.
شواهد فعال/ایراد باز بدون تعیین تکلیف پاک نمی‌شوند؛ backup دائمی نسازید.

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
