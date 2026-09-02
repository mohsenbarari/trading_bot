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
- ادغام دو میزبان Finland یک refactor صرفاً توپولوژیک و behavior-preserving است.
  هیچ قابلیت، policy، API، callback، متن کاربر، state transition، زمان‌بندی یا
  side effect نباید پنهانی تغییر کند؛ هر تغییر محصولی change set و تأیید جدا دارد.
- Web و Bot دو surface متمایزند. `origin_surface` هم provenance تغییرناپذیر و هم
  ورودی policy نسخه‌دار است؛ اشتراک مدل داده به معنی یکسان‌کردن policyهای وقت
  اضافه، سطح مشتری، انتشار، محدودیت، تأیید یا notification نیست.
- Offer منشأ تغییرناپذیر Web/Bot/Internal را مستقل از `home_site` نگه می‌دارد؛
  جابه‌جایی authority یا هم‌مکانی processها حق بازنویسی منشأ را ندارد.
- Trade هنگام ایجاد، منشأ Offer، منشأ Request، surface اجرای نهایی، policy version
  و context حساس actor/role/tier را snapshot و تغییرناپذیر می‌کند تا تصمیم تاریخی
  بدون اتکا به وضعیت بعدی رکوردهای مرتبط قابل‌بازتولید باشد.
- تا کامل‌شدن و تأیید کل پلن، هیچ provisioning، deploy، migration، DNS change،
  cleanup runtime یا cutover عملیاتی انجام نمی‌شود. تأیید پلن نیز مجوز خودکار
  اجرای Stageهای تولیدی نیست.
- `D-07`: budget اولیهٔ staging در بار مرجع CPU پایدار حداکثر ۶۰٪، RAM و disk
  حداکثر ۷۰٪ و DB pool حداکثر ۷۰٪ است؛ baseline می‌تواند بازبینی عددی را الزام کند.
- `D-08`: حداقل soak معماری یکپارچهٔ Finland برابر ۲۴ ساعت با job، queue، backup،
  log rotation و fault telemetry فعال است.
- `D-10`: پس از cutover، پایش فعال ۲ ساعت است؛ sourceهای قدیمی حداقل ۷ روز fenced
  و قابل‌بازیابی و backup مصوب ۳۰ روز نگه داشته می‌شود. پایان retention حذف خودکار
  نیست و `P1-08/P2-11` و مجوز جدا همچنان الزامی‌اند.
- `D-09`: cutover یک‌بارهٔ ادغام دو Finland فعلی یک پنجرهٔ رزروشدهٔ ۹۰ دقیقه‌ای
  دارد؛ تمام preflightها پیش از freeze انجام می‌شوند، توقف دسترسی/write حداکثر
  ۴ دقیقه است و اگر target تا آن زمان آماده نباشد عملیات abort می‌شود. این تصمیم
  SLO دیپلوی‌های بعدی نیست.

### نیازمند تأیید در بازبینی این پلن

| ID | تصمیم پیشنهادی | مقدار اولیه | وضعیت |
| --- | --- | --- | --- |
| `D-02` | DNS TTL محصول | ۳۰ ثانیه در دورهٔ آماده‌سازی/cutover؛ مقدار عادی پس از soak جدا تعیین شود | باز؛ سقف قطعی ۴ دقیقه تأیید شده، TTL هنوز نیازمند تأیید است |
| `D-03` | SLO deploy hotfix بدون migration | حداکثر ۱۵ دقیقه از artifact تأییدشده تا health سبز | موکول به بررسی عمیق بخش ۴ |
| `D-04` | SLO release عادی دو سرور | حداکثر ۳۰ دقیقه در حالت اتصال سالم | موکول به بررسی عمیق بخش ۴ |
| `D-05` | SLO rollback کد بدون DB restore | حداکثر ۱۰ دقیقه | موکول به بررسی عمیق بخش ۴ |
| `D-06` | artifact distribution | registry اصلی + OCI archive امضاشده در Object Storage ایران برای Iran/offline | باز؛ پس از توضیح و بررسی بخش ۴ |
| `D-11` | trigger عددی rollback cutover | invariant/duplicate-owner/hash/durability فوراً؛ error rate >۲٪ برای ۵ دقیقه، p95 >۲× baseline برای ۱۰ دقیقه یا queue lag >۳۰ثانیه برای ۵ دقیقه | باز؛ نیازمند تأیید آستانه‌ها و انسانی‌بودن فرمان rollback |

این موارد تا تأیید مالک، requirement پیشنهادی‌اند و Cursor حق تثبیت پنهان آن‌ها
در کد را ندارد.

`D-09` تأییدشده فقط انتقال اولیه از دو میزبان Finland قدیمی به Finland Primary است.
release، hotfix و rollbackهای روزمرهٔ معماری جدید قرارداد، زمان و gate مستقل خود
را در بخش ۴ دارند و نباید پنجرهٔ ۹۰ دقیقه/وقفهٔ ۴ دقیقه را به ارث ببرند.

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
18. اجرای Stage تغییردهندهٔ Finland قبل از تأیید Current-State Architecture
    Dossier و Feature Parity Contract ممنوع است؛ رفتار ناشناخته یا اختلاف بی‌توضیح
    میان current و target، gate را قرمز می‌کند.
19. bug یا رفتار نامطلوب کشف‌شده در ممیزی، داخل refactor توپولوژی silently fix
    نمی‌شود؛ ابتدا ثبت و سپس در change set مستقل تصویب یا صریحاً برای parity حفظ می‌شود.

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
| `P1-06` + `P4-07` | `P1-05`, `P4-04`, `P4-05`, `P4-06` و تصمیم‌های `D-07`, `D-08` |
| `P1-07` + `P4-08` | `P1-06`, `P4-07`, `P4-10`، تصمیم‌های `D-09`, `D-11` و مجوز تولید |
| `P1-08` | `P1-07`, `P2-11`، تصمیم `D-10` و retention/backup approval |
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

هدف این بخش انتقال قابلیت‌های دو میزبان فعلی Finland به یک Finland Primary با
IP فعلی `65.109.214.203` است. Web/API و Bot همچنان process/containerهای مستقل
هستند، اما از یک PostgreSQL، یک Redis و یک مجموعهٔ مشترک از سرویس‌های محلی
استفاده می‌کنند. sync شبکه‌ای میان Bot-Finland و Web-Finland حذف می‌شود؛ sync
میان Finland و Iran موضوع بخش ۲ است و حذف نمی‌شود.

این بخش یک **تغییر توپولوژی با الزام حفظ کامل رفتار** است. «ادغام» به معنی
یکی‌کردن برنامه، handler، policy یا تجربهٔ Web و Bot نیست. منشأ Web/Bot باید
به‌عنوان provenance تغییرناپذیر و ورودی policy نسخه‌دار باقی بماند. تفاوت‌های
موجود در tier 2، overtime، quota، confirmation، publication، notification،
commission و هر رفتار دیگری فقط با تصمیم جداگانهٔ مالک قابل‌تغییر است.

### روایت انسانی: قبل، بعد و هنگام خطا

**قبل از ریفکتور:** WebApp و Bot روی دو میزبان Finland اجرا می‌شوند. بخشی از
داده یا side effect از مسیر HTTP/sync داخلی عبور می‌کند و deploy، backup، restart
و تشخیص owner سرویس‌ها میان دو ماشین پخش شده است.

**بعد از ریفکتور:** کاربر Web همان API و رفتار قبلی را می‌بیند و کاربر Bot همان
command، callback، پیام و محدودیت قبلی را تجربه می‌کند. تفاوت فقط این است که
هر دو runtime روی یک میزبان و یک data plane هستند. ثبت Offer از Web همچنان
«Web-origin» و ثبت Offer از Bot همچنان «Bot-origin» است؛ همین قاعده مستقل برای
Request، Trade و actor/persona/tier نیز برقرار است.

**اگر یکی از processها خراب شود:** restart شدن Web/API نباید Bot را مجبور به
restart کند و خرابی Bot نباید API را از دسترس خارج کند. اگر PostgreSQL، Redis،
migration یا شرط لازم یک side effect آماده نباشد، سرویس وابسته باید fail-closed
شود؛ یعنی نباید به‌ظاهر موفق باشد و state ناقص یا side effect تکراری بسازد.

**اگر ادغام داده ناسالم باشد:** هیچ cutover انجام نمی‌شود. rehearsal روی clone
جداگانه تکرار می‌شود و conflictهای مالی، موجودی، identity یا media به quarantine
می‌روند. overwrite خام یا «آخرین مقدار برنده است» برای دادهٔ کسب‌وکار ممنوع است.

**اگر پس از cutover مشکل دیده شود:** تا قبل از اولین write روی target می‌توان به
دو source قبلی بازگشت. پس از اولین write، rollback اصلی بازگرداندن نسخهٔ برنامه
روی همان target است؛ روشن‌کردن مستقیم دیتابیس قدیمی بدون reconciliation مجاز
نیست.

### مرز تغییر این بخش

| در scope | خارج از scope |
| --- | --- |
| inventory و مدل دقیق معماری جاری | اصلاح رفتار محصول یا bug نامرتبط |
| پاکسازی repository/worktree/artifact با approval | حذف خودکار فایل، branch، backup یا سرور |
| config و compose مستقل از IP تاریخی | deploy تولید بدون مجوز جدا |
| هم‌مکان‌کردن Web، Bot، DB و Redis | یکی‌کردن Web و Bot در یک process |
| حذف transport داخلی میان دو Finland | حذف outbox/sync میان Finland و Iran |
| rehearsal، staging، cutover و rollback قابل‌اثبات | تغییر DNS، token یا دادهٔ تولید در شاخهٔ پلن |
| حفظ و تست تفاوت policy میان surfaceها | برابر فرض‌کردن Offer/Request وب و بات |

### ترتیب قابل‌مرور برای مالک

1. `P1-00`: بفهمیم اکنون دقیقاً چه داریم و چه رفتاری باید حفظ شود.
2. `P1-01`: repository و artifactها را با manifest و retention پاکسازی کنیم.
3. `P1-02`: config را از نام‌ها و IPهای تاریخی مستقل کنیم.
4. `P1-03`: runtime هدف یک‌سروره را بدون deploy تولید بسازیم.
5. `P1-04`: sync محلی دو Finland را به DB transaction/outbox درست تبدیل کنیم.
6. `P1-05`: ادغام واقعی داده را چندبار روی clone تمرین و rollback کنیم.
7. `P1-06`: رفتار معماری جاری و هدف را در staging به‌صورت differential بسنجیم.
8. `P1-07`: فقط با مجوز جدا، cutover اتمیک داده/runtime/deploy را انجام دهیم.
9. `P1-08`: پس از دورهٔ اطمینان، بدهی و منابع قدیمی را کنترل‌شده ببندیم.

`P1-02..P1-07` تا زمان تأیید artifactهای `P1-00` اجازهٔ تغییر behavior ندارند.
`P1-07` با `P4-08` یک change set عملیاتی است؛ جداسازی آن دو ممنوع است.

### قرارداد اجرایی Cursor برای تمام Stageهای بخش ۱

Cursor در آغاز هر Stage باید یک Task Card در مسیر زیر ایجاد یا تکمیل کند:

```text
docs/refactor/two-server/section-1/stages/P1-XX.md
```

هر Task Card باید دقیقاً این فیلدها را داشته باشد:

```text
status
approved_by / approved_at
base_branch / base_commit
goal / non_goals
dependencies and their evidence
files allowed to change
external systems allowed to read or write
pre-change snapshot
ordered implementation steps
tests: success / failure / idempotency / rollback / parity
evidence paths and SHA-256
known gaps / owner decisions
rollback trigger / rollback steps / rollback verification
resulting commits
human gate receipt
```

قواعد اجرای Task Card:

1. حافظهٔ پروژه، `AGENTS.md`، همین Stage و dependencyهای آن کامل خوانده شوند.
2. branch اجرا از commit مصوب ساخته و `base_commit` ثبت شود؛ working tree باید
   تمیز باشد. تغییر موجود کاربر حذف، stash یا بازنویسی نمی‌شود.
3. قبل از edit، فهرست دقیق فایل‌های در scope و testهای baseline ثبت شود.
4. هر Stage در branch و commitهای خودش اجرا شود. فقط Stageهایی که این سند
   اتمیک اعلام کرده می‌توانند در یک change set باشند.
5. ابتدا characterization/golden test نوشته یا baseline موجود اجرا شود، سپس
   implementation انجام شود. تستی که رفتار جاری را اثبات می‌کند برای تطبیق با
   implementation جدید تضعیف نمی‌شود.
6. خروجی خام، log و dump در Git قرار نگیرد. محل محلی evidence:
   `.local/test-results/two-server-refactor/P1-XX/<run-id>/` است. فقط index،
   checksum، نتیجه و دادهٔ sanitised لازم در docs track می‌شود.
7. secret value، Telegram token/session، cookie، DB credential و کلید Arvan
   نباید چاپ، commit یا داخل evidence ذخیره شود. فقط نام secret، mount و نتیجهٔ
   validation ثبت می‌شود.
8. هر command مخرب ابتدا dry-run و manifest هدف می‌خواهد. حذف branch، worktree،
   backup، volume، host یا داده نیازمند gate انسانی همان Stage است.
9. دسترسی read-only به runtime و دسترسی write به staging/production دو مجوز
   متفاوت‌اند. نبود مجوز با حدس یا fixture پنهان جبران نمی‌شود.
10. اگر response، state، ordering، timeout، copy یا side effect جاری با سند فرق
    داشت، Cursor آن را در Drift Register ثبت و Stage را `BLOCKED` می‌کند؛ انتخاب
    یکی از دو رفتار بدون تصمیم مالک ممنوع است.
11. هر command و exit code، commit، image digest، migration version و checksum
    لازم برای بازتولید نتیجه در Evidence Index ثبت شود.
12. Cursor حق اجرای Stage بعدی را فقط با `human gate receipt` مرحلهٔ قبلی و
    dependencyهای سبز دارد.

ساختار artifactهای tracked این بخش:

```text
docs/refactor/two-server/section-1/
├── 00-stage-ledger.md
├── 01-current-finland-architecture.md
├── 02-runtime-inventory.md
├── 03-dataflow-and-ownership.md
├── 04-surface-policy-matrix.md
├── 05-feature-parity-contract.md
├── 06-current-drift-register.md
├── 07-repository-cleanup-manifest.md
├── 08-target-configuration-contract.md
├── 09-target-runtime-topology.md
├── 10-finland-data-merge-contract.md
├── 11-staging-acceptance.md
├── 12-cutover-runbook.md
├── 13-closure-and-decommission.md
├── evidence-index.md
└── stages/P1-00.md ... stages/P1-08.md
```

فایل‌های `00..07` و Task Card `P1-00` در همین شاخه با ممیزی read-only ایجاد
شده‌اند. بقیهٔ فایل‌ها خروجی Stageهای آینده‌اند و پیش از dependency و gate مربوط
نباید با نتیجهٔ حدسی پر شوند.

## `P1-00` — Current-State Architecture و Behavior Baseline

وضعیت: `IN_PROGRESS — Gate انسانی در 2026-09-02 تأیید شد؛ تکمیل شواهد و rehearsal باز است`

Dependency: تأیید همین پلن؛ برای مشاهدهٔ runtime، مجوز read-only جداگانه.

### برداشت انسانی

این مرحله نقشه‌برداری است، نه اصلاح. نتیجه باید به این سؤال پاسخ دهد: «اگر دو
سرور را خاموش کنیم، دقیقاً چه process، داده، رفتار، job یا side effectی ممکن است
از دست برود؟» تا زمانی که پاسخ مستند نباشد، هیچ پیاده‌سازی ادغام قابل‌اعتماد نیست.

### Task Card فنی Cursor

1. **Preflight:** SHA مبنا، وضعیت branch/worktree، نسخهٔ schema/migration و فهرست
   منابع قابل‌خواندن را ثبت کند. هر runtime فاقد مجوز با `NOT_OBSERVED` مشخص شود.
2. **Static inventory:** با `rg --files` و جست‌وجوی هدفمند، composeها، Dockerfileها،
   systemd/cron/timer، deploy/runtime scriptها، env keyها بدون value، migrationها،
   routeها، Bot handler/callbackها، workerها، schedulerها، listenerها، queueها،
   publisherها، backup/restore و testها را inventory کند.
3. **Runtime inventory:** فقط read-only و روی هر دو Finland فعلی، hostname/OS،
   service/container، image digest، port/network، volume/media، mount secret،
   DB/Redis role، scheduler، queue depth، sync checkpoint، backup location و
   Telegram owner را با timestamp ثبت کند. هیچ config کامل یا env dump مجاز نیست.
4. **Target inventory:** SSH host-key fingerprint، OS، disk layout، CPU/RAM،
   firewall/port و ظرفیت `65.109.214.203` را با
   `docs/architecture/FINLAND_PRIMARY_TARGET.md` تطبیق دهد؛ mismatch blocker است.
5. **Data ownership:** برای هر table/domain، writer، reader، stable identity،
   transaction boundary، FK، sequence، media relation، cache، outbox، sync direction،
   retention، backup و restore path را ثبت کند.
6. **Mutation graph:** هر API endpoint، Web action، Bot command/callback، admin
   action، scheduled job و consumer را به جدول‌ها، eventها و side effectها وصل کند.
   edge بدون owner یا مسیر write بدون idempotency باید صریحاً علامت بخورد.
7. **Behavior matrix:** حداقل حوزه‌های auth/OTP/session، invitation/relation،
   Offer، Request، Trade، overtime/expiry/republish، Market guard، Messenger/media/
   realtime، Queue/Telegram، notification، parser/estimator و عملیات deploy/backup
   را بر اساس surface، persona، role و tier پوشش دهد.
8. **Surface policy:** برای Offer و Request دو provenance مستقل ثبت کند؛ ترکیب
   `offer_surface × request_surface × actor_role × customer_tier × time_context`
   باید eligibility، confirmation، quota، commission، publication، notification،
   timeout و failure behavior موجود را نشان دهد.
9. **Parity contract:** برای هر رفتار یک `behavior_id` با precondition، input،
   response/status/copy، DB mutation، event/outbox، audience/side effect، ordering،
   timeout، retry، idempotency، failure، evidence و regression test بسازد.
10. **Characterization:** برای رفتارهای پرریسک فاقد تست، ابتدا test مشاهده‌ای یا
    golden fixture بدون side effect خارجی بسازد. این تست‌ها رفتار موجود را ثبت
    می‌کنند و مجوز درست‌دانستن bug نیستند.
11. **Drift review:** اختلاف تصمیم مالک، code، runtime، test و docs را با severity،
    impact و evidence ثبت کند. هیچ اختلافی خودکار resolve نشود.
12. **Human review:** Dossier، Runtime Inventory، Dataflow، Surface Policy Matrix،
    Feature Parity Contract و Drift Register را برای review مالک آماده کند.

### خروجی و شواهد

- فایل‌های `01` تا `06` از ساختار canonical این بخش.
- baseline زمان deploy، restart، backup، restore، smoke و منابع هر دو میزبان.
- machine-readable inventory sanitised در کنار Markdown، با schema و checksum.
- Coverage report که endpoint، callback، job و side effect بدون `behavior_id` را
  صفر یا blocker نشان دهد.

### Gate انسانی و معیار خروج

- مالک در 2026-09-02، Dossier، Drift Register، قرارداد دوازده خانوادهٔ رفتاری،
  حفظ تفاوت‌های Web/Bot و provenance، رسیدگی اجباری به شش شکاف مدرک و baseline
  مالکیت runtime را تأیید کرد. این تأیید فقط baseline را freeze می‌کند.
- هیچ service، writer، scheduler، Telegram identity یا data store ناشناخته نماند.
- هر secret فقط با نام و محل mount دیده شود و audit هیچ write خارجی نداشته باشد.
- هر behavior جاری یا contract/test دارد یا blocker مصوب؛ «احتمالاً استفاده
  نمی‌شود» معیار حذف نیست.

ممنوع: refactor، rename، migration، deploy، restart، write روی runtime یا اصلاح
bug. تغییر repository فقط به docs و characterization testهای این Stage محدود است.
Rollback با revert همان commit انجام و artifactهای محلی audit طبق retention حذف
می‌شوند؛ خود audit نسبت به runtime و دادهٔ خارجی کاملاً read-only است.

## `P1-01` — پاکسازی و یکپارچگی repository محلی

وضعیت: `PROPOSED — سیاست و Retention در 2026-09-02 تأیید شد؛ اجرا هنوز مسدود است`

Dependency: `P1-00` و تأیید انسانی Cleanup Manifest قبل از هر حذف.

### برداشت انسانی

repository اصلی تنها محل source code و اسناد معتبر است. log، dump، backup، raw
Telegram data و test output یا باید در مسیر local مدیریت‌شده با owner/retention
باشند یا حذف شوند. فایل مفید خارج repository ابتدا به مسیر درست منتقل و سپس با
تست اثبات می‌شود؛ فایل ناشناخته صرفاً به‌دلیل قدیمی‌بودن حذف نمی‌شود.

### Task Card فنی Cursor

1. اندازهٔ repository، `git status`، tracked/untracked/ignored files، branchها،
   remote refs و `git worktree list --porcelain` را بدون تغییر ثبت کند.
2. فقط در rootهای ازپیش‌تأییدشدهٔ پروژه/deploy، clone، worktree، `.git`، backup،
   log، dump، release و artifact پراکنده را پیدا کند. اسکن یا حذف مسیر گستردهٔ
   نامشخص، `/`، home یا symlink target ممنوع است.
3. هر مورد را در `07-repository-cleanup-manifest.md` با این فیلدها طبقه‌بندی کند:
   `path`, `type`, `tracked`, `owner`, `size`, `last_used`, `reference`, `action`,
   `destination`, `retention`, `expires_at`, `rollback`.
4. action فقط یکی از `KEEP`, `MOVE`, `DELETE`, `QUARANTINE`, `BLOCKED` باشد. هیچ
   مورد بدون owner/reference analysis به `DELETE` نرود.
5. تمام branch/worktreeهای نامزد حذف و branch مربوط به Cursor را با diff نسبت به
   مبنای مناسب، unique commits و artifactهای مستندی ممیزی کند. اگر ارزش مستقلی
   ندارد، حذف آن فقط پس از درج SHA و تأیید مالک انجام شود.
6. `candidate/wa-ir-standby-v1` را فقط برای استخراج تصمیم، test idea و docs مفید
   ممیزی کند؛ merge/cherry-pick کد ممنوع است. این branch تا دستور جداگانهٔ مالک
   باقی می‌ماند، حتی اگر در این Stage مرجع مفیدی پیدا نشود.
7. tracked `tmp/`، log، screenshot، dump، generated report و test output را پیدا
   و مصرف‌کنندهٔ واقعی آن را ثابت کند. مورد لازم به مسیر source/docs canonical
   منتقل می‌شود؛ مورد runtime به `.local` و `.gitignore` contract می‌رود.
8. مسیرهای محلی را به
   `.local/{logs,test-results,deploy,backups,data,caches,quarantine}` استاندارد کند
   و برای هرکدام owner، quota/size alert، retention و cleanup schedule تعریف کند.
9. raw Telegram eventها را بر اساس source/date partition، stable event id
   deduplicate، پس از بازهٔ hot فشرده و با policy «raw/derived/replay-required»
   نگهداری کند. هیچ داده‌ای بدون اثبات replay/audit need نامحدود نمی‌ماند.
10. برای backup، release و quarantine حداقل `created_at`, `owner`, `reason`,
    `restore_status`, `expires_at`, `protected_until` ثبت کند؛ backup بدون restore
    test محافظ rollback محسوب نمی‌شود.
11. cleanup tooling را ابتدا dry-run کند. ابزار باید root-bound، allowlist-based،
    symlink-safe، lock-aware و idempotent باشد و active release/current rollback/
    protected backup را رد کند.
12. پس از gate انسانی، فقط موارد دقیق manifest را اجرا و سپس reference scan، test،
    `git status` و اندازهٔ قبل/بعد را ثبت کند. مورد متفاوت از manifest نیازمند gate
    جدید است.

### خروجی، آزمون و Gate انسانی

- Cleanup Manifest، retention matrix، branch/worktree audit و size report قبل/بعد.
- dry-run test، path-escape/symlink test، protected-artifact test و اجرای دوم
  idempotent بدون حذف تازه.
- repository تمیز، یک worktree canonical و هیچ runtime artifact جدید tracked نباشد.
- هر batch دقیق و هم‌نوع از cache/log یک رسید گروهی می‌خواهد؛ branch، worktree،
  backup، data set یا runtime مادی هرکدام رسید انسانی مستقل می‌خواهند.

Gate سیاست در 2026-09-02 تأیید شد: layout canonical، حذف گروهی quarantine-first،
بازه‌های retention، رفتار مسیرهای حجیم، تکمیل اجباری manifest و مرز جداگانهٔ
runtimeهای سرور پذیرفته شدند. این تأیید مجوز جابه‌جایی یا حذف نیست؛ اجرای هر batch
پس از بسته‌شدن `P1-00` و با receipt دقیق انجام می‌شود.

Rollback: موارد قابل‌حذف ابتدا تا پایان بازهٔ مصوب به quarantine قابل‌بازگشت منتقل
شوند؛ حذف نهایی فقط بعد از expiry و اثبات نبود reference انجام شود.

## `P1-02` — واژگان و configuration بدون topology تاریخی

وضعیت: `PROPOSED — قرارداد سناریویی در 2026-09-02 تأیید شد؛ اجرا مسدود است`

Dependency: `P1-00`، `P4-00` و تأیید Feature Parity Contract.

### برداشت انسانی

کد نباید از روی نام‌هایی مثل `foreign` یا `iran` و IP قدیمی نتیجه بگیرد که چه
کسی Writer، Bot owner یا collector است. محل فیزیکی، نقش runtime و منشأ درخواست سه
مفهوم جدا هستند. این جداسازی شرط ادغام Finland و بعداً ساخت Iran Standby است.

### سناریوهای تأییدشده

| سناریو | رفتار الزامی |
| --- | --- |
| اتصال عادی | Web فلاند Writer، Web ایران Standby و Bot فلاند مستقل است؛ هر process فقط capability مصوب خود را دارد. |
| Offer از Web/Bot روی یک host | `origin_surface` تفاوت محصول را حفظ می‌کند و `site_id` جای آن را نمی‌گیرد. |
| خرابی Web فلاند | Bot ادامه می‌دهد و ایران خودکار Writer نمی‌شود؛ تصمیم انتقال انسانی است. |
| انتقال انسانی به ایران | عامل، Finland Web را drain/fence می‌کند، DNS را تغییر می‌دهد و سپس ایران را صریحاً فعال می‌کند؛ هیچ lease/timeout دخیل نیست. |
| قطعی ارتباط مستقیم | writer ایران و Bot فلاند مستقل کار می‌کنند؛ outboxها پایدارند و source/home از topology حدس زده نمی‌شود. |
| بازگشت به فلاند | ایران fence، DNS فلاند verify و tail sync/parity کامل می‌شود؛ فقط سپس عامل Finland Writer را فعال می‌کند. قطعی write تا 3–4 دقیقه پذیرفته است. |
| config متناقض | دو Telegram owner، دو Writer، schema ناشناخته یا secret mount غایب قبل از readiness fail closed می‌شود؛ انتخاب خودکار ممنوع است. |
| تغییر IP/domain | فقط manifest خصوصی و endpoint registry تغییر می‌کند؛ کد و policy ثابت می‌ماند. |
| مهاجرت legacy | config قدیم/جدید فقط در صورت برابری dual-read می‌شوند؛ mismatch startup را متوقف می‌کند و adapter پس از صفرشدن مصرف در `P1-08` حذف می‌شود. |

در promotion عادی، sync lag ناشناخته یا بیش از 30 ثانیه مانع فعال‌سازی است.
Emergency override بخشی از قرارداد مصوب نیست و در صورت نیاز به تصمیم جداگانه
احتیاج دارد. `P1-02` فقط مدل config را تعریف می‌کند و handover را اجرا نمی‌کند.

### Task Card فنی Cursor

1. تمام `foreign/iran/finland/server_mode`، IP/domain/path ثابت، compose project
   name و شرط‌هایی که topology را از Web/Bot surface حدس می‌زنند inventory کند.
2. schema typed و versioned تعریف کند که حداقل این مفاهیم را جدا نگه دارد:
   `site_id`, `service_role`, `capabilities`, `writer_eligibility`,
   `database_role`, `redis_role`, `endpoint_registry`, `origin_surface`,
   `policy_version`, `config_schema_version`.
3. invariantها را صریح کند: روی target Finland، Web/API و Bot هم‌مکان‌اند؛ فقط یک
   Telegram executor مجاز است؛ `origin_surface` writer/site را تعیین نمی‌کند؛
   eligibility ثابت از `active_web_writer_site` انسانی و بدون expiry جداست؛ config
   متناقض پیش از اتصال به DB/Telegram fail می‌شود.
4. manifest عمومی بدون secret و manifest خصوصی محیط را جدا کند. secret فقط از
   mount/store مجاز resolve شود؛ generated config باید schema validation و hash
   قابل‌ثبت داشته باشد.
5. adapter سازگاری برای `server_mode` قدیمی بسازد: در فاز اول dual-read فقط هنگام
   برابری معنایی با warning و metric، و در mismatch توقف startup؛ در فاز دوم
   callsiteها روی schema جدید، و حذف adapter فقط در `P1-08` پس از صفرشدن مصرف.
   dual-write authority ممنوع است.
6. callsiteها را domain به domain و با commit کوچک migrate کند؛ پس از هر دسته،
   golden/characterization tests همان behavior اجرا شود.
7. fixtureهای IP/domain تاریخی را با placeholder مستند جایگزین کند؛ testی که
   عمداً IP target را می‌سنجد باید allowlist و دلیل داشته باشد.
8. CI guard برای hardcode شدن IP، domain، absolute project path، secret key/value،
   compose identity و topology inference اضافه کند.
9. failure testها را برای missing key، enum نامعتبر، writer متناقض، دو Telegram
   owner، schema version ناسازگار و secret mount غایب اجرا کند.
10. config matrix فعلی و هدف را generate و diff کند؛ تنها تفاوت‌های topology
    مصوب‌اند. response schema، callback data، copy و policy نباید تغییر کند.

### خروجی، آزمون و Gate انسانی

- `08-target-configuration-contract.md` شامل schema، example sanitised، invariant،
  compatibility phases و deprecation ledger.
- unit/schema/negative test، hardcode scan و parity diff سبز.
- با تغییر IP/domain فقط manifest خصوصی و artifact تولیدشده تغییر کند.
- حذف legacy name یا adapter در این Stage ممنوع؛ مالک قرارداد config و diff رفتار
  را قبل از merge تأیید کند.

Gate طراحی در 2026-09-02 تأیید شد: تفکیک `site_id/service_role/capabilities`،
استقلال Writer انسانی از DNS/config ثابت، adapter موقت fail-closed، عدم تغییر
schema/data/policy و جداسازی manifest عمومی از private پذیرفته شدند. این تأیید
مجوز implementation، config runtime، DNS یا writer handover نیست.

Rollback: callsiteها به adapter سازگار برمی‌گردند؛ schema/data product rollback یا
تغییر runtime تولید در این Stage وجود ندارد.

## `P1-03` — compose و service ownership هدف Finland

وضعیت: `PROPOSED`

Dependency: `P1-02`، `P4-02` و `P4-03`. این Stage ساخت artifact است، نه deploy.

### برداشت انسانی

روی ماشین جدید «یک برنامهٔ بزرگ» ساخته نمی‌شود. Web/API و Bot مستقل می‌مانند تا
restart، health و failure آن‌ها از هم جدا باشد. تنها data plane مشترک می‌شود و
owner هر job/credential دقیقاً یکی است.

### Runtime هدف

| service class | مسئولیت | قید ownership |
| --- | --- | --- |
| edge/nginx | TLS و route Web/API/dashboard | بدون Telegram/DB write مستقیم |
| web-api | رفتار Web و admin | process مستقل از Bot |
| telegram-bot/executor | update، callback و side effect تلگرام | تنها token/session owner |
| postgres | source of truth محلی Finland | یک cluster/authority |
| redis | queue/cache/coordination قراردادی | source of truth مالی نیست |
| workers/schedulers | jobهای product و Queue-v1 | یک owner برای هر job key |
| outbox transport | sync Finland↔Iran | هرگز loopback محلی |
| collectors/parser/estimator | Market pipeline مجاز | capability صریح |
| ops dashboard | مشاهده/عملیات مصوب | auth و audit اجباری |
| backup/log/metrics | durability و observability | بدون product mutation |

### Task Card فنی Cursor

1. Service Ownership Matrix را از `P1-00` به service، command، image، port،
   network، volume، secret، healthcheck، restart policy، resource budget و owner
   تبدیل کند.
2. تمام image/artifactهای release را content-addressed بسازد و roleهای backend را
   با command/profile صریح از source یکسان تعریف کند. تعداد artifactها از inventory
   واقعی می‌آید؛ یکی‌کردن اجباری imageهای مستقل مجاز نیست و tag شناور برای cutover
   ممنوع است.
3. compose target را با networkهای edge/app/data/ops حداقلی، volumeهای named و
   mountهای read-only در جای ممکن طراحی کند. DB/Redis نباید public bind شوند.
4. migration را به یک one-shot service با lock و schema compatibility check تبدیل
   کند. app فقط بعد از migration موفق ready و side-effect workerها بعد از app
   ready فعال شوند.
5. Web/API و Bot را به PostgreSQL/Redis مشترک وصل کند، اما command، healthcheck،
   lifecycle و restart domain آن‌ها را جدا نگه دارد.
6. Telegram credential را فقط برای executor مجاز mount کند. تمام serviceهای دیگر
   هم در compose و هم runtime guard باید بدون credential باشند.
7. برای هر cron/timer/scheduler/job یک `job_key`, owner service، cadence، lock،
   retry، idempotency و missed-run policy ثبت کند؛ startup با owner تکراری fail شود.
8. health را به liveness و readiness تفکیک کند. readiness واقعی باید DB، Redis،
   schema، queue و dependency لازم همان service را بسنجد؛ health نباید side effect
   خارجی ایجاد کند.
9. log/metric correlation را با `release_id`, `service`, `site_id`, `request_id`,
   `event_id` و بدون PII/secret استاندارد کند؛ disk quota/rotation نیز تعریف شود.
10. backup agent، restore command و media/object references را بدون دسترسی write
    تولیدی در compose staging قابل‌اجرا کند.
11. failure injection را برای stop/restart مستقل API، Bot، worker، Redis و DB اجرا
    کند و اثبات کند side effect ناقص یا duplicate owner ساخته نمی‌شود.
12. `docker compose config`، image digest، SBOM/scan طبق P4، port exposure، mount
    و ownership checks را در Evidence Index ثبت کند.

### خروجی، آزمون و Gate انسانی

- `09-target-runtime-topology.md`، Service Ownership Matrix و compose diagrams.
- compose validation و contract/integration test برای یک Telegram owner، یک job
  owner، migration ordering، restart isolation و fail-closed dependency.
- mutation Bot باید بدون HTTP sync در همان DB برای Web قابل‌مشاهده باشد، بدون
  loop یا side effect دوباره.
- مالک service/credential/job ownership و resource budget را پیش از `P1-04`
  تأیید کند. deploy روی `65.109.214.203` در این Stage ممنوع است.

Rollback: artifactهای compose/config به commit قبلی برمی‌گردند؛ volume یا دیتای
واقعی ایجاد/حذف نمی‌شود.

## `P1-04` — دادهٔ مشترک و حذف sync داخلی Finland

وضعیت: `PROPOSED`

Dependency: `P1-03`، `P2-00` و `P2-01`؛ Data Ownership Matrix باید مصوب باشد.

### برداشت انسانی

اکنون Web و Bot برای دیدن تغییر یکدیگر به انتقال شبکه‌ای نیاز ندارند. هر mutation
یک‌بار در DB مشترک انجام می‌شود و اگر لازم باشد به Iran برسد، یک outbox cross-site
در همان transaction ساخته می‌شود. منشأ Web/Bot پاک نمی‌شود، چون policyهای محصول
ممکن است بر اساس آن متفاوت باشند.

### Task Card فنی Cursor

1. تمام مسیرهای sync فعلی میان Bot-Finland و Web-Finland شامل HTTP client/server،
   `change_log` producer/consumer، listener، polling، retry، bulk update و repair
   script را به edgeهای graph تبدیل کند.
2. هر edge را با شاهد در یکی از چهار دسته قرار دهد:
   `REMOVE_LOCAL_TRANSPORT`, `CONVERT_TO_CROSS_SITE_OUTBOX`, `KEEP_LOCAL`,
   `BLOCKED_UNKNOWN`. دستهٔ آخر مانع implementation است.
3. برای هر mutation، transaction boundary و stable identity را مشخص کند. business
   row و outbox لازم باید atomic commit شوند؛ publish مستقیم پیش از commit ممنوع است.
4. مدل provenance را برای entityهای در scope تثبیت کند: حداقل `origin_site`,
   `origin_surface`, `actor_id/role`, `customer_tier_snapshot`, `policy_version`,
   `created_at` و identityهای Offer/Request/Trade. نام دقیق field پس از audit schema
   تعیین می‌شود و duplicate field بدون migration contract ساخته نمی‌شود.
5. `offer_source_surface` و `request_source_surface` را مستقل نگه دارد. درخواست از
   Bot روی Offer وب یا برعکس نباید provenance یکی را روی دیگری overwrite کند.
6. policy evaluation را از topology جدا و فقط از context نسخه‌دار مصوب تغذیه کند.
   رفتار tier 2، overtime، quota، confirmation، publication، notification و
   commission باید دقیقاً مطابق `04-surface-policy-matrix.md` بماند.
7. mappingهای legacy مانند `offer_home_server_for_source` را با تست characterize
   و سپس به site authority + surface policy تفکیک کند. حذف یک branch شرطی فقط وقتی
   مجاز است که parity test ثابت کند behavior از دست نمی‌رود.
8. outbox را با event id پایدار، aggregate/version، origin site/surface، payload
   schema version و idempotency key تعریف کند. local consumer نباید event Finland
   را دوباره به DB Finland loopback کند.
9. side-effect ledger تلگرام و notification را local و idempotent نگه دارد؛ replay
   cross-site نباید پیام، callback answer، publication یا notification تکراری بسازد.
10. ORM hook، raw SQL، bulk update و maintenance pathهایی را که outbox/ledger را
    دور می‌زنند با registry مقایسه و پوشش دهد یا با دلیل `LOCAL_ONLY` اعلام کند.
11. migration را expand/migrate/contract طراحی کند: schema افزایشی و backward-
    compatible، backfill resumable، validation، سپس حذف legacy فقط در closure.
12. contract tests را برای ماتریس surface/role/tier/time و نیز duplicate، retry،
    crash-before/after-commit، ordering و loop prevention اجرا کند.

### خروجی، آزمون و Gate انسانی

- Dataflow/ownership به‌روز، event registry، migration contract و parity mapping.
- CI باید receiver coverage، registry coverage و mutation emission را مقایسه کند.
- یک logical mutation دقیقاً یک business result و حداکثر یک event/side effect
  idempotent بسازد؛ visibility محلی نیازمند network hop نباشد.
- مالک هر تفاوت policy Web/Bot و تمام legacy mappingهای حذف‌شونده را تأیید کند.

Rollback: تا closure، schema و adapter قدیمی قابل‌خواندن می‌مانند. transport قدیمی
فقط در topology جداگانهٔ پیش از cutover قابل‌فعال‌شدن است؛ فعال‌کردن آن روی DB
مشترک بدون loop guard ممنوع است. دادهٔ commit‌شده در rollback حذف نمی‌شود.

## `P1-05` — rehearsal ادغام دادهٔ دو Finland

وضعیت: `PROPOSED`

Dependency: `P1-04`، `P2-05` و `P4-04`. مجوز production data فقط read-only backup
و طبق قرارداد امنیتی جداگانه است.

### برداشت انسانی

این مرحله dress rehearsal مهاجرت داده است. دو backup واقعی در محیط جدا restore
می‌شوند و merge دقیقاً مانند روز cutover اجرا می‌شود. تا زمانی که اجرای دوم همان
نتیجه، hash و conflict report را نسازد، مهاجرت آماده نیست.

### Task Card فنی Cursor

1. برای هر source، شناسهٔ host/database، snapshot time، WAL/binlog boundary، schema
   version، media root، backup command، encryption و checksum را در Merge Contract
   ثبت کند؛ value حساس در سند نیاید.
2. از هر backup یک restore مستقل روی محیط ایزوله انجام و با query read-only، schema،
   row count، FK و sample hash صحت restore را ثابت کند. «backup command موفق شد»
   به‌تنهایی کافی نیست.
3. table-by-table policy بنویسد: source/authority، stable identity، duplicate key،
   FK mapping، sequence strategy، merge rule، conflict rule، excluded field، media
   rule، verification query و rollback consequence.
4. users/account/relation/invitation و identityهای مشترک را پیش از dependentها حل
   کند. تطبیق بر اساس ID عددی تصادفی یا تشابه نام مجاز نیست.
5. Offer، Request و Trade را همراه origin surface، actor/role، tier snapshot،
   workflow، policy version و audit timestamps merge کند؛ target location نباید این
   metadata را بازسازی یا یکسان کند.
6. money، inventory، settlement، balance و trade conflict را فقط report/quarantine
   کند. auto-sum، overwrite و last-write-wins بدون rule مصوب ممنوع است.
7. media/upload/object reference را با hash، owner entity و existence check منتقل
   کند؛ orphan، collision و missing blob باید report جدا داشته باشد.
8. sequence/identity، FK، unique constraint، timezone/collation و extensionها را
   پس از load validate و sequenceها را بالاتر از max معتبر تنظیم کند.
9. Redis را از state دائمی جدا کند. فقط queue/session/cache state دارای قرارداد
   مصوب migrate شود؛ cache قابل‌بازسازی flush/rebuild می‌شود و source truth نیست.
10. migration/merge runner را resumable، checkpointed، deterministic و idempotent
    بسازد؛ kill/restart در میانه نباید duplicate یا state نیمه‌معتبر ایجاد کند.
11. پس از merge، row count، canonical business hash، balance/inventory invariant،
    orphan query، event/outbox count، media hash و نمونهٔ behavior contract را اجرا
    کند.
12. همان input را دو بار از صفر و یک‌بار به‌صورت resume اجرا کند؛ output hash و
    conflict report باید یکسان باشند.
13. app و Bot target را با side effect خارجی خاموش روی clone بالا بیاورد و full
    parity smoke را اجرا کند.
14. rollback rehearsal را با نابودکردن فقط target آزمایشی، restore مجدد source
    cloneها و بازاجرای runbook اثبات کند؛ backupهای اصلی دست‌نخورده بمانند.

### خروجی، آزمون و Gate انسانی

- `10-finland-data-merge-contract.md`، mapping جدول‌ها، conflict/quarantine report،
  restore receipt، run hashes و rollback receipt.
- هیچ conflict مالی/موجودی unresolved و هیچ missing media/FK پنهان باقی نماند.
- دو اجرای clean و یک resume نتیجهٔ business-equivalent بدهند.
- مالک conflict policy و report نهایی را تأیید کند؛ این Stage هیچ writer تولید را
  freeze، migrate یا تغییر نمی‌دهد.

Rollback: target rehearsal disposable است؛ source backupها immutable و تا پایان
cutover + retention محافظت می‌شوند.

## `P1-06` — staging یکپارچه Finland

وضعیت: `PROPOSED`

Dependency: `P1-05` و change set اتمیک `P4-07`.

### برداشت انسانی

staging باید نسخهٔ کوچک و واقعی معماری هدف باشد، نه mock ساده. یک corpus ثابت از
سناریوها هم روی معماری جاری و هم هدف اجرا می‌شود. پاسخ، state و side effectها پس
از حذف timestamp/IDهای غیرقطعی باید برابر باشند؛ «صفحه باز شد» معیار پذیرش نیست.

### Task Card فنی Cursor

1. exact image digest، config hash، migration version، dataset hash و test corpus
   را pin کند. staging نباید به Telegram، SMS، Arvan DNS یا Object Storage تولید
   side effect واقعی بزند.
2. دو محیط مقایسه بسازد: current-topology reference و target-single-Finland. داده
   production-shaped ولی sanitised و seed deterministic باشد.
3. corpus را برای Web/Bot/admin/internal، personaها، accountant، tier 1/2، Offer
   surface، Request surface، overtime/normal time، success/failure/retry و concurrent
   action تولید کند.
4. auth/login/registration/OTP/session، invitation/relation، Offer/Request/Trade،
   commission/settlement، expiry/republish/overtime و Market guard را اجرا کند.
5. Messenger text/media/upload/download/realtime/unread، Queue-v1، Telegram command/
   callback/publication و notification audience را با fake/sandbox adapter آزمون کند.
6. خروجی دو محیط را normalize و API status/schema/copy، Bot result، DB business
   hash، outbox/event، queue state، notification audience، ordering، retry و
   side-effect ledger را مقایسه کند.
7. مرورگرهای تعریف‌شده در acceptance matrix، mobile/desktop viewport و reconnect
   realtime را اجرا کند؛ screenshot به‌تنهایی جای assertion را نمی‌گیرد.
8. restart مستقل Web/API، Bot و worker و نیز failure/recovery Redis، PostgreSQL و
   Object Storage را تزریق کند. duplicate message، lost mutation و false-ready
   نباید رخ دهد.
9. backup/restore و application rollback را با exact artifact اجرا کند و پس از آن
   همان corpus و business hash را دوباره بسنجد.
10. load profile مبتنی بر baseline `P1-00` را اجرا و latency/error/queue lag، CPU،
    RAM، disk I/O، connection pool و storage growth را ثبت کند. budget قبل از test
    در Task Card عددگذاری و توسط مالک تأیید شود.
11. حداقل soak پیشنهادی ۲۴ ساعت را با jobها، log rotation، backup و queue فعال
    اجرا کند؛ مدت نهایی باید در Gate انسانی ثبت شود و قابل حدس‌زدن نیست.
12. هر diff را به `EXPECTED_TOPOLOGY`, `KNOWN_BASELINE_BUG`, `REGRESSION` یا
    `NONDETERMINISTIC_TEST` طبقه‌بندی کند. فقط اولی بدون تصمیم رفتار قابل‌قبول است؛
    سه مورد دیگر resolution یا waiver صریح می‌خواهند.

### خروجی، آزمون و Gate انسانی

- `11-staging-acceptance.md`، corpus/version، differential report، browser matrix،
  chaos/restart، performance، soak، backup/restore و rollback receipts.
- تمام `behavior_id`ها test passing و coverage صددرصد یا blocker مصوب داشته باشند.
- Telegram identity collision، double job owner، unexplained behavior diff و data
  invariant failure باید صفر باشد.
- مالک budget، soak duration، waiverهای احتمالی و آمادگی cutover را امضا کند.

Rollback: staging به artifact قبلی بازمی‌گردد یا rebuild می‌شود؛ هیچ dependency
تولید برای سبزکردن تست تغییر نمی‌کند.

## `P1-07` — cutover کنترل‌شده به Finland Primary

وضعیت: `PROPOSED — نیازمند مجوز تولید جدا`

Dependency: `P1-06`، `P4-07`، `P4-10`، change set اتمیک `P4-08` و مجوز صریح
تولید. تأیید سند به معنی این مجوز نیست.

### برداشت انسانی

در یک maintenance window، writeهای دو Finland قدیمی متوقف، آخرین delta ادغام و
target ابتدا در حالت dark بررسی می‌شود. Bot قدیمی باید پیش از دسترسی Bot جدید به
token/session به‌طور قطعی متوقف شود. در هر checkpoint عامل انسانی یا ادامه می‌دهد
یا rollback می‌کند؛ failover خودکار در این Stage وجود ندارد.

### Task Card فنی Cursor

#### آماده‌سازی پیش از پنجره

1. change ticket شامل owner عملیات، فرمان‌دهنده، observer، زمان، contact، نقاط
   go/no-go و مجوزهای DB/DNS/runtime را ثبت کند.
2. exact commit/image digest، SBOM/scan receipt، config hash، migration bundle،
   merge runner، backup/restore و rollback artifact مصوب را freeze کند.
3. target capacity، TLS/edge، DB/Redis volume، secret mount، monitoring، alert،
   clock/NTP و disk headroom را read-only verify کند.
4. backup نهایی هر دو source و media را با encryption/checksum بگیرد و حداقل یک
   restore off-source تأییدشده داشته باشد. backup فاقد restore receipt مانع cutover است.
5. DNS/route فعلی، TTL، old endpoint، target dark endpoint و روش rollback را ثبت
   کند؛ تغییر واقعی فقط در checkpoint مربوط و با مجوز انجام می‌شود.
6. baseline smoke، queue depth، sync lag، active session/job و business hash را
   بگیرد و mutationهایی را که باید quiesce شوند فهرست کند.

#### اجرای پنجره با checkpoint انسانی

7. Web Writer و Bot/worker mutation روی دو source را با ترتیب runbook وارد drain
   کند؛ requestهای جاری باید تمام و mutation تازه رد/صف شوند. صرفاً stop process
   بدون اثبات quiescence کافی نیست.
8. `QUIESCED` receipt شامل آخرین transaction/event/queue checkpoint بگیرد. اگر
   writer یا scheduler ناشناخته فعال بود، عملیات متوقف و rollback شود.
9. final delta و media delta را capture، merge runner را روی target اجرا و report،
   conflict، FK، sequence، business hash و invariant را مقایسه کند. conflict تازه
   go/no-go انسانی می‌خواهد.
10. migration را یک‌بار اعمال و verification/idempotency command را دوباره اجرا
    کند؛ اجرای دوم نباید schema/data change تازه بسازد.
11. PostgreSQL/Redis لازم، API و workerهای بدون side effect را dark بالا بیاورد؛
    readiness، admin smoke، data hash و internal route بررسی شوند و هنوز traffic
    یا Telegram credential فعال نشود.
12. Bot قدیمی و تمام Telegram executor/publisherهای قدیمی را stop و terminal
    ownership receipt بگیرد. سپس و فقط سپس credential روی Bot target mount و
    identity readback/owner guard اجرا شود.
13. Bot و job ownerهای target را با exact release فعال کند؛ duplicate owner guard،
    queue state و side-effect ledger بررسی شوند. پیام تست تولید فقط اگر change
    ticket صریحاً اجازه دهد ارسال می‌شود.
14. edge/DNS را طبق بخش deploy به target تغییر دهد و از resolver/probeهای مصوب
    مقصد IP/TLS/health را اثبات کند. موفقیت API provider به‌تنهایی کافی نیست.
15. smoke matrix بحرانی Web و Bot، login، Offer/Request/Trade، realtime، Queue،
    Market و backup را اجرا و response/state/side effect را با baseline مقایسه کند.
16. در observation window، error rate، latency، DB/Redis، queue، Telegram، jobs،
    disk، backup و business invariant را پایش کند. پایان window نیازمند رسید انسان است.
17. sourceهای قدیمی را خاموش/حذف نکند؛ آن‌ها را network-fenced و read-only در
    quarantine نگه دارد و از accidental writer شدن alert بسازد.

### مرز rollback

- **پیش از اولین mutation target:** credential/traffic target بسته، target متوقف،
  sourceها از همان checkpoint باز، route/DNS برگردانده و smoke ثبت می‌شود.
- **پس از اولین mutation target:** rollback برنامه روی همان DB target انجام می‌شود.
  بازگرداندن source DB قدیمی فقط با runbook reverse-migration/reconciliation تازه
  و مجوز مالک ممکن است؛ روشن‌کردن مستقیم آن خطر دو history دارد و ممنوع است.
- triggerهای rollback باید پیشاپیش عددی/مشاهده‌پذیر باشند: invariant failure،
  duplicate Telegram owner، migration/hash mismatch، critical parity regression،
  DB durability failure یا error budget breach.

### خروجی و Gate انسانی

- `12-cutover-runbook.md` تکمیل‌شده، timeline، تمام checkpoint receiptها، digestها،
  backup/restore، ownership، DNS/route، smoke و observation evidence.
- target تنها Web Writer Finland و تنها Telegram owner باشد؛ old sourceها fenced.
- Feature Parity Contract کامل باشد و topology تنها تفاوت عمدی ثبت‌شده بماند.
- هیچ decommission، credential revoke دائمی یا حذف backup در این Stage انجام نشود.

## `P1-08` — closure و حذف بدهی توپولوژی قدیمی

وضعیت: `PROPOSED`

Dependency: `P1-07`، `P2-11` و تأیید retention/backup/decommission. این dependency
عمداً باعث می‌شود منابع قدیمی پیش از عملیاتی‌شدن Iran Standby حذف نشوند.

### برداشت انسانی

«چند روز بدون خطا» به‌تنهایی مجوز حذف نیست. ابتدا باید ثابت شود هیچ code، DNS،
job، backup، monitoring، rollback یا runbookی به توپولوژی قدیمی وابسته نیست. سپس
منابع در چند gate جدا بازنشسته می‌شوند تا راه بازگشت زودتر از موعد از بین نرود.

### Task Card فنی Cursor

1. مدت quarantine/observation مصوب را از receipt `P1-07` کنترل و incident، alert،
   parity diff، backup و restoreهای این بازه را خلاصه کند.
2. repo، generated config، CI/CD، secret store nameها، DNS/edge، monitoring، backup،
   firewall، cron/systemd، documentation و operator workstation را برای reference
   به host/IP/path/role قدیمی اسکن کند.
3. هر reference را `ACTIVE`, `ROLLBACK_ONLY`, `HISTORICAL_DOC`, `STALE` یا
   `UNKNOWN` طبقه‌بندی کند. وجود `ACTIVE` یا `UNKNOWN` مانع decommission است.
4. telemetry اثبات کند sourceهای قدیمی هیچ write، Telegram call، scheduler run،
   inbound product traffic یا sync production ندارند و target backup/restore سالم است.
5. adapter `server_mode`، local-sync transport، env key، compose profile، script،
   test fixture و docs قدیمی را در commitهای کوچک حذف کند؛ قبل و بعد هر دسته
   hardcode/reference scan و full parity suite اجرا شود.
6. historical docs لازم را با banner منسوخ و لینک ADR نگه دارد؛ runbook قابل‌اجرا
   نباید به command یا host بازنشسته اشاره کند.
7. credential و access قدیمی را فقط پس از backup و audit با manifest revoke کند؛
   rollback credential تا پایان protected window حذف نمی‌شود.
8. volume، snapshot، backup، release و server را بر اساس retention manifest و با
   approval مستقل decommission کند. هر حذف material باید target دقیق و recovery
   status داشته باشد.
9. monitoring، alert routing، inventory/CMDB، billing، DNS record، firewall allowlist
   و backup schedule orphan را پاک یا منتقل و نتیجه را verify کند.
10. final architecture، cost/capacity، restore receipt، data hash و owner matrix را
    ثبت و Stage Ledger را به `COMPLETE` ببرد.
11. `candidate/wa-ir-standby-v1` در این Stage خودکار حذف نمی‌شود. حذف آن فقط پس از
    پایان استخراج معماری/مستندات و دستور صریح مالک، ترجیحاً در closure کل پلن است.

### خروجی، آزمون و Gate انسانی

- `13-closure-and-decommission.md`، no-reference report، deprecation ledger،
  credential/resource deletion manifest و final topology receipt.
- hardcode/reference scan، full parity suite، backup restore و disaster recovery
  smoke پس از cleanup سبز باشند.
- decommission هر host/volume/backup/credential نیازمند تأیید جداگانهٔ مالک است.
- پس از closure، repository و runbookها فقط معماری فعال و history صریحاً منسوخ را
  نشان دهند؛ هیچ billing/monitoring/backup orphan باقی نماند.

Rollback: پیش از حذف نهایی، sourceها fenced و recoverable می‌مانند. پس از حذف،
rollback فقط از backup off-host دارای restore receipt است؛ اگر چنین backupی نیست،
decommission مجاز نیست.

### Definition of Done بخش ۱

بخش ادغام فقط زمانی بسته است که همهٔ موارد زیر هم‌زمان برقرار باشند:

1. تمام `P1-00..P1-08` با dependency، evidence و gate انسانی `COMPLETE` باشند.
2. Web/API و Bot روی Finland Primary جدا اجرا شوند و PostgreSQL/Redis مشترک داشته
   باشند؛ restart و failure domain آن‌ها مستقل باشد.
3. تنها یک Telegram executor و تنها یک owner برای هر scheduler/job وجود داشته باشد.
4. هیچ sync شبکه‌ای برای visibility محلی Bot↔Web باقی نماند؛ outbox cross-site
   Finland↔Iran سالم و loop-free باشد.
5. منشأ مستقل Offer و Request و context نقش/tier/version حفظ و تمام تفاوت‌های
   Web/Bot در Surface Policy Matrix و تست‌های regression پوشش داده شده باشد.
6. دو rehearsal deterministic، staging differential، backup/restore و rollback
   واقعی سبز باشند و هیچ conflict مالی/موجودی یا behavior diff بی‌تصمیم نماند.
7. deploy/cutover با exact artifact و receipt انجام شده و old writerها fenced باشند.
8. repository، artifactها، retention، docs و topology قدیمی طبق manifest بسته شده
   باشند؛ حذف irreversible بدون backup restore-tested انجام نشده باشد.

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
- Web/Bot offer/trade از دیتابیس محصول materialize می‌شوند؛ surface هم provenance
  و هم policy input نسخه‌دار است، اما وزن estimator فقط طبق policy علمی مصوب تغییر
  می‌کند و یک logical event دوبار شمرده نمی‌شود.
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
