# Production Optimization and Benchmark Roadmap

Status: Complete through Stage P11; release readiness gate passed with accepted warnings.
Post-P11 recoverability tooling is available for operational backups, restore
smoke tests, live recovery readiness reports, and DB/Redis/sync/disk/backup
threshold alert sampling.

Last updated: 2026-06-11

This roadmap exists because the project is close to release. From this point on,
production optimization must be measured, reversible, and tied to release safety.
No broad tuning should be accepted only because it is theoretically faster.

## Execution Status

| Stage | Status | Evidence |
| --- | --- | --- |
| `P0` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T080030Z/baseline/summary.md`: 28 commands, 0 failed, foreign sync-health clean, Iran sync-health clean |
| `P1` | Safe production baseline complete on 2026-06-11 | `tmp/production-benchmark/20260611T083854Z/summary.md`: full mode ran 11 read-only/safe tasks against the Iran target, 0 required failures, clean foreign/Iran sync-health after cleanup |
| `P2` | Complete on 2026-06-11 | Iran DB backup `p2-postgres-tuning-20260611T090824Z.sql`; `tmp/production-benchmark/20260611T092543Z/summary.md`: targeted DB benchmark passed 3/3 tasks; `tmp/production-benchmark/20260611T091935Z/summary.md`: quick benchmark passed 7/7 tasks; foreign/Iran sync-health clean after system-seed backlog cleanup |
| `P3` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T094159Z/summary.md`: Redis durability benchmark passed 1/1 after live Redis restart probe; `tmp/production-benchmark/20260611T094110Z/summary.md`: quick benchmark passed 7/7; foreign/Iran sync-health clean |
| `P4` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T095946Z/summary.md`: targeted static benchmark passed 1/1 after live Iran Nginx refresh; `tmp/production-benchmark/20260611T100027Z/summary.md`: quick benchmark passed 8/8; foreign/Iran sync-health clean |
| `P5` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T105109Z/summary.md`: targeted workers benchmark passed; worker matrix kept Iran at `API_WORKERS=8` after 8/12/16 comparison |
| `P6` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T113726Z/summary.md`: targeted sync benchmark passed 3/3 tasks; `tmp/production-benchmark/20260611T113726Z/sync-p6/summary.md`: propagation, partial-failure guard, and sync-worker recovery scenarios passed with clean final health on both servers |
| `P7` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T121537Z/summary.md`: targeted trading benchmark passed; `tmp/production-benchmark/20260611T121537Z/trading-p7/summary.md`: bot text offer, parser, offer create/list/expire, trade execution, notification fanout, and race scenario measured on Iran with clean final health |
| `P8` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T131305Z/summary.md`: targeted frontend benchmark passed; `tmp/production-benchmark/20260611T131305Z/frontend-p8/summary.md`: login, dashboard, own/public/customer profile, market, notifications, and admin user-management routes measured on Iran with zero gate failures, zero warnings, bounded list gates, and clean final health |
| `P9` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T133318Z/summary.md`: targeted observability benchmark passed; `tmp/production-benchmark/20260611T133318Z/observability-p9/summary.md`: logging overhead, metrics target contract, durable audit anchor export, anchor shipper local relay, sync-health sampler timers, artifact hygiene scan, and focused observability gate passed with clean final health |
| `P10` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T134805Z/summary.md`: targeted deployment benchmark passed; `tmp/production-benchmark/20260611T134805Z/deployment-p10/summary.md`: release phase timings, pg_dump backup, app/sync-worker/Redis/PostgreSQL restart probes, rollback notes, and final foreign/Iran sync-health passed |
| `P11` | Complete on 2026-06-11 | `tmp/production-benchmark/20260611T141830Z/summary.md`: targeted release benchmark passed; `tmp/production-benchmark/20260611T141830Z/final-release-p11/summary.md`: 16/16 required checks passed, required pass rate 100.0%, warning-adjusted score 92.0/100, live health/sync clean, and four accepted warnings recorded with owner and release note |

## Current Baseline Known From Live Checks

Current Iran production-class host:

| Surface | Current observation |
| --- | --- |
| CPU | Intel Xeon E5-2680 v4, 28 physical cores / 56 logical CPUs |
| RAM | About 125 GiB total, current runtime usage low |
| Disk | About 1 TB non-rotational logical volume; root ext4 has about 837 GiB usable |
| API workers | Replacement Iran host defaults to `API_WORKERS=4` |
| DB pool | Replacement Iran host defaults to `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=4` |
| DB connections | Direct PostgreSQL outperformed temporary PgBouncer in the authenticated/chat benchmark |
| PostgreSQL | Replacement-host safety profile: `max_connections=150`, `shared_buffers=2GB`, `effective_cache_size=5GB`, `work_mem=4MB`, `maintenance_work_mem=256MB`, `max_wal_size=2GB`, `min_wal_size=512MB`; older P2 results below remain historical evidence for the retired host |
| Redis | AOF enabled with `appendfsync=everysec`; RDB status ok; `maxmemory=0` intentionally left unbounded until alert thresholds are defined; `maxmemory-policy=noeviction` |
| Static/media path | Iran Nginx serves immutable frontend assets directly with `Cache-Control: public, max-age=31536000, immutable` and `X-Static-Delivery: nginx`; service worker/manifest stay no-cache; raw `/uploads/` is blocked; protected chat media stays behind `/api/chat/files/{file_id}` token authorization |
| Cross-server sync | P6 targeted sync benchmark passed on both directions; partial receive errors are no longer accepted as success; final foreign/Iran sync-health was clean |
| Trading money path | P7 targeted benchmark runs isolated synthetic users/offers/trades on Iran, waits for sync-health clean, cleans both servers, and records bot/parser/offer/trade/notification/race latency |
| Frontend UX path | P8 targeted benchmark runs isolated synthetic users/accountants/customers/offers/trades/notifications on Iran, measures non-Messenger route usability/DOM/heap/network/list bounds, cleans both servers, and verifies final sync-health |
| Observability readiness | P9 targeted benchmark runs structured logging overhead, metrics contract, durable audit anchor export, anchor shipper relay, required sync sampler timer checks, artifact sensitive-data scan, and focused observability regression gate |
| Deployment/restart readiness | P10 targeted benchmark runs a full foreign-controlled release rehearsal, records phase timings, creates a pre-probe Iran `pg_dump` backup, restarts app/sync-worker/Redis/PostgreSQL in controlled order, and verifies final foreign/Iran sync-health |
| Final release gate | P11 validates all P0-P10 evidence, current manifest identity, live production health, live foreign/Iran sync-health, Messenger readiness, observability minimums, backup/rollback evidence, and accepted-warning ownership |
| Recoverability operations | `make production-backup-iran`, `make production-backup-all`, `make production-recoverability-report`, and `make production-recoverability-drill` provide post-P11 operational backup and DB restore-smoke evidence |
| Production alerts | `make production-alerts` evaluates PostgreSQL connection/idle-session, Redis memory/AOF, sync backlog/lag/queue, disk usage, and backup freshness thresholds; `make production-alerts-monitor-install` installs the 5-minute host-level sampler |
| Messenger benchmark | Mature dedicated Messenger comparison harness already exists |
| Full-product benchmark | Safe read-only harness active; mutation-heavy suites are opt-in only |

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
11. Data-mutating browser/E2E suites must not run against production unless explicitly requested with a dedicated cleanup plan; the default production benchmark runner excludes them.

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

- The full-product benchmark can run without touching real production user data. Production mode excludes mutating suites by default.
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

Completion notes:

- Applied conservative Iran-only PostgreSQL tuning through `docker-compose.iran.yml` with env-driven defaults, keeping the direct PostgreSQL architecture.
- Added runtime env rendering for PostgreSQL tuning keys so production manifests/source env files can override the defaults without code edits.
- Added `scripts/report_postgres_runtime.py` and wired it into `make production-benchmark-targeted PROFILE=db`; the probe verifies active PostgreSQL settings and the current/estimated connection budget inside the Iran app container.
- Added `scripts/report_market_trade_query_plans.py` to keep the P2 DB benchmark from being Messenger-only; it runs read-only EXPLAIN ANALYZE probes for active offers, recent trades, and user-specific offer/trade history when samples exist.
- Mounted `./scripts:/app/scripts` in app/sync-worker containers so runtime benchmark probes can run after rsync-only deploys without requiring a Docker image rebuild.
- Backup before DB recreate: `/srv/trading-bot/backups/p2-postgres-tuning-20260611T090824Z.sql` on the Iran host.
- Pre-change DB artifact: `tmp/production-benchmark/20260611T085932Z/summary.md` passed `messenger_query_plans` in `6.766s`.
- Final post-change DB artifact: `tmp/production-benchmark/20260611T092543Z/summary.md` passed `postgres_runtime_tuning` in `2.507s`, `messenger_query_plans` in `6.631s`, and `market_trade_query_plans` in `3.096s`.
- Active connection budget after tuning: `17` current PostgreSQL connections, estimated SQLAlchemy ceiling `126`, safe limit `400` under `max_connections=500`.
- Current market/trade data set had no user-specific offer/trade samples, so `user_offers_history` and `user_trade_history` were skipped; `active_offers_feed` and `recent_trades_feed` both used index scans with sub-millisecond execution.
- Quick production benchmark artifact: `tmp/production-benchmark/20260611T091935Z/summary.md` passed all `7` safe tasks against Iran.
- `make sync-health` and `make sync-health-iran` were clean after marking only mandatory/system seed backlog entries (`chats=1`, `chat_members=49`) as synced.
- Operational note: Iran currently uses legacy `docker-compose 1.29.2`; DB recreate hit the known `ContainerConfig` failure and was recovered by removing only the exited DB container while preserving the named PostgreSQL volume.

Rollback:

- Either revert this P2 commit and sync/recreate Iran DB-dependent services, or keep the code and set the Iran runtime env values back to the old profile before recreating DB:
  `POSTGRES_SHARED_BUFFERS=128MB`,
  `POSTGRES_EFFECTIVE_CACHE_SIZE=4GB`,
  `POSTGRES_WORK_MEM=4MB`,
  `POSTGRES_MAINTENANCE_WORK_MEM=64MB`,
  `POSTGRES_RANDOM_PAGE_COST=4`,
  `POSTGRES_EFFECTIVE_IO_CONCURRENCY=1`,
  `POSTGRES_CHECKPOINT_TIMEOUT=5min`,
  `POSTGRES_MAX_WAL_SIZE=1GB`,
  `POSTGRES_MIN_WAL_SIZE=80MB`,
  `POSTGRES_WAL_BUFFERS=4MB`.

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

Completion notes:

- Enabled Redis AOF for both compose profiles through env-driven settings: `REDIS_APPENDONLY=yes`, `REDIS_APPENDFSYNC=everysec`, `REDIS_MAXMEMORY=0`, and `REDIS_MAXMEMORY_POLICY=noeviction`.
- Kept `maxmemory-policy=noeviction` because sync/realtime queue loss is worse than a visible write failure.
- Left `REDIS_MAXMEMORY=0` intentionally. Live Iran Redis memory was only about `1.43M`, and a bounded cap should be introduced only after Redis memory alerting and realistic queue high-water data exist.
- Added runtime-env, manifest, and benchmark support for Redis durability keys so production overrides flow through the same deployment surface as PostgreSQL tuning.
- Added `scripts/report_redis_runtime.py` for runtime Redis settings/persistence/queue reporting and `scripts/probe_redis_persistence.py` for non-sensitive synthetic persistence probes.
- Live Iran synthetic probe key `p3:redis:persistence:20260611T0940Z` was written, Redis was restarted, the item was verified after restart, and the key was cleaned up.
- Final Redis benchmark artifact: `tmp/production-benchmark/20260611T094159Z/summary.md`; it passed `redis_runtime_durability` in `2.476s` with `appendonly=yes`, `appendfsync=everysec`, `aof_enabled=1`, `aof_last_write_status=ok`, and empty `sync:outbound` / `sync:retry` queues.
- Quick production benchmark after the Redis restart: `tmp/production-benchmark/20260611T094110Z/summary.md`; it passed all `7` safe tasks against Iran.
- `make sync-health`, `make sync-health-iran`, and `make production-online-health` were clean after the Redis restart/probe.

Rollback:

- Set `REDIS_APPENDONLY=no` in the runtime env or manifest-rendered env, then recreate only Redis-dependent services as needed.
- Keep `REDIS_MAXMEMORY_POLICY=noeviction` during rollback unless a separate queue-loss risk review explicitly approves another policy.
- If a compose-v1 recreate issue appears on Iran, remove only the Redis container while preserving the named Redis volume, then run `docker-compose -f docker-compose.iran.yml up -d redis`.

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

Completion notes:

- Updated the production Iran Nginx template so hashed frontend assets are served directly from `mini_app_dist` instead of being proxied through FastAPI.
- Added explicit static-delivery headers for benchmarkability: `X-Static-Delivery: nginx` on direct frontend static responses.
- Preserved the stale PWA JS chunk recovery behavior by adding an internal `@stale_js_chunk` Nginx fallback for missing `/assets/*.js` requests.
- Kept service worker and manifest responses no-cache while allowing hashed assets and Workbox chunks to use long immutable caching.
- Changed raw `/uploads/` public access to `404`; chat/media files remain protected through `/api/chat/files/{file_id}` and still return `401` without a token.
- Updated legacy/standalone Nginx setup files for Iran and foreign so future manual setup does not reintroduce proxied asset delivery.
- Added `scripts/report_static_delivery.py` and wired `PROFILE=static` into the production benchmark runner. The probe checks immutable asset headers, service-worker/manifest cache policy, raw upload blocking, and protected chat media authorization.
- Backed up the live Iran Nginx site before applying the change: `/etc/nginx/sites-available/trading-bot.p4-backup-20260611T095816Z`.
- Applied the Nginx change live on Iran, then immediately ran the certbot/redirect step. `nginx -t` passed and certbot confirmed HTTPS for `https://coin.gold-trade.ir`.
- Targeted static benchmark artifact: `tmp/production-benchmark/20260611T095946Z/summary.md`; it passed `static_delivery_headers` in `2.117s`.
- Quick production benchmark artifact: `tmp/production-benchmark/20260611T100027Z/summary.md`; it passed all `8` safe tasks, including the new static delivery task.
- `make sync-health`, `make sync-health-iran`, and `make production-online-health` were clean after the Nginx refresh.

Rollback:

- Restore the live backup on Iran if needed:
  `cp /etc/nginx/sites-available/trading-bot.p4-backup-20260611T095816Z /etc/nginx/sites-available/trading-bot && nginx -t && systemctl reload nginx`.
- Alternatively revert this P4 commit, run `make production-online-sync`, then run `configure-nginx` and `issue-cert` together so HTTPS remains valid.
- Do not expose `/uploads/` directly during rollback unless a separate media-authorization/X-Accel stage proves the protected path end-to-end.

### Stage P5 - Worker and Pool Recalibration After DB/Redis Tuning

Goal: decide whether `API_WORKERS=8` remains best after infrastructure tuning.

Expected changes:

- Re-run authenticated/chat and mixed API benchmarks for worker counts `8`, `12`, and optionally `16`.
- Keep per-worker pool sizes bounded.
- Track DB connection count, CPU saturation, RSS memory, p95/p99 latency, and error rate.

Acceptance:

- A higher worker count is accepted only if it improves p95/p99 or throughput without raising DB pressure unsafely.
- If results are flat, keep `8` workers and record the rejected candidate as evidence.

Completion notes:

- Added `scripts/report_worker_http_benchmark.py`, a redaction-safe authenticated HTTP benchmark that runs inside the Iran app container and covers `/api/auth/me`, chat conversations, chat poll, active/my offers, and trade history. It records p50/p95/p99, error rate, DB connection samples, active worker/pool settings, and explicitly notes that FastAPI auth may refresh the sampled user's `last_seen_at` once.
- Added `scripts/run_worker_pool_matrix.py` and wired it into `PROFILE=workers` in `scripts/run_production_benchmark.py`. The matrix backs up Iran `.env`, changes only `API_WORKERS`, recreates only the app service with `--no-deps`, runs the benchmark, records Docker stats snapshots, then restores the original `.env` and app service.
- Hardened the matrix runner for Iran's Docker Compose v1 behavior: app restarts use `up -d --no-deps app` to avoid the known `ContainerConfig` recreate failure on `migration`, and `docker-compose exec` is run with stdin redirected from `/dev/null` so remote `bash -s` scripts continue through marker emission and cleanup.
- Hardened `scripts/recover_cross_server_sync.sh` for the same Iran Docker Compose v1 constraint: recovery now recreates only `sync_worker` with `--no-deps` on both servers instead of touching DB/Redis dependencies.
- Final targeted artifact: `tmp/production-benchmark/20260611T105109Z/summary.md`; `worker_pool_matrix` passed in `85.543s`.
- Result summary:
  - `8` workers: `109.834` RPS, p95 `436.647ms`, p99 `624.062ms`, error rate `0.0`, peak DB connections `28/400`, estimated SQLAlchemy ceiling `126`, peak RSS about `1.84GiB`.
  - `12` workers: `92.336` RPS, p95 `493.984ms`, p99 `572.297ms`, error rate `0.0`, peak DB connections `32/400`, estimated SQLAlchemy ceiling `182`, peak RSS about `2.68GiB`.
  - `16` workers: `88.527` RPS, p95 `505.096ms`, p99 `592.376ms`, error rate `0.0`, peak DB connections `33/400`, estimated SQLAlchemy ceiling `238`, peak RSS about `3.51GiB`.
- Decision: keep Iran at `API_WORKERS=8`. Higher worker counts stayed DB-safe but reduced throughput, increased p95 latency, and consumed more CPU/RSS, so they did not clear the acceptance gate.
- Post-run live state verified on Iran: `.env` restored to `API_WORKERS=8`, `trading_bot_app` is healthy, no P5 benchmark/sampler process remains running, `make sync-recover` drained the three auth `last_seen_at` user updates created by the benchmark, and both `make sync-health` / `make sync-health-iran` report zero backlog.

Rollback:

- No production tuning value changed. If a future P5 run leaves a worker override behind, restore the `.env.p5-worker-matrix-<timestamp>.bak` file on Iran and recreate only app with `docker-compose -f docker-compose.iran.yml up -d --no-deps app`.

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

Completion notes:

- Added `scripts/sync_probe_worker.py` for redaction-safe synthetic sync probes against `market_schedule_overrides`, invalid change-log guard probes, cleanup, and health checks inside the app container.
- Added `scripts/report_cross_server_sync_benchmark.py` and wired `PROFILE=sync` targeted/full benchmark coverage through `scripts/run_production_benchmark.py`.
- Hardened `core/events.log_change()` so queued sync payloads include `change_log_id` when available, letting the worker mark the exact accepted change-log entry delivered.
- Hardened `core/sync_worker.py` and `core/sync_push.py` so peer `/api/sync/receive` is success only when HTTP status, JSON body status, and `errors == 0` all agree. HTTP 200 responses with partial/errors are now retried and kept unsynced.
- Replaced the remaining active `datetime.utcnow()` call sites touched by the sync/event path with the project's UTC helper.
- Live targeted sync artifact: `tmp/production-benchmark/20260611T113726Z/summary.md`; it passed `sync_health_iran`, `sync_health_foreign_peer`, and `cross_server_sync_benchmark` with `0` required failures.
- P6 scenario artifact: `tmp/production-benchmark/20260611T113726Z/sync-p6/summary.md`.
- Measured propagation:
  - Foreign to Iran insert latency `6.231s`, delete latency `6.117s`.
  - Iran to foreign insert latency `7.960s`, delete latency `7.601s`.
- Partial-failure guard result: synthetic invalid `chat_members` resync returned HTTP `200` with payload `errors=1`, but the change-log entry remained `synced=false` and `verified=false`; after guarded cleanup, both health checks returned clean.
- Sync-worker recovery result: with the foreign sync worker stopped, the synthetic change stayed dirty; after restart, peer visibility took `6.282s`, health became clean after `7.352s`, and total recovery was `14.228s`.
- Final state: `cleanup_after` deleted no leftover probe rows or invalid change logs, and final foreign/Iran health reported zero unsynced changes plus empty Redis sync queues.

Rollback:

- The runtime hardening is intentionally conservative. If it must be rolled back, revert the P6 commit and redeploy both servers; this restores the previous looser sync-success interpretation.
- Do not roll back only the benchmark scripts while keeping runtime hardening, because future benchmark summaries would no longer cover the active sync behavior.

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

Completion notes:

- Added `scripts/trading_core_probe_worker.py`, an in-container helper that creates isolated synthetic users/offers/trades with a unique `P7_TRADING_<timestamp>_` prefix and cleans related users, mandatory chat memberships, offers, trades, notifications, change logs, and Redis counters.
- Added `scripts/report_trading_core_benchmark.py`, a host-level orchestrator that runs the P7 worker on Iran, waits for foreign/Iran sync-health to become clean after mutation, cleans synthetic data on both servers, and writes a dedicated artifact under `tmp/production-benchmark/<timestamp>/trading-p7/`.
- Wired the benchmark runner with `PROFILE=trading`. The profile is marked mutating, remains excluded from full mode unless explicitly allowed, but is permitted when directly targeted because it has its own cleanup and sync-health gates.
- The probe exercises the shared bot/web offer parser, bot text-offer handler with Telegram/network boundaries mocked locally, API offer create/list/expire paths, API trade execution, DB-backed notification fanout, and a concurrent race against one wholesale offer.
- Final targeted artifact: `tmp/production-benchmark/20260611T121537Z/summary.md`; `trading_core_benchmark` passed in `103.541s` with `0` required failures.
- P7 scenario artifact: `tmp/production-benchmark/20260611T121537Z/trading-p7/summary.md`.
- Measured latencies on Iran:
  - Bot text-offer handler: p50 `9.181ms`, p95 `120.152ms`.
  - Parser: p50 `1.548ms`, p95 `2.462ms`, p99 `27.795ms`.
  - Offer create: p50 `45.529ms`, p95 `305.486ms`.
  - Offer list: p50 `19.862ms`, p95 `28.168ms`.
  - Offer expire: p50 `22.360ms`, p95 `30.056ms`.
  - Trade execute: p50 `139.593ms`, p95 `309.076ms`, with `3` samples.
  - Notification fanout: p50 `41.820ms`, p95 `54.778ms`.
- Race scenario: `4` simultaneous attempts against one offer produced exactly `1` winner, exactly `1` persisted trade, offer status `completed`, remaining quantity `0`, and race p95 `379.430ms`.
- Cleanup after the final run removed the synthetic rows from both servers. Final foreign/Iran sync-health reported zero unsynced changes and empty Redis sync queues.

Rollback:

- No production tuning value changed in P7. If the benchmark tooling must be removed, revert the P7 commit and run `make production-online-sync`.
- If a future interrupted run leaves synthetic rows behind, run `trading_core_probe_worker.py cleanup --prefix <P7 prefix>` inside both app containers, then verify `make sync-health` and `make sync-health-iran`.

### Stage P8 - Frontend UX Performance Beyond Messenger

Goal: make sure non-chat pages do not regress while Messenger remains the heavy benchmark.

Expected changes:

- Add profile, customer management, market, trade history, admin/user-management, and login flows to the full benchmark.
- Track first usable paint, route transition time, heap, DOM size, and network calls.
- Keep the existing Messenger benchmark as the dedicated deep-dive suite.

Acceptance:

- No major route has unbounded list rendering at production-like row counts.
- Customer/profile/market UI stays within the baseline-regression budget.

Completion notes:

- Added `scripts/frontend_ux_probe_worker.py`, an in-container helper that creates isolated synthetic users, coworkers, accountants, customers, customer/accountant relations, offers, trades, and notifications with a unique `P8_FRONTEND_<timestamp>_` prefix.
- Added `scripts/report_frontend_ux_benchmark.py`, a host-level orchestrator that prepares the synthetic fixture on Iran, runs the browser probe against `https://coin.gold-trade.ir`, cleans both servers, removes prefix-matching change-log and Redis queue residue, records final sync-health, and writes a dedicated artifact under `tmp/production-benchmark/<timestamp>/frontend-p8/`.
- Added `scripts/run_frontend_ux_benchmark.mjs`, a Playwright/Chromium mobile-viewport probe for login, dashboard, own profile, market, notifications, admin user management, public user profile, and customer public profile routes.
- Wired the production benchmark runner with `PROFILE=frontend`. The profile now selects the focused P8 route/list benchmark by default, while the older long/mutating frontend E2E matrix remains opt-in.
- The runner tracks first usable time, DOM nodes, JS heap, request counts, failed responses, console errors, and route-specific list/card counts. Synthetic `mark-all-read` aborts caused only by the benchmark closing its browser context are recorded as ignored failed requests rather than route failures.
- Final targeted artifact: `tmp/production-benchmark/20260611T131305Z/summary.md`; `frontend_ux_benchmark` passed in `63.759s` with `0` required failures.
- P8 scenario artifact: `tmp/production-benchmark/20260611T131305Z/frontend-p8/summary.md`.
- Measured route usability on Iran:
  - Login: `1377.216ms`, DOM `69`, heap `2.6MB`.
  - Dashboard: `2129.926ms`, DOM `129`, heap `2.97MB`.
  - Own profile: `3798.96ms`, DOM `839`, heap `4.34MB`, with `50` project users, `12` accountants, `24` customers, and `8` trade cards rendered after section actions.
  - Market: `1478.665ms`, DOM `87`, heap `3.02MB`.
  - Notifications: `2585.097ms`, DOM `440`, heap `3.34MB`.
  - Admin user management: `2722.171ms`, DOM `1125`, heap `4.15MB`, with bounded `100` visible user rows.
  - Public profile: `2478.513ms`, DOM `214`, heap `3.45MB`.
  - Customer public profile: `2465.017ms`, DOM `216`, heap `3.44MB`.
- Bounded-list gates passed: project users first page `25`, project users after load-more `50`, admin user manager `100`.
- Cleanup after the final run removed `98` synthetic users, `12` accountant relations, `24` customer relations, `26` offers, `8` trades, `12` notifications, `7` prefix-matching change-log rows, and `7` prefix-matching Redis retry entries on Iran. Final foreign/Iran sync-health reported zero unsynced changes and empty Redis sync queues.

Rollback:

- No production runtime tuning value changed in P8. If the benchmark tooling must be removed, revert the P8 commit and run `make production-online-sync`.
- If a future interrupted run leaves synthetic rows behind, run `frontend_ux_probe_worker.py cleanup --prefix <P8 prefix>` inside both app containers, then verify `make sync-health` and `make sync-health-iran`.

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

Completion notes:

- Added `scripts/check_observability_timer.py`, a host-level timer/cron checker used for sync-health sampler and audit-anchor timer readiness on both foreign and Iran hosts.
- Added `scripts/write_observability_audit_probe.py`, which appends a non-sensitive `observability_readiness_probe` audit event so a production host with no previous audit trail can still prove durable audit storage and anchor export.
- Added `scripts/report_observability_readiness.py`, a Stage P9 orchestrator that runs the readiness checks on the Iran target, writes `observability-p9` artifacts, and fails closed on dirty sync-health, missing required timers, non-durable audit anchors, invalid metrics contract, excessive logging overhead, or sensitive artifact scan findings.
- Added `make observability-readiness` and wired `PROFILE=observability` so targeted/full production benchmarks run the P9 readiness report plus the focused local observability regression gate. Quick mode still keeps the lightweight standalone logging-overhead and metrics-target probes.
- Final targeted artifact: `tmp/production-benchmark/20260611T133318Z/summary.md`; `observability_readiness` passed in `30.186s` and `observability_gate` passed in `12.566s`.
- P9 scenario artifact: `tmp/production-benchmark/20260611T133318Z/observability-p9/summary.md`.
- Structured logging overhead on Iran: `329.12us/event` against a `1000.0us` budget, acceptable `True`.
- Metrics contract: backend `memory`, with cross-service authoritative source explicitly documented as `loki_logs_until_explicit_multi_surface_scrape_is_deployed`; API metrics are marked single-process scope, while bot and sync-worker remain logs-only under the current backend.
- Durable audit anchor: the readiness probe created a non-sensitive audit event, exported anchor head `d4942167-f794-4076-b6ce-c398bb159d77`, verified `2` durable audit-trail records, and shipped the compact anchor line to the local relay artifact.
- Required sync-health sampler timers were active on both foreign and Iran. Audit anchor export/shipper timers reported `not_required` because no production anchor sink is configured in the manifest yet; the direct export and shipper code paths were still verified.
- Sensitive artifact scan found `0` blocked patterns. Final foreign/Iran sync-health reported zero unsynced changes and empty Redis sync queues.

Rollback:

- No production runtime tuning value changed in P9. If the benchmark tooling must be removed, revert the P9 commit and run `make production-online-sync`.
- The only durable side effect is the non-sensitive audit readiness probe event in the append-only audit trail; it should not be deleted because audit trails are intentionally immutable.

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

Completion notes:

- Added `scripts/report_deployment_restart_benchmark.py`, a Stage P10 orchestrator that runs the production release rehearsal with `IRAN_CONNECTIVITY_MODE=yes` and `IRAN_SHARED_DATA_MODE=skip`, parses timestamped release logs into per-phase durations, and writes `deployment-p10` artifacts.
- Added `make production-deployment-restart` and wired `PROFILE=deployment` so targeted/full production benchmarks include the P10 deployment/restart/rollback gate. Quick mode keeps only the lightweight production healthcheck.
- Before controlled restart probes, the benchmark creates an Iran PostgreSQL dump on the host and records its path, size, and sha256. The final P10 run created `/srv/trading-bot/backups/p10-deployment-20260611T134805Z.sql`, size `227148` bytes, sha256 `521bebdc271d0d9b36129c957c0073aa30349f33bd16aeeacfc28a2154796a65`.
- Final targeted artifact: `tmp/production-benchmark/20260611T134805Z/summary.md`; `deployment_restart_benchmark` passed in `523.533s` with `0` required failures.
- P10 scenario artifact: `tmp/production-benchmark/20260611T134805Z/deployment-p10/summary.md`.
- Measured release phase durations: local validation `6.0s`, foreign deploy `129.0s`, build `168.0s`, sync `12.0s`, image ship `50.0s`, image load `45.0s`, Iran deploy `11.0s`, shared data `0.0s` skipped by explicit mode, final health `14.0s`. Total production release subprocess duration was `474.41s`.
- Controlled restart probes passed on Iran: app recovered through `/api/config` in `10.003s`, sync-worker reached container-running in `11.334s`, Redis recovered through Redis ping plus `/api/config` in `1.284s`, and PostgreSQL recovered through `pg_isready` plus `/api/config` in `3.083s`.
- The release stderr contained two transient `curl: (56) Recv failure: Connection reset by peer` lines while the new app container was coming up; final health, restart probes, and sync-health all passed, so this is recorded as a startup-window observation rather than a gate failure.
- Final foreign/Iran sync-health reported zero unsynced changes and empty Redis sync queues.

Rollback:

- Revert the P10 tooling commit and run `make production-online-sync` if the benchmark tooling itself must be removed.
- If a release rollback is needed, run `make production-online-deploy && make production-online-health` after restoring the intended git/image state.
- If a DB restore is required, stop app/sync-worker first, restore the recorded pg_dump backup into PostgreSQL, restart app/sync-worker, then verify `make sync-health` and `make sync-health-iran`.

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

Completion notes:

- Added `scripts/report_final_release_gate.py`, a Stage P11 final gate that validates every P0-P10 artifact, checks the production deployment manifest identity fields, runs fresh `check-local`, production health, and foreign/Iran sync-health probes, verifies Messenger release readiness from redacted summary artifacts, checks P9 observability minimums, and checks P10 backup/restart/rollback evidence.
- Added `make production-release-gate` and wired `PROFILE=release` into the production benchmark runner. The runner writes both the standard benchmark artifact and `docs/PRODUCTION_RELEASE_READINESS_REPORT.md`.
- Final targeted artifact: `tmp/production-benchmark/20260611T141830Z/summary.md`; `final_release_gate` passed in `18.415s` with `0` required failures.
- P11 scenario artifact: `tmp/production-benchmark/20260611T141830Z/final-release-p11/summary.md`.
- Final report: `docs/PRODUCTION_RELEASE_READINESS_REPORT.md`.
- Required checks: `16/16`; required pass rate `100.0%`; warning-adjusted score `92.0/100`; accepted warnings `4`.
- Live production health passed. Fresh foreign and Iran sync-health both reported zero unsynced changes and empty Redis sync queues.
- Messenger release readiness passed: `14/14` surfaces measured, blocked surfaces `0`. Median comparison deltas showed list-ready improved on `9/12` scenarios, chat-ready improved on `12/12`, DOM nodes improved on `12/12`, JS heap improved on `7/12`, and context-menu latency remains accepted debt with `1/12` scenarios improved and `11/12` worse.
- Observability minimums passed: structured logging overhead `329.12us/event` under the `1000us` budget, metrics contract documented, durable audit anchor export verified, and sync sampler timers active. The memory metrics backend and non-immutable audit anchor sink remain accepted warnings with owners and release notes.
- Backup/rollback evidence passed: P10 backup `/srv/trading-bot/backups/p10-deployment-20260611T134805Z.sql`, size `227148` bytes, sha256 `521bebdc271d0d9b36129c957c0073aa30349f33bd16aeeacfc28a2154796a65`; app/sync-worker/Redis/PostgreSQL restart probes passed; rollback commands are recorded.

Final accepted warnings:

- `messenger_context_menu_latency_debt`: owner `Messenger performance`; release note says context-menu latency is accepted as post-release performance debt because chat-ready, DOM, heap, and surface readiness are green.
- `messenger_raw_performance_artifact_contains_synthetic_tokens`: owner `Release engineering`; release note says raw `tmp/messenger-benchmark/performance-results.json` must not be published, and only redacted comparison/surface artifacts should be used.
- `metrics_memory_backend_is_not_aggregate`: owner `Observability/monitoring`; release note says memory metrics remain process-local and Loki/sync-health samplers are the production cross-service source until a real aggregator/exporter is approved.
- `audit_anchor_sink_not_immutable_retention`: owner `Observability/ops`; release note says audit anchor export/shipper paths exist but compliance-grade immutable remote retention is deferred until a dedicated sink is provisioned.

Rollback:

- P11 added benchmark/reporting tooling only; no runtime setting was changed.
- If P11 tooling must be removed, revert the P11 commit and run `make production-online-sync`.

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
2. Run the first safe `full` benchmark against the current 8-worker direct-Postgres setup.
3. Apply PostgreSQL tuning in Stage P2.
4. Run targeted DB and full-product quick benchmark.
5. Stage P3 Redis durability is complete.
6. Start Stage P4 Nginx/static/media delivery optimization.
7. Run targeted static/media benchmark plus quick production benchmark after P4.

This order is deliberate: PostgreSQL, Redis, Nginx, and worker tuning are all useful, but near release the benchmark harness is the control system that prevents optimization work from becoming untraceable risk.
