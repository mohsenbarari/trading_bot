# Project market outbox — P3

## هدف

هر تغییر اقتصادیِ واقعی در `Offer` و `Trade` باید در همان transaction
PostgreSQL یک event کوچک و idempotent بسازد. این کار جای callback حافظه‌ای
Shadow را می‌گیرد و تضمین می‌کند commit موفقِ محصول، ورودی قابل‌پردازش برای
Market Store دارد؛ بدون اینکه SQLite، inference یا هر شبکه‌ای بتواند ثبت
آفر/معامله را متوقف کند.

## رویدادها

| subject | event |
| --- | --- |
| Offer جدید | `OFFER_OPENED` |
| Offer فعال با تغییر مانده | `OFFER_PARTIAL` |
| Offer کامل‌شده | `OFFER_COMPLETED` |
| Offer لغوشده | `OFFER_CANCELLED` |
| Offer منقضی | `OFFER_EXPIRED` |
| Trade که به `COMPLETED` می‌رسد | `TRADE_COMPLETED` |

کلید idempotency از نوع subject، ID محلی، نوع event و optimistic version آن
ساخته می‌شود. payload فقط commodity، side، settlement، price، quantity،
status و eligibility را دارد. نام/شماره کاربر، notes، متن خام، ID تلگرام و
هر identity دیگری در table یا payload وجود ندارد.

## چرخهٔ اجرا

listener SQLAlchemy هنگام `after_flush` فقط outbox row را به همان session
اضافه می‌کند. در commit دومِ داخلی SQLAlchemy، آن row همراه با Offer/Trade
commit می‌شود. در rollback هیچ‌کدام پایدار نمی‌شوند. listener هیچ worker یا
side effect خارجی ندارد.

جدول `coin_intelligence_market_outbox` وضعیت، lease و attempts را از ابتدا
دارد تا consumer مستقل آینده بتواند آن را idempotent claim کند. consumer
و زمان‌بندی آن در P4 اضافه می‌شود؛ تا آن زمان هیچ startup task یا پردازشگر
خودکاری برای این table وجود ندارد.

## migration و rollback

migration `b2d4e6f8a0c2` یک table و دو index مستقل ایجاد می‌کند و هیچ جدول
Offer/Trade موجودی را تغییر نمی‌دهد. downgrade فقط وقتی ممکن است که outbox
خالی باشد؛ حذف silent رویدادهای commit‌شده مجاز نیست.

**ترتیب deploy الزامی:** ابتدا migration، سپس کد application. تا وقتی migration
اجرا نشده، نباید نسخهٔ listener در production اجرا شود.
