# Stage 8 — پذیرش نهایی و عرضه مرحله‌ای

تاریخ: ۲۰۲۶-۰۸-۱۳

وضعیت: **`stage8_acceptance_matrix_and_team_rollout_documented_no_production`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## ۱. مجوز و حد آن

دستور مالک برای ادامه تا پایان Stage 8، نوشتن ماتریس پذیرش، تأیید freeze تصویری بازار/پیام‌رسان، و مدل عرضهٔ محدود تیمی را مجاز کرد.

این مجوز **merge به main، staging deploy، production deploy، یا Sites محصول** نیست.

`stage8CompleteAuthority=false`. سبز بودن تست به‌تنهایی پذیرش زیبایی نیست؛ بازبینی انسانی مالک هنوز لازم است.

## ۲. ماتریس پذیرش

منبع: `docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json`

- نقش: مهمان، عضو، مشتری، حسابدار، سرگروه، مدیر میانی، مدیر ارشد
- مسیر: تمام routeهای `uiRouteContract` به‌جز catch-all recovery به‌عنوان سطح محصول
- viewport موبایل: ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴، ۴۳۰
- viewport تطبیقی: ۷۶۸، ۱۰۲۴، ۱۴۴۰
- state: loading، empty، normal، dense، error، slow، offline، stale
- تعامل: touch، keyboard، zoom، reduced-motion
- محیط هدف بعدی: مرورگر موبایل، PWA، Telegram WebView غیرپیام‌رسان

پوشش evidence موجود از Stage 3–7 به هر سلول نگاشته شده است. سلول‌های بازار/پیام‌رسان فقط freeze hash دارند، نه redesign.

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

## ۵. گیت پایان roadmap (فنی، نه زیبایی)

- ماتریس نقش×مسیر×viewport×state مکتوب است؛
- freeze بازار/پیام‌رسان با hash زنده تأیید شد؛
- عرضه فقط تیمی و rollback-safe توصیف شده است؛
- merge/production انجام نشده و مجاز نیست تا مالک جداگانه بگوید.
