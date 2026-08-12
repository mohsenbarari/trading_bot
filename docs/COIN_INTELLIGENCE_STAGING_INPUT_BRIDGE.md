# Staging input bridge

این سند مسیر ورود داده‌های موجود به staging را تثبیت می‌کند. Collectorهای
تلگرام همچنان تنها مالک اتصال شبکه هستند؛ bridge فقط پایگاه‌های محلی آن‌ها را
به قرارداد واحد `MarketObservation` projection می‌کند.

## ورودی‌های متصل

| ورودی | منبع خواندنی | مقصد | رفتار |
|---|---|---|---|
| آبشدهٔ عمومی، جریان آبشده، هرات و اونس | collector عمومی پایدار `coin-public-market-telegram.service` و `apps/telegram-price-poc/data/market_prices.sqlite3` | `private-gold-live/market/market.sqlite3` | واحد و فرم بازار نرمال می‌شود؛ هرات تومان به ریال ×۱۰ تبدیل می‌شود |
| Tether و IME | همان مخزن، جدول `external_market_observations` | همان Market Store | فقط `USDT_IRT`، `IME_GOLD_BAR` و `IME_GOLD_COIN_IMAM` و quoteهای MID/LAST/CLOSE/BID/ASK پذیرفته می‌شوند؛ IME حباب مستقل وارد این مسیر نمی‌شود |
| گروه‌های معاملاتی سکه | `apps/coin-intelligence/data/conversation_events.sqlite3` | همان Market Store | آفر و معاملهٔ تأییدشده جداگانه projection می‌شوند و quality gate موجود حفظ می‌شود |

اجرای bridge در `scripts/bridge_staging_market_inputs.py` است. متن آفر، نام
کاربر، URL، chat/message id و payload منبع در Market Store ذخیره نمی‌شوند.
collector عمومی staging که قبلاً برای انتقال مستقیم به همین Market Store نصب شده
بود، به‌دلیل تکرار منبع و ایجاد دو نویسندهٔ هم‌زمان غیرفعال است؛ منبع عمومی قدیمی
همچنان زنده است و bridge آن را با checkpoint به staging منتقل می‌کند.

## دادهٔ قدیمی و زمان دسترسی

برای رویدادهای backfill، `event_time_utc` زمان واقعی رویداد است، اما
`available_at_utc` زمان اجرای bridge (یا زمان import ثبت‌شده برای conversation)
است. بنابراین دادهٔ قدیمی برای warm-state و آموزش staging در دسترس است، ولی
ارزیابی تاریخی نباید وانمود کند که backfill در گذشته در اختیار مدل بوده است.
ارزیابی leakage-safe باید با replay جداگانه و cutoff زمانی انجام شود.

اولین export گروه‌ها که قبل از تفکیک feedهاست با `GROUP_HISTORICAL` ثبت می‌شود؛
چون نسبت دادن آن به گروه ۱ یا ۲ بدون metadata حدس خواهد بود. feedهای جاری با
`GROUP_1` و `GROUP_2` ثبت می‌شوند.

## idempotency و نگهداری

- کلیدهای opaque و deterministic جلوی ثبت تکراری را می‌گیرند.
- sourceهای بزرگ به batchهای ۵۰۰۰تایی تقسیم می‌شوند و checkpoint در فایل
  محافظت‌شدهٔ `staging/market-input-bridge.state.json` ثبت می‌شود.
- قفل محلی `staging/.market-input-bridge.lock` از اجرای همزمان bridge جلوگیری
  می‌کند. سرویس private collector و bridge علاوه بر آن از
  `staging/.market-store-writer.lock` مشترک استفاده می‌کنند تا روی SQLite
  هم‌زمان ننویسند؛ اجرای عمومی از مسیر collector قدیمی انجام می‌شود.
- state فقط آخرین شناسهٔ داخلی source را نگه می‌دارد و در Market Store عبور
  نمی‌کند.

## حدود فعلی

اگر wa-ir/IME یا provider تتر دادهٔ واقعی تازه تولید نکند، bridge مقدار جایگزین
نمی‌سازد و Snapshot همان signal را `MISSING` نشان می‌دهد. USDT هرگز جای هرات
نمی‌نشیند. اجرای این bridge staging-only است و به PostgreSQL پروژه یا production
نمی‌نویسد.
