# Stage 8 — پیش‌نویس رهگیری پذیرش و عرضهٔ تیمی

بستهٔ mutable برای رهگیری پذیرش و مدل عرضه است. این پوشه نه freeze محصول است، نه
گواه اجرای ماتریس کامل، و نه مجوز production.

## وضعیت

- branch: `condidate/webapp-ui-ux-redesign-v2`
- access-policy snapshot: `8eccdd2177ea5e2b21710b3a8863eace40092c35`
- component canonicalization snapshot: `7588d9c20b995244197d8de09392dd6a5f61b195`
- historical bounded visual-recovery source: `4415b7431a6b67965d24c44f6f9f0e59e48ed422` (historical record; not rewritten or promoted)
- current bounded route-transition source: `31c69d5a5d2fb1e2c08d9647473d3612b9d85629` (evidence-only)
- current bounded invitation-presentation source: `4beeade2f3aae4964f1964dedc00f47dfbcd0c05` (evidence-only and nonpromotable)
- current bounded NONE-route typography source: `338918d56f57f7cb974a501b1c43cc22d6afc2b5` (evidence-only)
- current bounded public/focused auth viewport-containment source: `55f00218295d7aa6f52f75b664544318684d2826` (evidence-only)
- current bounded directory/profile rebaseline source: `601b4005d80ef265afaaa6a06a43b48c44c7ca90` (evidence-only)
- current bounded workspace roving-focus postcommit source: `d1e8ecd5a524a03c73b67e531edab363479a32b0` (evidence-only)
- current bounded account long-text wrapping source: `8082d8dd6352154b52e86ca6511e27464e072b13` (evidence-only)
- current bounded Account Hub singleton action-grid source: `656cf6c3b62111c5c7bae458e3ea6f61fd8af788` (evidence-only)
- current bounded notification route-affordance source: `95ef7aa768f833c8e8b954d38b36674a77a304a9` (evidence-only)
- current bounded CreateChannel help-popover placement source: `0d7f276006deb7f97d20ba07e6f9ecb4d1b48a79` (evidence-only)
- authority: `stage8CompleteAuthority=false`
- matrix status: `partial-browser-slice-executed-full-acceptance-pending`
- expected-access coverage: ۳۰ مسیر × ۹ پروفایل دقیق = ۲۷۰ outcome صریح
- full matrix execution: صفر سلول؛ viewport/state/interaction/environment هنوز فقط requirement هستند
- partial synthetic evidence: دوازده partial slice / ۱۶۳ partial scenario؛ چهار slice 8A، یک slice 8B typography، یک slice مستقل auth-containment با ۱۴ scenario، یک rebaseline مستقل directory/profile با ۴۰ scenario، یک slice postcommit roving-focus با ۸ scenario، یک slice long-text wrapping حساب با ۱۲ scenario، یک slice Account Hub singleton با ۴ scenario، یک slice notification route-affordance با ۳ scenario و یک slice CreateChannel help-popover placement با ۱۲ scenario ثبت شده‌اند؛ هیچ‌کدام به full matrix افزوده نمی‌شوند
- owner aesthetic acceptance: انجام نشده
- merge: انجام نشده
- production/staging/Sites: انجام نشده

## اصلاح مدل دسترسی

نسخهٔ قبلی `customer`، `accountant` و `group-lead` را کنار نقش‌ها قرار می‌داد، درحالی‌که
guard فعلی علاوه بر نقش از `account_status`، `is_customer` و `is_accountant` استفاده
می‌کند. نسخهٔ ۳ برای هر مسیر و هر access profile نتیجهٔ صریح router/guard دارد و به source
واقعی آن متصل است. چهار deep-link مجازِ router برای مدیر میانی (`/admin/channels`،
`/admin/commodities`، `/admin/messages` و `/admin/system`) جداگانه به outcome کامپوننتی
`AdminView → /admin` متصل شده‌اند؛ این redirect رکورد router یا forbidden recovery نیست.

نقش‌های پایدارشده در مدل `UserRole` عبارت‌اند از `تماشا`، `عادی`، `پلیس`، `مدیر میانی`
و `مدیر ارشد`. مشتری، حسابدار و مالک/سرگروه context رابطه‌ای هستند، نه نقش جدید.

این outcomeها انتظار ایستای normal-case هستند؛ authorization داخل component یا API،
هویت شیء پارامتری، حالت inactive/unavailable و پذیرش بصری باید جداگانه اجرا و evidence-bound
شوند. هیچ cross-product ساختگی یا ادعای ۵۱٬۹۶۸ سلول در این بسته وجود ندارد.

## شواهد محدود 8A، 8B، auth-containment، directory/profile، roving-focus، long-text-wrap، Account Hub، notification route-affordance و CreateChannel help-popover placement

[STAGE8A_EXECUTION_RECEIPTS.json](STAGE8A_EXECUTION_RECEIPTS.json) تنها count، hash و
source-revisionهای redacted چهار اجرای local/synthetic را نگه می‌دارد؛ screenshot، diagnostic،
trace یا مسیر محلی در repository ذخیره نشده است.

- slice دسترسی/shell در `390×844`: شش profile × هشت scenario، ۴۸/۴۸ cell و ۵۰ assertion؛
  این اجرا full matrix نیست.
- slice بازیابی directory/profile در source تاریخی `4415b743`: مسیرهای `/profile`، `/users/:id`،
  `/admin/users` و `/admin/users/:id` در viewportهای محدود بررسی شده‌اند؛ این اجرا full
  role×route acceptance نیست و فقط یک record تاریخی است. rebaseline جاریِ جداگانه در source
  `601b4005` این receipt تاریخی را تغییر، جایگزین یا promote نمی‌کند.
- slice رفتاری route-first directory در source `31c69d5a`: چهار scenario local/synthetic production
  browser (pointer و Enter در ۳۹۰، pointer در ۱۴۴۰، و deep-link مدیر میانی) با ۳۳ assertion ثبت
  شده‌اند؛ ۳ گذار directory هرکدام دقیقاً یک `GET /api/users/` کامل و non-aborted داشته‌اند.
  این اجرا full role×route acceptance نیست و به full matrix افزوده نمی‌شود.
- slice invitation-presentation در source `4beeade2`: ۴۴ assertion روی `390×844` و `1440×900`
  برای focus بازگشتی Cancel/Escape، overflow، copy و end-state حذف ثبت شده است. transport حذف
  mock بود و artifact paired Chromium abort دیده شد؛ بنابراین این slice nonpromotable است، تکمیل
  server را attest نمی‌کند و به full matrix افزوده نمی‌شود.

[STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json](STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json) یک
slice جداگانهٔ local/synthetic و source-bound به `338918d5` است: Vazirmatn و
`font-synthesis:none` فقط روی route vnode با `protection=NONE` اعمال می‌شوند؛ base
`font-sans` و مسیرهای FULL/MIXED، حتی هنگام fade هم‌زمان، بدون تغییر می‌مانند. ۱۲ sample مسیر
و ۴ probe مرزی (جمعاً ۱۶ scenario) با صفر page error ثبت شده‌اند؛ این اجرا full browser یا
full matrix نیست و هیچ سلول پذیرش را pass نمی‌کند.

[STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json](STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json)
یک slice جداگانهٔ local/synthetic و source-bound به `55f00218` است: `AuthFlowShell` فقط با
opt-in صریحِ public/focused، `100vh` سپس `100dvh` می‌گیرد. Login، invitation، web-register و
setup-password در `390×844` و `1440×900` و reduced-motion در ۳۹۰ بررسی شده‌اند؛ هر چهار flow
viewport را پر کردند، focus-visible و نبود overflow گذشت و page error صفر بود. SystemRecovery
credentialed عمداً modifier نگرفت؛ base-height و daily navigation بدون collision ماندند. این
۱۴ scenario evidence-only است و به full matrix افزوده نمی‌شود. The 14-case capture recorded 10
WebSocket-related fixture console diagnostics (6 setup-password; 4 credentialed SystemRecovery)
with no backend; excluded from layout/interaction conclusions and no clean-console claim.

[STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json](STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json)
یک receipt مستقل local/synthetic و source-bound به `601b4005` است: ۴۰ scenario شامل ۲۰
route×viewport عادی، ۸ loading/error recovery، دو lifecycle، یک keyboard journey، چهار
reduced-motion، چهار CDP 2× و یک container-threshold harness-only اجرا شد. چهار route template
directory/profile در scope بودند؛ source/tree/hash و dist پیش و پس از run یکسان و clean ثبت شده‌اند.
overflow document/app، control مرئیِ بدون نام، page error، failed/external request و unknown API
غیرمنتظره صفر بودند؛ lifecycle حداکثر یک UserManager مرئی/mounted و یک درخواست کامل user-list
در هر گذار پوشیده‌شده داشت و focus بازگشت keyboard روی کنترل دارای label ماند. شش console
diagnostic موردانتظار از fixtureهای injected 404/500 (از جمله retry warning) جداگانه طبقه‌بندی
شده‌اند و از نتیجهٔ layout/interaction کنار گذاشته می‌شوند؛ این receipt هیچ ادعای clean-console
ندارد. raw screenshot/trace/console/network artifact در repository ذخیره نشده و این slice به
full matrix افزوده نمی‌شود.

[STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json](STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json)
یک receipt مستقل local/synthetic production-build و source-bound به `d1e8ecd5` است: ۸ scenario
customer/accountant × filter/detail در `390×844` و `1440×900` با کلید `End` اجرا شد. ۸/۸
scenario گذشت؛ tab نهایی هم selected و هم focused ماند و rectangle آن با tolerance یک CSS pixel
داخل tablist خودش بود. document overflow و تغییر scroll صفحه/route صفر بود؛ دو تغییر scroll
موردانتظار فقط در strip موبایل رخ داد. console/page error/request failure/blocked external/unknown
API غیرمنتظره صفر و requestهای local/synthetic موردانتظار `80/80` بودند. هشت screenshot فقط
خارج repository ماندند؛ هیچ artifact خام، path/URL محلی یا fixture payload در این receipt ذخیره
نشده است. این slice full matrix، همهٔ کلیدهای keyboard، همهٔ route/stateها یا پذیرش نهایی را
attest نمی‌کند و به full matrix افزوده نمی‌شود.

[STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json](STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json)
یک aggregation redacted از سه browser report مستقل و source-bound به `8082d8dd` است: ۱۲ scenario
برای account security و account notifications در `360×740`، `390×844`، `414×896`، `430×932` و
`1440×900`، به‌همراه یک probe CDP visual-scale 2 برای هر family، همگی گذشتند. ۲۸۲ اندازه‌گیری
direct-target/ancestor و ۵۴ assertion متن DOM/accessibility، نبود overflow افقی و حفظ متن
synthetic را تأیید کردند. console/page error/request failure/blocked external صفر، API محلیِ
موردانتظار `131/131` و unknown API صفر بود؛ ۱۲ screenshot فقط خارج repository ماندند. این
aggregation اجرای browser تازه‌ای نیست و فقط این scope محدود را ثبت می‌کند؛ CDP 2× جایگزین همهٔ
پیاده‌سازی‌های zoom بومی نیست و این slice به full matrix افزوده نمی‌شود.

[STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json](STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json)
یک receipt مستقل local/synthetic production-build و source-bound به `656cf6c3` است: چهار scenario
normal/accountant در `390×844` و `1440×900` گذشتند. singletonها در موبایل عرض `332` و در desktop
عرض `1214` CSS pixelِ grid را پر کردند؛ security عادیِ desktop دقیقاً دو track مساوی `601` pixel
داشت و security حسابدار singleton بود. Telegram فقط برای normal خارج از grid و sibling آن ماند؛
overflow موبایل و overlap با daily navigation صفر بود و Enter و click هر دو به profile رسیدند.
console/page error/request failure/external attempt و API ناشناخته صفر و API محلیِ موردانتظار
`56/56` بود؛ چهار screenshot فقط خارج repository ماندند. این slice فقط همین role، viewport و
journey محدود را attest می‌کند و به full matrix افزوده نمی‌شود.

[STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json](STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json)
یک receipt مستقل local/synthetic production-build و source-bound به `95ef7aa7` است: سه scenario
در `360×740`، `390×844` و `1440×900` گذشتند. اعلان non-trade واجد مقصد امن دقیقاً یک cue بصریِ
non-interactive و `aria-hidden` داشت؛ حالت‌های non-trade ناامن یا recovery-resolving marker نداشتند
و به‌صورت article غیرقابل‌مسیر ماندند؛ اعلان trade ساختاری نیز marker نگرفت. Enter و pointer فقط
برای اعلان امن journey ثبت‌شده را کامل کردند و click روی articleهای غیرقابل‌مسیر navigation نداشت.
console/page error/request failure/external attempt و API ناشناخته صفر و API محلیِ موردانتظار
`162/162` بود. این slice فقط stateها، viewportها و journey محدود نام‌برده را attest می‌کند و به
full matrix افزوده نمی‌شود.

[STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json](STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json)
یک receipt مستقل local/synthetic production-build و source-bound به `0d7f2760` است: دوازده
scenario روی `/admin/channels` و overlay کانال `/chat` در حالت home/create، `390×844`،
`1440×900`، CDP 2× و reduced-motion همگی گذشتند (۱۲/۱۲، ۲۲۸/۲۲۸). containing-block همان کارت
محلی بود، trigger کنار عنوان همان کارت و دقیقاً `32×32` ماند، و note کامل داخل card/sheet/viewport
بود. clipping قبلی مثبت کاذب بود؛ defect واقعی placement با patch محلی و guardشده رفع شد و هیچ
shared/global overflow workaround استفاده نشد. در `/admin/channels` کارت، trigger و note با
scroll مسیر حرکت کردند؛ در `/chat` note داخل sheet ماند. diagnosticهای console/page/request/
external/unknown API و mutation محصول صفر و API محلیِ موردانتظار `132/132` بود. این slice فقط
جای‌گذاری اصلاح‌شده روی دو سطح FULL را attest می‌کند و به full matrix افزوده نمی‌شود.

مرجع Figma اختیاریِ generic roving-focus، DRAFT زنده/قابل‌ویرایش با section `603:18` و board
`603:19` است؛ frameهای mobile `390×844` و desktop `1440×900` clip دارند. audit آن ۴۴ text
Vazirmatn، ۱۶/۱۶ linked instance، zero overflow/crop/overlap، حداقل contrast متن `4.55:1`،
focus indicator `4.23:1` و privacy scan zero را ثبت کرده است. این target synthetic نه visual
freeze یا پذیرش نهایی است و نه evidence runtime/browser/accessibility.

مرجع Figma اختیاریِ generic برای همین rebaseline، DRAFT زنده/قابل‌ویرایش با section `583:146`،
scope `584:146`، mobile directory `584:147`، desktop rail `584:148` و mobile profile `584:149`
است. محتوای synthetic/sanitized آن ۱۷ linked instance، ۶۳ text Vazirmatn، صفر visible overflow،
حداقل contrast `5.01:1` و privacy review clear دارد. این مرجع نه visual freeze یا پذیرش نهایی
است و نه evidence runtime/browser/accessibility.

مرجع Figma زنده و قابل‌ویرایش در file `z8jgJxST4O2APzWnlyP9gv`، page `486:1455`، section
`508:95` و frame `508:96` (`390×844`) ثبت شده است. audit محدود آن ۲۷ text با Vazirmatn،
۷ instance متصل UIUX، ۴۹ node token-bound، و صفر phone/email/URL/query ناایمن گزارش کرده است.
این مرجع نه screenshot/hash-freeze است، نه evidence اجرای runtime، و نه پذیرش نهایی؛ فقط منبع
تاریخی `4415b743` را نشان می‌دهد و ادعایی دربارهٔ working tree جاری ندارد. receipt جداگانهٔ
`601b4005` این board تاریخی را به مرجع جاری یا visual freeze تبدیل نمی‌کند.

مرجع invitation-presentation در همان file، page `321:18`، section `535:1455` و board `535:1456`
به source `4beeade2` متصل و توسط مالک تأیید شده، اما live/editable و غیر-freeze است. audit آن ۴۸
text با Vazirmatn، ۱۸/۱۸ instance متصل، صفر phone/URL/token ناایمن و بدون crop را ثبت کرده است؛
این هم evidence runtime، پذیرش کامل یا authority عرضه نیست.

مرجع typography در همان file، page `321:18`، section `549:1549` و board `549:1550`، یک
DRAFT زنده و قابل‌ویرایش است که baseline `ec1cc82f` و implementation `338918d5` را ثبت می‌کند.
audit geometry و contrast آن pass و محتوایش عاری از دادهٔ حساس است، اما protected-baseline-pending
است و نه freeze، نه final acceptance، و نه owner-approved محسوب می‌شود.

مرجع generic auth-containment در همان file، section `567:1561` و board
`567:1562`، یک DRAFT زنده/قابل‌ویرایش برای public/focused auth است. frameهای `390×844` و
`1440×900` clip دارند؛ تمام textها Vazirmatn هستند، escape/crop/overlap دیده نشد و حداقل contrast
نمایش‌داده‌شدهٔ white/action برابر `4.55:1` است. این board عمداً هیچ provenance داخلی، مسیر، hash
یا جزئیات test ندارد؛ نه freeze، نه final acceptance و نه evidence runtime/browser است.

## محتوا

- [ACCEPTANCE_MATRIX.json](ACCEPTANCE_MATRIX.json)
- [STAGE8A_EXECUTION_RECEIPTS.json](STAGE8A_EXECUTION_RECEIPTS.json)
- [STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json](STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json)
- [STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json](STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json)
- [STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json](STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json)
- [STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json](STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json)
- [STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json](STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json)
- [STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json](STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json)
- [STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json](STAGE8_NOTIFICATION_ROUTE_AFFORDANCE_EXECUTION_RECEIPT.json)
- [STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json](STAGE8_CREATE_CHANNEL_HELP_POPOVER_PLACEMENT_EXECUTION_RECEIPT.json)
- [VISUAL_FREEZE_PROTECTED_SURFACES.json](VISUAL_FREEZE_PROTECTED_SURFACES.json)
- [ROLLOUT_PLAN.md](ROLLOUT_PLAN.md)
- [VALIDATION.md](VALIDATION.md)
