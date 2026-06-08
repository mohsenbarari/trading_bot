# Cross-Server Sync Observability

This document covers the foreign/Iran architecture when Iran loses external connectivity and later reconnects.

## Current Architecture

The project has two independent runtime stacks:

- Foreign server: API, bot, DB, Redis, optional sync worker for retry/recovery.
- Iran server: API, DB, Redis, sync worker, no Telegram bot.

Both servers should run `sync_worker`. This keeps retry delivery alive after a network outage. Manual `make sync-recover` remains the fallback for long outages or cases where one worker was stopped.

Every synced database change is written to local `change_log` in the same transaction as the original write. The same payload is also pushed to Redis:

```text
sync:outbound
sync:retry
```

The direct HTTP push path tries to deliver changes immediately. If Iran is disconnected, failed direct pushes are safe because the durable source of truth remains:

- `change_log`
- Redis retry queues
- always-on `sync_worker` on both servers
- `scripts/recover_cross_server_sync.sh` for reconnect recovery

## Outage Behavior

When Iran internet is down:

- local app behavior should continue on each server
- foreign can continue writing its own `change_log`
- Iran can continue writing its own `change_log` if local users can reach it
- direct cross-server delivery can fail without losing the original change
- sync backlog and lag should grow instead of disappearing silently

This is expected during an outage. The important question is whether backlog drains after reconnect.

## Health Endpoint

Local health:

```bash
make sync-health
```

Iran health through SSH:

```bash
make sync-health-iran
```

Both commands call:

```text
GET /api/sync/health
```

The endpoint requires:

```text
X-Dev-Api-Key
```

Returned fields:

- `server_mode`
- `peer_server_url_configured`
- `redis_ok`
- `unsynced_change_log_count`
- `oldest_unsynced_age_seconds`
- `unsynced_by_table`
- `redis_queues.sync:outbound`
- `redis_queues.sync:retry`

Each call also emits a structured log:

```text
event=sync.health
log_class=integration
```

Grafana uses this event to show sync state.

## Reconnect Procedure

After Iran reconnects:

```bash
make sync-health
make sync-health-iran
make sync-recover
make sync-health
make sync-health-iran
```

Expected final state:

- `unsynced_change_log_count = 0` on both servers
- `oldest_unsynced_age_seconds = 0` on both servers
- `sync:retry = 0` on both servers
- sync worker logs stop showing repeated network failures

If the first `sync-recover` run processed records, running it once more is acceptable. It is designed to drain both directions repeatedly.

## Grafana Dashboard

Start the local observability stack:

```bash
export GRAFANA_ADMIN_USER=admin
export GRAFANA_ADMIN_PASSWORD='use-a-long-random-password'
make observability-up
```

Open:

```text
http://127.0.0.1:3000
```

Folder:

```text
Trading Bot
```

Dashboard:

```text
Trading Bot Cross Server Sync
```

Important panels:

- Unsynced ChangeLog Backlog
- Oldest Unsynced Age
- Redis Sync Queues
- Recent Sync Health Samples
- Sync Worker Failures
- Direct Push Cooldown / Errors

The dashboard needs `make sync-health` and `make sync-health-iran` to be run periodically or by an external scheduler so fresh `sync.health` samples exist in Loki.

## Alert Rules

### Sync backlog high

Trigger:

- `unsynced_change_log_count > 100` for 10 minutes.

First checks:

```bash
make sync-health
make sync-health-iran
make logs-jobs
```

If Iran just reconnected:

```bash
make sync-recover
```

### Sync lag high

Trigger:

- `oldest_unsynced_age_seconds > 900` for 10 minutes.

Meaning:

- at least one change is older than 15 minutes and has not synced
- expected during a real outage
- suspicious after reconnect

First checks:

```bash
make sync-health
make sync-health-iran
```

Then inspect sync worker logs:

```bash
make logs-jobs
```

### Sync retry queue non-empty

Trigger:

- `sync:retry > 0` for 5 minutes.

Meaning:

- direct delivery or worker delivery failed and queued retry work remains

First action:

```bash
make sync-recover
```

## Operational Rule

Do not delete `change_log` rows or Redis sync queues to silence alerts. Backlog is the evidence needed to recover after an outage.

Only consider manual cleanup after:

- both servers show zero backlog
- audit/trade/user surfaces are verified
- a backup exists
- the cleanup is documented

## Monitoring Gaps

This implementation makes sync backlog and lag visible. It does not yet run an always-on external heartbeat scheduler.

Recommended production addition:

- run `make sync-health` locally every 1 minute
- run `make sync-health-iran` from the foreign server every 1 minute
- alert if either command fails repeatedly

That scheduler can be cron, systemd timer, or a small dedicated monitor container.
