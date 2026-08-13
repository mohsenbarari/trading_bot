# Stage 8 — پیش‌نویس رهگیری پذیرش و عرضهٔ مرحله‌ای

تاریخ: ۲۰۲۶-۰۸-۱۳

وضعیت: **`stage8_expected_access_traceability_draft_no_executed_acceptance_no_production`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

آخرین source اصلاح shared-dependency: `82cb016e`

## ۱. مجوز و حد آن

دستور مالک برای ادامهٔ Stage 8، نوشتن رهگیری دسترسی موردانتظار، بازخوانی freeze بازار/پیام‌رسان، و مدل عرضهٔ محدود تیمی را مجاز کرد.

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

نسخهٔ ۲ فقط ۳۰ × ۹ = ۲۷۰ نتیجهٔ normal-case دسترسی را از source رهگیری می‌کند. تعداد سلول‌های پذیرش کامل اجراشده صفر است؛ viewport، state، interaction و environment هنوز requirement هستند و به cell-level evidence متصل نشده‌اند. بازار/پیام‌رسان فقط freeze hash دارند، نه redesign.

## ۳. Visual freeze بازار و پیام‌رسان

پس از Stage 7 source، `guard:ui` دوباره pass شد و hashها با checkpoint Stage 4/6 یکی است:

- Home market interior: `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860`
- Market runtime files: `37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589` / `162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058`
- Messenger overlay: `f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58` / `3089210a77936d29754c9478fcdf40619acd08f35d1e8c64f6266fe8efb1699a`
- AdminMessages: `5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a`
- TradingSettings: `509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa`

این hashها بازنویسی نشده‌اند.

## ۴. مدل عرضه

1. فعال‌سازی محدود روی همین branch برای تیم/نقش آزمایشی؛
2. مشاهدهٔ خطا و بازخورد چند روزه **بدون** production؛
3. گسترش مرحله‌ای فقط پس از اجازهٔ صریح مالک؛
4. حذف adapter قدیمی فقط پس از rollback اثبات‌شده.

Sites و production در این Stage شروع نشده‌اند.

## ۵. گیت بعدی (فنی و بصری)

- ۲۷۰ نتیجهٔ موردانتظار مسیر×پروفایل به source متصل است؛
- freeze بازار/پیام‌رسان با hash زنده تأیید شد؛
- اجرای واقعی viewport/state/interaction/environment و sign-off زیبایی مالک هنوز pending است؛
- عرضه فقط به‌صورت مدل تیمی و rollback-safe توصیف شده و شروع نشده است؛
- merge/production انجام نشده و مجاز نیست تا مالک جداگانه بگوید.
