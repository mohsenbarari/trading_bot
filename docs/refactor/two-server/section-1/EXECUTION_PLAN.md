# بخش ۱ — برنامه اجرایی ادغام Finland

وضعیت طراحی: `APPROVED`

وضعیت اجرا: `NOT_AUTHORIZED`

شرح انسانی و Task Cardها: [Master Plan](../MASTER_PLAN.md#بخش-۱--ادغام-bot-و-webapp-روی-finland-primary)

ترتیب ماشینی: [`execution-order.yaml`](../execution-order.yaml)

این بخش باید behavior-preserving باشد. تغییر topology حق تغییر API، متن کاربر،
offer/request/trade lifecycle، overtime، tier policy، notification، Bot callback، job
schedule یا side effect را ندارد. defect کشف‌شده ثبت می‌شود و فقط در change set مستقل
اصلاح می‌گردد.

## Stage index

| Stage | خروجی اصلی | سند evidence |
| --- | --- | --- |
| `P1-00` | Current-State Architecture و Behavior Baseline | [`stages/P1-00.md`](stages/P1-00.md) و [`evidence-index.md`](evidence-index.md) |
| `P1-01` | پاکسازی branch/worktree/path/artifact/backup با retention | [`07-repository-cleanup-manifest.md`](07-repository-cleanup-manifest.md) |
| `P1-02` | vocabulary و typed config مستقل از نام سرور تاریخی | Master Plan Task Card |
| `P1-03` | یک runtime واحد Finland با service ownership صریح | inventoryها و compose/runtime evidence |
| `P1-04` | PostgreSQL/Redis مشترک و حذف sync داخلی دو Finland | dataflow/ownership و migration contract |
| `P1-05` | rehearsal idempotent ادغام داده و media | merge receipts، conflict ledger و restore proof |
| `P1-06` | staging یکپارچه و ۲۴ ساعت soak | full behavior/resource/fault evidence |
| `P1-07` | cutover یک‌باره به Finland Primary | پنجره ≤۹۰ دقیقه، interruption ≤۴ دقیقه و atomic journal |
| `P1-08` | closure و حذف topology قدیمی | quarantine/retention/backup-tested deletion ledger |

## ترتیب تصمیم‌گیری انسانی

1. `P1-00` هر رفتار و ownership فعلی را با code/runtime/evidence ثابت می‌کند.
2. مالک gapهای رفتار را به `PRESERVE`, `APPROVED_DEFECT` یا `UNRESOLVED` طبقه‌بندی می‌کند.
3. فقط پس از صفرشدن ambiguity بحرانی، repository/config/runtime/data rehearsal ساخته می‌شود.
4. staging و soak باید parity Web/Bot/API/job/data/resource را ثابت کنند.
5. `P1-07` production permission جدا می‌خواهد؛ تصویب این برنامه مجوز cutover نیست.
6. `P1-08` پس از Iran admission، drill، retention و backup replacement قابل اجراست.

## اسناد ممیزی موجود

- [`01-current-finland-architecture.md`](01-current-finland-architecture.md)
- [`02-runtime-inventory.md`](02-runtime-inventory.md)
- [`03-dataflow-and-ownership.md`](03-dataflow-and-ownership.md)
- [`04-surface-policy-matrix.md`](04-surface-policy-matrix.md)
- [`05-feature-parity-contract.md`](05-feature-parity-contract.md)
- [`06-current-drift-register.md`](06-current-drift-register.md)
- [`00-stage-ledger.md`](00-stage-ledger.md)

این اسناد baseline قبلی‌اند؛ Cursor در آغاز `P1-00` باید آن‌ها را با SHA جاری `main`
refresh کند و evidence قدیمی را بدون verification معتبر فرض نکند.
