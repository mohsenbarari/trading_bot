# Stage 8 — ثبت نهایی پذیرش و مرز عرضهٔ مرحله‌ای

تاریخ: ۲۰۲۶-۰۸-۱۵

وضعیت: **`closed-owner-aesthetic-approved`**

شاخهٔ فنی جاری: `main` ادغام‌شدهٔ Market A+C. مرجع فنی جاری رسید V2
`STAGE8_FULL_ACCEPTANCE_EXECUTION_RECEIPT_V2.json` است. بستن پذیرش در
`STAGE8_FINAL_ACCEPTANCE_CLOSURE.json` پس از تأیید صریح زیبایی مالک ثبت شد.
رسید V1 تاریخی به‌خاطر نقص evidence/fixture superseded است. Gate A v3 روی
`02162106` پیش از Market، superseded و non-promotable است.
`acceptanceAuthority=true` فقط برای پذیرش UI/UX مرحلهٔ ۸.

شاخهٔ تاریخی بستهٔ draft: `condidate/webapp-ui-ux-redesign-v2`

source اصلاح shared-dependency: `82cb016e`

source canonicalization کامپوننتی: `7588d9c20b995244197d8de09392dd6a5f61b195`

source تاریخی بازیابی محدود directory/profile: `4415b7431a6b67965d24c44f6f9f0e59e48ed422`

(record تاریخیِ untouched است؛ rebaseline جاریِ مستقل زیر ثبت شده است)

source محدود route-first directory-transition: `31c69d5a5d2fb1e2c08d9647473d3612b9d85629`
(evidence-only؛ بدون افزایش full matrix)

source محدود invitation-presentation: `4beeade2f3aae4964f1964dedc00f47dfbcd0c05`
(evidence-only و nonpromotable؛ بدون افزایش full matrix)

source محدود NONE-route typography: `338918d56f57f7cb974a501b1c43cc22d6afc2b5`
(evidence-only؛ بدون افزایش full matrix)

source محدود public/focused auth viewport-containment: `55f00218295d7aa6f52f75b664544318684d2826`
(evidence-only؛ بدون افزایش full matrix)

source rebaseline جاری directory/profile: `601b4005d80ef265afaaa6a06a43b48c44c7ca90`
(evidence-only؛ مستقل از receipt تاریخی `4415b743` و بدون افزایش full matrix)

source postcommit roving-focus workspace: `d1e8ecd5a524a03c73b67e531edab363479a32b0`
(evidence-only؛ بدون افزایش full matrix)

source محدود account long-text wrapping: `8082d8dd6352154b52e86ca6511e27464e072b13`
(evidence-only؛ بدون افزایش full matrix)

source محدود Account Hub singleton action-grid: `656cf6c3b62111c5c7bae458e3ea6f61fd8af788`
(evidence-only؛ بدون افزایش full matrix)

source محدود notification route-affordance: `95ef7aa768f833c8e8b954d38b36674a77a304a9`
(evidence-only؛ بدون افزایش full matrix)

source محدود CreateChannel help-popover placement: `0d7f276006deb7f97d20ba07e6f9ecb4d1b48a79`
(evidence-only؛ بدون افزایش full matrix)

## ۱. مجوز و حد آن

دستور مالک برای ادامهٔ Stage 8، یکپارچگی و زیباسازی UI/UX با کنترل ایمنی، و ثبت رهگیری
دسترسی/شواهد محدود را مجاز کرد.

این مجوز **merge به main، staging deploy، production deploy، یا Sites محصول** نیست.

`stage8CompleteAuthority=true` فقط برای پذیرش UI/UX مرحلهٔ ۸. این checkpoint پایان roadmap یا مجوز merge/staging/production/Sites نیست.

## ۲. ماتریس پذیرش

منبع: `docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json`

- پروفایل دسترسی: مهمان، تماشا، عادی، پلیس، مشتری، حسابدار، مالک/سرگروه، مدیر میانی و مدیر ارشد
- مسیر: هر ۳۰ route واقعی، شامل catch-all `system-recovery`
- viewport موبایل: ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴، ۴۳۰
- viewport تطبیقی: ۷۶۸، ۱۰۲۴، ۱۴۴۰
- state: loading، empty، normal، dense، error، slow، offline، stale
- تعامل: touch، keyboard، zoom، reduced-motion
- محیط هدف بعدی: مرورگر موبایل، PWA، Telegram WebView غیرپیام‌رسان

نسخهٔ ۳، ۳۰ × ۹ = ۲۷۰ نتیجهٔ normal-case router/guard را از source رهگیری می‌کند و چهار
deep-link denied مدیر میانی (`/admin/channels`، `/admin/commodities`، `/admin/messages` و
`/admin/system`) را نیز به canonicalization کامپوننتی `AdminView → /admin` متصل می‌کند.
تعداد سلول‌های access اجراشدهٔ رسمی محلی ۲۷۰ است. گسترش viewport/state/interaction/environment
روی اجرای رسمی V2 انجام شد (۹۶۰ شناسه؛ ۱۳۰ N/A منبع‌محور). بازار A+C و پیام‌رسان disposition
Stage 8 حفظ شده‌اند. تأیید زیبایی مالک ثبت شد. این checkpoint مجوز
staging/production/Sites نیست.

## ۳. شواهد محدود 8A/8B/auth-containment/directory-profile/account/notification و مرجع طراحی

منبع redacted: `docs/uiux-stage8-acceptance-rollout/STAGE8A_EXECUTION_RECEIPTS.json`

- slice دسترسی/shell محلی و synthetic در `390×844`: شش profile × هشت scenario، ۴۸/۴۸ cell و
  ۵۰ assertion؛ این مورد full matrix نیست و به `executedFullMatrixCellCount` افزوده نمی‌شود.
- slice تاریخی directory/profile محلی و synthetic در source `4415b743`: `/profile`، `/users/:id`،
  `/admin/users` و `/admin/users/:id` در ۳۶۰/۳۹۰/۴۱۴/۴۳۰/۱۴۴۰ بررسی شدند؛ این مورد full
  role×route acceptance نیست و یک record تاریخیِ untouched است. rebaseline جاری در `601b4005`
  receipt مستقل دارد و این record را overwrite، replace یا promote نمی‌کند.
- slice رفتاری route-first directory در source `31c69d5a`: چهار scenario local/synthetic production
  browser (pointer و Enter در ۳۹۰، pointer در ۱۴۴۰، و `/admin/commodities` برای مدیر میانی) با
  ۳۳ assertion اجرا شد. سه گذار directory هرکدام دقیقاً یک `GET /api/users/` با پاسخ ۲۰۰ کامل و
  non-aborted، صفر requestfailed/`ERR_ABORTED` و حداکثر یک UserManager/list مرئی داشتند؛ deep-link
  denied به `/admin` canonical شد و CommodityManager یا commodity API نداشت. Telegram probe محلی
  intercept شد و external transport مشاهده نشد. این slice evidence-only است و cell پذیرش کامل نیست.
- slice invitation-presentation در source `4beeade2`: ۴۴ assertion روی `390×844` و `1440×900`
  برای بازگشت focus پس از Cancel/Escape، overflow، copy و end-state حذف mock اجرا شد و ۲/۲
  viewport-flow گذشت. DELETE روی transport mock بود و artifact paired Chromium abort دیده شد؛
  بنابراین این receipt nonpromotable است و هیچ completion سرور واقعی را attest نمی‌کند.
- slice typography در source `338918d5`: Vazirmatn و `font-synthesis:none` فقط روی route vnode
  با `protection=NONE` اعمال شدند؛ base `font-sans` و FULL/MIXED، از جمله concurrent fade، تغییر
  نکردند. ۱۲ sample مسیر و ۴ probe cross-boundary (جمعاً ۱۶ scenario) با صفر page error،
  focused `40/40` و full serial `155 files / 1759 tests / 0 failed` ثبت شد. این browser receipt
  local/synthetic است؛ Telegram block، WebSocket 403 محلی و Market offers ساده‌شده fixture-only
  هستند و این مورد full browser یا full matrix acceptance نیست.
- slice auth-containment در source `55f00218`: `AuthFlowShell` فقط با opt-in صریح در Login،
  invitation، web-register و setup-password، `100vh` سپس `100dvh` می‌گیرد. ۱۴ scenario
  local/synthetic شامل چهار flow در `390×844` و `1440×900`، همان چهار flow در reduced-motion
  `390×844`، و SystemRecovery credentialed در هر دو viewport است. چهار opt-in viewport را پر
  کردند، focus-visible/نبود overflow گذشت و page error صفر بود؛ SystemRecovery modifier نگرفت و
  daily navigation بدون collision ماند. The 14-case capture recorded 10 WebSocket-related fixture
  console diagnostics (6 setup-password; 4 credentialed SystemRecovery) with no backend; excluded
  from layout/interaction conclusions and no clean-console claim. این slice evidence-only است و
  full matrix یا protected behavior را attest نمی‌کند.
- rebaseline جاری directory/profile در source `601b4005`: ۴۰ scenario local/synthetic شامل ۲۰
  route×viewport normal، هشت loading/error recovery، دو lifecycle، یک keyboard journey، چهار
  reduced-motion، چهار CDP 2× و یک probe harness-only اجرا شد. `/profile`، `/users/:id`،
  `/admin/users` و `/admin/users/:id` در `360×740`، `390×844`، `414×896`، `430×932` و
  `1440×900` پوشش داشتند؛ source/tree/hash و dist پیش/پس از run یکسان و clean بودند. overflow
  document/app، control بدون نام، page error، failed/external request و unknown API غیرمنتظره
  صفر بود؛ lifecycle حداکثر یک UserManager مرئی/mounted و یک user-list request کامل/non-aborted
  در هر گذار پوشیده‌شده داشت و keyboard return focus روی control دارای label ماند. شش console
diagnostic موردانتظار از fixtureهای injected 404/500، شامل retry warning، طبقه‌بندی و از
نتیجهٔ layout/interaction کنار گذاشته شده‌اند؛ این receipt ادعای clean-console ندارد. raw
artifact ثبت نشده و این slice evidence-only است.
- receipt postcommit roving-focus workspace در source `d1e8ecd5`: ۸ scenario local/synthetic
  production-build برای customer/accountant × filter/detail در `390×844` و `1440×900` با کلید
  `End` اجرا و ۸/۸ pass شد. selected final tab focused ماند و rectangle آن با tolerance یک CSS
  pixel داخل tablist خودش بود؛ document overflow و page/route scroll change صفر و دو tablist
  scroll change موبایل intentional بودند. console/page error/request failure/blocked external/
  unknown API غیرمنتظره صفر و `80/80` local synthetic API request موردانتظار ثبت شد. source/tree/
  hash و dist پیش/پس از run یکسان و clean بودند؛ هشت screenshot فقط خارج repository ماندند. این
  slice فقط همین scope، `End` و دو viewport را attest می‌کند و full matrix، همهٔ keyboard pathها
  یا پذیرش نهایی نیست.
- receipt long-text wrapping حساب در source `8082d8dd`: aggregation redacted سه report browser
  source-bound، ۱۲/۱۲ scenario account security/notifications، ۲۸۲ measurement و ۵۴ assertion
  DOM/accessibility را در پنج viewport عادی و دو probe CDP 2× ثبت می‌کند. console/page
  error/request failure/blocked external و unknown API غیرمنتظره صفر و API محلیِ موردانتظار
  `131/131` بود؛ ۱۲ screenshot فقط خارج repository ماندند. این aggregation اجرای browser تازه‌ای
  نیست، فقط scope نام‌برده را attest می‌کند و full matrix نیست.
- receipt Account Hub singleton action-grid در source `656cf6c3`: چهار scenario local/synthetic
  normal/accountant در `390×844` و `1440×900` همگی pass شدند. singletonها در mobile `332` و در
  desktop `1214` CSS pixelِ grid را پر کردند؛ security عادی desktop دو track مساوی `601` pixel و
  security حسابدار singleton بود. Telegram normal خارج grid ماند، overflow mobile و overlap
  daily navigation صفر بود و Enter/click هر دو به profile رسیدند. console/page error/request
  failure/external attempt و unknown API صفر و API محلیِ موردانتظار `56/56` بود؛ چهار screenshot
  فقط خارج repository ماندند. این slice evidence-only است و full matrix یا پذیرش نهایی نیست.
- receipt notification route-affordance در source `95ef7aa7`: سه scenario local/synthetic در
  `360×740`، `390×844` و `1440×900` همگی pass شدند. اعلان non-trade واجد مقصد امن دقیقاً یک cue
  بصریِ non-interactive و `aria-hidden` داشت؛ حالت‌های non-trade ناامن یا recovery-resolving
  marker نداشتند و article غیرقابل‌مسیر ماندند؛ اعلان trade ساختاری نیز marker نگرفت. Enter و
  pointer فقط برای اعلان امن journey ثبت‌شده را کامل کردند و click روی articleهای غیرقابل‌مسیر
  navigation نداشت. console/page error/request failure/external attempt و unknown API صفر و API
  محلیِ موردانتظار `162/162` بود. این slice evidence-only است و full matrix یا پذیرش نهایی نیست.
- receipt CreateChannel help-popover placement در source `0d7f2760`: دوازده scenario
  local/synthetic روی `/admin/channels` و overlay کانال `/chat` در home/create، `390×844`،
  `1440×900`، CDP 2× و reduced-motion همگی pass شدند (۱۲/۱۲، ۲۲۸/۲۲۸). containing-block همان
  کارت محلی بود، trigger کنار عنوان همان کارت و دقیقاً `32×32` ماند، و note کامل داخل
  card/sheet/viewport بود. clipping قبلی مثبت کاذب بود؛ defect واقعی placement با patch محلی و
  guardشده رفع شد و هیچ shared/global overflow workaround استفاده نشد. در `/admin/channels`
  کارت، trigger و note با scroll مسیر حرکت کردند؛ در `/chat` note داخل sheet ماند.
  console/page error/request failure/external attempt و unknown API صفر و API محلیِ موردانتظار
  `132/132` بود. این slice evidence-only است و full matrix یا پذیرش نهایی نیست.
- Figma اختیاری generic roving-focus: section `603:18`، board `603:19`، scope `604:22`، mobile
  `606:18` (`390×844`) و desktop `606:19` (`1440×900`) یک DRAFT زنده/قابل‌ویرایش با محتوای
  synthetic است. audit: ۴۴ text Vazirmatn، ۱۶/۱۶ instance متصل، semantic styles/variables reused،
  zero overflow/crop/overlap، contrast متن `4.55:1`، focus indicator `4.23:1` و privacy scan zero.
  این target نه visual freeze/final acceptance است و نه evidence runtime/browser/accessibility.
- Figma اختیاری generic directory/profile: section `583:146`، scope `584:146`، mobile directory
  `584:147`، desktop rail `584:148` و mobile profile `584:149`، یک DRAFT زنده/قابل‌ویرایش با
  محتوای synthetic/sanitized است. audit: ۱۷ linked instance، ۶۳ text Vazirmatn، صفر visible
  overflow، حداقل contrast `5.01:1` و privacy review clear. این target نه visual freeze یا
  پذیرش نهایی است و نه evidence runtime/browser/accessibility.
- Figma: file `z8jgJxST4O2APzWnlyP9gv`، page `486:1455`، section `508:95`، frame `508:96`
  (`390×844`) و provenance `511:151`. audit محدود: ۲۷ text با Vazirmatn، ۷ instance UIUX،
  ۴۹ node token-bound و صفر phone/email/URL/query ناایمن؛ review بصری بدون crop/overlap.
- Figma invitation-presentation: file `z8jgJxST4O2APzWnlyP9gv`، page `321:18`، section
  `535:1455` و board `535:1456`. این target زنده/قابل‌ویرایش و مورد تأیید مالک است، اما freeze
  یا پذیرش نهایی نیست؛ audit آن ۴۸ text Vazirmatn، ۱۸/۱۸ instance متصل، صفر phone/URL/token
  ناایمن و بدون crop را ثبت کرده است.
- Figma typography: file `z8jgJxST4O2APzWnlyP9gv`، page `321:18`، section `549:1549` و board
  `549:1550`، DRAFT زنده/قابل‌ویرایش با baseline `ec1cc82f` و implementation `338918d5` است.
  geometry و contrast pass و دادهٔ حساس مشاهده نشده، اما protected-baseline-pending است و نه
  owner-approved، نه freeze و نه final acceptance محسوب می‌شود.
- Figma generic auth-containment: file `z8jgJxST4O2APzWnlyP9gv`، section
  `567:1561` و board `567:1562` با frameهای `390×844` و `1440×900`، DRAFT زنده/قابل‌ویرایش
  است. textها Vazirmatn، escape/crop/overlap صفر و حداقل contrast نمایش‌داده‌شدهٔ white/action
  `4.55:1` است. board عمداً generic و بدون provenance داخلی، route/hash/test/harness/local-path/
  URL/token/deploy/Sites است؛ نه freeze، نه final acceptance و نه evidence runtime/browser است.

این دوازده slice محدود / ۱۶۳ scenario و مرجع‌های طراحیِ پیشین، فقط evidence local/synthetic یا live/editable هستند؛ freeze، runtime
accessibility acceptance، sign-off زیبایی مالک، یا release authority نیستند. receipt/reference مبتنی
بر `4415b743` فقط historical هستند و ادعایی دربارهٔ working tree جاری ندارند. artifact خام browser
در repository ذخیره نشده و هیچ Sites action انجام نشده است.

## ۴. رکورد تاریخی protected surfaces بازار و پیام‌رسان

پس از Stage 7 source، `guard:ui` دوباره pass شد و hashها با checkpoint Stage 4/6 یکی است:

- Home market interior: `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860`
- Market runtime files: `37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589` / `162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058`
- Messenger overlay: `f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58` / `3089210a77936d29754c9478fcdf40619acd08f35d1e8c64f6266fe8efb1699a`
- AdminMessages: `5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a`
- TradingSettings: `509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa`

این hashها از رکورد قبلی‌اند و در این update بازنویسی یا freeze جدیدی ساخته نشده است.

## ۵. مدل عرضه

1. تکمیل evidence قابل‌تکرار role×route و dimensionهای لازم روی همین branch؛
2. مشاهدهٔ خطا و بازخورد چند روزه فقط پس از اجازهٔ جداگانه و **بدون** production؛
3. گسترش مرحله‌ای فقط پس از اجازهٔ صریح مالک؛
4. حذف adapter قدیمی فقط پس از rollback اثبات‌شده.

Sites و production در این Stage شروع نشده‌اند.

## ۶. گیت بعدی (فنی و بصری)

- ۲۷۰ نتیجهٔ موردانتظار مسیر×پروفایل به source متصل است؛
- اجرای رسمی V2 با ۹۶۰/۸۳۰/۱۳۰ و ۲۷۰ سلول access ثبت شد و مالک زیبایی را تأیید کرد؛
- canonicalization مدیر میانی و دوازده slice محدود source-bound historical/non-counting مانده‌اند؛
- protected-surface hashهای تاریخی overwrite نشده‌اند؛
- عرضه فقط به‌صورت مدل تیمی و rollback-safe توصیف شده و شروع نشده است؛
- merge/staging/production/Sites انجام نشده و بستن Stage 8 مجوز آن‌ها نیست.
