# Stage 0B-1 validation evidence

تاریخ: ۲۰۲۶-۰۷-۱۸

دامنه: artifactهای design-only ورود، دعوت، ثبت‌نام، recovery و تنظیم رمز؛ بدون تغییر runtime محصول

## وضعیت این سند

capture اصلاح‌شده پس از بسته‌شدن HTML اجرا شد. نتیجه‌های زیر مستقیماً از `assets/stage0b-auth-validation-metrics.json` با زمان تولید `2026-07-18T09:08:14.124Z` خوانده شده‌اند.

## دستور بازتولید

پس از تکمیل HTML و فقط در گیت capture:

```bash
node docs/uiux-stage0b-auth/capture-prototype.cjs
```

خروجی machine-readable در `assets/stage0b-auth-validation-metrics.json` نوشته می‌شود.

## قرارداد capture و ابعاد

- تعداد قاب‌های سناریویی مورد انتظار: `15`؛ شامل ۱۱ قاب قبلی و چهار قاب `invite-web-only`، `recovery-unavailable`، `register-direct` و `setup-password-error`.
- عنصر mock موبایل در عرض مرجع باید outer size برابر `390 × 844` داشته باشد.
- PNG مستقل هر سناریو از کل `<article>` گرفته می‌شود؛ در نتیجه label سناریو و mock موبایل را با هم دارد و ابعاد PNG آن الزاماً `390 × 844` نیست. ابعاد واقعی هر PNG مستقیماً از header فایل PNG خوانده و جداگانه در JSON ثبت می‌شود.
- تصاویر بخش keyboard فقط illustration ثابت CSS از app viewport و صفحه‌کلید فرضی‌اند؛ اجرای کیبورد سیستم‌عامل، تغییر visual viewport، focus واقعی یا WebView را اثبات نمی‌کنند. ابعاد اجزای illustration و این محدودیت به‌صورت مستقل در JSON ثبت می‌شود.

نتیجه render جدید:

- تعداد scenario PNG: `15`.
- تعداد state card ثبت و assertشده در atlas: `41`.
- تعداد phone element با outer size دقیق `390 × 844`: `15` از `15`؛ mismatch صفر.
- هر scenario PNG برابر `432 × 933` است، زیرا label و phone را با هم capture می‌کند. ابعاد groupها: mobile برابر `1920 × 3831`، keyboard برابر `1920 × 814`، atlas برابر `1920 × 1851` و desktop برابر `1440 × 901` است.

## اندازه‌گیری fit عمودی و responsive

برای جلوگیری از پنهان‌شدن فشار محتوا توسط `height: 100%` یا flex shrink، هر `.screen` در یک context خارج از viewport با همان عرض clone می‌شود؛ محدودیت ارتفاع برداشته می‌شود و `naturalHeight` از بیشینه bounding height و `scrollHeight` به‌دست می‌آید. سپس این مقدار با `availableHeight` واقعی screen مقایسه می‌شود:

- `slack = availableHeight - naturalHeight`
- `slack < 0` یعنی محتوای طبیعی در فضای موجود جا نمی‌شود.
- `renderedScrollHeight > renderedClientHeight` نیز به‌عنوان سیگنال مستقل overflow حفظ می‌شود، اما به‌تنهایی معیار fit طبیعی نیست.

همین اندازه‌گیری برای تک‌تک قاب‌ها در عرض‌های `360`، `375`، `390`، `414` و `430` انجام می‌شود. بررسی قبلی horizontal overflow نیز بدون حذف حفظ شده است.

نتیجه render جدید:

- کمترین slack طبیعی در عرض ۳۹۰: `123px`؛ قاب دارای slack منفی: صفر.
- حداقل slack به‌ترتیب در عرض‌های ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰: `122px`، `122px`، `123px`، `134px` و `134px`؛ قاب نامتناسب در همه عرض‌ها: صفر.
- عرض دارای horizontal overflow: صفر.

## اهداف تعاملی و اندازه لمس

اندازه‌گیری دیگر به `.touch-target` محدود نیست. مجموعه هدف شامل تمام کنترل‌های semantic قابل‌مشاهده مانند `button`، لینک، input، select، textarea، summary، نقش‌های تعاملی و tabindex، به‌علاوه اهدافی است که با `data-actionable="true"` یا `.touch-target` صریحاً معرفی شده‌اند.

JSON موارد زیر را جداگانه گزارش می‌کند:

- همه اهداف semantic/actionable و context آن‌ها؛
- اهداف کوچک‌تر از `44 × 44`؛
- اهداف actionable که marker `.touch-target` ندارند؛
- حداقل عرض و ارتفاع مشاهده‌شده؛
- اهداف illustration کیبورد با context مستقل.

متن‌های `.state-card__example` در atlas نمونه copy/ظاهر state هستند و کنترل تعاملی واقعی محسوب نمی‌شوند. آن‌ها جداگانه فهرست می‌شوند و اگر در آینده semantic یا actionable شوند، گزارش آن را به‌عنوان وضعیت غیرمنتظره مشخص می‌کند.

نتیجه render جدید:

- تعداد اهداف semantic/actionable در عرض مرجع: `41`؛ کمینه `49 × 44` پیکسل.
- اهداف زیر ۴۴ پیکسل: صفر.
- اهداف actionable بدون marker: صفر.
- ۱۲ نمونه اقدام atlas جداگانه طبقه‌بندی شدند؛ نمونه ناخواسته تعاملی: صفر.

## اثبات بارگذاری فونت

صرف مشاهده نام فونت در `font-family` کافی نیست. capture وزن‌های ۴۰۰، ۵۰۰، ۶۰۰ و ۷۰۰ Vazirmatn را با `document.fonts.load` درخواست می‌کند، سپس برای هر وزن `document.fonts.check` و وضعیت `FontFace`های متناظر را می‌سنجد. در صورت loadنشدن واقعی هر وزن، capture fail می‌شود.

نتیجه render جدید:

- Vazirmatn load assertion برای وزن‌های ۴۰۰، ۵۰۰، ۶۰۰ و ۷۰۰: موفق.
- computed body font family: `Vazirmatn, Tahoma, Arial, sans-serif`.

## کنتراست tokenهای scoped

capture نسبت کنتراست ۹ جفت consequential متن/پس‌زمینه در محدوده همین prototype را با آستانه `4.5:1` برای متن عادی می‌سنجد. همه جفت‌ها عبور کردند؛ کمترین نسبت `4.632:1` برای `--text-muted` روی `--page` بود. نسبت placeholder روی surface برابر `5.020:1` و warning روی warning surface برابر `4.913:1` است. این نتیجه فقط همین tokenها و جفت‌های ثبت‌شده را پوشش می‌دهد و جای audit کامل runtime را نمی‌گیرد.

## تست قرارداد رفتاری موجود

rerun پس از اصلاح artifact:

```text
LoginView + InviteLanding + WebRegister + SetupPassword
48 passed, 0 failures, 0 errors
```

JUnit خام در `/tmp/stage0b-auth-unit-tests-r2.xml` قرار دارد: InviteLanding برابر ۸، LoginView برابر ۳۰، SetupPassword برابر ۴ و WebRegister برابر ۶ تست؛ skipped نیز صفر است. این عدد فقط قرارداد runtime موجود را پوشش می‌دهد و صحت HTML/prototype جدید را ثابت نمی‌کند.

## مرز شواهد

- این شواهد درباره prototype ثابت و قرارداد runtime موجود است؛ runtime جدیدی برای UI طراحی‌شده وجود ندارد.
- fit طبیعی و horizontal overflow در mock ثابت، جای تست device/WebView، صفحه‌کلید واقعی، screen reader یا full browser matrix را نمی‌گیرد.
- keyboard illustration مدرک قابلیت دسترسی CTA با کیبورد واقعی نیست؛ آن ادعا باید هنگام پیاده‌سازی با Playwright روی visual viewport و focus واقعی آزموده شود.
- atlas action sampleها کنترل نیستند و از پذیرش touch target کنار گذاشته می‌شوند، مگر اینکه بعداً به عنصر semantic/actionable تبدیل شوند.
- بازار و پیام‌رسان در این capture بررسی یا بازطراحی نمی‌شوند.
