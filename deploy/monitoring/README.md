# Isolated monitoring development stack

This stack is owned by the monitoring feature branch and is isolated from the
shared `trading_bot` and `trading_bot_staging` Compose projects.

Isolation boundaries:

- Compose project: `trading_bot_monitoring_dev`
- PostgreSQL database and named volume: monitoring-only
- Redis container and named volume: monitoring-only
- API binding: `127.0.0.1:18100` by default
- no production `.env` file is loaded into any service
- background jobs and Telegram are disabled for the default app/migration path
- the image tag and runtime `RELEASE_SHA` are refreshed from the current branch
  HEAD on every guarded command
- the bot is behind the `monitoring-telegram` profile and requires an explicit
  acknowledgement plus dedicated non-production credentials
- the application keeps `SERVER_MODE=foreign`; its public WebApp API is
  intentionally unavailable to host-originated requests under the
  foreign surface guard

Use the guarded lifecycle script:

```bash
scripts/monitoring_dev_stack.sh check
scripts/monitoring_dev_stack.sh up
scripts/monitoring_dev_stack.sh smoke
scripts/monitoring_dev_stack.sh ps
scripts/monitoring_dev_stack.sh down
```

`down` preserves the isolated database and Redis volumes. Destructive cleanup
requires the exact confirmation phrase printed by the script.

The smoke command checks `/api/config` from inside the application container.
An HTTP 404 for that path from the host is expected in `foreign` mode and must
not be "fixed" by changing the stack to `iran` or weakening the foreign surface
guard. The published loopback port remains useful for explicitly permitted
foreign/internal routes and local diagnostics.

Live Telegram execution is deliberately unavailable in the default lifecycle.
If it is ever needed, populate only dedicated non-production bot/channel values
in the ignored `.env.monitoring.local`, set
`MONITORING_TELEGRAM_LIVE_ACK=DEDICATED_NON_PRODUCTION_TELEGRAM`, enable the
monitoring flag, set a dedicated monitoring channel ID, and then run:

```bash
scripts/monitoring_dev_stack.sh telegram-up
```

Never copy production Telegram credentials into this environment. Never print
or commit `.env.monitoring.local`; the generated file is ignored and mode 0600.
The primary and monitoring bot tokens must be different, and `CHANNEL_ID` must
remain empty so this stack cannot publish to the primary offer channel.

Do not invoke the root `docker-compose.yml` from this feature worktree. The
shared runtime must be managed from its own clean runtime checkout.
