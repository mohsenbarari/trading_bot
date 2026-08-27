# رسید عملیاتی Stage 13-A — استقرار Staging Shadow

تاریخ اجرا: 2026-08-26

Release: `main@7047ef005ce64c0266d7b55a7593ea977d65bfb1`

وضعیت نهایی: `STAGING_SHADOW_LIVE / HOLD_PRIVATE_PRIMARY`

## نتیجه

pipeline خصوصی Market Data روی هر دو میزبان staging مستقر و به‌صورت زنده پایش شد. دریافت
Telegram و API، parse و archive روی میزبان وب/داده انجام می‌شود؛ facts روی شبکه خصوصی به
میزبان بات می‌رسند؛ estimator snapshot نسخه‌بندی‌شده را برمی‌گرداند. تمام اجزا هنوز در
`PRIVATE_SHADOW` هستند و هیچ feed محصول، WebApp authority یا production cutover تغییر
نکرده است.

اختیار زنده دو Telegram session با توقف ownerهای host-native به captureهای کانتینری تحویل
شد. sessionهای اصلی legacy برای rollback دست‌نخورده باقی ماندند و هرگز دو owner هم‌زمان
به یک session متصل نبودند.

## تمامیت release و استقرار

- release archive SHA-256:
  `8895c859037412dd0bf861ce009a88ab41c7bb61f8990db57ca1b1f3e65699a6`
- image tar SHA-256:
  `3e8ed9a341703d53156f6cefd0b941e4effc8b30336a9369d6dc15b72254fa70`
- image میزبان بات:
  `sha256:ab14c981b24a31ae4e1512c23e1ea6f658abcb2b7dcac511fdac5d5b5c08e891`
- image میزبان وب/داده پس از import:
  `sha256:d9d2a8e314338fed262da6f86448c18140787ceedcde3da60e755a7305f48482`
- هر دو image دارای RootFS layerهای برابر، revision برابر release و UID runtime برابر
  `10001` هستند؛ تفاوت image ID نتیجه نرمال‌سازی تاریخچه Docker هنگام انتقال است.
- release در هر دو میزبان در مسیر commit-bound
  `/srv/trading-bot/market-pipeline-releases/7047ef005ce64c0266d7b55a7593ea977d65bfb1`
  قرار دارد.
- داده staging در rootهای مستقل
  `/srv/trading-bot/market-data-staging-shadow` روی وب/داده و
  `/srv/trading-bot/staging-data/coin-intelligence/private-pipeline-shadow` روی بات قرار
  دارد؛ مسیرهای production دست‌نخورده‌اند.

## inventory زنده

روی میزبان وب/داده هفت service دائمی سالم هستند: PostgreSQL 15، دو capture تلگرام، capture
external، processor، Market Fact sync worker و snapshot receiver. migration همان release
با exit code صفر پایان یافت. روی میزبان بات چهار service receiver، adapter، estimator و
snapshot sender سالم هستند.

فقط دو port دریافت روی IP خصوصی bind شده‌اند:

- Market Fact receiver روی `10.240.1.10:9443`؛
- Estimator Snapshot receiver روی `10.240.1.20:9443`.

هر دو جهت mTLS/HMAC هستند و public fallback ندارند.

## تحویل اختیار Telegram

- receipt با contract برابر `market_capture_authority_handoff_receipt/1.0` و زمان
  `2026-08-26T14:29:16Z` در backup-root ایزوله staging ثبت شد؛
- `market-channel-capture.service` و `coin-capture.service` همچنان enabled ولی
  `inactive/dead`، با PID صفر و sessionهای اصلی سالم‌اند؛
- timer قدیمی `coin-intelligence-capture-shadow-input.timer` همچنان enabled/active است و
  unitهای قدیمی را به‌عنوان dependency می‌خواهد؛
- دو drop-in فقط-runtime در `/run/systemd/system` با `RefuseManualStart=yes` و یک
  `ConditionPathExists` عمداً ناموجود، شروع dependency/manual ownerهای قدیمی را مسدود
  می‌کنند؛
- markerهای `authority-container.json` و lockهای owner با mode `0600` و UID/GID
  کانتینر در session-root staging قرار دارند.

این guard موقت است و پس از reboot از بین می‌رود. رفتار fail-safe مورد انتظار پس از reboot
بازگشت ownerهای enabled میزبان است؛ بنابراین پیش از هر reboot برنامه‌ریزی‌شده باید authority
دوباره به‌صورت صریح تعیین و single-owner postcheck شود.

## شواهد soak و انتقال

در window اولیه ۳۰ ثانیه‌ای:

- Account1 از sequence `6476` به `6536` و Account2 از `1705` به `1706` رسید؛
- outbox هر دو capture صفر بود؛
- worker از `2190` به `2252` ACK رسید، queue depth صفر، duplicate/rejected صفر و p95
  حدود `123ms` بود.

در soak پنج‌نمونه‌ای بعدی، ownerهای legacy در تمام نمونه‌ها inactive، هر دو capture سالم و
restart count صفر ماندند. نمونه پایانی sequenceهای `7619` و `1719`، outbox صفر، queue
صفر، rejected/duplicate صفر و p95 حدود `104ms` داشت. postcheck بعدی نیز sequenceهای
`8024` و `1724` و `3868` ACK تجمعی را ثبت کرد.

snapshot نسخه `732` با status برابر `OK`، هشت نرخ، ۱۹ health component و ۱۹ input component
به وب رسید. SHA-256 نمایش canonical payload در هر دو میزبان دقیقاً برابر بود:
`2e035d75ec11d1e3d8012ec34e7ef2bd1e6ce3b2d84b428bc8d1dad1452e8dab`.
Web envelope آن را `FRESH` ثبت کرد. تفاوت hash فایل‌های کامل طبیعی است، چون فایل وب envelope
شامل metadata دریافت/انتشار است.

ledger کالیبراسیون در postcheck دارای `quick_check=ok`، تعداد `14478` prediction تا
`2026-08-26T14:43:59Z` و ۴۵ feedback تاییدشده بود. هیچ raw text یا Telegram identity به
این ledger منتقل نمی‌شود.

## ممیزی قابلیت parity زنده

یک window فقط‌خواندنی `14:30Z` تا `14:50Z` بین Market Store قدیمی و private shadow
مقایسه شد. این ممیزی فقط count و اشتراک event key را خواند و هیچ متن/هویتی خارج نکرد:

- legacy دارای ۹۲۳ و private shadow دارای ۲۸۹۲ fact یکتا بود؛
- برای `MELTED_AGGREGATE` تعداد ۱۳۷، `MELTED_FLOW` تعداد ۱۸۲ و `USD_HERAT` تعداد ۱۱
  event key مشترک وجود داشت؛
- legacy برای `PRIVATE_GOLD_CHANNEL` در این window رکوردی نداشت، در حالی که private
  shadow تعداد ۴۲۴ fact داشت. آخرین رکورد legacy این منبع متعلق به
  `2026-08-25T09:30:12Z` بود؛
- cadence منابع API یکسان نبود؛ برای نمونه XAU در همان window برابر ۲۲ در legacy و ۱۵۶۵
  در private shadow بود. این منابع باید بر اساس consumed value در timestamp هم‌تراز مقایسه
  شوند، نه count رویداد؛
- event keyهای گروه‌ها بین دو lane مشترک نبودند و حجم داده نیز هم‌ارز نبود. lane قدیمی فقط
  پنج fact گروه و private shadow تعداد ۴۹ fact گروه داشت.

در نتیجه اجرای مستقیم Stage 12 comparator روی این دو ورودی، outage/cadence/identity قدیمی
را به‌اشتباه به‌عنوان capture یا parser drift گزارش می‌کند و evidence معتبر promotion
نمی‌سازد. ادامه‌ی امن gate باید از یک Telegram capture owner استفاده کند و همان eventهای
immutable را بعد از capture به دو projection ایزوله و version-pinned fan-out کند. completeness
capture نیز با manifest و reconciliation مستقل سنجیده می‌شود؛ برای ساخت lane دوم هرگز session
تلگرام دوم یا owner هم‌زمان ایجاد نمی‌شود.

## هارنس parity تک‌مالک

برای ادامه ممیزی بدون ساخت Telegram owner دوم، هارنس
`scripts/rehearse_market_single_owner_parity_stage13.py` اضافه شد. قرارداد آن
`market_single_owner_parity/1.0` و mode آن `SINGLE_OWNER_FROZEN_REPLAY` است. این هارنس:

1. lock موجود writer قدیمی را بدون ساخت یا جایگزینی lock می‌گیرد و از Market Store و
   staging DB با SQLite online backup یک seed سازگار می‌سازد؛ capture زنده جدید در این مدت
   متوقف نمی‌شود؛
2. اندازه، inode و device هر فایل spool را هنگام بازکردن ثبت و دقیقاً همان prefix را در
   scratch با mode محافظت‌شده freeze می‌کند؛ append بعدی وارد این اجرا نمی‌شود و partial
   tail به اجرای بعد موکول می‌شود؛
3. هر رکورد کامل خراب، contract نامعتبر، truncation، قفل اشغال، خطای backup/ingest/snapshot
   یا پاک‌نشدن scratch را fail-closed می‌کند و artifact ناقص باقی نمی‌گذارد؛
4. پس از اعتبارسنجی تمام رکوردهای کامل prefix، فقط رکوردهایی را که receipt time آن‌ها دقیقاً
   داخل window است در spool مشتق‌شده و موقت می‌گذارد؛ همان subset و یک `now/as_of` ثابت
   برابر `window_end` با دو کپی یکسان seed و Python code rootهای baseline/candidate
   version-pinned در laneهای کاملاً جدا replay می‌شود؛ هیچ network call، session تلگرام،
   product feed یا runtime state مشترک ساخته نمی‌شود؛
5. final Market Storeهای دو lane را روی event keyهای HMACشده از نظر missing/added، unit،
   lifecycle و semantics parser مقایسه و count اختلاف را با source/instrument داخلی تفکیک
   می‌کند؛ snapshot/rate نیز در یک timestamp هم‌تراز ساخته می‌شود. برای XAU/USDT تفاوت
   value مشترک و مصرف‌شده severity-1 است، ولی تفاوت صرفاً metadata/cadence یا اضافه‌شدن
   فیلد value جدید به schema به‌اشتباه value mismatch نام‌گذاری نمی‌شود؛
6. فقط `report.json` امضاشده و `capture-manifest.json` privacy-minimized را با mode `0600`
   در artifact directory با mode `0700` حفظ می‌کند. متن خام، شناسه پیام، sender، event ID
   مستقیم، قیمت/تعداد اختلاف و database/spool موقت در خروجی ماندگار نمی‌ماند؛
7. کلید identity و signing را فقط از فایل محافظت‌شده با mode `0400`، `0440`، `0600` یا
   `0640` و بدون هیچ world permission می‌خواند؛ انتقال secret در argv ممنوع است. کلید
   signing مستقل است و با کلید transport reuse نمی‌شود.

حذف scratch به معنی حذف مسیرهای filesystem پس از اجراست و ادعای forensic erase روی
SSD ندارد؛ به همین دلیل scratch باید فقط روی storage کنترل‌شده میزبان قرار گیرد و هیچ backup
یا sync خودکاری آن را پوشش ندهد.

پنجره اجرای این rehearsal حداکثر ۲۵ دقیقه اخیر است و زمان منطقی ingester در هر دو process
ثابت است؛ بنابراین مرز backlog سی‌دقیقه‌ای با طول اجرای lane جابه‌جا نمی‌شود. همه factهای
نهایی دو clone مقایسه می‌شوند، نه فقط factهایی که `available_at` آن‌ها داخل پنجره است؛ در
نتیجه edit/delete یک پیام قدیمی نیز از مقایسه حذف نمی‌شود. زمان snapshot نیز دقیقاً همان
`window_end` مشترک است.

نمونه اجرا، بدون درج مقدار secret:

```bash
python3 scripts/rehearse_market_single_owner_parity_stage13.py run \
  --baseline-code-root /srv/coin-intelligence-shadow/app \
  --candidate-code-root /srv/trading-bot/market-pipeline-releases/COMMIT_SHA \
  --baseline-market-store /srv/coin-intelligence-shadow/runtime/live/market/market.sqlite3 \
  --baseline-staging-store /srv/coin-intelligence-shadow/runtime/live/staging/capture.sqlite3 \
  --baseline-writer-lock /srv/coin-intelligence-shadow/runtime/live/run/market-writer.lock \
  --market-spool-dir /srv/trading-bot/market-data-staging-shadow/capture/account1 \
  --coin-spool-dir /srv/trading-bot/market-data-staging-shadow/capture/account2 \
  --scratch-root /srv/trading-bot/market-data-staging-shadow/tmp \
  --artifact-dir /srv/trading-bot/market-data-staging-shadow/backups-staging/stage13-single-owner-parity-COMMIT_SHA \
  --identity-key-file ROOT_ONLY_HMAC_KEY_FILE \
  --signing-key-file ROOT_ONLY_SIGNING_KEY_FILE \
  --signing-key-id stage13-staging:COMMIT_SHA \
  --window-start UTC_START --window-end UTC_END \
  --acknowledge-no-cutover --confirm-sensitive-ephemeral-copy
```

این evidence عمداً `snapshot_timeline_complete=false`، `full_market_session=false` و
`promotion_recommendation=HOLD_STAGE12_LIVE_PARITY_REQUIRED` ثبت می‌کند. حتی در صورت صفر
بودن تمام اختلاف‌ها، به‌تنهایی مجوز `PRIVATE_PRIMARY` نیست؛ timeline واقعی، جلسه کامل بازار
باز و gate زنده Stage 12 همچنان لازم است.

## نتیجه rehearsal تک‌مالک

نسخه نهایی هارنس با release `main@50aea41de5db9bb03482756f8b7c601c32824470`
و archive SHA-256 برابر
`e2ec9f6eed1da5cbdfbb17c0d94bdd699116513d300472919b10739e9160b4a6` داخل image
فعال Stage 13 با network خاموش اجرا شد. image فقط dependency runtime را فراهم کرد؛ code
baseline و candidate هر دو از mountهای version-pinned خوانده شدند و هیچ service/image زنده
تعویض نشد. ۱۷ آزمون Stage 12/13 داخل همان image سبز بود.

گزارش نهایی window برابر `2026-08-26T18:45:11Z` تا `19:05:11Z` با report hash
`2f1cb107efdfe336cb6e0dca1d7d4c7cd08996fbbd53dbac01c220b18082903c` و key ID برابر
`stage13-staging:50aea41d` امضا و سپس مستقل verify شد. نتیجه redacted:

- ۴۸٬۵۱۵ رکورد کامل prefix اعتبارسنجی و دقیقاً ۱٬۲۱۶ event داخل window replay شد؛
  duplicate، partial tail، rejected و stale-skipped صفر بود و هر دو lane دقیقاً
  `records=accepted=1216` ثبت کردند؛
- manifest شامل ۱٬۲۱۵ event واقعی XAU و یک event `MELTED_AGGREGATE` بود. candidate دقیقاً
  ۱٬۲۱۵ XAU fact ساخت، درحالی‌که baseline قدیمی ۲۳ public fact نوشت. final Store دارای
  ۱٬۲۱۲ XAU fact اضافه candidate و ۲۰ XAU bucket/key قدیمیِ فقط baseline بود؛
- unit/parser/lifecycle mismatch در این window صفر بود. اختلاف XAU ناشی از سیاست مصوب
  حفظ هر quote واقعی و ممنوعیت minute compaction است؛ baseline قدیمی برای XAU oracle
  event-by-event معتبر نیست؛
- از ۱۹ signal، ۱۷ مورد schema جدید `mean_price` را داشتند. value مشترک USDT برابر بود؛
  فقط XAU به‌علت sample set کامل‌تر candidate value متفاوت داشت؛
- هر ۱۴ خروجی rate دقیقاً برابر و `rate_mismatch_count=0` بود؛
- severity-1 برابر ۱ (فقط XAU consumed value) و severity-2 برابر ۱٬۲۴۸ بود. این اختلاف‌ها
  حذف یا auto-accept نشدند و recommendation همان `HOLD_STAGE12_LIVE_PARITY_REQUIRED`
  ماند؛
- artifact directory با mode `0700` و دو فایل با mode `0600` باقی ماند. کلیدهای حساس،
  متن/شناسه پیام، sender، قیمت/تعداد اختلاف و رشته non-ASCII در artifact نبود؛ scratch پاک
  شد و هر هفت service زنده پس از اجرا healthy ماندند.

اجرای ابتدایی `951ca9f0` نشان داد `now-30m` دو process می‌تواند با طول replay جابه‌جا شود؛
آن evidence برای تصمیم parser `SUPERSEDED_TIMING_CONFOUND` است و فقط برای audit نگه داشته
شد. هارنس از `c751f582` به بعد subset دقیق window و `now/as_of=window_end` مشترک را اعمال
می‌کند. اجراهای میانی به تفکیک value/metadata/schema منجر شدند و گزارش بالا تنها مرجع نهایی
این rehearsal است.

این window پس از آرام‌شدن بازار فقط channel/XAU داشت و gate کامل گروه‌های سکه، کانال خصوصی،
هرات و session کامل بازار باز را نمی‌بندد. مرحله بعد باید valueهای XAU candidate را در
timestamp مشترک با ورودی واقعی مدل اصلی مقایسه و سپس یک session کامل بازار باز با snapshot
timeline واقعی ثبت کند؛ مقایسه دوباره با XAU دقیقه‌ای baseline معیار پذیرش نیست.

## بازیابی backlog و snapshot علّی

در ادامهٔ soak، receiver و sender سالم بودند ولی snapshot بات متوقف شد. adapter هنگام backlog
تمام `fact_deliveries` را با `ORDER BY` و `fetchall()` می‌خواند و مرتب‌سازی payloadهای کامل در
tmpfs محدود کانتینر به `database or disk is full` می‌رسید. اصلاح `main@fd665759` خواندن را به
cursor مستقل هر stream و merge سراسری bounded تبدیل کرد؛ در هر cycle حداکثر ۵۰۰ delivery
انتخاب می‌شود و ترتیب علّی داخل هر stream محفوظ است. preflight واقعی با tmpfs هشت‌مگابایتی
۵۰۰ delivery را بدون spill پردازش کرد و adapter سپس هر ۱۰ stream را به lag صفر رساند.

اولین اجرای timeline پس از بازیابی، `timeline_trace_time_order_invalid` را آشکار کرد: زمان
ارزیابی پیش از اولین SELECT تعیین و به ثانیه گرد می‌شد، بنابراین factی که بین آن لحظه و pin شدن
read snapshot commit شده بود می‌توانست `transferred_at` بعد از `generated_at` داشته باشد.
اصلاح `main@d2b79298` این مرز را در همهٔ خوانش‌های point-in-time روی event time، availability و
زمان ورود محلی اعمال می‌کند، read snapshot را پیش از انتخاب زمان زنده pin می‌کند، زمان تولید را
با دقت زیرثانیه نگه می‌دارد و contract هر ورودی منتقل‌شده پس از زمان تولید را رد می‌کند. محاسبهٔ
anchor تاریخی نیز window اقتصادی قدیمی را از مرز دانسته‌های زمان ارزیابی جدا نگه می‌دارد.

چهار service بات به image
`sha256:05d66759d14e47b19a41959c42d90ba50599b807d049ccb5f19346b070f4fff7` با revision کامل
`d2b79298d2e265a57a3fe5a4fac8ebcdcd9315a7` به‌ترتیب recreate شدند. هر چهار service healthy،
restart count صفر و feed همچنان `PRIVATE_SHADOW` بود. در postcheck، ۵۱٬۲۸۹ delivery روی ۱۰
stream با duplicate/rejection و lag صفر اعمال شده بود؛ snapshot جدید ترتیب علّی داشت و envelope
وب آن را `FRESH` دریافت کرد. هیچ service وب، product authority یا primary feed تغییر نکرد.

timeline واقعی ده‌نمونه‌ای پس از این اصلاح با report hash
`cdf73b4b297c7e51e16bd28bb794afa6bd1fe209072c3f38546275171a894883` و key ID برابر
`stage13-consumed-input:d2b79298` امضا و مستقل verify شد. artifact با mode `0600` در
`/srv/trading-bot/staging-data/coin-intelligence/private-pipeline-shadow/backups-staging/stage13-consumed-input-d2b79298.json`
قرار دارد و redaction و `cutover_performed=false` نیز مستقل تایید شدند. نتیجه:

- ۱۰ snapshot تازهٔ product با ۵۵ snapshot متوالی candidate مقایسه شد؛ version gap صفر و
  `snapshot_timeline_complete=true` بود؛
- p95 اختلاف زمانی جفت snapshotها `4.114s` و p95 انتقال ورودی جدید تا snapshot بعدی
  `6.788s` بود که gate هفت‌ثانیه‌ای را در این window پاس می‌کند؛
- point و mean اونس و تتر در همهٔ نمونه‌ها حداکثر ۲۵ bps فاصله داشتند؛ هرات فردایی و سه
  aggregate آبشده در هر ۱۰ نمونه دقیقاً برابر بودند؛
- private-gold امروز در product غایب و در candidate حاضر بود و private-gold فردایی و
  `MELTED_PAPER_TODAY` بیش از ۱۰۰ bps اختلاف داشتند. scheduled و exact-as-of تقریباً همان
  طبقه‌بندی را دادند، پس skew چهارثانیه‌ای علت اصلی این drift نیست؛
- بازار آرام بود و هر دو مسیر برای تمام ۱۴ نرخ coin فاقد خروجی بودند؛ بنابراین این window
  هیچ evidence پذیرشی برای rate parity یا parser/lifecycle بازار باز نمی‌سازد؛
- recommendation نهایی همان `HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED` ماند و هیچ cutover
  انجام نشد.

## پنجره فعال بازار 2026-08-27

پس از بازشدن آبشده، هرات، اونس و تتر، یک timeline تازه از
`2026-08-27T05:34Z` تا `06:07Z` اجرا شد. گزارش با hash
`2fca275f83d9dbe0e1ce1b7fe699a5493ababfda4153f50b062900788e044828` و key ID برابر
`stage13-open-market:a79183d3` امضا و مستقل verify شد. artifact redacted با mode `0600` در
`/srv/trading-bot/staging-data/coin-intelligence/private-pipeline-shadow/backups-staging/stage13-open-market-input-20260827T0534Z-a79183d3.json`
قرار دارد. نتیجه:

- ۶۰ snapshot تازهٔ محصول با ۳۱۴ snapshot candidate مقایسه شد؛ version gap صفر و
  `snapshot_timeline_complete=true` بود؛
- p95 فاصلهٔ زمانی جفت snapshotها `5.707s` و p95 انتقال source تازه تا snapshot بعدی
  `6.869s` بود؛ بنابراین transport/adapter در این window گیت هفت‌ثانیه‌ای را پاس کرد؛
- XAU در تمام ۶۰ نمونه حداکثر ۲۵ bps و USDT حداکثر ۵ bps فاصله داشت. هرات امروز حداکثر
  ۲۵ bps بود؛ Herat cash و فردایی چند نمونه تا ۱۰۰ bps داشتند ولی هیچ نمونه‌ای بیش از
  ۱۰۰ bps نبود؛
- آبشدهٔ عمومی فردایی در همهٔ نمونه‌ها حداکثر ۲۵ bps، physical unspecified دقیقاً برابر و
  paper unspecified فقط در یک نمونه تا ۱۰۰ bps بود. book امروز در هر دو مسیر stale و بیش
  از ۱۰۰ bps متفاوت بود و evidence پذیرش بازار زنده محسوب نمی‌شود؛
- lane قدیمی private-gold در تمام window stale بود، درحالی‌که candidate برای bookهای
  فردایی داده تازه داشت. بنابراین اختلاف private-gold نشان‌دهندهٔ توقف oracle قدیمی و
  پوشش زندهٔ بیشتر candidate است، نه مدرک parser parity؛
- محصول ۱۴ rate و candidate فقط ۱۰ rate داشت. candidate برای `IMAM/CASH`،
  `HALF_BAHAR/CASH` و هر دو settlement یک‌گرمی به‌درستی abstain کرد. Store جدید facts سکه
  را از `2026-08-26T07:56Z` دارد، اما پوشش آبشدهٔ لازم برای anchor transfer از حدود
  `13:15Z` همان روز آغاز شده است؛ anchorهای محصول حدود ۱٫۸ تا ۶٫۸ روز قدمت داشتند. در نتیجه
  چهار absence و بخشی از drift rate ناشی از نبود backfill نقطه‌زمانی هم‌راستای سکه و
  آبشده در Store جدید است، نه از دست‌رفتن انتقال زنده؛
- تاریخچهٔ فعلی گروه‌ها از ۹ ژوئن تا ۲۶ اوت، شامل ۱۷٬۷۹۴ آفر و ۱٬۳۰۲ معامله، روی میزبان
  بات موجود است و برای anchorهای مفقود نیز نمونه دارد. این تاریخچه باید همراه با underlying
  همان timestamp و حداقل در horizon هفت‌روزهٔ موتور نرخ، به staging جدید backfill شود؛
- postcheck بات ۱۰۱٬۰۱۷ delivery با status `APPLIED`، صفر rejection و ۱۰ checkpoint stream
  داشت. هر چهار service بات و هر هفت service وب/data healthy، restart-zero و همچنان روی
  imageهای قبلی بودند؛
- `full_market_session=false`، `cutover_performed=false` و recommendation نهایی
  `HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED` ماند.

بازپخش تک‌مالک parser/lifecycle برای همین window با mount فقط‌خواندنی امتحان شد و پیش از
ساخت artifact با `database_backup_failed` متوقف شد، زیرا SQLite WAL برای online backup به
دسترسی write روی فایل هماهنگی نیاز داشت. مجوز write روی دیتابیس زنده باز نشد و هیچ artifact
ناقصی باقی نماند. اجرای بعدی باید با snapshotهای موقت محافظت‌شده و پاک‌سازی قطعی انجام شود؛
این کار مستقل از timeline موفق بالاست و هیچ مجوز ضمنی برای cutover ایجاد نمی‌کند.

گیت کد شامل ۸۶ تست متمرکز روی host و ۷۴ تست سازگار داخل image با network خاموش بود و همه
سبز شدند. دو تست shell استقرار که وجود `curl` را فرض می‌کنند فقط روی host اجرا و پاس شدند و
جزء runtime image بازار نیستند.

## failure drill و اصلاحات حین استقرار

- قطع ۱۲ ثانیه‌ای Fact receiver باعث backlog موقت ۳۳تایی شد؛ پس از بازگشت، صف بدون
  rejected/duplicate تخلیه شد.
- قطع ۱۲ ثانیه‌ای Snapshot receiver با بازیابی sender و برابری snapshot دو میزبان پایان
  یافت.
- restart جداگانه Account1 و Account2، reconciliation محدود مبتنی بر watermark، ادامه
  sequence و outbox صفر را حفظ کرد.
- تست‌های disk-full، lost ACK، restart/replay و partial tail در image اجرا شدند.
- SQLite readerهای WAL اجازه ساخت shared-memory sidecar دارند، اما connection برنامه همچنان
  `mode=ro` و `query_only=ON` است.
- پیام reconciliation بدون متن به‌تنهایی quarantine می‌شود و loop capture را متوقف نمی‌کند.
- outcome معامله‌ای که root offer آن در archive وجود ندارد با reason code جدا رد می‌شود؛
  factهای بعدی ادامه می‌یابند و FK database fail-closed باقی می‌ماند.

گیت آزمون نهایی شامل ۶۹ تست متمرکز روی host و ۴۴ تست مرتبط داخل image بود و همه سبز شدند.

## rollback مجاز این مرحله

rollback اختیار capture باید با حفظ کامل outbox/checkpoint/session انجام شود:

1. فقط دو capture کانتینری Account1 و Account2 متوقف شوند؛ processor و archive لازم نیست
   برای حفظ داده متوقف شوند.
2. inactive بودن کانتینرها و نبود PID مالک session تایید شود.
3. markerهای authority کانتینری و دو drop-in runtime حذف و `systemctl daemon-reload` اجرا
   شود.
4. `market-channel-capture.service` و `coin-capture.service` شروع شوند و single-owner،
   heartbeat و رشد sequence آن‌ها تایید شود.
5. sessionهای staging، sessionهای legacy، outbox، checkpoint، archive و snapshot حذف یا
   reset نشوند.

receipt تحویل اختیار و نسخه‌های session نگهداری‌شده، مرجع اجرای rollback هستند. حذف هیچ
artifact، data directory یا unit در Stage 13-A مجاز نیست.

## gateهای باز

Stage 13-A مجوز این تغییرها را ایجاد نمی‌کند:

- `PRIVATE_SHADOW` به `PRIVATE_PRIMARY`؛
- اتصال WebApp/product feed به snapshot خصوصی؛
- production deploy یا production Telegram authority؛
- مهاجرت Product Sync عمومی به شبکه خصوصی؛
- disable/delete timer و unitهای legacy یا پاک‌سازی artifactها.

Stage 12 و Stage 13 تا پیاده‌سازی fan-out قابل ممیزی بالا، ثبت یک جلسه کامل بازار باز با
capture manifest و snapshot timeline واقعی، report parity امضاشده، severity-1/2 صفر، p95
حداکثر ۷ ثانیه و تصمیم صریح promotion در وضعیت HOLD باقی می‌مانند. failure soak کوتاه این
سند جای آن جلسه کامل را نمی‌گیرد.
