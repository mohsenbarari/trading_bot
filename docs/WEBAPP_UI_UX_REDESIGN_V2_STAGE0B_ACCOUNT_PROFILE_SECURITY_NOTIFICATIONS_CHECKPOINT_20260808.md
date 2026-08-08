# Stage 0B-5 — حساب، پروفایل، امنیت و اعلان‌ها

تاریخ: ۲۰۲۶-۰۸-۰۸

وضعیت: **تکمیل و تأیید بصری مالک محصول در ۲۰۲۶-۰۸-۰۸**؛ Phase 0، Figma canonical، evidence محلی و Sites خصوصی پاس شده‌اند؛ design-only و بدون مجوز تغییر runtime

شاخه: `condidate/webapp-ui-ux-redesign-v2`، ساخته‌شده مستقیم از `main`

## ۱. هدف checkpoint

این checkpoint قرارداد سناریومحور صفحه‌های حساب، پروفایل شخصی و عمومی، امنیت نشست‌ها، حافظه محلی و مرکز اعلان را پیش از هر تغییر محصول قطعی می‌کند. طراحی مرجع mobile-first و خلوت است: هر واحد محتوای پیش‌فرض باید یک تصمیم، اقدام، وضعیت ضروری یا ریسک واقعی را روشن کند.

صفحه canonical Figma، exportهای مستقیم، harness محلی fail-closed و پیش‌نمایش خصوصی Sites تولید و راستی‌آزمایی شده‌اند. این شواهد طراحی ایستا هستند و رفتار runtime، authorization، mutation یا delivery را اثبات نمی‌کنند.

## ۲. مرز کار

### داخل محدوده طراحی

- مرکز حساب در `/account`؛
- پروفایل شخصی در `/profile` و ویرایش avatar/address؛
- پروفایل عمومی در `/users/:id` با افشای محدود و نقش‌محور؛
- نشست‌های شخصی در `/account/security`؛
- حافظه و داده محلی در `/account/storage`؛
- مرکز اعلان در `/account/notifications` و stateهای Push؛
- loading، true empty، category empty، error/retry، unavailable، busy، success و failure؛
- قرارداد آینده redirect از `/settings` و `/notifications` به مقصدهای canonical؛
- پوسته احراز‌شده مصوب `0B-2` با مقصد فعال `حساب`.

### خارج از محدوده و محافظت‌شده

- UI و رفتار داخلی `/market`؛
- UI و رفتار داخلی `/chat`، `/share-receive` و `/admin/channels`؛
- preference اعلان آفر بازار؛
- منطق معامله، آفر یا realtime بازار؛
- جریان رابطه و تاریخچه به‌عنوان surface کامل؛ در پروفایل عمومی فقط affordance محدود و on-demand مجاز است؛
- ساخت API، تغییر route، رفع باگ، تغییر frontend/backend، تست mutation یا هر تغییر runtime.

حضور labelهای `بازار` و `پیام‌رسان` در navigation فقط reuse پوسته است و مجوز بازطراحی interior آن‌ها نیست.

## ۳. تصمیم‌های قفل‌شده Phase 0

1. پروفایل عمومی موبایل، شماره را masked و آدرس را پنهان می‌کند؛ داده کامل فقط در پروفایل خود یا برای نقش مدیریتی واقعاً مجاز قابل نمایش است.
2. ورود به مرکز اعلان، اعلان‌های موجود را مطابق رفتار فعلی خوانده‌شده می‌کند؛ فقط اعلان تازه realtime می‌تواند نشان «جدید» بگیرد.
3. حذف یا پاک‌کردن اعلان طراحی نمی‌شود، چون قابلیت پشتیبانی‌شده‌ای وجود ندارد.
4. Push فقط وقتی قابل فعال‌سازی است که browser/device و backend آن را واقعاً قابل اقدام کنند؛ کنترل خیالی disable یا test ساخته نمی‌شود.
5. پروفایل عمومی یک proof محدود از حریم خصوصی، header و action مجاز است؛ روابط و تاریخچه فقط on-demand باقی می‌مانند.
6. `/account`، `/account/security`، `/account/storage`، `/account/notifications`، `/profile` و `/users/:id` مقصدهای canonical هستند؛ redirect مسیرهای legacy یک carry-forward runtime برای بعد از `0B-6` است.
7. سه component set تازه `UIUX/Account Action Row` روی `121:14`، `UIUX/Session Row` روی `122:1327` و `UIUX/Notification Row` روی `123:1330` ساخته شده‌اند؛ variantهای Account-active در Bottom Navigation روی `127:14` و `127:35` ثبت شده‌اند.
8. collection، variable، text style یا effect تازه ساخته نشده است؛ ۶۵ variable، ۹ text style Vazirmatn و ۲ effect موجود reuse شدند.

## ۴. قرارداد ۱۰ root موبایل

همه rootهای مرجع دقیقاً `390×844` هستند و فقط از داده synthetic استفاده می‌کنند.

| شناسه | سناریو | قرارداد ضروری |
| --- | --- | --- |
| `0B5-M01` | مرکز حساب کاربر عادی | هویت واقعی پس از load، مقصدهای یکتا برای پروفایل/امنیت/حافظه/اعلان و navigation فعال `حساب`؛ بدون badge فرضی «فعال» |
| `0B5-M02` | مرکز حساب حسابدار | پروفایل، حافظه و اعلان؛ بدون مدیریت نشست شخصی، logout شخصی، Telegram یا کارت توضیحی طولانی محدودیت |
| `0B5-M03` | پروفایل شخصی | avatar، نام، شناسه لازم حساب، موبایل read-only، آدرس و اقدام‌های پرتکرار؛ اطلاعات ثانویه فقط on-demand |
| `0B5-M04` | ویرایش avatar/address | حریم خصوصی محلی، validation، busy، success و failure در همان context؛ داده واردشده در failure حفظ می‌شود |
| `0B5-M05` | پروفایل عمومی | شماره masked، آدرس hidden برای کاربر عادی، header واحد و action مجاز؛ افشای کامل فقط برای self/admin مجاز |
| `0B5-M06` | نشست‌های فعال | device، platform، last activity و فقط signal لازم current/primary؛ IP on-demand و بدون `home_server` |
| `0B5-M07` | تصمیم پایان نشست | پایان یک نشست مجاز یا «پایان همه نشست‌های دیگر» با confirm صادقانه و نتیجه success/failure در همان context؛ نشست جاری حفظ می‌شود |
| `0B5-M08` | حافظه محلی | اندازه cache واقعی یا state خطای اندازه، پاک‌سازی فایل‌های محلی همین browser/device و feedback busy/success/failure |
| `0B5-M09` | مرکز اعلان | tabهای `معاملات` و `سایر` بدون count ساختگی، اعلان synthetic، مقصد اجرایی فقط در صورت وجود route و نشان realtime برای مورد تازه |
| `0B5-M10` | state قابل اقدام Push | فقط فعال‌سازی در state مجاز؛ توضیح device/browser-specific و بدون ادعای تحویل سراسری، Telegram یا cross-channel |

## ۵. قرارداد نقش، حقیقت و حریم خصوصی

### مرکز حساب و پروفایل

- مقصدهای پیش‌فرض یکتا هستند؛ «تنظیمات کاربری» و «حافظه و داده‌ها» به یک مقصد تکراری تبدیل نمی‌شوند.
- پیش از load، نام «کاربر»، status فعال یا claim سلامت حساب ساخته نمی‌شود.
- badge مثبت «فعال» حذف می‌شود؛ فقط وضعیت inactive/restricted اثرگذار به‌صورت هشدار متناسب نمایش داده می‌شود.
- Telegram فقط برای کاربر غیرحسابدار، وقتی backend `can_connect_telegram` را واقعاً مجاز کرده و حساب linked نیست، اقدام است.
- نام، email، mobile، password و MFA قابل‌ویرایش فرض نمی‌شوند؛ در قرارداد فعلی فقط avatar و address ویرایش‌پذیرند.
- membership date، trade count، relation count، history، project users، route و server metadata از نمای پیش‌فرض حذف یا on-demand می‌شوند.

### ماتریس افشای پروفایل عمومی

| viewer | شماره | آدرس | action |
| --- | --- | --- | --- |
| خود کاربر | کامل و read-only | کامل و قابل‌ویرایش در جریان شخصی | ویرایش avatar/address |
| کاربر عادی مجاز | masked | hidden | فقط action مجاز و موجود |
| ادمین واقعاً مجاز | مطابق مجوز backend | مطابق مجوز backend | فقط action داخل دامنه و مجاز |
| forbidden / unavailable | افشا نمی‌شود | افشا نمی‌شود | recovery امن و بازگشت |

طراحی، مجوز backend را اختراع یا جایگزین نمی‌کند.

### نشست و خروج

- فقط primary session می‌تواند نشست دیگری را terminate کند.
- primary تا وقتی نشست دیگری وجود دارد قابل termination نیست.
- `/logout-all` نشست جاری را حفظ می‌کند و فقط نشست‌های دیگر را پایان می‌دهد؛ copy دقیق باید «پایان همه نشست‌های دیگر» یا «خروج از سایر دستگاه‌ها» باشد.
- حسابدار به مدیریت نشست شخصی و logout شخصی دسترسی ندارد؛ 403 به‌صورت empty یا action غیرفعال بی‌توضیح نمایش داده نمی‌شود.
- نشست‌ها در وضعیت فعلی local per-server هستند؛ طراحی فهرست merged یا تضمین cross-server نمی‌سازد.

### حافظه محلی

- پاک‌سازی فقط فایل‌های download/cache همین browser/device را حذف می‌کند.
- حساب، پیام‌ها، رکوردهای server و session حذف نمی‌شوند و فایل‌ها ممکن است دوباره download شوند.
- صفر واقعی cache با شکست محاسبه اندازه یکی نیست و دو state جدا دارد.

### اعلان و Push

- endpoint فقط آخرین ۵۰ اعلان را می‌آورد؛ تعداد category از طول این لیست به‌عنوان total نمایش داده نمی‌شود.
- tab غیرمعامله‌ای `سایر` است، نه «پیام مدیریت».
- item فقط content، time، affordance مقصد واقعی و یک signal unread/new لازم دارد؛ route خام نمایش داده نمی‌شود.
- item بدون route کلیک‌پذیر نیست.
- WebSocket وقوع اعلان تازه را بدون refresh دستی نشان می‌دهد، اما reconnect فعلی تضمین بازیابی history ازدست‌رفته نیست.
- sync رکورد اعلان میان سرورها با اثر realtime/Push محلی یکی نیست؛ هیچ تضمین تحویل هم‌زمان در Web، Telegram یا همه deviceها ادعا نمی‌شود.
- stateهای Push دقیقاً شامل checking، unsupported، insecure، server-disabled، permission-blocked، permission-default، subscribed، unsubscribed و error است.

## ۶. state atlas الزام‌آور

- حساب: loading، error/retry، inactive/restricted؛
- پروفایل: loading، error/retry، unavailable، address-save busy/success/failure؛
- نشست: loading، true empty، error/retry، terminate busy/success/failure، current non-primary/forbidden و accountant forbidden؛
- حافظه: busy، success، failure، zero و size-error؛
- اعلان: loading، true empty، category empty، error/retry، route-less و realtime-new؛
- Push: ۹ state قفل‌شده بالا؛
- حریم خصوصی: self، normal viewer، authorized admin و forbidden/unavailable.

هیچ loading با داده فرضی، error با empty، category empty با true empty یا size-error با صفر جایگزین نمی‌شود.

## ۷. ساختار نهایی Figma

فایل canonical: [Trading Bot — WebApp UI UX Redesign V2](https://www.figma.com/design/z8jgJxST4O2APzWnlyP9gv)

صفحه نهایی: `04 — Stage 0B-5 Account, Profile, Security & Notifications` با شناسه `117:2`.

sectionهای نهایی:

1. `117:3` — `00 — Contract`
2. `117:4` — `01 — Account and profile scenarios`
3. `117:5` — `02 — Security and storage scenarios`
4. `117:6` — `03 — Notification center scenarios`
5. `117:7` — `04 — State, route, visibility and push matrix`
6. `117:8` — `05 — Responsive and desktop proofs`

Foundations موجود `41:2` و Components موجود `46:2` reuse شده‌اند. freeze نهایی در `2026-08-08T17:10:58.500Z`، audit schema 2 در `2026-08-08T17:11:05.475Z` و capture مستقیم در `2026-08-08T17:13:12.738Z` ثبت شد.

## ۸. responsive و desktop

- پنج proof موبایل: `360 / 375 / 390 / 414 / 430 × 844`؛
- یک proof دسکتاپ دقیق `1440×900` برای امنیت/نشست‌ها؛
- دسکتاپ همان حقیقت و task موبایل را در container مناسب نمایش می‌دهد و KPI، نمودار، metadata یا معماری اطلاعات تازه اضافه نمی‌کند؛
- master/detail فقط در صورت اثبات نیاز task ساخته می‌شود و پیش‌فرض این checkpoint نیست؛
- bottom navigation در تمام مسیرهای این checkpoint مقصد `حساب` را active نگه می‌دارد؛
- ترتیب مصوب پوسته `خانه / بازار / پیام‌رسان / عملیات / حساب`، SVGهای مصوب و حداقل‌های `11px / 52px / 78px / 48×48px` حفظ می‌شوند.

## ۹. خروجی‌های شواهد نهایی

### مستقیم از Figma

- `assets/figma-account-profile-scenarios.png`
- `assets/figma-security-storage-scenarios.png`
- `assets/figma-notification-center-scenarios.png`
- `assets/figma-state-route-visibility-push-matrix.png`
- `assets/figma-responsive-and-desktop-proofs.png`
- `assets/figma-desktop-security-sessions-1440x900.png`
- `assets/figma-stage0b5-audit-metrics.json`

### مشتق‌شده محلی

- `assets/local-evidence/local-account-profile-scenarios.png`
- `assets/local-evidence/local-profile-visibility-matrix.png`
- `assets/local-evidence/local-security-storage-scenarios.png`
- `assets/local-evidence/local-notification-center-scenarios.png`
- `assets/local-evidence/local-state-route-push-atlas.png`
- `assets/local-evidence/local-account-notifications-responsive-sweep.png`
- `assets/local-evidence/local-desktop-security-sessions-1440x900.png`
- `assets/local-evidence/local-account-profile-security-notifications-validation-metrics.json`

metrics مستقیم با SHA-256 برابر `351f6afafb0e2d3b1a08e908dcd88cb72d9d2fd4fed8110c3fb22c12c6658d94` و metrics محلی با SHA-256 برابر `293524253132064c0056022132325e213f8122fc43c0bd8a3a9601a7f222ca91` ثبت شده‌اند. شناسه source node، ابعاد و checksum تمام PNGها در manifest قطعی است.

## ۱۰. قرارداد ۲۷ assertion مرتب‌شده

ترتیب و شناسه‌ها بخشی از قرارداد fail-closed هستند:

1. `font-vazirmatn-loaded`
2. `ten-mobile-scenarios-complete`
3. `mobile-roots-exact-390x844`
4. `no-product-overflow-or-clipping`
5. `touch-targets-44`
6. `cta-height-48`
7. `responsive-width-sweep`
8. `desktop-security-sessions-1440x900`
9. `desktop-adds-no-facts`
10. `shell-account-destination-invariant`
11. `canonical-account-route-contract`
12. `minimal-content-contract`
13. `synthetic-identities-only`
14. `account-hub-destinations-unique`
15. `accountant-account-scope-bounded`
16. `self-profile-progressive-disclosure`
17. `profile-address-feedback-in-context`
18. `public-profile-visibility-matrix-exact`
19. `public-profile-actions-bounded`
20. `session-list-metadata-bounded`
21. `session-decision-feedback-in-context`
22. `storage-action-feedback-in-context`
23. `notification-center-metadata-bounded`
24. `notification-empty-error-semantics-distinct`
25. `push-state-matrix-complete-and-truthful`
26. `recovery-state-atlas-complete`
27. `protected-interiors-absent`

هر ۲۷ assertion با ترتیب دقیق پاس شد. audit مستقیم صفر overflow/clipping و صفر blocker ثبت کرد. harness run `2839230-1786210464518` نیز `27 / 27`، صفر failure، صفر page error، exact file set، pre/post assertion یکسان، canonical DOM ثابت با SHA-256 برابر `c5693eb79e0405cd7946a7d3ebeedd6b9a8fac3b7fe3699454aeac4c82eae831` و promotion اتمیک/fail-closed را ثبت کرد.

## ۱۱. نتیجه audit مستقیم و component inventory

- audit schema 2: `27 / 27`، صفر blocker، صفر overflow/text clip و `142` target با کمینه `44×44px`؛
- پنج width proof دقیق و desktop `1440×900` با `19 / 19` fact parity؛
- ۶۵ variable، ۹ text style، ۲ effect، ۱۲ component set و ۵۴ variant؛
- ۷۷ instance bound و صفر detached instance؛
- حداقل کنتراست متن `4.548:1` و حداقل کنتراست focus برابر `3.972:1`؛
- صفر route/backend metadata، صفر noise ممنوع، صفر interior بازار/پیام‌رسان و فقط identity synthetic.

دو carry-forward غیرمسدودکننده Figma صادقانه حفظ شده‌اند:

1. avatar initials در component inherited، text style محلی exact متناظر ندارد؛ همه متن‌ها همچنان Vazirmatn هستند و fit/contrast پاس است.
2. variantهای قدیمی Operations-active Bottom Navigation بدهی focus/layout/style پیش از 0B-5 دارند؛ variantهای Account-active و rootهای این Stage قرارداد interaction را پاس کرده‌اند.

## ۱۲. Sites خصوصی و drift review

پیش‌نمایش مشتق‌شده روی [URL خصوصی Stage 0B-5](https://trading-bot-uiux-stage0b5.mohsenbarari235.chatgpt.site) منتشر شد:

- project `appgprj_6a776942e35c819198a0dcab372ac65e`، slug `trading-bot-uiux-stage0b5` و source commit `9a710611d52ca24c5cd300fc010f464fb1ad33c3`؛
- version `1` با ID برابر `appgprj_6a776942e35c819198a0dcab372ac65e~appgver_d0bbd46aed2481918e6dd16377916706`؛
- deployment موفق `appgdep_6a776aae0604819185ff740c57054fac` روی `site---6a776942e35c819198a0dcab372ac65e`؛ وضعیت موفق در `2026-08-08T17:43:19.978890Z` ثبت و connector reread نهایی در `2026-08-08T17:43:58.035651Z` تأیید شد؛
- archive محلی `391385` بایت با SHA-256 برابر `22d41b9fd89c7543c6be518fc7f23304daab84dd2390e936126bdd0a55f2f731`؛
- connector-normalized content شامل `27` فایل و `890880` بایت با SHA-256 برابر `058f397ec23d099c0ddcaf84e3f1a54ed1bcce86dc241cc43624b50d0bfc70a2`.

access policy بلافاصله قبل و بعد deploy برابر `custom`، owner-only، یک allowed user، صفر group و صفر external visitor بود. anonymous probe در `2026-08-08T17:43:56Z` پاسخ `401/no-store/no-referrer` و عنوان `Sign in required` گرفت. bypass token درخواست نشد و signed-in live content واکشی نشد؛ در نتیجه drift review فنی فقط `passed_artifact_and_source_bound` است. مالک محصول سپس در ۲۰۲۶-۰۸-۰۸ خروجی بصری را به‌صورت صریح تأیید کرد.

build با Next.js `16.3.0`، `npm audit --audit-level=high` با صفر vulnerability، Worker/ASSETS، سه probe محلی `200`، چهار فونت محلی byte-identical، sensitive scan بدون source map/env/key/log و پنجره ۱۵ دقیقه‌ای با صفر Worker error پاس شد.

## ۱۳. baseline runtime

baseline دقیق ۱۳ فایل مرتبط با `--maxWorkers=1 --no-file-parallelism` اجرا و `13 / 13` فایل و `128 / 128` تست با exit code صفر و Vitest duration برابر `38.54s` پاس شد. هشدار stale بودن Browserslist/caniuse-lite و logهای failure mock‌شده NetworkError، clear-cache و browser notification خروجی موردانتظارند.

این baseline رفتار فعلی را ثبت می‌کند و پیاده‌سازی طراحی تازه را اثبات نمی‌کند.

## ۱۴. carry-forwardهای اجرایی و حدود ادعا

- redirect مسیرهای legacy `/settings` و `/notifications` به مقصد canonical؛
- تفکیک runtime واقعی loading/empty/error و size-error/zero؛
- permission و privacy enforcement سمت server؛
- قرارداد truthful برای terminate session و حفظ نشست جاری؛
- WebSocket reconnect/recovery history، Push و receipt delivery؛
- session inventory محلی per-server و تصمیم آینده درباره نمایش cross-server؛
- visual freeze بازار/پیام‌رسان در هر تغییر component/token مشترک.

Figma، PNG، harness و Sites نمی‌توانند authorization، API mutation، redirect واقعی، session revocation، delivery، realtime recovery، screen reader، keyboard یا شبکه واقعی را اثبات کنند. شروع کدنویسی redesign فقط پس از جمع‌بندی و تأیید صریح `0B-6` مجاز است.

## ۱۵. گیت جاری و مراحل باقی‌مانده

Figma canonical، audit/export مستقیم، evidence محلی fail-closed و Sites خصوصی ثبت و راستی‌آزمایی شده‌اند؛ شواهد فنی Stage 0B-5 بسته و خروجی بصری آن در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح توسط مالک محصول تأیید شده است. `0B-6` فقط در سطح قرارداد طراحی در حال انجام است و runtime unauthorized می‌ماند.

مراحل باقی‌مانده:

1. تکمیل و تأیید صریح `0B-6` — قرارداد نهایی سیستم و گیت شروع پیاده‌سازی
2. `Stage 1` — اعتماد و تداوم کار
3. `Stage 2` — Design System V2 محافظت‌شده
4. `Stage 3` — پوسته، ورود و جریان‌های عمومی
5. `Stage 4` — هسته استفاده روزانه
6. `Stage 5` — workspace مشتریان و حسابداران
7. `Stage 6` — مدیریت و پروفایل
8. `Stage 7` — motion، دسترس‌پذیری و polish
9. `Stage 8` — پذیرش نهایی و عرضه مرحله‌ای
