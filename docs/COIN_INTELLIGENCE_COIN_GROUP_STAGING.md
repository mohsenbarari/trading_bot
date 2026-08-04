# P2-C-B1 — نگهداری موقت پیام گروه‌های سکه

## هدف و مرز

`coin_group_staging.py` تنها boundary کوتاه‌مدتِ private message برای گروه‌های
معاملاتی ۱ و ۲ است. متن، شناسهٔ message، reply relation و هویت هش‌شده فقط
برای parser/reconciliation لازم‌اند و هرگز به Market Store یا Snapshot راه
پیدا نمی‌کنند. این ماژول collector، scheduler یا Telegram credential ندارد.

## محل و ماندگاری

در deployment، مسیر SQLite باید خارج از checkout باشد. caller با
`repository_root` این قاعده را enforce می‌کند؛ ذخیره کردن raw text زیر مسیر
repository به‌طور fail-closed رد می‌شود. هر row دقیقاً سه روز پس از
`available_at_utc` منقضی است و `purge_expired_coin_group_staging` متن، reply
و digest را حذف می‌کند. دادهٔ نهایی Market Store و هر fact از قبل promotion
شده هرگز با این purge حذف نمی‌شود.

## idempotency و edit

کلید موقت `(group_number, message_id)` است. replay با content digest برابر
فقط timestamp مشاهده را به‌روز می‌کند؛ تغییر واقعی متن، reply، زمان، هویت
هش‌شده یا edit timestamp همان row را با `revision + 1` جایگزین می‌کند. به
همین دلیل parser بعدی فقط current version را می‌بیند و می‌تواند edit/reply
chain را بدون نگهداری نام یا شناسهٔ خام در مدل بررسی کند.

## موارد عمداً defer شده

این مرحله هنوز payload چند-event در یک post Telegram را split نمی‌کند،
trade را تشخیص نمی‌دهد و fact نهایی تولید نمی‌کند. P2-C-B2 مسئول adaptation
رویدادهای JSON به یک `CoinGroupStagingMessage` به‌ازای هر پیام واقعی است؛
P2-C-B3 resolver علّی کالا/قیمت و P2-C-B4 linking قطعی معامله را اضافه
می‌کنند. تا آن مراحل، همهٔ factهای خروجی P2-C-A `PENDING_REVIEW` هستند.
