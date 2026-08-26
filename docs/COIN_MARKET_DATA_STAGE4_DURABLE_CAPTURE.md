# Gate receipt مرحله 4 Capture پایدار و retention

تاریخ اجرا: 2026-08-26

وضعیت gate: **PASS برای implementation و fixture rehearsal؛ بدون deployment، login،
session copy، owner switch یا capture زنده**

مبنای اجرای نهایی: `main@2848b36560cfc8586dbd9759668d708625c16f2c`

## 1. مرز ایمنی

این مرحله دو capture role را داخل image مشترک pipeline پیاده کرد، ولی authority جاری
میزبان وب/داده را تغییر نداد. rehearsal با envelope مصنوعی، `--network none`، volume
موقت و بدون parser، Telegram credential/session، PostgreSQL محصول یا Market Store زنده
اجرا شد. serviceهای host-native `coin-capture` و `market-channel-capture` متوقف یا ویرایش
نشدند.

حالت `live` فقط برای `market-capture-account1` و `market-capture-account2` شناخته می‌شود.
قبل از بازشدن session، runtime تمام موارد زیر را fail-closed کنترل می‌کند:

- config secret با contract و allowlist دقیق همان حساب؛
- session file عادی، غیر symlink، mode `0600` و متعلق به UID runtime؛
- authority marker با mode `0600`، role و Git SHA دقیق همان release؛
- HMAC key حداقل 32 بایتی برای Account 2.

خود Stage 4 هیچ marker نساخت. ساخت آن جزئی از choreography cutover آینده و فقط بعد از
توقف owner میزبان مجاز است.

## 2. engine دوام مشترک

هر حساب state، session و spool جدا دارد. مسیر ثبت هر envelope:

1. validate schema/source/account و محدودیت 256 KiB؛
2. `BEGIN IMMEDIATE` و درج در SQLite outbox با `synchronous=FULL`؛
3. تخصیص sequence یکنواخت همان حساب؛
4. append کامل JSONL، سپس `fsync` فایل و در ایجاد فایل `fsync` دایرکتوری؛
5. ثبت seen/message/metric و حذف outbox فقط بعد از append پایدار.

اگر crash قبل از append رخ دهد، outbox در restart drain می‌شود. اگر crash بعد از append
و قبل از completion رخ دهد، startup event موجود را index می‌کند و همان outbox را بدون
درج دوباره complete می‌کند. outbox که از retention قدیمی‌تر شده باشد هرگز خودکار حذف
نمی‌شود و فقط alarm عملیاتی است.

partial tail با ثبت metadata شامل hash/offset و بدون raw repair می‌شود. خرابی وسط فایل،
event ID تکراری یا sequence regression capture را متوقف می‌کند. disk-full، short-write و
fsync failure هیچ ACK داخلی تولید نمی‌کنند.

## 3. قرارداد Telegram و حریم خصوصی

هیچ grammar یا فیلد اقتصادی در capture وجود ندارد. خروجی‌ها همان قراردادهای موجودند:

- Account 1: `market_channel_event/1.0` برای پنج source allowlist‌شده؛
- Account 2: `coin_group_event/2.0` برای `GROUP_1` و `GROUP_2`.

متن UTF-8 بدون normalize، Telegram UTF-16 entity offset، edit/delete، reply status، topic
metadata، publication/edit/receipt/availability و backfill حفظ می‌شوند. username، title،
access hash، phone و peer خام وارد spool یا health نمی‌شوند. sender Account 2 با HMAC
16-hex یک‌طرفه ذخیره می‌شود و anonymous admin با normalize کردن marked/bare chat ID
تشخیص داده می‌شود.

HMAC فعال capture فعلی باید هنگام cutover عیناً منتقل شود. ساخت کلید تازه در همان لحظه
branchهای reply نزدیک cutover را از نظر identity دوپاره می‌کند.

## 4. reconciliation و محدودیت ذاتی Telegram

- کانال‌های Account 1: پنجره 30 دقیقه و سقف 2,000 پیام/source؛
- گروه‌های Account 2: پنجره 6 ساعت و سقف 10,000 پیام/source؛
- reply ancestor گروه: حداکثر 2 ساعت، عمق حداکثر 20، والد قبل از فرزند؛
- ترتیب replay هر account با یک sequence مشترک و oldest-first حفظ می‌شود؛
- رسیدن به سقف، health را degraded می‌کند و سبز پنهان نمی‌ماند.

Telegram حذف‌های زمان آفلاین و revisionهای میانی edit را تضمین نمی‌کند. reconciliation
آخرین snapshot قابل مشاهده را برمی‌گرداند و هرگز زمان receipt جعلی از publication time
نمی‌سازد.

## 5. retention و observability

raw دقیقاً سه روز بر پایه `producer.available_at_utc` نگهداری می‌شود. فایل مخلوط با temp
file، fsync و replace اتمیک compact می‌شود؛ فایل کاملاً منقضی بعد از unlink با fsync
دایرکتوری حذف می‌شود. seen/message/quarantine state نیز با همان مرز پاک می‌شود، اما
outbox تحویل‌نشده حفظ می‌ماند.

هر purge یک audit row بدون متن، identity یا payload ثبت می‌کند. heartbeat هر source شامل
created، edited، deleted، duplicate، quarantined، gap recovered، last update age و آخرین
capture sequence است؛ در سطح account نیز outbox، global sequence و آخرین durable append
ثبت می‌شود. stdout/stderr هیچ raw envelope چاپ نمی‌کنند.

## 6. نتیجه rehearsal اختصاصی Stage 4

اجرای نهایی روی SHA بالا:

- image موقت: `sha256:f404495e0846b715aa3214b650f2423c411ea17bc0c1b2985d7b6d1ffebe598f`؛
- build: 22.142 ثانیه؛ Telethon: `1.44.0`؛
- Account 1: هفت event یکتا، پنج source، SQLite integrity=`ok`، outbox صفر؛
- Account 2: شش event یکتا، دو source، SQLite integrity=`ok`، outbox صفر؛
- duplicate deliveryهای ناشی از crash/restart شناسایی شدند و duplicate row ساخته نشد؛
- sequence gap هر دو حساب صفر؛
- crash بعد از stage و crash بعد از append هر دو با loss صفر بازیابی شدند؛
- owner دوم روی session mount مشترک با exit `78` fail-closed شد؛
- یک raw خارج از سه روز حذف و شش raw مرزی/تازه حفظ شدند؛ audit حاضر بود؛
- parser اجرا نشد و capture کامل ماند؛ network، live session و product DB استفاده نشد؛
- container، image و temporary root همگی حذف شدند.

image ID فوق artifact محلی موقت بود و در cleanup حذف شد؛ registry digest انتشار نیست.

## 7. gate بازگشتی Docker Stage 3

پس از افزودن dependencyهای capture، rehearsal کامل foundation دوباره اجرا شد:

- دو build مستقل و `--no-cache` از SHA یکسان image ID برابر ساختند؛
- candidate موقت: `sha256:52ae109492cdf93431c3f1f165713f0a6c1a07240bb8ffe7cdc73e5177b38519`؛
- image size: 147.232 MiB؛ buildها: 22.950 و 23.100 ثانیه؛
- filesystem/history secret scan هر دو PASS؛
- هفت service وب و چهار service بات non-root/read-only و بدون port اضافی ماندند؛
- migration 22 جدول و second-pass no-op، ACK/replay، snapshot، recreate، writer locks و
  rollback همگی PASS؛
- container/network/image/temp cleanup کامل بود.

## 8. نتیجه و مرحله بعد

Gate مرحله 4 برای کد و rehearsal مصنوعی بسته است. cutover هنوز مجاز یا انجام‌شده نیست؛
آزمون live بازار باز و جابه‌جایی session در مراحل rollout جدا انجام می‌شود.

مرحله بعد Stage 5 است: انتقال parser دو گروه سکه روی وب/داده، ساخت reply graph و
مقایسه reason-coded آن با parser فعلی بدون تغییر authority مدل.
