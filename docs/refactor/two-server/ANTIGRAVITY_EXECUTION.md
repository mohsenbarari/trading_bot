# اجرای خودکار ریفکتور دوسایته با Google Antigravity

وضعیت در ۲۰۲۶-۰۹-۰۵: `SIMPLIFICATION_REQUIRED / GATEWAY_NOT_ACCEPTED / NOT_INSTALLED / STAGE_TAKEOVER_NOT_STARTED`

gateway در commit `1290009dd51e80b776357c3668564796e3fc1f1f` نتیجهٔ
`CHANGES_REQUIRED` با یک finding سطح High و دو Medium گرفته است. smoke تاریخی،
آمادگی اجرای امروز را ثابت نمی‌کند. طبق اصل «امن، آسان و دقیق» در
[`EXECUTION_GOVERNANCE.md`](EXECUTION_GOVERNANCE.md)، گسترش gateway برای بازنگری و
ساده‌سازی متوقف است. شرح و فرمان‌های زیر قرارداد طراحی موجودند و تا پذیرش و نصب
مسیر اجرایی، دستور شروع عملیات محسوب نمی‌شوند.

این سند آداپتر اجرایی Antigravity برای همان پلن، Stage graph و حاکمیت موجود است؛
پلن تازه یا منبع حقیقت موازی نیست. منابع مرجع همچنان
[`MASTER_PLAN.md`](MASTER_PLAN.md)،
[`EXECUTION_GOVERNANCE.md`](EXECUTION_GOVERNANCE.md) و
[`execution-order.yaml`](execution-order.yaml) هستند.

## نتیجهٔ مورد انتظار

یک گفت‌وگوی والد با نقش `two-site-coordinator` کل چرخه را مدیریت می‌کند. والد یک
Worker نویسنده و سپس یک Verifier مستقل را به‌صورت subagent اجرا می‌کند، شناسهٔ
گفت‌وگوهایشان را نگه می‌دارد و یافته‌ها را مستقیماً بین آن‌ها منتقل می‌کند. کاربر
دیگر گزارش Worker را به Verifier یا Coordinator کپی نمی‌کند. پذیرش نهایی همچنان با
پل headless و مستقل Codex و برای SHA دقیق انجام می‌شود.

چرخهٔ عادی کاملاً خودکار است: صدور Assignment، کدنویسی، تست، commit روی branch
Worker، verification، commit ادغام، Codex review، برگشت findingها، commit اصلاحی،
pre-review دقیق closeout، closeout commit و verify پس از commit بدون approval یا
انتقال دستی کاربر انجام می‌شوند. توقف فقط برای blocker واقعی یا action خارجی رخ
می‌دهد.

فایل‌های پروژه‌ای Antigravity:

```text
.agents/agents/two-site-coordinator/agent.md
.agents/agents/two-site-stage-worker/agent.md
.agents/agents/two-site-stage-verifier/agent.md
.agents/skills/two-site-refactor/SKILL.md
.agents/skills/two-site-refactor/resources/AUTONOMOUS_ORCHESTRATION.md
.agents/skills/two-site-refactor/resources/antigravity-state.schema.json
.agents/skills/two-site-refactor/resources/orchestrator-fence.schema.json
.agents/rules/two-site-refactor-safety.md
```

تعریف‌ها داخل ریپازیتوری هستند؛ تنظیمات سراسری `~/.gemini`، credential و permission
عمومی را تغییر نمی‌دهند. هر سه Agent مدل pin‌شدهٔ parent را با `inherit` می‌گیرند و با سیاست اجرای command برابر
`sandbox` تعریف شده‌اند. Verifier فقط برای ساخت درخواست یک‌بارمصرف gateway ابزار
`write_to_file` دارد؛ نوشتن source یا state جزو اختیار آن نیست.

## شروع در IDE

1. همین Git root را در Antigravity باز کنید و در Agent Manager فرمان `/agents` را
   پس از کامل‌شدن login اجرا کنید. مدل اصلی را `Gemini 3.8 Flash (High)` انتخاب کنید.
2. Agent اصلی `two-site-coordinator` را انتخاب کنید.
3. فقط این پیام را بفرستید:

```text
Resume the approved two-site refactor autonomously from canonical repository and local
receipt state. Continue only dependency-ready non-external work. Do not perform any
production or external mutation without its exact scoped Codex pre-action receipt.
```

Coordinator ابتدا validator، وضعیت واقعی Git، YAML، registry، Assignment و receipt
را تطبیق می‌دهد. اگر Agentها در فهرست نبودند، پروژه باید reload شود؛ تعریف‌ها نباید
برای حل discovery به مسیر global کپی شوند.

پیش از اولین write، takeover اتمیک governance اجرا می‌شود: runtime مقصد باید سبز
باشد، integration و Worker قبلی هر دو تمیز و Worker قبلی non-running باشد، Assignment
منقضی نشده و scope آن با digest تغییرناپذیر به fence متصل باشد، fence با epoch تازه ابتدا
`RECONCILING` شود، Assignment قبلی `SUPERSEDED` و Assignment جدید با platform/epoch
صادر شود، registry reconcile و در پایان fence `ACTIVE` شود. mismatch یعنی توقف؛
Cursor و Antigravity هرگز هم‌زمان Writer ندارند.

Coordinator برای ساخت و کنترل اتمیک fence فقط از
`scripts/two_site_orchestrator_fence.py` (`claim`، `activate`، `check`، `worker-check`،
`stage-advance`، `stage-open-check`، `stage-bind`، `advance` و `release`) استفاده می‌کند؛ ویرایش دستی JSON fence مجاز
نیست. Worker هنگام تغییرات مشروع فقط `worker-check` فقط‌خواندنی را اجرا می‌کند؛ پس از
commit تمیز، Coordinator با `stage-advance` و اثبات ancestry، HEAD رجیستری Stage را
اتمیک به candidate متصل می‌کند و سپس Verifier آغاز می‌شود.
پس از closeout تأییدشده و `advance` شدن integration fence، Coordinator ابتدا با
`stage-open-check` نبود پرچم خارجی و metadata ناشناخته را ثابت می‌کند؛ سپس transition
Stage پذیرفته‌شده به `IN_PROGRESS` را کامیت و fence را دوباره `advance` می‌کند. worktree
بعدی باید روی همان integration HEAD ساخته شود و `stage-bind` رکورد قبلی را محلی می‌بندد
و Assignment/Worktree همان Stage را اتمیک فعال می‌کند. argv این فرمان به ID و SHA-256
دقیق درخواست typed متصل است؛ خود fence زیر lock، درخواست، مالک path/branch مربوط به
integration، مالک Stage قبلی و تمام هویت Assignment/registry جدید را پیش از اولین write
دوباره اثبات و کلید اضافه/ناقص یا digest نامعتبر رسید را رد می‌کند. rollback نیز Assignment
و branch بعدی را پیش از هر حذف می‌سنجد و بازیابی رکورد `REMOVED` تنها با برابری کامل
رکورد پایه، delta مجاز و اثبات نبودن و ثبت‌نبودن worktree قبلی مجاز است. رسید terminal
نیز پیش از انتشار و پاک‌کردن journal همین state نهایی را زیر lock دوباره کنترل می‌کند.
Assignment دارای external access از این مسیر رد می‌شود.

## شروع اختیاری در CLI

این مسیر فقط وقتی فعال است که CLI رسمی `agy` نصب و authenticate شده باشد.
در نسخهٔ آزموده‌شدهٔ `1.1.26`، فراخوانی تازهٔ `agy agents` ممکن است قبل از تکمیل
authentication فهرست خالی بدهد و `--agent` به default برگردد. بنابراین bootstrap
توسط Codex/controller، ابتدا در نشست تعاملیِ plan انجام می‌شود: بعد از login، `/agents`
باز و Coordinator واقعی انتخاب می‌شود. شناسهٔ مشاهده‌شدهٔ نشست، ورودی resume است؛
نام مدل یا پاسخ متنیِ خود Agent به‌تنهایی اثبات انتخاب درست نیست.
شناسهٔ این نشست مستقل در `execution_coordinator` رجیستری محلی ثبت می‌شود و همان UUID
باید مقدار `--instance` قفل باشد. پیش از claim، فرمان زیر هم smoke سه نقش و هم اتصال
نشست واقعی را کنترل می‌کند؛ در ادامهٔ کار، fence فقط بررسی read-only نشست واقعی را
تکرار می‌کند و هرگز نشست فعال خودش را دوباره اجرا نمی‌کند.

```bash
/usr/bin/python3 -I scripts/validate_antigravity_two_site_adapter.py --runtime \
  --execution-conversation <VERIFIED_EXECUTION_COORDINATOR_ID>
```

```bash
agy -p "Resume the approved two-site refactor autonomously from canonical state; run no external or production mutation." \
  --conversation <VERIFIED_EXECUTION_COORDINATOR_ID> \
  --model gemini-3.8-flash-high --effort high \
  --sandbox \
  --output-format stream-json \
  --print-timeout 60m
```

اگر `agy` در `PATH` وجود نداشت، آداپتر IDE همچنان قابل استفاده است و مسیر CLI تا
نصب رسمی آن غیرفعال می‌ماند. استفاده از `--dangerously-skip-permissions` ممنوع است.

## سناریو ۱ — ادامهٔ Stage جاری

Coordinator به خلاصهٔ قدیمی گفت‌وگو اعتماد نمی‌کند. ابتدا branch و HEAD و worktree
واقعی Git، سپس state/dependencyهای YAML، registry محلی، Assignment و receipt دقیق را
می‌خواند. bootstrap و handoff محلی فقط hint هستند. اگر همهٔ شناسه‌ها سازگار باشند،
همان Stage ادامه پیدا می‌کند؛ اگر SHA یا Assignment تعارض داشته باشد، فقط همان مسیر
`BLOCKED` می‌شود و هیچ فایل کپی، reset یا حذف نمی‌شود.

فایل `.local/two-site-refactor/orchestrator-fence.json` نیز باید `ACTIVE`، متعلق به
Antigravity و از نظر integration HEAD و epoch منطبق با Assignment باشد. Assignment
قدیمی Cursor بدون epoch جدید قابل resume نیست.

Coordinator Worker را با `Workspace: inherit` و مسیر worktree ثبت‌شده اجرا می‌کند.
گزینهٔ `branch` خودکار Antigravity استفاده نمی‌شود، چون worktree ساخته‌شده باید پیش
از نوشتن در registry و Assignment ثبت شده باشد.

## سناریو ۲ — Worker کار را تحویل می‌دهد

Worker فقط در worktree و مسیرهای Assignment می‌نویسد، تست‌ها را اجرا می‌کند، candidate
را commit می‌کند و worktree تمیز، گزارش فارسی، base/head کامل، فهرست commitها،
تست‌های success/failure/rollback و evidence را تحویل می‌دهد. Coordinator شناسهٔ
گفت‌وگوی Worker را در فایل ignored زیر نگه می‌دارد:

```text
.local/two-site-refactor/antigravity-state.json
```

این فایل cache بازیابی است و حق ندارد Git، YAML، Assignment یا receipt را override
کند؛ قالب آن با `resources/antigravity-state.schema.json` ثابت است و prompt، source،
credential یا raw evidence در آن ذخیره نمی‌شود. Verifier با context کامل و به‌صورت مستقل اجرا می‌شود. اگر
`CORRECTIONS_REQUIRED` بدهد، Coordinator تمام یافته‌ها را با `send_message` به همان
Worker idle می‌فرستد؛ کاربر واسطه نیست.

## سناریو ۳ — candidate آمادهٔ پذیرش است

پس از `VERIFIED`، Coordinator فقط یک candidate را روی HEAD ثبت‌شدهٔ integration
اعمال و commit می‌کند و تست integration را اجرا می‌کند. سپس review bundle ساخته و
`scripts/two_site_codex_final_review.sh` فراخوانی می‌شود. نتیجه‌ها:

- `APPROVE`: تغییر محدود ledger/state/durable receipt در index آماده می‌شود؛ Codex
  hash دقیق patch را با receipt نوع `STAGE_CLOSEOUT_PREACTION` می‌پذیرد؛ سپس همان
  patch commit و با `--verify-commit` کنترل می‌شود. فقط بعد از این کنترل Stage
  `COMPLETE` و dependencyها آزاد می‌شوند؛
- `CHANGES_REQUIRED`: یافته‌ها مستقیم به همان Worker/Verifier loop برمی‌گردند؛
- `REJECT` یا `BLOCKED`: همان مسیر متوقف می‌شود؛
- receipt ناقص، قدیمی یا متعلق به SHA دیگر: برابر approval نیست.

## سناریو ۴ — Coordinator یا subagent قطع می‌شود

Antigravity context یک Agent idle را نگه می‌دارد؛ پیام تازه آن را دوباره فعال می‌کند.
Agent کشته‌شده قابل resume نیست. در restart والد، Coordinator state را از منابع پایدار
بازسازی و فقط وقتی conversation ID را reuse می‌کند که Stage و Assignment آن همچنان
منطبق باشند. در غیر این صورت subagent تازه با handoff کامل ساخته می‌شود. transcript
برای audit باقی می‌ماند، اما منبع state اجرایی نیست.

## سناریو ۵ — اقدام خارجی یا Production فرا می‌رسد

اجرای عادی Stageهای غیرخارجی خودکار است. provisioning، deploy، migration/restore،
secret، Object Storage واقعی، DNS، Writer، cleanup مخرب و retirement به permission
عمومی Antigravity تبدیل نمی‌شوند. مسیر متوقف می‌شود تا receipt محدود، زمان‌دار و
متصل به SHA/target از Codex وجود داشته باشد. انتقال Web Writer همچنان فقط اقدام انسانی
در Dashboard است؛ Antigravity و deploy آن را خودکار نمی‌کنند.

## سناریو ۶ — سرعت بیشتر بدون افت کیفیت

یک Worker نویسنده پیش‌فرض است. Coordinator می‌تواند هم‌زمان یک subagent read-only
برای reconnaissance Stage بعدی اجرا کند. Worker نویسندهٔ دوم فقط پس از Pairing Receipt
Codex و اثبات جدایی path/lock/rollback مجاز است. ادغام و Full Matrix همیشه ترتیبی روی
HEAD تجمیع‌شده اجرا می‌شوند. `/teamwork-preview` برای کارکرد پایه لازم نیست و فعال‌شدن
یا نبودنش قواعد کیفیت را عوض نمی‌کند.

## کنترل صحت آداپتر

```bash
/usr/bin/python3 -I scripts/validate_antigravity_two_site_adapter.py
python3 -m unittest tests.test_antigravity_two_site_adapter
python3 -m unittest tests.test_antigravity_session_binding
```

گزینهٔ `--runtime` سه نشست اختصاصی smoke را از فایل ignored
`.local/two-site-refactor/antigravity-runtime-sessions.json` می‌گیرد. این فایل فقط
`schema_version: 1.0`، `cli_sha256` و چهار شناسهٔ متمایز `coordinator`، `worker`،
`verifier` و `execution_coordinator` دارد؛ مجوز، receipt یا جایگزین بررسی زنده نیست.
سه شناسهٔ اول اختصاصی smoke و چهارمی نشست واقعی Coordinator است. شناسه‌ها از اجرای واقعی
Coordinator و `invoke_subagent` گرفته می‌شوند، نه از متن ساختگی. نشست اجرای Stage
باید از نشست smoke جدا باشد تا validator همان Agent فعال را دوباره اجرا نکند.

`scripts/two_site_antigravity_session.py` پایگاه همان نشست‌های smoke را با SQLite
`mode=ro` و `query_only` می‌خواند؛ از `immutable=1` که WAL را نادیده می‌گیرد استفاده
نمی‌کند. نام/نقش، parent، مسیر فایل، prompt دقیق، ابزارهای مجاز، sandbox و مدل واقعی
هر اجرا باید با تعریف بررسی‌شده مطابق باشند. hash باینری آزموده‌شده در کد pin شده
و schema ناشناخته یا تغییر باینری باعث توقف می‌شود. به `init.tools` اعتماد نمی‌شود،
چون CLI در آن فهرست عمومی ابزارها را می‌دهد، نه ابزارهای واقعی نقش انتخاب‌شده.

سپس هر سه نشست با `--conversation`، مدل صریح `gemini-3.8-flash-high`، `plan` و sandbox
یک prompt ثابت بدون tool/read/write دریافت می‌کنند. پاسخ موفق، شناسهٔ دقیق و نبود
tool step الزامی است. HEAD، full status، digest وضعیت محلی و اتصال نشست به تعریف
Agent قبل/بعد کنترل می‌شوند. Agent قدیمی، config/model drift، خطای قبلی نشست، auth
نامعتبر یا smoke ناموفق، مسیر را مسدود می‌کند. validator هیچ Stage را اجرا نمی‌کند.

بررسی تنظیمات به prompt اول محدود نیست: همهٔ promptهای literal و symbolic، کنترل‌های
ادامهٔ اجرا، ابزارها، تنظیمات کمکی و permission/model/effort واقعی با envelope نسخهٔ
`1.1.26` تطبیق داده می‌شوند. fixture خوانای بدون credential در
`scripts/antigravity_cli_1_1_26_profile.json` نسخه‌های مشاهده‌شدهٔ همان CLI را ثبت می‌کند؛
فقط ترتیب map پرچم‌های CLI و مسیر دقیق repository/Gemini نرمال می‌شوند. فیلد، مقدار،
دستور یا شکل ناشناخته رد می‌شود؛ ارتقای CLI نیازمند ممیزی و receipt تازه است.
هر مشاهده یک مجموعهٔ تجزیه‌ناپذیر و مختص همان نقش است: source، executor، wrapper،
metadata و حضور مسیر workspace باید با هم مطابق یک مشاهدهٔ واقعی باشند. معتبر بودن
جداگانهٔ اجزا کافی نیست؛ برای مثال source متعلق به Coordinator همراه با executor
متعلق به Worker، حتی با prompt صحیح، رد می‌شود. ترکیب‌های ندیده نیز مجاز نیستند.

سناریوی کنترل: اگر هر سه smoke سالم باشند ولی نشست اجرای Stage با Agent پیش‌فرض یا
مدل دیگری ساخته شده باشد، `claim` پیش از تغییر fence/Assignment رد می‌شود. اگر اتصال
نشست بعداً عوض شود، `activate` و `check/worker-check` و مسیرهای mutation متوقف می‌شوند.
بررسی نشست فعال به تماس Google وابسته نیست: تاریخچهٔ موفق و فقط انتهای درحال‌اجرای
بدون خطا پذیرفته می‌شود؛ تست smoke همچنان هیچ step ناقص یا ناموفق را نمی‌پذیرد.

تعریف‌ها از `model: inherit` استفاده می‌کنند؛ مدل parent در CLI صریح pin می‌شود و
مدل واقعی child از executor metadata کنترل می‌شود. `model: pro` در آزمون قبلی برای
childها به `gemini-3.1-pro-low` تبدیل شد؛ بنابراین برچسب tier کافی نیست. انتخاب 3.8
Flash High با توجه به [معرفی رسمی Google در ۲ سپتامبر ۲۰۲۶](https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/)
و بررسی واقعی تنظیمات انجام شد؛ smoke صرفاً سلامت اتصال است، نه benchmark کیفیت کد.

در CLI 1.1.26، `list_permissions` registry معتبر ندارد و `multi_replace_file_content`
بی‌اثر است؛ در allowlist نقش‌ها نیستند. همچنین `skills:` نسبی به‌علت نبود
`AgentBasePath` بارگذاری نمی‌شود. هر نقش موظف است قبل از کار Stage، Skill canonical
را صریح بخواند و در صورت شکست متوقف شود. roleها فقط ابزارهای allowlist دقیق دارند.

مالک ارسال تعریف‌ها، metadata و سورس/تست/مستندات مرتبط این ریفکتور به Google را
صریحاً تأیید کرده است؛ credentialها، secrets و دادهٔ عملیاتی خارج از این مجوزند.
آزمون‌ها فقط context اختصاصی smoke و promptهای ثابت را می‌فرستند. نشست‌های شکست‌خورده
یا superseded شواهد موقت‌اند؛ حداکثر پس از هفت روز و رفع نیاز بررسی حذف شوند. سه
نشست smoke فعال تا پایان نیاز validator حفظ و پس از پایان ریفکتور پاکسازی می‌شوند.

تغییرات خود adapter نیز با
`scripts/two_site_codex_governance_review.sh` در commit و bundle مستقل exact-SHA
پذیرفته می‌شوند و داخل scope Stage جاری قرار نمی‌گیرند.

عبور از تست محلی یا smoke، به‌تنهایی پذیرش amendment، انتقال fence، تکمیل Stage یا
مجوز عملیات تولیدی نیست؛ receipt مستقل و سپس takeover همچنان الزامی‌اند.
