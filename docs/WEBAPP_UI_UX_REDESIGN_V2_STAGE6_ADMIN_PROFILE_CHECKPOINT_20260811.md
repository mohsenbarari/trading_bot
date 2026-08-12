# Stage 6 — مدیریت و پروفایل

تاریخ آغاز: ۲۰۲۶-۰۸-۱۱

وضعیت: **`stage6_phase1_phase2_phase3_phase4_phase5_phase6_phase7_delivered_broader_roadmap_partial_deferred`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## ۱. مجوز و حد آن

دستور صریح مالک، توقف تاریخی پس از Stage 5 را برای Phase 1/2/3/4 Stage 6 supersede کرد و matrix server-enforced PII/authority را نیز برای Phase 3 تأیید کرد. این مجوز، اجازهٔ انتشار محصول، تغییر staging یا production، فعال‌سازی runtime خارج از branch، یا overwrite کردن preview/evidence Stage 5 نیست.

Stage 6 به‌صورت route-scoped، rollback-safe و با گیت مستقل پیش می‌رود. هر زیرسطح فقط پس از بسته‌شدن گیت فنی زیرسطح قبل وارد کار می‌شود.

## ۲. ترتیب منابع حقیقت

در تعارض، این ترتیب حاکم است:

1. دستور صریح کنونی مالک؛
2. قرارداد سیستم V2 و checkpointهای `0B-4` مدیریت/دعوت و `0B-5` حساب/پروفایل؛
3. سیاست خلوتی هدفمند و roadmap V2؛
4. Figma editable canonical با file key `z8jgJxST4O2APzWnlyP9gv`؛
5. کد و contractهای backend فعلی، برای authority، permission و outcome واقعی.

صفحهٔ Figma این Stage برابر `08 — Stage 6 Admin & Profile` (`321:18`) است. root (`321:19`) Phase 1 و Phase 2 را دارد و sectionهای Phase 3 (`381:318`)، Phase 4 (`398:504`)، Phase 5 (`413:571`)، Phase 6 (`422:636`) و Phase 7 (`442:701`) sibling همان page هستند. این topology page-level صریحاً افشا می‌شود: هیچ board تأییدشدهٔ `0B-4`، `0B-5` یا Stage 5 بازنویسی نشده و evidence را نباید یک root-bundle nested واحد نامید.

## ۳. Phase 1 — ورودی مدیریت

اولین slice عمداً به `/admin` و `AdminPanel` محدود است.

- فقط مقصدهای مدیریتی واقعاً مجاز نقش جاری دیده می‌شوند؛
- action key، label، icon، role filter، event `navigate` و routeهای موجود حفظ می‌شوند؛
- heading تکراری، سطح دسترسی تزئینی، شمارندهٔ دسته/ابزار و accordionهای صرفاً متراکم حذف می‌شوند؛
- UI هیچ total، count یا badge pending را بدون بارگذاری authoritative نمی‌سازد؛
- actionها به فهرست قابل‌فهم و keyboard-accessible از primitiveهای موجود محدود می‌شوند؛
- طراحی mobile-first با پنج عرض مرجع انجام می‌شود؛ desktop فقط همان مقصدها را adaptive نمایش می‌دهد و fact یا KPI جدید اضافه نمی‌کند.

فایل‌های مجاز این slice فقط `frontend/src/components/AdminPanel.vue`، تست مستقیم آن و در صورت نیاز expectation محدود `AdminView.test.ts` هستند. تغییر child workflow، API، router، route guard، permission یا backend در Phase 1 مجاز نیست.

## ۴. deferred خارج از Phase 1/2/3/4/5/6/7 تحویل‌شده

موارد زیر sliceهای مستقل و گیت‌دار بعدی‌اند:

- commodity feedback persistence؛
- dialogهای sensitive خارج از PublicProfile block/unblock، workspace deletion و پایان نشست workspace؛
- هر تغییر در Admin Messages و System Settings غیرمحافظت‌شده.

`/market`، `/chat`، `/share-receive`، `/admin/channels`، interiorهای market/messenger در `AdminMessagesView` و `TradingSettings` و Home Market همچنان protected هستند. هیچ shared CSS/token بدون guard و proof نبود drift تغییر نمی‌کند.

## ۵. قرارداد authority، PII و state — تصمیم pending با Phase 3 supersede شد

- visibility هرگز جای enforcement backend نیست؛ ordinary peer mobile را server-masked می‌گیرد و address، presence، membership، relation و trade detail در projection او نیست.
- self contact/address موردنیازِ مجاز را حفظ می‌کند؛ administrator فقط projection مجاز server را می‌گیرد و client حق بازسازی فیلد حذف‌شده ندارد.
- action حساس admin→self، middle→any-admin و super→super-peer server-side `403` است؛ target پایین‌تر فقط پس از check سروری مجاز می‌شود.
- pending endpoint بدون total معتبر، source count برای KPI یا badge نیست؛
- success delivery فقط با receipt همان channel اعلام می‌شود؛
- در evidence و Figma فقط هویت synthetic استفاده می‌شود و URL/token واقعی نمایش داده نمی‌شود؛
- public profile فقط `/users/:id` است؛ inbound legacy query پیش از navigation canonical می‌شود و account_name/highlight/relation/metadata وارد URL یا history نمی‌شود.
- loading، error، true empty، unavailable و permission-protected جدا می‌مانند؛ هیچ empty یا success ساختگی جای آن‌ها را نمی‌گیرد.

## ۶. گیت Phase 1 و rollback

حداقل گیت قبل از عبور به slice بعدی:

```text
AdminPanel.test.ts + AdminView.test.ts + AppPrimitives.test.ts + router contracts
guard:ui
type/build/diff check
admin Playwright discovery و browser acceptance در viewportهای مرجع
protected-surface parity
```

rollback به revert commit مستقل Phase 1 محدود است؛ پس از rollback، routeها، role filterها، `navigate` payloadها و suiteهای Admin دوباره اجرا می‌شوند. این گیت اکنون برای Phase 1 با receipt مرورگر مستقل بسته شده است؛ invitation و هر تغییر PII/profile یا authority تا تصمیم و گیت جدا شروع نمی‌شوند.

## ۷. Figma، evidence و Sites

Figma منبع اصلی طراحی است. Phase 1 boundary card و proof موبایل ورودی مدیریت، Phase 2 directory/desktop/recovery، Phase 3 privacy/authority، Phase 4 invitation management، Phase 5 block confirmation، Phase 6 workspace account deletion و Phase 7 session-termination safe recovery را ثبت می‌کند؛ pending-attention canonical فقط در state دارای دادهٔ واقعی باقی می‌ماند و به runtime نسبت داده نمی‌شود. Phaseهای 3 تا 7 siblingهای page-level root هستند؛ این caveat مانع از ادعای nested bundle می‌شود.

Sites در Stage 6 هنوز هیچ mutation، repo/project، preview یا deployment ندارد. اگر و فقط اگر scope گسترده‌تر مجاز و closure جداگانه آغاز شود، ابتدا inputs immutable freeze می‌شوند و سپس یک repo/project تازه و private owner-only برای evidence ساخته می‌شود. آن preview product deployment نیست و هیچ staging/production را تغییر نمی‌دهد.

## ۸. رسید Phase 1

برش source فقط این فایل‌ها را تغییر می‌دهد:

- `frontend/src/components/AdminPanel.vue`؛
- `frontend/src/components/AdminPanel.test.ts`؛
- expectationهای بازگشت به منوی واقعی در `frontend/src/views/AdminView.test.ts`؛
- انتظار heading canonical در `frontend/e2e/admin-smoke.spec.ts`.

نتیجهٔ runtime: `AdminPanel` یک `nav` برچسب‌دار با `ul/li/button` است؛ همهٔ action keyها، iconها، role filterها و `navigate` payloadهای پیشین حفظ شده‌اند. heading دوم، access-note، counterهای synthetic، accordion و Help copy تکراری حذف شده‌اند. هیچ fetch، pending badge، API، router، guard، permission یا child workflow تغییر نکرده است.

Figma Phase 1 در صفحهٔ `08 — Stage 6 Admin & Profile` و root `321:19` ثبت شده است: عرض مرجع `390×844`، header/nav پیوندخورده و فقط دو مقصد حقیقی بدون pending/count ساختگی.

گیت‌های source اجراشده:

- focused unit/route/primitives: ۵ فایل و ۵۰ تست pass؛ recheck مستقیم AdminPanel/AdminView: ۲۴ تست pass؛
- `vue-tsc --noEmit`، `npm run build`، `npm run guard:ui` و `git diff --check`: pass؛
- ESLint فایل‌های تغییرکرده هیچ diagnostic جدیدی ندارد؛ پنج `no-explicit-any` در `AdminView.test.ts` عین baseline هستند؛
- Playwright discovery چهار smoke را می‌شناسد و heading آن با `مرکز مدیریت` canonical شد.

اجرای production-like dev-login شرط پذیرش نبود: browser harness مستقل با fixtureهای synthetic و traffic خارجی مسدود، در worktree جداگانهٔ commit Phase 1 اجرا شد. run `uiux-stage6-phase1-browser-20260811T174217804Z` با ۱۵/۱۵ assertion و ۱۲ screenshot، بدون diagnostic غیرمنتظره، source/Git/harness/environment یکسان قبل/بعد و بدون mutation محصول/Sites/staging/production pass شد. این receipt فقط Phase 1 را می‌بندد؛ freeze و Sites Stage 6 تا closure کامل شروع نمی‌شوند.

## ۹. رسید Phase 2 — directory و context خصوصی

Phase 2 به directory مدیریت و detail مجاز server-authoritative محدود است؛ `/admin/users` و `/admin/users/:id` بدون تغییر backend/API/router guard یا PublicProfile/Messenger بازطراحی شدند.

- جست‌وجو فقط state محلی و auth-scoped است؛ `q`، `account_name` یا هر دادهٔ هویتی در URL، history یا storage serialize نمی‌شود؛ تنها context مسیر `scroll` صحیح، نامنفی و integer است.
- list با `ul/li/button` semantic، keyboard-accessible، abort/stale-safe، empty/error/retry و نام کاربری long با ellipsis قابل‌دسترسی است.
- detail فقط از GET مجاز سرور بارگذاری می‌شود؛ 403/404 و مسیر legacy/invalid به recovery canonical بدون PII می‌رسند.
- بازگشت list/detail و history، `scroll` را بدون churn route یا remount race نگه می‌دارد؛ app-key مشترک برای دو route directory از overlap transition جلوگیری می‌کند.

برش source در commit `61854fc6490d38586d67a82fefc9cb3ab3f9304d` (tree `c64ad05da39d7076d77ca51b736365da920d2325`) بسته شد. focused tests ۵۷/۵۷، `vue-tsc --noEmit --pretty false --incremental false`، `npm run guard:ui`، build و diff check pass هستند؛ full serial Vitest نیز ۱۵۴ file و ۱۶۸۰ test pass شد. diagnosticهای parallel timeout فقط cold-transform/intermittent خارج از scope بودند و با اجرای serial بازتولید نشدند.

browser acceptance promotable Phase 2 در run `uiux-stage6-phase2-browser-20260811T173943646Z` روی commit clean بالا، ۱۷/۱۷ assertion و ۱۴ screenshot pass شد: super/middle در ۳۶۰/۳۷۵/۳۹۰/۴۱۴/۴۳۰/۱۴۴۰، privacy sentinel، list/detail scroll، 403/404 recovery، reflow و external blocking را پوشش می‌دهد؛ diagnostic غیرمنتظره صفر است. چهار console notice مربوط به fixtureهای synthetic 403/404 صریحاً expected/classified هستند.

Figma Phase 2 در همان page/root با directory mobile `336:50`، desktop proof `366:138` و recovery/detail proofهای `371:194`، `373:226`، `373:254` و `373:282` به‌روزرسانی شد. annotation قرارداد می‌گوید فقط `scroll` route context است و `q`/`account_name` در URL/history/storage ذخیره نمی‌شوند. تمام هویت‌ها synthetic هستند. Phase 2 اکنون به source final bind شده است، اما local freeze یا Sites Stage 6 آغاز نشده است.

blocker تاریخی Phase 2 برای matrix PII/authority با تأیید مالک و Phase 3 زیر supersede شده است. این supersede، فقط scope تحویل‌شده را می‌بندد؛ broader roadmap و closure Stage 6 همچنان deferred هستند.

## ۱۰. رسید Phase 3 — privacy، authority و route ID-only

Phase 3 در commit `3283a6e38209cb06d352740dae5b05bce5ba9002` (tree `7284ec4aac1980c0f61201e3346841425f6bcb09`) تحویل شد.

- server projection برای peer عادی mobile masked است و address/presence/membership/relation/trade detail را اصلاً برنمی‌گرداند؛ self و administratorِ مجاز فقط فیلدهای لازمِ مجاز را می‌گیرند.
- action حساس admin→self، middle→any-admin و super→super-peer backend-enforced `403` است؛ UI read-only فقط feedback است، نه enforcement.
- همهٔ entryهای public profile از direct، notification، toast و browser به `/users/:id` بدون query/hash می‌روند؛ legacy query پیش از navigation canonical می‌شود. Messenger/Forward discovery بدون تغییر باقی مانده است.
- PublicProfile در 360px reflow شد و controlهای interactive در reduced-motion transition مؤثر ندارند؛ 403/404 recovery عمومی، bounded و بدون detail leak است.

گیت‌های نهایی source:

- frontend serial: `154` file / `1700` test pass در `436.71s`؛ `vue-tsc` (`1.61s`)، build (`32.20s`)، `guard:ui` و diff check pass؛ focused profile برابر `75/75` pass.
- backend targeted authority/projection/notification: `131` test pass با config dummy محلی؛ warningهای inherited نتیجهٔ محصول نیستند.
- Playwright collection: `24` test در `4` spec pass. live E2E به علت unavailable بودن `127.0.0.1:8000/api/config` در محیط محلی اجرا نشد؛ هیچ staging/production لمس نشده است.

browser receipt نهایی aggregate `uiux-stage6-aggregate-browser-20260811T203934914Z` promotable/pass است: top-level `17/17` assertion و چهار screenshot؛ child Phase 2 برابر `17/17`/۱۴ screenshot و child Phase 3 برابر `14/14`/۱۲ screenshot است. aggregate source binding برای `560` فایل `6a4ba01a41ce97494ae1b95bdab605b88293b15c368cdb426c235bb358a1b3fd` است و pre/post source/Git/harness/environment identical باقی مانده‌اند. هر دو child هیچ diagnostic غیرمنتظره ندارند: Phase 2 با کلیدهای `expectedProfileResponseConsoleEvents=4` (403/404 fixture) و `externalRequestsBlocked=15` ثبت شده است؛ Phase 3 با کلیدهای مستقل `expectedHttpErrors=4` (403/404) و `externalTrafficIntercepted=13` (Telegram loader local-intercepted). بنابراین counterهای نام‌برده‌شدهٔ Phase 2 به Phase 3 نسبت داده نمی‌شوند.

## ۱۱. Figma post-fix evidence

read-only audit `assets/figma/final-provenance-20260811T204635Z/stage6-final-figma-provenance-audit.json` با SHA-256 `ccdea4bd31124d759c68ed89e16c9ed73290f04e2bb58359b4138e8ed575b89b` در `2026-08-11T20:49:17Z` result `pass_with_documented_page_sibling_topology` دارد.

- labelهای Phase 1/2/3 به‌ترتیب `323:19`، `336:49` و `381:319` visible هستند و هر سه `source 3283a6e3` و `دادهٔ synthetic` دارند.
- proofهای Phase 1: `326:20` و `347:107`؛ Phase 2: `336:50`، `366:138`، `371:194`، `373:226`، `373:254`، `373:282`؛ Phase 3: `382:556`، `381:330`، `382:573`، `381:393`، `381:341`، `381:359`.
- render page/root/sectionها clipping یا overlap label ندارد. Phase 1/2 child root `321:19` هستند، اما Phase 3 (`381:318`) sibling همان page `321:18` است؛ پس evidence page-level است.

## ۱۲. رسید Phase 4 — invitation management و revoke ایمن

Phase 4 در commit `1d664042446016e1528527fc192a0016f09d7162` تحویل شد؛ اصلاح shared dialog لازم برای viewport واقعی در commit `7ac46a36a2ba968246bd285c357bc362a328cdd2` انجام شد.

- لینک invitation فقط in-memory و با clipboard action صریح قابل‌دریافت است؛ در DOM، URL، history یا storage serialize/render نمی‌شود و fallback textarea وجود ندارد.
- invitation تازه و invitation recovered فقط بر پایهٔ receipt سرور، با پیام delivery صادقانه نمایش می‌یابند؛ queue هیچ count/KPI ساختگی ندارد.
- load/create/delete با `cache: no-store` و کنترل abort/revision انجام می‌شود؛ `204` تنها receipt حذف row است، `400/404` یک reconciliation تازه می‌گیرد و `403` queue، dialog و تمام کنترل‌های copy/دادهٔ حساس را پاک می‌کند.
- revoke فقط پس از confirm dialog واقعی انجام می‌شود. `AppConfirmDialog` با `Teleport` به `body` منتقل شد تا backdrop/dialog در subtree route clip نشوند؛ focus، scroll-lock و CTAها حفظ شده‌اند.

گیت‌های source: focused dialog/invitation برابر `31/31` pass، `vue-tsc --noEmit`، `npm run guard:ui`، build و `git diff --check` pass هستند. frontend serial کامل نیز `154` file / `1700` test pass داشت.

browser receipt promotable در run `uiux-stage6-phase4-invitations-20260812T052926717Z` روی commit clean `7ac46a36…` با `8/8` assertion pass شد: reflow 360/1440، fresh/recovered copy-only، عدم نشت bearer در DOM/URL/history/storage، revoke 204 پس از modal، reconcile 400/404، پاک‌سازی 403 و modal fullscreen-safe را پوشش می‌دهد. metrics SHA-256 برابر `e2a58abee27a7a7cf93748bb0660f7cbdb54f4d2abb07faedb2a44fdc8ef4e3f` و source-binding SHA-256 برابر `bfc5b609c03873775e3216f60a86bea0f1089932901dd4a4faf7455b28e7cb97` است؛ diagnostic غیرمنتظره صفر و همهٔ traffic خارجی محلی intercepted بوده‌اند.

Figma Phase 4 در page `321:18`، section `398:504`، با دو mobile screen و scope card ثبت شده است. label `402:504` دقیقاً `source 7ac46a36 · دادهٔ synthetic` است؛ نمونهٔ دعوت فقط synthetic است و audit raw URL/token/phone را نیافت.

## ۱۳. رسید Phase 5 — تأیید و receipt امن بلاک عمومی

Phase 5 در commit `5ca7d00120c693c8c8507656dbe203dd530396a5` (tree `9776550334dfc45a563e1b5fd221d63156334c36`) تحویل شد و فقط `PublicProfile.vue` و تست مستقیم آن را تغییر می‌دهد.

- `window.confirm` و `window.alert` از flow بلاک/رفع‌بلاک حذف شدند؛ `AppConfirmDialog` موجود با Teleport، focus-trap، Escape، restore-focus و scroll-lock استفاده می‌شود.
- بازکردن و لغو dialog هیچ mutation ندارد. فقط `{ success: true }` معتبر، پس از `POST` یا `DELETE` دقیق `/api/blocks/:id`، state محلی را تغییر می‌دهد؛ رفع‌بلاک حتی وقتی block جدید مجاز نیست در دسترس می‌ماند.
- ۴۰۰/۴۰۳/۴۰۴، network و payload نامعتبر state را حفظ می‌کنند و فقط receipt ثابت می‌دهند؛ account name، detail/message یا payload خام سرور در dialog، feedback، URL، history یا storage وارد نمی‌شود.

گیت‌های source: focused `PublicProfile` + `AppPrimitives` برابر `65/65` pass، `vue-tsc --noEmit`، `npm run guard:ui` و `git diff --check` pass هستند.

browser receipt promotable در run `uiux-stage6-phase5-public-block-20260812T062238392Z` روی commit clean بالا با `7/7` assertion و ۶ screenshot pass شد: dialog/focus/scroll-lock/cancel در `360×740`، POST و receipt امن در `390×844`، DELETE رفع‌بلاک در `1440×900` و ۴۰۳/۴۰۴/malformed بدون state flip یا raw-detail را پوشش می‌دهد. source binding `4b153ba3d9cd7fdeab262f89e48286f7ecfa592cd5cff5d3a30f2f7d4a671451` (393 files)، metrics SHA-256 `5a88e7c01641738d7ba667a188b354386e786d2200a8511b7556b1e25dc9a70e` و binding SHA-256 `b91ac618e35f1582ebb316a711ceb541c1758615a6b16ea8a13b2175a9ad1ab0` است؛ diagnostic غیرمنتظره صفر و traffic خارجی فقط locally intercepted بوده است.

Figma Phase 5 در page `321:18`، section `413:571` ثبت شد: confirmation screen `413:4241` و fixed receipt/error screen `416:605` هر دو `390×844` هستند. label `413:4240` شامل `source 5ca7d001 · دادهٔ synthetic` است؛ همهٔ textها Vazirmatn، Button/Header/Nav instanceهای design-system موجود و card/dialog token-bound هستند. independent audit clipping، PII، URL یا raw server payload نیافت.

## ۱۴. رسید Phase 6 — حذف حساب workspace با portal و recovery امن

Phase 6 در commit `06579e2bbccbb2b8a33bd9a92bc55a851e8a2329` (tree `bcdb89069aec2619cc8e7e7da6c0126bf9b22986`) تحویل شد.

- فقط Customer/Accountant workspace view، dialog اختصاصی workspace deletion و guard باریک V2 portal را تغییر می‌دهد؛ هیچ backend، router، API contract یا protected Market/Messenger surface تغییر نکرده است.
- dialog حذف حساب به `body` Teleport می‌شود و زیر scope رسمی V2 portal قرار می‌گیرد؛ focus trap، Escape، restore-focus و scroll-lock حفظ شده‌اند و geometry موبایل قابل‌کلیک/بدون clipping است.
- نام نمایش‌داده‌شده و acknowledgement لازم‌اند؛ cancel یا Escape هیچ DELETE نمی‌فرستد. API deletion همچنان دقیقاً `expected_action=delete-account` دارد.
- فقط receipt همان relation با `status: deleted` navigation/local reconciliation را فعال می‌کند. 400/403/404، malformed و network dialog/relation/route را نگه می‌دارند و فقط پیام ثابت امن نمایش می‌دهند؛ detail/message خام سرور نشت نمی‌کند.

گیت‌های source: dialog `6/6`، Customer/Accountant serial `3 file / 85 test`، design-system guard `43/43`، `vue-tsc --noEmit`، build، `guard:ui`، Stage4 guard `11/11` و diff check همگی pass هستند.

browser receipt promotable در run `uiux-stage6-phase6-account-delete-20260812T075056249Z`، با `18/18` assertion و `20` screenshot روی source clean فوق pass شد. Customer/Accountant در 360/390/1440، portal/full viewport، focus/cancel، expected action، receipt id/status صحیح، همهٔ failure recoveryها و no-raw-payload را پوشش می‌دهد؛ diagnostic غیرمنتظره صفر است. source binding `fcf977ab478b6c2c38fe4f719e90b2302f48039774b72e9fcb9da8a8aa1eb63e` برای 393 فایل، metrics `5bd62ad9ea00292cc8620005695f3207b2847caab667c5013738b84241220ecb` و binding `64f2eb7974e2692a2840e0080dfd53ac20fa1364f3fd50d33cc0cbf24b7d7a3f` است.

Figma Phase 6 در page `321:18`، sibling section `422:636` ثبت شد: W1=`422:638` body-portal confirmation و W2=`422:661` safe-error recovery، هر دو `390×844`. label `422:637` دقیقاً `source 06579e2b · دادهٔ synthetic` دارد؛ backdrop `422:653` تمام viewport و dialog `422:654` کاملاً در viewport است. audit live Vazirmatn، linked Button/Header/Nav، bindingهای input/acknowledgement و نبود URL/email/phone/raw-detail را تأیید کرد.

## ۱۵. رسید Phase 7 — پایان امن یک نشست workspace

Phase 7 در commit `24a8d0f500e798c70eb94764045ee9ed90151b99` (tree `c611f9612ce45ac698d5a76589b5a2474e0860e5`) تحویل شد و فقط چهار فایل مستقیم Customer/Accountant workspace و testهایشان را تغییر می‌دهد.

- این slice فقط مسیرهای زندهٔ `/operations/customers/:relationId` و `/operations/accountants/:relationId` را سخت می‌کند؛ modalهای compatibility مالک runtime نیستند و تغییر داده نشدند.
- cancel یا Escape هیچ DELETE نمی‌فرستد. فقط receipt با `terminated_session_id` دقیقاً برابر نشست انتخاب‌شده همان نشست را از state محلی حذف می‌کند و در صورت receipt معتبر، نشست باقی‌مانده را primary می‌سازد.
- 400/403/404، network یا receipt malformed، dialog، route، relation و اطلاعات نمایش‌داده‌شدهٔ نشست را حفظ می‌کنند؛ فقط پیام ثابت امن نشان داده می‌شود و `detail`/`message` یا شناسهٔ raw سرور نمایش یا serialize نمی‌شود.

گیت‌های source: serial Customer/Accountant workspace برابر **80/80** test pass، `vue-tsc --noEmit`، `npm run guard:ui` و `git diff --check` pass هستند.

browser receipt promotable در run `uiux-stage6-session-browser-20260812T121245634Z` روی source clean بالا با **18/18** assertion و **16** screenshot pass شد. Customer و Accountant را برای 360 focus/cancel، پنج failure mode در 390، و receipt صحیح در 390/1440 پوشش می‌دهد. source binding SHA-256 `6fde9fb1b0f53fd2820c932b14165a7a9d98fd35fb3b7719aa41f61602f62354` برای 393 فایل، metrics `e7a558edd7c4d55972fe9ee279d8925cf02f999aa26d9186d0252035f932b28d` و binding `24022d531dfba3349f729f9945debf2eb0968ea315a584306b93b74f1023fd38` است؛ harness SHA-256 `e2a6d6781f9024b02fa67fd61cafd192af8a4387d0cfa758a9fb60e48df50402`. diagnostic غیرمنتظره صفر است؛ فقط شش HTTP fixture، دو network console/failure fixture و 17 loader خارجی locally intercepted به‌طور صریح classified هستند.

Figma Phase 7 در page `321:18`، sibling section `442:701` ثبت شد: W1=`442:703` confirmation و W2=`442:733` safe recovery، هر دو `390×844`. dialog=`442:719` در `(16,278,358,288)` کامل داخل viewport است. label شامل `source 24a8d0f5 · دادهٔ synthetic` است؛ audit Vazirmatn، Button/Header/Nav linked، CTA secondary/primary درست و نبود phone/email/URL/raw error را تأیید کرد. این reference live/editable است و freeze یا screenshot hash مستقل نیست.

## ۱۶. مرز closure

Phase 1/2/3/4/5/6/7 تحویل‌شده‌اند، اما broader roadmap Stage 6 partial/deferred است و **`stage6CompleteAuthority=false`**. هیچ `EVIDENCE_MANIFEST`، local freeze، Sites project/preview، product deployment، staging deployment یا production deployment ساخته یا تغییر داده نشده است. این checkpoint مجوز انجام deferredها یا ادعای complete Stage 6 نیست.
