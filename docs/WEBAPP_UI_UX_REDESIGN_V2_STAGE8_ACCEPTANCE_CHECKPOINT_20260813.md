# Stage 8 — پیش‌نویس رهگیری پذیرش و عرضهٔ مرحله‌ای

تاریخ: ۲۰۲۶-۰۸-۱۳

وضعیت: **`stage8_partial_synthetic_browser_slices_evidence_only_no_full_acceptance_no_production`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

source اصلاح shared-dependency: `82cb016e`

source canonicalization کامپوننتی: `7588d9c20b995244197d8de09392dd6a5f61b195`

source تاریخی بازیابی محدود directory/profile: `4415b7431a6b67965d24c44f6f9f0e59e48ed422`

(validation تغییر محلی P1 پس از آن pending است)

source محدود route-first directory-transition: `31c69d5a5d2fb1e2c08d9647473d3612b9d85629`
(evidence-only؛ بدون افزایش full matrix)

## ۱. مجوز و حد آن

دستور مالک برای ادامهٔ Stage 8، یکپارچگی و زیباسازی UI/UX با کنترل ایمنی، و ثبت رهگیری
دسترسی/شواهد محدود را مجاز کرد.

این مجوز **merge به main، staging deploy، production deploy، یا Sites محصول** نیست.

`stage8CompleteAuthority=false`. این checkpoint نه پذیرش کامل است و نه پایان roadmap؛ سبز بودن تست به‌تنهایی پذیرش زیبایی نیست و بازبینی انسانی مالک هنوز لازم است.

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
تعداد سلول‌های پذیرش کامل اجراشده صفر است؛ viewport، state، interaction و environment هنوز
requirement هستند و به cell-level evidence کامل متصل نشده‌اند. بازار/پیام‌رسان redesign نشده‌اند.

## ۳. شواهد محدود 8A و مرجع طراحی

منبع redacted: `docs/uiux-stage8-acceptance-rollout/STAGE8A_EXECUTION_RECEIPTS.json`

- slice دسترسی/shell محلی و synthetic در `390×844`: شش profile × هشت scenario، ۴۸/۴۸ cell و
  ۵۰ assertion؛ این مورد full matrix نیست و به `executedFullMatrixCellCount` افزوده نمی‌شود.
- slice تاریخی directory/profile محلی و synthetic در source `4415b743`: `/profile`، `/users/:id`،
  `/admin/users` و `/admin/users/:id` در ۳۶۰/۳۹۰/۴۱۴/۴۳۰/۱۴۴۰ بررسی شدند؛ این مورد full
  role×route acceptance نیست و validation تغییر محلی P1 پس از آن pending است.
- slice رفتاری route-first directory در source `31c69d5a`: چهار scenario local/synthetic production
  browser (pointer و Enter در ۳۹۰، pointer در ۱۴۴۰، و `/admin/commodities` برای مدیر میانی) با
  ۳۳ assertion اجرا شد. سه گذار directory هرکدام دقیقاً یک `GET /api/users/` با پاسخ ۲۰۰ کامل و
  non-aborted، صفر requestfailed/`ERR_ABORTED` و حداکثر یک UserManager/list مرئی داشتند؛ deep-link
  denied به `/admin` canonical شد و CommodityManager یا commodity API نداشت. Telegram probe محلی
  intercept شد و external transport مشاهده نشد. این slice evidence-only است و cell پذیرش کامل نیست.
- Figma: file `z8jgJxST4O2APzWnlyP9gv`، page `486:1455`، section `508:95`، frame `508:96`
  (`390×844`) و provenance `511:151`. audit محدود: ۲۷ text با Vazirmatn، ۷ instance UIUX،
  ۴۹ node token-bound و صفر phone/email/URL/query ناایمن؛ review بصری بدون crop/overlap.

این سه receipt و مرجع Figma live/editable یا local/synthetic هستند؛ screenshot/hash-freeze، runtime
accessibility acceptance، sign-off زیبایی مالک، یا release authority نیستند. receipt/reference مبتنی
بر `4415b743` تا validation P1 ادعایی دربارهٔ working tree جاری ندارند. artifact خام browser در
repository ذخیره نشده و هیچ Sites action انجام نشده است.

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
- canonicalization مدیر میانی و سه slice محدود source-bound ثبت شده‌اند، اما full matrix همچنان صفر است؛
- protected-surface hashهای تاریخی overwrite نشده‌اند؛
- اجرای واقعی viewport/state/interaction/environment و sign-off زیبایی مالک هنوز pending است؛
- عرضه فقط به‌صورت مدل تیمی و rollback-safe توصیف شده و شروع نشده است؛
- merge/staging/production/Sites انجام نشده و مجاز نیست تا مالک جداگانه بگوید.
