# اتصال staging به مدل تخمین کالا

## وضعیت و مرز این سند

این runbook مسیر تاریخی Snapshot محصول staging را توضیح می‌دهد؛ جایگزین
deploy رسمی pipeline جدید market-data نیست. authority محصول فعلاً
`LEGACY` می‌ماند و `PRIVATE_PRIMARY` بدون مجوز صریح ممنوع است. این
نام‌گذاری Product به‌معنای وجود live legacy collector نیست: capture تک‌مالک
جدید تنها منبع live `GROUP_1`، `GROUP_2` و `PRIVATE_GOLD_CHANNEL` است؛
DB/snapshot قدیمی فقط historical seed/regression است و نباید gate باشد.

این بازنگری هیچ installer، deploy، restart، marker یا authority switchی اجرا نمی‌کند.

این اتصال فقط برای staging است. Snapshot مدل از خروجی اتمیک و تازهٔ publisher
خوانده می‌شود؛ این runbook owner دیگری برای capture راه نمی‌اندازد و پایگاه یا سرویس production هیچ‌گاه
نوشته نمی‌شود.

## قرارداد ایمن

- کاتالوگ کالا از production فقط به‌شکل manifest شامل `name` و `aliases` خوانده
  می‌شود. ورود manifest افزایشی است و فقط روی مرجع رسمی کاتالوگ یعنی staging
  ایران با `SERVER_MODE=iran` مجاز است؛ حذف و تغییرنام انجام نمی‌شود.
- شناسه‌های عددی بین دو پایگاه قابل حمل نیستند. sync رسمی، نام canonical و alias
  را به کلید طبیعی محلی نگاشت می‌کند.
- Snapshot ابتدا از نظر schema، تازگی و یکی از دو حالت ساختاریافتهٔ rate-ready
  یا `NO_DATA` validate می‌شود، سپس با rename اتمیک در مسیر staging هر دو سرور
  قرار می‌گیرد. فایل داخل کانتینرها read-only mount می‌شود.
- publisher مخصوص staging با پرچم صریح
  `--publish-staging-no-data-snapshot` کار می‌کند. اگر بازار بسته باشد و هیچ نرخ
  قابل تخمینی وجود نداشته باشد، به‌جای تازه‌کردن مصنوعی نرخ قبلی یک Snapshot
  تازه، معتبر و صریح `NO_DATA` منتشر می‌کند. relay فقط Snapshot تازه را منتقل
  می‌کند؛ stale، future، خراب یا ناسازگار با schema/engine همچنان متوقف می‌شود.
- Snapshot نوع `NO_DATA` هیچ نرخ قابل استفاده‌ای ندارد: تشخیص کالا abstain و
  کنترل قیمت fail-open می‌شود. سقف تازگی ۱۲۰ ثانیه تغییر نمی‌کند و
  `auto-selection` در staging باید خاموش بماند.
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

## relay تاریخی و gap اتصال به deploy رسمی

فرایند رسمی production ریپازیتوری و build authority را روی سرور بات نگه
می‌دارد، foreign را اول deploy می‌کند و source payload/imageهای متصل به همان
Git SHA را به سرور وب/ایران می‌فرستد. وجود فایل‌های `deploy/market-data`
در payload به‌معنای deployment آنها نیست. در وضع موجود، اسکریپت‌های رسمی
staging/production هنوز image جداگانه market-data را build/ship/load نمی‌کنند،
`compose.web.yml`/`compose.bot.yml` را orchestrate نمی‌کنند و migration، digest
receipt، rollout order و rollback آن را نمی‌بندند. بنابراین Stage 3 «اتصال به
deploy رسمی» هنوز باز است.

فرمان‌های زیر فقط دستورالعمل مسیر تاریخی publisher/relay هستند و نباید بدون
مجوز جداگانه اجرا شوند. روی سرور staging بات، publisher فقط با محیط
`staging` و تأیید دقیق زیر مجاز است؛ واحدها را
پیش از جایگزینی بررسی و نسخهٔ پشتیبان می‌گیرد، نصب را اتمیک انجام می‌دهد و اگر
اجرای publisher، اعتبار Snapshot یا وضعیت timer شکست بخورد واحدها و وضعیت timer
قبلی را برمی‌گرداند. هیچ credentialای در unit نوشته نمی‌شود و این installer هیچ
مسیر production deployment را فراخوانی نمی‌کند. timer relay بعد از publisher
اجرا می‌شود و همان digest را به staging ایران می‌رساند:

```bash
STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_ENVIRONMENT=staging \
STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_CONFIRM=install-staging-coin-inference-snapshot-publisher \
  scripts/install_staging_coin_inference_snapshot_publisher.sh
scripts/install_staging_coin_inference_snapshot_relay.sh
scripts/deploy_staging.sh check
```

خروج عادی publisher، چه `PUBLISHED` و چه `PUBLISHED_NO_DATA`، صفر است. خروج ۳ یا
هر خروج غیرصفر دیگری خطای واقعی unit محسوب می‌شود و نباید با
`SuccessExitStatus` پنهان شود. installer بعد از اجرای unit، Snapshot را با سقف
ثابت ۱۲۰ ثانیه بررسی می‌کند و فقط `FRESH` یا `FRESH_NO_DATA` را می‌پذیرد.

deploy فعلی محصول این Snapshot تاریخی را فقط در صورت rate-ready تازه یا
`NO_DATA` تازه می‌پذیرد؛ این check، pipeline جدید را deploy نمی‌کند. متغیرهای
staging تاریخی:

```text
STAGING_COIN_INFERENCE_PREVIEW_ENABLED=true
STAGING_COIN_INFERENCE_SELECTION_ENABLED=true
STAGING_COIN_INFERENCE_AUTO_SELECTION_ENABLED=false
STAGING_COIN_INFERENCE_SNAPSHOT_HOST_PATH=/srv/trading-bot/staging-data/coin-intelligence/coin-rates.json
STAGING_COIN_INFERENCE_SNAPSHOT_CONTAINER_PATH=/app/runtime/coin-inference/coin-rates.json
```

Compose پوشهٔ والد این فایل‌ها را read-only mount می‌کند، نه خود فایل را. این
جزئیات ضروری است چون relay فایل Snapshot را به‌صورت atomic جایگزین می‌کند؛ mount
مستقیم فایل می‌تواند کانتینر را روی inode قدیمی و Snapshot منقضی نگه دارد.

## معیار پذیرش

این معیارها فقط برای مسیر Snapshot تاریخی‌اند و Stage 3 جدید را نمی‌بندند:

1. کاتالوگ هفت‌گانه و تمام aliasهای production در هر دو staging موجود باشد.
2. digest و زمان Snapshot در دو host برابر و تازه باشد.
3. timerهای publisher و relay فعال باشند، آخرین اجرای هر دو موفق باشد و واحد
   publisher هیچ `SuccessExitStatus=3` نداشته باشد.
4. `app`، `foreign_app` و `bot` فایل را read-only ببینند و سه flag مقدار بالا
   داشته باشند.
5. یک لفظ بدون نام کالا در WebApp و بات به `CONFIRM` با گزینهٔ canonical برسد؛
   هیچ Offer در smoke ساخته نشود.
6. لفظ دارای نام صریح بدون تغییر کار کند و Snapshot stale/missing، ثبت ضمنی را
   fail-closed کند.
