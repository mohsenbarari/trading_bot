# P2-C-B5 — اجرای محلی و صریح جریان گروه سکه

`process_coin_group_staging` یک function caller-driven است، نه worker. ترتیب
آن چنین است:

1. current rows غیرمنقضی staging را می‌خواند؛
2. فقط anchorهای `ELIGIBLE`, non-conditional, unit-compatible از Market Store
   و rangeهای downsample‌شدهٔ `MAIN_ONLINE` را که پیش از همان پیام ساخته شده‌اند
   می‌گیرد؛
3. آفرها را causal resolve و با `available_at` زمان واقعی اجرای resolver
   upsert می‌کند؛
4. فقط ریشه‌هایی با **یک** آفر eligible را برای reply trade linking می‌فرستد؛
5. offer و trade نهایی را با event key opaque upsert می‌کند.

این تابع transaction را commit نمی‌کند؛ caller یک transaction محلی برای
Market Store دارد. Replay همان staging باعث افزایش شمار facts نمی‌شود. بدون
anchor/range کافی، آفر `PENDING_REVIEW` می‌ماند و trade آن ساخته نمی‌شود.
تبدیل full-Toman به project-thousand فقط یک‌بار در adapter فقط‌خواندنی prediction
انجام می‌شود؛ خود pipeline هیچ تبدیل واحد یا کالای پیش‌فرضی نمی‌سازد.

Collector این function را در transaction محلی صدا می‌زند؛ خود pipeline همچنان
worker، scheduler، endpoint یا اتصال شبکه ثبت نمی‌کند.
