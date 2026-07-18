# Stage 0B-1 — Modern Finance authentication scenarios

مرجع اصلی بازبینی `auth-scenarios.html` است. این prototype فقط سناریوهای ورود، دعوت، ثبت‌نام، recovery و تنظیم رمز را نمایش می‌دهد و هیچ کد runtime محصول نیست.

برای بازتولید تصاویر:

```bash
node docs/uiux-stage0b-auth/capture-prototype.cjs
```

خروجی‌ها در `assets/` قرار می‌گیرند. اسکریپت علاوه بر تصویر، ابعاد elementهای موبایل و PNGها، fit طبیعی و overflow در پنج عرض، بارگذاری واقعی فونت، حداقل عمومی ۴۴ پیکسل کنترل‌ها، حداقل مستقل ۴۸ پیکسل همه variantهای CTA، پیمایش مصنوعی focus، source audit تزئینات و ۱۴ جفت کنتراست با threshold متناسب را گزارش می‌کند؛ نمونه‌اقدام‌های غیرتعاملی atlas نیز جداگانه طبقه‌بندی می‌شوند.

PNGها و metrics ابتدا در staging sibling تولید و فقط پس از عبور همه assertionها یکجا promote می‌شوند. این evidence همچنان static-artifact است و runtime keyboard/WebView، screen reader، zoom/font scaling یا همه حالت‌های out-of-flow را اثبات نمی‌کند.

روش، اعداد و مرز ادعاها در `VALIDATION.md` و disposition بازبینی‌های خارجی در `EXTERNAL_REVIEW_FINDINGS.md` ثبت شده‌اند.

بازار و پیام‌رسان در این artifact حضور ندارند و بررسی نشده‌اند.
