# بخش ۳ — برنامه اجرایی Market، Parser و مدل تخمین

وضعیت طراحی: `APPROVED`

وضعیت اجرا: `NOT_AUTHORIZED`

منبع معنا و سناریوهای محصول: [Master Plan](../MASTER_PLAN.md#بخش-۳--تطبیق-parser-و-مدلهای-تخمین-با-معماری-جدید)

ترتیب و dependency ماشینی: [`execution-order.yaml`](../execution-order.yaml)

شناسه‌های `P3-*` داخل Master Plan دسته‌بندی انسانی موضوع‌ها هستند. Cursor فقط
Task Cardهای اجرایی `MKT-0..MKT-13` این سند را با ترتیب YAML اجرا می‌کند.

## نتیجه‌ای که باید ساخته شود

- capture، parse، canonical archive، projection و estimator یک pipeline روشن با
  مرزهای ذخیره‌سازی و ownership مشخص باشند.
- آفر و معاملهٔ Web و Bot به‌صورت canonical event وارد مدل شوند؛ `origin_surface`،
  `origin_site`، `home_site` و policy snapshot تاریخی حفظ شود.
- همهٔ Factها، exact consumed-inputها، model bundleها و output receiptهای لازم میان
  دو سایت همگام شوند؛ cache یا state موقت مدل merge نشود.
- Finland تنها training/promotion authority باشد. هر دو سایت inference را روی یک
  bundle امضاشده اجرا کنند؛ Shadow هیچ‌گاه خودکار promote نشود.
- کم‌شدن داده خروجی را قطع نکند؛ uncertainty و بازه به‌صورت bounded و monotonic
  افزایش یابد. فقط خرابی فنی، schema/unit ناسازگار، artifact نامعتبر، کالای پشتیبانی‌نشده
  یا نبود baseline معتبر اجازهٔ `NO_OUTPUT` دارد.

## قرارداد ذخیره و حذف

| داده | محل حقیقت | نگهداری نهایی |
| --- | --- | --- |
| canonical Fact پذیرفته‌شده و lineage/revision | PostgreSQL archive هر سایت + sync | دائمی؛ بدون حذف خودکار |
| G1/G2، کانال خصوصی آب‌شده، دو کانال آب‌شده، دلار هرات، USDT و بورس آینده | همان archive canonical | دائمی |
| آفر/معاملهٔ committed وب و بات که ورودی مدل است | Product event archive | دائمی و deduplicated |
| raw موفق source | capture store محلی | ۷ روز، فقط بعد از projection و ACK/checkpoint دوطرفه |
| raw مربوط به quarantine حل‌نشده | capture/quarantine | بدون حذف خودکار |
| raw حل‌شدهٔ quarantine | capture/quarantine | ۳۰ روز مگر incident باز |
| envelope تحویل‌شدهٔ transport | Object Storage transport | ۷ روز بعد از ACK+snapshot؛ حذف آن Fact را حذف نمی‌کند |
| exact input/output مرجع Production و گزارش نهایی | archive/model registry | دائمی |
| promoted model bundle | model registry هر دو سایت | دائمی |
| ورودی و جزئیات Shadow بدون reference | shadow namespace | ۹۰ روز |
| metric روزانهٔ Shadow | metrics archive | یک سال |
| research cache | research namespace | ۳۰ روز |
| debug / warning-error / incident evidence | log/evidence storage | ۱۴ روز / ۹۰ روز / بسته‌شدن incident +۹۰ روز |

Market، parse و estimate دادهٔ محرمانه محسوب نمی‌شوند؛ نام و Telegram ID نیز در این
دامنه نیازمند encryption/HMAC اجباری نیستند. امضا، hash، TLS و immutability برای
integrity اجباری‌اند. secret، OTP، session و credential اصلاً وارد این pipeline نمی‌شوند.

## سناریوهای مالک

### اینترنت ایران متصل است

1. Iran بورس/IME و USDT را محلی capture و parse می‌کند.
2. Finland منابع تلگرامی، G1/G2، آب‌شده/XAU قابل‌دسترسی و Product Events وب/بات را می‌سازد.
3. هر دو طرف Factهای canonical را از transport ایران مبادله می‌کنند؛ lag هدف ≤۳۰ ثانیه است.
4. Finland مدل Production را اجرا می‌کند و Iran همان bundle/input را `VERIFY_ONLY` اجرا می‌کند.
5. اختلاف input hash یا output tolerance، `MARKET_READY` را قرمز می‌کند ولی Writer را
   خودکار عوض نمی‌کند.

### اینترنت ایران قطع می‌شود و Iran Web Writer است

1. Finland Bot و ingestهای قابل‌دسترسی خود را بدون محدودیت ادامه می‌دهد.
2. Iran بورس/IME، USDT و Product Events وب ایران را ادامه می‌دهد.
3. آخرین bundle مصوب freeze می‌شود؛ Iran با `IR_CONTINUITY_BASE` inference می‌دهد.
4. اگر مالک بعداً مسیر معتبر آب‌شده یا XAU فراهم کرد، adapter ایزوله آن را به
   `IR_CONTINUITY_ENRICHED` اضافه می‌کند؛ نبود این adapter شکست پایه نیست.
5. کاهش event، بازه و reason code را بزرگ می‌کند؛ `DEGRADED/REFERENCE_ONLY` حق رد آفر ندارند.

### اتصال بازمی‌گردد

1. Iran Writer باقی می‌ماند و هیچ reconnect نقش را عوض نمی‌کند.
2. eventها از آخرین contiguous checkpoint منتقل، verify، dedupe و apply می‌شوند.
3. یک cutoff مشترک pin می‌شود؛ هر دو سایت exact input set را بازسازی می‌کنند.
4. outputهای divergent دوران قطعی merge نمی‌شوند؛ از Factهای همگرا دوباره تولید می‌شوند.
5. فقط matching input hashes، artifact/schema برابر، output parity و نبود gap/conflict
   `MARKET_READY` می‌سازد. سپس handover انسانی بخش ۲ ممکن می‌شود.

### event خراب یا فشار منابع

- poison event به quarantine می‌رود؛ eventهای مستقل ادامه و dependent facts در `REVIEW` می‌مانند.
- اول Shadow، سپس research/backfill/compaction متوقف می‌شوند؛ Web/API/Bot/Redis و
  durable capture بالاترین اولویت را دارند.
- هیچ فشار دیسکی اجازهٔ حذف permanent، unACKed، referenced یا unresolved data نمی‌دهد.

## Task Cardهای Cursor

| Stage | تغییر محدود | خروجی اجباری | Gate خروج |
| --- | --- | --- | --- |
| `MKT-0` | inventory کل collector/parser/store/model/consumer و freeze replay baseline | current-state graph، source registry، behavior corpus، resource baseline و gap ledger | تمام sourceها و مسیرهای legacy/Shadow/Production نام‌گذاری و هیچ ambiguity پنهان نباشد |
| `MKT-1` | schema و storage canonical Fact، revision، lineage و data class | migration expand-only، schema contract و permanent archive | Decimal/UTC/unit، stable ID، dedupe و point-in-time semantics با property test ثابت شود |
| `MKT-2` | capture owner و raw lifecycle برای همهٔ منابع | adapters، receipt/availability bounds و local durable spool | یک owner برای هر session/source؛ crash/redelivery بدون loss یا duplicate apply |
| `MKT-3` | parser transport-neutral و deterministic | versioned parser API، corpus واقعی و rejection taxonomy | replay یک input/version نتیجهٔ برابر؛ ambiguity هرگز synthetic resolve نشود |
| `MKT-4` | Product outbox برای offer/trade وب و بات | canonical event با origin/home/authority/policy snapshot | committed business event دقیقاً یک Fact؛ model suggestion self-evidence نشود |
| `MKT-5` | projection/materialization جدا از ingest | idempotent projectors، watermarks و rebuild command | projection از archive خالی قابل‌بازسازی و live path رفتار فعلی را حفظ کند |
| `MKT-6` | archive/retention/cleanup class-aware | registry ماشینی، dry-run cleanup و deletion receipt بدون payload | permanent classes حذف‌ناپذیر؛ TTL فقط پس از prerequisites و reference scan |
| `MKT-7` | sync دوطرفهٔ Fact و exact input identity | stream/outbox/inbox/ACK/checksum/repair integration | gap، out-of-order، duplicate، partition و reconnect با RPO محلی صفر |
| `MKT-8` | feature/input contract و weighting نسخه‌دار | input manifest، feature schema و anti-feedback guards | هر snapshot از exact Fact IDs/cutoff قابل‌بازتولید و surface policy قابل‌ردیابی باشد |
| `MKT-9` | Production/Shadow/training/promotion roles | signed model bundle و role guard | training/promotion فقط Finland؛ inference برابر؛ Shadow read-only/low-priority/no-auto-promote |
| `MKT-10` | profiles، freshness decay، widening و Price Guard | `FULL_CONNECTED`, `IR_CONTINUITY_BASE/ENRICHED` و reason codes | سکوت widening monotonic؛ technical failure fail-open برای guard و fail-closed برای publication |
| `MKT-11` | reconnect barrier و model parity | cutoff protocol، input/output hash comparator و dashboard state | divergent outputs regenerate؛ `MARKET_READY` فقط پس از parity واقعی |
| `MKT-12` | backpressure، capacity، backfill و restore/rebuild | quotas، schedulers، resume journal و archive restore drill | foreground سالم؛ bounded catch-up؛ restart بدون reset checkpoint یا delete evidence |
| `MKT-13` | کامل‌ترین matrix علمی/عملیاتی و promotion gate | machine matrix، backtest، shadow، fault/load/restore evidence | صفر High/Critical gap، صفر behavior drift و تأیید انسانی جدا برای Product promotion |

## معیار پایان بخش ۳

- تمام `MKT-0..MKT-13` با evidence واقعی `COMPLETE` باشند.
- هر source حداقل یک trace غیرصفر capture→parse→Fact→archive/sync→input→snapshot داشته باشد.
- replay، rebuild، restore و reconnect parity از دادهٔ واقعی و cutoff ثابت سبز باشند.
- resource thresholdهای نهایی فقط از baseline/load/replay تعیین و در decision ledger ثبت شوند.
- هیچ activation مدل، deploy یا تغییر Product در نتیجهٔ تأیید طراحی این سند مجاز نیست.
