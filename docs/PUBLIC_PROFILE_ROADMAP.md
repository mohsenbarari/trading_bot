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
- [x] هر کاربر باید در پروفایل خودش بتواند لیست کاربران پروژه را ببیند.
- [x] حسابداران فعال همان کاربر هم باید روی پروفایل resolved owner به همین لیست دسترسی داشته باشند.
- [x] مشتریان در هر دو سطح نباید به لیست کاربران پروژه دسترسی داشته باشند.
- [x] این لیست فقط شامل کاربران پروژه با نقش های `عادی`، `مدیر میانی` و `مدیر ارشد` است؛ حسابداران و مشتریان در آن جایی ندارند.
- [x] داده هر ردیف در این لیست فقط `نام کاربر` و `شماره تماس` است و کلیک روی نام باید viewer را به پروفایل عمومی همان کاربر ببرد.

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

### 3.7 وضعیت فعلی لیست کاربران پروژه

- [x] route آماده ای برای «لیست کاربران پروژه» با permission و contract مورد نیاز این feature وجود ندارد.
- [x] `GET /api/users/` در `api/routers/users.py` admin-only است و contract آن برای این surface بیش از حد privileged و پرحجم است.
- [x] `GET /api/users-public/search` در `api/routers/users_public.py` هم مناسب این نیاز نیست، چون search-based است، از contract عمومی سنگین استفاده می کند، و accountant/customer contextها را هم resolve می کند.
- [x] `PublicProfile.vue` الان owner-only action surface دارد، اما gate فعلی آن (`showOwnerSections`) باعث می شود accountant ای که owner profile را می بیند به owner-only sectionها دسترسی نداشته باشد.
- [x] در این پروژه حسابدار/مشتری بودن از relationهای فعال می آید، نه فقط از role؛ بنابراین فیلتر «فقط کاربران پروژه» را نمی توان صرفاً با role پیاده کرد.

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

### 4.5 لیست کاربران پروژه در پروفایل خود/مالک

- [x] این feature باید backend route اختصاصی و contract اختصاصی خودش را داشته باشد و نباید روی `GET /api/users/` یا `GET /api/users-public/search` سوار شود.
- [x] دسترسی به این لیست فقط در self profile و owner-resolved profile برای حسابداران فعال همان owner مجاز است.
- [x] customerها در هر دو سطح به این لیست دسترسی ندارند، حتی اگر profile target همان owner آن ها باشد.
- [x] فیلتر اعضای این لیست باید هم role-based باشد (`عادی`، `مدیر میانی`، `مدیر ارشد`) و هم relation-based تا هر userی که accountant/customer فعال است حذف شود.
- [x] row data باید عمداً minimal بماند: فقط نام قابل نمایش و شماره تماس، به همراه شناسه لازم برای navigation به public profile.
- [x] این feature باید از public-profile navigation contract موجود استفاده کند و وارد surfaceهای admin management نشود.

## 5. roadmap اجرایی پیشنهادی

### فاز 1: hardening قرارداد عمومی پروفایل

- [x] حذف `role` از `schemas.UserPublicRead`.
- [x] به روزرسانی `_serialize_public_user()` و تمام callsiteهای `users_public` برای contract جدید.
- [x] audit کردن consumerهای frontend که از `UserPublicRead` استفاده می کنند تا جایی به `role` متکی نباشند.
- [x] افزودن unittest و Vitest برای lock کردن حذف role از surface عمومی.
- [x] `mobile_number` و `address` در این فاز باید public بمانند.

### فاز 1.5: لیست کاربران پروژه در پروفایل خود / مالک

- [x] طراحی route و schema اختصاصی برای project users directory در surface عمومی.
- [x] enforce کردن policy دسترسی برای self profile و accountant-resolved owner profile.
- [x] اعمال فیلتر role + relation برای حذف حسابداران و مشتریان از این list.
- [x] افزودن section یا accordion سبک در `PublicProfile.vue` برای نمایش این list.
- [x] wiring navigation هر row به public profile target بدون ورود به admin surface.
- [x] افزودن backend و frontend tests برای access-control، filtering، و navigation contract این list.

### فاز 2: اضافه کردن online / last seen به PublicProfile

- [x] extract کردن helper مشترک برای presence از `ChatConversationList.vue` به utility/composable مشترک.
- [x] افزودن rendering `آنلاین` یا `آخرین بازدید ...` به hero پروفایل.
- [x] اطمینان از یکسان بودن threshold و formatting با messenger.
- [x] افزودن Vitest برای presence states:
  - online
  - offline with timestamp
  - no timestamp

### فاز 3: block UX hardening بدون افشای بلاک

- [x] استفاده از `GET /api/blocks/status` برای capability-aware UI در پروفایل عمومی.
- [x] نمایش disable state مناسب برای blocker وقتی سقف بلاک پر شده یا capability خاموش است.
- [x] audit کردن pathهای market/offer interaction برای هر message/detail ای که بتواند به blocked user سرنخ قابل اتکا بدهد.
- [x] استانداردسازی و test-lock کردن copyهای generic در pathهای تعاملی blocked داخل market.
- [x] اطمینان از این که state محلی PublicProfile فقط برای blocker روشن است و هیچ event/payload عمومی درباره بلاک شدن تولید نمی شود.
- [x] اطمینان از این که block هیچ محدودیتی روی chat و سایر surfaceها اعمال نمی کند.

### فاز 4: queryable history در پروفایل عمومی

- [x] افزودن query params استاندارد به read pathهای trade history برای:
  - `from_date`
  - `to_date`
  - `commodity_id` یا `commodity_query`
- [x] افزودن filter bar سبک به accordion history در PublicProfile.
- [x] presetهای زمانی در UI:
  - `1 ماهه`
  - `3 ماهه`
  - `6 ماهه`
  - `1 ساله`
  - `بازه دلخواه`
- [x] picker بازه زمانی باید با نمایش فارسی/Jalali هم راستا باشد ولی query نهایی باید unambiguous و server-safe بماند.
- [x] filter کالا باید به شکل lightweight single-select search/select طراحی شود، نه dropdown سنگین و ثابت.
- [x] query builder frontend باید بتواند همزمان بازه زمانی و کالای انتخاب شده را در یک درخواست history اعمال کند.

### فاز 5: export history برای web public profile

- [x] ایجاد service export backend برای `Excel/PDF` history در web public profile.
- [x] طراحی service export جدید برای `PDF` به جای placeholder فعلی bot.
- [x] افزودن endpointهای export برای same-perspective history:
  - self history export
  - mutual history export
  - existing privileged target-user history export
- [x] هماهنگ کردن naming فایل، timezone، و labelها با context فعلی history.
- [x] اطمینان از این که export از همان filter state جاری UI استفاده می کند.
- [x] ساخت و review کردن نمونه اولیه فایل `Excel` و `PDF` قبل از finalize کردن endpoint و UX export.
- [x] contract نهایی export در همین scope به صورت minimal و production-like قفل می شود؛ summary جداگانه، metadata اضافی، و framingهایی مثل `history type / viewer role / trade path` خارج از scope همین فاز هستند.
- [x] برای `self history export`، header باید فقط `نام خود کاربر` و `بازه زمانی` را نشان دهد. جدول هم `نوع معامله` را فقط از perspective خود همان کاربر نشان دهد و هیچ ستون `role/counterparty/path` نداشته باشد.
- [x] برای `mutual history export` ordinary viewer، header باید فقط `نام target` و `بازه زمانی` را نشان دهد. جدول هم فقط `نوع معامله` perspective-based را نگه دارد و هیچ ستون `role/counterparty/path` نداشته باشد.
- [x] برای `existing privileged target-user history export`، header باید فقط `نام target` و `بازه زمانی` را نشان دهد. جدول هم `نوع معامله` را از perspective خود target نشان دهد و هیچ ستون `role/counterparty/path` نداشته باشد.
- [x] اگر filter بازه یا کالا روی UI فعال باشد، همان filter فقط scope ردیف های export را تعیین می کند و نیازی به summary اضافی در header ایجاد نمی کند.

### فاز 6: تست و non-regression

- [ ] backend:
  - users_public contract tests
  - [x] trade history filter tests
  - [x] export permission/filter tests
  - [x] blocked-path generic-message tests
- [ ] frontend unit:
  - PublicProfile presence
  - [x] PublicProfile filter state
  - [x] PublicProfile export action state
  - [x] block UX disable/loading/success states
- [ ] Playwright:
  - public profile presence rendering
  - mutual history filtering by date and commodity
  - self history export presets
  - custom range export
  - blocked interaction generic UX

## 6. سوال ها و ابهام های باقیمانده قبل از شروع اجرا

- [x] در scope فعلی public profile، ابهام product-critical برای export mutual/self/privileged history باقی نمانده است.
- [x] `PDF` و `Excel` هر دو با header minimal جلو می روند و summary اضافی در scope فاز اول اضافه نمی شود.
- [x] وقتی viewer خودش target را بلاک کرده، `ارسال پیام` همچنان visible می ماند، چون block فقط market-only است و نباید روی chat یا سایر surfaceها اثری بگذارد.

### 6.1 تصمیم های فنی پذیرفته شده برای شروع اجرا

- [x] `api/routers/users_public.py` برای search و profile به دو contract سبک تر و مجزا split می شود تا overfetch و data leakage کم شود.
- [x] `frontend/src/components/PublicProfile.vue` قبل از توسعه phaseهای history/export/status به `apiFetch` / `apiFetchJson` مهاجرت می کند.
- [x] presence با یک source of truth مشترک برای parse + online threshold + format پیاده می شود و consumerهای فعلی هم به همان seam migrate می شوند.
- [x] برای history/filter/export روی `api/routers/trades.py` mode صریح برای `mutual` در برابر `target` اضافه می شود و route overloaded فعلی مبنای نهایی contract نمی ماند.
- [x] perspective history و `نوع معامله` server-authoritative می شود تا list UI و export file از هم diverge نکنند.
- [x] UI Jalali فقط در picker باقی می ماند و request نهایی history/export با boundary صریح روز و به صورت ISO/Gregorian به backend می رود.
- [x] filter کالا در فاز اول با `commodity_query` سبک و suggestionهای commodity list جلو می رود و backend همچنان `commodity_id` را هم به صورت additive می پذیرد.
- [x] export همیشه full filtered query مستقل از pagination UI می زند و به limit/offset list وابسته نمی شود.
- [x] `GET /api/blocks/status` برای UX capability-aware با reason code machine-readable توسعه می یابد.
- [x] extraction از `bot/handlers/trade_history.py` در مرز query-to-document pipeline انجام می شود؛ bot/web فقط transport wrapper باقی می مانند.

### 6.2 تصمیم های نهایی برای لیست کاربران پروژه

- [x] `نام کاربر` در این list به صورت canonical همان `account_name` است.
- [x] row خود owner / viewer در dataset می ماند و navigation به همان public profile harmless تلقی می شود.
- [x] فاز اول این list با search سبک server-side و بدون pagination UI جلو می رود.

## 7. ایده های مفید ولی خارج از scope همین مرحله

- [x] `یادداشت خصوصی / alias` برای هر viewer روی هر user.
- [x] tab یا sheet برای `رسانه ها / فایل های مشترک` با همین کاربر.
- [x] summary سبک اعتماد معاملاتی مثل `آخرین معامله` یا `سطح فعالیت` بدون رفتن به سمت rating system سنگین.
- [x] هرگونه توسعه مرتبط با `police` خارج از scope این roadmap است.
