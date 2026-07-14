# Stage 0 Challenge Resolution - بخش «رصد بازار»

## وضعیت سند

- تاریخ شروع: `2026-07-14`
- شاخه: `candidate/admin-market-monitoring`
- Roadmap مرجع: `docs/ADMIN_MARKET_MONITORING_ROADMAP_20260714.md`
- Stage: `0 - رفع چالش‌ها و بستن قراردادها`
- وضعیت کلی: `IN_PROGRESS`
- وضعیت `AMM-C01`: `PROPOSED_FOR_PRODUCT_APPROVAL`
- کدنویسی feature و migration تا بسته‌شدن P0ها مجاز نیست.

این سند تصمیم‌های Stage 0 را به‌ترتیب challenge ثبت می‌کند. هر تصمیم ابتدا با وضعیت `PROPOSED_FOR_PRODUCT_APPROVAL` نوشته می‌شود و فقط پس از تأیید مالک محصول به `DECIDED` تغییر می‌کند. پس از `DECIDED` شدن، ردیف متناظر در Roadmap نیز به‌روزرسانی خواهد شد.

## قواعد ثبت تصمیم

برای هر challenge باید این موارد وجود داشته باشد:

1. پرسش دقیق محصول یا معماری
2. واقعیت فعلی کد
3. گزینه‌های قابل انتخاب
4. تصمیم پیشنهادی
5. سناریوهای مثبت، منفی، همزمانی و outage
6. معیار پذیرش قابل تست
7. dependency و stop condition
8. تأیید یا اصلاح مالک محصول

---

## AMM-C01 - قرارداد دقیق feed بدون فیلتر

### وضعیت

`PROPOSED_FOR_PRODUCT_APPROVAL`

### پرسش

عبارت «همان آفرهای بازار» در صفحه `رصد بازار` دقیقاً به چه مجموعه‌ای از آفرهای active و terminal، با چه ترتیب، pagination و رفتار Realtime اشاره می‌کند؟

### واقعیت فعلی بازار

در baseline فعلی، صفحه عمومی بازار یک feed مرکب می‌سازد:

- فیلتر پیش‌فرض نوع آفر `همه` است.
- فیلتر پیش‌فرض نوع تسویه `همه تسویه‌ها` است.
- آفرهای active از `GET /api/offers/` دریافت می‌شوند.
- endpoint active فقط `Offer.status=ACTIVE` را با ترتیب `created_at DESC` برمی‌گرداند.
- اندازه پیش‌فرض پنجره active برابر ۵۰ ردیف است و UI فعلی برای active دکمه load-more ندارد.
- client آفر activeای را که `expires_at_ts` آن گذشته باشد از لیست active حذف می‌کند.
- برای کاربر غیرمشتری و غیرحسابدار، تاریخچه ۴۸ ساعت اخیر نیز از `GET /api/offers/market-history` دریافت می‌شود.
- صفحه اول history شامل ۲۵ ردیف است و history امکان load-more دارد.
- history شامل آفرهای معامله‌شده، منقضی/لغوشده قابل نمایش و stale-activeهای عبورکرده از زمان انقضا است.
- ترتیب history بر اساس زمان رخداد terminal و سپس زمان ساخت، هر دو نزولی است.
- UI ابتدا block آفرهای active و سپس block history را قرار می‌دهد؛ این دو block به‌صورت global دوباره sort نمی‌شوند.
- اگر یک شناسه همزمان در active و history وجود داشته باشد، نسخه history آن دوباره نمایش داده نمی‌شود.
- رخداد create باعث refresh feed active می‌شود.
- رخداد update وضعیت active را اصلاح می‌کند.
- رخداد expired/completed آفر را از active خارج و history را refresh می‌کند.
- کارت history read-only است و برچسب `منقضی` یا `معامله‌شده` دارد.

شواهد اصلی baseline:

- `frontend/src/views/MarketView.vue`
- `frontend/src/composables/useOffers.ts`
- `frontend/src/components/OffersList.vue`
- `api/routers/offers.py`
- `frontend/src/views/MarketView.test.ts`
- `frontend/src/composables/useOffers.test.ts`

### گزینه‌ها

#### گزینه A - فقط آفرهای active

در `رصد بازار` فقط آفرهای فعال نمایش داده شوند و history صرفاً داخل پروفایل کاربر قابل مشاهده باشد.

مزیت:

- ساده‌ترین و سبک‌ترین صفحه مدیریتی

ایراد:

- با نمای پیش‌فرض فعلی بازار یکسان نیست.
- آفر بلافاصله پس از معامله یا انقضا از context مدیر ناپدید می‌شود.
- مدیر برای هشدار درباره آفر تازه منقضی‌شده مجبور به جست‌وجوی مجدد می‌شود.

نتیجه پیشنهادی: `REJECT`

#### گزینه B - feed مرکب مشابه نمای پیش‌فرض بازار

`رصد بازار` همان active block و history block فعلی را بدون کنترل فیلتر نمایش دهد.

مزیت:

- با مدل ذهنی «عین آفرهای بازار» هم‌راستا است.
- context آفر پس از معامله یا انقضا باقی می‌ماند.
- هیچ feed یا ranking مدیریتی جداگانه ساخته نمی‌شود.

ایراد:

- قرارداد pagination فعلی active فقط ۵۰ ردیف را پوشش می‌دهد.
- باید identity به هر دو نوع کارت active و history اضافه شود.

نتیجه پیشنهادی: `ACCEPT`

#### گزینه C - feed مدیریتی مستقل با sort یا pagination متفاوت

مدیر همه آفرها را با ترتیب یا اندازه صفحه مستقل از بازار عمومی ببیند.

مزیت:

- آزادی بیشتر برای طراحی مدیریتی

ایراد:

- اصل parity را می‌شکند.
- ممکن است مدیر آفرهایی را ببیند که در پنجره قابل دسترس بازار عمومی نیستند.
- دوباره یک منبع رفتار جدا برای بازار ایجاد می‌کند.

نتیجه پیشنهادی: `REJECT`

### تصمیم پیشنهادی C01

`رصد بازار` آینه نمای پیش‌فرض بازار عمومی برای یک مدیر است، با یک تفاوت مجاز: context هویتی و اقدامات مدیریتی.

قرارداد دقیق پیشنهادی:

1. صفحه هیچ filter chip، search، sort selector یا فیلتر نقش/کاربر ندارد.
2. scope ثابت صفحه معادل `همه آفرها + همه تسویه‌ها` است.
3. feed از دو block تشکیل می‌شود:
   - ابتدا active offers
   - سپس terminal history قابل نمایش در ۴۸ ساعت اخیر
4. active block با `created_at DESC` مرتب می‌شود.
5. history block با `history_event_at DESC` و سپس `created_at DESC` مرتب می‌شود.
6. active و history با هم global sort نمی‌شوند؛ ترتیب blockها مانند بازار فعلی محفوظ می‌ماند.
7. terminal rowها read-only هستند، اما مدیر می‌تواند از همان row وارد context کاربر یا اقدام هشدار شود.
8. دکمه‌ها و interaction معامله در `رصد بازار` وجود ندارند؛ یکسانی مربوط به محتوا، ترتیب و وضعیت کارت است، نه امکان معامله.
9. قیمت، مقدار، مانده، نوع تسویه، کالا، لات‌ها، توضیح، timer و status stamp باید دقیقاً از همان public-card projection بازار استفاده کنند.
10. raw price یا فیلد تجاری اضافه فقط به دلیل admin بودن در کارت نمایش داده نمی‌شود.
11. نوار هویت و actionهای مدیریتی بعد از projection عمومی کارت اضافه می‌شوند و نباید مقادیر عمومی کارت را تغییر دهند.
12. مقایسه parity بر اساس ترتیب `offer_public_id`ها و محتوای عمومی کارت انجام می‌شود؛ identity band در مقایسه نادیده گرفته می‌شود.

### قرارداد pagination

برای جلوگیری از رانت اطلاعاتی، `رصد بازار` نباید پنجره بزرگ‌تری از بازار عمومی داشته باشد.

قرارداد baseline:

- active initial window: حداکثر ۵۰ ردیف، مانند بازار فعلی
- active load-more: در baseline فعلی وجود ندارد
- history initial window: ۲۵ ردیف
- history load-more: مجاز و مطابق بازار عمومی

قاعده آینده:

- اگر pagination آفرهای active در `main` تغییر کرد، بازار عمومی و `رصد بازار` باید همزمان همان قرارداد مشترک را مصرف کنند.
- توسعه مستقل active pagination فقط برای admin ممنوع است.
- وجود بیش از ۵۰ active offer در staging یک parity stop condition است: تا وقتی بازار عمومی مسیر دسترسی به ردیف‌های بعدی ندارد، `رصد بازار` نیز نباید آن‌ها را به‌صورت admin-only نمایش دهد و release این قابلیت باید متوقف بماند.

### قرارداد transition و Realtime

#### ایجاد آفر

- create event باعث refresh authoritative active feed می‌شود.
- آفر جدید بر اساس ترتیب backend در active block قرار می‌گیرد.
- event عمومی همچنان بدون هویت باقی می‌ماند؛ identity از read model مجاز admin تأمین می‌شود.

#### به‌روزرسانی مقدار یا قیمت

- row active بدون تغییر جایگاه غیرضروری patch/refresh می‌شود.
- محتوای عمومی کارت در بازار و `رصد بازار` باید برابر بماند.

#### معامله، انقضا یا لغو

- row از active block حذف می‌شود.
- history به‌صورت silent refresh می‌شود.
- در صورت قرارگرفتن در قرارداد history، همان `offer_public_id` در history block read-only ظاهر می‌شود.
- هیچ لحظه‌ای نباید یک آفر دوبار در feed ترکیبی نمایش داده شود.

#### خطای موقت شبکه یا sync lag

- داده موجود به‌دلیل خطای transient پاک نمی‌شود.
- refresh بعدی باید همگرایی را برقرار کند.
- صفحه نباید با payload Realtime عمومیِ بدون هویت، identity قبلی را null یا اشتباه کند.
- اگر parity میان public و admin feed در staging قابل اثبات نباشد، release متوقف می‌شود.

### سناریوهای منفی

- `رصد بازار` نباید فقط به‌خاطر admin بودن، آفر archived یا خارج از history window را نشان دهد.
- نباید آفر اولیه‌ای که هرگز به بازار منتشر نشده است در history مدیریتی بازار ظاهر شود.
- نباید آفر tier/customer-specific خارج از قرارداد بازار عمومی به feed اضافه شود.
- نباید به دلیل نبود identity، کل آفر از feed حذف شود؛ row عمومی باقی می‌ماند و identity حالت کنترل‌شده `نامشخص/حذف‌شده` می‌گیرد که قرارداد دقیق آن در `AMM-C02` بسته می‌شود.
- نباید فیلترهای مخفی URL یا query باعث تفاوت feed مدیران مختلف شوند.

### معیارهای پذیرش قابل تست

1. در snapshot یکسان، ترتیب `offer_public_id`های public default feed و admin feed برابر باشد.
2. active rows پیش از history rows قرار بگیرند.
3. active rows بر اساس `created_at DESC` باشند.
4. history rows بر اساس `history_event_at DESC, created_at DESC` باشند.
5. یک شناسه در feed ترکیبی تکرار نشود.
6. create event هر دو surface را به مجموعه یکسان همگرا کند.
7. completed/expired event آفر را از active به history منتقل کند.
8. terminal row در `رصد بازار` هیچ کنترل معامله‌ای نداشته باشد.
9. active row در `رصد بازار` نیز هیچ کنترل معامله‌ای نداشته باشد.
10. هیچ filter chip یا sort selector در صفحه وجود نداشته باشد.
11. داده عمومی کارت در دو صفحه برابر و فقط identity/action area اضافه باشد.
12. non-admin نتواند endpoint یا projection دارای identity را دریافت کند.
13. در بیش از ۵۰ active row، تست staging stop condition را فعال کند مگر اینکه public pagination مشترک قبلاً وارد `main` شده باشد.
14. failure موقت refresh، آخرین feed سالم را پاک نکند.

### dependencyها

- `AMM-C02`: قرارداد صاحب آفر و ثبت‌کننده
- `AMM-C03`: read model و Realtime بدون نشت identity
- `AMM-C11`: مرز componentهای کارت عمومی و shell مدیریتی
- `AMM-C12`: query و pagination سابقه
- `AMM-C13`: Realtime مدیریتی و sync apply
- `AMM-C18`: ماتریس parity دو سرور و دو surface

### stop conditionهای C01

- admin feed آفر اضافه یا کم نسبت به public default feed داشته باشد.
- ترتیب blockها یا rowها بین دو صفحه متفاوت شود.
- admin feed برای active pagination از قراردادی بزرگ‌تر از public market استفاده کند.
- identity از event یا endpoint عمومی بازار قابل استخراج شود.
- terminal row امکان معامله داشته باشد.
- تغییر یک کارت عمومی در یک صفحه اعمال و در صفحه دیگر اعمال نشود.

### تأیید مالک محصول

- وضعیت: `PENDING`
- تصمیم ثبت‌شده: هنوز تأیید نشده است.
- پس از تأیید:
  - وضعیت C01 در این سند به `DECIDED` تغییر می‌کند.
  - ردیف `AMM-C01` در Roadmap به `DECIDED` تغییر می‌کند.
  - تحلیل `AMM-C02` آغاز می‌شود.

---

## صف ادامه Stage 0

پس از تأیید C01، challenge بعدی `AMM-C02` است: تعیین دقیق تفاوت «صاحب آفر» و «ثبت‌کننده آفر» در سناریوهای مستقیم، حسابدار و مشتری.
