# Stage 7 — پویایی، دسترس‌پذیری و polish

تاریخ آغاز: ۲۰۲۶-۰۸-۱۳

وضعیت: **`stage7_phase1_delivered_broader_dead_css_deferred`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## ۱. مجوز و حد آن

دستور مالک برای ادامهٔ بدون توقف تا پایان Stage 8، شروع Stage 7 را مجاز کرد. این مجوز اجازهٔ merge، staging، production، Sites، یا overwrite evidence Stage 4/5/6 را نمی‌دهد.

`stage7CompleteAuthority=false`. Phase 1 گیت فنی copy/keyboard/live-region/zoom/reduced-motion را برای سطح‌های غیرمحافظت‌شده می‌بندد؛ حذف CSS مرده و یکدست‌سازی microcopy باقی‌مانده deferred است.

## ۲. Phase 1 — copy، keyboard، live region، zoom، reduced-motion

- انتخاب متن فقط با کلاس `app-copyable-info` روی پوستهٔ غیرlegacy و با `user-select: text` داخل scope V2؛ `html, body { user-select: none }` برای بازار و پیام‌رسان باقی ماند.
- `/admin/messages` و `/admin/system` از copyable خارج شدند تا interior محافظت‌شده عوض نشود.
- `AppTabs` فوکوس کیبورد را مثل FilterChips منتقل می‌کند. FilterChips بازار opt-in نشده است.
- FilterChips پروفایل عمومی و workspace مشتری/حسابدار `focus-selection-on-keyboard` دارند.
- تقویم جلالی با کلیدهای جهت فوکوس روز را جابه‌جا می‌کند و مقدار را عوض نمی‌کند؛ CSS تقویم دست نخورده است.
- `AppEmptyState` برابر `role="status"` است.
- متن بلند با `overflow-wrap: anywhere` روی عنوان/توضیح/لیست/کارت.
- fade و transition تب‌ها با `prefers-reduced-motion: reduce` قطع می‌شوند.

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

صفحهٔ تازه `09 — Stage 7 Motion & A11y` (`486:1455`). section `487:18`. W1=`487:20`، W2=`487:39`. Sites ساخته نشده است.

## ۵. رسید

- source: `ab0834aac3383e3c790c5865170ab9f007db235c` / tree `93e0c5cb0f8485a804b698d784cd3803a896081e`
- browser: `uiux-stage7-phase1-motion-a11y-20260812T224044165Z` passed/promotable، ۷ assertion / ۵ screenshot
- Figma: page `486:1455`، section `487:18`
