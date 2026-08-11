# Stage 6 — مدیریت و پروفایل

تاریخ آغاز: ۲۰۲۶-۰۸-۱۱

وضعیت: **`stage6_authorized_phase1_source_complete`**

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

صفحهٔ تازهٔ Figma این Stage برابر `08 — Stage 6 Admin & Profile` (`321:18`) است. root فعلی (`321:19`) فقط delta اجرایی Phase 1 را ثبت می‌کند و هیچ board تأییدشدهٔ `0B-4`، `0B-5` یا Stage 5 را بازنویسی نمی‌کند.

## ۳. Phase 1 — ورودی مدیریت

اولین slice عمداً به `/admin` و `AdminPanel` محدود است.

- فقط مقصدهای مدیریتی واقعاً مجاز نقش جاری دیده می‌شوند؛
- action key، label، icon، role filter، event `navigate` و routeهای موجود حفظ می‌شوند؛
- heading تکراری، سطح دسترسی تزئینی، شمارندهٔ دسته/ابزار و accordionهای صرفاً متراکم حذف می‌شوند؛
- UI هیچ total، count یا badge pending را بدون بارگذاری authoritative نمی‌سازد؛
- actionها به فهرست قابل‌فهم و keyboard-accessible از primitiveهای موجود محدود می‌شوند؛
- طراحی mobile-first با پنج عرض مرجع انجام می‌شود؛ desktop فقط همان مقصدها را adaptive نمایش می‌دهد و fact یا KPI جدید اضافه نمی‌کند.

فایل‌های مجاز این slice فقط `frontend/src/components/AdminPanel.vue`، تست مستقیم آن و در صورت نیاز expectation محدود `AdminView.test.ts` هستند. تغییر child workflow، API، router، route guard، permission یا backend در Phase 1 مجاز نیست.

## ۴. خارج از Phase 1

موارد زیر sliceهای مستقل و گیت‌دار بعدی‌اند:

- invitation management و pending invitation؛
- user directory، query/back/scroll context و user detail؛
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

rollback به revert commit مستقل Phase 1 محدود است؛ پس از rollback، routeها، role filterها، `navigate` payloadها و suiteهای Admin دوباره اجرا می‌شوند. تا زمانی که این گیت‌ها سبز نیستند، invitation، user detail و profile شروع نمی‌شوند.

## ۷. Figma، evidence و Sites

Figma منبع اصلی طراحی است. Phase 1 فقط boundary card و proof موبایل ورودی مدیریت را در صفحهٔ Stage 6 اضافه می‌کند؛ pending-attention canonical فقط در state دارای دادهٔ واقعی باقی می‌ماند و به runtime Phase 1 نسبت داده نمی‌شود.

Sites در شروع Stage 6 هیچ mutation یا deployment ندارد. پس از implementation، browser/Figma evidence و freeze محلی، فقط یک repo/project تازه و private owner-only برای evidence ساخته می‌شود. آن preview product deployment نیست و هیچ staging/production را تغییر نمی‌دهد.

## ۸. رسید Phase 1

برش source فقط این فایل‌ها را تغییر می‌دهد:

- `frontend/src/components/AdminPanel.vue`؛
- `frontend/src/components/AdminPanel.test.ts`؛
- expectationهای بازگشت به منوی واقعی در `frontend/src/views/AdminView.test.ts`؛
- انتظار heading canonical در `frontend/e2e/admin-smoke.spec.ts`.

نتیجهٔ runtime: `AdminPanel` یک `nav` برچسب‌دار با `ul/li/button` است؛ همهٔ action keyها، iconها، role filterها و `navigate` payloadهای پیشین حفظ شده‌اند. heading دوم، access-note، counterهای synthetic، accordion و Help copy تکراری حذف شده‌اند. هیچ fetch، pending badge، API، router، guard، permission یا child workflow تغییر نکرده است.

Figma Phase 1 در صفحهٔ `08 — Stage 6 Admin & Profile`، root `321:19` و mobile proof `326:20` ثبت شده است: عرض `390×844`، header/nav پیوندخورده و فقط دو مقصد حقیقی بدون pending/count ساختگی.

گیت‌های source اجراشده:

- focused unit/route/primitives: ۵ فایل و ۵۰ تست pass؛ recheck مستقیم AdminPanel/AdminView: ۲۴ تست pass؛
- `vue-tsc --noEmit`، `npm run build`، `npm run guard:ui` و `git diff --check`: pass؛
- ESLint فایل‌های تغییرکرده هیچ diagnostic جدیدی ندارد؛ پنج `no-explicit-any` در `AdminView.test.ts` عین baseline هستند؛
- Playwright discovery چهار smoke را می‌شناسد و heading آن با `مرکز مدیریت` canonical شد.

اجرای محلی smoke واقعی Chromium تا UI پیش نرفت، زیرا `http://127.0.0.1:8000/api/auth/dev-login` در محیط حاضر پاسخ موفق نداد. این یک precondition backend محیطی است، نه failure محصول یا receipt browser؛ بنابراین browser freeze و Sites Stage 6 هنوز شروع نشده‌اند. هیچ product/staging/production یا Sites mutation انجام نشده است.
