# Stage 7 — پویایی، دسترس‌پذیری و polish

تاریخ آغاز: ۲۰۲۶-۰۸-۱۳

وضعیت: **`stage7_complete`**

`stage7CompleteAuthority=true`

frontend runtime freeze: `3e62accdd157bed5dc6f2ed974e56e07c7349910`

مرحلهٔ بعدی مجاز: Stage 8، فقط پس از merge روی integration commit. هیچ slice تازهٔ Stage 8 اجرا نشده است.

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## ۱. مجوز و حد آن

دستور مالک برای ادامهٔ بدون توقف تا پایان Stage 8، شروع Stage 7 را مجاز کرد. این مجوز اجازهٔ merge، staging، production، Sites، یا overwrite evidence Stage 4/5/6 را نمی‌دهد.

`stage7CompleteAuthority=true`. Phase 1 و اصلاح shared dependency تاریخی می‌مانند. حذف گستردهٔ CSS مرده و یکدست‌سازی microcopy با evidence به `PASS_NO_PATCH` reconcile شدند، چون حذف امن یا ناسازگاری فهم/دسترس‌پذیری پیدا نشد.

## ۲. Phase 1 — copy، keyboard، live region، zoom، reduced-motion

- انتخاب متن فقط با کلاس `app-copyable-info` روی پوستهٔ غیرlegacy و با `user-select: text` داخل scope V2؛ `html, body { user-select: none }` برای بازار و پیام‌رسان باقی ماند.
- `/admin/messages` و `/admin/system` از copyable خارج شدند تا interior محافظت‌شده عوض نشود.
- `AppTabs` فوکوس کیبورد را مثل FilterChips منتقل می‌کند. FilterChips بازار opt-in نشده است.
- FilterChips پروفایل عمومی و workspace مشتری/حسابدار `focus-selection-on-keyboard` دارند.
- تقویم جلالی به‌صورت پیش‌فرض inert است؛ فقط UserProfile/PublicProfile صریحاً کلیدهای جهت را برای جابه‌جایی فوکوس فعال می‌کنند و TradingSettings محافظت‌شده opt-in نمی‌شود.
- `AppEmptyState` بدون role پیش‌فرض است؛ call-siteهای مجاز `status` می‌گیرند و مصرف‌کننده‌های Market/CreateChannel محافظت‌شده inert می‌مانند.
- متن بلند با `overflow-wrap: anywhere` روی عنوان/توضیح/لیست/کارت.
- reduced-motion با marker متصل به vnode فقط برای SECTIONهای غیرمحافظت‌شده قطع می‌شود؛ fade مسیرهای full/mixed، از جمله هنگام عبور دوطرفه، رفتار قبلی را حفظ می‌کند.
- guard Stage 4 وابستگی‌های shared را هم بدون rebase کردن hashهای محافظت‌شده fail-closed بررسی می‌کند.

فایل‌های اصلی: `App.vue`، `main.css`، `design-system-v2.components.css`، `AppTabs.vue`، `AppEmptyState.vue`، `JalaliDatePicker.vue`، `PublicProfile.vue`، `CustomerWorkspaceView.vue`، `AccountantWorkspaceView.vue`.

## ۳. گیت Phase 1

```text
App.test.ts + AppPrimitives.test.ts + JalaliDatePicker.test.ts + designSystemV2.test.ts
PublicProfile / Customer / Accountant focused
guard:ui (بازار و پیام‌رسان بدون drift)
browser harness copy/keyboard/zoom/reduced-motion
Figma page 09 sibling، نه overwrite صفحهٔ Stage 6
```

## ۴. Figma و Sites

صفحهٔ `09 — Stage 7 Motion & A11y` (`486:1455`) artifact تاریخی `487:18` را حفظ می‌کند. مرجع جدید design-system-bound با provenance `source 82cb016e` در section `496:18` ساخته شد: W1=`497:18`، W2=`499:49` و panel=`501:93`. این مرجع live/editable و non-freeze است. Sites ساخته نشده است.

## ۵. رسید

- source: `ab0834aac3383e3c790c5865170ab9f007db235c` / tree `93e0c5cb0f8485a804b698d784cd3803a896081e`
- browser: `uiux-stage7-phase1-motion-a11y-20260812T224044165Z` passed/promotable، ۷ assertion / ۵ screenshot
- Figma: page `486:1455`، section `487:18`

### رسید اصلاح shared dependency

- source: `82cb016e57e676c211d746ae852a6600d8d3b6fa` / tree `db65232c7835440868773c8fdbbf032b7bdfd890`
- browser: `uiux-stage7-shared-dependency-correction-20260813T072432779Z` passed/promotable، ۲۴ assertion / ۹ screenshot / ۳ viewport
- protected/full/mixed enter+leave=`200ms`؛ SECTION مجاز under reduce enter+leave=`0ms`
- Figma live: section `496:18`، ۵۰ text همگی Vazirmatn، ۹ instance، ۱۰۳ node variable-bound، unsafe scan صفر و overflow صفر
- هیچ closure/freeze/Sites/staging/production/merge ادعا یا اجرا نشده است

## ۶. بستن نهایی و follow-up مرز عبور

Stage 7 روی frontend runtime `3e62accd` بسته است و **`stage7CompleteAuthority=true`** فقط پس از PASS همین follow-up باقی می‌ماند.

- run تاریخی `stage7-closure-20260814084158579` برابر `60/60` است و چهار request failure طبقه‌بندی‌نشده در چهار سناریوی cross-boundary دارد.
- آن report و harness تاریخی تغییر نکرده‌اند: report `5b842a08c93cbe31966b446510185276c46d38bf7be85db19322e3c254722cc9` و harness `3567e564fa23df27254ea87f69889686a5d422b13f84bd67b88d099606b1fb18`.
- follow-up مستقل `stage7-xboundary-20260814093542875` همان چهار جهت را در `390×844`، `1440×900` و `390×844` با `prefers-reduced-motion` با کنترل واقعی محصول اجرا کرد: `12/12` پاس، request failure صفر، cancellation صفر.
- علت تاریخی: harness قبلی برای hop دوم از `page.goto` استفاده می‌کرد و abortهای in-flight را بدون طبقه‌بندی ثبت می‌کرد. با transition واقعی SPA، failure صفر شد و defect محصول پیدا نشد.
- این follow-up slice مرحله ۸ نیست و شمارش `270 / 0 / 12 / 163` را تغییر نمی‌دهد.
- Receipt: `docs/uiux-stage7-motion-a11y/STAGE7_CROSS_BOUNDARY_DIAGNOSTIC_RECEIPT.json`.
