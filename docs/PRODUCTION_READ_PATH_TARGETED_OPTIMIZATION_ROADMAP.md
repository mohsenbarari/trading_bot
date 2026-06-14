# Production Read Path Targeted Optimization Roadmap

Status: RPO0 through RPO5 completed; RPO6 is pending the combined short benchmark.
This roadmap is for targeted read-path optimization, not for another broad
benchmark loop.

Last updated: 2026-06-14

## Purpose

The RPL roadmap closed with useful low-risk fixes, but the targeted post-RPL
`500 RPS / 2m` benchmark did not beat the accepted L7 shape. The next step is
not to rerun the full L9 soak or increase infrastructure knobs. The next step
is to reduce endpoint-level read-path cost in the endpoint families that still
show high p95/p99 under concurrency.

This roadmap uses a strict rule:

- Each stage changes one endpoint family or one shared read helper.
- Each stage must define a correctness budget before implementation.
- Each stage must run a short targeted benchmark after implementation.
- A full L9 `500 RPS / 30m` rerun is allowed only after targeted evidence beats
  the accepted L7 shape on p95, p99, and dropped-iteration ratio.

## Current Evidence

Accepted L7 capacity proof:

```text
artifact: tmp/production-benchmark/20260613T111706Z/load-pool-matrix/
shape: workers=24 DB_POOL_SIZE=10 DB_MAX_OVERFLOW=4 LOAD_RUNNER_SHARDS=2
duration: 500 RPS / 10m
effective_rps: 498.21
p95: 559.76ms
p99: 812.07ms
dropped_iteration_ratio: 0.324%
request_failure_rate: 0%
nginx_5xx_delta: 0
max_postgres_connections: 248
```

RPL6 targeted run:

```text
artifact: tmp/production-benchmark/20260613T160851Z/load-pool-matrix/
shape: workers=24 DB_POOL_SIZE=10 DB_MAX_OVERFLOW=4 LOAD_RUNNER_SHARDS=2
duration: 500 RPS / 2m
effective_rps: 486.86
p95: 637.08ms
p99: 1534.14ms
dropped_iteration_ratio: 2.208%
request_failure_rate: 0%
nginx_5xx_delta: 0
max_postgres_connections: 250
```

RPL6 endpoint p95 highlights:

| Endpoint family | p95 | p99 |
| --- | ---: | ---: |
| `chat_conversations` | `736.51ms` | `1903.41ms` |
| `notifications_unread` | `711.00ms` | `1441.09ms` |
| `direct_messages` | `710.62ms` | `1922.96ms` |
| `offers_list` | `704.54ms` | `1693.59ms` |
| `customer_relations` | `699.44ms` | `1693.16ms` |
| `offers_my` | `693.54ms` | `1755.50ms` |
| `users_public_detail` | `678.09ms` | `1447.82ms` |
| `users_public_search` | `665.38ms` | `1763.55ms` |
| `chat_poll` | `664.66ms` | `1644.14ms` |

## Guardrails

- Keep production runtime defaults unchanged unless a separate capacity
  roadmap says otherwise:
  - `API_WORKERS=8`;
  - `DB_POOL_SIZE=8`;
  - `DB_MAX_OVERFLOW=6`.
- Use the accepted diagnostic benchmark shape for short optimization proof
  runs unless a stage explicitly needs a narrower scenario:
  - `workers=24`;
  - `DB_POOL_SIZE=10`;
  - `DB_MAX_OVERFLOW=4`;
  - `LOAD_RUNNER_SHARDS=2`;
  - no media and no mutating traffic unless the stage requires it.
- Do not add broad caches to mutable user state.
- Do not add indexes only because a table has a sequential scan. Add an index
  only if Iran `EXPLAIN ANALYZE` shows a clear production-data problem.
- Do not rewrite Messenger architecture in this roadmap.
- Do not rerun L9 until at least one short targeted benchmark beats L7 on:
  - p95;
  - p99;
  - dropped-iteration ratio;
  - request failures;
  - Nginx 5xx delta.

## Stages

| Stage | Status | Scope |
| --- | --- | --- |
| `RPO0` | Complete | Add endpoint-family attribution/read-path reporting needed for this roadmap and freeze the benchmark contract. |
| `RPO1` | Complete | Optimize `chat_conversations` read shape without changing Messenger behavior. |
| `RPO2` | Complete | Optimize `direct_messages` and room message read pagination/projection. |
| `RPO3` | Complete | Optimize `offers_list` and `offers_my` read paths. |
| `RPO4` | Complete | Optimize relation and public-user reads: `customer_relations`, `users_public_detail`, `users_public_search`, and related directory reads. |
| `RPO5` | Complete | Review `notifications_unread` and remaining poll/read counters for narrow, correctness-safe improvements. |
| `RPO6` | Pending | Run a combined short benchmark across accepted RPO changes and decide whether L9 rerun is justified. |

## Stage RPO0 - Attribution Contract

Goal:

- Make sure each next optimization has endpoint-family evidence before code
  changes.
- Avoid another broad "change and hope" loop.

Tasks:

- Review the existing k6 endpoint breakdown in the latest L7/RPL6 artifacts.
- Confirm whether the current benchmark output is enough to isolate:
  - `chat_conversations`;
  - `direct_messages`;
  - `offers_list`;
  - `offers_my`;
  - `customer_relations`;
  - `users_public_detail`;
  - `users_public_search`;
  - `notifications_unread`.
- If needed, add a small report/helper that extracts endpoint-family p50/p95/p99,
  failure rate, and request count from benchmark artifacts.
- Do not change production behavior in this stage.

Correctness budget:

- Reporting-only.
- No endpoint code changes.
- No DB migrations.

Validation:

- Run the report against:
  - accepted L7 artifact;
  - RPL6 artifact.
- The report must identify the slowest endpoint families and preserve the source
  artifact path in its output.

Exit criteria:

- A short, repeatable endpoint attribution report exists.
- RPO1 can start with a clear target and baseline.

Implementation:

- Added `scripts/report_read_path_endpoint_attribution.py`.
- Added `make production-read-path-attribution`.
- The report supports existing `load-pool-matrix` artifacts and reads the
  underlying k6 endpoint metrics so each endpoint row includes:
  - request count;
  - successful and failed request counts;
  - failure rate;
  - p50, p90, p95, p99, average, and max latency;
  - comparison deltas against the first artifact.

Validation:

- Ran:
  `make production-read-path-attribution ARGS="--json --limit 12"`.
- Artifacts:
  - `tmp/production-read-path-attribution/read-path-endpoint-attribution-20260613T184622Z.json`;
  - `tmp/production-read-path-attribution/read-path-endpoint-attribution-20260613T184622Z.md`.
- The report compared:
  - accepted L7 artifact
    `tmp/production-benchmark/20260613T111706Z/load-pool-matrix/`;
  - RPL6 artifact
    `tmp/production-benchmark/20260613T160851Z/load-pool-matrix/`.
- Focused unit coverage added in `tests/test_read_path_endpoint_attribution.py`.

RPO0 result:

- RPO0 is complete.
- `chat_conversations` remains the correct first RPO1 target:
  - L7 p95/p99: `656.76ms` / `888.72ms`;
  - RPL6 p95/p99: `736.51ms` / `1903.41ms`;
  - RPL6 request count: `8155`;
  - RPL6 failure rate: `0%`.

## Stage RPO1 - Chat Conversations Read Shape

Goal:

- Reduce p95/p99 for `GET /api/chat/conversations`.
- Preserve current visibility, unread, mute, pinned, preview, role-tag, avatar,
  and direct/group/channel behavior.

Likely investigation points:

- Over-wide projections returned from conversation list queries.
- Repeated profile/member metadata hydration.
- Expensive unread/mention/reaction aggregates in the hot list path.
- Direct and room conversation branches that can be split into lighter summary
  queries.
- Python-side filtering after broad DB reads.

Allowed changes:

- Narrow selected columns.
- Split heavy projections into a lightweight list query plus small batched
  lookups where it reduces total work.
- Add short-lived Redis cache only for stable, non-user-specific metadata such
  as role label dictionaries or capability constants.
- Add an index only if Iran query plans show a specific high-cost query.

Disallowed changes:

- No Messenger feature removal.
- No stale unread counts.
- No caching of per-user unread state unless invalidation is exact and covered.
- No virtualized frontend work in this roadmap.

Validation:

- Focused backend tests for conversation visibility and schema.
- Read-path query-plan report for chat conversations.
- Short targeted benchmark with endpoint breakdown.

Exit criteria:

- `chat_conversations` p95 improves versus RPL6 without increasing request
  failure rate, Nginx 5xx delta, or PostgreSQL connection pressure.

Implementation slice 1:

- Added `build_direct_conversation_other_user_id_expr()` in
  `core/services/chat_service.py`.
- Extended `build_direct_conversation_list_stmt()` with optional
  `allowed_target_ids`.
- Updated `GET /api/chat/conversations` so customer viewers push their allowed
  direct-target filter into SQL instead of relying only on Python-side filtering
  after the full direct list has already been read.
- Kept the Python-side filter as a defensive correctness layer.

Correctness budget for slice 1:

- Applies only when the viewer is an active customer and therefore already has
  an explicit allowed target set.
- Does not change non-customer conversation queries.
- Does not remove the existing post-query visibility filter.
- Does not cache unread or mutable per-user state.

Validation for slice 1:

- `python3 -m unittest tests.test_read_path_endpoint_attribution tests.test_chat_service_projection_and_send_helpers tests.test_chat_router_direct_reads`
  passed.
- `python3 -m py_compile scripts/report_read_path_endpoint_attribution.py core/services/chat_service.py api/routers/chat.py tests/test_read_path_endpoint_attribution.py`
  passed.

Slice 1 benchmark:

- Initial local tmux run missed the load-runner environment and only validated
  restore behavior:
  - artifact: `tmp/production-benchmark/20260613T185648Z/load-pool-matrix`;
  - load status: failed with `LOAD_RUNNER_HOST is required`;
  - Iran profile restore: passed.
- Re-ran with the real load-runner:
  - command shape: `500 RPS / 2m`, `API_WORKERS=24`,
    `DB_POOL_SIZE=10`, `DB_MAX_OVERFLOW=4`, `LOAD_RUNNER_SHARDS=2`,
    no media, no mutations;
  - artifact: `tmp/production-benchmark/20260613T190022Z/load-pool-matrix`;
  - `chat_conversations` p95/p99: `654.42ms` / `978.32ms`;
  - overall p95/p99: `535.25ms` / `863.57ms`;
  - request failure rate: `0%`;
  - Nginx 5xx delta from first sample: `0`;
  - Iran profile restore: `API_WORKERS=8`, `DB_POOL_SIZE=8`,
    `DB_MAX_OVERFLOW=6`.

Result after slice 1:

- `chat_conversations` p95 improved materially versus RPL6
  (`736.51ms` -> `654.42ms`) and is competitive with L7 (`656.76ms`).
- `chat_conversations` p99 improved materially versus RPL6
  (`1903.41ms` -> `978.32ms`) but is still above L7 (`888.72ms`).
- This is enough to continue RPO1 with one more narrow read-shape slice, but
  not enough to justify a full L9 rerun by itself.

Attempted slice 2:

- Replaced the room-conversation active-member-count correlated scalar subquery
  with one grouped aggregate subquery joined by `chat_id`.
- This preserved member-count semantics, but it was not a win on the production
  benchmark shape.

Validation for slice 2:

- `python3 -m unittest tests.test_chat_room_service_room_read_models tests.test_chat_service_projection_and_send_helpers tests.test_chat_router_direct_reads`
  passed.
- `python3 -m py_compile core/services/chat_room_service.py core/services/chat_service.py api/routers/chat.py`
  passed.

Slice 2 benchmark and decision:

- Post-deploy artifact:
  `tmp/production-benchmark/20260613T191155Z/load-pool-matrix`.
- `chat_conversations` p95/p99 regressed versus slice 1:
  - slice 1: `654.42ms` / `978.32ms`;
  - slice 2: `696.24ms` / `1170.85ms`.
- Request failure rate stayed `0%`, and Iran restored to
  `API_WORKERS=8`, `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=6`.
- Decision: reject and revert slice 2. For the current data shape, the grouped
  active-member aggregate is more expensive than the existing correlated count.

RPO1 final result:

- Accepted change: SQL-side direct-target visibility filtering for active
  customer viewers.
- Accepted benchmark artifact:
  `tmp/production-benchmark/20260613T190022Z/load-pool-matrix`.
- Final accepted `chat_conversations` improvement versus RPL6:
  - p95: `736.51ms` -> `654.42ms`;
  - p99: `1903.41ms` -> `978.32ms`.
- Remaining debt: p99 is still above accepted L7 (`888.72ms`), so broad
  conversation projection width remains a future debt item. It is not expanded
  in RPO1 to avoid a higher-risk Messenger behavior change.
- RPO1 is closed.

## Stage RPO2 - Message List Reads

Goal:

- Reduce p95/p99 for `direct_messages` and the equivalent room message read
  endpoints.
- Preserve pagination, unread-anchor behavior, seen-state behavior, album/media
  metadata, forwarded-channel attribution, and sender tags.

Likely investigation points:

- Message list query selecting more columns than initial viewport needs.
- Sender/profile metadata loaded repeatedly per page.
- Media/album metadata loaded in a way that creates extra queries.
- Count queries that are not required for the visible page.
- Older-message prepend and first-unread anchor reads that can share logic.

Allowed changes:

- Narrow message list projections.
- Batch sender/profile/media metadata by message page.
- Keep media dimensions/aspect-ratio fields available for scroll stability.
- Add keyset pagination improvements only if they preserve current anchors.

Disallowed changes:

- No regression in unread anchor placement.
- No regression in seen/read receipts.
- No change to album ordering or media captions.
- No delayed rendering of required message metadata.

Validation:

- Existing Messenger tests covering unread anchors, seen state, albums, media,
  forwarding, and room/direct behavior.
- Targeted read benchmark for message list endpoints.

Exit criteria:

- Message-read p95/p99 improves versus RPL6 while keeping Messenger behavior
  unchanged.

Implementation slice 1:

- In `GET /api/chat/messages/{user_id}`, replaced the full `db.get(User, id)`
  target lookup with a narrow `select(User.id)` existence check.
- This removes a full user-row hydration from the direct-message hot read path.
- Pagination, around-message behavior, customer visibility checks, and message
  serialization are unchanged.

Validation for slice 1:

- `python3 -m unittest tests.test_chat_router_direct_reads tests.test_chat_room_service_room_read_models tests.test_chat_service_projection_and_send_helpers`
  passed.
- `python3 -m py_compile api/routers/chat.py core/services/chat_room_service.py core/services/chat_service.py tests/test_chat_router_direct_reads.py`
  passed.

RPO2 benchmark and decision:

- Benchmark artifact:
  `tmp/production-benchmark/20260614T053036Z/load-pool-matrix`.
- Shape: `500 RPS / 2m`, `workers=24`, `DB_POOL_SIZE=10`,
  `DB_MAX_OVERFLOW=4`, `LOAD_RUNNER_SHARDS=2`, no media mutations, then
  restored Iran to `API_WORKERS=8`, `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=6`.
- Run quality caveat: request failure rate stayed `0%` and sampler passed, but
  effective throughput dropped to `420.02 RPS` with `9307` dropped iterations
  (`15.511%`). Treat this as endpoint evidence, not as a clean capacity gate.
- Message endpoint comparison versus RPL6:
  - `direct_messages` p95/p99: `710.62ms` / `1922.96ms` ->
    `691.08ms` / `1839.39ms`;
  - `room_messages` p95/p99: `650.28ms` / `2086.34ms` ->
    `942.80ms` / `1897.14ms`.
- A rerun was attempted with the same shape but stopped after k6 exceeded the
  expected duration without producing a clean local summary. Iran remained
  restored and healthy at `API_WORKERS=8`, `DB_POOL_SIZE=8`,
  `DB_MAX_OVERFLOW=6`.
- Decision: keep slice 1 because it is low-risk and removes one unnecessary
  direct-message user hydration. Do not expand RPO2 into message serializer,
  media, reply, forwarding, or room-history projection changes before release;
  those paths are behavior-sensitive and the benchmark did not justify the
  extra risk.
- RPO2 is closed as a limited improvement with message-read projection debt
  deferred.

## Stage RPO3 - Offers Read Paths

Goal:

- Reduce p95/p99 for `offers_list` and `offers_my`.
- Preserve market correctness, ownership visibility, active/expired state,
  customer/accountant constraints, and current response schema.

Likely investigation points:

- Repeated user/account/customer joins.
- Sorting/filtering over non-selective conditions.
- Serialization cost from returning fields not needed by list screens.
- Repeated market runtime/current-message reads adjacent to offers screens.

Allowed changes:

- Query projection narrowing.
- Batch lookup of related user/account data.
- Small TTL cache for stable reference metadata.
- Index work only with production query-plan evidence.

Disallowed changes:

- No stale active/expired offer state.
- No stale user permission visibility.
- No broad cache of active offers unless invalidation is exact.

Validation:

- Offers router/service tests.
- Query-plan report for active offer list and owner offer history.
- Short targeted benchmark focused on offers reads.

Exit criteria:

- `offers_list` and `offers_my` improve without increasing write-path risk or
  market correctness risk.

Implementation slice 1:

- Added `build_offer_read_options(include_owner_identity=...)` to make eager
  relationship loading explicit per offer read route.
- `GET /api/offers` no longer eager-loads `Offer.user`, because active market
  list responses intentionally do not include owner identity.
- `GET /api/offers/my` still eager-loads `Offer.user`, because that route keeps
  owner identity in the response.
- Per-offer expiry trace logs were downgraded from `info` to `debug` to avoid
  high-volume read-path log noise without changing the response schema.

Validation for slice 1:

- `python3 -m unittest tests.test_chat_router_direct_reads tests.test_chat_room_service_room_read_models tests.test_chat_service_projection_and_send_helpers tests.test_offers_router_reads`
  passed.
- `python3 -m py_compile api/routers/chat.py core/services/chat_service.py core/services/chat_room_service.py api/routers/offers.py tests/test_offers_router_reads.py`
  passed.

Implementation slice 2:

- Empty `GET /api/offers` and `GET /api/offers/my` result sets now return
  before loading trading settings or customer read context.
- `load_offer_customer_read_context()` still reuses the viewer relation from
  the already-loaded owner relation map when possible, but its fallback viewer
  lookup now reads `CustomerRelation` directly instead of using the heavier
  helper that joined `User`.
- Offer status filter mapping is centralized in `OFFER_STATUS_FILTERS` to keep
  the `offers_my` branch consistent and avoid duplicate mapping construction.

Validation for slice 2:

- `python3 -m unittest tests.test_offers_router_reads tests.test_customer_relation_service`
  passed.
- `python3 -m py_compile api/routers/offers.py core/services/customer_relation_service.py tests/test_offers_router_reads.py tests/test_customer_relation_service.py`
  passed.

Slice 2 deployment:

- Commit `e769c90` was deployed to foreign and Iran with
  `make production-release`.
- Release log: `tmp/e2e-logs/rpo3-rpo4-release.log`.
- Foreign and Iran health checks passed.
- Iran deployment used the incremental path:
  - frontend build skipped;
  - wheel cache rebuild skipped;
  - Docker image build/save/upload/load skipped where checksums matched;
  - only changed payload files were synced.

RPO3 benchmark:

- Artifact: `tmp/production-benchmark/20260614T060230Z/load-pool-matrix`.
- Shape: `500 RPS / 2m`, `workers=24`, `DB_POOL_SIZE=10`,
  `DB_MAX_OVERFLOW=4`, `LOAD_RUNNER_SHARDS=2`, no media, no mutations.
- Overall:
  - effective RPS: `493.39`;
  - p95/p99: `589.67ms` / `1129.99ms`;
  - dropped-iteration ratio: `1.025%`;
  - request failure rate: `0%`;
  - Nginx 5xx delta from first sampler sample: `0`;
  - max PostgreSQL connections: `252`;
  - sampler status: passed.
- Iran was restored to `API_WORKERS=8`, `DB_POOL_SIZE=8`,
  `DB_MAX_OVERFLOW=6`.
- Endpoint comparison versus RPL6:
  - `offers_list` p95/p99: `704.54ms` / `1693.59ms` ->
    `614.90ms` / `1494.70ms`;
  - `offers_my` p95/p99: `693.54ms` / `1755.50ms` ->
    `617.81ms` / `1471.62ms`.

RPO3 final decision:

- RPO3 is closed with the accepted low-risk read-path reductions.
- The short run improved offers endpoints versus RPL6 and stayed clean on
  request failures and Nginx 5xx, but it did not beat the accepted L7 offers
  endpoints or the best RPO1 short-run variance.
- Do not expand offers reads into broader active-offer caching or market-state
  behavior before release; the correctness risk is not justified by this
  evidence.

## Stage RPO4 - Relations and Public User Reads

Goal:

- Reduce p95/p99 for `customer_relations`, `users_public_detail`,
  `users_public_search`, and related project-user directory reads.
- Preserve all messaging and profile visibility rules:
  - users can see project users;
  - accountants are reachable according to the current plan;
  - non-group level-1/level-2 customers stay hidden from unauthorized users.

Likely investigation points:

- Pending invitation expiry sweep running in read paths.
- Repeated capability/profile role computation.
- Public profile detail loading full relation state where summary is enough.
- Search/directory queries doing broad scans once production row counts grow.

Allowed changes:

- Move write-like pending-expiry cleanup away from hot reads if safely possible.
- Add short cache for capability/role metadata where invalidation is simple.
- Narrow public profile and search projections.
- Add evidence-based indexes if production plans justify them.

Disallowed changes:

- No privacy regression.
- No unauthorized customer/accountant visibility.
- No stale relationship status in management actions.

Validation:

- Relation visibility tests.
- Public profile/search tests.
- Query-plan report for relation and public-user cases.
- Short targeted benchmark focused on relation/profile reads.

Exit criteria:

- Relation/profile p95/p99 improves and visibility tests stay stable.

Implementation slice 1:

- Public search now defers `get_active_accountant_relation_for_accountant()`
  for the viewer until the returned rows actually include a customer profile
  that needs accountant-based access validation.
- Project-user directory reads skip the viewer accountant lookup for the
  current user's own profile, because self access is already definitive.
- Public profile detail reads defer the viewer accountant lookup until the
  target is confirmed to be a customer profile and the viewer is not already
  the customer, the owner, or a super admin.
- Direct test calls to the project-user route now normalize FastAPI `Query`
  defaults for `limit`/`offset`, matching runtime behavior without changing the
  HTTP contract.

Validation for slice 1:

- `python3 -m unittest tests.test_users_public_router_read tests.test_users_public_router_search tests.test_users_public_project_users`
  passed.
- `python3 -m py_compile api/routers/users_public.py tests/test_users_public_router_read.py tests/test_users_public_router_search.py tests/test_users_public_project_users.py`
  passed.

RPO4 benchmark and query-plan evidence:

- The RPO3 benchmark also exercised RPO4 slice 1:
  - `customer_relations` p95/p99 improved versus RPL6 from
    `699.44ms` / `1693.16ms` to `599.66ms` / `1364.95ms`;
  - `users_public_detail` p95/p99 improved versus RPL6 from
    `678.09ms` / `1447.82ms` to `554.40ms` / `1229.91ms`;
  - `users_public_search` p95/p99 improved versus RPL6 from
    `665.38ms` / `1763.55ms` to `607.07ms` / `1138.29ms`.
- Iran query-plan evidence was collected inside the production app container on
  2026-06-14:
  - `users_public_search_loadtest`: execution `0.487ms`, shared reads `0`;
  - `project_users_directory`: execution `0.601ms`, shared reads `0`;
  - `customer_pending_sweep_probe`: execution `0.085ms`, shared reads `0`;
  - `customer_relations_list`: execution `0.297ms`, shared reads `0`;
  - `accountant_pending_sweep_probe`: execution `0.089ms`, shared reads `0`;
  - `accountant_relations_list`: execution `0.285ms`, shared reads `0`.

RPO4 final decision:

- RPO4 is closed with slice 1 only.
- No production index change is justified by the current query-plan evidence:
  relation/profile reads are sub-millisecond on Iran data and have zero shared
  read blocks.
- Do not expand public-profile/search visibility rewrites before release. That
  code is privacy-sensitive, and the measured gain does not justify broader
  access-rule changes.

## Stage RPO5 - Notification and Counter Reads

Goal:

- Review `notifications_unread` and any remaining high-frequency counter reads
  for narrow improvements.
- Preserve independent read/delete semantics between owners and accountants.

Likely investigation points:

- Repeated unread-count queries on every route transition/poll.
- Lack of short per-user TTL for read-only counters.
- Counter reads mixed with heavier notification list projections.

Allowed changes:

- Short per-user TTL cache for unread counters only if invalidated on:
  - notification create;
  - read/unread;
  - delete;
  - sync receive for notification tables.
- Split unread-counter query from notification-list query if currently coupled.

Disallowed changes:

- No shared read/delete state between owner and accountant.
- No stale unread count after explicit user action.
- No cache without cross-server invalidation.

Validation:

- Notification ownership/read/delete tests.
- Sync invalidation tests if a cache is added.
- Short targeted benchmark or endpoint micro-benchmark.

Exit criteria:

- `notifications_unread` improves or is explicitly deferred with evidence.

Implementation slice 1:

- `GET /api/notifications/unread` and `GET /api/notifications/` now honor
  optional `limit` and `offset` query parameters.
- Omitting `limit` preserves the existing frontend behavior of returning the
  full notification history.
- The Stage L/RPO load harness already calls these endpoints with `limit=30`,
  so benchmark reads now avoid fetching an unbounded notification history.
- `GET /api/notifications/unread-count` remains Redis-only; no DB fallback or
  cross-user cache was added.
- Broad bulk update/delete was intentionally not used for notification
  mutations, because notification ORM events feed the cross-server sync change
  log. A bulk SQL rewrite would risk bypassing those events.

Correctness budget for slice 1:

- Owner/accountant read/delete independence is unchanged because every
  notification route still filters by `Notification.user_id == current_user.id`.
- Explicit read/delete actions still call `sync_unread_count()` after mutation.
- Sync receive for notification tables still refreshes per-user Redis unread
  counters.

Validation for slice 1:

- `python3 -m unittest tests.test_notifications_router_reads tests.test_notifications_router_mutations tests.test_sync_router_unread_refresh`
  passed.
- `python3 -m py_compile api/routers/notifications.py tests/test_notifications_router_reads.py`
  passed.

RPO5 final decision:

- RPO5 is closed as a bounded read-list improvement plus a counter-semantics
  review.
- `notifications_unread_count` latency is expected to remain mostly proxy,
  auth, and Redis round-trip cost because the endpoint already avoids DB work.

## Stage RPO6 - Combined Short Benchmark and L9 Decision

Goal:

- Decide whether the accepted RPO changes justify a full L9 rerun.

Tasks:

- Run the same short diagnostic shape used by RPL6:
  - `500 RPS / 2m`;
  - `workers=24`;
  - `DB_POOL_SIZE=10`;
  - `DB_MAX_OVERFLOW=4`;
  - `LOAD_RUNNER_SHARDS=2`;
  - no media/mutations unless prior stages require otherwise.
- Compare against accepted L7 and RPL6.
- Restore production profile after the run.
- Check foreign and Iran sync health.

L9 rerun is allowed only if:

- p95 is materially better than RPL6 and at least competitive with L7;
- p99 is materially better than RPL6 and near L7;
- dropped-iteration ratio is below or near L7;
- request failure rate remains `0%`;
- Nginx 5xx delta remains `0`;
- PostgreSQL connection pressure stays in the accepted range.

Exit criteria:

- If gates pass: schedule one full L9 `500 RPS / 30m` rerun.
- If gates do not pass: document remaining debt and do not spend another long
  benchmark cycle.

## Current Recommendation

Start with `RPO0`, then `RPO1`.

Reason:

- The slowest remaining endpoint family is `chat_conversations`.
- It is likely broad read-shape cost rather than one missing index.
- It is high-risk enough that it needs attribution/reporting first, but not so
  broad that it requires a Messenger architecture rewrite.
