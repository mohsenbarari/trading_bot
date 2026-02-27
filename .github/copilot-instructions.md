# Gold/Coin Trading Bot — Copilot Instructions

## Language & Communication
- User speaks **Persian (Farsi)**. Respond in Persian for explanations.
- Code, comments, and commit messages in **English**.
- After every fix: **commit with descriptive message + deploy via `make up`**.

## Architecture Overview

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
4. **bot_username null on Iran**: `/api/config` returns null on Iran; use API response links directly
5. **Token expiry mismatch**: Access 60min, refresh 30 days — keep consistent
6. **OTP not deleted after verify**: Must delete Redis OTP key after successful verification
7. **SMS too long**: Keep invitation SMS under 3 UCS-2 segments (~161 chars)
8. **Admin user list showing only active users**: GET /api/users defaults `include_deleted=True` so admins see all users. Bot also shows all users with 🗑 icon for deleted ones. Frontend shows deleted users with "حذف شده" badge.
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
| 2026-02-27 14:00 | Copilot | Switched bot FSM storage from `MemoryStorage` to `RedisStorage` in `run_bot.py`. FSM state now persists across bot restarts via Redis. |
| 2026-02-27 14:10 | Copilot | Removed duplicate `offer_expiry_loop()` from `run_bot.py`. It already runs in `main.py` (app container) on both servers, so running it in bot too caused double execution on the foreign server. |
| 2026-02-27 05:45 UTC | Antigravity | Refactored `ChatView.vue` monolithic logic into four modular composables (`useChatMedia`, `useChatWebSocket`, `useChatMessages`, `useChatScroll`) to significantly reduce file size and improve maintainability. |
| 2026-02-27 05:48 UTC | Antigravity | Fixed TypeScript type errors in `ChatView.vue` regarding event types (`handleMessageClick`) and missing props (`isUploading`, `selectedMessages`) in `ChatInputBar`. |
