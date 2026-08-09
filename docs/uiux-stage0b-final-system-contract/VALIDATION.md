# Stage 0B-6 — برنامه و وضعیت validation

وضعیت جاری roadmap: `stage0b6_stage1_stage2_complete_stage3_authorized_not_started`

```text
ownerSystemContractApproval.status = approved
ownerSystemContractApproval.approvedAt = 2026-08-08T20:57:28.073Z
continuousProgressionAuthorized = true
runtimeImplementationAuthorized = true
nextAuthorizedRuntimeStage = Stage 3
stage1RuntimeWorkStarted = true
stage1Status = complete
stage1TechnicalGate = passed
stage2RuntimeImplementationAuthorized = true
stage2RuntimeWorkStarted = true
stage2Status = complete
stage2TechnicalGate = passed_with_preexisting_full_typecheck_parity
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = false
```

## اصل fail-closed

`pending` به معنی pass نیست. assertion فقط زمانی `passed` می‌شود که evidence واقعی آن ساخته و دوباره خوانده شود. node ID، SHA-256، run ID، Sites URL یا test result ساختگی/placeholder ممنوع است. failure هر assertion، نبود exact assertion، تغییر ترتیب یا mismatch pre/post، promotion بسته را متوقف می‌کند.

## Phaseها

| Phase | خروجی | وضعیت |
| --- | --- | --- |
| 0 | قرارداد متنی، manifests معتبر، route inventory و scope lock | `passed` |
| 1 | Auth canonical، Home binding audit/rebind و Operations nav debt fix در Figma | `passed` |
| 2 | هشت section، پنج family ref، پنج width و desktop proof | `passed` |
| 3 | freeze، audit مستقیم schema، ۹ export مستقیم و hash | `passed` |
| 4 | harness محلی مستقل، semantic parity، pre/post equality و atomic promotion | `passed` |
| 5 | Sites خصوصی owner-only، build/security/access/drift checks | `passed` |
| 6 | baseline runtime read-only، build و guard | `passed` |
| 7 | final source/Sites provenance و بازکردن runtime gate | `passed` |

## قرارداد exact 32 assertion

| # | assertion ID | Figma / local | منبع اثبات نهایی |
| ---: | --- | --- | --- |
| 1 | `owner-approval-0b1-through-0b5-recorded` | passed / passed | checkpoint registry + owner history |
| 2 | `canonical-source-registry-complete` | passed / passed | Figma source registry + manifest |
| 3 | `canonical-source-references-resolve` | passed / passed | node/file resolver audit |
| 4 | `approved-source-fact-parity` | passed / passed | cross-stage exact content parity audit |
| 5 | `stage0b6-contract-only-no-new-feature-facts` | passed / passed | product-root fact/action signature scan |
| 6 | `runtime-diff-empty` | passed / passed | Git runtime diff scope audit |
| 7 | `modern-finance-direction-locked` | passed / passed | contract + Figma board |
| 8 | `font-vazirmatn-only` | passed / passed | text-node/font audit |
| 9 | `foundation-inventory-65-9-2` | passed / passed | variables/styles/effects audit |
| 10 | `broken-variable-aliases-zero` | passed / passed | variable binding audit |
| 11 | `component-inventory-12-sets-56-variants-with-delta` | passed / passed | component catalog audit؛ delta دو variant Home-active |
| 12 | `product-proof-detached-instances-zero` | passed / passed | instance audit on product proofs |
| 13 | `known-figma-debt-disposition-complete` | passed / passed | three mandatory gates + Stage 2 carry-forward |
| 14 | `five-mobile-family-references-complete` | passed / passed | five reference roots |
| 15 | `mobile-reference-roots-390x844` | passed / passed | root dimension audit |
| 16 | `responsive-widths-360-375-390-414-430` | passed / passed | five proof dimensions |
| 17 | `desktop-layout-archetypes-complete` | passed / passed | archetype matrix + representative desktop proof |
| 18 | `desktop-fact-parity` | passed / passed | exact fact/action set |
| 19 | `no-product-overflow-or-clipping` | passed / passed | geometry/text audit + local render |
| 20 | `touch-targets-44` | passed / passed | interactive target audit |
| 21 | `primary-cta-height-48` | passed / passed | CTA geometry audit |
| 22 | `navigation-label-11` | passed / passed | text style/size audit |
| 23 | `text-contrast-45` | passed / passed | computed contrast audit |
| 24 | `focus-contrast-3-stroke-3` | passed / passed | focus variant audit |
| 25 | `shell-route-layer-contract-complete` | passed / passed | 29 routes + catch-all + layer board |
| 26 | `common-state-feedback-contract-complete` | passed / passed | state/feedback matrix |
| 27 | `motion-reduced-motion-contract-complete` | passed / passed | motion board + reduced-motion rules |
| 28 | `content-necessity-inventory-complete` | passed / passed | content matrix + exact product-root inventory |
| 29 | `reviewer-metadata-absent-from-product-roots` | passed / passed | reviewer-rationale/copy scan |
| 30 | `synthetic-identities-and-forbidden-copy-clean` | passed / passed | PII/identity/copy scan |
| 31 | `protected-interiors-absent` | passed / passed | node scan + protected manifest |
| 32 | `implementation-gate-and-static-limits-explicit` | passed / passed | approval board + docs/manifest parity |

## نتیجه evidence فنی تا پیش از Sites

کنترل‌های زیر در ۲۰۲۶-۰۸-۰۸ پاس شدند:

- branch جاری دقیقاً `condidate/webapp-ui-ux-redesign-v2` است؛
- هر دو JSON بدون خطا parse شدند؛
- لینک‌های محلی اسناد اصلی resolve شدند؛
- manifest و هر دو metrics دقیقاً ۳۲ assertion با ID/order مصوب دارند؛
- هر ۲۹ route فعلی `frontend/src/router/index.ts` در قرارداد route ثبت شده است؛
- `git diff --check` پاس شد؛
- runtime diff و protected-surface diff صفر است؛
- audit مستقیم Figma و harness محلی هر دو `32/32` پاس هستند؛
- تأیید مالک، Sites خصوصی و source binding تاریخی `0B-6` ثبت و پاس شده‌اند؛ Stage 1 و Stage 2 هرکدام با گیت مستقل خود `complete` شده‌اند. passهای تاریخی این سند جایگزین evidence Stage 2 نیستند.

این pass قرارداد و evidence ایستا/خواندنی را نشان می‌دهد؛ رفتار runtime تازه یا مجوز implementation را ثابت نمی‌کند.

## audit مستقیم Figma — passed

audit مستقیم پس از آخرین mutation و freeze تولید شد:

- file/page/section/root IDs و نام‌های دقیق؛
- timestamp freeze و audit؛
- exact assertion IDs/order/status؛
- counts: variable/text style/effect/component set/variant/instance/detached؛
- alias failures، font families، overflow/text clipping؛
- target/CTA/nav label minimum؛
- text/focus contrast minimum و focus stroke؛
- five mobile refs، five width proofs و desktop fact parity؛
- forbidden reviewer metadata، real PII، route/backend/server copy و protected interior scan؛
- canonical source registry و disposition بدهی‌ها؛
- SHA-256 metrics file.

صفحه `168:1974` و board `168:1975` در `2026-08-08T19:54:11.151Z` freeze شدند؛ audit در `2026-08-08T20:06:32.118Z` و reread provenance در `2026-08-08T20:15:41.663Z` انجام شد. metrics مستقیم SHA-256 برابر `7eaa85d626366ea623714fa4d22cc521bf4455434c05e72db1a4b38a9659e2ff` دارد. هر mutation بعدی این نتیجه را باطل می‌کند.

## export مستقیم — passed

۹ export مستقیم در manifest این فیلدها را دارند:

- path؛
- exact source node ID؛
- capturedAt؛
- width/height؛
- byte size؛
- SHA-256؛
- status.

همه path/node/dimension/bytes/SHA-256 از فایل روی دیسک دوباره تطبیق داده شدند.

## harness محلی مستقل — passed

harness محلی:

- همه assetهای لازم را local و deterministic بارگذاری کند؛
- exact 32 IDs/order را پیش و پس از capture دوباره اندازه بگیرد؛
- هر assertion گم‌شده یا تکراری را failure بداند؛
- canonical DOM/tree hash قبل و بعد برابر داشته باشد؛
- screenshotها را از اندازه‌های `360/375/390/414/430` و `1440×900` بگیرد؛
- page error، network dependency، font fallback، overflow و sensitive string را fail کند؛
- خروجی موقت را فقط بعد از pass کامل با atomic directory swap promote کند؛
- run ID، timestamps، tool/browser version، file hashes و result count ثبت کرده است.

run نهایی `stage0b6-20260808T205504009Z-c6c1b8be` در `2026-08-08T20:55:13.301Z` با `32/32`، DOM ثابت `c974469f9240d756449587e577cb965387634577b0d873263471aa411d88680c` و audit ثابت `f6a7ed5b95871475ff96d31d8b6fe73fb89d6caaef947cea8f8b046f364d0955` پایان یافت. semantic hardening هر پنج family، Home آرام responsive، exact facts/actions دسکتاپ، Auth واقعاً `LTR` با border آبی `2px` و input غیر readonly، و selected row متمایز دسکتاپ با `aria-current="true"` را pass و `driftFindings: []` ثبت کرده است. runهای پیشین که دو false pass دیداری را نگرفته بودند superseded هستند و evidence نهایی محسوب نمی‌شوند.

## Sites خصوصی — passed

موارد زیر پس از deploy دوباره خوانده و پاس شدند:

- access policy برابر `custom` و owner-only؛ یک owner و صفر workspace group، tenant group یا external visitor؛
- source repo commit دقیق `c42eff2b6fb84ee6030a32d0e01f0b3a5fe4c982` روی `main`؛
- archive محلی `/tmp/uiux-stage0b6-sites-preview-dist.tgz` با `401983` byte و SHA-256 `e01ab7ea18a5a7d85ae3e5f39ab3f21230f23714ebd1db1de66352c8f31ee4b6` و topology درست `dist/server/index.js`؛
- normalized Sites tar با ۲۷ فایل، `931840` byte و SHA-256 `db0b581048f8033cc4d19f39e5d685ab4e2c4df5e03568c6e95922d805ee0288`؛ dist tree با ۲۷ فایل، summed bytes `908833` و SHA-256 `2449d36955c9af32461ddc509c761be27f9c9601d555cccb45f8064ad56af2b9`؛
- hosting source/dist هر دو `63` byte و SHA-256 `c5a5444d8fb03d30e39aff801b7cac56ab1ec84fb2b237498ded3e607a291843`؛ worker SHA-256 `55e64c6d4c7bc3d45166f2ac5b2f350bc719e86074367b97d69d645ec35b40b3`؛
- HTML منتشرشده دقیقاً `69362` byte و SHA-256 `6bfe3fce136eb2477f6cbac99561d2e934b469324f3db3c6ca5ec60fee61d5fa`؛
- `npm verify`، build، `audit:dist` و `npm audit` نهایی پاس و آسیب‌پذیری نهایی صفر؛ environment revision `0` با صفر entry و error log پنجره ۶۰ دقیقه‌ای صفر؛
- project `appgprj_6a77997ed65481918d71b8f1f3db541f`، version 1، deployment موفق `appgdep_6a779a76a0348191ba7f15b7a4fb2fd8` و URL `https://trading-bot-uiux-stage0b6.mohsenbarari235.chatgpt.site`؛
- probe ناشناس بدون cookie/auth/bypass در `2026-08-08T21:07:38Z`: root و مسیر evidence هر دو `401`، `no-store` و `no-referrer`؛
- bypass capability وجود دارد اما هرگز generate، request، read، use، persist یا expose نشده است.

Sites behavior runtime را اثبات نمی‌کند؛ این pass فقط privacy، provenance، build و parity مشتق ایستا را ثابت می‌کند.

## baseline runtime — passed read-only

baseline نهایی فقط read-only است و suiteهای مرتبط با Auth، Home/Shell، Operations/Workspaces، Admin/Invitations و Account/Profile/Security/Notifications را ثبت کرده است. `/tmp/uiux-stage0b6-runtime-baseline.json` با SHA-256 `47705d3d10e0a1280b3aa37ffd2fa92b6293294ec2de85617d9007586f3343c0` شامل `35/35` فایل، `322/322` تست، صفر fail و صفر skip است؛ بازه ثبت‌شده `2026-08-08T20:05:12.286Z` تا `2026-08-08T20:06:52.945Z` است.

`npm run build` با ۲۱۴۶ module در `24.69s` پاس شد و `npm run guard:ui` نیز guardهای design-token، hardcoded trade-side color و bespoke modal-overlay را پاس کرد. این نتایج رفتار فعلی را ثبت می‌کنند، نه implementation طراحی تازه.

## diff و scope audit

پیش از final technical freeze و پیش از هر commit باید بررسی شود:

- branch دقیق `condidate/webapp-ui-ux-redesign-v2` است؛
- runtime diff خالی است؛
- فایل Market/Messenger/Bot/Backend تغییر نکرده است؛
- فقط docs/evidence/preview source در scope `0B-6` وجود دارد؛
- فایل‌های ignored عمداً با `git add -f` و exact path پس از freeze stage می‌شوند، نه در Phase 0؛
- HTML محلی، source commit، archive و Sites version با SHA-256 یکسان HTML به یک snapshot اشاره می‌کنند.

## شرایط پایان

technical status `0B-6` پس از pass شدن Sites و final source binding در `2026-08-08T21:07:38Z` کامل شد؛ وضعیت جاری ترکیبی `stage0b6_stage1_stage2_complete_stage3_authorized_not_started` است:

```text
runtimeImplementationAuthorized = true
nextAuthorizedRuntimeStage = Stage 3
stage1RuntimeWorkStarted = true
stage1Status = complete
stage1TechnicalGate = passed
stage2RuntimeImplementationAuthorized = true
stage2RuntimeWorkStarted = true
stage2Status = complete
stage2TechnicalGate = passed_with_preexisting_full_typecheck_parity
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = false
```

Stage 1 پس از Stage `0B-6` اجرا و با evidence fresh خودش `complete` شد؛ baseline این سند نتیجه fresh Stage 1 محسوب نمی‌شود و مرجع آن [بسته Stage 1](../uiux-stage1-trust-continuity/README.md) است. Stage 2 نیز مطابق [checkpoint مستقل](../WEBAPP_UI_UX_REDESIGN_V2_STAGE2_PROTECTED_DESIGN_SYSTEM_CHECKPOINT_20260809.md) با گیت فنی/protected diff، evidence و Sites `complete` شده است. Stage 3 مجاز و شروع‌نشده است؛ Stageهای بعدی بدون تأیید جداگانه مالک اما فقط با گیت فنی خودشان ادامه می‌یابند؛ مگر اینکه مالک صریحاً توقف کند.
