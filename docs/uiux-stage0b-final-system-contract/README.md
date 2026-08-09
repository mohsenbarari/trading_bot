# Stage 0B-6 — Final system contract evidence package

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

## هدف بسته

این پوشه evidence و قرارداد نهایی Stage 0B را بدون تغییر runtime جمع می‌کند. منبع editable نهایی Figma رسمی است؛ export مستقیم provenance همان node را دارد، harness محلی assertionهای یکسان را مستقل و fail-closed سنجیده و Sites فقط preview خصوصی owner-only خواهد بود.

Figma canonical، ۹ export مستقیم، audit مستقیم `32/32`، مشتق محلی با ۷ PNG و `32/32`، baseline خواندنی `35/35` فایل و `322/322` تست، build و `guard:ui` پاس شده‌اند. semantic hardening محلی parity دقیق هر پنج خانواده، Home آرام responsive، fact/action دسکتاپ، Auth `LTR` با border آبی `2px` و input قابل‌ویرایش، و selection متمایز/`aria-current` دسکتاپ را سنجیده و drift صفر ثبت کرده است. Sites خصوصی owner-only نیز source-bound، منتشر و با دو probe ناشناس `401` پاس شده است. **Stage `0B-6` از نظر فنی کامل است؛ Stage 1 پس از closure اجرا و در بسته مستقل `uiux-stage1-trust-continuity` بسته شد.**

## ترتیب مرجع

1. [Checkpoint اصلی](../WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_FINAL_SYSTEM_CONTRACT_CHECKPOINT_20260808.md)
2. [قرارداد `SYS-01..SYS-14`](DESIGN_CONTRACT.md)
3. [ماتریس ضرورت محتوا](CONTENT_NECESSITY_MATRIX.md)
4. [ماتریس traceability runtime](RUNTIME_TRACEABILITY_MATRIX.md)
5. [برنامه validation و ۳۲ assertion](VALIDATION.md)
6. [manifest سطح‌های محافظت‌شده](PROTECTED_SURFACE_MANIFEST.json)
7. [manifest snapshot و evidence](FIGMA_SNAPSHOT_MANIFEST.json)

## سلسله‌مراتب canonical

| سطح | نقش |
| --- | --- |
| Figma file `z8jgJxST4O2APzWnlyP9gv` | منبع editable و canonical طراحی |
| checkpoint و manifestها | قرارداد متنی، provenance و گیت پذیرش |
| export مستقیم Figma | شاهد تصویری source-node-bound |
| harness محلی | شاهد مستقل و fail-closed؛ نه منبع طراحی |
| Sites خصوصی | preview مشتق‌شده برای بازبینی مالک؛ نه منبع طراحی |
| runtime baseline | ثبت رفتار قبل از پیاده‌سازی؛ نه اثبات طراحی تازه |

## سه گیت Figma

- Auth مصوب `0B-1` در root `168:2017` به Figma رسمی canonical شد: `passed`.
- Home مصوب `0B-2` در root `168:2018` binding/parity audit شد: `passed`.
- بدهی Operations-active برای root `168:2079` اصلاح و دوباره audit شد: `passed`.

assertion `known-figma-debt-disposition-complete` در audit مستقیم و محلی pass است. فقط debt غیر blocker «text style دقیق avatar initials» به Stage 2 منتقل شده است.

## ساختار Figma frozen

صفحه `168:1974`: `05 — Stage 0B-6 Final System Contract`؛ board `168:1975` در `2026-08-08T19:54:11.151Z` freeze و در `2026-08-08T20:06:32.118Z` audit شد. provenance نیز در `2026-08-08T20:15:41.663Z` دوباره خوانده شد.

هشت section frozen:

1. `168:1976` — System scope, provenance and gate
2. `168:1977` — Approved family references
3. `168:1978` — Foundations and components
4. `168:1979` — Shell, route and layout
5. `168:1980` — State, feedback, motion and accessibility
6. `168:1981` — Content, privacy and protected surfaces
7. `168:1982` — Responsive and desktop acceptance proofs
8. `168:1983` — Implementation stage map and owner decision

## قاعده artifact

- داده نمایشی فقط synthetic است؛
- product root نباید node ID، route path، backend source، hash، run ID یا متن مخصوص reviewer را نشان دهد؛
- فایل مستقیم و مشتق محلی باید نام، node source، ابعاد، زمان capture و SHA-256 داشته باشد؛
- metrics قبل و بعد از capture باید exact assertion set/order یکسان و canonical tree hash ثابت داشته باشد؛
- promotion evidence فقط پس از pass کامل و به‌صورت atomic انجام می‌شود؛
- Sites باید قبل و بعد deploy `custom/owner-only` بماند و probe ناشناس access را رد کند؛
- هیچ artifact ایستایی رفتار authorization، mutation، delivery، realtime، keyboard یا screen reader را اثبات نمی‌کند.

## خروجی‌های ثبت‌شده

- audit مستقیم: [`assets/figma-stage0b6-audit-metrics.json`](assets/figma-stage0b6-audit-metrics.json)، SHA-256 `7eaa85d626366ea623714fa4d22cc521bf4455434c05e72db1a4b38a9659e2ff`؛
- derivative محلی: [`final-system-contract-evidence.html`](final-system-contract-evidence.html)، SHA-256 `6bfe3fce136eb2477f6cbac99561d2e934b469324f3db3c6ca5ec60fee61d5fa`؛
- harness: [`capture-evidence.cjs`](capture-evidence.cjs)، SHA-256 `1491a6ccd536e68b38deebd4b0989ad32b62fff86d69517d0604476673758bc0`؛
- metrics محلی: [`assets/local-evidence/local-stage0b6-final-system-contract-validation-metrics.json`](assets/local-evidence/local-stage0b6-final-system-contract-validation-metrics.json)، run `stage0b6-20260808T205504009Z-c6c1b8be` و SHA-256 `6639bc3a6398bff15972c825bc33300cb43bef316d7c1e30efcc00218da844d8`؛
- export مستقیم: ۹ PNG؛ evidence محلی: دقیقاً ۷ PNG. path، node، ابعاد، bytes و hash تک‌تک در [manifest](FIGMA_SNAPSHOT_MANIFEST.json) ثبت شده‌اند؛
- baseline خارجی `/tmp/uiux-stage0b6-runtime-baseline.json`: SHA-256 `47705d3d10e0a1280b3aa37ffd2fa92b6293294ec2de85617d9007586f3343c0`، `35/35` فایل و `322/322` تست؛
- `npm run build`: pass با ۲۱۴۶ module در `24.69s`؛ `npm run guard:ui`: pass؛ runtime diff و protected-surface diff خالی؛
- Sites: project `appgprj_6a77997ed65481918d71b8f1f3db541f`، version 1، deployment `appgdep_6a779a76a0348191ba7f15b7a4fb2fd8` و URL خصوصی `https://trading-bot-uiux-stage0b6.mohsenbarari235.chatgpt.site`؛ source commit `c42eff2b6fb84ee6030a32d0e01f0b3a5fe4c982` و archive SHA-256 `e01ab7ea18a5a7d85ae3e5f39ab3f21230f23714ebd1db1de66352c8f31ee4b6`؛
- access: `custom/owner-only` با یک owner و صفر workspace/tenant group یا external visitor؛ bypass هرگز generate/request/read/use/persist/expose نشده است؛
- probe ناشناس در `2026-08-08T21:07:38Z`: root و مسیر evidence هر دو `HTTP 401` همراه `cache-control: no-store` و `referrer-policy: no-referrer`؛ environment revision `0` با صفر entry و error log در پنجره ۶۰ دقیقه‌ای صفر.

## گیت نهایی

مالک قرارداد و ادامه بی‌وقفه roadmap را تأیید کرده و Sites/provenance نیز پاس شده است. گیت `0B-6` در `2026-08-08T21:07:38Z` بسته شد؛ وضعیت جاری progression پس از closure Stage 1 و Stage 2 چنین است:

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

Stage 1 `complete` است؛ [بسته evidence](../uiux-stage1-trust-continuity/README.md) گیت fresh آن را ثبت می‌کند. Stage 2 نیز طبق [checkpoint مستقل](../WEBAPP_UI_UX_REDESIGN_V2_STAGE2_PROTECTED_DESIGN_SYSTEM_CHECKPOINT_20260809.md) با گیت فنی/protected diff، evidence و Sites `complete` است. Stage 3 مجاز و شروع‌نشده است. ادامه Stageهای 3 تا 8 نیازمند تأیید جداگانه مالک نیست اما هرکدام گیت فنی، test، protected diff و rollback خود را دارند؛ مگر اینکه مالک صریحاً توقف کند.
