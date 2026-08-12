# قرارداد Market Store برای تشخیص کالا

**Contract version:** `1`
**SQLite schema version:** `3`
**وضعیت:** P1 — قرارداد ذخیره‌سازی؛ هنوز هیچ collector، worker یا API محصولی
را فعال نمی‌کند.

## مرز داده

`MarketObservation` تنها مسیر نوشتن در Market Store است. هر observation
یک `event_key` باینری و opaque دارد که پیش از ورود با `derive_event_key` از
شناسهٔ خصوصی منبع ساخته می‌شود. Store هیچ‌کدام از متن خام، نام کاربر، URL،
شناسهٔ پیام/کانال یا شماره تلفن را نمی‌پذیرد.

raw staging فقط برای adapterهای P2 است، حداکثر سه روز ماندگاری دارد و دیتابیس
آن از Market Store جداست. Postgres پروژه نیز برای تراکنش‌ها و outbox P3
مرجع قطعی باقی می‌ماند؛ SQLite هرگز دلیل rollback کردن ثبت Offer یا Trade
نیست.

## قرارداد observation

| دسته | فیلدهای لازم |
| --- | --- |
| هویت امن | `event_key` opaque، `source_code`، `source_family` |
| زمان | `event_time_utc`، `available_at_utc`، `tehran_datetime/date/minute/weekday` مشتق‌شده از event time |
| اقتصاد | `instrument`، `market_label`، `price`، `price_unit`، `currency`، `quantity/unit` |
| نوع رویداد | `settlement_term`، `trade_form`، `event_type`، `side`، `is_conditional` |
| کیفیت | `parser_version`، `parse_confidence`، `quality_state`، `quality_policy_version` |
| attribute امن | فقط JSON نرمال‌شدهٔ فاقد identity/raw text |

مقادیر قرارداد fail-closed هستند. `PAPER_*` و `PHYSICAL` جدا هستند؛ برای
اونس `SPOT`/`NOT_APPLICABLE` نیز صریح است. unit سازگار با instrument باید
صریح باشد. برای مثال `USD_HERAT` فقط
`TOMAN_PER_USD`، `USDT_IRT` فقط `TOMAN_PER_USDT` و `MELTED_GOLD*` فقط
`TOMAN_PER_MESGHAL_750` می‌پذیرند. adapterهای legacy تبدیل Rial/Toman را
پیش از ساخت observation و با provenance صریح انجام می‌دهند؛ هیچ conversion
یا جایگزینی پنهان در خود قرارداد (از جمله
تتر به‌جای هرات) در این لایه انجام نمی‌شود.

## schema و سازگاری legacy

یک جدول قابل‌نوشتن وجود دارد: `market_observations`. نام سابق
`external_market_observations` فقط یک **view read-only** روی observationهای
`EXTERNAL_MARKET` است؛ بنابراین دو schema موازی نداریم.

`initialize_market_store` روی SQLite قدیمی با `price_events` یا جدول خارجی
legacy، عمداً خطا می‌دهد. مسیر درست فقط این است:

1. collector/legacy database را متوقف یا read-only کن؛
2. `upgrade_legacy_market_store(source_path, destination_path)` را با مقصد
   جدا اجرا کن؛
3. report، count و freshness را بررسی کن؛
4. فقط پس از تأیید، adapterهای جدید را به مقصد canonical وصل کن؛
5. نگهداری یا حذف source قدیمی بر اساس سیاست retention انجام می‌شود، نه توسط
   migration.

source با `mode=ro` باز می‌شود. واحد یا conversion نامعلوم skip می‌شود، نه
حدس زده. متن خام و شناسه‌های legacy در مقصد کپی نمی‌شوند.

Schema `2` فقط جدول عملیاتی `market_source_checkpoints` را اضافه می‌کند. این
جدول برای restart-safe خواندن public sourceها، حداکثر message ID و زمان آن را
نگه می‌دارد؛ این شناسه در observation/model row ذخیره نمی‌شود. upgrade `1`
به `2` additive است، هیچ fact موجودی را بازنویسی نمی‌کند و فقط در زمان باز
شدن Store انجام می‌شود.

Schema `3` indexهای متناسب با Snapshot و جدول cold archive را اضافه می‌کند.
upgrade `2` به `3` نیز هیچ fact یا مقیاس قیمتی را بازنویسی نمی‌کند؛ اصلاح
داده‌ی legacy با مقیاس نادرست باید از مسیر audit/repair صریح انجام شود.

## خارج از P1

- دریافت تلگرام/تتر/IME و checkpoint آنها (P2)؛
- PostgreSQL outbox پروژه (P3)؛
- Snapshot، anchor، regime و pricing (P4)؛
- inference، Bot و WebApp (P5/P6)؛
- هر نوع sync یا runtime سه‌سروره.
