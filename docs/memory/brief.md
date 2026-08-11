# Project Brief

Purpose:
- Gold/Coin Trading Bot is a Persian-first trading system for invite-based users and administrators.
- It provides trading offers and execution, OTP-first authentication, sessions, notifications, chat/media, and operational controls through API, Telegram, and web surfaces.

Current direction:
- Preserve production safety while evolving the Telegram bot, FastAPI backend, Vue PWA, and cross-server sync/observability workflows.
- Evolve the Vue PWA through staged, mobile-first UI/UX V2 work in the approved modern-financial and purposeful-minimalism direction; keep Market and Messenger protected and every stage independently reversible.
- Treat the repository's current code and approved roadmaps/runbooks as authoritative; verify behavior with focused tests and diffs before release work.

System shape:
- FastAPI backend (`main.py`), aiogram bot (`run_bot.py`), Vue 3 + TypeScript PWA (`frontend/`), PostgreSQL, Redis, Docker Compose, and Telegram/SMS integrations.
- Legacy runtime code remains in `api/`, `bot/`, and `models/`; `src/` is a gradual Clean Architecture migration, not a replacement already completed.
- Start orientation with `LOCAL_ASSISTANT_CONTEXT.md`; use `docs/PROJECT_DOCUMENTATION.md` and task-specific roadmap/runbook documents for deeper scope.
