# Production Recoverability Runbook

This runbook defines how to keep production recoverable after release. Cross-server
sync improves availability, but it is not a backup: deletes, bad migrations, bad
business logic, or corrupted state can sync to both servers.

## Recovery Targets

| Target | Initial production target | Notes |
| --- | --- | --- |
| `RPO` | 24 hours for scheduled backups; immediate backup before deploy/reset | Tighten to 1 hour only after automated off-host backup is stable. |
| `RTO` | 2 hours for DB restore; 30 minutes for app rollback without DB restore | Measure with restore drills, not estimates. |

## Required Operator Commands

Create a backup on the Iran host:

```bash
make production-backup-iran
```

Create backups on both hosts:

```bash
make production-backup-all
```

Run the recoverability report with live health and sync checks:

```bash
make production-recoverability-report
```

Evaluate the current operational alert thresholds for PostgreSQL, Redis, sync,
disk usage, and backup freshness:

```bash
make production-alerts
```

Install the 5-minute host-level alert sampler on the foreign host:

```bash
make production-alerts-monitor-install
```

Run a DB restore smoke test in a temporary PostgreSQL container on the Iran host:

```bash
make production-recoverability-drill
```

The drill restores the fresh `pg_dump` into a temporary `postgres:15-alpine`
container and removes that container afterward. It does not touch the production
database.

## Backup Contents

The backup command records a JSON manifest and SHA-256 digest for:

| Artifact | Why it matters |
| --- | --- |
| PostgreSQL dump | Users, trades, offers, chat metadata, settings, sync state |
| Redis archive | Durable Redis/AOF state, sync/realtime/session queues |
| Uploads archive | Chat media, avatars, profile/group/channel images |
| Audit archive | Durable audit trail data used for incident evidence |

Store a copy off-host. Keeping the only backup on the same production server is
not sufficient.

## Minimum Backup Policy

- Before every production deploy or shared-table reset: run `make production-backup-iran`.
- Daily: run `make production-backup-all` and copy artifacts off-host.
- Retention: keep `7` daily, `4` weekly, and `3` monthly backups.
- Monthly: run `make production-recoverability-drill` and keep the report artifact.
- After any failed deploy, failed migration, or suspicious sync event: take a new
  backup before manual repair unless the backup itself would worsen disk pressure.

## Restore Flow

1. Stop write paths first:
   - app
   - bot on the foreign host
   - sync_worker on both hosts
2. Take one last emergency backup if the DB is readable.
3. Restore PostgreSQL from the selected backup.
4. Restore uploads and audit data if the incident affected files or audit volume.
5. Restore Redis only when the incident specifically involves queues/session
   state and the selected Redis archive is known to be safer than current state.
6. Start DB and Redis.
7. Run migrations only from the target release revision.
8. Start app and sync_worker.
9. Run:

```bash
make production-online-health
make sync-health
make sync-health-iran
```

10. Confirm login, dashboard, Messenger open, offer list, and notification load
    on the intended public domain.

## Rollback Flow Without DB Restore

Use this path when a deploy breaks code/runtime behavior but did not corrupt DB
state.

1. Revert or checkout the previous known-good commit.
2. Run:

```bash
make production-release
```

3. Confirm:

```bash
make production-online-health
make sync-health
make sync-health-iran
```

4. If the rollback included env or manifest changes, verify generated runtime
   `.env` files on both hosts before reopening traffic.

## Alerts That Must Be Treated As Serious

`make production-alerts` evaluates these thresholds against both production
hosts and writes an artifact under `tmp/production-benchmark/<timestamp>/`.
The systemd sampler writes the latest JSON snapshot to
`/var/lib/trading-bot-observability/production-alerts-latest.json`.

| Signal | Warning | Critical |
| --- | --- | --- |
| PostgreSQL connections | `> 200` | `> 300` |
| PostgreSQL `idle in transaction` | any session older than a few minutes | repeated or blocking sessions |
| Redis memory | `> 4GB` until real baseline is known | `> 8GB` or rapid growth |
| Redis AOF status | `aof_last_write_status != ok` | AOF rewrite/write failure |
| Sync backlog | non-zero backlog after retry window | growing backlog or retry queue |
| Disk usage | `> 75%` | `> 85%` |
| Backup freshness | no successful backup in 24h | no backup in 48h or failed restore drill |
| SSL | renewal due within 14 days | renewal due within 7 days or failed renewal |

## Incident Notes

- Do not assume the other server has clean data during a logical data incident.
- Do not run destructive sync recovery before taking an emergency backup.
- Do not publish raw backup manifests if paths or hostnames should remain private.
- Record the git SHA, release manifest, backup manifest path, and operator action
  in the incident notes.
