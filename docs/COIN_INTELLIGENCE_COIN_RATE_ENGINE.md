# P4-B-A — هستهٔ ساختاری بازهٔ قیمت سکه

این engine فقط از Market Store canonical و با cutoff `as_of` استفاده می‌کند.
برای تاریخ‌پایین، قیمت آبشدهٔ فیزیکال اولویت دارد و در نبود آن paper fallback
به‌صراحت برچسب می‌خورد. intrinsic بر حسب واحد پروژه چنین است:

```text
project_melted = IRT_PER_MESGHAL_750 / 10,000
full = project_melted × 2.253
half = full / 2
quarter = full / 4
one-gram = full / 8.130
```

برای دیگر سکه‌ها، آخرین anchor معتبر همان کالا و همان settlement با تغییر
آبشده transfer می‌شود. anchor قدیمی، form/settlement متفاوت، conditional یا
واحد متفاوت هرگز وارد نمی‌شود. امام نقدی می‌تواند در نبود anchor فقط از
IME Imam cash reference استفاده کند؛ سکهٔ غیرتاریخ‌پایین بدون anchor امن
`NO_DATA` می‌ماند، نه اینکه حباب ثابت اختراع شود.

بازه حداکثر ۲٪ است و بر اساس spread آبشده، سن anchor و رژیم paper کم‌وبیش
نامتقارن می‌شود. این ماژول pure است؛ snapshot publishing، P5 product
selection، زمان/تقویم ایران و مدل‌های learned در substageهای بعدی می‌آیند.
