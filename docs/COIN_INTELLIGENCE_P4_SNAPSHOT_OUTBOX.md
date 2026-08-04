# P4-A — تحویل Outbox و Snapshot محلی

## مرز این commit

این مرحله فقط دو مرز لازم برای inference محصولی را پیاده می‌کند:

1. تحویل idempotent آفر/معاملهٔ commit‌شدهٔ محصول از PostgreSQL به Market
   Store محلی؛
2. ساخت و انتشار atomically یک Snapshot نقطه‌زمانی از underlyingهای موجود.

این commit **هیچ worker، cron، task lifespan، تنظیم runtime، feature flag یا
مسیر volume را فعال نمی‌کند**. فراخوانندهٔ عملیاتی در مرحله‌ای بعدی باید
مسیر محافظت‌شدهٔ خارج از checkout را صریحاً انتخاب کند. بنابراین نصب این
commit به‌تنهایی هیچ نوشتن SQLite یا خواندن Telegram ایجاد نمی‌کند.

## Outbox → Market Store

`ProjectMarketOutboxConsumer.consume_one(session)` فقط با فراخوانی صریح کار
می‌کند و هر بار حداکثر یک row را پردازش می‌کند.

```text
PostgreSQL: claim + lease + commit
        │
        ▼
SQLite Market Store: validate payload + opaque-key upsert + commit
        │
        ▼
PostgreSQL: COMPLETE فقط با همان lease token + commit
```

- claim با `FOR UPDATE SKIP LOCKED` و lease token است؛ row منقضی‌شده قابل
  retry است.
- Market Store key فقط digestِ idempotency key است. اگر SQLite commit شود و
  پیش از COMPLETE شدن process متوقف شود، replay همان fact را upsert می‌کند
  و row دوم ساخته نمی‌شود.
- SQLite در دسترس نبودن، transaction اصلی آفر/معامله را تغییر نمی‌دهد.
  خطا با کد محدود و بدون متن خام ثبت و با backoff حداکثر ۵ دقیقه retry
  می‌شود؛ payload نامعتبر یا ۸ شکست، `FAILED` می‌شود تا بررسی شود.
- event پروژه فقط قیمت، تعداد، سمت، تسویه، lifecycle و `commodity_id` محلی
  را حمل می‌کند. نام کاربر، شماره، متن، یادداشت، public offer ID و
  Telegram identity وارد fact نمی‌شوند.
- آفرِ `exclude_from_competitive_price` نگهداری می‌شود اما با
  `quality_state=IGNORED` به مدل پیشنهاد قیمت راه پیدا نمی‌کند.

`commodity_id` محلی فقط evidence محصول است و هرگز نتیجهٔ inference یا
شناسهٔ قابل انتقال میان سایت‌ها نیست. P5 باید خروجی canonical name را با
جدول `commodities` همان سایت، exact-match کند.

## Snapshot نقطه‌زمانی

`build_market_snapshot(connection, as_of_utc=...)` برای هر read فقط rowی را
می‌بیند که **هم** `event_time_utc <= as_of` و **هم**
`available_at_utc <= as_of` دارد. بنابراین replay تاریخی از داده‌ای که
بعدتر معلوم شده استفاده نمی‌کند.

Snapshot فعلی source-separated است و موارد زیر را مستقل نگه می‌دارد:

- آبشدهٔ فیزیکال با settlement نامشخص؛ public feed آن CASH را صریحاً ثابت
  نمی‌کند، پس به‌اشتباه CASH نام نمی‌گیرد؛
- آبشدهٔ کاغذی امروز/فردا از `MELTED_FLOW`، با شمارش مستقل offer/trade و
  median وزن‌دارِ trade بالاتر؛
- آبشدهٔ خصوصی: فیزیکال امروز/فردا به‌صورت individual fact، و هر شش cell
  کاغذیِ `(today/tomorrow × normal/reverse/swim)` به‌شکل minute quote مستقل؛
- هرات نقدی، امروز و فردا؛
- تتر، فقط به‌عنوان `USDT_IRT` مستقل و با برچسب صریح «not Herat
  substitution»؛
- اونس جهانی.

رژیم اولیه فقط وقتی حداقل دو signal مستقل و fresh دارد، برچسب محافظه‌کارانه
می‌دهد؛ در غیر این صورت `ABSTAIN` است. این خروجی هنوز rate یا range کالا
نمی‌سازد و نباید مستقیم به UI یا ثبت آفر وصل شود.

`publish_market_snapshot_atomically(path, snapshot)` اول schema/privacy را
validate و سپس tmp-file را `fsync` و `os.replace` می‌کند؛ Snapshot قبلی تا
موفقیت کامل دست‌نخورده می‌ماند. `AtomicMarketSnapshotProvider` در صورت
تغییر فایل هنگام read، حجم غیرمجاز، JSON معیوب یا schema نامعتبر fail closed
است.

## آنچه هنوز انجام نشده است

- P2-B/P2-C/P2-D: کانال جدید آبشده، گروه‌های سکه، تتر و IME محصولی؛
- P4-B: لنگرهای کالا، bridge صریح هرات/تتر، low-date، basis نقدی/فردایی و
  rangeهای canonical؛
- P5: bundle محصولی، ranker و تصمیم `AUTO_SELECT`/`CONFIRM`/`ABSTAIN`؛
- هرگونه scheduler، health endpoint یا عملیات production.

تا زمانی که موارد فوق و تست‌های replay/بازگشایی انجام نشده‌اند، این Snapshot
فقط artifact زیرساختی و قابل بازبینی است، نه خروجی قیمت محصول.
