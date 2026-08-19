# WebApp UI/UX Unification V3

این track مستقل از Stage 8 است. رسیدها، closure، acceptance authority و
visual freeze مرحلهٔ ۸ read-only می‌مانند و در این پوشه overwrite نمی‌شوند.

## هویت برنامه

- نام: WebApp UIUX Unification V3
- شاخه: `candidate/webapp-uiux-unification-v3`
- worktree: sibling مستقل خارج از repository اصلی
- مبنای کد: `origin/main` در لحظهٔ انزوا
- مرجع Figma موجود: fileKey `z8jgJxST4O2APzWnlyP9gv` (صفحه‌های تاریخی دست‌نخورده)
- صفحهٔ جدید پیشنهادی: `WebApp UIUX Unification V3 · DRAFT`
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

## فازها

0. اتصال، انزوا و خط مبنا
1. ممیزی جامع سطح و state (read-only محصول)
2. Figma و foundation سیستم طراحی
3. پروفایل و هویت
4. عملیات مشتری و حسابدار
5. خانه، Dashboard و Account
6. مدیریت
7. احراز، مسیرهای عمومی، recovery و share
8. Market و overlayهای معامله (هماهنگ‌سازی پوسته، نه بازطراحی A+C)
9. Messenger و سطوح مشترک (همگرایی بصری، بدون تغییر rollout)
10. shell، navigation و overlay مشترک
11. پاک‌سازی، پذیرش شاخه و گزارش ناظر

## گیت‌های بین‌فازی

هر فاز پس از عبور واقعی از gate همان فاز، commit مستقل می‌گیرد و فاز بعد
بدون تأیید مالک شروع می‌شود. توقف فقط برای مانع خارجی غیرقابل‌حل مجاز است.

## خروجی‌های این پوشه

- `SURFACE_INVENTORY.json`
- `STATE_MATRIX.json`
- `FIGMA_REFERENCES.json`
- `VALIDATION.md`
- `EXECUTION_REPORT.md`
