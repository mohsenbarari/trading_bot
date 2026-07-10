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
placement policy.
