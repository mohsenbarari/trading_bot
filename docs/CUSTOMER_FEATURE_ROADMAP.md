# Roadmap اجرای فیچر مشتری

این roadmap بعد از بسته‌شدن challengeهای [CUSTOMER_FEATURE_CHECKLIST.md](CUSTOMER_FEATURE_CHECKLIST.md) ساخته شده است. این سند قرار نیست تصمیم محصولی جدید خلق کند؛ فقط مسیر اجرای دقیق، ترتیب فازها، سناریوهای اصلی، dependencyها، validationها، و نقاط rollback را مشخص می‌کند.

## Snapshot وضعیت شروع roadmap

- [x] challengeهای محصولی اصلی customer بسته شده‌اند.
- [x] دو اصل تفسیری نهایی روشن شده‌اند:
  - [x] مشتری یک کاربر عادی اما محدود است.
  - [x] هر رفتار عادی کاربر برای مشتری مجاز است مگر آن‌جا که محدودیت صریح customer آن را منع کند.
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
- [x] پروفایل عمومی customer و ورود ادمین به تنظیمات همان customer باید روی contract فعلی `PublicProfile.vue` ↔ `UserProfile.vue` سوار شود، نه با ایجاد یک panel موازی جدید.

## 2. Phase 1 - Data Foundation

هدف:
ساخت foundation داده‌ای customer بدون دست‌زدن به behavior فعلی userهای موجود.

خروجی‌های لازم:
- [ ] مدل relation جدید برای owner ← customer اضافه شود.
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
- [ ] direct chat creation برای customer فقط با owner و accountantهای همان owner مجاز شود.
- [ ] direct chat creation از سمت owner/accountant به customerهای مجاز ممکن باشد.
- [ ] customer نتواند با customer دیگر یا user خارج از tree همان owner وارد چت شود.
- [ ] customer نتواند channel member شود.
- [ ] customer نتواند group جدید بسازد.
- [ ] group membership rule دقیق enforce شود: owner/accountants + همان customer و نه بیشتر از یک customer.

اصل طراحی این فاز:
- [ ] deny pathها باید در backend authoritative باشند، نه فقط در frontend.
- [ ] frontend فقط UI affordance را پنهان می‌کند؛ rule enforcement باید در router/service لایه chat انجام شود.
- [ ] چون customer کاربر عادی است، message model, conversation model, read-state, reactions و بقیه runtimeها reuse می‌شوند؛ فقط target graph محدود می‌شود.

validation phase:
- [ ] direct chat allow/deny matrix سبز شود.
- [ ] group creation/member mutation deny matrix سبز شود.
- [ ] notification/realtime behavior در chatهای مجاز intact بماند.

## 6. Phase 5 - منطق قیمت‌گذاری، سناریو محور و exhaustive

هدف:
مشخص‌کردن اینکه قیمت خام، قیمت تعدیل‌شده، نمایش owner-facing، و محاسبه fair-price چطور با حضور customer هم‌زمان درست بمانند.

### 5.1. اصل بنیادی

- [ ] قیمت ذخیره‌شده در DB برای آفر customer باید «قیمت خام actor» بماند.
- [ ] کمیسیون یک policy نمایشی/معاملاتی owner روی customer است، نه اینکه raw market data را overwrite کند.
- [ ] در نتیجه، adjusted price باید در response/runtime محاسبه شود نه اینکه جایگزین raw price در persistence شود.

### 5.2. دو قیمت هم‌زمان که باید همیشه از هم تفکیک شوند

- [ ] `raw_price`: همان قیمتی که customer یا owner واقعاً ثبت کرده است.
- [ ] `effective_market_price`: همان قیمتی که viewer مجاز باید در بازار ببیند.

این تفکیک مهم است چون:
- [ ] customer باید raw_price خودش را ببیند.
- [ ] owner و admin باید raw_price customer خود را ببینند.
- [ ] بقیه باید effective_market_price را ببینند.

### 5.3. سناریوی پایه فروش customer

اگر customer یک آفر فروش ثبت کند:
- [ ] raw_price همان عددی است که customer وارد کرده است.
- [ ] برای خود customer: همان raw_price نمایش داده می‌شود.
- [ ] برای owner همان customer: raw_price نمایش داده می‌شود + badge مشتری.
- [ ] برای admin: raw_price نمایش داده می‌شود + badge مشتری.
- [ ] برای هر viewer دیگر: `raw_price + commission` و سپس rounding rule اعمال می‌شود.

مثال:
- [ ] customer قیمت فروش خام `199600` ثبت می‌کند.
- [ ] commission = `0.5%`.
- [ ] adjusted = `199600 × 1.005 = 200598`.
- [ ] nearest-100 = `200600`.
- [ ] customer/owner/admin عدد `199600` را می‌بینند.
- [ ] سایر viewerها عدد `200600` را می‌بینند.

### 5.4. سناریوی پایه خرید customer

اگر customer یک آفر خرید ثبت کند:
- [ ] raw_price همان عدد واردشده است.
- [ ] برای customer: raw_price.
- [ ] برای owner/admin: raw_price + badge مشتری.
- [ ] برای سایر viewerها: `raw_price - commission` و سپس rounding rule.

مثال:
- [ ] customer قیمت خرید خام `200800` ثبت می‌کند.
- [ ] commission = `0.5%`.
- [ ] adjusted = `200800 × 0.995 = 199796`.
- [ ] nearest-100 = `199800`.

### 5.5. سناریوی midpoint در فروش

اگر adjusted price دقیقاً در midpoint بین دو مضرب 100 قرار بگیرد:
- [ ] در آفر فروش به بالا round می‌شود.

مثال:
- [ ] adjusted = `200550`.
- [ ] چون midpoint در فروش است، خروجی `200600` می‌شود.

### 5.6. سناریوی midpoint در خرید

- [ ] در آفر خرید midpoint باید به پایین round شود.

مثال:
- [ ] adjusted = `200550`.
- [ ] چون خرید است، خروجی `200500` می‌شود.

### 5.7. سناریوی non-midpoint نزدیک پایین

- [ ] adjusted = `200525` midpoint نیست، به نزدیک‌ترین مضرب 100 می‌رود.
- [ ] خروجی `200500` می‌شود.

### 5.8. وقتی owner خودش market را می‌بیند

- [ ] owner باید raw view ببیند، نه adjusted view.
- [ ] دلیل: owner policy maker است و باید بداند customer چه قیمت خامی ثبت کرده.
- [ ] owner نباید برای customerهای ownerهای دیگر identity اضافه ببیند.
- [ ] owner فقط customerهای خودش را با badge مشتری می‌بیند.

### 5.9. وقتی admin market را می‌بیند

- [ ] admin باید با owner همان visibility را نسبت به customer offer داشته باشد.
- [ ] یعنی raw price customer + badge مشتری برای همه customer offerها.
- [ ] این requirement باید دقیقاً با contract فعلی `PublicProfile` و `UserProfile` هم‌راستا باشد: admin از public profile بتواند همان تنظیمات/visual context را ببیند که owner در مدیریت user می‌بیند.

### 5.10. وقتی customer خودش market را می‌بیند

- [ ] فقط آفر خودش باید raw باشد.
- [ ] آفرهای بقیه market طبق همان rules عمومی market برای او نمایش داده شوند.
- [ ] customer نباید commission rate خودش را از UI استخراج کند.
- [ ] بنابراین حتی اگر raw/effective difference برای آفر خودش قابل مقایسه باشد، نباید label یا helper مستقیمی نرخ را افشا کند.

### 5.11. وقتی یک user عادی دیگر market را می‌بیند

- [ ] نباید بفهمد این آفر متعلق به customer است.
- [ ] نه badge، نه owner relation، نه management name، نه field اضافی UI.
- [ ] فقط effective_market_price را می‌بیند.

### 5.12. سناریوی owner editing customer commission

وقتی owner commission را از `0.5` به `0.8` تغییر می‌دهد:
- [ ] هیچ raw_price قبلی در DB تغییر نمی‌کند.
- [ ] همه آفرهای active customer از همان لحظه با commission جدید render می‌شوند.
- [ ] helper text فرم باید زنده آپدیت شود.
- [ ] competitive/fair-price logic باید بداند raw_price کدام است و effective price viewer-facing کدام است.

### 5.13. سناریوی fair-price calculation

این حساس‌ترین بخش است.

اصل نهایی:
- [ ] fair-price باید بر مبنای قیمت‌های قابل مقایسه محاسبه شود، نه قیمت‌های distort شده‌ی warning/excluded.
- [ ] چون raw_price مبنای واقعی offer owner/customer است، باید تصمیم فنی روشن شود که fair-price روی raw market basis محاسبه شود یا viewer-facing basis.
- [ ] با توجه به الگوی موجود پروژه و rollout اخیر warningها، direction پیشنهادی roadmap این است:
  - [ ] persistent fair-price basis = raw comparable prices
  - [ ] customer commission فقط لایه presentation/visibility و execution-facing adjustment باشد
  - [ ] offerهای flagged with aggressive warning همچنان از fair-price کنار گذاشته شوند

چرا این direction منطقی است:
- [ ] اگر fair-price بر مبنای adjusted customer prices محاسبه شود، owner policy خصوصی یک customer روی همه market analytics نشت می‌کند.
- [ ] اگر fair-price بر raw prices بماند، market baseline خالص‌تر و قابل مقایسه‌تر می‌ماند.

### 5.14. سناریوی trade execution روی offer customer

اگر user عادی روی آفر customer معامله بزند:
- [ ] system باید بداند raw stored offer price چیست.
- [ ] system باید مطابق contract execution مشخص کند counterpart-facing execution بر اساس raw business price انجام می‌شود یا adjusted presented price.
- [ ] چون customer برای دیگران adjusted price دیده شده، execution summary نباید mismatch فریبنده ایجاد کند.
- [ ] در implementation phase باید rule صریح انتخاب و در response/notification/history یکنواخت شود.

### 5.15. سناریوی customer trading restriction

اگر customer به‌صورت owner-level موقت restricted شده باشد:
- [ ] market list شاید هنوز قابل مشاهده باشد، اما create/execute trade باید بسته شود.
- [ ] messaging نباید خودکار بسته شود.
- [ ] UI باید شبیه user عادی restricted رفتار کند، نه surface کاملاً جدا.

### 5.16. validation phase برای قیمت‌گذاری

- [ ] pure math tests برای buy/sell, midpoint, non-midpoint, nearest-100.
- [ ] serializer tests برای owner/admin/customer/public viewer matrix.
- [ ] market e2e برای raw vs adjusted rendering.
- [ ] regression برای warning-excluded fair-price path.

## 7. Phase 6 - تاریخچه معامله، سناریو محور و exhaustive

هدف:
نمایش trade history به‌گونه‌ای که business relation واقعی حفظ شود، اما customer context فقط برای viewerهای مجاز دیده شود.

### 7.1. اصل بنیادی

- [ ] trade از دید actor ممکن است توسط customer انجام شده باشد.
- [ ] اما از دید business relation، owner مرجع principal آن رابطه است.
- [ ] history باید این دو لایه را قاطی نکند.

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
- [ ] اگر نمایش نام فعلی customer ممکن نیست، باید fallback stable وجود داشته باشد تا history مبهم نشود.

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
- [x] `PublicProfile.vue` اکنون برای ادمین می‌تواند `UserProfile.vue` را در modal باز کند.
- [x] `AdminView.vue` نیز همان `UserProfile.vue` را به‌عنوان surface مدیریت user استفاده می‌کند.

نتیجه اجرایی برای customer:
- [ ] customer section owner-only در `PublicProfile.vue` اضافه می‌شود.
- [ ] admin هم باید در همان profile surface customer visibility را ببیند، ولی actionهای owner-only را نه.
- [ ] اگر admin از public profile وارد modal تنظیمات کاربر شد، همان data contract باید customer-related fields را هم در `UserProfile.vue` نشان دهد.
- [ ] بنابراین customer settings duplication بین `PublicProfile.vue` و `AdminView.vue` ممنوع است؛ منبع مدیریت باید همان `UserProfile.vue` بماند.

خروجی‌های لازم:
- [ ] owner customer manager modal / section
- [ ] admin-visible customer summary in public profile
- [ ] `UserProfile.vue` support for `max_customers` and customer-aware admin controls
- [ ] route/state sync بین public profile و admin user settings برای customer surfaces

validation phase:
- [ ] owner profile customer CRUD UI سبز شود.
- [ ] admin public-profile → user-settings handoff سبز شود.
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
- [ ] customer creates sell offer
- [ ] owner sees raw price + badge
- [ ] normal viewer sees adjusted anonymous price
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

- [ ] execution contract نهایی بین raw displayed price و trade confirmation payload دقیقاً چگونه در API/history/notification encode می‌شود؟
- [ ] fallback naming برای history وقتی customer relation بعداً deleted/revoked می‌شود چیست؟
- [ ] آیا market fair-price persistence دقیقاً روی raw prices خواهد ماند یا در بعضی viewer-facing analytic surfaces adjusted projection جداگانه لازم است؟

این موارد challenge جدید محصولی نیستند؛ فقط detailهای implementation-level هستند و باید در phaseهای 4 تا 8 به‌صورت صریح بسته شوند.