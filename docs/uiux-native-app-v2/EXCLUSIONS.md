# مرزهای Native App Feel V2

این track زبان بصری و حس اپ بومی را روی کل وب‌اپ جلو می‌برد، اما قراردادهای زیر را بازنویسی نمی‌کند.

## خارج از کار

- مسیر `/market` و تمام فید، کارت معامله، meter، hourglass، two-tap و overtime فید
- `home-market-widget`
- `admin-messages-market-delivery`
- `trading-settings-market-controls`
- تغییر backend، دیتابیس، authorization یا منطق کسب‌وکار
- تغییر rollout پیش‌فرض پیام‌رسان یا حذف مسیر legacy
- تشخیص آلبوم جز با `album_id` + `album_index`
- تغییر schema / websocket / upload / cache فقط برای restyle
- احیای تلگرام Mini App
- ورود developer به‌عنوان محصول کاربر
- overtime preference بیرون از حساب/تنظیمات
- merge، rebase مخرب، push، deploy مگر با دستور جدا
- اعلام owner-approved یا production-ready

## داخل کار

- ۲۹ مسیر غیر بازار و همهٔ زیرسطح زنده‌شان
- پوسته، ناوبری، overlay، خانهٔ غیر بازار، حساب، پروفایل، عملیات، مدیریت، احراز
- پیام‌رسان کامل از نظر ظاهر (`M01` تا `M14`)
- `admin-messages-messenger-delivery`
- بخش غیر بازار `/admin/system` و پوستهٔ تقویم جلالی
- متن‌های اضافه، حاشیه، overflow دکمه، کارت‌های چسبیده به لبه

## مرجع رفتار که بازنویسی نمی‌شود

- جدول معاملات امروز خانه تک‌سطری می‌ماند؛ فقط پوسته و حاشیه بومی می‌شود
- confirm تقویم TradingSettings:
  `if (!confirm('آیا از حذف این استثنای تقویمی مطمئن هستید؟'))`
- `account_status` مرجع دسترسی است
- مسیر rollback پیام‌رسان سالم می‌ماند
