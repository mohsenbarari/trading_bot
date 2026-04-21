# Gold/Coin Trading Bot — Copilot Instructions

## Language & Communication
- User speaks **Persian (Farsi)**. Respond in Persian for explanations.
- Code, comments, and commit messages in **English**.
- After every fix: **commit with descriptive message + deploy via `make up`**.

## Architecture Overview

> [!WARNING]
> **TEMPORARY: SINGLE-SERVER DEVELOPMENT MODE IN EFFECT (IRAN INTERNET OUTAGE)**
> 
> Due to internet connectivity issues reaching the Iran server (`87.107.110.68`), the following temporary changes are in effect to prevent development from stopping:
> 1. **DO NOT DEPLOY TO IRAN:** Do not use `make up` or `make iran`. Instead, ONLY use `make foreign` for deployments to build the frontend and serve it from the German API on port 8000.
> 2. **CROSS-SERVER SYNC OFF:** `trading_bot_sync_worker` container has been stopped via `docker stop` to prevent log floods and connection timeouts.
> 3. **MESSENGER ON FOREIGN:** The frontend Messenger (`mini_app_dist`) is fully available and testable locally on the Foreign server at `http://<Foreign_IP>:8000/chat`.
> 
> **To Revert when regular internet access resumes:**
> 1. Remove this warning block from `.github/copilot-instructions.md`.
> 2. Restart sync worker: `docker start trading_bot_sync_worker`.
> 3. Revert `deploy.sh`: Remove the `build_frontend` line from the `foreign)` case if you don't want the frontend to automatically build on foreign-only deployments.
> 4. Run full deploy: `make up`.
> 5. Run `POST /api/sync/resync` to merge any out-of-sync database records between Germany and Iran.

### Two-Server Deployment
| Server | Location | Services | Domain |
|---|---|---|---|
| **Foreign** | Germany (current machine) | Bot + API + Sync Worker + DB + Redis | — |
| **Iran** | 87.107.110.68 | API + Nginx + Frontend (no bot) | `coin.gold-trade.ir` |

### Tech Stack
- **Backend**: FastAPI 0.111 + SQLAlchemy 2.0 (async, asyncpg) + PostgreSQL 15 + Redis 7
- **Bot**: aiogram 3.10 (long-polling, FSM)
- **Frontend**: Vue 3 + TypeScript + Tailwind CSS + Vite (PWA)
- **Deploy**: `make up` → `deploy.sh all` (builds frontend, rsyncs to Iran, Docker rebuild both)

### Docker Services (Foreign)
`app` (FastAPI:8000), `bot` (aiogram polling), `sync_worker`, `migration` (alembic), `db` (postgres:15), `redis` (redis:7), `adminer` (127.0.0.1:8080)

Iran is identical but **no bot service**. Nginx proxies `/api/` → backend, `/` → SPA (`mini_app_dist/`).

## Database Models

### Core Tables
- **users**: id, account_name (unique), mobile_number (unique), telegram_id (nullable, unique), full_name, address, role (Enum), has_bot_access, is_deleted, deleted_at, trading_restricted_until, max_daily_trades, max_active_commodities, max_daily_requests, limitations_expire_at, trades_count, commodities_traded_count, channel_messages_count, can_block_users, max_blocked_users, last_seen_at
- **invitations**: id, account_name, mobile_number, token (unique), short_code (unique), role, created_by_id→users, is_used, expires_at
- **offers**: id, version_id (optimistic lock), user_id→users, offer_type (buy/sell), commodity_id, quantity, price, remaining_quantity, is_wholesale, lot_sizes (JSON), status (active/completed/cancelled/expired), notes, channel_message_id, idempotency_key
- **trades**: id, version_id, trade_number (unique, starts 10000), offer_id, offer_user_id, offer_user_mobile, responder_user_id, responder_user_mobile, commodity_id, trade_type, quantity, price, status (pending/confirmed/completed/cancelled), idempotency_key
- **messages**: id, sender_id, receiver_id, reply_to_message_id, forwarded_from_id, content, message_type (text/image/sticker), is_read, is_deleted, edit_history (JSON)
- **conversations**: id, user1_id (smaller), user2_id (larger), last_message_id, unread_count_user1/2
- **notifications**: id, user_id, message, is_read, level (INFO/SUCCESS/WARNING/ERROR), category (SYSTEM/USER/TRADE)
- **commodities** + **commodity_aliases**: name/alias with cascade delete
- **user_blocks**: blocker_id, blocked_id (unidirectional data, bidirectional logic)
- **chat_files**: UUID pk, uploader_id, s3_key (disk path), file_name, mime_type, size, thumbnail (base64)
- **trading_settings**: key-value store (offer_expiry_minutes, max_active_offers, etc.)
- **change_log** + **sync_blocks**: Cross-server sync tracking

### Enums
- `UserRole`: WATCH="تماشا", STANDARD="عادی", POLICE="پلیس", MIDDLE_MANAGER="مدیر میانی", SUPER_ADMIN="مدیر ارشد"
- `OfferType`: buy/sell | `OfferStatus`: active/completed/cancelled/expired
- `TradeType`: buy/sell | `TradeStatus`: pending/confirmed/completed/cancelled

## API Endpoints Summary

### Auth (`/api/auth`): request-otp, resend-otp-sms, verify-otp, webapp-login, refresh, register-otp-request/verify/complete, /me
### Invitations (`/api/invitations`): CRUD + lookup/{short_code} + validate/{token}
### Users (`/api/users`): CRUD (Super Admin/Dev Key required). DELETE = soft delete
### Offers (`/api/offers`): CRUD + /parse (NLP). Creates Telegram channel messages
### Trades (`/api/trades`): POST (execute on offer with FOR UPDATE lock), GET my/with/{user_id}
### Chat (`/api/chat`): conversations, messages, send, edit (48h), delete (48h), upload-image, files/{id}, stickers, typing, read, poll
### Notifications (`/api/notifications`): CRUD + SSE stream
### Blocks (`/api/blocks`): block/unblock/check/search
### Realtime (`/api/realtime`): WebSocket + SSE via Redis pub/sub
### Sync (`/api/sync/receive`): HMAC-signed cross-server sync
### Sync Resync (`/api/sync/resync`): POST, Dev API Key, pushes unsynced change_log entries (params: limit, table_filter)
### Config (`/api/config`): Public bot_username + frontend_url

## Bot Handlers

| Handler | Purpose |
|---|---|
| start.py | /start + invitation token, registration FSM (contact→address), channel trade callbacks |
| link_account.py | /link — link web account to Telegram |
| panel.py | User/admin panels, trading settings view/edit |
| trade_create.py | Offer creation FSM: type→commodity→quantity→lots→price→notes→confirm |
| trade_execute.py | Channel inline button → execute trade |
| trade_manage.py | Expire offer from bot |
| trade_history.py | History display, filtering, Excel/PDF export |
| admin.py | Invitation creation FSM |
| admin_commodities.py | Commodity + alias CRUD via bot |
| admin_users.py | User management: list, search, profile, block, limit, role, delete |
| block_manage.py | User-facing block management |

## Frontend Components

### Views: DashboardView, LoginView (OTP), MarketView, MessengerView, ProfileView, AdminView, InviteLanding, WebRegister
### Key Components: UserProfile (admin actions), UserManager (user list), CreateInvitationView, CommodityManager, ChatView, OffersList, TradingView, NotificationCenter, TradingSettings
- **Note on Chat Components**: `ChatView.vue` acts as the data orchestrator (WebSockets, IndexedDB, API polling) and delegates UI rendering to 4 extracted subcomponents: `ChatHeader.vue`, `ChatInputBar.vue`, `ChatMessageItem.vue`, and `ChatContextMenu.vue`. When modifying chat features, ensure state management stays in `ChatView.vue` while UI/gesture logic goes into the respective subcomponent.

### Auth Flow (`utils/auth.ts`)
- Tokens in `localStorage`: `auth_token`, `refresh_token`
- `apiFetch()` wrapper: adds Bearer token, auto-retries 401 with refresh
- `setupExpiryTimer()`: checks every 30s, auto-refreshes before expiry
- `apiBaseUrl = import.meta.env.VITE_API_BASE_URL || ''` → **empty string = same-origin** (Nginx proxies /api/)

## Critical Patterns & Gotchas

### Authentication
- JWT Subject = `user.id` (integer). Access token 60min, refresh 30 days, HS256.
- `get_current_user()` tries ID first, fallback to telegram_id (legacy).
- Admin endpoints use `verify_super_admin_or_dev_key` dependency.
- DEV_API_KEY: `3f7bc530-b8a8-4125-9b79-256dc46bc530` (header: `X-Dev-Api-Key`)

### Soft Delete
Users: `is_deleted=True, deleted_at=datetime.utcnow()`. Atomic: preserves mobiles in trades, expires offers, deletes invitations.

### apiBaseUrl = '' (Empty String)
Frontend `apiBaseUrl` is `''` for same-origin. **NEVER use falsy checks** like `if (!apiBaseUrl)` — empty string is falsy in JS but is the correct value.

### Two-Server Sync
SQLAlchemy event listeners → `change_log` + Redis queue + direct HTTP push. Sync worker retries. Receiver does dependency-ordered upserts. FK violations deferred and retried.

### Real-time
WebSocket + SSE via Redis pub/sub. Events: `offer:created/expired/updated/cancelled/completed`, `trade:created`. Per-user events via `notifications:{user_id}` channel.

### Optimistic Locking
Offers and Trades use `version_id`. SQLAlchemy raises `StaleDataError` on conflict.

### Persian/Jalali
Store UTC → display Iran time (Asia/Tehran) + Jalali calendar. Persian numeral normalization on all inputs.

### SMS (SMS.ir)
- Invitation SMS: bot link + web link, ~161 chars (3 UCS-2 segments)
- OTP SMS: simple code message
- API key in .env `SMSIR_API_KEY`

## Core Services

| Service | Purpose |
|---|---|
| config.py | pydantic-settings: bot_token, server_mode, db urls, jwt_secret, etc. |
| db.py | Async SQLAlchemy engine (pool_size=100, max_overflow=50) |
| security.py | JWT create/verify (access + refresh tokens) |
| cache.py | Redis cache: users (5min), commodities (5min), offer counts (30s) |
| events.py | SQLAlchemy after_insert/update/delete → sync + real-time publish |
| sync_push.py | HMAC-signed HTTP push to other server (thread pool) |
| sync_worker.py | Redis queue consumer, batch + retry sync |
| offer_expiry.py | Background task: expire old offers every 15s |
| notifications.py | Cross-server notification delivery |
| sms.py | SMS.ir integration |
| connectivity.py | Iran server: checks Telegram API accessibility every 30s |
| trading_settings.py | Dynamic settings: Redis cache → DB → JSON → defaults |

## Media & File Storage
- **Backend Storage**: Local disk (migrated from S3). Max size configured to 50MB in Nginx (`client_max_body_size`) and FastAPI. 
- **Upload Endpoint**: `/api/chat/upload-media` (Accepts `image/*` and `video/*`).
- **Download Endpoint**: `/api/chat/files/{id}?token=`
- **Frontend Uploads**: Uses `XMLHttpRequest` instead of `fetch` to accurately track `upload_progress`.
- **Frontend Downloads**: Uses `fetch` with `ReadableStream` to track `download_progress`. Media is NOT auto-downloaded on load (saves bandwidth).
- **Frontend Caching**: Uses `IndexedDB` (`trading_bot_images` store) to cache media blobs locally. A base64 `thumbnail` is shipped with the DB message to show a blurred preview while the full file downloads.
- **UI Rendering Rule**: Always render `<img>` or `<video>` tags unconditionally using `local_blob_url` or `imageCache` when available. Progress rings should be placed as an absolute overlay *on top* of the media, rather than using `v-else-if` to hide the media tag conditionally (which causes the container to collapse).

## Deploy Commands
```bash
make up          # Full deploy (both servers)
make frontend    # Frontend only
make iran        # Iran server only
make foreign     # Foreign server only
make logs        # Docker logs (foreign)
make logs-iran   # Docker logs (iran)
make restart     # Restart containers (foreign)
make status      # Container status
```

## Known Fixed Issues (Don't Reintroduce)
1. **apiBaseUrl falsy check**: `!props.apiBaseUrl` blocks all admin actions because `'' == falsy`
2. **soft_delete() missing**: User model needs `soft_delete()` method
3. **Bot hard delete**: Must soft-delete, not `session.delete()` (FK violations)
4. **bot_username null on Iran**: Fixed — `BOT_USERNAME=mbmtrading1_bot` added to Iran `.env`. Was missing, causing `https://t.me/None?start=...` links in invitations and SMS.
5. **Token expiry mismatch**: Access 60min, refresh 30 days — keep consistent
6. **OTP not deleted after verify**: Must delete Redis OTP key after successful verification
7. **SMS too long**: Keep invitation SMS under 3 UCS-2 segments (~161 chars)
8. **Admin user list showing only active users**: GET /api/users defaults `include_deleted=False` so only active users are shown. Bot also filters `is_deleted==False`. Deleted users will be viewable later via a dedicated admin section.
9. **Sync data not reaching other server**: change_log entries created by events outside SQLAlchemy (direct SQL, scripts) have no sync entries. Use `POST /api/sync/resync` endpoint (Dev API Key required) to push unsynced change_log entries in batches of 50. Real-time sync via events.py works for normal app operations.

## Assistant Collaboration Protocol

### Multi-Assistant Coordination
- **Ownership**: Each assistant (Antigravity, Copilot, etc.) MUST only commit changes they have personally verified and initiated. Do not commit code generated by another assistant unless you have fully reviewed and integrated it.
- **Context Awareness**: Before starting a major task, check the **Major Changes History** below and the `brain/` directory for recent implementation plans and walkthroughs.

### Commit Rules
- Commit messages must clearly state the scope of changes.
- If multiple assistants are working sequentially, ensure each step is committed separately to maintain a clean git history and avoid "ghost" changes.

## Major Changes History

| Date | Assistant | Description |
| :--- | :--- | :--- |
| 2026-02-25 | Antigravity | Refactored `ChatView.vue` into `ChatHeader.vue`, `ChatInputBar.vue`, `ChatMessageItem.vue`, and `ChatContextMenu.vue` for modularity. |
| 2026-02-26 | Antigravity | Established the Assistant Collaboration Protocol and Change History tracking. |
| 2026-02-27 05:45 UTC | Antigravity | Refactored `ChatView.vue` monolithic logic into four modular composables (`useChatMedia`, `useChatWebSocket`, `useChatMessages`, `useChatScroll`) to significantly reduce file size and improve maintainability. |
| 2026-02-27 05:48 UTC | Antigravity | Fixed TypeScript type errors in `ChatView.vue` regarding event types (`handleMessageClick`) and missing props (`isUploading`, `selectedMessages`) in `ChatInputBar`. |
| 2026-02-27 05:50 UTC | Copilot | Switched bot FSM storage from `MemoryStorage` to `RedisStorage` in `run_bot.py`. FSM state now persists across bot restarts via Redis. |
| 2026-02-27 05:58 UTC | Copilot | Removed duplicate `offer_expiry_loop()` from `run_bot.py`. It already runs in `main.py` (app container) on both servers, so running it in bot too caused double execution on the foreign server. |
| 2026-02-27 06:10 UTC | Antigravity | Refactored `ChatView.vue` UI template and styles into four separate components (`ChatConversationList`, `ChatForwardModal`, `ChatLightbox`, `ChatEmptyState`), reducing `ChatView.vue` logic and markup to under 1000 lines. |
| 2026-02-27 06:29 UTC | Copilot | Replaced raw `fetch` with `apiFetch` (auto-refresh on 401) in all admin components: `UserManager.vue`, `CommodityManager.vue`, `CreateInvitationView.vue`, `TradingSettings.vue`, `UserProfile.vue`. Fixes "خطا در دریافت لیست کاربران" when JWT expires. |
| 2026-02-27 06:48 UTC | Copilot | Hide soft-deleted users from bot and web user lists. API `include_deleted` default changed to `False`. Bot query filters `is_deleted==False`. Frontend deleted-user badge/styles removed. Deleted users viewable later via dedicated admin section. |
| 2026-02-27 07:02 UTC | Copilot | Fixed `bot_username` null on Iran server. Added `BOT_USERNAME=mbmtrading1_bot` to Iran `.env`. This caused invitation links to be `https://t.me/None?start=...` in both API responses and SMS. |
| 2026-04-02 17:31 UTC | Antigravity | Activated Single-Server Development Mode bypassing Iran Server. Stopped `sync_worker` container and updated instructions explicitly prohibiting `make up` in favor of `make foreign`. |
| 2026-04-02 17:39 UTC | Antigravity | Updated `deploy.sh` to include `build_frontend` under the `foreign` target to ensure UI changes are served locally during single-server mode. |
| 2026-04-02 17:53 UTC | Antigravity | Fixed layout jump bug in `ChatMessageItem.vue` during scroll-to-reply. The `.highlight-message` animation was overriding `.message-bubble`'s default animation, causing `slideIn` to replay downwards. Used `::after` pseudo-element to isolate the highlight animation. |
| 2026-04-02 18:28 UTC | Antigravity | Refactored Messenger Search to mimic Telegram's behavior. Created a full-screen Global Search List (`ChatSearchGlobalList.vue`) and added In-Chat Navigation controls (`next`/`prev` arrows in `ChatHeader.vue`) alongside `<mark>` background highlighting in `ChatMessageItem.vue`. |
| 2026-04-02 18:48 UTC | Antigravity | Fixed Search UI bugs reported by user: Cleared old `searchQuery` state upon toggling search; Updated `ChatSearchGlobalList.vue` to show both Date and Time (Jalali); Implemented `loadMessages` in `nextSearchResult/prevSearchResult` to ensure older search targets can be scrolled to; Added List/Chat view toggle in `ChatHeader.vue` for in-chat search results. |
| 2026-04-02 19:08 UTC | Antigravity | Refactored Search UI layout to exactly match Telegram Android: hid header avatar/info during search to maximize input width, removed cramped header navigation, and created `ChatSearchBottomBar.vue` to act as the bottom navigation dock replacing `ChatInputBar` while searching. |
| 2026-04-02 19:19 UTC | Antigravity | Fixed visual bugs in Search UI overlay: Hid the duplicate header "back" button while search is active, retained `ChatSearchBottomBar` visibility during list mode, and implemented dynamic Chat/List SVG icon toggling. |
| 2026-04-02 19:32 UTC | Antigravity | Fixed in-chat list-to-chat navigation context loss: Updated `handleToggleInChatList` and `handleSearchResultClick` to preserve `isSearchActive` when viewing search targets, and added a `setTimeout` for `scrollToMessage` to allow the chat window to fully mount prior to attempting to scroll to the target message element. |
| 2026-04-02 19:54 UTC | Antigravity | Rearranged `ChatView.vue` template rendering tree to ensure `ChatSearchBottomBar` is permanently locked to the bottom of the screen alongside the list overlay (`ChatSearchGlobalList`) exactly like the official Telegram Android app. |
| 2026-04-02 19:59 UTC | Antigravity | Cleaned up the Search UI Bottom Bar: Hid the calendar icon, result counter, and navigation arrows while in list-view mode, leaving only the return-to-chat toggle to match the pure list aesthetics of Android Telegram. |
| 2026-04-04 10:30 UTC | Copilot | **Pre-requisite for Custom Auth**: Added `admin_password_hash` and `must_change_password` to `User` model. Created `scripts/create_superadmin.py` to allow developers to create a singleton `SUPER_ADMIN`. Added `REQUIRES_PASSWORD_CHANGE` flow in interceptors, `SetupPassword.vue`, and `/api/auth/setup-password`. |
| 2026-04-04 11:45 UTC | Copilot | **Multi-Session Management**: Implemented WhatsApp-like session system. Added `UserSession` and `SessionLoginRequest` models with UUID PKs. Created `SessionService` with anti-abuse thresholds and primary device logic. Updated `verify-otp`, `register-complete`, and `webapp-login` to handle session creation and approval flow. Added real-time login notifications via WebSocket and a new `/api/sessions` router. |
| 2026-04-04 11:55 UTC | Copilot | **Security Refactor**: Switched from `passlib` to raw `bcrypt` (v5.0.0) for password hashing to fix internal crash on 73-byte test strings. Updated `create_access_token` to support both `subject=` and `data=` parameters for better compatibility across the codebase. |
| 2026-04-04 12:10 UTC | Copilot | **Frontend Session UI**: Integrated multi-session flow in `LoginView.vue` (Waiting for Approval state). Added `SessionApprovalModal.vue` globally in `App.vue` to allow primary devices to approve/reject new logins. Updated `ProfileView.vue` with an active sessions list and `UserProfile.vue` with an admin-only session limit configuration. |
| 2026-04-05 16:30 UTC | Copilot | **Session Suspension Logic**: Implemented "Soft Re-authentication" flow. Introduced `core/session_expiry.py` background worker to suspend stale sessions (30d+5d grace) instead of hard logout. Updated `api/routers/auth.py` and `SessionService` to support `suspended_refresh_token` for reviving sessions via OTP without losing device roles (Primary/Secondary). |
| 2026-04-05 16:45 UTC | Copilot | **Frontend Auth Resilience**: Added `suspendSession()` to `frontend/src/utils/auth.ts` to preserve expired refresh tokens for soft-reauth. Patched `apiFetch` to use suspension logic on 401s. Fixed concurrent refresh token race conditions using an async singleton promise lock. |
| 2026-04-05 17:10 UTC | Copilot | **PWA & Router Recovery**: Implemented `vite:preloadError` listener in `main.ts` and `router.onError` in `index.ts` to detect and fix ChunkLoadErrors. Implemented a smart "JS Fallback" in `main.py` that serves a reload script instead of a 403/404 when a stale PWA chunk is requested, forcing a transparent client-side cache refresh. |
| 2026-04-05 17:20 UTC | Copilot | **UX & Touch Optimization**: Fixed navigation unresponsiveness in `BottomNav.vue` by applying `pointer-events: none` to SVG icons/labels, ensuring the parent `router-link` captures all touch events. |
| 2026-04-05 17:40 UTC | Copilot | **Schema & Bug Fixes**: Fixed `NameError: Optional` in `auth.py` by adding missing typing imports. Updated `UserBase` schema in `schemas.py` to allow `telegram_id: None`, fixing "500 ResponseValidationError" during web-only user registration. |
| 2026-04-06 11:35 UTC | Antigravity | Added Telegram-style "New Conversation" feature: Implemented backend `search_public_users` API in `users_public.py`, created `ChatNewConversationModal.vue` for user search, added FAB to `ChatConversationList.vue`, and wired it to `ChatView.vue`'s `startNewChat`. Changed modal's fetch implementation from Axios to native `fetch` and custom debounce for standalone compatibility. |
| 2026-04-07 09:50 UTC | Copilot | **Session Termination Security**: Enforced stricter access control in `api/routers/sessions.py`. Only primary devices can terminate other active sessions. Fixed `logout_all_sessions` to exclude the caller. Improved `ProfileView.vue` and `LoginView.vue` login/logout sequence. Fixed incorrectly mapped deps in `users_public.py`. |
| 2026-04-07 10:15 UTC | Copilot | **Session Expiry Rules Fixed**: Fixed "random logout after 1-2 hours" issue caused by refresh token rotation races and route guards aggressively evicting users on expired access tokens instead of awaiting a refresh. The backend `/refresh` endpoint now returns the same refresh token, resolving concurrent request token invalidation. Introduced hard enforcement of `expires_at` (30 days limit) check within `/refresh` to seamlessly trigger the 5-day Session Suspension flow requiring OTP re-authentication. |
| 2026-04-08 08:30 UTC | Copilot | **Session Network Tolerance**: Rewrote Token Refresh mechanism in `frontend/src/utils/auth.ts`. Introduced `RefreshResult` (`success`, `network_error`, `auth_error`). Frontend no longer destroys the local session upon `network_error` (e.g. device went to sleep, slow 4G, temporary 502/504), eliminating the "Random Logout after an hour" issue caused by background timeouts. |
| 2026-04-08 08:36 UTC | Antigravity | **Critical bugfix for New Conversation feature**: 1) Restored accidentally removed `ChatSearchGlobalList` import in `ChatView.vue` which was causing the entire messenger component tree to silently break. 2) Fixed API URL in `ChatNewConversationModal.vue` from `/api/v1/users/public/search` to `/api/users-public/search` to match the actual backend route prefix. 3) Fixed CSS selector in `ChatView.vue` from `.conversations-list` to `.conversation-list-wrapper` to match the new wrapper div structure. |
| 2026-04-08 09:10 UTC | Copilot | **Dev Login Bypass Endpoint**: Added `POST /api/auth/dev-login` to grant instant SUPER_ADMIN access without OTP, session limits, or constraints. Automatically authenticates as a dev user with mobile `09999999999`. Only accessible from local subnets (127.x, 10.x, 172.x, 192.x) or with `X-DEV-API-KEY`. Added a visible "ورود سریع ۱ ساله" button in `LoginView.vue` for local devs. |
| 2026-04-09 07:26 UTC | Antigravity | **Fix FAB modal never rendering**: Moved `ChatNewConversationModal` outside the `<template v-else>` (Messages View) block in `ChatView.vue`. The modal was nested inside a conditional branch that only renders when `selectedUserId` is set, so when viewing the conversation list (selectedUserId=null) the modal component was never mounted and the FAB click had no effect. Verified fix via browser: FAB opens modal, search returns users, selecting a user opens chat. |
| 2026-04-09 10:15 UTC | Copilot | **Global Auto-Reconnect UI**: Intercepted network disconnects (`TypeError: Failed to fetch`) inside `apiFetch` in `auth.ts` to implement a translucent infinite retry loop with exponential backoff (Max 3s). Exposed `isAppConnecting` state which renders a fixed orange "در حال اتصال..." header in `App.vue`. Prevents polling fetch failures when returning from sleep from replacing child components with localized "تلاش مجدد" buttons. |
| 2026-04-09 10:25 UTC | Copilot | **Fix Global Auto-Reconnect**: Re-routed `502/503/504` Server Proxy Errors directly into the `apiFetch` exponential backoff loop inside `auth.ts`. Previously, Nginx `502 Bad Gateway` bypassed the `TypeError: Failed to fetch` catch block causing components to natively render an ugly `error.value` and a manual "تلاش مجدد" fallback button. Components now seamlessly await background reconnection with the global UI header displayed. |
| 2026-04-09 09:25 UTC | Antigravity | **Fix Online Status Timezone Bug**: Updated `useChatMessages.ts` to correctly parse `last_seen_at` as UTC by explicitly appending 'Z' before passing it to `new Date()`. Previously, JS parsed the timezone-naive timestamp (e.g. `2026-04-09T09:20:00`) as local time, breaking the `diffSeconds` calculation and causing genuinely online users to show up as "آخرین بازدید امروز و ساعت الان". |
| 2026-04-09 11:05 UTC | Antigravity | **Global Notification System & Unread Badge**: Implemented a comprehensive real-time notification system. (1) Created `browserNotifications.ts` utility for native browser alerts (truncated at 300 chars). (2) Created `notificationStore.ts` (Pinia) to track global unread counts. (3) Updated `App.vue` with global WebSocket listeners for `chat:message` and system `message` events. (4) Added a red unread badge to the Messenger icon in `BottomNav.vue`. (5) Applied the UTC 'Z' fix to `ChatConversationList.vue` to ensure the online indicator dot works correctly. |
| 2026-04-09 11:25 UTC | Antigravity | **Notification Fix & Unread Chat Logic**: (1) Fixed browser notifications by moving `requestPermission` to a global click/touchstart listener in `App.vue` (addressing browser security blocks). (2) Changed unread logic to show **number of unique chats** instead of total messages on the badge. (3) Updated Backend `/api/chat/poll` to calculate `unread_chats_count`. (4) Optimized `notificationStore.ts` to re-poll server counts on new message events for accuracy. (5) Loosened `document.hidden` restrictions in `App.vue` to ensure notifications show while app is foregrounded if user is on a different page. |
| 2026-04-09 17:20 UTC | Antigravity | **Sender Name in Notifications**: Updated `MessageRead` schema and `send_message` endpoint in `api/routers/chat.py` to include `sender_name`. This allows in-app toast notifications to display the actual name of the sender instead of generic placeholders. Added `joinedload(Message.sender)` to retrieval queries for consistency. |
| 2026-04-09 17:37 UTC | Antigravity | **Enhanced Toasts & Navigation**: Added routing support and swipe-to-dismiss for in-app Toasts (`AppToasts.vue`, `notifications.ts`). Clicking a chat notification navigates to the specific conversation, while general system notifications route to `/notifications`. Updated payload logic in `App.vue` to format 'image'/'video' messages explicitly as "تصویر" or "ویدئو". |
| 2026-04-09 17:54 UTC | Antigravity | **Fixed Routing & Notification Center**: Corrected chat navigation route to use query parameters (`/chat?user_id=...`) as required by `MessengerView.vue`, fixing the "white screen" bug. Created [NotificationsView.vue](file:///root/trading-bot/trading_bot/frontend/src/views/NotificationsView.vue) and registered `/notifications` route in `router/index.ts` to act as a placeholder for the notification center. Polished `AppToasts.vue` with glassmorphism styles and refined swipe animation logic. |
| 2026-04-09 18:07 UTC | Antigravity | **Unified Notification System**: Merged the old database-backed notification history with the new real-time WebSockets system. (1) Updated `notificationStore.ts` to fetch history via API and handle delete/mark-as-read actions. (2) Removed the obsolete `NotificationCenter.vue` component. (3) Upgraded `NotificationsView.vue` to fetch DB history on mount while still receiving live WebSocket inserts. Added dynamic icon support based on notification `level` (success, warning, error, info). |
| 2026-04-09 18:29 UTC | Antigravity | **Hotfix: Vue-Router Reactivity & State in Chat**: Addressed the "white screen" (stuck on white `ChatConversationList`) when clicking chat notifications while already in MessengerView. (1) Added `watch` on `targetUserId` in `ChatView.vue` to react to query parameter changes instantly without full remount. (2) Fixed `useBackButton.ts` which was overwriting Vue-Router's strict browser `history.state`, preventing potential silent navigation failures across the app. |
| 2026-04-10 06:11 UTC | Antigravity | **Notification Sounds & Vibrations**: Implemented a coded sound system using Web Audio API (`audio.ts`) to avoid external MP3 dependencies. Integrated `playNotificationSound` into `notificationStore.ts` for all in-app toasts. Added `vibrate` pattern support in `browserNotifications.ts` for mobile system notifications. Ensured `AudioContext` is enabled via user interaction in `App.vue` to comply with browser safety policies. |
| 2026-04-10 06:22 UTC | Antigravity | **Media Storage Persistence**: Fixed 404 errors on image downloads by adding a persistent Docker volume (`uploads_data`) mapping `./uploads` to `/app/uploads` in `docker-compose.yml`. Previously, media was stored in the ephemeral container layer and lost on restarts. Also updated `.gitignore` to protect the host-side `uploads` directory. |
| 2026-04-10 06:35 UTC | Antigravity | **Mobile Audio Reliability (Safari/Chrome)**: Resolved silent notifications on iOS Safari and "pop" sounds on Android. Implemented `unlockAudioContext` in `audio.ts` which plays a silent buffer on first user gesture to prime the browser's audio engine. Added a 50ms look-ahead delay and 10ms fade-in to `playNote` to prevent hardware clipping on mobile speakers. |
| 2026-04-10 09:30 UTC | Copilot | **Real-time Chat Bugfix**: Resolved a critical message drop bug where incoming WebSocket payloads were ignored due to a JS type-mismatch (String vs Number) in the `senderId === selectedUserId` check. Coerced both to `Number()` in `useChatWebSocket.ts`. |
| 2026-04-10 10:15 UTC | Copilot | **Admin Session Security**: Enforced a strict 1-session limit for ادمین ارشد/مدیانی roles directly in `SessionService.py`. Locked the UI in `UserProfile.vue` for admin accounts to prevent increasing their session cap, while maintaining editability (1-3) for regular users via dynamic `:disabled` and `pointer-events` logic. Added click-to-alert security note for admins. |
| 2026-04-10 10:45 UTC | Copilot | **Dynamic Anti-Abuse Settings**: Externalized hardcoded login request thresholds (daily/weekly/monthly) into `TradingSettings.py` and dynamic database configuration. Created a new "Security & Sessions" section in `TradingSettings.vue` allowing admins to configure base thresholds (1-session baseline) with a note explaining the derived scaling formula for multi-session users. |
| 2026-04-10 09:30 UTC | Copilot | **Real-time Chat Bugfix**: Resolved a critical message drop bug where incoming WebSocket payloads were ignored due to a JS type-mismatch (String vs Number) in the `senderId === selectedUserId` check. Coerced both to `Number()` in `useChatWebSocket.ts`. |
| 2026-04-10 10:15 UTC | Copilot | **Admin Session Security**: Enforced a strict 1-session limit for ادمین ارشد/مدیانی roles directly in `SessionService.py`. Locked the UI in `UserProfile.vue` for admin accounts to prevent increasing their session cap, while maintaining editability (1-3) for regular users via dynamic `:disabled` and `pointer-events` logic. Added click-to-alert security note for admins. |
| 2026-04-10 10:45 UTC | Copilot | **Dynamic Anti-Abuse Settings**: Externalized hardcoded login request thresholds (daily/weekly/monthly) into `TradingSettings.py` and dynamic database configuration. Created a new "Security & Sessions" section in `TradingSettings.vue` allowing admins to configure base thresholds (1-session baseline) with a note explaining the derived scaling formula for multi-session users. |
| 2026-04-10 08:03 UTC | Antigravity | **Multiple Media Uploads**: Modified `ChatInputBar.vue` to allow selecting multiple files at once by adding the `multiple` attribute. Refactored upload handler to loop through files and emit `upload-media` dynamically. Updated `useChatMedia.ts` to implement a counter (`activeUploadsCount`) for `isUploading` to gracefully track concurrent uploads without prematurely clearing the uploading UI state. |
| 2026-04-10 12:00 UTC | Copilot | **Attachment Bottom Sheet & Location Messages**: (1) Created `AttachmentMenu.vue` — Telegram-style Bottom Sheet with Gallery/File/Location tabs, swipe-to-dismiss. Gallery tab compresses images via `browser-image-compression`; File tab sends raw. (2) Refactored `ChatInputBar.vue` to toggle `AttachmentMenu` instead of direct file input. (3) Added Location tab with Leaflet map (fixed center pin, drag-to-select UX). (4) Added `maptiler/tileserver-gl` to `docker-compose.yml` on port 8088. (5) Created `scripts/init_offline_map.sh` for Iran OSM data download. (6) Added `LOCATION` and `VIDEO` to `MessageType` enum + Alembic migration. (7) Backend `send_message` generates static map snapshot via tileserver. (8) `ChatMessageItem.vue` renders location messages with Google Maps link on tap. |
| 2026-04-14 12:00 UTC | Copilot | **Soft Delete Auto-Free Namespace**: Updated `User.soft_delete` to automatically append `_del_XX` to `account_name` and `mobile_number` while clearing `telegram_id` to free the namespace for re-registration without DB constraint errors. Added `utils/formatters.ts` and intercepted global `apiFetch` JSON parsing via JS `Proxy` and WebSocket messages to dynamically strip `_del_XX` suffixes for clean UI display using the existing "Deleted" labels without breaking DB sync integrity. Created `scripts/free_deleted_user.py` for retroactively freeing old deleted users. |
| 2026-04-14 17:35 UTC | Copilot | **DB & Map Fixes**: (1) Fixed `InvalidTextRepresentationError` in chat by adding uppercase `VIDEO` and `LOCATION` to `MessageType` enum via new Alembic migration `e2b3c4d5f6a7`. (2) Compiled and deployed offline Iran vector map using `planetiler` and 4GB temporary swap, freeing 9GB disk space after cleanup. (3) Configured permanent 1GB Swap and persistent Tileserver auto-discovery for `iran.mbtiles`. |
| 2026-04-14 18:40 UTC | Copilot | **In-App Map Viewer**: Implemented Telegram-style in-app location viewer. Created `ChatLocationModal.vue` using Vue-Leaflet with a fixed center pin and offline local tiles. Updated `ChatMessageItem.vue` to emit `location-click` instead of external Google Maps redirect, and integrated it into `ChatView.vue` via Teleport overlay. |
| 2026-04-15 10:45 UTC | Copilot | **Full-Screen Map Attachment**: Refactored `AttachmentMenu.vue` to transition the location picker to a true full-screen overlay (`top: 0 !important`). Implemented native browser Geolocation (My Location button) to automatically find and center the map on the user's real-time position. Disabled swipe-to-dismiss behavior when touching inside the map to allow unobstructed panning/scrolling. |
| 2026-04-15 11:20 UTC | Copilot | **Static Map Snapshots in Chat**: (1) Refactored `api/routers/chat.py` so `generate_location_snapshot` saves the returned tile image directly to the DB as a `ChatFile`. (2) Updated `send_message` to synchronously await the snapshot process and inject `snapshot_id` directly into the Location payload. (3) Updated `ChatMessageItem.vue` to compute `mapSnapshotUrl` and render `<div class="location-snapshot">` replacing the old static location SVG UI block. || 2026-04-18 10:30 UTC | Copilot | **PWA WebAPK Optimization**: Consolidated PWA icons in `vite.config.ts` by merging `any` and `maskable` purposes into `any maskable`. This ensures better compatibility with Android's WebAPK generation to prevent browser-branded shortcuts and ensure a native-like app experience once served over HTTPS. |
| 2026-04-18 11:15 UTC | Copilot | **Mobile Layout Fix**: Fixed the "Desktop-scale" scroll bug in the installed PWA. (1) Locked `html/body` overflow to prevent dual-scrolling. (2) Refactored `App.vue` to use a flex-column wrapper where `RouterView` lives in a `flex-1 overflow-y-auto` container, keeping `BottomNav` fixed without layout shifts. (3) Normalized `min-height` and `padding-bottom` across `DashboardView` and `ProfileView` to ensure a consistent app-like viewport. || 2026-04-18 11:35 UTC | Copilot | **Type Refactoring & Bugfix**: Fixed voice message regression where voice bubbles incorrectly rendered as basic video players. Aligned TypeScript types across ChatView.vue, useChatMessages.ts, and useChatMedia.ts to include 'voice' in supported media message types, resolving compiler warnings and ensuring proper UI bubble selection. |
| 2026-04-18 12:05 UTC | Copilot | **Voice Persistence Fix**: Fixed a bug where older voice messages wouldn't play after page reload because they weren't being automatically cached. Implemented  trigger in  and expanded  in  to support auto-downloading of small media like voice and stickers. |
| 2026-04-18 12:05 UTC | Copilot | **Voice Persistence Fix**: Fixed a bug where older voice messages wouldn't play after page reload because they weren't being automatically cached. Implemented onLoad trigger in ChatMessageItem.vue and expanded loadImageForMessage in useChatMedia.ts to support auto-downloading of small media like voice and stickers. |
| 2026-04-18 12:45 UTC | Copilot | **Single Audio Playback**: Implemented global audio state management via `audioStore.ts` to ensure only one voice message plays at a time. Updated `ChatMessageItem.vue` to watch `currentPlayingId` and stop playback if another message starts. |
| 2026-04-18 19:45 UTC | Copilot | **Cancel Send**: Added capability to cancel in-progress text messages and media uploads. Integrated `AbortController` into `useChatMessages.ts` for text requests and wrapped `XMLHttpRequest` uploads in `useChatMedia.ts` with `.abort()`. Added cancel buttons overlays in `ChatMessageItem.vue` and bound them through `ChatView.vue`. |
| 2026-04-19 12:30 UTC | Copilot | **Native Image Gallery (PhotoSwipe)**: (1) Installed `photoswipe` version 5. (2) Refactored `useChatMedia.ts` to implement a Telegram-style gallery that scans all images in the chat history. (3) Added support for Pinch-to-zoom and Swipe-to-close gestures. (4) Fixed TypeScript errors regarding undefined objects and strict property checks in `ChatMessageItem.vue` and `useChatMedia.ts`. |
| 2026-04-19 12:40 UTC | Copilot | **Native Voice Messages (WaveSurfer.js)**: (1) Installed `wavesurfer.js`. (2) Replaced the static HTML5 `<audio>` player inside `ChatMessageItem.vue` with an interactive Waveform UI. (3) Customized waveforms to use different colors for sent (`#3390ec`) vs received messages, exactly matching Telegram's voice aesthetic. (4) Maintained integration with global `audioStore` to pause playback on unmount or when playing a new voice message. |
| 2026-04-19 19:08 UTC | Antigravity | **Telegram-Tier Messenger Refactoring (Phase 1)**: Massive UI/UX overhaul. (1) **Dependencies**: Installed `@tanstack/vue-virtual`, `@formkit/auto-animate`, `@vueuse/core`, `@vueuse/motion`, `radix-vue`, `blurhash`. (2) **BlurHashCanvas.vue**: New component for instant image placeholders using BlurHash decoding. (3) **Auto-Animate**: Applied `v-auto-animate` to `ChatConversationList.vue` and message groups in `ChatView.vue` for smooth enter/exit transitions. (4) **Context Menu Upgrade**: Rewrote `ChatContextMenu.vue` with smart viewport-aware positioning, glassmorphism backdrop, save-media action, and ARIA roles. (5) **Save-to-Gallery**: Added `handleSaveMedia` in `ChatView.vue` using hidden `<a download>` with Blob URL for saving images/videos to device. (6) **Lightbox Overhaul**: Upgraded `ChatLightbox.vue` with zoom-scale transitions, backdrop blur, download toolbar button. (7) **Forward Modal**: Added `Teleport` + slide-up animation to `ChatForwardModal.vue`. (8) **Conversation Previews**: Added video/voice/location message type previews in conversation list. |
| 2026-04-19 19:20 UTC | Antigravity | **Telegram-Tier Messenger Refactoring (Phase 2)**: (1) **Document Messages**: Added `document` to `MessageType` union in `types/chat.ts`. Created full document message rendering in `ChatMessageItem.vue` with file-type-specific gradient icons (PDF red, ZIP orange, Excel green, Word blue, generic gray), file name ellipsis, size display, and download action. Added `📎 فایل` preview in conversation list. (2) **Album Layout**: Created `ChatAlbumLayout.vue` — Telegram-style mosaic grid for grouped consecutive images/videos (2-col, 3-grid, 4-grid layouts with `+N` overlay for excess). (3) **Skeleton Loading**: Created `ConversationSkeleton.vue` and `ChatSkeleton.vue` with Telegram-style shimmer animations replacing generic `LoadingSkeleton`. Conversation skeleton shows avatar circles and name/preview bars; Chat skeleton alternates sent/received bubble layouts with color-matched shimmer. (4) **Document conversation preview**: Added `📎 فایل` type preview. |
| 2026-04-19 19:45 UTC | Antigravity | **Telegram UI/UX Bug Fixes (Autonomous)**: (1) **Grouped Images Layout**: Grouped consecutive media messages in `ChatView.vue` and rendered them via `ChatAlbumLayout` for an authentic Telegram mosaic layout. (2) **Media Bubble Layout Shifts**: Injected aspect-ratio and explicit dimensions on media wrappers in `ChatMessageItem.vue` before load to prevent DOM jumping. (3) **Broken Lightbox**: Added `lightbox.loadAndOpen()` to immediately trigger PhotoSwipe programmatically on click. (4) **Missing Audio Waveforms**: Fixed WaveSurfer lifecycle bug by refactoring the `watch` into `initWaveSurfer()` and calling it explicitly during `onMounted`. (5) **Document Upload Styling**: Updated file upload bubble UI to include a cancel "✕" button on the left with circular progress, and filename/size alignment. (6) **Text Selection**: Added global `user-select: none` in `main.css` and overridden it for specific selectable elements to emulate a native app. |
| 2026-04-19 20:25 UTC | Antigravity | **Telegram Media Pipeline Optimization (Phase 3)**: (1) **Album Refactoring**: Moved `ChatAlbumLayout` inside `ChatMessageItem` to properly wrap albums inside message bubbles (showing sender, time, and read status). (2) **Media Aspect Ratio**: Enforced `object-fit: cover` and `height: 100%` on `.msg-media-content` so images follow the computed Telegram aspect-ratio algorithm without stretching. (3) **Lightbox Enhancements**: Registered PhotoSwipe UI elements: A `thumbnails-carousel` at the bottom for quick album navigation and a `download-button` for saving individual images. Hidden default arrows/counters. (4) **Flat Waveform baseline**: Overrode WaveSurfer `renderFunction` to draw a flat 2px baseline when audio data is still loading. (5) **Non-Blocking Uploads**: Set `useWebWorker: true` for `imageCompression` in `useChatMedia.ts` to prevent UI freeze during thumbnail/media compression. Removed `:disabled="isUploading"` from `ChatInputBar` to allow multitasking and concurrent uploads. |
| 2026-04-20 09:17 UTC | Antigravity | **Messenger Rendering Fixes (Phase 4)**: (1) **Landscape Bubble Distortion**: Fixed CSS `min-h-[150px]` conflicts causing thumbnail bleed. Replaced with strict `absolute inset-0 w-full h-full object-cover` matching the computed parent container `aspectRatio`. Clamped aspect ratio bounds to prevent ultra-tall panorama rendering bugs. (2) **EXIF Orientation Dimensions**: Fixed the bug where portrait mobile photos were interpreted as landscape (or vice-versa) by injecting `exifOrientation: true` into the `imageCompression` step in `useChatMedia.ts`. Then, extracted the final physical `naturalWidth`/`naturalHeight` post-compression to accurately feed into the JSON payload. |
| 2026-04-20 09:41 UTC | Antigravity | **EXIF Pipeline Sequence Fix (Phase 4.1)**: (1) **Thumbnail Generation**: Refactored `compress_thumb` to generate the base64 thumbnail from the physically rotated `uploadFile` instead of the raw input `file`. (2) **Optimistic Rendering**: Created a new `rotatedUrl` blob from the post-compression file and injected it into `optimisticMsg.local_blob_url` and `imageCache` so the UI instantly reflects the correct rotation before the backend finishes processing. |
| 2026-04-20 09:55 UTC | Antigravity | **Strict Dimension Extraction (Phase 4.2)**: Injected a robust `extractTrueDimensions` async utility in `useChatMedia.ts`. Forced a strict sequential pipeline where dimensions are guaranteed to be read *only* from the post-compression, EXIF-rotated `Blob` instead of prematurely reading from the raw file, permanently resolving the rotated landscape bubbles bug. |
| 2026-04-20 10:45 UTC | Copilot | **Full-Stack EXIF Resolution**: (1) Fixed `createImageBitmap` property name from `orientation` to `imageOrientation` in `useChatMedia.ts` with `<img>` fallback. (2) Added server-side `ImageOps.exif_transpose` using **Pillow** in `api/routers/chat.py` to physically rotate and strip EXIF tags from stored files. (3) Forcefully injected backend-derived dimensions into the frontend payload to override inaccurate browser-side metadata, ensuring pixel-perfect bubble aspect ratios across all devices. |
| 2026-04-20 11:15 UTC | Copilot | **PhotoSwipe Dynamic Dimension Resolution**: Refactored `useChatMedia.ts` so PhotoSwipe `dataSource` now prefers the actually rendered `<img>` element for both `src` and `naturalWidth`/`naturalHeight`, falling back to runtime image decoding only when needed. Added `data-media-msg-id` markers in `ChatMessageItem.vue` and `ChatAlbumLayout.vue`, and aligned the lightbox fallback path with PhotoSwipe v5 by updating slide content through `slide.updateContentSize(true)` instead of invalid legacy methods. |
| 2026-04-20 19:10 UTC | Copilot | **Gallery EXIF Pipeline Fix**: Removed the non-EXIF-safe pre-compression step from `AttachmentMenu.vue`. Gallery images now pass unchanged into `useChatMedia.ts`, so the single authoritative EXIF-safe compression pipeline determines the final blob, dimensions, thumbnail, optimistic preview, and PhotoSwipe source consistently. |
| 2026-04-21 06:48 UTC | Copilot | **Messenger Album Batch Grouping**: (1) Replaced consecutive-media album collapsing in `ChatView.vue` with explicit `album_id` / `album_index` metadata so only media selected together in one gallery action form an album. (2) Updated `AttachmentMenu.vue` and `useChatMedia.ts` to stamp and preserve album batch metadata through optimistic uploads and final sends. (3) Removed the WhatsApp-style `+N` overflow behavior in `ChatAlbumLayout.vue` so all album media render together in the chat like Telegram. |

