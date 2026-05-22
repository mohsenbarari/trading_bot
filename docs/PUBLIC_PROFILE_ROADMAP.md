# سند راهبردی پروفایل عمومی کاربر

وضعیت: draft اولیه پس از بسته شدن تصمیم های محصولی این مرحله

هدف این سند بستن scope، seamهای واقعی موجود در کد، dependencyها، و سوال های باز برای توسعه بخش «پروفایل عمومی کاربر» است. این فایل عمداً roadmap اجرایی است، نه تحلیل کلی محصول.

راهنمای وضعیت:
- `[x]` موردی که از روی تصمیم شما و وضعیت فعلی کد برای من روشن شده است
- `[ ]` موردی که challenge یا decision blocker است و باید قبل از اجرا بسته شود

## 1. خواسته های قطعی این مرحله

- [x] هیچ کاربر عادی نباید از `role` خود یا نقش دیگر کاربران از طریق surface عمومی مطلع شود.
- [x] اگر جایی role برای ادمین لازم است، باید از surface privileged بیاید، نه از contract عمومی `users-public`.
- [x] نمایش `آنلاین / آخرین بازدید` برای تمام کاربران در پروفایل عمومی مفید است و باید اضافه شود.
- [x] نمایش `تعداد معاملات` در پروفایل عمومی لازم نیست، چون history در دسترس است.
- [x] UX بلاک باید بهتر شود، اما کاربر بلاک شده نباید از بلاک شدن خود مطلع شود.
- [x] بلاک فقط برای market است و نباید در هیچ بخش دیگری از پروژه محدودیت ایجاد کند.
- [x] در تاریخچه معاملاتِ پروفایل، کاربر باید بتواند بر اساس بازه زمانی query بزند.
- [x] در تاریخچه معاملاتِ پروفایل، کاربر باید بتواند بر اساس کالا query بزند.
- [x] از همان بخش history باید خروجی `PDF` و `Excel` با presetهای `1 ماهه`، `3 ماهه`، `6 ماهه`، `1 ساله` و `بازه دلخواه` قابل دانلود باشد.
- [x] توسعه `police` در این مرحله out-of-scope است.
- [x] آدرس و شماره تماس کاربر و حسابداران کاربر باید برای تمام کسانی که به پروفایل او دسترسی دارند عمومی بماند.
- [x] در پروفایل یک کاربر دیگر، export فقط باید `تاریخچه مشترک` را دانلود کند.
- [x] filter کالا در این فاز `single-select` است و باید بتواند همزمان با filter تاریخی اعمال شود.
- [x] قبل از finalize کردن export، نمونه فایل `PDF` و `Excel` باید review شود.

## 2. توضیح شفاف ایده ای که فعلاً در scope نیست

- [x] منظور از `یادداشت خصوصی / alias` این بود که هر کاربر بتواند برای یک کاربر دیگر یک نام داخلیِ فقط-برای-خودش ثبت کند؛ چیزی شبیه `"علی صراف بازار"` یا `"مشتری قدیمی"` که فقط برای همان viewer دیده شود.
- [x] این قابلیت در کد فعلی وجود ندارد و در این مرحله هم جزو scope اجرایی نیست.

## 3. واقعیت فعلی کد که روی roadmap اثر می گذارد

### 3.1 contract عمومی فعلی backend

- [x] `schemas.UserPublicRead` در حال حاضر این فیلدها را به surface عمومی می دهد: `role`, `mobile_number`, `address`, `created_at`, `trades_count`, `last_seen_at` و contextهای accountant/customer.
- [x] در نتیجه، خودِ backend public contract الان بیش از نیاز این مرحله data می دهد.
- [x] اگر هدف این است که role از هیچ surface عمومی نشت نکند، حذف آن باید از خود schema و serialization `users_public` شروع شود، نه فقط hide در frontend.
- [x] برخلاف `role`، `mobile_number` و `address` جزو داده های مجاز این surface باقی می مانند.

### 3.2 وضعیت فعلی PublicProfile frontend

- [x] `frontend/src/components/PublicProfile.vue` الان از data عمومی فقط این ها را واقعاً نشان می دهد: `account_name`, `mobile_number`, `address`, `created_at_jalali`, history و contextهای accountant/customer.
- [x] `role` با این که از backend می آید، در خود PublicProfile مصرف نمی شود.
- [x] `last_seen_at` با این که از backend می آید، در خود PublicProfile مصرف نمی شود.
- [x] `trades_count` با این که از backend می آید، در خود PublicProfile مصرف نمی شود.
- [x] در نتیجه، حذف `role` از contract عمومی ریسک frontend محدودی در خود PublicProfile دارد، اما باید search/profile consumers دیگر هم audit شوند.

### 3.3 seam آماده برای online / last seen

- [x] منطق online detection همین الان در `frontend/src/components/chat/ChatConversationList.vue` وجود دارد.
- [x] threshold فعلی آن `3 دقیقه` است.
- [x] بهترین اجرای این feature در پروفایل عمومی reuse یا extract همین helper است، نه نوشتن منطق دوم و ناسازگار.

### 3.4 seam آماده برای بلاک

- [x] backend block API همین الان surfaceهای لازم را دارد:
  - `GET /api/blocks/status`
  - `GET /api/blocks/check/{user_id}`
  - `POST /api/blocks/{user_id}`
  - `DELETE /api/blocks/{user_id}`
- [x] PublicProfile همین الان از `check/post/delete` استفاده می کند، اما UX آن هنوز capability-aware نیست.
- [x] `GET /api/blocks/status` seam خوبی برای disable reason، remaining capacity، و guard بهتر UI است.

### 3.5 وضعیت فعلی عدم افشای بلاک در market

- [x] در `api/routers/trades.py` برای مسیر اجرای معامله، comment صریحاً می گوید بلاک باید پنهان بماند.
- [x] در همان path، پیام خطا الان generic است: `"امکان انجام این معامله وجود ندارد."`
- [x] با این حال باید کل blocked UX در market audit شود تا هیچ نشانه غیرمستقیم قابل اتکایی برای کاربر بلاک شده باقی نماند.
- [x] chat و سایر surfaceها نباید تحت تأثیر policy بلاک بازار قرار بگیرند.

### 3.6 وضعیت فعلی history و export

- [x] PublicProfile history الان فقط read path دارد و از `GET /api/trades/my` یا `GET /api/trades/with/{user_id}` استفاده می کند.
- [x] filter زمانی/کالایی در web public profile فعلاً وجود ندارد.
- [x] export web برای history فعلاً وجود ندارد.
- [x] bot trade history همین الان presetهای زمانی `1/3/6 ماه` و export `Excel/PDF` دارد.
- [x] `bot/handlers/trade_history.py` seam واضحی برای reuse یا extraction منطق export فراهم می کند.
- [x] `generate_pdf()` در bot فعلاً placeholder ساده است و برای web-grade export کافی نیست.

## 4. تصمیم های طراحی که باید مستقیم وارد اجرا شوند

### 4.1 حریم نقش ها

- [x] `role` باید از public response contract حذف شود.
- [x] اگر بخشی از frontend ادمینی به نقش نیاز دارد، باید از endpointهای privileged مثل `/api/users/{id}` بگیرد، نه از `/api/users-public/{id}`.
- [x] این تغییر باید همراه با audit search result consumers انجام شود تا هیچ consumer پنهانی به `UserPublicRead.role` وابسته نماند.

### 4.2 presence در پروفایل

- [x] `online / last seen` باید نزدیک هدر پروفایل و کنار identity اصلی کاربر نمایش داده شود، نه پایین صفحه.
- [x] از آنجا که این اطلاعات dynamic و lightweight است، نمایش آن در hero/profile header مناسب ترین مکان است.
- [x] برای null/ناموجود بودن `last_seen_at` باید fallback بی صدا وجود داشته باشد و UI چیزی حدسی نشان ندهد.

### 4.3 نمایش history

- [x] history باید همان contract فعلی perspective را حفظ کند:
  - self profile: تاریخچه خود کاربر
  - ordinary viewer on another ordinary profile: تاریخچه مشترک
  - special existing flows مثل customer/super-admin: contract فعلی حفظ شود
- [x] filterهای جدید باید روی همان perspective موجود اعمال شوند، نه این که semantics history را عوض کنند.
- [x] filter زمانی و filter کالا باید بتوانند همزمان روی همان history query اعمال شوند.
- [x] filter کالا در این فاز single-select است.

### 4.4 export

- [x] export باید هم preset زمانی داشته باشد و هم custom range.
- [x] export باید دقیقاً از همان query/filter جاری UI تغذیه کند تا mismatch بین list و file پیش نیاید.
- [x] export `Excel` seam آماده تری برای reuse دارد.
- [x] export `PDF` نیاز به implementation واقعی دارد و نباید روی placeholder bot تکیه کند.
- [x] ordinary viewer در پروفایل کاربر دیگر فقط باید `تاریخچه مشترک` را export کند.
- [x] در `تاریخچه مشترک` پروفایل یک کاربر دیگر، counterparty باید با context صفحه سازگار باشد؛ یعنی اگر محسن در پروفایل علی است، export فقط معاملات `محسن ↔ علی` را نشان می دهد و نباید نام طرف های متفاوت در ردیف ها دیده شود.
- [x] header خروجی `mutual history` برای ordinary viewer باید فقط `نام target` و `بازه زمانی` را نشان دهد و نباید viewer، نوع history، یا توضیح test-style اضافی را وارد فایل کند.
- [x] جدول `mutual history export` باید ستون `نوع معامله` را از perspective بیننده همان صفحه نشان دهد؛ یعنی همان معامله برای محسن می تواند `فروش` باشد و برای علی `خرید`.
- [x] در خروجی mutual history ordinary viewer ستون های `role`، `counterparty` و `path` نباید نمایش داده شوند.
- [x] review نمونه فایل های `PDF` و `Excel` بخشی از این فاز است.

## 5. roadmap اجرایی پیشنهادی

### فاز 1: hardening قرارداد عمومی پروفایل

- [ ] حذف `role` از `schemas.UserPublicRead`.
- [ ] به روزرسانی `_serialize_public_user()` و تمام callsiteهای `users_public` برای contract جدید.
- [ ] audit کردن consumerهای frontend که از `UserPublicRead` استفاده می کنند تا جایی به `role` متکی نباشند.
- [ ] افزودن unittest و Vitest برای lock کردن حذف role از surface عمومی.
- [x] `mobile_number` و `address` در این فاز باید public بمانند.

### فاز 2: اضافه کردن online / last seen به PublicProfile

- [ ] extract کردن helper مشترک برای presence از `ChatConversationList.vue` به utility/composable مشترک.
- [ ] افزودن rendering `آنلاین` یا `آخرین بازدید ...` به hero پروفایل.
- [ ] اطمینان از یکسان بودن threshold و formatting با messenger.
- [ ] افزودن Vitest برای presence states:
  - online
  - offline with timestamp
  - no timestamp

### فاز 3: block UX hardening بدون افشای بلاک

- [ ] استفاده از `GET /api/blocks/status` برای capability-aware UI در پروفایل عمومی.
- [ ] نمایش disable state مناسب برای blocker وقتی سقف بلاک پر شده یا capability خاموش است.
- [ ] audit کردن pathهای market/offer interaction برای هر message/detail ای که بتواند به blocked user سرنخ قابل اتکا بدهد.
- [ ] استانداردسازی copyهای generic در pathهای تعاملی blocked داخل market.
- [ ] اطمینان از این که state محلی PublicProfile فقط برای blocker روشن است و هیچ event/payload عمومی درباره بلاک شدن تولید نمی شود.
- [ ] اطمینان از این که block هیچ محدودیتی روی chat و سایر surfaceها اعمال نمی کند.

### فاز 4: queryable history در پروفایل عمومی

- [ ] افزودن query params استاندارد به read pathهای trade history برای:
  - `from_date`
  - `to_date`
  - `commodity_id` یا `commodity_query`
- [ ] افزودن filter bar سبک به accordion history در PublicProfile.
- [ ] presetهای زمانی در UI:
  - `1 ماهه`
  - `3 ماهه`
  - `6 ماهه`
  - `1 ساله`
  - `بازه دلخواه`
- [ ] picker بازه زمانی باید با نمایش فارسی/Jalali هم راستا باشد ولی query نهایی باید unambiguous و server-safe بماند.
- [ ] filter کالا باید به شکل lightweight single-select search/select طراحی شود، نه dropdown سنگین و ثابت.
- [ ] query builder frontend باید بتواند همزمان بازه زمانی و کالای انتخاب شده را در یک درخواست history اعمال کند.

### فاز 5: export history برای web public profile

- [ ] استخراج منطق `Excel` history از `bot/handlers/trade_history.py` به service مشترک backend.
- [ ] طراحی service export جدید برای `PDF` به جای placeholder فعلی bot.
- [ ] افزودن endpointهای export برای same-perspective history:
  - self history export
  - mutual history export
  - existing privileged target-user history export
- [ ] هماهنگ کردن naming فایل، timezone، و labelها با context فعلی history.
- [ ] اطمینان از این که export از همان filter state جاری UI استفاده می کند.
- [ ] ساخت و review کردن نمونه اولیه فایل `Excel` و `PDF` قبل از finalize کردن endpoint و UX export.
- [ ] برای `mutual history export` ordinary viewer، summary/header باید minimal بماند: فقط `نام target` و `بازه زمانی`. جدول هم فقط `نوع معامله` perspective-based را نگه دارد و هیچ ستون `role/counterparty/path` نداشته باشد.

### فاز 6: تست و non-regression

- [ ] backend:
  - users_public contract tests
  - trade history filter tests
  - export permission/filter tests
  - blocked-path generic-message tests
- [ ] frontend unit:
  - PublicProfile presence
  - PublicProfile filter state
  - PublicProfile export action state
  - block UX disable/loading/success states
- [ ] Playwright:
  - public profile presence rendering
  - mutual history filtering by date and commodity
  - self history export presets
  - custom range export
  - blocked interaction generic UX

## 6. سوال ها و ابهام های باز قبل از شروع اجرا

- [ ] برای نمونه اولیه `PDF` و `Excel`، آیا تمایل دارید فایل فقط جدول را نشان دهد یا یک summary header سبک هم داشته باشد؟
  پیشنهاد فعلی: summary سبک شامل نوع history، بازه، کالای فیلترشده و تعداد ردیف ها.

- [ ] export PDF باید فقط table ساده باشد یا summary هم داشته باشد؟
  پیشنهاد فعلی: در فاز اول فقط table filtered rows + metadata بازه/کالا.

- [ ] وقتی viewer خودش target را بلاک کرده، آیا `ارسال پیام` باید همچنان visible بماند یا باید با UX دیگری replace شود؟
  پیشنهاد فعلی: visible بماند، چون block فقط market-only است و نباید به chat محدودیت بدهد.

## 7. ایده های مفید ولی خارج از scope همین مرحله

- [x] `یادداشت خصوصی / alias` برای هر viewer روی هر user.
- [x] tab یا sheet برای `رسانه ها / فایل های مشترک` با همین کاربر.
- [x] summary سبک اعتماد معاملاتی مثل `آخرین معامله` یا `سطح فعالیت` بدون رفتن به سمت rating system سنگین.
- [x] هرگونه توسعه مرتبط با `police` خارج از scope این roadmap است.
