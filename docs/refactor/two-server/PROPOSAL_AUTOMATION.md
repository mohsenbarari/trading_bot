# اتوماسیون سادهٔ Codex و Antigravity

وضعیت: `GEMINI_FIRST_REVIEW_REQUIRED` — ۲۰۲۶-۰۹-۰۵

مسیر جاری Gemini Worker → Gemini Reviewer مستقل → Codex final gate → اعمال/تست/commit
است. چرخهٔ زندهٔ قبلی Worker → Codex را اثبات کرد؛ الزام review اول توسط Gemini تصمیم
جدید مالک و جایگزین مسیر قبلی است و تا اجرای smoke زندهٔ خودش ادعای runtime acceptance
ندارد. شرح شاهد تاریخی در [گزارش چرخه](reviews/AUTOMATION_PROPOSAL_LIVE_20260905.md) است.
gateway پیچیدهٔ قبلی همچنان غیرفعال و نصب‌نشده باقی می‌ماند.

## سناریوی عادی

مثلاً باید رفتار خطای یک تابع در `P1-00b` مستند و با تست موجود تطبیق داده شود:

1. Codex وابستگی Stage و وضعیت واقعی Git را بررسی و یک زیرکار محدود تعیین می‌کند.
   فایل‌های مرتبط، رفتار غیرقابل‌تغییر، خروجی لازم و معیارهای پذیرش داخل Assignment
   می‌آیند؛ لازم نیست Gemini کل ROADMAP یا تمام پروژه را بخواند.
2. Codex با runner یک نشست تازهٔ Gemini Worker می‌سازد. تمام کدنویسی، تست و تغییر
   مستندات پروژه در خروجی کامل همین Worker انجام می‌شود؛ مدل متن نهایی فایل‌ها را
   برمی‌گرداند، اما shell، Git و نوشتن مستقیم روی worktree ندارد.
3. runner شکل پاسخ، پوشش اعلام‌شدهٔ نیازمندی‌ها، مسیرها، hash نسخهٔ اولیه، شناسهٔ نشست،
   مدل و نبود تلاش برای ابزار را می‌سنجد. `PROPOSAL_READY` به معنی «پیشنهاد قابل بررسی»
   است، نه «تست‌شده»، «درست» یا «پذیرفته‌شده».
4. پیش از Codex، runner یک نشست تازه و مستقل Gemini Reviewer می‌سازد. Reviewer همان
   task/snapshot و proposal دقیق را می‌گیرد، حق تولید تغییر ندارد و فقط `VERIFIED`،
   `CHANGES_REQUIRED` یا `BLOCKED` می‌دهد. Runner تفاوت conversation ID، وضعیت Worker
   و SHA دقیق proposal را کنترل می‌کند. ارتباط مستقیمی میان دو Gemini وجود ندارد.
5. اگر Gemini رد کند، Codex یافته‌ها را در feedback چهارفیلدیِ متصل به proposal SHA
   قرار می‌دهد و آن را همراه proposal ردشده به Worker تازه می‌فرستد؛ سپس Reviewer
   تازه از ابتدا review می‌کند. هیچ گزارش دستی توسط مالک جابه‌جا نمی‌شود.
6. پس از `VERIFIED`، Codex بازبین نهایی است: بستهٔ task/source/proposal/review/hash را
   بررسی می‌کند و خودش کد، تست یا مستندات پروژه را نمی‌نویسد. رد Codex نیز با همان
   قرارداد feedback به Worker و سپس Gemini Reviewer تازه بازمی‌گردد.
7. فقط پس از عبور review ایستا، Codex bytes دقیق proposal را اعمال، hash نهایی را
   کنترل و تست‌های Assignment را اجرا می‌کند. شکست تست با evidence واقعی و پس از
   بازگرداندن baseline تمیز وارد چرخهٔ اصلاح می‌شود؛ Codex failure را شخصاً fix نمی‌کند.
8. پس از سبزشدن تست و review کامل diff، Codex فقط فایل‌های همان کار را commit می‌کند. برای پذیرش
   Stage، review مستقل Codex روی SHA دقیق و closeout مصوب همچنان لازم است؛ این runner
   اجازهٔ تغییر `COMPLETE`، ارتقای `main` یا آزادکردن dependency را صادر نمی‌کند.

Gemini Worker نویسندهٔ کامل کد است؛ «proposal» فقط به این معناست که تا عبور از دو
review اجازهٔ نوشتن مستقیم ندارد، نه اینکه صرفاً ایده یا plan بدهد. Codex تنها
اعمال‌کننده و مالک Git است. Gemini Reviewer حق تغییر یا پذیرش نهایی ندارد؛ لایهٔ privileged gateway،
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

review اول زیر اجباری است و در نشست تازهٔ Gemini اجرا می‌شود:

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

تمام ورودی‌های فایل‌محور مدل ــ task، context، feedback، proposal، snapshot و
template ــ با خواندن حداکثر `limit+1` بایت از file descriptor بازشده با
`O_NOFOLLOW` دریافت می‌شوند؛ بنابراین سنجش اندازه بعد از `read_bytes()` نامحدود
وجود ندارد. مسیر باید داخل root باشد، فایل regular و دارای دقیقاً یک hardlink
(`st_nlink == 1`) باشد و inode/اندازه/timestamp پیش و پس از خواندن ثابت بماند.
این قاعده عمداً hardlink حتی میان دو مسیر داخل پروژه را نیز رد می‌کند تا هیچ alias
خارج پروژه قابل تشخیص‌نبودن نباشد.

مدل در کد pin شده: `gemini-3.8-flash-high` با `high`.
timeout پیش‌فرض ۳۰۰ ثانیه است؛ context هر زیرکار حداکثر ۱۶۰ کیلوبایت.
کد، task و feedback باید فاقد کلید و دادهٔ عملیاتی باشند. فیلتر token، جایگزین
بررسی محتوا توسط Codex نیست. مالک ارسال bounded source/proposal/review/finding لازم
برای ریفکتور دوسایته به Gemini و Codex را تأیید کرده است؛ secret، دادهٔ کاربر و دادهٔ
عملیاتی همچنان خارج scope هستند. feedback اصلاحی دقیقاً این قرارداد را دارد و hash
باید proposal ردشده باشد؛ کلید اضافه یا گزارش آزاد رد می‌شود:

```json
{
  "reviewer": "gemini",
  "reviewed_proposal_sha256": "<64-hex>",
  "findings": ["path/function: triggering case and expected behavior"],
  "test_evidence": []
}
```

برای رد نهایی مقدار `reviewer` برابر `codex` است. نتیجهٔ تست نسخهٔ قبلی به‌عنوان
شاهد تغییر تازه استفاده نمی‌شود.

## خروجی ناقص یا قطع Worker

- اگر مدل فقط توضیح یا برنامه بدهد، خروجی به‌عنوان پیاده‌سازی رد می‌شود.
- اگر Gemini Reviewer یا Codex رد کند یا تست شکست بخورد، Codex یافته‌ها و شاهد واقعی
  را خودکار به Worker تازه می‌دهد. اجرای اصلاحی شناسهٔ تازه و proposal قبلی را دارد
  و پس از آن همیشه یک Gemini Reviewer تازه اجرا می‌شود.
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
- یک `flock` انحصاری و non-blocking برای هر worktree از پیش از اولین اجرای CLI تا
  ثبت وضعیت نهایی نگه داشته می‌شود. اجرای دوم بدون انتظار و پیش از شروع provider با
  `BLOCKED` رد می‌شود. خود فایل lock نیز symlink/hardlink، مالک ناشناخته یا mode
  ناامن را نمی‌پذیرد و با مرگ process به‌صورت کرنلی آزاد می‌شود.

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

همین نشست Codex Supervisor بازبین نهایی و مالک apply/test/Git است؛ nested Codex
اجرا نمی‌شود. Supervisor نمی‌تواند implementation/test/project-doc را بنویسد یا
failure را شخصاً fix کند: قبل از هر اعمال، Worker و Reviewer مستقل Gemini لازم‌اند
و هر finding به چرخهٔ اصلاحی Gemini بازمی‌گردد.

خروجی commitدار Codex باید `gemini_worker_run_id`، `gemini_reviewer_run_id` و
`proposal_sha256` دقیق را برگرداند. Supervisor پیش از قبول چرخه، status/snapshot/result
هر دو نشست، تفاوت conversation ID، SHAها، تک‌commit‌بودن history و برابری کامل
path/mode/bytes commit با proposal را محلی کنترل می‌کند. نتیجهٔ بدون commit هر سه
فیلد را `null` می‌دهد؛ متن مدل به‌تنهایی نمی‌تواند این gate را پاس کند.

گزینهٔ `--approve-for-me` خود Codex درخواست‌ها را با sandbox نوع workspace-write
بازبینی می‌کند؛ CLI اجازهٔ ترکیب هم‌زمان آن با `--sandbox` را نمی‌دهد. Supervisor
عمداً فقط اولی را می‌فرستد و هیچ bypass سراسری approvals/sandbox به کار نمی‌برد.

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
را فعال نمی‌کند. Supervisor مسیر Worker → Gemini Reviewer → Codex final gate را
اجرا می‌کند؛ شکست معمولی حداکثر دو اصلاح دارد و تغییر مبهم retry نمی‌شود.
Supervisor نیز ورودی‌های schema/prompt/result/status را محدود می‌خواند، hardlinkهای
فایل‌های کنترلی را رد می‌کند و lock انحصاری خود را بدون دنبال‌کردن symlink نگه می‌دارد.

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

مجموعهٔ Runner، Supervisor، adapter قدیمی و session-binding پس از این تغییر ۹۳ تست
آفلاین سبز دارد؛ این عدد شاهد قرارداد کد است، نه smoke واقعی Gemini Reviewer.

شاهد جداگانهٔ زنده، Worker واقعی، Gemini Reviewer تازه، Codex final gate، تغییر
اعمال‌شده، تست و commit را ثبت خواهد کرد. نه شاهد تاریخی و نه تست‌ها به معنی پذیرش
Stage یا آمادگی تولید نیستند. Web/Bot، deploy، DNS و انتقال
انسانی Web Writer با این تغییر دست‌نخورده می‌مانند.
