# Stage 2 — بسته Design System V2 محافظت‌شده

وضعیت: **`stage2_complete_stage3_authorized_not_started`**

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

این بسته قرارداد و ledger جاری Stage 2 را روی شاخه `condidate/webapp-ui-ux-redesign-v2` و مبنای مقایسه `8e4cdbf4d1bf23c1b8c159eeaea3544f5fd7cd54` ثبت می‌کند. Stage 2 فقط foundation محافظت‌شده و opt-in است؛ هیچ route محصول در این مرحله V2 را فعال نمی‌کند و هیچ migration صفحه‌ای به Stage 2 نسبت داده نمی‌شود.

مرجع editable طراحی، فایل Figma با کلید `z8jgJxST4O2APzWnlyP9gv`، صفحه `208:2` و root `208:3` است. snapshot این صفحه در `2026-08-09T01:27:37.567Z` frozen و در `2026-08-09T01:24:59Z` audit شده است. root دقیقاً `1440×13028` و بخش `Section/02` با node `213:2` برابر `1280×2092` است؛ inventory آن `65` variable (`20/26/19`)، `26` semantic alias، `10` text style، `2` effect، `12` component set و `56` variant است.

## ترتیب مرجع

1. [Checkpoint اصلی](../WEBAPP_UI_UX_REDESIGN_V2_STAGE2_PROTECTED_DESIGN_SYSTEM_CHECKPOINT_20260809.md)
2. [قرارداد Design System V2](DESIGN_SYSTEM_CONTRACT.md)
3. [Validation ledger](VALIDATION.md)
4. [Manifest Figma و snapshot](FIGMA_SNAPSHOT_MANIFEST.json)
5. [Manifest freeze و protected diff](PROTECTED_SURFACE_DIFF_MANIFEST.json)
6. [Roadmap مصوب](../WEBAPP_UI_UX_REDESIGN_V2_ROADMAP_20260717.md#مرحله-۲--design-system-v2-محافظت‌شده)

## مرز اجرایی

- selectorهای مجاز فقط `[data-ui-system="v2"]` و `[data-ui-system="v2-portal"]` هستند؛
- namespace canonical فقط `--ui-v2-*` است؛ `:root`، `html`، `body`، selector عمومی `*` و remap خانواده `--ds-*` ممنوع‌اند؛
- هر `29` route تولید در Stage 2 دقیقاً `v2Scope: off` دارد؛
- routeهای کامل محافظت‌شده `/market`، `/chat`، `/share-receive` و `/admin/channels` هستند؛
- routeهای mixed برابر `/`، `/admin/messages` و `/admin/system` هستند و interiorهای بازار/پیام‌رسان آن‌ها محافظت می‌شود؛
- catalog فقط یک proof خصوصی و غیر route‌شده است؛ export عمومی یا production route برای آن مجاز نیست؛
- `ui-*` خانواده primitive مرجع باقی می‌ماند؛ ساخت primitive محلی جدید یا فعال‌سازی سراسری V2 ممنوع است؛
- migration محصول و shell متعلق به Stageهای بعدی است؛ تغییر `WorkspaceShell` و adapter کامل `ds-workspace-*` صریحاً به Stage 4 carry-forward شده است.

## آنچه اکنون قطعی است

- Figma Stage 2 روی page/root بالا frozen و reread canonical آن پاس است؛
- همه `65` variable دارای WEB code syntax در namespace `--ui-v2-*` هستند؛
- text style مستقل `UIUX v2/Avatar/Initial` ایجاد و روی دو avatar مرجع bind شده است؛
- component distribution صحیح Button `48:14 = 6` و Status `49:14 = 4` است؛
- catalog Figma شامل `56` reference سطح اول و `6` reference تو‌در‌تو از `12` component set و detached instance برابر صفر است؛
- icon scale `16/20/24px` با nodeهای `267:24 / 267:29 / 267:34` به variableهای `39:28 / 39:29 / 39:30` bind است و verifier مرورگر همان geometry را اندازه‌گیری می‌کند؛
- catalog اجرایی قرارداد list native `ul > li` را حفظ می‌کند؛ item تعاملی `button` و item غیرتعاملی `article` است و role جایگزین ندارد؛
- قرارداد route شامل دقیقاً `29` route با V2 خاموش است؛
- proofهای responsive برای `360/375/390/414/430` و desktop دقیق `1440×900` در Figma وجود دارند؛ audit مستقیم `66` target با failure صفر، `10` نمونه focus و product activation/protected interior برابر صفر را ثبت می‌کند؛
- focused Stage 2 پس از hardening guard برابر `6/6` فایل / `68/68` تست در `14.27s` پاس است؛ guard test دقیقاً `39/39` تست دارد و `guard:ui`، ESLint و Prettier نیز در QA مستقل پاس هستند؛
- contract runtime شامل `65` token canonical + `43` implementation tuple، در مجموع `108` definition tuple و `106` نام یکتا است؛ دو نام اضافه فقط overrideهای مجاز reduced-motion هستند؛
- baseline سریال final دقیق `41` فایل یکتا / `452/452` تست و `84/84` suite با exit `0` و wall `121007ms` پاس است؛ metrics evidence اجرای immediately-prior مستقل `124.87s / 126229ms` را bind می‌کند؛
- targeted Stage 2 typecheck، build با `2153` module در `26.07s` و browser verifier در شش عرض مرجع پاس هستند؛
- protected source diff صفر با SHA-256 خروجی خالی `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` است؛ visual comparison همان `21/26` با پنج carry-forward تاریخی Stage 1 و snapshot update صفر باقی مانده است.
- direct audit برابر `25/25`، هشت export مستقیم hash-bound و local derivative برابر شش PNG + یک metrics با DOM/audit pre-post یکسان و error/residue صفر پاس است.

full `vue-tsc` پاس نامیده نمی‌شود: command یکسان current و base ایزوله هر دو exit `2` و stream byte-identical با `2797` token داده‌اند، هیچ path Stage 2 در آن نیست و targeted Stage 2 typecheck پاس است. جزئیات نسخه‌ها و command در [Validation ledger](VALIDATION.md) ثبت شده است. در نتیجه گیت فنی Stage 2 با parity بدهی ازقبل‌موجود بسته است؛ evidence و Sites نیز با شواهد مستقل پاس شده‌اند.

## evidence بسته‌شده

- [direct audit](assets/figma-stage2-direct-audit.json) — `12558` byte، SHA-256 `7361d43e2f3c9437997663cb313a03b87a5d505f3046f4406d97bd82b6ddacc5`؛
- [HTML evidence](stage2-protected-design-system-evidence.html) — `35371` byte، SHA-256 `cfd12b5a97b8dc055bf4001ca7b4441c3c6f169800b6fb549402ab5a5998bf17`؛
- [capture script](capture-evidence.cjs) — `71379` byte، SHA-256 `95f1dd26c7787cfd608d401506171c68b86a6cdae0f059fdcad9623f29a6ef94`؛
- [local metrics](assets/local-evidence/local-stage2-protected-design-system-validation-metrics.json) — `39640` byte، SHA-256 `4013aa95b5cb16fa4f5f998284786b5be16bd15391cb1eab32813d13ba62e692`؛
- direct export aggregate — `5af685f38703408f10618a1e87397386dbe98e5a8c6df0f8967cdff6da38dbf5`؛
- code evidence aggregate — `46206b37e220ead294598ad142cc657d40f1e804a479c486a26689942858a5a6`.

local evidence مشتق ثانویه و غیرcanonical است و behavior runtime یا protected Git diff را از screenshot اثبات نمی‌کند؛ آن دو با گیت‌های مستقل بالا بسته شده‌اند.

## Sites و progression

Sites خصوصی owner-only پاس است: project `appgprj_6a77e71b6af081918c364e7677286e2c`، version `appgprj_6a77e71b6af081918c364e7677286e2c~appgver_d57bc49b79c08191aa306aa363675158`، deployment موفق `appgdep_6a77e7ff91f08191ab21329ef59b32d4` و URL برابر `https://trading-bot-uiux-stage2-design-system.mohsenbarari235.chatgpt.site` است. source commit مخزن preview برابر `147abdc23c8c94a85c6326b60eac0ef060a89aff` و worktree clean است؛ Git remote/upstream ندارد و commit از مسیر connector به version Sites upload/bind شده است. این hash متعلق به شاخه محصول نیست.

policy پیش/پس از deploy برابر `custom`، role جاری `owner`، allowed user دقیقاً یک owner و group/external/environment برابر صفر است. probe ناشناس root و evidence هر دو `401` با `no-store` و `no-referrer` است. workflow ابزار generation برای bypass را فراخوانی نکرد، از bypass در deploy/probe استفاده نکرد و secret را در source/dist/archive/docs یا artifact کاربر ذخیره/افشا نکرد؛ QA نهایی فقط وجود state غیرخالی secret-bearing مدیریت‌شده provider را از connector مشاهده کرد و مقدار آن redacted، بدون echo و بدون نوشتن در فایل/artifact ماند. provenance machine-readable با `7418` byte و SHA-256 `ba33160599a6f65c568bc326b60f43da33e98c52369ac25b0203c3e1b10d07b3` و dist audit با `7590` byte و SHA-256 `70cce3c027015b6366546d70308164e8e7f7620e86ea159e2d3866d556608ec6` verify شده‌اند. screenshot connector بازبینی شد، اما live HTML احرازشده fetch نشد.

Stage 2 با مجوز پیشروی پیوسته مالک `complete` و Stage 3 `authorized_not_started` است. commit/push شاخه محصول در transaction root ثبت می‌شود؛ در این بسته head hash جعل نشده و runtime Stage 3 هنوز شروع نشده است.
