# Validation — Stage 8 traceability and bounded 8A evidence

## Access-policy source binding

`8eccdd2177ea5e2b21710b3a8863eace40092c35` / tree
`6cba83c9c2a87672b2741d4a52500c7c6c4f197b`

این SHA فقط snapshot چهار source تغییرنکردهٔ router/guard/role برای استخراج outcomeهاست؛
این چهار فایل در source `4415b7431a6b67965d24c44f6f9f0e59e48ed422` نیز unchanged هستند.
این binding پذیرش کامل Stage 8 نیست.

ماتریس به SHA-256 محتوای این sourceها نیز bind شده است:

- `frontend/src/router/index.ts`
- `frontend/src/router/uiRouteContract.ts`
- `frontend/src/utils/auth.ts`
- `models/user.py`

## Component canonicalization binding

`7588d9c20b995244197d8de09392dd6a5f61b195` / tree
`10d0af9a9d9ecf54e359a9c61d6093e9ed955876`، source و test زیر را bind می‌کند:

- `frontend/src/views/AdminView.vue` — `a1f3e16dbc1957b61bc61a3bfea21fce27cf966cbf3cc268aaeb505d74b91323`
- `frontend/src/views/AdminView.test.ts` — `c4d72ba4a353390c95752788431efe6831fde7451830aeef7780b8bdeb62ab57`

router برای مدیر میانی چهار مسیر `/admin/channels`، `/admin/commodities`،
`/admin/messages` و `/admin/system` را render-route می‌پذیرد، اما `AdminView` همان
deep-linkهای denied را با `router.replace({ name: 'admin' })` به `/admin` canonical می‌کند.
این یک outcome کامپوننتی ثبت‌شده است؛ نه router-record redirect و نه forbidden recovery.

## What is validated by this correction

- JSON schema 3 parse می‌شود.
- هر ۳۰ route موجود در router و UI route contract حاضر است؛ catch-all recovery نیز حذف نشده است.
- ۹ access profile دقیق تعریف شده و هر route برای هر profile یک outcome و `evidenceRefs` دارد.
- تعداد واقعی outcomeهای موردانتظار ۲۷۰ است.
- چهار component canonical outcome مدیر میانی به source/test `AdminView` متصل‌اند.
- `executedFullMatrixCellCount=0` است؛ دو partial synthetic slice جداگانه ثبت شده‌اند اما
  به full matrix یا viewport/state/interaction/environment expansion افزوده نشده‌اند.
- نقش‌های واقعی از contextهای customer/accountant/owner جدا شده‌اند.

این‌ها validation ساختاری و source-traceability هستند، نه اجرای browser/backend روی ۲۷۰ سلول.

## Focused checks and bounded browser receipts on 2026-08-13

- `jq` parse/invariant check روی `ACCEPTANCE_MATRIX.json`: ۳۰ route، ۹ profile،
  ۲۷۰ outcome دارای `evidenceRefs` معتبر، چهار component outcome، و صفر full-acceptance cell.
  Evidence: [ACCEPTANCE_MATRIX.json](ACCEPTANCE_MATRIX.json).
- `npm run test:unit:run -- src/router/index.test.ts src/utils/auth.test.ts`: pass؛
  ۲ فایل و ۴۲ تست. Evidence source:
  [`frontend/src/router/index.test.ts`](../../frontend/src/router/index.test.ts) و
  [`frontend/src/utils/auth.test.ts`](../../frontend/src/utils/auth.test.ts).
- `npm run test:unit:run -- --no-file-parallelism --maxWorkers=1 src/components/UserManager.test.ts src/components/PublicProfile.test.ts`:
  ۶۶/۶۶ pass؛ `npx vue-tsc --noEmit` و `npm run guard:ui` نیز pass.

[STAGE8A_EXECUTION_RECEIPTS.json](STAGE8A_EXECUTION_RECEIPTS.json) دو slice redacted را
ثبت می‌کند: ۴۸/۴۸ scenario cell دسترسی/shell در `390×844` و recovery محدود directory/profile
در viewportهای `360/390/414/430/1440`. slice دوم فقط evidence تاریخی source `4415b743` است؛
پس از آن یک تغییر محلی P1 ایجاد شده و validation آن pending است. خروجی‌های browser فقط
local/synthetic بودند و artifact یا diagnostic خام در repository نگه‌داری نشده است. این receiptها
هیچ سلول full Stage 8 را pass نمی‌کنند و دربارهٔ working tree جاری ادعایی ندارند.

## Live Figma reference, separately bounded

مرجع live/editable در file `z8jgJxST4O2APzWnlyP9gv`، page `486:1455`، section `508:95` و
frame `508:96` (`390×844`) audit محدود گذرانده است: ۲۷ text همگی Vazirmatn، ۷ UIUX instance
متصل، ۴۹ node token-bound، صفر phone/email/URL/query ناایمن، و review بصری بدون crop/overlap.
این فقط clean design reference با محتوای synthetic و source تاریخی `4415b743` است؛ تا validation
محلی P1، هیچ claim دربارهٔ working tree جاری، runtime accessibility، screenshot freeze یا پذیرش
نهایی از آن نتیجه نمی‌شود.

## Historical technical records referenced, not overwritten or promoted here

- Stage 7 browser `uiux-stage7-phase1-motion-a11y-20260812T224044165Z` passed
- `npm run guard:ui` after Stage 7 source: pass
- protected hashes were recorded as matching guard output at that file's `checkedAtSource`

این موارد از receiptهای قبلی ارجاع شده‌اند؛ هیچ protected-surface hash یا freeze در این update
بازنویسی نشده است. پوشش primitive/harness مرحلهٔ ۷ معادل اجرای actual role×route matrix نیست و
در فایل ماتریس هیچ سلول Stage 8 را pass نمی‌کند.

## Not claimed

- owner aesthetic sign-off
- any executed full role/access-profile × route acceptance cell
- viewport/state/interaction/environment cross-product
- component/API-level object authorization
- live backend / Telegram WebView field trial
- merge
- production or staging deployment
- Sites preview or mutation
- a new visual freeze or final Figma acceptance
