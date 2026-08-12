# Stage 7 — Motion & A11y

بستهٔ توضیحی mutable برای Phase 1 تحویل‌شده. freeze یا `EVIDENCE_MANIFEST.json` کل Stage 7 نیست.

## وضعیت

- branch: `condidate/webapp-ui-ux-redesign-v2`
- latest implementation: `ab0834aac3383e3c790c5865170ab9f007db235c`
- latest tree: `93e0c5cb0f8485a804b698d784cd3803a896081e`
- delivered: Phase 1 (copy محدود، keyboard Tabs/چیپ غیر بازار/تقویم، live region خالی، zoom ۲۰۰٪، reduced-motion)
- authority: `stage7CompleteAuthority=false`

هیچ Sites، staging، production یا merge در این کار آغاز نشده است.

## مرز تحویل‌شده

1. اطلاعات مجاز در سطح‌های V2 و routeهای غیرlegacy قابل انتخاب/کپی است؛ بازار، پیام‌رسان، کانال‌ها، share-receive، پیام‌های مدیریت و تنظیمات سیستم از این رفتار خارج‌اند.
2. Tabs فوکوس کیبورد را جابه‌جا می‌کند. FilterChips بازار رفتار قبلی را حفظ کرده است.
3. تقویم جلالی با کلید جهت فقط فوکوس را عوض می‌کند.
4. empty state یک live region آرام است؛ loading/error از قبل status/alert بودند.
5. zoom ۲۰۰٪ و متن بلند فارسی بدون overflow افقی در harness مصنوعی اثبات شد.
6. reduced-motion transition تب را به `0s` می‌رساند؛ قابلیت به انیمیشن وابسته نیست.

## Deferred

- حذف گستردهٔ CSS محلی منقضی؛
- تغییر ظاهر JalaliDatePicker / HelpPopover / CustomerNameWithBadge؛
- keyboard FilterChips داخل `/market`؛
- closure/freeze/Sites کل Stage 7.
