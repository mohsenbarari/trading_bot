# Stage 2 — Design System V2 محافظت‌شده

تاریخ: ۲۰۲۶-۰۸-۰۹

وضعیت: **`complete`**. منبع Figma frozen و reread canonical، گیت فنی runtime، protected diff، evidence hash-bound و Sites خصوصی owner-only همگی پاس هستند. مجوز پیشروی بی‌وقفه مالک closure فنی/مالکانه Stage 2 را می‌بندد و Stage 3 را مجاز می‌کند؛ ویرایش runtime آن هنوز شروع نشده است. implementation/evidence commit واقعی شاخه محصول `7d1833f33d3332d574ceae312cfc624daaf4f1e5` ثبت شده و این سند در governance transaction بعدی به آن bind می‌شود.

```text
continuousProgressionAuthorized = true
activeRuntimeStage = none
stage2Status = complete
stage2TechnicalGate = passed_with_preexisting_full_typecheck_parity
stage2FigmaStatus = frozen
stage2ProtectedDiff = passed
stage2EvidenceStatus = passed
stage2SitesStatus = passed
nextAuthorizedRuntimeStage = Stage 3
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = false
```

## ۱. هدف و مبنا

هدف Stage 2 ساخت مرجع واقعی Design System V2 با activation صریح و بدون نشت به legacy، Market یا Messenger است. مبنای مقایسه commit `8e4cdbf4d1bf23c1b8c159eeaea3544f5fd7cd54` روی شاخه `condidate/webapp-ui-ux-redesign-v2` است.

این Stage foundation-only است:

- هیچ route محصولی به V2 migrate نمی‌شود؛
- shell تولید، Market، Messenger و interiorهای protected تغییر نمی‌کنند؛
- catalog فقط proof خصوصی و route‌نشده است؛
- migration صفحه‌ها و adapterهای کامل به Stageهای بعدی تعلق دارند؛
- هر ادعای completion نیازمند گیت fresh و hash-bound مستقل است.

## ۲. snapshot frozen Figma

منبع canonical:

| فیلد | مقدار |
| --- | --- |
| file key | `z8jgJxST4O2APzWnlyP9gv` |
| page | `208:2` |
| root | `208:3` |
| frozen at | `2026-08-09T01:27:37.567Z` |
| audited at | `2026-08-09T01:24:59Z` |
| root geometry | `1440×13028` |
| section `213:2` (`Section/02`) | `1280×2092` |
| variable | `65` = `20` primitive + `26` semantic + `19` dimension |
| semantic alias | `26` |
| style | `10` text + `2` effect |
| component | `12` set + `56` variant |
| component distribution | Button `48:14` = `6`؛ Status `49:14` = `4` |
| catalog | `56` reference سطح اول + `6` reference تو‌در‌تو؛ detached `0` |

ساختار صفحه:

1. `210:2` — cover و provenance؛
2. `211:2` — رنگ‌ها و semantic aliasها؛
3. `213:2` — typography، geometry و motion؛
4. `215:2` — component catalog با بخش‌های `215:6` و `215:8`؛
5. `221:2` — scope، route و protected contract؛
6. `223:2` — responsive acceptance؛
7. `226:18` — guard، rollback و evidence.

proofهای موبایل `223:7 / 223:31 / 223:55 / 223:80 / 223:104` به‌ترتیب عرض‌های `360/375/390/414/430` و ارتفاع `620` دارند. proof دسکتاپ `224:2` دقیقاً `1440×900` است.

## ۳. reconciliation foundations

- WEB code syntax هر `65` variable روی الگوی `var(--ui-v2-{name-as-kebab})` قرار گرفته است؛
- aliasهای semantic داخل namespace V2 باقی مانده‌اند؛
- style تازه `UIUX v2/Avatar/Initial` برای Vazirmatn Bold `16` ساخته و روی nodeهای `51:16` و `51:27` bind شده است؛
- placeholder به `neutral/ink-700` alias شده و contrast ثبت‌شده آن روی سفید `5.729:1` است؛
- `neutral/border-300` برابر `#8091A3` است؛ contrast آن روی سفید `3.232:1` و روی سطح صفحه `3.006:1` است؛
- icon scale اجرایی `16/20/24px` با nodeهای `267:24 / 267:29 / 267:34` به‌ترتیب به variableهای `39:28 / 39:29 / 39:30` bind شده است؛
- شش variant Bottom Navigation به variable `size/bottom-nav` bind و ارتفاع آن‌ها `80` است؛
- سه glyph متنی Relation Row با vectorهای `244:2599 / 244:2600 / 244:2601` جایگزین شده‌اند.

reread نهایی Figma همچنین `66` target با failure صفر، `10` نمونه focus، product activation برابر صفر و protected interior برابر صفر را ثبت کرده است. direct audit برابر `25/25` و hash فایل audit و هشت export مستقیم در evidence نهایی ثبت شده‌اند.

این موارد facts frozen طراحی هستند؛ parity runtime، hardening guard، protected diff و artifact/hashهای evidence هرکدام با گیت مستقل بسته شده‌اند.

## ۴. قرارداد runtime Stage 2

foundation runtime به‌صورت افزایشی و opt-in تعریف شده است:

- `frontend/src/styles/design-system-v2.tokens.css` — tokenهای scoped، type/effect/motion؛
- `frontend/src/styles/design-system-v2.components.css` — focus و motion behavior فقط زیر scope؛
- `frontend/src/components/ui/AppDesignSystemScope.vue` — provider subtree؛
- `frontend/src/components/ui/uiDesignSystemScope.ts` — attribute helper و portal lifecycle fail-closed؛
- `frontend/src/router/uiRouteContract.ts` — contract دقیق route/protection/scope؛
- `frontend/src/design-system-v2/scope-manifest.json` — mirror machine-readable؛
- `frontend/src/design-system-v2/canonical-token-contract.json` — freeze machine-readable دقیق `65` token canonical و `43` tuple اجرایی؛
- `frontend/src/components/ui/AppDesignSystemCatalog.vue` — catalog خصوصی، synthetic و route‌نشده؛
- `frontend/scripts/check-design-system-v2-guards.mjs` و library آن — guard CSS/route/activation/catalog؛
- `frontend/scripts/verify-design-system-v2-catalog-browser.mjs` — verifier مرورگر خصوصی.

این foundation در هیچ entrypoint یا route محصول فعال نشده است. تبدیل `WorkspaceShell` و adapterهای `ds-workspace-*` نیز در Stage 2 انجام نشده و صریحاً به Stage 4 carry-forward است.

## ۵. scope و namespace قطعی

دو selector تنها نقطه فعال‌سازی هستند:

```text
[data-ui-system="v2"]
[data-ui-system="v2-portal"]
```

قواعد fail-closed:

- custom property فقط با prefix `--ui-v2-*`؛
- `:root`، `html`، `body` و `*` ممنوع؛
- remap/definition خانواده `--ds-*` ممنوع؛
- sibling escape، functional scope ناقص و selector بی‌scope ممنوع؛
- activation literal یا helper-based در product source ممنوع؛
- catalog route مستقیم یا nested ممنوع؛
- catalog در barrel عمومی export نمی‌شود؛
- scope portal conflicting را overwrite نمی‌کند و cleanup تغییر بیرونی را clobber نمی‌کند.

قرارداد token runtime دقیقاً `108` definition tuple دارد: `65` canonical + `43` implementation tuple. به‌دلیل override مجاز دو motion token در reduced-motion، تعداد نام یکتا `106` است؛ این اختلاف تکرار پنهانی token نیست.

## ۶. قرارداد دقیق route

router تولید دقیقاً `29` route دارد و در Stage 2 مقدار `v2Scope` برای هر `29` مورد `off` است.

routeهای کامل محافظت‌شده:

- `/market`؛
- `/chat`؛
- `/share-receive`؛
- `/admin/channels`.

routeهای mixed:

- `/` با interior `home-market-widget`؛
- `/admin/messages` با interiorهای `admin-messages-market-delivery` و `admin-messages-messenger-delivery`؛
- `/admin/system` با interior `trading-settings-market-controls`.

route mixed در Stageهای بعد نیز حق whole-route activation ندارد؛ مهاجرت آن فقط section-level و با freeze interior ممکن است.

## ۷. catalog و acceptance contract

component catalog Figma هر `56` variant از `12` set را با `56` reference سطح اول و `6` reference تو‌در‌تو نشان می‌دهد. catalog runtime بدون route محصول، پنج state normal/loading/disabled/error/destructive، foundations، focus، portal dialog، motion/reduced motion و responsive را قابل اندازه‌گیری می‌کند.

دو قرارداد executable افزوده و با unit/browser verifier سنجیده شده‌اند:

- icon scale باید در computed geometry دقیقاً `16/20/24px` باشد؛
- list معنایی باید ساختار native `ul > li` را حفظ کند؛ ردیف تعاملی `button` و ردیف غیرتعاملی `article` است و هیچ `role` جایگزینی مجاز نیست.

حدهای پذیرش:

- touch target حداقل `44×44`؛
- CTA حداقل `48px`؛
- Bottom Navigation برابر `80px`؛
- focus ring برابر `3px` با offset `2px` و contrast حداقل `3:1`؛
- متن عادی حداقل `4.5:1`؛
- motion `140/180ms` و reduced motion برابر `1ms`؛
- overflow افقی صفر در `360/375/390/414/430/1440`.

## ۸. protected freeze و rollback

protected diff باید نسبت به base Stage 2 خالی باشد. این انتظار شامل full routeها، interiorهای mixed، `App.vue`، `AppAuthenticatedShell.vue`، `BottomNav.vue`، `AppToasts.vue`، `PWAInstallOverlay.vue` و legacy `main.css` است.

rollback باید فقط foundation افزایشی Stage 2 را حذف کند: provider/helper، دو stylesheet V2، catalog خصوصی، route/scope/token contract و guard/verifier. چون activation route برابر صفر است، rollback نباید تغییر صفحه محصول یا interior protected را برگرداند.

protected source diff نسبت به base خالی و SHA-256 خروجی خالی برابر `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` است. visual suite برابر `21/26` باقی مانده؛ هر پنج mismatch دقیقاً carry-forward تاریخی Stage 1 با اختلاف‌های `1725/2243/7887/9965/16442` هستند، drift تازه Stage 2 ثبت نشده و snapshot update برابر صفر است. جزئیات در [PROTECTED_SURFACE_DIFF_MANIFEST](uiux-stage2-protected-design-system/PROTECTED_SURFACE_DIFF_MANIFEST.json) ثبت شده‌اند.

## ۹. وضعیت گیت‌ها

| گیت | وضعیت |
| --- | --- |
| Figma source frozen | `passed` |
| Figma final reread | `passed` در `2026-08-09T01:24:59Z` |
| Figma audit/export hashها | `passed`؛ audit `25/25` |
| direct exports + per-file SHA-256 | `passed`؛ `8` export، aggregate `5af685f38703408f10618a1e87397386dbe98e5a8c6df0f8967cdff6da38dbf5` |
| focused Stage 2 | `passed`؛ `6/6` فایل، `68/68` تست، شامل `39/39` guard test؛ `14.27s`، exit `0` |
| serial Stage 1 + Stage 2 baseline | `passed`؛ final artifact `41` فایل یکتا / `452/452` تست؛ `84/84` suite، wall `121007ms`، exit `0` |
| guard self-tests و `guard:ui` | `passed`؛ `3` فایل CSS V2 / `29` route |
| ESLint / Prettier | `passed` |
| targeted Stage 2 type | `passed`؛ scoped config، exit `0` |
| full `vue-tsc` | **نه pass**؛ base/current هر دو exit `2` و stream یکسان |
| production build | `passed`؛ `2153` module در `26.07s`؛ فقط warningهای stale caniuse/chunk |
| browser verifier پنج موبایل + desktop | `passed`؛ `360/375/390/414/430/1440` |
| protected source/region diff | `passed`؛ changed path `0` |
| visual comparison | `21/26`؛ فقط پنج carry-forward تاریخی، drift تازه صفر |
| snapshot update | `0` |
| local evidence + immutability | `passed`؛ `25/25`، `6 PNG + 1 metrics`، DOM/audit pre-post برابر |
| Sites owner-only/source-bound/probe | `passed`؛ project/version/deployment/access/probe exact |
| commit/push شاخه محصول | implementation/evidence commit واقعی `7d1833f33d3332d574ceae312cfc624daaf4f1e5` ثبت شد؛ governance binding commit و push در همین transaction root ادامه دارد |

hashهای settled QA مستقل:

- guard library: `ef57cf1fd8fdb5bd50da7e67af03e8170e3a3f4bc4b241553899c3cf11bc90d8`؛
- guard tests: `36ff408a722bd03c9fe4f939dfba9d80af2b415a21e08e43f67ba6764c705122`؛
- canonical token contract: `a0c3f3560acaa8c4fddc123ec042657d7db73d0599698e49eb172f647227cf66`.

اجرای fresh browser نیز focus `3px` با offset `2px`، target حداقل `44px`، CTA `48px`، contrast border روی سفید `3.232327:1` و روی صفحه `3.006272:1`، icon scale دقیق `16/20/24px`، list native و reduced motion `0.001s` را پاس کرد.

final serial artifact مستقل `/tmp/uiux-stage2-runtime-baseline.json` با اندازه `151246` byte و SHA-256 `5c193490d0c913191bdc5245a2f3c01a25c1fe6e26d8a27ef4bf5c8aad096d8d` از `2026-08-09T02:28:33.797859751Z` تا `2026-08-09T02:30:34.812417639Z` اجرا شد: `success=true`، `41` فایل یکتا، `452/452` تست، `84/84` suite، زمان derived برابر `119786.60864257812ms`، wall `121007ms` و exit `0`. شمار suite به‌عنوان شمار فایل گزارش نمی‌شود. اجرای immediately-prior که در metrics evidence bind شده نیز مستقل و پاس است (`124.87s` Vitest / `126229ms` wall)؛ این دو run با هم ادغام یا یکی فرض نشده‌اند.

مرز typecheck دقیق است: `npx vue-tsc --noEmit -p tsconfig.app.json --incremental false` روی current و archive ایزوله base `8e4cdbf4` با نسخه‌های قفل‌شده TypeScript `5.9.3`، `vue-tsc 3.1.1`، Vue `3.5.22` و `@types/node 22.18.11` در هر دو اجرا exit `2` داده و stream diagnostic ثبت‌شده آن‌ها byte-identical و `2797` token بوده است؛ هیچ path متعلق به Stage 2 در آن نیست. این نتیجه full typecheck را PASS نمی‌نامد؛ targeted Stage 2 typecheck پاس است و diagnostic تازه Stage 2 صفر است.

بسته local evidence با run `stage2-20260809T022716844Z-19dd7808` از `2026-08-09T02:27:16.843Z` تا `2026-08-09T02:27:35.682Z` پاس شد:

- [direct audit](uiux-stage2-protected-design-system/assets/figma-stage2-direct-audit.json): `12558` byte، SHA-256 `7361d43e2f3c9437997663cb313a03b87a5d505f3046f4406d97bd82b6ddacc5`؛
- [HTML evidence](uiux-stage2-protected-design-system/stage2-protected-design-system-evidence.html): `35371` byte، SHA-256 `cfd12b5a97b8dc055bf4001ca7b4441c3c6f169800b6fb549402ab5a5998bf17`؛
- [capture script](uiux-stage2-protected-design-system/capture-evidence.cjs): `71379` byte، SHA-256 `95f1dd26c7787cfd608d401506171c68b86a6cdae0f059fdcad9623f29a6ef94`؛
- [local metrics](uiux-stage2-protected-design-system/assets/local-evidence/local-stage2-protected-design-system-validation-metrics.json): `39640` byte، SHA-256 `4013aa95b5cb16fa4f5f998284786b5be16bd15391cb1eab32813d13ba62e692`؛
- output exact: شش PNG و یک metrics، error/request/residue صفر؛ hashهای تک‌تک PNGها داخل metrics ثبت شده‌اند؛
- DOM قبل/بعد برابر `51a735f2a7085c1520251d8d6908a359eedd376c2c2ba37659581f493dff8e27` و audit قبل/بعد برابر `ea003382e2a3b966d16a042a3c036ec9109c8fe5a22374fe505e356fc355bff2` است.

این harness مشتق ثانویه است: منبع canonical را جایگزین نمی‌کند و pass رفتار runtime/protected Git diff را از screenshot استنباط نمی‌کند. گیت‌های runtime، protected و Sites شواهد مستقل دارند و هیچ مورد pending به‌صورت pass فرض نشده است.

Sites خصوصی Stage 2 نیز پاس است:

- project `appgprj_6a77e71b6af081918c364e7677286e2c`، slug `trading-bot-uiux-stage2-design-system` و URL `https://trading-bot-uiux-stage2-design-system.mohsenbarari235.chatgpt.site`؛
- source preview روی branch `main` با final commit `147abdc23c8c94a85c6326b60eac0ef060a89aff` clean است؛ Git remote/upstream ندارد و push گیت remote موضوعیت ندارد؛ همین commit از مسیر connector به Sites upload/bind شده و versionCommit دقیقاً برابر آن است. این commit متعلق به repo پیش‌نمایش است، نه head شاخه محصول؛
- version `1` با ID `appgprj_6a77e71b6af081918c364e7677286e2c~appgver_d57bc49b79c08191aa306aa363675158` و deployment موفق `appgdep_6a77e7ff91f08191ab21329ef59b32d4` در `2026-08-09T02:38:43.513392Z`؛
- archive محلی `2343548` byte، `36` فایل و SHA-256 `a272e245ddd7344bf8795e669338c5814ff670bf264460adbf34bd3152c6c6d2`؛ normalized Sites tar برابر `3041280` byte، `36` فایل و SHA-256 `d06d25c167ac91e18d4387dbb64d375aa67aa936d2e8bf2272024880bfc3b2fb`؛
- policy قبل/بعد `custom`، current role `owner`، فقط یک allowed user با role `owner`، workspace/tenant group و external visitor همگی صفر، environment entry صفر؛
- probe ناشناس بدون cookie/Authorization/bypass در `2026-08-09T02:38:43Z` برای root و مسیر evidence هر دو `HTTP 401` با `Cache-Control: no-store`، `Referrer-Policy: no-referrer` و title `Sign in required`؛
- workflow هیچ ابزار generation برای bypass فراخوانی نکرد و bypass را در deploy/probe استفاده، در source/dist/archive/docs ذخیره، یا به کاربر/artifact افشا نکرد. QA نهایی در `2026-08-09T03:17:20.428Z` وجود یک state غیرخالی secret-bearing مدیریت‌شده توسط provider را از connector مشاهده کرد؛ مقدار redacted شد، echo نشد و در فایل/artifact نوشته نشد و به این workflow نسبت داده نشده است؛ security countها صفر و log پنجره ۶۰ دقیقه error/exception صفر است. تنها event یک favicon `404` خودکار با level `info` و outcome `ok` بود؛
- provenance machine-readable `/tmp/stage2-sites-provenance-final.json` برابر `7418` byte و SHA-256 `ba33160599a6f65c568bc326b60f43da33e98c52369ac25b0203c3e1b10d07b3` است.

screenshot connector بازبینی شد، اما live HTML احراز‌شده fetch نشد؛ این محدودیت صریحاً حفظ شده است.

## ۱۰. تصمیم progression

مجوز ادامه بی‌وقفه مالک همچنان معتبر است و همه گیت‌های closure فنی Stage 2 بسته‌اند. در وضعیت فعلی:

```text
stage2Status = complete
stage2TechnicalGate = passed_with_preexisting_full_typecheck_parity
stage2FigmaStatus = frozen
stage2ProtectedDiff = passed
stage2EvidenceStatus = passed
stage2SitesStatus = passed
nextAuthorizedRuntimeStage = Stage 3
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = false
```

Stage 2 از نظر فنی و approval مالکانه `complete` است. Stage 3 مجاز است اما `stage3RuntimeWorkStarted=false` باقی می‌ماند. `comparisonHeadCommit` اکنون از Git واقعی برابر `7d1833f33d3332d574ceae312cfc624daaf4f1e5` است؛ governance commit حامل این binding و push شاخه در transaction root انجام می‌شوند و هیچ hash پیشاپیشی جعل نشده است.

## ۱۱. بسته governance

- [README](uiux-stage2-protected-design-system/README.md)
- [DESIGN_SYSTEM_CONTRACT](uiux-stage2-protected-design-system/DESIGN_SYSTEM_CONTRACT.md)
- [VALIDATION](uiux-stage2-protected-design-system/VALIDATION.md)
- [FIGMA_SNAPSHOT_MANIFEST](uiux-stage2-protected-design-system/FIGMA_SNAPSHOT_MANIFEST.json)
- [PROTECTED_SURFACE_DIFF_MANIFEST](uiux-stage2-protected-design-system/PROTECTED_SURFACE_DIFF_MANIFEST.json)
