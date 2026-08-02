# نقشه راه اجرایی انتشار معماری سه‌سایته روی Staging

وضعیت سند: `Stage 0 / در حال تثبیت`

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
- عدم تغییر production DNS/CDN/domain در این roadmap؛
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
- تغییر Arvan production origin یا دامنه `coin.gold-trade.ir`؛
- migration داده production به staging؛
- فعال‌سازی Queue-v1 پیش از اثبات معماری پایه در حالت legacy؛
- اجرای benchmarkها یا سناریوهای تکراری که invariant جدیدی را پوشش نمی‌دهند.

این موارد حذف دائمی از برنامه production نیستند؛ فقط نباید هدف اول را مسدود
کنند.

### 3.4 نقاط تصمیم انسانی

برای کل مسیر فقط این تصمیم‌های انسانی مستقل باقی می‌مانند:

1. پذیرش scope و Stage 0؛
2. اجازه شروع mutation میزبان‌های staging در Stage 3B و deploy همان release در
   Stage 4؛
3. اجازه اجرای failover/failback برنامه‌ریزی‌شده Stage 5؛
4. تصمیم مستقل Queue-v1 در Stage 6، فقط اگر activation انتخاب شود.

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

- Status: `IN_PROGRESS`
- Branch: `roadmap/three-site-staging-release`
- Base SHA: `9105264000f51b480eb88f1f80845fd7608bd6b2`
- Implementation commits: `در closure ثبت می‌شود`
- Changes delivered: `در closure ثبت می‌شود`
- Commands/tests executed: `در closure ثبت می‌شود`
- Passed / failed / skipped: `در closure ثبت می‌شود`
- External evidence: `ندارد`
- Production touched: `no`
- Deviations: `در closure ثبت می‌شود`
- Open risks: `در closure ثبت می‌شود`
- Rollback verified: `در closure ثبت می‌شود`
- Decision: `pending`
- Next stage authorized: `no`

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

- Status: `NOT_STARTED`
- Branch: `stage/three-site-staging-01-baseline`
- Base SHA: `pending`
- Implementation commits: `pending`
- Deployed/tested release SHA: `ندارد`
- Changes delivered: `pending`
- Commands/tests executed: `pending`
- Passed / failed / skipped: `pending`
- External evidence paths and SHA-256: `pending`
- Production touched: `no`
- Deviations / open risks: `pending`
- Rollback verified: `pending`
- Decision: `pending`
- Next stage authorized: `no`

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

- Status: `NOT_STARTED`
- Branch: `stage/three-site-staging-02-validation`
- Base SHA / implementation commits: `pending`
- Deployed/tested release SHA: `pending`
- Changes delivered: `pending`
- Commands/tests executed: `pending`
- Passed / failed / skipped: `pending`
- Evidence paths and SHA-256: `pending`
- Production touched: `no`
- Deviations / open risks: `pending`
- Rollback verified: `pending`
- Decision / next stage: `pending / no`

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

### بخش B — آماده‌سازی isolation با authorization مرحله

1. ساخت یک inventory planned و سپس measured برای چهار role.
2. اختصاص disk/mount مستقل staging و cgroup aggregate محدود.
3. تثبیت Compose project، volume prefix، network، port و evidence root مستقل.
4. ساخت credentialهای staging-only و اثبات عدم برابری با production.
5. ساخت bucket/prefix versioned خصوصی staging و تست upload/download encrypted.
6. ساخت DNS/TLS مخصوص staging؛ production domain تغییر نمی‌کند.
7. آماده‌سازی dedicated staging Telegram bot/token یا ادامه بدون Bot عمومی.
8. گرفتن backup از source staging موجود و اجرای restore drill مستقل.
9. آماده‌سازی rollback manifest برای بازگرداندن source staging قبلی.
10. ایجاد یک staging operations session محدود به release روی Witness؛ receiptها
    از همان session صادر می‌شوند و TOTP برای هر زیرمرحله تکرار نمی‌شود.

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

- Status: `NOT_STARTED`
- Branch: `stage/three-site-staging-03-host-readiness`
- Base SHA / implementation commits: `pending`
- Tested release SHA: `pending`
- Host inventory summary: `pending`
- Commands/tests and results: `pending`
- Evidence paths and SHA-256: `pending`
- Production touched: `no`
- Deviations / open risks: `pending`
- Rollback verified: `pending`
- Decision / next stage: `pending / no`

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
14. route فقط دامنه staging به WebApp-FI؛
15. اجرای smokeهای login، offer، trade، notification، messenger و upload؛
16. تأیید backlog صفر یا bounded و convergence اولیه؛
17. ثبت global staging commit evidence.

هر command تغییردهنده ابتدا dry-run و سپس با confirmation همان plan اجرا می‌شود.
Confirmationهای هم‌معنی ادغام می‌شوند، ولی fencing و ownership هرگز bypass
نمی‌شوند.

### Exit gate — G4 Staging Published

- هر چهار role exact release SHA را گزارش می‌کنند؛
- FI lease معتبر دارد و تنها Writer است؛
- IR Writer نیست و داده standby آن با snapshot/event boundary منطبق است؛
- مسیر عمومی staging healthy و production route بدون تغییر است؛
- smokeهای Tier-1 قبول و error/backlog بحرانی صفر است؛
- rollback به source staging قدیمی dry-run و قابل اجراست.

پایان این stage برابر **M1 — Staging Published** است.

### Rollback

route staging به source قبلی، fence هر دو WebApp جدید، stop projectهای جدید و
restart فقط سرویس‌هایی که در freeze evidence فعال بوده‌اند. volume جدید حذف
نمی‌شود.

### گزارش پایان Stage 4

- Status: `NOT_STARTED`
- Branch: `stage/three-site-staging-04-deploy`
- Base / implementation / deployed SHA: `pending`
- Role health and writer term: `pending`
- Smoke and parity results: `pending`
- Evidence paths and SHA-256: `pending`
- Production touched: `no`
- Deviations / open risks: `pending`
- Rollback verified: `pending`
- Decision / next stage: `pending / no`

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
