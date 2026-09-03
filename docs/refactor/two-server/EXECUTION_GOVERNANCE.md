# حاکمیت اجرای ریفکتور دو سایته

وضعیت: `ACTIVE`

منبع ترتیب: [`execution-order.yaml`](execution-order.yaml)

این سند مرجع یکتای نقش‌ها، اختیار تأیید، موازی‌سازی، branch/worktree و تحویل
بین Cursor و Codex است. جزئیات معماری و معیار فنی هر Stage همچنان در
[`MASTER_PLAN.md`](MASTER_PLAN.md) و Task Card همان Stage قرار دارد.

## تفویض اختیار

مالک پروژه در ۲۰۲۶-۰۹-۰۳ مدیریت اجرای این پلن و تأیید نهایی تغییرات را به
`Codex Final Reviewer` تفویض کرد. پس از این تفویض:

- شروع و پذیرش Stage به تأیید مرحله‌ای کاربر وابسته نیست؛
- Cursor یا Coordinator حق تأیید خروجی خود را ندارد؛
- فقط receipt نهایی Codex می‌تواند Stage را `COMPLETE` کند؛
- تصمیم فنی باز به Codex ارجاع می‌شود؛ رفتار موجود تا تصمیم نهایی حفظ می‌شود؛
- کاربر همچنان می‌تواند scope یا نیازمندی کسب‌وکار را تغییر دهد، اما در زنجیرهٔ
  اجرایی هر Stage نقش approval اجباری ندارد.

تفویض اختیار، invariant انسانی انتقال Web Writer را تغییر نمی‌دهد. drain، تغییر
DNS و فعال‌سازی Writer همچنان در Dashboard به‌صورت اقدام صریح عامل انسانی انجام
می‌شود؛ این «عملیات محصول» است، نه «تأیید تغییر کد».

## نقش‌ها

### Cursor Coordinator

- تنها مالک integration branch، dependency graph و صف ادغام است.
- برای هر Worker یک Work Assignment با Stage، SHA، scope، lock و deadline می‌سازد.
- Stage استاندارد و غیرعملیاتی را پس از `COMPLETE` بودن dependencyها شروع می‌کند.
- خروجی Worker را بررسی مقدماتی می‌کند، اما آن را `COMPLETE` اعلام نمی‌کند.
- هیچ Stage وابسته‌ای را پیش از receipt نهایی Codex آغاز نمی‌کند.

### Cursor Worker

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
`COMPLETE` فقط پس از receipt نهایی Codex برای همان commit صادر می‌شود. تغییر حتی
یک commit بعد از review، receipt را باطل می‌کند و review تازه لازم دارد.

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
/two-site-refactor role=coordinator stage=P1-00
/two-site-refactor role=worker stage=P1-00 assignment=<ASSIGNMENT_ID>
```

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

اگر Cursor و Codex در دو گفتگو اجرا شوند، Coordinator مسیر review bundle و exact
SHA را در handoff نهایی اعلام می‌کند. انتقال این شناسه‌ها صرفاً جابه‌جایی مکانیکی
است و از کاربر تصمیم یا approval نمی‌خواهد؛ Codex تمام تصمیم‌های review را می‌گیرد.

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
