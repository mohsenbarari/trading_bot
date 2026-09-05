# اتوماسیون سادهٔ Codex و Antigravity

وضعیت: `BOUNDED_CYCLE_HARDENED` — ۲۰۲۶-۰۹-۰۵

مسیر محدودِ Gemini Worker → Codex Verifier مستقل → اعمال و تست → commit آزمایش شد.
این اثباتِ یک چرخهٔ توسعه است، نه تکمیل همهٔ Stageها یا daemon دائمی.
شرح دقیق و حدود شاهد در [گزارش چرخه](reviews/AUTOMATION_PROPOSAL_LIVE_20260905.md) است.
gateway پیچیدهٔ قبلی همچنان غیرفعال و نصب‌نشده باقی می‌ماند.

## سناریوی عادی

مثلاً باید رفتار خطای یک تابع در `P1-00b` مستند و با تست موجود تطبیق داده شود:

1. Codex وابستگی Stage و وضعیت واقعی Git را بررسی و یک زیرکار محدود تعیین می‌کند.
   فایل‌های مرتبط، رفتار غیرقابل‌تغییر، خروجی لازم و معیارهای پذیرش داخل Assignment
   می‌آیند؛ لازم نیست Gemini کل ROADMAP یا تمام پروژه را بخواند.
2. Codex با runner یک نشست تازهٔ Gemini می‌سازد. مدل فقط متن همان فایل‌ها را می‌گیرد
   و متن کامل فایل‌های پیشنهادی را برمی‌گرداند؛ shell، Git و فایل‌نویسی به مدل واگذار نمی‌شوند.
3. runner شکل پاسخ، پوشش اعلام‌شدهٔ نیازمندی‌ها، مسیرها، hash نسخهٔ اولیه، شناسهٔ نشست،
   مدل و نبود تلاش برای ابزار را می‌سنجد. `PROPOSAL_READY` به معنی «پیشنهاد قابل بررسی»
   است، نه «تست‌شده»، «درست» یا «پذیرفته‌شده».
4. پیش از اعمال تغییر، یک Codex Verifier مستقل، پیشنهاد دقیق و فایل‌های اصلی را
   بررسی می‌کند؛ snapshot و hash نتیجه باید با `task.json` و source/HEAD فعلی منطبق
   باشند. این review ایستا است، نه اجرای تست. این همان مسیر آزموده‌شده است و ارسال
   دوبارهٔ draft یا feedback به Google را لازم ندارد. اگر ارسال چنین بسته‌ای صریحاً
   مجاز باشد، نقش اختیاری `verifier` در runner نشست تازهٔ Gemini می‌سازد و تطبیق را
   خودکار انجام می‌دهد؛ این گزینه هنوز آزمایش زنده نشده است.
5. Codex diff را می‌خواند، با `apply_patch` اعمال و تست‌های مشخص را در محیط غیرعملیاتی
   اجرا می‌کند. فرمان تست از Assignment معتبر می‌آید، نه فرمان دلخواه داخل پاسخ مدل.
   شکست واقعی به اجرای اصلاحی تازه با source و hash تازه برمی‌گردد؛ پذیرش به تست سبز
   و review نهایی Codex روی کل تغییر وابسته است، نه صرفاً نظر Gemini.
6. Codex تغییر نهایی را review و فقط فایل‌های همان کار را commit می‌کند. برای پذیرش
   Stage، review مستقل Codex روی SHA دقیق و closeout مصوب همچنان لازم است؛ این runner
   اجازهٔ تغییر `COMPLETE`، ارتقای `main` یا آزادکردن dependency را صادر نمی‌کند.

Codex تنها اعمال‌کنندهٔ تغییرات در این حالت است. Gemini فقط پیشنهاد می‌دهد و
Verifier مستقل حق اعمال یا پذیرش کار خودش را ندارد؛ لایهٔ privileged gateway،
installer و session-binding خصوصی لازم نیست.
این تغییرِ نقش مجوزی برای اجرای مسیر قدیمی، حذف داده یا تغییر عملیات محصول نیست.

## قرارداد قابل اجرا

ابزار: `scripts/two_site_proposal_runner.py`؛ قالب مشترک:
[`templates/GEMINI_TASK_PROMPT.md`](templates/GEMINI_TASK_PROMPT.md).
همهٔ فهرست‌ها غیرخالی‌اند؛ شمارهٔ نیازمندی‌ها از ۱ و مطابق ترتیب فهرست است.

```json
{
  "task_id": "P1-00B-BOUNDED-001",
  "goal": "شرح دقیق یک تغییر با حفظ رفتار فعلی",
  "context_files": ["path/to/existing.py"],
  "allowed_files": ["tests/test_example.py"],
  "requirements": ["حالت موفق", "حالت شکست و رفتار ثابت مورد انتظار"],
  "test_selectors": ["tests/test_example.py"]
}
```

این فقط نمونهٔ ساختار است، نه Assignment آمادهٔ اجرا. Codex قبل از هر فراخوانی مسیرهای
واقعی و معیارهای معنادار را جایگزین می‌کند. Assignment اصلی Stage، scope و مجوزها
همچنان مرجع‌اند؛ task ورودی runner فقط بستهٔ کاری کوچکِ مشتق‌شده از آن است.

```bash
python3 scripts/two_site_proposal_runner.py worker \
  --task .local/two-site-refactor/task.json --run-id unique-worker-001
```

در مسیر آزموده‌شده، Codex پیشنهاد را مستقیم به Verifier مستقلِ فقط‌خواندنی می‌دهد.
فرمان زیر اختیاری است و فقط با مجوز ارسال proposal به Google اجرا می‌شود:

```bash
python3 scripts/two_site_proposal_runner.py verifier \
  --task .local/two-site-refactor/task.json --run-id unique-verifier-001 \
  --proposal .local/two-site-refactor/proposals/unique-worker-001/result.json
```

`--cli` مسیر executable موجود را در worktree جدا مشخص می‌کند؛ پیش‌فرض
ابتدا `.local/tools/agy` همان worktree و سپس alias مشترک checkout اصلی است.
پوشهٔ محلی باینری نام نسخه ندارد (`.local/tools/antigravity-cli/antigravity`) تا
نام مسیر با auto-update کاذب نشود؛ alias ثابت `.local/tools/agy` به آن اشاره می‌کند.
Runner در هر اجرا نسخهٔ واقعی `1.1.27` و SHA-256 بازبینی‌شدهٔ باینری را پیش و پس
از فراخوانی کنترل و در `status.json` ثبت می‌کند. هر تغییر نسخه، hash یا هویت حین
اجرا fail-closed است و تا بازبینی و pin تازه اجازهٔ proposal نمی‌دهد. پروفایل قدیمی
`1.1.26` متعلق به adapter متوقف‌شده است و مسیر اجرایی فعلی نیست.

مدل در کد pin شده: `gemini-3.8-flash-high` با `high`.
timeout پیش‌فرض ۳۰۰ ثانیه است؛ context هر زیرکار حداکثر ۱۶۰ کیلوبایت.
کد، task و feedback باید فاقد کلید و دادهٔ عملیاتی باشند. فیلتر token، جایگزین
بررسی محتوا توسط Codex نیست. ارسال به سرویس فقط در scope مجاز و با مجوز ابزار محیط است.
در اصلاح، فقط در scope مجازِ ارسال، `--feedback` شاهد خطا و `--proposal` پیشنهاد قبلی
را به Worker می‌دهند. مجوز source-only به‌طور خودکار مجوز این دو ورودی نیست؛ اگر
محیط ارسالشان را رد کرد، آن محتوا را زیر نام ورودی دیگری ارسال نکنید. بازبینی محلی
Codex مجاز می‌تواند ادامه یابد؛ مسیر مسدود گزارش می‌شود. نتیجهٔ تستِ نسخهٔ قبلی
نباید به‌عنوان تستِ تغییر تازه معرفی شود.

## خروجی ناقص یا قطع Worker

- اگر مدل فقط توضیح یا برنامه بدهد، خروجی به‌عنوان پیاده‌سازی رد می‌شود.
- اگر تست شکست بخورد، Codex شاهد شکست و الزام نقض‌شده را خودکار به Gemini می‌دهد.
  اجرای اصلاحی باید شناسهٔ تازه داشته باشد و پیشنهاد قبلی را نیز دریافت کند.
- حداکثر دو اصلاح خودکار برای همان زیرکار انجام می‌شود. تکرار finding یکسان بدون
  تغییر مؤثر یا نبود پیشرفت یعنی بازنگری اندازهٔ کار/زمینه/روش، نه retry بی‌پایان.
  این حد به معنی تأیید خروجی ناقص یا الزام درخواست تأیید معمول از مالک نیست.
- timeout فرایند فرزند را متوقف و وضعیت را `BLOCKED` می‌کند. اگر خود Codex/میزبان
  قطع شد، در بازیابی وضعیت `RUNNING` دلیل زنده‌بودن یا موفقیت نیست: ابتدا فرایند و
  خروجی واقعی بررسی می‌شود؛ همان run-id هرگز بازنویسی یا کورکورانه تکرار نمی‌شود.
- مجوز ردشده دور زده نمی‌شود. مانع دسترسی، ایراد کد و نقص پاسخ مدل جدا گزارش می‌شوند.
- سقف مشترک دو مگابایتی `stdout` و `stderr` هنگام خواندن stream اعمال می‌شود؛ Runner
  خروجی نامحدود را با `communicate()` در حافظه جمع نمی‌کند. با اولین بایتِ بیشتر از
  سقف، process group متوقف می‌شود و مجموع فایل‌های evidence نیز از سقف عبور نمی‌کند.

## محل اجرا و شواهد

یک integration worktree ثبت‌شده روی `refactor/two-site-architecture` کافی است؛
برای مدل پیشنهاددهنده worktree جدا ساخته نمی‌شود. اگر `main` مشغول کار دیگری است،
آن checkout یا تغییراتش جابه‌جا/تمیز نمی‌شوند. Codex پیش از اعمال و commit مالکیت و
scope را بررسی می‌کند؛ دو Coordinator هم‌زمان حق نوشتن روی integration ندارند.

هر run در `.local/two-site-refactor/proposals/<run-id>/` شامل task/source snapshot،
prompt، stream، status، نتیجه و diff است. hash فایل و شناسهٔ نشست برای پیگیری‌اند،
نه امضا یا مجوز پذیرش. نگهداری پیش‌فرض ۷ روز در `retain_until` ثبت می‌شود.
در آغاز و پایان چرخه، Codex runهای terminal و منقضی را بررسی و پس از حفظ خلاصهٔ لازم
در review bundle، فقط همان مسیرهای دقیق را حذف می‌کند؛ run فعال یا مدرکِ finding باز
بدون تعیین تکلیف حذف نمی‌شود. تمدید نیازمند علت و موعد جدید است. این runner سرویس
پاکسازی زمان‌بندی‌شده نصب نمی‌کند؛ deadline به‌تنهایی ادعای انجام حذف نیست.

## اجرا بدون گفتگوی باز

`scripts/two_site_codex_autopilot.py` یک Supervisor محدود برای اجرای پشت‌سرهم
`codex exec` است. مدل Coordinator روی `gpt-6-astra` با reasoning=`high` و CLI روی
نسخه/SHA بازبینی‌شده pin شده است؛ authentication پیش از هر چرخه بررسی می‌شود و هر
invocation نشست تازه و ephemeral دارد. هر چرخه فقط پس از یک commit واقعی، ancestry
صحیح، worktree تمیز، ثبات refهای `main`/`origin/main`/candidate و ثبات فایل‌های کنترلی
به چرخهٔ بعد می‌رود. نبود پیشرفت، dirty tree، بازنویسی history، تغییر ref محافظت‌شده،
خروجی نامعتبر یا gate بیرونی چرخه را متوقف می‌کند؛ مدل نمی‌تواند با نوشتن `CONTINUE`
این کنترل‌های محلی را دور بزند.

راه‌انداز محلی:

```bash
scripts/two_site_autopilot_tmux.sh start
scripts/two_site_autopilot_tmux.sh status
scripts/two_site_autopilot_tmux.sh logs
```

پس از `start`، بازبودن صفحه یا گفتگوی Codex لازم نیست؛ session با نام
`two-site-autopilot` تا وقتی میزبان و `tmux` زنده‌اند ادامه می‌یابد. این سرویس دائمی
پس از reboot نیست؛ وضعیت canonical در Git و شواهد محدود زیر
`.local/two-site-refactor/autopilot/` بازیابی می‌شود. `stop` پس از چرخهٔ جاری و
`stop-now` با signal و توقف process group خاتمه می‌دهد.

خروجی هر Codex حین stream به ۱۶ مگابایت محدود، timeout هر چرخه سه ساعت و تعداد کل
چرخه‌ها ۲۰۰ است. فقط ۲۰ run عادی آخر حفظ می‌شود؛ شاهد `BLOCKED` برای بررسی باقی
می‌ماند. Supervisor هیچ gate تولید/DNS/Writer/secret را مجاز نمی‌کند و gateway قدیمی
را فعال نمی‌کند. مسیر مستقلِ Codex، proposal Gemini را review، تغییر پذیرفته‌شده را
اعمال، تست و commit می‌کند؛ شکست معمولی حداکثر دو retry دارد و تغییر مبهم retry نمی‌شود.

## مرز پذیرش

تست محلی ابزار:

```bash
APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest tests.test_two_site_proposal_runner -v
```

این تست‌ها CLI را mock می‌کنند و به Google وصل نمی‌شوند. تست Supervisor نیز آفلاین
است و قرارداد state، ثبات ref/control، no-progress، retention و command امن را می‌سنجد:

```bash
APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest tests.test_two_site_codex_autopilot -v
```

شاهد جداگانهٔ زنده، Worker
واقعی، تغییرِ اعمال‌شده، تست، review مستقل و commit را ثبت می‌کند. نه این شاهد و نه
تست‌ها به معنی پذیرش Stage یا آمادگی تولید نیستند. Web/Bot، deploy، DNS و انتقال
انسانی Web Writer با این تغییر دست‌نخورده می‌مانند.
