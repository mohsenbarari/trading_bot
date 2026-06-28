# Cross-Server Sync Observability

This document covers the foreign/Iran architecture when Iran loses external connectivity and later reconnects.

## Current Architecture

The project has two independent runtime stacks:

- Foreign server: API, bot, DB, Redis, optional sync worker for retry/recovery.
- Iran server: API, DB, Redis, sync worker, no Telegram bot.

Both servers should run `sync_worker`. This keeps retry delivery alive after a network outage. Manual `make sync-recover` remains the fallback for long outages or cases where one worker was stopped.

Every synced database change is written to local `change_log` in the same
transaction as the original write. This committed row is the durable sync
outbox. `sync_worker` drains committed `change_log` rows and delivers those
rows to the peer.

Redis is not the source of truth for database sync. It is used only as runtime
queue/signaling state:

```text
sync:outbound  wake-up signal / compatibility queue for committed work
sync:retry     worker-created retry payloads after a committed delivery failure
```

The old flush-time direct HTTP push for database changes is no longer part of
the committed sync path. `push_sync_direct()` still exists for narrow non-DB
relay paths such as Telegram notification relay from Iran to foreign. If a
future database direct-push acceleration is reintroduced, it must run only after
the source transaction has committed.

If Iran is disconnected, failed delivery attempts are safe because the durable
source of truth remains:

- `change_log`
- always-on `sync_worker` on both servers
- Redis retry queues created by the worker after committed delivery failure
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

The endpoint accepts either:

```text
loopback access from the local host
X-Observability-Api-Key for non-loopback callers
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
- `parity_status.comparison_status`
- `parity_status.fresh`
- `parity_status.latest_comparison`
- `parity_status.freshness_required_seconds`

Each call also emits a structured log:

```text
event=sync.health
log_class=integration
```

Grafana uses this event to show sync state.

### Parity Status

`GET /api/sync/health` is not proof of database parity by itself. It reports
local backlog, Redis queues, publication health, and the last parity comparison
that an operator or scheduled job explicitly recorded.

The latest comparison is recorded with:

```text
POST /api/sync/parity/status
X-Observability-Api-Key: ...
```

Loopback callers may omit the key. Non-loopback callers must send the configured
observability key.

The body must be the JSON output from `scripts/compare_sync_parity.py compare`.
The endpoint stores only an operator summary in Redis under
`sync:parity:latest_comparison`.

Example:

```bash
python3 scripts/compare_sync_parity.py compare \
  --local-snapshot tmp/foreign-parity-deep.json \
  --peer-snapshot tmp/iran-parity-deep.json \
  --record-url http://127.0.0.1:8000/api/sync/parity/status \
  --record-observability-key "$OBSERVABILITY_API_KEY"
```

Healthy strict-mode evidence requires:

- `parity_status.comparison_status` is fresh and not `missing` or `stale`
- `parity_status.latest_comparison.status` is `ok` or only an accepted
  `non_business_difference`
- `parity_status.latest_comparison.business_drift_count = 0`
- `parity_status.latest_comparison.critical_drift_count = 0`
- `parity_status.latest_comparison.incomplete_count = 0`
- `parity_status.latest_comparison.truncated_table_count = 0`
- `parity_status.latest_comparison.duplicate_identity_count = 0`

If the latest comparison is missing or stale, the system may still be syncing
normally, but strict parity alerts must remain warning-only.

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

For production on the foreign host:

```bash
make sync-health-monitor-install
```

This installs a one-minute systemd timer when `systemctl` is available. If systemd is missing, the installer prints the equivalent cron entry instead.

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

- worker delivery failed, or an explicit replay/relay path queued retry work
  after a committed source row was already available

First action:

```bash
make sync-recover
```

### Watermark stale, duplicate, or conflict decisions

The receiver records source-sequence watermark decisions in metrics:

```text
trading_bot_sync_watermark_decisions_total
```

Important labels:

- `server_mode`
- `table`
- `decision`
- `reason`

Expected duplicate decisions can happen when the worker retries a delivered
event, when an operator replays an already-applied row, or when a legacy
compatibility path re-delivers the same logical event. Stale or conflict
decisions require investigation because they can indicate out-of-order delivery,
manual replay mistakes, or a bad watermark repair.

## Missing Sync Health Samples

Trigger:

- no `sync.health` sample from `server_mode=foreign` for 5 minutes
- no `sync.health` sample from `server_mode=iran` for 5 minutes

Meaning:

- the foreign sampler is not running, failing, or cannot reach one side
- Grafana/Loki ingestion may be broken
- the foreign host may have lost SSH access to Iran

First checks:

```bash
make sync-health-sample
systemctl status trading-bot-sync-health-sampler.timer
systemctl status trading-bot-sync-health-sampler.service
```

If systemd is not used, check the cron entry and its target log file.

## Operational Rule

Do not delete `change_log` rows or Redis sync queues to silence alerts. Backlog is the evidence needed to recover after an outage.

Only consider manual cleanup after:

- both servers show zero backlog
- audit/trade/user surfaces are verified
- a backup exists
- the cleanup is documented

## Degrade And Rollback Playbook

Use this playbook when sync behavior is suspicious but production data must stay
safe. The first goal is to stop new cross-server side effects while preserving
evidence for recovery.

### Hold immediate cross-server push

Set this on the affected app/bot process environment and restart only the
affected services:

```bash
TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH=true
```

This disables the remaining fire-and-forget direct HTTP helper. For database
sync, committed `change_log` rows remain untouched and the `sync_worker` can
still drain durable backlog when it is running. For non-DB relay paths, this is
a side-effect hold and should be used only when temporary loss of that relay is
acceptable.

### Disable strict watermark mode

Keep or restore:

```bash
SYNC_WATERMARK_STRICT_MODE=false
```

With strict mode off, compatibility behavior is preserved while the receiver
still logs and counts stale, duplicate, and conflict decisions. Do not enable
strict mode again until fresh parity evidence is clean.

### Hold queue drain without data loss

To pause cross-server delivery while keeping the durable backlog, stop only the
sync worker service on the affected side:

```bash
docker compose stop sync_worker
```

To resume:

```bash
docker compose up -d --no-deps sync_worker
```

Do not clear `sync:outbound`, `sync:retry`, or unsynced `change_log` rows as a
normal rollback action.

### Publication gate during medium or long outage recovery

The active-publication gate is the safe path for medium/long outage recovery.
It prevents old local-only active offers from being republished as active before
full catch-up and recovery finalization are complete. Use the existing recovery
flow and keep the gate active until sync health is clean.

```bash
make sync-health
make sync-health-iran
make sync-recover
make sync-health
make sync-health-iran
```

If the gate reports candidates, run the documented recovery finalization flow in
dry-run first and only apply it after the expected row count is reviewed.

### Repair is dry-run-first

Build repair evidence before any repair write:

```bash
python3 scripts/sync_repair_tool.py plan \
  --local-snapshot tmp/local-parity.json \
  --peer-snapshot tmp/peer-parity.json
```

For one-row replay, run without `--apply` first. `--apply` also requires
`--confirm-write` and `--source-sequence` so receiver watermarks remain
auditable.

## Production Sampler

The foreign host now has a dedicated sampler path:

- `scripts/sample_sync_health.py` samples local sync health and then SSHes into Iran to sample the Iran API over `127.0.0.1`
- `scripts/install_sync_health_monitor.sh` installs a one-minute systemd timer or prints a cron fallback
- `make sync-health-sample` runs a single immediate sample
- `make sync-health-monitor-install` installs the recurring sampler

This keeps the sync dashboard and alerts active without manual `make sync-health` calls.
