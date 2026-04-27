# Local Assistant Context

## Purpose
This document is a curated, high-signal context file for a local AI assistant.
It is intended to replace workflow-heavy agent instructions in packaging flows such as Repomix.

Use this file when the assistant should answer questions about the product's implemented behavior, user flows, and source-of-truth code locations.

## Product Summary
Gold/Coin Trading Bot is a Persian-first trading platform with three main surfaces:
- FastAPI backend for auth, trading, chat, notifications, and admin APIs
- aiogram Telegram bot for onboarding, invitations, trading, admin tasks, and user controls
- Vue 3 + TypeScript PWA frontend for market activity, messenger, profile, and admin tools

## Main User Flows

### Authentication and Registration
- OTP-based authentication is the default login path.
- Web users can register through invitation-based onboarding.
- Sessions support primary/secondary device approval flows.
- Suspended sessions can be revived through OTP-based re-authentication instead of immediate hard logout.

Source of truth:
- `api/routers/auth.py`
- `api/routers/sessions.py`
- `core/services/session_service.py`
- `frontend/src/views/LoginView.vue`
- `frontend/src/views/SetupPassword.vue`

### Invitations
- Invitations are created by admins and can be looked up by short code or token.
- Invitation links are used in both bot and web onboarding.

Source of truth:
- `api/routers/invitations.py`
- `bot/handlers/admin.py`
- `frontend/src/components/CreateInvitationView.vue`

### Trading and Offers
- Users create buy/sell offers for commodities with quantity, price, and optional lot sizing.
- Trades execute against existing offers and use locking/versioning protections.
- Offer parsing and channel publishing behavior lives in backend and bot code, not only UI.

Source of truth:
- `api/routers/offers.py`
- `api/routers/trades.py`
- `core/services/trade_service.py`
- `bot/handlers/trade_create.py`
- `bot/handlers/trade_execute.py`
- `frontend/src/views/MarketView.vue`
- `frontend/src/components/TradingView.vue`

### Messenger and Media
- Chat supports text, image, video, voice, sticker, document, forwarded, reply, and location-oriented flows.
- Media upload/download behavior is performance-sensitive and intentionally optimized for weak devices.
- Album behavior is explicit-batch-based, not naive grouping by adjacency.
- The frontend messenger is modular: state stays in `ChatView.vue` and logic is split across composables and subcomponents.

Source of truth:
- `api/routers/chat.py`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/`
- `frontend/src/composables/chat/`
- `frontend/src/services/chatUploadBackground.ts`

### User Blocks and Limits
- Blocking is a real domain concept with API and bot management flows.
- User limits and restrictions are distinct from blocks and are stored on the user model/settings.

Source of truth:
- `api/routers/blocks.py`
- `core/services/block_service.py`
- `bot/handlers/block_manage.py`
- `models/user.py`

### Notifications and Realtime
- The system uses WebSocket and SSE patterns backed by Redis pub/sub.
- Notifications exist both as persisted records and as realtime events.

Source of truth:
- `api/routers/notifications.py`
- `api/routers/realtime.py`
- `core/notifications.py`
- `frontend/src/stores/notifications.ts`
- `frontend/src/components/AppToasts.vue`

## Core Domain Entities
- `users`: account identity, role, trading limits, soft-delete state, block permissions
- `invitations`: invitation tokens and short codes
- `offers`: buy/sell offers with optimistic locking and remaining quantity
- `trades`: executed responses to offers
- `messages`: chat messages with edit/delete history and media semantics
- `conversations`: per-user unread counters and last-message pointers
- `notifications`: persistent user notifications
- `user_blocks`: directional block relationships
- `chat_files`: uploaded media metadata and storage pointers
- `trading_settings`: dynamic runtime configuration

## Roles
- `WATCH`
- `STANDARD`
- `POLICE`
- `MIDDLE_MANAGER`
- `SUPER_ADMIN`

Role semantics are implemented in backend permission checks and admin tooling, not only in frontend labels.

## High-Signal Behavioral Rules
- Soft delete matters: user deletion is intentionally non-destructive and preserves trade history.
- `apiBaseUrl = ''` on the frontend is valid same-origin behavior and must not be treated as a missing config.
- Persian/Jalali presentation is expected in user-facing experiences even when UTC storage is used internally.
- Chat/media behavior is intentionally optimized for mobile reliability; apparent complexity in messenger code often exists to preserve UX on weak devices.
- Session behavior is multi-device aware; approval, refresh, and suspension logic are all part of the implemented product behavior.

## Retrieval Priorities for a Local Assistant
When answering product questions, prioritize these sources in order:
1. Backend routers and services for real business behavior
2. Frontend views/components for visible user flows
3. Bot handlers for Telegram-specific behavior
4. Models and schemas for data shape confirmation
5. Long historical change logs only when behavior changed recently and source code alone is ambiguous

## What This File Intentionally Excludes
- Agent tool instructions
- Commit/deploy workflow rules
- Multi-assistant coordination rules
- Long historical change logs
- Temporary operational details that do not help answer product questions
- Sensitive development-only values

## Packaging Guidance
For local-assistant corpora, include this file and exclude `.github/copilot-instructions.md` unless the assistant is specifically meant to help developers operate the repository itself.