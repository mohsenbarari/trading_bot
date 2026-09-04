# حاکمیت اجرای ریفکتور دو سایته

وضعیت: `ACTIVE`

منبع ترتیب: [`execution-order.yaml`](execution-order.yaml)

این سند مرجع یکتای نقش‌ها، اختیار تأیید، موازی‌سازی، branch/worktree و تحویل
بین پلتفرم اجرایی و Codex است. Cursor و Google Antigravity دو آداپتر مجاز همین
قراردادند و هیچ‌کدام Stage state یا پلن موازی ندارند. جزئیات معماری و معیار فنی
هر Stage همچنان در
[`MASTER_PLAN.md`](MASTER_PLAN.md) و Task Card همان Stage قرار دارد.

## تفویض اختیار

مالک پروژه در ۲۰۲۶-۰۹-۰۳ مدیریت اجرای این پلن و تأیید نهایی تغییرات را به
`Codex Final Reviewer` تفویض کرد. پس از این تفویض:

- شروع و پذیرش Stage به تأیید مرحله‌ای کاربر وابسته نیست؛
- Cursor، Antigravity یا Coordinator حق تأیید خروجی خود را ندارد؛
- فقط receipt نهایی Codex می‌تواند Stage را `COMPLETE` کند؛
- تصمیم فنی باز به Codex ارجاع می‌شود؛ رفتار موجود تا تصمیم نهایی حفظ می‌شود؛
- کاربر همچنان می‌تواند scope یا نیازمندی کسب‌وکار را تغییر دهد، اما در زنجیرهٔ
  اجرایی هر Stage نقش approval اجباری ندارد.

تفویض اختیار، invariant انسانی انتقال Web Writer را تغییر نمی‌دهد. drain، تغییر
DNS و فعال‌سازی Writer همچنان در Dashboard به‌صورت اقدام صریح عامل انسانی انجام
می‌شود؛ این «عملیات محصول» است، نه «تأیید تغییر کد».

## نقش‌ها

### Execution Coordinator (Cursor یا Antigravity)

- تنها مالک integration branch، dependency graph و صف ادغام است.
- در حالت خودکار، همان Agent والد و بلندعمر پلتفرم انتخابی است و Worker/Verifier را به‌شکل
  sub-agent اجرا می‌کند؛ این نقش در Chat جداگانه‌ای که به انتقال دستی نیاز داشته باشد
  اجرا نمی‌شود.
- برای هر Worker یک Work Assignment با Stage، SHA، scope، lock و deadline می‌سازد.
- Stage استاندارد و غیرعملیاتی را پس از `COMPLETE` بودن dependencyها شروع می‌کند.
- خروجی Worker را بررسی مقدماتی می‌کند، اما آن را `COMPLETE` اعلام نمی‌کند.
- هیچ Stage وابسته‌ای را پیش از receipt نهایی Codex آغاز نمی‌کند.

### Execution Worker

- در هر نوبت دقیقاً یک Stage یا یک زیرکار صریحاً واگذارشده را اجرا می‌کند.
- فقط در branch/worktree و مسیرهای مجاز Assignment می‌نویسد.
- success، failure و rollback/recovery را آزمایش و review bundle تولید می‌کند.
- حق ادغام در integration branch، تغییر ledger مرکزی یا تأیید کار خود را ندارد.

### Codex Final Reviewer

- مرجع نهایی scope، معماری، کیفیت، ایمنی و پذیرش است.
- diff دقیق commit، dependency receipt، تست‌ها، evidence و rollback را مستقل
  بررسی می‌کند.
- verdict یکی از `APPROVE`, `CHANGES_REQUIRED`, `REJECT` یا `BLOCKED` است.
- commit تجمیع‌شده روی integration branch را بررسی می‌کند؛ فقط verdict برابر
  `APPROVE` اجازهٔ تغییر Stage به `COMPLETE` و شروع dependency بعدی را می‌دهد.
- پیش از هر اقدام خارجی/تولیدی/مخرب، receipt محدود و زمان‌دار همان اقدام را صادر
  یا رد می‌کند.

## چرخهٔ Stage

```text
PROPOSED → READY → IN_PROGRESS → COMPLETE_CANDIDATE
          ↘ BLOCKED/FAILED       ↓
                        FINAL_REVIEW
                     ↙       ↓       ↘
         CHANGES_REQUIRED  COMPLETE  REJECTED
```

`READY` یعنی dependencyها و Assignment معتبرند. `COMPLETE_CANDIDATE` فقط ادعای
Worker است. Coordinator ابتدا candidate را به‌صورت ترتیبی روی integration branch
اعمال و تست می‌کند؛ سپس Codex همان integration commit دقیق را review می‌کند.
receipt نهایی Codex ابتدا فقط implementation commit دقیق را می‌پذیرد و به‌تنهایی
dependencyها را آزاد نمی‌کند. Coordinator سپس تغییر مکانیکی ثبت وضعیت را در index
آماده می‌کند و پیش از commit، آن staged diff را با
`scripts/two_site_codex_closeout_review.sh` به Codex می‌دهد. receipt دوم باید به
implementation SHA، Stage/Assignment، فهرست مسیرهای محدود و SHA-256 دقیق patch مقید
باشد. فقط پس از commit شدن عین همان patch و موفقیت حالت `--verify-commit`، Stage برای
dependencyها `COMPLETE` محسوب می‌شود.

هر commit پس از review implementation، receipt را باطل می‌کند؛ تنها استثنا یک
closeout child است که parent آن دقیقاً implementation SHA پذیرفته‌شده باشد، صرفاً
`execution-order.yaml`، `CHANGE_LEDGER.md` و نسخهٔ durable همان receipt را تغییر دهد،
پیشاپیش receipt نوع `STAGE_CLOSEOUT_PREACTION` برای hash دقیق patch داشته باشد و
بعد از commit نیز به‌صورت ماشینی verify شود. closeout حق تغییر source، test، معماری،
scope، dependency یا evidence محتوایی را ندارد. هر اختلاف path/hash/parent یا فایل
اضافی، هر دو receipt را برای آزادسازی dependency نامعتبر می‌کند و review تازه لازم
دارد.

## سیاست موازی‌سازی

حالت پیش‌فرض یک Worker نویسنده است. Coordinator می‌تواند Worker دوم را فقط با
`PAIRING_APPROVED` صادرشده توسط Codex فعال کند و مجموع Workerهای نویسنده هرگز از
دو بیشتر نمی‌شود.

Pair فقط وقتی مجاز است که:

1. dependencyهای هر دو کار `COMPLETE` باشند؛
2. base SHA هر دو دقیق و ثبت‌شده باشد؛
3. allowed pathها و resource lockها هم‌پوشانی نداشته باشند؛
4. هیچ‌کدام integration branch، ledger مرکزی، migration sequence، contract
   مشترک، Compose/config مشترک یا محیط خارجی یکسان را تغییر ندهد؛
5. تست مستقل و integration test پس از ادغام برای هر دو تعریف شده باشد؛
6. rollback یکی باعث نامعتبرشدن دیگری نشود.

اگر شرطی نامعلوم باشد، اجرای نویسنده ترتیبی است. Worker دوم بدون Pairing Receipt
فقط می‌تواند روی checkout ثابت، ممیزی read-only یا آماده‌سازی گزارش انجام دهد.

## Lockهای انحصاری

این resourceها هم‌زمان فقط یک مالک دارند:

```text
integration-branch
change-ledger
database-migrations
domain-event-contracts
runtime-compose-and-config
ansible-and-release-control
writer-dns-state-machine
shared-test-fixtures
production-environment
iran-object-storage-mutation
```

Assignment می‌تواند lock جزئی بیشتری تعریف کند. نبود lock در Assignment به معنی
آزادبودن resource مشترک نیست؛ Coordinator باید آن را `UNKNOWN` و اجرای موازی را
مسدود کند.

## Branch و worktree

- integration branch یکتا: `refactor/two-site-architecture`
- branch Worker: `refactor/stage/<STAGE_ID>-<slug>`
- یک integration worktree canonical و حداکثر دو stage worktree موقت مجاز است.
- هر worktree باید در registry محلی ignored دارای `stage_id`, `owner_role`,
  `base_sha`, `branch`, `created_at`, `expires_at`, `locks` و `status` باشد.
- stage worktree حداکثر ۲۴ ساعت پس از ادغام/رد حذف می‌شود؛ expiry پیش‌فرض Assignment
  هفت روز است و تمدید فقط با ثبت دلیل ممکن است.
- clone، branch یا worktree ثبت‌نشده، دائمی یا مشترک بین دو Worker ممنوع است.
- Worker مستقیم به integration branch یا `main` push/merge نمی‌کند. Coordinator
  candidateها را یکی‌یکی روی integration branch اعمال می‌کند؛ promotion از
  integration branch به `main` فقط در barrier مصوب و با receipt جداگانهٔ Codex
  برای exact head/base مجاز است.

## Work Assignment اجباری

هر Assignment حداقل این موارد را دارد:

```yaml
assignment_id: <unique-id>
stage_id: <stage-id>
role: WORKER
orchestrator: <CURSOR|ANTIGRAVITY>
orchestration_epoch: <positive-integer>
supersedes: null
base_sha: <full-sha>
branch: <temporary-branch>
allowed_paths: []
forbidden_paths: []
locks: []
parallel_peer: null
deliverables: []
tests: []
failure_tests: []
rollback_test: null
external_access: NONE
expires_at: <utc>
```

تغییر scope، base، lock یا peer پس از شروع Assignment را باطل و re-issue را
الزامی می‌کند.

## Fence و تحویل مالکیت بین Cursor و Antigravity

در هر لحظه فقط یک پلتفرم می‌تواند Execution Coordinator فعال باشد. مالکیت runtime
در فایل ignored و اتمیک `.local/two-site-refactor/orchestrator-fence.json` با schema
`orchestrator-fence.schema.json` ثبت می‌شود. این fence «lease مربوط به Web Writer»
نیست، expiry یا auto-failover محصول ندارد و هیچ نقش سروری را عوض نمی‌کند؛ فقط مانع
نوشتن هم‌زمان دو ابزار توسعه روی Git/worktree می‌شود.

عملیات `claim`، `activate`، `check`، `worker-check`، `stage-advance`،
`stage-open-check`، `stage-bind`، `advance` و `release` با
`scripts/two_site_orchestrator_fence.py` انجام می‌شود؛ Agent حق ندارد JSON fence را
با ویرایش آزاد یا بدون file lock بسازد.

هر fence یک `epoch` صعودی، platform، شناسهٔ instance، integration HEAD، Assignment
فعال و SHA-256 canonical از فیلدهای تغییرناپذیر قرارداد Assignment دارد. هر Assignment
جدید باید platform و همان epoch را داشته و scope/path/locks/tests/external-access/expiry
آن با digest fence یکسان بماند. `base_sha` جایگزین باید دقیقاً HEAD ثبت‌شدهٔ worktree
باشد. Coordinator
و Worker پیش از هر mutation phase، `git add/commit`، integration یا تغییر registry باید
`status=ACTIVE`، branch/head و تطابق epoch/instance را دوباره بخوانند. mismatch یا
فایل مفقود برابر `BLOCKED` است؛ Agent قدیمی با Assignment epoch قبلی حق ادامه ندارد.
بعد از هر integration/closeout commit، مالک فعلی فقط با `advance` و اثبات direct-child
و worktree تمیز، fence و registry را زیر همان lock جلو می‌برد؛ تا پایان transaction
write بعدی مسدود است. تحویل عادی ابتدا `release` و سپس `claim` پلتفرم بعدی است.

`worker-check` هیچ فایل یا registry را تغییر نمی‌دهد و هنگام dirty بودن مشروع candidate
فقط ownership، scope، expiry، branch و ancestry را می‌سنجد. پس از commit تمیز Worker،
Coordinator باید `stage-advance` را اجرا کند؛ این فرمان descendant بودن SHA جدید نسبت
به HEAD ثبت‌شده را اثبات و registry را اتمیک به `COMPLETE_CANDIDATE` می‌برد. Verifier
فقط بعد از این reconciliation شروع می‌شود.

پس از closeout commit تأییدشده و `advance` integration، Coordinator Assignment و
Stage بعدی dependency-ready/non-external را به‌صورت قطعی انتخاب می‌کند. پیش از هر
تغییر، `stage-open-check` باید روی state برابر `PROPOSED`، تمام dependencyها و مجموعهٔ
بستهٔ metadata موفق شود؛ هر metadata ناشناخته یا هر پرچم `production`، `destructive`،
`dns_change`، `writer_change` یا `secret_change` ادامهٔ خودکار را مسدود می‌کند. سپس فقط
transition آن به `IN_PROGRESS` تحت standing start authorization کامیت و fence `advance`
می‌کند. سپس Assignment و worktree ثبت‌شده را آماده و `stage-bind` را اجرا می‌کند. این
transaction با همان epoch، fence را موقتاً `RECONCILING`، Stage قبلی را محلی `CLOSED`
و registry/fence را به Assignment جدید bind می‌کند. فرمان خود نیز COMPLETE بودن Stage
قبلی، IN_PROGRESS بودن Stage جدید، کامل بودن dependencies و غیرproduction/
غیرخارجی بودن آن را دوباره می‌سنجد. Assignment، base و HEAD worktree بعدی باید دقیقاً
برابر integration HEAD باشند و worktree باید Git common directory یکسان داشته باشد.
recovery فقط با همان instance و پارامترها مجاز است و automatic bind فقط
`external_access: NONE` را می‌پذیرد.

takeover کاملاً خودکار ولی fail-closed است و فقط با این ترتیب انجام می‌شود:

1. runtime پلتفرم مقصد discovery و smoke read-only موفق داشته باشد؛
2. integration worktree و worktree ثبت‌شدهٔ Worker قبلی تمیز باشند؛ Worker قبلی در
   registry حالت running نداشته، HEAD/path/branch آن با registry منطبق و Assignment
   منقضی نشده باشد؛
3. فرمان `claim` زیر قفل فایل محلی fence را با epoch بعدی `RECONCILING` کند؛ فقط
   همین فرمان در این state اجازهٔ نوشتن transaction را دارد؛
4. همان transaction، digest scope قرارداد قبلی را در fence تثبیت کند، Assignment قبلی
   را بدون حذف `SUPERSEDED` و Assignment جایگزین را با `supersedes`، Stage/worktree
   موجود، head واقعی، platform و epoch جدید صادر کند؛
5. همان transaction، registry را به نقش‌های `EXECUTION_COORDINATOR/EXECUTION_WORKER`،
   platform/epoch و Assignment جدید reconcile کند؛
6. فرمان پس از تطبیق مجدد همهٔ فایل‌ها fence را `ACTIVE` کند؛ crash در میانه آن را
   `RECONCILING` نگه می‌دارد و فقط `activate` همان instance/epoch را recovery می‌کند.

اگر Agent قبلی running/unknown باشد، یکی از worktreeها dirty باشد، قرارداد/expiry/path
drift کند، discovery مقصد fail شود یا هر فایل نیمه‌به‌روز بماند، fence `BLOCKED` می‌شود
و هیچ دو Writerی فعال نمی‌شوند.
Cursor conversation ID به Antigravity منتقل نمی‌شود؛ فقط Git، Assignment، evidence و
receiptهای پایدار handoff هستند.

## Bootstrap ورود به اجرا

1. همین بسته پس از Final Review در `main` ادغام می‌شود؛ execution branch از شاخهٔ
   پلن یا commit تأییدنشده ساخته نمی‌شود.
2. Coordinator از checkout تمیز `main`، branch یکتای
   `refactor/two-site-architecture` و registry محلی worktreeها را می‌سازد.
3. نخستین Assignment فقط ادامه و refresh کردن `P1-00` روی SHA جدید است. در این
   نقطه یک Worker نویسنده داریم، چون baseline و فایل‌های حاکمیتی lock مشترک‌اند.
4. Worker شش شکاف evidence و rehearsalهای باقیماندهٔ `P1-00` را می‌بندد و
   `COMPLETE_CANDIDATE` تحویل می‌دهد؛ هیچ refactor یا اقدام تولیدی در scope نیست.
5. Codex نتیجه را مستقل بررسی می‌کند. فقط پس از `P1-00=COMPLETE`، Coordinator
   ready-set را دوباره محاسبه و برای کارهای مستقل Pairing پیشنهاد می‌کند.

Invocation نمونه برای Cursor:

```text
/two-site-refactor role=coordinator mode=autonomous stage=auto
/two-site-refactor role=coordinator mode=autonomous stage=P1-00
/two-site-refactor role=worker stage=P1-00 assignment=<ASSIGNMENT_ID>
```

حالت اول ورودی عادی است. `role=worker` فقط برای recovery یک Assignment صادرشده
باقی می‌ماند و مسیر روزمرهٔ اجرا نیست.

ورود Antigravity از custom agent پروژه‌ای `two-site-coordinator` در `/agents` و با
پیام resume ثبت‌شده در [`ANTIGRAVITY_EXECUTION.md`](ANTIGRAVITY_EXECUTION.md) انجام
می‌شود. Coordinator، Worker و Verifier آن از `.agents/` discover می‌شوند و همان
Assignment، registry، integration branch و Codex bridge را مصرف می‌کنند.

## بستهٔ Final Review

Worker باید این موارد را به Coordinator تحویل دهد و Coordinator بدون تغییر به
Codex ارائه کند:

- Assignment و dependency receiptها؛
- full base/head SHA و diff محدود؛
- وضعیت worktree و فهرست فایل‌های تغییرکرده؛
- نتیجهٔ success/failure/rollback tests با evidence URI؛
- اثر روی رفتار، schema، داده، runtime، امنیت و عملیات؛
- خطر باقیمانده و روش rollback؛
- نتیجهٔ integration test روی HEAD جاری integration branch.

Codex نتیجه را با قالب
[`templates/FINAL_REVIEW.md`](templates/FINAL_REVIEW.md) ثبت می‌کند. نبود evidence،
تست نامتناسب، drift رفتاری یا High/Critical gap باز، verdict را از `APPROVE`
خارج می‌کند.

## تحویل خودکار بین پلتفرم اجرایی و Codex

انتقال دستی گزارش توسط کاربر ممنوع است. Coordinator والد در Cursor یا Antigravity
باید:

1. Worker نویسندهٔ پلتفرم را با sub-agent `two-site-stage-worker` اجرا و شناسهٔ آن را برای
   resume نگه دارد؛
2. خروجی را مستقیماً به sub-agent مستقل و read-only با نام
   `two-site-stage-verifier` بدهد؛
3. پس از integration و ساخت bundle، اسکریپت
   `scripts/two_site_codex_final_review.sh` را با Stage، Assignment، base/head کامل و
   مسیر bundle اجرا کند؛
4. receipt ماشینی `CODEX_FINAL_REVIEW.json` را بخواند و بدون واسطهٔ کاربر، verdict
   را به همان Worker برگرداند یا Stage پذیرفته‌شده را ببندد.
5. در verdict برابر `APPROVE`، closeout محدود را stage کند، با
   `scripts/two_site_codex_closeout_review.sh` برای patch دقیق pre-approve بگیرد، همان
   index را commit و سپس commit حاصل را با حالت `--verify-commit` کنترل کند؛ پیش از
   این کنترل Stage وابسته‌ای شروع نمی‌شود.

پل review یک نشست headless و تازهٔ Codex را با sandbox فقط‌خواندنی اجرا می‌کند؛
context آن از AGENTS، MemoryCustodian، منابع canonical و bundle دقیق بازسازی می‌شود.
wrapper فقط receipt را به‌صورت atomic در مسیر ignored همان bundle می‌نویسد. نبود یا
خرابی CLI/auth/schema، تغییر SHA یا receipt ناسازگار برابر `BLOCKED` است و هرگز
approval ضمنی محسوب نمی‌شود.

تغییر خودِ حاکمیت، agent adapter یا bridge یک Stage محصولی نیست و نباید داخل
Assignment جاری پنهان شود. این تغییر در commit مستقل و bundle مستقل با
`scripts/two_site_codex_governance_review.sh` بازبینی می‌شود؛ فقط receipt نوع
`EXECUTION_GOVERNANCE_AMENDMENT` روی exact SHA آن را به زیرساخت pre-reviewed تبدیل
می‌کند و هیچ Stage completion یا اقدام خارجی اعطا نمی‌کند.

چون این review، diff و evidence انتخاب‌شدهٔ ریپازیتوری خصوصی را برای OpenAI Codex
ارسال می‌کند، اولین اجرا نیازمند اجازهٔ صریح و یک‌بارهٔ مالک پروژه با scope دقیق
`TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX` است. این اجازه فقط در مسیر
ignored محلی `.local/two-site-refactor/authorizations/CODEX_SOURCE_REVIEW.md` ثبت
می‌شود و هیچ Agentی حق استنتاج یا ساخت خودسرانهٔ آن از روی تفویض کلی اجرا را ندارد.

راهنمای Cursor در
`.cursor/skills/two-site-refactor/references/autonomous-orchestration.md` و راهنمای
Antigravity در [`ANTIGRAVITY_EXECUTION.md`](ANTIGRAVITY_EXECUTION.md) است. کاربر
فقط milestone و blocker واقعی خارج از اختیارات مصوب را می‌بیند؛ در handoff مکانیکی
Worker، Coordinator و Reviewer نقشی ندارد.

## اقدامات خارجی و Production

تفویض کلی این پلن جای receipt موردی را نمی‌گیرد. پیش از provisioning، deploy،
migration/repair/restore، secret access/rotation، نوشتن یا lifecycle واقعی Object
Storage، تغییر DNS/Writer، cleanup مخرب یا retirement، Codex باید exact target،
preflight، command/controller step، rollback، blast radius و بازهٔ اعتبار را review
و `EXTERNAL_ACTION_APPROVED` صادر کند.

Receipt یک‌بارمصرف است، به SHA و target مقید است و با drift، expiry یا شکست preflight
باطل می‌شود. برای Web Writer، receipt فقط آمادگی را تأیید می‌کند؛ اقدام نهایی
Dashboard همچنان انسانی و بدون auto-promotion است.

## راهبرد سرعت بدون افت کیفیت

1. Coordinator در حالی که Worker اصلی Stage جاری را پیاده می‌کند، Worker دوم را
   فقط برای ممیزی read-only Stage بعدی به‌کار می‌گیرد.
2. نویسندگی موازی فقط برای Pair اثبات‌شده و بدون lock مشترک فعال می‌شود.
3. ادغام همیشه یکی‌یکی است؛ پس از هر ادغام تست Stage و سپس barrier موج اجرا می‌شود.
4. Full Matrix، migration، deploy و تمام عملیات واقعی هرگز shard نویسنده نمی‌شوند.
5. Reviewer مستقل باقی می‌ماند؛ سرعت هیچ gate High/Critical را حذف نمی‌کند.
