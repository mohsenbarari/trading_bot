# Stage 6 — Admin & Profile

این پوشه بستهٔ Stage 6 است. Phaseهای ۱ تا ۱۹ تاریخی می‌مانند؛ بستن نهایی روی runtime `3e62accd` ثبت شده است.

## وضعیت دقیق

- branch: `condidate/webapp-ui-ux-redesign-v2`
- runtime freeze: `3e62accdd157bed5dc6f2ed974e56e07c7349910`
- runtime tree: `3f4a186e46b12aee326c699cd1975ba34e485be7`
- status: `stage6_complete`
- authority: `stage6CompleteAuthority=true`
- in-scope deferred: صفر
- next authorized stage: Stage 7
- Sites: non-requirement صریح؛ اجرا نشده است

مرجع ماشین‌خوان: [STAGE6_CLOSURE_LEDGER.json](STAGE6_CLOSURE_LEDGER.json) و [STAGE6_CLOSURE_EVIDENCE_MANIFEST.json](STAGE6_CLOSURE_EVIDENCE_MANIFEST.json).

## مرز تحویل‌شده

1. ورودی مدیریت با مقصدهای واقعی و role-aware، بدون count/pending ساختگی.
2. directory مدیریت با جست‌وجوی session-local و detail server-authorized؛ تنها `scroll` معتبر route context است.
3. profile عمومی با projection server-authoritative: peer عادی فقط mobile masked می‌بیند و address/presence/membership/relation/trade detail دریافت نمی‌کند؛ self و administratorِ مجاز فقط دادهٔ موردنیازِ مجاز را می‌گیرند.
4. actionهای حساسِ self/same-level در backend read-only/forbidden هستند؛ visibility یا disabled UI جای enforcement نیست.
5. همهٔ ورودهای public profile به `/users/:id` canonical می‌شوند؛ query تاریخی پیش از navigation حذف می‌شود. Messenger/Forward discovery بازطراحی یا محدود نشده است.
6. invitation فقط copy-only/in-memory است؛ `204` حذف را receipt می‌کند، `400/404` reconcile می‌شوند و `403` state حساس/copy را پاک می‌کند.
7. block/unblock عمومی و حذف حساب workspace، native confirm/alert ندارند: cancel/Escape mutation نمی‌دهد و فقط receipt معتبرِ همان contract state یا navigation را تغییر می‌دهد.
8. حذف حساب workspace در دو route فعال Customer/Accountant با dialog Teleport‌شده به `body`، تایپ نام synthetic/نمایشی و acknowledgement انجام می‌شود؛ خطای 400/403/404/malformed/network فقط متن امن ثابت می‌دهد و relation/route را حفظ می‌کند.
9. پایان یک نشست workspace در همان دو route زنده با `AppConfirmDialog` انجام می‌شود؛ فقط receipt با شناسهٔ دقیق نشست state محلی را تغییر می‌دهد و 400/403/404/malformed/network فقط پیام ثابت امن می‌دهند، بدون raw detail و بدون تغییر route/relation/session نمایش‌داده‌شده.
10. چهار mutation رابطهٔ workspace فقط با receipt دقیق همان relation (`revoked` یا `deleted`) اعمال می‌شوند؛ 400/403/404/wrong-id/wrong-status/malformed/network dialog، relation، route و query را نگه می‌دارند و raw detail/message را نمایش یا serialize نمی‌کنند.
11. `/admin/commodities` برای create/edit کالا و alias status/identity receipt دقیق می‌خواهد و حذف کالا/alias فقط با `204` خالی اعمال می‌شود؛ dialog body-teleported، cancel/Escape بدون DELETE و failure/mismatch با context و copy امن ثابت می‌مانند.
12. `/admin/users/:id` حذف کاربر را فقط پس از پاسخ `200` با پیام ثابت موفقیت و پایان همهٔ نشست‌ها را فقط پس از پاسخ `200` با عدد صحیح `terminated_sessions` اعمال می‌کند؛ دیالوگ نام حساب یا موبایل ندارد، cancel/Escape درخواستی نمی‌فرستد و خطا فقط copy ثابت امن می‌دهد.
13. `/account/security` پایان یک نشست دیگر را فقط پس از پاسخ `200` با متن ثابت موفقیت و خروج از نشست‌های دیگر را فقط پس از پاسخ `200` با الگوی عددی پایان نشست اعمال می‌کند؛ دیالوگ نام دستگاه ندارد، cancel/Escape درخواستی نمی‌فرستد و خطا فقط copy ثابت امن می‌دهد.
14. `/account/storage` پاک‌سازی فایل‌های محلی را فقط پس از تأیید `AppConfirmDialog` اعمال می‌کند؛ cancel/Escape هیچ پاک‌سازی یا reload نمی‌دهد، شکست اندازه و فایل‌ها را با copy ثابت امن نگه می‌دارد و جزئیات داخلی حافظه را نمایش نمی‌دهد.
15. `/admin/users/:id` تغییر وضعیت حساب، رفع مسدودیت و رفع محدودیت را فقط پس از پاسخ `200` با فیلدهای دقیق همان اقدام اعمال می‌کند؛ دیالوگ نام حساب یا موبایل ندارد، جملهٔ لغو/Escape دارد، cancel/Escape درخواستی نمی‌فرستد و خطا فقط copy ثابت امن می‌دهد.
16. `/admin/users/:id` منوی تنظیمات و فرم مسدودیت/محدودیت را با primitiveهای مشترک `ui-*` و رنگ `--ds-*` نشان می‌دهد؛ فرم‌ها confirm نشده‌اند و قرارداد mutation عوض نشده است.
17. `/admin/users` ردیف‌های فهرست را با `ui-list-item` و نشان وضعیت مشترک نشان می‌دهد؛ جستجو session-local مانده و قرارداد navigation عوض نشده است.
18. `/users/:id` و `/profile` بازگشت، تلاش دوباره و ویرایش آدرس را با `ui-icon-button` و `ui-button` نشان می‌دهند؛ هوک کلاس تست و قرارداد privacy/authority حفظ شده است.
19. `/admin/users/:id` انتخاب تاریخ سفارشی محدودیت و مسدودیت را با `ui-button` نشان می‌دهد؛ منطق Jalali و قرارداد mutation حفظ شده است.
20. `/users/:id` و `/profile` آواتار قابل‌ویرایش را به‌صورت دکمهٔ specialized و لینک طرف معامله را با رنگ `--ds-success-700` نشان می‌دهند؛ هوک تست و قرارداد privacy/authority حفظ شده است.
21. `/users/:id` و `/profile` کارت‌های اقدام مالک را با `ui-action-card` و رنگ `--ds-*` نشان می‌دهند؛ navigation تنظیمات حفظ شده است.

## نقشهٔ این بسته

- [STAGE6_CLOSURE_LEDGER.json](STAGE6_CLOSURE_LEDGER.json): verdict هر الزام داخل محدوده.
- [STAGE6_CLOSURE_EVIDENCE_MANIFEST.json](STAGE6_CLOSURE_EVIDENCE_MANIFEST.json): گیت تست، مرورگر، Figma و مرز Stage 8.
- [RUNTIME_CONTRACT.md](RUNTIME_CONTRACT.md): privacy، authority، route و recovery contract.
- [CONTENT_NECESSITY_MATRIX.md](CONTENT_NECESSITY_MATRIX.md): هر سطح باقی‌مانده و علت آن.
- [ROUTE_SURFACE_MANIFEST.json](ROUTE_SURFACE_MANIFEST.json): route/state boundary.
- [PROTECTED_SURFACE_DIFF_MANIFEST.json](PROTECTED_SURFACE_DIFF_MANIFEST.json): disposition محافظت‌شده به‌همراه addendum بستن.
- [FIGMA_SNAPSHOT_MANIFEST.json](FIGMA_SNAPSHOT_MANIFEST.json): Phaseهای تاریخی به‌علاوه section زندهٔ بستن نهایی.
- [DELIVERED_SCOPE_EVIDENCE_INVENTORY.json](DELIVERED_SCOPE_EVIDENCE_INVENTORY.json): allowlist تاریخی Phase 1–3 و اشاره به closure.
- [VALIDATION.md](VALIDATION.md): receiptهای source/browser/Figma.

artifactهای allowlisted زیر `assets/` برای review تاریخیِ Phase 1–3 curated هستند؛ receiptهای Phase 4–19 هم در validation/checkpoint ثبت شده‌اند اما هنوز allowlist یا aggregate جدیدی ندارند. این فایل‌های narrative عمداً mutable باقی می‌مانند؛ هیچ‌کدام `EVIDENCE_MANIFEST` یا freeze کل Stage 6 نیست. یک freeze آینده باید فقط inputs immutable را انتخاب کند و این متن‌ها را داخل aggregate خودش قرار ندهد.

## Deferred reconcile

فهرست deferred قدیمی checkpoint supersede شده است:

- dialog حساس داخل محدوده با `AppConfirmDialog` بسته است؛ تنها `confirm` زنده‌ای که باقی مانده حذف تقویم بازار است و عمداً محافظت‌شده است.
- پوستهٔ غیرمحافظت‌شدهٔ Admin Messages شکاف دیدنی نداشت (`PASS_NO_PATCH`).
- بازنشانی غیر بازار در تنظیمات سیستم با dialog مشترک بسته شد.
- Sites برای این closure الزام نیست و ساخته نشده است.

سطح‌های محافظت‌شده complete معرفی نمی‌شوند؛ آن‌ها `PROTECTED_OUT_OF_SCOPE` می‌مانند.
