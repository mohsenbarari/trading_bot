# Stage 7 — Motion & A11y

بستهٔ توضیحی mutable برای Phase 1 و اصلاح ایزولیشن shared dependency است. freeze یا
`EVIDENCE_MANIFEST.json` کل Stage 7 نیست.

## وضعیت

- branch: `condidate/webapp-ui-ux-redesign-v2`
- historical Phase 1 implementation: `ab0834aac3383e3c790c5865170ab9f007db235c`
- latest shared-dependency correction: `82cb016e57e676c211d746ae852a6600d8d3b6fa`
- latest correction tree: `db65232c7835440868773c8fdbbf032b7bdfd890`
- delivered: Phase 1 + correction (copy محدود، keyboard Tabs/چیپ غیر بازار، تقویم opt-in، semantic empty-state opt-in، zoom ۲۰۰٪ و reduced-motion per-route)
- authority: `stage7CompleteAuthority=false`

هیچ Sites، staging، production یا merge در این کار آغاز نشده است.

## مرز تحویل‌شده

1. اطلاعات مجاز در سطح‌های V2 و routeهای غیرlegacy قابل انتخاب/کپی است؛ بازار، پیام‌رسان، کانال‌ها، share-receive، پیام‌های مدیریت و تنظیمات سیستم از این رفتار خارج‌اند.
2. Tabs فوکوس کیبورد را جابه‌جا می‌کند. FilterChips بازار رفتار قبلی را حفظ کرده است.
3. تقویم جلالی به‌صورت پیش‌فرض inert است؛ فقط UserProfile/PublicProfile صریحاً navigation کیبورد را فعال می‌کنند و TradingSettings محافظت‌شده رفتار قبلی را حفظ می‌کند.
4. `AppEmptyState` به‌صورت پیش‌فرض role ندارد؛ سطح‌های مجاز صریحاً `status` می‌گیرند، Market/CreateChannel محافظت‌شده inert می‌مانند و error همچنان `alert` است.
5. zoom ۲۰۰٪ و متن بلند فارسی بدون overflow افقی در harness مصنوعی اثبات شد.
6. reduced-motion فقط روی vnode مسیرهای `protection:none` + `v2Scope:section` transition را به `0ms` می‌رساند؛ Home mixed و Market/Messenger/AdminMessages/TradingSettings روی fade قبلی `200ms` می‌مانند.

## رسید اصلاح

- browser run: `uiux-stage7-shared-dependency-correction-20260813T072432779Z`
- ۲۴ assertion، ۳ viewport و ۹ screenshot؛ source/Git/harness/environment قبل و بعد یکسان
- Figma live/editable: page `486:1455`، section جدید `496:18` با دو viewport واقعی `497:18` و `499:49`؛ artifact تاریخی `487:18` حفظ شده است
- این Figma design-system-bound است، اما به‌تنهایی acceptance یا freeze نیست

## Deferred

- حذف گستردهٔ CSS محلی منقضی؛
- تغییر ظاهر JalaliDatePicker / HelpPopover / CustomerNameWithBadge؛
- keyboard FilterChips داخل `/market`؛
- closure/freeze/Sites کل Stage 7.
