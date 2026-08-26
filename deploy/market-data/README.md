# Private Market Pipeline Docker Foundation

این مسیر فقط stack جدید Market Data را تعریف می‌کند و از Compose محصول جدا است.

وضعیت فعلی پس از Stage 4:

- image و dependencyها pinned و runtime غیر root است؛
- Compose پایه با override مستقل `web` و `bot` وجود دارد؛
- PostgreSQL، state، session، model و snapshot روی bind mountهای پایدار قرار می‌گیرند؛
- secretها فقط از file mount خارج Git خوانده می‌شوند؛
- فقط دو receiver روی host port منتشر می‌شوند و bind باید private IP دقیق باشد؛
- دو نقش capture دارای runtime پایدار Stage 4 هستند، اما `live` فقط با marker
  release-bound روی همان session mount قابل اجرا است؛
- نقش‌های processor، transport، adapter و estimator همچنان در `live` fail-closed هستند.

بنابراین این نسخه مجوز deploy یا تصاحب session تلگرام نیست. marker فقط در choreography
cutover و بعد از توقف owner میزبان ساخته می‌شود؛ خود Stage 4 هیچ marker یا session واقعی
نساخته است. processor/transport/estimator زنده نیز در مراحل بعدی پیاده می‌شوند.

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

## ترتیب release آینده

1. build از worktree تمیز و label برابر Git SHA؛
2. ثبت/انتقال همان digest برای هر دو میزبان؛
3. preflight path، secret و private bind؛
4. receiver-first؛
5. migration یک‌باره و idempotent؛
6. writerها بدون authority switch؛
7. shadow/parity؛
8. authority switch با مجوز مستقل؛
9. postcheck و soak؛
10. rollback فقط با pin digest قبلی و بدون حذف volume/checkpoint/outbox.
