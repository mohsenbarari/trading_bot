# اصلاحات امنیتی ۲۳ اکتبر ۲۰۲۶

## دامنه و وضعیت

این کار مستقل از ریفکتور معماری است. مبنای شاخهٔ امنیتی، کامیت
`95283032898dd2a2318dd06e94521f12ba482934` است. کاربر صریحاً ادغام اصلاحات
امنیتی در `main` و ارسال تاریخچهٔ موجود آن، شامل ۲۴ کامیت محلیِ قبلاً ارسال‌نشده،
را مجاز کرده است. این اجازه معادل فعال‌سازی تغییرات بازار نیست؛ worktree فعال
ریفکتور در این کار تغییر نمی‌کند. وضعیت فعلی **اصلاحات اپ آزموده و بازبینی شده، ولی
هنوز مستقر نشده؛ Docker/containerd میزبان بات ارتقا یافته و رفع کامل باقی باز است**.
نباید نتیجهٔ آزمون کد یا lockfile را به‌عنوان پاک‌بودن سرویس‌های جاری گزارش کرد.
اصل بسته در دو کامیت `0a5e21e5` و `3f6682e6` با fast-forward در main ادغام و
به remote ارسال شد. تغییر کامیت‌نشدهٔ retention کاربر عیناً حفظ شد. اجرای CI
همان SHA شکست‌های زیر را آشکار کرد؛ نتیجهٔ محلی جای تأیید CI جدید را نمی‌گیرد.

## تغییرات و سناریوهای پذیرش

| مسیر | خطر مشاهده‌شده | اصلاح | اثبات لازم |
| --- | --- | --- | --- |
| فایل‌های وب | مسیر catch-all بدون containment | resolve زیر ریشهٔ build برای فایل و index؛ رد traversal، مسیر مطلق و symlink بیرونی | درخواست واقعی ASGI با مسیر خام/encoded و خروجی 404؛ حفظ SPA، هدرها و JS قدیمی 410 |
| فرم/API | Starlette قدیمی بدون محدودیت مؤثر برخی فیلدها | ارتقای سازگار خانوادهٔ FastAPI/Starlette/Pydantic | ورودی تکه‌تکهٔ urlencoded/multipart بیش‌ازحد رد شود؛ فرم و فایل عادی پذیرفته شوند |
| دریافت تصویر | Pillow قدیمی؛ canvas بزرگ APNG پیش از کنترل اندازه | Pillow 13، کنترل PNG و پیکسل‌ها، عدم ذخیرهٔ اصل تصویر ردشده | fixture کوچک بدون تخصیص بزرگ؛ EXIF/JPEG/PNG عادی بدون تغییر رفتار |
| بات و HTTP client | aiohttp قدیمی؛ محدودیت نسخهٔ aiogram مانع patch | ارتقای هماهنگ aiogram/aiohttp؛ urllib3 و pyasn1 اصلاح‌شده | resolver، آزمون قراردادها و API/Bot، بدون شبکهٔ واقعی |
| فرانت و ابزار ساخت | هشدارهای npm از جمله Vite/Vitest | lock تازه و هماهنگ؛ حذف وابستگی قدیمی tinypool با Vitest 4 | اسکن نام/نسخه، build، همهٔ unitها و guardهای UI؛ بدون حذف assertion |
| پایهٔ ایمیج | Bullseye پایان‌یافته، CA قدیمی | Python 3.11.17 روی Bookworm با digest؛ دریافت OS updates از مخزن رسمی | ساخت و آزمون واقعی ایمیج؛ CA سیستم و certifi جداگانه بررسی شوند |
| Node | Node 20 پایان‌یافته و Brotli آسیب‌پذیر؛ bootstrap انتشار ناسازگار با guard جدید | `.nvmrc` و CI روی 24.22.1؛ policy مشترک build/test/deploy؛ نصب محدود و SHA-verified در checkout | Node قدیمی حتی پیش از cache رد شود؛ PATH فقط فرایند انتشار و فرزندانش تغییر کند |

FastAPI جدید با Starlette جدید و aiogram جدید با aiohttp جدید باید با هم نصب
شوند؛ force-upgrade ترانزیتیو برخلاف محدودیت والد پذیرفته نیست. پین‌های نسخهٔ
هدف امنیتی در `requirements.txt` و lock فرانت در `frontend/package-lock.json`
قرار دارند. requirements فعلی، lock هش‌دار کامل همهٔ وابستگی‌های ترانزیتیو نیست.

### مثال ابزار ساخت در زمان انتشار

اگر میزبان هنوز Node 20 داشته باشد، preflight نباید آن را قبول کند و سپس
build در guard جدید متوقف شود. انتشار همان policy فرانت را پیش از cache اجرا
می‌کند و در صورت نیاز فقط Node 24.22.1 با SHA رسمیِ مختص معماری را در
`.local/tools/` همان checkout نصب می‌کند. دانلود، زمان، اندازه و استخراج
محدودند؛ archive پیش از استخراج اعتبارسنجی می‌شود. lock هم‌زمانی، hardlink،
symlink و FIFO نامعتبر را بدون truncation یا انتظار نامحدود رد می‌کند.
بستهٔ موجودِ نامعتبر overwrite نمی‌شود. symlinkهای Node سراسری، apt و سرویس‌ها
دست نمی‌خورند؛ shell ساخت نیز PATH تأییدشده را با login profile جایگزین نمی‌کند.

### مثال تصویر مخرب و تصویر معتبر

فایل APNG کوچکی که اندازهٔ canvas بسیار بزرگی ادعا می‌کند نباید وارد مسیر
decode/EXIF/نوشتن فایل یا fallback ذخیرهٔ اصل شود. آزمون‌ها canvas کوچک و
محدودیت کوچک دارند و تخصیص خطرناک را mock می‌کنند؛ هیچ PoC پرمصرفی روی سرویس
واقعی اجرا نمی‌شود. PNG با IHDR تکراری/جابه‌جا نیز باید قبل از decode به کنترل
پیکسل برسد. Pillow 13 تخصیص اولیهٔ آسیب‌پذیر را به تعویق انداخته است؛ یک parser
جدید و پیچیدهٔ PNG جایگزین آن نشده است.

عکس عادی با orientation باید همچنان درست بچرخد؛ PNG شفاف، نوع فایل، ابعاد و
جریان album تغییری نمی‌کنند. فایل بیش‌ازحد با پاسخ 413 و بدون ذخیره رد می‌شود.

### مثال trust store

به‌روزرسانی CA میزبان، trust store کانتینر قدیمی یا certifi را تغییر نمی‌دهد.
`scripts/verify_image_trust.py` هنگام build نبودن ریشهٔ سلب‌اعتمادشدهٔ
`emSign ECC Root CA - G3` را در store مؤثر سیستم و، برای اپ، certifi بررسی می‌کند.
خالی‌بودن store یا نبودن certifi الزامی خطاست. شکست gate نباید با خاموش‌کردن
TLS verification یا حذف بررسی دور زده شود. این یک gate محدود CA است، نه اسکن
کامل آسیب‌پذیری همهٔ لایه‌ها.

Market در هر دو مرحلهٔ assembly/runtime از همان `patched-base` استفاده می‌کند؛
اسکریپت gate در امضای ورودی ایمیج و payload کنترل نسخه قرار گرفته است. داده،
مدل، checkpoint و مالکیت session تلگرام تغییر نمی‌کنند.

## شواهد تأییدشده تا این مرحله

- ۱۱ آزمون مستقل containment فایل‌ها پاس شد؛ به build موجود یا import اپ زنده
  وابسته نیستند و در نبود dist نیز skip نمی‌شوند.
- اصلاح تکمیلی Node: ۱۹ تست hermetic جدید و ۶۲ تست قرارداد موجود پاس شدند؛
  بازبین مستقل ۱۹ تست جدید، ۵۹ تست choreography و ۴۴ تست surface را پاس کرد.
  پین‌های SHA رسمی مستقل تطبیق داده شدند. سپس bootstrap واقعی در مسیر خصوصی
  checkout اصلی پاس شد: Node 24.22.1 و npm 11.19.1؛ Node سراسری 20.19.5
  دست‌نخورده است و ارتقای آن ادعا نمی‌شود.
- CI قدیمِ تست observability هنوز literal نسخهٔ ناامن Node را الزامی می‌کرد.
  فقط قرارداد آزمون با policy جدید هماهنگ شد؛ گیت کامل ۱۴۶ تست و آزمون مستقل
  ۳۰تاییِ config/Node پاس شدند، بدون تغییر logging یا حذف آزمون.
- دو شکست PostgreSQL از fixture قدیمیِ فاقد هویت canonical منتشرکننده بودند،
  نه تغییر امنیتی سرویس بازار. هر دو شکست روی PostgreSQL موقت و جدا بازتولید
  شدند. اصلاح باید فقط دادهٔ fixture آزمون‌های fairness را کامل کند؛ assertionهای
  ارسال و آزمون‌های مستقلِ هویت مفقود دست‌نخورده ماندند. بعد از اصلاح، تمام
  ۸ تست ماژول PostgreSQL و ۱۸ تست reconciliation روی ایمیج واقعی با وابستگی‌های
  جدید پاس شدند، بدون skip. ۳۱ تست مستقل سرویس/هویت نیز پاس شدند؛ بازبین
  ثابت کرد تمام ۳۲ assertion و بدنهٔ متدهای آزمون تغییر نکرده‌اند. CI جدید
  باید جداگانه تأیید شود.
- CI بعدی، پاس‌شدن هر ۸ تست publication را تأیید کرد و به fixture قدیمیِ
  دیگری در آزمون receipt انقضا رسید: وضعیت SENT با message ID، ولی بدون
  publisher/chat ID معتبر. فقط دو فیلدِ ساختگی fixture کامل شدند؛ trigger و
  منطق سرویس تغییر نکردند و هر ۲۹ assertion حفظ شد. برای جلوگیری از تکرار
  ارسال/انتظار، تمام ۹ مجموعهٔ PostgreSQL یک‌جا روی PG/Redis موقت و داخلی
  اجرا شدند. پس از بازاجرای مجموعهٔ اصلاح‌شده، نتیجهٔ تجمیعی **۴۷/۴۷، بدون
  skip** است؛ ۳۸ تست انقضا/reconciliation/state نیز در اجرای پایانی پاس شد.
  این شاهدِ محلیِ ماژول‌هاست، نه receipt رسمی CI یا مجوز استقرار. کانتینرها
  و شبکهٔ آزمون فقط منابع اختصاصی موقت‌اند و پس از آزمون حذف می‌شوند.
- اجرای CI روی `3daff855`، عبور گیت PostgreSQL را تأیید کرد. مرحلهٔ بعدیِ
  دو پردازهٔ Redis به علت نبود چهار تنظیم اجباری در همان step، پیش از subscribe
  شکست خورد؛ env فرزندِ runner قبلی به step بعدی منتقل نمی‌شود. هر دو workflow
  با config عمومی unit و تنظیمات صریحِ scratch کامل شدند. گزارش خطای startup
  فقط نام کلاس خطا را می‌فرستد، نه متن ValidationError یا مقادیر محیط؛ readiness
  دقیقاً True لازم دارد. assertionهای تحویل/عدم‌تکرار پیام و timeoutها حفظ شدند.
  شکست اصلی روی Redis جدا بازتولید شد؛ پس از اصلاح، ۳/۳ تست با دو پردازهٔ
  واقعی، بدون skip، و ۱۵ regression بدون شبکه پاس شدند. بازبین مستقل پذیرفت؛
  Redis موقت حذف شد. نتیجهٔ نهایی CI و مرورگر همچنان جداگانه لازم است.
- تطبیق سورس واقعی ایمیج زنده، ۴۹۶ فایل Python مجاز را با کامیت
  `ba1f7616ade57558f51bb3a7fbb903243a33aab4` منطبق یافت. آزمون ۴۷ مسیر consumer
  و ۲۲ subtest با ممنوع‌کردن اجرای producerهای تغییرکرده پاس شد و فراخوانی
  آن‌ها صفر بود. برای حفظ این نتیجه، mode برابر LEGACY، انتخاب خودکار خاموش،
  فرمان‌های فعلی اپ/بات/سینک و mountهای snapshot فقط‌خواندنی باید حفظ شوند؛
  release امنیتی نباید producer یا bridge مستقل بازار را راه‌اندازی/تغییر دهد.
- اسکن تکمیلی فرانت با ارسال فقط ۸۸۸ نام/نسخهٔ عمومی به endpoint رسمی npm،
  پاسخ HTTP 200 و `{}` داد. ۵۸ شناسهٔ GHSA متمایز اسکن قبلی در پاسخ جدید نیستند؛
  این عدد معادل ۵۸ exploit عملیاتی یا CVE مستقل نیست.
- مجموعهٔ جدید بک‌اند در venv مستقل resolve و dependency-check شد. import اپ و
  OpenAPI با ۱۸۸ مسیر و ۱۸۰ schema، بدون lifespan و با شبکهٔ مسدود، پاس شد.
- پایهٔ واقعی Python 3.11.17 ساخته و در کانتینر موقت بدون شبکه/mount اجرا شد:
  CA نسخهٔ `20250419~deb12u2`، OpenSSL `3.0.22-1~deb12u1` و gate CA پاس شدند.
- Buildx میزبان از 0.36.1 به 0.38.1 ارتقا یافت. فقط همان بسته تغییر کرد؛
  `NEEDRESTART_MODE=l` مانع restartهای جانبی شد. PID daemon و شناسه/uptime هر
  ۱۵ کانتینر قبل/بعد ثابت ماندند. این به معنی رفع Docker/containerd/kernel نیست.
- آزمون گستردهٔ بک‌اند: ۱۳۴۶ تست و ۲۱۱ subtest پاس شد؛ تکرار نهایی آپلود/امنیت
  با تست‌های جدید نیز ۴۱ تست و ۳۴ subtest پاس شد.
- ایمیج کامل آزمایشی اپ با شناسهٔ
  `sha256:61e83a73ebd37b8582140a4c0cc9abcd8895a8b0b4f5289dc1fd07d2bae78f34`
  ساخته شد: ۸۲ تست امنیت/آپلود/API روی Python 3.11.17 پاس شد. ۱۸۹ مسیر OpenAPI
  با dist، در برابر ۱۸۸ مسیر بدون dist، و ۱۸۰ schema ساخته شد؛ lifespan اجرا
  نشد. این ایمیجِ validation است، نه release مجاز برای production.
- HTTP 200 و رفت‌وبرگشت واقعی WebSocket روی همان ایمیج، با Uvicorn 0.30.1 و
  websockets 17.3 پاس شد؛ فقط loopback کانتینر بدون شبکهٔ خارجی استفاده شد.
- پس از اجازهٔ صریح کاربر، metadata رسمی PyPI برای ۸۸ وابستگی محیط آزمون و
  سه ابزار bootstrap متفاوت ایمیج بررسی شد. هر ۸۷ توزیع Python در ایمیج واقعی
  پوشش داده شد؛ دو CVE متمایز باقی است، نه چهار آسیب ناشی از چهار alias:
  `CVE-2026-85394` در python-jose و `CVE-2024-23342` در ecdsa.
- مجموعهٔ کامل نهایی فرانت با دو worker: ۱۷۰ فایل و ۲۰۰۸ تست، همه پاس، در
  ۲۲۸٫۳۱ ثانیه. build با guard نسخهٔ Node در ۳۴٫۱۰ ثانیه، guardهای محافظ UI و
  بررسی سازگاری peerها نیز پاس شدند. بازبین مستقل ۹۹ تست هدفمند را دوباره
  اجرا و نبود حذف assertion/skip را بررسی کرد.
- اجرای اختیاری coverage چهار-worker به علت رقابت منابع و timeout یک آزمون
  قطع شد؛ موفق اعلام نمی‌شود. اجرای کامل دو-worker بالا بدون افزایش timeout
  پاس شد. مرورگرهای E2E محلی نصب نبودند و دانلود حجیم انجام نشد.
- دو بازبین مستقل تغییرات محدودهٔ خود را پذیرفتند. `git diff --check`، syntax
  شل و قراردادهای سورس/ایمیج پاس شدند. `MemoryCustodian check` یک هشدار قدیمی
  دارد: `decisions.md` موجود ۸۸۶/۸۰۰ token است؛ این فایل در این بسته تغییر
  نکرده و پاکسازی حافظهٔ نامرتبط انجام نشده است.
- کش npm مخصوص همین آزمون (حدود ۳۸۳MB) و archive نصب Node (حدود ۳۲MB) حذف شد؛
  هیچ داده، backup، log عملیاتی، volume یا ایمیج فعال حذف نشد. حدود ۵٫۲GB روی
  دیسک اصلی و ۱۳GB روی دیسک داده آزاد باقی ماند. ابزار/venv و ایمیج‌های
  validation فقط تا نیاز به بازبینی/انتشار نگهداری شوند؛ بازبینی پاکسازی
  artifacts این کار حداکثر تا ۲۰۲۶-۱۰-۳۰، با حفظ evidence رخداد باز، لازم است.

آزمون‌های importکنندهٔ اپ باید پیش از import،
`APP_ENV_FILE=config/unit-test.env.example` را تنظیم و دسترسی شبکهٔ واقعی را
مسدود کنند. هیچ `.env`، دادهٔ کاربر یا دیتابیس عملیاتی برای آزمون بارگذاری نشود.
گزارش‌های موقت فقط در `.local/` همان worktree قرار می‌گیرند؛ کش‌های دانلود
آزمایش پس از استفاده قابل حذف‌اند و نباید به artifact دائمی تبدیل شوند.

## مواردی که هنوز بسته نیستند

### پیگیری CI و کنترل انتشار — ۲۳ اکتبر

- روی `aa8050b6`، گیت‌های governance، observability و PostgreSQL/Redis واقعی
  پاس شدند. ماتریس مرورگر پیش از اجرای assertion، هنگام bootstrap به مسیر
  bind عملیاتیِ ناموجود در runner برخورد کرد؛ این شکست، نتیجهٔ E2E موفق نیست.
  هر دو workflow اکنون سه مسیر bind را صریحاً در `/tmp` آماده می‌کنند؛ مسیرهای
  snapshot خالی‌اند و read-only و `create_host_path: false` حفظ می‌شوند.
  قرارداد چهارآزمونیِ اجباری قبل از fan-out مرورگر اضافه شد. ۱۹ آزمون مرتبط
  و بازبینی مستقل پاس شد؛ آزمون Docker ایزوله نیز mount خالی و read-only را
  تأیید کرد. اجرای CI جدید همچنان باید جداگانه بررسی شود.
- اجرای coverage شمارهٔ `39993855386` ناموفق شد؛ علت دقیق شکست مرحلهٔ Stage 9
  و وضعیت suiteها در [گزارش تفکیکی CI](2026-10-23-ci-coverage-followup.md)
  ثبت شد. ۱۴ failure و ۸۲ error بک‌اند موفق اعلام نمی‌شوند؛ سه شکست تاریخی
  با هر دو مجموعهٔ وابستگی قدیم/جدید بازتولید شدند و هر ۱۳ تست expiry در اجرای
  ایزوله با هر دو پاس شدند. علت تداخل expiry در مجموعهٔ کامل و mutation باز است.
- در بررسی فقط‌خواندنی، مسیر خصوصی کنترل انتشار یک bind فعال از دیسک متصل
  بود؛ عنوان قدیمیِ مسیر مبدأ به معنی backup قابل حذف نیست. جابه‌جایی
  filesystem با اختلاف device/inode قفل هم‌خوان است. زنجیرهٔ واقعی authorization
  و mirror پیش از انتقال با journal نهایی، جز سه تغییر مجازِ تکمیل انتقال،
  تطبیق داده شد. [ابزار بازیابی محدود](2026-10-23-bot-lock-identity-recovery.md)
  بازبینی مستقل و آزمون crash/resume را گذراند؛ همراه گاردهای قبلی و backup،
  ۷۰ آزمون مستقل بدون skip پاس شد. restore-smoke اکنون بدون شبکه/دانلود و با
  سقف منابع اجرا می‌شود. هیچ قفل، journal، سرویس یا دادهٔ عملیاتی تغییر نکرد.
- **بازیابی عملیاتی هنوز مجاز به اجرا نیست:** بررسی زنده نشان داد واحد گروه
  به projection از facts تبدیل شده، ولی collectorهای private-gold و public
  همچنان واقعی و فعال‌اند. بنابراین inventory تاریخی quiesced دیگر وضعیت
  امروز را توصیف نمی‌کند؛ ابزار پیش از نوشتن باید امتناع کند. توقف projection
  یا collector برای سبزکردن صوری گیت ممنوع است؛ قرارداد مالکیت ingestion فعلی
  باید جداگانه با حفظ ورودی مدل تطبیق داده شود.
- کاربر پنجرهٔ نگهداری شب ۲۳ تا صبح ۲۴ اکتبر را صریحاً برای توقف هر دو پروژهٔ
  trading-bot و golden_bot تأیید کرد. این مجوز تاریخ‌دار جای backup قابل restore،
  حفظ بسته‌های قبلی، بازگشت سرویس‌ها یا کنترل پس از reboot را نمی‌گیرد و برای
  پنجره‌های بعدی مجوز دائمی محسوب نمی‌شود. ارتقای daemon انجام شد؛ reboot هنوز
  اجرا نشده است. جزئیات اثبات‌شده در بخش نگهداری زیر آمده است.

1. **استقرار اپ و فرانت:** ایمیج‌های زنده همچنان قدیمی‌اند. ساخت/آزمون این شاخه
   مجوز اعلام «production امن شد» نیست. frontend تولیدشده نیز تا استقرار و
   اثبات digest/SBOM همان frontend عملیاتی محسوب نمی‌شود.
2. **Docker/containerd:** میزبان بات اکنون واقعاً Docker 29.9.2 و containerd
   2.4.2 اجرا می‌کند؛ صرف ارتقای CLI گزارش نشده است. وضعیت backport توزیع سرور
   وب همچنان جداگانه باز است. اصلاحات containerd به feature و backport وابسته‌اند؛
   نسخهٔ upstream به‌تنهایی برای verdict کافی نیست. شواهد و محدودیت‌های هر دو
   میزبان در بخش نگهداری زیر تفکیک شده‌اند.
3. **کرنل:** کرنل اصلاح‌شده نصب شده ولی کرنل فعال قدیمی است. reboot بدون پنجرهٔ
   نگهداری، بررسی backup/restore، quiescence و کنترل مالکیت بات/سینک انجام نشود.
4. **Brotli میزبان و stage قدیمی:** Node 20 و Python/OS Brotli قدیمی با نصب ابزار
   side-by-side یا تغییر فایل repo رفع نمی‌شوند. برای هر مصرف‌کننده، ارتقای
   vendor و restart/retirement کنترل‌شده لازم است. رشتهٔ نسخهٔ Brotli در Node
   vendor-patched ممکن است همچنان قدیمی بماند؛ release دقیق مبناست.
5. **JOSE/ECDSA:** نسخهٔ 3.5 مشکلات قبلی را رفع می‌کند، اما برای CVE-2026-85394 patch
   اعلام‌شده‌ای تأیید نشده است. allowlist ثابت الگوریتم و کلید HMAC مستقل حفظ
   شود؛ public key نامتقارن نباید کلید HMAC باشد. آزمایش قرارداد JWT و ارزیابی
   مسیر واقعی، جای ادعای «بستهٔ کاملاً پاک» را نمی‌گیرد. ecdsa 0.19.2 نیز هشدار
   timing هنگام signing/key generation/ECDH بدون patch دارد؛ verification
   مشمول همان مشکل نیست. کد فعلی HS256 و backend انتخاب‌شدهٔ JOSE از cryptography
   استفاده می‌کند؛ مسیر آسیب‌پذیر signing بومی ECDSA مشاهده نشد. این ارزیابی
   reachability است، نه حذف هشدار کتابخانه.
6. **پوشش اسکن:** تمام OS packageها/لایه‌های همهٔ ایمیج‌ها اسکن نشده‌اند. پوشش
   metadata پکیج‌های Python مربوط به ایمیج آزمایشی جدید است، نه هر ایمیج عملیاتی.
   مشکل قبلی مجوز PyPI با تأیید کاربر رفع و اسکن فوق تکمیل شد.
7. **میزبان هدف جدید:** دسترسی SSH ممیزی قبلی پذیرفته نشد؛ وضعیت امنیتی آن
   «نامعلوم» است، نه سالم. از تعویض کلید یا غیرفعال‌کردن host verification پرهیز شود.
8. **Nginx:** پیش‌شرط برخی advisories تازه در پیکربندی ممیزی‌شده وجود نداشت؛
   این معادل پاک‌بودن همهٔ server blockها، TLS inheritance و buildها نیست.
9. **اعتبارسنجی مرورگر:** unit و build جای E2E واقعی را نمی‌گیرند. در ممیزی
   مشخص شد مسیر قدیمی ImageEditor از شکل callback برای `Fabric.Image.fromURL`
   استفاده می‌کند، در حالی که امضای هر دو Fabric 7.3.1 قبلی و 7.4.0 جدید
   Promise است. این نقص از قبل وجود دارد و در این بستهٔ امنیتی UI بازنویسی
   نشده است؛ برای آن بررسی/رفع مستقل و آزمون مرورگر پیش از ادعای سلامت کامل
   ویرایش تصویر لازم است.
10. **بازیابی کنترل انتشار:** قفل پایدارِ انتقال اختیار بازار و journal آن
    وضعیت `AUTHORITY_TRANSFERRED` همسان دارند، ولی هویت device/inode ثبت‌شده
    با فایل جاری تطابق ندارد. کنترلر موجود هیچ مسیر بازتأییدِ این وضعیت را
    ندارد؛ نبودن PID زنده دلیل مجازبودن حذف قفل نیست. آماده‌سازی رسمی انتشار
    اجرا نشد. ابزار محدود اکنون آزموده و بازبینی شده، ولی ناسازگاری inventory
    زندهٔ بالا مستقل از inode مانع اجراست. مالکیت capture و projection موجود
    باید حفظ شود؛ حذف قفل، تغییر دستی receipt یا خاموش‌کردن guard ممنوع است.

## نگهداری واقعی میزبان بات — ۲۳ اکتبر، ساعت ۱۹:۰۴ UTC

- هر دو production backup با restore-smoke بدون شبکه و cleanup موفق تکمیل شد؛
  چهار archive هر سایت جداگانه اعتبارسنجی شدند. Golden با API آنلاین SQLite
  backup گرفته شد، نه کپی خام فایل زنده؛ quick_check نسخهٔ backup موفق بود.
- پنج بستهٔ قدیمی از مخزن رسمی، با نام/نسخه/معماری/اندازه و SHA شاخص رسمی
  تطبیق داده و حفظ شدند. مجموع ۱۰ فایل شامل چهار archive سایت foreign، پنج
  بسته و backup Golden، برابر ۱۱۸٬۳۱۲٬۲۹۷ بایت، با SSH strict به سرور وب منتقل
  و hash/size و fsync آن تأیید شد. چهار archive سایت Iran نیز به کنترلر کشیده
  و با receipt اصلی تطبیق داده شد. هیچ backup قبلی حذف نشد.
- بستهٔ Ansible و guard ابتدا مستقل بازبینی شد؛ در آخرین نسخه، **۵۷ آزمون
  hermetic بدون skip** پاس شد. preflight واقعی، ترتیب نامعین mountها، Health
  اختیاری Docker و تفاوت loopback داخل کانتینر با پورت منتشرشدهٔ Foreign را
  پیش از توقف گرفت؛ اصلاح‌ها هیچ گارد نقش/احراز هویت را ضعیف نکردند.
- اجرای واقعی از `b577c5e5` و manifest خصوصی با SHA
  `587eb4bc43c2ccf12c5d5517a55daeebc93d53e7bb42d4825fa5dde718424b37`
  پس از preflight کامل انجام شد. Ansible: ۲۹ task موفق، ۱۳ تغییر، صفر failure،
  صفر rescue. receipt مستقل `docker_daemon_maintenance_result/1` در
  `security-host-daemon-20261023-01.daemon-maintenance-result.json` وضعیت
  `completed` را در `2026-10-23T19:04:16.866321+00:00` ثبت کرد.
- Docker server **29.9.2** و containerd server **2.4.2** به‌صورت واقعی بررسی
  شدند. هر ۱۵ کانتینر با همان ID، image ID، project، mount و restart policy
  بازگشتند. چهار DB/Redis با pg_isready/PONG، سلامت کانتینرهای دارای healthcheck،
  API `/api/config` از loopback داخل همان کانتینر و ۲۱ واحد/تایمر بررسی شدند.
  Golden فعال است و quick_check دیتابیس زندهٔ آن نیز `ok` بود.
- marker و journal قدیمی عیناً حفظ شدند؛ reservation موقت عملیات پس از اثبات
  سلامت حذف شد، نه قفل پایدار انتشار. Writer، DNS، capture owner، schema و
  ایمیج اپ تغییر نکردند. کرنل هنوز **6.8.0-138-generic** است؛ نصب کرنل جدید
  معادل اجرای آن نیست. راهنمای اجرا و محدودیت‌ها:
  [daemon maintenance](2026-10-23-daemon-maintenance.md).
- اتصال موقت Web→Bot با public key محدود به IP وب و انقضای پایان همین پنجره،
  بدون انتقال/خواندن محتوای کلید خصوصی توسط عامل، واقعاً تأیید شد. این اثبات SSH است، نه اثبات
  وجود runner مستقلِ پایدار برای reboot. پاکسازی دسترسی باید جداگانه ثبت شود.
  [راهنمای دسترسی موقت](2026-10-23-temporary-controller-access.md).
- پاکسازی واقعی همان دسترسی تکمیل شد: ابتدا فقط خط public key متعلق به
  مأموریت revoke شد، سپس کلید/پین/فایل عمومی همان workspace و پوشهٔ خالی حذف
  شدند. Ansible cleanup با صفر failure برگشت؛ محتوای کلید خصوصی توسط عامل
  خوانده یا منتقل نشد؛ SSH client فقط در محل تولید از آن استفاده کرد.
  نیازی به نگه‌داشتن credential موقت تا زمان انقضا باقی نماند.
- پیش‌بررسی و setup واقعی [گیت mount بوت](2026-10-23-boot-mount-gates.md)
  نیز موفق شد: ۱۱ drop-in محدود و dependency/assertionهای مؤثر آن‌ها پس از
  daemon-reload از خود systemd تأیید شدند؛ restart/reboot اجرا نشد. کنترل
  پس از آن، همان ۱۵ کانتینر با صفر RestartCount و StartTime پیش از این مرحله
  و همان marker/journal را تأیید کرد. این اثبات تنظیمات بارشده است، نه fault
  drill یا reboot موفق. runner مستقل reboot هنوز آماده/آزموده نشده است.
- پس از تأیید نسخهٔ نصب‌شده، فقط پنج archive دانلودشدهٔ همین ارتقا از cache
  apt حذف شدند: ۸۳٬۶۸۹٬۹۳۲ بایت قابل دریافت مجدد از مخزن رسمی. پنج بستهٔ قدیمی
  بازگشت، همهٔ backupها، داده‌ها، volumeها و ایمیج‌ها حفظ شدند.
- CI روی `00588776` هنوز مجوز انتشار اپ نمی‌دهد: coverage ناموفق شد و سه shard
  مرورگر شمارهٔ ۴ شکست خوردند؛ بخشی از matrix هنوز در حال اجرا بود. گیت‌های
  PostgreSQL، Redis و observability موفق‌اند، اما موفقیت آن‌ها جای پذیرش همهٔ
  آزمون‌های لازم را نمی‌گیرد. triage خطاهای باقی‌مانده جداگانه ادامه دارد.

### محدودیت حل‌نشدهٔ سرور وب Ubuntu 26.04

بستهٔ رسمی Docker.io-app همچنان `29.1.3-0ubuntu4.1` است. containerd-app موجود
`2.2.2-0ubuntu1.1` و candidate برابر `.1.2` است؛ تغییر candidate فقط اصلاح
ShutdownSandbox را نشان می‌دهد و مدرک رفع advisories تازه نیست. بستهٔ
containerd-stable همان توزیع نیز راه‌حل اثبات‌شده‌ای ارائه نمی‌کند.
برای [CVE-2026-92543](https://ubuntu.com/security/CVE-2026-92543) و
[CVE-2026-53493](https://ubuntu.com/security/CVE-2026-53493) ارزیابی vendor
همچنان باز است؛ وضعیت [CVE-2026-46680](https://ubuntu.com/security/CVE-2026-46680)
نیز باید با reachability واقعی بررسی شود. patch توزیع برای همهٔ این موارد
تأیید نشده؛ «سرور وب امن شد» گفته نمی‌شود.

بررسی بعدی، پشتیبانی رسمی Docker CE از **همان Ubuntu 26.04/Resolute** را
تأیید کرد؛ به مخزن توزیع دیگر نیاز نیست. InRelease و index رسمی CE با کلید
Docker و metadata چهار بستهٔ بازگشت Ubuntu با archive keyring مستقل تطبیق
شدند. بسته‌های هدف CE29.9.2/containerd2.4.2 موجودند؛ payload هنوز نصب نشده.
وب ۲۷ کانتینر، ۲۶ فعال و پنج project دارد و از قبل containerd image store
است؛ تبدیل صوری به overlay2 یا اتکا به backup صرفاً `/var/lib/docker` مجاز
نیست. طرح باید هر دو store موجود و وضعیت کانتینر متوقف را حفظ کند.
[پشتیبانی رسمی Docker](https://docs.docker.com/engine/install/ubuntu/).

phasing اجباری، مخزن توزیع دیگر یا مهاجرت CE بدون طرح سازگاری انجام نشد.
تعویض صرف نام بسته به stable هم این finding را نمی‌بندد. تا فعال‌سازی معتبر، از
pull/import/build غیرضروری روی این میزبان پرهیز شود. برای advisory registry،
اعتماد به image digest به‌تنهایی کافی نیست؛ DNS مورد اعتماد یا محدودسازی egress
باید جداگانه، بدون شکستن registry/CDNهای موردنیاز، طراحی و آزموده شود. منابع:
[Moby advisory](https://github.com/moby/moby/security/advisories/GHSA-7cfq-22r6-qp73)،
[containerd advisory](https://github.com/containerd/containerd/security/advisories/GHSA-vwhp-8p66-mq4f)،
[changelog رسمی Ubuntu](https://git.launchpad.net/ubuntu/+source/containerd-app/plain/debian/changelog?h=applied/ubuntu/resolute-updates).

## ترتیب فعال‌سازی ایمن

### پیگیری همین شب: سورس و آزمون، نه ادعای استقرار

- JOSE/ECDSA بدون patch از requirements اپ حذف و با PyJWT 2.15.1 جایگزین
  شدند. همهٔ decoderها allowlist قبلی را نگه می‌دارند؛ exception مشترک از
  `core/jwt_codec.py` می‌آید. توکن ساختگیِ تولیدشده با JOSE قدیمی، با همان
  امضا/claims پذیرفته می‌شود؛ future-iat نامعتبر برخلاف JOSE قدیمی رد می‌شود،
  نه اینکه برای سازگاری بررسی claim خاموش شود. advisory خود PyJWT در نسخه‌های
  تا 2.13 نیز لحاظ شد؛ نسخهٔ جایگزین از آن بازه نیست.
  [advisory رسمی](https://github.com/advisories/GHSA-ffc3-869f-jxw9)،
  [نسخهٔ رسمی](https://github.com/jpadilla/pyjwt/releases/tag/2.15.1).
- در venv جدا، دو کتابخانهٔ بازنشسته واقعاً حذف شدند و dependency-check پاس شد.
  ۹ تست codec/dependency با warnings=error و ۴۷ زیرآزمون پاس شد؛ بازبین مستقل
  نیز همین نتیجه را گرفت. ۱۲۲ تست مسیرهای auth/session/chat/WebSocket و
  ۶۷ زیرآزمون با TCP/DNS مسدود پاس شدند؛ ۱۴ تست media نیز پاس شد. هشدارهای
  قدیمی Pydantic/datetime در مجموعهٔ اپ باقی‌اند؛ اجرای اولیهٔ آن با
  warnings=error در collection شکست خورد و موفق اعلام نمی‌شود.
- فرانت: URL دارای توکن WebSocket، browser error خام و خطای parser دارای
  payload دیگر لاگ نمی‌شوند. race دریافت هویت Dashboard/BottomNav با حفظ
  force/token/logout fences اصلاح شد. ۱۷۰ فایل/۲۰۱۶ unit، build و گاردهای UI
  پاس شدند؛ بازبین مستقل ۵۰ تست هدفمند را پاس کرد. ۴۲ تست سه مرورگر فقط
  collect شدند؛ پذیرش واقعی مرورگر و CI جدید همچنان لازم‌اند.
- شش fixture کنترل انتشار برای runner غیر-root اصلاح شدند؛ production
  root/ownership/symlink guards تغییر نکردند. ۱۵۹ تست و ۶ زیرآزمون پاس شد؛
  بررسی مستقل شبکه‌بسته ۱۵۸ تست را پاس کرد و یک مورد mTLS ساختگیِ loopback
  را طبق fence رد کرد، نه اینکه موفق یا skip اعلام کند. smoke بیست‌ویک
  ابزار با interpreter نصب‌شدهٔ آزمون، نه Python سیستم فاقد dependencies، پاس شد.

### حادثهٔ آزمون و پیشگیری از تکرار

در اجرای اولیهٔ fixture اصلاح‌شده، اضافه‌شدن عضو پنجم به tuple چهارعضویِ
context باعث شد `_run` mock نشود و systemctl واقعی به پنج واحد Bot برسد.
آزمون متوقف شد. دقیقاً سه enablement ثبت‌شدهٔ قبلی و اجرای سرویس/timerها
بازگردانده شد؛ دو oneshot در `2026-10-23T19:34:43Z` مجدداً موفق شدند.
سپس کنترل واقعی baseline هر ۱۵ کانتینر، policies، DB/Redis/API و intent
سرویس‌ها پاس شد. هیچ تغییر اختیار Writer/DNS یا journal عملیاتی لازم نبود.
tuple چهارعضوی حفظ و mock ترکیبی نام‌دار شد؛ `subprocess.run` در setup این
suite بدون استثنا deny است مگر همان mock فرمان ساختگی. آزمون منفیِ حذف mock
باید پیش از رسیدن به میزبان خطا بدهد. این حادثه از گزارش حذف نمی‌شود.

### شرط بازگشت چرخه

طبق درخواست کاربر، چرخهٔ ریفکتور تا رفع و فعال‌سازی/راستی‌آزمایی یافته‌های
امنیتی شناسایی‌شده باز نمی‌گردد. سپس پایش باید ۳۰ واحد کاری واقعاً کامل و
پذیرفته‌شدهٔ بدون خطا را مشاهده کند؛ poll سلامت یا درخواست ناموفق واحد کامل
نیست. شکست‌ها باید رفع و با شواهد نگهداری شوند، نه با waiver یا reset تاریخچه.

1. بازبینی مستقل تغییرات، تکمیل آزمون‌ها، ثبت کامیت و ادغام/ارسال معمول main
   طبق مجوز صریح کاربر؛ سپس بررسی نتیجهٔ CI همان SHA. تغییر کامیت‌نشدهٔ کاربر
   در حافظهٔ retention نباید وارد کامیت امنیتی شود یا برای عبور gate پنهان شود.
2. ساخت release دقیق و ثبت image digest، dependency inventory و frontend artifact؛
   برنامهٔ rollback به digest قبلی، بدون حذف داده/backup.
3. rehearsal ایزولهٔ API، login/OTP، upload/album، بات، sync و مدل؛ هیچ session
   واقعی تلگرام با مالک دوم اجرا نشود.
4. فعال‌سازی طبق قرارداد انتشار هر سایت، بدون Writer/DNS change، با کنترل خطا،
   صف، latency و امکان بازگشت. schema/migration جدیدی در این بسته وجود ندارد.
5. ارتقای میزبان/daemon و reboot در پنجرهٔ کنترل‌شدهٔ جدا؛ سپس اجرای مجدد ممیزی
   نسخهٔ واقعی کرنل، runtime و trust stores. هیچ «رفع کامل» پیش از این مرحله ثبت نشود.

## منابع اصلی

- [Starlette multipart DoS](https://github.com/Kludex/starlette/security/advisories/GHSA-f96h-pmfr-66vw)
  و [urlencoded limits](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq).
- [Pillow APNG](https://github.com/python-pillow/Pillow/security/advisories/GHSA-5xm2-6676-qcp9).
- [aiohttp response parser](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-cq5v-8q36-5273).
- [JOSE algorithm confusion](https://github.com/mpdavis/python-jose/issues/414)
  و [ECDSA timing](https://github.com/tlsfuzzer/python-ecdsa/security/advisories/GHSA-wj6h-64fc-37mp).
- [Node 24.22.1 security release](https://nodejs.org/en/blog/release/v24.22.1).
- [CA distrust در Ubuntu](https://ubuntu.com/security/notices/USN-8944-1)
  و [Debian Bookworm](https://security-tracker.debian.org/tracker/DLA-4845-1).
- [Docker insecure-registry](https://github.com/moby/moby/security/advisories/GHSA-7cfq-22r6-qp73)،
  [Buildx bake](https://github.com/docker/buildx/security/advisories/GHSA-gwr2-q96m-6682)،
  [Ubuntu kernel](https://ubuntu.com/security/CVE-2026-53266).
