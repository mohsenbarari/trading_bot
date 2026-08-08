# Stage 0B-4 validation plan and record

تاریخ: ۲۰۲۶-۰۸-۰۸

وضعیت: **شواهد فنی تکمیل شده؛ در انتظار تأیید بصری مالک محصول.** Phase 0، بسته رسمی Figma، harness محلی و Sites خصوصی ثبت شده‌اند؛ runtime implementation انجام نشده است.

## دامنه برنامه‌ریزی‌شده

- صفحه Stage: `75:2`؛ نام `03 — Stage 0B-4 Admin Users & Invitations`
- Foundations موجود: `41:2`
- Components موجود: `46:2`
- ۱۰ root موبایل با مرجع ۳۹۰×۸۴۴
- پنج proof موبایل در عرض‌های ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰
- یک proof مستقل user directory master/detail در ۱۴۴۰×۹۰۰
- state atlas برای loading/error/empty/search-empty/missing/permission/busy/clipboard/invitation-stale
- permission matrix برای مدیر میانی، مدیر ارشد، self-target و same-level target
- content-necessity، action-truth، invitation-truth، protected-surface و contrast audit
- cross-stage shell invariant مصوب `0B-2`

freeze نهایی Figma در `2026-08-08T13:43:05.564Z`، audit کامل schema 7 پاس در `2026-08-08T13:44:57.691Z` و capture مستقیم در `2026-08-08T13:45:57.660Z` ثبت شد. source node/root/proof و checksumها قطعی‌اند.

## وضعیت audit نهایی Figma — schema 7

| کنترل | نتیجه نهایی | معیار خروج |
| --- | --- | --- |
| mobile root | `10 / 10` پاس | `10 / 10` |
| responsive proof | `5 / 5` پاس | `5 / 5` |
| desktop proof | `1 / 1` پاس؛ `1440×900` | `1 / 1` و دقیقاً ۱۴۴۰×۹۰۰ |
| clipped text / geometry | صفر failure | صفر violation |
| font | صفر wrong font؛ فقط Vazirmatn | صفر؛ فقط Vazirmatn |
| unbound solid paint | صفر | صفر |
| forbidden copy | صفر hit | صفر |
| shell/protected interior | shell پاس و interior تازه بازار/پیام‌رسان صفر | صفر leak |
| semantic target | `142` هدف؛ کمینه `44×44` | عمومی حداقل ۴۴×۴۴ |
| CTA | `13` مورد؛ کمینه ارتفاع `48px` | حداقل ۴۸px ارتفاع |
| contrast | `9 / 9` پاس؛ متن حداقل ۴٫۵:۱ و focus حداقل ۳:۱ | همه جفت‌ها پاس |

## Foundation و component validation

Foundation بدون token تازه با ۶۵ variable، صفر alias شکسته و Vazirmatn reuse شد. چهار component set bounded Stage ساخته و audit شدند:

- `UIUX/Form Field` — `77:610`، دوازده variant؛
- `UIUX/Admin User Row` — `78:566`، سه variant؛
- `UIUX/Standard Invitation Row` — `80:574`، دو variant؛
- `UIUX/Decision Panel` — `81:566`، دو variant.

این Stage چهار component set و ۱۹ variant افزوده است؛ unbound solid paint و متن غیرVazirmatn هر دو صفر ثبت شدند. Code Connect به‌دلیل نبود mapping موجود و منتشرنبودن فایل library ساخته نشد؛ این موضوع runtime mapping را اثبات نمی‌کند.

## invariantهای محتوایی و سناریویی

- ورودی مدیر میانی فقط کاربران و دعوت‌ها را نشان می‌دهد.
- endpoint pending محدود به ۱۰۰ و بدون total است؛ فقط نشان غیرعددی «نیازمند رسیدگی» استفاده می‌شود و `array.length` به‌عنوان count کل نمایش داده نمی‌شود.
- فهرست جست‌وجوی پایدار دارد و role/status filter ناقص ندارد.
- row فقط هویت و metadata اثرگذار بر تصمیم را نگه می‌دارد.
- موبایل list XOR detail است و دسکتاپ همان داده را در master/detail نشان می‌دهد.
- محدودیت معامله، محدودیت تعدادی، غیرفعال‌سازی و حذف حساب از نظر عنوان، copy، confirm و پیامد جدا هستند.
- `M04` status دقیق `وضعیت: فعال ← محدود` و دقیقاً یک deadline مستقل `پایان: ۲۲ مرداد ۱۴۰۵، ساعت ۱۴:۳۰` دارد؛ تاریخ در status تکرار نمی‌شود.
- `M05` deadline دقیق `۲۲ مرداد ۱۴۰۵، ۱۴:۳۰` و سهمیه «تعداد کالای معامله‌شده» دارد؛ واژه/واحد گرم ممنوع است.
- `M06` و success غیرفعال‌سازی deadline دقیق `۱۹ مرداد ۱۴۰۵، ۱۴:۳۰` دارند.
- غیرفعال‌سازی، پیامد فوری و پیامد پس از دو روز را جدا توضیح می‌دهد؛ confirm فقط خروج مورد انتظار کاربر از کانال را با نتیجه صریحاً تأییدنشده بیان می‌کند و success فقط observable account state/deadline update را ادعا می‌کند.
- self-target برای هر ادمین و same-level super-admin برای role edit، محدودیت معامله، محدودیت تعدادی، غیرفعال‌سازی، پایان نشست‌ها و حذف read-only هستند؛ مدیر میانی هیچ **ادمین دیگری** را target نمی‌گیرد و self او visible/read-only است.
- copy دقیق M06 برابر «فوری: بازار بسته می‌شود. خروج کاربر از کانال تلگرام مورد انتظار است؛ نتیجه آن در این صفحه قابل‌تأیید نیست.» است و هیچ execution/outcome claim دیگری ندارد.
- permission atlas دقیقاً شش ردیف مستقل `middle→non-admin`، `middle→self`، `middle→other-admin`، `super→lower`، `super→self` و `super→peer-super` را machine-audit می‌کند.
- permission proof می‌تواند جدول ستونی یا نمایش فشرده گروه‌بندی‌شده باشد، اما scope مشاهده و هر شش اقدام محافظت‌شده `role edit`، `trading restriction`، `quantitative limits`، `deactivation`، `terminate-all` و `delete` باید جداگانه و آشکار enumerate شوند؛ audit ماشینی باید مقدار دقیق تک‌تک dimensionها را برای هر actor/target کنترل کند.
- deactivation آفرها را expire/collect نمی‌کند؛ این cascade فقط به delete تعلق دارد.
- role matrix دعوت دقیق است: مدیر میانی `watch/standard`، مدیر ارشد `watch/standard/police/middle` و هرگز `super`.
- مدیر میانی فقط pendingهای خودش و مدیر ارشد همه pendingها را می‌بیند/revoke می‌کند.
- نتیجه دعوت created/reused، مهلت، SMS و لینک‌های واقعاً موجود را صادقانه نشان می‌دهد.
- delivery Telegram بدون receipt ادعا نمی‌شود و URL خام در همه stateها، حتی clipboard failure، پنهان است.
- state atlas حالت‌های تعریف‌شده را با هم اشتباه نمی‌گیرد.
- حذف حساب به قرارداد canonical `0B3-M10` و الگوی نشست به `0B3-M09` ارجاع می‌دهد و flow پرریسک تکراری نمی‌سازد.
- هیچ صفحه یا رفتار داخلی بازار و پیام‌رسان در artifact وجود ندارد.
- همه نام‌ها، هویت‌ها و شماره‌های تلفن در Figma، Sites و harness synthetic هستند؛ production data count باید صفر باشد.
- navigation shell دقیقاً به‌ترتیب `خانه`، `بازار`، `پیام‌رسان`، `عملیات`، `حساب` است و `عملیات` active است.
- icon treatment همان SVG مصوب `0B-2` است؛ label موبایل حداقل `11px`، item موبایل حداقل `52px` ارتفاع، nav موبایل حداقل `78px` ارتفاع و item دسکتاپ حداقل `48×48px` است.
- این کنترل فقط shell را audit می‌کند؛ Market/Messenger interior همچنان محافظت‌شده و خارج دامنه است.

نتیجه audit schema 7: `passed` با شش permission row، ۱۴۲ target، ۱۳ CTA، ۹ contrast pair و صفر failure. harness محلی و verification فنی Sites نیز `passed` هستند.

## شواهد مستقیم Figma — نهایی

capture batch: `2026-08-08T13:45:57.660Z`

| فایل | source node | SHA-256 |
| --- | --- | --- |
| `assets/figma-admin-entry-directory-scenarios.png` | `83:2` | `22c0d82bd0d9f1b45dc46fcd2883e901f9522411eea525944d42ccfcd79f204c` |
| `assets/figma-admin-user-decision-scenarios.png` | `75:12` | `6c94393cb2c7d5c5c59beb75a44780f6da9451fef91f8f89ad0dfc89c50bbb0a` |
| `assets/figma-standard-invitation-scenarios.png` | `75:17` | `8b637f984881655914f197d0135068cb7a123a6ce4b9e3c687c017d91a835f5a` |
| `assets/figma-admin-state-permission-atlas.png` | `75:22` | `becf3a64007558698cd4d6731a3f119b4a3cbaffeb014e80f047fe7b81993054` |
| `assets/figma-responsive-and-desktop-proofs.png` | `75:27` | `7c2c0b0322eb2cf442004c3698f1570a2f5e26086836c01cec783c754e935d2a` |
| `assets/figma-desktop-user-master-detail-1440x900.png` | `89:758` | `63edf585480242b1d1b038d7754e6acfe6797699a52345537e453f483c313aea` |

metrics نهایی: `assets/figma-stage0b4-audit-metrics.json` با SHA-256 برابر `5c57aa316f0cdf5053eef8e29f9f6f19be5f29791057559b277c2e8fc2b09a1c`.

## Sites preview — منتشرشده، خصوصی و source-bound

| فیلد | وضعیت |
| --- | --- |
| URL خصوصی | [trading-bot-uiux-stage0b4.mohsenbarari235.chatgpt.site](https://trading-bot-uiux-stage0b4.mohsenbarari235.chatgpt.site) |
| عنوان / slug | `Trading Bot UI/UX — Stage 0B-4` / `trading-bot-uiux-stage0b4` |
| project | `appgprj_6a772e6ffef481918c82f9c70b4c71c8` |
| source canonical | Figma file `z8jgJxST4O2APzWnlyP9gv`؛ Sites صرفاً derivative خصوصی |
| source commit | `0874c43781805bf1404226cc1948485ebdbb04f1` |
| version | `3`؛ `appgprj_6a772e6ffef481918c82f9c70b4c71c8~appgver_c31913427d2081918c4e4b3a290620cb` |
| deployment | `appgdep_6a77396e6a5c8191b9945deaf884107d` روی `site---6a772e6ffef481918c82f9c70b4c71c8` |
| زمان انتشار موفق | `2026-08-08T14:13:17.018345Z` |
| archive | `901120` بایت، `28` فایل، SHA-256 `eab92d01cea68f922c80bd90c0229aa16088367f55a7d8701a457498ce0a85ce` |
| بازبینی drift | `passed_artifact_and_source_bound`؛ تأیید بصری signed-in مالک pending |

access policy بلافاصله پیش و پس از deploy دوباره خوانده شد: `access_mode=custom`، نقش جاری `owner`، یک کاربر مجاز و آن هم مالک، صفر گروه و صفر بازدیدکننده خارجی. probe ناشناس در `2026-08-08T14:13:54Z` پاسخ `HTTP 401`، `Cache-Control: no-store`، `Referrer-Policy: no-referrer` و عنوان `Sign in required` گرفت و SIWC owner gate را تأیید کرد. بنابراین render ناشناس ادعا نمی‌شود. bypass token درخواست نشد و signed-in live content دور زده/واکشی نشد؛ مالک می‌تواند URL بالا را برای تأیید بصری باز کند.

verification بسته و deploy:

- production build: پاس؛
- `npm audit --audit-level=high`: صفر vulnerability؛
- delegation درخواست Worker به `ASSETS`: پاس؛ نبود binding به‌درستی `503` برمی‌گرداند؛
- `dist/server/index.js`، `dist/client` و `dist/.openai/hosting.json`: حاضر؛
- HTML عمومی و HTML داخل built archive: byte-identical با SHA-256 برابر `ca44c01da79ce479d34efb22f89e39c3b8f1c1dda9008c42fc7c3658c7178ec7`؛
- چهار فونت محلی Vazirmatn: حاضر و byte-identical؛
- scan بسته: بدون raw external URL، موبایل unmasked، credential pattern، source map، env/key یا log؛
- query `errors-only` برای deployment نهایی در پنجره ۱۵ دقیقه‌ای: صفر worker error event.

دو تلاش اولیه deployment در packaging preflight متوقف و با اصلاح بسته supersede شدند؛ deployment نهایی بالا موفق است و blocker بازی باقی نمانده. به‌دلیل build ID تصادفی Next، whole-dist hash معیار reproducibility نیست؛ archive ذخیره‌شده و hashهای semantic artifact مرجع‌اند.

Sites شاهد ثانویه و مشتق‌شده است. موفقیت interaction در preview، authorization، mutation، delivery یا رفتار runtime محصول را اثبات نمی‌کند و محرمانگی آن به حفظ gate owner-only وابسته است.

## harness محلی مشتق‌شده — پذیرفته‌شده

harness پس از تثبیت Figma این موارد را به‌صورت fail-closed کنترل کرد:

- width sweep دقیق ۳۶۰/۳۷۵/۳۹۰/۴۱۴/۴۳۰؛
- نبود overflow افقی و عمودی در rootهای محصول؛
- حداقل target و CTA؛
- بارگذاری واقعی چهار face Vazirmatn؛
- list XOR detail موبایل و master/detail دسکتاپ؛
- وجود recoveryهای state atlas و permission matrix؛
- جدایی چهار action مدیریتی؛
- نبود role/status filter، گزینه دائمی، role دعوت `super`، delivery claim، URL خام و copy ممنوع؛
- انطباق roleهای دعوت و scope pending با matrix دقیق مدیر میانی/مدیر ارشد؛
- نبود total عددی pending از `array.length` و وجود نشان غیرعددی «نیازمند رسیدگی»؛
- read-onlyبودن کامل self/same-level برای actionهای حساس و محدودبودن success deactivation فقط به observable state/deadline update؛
- نبود ادعای ثبت/آغاز یا تکمیل channel removal و notification delivery؛
- نبود expire/collect آفر در deactivation و وجود آن فقط در قرارداد canonical delete؛
- استفاده انحصاری از هویت و شماره تلفن synthetic در همه artifactها؛
- ترتیب/label/active-state/SVG و حداقل اندازه‌های shell مصوب `0B-2` در همه proofهای واجد navigation؛
- نبود interior بازار/پیام‌رسان؛
- ابعاد دقیق screenshot دسکتاپ ۱۴۴۰×۹۰۰؛
- capture و metrics هم‌نسخه با checksum قابل‌تأیید.

provenance و نتیجه اجرای نهایی artifactهای محلی:

- فایل HTML: `admin-users-invitations-evidence.html`
- اسکریپت capture: `capture-evidence.cjs`
- metrics: `assets/local-evidence/local-admin-users-invitations-validation-metrics.json`
- metrics timestamp: `2026-08-08T13:46:25.656Z`
- run ID: `2251888-1786196776466`
- assertion count/pass/failure: `25 / 25 / 0`
- page errors: `0`
- action targets: `176` با کمینه `44×44px`
- CTA: `14` با کمینه ارتفاع `48px`
- font: چهار face بارگذاری‌شده Vazirmatn Evidence
- responsive widths: `360 / 375 / 390 / 414 / 430`، همگی دقیقاً `844px` ارتفاع و بدون overflow
- desktop screenshot: دقیقاً `1440×900`
- capture count: `7`
- metrics SHA-256: `5219d9c90d3c33a36c51b3a461e8da753c7f144a719ae7b176771a3812f0c41b`
- pre/post assertionها یکسان؛ canonical DOM هنگام capture بدون تغییر

## baseline runtime

تست‌های runtime این Stage فقط رفتار موجود را ثبت می‌کنند و پیاده‌سازی طرح تازه را اثبات نمی‌کنند. baseline تازه روی HEAD جاری، روی ۱۰ فایل مرتبط به‌صورت سریالی اجرا شد و `91 / 91` تست در Vitest duration برابر `35.59s` با exit code صفر پاس شد:

`npm run test:unit:run -- --maxWorkers=1 src/components/AdminPanel.test.ts src/components/CreateInvitationView.test.ts src/components/UserManager.test.ts src/components/UserProfile.test.ts src/components/ui/AppPrimitives.test.ts src/composables/useUserProfileTiming.test.ts src/router/index.test.ts src/utils/currentUser.test.ts src/views/AdminView.test.ts src/components/BottomNav.test.ts`

دو warning غیرشکست مربوط به staleبودن `caniuse-lite` و مسیر تست شناخته‌شده deprecation تاریخ نامعتبر Moment بودند.

این نتیجه مجوز تغییر runtime یا ادعای پوشش طراحی تازه نیست. E2E و backend matrix در Phase 0 فقط inventory شدند و به‌علت احتمال تغییر state اجرا نشدند.

## حدود ادعا

شواهد static و مشتق‌شده نمی‌توانند API واقعی، authorization، forwarding cross-server، Bot authority، reset سهمیه، deadline واقعی، session revocation، تحویل SMS/Telegram، clipboard، router history، focus management، screen reader، keyboard موبایل، شبکه کند یا failure race را اثبات کنند. این موارد در Stageهای اجرایی ۱، ۴، ۶، ۷ و ۸ validation می‌شوند.

count authoritative/pagination صف pending و enforcement سمت سرور برای self-target، same-level target و scope مدیر میانی carry-forward صریح Stage اجرایی‌اند؛ وضعیت فعلی backend با contract طراحی یکسان فرض نمی‌شود.

شناسه‌ها، audit/export پس از refreeze، provenance نهایی Figma، harness محلی و Sites URL/source version/archive/deployment/drift review ثبت و تطبیق داده شده‌اند؛ شواهد فنی Stage 0B-4 `complete` است. تأیید بصری مالک در محیط signed-in هنوز pending است، `0B-5` آغاز نشده و تغییر runtime تا تأیید صریح `0B-6` ممنوع می‌ماند.
