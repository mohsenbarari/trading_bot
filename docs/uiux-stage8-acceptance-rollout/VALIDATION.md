# Validation — Stage 8 traceability and bounded 8A/8B/auth-containment/directory-profile evidence

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

## NONE-route typography source binding

`338918d56f57f7cb974a501b1c43cc22d6afc2b5` / tree
`5e7648e47e3bd74d3b281d0d9b043d71cecdc744`، با baseline
`ec1cc82f429187f3fbbdfeedb0cefad794255854`، bridge محدود typography را bind می‌کند.
`frontend/src/App.vue` فقط وقتی
`getUiRouteContractByName(route.name)?.protection === UI_ROUTE_PROTECTION.NONE` است،
Vazirmatn و `font-synthesis:none` را بر route vnode می‌گذارد. `font-sans` در shell باقی
می‌ماند و FULL/MIXED، شامل concurrent fade، تغییر نمی‌کنند. hash همهٔ source/test/guardهای
مرتبط در [STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json](STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json)
ثبت شده‌اند؛ این binding protected freeze را جایگزین نمی‌کند.

## Public/focused auth viewport-containment source binding

`55f00218295d7aa6f52f75b664544318684d2826` / tree
`cd37c89d082d1062fc83b8520b75326bc7ac74d3`، modifier جداگانه و صریحِ
`AuthFlowShell` را bind می‌کند. `fillViewport` به‌طور پیش‌فرض false است؛ فقط Login،
InviteLanding، WebRegister و SetupPassword آن را opt-in می‌کنند. CSS scoped دقیقاً
`min-height: 100vh` و سپس `min-height: 100dvh` دارد. SystemRecovery modifier نمی‌گیرد تا
در حالت credentialed، رزرو daily navigationِ shell دست‌نخورده بماند.

hash source/test/guardهای مرتبط، از جمله `AuthFlowShell`، چهار caller،
`SystemRecoveryView`، `App.vue`، UI route contract و CSS guard در
[STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json](STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json)
ثبت شده‌اند. این binding مسیرهای FULL/MIXED، root/global cascade یا protected freeze را تغییر
نمی‌دهد.

## Current directory/profile rebaseline source binding

`601b4005d80ef265afaaa6a06a43b48c44c7ca90` / tree
`9d3eedd65a01a190bd1899364ebd7a4a8b060ce5`، source manifest و dist snapshot اجرای browser
محدود جاری را bind می‌کند. این source binding شامل `App.vue`، router/index و UI route contract،
`AdminView`، view/componentهای profile و directory، `AppListItem` و CSSهای V2 است؛ SHA-256 هر
۱۲ source در [STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json](STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json)
ثبت شده است. report همین commit/tree، همان hashهای source و dist `169` فایلی را پیش و پس از run
یکسان و clean ثبت کرد.

این receipt مستقل از و بعد از record تاریخی `4415b743` است. فایل
[STAGE8A_EXECUTION_RECEIPTS.json](STAGE8A_EXECUTION_RECEIPTS.json)، source/tree/hashهای تاریخی آن
و freeze protected surfaces در این rebaseline تغییر، overwrite یا promote نشده‌اند.

## Postcommit workspace roving-focus source binding

`d1e8ecd5a524a03c73b67e531edab363479a32b0` / tree
`c2399cf06099929bcd79c6f9e675fcd62da24952`، five source/test file و dist snapshot اجرای
browser محدود postcommit را bind می‌کند: `AppFilterChips`، `AppTabs`، two workspace caller و
`AppPrimitives.test`. SHA-256 هر پنج source و dist `169` فایلی در
[STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json](STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json)
ثبت شده است. report همان commit/tree/hash و dist را پیش و پس از run یکسان و clean ثبت کرد.

این receipt فقط reveal صریحِ keyboard selection در filter/detail stripهای workspace customer و
accountant را در scope دارد. نه global primitive behavior، نه route/access outcomeهای matrix، و
نه receiptهای تاریخی یا protected freeze را overwrite، promote یا به پذیرش تبدیل نمی‌کند.

## Account long-text wrapping source binding

`8082d8dd6352154b52e86ca6511e27464e072b13` / tree
`7969b02a8cd49c0abe39dd660a7c01e5203df2ff`، چهار source/test file و dist snapshot اجرای
browser محدود account security/notifications را bind می‌کند. SHA-256 هر چهار source و dist
`169` فایلی در [STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json](STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json)
ثبت شده است. aggregation redacted سه report source-bound را نگه می‌دارد؛ این binding نه receiptهای
تاریخی یا freeze را تغییر می‌دهد و نه به پذیرش کامل تبدیل می‌شود.

## Account Hub singleton action-grid source binding

`656cf6c3b62111c5c7bae458e3ea6f61fd8af788` / tree
`64909c2671348fb81903f70ecbedd8d73afbd596`، `AccountHubView` و test آن را با dist `169` فایلی
اجرای browser محدود bind می‌کند. SHA-256 هر دو source/test و report digest در
[STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json](STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json)
ثبت شده است؛ report همان commit/tree و dist را پیش و پس از هر چهار case یکسان و clean ثبت کرد.

این receipt فقط action-grid singleton، مرز Telegram، عدم overlap navigation و journey محدود
normal/accountant را توصیف می‌کند؛ نه access outcomeهای matrix، نه receiptهای تاریخی، و نه
protected freeze را overwrite، promote یا به پذیرش تبدیل می‌کند.

## Notification route-affordance source binding

`95ef7aa768f833c8e8b954d38b36674a77a304a9` / tree
`d5ea1acdf4a461b15c784ff5b555b186de6315ff`، `NotificationsView` و test آن را با dist `169`
فایلی اجرای browser محدود bind می‌کند. SHA-256 هر دو source/test و report digest در
[STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json](STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json)
ثبت شده است؛ report همان commit/tree و dist را پیش و پس از هر سه case یکسان و clean ثبت کرد.

این receipt فقط cue بصریِ non-interactive برای اعلان non-trade واجد مقصد امن، مرز marker-free
برای اعلان‌های non-routable/trade، و journey محدود keyboard/pointer را توصیف می‌کند؛ نه access
outcomeهای matrix، نه receiptهای تاریخی، و نه protected freeze را overwrite، promote یا به پذیرش
تبدیل می‌کند.

## CreateChannel help-popover placement source binding

`0d7f276006deb7f97d20ba07e6f9ecb4d1b48a79` / tree
`8ba2636354f638910198327d84a2d74fe2f21b06`، `CreateChannelView` و guard disposition مجاز را با
dist `169` فایلی اجرای browser محدود bind می‌کند. SHA-256 پنج source/test/guard و report digest در
[STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json](STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json)
ثبت شده است.

این receipt فقط جای‌گذاری اصلاح‌شدهٔ help روی دو سطح FULL، containing-block محلی، target
`32×32` و رفتار scroll ثبت‌شده را توصیف می‌کند؛ clipping قبلی مثبت کاذب بود و هیچ
shared/global overflow workaround استفاده نشد. نه access outcomeهای matrix، نه receiptهای
تاریخی، و نه protected freeze را overwrite، promote یا به پذیرش تبدیل می‌کند.

## What is validated by this correction

- JSON schema 3 parse می‌شود.
- هر ۳۰ route موجود در router و UI route contract حاضر است؛ catch-all recovery نیز حذف نشده است.
- ۹ access profile دقیق تعریف شده و هر route برای هر profile یک outcome و `evidenceRefs` دارد.
- تعداد واقعی outcomeهای موردانتظار ۲۷۰ است.
- چهار component canonical outcome مدیر میانی به source/test `AdminView` متصل‌اند.
- `executedFullMatrixCellCount=270` پس از اجرای رسمی محلی access است؛ دوازده partial
  synthetic slice تاریخی جداگانه می‌مانند و به این عدد افزوده نشده‌اند.
- نقش‌های واقعی از contextهای customer/accountant/owner جدا شده‌اند.

## Official local full run on merged Market A+C — 2026-08-15

مرجع فنی جاری اجرای رسمی اصلاح‌شدهٔ پنج‌فازی روی `main` ادغام‌شده است:
`STAGE8_FULL_ACCEPTANCE_EXECUTION_RECEIPT_V2.json`، run
`stage8-full-acceptance-20260815T063022538Z`، report SHA-256
`e2a00c5599d87a512fadb4a315a55071e47ebbff59a61ef85cf2e52cfcd68aec`،
receipt SHA-256
`a4d1f3338e19bd2f907257bb86f418de3802ed0b09f7c2f4f761fba8d63f1a80`.

۹۶۰ شناسه یکتا، ۲۷۰/۲۷۰ access، ۲۴۰/۲۴۰ viewport، ۱۲۰/۱۱۳/۸۷ applicable
state/interaction/environment، صفر harness-deferred، صفر failure، صفر drift،
صفر unknown/mutating/external. شواهد loading/slow قبل از release پایدار و داخل
digest است. تاریخ نمایشی Market شمسی/خوانا است و loading/error دیگر در
`historyHiddenByProfile` نیستند. رسید V1
`eec298d957532dd0974f358e6df43d96640af4e6b01c482f351fe8fc571dc891` تاریخی،
superseded و non-promotable مانده است. Gate A v3 روی `02162106` با report
`aa6c94bfaa595e3ad1292078a1f2a13bea049c98abe2949e455aaafc3469247a` پیش از ادغام
Market است، superseded است و promotable نیست. این اجرا مجوز staging/production/Sites
نیست. تأیید صریح زیبایی مالک در
[STAGE8_FINAL_ACCEPTANCE_CLOSURE.json](STAGE8_FINAL_ACCEPTANCE_CLOSURE.json)
ثبت شد و `acceptanceAuthority=true` فقط برای بستن پذیرش UI/UX مرحلهٔ ۸ است، نه
مجوز staging/production/Sites.

## Focused checks and bounded browser receipts through 2026-08-14

- `jq` parse/invariant check روی `ACCEPTANCE_MATRIX.json`: ۳۰ route، ۹ profile،
  ۲۷۰ outcome دارای `evidenceRefs` معتبر، چهار component outcome، ۲۷۰ full-acceptance
  cell اجراشده، و دوازده slice محدود / ۱۶۳ scenario تاریخی و non-counting.
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
slice دوم فقط evidence تاریخی source `4415b743` است؛ qualifier pending در receipt تاریخی همان
زمان را ثبت می‌کند و با rebaseline مستقل جاری جایگزین یا promote نشده است. slice جدید با delayed response `550ms`، چهار scenario (pointer/Enter در ۳۹۰، pointer در ۱۴۴۰،
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

receipt پنجم در [STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json](STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json)
به source `338918d5` bind است: focused `40/40`، full serial `155 files / 1759 tests / 0 failed`،
production build، `vue-tsc` و `guard:ui` همگی pass شدند. browser local/synthetic، ۱۲ sample
route typography و ۴ probe واقعی cross-boundary (جمعاً ۱۶ scenario) را با صفر page error پوشش
داد: `/login` و `/admin/invitations` در ۳۹۰/۱۴۴۰ marker=1، Vazirmatn و `font-synthesis:none`
بدون overflow؛ FULL `/market`، `/chat`، `/admin/channels` و `/share-receive` و MIXED `/`،
`/admin/messages` و `/admin/system` marker=0 و legacy system sans باقی ماندند؛ sample LTR mono
`/admin/users` نیز حفظ شد. overlapهای Market↔PublicProfile و Home↔AdminInvitations فقط vnode
NONE را marker‌دار نگه داشتند. Telegram block، WebSocket 403 محلی و Market offers ساده‌شده
diagnosticهای fixture-only هستند؛ این receipt full browser acceptance نیست و به full matrix
افزوده نمی‌شود.

receipt ششم در [STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json](STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json)
به source `55f00218` bind است: full serial `155 files / 1762 tests / 0 failed`، production build
(`2159` module و `160` PWA entry)، `vue-tsc`، `guard:ui` و diff check همگی pass شدند. browser
local/synthetic ۱۴ scenario را پوشش داد: چهار opt-in public/focused auth flow در `390×844` و
`1440×900` (۸ flow)، همان چهار flow در reduced-motion `390×844` (۴ flow)، و SystemRecovery
credentialed در هر دو viewport (۲ flow). چهار opt-in viewport را پر کردند، focus-visible و
document/route overflow نداشتند و page error صفر بود؛ SystemRecovery modifier ندارد و daily nav
بدون collision ماند. The 14-case capture recorded 10 WebSocket-related fixture console diagnostics
(6 setup-password; 4 credentialed SystemRecovery) with no backend; excluded from layout/interaction
conclusions and no clean-console claim. این receipt full browser یا full matrix acceptance نیست و به
full matrix افزوده نمی‌شود.

receipt هفتم در [STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json](STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json)
به source clean `601b4005` / tree `9d3eedd` bind است: ۴۰ scenario شامل ۲۰ route×viewport
normal، ۸ loading/error recovery، ۲ lifecycle، ۱ keyboard journey، ۴ reduced-motion، ۴ CDP
2× و ۱ container-threshold harness-only اجرا شد. route templateهای `/profile`، `/users/:id`،
`/admin/users` و `/admin/users/:id` در viewportهای `360×740`، `390×844`، `414×896`،
`430×932` و `1440×900` پوشش داشتند. source/tree/hash و dist `169` فایلی پیش و پس از run
یکسان و clean بودند؛ overflow document/app، control مرئی بدون نام، page error، request failure،
external request و unknown API غیرمنتظره صفر بود. lifecycle حداکثر یک UserManager
mounted/visible، صفر root outgoing و دقیقاً یک user-list request کامل/non-aborted در هر گذار
directory پوشیده‌شده داشت؛ keyboard return focus نیز روی control دارای label ماند. شش console
diagnostic موردانتظار فقط از fixtureهای deliberate `404/500` recovery (از جمله retry warning)
آمدند، جداگانه طبقه‌بندی و از نتیجهٔ layout/interaction کنار گذاشته شده‌اند؛ بنابراین این receipt
ادعای clean-console ندارد. 2× فقط CDP visual-scale و probe threshold فقط harness-only است. هیچ
artifact خام یا local path/URL در repository نگه‌داری نشده و این slice full
matrix یا protected-surface behavior را attest نمی‌کند.

receipt هشتم در [STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json](STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json)
به source clean `d1e8ecd5` / tree `c2399cf` bind است: ۸ scenario customer/accountant ×
filter/detail در `390×844` و `1440×900` با کلید `End` اجرا و ۸/۸ pass شد. selected final tab
همیشه focused ماند و rectangle آن با tolerance یک CSS pixel در tablist خودش بود؛ document
overflow و page/route scroll change صفر و دو tablist scroll change موبایل intentional بودند.
console/page error/request failure/blocked external/unknown API غیرمنتظره صفر و ۸۰/۸۰ local
synthetic API request موردانتظار دیده شد. source/tree/hash و dist `169` فایلی پیش و پس از run
یکسان و clean بودند؛ هشت screenshot فقط خارج repository نگه‌داری شدند. این receipt فقط scope
نام‌برده، `End` و دو viewport را attest می‌کند؛ full matrix، همهٔ keyboard pathها، visual freeze
یا پذیرش نهایی را attest نمی‌کند.

receipt نهم در [STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json](STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json)
به source clean `8082d8dd` / tree `7969b02` bind است: aggregation redacted سه report مستقل،
۱۲/۱۲ scenario account security/notifications را در پنج viewport عادی و دو probe CDP visual-scale
2 ثبت می‌کند. ۲۸۲ direct-target/ancestor measurement و ۵۴ assertion متن DOM/accessibility گذشت؛
console/page error/request failure/blocked external و unknown API غیرمنتظره صفر و API محلیِ
موردانتظار `131/131` بود. دوازده screenshot فقط خارج repository ماندند. این aggregation اجرای
browser تازه‌ای نیست، CDP 2× همهٔ zoomهای بومی را attest نمی‌کند و به full matrix افزوده نمی‌شود.

receipt دهم در [STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json](STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json)
به source clean `656cf6c3` / tree `64909c` bind است: چهار scenario normal/accountant در
`390×844` و `1440×900` همگی pass شدند. singleton gridها `332` CSS pixel در mobile و `1214` در
desktop را پر کردند؛ security عادی desktop دقیقاً دو track مساوی `601` pixel داشت و security
حسابدار singleton بود. Telegram normal خارج از grid و sibling آن ماند، overflow موبایل و overlap
با daily navigation صفر بود و Enter/click هر دو به profile رسیدند. console/page error/request
failure/external attempt و unknown API صفر و API محلیِ موردانتظار `56/56` بود؛ چهار screenshot
فقط خارج repository ماندند. full regression همان source `155 files / 1769 tests`، build،
`vue-tsc` و `guard:ui` را pass ثبت کرده است. این receipt فقط چهار case نام‌برده را attest می‌کند
و full matrix، visual freeze یا پذیرش نهایی نیست.

receipt یازدهم در [STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json](STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json)
به source clean `95ef7aa7` / tree `d5ea1ac` bind است: سه scenario در `360×740`، `390×844` و
`1440×900` pass شدند. اعلان non-trade واجد مقصد امن دقیقاً یک cue بصریِ non-interactive و
`aria-hidden` داشت؛ حالت‌های non-trade ناامن یا recovery-resolving marker نداشتند و article
غیرقابل‌مسیر ماندند؛ اعلان trade ساختاری نیز marker نگرفت. Enter و pointer فقط برای اعلان امن
journey ثبت‌شده را کامل کردند و click روی articleهای غیرقابل‌مسیر navigation نداشت. console/page
error/request failure/external attempt و unknown API صفر و API محلیِ موردانتظار `162/162` بود.
full regression همان source `155 files / 1770 tests`، build، `vue-tsc` و `guard:ui` را pass ثبت
کرده است. این receipt فقط سه case نام‌برده را attest می‌کند و full matrix، visual freeze یا
پذیرش نهایی نیست.

receipt دوازدهم در [STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json](STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json)
به source `0d7f2760` / tree `8ba26363` bind است: دوازده scenario روی `/admin/channels` و
overlay کانال `/chat` در home/create، `390×844`، `1440×900`، CDP 2× و reduced-motion همگی
pass شدند (۱۲/۱۲، ۲۲۸/۲۲۸). containing-block همان کارت محلی بود، trigger کنار عنوان همان کارت
و دقیقاً `32×32` ماند، و note کامل داخل card/sheet/viewport بود. clipping قبلی مثبت کاذب بود؛
defect واقعی placement با patch محلی و guardشده رفع شد و هیچ shared/global overflow
workaround استفاده نشد. در `/admin/channels` کارت، trigger و note با scroll مسیر حرکت کردند؛
در `/chat` note داخل sheet ماند. console/page error/request failure/external attempt و unknown
API صفر و API محلیِ موردانتظار `132/132` بود. validation پس از commit فقط CreateChannel
`9/9`، HelpPopover `4/4`، protected guard `18/18`، `guard:ui`، `vue-tsc` و browser PASS را
ثبت می‌کند و full suite را به‌عنوان اجرای post-commit معرفی نمی‌کند. این receipt فقط دو سطح
FULL نام‌برده را attest می‌کند و full matrix، visual freeze یا پذیرش نهایی نیست.

مرجع Figma اختیاریِ generic roving-focus برای receipt مستقل roving-focus، section `603:18`، board `603:19`،
scope `604:22` و frameهای mobile `606:18` (`390×844`) و desktop `606:19` (`1440×900`) است.
DRAFT زنده/قابل‌ویرایش، ۴۴ text Vazirmatn، ۱۶/۱۶ instance متصل، semantic style/variable reuse،
zero overflow/crop/overlap، contrast متن `4.55:1`، focus indicator `4.23:1` و privacy scan zero
دارد. محتوای آن generic/synthetic و بدون technical provenance است؛ نه visual freeze، نه final
acceptance و نه evidence runtime/browser/accessibility.

مرجع Figma اختیاریِ generic directory/profile برای همین rebaseline، section `583:146`، scope
`584:146`، mobile directory `584:147`، desktop rail `584:148` و mobile profile `584:149` است.
این DRAFT زنده/قابل‌ویرایش محتوای synthetic/sanitized دارد؛ audit آن ۱۷ linked instance، ۶۳ text
Vazirmatn، صفر visible overflow، حداقل contrast `5.01:1` و privacy review clear را ثبت می‌کند.
نه visual freeze یا پذیرش نهایی است و نه evidence runtime/browser/accessibility.

## Live Figma reference, separately bounded

مرجع live/editable در file `z8jgJxST4O2APzWnlyP9gv`، page `486:1455`، section `508:95` و
frame `508:96` (`390×844`) audit محدود گذرانده است: ۲۷ text همگی Vazirmatn، ۷ UIUX instance
متصل، ۴۹ node token-bound، صفر phone/email/URL/query ناایمن، و review بصری بدون crop/overlap.
این فقط clean design reference با محتوای synthetic و source تاریخی `4415b743` است؛ هیچ claim
دربارهٔ working tree جاری، runtime accessibility، screenshot freeze یا پذیرش نهایی از آن نتیجه
نمی‌شود. receipt جداگانهٔ جاری `601b4005` این board تاریخی را reuse یا promote نمی‌کند.

مرجع invitation-presentation در همان file، page `321:18`، section `535:1455` و board
`535:1456` به source `4beeade2` مربوط است. audit نهایی آن ۴۸ text Vazirmatn، ۱۸/۱۸ instance
متصل، صفر phone/URL/token ناایمن و بدون crop را ثبت کرده است. این target مورد تأیید مالک اما
live/editable و غیر-freeze است؛ review طراحی آن evidence runtime، transport، پذیرش کامل یا
authority عرضه نیست.

مرجع typography در همان file، page `321:18`، section `549:1549` و board `549:1550` با
provenance baseline `ec1cc82f` و implementation `338918d5` ثبت شده است. این DRAFT زنده و
قابل‌ویرایش، protected-baseline-pending است؛ audit geometry و contrast آن pass و دادهٔ حساس
مشاهده نشده است. این مرجع نه owner-approved، نه visual freeze، و نه final acceptance یا evidence
runtime/browser است.

مرجع generic auth-containment در همان file، section `567:1561` و board
`567:1562` با frameهای `390×844` و `1440×900` DRAFT زنده/قابل‌ویرایش است. هر دو frame و board
clip دارند؛ textها Vazirmatn Regular/Medium/SemiBold/Bold، escape مرئی صفر، و review بصری بدون
crop/overlap است. دو instance desktop به Form Field و primary Button متصل‌اند و کمترین contrast
نمایش‌داده‌شدهٔ white/action `4.55:1` است. به‌دلیل policy، board فقط محتوای generic امن دارد و
عمداً commit/tree/hash/route/test/harness/local-path/URL/token/deploy/Sites را نگه نمی‌دارد. این
مرجع source-bound نیست و نه freeze، نه final acceptance و نه evidence runtime/browser است.

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
