# اجرای خودکار ریفکتور دوسایته با Google Antigravity

وضعیت: `STATIC_VALIDATED / RUNTIME_DISCOVERY_BLOCKED / NO_STAGE_OR_PRODUCTION_ACTION`

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
.agents/agents/two-site-coordinator.md
.agents/agents/two-site-stage-worker.md
.agents/agents/two-site-stage-verifier.md
.agents/skills/two-site-refactor/SKILL.md
.agents/skills/two-site-refactor/resources/AUTONOMOUS_ORCHESTRATION.md
.agents/skills/two-site-refactor/resources/antigravity-state.schema.json
.agents/skills/two-site-refactor/resources/orchestrator-fence.schema.json
.agents/rules/two-site-refactor-safety.md
```

تعریف‌ها داخل ریپازیتوری هستند؛ تنظیمات سراسری `~/.gemini`، credential و permission
عمومی را تغییر نمی‌دهند. هر سه Agent با مدل `pro` و سیاست اجرای command برابر
`sandbox` تعریف شده‌اند. Verifier ابزار ویرایش فایل ندارد.

## شروع در IDE

1. همین Git root را در Antigravity باز کنید و در Agent Manager فرمان `/agents` را
   اجرا کنید.
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
`stage-advance`، `stage-bind`، `advance` و `release`) استفاده می‌کند؛ ویرایش دستی JSON fence مجاز
نیست. Worker هنگام تغییرات مشروع فقط `worker-check` فقط‌خواندنی را اجرا می‌کند؛ پس از
commit تمیز، Coordinator با `stage-advance` و اثبات ancestry، HEAD رجیستری Stage را
اتمیک به candidate متصل می‌کند و سپس Verifier آغاز می‌شود.
پس از closeout تأییدشده و `advance` شدن integration fence، Coordinator با `stage-bind`
ابتدا Stage بعدی dependency-ready و non-external را قطعی انتخاب، transition آن به
`IN_PROGRESS` را تحت standing authorization کامیت و fence را دوباره `advance` می‌کند؛
سپس رکورد قبلی را محلی می‌بندد و Assignment/Worktree همان Stage را اتمیک فعال می‌کند.
Assignment دارای external access از این مسیر رد می‌شود.

## شروع اختیاری در CLI

این مسیر فقط وقتی فعال است که CLI رسمی `agy` نصب و authenticate شده باشد:

```bash
agy agents
agy -p "Resume the approved two-site refactor autonomously from canonical state; run no external or production mutation." \
  --agent two-site-coordinator \
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
python3 scripts/validate_antigravity_two_site_adapter.py
python3 -m unittest tests.test_antigravity_two_site_adapter
```

گزینهٔ `--runtime` علاوه بر ساختار فایل، discovery توسط `agy agents` را بررسی می‌کند
و سپس خود `two-site-coordinator` را در `plan` و sandbox با یک prompt بدون tool/mutation
اجرا می‌کند؛ HEAD، full status و digest وضعیت orchestration محلی قبل/بعد باید
byte-identical و پاسخ JSON دقیق باشد.
نبود CLI، discovery،
authentication یا smoke واقعی به‌طور fail-closed گزارش می‌شود. validator هیچ Stage
را اجرا نمی‌کند. تغییرات خود adapter نیز با
`scripts/two_site_codex_governance_review.sh` در commit و bundle مستقل exact-SHA
پذیرفته می‌شوند و داخل scope Stage جاری قرار نمی‌گیرند.

در آزمون جاری CLI رسمی اجرا شد، `agy agents` خروجی discovery نداد و اجرای مستقیم
Coordinator نیز نیاز به authentication تعاملی را گزارش کرد. تا احراز هویت، discovery
هر سه Agent و smoke بدون write موفق نشود، هیچ takeover یا اجرای Stage به Antigravity
واگذار نمی‌شود.
