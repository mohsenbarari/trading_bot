# WebApp UI/UX Unification V3

این track مستقل از Stage 8 است. رسیدها، closure، acceptance authority و
visual freeze مرحلهٔ ۸ read-only می‌مانند و در این پوشه overwrite نمی‌شوند.

## هویت برنامه

- نام: WebApp UIUX Unification V3
- شاخه: `candidate/webapp-uiux-unification-v3`
- worktree: `/root/trading-bot/webapp-uiux-unification-v3`
- مبنای کد: `origin/main` در لحظهٔ انزوا (`7f723de9`)
- مرجع Figma موجود: fileKey `z8jgJxST4O2APzWnlyP9gv` (صفحه‌های تاریخی دست‌نخورده)
- صفحهٔ جدید: `WebApp UIUX Unification V3 · DRAFT` (`663:398`)
- Sites، staging، production و merge به `main` خارج از مجوز این برنامه است

## هدف بصری و UX

- مدرن مالی، خلوت و هدفمند
- فارسی و RTL-first با Vazirmatn
- سلسله‌مراتب روشن، حداقل شلوغی، action قابل‌تشخیص
- تراکم مناسب موبایل و دسکتاپ
- WCAG 2.2 AA
- حالت‌های منسجم loading / empty / error / retry / slow / offline / stale
- navigation و back behavior واحد
- بدون overflow افقی و بدون CTA دفن‌شده
- بدون تغییر ناخواسته در privacy، access policy یا منطق محصول

## مرز نسبت به Stage 8

- Stage 8 بسته و owner-aesthetic برای همان مرحله است؛ این برنامه آن را بازنویسی نمی‌کند.
- Market A+C و Messenger M01–M14 مرجع رفتار باقی می‌مانند.
- مسیر legacy پیام‌رسان و rollout پیش‌فرض آن حذف یا عوض نمی‌شود.
- Telegram Mini App منسوخ است و در ماتریس این برنامه نیست.
- فایل‌های hash-frozen پیام‌رسان (از جمله ShareReceive) و confirm تقویم TradingSettings قابل restyle نیستند.

## فازها

| فاز | وضعیت |
|---|---|
| 0 اتصال، انزوا و خط مبنا | done |
| 1 ممیزی جامع سطح و state | done |
| 2 Figma و foundation سیستم طراحی | done |
| 3 پروفایل و هویت | done |
| 4 عملیات مشتری و حسابدار | done |
| 5 خانه، Dashboard و Account | done |
| 6 مدیریت | done؛ confirm تقویم به disposition Stage 6 برگشت |
| 7 احراز، recovery و share | done؛ ShareReceive به فایل منجمد برگشت |
| 8 Market overlays | done؛ A+C بازطراحی نشد |
| 9 Messenger | done؛ restyle پوسته به‌خاطر freeze برگردانده شد |
| 10 shell و overlay مشترک | done |
| 11 پاک‌سازی، پذیرش شاخه و گزارش ناظر | done |

## گیت‌های بین‌فازی

هر فاز پس از عبور واقعی از gate همان فاز، commit مستقل گرفته است.
توقف فقط برای مانع خارجی غیرقابل‌حل مجاز بود. Figma در دسترس بود.

## خروجی‌های این پوشه

- `SURFACE_INVENTORY.json`
- `STATE_MATRIX.json`
- `FIGMA_REFERENCES.json`
- `VALIDATION.md`
- `EXECUTION_REPORT.md`
