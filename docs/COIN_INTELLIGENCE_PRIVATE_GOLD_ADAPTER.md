# P2-B — adapter آفر/معاملهٔ آبشدهٔ خصوصی

## مرز فعلی

این adapter یک parser و projection **خالص و offline** است. ورودیِ موقت آن
می‌تواند متن و شناسهٔ خصوصی رویداد را داشته باشد، اما خروجی آن فقط
`MarketObservation`های privacy-minimized با opaque event key است.

این commit:

- Telegram client، listener، staging database، retention job یا scheduler را
  فعال نمی‌کند؛
- متن، نام فرستنده، شناسهٔ پیام یا لینک را در Market Store ذخیره نمی‌کند؛
- هیچ آفر یا معاملهٔ واقعی را نمی‌خواند.

لایهٔ production که raw کوتاه‌مدت سه‌روزه را نگه می‌دارد و سپس این adapter را
فراخوانی می‌کند، بعد از review lifecycle و deployment جدا اضافه خواهد شد.

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

برای complete شدن P2-B، worker/transport جدا باید raw private event را در
مسیر محافظت‌شده و خارج از checkout حداکثر سه روز نگه دارد، edit/verifier را
idempotent reconcile کند، ingestion order را metric کند و بعد فقط factهای
خروجی این adapter را در Market Store بنویسد. آن worker در این commit وجود
ندارد و بدون تأیید deployment فعال نخواهد شد.
