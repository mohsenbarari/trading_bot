# Stage 6 — Admin & Profile

این پوشه یک بستهٔ توضیحیِ **mutable** برای برش تحویل‌شدهٔ Phase 1 تا Phase 13 است؛ freeze، `EVIDENCE_MANIFEST.json` یا مجوز closure کل Stage 6 نیست.

## وضعیت دقیق

- branch: `condidate/webapp-ui-ux-redesign-v2`
- latest implementation: `b4c8fec657bb78d848ddfb0c5be2b33812c80a64`
- latest tree: `e313defd57d42476ff68be08e3128233229dd2dd`
- delivered: Phase 1 (Admin landing)، Phase 2 (Admin user directory/detail)، Phase 3 (public-profile privacy/authority)، Phase 4 (invitation management)، Phase 5 (public-profile block/unblock)، Phase 6 (workspace account deletion)، Phase 7 (safe recovery برای پایان یک نشست workspace)، Phase 8 (safe recovery برای mutationهای رابطهٔ workspace)، Phase 9 (receipt-safe commodity/alias mutations)، Phase 10 (حذف امن کاربر مدیریت و پایان نشست‌ها)، Phase 11 (نشست‌های امن حساب)، Phase 12 (پاک‌سازی حافظه محلی حساب) و Phase 13 (تأیید امن تغییر وضعیت حساب، رفع مسدودیت و رفع محدودیت).
- authority: `stage6CompleteAuthority=false`.
- broader Stage 6 roadmap: partial/deferred؛ این بسته فقط واقعیت برش تحویل‌شده را ثبت می‌کند.

هیچ Sites، staging، production یا product deployment در این کار آغاز یا تغییر داده نشده است.

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

## نقشهٔ این بسته

- [RUNTIME_CONTRACT.md](RUNTIME_CONTRACT.md): privacy، authority، route و recovery contract.
- [CONTENT_NECESSITY_MATRIX.md](CONTENT_NECESSITY_MATRIX.md): هر سطح باقی‌مانده و علت آن.
- [ROUTE_SURFACE_MANIFEST.json](ROUTE_SURFACE_MANIFEST.json): route/state boundary for the delivered Phase 1–13 slices.
- [PROTECTED_SURFACE_DIFF_MANIFEST.json](PROTECTED_SURFACE_DIFF_MANIFEST.json): disposition دقیق Messenger protected surface.
- [FIGMA_SNAPSHOT_MANIFEST.json](FIGMA_SNAPSHOT_MANIFEST.json): historical Phase 1–3 static Figma audit plus live editable references for Phase 4–13.
- [DELIVERED_SCOPE_EVIDENCE_INVENTORY.json](DELIVERED_SCOPE_EVIDENCE_INVENTORY.json): historical allowlist/hash inventory for Phase 1–3 plus non-freeze supplemental receipts for Phase 4–13.
- [VALIDATION.md](VALIDATION.md): receiptهای source/browser/Figma و محدودیت‌هایشان.

artifactهای allowlisted زیر `assets/` برای review تاریخیِ Phase 1–3 curated هستند؛ receiptهای Phase 4–13 هم در validation/checkpoint ثبت شده‌اند اما هنوز allowlist یا aggregate جدیدی ندارند. این فایل‌های narrative عمداً mutable باقی می‌مانند؛ هیچ‌کدام `EVIDENCE_MANIFEST` یا freeze کل Stage 6 نیست. یک freeze آینده باید فقط inputs immutable را انتخاب کند و این متن‌ها را داخل aggregate خودش قرار ندهد.

## Deferred، نه بسته‌شده

- dialogهای حساسِ باقی‌مانده خارج از PublicProfile، workspace deletion، پایان نشست workspace، mutationهای رابطهٔ workspace، حذف کالا/نام مستعار در `/admin/commodities`، حذف/پایان نشست/تغییر وضعیت/رفع مسدودیت/رفع محدودیت کاربر در `/admin/users/:id`، نشست‌های `/account/security` و پاک‌سازی `/account/storage`؛
- تغییر مستقل Admin Messages/System Settings؛
- closure/freeze/Sites evidence Stage 6؛
- هر ادعای live backend، staging یا production acceptance.

این موارد نتیجهٔ منفی دربارهٔ رفتار موجود نیستند؛ صرفاً خارج از authority این برش‌اند.
