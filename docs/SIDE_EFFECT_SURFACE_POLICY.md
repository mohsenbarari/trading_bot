# Side Effect Surface Policy

Branch: `candidate/bot-webapp-integration`

This document is part of Step 4B of the Bot/WebApp integration contract.

## Classification

| Surface | Execution server | Sync role | Policy |
| --- | --- | --- | --- |
| Telegram | `foreign` | publication side effect | All backend/API/background Telegram effects must use `core.telegram_gateway`. Iran fails closed before Telegram API execution. Temporary bot-runtime exceptions are documented in `TELEGRAM_GATEWAY_EXCEPTIONS.md`. |
| Web Push | `iran` | local WebApp delivery side effect | Browser push subscriptions stay Iran-local. Foreign must not execute Web Push directly, even if VAPID settings are present. Foreign business events must sync durable notification intent or product data to Iran. |
| WebApp realtime | local WebApp runtime | local delivery side effect | Realtime publish writes to Redis/WebSocket/SSE only. It must not write `change_log` or any outbound sync item. Sync apply may emit local realtime after data is applied. |
| Notification rows | shared product/intent data | synced rows | `notifications` sync as durable notification intent/result rows. Local unread count refresh and Web Push fanout are receiver-side side effects after apply. |
| Notification preferences | shared routing policy | synced policy rows | `user_notification_preferences` are registered as sync-policy rows before broad receiver replication is enabled. Conflict/version policy must be finalized before enabling broad preference writes. |
| Push subscriptions | Iran browser runtime | no sync | `push_subscriptions` are local browser runtime rows and must not cross-sync. |

## Step 4B Runtime Rules

- `core.web_push.is_web_push_configured()` is true only on `SERVER_MODE=iran` with valid VAPID settings
  and the optional `pywebpush` dependency available.
- In `SERVER_MODE=foreign`, Web Push helpers return a no-op result or skip background scheduling.
- Foreign notification-producing business flows must persist or sync notification/product intent instead
  of trying to send browser push from foreign.
- `api.routers.realtime.publish_event()` is a local fanout helper. It does not record outbound sync work.
- Sync-applied terminal offer updates may call realtime fanout after the authoritative data is already
  applied locally.
