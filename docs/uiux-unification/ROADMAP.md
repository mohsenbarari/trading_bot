# WebApp UI/UX Unification V3

این track مستقل از Stage 8 است. رسیدها، closure، acceptance authority و
visual freeze مرحلهٔ ۸ read-only می‌مانند و در این پوشه overwrite نمی‌شوند.

## هویت برنامه

- نام: WebApp UIUX Unification V3
- شاخه: `candidate/webapp-uiux-unification-v3`
- worktree: `/root/trading-bot/webapp-uiux-unification-v3`
- مبنای کد: `origin/main` در لحظهٔ انزوا (`7f723de9`)
- همگام‌سازی اصلاحی: `origin/main` تا `2f8dd6e0` در شاخهٔ کاندید merge شد؛ `main` محلی دست‌نخورده ماند
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
| 0 اتصال، انزوا و خط مبنا | implementation-complete |
| 1 ممیزی جامع سطح و state | implementation-complete؛ ماتریس ۶۳۰تایی فقط declared/source-derived است و receipt اجرا نیست |
| 2 Figma و foundation سیستم طراحی | draft-complete؛ پذیرش محصول نیست |
| 3 پروفایل و هویت | implementation-complete؛ شناسهٔ عمومی نامعتبر fail-closed است |
| 4 عملیات مشتری و حسابدار | implementation-complete |
| 5 خانه، Dashboard و Account | implementation-complete |
| 6 مدیریت | disposition-complete؛ confirm تقویم به قرارداد محافظت‌شده برگشت |
| 7 احراز، recovery و share | disposition-complete؛ ShareReceive منجمد باقی ماند |
| 8 Market overlays | protected-frozen؛ A+C بازطراحی نشد |
| 9 Messenger | protected-frozen؛ restyle پوسته به‌خاطر freeze برگردانده شد |
| 10 shell و overlay مشترک | implementation-complete |
| 11 پاک‌سازی، پذیرش شاخه و گزارش ناظر | correction-gate-in-progress؛ نیازمند receipt تمیز نهایی |

## گیت‌های بین‌فازی

`implementation-complete` فقط پایان کار مجاز آن فاز است؛ معادل owner approval،
production-ready یا اجرای ضرب کامل ماتریس نیست. `protected-frozen` نیز یعنی قرارداد
محافظت‌شده عمداً بدون restyle نگه داشته شده است، نه اینکه طراحی داخلی آن توسط V3
بازپذیرفته شده باشد.

موجودی نهایی ۴۵ سطح را به‌صورت صریح تعیین تکلیف می‌کند: ۳۸ سطح هم‌راستا، ۶ سطح
محافظت‌شده و منجمد، و یک سطح legacy غیرزنده. وضعیت‌های مبهم `partial`،
`inconsistent` و `unknown` در ستون نهایی مجاز نیستند؛ وضعیت ممیزی اولیه در
`baseline_status` حفظ می‌شود.

## خروجی‌های این پوشه

- `SURFACE_INVENTORY.json`
- `STATE_MATRIX.json`
- `FIGMA_REFERENCES.json`
- `VALIDATION.md`
- `EXECUTION_REPORT.md`
