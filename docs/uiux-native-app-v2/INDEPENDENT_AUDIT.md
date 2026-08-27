# ممیزی مستقل Native App Feel V2

تاریخ: ۲۰۲۶-۰۸-۲۷

شاخه: `candidate/webapp-native-app-v2`

HEAD ممیزی‌شده: `e574154da55cebc612a9ed0e3e5a443d4b7b961e`

درخت: `f920c14147b1fbdcf3bc862762f4aee6ca46a6fa`

حکم: `BLOCKED — CORRECTION REQUIRED`

این حکم جایگزین `READY FOR INDEPENDENT NATIVE UI REVIEW` است. تأیید زیبایی مالک دیگر gate این track نیست؛ پایان track فقط با ممیزی مستقل کد، مرورگر، دسترس‌پذیری، یکپارچگی زبان طراحی و گیت‌های فنی مجاز است.

## آنچه مستقل تأیید شد

- Market نسبت به `main` دست‌نخورده است.
- build تولید محلی پاس شد: ۱۷۱ فایل، ۵٬۷۱۹٬۶۲۴ بایت، digest درخت `ab012757b010970723791639df776d8617b7c859700a8ba121ce2d4f04525084`.
- `npm run guard:ui` و `git diff --check` پاس شدند.
- ۲۹ مسیر غیر Market در ۳۹۰×۸۴۴ و ۱۴۴۰×۹۰۰، جمعاً ۵۸ اجرا، بدون شکست navigation، page error، document overflow یا CTA پوشیده در انتهای اسکرول اجرا شدند.
- کنترل بی‌نام و کنترل تودرتو در حالت‌های اولیهٔ مصنوعی صفر بود؛ این نتیجه جای ماتریس حالت و overlay را نمی‌گیرد.
- `vue-tsc -p tsconfig.app.json --noEmit` همچنان قرمز است. اختلاف candidate-added نسبت به baseline گزارش نشده، اما گیت کل فرانت سبز نیست.

شواهد خام مرورگر خارج مخزن ماندند. digestها: گزارش `b7283e013b892385a0f9b66eda3d000cb51da416f37961642169ee4acfd59942`، برگه موبایل `480f7ca136f31e84674ef4e7c96e73dbfc9c4ce1dbe83852854232361dcf0c02`، برگه دسکتاپ `c8a7bcdc3a4c360b943049276fb256b14a1420b125572e3ce14b258332f40d6b`.

## مانع‌های قطعی

1. `AppActionOverflow` فوکوس اولیه، Arrow/Home/End و بازگرداندن فوکوس با Escape ندارد؛ نقص روی پروفایل و عملیات پخش می‌شود.
2. زیرمسیرهای مدیریت `h1` ندارند؛ `/admin/channels` دو `main` دارد؛ `/chat` و `/share-receive` هیچ `main` ندارند. تست فعلی این سه مسیر را عمداً skip می‌کند.
3. عملیات هنوز هم‌زمان از `--ds-*` و scope زندهٔ `--ui-v2-*` استفاده می‌کند. کادر workspace، section و inset روی هم قرار گرفته و زبان واحد حاصل نشده است.
4. `ChatForwardModal` dialog semantics، label جستجو، focus trap، Escape و focus restoration ندارد. Lightbox، Location، Gallery و Attachment نیز باید با همان قرارداد مستقل مرور شوند.
5. `CreateChannelView` هنوز HelpPopover، دو `main` و کارت‌های تودرتو دارد.
6. skeleton عنوان پروفایل یک `div` داخل `h1` دارد.
7. account hub به baseline بومی نزدیک است، اما عملیات، پرونده‌ها، نشست‌ها، پروفایل، دعوت و کانال هنوز کارت وب و متن/قاب اضافی دارند.
8. mock عمومی E2E هر API ناشناخته را با `{}` موفق می‌کند و چند معیار پذیرش را نمی‌سنجد؛ بنابراین ۴۲ pass فعلی پایان track را اثبات نمی‌کند.
9. `ROADMAP.md` و `SURFACE_INVENTORY.json` پیش از رفع gapهای ثبت‌شده فازها را complete/ready نامیده بودند.

## ترتیب اصلاح اجباری

1. primitive مشترک، landmark، heading و semantic markup.
2. حذف adapter زندهٔ `ui-v2` از عملیات و همگرایی کامل روی `--ds-*`.
3. حذف کارت‌های تودرتو و ساده‌سازی عملیات، پروفایل، حساب، دعوت و کانال.
4. استانداردسازی تمام overlayهای پیام‌رسان با dialog semantics، focus trap، Escape، restoration و هدف ۴۸ پیکسل؛ بدون تغییر رفتار، schema، WebSocket، upload، cache یا آلبوم.
5. fail-closed کردن mockهای E2E، حذف skipهای landmark و پوشش state/interaction واقعی.
6. اجرای ۳۶۰، ۳۹۰، ۴۳۰، ۷۶۸ و ۱۴۴۰؛ loading/empty/error/retry/offline/long Persian؛ keyboard، Escape، soft keyboard، ۲۰۰٪ zoom و reduced motion.
7. سبز شدن `vue-tsc`، Vitest، build، guardها و ممیزی مستقل نهایی.

## مرز انتشار

این شاخه آمادهٔ merge، push یا deploy نیست. staging نیز نباید به‌عنوان جایگزین تکمیل gate استفاده شود. `.mimosa/` artifact محلی Codex است و در این track stage یا commit نمی‌شود.
