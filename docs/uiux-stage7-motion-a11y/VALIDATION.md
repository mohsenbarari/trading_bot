# Validation — Stage 7 Phase 1 + shared-dependency correction

## Source

| field | value |
| --- | --- |
| branch | `condidate/webapp-ui-ux-redesign-v2` |
| commit | `ab0834aac3383e3c790c5865170ab9f007db235c` |
| tree | `93e0c5cb0f8485a804b698d784cd3803a896081e` |
| parent | `704ab7d50352a2af22f0565eb3df26ba9363c80c` |

- App.test / AppPrimitives / JalaliDatePicker / designSystemV2 / PublicProfile / CustomerWorkspace / AccountantWorkspace: pass
- `npm run guard:ui`: pass

این جدول receipt تاریخی Phase 1 را حفظ می‌کند. اصلاح جدید:

| field | value |
| --- | --- |
| commit | `82cb016e57e676c211d746ae852a6600d8d3b6fa` |
| tree | `db65232c7835440868773c8fdbbf032b7bdfd890` |
| parent | `8eccdd2177ea5e2b21710b3a8863eace40092c35` |
| source binding | `327753ddf601ead90e95fa28e1d1961caea3c1d8a3b8ea02f4326d337ae2e164` / 394 files |

## Full regression after correction

روی HEAD `69a228b7` (همان source محصول `82cb016e` به‌علاوهٔ commit مستندات Stage 8):

- Vitest کامل و سری: ۱۵۴ فایل، ۳۱۰ suite و ۱٬۷۳۳/۱٬۷۳۳ تست؛ بدون failure/retry/flake
- production build: pass؛ ۲٬۱۵۹ module، ۲۷٫۵۵ ثانیه و PWA precache با ۱۶۰ entry
- `vue-tsc --noEmit --pretty false --incremental false`: pass، بدون diagnostic
- `npm run guard:ui`: pass؛ ۳۰ route، protected hashes و shared-dependency isolation
- `git diff --check`: pass
- هشدار غیرمسدودکننده: caniuse-lite قدیمی و chunkهای موجود بزرگ‌تر از ۵۰۰ kB

اجرا از worktree پاک آغاز شد. در طول اجرا همین ۹ فایل مستندات Stage 7/Memory به‌صورت
هم‌زمان و آگاهانه ویرایش شدند؛ HEAD و تمام فایل‌های `frontend/` ثابت ماندند. بنابراین receipt
رگرسیون کد معتبر است، اما ادعای repo-wide post-clean برای لحظهٔ پایان آن نمی‌شود.

## Browser

run `uiux-stage7-phase1-motion-a11y-20260812T224044165Z`

- passed / promotable=true
- 7/7 assertion، 5 screenshot
- source binding `3578f342b90d11f3aaef8402bcdfb7c0af27dc7ccaca5c63d14c5daf8ea529f6` (393 files)
- metrics SHA-256 `fd9e514637b4ac887d5a2f3e536b12eac0d1f4cefa99e7ef3875e43a5f5e993c`
- harness SHA-256 `fd239418e1755923fda4e210b8eca288e15b33cf79c54840c3b2142ca81e580e`
- harness path: `/tmp/stage7-phase1-motion-a11y-browser-harness.mjs`
- output: `/tmp/uiux-stage7-phase1-motion-a11y-browser/uiux-stage7-phase1-motion-a11y-20260812T224044165Z/`

### Shared-dependency correction

run `uiux-stage7-shared-dependency-correction-20260813T072432779Z`

- passed / promotable=true
- ۲۴ assertion، ۳ runtime (۳۶۰، ۳۹۰، ۱۴۴۰)، ۱۵ جفت رفت‌وبرگشت دوطرفه و ۹ screenshot
- source binding `327753ddf601ead90e95fa28e1d1961caea3c1d8a3b8ea02f4326d337ae2e164` (394 files)
- source-binding artifact SHA-256 `6f21eb702f2a6f8753620e81e574b87364e2e83cdbaf06a6f2dbf3b1b0edab99`
- metrics SHA-256 `f9063c4a5de6096d9baab206fd73948d5350b9d372ba5496a9efc822fbd25cdb`
- harness SHA-256 `5d4223a62452c341c64d8cc9d61e4373a17603077ce2ee53a2e757c6608deddc`
- protected/mixed roots: marker=false و enter/leave=`200ms`; SECTION مجاز under reduce: marker=true و enter/leave=`0ms`
- TradingSettings calendar inert؛ User/Public Profile فوکوس را جابه‌جا کردند، model ثابت و emission صفر
- protected empty-state role=null؛ approved empty=`status`؛ error=`alert`
- overflow، console/page/API/request/WebSocket unexpected همگی صفر؛ ۳ probe خارجی پیش از transport مسدود شد
- source/Git/harness/dependency/environment قبل و بعد یکسان؛ detached worktree پاک
- artifact فقط local/ephemeral است و به freeze یا selected evidence ارتقا داده نشده است

## Figma

- page `486:1455` `09 — Stage 7 Motion & A11y`
- section `487:18`
- W1 `487:20` 390×844
- W2 `487:39` 390×844
- label `487:19`
- Vazirmatn only؛ unsafe empty
- Stage 6 page `321:18` unchanged

### Design-system-bound live reference

- section `496:18` `2 — Integrated UI/UX reference · source 82cb016e`
- provenance `496:21`؛ W1 `497:18`؛ W2 `499:49`؛ design-system panel `501:93`
- هر دو viewport دقیقاً 390×844 و بدون descendant خارج از bounds
- ۵۰ text، همگی Vazirmatn؛ ۹ instance لینک‌شده؛ ۱۰۳ node دارای variable binding
- Header/Form Field/Button/Bottom Navigation/Status از component library محلی instance شده‌اند
- کنترل‌های اصلی ۴۸ یا ۷۲px؛ unsafe phone/email/URL/query/bearer scan صفر
- render hashهای محلی: section `192cf744fe1d8b027c53f416f3b9d770209ec2859580cc909d078a33eeeb553b`؛ W1 `f22f6d94c90bcf8f53d90d6d3ddea3a37540fc4497e09df6f9ebe229322c24e2`؛ W2 `f45237d89abc5552042c8d391eb0d60c47a81a28aec6a43c460097fd0f74ac57`؛ panel `ca2f714346992395a5850759cab68095d1e394cc00da44e7baed2759b380b3bb`
- renderها local/ephemeral و non-selected هستند؛ Figma live/editable است، freeze یا acceptance کامل نیست

## Closure — frontend runtime `3e62accd`

| field | value |
| --- | --- |
| status | `stage7_complete` |
| authority | `stage7CompleteAuthority=true` |
| frontend runtime | `3e62accdd157bed5dc6f2ed974e56e07c7349910` |
| Stage 6 docs parent | `cd6d9f8549365d246d58bc225133b222aff24a32` |
| focused motion/a11y | `11` file / `297` test pass |
| full serial Vitest | `156` file / `1779` test pass on unchanged frontend |
| `vue-tsc --noEmit` | pass |
| production build | pass؛ `169` file؛ SHA-256 `ee970be6ff3570f4c325c3f420c816b5fa61beb67a9b4071d208b2b3657c50db` |
| `guard:ui` | pass |
| browser | `stage7-closure-20260814084158579`؛ `60/60`؛ report `5b842a08c93cbe31966b446510185276c46d38bf7be85db19322e3c254722cc9`؛ harness `3567e564fa23df27254ea87f69889686a5d422b13f84bd67b88d099606b1fb18`؛ چهار request failure طبقه‌بندی‌نشده در چهار سناریوی cross-boundary؛ این report تغییر نکرده است |
| NONE coverage | همهٔ ۲۳ route با `protection=none` در ۳۹۰ و ۱۴۴۰ |
| cross-boundary | NONE↔FULL، NONE↔MIXED، public بدون نشست |
| follow-up | `stage7-xboundary-20260814093542875`؛ `12/12`؛ report `4781de48a9d9a08232fdd1523f89e2eca63acfa211ef34356bf9a02874bf591a`؛ harness `5a1c6e73d6393588bbc9171ca8412b025a7df43b657223b84189501111a8de61`؛ request failure=`0`؛ cancellation=`0`؛ defect محصول پیدا نشد |
| CSS/adapter removals | صفر؛ هیچ safe dead candidate |
| Figma new section | ساخته نشد؛ historical `487:18` و `496:18` کافی بودند |
| Sites | در محدودهٔ Stage 7 نیست |

Stage 8 دست‌نخورده: `270` / full `0` / slices `12` / scenarios `163` / authority `false`. پذیرش نهایی Stage 8 باید پس از merge روی integration commit اجرا شود.

Ledger SHA-256 `211ba71cbf7eebf88a5282d334f2662d584a40d7f8152cacf6ec6ed7656a8985`. Evidence SHA-256 `5f15f5b8891b90a9f810d2368af6649d58b2dca470f534943969139bb3a71f93`. Follow-up receipt SHA-256 `9c03e10e92cffc62455ddda10e4f5a5acc13eef094db3f34148114610dfbeac3`.
