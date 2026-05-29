# Admin Market And Broadcast Messages Roadmap

## Locked Decisions

- Only `SUPER_ADMIN` can create or resend management messages.
- Market management messages are created from the admin panel, not from the market page.
- Market management messages are visible to users with market access and exclude accountants from notification delivery.
- Market audience includes ordinary users, managers, middle managers, Tier 1 customers, and Tier 2 customers.
- Only one market management message is active/pinned at a time. Creating or reusing a message publishes a new row and preserves the old history.
- The market page renders the title `پیام مدیریت` clearly, with the admin text on the next line.
- When market is closed, the full pinned market message is shown. When market is open and offers appear below it, the message collapses to a short Telegram-like preview and expands on tap.
- Messenger send-to-all is independent of mandatory/optional channels.
- Messenger send-to-all targets can include users, managers, accountants, and customers.
- Messenger send-to-all must create management notifications and messenger unread state.
- Messenger send-to-all must not expose the real Super Admin identity to recipients. The real actor is kept only for audit.
- Messenger send-to-all uses a distinct management-message visual style, not the normal user-message style.

## Implementation Shape

- Market messages use a dedicated persisted history surface with one current pinned message.
- Messenger broadcasts use system chat rooms that are independent from channels and read-only for recipients. Each recipient gets a private management room row with normal unread mechanics and special system styling.
- Notifications use `NotificationCategory.SYSTEM`, letting the frontend title them as `پیام مدیریت` while route metadata points users to `/market` or the management-message room.
- Recipient resolution is service-owned and deduplicated to keep market-notification exclusion rules separate from send-to-all inclusion rules.

## Validation Targets

- Backend service/router tests for market current/history/reuse, broadcast recipient selection, unread receipts, and notification fanout.
- Frontend unit coverage for market pinned/collapsed rendering, admin compose/history actions, messenger management row styling, and read-only management-room display.
- Endpoint smoke after deployment because new authenticated routers and realtime events are involved.