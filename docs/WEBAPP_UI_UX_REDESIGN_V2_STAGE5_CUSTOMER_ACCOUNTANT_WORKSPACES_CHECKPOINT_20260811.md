# Stage 5 — فضای کاری مشتریان و حسابداران

تاریخ آغاز: ۲۰۲۶-۰۸-۱۱

وضعیت: **`stage5_complete`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## وضعیت machine-readable جاری

```text
stage5Status = stage5_complete
stage5RuntimeImplementationAuthorized = true
stage5RuntimeWorkStarted = true
stage5AuthorizationSource = current_owner_instruction_2026_08_11
stage5ComparisonBaseCommit = 646ca6dd83b50e3efd5689e94a241745c030ec9d
stage5ComparisonBaseTree = 1d6adb6b33fb13ddea2966de99f8f3afe3eb92bb
stage5BaselineFocusedSuites = 3
stage5BaselineFocusedTests = 27
stage5BaselineFocusedStatus = passed
stage5ProtectedBaselineStatus = passed
stage5ImplementationCommit = 08c5ae1ea95b3087893146547bed8a220eb83d2b
stage5ImplementationTree = 96e2f32c46668f37a4753ccaee21216a2b500097
stage5ImplementationParent = 646ca6dd83b50e3efd5689e94a241745c030ec9d
stage5ImplementationPathCount = 34
stage5ImplementationPathSetSha256 = c739ac017e954522ac8d96a5875e5c954e962c42c010e8e197388c98ecc4656f
stage5ImplementationPathContentSha256 = b164a6ca22cd24b3e9d720f27cf2838aa27a41d8ce06102dbdb6be9103b8b8e1
stage5TechnicalGate = passed_with_inherited_diagnostics_disclosed
stage5ProtectedDiffStatus = passed_zero_unauthorized_drift
stage5BrowserAcceptanceStatus = passed_23_of_23_promotable
stage5FigmaClosureStatus = passed_runtime_delta_locally_hash_bound
stage5EvidenceStatus = local_evidence_inputs_frozen
stage5SitesStatus = passed_private_owner_only_source_bound
stage5SitesProvenanceSha256 = c51c25ae739ddac84b061764e0b6f3c2bf73404c6eca69f4a649a82e912a230f
stage5SitesSourceCommit = d06483ceefdeeb26ae0eb47d23bd3718fb01ea5a
stage5SitesSourceTree = 9c21fe3996e18b1e80ca650b119e1961c0589211
stage5CompleteAuthority = true
nextAuthorizedRuntimeStage = null
stage6RuntimeImplementationAuthorized = false
stage6RuntimeWorkStarted = false
workStoppedAfterStage5 = true
```

دستور مالک، توقف تاریخی ثبت‌شده پس از Stage 4 را فقط برای اجرای Stage 5 supersede کرد. همهٔ گیت‌های Stage 5 اکنون بسته‌اند و provenance مستقل Sites تکمیل closure را اثبات می‌کند. این completion هیچ مجوزی برای Stage 6، runtime activation یا product/staging/production deployment ایجاد نمی‌کند؛ بسته و checkpoint بسته‌شده Stage 4 نیز تغییر نکرده‌اند.

## ۱. دامنه و مرز تغییر

مالکیت مستقیم Stage 5 محدود است به:

- `/operations/customers`؛
- `/operations/customers/:relationId`؛
- `/operations/accountants`؛
- `/operations/accountants/:relationId`؛
- interiorهای `CustomerWorkspaceView` و `AccountantWorkspaceView`؛
- helperها، تست‌ها و evidenceهایی که فقط همین دو workflow را پشتیبانی می‌کنند.

routeهای فوق همچنان `requiresAuth` و `requiresOwnerAccess` دارند. authority سمت backend و predicate واقعی owner مرجع نهایی است؛ visibility جای enforcement را نمی‌گیرد. route scope در این مرحله `SECTION` می‌ماند و V2 از طریق opt-in محلی adapterها فعال می‌شود. defaultهای `WorkspaceShell` و adapterهای compatibility، route manifest و role logic بدون migration صریح تغییر نمی‌کنند.

Market، Messenger، بخش Market در Home، interiorهای market/messenger در `AdminMessagesView` و `TradingSettings` و همچنین Daily Core بسته‌شده Stage 4 protected هستند. هر shared change احتمالی باید default-off و با guard و اثبات نبود drift همراه باشد.

## ۲. منابع حقیقت طراحی

منبع اصلی Figma فایل `z8jgJxST4O2APzWnlyP9gv` است:

- Stage 0B-3 page/root: `55:2` / `55:3`؛
- قرارداد و role operations: `55:4` / `55:19`؛
- customer scenarios: `55:22` با mobileهای `57:93`، `57:177` و `57:235`؛
- accountant scenarios: `55:25` با mobileهای `58:231` و `58:309`؛
- sensitive/state atlas: `55:28` با `60:325`، `60:386` و `60:452`؛
- responsive rules: `55:31` و عرض‌های `62:397`، `62:465`، `62:533`، `62:602` و `62:670`؛
- dense desktop master/detail: `63:544`؛
- Design System V2 جدیدتر: page/root `208:2` / `208:3` و component setهای Button `48:14`، Status `49:14` و Relation Row `50:26`.

در تعارض احتمالی، قرارداد Design System V2 جدیدتر بر foundations/components تاریخی `41:2` و `46:2` مقدم است. implementation باید Vue/TypeScript و CSS/tokenهای موجود پروژه باشد؛ React/Tailwind تولیدشده از context فقط مرجع ترجمه است و dependency جدید ایجاد نمی‌کند.

## ۳. پنج task اصلی و ثابت هر workspace

پنج task زیر مبنای تست، browser evidence، Figma closure و Sites هستند:

1. جست‌وجو، بازکردن رابطه و بازگشت با حفظ query، filter، selection و scroll؛
2. ایجاد دعوت و مدیریت pending شامل deadline، وضعیت ارسال، copy و cancel با feedback محلی؛
3. ویرایش مالی مشتری با before/after و اثر future-only، یا ویرایش تک‌فیلدی شرح وظیفه حسابدار؛
4. مشاهده نشست‌ها و پایان نشست انتخاب‌شده با confirm، busy و outcome در context؛
5. حذف حساب/بستن رابطه با نام شخص، پیامدهای cascade واقعی، تأیید قوی و برگشت امن.

برای موبایل list و detail نباید هم‌زمان نمایش داده شوند. در desktop همان facts و hierarchy در master/detail adaptive نمایش داده می‌شود؛ desktop اجازه KPI، metadata یا action اضافی ندارد.

## ۴. قرارداد محتوا و حالت‌ها

- row همیشه‌نمایان فقط identity، یک disambiguator لازم، status اثرگذار و affordance ورود دارد؛
- count کل رابطه، active، tier یا inactive حذف می‌شود؛ تنها pending action queue می‌تواند count داشته باشد؛
- اطلاعات تاریخچه، معاملات، آمار و نشست‌ها on-demand باقی می‌مانند؛
- `home_server`، route/backend/server metadata و علت حدسی خطا نمایش داده نمی‌شود؛
- دعوت pending فقط یک بار deadline و وضعیت واقعی SMS را نزدیک copy/cancel نشان می‌دهد؛
- تغییر مالی پیش از PATCH مرور before/after دارد و روشن می‌کند فقط معاملات آینده تغییر می‌کنند؛ تاریخچه تکمیل‌شده دست‌نخورده است؛
- شرح وظیفه حسابدار یک field دارد و متن فعلی را در کارت جدا تکرار نمی‌کند؛
- copy، cancel، terminate، save و delete نتیجه success/failure/busy را کنار همان action نشان می‌دهند و failure draft/context را پاک نمی‌کند؛
- «قطع ارتباط» برای deletion کافی نیست. عنوان و CTA باید «حذف حساب» باشد و غیرفعال‌شدن وب‌اپ/بات، پایان همه نشست‌ها، انقضای آفرهای فعال، لغو دعوت‌های pending و بسته‌شدن روابط وابسته را صریح کند.

stateهای مستقل: structural loading، retained refresh/stale، cause-neutral load error با retry، true empty با action مجاز، search/filter empty با clear، normal/dense، missing detail با بازگشت معتبر، و action busy/success/failure. deep link نامعتبر نباید blank یا loading بی‌پایان بسازد.

## ۵. معیار پذیرش

- پنج task بالا برای هر workspace در mobile؛
- viewportهای `360/375/390/414/430` و canonical `390×844` بدون horizontal overflow یا CTA obscured؛
- یک سناریوی کامل داده متراکم در desktop `1440×900` و regression در `768/1024/1440`؛
- target حداقل `44×44` و CTA با ارتفاع `48px`؛
- WCAG 2.2 AA، متن عادی `4.5:1`، focus/non-text `3:1`، keyboard، focus return/trap، live feedback، reduced motion و zoom `200%`؛
- permission، API، validation، recovery و business logic فعلی حفظ شود؛
- test، type/build/diff، delta lint/format، guard، protected hashes، rollback و source identity به implementation commit دقیق bind شوند؛
- Figma، browser evidence و Sites closure از implementation جدا و hash/source-bound باشند.

### ۵.۱. supersession محدودِ بند «حفظ API و business logic»

بند بالا به معنی حفظ authority، permission، outcomeهای مجاز و جلوگیری از drift بیرون scope است، نه ممنوعیت هر hardening در قرارداد داخلی همین دو workflow. implementation نهایی دو extension ضروری و محدود را ثبت می‌کند و از این جهت تعبیر «API کاملاً بدون تغییر» را supersede می‌کند:

1. DELETE رابطه همان endpoint/method را نگه می‌دارد، اما query اجباری `expected_action` را با سه capability معنایی `cancel-pending`، `delete-relation` و `delete-account` می‌خواهد. invitation-first/relation-row locks و پاسخ `409` در mismatch پیش از side effect، copy و پیامد destructive را با عمل واقعی هم‌راستا و race را fail-closed می‌کنند.
2. detail owner-only برای customer/accountant می‌تواند terminal lifecycle را بخواند تا deep link و receipt پس از حذف truthful بماند؛ list endpoint همچنان فقط relationهای live را برمی‌گرداند.

این supersession permission تازه، endpoint عمومی بدون owner authority یا business outcome جدید ایجاد نمی‌کند. همهٔ call siteهای route-native و legacy manager به قرارداد اجباری مهاجرت کرده‌اند و backend/frontend/browser regressionها آن را پوشش می‌دهند. سایر permission، validation، session، invitation و trade semantics باید همان authority قبلی را حفظ کنند.

## ۶. baseline قابل تکرار

در comparison base، suiteهای زیر با `27/27` تست پاس شدند:

```text
frontend/src/views/CustomerWorkspaceView.test.ts
frontend/src/views/AccountantWorkspaceView.test.ts
frontend/src/components/workspace/WorkspacePrimitives.test.ts
```

`npm run guard:ui` نیز قبل از هر runtime edit پاس شد و hashهای frozen Stage 4 برای Market، Messenger، Home Market و دو admin interior را بدون drift تأیید کرد. advisory ارثی Browserslist در baseline وجود داشت و failure جدید محسوب نمی‌شود.

## ۷. قرارداد evidence و Sites

closure محلی در `docs/uiux-stage5-customer-accountant-workspaces/` پیش از Sites تکمیل شد. runtime commit، browser evidence و snapshot نهایی Figma قطعی و hash-bound هستند؛ `EVIDENCE_MANIFEST.json` و metrics محلی با `stageCompleteAuthority=false` و `sitesProven=false` freeze شدند و همان مرز تاریخی را بدون mutation حفظ می‌کنند. run واقعی `stage5-local-20260811T113702070Z` از `2026-08-11T11:37:02.070Z` تا freeze `2026-08-11T11:38:52.188Z` اجرا و در `2026-08-11T11:39:52.776Z` rerender/remeasure شد. بستهٔ binder برابر `93` فایل / `9259772` بایت / aggregate `66461e40b534aa4ff0a48e11aeefbcd651655c63d0581d286ea3c36df0be1b8f` / content+mtime `9c370ec1d5b3ff92a271bb2afa70ab7ae8c1c58c24f5fb28c8cf4aa6b49ca89f` است. HTML، manifest و metrics به‌ترتیب SHA-256های `7b645ad4212ec2566ef4857372f1d608db52256dfa5a2a5038a2715d0e39b2e9`، `8159b83bd80993cbcfcd4c5badc4dae4615714adfc30f5da18baf5d329e4bf04` و `1d895cf14769adcc89bf434215c9ba7360e5fbdd4f456776290dcd6b7b7bfcec` دارند. تازه‌ترین clone دورریختنی با exact current scripts و enforce زنجیرهٔ `started ≤ completed=frozen ≤ validated ≤ bound` در `boundAt=2026-08-11T11:46:34.799Z` به `bound_byte_identical` رسید و verifier همان clone `passed` شد؛ خود این اعتبارسنجی دورریختنی هیچ deployی انجام نداد. authority نهایی Sites اکنون فقط در `SITES_PROVENANCE.json`، بیرون frozen input set، ثبت شده و همین checkpoint completion پسینی را ارزیابی می‌کند.

Sites یک evidence preview است، نه product deployment. Stage 5 باید repo و project تازه و مستقل داشته باشد، هیچ Stage 3/4 project را overwrite نکند، source exact commit را push کند، archive deterministic همان commit را save کند و فقط به‌صورت private owner-only deploy شود. environment entry، backend/API/WebSocket، telemetry، external executable resource و SIWC bypass مجاز نیست. anonymous probes باید بدون bypass برابر `401 + no-store + no-referrer` باشند.

## ۸. وضعیت implementation و گیت‌ها

runtime با commit `08c5ae1ea95b3087893146547bed8a220eb83d2b`، tree `96e2f32c46668f37a4753ccaee21216a2b500097` و parent دقیق comparison base بسته شد. delta برابر `34` مسیر (`27` modified و `7` added) است.

- frontend serial: `154` فایل / `1663/1663` پاس؛
- backend هدفمند: چهار module / `127/127` پاس با `76` warning ارثی؛
- typecheck، build (`2160` module / `54.28s`)، diff-check و `guard:ui` پاس؛
- ESLint added برابر صفر با `41` diagnostic ارثی؛ Prettier added برابر صفر با سه فایل dirty ارثی؛
- protected source/behavior/visual drift غیرمجاز صفر؛
- browser run `uiux-stage5-browser-20260811T100859948Z` برابر `23/23`، promotable، با `54` screenshot و source binding دقیق `393` فایل است.

کیفیت artifactها صریح است: raw JSON sanitized از rerun serial Vitest با `154` فایل / `310` suite / `1663/1663` پاس ثبت شد؛ فقط prefix ثابت worktree از نام فایل‌ها حذف شده است. build و backend در این بسته summary قطعی execution هستند، زیرا stream خام اجرای قبلی ذخیره نشده و نباید raw gate evidence نامیده شوند.

## ۹. وضعیت closure بیرونی

- Figma page/root Stage 5 روی `297:18` / `297:19` پس از مجوز صریح مالک با section هفتم `308:556` تکمیل شد. direct audit نهایی `1213` node، `74/74` instance linked، unlinked صفر، coverage کامل runtime deletion/recovery و PII/internal-token hit صفر را ثبت می‌کند. exact Git/security hashes طبق محدودیت connector فقط در provenance محلی هستند.
- Sites repo مستقل با commit `d06483ceefdeeb26ae0eb47d23bd3718fb01ea5a` و tree `9c21fe3996e18b1e80ca650b119e1961c0589211` به frozen local package بسته شد. archive محلی release برابر `43` فایل / `1761912` بایت / SHA-256 `c623d19f82f11e5925056ad7913fc3920466ae46991de9d868f210e62fa95563` و archive ذخیره‌شدهٔ provider برابر `sha256:f2ba4ba4692e0b5fe26fcc245658ef924420064c551e41e5c28dbaa387b80410` است.
- project مستقل `appgprj_6a7b0d76e280819183076ac92b24ff4a` با slug واقعی `tb-uiux-stage5-workspaces` در version `2` ذخیره و در `2026-08-11T12:30:49.897033+00:00` با موفقیت روی preview خصوصی `https://tb-uiux-stage5-workspaces.mohsenbarari235.chatgpt.site` deploy شد.
- access policy برابر custom owner-only با users/groups/external/editors=`1/0/0/0` است؛ environment revision/entry=`0/0`، bypass استفاده‌نشده، worker error صفر و `npm audit --audit-level=high` با صفر vulnerability پاس شدند. probe ناشناس `/` و evidence route هر دو `401 + no-store + no-referrer` بودند.
- aggregate evidence محلی پیش از Sites و با `sitesProven=false` و `stageCompleteAuthority=false` freeze شده است؛ این مقادیر حقیقت تاریخی freeze هستند و تغییر نکرده‌اند. `SITES_PROVENANCE.json` لایهٔ پسینی و مستقل completion است.
- freeze فقط ۸۵ ورودی immutable پیش از capture را در `EVIDENCE_MANIFEST.inputs` دارد؛ خود manifest، شش capture و metrics جداگانه توسط binder افزوده می‌شوند. narrativeهای closure برای درج provenance بعدی Sites عمداً خارج از این مجموعه‌اند.
- metrics وضعیت لحظهٔ capture را محافظه‌کارانه `sitesInputReady=false` نگه می‌دارد؛ receipt پسینی binder در README/VALIDATION پاس واقعی `bound_byte_identical` را ثبت می‌کند.
- در پروتکل دو-pass، `completedAt` مرز تصمیم freeze پس از render اولیه و `validatedAt` پایان rerender/remeasurement فیزیکی نهایی است. pass اولیه staging جداگانهٔ ماندگار ندارد؛ فقط فایل‌های نهایی با SHA و mtime داخل package lock شاهد immutable هستند.

زنجیرهٔ closure (`Figma write/re-audit/export → local aggregate freeze با sitesProven=false → Sites bind/private deploy/provenance → final checkpoint`) کامل شده است. بنابراین `stage5CompleteAuthority=true` است. preview منتشرشده فقط evidence است و هیچ product، staging یا production deployment و هیچ runtime activation انجام نشده است؛ `nextAuthorizedRuntimeStage=null`، Stage 6 غیرمجاز و کار پس از Stage 5 متوقف می‌ماند.
