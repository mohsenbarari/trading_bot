# Stage 2 — Validation ledger

وضعیت: **`complete`**؛ Figma frozen و reread canonical، گیت فنی runtime، protected diff، evidence hash-bound و Sites خصوصی source-bound بسته‌اند. Stage 3 مجاز و runtime آن هنوز شروع‌نشده است.

## قاعده ثبت نتیجه

فقط خروجی تازه‌ای که command، زمان، exit code، count و در صورت تولید artifact، SHA-256 واقعی داشته باشد می‌تواند `passed` ثبت شود. وجود کد یا نمونه Figma به‌تنهایی pass runtime نیست. مقدار `pending` نباید به‌صورت ضمنی success خوانده شود.

مبنای مقایسه Stage 2:

```text
branch = condidate/webapp-ui-ux-redesign-v2
comparisonBaseCommit = 8e4cdbf4d1bf23c1b8c159eeaea3544f5fd7cd54
comparisonHeadCommit = 7d1833f33d3332d574ceae312cfc624daaf4f1e5
stage2Status = complete
stage2TechnicalGate = passed_with_preexisting_full_typecheck_parity
nextAuthorizedRuntimeStage = Stage 3
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = false
```

## ۱. ledger Figma

| gate | انتظار | وضعیت جاری | شاهد قابل قبول برای closure |
| --- | --- | --- | --- |
| source/freeze | file `z8jgJxST4O2APzWnlyP9gv`، page `208:2`، root `208:3`، freeze `2026-08-09T01:27:37.567Z` | `passed` | audit `25/25` در `2026-08-09T01:24:59Z`؛ SHA-256 `7361d43e2f3c9437997663cb313a03b87a5d505f3046f4406d97bd82b6ddacc5` |
| geometry root/section | root `1440×13028`؛ `Section/02` با node `213:2` برابر `1280×2092` | `passed` | direct export hash-bound |
| foundations | `65` variable = `20` primitive + `26` semantic + `19` dimension؛ alias `26` | `passed_canonical_reread` | exact name/value/type/syntax/alias audit |
| styles | `10` text style و `2` effect | `passed_canonical_reread` | exact inventory + broken binding صفر |
| components | `12` set، `56` variant؛ Button `6`، Status `4`؛ `56` reference سطح اول + `6` تو‌در‌تو؛ detached `0` | `passed_canonical_reread` | source-node audit + catalog traversal |
| contrast repair | placeholder `#52697B` و `5.729:1`؛ border `#8091A3`، روی سفید `3.232:1` و روی صفحه `3.006:1` | `passed` | direct audit hash-bound |
| geometry repair | شش BottomNav با height token `size/bottom-nav` و ارتفاع `80` | `passed_canonical_reread` | exact binding audit |
| icon repair | Relation Row با سه vector token-bound و text glyph صفر | `passed_canonical_reread` | source traversal و screenshot مستقیم |
| icon scale | `16/20/24px`؛ node `267:24/29/34`؛ variable `39:28/29/30` | `passed` | browser computed geometry دقیق `16/20/24` |
| accessibility/protection inventory | `66` target با failure صفر، `10` focus، product activation `0`، protected interior `0` | `passed` | direct audit hash-bound |
| route contract | دقیقاً `29` route، همه `OFF`؛ full/mixed exact | `passed_canonical_reread` | exact route text/machine audit |
| responsive | `360/375/390/414/430` و desktop `1440×900` | `passed` | هشت direct export با dimension/hash دقیق |
| capture immutability | tree/audit قبل و بعد capture یکسان | `passed` | canonical tree قبل/بعد `4df00cbcb8734865367e41fa28c8936d39a49e1c6578af3d5979f79857365a22` |

جزئیات audit، rereadها، inventory digest و SHA-256 هشت export در [manifest Figma](FIGMA_SNAPSHOT_MANIFEST.json) ثبت شده‌اند. این pass فقط منبع editable و مشتق‌های مستقیم آن را می‌بندد و به‌تنهایی pass runtime یا Sites نیست.

رشته `figma_frozen_external_evidence_pending` در audit مستقیم و metrics محلی، وضعیت immutable **در لحظه freeze** است و برای حفظ hash تغییر نمی‌کند؛ وضعیت جاری external evidence از ledger مستقل local/Sites همین سند و manifest برابر `passed` است و Stage 2 `complete` شده است.

## ۲. ledger runtime و static analysis

| gate | command/scope | وضعیت | run/exit/count/hash |
| --- | --- | --- | --- |
| token parity | exact `65` canonical + `43` implementation tuple؛ `108` definition / `106` نام یکتا | `passed` | contract SHA-256 `a0c3f3560acaa8c4fddc123ec042657d7db73d0599698e49eb172f647227cf66` |
| route parity | test exact `29` route و manifest | `passed` | `guard:ui` تعداد route `29` |
| scope provider/helper | unit test root/portal/ref-count/fail-closed/cleanup | `passed` | داخل focused `6/6` فایل / `68/68` تست |
| private catalog | unit test scope/privacy/states/foundations/behavior/responsive/list/icon | `passed` | داخل focused `6/6` فایل / `68/68` تست |
| V2 guard self-tests | CSS + route/activation/catalog/token policy fixtures | `passed` | `39/39` تست؛ SHA-256 `36ff408a722bd03c9fe4f939dfba9d80af2b415a21e08e43f67ba6764c705122` |
| focused Stage 2 unit | exact `6` Stage 2 test file | `passed` | `68/68` تست؛ `14.27s`؛ exit `0` |
| serial Stage 1 + Stage 2 baseline | exact `41` unique test file | `passed` | final artifact `452/452` test، `84/84` suite؛ wall `121007ms`؛ exit `0` |
| ESLint | exact Stage 2 source list | `passed` | exit `0` |
| Prettier | exact Stage 2 runtime source/config list | `passed` | exit `0`؛ governance و evidence hash-bound در این scope نبودند و برای حفظ hash بازنویسی نشدند |
| UI guard | `npm run guard:ui` | `passed` | `3` CSS V2 / `29` route؛ guard library SHA-256 `ef57cf1fd8fdb5bd50da7e67af03e8170e3a3f4bc4b241553899c3cf11bc90d8` |
| targeted Stage 2 TypeScript | tsconfig موقت محدود به surface Stage 2 | `passed` | exit `0`؛ full typecheck را pass نمی‌کند |
| full TypeScript | `npx vue-tsc --noEmit -p tsconfig.app.json --incremental false` | `bounded_preexisting_failure_not_pass` | current/base exit `2`؛ stream byte-identical `2797` token؛ path تازه Stage 2 صفر |
| production build | `npm run build` | `passed` | `2153` module؛ `26.07s`؛ فقط stale caniuse/chunk warning |
| diff hygiene | `git diff --check` + wrapper روی `git diff --no-index --check /dev/null <new-file>` | `passed` | tracked exit `0`؛ هر no-index به‌علت تفاوت محتوایی exit موردانتظار `1` و output خطای whitespace صفر؛ exit تجمیعی wrapper `0` |

دو run سریال مستقل و پاس ثبت شده‌اند:

- run immediately-prior از `2026-08-09T02:20:35.943525486Z` تا `2026-08-09T02:22:42.181231229Z` با Vitest `124.87s` و wall `126229ms` در local metrics bind شده است؛
- run نهایی machine-readable از `2026-08-09T02:28:33.797859751Z` تا `2026-08-09T02:30:34.812417639Z` در `/tmp/uiux-stage2-runtime-baseline.json` با `151246` byte و SHA-256 `5c193490d0c913191bdc5245a2f3c01a25c1fe6e26d8a27ef4bf5c8aad096d8d` ثبت شد: `success=true`، `41` فایل یکتا، `452/452` تست، `84/84` suite، زمان derived `119786.60864257812ms`، wall `121007ms` و exit `0`.

عدد `84` شمار suiteهای Vitest است، نه شمار فایل؛ هیچ‌یک از دو run با دیگری ادغام نشده است.

مرز full typecheck با command بالا روی current و archive ایزوله `git archive 8e4cdbf4` تحت dependencyهای یکسان TypeScript `5.9.3`، `vue-tsc 3.1.1`، Vue `3.5.22` و `@types/node 22.18.11` سنجیده شد. هر دو exit `2` و stream diagnostic ثبت‌شده byte-identical با اندازه `2797` token دارند و هیچ path متعلق به Stage 2 در آن نیست. این عدد شمار diagnostic نیست و full typecheck **PASS نیست**؛ targeted Stage 2 typecheck در اجرای fresh پس از hardening پاس است.

## ۳. browser verifier

command مرجع: `npm run verify:ui:v2:browser`.

| assertion | عرض‌ها/حالت | وضعیت | metrics/hash |
| --- | --- | --- | --- |
| root/landmark/content overflow صفر | `360/375/390/414/430/1440` | `passed` | overflow صفر در هر شش عرض |
| touch target حداقل `44×44` | همه عرض‌ها | `passed` | minimum `44px` |
| CTA حداقل `48px` | همه عرض‌ها | `passed` | `48px` |
| focus واقعی | outline `3px`، offset `2px`، border token، shadow none | `passed` | `3px / 2px` |
| token computed parity | placeholder/border و namespace | `passed` | border `3.232327:1` روی سفید / `3.006272:1` روی صفحه |
| icon computed geometry | دقیقاً `16/20/24px` | `passed` | `16/20/24` |
| semantic list | `UL > LI` با childهای native `BUTTON/ARTICLE` و role override صفر | `passed` | native semantics حفظ شد |
| reduced motion | duration collapse و transform تزئینی صفر | `passed` | `0.001s` |

اجرای مرورگر باید از catalog واقعی و private entry موقت استفاده کند؛ screenshot استاتیک یا DOM ساختگی جای آن را نمی‌گیرد.

## ۴. protected freeze

انتظار: source diff و protected-region diff نسبت به `8e4cdbf4d1bf23c1b8c159eeaea3544f5fd7cd54` خالی باشد.

| gate | انتظار | وضعیت | hash/count |
| --- | --- | --- | --- |
| protected full-file diff | changed path `0` | `passed` | SHA-256 خروجی خالی `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Home market region | base/head یکسان | `passed` | protected source diff خالی |
| admin messages protected interiors | base/head یکسان | `passed` | protected source diff خالی |
| trading settings protected interior | base/head یکسان | `passed` | protected source diff خالی |
| protected unit regression | بدون regression تازه | `passed` | baseline سریال `41` فایل / `452/452` تست |
| protected visual exact | drift تازه Stage 2 صفر | `passed_with_historical_carry_forwards` | `21/26`؛ پنج مورد پایین |
| snapshots | update پنهانی `0` | `passed` | changed snapshot `0` |

نتیجه source/visual در [PROTECTED_SURFACE_DIFF_MANIFEST](PROTECTED_SURFACE_DIFF_MANIFEST.json) machine-readable و به local metrics hash-bound شده است. اجرای نهایی diff در `2026-08-09T03:04:11.778Z` ثبت شد؛ سه فایل partial/full-freeze نیز base/head SHA یکسان و واقعی دارند.

## ۵. carry-forward تصویری Stage 1

پنج finding زیر از Stage 1 آمده‌اند و نباید با update خودکار snapshot پاک شوند:

| سناریو | اختلاف Stage 1 | disposition لازم در Stage 2 | وضعیت |
| --- | ---: | --- | --- |
| `mobile-390:market` | `1725px` | بازتولید روی base و اثبات عدم drift تازه protected | `historical_carry_forward_unchanged` |
| `mobile-390:customers` | `2243px` | audit state/list-only در برابر snapshot قدیمی | `historical_carry_forward_unchanged` |
| `mobile-390:profile` | `7887px` | audit fixture منبع‌حقیقت self | `historical_carry_forward_unchanged` |
| `desktop-1440:profile` | `9965px` | audit همان fixture در desktop | `historical_carry_forward_unchanged` |
| `mobile-390:invite-landing` | `16442px` | audit دعوت معتبر/canonical | `historical_carry_forward_unchanged` |

suite عمداً `21/26` ثبت می‌شود و PASS کلی نامیده نمی‌شود. هر پنج مورد دقیقاً carry-forward تاریخی Stage 1 هستند؛ drift تازه Stage 2 صفر و snapshot update برابر صفر است.

## ۶. evidence محلی و Sites

| artifact/gate | وضعیت | path/id/hash |
| --- | --- | --- |
| audit metrics مستقیم Figma | `passed` | [direct audit](assets/figma-stage2-direct-audit.json)، `12558` byte، SHA-256 `7361d43e2f3c9437997663cb313a03b87a5d505f3046f4406d97bd82b6ddacc5` |
| exportهای مستقیم Figma | `passed` | `8` فایل؛ aggregate `5af685f38703408f10618a1e87397386dbe98e5a8c6df0f8967cdff6da38dbf5`؛ [manifest Figma](FIGMA_SNAPSHOT_MANIFEST.json) |
| HTML evidence محلی | `passed` | [HTML](stage2-protected-design-system-evidence.html)، `35371` byte، SHA-256 `cfd12b5a97b8dc055bf4001ca7b4441c3c6f169800b6fb549402ab5a5998bf17` |
| capture script | `passed` | [script](capture-evidence.cjs)، `71379` byte، SHA-256 `95f1dd26c7787cfd608d401506171c68b86a6cdae0f059fdcad9623f29a6ef94` |
| local metrics | `passed` | [metrics](assets/local-evidence/local-stage2-protected-design-system-validation-metrics.json)، `39640` byte، SHA-256 `4013aa95b5cb16fa4f5f998284786b5be16bd15391cb1eab32813d13ba62e692` |
| capture PNGها | `passed` | exact `6 PNG + 1 metrics`؛ hashهای تک‌فایل داخل metrics |
| Sites project | `passed` | project `appgprj_6a77e71b6af081918c364e7677286e2c`؛ version `appgprj_6a77e71b6af081918c364e7677286e2c~appgver_d57bc49b79c08191aa306aa363675158`؛ deployment `appgdep_6a77e7ff91f08191ab21329ef59b32d4` succeeded |
| source binding | `passed` | preview commit `147abdc23c8c94a85c6326b60eac0ef060a89aff` و worktree clean؛ Git remote/upstream ندارد؛ commit از مسیر connector upload/bind و versionCommit exact است؛ local archive `2343548` byte / SHA `a272e245ddd7344bf8795e669338c5814ff670bf264460adbf34bd3152c6c6d2`؛ normalized tar `3041280` byte / `36` file / SHA `d06d25c167ac91e18d4387dbb64d375aa67aa936d2e8bf2272024880bfc3b2fb` |
| owner-only policy | `passed` | pre/post `custom`؛ current/allowed role `owner`؛ allowed user `1`؛ workspace/tenant/external/environment همگی `0` |
| anonymous probes / bypass boundary | `passed` | `2026-08-09T02:38:43Z`؛ root و evidence هر دو `401` / `no-store` / `no-referrer` و بدون cookie/Authorization/bypass. workflow ابزار generation را فراخوانی نکرد و bypass را برای deploy/probe استفاده یا در source/dist/archive/docs ذخیره/برای کاربر افشا نکرد؛ QA نهایی در `2026-08-09T03:17:20.428Z` state غیرخالی secret-bearing مدیریت‌شده provider را مشاهده کرد، اما مقدار redacted/بدون echo/بدون نوشتن در فایل یا artifact ماند |
| Sites provenance | `passed` | `/tmp/stage2-sites-provenance-final.json`؛ `7418` byte؛ SHA-256 `ba33160599a6f65c568bc326b60f43da33e98c52369ac25b0203c3e1b10d07b3` |
| dist audit | `passed` | `/tmp/stage2-sites-dist-audit-final.json`؛ `7590` byte؛ SHA-256 `70cce3c027015b6366546d70308164e8e7f7620e86ea159e2d3866d556608ec6`؛ `36` file / `3005300` summed byte / tree SHA `31005f386b74d70507b2f8785786688c7206fde13c3ed3d2cc3b4f665aed5f02` |

run local evidence با id `stage2-20260809T022716844Z-19dd7808` از `2026-08-09T02:27:16.843Z` تا `2026-08-09T02:27:35.682Z` برابر `25/25` پاس شد. DOM pre/post هر دو `51a735f2a7085c1520251d8d6908a359eedd376c2c2ba37659581f493dff8e27` و audit pre/post هر دو `ea003382e2a3b966d16a042a3c036ec9109c8fe5a22374fe505e356fc355bff2` هستند؛ console/page/request/network error و residue صفر است.

HTML و PNGهای محلی مشتق ثانویه و غیرcanonical هستند. خود harness صریحاً `runtimeBehaviorProven=false`، `protectedGitDiffProven=false` و `sitesProven=false` ثبت می‌کند؛ pass runtime و protected diff از گیت‌های مستقل این ledger می‌آید.

## ۷. گیت closure

Stage 2 فقط وقتی قابل بستن است که همه موارد زیر هم‌زمان برقرار باشند:

1. reread/audit و exportهای Figma hash-bound باشند؛
2. parity exact token/component/route و browser verifier پاس باشد؛
3. guard، focused tests، lint/format، build و type diagnostic policy بسته باشند؛
4. protected source/region/visual diff صفر و snapshot update صفر باشد؛
5. پنج carry-forward Stage 1 disposition مستند داشته باشند؛
6. evidence محلی immutable و Sites خصوصی source-bound با probe ناشناس پاس باشد؛
7. checkpoint/manifestها با comparison base، implementation head واقعی و hashهای واقعی evidence/source به‌روزرسانی شوند؛
8. closure فنی/مالکانه و implementation/evidence commit واقعی ثبت شوند؛ governance binding commit و push شاخه در ادامه transaction root انجام می‌شوند.

تصمیم فعلی:

```text
stage2Status = complete
stage2TechnicalGate = passed_with_preexisting_full_typecheck_parity
stage2ProtectedDiff = passed
stage2EvidenceStatus = passed
stage2SitesStatus = passed
nextAuthorizedRuntimeStage = Stage 3
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = false
```

همه گیت‌های فنی closure پاس هستند و مجوز پیوسته مالک Stage 2 را می‌بندد. `comparisonHeadCommit` از commit واقعی implementation/evidence برابر `7d1833f33d3332d574ceae312cfc624daaf4f1e5` ثبت شده است؛ این governance commit همان binding را حمل می‌کند و push پس از آن انجام می‌شود. Stage 3 مجاز است، اما هیچ ویرایش runtime آن در این ledger شروع‌شده ادعا نمی‌شود. screenshot connector بازبینی شد؛ signed-in live HTML fetch نشد و این محدودیت محفوظ است.
