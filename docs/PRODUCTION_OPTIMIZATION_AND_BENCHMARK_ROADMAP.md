# Production Optimization and Benchmark Roadmap

Status: draft execution contract for the first production release window.

Last updated: 2026-06-11

This roadmap exists because the project is close to release. From this point on,
production optimization must be measured, reversible, and tied to release safety.
No broad tuning should be accepted only because it is theoretically faster.

## Current Baseline Known From Live Checks

Current Iran production-class host:

| Surface | Current observation |
| --- | --- |
| CPU | Intel Xeon E5-2680 v4, 28 physical cores / 56 logical CPUs |
| RAM | About 125 GiB total, current runtime usage low |
| Disk | About 1 TB non-rotational logical volume; root ext4 has about 837 GiB usable |
| API workers | Iran defaults to `API_WORKERS=8` |
| DB pool | Iran defaults to `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=6` |
| DB connections | Direct PostgreSQL outperformed temporary PgBouncer in the authenticated/chat benchmark |
| PostgreSQL | Still mostly default tuning: `shared_buffers=128MB`, `effective_cache_size=4GB`, `work_mem=4MB` |
| Redis | AOF disabled; RDB snapshots enabled; `maxmemory=0`; `maxmemory-policy=noeviction` |
| Static/media path | Nginx still proxies several asset/media paths through the app instead of using a fully optimized static/media path |
| Messenger benchmark | Mature dedicated Messenger comparison harness already exists |
| Full-product benchmark | Missing; must be added before further broad optimization |

## Principles

1. Benchmark first, then tune.
2. Keep every production change small enough to roll back.
3. Separate Iran and foreign profiles; do not assume one server's tuning is valid for both.
4. Never mutate production user data during benchmark runs.
5. Use deterministic benchmark fixtures with explicit cleanup.
6. Record the exact git SHA, env profile, server role, worker count, DB pool settings, and hardware snapshot for every run.
7. Treat sync health as a release gate, not a best-effort check.
8. Prefer direct measurement over generic advice. PgBouncer is not accepted for production until it wins under this project's workload.
9. Keep logs and benchmark artifacts redacted. No passwords, OTPs, tokens, phone numbers, captions, message bodies, or signed URLs in artifacts.
10. A stage is complete only when its focused benchmark and rollback note are recorded.

## Roadmap Stages

### Stage P0 - Baseline Freeze and Safety Inventory

Goal: capture the current production-like state before changing DB/Redis/Nginx/runtime tuning.

Changes:

- Record foreign and Iran runtime values from the deployment manifest and generated `.env` files.
- Snapshot Docker service status, API worker count, DB pool settings, PostgreSQL settings, Redis settings, disk layout, and sync health.
- Record current git SHA and image tags for both servers.
- Confirm that benchmark fixture names and ids are isolated from real users.

Acceptance:

- A baseline artifact exists under `tmp/production-benchmark/<timestamp>/baseline/`.
- `make sync-health` and `make sync-health-iran` are clean before any optimization run.
- No runtime tuning change is included in this stage.

### Stage P1 - Full-Product Benchmark Harness

Goal: build the missing benchmark layer for the whole project before more production tuning.

Changes:

- Add a top-level benchmark runner that can orchestrate existing focused tools:
  - Messenger benchmark
  - frontend Playwright smoke/matrix slices
  - backend authenticated API probes
  - market/offer/trade workloads
  - profile/customer/accountant workloads
  - realtime/WebSocket/SSE probes
  - cross-server sync probes
  - bot handler/job probes
  - observability/logging overhead probes
  - deployment/restart health probes
- Store artifacts in a stable layout:
  - `tmp/production-benchmark/<timestamp>/metadata.json`
  - `tmp/production-benchmark/<timestamp>/results.json`
  - `tmp/production-benchmark/<timestamp>/summary.md`
  - `tmp/production-benchmark/<timestamp>/logs/`
- Add benchmark modes:
  - `quick`: short release-smoke profile after low-risk changes
  - `targeted`: one stage/domain at a time
  - `full`: release-candidate matrix only
- Add Make targets only after the runner contract is stable:
  - `make production-benchmark-quick`
  - `make production-benchmark-targeted PROFILE=<name>`
  - `make production-benchmark-full`

Acceptance:

- The full-product benchmark can run without touching real production user data.
- The summary reports pass/fail, deltas, and the bottleneck class per surface.
- The runner fails closed on fixture cleanup errors.
- The first full baseline is recorded before Stage P2 begins.

### Stage P2 - PostgreSQL Production Tuning

Goal: use the Iran host RAM/CPU/disk more effectively without hiding DB pressure.

Expected changes:

- Add conservative PostgreSQL tuning to `docker-compose.iran.yml`.
- Tune for the current direct-Postgres architecture first, not PgBouncer.
- Back up the database before any DB container restart.
- Recreate/restart only the DB-dependent services needed for the change.

Candidate settings to validate, not blindly accept:

| Setting | Current | Candidate direction |
| --- | --- | --- |
| `shared_buffers` | `128MB` | `8GB` to `16GB` |
| `effective_cache_size` | `4GB` | `64GB` to `96GB` |
| `work_mem` | `4MB` | `8MB` to `16MB` |
| `maintenance_work_mem` | `64MB` | `512MB` to `1GB` |
| `random_page_cost` | `4` | `1.1` to `1.5` for SSD/NVMe-like storage |
| `effective_io_concurrency` | `1` | `100` to `200` if storage confirms non-rotational behavior |
| `checkpoint_timeout` | default | `10min` to `15min` |
| `max_wal_size` | default | `8GB` to `16GB` |

Acceptance:

- Targeted DB benchmark shows no regression in authenticated/chat and market/trade p95.
- PostgreSQL connection count remains below the safe pool budget.
- No sync backlog remains after restart.
- Rollback is documented as the previous DB command/settings.

### Stage P3 - Redis Durability and Queue Safety

Goal: protect sync/realtime queues from avoidable data loss while keeping latency acceptable.

Expected changes:

- Enable Redis AOF with `appendfsync everysec`.
- Keep `maxmemory-policy=noeviction` because sync queues must not be evicted silently.
- Add a bounded `maxmemory` only after estimating real memory usage and alerting behavior.
- Verify restart behavior with non-sensitive synthetic queue items.

Acceptance:

- Redis restart does not lose the synthetic queue probe.
- Sync health remains clean after Redis restart.
- Realtime latency does not regress beyond the benchmark gate.

### Stage P4 - Nginx, Static Assets, and Media Delivery

Goal: reduce unnecessary app load for static/frontend assets and prepare a safer media path.

Expected changes:

- Serve immutable frontend assets directly from Nginx with long cache headers.
- Keep SPA fallback behavior correct.
- Review chat media/file authorization before any X-Accel migration.
- Only move protected media to X-Accel after the backend access check is proven complete.

Acceptance:

- Asset requests no longer consume API worker capacity.
- Frontend boot benchmark improves or remains stable.
- Protected media remains inaccessible without valid authorization.

### Stage P5 - Worker and Pool Recalibration After DB/Redis Tuning

Goal: decide whether `API_WORKERS=8` remains best after infrastructure tuning.

Expected changes:

- Re-run authenticated/chat and mixed API benchmarks for worker counts `8`, `12`, and optionally `16`.
- Keep per-worker pool sizes bounded.
- Track DB connection count, CPU saturation, RSS memory, p95/p99 latency, and error rate.

Acceptance:

- A higher worker count is accepted only if it improves p95/p99 or throughput without raising DB pressure unsafely.
- If results are flat, keep `8` workers and record the rejected candidate as evidence.

### Stage P6 - Cross-Server Sync and Recovery Hardening

Goal: validate that Iran/foreign shared data stays correct during deploy, reconnect, seed, and recovery scenarios.

Expected changes:

- Benchmark foreign-to-Iran and Iran-to-foreign sync propagation latency.
- Test fresh-host seed, existing-host skip, and guarded reset paths.
- Measure recovery time after pausing one side's sync worker.
- Verify partial receive failures keep changes unsynced.

Acceptance:

- Shared-table sync health is clean on both servers after every scenario.
- No historical replay bug or false-success partial result is accepted.
- Recovery time is recorded in the benchmark summary.

### Stage P7 - Trading Core, Market, and Bot Workloads

Goal: measure the product's money-path behavior, not only chat and HTTP health.

Expected changes:

- Benchmark offer creation, expiration, parse, list, and trade execution.
- Include optimistic-lock/race scenarios for simultaneous trade attempts.
- Exercise Telegram bot handler paths with mocked Telegram network boundaries where possible.
- Measure notification fanout cost separately from trade DB mutation cost.

Acceptance:

- Exactly one winner in race scenarios.
- No duplicate trade, duplicate notification, or stale offer state.
- p95/p99 latency and error rate are recorded under controlled concurrency.

### Stage P8 - Frontend UX Performance Beyond Messenger

Goal: make sure non-chat pages do not regress while Messenger remains the heavy benchmark.

Expected changes:

- Add profile, customer management, market, trade history, admin/user-management, and login flows to the full benchmark.
- Track first usable paint, route transition time, heap, DOM size, and network calls.
- Keep the existing Messenger benchmark as the dedicated deep-dive suite.

Acceptance:

- No major route has unbounded list rendering at production-like row counts.
- Customer/profile/market UI stays within the baseline-regression budget.

### Stage P9 - Observability, Audit, and Alert Readiness

Goal: ensure production visibility without creating unacceptable overhead or leaking sensitive data.

Expected changes:

- Include `scripts/measure_logging_overhead.py` in the full benchmark.
- Verify sync-health sampler, audit anchor exporter, audit anchor shipper, and metrics target rendering.
- Confirm known limitation: memory metrics remain per-process until a real aggregator/exporter stage is approved.

Acceptance:

- Logging overhead remains under the configured budget.
- No benchmark artifact contains raw secrets or sensitive payloads.
- Observability timers are active where production requires them.

### Stage P10 - Deployment, Restart, Backup, and Rollback Benchmark

Goal: measure release operations, not only application request paths.

Expected changes:

- Measure `make production-release` phases separately:
  - local validation
  - foreign deploy
  - build
  - sync
  - image ship/load
  - Iran deploy
  - shared seed/health
  - final health
- Test app-only restart, sync-worker restart, Redis restart, and PostgreSQL restart in controlled order.
- Verify backup creation before destructive reset paths.

Acceptance:

- Release duration and downtime windows are recorded.
- Recovery commands are validated, not just documented.
- Rollback requirements are explicit before the release candidate is approved.

### Stage P11 - Final Release Gate

Goal: decide whether the project is ready for the first production release.

Acceptance:

- Full benchmark is green or every accepted warning has an owner and release note.
- Production deployment manifest is current.
- Sync health is clean on both servers.
- Observability minimums are active.
- Backup/restore and rollback notes exist.
- Messenger benchmark remains green or accepted debt is explicitly documented.
- No high-risk infrastructure change is merged without targeted benchmark evidence.

## Full-Product Benchmark Surface Map

| ID | Surface | Existing coverage to reuse | Missing benchmark work |
| --- | --- | --- | --- |
| `B00` | Host/runtime snapshot | production deploy checks | stable metadata artifact |
| `B01` | API health/config/static | healthcheck scripts | latency/error aggregation |
| `B02` | Auth/session/login | backend and Playwright tests | authenticated load profile |
| `B03` | Users/profile/customers/accountants | frontend tests | route and list-scale timing |
| `B04` | Market/offers/trades | backend tests | concurrent money-path benchmark |
| `B05` | Messenger | existing Messenger benchmark | integrate into full summary |
| `B06` | Media/upload/download | Messenger/media tests | upload CPU, queue, and file latency summary |
| `B07` | Realtime/WebSocket/SSE | targeted tests | fanout and reconnect timing |
| `B08` | Cross-server sync | sync-health/recover scripts | propagation and recovery benchmark |
| `B09` | Bot/jobs | unittest coverage | handler/job throughput and failure profile |
| `B10` | Observability/audit | existing scripts/timers | benchmark artifact and active-timer gate |
| `B11` | Deployment/restart/rollback | production release script | phase timing and downtime summary |

## Benchmark Metrics Contract

Every benchmark run should record:

- `git_sha`
- `release_label`
- `server_role`
- `domain`
- `api_workers`
- `db_pool_size`
- `db_max_overflow`
- `postgres_settings`
- `redis_settings`
- `docker_images`
- `hardware_snapshot`
- `started_at`
- `finished_at`
- `duration_seconds`
- per-surface:
  - request count
  - success count
  - failure count
  - p50/p95/p99 latency
  - max latency
  - error classes
  - CPU/RSS samples when available
  - DB connection high-water mark when available
  - Redis memory/queue samples when available

Frontend runs should also record:

- first usable route paint
- route transition time
- network request count
- JS heap
- DOM node count
- scroll FPS/jank for list-heavy pages
- upload completion time for media scenarios

Sync runs should also record:

- source server
- destination server
- change count
- propagation p50/p95/max
- unsynced count before/after
- oldest unsynced age before/after
- partial/error count

## Benchmark Modes

| Mode | Runtime target | When to run | Required surfaces |
| --- | --- | --- | --- |
| `quick` | 5-15 minutes | small code/config changes | `B00`, `B01`, focused changed surface, sync-health |
| `targeted` | 10-40 minutes | each roadmap stage | stage surface plus regression guard surfaces |
| `full` | 60-120 minutes | release candidate and major infrastructure changes | `B00`-`B11` |

## Initial Regression Gates

Until enough production baselines exist, use baseline-relative gates:

- no new correctness failure
- no unclean sync-health at end of a run
- no benchmark fixture cleanup failure
- no p95 regression above 10% on the changed surface unless explicitly accepted
- no p99 regression above 15% on unchanged critical surfaces
- no API 5xx under normal benchmark load
- no DB connection exhaustion
- no Redis write failure
- no secret-bearing artifact
- no production data mutation outside isolated benchmark fixtures

After three stable full runs, convert these relative gates into absolute per-surface budgets.

## Recommended Immediate Order

1. Implement Stage P0 and P1 first.
2. Run the first `full` benchmark against the current 8-worker direct-Postgres setup.
3. Apply PostgreSQL tuning in Stage P2.
4. Run targeted DB and full-product quick benchmark.
5. Apply Redis durability in Stage P3.
6. Run targeted sync/realtime benchmark.
7. Continue to Nginx/static optimization only after the baseline numbers are trustworthy.

This order is deliberate: PostgreSQL, Redis, Nginx, and worker tuning are all useful, but near release the benchmark harness is the control system that prevents optimization work from becoming untraceable risk.
