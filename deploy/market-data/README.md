# Private Market Pipeline Docker Foundation

این مسیر فقط stack جدید Market Data را تعریف می‌کند و از Compose محصول جدا است.

وضعیت فعلی پس از گیت offline مرحله 12:

- image و dependencyها pinned و runtime غیر root است؛
- Compose پایه با override مستقل `web` و `bot` و به‌ترتیب ۸ و ۴ service وجود دارد؛
- PostgreSQL، state، session، model و snapshot روی bind mountهای پایدار قرار می‌گیرند؛
- secretها فقط از file mount خارج Git خوانده می‌شوند؛
- فقط دو receiver روی host port منتشر می‌شوند و bind باید private IP دقیق باشد؛
- دو نقش capture دارای runtime پایدار Stage 4 هستند، اما تصاحب session در `live` فقط با marker
  release-bound روی همان session mount قابل اجرا است؛
- `market-capture-external` بدون session/credential خصوصی، Wallex و PAXG را با outbox
  پایدار و poll پیش‌فرض ۱۰ ثانیه‌ای در spool مستقل ثبت می‌کند؛
- `market-processor` هر سه spool و نه source را با بودجه مستقل مصرف می‌کند و فقط
  Market Store و input ledger در حالت shadow می‌سازد؛
- PostgreSQL archive/outbox، Fact sender/receiver، adapter بات، estimator snapshot و مسیر
  برگشت WebApp پیاده و در Docker آزمایش شده‌اند؛ migration مستقل اکنون version 3 و ۲۸ جدول است؛
- importer تاریخچه و parity report امضاشده آماده‌اند، ولی import واقعی، deploy و authority
  switch هنوز انجام نشده است.

بنابراین این نسخه به‌تنهایی مجوز deploy یا تصاحب session تلگرام نیست. marker فقط در
choreography cutover و بعد از توقف owner میزبان ساخته می‌شود. feed پیش‌فرض `LEGACY` است؛
ابتدا `PRIVATE_SHADOW` و live open-market parity لازم است و `PRIVATE_PRIMARY` مجوز مستقل
می‌خواهد. هیچ مرحله offline، capture owner فعلی یا authority مدل را تغییر نداده است.

در mode زنده، processor بدون دو snapshot فقط‌خواندنی و هم‌دورهٔ
`review-decisions.sqlite3` و `prediction-ledger.sqlite3` شروع نمی‌شود. فایل دوم باید
لنگرهای `MAIN_ONLINE` را با زمان رخداد و زمان availability واقعی نگه دارد. جایگزین‌کردن
آن با تخمین فعلی یا اجرای parser بدون این دو ورودی ممنوع است. ورودی‌ها باید با rename
اتمیک در مسیر `calibration/coin-groups` منتشر شوند؛ DB در حال نوشتن legacy مستقیم mount
نمی‌شود.
seed اولیه فقط با `export_market_calibration_seed.py` و از ستون‌های allowlist‌شده ساخته
می‌شود؛ کپی کامل SQLite تولیدی ممنوع است. در Shadow، estimator snapshot فقط در lane
ایزولهٔ Shadow منتشر می‌شود و حق refresh کردن prediction ledger مصرفی parser را ندارد.
آن ledger فقط از seed قابل ممیزی و سپس، بعد از مجوز مستقل Primary، از snapshotهای
`PRIVATE_PRIMARY` به‌روز می‌شود. processor آن را query-only می‌خواند؛ DB زندهٔ legacy
هرگز mount نمی‌شود و هیچ snapshot ثابت روز deployment نیز نباید بی‌قید زمان مصرف شود.

دایرکتوری والد secret باید `root:root 0700` و فایل‌های مصرفی باید `root:10001 0440` باشند. PostgreSQL با UID/GID `70:70` فقط supplemental group `10001` می‌گیرد؛ بنابراین همان فایل password برای migration/runtime قابل خواندن است، بدون root container یا world-readable secret.

## Evidence رسمی release (بدون deploy)

`scripts/prepare_market_pipeline_release.py` دو source نقش‌محور خارج Git را بررسی و envهای
release-bound می‌سازد. sourceها فقط topology و مسیر secret هستند؛ image، Git SHA، mode، feed
و primary authority را controller رسمی تزریق می‌کند. فعال‌سازی evidence در manifest production
به confirmation دقیق نیاز دارد و فقط image/receipt/env pair را آماده می‌کند. این مرحله image را
به وب منتقل یا load نمی‌کند، migration/service اجرا نمی‌کند و هیچ Telegram session یا Product
authority را تغییر نمی‌دهد. نمونه sourceها:

- `web.production-source.env.example`؛
- `bot.production-source.env.example`.

gate دوم اختیاری، payload کنترلی commit-exact را در
`/srv/trading-bot/market-pipeline-releases/<SHA>` روی هر دو میزبان نگه می‌دارد، image را مستقیم
از Docker store بات به `docker load` وب stream می‌کند و پس از تطبیق content ID، preflight واقعی
دو نقش را اجرا می‌کند. این gate نیز service یا migration اجرا نمی‌کند؛ حداقل فضای آزاد پیش‌فرض
۲ GiB است و هیچ artifact انتقالی در `/tmp` یا `/var/tmp` نمی‌سازد.

ابزار `scripts/backup_market_pipeline_archive.py` گیت مستقل پیش از migration است. برای DB
موجود، runtime دقیق PostgreSQL را bind می‌کند، `pg_dump -Fc` را در مسیر root-only خارج از
data root می‌سازد و آن را در container موقت بدون network restore می‌کند. schema/table/fact
count باید reconcile شود و منابع موقت بعد از cleanup نباید باقی بمانند. datastore کاملاً خالی
فقط receipt `INITIAL_EMPTY` می‌گیرد؛ حالت نیمه‌ساخته یا DB بدون schema بازار رد می‌شود. backup
موجود باید پیش از migration با SHA-256 یکسان روی میزبان دوم نگه‌داری شود. این ابزار transport
یا migration/service start انجام نمی‌دهد.

controller رسمی گیت بعدی را فقط با confirmation مستقل فعال می‌کند: receipt/artifact verified
روی میزبان بات نگه‌داری می‌شود، سپس `scripts/migrate_market_pipeline_archive.py` فقط database
را با `--no-recreate` آماده و migration را دو بار اجرا می‌کند. اجرای دوم باید
`already_current` باشد و فقط database مجاز است running بماند؛ capture و Product authority
خاموش می‌مانند. شکست initial database باعث stop بدون حذف state می‌شود.

گیت receiver-first بعدی از `scripts/rollout_market_pipeline_shadow.py` استفاده می‌کند و فقط
receiverهای بات/وب، processor+fact sender وب و adapter+estimator+snapshot sender بات را شروع
می‌کند. captureهای account1/account2/external در فهرست مجاز نیستند. journal دو میزبان rollback
را به containerهای دقیق ساخته‌شده محدود می‌کند؛ volume/state/database حفظ می‌شوند. runtime
قدیمی موجود به‌جای replace خودکار رد می‌شود تا upgrade/rollback مستقل آن طراحی شود.

## فایل‌ها

- `Dockerfile`: image مشترک serviceها، Python 3.11 Bookworm pinned؛
- `requirements.lock`: dependencyهای Stage 3 همراه hash؛
- `compose.yml`: امنیت و network مشترک؛
- `compose.web.yml`: database و پنج service سرور وب/داده؛
- `compose.bot.yml`: چهار service سرور بات؛
- `market-data.env.example`: فقط نام متغیرها و pathها، بدون credential؛
- `migrations/`: migration مستقل Market Data.

## فرمان‌های مجاز Stage 3

```bash
APP_ENV_FILE=config/unit-test.env.example python3 -m unittest \
  tests.test_market_pipeline_stage3_foundation \
  tests.test_rehearse_market_pipeline_stage3

python3 scripts/manage_market_pipeline_stage3.py prepare-paths \
  --role web --root /path/to/disposable-root

python3 scripts/rehearse_market_pipeline_stage3.py
```

`prepare-paths` بدون `--apply --acknowledge-host-mutation` فقط dry-run است. rehearsal نیز worktree تمیز می‌خواهد و فقط fixture secret، listener لوپ‌بک و path موقت می‌سازد؛ cleanup آن حذف کامل artifactهای خودش را کنترل می‌کند.

## Gate مرحله 4

```bash
APP_ENV_FILE=config/unit-test.env.example python3 -m unittest \
  tests.test_market_pipeline_stage4_capture \
  tests.test_rehearse_market_capture_stage4

python3 scripts/rehearse_market_capture_stage4.py
```

rehearsal مرحله 4 با `--network none` و envelopeهای مصنوعی اجرا می‌شود. دو crash window،
restart/replay، مالک دوم، retention دقیق، edit/delete/reply metadata و سلامت per-source را
می‌سنجد و هیچ parser، session، credential یا database محصولی را باز نمی‌کند.

config زنده یک secret JSON با contract برابر
`market_telegram_capture_config/1.0` است. Account 1 باید دقیقاً پنج source code
`MELTED_PRIMARY_FLOW`, `MELTED_AGGREGATE`, `MELTED_FLOW`, `USD_HERAT`, `XAUUSD` و
Account 2 باید دقیقاً `GROUP_1`, `GROUP_2` را bind کند. peer ID و API credential فقط
داخل همان secret خارج Git می‌مانند. HMAC هویت فرستنده Account 2 secret جداگانه است.
نسخه 2.1 رویداد گروه، شناسه تلگرام و نام نمایشی را فقط تا مرز processor حمل می‌کند؛
نسخه دائمی هر دو مقدار با کلید مستقل `market_research_encryption_key` در PostgreSQL وب
رمز می‌شود و هرگز وارد Fact، transport بات، log یا heartbeat نمی‌شود. متن خام متناظر با
factهای پنج منبع پژوهشی نیز با همان کلید و بدون message/link/channel metadata نگه‌داری
می‌شود. دو کانال عمومی آبشده از migration 3 آرشیو دائمی‌اند؛ envelopeهای ACKشده outbox
پس از هفت روز و فقط پشت checkpoint به `{}` فشرده می‌شوند.
پیش‌نمایش backfill محلی و بدون نمایش محتوای حساس با دستور زیر انجام می‌شود؛ افزودن
`--apply` فقط پس از migration و نصب secret مجاز است و ورودی‌های هم‌پوشان را idempotent
مصرف می‌کند:

```bash
python -m core.market_intelligence.market_research_backfill \
  --capture-db /var/lib/market-data/state/market-processor/capture-staging.sqlite3
```

captureهای قدیمی گروه فقط peer HMAC و نام `null` دارند؛ بنابراین شناسه و نام واقعی آن
بخش از تاریخچه قابل بازسازی نیست و آرشیو هویت از اولین رویداد نسخه 2.1 کامل می‌شود.
در cutover، همان HMAC key فعال capture فعلی باید به secret جدید منتقل شود؛ تولید کلید
تازه branchهای reply نزدیک cutover را از هم جدا می‌کند و ممنوع است.

## Gate مرحله 5

```bash
APP_ENV_FILE=config/unit-test.env.example python3 -m unittest \
  tests.test_audit_coin_group_parser_production \
  tests.test_coin_group_calibration_corpus \
  tests.test_market_pipeline_stage5_coin_processor \
  tests.test_rehearse_market_coin_parser_stage5

python3 scripts/rehearse_market_coin_parser_stage5.py
```

ممیزی production-shaped با چهار SQLite فقط‌خواندنی اجرا می‌شود: staging خام، Market
Store، اصلاحات انسانی و prediction ledger. خروجی فقط count و reason code دارد و متن،
شناسه پیام، event key یا هویت فرستنده را چاپ/کپی نمی‌کند. rehearsal Docker نیز با
`--network none`، داده مصنوعی، partial tail، invalid sibling، restart/replay، reply
branch دقیق و instrument inference مبتنی بر لنگر زمانی اجرا و همه artifactهای خود را
پاک می‌کند.

## Gate مراحل 6 تا 12

همان rehearsal ایزوله parser علاوه بر lifecycle کانال خصوصی، مسیر external و ledger را
می‌سنجد: دو quote واقعی XAU در یک دقیقه، Wallex point/mean دقیق Decimal، تقدم XAU مستقیم
بر PAXG، چهار outcome خصوصی، quiet-cycle idempotency و replay صفر. تست متمرکز Stage 7:

```bash
APP_ENV_FILE=config/unit-test.env.example python3 -m unittest \
  tests.test_market_pipeline_stage7_input_materializer \
  tests.test_market_pipeline_stage6_channel_processor

python3 scripts/rehearse_market_coin_parser_stage5.py
```

spool خارجی فقط قرارداد کمینه `external_quote_event/1.0` را نگه می‌دارد؛ response خام،
URL، API key یا header در آن ذخیره نمی‌شود. MID هر poll موفق Wallex ورودی اصلی USDT است؛
BID/ASK برای audit همان observation واقعی باقی می‌مانند. PAXG همیشه proxy برچسب می‌خورد
و فقط با guard دو book و band اونس مستقیم قابل انتخاب است.

گیت تاریخچه و parity:

```bash
MARKET_STAGE11_IMAGE=<commit-bound-local-image> \
  scripts/run_market_history_stage11_gate.sh

python3 scripts/market_shadow_parity_stage12.py --help
python3 scripts/rehearse_market_shadow_stage12.py --events 1000
```

Stage 11 فقط bundle نرمال‌شده و sensitive ciphertext را می‌پذیرد؛ seed بات raw یا identity
ندارد. Stage 12 بدون capture manifest کامل، snapshot timeline واقعی، report HMAC-signed،
یک جلسه کامل بازار باز و verifier مستقل receiptهای release-bound هرگز promotion
توصیه نمی‌کند. نتیجه replay آفلاین فعلی `HOLD_LIVE_OPEN_MARKET_REQUIRED` است؛
evidence زنده تا پیاده‌شدن verifier با `TRUSTED_LIVE_ATTESTATION_UNAVAILABLE` و
`HOLD_BLOCKING_PARITY_FINDINGS` متوقف می‌شود.

export عملیاتی Stage 11 باید `--temporary-directory` را به یک دایرکتوری خالی، محافظت‌شده،
متعلق به همان کاربر اجرا، با mode `0700` و واقع بر دیسک بدهد. این scratch نباید `/tmp` باشد و هیچ اجرای هم‌زمان یا
artifact باقی‌مانده را نمی‌پذیرد؛ sort بزرگ SQLite در RAM یا tmpfs برای backfill کامل مجاز
نیست.

## ترتیب release آینده

1. build از worktree تمیز و label برابر Git SHA؛
2. ثبت/انتقال همان digest رجیستری یا Docker image ID محتواآدرس‌پذیر برای هر دو میزبان؛
3. preflight path، secret و private bind؛
4. backup/restore-smoke و کپی verified روی failure domain دوم؛
5. migration یک‌باره و اجرای دوم `already_current`؛
6. receiver-first و سپس writerها، بدون authority switch؛
7. shadow/parity؛
8. authority switch با مجوز مستقل؛
9. postcheck و soak؛
10. rollback فقط با pin digest قبلی و بدون حذف volume/checkpoint/outbox.

## فضای موقت انتقال release

فایل image/release هرگز نباید در `/tmp` میزبان ساخته یا کپی شود؛ `/tmp` روی میزبان وب
`tmpfs` است و هر byte آن RAM مصرف می‌کند. مسیر موقت مجاز، disk-backed و محدود زیر است:

```bash
sudo scripts/install_market_pipeline_transfer_workspace.sh
```

این فرمان `/var/tmp/trading-bot-market-pipeline-transfer` را با مالکیت `root:root` و mode
`0700` می‌سازد و پاک‌سازی محتوای قدیمی‌تر از یک ساعت را به `systemd-tmpfiles` می‌سپارد.
انتقال ترجیحاً باید stream شود (`docker save | ssh docker load`) تا فایل remote ساخته نشود؛
اگر ابزار انتقال به فایل نیاز دارد فقط همین مسیر مجاز است و پس از verify/import باید همان
فایل را در trap موفقیت یا شکست حذف کند. releaseهای commit-bound و rollback فقط در مسیر
پایدار `/srv/trading-bot/market-pipeline-releases` نگه‌داری می‌شوند، نه در فضای موقت.
