# گزارش نهایی Native App Standardization V1

## حکم

`READY FOR INDEPENDENT NATIVE UI REVIEW`

این حکم فقط برای بازبینی مستقل شاخه است. تأیید مالک، merge، push، staging،
production یا Sites را اعلام یا مجاز نمی‌کند.

## نتیجهٔ محصول

- هر ۲۹ مسیر غیر Market و همهٔ سطح‌های زندهٔ آن‌ها به زبان مشترک native
  نزدیک شده‌اند: کنترل ۴۸px، فهرست inset/grouped، safe-area، focus مرئی، RTL
  و Vazirmatn در scopeهای مجاز.
- پیام‌رسان از پوسته و فهرست تا اتاق، composer، رسانه، context menu، جستجو،
  viewer، مدیریت اتاق و ShareReceive یکپارچه شد. قراردادهای M01–M14 و rollout
  `legacy` تغییر نکردند.
- کنترل‌های mouse-only یا بدون نام به عنصر معنایی و keyboard-accessible تبدیل
  شدند؛ nested interactiveهای banner/media حذف شدند و focus پس از Escape به
  trigger برمی‌گردد.
- آخرین کنترل مسیرهای V2 فضای پایانی مستقل از Market دارد و ماتریس عرض مرزی
  پس از سه نمونهٔ layout پایدار اندازه‌گیری می‌شود.

## قفل Market

Market به حالت پذیرفته‌شدهٔ فعلی `main` برگشت و در این track بازطراحی نشد.

- فایل‌های مستقیم Market با `main` اختلاف ندارند.
- رنگ fallback پنجرهٔ تشخیص کالا دقیقاً به main برگشت.
- shell و primitiveهای مشترک فقط زیر کلاس صریح Market هندسهٔ قبلی را حفظ می‌کنند.
- تصاویر build تولیدی main و candidate در ۳۹۰ و ۱۴۴۰ SHA-256 یکسان دارند.
- guard Market روی اثرانگشت پذیرفته‌شدهٔ ۲۰ فایل سبز است.

## شواهد مرورگر

اجرای clean-bound روی commit محصول `01bf5ba0`:

- ۳۰ مسیر، ۱۶۷ سناریوی یکتا، ۱۶۷ پاس، صفر شکست
- Chromium ۱۴۳، Firefox ۱۲، WebKit ۱۲
- normal ۱۱۹، loading ۱۹، empty ۹، error ۲۰
- keyboard ۵، reduced-motion ۳، zoom 200% شش، PWA سه
- صفر overflow سند/اپ، CTA پوشیده، کنترل بی‌نام یا تو‌در‌تو
- صفر page error، API ناشناخته، mutation، درخواست خارجی یا false-positive

اجرای مکمل پیام‌رسان: ۱۳ پاس روی سه موتور؛ دو skip فقط برای CDP zoom در
Firefox/WebKit. اجرای مکمل viewport: هشت عرض از ۳۶۰ تا ۱۴۴۰، هشت پاس.

## گیت کد

- Vitest کامل: ۱۶۹ فایل، ۱۹۶۲ تست، همه پاس
- `vue-tsc --noEmit`: پاس
- build تولیدی: ۲۲۰۸ module، ۱۷۱ فایل خروجی، پاس
- `guard:ui`: پاس، شامل قفل Market، Messenger، AdminMessages و TradingSettings
- `git diff --check`: پاس

warningهای باقی‌مانده فقط diagnosticهای شناخته‌شدهٔ تست (APIهای mock، JSDOM
media/canvas، Browserslist قدیمی و chunk advisory) هستند و assertion را تضعیف
نکردند.

## فایل‌های شواهد

- `SURFACE_INVENTORY.json`: طبقه‌بندی تمام ۳۰ مسیر
- `MESSENGER_SURFACE_MAP.json`: نگاشت M01–M14
- `EXCLUSIONS.md`: مرز Market و رفتارهای منجمد
- `FINAL_REVIEW_RECEIPT.json`: اتصال commit/tree/hash/count بدون artifact خام

تصاویر و گزارش خام مرورگر خارج مخزن ماندند؛ فقط digest و شمارش redacted ثبت شد.
