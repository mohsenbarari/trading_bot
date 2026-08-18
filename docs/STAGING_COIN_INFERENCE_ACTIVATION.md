# اتصال staging به مدل تخمین کالا

این اتصال فقط برای staging است. Snapshot مدل از خروجی اتمیک و تازهٔ publisher
خوانده می‌شود؛ collector جدیدی راه نمی‌افتد و پایگاه یا سرویس production هیچ‌گاه
نوشته نمی‌شود.

## قرارداد ایمن

- کاتالوگ کالا از production فقط به‌شکل manifest شامل `name` و `aliases` خوانده
  می‌شود. ورود manifest افزایشی است و فقط روی مرجع رسمی کاتالوگ یعنی staging
  ایران با `SERVER_MODE=iran` مجاز است؛ حذف و تغییرنام انجام نمی‌شود.
- شناسه‌های عددی بین دو پایگاه قابل حمل نیستند. sync رسمی، نام canonical و alias
  را به کلید طبیعی محلی نگاشت می‌کند.
- Snapshot ابتدا از نظر schema، تازگی و وجود نرخ تخمینی validate می‌شود، سپس با
  rename اتمیک در مسیر staging هر دو سرور قرار می‌گیرد. فایل داخل کانتینرها
  read-only mount می‌شود.
- `preview` و `selection` در staging روشن‌اند، ولی `auto-selection` خاموش است.
  بنابراین حذف نام کالا از لفظ مجاز است، اما کاربر باید پیشنهاد مدل را صریحاً
  تأیید کند. تغییر یا کهنگی Snapshot هنگام ثبت نهایی باعث رد fail-closed می‌شود.
- این فعال‌سازی هیچ مجوزی برای production ایجاد نمی‌کند.

## همگام‌سازی کاتالوگ

manifest باید با query فقط‌خواندنی از production ساخته و بدون شناسهٔ عددی به
stdin ابزار داده شود. ابتدا dry-run و سپس apply روی کانتینر API staging ایران:

```bash
python scripts/sync_staging_commodity_catalog.py \
  --manifest /secure-temporary/catalog.json \
  --expected-database-name trading_bot_staging \
  --expected-server-mode iran

python scripts/sync_staging_commodity_catalog.py \
  --manifest /secure-temporary/catalog.json \
  --expected-database-name trading_bot_staging \
  --expected-server-mode iran \
  --apply
```

پس از apply باید هر دو peer با کلید طبیعی برابر، backlog باز صفر و اجرای دوبارهٔ
ابزار بدون addition باشد. manifest موقت نباید commit شود.

## relay و deploy

روی سرور staging بات، installer زیر timer سی‌ثانیه‌ای را نصب می‌کند. timer بعد
از publisher اجرا می‌شود و همان digest را به staging ایران می‌رساند:

```bash
scripts/install_staging_coin_inference_snapshot_relay.sh
scripts/deploy_staging.sh check
```

deploy رسمی هر peer فقط در صورت Snapshot تازه ادامه می‌یابد. متغیرهای staging:

```text
STAGING_COIN_INFERENCE_PREVIEW_ENABLED=true
STAGING_COIN_INFERENCE_SELECTION_ENABLED=true
STAGING_COIN_INFERENCE_AUTO_SELECTION_ENABLED=false
STAGING_COIN_INFERENCE_SNAPSHOT_HOST_PATH=/srv/trading-bot/staging-data/coin-intelligence/coin-rates.json
STAGING_COIN_INFERENCE_SNAPSHOT_CONTAINER_PATH=/app/runtime/coin-inference/coin-rates.json
```

## معیار پذیرش

1. کاتالوگ هفت‌گانه و تمام aliasهای production در هر دو staging موجود باشد.
2. digest و زمان Snapshot در دو host برابر و تازه باشد.
3. `app`، `foreign_app` و `bot` فایل را read-only ببینند و سه flag مقدار بالا
   داشته باشند.
4. یک لفظ بدون نام کالا در WebApp و بات به `CONFIRM` با گزینهٔ canonical برسد؛
   هیچ Offer در smoke ساخته نشود.
5. لفظ دارای نام صریح بدون تغییر کار کند و Snapshot stale/missing، ثبت ضمنی را
   fail-closed کند.
