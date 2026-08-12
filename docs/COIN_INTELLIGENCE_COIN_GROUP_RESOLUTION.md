# P2-C-B3 — اعتبارسنجی علّی قیمت و کالا در گروه‌های سکه

## تصمیم

نام کالا در متن گروه، label قطعی نیست. `coin_group_resolution.py` هر آفر را
فقط با anchorهای `ELIGIBLE` که در **همان settlement و trade form** و به‌طور
strictly-prior در دسترس بوده‌اند ارزیابی می‌کند. حداقل دو anchor لازم است؛
anchor آینده، دیررس، conditional، کیفیت‌نداشته یا کتاب متفاوت نادیده گرفته
می‌شود.

کالای بی‌نام فقط با winner فاصله‌دار و نزدیک resolve می‌شود. نام explicit
اگر winner با آن متفاوت باشد `REJECTED` است، نه اینکه پنهانی به کالای دیگر
تبدیل شود. نبودِ anchor کافی یا رقابت نزدیک نیز `PENDING_REVIEW` می‌ماند.
بنابراین هیچ قیمت صریح یا ضمنی صرفاً به اتکای بازهٔ ثابت وارد مدل نمی‌شود.

## causality و projection

زمان رخداد observation همان زمان پیام اصلی است. در reconciliation دیرتر،
caller باید `resolution_available_at_utc` را زمان واقعیِ تکمیل resolver دهد؛
پس snapshot تاریخی قبل از آن، نتیجهٔ بعدی را نمی‌بیند. projection فقط code،
price، quantity، side، form، settlement و evidence عددی محدود دارد؛ متن،
ID، reply و هویت هرگز وارد Market Store نمی‌شوند.

## مرز مرحله

این resolver anchorها را خودکار از Store یا model جمع نمی‌کند و Store هم
نمی‌نویسد؛ provider بعدی باید فقط factهای واقعاً `ELIGIBLE` و unit-compatible
را به `CoinPriceAnchor` تبدیل کند. این تفکیک مانع bootstrap خود-تأییدکننده
و leakage می‌شود. trade linking و promotion transaction در P2-C-B4 باقی
مانده‌اند.
