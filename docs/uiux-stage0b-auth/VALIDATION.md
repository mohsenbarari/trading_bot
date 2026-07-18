# Stage 0B-1 validation evidence

تاریخ: ۲۰۲۶-۰۷-۱۸

دامنه: artifactهای design-only ورود، دعوت، ثبت‌نام، recovery و تنظیم رمز؛ بدون تغییر runtime محصول

## وضعیت این سند

capture اصلاح‌شده پس از رسیدگی به یافته‌های دور دوم اجرا شد. نتیجه‌های زیر مستقیماً از `assets/stage0b-auth-validation-metrics.json` با schema نسخه `4` و زمان تولید `2026-07-18T19:01:34.748Z` خوانده شده‌اند.

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
- تصاویر بخش keyboard فقط illustration ثابت CSS از app viewport و صفحه‌کلید فرضی‌اند؛ اجرای کیبورد سیستم‌عامل، تغییر visual viewport یا WebView را اثبات نمی‌کنند. ابعاد اجزای illustration و این محدودیت به‌صورت مستقل در JSON ثبت می‌شود.

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

- کمترین slack طبیعی در عرض ۳۹۰: `116px`؛ قاب دارای slack منفی: صفر.
- حداقل slack به‌ترتیب در عرض‌های ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰: `95px`، `95px`، `116px`، `116px` و `116px`؛ قاب نامتناسب در همه عرض‌ها: صفر.
- عرض دارای horizontal overflow: صفر.

این اندازه‌گیری بر محتوای normal-flow قاب‌های فعلی تکیه دارد. descendantهای `absolute` یا `fixed` که در ارتفاع طبیعی containing block مشارکت نمی‌کنند می‌توانند از این روش عبور کنند؛ بنابراین پیاده‌سازی باید علاوه بر این sweep، bounding/viewport assertion مخصوص محتوای out-of-flow داشته باشد یا آن را در این خانواده ممنوع کند.

## اهداف تعاملی و اندازه لمس

اندازه‌گیری دیگر به `.touch-target` محدود نیست. مجموعه هدف شامل تمام کنترل‌های semantic قابل‌مشاهده مانند `button`، لینک، input، select، textarea، summary، نقش‌های تعاملی و tabindex، به‌علاوه اهدافی است که با `data-actionable="true"` یا `.touch-target` صریحاً معرفی شده‌اند.

JSON موارد زیر را جداگانه گزارش می‌کند:

- همه اهداف semantic/actionable و context آن‌ها؛
- اهداف کوچک‌تر از `44 × 44`؛
- اهداف actionable که marker `.touch-target` ندارند؛
- حداقل عرض و ارتفاع مشاهده‌شده؛
- اهداف illustration کیبورد با context مستقل.
- CTAهای اصلی به‌صورت مستقل با آستانه ارتفاع `48px`.

متن‌های `.state-card__example` در atlas نمونه copy/ظاهر state هستند و کنترل تعاملی واقعی محسوب نمی‌شوند. آن‌ها جداگانه فهرست می‌شوند و اگر در آینده semantic یا actionable شوند، گزارش آن را به‌عنوان وضعیت غیرمنتظره مشخص می‌کند.

نتیجه render جدید:

- تعداد اهداف semantic/actionable در عرض مرجع: `41`؛ کمینه `49 × 44` پیکسل.
- اهداف زیر ۴۴ پیکسل: صفر.
- اهداف actionable بدون marker: صفر.
- تعداد دکمه‌های CTA اندازه‌گیری‌شده، شامل variantهای primary، secondary، danger و ghost: `20`؛ کمینه ارتفاع `48px` و مورد زیر آستانه: صفر.
- ۱۲ نمونه اقدام atlas جداگانه طبقه‌بندی شدند؛ نمونه ناخواسته تعاملی: صفر.

## پیمایش فوکوس و نام‌گذاری تزئینات

پس از render، اسکریپت یک پیمایش مصنوعی `Tab` روی همه کنترل‌های قابل‌مشاهده و فعال artifact انجام می‌دهد. این پیمایش وجود `:focus-visible`، outline پیوسته با ضخامت حداقل `3px`، تطابق رنگ محاسبه‌شده هر outline با token آزموده‌شده `--focus-ring` و کامل‌بودن چرخه را ثبت می‌کند. دو CTA غیرفعال عمداً از شمار focusable کنار گذاشته می‌شوند؛ در نتیجه `39` کنترل مورد انتظار و هر `39` کنترل بازدید شدند، رنگ محاسبه‌شده همه `rgb(31, 94, 216)`، کمینه ضخامت outline برابر `3px` و failure صفر بود.

شش glyph تزئینی picker و ۱۴ marker تزئینی notice نیز از نظر source بررسی شدند. همه با `aria-hidden="true"` از نام دسترس‌پذیر کنار گذاشته شده‌اند و pickerها `aria-label` صریح و بدون glyph دارند؛ exposed decorative count صفر است. این source audit و Tab sweep مربوط به artifact ثابت است و جای accessibility-tree، screen-reader یا runtime WebView test را نمی‌گیرد.

## اثبات بارگذاری فونت

صرف مشاهده نام فونت در `font-family` کافی نیست. capture وزن‌های ۴۰۰، ۵۰۰، ۶۰۰ و ۷۰۰ Vazirmatn را با `document.fonts.load` درخواست می‌کند، سپس برای هر وزن `document.fonts.check` و وضعیت `FontFace`های متناظر را می‌سنجد. در صورت loadنشدن واقعی هر وزن، capture fail می‌شود.

نتیجه render جدید:

- Vazirmatn load assertion برای وزن‌های ۴۰۰، ۵۰۰، ۶۰۰ و ۷۰۰: موفق.
- computed body font family: `Vazirmatn, Tahoma, Arial, sans-serif`.

## کنتراست tokenهای scoped

capture اکنون ۱۴ جفت consequential را با threshold متناسب با کاربرد می‌سنجد: ۱۱ جفت متن/visible glyph با آستانه محافظه‌کارانه `4.5:1` و سه جفت focus indicator با آستانه `3:1` نسبت به سطح مجاور. همه جفت‌ها عبور کردند:

- کمترین نسبت متن عادی `4.632:1` برای `--text-muted` روی `--page`؛
- neutral state tag برابر `5.185:1`؛
- warning marker برابر `4.913:1`، با وجود تزئینی و `aria-hidden` بودن marker؛
- focus ring روی page، surface و action surface به‌ترتیب `5.307:1`، `5.752:1` و `5.210:1`.

این نتیجه فقط tokenها، سطوح و thresholdهای ثبت‌شده را پوشش می‌دهد و جای audit کامل runtime، forced-colors، zoom یا theme دیگر را نمی‌گیرد.

## رفتار fail-closed شواهد

همه PNGها و metrics ابتدا در یک sibling staging directory تولید می‌شوند. فقط پس از عبور همه assertionهای تعداد و ابعاد قاب‌ها، fit طبیعی در هر پنج عرض، نبود horizontal overflow، حداقل ۴۴ پیکسل برای همه اهداف، marker صریح اهداف، حداقل ۴۸ پیکسل برای CTA، پیمایش کامل فوکوس، semantics تزئینات، بارگذاری فونت و کنتراست scoped، کل مجموعه با directory promotion جایگزین assets قبلی می‌شود. بنابراین اجرای ناموفق، نسل تازه و ناقصی از PNGها را کنار metrics قدیمی باقی نمی‌گذارد.

## تست قرارداد رفتاری موجود

rerun پس از اصلاح artifact:

```text
LoginView + InviteLanding + WebRegister + SetupPassword
48 passed, 0 failures, 0 errors
```

JUnit خام آخرین rerun در `/tmp/stage0b-auth-unit-tests-r3.xml` قرار دارد: InviteLanding برابر ۸، LoginView برابر ۳۰، SetupPassword برابر ۴ و WebRegister برابر ۶ تست؛ skipped نیز صفر است. این عدد فقط قرارداد runtime موجود را پوشش می‌دهد و صحت HTML/prototype جدید را ثابت نمی‌کند.

## مرز شواهد

- این شواهد درباره prototype ثابت و قرارداد runtime موجود است؛ runtime جدیدی برای UI طراحی‌شده وجود ندارد.
- fit طبیعی و horizontal overflow در mock ثابت، جای تست device/WebView، صفحه‌کلید واقعی، screen reader یا full browser matrix را نمی‌گیرد و out-of-flow content را به‌طور عام اثبات نمی‌کند.
- keyboard illustration مدرک دسترسی CTA با کیبورد سیستم‌عامل نیست. Tab sweep فقط focus artifact ثابت را می‌سنجد؛ در پیاده‌سازی باید visual viewport، focus retention و کیبورد واقعی جداگانه آزموده شوند.
- atlas action sampleها کنترل نیستند و از پذیرش touch target کنار گذاشته می‌شوند، مگر اینکه بعداً به عنصر semantic/actionable تبدیل شوند.
- بازار و پیام‌رسان در این capture بررسی یا بازطراحی نمی‌شوند.
