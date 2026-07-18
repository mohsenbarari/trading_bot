# Stage 0B-1 — Modern Finance authentication scenarios

مرجع اصلی بازبینی `auth-scenarios.html` است. این prototype فقط سناریوهای ورود، دعوت، ثبت‌نام، recovery و تنظیم رمز را نمایش می‌دهد و هیچ کد runtime محصول نیست.

برای بازتولید تصاویر:

```bash
node docs/uiux-stage0b-auth/capture-prototype.cjs
```

خروجی‌ها در `assets/` قرار می‌گیرند. اسکریپت علاوه بر تصویر، ابعاد elementهای موبایل و PNGها، fit طبیعی و overflow در پنج عرض، بارگذاری واقعی فونت، کنتراست tokenهای scoped و اندازه همه کنترل‌های معنایی/قابل‌اقدام را گزارش می‌کند؛ نمونه‌اقدام‌های غیرتعاملی atlas نیز جداگانه طبقه‌بندی می‌شوند.

بازار و پیام‌رسان در این artifact حضور ندارند و بررسی نشده‌اند.
