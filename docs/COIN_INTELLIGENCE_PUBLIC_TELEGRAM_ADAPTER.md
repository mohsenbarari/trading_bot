# Public Telegram market adapter — P2-A

این adapter فقط چهار source عمومی لازم برای ساخت لنگر نرخ را پشتیبانی می‌کند:

| source code | بازار | قاعدهٔ مهم |
| --- | --- | --- |
| `MELTED_AGGREGATE` | آبشده / مظنه اتحادیه | فقط «نقدی» یا «رسمی» physical است؛ گرم و خلاصهٔ ساعتی حذف می‌شوند |
| `MELTED_FLOW` | آبشدهٔ کاغذی NaghdP | امروز جداست؛ «باحواله» بدون روز، فردایی است؛ معاملهٔ بدون سمت فقط از آفر strictly-prior هم‌قیمت side می‌گیرد |
| `USD_HERAT` | دلار هرات | فقط «نقدی» physical است؛ امروز/فردا و حالت بدون نقدی paper هستند |
| `XAUUSD` | اونس جهانی | `SPOT` است، trade نیست و آخرین quote هر دقیقه حفظ می‌شود |

## مرز فعال‌سازی

هیچ worker، cron، FastAPI startup، settings global یا dependency production در
P2-A تغییر نکرده است. `collect_public_market_telegram` فقط با فراخوانی صریح
و پس از دادن settings اجرا می‌شود. اگر `telethon` نصب نباشد، fail-closed با
`telethon_optional_dependency_not_installed` متوقف می‌شود.

credentialها فقط از environment عملیاتی خوانده می‌شوند و در repository یا
log وجود ندارند:

```text
COIN_MARKET_TELEGRAM_API_ID
COIN_MARKET_TELEGRAM_API_HASH
COIN_MARKET_TELEGRAM_PHONE
```

مسیر session و SQLite باید خارج از repository باشند، symlink مجاز نیست، و
session permission محدود می‌شود. انتخاب path نهایی روی volume، نصب dependency
و زمان‌بندی collector بعد از تکمیل P2 و review عملیاتی انجام می‌شود.

## normalization و retention

قیمت‌های داخلی این چهار channel که به تومان اعلام می‌شوند قبل از ورود به
Store به `IRT` تبدیل می‌شوند (`value × 10`). این conversion در parser صریح و
قابل‌آزمون است؛ اونس به USD دست‌نخورده می‌ماند. هیچ source به source دیگر
بازبرچسب‌گذاری نمی‌شود و تتر در این adapter اصلاً وجود ندارد.

متن پیام و public message ID فقط در حافظهٔ input وجود دارند. message ID صرفاً
برای checkpoint restart-safe نگهداری می‌شود و هرگز در `market_observations`
نمی‌آید. event key داخل fact یک digest opaque است. Adapter برای public source
raw staging پایدار ایجاد نمی‌کند.

## تست‌ها

تمام parser/ingest/transport testهای P2-A offline هستند. transport با یک
`telethon` جعلی و SQLite موقت اجرا می‌شود؛ هیچ credential یا درخواست شبکهٔ
واقعی استفاده نمی‌شود.
