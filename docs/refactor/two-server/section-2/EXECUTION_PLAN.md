# بخش ۲ — برنامه اجرایی Iran Standby و Web Writer

وضعیت طراحی: `APPROVED`

وضعیت اجرا: `NOT_AUTHORIZED`

شرح کامل سناریوها و Task Cardها: [Master Plan](../MASTER_PLAN.md#بخش-۲--iran-standby-و-انتقال-کنترلشدهٔ-web-writer)

ترتیب ماشینی: [`execution-order.yaml`](../execution-order.yaml)

## invariants غیرقابل مذاکره

- فقط انسان Writer را از یکی از دو Dashboard مستقل تغییر می‌دهد؛ lease، TTL نقش،
  auto-failover یا network-triggered promotion وجود ندارد.
- DNS مسیر کاربر است، نه authority. مقصد فقط پس از source drain/fence، Fence Receipt،
  DNS verification و gateهای direction-specific فعال می‌شود.
- Finland همواره تنها Telegram Executor است و Bot با تغییر Web Writer محدود نمی‌شود.
- SMS Executor روی هر دو سایت وجود دارد ولی فقط current Web Writer/generation اجرا می‌کند.
- Object Storage ایران transport است، نه database، Writer، lock یا backup.
- reconnect نقش را تغییر نمی‌دهد. Failback تا `FULL_SYNC`, role-relevant release parity
  و `MARKET_READY` بسته است.
- هر دو Dashboard username/password/TOTP، بدون IP allowlist و session ۲۴ساعته دارند.

## Stage index

| Stage | نتیجه | مانع اصلی |
| --- | --- | --- |
| `P2-00` | registry کامل SQL/Redis/file/object و authority/data class | `UNKNOWN` مجاز نیست؛ field حساس حداقلی و session/OTP محلی |
| `P2-01` | event stream نسخه‌دار با sequence/aggregate version/outbox/inbox/ACK | timestamp ordering و LWW مالی ممنوع؛ rejection checkpoint را جلو نمی‌برد |
| `P2-02` | transport امضاشده، immutable و قابل resume در Object Storage ایران | app delete ندارد؛ class حساس AEAD؛ Market غیرحساس encryption اجباری ندارد |
| `P2-03` | bootstrap snapshot+cutoff+replay و parity هم‌مرز | target تا اتمام write/side-effect ندارد؛ `FULL_SYNC` مستقل از `MARKET_READY` |
| `P2-04` | manual handover، generation، fence و receipt chain | دو Writer یا force بدون Emergency Fence Bundle ناممکن |
| `P2-05` | home-site، quota reservation و conflict/quarantine policy | پول/موجودی/trade LWW ندارد؛ origin Web/Bot تغییرناپذیر |
| `P2-06` | دو Operations Dashboard مستقل با local control journal | peer unreachable≠failed؛ stale/unknown سبز نیست؛ ۲FA و audit اجباری |
| `P2-07` | Arvan DNS plan/apply/verify با TTL دائمی ۳۰ ثانیه | token فقط Finland و least-privilege؛ provider panel fallback انسانی و reconciled |
| `P2-08` | state machine اتصال/partition/reconnect | restart/link event نقش را عوض نمی‌کند؛ mismatch fail-closed |
| `P2-09` | OTP/session/notification/Messenger continuity | Product session نسل قبل invalid؛ provider side effect duplicate نشود؛ final media sync شود |
| `P2-10` | Full Matrix حداکثری روی میزبان‌های واقعی بدون VM اضافه | هر High/Critical intersection driver و evidence واقعی دارد |
| `P2-11` | پذیرش یک‌باره Iran به‌عنوان production standby | product-write-blocked، no Telegram proof، bootstrap/parity و soak هفت‌روزه |
| `P2-12` | drill واقعی `FI→IR→FI` | DNS واقعی، data کنترل‌شده، Iran Writer ۶۰–۹۰ دقیقه، handover downtime ≤۴ دقیقه |

## سه سناریوی مرجع

### اتصال عادی

Finland Web Writer و Telegram owner است؛ Iran read-only standby، collector داخلی،
receiver و Shadow را اجرا می‌کند. lag هدف ≤۳۰ ثانیه و هر دو Dashboard sequence/ACK/
gap/backlog/checksum/release/schema/model state را مستقل نشان می‌دهند.

### قطع اینترنت

انسان Finland را drain/fence می‌کند، receipt را به Iran می‌رساند، DNS را با API آروان
تغییر و verify می‌کند و سپس Iran را در generation بعدی Writer می‌نماید. Finland Bot
فعال می‌ماند؛ Iran با SMS و داده/مدل continuity محلی Web را سرو می‌کند.

### اتصال مجدد

Iran Writer باقی می‌ماند؛ backlog هر دو جهت apply/reconcile می‌شود. پس از release/schema/
business/media/Market parity، انسان Iran را drain/fence، DNS را به Finland verify و
Finland را generation بعدی Writer می‌کند. هیچ مرحلهٔ خودکار نقش را تغییر نمی‌دهد.

## Gate پایان بخش ۲

- تمام identity/generation/receiptها durable، امضاشده، auditشده و restart-safe باشند.
- full matrix دو Writer، duplicate Bot/Executor/Job، loss/duplicate apply و oversell را صفر کند.
- dashboardها وضعیت واقعی و علت block هر action را نشان دهند.
- `P2-11` و `P2-12` فقط با مجوز production/drill جدا اجرا شوند.
