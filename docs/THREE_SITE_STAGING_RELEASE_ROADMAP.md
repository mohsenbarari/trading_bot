# نقشه راه اجرایی انتشار معماری سه‌سایته روی Staging

وضعیت سند: `Stage 0 تا 3 / پذیرفته‌شده؛ Stage 4 / در حال اجرا`

دامنه: انتشار و ارزیابی معماری `Bot-FI + WebApp-FI + WebApp-IR + Witness`
روی منابع کاملاً مجزای staging. این سند مجوز انتشار production یا تغییر دامنه
`coin.gold-trade.ir` نیست.

شاخه سند: `roadmap/three-site-staging-release`

مبنای اولیه: `main@9105264000f51b480eb88f1f80845fd7608bd6b2`

---

## 1. هدف و تعریف نتیجه

هدف اول پروژه، بالا آوردن یک معماری سه‌سایته واقعی و قابل استفاده روی staging
است؛ به شکلی که:

1. `WebApp-FI` در حالت عادی تنها Writer وب‌اپ باشد؛
2. `WebApp-IR` داده آماده و قابل اثبات داشته باشد ولی در حالت عادی Writer نباشد؛
3. Witness مستقل، term و lease نویسنده را کنترل کند؛
4. از دست رفتن مسیر FI به Witness، قبل از امکان Writer شدن IR باعث fence شدن FI
   شود؛
5. انتقال داده بین Finland و Iran مطابق سیاست Object Storage انجام شود و مسیر
   Finland-local بی‌دلیل از ایران عبور نکند؛
6. failover، recovery و failback روی staging قابل تکرار و دارای rollback باشند؛
7. هیچ volume، credential، token، bucket، domain، Compose project یا database
   متعلق به production تغییر نکند.

دو milestone از هم جدا هستند:

- **M1 — Staging Published:** چهار نقش staging بالا هستند؛ FI Writer و IR
  Standby است؛ مسیر عمومی staging سالم است؛ production دست‌نخورده است.
- **M2 — Staging Qualified:** failover و failback کنترل‌شده، convergence،
  rollback و soak کوتاه staging با شواهد قابل بازتولید قبول شده‌اند.

رسیدن به M1 به‌معنای production-ready بودن نیست. Full Matrix کامل، آزمون‌های
تخریبی روی چهار میزبان disposable، endurance بلندمدت و تغییر production در
roadmap جداگانه پیش‌تولید انجام می‌شوند.

---

## 2. واقعیات تثبیت‌شده در شروع roadmap

### 2.1 موارد تأییدشده

- شالوده Witness، writer control، SQL fencing، DR event/blob و توپولوژی روی
  `main@91052640` وجود دارد.
- IPهای canonical فعلی در `core/three_site_topology.py` عبارت‌اند از:
  `65.109.216.187`، `65.109.220.59`، `95.38.164.29` و `37.152.191.11`.
- ۹۴ تست هسته‌ای اعلام‌شده با `python3 -m unittest` بازتولید و همگی قبول شدند.
- ۱۴۴۱ فایل Python tracked از نظر syntax بدون خطا compile شدند.
- `verify_three_site_topology_contract.py --json` قبول شد.
- `deploy/staging/docker-compose.three-site.yml` با env نمونه از نظر Compose
  structure معتبر است.
- شاخه `candidate/three-site-production-ready` نسبت به main فقط شامل اصلاح
  نامرتبط قیمت سکه و گزارش consolidation است؛ candidate معتبر staging محسوب
  نمی‌شود.

### 2.2 موانع شناخته‌شده

- اجرای `test_three_site*.py` نتیجه `191 tests / 1 error / 24 skipped` داشت.
- خطای فعلی یک contract drift بین presigned POST در
  `run_three_site_staging_convergence_observer.py` و انتظار presigned PUT در
  تست convergence است.
- ۲۴ تست PostgreSQL fencing به‌علت نبود scratch database URL اجرا نشدند.
- `main` فاقد live driver نهایی مورد ادعای Full Matrix است، در حالی که بخشی از
  پیاده‌سازی آن در lineageهای فرعی وجود دارد.
- تگ `backup/pre-consolidation-20260802` فقط یک commit از شاخه coin-price را
  نگه می‌دارد و backup همه branch tipها نیست.
- هیچ مدرک تازه‌ای از SHA کانتینرهای واقعی، Docker image identity، وضعیت volume،
  NTP، firewall، disk headroom یا staging runtime چهار میزبان جمع‌آوری نشده است.
- `scripts/deploy_staging.sh` توپولوژی سه‌سایته را اجرا نمی‌کند؛ مسیر canonical
  آن runbook چندمیزبانه و `docker-compose.three-site.yml` است.

### 2.3 lineageهایی که باید بررسی شوند، نه merge کورکورانه

| Lineage | ارزش احتمالی | تصمیم اولیه |
|---|---|---|
| `main@91052640` | شالوده پذیرفته‌شده و baseline جاری | مبنای consolidation |
| `fix/full-matrix-relay-approval-lifetime` | live driver، staging hardening و activation pair | بررسی انتخابی در Stage 1 |
| `feature/three-site-full-matrix-live-driver-v3` | اولین lineage دارای driver واقعی | منبع مقایسه، نه release مستقیم |
| `work/production-three-site-integration-91052640` | production shadow و ابزار production | خارج از دامنه staging اولیه |
| `work/release0-*` | lineage قدیمی و شدیداً divergent | قرنطینه؛ فقط استخراج نیاز با مدرک |
| `work/emergency-*` و `fix/emergency-*` | مسیر اضطراری production/IR | خارج از M1 و M2 |
| `candidate/coin-price-intelligence` | قابلیت مستقل قیمت | خارج از معماری سه‌سایته |

---

## 3. اصول حاکم بر اجرای stageها

### 3.1 قواعد غیرقابل حذف

این موارد gate اضافی نیستند و در هیچ stage حذف نمی‌شوند:

- single-writer و term/lease fencing؛
- fail-closed بودن در ابهام مالکیت؛
- جداسازی کامل staging از production؛
- backup و restore drill پیش از migration؛
- release SHA و image identity یکسان روی نقش‌های مرتبط؛
- NTP معتبر روی Writerها و Witness؛
- رمزنگاری و hash verification برای payloadهای Object Storage؛
- journal و rollback برای عملیات چندمرحله‌ای؛
- عدم تغییر route/domain سرویس production (`coin.gold-trade.ir`) یا هر record
  غیر staging در این roadmap؛
- عدم انتقال secret به Git، shell history یا خروجی roadmap.

### 3.2 gateهایی که برای کوتاه شدن مسیر ادغام می‌شوند

| وضعیت قبلی | تصمیم این roadmap |
|---|---|
| approval انسانی جداگانه برای هر زیرمرحله | یک session محدود به release و staging؛ receiptهای action-bound از همان session |
| build مستقل روی هر میزبان | یک build canonical؛ توزیع با content identity و hash یکسان |
| چند preflight تکراری با خروجی مشابه | یک host-readiness manifest برای هر میزبان و یک aggregate چهارمیزبانه |
| چند سند inventory موازی | یک inventory versioned با بخش planned و measured |
| branch جدید برای هر failure | دقیقاً یک branch برای هر Stage؛ failure روی همان branch اصلاح می‌شود |
| approval دوباره برای resume همان journal | resume فقط با همان journal و همان release؛ عملیات جدید approval جدید می‌خواهد |
| Full Matrix کامل به‌عنوان شرط bring-up | matrix ریسک‌محور برای M2؛ Full Matrix کامل به pre-production منتقل می‌شود |

### 3.3 مواردی که از roadmap staging حذف یا به بعد موکول می‌شوند

- ۱۱۰ سناریوی Full Matrix به‌عنوان شرط M1؛
- شش آزمون destructive روی میزبان‌های production؛
- Gate-D aggregate به‌عنوان شرط بالا آمدن staging؛
- production shadow، production cutover controller و emergency standalone؛
- تغییر Arvan production origin یا record سرویس production `coin.gold-trade.ir`؛
- migration داده production به staging؛
- فعال‌سازی Queue-v1 پیش از اثبات معماری پایه در حالت legacy؛
- اجرای benchmarkها یا سناریوهای تکراری که invariant جدیدی را پوشش نمی‌دهند.

این موارد حذف دائمی از برنامه production نیستند؛ فقط نباید هدف اول را مسدود
کنند.

### 3.4 نقاط تصمیم انسانی

برای کل مسیر فقط این تصمیم‌های انسانی مستقل باقی می‌مانند:

1. پذیرش scope و Stage 0؛
2. اجازه شروع mutation روی پنج VPS تستی موجود در Stage 3B؛ این اجازه در
   `2026-08-02` صادر شد، اما ساخت یا حذف VPS/volume و هر mutation روی دو VPS
   production ایران را شامل نمی‌شود؛
3. اجازه deploy همان release در Stage 4؛
4. اجازه اجرای failover/failback برنامه‌ریزی‌شده Stage 5؛
5. تصمیم مستقل Queue-v1 در Stage 6، فقط اگر activation انتخاب شود.

receiptهای فنی inventory، migration و journal که از یک staging session مشتق
می‌شوند، evidence و authorization binding هستند؛ prompt یا gate انسانی جدید
محسوب نمی‌شوند. تغییر release SHA یا خروج از scope staging، session موجود را
باطل می‌کند و نیازمند تصمیم جدید است.

---

## 4. قرارداد branch، commit و پایان Stage

### 4.1 شاخه‌ها

شاخه integration هدف:

```text
candidate/three-site-staging
```

شاخه‌های مجاز stage:

```text
roadmap/three-site-staging-release
stage/three-site-staging-01-baseline
stage/three-site-staging-02-validation
stage/three-site-staging-03-host-readiness
stage/three-site-staging-04-deploy
stage/three-site-staging-05-failover
stage/three-site-staging-06-acceptance
```

قواعد:

1. Stage بعدی فقط از closure commit مرحله قبل شروع می‌شود.
2. هنگام شروع Stage 1، `candidate/three-site-staging` از closure commit Stage 0
   ساخته می‌شود.
3. branch هر Stage از tip فعلی `candidate/three-site-staging` ساخته می‌شود؛ پس
   از قبول exit gate، closure commit همان Stage به candidate منتقل می‌شود و
   Stage بعدی فقط از candidate به‌روز شروع می‌شود.
4. هر Stage دقیقاً یک branch دارد؛ ساخت branch اضطراری یا checkpoint تودرتو
   ممنوع است.
5. اگر main تغییر کرد، drift در همان stage تحلیل می‌شود؛ branch دیگری ساخته
   نمی‌شود.
6. merge کور lineageهای بزرگ ممنوع است؛ هر تغییر باید `adopt`، `reimplement`،
   `defer` یا `reject` ثبت شود.
7. هیچ branch قدیمی تا پایان M2 حذف نمی‌شود.
8. history بازنویسی و force-push ممنوع است.

### 4.2 قرارداد commit

- هر commit یک هدف مشخص و قابل revert دارد.
- کد، تست همان کد و migration لازم ترجیحاً در یک commit اتمیک ثبت می‌شوند.
- تغییرات مستنداتی پایان stage در commit جداگانه زیر ثبت می‌شوند:

```text
docs(three-site): close stage N <short-name>
```

- commit نامرتبط با دامنه stage ممنوع است.
- پیش از هر commit، `git diff --check` و تست متناسب با همان تغییر اجرا می‌شود.
- پیش از closure commit، worktree باید clean باشد و تمام commitهای stage در
  بخش «گزارش پایان Stage» همین سند ثبت شده باشند.

### 4.3 گزارش اجباری پایان هر Stage

در پایان هر stage، subsection همان stage باید با این اطلاعات به‌روزرسانی شود:

```text
Status:
Branch:
Base SHA:
Implementation commits:
Deployed/tested release SHA (اگر وجود دارد):
Changes delivered:
Commands/tests executed:
Passed / failed / skipped:
External evidence paths and SHA-256:
Production touched: yes/no (برای این roadmap باید no باشد)
Deviations from roadmap:
Open risks:
Rollback verified:
Decision: accepted / rejected / blocked
Next stage authorized:
```

فایل‌های secret، dump، token، private key، full env و evidence حجیم وارد Git
نمی‌شوند؛ فقط مسیر owner-only، schema، timestamp و SHA-256 آن‌ها در گزارش
ثبت می‌شود.

---

## 5. ترتیب stageها و gateهای نهایی

| Stage | نتیجه | Exit gate یکتا | Milestone |
|---:|---|---|---|
| 0 | roadmap و change-control تثبیت‌شده | `G0 Scope Accepted` | — |
| 1 | candidate تمیز و lineage تصمیم‌گیری‌شده | `G1 Candidate Coherent` | — |
| 2 | کد و Compose با تست کامل local/integration سبز | `G2 Software Verified` | — |
| 3 | چهار میزبان و rollback boundary آماده | `G3 Hosts Isolated` | — |
| 4 | چهار نقش staging در normal mode فعال | `G4 Staging Published` | M1 |
| 5 | failover/failback و convergence قبول | `G5 DR Qualified` | M2 |
| 6 | soak، Queue decision و handoff نهایی | `G6 Staging Accepted` | پایان هدف اول |

هر stage فقط یک exit gate دارد. checkهای داخل stage evidence تولید می‌کنند، اما
approval gate مستقل نیستند.

---

## Stage 0 — Roadmap، حقیقت مخزن و change-control

### هدف

تبدیل وضعیت پراکنده فعلی به یک برنامه واحد، محدود و قابل audit پیش از هر تغییر
کد یا سرور.

### Branch

`roadmap/three-site-staging-release`

### اقدامات

1. ساخت شاخه مستند از `main@91052640`، بدون commit قیمت سکه.
2. ثبت واقعیات Git، تست‌ها، blockers و lineageهای مهم.
3. تعیین دقیق M1 و M2 و تفکیک آن‌ها از production readiness.
4. تعریف branch/commit contract و قالب closure record.
5. تعریف gateهای نگه‌داشته‌شده، ادغام‌شده و deferred.
6. commit نسخه اولیه roadmap.
7. بازخوانی roadmap در برابر ADR، topology، staging Compose و runbook.
8. تکمیل گزارش پایان Stage 0 و ثبت closure commit.

### Exit gate — G0 Scope Accepted

- roadmap شامل تمام stageها، ترتیب، ورودی، خروجی، rollback و exit criteria است؛
- production صریحاً خارج از دامنه است؛
- branch و commit contract مشخص است؛
- هیچ تغییر عملیاتی یا server mutation انجام نشده است؛
- Stage 0 closure record کامل و committed است.

### Rollback

حذف نکردن شاخه‌ها و بازگشت ساده به `main@91052640`. Stage 0 هیچ runtime یا
داده‌ای را تغییر نمی‌دهد.

### گزارش پایان Stage 0

- Status: `COMPLETED_ACCEPTED`
- Branch: `roadmap/three-site-staging-release`
- Base SHA: `9105264000f51b480eb88f1f80845fd7608bd6b2`
- Implementation commits:
  `aac81474879d93113ffff348c222e8659ef838cc` — تعریف نسخه اولیه roadmap
- Changes delivered:
  - تعریف M1 و M2 و تفکیک صریح staging از production؛
  - تعریف Stageهای 0 تا 6، branch هر Stage، exit gate یکتا و rollback؛
  - کاهش approval انسانی به چهار نقطه تصمیم؛
  - انتقال Full Matrix کامل، destructive hosts و 24-hour endurance به
    pre-production؛
  - تعریف matrix ریسک‌محور ۱۲سناریویی برای M2؛
  - تعریف closure record و commit contract اجباری برای تمام Stageها.
- Commands/tests executed:
  - کنترل whitespace با `git diff --no-index --check`؛
  - شمارش ساختاری Stage، exit gate و closure record؛
  - بررسی وجود pathها و branchهای مرجع roadmap؛
  - `python3 scripts/verify_three_site_topology_contract.py --json`؛
  - `docker compose ... docker-compose.three-site.yml config --quiet`؛
  - `git diff --cached --check` پیش از commit اولیه.
- Passed / failed / skipped:
  `7 stages / 7 exit gates / 7 closure records؛ topology passed؛ Compose passed؛ 0 failed؛ 0 skipped`
- External evidence: `ندارد`
- Production touched: `no`
- Deviations:
  `docs/*` در `.gitignore` است؛ فایل roadmap با `git add -f` عمداً tracked شد.
- Open risks:
  - خطای فعلی suite سه‌سایته و ۲۴ تست PostgreSQL skip‌شده در Stage 1/2 باز است؛
  - backup tag فعلی همه refها را حفظ نمی‌کند؛
  - runtime واقعی چهار میزبان هنوز read-only audit نشده است.
- Rollback verified:
  `yes؛ Stage 0 فقط یک فایل مستند روی branch مستقل از main افزوده و هیچ runtime یا data mutation ندارد.`
- Decision:
  `ACCEPTED؛ مالک در 2026-08-02 شروع دقیق Stage بعدی طبق roadmap را صریحاً مجاز کرد.`
- Next stage authorized: `yes؛ Stage 1 فقط در دامنه Git و candidate محلی`

---

## Stage 1 — حفاظت Git و ساخت candidate منسجم staging

### هدف

ساخت یک release lineage کوچک، قابل توضیح و بدون کد نامرتبط که تمام نیازهای
واقعی staging را داشته باشد.

### Branch

`stage/three-site-staging-01-baseline`

### ورودی

- closure commit پذیرفته‌شده Stage 0؛
- `main@91052640`؛
- branch inventory و commit graph موجود؛
- lineageهای staging مشخص‌شده در بخش 2.3.

### اقدامات

1. ایجاد backup واقعی پیش از consolidation:
   - `git bundle --all` در مسیر خارج از worktree؛
   - ثبت SHA-256 bundle؛
   - ایجاد ref/tag مستقل برای tip lineageهای حیاتی؛
   - اثبات اینکه tipهای critical از backup قابل enumerate هستند.
2. ایجاد `candidate/three-site-staging` از closure commit Stage 0.
3. تولید inventory تغییرات staging با چهار نتیجه مجاز:
   `adopt`، `reimplement`، `defer`، `reject`.
4. حفظ دو commit اختصاصی main یعنی `cf986cf4` و `91052640` در baseline.
5. بررسی انتخابی lineage `fix/full-matrix-relay-approval-lifetime`، بدون merge
   یکجای آن.
6. حل contract presigned PUT/POST بر اساس رفتار واقعی Arvan و افزودن تست متناظر.
7. وارد کردن فقط hardeningهای لازم برای M1/M2؛ production-shadow، release0،
   emergency و coin-price وارد candidate نمی‌شوند.
8. baseline اولیه با Telegram owner=`legacy` و Queue cutover=`false` باقی می‌ماند.
9. migration graph باید یک head معتبر داشته باشد.
10. مستند قدیمی staging در نقاط متناقض با واقعیت candidate اصلاح می‌شود.

### Exit gate — G1 Candidate Coherent

- backup همه refهای حیاتی واقعاً قابل بازیابی است؛
- diff candidate با main فقط تغییرات توضیح‌داده‌شده staging را دارد؛
- هیچ فایل production-shadow/emergency/coin-intelligence وارد نشده است؛
- هر commit منتقل‌شده منشأ، علت و تست دارد؛
- Queue baseline fail-closed است؛
- worktree clean و migration graph تک-head است.

### Rollback

بازگشت به closure Stage 0 و بازیابی هر lineage از bundle/refهای ثبت‌شده.

### گزارش پایان Stage 1

- Status: `COMPLETED_ACCEPTED`
- Branch: `stage/three-site-staging-01-baseline`
- Base SHA: `198c2d65a4edb11f51d5b92b9fc0fca747cb97da`
- Implementation commits:
  - `0dba3fea615c2016dfe5495bb94af03f4c52d1ba` — inventory و تصمیم lineageها؛
  - `72e2956ef2c16fa0bbaca766061c8ec01ed4f1dc` — الزام Witness relay trust key صریح؛
  - `11ca6503a4845650447f2c64fb7710027c8b6b3b` — محدود کردن reusable session به relay صریح؛
  - `e1ec7036235756f223e05270ebd0134e373ea58f` — کمینه‌سازی scope پیش‌فرض staging؛
  - `2348dba2a58adf94e9aab5b5d376ed21ae66dfe0` — اتصال receipt به session scope؛
  - `e5fc5b483778281ab4050d718da706eddf53b66a` — trust key مقید به backend در failover؛
  - `03613052add09c6c624885596639405cc084db69` — همسان‌سازی تست با presigned POST؛
  - `2da32750f85a1283aade34c5f83fc328562c3255` — Queue-disabled baseline.
- Deployed/tested release SHA:
  `2da32750f85a1283aade34c5f83fc328562c3255؛ فقط local tested و deploy نشده است.`
- Changes delivered:
  `backup کامل refs و ۹ safety tag؛ candidate محدود به پنج hardening approval، اصلاح تست presigned POST و Queue-disabled baseline؛ lineageهای production، emergency، Full Matrix بزرگ و coin-price وارد candidate نشدند.`
- Commands/tests executed:
  `git bundle create/list-heads/verify و sha256sum؛ تست‌های approval، convergence، Queue، هسته و discovery سه‌سایتی؛ python3 -m alembic heads؛ tests.test_migration_smoke؛ make deployment-surface-guard؛ make three-site-topology-contract-check؛ git diff --check و forbidden-path audit.`
- Passed / failed / skipped:
  `approval 27/0/0؛ convergence 6/0/0؛ Queue 12/0/0؛ core 94/0/0؛ سه‌سایتی 167/0/24 از مجموع 191؛ migration smoke 15/0/0؛ هر دو guard موفق.`
- External evidence paths and SHA-256:
  `/root/trading-bot/git-backups/trading-bot-pre-stage1-20260802.bundle` —
  `c923a93285984f4dcfd9027b15f5e6497f6e4fbd64fb42f8451107789c96ade3`
- Production touched: `no`
- Deviations from roadmap:
  `ندارد؛ docs/* در .gitignore است و دو سند roadmap با git add -f آگاهانه track شدند.`
- Open risks:
  `۲۴ تست PostgreSQL fencing بدون scratch database URL اجرا نشدند و باید در Stage 2 اجرا شوند؛ upload واقعی Arvan و هویت artifact/Compose نیز طبق Stage 2 و 3 هنوز اثبات نشده‌اند.`
- Rollback verified:
  `yes؛ bundle با complete history و checksum ثابت verify شد، ۹ lineage حیاتی از bundle قابل enumerate هستند و بازگشت candidate به Base SHA هیچ runtime/data mutation را نیاز ندارد.`
- Decision:
  `ACCEPTED؛ تمام شروط G1 Candidate Coherent عبور کردند.`
- Next stage authorized:
  `no؛ شروع Stage 2 نیازمند دستور صریح مالک پس از مرور این closure است.`

---

## Stage 2 — اعتبارسنجی کامل نرم‌افزار و artifact

### هدف

تبدیل candidate منسجم به release قابل استقرار، پیش از هر تماس تغییردهنده با
چهار میزبان.

### Branch

`stage/three-site-staging-02-validation`

### اقدامات

1. اجرای مجدد ۹۴ تست هسته Witness/Fencing/DR/Failover با `unittest`.
2. اجرای تمام `test_three_site*.py` بدون failure.
3. راه‌اندازی PostgreSQL 15 disposable و اجرای ۲۴ تست skip‌شده؛ skip در مجموعه
   mandatory قابل قبول نیست.
4. اجرای تمام تست‌های `test_dr_*.py`، `test_writer*.py` و
   `test_webapp_writer*.py`.
5. اجرای migration upgrade روی DB خالی و clone داده نمونه؛ سپس بررسی head.
6. compile تمام Pythonهای tracked بدون نوشتن pyc در worktree.
7. اعتبارسنجی `docker compose config` برای Compose canonical و هر چهار role
   render شده با secretهای dummy غیرقابل استفاده.
8. ساخت imageها یک‌بار و ثبت image ID، content identity و release label.
9. smoke محلی برای Witness acquire/renew/drain/expiry و دو WebApp DB مستقل.
10. اجرای deployment-surface guard و اثبات نبود reference به production
    volume/domain/bucket/credential.
11. ثبت یک validation manifest شامل command، exit code، مدت، test count و hash
    خروجی‌های اصلی.

### حداقل فرمان‌های قابل بازتولید

```bash
python3 -m unittest \
  tests.test_webapp_writer_control \
  tests.test_writer_fencing \
  tests.test_writer_witness \
  tests.test_writer_witness_client \
  tests.test_writer_witness_service \
  tests.test_dr_event_protocol \
  tests.test_dr_sync_auth \
  tests.test_dr_receiver_readiness \
  tests.test_dr_blob_crypto \
  tests.test_dr_failover_orchestrator \
  tests.test_dr_staging_operation_backend

python3 -m unittest discover -s tests -p 'test_three_site*.py'
python3 scripts/verify_three_site_topology_contract.py --json
docker compose --env-file deploy/staging/env.three-site.staging.example \
  -f deploy/staging/docker-compose.three-site.yml config --quiet
```

فرمان نهایی Stage 2 باید توسط validation manifest freeze شود؛ شمارش تست‌ها
پس از consolidation ممکن است تغییر کند و عدد ثابت جای فهرست تست را نمی‌گیرد.

### Exit gate — G2 Software Verified

- تمام مجموعه mandatory بدون failure و بدون skip غیرمجاز قبول است؛
- migration روی DB خالی و clone موفق است؛
- چهار role bundle deterministic و قابل verify هستند؛
- image content identity بین bundleها یکسان است؛
- validation manifest به candidate SHA دقیق متصل است؛
- هیچ server mutation انجام نشده است.

### Rollback

revert اتمیک commit ناموفق روی همان branch؛ ساخت branch جدید ممنوع است.

### گزارش پایان Stage 2

- Status: `COMPLETED — G2 ACCEPTED`
- Branch: `stage/three-site-staging-02-validation`
- Base SHA / implementation commits:
  `425891169999cc379ac6b8d273b54983f4c88dba / 27c9404d9f6c3df2b0c2a7dd28aa798c6bbbb36d, 0e63a7ec1b08bef29ea199041215298a021b56ef`
- Deployed/tested release SHA:
  `0e63a7ec1b08bef29ea199041215298a021b56ef`؛ deploy روی remote انجام نشد و
  منظور از tested، نرم‌افزار، migration، Compose و imageهای local همین SHA است.
- Changes delivered:
  `تست projection با قرارداد canonical destination و مرز Receiver/Projector همسو شد؛
  smoke واقعی acquire/renew/drain/expiry/handoff به Witness PostgreSQL افزوده شد؛
  runner مستقیم gate رسمی Witness مسیر import صحیح و شمار دقیق ۷ تست را دارد؛
  validation manifest کامل Stage 2 ثبت شد.`
- Commands/tests executed:
  `unittest هسته؛ discovery تمام test_three_site*.py؛ تمام الگوهای DR/Writer با
  runtime identity تفکیک‌شده؛ gate واقعی Witness/commit-fence؛ migration smoke؛
  guarded upgrade از zero و clone؛ compile بدون pyc؛ topology و deployment-surface
  guard؛ secret-boundary؛ render دوبل چهار role؛ docker compose config؛ build و
  inspect دو image یکتا.`
- Passed / failed / skipped:
  `هسته 94/94؛ three-site 191/191 شامل ۲۴ PostgreSQL؛ الگوهای DR/Writer 343/343؛
  migration smoke 15/15؛ Witness/commit-fence 7/7. در تمام مجموعه‌های mandatory:
  failed=0 و unauthorized skipped=0. شمارها بین gateها هم‌پوشانی دارند و جمع آنها
  به‌عنوان unique test count گزارش نمی‌شود.`
- Evidence paths and SHA-256:
  `docs/THREE_SITE_STAGE2_VALIDATION_MANIFEST.json =
  ff6c8ebeafa9a8e6489890e4b083b32b9ba7c9397e535ec7d36e545d54c3a1c8؛
  /tmp/three-site-stage2-final-a-0e63a7ec و
  /tmp/three-site-stage2-final-b-0e63a7ec صرفاً convenience copy هستند و hashهای
  canonical آنها داخل manifest track شده است.`
- Production touched: `no`
- Deviations / open risks:
  `PostgreSQL خام به‌دلیل نبود extension الزامی trading_bot_boottime کنار گذاشته و
  image canonical مخزن استفاده شد. یک build ترکیبی با پیام status مالک قطع شد؛
  PostgreSQL کامل مانده بود و app از همان BuildKit cache تکمیل و هر دو مستقل inspect
  شدند. attestation واقعی runtime میزبان Witness، secretهای واقعی، upload artifact،
  host isolation و rollback میزبان عمداً متعلق به Stage 3 هستند.`
- Rollback verified:
  `yes؛ تغییرات نرم‌افزاری دو commit اتمیک و قابل revert هستند؛ هیچ remote mutation
  وجود ندارد؛ کانتینر و DBهای disposable با label اختصاصی حذف شدند؛ imageهای release
  فقط local و با tag دقیق SHA باقی ماندند.`
- Decision / next stage:
  `ACCEPTED / no؛ G2 عبور کرد، اما شروع Stage 3 تا دستور صریح مالک مجاز نیست.`

---

## Stage 3 — ممیزی میزبان‌ها، isolation و آمادگی rollback

### هدف

اثبات اینکه چهار نقش staging بدون مصرف یا آلودگی منابع production قابل اجرا
هستند و rollback پیش از deploy آماده است.

### Branch

`stage/three-site-staging-03-host-readiness`

### بخش A — ممیزی read-only

برای هر چهار میزبان ثبت شود:

- hostname، machine-id hash، IP و role مورد انتظار؛
- OS/kernel، Docker/Compose و PostgreSQL version؛
- commit/image/release label کانتینرهای موجود؛
- Compose projectها، containerها، networkها و volumeها؛
- CPU/RAM/PID و disk headroom؛
- mountها، filesystem UUID و مسیرهای production؛
- NTP state و clock offset با `chronyc` یا `ntpq`؛
- port listenerها، firewall policy و مسیرهای peer؛
- domain/origin فعلی staging و production؛
- bucket/versioning/lifecycle مورد استفاده، بدون نمایش secret.

تا پایان این بخش هیچ package install، firewall edit، mount، container start یا
DNS mutation انجام نمی‌شود.

#### checkpoint بخش A — 2026-08-02

- Status: `COMPLETE_WITH_BLOCKERS`
- Audit commit: `7effce5bf808885b186c20f14f030180f2a1c681`
- Evidence:
  `docs/THREE_SITE_STAGE3_HOST_READINESS_AUDIT.json` با SHA-256 برابر
  `28950ff335fce269fc5e9886eb878a9c1a52ba74be9e9dc04266611e9b934514`.
- Bot-FI به‌درستی مستقیماً از میزبان محلی ممیزی شد؛ SSH فقط برای سه میزبان
  دیگر استفاده شد.
- Bot-FI، WebApp-FI و WebApp-IR هر کدام mount مستقل staging، UUID متمایز،
  ظرفیت بیش از حداقل و cgroup aggregate فعال دارند. Witness فقط دیسک root
  production را دارد؛ Docker/Compose، mount staging و cgroup محدود روی آن
  موجود نیست.
- release هدف `0e63a7ec1b08bef29ea199041215298a021b56ef` هنوز روی هیچ مقصدی نصب نشده
  است. پروژه‌های فعال سه‌سایته با releaseهای قدیمی `3138d0c2...` و
  `771c957b...` متعلق به تلاش‌های پیشین‌اند و source truth این Stage نیستند.
- Witness production به‌صورت native systemd روی release
  `4c171289eb62bd82822a6821e0cd474c10f88bab` فعال است؛ این سرویس در ممیزی
  تغییر نکرد.
- bucket staging قدیمی private بودن را با سطح دسترسی فعلی اثبات نکرد؛
  versioning فعال است ولی bucket encryption configuration، lifecycle و
  public-access-block configuration وجود ندارند. prefix تازه release هدف و
  تست encrypted upload/download هنوز ساخته نشده‌اند.
- یک credential-like runtime value در خروجی موقت probe اولیه تشخیص داده شد،
  در evidence track نشده و باید پیش از Stage 4 rotate شود. هیچ secret در
  roadmap یا evidence commit نشده است.
- Production touched: `no`؛ فقط commandهای read-only شامل host/Docker/systemd
  inspection و S3 HEAD/GET configuration اجرا شدند.

#### checkpoint تکمیلی — inventory پنل و انتخاب topology تستی — 2026-08-02

- پنل Arvan با credential زیرساخت owner-only و فقط از مسیرهای `GET` ممیزی شد:
  دقیقاً ۷ VPS فعال وجود دارد. `95.38.164.29` و `37.152.191.11` طبق اعلام مالک
  production هستند و از تمام mutationهای این Stage خارج شدند.
- چهار VPS disposable موجود به‌عنوان topology واقعی Stage 3 انتخاب شدند:
  `Bot-FI=130.185.121.98`، `WebApp-FI=194.5.206.69`،
  `WebApp-IR=188.213.198.115` و `Witness=130.185.121.152`. انتخاب آن‌ها هیچ
  تغییری در IPهای canonical production داخل `core/three_site_topology.py`
  ایجاد نمی‌کند؛ نگاشت staging فقط در inventory/runtime bundle ثبت می‌شود.
- VPS پنجم `185.231.182.6` یک Witness انتقالی قدیمی است و در topology چهار role
  انتخاب نشد. probe اولیه Ed25519 رد شد؛ در بخش B کلید RSA موجود و معتبر آن
  پیدا شد و فقط به‌عنوان relay کنترل داخل region ایران استفاده شد.
- Bot-FI، WebApp-FI و Witness تستی Ubuntu 24.04، Docker `29.1.3`، Compose
  `2.40.3`، NTP synchronized، بدون container و دارای volume مستقل 50 GiB
  هستند. هر سه volume از قبل ext4 و روی
  `/srv/trading-bot-three-site-staging-data` mount شده‌اند؛ UUID، daemon ID و
  machine-id hash آن‌ها متمایز است.
- WebApp-IR تستی در API فعال و volume 50 GiB آن attached بود، ولی TCP/22 از مسیر
  بین‌المللی با وجود security-group صحیح پاسخ نمی‌داد. این وضعیت به‌عنوان blocker
  بخش A ثبت و recovery کنسول out-of-band به بخش B منتقل شد؛ server/volume create،
  delete، rebuild یا detach مجاز نیست.
- Evidence:
  `docs/THREE_SITE_STAGE3_ARVAN_TEST_INVENTORY.json`؛ hash آن پس از commit این
  checkpoint برابر
  `81d5730098364b307fe1e85e0d9032e22cc914bd37e38ea022608539877ae440` است؛
  فایل در checkpoint بخش B تکمیل شده و این hash نسخه جاری است.
- Production touched: `no`؛ inventory پنل و host probeها read-only بودند.

### بخش B — آماده‌سازی isolation با authorization مرحله

ترتیب زیر با authorization یکپارچه مالک برای استفاده از پنج VPS تستی موجود اجرا
می‌شود. lifecycle منابع پنل خارج از اختیار است: هیچ VPS یا volume ساخته، حذف،
detach، rebuild یا resize نمی‌شود. چهار IP canonical و تمام workloadهای production
نیز خارج از mutation هستند.

1. inventory پنل، دو IP production و چهار server/volume ID انتخابی pin و evidence
   می‌شوند؛ نگاشت roleهای staging بدون تغییر topology production ثبت می‌شود.
2. guest VPS تستی WebApp-IR از console out-of-band بازیابی می‌شود: provider
   identity پیش از اتصال دوباره match، host key از چند vantage مقایسه، کلید کنترل
   نصب و password login خاموش می‌شود. boot disk rebuild نمی‌شود.
3. attestation کامل چهار میزبان دوباره جمع می‌شود: hostname و machine-id hash،
   Docker daemon ID، NTP، listener/firewall، `/dev/vdb` provider serial، UUID،
   mount و ظرفیت. volume موجود format یا پاک نمی‌شود.
4. mountهای staging با ownership/mode و persistence صحیح و cgroup aggregate محدود
   می‌شوند؛ containerهای application هنوز start نمی‌شوند.
5. inventory planned واحد با campaign/deployment/Compose namespace، project،
   volume prefix، network، port و evidence root یکتا ساخته و verify می‌شود؛ هیچ
   namespace یا volume متعلق به production/تلاش قدیمی reuse نمی‌شود.
6. credentialهای staging-only ساخته/rotate و با fingerprint اثبات می‌شوند که با
   production برابر نیستند. credential-like value دیده‌شده در probe اولیه نیز
   قبل از Stage 4 rotate می‌شود.
7. bucket اختصاصی staging فقط پس از اثبات private ACL/policy، versioning و lifecycle
   قابل reuse است؛ prefix تازه و encrypted upload/download دارای VersionId/hash
   اجرا می‌شود. production bucket هرگز query تغییردهنده نمی‌گیرد.
8. فقط PostgreSQLهای campaign تازه برای provision/measure start می‌شوند؛ چهار
   system identifier متمایز ثبت و inventory measured دوباره verify می‌شود.
9. source و imageهای exact release به مقصدها منتقل و hash آن‌ها بدون start کردن
   application roleها attestation می‌شود.
10. DNS/TLS مخصوص staging آماده می‌شود؛ production domain و route تغییر نمی‌کند.
11. dedicated staging Telegram token آماده می‌شود؛ در نبود آن Bot عمومی خاموش
    می‌ماند و این موضوع G3 را بلاک نمی‌کند.
12. از آنجا که source staging قدیمی روی BOT-FL کنار workloadهای production فعال
    است، در Stage 3 هیچ freeze یا stop روی آن انجام نمی‌شود. backup/restore drill
    روی هر چهار PostgreSQL مستقل همین کمپین و database scratch موقت اجرا می‌شود؛
    freeze نهایی source و ثبت restore bundle آن فقط در ابتدای Stage 4 و پس از مجوز
    deploy انجام خواهد شد. rollback command plan کمپین در همین Stage dry-run می‌شود.
13. operations session محدود به release نگهداری و receiptهای لازم از همان session
    صادر می‌شوند؛ برای زیرگام‌های هم‌معنی gate انسانی تکراری ساخته نمی‌شود.

#### checkpoint بخش B — recovery کنترل، boundary و planned inventory — 2026-08-02

- گام‌های ۱ تا ۵ کامل شدند. هیچ VPS/volume ساخته، حذف، detach، rebuild، resize یا
  format نشد و هیچ application container start نشد.
- کنسول out-of-band ثابت کرد WebApp-IR از قبل کاملاً bootstrap شده است: SSH
  active/key-only، password login خاموش، authorized-key fingerprint برابر کلید
  BOT-FL، Docker/NTP سالم، volume مستقل mount و cgroup فعال است. marker ناقصِ
  گزارش اولیه ناشی از بررسی نام فایل اشتباه بود.
- ingress مستقیم بین‌المللی به WebApp-IR همچنان timeout می‌شود. دو rule محدود
  `/32` روی security group تستی اضافه شد: مسیر Bot-FI تستی persist شد ولی در
  data plane timeout ماند؛ مسیر `185.231.182.6/32` داخل region ایران باز شد.
  host key مقصد از کنسول و relay با fingerprint
  `SHA256:P+Smj2GAf5y7WpKXh9nQQfN1ewuSQ/8q5sY/+gAxO70` match و key-only SSH از
  relay قبول شد. هیچ rule یا firewall production تغییر نکرد.
- هر چهار role بدون container و با machine ID، Docker daemon ID، filesystem UUID
  و boundary متمایز attestation شدند. mountها 50 GiB، `ext4` و دارای
  `nosuid,nodev,noexec` هستند؛ فضای آزاد هرکدام بیش از 52.5 GB است. cgroupهای
  200%/5 GiB، 150%/3 GiB، 200%/4 GiB و 100%/2 GiB مطابق roleها فعال‌اند.
- planned inventory با campaign
  `fd34231d-f52e-498a-aab4-438c99d88fc5` و deployment
  `stage3-0e63a7ec-fd34231d` در مسیر owner-only
  `/root/secure-envs/trading-bot/three-site-staging-0e63a7ec-fd34231d/planned-inventory.json`
  با mode `0600` نصب شد. verifier رسمی در حالت `dedicated-host-destructive`
  قبول و SHA-256 آن
  `cfd9095bf961690ad96aa4f5849ec7c5c2276775fdd4c2502d1b074351d089d0` است.
  approval رسمی exact-subject هنوز صادر نشده؛ بنابراین provision دیتابیس و
  data movement شروع نشده‌اند.
- ابزار idempotent rule با commitهای `bf5c6e98` و `9fc10979` ثبت شد؛ endpoint
  lifecycle یا delete ندارد و production overlap را fail-closed رد می‌کند.
- Production touched: `no`؛ read-only identifier probeهای production فقط denylist
  inventory را تازه کردند.

#### checkpoint بخش B — Object Storage و subject تأیید — 2026-08-02

- گام ۷ کامل شد. باکت موجود `gold-trade-staging-three-site-dr` ساخته یا حذف نشد؛
  ACL خصوصی، policy غیرعمومی، versioning فعال و lifecycle دقیقاً محدود به prefix
  کمپین `staging/fd34231d-f52e-498a-aab4-438c99d88fc5/` اثبات شدند. retention
  جاری ۴۵ روز، noncurrent retention چهارده روز و abort multipart یک روز است.
- backend واقعی Arvan با دو کنترل AWS-style سازگار نبود: با default SSE فعال،
  `PutObject` حتی در حالت PAB آزاد `InvalidArgument` داد؛ با SSE خاموش و PAB
  سخت‌گیر نیز `AccessDenied` داد. diagnostic با restoration اجباری اجرا شد و
  حذف نهایی آن دو configuration فقط پس از تطبیق دقیق محتوایی انجام گرفت. مرز
  خصوصی با ACL خصوصی و policy غیرعمومی حفظ شد و رمزنگاری payload به‌صورت اجباری
  client-side `AES-256-GCM` باقی ماند.
- probe نهایی ۴۰۹۶ بایت plaintext را فقط در حافظه رمز کرد، ۴۱۲۴ بایت ciphertext
  را versioned upload/readback کرد و hash و decrypt را پذیرفت. VersionId برابر
  `pleFwLJZnR.ch9ZX6Pw1DSd6K4734Gg` و ciphertext SHA-256 برابر
  `df44b4ab9ddaf284a4509b9b388fdab94ee8d7889c4b4f1a5d2bd199a9fff009`
  است؛ کلید موقت persist نشد.
- evidence owner-only در
  `/root/secure-envs/trading-bot/three-site-staging-0e63a7ec-fd34231d/object-storage-readiness-v3/object-storage-readiness.json`
  با mode `0600` و SHA-256 برابر
  `d803fd9e914fa5cd517f3888ec8394e247492176e497754cfa99cc8a8b307d1e`
  ثبت شد. ابزار و تست‌های guard با commitهای `34102db5` و `67e5ed17` ثبت شدند؛
  ۱۲ تست Object Storage مرتبط قبول شدند.
- گام ۶ هنوز کامل نیست. همه‌ی credentialهای owner-only موجود پس از normalize
  همان fingerprint `285b32f...868b6de` را دارند و rotation واقعی رخ نداده است.
  API key زیرساخت برای ECC معتبر است، اما endpoint رسمی Storage به Bearer JWT
  نشست پنل نیاز دارد؛ بنابراین ساخت temp user یا refresh secret بدون نشست معتبر
  پنل اجرا نشد. این مورد باید پیش از Stage 4 بسته شود، ولی صحت گام ۷ را نقض نمی‌کند.
- subject دقیق planned inventory آماده است: مسیر owner-only
  `/root/secure-envs/trading-bot/three-site-staging-0e63a7ec-fd34231d/planned-inventory-approval-subject.json`،
  mode `0600`، SHA-256 برابر
  `507ea62f12ee82aa9742c72756af79b9266650dafba272f424f5c92305dea0a2` و
  canonical artifact hash برابر
  `4f787ec755264adb6cab93ae98b270d6f98b673002d9029b721625a1c55ae5a6`.
  صدور approval به passphrase/TOTP در TTY مورد اعتماد نیاز دارد و bypass نشده است.
- Production touched: `no`؛ هیچ production bucket/domain/host یا server/volume
  lifecycle API تغییر نکرد.

#### checkpoint بخش B — provision، artifact و restore drill — 2026-08-02

- گام ۸ کامل شد. تنها چهار PostgreSQL کمپین تازه start شدند؛ هیچ Redis، API، Bot،
  migration، worker یا Witness API شروع نشد. system identifierها به‌ترتیب
  `7669505181206511650`، `7669505201946177569`، `7669505221491204130` و
  `7669505191035326497` هستند؛ هر چهار مقدار متمایز و خارج از denylist production
  هستند. provisioned inventory و approval با شناسه
  `ca33ed40-efbb-4790-8a6d-9e563900c098` روی هر چهار میزبان نصب و verifier رسمی در
  حالت `provisioned` قبول شد. این دومین و آخرین approval inventory بود و تکرار
  نمی‌شود.
- گام ۹ کامل شد. Git bundle و imageهای exact release بدون build مقصد منتقل شدند.
  hash archiveهای Git، PostgreSQL، app و dependency به‌ترتیب `73caa081...b2ec6`،
  `d87f651a...9a4b`، `6cdc6448...f427` و `38c24ec2...b62d` است. انتقال WebApp-IR
  از Object Storage خصوصی/versioned و با age encryption انجام شد؛ URL موقت persist
  نشد و relay payload cache نداشت. verifier image inventory v2 تفاوت طبیعی Docker
  legacy/containerd ID را با canonical config/rootfs حل کرد و content identityهای
  app/PostgreSQL/nginx/Redis روی roleهای لازم برابر شدند.
- گام ۱۰ از نظر readiness کامل است و routing همچنان hold است. TLS معتبر فعلی
  `staging.gold-trade.ir` تا `2026-10-20` و `staging.362514.ir` تا `2026-09-14`
  اعتبار دارد؛ certificateهای private roleها نیز تازه ساخته و نصب شده‌اند. چون
  application هنوز start نشده، هیچ DNS/origin تغییر نکرد و این تغییر به Stage 4
  تعلق دارد.
- گام ۱۱ مطابق policy بسته است: Bot عمومی با synthetic disabled values خاموش
  می‌ماند؛ نبود Telegram token اختصاصی G3 را بلاک نمی‌کند.
- گام ۱۲ در دامنه مجاز کامل شد. چهار dump مستقل در scratch databaseهای موقت restore
  شدند؛ fingerprint منطقی schema/data قبل و بعد برابر، scratchها حذف و host
  attestation پس از drill دوباره قبول شد. freeze source قدیمی به ابتدای Stage 4
  منتقل شد، زیرا آن source روی BOT-FL با production هم‌میزبان است و مجوز فعلی
  اجازه stop آن را نمی‌دهد. rollback plan بدون اجرای stop و بدون حذف داده dry-run
  شد.
- aggregate بدون secret در `docs/THREE_SITE_STAGE3_G3_EVIDENCE_AGGREGATE.json` و
  command plan در `docs/THREE_SITE_STAGE3_ROLLBACK_PLAN.json` ثبت شده است. تنها
  blocker فنی G3 چرخش credential تکراری Object Storage است؛ blocker تصمیمی نیز
  اجازه صریح مالک برای Stage 4 پس از مرور aggregate است.
- cleanup: cache ناقص relay و partial/نسخه‌های موقت bootstrap روی WebApp-IR امن
  پاک شدند؛ archiveهای نهایی و backupها تا acceptance حفظ می‌شوند.
- Production touched: `no`.

#### checkpoint بخش B — rotation نهایی Object Storage — 2026-08-02

- Secret همان principal اختصاصی staging در پنل Arvan rotate شد. Access Key ID
  ثابت ماند، ولی fingerprint secret از
  `5032a2e79be5a2660ae239240759b83dc4edd1a414cffcc808731f36dca4ea74`
  به
  `ddc1973db41e0a52b816479a8d63a069fba210dd325918173dbd6095aed0f9b1`
  تغییر کرد؛ secret قبلی روی `HeadBucket` پاسخ `403` و secret جدید پاسخ `200`
  گرفت. بنابراین rollback به secret قبلی ممنوع است و در صورت نیاز فقط rotation
  تازه مجاز خواهد بود.
- hardener پس از rotation یک drift ارائه‌دهنده را fail-closed متوقف کرد. Arvan
  برای policy شامل دو statement با principal دقیقاً یکسانِ Access Key جاری،
  `IsPublic=true` گزارش می‌کرد؛ خود policy هیچ wildcard یا `NotPrincipal Allow`
  نداشت و probe ناشناس `HEAD/LIST/GET` همگی `403` بودند. تحلیل semantic policy با
  commit `1657ea80` اضافه شد؛ wildcard همچنان gate را می‌بندد. اتصال fingerprint
  credential به evidence با commit `e730ff0e` ثبت شد.
- probe نهایی fingerprint-bound با `AES-256-GCM`، versioned upload، readback و
  decrypt قبول شد. evidence owner-only در
  `/root/secure-envs/trading-bot/three-site-staging-0e63a7ec-fd34231d/object-storage-readiness-rotated-v2/object-storage-readiness.json`
  با SHA-256 برابر
  `76c3671fd3c1bc32374f069675d05c78013bb50e785ae23a4dc001e549d2e4d1`
  و VersionId برابر `lEnt7fUtdJoE3jKlZkalN5BDYe1gnkx` ثبت شد.
- ابزار rotation محدود material با commitهای `d06ec8f6` و `8f513b03` ثبت شد؛
  ۱۷ تست مرتبط قبول شدند. فقط credential JSON و manifest تغییر کردند؛ TLS، HMAC،
  database secrets، role env و role compose بازتولید نشدند. manifest نهایی
  `887b521175caca8ddfb301ec123917e520947d9367600381122de87de98398cc`
  و credential file نهایی
  `b3806a027cf3c832e3483b26daf8cc5da9ede30cd8e4e5881befedbf29b6f66c`
  است.
- credential فقط روی WebApp-FI و WebApp-IR با mode `0600` نصب شد؛ Bot-FI و
  Witness مطابق secret boundary فاقد آن ماندند. manifest روی هر چهار role match
  شد، هیچ فایل موقت باقی نماند و تنها container فعال هر میزبان همان PostgreSQL
  قبلی است. probe read-only از image پین‌شده روی هر دو WebApp، `HeadBucket=200`
  و versioning=`Enabled` داد و container یک‌بارمصرف حذف شد.
- campaign bundle با provisioned inventory امضاشده، policy issuer اصلی و hash
  `bc053cdbd2a43b999a0ddf8c96ef4a25d60416a37b7cc364aa6e5763eb811a88`
  verify شد. policy issuer exact با mode `0600` داخل evidence root همین کمپین
  نگهداری می‌شود؛ approval inventory تکرار نشد.
- تمام الزام‌های فنی G3 کامل‌اند. مالک در `2026-08-02T20:27:34Z`، G3 را برای
  release دقیق `0e63a7ec1b08bef29ea199041215298a021b56ef` پذیرفت و شروع Stage 4
  را صریحاً مجاز کرد. این تصمیم مستقیم جایگزین token/gate تکراری است.
  Production touched: `no`.

### Exit gate — G3 Hosts Isolated

- چهار host attestation تازه و aggregate معتبر است؛
- staging data root روی mount مستقل و دارای ظرفیت کافی است؛
- هیچ production identifier در staging inventory وجود ندارد؛
- backup و restore drill قبول شده‌اند؛
- rollback command plan قبل از deploy dry-run شده است؛
- release/artifact/image hashes روی مقصدها match هستند؛
- owner مجوز شروع Stage 4 را برای همین release صادر کرده است.

### Rollback

حذف نکردن source staging قبلی؛ خاموش کردن projectهای جدید و unmount نکردن یا
پاک نکردن داده تا acceptance نهایی. هر cleanup یک تصمیم جدا پس از M2 است.

### گزارش پایان Stage 3

- Status: `COMPLETED — G3 ACCEPTED / STAGE 4 AUTHORIZED`
- Branch: `stage/three-site-staging-03-host-readiness`
- Base SHA / implementation commits:
  `aab3558aebf6a25454a4d1f532e516b75dcbddde /
  7effce5bf808885b186c20f14f030180f2a1c681, 8dcbba1e, bf5c6e98, 9fc10979,
  34102db5, 67e5ed17, 75e3836c, 41546339, da2a45f8, 7f7d8aee,
  55fdab64, 9693a875, 57392f15, 679bfdec, 59023838, 2331b2d2,
  c2f5aee5, 1657ea80, e730ff0e, d06ec8f6, 8f513b03`
- Tested release SHA: `0e63a7ec1b08bef29ea199041215298a021b56ef`
- Host inventory summary:
  `چهار VPS disposable قابل‌دسترسی و هر کدام فقط دارای PostgreSQL سالم کمپین
  هستند؛ Docker/NTP/mount/cgroup هر چهار role attestation و identifierهایشان
  متمایز است. WebApp-IR از
  relay تستی داخل ایران با key-only SSH کنترل می‌شود. مرز Object Storage خصوصی،
  versioned و دارای lifecycle است و encrypted versioned readback قبول شد. دو VPS
  production ایران و چهار مقصد canonical از mutation خارج‌اند.`
- Commands/tests and results:
  `چهار ممیزی read-only canonical؛ inventory هفت VPS پنل؛ attestation چهار VPS
  تستی؛ VNC/SSH host-key و SSH policy؛ lsblk/findmnt/wipefs --no-act؛ Docker/NTP/
  cgroup؛ دو security-group rule محدود تستی؛ structural inventory verifier =
  PASS؛ S3 private/versioning/lifecycle و provider-compatibility diagnostics؛
  AES-256-GCM fingerprint-bound upload/readback = PASS؛ secret قبلی = 403؛
  credential نصب‌شده از هر دو WebApp = HEAD 200/versioning Enabled؛ semantic
  policy و anonymous HEAD/LIST/GET = PASS؛ image inventory چهار role = PASS؛ چهار
  backup/restore scratch drill و attestation پس از آن = PASS؛ unittestهای ابزارهای
  جدید = PASS؛ campaign bundle و secret boundary = PASS. lifecycle منابع پنل و
  application start = صفر.`
- Evidence paths and SHA-256:
  docs/THREE_SITE_STAGE3_HOST_READINESS_AUDIT.json =
  28950ff335fce269fc5e9886eb878a9c1a52ba74be9e9dc04266611e9b934514؛
  docs/THREE_SITE_STAGE3_ARVAN_TEST_INVENTORY.json =
  42da3635a67e089ab5c0469dce5f84139da53b70238cb6b1a0df8254d3fdae0b؛
  owner-only provisioned inventory =
  a44a795ad2753caa38357deda26f5269ba14082fd1d969587b1fd7b9b1408c25؛
  owner-only Object Storage readiness =
  76c3671fd3c1bc32374f069675d05c78013bb50e785ae23a4dc001e549d2e4d1؛
  rotated bootstrap material manifest =
  887b521175caca8ddfb301ec123917e520947d9367600381122de87de98398cc؛
  aggregate = docs/THREE_SITE_STAGE3_G3_EVIDENCE_AGGREGATE.json / SHA-256
  `ce96778c06d7afbbe9c390bf07ef75f7930e50378ceb5eef5c6d242cb3cb8959`؛
  rollback plan = docs/THREE_SITE_STAGE3_ROLLBACK_PLAN.json / SHA-256
  `20c8a126843a9422b323589eabd337bf7120be2dfb9dceaa681bcae7c2fea625`
- Production touched: `no`
- Deviations / open risks:
  `ingress بین‌المللی مستقیم WebApp-IR هنوز timeout است و control از relay ایران
  عبور می‌کند؛ Arvan policy-status را برای principal صریح جاری public گزارش می‌کند،
  ولی policy semantic فاقد wildcard و دسترسی ناشناس عملاً بسته است؛ SSE پیش‌فرض و
  strict PAB به‌علت رفتار ناسازگار provider قابل استفاده نیستند و client-side
  encryption اجباری است؛ هیچ منبع پنل بدون اجازه مالک ساخته یا حذف نمی‌شود.`
- Rollback verified: `yes برای boundary پیش از deploy؛ چهار restore drill و command-plan dry-run قبول شدند. freeze/restore bundle source قدیمی طبق مرز production در ابتدای Stage 4 انجام می‌شود.`
- Decision / next stage:
  `G3_ACCEPTED / yes؛ مالک G3 را پذیرفت و Stage 4 را برای release دقیق
  0e63a7ec1b08bef29ea199041215298a021b56ef مجاز کرد. gate inventory یا token
  دیگری برای شروع Stage 4 ساخته نمی‌شود.`

---

## Stage 4 — انتشار baseline سه‌سایته روی staging

### هدف

رسیدن به M1 با topology واقعی، FI Writer، IR Standby و Queue در حالت legacy.

### Branch

`stage/three-site-staging-04-deploy`

### ترتیب اجرای تغییرناپذیر

1. freeze فقط sourceهای staging و ثبت دقیق سرویس‌های قبلاً فعال؛
2. backup نهایی PostgreSQL/uploads/audit و تأیید fingerprint؛ Redis restore نشود؛
3. انتقال payloadهای Finland-local مستقیم و رمزنگاری‌شده بین Bot-FI و
   WebApp-FI؛
4. انتقال payloadهای Iran-bound فقط از Object Storage staging با VersionId و
   hash verification؛
5. start دیتابیس Witness و migration با identity کم‌اختیار؛
6. start دیتابیس‌ها و private DR receiverهای Bot-FI، WebApp-FI و WebApp-IR؛
7. restore seedها و اثبات business/data fingerprint؛
8. start workerهای DR، blob و projection؛
9. acquire term اولیه برای `webapp_fi` و اثبات renew تازه؛
10. اثبات fenced/standby بودن `webapp_ir`؛
11. start API و background jobهای WebApp-FI؛
12. start API standby محدود WebApp-IR بدون public writer authority؛
13. start Bot-FI با `TELEGRAM_DELIVERY_EXECUTION_OWNER=legacy`؛
14. route فقط `staging.gold-trade.ir` به WebApp-FI؛
15. اجرای smokeهای login، offer، trade، notification، messenger و upload؛
16. تأیید backlog صفر یا bounded و convergence اولیه؛
17. ثبت global staging commit evidence.

هر command تغییردهنده ابتدا dry-run و سپس با confirmation همان plan اجرا می‌شود.
Confirmationهای هم‌معنی ادغام می‌شوند، ولی fencing و ownership هرگز bypass
نمی‌شوند.

#### checkpoint اجرای Stage 4 — freeze، backup و seed transfer — 2026-08-02

- مالک G3 را برای release دقیق
  `0e63a7ec1b08bef29ea199041215298a021b56ef` پذیرفت و Stage 4 را مجاز کرد؛
  branch `stage/three-site-staging-04-deploy` از همان نقطه ساخته شد.
- sourceهای legacy از قبل freeze بودند. به‌جای start کردن application برای عبور
  از gate، ابزار fail-closed re-attestation اضافه شد. Bot-FI فقط `db,redis` با
  PostgreSQL system ID `7660955632928653346` و fingerprint
  `8f0cfc5edd66a74505f345764636055ddce26c42e945a8b8c447b4c3d85b40b2`
  باقی ماند؛ WebApp-FI نیز فقط `db,redis` با system ID
  `7660954509197713442` و fingerprint
  `aeb56dada8d2208adc7a0c5ff3c1a5a84338a6dc7bf6d0740b0a628e4b081b2d`
  باقی ماند. هیچ application start/stop/recreate نشد و Redis restore نشد.
- backup نهایی PostgreSQL/uploads/audit و restore drill مستقل برای هر دو source
  قبول شد. canonical manifest SHA-256ها به‌ترتیب Bot-FI و WebApp-FI برابر
  `48951978f6a5f9fd563681da0a9ef12a1a93e59ba884e4810fb573b50da0e7c8`
  و `2c6aabf8bb79235e81c931031a1273f4ae9899277da2f5fdcec78acb72ee51f9`
  هستند. تلاش اول Bot-FI پیش از هر application mutation روی pull/build ناخواسته
  image fail-closed شد؛ ابزار به read-only volume mount، شبکه `none` و image ID
  موجود و immutable اصلاح شد و اجرای v2 قبول شد. artifact ناقص برای audit حفظ شد.
- evidenceهای WebApp-FI از کانال SSH با host key pin‌شده به BOT-FL تجمیع شدند.
  age identity تازه و owner-only مخصوص campaign ساخته شد. هر دو seed در باکت
  خصوصی staging و فقط زیر prefix campaign منتشر، exact VersionId خوانده، hash
  ciphertext بررسی و سپس decrypt/readback شد. seed manifest SHA-256های canonical
  Bot-FI و WebApp-FI برابر
  `f97212ca45f5bb9a39269a4b1b237080970ebfbedaaa9b8fe19b97e3ac278e31`
  و `5cf01dd9566811235d22e68e31dd8e8f26198d37e7a595102d76eb54248239b2`
  هستند.
- migration plan کوتاه‌عمر با mapping رسمی `bot_fi<-bot_fi`،
  `webapp_fi<-webapp_fi`، `webapp_ir<-webapp_fi` و Witness خالی ساخته شد.
  canonical plan SHA-256 برابر
  `4df634c6a12fba713ca0d7958e54ca0bd7aa5e5e5a6bc50fa1a5474232b722a3`
  و subject SHA-256 روی دیسک برابر
  `8272da8c680f534b3a8c7874e23a97f88c76405d0886ec10fed8114d2dbd0752`
  است. G3/مجوز شروع Stage 4 تکرار نمی‌شود؛ receipt دقیق `approve_migration`
  فقط guard رمزنگاری‌شده‌ی همان plan برای شروع restore/activation است و برای هر
  role یا confirmation دوباره token ساخته نخواهد شد.
- Production touched: `no`. هیچ VPS/volume ساخته، حذف، detach، rebuild، resize یا
  format نشد؛ هیچ production project، bucket، route یا domain تغییر نکرد.

#### checkpoint اجرای Stage 4 — target migration و private plane — 2026-08-03

- plan اولیه هنگام توقف لایه approval ابزار منقضی شد و بدون bypass کنار گذاشته
  شد. plan هم‌محتوای چهار‌ساعته با همان release، inventory، mapping و evidenceها
  ساخته و با password+TOTP دوباره به‌صورت exact-subject تأیید شد. canonical hash
  plan جدید `6b2590843dfb78d49d18d974455b292d1cca48e6ff995a667875e231df8758bd`
  و approval ID برابر `bc2fbc2f-eb66-40ad-a8d8-bac0603270f0` است. G3 و approval
  inventory تکرار نشدند.
- target seed هر چهار role تأیید شد: Bot-FI و WebApp-FI از مسیر SSH pin‌شده داخل
  Finland، WebApp-IR فقط از سه VersionId ثبت‌شده در Object Storage خصوصی و Witness
  به‌صورت empty seed. canonical evidence hashها به‌ترتیب
  `1415f574a57b5dfee9e17c0a4b9b5bf412f83f1c78524e5220584d9b14acf9c5`،
  `ef7fcc92d1644596c79dfbffbb8f2190eeeace5d9f1151b9e5c661e9356308f0`،
  `bde18c050d2fc1ebf49c1838e56eb0176e8ee406bdbb5fa5328af4eb398a35ba`
  و `fd12e924bf840f72749cc5c7735905dbcb2ab14357a9969c79b1c25b368249ea`
  هستند. Redis restore نشد.
- Python میزبان WebApp-IR فاقد `boto3` بود. به‌جای package install یا تغییر system
  Python، bundle pure-Python نسخه‌های موجود در image exact release با container
  بدون شبکه استخراج شد؛ import روی Python میزبان قبول و aggregate hash آن
  `cfc8cd66946c13e7c0874ba92542d7cdd475764a8934ef05eb448f3f4b82157b`
  ثبت شد.
- journalهای durable چهار role ساخته شدند. seed سه دیتابیس محصول restore شد؛
  Witness خالی باقی ماند؛ schema و نقش‌های کم‌اختیار هر چهار دیتابیس پیکربندی شد؛
  private receiver/projection/TLS و سپس workerها با barrier چهار-journalی شروع
  شدند. وضعیت فعلی Bot-FI `workers_ready`، WebApp-FI `writer_initialized +
  workers_ready`، WebApp-IR `standby_fenced + workers_ready` و Witness
  `private_ready` است. public services و route هنوز شروع/تغییر داده نشده‌اند.
- security groupهای تستی data-plane نداشتند و اتصال WebApp-FI به Witness پیش از
  acquire fail-closed شد. ابزار idempotent با server/group ID pin‌شده و production
  denylist در commit `1c53bab8` اضافه شد. هفت rule محدود `/32` برای پورت‌های
  private TLS `8443/8444` با commitment
  `fe03fea6dc0c2590a506d3a1424b9795aeb250dea1d56c0ed003f6b2eac477e4`
  در دو security group تستی اعمال و از پنل بازخوانی شد؛ lifecycle operation و
  production overlap هر دو false بودند.
- WebApp-FI با request ID ثابت `1306c6a2-c162-4055-bf62-59ab0a752a06` term اولیه
  epoch 1 را از Witness گرفت و proof را اتمی import کرد. سپس agent کنترل FI یک
  renewal تازه را اثبات کرد؛ WebApp-IR هم‌زمان epoch 1 fenced، بدون lease و بدون
  Writer authority باقی ماند.
- مسیر مستقیم بین‌المللی WebApp-IR همچنان طبق inventory timeout است. controller
  convergence در commit `ff9cdac9` فقط برای WebApp-IR به relay تستی pin‌شده
  `185.231.182.6` مجهز شد؛ SSH از این مسیر فقط receipt کوچک exporter را برمی‌گرداند
  و snapshot payload از SSH عبور نمی‌کند.
- گام بعدی `routing-hold` است. ابزار convergence یک snapshot JSON redacted شامل
  hash/count/checkpoint، بدون business value یا file bytes، را با presigned URL در
  باکت private/versioned `gold-trade-staging-three-site-dr` زیر prefix staging
  ایجاد و exact VersionId را بازخوانی می‌کند. لایه ایمنی اجرا برای همین write به
  سرویس cloud شخص ثالث، approval صریح تازه مالک خواسته است؛ هیچ workaround انجام
  نشد و Stage 4 در همین مرز fail-closed متوقف است.
- Production touched: `no`. هیچ production host، security group، bucket، domain
  یا route تغییر نکرد و هیچ VPS/volume ساخته، حذف، detach، rebuild، resize یا
  format نشد.

#### checkpoint اجرای Stage 4 — convergence، routing-hold و public-ready — 2026-08-03

- مالک upload snapshot redacted مربوط به convergence در باکت خصوصی و versioned
  `gold-trade-staging-three-site-dr` و exact VersionId readback را صریحاً مجاز کرد.
  اجرای نهایی سه snapshot هم‌هویت را از Bot-FI، WebApp-FI و WebApp-IR گرفت؛ raw
  snapshotها فقط شامل hash/count/checkpoint و فاقد business value/file bytes هستند.
  WebApp-IR payload را از SSH عبور نداد و آن را زیر object key
  `staging/wa-ir-transport/convergence/fd34231d-f52e-498a-aab4-438c99d88fc5/0e63a7ec1b08bef29ea199041215298a021b56ef/010a99bc-ec8a-4eec-8633-89d74ebee50d.json`
  منتشر کرد. exact VersionId بازخوانی‌شده
  `vWH0DikDqX6vkYmxsNNCEyJDo6FsOWG` است؛ summary خام SHA-256 برابر
  `0fc77c7b977bbe6b50327c14d13ddb6988f95b0ea035e60875210af64efab8d0`
  دارد. نسخه‌های fail-closed قبلی برای audit حذف نشدند.
- convergence نهایی قبول شد: event checkpoint SHA-256 برابر
  `0e56638e2f1f500e4ec145e885c217a804eab745e9c4d175f38d7c67eabad23a`،
  database parity برابر
  `f2e2bf13855e511508f4d79a3431bf7ace73feb1ab6a749a61eeead4f6de24d7`
  و blob parity برابر
  `55b5f5e45f3a7e32032a3273217162b88998feef50e696a8e8d5831fe003f9eb`
  است. release checkout/image تغییر نکرد؛ adapter read-only با SHA-256
  `9ce088da14bcdefe345418cae36a0c44a00dd1180f848c113e16c1024947c52f`
  فقط transaction observer را محدود کرد. اصلاحات controller در commitهای
  `dbde9b2c`، `5b1d0ff9`، `34569c89` و `c454b5c7` ثبت و ۲۶ تا ۴۲ تست مرتبط در
  مجموعه‌های مربوط قبول شدند.
- routing provider به‌صورت read-only بازخوانی شد و `app.gold-trading.ir` همچنان
  روی origin قبلی `65.109.220.59` ماند. routing-hold سه role محصول با evidenceهای
  SHA-256 `4e1b85f1...59cc`، `a07058e9...7bc` و `5b129948...bb53` قبول شد؛ هیچ
  DNS/origin mutation انجام نشد. سپس `start-public` برای Bot-FI، WebApp-FI و
  WebApp-IR با confirmation همان plan اجرا شد و journalها به `public_ready`
  رسیدند؛ state hashها به‌ترتیب `00e185b0...acee`، `287d8915...e968` و
  `54385fe7...6335` هستند. Witness طبق topology در `private_ready` ماند.
- observation اولیه WebApp-FI و WebApp-IR قبول شد. collector برای Witness دو
  وابستگی اشتباه به config کامل WebApp و host-published port داشت؛ اصلاحات
  control-plane در commitهای `14fc4f24` و `aacee34e` ثبت و ۲۴/۸ تست مرتبط قبول
  شدند. ابزار با SHA-256
  `1471ee35ed139d1a21a922548fb328cced5bb138392273b051dfd4f7729059dc`
  خارج از release tree روی Witness نصب شد و probe مستقیم آن با artifact SHA-256
  `583198eaafba8e82dc6dd09a41d08215998f32a0a7ae03030a882819e239b1b4`
  قبول شد. acceptance نهایی عمداً تا routing/convergence تازه تکرار نمی‌شود.
- Bot-FI با bootstrap token مصنوعی Stage 3 start شده بود و پس از phase به restart
  افتاد. به‌جای re-plan/re-approval یا تغییر خام env، amendment تک‌کلیدی و
  evidence-bound در commit `8b8f1d97` اضافه شد؛ ۳۸ تست migration/observer/
  coordinator قبول شدند. ابزار هر تغییر غیر از `BOT_TOKEN`، تطبیق با `.env`
  canonical، image خارج از inventory، fingerprint متفاوت زنده، restart یا خطای
  Telegram را رد می‌کند. runtime env SHA-256 برابر
  `51937482825106c3397f4f5dda759e87e99c197bdba71b91bec5359646c9b704`،
  evidence canonical hash برابر
  `e4202d09eb782fb83d894b7b076ffc85a8aade9d283277d3f04e81ea0ae4a3e1`
  و evidence file SHA-256 برابر
  `bc2c65813d407ac7eccf68023ba6d1bb0b410b2f8be95f74efea831f8c80adac`
  است. `getMe` قبول و token fingerprint `d70357f9...9438` از production
  fingerprint `e3321b43...a9f7` متمایز است؛ plaintext persist/log نشد.
- apply amendment روی VPS آزمایشی Bot-FI قبل از receipt fail-closed شد: token
  معتبر بود و polling شروع شد، ولی Telegram وجود poller دوم را گزارش کرد. poller
  رقیب دقیقاً container کمپین staging قدیمی با release `3138d0c2...` و همان
  fingerprint `d70357f9...9438` روی BOT-FL/`65.109.216.187` است؛ Bot canonical
  fingerprint دیگری دارد. ابزار فقط Bot جدید روی `130.185.121.98` را متوقف کرد.
  چون BOT-FL در denylist canonical است، poller قدیمی بدون مجوز تازه متوقف نشد.
  اسکن owner-only نشان داد هر ۷۶ env معتبر staging همین یک token را دارند؛ بنابراین
  مسیر فنی بعدی یا مجوز محدود stop همان container staging قدیمی (بدون remove) است
  یا دریافت token اختصاصی staging تازه. این blocker با token/approval تکراری دور
  زده نمی‌شود.
- Production touched: `read-only only`. روی `65.109.216.187` فقط container list و
  token/release fingerprint hash خوانده شد؛ هیچ container/service/route/file روی
  آن تغییر نکرد. هیچ VPS/volume/bucket/domain ساخته، حذف یا تغییر lifecycle نکرد.

#### checkpoint اجرای Stage 4 — completion داخلی و مانع ingress عمومی — 2026-08-03

- مالک stop فقط همان poller قدیمی staging روی BOT-FL را صریحاً مجاز کرد. container
  کمپین قدیمی با release `3138d0c2...` و fingerprint staging مشترک، با timeout
  graceful متوقف شد و **remove نشد**؛ rollback آن صرفاً `docker start` همان
  container است. Bot canonical و هر credential یا workload production تغییر
  نکرد. پس از آن amendment Bot-FI به receipt canonical SHA-256
  `b6990bbfe034fc6e0b6aac3faf36839fcb1c7bc2d5330153ffea4c8cf01cbe15`
  رسید و Bot آزمایشی با همان release شروع شد.
- observation تازه هر چهار role، `role-acceptance` و سپس `global-commit` اجرا
  و verify شدند. hash سند global commit برابر
  `ae12bd249f301a6c2dc01cc222065c5a8d7f16c65debb5a0d786ced481a1eaf1` و
  summary convergence نهایی در مسیر owner-only
  `.../stage4/convergence-role-acceptance-v3/summary.json` برابر
  `5367e4de832f0d352d7552bd9d2136e92be8c80a428ef53646b76b3b9ab2d935` است.
  `finish` پس از dry-run و confirmation دقیق روی هر چهار target انجام شد؛ exact
  release همچنان `0e63a7ec1b08bef29ea199041215298a021b56ef` است.
- route عمداً همچنان روی origin سابق `65.109.220.59` نگه داشته شد. read-only
  observation جدید routing در مسیر owner-only
  `.../stage4/routing-observation-v4.json` با SHA-256
  `d1d71ed987621616b986f61f7d19f43fa78a27f19ab0c318c25d969a544c2429`
  همین وضعیت را ثبت می‌کند؛ هیچ DNS/CDN mutation انجام نشده است.
- مالک روشن کرد که آدرس رسمی staging پروژه `staging.gold-trade.ir` است و در
  این بازه برای آزمون معماری سه‌سایته آزاد است؛ `app.gold-trading.ir` فقط جایگزین
  موقت بوده است. بنابراین `FRONTEND_URL` و `PUBLIC_WEBAPP_URL` bundle اجراشده
  که هر دو `https://staging.gold-trade.ir` هستند، **درست**‌اند و replan/redeploy
  لازم نیست. در guard route نیز فقط record دقیق `staging` در zone `gold-trade.ir`
  مجاز است؛ recordهای production و namespace موقت read-only هستند.
- preflight مستقیم WebApp-FI آزمایشی نشان داد application exact release روی
  `127.0.0.1:8212` سالم است (`/` و `/health/ready` هر دو `200`)، اما host هیچ
  listener عمومی `80/443`، Nginx، Certbot یا certificate ندارد. بنابراین probe
  مستقیم `--resolve staging.gold-trade.ir:443:194.5.206.69` timeout شد و switch
  Arvan عمداً اجرا نشد.
- record رسمی `staging.gold-trade.ir` به‌صورت read-only بازخوانی شد: proxied،
  `upstream_https=https`، TTL `120` و origin فعلی `65.109.220.59` است؛ پاسخ
  public فعلی `401` Basic Auth می‌دهد. certificate origin فعلی نیز مستقیم
  read-only بررسی شد: `CN=staging.gold-trade.ir`، issuer `Let's Encrypt YE1` و
  expiry `2026-10-08T16:39:33Z` دارد. این دو مقدار boundary دقیق rollback و
  استاندارد TLS مقصد هستند.
- authoritative DNS این zone روی `j.ns.arvancdn.ir` و `r.ns.arvancdn.ir` است و
  `lego` نصب‌شده، provider `arvancloud` DNS-01 را پشتیبانی می‌کند؛ بنابراین صدور
  certificate trusted بدون تغییر origin شدنی است. ACME account محلی contact
  email ندارد؛ صدور account بدون email عمداً انجام نشد. گام بعدی فقط دریافت email
  تماس مالک، سپس DNS-01 موقتی، نصب owner-only certificate و Nginx محدود روی
  WebApp-FI است. تا آن زمان هیچ listener، package، DNS record یا route تغییر
  نکرده است.
- مسیر بعدی پیش از هر switch عمومی: (۱) تعریف exact public-origin bundle برای
  `staging.gold-trade.ir` با bundle حاضر، (۲) provision محدود Nginx/TLS روی VPS
  آزمایشی WebApp-FI و اثبات direct
  origin، (۳) dry-run و فقط سپس با authorization مستقل، switch record دقیق staging
  در Arvan و smoke عمومی. این مسیر هیچ domain یا route production را در بر
  نمی‌گیرد.
- Production touched: `no`. تنها mutation روی میزبان canonical، stop مجاز و
  reversible همان container **staging قدیمی** بود؛ هیچ container production،
  VPS/volume، bucket یا route/domain production تغییر نکرد.

#### checkpoint اجرای Stage 4 — public ingress، TLS و dry-run route — 2026-08-03

- آدرس تماس ACME فقط برای صدور account/certificate استفاده شد و در Git یا evidence
  ثبت نشده است. provider داخلی `lego` به endpoint قدیمی `.com` متصل نمی‌شد؛ hook
  محدود DNS-01 در commit `c2ebcf23` فقط TXT موقت
  `_acme-challenge.staging.gold-trade.ir` را از طریق API رسمی `.ir` ایجاد و پاک
  می‌کند. گواهی trusted صادر شد، سپس همان TXT پاک شد؛ certificate/key هر دو
  owner-only بیرون از repository نگهداری می‌شوند. SHA-256 certificate برابر
  `d03b3b640806d13be5937ec98be6d2acb6d48a34bc32729c900d07bab9d50a10`،
  CN برابر `staging.gold-trade.ir`، issuer برابر `Let's Encrypt YE1` و بازهٔ
  اعتبار `2026-08-03T04:56:59Z` تا `2026-11-01T04:56:58Z` است. renewal باید
  حداکثر تا `2026-10-01` با همان hook محدود اجرا شود؛ این مورد handoff عملیاتی
  Stage 6 است، نه gate تکراری برای switch فعلی.
- روی تنها WebApp-FI آزمایشی `194.5.206.69`، Nginx با configuration محدود commit
  `9a8efa61` نصب و پس از `nginx -t` فعال شد. فقط hostname دقیق
  `staging.gold-trade.ir` پذیرفته، TLS به loopback `127.0.0.1:8212` proxy می‌شود،
  Basic Auth روی public surface اجباری است و `/metrics` به‌صورت صریح `404` است.
  site پیش‌فرض package حذف نشد و به
  `sites-disabled/default.pre-three-site-stage4` منتقل شد. خطای نخستین `500` فقط
  از نبود execute permission برای گروه worker روی directory خصوصی htpasswd بود؛
  با `root:www-data 0750` برای directory و `root:www-data 0640` برای خود فایل
  اصلاح و reload شد. این تغییر فقط روی VPS آزمایشی انجام شد.
- guard محدود commit `9a8efa61` دو rule inbound عمومی TCP `80/443` را صرفاً به
  security group آزمایشی مشترک اروپایی با commitment
  `7145d1da80b841191428e8e1898e2f21f8367f2a61b55649a3135ddc72ca4b70`
  افزود؛ production overlap ندارد و هیچ VPS/volume/network production تغییر
  نکرد. commit `2a147f89` اجرای مستقیم guard را نیز repair کرد.
- direct-origin proof با `--resolve staging.gold-trade.ir:443:194.5.206.69` و
  credential موجود staging قبول شد: `/health/ready` برابر `200` و
  `ready=true,database_ok=true,redis_ok=true,physical_site=webapp_fi` است؛ root
  بدون credential برابر `401` و `/metrics` بدون credential برابر `404` است.
  TLS مستقیم trusted و با CN/issuer/bازهٔ اعتبار بالا match دارد. بنابراین خود
  ingress و application target برای switch آماده‌اند.
- dry-run ابزار fail-closed route (بدون `--apply`) نیز دقیقاً record
  `staging` در zone `gold-trade.ir` را بازخوانی کرد: id
  `d763b36e-ad18-44fe-84a0-9a374b9c81f4`، proxied، `upstream_https=https`،
  TTL `120` و origin پیشین `65.109.220.59`. target پیشنهادی فقط
  `194.5.206.69` است. route هنوز تغییر نکرده است؛ اعمال آن به authorization
  مستقل و صریح owner برای همین source/target نیاز دارد.
- Production touched: `no`. هیچ domain/route production، VPS/volume، bucket یا
  lifecycle تغییر نکرد؛ mutationهای این checkpoint فقط certificate hook و
  security group/Nginx روی هدف آزمایشی WebApp-FI هستند.

#### checkpoint اجرای Stage 4 — switch عمومی و مانع واقعی Tier-1 — 2026-08-03

- مالک فقط تغییر origin رکورد دقیق `staging.gold-trade.ir` از
  `65.109.220.59` به `194.5.206.69` را مجاز کرد. ابزار fail-closed ابتدا state
  پیشین را با expected origin تطبیق داد و سپس همان رکورد immutable با ID
  `d763b36e-ad18-44fe-84a0-9a374b9c81f4` را تغییر داد؛ proxy،
  `upstream_https=https` و TTL `120` ثابت ماندند. audit owner-only در
  `.../stage4/public-ingress-v1/arvan-origin-switch-audit.jsonl` با SHA-256
  `e4c6736522af924c15b37e0dcf4d0947b5141ea81b8d40767a8483bab1d71848`
  ثبت شده است. edge عمومی پس از switch با credential staging،
  `/health/ready=200` و `physical_site=webapp_fi` را داد؛ `/` بدون credential
  `401` و `/metrics` `404` ماندند.
- runner واقعی Tier-1 نقش‌های معامله با release مستقر، containerهای exact
  campaign و cleanup mapper-based زیر Writer fence اجرا شد. پیش‌پاکسازی هر ۱۳
  prefix و commodity test fixture، planned/deleted=0 بود. در اولین fixture
  business، خطای واقعی PostgreSQL ظاهر شد و run متوقف شد؛ بنابراین هیچ پذیرش
  fixture یا نتیجهٔ Tier-1 موفق ادعا نمی‌شود. evidence immutable این تلاش در
  `.../stage4/tier1-role-trading-e2e-v5/` است؛ `playwright.log` و
  `report.json` به‌ترتیب SHA-256
  `da922faa0b39c38531afb7523f684d8f500cc9b28dcc2516c1505af6771763dc` و
  `aa2a690a6eccf873b1b8045b8ea16116eca3e666f3971033bd378678bead8042` دارند.
- علت، ضعف fixture یا bypass fencing نیست: نقش برنامه `webapp_fi_app` روی
  `dr_durability_state` عمداً فقط `SELECT` دارد؛ SELECT ساده کار می‌کند، اما
  PostgreSQL برای `FOR SHARE` به privilege قفل‌کنندهٔ گسترده‌تری نیاز دارد و
  گیت دوام قبل از هر mutation با `permission denied for table
  dr_durability_state` fail می‌شود. دادن `UPDATE` به نقش برنامه مرز control
  plane را می‌شکند و رد شد.
- remediation source در commit `e00283c0` آماده و ۳۸ test مرتبط سبز است:
  گیت برنامه فقط تابع parameterless و `SECURITY DEFINER`
  `public.trading_bot_read_durability_state_for_write_gate()` را فراخوانی
  می‌کند؛ تابع با owner دیتابیس همان row را `FOR SHARE` می‌خواند،
  `search_path` ثابت دارد، `PUBLIC` و تمام runtime roleها از execute revoke
  می‌شوند و فقط role برنامه `EXECUTE` می‌گیرد. ACL جدول برای برنامه همچنان
  `SELECT` است. این commit هنوز deploy نشده و هیچ DB grant، container، route یا
  سرویس staging به‌خاطر remediation تغییر نکرده است.
- اقدام بعدی فقط با مجوز جدید مالک انجام می‌شود، زیرا release پذیرفته‌شده را
  تغییر می‌دهد: activation idempotent تابع روی هر دو DB staging WebApp-FI و
  WebApp-IR، build/deploy release جدید روی چهار role staging، re-attestation و
  اجرای دوبارهٔ Tier-1 از cleanup fence‌شده. هیچ تغییر Arvan/DNS، production یا
  lifecycle VPS در این remediation وجود ندارد.
- Production touched: `no`.

#### checkpoint اجرای Stage 4 — hotfix مرز نقش دوام و re-attestation — 2026-08-03

- سیاست مالک برای این مرحله دوباره صریح شد: SSH فقط برای اجرای فرمان روی
  مقصد و receipt کوتاه است؛ هیچ artifact/data payload نباید با `scp` میان
  Finland و Iran جابه‌جا شود و مسیر انتقال artifact باید Object Storage باشد.
  یک تلاش اولیه برای انتقال image به WebApp-IR با `scp` پیش از `docker load` یا
  هر mutation runtime متوقف شد؛ هیچ process `scp` باقی نماند و artifact جزئی
  روی IR پاک شد. image نهایی فقط با client-side encryption از Object Storage
  private/versioned staging دریافت و روی خود IR decrypt/hash-verify شد.
- artifact دقیق application hotfix با release
  `e00283c037ec5ca63340b9827768256b1c5ef144`، SHA-256 plaintext
  `db2323e6f65298e8c4a0804389e2e8096bd50de683742cce7eb247bc0e43e241`
  و اندازه `249970667` bytes در key اختصاصی campaign زیر
  `staging/fd34231d-f52e-498a-aab4-438c99d88fc5/transport/webapp-ir/hotfix/e00283c037ec5ca63340b9827768256b1c5ef144/`
  منتشر و از exact VersionId
  `SLk.xxyu-IcZIXNLuwZvgpDqw1e-a5Z` بازخوانی شد. ciphertext SHA-256 برابر
  `5e96356c5d385e4e9949dea7e62d8aea152de19ec56bf1d2295cc80aabf99cc8`
  است. هیچ bucket production استفاده یا تغییر نکرد.
- پیش از mutation، dry-run activation روی هر دو DB WebApp با `303` statement
  پذیرفته شد. سپس تنها application serviceهای چهار role با image جدید
  بازسازی شدند؛ PostgreSQL image/container، Redis، TLS و همه volumeها ثابت
  ماندند. env قبلی هر role دست‌نخورده و env hotfix جداگانه با تنها تفاوت
  `STAGING_RELEASE_SHA=e002...` برای rollback نگهداری شد. هر دو activation
  database با status `applied` و `303` statement پایان یافت.
- remediation `e00283c0` در runtime درست است: function parameterless
  `public.trading_bot_read_durability_state_for_write_gate()` در هر دو WebApp
  اجرا می‌شود؛ role برنامه هنوز روی table
  `dr_durability_state` فقط `SELECT` دارد و `UPDATE` نگرفته است. FI در epoch 1
  active با lease تازه Witness است؛ IR در epoch 1، `fenced`، بدون lease و بدون
  authority Writer است. renewalهای FI پس از deploy به Witness با `200` ادامه
  دارند.
- re-attestation یک blocker مستقل و واقعی را آشکار کرد: هر دو state دوام
  `connectivity_mode=ambiguous`، `event_journal_healthy=false`،
  `blob_journal_healthy=false` و بدون expiry تازه هستند. این وضعیت باید
  critical write را fail-closed نگه دارد. ریشه در
  `scripts/update_dr_connectivity_state.py` است: controller موجود به‌صراحت
  هر دو health flag را همواره `false` می‌نویسد و خود کد می‌گوید local journal
  مستقل/approved هنوز پیاده نشده است. بنابراین پس از رفع privilege mismatch،
  Tier-1 تجاری به خطای درستِ durability policy می‌رسد مگر آنکه evidence
  journal واقعی ساخته شود؛ هیچ grant گسترده یا bypass مجاز نیست.
- اجرای نخست Tier-1 hotfix پیش از شروع Playwright/fixture تجاری متوقف شد، چون
  listener قدیمی `127.0.0.1:8100` یک SSH tunnel بود. tunnel قدیمی تغییر یا
  حذف نشد، ولی runner متوقف و دیگر از آن استفاده نمی‌شود. cleanupهای scoped
  پیش از توقف فقط prefixهای staging را زیر Writer fence بررسی کردند؛ report
  یا pass Tier-1 ادعا نمی‌شود. credential Basic Auth قدیمیِ موجود نیز برای
  edge فعلی `401` داد و plaintext آن نمایش یا ثبت نشد.
- Decision: `G4_BLOCKED / no`. scope بعدی باید کوچک و واقعی باشد: implementation
  یک same-region event/blob journal با restore drill و controller evidence
  کوتاه‌عمر که فقط پس از اثبات بتواند health flags را true کند؛ سپس release
  جدید، re-attestation، و Tier-1 بدون SSH data path. تا آن زمان public route
  staging و writer fencing حفظ می‌شوند و critical business mutation عمداً
  frozen است.
- Production touched: `no`. هیچ VPS/volume lifecycle، route/DNS/CDN، bucket
  production یا workload production تغییر نکرد.

### Stage 4R — remediation journal دوام هم‌منطقه‌ای

#### مسئله و هدف محدود

hotfix `e00283c0` مرز دسترسی PostgreSQL را درست کرد، اما بررسی مستقیم نشان داد
controller موجود عمداً هر دو flag دوام را `false` می‌نویسد. پس Tier-1 تا اینجا
باید fail-closed بماند. هدف 4R تنها بستن این شکاف است: Writer فعال در
`webapp_fi` فقط پس از ثبت **هم‌زمان، مستقل و قابل بازیابی** journal خود روی
`bot_fi` بتواند mutation حیاتی را commit کند. `webapp_ir` receiver مستقل
هم‌منطقه‌ای ندارد؛ بنابراین در این remediation Writer نمی‌شود و flagهایش false
و fence آن پابرجا می‌ماند.

این یک gate اضافی نیست: همان شرط بنیادی RPO برای بازکردن Tier-1 است. نتیجهٔ آن
صرفاً evidence کوتاه‌عمر برای gate موجود است؛ هیچ approval، token یا تغییر route
جدیدی ایجاد نمی‌کند.

#### حدود و قراردادهای غیرقابل‌مذاکره

1. مقصد journal، PostgreSQL/volume مستقل `bot_fi` روی میزبان Finland است؛ نه
   همان DB/volume/host Writer و نه Object Storage ایران. Object Storage فقط برای
   artifact و evidence انتقال بین کشورها به‌کار می‌رود، نه برای ادعای دوام
   هم‌منطقه‌ای.
2. رخدادهای WebApp-private (از جمله Messenger) در Bot قابل projection یا
   خواندن نیستند. receiver جدید فقط envelope رمز‌شده و opaque، hash، epoch و
   checkpoint را ثبت می‌کند؛ role آن به جدول‌های business و projection دسترسی
   ندارد. کلید decrypt به receiver داده نمی‌شود.
3. commit mutation حیاتی در FI باید با یک coordinator پایدار دو‌مرحله‌ای انجام
   شود: receiver ابتدا record را `prepared` می‌کند، Writer پس از flush موفق
   transaction محلی را با GID یکتا `PREPARE TRANSACTION` می‌کند، سپس coordinator
   record را `committed` و Writer همان GID را `COMMIT PREPARED` می‌کند. بنابراین
   ACK سادهٔ پیش از commit و orphan نامشخص مجاز نیست. timeout، ACK ناسازگار/تکراری،
   قطع Bot-FI، hash mismatch، GID unresolved یا expiry evidence باید mutation را
   freeze کند؛ reconciliation فقط می‌تواند GID prepared را به commit قطعی یا
   rollback قطعی برساند.
4. blobهای `chat_files` قرارداد جداگانه دارند: پیش از پذیرش mutation مرجع blob،
   ciphertext/hash/size آن باید در journal مستقل FI قابل read-back باشد. تا
   تکمیل این قرارداد، blob flag false است و upload حیاتی باز نمی‌شود.
5. controller فقط receiptهای امضاشدهٔ همین release، site=`webapp_fi`، epoch فعال
   و restore drill تازه را می‌پذیرد و TTL حداکثر 60 ثانیه می‌دهد. هیچ command یا
   role application حق set کردن مستقیم flagها را ندارد.
6. SSH فقط اجرای command و receipt کوتاه روی مقصد است. هیچ payload، image،
   snapshot یا test fixture میان Finland و Iran با SCP/tunnel عبور نمی‌کند.
   تغییر DNS/CDN، production، lifecycle VPS/volume و secret rotation خارج از
   scope 4R هستند.

#### ترتیب اجرا

1. این طراحی، مدل تهدید، schema/ACL و rollback را در همین roadmap ثبت و commit
   می‌کنیم؛ سپس pipeline و policy فعلی را با تست‌های red مشخص می‌کنیم.
2. journal append-only Bot-FI، endpoint mTLS/authenticated و schema migration
   least-privilege را پیاده می‌کنیم. پیام opaque client-side-encrypted است و
   idempotency فقط بر `(origin_site, epoch, transaction_id, envelope_hash)`
   تعریف می‌شود.
3. hook دو‌مرحله‌ای برای event را اضافه می‌کنیم: `flush → journal prepare →
   PostgreSQL PREPARE TRANSACTION → journal commit → COMMIT PREPARED`. این به
   `max_prepared_transactions>0` نیاز دارد و lifecycle کامل GID (از جمله crash
   recovery) را تست می‌کند. مسیر async فعلی DR حذف یا bypass نمی‌شود و فقط
   transport/recovery دوردست است.
4. controller evidence را به receipt معتبر journal وصل می‌کنیم، به‌طوری که
   expiry طبیعی یا هر خطای health دوباره gate را ببندد. IR در هر حالت false/fenced
   باقی می‌ماند.
5. تست‌های unit/integration برای ACL، malformed/replayed ACK، duplicate، rollback
   local، outage Bot-FI، expiry، crash در هر مرز 2PC، reconciliation GID،
   محرمانگی WebApp payload، blob read-back و restore اجرا می‌شوند. test پاس‌شده
   بدون fault injection کافی نیست.
6. image/migration immutable ساخته و روی هر نقش با همان release attestation
   می‌شود. هر artifact لازم برای WebApp-IR فقط با Object Storage private/versioned
   و CSE منتقل می‌شود؛ هیچ artifact با SSH/SCP منتقل نمی‌شود.
7. روی staging، تنها marker مصنوعی مخصوص campaign در journal Bot-FI ثبت، read-back
   و restore در database scratch انجام می‌شود؛ سپس outage کنترل‌شدهٔ receiver
   باید freeze را اثبات کند. business data و production لمس نمی‌شوند.
8. پس از evidence تازه، Tier-1 از runner مقصد یا artifact قابل‌ردیابی اجرا
   می‌شود؛ از tunnel محلی قدیمی و Basic-Auth نامعتبر استفاده نمی‌شود. در پایان
   result و decision در همین Stage ثبت و commit می‌شوند.

#### Exit gate — G4R Journal Proven

- mutation حیاتی FI تنها با 2PC پایدار Bot-FI و receipt تازه پذیرفته می‌شود؛
- restore drill marker از journal مستقل FI hash-identical است؛
- قطع receiver یا expiry evidence فوراً mutation حیاتی FI را freeze می‌کند؛
- هیچ GID prepared یا outcome نامشخص پس از crash/reconciliation باقی نمانده است؛
- IR همچنان fenced است و journal health آن true ادعا نمی‌شود؛
- ACL receiver به business/projection داده دسترسی ندارد و تمام تست‌های قرارداد
  و Tier-1 لازم قبول‌اند.

#### Rollback

هیچ journal یا volume حذف نمی‌شود. قطع receiver، revoke کردن evidence controller
یا پایان TTL به‌تنهایی مسیر را fail-closed می‌کند. rollback release فقط سرویس‌های
4R را به image پیشین برمی‌گرداند و immutable journal برای recovery حفظ می‌شود.
route عمومی، production و lifecycle زیرساخت در rollback 4R تغییر نمی‌کند.

#### checkpoint شروع Stage 4R — 2026-08-03

- Status: `IN_PROGRESS — DESIGN COMMITTED; IMPLEMENTATION NOT YET STARTED`
- Basis: `e00283c0` روی چهار نقش deploy است و function امن read-only کار می‌کند؛
  blocker باقی‌مانده نبود journal واقعی و independent برای `webapp_fi` است.
- Observation: امکان‌سنجی read-only روی PostgreSQL واقعی `webapp_fi` نشان داد
  `max_prepared_transactions=0`، `wal_level=replica` و `synchronous_commit=on`.
  بنابراین 2PC هنوز فعال نیست و تا build/test/deploy و restart کنترل‌شدهٔ PostgreSQL
  staging، flag دوام نباید true شود.
- Production touched: `no`.

#### checkpoint implementation Stage 4R — 2026-08-03

- Status: `IN_PROGRESS — CODE/MIGRATION/RECOVERY IMPLEMENTED; STAGING DEPLOY NOT STARTED`
- Branch: `stage/three-site-staging-04-deploy`; Base: release staging فعلی
  `e00283c037ec5ca63340b9827768256b1c5ef144`؛ implementation commit:
  `d6f86634`.
- تغییر تحویل‌شده: receiver اختصاصی Bot-FI فقط ciphertext opaque و metadata
  امضاشده را با role `bot_fi_journal` نگه می‌دارد؛ schema به head
  `d9e3f5a7b2c4` رسیده و GID محلی PostgreSQL به prepare record متصل و unique
  شده است. WebApp-FI فقط در صورت feature flag صریح، `flush → remote prepare →
  PREPARE TRANSACTION → remote commit decision → COMMIT PREPARED` را اجرا
  می‌کند. پاسخ گم‌شدهٔ phase-2 به `DurabilityJournalInDoubtError` منتهی می‌شود؛
  transaction prepared حفظ می‌شود و command محدود
  `reconcile_same_region_journal_prepared_transaction.py --gid …` تنها از روی
  status امضاشدهٔ Bot آن را commit یا rollback می‌کند.
- hardening: ACK ساده هرگز evidence commit نیست؛ GID، request hash، release,
  ciphertext hash و state در پاسخ امضاشده bind می‌شوند. اگر 2PC feature فعال
  نباشد، durability gate حتی با health flag اشتباه true نیز critical write را
  با reason `same_region_two_phase_disabled` می‌بندد. مسیر async فعلی DR حذف یا
  bypass نشده و IR همچنان receiver هم‌منطقه‌ای/Writer ندارد.
- آماده‌سازی deployment: Compose فقط برای DB آزمایشی WebApp-FI ظرفیت startup
  `max_prepared_transactions` با default `32` دارد، ولی feature flag default
  `false` است. تا migration، restart کنترل‌شده، read-only attestation و drill
  واقعی انجام نشود، هیچ mutation حیاتی باز نمی‌شود.
- Commands/tests executed: مجموعهٔ گستردهٔ unittest شامل ۱۴۴ آزمون Writer,
  Witness, DR protocol/blob/failover، contract journal، migration، role-compose
  و secret boundary با `OK`؛ `python3 -m compileall` و `git diff --check` نیز
  قبول شدند؛ Alembic head دقیق `d9e3f5a7b2c4` است.
- محدودیت آزمون محلی: یک کانتینر PostgreSQL موقت `--rm` برای integration 2PC
  ساخته شد، اما Docker daemon در readiness/read-only log پاسخ به‌موقع نداد؛
  بدون اجرای test، با `docker stop` متوقف و به‌علت `--rm` حذف شد. بنابراین
  اجرای واقعی 2PC همچنان فقط روی staging کنترل‌شده و پیش از هر health=true
  اثبات می‌شود؛ این failure به‌هیچ‌وجه pass یا evidence تلقی نشده است.
- Deployed/tested release SHA: `none after d6f86634`; artifact، snapshot یا
  payloadی منتقل نشده است. DNS/CDN، production، VPS/volume lifecycle، bucket
  و secret rotation تغییر نکرده‌اند.
- Open risks: blob journal/read-back هنوز پیاده نشده، controller هنوز health
  false می‌نویسد، و `max_prepared_transactions=0` روی WebApp-FI مستقر باقی
  مانده است. پس G4R و G4 هر دو هنوز پذیرفته نشده‌اند.
- Production touched: `no`; Decision: `continue Stage 4R`.

#### checkpoint runtime remediation Stage 4R — 2026-08-03

- Status: `IN_PROGRESS — RUNTIME BLOCKER FIXED IN SOURCE; STAGING DEPLOY PENDING`.
  این checkpoint پایان Stage 4R یا پذیرش G4R نیست.
- Read-only observation از PostgreSQL واقعی `webapp_fi` و log پنج‌دقیقه‌ای worker
  نشان داد release فعال `e00283c0` روی migration `b986c7d8e0f1` است و
  `webapp_fi_dr_delivery` با خطای
  `three-site projection attempted a forbidden field on dr_event_deliveries`
  restart می‌شود. allowlist پایدار همهٔ fieldهای mutateشدهٔ delivery را داشت جز
  `first_attempt_at`; همان column در migration بعدی `a875b6c7d9e0` اضافه شده بود
  و در policy reconcile قدیمی ثبت نشده بود. این failure مستقل از journal/2PC
  است و هر claim را عمداً fail-closed می‌کرد.
- Remediation implementation: `c2c890464d6fcd300410acfe234237b61850b295`.
  migration forward-only `e0a4b6c8d1e3` فقط tuple
  `dr_event_deliveries/first_attempt_at` را، idempotent، به
  `dr_projection_field_allowlist` اضافه می‌کند. trigger، service role و هیچ
  field دیگری broaden نشده‌اند. expected head تمام ابزارهای staging و testها
  به `e0a4b6c8d1e3` تغییر کرده است.
- Verification local: `python3 -m unittest` برای ۶۷ آزمون foundation، migration
  graph/history، role plan/journal و collector با `OK`؛ `compileall`،
  `git diff --check` و `alembic heads` نیز قبول‌اند و head یکتا
  `e0a4b6c8d1e3` است. آزمون live 2PC هنوز انجام نشده و به‌عنوان evidence pass
  ثبت نشده است.
- Next controlled action: image و role bundle همین SHA فقط از Object Storage
  private/versioned و CSE به نقش‌های staging می‌رسد؛ سپس migration اول روی
  `webapp_fi` اعمال و restart worker بدون crash-loop مشاهده می‌شود. تا آن
  observation، `max_prepared_transactions` همچنان `0` و هر دو durability health
  flag `false` است؛ Tier-1 باز نمی‌شود. IR fenced، public route و production
  بدون تغییر می‌مانند.
- Production touched: `no`; artifact/data transfer via SSH/SCP: `no`; Decision:
  `continue Stage 4R with controlled staging deployment`.

#### checkpoint immutable deployment remediation Stage 4R — 2026-08-03

- Status: `IN_PROGRESS — FOUR-ROLE IMMUTABLE RELEASE DEPLOYED; 2PC/restore
  evidence NOT YET ACCEPTED`. این checkpoint پایان Stage 4R، پذیرش G4R یا
  بازشدن Tier-1 نیست.
- Deployed runtime release: `456a3765bc8dd10f2776c3dc4e38d3e3da118f9d` روی
  Bot-FI، WebApp-FI، WebApp-IR و Witness. branch همچنان
  `stage/three-site-staging-04-deploy` است. دو remediation واقعی در جریان
  deploy کشف و به‌صورت commitهای جدا ثبت شدند: `f66c4f21`، که
  `durability_journal_app.py` را به image اضافه می‌کند، و `456a3765`، که
  `ORIGIN_EXPECTED_MIGRATION_REVISION=e0a4b6c8d1e3` را به material role bind
  می‌کند. هیچ hot-fix خارج از Git اعمال نشد.
- Delivery: هر چهار bundle با CSE، bucket private/versioned
  `gold-trade-staging-three-site-dr`، read-back exact VersionId و decrypt/hash
  verify روی مقصد تحویل شد. VersionIdها به‌ترتیب Bot-FI
  `k0yExogHsGCeB4R1Y4spQj8r3GBTLa-`، WebApp-FI
  `rcGhgC6kp8OTF.YtK5s0IO4gqSQI0nj`، WebApp-IR
  `y476sM93108kFB-UEvKs9a3JkiyW71V` و Witness
  `J6p3Wu-CxG6uA7AWt37UGR7x7E.rzX` هستند. SSH فقط command نصب و receipt کوتاه
  حمل کرد (`ssh_payload_transfer=false`). bundle رمزگشایی‌شده پس از نصب پاک شد؛
  نسخهٔ encrypted و versioned Object Storage باقی مانده است.
- Runtime observation: سه PostgreSQL application role تا
  `e0a4b6c8d1e3` migrated هستند؛ WebApp-FI مقدار startup
  `max_prepared_transactions=32` را گزارش می‌کند. Bot-FI journal و هر سه DR
  receiver healthy هستند. receiver/delivery/projection روی هر سه role
  application با image exact release و `RestartCount=0` دیده شدند؛ restart-loop
  قبلی `webapp_fi_dr_delivery` دیگر مشاهده نشد. processهای `*_sync_observer`
  و `webapp_ir_convergence_exporter` one-shot/manual هستند و exit=1 آن‌ها
  failure runtime worker تلقی نشده است.
- Public staging: درخواست مستقیم HTTPS با SNI
  `staging.gold-trade.ir` به origin مجاز `194.5.206.69` برای `/` و
  `/api/config` پاسخ `401` مورد انتظار Basic Auth داد؛ مسیر public پس از
  recreate برقرار است. DNS/CDN تغییر نکرد.
- هنوز عمداً بسته است: `STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED=false`،
  flagهای controller health true نشده‌اند، marker 2PC/restore drill و outage
  freeze واقعی اجرا نشده و قرارداد blob read-back نیز evidence ندارد. پس
  mutation حیاتی، Tier-1، G4R و G4 همگی fail-closed باقی می‌مانند.
- Read-only post-deploy state: `webapp_fi` در epoch=1 و `active` است، اما
  readiness evidence آن منقضی شده است؛ `dr_durability_state` نیز
  `connectivity_mode=ambiguous`, `event_journal_healthy=false` و
  `blob_journal_healthy=false` گزارش می‌کند. `webapp_ir` در همان epoch با
  `control_state=fenced` و evidence منقضی باقی مانده است. این وضعیت با lease
  یا token جدید، update مستقیم control table، یا bypass آزمایشی تغییر داده
  نشد؛ هر drill بعدی باید ابتدا evidence واقعی controller را تولید کند.
- Production touched: `no`. هیچ route/DNS/CDN production، VPS/volume lifecycle
  یا secret rotation انجام نشد. Decision: `continue Stage 4R from the real
  2PC journal marker and restore/outage drills`.

#### checkpoint final four-role rollout and upstream remediation Stage 4R — 2026-08-03

- Status: `IN_PROGRESS — RELEASE 554503a7 DEPLOYED ON ALL FOUR ROLES; G4R/G4
  REMAIN CLOSED`. این checkpoint جای evidence تاریخی releaseهای قدیمی را
  نمی‌گیرد و به‌هیچ‌وجه پذیرش M1 یا بازشدن Tier-1 نیست.
- Source remediation: commit
  `554503a7cb6e15f0c606ab99b32437f4ead14879` (`fix(three-site): re-resolve
  private TLS upstreams`) در هر چهار Nginx role، Docker DNS resolver
  `127.0.0.11`، TTL ده‌ثانیه‌ای و variable upstream را جای lookup یک‌بارهٔ
  startup قرار می‌دهد. علت واقعی مشاهده‌شده این بود که restart کنترل‌شدهٔ
  journal، IP Docker آن را عوض می‌کرد و Nginx قدیمی به IP مرده می‌ماند؛ پاسخ
  recovery در آن پنجره `502` و fail-closed بود. 37 تست focused مربوط به
  journal، 2PC، role Compose/bundle و secret boundary با dummy env قبول شدند.
  اجرای بدون env محلی صرفاً در import `Settings` با required-env validation
  متوقف شد و test failure محسوب نشد؛ rerun با URLهای loopback dummy،
  `37 tests / OK` بود.
- Delivery release: چهار role bundle با CSE به bucket خصوصی و versioned
  `gold-trade-staging-three-site-dr` رسیدند. VersionIdهای exact به‌ترتیب
  Bot-FI `ZH0NJM1qvRcvNdfUj7vlDhE4UzcwVW7`، WebApp-FI
  `ttfjDnfehHDFK2n3InT6AUXODD.Dmg8`، WebApp-IR
  `n2EIjqMVfBMJWoAkCnGFfsndNcxMt0E` و Witness
  `vhp.HcwrSCqvV-5ePgmvRzGIshBq6rG` هستند. همهٔ receiptها
  `ssh_payload_transfer=false` دارند. build مستقیم WebApp-IR به‌علت timeout
  Docker Hub تکرار نشد: image نهایی WebApp-FI با age برای کلید WebApp-IR
  رمز شد، از همین bucket upload/read-back و سپس در مقصد load شد. VersionId آن
  `ZjrjIVJ9O0v.f306YWMI-JT.JXv-mjZ`، plaintext SHA-256
  `d56c31bf3320d47c6b67ffa2b3396fbbf78fd4de4d9eb9356c0a079f0e28fea1` و
  ciphertext SHA-256
  `b92d70c01161447bc6ed77351dbbb76f2d0181ff2dd9524e80a17ac33c8c7ba3` است.
  receipt owner-only آن در
  `.../three-site-staging-0e63a7ec-fd34231d/stage4r-554503a7/transfer-webapp-fi-to-webapp-ir-image.json`
  قرار دارد. هیچ payloadی با SCP/rsync/SSH جابه‌جا نشد.
- State-preserving rollout: در اولین اجرای release جدید مشخص شد namespace
  Compose که به SHA release وابسته است، volume تازه و خالی می‌سازد. هیچ volume
  حذف نشد و هیچ data copy مستقیمی انجام نشد. roleهای جدید با image و material
  `554503a7` اما با `-p` namespace state قبلی
  `three-site-stage4r-456a3765-{bot-fi,webapp-fi,webapp-ir,witness}` دوباره
  ایجاد شدند تا دقیقاً همان volumeهای موجود را mount کنند. این قاعده برای
  deployهای بعدی Stage 4R اجباری است تا پیش از یک migration مستقل و
  Object-Storage-backed، state را با namespace release جدید جایگزین نکند.
- Runtime evidence: Bot-FI، WebApp-FI، WebApp-IR و Witness همگی image exact
  `trading_bot_three_site_staging:554503a7cb6e15f0c606ab99b32437f4ead14879`
  را گزارش می‌کنند؛ PostgreSQL هر role healthy و migrationهای WebApp-FI/IR
  موفق‌اند؛ DR receiverهای Bot-FI، WebApp-FI و WebApp-IR healthy و `nginx -t`
  در هر role موفق است. `*_sync_observer` و
  `webapp_ir_convergence_exporter` one-shot/manual هستند و پیام
  `invoke with docker compose run` با exit=1 دارند؛ worker crash تلقی نشده‌اند.
  درخواست مستقیم HTTPS با SNI صحیح به
  `staging.gold-trade.ir` روی origin `194.5.206.69` پاسخ مورد انتظار
  `401` و TLS معتبر داد؛ DNS/CDN تغییر نکرد.
- Restart proof: recovery-status امضاشده و فقط‌خواندنی یک marker مصنوعیِ
  committed، از WebApp-FI قبل از restart journal Bot-FI `committed` بود؛ بعد
  از restart تنها container journal، healthy شدن آن و گذشت TTL resolver نیز
  همان پاسخ `committed` را داد. هیچ دادهٔ کسب‌وکاری، production، DNS/CDN یا
  lifecycle VPS/volume تغییر نکرد.
- Prior durability drill remains evidence but not acceptance: marker مصنوعی
  قبلی مسیر کامل local PostgreSQL `PREPARE TRANSACTION`، journal `PREPARE` و
  `COMMIT` امضاشده، `COMMIT PREPARED` و `pg_prepared_xacts=0` را گذراند؛ dump
  journal در scratch DB restore شد و outage کنترل‌شدهٔ journal باعث block
  fail-closed و سپس rollback/reconciliation امن شد. release جدید همان marker
  committed را در status امضاشده دید، اما این اثبات event journal به‌تنهایی
  قرارداد blob را کامل نمی‌کند.
- Still closed: `STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED=false`، health
  controller برای event/blob هنوز false، evidence readiness منقضی و blob
  journal/read-back اثبات‌نشده است. برای همین Tier-1، G4R و G4 با وجود bring-up
  موفق، **accepted نیستند**. رفع بعدی فقط با evidence واقعی controller و
  blob journal/read-back روی همین branch و release جدید انجام می‌شود، نه با
  تغییر مستقیم control table یا bypass configuration.
- Production touched: `no`; DNS/CDN mutation: `no`; VPS/volume creation or
  deletion: `no`; Decision: `continue Stage 4R with the remaining durability
  controller and blob-read-back evidence`.

#### checkpoint bounded durability-health controller Stage 4R — 2026-08-03

- Status: `IN_PROGRESS — SOURCE IMPLEMENTED AND TESTED; NO NEW RELEASE OR
  GATE ACCEPTANCE`. این checkpoint فقط blocker واقعیِ controller را رفع می‌کند؛
  هیچ state، feature flag، writer lease، DNS/CDN یا production تغییر نکرده است.
- مسئلهٔ تأییدشده: `update_dr_connectivity_state.py` عمداً فقط evidence
  connectivity را می‌نوشت و `event_journal_healthy` و
  `blob_journal_healthy` را هر بار `false` می‌گذاشت. بنابراین اجرای دوبارهٔ
  connectivity controller یا update مستقیم جدول، نمی‌توانست و نباید G4 را
  باز کند.
- تغییر source: control-plane one-shot جدید
  `webapp_fi_durability_health` و command
  `scripts/refresh_three_site_durability_health.py` اضافه شد. این command فقط
  با role `webapp_fi_control` اجرا می‌شود و نه role application دارد، نه secret
  رمزگذاری journal، نه credential Object Storage، نه Witness authority، نه
  public ingress و نه loop دائمی. دسترسی شبکه‌اش دقیقاً TLS خصوصی Bot-FI journal
  روی `webapp_fi_dr_egress` است.
- شرط ثبت health=true: controller ابتدا recovery-status زنده و MAC-verified
  Bot-FI را برای **GID مشخص** می‌خواند و فقط `committed` با release SHA فعال و
  transaction/ciphertext hash معتبر را می‌پذیرد. سپس فقط‌خواندنی، delivery
  `acknowledged` WebApp-FI→WebApp-IR را با source manifest `uploaded`،
  `acknowledgement_hash`، ciphertext identity و exact Object Storage
  `VersionId` می‌سنجد. acknowledgement مقصد پیش‌تر در receiver مبدأ با HMAC و
  receiptی که پس از decrypt/hash read-back در IR ساخته شده تأیید شده است؛ پس
  controller نه Object Storage credential می‌گیرد و نه blob را انتقال می‌دهد.
- fail-closed/time bound: evidence connectivity باید هنوز `online` و fresh باشد؛
  receipt blob حداکثر ۱۲۰ ثانیه عمر دارد؛ TTL health حداکثر ۱۲۰ ثانیه است و
  هرگز expiry connectivity را تمدید نمی‌کند. هر خطا قبل از commit باعث error
  و باقی‌ماندن gate بسته می‌شود. کنترلر به هیچ‌وجه row state را مستقیم از CLI
  یا evidence تاریخی update نمی‌کند.
- database boundary: fencing فقط `SELECT` روی `dr_blob_deliveries` و
  `dr_blob_manifests` به role control می‌دهد؛ write آن role همچنان فقط
  `dr_durability_state` و control-tableهای قبلی است. `verify_three_site_database_role_bindings`
  نیز service جدید را فقط role `control` می‌پذیرد.
- Local verification: `compileall`، `git diff --check` و ۴۵ unittest متمرکز
  (health validator، journal/2PC/reconciliation، role compose/bundle و secret
  boundary) با dummy env و `OK` اجرا شد. آزمون‌های جدید non-online/expired
  connectivity، journal stale/wrong-release، Blob stale/wrong-destination و
  VersionId missing را fail-closed بررسی می‌کنند.
- اقدام deployment بعدی (هنوز انجام نشده): immutable release این commit از
  همان CSE/Object-Storage private+versioned به چهار role می‌رود؛ fencing
  WebApp-FI دوباره اعمال می‌شود؛ سپس فقط روی staging یک marker مصنوعیِ جدید
  2PC با SHA همین release، یک Blob مصنوعی non-business با IR read-back و یک
  connectivity observation تازه تولید می‌شود. در انتها command one-shot با
  شناسه‌های کوتاه receipt اجرا خواهد شد. هیچ payloadی از SSH/SCP/rsync عبور
  نمی‌کند.
- Production touched: `no`; Object Storage/SSH payload transfer: `no`; Decision:
  `continue Stage 4R with immutable controller release and fresh evidence`.

#### checkpoint deployed bounded durability-health controller Stage 4R — 2026-08-03

- Status: `IN_PROGRESS — FOUR-ROLE RELEASE DEPLOYED; WEBAPP_FI→WEBAPP_IR
  NETWORK GATE BLOCKED; G4R/G4 NOT ACCEPTED`. این checkpoint rollout را ثبت
  می‌کند، نه پذیرش durability، Tier-1 یا M1.
- Runtime release: `94a691f4515f2f235dddb05d2997fe6bdf6d0e52`
  (`feat(three-site): attest bounded durability health`) روی Bot-FI، WebApp-FI،
  WebApp-IR و Witness نصب شد. تست source همان ۴۵ unittest focused و
  `compileall` پیش از build قبول شد؛ هیچ migration جدیدی بین `554503a7` و این
  release وجود نداشت.
- انتقال source role bundle فقط با CSE و bucket private/versioned
  `gold-trade-staging-three-site-dr` انجام شد: Bot-FI VersionId
  `VZvxGNPPDqMONCByN9Na2dikG20WeDd`، WebApp-FI
  `VsTKj5A96s5neYkla5LYvRODFneL7Xv`، WebApp-IR
  `UvWEFVqfPrA15pdY6hjSqA4Mkl8.BF8` و Witness
  `JTVkIaXGIl3NqoTz31c-.691.0fFvpx`. receiptهای owner-only در
  `.../stage4r-94a691f4/transfer-*.json` هستند؛ `ssh_payload_transfer=false`.
- `mini_app_dist_staging` چون untracked بود در Git bundle نبود. artifact آن
  روی WebApp-FI از release فعال ساخته، به Object Storage upload/read-back و
  سپس روی هر چهار source root از همان Object Storage نصب شد: SHA-256
  `753b1887627ca62fb1951b6358419a0b81e247010bafe38a15ddbb91f9d2b415`،
  5,693,440 bytes، VersionId `MyP1hn8XNdP8T-I1.ZNJS5RrCWU9D1o` و 173 file.
  هیچ SCP/rsync/SSH payloadی وجود نداشت.
- WebApp-IR image cache نیز فقط از Object Storage پر شد: app و PostgreSQL
  bundle plaintext SHA-256
  `5437c429bed85cdbdd63d187ee69774b688bd123969b1b15eed1d8ce50a3ce6f`
  (341,112,892 bytes) پس از age encryption به exact VersionId
  `3epcaMZTxSWj31i7mORclS6-FJAmGmL` upload/read-back شد. ciphertext SHA-256
  `5af161ab4fa0850594242a8048dbae7b3f453fef3d66447e466b9f3a830d926b` است.
  receipt owner-only:
  `.../stage4r-94a691f4/transfer-webapp-ir-images.json`. decrypt و `zstd --test`
  پذیرفته شد و همان archive locally به Docker load شد؛ SSH فقط URL کوتاه، hash
  و receipt حمل کرد.
- Rollout state-preserving بود: همهٔ commandها با namespace موجود
  `three-site-stage4r-456a3765-{bot-fi,webapp-fi,webapp-ir,witness}` و
  `--no-build --no-deps` اجرا شدند. PostgreSQL، Redis، TLS proxy و volumeهای
  موجود restart/recreate نشدند. فقط application workerها و Bot journal روی
  image جدید recreate شدند. `webapp_fi_db_fencing` با image جدید بدون restart
  dependency اجرا و 304 statement idempotent را با status `applied` ثبت کرد.
- Runtime verification: application image exact در هر چهار role
  `trading_bot_three_site_staging:94a691f4515f2f235dddb05d2997fe6bdf6d0e52`
  است؛ Bot receiver و durability journal، WebApp-FI/IR receiver و Witness API
  healthy هستند. WebApp-FI `/health/ready` روی loopback قبول شد. درخواست SNI
  مستقیم به `staging.gold-trade.ir` روی origin `194.5.206.69` پاسخ expected
  `401` Basic Auth همراه TLS/headerهای staging داد؛ DNS/CDN تغییر نکرد.
  `webapp_fi_durability_health` عمداً invoke نشده است.
- Private TLS پس از recreate workerها: WebApp-FI→Bot-FI و
  WebApp-FI→Witness پاسخ `200` health دادند و WebApp-IR self-proxy نیز سالم
  بود؛ بنابراین dynamic Docker resolver در proxyهای restart‌نشده، upstream IP
  جدید را درست resolve کرد. اما WebApp-FI→WebApp-IR `188.213.198.115:8443`
  timeout شد. Bot-FI→همان endpoint نیز timeout شد.
- تشخیص محدود network: listener WebApp-IR روی `188.213.198.115:8443` و
  self TLS healthy است؛ `tcpdump` 15-second روی WebApp-IR هنگام probe TCP از
  WebApp-FI هیچ SYNی ندید و Docker DNAT counter نیز صفر ماند. IP مشاهده‌شدهٔ
  خروجی WebApp-FI دقیقاً `194.5.206.69` است. Arvan API read-only نشان داد
  security group مقصد ruleهای TCP/8443 برای هر دو `194.5.206.69/32` و
  `130.185.121.98/32` دارد و security group مبدأ egress TCP باز دارد. پس
  نقص خارج از application/host firewall و در provider/network path است. مهم‌تر
  از آن، این direct event path با policy صریح انتقال payload/data بین FI و IR
  فقط از Object Storage سازگار نیست؛ rule حدسی، DNS/CDN یا production تغییر
  داده نشد و این مسیر هدف remediation شبکه قرار نمی‌گیرد.
- Still closed: `STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED=false`،
  `event_journal_healthy=false` و `blob_journal_healthy=false` باقی می‌مانند؛
  controller one-shot، marker 2PC جدید، Blob receipt جدید و connectivity
  observation fresh اجرا نشده‌اند. timeout مسیر الزامی WebApp-FI→WebApp-IR
  نیز مانع انجام آن evidenceها است. Tier-1، G4R و G4 همچنان fail-closed هستند.
- Production touched: `no`; DNS/CDN mutation: `no`; server/volume lifecycle:
  `no`; SSH/SCP/rsync payload transfer: `no`. Decision: `BLOCKED pending a
  committed Object-Storage-only FI↔IR event-plane remediation`; provider-level
  restoration of direct TCP/8443 is neither required nor authorized as a
  substitute. پس از implementation/test/deploy آن remediation، connectivity
  evidence، synthetic 2PC/blob receipts و one-shot durability controller در
  همین branch ادامه می‌یابد.

#### remediation 4R-OS — event plane بین FI و IR فقط Object Storage

- Status: `CORRECTIVE IMMUTABLE RELEASE READY — FOUR-ROLE DEPLOY/SYNTHETIC
  EVIDENCE PENDING`.
  این remediation بخشی از همان Stage 4R و همان branch است؛ VPS، DNS/CDN،
  security-group، production یا lifecycle جدیدی ایجاد نمی‌کند.
- اصلاح deploy: نخستین start کنترل‌شدهٔ `webapp_ir_dr_delivery` از release
  `8f9dea34` پیش از پردازش هر event با نبودن فایل CA در
  `/run/staging-dr-ca/ca.crt` fail-closed شد. علت، رفتار استاندارد Compose بود:
  `volumes` مختص service، `x-app.volumes` را جایگزین کرده بود. commit
  `5fd6cad3cacb56da48426a039d7e8fec08b7a535` mount گواهی را صریحاً به هر دو
  worker delivery اضافه و regression test را ثبت می‌کند. فقط همان worker IR به
  Compose/image شناخته‌شدهٔ قبلی برگردانده شد؛ database، Redis، TLS، volume،
  DNS/CDN و production تغییر نکرد و هیچ event/blob یا payload بین FI و IR
  منتقل نشد. release بعدی باید یک‌باره و immutable برای هر چهار role، فقط با
  Object Storage private/versioned و CSE، بسته‌بندی و deploy شود.
- Verification اصلاح: `31` unittest focused شامل role Compose/secret boundary/
  role و campaign bundle و Object-Storage transport با `OK` قبول شد؛ بخش
  transport با environment ساختگی و بدون secret اجرا شد. `compileall` و
  `git diff --check` نیز قبول شدند. این مشاهدهٔ health یک gate جدید نیست؛ همان
  fail-closed بودن موردنیاز، قبل از هر synthetic evidence، خطای واقعی را گرفته
  است.
- Scope دقیق: هر دو hop `webapp_fi ↔ webapp_ir` در `dr_delivery_worker` و
  receiptهای Blob در `dr_blob_worker`. خود Blob پیش‌تر فقط در Object Storage
  private/versioned بود؛ اکنون درخواست receipt و acknowledgement آن نیز همان
  مسیر را دارند. ارتباط Bot-FI↔WebApp-FI، WebApp↔Witness و تمام control/lease
  RPCهای داخلی FI خارج از تغییر هستند.
- publish/consume: record رویداد canonical با HMAC جهت‌دار و AES-256-GCM
  application keyring در کلید opaque ذخیره می‌شود. writer فقط پس از decrypt و
  read-back همان `VersionId` آن را منتشرشده می‌داند. مقصد record را از Object
  Storage poll، decrypt/hash/HMAC verify و در receiver محلی اعمال می‌کند؛ سپس
  receipt HMAC-bound به `object key + VersionId + ciphertext hash + size` را
  می‌نویسد. source فقط چنین receiptی را terminal می‌پذیرد. receipt رویداد فقط
  از `received` به `applied` یک Version جدید می‌سازد؛ event record هرگز
  overwrite نمی‌شود.
- fail-closed: object مفقود، metadata/ciphertext/plaintext hash ناسازگار،
  VersionId مفقود/تغییرکرده، JSON تکراری، key-id/source/destination/MAC نادرست
  یا acknowledgement خارج از batch دقیق، delivery را `pending/retry` نگه
  می‌دارد. ledger و source-object VersionId مرز replay/dedup این مسیرند.
- direct route removal: `webapp_fi_dr_delivery` و `webapp_fi_blobs` دیگر host
  map ایران ندارند؛ `webapp_ir_dr_delivery` و `webapp_ir_blobs` نیز host map
  فنلاند یا شبکهٔ `webapp_ir_dr_egress` ندارند. peer URL فقط قرارداد sparse
  topology برای مسیرهای دیگر است و در این hop فراخوانی HTTP نمی‌شود. port،
  rule شبکه یا bypass تازه‌ای ایجاد نشده است.
- secret boundary: credential و keyring فقط به چهار worker delivery/blob دو
  WebApp mount می‌شود؛ API، receiver public، writer-control، observer و
  durability controller credential Object Storage ندارند. verifier bundle و
  Compose این boundary و نبود route مستقیم را enforce می‌کنند.
- Source/tests: `core/dr_object_storage.py` و `core/dr_object_transport.py`
  access مشترک private/versioned و envelope/receiptهای encrypted را فراهم
  می‌کنند؛ `api/routers/dr_sync.py` receipt Blob را برای local Object Storage
  consumer refactor کرده است. `61` unittest مرتبط (transport crypto/API,
  Compose، secret boundary، role bundle و campaign bundle) با `OK` قبول شد؛
  شامل publish/read-back exact VersionId، idempotence، tamper MAC، receipt
  advance و حذف host route مستقیم است.
- Completion criterion: release immutable روی هر چهار role deploy شود، یک
  **event replay بدون mutation** از دو event قبلاً `applied` و یک Blob marker
  داخلی non-business با exact source/receipt VersionId در staging قبول شود و
  هیچ FI↔IR TCP payload/receipt مشاهده نشود. بازپخش کنترل‌شده به‌جای ساخت event
  کسب‌وکاری تازه، همان protocol/crypto/receipt واقعی را می‌آزماید و یک gate یا
  fixture غیرضروری به مسیر اضافه نمی‌کند. فقط پس از آن connectivity fresh و
  evidence 2PC/blob مرحلهٔ 4R ادامه می‌یابد.

#### checkpoint runtime repair و probe آمادهٔ 4R-OS — 2026-08-03

- Status: `IN_PROGRESS — LIVE EVENT DELIVERY RECOVERED; IMMUTABLE PROBE
  RELEASE/FOUR-ROLE ATTESTATION PENDING`. این checkpoint پذیرش 4R-OS، G4R، G4
  یا Tier-1 نیست.
- نخستین deploy `8f9dea34` روی delivery worker ایران fail-closed شد، زیرا
  `volumes` service-specific فایل CA را از `x-app` override می‌کرد. commit
  `5fd6cad3cacb56da48426a039d7e8fec08b7a535` mount صریح CA را به هر دو
  delivery worker افزود و 31 آزمون focused قبول شد. database schema، Redis،
  TLS proxy، volume، DNS/CDN و production تغییر نکردند.
- سپس یک record واقعی Object Storage تا receiver ایران رسید و نبود grant
  `dr_stream_checkpoints` برای role delivery را آشکار کرد. commit
  `61e199b12eeeb01249bc9628a8dc9f5c6686b148` فقط grantهای least-privilege
  ledger لازم برای consumer delivery را اضافه کرد؛ replay nonce، Blob grant و
  هرگونه access business به این role اضافه نشد. 49 آزمون focused و compile/diff
  check قبول شد. fencing idempotent روی هر دو WebApp با 308 statement اعمال
  شد؛ این تنها mutation database این corrective deployment است و business/schema
  data را تغییر نمی‌دهد.
- observation زنده پس از remediation: فقط `webapp_fi_dr_delivery` و
  `webapp_ir_dr_delivery` با image `61e199b1` recreate شدند و هر دو
  `running` با restart صفر هستند. ledger خلاصه نشان می‌دهد 12 delivery
  WebApp-FI→WebApp-IR در source `acknowledged` و در IR receiptها `applied`
  هستند؛ checkpoint IR برای origin WebApp-FI برابر `received=12` و
  `applied=12` است. خطای CA یا permission در log جدید دیده نشد. این evidence
  data payload یا مقدار کسب‌وکاری را ثبت نمی‌کند.
- Blob fixture قبلی وجود نداشت (هر دو WebApp count صفر برای manifest، delivery
  و receipt داشتند)؛ نبود fixture failure نیست و با دادهٔ واقعی جبران نمی‌شود.
  commit `c2eac188` ابزار bounded
  `scripts/run_stage4r_object_storage_probe.py` را افزود: event probe فقط دو
  event قبلاً applied را بدون mutation بازپخش می‌کند؛ Blob probe یک marker
  کوچک content-addressed، جدا از `chat_files` و با run-id یکتا می‌سازد. Blob
  در IR decrypt/hash-verify می‌شود و request/ack receipt نیز فقط Object
  Storage private/versioned را استفاده می‌کند. ابزار URL peer یا HTTP client
  ندارد، bucket production را نمی‌پذیرد و خارج از `staging` یا role صحیح
  fail-closed است. آزمون‌های آن به مجموعهٔ focused افزوده و نتیجهٔ محلی
  `52 tests / OK` است.
- next controlled action: release immutable شامل `c2eac188` با CSE و
  Object Storage private/versioned به چهار role می‌رسد؛ فقط WebApp-FI/IR
  commandهای one-shot probe را اجرا می‌کنند و SSH صرفاً receipt کوتاه JSON
  حمل می‌کند. سپس VersionIdهای source/event receipt/blob receipt و source
  acknowledgement ثبت می‌شوند. هیچ SCP/rsync/SSH payload، TCP FI↔IR، تغییر
  DNS/CDN، production یا lifecycle VPS/volume مجاز نیست.
- Production touched: `no`; direct FI↔IR payload/receipt TCP used: `no`;
  Decision: `continue 4R-OS with the committed Object-Storage-only probe`.

#### checkpoint completion remediation 4R-OS — 2026-08-03

- Status: `COMPLETE — OBJECT-STORAGE-ONLY FI↔IR EVENT/BLOB DATA PLANE
  ATTESTED ON FOUR-ROLE STAGING RELEASE`. این فقط completion remediation
  `4R-OS` است؛ Stage 4R همچنان `IN_PROGRESS` و G4R، G4 و Tier-1 همچنان
  بسته‌اند.
- Source repairهای حین اجرای واقعی، هر دو fail-closed و پیش از ایجاد evidence
  نادرست متوقف شدند: `cb1ca2308ac31b8395f2200ac559edb6b3738ffd` metadata
  ذخیره‌شدهٔ receipt event (object key و VersionId واقعی) را به ابزار افزود؛
  `471ab9de34ee059b05a5d26eda7938f66bd1d4a8` manifest Blob را پیش از delivery
  flush می‌کند تا foreign key PostgreSQL ترتیب ORM را حدس نزند. 53 unittest
  focused برای repair اول و 54 unittest focused برای repair نهایی با `OK`، همراه
  `git diff --check`، قبول شد. خطای نخست فقط هنگام چاپ receipt بود، اما receipt
  پیش‌تر در Object Storage ثبت شده بود؛ خطای دوم قبل از upload Blob و پیش از
  commit ledger rollback شد. هیچ business row یا fixture کسب‌وکاری ایجاد نشد.
- Release نهایی `471ab9de…` با role bundleهای CSE، bucket private/versioned
  `gold-trade-staging-three-site-dr`، read-back exact VersionId و decrypt/hash
  verify به چهار نقش رسید: Bot-FI `ZGua2dP5AO.CfPIy2IYtWmJhWW6gPh4`،
  WebApp-FI `khOYgwh2tfJdpySpTsqqH1tQM0qX.QK`، WebApp-IR
  `ky6Q7AnlWc9x6EG4JIc0pOs0ZnhmnzM` و Witness
  `cEa.Cg1W7jLWnglnB4hjCHpZsn.rVBJ`. receiptهای owner-only در
  `.../stage4r-471ab9de/transfer-*.json` هستند و همگی
  `ssh_payload_transfer=false` دارند. source هر نقش از همان bundle محلی clone و
  روی SHA دقیق checkout شد؛ image overlay نیز با `--network=none` ساخته شد.
- Runtime چهار نقش با namespace state-preserving قبلی
  `three-site-stage4r-456a3765-*` و image exact
  `trading_bot_three_site_staging:471ab9de34ee059b05a5d26eda7938f66bd1d4a8`
  بازسازی شد. Bot-FI receiver و durability journal، هر دو WebApp receiver و
  Witness healthy هستند؛ همهٔ delivery/projection/blob/effects/control/API
  workerهای موردنیاز `running` و `RestartCount=0` دارند.
- Event evidence روی همین release: دو event از قبل `applied` بدون ساخت event یا
  mutation تازه از WebApp-FI در Object Storage replay شدند. source VersionId
  `pVZ-i3J4k0ynyhiqfgom7fKrWndguID` و receipt VersionId
  `xkdNPdZFAmCw5sUWPSzwdtMpipobYeG` هستند؛ WebApp-IR MAC/decrypt/ledger apply
  را تأیید و WebApp-FI همان receipt را read/verify کرد. receipt hash
  `c519ad10c1fa33b07ed3baae2d699642a70d0d2aa38c4bb5d799a4791998978f` است.
- Blob evidence روی همین release: marker داخلی non-business با content hash
  `e51971429dd92514e3391d3de0b9f7c4310066647eb79999d2c574ea3b52ace7` و 196
  bytes، جدا از `chat_files`، در WebApp-FI ساخته شد. source Blob VersionId
  `9cLl6wD.u22bqv1NkBdAyCXFVCPQpgh` است. WebApp-IR همان شیء را مستقیم از
  Object Storage decrypt/hash-verify کرد و receipt/ack VersionId
  `yG.tpmZobpBxDJvsE3LMhRC0wTZHbze` را نوشت. ledger FI اکنون `acknowledged` و
  hash تأییدیهٔ مشترک
  `69b57b7d6c52d82658c7f1998ff321beb68f50b498c53822340f40a41adc4a33` دارد؛
  receipt IR نیز موجود و به همان acknowledgement bound است.
- Boundary اثبات‌شده: payload event، Blob و receipt فقط از Object Storage
  private/versioned عبور کردند. SSH (و relay ایران) فقط command کوتاه و receipt
  JSON حمل کرد؛ SCP/rsync/SSH payload و TCP payload/receipt مستقیم FI↔IR صفر
  بود. DNS/CDN، security group، VPS/volume lifecycle، دادهٔ production و
  scriptهای production deployment تغییر نکردند.
- Exception عملیاتی که جداگانه باقی می‌ماند: `bot_fi_bot` با image نهایی
  `RestartCount=11` دارد، زیرا `BOT_TOKEN` فعال staging طول 80 دارد اما از نظر
  validator Telegram نامعتبر است. یک candidate معتبر فقط در material owner-only
  قدیمی `stage4/bot-token-amendment-v1/bot-fi.runtime.env` وجود دارد، ولی بدون
  تصمیم صریح owner به release فعال promote نشد و مقدار هیچ tokenی چاپ نشد. این
  مورد، G4 عمومی را باز نگه می‌دارد؛ completion 4R-OS را به pass ساختگی تبدیل
  نمی‌کند.
- Remaining real work، نه gate افزوده: انتخاب/نصب token معتبر staging، marker
  2PC و recovery/outage drill واقعی، observation connectivity تازه و اجرای
  controller bounded durability-health برای evidence تازهٔ event/blob. تا آن
  زمان `STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED=false`، G4R/G4 و Tier-1
  fail-closed باقی می‌مانند.
- Production touched: `no`; Decision: `close remediation 4R-OS and continue
  Stage 4R from the valid staging Bot token and remaining durability evidence`.

#### checkpoint staging Bot token transfer — 2026-08-03

- Status: `COMPLETE — OWNER-AUTHORIZED STAGING BOT TOKEN TRANSFERRED AND
  BOT-FI STABLE`. این checkpoint فقط blocker عملیاتی Bot-FI را می‌بندد؛ Stage
  4R، G4R، G4 و Tier-1 هنوز به‌دلیل durability/2PC evidence پذیرفته نشده‌اند.
- material قدیمیِ owner-only
  `stage4/bot-token-amendment-v1/bot-fi.runtime.env` با `getMe` رسمی Telegram
  تأیید شد. amendment فقط `BOT_TOKEN` را نسبت به role env پایه تغییر داد؛
  `runtime_env_sha256=69451fd44760d34db3cfc4d02548daa051910d4ffd0f23bf73297e2babc9ca18`،
  `token_sha256=d70357f9a1e4a42f902564812e622be00049428ade6bd53a03c32823ecc99438`
  و Telegram identity fingerprint همان material قبلی است. مقدار secret در هیچ
  log یا roadmap ثبت نشد.
- role bundle جدید با CSE به همان bucket private/versioned رسید و مقصد exact
  VersionId `lOd4ETlQE5Y4hiUKUz-l5RgeGz27c.U` را read-back/decrypt/hash کرد.
  bundle قبلی روی Bot-FI حذف نشد و در مسیر superseded با نام
  `stage4r-bot-fi-471ab9de-pre-token-amendment.tar` نگه‌داری شد تا rollback
  recoverable باشد. انتقال payload با SSH/SCP انجام نشد.
- روی Bot-FI فقط `bot-fi.env` از archive جدید نصب و فقط `bot_fi_bot` با همان
  Compose namespace و image دقیق `471ab9de…` force-recreate شد. مشاهدهٔ نهایی:
  `running=true`، `RestartCount=0`، env fingerprint برابر material آماده‌شده و
  در دو دقیقهٔ پس از start هیچ `TokenValidationError`، `TelegramConflictError`
  یا traceback دیده نشد. receiver، journal و workerهای دیگر restart نشدند.
- نتیجه: restart loop ناشی از token نامعتبر بسته شد؛ این مورد دیگر blocker
  staging health نیست. Production، DNS/CDN، VPS/volume lifecycle و scriptهای
  production deployment همچنان untouched هستند.
- Remaining real work، نه gate افزوده: marker واقعی 2PC و recovery/outage drill،
  connectivity observation تازه و controller bounded durability-health برای
  event/blob evidence. `STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED=false` و
  G4R/G4/Tier-1 تا تکمیل همین evidenceها fail-closed می‌مانند.
- Production touched: `no`; Decision: `continue Stage 4R with durability
  evidence and do not reopen Tier-1 yet`.

### Exit gate — G4 Staging Published

- هر چهار role exact release SHA را گزارش می‌کنند؛
- FI lease معتبر دارد و تنها Writer است؛
- IR Writer نیست و داده standby آن با snapshot/event boundary منطبق است؛
- مسیر عمومی staging healthy و production route بدون تغییر است؛
- smokeهای Tier-1 روی release فعال قبول و error/backlog بحرانی صفر است؛
- گیت دوام مسیر business mutation بدون اعطای DML روی control table به role
  برنامه اجرا می‌شود؛
- rollback به source staging قدیمی dry-run و قابل اجراست.

پایان این stage برابر **M1 — Staging Published** است.

### Rollback

route staging به source قبلی، fence هر دو WebApp جدید، stop projectهای جدید و
restart فقط سرویس‌هایی که در freeze evidence فعال بوده‌اند. volume جدید حذف
نمی‌شود.

### گزارش پایان Stage 4

- Status: `IN_PROGRESS — PUBLIC ROUTE SWITCHED; ACL HOTFIX DEPLOYED; TIER-1 BLOCKED BY MISSING SAME-REGION DURABILITY JOURNAL EVIDENCE`
- Branch: `stage/three-site-staging-04-deploy`
- Base / implementation / deployed SHA:
  `0e63a7ec1b08bef29ea199041215298a021b56ef / e00283c037ec5ca63340b9827768256b1c5ef144 / application services on all four roles = e00283c037ec5ca63340b9827768256b1c5ef144`
- Role health and writer term:
  `four fresh observations, role-acceptance, global-commit and finish passed; WebApp-FI remains the accepted epoch-1 Writer and WebApp-IR remains fenced standby.`
- Smoke and parity results:
  `convergence and private readiness passed; public staging.gold-trade.ir now returns authenticated /health/ready=200, unauthenticated /=401 and /metrics=404. The first Tier-1 business fixture exposed the durability-gate database-privilege defect before acceptance; its run stopped and is not counted as a pass.`
- Evidence paths and SHA-256:
  `owner-only .../stage4/controller-role-acceptance-v1/evidence/global-commit-global-commit.json = ae12bd249f301a6c2dc01cc222065c5a8d7f16c65debb5a0d786ced481a1eaf1; .../stage4/convergence-role-acceptance-v3/summary.json = 5367e4de832f0d352d7552bd9d2136e92be8c80a428ef53646b76b3b9ab2d935; .../stage4/public-ingress-v1/arvan-origin-switch-audit.jsonl = e4c6736522af924c15b37e0dcf4d0947b5141ea81b8d40767a8483bab1d71848; .../stage4/tier1-role-trading-e2e-v5/report.json = aa2a690a6eccf873b1b8045b8ea16116eca3e666f3971033bd378678bead8042.`
- Production touched: `no`
- Deviations / open risks:
  `the owner confirmed staging.gold-trade.ir as the authorized staging route and the exact CDN origin switch is complete. WebApp-FI ingress/TLS is proven. e00283c0 is deployed and fixes the PostgreSQL row-lock privilege mismatch without broadening application-table DML. Re-attestation then proved the deeper blocker: same-region event/blob durability evidence is not implemented, and the existing controller deliberately writes both health flags false. A stale local SSH tunnel was discovered and is excluded from further testing; Iran-bound artifact transport is Object Storage only. Certificate renewal is due by 2026-10-01 and is an operational handoff item.`
- Rollback verified:
  `yes for the completed private topology; route rollback is exactly staging.gold-trade.ir back to 65.109.220.59, target data is retained, and the stopped stale staging poller remains recoverable by start-only rollback.`
- Decision / next stage:
  `G4 not yet accepted / no; the closed durability-gate function is already applied and e00283c0 is deployed. Implement and independently restore-drill a real same-region event/blob journal evidence path, then deploy/re-attest its immutable release and rerun fenced Tier-1 without an SSH data path. No Arvan/DNS, production, VPS or lifecycle action is in scope.`

---

## Stage 5 — qualification ریسک‌محور failover، recovery و failback

### هدف

اثبات invariantهای حیاتی معماری با مجموعه کوچک و غیرتکراری از سناریوهای
واقعی، بدون الزام Full Matrix صدوده‌سناریویی.

### Branch

`stage/three-site-staging-05-failover`

### matrix اجباری M2

هر سناریو باید حداقل دو بار از baseline clean اجرا شود:

1. **Witness path loss from FI:** FI تا safety deadline و سپس fail-closed؛
2. **Asymmetric partition:** وقتی FI renew می‌کند، IR اجازه acquire ندارد؛
3. **Witness unavailable to both:** هیچ Writer جدیدی ایجاد نمی‌شود؛
4. **Controlled FI drain:** IR فقط پس از expiry، term بعدی را acquire می‌کند؛
5. **Lost response/retry:** request id یکسان باعث term یا effect تکراری نمی‌شود؛
6. **IR-active mode:** عملیات Tier-1 روی IR انجام و به journal/event تبدیل می‌شود؛
7. **Object Storage delay/outage:** durability policy write بحرانی را طبق قرارداد
   متوقف می‌کند و داده ناقص apply نمی‌شود؛
8. **Duplicate/out-of-order event:** cursor، dedupe و transaction hash صحیح است؛
9. **Process restart:** restart Witness، control agent و Writer term را جعل نمی‌کند؛
10. **Failure during promotion:** هر دو WebApp fenced و سیستم safe-unavailable است؛
11. **Recovery convergence:** event، business hash و blob parity قبول است؛
12. **Controlled failback:** IR drain، FI term بعدی، route staging و job authority
    به ترتیب درست بازمی‌گردند.

Tier-1 business flowها در normal، IR-active و post-failback:

- login/session؛
- ایجاد، مشاهده، اجرا، انقضا و لغو offer/trade؛
- account/user limit و block؛
- notification و Telegram delivery بدون duplicate؛
- messenger message و upload/blob؛
- admin mutationهای حساس.

### معیارهای اندازه‌گیری

- هم‌پوشانی دو Writer: صفر؛
- term jump یا term reuse: صفر؛
- mutation بدون DR event برای جدول authoritative: صفر؛
- event conflict حل‌نشده: صفر؛
- backlog پایان recovery: صفر؛
- business fingerprint drift: صفر؛
- blob hash mismatch: صفر؛
- duplicate external effect: صفر؛
- production mutation: صفر.

### Exit gate — G5 DR Qualified

- تمام ۱۲ سناریو دو بار قبول شده‌اند؛
- rollback پس از حداقل یک failure تزریق‌شده اثبات شده است؛
- failover و failback با یک journal پیوسته و قابل verify انجام شده‌اند؛
- هیچ skip، residue یا mismatch بحرانی باقی نمانده است؛
- M2 evidence manifest به release SHA متصل است.

پایان این stage برابر **M2 — Staging Qualified** است.

### Rollback

در هر ambiguity، هر دو WebApp fence می‌شوند و route فعلی staging بدون تغییر
می‌ماند تا تصمیم recovery جداگانه گرفته شود. auto-failback ممنوع است.

### گزارش پایان Stage 5

- Status: `NOT_STARTED`
- Branch: `stage/three-site-staging-05-failover`
- Base / implementation / deployed SHA: `pending`
- Scenario repetitions and results: `pending`
- Writer terms and route transitions: `pending`
- Evidence paths and SHA-256: `pending`
- Production touched: `no`
- Deviations / open risks: `pending`
- Rollback verified: `pending`
- Decision / next stage: `pending / no`

---

## Stage 6 — Queue decision، soak و handoff هدف اول

### هدف

تثبیت staging پس از M2، تصمیم مستقل درباره Queue-v1 و تحویل یک runbook کوتاه
عملیاتی برای استفاده روزانه.

### Branch

`stage/three-site-staging-06-acceptance`

### اقدامات

1. حداقل شش ساعت soak با workload staging شامل یک بازه normal، یک failover
   کنترل‌شده و recovery؛ endurance ۲۴ساعته به pre-production منتقل می‌شود.
2. بررسی alert، log، audit chain، lease renewal، backlog، disk و resource usage.
3. تعیین نتیجه Queue-v1:
   - اگر Queue خارج از هدف معماری پایه است، staging با legacy پذیرفته و Queue در
     roadmap مستقل ادامه می‌یابد؛
   - اگر Queue برای acceptance ضروری است، activation release باید یک commit
     مستقیم باشد که فقط readiness constant را از `False` به `True` تغییر دهد.
4. در حالت activation، exact activation SHA tag و deploy می‌شود؛ envهای owner و
   cutover فقط پس از preflight Bot staging تغییر می‌کنند.
5. closure documentation commit بعد از activation مجاز است، اما release مورد
   استفاده در evidence همان activation SHA باقی می‌ماند، نه tip مستنداتی branch.
6. تولید runbook روزانه کوتاه برای health، renew، fence، promote، failback و
   rollback.
7. تولید فهرست blockerهای production بدون شروع production roadmap.

### Exit gate — G6 Staging Accepted

- soak شش‌ساعته بدون split-brain، drift، backlog یا resource incident است؛
- وضعیت نهایی Queue صریح و قابل rollback است؛
- runbook روزانه و ownerهای عملیات مشخص‌اند؛
- release SHA، image identity، inventory و evidence index نهایی ثبت شده‌اند؛
- تمام stage closureها در همین سند تکمیل و committed هستند؛
- هیچ production mutation انجام نشده است.

### Rollback

برای Queue، بازگشت به legacy فقط با drain و runtime gate معتبر انجام می‌شود.
برای معماری، rollback Stage 4 همچنان تا تصمیم cleanup نهایی حفظ می‌شود.

### گزارش پایان Stage 6

- Status: `NOT_STARTED`
- Branch: `stage/three-site-staging-06-acceptance`
- Base / implementation / deployed SHA: `pending`
- Soak window and workload: `pending`
- Queue decision and release SHA: `pending`
- Final evidence index: `pending`
- Production touched: `no`
- Deviations / open risks: `pending`
- Rollback verified: `pending`
- Decision: `pending`
- Production roadmap authorized: `no؛ نیازمند تصمیم جداگانه مالک`

---

## 6. Stop conditions مشترک

در هر stage، رخداد یکی از موارد زیر ادامه کار را متوقف می‌کند؛ branch جدید ساخته
نمی‌شود و مشکل روی همان stage ثبت می‌شود:

- worktree dirty یا release SHA مبهم؛
- استفاده یا احتمال استفاده از production credential/volume/domain/bucket؛
- بیش از یک Writer یا ناتوانی در اثبات term؛
- clock خارج از محدوده یا NTP نامعتبر؛
- backup بدون restore drill؛
- image/content identity متفاوت؛
- تست mandatory failed/skipped؛
- migration head چندگانه یا schema drift؛
- event gap، parity drift یا blob corruption؛
- journal ناقص، تغییرکرده یا غیرقابل resume/rollback؛
- host headroom ناکافی؛
- هر command یا فایل خارج از scope stage؛
- نیاز به production mutation.

رفع stop condition با commit و evidence در همان branch انجام می‌شود. اگر رفع آن
دامنه roadmap را عوض کند، stage `BLOCKED` ثبت و تصمیم مالک درخواست می‌شود.

---

## 7. تعریف نهایی Done برای هدف اول

هدف «انتشار معماری سه‌سایته روی staging» فقط وقتی تمام است که:

- G0 تا G6 قبول شده باشند؛
- M1 و M2 evidence موجود و hash-bound باشد؛
- چهار role staging exact SHA یکسان و identity معتبر داشته باشند؛
- FI/IR writer transition دوبار بدون overlap اثبات شده باشد؛
- داده و Blob پس از recovery همگرا باشند؛
- rollback هنوز قابل اجرا باشد؛
- Queue state صریح باشد؛
- production دست‌نخورده باقی مانده باشد؛
- همه گزارش‌های پایان stage در همین سند تکمیل و commit شده باشند.

پس از این نقطه، production readiness، Full Matrix کامل، چهار میزبان disposable،
endurance ۲۴ساعته، production shadow و DNS/CDN cutover در roadmap مستقل تعریف
می‌شوند و هیچ‌کدام به‌طور ضمنی از پذیرش staging مجاز نمی‌شوند.
