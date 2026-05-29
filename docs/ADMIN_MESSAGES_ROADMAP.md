# Admin Market And Broadcast Messages Roadmap

## Locked Decisions

- Only `SUPER_ADMIN` can create or resend management messages.
- Market management messages are created from the admin panel, not from the market page.
- Market management messages are visible to users with market access and exclude accountants from notification delivery.
- Market audience includes ordinary users, managers, middle managers, Tier 1 customers, and Tier 2 customers.
- Only one market management message is active/pinned at a time. Creating or reusing a message publishes a new row and preserves the old history.
- `SUPER_ADMIN` can clear the current market pin without deleting the historical row.
- The admin management page starts with two top-level options: `ارسال پیام در صفحه بازار` and `ارسال پیام در چت`.
- The market page renders the title `پیام مدیریت` clearly, with the admin text on the next line.
- When market is closed, the full pinned market message is shown. When market is open and offers appear below it, the message collapses to a short Telegram-like preview and expands on tap.
- Messenger send-to-all is independent of mandatory/optional channels.
- Messenger send-to-all targets can include users, managers, accountants, and customers.
- Messenger send-to-all must create management notifications and messenger unread state.
- Messenger send-to-all must not expose the real Super Admin identity to recipients. The real actor is kept only for audit.
- Messenger send-to-all uses a distinct management-message visual style, not the normal user-message style.
- Inside the market option, the active market pin is shown first with the same preview style as the real market pin, a `مشاهده همه پیام` expand action, and an explicit clear-pin action.
- Inside the market option, the last 5 historical market messages live in a default-closed accordion. Each row keeps a small publish-date tag and a pencil edit action that brings the viewport back to the composer.

## Implementation Shape

- Market messages use a dedicated persisted history surface with one current pinned message.
- Clearing the market pin only deactivates the current row and republishes an empty market-management event; history remains intact.
- Selecting a market history row for edit rehydrates the composer instead of mutating the history row directly; publish still creates a fresh row.
- Messenger broadcasts use system chat rooms that are independent from channels and read-only for recipients. Each recipient gets a private management room row with normal unread mechanics and special system styling.
- Notifications use `NotificationCategory.SYSTEM`, letting the frontend title them as `پیام مدیریت` while route metadata points users to `/market` or the management-message room.
- Recipient resolution is service-owned and deduplicated to keep market-notification exclusion rules separate from send-to-all inclusion rules.

## Validation Targets

- Backend service/router tests for market current/history/reuse/clear-pin, broadcast recipient selection, unread receipts, and notification fanout.
- Frontend unit coverage for market pinned/collapsed rendering, the market/chat option switcher, market history accordion behavior, pencil-to-composer scroll/focus, admin compose/history/clear-pin actions, messenger management row styling, and read-only management-room display.
- Endpoint smoke after deployment because new authenticated routers and realtime events are involved.