# Staging input bridge

این سند مسیر ورود داده‌های موجود به staging را تثبیت می‌کند. Collectorهای
تلگرام همچنان تنها مالک اتصال شبکه هستند؛ bridge فقط پایگاه‌های محلی آن‌ها را
به قرارداد واحد `MarketObservation` projection می‌کند.

## ورودی‌های متصل

| ورودی | منبع خواندنی | مقصد | رفتار |
|---|---|---|---|
| آبشدهٔ عمومی، جریان آبشده، هرات و اونس | collector عمومی پایدار `coin-public-market-telegram.service` و `apps/telegram-price-poc/data/market_prices.sqlite3` | `private-gold-live/market/market.sqlite3` | واحد و فرم بازار نرمال می‌شود؛ هرات تومان به ریال ×۱۰ تبدیل می‌شود |
| Tether و IME | همان مخزن، جدول `external_market_observations` | همان Market Store | فقط `USDT_IRT`، `IME_GOLD_BAR` و `IME_GOLD_COIN_IMAM` و quoteهای MID/LAST/CLOSE/BID/ASK پذیرفته می‌شوند؛ IME حباب مستقل وارد این مسیر نمی‌شود |
| گروه‌های معاملاتی سکه | collector مستقل `coin-group-event-telegram.service` | همان Market Store | پیام خام حداکثر سه روز در staging محلی می‌ماند و parser/resolver/trade-linker فعلی مستقیماً factهای کمینه‌شده را ثبت می‌کنند |

اجرای bridge در `scripts/bridge_staging_market_inputs.py` است. متن آفر، نام
کاربر، URL، chat/message id و payload منبع در Market Store ذخیره نمی‌شوند.
collector عمومی سازگار با estimator فقط مالک schema فشردهٔ صفحه است و bridge
آن را با checkpoint به Market Store منتقل می‌کند. گروه‌ها دیگر از conversation
DB قدیمی bridge نمی‌شوند؛ این مرز از حلقه و دوبرابر شدن داده میان Market Store
و projection سازگار داشبورد جلوگیری می‌کند.

## دادهٔ قدیمی و زمان دسترسی

برای رویدادهای backfill، `event_time_utc` زمان واقعی رویداد است، اما
`available_at_utc` زمان اجرای bridge
است. بنابراین دادهٔ قدیمی برای warm-state و آموزش staging در دسترس است، ولی
ارزیابی تاریخی نباید وانمود کند که backfill در گذشته در اختیار مدل بوده است.
ارزیابی leakage-safe باید با replay جداگانه و cutoff زمانی انجام شود.

دادهٔ تاریخی گروه‌ها که پیش از این وارد Market Store شده حفظ می‌شود؛ feedهای
جاری با `GROUP_1` و `GROUP_2` و بدون شناسه یا متن خام در آن ثبت می‌شوند.

## idempotency و نگهداری

- کلیدهای opaque و deterministic جلوی ثبت تکراری را می‌گیرند.
- sourceهای بزرگ به batchهای محدود تقسیم می‌شوند و checkpoint در فایل
  محافظت‌شدهٔ `staging/market-input-bridge.state.json` ثبت می‌شود.
- قفل محلی `staging/.market-input-bridge.lock` از اجرای همزمان bridge جلوگیری
  می‌کند. collectorهای private/group و bridge علاوه بر آن از
  `staging/.market-store-writer.lock` مشترک استفاده می‌کنند تا روی SQLite
  هم‌زمان ننویسند؛ collector عمومی سازگار در مسیر canonical داده اجرا می‌شود.
- state فقط آخرین شناسهٔ داخلی source را نگه می‌دارد و در Market Store عبور
  نمی‌کند.

## حدود فعلی

اگر wa-ir/IME یا provider تتر دادهٔ واقعی تازه تولید نکند، bridge مقدار جایگزین
نمی‌سازد و Snapshot همان signal را `MISSING` نشان می‌دهد. USDT هرگز جای هرات
نمی‌نشیند. اجرای این bridge staging-only است و به PostgreSQL پروژه یا production
نمی‌نویسد.
