# Roadmap اجرای فیچر حسابدار

این roadmap فقط بعد از بسته‌شدن challengeهای [ACCOUNTANT_FEATURE_CHECKLIST.md](ACCOUNTANT_FEATURE_CHECKLIST.md) ساخته شده است. این سند challenge جدید باز نمی‌کند؛ فقط ترتیب اجرای دقیق، وابستگی‌ها، validation، و rollback surface را مشخص می‌کند.

## Snapshot وضعیت فعلی تا 2026-05-13

- [x] Phase 1 از نظر data foundation، migration، sync/change-log و actor fields بسته شده است.
- [x] Phase 2 از نظر seamهای relation lifecycle، effective owner resolution و audience fanout بسته شده است.
- [x] Phase 3 از نظر owner-facing accountant APIs، accountant-aware register/session policy و bot deny branching بسته شده است.
- [x] Phase 4 از نظر delegated offer/trade write/read، actor audit، و fanout/privacy validation بسته شده است.
- [x] Phase 5 از نظر contractهای messenger/public profile و consumerهای اصلی بسته شده است؛ `users_public` accountantها را به owner principal resolve می‌کند، همه consumerهای chat/profile از جمله `ChatNewConversationModal.vue` relation-aware شده‌اند، و deny pathهای accountant برای direct chat جدید و group creation در messenger/backend سبز شده‌اند.
- [ ] Phase 6 به‌صورت partial پیش رفته است؛ modal مدیریت حسابدار owner اکنون create/list/edit/cancel pending را پوشش می‌دهد، section حسابداران در profile/public profile اضافه شده، edit contract روی `duty_description` محدود شده، و stateهای واضح pending/active به‌همراه expiry timer به UI اضافه شده‌اند؛ اما active unlink و بعضی UX/lifecycle ruleهای نهایی هنوز باقی مانده‌اند.
- [ ] Phase 7 هنوز شروع نشده است.
- [ ] Phase 8 هنوز شروع نشده است.

## 0. قواعد اجرای roadmap

- [ ] اجرای roadmap به ترتیب phaseها انجام شود و phase بعدی قبل از سبز شدن validation phase قبلی شروع نشود.
- [ ] همه تغییرات schema به‌صورت additive شروع شوند؛ drop/cleanup فقط در phase پایانی انجام شود.
- [ ] در تمام phaseها invariant اصلی حفظ شود: `effective_owner` هویت business و `actor_user_id` هویت عامل واقعی است.
- [ ] در طول توسعه و استقرار این فیچر، به‌دلیل single-server development mode فقط از `make foreign` استفاده شود و `make up` اجرا نشود.

## 1. قراردادهای نهایی که roadmap بر آن‌ها تکیه می‌کند

- [ ] model اصلی این فیچر `AccountantRelation` با جدول `accountant_relations` است.
- [ ] bind ثبت‌نام relation به invitation از طریق `invitation_token` انجام می‌شود، نه FK مستقیم به `invitations.id`.
- [ ] field audit یکنواخت برای actor واقعی روی سطوح delegated با نام `actor_user_id` استفاده می‌شود.
- [ ] `User.account_name` هویت سراسری immutable user است و `relation_display_name` نام immutable رابطه‌ای owner ← accountant.
- [ ] accountant در فاز اول bot access ندارد، direct chat دلخواه شروع نمی‌کند، و group جدید نمی‌سازد.
- [ ] authority همه business actionهای delegated از `effective_owner.home_server` گرفته می‌شود.

## 2. فازبندی کلان

### Phase 1 - Data Foundation

هدف:
بستن schema پایه، sync/change-log، و settings بدون تغییر رفتار UI.

خروجی‌های لازم:
- [x] مدل `AccountantRelation` در [models](models) اضافه شد.
- [x] migration جدید برای `accountant_relations` ساخته شد.
- [x] indexها و uniquenessهای نهایی relation اعمال شدند.
- [x] limit پیش‌فرض تعداد حسابداران به‌صورت per-user روی `users.max_accountants` با default `3` اضافه شد.
- [x] field `actor_user_id` به جدول‌های لازم delegated اضافه شد.

حداقل جدول‌ها/فیلدهای جدید:
- [x] `accountant_relations.id`
- [x] `accountant_relations.owner_user_id`
- [x] `accountant_relations.accountant_user_id`
- [x] `accountant_relations.invitation_token`
- [x] `accountant_relations.global_account_name`
- [x] `accountant_relations.relation_display_name`
- [x] `accountant_relations.duty_description`
- [x] `accountant_relations.mobile_number`
- [x] `accountant_relations.status`
- [x] `accountant_relations.expires_at`
- [x] `accountant_relations.activated_at`
- [x] `accountant_relations.deleted_at`
- [x] `users.max_accountants`
- [x] `offers.actor_user_id`
- [x] `trades.actor_user_id`
- [x] `messages.actor_user_id`

فایل‌های درگیر اصلی:
- [x] [models](models)
- [x] [migrations](migrations)
- [x] [models/user.py](models/user.py)
- [x] [schemas.py](schemas.py)
- [x] [api/routers/users.py](api/routers/users.py)

sync/change-log:
- [x] `accountant_relations` به `TABLE_ORDER` در [api/routers/sync.py](api/routers/sync.py) اضافه شد.
- [x] upsert/delete logic برای `accountant_relations` در [api/routers/sync.py](api/routers/sync.py) از مسیر generic model mapping فعال شد.
- [x] event listenerهای relation در [core/events.py](core/events.py) اضافه شدند.
- [x] payload sync جدول‌های delegated برای `actor_user_id` به‌روزرسانی شد.

validation phase:
- [x] `alembic heads` و migration smoke repository برای head جدید سبز شد.
- [x] focused sync/event tests برای `accountant_relations` و فیلدهای actor سبز شد.
- [x] entrypoint/import smoke بعد از foundation changes سبز ماند.

rollback surface:
- [ ] اگر Phase 1 مشکل داشت، UI/accountant routes هنوز expose نشده باشند و rollback فقط در سطح code disable انجام شود.
- [ ] schema جدید additive باقی بماند و فوراً drop نشود.

### Phase 2 - Shared Backend Seams

هدف:
ساخت seamهای مشترک تا accountant logic در routerها duplicate نشود.

خروجی‌های لازم:
- [x] service جدید برای relation lifecycle ساخته شود.
- [x] helper مشترک `resolve_effective_owner_actor(...)` یا معادل آن ساخته شود.
- [x] helper مشترک برای `list_active_accountants_for_owner(...)` ساخته شود.
- [x] helper مشترک برای `validate_accountant_capacity(...)` ساخته شود.
- [x] helper مشترک برای trade notification audience fanout ساخته شود.

فایل‌های درگیر اصلی:
- [x] [core/services](core/services)
- [x] [api/deps.py](api/deps.py)
- [x] [core/utils.py](core/utils.py)

validation phase:
- [x] unit tests سرویس relation سبز شود.
- [x] unit tests resolver owner/actor سبز شود.
- [x] unit tests audience fanout سبز شود.

rollback surface:
- [ ] seamهای جدید فقط additive باشند و behavior قدیمی را تا قبل از phaseهای بعدی نشکنند.

### Phase 3 - Auth, Register, Session, Bot Branching

هدف:
branch کردن onboarding و session policy برای accountant.

خروجی‌های لازم:
- [x] owner-facing accountant create/list/cancel APIs اضافه شود.
- [x] `register_otp_request`, `register_otp_verify`, `register_complete` در [api/routers/auth.py](api/routers/auth.py) accountant-aware شوند.
- [x] `register_complete` هنگام accountant registration، `has_bot_access=False` را enforce کند.
- [x] relation pending بر اساس `invitation_token` به user تازه‌ساخته bind شود.
- [x] `core/services/session_service.py` accountant را در effective max session = 1 enforce کند.
- [x] update user admin path هر تغییر `max_sessions` برای accountant را clamp یا reject کند.
- [x] `bot/handlers/start.py` و [bot/handlers/link_account.py](bot/handlers/link_account.py) path accountant را از bot-enable flow جدا کنند.

فایل‌های درگیر اصلی:
- [x] [api/routers/auth.py](api/routers/auth.py)
- [x] [api/routers/invitations.py](api/routers/invitations.py) یا router/accountant جدید
- [x] [core/services/session_service.py](core/services/session_service.py)
- [x] [api/routers/users.py](api/routers/users.py)
- [x] [bot/handlers/start.py](bot/handlers/start.py)
- [x] [bot/handlers/link_account.py](bot/handlers/link_account.py)

validation phase:
- [x] owner accountant create/cancel/pending-expire tests سبز شود.
- [x] accountant registration flow با web-only session سبز شود.
- [x] second-login approval flow برای accountant سبز شود.
- [x] bot `/start` و `/link` برای accountant hard-deny/regression tests سبز شود.

rollback surface:
- [ ] اگر bot/accountant branch مشکل داشت، accountant creation endpoint موقتاً disable شود اما relation data حفظ شود.

### Phase 4 - Delegated Business Actions

هدف:
اعمال `effective_owner` و `actor_user_id` روی offer/trade و side effectهای آن.

وضعیت فعلی:
- create/expire offer، execute trade، و read/historyهای offer/trade روی owner principal هم‌راستا شده‌اند.
- `actor_user_id` روی create/execute delegated path ثبت می‌شود.
- trade notification audience برای owner/accountant fanout شده است.
- close-out phase هنوز به بستن validationهای باقیمانده و مرور seamهای جانبی وابسته است.

خروجی‌های لازم:
- [x] create/expire offer path delegated-aware شود.
- [x] execute trade path delegated-aware شود.
- [x] `offers.user_id` و `trades.offer_user_id/responder_user_id` principal owner را نگه دارند.
- [x] `actor_user_id` روی surfaceهای delegated ذخیره شود.
- [x] trade fanout helper owner + active accountants هر دو سمت را notify کند.
- [x] counterpart-facing notification/realtime فقط owner principal را نشان دهد.
- [x] read/history surfaceهای offer/trade هم owner principal را به‌عنوان identity موثر ببینند.

فایل‌های درگیر اصلی:
- [x] [api/routers/offers.py](api/routers/offers.py)
- [x] [api/routers/trades.py](api/routers/trades.py)
- [ ] [core/services/trade_service.py](core/services/trade_service.py)
- [ ] [core/notifications.py](core/notifications.py)

validation phase:
- [x] create offer by accountant تحت owner principal سبز شود.
- [x] execute trade by accountant با audit actor سبز شود.
- [x] offer/trade read history تحت owner principal سبز شود.
- [ ] notification fanout to owner + accountants سبز شود.
- [ ] counterpart-facing payload privacy tests سبز شود.

rollback surface:
- [ ] اگر delegated trade path unstable شد، accountant write actions موقتاً disable شوند ولی read-only surfaces بمانند.

### Phase 5 - Messenger and Public Profile Contracts

هدف:
تغییر contractهای backend و frontend برای relation-aware display و owner-resolved profile navigation.

وضعیت فعلی:
- شروع شده است.
- [api/routers/users_public.py](api/routers/users_public.py) برای read/search accountant active را به owner principal resolve می‌کند و metadata additive برمی‌گرداند.
- consumerهای chat/profile اکنون relation-aware شده‌اند و search modal شروع مکالمه هم owner-resolved accountant hit را با context مناسب نمایش می‌دهد.

خروجی‌های لازم:
- [x] `users_public` owner-resolve behavior اضافه شود.
- [x] `UserPublicRead` با metadata لازم مثل `resolved_from_accountant_id` گسترش پیدا کند.
- [x] `ConversationRead`, `MessageRead`, و payloadهای realtime با fieldهای additive برای display/profile target گسترش پیدا کنند.
- [x] `chat_service` projection relation-aware display name تولید کند.
- [x] accountant direct chat initiation در UI/backend deny شود.
- [x] accountant group creation در UI/backend deny شود.

فایل‌های درگیر اصلی:
- [x] [api/routers/users_public.py](api/routers/users_public.py)
- [x] [schemas.py](schemas.py)
- [x] [api/routers/chat_schemas.py](api/routers/chat_schemas.py)
- [x] [core/services/chat_service.py](core/services/chat_service.py)
- [x] [api/routers/chat.py](api/routers/chat.py)

frontend contract consumers:
- [x] [frontend/src/components/ChatView.vue](frontend/src/components/ChatView.vue)
- [x] [frontend/src/components/chat/ChatConversationList.vue](frontend/src/components/chat/ChatConversationList.vue)
- [x] [frontend/src/components/chat/ChatMessageItem.vue](frontend/src/components/chat/ChatMessageItem.vue)
- [x] [frontend/src/components/chat/ChatNewConversationModal.vue](frontend/src/components/chat/ChatNewConversationModal.vue)
- [x] [frontend/src/views/PublicProfileView.vue](frontend/src/views/PublicProfileView.vue)
- [x] [frontend/src/components/PublicProfile.vue](frontend/src/components/PublicProfile.vue)

validation phase:
- [x] owner-resolved public profile navigation سبز شود.
- [x] accountant highlight query/state سبز شود.
- [x] conversation list/header/message labels relation-aware سبز شوند.
- [x] accountant direct-chat deny path سبز شود.
- [x] accountant group-create deny path سبز شود.

rollback surface:
- [ ] additive schema fields fallback داشته باشند تا فرانت قدیمی هنوز با fieldهای legacy کار کند.

### Phase 6 - Owner Management UI

هدف:
دادن UI کامل owner برای مدیریت حسابداران بدون reuse ناقص UI دعوت‌نامه‌ی عمومی.

وضعیت فعلی:
- modal اختصاصی owner manager از [frontend/src/components/PublicProfile.vue](frontend/src/components/PublicProfile.vue) باز می‌شود و create/list/edit/cancel pending را پوشش می‌دهد.
- stateهای واضح pending/active و expiry timer به modal اضافه شده‌اند، اما active unlink و بعضی lifecycle ruleهای نهایی هنوز باقی است.

خروجی‌های لازم:
- [x] section حسابداران در owner profile و public owner profile اضافه شود.
- [x] لیست pending/active با stateهای واضح و expiry timer اضافه شود.
- [x] create accountant form اختصاصی با `relation_display_name`, `account_name`, `mobile_number`, `duty_description` اضافه شود.
- [x] owner بتواند فقط `duty_description` را ویرایش کند.
- [ ] unlink/cancel controls برای pending/active اضافه شود.
- [x] admin UI تنظیم `max_accountants` به‌ازای هر owner را در [frontend/src/components/UserProfile.vue](frontend/src/components/UserProfile.vue) انجام دهد و session cap accountant را editable نکند.

فایل‌های درگیر اصلی:
- [ ] [frontend/src/views/ProfileView.vue](frontend/src/views/ProfileView.vue)
- [x] [frontend/src/components/PublicProfile.vue](frontend/src/components/PublicProfile.vue)
- [ ] [frontend/src/components/CreateInvitationView.vue](frontend/src/components/CreateInvitationView.vue) یا component جدید اختصاصی accountant
- [x] [frontend/src/components/OwnerAccountantManagerModal.vue](frontend/src/components/OwnerAccountantManagerModal.vue)
- [ ] [frontend/src/components/UserProfile.vue](frontend/src/components/UserProfile.vue)

validation phase:
- [ ] owner CRUD accountant UI سبز شود.
- [ ] pending expiry/cancel UI سبز شود.
- [x] description-only edit UI سبز شود.
- [ ] admin cap read-only/accountant session clamp UI سبز شود.

rollback surface:
- [ ] اگر UI ناپایدار بود، entry pointهای accountant manager hide شوند و backend data intact بماند.

### Phase 7 - Deletion, Lifecycle, and Sync Convergence

هدف:
بستن lifecycleهای نهایی و جلوگیری از orphan یا drift بین سرورها.

خروجی‌های لازم:
- [ ] pending revoke flow کامل شود.
- [ ] active unlink flow از `user_deletion_service` reuse کند.
- [ ] owner delete cascade accountant relations و accountant users را ببندد.
- [ ] resync/change-log drift cases برای accountant relations و actor fields پوشش داده شود.

فایل‌های درگیر اصلی:
- [ ] [core/services/user_deletion_service.py](core/services/user_deletion_service.py)
- [ ] [api/routers/sync.py](api/routers/sync.py)
- [ ] [core/events.py](core/events.py)
- [ ] relation service جدید

validation phase:
- [ ] unlink active accountant tests سبز شود.
- [ ] owner delete cascade tests سبز شود.
- [ ] sync replay/resync tests سبز شود.

rollback surface:
- [ ] deletion flows باید idempotent بمانند تا retry/replay crash نکند.

### Phase 8 - Test Matrix, Deploy, and Release Gate

هدف:
بستن non-regression قبل از release.

backend test scope:
- [ ] relation lifecycle service tests
- [ ] auth/register/accountant branch tests
- [ ] session approval/single-session tests
- [ ] offer/trade delegated actor tests
- [ ] users_public resolve tests
- [ ] sync receive/resync/accountant relation tests
- [ ] deletion cascade tests

frontend test scope:
- [ ] component/unit tests for owner accountant manager
- [ ] profile redirect/highlight tests
- [ ] chat contract display tests
- [ ] direct-chat deny tests
- [ ] e2e owner create → pending → activate → view → unlink flow

bot test scope:
- [ ] `/start` deny for accountant
- [ ] `/link` deny/no bot-enable for accountant

deploy order:
- [ ] migration image/build آماده شود.
- [ ] migrations اجرا شود.
- [ ] backend deploy شود.
- [ ] bot deploy شود.
- [ ] frontend deploy آخر انجام شود.
- [ ] در این repo فقط `make foreign` برای استقرار استفاده شود.

release gate:
- [ ] backend focused suites سبز شوند.
- [ ] frontend unit + Playwright suites سبز شوند.
- [ ] sync smoke سبز شود.
- [ ] manual sanity روی owner/accountant happy path سبز شود.

rollback strategy:
- [ ] rollback در فاز اول با hide کردن UI و accountant routes انجام شود، نه drop فوری schema.
- [ ] اگر لازم شد write paths accountant خاموش شوند و read-only/audit data حفظ شود.
- [ ] migrationهای destructive تا بعد از یک release پایدار انجام نشوند.

## 3. ترتیب وابستگی اجباری

- [ ] Phase 1 قبل از همه phaseهای دیگر.
- [ ] Phase 2 بعد از Phase 1 و قبل از هر branch در auth/offers/trades/chat.
- [ ] Phase 3 و Phase 4 فقط بعد از آماده‌شدن seamهای Phase 2.
- [ ] Phase 5 فقط بعد از بسته‌شدن contractهای backend در Phase 3 و 4.
- [ ] Phase 6 فقط بعد از آماده‌شدن API/contractهای backend.
- [ ] Phase 7 بعد از استقرار همه write pathها.
- [ ] Phase 8 gate نهایی قبل از release.

## 4. Definition of Done

- [ ] owner می‌تواند accountant pending بسازد، ببیند، لغو کند، و بعد از activation آن را مدیریت کند.
- [ ] accountant بدون bot access و با single-session policy وارد webapp می‌شود.
- [ ] accountant actionها تحت owner principal ثبت می‌شوند و `actor_user_id` audit از دست نمی‌رود.
- [ ] profile/messenger/trade history همه owner-resolved و relation-aware هستند.
- [ ] trade/create/execute notifications به owner و active accountants درست fanout می‌شوند.
- [ ] unlink و owner delete orphan ایجاد نمی‌کنند.
- [ ] sync/resync accountant data را بین سرورها converge می‌کند.
- [ ] test gate و deploy/rollback path برای release آماده‌اند.