# Gate receipt مرحله ۱ شبکه خصوصی بازار

تاریخ اجرا: 2026-08-26

وضعیت gate: **PASS؛ بدون endpoint دائمی، deployment یا cutover**

ابزار بازتولیدپذیر: `scripts/audit_coin_market_private_network_stage1.py`

## 1. محدوده و ایمنی

این مرحله فقط transport مصنوعی و فاقد داده محصول را روی Network از قبل ساخته‌شده آزمایش کرد. هیچ raw market event، شناسه Telegram، credential، session، payload مدل، جدول محصول یا sync عمومی خوانده یا تغییر داده نشد.

دو listener موقت روی port آزمایشی `18443` و فقط روی IP خصوصی هر میزبان اجرا شدند. هر listener علاوه بر bind خصوصی، IP خصوصی peer را allowlist و تمام sourceهای دیگر را در application رد می‌کرد. ruleهای موقت nft نیز فقط همان interface، destination و port را پوشش می‌دادند.

کلیدهای HMAC، CA و private keyهای TLS فقط در دایرکتوری root-only زیر `/run` ساخته شدند، هرگز وارد Git، `/tmp`، log یا artifact نشدند و در پایان از هر دو میزبان حذف شدند.

## 2. قرارداد امنیتی آزمایش‌شده

- TLS حداقل 1.2؛ اجرای زنده در هر دو جهت با TLS 1.3 و cipher برابر `TLS_AES_256_GCM_SHA384`؛
- CA دارای `basicConstraints=CA:TRUE` و `keyUsage=keyCertSign,cRLSign`؛
- leaf certificate با `CA:FALSE`، `serverAuth` و IP SAN دقیق endpoint خصوصی؛
- HMAC-SHA256 روی method، path، key ID، timestamp، nonce و SHA-256 body؛
- پنجره clock-skew برابر 30 ثانیه و nonce replay window برابر 120 ثانیه؛
- key ring دوکلیدی برای overlap rotation؛ key بازنشسته‌شده خارج allowlist؛
- حداکثر body برابر 1 MiB و خروجی/log فاقد body یا key material؛
- health endpoint و data probe هر دو فقط برای peer خصوصی مجاز بودند.

این قرارداد متعلق به harness مرحله ۱ است. schema نهایی batch/ACK و انتخاب دقیق headerها در مرحله ۲ version می‌شود؛ نتیجه این مرحله الزام‌های امنیتی را ثابت می‌کند و مجوز کپی مستقیم harness به runtime نیست.

## 3. نتیجه دوطرفه

هر جهت 200 درخواست متوالی با payload مصنوعی 64 KiB را پس از تمام کنترل‌های منفی اجرا کرد.

| مسیر | throughput | median | p95 | max | نتیجه |
| --- | ---: | ---: | ---: | ---: | --- |
| بات → وب/داده | 29.117 MiB/s | 1.663 ms | 2.167 ms | 3.145 ms | PASS |
| وب/داده → بات | 25.297 MiB/s | 2.205 ms | 3.446 ms | 4.822 ms | PASS |

آستانه harness حداقل 2 MiB/s و p95 حداکثر 100 ms بود. پس از rotation گواهی و restart نیز مجموعه کامل با 50 درخواست در هر جهت دوباره پاس شد؛ throughput دو جهت 28.144 و 27.432 MiB/s و p95 آن‌ها 2.191 و 3.489 ms بود.

## 4. کنترل‌های منفی و failure drill

| آزمون | انتظار | نتیجه |
| --- | --- | --- |
| HMAC نادرست | `401` بدون side effect | PASS |
| timestamp قدیمی و آینده | `401 clock_skew` | PASS |
| nonce تکراری | `409 replay_detected` | PASS |
| key بعدی در rotation | پذیرفته شود | PASS |
| key بازنشسته/ناشناخته | `401 unknown_key` | PASS |
| قطع connection و اتصال مجدد | failure قابل تشخیص و reconnect موفق | PASS |
| packet-loss واقعی روی SYNهای port خصوصی | ترکیب success/failure و recovery پس از حذف rule | PASS؛ 7 failure از 40 probe |
| route blackhole دوطرفه | fail-closed و recovery پس از rollback | PASS |
| firewall drop دوطرفه | timeout/failure و recovery پس از rollback | PASS |
| restart و rotation گواهی | اعتماد CA و HMAC بعد از restart حفظ شود | PASS |
| اتصال به همان port روی IP عمومی | بسته باشد | PASS در هر دو جهت |
| NTP | هر دو میزبان synchronized | PASS |

packet-loss، route و firewall فقط روی مقصد/port آزمایشی خصوصی اعمال شدند. SSH عمومی و workloadهای موجود در محدوده drill نبودند.

## 5. policy اثبات‌شده firewall و bind

policy آزموده‌شده برای receiver آینده:

1. process فقط روی IP خصوصی و port صریح bind شود؛
2. روی interface خصوصی فقط source IP طرف مقابل برای همان destination/port پذیرفته شود؛
3. باقی sourceها برای همان port drop شوند؛
4. peer allowlist داخل application نیز مستقل از firewall باقی بماند؛
5. port روی IP عمومی listener نداشته باشد؛
6. health و data endpoint از نظر مسیر و authentication تفکیک شوند، ولی هیچ‌کدام public نباشند.

rule دائمی تا ساخته‌شدن receiver واقعی نصب نشد تا port بدون owner و policy بدون service وارد runtime نشود. Compose/deploy مرحله ۳ باید همین policy را با port نهایی و inventory قابل ممیزی اعمال کند.

## 6. rollback receipt

پس از پایان آزمون:

- هر دو transient systemd unit متوقف و جمع شدند؛
- هیچ listener روی port آزمایشی در هیچ میزبان باقی نماند؛
- تمام tableهای nft آزمایشی حذف شدند؛
- تمام CA، certificate، private key و HMAC keyهای موقت از `/run` هر دو میزبان حذف شدند؛
- routeهای blackhole باقی نماندند؛
- ping خصوصی دوطرفه پس از rollback موفق ماند؛
- Network provider و delete-protection آن بدون تغییر باقی ماند؛
- هیچ مسیر product sync، service بازار یا public fallback تغییر نکرد.

## 7. Gate و قدم بعد

- ارتباط خصوصی پایدار: **PASS**؛
- public exposure صفر: **PASS**؛
- TLS/HMAC، replay، skew و rotation: **PASS**؛
- throughput، reconnect و failure recovery: **PASS**؛
- firewall policy و rollback rehearsal: **PASS**؛
- عدم تغییر sync عمومی و عدم cutover: **PASS**.

مرحله ۱ بسته شد. قدم بعد، مرحله ۲ یعنی version کردن contractهای capture/fact/batch/snapshot، source registry، schema ذخیره‌سازی و ADR پایگاه داده است.
