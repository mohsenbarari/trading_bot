# Validation — WebApp UIUX Unification V3

قرارداد پذیرش این track مستقل است. ماتریس و رسید Stage 8 مرجع تاریخی
می‌مانند و در این فایل شمارش یا overwrite نمی‌شوند.

## گیت‌های اجباری پایانی

- full frontend Vitest
- `npm run build`
- `npx vue-tsc --noEmit`
- `npm run guard:ui`
- `git diff --check`
- آزمون‌های route/scope manifest
- ماتریس مرورگر روی production build محلی
- Chromium؛ Firefox؛ WebKit برای مسیرهای حساس
- accessibility scan
- keyboard matrix
- reduced motion
- زوم ۲۰۰٪
- overflow افقی صفر
- CTA نهایی پوشیده صفر
- خطای صفحه صفر
- unknown API صفر
- درخواست خارجی صفر
- source drift صفر
- backend/schema diff صفر
- regression حریم خصوصی یا دسترسی صفر
- مقایسه bundle با baseline همین شاخه
- audit ساختاری/بصری/حریم Figma در صورت دسترسی
- `memory-custodian check`

## محیط‌های برنامه

- مرورگر موبایل
- مرورگر دسکتاپ
- PWA نصب‌شده

Telegram Mini App در ماتریس نیست.

## پروفایل‌های دسترسی

guest، watch، member، police، customer، accountant، owner-context،
middle-admin، senior-admin

## Viewportها

360×740، 390×844، 430×932، 768×1024، 1440×900

## قاعدهٔ حکم

سناریوی اجرا نشده pass اعلام نمی‌شود. هر N/A باید دلیل صریح داشته باشد.
این برنامه owner-approved یا production-ready اعلام نمی‌کند.
