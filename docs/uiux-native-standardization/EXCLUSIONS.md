# مرزهای Native App Standardization

این track زبان بصری و UX همهٔ مسیرهای وب‌اپ را یکپارچه می‌کند، اما قراردادهای
زیر را عمداً بازنویسی نمی‌کند.

- `/market` و تمام فید/کارت/مهلت/وقت‌اضافه/two-tap آن قفل مالک است. build
  candidate در ۳۹۰ و ۱۴۴۰ با build فعلی `main` تصویر بایت‌به‌بایت یکسان دارد.
- `home-market-widget`، `admin-messages-market-delivery` و
  `trading-settings-market-controls` رفتار پذیرفته‌شدهٔ Market را حفظ می‌کنند.
- پیام‌رسان از نظر بصری یکپارچه شده، ولی schema، WebSocket، upload/download،
  cache، تشخیص آلبوم و rollout پیش‌فرض `legacy` تغییر نکرده است.
- Telegram Mini App منسوخ است و در این track احیا یا آزمون نمی‌شود.
- backend، مجوزها، داده، staging، production، Sites و انتشار خارج از این مأموریت‌اند.

این موارد «ناشناخته» یا کار ناتمام نیستند؛ مرز صریح و قابل‌ممیزی کارند.
