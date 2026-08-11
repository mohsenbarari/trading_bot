# Stage 6 — Admin & Profile

این پوشه یک بستهٔ توضیحیِ **mutable** برای برش تحویل‌شدهٔ Phase 1/2/3 است؛ freeze، `EVIDENCE_MANIFEST.json` یا مجوز closure کل Stage 6 نیست.

## وضعیت دقیق

- branch: `condidate/webapp-ui-ux-redesign-v2`
- implementation: `3283a6e38209cb06d352740dae5b05bce5ba9002`
- tree: `7284ec4aac1980c0f61201e3346841425f6bcb09`
- delivered: Phase 1 (Admin landing)، Phase 2 (Admin user directory/detail) و Phase 3 (public-profile privacy/authority).
- authority: `stage6CompleteAuthority=false`.
- broader Stage 6 roadmap: partial/deferred؛ این بسته فقط واقعیت برش تحویل‌شده را ثبت می‌کند.

هیچ Sites، staging، production یا product deployment در این کار آغاز یا تغییر داده نشده است.

## مرز تحویل‌شده

1. ورودی مدیریت با مقصدهای واقعی و role-aware، بدون count/pending ساختگی.
2. directory مدیریت با جست‌وجوی session-local و detail server-authorized؛ تنها `scroll` معتبر route context است.
3. profile عمومی با projection server-authoritative: peer عادی فقط mobile masked می‌بیند و address/presence/membership/relation/trade detail دریافت نمی‌کند؛ self و administratorِ مجاز فقط دادهٔ موردنیازِ مجاز را می‌گیرند.
4. actionهای حساسِ self/same-level در backend read-only/forbidden هستند؛ visibility یا disabled UI جای enforcement نیست.
5. همهٔ ورودهای public profile به `/users/:id` canonical می‌شوند؛ query تاریخی پیش از navigation حذف می‌شود. Messenger/Forward discovery بازطراحی یا محدود نشده است.

## نقشهٔ این بسته

- [RUNTIME_CONTRACT.md](RUNTIME_CONTRACT.md): privacy، authority، route و recovery contract.
- [CONTENT_NECESSITY_MATRIX.md](CONTENT_NECESSITY_MATRIX.md): هر سطح باقی‌مانده و علت آن.
- [ROUTE_SURFACE_MANIFEST.json](ROUTE_SURFACE_MANIFEST.json): route/state boundary.
- [PROTECTED_SURFACE_DIFF_MANIFEST.json](PROTECTED_SURFACE_DIFF_MANIFEST.json): disposition دقیق Messenger protected surface.
- [FIGMA_SNAPSHOT_MANIFEST.json](FIGMA_SNAPSHOT_MANIFEST.json): Figma evidence با topology و caveatهایش.
- [DELIVERED_SCOPE_EVIDENCE_INVENTORY.json](DELIVERED_SCOPE_EVIDENCE_INVENTORY.json): allowlist، hash و مرز دقیق artifactهای نهاییِ این برش.
- [VALIDATION.md](VALIDATION.md): receiptهای source/browser/Figma و محدودیت‌هایشان.

artifactهای allowlisted زیر `assets/` برای review این برش curated هستند. این فایل‌های narrative عمداً mutable باقی می‌مانند؛ inventory هم `EVIDENCE_MANIFEST` یا freeze کل Stage 6 نیست. یک freeze آینده باید فقط inputs immutable را انتخاب کند و این متن‌ها را داخل aggregate خودش قرار ندهد.

## Deferred، نه بسته‌شده

- invitation management و pending-invitation flow؛
- commodity feedback persistence؛
- sensitive dialog migration؛
- تغییر مستقل Admin Messages/System Settings؛
- closure/freeze/Sites evidence Stage 6؛
- هر ادعای live backend، staging یا production acceptance.

این موارد نتیجهٔ منفی دربارهٔ رفتار موجود نیستند؛ صرفاً خارج از authority این برش‌اند.
