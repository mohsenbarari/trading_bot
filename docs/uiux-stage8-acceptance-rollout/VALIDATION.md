# Validation — Stage 8 traceability draft

## Access-policy source binding

`8eccdd2177ea5e2b21710b3a8863eace40092c35` / tree
`6cba83c9c2a87672b2741d4a52500c7c6c4f197b`

این SHA فقط snapshot چهار source تغییرنکردهٔ router/guard/role برای استخراج outcomeهاست.
ایزوله‌سازی shared dependency در `82cb016e` ثبت شده و اصلاح ماتریس در commit مستندات
همین بسته قرار می‌گیرد؛ هیچ‌یک نباید به‌عنوان binding پذیرش کامل Stage 8 تفسیر شوند.

ماتریس به SHA-256 محتوای این sourceها نیز bind شده است:

- `frontend/src/router/index.ts`
- `frontend/src/router/uiRouteContract.ts`
- `frontend/src/utils/auth.ts`
- `models/user.py`

## What is validated by this correction

- JSON schema 2 parse می‌شود.
- هر ۳۰ route موجود در router و UI route contract حاضر است؛ catch-all recovery نیز حذف نشده است.
- ۹ access profile دقیق تعریف شده و هر route برای هر profile یک outcome و `evidenceRefs` دارد.
- تعداد واقعی outcomeهای موردانتظار ۲۷۰ است.
- `executedFullMatrixCellCount=0` و viewport/state/interaction/environment expand یا pass اعلام نشده‌اند.
- نقش‌های واقعی از contextهای customer/accountant/owner جدا شده‌اند.

این‌ها validation ساختاری و source-traceability هستند، نه اجرای browser/backend روی ۲۷۰ سلول.

## Correction-run checks executed on 2026-08-13

- `jq` parse/invariant check روی `ACCEPTANCE_MATRIX.json`: pass؛ ۳۰ route، ۹ profile،
  ۲۷۰ outcome دارای `evidenceRefs` معتبر و صفر full-acceptance cell. Evidence:
  [ACCEPTANCE_MATRIX.json](ACCEPTANCE_MATRIX.json).
- `npm run test:unit:run -- src/router/index.test.ts src/utils/auth.test.ts`: pass؛
  ۲ فایل و ۴۲ تست. Evidence source:
  [`frontend/src/router/index.test.ts`](../../frontend/src/router/index.test.ts) و
  [`frontend/src/utils/auth.test.ts`](../../frontend/src/utils/auth.test.ts).

این اجرای focused فقط policyهای router/guard را بررسی کرده است؛ browser matrix یا پذیرش
زیبایی/یکپارچگی UI/UX اجرا نشده است.

## Historical technical gates referenced, not rerun or promoted here

- Stage 7 browser `uiux-stage7-phase1-motion-a11y-20260812T224044165Z` passed
- `npm run guard:ui` after Stage 7 source: pass
- protected hashes were recorded as matching guard output at that file's `checkedAtSource`

این موارد از receiptهای قبلی ارجاع شده‌اند. پوشش primitive/harness مرحلهٔ ۷ معادل اجرای
actual role×route matrix نیست و در فایل ماتریس هیچ سلول Stage 8 را pass نمی‌کند.

## Not claimed

- owner aesthetic sign-off
- any executed full role/access-profile × route acceptance cell
- viewport/state/interaction/environment cross-product
- component/API-level object authorization
- live backend / Telegram WebView field trial
- merge
- production or staging deployment
- Sites preview
