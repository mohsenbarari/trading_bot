# P2-C-B5 — اجرای محلی و صریح جریان گروه سکه

`process_coin_group_staging` یک function caller-driven است، نه worker. ترتیب
آن چنین است:

1. current rows غیرمنقضی staging را می‌خواند؛
2. فقط anchorهای `ELIGIBLE`, non-conditional, unit-compatible از Market Store
   (و anchor صریح اضافه) را می‌گیرد؛
3. آفرها را causal resolve و با `available_at` زمان واقعی اجرای resolver
   upsert می‌کند؛
4. فقط ریشه‌هایی با **یک** آفر eligible را برای reply trade linking می‌فرستد؛
5. offer و trade نهایی را با event key opaque upsert می‌کند.

این تابع transaction را commit نمی‌کند؛ caller یک transaction محلی برای
Market Store دارد. Replay همان staging باعث افزایش شمار facts نمی‌شود. بدون
anchor کافی، آفر `PENDING_REVIEW` می‌ماند و trade آن ساخته نمی‌شود. تبدیل
قیمت از بازار دیگر در این لایه ممنوع است؛ provider آینده باید تبدیل واحد را
خارج از آن به `CoinPriceAnchor` صریح انجام دهد.

Collector، scheduler، startup registration، endpoint و هر اتصال سه‌سروره در
این مرحله عمداً وجود ندارند.
