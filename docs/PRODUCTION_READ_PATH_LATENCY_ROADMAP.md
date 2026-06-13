# Production Read Path Latency Roadmap

Status: Stage RPL4 completed on 2026-06-13 by reducing Messenger
`/api/chat/poll` read amplification without changing conversation-list
behavior.

Last updated: 2026-06-13

## Context

Stage L proved that the system can pass the short official target
`500 RPS / 10m`, but the `500 RPS / 30m` soak failed latency gates:

```text
artifact: tmp/production-benchmark/20260613T123638Z/load-pool-matrix/
effective_rps: 475.46
p95: 5594.85ms
p99: 11504.99ms
dropped_iteration_ratio: 4.88%
max_postgres_connections: 344
nginx_5xx_delta: 5
post_run_sync_health: clean
```

The failure was not a crash, memory leak, Redis exhaustion, or hard
PostgreSQL connection exhaustion. The measured problem is broad read-path
latency under sustained concurrency.

## Hot Surfaces From L9

| Endpoint family | L9 P95 | Initial classification |
| --- | ---: | --- |
| `market_state` | `6046.04ms` | High-frequency read; safe candidate for very short cache. |
| `chat_poll` | `5943.24ms` | Polling/read amplification; needs careful Messenger-specific work. |
| `admin_market_current` | `5804.73ms` | Read-heavy current-message lookup; likely cacheable. |
| `project_users` | `5792.50ms` | Directory query; inspect indexes and pagination access. |
| `users_public_search` | `5785.73ms` | Search/visibility query; index and query-plan work required. |
| `accountant_relations` | `5781.70ms` | Relation read includes pending-expiry sweep; avoid write work in hot read path if possible. |
| `offers_list` | `5773.88ms` | Active offers read; query/index and serialization review. |
| `chat_conversations` | `5765.80ms` | Complex Messenger aggregation; requires targeted query-plan work. |
| `admin_users` | `5750.55ms` | Admin list read; pagination/index review. |
| `offers_my` | `5745.78ms` | Owner offer history read; query/index and serialization review. |

## Rules

- Do not raise worker counts or DB pool sizes as the first response.
- Do not rewrite Messenger or market logic under release pressure.
- Prefer changes that are reversible, measurable, and scoped to one hot
  surface.
- Every optimization must name its correctness budget, for example a TTL,
  invalidation event, or pagination/index assumption.
- Rerun a targeted benchmark before rerunning the full L9 soak.

## Stages

| Stage | Status | Scope |
| --- | --- | --- |
| `RPL0` | Complete | Artifact review and hot-surface inventory from L9. |
| `RPL1` | Complete | Low-risk micro-cache for market runtime state; cache only live reads and invalidate after market runtime mutations. |
| `RPL2` | Complete | Add query-plan/report coverage for the L9 hot read surfaces not already covered by P2/P7: public user search, project users, relation lists, admin users, offers list/my, and chat conversations/poll. |
| `RPL3` | Complete | Apply index/query fixes only where RPL2 shows a clear plan problem; closed with no index/query mutation because current Iran plans are fast and the remaining issue is read amplification/concurrency pressure. |
| `RPL4` | Complete | Reduce polling/read amplification in Messenger and market screens where backend correctness permits it. |
| `RPL5` | Pending | Add narrowly-scoped Redis or in-process caches for read-only/current-state endpoints such as current market/admin message reads, with explicit TTL and invalidation. |
| `RPL6` | Pending | Rerun targeted 500RPS benchmark, then rerun L9 `500 RPS / 30m` only if targeted results improve p95/p99 materially. |

## Stage RPL1 - Market Runtime Micro-Cache

Why this stage first:

- `market_state` was the worst L9 endpoint by p95.
- It is a high-frequency read endpoint.
- Its response can tolerate a very small freshness budget.
- The current implementation evaluates schedule/overrides and runtime state on
  every request.

Change:

- Cache `get_market_runtime_view()` for live reads where `current_time is None`.
- Default TTL: `1.0` second via
  `TRADING_BOT_MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS`.
- Bypass cache for explicit `current_time` calls so deterministic tests and
  scheduled calculations stay exact.
- Invalidate cache after market runtime state mutations:
  - offer-created runtime counter update;
  - market open transition;
  - market close transition;
  - initial runtime-state creation.

Expected effect:

- Reduce repeated DB/schedule work for `/api/trading-settings/market-state`
  during high fan-out polling.
- No expected behavior change beyond at most one second of live-read staleness.

Validation:

- Unit tests cover cache hit, explicit-time bypass, and cache reset between
  tests.
- `python3 -m unittest tests.test_market_transition_service` passed.
- `python3 -m py_compile core/services/market_transition_service.py tests/test_market_transition_service.py` passed.
- `git diff --check` passed.
- Follow-up benchmark must compare endpoint p95 for `market_state` before and
  after deployment.

## Stage RPL2 - Production Read-Path Query Plans

Why this stage:

- Stage L showed broad read-path latency rather than one hard resource limit.
- Before adding indexes or caches, each hot read surface needs a repeatable
  `EXPLAIN ANALYZE` entrypoint.
- The report must mirror the Stage L endpoint parameters so future tuning is
  measured against the same traffic contract.

Change:

- Added `scripts/report_production_read_path_query_plans.py`.
- Added `make production-read-path-query-plans`.
- The report covers:
  - `GET /api/users-public/search?q=loadtest&limit=20`;
  - `GET /api/users-public/{id}/project-users?limit=30`;
  - `GET /api/accountants/owner-relations`;
  - `GET /api/customers/owner-relations`;
  - `GET /api/users/?limit=30`;
  - `GET /api/offers/?limit=30`;
  - `GET /api/offers/my?limit=30`;
  - `GET /api/admin-messages/market/current`;
  - `GET /api/chat/conversations`;
  - `GET /api/chat/poll`.

Correctness budget:

- Read-only `EXPLAIN ANALYZE` only.
- Default per-statement timeout: `15000ms`.
- No route/runtime behavior changes.
- `chat/poll` is intentionally documented as reading full direct/room
  conversation projections and then filtering unread rows in Python. The report
  includes a direct unread-only diagnostic query only as RPL4 evidence, not as a
  claim about current route behavior.

Validation:

- `python3 -m py_compile scripts/report_production_read_path_query_plans.py`
  passed.
- `python3 scripts/report_production_read_path_query_plans.py --help` passed.
- Local Compose read-only execution passed:
  `docker compose exec -T app python scripts/report_production_read_path_query_plans.py --output-dir tmp/production-read-path-query-plans --statement-timeout-ms 10000`.
- Local artifact:
  `tmp/production-read-path-query-plans/production-read-path-query-plans-20260613T134858Z.json`.
- Initial local result had no report errors. Several Seq Scans appear on the
  current small dataset, but execution times were sub-5ms; Stage RPL3 must
  decide index/query changes only after running this report on Iran production
  data and comparing row counts/plans.

## Stage RPL3 - Index/Query Decision

Decision:

- No production index migration or endpoint query rewrite was applied in RPL3.
- The RPL2 Iran report did not expose one clear slow query-plan problem.
- The remaining Stage L/L9 bottleneck is still classified as broad read
  amplification and concurrency pressure, not a single missing index.

Iran evidence:

```text
generated_at: 2026-06-13T14:04:01Z
source: docker-compose exec -T app python scripts/report_production_read_path_query_plans.py --json --statement-timeout-ms 10000
errors: 0
skipped: 0
```

Key execution timings from Iran:

| Query case | Execution | Planning | Notes |
| --- | ---: | ---: | --- |
| `chat_conversations_direct` | `2.275ms` | `21.091ms` | Highest planning cost; not an index-only signal. |
| `chat_poll_direct_full` | `1.471ms` | `10.476ms` | Current poll path reads full projection before Python unread filtering. |
| `chat_poll_direct_unread_diagnostic` | `1.427ms` | `11.205ms` | Useful evidence for RPL4, not current route behavior. |
| `chat_conversations_rooms` | `1.065ms` | `2.703ms` | Fast execution; contains Seq Scans on small tables. |
| `project_users_directory` | `0.596ms` | `1.858ms` | Seq Scans present, but no slow execution on current data. |
| `users_public_search_loadtest` | `0.507ms` | `2.028ms` | Seq Scans present, but zero result rows and fast execution. |
| `customer_relations_list` | `0.415ms` | `1.071ms` | Fast; pending sweep remains a read-path design concern. |
| `admin_users_list` | `0.194ms` | `0.192ms` | Uses index scan on users. |
| `offers_list_active` | `0.204ms` | `1.141ms` | Fast on current data. |
| `offers_my_owner_history` | `0.178ms` | `0.190ms` | Fast on current data. |
| `admin_market_current` | `0.348ms` | `2.148ms` | Fast; remains a candidate for scoped cache in RPL5. |

Why no index was added:

- All measured executions are sub-3ms on current Iran production data.
- No temp reads/writes were observed.
- Shared read blocks were `0`, so the reported query cases were served from
  cache and did not show disk pressure.
- Several Seq Scans exist, but the involved tables are currently small enough
  that PostgreSQL reasonably chooses sequential scans.
- Adding indexes now would increase write cost, migration surface, and future
  maintenance without evidence that it would fix the L9 p95/p99 problem.

RPL3 output:

- Treat index work as deferred evidence-based debt.
- Revisit index candidates only after row counts grow or a later production
  report shows:
  - repeated execution time above `50ms` for an individual hot read query;
  - temp block usage;
  - high shared read blocks under warm cache;
  - bad row-estimate drift with high actual rows;
  - a Stage L targeted benchmark proving one endpoint family dominates latency.

## Stage RPL4 - Messenger Poll Read-Amplification Reduction

Decision:

- Optimize `/api/chat/poll` first because RPL3 showed the main problem is
  query shape/read amplification, not a missing index.
- Keep `/api/chat/conversations` behavior unchanged.
- Do not add a cache to unread state in this stage; correctness requires poll
  to reflect read/unread/mute state directly from the database.

Change:

- Replaced the poll route's direct full conversation-list projection with
  `build_direct_poll_summary_stmt()`.
- Added `list_room_poll_summaries()` and `build_room_poll_summary_stmt()` for
  group/channel poll reads.
- The poll route now reads only rows that can affect the poll response:
  - direct conversations with unread or muted state;
  - rooms/channels with unread, unread mention, or muted state.
- The poll route no longer reads last-message preview fields, avatar fields,
  member counts, pin/order state, room send capability, or full conversation
  list rows just to compute unread counters.
- Updated `scripts/report_production_read_path_query_plans.py` so future
  reports explain the new RPL4 poll summary queries instead of the old full
  projection path.

Correctness budget:

- No TTL and no stale unread state.
- The response schema stays unchanged:
  - `total_unread`;
  - `unread_chats_count`;
  - `conversations_with_unread`;
  - `muted_conversation_ids`;
  - `total_unread_mentions`.
- Customer direct-chat visibility filtering is still applied after the direct
  summary query, matching the old route behavior.
- Conversation-list ordering and full preview behavior are intentionally left
  untouched.

Validation:

- `python3 -m unittest tests.test_chat_router_direct_reads tests.test_chat_router_remaining_paths tests.test_chat_room_service_room_read_models tests.test_chat_service_projection_and_send_helpers` passed.
- `python3 -m py_compile api/routers/chat.py core/services/chat_service.py core/services/chat_room_service.py scripts/report_production_read_path_query_plans.py` passed.
- `docker compose exec -T app python scripts/report_production_read_path_query_plans.py --json --statement-timeout-ms 10000` passed locally with no report errors.
- Local post-change query-plan coverage reported:
  - `chat_poll_direct_summary`: execution about `1.425ms`, planning about
    `7.45ms`, no temp blocks;
  - `chat_poll_rooms_summary`: execution about `1.504ms`, planning about
    `1.427ms`, no temp blocks.
- `git diff --check` passed.

Next:

- Stage RPL5 should target tightly scoped current-state caches, especially
  reads that are naturally cacheable and already have clear invalidation points.
- Stage RPL6 should run a targeted 500RPS benchmark before deciding whether a
  full L9 rerun is worth the cost.
