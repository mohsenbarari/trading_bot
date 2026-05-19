# Roadmap اجرای فیچر مشتری

این roadmap بعد از بسته‌شدن challengeهای [CUSTOMER_FEATURE_CHECKLIST.md](CUSTOMER_FEATURE_CHECKLIST.md) ساخته شده است. این سند قرار نیست تصمیم محصولی جدید خلق کند؛ فقط مسیر اجرای دقیق، ترتیب فازها، سناریوهای اصلی، dependencyها، validationها، و نقاط rollback را مشخص می‌کند.

## Snapshot وضعیت شروع roadmap

- [x] challengeهای محصولی اصلی customer بسته شده‌اند.
- [x] سطح‌بندی customer روی دو tier بسته شده است:
  - [x] `Tier 1`: آفرساز، بازار هم‌قیمت با عموم، کمیسیون توافقی خارج پلتفرم.
  - [x] `Tier 2`: non-offer-creator، market consumer/executor، کمیسیون سیستمی داخل پلتفرم.
- [x] دو اصل تفسیری نهایی روشن شده‌اند:
  - [x] مشتری یک کاربر عادی اما محدود است.
  - [x] هر رفتار عادی کاربر برای مشتری مجاز است مگر آن‌جا که محدودیت صریح customer آن را منع کند.
- [x] contract اجرای معامله‌ی customer روشن شده است: customer هیچ trade مستقیم نهایی با طرف بیرونی ندارد و chain معامله حتماً از owner او عبور می‌کند.
- [x] contract visibility بازار روشن شده است: role ادمینی به‌تنهایی raw visibility اضافه نمی‌کند و market visibility صرفاً relation-based است.
- [x] challenge fair-price برای offer source بسته شده است: `Tier 1` دقیقاً مانند user عادی رفتار می‌کند و `Tier 2` چون offer source نیست branch مستقلی در fair-price ندارد.
- [x] seamهای مهمی که می‌توان از آن‌ها reuse کرد در پروژه وجود دارند: `accountant_relation_service.py`, `chat_service.py`, `chat_room_service.py`, `session_service.py`, `user_deletion_service.py`, `users_public`, و الگوی `actor_user_id`.
- [x] در فرانت، پروفایل عمومی و سطح مدیریت ادمین دیگر دو surface جدا و disconnected نیستند؛ `PublicProfile.vue` اکنون می‌تواند `UserProfile.vue` را در modal ادمینی باز کند و این contract باید در طراحی customer نیز حفظ شود.

## 0. قواعد اجرای roadmap

- [x] توسعه به‌صورت phase-by-phase انجام می‌شود و هر phase فقط وقتی بسته می‌شود که validation phase قبلی سبز شده باشد.
- [x] همه schema changeها ابتدا additive هستند؛ destructive cleanup در release اولیه customer انجام نمی‌شود.
- [x] اصل invariant این roadmap: «customer = principal user با relation owner + policy overlay». 
- [x] identity واقعی actor در عملیات business باید قابل audit بماند.
- [x] owner مرجع policy و visibility مشتری است، اما خود مشتری actor واقعی عملیات خودش می‌ماند مگر جایی که business projection عمداً owner-facing می‌شود.
- [x] به‌دلیل single-server development mode، deployهای runtime فقط با `make foreign` انجام می‌شوند.

## 1. قراردادهای نهایی که roadmap بر آن‌ها تکیه می‌کند

- [x] customer یک user واقعی در جدول `users` است، نه subtype جدا یا صرفاً relation-only placeholder.
- [x] relation اصلی این فیچر یک جدول مستقل مشابه accountant relation است.
- [x] owner مرجع policy، visibility، cap، و customer management است.
- [x] customer تا جایی که restriction صریحی نقض نشود باید همان capabilityهای user عادی را inherit کند.
- [x] محدودیت‌های customer شامل market/trade visibility و allowed communication graph هستند، نه اینکه کل lifecycle user از نو تعریف شود.
- [x] پروفایل عمومی customer و ورود `SUPER_ADMIN` به تنظیمات همان customer باید روی contract فعلی `PublicProfile.vue` ↔ `UserProfile.vue` سوار شود، نه با ایجاد یک panel موازی جدید.

## 2. Phase 1 - Data Foundation

هدف:
ساخت foundation داده‌ای customer بدون دست‌زدن به behavior فعلی userهای موجود.

خروجی‌های لازم:
- [ ] مدل relation جدید برای owner ← customer اضافه شود.
- [ ] سطح customer به‌صورت صریح در مدل داده اضافه شود (`customer_tier`).
- [ ] migration جدول relation و cap per-owner اضافه شود.
- [ ] indexها و uniquenessها برای management name و active membership تعریف شود.
- [ ] fieldهای policy per-customer روی relation تعریف شوند.

حداقل فیلدهای مورد نیاز relation:
- [ ] `id`
- [ ] `owner_user_id`
- [ ] `customer_user_id`
- [ ] `created_by_user_id`
- [ ] `invitation_token`
- [ ] `management_name`
- [ ] `customer_tier`
- [ ] `commission_rate`
- [ ] `min_trade_quantity`
- [ ] `max_trade_quantity`
- [ ] `max_daily_trades`
- [ ] `max_daily_commodity_volume`
- [ ] `trading_restricted_until`
- [ ] `status`
- [ ] `activated_at`
- [ ] `deleted_at`
- [ ] `expires_at` برای pending invite

تغییرات تکمیلی روی user:
- [ ] `users.max_customers` با default `5` اضافه شود.

اصل طراحی این فاز:
- [ ] customer data باید حول relation شکل بگیرد، نه با پر کردن ده‌ها فیلد customer-specific در `users`.
- [ ] چون customer یک user عادی است، identity اصلی او در `users` باقی می‌ماند.
- [ ] policyهای owner-specific روی relation قرار می‌گیرند چون اگر روزی customer lifecycle عوض شود، باید از identity user مستقل بمانند.
- [ ] `commission_rate` برای `Tier 1` nullable/unused است و فقط برای `Tier 2` authoritative خواهد بود.

validation phase:
- [ ] migration head سبز شود.
- [ ] uniqueness management name در محدوده owner تست شود.
- [ ] relation activation / deletion / duplicate-active tests سبز شود.

rollback surface:
- [ ] با disable کردن consumerها و endpointها، schema additive قابل نگه‌داری است.

## 3. Phase 2 - Shared Backend Seams

هدف:
ساخت seamهای reusable تا customer logic در routerها duplicate نشود و مثل accountant قابل نگه‌داری بماند.

خروجی‌های لازم:
- [ ] service جدید `customer_relation_service.py` ساخته شود.
- [ ] helperهای capacity, active-relation lookup, owner resolution, allowed-communication graph ساخته شود.
- [ ] helperهای shared commission calculation و reverse presentation ساخته شود.

حداقل helperهای لازم:
- [ ] `get_active_customer_relation_for_user(...)`
- [ ] `get_owner_for_customer(...)`
- [ ] `list_active_customers_for_owner(...)`
- [ ] `validate_owner_customer_capacity(...)`
- [ ] `validate_customer_trade_limits(...)`
- [ ] `apply_customer_commission(raw_price, rate, offer_type)`
- [ ] `round_customer_price(adjusted_price, offer_type)`
- [ ] `build_allowed_customer_chat_targets(customer_id)`

اصل طراحی این فاز:
- [ ] منطق customer نباید بین auth, offers, trades, chat, users_public و frontend هرکدام جداگانه بازنویسی شود.
- [ ] math کمیسیون باید pure و deterministic باشد تا تمام سناریوهای midpoint و edge-caseها testable باشند.

validation phase:
- [ ] unit testهای pure commission math سبز شوند.
- [ ] owner resolution و capacity tests سبز شوند.
- [ ] allowed chat graph tests سبز شوند.

## 4. Phase 3 - Auth, Register, Session Policy

هدف:
وارد کردن customer به lifecycle ثبت‌نام و session بدون شکستن منطق user عادی.

خروجی‌های لازم:
- [ ] invitation flow customer-aware شود.
- [ ] registration بر اساس token customer relation را activate کند.
- [ ] session policy customer فقط در محدوده restrictionهای صریح محدود شود، نه با branch کاملاً مجزا.

رفتار مطلوب:
- [ ] customer از دید login/OTP/refresh/session یک user عادی است مگر requirement صریح customer-specific آن را محدود کند.
- [ ] اگر policy فعلی session برای userهای عادی مجاز است و هیچ محدودیت customer آن را منع نکرده، customer هم همان behavior را بگیرد.
- [ ] bot/web split همچنان باید با challenge document سازگار بماند؛ customer در این فاز web-only است و bot path نباید onboarding موازی بسازد.

validation phase:
- [ ] customer invitation register flow سبز شود.
- [ ] invalid/expired/reused token tests سبز شوند.
- [ ] customer session regression با user عادی مقایسه شود تا محدودیت ناخواسته اضافه نشده باشد.

## 5. Phase 4 - Communication Graph and Messenger Restrictions

هدف:
پیام‌رسان برای customer «شبیه user عادی ولی در گراف محدودشده» کار کند.

خروجی‌های لازم:
- [ ] direct chat creation برای customer با owner، accountantهای فعال همان owner، و `SUPER_ADMIN` مجاز شود.
- [ ] direct chat creation از سمت owner/accountant و `SUPER_ADMIN` به customerهای مجاز ممکن باشد.
- [ ] customer نتواند با customer دیگر یا user خارج از tree همان owner وارد چت شود.
- [ ] `MIDDLE_MANAGER` در این فاز direct chat مجاز با customer ندارد.
- [ ] customer نتواند channel member شود.
- [ ] customer نتواند group جدید بسازد.
- [ ] group membership rule دقیق enforce شود: owner/accountants + همان customer و نه بیشتر از یک customer.

اصل طراحی این فاز:
- [ ] deny pathها باید در backend authoritative باشند، نه فقط در frontend.
- [ ] frontend فقط UI affordance را پنهان می‌کند؛ rule enforcement باید در router/service لایه chat انجام شود.
- [ ] چون customer کاربر عادی است، message model, conversation model, read-state, reactions و بقیه runtimeها reuse می‌شوند؛ فقط target graph محدود می‌شود.
- [ ] چون direct chat فعلی conversation دوطرفه است، مجازبودن `SUPER_ADMIN → customer` باید به‌صورت یک edge دوطرفه در allowed communication graph مدل شود، نه یک send-only استثنای شکننده.

validation phase:
- [ ] direct chat allow/deny matrix سبز شود.
- [ ] matrix باید owner/accountant/same-owner customer/`SUPER_ADMIN`/`MIDDLE_MANAGER` را صریح پوشش دهد.
- [ ] group creation/member mutation deny matrix سبز شود.
- [ ] notification/realtime behavior در chatهای مجاز intact بماند.

## 6. Phase 5 - منطق قیمت‌گذاری، سناریو محور و exhaustive

هدف:
مشخص‌کردن اینکه pricing behavior برای Tier 1 و Tier 2 بدون تداخل و با projection قابل‌ردیابی اجرا شود.

### 5.0. رفتار tier-aware

- [ ] `Tier 1`: آفر با raw price منتشر می‌شود و همه viewerها همان raw published price را می‌بینند.
- [ ] `Tier 1`: owner همان customer روی آفر، تگ «مشتری + management name» می‌بیند.
- [ ] `Tier 2`: حق ثبت آفر ندارد و فقط request روی آفرهای دیگر می‌زند.
- [ ] `Tier 2`: pricing projection بر اساس commission policy سیستمی باقی می‌ماند.
- [ ] `Tier 2`: projection قیمت viewer-specific است؛ دو `Tier 2` با نرخ‌های متفاوت می‌توانند یک آفر واحد را با دو price متفاوت ببینند.

### 5.1. اصل بنیادی

- [ ] قیمت ذخیره‌شده در DB برای آفر customer باید «قیمت خام actor» بماند.
- [ ] کمیسیون سیستمی فقط به `Tier 2` تعلق دارد.
- [ ] برای `Tier 1` هیچ adjusted price در pipeline پلتفرم تولید نمی‌شود.
- [ ] زنجیره execution باید با tier طرفین سازگار باشد.
- [ ] برای `Tier 2`، قیمت execution روی leg customer ↔ owner همان `viewer_effective_price` همان customer است و روی leg بیرونی owner ↔ counterparty همان raw published price باقی می‌ماند.

### 5.2. لایه‌های قیمت

- [ ] `raw_price`: همان قیمتی که customer یا owner واقعاً ثبت کرده است.
- [ ] `market_published_price`: قیمت منتشرشده در market.
- [ ] `viewer_effective_price`: قیمت نهایی نمایش برای viewer.

این تفکیک مهم است چون:
- [ ] برای `Tier 1`: `raw_price == market_published_price == viewer_effective_price` برای همه viewerها (به‌جز owner tag context).
- [ ] برای `Tier 2`: projectionهای کمیسیونی فعال هستند و می‌توانند بین این سه لایه اختلاف بسازند.
- [ ] برای `Tier 2` اگر viewer rate نداشته باشد، `viewer_effective_price == market_published_price`.

### 5.3. سناریوی Tier 1 - آفر فروش

اگر customer یک آفر فروش ثبت کند:
- [ ] raw_price همان عددی است که customer وارد کرده است.
- [ ] برای خود customer: همان raw_price نمایش داده می‌شود.
- [ ] برای owner همان customer: raw_price نمایش داده می‌شود + badge مشتری.
- [ ] برای همه viewerها همان raw_price منتشر و نمایش داده می‌شود.
- [ ] فقط owner همان customer context tag می‌بیند.

مثال:
- [ ] `سینا` sell raw=`52000` ثبت می‌کند.
- [ ] آفر با `52000` برای همه viewerها منتشر می‌شود.
- [ ] فقط `رامین` روی همان offer تگ مشتری + نام `سینا` را می‌بیند.

### 5.4. سناریوی Tier 1 - آفر خرید

اگر customer یک آفر خرید ثبت کند:
- [ ] raw_price همان عدد واردشده است.
- [ ] برای customer: raw_price.
- [ ] برای owner همان customer: raw_price + badge مشتری.
- [ ] برای همه viewerها همان raw_price منتشر و نمایش داده می‌شود.
- [ ] فقط owner همان customer context tag می‌بیند.

مثال:
- [ ] `پیمان` buy raw=`98000` ثبت می‌کند.
- [ ] آفر با `98000` برای همه viewerها منتشر می‌شود.
- [ ] فقط `مجید` روی همان offer تگ مشتری + نام `پیمان` را می‌بیند.

### 5.5. midpoint / rounding

- [ ] این بخش فقط برای `Tier 2` کاربرد دارد.
- [ ] `Tier 1` چون projection کمیسیونی ندارد، midpoint rule در انتشار آفر استفاده نمی‌شود.
- [ ] سناریوهای قطعی `Tier 2` نشان می‌دهند که rounding authoritative همان direction-fixed است، نه nearest-100.

مثال:
- [ ] adjusted = `200550`.
- [ ] چون midpoint در فروش است، خروجی `200600` می‌شود.

### 5.6. Tier 2 projection در آفر خرید

- [ ] formula: `viewer_effective_price = floor_100(raw_price - commission(viewer))`.
- [ ] مثال قطعی: `192800` با کمیسیون `0.5%` به `191800` و با کمیسیون `0.7%` به `191400` می‌رسد.

### 5.7. Tier 2 projection در آفر فروش

- [ ] formula: `viewer_effective_price = ceil_100(raw_price + commission(viewer))`.
- [ ] مثال قطعی: `53500` با کمیسیون `0.5%` به `53800` و با کمیسیون `0.7%` به `53900` می‌رسد.

### 5.8. وقتی owner خودش market را می‌بیند

- [ ] owner برای آفرهای `Tier 1` customer خودش raw view کامل می‌بیند.
- [ ] owner برای `Tier 2` market projection مستقلی روی آفر customer-originated ندارد، چون `Tier 2` آفر publish نمی‌کند.
- [ ] دلیل: owner policy maker است و باید بداند customer چه قیمت خامی ثبت کرده.
- [ ] owner نباید برای customerهای ownerهای دیگر identity اضافه ببیند.
- [ ] owner فقط customerهای خودش را با badge مشتری می‌بیند.

### 5.9. وقتی admin market را می‌بیند

- [ ] در market، admin نباید visibility ویژه صرفاً به خاطر role داشته باشد.
- [ ] middle admin و super admin در market باید دقیقاً مثل user عادی رفتار کنند مگر آن‌که خودشان owner همان customer relation باشند.
- [ ] بنابراین raw price customer در market فقط relation-based دیده می‌شود، نه role-based.
- [ ] contract فعلی `PublicProfile` و `UserProfile` فقط surface مدیریتی/profile را هم‌راستا می‌کند و نباید باعث market-rent شود.

### 5.10. وقتی customer خودش market را می‌بیند

- [ ] در `Tier 1` فقط آفر خودش raw نیست؛ کل market با همان raw published price عمومی دیده می‌شود.
- [ ] در `Tier 2` projection کمیسیونی viewer-facing فعال است.
- [ ] دو `Tier 2` مختلف می‌توانند هم‌زمان یک آفر واحد را با priceهای متفاوت ببینند.
- [ ] customer نباید commission rate خودش را از UI استخراج کند.
- [ ] بنابراین حتی اگر raw/effective difference برای آفر خودش قابل مقایسه باشد، نباید label یا helper مستقیمی نرخ را افشا کند.

### 5.11. وقتی یک user عادی دیگر market را می‌بیند

- [ ] نباید بفهمد این آفر متعلق به customer است.
- [ ] نه badge، نه owner relation، نه management name، نه field اضافی UI.
- [ ] فقط final viewer-facing price همان viewer را می‌بیند؛ برای user عادی این همان `market_published_price` است.

### 5.12. سناریوی owner editing customer commission

وقتی owner commission را از `0.5` به `0.8` تغییر می‌دهد:
- [ ] این سناریو برای `Tier 2` است.
- [ ] `Tier 1` commission setting سیستمی ندارد.
- [ ] هیچ raw_price قبلی در DB تغییر نمی‌کند.
- [ ] tradeهای قبلاً ثبت‌شده باید با commission historical خودشان ثابت بمانند و rewrite نشوند.
- [ ] هر trade جدیدی که بعد از این تغییر ایجاد می‌شود باید با commission جدید محاسبه شود.
- [ ] اگر آفر active هنوز باز باشد، projectionهای market و viewer برای executionهای آینده باید از لحظه تغییر با policy جدید محاسبه شوند.
- [ ] helper text فرم باید زنده آپدیت شود.
- [ ] competitive/fair-price logic باید بداند raw_price کدام است و effective price viewer-facing کدام است.

### 5.13. سناریوی fair-price calculation

این بخش دیگر challenge محصولی باز ندارد.

- [x] آفرهای `Tier 1` در fair-price / competitive-price دقیقاً باید مثل آفر سایر کاربران رفتار کنند.
- [x] raw price آفر `Tier 1` همان input authoritative برای fair-price است.
- [x] `Tier 2` چون offer source نیست، branch جداگانه‌ای در fair-price calculation ندارد.
- [x] viewer projection مربوط به `Tier 2` فقط concern نمایشی/اجرایی responder است و وارد dataset fair-price offer source نمی‌شود.
- [ ] roadmap فقط باید تضمین کند که customer rollout current aggressive-price warning logic را برای offer sourceهای مجاز نشکند.

### 5.14. سناریوی trade execution روی offer customer

- [ ] customer نباید trade مستقیم customer ↔ outsider داشته باشد.
- [ ] هر execution باید از owner mediation عبور کند.
- [ ] full matrix در checklist section 9.0 مرجع نهایی actor-category coverage است؛ این بخش همان matrix را به requirementهای اجرایی تبدیل می‌کند.
- [ ] offer source فقط `Owner` یا `Tier 1` است؛ `Tier 2` source invalid است و باید در create-offer guard رد شود.
- [ ] buy/sell mirrorها با همان chain actorها derive می‌شوند؛ فقط جهت buyer/seller در هر leg و price projection متناظر با offer type تغییر می‌کند.
- [ ] Tier 1 canonical cases (قیمت یکسان در همه legs):
  - [ ] customer seller vs customer buyer (ownerهای متفاوت): chain سه‌مرحله‌ای.
  - [ ] customer buyer vs owner outsider: chain دو‌مرحله‌ای.
  - [ ] owner offer vs customer outsider: chain دو‌مرحله‌ای.
  - [ ] owner offer vs own-customer: trade مستقیم یک‌مرحله‌ای.
- [ ] Tier 1 canonical examples:
  - [ ] `سینا sell 52000` + `پیمان request` => `پیمان ↔ مجید @52000`، `مجید ↔ رامین @52000`، `رامین ↔ سینا @52000`.
  - [ ] `پیمان buy 98000` + `رامین request` => `مجید ↔ رامین @98000`، `پیمان ↔ مجید @98000`.
  - [ ] `مجید buy 189600` + `سینا request` => `رامین ↔ سینا @189600`، `مجید ↔ رامین @189600`.
  - [ ] `رامین sell 188000` + `سینا request` => direct `سینا ↔ رامین @188000`.
- [ ] notificationها، historyها، و UI summaryها باید با همین chainها هم‌راستا باشند.
- [ ] Tier 2 canonical cases:
  - [ ] owner-offer + own Tier2 responder: direct one-leg trade at `viewer_effective_price`.
  - [ ] owner/non-customer buy-offer + Tier2 responder: two-leg chain (`customer ↔ own owner @ viewer_effective_price`, `own owner ↔ source buyer @ raw_price`).
  - [ ] owner/non-customer sell-offer + Tier2 responder: two-leg chain (`customer ↔ own owner @ viewer_effective_price`, `own owner ↔ source seller @ raw_price`).
  - [ ] Tier1 sell-offer + same-owner Tier2 responder: two-leg chain (`Tier2 customer ↔ shared owner @ viewer_effective_price`, `shared owner ↔ Tier1 source seller @ raw_price`).
  - [ ] Tier1 offer + other-owner Tier2 responder: three-leg chain (`Tier1 source ↔ source owner @ raw_price`, `source owner ↔ responder owner @ raw_price`, `responder owner ↔ Tier2 responder @ viewer_effective_price`) with buyer/seller directions derived from offer type.
  - [ ] core rule: `Tier 2` responder leg always uses `viewer_effective_price`; all inter-owner/source legs preserve `raw_price`; source-side mediation depends on whether the source actor is `Owner`/non-customer or `Tier 1`.
- [ ] Final matrix examples:
  - [ ] Owner source vs other owner: `مجید ←buyer / رامین ←seller @ 50000`.
  - [ ] Owner source vs own Tier1: `رامین ←buyer / سینا ←seller @ 100000`.
  - [ ] Owner source vs other Tier1: `پیمان ←buyer / مجید ←seller @ 200000`; `مجید ←buyer / رامین ←seller @ 200000`.
  - [ ] Owner buy raw=`50000` vs own Tier2: `49750` rounds down to `49700`; `رامین ←buyer / علی ←seller @ 49700`.
  - [ ] Owner sell raw=`100000` vs other Tier2: `محمد ←buyer / مجید ←seller @ 100700`; `مجید ←buyer / رامین ←seller @ 100000`.
  - [ ] Tier1 source vs own owner: `رامین ←buyer / سینا ←seller @ 200000`.
  - [ ] Tier1 source vs other owner: `سینا ←buyer / رامین ←seller @ 50000`; `رامین ←buyer / مجید ←seller @ 50000`.
  - [ ] Tier1 source vs same-owner Tier1: `سهراب ←buyer / رامین ←seller @ 100000`; `رامین ←buyer / سینا ←seller @ 100000`.
  - [ ] Tier1 source vs other-owner Tier1: `سینا ←buyer / رامین ←seller @ 200000`; `رامین ←buyer / مجید ←seller @ 200000`; `مجید ←buyer / پیمان ←seller @ 200000`.
  - [ ] Tier1 sell raw=`50000` vs same-owner Tier2: `50250` rounds up to `50300`; `علی ←buyer / رامین ←seller @ 50300`; `رامین ←buyer / سینا ←seller @ 50000`.
  - [ ] Tier1 buy raw=`100000` vs other-owner Tier2: `سینا ←buyer / رامین ←seller @ 100000`; `رامین ←buyer / مجید ←seller @ 100000`; `مجید ←buyer / محمد ←seller @ 99300`.
- [ ] notificationها، historyها، و UI summaryها باید این chain را منعکس کنند، نه اینکه یک trade مستقیم customer ↔ customer بسازند.

### 5.15. سناریوی customer trading restriction

اگر customer به‌صورت owner-level موقت restricted شده باشد:
- [ ] market list شاید هنوز قابل مشاهده باشد، اما create/execute trade باید بسته شود.
- [ ] messaging نباید خودکار بسته شود.
- [ ] UI باید شبیه user عادی restricted رفتار کند، نه surface کاملاً جدا.

### 5.16. validation phase برای قیمت‌گذاری

- [ ] pure math tests برای buy/sell, midpoint, non-midpoint, floor_100/ceil_100.
- [ ] serializer tests برای owner/admin/customer/public viewer matrix.
- [ ] market e2e برای raw vs adjusted rendering.
- [ ] regression برای warning/exclusion path بدون reintroduce کردن challenge fair-price.

## 7. Phase 6 - تاریخچه معامله، سناریو محور و exhaustive

هدف:
نمایش trade history به‌گونه‌ای که business relation واقعی حفظ شود، اما customer context فقط برای viewerهای مجاز دیده شود.

### 7.1. اصل بنیادی

- [ ] trade از دید actor ممکن است توسط customer انجام شده باشد.
- [ ] اما از دید business relation، owner مرجع principal آن رابطه است.
- [ ] history باید این دو لایه را قاطی نکند.
- [ ] در سناریوهای customer-mediated، history باید بداند که یک business action ممکن است به چند trade row شکسته شده باشد.

### 7.2. دو حقیقت هم‌زمان در history

- [ ] `principal relation truth`: این معامله در graph تجاری owner رخ داده است.
- [ ] `actor context truth`: این عملیات مشخص را خود customer انجام داده است.

history باید بسته به viewer یکی یا هر دو را نشان دهد.

### 7.3. سناریوی owner در تاریخچه با user عادی دیگر

اگر customer1 از owner1 با user3 معامله کند و owner1 تاریخچه مشترک خودش با user3 را ببیند:
- [ ] معامله باید در history owner1 ↔ user3 دیده شود.
- [ ] کنار آن یک context کوچک مشتری نمایش داده شود.
- [ ] این context باید حداقل شامل badge `مشتری` و `management_name` customer1 باشد.

هدف این نمایش:
- [ ] owner بفهمد این معامله از کانال کدام customer اتفاق افتاده است.
- [ ] بدون آنکه trade principal owner/user3 از هم بپاشد.

### 7.3.1. سناریوی customer ↔ customer با ownerهای متفاوت

- [ ] اگر یک customer از ownerA و یک customer از ownerB در دو سمت یک business action باشند، history نباید این اتفاق را به‌صورت یک trade مستقیم customer ↔ customer نمایش دهد.
- [ ] هر طرف باید leg مرتبط با خودش را در history خود ببیند.
- [ ] ownerA و ownerB باید leg بین‌مالکی (`ownerB ↔ ownerA`) را هم در historyهای مرتبط خود ببینند.
- [ ] source customer باید leg `ownerA ↔ customerA` را ببیند.
- [ ] responder customer باید leg `customerB ↔ ownerB` را ببیند.
- [ ] mutual history بین ownerA و ownerB باید بر leg واسطه‌ای بین دو owner تکیه کند، نه بر customer endpointها.
- [ ] این chain برای `Tier 1` قطعی است و با قیمت یکسان در همه legs ثبت می‌شود.

### 7.3.2. سناریوی Tier 2 روی owner-offer

- [ ] اگر `Tier 2` روی آفر owner خودش request بزند، history همان direct trade owner ↔ customer را با price projected-to-customer نشان می‌دهد.
- [ ] owner در این view customer context را به‌صورت صریح می‌بیند چون counterparty خودش customer خودش است.

### 7.3.3. سناریوی Tier 2 روی outsider-offer

- [ ] اگر `Tier 2` روی آفر buy یا sell بیرونی request بزند، history باید دو leg را جدا نگه دارد.
- [ ] customer فقط leg خودش با owner خودش را می‌بیند.
- [ ] owner customer هر دو leg را با customer context مناسب می‌بیند.
- [ ] counterparty بیرونی فقط leg مجاز مربوط به خودش را می‌بیند و customer identityهای پشت ownerها برای او sanitize می‌شود.
- [ ] اگر source actor یک `Tier 1` باشد و responder بیرون از همان owner relation باشد، history باید source-owner leg را نیز حفظ کند؛ leg بین‌مالکی به source owner ختم می‌شود و source owner leg جداگانه به خود `Tier 1` ختم می‌شود.

### 7.4. سناریوی counterpart در تاریخچه با owner

اگر user3 تاریخچه مشترک با owner1 را ببیند:
- [ ] اصل معامله را می‌بیند.
- [ ] ولی customer context را نباید ببیند.
- [ ] نه badge مشتری، نه management_name، نه relation hint.

دلیل:
- [ ] customer identity برای counterpart private است مگر requirement دیگری بعداً آن را باز کند.

### 7.5. سناریوی owner در تاریخچه با خود customer

اگر owner1 وارد پروفایل عمومی customer1 شود و تاریخچه را ببیند:
- [ ] اینجا viewer دارد رابطه مستقیم owner ↔ customer را می‌بیند.
- [ ] هر trade باید طرف دیگر معامله را همراه context نمایش دهد.
- [ ] مثلاً اگر customer1 با user3 معامله‌ای انجام داده، owner1 در لیست customer1 باید ببیند counterpart چه کسی بوده است.
- [ ] در این view، customer context دیگر زائد است چون خود صفحه متعلق به customer1 است.

### 7.6. سناریوی accountant در تاریخچه customer

- [ ] accountant owner باید بتواند customerها و history آن‌ها را ببیند.
- [ ] اما نباید owner-only management control ببیند.
- [ ] نمایش history برای accountant باید شبیه owner باشد، با همان customer contextهای لازم.

### 7.7. سناریوی admin در تاریخچه

- [ ] admin باید همان visibility owner را در history customer-aware داشته باشد.
- [ ] اگر owner در یک view badge/customer context می‌بیند، admin هم باید ببیند.
- [ ] این دقیقاً باید با sync اخیر public profile و admin user modal سازگار بماند؛ یعنی جایی که admin از public profile وارد تنظیمات user می‌شود، همان entity/context را ببیند.
- [ ] این rule history-specific است و نباید به market visibility ویژه برای admin نشت کند.

### 7.8. سناریوی customer در تاریخچه خودش

- [ ] customer باید trade history خودش را مثل یک user عادی ببیند.
- [ ] نباید internal commission metadata یا owner policy reasoning را ببیند.
- [ ] اگر context اضافه‌ای نشان داده می‌شود، فقط چیزهایی که user عادی هم می‌تواند برای trade خودش ببیند.

### 7.9. سناریوی multiple customers of same owner

اگر owner چند customer داشته باشد و در history مشترک owner با userX چند trade از customerهای مختلف وجود داشته باشد:
- [ ] هر trade باید customer context خودش را جداگانه بگیرد.
- [ ] badge/customer label نباید کلی و یک‌جا در header history بیاید، چون trade-by-trade متفاوت است.
- [ ] ordering history و filterها نباید با customer projection بشکنند.

### 7.10. سناریوی delete/unlink customer روی history

اگر customer relation حذف یا revoke شود:
- [ ] history گذشته نباید از بین برود.
- [ ] context tradeهای قبلی باید تا حد امکان preserved بماند.
- [ ] baseline پروژه برای userهای عادی و middle admin همین الان history-preserving soft delete است: user row حذف فیزیکی نمی‌شود، `account_name/mobile` suffix می‌خورند، trade rows باقی می‌مانند، و frontend suffix را برای display پاک می‌کند.
- [ ] customer lifecycle هم باید همین اصل را inherit کند: حذف/revoke نباید history را نابود کند.
- [ ] برای customer-specific context، همان الگوی existing soft delete authoritative است: relation row باید soft-deleted/revoked باقی بماند تا `management_name` historical از lookup داده‌ی soft-deleted برگردد؛ snapshot trade-time جدید برای این فاز نیاز نیست.
- [ ] public profile deleted user می‌تواند unavailable شود، اما trade history و display nameهای historical باید پایدار بمانند.

### 7.11. سناریوی block/restriction propagation در history

- [ ] block یا restriction باید tradeهای جدید را متوقف کند.
- [ ] اما history گذشته را تغییر نمی‌دهد.
- [ ] اگر trade در دوره‌ای انجام شده که customer فعال بوده، بعداً revoke شدن relation نباید آن history را rewrite کند.

### 7.12. projection design اصل‌محور

- [ ] history serialization نباید با if/elseهای پراکنده در چند endpoint ساخته شود.
- [ ] یک helper یا projection seam customer-aware لازم است تا:
  - [ ] owner/admin/accountant view را غنی کند
  - [ ] counterpart/public view را sanitize کند
  - [ ] customer self-view را minimal نگه دارد

### 7.13. validation phase برای history

- [ ] mutual history tests برای owner/customer/counterparty/admin/accountant matrix.
- [ ] public profile history rendering tests.
- [ ] e2e برای badge/customer context visibility.
- [ ] revoke/delete regression برای historical context persistence.

## 8. Phase 7 - Public Profile, Owner Management, and Admin Sync

هدف:
customer management و customer visibility روی contract فعلی PublicProfile/Admin/UserProfile سوار شود و UI موازی جدید نسازد.

وضعیت contract فعلی که باید reuse شود:
- [x] `PublicProfile.vue` برای owner action cardهای owner-only دارد.
- [x] `PublicProfile.vue` اکنون برای نقش‌های ادمینی می‌تواند `UserProfile.vue` را در modal باز کند، اما customer visibility باید از اینجا به بعد `SUPER_ADMIN`-only شود.
- [x] `AdminView.vue` نیز همان `UserProfile.vue` را به‌عنوان surface مدیریت user استفاده می‌کند.

نتیجه اجرایی برای customer:
- [ ] customer section owner-only در `PublicProfile.vue` اضافه می‌شود.
- [ ] فقط `SUPER_ADMIN` باید در همان profile surface customer visibility را ببیند، ولی actionهای owner-only را نه.
- [ ] `MIDDLE_MANAGER` نباید customer public profile و customer list در public profile owner را ببیند.
- [ ] اگر `SUPER_ADMIN` از public profile وارد modal تنظیمات کاربر شد، همان data contract باید customer-related fields را هم در `UserProfile.vue` نشان دهد.
- [ ] بنابراین customer settings duplication بین `PublicProfile.vue` و `AdminView.vue` ممنوع است؛ منبع مدیریت باید همان `UserProfile.vue` بماند.
- [ ] برای customer surface نباید از helper عمومی `isAdminRoleValue()` استفاده شود؛ gate این بخش باید صریحاً `SUPER_ADMIN` را از `MIDDLE_MANAGER` جدا کند.
- [ ] `users_public` و searchهای وابسته به آن باید customer visibility را server-side و relation-aware enforce کنند تا هم public profile نشت نکند و هم featureهای chat/search با rule جدید سازگار بمانند.
- [ ] history accordion در public profile برای `SUPER_ADMIN` باید از mutual-history contract فعلی جدا شود و history واقعی user هدف را بارگذاری کند.
- [ ] buy/sell badge در super-admin profile history باید از perspective همان user هدف محاسبه شود، نه از perspective viewer.
- [ ] customer-aware rows در super-admin profile history باید context relation را حمل کنند: owner-target → `customer badge + management_name`، customer-target → `owner + tier`.
- [ ] تا قبل از phase مربوط به chain/grouping، super-admin profile history فقط rowهای مستقیمِ target user را نشان می‌دهد و legهای غیرمستقیم را infer/group نمی‌کند.

خروجی‌های لازم:
- [ ] owner customer manager modal / section
- [ ] super-admin-visible customer summary in public profile
- [ ] `UserProfile.vue` support for `max_customers` and customer-aware admin controls
- [ ] route/state sync بین public profile و admin user settings برای customer surfaces

validation phase:
- [ ] owner profile customer CRUD UI سبز شود.
- [ ] super-admin public-profile → user-settings handoff سبز شود.
- [ ] super-admin target-user history load/perspective tests سبز شود.
- [ ] public profile visibility matrix for customer/accountant/admin/user عادی سبز شود.

## 9. Phase 8 - Sync, Deletion, and Lifecycle Convergence

هدف:
customer relation در sync/replay/deletion drift نکند و lifecycle آن روی user lifecycle عادی سوار بماند.

خروجی‌های لازم:
- [ ] `customer_relations` به sync/change-log اضافه شود.
- [ ] `users.max_customers` در sync payloadهای user همگرا شود.
- [ ] relation delete/revoke/owner delete/customer delete paths converge شوند.

اصل lifecycle:
- [ ] چون customer یک user عادی است، delete path باید بر lifecycle موجود user تکیه کند.
- [ ] relation owner ← customer لایه‌ی اضافه است که همراه delete/revoke باید همگرا شود.
- [ ] history/trade/message گذشته preserved می‌ماند.

validation phase:
- [ ] sync receive/resync tests.
- [ ] owner delete/customer delete/relation revoke tests.
- [ ] stale relation replay regression tests.

## 10. Phase 9 - Release Gate and End-to-End Validation

هدف:
بستن release با اطمینان از اینکه customer نه overly-restricted شده و نه privacy ruleها را نقض می‌کند.

حداقل سناریوهای E2E:
- [ ] owner creates customer invite
- [ ] customer registers via web
- [ ] owner edits commission and limits
- [ ] Tier 1 customer creates sell offer
- [ ] owner sees raw price + badge on Tier 1 offer
- [ ] Tier 2 viewer sees projected buy/sell prices based on own commission
- [ ] Tier 2 executes request on own-owner offer and outsider offer
- [ ] admin sees owner-facing customer context
- [ ] customer allowed direct chat with owner succeeds
- [ ] customer direct chat with unrelated user is denied
- [ ] customer cannot join channel / create group
- [ ] owner mutual history with third-party shows customer badge + management name
- [ ] third-party mutual history with owner hides customer context

release gate:
- [ ] focused backend suite سبز
- [ ] focused frontend unit suite سبز
- [ ] customer Playwright flow سبز
- [ ] sync/replay suite سبز
- [ ] `make foreign` deploy green

## 11. سوال‌های فنی جدیدی که بعد از پاسخ‌های فاز 0 هنوز باید فقط در implementation phase بسته شوند

این‌ها blocker محصولی نیستند، ولی در طراحی فنی باید early explicit شوند:

- [x] تعداد دقیق legs و shape نهایی trade chain برای owner/Tier1 source در برابر owner/Tier1/Tier2 responder در ماتریس ۱۱ حالته section 5.14 بسته شد.
- [x] fallback naming برای history وقتی customer relation بعداً deleted/revoked می‌شود از همان soft-deleted relation lookup و الگوی موجود user soft delete می‌آید؛ snapshot trade-time جدید برای این فاز انتخاب نشد.
- [x] fair-price customer-aware به این صورت بسته شد که `Tier 1` دقیقاً مانند سایر کاربران offer source رفتار می‌کند و `Tier 2` چون offer source نیست، branch مستقلی در fair-price ندارد.

این موارد challenge جدید محصولی نیستند؛ فقط detailهای implementation-level هستند و باید در phaseهای 4 تا 8 به‌صورت صریح بسته شوند.