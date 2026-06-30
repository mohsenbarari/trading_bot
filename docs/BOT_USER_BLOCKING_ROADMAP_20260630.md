# Telegram Bot User Blocking Roadmap - 2026-06-30

## Goal

Expose user-to-user blocking in the Telegram bot with the lowest practical risk by reusing the existing WebApp/backend block service and the existing bot `block_manage` flow.

## Current State

- Backend block management already exists in `core/services/block_service.py`.
- WebApp API endpoints already use the shared block service through `api/routers/blocks.py`.
- Bot handlers for search, block, unblock, and blocked-user listing already exist in `bot/handlers/block_manage.py`.
- `bot/handlers/block_manage.py` is already included in `run_bot.py`.
- The current user panel button `🚫 کاربران مسدود شده` in `bot/handlers/panel.py` only opens a list/unblock view and does not expose the existing search-and-block flow.
- `user_blocks` already has sync support with pair identity `(blocker_id, blocked_id)`.

## Product Rules To Preserve

- Customers and accountants must not manage block lists directly.
- Users should block real trading principals. Customer trade visibility remains delegated through the owner/سرگروه rules already enforced by `is_trade_blocked_by_principals`.
- Users outside a customer's group must not directly block another user's customer; they must block the customer's owner/سرگروه instead.
- Existing WebApp behavior must not change.

## Low-Risk Implementation Plan

1. Keep the shared block service as the single source of truth.
2. Connect the Telegram user-panel `🚫 کاربران مسدود شده` button to the existing `block_manage` main menu instead of the legacy list-only view.
3. Keep legacy user-panel unblock callbacks intact so old inline messages do not break.
4. Add a clear `بازگشت به پنل کاربر` callback from the bot block menu.
5. Harden `block_user()` against crafted callbacks by rejecting missing or deleted target users before inserting `UserBlock`.
6. Add focused regression tests for:
   - bot panel opening the full block menu;
   - block menu back-to-panel callback;
   - missing/deleted target rejection in the shared block service;
   - existing block-management tests staying green.

## Validation

- Run focused bot block and panel tests.
- Run focused block service tests.
- Run `py_compile` for changed bot/service modules.
- Run `git diff --check`.

## Deployment Notes

- No migration is required.
- Production deploy should be explicit and separate from this implementation unless requested.
