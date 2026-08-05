# P2-B — adapter آفر/معاملهٔ آبشدهٔ خصوصی

## مرز فعلی

این adapter یک parser و projection **خالص و offline** است. ورودیِ موقت آن
می‌تواند متن و شناسهٔ خصوصی رویداد را داشته باشد، اما خروجی آن فقط
`MarketObservation`های privacy-minimized با opaque event key است.

در این مرحله:

- کتابخانهٔ raw staging سه‌روزه، با مسیر اجباری خارج از checkout، برای ادغام
  idempotent آفر و verifier اضافه شده است؛
- متن، نام فرستنده، شناسهٔ پیام یا لینک را در Market Store ذخیره نمی‌کند؛
- هیچ آفر یا معاملهٔ واقعی را نمی‌خواند.

`private_gold_staging` می‌تواند update معامله را—even اگر زودتر از متن آفر
برسد—موقتاً با `source_message_id` نگه دارد. تا وقتی متن آفر نرسیده، هیچ
factی به Market Store نوشته نمی‌شود. پس از رسیدن آفر، fact آفر با زمان
دریافت خود آفر و fact معامله با زمان دریافت verifier جداگانه ساخته می‌شوند؛
بنابراین update دیررس نمی‌تواند زمانِ در دسترس‌بودن آفر را تغییر دهد.

Telegram client، listener، runtime path، retention job، scheduler و ingest
واقعی هنوز فعال یا ساخته نشده‌اند. لایهٔ production که این کتابخانه را صدا
می‌زند، پس از review lifecycle و deployment جدا اضافه خواهد شد.

## قواعد قطعی بازار

| نشانهٔ متن | `trade_form` | settlement | نوع کاغذی |
| --- | --- | --- | --- |
| `با حواله` و بدون `روز` | `PAPER_NORMAL/REVERSE/SWIM` | `TOMORROW` | از متن |
| `با حواله` و `روز` | `PAPER_NORMAL/REVERSE/SWIM` | `TODAY` | از متن |
| `روز` بدون نشانۀ فیزیکی | `PAPER_NORMAL/REVERSE/SWIM` | `TODAY` | از متن |
| `فردا` بدون نشانۀ فیزیکی | `PAPER_NORMAL/REVERSE/SWIM` | `TOMORROW` | از متن |
| `نقد حاضر` | `PHYSICAL` | `TODAY` | ندارد |
| `بی حواله` / `بدون حواله` | `PHYSICAL` | `TOMORROW` | ندارد |
| متن بدون نشانۀ معتبر بازار | — | — | abstain/ignore |

«روز» در `فروشروز` نیز تشخیص داده می‌شود. هیچ متن بدون marker به شکل پنهان
فیزیکال فرض نمی‌شود.

عدد کانال در **تومان** دریافت و به‌صورت صریح `×10` به واحد canonical
`IRT_PER_MESGHAL_750` تبدیل می‌شود. price یا quantity یا side نامشخص، خروجی
ندارد؛ در نتیجه عدد یا سمت ساختگی وارد مدل نمی‌شود.

## معامله و شرط

- offer خامِ parse‌شده همیشه fact مستقل خود را دارد.
- edit این source evidence معامله است؛ زمان edit بر timestamp verifier
دیررس مقدم است.
- معاملهٔ `FULL` با quantity نامشخص از quantity آفر استفاده می‌کند؛ اما
`PARTIAL` بدون quantity هیچ معاملهٔ کامل ساختگی ایجاد نمی‌کند.
- edit بدون نتیجهٔ صریحِ مخالف نیز طبق convention همین source، معاملهٔ کامل
  به اندازهٔ quantity آفر است؛ `NONE` صریح بر edit مقدم است.
- آفر شرطی (فیش، مهلت، چک، شرط صریح یا توضیحات آزاد) در fact store حفظ و با
`is_conditional=true` برچسب می‌خورد، ولی از minute quote عادی و مرجع قیمت
فیزیکال مستقیم کنار گذاشته می‌شود. policy آینده می‌تواند آن را فقط پس از
market-comparability gate برای آموزش بررسی کند.

## factهای منفرد و minute quote

فیزیکال‌های غیرشرطی aggregate نمی‌شوند: هر offer و trade مستقل ثبت می‌شود.

برای هر دقیقهٔ کاغذی و هر cell مستقلِ `(TODAY|TOMORROW,
NORMAL|REVERSE|SWIM)`، `refresh_private_gold_paper_minute` یک quote derived
می‌سازد. میانگین وزن‌دار آن این است:

```text
(sum(offer_price × 1) + sum(confirmed_trade_price × 3))
÷ (offer_count + 3 × confirmed_trade_count)
```

minute quote فقط در همان cell و فقط از raw factهای non-conditional خوانده
می‌شود. identifierهای input، متن و نام در quote وجود ندارند. Snapshot نیز
physical امروز/فردا و تمام six paper cellها را جدا نگه می‌دارد؛ variantها با
هم merge نمی‌شوند. quote پیش از بسته‌شدن دقیقه اصلاً منتشر نمی‌شود؛ به‌جای
ثبت timestamp آینده، adapter fail closed می‌کند تا replay تاریخی دچار نشت
دادهٔ آینده نشود.

## ماندۀ P2-B

کتابخانهٔ staging اکنون retention سه‌روزه، ادغام idempotent و promotion بدون
متن خام را دارد. decoder آفلاین نیز envelope نسخهٔ `1.0` را فقط وقتی
می‌پذیرد که market/source/channel role آن با stream مورد اعتماد collector
سازگار باشد؛ offer و verifier در دو stream جدا هستند و batchهای JSON نیز
بدون حدس پردازش می‌شوند.

verifier با نتیجهٔ صریح `no_trade` ممکن است هیچ شیء `trade` نداشته باشد. این
حالت به `NONE` تبدیل می‌شود؛ پس edit قبلی یا دیررس نمی‌تواند به‌اشتباه معامله
بسازد. verifierهایی که واقعاً معامله دارند باید `result=traded` و شیء trade
سازگار داشته باشند.

برای complete شدن P2-B، worker/transport جدا باید channel بیرونی را به stream
مورد اعتماد bind کند، decoder و runtime path محافظت‌شده را صدا بزند، ترتیب
ingestion را metric کند و `private_gold_pipeline` را با commit/retry کنترل‌شده
اجرا کند. pipeline فقط minute کاغذیِ بسته‌شده و non-conditional را با وزن
offer=`1` و trade=`3` materialize می‌کند؛ physical aggregate نمی‌شود. worker
در این commit وجود ندارد و بدون تأیید deployment فعال نخواهد شد.

برای evidence دستی، `scripts/ingest_private_gold_event_spool.py` فقط فایل‌های
JSONL موجود در runtime root را می‌پذیرد. فایل offer و trade جدا معرفی می‌شود؛
هر line باید `published_at_utc` و `payload_text` داشته باشد. staging پیش از
Market Store commit می‌شود تا failure بعدی با replay امن جبران شود. این command
نه Telegram را می‌خواند، نه spool را حذف می‌کند و نه به‌صورت خودکار اجرا
می‌شود. `published_at_utc` فقط برای اعتبارسنجی metadata است؛ availability
واقعی را زمان دریافت محافظه‌کارانهٔ runner تعیین می‌کند تا backfill با clock
یا ترتیب انتشار ناسازگار موجب حذف داده یا نشت زمانی نشود.
