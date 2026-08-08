# Stage 0B-6 — ماتریس ضرورت محتوای کل سیستم

وضعیت: `stage0b6_complete_stage1_authorized`؛ validation Figma/local/Sites و source binding پاس شده است

اصل: هر واحد پیش‌فرض باید کار، تصمیم، اقدام، وضعیت ضروری یا ریسک واقعی را روشن کند. «فضای خالی» خروجی معتبر طراحی است و با KPI، کارت یا توضیح بی‌اثر پر نمی‌شود.

## معنای تصمیم‌ها

- `Keep`: بدون این واحد، کاربر کار/تصمیم/اقدام/ریسک فعلی را درست نمی‌فهمد.
- `On demand`: مفید است اما فقط پس از انتخاب، ورود به جزئیات یا درخواست کاربر.
- `Remove`: تکراری، تزئینی، داخلی، غیرقابل اقدام یا بدون اثر بر تصمیم فعلی.
- `Protected`: interior خارج از دامنه است و درباره محتوای آن تصمیم تازه‌ای گرفته نمی‌شود.

## ماتریس خانواده‌ها

| خانواده | پرسش پنج‌ثانیه‌ای کاربر | Keep در نمای پیش‌فرض | On demand / مقصد بعدی | Remove از نمای پیش‌فرض |
| --- | --- | --- | --- | --- |
| ورود | چگونه امن وارد شوم و اگر شکست خورد چه کنم؟ | عنوان مقصد، fieldهای لازم، CTA، خطای field/form، recovery واقعی | راهنمای امنیت پس از نیاز | معرفی بازار به‌جای محصول، route/backend، کارت مزایا، greeting |
| دعوت | آیا دعوت معتبر است و انتخاب واقعی من چیست؟ | inviter لازم، اعتبار/مهلت مؤثر، انتخاب Web/Telegram واقعاً موجود | جزئیات سیاست دعوت | نام نوع route، منبع server، summary تکراری لینک |
| ثبت‌نام | در کدام مرحله‌ام و چه داده‌ای لازم است؟ | step جاری، field لازم، validation، back/continue، حفظ داده در failure | توضیح طولانی حریم خصوصی در نقطه مرتبط | metadata مسیر، stepهای تمام‌شده تکراری، کارت توضیحی بی‌اقدام |
| تنظیم رمز | آیا security gate کامل شده و راه ادامه چیست؟ | requirementها، validation، busy/result و مقصد بعدی | راهنمای امنیت بیشتر | navigation روزانه، status موفق پیش از پاسخ، اطلاعات حساب اضافی |
| خانه آرام | آیا چیزی نیازمند توجه است و اقدام بعدی چیست؟ | هویت کوتاه، مهم‌ترین موضوع واقعی، navigation و slot محافظت‌شده بازار | تاریخچه، کالا، رابطه، تنظیمات | KPI عمومی، سلامت مثبت، greeting، تعداد معاملات/افراد/ابزار |
| خانه اختلال | داده تازه است و چه recovery دارم؟ | offline/stale/error دقیق، freshness مؤثر، retry واقعی | جزئیات فنی اتصال | چند badge هم‌معنا، skeleton روی داده قبلی، علت حدسی |
| PWA | آیا نصب الان مفید و قابل رد است؟ | یک فایده، install/later در state مجاز | راهنمای platform-specific | modal blocking، feature list، نسخه و تبلیغ |
| عملیات | کدام مقصد واقعی برای نقش من قابل انجام است؟ | عنوان، destinationهای مجاز و یک empty/recovery واقعی | شرح قابلیت پس از ورود | تعداد مسیر/ابزار، توضیح permission، role chip، tab مرده |
| فهرست مشتری | چه رابطه‌ای را پیدا و باز کنم؟ | search، هویت لازم، status مؤثر، pending action واقعی | جزئیات، تاریخچه، نشست و تنظیمات رابطه | تعداد کل روابط، server، route، آمار بدون اقدام |
| جزئیات مشتری | آیا روی فرد درست اقدام می‌کنم و پیامد چیست؟ | هویت، state رابطه، actionهای task و feedback همان context | audit/history، action کم‌تکرار | metadata داخلی، accordion بی‌دلیل، نتیجه دور از action |
| فهرست حسابدار | کدام حسابدار/رابطه نیازمند کار است؟ | search، هویت، status مؤثر و pending queue واقعی | جزئیات مالی/رابطه | total count، role/source/path، KPI تزئینی |
| جزئیات حسابدار | قبل/بعد و اثر اقدام چیست؟ | identity، مقدار قبل/بعد، اثر future-only، confirm/result | audit کامل | عدد تکراری، توضیح سرور، action بدون authority |
| مدیریت landing | چه صف یا اقدام مدیریتی واقعی دارم؟ | search، صف نیازمند رسیدگی، destinationهای واقعی | ابزار کم‌تکرار | تعداد ابزار، تعداد route، کارت KPI، متن «بر اساس نقش» |
| کاربران ادمین | کدام کاربر و کدام ریسک/محدودیت؟ | search، هویت، status اثرگذار و ورود به detail | PII و history فقط در detail مجاز | فیلتر ناقص، total count، server metadata، role توضیحی تکراری |
| تصمیم کاربر | پیامد هشدار/محدودیت/مسدودیت/حذف چیست؟ | action مستقل، دامنه/مدت/مقدار، confirm، busy و result | audit/receipt | actionهای ادغام‌شده، موفقیت optimistic، پیام delivery بدون receipt |
| دعوت ادمین | دعوت چه سطح مجازی می‌سازد و delivery چه شد؟ | گیرنده لازم، نقش مجاز، expiry و status authoritative | receipt/detail | نقش مدیر ارشد، count تزئینی، claim Telegram بدون receipt |
| حساب | مقصد پرتکرار من کدام است؟ | هویت، profile/security/storage/notifications مجاز و وضعیت محدودکننده واقعی | جزئیات نقش/حساب | status مثبت «فعال»، مقصد تکراری، membership/trade/relation count |
| پروفایل شخصی | چه چیزی قابل مشاهده یا ویرایش است؟ | avatar، نام، mobile read-only، address/action پشتیبانی‌شده | history/relations | field قابل‌ویرایش خیالی، server/path، آمار تزئینی |
| پروفایل عمومی | آیا فرد درست است و چه داده/action مجاز است؟ | هویت، phone masked، address hidden، action مجاز | PII کامل فقط self/admin detail مجاز | PII کامل عمومی، شمارنده روابط، history پیش‌فرض |
| نشست‌ها | کدام device است و چه نشست مجاز به پایان است؟ | device/platform/last activity/current-primary signal لازم | IP و detail امنیتی | `home_server`، topology، merge cross-server، statusهای تزئینی |
| تصمیم پایان نشست | دقیقاً چه نشست‌هایی پایان می‌یابند؟ | scope، حفظ نشست جاری، confirm، busy/result | audit | copy مبهم «خروج از همه»، موفقیت قبل از revocation |
| حافظه محلی | چه داده‌ای روی همین device پاک می‌شود؟ | scope local، size واقعی یا size-error، busy/result | جزئیات فایل‌ها | ادعای حذف حساب/server data، یکی‌گرفتن صفر با خطا |
| اعلان | چه مورد تازه/قابل اقدام است؟ | tab معاملات/سایر، content/time، route واقعی و signal تازه | جزئیات مقصد | total از ۵۰ مورد، route خام، clear/delete خیالی، count ساختگی |
| Push | آیا فعال‌سازی در این browser واقعاً ممکن است؟ | state دقیق و CTA فقط در `unsubscribed` قابل اقدام | راهنمای browser/device | disable/test خیالی، تضمین همه deviceها/Telegram |
| 404/forbidden | چرا اینجا هستم و راه معتبر بازگشت چیست؟ | عنوان cause-appropriate، recovery و shell درست | detail پشتیبانی | blank، route/backend، redirect loop، CTA به مسیر نامجاز |
| دسکتاپ | چگونه همان task را سریع‌تر انجام دهم؟ | همان factها با layout مناسب؛ list/detail در task پیچیده | pane ثانویه لازم | KPI، فیلتر، navigation یا fact تازه برای پرکردن فضا |
| بازار | خارج از دامنه | `Protected`؛ فقط جایگاه/shell موجود | طبق محصول موجود | هر تصمیم محتوایی یا CTA تازه از این roadmap |
| پیام‌رسان | خارج از دامنه | `Protected`؛ فقط مقصد shell موجود | طبق محصول موجود | هر تصمیم محتوایی یا behavior تازه از این roadmap |

## قواعد cross-family

### Keep مشترک

- عنوان مقصد/کار وقتی از context روشن نیست؛
- هویت لازم برای جلوگیری از اقدام روی شخص/حساب اشتباه؛
- status فقط وقتی ترتیب کار، دسترسی یا پیامد را عوض می‌کند؛
- یک اقدام اصلی و actionهای ثانویه واقعاً قابل استفاده؛
- validation، deadline، risk و feedback نزدیک مبدأ؛
- recovery واقعی برای loading/error/offline/forbidden؛
- حریم خصوصی و authority در نقطه تصمیم.

### On demand مشترک

- history، audit و receipt؛
- IP و جزئیات نشست؛
- PII کامل در detail اختصاصی مجاز؛
- علت/سیاست طولانی محدودیت؛
- actionهای کم‌تکرار یا destructive خارج از task جاری؛
- analytics فقط در surface گزارش واقعی.

### Remove مشترک

- تعداد کل روابط، ابزارها، routeها یا featureها؛
- نام route، endpoint، backend، server، source یا node؛
- role chip وقتی گزینه‌های واقعی نقش از قبل scope شده‌اند؛
- «فعال/سالم/آماده» بدیهی و مثبت؛
- subtitle، notice و CTA با معنای تکراری؛
- badge/count بدون صف یا action وابسته؛
- divider، icon، card یا illustration صرفاً برای پرکردن فضا؛
- reviewer metadata، node ID، hash، run ID و evidence label در product root.

## آزمون پذیرش محتوا

برای هر product root Figma و هر route در Stage اجرایی:

1. inventory واحدهای همیشه‌نمایان استخراج می‌شود.
2. هر واحد باید owner، پرسش کاربر و اثر بر تصمیم/اقدام/ریسک داشته باشد.
3. واحد بدون توجیه blocker است؛ whitespace مجوز افزودن محتوا نیست.
4. یک fact فقط یک خانه بصری اصلی در همان state/viewport دارد.
5. dense data برای stress layout است و مجوز افشای metadata ثانویه نیست.
6. حریم خصوصی، security، validation و recovery به نام مینیمالیسم حذف نمی‌شوند.
7. protected interior در inventory محتوایی وارد نمی‌شود؛ فقط absence/regression آن audit می‌شود.

## وضعیت validation

assertion `content-necessity-inventory-complete` در audit مستقیم Figma و harness محلی نهایی هر دو پاس است. local semantic hardening امضای دقیق متن/action هر پنج family، Home آرام و خالی در همه proofهای responsive، facts/actions دقیق دسکتاپ، state دیداری Auth و selected row متمایز/`aria-current` دسکتاپ را سنجیده و `driftFindings: []` ثبت کرده است. این validation درباره محتوای evidence است، نه رفتار runtime.

```text
ownerSystemContractApproval.status = approved
ownerSystemContractApproval.approvedAt = 2026-08-08T20:57:28.073Z
continuousProgressionAuthorized = true
runtimeImplementationAuthorized = true
nextAuthorizedRuntimeStage = Stage 1
stage1RuntimeWorkStarted = false
```

Stage 1 اکنون مجاز است اما در این snapshot runtime edit آن شروع نشده است.
