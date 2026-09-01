# پلن جامع ریفکتور معماری دو سروره

وضعیت: `DRAFT — در انتظار بازبینی و تأیید مالک`
شاخهٔ تدوین: `plan/two-server-refactor-v1`
مبنای کد: `main` در `d60e81b6d591c4d0a1096123a65719a3e679ced4`
مجری آینده: Cursor Agent، مرحله‌به‌مرحله و فقط پس از تأیید این سند
دامنهٔ این شاخه: مستندات و طراحی؛ نه deploy، نه DNS، نه تغییر نقش، نه دادهٔ تولید

این سند برنامهٔ مرجع مهاجرت پروژه از دو میزبان فنلاند به یک Finland Primary و
یک Iran Standby است. هدف فقط جابه‌جایی سرور نیست؛ ساختار runtime، همگام‌سازی،
مدل‌های Market، deploy، نگهداری فایل و مستندات باید به‌صورت یک سامانهٔ واحد
بازطراحی شوند.

پنج بخش اصلی سند ثابت‌اند، اما شمارهٔ بخش‌ها ترتیب مکانیکی اجرا نیست. ترتیب
واقعی را «امواج اجرا» و dependency هر Stage تعیین می‌کند. هر موضوع جدید باید
در بخش مفهومی درست قرار گیرد و سپس بر اساس dependency وارد موج مناسب شود.

---

## قرارداد خواندن و اجرای این سند

### مخاطب انسانی

برای هر مرحله ابتدا سناریوی واقعی، سپس تغییر مورد انتظار، خطر، معیار موفقیت و
راه بازگشت توضیح داده شده است. مالک باید بتواند بدون خواندن کد بفهمد چه چیزی
عوض می‌شود و در صورت شکست چه اتفاقی می‌افتد.

### مخاطب AI Agent

هر Stage یک واحد مستقل اجراست. Cursor در یک درخواست فقط یک Stage را اجرا
می‌کند، مگر اینکه این سند صریحاً چند Stage را یک change set اتمیک اعلام کند.
عبارت «کد نوشته شد» یا «تست واحد سبز شد» برای complete بودن کافی نیست.

هر Stage فقط وقتی `COMPLETE` است که هم‌زمان این موارد وجود داشته باشد:

1. خروجی‌های اعلام‌شده ایجاد شده باشند.
2. تست موفقیت، شکست و rollback همان Stage سبز باشد.
3. هیچ invariant سراسری نقض نشده باشد.
4. diff فقط در scope همان Stage باشد.
5. وضعیت، commandهای اجراشده و نتیجهٔ gate در tracker ثبت شده باشد.
6. اگر Stage عملیاتی است، رسید محیط هدف و نه fixture حافظه‌ای وجود داشته باشد.

وضعیت مجاز Stage:

```text
PROPOSED → APPROVED → IN_PROGRESS → BLOCKED | COMPLETE → SUPERSEDED
```

Cursor حق ندارد `APPROVED` را از متن حدس بزند، `BLOCKED` را با workaround
پنهان کند یا برای سبزکردن gate داده/رویداد مصنوعی بسازد.

### ترتیب منابع حقیقت

در صورت تعارض، ترتیب زیر معتبر است:

1. تصمیم صریح جدید مالک
2. همین سند پس از تأیید مالک
3. قراردادها و تست‌های جاری `main`
4. ADRهای جدید این ریفکتور
5. مستندات جاری و غیرمنسوخ پروژه
6. شاخهٔ `candidate/wa-ir-standby-v1` فقط به‌عنوان مرجع تاریخی

از شاخهٔ سه‌سروره code merge انجام نمی‌شود. کنترل‌پلین‌های بازنشستهٔ
`WA-IR`، `Writer-Witness` و `Object-Delta` نباید زنده شوند.

### مجوزها

تأیید این پلن به معنی مجوز موارد زیر نیست:

- push یا merge
- provisioning سرور
- نوشتن روی Object Storage واقعی
- deploy staging یا production
- migration یا repair دادهٔ تولید
- تغییر DNS
- انتقال Writer
- غیرفعال یا حذف‌کردن سرورهای فعلی

هر مورد بالا gate و مجوز جدا دارد.

---

## وضعیت هدف به زبان ساده

| جزء | Finland Primary | Iran Standby |
| --- | --- | --- |
| نقش عادی | Web/API Writer + Bot + Queue + workers | Web/API read-only standby |
| نقش هنگام قطعی ایران | Bot/Telegram owner؛ Web/API write-fenced، ولی Bot-home mutation مجاز | Web/API Writer با اقدام انسانی |
| PostgreSQL و Redis | دارد | دارد |
| Telegram token/session/executor | تنها مالک | مطلقاً ندارد |
| داشبورد عملیات | دارد | دارد |
| Market capture | G1/G2، منابع تلگرامی و منابع خارجی قابل دسترس | IME/بورس، USDT و ورودی‌های داخلی قابل دسترس |
| تخمین | `FULL_CONNECTED` و مرجع promotion مدل | Shadow در حالت وصل؛ `IR_CONTINUITY_*` در قطعی |
| مسیر داده بین دو کشور | Object Storage ایران | Object Storage ایران |

Finland Primary هدف فعلی `65.109.214.203` است. IP و هویت Iran Standby باید
در inventory تأییدشده و manifest خصوصی ثبت شود؛ هیچ IP تاریخی از کد یا سند
قدیمی به‌صورت پیش‌فرض معتبر فرض نمی‌شود.

### سناریوی اتصال عادی

1. DNS محصول به Finland Primary اشاره می‌کند.
2. Finland تنها Web Writer و تنها Telegram owner است.
3. Iran محصول قابل‌نوشتن سرو نمی‌کند، ولی dashboard، collectorهای داخلی،
   sync receiver و shadow inference فعال‌اند.
4. Market Facts و داده‌های مشترک از هر دو جهت از طریق Object Storage ردوبدل
   می‌شوند.
5. lag عادی حداکثر ۳۰ ثانیه است.
6. داشبورد هر دو سرور sequence، ACK، gap، backlog، checksum، peer state و
   مدل فعال را نشان می‌دهد.

### سناریوی قطع اینترنت ایران

1. عامل انسانی که طبق تصمیم مالک به هر دو سرور دسترسی دارد، در dashboard
   Finland فرمان `DRAIN WEB WRITER` را صادر می‌کند. Finland mutation جدید Web
   را می‌بندد، تراکنش‌های جاری را تمام می‌کند و `Fence Receipt` امضاشده می‌دهد؛
   Bot بدون محدودیت ادامه می‌دهد.
2. عامل Receipt را به dashboard Iran منتقل و اعتبار آن را مشاهده می‌کند.
3. عامل با فرمان صریح و auditشده، DNS محصول را از طریق API آروان به Iran تغییر
   می‌دهد و probeهای مقصد را بررسی می‌کند.
4. تنها بعد از Receipt و `DNS_READY`، عامل Iran را با
   `writer_generation + 1` فعال می‌کند؛ هیچ promotion خودکاری وجود ندارد.
5. ورود در Iran از مسیر SMS داخلی ممکن است؛ sessionهای محصول بین دو سرور
   منتقل نمی‌شوند و کاربر دوباره وارد می‌شود.
6. رویدادهای هر سمت در outbox محلی/سطل جمع می‌شوند؛ نبود peer خطای محصول
   ایران نیست.
7. مدل ایران با `IR_CONTINUITY_BASE` یا `IR_CONTINUITY_ENRICHED` خروجی
   می‌دهد. کاهش داده باعث قطع خروجی نمی‌شود؛ بازه عریض و confidence کمتر می‌شود.

### سناریوی اتصال مجدد

1. DNS و Writer همچنان Iran می‌مانند.
2. هر دو جریان، backlog را از آخرین checkpoint معتبر ادامه می‌دهند.
3. Facts، دادهٔ کسب‌وکار، media، model artifact و snapshotها با قرارداد مخصوص
   خودشان تطبیق داده می‌شوند.
4. event ردشده یا conflict پنهان نمی‌شود. پول، موجودی و معامله با
   last-write-wins حل نمی‌شوند.
5. تا `FULL_SYNC` و `MARKET_READY`، دکمهٔ Finland Writer غیرفعال است.
6. عامل Iran را drain می‌کند؛ final delta منتقل و Fence Receipt امضاشده Iran
   در dashboard Finland تأیید می‌شود.
7. عامل DNS را به Finland تغییر می‌دهد و probe واقعی امضاشده مسیر را تأیید
   می‌کند.
8. عامل Finland را با `writer_generation + 1` فعال می‌کند؛ Iran در standby
   پایدار می‌ماند و هیچ تغییر نقش خودکاری رخ نمی‌دهد.

---

## تصمیم‌های تثبیت‌شده و تصمیم‌های باز

### تثبیت‌شده

- یک Finland Primary میزبان Web/API/Bot/Queue/PostgreSQL/Redis خواهد بود.
- Iran فقط Web standby/writer است و هیچ Telegram credential یا executor ندارد.
- انتقال Web Writer فقط با اقدام انسانی از dashboard انجام می‌شود.
- dashboard ترتیب انسانی source-drain، Fence Receipt، DNS verification و
  destination activation را enforce می‌کند و اجازهٔ پرش یا جابه‌جایی ترتیب نمی‌دهد.
- هر دو سرور dashboard امن و مستقل با username/password/TOTP دارند.
- مسیر دادهٔ بین Finland و Iran فقط Object Storage ایران است؛ SSH مسیر sync نیست.
- سقف lag در اتصال عادی ۳۰ ثانیه است.
- failback به Finland هم گارد DNS و هم گارد sync/model دارد.
- `home_site` مرجع mutation آفر و معامله است؛ پول/موجودی LWW ندارند.
- همهٔ Market Facts سینک می‌شوند، نه فقط G1/G2.
- ورودی‌های مدل، artifact مدل و snapshot نهایی هرکدام با قرارداد مستقل سینک می‌شوند.
- state موقت cache و online learner دوطرفه merge نمی‌شود.
- کاهش داده باعث widening خروجی می‌شود، نه قطع خروجی؛ فقط خرابی بنیادی مدل یا
  نبود baseline معتبر خروجی را ناممکن می‌کند.
- `DEGRADED` و `REFERENCE_ONLY` حق ردکردن آفر ندارند.
- تغییر Web Writer هیچ محدودیتی روی Bot فنلاند ایجاد نمی‌کند. Bot با authority
  مستقل `TELEGRAM_OWNER` و تمام قابلیت‌های فعلی خود ادامه می‌دهد؛ طراحی conflict
  policy باید این پیوستگی را بدون خاموش‌کردن commandهای Bot تضمین کند.
- تعیین و انتقال Web Writer فقط با اقدام صریح عامل انسانی انجام می‌شود. مالک
  دسترسی هم‌زمان خود به dashboard هر دو سرور را در قطعی تضمین کرده است؛ Web
  Writer lease، renewal، TTL و automatic promotion/demotion وجود ندارد.
- هر انتقال ابتدا مبدأ را drain/fence می‌کند، سپس Fence Receipt امضاشده و DNS
  مقصد را ثابت می‌کند و در پایان با فرمان جداگانهٔ انسان مقصد را فعال می‌کند.
- API آروان برای تغییر صریح DNS و عملیات لازم مجاز است؛ credential محلی قدیمی
  باید بدون افشای مقدار، اعتبارسنجی و به secret mount محدود منتقل شود.

### نیازمند تأیید در بازبینی این پلن

| ID | تصمیم پیشنهادی | مقدار اولیه |
| --- | --- | --- |
| `D-02` | DNS TTL محصول | ۳۰ ثانیه در دورهٔ آماده‌سازی/cutover؛ مقدار عادی پس از soak جدا تعیین شود |
| `D-03` | SLO deploy hotfix بدون migration | حداکثر ۱۵ دقیقه از artifact تأییدشده تا health سبز |
| `D-04` | SLO release عادی دو سرور | حداکثر ۳۰ دقیقه در حالت اتصال سالم |
| `D-05` | SLO rollback کد بدون DB restore | حداکثر ۱۰ دقیقه |
| `D-06` | artifact distribution | registry اصلی + OCI archive امضاشده در Object Storage ایران برای Iran/offline |

این موارد تا تأیید مالک، requirement پیشنهادی‌اند و Cursor حق تثبیت پنهان آن‌ها
در کد را ندارد.

---

## invariantهای سراسری

1. تنها یک Web Writer و تنها یک Telegram execution owner وجود دارد.
2. DNS مسیر ترافیک است، نه منبع حقیقت Writer.
3. هر mutating endpoint، worker و scheduler قبل از commit باید authority class
   خود (`WEB_WRITER`, `TELEGRAM_OWNER`, `HOME_SITE`, `LOCAL_ONLY`) و generation
   مرتبط را بررسی کند. Web fence نباید Bot-home mutation مجاز را خاموش کند.
4. فقط انسان Web Writer را تغییر می‌دهد؛ timeout، network event، restart، DNS
   یا sync state حق promotion/demotion خودکار ندارند.
5. Bot و Web روی Finland از یک PostgreSQL مشترک استفاده می‌کنند؛ sync داخلی
   بین دو process جایگزین تراکنش مشترک نمی‌شود.
6. Object Storage یک transport است، نه database و نه backup و نه Web Writer authority.
7. event delivery حداقل‌یک‌بار و event apply دقیقاً یک‌بار از دید کسب‌وکار است.
8. sequence gap، hash mismatch، schema mismatch یا signature failure fail-closed
   و قابل‌مشاهده است.
9. stable public identity مرجع cross-site است؛ integer ID محلی است.
10. `created_at` آفر immutable است.
11. event/availability/persisted timestamps برای Point-in-Time حفظ می‌شوند.
12. secret وارد Git، log، artifact، prompt، `/tmp` یا Object Storage plaintext نمی‌شود.
13. هیچ deploy از dirty tree یا artifact بدون commit/digest انجام نمی‌شود.
14. staging قبل از production الزامی است؛ production permission جدا دارد.
15. guardهای deploy متناسب با نوع تغییرند؛ gate نامرتبط نباید hotfix را ساعت‌ها
    متوقف کند.
16. active release، آخرین rollback و آخرین backup قابل‌بازیابی هرگز با cleanup
    حذف نمی‌شوند.
17. `main` شاخهٔ بلندمدت است؛ شاخه‌های plan/implementation پس از merge یا رد
    شدن عمر محدود دارند.

---

## امواج اجرایی واقعی

| موج | Stageهای اصلی | نتیجه |
| --- | --- | --- |
| `W0` | `P1-00`, `P2-00`, `P3-00`, `P4-00`, `P5-00` تا `P5-02` | baseline، ADR و inventory بدون تغییر رفتار |
| `W1` | `P1-01`, `P1-02`, `P4-01`, `P4-02`, `P4-05`, `P5-06`, `P5-07` | ریپوی تمیز، release foundation و Cursor Skill تأییدشده |
| `W2` | `P1-03`, `P4-03`, `P4-04` | runtime یکپارچه Finland در محیط ایزوله |
| `W3` | `P2-01` تا `P2-05`, `P1-04`, `P3-01` تا `P3-05` | قرارداد داده، sync، authority و Market Facts |
| `W4` | `P1-05`, `P2-06` تا `P2-09`, `P3-06` تا `P3-08`, `P4-06` | ادغام داده rehearsal، dashboard و continuity model |
| `W5` | `P1-06`, `P2-10`, `P3-09`, `P4-07`, `P4-10` | staging/fault/restore کامل بدون cutover تولید |
| `W6` | `P1-07` + `P4-08` به‌صورت change set اتمیک | انتقال کنترل‌شدهٔ تولید به Finland Primary |
| `W7` | `P2-11` + `P4-09` به‌صورت change set اتمیک | فعال‌سازی Iran به‌عنوان standby بدون failover واقعی |
| `W8` | `P2-12`, `P3-10`, مستندات متناظر `P5-03/P5-04` | drill قطع/وصل/failback و پذیرش عملیاتی |
| `W9` | `P1-08`, `P5-05`, `P5-08` | حذف بدهی قدیمی و closure مستندات |

Stage تولیدی یک موج فقط با مجوز جدا اجرا می‌شود. کامل‌شدن Stage کدنویسی، مجوز
ورود خودکار به موج تولید نیست.

### dependency registry الزام‌آور

Cursor پیش از شروع هر Stage باید تمام dependencyهای زیر را `COMPLETE` ببیند.
محدوده‌های پیوسته مثل `P2-01..P2-09` یعنی همهٔ Stageهای آن بازه.

| Stage | Depends on |
| --- | --- |
| `P1-00`, `P3-00`, `P4-00`, `P5-00` | تأیید همین پلن |
| `P1-01` | `P1-00` |
| `P1-02` | `P1-00`, `P4-00` |
| `P1-03` | `P1-02`, `P4-02`, `P4-03` |
| `P1-04` | `P1-03`, `P2-00`, `P2-01` |
| `P1-05` | `P1-04`, `P2-05`, `P4-04` |
| `P1-06` + `P4-07` | `P1-05`, `P4-04`, `P4-05`, `P4-06` |
| `P1-07` + `P4-08` | `P1-06`, `P4-07`, `P4-10`, مجوز تولید |
| `P1-08` | `P1-07`, `P2-11`, retention/backup approval |
| `P2-00` | `P1-00` |
| `P2-01` | `P2-00` |
| `P2-02` | `P2-01`, `P4-02` |
| `P2-03` | `P2-01`, `P2-02` |
| `P2-04` | `P2-03` و تصمیم تثبیت‌شدهٔ انتقال کاملاً انسانی Writer |
| `P2-05` | `P2-00`, `P2-01`, `P2-04` و تصمیم تثبیت‌شدهٔ استقلال کامل Bot |
| `P2-06` | `P2-03`, `P2-04`, `P2-05` |
| `P2-07` | `P2-04`, `P2-06`, تصمیم `D-02` |
| `P2-08` | `P2-04`, `P2-06`, `P2-07` |
| `P2-09` | `P2-05`, `P2-08` |
| `P2-10` | `P2-01..P2-09`, `P3-08` |
| `P2-11` + `P4-09` | `P1-07`, `P2-10`, `P3-09`, `P4-07`, مجوز تولید |
| `P2-12` | `P2-11`, `P3-10`, `P4-10`, مجوز drill |
| `P3-01` | `P3-00`, `P2-01` |
| `P3-02` | `P3-01`, `P2-02` |
| `P3-03` | `P3-01` |
| `P3-04` | `P3-01`, `P1-04` |
| `P3-05` | `P3-01`, `P2-02`, `P4-02` |
| `P3-06` | `P3-02`, `P3-04`, `P3-05` |
| `P3-07` | `P3-06` |
| `P3-08` | `P3-07` |
| `P3-09` | `P3-03..P3-08` |
| `P3-10` | `P3-09`, `P2-10` |
| `P4-01` | `P4-00` |
| `P4-02` | `P4-01` |
| `P4-03` | `P4-01`, `P4-02`, `P1-02` |
| `P4-04` | `P4-02`, `P4-03` |
| `P4-05` | `P4-00` |
| `P4-06` | `P4-02`, `P4-03`, `P4-05`, تصمیم‌های `D-03..D-05` |
| `P4-10` | `P4-04`, `P4-07` |
| `P5-01` | `P5-00` |
| `P5-02` | `P5-00`, تصمیم‌های معماری مصوب |
| `P5-03`, `P5-04` | همگام با Stage فنی متناظر؛ پیش از gate آن Stage |
| `P5-05` | `P5-02..P5-04` و docs inventory |
| `P5-06`, `P5-07` | تأیید پلن، `P5-00`, `P5-01` |
| `P5-08` | تمام Stageهای لازم و رسیدهای پذیرش |

Dependency چرخه‌ای مجاز نیست. دو جفت اتمیک جدول (`P1-06+P4-07` و Stageهای
تولیدی مشخص‌شده) یک change set هستند، نه dependency چرخه‌ای.

---

# بخش ۱ — ادغام Bot و WebApp روی Finland Primary

هدف این بخش حذف sync و پیچیدگی مصنوعی میان دو سرور فنلاند و ساخت یک runtime
یکپارچه روی `65.109.214.203` است. Bot و API processهای جدا می‌مانند، اما یک
دیتابیس، یک Redis و یک مدل authority دارند.

## `P1-00` — baseline و نقشهٔ سطح موجود

وضعیت: `PROPOSED`

کار:

- inventory کامل میزبان‌های فعلی، processها، containerها، timerها، دامنه‌ها،
  volumeها، secret mountها، backupها و ownerهای Telegram تهیه شود.
- تمام مسیرهای mutation از Bot، Web، worker و admin به مدل/جدول نگاشت شوند.
- source databaseهای دو Finland فعلی و drift آن‌ها فقط read-only بررسی شوند.
- سرویس‌های target Finland و منابع موردنیاز CPU/RAM/disk/network ثبت شوند.
- وضعیت واقعی SSH، fingerprint و سیستم‌عامل هدف با سند
  `docs/architecture/FINLAND_PRIMARY_TARGET.md` تطبیق داده شود.

خروجی:

- `CURRENT_RUNTIME_INVENTORY.md`
- `MUTATION_SURFACE_MATRIX.md`
- `FINLAND_MERGE_DATA_REPORT.md`
- baseline زمان deploy، restart، backup، restore و smoke test

Gate خروج:

- هیچ service یا دادهٔ ناشناخته باقی نماند.
- هر Telegram token/session دقیقاً یک owner ثبت‌شده داشته باشد.
- audit هیچ write خارجی انجام ندهد و secret چاپ نکند.

Rollback: چون read-only است، rollback ندارد؛ artifactهای audit طبق retention پاک
می‌شوند.

## `P1-01` — پاکسازی و یکپارچگی repository محلی

وضعیت: `PROPOSED`

سناریو: فایل، log، test output، backup یا clone پراکنده نباید source of truth
شود یا تا ابد دیسک را اشغال کند.

کار:

- فقط یک worktree canonical حفظ شود؛ worktree/clone تازه owner و expiry می‌خواهد.
- branchهای حذف‌شدنی پاک شوند؛ `candidate/wa-ir-standby-v1` فقط تا پایان استخراج
  مرجع local-only می‌ماند و merge source نیست.
- tracked logهای خام، خروجی test و evidenceهای تکراری inventory شوند. حذف فقط
  پس از manifest اثر و تأیید اینکه مرجع فعال نیست انجام شود.
- artifactهای local به `.local/{logs,test-results,deploy,backups,data,caches,quarantine}`
  منتقل شوند.
- cleanup command حتماً dry-run-first، root-bound، symlink-safe و retention-aware باشد.
- Telegram raw data بر اساس source/day پارتیشن، deduplicate و compress شود.
- logها، backupها، releaseها و quarantine retention و metric داشته باشند.

Gate خروج:

- `git status` تمیز؛ هیچ runtime artifact جدید tracked نیست.
- cleanup نمی‌تواند بیرون `.local` یا rootهای declareشده حذف کند.
- active release، rollback و backup قابل restore در dry-run مستثنا هستند.
- repo size و artifact size قبل/بعد گزارش شود.

## `P1-02` — واژگان و configuration بدون topology تاریخی

وضعیت: `PROPOSED`

کار:

- مفاهیم مبهم `foreign/iran` به `site_id=fi|ir`، `runtime_role` و
  `writer_role` تفکیک شوند.
- IP/domain/path از runtime code حذف و فقط از manifest معتبر خوانده شوند.
- `server_mode` قدیمی تا migration کامل adapter سازگار دارد، ولی منبع authority
  جدید نمی‌شود.
- test fixtureهای IP تاریخی به placeholder یا fixture صریح تبدیل شوند.
- CI hardcode guard برای IP، domain، project path و compose identity گسترش یابد.

Gate خروج:

- تغییر IP یا دامنه فقط یک manifest خصوصی و artifactهای تولیدشده را تغییر دهد.
- runtime از نام «Iran» برای سرور قدیمی فنلاند استفاده نکند.
- config contradictory پیش از mutation fail شود.

## `P1-03` — compose و service ownership هدف Finland

وضعیت: `PROPOSED`

سرویس‌های هدف Finland:

- Nginx/edge
- Web/API
- Telegram primary و executor/publisherهای مجاز Queue-v1
- PostgreSQL و Redis
- sync outbox/transport به Iran
- Market collectors مجاز، parser/materializer و estimator
- scheduler/workerهای product
- dashboard عملیات
- log/metrics/backup agents

کار:

- یک image content-addressed و role profileهای صریح ساخته شود.
- non-bot serviceها credential تلگرام خالی و runtime guard داشته باشند.
- Bot و Web به یک PostgreSQL/Redis وصل شوند؛ مسیر HTTP sync قدیمی بین این دو
  process حذف یا no-op سازگار شود.
- job ownership برای هر scheduler ثبت و duplicate execution در startup block شود.
- migration service قبل از app و app قبل از Bot/workerهای side-effect اجرا شود.
- health به readiness واقعی DB/Redis/migration/queue وابسته باشد، نه فقط process up.

Gate خروج:

- compose config test ثابت کند دقیقاً یک Telegram execution owner وجود دارد.
- mutation Bot بلافاصله در Web از همان DB دیده شود و change loop محلی نسازد.
- restart API باعث restart اجباری Bot نشود و برعکس.
- dependency failure، سرویس side-effect را fail-closed کند.

## `P1-04` — دادهٔ مشترک و حذف sync داخلی Finland

وضعیت: `PROPOSED`

کار:

- `change_log` از مفهوم «Bot server در برابر Web server» جدا و به outbox
  cross-site تبدیل شود.
- eventهای محلی Bot/Web با `origin_site=fi` و `origin_surface` ثبت شوند، اما فقط
  یک‌بار به Iran منتشر شوند.
- تمام side-effect ledgerهای Telegram local باقی بمانند.
- stable identity و field policy برای offer/trade/message/media بررسی شود.
- هر listener/bulk update که outbox را دور می‌زند اصلاح یا صریحاً local اعلام شود.

Gate خروج:

- receiver coverage، registry coverage و event emission به‌صورت CI مقایسه شوند.
- هیچ event محلی به همان دیتابیس loopback نشود.
- duplicate surface Web/Bot یک logical event را دوبار به Market یا sync ندهد.

## `P1-05` — rehearsal ادغام دادهٔ دو Finland

وضعیت: `PROPOSED`

سناریو:

1. از هر source یک backup immutable و restore-tested گرفته می‌شود.
2. writerها در محیط rehearsal freeze می‌شوند.
3. داده با stable identity و policy جدول merge می‌شود.
4. FK، sequence، checksum و business invariant بررسی می‌شود.
5. app و Bot روی clone target بالا می‌آیند و full matrix اجرا می‌شود.

قواعد:

- raw row overwrite ممنوع است.
- users/relations/invitations/offer/trade/media هرکدام policy مشخص دارند.
- money/inventory conflict فقط report/quarantine؛ نه auto LWW.
- Redis source of truth محسوب نمی‌شود؛ فقط state موردنیاز queue/session با قرارداد
  خودش migrate می‌شود.

Gate خروج:

- دو اجرای idempotent نتیجهٔ یکسان بدهند.
- row count تنها معیار نیست؛ canonical business hash و invariant لازم است.
- restore rehearsal و rollback rehearsal سبز باشند.

## `P1-06` — staging یکپارچه Finland

وضعیت: `PROPOSED`

سناریوهای الزامی:

- login/OTP، invitation، offer، trade، overtime و expiry
- Messenger متن/media و realtime
- Bot callback، publication و Queue-v1
- restart مستقل API/Bot/Redis/DB
- backup/restore و application rollback
- بار واقعی و disk/memory headroom
- قطع موقت Object Storage بدون خرابی product محلی

Gate خروج:

- full functional matrix و browser matrix سبز باشد.
- latency و resource baseline بدتر از budget مصوب نباشد.
- Telegram identity readback هیچ collision نشان ندهد.

## `P1-07` — cutover کنترل‌شده به Finland Primary

وضعیت: `PROPOSED — نیازمند مجوز تولید جدا`

ترتیب:

1. exact release، backup و rollback artifact تأیید شود.
2. mutation sourceهای دو Finland قدیمی quiesce شوند.
3. final delta و checksum گرفته شود.
4. DB/media روی target restore و دو بار migration idempotency بررسی شود.
5. API target بالا آید ولی traffic نگیرد.
6. Bot قدیمی stop و session/lease آن terminal شود.
7. Bot target با همان exact release و owner receipt بالا آید.
8. DNS/edge به Finland Primary تغییر کند.
9. smoke، queue، Market و audit بررسی شود.
10. سرورهای قدیمی در یک quarantine window فقط read-only/rollback بمانند.

Rollback:

- قبل از DB write جدید target: بازگشت کامل به sourceهای قبلی.
- بعد از write جدید: rollback کد روی target؛ بازگرداندن خام DB قدیمی ممنوع مگر
  restore plan و reconciliation صریح.

Gate خروج:

- target تنها Web Writer و Telegram owner است.
- no-op release و rollback واقعی اندازه‌گیری شده‌اند.
- decommission تا پایان retention و تأیید backup انجام نمی‌شود.

## `P1-08` — closure و حذف بدهی توپولوژی قدیمی

وضعیت: `PROPOSED`

- adapter، env، script و docs مربوط به دو Finland فقط بعد از اثبات no-reference
  حذف شوند.
- سرور قدیمی پس از backup/off-host verification، incident window و تأیید مالک
  decommission شود.
- مانیتورینگ و billing orphan بررسی شود.

---

# بخش ۲ — Iran Standby و انتقال کنترل‌شدهٔ Web Writer

هدف این بخش ساخت standby واقعی است، نه یک clone خاموش. Iran باید در اتصال
عادی همگام و قابل‌مشاهده باشد، در قطعی با دستور انسان Writer شود و پس از اتصال
مجدد بدون split-brain به standby برگردد.

## `P2-00` — Data Ownership Matrix

وضعیت: `PROPOSED`

برای تک‌تک مدل‌ها/جدول‌ها این فیلدها ثبت شود:

```text
table/domain
stable identity
write surfaces
home_site/authority
sync direction
field exclusions
conflict rule
side effects
retention
bootstrap method
parity hash
repair method
```

رده‌های اصلی:

- shared product state
- site-local auth/session/browser state
- Telegram-local execution state
- Messenger metadata و media blob
- sync bookkeeping
- Market Facts/raw/audit/model artifacts/snapshots
- operational audit و dashboard state

Blocker صریح: registry فعلی بعضی جدول‌های Messenger مانند chat/message/file را
local-only می‌داند. این وضعیت برای standby دارای «تمام قابلیت‌های وب‌اپ» کافی
نیست. business message metadata و media لازم برای مشاهدهٔ تاریخچه باید contract
cross-site بگیرند؛ upload lease، browser cache، provider/runtime ID و session
همچنان local می‌مانند. Cursor حق ندارد `NO_SYNC` فعلی را بدون بازبینی به معماری
جدید منتقل کند.

Gate خروج: هیچ مدل SQLAlchemy، object blob یا file store در registry با وضعیت
`UNKNOWN` باقی نماند.

## `P2-01` — قرارداد event و stream

وضعیت: `PROPOSED`

پاکت حداقل شامل این موارد است:

```text
contract_version, event_id, stream_id, source_site, source_sequence,
authority_class, authority_generation, aggregate_type, aggregate_public_id, operation,
occurred_at_utc, available_at_utc, persisted_at_utc,
payload_hash, previous_hash, schema_version, payload
```

قواعد:

- source sequence مستقل و افزایشی برای هر stream است؛ integer PK جدول نیست.
- outbox در همان transaction کسب‌وکار نوشته می‌شود.
- receiver فقط contiguous sequence را apply می‌کند.
- ACK فقط بعد از commit و شامل hash همان event است.
- duplicate با همان hash idempotent؛ sequence برابر با hash متفاوت conflict است.
- gap بعدی را متوقف می‌کند و repair request می‌سازد.
- event ردشده برای audit سینک/نگهداری می‌شود، اما state کسب‌وکار را تغییر نمی‌دهد.

## `P2-02` — Object Storage transport

وضعیت: `PROPOSED`

namespace پیشنهادی:

```text
v1/events/{source_site}/{stream_id}/{sequence}
v1/acks/{source_site}/{stream_id}/{sequence}/{receiver_site}
v1/heads/{source_site}/{stream_id}
v1/snapshots/{site}/{snapshot_id}/...
v1/checksums/{site}/{cutoff}/...
v1/media/{content_digest}
v1/models/{model_name}/{version}/...
v1/releases/{release_digest}/...
```

کار:

- قابلیت واقعی conditional write/ETag/CAS، multipart، lifecycle و consistency
  سرویس‌دهنده آزمایش شود؛ S3-compatible بودن به‌تنهایی اثبات نیست.
- envelope و blob قبل از upload با AEAD رمز و integrity-bound شوند.
- key rotation و dual-read window تعریف شود.
- head فقط hint است؛ correctness از sequence scan و ledger می‌آید.
- event/ACK تأییدنشده خودکار پاک نشود؛ ACKed data طبق retention و checkpoint
  پاک شود.
- media با content digest و reference ledger deduplicate شود.

Gate خروج:

- corruption، missing object، duplicate، out-of-order، partial multipart و credential
  rotation در integration test پوشش داشته باشند.
- هیچ plaintext PII/secret در bucket inspection دیده نشود.

## `P2-03` — bootstrap، snapshot و parity

وضعیت: `PROPOSED`

- bootstrap از snapshot transactionally consistent و cutoff-bound انجام شود.
- snapshot chunk، manifest، row count، schema digest و per-table business hash دارد.
- incremental replay از `cutoff + 1` آغاز می‌شود.
- parity شامل watermark، outbox/ACK، business checksum، media references و rejected
  events است.
- row count برابر بدون hash برابر `FULL_SYNC` نیست.

Gate خروج: bootstrap وسط crash قابل resume است و اجرای دوباره state را تغییر
نمی‌دهد.

## `P2-04` — Manual Writer Handover، generation و fencing

وضعیت: `PROPOSED؛ تصمیم معماری انتقال کاملاً انسانی تثبیت شده است`

اصل:

- نقش پایدار محلی هر Web Server یکی از `WEB_WRITER`، `WEB_DRAINING` یا
  `WEB_STANDBY` است و پس از restart حفظ می‌شود.
- فقط عامل انسانی احراز هویت‌شده با username/password/TOTP و تأیید صریح می‌تواند
  transition را آغاز یا تکمیل کند. هیچ network event، timeout، DNS، sync state،
  process restart یا scheduler حق promotion/demotion خودکار ندارد.
- انتقال از dashboard مبدأ آغاز می‌شود: mutation جدید Web بسته، transactionهای
  جاری drain، نقش `WEB_STANDBY` پایدار و Fence Receipt امضاشده صادر می‌شود.
- Receipt حداقل `transition_id`، `source_site`، `previous_writer_generation`،
  `last_web_mutation_id`، drain result، زمان، release digest و امضا دارد.
- عامل Receipt را به dashboard مقصد منتقل می‌کند. مقصد replay، tamper، generation
  mismatch یا Receipt مربوط به انتقال دیگر را رد می‌کند.
- تغییر DNS آروان یک اقدام انسانی جدا با preview، TOTP، provider receipt و
  verification است. موفقیت API به‌تنهایی `DNS_READY` نیست.
- فقط پس از Fence Receipt معتبر، gateهای sync/model متناسب با جهت انتقال و
  `DNS_READY`، عامل مقصد را با `writer_generation + 1` فعال می‌کند.
- `writer_generation` منقضی یا renew نمی‌شود؛ فقط شمارهٔ انتقال انسانی برای audit،
  event provenance و رد mutation نسل قدیمی است. generation وب روی event مستقل
  `TELEGRAM_OWNER` اعمال نمی‌شود.
- اگر dashboard مبدأ در دسترس نباشد، Runbook دستی ابتدا Web/API مبدأ را از طریق
  SSH یا provider hard-fence و نقش restart آن را `WEB_STANDBY` می‌کند. مقصد بدون
  evidence این عملیات دکمهٔ Force Activate ندارد.
- Object Storage مسیر sync و نگهداری نسخهٔ audit receipt است؛ مرجع Writer، lease
  یا محرک تغییر نقش نیست.

تست‌های بحرانی:

- عامل دو dashboard را هم‌زمان باز می‌کند یا دکمه را دوبار می‌زند.
- فعال‌سازی مقصد بدون Receipt، با Receipt دستکاری‌شده یا replayشده تلاش می‌شود.
- source میان `DRAINING` و `STANDBY` restart می‌شود.
- DNS API پس از fence مبدأ ولی پیش از activation مقصد شکست می‌خورد.
- client با DNS cache قدیمی به مبدأ standby mutation می‌فرستد.
- مسیر دستی hard-fence و بازیابی کنترل‌شده rehearsal می‌شود.

Gate خروج: هر transition رسید انسانی کامل دارد، خطا فقط downtime/read-only ایجاد
می‌کند و هیچ تستی دو commit معتبر Web از دو generation/site هم‌زمان نمی‌سازد.

## `P2-05` — authority و conflict policy

وضعیت: `PROPOSED`

- offer/trade mutation فقط روی `home_site` انجام می‌شود.
- آفر Finland در partition روی Iran فقط historical/read-only است و قابل execute
  نیست؛ آفر تازهٔ Iran، `home_site=ir` دارد.
- Bot در Finland هنگام Iran Web Writer تمام قابلیت‌های فعلی خود را بدون محدودیت
  ادامه می‌دهد؛ تغییر Web Writer به‌تنهایی مجاز نیست هیچ command بات را
  `unavailable`، read-only یا متوقف کند.
- هر command بات باید در Data Ownership Matrix به authority مستقل
  `TELEGRAM_OWNER`، `HOME_SITE=fi` یا aggregate صریحاً Bot-owned نگاشت شود. اگر
  دامنه‌ای اکنون هم از Web و هم از Bot mutation می‌پذیرد، این Stage باید قرارداد
  authority/merge آن را سناریومحور طراحی کند؛ خاموش‌کردن Bot راه‌حل قابل‌قبول نیست.
- command به home غیرقابل‌دسترس pending نامحدود نمی‌ماند؛ نتیجهٔ صریح
  `HOME_SITE_UNREACHABLE` می‌دهد.
- admin/global state به‌صورت blanket تابع Web Writer generation نیست؛ authority هر
  command و aggregate باید صریح باشد تا Web ایران و Bot فنلاند بدون split-brain
  و بدون محدودکردن Bot کار کنند.
- immutable append events merge می‌شوند؛ mutable aggregateها state machine دارند.
- money، quantity و inventory هرگز LWW نیستند.
- eventی که oversell/negative inventory بسازد quarantine می‌شود، sync green را
  می‌بندد و تصمیم انسانی/repair audited می‌خواهد.
- field local مثل Telegram message ID یا dashboard transition session به peer
  business state وارد نمی‌شود.

Gate خروج: conflict matrix برای تمام `SYNC` tables تست تولیدی و property-based دارد.

## `P2-06` — dashboard مستقل دو سرور

وضعیت: `PROPOSED`

امنیت:

- hostname ثابت جدا از محصول
- HTTPS، local username/password/TOTP
- cookie/session جدا، کوتاه‌عمر، SameSite/CSRF و rate limit
- bootstrap حساب فقط حضوری/کنترل‌شده؛ TOTP secret sync نمی‌شود
- audit append-only برای login، writer handover، DNS، repair و override

نمای لازم:

- نقش و writer generation محلی/peer
- آخرین transition، Fence Receipt و وضعیت drain مبدأ
- bucket reachability و آخرین peer-seen
- local/published/ACKed/applied sequence برای هر stream
- gap، unpublished، unacked، apply backlog و rejected count
- business checksum و media backlog
- active release/model/schema/registry fingerprint هر دو سرور
- DNS expected/observed و probeهای داخلی/خارجی
- `SYNC_READY`, `MARKET_READY`, `DNS_READY`, `WRITER_READY`

Dashboard summary نباید جزئیات conflict را پنهان کند؛ drill-down event-level لازم است.

## `P2-07` — DNS control و route verification

وضعیت: `PROPOSED؛ D-02 باید تأیید شود`

- فقط یک A record allowlisted بین دو IP تأییدشده قابل تغییر است.
- dashboard درخواست را plan و human confirm می‌کند؛ backend با token root-only
  provider API را CAS-like اجرا می‌کند.
- مقدار unexpected، multi-record یا provider ambiguity mutation را block می‌کند.
- موفقیت API کافی نیست؛ authoritative DNS، resolverهای منتخب و signed site probe
  باید destination و writer generation درست را ثابت کنند.
- هر تغییر before/after، operator، request id و provider receipt دارد.
- fallback دستی پنل provider مستند است ولی bypass audit نیست؛ نتیجه باید ثبت شود.

## `P2-08` — state machine اتصال، قطعی و اتصال مجدد

وضعیت: `PROPOSED`

```text
CONNECTED_FI_WRITER
  → HUMAN_REQUESTS_FI_DRAIN
  → FI_STANDBY_RECEIPT_VERIFIED
  → DNS_TO_IR_VERIFIED
  → HUMAN_ACTIVATES_IR
  → IR_WRITER
  → RECONNECTING_IR_WRITER
  → FULL_SYNC_AND_MARKET_READY
  → HUMAN_REQUESTS_IR_DRAIN
  → IR_STANDBY_RECEIPT_VERIFIED
  → DNS_TO_FI_VERIFIED
  → HUMAN_ACTIVATES_FI
  → CONNECTED_FI_WRITER
```

هر transition باید precondition، mutation، timeout، audit، observable proof و
rollback/forward-recovery داشته باشد. timeout فقط درخواست UI را منقضی می‌کند و
حق تغییر role ندارد. dashboard حق جهش مستقیم بین stateها را ندارد.

## `P2-09` — OTP، session، notification و Messenger در قطعی

وضعیت: `PROPOSED`

- product sessionها site-local هستند؛ پس از Writer switch ورود مجدد لازم است.
- SMS داخلی روی Iran مستقل از Telegram کار می‌کند.
- Iran هیچ Telegram send/poll/token ندارد؛ Telegram side effect در Finland باقی
  می‌ماند و ممکن است برای کاربر داخل ایران در partition قابل دریافت نباشد.
- Web notification/realtime روی Writer محلی اجرا می‌شود.
- Messenger metadata مشترک و media blob دقیقاً طبق Data Ownership Matrix رفتار
  می‌کنند؛ cache/device state sync نمی‌شود.
- notification dedupe و unread state در reconnect دو بار اعمال نمی‌شوند.

## `P2-10` — staging و fault matrix

وضعیت: `PROPOSED`

سناریوهای حداقل:

1. اتصال سالم با lag زیر ۳۰ ثانیه
2. bucket قطع برای Finland، Iran یا هر دو
3. partition وسط upload و وسط ACK
4. gap و object خراب
5. duplicate و out-of-order
6. درخواست هم‌زمان فعال‌سازی انسانی در دو dashboard
7. stale DNS cache
8. restart Writer و standby
9. disk full و clock skew
10. media ناقص
11. offer/trade هم‌زمان روی home siteهای متفاوت
12. negative inventory conflict
13. reconnect دوباره قطع شود
14. failback بعد از final drain شکست بخورد
15. dashboard یا provider API unavailable باشد

هر سناریو expected state، user-visible behavior، alert و recovery command دارد.

## `P2-11` — production standby activation

وضعیت: `PROPOSED — مجوز جدا`

- Iran ابتدا receiver-only و product-blocked نصب می‌شود.
- snapshot و replay تا parity کامل اجرا می‌شود.
- dashboard و auth جدا enroll می‌شوند.
- DNS محصول تغییر نمی‌کند.
- soak حداقل یک دورهٔ مصوب با lag، checksum، backup و model shadow انجام می‌شود.

## `P2-12` — failover/failback drill

وضعیت: `PROPOSED — مجوز جدا`

- drill برنامه‌ریزی‌شده با کاربران/دادهٔ کنترل‌شده اجرا می‌شود.
- زمان fence، فعال‌سازی انسانی، DNS، login، RPO، sync recovery و failback اندازه‌گیری می‌شود.
- هیچ موفقیتی بدون evidence دو dashboard و business parity پذیرفته نمی‌شود.
- rollback و incident report بخشی از gate است.

---

# بخش ۳ — تطبیق Parser و مدل‌های تخمین با معماری جدید

هدف این بخش تبدیل Market Intelligence به pipeline مستقل از محل capture است.
ورودی‌ها Facts نسخه‌بندی‌شده‌اند؛ مدل بر اساس profile و evidence خروجی می‌دهد.

## `P3-00` — baseline علمی و contract inventory

وضعیت: `PROPOSED`

- تمام collectorها، parserها، materializerها، Market Storeها، مدل‌ها، artifactها،
  featureها، freshnessها و consumerها inventory شوند.
- نسخه و نتیجهٔ جاری parser/estimator روی یک replay ثابت freeze شود.
- تفاوت Product=`LEGACY`، Shadow و Private Primary صریح ثبت شود.
- هیچ model promotion در این Stage انجام نمی‌شود.

## `P3-01` — Canonical Market Fact

وضعیت: `PROPOSED`

Fact مشترک حداقل دارد:

```text
fact_id, fact_type, source_family, source_site, source_event_id,
event_revision, occurred_at_utc, available_at_utc, persisted_at_utc,
instrument, settlement, side, price, quantity, unit,
quality_state, parser_version, payload_hash, lineage
```

- raw source و normalized Fact جدا هستند.
- rejected/quarantined Fact برای audit منتقل می‌شود ولی estimator eligible نیست.
- PII و Telegram raw actor در Fact cross-site ممنوع است؛ identity لازم HMAC/pseudonym است.
- correction/revision event است؛ overwrite بدون lineage ممنوع.

## `P3-02` — مالکیت capture و جهت sync

وضعیت: `PROPOSED`

در اتصال عادی:

- Iran: بورس/IME و USDT را دریافت و از Object Storage به Finland می‌فرستد.
- Finland: G1/G2، منابع تلگرامی طلا/آب‌شده/اونس قابل دسترس و Product Events را
  به Iran می‌فرستد.
- Web/Bot offer/trade از دیتابیس محصول materialize می‌شوند و surface فقط metadata
  است؛ یک logical event دوبار شمرده نمی‌شود.
- snapshot نهایی هر inference برای parity و audit سینک می‌شود.

در قطعی:

- Iran دریافت بورس/IME، USDT و Product Events محلی را ادامه می‌دهد.
- مسیر اختیاری دریافت آب‌شده/XAU از منبعی که مالک بعداً فراهم می‌کند از ابتدا
  plug-in contract دارد، ولی requirement پایه نیست.

## `P3-03` — parserهای transport-neutral و deterministic replay

وضعیت: `PROPOSED`

- parser input از Telegram client/HTTP request جدا شود.
- قرارداد settlement، shorthand، tail، quantity، causal reply و trade linking
  جاری حفظ شود.
- parser version روی Fact ثبت و output آن deterministic باشد.
- ambiguous data به REVIEW/REJECT می‌رود؛ constant یا synthetic anchor ممنوع است.
- replay با event/availability/persisted cutoff نتیجهٔ point-in-time درست بدهد.
- parser promotion نیازمند version bump، corpus واقعی و regression dominance است.

## `P3-04` — Product Market Events

وضعیت: `PROPOSED`

آفر و معاملهٔ Bot/Web باید وارد مدل شوند. contract:

```text
event_type, surface, source_site, home_site, authority_class, authority_generation,
offer_public_id/trade_public_id, commodity, settlement, side,
price, quantity, status, occurred_at, price_origin, model_snapshot_id
```

قواعد feedback:

- `USER_ENTERED` معاملهٔ کامل: وزن آموزشی ۲٫۰
- معاملهٔ تأییدشده G1/G2: وزن ۱٫۵
- `MODEL_SUGGESTED_UNCHANGED`: در live حداکثر وزن ۱٫۰، در training target وزن صفر
- `MODEL_SUGGESTED_HUMAN_EDITED`: وزن آموزشی اولیه ۱٫۰
- آفر فعال در live range وزن ۱٫۰ دارد؛ بعد از timeout historical weight برابر ۱/۳
- مجموع offer class در حضور trade حداکثر ۴۰٪ وزن مؤثر است.
- trade، source offer خود را supersede می‌کند.
- quantity خطی weight نمی‌سازد و multiplier حداکثر ۲x است.

این اعداد با `weight_policy_version` ثبت و قبل از promotion backtest می‌شوند.

## `P3-05` — مدل‌ها و artifact authority

وضعیت: `PROPOSED`

- Finland تنها training و model-promotion authority است.
- هر دو سایت inference deterministic روی artifact امضاشده اجرا می‌کنند.
- artifact شامل model weights، feature/schema version، training cutoff، data digest،
  code/release digest، evaluation و signature است.
- artifact فقط پس از shadow parity و تأیید انسانی promote می‌شود.
- cache، optimizer state یا online learner state دوطرفه merge نمی‌شود.
- Iran در partition artifact آخرین نسخهٔ تأییدشده را freeze می‌کند و local inference
  را ادامه می‌دهد.

## `P3-06` — سه profile تخمین

وضعیت: `PROPOSED`

### `FULL_CONNECTED`

همهٔ ورودی‌های معتبر متصل؛ مرجع عادی Finland و shadow Iran.

### `IR_CONTINUITY_BASE`

بورس/IME، USDT، Product Events ایران و تاریخچه/artifact معتبر. نبود G1/G2 به‌تنهایی
خروجی را قطع نمی‌کند.

### `IR_CONTINUITY_ENRICHED`

Base به‌اضافهٔ آب‌شده و/یا XAU که از مسیر اختیاری معتبر به Iran رسیده است.

هر output profile، source coverage، age، confidence و reason code دارد.

## `P3-07` — عمر اطلاعاتی و widening

وضعیت: `PROPOSED؛ سیاست مفهومی تأیید شده، اعداد با backtest نهایی می‌شوند`

عمر عملیاتی آفر با عمر اطلاعاتی آن یکی نیست. آفر لغوشده قابل معامله نیست، ولی
ممکن است با وزن کاهشی evidence تاریخی باشد.

سیاست اولیه:

- trade کامل: ۳۰ دقیقه اثر کامل، تا ۶ ساعت کاهشی، تا ۲۴ ساعت historical weak
- offer فعال محلی: ۱۵ دقیقه قوی، تا ۶۰ دقیقه کاهشی؛ پس از لغو/انقضا live weight صفر
- آخرین G1/G2 قطع‌شده: فقط عمر طبیعی live؛ سپس حداکثر ۶ ساعت historical decay
- IME official close تا session بعد session-valid است.
- USDT زیر ۶۰ ثانیه live؛ قدیمی‌تر با uncertainty penalty، نه عنوان live

```text
final_band = model_band + missing_source_penalty
             + silence_time_penalty + volatility_penalty
```

پیشنهاد اولیهٔ time penalty:

- ۰ تا ۳۰ دقیقه: بدون widening زمانی اضافه
- ۳۰ دقیقه تا ۲ ساعت: هر ۳۰ دقیقه ۰٫۲۵٪ به هر طرف
- ۲ تا ۶ ساعت: هر ساعت ۰٫۵٪ به هر طرف
- بعد از ۶ ساعت: هر ساعت ۱٪ تا سقف ±۱۵٪

وضعیت خروجی:

- `LIVE`: بازه عادی و evidence چندمنبعی
- `CONTINUITY`: بعضی منابع غایب، خروجی معتبر با بازه بزرگ‌تر
- `DEGRADED`: evidence کم؛ نمایش و هشدار، بدون reject آفر
- `REFERENCE_ONLY`: carry-forward uncertainty envelope؛ هرگز گارد رد آفر نیست

تنها artifact خراب/نامعتبر، schema/unit ناسازگار، کالای unsupported یا نبود کامل
baseline معتبر می‌تواند output را ناممکن کند.

## `P3-08` — Price Guard و جلوگیری از ضدامنیت

وضعیت: `PROPOSED`

- فقط snapshot اتمیک HIGH/MEDIUM و underlying واقعاً تازه می‌تواند reject کند.
- `DEGRADED`, `REFERENCE_ONLY`, future/malformed/unsupported fail-open هستند.
- UI باید center، band، confidence، profile و علت widening را نشان دهد.
- یک event منفرد band را ناگهانی جمع نمی‌کند؛ evidence مستقل یا trade معتبر لازم است.
- rejection و override audit می‌شوند و مدل با rejection خام train نمی‌شود.

## `P3-09` — backtest، shadow و cross-site parity

وضعیت: `PROPOSED`

ماتریس:

- replay اتصال عادی
- حذف G1/G2
- فقط IME+USDT
- کاهش شدید Product Events
- ورود/عدم ورود XAU و آب‌شده
- market close و سکوت چندساعته
- shock، outlier، late event، correction و gap
- model-origin feedback loop
- identical facts/artifact روی دو سایت

Gate:

- deterministic output یا توضیح دقیق تفاوت platform.
- coverage و error نسبت به baseline degrade غیرمجاز نداشته باشد.
- widening monotonic و contraction فقط پس از evidence معتبر باشد.

## `P3-10` — promotion و reconnect model gate

وضعیت: `PROPOSED — مجوز جدا برای Product`

`MARKET_READY` فقط وقتی سبز است که:

- Fact streams تا cutoff توافق‌شده gap ندارند.
- artifact/version/schema هر دو سایت برابر است.
- replay checksum و snapshot comparison در tolerance مصوب است.
- rejected conflict حل یا صریحاً waiver انسانی با دامنه محدود دارد؛ synthetic evidence
  waiver ممنوع است.
- Iran Writer تا پایان این gate model output خود را ادامه می‌دهد.

---

# بخش ۴ — ریفکتور Deploy و رفع نواقص تحویل

هدف این بخش ساخت deploy قابل‌فهم، سریع، تکرارپذیر و rollbackپذیر است. امنیت
غیرقابل حذف است، اما gate باید بر اساس blast radius تغییر انتخاب شود؛ اجرای
تست‌های نامرتبط یا کنترل‌های تکراری نباید hotfix ساده را ساعت‌ها متوقف کند.

## `P4-00` — audit خط فعلی deploy

وضعیت: `PROPOSED`

- همهٔ entrypointها در `Makefile`، `deploy.sh`، `scripts/*deploy*`، Composeها،
  Nginx، env renderer، CI و docs inventory شوند.
- هر step با input/output، mutation، privilege، timeout، retry، idempotency و
  مدت واقعی ثبت شود.
- نام‌گذاری تاریخی، hardcode، build تکراری، transfer بزرگ، prompt دستی، test تکراری،
  remote package install و نقطه‌های بدون rollback مشخص شوند.
- baseline یک no-op deploy و app-only change اندازه‌گیری شود.

خروجی: `DEPLOYMENT_CURRENT_STATE_AND_WASTE_REPORT.md` و graph واحد.

## `P4-01` — یک CLI، یک manifest، چند role

وضعیت: `PROPOSED`

target interface:

```text
deploy plan --release <sha>
deploy build --release <sha>
deploy apply --site fi|ir|all --release <digest>
deploy verify --site fi|ir|all --release <digest>
deploy rollback --site fi|ir --to <digest>
deploy cleanup --dry-run
```

- orchestration اصلی تست‌پذیر و ترجیحاً Python است؛ shell فقط wrapper کوچک.
- manifest topology، role، path، domain، artifact source و policy را می‌دهد؛ secret
  value جداست.
- command non-interactive در CI و human-confirmed در production است.
- هر mutation plan JSON و audit receipt دارد.
- old script تا parity command-for-command حذف نمی‌شود.

## `P4-02` — immutable release artifact

وضعیت: `PROPOSED؛ D-06 باید تأیید شود`

- build یک‌بار برای exact commit/lockfiles انجام می‌شود.
- image multi-arch یا architecture-bound با digest صریح تولید می‌شود.
- release manifest شامل code SHA، tree state، image digests، frontend digest، migration
  head، config schema، SBOM، provenance و signature است.
- server build، `pip install` و `npm build` حین deploy عادی ممنوع است.
- Iran نسخهٔ OCI archive امضاشده را در Object Storage ایران دریافت می‌کند تا pull
  خارجی شرط deploy نباشد.
- artifact فعال و rollback قبل از cleanup pin می‌شوند.

## `P4-03` — role-specific runtime و secret projection

وضعیت: `PROPOSED`

- یک immutable image با profileهای `fi-primary` و `ir-standby`.
- env renderer فقط allowlist موردنیاز هر service را می‌دهد.
- Iran هیچ Telegram/API hash/session/publisher token دریافت نمی‌کند.
- dashboard، sync encryption و DNS provider secretها جدا و root-only هستند.
- secret rotation بدون rebuild image و با preflight قابل انجام است.
- config diff باید redacted باشد؛ hash یا چاپ secret نیز ممنوع است.

## `P4-04` — migration و ترتیب rollout

وضعیت: `PROPOSED`

- migrationها expand/contract و backward-compatible بین دو release متوالی‌اند.
- receiver/schema compatibility پیش از sender جدید deploy می‌شود.
- standby قبل از Writer برای schema/sync upgrade می‌شود.
- API canary و readiness پیش از traffic؛ Bot/Queue فقط پس از owner fence.
- migration backup، lock timeout، disk headroom و idempotent second pass دارد.
- destructive contract migration فقط پس از soak release قبلی و مجوز جدا.

## `P4-05` — risk-scoped gate matrix

وضعیت: `PROPOSED`

| نوع تغییر | gate الزامی | gate نامرتبط |
| --- | --- | --- |
| docs-only | lint/link/schema docs | DB backup، Market parity |
| frontend-only | unit/build/browser smoke | Bot identity، DB migration |
| API بدون schema | focused backend + API smoke | full Market replay مگر مسیر Market لمس شده |
| Bot/Queue | identity، lease، queue focused tests | frontend full matrix مگر contract مشترک عوض شده |
| schema/data | backup+restore، migration، parity | هیچ shortcut hotfix |
| sync/writer | full fault matrix و staging | quick lane ممنوع |
| model/parser | replay/backtest/shadow | unrelated UI suite |
| security hotfix | exploit regression + affected smoke + rollback | gateهای طولانی نامرتبط |

هر gate timeout و owner دارد. timeout به معنی success نیست؛ failure class و retry
یا escalation مشخص می‌شود.

## `P4-06` — hotfix lane سریع ولی امن

وضعیت: `PROPOSED؛ D-03 باید تأیید شود`

شرایط ورود:

- incident/hotfix ID و scope مشخص
- commit تمیز و reviewشده
- تغییر schema destructive نیست؛ اگر هست quick lane ممنوع
- focused tests و artifact signature سبز
- rollback artifact آماده

جریان:

1. affected tests و static checks
2. build/reuse artifact
3. plan و diff redacted
4. canary API یا fenced Bot handoff
5. health و synthetic smoke
6. auto-hold در failure، نه ادامهٔ کور
7. post-deploy observation کوتاه و ثبت follow-upهای deferred

امنیت حذف نمی‌شود؛ کار تکراری و نامرتبط حذف می‌شود.

## `P4-07` — staging release و chaos deploy

وضعیت: `PROPOSED`

سناریوها:

- no-op، app-only، frontend-only، migration و rollback
- network timeout، SSH disconnect، registry/bucket failure
- image digest mismatch و partial upload
- migration failure و health failure
- Bot old owner alive
- deploy Iran در حالت بدون اینترنت بین‌الملل
- قطع مجدد وسط deploy/reconnect
- disk full و backup stale

Gate خروج: هر failure state معلوم دارد و اجرای دوباره idempotent است.

## `P4-08` — deploy تولید Finland

وضعیت: `PROPOSED — مجوز جدا`

- از exact release پذیرفته‌شدهٔ `P1-07` استفاده می‌کند.
- backup/off-host receipt، capacity، TLS، firewall و observability قبل از traffic.
- deploy و cutover journal اتمیک/قابل resume است.
- SLOهای D-03 تا D-05 اندازه‌گیری و گزارش می‌شوند.

## `P4-09` — deploy تولید Iran Standby

وضعیت: `PROPOSED — مجوز جدا`

- Iran ابتدا standby و product write-blocked است.
- artifact از مسیر داخلی قابل دسترس و digest برابر Finland دارد.
- no Telegram secret proof، dashboard proof، bucket proof و restore proof لازم است.
- این Stage DNS محصول یا Writer را تغییر نمی‌دهد.

## `P4-10` — rollback، recovery و retention عملیاتی

وضعیت: `PROPOSED`

- application rollback بدون DB restore یک command است.
- DB restore workflow جدا، destructive و نیازمند تأیید دقیق است.
- backup schedule، restore drill و off-host copy اجباری‌اند؛ sync backup نیست.
- logs rotate؛ test results، failed releases، caches، raw Telegram و backup staging
  retention enforce می‌شوند.
- cleanup dry-run، reclaimed bytes، exclusions و last verified backup را گزارش می‌کند.
- active/rollback release و open incident evidence هرگز auto-delete نمی‌شوند.

---

# بخش ۵ — مستندات کامل، قابل نگهداری و قابل اجرای AI

هدف این بخش ساخت «یک سند بزرگ دیگر» نیست؛ هدف ساخت مجموعهٔ کوچک و authoritative
است که انسان، Cursor و عامل عملیات یک رفتار واحد از آن بفهمند.

## `P5-00` — Documentation Information Architecture

وضعیت: `PROPOSED`

ساختار هدف:

```text
docs/two-server/
  README.md
  ARCHITECTURE.md
  DATA_OWNERSHIP.md
  SYNC_PROTOCOL.md
  WRITER_STATE_MACHINE.md
  MARKET_PIPELINE.md
  DEPLOYMENT.md
  HOTFIX.md
  FAILOVER.md
  FAILBACK.md
  BACKUP_AND_RECOVERY.md
  SECURITY.md
  OBSERVABILITY.md
  TEST_AND_ACCEPTANCE_MATRIX.md
  MIGRATION_AND_CUTOVER.md
  decisions/
  schemas/
```

`README.md` نقشهٔ authority، audience، owner و وضعیت هر سند را نشان می‌دهد.

## `P5-01` — قالب سناریومحور مشترک

وضعیت: `PROPOSED`

هر runbook/Stage باید این ترتیب را داشته باشد:

1. هدف و مسئله
2. پیش‌شرط‌ها
3. وضعیت قبل
4. اقدام عامل
5. رفتار داخلی سامانه
6. چیزی که کاربر می‌بیند
7. metric/log/dashboard مورد انتظار
8. failureها و تشخیص
9. rollback یا forward recovery
10. معیار `DONE`

command بدون توضیح اثر، target، privilege و rollback در سند پذیرفته نیست.

## `P5-02` — اسناد معماری و ADR

وضعیت: `PROPOSED`

ADRهای حداقل:

- co-location Bot/Web روی یک DB
- Object Storage به‌عنوان transport
- manual writer handover + signed Fence Receipt
- home_site و conflict policy
- local session policy
- Market Fact و artifact authority
- widening به‌جای no-output
- immutable artifact و risk-scoped deploy gates
- backup مستقل از sync

هر ADR alternatives، دلیل رد، consequences و migration impact دارد.

## `P5-03` — Runbookهای عامل انسانی

وضعیت: `PROPOSED`

سناریوهای تصویری/متنی لازم:

- اتصال سالم روزمره
- هشدار lag یا checksum
- قطع برنامه‌ریزی‌شده و ناگهانی ایران
- فعال‌سازی انسانی Iran Writer
- ورود کاربر با SMS
- reconnect و backlog drain
- conflict و oversell quarantine
- failback با DNS و sync guards
- deploy عادی، hotfix و rollback
- backup/restore و disk pressure
- خرابی dashboard/provider/object storage

هر runbook checklist کوتاه کنار توضیح کامل دارد.

## `P5-04` — مستندات توسعه و قرارداد کد

وضعیت: `PROPOSED`

- module ownership و source-of-truth map
- event/schema catalog با exampleهای بدون داده حساس
- test pyramid و command map
- local/staging setup بدون env تولید
- migration conventions و compatibility policy
- log/metric/error taxonomy
- definition of done و review checklist

`LOCAL_ASSISTANT_CONTEXT.md` و `docs/PROJECT_DOCUMENTATION.md` باید با runtime
واقعی تولید/بازنویسی شوند و ادعاهای تاریخی حذف یا deprecation بگیرند.

## `P5-05` — ممیزی و پاکسازی  مستندات فعلی

وضعیت: `PROPOSED`

برای هر سند tracked یکی از این وضعیت‌ها ثبت شود:

```text
CANONICAL | SUPPORTING | HISTORICAL | SUPERSEDED | DELETE_CANDIDATE
```

- سند تاریخی لازم archive و در header منسوخ علامت می‌خورد.
- duplicate roadmap بعد از استخراج تصمیم حذف می‌شود.
- raw log، screenshot و test evidence فقط با manifest و ضرورت regression در Git
  می‌ماند؛ بقیه به artifact store با retention می‌رود.
- protected UI evidence بدون audit مالکیت حذف نمی‌شود.
- link checker، orphan checker و doc freshness gate به CI اضافه می‌شود.

## `P5-06` — AI execution ledger

وضعیت: `PROPOSED`

برای هر Stage یک task card تولید می‌شود:

```yaml
id: P2-04
status: APPROVED
depends_on: [P2-01, P2-02]
allowed_scope: [...]
forbidden_actions: [...]
required_reads: [...]
deliverables: [...]
tests: [...]
failure_tests: [...]
rollback_test: ...
owner_decisions: [...]
```

Cursor قبل از اجرا branch/status/base را کنترل، سپس source را inspect و در پایان
diff/test receipt خلاصه می‌کند. raw command log tracked نمی‌شود.

## `P5-07` — تبدیل پلن به Cursor Skill و Rules

وضعیت: `PROPOSED — فقط بعد از تأیید پلن`

این پروژه هم‌اکنون `.cursor/skills/` و `.cursor/rules/` دارد؛ بنابراین تبدیل
ممکن است. خروجی پیشنهادی:

```text
.cursor/skills/two-server-refactor/SKILL.md
.cursor/skills/two-server-refactor/references/STAGE_PROTOCOL.md
.cursor/skills/two-server-refactor/references/SAFETY_GATES.md
.cursor/rules/two-server-refactor-safety.mdc
```

Skill فقط workflow و routing را نگه می‌دارد و متن این پلن را duplicate نمی‌کند.
ویژگی‌های لازم:

- نام `two-server-refactor`
- invocation ترجیحاً صریح برای اجرای Stage
- الزام خواندن task card و source-of-truthهای Stage
- ممنوعیت push/merge/deploy/DNS/production بدون دستور جدا
- یک Stage در هر invocation
- توقف روی owner decision باز
- تست failure و rollback اجباری
- ممنوعیت complete اعلام‌کردن با fixture-only evidence

Rule کوچک و همیشه‌اعمال فقط invariantهای ایمنی را نگه می‌دارد؛ جزئیات حجیم در
Skill و docs باقی می‌ماند.

مرجع قالب و discovery باید مستندات رسمی Cursor باشد:

- <https://cursor.com/docs/skills>
- <https://cursor.com/docs/rules>

## `P5-08` — پذیرش نهایی و handoff

وضعیت: `PROPOSED`

Gate نهایی:

- پنج بخش این پلن tracker کامل دارند.
- تمام تصمیم‌های باز بسته یا به‌صورت صریح deferred شده‌اند.
- architecture، runtime، manifest، dashboard و runbook یک واژگان دارند.
- commandهای مستند در clean environment آزمایش شده‌اند.
- یک عامل انسانی از روی runbook و یک Cursor Agent از روی Skill، rehearsal یکسان
  را بدون دانش شفاهی انجام می‌دهند.
- سند منسوخ به‌عنوان مرجع جاری در search result داخلی باقی نمانده است.
- restore، failover، failback و rollback receipt واقعی staging موجود است.

---

## قرارداد تست سراسری

هر تغییر حداقل این لایه‌ها را متناسب با scope پوشش می‌دهد:

1. unit برای policy/state machine/parser/config
2. property/invariant برای ordering/idempotency/conflict
3. integration با PostgreSQL/Redis/S3-compatible store
4. Compose runtime test برای role isolation
5. staging scenario برای network/DNS/manual-handover/fence-receipt/reconnect
6. browser test برای dashboard و product behavior
7. load/soak برای lag، backlog، memory، disk و deploy time
8. restore/rollback drill

تست success بدون تست failure متناظر gate را کامل نمی‌کند.

## قرارداد commit و branch برای اجرا

- این شاخه فقط تدوین پلن است.
- پس از تأیید، implementation branch تازه از `main` ساخته می‌شود؛ نام پیشنهادی:
  `refactor/two-server-architecture-v1`.
- هر Stage یک یا چند commit منسجم با Stage ID در subject دارد.
- unrelated changes وارد Stage نمی‌شوند.
- قبل و بعد هر Stage `git status`, branch و base ثبت می‌شوند.
- merge فقط پس از review مستقل و gate همان موج است.
- branch پس از merge/رد شدن طبق retention حذف می‌شود؛ branch backup دائمی نیست.

## Definition of Done کل پروژه

پروژه فقط وقتی از این ریفکتور عبور کرده است که:

1. Bot و Web روی Finland Primary با یک DB/Redis و بدون sync داخلی دو-سروره اجرا شوند.
2. Finland دقیقاً یک Telegram owner و Iran صفر Telegram credential داشته باشد.
3. Iran در حالت عادی standby همگام و product-write-blocked باشد.
4. انتقال انسانی، Fence Receipt مبدأ و گارد ترتیب dashboard دو Web Writer را ببندند.
5. failover، reconnect و failback در staging و سپس با مجوز در production ثابت شوند.
6. تمام داده‌های shared و Market Facts registry، sequence، ACK، checksum و repair
   قابل اثبات داشته باشند.
7. مدل‌های `FULL_CONNECTED` و `IR_CONTINUITY_*` با widening و confidence درست
   کار کنند.
8. deploy عادی و hotfix از artifact immutable، یک CLI و gate متناسب استفاده کنند.
9. rollback و restore اندازه‌گیری و قابل اجرا باشند.
10. repository، log، test output، data و backup retention enforce شوند.
11. مستندات canonical و Cursor Skill به‌روز و قابل اجرا باشند.
12. هیچ server قدیمی، script قدیمی یا سند منسوخ source of truth پنهان نباشد.

---

## مواردی که این نسخه عمداً انجام نداده است

- هیچ کدی را refactor نکرده است.
- هیچ Stage را `APPROVED` یا `COMPLETE` اعلام نکرده است.
- هیچ secret یا manifest خصوصی را نخوانده یا ثبت نکرده است.
- هیچ server inventory زنده، deploy، DNS، bucket write یا database write انجام نداده است.
- Cursor Skill را هنوز نساخته است؛ ساخت آن پس از تأیید محتوای همین پلن انجام می‌شود.
