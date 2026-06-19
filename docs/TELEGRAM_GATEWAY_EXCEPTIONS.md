# Telegram Gateway Exceptions

Branch: `candidate/bot-webapp-integration`

This document is part of Step 4A of the Bot/WebApp integration contract.

## Approved Gateway

Backend, API, sync, background, and channel-publication Telegram side effects
must use `core.telegram_gateway`.

The gateway is intentionally foreign-only. A Telegram execution attempt on
Iran must fail closed before a Telegram API request is made.

## Temporary Exceptions

The remaining direct `aiogram` message/edit/delete calls inside `bot/` are
temporary bot-runtime exceptions.

Reason:

- These calls run inside the Telegram bot process, which is already blocked
  from starting outside the foreign surface by Step 3A.
- Migrating every interactive handler call in one step would create a large
  regression surface across admin, trade, history, profile, and onboarding
  flows.
- The high-risk backend/API/channel paths have already moved to the central
  gateway in Step 4A.

Removal plan:

- Future bot handler work must prefer a bot-runtime adapter that delegates to
  `core.telegram_gateway` policy checks.
- New backend/API/background Telegram calls must not use direct HTTP or direct
  `Bot(...)` execution.
- New direct `api.telegram.org` references are blocked by tests except for the
  gateway and the connectivity probe.
