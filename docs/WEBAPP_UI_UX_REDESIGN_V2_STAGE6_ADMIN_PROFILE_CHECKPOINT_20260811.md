# Stage 6 — مدیریت و پروفایل

تاریخ آغاز: ۲۰۲۶-۰۸-۱۱

وضعیت: **`stage6_phase1_phase2_complete_pii_authority_decision_required`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## ۱. مجوز و حد آن

دستور صریح کنونی مالک، توقف تاریخی پس از Stage 5 را فقط برای آغاز Stage 6 supersede می‌کند. این مجوز، اجازهٔ انتشار محصول، تغییر staging یا production، فعال‌سازی runtime خارج از branch، یا overwrite کردن preview/evidence Stage 5 نیست.

Stage 6 به‌صورت route-scoped، rollback-safe و با گیت مستقل پیش می‌رود. هر زیرسطح فقط پس از بسته‌شدن گیت فنی زیرسطح قبل وارد کار می‌شود.

## ۲. ترتیب منابع حقیقت

در تعارض، این ترتیب حاکم است:

1. دستور صریح کنونی مالک؛
2. قرارداد سیستم V2 و checkpointهای `0B-4` مدیریت/دعوت و `0B-5` حساب/پروفایل؛
3. سیاست خلوتی هدفمند و roadmap V2؛
4. Figma editable canonical با file key `z8jgJxST4O2APzWnlyP9gv`؛
5. کد و contractهای backend فعلی، برای authority، permission و outcome واقعی.

صفحهٔ تازهٔ Figma این Stage برابر `08 — Stage 6 Admin & Profile` (`321:18`) است. root فعلی (`321:19`) deltaهای اجرایی Phase 1 و Phase 2 را ثبت می‌کند و هیچ board تأییدشدهٔ `0B-4`، `0B-5` یا Stage 5 را بازنویسی نمی‌کند.

## ۳. Phase 1 — ورودی مدیریت

اولین slice عمداً به `/admin` و `AdminPanel` محدود است.

- فقط مقصدهای مدیریتی واقعاً مجاز نقش جاری دیده می‌شوند؛
- action key، label، icon، role filter، event `navigate` و routeهای موجود حفظ می‌شوند؛
- heading تکراری، سطح دسترسی تزئینی، شمارندهٔ دسته/ابزار و accordionهای صرفاً متراکم حذف می‌شوند؛
- UI هیچ total، count یا badge pending را بدون بارگذاری authoritative نمی‌سازد؛
- actionها به فهرست قابل‌فهم و keyboard-accessible از primitiveهای موجود محدود می‌شوند؛
- طراحی mobile-first با پنج عرض مرجع انجام می‌شود؛ desktop فقط همان مقصدها را adaptive نمایش می‌دهد و fact یا KPI جدید اضافه نمی‌کند.

فایل‌های مجاز این slice فقط `frontend/src/components/AdminPanel.vue`، تست مستقیم آن و در صورت نیاز expectation محدود `AdminView.test.ts` هستند. تغییر child workflow، API، router، route guard، permission یا backend در Phase 1 مجاز نیست.

## ۴. خارج از Phase 2

موارد زیر sliceهای مستقل و گیت‌دار بعدی‌اند:

- invitation management و pending invitation؛
- authority matrix برای self/same-level/middle-manager/super-admin؛
- PII profile، mask/hide سمت server و تجربهٔ profile self/public؛
- commodity feedback persistence؛
- dialogهای sensitive به‌جای `alert`/`confirm`؛
- هر تغییر در Admin Messages و System Settings غیرمحافظت‌شده.

`/market`، `/chat`، `/share-receive`، `/admin/channels`، interiorهای market/messenger در `AdminMessagesView` و `TradingSettings` و Home Market همچنان protected هستند. هیچ shared CSS/token بدون guard و proof نبود drift تغییر نمی‌کند.

## ۵. قرارداد authority، PII و state

- visibility هرگز جای enforcement backend نیست؛
- pending endpoint بدون total معتبر، source count برای KPI یا badge نیست؛
- success delivery فقط با receipt همان channel اعلام می‌شود؛
- در evidence و Figma فقط هویت synthetic استفاده می‌شود و URL/token واقعی نمایش داده نمی‌شود؛
- public profile برای viewer عادی mobile masked/hidden است و هر اصلاح آن باید server-side role matrix داشته باشد؛
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

Figma منبع اصلی طراحی است. Phase 1 boundary card و proof موبایل ورودی مدیریت را ثبت می‌کند و Phase 2 proofهای directory، desktop، recovery و privacy route-context را به همان صفحه می‌افزاید؛ pending-attention canonical فقط در state دارای دادهٔ واقعی باقی می‌ماند و به runtime نسبت داده نمی‌شود.

Sites در Stage 6 هنوز هیچ mutation یا deployment ندارد. فقط پس از بسته‌شدن همهٔ sliceهای Stage 6، browser/Figma evidence نهایی و freeze محلی، یک repo/project تازه و private owner-only برای evidence ساخته می‌شود. آن preview product deployment نیست و هیچ staging/production را تغییر نمی‌دهد.

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

Figma Phase 2 در همان page/root با directory mobile `336:50`، desktop proof `366:138` و recovery/detail proofهای `371:194`، `373:226`، `373:254` و `373:282` به‌روزرسانی شد. annotation قرارداد می‌گوید فقط `scroll` route context است و `q`/`account_name` در URL/history/storage ذخیره نمی‌شوند. تمام هویت‌ها synthetic هستند؛ export/audit/freeze نهایی Figma عمداً تا closure کامل Stage 6 انجام نمی‌شود.

گیت بعدی و تنها blocker Stage 6: مالک باید matrix server-enforced برای visibility PII و authority actionهای self/same-level/middle/super را صریح تأیید کند. تغییر client-only masking، تغییر PublicProfile، `/users-public/search`، project-users یا Messenger/Forward در این Phase مجاز و انجام‌شده نیست.
