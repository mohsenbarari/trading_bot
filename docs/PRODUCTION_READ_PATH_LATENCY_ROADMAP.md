# Production Read Path Latency Roadmap

Status: Stage RPL1 completed on 2026-06-13 with one low-risk hot-path fix:
short in-process caching for `/api/trading-settings/market-state`.

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
| `RPL2` | Pending | Add query-plan/report coverage for the L9 hot read surfaces not already covered by P2/P7: public user search, project users, relation lists, admin users, offers list/my, and chat conversations/poll. |
| `RPL3` | Pending | Apply index/query fixes only where RPL2 shows a clear plan problem. |
| `RPL4` | Pending | Reduce polling/read amplification in Messenger and market screens where backend correctness permits it. |
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
