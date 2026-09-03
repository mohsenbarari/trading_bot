# بخش ۴ — برنامه اجرایی Deploy و Release

وضعیت طراحی: `APPROVED`

وضعیت اجرا: `AUTHORIZED_CODEX_GATED`

منبع معنا: [Master Plan](../MASTER_PLAN.md#بخش-۴--ریفکتور-deploy-و-رفع-نواقص-تحویل)

ترتیب ماشینی: [`execution-order.yaml`](../execution-order.yaml)

شناسه‌های `P4-*` در Master Plan دسته‌بندی انسانی مشکلات خط فعلی‌اند. شناسه‌های
اجرایی و گزارش‌پذیر این بخش فقط `DPL-1..DPL-10` هستند.

## قرارداد زمانی تأییدشده

| عملیات | شروع ساعت | پایان ساعت | SLO |
| --- | --- | --- | --- |
| Hotfix بدون schema مخرب | commit تأییدشده | artifact با وضعیت `READY` | ≤۱۰ دقیقه |
| همان Hotfix | commit تأییدشده | Finland Writer سالم | ≤۱۵ دقیقه |
| همان Hotfix در اتصال سالم | commit تأییدشده | همگرایی release دو سایت | ≤۲۰ دقیقه |
| Release عادی R0/R1 | merge روی `main` | سلامت و همگرایی هر دو سایت | ≤۳۰ دقیقه |
| Web/API switchover | شروع activation | سرویس سالم slot جدید | downtime ≤۳۰ ثانیه |
| Web/API rollback | تصمیم rollback | slot قبلی سالم | ≤۲ دقیقه |
| rollback کامل R0/R1 | تصمیم rollback | تمام componentهای در scope سالم | ≤۵ دقیقه |

routine security gate، retry، build و smoke داخل ساعت اندازه‌گیری می‌شوند. توقف ساعت
فقط برای pause صریح Codex Final Reviewer یا outage خارجی ثبت‌شده مجاز است و نتیجه همچنان با برچسب
`SLO_EXTERNAL_BLOCK` گزارش می‌شود؛ زمان پنهان نمی‌شود. R2/R3 پیش از اجرا سقف و downtime
مخصوص خود را می‌گیرند و به زور داخل ۳۰ دقیقه قرار نمی‌گیرند.

## معماری Release

- GitHub Actions روی runner موقت exact commit و lockfile را test/build می‌کند و یک
  artifact immutable با image digest، frontend digest، migrations، config schema،
  SBOM، provenance و Ed25519 signature می‌سازد. CI هرگز deploy نمی‌کند.
- Operations Dashboard هر سایت فقط release با وضعیت `READY` را به controller محلی
  می‌دهد. Dashboard و emergency CLI یک state machine و journal دارند.
- هر میزبان Blue/Green Web/API دارد. Bot، Telegram Executor، jobs و sync به‌جای دو
  نسخهٔ هم‌زمان، handoff تک‌مالک دارند.
- Ansible فقط host، Docker/proxy/firewall/layout/monitoring/controller/config mount و
  cacheهای سیستم را idempotently provision/maintain می‌کند. عامل انسانی روی shell
  سرورها command اجرا نمی‌کند؛ با این حال mutation اولیه receipt موردی Codex می‌خواهد.
- production هیچ `pip install`، `npm install/build` یا dependency download ندارد.
  Finland registry و bundle امضاشده را می‌گیرد؛ Iran `Active/Previous/Candidate` را
  cache می‌کند و از Object Storage ایران یا import دستی digest-verified تغذیه می‌شود.
- یک artifact digest روی هر دو سایت اجرا می‌شود؛ تفاوت role فقط typed config و secret
  projection کمینه است. secret در Git/artifact/sync/Object Storage/log/dashboard نیست.

## سناریوهای اجرای کنترل‌شده

### Release عادی و اینترنت متصل

1. merge به `main`، CI را فعال می‌کند؛ test scope از diff و risk class استخراج می‌شود.
2. CI یک artifact می‌سازد، امضا می‌کند و با evidence معتبر `READY` می‌نماید.
3. Coordinator پس از receipt لازم، `Plan` را می‌بیند و activation را از مسیر
   controller/Ansible شروع می‌کند؛ انتقال Writer/DNS در Dashboard همچنان انسانی است.
4. controllerها mutex می‌گیرند؛ Iran Standby ابتدا stage/smoke/commit می‌شود.
5. Finland slot غیرفعال را آماده و smoke می‌کند؛ proxy سپس با downtime ≤۳۰ ثانیه عوض می‌شود.
6. singleton jobs و Bot/Executor با journal و ownership fence handoff می‌شوند.
7. دو dashboard digest/config/schema/health برابر را نشان می‌دهند؛ deploy هیچ DNS یا
   Writer change انجام نمی‌دهد.

### Hotfix فوری

1. risk classifier فقط affected gates و exploit regression را اجرا می‌کند؛ gate امنیتی
   حذف نمی‌شود ولی Full Matrix نامرتبط قبل از activation مانع نیست.
2. artifact immutable و rollback slot باید آماده باشد؛ schema مخرب quick lane ندارد.
3. در اتصال سالم standby-first اجرا می‌شود. اگر peer واقعاً unreachable باشد، site جاری
   می‌تواند R0/R1 compatible را محلی deploy و `DIVERGED_SAFE` ثبت کند.
4. post-activation Full Matrix اجرا می‌شود؛ failure آن promotionهای بعدی را freeze و
   incident/follow-up می‌سازد، نه اینکه evidence را حذف کند.

### Deploy ایران هنگام قطع اینترنت بین‌الملل

1. controller ایران release manifest را از cache محلی یا Object Storage داخلی می‌گیرد.
2. signature، digest، config/schema compatibility و rollback presence محلی verify می‌شود.
3. هیچ dependency از GitHub، registry خارجی، PyPI، npm یا apt در زمان deploy دانلود نمی‌شود.
4. اگر Iran Writer است، Web/API Blue/Green محلی اجرا می‌شود؛ SMS Executor فقط نسل Writer
   فعال است و Telegram همچنان وجود ندارد.
5. dashboard ایران تمام stage/smoke/commit/rollback و metricهای محلی را نشان می‌دهد؛ peer
   Finland به‌صورت `UNREACHABLE/STALE` نمایش داده می‌شود، نه `FAILED` جعلی.
6. پس از reconnect، release parity پیش از هر Writer transfer اجباری است.

### اولین نصب ایران

1. پس از receipt موردی Codex Final Reviewer، inventory/fingerprint/capacity verify و
   Ansible dry-run می‌شود.
2. Ansible host hardening، container runtime، proxy/TLS، path/volume، controller، dashboard،
   metrics/logs و cache داخلی را از نسخه‌های pin‌شده نصب می‌کند.
3. artifact و config typed جدا وارد می‌شوند؛ secretها per-service و root-only projection دارند.
4. Iran در `DARK_STANDBY` و product write-blocked بالا می‌آید؛ هیچ DNS/Writer/Telegram side
   effect ندارد. bootstrap و پذیرش آن فقط `P2-11` است.

### Migration و failure

- R0 docs/config-safe و R1 app-compatible rollback خودکار دارند.
- R2 additive فقط با runner امضاشده، lock timeout، idempotency و backup اجرا می‌شود؛ در
  partition فقط اگر قبلاً `partition_safe` اثبات شده باشد.
- R3 destructive فقط در اتصال کامل، پس از expand/backfill/soak، WAL پیوسته، base backup
  روزانه، restore proof و receipt موردی downtime از Codex اجرا می‌شود. failure آن forward-fix
  انسانی یا restore دقیق دو سایت است؛ auto downgrade schema ممنوع است.
- قطع SSH، شکست registry/bucket، digest mismatch، health failure، disk pressure یا owner
  تکراری state معلوم دارد: قبل از commit، slot فعلی دست‌نخورده می‌ماند؛ بعد از commit
  compatible smoke failure rollback محدود را فعال می‌کند.

## Task Cardهای Cursor

| Stage | دامنه | خروجی اجباری | Gate خروج |
| --- | --- | --- | --- |
| `DPL-1` | audit تمام entrypointها و risk classifier R0-R4 | graph خط فعلی، timing/waste report، typed change plan | هر step ورودی/خروجی/privilege/timeout/retry/idempotency/rollback دارد؛ ambiguity scope را بزرگ می‌کند |
| `DPL-2` | artifact immutable و Blue/Green/single-owner runtime | signed release manifest، slots و rollback pin | build once، same digest، no production build/install، compatible failure rollbackable |
| `DPL-3` | evidence reuse و gate expiry | evidence graph و policy engine | drift evidence را باطل کند؛ security=۲۴h، environment=۵m، live ownership/sync=۳۰s |
| `DPL-4` | migration runner و backup/recovery | expand/backfill/contract metadata و restore receipts | startup migration حذف؛ R3 بدون synchronized sites/backup/approval ناممکن |
| `DPL-5` | controller/journal دو میزبان | idempotent `plan/stage/smoke/commit/rollback/reconcile` state machine | retry/resume امن؛ یک mutex؛ peer stale از failure جدا؛ deploy Writer/DNS را لمس نکند |
| `DPL-6` | typed config و least-privilege secrets | schema، Ansible inventory projection و redacted diff | missing critical config activation را block کند؛ secret هیچ مسیر ممنوعی را طی نکند |
| `DPL-7` | GitHub Actions و dashboard activation | CI pipeline، `READY` registry، controller API و audit UI | CI فقط build؛ dashboard فقط READY؛ emergency builder محدود/audited |
| `DPL-8` | supply chain، offline Iran cache و OS patch policy | pinned dependency/bases، SBOM/provenance/vulnerability policy و caches | integrity/secret/reachable Critical block؛ scanner outage reuse ≤۲۴h؛ production offline-installable |
| `DPL-9` | Business/Deployment Matrix، fault/load/restore و SLO | machine matrix و immutable evidence | صفر High/Critical gap؛ RPO/owner/duplicate invariants و تمام SLOهای بالا سبز |
| `DPL-10` | shadow legacy، provisioning، cutover و retirement | Ansible plans، atomic cutover journal، quarantine/retirement ledger | legacy تا cutover تنها authority؛ بعد از first new write بازگشت DNS به DB قدیم ممنوع؛ حذف فقط پس از تمام gates |

## پاکسازی و نگهداری Release

- `Active`, `Previous` و release مرتبط با incident هرگز auto-delete نمی‌شوند.
- سایر releaseهای موفق ۳۰ روز، failed/partial buildها ۲۴ ساعت و SBOM/security summaryها
  ۱۸۰ روز نگهداری می‌شوند؛ evidence release تا عمر release +۱۸۰ روز باقی می‌ماند.
- cleanup ابتدا dry-run، bytes قابل‌بازیابی، exclusions و آخرین backup restore-tested را
  گزارش می‌کند. script، alias، `.bak` یا release قدیمی دائمی جای retention policy نیست.

## معیار پایان بخش ۴

- هر `DPL-1..DPL-10` evidence و rollback واقعی دارد و هیچ deploy path موازی/دستی پنهان نیست.
- deploy متصل، partitioned Iran/Finland، first install، hotfix، migration، rollback، restore
  و failureهای transport/controller/disk در matrix سبز باشند.
- دو release عادی، یک hotfix ≤۱۰ دقیقه، یک rollback، یک partition/reconnect و یک restore
  پیش از حذف legacy path اجرا شده باشند؛ production هرکدام receipt موردی Codex می‌خواهد.
