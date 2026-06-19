# Runtime Session And Bot Account Freshness Policy

Branch: `candidate/bot-webapp-integration`

This document is part of Step 4C of the Bot/WebApp integration contract.

## Runtime Versus Product Data

| Data | Policy | Reason |
| --- | --- | --- |
| `user_sessions` | no sync | WebApp session runtime is local to the WebApp/Iran surface. |
| `session_login_requests` | no sync | Pending login approval is local runtime state and must not replicate as product data. |
| `single_session_recovery_requests` | no sync | Recovery workflow state is local runtime state. |
| `single_session_recovery_admin_targets` | no sync | Recovery routing state is local runtime state. |
| Bot FSM state | foreign local runtime | Telegram bot runtime is foreign-only and FSM state must not be treated as shared product data. |
| `users` account fields | sync | `telegram_id`, profile fields, account status, roles, limits, and counters are product/account data subject to field policy. |
| `user.home_server` | legacy compatibility only | It must not represent the user's current active surface and must not be flipped by login/runtime activity. |

## Bot Freshness Behavior

- If the bot cannot find the account row needed for `/link` or channel join approval, it must fail closed
  and show a retry-visible sync-pending message.
- Unknown channel join requests are declined until synced account data is visible on foreign.
- Inactive or deleted accounts are denied explicitly and must not be linked or approved into the channel.
- Runtime sessions still carry their own `home_server`/server id. That runtime value belongs on
  `UserSession` and token claims, not on `User.home_server`.
