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

## Route-first directory-transition source binding

`31c69d5a5d2fb1e2c08d9647473d3612b9d85629` / tree
`6b2a30464916f6d1734280852aeabb4c445e4551`، source/testهای route-first زیر را bind می‌کند:

- `frontend/src/views/AdminView.vue` — `b69c3b6c1d34788449f40c1394dbc8981f63597044ad37c24268d76f5f385c0a`
- `frontend/src/views/AdminView.test.ts` — `b1ac1f1d57390a0b79e44030660acb96867b9c40858ee8cd1af50e699969a231`
- `frontend/src/App.vue` — `f415162132ed860ac7f6863f3ba51d77964757ffcb98b705b7c943ed89df853f`
- `frontend/src/components/UserManager.vue` — `6f59e0a3f5d776892b535620943218bf40e52bc26ed05ec11b04a8d4058da078`

این binding مستقل از snapshot تاریخی `7588d9c20b995244197d8de09392dd6a5f61b195` برای
canonicalization مدیر میانی است و hashهای تاریخی آن را جایگزین نمی‌کند. فقط رفتار lifecycle
گذار `/admin` به `/admin/users` و مرز mount directory را برای receipt محدود جدید توصیف می‌کند.

## Invitation-presentation source binding

`4beeade2f3aae4964f1964dedc00f47dfbcd0c05` / tree
`90a4ccea3a88768dc8b2525c5e55ca91aca82e9c`، source/test زیر را bind می‌کند:

- `frontend/src/components/CreateInvitationView.vue` —
  `f2c89b4c4becf509496978718e1cbe222a16a85b1e4a12968b40e1504ea762e0`
- `frontend/src/components/CreateInvitationView.test.ts` —
  `a417db3ca12be4d1e4f83ff5aca3c7e76a7360d68c60bfc6491d7ddbb1aa3663`

این binding فقط source-bound presentation و regression محلی focus Cancel/Escape را توصیف
می‌کند؛ نه receipt تکمیل DELETE روی سرور واقعی.

## What is validated by this correction

- JSON schema 3 parse می‌شود.
- هر ۳۰ route موجود در router و UI route contract حاضر است؛ catch-all recovery نیز حذف نشده است.
- ۹ access profile دقیق تعریف شده و هر route برای هر profile یک outcome و `evidenceRefs` دارد.
- تعداد واقعی outcomeهای موردانتظار ۲۷۰ است.
- چهار component canonical outcome مدیر میانی به source/test `AdminView` متصل‌اند.
- `executedFullMatrixCellCount=0` است؛ چهار partial synthetic slice جداگانه ثبت شده‌اند اما
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

[STAGE8A_EXECUTION_RECEIPTS.json](STAGE8A_EXECUTION_RECEIPTS.json) چهار slice redacted را
ثبت می‌کند: ۴۸/۴۸ scenario cell دسترسی/shell در `390×844`، recovery محدود directory/profile
در viewportهای `360/390/414/430/1440`، و slice رفتاری route-first در source `31c69d5a`.
slice دوم فقط evidence تاریخی source `4415b743` است؛ پس از آن یک تغییر محلی P1 ایجاد شده و
validation آن pending است. slice جدید با delayed response `550ms`، چهار scenario (pointer/Enter در ۳۹۰، pointer در ۱۴۴۰،
و deep-link مدیر میانی) و ۳۳ assertion اجرا شد: هر سه گذار directory دقیقاً یک `GET /api/users/`
با ۲۰۰ کامل/non-aborted، صفر requestfailed/`ERR_ABORTED`، و حداکثر یک UserManager/list مرئی
داشتند؛ deep-link denied به `/admin` canonical شد و CommodityManager یا commodity API نداشت.
Telegram probe محلی intercept شد و هیچ external transport مشاهده نشد. خروجی‌های browser فقط
local/synthetic بودند و artifact یا diagnostic خام در repository نگه‌داری نشده است. این receiptها
هیچ سلول full Stage 8 را pass نمی‌کنند و پذیرش کامل یا aesthetic sign-off ایجاد نمی‌کنند.

receipt چهارم invitation-presentation به source تمیز `4beeade2` bind است: run
`invitation-focus-browser-revalidation-2026-08-13-r1`، ۴۴ assertion، صفر capture و ۲/۲
viewport-flow در `390×844` و `1440×900`. focus بازگشتی Cancel/Escape، copy، نبود overflow
و ۲/۲ end-state DELETE-204 محلی mock مشاهده شد. این receipt **nonpromotable** است: transport
API mock بود و artifact paired Chromium abort مانع ادعای clean network diagnostics یا تکمیل واقعی
سرور است. همچنین boundary محلی Vite برای symlink، verification باینری Vazirmatn را در این محیط
مسدود کرد. artifact خام در repository نگه‌داری نشده و این receipt به full matrix اضافه نمی‌شود.

- focused route-first validation: ۵۵/۵۵ test pass.
- سپس full serial validation: ۱۵۴ فایل / ۱۷۴۶ test pass؛ production build، type check،
  `guard:ui` و diff check نیز pass.

## Live Figma reference, separately bounded

مرجع live/editable در file `z8jgJxST4O2APzWnlyP9gv`، page `486:1455`، section `508:95` و
frame `508:96` (`390×844`) audit محدود گذرانده است: ۲۷ text همگی Vazirmatn، ۷ UIUX instance
متصل، ۴۹ node token-bound، صفر phone/email/URL/query ناایمن، و review بصری بدون crop/overlap.
این فقط clean design reference با محتوای synthetic و source تاریخی `4415b743` است؛ تا validation
محلی P1، هیچ claim دربارهٔ working tree جاری، runtime accessibility، screenshot freeze یا پذیرش
نهایی از آن نتیجه نمی‌شود.

مرجع invitation-presentation در همان file، page `321:18`، section `535:1455` و board
`535:1456` به source `4beeade2` مربوط است. audit نهایی آن ۴۸ text Vazirmatn، ۱۸/۱۸ instance
متصل، صفر phone/URL/token ناایمن و بدون crop را ثبت کرده است. این target مورد تأیید مالک اما
live/editable و غیر-freeze است؛ review طراحی آن evidence runtime، transport، پذیرش کامل یا
authority عرضه نیست.

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
