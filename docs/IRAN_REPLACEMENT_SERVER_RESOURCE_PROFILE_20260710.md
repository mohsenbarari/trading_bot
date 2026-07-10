# Iran Replacement Server Resource Profile

Date: 2026-07-10

## Host Baseline

- CPU: 4 vCPU
- RAM: 7.6 GiB visible to the operating system
- Root storage: 150 GB
- Role: Iran WebApp/API, PostgreSQL, Redis, sync worker, and Nginx; no Telegram bot

## Initial Safe Profile

| Setting | Value |
| --- | --- |
| `API_WORKERS` | `4` |
| `DB_POOL_SIZE` | `8` |
| `DB_MAX_OVERFLOW` | `4` |
| `POSTGRES_MAX_CONNECTIONS` | `150` |
| `POSTGRES_SHARED_BUFFERS` | `2GB` |
| `POSTGRES_EFFECTIVE_CACHE_SIZE` | `5GB` |
| `POSTGRES_WORK_MEM` | `4MB` |
| `POSTGRES_MAINTENANCE_WORK_MEM` | `256MB` |
| `POSTGRES_CHECKPOINT_TIMEOUT` | `15min` |
| `POSTGRES_MAX_WAL_SIZE` | `2GB` |
| `POSTGRES_MIN_WAL_SIZE` | `512MB` |
| `POSTGRES_WAL_BUFFERS` | `16MB` |

The steady API connection ceiling is `4 * (8 + 4) = 48`. The sync worker can
open up to another `12`, leaving substantial headroom below PostgreSQL's `150`
connection limit for migrations, maintenance, and operator access.

`effective_cache_size` is a planner estimate and does not reserve 5 GiB. The
former `8GB/80GB` PostgreSQL profile belonged to the retired high-memory Iran
host and must not be reused on this server.

## Rollout Gate

The replacement host runs Ubuntu 26.04 (`resolute`). Its archive provides
`docker-compose-v2` and does not provide the legacy `docker-compose` package.
The bootstrap must select the available package after refreshing APT metadata;
it must not hard-code the legacy package name.
The standalone `bootstrap-iran` command only installs host prerequisites. Sync
sampler installation remains after `sync-project` in the full release because a
fresh host does not yet contain the sampler script.

Shared-data recovery is registry-guarded. The reset/backlog list must equal all
current `SyncPolicy.SYNC` tables, and fresh-host inspection treats every shared
table as blocking unless it is explicitly allowed to contain migration-created
bootstrap rows. This prevents a newly synced table from being silently omitted
from replacement-host recovery.

During a fresh-host seed, start the Iran API with
`BACKGROUND_JOBS_ENABLED=false`, keep `sync_worker` stopped, and serve the
recovery Nginx profile. That profile permits only the HMAC-authenticated
`/api/sync/receive` endpoint from the configured foreign public IP and returns
`503` for every user-facing route. Re-enable jobs and the full Nginx profile
only after clean shared-table parity.

## Current-State Seed Contract

The first foreign-to-Iran seed on 2026-07-10 stopped safely at
`commodity_aliases` with `deferred_foreign_key_dependency_missing`. The fresh
migration had already inserted the canonical `امام` commodity. A natural-key
conflict consumed a target sequence value, so later commodity integer IDs no
longer represented the same names on both servers. The raw snapshot payload
also bypassed `core.sync_field_policy`, which could copy local-only delivery or
publication fields that normal sync intentionally drops.

The recovery contract is therefore:

1. `scripts/seed_shared_sync_tables.py --dry-run` must build and validate every
   payload, not merely count rows.
2. Snapshot rows must pass through `sanitize_sync_payload()` before delivery.
3. Local foreign keys must carry stable identities: commodity name, offer
   public ID, trade number, and customer-relation invitation token as
   applicable. A missing required identity aborts before any network send.
4. The receiver resolves those identities to target-local IDs before upsert.
   Republished offers are seeded newest-first, and trades are seeded before
   completed offer-request ledgers.
5. The partially seeded Iran database must be backed up and reset before a
   retry. A partial seed must never be resumed table-by-table as if it were a
   clean baseline.
6. Foreign producers and the Iran receiver must run the same reference-aware
   sync contract before Iran background jobs, the Iran sync worker, or public
   WebApp traffic are enabled.

Read-only validation against the foreign production database covered all 981
current shared rows: 46 users, 4 customer relations, 372 notifications, 7
commodities, 23 aliases, 158 offers, 139 publication states, 27 trades, 2 offer
requests, 8 trade-delivery receipts, and the remaining shared operational
rows. No payload was sent during this validation.

After the reset and seed retry, acceptance still requires deep parity by
stable identity. Matching row counts alone are insufficient; all business
hashes must match or have an explicitly reviewed local-only classification.

Before production startup:

1. Render the Iran runtime env and assert every value above.
2. Render `docker-compose.iran.yml` and confirm the same values reach the app
   and PostgreSQL services.
3. Start PostgreSQL first and verify it remains healthy without OOM activity.
4. Start migration, API, and sync worker; then inspect memory, DB connections,
   sync backlog, and application logs.
5. Increase workers, pools, or PostgreSQL memory only after a production-like
   staging benchmark and an explicit capacity decision.

This profile changes no business logic, data model, sync contract, or Telegram
placement policy except for the reference-safe recovery/sync payload contract
documented above.
