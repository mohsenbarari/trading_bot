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

Stage 12 و Stage 13 تا ثبت یک جلسه کامل بازار باز با capture manifest و snapshot timeline
واقعی، report parity امضاشده، severity-1/2 صفر، p95 حداکثر ۷ ثانیه و تصمیم صریح promotion
در وضعیت HOLD باقی می‌مانند. failure soak کوتاه این سند جای آن جلسه کامل را نمی‌گیرد.
