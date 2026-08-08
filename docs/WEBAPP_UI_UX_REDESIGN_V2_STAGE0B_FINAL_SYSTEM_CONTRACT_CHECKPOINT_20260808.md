# Stage 0B-6 — قرارداد نهایی سیستم و پذیرش

تاریخ: ۲۰۲۶-۰۸-۰۸

وضعیت: **تکمیل فنی Stage `0B-6` و مجوز Stage 1**. `ownerSystemContractApproval.status: approved` در `2026-08-08T20:57:28.073Z`، `continuousProgressionAuthorized: true` و closure فنی/Sites در `2026-08-08T21:07:38Z` ثبت شده است. `runtimeImplementationAuthorized: true` و `nextAuthorizedRuntimeStage: Stage 1` است؛ Stage 1 هنوز runtime edit شروع‌شده ندارد.

شاخه: `condidate/webapp-ui-ux-redesign-v2`، ساخته‌شده مستقیم از `main`

## ۱. هدف checkpoint

این checkpoint تصمیم‌های تأییدشده `0B-1` تا `0B-5` را به یک قرارداد واحد و قابل‌ممیزی برای کل وب‌اپ غیر بازار/پیام‌رسان تبدیل می‌کند. خروجی باید نشان دهد رنگ، تایپوگرافی، فاصله، شعاع، پوسته، ناوبری، state، feedback، motion، حریم خصوصی و اقتصاد اطلاعات در همه خانواده‌ها یک معنا دارند و برای ورود مرحله‌ای به runtime ابهام حل‌نشده‌ای باقی نمانده است.

Figma canonical، audit و export مستقیم، harness محلی، baseline خواندنی runtime و Sites خصوصی source-bound تکمیل و پاس شده‌اند. هیچ تصویر یا harness ایستایی نباید به‌عنوان اثبات رفتار runtime خوانده شود. مالک ادامه بی‌وقفه roadmap را تأیید کرده و Stage 1 اکنون next authorized است، اما این checkpoint هیچ runtime edit از Stage 1 را ادعا نمی‌کند.

## ۲. منبع canonical و سابقه تأیید

| checkpoint | منبع مصوب | وضعیت مالک |
| --- | --- | --- |
| `0B-1` ورود، دعوت و ثبت‌نام | [checkpoint ورود](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_AUTH_CHECKPOINT_20260718.md) و `docs/uiux-stage0b-auth/` | تأییدشده و در root `168:2017` canonical شده است |
| `0B-2` خانه و پوسته | [checkpoint خانه](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_HOME_SHELL_CHECKPOINT_20260808.md) و صفحه Figma `12:2` | تأییدشده؛ binding/parity آن در root `168:2018` پاس شده است |
| `0B-3` عملیات و workspace | [checkpoint عملیات](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_OPERATIONS_WORKSPACES_CHECKPOINT_20260808.md) و صفحه Figma `55:2` | تأییدشده؛ بدهی ناوبری در root `168:2079` رفع و audit شده است |
| `0B-4` کاربران و دعوت‌های ادمین | [checkpoint مدیریت](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ADMIN_USERS_INVITATIONS_CHECKPOINT_20260808.md) و صفحه Figma `75:2` | تأیید بصری صریح |
| `0B-5` حساب، پروفایل، امنیت و اعلان | [checkpoint حساب](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ACCOUNT_PROFILE_SECURITY_NOTIFICATIONS_CHECKPOINT_20260808.md) و صفحه Figma `117:2` | تأیید بصری صریح |

Figma رسمی با file key برابر `z8jgJxST4O2APzWnlyP9gv` منبع editable نهایی طراحی است. Sites فقط مشتق خصوصی و بسته محلی فقط evidence بازتولیدپذیر است؛ هیچ‌کدام جای Figma canonical را نمی‌گیرند.

## ۳. مرز کار و سه گیت Figma `0B-6`

داخل محدوده:

- هم‌بندی پنج خانواده نماینده در یک قرارداد مشترک؛
- inventory کامل ۲۹ route فعلی و مالکیت catch-all برنامه‌ریزی‌شده؛
- قرارداد `SYS-01..SYS-14`، هشت تصمیم مالک و ۳۲ assertion fail-closed؛
- ماتریس ضرورت محتوا و traceability مرحله‌های `1..8`؛
- Figma canonical، export مستقیم، harness محلی fail-closed و Sites خصوصی owner-only؛
- baseline خواندنی runtime، بدون تغییر فایل محصول.

خارج از محدوده:

- هر تغییر frontend، backend، bot، API، database، route یا deployment محصول؛
- بازطراحی یا ممیزی داخلی بازار و پیام‌رسان؛
- رفع زودهنگام carry-forwardهای Stageهای اجرایی؛
- ادعای authorization، mutation، delivery، realtime، focus، screen reader یا network behavior بر اساس تصویر ایستا.

سه مورد زیر **گیت پذیرش `0B-6`** بودند و در Figma frozen بسته شده‌اند:

1. `Auth canonicalization`: در root `168:2017` با fact parity و اتصال به مرجع رسمی: `passed`.
2. `Home binding audit`: در root `168:2018` با صفر alias/binding شکسته و صفر detached product instance: `passed`.
3. `Operations navigation debt`: در root `168:2079` با اصلاح variantها و audit focus/layout/style/target/label: `passed`.

بدهی غیر blocker text style دقیق avatar initials به Stage 2 منتقل شده و در evidence پنهان یا false-pass نشده است.

## ۴. هشت تصمیم قفل‌شده مالک

| شناسه | تصمیم |
| --- | --- |
| `OD-01` | Figma رسمی منبع canonical است؛ Auth به آن canonical می‌شود و Home پیش از freeze نهایی binding audit/rebind دارد. |
| `OD-02` | inventory نشست‌ها `local per-server` است؛ UI فهرست ادغام‌شده یا تضمین cross-server اختراع نمی‌کند. |
| `OD-03` | reconnect اعلان، آخرین ۵۰ رکورد را دوباره می‌خواند و با شناسه dedupe می‌کند؛ این پنجره total یا تضمین history کامل نیست. |
| `OD-04` | PII در نمای عمومی masked/hidden است؛ داده کامل فقط برای self یا جزئیات مدیریتی اختصاصی با مجوز واقعی backend دیده می‌شود. |
| `OD-05` | اقدام وب که authority آن در bot/server مرجع است باید به مرجع forward شود یا fail-closed برگردد؛ موفقیت محلی خیالی مجاز نیست. |
| `OD-06` | موفقیت delivery فقط با receipt معتبر همان کانال اعلام می‌شود؛ قبول درخواست یا sync رکورد معادل تحویل نیست. |
| `OD-07` | V2 به routeهای مجاز scope می‌شود؛ token/component/CSS مشترک نباید به interior محافظت‌شده بازار و پیام‌رسان نشت کند. |
| `OD-08` | مالک در `2026-08-08T20:57:28.073Z` قرارداد و ادامه بی‌وقفه roadmap را تأیید کرد؛ Stage 1 فقط پس از closure فنی/Sites شروع می‌شود و هیچ Stageای پیش از گیت فنی مالک خودش «شروع‌شده» تلقی نمی‌شود. |

## ۵. قرارداد واحد `SYS-01..SYS-14`

جزئیات الزام‌آور در [DESIGN_CONTRACT](uiux-stage0b-final-system-contract/DESIGN_CONTRACT.md) ثبت شده است.

| شناسه | موضوع | نتیجه الزام‌آور |
| --- | --- | --- |
| `SYS-01` | دامنه محافظت‌شده | interior بازار و پیام‌رسان frozen؛ فقط shell پیرامونی داخل دامنه است. |
| `SYS-02` | جهت و typography | «مالی مدرن»، فقط Vazirmatn و foundation مصوب. |
| `SYS-03` | خلوتی | هر واحد پیش‌فرض باید اثر مشخص بر تصمیم، اقدام، وضعیت ضروری یا ریسک داشته باشد. |
| `SYS-04` | mobile-first و parity | مرجع `390×844`، sweep پنج عرض و desktop بدون واقعیت تازه. |
| `SYS-05` | shell، IA و route | پنج مقصد shell، ۲۹ route فعلی و catch-all system-owned با recovery روشن. |
| `SYS-06` | حقیقت state | loading/empty/error/offline/stale/busy/success/failure جای یکدیگر نمی‌نشینند. |
| `SYS-07` | تداوم context | back/deep link/query/filter/scroll/input تا حد لازم حفظ می‌شوند. |
| `SYS-08` | اقدام حساس | authority، confirm، busy guard و feedback نزدیک مبدأ الزامی است. |
| `SYS-09` | مجوز و PII | backend مرجع حقیقت است؛ افشای عمومی حداقلی و نقش‌محور است. |
| `SYS-10` | cross-platform و سهمیه | delivery بدون receipt موفق نیست؛ محدودیت تعدادی دائمی باید مقدار محدود و enforceable داشته باشد. |
| `SYS-11` | نشست | local per-server، بدون نمایش `home_server` و بدون ادعای merge. |
| `SYS-12` | اعلان و Push | آخرین ۵۰ total نیست، tab دوم «سایر»، item بدون route غیرتعاملی و ۹ state Push صادقانه‌اند. |
| `SYS-13` | a11y و motion | target `44`، CTA `48`، label `11`، contrast متن `4.5:1`، focus `3:1`/stroke `3` و motion `140/180ms` با reduced-motion. |
| `SYS-14` | evidence و rollout | طراحی، runtime proof نیست؛ مهاجرت فقط مرحله‌ای، route-scoped، قابل rollback و با گیت مستقل است. |

## ۶. inventory route

router فعلی دقیقاً ۲۹ route دارد. قرارداد route، shell، canonical/legacy و owner هر recovery در [DESIGN_CONTRACT](uiux-stage0b-final-system-contract/DESIGN_CONTRACT.md#قرارداد-route-و-shell) قفل شده است. route برنامه‌ریزی‌شده `/:pathMatch(.*)*` یک catch-all system-owned برای 404/recovery است و به تعداد ۲۹ route baseline افزوده نشده است.

مسیرهای `/settings` و `/notifications` legacy هستند و پس از مجوز runtime به‌ترتیب باید به `/account/security` و `/account/notifications` canonical شوند. این checkpoint خود route را تغییر نمی‌دهد.

## ۷. قرارداد ۳۲ assertion قطعی

ترتیب زیر جزئی از schema اعتبارسنجی است و تغییر نام یا ترتیب، نیازمند تصمیم مستند است:

1. `owner-approval-0b1-through-0b5-recorded`
2. `canonical-source-registry-complete`
3. `canonical-source-references-resolve`
4. `approved-source-fact-parity`
5. `stage0b6-contract-only-no-new-feature-facts`
6. `runtime-diff-empty`
7. `modern-finance-direction-locked`
8. `font-vazirmatn-only`
9. `foundation-inventory-65-9-2`
10. `broken-variable-aliases-zero`
11. `component-inventory-12-sets-56-variants-with-delta`
12. `product-proof-detached-instances-zero`
13. `known-figma-debt-disposition-complete`
14. `five-mobile-family-references-complete`
15. `mobile-reference-roots-390x844`
16. `responsive-widths-360-375-390-414-430`
17. `desktop-layout-archetypes-complete`
18. `desktop-fact-parity`
19. `no-product-overflow-or-clipping`
20. `touch-targets-44`
21. `primary-cta-height-48`
22. `navigation-label-11`
23. `text-contrast-45`
24. `focus-contrast-3-stroke-3`
25. `shell-route-layer-contract-complete`
26. `common-state-feedback-contract-complete`
27. `motion-reduced-motion-contract-complete`
28. `content-necessity-inventory-complete`
29. `reviewer-metadata-absent-from-product-roots`
30. `synthetic-identities-and-forbidden-copy-clean`
31. `protected-interiors-absent`
32. `implementation-gate-and-static-limits-explicit`

هر ۳۲ assertion در audit مستقیم Figma و harness محلی نهایی، با exact set/order، pre/post equality و artifactهای hash-bound پاس شده‌اند. assertion شماره ۱۱ عمداً inventory نهایی `12 sets / 56 variants` و delta دو variant Home-active را ثبت می‌کند.

## ۸. ساختار frozen Figma و evidence

صفحه `168:1974` با نام `05 — Stage 0B-6 Final System Contract` و board `168:1975` در فایل رسمی ساخته شد. freeze در `2026-08-08T19:54:11.151Z`، audit در `2026-08-08T20:06:32.118Z` و reread provenance در `2026-08-08T20:15:41.663Z` ثبت شده است. هشت section:

1. `168:1976` — System scope, provenance and gate
2. `168:1977` — Approved family references
3. `168:1978` — Foundations and components
4. `168:1979` — Shell, route and layout
5. `168:1980` — State, feedback, motion and accessibility
6. `168:1981` — Content, privacy and protected surfaces
7. `168:1982` — Responsive and desktop acceptance proofs
8. `168:1983` — Implementation stage map and owner decision

پنج root خانواده موبایل `168:2017 / 168:2018 / 168:2079 / 168:2163 / 168:2171` دقیقاً `390×844` هستند؛ پنج proof عرض در `173:2279 / 173:2341 / 173:2403 / 173:2472 / 173:2534` و proof دسکتاپ `173:2600` دقیقاً `1440×900` است. ۹ export مستقیم و audit JSON روی دیسک hash-bound هستند. preview مشتق نیز در Sites خصوصی owner-only source-bound شده است.

## ۹. نتیجه evidence فنی فعلی

- audit مستقیم Figma: `32/32`؛ SHA-256 فایل metrics برابر `7eaa85d626366ea623714fa4d22cc521bf4455434c05e72db1a4b38a9659e2ff`؛
- inventory: `65` variable، `9` text style، `2` effect، `12` component set و `56` variant، صفر broken alias/binding و صفر detached product instance؛
- harness نهایی: run `stage0b6-20260808T205504009Z-c6c1b8be`، پایان `2026-08-08T20:55:13.301Z`، `32/32` و metrics SHA-256 `6639bc3a6398bff15972c825bc33300cb43bef316d7c1e30efcc00218da844d8`؛
- ثبات capture: DOM قبل/بعد `c974469f9240d756449587e577cb965387634577b0d873263471aa411d88680c` و audit قبل/بعد `f6a7ed5b95871475ff96d31d8b6fe73fb89d6caaef947cea8f8b046f364d0955`؛
- semantic hardening: parity متن/action هر پنج family، Home خالی و آرام responsive، desktop facts/actions، Auth `LTR` با border آبی `2px` و input قابل‌ویرایش، selected row متمایز با `aria-current=true` و drift صفر؛
- baseline runtime: artifact خارجی `/tmp/uiux-stage0b6-runtime-baseline.json` با SHA-256 `47705d3d10e0a1280b3aa37ffd2fa92b6293294ec2de85617d9007586f3343c0`، `35/35` فایل و `322/322` تست، صفر fail/skip؛
- build: `npm run build` با ۲۱۴۶ module در `24.69s` پاس؛ `npm run guard:ui` پاس؛ runtime diff و protected-surface diff خالی؛
- Sites: project `appgprj_6a77997ed65481918d71b8f1f3db541f`، source commit `c42eff2b6fb84ee6030a32d0e01f0b3a5fe4c982`، version 1 و deployment موفق `appgdep_6a779a76a0348191ba7f15b7a4fb2fd8` در `2026-08-08T21:07:15.893642Z`؛
- URL خصوصی: `https://trading-bot-uiux-stage0b6.mohsenbarari235.chatgpt.site`؛ policy برابر `custom/owner-only`، یک owner و صفر group/external visitor؛
- archive source-bound: `401983` byte و SHA-256 `e01ab7ea18a5a7d85ae3e5f39ab3f21230f23714ebd1db1de66352c8f31ee4b6`؛ normalized Sites tar برابر ۲۷ فایل/`931840` byte/SHA-256 `db0b581048f8033cc4d19f39e5d685ab4e2c4df5e03568c6e95922d805ee0288`؛
- probe ناشناس `2026-08-08T21:07:38Z`: root و evidence هر دو `HTTP 401` با `no-store` و `no-referrer`؛ bypass هرگز generate/request/read/use/persist/expose نشده است؛
- `npm verify`، build، `audit:dist` و npm audit نهایی پاس با صفر vulnerability؛ environment خالی و error log پنجره ۶۰ دقیقه‌ای صفر؛
- وضعیت: technical/Sites complete؛ Stage 1 مجاز و هنوز شروع‌نشده است.

## ۱۰. carry-forward مرحله‌های اجرایی

[RUNTIME_TRACEABILITY_MATRIX](uiux-stage0b-final-system-contract/RUNTIME_TRACEABILITY_MATRIX.md) مالک هر تعهد را مشخص می‌کند:

- Stage 1: حقیقت state، recovery، busy و feedback؛
- Stage 2: Design System V2 route-scoped و guard عدم نشت؛
- Stage 3: shell، Auth canonical و catch-all؛
- Stage 4: خانه، عملیات، حساب، امنیت، حافظه و اعلان؛
- Stage 5: workspace مشتری/حسابدار و حفظ context؛
- Stage 6: مدیریت، دعوت و پروفایل با authority/PII؛
- Stage 7: motion، reduced-motion، keyboard، focus و screen reader؛
- Stage 8: ماتریس پذیرش، visual freeze، rollout و rollback.

این traceability مجوز پیشاپیش هیچ Stage را صادر نمی‌کند.

## ۱۱. گیت پذیرش و حدود ادعا

پذیرش نهایی فقط وقتی ممکن است که:

- هر ۳۲ assertion در audit مستقیم و harness محلی پاس شود؛
- سه گیت Auth/Home/Operations بسته شوند؛
- Figma، export، metrics، manifest، Sites خصوصی و baseline runtime source-bound و قابل راستی‌آزمایی باشند؛
- diff runtime خالی و protected-surface diff خالی باشد؛
- تأیید مالک ثبت شده باشد؛ این گیت در `2026-08-08T20:57:28.073Z` بسته شده است؛
- Sites و source binding نهایی نیز پاس و ثبت شوند؛ این شرط بسته شده است.

مالک قرارداد و ادامه بی‌وقفه roadmap را تأیید کرده و همه گیت‌های فنی `0B-6` بسته شده‌اند. اکنون:

```text
ownerSystemContractApproval.status = approved
ownerSystemContractApproval.approvedAt = 2026-08-08T20:57:28.073Z
continuousProgressionAuthorized = true
runtimeImplementationAuthorized = true
nextAuthorizedRuntimeStage = Stage 1
stage1RuntimeWorkStarted = false
```

Stage 1 next authorized است؛ اجرای آن در Stage بعدی و با گیت فنی مستقل ثبت خواهد شد.

## ۱۲. فهرست بسته

- [README](uiux-stage0b-final-system-contract/README.md)
- [VALIDATION](uiux-stage0b-final-system-contract/VALIDATION.md)
- [DESIGN_CONTRACT](uiux-stage0b-final-system-contract/DESIGN_CONTRACT.md)
- [CONTENT_NECESSITY_MATRIX](uiux-stage0b-final-system-contract/CONTENT_NECESSITY_MATRIX.md)
- [RUNTIME_TRACEABILITY_MATRIX](uiux-stage0b-final-system-contract/RUNTIME_TRACEABILITY_MATRIX.md)
- [PROTECTED_SURFACE_MANIFEST](uiux-stage0b-final-system-contract/PROTECTED_SURFACE_MANIFEST.json)
- [FIGMA_SNAPSHOT_MANIFEST](uiux-stage0b-final-system-contract/FIGMA_SNAPSHOT_MANIFEST.json)
