# P2-D — adapter تتر و IME

## مرز فعلی

این ماژول فقط normalization یک quote دریافت‌شده را انجام می‌دهد. هیچ HTTP
request، endpoint، API key، session، daemon یا schedule به پروژه اضافه نشده
است. Collector production آینده باید `observed_at_utc` واقعی بازار و
`available_at_utc` واقعی دریافت را جداگانه به adapter بدهد؛ زمان دریافت
هرگز جای زمان quote بازار نوشته نمی‌شود.

هر fact با opaque key از event identity گذرا ساخته می‌شود. URL، body پاسخ،
cookie، نام provider یا متن خطا به Market Store وارد نمی‌شوند.

## واحدها و تبدیل‌های قطعی

| ورودی | واحد ورودی | خروجی canonical | تبدیل |
| --- | --- | --- | --- |
| تتر | `TOMAN_PER_USDT` | `IRT_PER_USDT` | `price × 10` |
| گواهی شمش IME | `IRR_PER_CERTIFICATE_0_1G_995` | `IRT_PER_MESGHAL_750` | `(price ÷ 0.1) × (750 ÷ 995) × 4.3318` |
| سکه امام IME | `IRR_PER_COIN` | `IRT_PER_COIN` | identity |

بنابراین خروجی شمش IME از نظر عیار و واحد مستقیماً با آبشدهٔ مثقال ۷۵۰
قابل مقایسه است. خروجی سکه IME هرگز به هزار تومان یا تومان کاهش داده
نمی‌شود. کد legacy که در نام‌گذاری `IRT` و تومان ابهام داشت، به مسیر
محصولی منتقل نشده است.

## provenance و رفتار مدل

- تتر با instrument مستقل `USDT_IRT` ثبت می‌شود و هرگز به `USD_HERAT`
  تبدیل یا برچسب‌گذاری نمی‌شود.
- IME شمش (`IME_GOLD_BAR`) و IME سکه امام (`IME_GOLD_COIN_IMAM`) دو source
  reference جدا در Snapshot هستند.
- `BID` و `ASK` فقط side quotation هستند؛ دیگر quoteها `MID` هستند.
- زمان معکوس، quote kind نامعتبر، عدد صفر/منفی یا غیرfinite fail closed
  هستند.

## ماندۀ P2-D

انتخاب و health-check transport زنده، retry/rate-limit، historical backfill،
مسیر volume و انتقال امن داده از سرور ایران در این مرحله انجام نشده‌اند.
این ملاحظات فقط پس از تکمیل معماری سه‌سروره و با deployment review جدا وارد
runtime خواهند شد.
