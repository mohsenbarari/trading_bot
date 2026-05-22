# Roadmap زمان‌بندی بازار و اعلان‌های شروع/پایان

این سند roadmap اختصاصی feature «باز/بسته بودن بازار بر اساس روز، ساعت، تعطیلی تقویمی، و overrideهای ادمین» است. تا قبل از این، برای این feature در `docs/` سند مستقلی وجود نداشت و اشاره‌های موجود فقط به gate شدن بازار در سناریوی `inactive user` محدود بودند، نه زمان‌بندی خود بازار.

هدف این roadmap این است که contract محصول، seamهای واقعی کد، transitionهای runtime، و surfaceهای UI/Telegram/Bot را قبل از implementation قفل کند.

## 0. Snapshot تصمیم‌های قطعی این فاز

- [x] بازار نباید 24/7 باز باشد و باید بر اساس ruleهای زمانی و روزی کنترل شود.
- [x] schedule پایه باید قابل تنظیم توسط سوپرادمین باشد.
- [x] مدیریت schedule و exceptionها فقط از طریق وب‌اپ انجام می‌شود و برای این feature نیازی به UI تنظیمات در بات تلگرام نیست.
- [x] تعطیلی‌های تقویمی و overrideهای خاص باید از schedule پایه جدا مدل شوند.
- [x] با تمام شدن تایم بازار، همه آفرهای `ACTIVE` باید هم در وب‌اپ و هم در تلگرام منقضی شوند.
- [x] با بسته شدن بازار، پیام «پایان فعالیت بازار» باید در کانال تلگرامی و در وب‌اپ منتشر شود.
- [x] با بسته شدن بازار، chatbox / input ثبت آفر در صفحه بازار وب باید غیرفعال شود.
- [x] اگر کاربر در بات هنگام بسته بودن بازار اقدام به ثبت آفر کرد، باید دقیقاً این پیام را بگیرد:
  - [x] `بعلت بسته بودن بازار درخواست شما ثبت نشد`
  - [x] `لطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید.`
- [x] با باز شدن بازار، پیام «پایان فعالیت بازار» در صفحه وب بازار باید حذف و پیام «شروع فعالیت بازار» منتشر شود.
- [x] با باز شدن بازار، chatbox / input ثبت آفر در صفحه وب بازار باید دوباره فعال شود.
- [x] پیام «شروع فعالیت بازار» در وب فقط یک reminder ساده است که از این لحظه امکان ثبت آفر و معامله دوباره برقرار شده است؛ این بخش نیازی به UX یا state اضافه‌ی نمایشی فراتر از حد لازم ندارد.
- [x] در کانال تلگرام، پیام قبلی «پایان فعالیت بازار» نیازی به حذف ندارد.
- [x] در کانال تلگرام، با باز شدن بازار باید پیام «شروع فعالیت بازار» منتشر شود.
- [x] با باز شدن بازار، امکان ثبت آفر در بات دوباره فعال شود.
- [x] در وب‌اپ، پیام «شروع فعالیت بازار» باید بعد از ثبت دومین آفر سراسری بازار توسط هر کاربری محو شود؛ بعد از آن خود آفرها برای فهم باز بودن بازار کافی هستند.
- [x] transitionهای باز/بسته شدن بازار باید idempotent باشند؛ یعنی loop یا restart نباید دوباره همان side effectها را تکرار کند.
- [x] پیام‌های start/end در وب نباید فقط local state باشند؛ باید طوری طراحی شوند که با refresh، reconnect، و open شدن market page در تب دیگر از بین نروند.

## 1. seamهای فعلی کد که این feature را درگیر می‌کنند

### 1.1. schedule و settings

- [x] `core/trading_settings.py` seam فعلی settings سراسری و Redis/DB-backed پروژه است.
- [x] `api/routers/trading_settings.py` surface فعلی read/update/reset تنظیمات سیستم است.
- [x] `bot/handlers/panel.py` surface فعلی بات برای تنظیمات سیستم وجود دارد، اما طبق تصمیم محصول برای این feature خارج از scope مدیریت schedule است و parity با وب لازم ندارد.

### 1.2. transitionهای خودکار و background loops

- [x] `core/offer_expiry.py` loop فعلی auto-expire آفرها را بر اساس `offer_expiry_minutes` انجام می‌دهد.
- [x] `main.py` همین حالا `offer_expiry_loop()` و loopهای background دیگر را در `lifespan` با `asyncio.create_task(...)` بالا می‌آورد.
- [x] پس market schedule transition loop seam طبیعی و هم‌سبک با loopهای موجود دارد و لازم نیست worker جدیدی برای MVP ساخته شود.

### 1.3. create / execute surfaces

- [x] `api/routers/offers.py` create-offer authority فعلی برای وب است.
- [x] `api/routers/trades.py` execute-trade authority فعلی برای اجرای معامله است.
- [x] `bot/handlers/trade_create.py` هم مسیر text offer و هم مسیر wizard/FSM ایجاد آفر در بات را نگه می‌دارد.

### 1.4. وب‌اپ بازار

- [x] `frontend/src/views/MarketView.vue` surface فعلی market page و text-offer chatbox را نگه می‌دارد.
- [x] `frontend/src/composables/useOffers.ts` / websocket offer runtime، offer list را با `offer:created` و `offer:expired` به‌روزرسانی می‌کند.
- [x] `frontend/src/components/TradingView.vue` نیز همچنان websocket-driven است و offer/trade runtime را مصرف می‌کند.

## 2. پیشنهاد مدل داده

### 2.1. schedule پایه در trading settings

- [x] schedule پایه در `core/trading_settings.py` بماند، نه در جدول exceptionها.
- [x] فیلدهای additive پیشنهادی برای schedule پایه:
  - [x] `market_schedule_enabled`
  - [x] `market_timezone` (پیش‌فرض: `Asia/Tehran`)
  - [x] `market_open_time_local`
  - [x] `market_close_time_local`
  - [x] `market_closed_weekdays`
- [x] برای MVP، یک window یکنواخت روزانه کافی است و per-weekday custom hours لازم نیست مگر product بعداً بخواهد.

### 2.2. exceptionها در جدول جدا

- [ ] تعطیلی‌های تقویمی و overrideهای خاص نباید داخل key-value فعلی فشرده شوند.
- [ ] یک جدول جدید مثل `market_schedule_overrides` لازم است.
- [ ] هر row حداقل این contract را داشته باشد:
  - [ ] `date`
  - [ ] `override_type` (`closed_all_day`, `open_all_day`, `custom_hours`)
  - [ ] `open_time_local` nullable
  - [ ] `close_time_local` nullable
  - [ ] `note`
  - [ ] `created_by_user_id`
  - [ ] `updated_at`
- [ ] این جدول باید مثل سایر entityهای اصلی وارد sync/change_log شود.

### 2.3. runtime state جدا از settings

- [x] برای notice بسته/باز بودن وب و rule «بعد از دومین آفر، پیام شروع محو شود» یک state runtime حداقلی لازم است و نباید فقط از schedule مشتق شود.
- [x] یک singleton/runtime table مثل `market_runtime_state` پیشنهاد می‌شود.
- [x] contract حداقلی پیشنهادی:
  - [x] `is_open`
  - [x] `last_transition_at`
  - [x] `active_web_notice_visible`
  - [x] `offers_since_last_open`
- [x] این state فقط باید حداقل اطلاعات لازم برای reminder وب، idempotent transition، و hide شدن notice شروع بعد از دومین آفر را نگه دارد؛ نه بیشتر.

## 3. سرویس‌ها و loopهای runtime

### 3.1. سرویس authoritative تصمیم‌گیری

- [x] یک سرویس مشترک مثل `core/services/market_schedule_service.py` اضافه شود.
- [x] این سرویس باید تنها مرجع truth برای این سؤال باشد: «الان بازار باز است یا بسته؟»
- [x] خروجی سرویس باید علاوه بر bool، reason و next transition را هم بدهد.
- [x] ترتیب تصمیم‌گیری باید این باشد:
  - [x] override روز خاص
  - [x] تعطیلی کامل روز
  - [x] روز بسته هفتگی
  - [x] window ساعتی روزانه

### 3.2. loop transition

- [x] یک loop جدید مثل `market_schedule_loop()` در `main.py` به background tasks اضافه شود.
- [x] loop باید فقط transition detect کند؛ business side effectها را به `market_transition_service` بسپارد.
- [x] loop باید idempotent باشد و restart/race باعث دوباره‌انتشار notice یا دوباره-expire شدن همان آفرها نشود.

### 3.3. transition بسته شدن بازار

- [x] با transition به حالت closed باید این side effectها به‌ترتیب و اتمیک تا حد ممکن اجرا شوند:
  - [x] همه آفرهای `ACTIVE` لوکال expire شوند.
  - [x] keyboard پیام‌های کانال offerها حذف شود.
  - [x] برای هر offer، `offer:expired` realtime publish شود تا وب‌اپ فوراً sync شود.
  - [x] `market_runtime_state` به وضعیت `closed` برود.
  - [x] `is_open=false` و `active_web_notice_visible=true` به‌عنوان state اعلان پایان بازار persist شود.
  - [x] پیام «پایان فعالیت بازار» در کانال تلگرام publish شود.
  - [x] رویداد realtime جدید `market:closed` برای clientهای وب publish شود.
- [x] انقضای بازار-بسته باید از auto-expire زمانی عادی از نظر business reason قابل تمایز باشد، حتی اگر status نهایی هر دو `EXPIRED` بماند.

### 3.4. transition باز شدن بازار

- [x] با transition به حالت open باید این side effectها اجرا شوند:
  - [x] `market_runtime_state` به وضعیت `open` برود.
  - [x] `active_web_notice_visible=true` شود.
  - [x] `offers_since_last_open=0` reset شود.
  - [x] پیام «شروع فعالیت بازار» در کانال تلگرام publish شود.
  - [x] رویداد realtime جدید `market:opened` برای clientهای وب publish شود.
- [x] پیام قبلی «پایان فعالیت بازار» در کانال حذف نمی‌شود.
- [x] آفرهای expireشده‌ی session قبلی revive نمی‌شوند؛ بازار فقط برای آفرهای جدید باز می‌شود.

## 4. contract رفتار وب‌اپ

### 4.1. Market page

- [x] `frontend/src/views/MarketView.vue` باید state جدید بازار را consume کند.
- [x] وقتی بازار بسته است:
  - [x] banner/notice «پایان فعالیت بازار» در market page نمایش داده شود.
  - [x] chatbox / input ثبت آفر غیرفعال شود.
  - [x] ارسال با Enter و click هم از UI block شود.
  - [x] draft محلی کاربر حفظ شود مگر product خلاف آن را بخواهد.
- [x] وقتی بازار باز می‌شود:
  - [x] banner قبلی «پایان فعالیت بازار» حذف شود.
  - [x] banner «شروع فعالیت بازار» به‌صورت یک reminder ساده نمایش داده شود.
  - [x] chatbox / input دوباره فعال شود.

### 4.2. محو شدن پیام شروع بازار بعد از دومین آفر

- [x] interpretation قطعی محصول: banner «شروع فعالیت بازار» در وب بعد از ثبت دومین آفر سراسری پذیرفته‌شده بازار بعد از current open transition محو می‌شود، فارغ از این‌که آن دو آفر را چه کاربری ثبت کرده باشد.
- [x] rationale قطعی محصول: بعد از دیده‌شدن آفرها، دیگر خود لیست بازار نشان می‌دهد که بازار باز است و نیازی به notice جداگانه نیست.
- [x] این behavior باید بر اساس state سراسری بازار باشد، نه صرفاً local state یک tab.
- [x] بنابراین create-offer success path باید `offers_since_last_open` را افزایش دهد.
- [x] وقتی این شمارنده به 2 رسید:
  - [x] `active_web_notice_visible=false` شود.
  - [x] یک realtime event مثل `market:notice_hidden` برای clientهای باز publish شود.

### 4.3. realtime contract وب

- [x] `useOffers` یا یک composable مستقل بازار باید eventهای جدید را consume کند:
  - [x] `market:closed`
  - [x] `market:opened`
  - [x] `market:notice_hidden`
- [x] UI بازار نباید برای دیدن start/end notice فقط به polling وابسته باشد.
- [x] refresh و open کردن market page در تب جدید باید آخرین state persisted را درست نشان دهد.

## 5. contract رفتار بات و کانال تلگرام

### 5.1. ثبت آفر در بات هنگام بسته بودن بازار

- [ ] `bot/handlers/trade_create.py` باید قبل از ورود به parse/preview/send flow، market-open policy را enforce کند.
- [ ] این guard باید هم text-offer path و هم wizard/FSM path را پوشش دهد.
- [ ] copy قطعی bot هنگام بسته بودن بازار:
  - [ ] `بعلت بسته بودن بازار درخواست شما ثبت نشد`
  - [ ] `لطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید.`

### 5.2. کانال تلگرام

- [x] در close transition، پیام «پایان فعالیت بازار» در کانال publish شود.
- [x] در open transition، پیام «شروع فعالیت بازار» در کانال publish شود.
- [x] close announcement قبلی در کانال حذف نمی‌شود.
- [ ] از لحظه بازگشایی، امکان ثبت آفر در بات دوباره فعال می‌شود.

## 6. contract backend create/execute

- [x] `api/routers/offers.py` باید روی create-offer authority، market-open policy را enforce کند.
- [x] اگر بازار بسته باشد، create-offer باید با error business مناسب reject شود.
- [x] `api/routers/trades.py` نیز باید guard هم‌راستا داشته باشد، حتی اگر close transition همه آفرها را expire کرده باشد؛ چون backend authority نباید فقط به UI یا transition side effect تکیه کند.

## 7. UI مدیریت ادمین برای schedule و exceptionها

- [x] این بخش فقط در وب‌اپ مدیریت می‌شود و برای MVP هیچ UI مدیریتی در بات تلگرام به آن اضافه نمی‌شود.
- [x] در `TradingSettings.vue` یک سکشن مستقل برای schedule پایه اضافه شود.
- [x] exceptionهای تقویمی/overrideها باید manager جداگانه داشته باشند، نه چند input عددی داخل فرم فعلی.
- [x] capabilityهای لازم برای UI ادمین:
  - [x] تعیین روزهای بسته هفتگی
  - [x] تعیین ساعت باز و بسته شدن روزانه
  - [x] ثبت تعطیلی تقویمی
  - [x] ثبت روز باز استثنایی
  - [x] ثبت ساعت سفارشی برای یک روز خاص
  - [x] نمایش preview از وضعیت فعلی بازار و next transition

## 8. تست و non-regression

### 8.1. backend

- [x] unit tests برای schedule evaluation با timezone `Asia/Tehran`
- [x] unit tests برای override precedence
- [x] unit tests برای close/open transition idempotency
- [x] unit tests برای expire-all-active-offers on close
- [x] unit tests برای create-offer / create-trade denial وقتی بازار بسته است

### 8.2. bot

- [ ] tests برای text offer path هنگام بسته بودن بازار با copy دقیق
- [ ] tests برای wizard start/confirm path هنگام بسته بودن بازار
- [ ] tests برای publish start/end message به کانال

### 8.3. frontend

- [x] Vitest برای MarketView notice rendering و disabled state
- [x] Vitest برای remove/show start/end notice روی eventهای realtime
- [x] Vitest برای `TradingSettings.vue` schedule base / preview / override manager
- [ ] Playwright برای close transition، open transition، disable/enable شدن chatbox، و hide شدن start notice بعد از دومین آفر

## 9. challengeها و سوال‌های باز بعد از audit

### 9.1. معنی دقیق «بعد از ثبت دومین آفر»

- [x] این ambiguity بسته شد.
- [x] contract قطعی: «دومین آفر سراسری پذیرفته‌شده بازار بعد از current open transition» مبنا است، نه آفرِ همان کاربر / همان session / همان tab.

### 9.2. scope دقیق chatbox غیرفعال‌شونده در وب

- [x] این ambiguity بسته شد.
- [x] route زنده بازار در فرانت فعلی `/market` است و به `frontend/src/views/MarketView.vue` وصل است.
- [x] `frontend/src/components/TradingView.vue` فعلاً route/view زنده بازار نیست و در usage فعلی فقط فایل component و test suite خودش دیده می‌شود؛ پس scope این roadmap برای chatbox وب، `MarketView.vue` است.

### 9.3. scope مدیریت schedule

- [x] این ambiguity بسته شد.
- [x] contract قطعی: مدیریت schedule و exceptionها فقط در وب‌اپ انجام می‌شود و برای این feature نیازی به توسعه UI تنظیمات متناظر در بات تلگرام نیست.

## 10. معیار آماده‌بودن برای implementation

- [x] سوال 9.1 بسته شد.
- [x] سوال 9.2 بسته شد.
- [x] roadmap از نظر contract محصولی لازم برای ورود به implementation phase آماده است.

## 11. وضعیت اجرای phaseها

- [x] Phase 1 - Data foundation
  - [x] مدل `market_schedule_overrides` اضافه شد.
  - [x] مدل `market_runtime_state` اضافه شد.
  - [x] migration additive برای هر دو table اضافه شد.
  - [x] `change_log` / sync mapping / ORM event listener برای هر دو surface اضافه شد.
  - [x] validation محدود این phase با `tests.test_market_schedule_foundation` و `tests.test_migration_smoke` سبز شد.
- [x] Phase 2 - Shared schedule evaluation service
  - [x] فیلدهای schedule پایه به `TradingSettings` به‌صورت JSON-safe اضافه شد.
  - [x] `core/services/market_schedule_service.py` precedence و next transition را به‌صورت pure محاسبه می‌کند.
  - [x] validation محدود این phase با `tests.test_market_schedule_service` و `tests.test_core_trading_settings_runtime` سبز شد.
- [x] Phase 3 - Transition loop and side effects
  - [x] `core/market_schedule_loop.py` به startup اضافه شد و transition detect را به service جدا سپرد.
  - [x] `core/services/market_transition_service.py` side effectهای open/close، noticeهای کانال، و realtime `market:*` را متمرکز کرد.
  - [x] `offers.expire_reason` اضافه شد تا `market_closed` از `time_limit` متمایز بماند.
  - [x] validation محدود این phase با `tests.test_market_transition_service`، `tests.test_market_schedule_loop`، `tests.test_main_lifespan`، `tests.test_offer_expiry` و `tests.test_migration_smoke` سبز شد.
- [x] Phase 4 - Backend offer/trade authority guards
  - [x] `api/routers/offers.py` و `api/routers/trades.py` هر دو runtime schedule را مستقیم evaluate می‌کنند و روی closed-market با business conflict برمی‌گردند.
  - [x] authority دیگر فقط به expire side effect یا UI disable تکیه نمی‌کند و boundary raceهای close transition را هم می‌بندد.
  - [x] validation محدود این phase با `tests.test_market_transition_service`، `tests.test_offers_router_create_guards`، `tests.test_offers_router_create_success`، `tests.test_trades_router_authoritative_guards` و `tests.test_trades_router_authoritative_success` سبز شد.
- [x] Phase 5 - Market web runtime and realtime notices
  - [x] public runtime endpoint برای state جاری بازار و persisted notice روی `trading-settings/market-state` اضافه شد.
  - [x] `create_offer` شمارنده `offers_since_last_open` را جلو می‌برد و روی آفر دوم `market:notice_hidden` publish می‌کند.
  - [x] `frontend/src/views/MarketView.vue` state اولیه بازار و realtime `market:*` را consume می‌کند و notice/disable state را روی `/market` اعمال می‌کند.
  - [x] validation محدود این phase با `tests.test_market_transition_service`، `tests.test_trading_settings_router_read`، `tests.test_offers_router_create_success` و `frontend/src/views/MarketView.test.ts` سبز شد.
- [x] Phase 6 - Admin web schedule management UI
  - [x] `api/routers/trading_settings.py` فیلدهای schedule پایه را در read/update/reset contract اکسپوز می‌کند.
  - [x] CRUD استثناهای تقویمی بازار روی همان router اضافه شد و با sync/event foundation موجود سازگار است.
  - [x] `frontend/src/components/TradingSettings.vue` حالا preview وضعیت فعلی بازار، تنظیم روزهای بسته/ساعات پایه، و manager استثناها را دارد.
  - [x] validation محدود این phase با `tests.test_trading_settings_router_read`، `tests.test_trading_settings_router_update`، `tests.test_trading_settings_router_overrides` و `frontend/src/components/TradingSettings.test.ts` سبز شد.
- [ ] Phase 7 - Focused regression coverage and rollout
