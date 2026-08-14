# Stage 7 — Motion & A11y

بستهٔ Stage 7 پس از بستن Stage 6. Phase 1 و اصلاح shared dependency تاریخی می‌مانند.

## وضعیت

- branch: `condidate/webapp-ui-ux-redesign-v2`
- frontend runtime freeze: `3e62accdd157bed5dc6f2ed974e56e07c7349910`
- status: `stage7_complete`
- authority: `stage7CompleteAuthority=true`
- in-scope deferred: صفر
- next authorized stage: Stage 8، فقط پس از merge روی integration commit
- Sites Stage 7 در محدوده این مرحله نیست
- follow-up مرز عبور: run تاریخی ۶۰/۶۰ چهار request failure طبقه‌بندی‌نشده داشت؛ report تاریخی تغییر نکرد؛ follow-up مستقل ۱۲/۱۲ با صفر request failure علت را مشخص کرد و defect محصول پیدا نشد

مرجع ماشین‌خوان: [STAGE7_CLOSURE_LEDGER.json](STAGE7_CLOSURE_LEDGER.json)، [STAGE7_CLOSURE_EVIDENCE_MANIFEST.json](STAGE7_CLOSURE_EVIDENCE_MANIFEST.json) و [STAGE7_CROSS_BOUNDARY_DIAGNOSTIC_RECEIPT.json](STAGE7_CROSS_BOUNDARY_DIAGNOSTIC_RECEIPT.json).

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

## Deferred reconcile

- CSS مرده: هیچ `safe dead candidate` نبود؛ `WorkspaceShell` هنوز سه مصرف‌کنندهٔ زنده دارد (`PASS_NO_PATCH`).
- ظاهر Jalali/HelpPopover/CustomerNameWithBadge فقط سلیقه‌ای بود و patch نشد.
- keyboard FilterChips بازار محافظت‌شده و خارج از محدوده است.
- Figma تاریخی `487:18` و `496:18` کافی بود؛ section تازه ساخته نشد چون runtime gap نبود.
- Sites Stage 7 در محدوده این مرحله نیست.
- follow-up مرز عبور یک slice مرحله ۸ نیست و شمارش مرحله ۸ را افزایش نمی‌دهد.
