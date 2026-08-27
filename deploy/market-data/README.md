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
  برگشت WebApp پیاده و در Docker آزمایش شده‌اند؛ migration مستقل اکنون version 2 و ۲۶ جدول است؛
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
می‌شود؛ کپی کامل SQLite تولیدی ممنوع است. پس از شروع shadow، receiver وب هر estimator
snapshot امضاشده را با transaction اتمیک به ledger اختصاصی single-writer/WAL می‌افزاید و
فقط ۲۴ ساعت را نگه می‌دارد. processor این ledger محلی را query-only می‌خواند؛ DB زندهٔ
legacy هرگز mount نمی‌شود و parser به snapshot ثابت روز deployment وابسته نمی‌ماند.

دایرکتوری والد secret باید `root:root 0700` و فایل‌های مصرفی باید `root:10001 0440` باشند. PostgreSQL با UID/GID `70:70` فقط supplemental group `10001` می‌گیرد؛ بنابراین همان فایل password برای migration/runtime قابل خواندن است، بدون root container یا world-readable secret.

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
ندارد. Stage 12 بدون capture manifest کامل، snapshot timeline واقعی، report HMAC-signed و
یک جلسه کامل بازار باز هرگز promotion توصیه نمی‌کند. نتیجه offline فعلی
`HOLD_LIVE_OPEN_MARKET_REQUIRED` است.

## ترتیب release آینده

1. build از worktree تمیز و label برابر Git SHA؛
2. ثبت/انتقال همان digest رجیستری یا Docker image ID محتواآدرس‌پذیر برای هر دو میزبان؛
3. preflight path، secret و private bind؛
4. receiver-first؛
5. migration یک‌باره و idempotent؛
6. writerها بدون authority switch؛
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
