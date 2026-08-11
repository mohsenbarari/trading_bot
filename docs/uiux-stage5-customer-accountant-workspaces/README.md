# Stage 5 Customer & Accountant Workspaces — بستهٔ closure کامل

وضعیت جاری: **`stage5_complete`**

```text
stage5RuntimeImplementationAuthorized = true
stage5RuntimeWorkStarted = true
stage5ImplementationCommit = 08c5ae1ea95b3087893146547bed8a220eb83d2b
stage5ImplementationTree = 96e2f32c46668f37a4753ccaee21216a2b500097
stage5TechnicalGate = passed_with_inherited_diagnostics_disclosed
stage5ProtectedDiffStatus = passed_zero_unauthorized_drift
stage5BrowserAcceptanceStatus = passed_23_of_23_promotable
stage5FigmaClosureStatus = passed_runtime_delta_locally_hash_bound
stage5EvidenceStatus = local_evidence_inputs_frozen
stage5SitesStatus = passed_private_owner_only_source_bound
stage5CompleteAuthority = true
nextAuthorizedRuntimeStage = null
stage6RuntimeImplementationAuthorized = false
stage6RuntimeWorkStarted = false
```

runtime مرحلهٔ ۵ روی چهار route مشتری/حسابدار commit، به مرورگر نهایی hash-bound و با snapshot نهایی Figma دارای runtime-delta تطبیق داده شده است. ورودی evidence محلی با مرز تاریخی `sitesProven=false` freeze شد و بدون mutation باقی ماند؛ provenance مستقل Sites اکنون preview خصوصی owner-only را اثبات و closure مرحلهٔ ۵ را کامل می‌کند.

مرجع وضعیت اصلی: [checkpoint Stage 5](../WEBAPP_UI_UX_REDESIGN_V2_STAGE5_CUSTOMER_ACCOUNTANT_WORKSPACES_CHECKPOINT_20260811.md)

## مراجع بسته

- [Runtime contract](RUNTIME_CONTRACT.md)
- [Content necessity matrix](CONTENT_NECESSITY_MATRIX.md)
- [Validation ledger](VALIDATION.md)
- [Route/surface manifest](ROUTE_SURFACE_MANIFEST.json)
- [Protected-surface manifest](PROTECTED_SURFACE_DIFF_MANIFEST.json)
- [Figma snapshot manifest — passed/local hash binding](FIGMA_SNAPSHOT_MANIFEST.json)
- [Local evidence input freeze](EVIDENCE_MANIFEST.json)
- [Local validation metrics](assets/local-evidence/local-stage5-customer-accountant-workspaces-validation-metrics.json)
- [Sites provenance — private owner-only](SITES_PROVENANCE.json)
- [Gate manifest](assets/gates/stage5-final-gate-manifest.md)
- [Browser evidence manifest](assets/browser-evidence/stage5-final-evidence-manifest.json)

`FIGMA_SNAPSHOT_MANIFEST.json` snapshot هفت‌بخشی، runtime-delta ناشناس‌سازی‌شده، direct audit و دو export authoritative تازه را ثبت می‌کند. exact Git/security provenance طبق policy فقط محلی است و داخل Figma نوشته نشده. `SITES_PROVENANCE.json` با SHA-256 برابر `c51c25ae739ddac84b061764e0b6f3c2bf73404c6eca69f4a649a82e912a230f`، جدا از freeze ورودی، source/archive/version/deployment/access/probeهای نهایی Sites را ثبت می‌کند.

freeze محلی نهایی و ورودی binder به این رکوردها بسته است:

- HTML ایستا: `19710` بایت / `7b645ad4212ec2566ef4857372f1d608db52256dfa5a2a5038a2715d0e39b2e9`؛
- manifest ورودی: `25912` بایت / `8159b83bd80993cbcfcd4c5badc4dae4615714adfc30f5da18baf5d329e4bf04`؛ `85` ورودی immutable / `8386267` بایت / projection `20bbbe2a3386b262823b21f62a109087abd62c24b2cb9f48cbff7a029872e4c6`؛
- metrics محلی: `14775` بایت / `1d895cf14769adcc89bf434215c9ba7360e5fbdd4f456776290dcd6b7b7bfcec`؛
- source package binder: `93` فایل / `9259772` بایت / aggregate `66461e40b534aa4ff0a48e11aeefbcd651655c63d0581d286ea3c36df0be1b8f` / content+mtime `9c370ec1d5b3ff92a271bb2afa70ab7ae8c1c58c24f5fb28c8cf4aa6b49ca89f`.

این مجموعه شامل manifest، ۸۵ ورودی، شش capture واقعی Chromium و metrics است. run محلی `stage5-local-20260811T113702070Z` واقعاً از `2026-08-11T11:37:02.070Z` آغاز، در `2026-08-11T11:38:52.188Z` freeze و پس از rerender نهایی در `2026-08-11T11:39:52.776Z` بازاندازه‌گیری شد. narrativeهای mutable همین پوشه و capture/metrics از `EVIDENCE_MANIFEST.inputs` خارج‌اند؛ binder آن سه لایه را بدون duplicate جدا اضافه می‌کند. تازه‌ترین clone دورریختنی پس از سخت‌گیری timestamp، زنجیرهٔ `started ≤ completed=frozen ≤ validated ≤ bound` را enforce کرد و در `boundAt=2026-08-11T11:46:34.799Z` به `bound_byte_identical` رسید؛ verifier همان clone نیز `passed` شد. هیچ Site/project/deploy در این اعتبارسنجی ساخته نشد.

## هویت implementation

- branch: `condidate/webapp-ui-ux-redesign-v2`
- comparison base: `646ca6dd83b50e3efd5689e94a241745c030ec9d`
- comparison base tree: `1d6adb6b33fb13ddea2966de99f8f3afe3eb92bb`
- implementation commit: `08c5ae1ea95b3087893146547bed8a220eb83d2b`
- implementation tree: `96e2f32c46668f37a4753ccaee21216a2b500097`
- implementation parent: comparison base دقیق
- exact path count: `34` = `27` modified + `7` added
- pathset SHA-256: `c739ac017e954522ac8d96a5875e5c954e962c42c010e8e197388c98ecc4656f`
- path-content SHA-256: `b164a6ca22cd24b3e9d720f27cf2838aa27a41d8ce06102dbdb6be9103b8b8e1`

الگوریتم‌های دقیق دو hash در `assets/gates/stage5-implementation-git-binding.json` ثبت شده‌اند.

## خروجی runtime

- چهار route موجود `/operations/customers`، detail آن، `/operations/accountants` و detail آن بدون تغییر permission و route scope به workspaceهای route-native تبدیل شدند.
- زیر `900px` list/detail به‌صورت XOR است؛ از `900px` master/detail هم‌زمان فعال می‌شود. query، filter، tab و scroll در رفت‌وبرگشت حفظ و canonical می‌شوند.
- customer workflow شامل pending invitation، مرور مالی before/after با اثر future-only، history/statistics on-demand، session management و حذف حساب/رابطه است.
- accountant workflow شامل pending invitation، ویرایش تک‌فیلدی شرح وظیفه، session management و حذف حساب/رابطه است.
- endpoint detail owner-only اکنون terminal relation را نیز برمی‌گرداند؛ list endpointها فقط رابطه‌های live را نگه می‌دارند.
- حذف destructive به `expected_action` اجباری (`cancel-pending`، `delete-relation` یا `delete-account`) و lock ترتیبی invitation-first/relation-row مجهز است؛ mismatch پیش از side effect با `409` متوقف می‌شود.
- loading/empty/error/retained refresh/missing detail/busy/success/failure از هم جدا هستند و requestهای stale با AbortController، generation و context sentinel مهار می‌شوند.
- overlayها focus trap/return، Escape/backdrop policy، portal scope و targetهای لمسی لازم را حفظ می‌کنند.

## گیت‌های قطعی فعلی

- frontend کامل: `154` فایل / `1663/1663` تست پاس؛
- backend هدفمند: `4` ماژول / `127/127` پاس؛ `76` warning ارثی ثبت‌شده؛
- typecheck، production build، `git diff --check` و aggregate `guard:ui` پاس؛ build برابر `2160` module در `54.28s` با advisoryهای Browserslist/chunk؛
- Playwright collection: `2` spec / `5` تست Chromium؛
- ESLint delta: current `41`، base `55`، inherited `41`، added `0`، removed `14`؛
- Prettier delta: current/base dirty برابر `3/15`، inherited `3`، added `0`، removed `12`؛ raw nonzero blanket-clean نیست؛
- protected Stage 3/4 hashها بدون drift‌اند؛
- browser run `uiux-stage5-browser-20260811T100859948Z`: `23/23`، شش suite، `54` screenshot / `3679487` بایت، promotable و bindشده به commit/tree بالا.

## مرز closure

- browser evidence نهایی است و مستقیم در پوشهٔ خودش freeze شده؛ runهای قبلی فقط diagnostic/non-promotable هستند.
- Figma closure: **passed/local hash-bound**؛ page/root `297:18` / `297:19` اکنون هفت section دارد و runtime-delta `308:556` حذف/recovery را بدون PII یا internal token ثبت می‌کند. exact Git/security provenance فقط در manifest محلی است.
- Sites closure: **passed/private owner-only/source-bound**؛ project `appgprj_6a7b0d76e280819183076ac92b24ff4a`، version `2` و deployment خصوصی در `2026-08-11T12:30:49.897033+00:00` موفق‌اند. policy برابر users `1` / groups `0` / external `0` / editors `0` و probeهای anonymous روی `/` و evidence route برابر `401 + no-store + no-referrer` هستند.
- evidence محلی: **frozen و binder-validated**؛ `EVIDENCE_MANIFEST.json` و metrics با `sitesProven=false` و `stageCompleteAuthority=false` حقیقت تاریخی لحظهٔ freeze را نگه می‌دارند و دست‌نخورده‌اند. `SITES_PROVENANCE.json` لایهٔ پسینی و مستقل completion است.
- preview منتشرشده فقط evidence است؛ هیچ product، staging یا production deployment انجام نشده است.
- Stage 5 کامل است، اما این بسته مجوز Stage 6 نیست و `nextAuthorizedRuntimeStage=null` باقی می‌ماند.
