# Private Market Pipeline Docker Foundation

این مسیر فقط stack جدید Market Data را تعریف می‌کند و از Compose محصول جدا است.

وضعیت فعلی Stage 3:

- image و dependencyها pinned و runtime غیر root است؛
- Compose پایه با override مستقل `web` و `bot` وجود دارد؛
- PostgreSQL، state، session، model و snapshot روی bind mountهای پایدار قرار می‌گیرند؛
- secretها فقط از file mount خارج Git خوانده می‌شوند؛
- فقط دو receiver روی host port منتشر می‌شوند و bind باید private IP دقیق باشد؛
- `MARKET_PIPELINE_MODE=live` عمداً fail-closed است؛ تنها `fixture` برای rehearsal مجاز است.

بنابراین این نسخه مجوز deploy یا تصاحب session تلگرام نیست. capture/processor/transport/estimator زنده در مراحل بعدی پیاده می‌شوند و تا gate همان مرحله نباید این stack روی staging یا production بالا آورده شود.

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
