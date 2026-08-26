# حس اپ بومی وب‌اپ — Native App Feel V2

این track مستقل از Stage 8، UIUX V3 و Native Controls V1 است.
رسیدهای تاریخی را overwrite نمی‌کند. کار روی همین شاخه اجرا می‌شود.

## هویت برنامه

- نام: Native App Feel V2
- شاخه: `candidate/webapp-native-app-v2`
- مبنای کد: `main` در `951ca9f0`
- مرجع زبان: `LANGUAGE.md`
- مرجع ممیزی: `AUDIT.md`
- موجودی: `SURFACE_INVENTORY.json`
- مرز: `EXCLUSIONS.md`
- Figma تاریخی: fileKey `z8jgJxST4O2APzWnlyP9gv` دست‌نخورده می‌ماند
- Sites، staging، production، merge به `main` و push خارج از این سند است

## چرا track جدید لازم است

دو ریفکتور قبلی یکپارچگی کنترل و بخشی از فهرست را بهتر کردند.
نمرهٔ مالک در شروع این شاخه:

| هدف | نمرهٔ مالک | واقعیت ممیزی |
|---|---|---|
| یکپارچگی ظاهر | ۸۰ | دو سامانهٔ توکن و چند الگوی ردیف هنوز موازی‌اند |
| ساده‌سازی متن و بخش | ۷۰ | توضیح تکراری در پوسته، بخش، ردیف و dialog |
| حس اپ iOS/Android | زیر ۵۰ | کارت وب، لبهٔ چسبیده، overflow دکمه، داشبورد پروفایل/خانه |

V1 هندسهٔ ۴۸ پیکسل را بست و خیلی از مسیرها را «aligned-native» نوشت.
آن برچسب برای حس اپ کافی نبود. این برنامه همان مسیرها را دوباره، ریزبینانه و با زبان Settings/Messages جلو می‌برد.

هدف پایانی: حس بومی به نمرهٔ قبولی برسد (حداقل ۷۵). یکپارچگی به ۹۰ و ساده‌سازی به ۸۵ نزدیک شود.

## هدف

کل وب‌اپ به‌جز Market مثل یک اپ نصب‌شدهٔ iOS/Android حس شود:

- فارسی و RTL با Vazirmatn
- پس‌زمینهٔ grouped، فهرست inset، عنوان بزرگ، هدف لمسی ۴۸
- کارت و دکمه به گوشه نمی‌چسبند؛ متن دکمه از کادر بیرون نمی‌زند
- متن فقط برای اقدام لازم است
- پیام‌رسان immersive است
- Market کاملاً خارج است
- WCAG 2.2 AA

بین فازها توقف یا تأیید مالک لازم نیست. بازبینی مالک فقط در پایان و در staging است.

## مرز قطعی

جزئیات در `EXCLUSIONS.md` است.

خارج: `/market`، ویجت بازار خانه، interiors بازار در پیام مدیریت و تنظیمات سیستم، backend، آلبوم بدون metadata، Mini App، push/merge/deploy.

داخل: ۲۹ مسیر غیر بازار، پوسته، overlay، عملیات مشتری/حسابدار، پروفایل، مدیریت غیر بازار، احراز، پیام‌رسان ظاهر.

## مالکیت مسیرها

`/market` در جدول اجرا نیست.

| مسیر | فاز | یادداشت |
|---|---|---|
| پوسته، BottomNav، overlay مشترک | ۲ و ۱۰ | FAB ناوبری روی چت حذف می‌شود |
| `/` | ۳ | فقط غیر بازار؛ ویجت بازار قفل |
| `/account` و زیرمسیرها | ۴ | overtime اینجا می‌ماند |
| `/settings` | ۴ | |
| `/notifications` | ۴ | alias حساب |
| `/profile` `/users/:id` | ۵ | |
| `/operations` | ۶ | |
| `/operations/customers` و جزئیات | ۶ | اولویت بالا |
| `/operations/accountants` و جزئیات | ۶ | اولویت بالا |
| `/admin` | ۷ | |
| `/admin/invitations` | ۷ | |
| `/admin/users` | ۷ | |
| `/admin/users/:id` | ۵ و ۷ | |
| `/admin/commodities` | ۷ | |
| `/admin/messages` | ۷ و ۹ | پیام‌رسان داخل؛ تحویل بازار قفل |
| `/admin/system` | ۷ | کنترل بازار قفل؛ confirm تقویم قفل |
| `/login` `/register` `/i/:code` `/setup-password` | ۸ | OTP عوض نمی‌شود |
| `/:pathMatch(.*)*` | ۸ | fill سراسری نمی‌گیرد |
| `/chat` | ۹ | کل `M01`–`M14` ظاهر |
| `/admin/channels` | ۹ | |
| `/share-receive` | ۹ | |

## فازها

هر فاز پس از gate واقعی commit مستقل می‌گیرد.

### فاز ۰ — قرارداد و ممیزی

وضعیت: complete در همین شاخه.

کار انجام‌شده:

- شاخه از `main`
- ممیزی ۲۹ مسیر غیر بازار
- زبان بومی، موجودی و مرز

Gate:

- ۲۹ مسیر owner دارند
- `/market` excluded است نه unknown
- `git diff --check` روی اسناد سبز

### فاز ۱ — foundation بومی واقعی

کار:

- `AppInsetGroup`: گروه سفید، شعاع ۱۰–۱۲، حاشیه ۱۶، overflow hidden
- `AppListItem` حالت grouped پیش‌فرض: بدون border جدا، بدون hover lift، hairline، حداقل ۴۸
- `AppButton`: متن می‌شکند؛ `min-width: 0`؛ در ردیف شلوغ به منو می‌رود
- `--ds-page-padding` همیشه ۱۶
- بودجهٔ متن در primitive: description اختیاری و پیش‌فرض خالی
- الگوی overflow action برای ۳+ دکمه
- صفحهٔ Figma جدید «Native App V2 · Language»؛ صفحات تاریخی دست نمی‌خورند

Gate:

- `npm run guard:ui` سبز
- تست primitiveهای فهرست و دکمه: متن بلند فارسی داخل کادر؛ گروه به لبه نمی‌چسبد
- هیچ `--ui-v2-*` جدید در CSS محصول

### فاز ۲ — پوسته و ناوبری

کار:

- عنوان بزرگ دیده می‌شود
- BottomNav: برچسب خوانا، هدف ۴۸، بدون کلاس محصول جدید `ui-v2-*`
- پیام‌رسان بدون FAB ناوبری سراسری
- منوی هویت خانه به‌صورت sheet
- toast / banner / dialog / sheet یک خانواده
- خانه بازار را بازطراحی نکن

Gate:

- document overflow صفر روی ۲۹ مسیر در ۳۹۰ و ۱۴۴۰
- CTA پوشیده صفر
- `/chat` FAB همبرگری ندارد

### فاز ۳ — خانه غیر بازار

کار:

- هدر خانه از `ui-v2-home-*` به `--ds-*`
- افشا ۴۸ پیکسل
- معاملات امروز تک‌سطری؛ حذف حس «جدول وب» از پوسته و راهنمای اسکرول
- اعلان و وضعیت حساب به‌صورت notice تک‌خط، نه کارت قهرمان
- ویجت بازار دست‌نخورده

Gate:

- هیچ اختلاف پیکسلی Market و `home-market-widget`
- empty/error/loading/offline خانه پاس

### فاز ۴ — حساب، تنظیمات، اعلان

کار:

- عنوان «حساب» بزرگ و مرئی
- هر گروه فقط عنوان کوتاه
- ردیف بدون توضیح تکراری
- تلگرام یک ردیف عملی
- اعلان بدون نوار رنگی وب
- overtime با توکن `--ds-*`
- storage و نشست یک بار توضیح داده می‌شوند

Gate:

- نقش‌های مجاز حساب/تنظیمات
- privacy نشست بدون تغییر
- overflow دکمه صفر

### فاز ۵ — پروفایل و هویت

کار:

- حذف کارت بیرونی `PublicProfile` / `UserProfile`
- هویت بالا + گروه‌های inset
- حذف `HelpPopover` از پروفایل زنده
- آمار و معامله به‌صورت ردیف، نه کارت کوچک
- هدر تکراری حذف می‌شود
- مجوزها ادغام نمی‌شوند

Gate:

- جابه‌جایی `/profile` ↔ `/users/:id` دوباره mount نمی‌شود
- regression دسترسی صفر
- زوم ۲۰۰٪ overflow ندارد

### فاز ۶ — عملیات مشتری و حسابدار

اولویت مالک.

کار:

- فهرست و پرونده با `AppInsetGroup`
- دعوت باز: یک اقدام اصلی «بررسی» + منوی کپی/لغو
- نشست: نشان در trailing؛ پایان نشست در ردیف یا sheet جدا
- حذف description پوسته و بخش
- محدودیت: مقدار در ردیف؛ یک پاورقی برای «خالی یعنی بدون سقف»
- دورهٔ آمار با `AppFilterChips`
- مرور مالی به فهرست فشرده یا sheet
- `Owner*ManagerModal` اگر ورودی زنده ندارد از مسیر محصول خارج یا به همان workspace redirect شود؛ `window.confirm` و select خام نماند
- `expected_action` روی DELETE قفل می‌ماند

Gate:

- چهار مسیر عملیات و زیرstateها
- هیچ ردیف با بیش از یک دکمهٔ اصلی هم‌عرض
- cancel/Escape mutation ندارد
- حاشیهٔ گروه ۱۶ پیکسل در ۳۹۰

### فاز ۷ — مدیریت

کار:

- عنوان بزرگ زیرصفحه
- `UserManager` گروه inset، نه کارت با gap
- دعوت و کالا همان فهرست و فرم بومی
- پیام مدیریت غیر بازار: یک فهرست + composer sheet؛ حذف `card-with-help`
- پوستهٔ TradingSettings و Jalali ۴۸ پیکسل؛ confirm تقویم و interiors بازار دست نمی‌خورند
- CreateChannel فقط هماهنگی ظاهر اولیه؛ عملکرد در فاز ۹

Gate:

- کنترل غیرمجاز صفر
- destructive با dialog مشترک
- تست‌های مدیریت سبز
- صفر HelpPopover روی پیام مدیریت زنده

### فاز ۸ — احراز و مسیرهای عمومی

کار:

- Login، Register، Invite، SetupPassword، SystemRecovery
- خروج از زبان `ui-v2-auth-*` به همان صفحهٔ grouped و `--ds-*`
- یک خط راهنمای OTP؛ حذف پاراگراف‌های تکراری privacy/delivery مگر الزام حقوقی یک خط
- developer login محصول کاربر نمی‌شود
- OTP و recovery از نظر عملکرد عوض نمی‌شوند

Gate:

- focus و keyboard کامل
- outcome مسیرها صحیح
- overflow صفر

### فاز ۹ — پیام‌رسان ظاهر

رفتار قفل است. ظاهر آزاد است.

کار:

- immersive: بدون FAB ناوبری
- safe-area بالا روی `ChatView`؛ پایین روی composer و فوتر modal
- فهرست گفتگو full-bleed
- یک FAB گفتگوی جدید ۴۸ پیکسل
- loading shimmer ردیفی
- ShareReceive همان توکن messenger و `100dvh`
- CSS مردهٔ `ChatView` حذف یا به container منتقل می‌شود
- `native-app-messenger-visual-v1` برای گارد Stage 4 می‌ماند

Gate:

- `M01`–`M14` در موجودی `aligned` یا excluded-functional با دلیل
- default rollout همچنان legacy
- هیچ اختلاف Market
- album detection عوض نشده

### فاز ۱۰ — overlay و حالت‌ها

کار:

- `SessionApprovalModal` فقط `AppBottomSheet` و `--ds-*`
- `OvertimeApprovalModal` با `AppButton` و توکن
- `PWAInstallOverlay` به‌صورت sheet پایین
- مرور ۲۹ مسیر برای یک زبان
- حذف CSS تکراری واقعاً بدون مصرف

Gate:

- overlay نمونه keyboard و Escape پاس
- هیچ مسیر فقط با `data-ui-system=v2` کامل اعلام نشود

### فاز ۱۱ — پذیرش شاخه

کار:

- Vitest کامل، `vue-tsc`، build موقت، `guard:ui`
- ماتریس مرورگر غیر بازار + پیام‌رسان
- accessibility، keyboard، زوم، reduced-motion
- `memory-custodian check`
- merge-tree فقط خواندنی در برابر `origin/main`

حکم مجاز:

- `READY FOR INDEPENDENT NATIVE UI REVIEW`
- یا `BLOCKED` با مانع دقیق

حکم ممنوع: owner-approved، production-ready

## وضعیت فازها

| فاز | وضعیت |
|---|---|
| ۰ قرارداد و ممیزی | complete |
| ۱ foundation | implementation-complete |
| ۲ پوسته | implementation-complete |
| ۳ خانه | implementation-complete |
| ۴ حساب | implementation-complete |
| ۵ پروفایل | implementation-complete |
| ۶ عملیات | pending |
| ۷ مدیریت | pending |
| ۸ احراز | pending |
| ۹ پیام‌رسان | pending |
| ۱۰ overlay | pending |
| ۱۱ پذیرش | pending |

## ماتریس پذیرش

نقش: guest، watch، member، police، customer، accountant، owner-context، middle-admin، senior-admin — فقط نقش مجاز هر مسیر.

Viewport: ۳۶۰×۷۴۰، ۳۹۰×۸۴۴، ۴۳۰×۹۳۲، ۷۶۸×۱۰۲۴، ۱۴۴۰×۹۰۰.

حالت: loading، empty، normal، error، retry، offline، stale، long Persian، LTR عددی.

تعامل: touch، keyboard، Escape، back، soft keyboard، زوم ۲۰۰٪، reduced-motion.

چک بصری اجباری هر مسیر داخل برنامه:

1. گروه یا کارت به لبه نچسبیده مگر لیست immersive پیام‌رسان
2. متن دکمه داخل کادر است
3. عنوان صفحه دیده می‌شود
4. توضیح اضافه نیست
5. هدف لمسی ۴۸ است

`/market` در ماتریس این track نیست.

## قواعد commit

- هر فاز یک یا چند commit کوچک
- تست همراه منبع
- اسناد جدا
- بدون squash / merge / rebase مخرب
- بدون push مگر دستور بعدی

## حافظه

پس از تصمیم ماندگار، فقط `docs/memory/areas/frontend-uiux.md` را semantic merge کن.
