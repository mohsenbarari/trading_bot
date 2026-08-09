# ممیزی و نقشه راه بازطراحی UI/UX وب‌اپ — نسخه دوم

تاریخ: ۲۰۲۶-۰۷-۱۷

وضعیت: مورد تأیید مالک محصول؛ Stage 0A، `0B-1..0B-6`، Stage 1، Stage 2 و Stage 3 بسته شده‌اند. قرارداد و پیشروی بی‌وقفه roadmap در `2026-08-08T20:57:28.073Z` تأیید شده است. Stage 3 با implementation commit، گیت فنی/protected diff، Figma read-only closure، evidence hash-bound و Sites خصوصی owner-only `complete` است؛ Stage 4 مرحله بعدی مجاز با وضعیت `authorized_not_started` است.

نوع خروجی: تحلیل و نقشه راه مصوب؛ بدون تغییر کد محصول

شاخه اجرا: `condidate/webapp-ui-ux-redesign-v2`، ساخته‌شده مستقیم از `main`

تصمیم جهت بصری: مالک محصول در ۲۰۲۶-۰۷-۱۸ گزینه «مالی مدرن» را انتخاب کرد. تمام نمونه‌ها و پیاده‌سازی‌های بعدی باید از قرارداد ثبت‌شده در `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0_VISUAL_DIRECTION_20260718.md` پیروی کنند.

تصمیم تراکم اطلاعات: مالک محصول در ۲۰۲۶-۰۸-۰۸ «خلوتی هدفمند» را به‌عنوان قاعده الزام‌آور همه صفحه‌ها تعیین کرد. قرارداد کامل در `docs/WEBAPP_UI_UX_REDESIGN_V2_CONTENT_MINIMALISM_POLICY_20260808.md` و inventory صفحه‌ها در `docs/WEBAPP_UI_UX_REDESIGN_V2_CONTENT_PRUNING_INVENTORY_20260808.md` ثبت شده و در صورت تعارض، نمونه‌های پرتراکم قبلی را در سطح محتوا جایگزین می‌کند.

تصمیم گیت بازبینی: مالک محصول در ۲۰۲۶-۰۸-۰۸ اعلام کرد تأیید چهار دستیار خارجی فعلاً شرط پیشروی نیست و ادامه با Figma انجام شود. پروتکل و یافته‌های معتبر حفظ می‌شوند، اما prompt/ZIP و quorum جدید تا درخواست بعدی blocking نیستند. سپس در `2026-08-08T20:57:28.073Z` قرارداد نهایی و پیشروی بی‌وقفه تا تکمیل roadmap یا توقف صریح مالک را تأیید کرد؛ تأیید جداگانه مالک برای هر Stage لازم نیست، اما گیت فنی، test، protected diff و rollback هر Stage الزام‌آور می‌ماند. closure فنی/Sites `0B-6` پاس و Stage 1 مجاز شده است، بدون اینکه runtime edit آن در Stage 0 ادعا شود.

## ۱. جمع‌بندی مدیریتی

وب‌اپ فعلی از نظر فنی فاقد زیرساخت UI نیست. Design Token، کامپوننت‌های مشترک، فونت فارسی، آیکون‌های یکدست، تست viewport و baseline تصویری در پروژه وجود دارند. مشکل اصلی این است که چند موج اصلاح، هر بار بخشی از محصول را بهتر کرده‌اند اما یک «جهت طراحی واحد و تأییدشده» بالای همه آن‌ها وجود نداشته است.

در وضعیت فعلی سه نسل ظاهری هم‌زمان دیده می‌شود:

1. استایل‌ها و کنترل‌های قدیمی و محلی هر صفحه؛
2. خانواده `ds-workspace-*`؛
3. خانواده جدیدتر `ui-*` و `App*`.

نتیجه این هم‌زیستی این است که یک صفحه تمیزتر شده، صفحه دیگر حرفه‌ای‌تر شده و صفحه سوم فقط روی کامپوننت جدید سوار شده، اما کاربر هنوز حس نمی‌کند همه آن‌ها متعلق به یک محصول‌اند.

راه‌حل پیشنهادی «یک دور پولیش صفحه‌به‌صفحه دیگر» نیست. مسیر درست سه لایه دارد:

1. ابتدا جهت بصری، معماری اطلاعات و اصول تعامل با نمونه‌های واقعی و تأیید مالک محصول قطعی شود؛
2. سپس خطاهای پایه UX که اعتماد و تداوم کار را مختل می‌کنند برطرف شوند؛
3. در نهایت صفحه‌ها به‌ترتیب اهمیت، روی یک زبان بصری واحد مهاجرت کنند.

## ۲. سیاست Mobile-first

طبق اعلام مالک محصول، حدود ۹۵٪ استفاده از وب‌اپ روی موبایل است. بنابراین:

- نسخه موبایل مرجع اصلی طراحی و پذیرش است؛
- طراحی ابتدا برای عرض‌های ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰ انجام می‌شود؛
- دسترسی با یک دست، محدوده شست، safe area، کیبورد موبایل، PWA و WebView در اولویت‌اند؛
- دسکتاپ یک نسخه تطبیقی و کامل است، نه یک محصول جدا با معماری متفاوت؛
- صفحه‌های متمرکز مثل ورود، ثبت‌نام و فرم‌های کوتاه می‌توانند روی دسکتاپ عمداً باریک بمانند؛
- فقط فضاهای کاری سنگین مانند مشتریان، حسابداران و مدیریت در دسکتاپ به layout عریض یا دو ستونه تبدیل می‌شوند؛
- ساخت یک سیستم ناوبری کاملاً جداگانه برای دسکتاپ در این roadmap اولویت ندارد، مگر داده استفاده یا تست کاربری ضرورت آن را نشان دهد.

### ۲.۱. سیاست خلوتی هدفمند و اقتصاد اطلاعات

هر داده، آمار، badge، summary، helper، notice، card و action که به‌صورت پیش‌فرض دیده می‌شود باید دست‌کم یکی از این کارها را انجام دهد:

1. تصمیم یا اقدام جاری کاربر را ممکن یا سریع کند؛
2. وضعیت یا نتیجه لازم را روشن کند؛
3. از خطای مهم، پیامد یا ریسک حریم خصوصی جلوگیری کند.

مورد فاقد این توجیه حذف می‌شود یا فقط هنگام نیاز با افشای تدریجی نمایش داده می‌شود. نام route/path، منبع backend و جزئیات داخلی سیستم محتوای کاربر نیستند. تعداد روابط، مسیرها، ابزارها یا آیتم‌ها فقط وقتی دیده می‌شود که مستقیماً روی تصمیم، محدودیت، انتخاب یا اقدام جاری اثر داشته باشد. «مالی مدرن» مجوز افزایش تراکم اطلاعات نیست و داده پرتراکم در prototype/QA فقط آزمون تحمل layout است، نه الزام نمایش هم‌زمان همه metadata.

برای هر صفحه یک inventory با تصمیم‌های `Keep`، `On demand` و `Remove` لازم است. نبود overflow یا وجود فضای خالی، به‌تنهایی خلوتی و مفیدبودن محتوا را ثابت نمی‌کند.

## ۳. مرز دقیق کار

### داخل محدوده

- ورود، OTP، تأیید نشست و بازیابی هویت؛
- دعوت‌نامه، ثبت‌نام وب و تنظیم رمز اولیه؛
- خانه و داشبورد، به‌جز بازطراحی ویجت‌ها و رفتارهای داخلی بازار؛
- عملیات و ناوبری نقش‌محور؛
- فضای کاری مشتریان؛
- فضای کاری حسابداران؛
- مرکز حساب؛
- تنظیمات حساب، امنیت، نشست‌ها و حافظه؛
- اعلان‌ها؛
- پروفایل شخصی و پروفایل عمومی؛
- پنل مدیریت، دعوت‌ها، کاربران و کالاها؛
- پوسته و اعلان‌های مدیریتی غیرمرتبط با بازار در بخش پیام‌های مدیریت؛
- قسمت‌های غیرمرتبط با بازار در تنظیمات سیستم؛
- پوسته عمومی و احراز‌شده، نوار ناوبری، toast، dialog، loading، empty، error و confirm؛
- PWA install prompt، انتخاب‌گر تاریخ و micro interactionهای عمومی.

### خارج از محدوده و محافظت‌شده

- مسیر `/market` و تمام UI و رفتار داخلی بازار؛
- مسیر `/chat` و تمام UI و رفتار داخلی پیام‌رسان؛
- مسیر `/share-receive` چون بخشی از جریان اشتراک‌گذاری پیام‌رسان است؛
- مسیر `/admin/channels` چون به مدیریت کانال پیام‌رسان تعلق دارد؛
- بخش‌های بازار و اعلان بازار در `/admin/messages`؛
- کنترل‌ها و ویجت‌های بازار در داشبورد و تنظیمات سیستم؛
- هر تغییر در منطق معامله، آفر، realtime بازار یا رفتار پیام‌رسان.

قاعده محافظتی: بازار و پیام‌رسان بازطراحی یا تحلیل داخلی نمی‌شوند. فقط baseline آن‌ها به‌عنوان «قفل عدم تغییر» نگه داشته می‌شود تا تغییرات مشترک CSS یا navigation تصادفی به آن‌ها نشت نکند.

## ۴. موجودی سطح‌های وب‌اپ

| حوزه | مسیر یا سطح | نقش اصلی | وضعیت ممیزی |
| --- | --- | --- | --- |
| ورود | `/login` | مهمان | داخل محدوده |
| دعوت | `/i/:code` | مهمان | داخل محدوده |
| ثبت‌نام | `/register` | مهمان | داخل محدوده |
| رمز اولیه | `/setup-password` | کاربر واجد شرایط | داخل محدوده |
| خانه | `/` | همه کاربران واردشده | داخل محدوده با حفظ ویجت بازار |
| عملیات | `/operations` | نقش‌محور | داخل محدوده |
| مشتریان | `/operations/customers` و جزئیات | مالک/سرگروه | داخل محدوده |
| حسابداران | `/operations/accountants` و جزئیات | مالک/سرگروه | داخل محدوده |
| مرکز حساب | `/account` | همه کاربران | داخل محدوده |
| امنیت/حافظه | `/account/security`، `/account/storage`، `/settings` | همه کاربران با قواعد نقش | داخل محدوده |
| اعلان‌ها | `/account/notifications` و `/notifications` | همه کاربران | داخل محدوده |
| پروفایل خود | `/profile` | همه کاربران | داخل محدوده |
| پروفایل دیگران | `/users/:id` | نقش‌محور | داخل محدوده؛ رفتار ورود به پیام‌رسان حفظ می‌شود |
| مرکز مدیریت | `/admin` | مدیر میانی/ارشد | داخل محدوده |
| دعوت مدیریتی | `/admin/invitations` | مدیر میانی/ارشد | داخل محدوده |
| کاربران | `/admin/users` و جزئیات | مدیر میانی/ارشد | داخل محدوده |
| کالاها | `/admin/commodities` | مدیر ارشد | داخل محدوده؛ خود بازار دست‌نخورده |
| پیام‌های مدیریت | `/admin/messages` | مدیر ارشد | پوسته و اعلان عمومی داخل محدوده؛ کنترل پیام بازار محافظت‌شده |
| تنظیمات سیستم | `/admin/system` | مدیر ارشد | فقط بخش‌های غیر بازار |
| بازار | `/market` | کاربران مجاز | خارج از محدوده |
| پیام‌رسان | `/chat` | کاربران واردشده | خارج از محدوده |
| کانال/اشتراک‌گذاری | `/admin/channels`، `/share-receive` | پیام‌رسان | خارج از محدوده |

## ۵. یافته‌های ممیزی

یادداشت وضعیت: این بخش snapshot ممیزیِ پیش از اجرای Stage 1 را حفظ می‌کند. موارد اعتماد، recovery و اقدام حساسِ متعلق به Stage 1 در closure همان مرحله بسته شده‌اند؛ باقی موارد مطابق مالکیت Stageهای 2 تا 8 پیگیری می‌شوند.

### ۵.۱. چرا اصلاحات قبلی به نتیجه مورد انتظار نرسیده‌اند؟

موج‌های قبلی بخش زیادی از مشکلات فنی را درست کرده‌اند: routeهای جدید، primitiveها، responsive guard، safe area، تست واحد و screenshot baseline اضافه شده‌اند. بااین‌حال معیار «تکمیل» عمدتاً عبور تست‌ها، نبود overflow و استفاده از component مشترک بوده است؛ نه تأیید انسانی زیبایی، خوانایی و حس یکپارچه محصول.

شکاف‌های اصلی روند قبلی:

- جهت بصری قبل از کدنویسی با مالک محصول انتخاب و تأیید نشده است؛
- desktop width و هویت بصری چند بار به مراحل بعد موکول شده‌اند؛
- baselineها غالباً حالت empty و داده mock کم‌تراکم را می‌سنجند؛
- زیبایی و hierarchy با green بودن unit test قابل سنجش نیست؛
- تغییرات صفحه‌به‌صفحه انجام شده و بدهی نسل‌های قبلی هم‌زمان باقی مانده است؛
- برخی roadmapها «تکمیل‌شده» اعلام شده‌اند، درحالی‌که خود handoffها هنوز componentهای بسیار بزرگ، CSS محلی و visual QA روی دستگاه واقعی را بدهی باقی‌مانده ثبت کرده‌اند.

### ۵.۲. یکپارچگی بصری

- `main.css` بیش از ۲۰۰۰ خط دارد و دو خانواده layout/component تقریباً موازی تعریف می‌کند؛
- `AppPage`، `WorkspaceShell` و layout اختصاصی داشبورد سه قرارداد متفاوت صفحه می‌سازند؛
- چهار component قدیمی پروفایل و روابط هنوز تعداد زیادی button/input بومی و استایل مستقل دارند؛
- در صفحه‌های کلیدی بیش از ۲۳ هزار خط template/script/style متمرکز شده است؛
- `PublicProfile.vue` بیش از ۳۲۰۰ خط، manager مشتری بیش از ۲۵۰۰ خط و manager حسابدار نزدیک ۱۹۰۰ خط دارد؛
- card-inside-card، borderهای متعدد و pastel surfaceهای زیاد، hierarchy را ضعیف و صفحه را سنگین کرده‌اند؛
- طیف amber در بسیاری از نقاط هم‌زمان نقش برند، warning، CTA و سطح تزئینی را بازی می‌کند؛
- typography از اندازه‌های بسیار کوچک استفاده می‌کند؛ برچسب نوار پایین در بعضی حالت‌ها حدود ۹٫۶ پیکسل است؛
- وزن‌های غیررسمی مثل 650، 750، 850 و 950 نتیجه فونت را غیرقابل پیش‌بینی می‌کنند؛
- چند رنگ CTA فعلی با متن سفید در بخش روشن خود به کنتراست AA نمی‌رسند.

### ۵.۳. معماری اطلاعات و ناوبری

- پوسته احراز‌شده فقط از `/login` حذف می‌شود؛ در نتیجه صفحات عمومی دعوت و ثبت‌نام هم نوار کاربر واردشده را می‌گیرند؛
- اعلان‌ها دو URL و تنظیمات سه URL برای یک محتوا دارند، بدون مسیر canonical روشن؛
- دو گزینه در مرکز حساب به یک مقصد مشترک می‌روند؛
- رفتار Back در اعلان، تنظیمات، پروفایل، workspace و admin یکسان نیست؛
- عملیات و پنل مدیریت بخشی از مقصدها را دوباره تکرار می‌کنند؛
- state بخش مدیریت هم‌زمان در URL، state داخلی و back stack اختصاصی نگهداری می‌شود؛
- برای برخی نقش‌ها tab عملیات وجود دارد اما محتوای مفیدی ارائه نمی‌کند؛
- مسیر 404 و تجربه بازیابی از URL نامعتبر وجود ندارد؛
- هویت متن صفحه ورود هنوز کل محصول را فقط «بازار» معرفی می‌کند، درحالی‌که وب‌اپ حساب، عملیات، مدیریت و اعلان هم دارد.

### ۵.۴. اعتماد، خطا و تداوم کار

- شکست دریافت اطلاعات در داشبورد، پروفایل یا عملیات می‌تواند صفحه خالی یا loading دائمی بسازد؛
- خطای شبکه اعلان‌ها ممکن است به‌اشتباه «هیچ اعلانی یافت نشد» نمایش داده شود؛
- مرکز حساب قبل از دریافت داده واقعی، نام و وضعیت «فعال» فرضی نشان می‌دهد؛
- نتیجه برخی اقدام‌های مشتری/حسابدار در تب دیگری نمایش داده می‌شود و کاربر آن را نمی‌بیند؛
- confirm بعضی درخواست‌ها پیش از پایان عملیات بسته می‌شود؛
- پایان نشست یا خروج از همه نشست‌ها در برخی خطاها feedback روشن ندارد؛
- پیام موفقیت مدیریت کالاها بلافاصله با refresh داده پاک می‌شود.

این موارد باید قبل از تزئین ظاهری حل شوند، زیرا محصول زیبا با state غیرقابل اعتماد هنوز UX ضعیفی دارد.

### ۵.۵. دسترس‌پذیری و فرم‌ها

- Tabs و Filter Chips مقدار را با arrow key عوض می‌کنند اما focus DOM را هم‌گام نمی‌کنند؛
- ردیف کاربر ادمین clickable است اما قرارداد کامل keyboard ندارد؛
- برخی actionهای مدیریتی هنوز از `alert/confirm` مرورگر استفاده می‌کنند؛
- انتخاب‌گر تاریخ از نظر keyboard، aria و بازگرداندن focus کامل نیست؛
- انتخاب متن در کل body غیرفعال شده و کپی شماره، حساب، آدرس یا شناسه را سخت می‌کند؛
- فرم‌های موبایل همیشه `inputmode`، `autocomplete` و keyboard مناسب ندارند؛
- ورود و ثبت‌نام چندمرحله‌ای step indicator و focus announcement استاندارد ندارند؛
- وضعیت اتصال، اعلان push و تغییر مرحله‌ها live region منسجم ندارند.

### ۵.۶. پویایی و motion

پروژه transition و animation دارد، اما motion system ندارد. duration و easing در صفحه‌ها پراکنده‌اند و reduced-motion همه آن‌ها را پوشش نمی‌دهد.

پویایی مطلوب باید از این موارد بیاید:

- تغییر نرم و کوتاه state؛
- skeleton متناسب با ساختار واقعی؛
- feedback فوری روی press، save، copy و retry؛
- باز و بسته‌شدن کنترل‌شده sheet/dialog؛
- حفظ context هنگام رفت‌وبرگشت؛
- transition محدود بین مراحل فرم؛
- بدون animation دائمی و تزئینی که تمرکز را کم کند.

### ۵.۷. responsive و desktop

با توجه به سهم ۹۵٪ موبایل، باریک بودن صفحه ورود یا حساب روی دسکتاپ ذاتاً اشکال نیست. اشکال زمانی است که یک task سنگین مدیریتی نیز بدون استفاده از فضای بیشتر، مانند موبایل کشیده و طولانی بماند.

سیاست پیشنهادی عرض:

- Focused flow: حدود ۴۸۰ تا ۶۴۰ پیکسل در دسکتاپ؛
- Reading/list flow: حدود ۷۲۰ تا ۸۸۰ پیکسل؛
- Operational workspace: حدود ۱۰۸۰ تا ۱۲۸۰ پیکسل و master/detail در صورت نیاز؛
- موبایل همیشه single-column، با actionهای مهم در محدوده دسترس شست؛
- breakpointها به چهار قرارداد رسمی محدود شوند و layout داخل componentهای پیچیده در صورت لزوم با container behavior تطبیق یابد.

## ۶. شخصیت مصوب محصول

پس از مقایسه سه جهت Stage 0A، مالک محصول جهت **مالی مدرن: دقیق، سریع، ساختاریافته و خلوت** را انتخاب کرد. قرارداد آن چنین است:

- زمینه خنثی سرد و روشن؛
- سرمه‌ای برای hierarchy و آبی برای اقدام اصلی؛
- طلایی فقط به‌عنوان accent محدود و امضای برند، نه CTA یا پس‌زمینه غالب؛
- متن با کنتراست قوی؛
- success، danger، warning و info کاملاً semantic؛
- کارت کمتر، گروه‌بندی اطلاعات بیشتر؛
- اطلاعات پیش‌فرض کمتر و افشای تدریجی جزئیات ثانویه، مطابق سیاست خلوتی هدفمند؛
- border و shadow محدود و هدفمند؛
- سه radius رسمی؛
- spacing بر پایه ۴ پیکسل؛
- پنج نقش typography روشن: caption، body، label، title و display؛
- Lucide با اندازه‌های ثابت؛
- gradient فقط برای CTA اصلی یا نقطه برند؛
- blur/glass فقط برای shell شناور، نه همه کارت‌ها.

این انتخاب زبان بصری را قفل می‌کند؛ inventory محتوای هر صفحه همچنان تابع سیاست خلوتی هدفمند است.

## ۷. سناریوهای مرجع طراحی

### سناریو A — مهمان و ثبت‌نام

کاربر از دعوت وارد می‌شود، مسیر وب یا تلگرام را می‌فهمد، ثبت‌نام چندمرحله‌ای را با step روشن طی می‌کند، در خطا داده واردشده را از دست نمی‌دهد و هیچ navigation مخصوص کاربر واردشده را نمی‌بیند.

### سناریو B — کار روزانه روی موبایل

کاربر خانه را باز می‌کند و در چند ثانیه می‌فهمد وضعیت حساب چیست، امروز چه اتفاقی افتاده و اقدام بعدی کدام است. صفحه انبار کارت نیست و محتوای نقش‌محور فقط در صورت نیاز باز می‌شود.

### سناریو C — مدیریت مشتری یا حسابدار

مالک از عملیات وارد workspace می‌شود، رابطه را پیدا می‌کند، پرونده را می‌بیند، اقدام را انجام می‌دهد و نتیجه همان‌جا نمایش داده می‌شود. روی موبایل context گم نمی‌شود و روی دسکتاپ list/detail هم‌زمان قابل استفاده است.

### سناریو D — مدیریت کاربر

ادمین وارد مرکز مدیریت می‌شود، بدون عبور از منوهای تکراری کاربر را جست‌وجو می‌کند، وضعیت و ریسک اقدام را می‌بیند، confirm معنی‌دار می‌گیرد و نتیجه موفق یا ناموفق را از دست نمی‌دهد.

### سناریو E — حساب، نشست و اعلان

کاربر از مرکز حساب به مقصد canonical می‌رود، back behavior قابل پیش‌بینی است، empty با error اشتباه نمی‌شود و action حساس مانند پایان نشست feedback و busy state دارد.

### سناریو F — اینترنت کند یا قطع

هر صفحه بین loading، empty، error، stale و offline تفاوت می‌گذارد؛ retry نزدیک خطا قرار دارد؛ کاربر در loading بی‌انتها یا صفحه خالی نمی‌ماند.

## ۸. نقشه راه اجرایی

زمان‌ها تقریبی‌اند و پس از تأیید دامنه و ظرفیت تیم دقیق می‌شوند.

### مرحله ۰ — قرارداد طراحی و گیت تأیید

مدت پیشنهادی: ۳ تا ۵ روز

هدف: جلوگیری از یک دور اصلاح سلیقه‌ای دیگر.

خروجی‌ها:

- سه جهت بصری برای مقایسه و ثبت «مالی مدرن» به‌عنوان انتخاب نهایی مالک محصول؛
- prototype موبایل با داده واقعی/پرتراکم برای صفحه‌های نماینده:
  - ورود و ثبت‌نام؛
  - خانه؛
  - عملیات؛
  - workspace مشتری؛
  - کاربران ادمین؛
  - حساب/اعلان؛
- یک نمونه desktop فقط برای workspace پیچیده، جهت اثبات تطبیق؛
- نقشه IA و مقصد canonical هر قابلیت؛
- قرارداد header، back، bottom navigation و deep link؛
- فهرست surfaceهای محافظت‌شده بازار/پیام‌رسان و baseline عدم تغییر؛
- inventory ضرورت محتوا برای هر prototype با تصمیم‌های `Keep`، `On demand` و `Remove`؛
- معیار پذیرش بصری که با نظر مالک محصول بسته می‌شود.

گیت پایان: تا وقتی یک جهت بصری و prototypeهای نماینده صریحاً تأیید نشده‌اند، کدنویسی redesign شروع نمی‌شود.

#### پیشرفت checkpointهای Stage 0B

| checkpoint | وضعیت | مرجع |
| --- | --- | --- |
| `0B-1` ورود، دعوت و ثبت‌نام | تکمیل و تأیید مالک محصول برای ادامه | `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_AUTH_CHECKPOINT_20260718.md` |
| `0B-2` خانه و پوسته احراز‌شده | تکمیل و تأیید مالک محصول برای ادامه | `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_HOME_SHELL_CHECKPOINT_20260808.md` |
| `0B-3` عملیات و workspace مشتری/حسابدار | تکمیل و تأیید مالک محصول برای ادامه | `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_OPERATIONS_WORKSPACES_CHECKPOINT_20260808.md` |
| `0B-4` مدیریت کاربران و دعوت‌ها | تکمیل؛ شواهد فنی پاس و طراحی به‌صورت صریح توسط مالک محصول تأیید شده است | `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ADMIN_USERS_INVITATIONS_CHECKPOINT_20260808.md` |
| `0B-5` حساب، پروفایل، امنیت و اعلان‌ها | تکمیل؛ شواهد فنی پاس و خروجی بصری در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح توسط مالک محصول تأیید شده است | `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ACCOUNT_PROFILE_SECURITY_NOTIFICATIONS_CHECKPOINT_20260808.md` |
| `0B-6` قرارداد نهایی سیستم و پذیرش | تکمیل؛ Figma `32/32`، local `32/32`، baseline `322/322`، build/guard و Sites owner-only source-bound پاس؛ `runtimeImplementationAuthorized: true` و `nextAuthorizedRuntimeStage: Stage 1`، بدون runtime edit Stage 1 | [checkpoint قرارداد نهایی](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_FINAL_SYSTEM_CONTRACT_CHECKPOINT_20260808.md) |

خروجی `0B-3` شامل ۱۰ root موبایل، state atlas، پنج width proof، یک master/detail دقیق ۱۴۴۰×۹۰۰، ۶۵ variable، ۵ component set و ۱۷ variant در Figma رسمی است و با تأیید مالک محصول بسته شده است. قرارداد Phase 0 در `0B-4` نیز جست‌وجوی پایدار بدون filter ناقص، جدایی actionهای مدیریتی، دعوت استاندارد و stateهای permission-safe را قطعی کرده است؛ nodeها، snapshotها، audit schema 7، evidence محلی و preview خصوصی Sites از نظر فنی پاس و طراحی در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح توسط مالک محصول تأیید شده است. در `0B-5` صفحه canonical `117:2`، ۱۰ root موبایل، پنج width proof، desktop `1440×900`، state/visibility/push matrix، audit schema 2 با `27/27`، harness محلی `27/27` و preview خصوصی source-bound ثبت و پاس شده‌اند و خروجی بصری نیز در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح توسط مالک محصول تأیید شده است. در `0B-6` صفحه `168:1974` و board `168:1975`، قرارداد `SYS-01..SYS-14`، exact `32/32` در Figma و local، inventory ۲۹ route، پنج family، پنج width، desktop `1440×900` و baseline `35/35` فایل و `322/322` تست ثبت و پاس شده‌اند. Sites project `appgprj_6a77997ed65481918d71b8f1f3db541f` نیز private owner-only و source-bound پاس است؛ Stage 1 پس از این closure اجرا و با گیت فنی مستقل بسته شد.

### مرحله ۱ — اعتماد و تداوم کار

مدت پیشنهادی: ۴ تا ۶ روز

وضعیت اجرایی: **`complete`**؛ ده commit rollback-safe ثبت شده و گیت fresh با `413/413` تست متمرکز، `231/231` تست protected، `1255/1255` تست کامل، typecheck/build/guard، viewport `8/8` و protected source diff صفر بسته است. مقایسه تصویری exact برابر `21/26` است و پنج اختلاف شناخته‌شده بدون snapshot update به Stage 2 carry-forward شده‌اند؛ یکی از آن‌ها روی base دست‌نخورده دقیقاً بازتولید شده و چهار مورد دیگر snapshot/fixture قدیمی‌اند. مرجع: [checkpoint Stage 1](WEBAPP_UI_UX_REDESIGN_V2_STAGE1_TRUST_CONTINUITY_CHECKPOINT_20260808.md). Stage 2 پس از این closure آغاز شده و وضعیت جاری آن در checkpoint مستقل ثبت می‌شود.

هدف: برطرف کردن مشکلاتی که حتی در UI زیبا نیز تجربه را غیرقابل اعتماد می‌کنند.

سناریوهای تحت پوشش:

- loading، empty، error، stale، offline و retry؛
- جلوگیری از loading بی‌انتها؛
- جداسازی خطای شبکه اعلان‌ها از empty state؛
- حذف وضعیت‌های فرضی حساب پیش از دریافت داده؛
- feedback نزدیک مبدأ اقدام؛
- busy state و جلوگیری از ارسال تکراری؛
- confirm و نتیجه روشن برای actionهای حساس؛
- حفظ context در شکست درخواست.

گیت پایان: هیچ مسیر اصلی مجاز در خطای API به صفحه خالی، empty دروغین یا loading نامحدود ختم نشود.

### مرحله ۲ — Design System V2 محافظت‌شده

مدت پیشنهادی: ۴ تا ۶ روز

وضعیت اجرایی: **`complete`**. منبع Figma با inventory `65` variable (`20/26/19`)، `26` semantic alias، `10` text style، `2` effect و `12 set / 56 variant` frozen است؛ توزیع صحیح Button/Status برابر `6/4` و contract دقیق `29` route همه با `v2Scope: off` ثبت شده است. گیت فنی runtime، protected source/visual boundary، evidence hash-bound و Sites خصوصی source-bound پاس هستند. مرجع closure: [checkpoint Stage 2](WEBAPP_UI_UX_REDESIGN_V2_STAGE2_PROTECTED_DESIGN_SYSTEM_CHECKPOINT_20260809.md) و [بسته governance](uiux-stage2-protected-design-system/README.md).

هدف: ساخت یک مرجع واقعی، بدون تغییر ناخواسته بازار و پیام‌رسان.

خروجی‌ها:

- palette دارای کنتراست AA؛
- typography رسمی فارسی؛
- spacing، radius، elevation و icon scale؛
- motion و reduced-motion contract؛
- قرارداد صفحه، header، form، list، card، status، feedback و overlay؛
- component catalog با حالت‌های normal، loading، disabled، error و destructive؛
- scope بصری V2 فقط روی routeهای مجاز؛
- تعیین `ui-*` به‌عنوان خانواده مرجع و قرارداد carry-forward تبدیل `WorkspaceShell` / `ds-workspace-*` به adapter در Stage 4؛ Stage 2 آن‌ها را تغییر نمی‌دهد؛
- guard جلوگیری از hard-code و ساخت component محلی تکراری.

گیت پایان: componentهای مرجع در Figma و نمونه اجرایی کوچک، هر دو تأیید شوند و بازار/پیام‌رسان diff بصری نداشته باشند.

### مرحله ۳ — پوسته، ورود و جریان‌های عمومی

مدت پیشنهادی: ۵ تا ۷ روز

وضعیت اجرایی: **`complete`**؛ comparison base مرحله `3822df67a48e7ee3197bc6d67c79aa7ee84a7905` و implementation commit برابر `bfe4e59192d678eaf4776fbc025d3aa0f431896d` است. final serial برابر `58 file / 118 suite / 664 test`، browser acceptance برابر `23/23` و ماتریس viewport برابر `8/8` پاس شده‌اند؛ protected drift غیرمجاز صفر است؛ بسته evidence دقیق `31 file / 2,599,621 byte` با SHA-256 تجمیعی `ba851f9714c55d1d35d15e49d51fca31ebf0ca6c20de3b31b8a2592567489d24` و Sites نسخه ۱ private owner-only ثبت شده است. مرجع closure: [checkpoint Stage 3](WEBAPP_UI_UX_REDESIGN_V2_STAGE3_SHELL_AUTH_PUBLIC_FLOWS_CHECKPOINT_20260809.md). `stage4RuntimeImplementationAuthorized=true` و `stage4RuntimeWorkStarted=false` است.

snapshot نهایی دقیقاً `30` route دارد: scope برابر `route 5 / section 21 / off 4` و shell برابر `public 3 / focused-authenticated 1 / standard-authenticated 21 / protected-legacy 4 / system-recovery 1` است. outcomeهای canonical فقط `not-found`، `forbidden` و `deep-link-failure` هستند. تمام URLهای فعال دعوت Web در API، SMS و copy بات دقیقاً pathname هشت‌کاراکتری `/i/[A-Za-z0-9]{8}` دارند؛ responseهای create/list و relation داخلی دیگر fieldهای `token` یا `invitation_token` را برنمی‌گردانند. تنها استثنای response دارای bearer خام، lookup عمومی `no-store` است که short code را با `no-referrer/access-log-off` resolve و فقط تا انتخاب مسیر در memory نگه می‌دارد. شاخه Web bearer را فقط یک‌بار در body `POST /api/auth/registration-context/exchange` حمل و با context opaque ده‌دقیقه‌ای Redis عوض می‌کند؛ browser فقط cookie production/staging برابر `__Host-web_registration` با `Secure/HttpOnly/SameSite=Strict/Path=/` می‌گیرد و response context فقط factهای mask‌شده لازم دارد. تنها استثنای URL خام، deep-link user-initiated تلگرام با `t.me?start=<raw-invitation>` است؛ purpose-bound است و fallback وب را مجاز نمی‌کند. exchange binding تصادفی ۲۵۶ بیتی، tab-local و بدون token/code/mobile/route است؛ proof OTP فقط SHA-256-handle-bound و محدود به TTL context است و proof سراسری raw وجود ندارد. `/api/invitations/validate/{token}` بدون شرط، پیش از DB و با `410/no-store` بازنشسته است؛ سه endpoint خام registration نیز unconditional با `410/no-store` پیش از side effect بازنشسته‌اند و helperهای خصوصی فقط بعد از opaque-context verification اجرا می‌شوند. replay و response-loss exchange/OTP/complete/Login با همان binding/cookie و completion receipt محدود reconcile می‌شود. navigation failure resolveشده نیز failure است؛ Setup receipt موفق را برای retry بدون POST دوم نگه می‌دارد، Login/intended-route و شاخه مستقیم Register→Home پس از navigation موفق cleanup می‌شوند، و شاخه اختیاری Telegram context terminal را پس از receipt + `/api/auth/me` معتبر + render مرحله ۴ پاک و Skip failure را retry-safe می‌کند. refresh مرحله اختیاری Telegram یا marker باقی‌مانده، session محلی را با `/api/auth/me` validate و در صورت اعتبار به Home می‌برد. stale boot/chunk recovery فقط pathname را carry می‌کند، query/fragment را حذف می‌کند و `Referrer-Policy: no-referrer` در HTML/app/proxy contract است. DB engine با `hide_parameters=true` bind value را از خطای SQLAlchemy حذف می‌کند و redaction prefix-aware، bearerهای `INV/ACCT/CUST/REG` را در logging و error tracking می‌پوشاند؛ focused security regression مربوط به این مرز `23/23` و final auth/privacy/browser matrix پاس است. System Recovery میان shell مهمان و احرازشده تمایز می‌گذارد.

مرز protected در Stage 3، base-identical مطلق برای کل wrapper نیست: interiorهای محافظت‌شده و fixtureهای normal legacy بدون drift غیرمجاز مانده‌اند، ولی دقیقاً دو delta مشترک مصوب‌اند—PWA روی protected دیگر render نمی‌شود چون Home-only است، و access denial/unavailable به System Recovery می‌رود. Home market region با الگوریتم canonical شش‌بخشی `stage3-dashboard-market-region-v1`، اندازه `4553` byte و composite base=head برابر `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860` در guard و Git binding قفل شده است؛ `d037…` whole-file/legacy قرارداد region نیست. Session Approval runtime/modal، toast و BottomNav در false branch legacy با تست و browser acceptance نهایی پاس شده‌اند. استثنای ازپیش‌موجود OTP log فقط در staging با flag صریح مجاز و در default/deploy خاموش است؛ گسترش آن مجاز نیست.

release disposition نیز صریح است: opaque-cookie response دیگر raw `registration_token` را برای Login JS قدیمیِ ازقبل‌بارگذاری‌شده برنمی‌گرداند و compatibility fallback ممنوع است. production cutover باید اتمیک/maintenance یا version-gated forced reload باشد؛ interruption تب قدیمی با reload پذیرفته می‌شود و هیچ ادعای zero-downtime compatibility وجود ندارد. گیت‌های browser/final test، Figma read-only closure، Sites private owner-only و implementation commit همگی ثبت و بسته شده‌اند؛ این closure مجوز deploy محصول به production نیست.

صفحه‌ها:

- App shell عمومی و احراز‌شده؛
- Login؛
- Invite Landing؛
- Web Register؛
- Setup Password؛
- PWA install prompt؛
- toast، connection status و session approval shell.

تغییر تجربه:

- صفحات عمومی navigation کاربر واردشده را نمی‌بینند؛
- فرم چندمرحله‌ای step و focus management دارد؛
- keyboard و autocomplete موبایل درست‌اند؛
- prompt نصب PWA در زمان و مکان مناسب ظاهر می‌شود؛
- هویت متن صفحه ورود کل محصول را معرفی می‌کند، نه فقط بازار؛
- 404 و عدم دسترسی مسیر بازیابی روشن دارند.

گیت پایان: سناریوی دعوت تا ورود روی موبایل بدون ابهام، obstruction یا از دست رفتن داده طی شود.

### مرحله ۴ — هسته استفاده روزانه

مدت پیشنهادی: ۶ تا ۸ روز

صفحه‌ها:

- Dashboard؛
- Operations؛
- Account Hub؛
- Settings/Security/Storage؛
- Notifications.

تغییر تجربه:

- خانه بر «وضعیت، اتفاق امروز و اقدام بعدی» متمرکز شود؛
- وضعیت و اتفاق فقط وقتی نمایش داده شوند که نیازمند توجه یا مؤثر بر اقدام بعدی باشند؛ آمار عمومی و خلاصه‌های بی‌اقدام حذف شوند؛
- ویجت بازار به‌عنوان module محافظت‌شده حفظ شود؛
- `WorkspaceShell` و خانواده `ds-workspace-*` فقط در این Stage و با adapter V2 rollback-safe مهاجرت شوند؛ Stage 2 و Stage 3 مجاز به جذب زودهنگام این تغییر نیستند؛
- عملیات بر اساس نقش شخصی‌سازی شود، شمارنده مسیر/ابزار و توضیح دسترسی تکراری نداشته باشد و برای نقش فاقد اقدام، tab بی‌فایده نسازد؛
- مرکز حساب مقصدهای تکراری نداشته باشد؛
- اعلان و تنظیمات URL canonical و back behavior مشترک داشته باشند؛
- اعلان‌ها واقعاً حس notification center داشته باشند؛
- actionهای نشست و حافظه نتیجه قابل مشاهده داشته باشند.

گیت پایان: کاربر عادی، مشتری، حسابدار، سرگروه، مدیر میانی و مدیر ارشد هرکدام مسیر روزانه واضح و غیرتکراری داشته باشند و هیچ واحد محتوای پیش‌فرض بدون اثر ثبت‌شده بر تصمیم، اقدام، وضعیت ضروری یا ریسک باقی نماند.

### مرحله ۵ — Workspace مشتریان و حسابداران

مدت پیشنهادی: ۸ تا ۱۲ روز

هدف: یک الگوی مشترک برای دو جریان مشابه، بدون باقی‌ماندن حس modal قدیمی.

تغییر تجربه:

- mobile-first list → detail با context پایدار؛
- desktop adaptive master/detail برای استفاده محدود اما کامل دسکتاپ؛
- جست‌وجو، filterهای واقعاً لازم، دعوت‌های در انتظار و رابطه فعال با hierarchy روشن؛
- badge تعداد کل روابط حذف شود؛ شمارنده فقط برای صف نیازمند اقدام یا محدودیت مؤثر مجاز است؛
- detail دارای تب‌های کم و معنادار، نه accordionهای تو در تو؛
- action اصلی و action خطرناک کاملاً تفکیک شوند؛
- ساخت/ویرایش روی موبایل keyboard و sticky action مناسب داشته باشد؛
- نتیجه copy، cancel، terminate و unlink کنار همان action دیده شود؛
- deep link در loading فضای خالی نسازد؛
- منطق، permission و API فعلی حفظ شوند.

گیت پایان: پنج task اصلی هر workspace روی موبایل و یک سناریوی کامل روی دسکتاپ با داده پرتراکم تأیید شود؛ داده پرتراکم برای آزمون تحمل است و اطلاعات ثانویه همچنان فقط هنگام نیاز آشکار می‌شود.

### مرحله ۶ — مدیریت و پروفایل

مدت پیشنهادی: ۸ تا ۱۲ روز

صفحه‌ها:

- Admin landing؛
- invitation management؛
- user list/detail؛
- commodity management؛
- پوسته و اعلان‌های عمومی غیر بازار در Admin Messages؛
- بخش‌های غیر بازار system settings؛
- Profile self؛
- Public Profile.

تغییر تجربه:

- عملیات و پنل مدیریت مقصدهای تکراری نداشته باشند؛
- landing مدیریت تعداد ابزارها یا روابط را به‌عنوان KPI تزئینی نمایش ندهد و فقط جست‌وجو، صف نیازمند رسیدگی و اقدام واقعی را برجسته کند؛
- admin route منبع واحد state و deep link باشد؛
- جست‌وجو و ردیف کاربران keyboard-accessible باشد؛
- browser alert/confirm با dialog مشترک و توضیح پیامد جایگزین شود؛
- feedback مدیریت کالا بعد از refresh باقی بماند؛
- پروفایل از یک فایل چندمنظوره به بخش‌های هویتی، روابط، تاریخچه و actionها تفکیک شود؛
- action ورود به پیام‌رسان فقط behavior موجود را حفظ کند و وارد redesign پیام‌رسان نشود؛
- بخش‌های market/channel/admin message محافظت‌شده باقی بمانند.

گیت پایان: ادمین از جست‌وجو تا action نهایی، و کاربر از مشاهده هویت تا مدیریت پروفایل، مسیر قابل پیش‌بینی و یکپارچه داشته باشند؛ metadata مدیریتی فقط در context تصمیم و نه به‌دلیل نقش ادمین دیده شود.

### مرحله ۷ — پویایی، دسترس‌پذیری و polish سراسری

مدت پیشنهادی: ۴ تا ۶ روز

خروجی‌ها:

- motion سه‌سطحی: micro، component و page/state؛
- reduced-motion کامل؛
- focus management و live region؛
- Tabs، Filter Chips و Date Picker قابل استفاده با keyboard؛
- انتخاب متن و copy در اطلاعات مجاز؛
- کنتراست WCAG 2.2 AA؛
- zoom تا ۲۰۰٪؛
- متن‌های بلند فارسی، اعداد، تاریخ، نام‌های طولانی و داده پرتراکم؛
- حذف CSS محلی و adapterهای منقضی‌شده؛
- microcopy و آیکون‌های یکدست.

گیت پایان: پویایی به فهم state کمک کند و هیچ قابلیت به animation وابسته نباشد.

### مرحله ۸ — پذیرش نهایی و عرضه مرحله‌ای

مدت پیشنهادی: ۵ تا ۷ روز

ماتریس پذیرش:

- نقش: مهمان، عضو، مشتری، حسابدار، سرگروه، مدیر میانی و مدیر ارشد؛
- مسیر: تمام routeهای داخل محدوده؛
- viewport موبایل: ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰؛
- viewport تطبیقی: ۷۶۸، ۱۰۲۴ و ۱۴۴۰؛
- وضعیت داده: loading، empty، normal، dense، error، slow، offline و stale؛
- تعامل: touch، keyboard، screen reader smoke، zoom و reduced-motion؛
- محیط: مرورگر موبایل، PWA و Telegram WebView در حد سطح‌های غیرپیام‌رسان؛
- visual regression اجباری برای routeهای تغییرکرده؛
- visual freeze برای بازار و پیام‌رسان؛
- تست task-based انسانی با سناریوهای مرجع، نه فقط screenshot empty state.

مدل عرضه:

1. فعال‌سازی محدود برای تیم/نقش‌های آزمایشی؛
2. مشاهده خطا، بازخورد و رفتار چند روزه؛
3. گسترش مرحله‌ای؛
4. حذف adapterهای قدیمی فقط پس از اطمینان از rollback و نبود وابستگی.

گیت پایان: بازبینی انسانی بر اساس استفاده واقعی و مقایسه تصویری همراه با گیت‌های فنی؛ green بودن تست‌ها به‌تنهایی به معنی پذیرش زیبایی نیست. مطابق اجازه پیوسته مالک، این Stage منتظر تأیید جداگانه per-stage نمی‌ماند مگر مالک صریحاً توقف یا اصلاح بخواهد.

## ۹. ترتیب پوشش همه سطح‌ها

| موج | سطح‌ها | دلیل ترتیب |
| --- | --- | --- |
| ۰ | prototype نماینده | تعیین سلیقه پیش از هزینه کدنویسی |
| ۱ | stateها و feedback | اعتماد و جلوگیری از خطا قبل از polish |
| ۲ | Design System V2 | حذف علت اصلی drift |
| ۳ | ورود، دعوت، ثبت‌نام، shell | اولین تماس کاربر و الگوی فرم موبایل |
| ۴ | خانه، عملیات، حساب، تنظیمات، اعلان | ۹۵٪ مسیرهای پرتکرار موبایل |
| ۵ | مشتریان و حسابداران | workflowهای سنگین و حساس |
| ۶ | ادمین و پروفایل | context و permissionهای متنوع |
| ۷ | motion، a11y و پاکسازی | یکدست‌کردن نهایی پس از مهاجرت |
| ۸ | QA و rollout | اثبات کیفیت و عدم نشت به بخش‌های ممنوع |

## ۱۰. معیارهای موفقیت

### ادراکی

- کاربر در جابه‌جایی بین صفحه‌ها حس ورود به محصول دیگری نداشته باشد؛
- hierarchy بدون اتکا به رنگ زیاد قابل فهم باشد؛
- صفحه‌ها premium و آرام باشند، نه شلوغ یا تزئینی؛
- action اصلی در کمتر از چند ثانیه قابل تشخیص باشد.
- هدف صفحه، وضعیت لازم و اقدام بعدی در آزمون پنج‌ثانیه‌ای قابل تشخیص باشند؛
- فضای خالی به‌عنوان بخشی از hierarchy حفظ شود و با کارت یا KPI بی‌اثر پر نشود.

### کارکردی

- هیچ loading بی‌نهایت، empty دروغین یا شکست خاموش باقی نماند؛
- تمام actionهای حساس busy، confirm و feedback داشته باشند؛
- back و deep link قابل پیش‌بینی باشند؛
- فرم موبایل keyboard و validation درست داشته باشد.

### یکپارچگی

- فقط یک خانواده component مرجع برای صفحه‌های V2 وجود داشته باشد؛
- CSS feature فقط layout خاص همان feature را نگه دارد؛
- typography، color، radius، shadow، icon و motion از قرارداد مشترک بیایند؛
- هیچ تغییر بصری ناخواسته در بازار و پیام‌رسان رخ ندهد.

### کیفیت

- WCAG 2.2 AA برای کنتراست و تعامل‌های اصلی؛
- بدون horizontal overflow یا پوشانده‌شدن CTA؛
- عملکرد درست روی عرض‌های موبایل مرجع؛
- دسکتاپ برای taskهای پیچیده از فضا استفاده کند، بدون ساخت محصولی جدا؛
- تست با داده واقعی، خطا، شبکه کند و محتوای طولانی انجام شود.
- صددرصد واحدهای همیشه‌نمایان inventory ضرورت محتوا داشته باشند و تعداد واحدهای بدون توجیه صفر باشد؛
- شمارنده بدون اثر مستند بر تصمیم، محدودیت، انتخاب، پیشرفت یا اقدام وجود نداشته باشد؛
- نام route/path، منبع backend و متن داخلی سیستم در UI محصول نمایش داده نشود؛
- یک واقعیت در یک state و viewport فقط یک خانه بصری اصلی داشته باشد؛
- حریم خصوصی، امنیت، اعتبارسنجی و recovery به نام خلوت‌سازی حذف نشوند و نزدیک context مرتبط باقی بمانند.

## ۱۱. ریسک‌ها و کنترل‌ها

| ریسک | کنترل |
| --- | --- |
| تکرار اصلاح سلیقه‌ای | تأیید Figma و prototype قبل از کد |
| نشت تغییر به بازار/پیام‌رسان | scope V2 و visual freeze |
| big-bang rewrite | مهاجرت موجی و rollback در هر مرحله |
| سبز بودن تست اما نارضایتی ظاهری | گیت انسانی و task-based review |
| تبدیل «مالی مدرن» به صفحه پرتراکم | inventory ضرورت محتوا، آزمون پنج‌ثانیه‌ای و حذف شمارنده/metadata بی‌اقدام |
| باقی‌ماندن نسل قدیمی | برنامه صریح حذف adapter و CSS محلی |
| regression permission/business | حفظ backend authority و تست نقش‌محور |
| کم‌توجهی به موبایل واقعی | اولویت ۳۶۰ تا ۴۳۰، PWA و WebView |
| هزینه بیش از حد برای ۵٪ دسکتاپ | adaptive enhancement فقط برای taskهای پیچیده |

## ۱۲. برآورد کلان

با فرض یک جریان اجرایی پیوسته و review منظم، بازه واقع‌بینانه برای این roadmap حدود ۷ تا ۱۰ هفته است. این عدد شامل بازطراحی بازار و پیام‌رسان نیست و با تعداد iterationهای مرحله صفر و بازخورد مالک محصول تغییر می‌کند.

اولویت زمانی بر اساس سهم استفاده:

- بیشترین سرمایه‌گذاری: تجربه موبایل، stateها، فرم‌ها و مسیرهای پرتکرار؛
- سرمایه‌گذاری هدفمند: desktop adaptive برای workspaceهای پیچیده؛
- بدون سرمایه‌گذاری در این roadmap: redesign بازار و پیام‌رسان.

## ۱۳. شواهد کلیدی ممیزی

این ممیزی با inventory تمام routeهای مجاز، بررسی کد و componentها، مرور roadmapها و handoffهای قبلی و مشاهده baselineهای تصویری نسخه‌شده برای موبایل و دسکتاپ انجام شد. اجرای دوباره Playwright آغاز شد، اما محیط فعلی binary مرورگر Chromium را ندارد؛ بنابراین اجرا پیش از بازشدن صفحه متوقف شد. این مورد شکست برنامه نیست، ولی اجرای تازه ماتریس مرورگر در مرحله پذیرش الزامی است.

- تعریف routeها و aliasهای تکراری: [`router/index.ts`](../frontend/src/router/index.ts#L7)
- نمایش پوسته احراز‌شده روی همه مسیرها به‌جز Login: [`App.vue`](../frontend/src/App.vue#L19)
- سه قرارداد صفحه در عمل: [`DashboardView.vue`](../frontend/src/views/DashboardView.vue#L564)، [`OperationsView.vue`](../frontend/src/views/OperationsView.vue#L161)، [`AccountHubView.vue`](../frontend/src/views/AccountHubView.vue#L133)
- شروع نسل `ds-workspace-*`: [`main.css`](../frontend/src/assets/main.css#L404)
- شروع نسل جدیدتر `ui-*`: [`main.css`](../frontend/src/assets/main.css#L824)
- typography و tokenهای فعلی: [`main.css`](../frontend/src/assets/main.css#L136)
- غیرفعال‌بودن انتخاب متن روی کل بدنه: [`main.css`](../frontend/src/assets/main.css#L212)
- نوار ناوبری و اندازه متن بسیار کوچک: [`BottomNav.vue`](../frontend/src/components/BottomNav.vue#L477)
- وضعیت خطای ناکامل داشبورد: [`DashboardView.vue`](../frontend/src/views/DashboardView.vue#L507)
- loading ماندگار پروفایل در خطا: [`ProfileView.vue`](../frontend/src/views/ProfileView.vue#L43)
- empty شدن اعلان بعد از خطای شبکه: [`notifications.ts`](../frontend/src/stores/notifications.ts#L307)
- state فرضی مرکز حساب پیش از دریافت هویت: [`AccountHubView.vue`](../frontend/src/views/AccountHubView.vue#L30)
- feedback جابه‌جا در workspace مشتری: [`CustomerWorkspaceView.vue`](../frontend/src/views/CustomerWorkspaceView.vue#L445)
- ردیف کاربر ادمین بدون قرارداد کامل keyboard: [`UserManager.vue`](../frontend/src/components/UserManager.vue#L116)
- استفاده از confirm/alert بومی در مدیریت کاربر: [`UserProfile.vue`](../frontend/src/components/UserProfile.vue#L350)
- baseline تصویری فقط در دو viewport و به‌صورت opt-in: [`non-messenger-visual-baseline.spec.ts`](../frontend/e2e/non-messenger-visual-baseline.spec.ts#L3)
- تست viewport عمدتاً overflow و bottom chrome را می‌سنجد: [`non-messenger-viewport.spec.ts`](../frontend/e2e/non-messenger-viewport.spec.ts#L190)
- بدهی ثبت‌شده پس از roadmapهای پیشین: [`NON_MESSENGER_PROFESSIONAL_APP_UX_HANDOFF.md`](NON_MESSENGER_PROFESSIONAL_APP_UX_HANDOFF.md#L216)

## ۱۴. روش اجرای Git

- roadmap در branch مستقل `condidate/webapp-ui-ux-redesign-v2` ثبت می‌شود؛
- این branch مستقیم از `main` ساخته شده است؛
- قبل از هر commit، branch جاری و scope diff بررسی شود؛
- هر مرحله commitهای مستقل و rollbackپذیر داشته باشد؛
- بازار و پیام‌رسان در diff طراحی این branch وارد نشوند؛
- rollout production فقط با دستور صریح مالک پروژه انجام شود.

## ۱۵. پیشنهاد شروع

اولین اقدام اجرایی کدنویسی نیست. پیشنهاد شروع:

1. تثبیت دامنه و surfaceهای محافظت‌شده؛
2. ساخت سه جهت بصری در Figma؛
3. طراحی high-fidelity صفحه ورود، خانه، عملیات، workspace مشتری و کاربران ادمین در موبایل؛
4. طراحی یک variant دسکتاپ برای workspace مشتری؛
5. دریافت اصلاحات و تأیید مالک محصول؛
6. قفل‌کردن design contract؛
7. ورود به مرحله اعتماد و تداوم کار.

این گیت مهم‌ترین تفاوت roadmap جدید با موج‌های قبلی است: قبل از اینکه کد تعیین کند محصول چه شکلی باشد، تصویر نهایی و تجربه مورد انتظار تعیین و تأیید می‌شود.
