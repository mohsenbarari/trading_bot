# Local Assistant Context

## Purpose
This file is a curated, product-grounded context layer for a local AI assistant.
It gives the assistant a high-signal map of the repository before it reads the full packed corpus.

Use this file when the assistant should answer questions about:
- what the product does
- how the implemented user flows work
- where the source of truth lives in code
- which behaviors are intentional and must not be misread as bugs

This file is intentionally more precise than a generic project overview and intentionally much smaller than the full repository pack.

## What The Product Is
Gold/Coin Trading Bot is a Persian-first trading system with three first-class surfaces:
- FastAPI backend for auth, invitations, users, commodities, offers, trades, chat, notifications, realtime, blocks, sync, and settings
- aiogram Telegram bot for onboarding, trading, admin operations, and user controls
- Vue 3 + TypeScript PWA for dashboard, market activity, messenger, profile, notifications, admin, and web registration

Primary entry points:
- `main.py`
- `run_bot.py`
- `frontend/src/main.ts`
- `frontend/src/router/index.ts`

## Frontend Surface Map
The current web/PWA routes are:
- `/` -> authenticated dashboard
- `/setup-password` -> authenticated forced password setup flow
- `/login` -> login and OTP entry
- `/market` -> authenticated trading surface
- `/chat` -> authenticated messenger
- `/profile` -> authenticated profile and session management
- `/admin` -> authenticated admin area, admin-only
- `/i/:code` -> invitation landing page
- `/register` -> web registration flow
- `/notifications` -> authenticated notification history

Source of truth:
- `frontend/src/router/index.ts`

Important frontend routing behavior:
- `authGuard` is the gatekeeper for authenticated routes.
- The router hard-reloads the page on chunk-load failures so stale PWA bundles can self-recover after deploys.

## Backend API Map
The backend mounts these routers under the `/api` prefix:
- `/api/auth`
- `/api/invitations`
- `/api/commodities`
- `/api/users`
- `/api/notifications`
- `/api/trading-settings`
- `/api/offers`
- `/api/trades`
- `/api/realtime`
- `/api/users-public`
- `/api/chat`
- `/api/blocks`
- `/api/sync`
- `/api/sessions`
- `/api/config` (public config endpoint)

Source of truth:
- `main.py`

`/api/config` returns non-sensitive public settings such as `bot_username` and `frontend_url`.

## Major User Flows

### Authentication And Session Lifecycle
Authentication is OTP-first, not password-first.

Implemented auth paths include:
- `POST /api/auth/request-otp`
- `POST /api/auth/resend-otp-sms`
- `POST /api/auth/verify-otp`
- `POST /api/auth/webapp-login`
- `POST /api/auth/refresh`
- `GET /api/auth/me`
- `POST /api/auth/register-otp-request`
- `POST /api/auth/register-otp-verify`
- `POST /api/auth/register-complete`
- `POST /api/auth/setup-password`

Implemented session-management paths include:
- `GET /api/sessions/login-requests/pending`
- `POST /api/sessions/login-requests/{request_id}/approve`
- `POST /api/sessions/login-requests/{request_id}/reject`
- `GET /api/sessions/login-requests/{request_id}/status`
- `GET /api/sessions/active`
- `DELETE /api/sessions/{session_id}`
- `POST /api/sessions/logout-all`

Behavioral rules:
- web registration is invitation-based
- OTP values are stored in Redis and must be deleted after successful verification
- refresh tokens are materially longer-lived than access tokens
- session logic supports primary and secondary devices
- pending login approvals are meaningful only for the primary device
- session suspension exists as a soft re-authentication path rather than forcing immediate hard logout under stale-session conditions

Source of truth:
- `api/routers/auth.py`
- `api/routers/sessions.py`
- `core/services/session_service.py`
- `core/session_expiry.py`
- `frontend/src/views/LoginView.vue`
- `frontend/src/views/SetupPassword.vue`
- `frontend/src/views/ProfileView.vue`

Do not treat developer-only login shortcuts as end-user product features.

### Invitations And Onboarding
Invitations are admin-created onboarding credentials.

Implemented invitation paths include:
- `POST /api/invitations/`
- `GET /api/invitations/lookup/{short_code}`
- `GET /api/invitations/validate/{token}`

Behavioral rules:
- invitations are tied to account name, mobile number, and role
- active invitations can be reused instead of creating duplicates
- short codes exist for compact landing links
- SMS payload length matters, so shorter landing links are preferred operationally

Source of truth:
- `api/routers/invitations.py`
- `bot/handlers/admin.py`
- `bot/handlers/start.py`
- `frontend/src/components/CreateInvitationView.vue`
- `frontend/src/views/InviteLanding.vue`
- `frontend/src/views/WebRegister.vue`

### Trading, Offers, And Execution
Trading is built around user-created offers and explicit trade execution against those offers.

Implemented offer paths include:
- `POST /api/offers/`
- `GET /api/offers/`
- `GET /api/offers/my`
- `DELETE /api/offers/{offer_id}`
- `POST /api/offers/parse`

Implemented trade paths include:
- `POST /api/trades/`
- `GET /api/trades/my`
- `GET /api/trades/with/{user_id}`

Behavioral rules:
- offers are buy/sell records, not informal chat messages
- offers support quantity, price, notes, and optional lot-size structure
- offer parsing exists as a backend capability, not just a frontend convenience
- executing a trade is stateful and protected by locking/versioning logic
- expiring an offer is owner-restricted and can also update Telegram channel controls
- current trading settings influence offer validation and expiry behavior

Source of truth:
- `api/routers/offers.py`
- `api/routers/trades.py`
- `core/services/trade_service.py`
- `core/trading_settings.py`
- `bot/handlers/trade_create.py`
- `bot/handlers/trade_execute.py`
- `bot/handlers/trade_manage.py`
- `frontend/src/views/MarketView.vue`
- `frontend/src/components/TradingView.vue`
- `frontend/src/components/OffersList.vue`

### Commodities And Trading Settings
Commodities are centrally managed and can have aliases.

Implemented commodity paths include:
- `GET /api/commodities/`
- `GET /api/commodities/{commodity_id}`
- `POST /api/commodities/`
- `PUT /api/commodities/{commodity_id}`
- `DELETE /api/commodities/{commodity_id}`

Implemented trading-settings paths include:
- `GET /api/trading-settings/`
- `PUT /api/trading-settings/`
- `POST /api/trading-settings/reset`

Behavioral rules:
- deleting a commodity with active offers is intentionally blocked
- settings are dynamic runtime configuration, not just static constants
- commodity reads are cache-backed

Source of truth:
- `api/routers/commodities.py`
- `api/routers/trading_settings.py`
- `core/trading_settings.py`
- `bot/handlers/admin_commodities.py`
- `frontend/src/components/CommodityManager.vue`
- `frontend/src/components/TradingSettings.vue`

### Messenger And Media
Messenger is a core product surface, not a simple auxiliary chat.

Implemented chat paths include:
- `GET /api/chat/conversations`
- `GET /api/chat/search`
- `GET /api/chat/messages/{user_id}`
- `POST /api/chat/typing`
- `POST /api/chat/send`
- `PUT /api/chat/messages/{message_id}`
- `DELETE /api/chat/messages/{message_id}`
- `POST /api/chat/read/{user_id}`
- `GET /api/chat/poll`
- `GET /api/chat/stickers`
- `POST /api/chat/upload-media`
- `GET /api/chat/files/{file_id}`

Implemented public-user discovery paths used by chat include:
- `GET /api/users-public/search`
- `GET /api/users-public/{user_id}`

Behavioral rules:
- chat supports text, image, video, voice, sticker, document, forwarded, reply, and location-oriented flows
- message history supports `before_id` pagination and `around_id` context loading
- editing and deleting messages are time-limited product behaviors
- uploaded media is stored as chat files and served through authenticated file endpoints
- media is intentionally not auto-downloaded on load; previews and later hydration are part of the design
- frontend downloads/uploads are designed around real progress reporting, cancellation, weak-device resilience, and background continuation
- albums are explicit batch constructs driven by `album_id` and `album_index`, not by grouping adjacent images
- messenger state orchestration lives in `ChatView.vue`; UI and gestures are delegated to subcomponents and composables

Source of truth:
- `api/routers/chat.py`
- `api/routers/users_public.py`
- `models/message.py`
- `models/chat_file.py`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/`
- `frontend/src/composables/chat/`
- `frontend/src/services/chatUploadBackground.ts`
- `frontend/src/views/MessengerView.vue`

Chat/media precision notes that matter for grounding:
- keep one authoritative EXIF-safe image pipeline in `frontend/src/composables/chat/useChatMedia.ts`
- edited images from `ImageEditorModal.vue` are intentional final assets and should not be reinterpreted as needing another resize pipeline
- album grouping must use metadata, not adjacency
- uncached media/lightbox behavior often depends on authenticated file URLs rather than tiny embedded thumbnails
- no-auto-download is intentional, not an incomplete implementation

Reference notes:
- `/memories/repo/chat-media-pipeline.md`
- `/memories/repo/chat-upload-gotchas.md`
- `/memories/repo/chat-album-grouping.md`

### User Blocks, Restrictions, And Admin Limits
Blocking and admin restrictions are different concepts.

Implemented block paths include:
- `GET /api/blocks/status`
- `GET /api/blocks/`
- `POST /api/blocks/{user_id}`
- `DELETE /api/blocks/{user_id}`
- `GET /api/blocks/check/{user_id}`
- `GET /api/blocks/search`
- `GET /api/blocks/is-blocked-by/{user_id}`

Implemented admin-user management paths include user CRUD and limitation updates under `/api/users`.

Behavioral rules:
- a user may or may not have permission to block others
- block capacity is bounded by `max_blocked_users`
- trading restrictions and usage limitations are admin-controlled user state, distinct from peer blocking
- soft deletion is the deletion strategy for users
- deleted users are intentionally excluded from normal active-user listings by default

Source of truth:
- `api/routers/blocks.py`
- `core/services/block_service.py`
- `api/routers/users.py`
- `models/user.py`
- `models/user_block.py`
- `bot/handlers/block_manage.py`
- `bot/handlers/admin_users.py`
- `frontend/src/components/UserProfile.vue`
- `frontend/src/components/UserManager.vue`

### Notifications And Realtime
Notifications are both persisted history and realtime delivery.

Implemented notification paths include:
- `GET /api/notifications/unread-count`
- `GET /api/notifications/unread`
- `GET /api/notifications/`
- `PATCH /api/notifications/{notification_id}/read`
- `POST /api/notifications/mark-all-read`
- `DELETE /api/notifications/{notification_id}`
- `GET /api/notifications/stream`

Implemented realtime path includes:
- `WS /api/realtime/ws?token=<jwt>`

Behavioral rules:
- realtime uses Redis pub/sub under the hood
- notification payloads and generic event payloads are sanitized before broadcast
- frontend unread chat badge logic is chat-count oriented, not raw unread-message-count oriented
- notification history and live toasts are separate but connected concerns

Source of truth:
- `api/routers/notifications.py`
- `api/routers/realtime.py`
- `core/notifications.py`
- `frontend/src/stores/notifications.ts`
- `frontend/src/components/AppToasts.vue`
- `frontend/src/views/NotificationsView.vue`

### Sync And Cross-Server Replication
Sync exists as a system behavior, but it is mainly operational rather than end-user-facing.

Implemented sync behaviors include:
- signed receive endpoint
- dependency-ordered application of table changes
- fallback merge behavior for some natural-key conflicts
- deferred retry behavior for FK ordering problems

Source of truth:
- `api/routers/sync.py`
- `core/events.py`
- `core/sync_push.py`
- `core/sync_worker.py`
- `models/change_log.py`
- `models/sync_block.py`

The local assistant should mention sync only when the user is clearly asking about data replication, missing cross-server state, or operational consistency.

## Bot Capability Map
High-value bot handlers:
- `bot/handlers/start.py` -> `/start`, invitation token entry, registration FSM, channel trade callbacks
- `bot/handlers/link_account.py` -> `/link` account linking
- `bot/handlers/panel.py` -> user/admin panels and trading settings interactions
- `bot/handlers/trade_create.py` -> offer creation FSM
- `bot/handlers/trade_execute.py` -> channel-execution entry point
- `bot/handlers/trade_manage.py` -> offer expiry actions from bot side
- `bot/handlers/trade_history.py` -> history display and export
- `bot/handlers/admin.py` -> invitation creation FSM
- `bot/handlers/admin_commodities.py` -> commodity and alias CRUD
- `bot/handlers/admin_users.py` -> user management, blocking, limits, roles, delete
- `bot/handlers/block_manage.py` -> user-facing block management

Use bot handlers as the source of truth for Telegram-specific UX, not for core business rules unless the handler itself computes the rule.

## Core Domain Entities
High-value entities and what they represent:
- `users` -> identity, role, address, bot access, soft-delete state, block permissions, trading restrictions, usage limits, counters
- `invitations` -> onboarding credentials with token, short code, creator, expiration, and role
- `user_sessions` / `session_login_requests` -> multi-device auth state, primary/secondary device logic, approval flows
- `offers` -> buy/sell trading intent with quantity, remaining quantity, price, lot sizing, status, and optimistic locking
- `trades` -> concrete executions against offers, trade numbering, participants, quantity, price, and status
- `messages` -> chat messages with sender/receiver, forwarded/reply links, deletion/edit history, media payload semantics
- `conversations` -> per-user thread state, last message, unread counters
- `notifications` -> persistent user notifications with level and category
- `user_blocks` -> directional user block edges
- `chat_files` -> media storage metadata and downloadable payload pointers
- `commodities` / `commodity_aliases` -> tradeable commodity names and aliases
- `trading_settings` -> live system constraints and defaults
- `change_log` / `sync_blocks` -> replication bookkeeping

Primary model files:
- `models/user.py`
- `models/session.py`
- `models/offer.py`
- `models/trade.py`
- `models/message.py`
- `models/conversation.py`
- `models/notification.py`
- `models/chat_file.py`
- `models/commodity.py`
- `models/trading_setting.py`
- `models/change_log.py`
- `models/sync_block.py`

## Roles
Current user roles:
- `WATCH`
- `STANDARD`
- `POLICE`
- `MIDDLE_MANAGER`
- `SUPER_ADMIN`

Role semantics are enforced in backend dependencies and admin tooling, not only by labels shown in UI.

## High-Signal Behavioral Rules
These are important implementation truths that a local assistant should preserve:
- soft delete is intentional for users; destructive deletion is not the normal behavior
- `apiBaseUrl = ''` on the frontend is valid same-origin configuration, not a missing config
- Persian numerals and Jalali/Iran-time presentation are part of the product behavior
- notification, session, and media code are more complex than average because the product targets mobile/PWA reliability under weak-device and unstable-network conditions
- chat uploads/downloads intentionally favor progress-aware flows, resumability, and safe fallback behavior
- album semantics are batch-driven metadata, not inferred from consecutive media layout
- message/file behavior depends on authenticated media endpoints and local cache hydration, so tiny thumbnails are often placeholders rather than the final source of truth

## Retrieval Playbook
When answering a question, use this priority order unless the topic clearly demands otherwise:
1. router/service code that computes the behavior
2. model/schema definitions that constrain the shape
3. frontend or bot code that exposes the behavior to the user
4. curated repo memory notes for fragile chat/media behavior
5. historical change logs only if current source code is ambiguous about why something exists

Topic-to-file routing:
- auth/login/session questions -> `api/routers/auth.py`, `api/routers/sessions.py`, `core/services/session_service.py`, `frontend/src/views/LoginView.vue`, `frontend/src/views/ProfileView.vue`
- invitation/registration questions -> `api/routers/invitations.py`, `api/routers/auth.py`, `frontend/src/views/InviteLanding.vue`, `frontend/src/views/WebRegister.vue`, `bot/handlers/start.py`
- offer/trade questions -> `api/routers/offers.py`, `api/routers/trades.py`, `core/services/trade_service.py`, `frontend/src/views/MarketView.vue`, `bot/handlers/trade_create.py`
- commodity/settings questions -> `api/routers/commodities.py`, `api/routers/trading_settings.py`, `core/trading_settings.py`, admin UI/components
- chat/media questions -> `api/routers/chat.py`, `frontend/src/components/ChatView.vue`, `frontend/src/composables/chat/`, `frontend/src/components/chat/`, repo memory chat notes
- blocks/limits/admin questions -> `api/routers/blocks.py`, `api/routers/users.py`, `core/services/block_service.py`, `models/user.py`, admin handlers/components
- realtime/notification questions -> `api/routers/realtime.py`, `api/routers/notifications.py`, `core/notifications.py`, frontend notification store/components
- sync questions -> `api/routers/sync.py`, `core/sync_push.py`, `core/sync_worker.py`, `core/events.py`

## Answering Rules For A Local Assistant
When using this repository as grounding:
- prefer implemented behavior over assumptions from product naming
- distinguish user-facing rules from dev-only shortcuts
- distinguish peer blocking from admin restrictions and trading limits
- if a behavior spans backend and frontend, prefer backend for truth and frontend for UX details
- if the code shows a protective or unusual branch, assume it probably exists because of a prior real bug or deployment constraint, not by accident
- if the answer is uncertain, point to the likely source-of-truth files rather than inventing certainty

## What This File Intentionally Excludes
This file intentionally does not carry:
- agent tool instructions
- commit, push, or deploy workflow rules
- multi-assistant coordination rules
- long historical change logs
- temporary operational details that do not help answer product questions
- secrets, dev keys, or environment-specific sensitive values

## Packaging Guidance
For local-assistant corpora:
- include this file near the top of the corpus
- include the packed repository output for deep retrieval
- exclude `.github/copilot-instructions.md` unless the assistant is specifically intended to help developers operate the repository itself
- treat this file as routing guidance, not as a substitute for source code
