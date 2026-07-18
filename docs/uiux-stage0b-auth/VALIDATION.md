# Stage 0B-1 validation evidence

تاریخ: ۲۰۲۶-۰۷-۱۸

دامنه: artifactهای design-only ورود، دعوت، ثبت‌نام، recovery و تنظیم رمز؛ بدون تغییر runtime محصول

## Prototype render

دستور:

```bash
node docs/uiux-stage0b-auth/capture-prototype.cjs
```

نتیجه:

- ۱۱ قاب موبایل با اندازه دقیق `390 × 844` تولید شد.
- هیچ قاب در عرض‌های `360`، `375`، `390`، `414` و `430` پیکسل horizontal overflow نداشت.
- ۳۲ هدف لمسی علامت‌گذاری‌شده بررسی شد؛ کمینه اندازه `74 × 44` پیکسل و تعداد اهداف زیر ۴۴ پیکسل صفر بود.
- فونت محاسبه‌شده `Vazirmatn, Tahoma, Arial, sans-serif` بود.
- چهار نمای کیبورد باز، state atlas و نمونه adaptive دسکتاپ با همان render تولید شدند.
- گزارش machine-readable در `assets/stage0b-auth-validation-metrics.json` قرار دارد.

## Existing behavioral contract tests

دستور:

```bash
cd frontend
npm run test:unit:run -- src/views/LoginView.test.ts src/views/InviteLanding.test.ts src/views/WebRegister.test.ts src/views/SetupPassword.test.ts --reporter=junit --outputFile=/tmp/stage0b-auth-unit-tests.xml
```

نتیجه: `48 passed`, `0 failures`, `0 errors` در چهار suite.

فایل خام JUnit خارج از Git است و در بسته بازبینی ChatGPT قرار می‌گیرد.

## Review boundary

- Full matrix اجرا نشد، زیرا این checkpoint فقط فایل‌های مستندات، HTML محلی و تصاویر بازبینی را تغییر می‌دهد و هیچ کد محصول یا test runtime تغییر نکرده است.
- تصاویر با بازبینی بصری کنترل شدند: hierarchy، RTL، متن فارسی، progress الزامی/اختیاری، privacy دعوت، OTP ورود/ثبت‌نام، eligibility recovery و چیدمان adaptive دسکتاپ.
- این شواهد صحت رفتار runtime جدید را ادعا نمی‌کنند؛ هنوز runtime جدیدی وجود ندارد.
