# بخش ۵ — برنامه اجرایی مستندات و Handoff

وضعیت طراحی: `APPROVED`

وضعیت اجرا: `AUTHORIZED_CODEX_GATED`

منبع معنا: [Master Plan](../MASTER_PLAN.md#بخش-۵--مستندات-کامل-قابل-نگهداری-و-قابل-اجرای-ai)

ترتیب ماشینی: [`execution-order.yaml`](../execution-order.yaml)

شناسه‌های `P5-*` در Master Plan موضوع‌های انسانی‌اند. اجرای مستندات فقط با
`DOC-1..DOC-10` و بدون ساخت source-of-truth موازی انجام می‌شود.

## معماری اطلاعات نهایی

```text
README.md
docs/
  INDEX.md
  architecture/
  contracts/
  product/
  operations/runbooks/
  testing/
  refactor/two-server/
  adr/
  archive/
```

- متن canonical فارسی است و identifier، schema، field و command انگلیسی می‌ماند؛
  ترجمهٔ کامل موازی ساخته نمی‌شود.
- هر topic دقیقاً یک source of truth با `status`, `owner`, `scope`, `applies_to`,
  `reviewed_at`, `review_triggers` و `supersedes` دارد.
- generated evidence، test output و log مستندات نیستند و وارد Git نمی‌شوند.
- سند موجود فقط بعد از انتقال و trace همهٔ محتوای منحصربه‌فرد `MERGE/ARCHIVE/DELETE`
  می‌شود؛ فایل `.bak`، stub منسوخ و archive بی‌انتها ممنوع است.

## سناریوهای مالک

### توسعه‌دهنده یا Agent تازه وارد می‌شود

1. از root `README.md` به `docs/INDEX.md` می‌رود و topology، واژگان و source of truth را می‌بیند.
2. `doctor/bootstrap/dev-up/test-fast` روی checkout تمیز بدون production credential اجرا می‌شود.
3. codebase map، typed config، command reference و database/migration guide مسیر واقعی کد را نشان می‌دهند.
4. هیچ دستور به فایل بیرون repo، نام تاریخی سرور یا دانش شفاهی وابسته نیست.

### حادثه یا قطعی رخ می‌دهد

1. Dashboard وضعیت را با vocabulary مشترک نشان می‌دهد و runbook دقیق را لینک می‌کند.
2. runbook قبل/بعد، prerequisite، زمان/downtime، step ID، انتظار dashboard/log، stop/abort و recovery دارد.
3. Codex Final Reviewer receipt موردی می‌دهد؛ Coordinator/Ansible/controller stepها را
   اجرا می‌کنند. اقدام Dashboard برای انتقال Writer/DNS همچنان انسانی است و deploy به
   Writer/DNS دست نمی‌زند.
4. receipt و incident evidence خارج Git و با retention مصوب ذخیره می‌شود.

### Cursor یک Stage را اجرا می‌کند

1. Skill به Master Plan، YAML و Stage Card route می‌کند و clean baseline را ثبت می‌کند.
2. هر Worker فقط یک dependency-ready Stage و diff محدود آن را اجرا می‌کند؛ Worker
   نویسندهٔ دوم فقط با Pairing Receipt مجاز است.
3. success، failure و rollback tests و evidence واقعی ثبت می‌شوند؛ fixture-only برای High/Critical کافی نیست.
4. Cursor گزارش فارسی می‌سازد اما self-approval نمی‌کند؛ فقط Codex Final Reviewer
   می‌تواند commit دقیق را `COMPLETE` کند.
5. production/migration/DNS/Writer/secret/destructive/legacy-retirement تا receipt
   موردی Codex در hard stop می‌ماند.

### سند یا قرارداد تغییر می‌کند

1. PR اثر مستندی را اعلام می‌کند؛ contract schema و generated reference از source code ساخته می‌شوند.
2. CI سریع در ≤۶۰ ثانیه metadata، link، stable ID، authority uniqueness، path، secret، schema drift
   و Mermaid را می‌سنجد؛ audit خارجی/staleness/archive asynchronous است.
3. change بدون update source of truth یا تست مرتبط merge-ready نیست.

## Task Cardهای Cursor

| Stage | دامنه | خروجی اجباری | Gate خروج |
| --- | --- | --- | --- |
| `DOC-1` | information architecture و catalog | root map، `docs/INDEX.md` و document registry | هر topic یک authority؛ status/owner/review/supersession قابل جست‌وجو |
| `DOC-2` | معماری canonical | overview، topology، authority/state machine، ownership، sync، Market و security docs | هر service/job/side effect/stream/scenario به code/contract/ADR/test trace شود |
| `DOC-3` | contract documentation | machine registry برای schema/version/owner/producer/consumer/ordering/dedupe/retention/test | evolution از expand→compatible consumers→backfill→verify→contract؛ unknown quarantine |
| `DOC-4` | runbookهای عملیات | provisioning/deploy/hotfix/partition/migration/rollback/writer/reconnect/repair/restore/model/rotation/incident/retirement | هر runbook scenario، permission، bounded steps، evidence، abort و recovery drill دارد |
| `DOC-5` | behavior catalog | glossary، roles، feature-surface matrix و lifecycleهای offer/request/trade/auth/Market/message/admin | protected behavior، approved defect و unresolved ambiguity جدا؛ Web/Bot و FI/IR صریح |
| `DOC-6` | test/evidence documentation | Business/Deployment matrices و requirement→invariant→scenario→test→evidence trace | High/Critical کامل و non-waivable؛ evidence immutable/redacted و خارج Git |
| `DOC-7` | dashboard/observability docs | control-plane guide، signal dictionary، alert catalog و Grafana guide | `STALE/UNKNOWN` سبز نیست؛ peer unreachable≠failed؛ alert owner/runbook/test دارد |
| `DOC-8` | developer/operator docs | getting-started، code map، local env، config، commands، DB، debug، hygiene و contribution | fresh checkout بدون secret/hidden file از مسیر تست‌شده بالا می‌آید |
| `DOC-9` | governance و cleanup | `KEEP/MERGE/ARCHIVE/DELETE` inventory، trace map، CI policy و retention ledger | unique content پیش از حذف منتقل؛ canonical file بدون تاریخ؛ stale authority صفر |
| `DOC-10` | plan packaging و Cursor handoff | Master Plan، Governance، YAML، ledger، Assignment/Review templates، Skill و scoped Rules | یک Stage در هر Worker، حداکثر دو نویسنده با Pairing Receipt، integration ترتیبی و Codex-gated؛ جزئیات را duplicate نکند |

## Evidence retention

| نوع evidence | retention |
| --- | --- |
| PR و nightly | ۳۰ روز |
| اجرای دو میزبان | ۹۰ روز |
| release evidence | عمر release +۱۸۰ روز |
| restore/DR | یک سال |
| incident | closure +۹۰ روز |
| spool محلی ACKشده | حداکثر ۲۴ ساعت |

## معیار پایان بخش ۵

- Coordinator، Worker و Codex Reviewer از روی منابع یکسان rehearsal یکسان را بدون
  دانش شفاهی یا تأیید مرحله‌ای کاربر اجرا کنند.
- link/path/command/schema checks روی checkout تمیز سبز باشند.
- search داخلی هیچ سند منسوخ را به‌عنوان authority جاری برنگرداند.
- تمام تصمیم‌ها، gapها، receiptها و Stageها با ID یکتا و وضعیت واقعی trace شوند.
- حذف سند، artifact یا branch فقط با retention و receipt محدود Codex انجام شود.
