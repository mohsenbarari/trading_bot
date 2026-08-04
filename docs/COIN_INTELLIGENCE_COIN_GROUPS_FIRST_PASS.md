# P2-C-A — فیلتر و parser محافظه‌کار گروه‌های سکه

## مرز این مرحله

این مرحله parser متنِ گذرای گروه‌های ۱ و ۲ است. input می‌تواند text و
شناسهٔ خصوصی message داشته باشد، اما Market Store فقط fact اقتصادی و digest
opaque نگه می‌دارد. هیچ متن، لینک، نام فرستنده، شناسهٔ پیام یا reply ID در
fact نهایی ذخیره نمی‌شود.

raw staging سه‌روزه، reconciliation پیام edit شده، reply-chain و trade
linking هنوز به worker P2-C-B واگذار شده‌اند. این commit هیچ Telegram
collector یا database runtime جدیدی فعال نمی‌کند.

## قواعد پذیرش

هر خط پیام مستقل بررسی می‌شود. خطی که یکی از شرایط زیر را داشته باشد fact
نمی‌سازد:

- سال‌های `403`/`404`/`1403`/`1404`؛
- پنجشنبه یا کشیک؛
- نمونه/راهنما/پیام سیستمی؛
- سمت، price یا quantity نامشخص؛
- price ناممکن پس از normalizing shorthand؛
- نوع کالای explicit که با static safety range آن سازگار نیست.

فرمت‌های متداول مانند `186,900 / 5 تا` و shorthand `458` برای ربع تاریخ
پایین به‌ترتیب به `186900` و `45800` در واحد پروژه نرمال می‌شوند. `/` هرگز
به‌عنوان جداکنندهٔ price و quantity با هم ادغام نمی‌شود.

settlement بدون marker به‌صورت `TOMORROW` می‌ماند؛ silence هرگز `CASH` نیست.
`نقدی`/`نقد`/`امروز`/`حاضر` CASH فیزیکال هستند. کاغذی/حواله/غیررسمی و سه
variant normal/reverse/swim صریح می‌مانند.

## کالای بدون نام

قاعدهٔ UX پروژه این است که کالای omitted *می‌تواند* امام باشد، اما parser
گروه اجازه ندارد این را بدون ارزیابی بازار به عنوان یک حقیقت بازار ثبت کند.
بنابراین آفر بی‌نام به شکل زیر ذخیره می‌شود:

```text
instrument = COIN_UNRESOLVED
quality_state = PENDING_REVIEW
commodity_resolution = UNRESOLVED
```

چنین rowی در snapshot/range/training وارد نمی‌شود. P2-C-B/P5 فقط با
Snapshot strictly-prior، price range و policy conflict می‌تواند آن را به
کالای canonical تبدیل کند. حتی نام explicit هم در P2-C-B با مدل بازار
سنجیده خواهد شد تا typo یا outlier وارد مدل نشود.

## ماندۀ P2-C

P2-C-B باید raw محدود به سه روز را خارج از checkout نگه دارد، event یک‌تایی
و چندتایی را idempotent split کند، offer/request/reply-chain را وصل کند،
قیمت توافقی متفاوت از آفر اصلی و partial fill را ثبت کند، و فقط معاملهٔ
قطعاً تأییدشده را به `TRADE` model-eligible تبدیل کند. این commit هیچ‌یک از
این تصمیم‌ها را حدس نمی‌زند.
