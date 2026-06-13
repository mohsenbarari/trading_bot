# Production Read Path Targeted Optimization Roadmap

Status: RPO0 completed, RPO1 in validation, and RPO2 started on 2026-06-13. This roadmap is for
targeted read-path optimization, not for another broad benchmark loop.

Last updated: 2026-06-13

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
| `RPO1` | In Progress | Optimize `chat_conversations` read shape without changing Messenger behavior. |
| `RPO2` | In Progress | Optimize `direct_messages` and room message read pagination/projection. |
| `RPO3` | Pending | Optimize `offers_list` and `offers_my` read paths. |
| `RPO4` | Pending | Optimize relation and public-user reads: `customer_relations`, `users_public_detail`, `users_public_search`, and related directory reads. |
| `RPO5` | Pending | Review `notifications_unread` and remaining poll/read counters for narrow, correctness-safe improvements. |
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

Implementation slice 2:

- Replaced the room-conversation active-member-count correlated scalar subquery
  with one grouped aggregate subquery joined by `chat_id`.
- This preserves member-count semantics while avoiding repeated active-member
  counts for every visible group/channel row.
- Unread, mention, pin, mute, can-send, mandatory-channel, and system-group
  behavior is unchanged.

Validation for slice 2:

- `python3 -m unittest tests.test_chat_room_service_room_read_models tests.test_chat_service_projection_and_send_helpers tests.test_chat_router_direct_reads`
  passed.
- `python3 -m py_compile core/services/chat_room_service.py core/services/chat_service.py api/routers/chat.py`
  passed.

Next RPO1 decision:

- Deploy slice 2 and run one more short targeted benchmark with the same shape.
- Close RPO1 if `chat_conversations` keeps the slice-1 p95 improvement and p99
  does not regress; otherwise document the remaining p99 debt and do not expand
  RPO1 beyond this narrow read-shape work.

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
