# Three-Site WebApp DR, Failover, And Conflict-Free Recovery Roadmap

Status: Active implementation roadmap; tracked on the dedicated feature branch
Prepared on: 2026-07-10
Original preparation branch: `candidate/webapp-ui-ux-unification`
Current implementation branch: `feature/arvan-controlled-origin-failover`
Publication baseline: `main` at `04fef2a5`; local-fencing foundation at `8ff234de`
Production gate: no production mutation, deploy, DNS/CDN change, sync change, or data migration is authorized by this document

## 1. Purpose

This roadmap records the architecture decisions accepted for adding a third physical server in Iran while preserving the project's existing Bot/WebApp authority rules and the existing short-, medium-, and long-outage behavior.

The primary engineering problem addressed here is not merely routing traffic after an Iranian international-connectivity outage. The primary problem is ensuring that independently committed Telegram-side and WebApp-side work converges after connectivity returns without:

- creating two writers for the same business aggregate;
- overwriting newer authoritative state with a stale row snapshot;
- generating identity or sequence collisions;
- losing increments, deletes, files, or terminal business state;
- replaying the same side effect more than once;
- prematurely failing back to the Finland WebApp before data and files are safe;
- changing the already accepted outage rules.

The target is conflict prevention by authority, identity, ordering, and idempotency. Checksums are a final convergence proof, not a conflict-resolution algorithm.

## 2. Publication And Branch Handling

The owner approved publication on 2026-07-14 after the prerequisite branch
merges completed. The roadmap is now stored under `docs/` and implemented on
`feature/arvan-controlled-origin-failover`, which was created directly from the
then-current `main` baseline.

Publication does not relax the production gate:

- roadmap changes and implementation remain on the dedicated feature branch;
- every stage must retain its explicit tests, failure assumptions, and stop
  conditions;
- no migration, WebApp-IR start, public origin switch, or production deploy is
  authorized without a later explicit owner instruction;
- the current live topology must not be described as three-site-ready until all
  blocking gates are closed with evidence.

## 3. Existing Contracts That Remain Authoritative

This roadmap extends, and must not weaken or silently replace, the following project contracts:

- `docs/BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md`
- `docs/CROSS_SERVER_SYNC_PARITY_FOLLOWUP_REMEDIATION_ROADMAP.md`
- `docs/CROSS_SERVER_SYNC_PARITY_IMPLEMENTATION_ROADMAP.md`
- `docs/CROSS_SERVER_SYNC_PARITY_REVIEW_REMEDIATION_ROADMAP.md`
- `docs/CROSS_SERVER_SYNC_PRODUCTION_ENV_AND_TRANSPORT_VALIDATION_ROADMAP.md`
- `docs/TRADE_NOTIFICATION_DELIVERY_GUARANTEE_ROADMAP.md`
- `docs/RUNTIME_SESSION_AND_BOT_ACCOUNT_POLICY.md`
- `docs/SIDE_EFFECT_SURFACE_POLICY.md`

If this roadmap conflicts with an existing accepted business rule, implementation must stop and the conflict must be resolved explicitly. The existence of a newer infrastructure topology does not authorize changing product behavior by assumption.

## 4. Accepted Physical Topology

### 4.1 Physical sites

| Site ID | Location | Current/Planned Address | Permanent responsibility |
|---|---|---:|---|
| `bot_fi` | Hetzner Finland | `65.109.216.187` | Telegram Bot and foreign authority |
| `webapp_fi` | Hetzner Finland | `65.109.220.59` | Normal WebApp primary and recovery sync hub |
| `webapp_ir` | Arvan Tehran, Simin | `185.206.95.250` | Normal standby and national-outage WebApp primary |

The current Iran address is an observed deployment input, not a permanent
provider commitment. The replacement has 4 vCPU, 8 GiB RAM, and 75 GB root
storage. Origin TLS, replacement SLA, and Iran-local redundancy remain
unresolved deployment decisions.

The primary Writer Witness is `185.206.95.94`; `185.231.182.6` remains a
transitional Witness until controlled retirement. Bot-FI, WebApp-FI, WebApp-IR,
and both Witness hosts use UTC with synchronized NTP. User-visible time remains
rendered explicitly in `Asia/Tehran`.

### 4.2 Accepted ingress topology

```text
Users
  |
  v
Arvan CDN / Edge
  |
  +-- normal global connectivity --> WebApp-FI
  |
  +-- Iranian international outage --> WebApp-IR
```

Accepted decisions:

- Arvan is the public ingress and origin router.
- Users always use the public domain behind Arvan.
- A separate Iran reverse proxy must not relay all normal user traffic to Finland.
- Normal traffic goes directly from Arvan to `webapp_fi`.
- Outage traffic goes from Arvan to `webapp_ir` only after Iran-side promotion is complete.
- The two WebApp sites are active/passive for public WebApp traffic.
- Weighted active-active distribution of user traffic across both WebApp sites is forbidden.
- Automatic failback based only on origin reachability is forbidden.

### 4.3 Logical roles are separate from physical sites

The current code uses `iran` and `foreign` as both business-authority identities and deployment identities. The new topology requires these concepts to be separated.

Target concepts:

```text
logical_authority = foreign | webapp
physical_site     = bot_fi | webapp_fi | webapp_ir
runtime_role      = active | standby | relay | recovering | fenced
writer_epoch      = monotonic promotion term
```

Compatibility note:

- Existing `foreign` business authority maps to the permanent Telegram authority on `bot_fi`.
- Existing `iran` business authority maps to the logical WebApp authority, not necessarily to a physical Iranian machine.
- During normal operation the logical WebApp authority executes on `webapp_fi`.
- During an Iranian international outage the same logical WebApp authority executes on `webapp_ir`.

## 5. Two Logical Authorities, Not Three

The system has three physical servers but only two logical business authorities:

```text
Foreign authority
  +-- permanent writer: Telegram-Bot-FI

WebApp authority
  +-- normal writer: WebApp-FI
  +-- outage writer: WebApp-IR
```

This invariant is the main conflict-prevention mechanism.

- `bot_fi` remains active during every Iranian connectivity state.
- `webapp_fi` and `webapp_ir` must never be active writers for the WebApp authority at the same time.
- Losing the public WebApp role does not remove the sync-hub/relay role from `webapp_fi`.
- During an outage, `webapp_fi` may continue receiving foreign-authority events from `bot_fi`.
- During an outage, `webapp_fi` must not create new WebApp-authority mutations or run WebApp-authoritative jobs.
- During an outage, `webapp_ir` creates WebApp-authority events and queues cross-surface effects until recovery.

## 6. Runtime Modes

### 6.1 Normal mode

```text
User --> Arvan --> WebApp-FI

Telegram-Bot-FI <---- product sync ----> WebApp-FI
WebApp-FI ------- WebApp DR -----------> WebApp-IR
```

Required behavior:

- `bot_fi` is active for Telegram.
- `webapp_fi` is active for the public WebApp and holds the current WebApp writer epoch.
- `webapp_ir` is standby/read-only and cannot run WebApp-authoritative jobs.
- Product sync between `bot_fi` and `webapp_fi` behaves according to the current project rules.
- WebApp DR continuously prepares `webapp_ir`, including the WebApp-owned data that product sync intentionally excludes.
- Readiness for `webapp_ir` remains false for public routing while it is standby.

### 6.2 Suspected outage mode

This is a control-plane state, not yet a promotion.

Required behavior:

- Use more than a single HTTP failure to classify an Iranian international outage.
- Combine inside-Iran probes, origin reachability, sync-link state, and a stability window.
- Do not promote merely because one external host or one Arvan probe is unhealthy.
- Freeze or fail closed for WebApp-authoritative financial writes if writer ownership is ambiguous.
- Preserve reads and explicitly safe local behavior according to existing policy.

### 6.3 Iran-active outage mode

```text
Iranian WebApp users --> Arvan --> WebApp-IR

Telegram users --> Telegram-Bot-FI <---- sync ----> WebApp-FI

WebApp-FI -X- WebApp-IR
```

Required behavior:

- `bot_fi` continues Telegram operation without failover.
- `webapp_ir` holds the new WebApp writer epoch and serves users.
- `webapp_fi` loses only the public WebApp writer role.
- `webapp_fi` remains a foreign-event projection and sync hub.
- Foreign-authority changes may continue from `bot_fi` to `webapp_fi`.
- WebApp-authority changes remain durable on `webapp_ir` until the link returns.
- No server may mutate a remote-home offer locally.

### 6.4 Recovery mode

```text
Users --> Arvan --> WebApp-IR (still active)

WebApp-IR <---- recovery sync ----> WebApp-FI <---- product sync ----> Telegram-Bot-FI
```

Required behavior:

- User traffic remains pinned to `webapp_ir` throughout recovery.
- `webapp_fi` remains public-not-ready and WebApp-writer-fenced.
- Foreign-origin events accumulated on `webapp_fi` are delivered to `webapp_ir`.
- WebApp-origin events accumulated on `webapp_ir` are delivered to `webapp_fi`.
- `webapp_fi` relays the original WebApp-origin events to `bot_fi` without changing event identity or origin.
- Existing short/medium/long outage finalization rules run before public failback.
- Recovery continues until delivery, event ordering, business invariants, database parity, file parity, and stability gates pass.

### 6.5 Controlled failback

Required handoff order:

```text
WebApp-IR active
  -> enter final handoff barrier
  -> briefly gate sensitive writes
  -> deliver final deltas
  -> verify final checkpoints and parity
  -> fence WebApp-IR writer
  -> issue a new writer epoch to WebApp-FI
  -> mark WebApp-FI origin-ready
  -> switch Arvan public traffic
  -> keep WebApp-IR as standby
```

Reads should remain available during the handoff. Sensitive writes may be briefly pending or rejected rather than risk simultaneous writers.

## 7. Existing Outage Classification Remains Unchanged

The existing project policy is authoritative:

- Short outage: up to 2 minutes.
- Medium outage: more than 2 minutes and up to 1 hour.
- Long outage: more than 1 hour.

This roadmap does not redefine those classes.

### 7.1 Short outage

- Normal pending replay is allowed.
- Replay and peer publication use the latest authoritative aggregate state.
- An old create event must not republish an offer that is already completed, cancelled, or expired.
- A partially traded offer may publish only its latest authoritative remaining quantity and state.

### 7.2 Medium outage

- Cross-surface active publication remains gated until catch-up is proven.
- Active local-only offers must not become newly active on the peer after recovery.
- The offer home server expires those still-active local-only offers during recovery finalization.
- Only final/expired state is synced across the surface boundary.

### 7.3 Long outage

- The medium-outage safety rules remain active.
- Stronger operator review, observability, and reconciliation are required before clearing the recovery gate.
- No failback is allowed while unresolved conflicts, failed deliveries, or ambiguous business state remain.

## 8. Offer And Trade Rules During Partition

### 8.1 Foreign-home path

- Offers created in Telegram remain `offer_home_server=foreign`.
- They may be created, requested, traded, cancelled, and expired on the Telegram/foreign authority.
- They may continue syncing to `webapp_fi` during the Iranian outage.
- They are not available for authoritative mutation on `webapp_ir` until recovery.

### 8.2 WebApp-home path

- Offers created on `webapp_ir` remain `offer_home_server=iran` under the current compatibility model.
- They may be created, requested, traded, cancelled, and expired on the active WebApp authority.
- Telegram publication and foreign delivery remain pending until connectivity returns.
- The durable event/outbox record is committed in the same transaction as the authoritative business mutation.

### 8.3 Pre-outage cross-surface rows

- A foreign-home offer already visible on the Iran WebApp remains readable but cannot be mutated there while authority is unreachable.
- An Iran-home offer already visible in Telegram remains readable/displayable according to existing rules but cannot be authoritatively mutated by the foreign server.
- Remote-home commands remain rejected by default; any future queued command must remain explicitly non-final until accepted by the home authority.

## 9. Public And Sync Domains Must Be Different

### 9.1 Public domain

The public domain is a logical active-WebApp address behind Arvan.

The deployment namespaces are fixed and deliberately different:

```text
production root:       gold-trade.ir
current production:    coin.gold-trade.ir
failover-test root:    gold-trading.ir
current CDN test app:  app.gold-trading.ir
```

`gold-trading.ir` is not an alternate spelling or alias for production. It is
the isolated provider-validation zone. As of the read-only Arvan check on
2026-07-19, only this test root is enrolled in the current CDN account;
`gold-trade.ir` is not. Production onboarding and routing are a separate
post-Full-Matrix change gate. Evidence from the test root cannot authorize a
production-root mutation.

Required properties:

- behind Arvan;
- serves public WebApp, public API, WebSocket, and approved media routes;
- origin changes according to the controlled active-site state;
- routes to `webapp_fi` normally and `webapp_ir` during an accepted outage;
- rejects `/api/sync/*` and internal control-plane endpoints;
- caches only approved static assets;
- never caches dynamic API, WebSocket, authentication, or sync traffic.

### 9.2 Fixed physical sync domains

Each physical sync receiver requires a stable name that never follows public failover.

Examples only:

```text
sync-bot-fi.example.net       -> bot_fi
sync-webapp-fi.example.net    -> webapp_fi
sync-webapp-ir.example.ir     -> webapp_ir
```

The final names are deployment decisions. The required invariant is:

```text
public domain = logical active WebApp
sync domain   = one fixed physical receiver
```

Required properties:

- no public WebApp, chat, static, or general API routes;
- only allowlisted sync, repair, readiness, and health paths;
- TLS verification always enabled;
- HMAC plus timestamp, nonce/event dedupe, source, destination, and protocol audience checks;
- pairwise credentials instead of one global three-server secret;
- cache disabled;
- no automatic load balancing or failover between physical sync receivers;
- receiver idempotency remains mandatory even when the network or proxy retries a POST.

### 9.3 Pairwise trust relationships

Base topology:

```text
bot_fi     <-> webapp_fi
webapp_fi  <-> webapp_ir
```

No direct `webapp_ir -> bot_fi` dependency is required in the base architecture. Iran-origin events reach `bot_fi` through `webapp_fi` after recovery. This keeps the Telegram and Iran physical boundaries simple.

## 10. Separate Product Sync From WebApp DR

The current product sync policy and WebApp disaster-recovery replication are different data planes and must not share one ambiguous registry.

### 10.1 Product sync

Participants:

```text
bot_fi <-> logical WebApp authority
```

Purpose:

- shared non-messenger product state;
- offers, trades, requests, users, relations, settings, notifications, and delivery ledgers according to the existing sync registry;
- preservation of `offer_home_server` and table/field authority;
- no Messenger database or upload replication to the Telegram server.

### 10.2 WebApp DR replication

Participants:

```text
webapp_fi <-> webapp_ir
```

Purpose:

- prepare either WebApp site to serve the complete WebApp experience;
- replicate canonical WebApp product data;
- replicate WebApp-owned Messenger data;
- replicate upload/file metadata and file content/manifests;
- provide explicit session-continuity behavior;
- exclude or rebuild volatile caches, local leases, process state, and other runtime-only data.

Important exception:

- Tables currently marked `NO_SYNC` because they must not reach the Telegram/foreign product surface may still require WebApp-to-WebApp DR replication.
- Therefore `NO_SYNC` in product sync must not automatically mean `NO_DR_REPLICATION`.

The target policy model should explicitly declare both dimensions, for example:

```text
product_sync_policy = sync | no-sync | bookkeeping
webapp_dr_policy    = replicate | rebuild | local-only | exclude
```

### 10.3 DPI-aware bulk transport and Object Storage

The accepted bulk data plane uses the private, versioned Arvan bucket
`production-sync-coin` in region `ir-thr-at1`:

- large release, snapshot, file, and later immutable event-batch objects move
  through Object Storage instead of a persistent Finland-Iran SSH stream;
- WebApp-IR downloads with short-lived presigned HTTPS URLs and never receives
  the bucket HMAC credential;
- every payload is encrypted before upload with an independently custodied age
  X25519 key and verified by ciphertext and content manifests;
- SSH remains a low-volume management/control path, not the bulk data plane;
- Arvan CDN remains public ingress/control infrastructure and is not a second
  durable queue or backup store;
- direct sync may remain only as a bounded fallback after measured DPI-safe
  limits and an explicit activation decision.

Live compatibility testing found that Arvan accepts Public Access Block and
default bucket SSE control calls but rejects actual `PutObject` requests while
either setting is active. Those controls are absent; the bucket ACL remains
private, anonymous reads return `403`, versioning is enabled, and client-side
encryption is mandatory. Revalidate this provider behavior before any future
attempt to enable those controls.

## 11. Conflict Prevention Principles

The implementation must satisfy all of the following:

1. One authoritative writer per aggregate.
2. One active WebApp writer epoch at a time.
3. Stable cross-server identity independent of local integer primary keys.
4. Immutable origin event identity.
5. Monotonic aggregate versions.
6. Per-producer/epoch sequence ordering and gap detection.
7. Idempotent receiver application.
8. Monotonic terminal-state protection.
9. Tombstone-aware deletes.
10. Per-destination delivery acknowledgment.
11. No generic last-write-wins by clock time.
12. Conflict quarantine instead of automatic destructive resolution.
13. Canonical parity excluding intentional local-runtime differences.
14. Relay without changing origin identity or creating echo events.

## 12. Target Replication Event Model

The exact table names are provisional. The required semantics are not.

### 12.1 Immutable event journal

Proposed logical shape:

```text
replication_events
  event_id UUID PRIMARY KEY
  authority_role foreign | webapp
  producer_site bot_fi | webapp_fi | webapp_ir
  writer_epoch BIGINT
  source_sequence BIGINT
  aggregate_type TEXT
  aggregate_id TEXT
  aggregate_scope TEXT
  aggregate_version BIGINT
  operation insert | update | tombstone | snapshot
  payload JSONB
  payload_hash TEXT
  command_id TEXT/UUID NULL
  transaction_id UUID NULL
  transaction_index INTEGER NULL
  transaction_size INTEGER NULL
  schema_version INTEGER
  registry_version INTEGER
  outage_partition_id UUID NULL
  created_at TIMESTAMP
```

Required uniqueness:

```text
UNIQUE(event_id)
UNIQUE(producer_site, writer_epoch, source_sequence)
UNIQUE(aggregate_type, aggregate_id, aggregate_scope, aggregate_version)
```

Notes:

- `created_at` is audit data and is not the conflict winner.
- `event_id` remains identical through every relay.
- `authority_role` identifies business ownership.
- `producer_site` identifies the physical process that produced the event.
- `writer_epoch` identifies the promotion term that authorized production.
- `aggregate_scope` is `root` for a truly single-writer aggregate and an authority-owned subaggregate or field family for a multi-authority entity.
- `aggregate_version` protects ordering within one canonical aggregate scope across site changes.
- A shared entity such as User must not use one global aggregate-version stream when independent authorities may update different field families during a partition.
- Multi-row mutations that form one business command must carry a transaction envelope; the receiver must either apply that envelope atomically or use an explicitly proven invariant-safe dependency protocol.

### 12.2 Per-destination delivery ledger

```text
replication_deliveries
  event_id UUID
  destination_site TEXT
  status pending | leased | retry | acked | terminal_rejected
  attempt_count INTEGER
  next_attempt_at TIMESTAMP NULL
  last_attempt_at TIMESTAMP NULL
  acked_at TIMESTAMP NULL
  last_error_code TEXT NULL
  last_error_hash TEXT NULL
  lease_owner TEXT NULL
  lease_until TIMESTAMP NULL
  UNIQUE(event_id, destination_site)
```

This replaces the assumption that one `change_log.synced=true` proves delivery everywhere.

### 12.3 Receiver receipt

```text
replication_receipts
  destination_site TEXT
  event_id UUID
  applied_status applied | duplicate | ignored_safe | quarantined
  aggregate_type TEXT
  aggregate_id TEXT
  aggregate_scope TEXT
  aggregate_version BIGINT
  payload_hash TEXT
  applied_at TIMESTAMP
  UNIQUE(destination_site, event_id)
```

The receipt, materialized row update, and checkpoint update must commit in one local database transaction.

### 12.4 Stream checkpoints

```text
replication_checkpoints
  destination_site TEXT
  producer_site TEXT
  writer_epoch BIGINT
  last_contiguous_sequence BIGINT
  last_event_hash TEXT
  updated_at TIMESTAMP
  UNIQUE(destination_site, producer_site, writer_epoch)
```

Checkpoints must represent the last contiguous sequence. Receiving event 105 must not advance a checkpoint from 100 to 105 while 101-104 are missing.

`source_sequence` must be an ordinal of committed events in its producer/epoch stream, not the raw local change-log primary key. It must be allocated transactionally so rollback does not create an unresolvable committed-stream gap. A sender must not use table-priority scheduling to send a later sequence ahead of an earlier sequence in the same stream unless the receiver persists and later closes the explicit gap.

### 12.5 Conflict quarantine

```text
replication_conflicts
  conflict_id UUID
  destination_site TEXT
  event_id UUID
  aggregate_type TEXT
  aggregate_id TEXT
  aggregate_scope TEXT
  expected_version BIGINT NULL
  incoming_version BIGINT NULL
  current_hash TEXT NULL
  incoming_hash TEXT NULL
  reason TEXT
  status open | resolved | accepted_exception
  detected_at TIMESTAMP
  resolution_note TEXT NULL
```

Any open critical conflict blocks parity-ready and failback-ready states.

### 12.6 Approved Load, Capacity, And Latency Contract

The owner approved the following design contract on 2026-07-14. These are architecture targets and acceptance gates, not claims about the current implementation or the future Iran link before benchmark evidence exists.

Request rate is not replication-event rate. Capacity planning must measure:

```text
R_total     = total application requests per second
R_bot       = Bot-FI requests per second
R_webapp    = WebApp requests per second
w_bot       = fraction of Bot requests that create shared mutations
w_webapp    = fraction of WebApp requests that create shared mutations
a_bot       = average replication events per mutating Bot command
a_webapp    = average replication events per mutating WebApp command

lambda_bot    = R_bot * w_bot * a_bot
lambda_webapp = R_webapp * w_webapp * a_webapp
lambda_dr     = lambda_bot + lambda_webapp + required WebApp-only DR events
```

The accepted planning scenario is:

```text
R_total  = 300 requests/second
R_bot    = 150 requests/second
R_webapp = 150 requests/second
```

Worst-case one-shared-event-per-request planning therefore starts at:

| Link | Planning arrival rate |
|---|---:|
| `bot_fi -> webapp_fi` | 150 events/second |
| `webapp_fi -> bot_fi` | 150 events/second |
| `webapp_fi -> webapp_ir` in normal mode | 300 events/second plus required WebApp-only DR data |

Actual sizing must use measured write fraction, transaction-envelope amplification, payload bytes, file traffic, and bursts. If one command produces multiple replicated events, the capacity requirement scales by that amplification; request RPS alone is not an acceptable sizing input.

The target sender must replace one-item serial HTTP delivery with destination-aware micro-batching and bounded concurrency while preserving per-aggregate and contiguous-stream correctness. Initial tuning values for staging are:

- 50 to 100 events per batch;
- 5 to 10 milliseconds maximum batch wait;
- 4 to 8 concurrent in-flight batches per destination for independent work;
- adaptive backpressure and bounded retry concurrency;
- no parallel execution that violates aggregate order, transaction envelopes, or contiguous checkpoints.

Sustained accepted-event capacity for every directed link must be at least five times that link's measured peak event-arrival rate after amplification. For the one-event-per-request planning case, `webapp_fi -> webapp_ir` therefore requires at least 1,500 accepted events/second before production consideration. Higher measured amplification raises this gate proportionally.

Initial latency SLOs under the 300-request/second scenario are:

| Directed path | Normal target | p95 target | p99 target |
|---|---:|---:|---:|
| `bot_fi <-> webapp_fi` | 20-80 ms | < 150 ms | < 300 ms |
| `webapp_fi -> webapp_ir` while the international link is healthy | 100-500 ms | < 750 ms | < 2 s |

The Finland-to-Iran targets are provisional until the selected Iran host completes a sustained 24-hour production-shaped benchmark with latency, loss, asymmetric ACK failure, DPI-safe bandwidth, database pressure, and file traffic. Missing those targets does not permit changing the numbers silently; it requires capacity work or an explicit owner-approved SLO/RPO decision.

Time-scoped baseline evidence collected on 2026-07-14:

- direct `bot_fi` to `webapp_fi` ICMP RTT: 0.790 ms average, 0% loss across 10 samples;
- current low-load one-item delivery through the currently configured public sync route, 146 samples: 70.18 ms average, 57.74 ms p50, 157.33 ms p95, 220.56 ms p99, and 239.72 ms maximum; this is not evidence for the target fixed physical sync domain;
- the current worker sends a batch of one and processes serially, so the observed average implies only about 14 deliveries/second per worker and is not acceptable evidence for the 300-request/second target.

At 150 one-event mutations/second in one direction, a 14-event/second worker would grow backlog by about 136 events/second. Latency would therefore grow without bound while the load continues. The new worker and benchmark gates are mandatory before adding public three-site failover.

### 12.7 Approved Commit, Delivery, And Cut-Boundary Semantics

Cross-site ACK is not on the user-success path. A mutating user request may report success only after the authoritative business mutation and its immutable replication journal/transaction envelope commit atomically on the active site. Delivery to other sites continues asynchronously and remains independently auditable per destination.

Every event at a connectivity cut belongs to exactly one of these states:

| Cut-boundary state | Required behavior |
|---|---|
| Receiver applied and source recorded ACK | Delivery is complete for that destination. |
| Receiver applied but ACK was lost | Source retries the same `event_id`; receiver returns idempotent duplicate success without repeating business or side effects. |
| Receiver did not apply | Durable source delivery remains pending/retry and is replayed after connectivity returns. |
| Local business transaction did not commit | No authoritative event exists; client retry uses the same `command_id` and cannot create a duplicate command. |

During an Iranian international outage, `bot_fi` and `webapp_fi` continue their Finland product sync. The `webapp_fi <-> webapp_ir` deliveries accumulate durably on their producing sides. The system must not claim the three physical sites are current while that link is unavailable. It instead guarantees local durability, stable event identity, authority-safe independent operation, and later evidence-backed convergence, subject to the approved per-data-class RPO.

The no-cross-site-ACK decision accepts that a permanently destroyed active site can lose the last locally acknowledged but not yet replicated mutations. Eliminating that residual-loss window requires same-region independent durability, such as a database replica or continuous WAL target, or reintroducing a remote durability ACK for the affected data class. The exact RPO and same-region durability requirement remain a P0 decision by data class.

## 13. Extending Current Metadata

The current `source_server + source_sequence` watermark is a useful two-server base but cannot identify two physical WebApp producers safely.

Target metadata must separate:

```text
authority_server / authority_role
producer_site
writer_epoch
source_sequence
event_id
aggregate_id
aggregate_scope
aggregate_version
destination_site
```

Do not solve the problem by replacing `source_server=iran` with independent `webapp_fi` and `webapp_ir` business authorities. That would let stale events from one physical WebApp site compete with newer events from the other. Physical-source ordering and business-authority ordering are separate concerns.

## 14. Receiver Apply Algorithm

For every incoming event, the receiver must:

1. Authenticate the physical sender and destination audience.
2. Validate schema and registry compatibility.
3. Validate that the event authority is allowed to mutate the declared fields/aggregate.
4. Validate the producer's writer epoch when the event is WebApp-authoritative.
5. Check `event_id` for idempotent replay.
6. Acquire a transaction-scoped advisory or row lock for the canonical aggregate key and scope.
7. Validate and assemble any multi-row transaction envelope before exposing partial state.
8. Compare aggregate-scope version and terminal-state rules.
9. Apply the materialized state only if safe.
10. Persist receipt and checkpoint in the same transaction.
11. Emit local side effects only through destination-owned, idempotent side-effect outboxes.

Decision table:

| Condition | Required action |
|---|---|
| Same `event_id` and same hash already applied | Idempotent success/no-op |
| Incoming aggregate version is exactly next | Apply |
| Incoming version is older | Ignore as stale and record evidence |
| Incoming version is ahead with a gap | Defer; request missing range/state |
| Same version with different hash | Quarantine; block recovery/failback |
| Wrong business authority | Reject visibly |
| Unsupported schema/registry version | Reject visibly; sender must not mark ACK |
| Terminal state would reactivate | Ignore/reject according to current business guard |
| Completed trade would be destructively changed | Preserve completed trade and quarantine/log |
| Foreign-key dependency missing | Defer without advancing contiguous checkpoint |

No receiver path may return generic success for an unknown, skipped, or policy-forbidden event.

## 15. Relay Semantics And Echo Prevention

Example recovery path:

```text
WebApp-IR -> WebApp-FI -> Telegram-Bot-FI
```

`webapp_fi` is a relay, not a new origin.

The following fields remain unchanged:

```text
event_id
authority_role
producer_site
writer_epoch
source_sequence
aggregate_id
aggregate_scope
aggregate_version
payload_hash
```

`webapp_fi` adds only a new delivery row for `destination_site=bot_fi`.

Applying a replicated event must not create a new authoritative business event for the same mutation. Destination-specific realtime, cache invalidation, Telegram publication, Web Push, or audit effects must use separate idempotent side-effect ledgers.

## 16. Identity Strategy

Local integer IDs remain local database implementation details. Cross-server identity must use stable canonical identities.

| Data family | Canonical identity direction |
|---|---|
| Offers | Existing `offer_public_id` |
| Trades | Existing partition-safe `trade_number` initially; future UUID optional |
| Offer requests | `request_home_server + idempotency_key`, with `offer_public_id` reference |
| Publication state | Existing deterministic `dedupe_key` |
| Delivery receipts | Existing deterministic `dedupe_key` |
| Notifications | Deterministic producer `dedupe_key` |
| Users | Add stable `user_public_id`; do not depend only on local integer ID or Telegram ID |
| User relations/blocks | Canonical user public IDs plus relation natural identity |
| Commodities/aliases/settings | Existing accepted natural keys plus authority rules |
| Messenger messages/chats | Stable UUID identities suitable for WebApp DR |
| Files | Stable file UUID plus content checksum/blob key |

During migration, server-partitioned integer sequences remain a collision-reduction guard until all sync references use canonical identity.

Avoiding equal integer IDs is necessary during migration but is not conflict prevention. Two sites can create different IDs for the same human, command, invitation, request, balance effect, or business aggregate; they can also apply contradictory terminal transitions to the same canonical aggregate while every local primary key remains unique. Therefore:

- local integer primary keys may remain database implementation details;
- every replicated entity and cross-site reference must use a stable canonical identity;
- every user command requires a stable idempotency `command_id`;
- every replication event requires a globally unique immutable `event_id`;
- natural-identity uniqueness, writer authority, writer epoch, aggregate-scope version, terminal-state rules, and transaction envelopes remain mandatory;
- prefix, range, odd/even, or site-partitioned integer allocation is only a temporary collision guard and must never be treated as the conflict-resolution algorithm.

## 17. Writer Epoch And Fencing

Promotion must issue a new writer epoch before the new WebApp site can produce authoritative events.

Required properties:

- epochs are monotonic;
- only one site may hold the active WebApp epoch;
- every WebApp-authoritative mutation validates the local active epoch;
- background jobs validate the same epoch/role before mutating shared state;
- a demoted or expired-lease site fails closed for WebApp-authoritative writes;
- internal foreign-event projection may continue on the demoted `webapp_fi` without granting it WebApp authority;
- origin readiness is false unless the site holds the valid active epoch.

The exact witness/lease implementation is an implementation-stage decision and must be validated under asymmetric network failure. A two-node heartbeat alone is not sufficient proof against every split-brain scenario.

## 18. Deletes And Tombstones

Hard delete is unsafe for replicated product and DR data because an older update can recreate a deleted row.

Replicated delete semantics must include:

```text
deleted_at
deleted_by / source
delete_reason
aggregate_version
tombstone event
```

Rules:

- a tombstone participates in normal aggregate ordering;
- an older update cannot resurrect a tombstoned aggregate;
- physical deletion waits for retention expiry and acknowledgment from every required destination;
- completed trades and protected audit ledgers are not physically deleted through ordinary sync;
- file deletion uses a tombstone plus delayed blob garbage collection after all manifests acknowledge it.

## 19. Counters, Limits, And Derived State

Generic `MAX` merge is not correct for counters independently incremented on both authorities.

Example:

```text
initial trades_count = 10
foreign increments   = 11
webapp increments    = 11
MAX merge result     = 11
correct total        = 12
```

Target direction:

- immutable trades and request ledgers are business truth;
- aggregate counters are projections rebuilt from unique ledger identities where feasible;
- if a counter cannot be rebuilt cheaply, store authority/site components and derive the total;
- unread/session/runtime counters remain local unless WebApp DR explicitly requires them;
- global limit enforcement during a true partition must follow the already accepted product rule; storage merge cannot retroactively guarantee a global limit that both isolated sites were allowed to consume independently.

No new generic counter merge rule may be introduced without table-specific tests.

## 20. User Field-Level Authority

Full-row user snapshots are unsafe because identity, Telegram link, profile, admin restrictions, deletion state, and counters may have different authorities.

Target event semantics should be domain/field scoped, for example:

```text
UserRegistered
UserTelegramLinked
UserProfileUpdated
UserAdminRestrictionsChanged
UserAccountDeactivated
UserDeleted
UserTradeProjectionUpdated
```

Rules:

- each event mutates only authority-approved fields;
- shared User state is divided into explicit streams such as identity/account policy, Telegram linking/onboarding, and authority-specific activity projections;
- each such stream owns its own `aggregate_scope + aggregate_version` sequence;
- one global User aggregate version is forbidden unless all User writes are routed through one available coordinator;
- deletion/deactivation state is monotonic unless an explicit authorized recovery command exists;
- verified mobile/account identity collisions are quarantined rather than merged by timestamp;
- Telegram linking must not overwrite an unrelated WebApp-created user without an explicit identity-resolution rule;
- local session/runtime fields remain outside generic user product sync.

## 21. Trade Number And Aggregate Version Continuity

The current foreign/even and WebApp/odd trade-number allocation remains valid because there are still two logical trade authorities, not three.

Requirements:

- `webapp_ir` must verify sequence alignment before promotion;
- `webapp_fi` must not allocate WebApp-authority trade numbers while fenced;
- imported foreign trade numbers remain foreign-partition values;
- trade identity remains `trade_number` during the compatibility phase;
- completed-trade destructive guards remain mandatory;
- `aggregate_version` remains monotonic across a WebApp site promotion.

`change_log.id` must not be treated as a globally monotonic WebApp authority sequence across physical sites. Use epoch-scoped producer sequences plus aggregate versions and event UUIDs.

## 22. Messenger, Media, And Files

Messenger data remains excluded from Bot/WebApp product sync but must be included in WebApp DR if seamless WebApp failover is required.

Required DR coverage must classify at least:

- messages;
- conversations;
- chats and chat members;
- message state needed for continuity;
- chat file metadata;
- upload batches/sessions where continuity is intended;
- content blobs on disk/object storage;
- thumbnails and file integrity metadata.

File replication requires:

- stable file UUID;
- content hash;
- byte size;
- immutable blob key or version;
- resumable transfer;
- per-destination file delivery state;
- manifest comparison;
- delayed deletion/garbage collection;
- readiness failure when required canonical files are missing or corrupt.

Database row parity without blob parity is not sufficient for WebApp origin readiness.

## 23. Sessions And Runtime State

Current WebApp sessions and login/recovery rows are local runtime state. Generic replication of Redis or session tables is forbidden without an explicit security design.

Before seamless production failover, one of the following must be selected and tested:

- portable signed access plus replicated durable refresh/session authority;
- WebApp-site-specific session continuity with controlled reauthentication;
- another explicit security-reviewed continuity design.

Requirements regardless of the chosen option:

- no duplicate active-session authority caused by blind Redis replication;
- revocation and account deactivation remain enforceable on the promoted site;
- JWT subject identity resolves through canonical user identity;
- session behavior during failover is included in the user-experience acceptance matrix.

This remains an open design decision and blocks a claim of fully transparent WebApp failover.

## 24. Recovery Algorithm

### 24.1 Enter recovery

- Keep `webapp_ir` active for user traffic.
- Keep `webapp_fi` public-not-ready and WebApp-writer-fenced.
- Keep `bot_fi` active.
- Enable or preserve the existing medium/long publication gate when applicable.
- Capture a recovery session/partition identifier.

### 24.2 Exchange stream manifests

Each site reports, per producer and writer epoch:

- first retained sequence;
- last produced sequence;
- last contiguous applied sequence;
- gap ranges;
- event hash chain or range hashes;
- open conflicts;
- pending and failed destination deliveries.

### 24.3 Catch up foreign-origin changes

- `webapp_fi` sends the original foreign-origin events accumulated from `bot_fi` to `webapp_ir`.
- `webapp_ir` applies them under foreign authority rules.
- Missing dependencies are deferred without advancing the contiguous checkpoint.
- Events already present are idempotent no-ops.

### 24.4 Catch up WebApp-origin changes

- `webapp_ir` sends its WebApp-authority events to `webapp_fi`.
- `webapp_fi` applies them as a projection while remaining WebApp-writer-fenced.
- `webapp_fi` creates destination delivery rows to relay the same event IDs to `bot_fi` where product sync policy requires it.
- `bot_fi` applies product state and owns Telegram-local side effects.

### 24.5 Resolve gaps, not by timestamp

- Request exact missing event ranges where retained.
- Use an authorized canonical aggregate snapshot only when the event range is unavailable.
- A snapshot must include authority, aggregate version, base checkpoint, payload hash, and explicit repair provenance.
- Snapshot/repair application requires dry-run and audit evidence for critical data.

### 24.6 Apply existing outage finalization

- Short outage: replay latest authoritative state and permit only safe current publication.
- Medium/long outage: keep publication gated, expire still-active local-only offers on the home authority, and sync final state.
- Do not emit delayed user-facing old-trade notifications prohibited by current delivery policy.

### 24.7 Prove convergence

Recovery is not complete until:

- required event ranges are contiguous;
- required destination deliveries are ACKed;
- retry queues are drained or explicitly accepted as terminal safe rejection;
- critical conflict quarantine is empty;
- canonical database parity passes;
- file manifest/content parity passes;
- schema/registry/application versions are compatible;
- critical business invariants pass;
- origin readiness dependencies are healthy.

Backlog recovery capacity must be calculated while live traffic continues:

```text
catch_up_seconds ~= backlog_events / (accepted_event_capacity - live_event_arrival_rate)
```

Recovery must not be declared capable of convergence or failback if accepted-event capacity is less than or equal to live arrival rate. In the approved worst-case planning example, a one-hour outage at 150 one-event mutations/second on each active side creates approximately 540,000 events per side. A 1,500-event/second recovery path leaves sufficient headroom for an estimated single-stream catch-up on the order of 7-10 minutes after live traffic and safety throttles are included. This is a capacity example, not an RTO promise; measured amplification, dependencies, database writes, payload bytes, files, DPI limits, and relay work determine the real result.

## 25. Canonical Checksum And Parity

Checksums are a final verification layer.

They must not compare raw database bytes or all columns blindly. Intentional local differences include local primary keys, local leases, local worker IDs, provider message IDs, volatile timestamps, caches, and other runtime evidence.

Required parity layers:

1. Schema and migration parity.
2. Registry and protocol compatibility.
3. Per-stream event/checkpoint parity.
4. Per-table canonical row counts.
5. Chunked canonical hashes by stable identity.
6. Critical aggregate detail hashes.
7. File manifest and content hashes.
8. Business invariant checks.
9. Open conflict and failed-delivery checks.

Canonical serialization must be stable, deterministic, sensitive-data-safe, and versioned.

## 26. Health And Readiness Endpoints

Separate concepts are required:

```text
/health/live          process is running
/health/ready         local dependencies are ready
/health/sync          lag, backlog, checkpoints, conflicts, parity summary
/health/origin-ready  eligible for public Arvan origin routing
```

`origin-ready` must be false unless all applicable conditions hold:

- the site is the selected active public WebApp;
- the site holds the valid writer epoch;
- schema and application versions are correct;
- database and required file storage are healthy;
- required recovery/failover checkpoint threshold is satisfied;
- no critical conflict or migration block exists;
- required background-job authority is configured correctly.

`webapp_fi` may remain sync-ready while public-origin-not-ready during outage and recovery.

## 27. Arvan Control Boundary

Arvan routes requests but must not decide business/data readiness by itself.

Required behavior:

- project control plane determines which origin is eligible;
- Arvan consumes origin readiness/health or an explicitly controlled origin configuration;
- restored external reachability must not automatically trigger failback;
- routing must remain pinned to `webapp_ir` until recovery, parity, stability, and handoff pass;
- Arvan health-check scope from Iranian users/PoPs must be validated empirically;
- WebSocket, dynamic API, large file, and partial/asymmetric-connectivity behavior must be tested;
- exact active-passive origin behavior and configuration propagation time must be confirmed before production.

## 28. Security Requirements

- Public origin access should be restricted to approved Arvan/origin paths where operationally possible.
- Sync receivers use separate virtual hosts/listeners and do not expose general user routes.
- TLS verification remains enabled end to end.
- Pairwise sync keys are rotated independently.
- HMAC covers source site, destination site, event ID, timestamp, body hash, and protocol audience.
- Replay defense uses persisted event identity/nonce in addition to a timestamp window.
- Unsupported or wrongly addressed events fail closed.
- Sensitive fields follow the existing sync field policy.
- Logs contain hashes/redacted summaries, not raw sensitive payloads.
- Repair and snapshot endpoints require stronger operator authorization than ordinary event delivery.

## 29. Observability Requirements

Minimum per-site metrics:

- last produced sequence per epoch;
- last contiguous applied sequence per source/epoch;
- event gap count and oldest gap age;
- pending/retry/failed/ACKed deliveries per destination;
- oldest unacknowledged authoritative event;
- current WebApp writer epoch and site;
- origin readiness state and reason;
- rejected wrong-authority writes;
- stale, duplicate, conflict, and quarantined event counts;
- active publication gate state;
- canonical parity status and last successful run;
- file manifest lag and corrupt/missing blob count;
- recovery session state and duration;
- failover/failback transitions and actor/reason;
- application request rate split by read, local-only mutation, product-sync mutation, and WebApp-DR mutation;
- replication-event amplification per committed command and transaction envelope;
- produced, accepted, ACKed, retry, and quarantined events/second per directed link;
- end-to-end commit-to-receipt and commit-to-source-ACK p50/p95/p99 per destination;
- batch size, batch wait, in-flight concurrency, sender/receiver saturation, and backpressure state;
- backlog growth rate, drain rate, and recovery ETA;
- bytes/second and request rate per sync traffic class for DPI budgeting.

Required alerts:

- simultaneous WebApp writer claims;
- writer epoch mismatch;
- same aggregate version with different hash;
- contiguous sequence gap beyond threshold;
- terminal-state regression attempt;
- completed-trade destructive event;
- critical parity drift;
- nonzero open critical conflicts;
- public routing to a non-ready origin;
- required file parity failure;
- recovery gate cleared before all required conditions pass;
- sustained accepted-event capacity falls below five times measured peak arrival after amplification;
- steady-state p95/p99 delivery SLO is breached for the configured evaluation window;
- backlog grows while the link is classified healthy;
- event amplification changes materially without a new capacity review;
- recovery ETA, disk forecast, or DPI-safe bandwidth exceeds the approved outage budget.

## 30. Implementation Stages

### Stage 0 - Post-Merge Refresh And Architecture Contract

Status: completed on 2026-07-14; later architecture decisions still require
their own ADR approval and evidence.

Deliverables:

- wait for owner-confirmed branch merges into `main`;
- refresh this roadmap against current `main`;
- verify current code and roadmap implementation status;
- move the refreshed document to `docs/`;
- link it from the active sync parity and Bot/WebApp roadmaps;
- select a dedicated implementation branch;
- record unresolved product decisions and provider confirmations.

Exit criteria:

- no branch-policy conflict;
- owner approves the final roadmap scope;
- no code or production action has started prematurely.

### Stage 1 - Baseline, Inventory, And Risk Report

Deliverables:

- inventory all synced, no-sync, bookkeeping, Messenger, file, session, and runtime data;
- add a provisional `webapp_dr_policy` classification for every model/table;
- inventory all integer-ID and cross-server reference dependencies;
- inventory all counter and delete behaviors;
- produce current parity and outbox/watermark baseline;
- measure normal cross-border traffic by flow class;
- measure request-to-event amplification for each authoritative command and multi-row transaction;
- reproduce the 2026-07-14 one-item-worker baseline with commit-to-receipt, ACK, throughput, CPU, database, Redis, and network attribution;
- define the production-shaped 300-request/second workload with the accepted 50/50 Bot/WebApp split.

Exit criteria:

- every table and file family is classified;
- no data family is implicitly replicated or excluded.

### Stage 2 - Logical Authority And Physical Site Separation

Status: source foundation completed on 2026-07-14; WebApp-IR deployment and the
full deployment-surface assertion matrix remain pending.

Deliverables:

- introduce site identity separate from logical authority;
- define `bot_fi`, `webapp_fi`, and `webapp_ir` configuration;
- preserve compatibility for current `iran`/`foreign` business semantics;
- add deployment-surface assertions for services and jobs;
- add tests proving site identity cannot change offer authority.

Exit criteria:

- business authority no longer depends on physical location;
- current two-server behavior remains unchanged under compatibility configuration.

### Stage 3 - Event Identity And Multi-Destination Delivery

Deliverables:

- add immutable event identity and metadata;
- add per-destination delivery ledger;
- add persisted receiver receipt and contiguous checkpoints;
- preserve origin identity through relay;
- make existing `change_log.synced` compatibility-only or migrate away from it safely;
- provide migration/backfill and rollback strategy;
- implement destination-aware batches of 50-100 with a 5-10 ms maximum flush window as initial tunable defaults;
- implement bounded 4-8 in-flight batch concurrency per destination without violating aggregate, stream, or transaction-envelope ordering;
- expose per-link throughput, amplification, batch, latency, saturation, backlog-growth, and backpressure metrics;
- benchmark sustained capacity at five times measured peak event arrival after amplification.

Exit criteria:

- one destination ACK cannot mark another destination delivered;
- duplicate, delayed, and relayed events are deterministic;
- the 300-request/second planning load meets the approved Finland-Finland latency SLOs;
- sender and receiver remain stable at the required five-times capacity gate with no unbounded queue growth.

### Stage 4 - Writer Epoch And Fencing

Status: source foundation in progress. Local durable fencing and the accepted
witness/lease contract and source-only authenticated transport/renewal exist;
dedicated deployment, global three-database proof, and production enablement
remain blocked.

Deliverables:

- implement promotion epochs/terms;
- enforce writer ownership in authoritative commands and background jobs;
- implement demotion and public readiness behavior;
- define witness/lease behavior and asymmetric-failure policy;
- add audit trail for promotion/demotion.

Exit criteria:

- automated tests cannot produce simultaneous accepted WebApp-authority writes on two sites;
- ambiguity fails closed for sensitive mutations.

### Stage 5 - Identity, Tombstone, Counter, And Field-Authority Hardening

Deliverables:

- complete canonical identities for users and remaining tables;
- migrate sync references away from local integer IDs;
- implement tombstones for replicated deletes;
- replace unsafe counter merges with projections or authority components;
- enforce field-level user authority;
- preserve existing offer/trade terminal guards.

Exit criteria:

- no supported outage scenario depends on last-write-wins timestamps;
- identity and delete replay are collision-safe.

### Stage 6 - WebApp DR Data And File Plane

Deliverables:

- implement `webapp_dr_policy` execution;
- replicate Messenger database state between WebApp sites only;
- implement resumable file/blob replication and manifests;
- classify rebuildable Redis/runtime state;
- implement chosen session-continuity policy;
- add DR lag and file integrity metrics.

Exit criteria:

- standby can pass an isolated read-only completeness drill;
- missing/corrupt required files block origin readiness.

### Stage 7 - Public Routing And Fixed Sync Domains

Deliverables:

- create separate public and sync virtual-host configurations;
- deny sync paths on the public host;
- deny public WebApp paths on sync hosts;
- configure pairwise TLS/HMAC trust;
- implement readiness endpoints;
- validate Arvan active-passive behavior without public traffic splitting.

Exit criteria:

- public failover cannot redirect physical sync traffic;
- sync hosts cannot serve user-facing WebApp traffic.

### Stage 8 - Recovery Orchestrator

Deliverables:

- create recovery session state machine;
- exchange manifests/checkpoints;
- request and replay missing ranges;
- relay preserved-origin events;
- integrate current short/medium/long publication rules;
- integrate conflict quarantine and parity gates;
- implement controlled final handoff;
- implement backlog ETA from measured net drain capacity while live traffic continues;
- enforce DPI-safe, database-safe recovery throttles without allowing live work to starve historical catch-up or vice versa;
- preserve the four approved cut-boundary states across reconnect and process restart.

Exit criteria:

- recovery is resumable after process restart;
- no failback occurs with gaps, conflicts, failed critical delivery, or parity failure.

### Stage 9 - Automated Failure Matrix

Required scenarios:

- normal three-site replication;
- duplicate event delivery;
- dropped wake-up with durable drain;
- out-of-order event delivery;
- missing sequence range;
- same event replay;
- same aggregate version with different hash;
- stale terminal-to-active attempt;
- completed-trade destructive attempt;
- WebApp-FI lost while Iran remains connected;
- Iranian international cutoff;
- partial/asymmetric connectivity;
- WebApp-FI and WebApp-IR simultaneous promotion attempt;
- process restart during promotion;
- process restart during recovery;
- short outage recovery;
- medium outage recovery;
- long outage recovery;
- bot remains active during every outage class;
- WebApp-IR remains active while recovery runs;
- relay without echo;
- integer ID collision fixtures;
- user identity collision fixtures;
- counter double-increment fixture;
- delete/update resurrection fixture;
- file transfer interruption/resume;
- missing/corrupt file blocking readiness;
- session failover behavior;
- Arvan public routing does not affect sync routing;
- failed parity blocks failback;
- final handoff with writes arriving near the barrier;
- standby is promoted while one acknowledged active-site event has not reached it;
- table-priority scheduling attempts to deliver a later stream sequence first;
- transaction envelope is interrupted after only part of a trade command arrives;
- startup mutation runs on a fenced standby;
- FI-to-IR works while IR-to-FI acknowledgments fail, and the inverse;
- writer lease expires during VM pause or clock drift beyond the safety margin;
- Arvan control API is unavailable or rate-limited during promotion;
- different Arvan PoPs temporarily route to different origins;
- an old WebSocket and keep-alive connection remains on the demoted origin;
- CDN returns stale health or accidentally caches a dynamic API response;
- origin or sync certificate expires during a national outage;
- DNS works globally but not nationally, and the inverse;
- recovery backlog fills event, WAL, database, Redis, or blob storage;
- database event succeeds while its blob transfer fails, and the inverse;
- deployment or migration starts while promotion/recovery is active;
- peers run incompatible protocol or schema versions;
- pairwise key rotation starts during a partition;
- a restored backup contains an old active epoch and completed side-effect outboxes;
- WebApp-FI recovery hub is permanently lost during the outage;
- WebApp-IR fails while it is the only active national WebApp origin;
- the international link repeatedly flaps during catch-up;
- recovery traffic exceeds the approved DPI-safe bandwidth envelope;
- power fails after fencing one writer but before enabling the next;
- duplicate operator promote/failback commands race;
- promotion automation restarts midway through an Arvan change;
- backup restore passes table counts but fails business, journal, or file parity;
- 300 requests/second split 50/50 between Bot and WebApp with measured write fractions and event amplification;
- 150 one-event mutations/second in each Finland direction without SLO breach or backlog growth;
- 300 one-event mutations/second on the normal WebApp DR path, then the measured amplified equivalent;
- batch-size, flush-window, and in-flight-concurrency boundary tests;
- connectivity cuts before receiver commit, after receiver commit but before ACK, and after source ACK persistence;
- retry of the same client `command_id` after an ambiguous response;
- different local integer IDs representing one natural identity or command;
- unique non-colliding IDs with a real business-state conflict, proving ID collision prevention alone is insufficient;
- one-hour modeled backlog recovery under simultaneous live traffic and DPI-safe throttling.

Exit criteria:

- failure paths prove safe rejection, not just happy-path success;
- the matrix records exact state, backlog, conflict, and parity evidence.

### Stage 10 - Staging Three-Site Drill

Deliverables:

- isolated three-site-like staging topology;
- no production peer or data;
- normal baseline and checksum evidence;
- controlled international-link cut;
- concurrent Telegram and WebApp activity according to current policy;
- short/medium/long recovery drills;
- repeated failover/failback drills;
- WebSocket and file continuity tests;
- Arvan routing validation;
- rollback drill;
- sustained 24-hour Finland-Iran replication benchmark with latency, loss, asymmetric ACK failure, payload mix, database pressure, and file traffic;
- measured recovery run proving backlog drain remains faster than live event production;
- retained p50/p95/p99 latency and five-times-capacity evidence for every directed link.

Exit criteria:

- repeated successful drills with no unresolved conflict;
- no duplicate trade, offer, notification, or publication side effect;
- canonical DB and file parity pass after recovery;
- owner reviews the evidence;
- Finland-Finland and Finland-Iran approved latency SLOs pass under the 300-request/second workload;
- no backlog grows while any link is reported healthy;
- capacity and storage remain within safe limits during the modeled long-outage catch-up.

### Stage 11 - Production Readiness Review

This stage is planning only and does not authorize production action.

Required gates:

- backup and restore drill current;
- migration dry-run and exact row counts;
- zero unsafe active-offer state at cutover if required by migration plan;
- rollback path documented;
- provider/Arvan behavior confirmed;
- monitoring and alerting active;
- runbook reviewed;
- owner explicitly authorizes a later production action;
- measured event amplification and five-times per-link capacity evidence are current;
- the selected Iran host has passed the required 24-hour benchmark;
- owner-approved per-data-class RPO/RTO and same-region durability decisions are recorded.

## 31. Rollback Requirements

Every schema stage must be additive and compatibility-first.

Rollback must be able to:

- keep current two-server product sync operational while new tables are dark;
- disable third-site routing without losing durable events;
- fence `webapp_ir` and restore `webapp_fi` only after safe state proof;
- stop new protocol emission while retaining readable audit history;
- preserve all historical offers, trades, requests, users, and files;
- avoid destructive cleanup as a rollback shortcut;
- keep old protocol compatibility until all sites have upgraded or rolled back.

## 32. Confirmed Decisions And Remaining Open Decisions

### 32.1 Owner-confirmed synchronization decisions on 2026-07-14

The following decisions are no longer open:

1. User success does not wait for cross-site or all-three-site ACK.
2. User success for a mutation requires one atomic authoritative commit containing both business state and its immutable replication journal/transaction envelope.
3. Replication is asynchronous, durable, retryable, idempotent, ordered, and independently ACKed per required destination.
4. Three-site equality is not claimed during a partition. Safety comes from one writer per aggregate, durable local streams, stable canonical identity, and controlled convergence.
5. Public traffic remains on `webapp_ir` after international connectivity returns until required deliveries, gaps, conflicts, canonical database parity, file parity, stability, and final handoff pass.
6. Product sync and WebApp DR use separate data policies; the three physical databases are not expected to be byte-identical.
7. Local integer IDs remain implementation details. Replicated identities and references use stable canonical identities; non-colliding integers alone do not prevent business conflict.
8. Capacity planning uses measured replication-event arrival after write fraction and event amplification, not raw request RPS.
9. The accepted planning baseline is 300 requests/second split 50/50 between Bot and WebApp.
10. Initial sender tuning is 50-100 events per batch, 5-10 ms maximum batch wait, and 4-8 bounded in-flight batches per destination, subject to benchmark tuning without weakening ordering.
11. Every directed link must demonstrate sustained accepted-event capacity of at least five times measured peak arrival after amplification.
12. Initial Finland-Finland latency targets are 20-80 ms normal, p95 below 150 ms, and p99 below 300 ms.
13. Initial healthy-link Finland-Iran targets are 100-500 ms normal, p95 below 750 ms, and p99 below 2 seconds; these remain benchmark-gated on the selected Iran host.
14. Receiver-commit/ACK ambiguity is resolved by replaying the same immutable `event_id`; client-response ambiguity is resolved by replaying the same `command_id`.

### 32.2 Decisions that remain open

The following items still require explicit selection or measured evidence:

1. Exact Iran server provider, IP, capacity, and storage layout.
2. Exact public and physical sync domain names.
3. Exact Arvan active-passive control mechanism and Iran-PoP health semantics.
4. Exact witness deployment isolation, authenticated transport, key custody,
   measured lease tuning, and availability target under asymmetric partitions;
   the state-machine contract itself is accepted in the 2026-07-14 ADR.
5. Exact WebApp DR transport technology for database/event/file transfer.
6. Session-continuity design and acceptable reauthentication behavior.
7. Exact RPO/RTO and maximum promotion lag per data class, especially locally acknowledged financial writes.
8. Whether zero-loss financial durability requires an Iran-local and Finland-local independent database/WAL replica before user success.
9. Retention duration for events, receipts, tombstones, and files.
10. Operator approval thresholds for large medium/long recovery-expiry batches.
11. Final benchmark-derived batch size, concurrency, traffic budget, and recovery rate for the selected Iran provider.

These decisions must be resolved before their dependent implementation stage. They must not be filled in by assumption. In particular, rejecting cross-site ACK removes cross-border connectivity from the user-success path but does not authorize a zero-loss claim without an approved local durability and RPO contract.

## 33. Final Acceptance Criteria

The architecture is ready for production consideration only when all of the following are proven:

- Telegram Bot remains active throughout Iranian international outages.
- Public WebApp traffic switches to Iran only after valid promotion.
- Public traffic never splits across two WebApp writers.
- `webapp_fi` can remain a sync relay while public-WebApp-fenced.
- Existing short/medium/long outage behavior remains unchanged.
- Every authoritative event has stable identity, authority, producer, epoch, sequence, aggregate identity, version, and hash.
- Every required destination has an independent delivery state.
- A source cannot mark one destination delivered because another destination ACKed.
- Recovery relay preserves origin and cannot echo.
- Duplicate and out-of-order delivery are idempotent and deterministic.
- A same-version/different-hash conflict blocks failback.
- Terminal offers and completed trades cannot be reopened or destructively overwritten.
- User identity, deletes, counters, and files have explicit non-LWW policies.
- Canonical DB parity and file parity pass.
- Recovery remains on the Iran WebApp until catch-up and stability gates pass.
- Failback uses a final write barrier and new writer epoch.
- Public and sync domains are separate.
- Sync endpoints are non-cacheable, fixed-destination, authenticated, and not exposed through the public user host.
- A 300-request/second workload split 50/50 between Bot and WebApp has measured write fraction and event amplification evidence.
- Every directed replication link sustains at least five times its measured peak amplified event-arrival rate.
- Finland-Finland p95 is below 150 ms and p99 below 300 ms under the accepted workload.
- The selected Finland-Iran link passes its provisional p95 below 750 ms and p99 below 2 seconds targets or has a newer explicit owner-approved contract.
- No healthy link accumulates an unbounded backlog.
- Every cut-boundary case is proven: applied-and-ACKed, applied-with-lost-ACK, not-applied-and-pending, and uncommitted local command.
- Reusing an `event_id` or `command_id` is idempotent and does not duplicate business state or side effects.
- Canonical identity and authority/version rules prevent conflicts even when local integer IDs differ and do not collide.
- Repeated staging drills pass with evidence and rollback.
- No production action occurs without a later explicit owner authorization.

## 34. Deep Project Challenge Register

### 34.1 Audit scope and interpretation

This register is based on a static, repository-backed audit of the current branch and the architecture described in this roadmap. It covers application code, database models, sync protocol, background jobs, sessions, Messenger/media, deployment manifests, Nginx, staging, backup/recovery, tests, and existing policy documents.

Original audit baseline: branch `candidate/webapp-ui-ux-unification` at commit
`75972f6b`. The post-merge refresh was completed against `main` at `04fef2a5`;
sections 36 and 37 record the later live Arvan validation and the local-fencing
foundation implemented at `8ff234de`.

It does not claim to validate the current live state of Arvan, any production host, the future Iran provider, routing, firewalls, certificates, capacity, or provider control-plane behavior. Those items remain explicit operational-verification challenges.

Challenge classifications:

| Priority | Meaning |
|---|---|
| P0 - safety blocker | Promotion, three-site writes, or production failback must not be enabled until closed. Failure can create split brain, lose acknowledged state, corrupt business invariants, or expose a security boundary. |
| P1 - production-readiness blocker | The architecture may be prototyped, but production readiness cannot be declared until closed and evidenced. |
| P2 - resilience or operability gap | May be scheduled after the safe core exists, but must have an owner and deadline before broad production scale. |

A challenge is closed only when all of the following exist:

- an explicit architectural or product decision;
- an implementation or an explicitly accepted limitation;
- automated tests at the appropriate layer;
- operational evidence from the three-site-like staging environment;
- observability and a rollback or safe-stop path;
- an owner who accepts the residual risk.

The audit also found reusable foundations. They must not be mistaken for completed three-site capability:

| Current foundation | Reusable value | Boundary that remains |
|---|---|---|
| Transaction-coupled change logging in core/events.py | A local business commit can durably create outbound work. | The row has local identity, one peer-level delivery state, and no transaction envelope. |
| Protocol and registry metadata | Sender and receiver already negotiate explicit protocol/table-policy versions. | Legacy/missing metadata is still accepted and the registry models product sync, not WebApp DR. |
| Aggregate watermarks and payload hashes | Stale, duplicate, and same-sequence conflict detection has a working base. | Ordering is two-server/local-sequence based; gaps, relay identity, durable quarantine, and canonical hashing remain incomplete. |
| Offer and completed-trade receiver guards | Important terminal-state regressions are already rejected on some paths. | The invariant is not yet one shared state machine across all local, job, repair, relay, and DR paths. |
| Stable offer_public_id, idempotency keys, trade-number partitions, and several side-effect dedupe keys | Several business identities can be migrated rather than reinvented. | Users, Messenger entities, files, and multiple foreign keys still lack safe global identity. |
| Quick parity and strict parity-observability gates | There is an operational pattern for blocking on drift. | Coverage is selected/truncatable product data and excludes full journal, DR-only data, and file-byte parity. |
| Existing short/medium/long outage policy | Business recovery behavior is already decided for major offer/publication cases. | The policy is not yet fully implemented or composed with three-site promotion and recovery. |
| Sync TLS verification and current production backup scripts | Secure transport and recoverability work have partial building blocks. | Other internal RPCs, pairwise identity, PITR, DB-plus-blob consistency, and three-site roles are missing. |

### 34.2 Technical safety blockers

| ID | Priority | Challenge and current evidence | Failure mode | Required closure evidence |
|---|---|---|---|---|
| T-001 | P0 | `logical_authority` and explicit `physical_site` now model `bot_fi`, `webapp_fi`, and `webapp_ir`, while legacy `server_mode` remains for compatibility. Runtime role is derived from durable local writer state. Event metadata, remaining tooling, metrics, and every deploy surface have not yet been migrated to the new identity. | An unconverted path can still treat logical Iran/WebApp authority as a physical host and route, authorize, or label work incorrectly. | Complete the identity inventory and migration; prove every write, event, job, metric, lock, CLI, and deployment surface records and validates the correct logical authority, physical site, runtime role, and epoch. |
| T-002 | P0 | Additive migrations now provide local durable writer epoch/state, transition audit, optional signed-lease evidence, HTTP/startup/background preflight, and a SQL `before_commit` recheck. This protects a local database but does not yet bind every command/event/side effect to the global epoch or stop an old remote database by itself. | An unaudited path, external side effect, long-lived process, or independently active remote database can escape the local fence after promotion. | Complete the mutation/side-effect audit, carry and reject stale epochs at every destination, deploy the witness, and demonstrate simultaneous promotion against independent databases with exactly one accepted writer. |
| T-003 | P0 | Main WebApp HTTP mutations, the known startup mutation, and policy-listed background jobs now consult physical site/local writer state and, when enabled, witness lease validity; the leader restarts when lease eligibility changes. Repair tools, every worker/route, and effects that can occur before commit still need a complete audit. | An uncovered worker, direct-origin path, repair command, or pre-commit external effect can write or act after the site's term is no longer valid. | Fence every HTTP/WebSocket mutation, startup mutation, recurring job, side-effect worker, repair process, and migration helper; prove no external effect can escape a stale or expired term. |
| T-004 | P0 | The accepted database-clock witness remains dark on the independent Iran-reachable host with isolated PostgreSQL roles/schema, private TLS, fixed FI/IR ingress, pairwise HMAC, Ed25519 signing custody, measured clock evidence, reboot persistence, local backups, and encrypted immutable HEL1 custody. A second clean host at `185.206.95.94` was rebuilt from a checksumed manifest/wheelhouse, restored first into an isolated database and then into live through a validated candidate/rollback swap, rebooted, and revalidated as `webapp:0:vacant`. WebApp-FI and WebApp-IR authenticated TLS each returned `200`, unsigned access from each returned `401`, WebApp-IR direct TCP `443` and UTC/NTP alignment were proven, and a non-allowlisted source timed out. Pairwise FI and IR HMAC credentials were each rotated from `v1` to `v2` through an overlap: old and new both returned `200` before revocation, then new returned `200` and old returned `401`; no credential persisted in either WebApp runtime. The guarded four-PostgreSQL drill still proves concurrent acquisition, asymmetric partition, delayed negative replay, lost response, service/database recreation, local fail-closed expiry, and epoch-2 IR activation. All WebApp witness enforcement/renewal flags remain disabled. | The real independent-host directional fault matrix is still unproved; host compromise, clock jump, disk-full, unsafe operator action, or a network interleaving outside the container drill can still prevent safe ownership proof. | Repeat concurrent acquire, lost response, delayed packet, process/DB/VM pause, host loss, clock jump, disk-full, and directional FI/IR faults across the real hosts before enabling any lease. |
| T-005 | P0 | models/change_log.py has one local integer id and one synced/verified state; core/sync_worker.py drains to one target URL and marks the row delivered after that peer succeeds. | Delivery to Bot-FI can incorrectly imply delivery to WebApp-IR, or vice versa; retention can delete an event still needed by one destination. | Implement immutable global event identity plus a per-destination delivery ledger, destination-specific ACKs, retry state, retention gates, and backlog metrics. |
| T-006 | P0 | Receiver-applied transactions use is_sync and core/events.py intentionally suppresses new change-log creation for such writes. This prevents two-site echoes but also prevents WebApp-FI from relaying an original Bot-FI event to WebApp-IR through the current outbox. | WebApp-IR misses foreign-origin changes accumulated at WebApp-FI during a national outage, or relay is re-enabled naively and creates loops. | Implement preserved-origin relay: same event_id, origin site, origin sequence, authority, epoch, and payload hash; create only the missing destination delivery without generating a new business event. |
| T-007 | P0 | Sync ordering uses the local change-log integer as source_sequence. The worker prioritizes table class before id, so a larger sequence can be sent before a smaller one, while the receiver can advance to the larger value without a durable missing-range record. Database sequences may also contain rollback gaps, and two physical WebApp databases can overlap. | A legitimate event is classified stale, a missing event is never requested, or different events occupy the same apparent sequence. | Define an emitted stream sequence independent of ordinary database id gaps, scope it by producer_site and epoch, persist gap ranges, and test priority reordering, rollback gaps, restore, promotion, and same-sequence/different-hash cases. |
| T-008 | P0 | core/sync_protocol.py still accepts missing protocol metadata as a legacy path, and sync_watermark_strict_mode defaults false. | A downgraded or partially upgraded sender can bypass the metadata and ordering guarantees required for safe three-site recovery. | Define a rolling-upgrade compatibility matrix, deploy strict receiver capability first, gate legacy acceptance by peer/version and time, then prove production readiness with strict mode on everywhere. |
| T-009 | P0 | The sync HMAC envelope uses one global sync_api_key, a timestamp window, and timestamp-plus-body signing. It has no pairwise source/destination audience, key id, durable nonce, or event receipt binding. | A compromised site can impersonate another peer; a captured request can be replayed inside the window; key rotation can interrupt all links at once. | Use pairwise credentials or mTLS identities, bind source, destination, method, path, protocol, key id, and request/event identity into authentication, persist replay protection, and drill overlapping key rotation. |
| T-010 | P0 | The owner has decided that user success will not wait for cross-site ACK; success requires an atomic local business-plus-journal commit and per-destination delivery continues asynchronously. Exact per-data-class RPO, maximum safe promotion lag, and same-region independent durability remain undecided. A lagging standby can still branch from an older aggregate version if promoted without an approved threshold and fencing proof. | A permanently lost active site can lose locally acknowledged but unreplicated financial mutations, or unsafe promotion creates a deterministic same-version conflict even though there was only one writer at a time. | Approve RPO and maximum promotion lag per data class; decide local database/WAL replica requirements; gate promotion on contiguous checkpoints, conflicts, and writer epoch; test process/host/disk loss and promotion at every local-commit, receiver-commit, lost-ACK, and source-ACK boundary. |
| T-011 | P0 | users has only a local integer primary key; natural identifiers such as mobile_number, account_name, and telegram_id are nullable or independently unique. core/sync_parity.py falls back to id for user identity. | Independent registration or restore can create duplicate humans, uniqueness failures, or incorrect foreign-key attachment. | Introduce a stable public user/account identity and a deterministic identity-resolution workflow; migrate references safely and test Bot-only, WebApp-only, linked, conflicting, and restored accounts. |
| T-012 | P0 | Multiple cross-site entities still use local integer identifiers and integer foreign keys, including users, chats, members, conversations, messages, and several relationship tables. | Equal integers can refer to different objects, while regenerated IDs can break callbacks, replies, ownership, or parity. | Produce a complete identity inventory; adopt UUID/public IDs or explicit source-scoped composite identities; prohibit generic record_id matching where identity is not globally stable. |
| T-013 | P0 | The current user merge path applies greatest/max-style rules to counters. Concurrent increments on Bot-FI and WebApp-IR are not additive, and independent daily/global limit checks can each succeed during a partition. | Counts are lost after merge, and financial/request limits can be exceeded even if row convergence later succeeds. | Convert critical counters to immutable ledger events or per-authority monotonic components; define quota reservation or fail-closed rules for partitions; reconcile from source events and test concurrent increments. |
| T-014 | P0 | Users and other shared rows still have broad row-upsert/recency behavior for fields with different business authorities. A single global aggregate-version sequence is also invalid for a User when Bot and WebApp authorities can legitimately update separate field families during a partition. | A newer timestamp from a non-authoritative surface can overwrite protected data, or both authorities can produce the same next User version with different valid content. | Maintain a field-level authority matrix and separate authority-owned subaggregate/version streams, or select an available coordinator; enforce event type, allowed writer, conflict key, merge/reject rule, and side effects on both local writes and receiver apply. |
| T-015 | P0 | Deletes do not have a universal durable tombstone identity/version/retention contract across all replicated tables. Some receiver delete paths physically remove rows and can advance ordering even when a delete cannot be applied. | An old update or a restored standby can resurrect deleted preferences, relations, messages, files, or business rows; a lost delete can become unrecoverable. | Define per-table delete semantics, durable tombstones, causal version checks, retention beyond maximum offline/backup replay age, and delete-versus-update tests. |
| T-016 | P0 | Terminal offer/trade guards exist in the sync receiver, but the invariant is not yet enforced as one common state machine across local API, Bot, jobs, recovery replay, repair tools, and future DR receiver paths. | A stale path can reopen an expired/traded offer or destructively replace a completed trade despite another path being protected. | Centralize terminal-state transitions and destructive guards; add database constraints where possible; run the same invariant suite against every mutation and repair surface. |
| T-017 | P0 | core/sync_registry.py correctly excludes Messenger, chat files, upload state, push subscriptions, and user sessions from current Bot/WebApp product sync. The new standby, however, needs a separate WebApp-to-WebApp DR policy for much of that state. | Reusing product sync either leaks WebApp-local runtime data to Bot-FI or leaves WebApp-IR unable to take over the complete WebApp experience. | Create a distinct webapp_dr registry with explicit replicate, rebuild, local-only, and secret/runtime classifications for every table and storage object; enforce registry completeness in CI. |
| T-018 | P0 | Messenger entities use local integer IDs and reply/member relationships; there is no demonstrated independent-database merge protocol for conversations and messages. | Concurrent or replayed messages can attach to the wrong conversation, duplicate, reorder incorrectly, or lose reply relationships. | Define stable conversation/message/member identities, ordering and idempotency, delete/edit semantics, unread-state ownership, and a WebApp-FI/WebApp-IR-only replication test matrix. |
| T-019 | P0 | Chat metadata refers to local file paths and Compose uses host-local named upload volumes; there is no content-addressed blob manifest, destination receipt, or verified file-copy pipeline. | Database parity can pass while an attachment or thumbnail is missing or corrupt after failover. | Add immutable content hashes, blob manifests, resumable transfer, per-destination verification, garbage-collection safety, encryption policy, and make critical file parity a readiness gate. |
| T-020 | P0 | Runtime sessions are intentionally local to home_server; JWT subjects and many authorization paths use local integer user IDs. Refresh-token state, revocation, primary-session rules, and Redis security state are not standby-continuous. | Existing users are logged out, mapped to the wrong account, or retain revoked access after origin switch; simultaneous refresh can violate session rules. | Decide the acceptable reauthentication contract, then implement canonical JWT subject mapping, shared/replicated key material, refresh and revocation semantics, and failover tests without weakening local session authority. |
| T-021 | P0 | `/health/live`, `/health/ready`, `/health/sync`, and protected `/health/origin-ready` now separate process, local dependency, sync, and active-origin evidence. Origin readiness checks local writer/witness state, evidence freshness, release/schema identity, database/Redis, background jobs, and assets, and deliberately stays false while witness enforcement is disabled. Promotion/failback parity, file, measured clock, and automatically generated signed evidence are still absent. | Arvan can still be switched using incomplete or operator-asserted evidence unless the final orchestrator requires every pending DR gate. | Add promotion-ready and failback-ready evidence backed by journal/parity/file/clock measurements, sign and retain the evidence, and make Arvan orchestration consume only the complete fail-closed gate. |
| T-022 | P0 | The current Iran Nginx template proxies the general /api/ surface on a public virtual host; sync is not yet isolated to fixed physical sync hosts. | CDN caching, WAF rules, public exposure, origin switching, or an attacker can affect machine-to-machine replication. | Create separate public and sync vhosts/domains; deny sync paths on public hosts and public application paths on sync hosts; enforce non-cacheability, origin ACLs, TLS identity, and direct destination tests. |
| T-023 | P0 | Recovery currently has no durable session state machine, barrier protocol, or resumable manifest/range reconciliation implementation. | Restarting recovery can duplicate work; live writes can cross the parity snapshot; a zero-backlog observation can race with a new commit and permit unsafe failback. | Persist recovery sessions and phases, use stream high-water manifests, gap repair, final write barrier, new epoch handoff, resumability, and an auditable proof bundle. |
| T-024 | P0 | Existing quick parity covers selected product tables, may truncate at a row limit, and does not prove the event journal, Messenger, sessions, or file bytes. User identity can fall back to local id. | A green checksum can conceal missing rows, identity misattachment, event gaps, or absent blobs. | Define canonical per-table identity and serialization, full deterministic range hashing, journal and delivery parity, blob parity, schema/release checks, and explicit large-dataset behavior without silent sampling. |
| T-025 | P0 | Current outage rules are accepted in policy documents but parts are still described as unimplemented. The new recovery layer must preserve short, medium, and long behavior while two independent event streams converge. | A generic replay can publish stale active offers, violate expiry rules, duplicate trades/notifications, or keep the foreign surface open past a required close. | Encode outage class and authoritative state into recovery decisions; reuse one tested domain service for finalization; prove all current policy scenarios before adding routing changes. |
| T-026 | P0 | Side effects have several specialized outboxes and dedupe keys, but three-site recovery introduces a second delivery dimension and relay path. Database convergence alone does not prove Telegram posts, push notifications, SMS, WebSocket events, or market publications occurred exactly once where required. | Users see duplicate/missing notifications or stale active listings even though rows match. | Inventory every side effect, declare its execution authority and outage behavior, use stable business idempotency keys and destination receipts, and include side-effect parity in failback gates. |
| T-027 | P0 | A same-version/different-payload event is detectable in parts of the current watermark receiver, but canonical event-hash semantics across relay, schema versions, local IDs, outbox serialization, and sanitized repair payloads are not yet defined. | Equivalent events can hash differently, or different business events can be mistaken for duplicates after transformation. | Version canonical serialization; hash stable business identity and normalized payload; preserve the original hash through relay; quarantine rather than auto-merge ambiguous equality. |
| T-028 | P0 | Trade numbers use odd/even logical-authority sequences, which is a useful two-authority guard, but promotion does not yet prove sequence state, logical-authority mapping, or fencing on the standby. | WebApp-IR can allocate the wrong parity, reuse restored values, or WebApp-FI can continue allocating after promotion. | Keep logical-authority sequence ownership, validate sequence maxima and parity before promotion, replicate or advance state safely, and reject allocation on a fenced physical site. |
| T-029 | P0 | Rolling schema and protocol compatibility across three sites is not specified. Current deploy and migration flow assumes two roles and compatibility paths are permissive. | One site emits fields another cannot apply, or a rollback reintroduces a writer that cannot understand the current journal. | Define expand-migrate-contract phases, minimum compatible release/protocol registry, per-site upgrade order, dark reads/writes, rollback floor, and mixed-version failure tests. |
| T-030 | P0 | Arvan active-passive behavior, health semantics from Iranian PoPs, API/control-plane availability during national isolation, cache behavior, and origin stickiness are not yet proven. | Traffic can split, switch before promotion, remain on a failed origin, or be impossible to switch during the exact outage being handled. | Obtain and test the exact Arvan mechanism with controlled origins; separate health observation from writer promotion; retain signed manual override and rollback procedures with evidence from outage drills. |
| T-031 | P0 | A business command can update Offer, OfferRequest, Trade, delivery receipts, and other rows in one local transaction, while the current worker sends independent change items and has no transaction_id or causation envelope. | A receiver observes a partially applied business command or runs side effects before all invariant-related rows arrive. | Add transaction/command causation metadata and define atomic batch apply or an invariant-safe dependency protocol; crash-test every boundary of multi-row trade execution. |
| T-032 | P0 | Security hardening is inconsistent across internal RPCs. Sync transport verifies TLS, but trade and customer/authority forwarding paths can default to TLS verification disabled and are not all bound to fixed physical destinations. | A sensitive internal command can be intercepted, redirected through the public/CDN path, or accepted by the wrong site. | Inventory every internal endpoint, require strict TLS and pairwise audience-bound authentication, isolate it on internal vhosts, and make insecure defaults impossible in production validation. |
| T-033 | P0 | Current recovery/publication state is partly local-Redis based, error behavior can fail open, and operator tooling can clear a recovery gate without full parity/file/conflict proof. | A process restart loses recovery state or an operator resumes publication/failback while another site still has gaps. | Persist the recovery state machine and approvals in durable storage, make every safety gate fail closed, and require a signed proof bundle before any CLI/API can clear it. |
| T-034 | P0 | Event convergence is not the only cross-site path. During recovery WebApp-IR remains the active command authority, while Bot-FI may need a synchronous command result; current routing generally points to one logical peer/hub. | A Telegram command is sent to fenced WebApp-FI, rejected unnecessarily, or executed against stale state while recovery is still in progress. | Define active-authority command routing separately from event relay, with signed active-site discovery, idempotent command receipts/reservations, no public-domain fallback, and hub-loss behavior. |

### 34.3 Technical production-readiness and operability challenges

| ID | Priority | Challenge and current evidence | Failure mode | Required closure evidence |
|---|---|---|---|---|
| T-101 | P1 | core/connectivity.py treats any HTTP response, including an application error, as connectivity and probes one target on a periodic loop. | A service error is mistaken for healthy international connectivity, or a single-path failure triggers the wrong outage classification. | Build multi-vantage, multi-target probes with reason codes, asymmetric-link detection, consecutive thresholds, hysteresis, and a recorded decision timeline. |
| T-102 | P1 | Deployment configuration, scripts/deploy_config.py, runtime-env rendering, production env examples, host-file rendering, and recovery tools are hard-coded around one iran and one foreign host. | The third site receives the wrong environment, secrets, artifacts, Compose project, peer URL, backup role, or recovery command. | Replace role-only manifests with site inventories and generated per-site configuration; validate uniqueness, topology, secret scope, and dry-run output in CI. |
| T-103 | P1 | Current staging still runs logical Iran and foreign services against shared infrastructure. A separate guarded control-plane drill now provides four independent PostgreSQL containers and deterministic witness-link failure, response loss, service recreation, and database pause without production peers/data, but it does not yet isolate Redis, uploads, clocks, hosts, or the complete application/sync topology. | Product, storage, identity, replication, and recovery tests can still pass because the full staging system shares state or one host even though the witness subset no longer does. | Extend the guarded topology to independent Redis/upload planes and full Bot-FI/WebApp-FI/WebApp-IR runtimes with controllable directional latency, loss, DNS, process/host restarts, disk faults, and clock skew. |
| T-104 | P1 | Backup tooling and the recoverability runbook understand only foreign and one iran role. The documented database backup RPO is daily, DB and live uploads are archived separately, and off-host copying has manual elements. | The promoted site, journal, delivery ledger, file manifest, or latest acknowledged writes cannot be restored to one consistent point. | Define backup sets per physical site, PITR/WAL and DB-plus-blob recovery manifests, automated encrypted immutable off-host copies, and selective plus full restore drills. |
| T-105 | P1 | Production documentation records no complete Iran-offline deployment branch, no immutable release rollback layout, bind-mounted application code, and removed object-store assumptions. | Operators cannot patch, roll back, renew artifacts, or reconstruct the active Iran site while international access is unavailable. | Pre-position signed immutable releases/images, dependencies, migrations, certificates, and rollback artifacts inside Iran; prove an offline deploy and rollback drill. |
| T-106 | P1 | The 300-request/second 50/50 planning load, event-rate formula, five-times capacity rule, and catch-up formula are approved, but actual write fraction, event amplification, payload/file volume, storage growth, database cost, and DPI-safe net drain rate remain unmeasured. | Disks fill during isolation or replay saturates database/network and degrades the still-active WebApp; a nominal events/second target can be false if one command amplifies into many rows or large blobs. | Measure amplification and bytes by data class; load-test short/medium/long backlog with simultaneous live traffic; enforce quotas, watermarks, reserved disk, adaptive throttling, non-starvation, and an evidence-based recovery ETA. |
| T-107 | P1 | There is no three-site observability schema for origin site, authority, epoch, event, destination, recovery session, CDN decision, and file receipt. Current monitoring also depends heavily on foreign-side visibility. | Operators cannot distinguish application failure, partition, stale standby, split brain, queue lag, or a safe duplicate, especially during isolation. | Define bounded metrics plus trace/audit identifiers, inside/outside Iran probes and alerts, cross-site dashboards, clock-skew handling, and a durable incident timeline. |
| T-108 | P1 | Redis contains heterogeneous state: leader locks, OTPs, rate limits, caches, revocations, counters, leases, and worker coordination. No per-key-class DR decision exists. | Replicating unsafe leases creates duplicate work; not replicating security/session state weakens controls; stale cache can mislead users. | Inventory Redis namespaces and classify each as rebuild, replicate, local-only, or database-backed; define promotion initialization and tests for every security-critical class. |
| T-109 | P1 | Upload sessions persist local temporary paths and chunk progress; finalization reads and writes local disk. | Promotion during a multipart upload strands temporary bytes or lets a client resume against a site that lacks chunks. | Define in-flight upload behavior: sticky completion, explicit restart, or replicated chunk store; bind resume tokens to site/generation and garbage-collect safely. |
| T-110 | P1 | Sync repair, seed, benchmark, backup, readiness, and cutover scripts contain two-server role assumptions and some operate by broad table replay. | An operator can repair the wrong direction, regenerate identity/hash incorrectly, or mark a destination complete while another is missing. | Make all tools topology-aware, dry-run by default, require source/destination/event-range scope, preserve origin metadata, produce signed manifests, and test interrupted repair. |
| T-111 | P1 | trusted_proxy_cidrs defaults to loopback while the public origin will sit behind Arvan; WebSocket access tokens are passed in the query string. | Client identity, rate limits, security logs, and audit trails may use the CDN address, while tokens may appear in proxy/CDN logs. | Configure and continuously validate trusted proxy ranges/header policy, sanitize query logging, prefer a safer WebSocket authentication handshake, and test spoofed forwarding headers. |
| T-112 | P1 | Origin restriction, certificate issuance/renewal, mTLS feasibility, DNS dependency, and Iran-offline certificate lifetime are not defined. Existing certificate automation assumes a domain can reach the target host. | Public users or sync peers cannot connect during outage, HTTP-01 reaches the wrong origin, or origins become directly reachable and bypass CDN/WAF controls. | Define origin ACLs, edge/origin/internal certificate ownership, DNS-01 or equivalent renewal, mTLS lifecycle, emergency rotation, and direct-origin denial tests. |
| T-113 | P1 | Event, receipt, tombstone, audit, and blob retention/compaction rules are undecided. Long-disconnected or restored sites may need history older than the primary retention window. | Required replay data is compacted before a standby catches up, or storage grows without bound. | Set retention from maximum outage plus backup age, require all-destination durable checkpoints before deletion, archive safely, and test rejoin beyond the normal retention window. |
| T-114 | P1 | Standby parity currently does not gate exact application release, migration head, configuration fingerprint, feature flags, secrets/key versions, timezone/clock, or static frontend assets. | A data-current standby can still behave differently or fail after traffic arrives. | Produce signed site manifests and readiness checks for release, schema, config, key set, static assets, clock, dependencies, and rollback compatibility. |
| T-115 | P1 | Failover is discussed mainly for Iranian international isolation; local WebApp-FI hardware/database failure while Iran still has global connectivity and simultaneous WebApp-IR failure during an outage need explicit handling. | The control plane applies the wrong outage rules or has no safe service path for a different failure class. | Create a failure taxonomy separating origin, database, storage, provider, CDN, and connectivity failures; define promotion eligibility and degraded behavior for each combination. |
| T-116 | P1 | Network topology can be asymmetric: Bot-FI may reach WebApp-FI while WebApp-IR cannot, one direction may pass HMAC requests but not ACKs, or Arvan may reach an origin operators cannot. | Backlogs and ownership decisions diverge even though a coarse health check appears green. | Model every directed link separately, expose send/receive/ACK health, use idempotent retries, and test one-way blackholes and delayed ACKs. |
| T-117 | P1 | External dependencies have different locality: Telegram API and Web Push are international; SMS.ir, Arvan object storage, DNS, CA, and time sources have separate Iranian availability characteristics. | WebApp-IR is healthy but login OTP, invitation SMS, push, media, certificates, or notifications fail or retry indefinitely. | Inventory every endpoint, classify required/optional by runtime mode, provide bounded queues/fallback UX, and drill dependency-specific failure from each site. |
| T-118 | P1 | Same-origin WebSocket reconnect is helpful for origin switching, but the client reconnects without a demonstrated durable event cursor/resync protocol. Existing connections can remain attached to the demoted origin after an Arvan change. | Users miss events, receive duplicates, or continue issuing requests through a stale long-lived connection. | Add durable cursor or post-reconnect reconciliation, close/drain demoted-origin sockets, configure Arvan WebSocket behavior, and test events at the handoff boundary. |
| T-119 | P1 | The active Iran site can become the only immediately reachable copy of WebApp-origin writes during a long national outage; its local disk/database failure is not covered by the third-site plan. | A second failure during isolation destroys acknowledged Iranian WebApp activity. | Decide an Iran-local backup/replica strategy and RPO, isolate failure domains where possible, and test restore while international connectivity remains absent. |
| T-120 | P1 | Audit and security logs are local and can be incomplete during isolation; application clocks can diverge. Timestamp order is not causal order, while HMAC windows and future leases depend on time. | Incident reconstruction and financial dispute evidence become ambiguous; clock drift can reject valid traffic or make a lease unsafe. | Use event identity/sequence for causality, tamper-evident audit export, Iran-reachable time sources, clock-offset readiness thresholds, and clock-jump tests. |
| T-121 | P1 | A deterministic witness-specific harness now covers four independent PostgreSQL containers, concurrent acquisition, one directional partition, delayed negative replay, post-commit response loss, service recreation, witness database pause/resume, and local lease expiry. It does not yet cover the full product journal/storage planes or the wider fault matrix. | Rare business-event, storage, migration, and control-plane interleavings remain untested and could otherwise first appear in production. | Add bidirectional latency, loss, reordering, duplication, process/VM kill, disk-full, clock-skew/jump, CDN switch, key rotation, migration overlap, sync convergence, and file recovery scenarios with reproducible seeds. |
| T-122 | P1 | Safety properties are described in prose but are not represented as machine-checked invariants or state-machine/property tests. | Scenario tests miss an unexpected interleaving that violates single writer, monotonic terminal state, idempotency, or no-lost-ACK guarantees. | Encode core invariants in property/model-based tests and database assertions; retain minimized failing traces as regression fixtures. |
| T-123 | P1 | A 2026-07-14 low-load baseline found 0.790 ms average Finland-Finland RTT and current one-item delivery p50/p95/p99 of 57.74/157.33/220.56 ms across 146 samples, but the current serial batch-of-one worker provides only about 14 deliveries/second and no production-shaped benchmark proves the approved 300-request/second scenario, five-times headroom, Iran-link SLO, maximum safe outage backlog, or DPI-safe recovery. | Low-load latency is mistaken for capacity; production queues grow without bound, recovery triggers filtering, or the active database is overloaded. | Implement bounded micro-batching and concurrency, then benchmark representative amplified transaction envelopes, Messenger blobs, file traffic, long-outage backlog, parity, and final barrier; prove per-link five-times capacity, latency SLOs, traffic budgets, ramp-up, and abort thresholds. |
| T-124 | P1 | Restoration, promotion, recovery, failback, and rollback have separate concepts but no single end-to-end drill proves their composition. | A valid backup cannot be promoted, or a promoted restore cannot reconcile and safely return traffic. | Run repeated full-lifecycle drills from backup restore through active service, bidirectional catch-up, parity, failback, and rollback with retained evidence. |
| T-125 | P1 | Runtime secrets are rendered broadly across roles; least privilege for Telegram token, JWT, VAPID, SMS, database, Arvan, repair, and pairwise sync credentials is not yet implemented. | Compromise of one WebApp site exposes unrelated authorities or lets an application container change control-plane state. | Generate three least-privilege secret sets, keep control/repair credentials out of normal containers, rotate by key version, and drill loss of one site. |
| T-126 | P1 | WebApp-FI is intentionally a recovery hub, but the reconstruction path if that hub is permanently lost during an outage is not defined. | Bot-FI and WebApp-IR remain active yet cannot converge through the assumed relay, delaying recovery indefinitely or encouraging unsafe public-domain rerouting. | Define clean hub rebuild from backup plus both original streams, or a pre-authorized temporary pairwise path with new credentials; document RTO and prove it in staging. |
| T-127 | P2 | Large parity and recovery scans can contend with production queries and vacuum/maintenance; current quick parity already limits rows for practicality. | Safety checks themselves degrade the active WebApp or never finish within RTO. | Use chunked snapshots/range hashes, rate controls, read replicas where justified, progress checkpoints, and measured database impact. |
| T-128 | P2 | Repeated event replay, delivery receipts, manifests, and audit dimensions can create high-cardinality logs/metrics and expensive storage. | Observability becomes unaffordable or is disabled during the incident where it is needed. | Separate bounded metrics from searchable structured logs, set retention/sampling rules that preserve financial evidence, and capacity-test telemetry. |
| T-129 | P2 | A legacy notification item is handled before the normal protocol/registry/watermark pipeline and does not have the same durable destination receipt semantics. | Network retry can send duplicate Telegram notifications or lose an error from recovery evidence. | Remove the legacy path or place it behind the standard authenticated, idempotent, destination-ledger side-effect mechanism. |

### 34.4 Non-technical, organizational, and provider challenges

These are not secondary to the implementation. Several are P0 because software cannot infer business risk tolerance or safely choose who is allowed to promote a financial writer.

| ID | Priority | Challenge | Risk if unresolved | Required closure evidence |
|---|---|---|---|---|
| N-001 | P0 | The owner has approved asynchronous cross-site delivery and rejected cross-site ACK on the user-success path, but exact RPO, RTO, maximum promotion lag, same-region durability, tolerated WebApp write freeze, and maximum failback delay are not approved per data class. The current daily-backup RPO/RTO is not the same as DR service objectives. | Stakeholders may interpret local success as zero-loss durability, while engineering cannot choose replica, storage, promotion, or degraded-mode semantics for the remaining residual-loss window. | Owner-approved objectives for financial rows, users, Messenger text, files, sessions, notifications, and analytics, including accepted residual loss and whether local database/WAL replication is mandatory. |
| N-002 | P0 | Promotion and failback authority/RACI is undefined. It is unclear who may declare an outage, mint a writer epoch, change Arvan, accept degraded writes, approve conflicts, or abort recovery. | Conflicting operator actions can create the same split brain that technical controls are intended to prevent. | Named primary/backup roles, least-privilege access, command checklist, escalation path, and two-person approval for high-risk financial handoffs. |
| N-003 | P0 | The boundary between automatic detection, automatic traffic switching, automatic writer promotion, and manual approval is undecided. | Excessive automation can promote on a false positive; excessive manual dependence can miss RTO or be unavailable during a national outage. | A signed state-transition policy stating which transitions are automatic, recommendations, or human-authorized, with timeout and fail-closed behavior. |
| N-004 | P0 | Product behavior for ambiguous ownership, quota exhaustion, identity conflicts, reauthentication, partial Messenger/file availability, and unresolved recovery conflicts requires explicit business approval. | Engineers may silently select data-loss or user-impact behavior that contradicts commercial rules. | Decision records with owner, approved UX, financial consequence, support script, and testable acceptance criteria for every ambiguity. |
| N-005 | P1 | Arvan plan features, active-passive semantics, health probes, API availability inside Iran, TTL/cache purge behavior, support SLA, and emergency access are not contractually verified. | The architecture depends on a provider capability that may behave differently during a nationwide event or require unavailable control-plane access. | Written provider confirmation where possible, sandbox test results, support/escalation contacts, account access drill, and a documented manual fallback. |
| N-006 | P1 | The future Iranian hosting provider, server IP, storage durability, replacement SLA, remote console, network limits, and backup options are undecided. | The standby may lack the capacity or recovery access needed when it becomes the only WebApp origin. | Provider selection record, capacity/SLA evidence, access separation, replacement and data-recovery procedure, and cost approval. |
| N-007 | P1 | Arvan becomes the public ingress control point and therefore a vendor/control-plane dependency even though two origins exist. | CDN outage, account lockout, misconfiguration, or compromise can make both healthy origins unavailable. | Break-glass access, configuration backup/export, least-privilege/MFA, change audit, origin emergency-access decision, and a tested provider-failure contingency. |
| N-008 | P1 | Data residency, privacy, retention, and cross-border replication rules for identity data, chats, attachments, audit logs, and phone numbers are not documented. | Replicating WebApp state between Iran and Finland may create legal, contractual, or customer-trust exposure. | Data-classification and legal/privacy review, approved residency flows, retention/deletion policy, encryption requirements, and user-facing disclosures if required. |
| N-009 | P1 | Secret custody for HMAC/mTLS, JWT, database, backup, Arvan, DNS, SMS, VAPID, and provider accounts is not assigned across three sites. | One credential compromises every link, rotation causes outage, or the incident team cannot access required secrets. | Secret owner, per-site scope, vault/offline escrow, dual-control break glass, rotation/revocation drill, and evidence that secrets are absent from artifacts/logs. |
| N-010 | P1 | Cost approval is absent for the third server, storage growth, Iran-local redundancy, bandwidth, CDN, immutable backups, logs, monitoring, drills, and operational staffing. | Safety features are later reduced or retention/capacity silently undersized. | A capacity-based budget with normal, long-outage, recovery-burst, and growth scenarios plus an owner-approved reserve. |
| N-011 | P1 | A 24x7 incident response and communications plan is not defined. National outages may also disrupt the team's normal communication and remote-access channels. | Correct automation exists but nobody can authorize, observe, or explain the service state. | On-call roster, Iran-available and international communication paths, incident commander role, status templates, access test, and periodic exercises. |
| N-012 | P1 | Operator training and recurring drills are not scheduled. Promotion/recovery contains high-risk, infrequent steps. | Runbook steps are executed out of order or evidence gates are bypassed under pressure. | Training completion, quarterly or agreed-frequency game days, measured RTO/RPO, action-item ownership, and expiry dates for drill evidence. |
| N-013 | P1 | Release/change governance for a three-site mixed-version system is undefined, including freezes during incidents and who may override a failed readiness gate. | A routine deploy changes schema/protocol while the sites are partitioned and makes later recovery impossible. | Change calendar, compatibility checklist, incident freeze rules, override authority, immutable release inventory, and rollback decision tree. |
| N-014 | P1 | User-facing degraded-mode expectations are not finalized: read-only mode, queued action, retry, re-login, missing attachment, delayed Telegram reflection, or recovery banner. | Technically safe fail-closed behavior is perceived as random data loss, causing duplicate user actions and support load. | Approved Persian UX copy and state model, idempotent retry guidance, support FAQ, and analytics for affected actions; copy still requires owner approval before implementation. |
| N-015 | P1 | The statement that the Bot remains active can be confused with Iranian users being able to reach Telegram. The architecture controls Bot-FI service continuity but not Telegram reachability from every Iranian network. | Availability promises exceed the system boundary and incident reports misclassify an external access failure as Bot failure. | A precise service-level definition separating Bot process/API health, Telegram delivery health, and end-user network reachability. |
| N-016 | P1 | DPI behavior and the traffic threshold that caused prior blocking are opaque. There is no approved cross-border traffic budget or test/deploy policy. | Normal sync, large recovery, backups, staging tests, or deployments can again trigger throttling or blocking. | Per-link byte/request telemetry, traffic classes and budgets, throttled recovery, maintenance policy, anomaly alerting, and incident evidence retention. |
| N-017 | P1 | External suppliers such as Telegram, SMS.ir, Arvan object storage, DNS/CA, push services, and time sources have no consolidated outage/SLA/contact matrix. | A dependency failure blocks authentication or delivery while the team incorrectly initiates WebApp failover. | Vendor inventory with locality, owner, credentials, support path, retry/fallback policy, status source, and dependency-specific incident runbook. |
| N-018 | P1 | Financial conflict quarantine and manual reconciliation ownership are undefined. | Conflicts remain indefinitely, or an operator makes an unaudited choice that changes money or trade history. | Restricted reconciliation role, four-eyes approval, immutable before/after evidence, allowed actions, customer/support escalation, and closure SLA. |
| N-019 | P1 | Audit evidence requirements for financial disputes, security incidents, and provider actions are not formally set. | The system converges but cannot prove who promoted, changed routing, replayed data, or resolved a conflict. | Approved audit event catalogue, retention and tamper-evidence policy, clock/sequence interpretation, access controls, and periodic retrieval test. |
| N-020 | P2 | Initial per-link latency, five-times-capacity, backlog, amplification, and convergence metrics are now defined, but final SLO ownership, evaluation windows, error budgets, escalation, and post-incident review ownership are not. | Metrics exist without accountable action, or provisional targets become permanent despite provider and workload drift. | Assign owners and evaluation windows for lag, loss, duplicate suppression, promotion/failback time, conflicts, user impact, capacity, DPI budget, drill freshness, and mandatory post-incident review. |

### 34.5 Dependency map from implementation stages to challenges

The stages remain ordered by prerequisites. A stage may start exploratory work earlier, but it cannot satisfy its exit criteria while any listed P0 dependency remains open.

| Roadmap stage | Primary challenge dependencies |
|---|---|
| Stage 0 - Architecture contract | T-001, T-004, T-010, T-025, T-030, T-032 through T-034, N-001 through N-004 |
| Stage 1 - Inventory and baseline | T-011 through T-020, T-024, T-026, T-101 through T-120, T-125, N-005 through N-010 |
| Stage 2 - Authority/site separation | T-001 through T-004, T-028, T-107, T-114 |
| Stage 3 - Event and delivery model | T-005 through T-009, T-023, T-027, T-029, T-031, T-113 |
| Stage 4 - Epoch and fencing | T-002 through T-004, T-021, T-023, T-033, N-002, N-003 |
| Stage 5 - Data-policy hardening | T-011 through T-016, T-025, T-028, N-004, N-018 |
| Stage 6 - WebApp DR data/file plane | T-017 through T-020, T-106, T-108, T-109, T-113, T-117 through T-119 |
| Stage 7 - Routing and domains | T-021, T-022, T-030, T-032, T-101, T-111, T-112, T-116, T-118, T-125, N-005, N-007 |
| Stage 8 - Recovery orchestrator | T-006, T-007, T-023 through T-027, T-033, T-034, T-106, T-110, T-113, T-120, T-126, N-018 |
| Stage 9 - Failure matrix | T-121 through T-124 plus every P0 safety invariant |
| Stage 10 - Three-site drill | T-030, T-101 through T-126, N-002, N-003, N-011 through N-017 |
| Stage 11 - Production readiness | All P0 and P1 items, or a written owner-approved residual-risk exception that does not violate a P0 safety invariant |

### 34.6 Mandatory pre-implementation decision records

Before schema or routing implementation begins, create decision records for at least:

1. Writer-term authority and partition/witness assumptions.
2. Automatic versus manual promotion and failback transitions.
3. RPO/RTO, maximum promotion lag, same-region durability, and residual-loss contract per data class; cross-site ACK on the normal user-success path is already rejected.
4. Canonical user and aggregate identity migration.
5. Counter, quota, and concurrent-limit behavior during partition.
6. WebApp DR registry scope, including Messenger, files, sessions, Redis, and secrets.
7. Pairwise transport identity, key rotation, replay protection, and physical sync domains.
8. Arvan control mechanism, cache/health semantics, and provider-failure fallback.
9. Recovery barrier and treatment of writes arriving during final convergence.
10. User-visible degraded behavior and reauthentication contract.
11. Event/tombstone/blob retention and restored-site rejoin behavior.
12. Audit, reconciliation, and four-eyes approval requirements.
13. Multi-row transaction envelope and invariant-safe receiver apply.
14. Active-authority command routing during recovery and hub loss.
15. Clock/time-source, certificate, and Iran-offline artifact strategy.
16. Replication performance contract: request-to-event amplification, five-times capacity gate, batch/concurrency tuning, latency SLO evaluation windows, DPI budget, and recovery non-starvation.

Each decision record must list rejected alternatives, failure assumptions, residual risk, rollback impact, owner, and the roadmap stages it unlocks.

### 34.7 Production stop conditions

Any of the following must block promotion or immediately stop failback:

- writer ownership or epoch cannot be proven;
- two WebApp sites report active writer status;
- a stale-epoch write is accepted anywhere;
- protocol, registry, schema, release, certificate, or clock compatibility is unknown;
- a required stream has a sequence gap;
- standby age or contiguous checkpoint lag exceeds the approved per-data-class promotion RPO;
- the same event or aggregate version has different canonical hashes;
- any critical destination delivery is missing or irrecoverably failed;
- a multi-row command is only partially applied;
- terminal offer or trade invariants fail;
- user identity is ambiguous or unresolved;
- critical counter or quota reconciliation is incomplete;
- database parity is truncated, stale, or based on unstable identity;
- a required Messenger file or blob is missing or corrupt;
- recovery state cannot resume deterministically;
- background jobs or startup mutations are not fenced on the intended writer;
- active-origin readiness is false;
- Arvan routing state and application writer state disagree;
- the stability window has not passed;
- backup or rollback evidence is stale or unavailable;
- current workload amplification or directed-link capacity evidence is absent, stale, or below the approved five-times gate;
- a link is reported healthy while its durable backlog continues to grow;
- an operator lacks the authority or evidence required by the RACI.

These stop conditions are safety controls, not advisory alerts. Bypassing one requires an explicit owner decision only where the condition is not a P0 invariant; single-writer, terminal-state, identity, and acknowledged-data integrity controls are not bypassable.

## 35. Post-Merge Publication Record

Completed on 2026-07-14 after explicit owner approval:

1. Verified the implementation branch and clean tracked worktree before work.
2. Confirmed the feature branch was created directly from `main` at `04fef2a5`.
3. Refreshed the roadmap through the Arvan Basic and local-fencing evidence in
   sections 36 and 37.
4. Moved the roadmap to its final `docs/` path after explicit owner approval.
5. Added active-document references without claiming that the live topology is
   already production-ready.
6. Retained the production stop conditions and explicit unresolved witness,
   journal, recovery, capacity, and operational gates.

## 36. Arvan Basic-Plan Controlled-Origin Validation - 2026-07-14

This section records live evidence gathered after the owner rejected the
Professional CDN plan cost and approved the lower-cost controlled-origin path.
It narrows the provider uncertainty but does not satisfy any application-level
promotion, fencing, synchronization, or recovery gate.

### 36.1 Cost and mechanism decision

- Keep the Arvan CDN domain on the Basic plan for the current implementation
  and validation stages.
- Do not use Arvan Load Balancer as the WebApp writer decision-maker.
- Use one proxied public `app` A record and change only its origin IP through
  the Arvan API after the project control plane has separately proved writer
  ownership and `origin-ready`.
- Keep the provider operation as a routing action. It must never mint a writer
  epoch, infer synchronization completeness, or promote a database.
- Growth is not an accepted substitute: it reduces neither the project-level
  fencing requirement nor the need for truthful active-origin readiness.
- Professional may be reconsidered later as an operational convenience, not as
  a prerequisite or a replacement for application safety.

### 36.2 Live configuration baseline

The following changes were made only on the test domain `gold-trading.ir`:

- `app.gold-trading.ir` is proxied by Arvan and remains pointed at WebApp-FI
  `65.109.220.59`;
- edge SSL is enabled with minimum TLS 1.2 and SNI verification;
- HTTP-to-HTTPS redirect is enabled;
- global dynamic caching is disabled pending narrower static-asset page rules;
- HSTS remains disabled until the complete hostname and rollback matrix passes;
- no Load Balancer or Arvan health check exists;
- no public request was routed to WebApp-IR during this validation.

The production root is `gold-trade.ir`; it is outside this validation scope.
A read-only API check on 2026-07-19 returned only `gold-trading.ir` as an active
CDN domain. It also confirmed both `app` and `switch-test` remained proxied over
HTTPS to WebApp-FI `65.109.220.59`. No mutation was performed. The current
origin-switch implementation now rejects every applied change whose root is
not exactly `gold-trading.ir`, before contacting the API. Unlocking
`gold-trade.ir` requires a later reviewed code/config change after Full Matrix,
explicit operator authorization, and a tested rollback/stability procedure.

The pre-change and post-change API snapshots are retained under the ignored
local path `tmp/arvan-cdn-snapshots/20260714T191952Z/`. The API token remains in
an ignored owner-only file and must be rotated because it was previously shared
through the conversation channel.

### 36.3 Isolated switch drill

An isolated proxied record, `switch-test.gold-trading.ir`, was created so that
provider behavior could be measured without changing `app` or user traffic.
The safe operator primitive is implemented on branch
`feature/arvan-controlled-origin-failover` in
`scripts/arvan_origin_switch.py`. It is dry-run by default and an apply requires:

- exactly one matching proxied A record;
- HTTPS edge-to-origin;
- the current origin IP to equal an explicit expected value;
- an exact domain/record/target confirmation phrase;
- post-update API read-back proving the target IP;
- owner-only token-file permissions.

Eight focused unit tests cover dry-run behavior, stale-current-origin rejection,
confirmation rejection, idempotence, proxy/HTTPS preservation, read-back,
token-file permissions, and owner-only mutation audit output.

Observed drill timeline, all in UTC:

| Event | Time/result |
|---|---|
| Authenticated API dry-run from WebApp-IR | succeeded; the token was held temporarily in `/run` with mode `600` |
| API update WebApp-FI to WebApp-IR | 20:17:21.658 through 20:17:37.627; API read-back confirmed `87.236.212.194` |
| First recorded Iran-side request after update | 20:17:50.899; Arvan returned origin `502` in 0.387 seconds |
| Outside-Iran requests after update | four recorded probes timed out at 4 seconds while Iran-side probes returned `502` |
| API rollback WebApp-IR to WebApp-FI | 20:19:07.476 through 20:19:13.777; API read-back confirmed `65.109.220.59` |
| First recorded two-sided request after rollback | 20:19:28.191; outside and Iran both returned `200` |
| Post-drill state | `switch-test` and `app` both point to WebApp-FI; temporary Iran-side API token removed |

These are observation bounds, not propagation percentiles: polling started
after each API operation completed. A later high-frequency multi-vantage drill
must measure first-change time, mixed-PoP duration, p50/p95/p99, WebSocket
behavior, and rollback under repeated trials.

### 36.4 Iran-origin findings and stop decision

WebApp-IR `87.236.212.194` currently returns `502` because Nginx is running but
the production application and sync-worker containers received a graceful
`SIGTERM` on 2026-07-13 and remain stopped. The database and Redis containers
are still running. The last application evidence also showed old unsent change
rows and sync delivery errors, while the deployed release files are older than
the current project state.

The Iran application was intentionally not restarted. Starting it as ordinary
`SERVER_MODE=iran` would not create a safe standby: current code does not yet
separate logical WebApp authority from physical site/runtime role, fence
background/startup mutations, or prove the distinct WebApp-to-WebApp DR stream.

The Iran host did reach `napi.arvancloud.ir` directly; an unauthenticated probe
returned HTTP 401 in approximately 0.24 seconds, and authenticated dry-run plus
both isolated mutations succeeded. This proves present-day control-plane access
from that host, but not availability during national isolation.

### 36.5 Updated gates

The Basic-plan controlled-origin mechanism is technically feasible and becomes
the accepted provider-control candidate. The following remain blocking:

1. Implement physical-site/runtime-role separation before starting WebApp-IR as
   a standby.
2. Implement persistent writer epoch and fail-closed fencing before any public
   promotion.
3. Implement distinct WebApp-FI to WebApp-IR delivery/receipt/checkpoint policy.
4. Implement `/health/live`, `/health/ready`, `/health/sync`, and fail-closed
   `/health/origin-ready`; `/api/config` is not a routing health check.
5. Require a durable audit record and a signed/manual break-glass procedure for
   every Arvan mutation.
6. Measure Arvan propagation repeatedly from Iran and outside Iran, including
   mixed-PoP and long-lived WebSocket behavior.
7. Repeat control-plane access testing during a real or provider-supported
   national-isolation simulation.
8. Keep `app` on WebApp-FI until all production stop conditions in section 34.7
   pass together.

## 37. Local Writer-Fencing Foundation - 2026-07-14

This section records the implementation immediately following the Arvan Basic
validation. It partially advances stages 2 and 4, but it is not a completed
three-site promotion system and does not authorize starting or routing public
traffic to WebApp-IR.

### 37.1 Implemented contracts

- Runtime identity now separates legacy business routing from deployment
  identity:
  - `SERVER_MODE=foreign`, `LOGICAL_AUTHORITY=foreign`, `PHYSICAL_SITE=bot_fi`;
  - `SERVER_MODE=iran`, `LOGICAL_AUTHORITY=webapp`,
    `PHYSICAL_SITE=webapp_fi` for the current WebApp origin;
  - the physical Iran standby uses the explicit `webapp_ir` overlay.
- Invalid server modes and impossible authority/site combinations fail at
  process initialization. Legacy two-value configuration remains compatible,
  but an applied writer transition requires explicit `PHYSICAL_SITE`.
- The additive migration `d1c6e7f8a9b0` creates one durable local
  `webapp_writer_state` row and an append-oriented transition audit. It
  bootstraps `webapp_fi`, epoch `1`, as active so the migration does not
  silently demote the existing production WebApp.
- `webapp_writer_state` and `webapp_writer_transitions` are classified as local
  internal bookkeeping and are prohibited from ordinary product-table sync.
- Explicit `fence`, `activate`, and `approve` transitions use a row lock,
  exact expected epoch/site compare-and-set checks, named operator, reason,
  transition UUID, and readiness-evidence hash. Activation is allowed only
  from a locally fenced state and increments the epoch.
- Readiness evidence is rejected when the target site or epoch differs, any
  mandatory gate is not exactly true, timestamps are missing/naive/future,
  evidence is stale/expired, or its lifetime exceeds the configured maximum.
- Unsafe WebApp `/api/*` methods are rejected before router execution unless
  the local site is the durable active writer. Sync-receiver projection paths
  remain exempt so a standby can converge.
- A SQLAlchemy `before_commit` guard re-reads the durable active site, epoch,
  state, and transition under a PostgreSQL shared lock. A transaction that
  began under an older term cannot commit after a fencing transition obtains
  the exclusive row lock.
- Authoritative startup mutations and background jobs run only on the local
  active WebApp writer. Local runtime work such as sync delivery, connectivity
  observation, and local session cleanup can continue on standby. The leader
  loop restarts its job set when writer state changes.
- Health surfaces now distinguish liveness, local dependency readiness, sync
  health, and active-origin eligibility. `/health/origin-ready` is hidden from
  unauthorized public callers and fails closed unless the local site is the
  approved active writer with fresh evidence, exact release/schema identity,
  database/Redis readiness, background-job readiness, and frontend assets.
- The writer transition CLI is dry-run-first, requires exact current state,
  exact confirmation, explicit site identity, evidence where applicable,
  operator/reason, a transaction, and post-commit read-back.
- Production runtime rendering now emits explicit logical/physical identity,
  the exact release SHA, and the current Alembic head. The origin-readiness
  secret is rendered only into the WebApp runtime. A separate identity overlay
  documents the future `webapp_ir` runtime without turning it on.

### 37.2 Verification evidence

The following checks passed on the feature branch without production deploy or
public CDN mutation:

| Evidence | Result |
|---|---|
| Focused Python unit/integration-adjacent suite | 73 tests passed, covering runtime identity, state/evidence transitions, HTTP fencing, origin readiness, background authority, runtime env rendering, migration graph, Arvan primitive, startup behavior, and sync-registry coverage |
| Deployment-surface, resource-profile, and observability suite | 48 tests passed, including Dockerfile build checks and Nginx syntax validation outside the restricted sandbox |
| PostgreSQL scratch migration from empty database to head | passed; revision `d1c6e7f8a9b0`, active row `webapp_fi/1/active`, and bootstrap audit row verified |
| Scratch downgrade to `d0b5e6f7a8c9` | passed; both writer-control tables were absent after downgrade |
| Scratch re-upgrade to head | passed |
| Real PostgreSQL stale-term commit test | passed; after the control row changed to fenced, a transaction holding the earlier writer context raised `WriterFenceError` at commit |
| Static/syntax checks | Python compile, shell syntax, Alembic head load, CLI direct execution, and `git diff --check` passed |

An older sync-router test group remains red under the checkout's current
`REGISTRATION_SYNC_V2_ENABLED=true` environment because its fixtures omit the
now-required source metadata and some fake apply paths raise unrelated type
errors. Those failures reproduce when that group runs alone, and this phase did
not change its receiver logic. They remain baseline debt and must not be
misreported as writer-fencing coverage.

### 37.3 Explicit remaining safety boundary

This implementation proves only **local durable fencing**. Each WebApp site has
an independent database, so its local control row alone cannot prove that the
other partition has stopped writing. The following therefore remain blocking:

1. Deploy the accepted witness/lease contract as a dedicated Iran-reachable,
   least-privilege service and prove it against independent site databases;
   the source-only state machine is not production ownership proof.
2. Bind every WebApp-authoritative command/event to the writer epoch and reject
   stale epochs at every destination, not only at the local SQL commit boundary.
3. Complete the route and WebSocket mutation audit, including work that can
   outlive the HTTP request context.
4. Build the distinct WebApp-FI to WebApp-IR journal, receipt, checkpoint,
   parity, file, and recovery barrier required by stages 3, 5, 6, and 8.
5. Generate readiness evidence from measured checks rather than operator-entered
   booleans, sign it, and bind it to release/config/site/epoch/checkpoint state.
6. Build the higher-level orchestrator that orders old-writer fencing, witness
   proof, new-site activation, origin readiness, Arvan mutation, read-back,
   stability window, and rollback. The low-level Arvan tool must remain
   independent until this proof exists.
7. Prove that existing long-lived connections drain from the demoted origin and
   that all write surfaces reject the old term.
8. Do not migrate production, restart WebApp-IR, approve its readiness, or
   change `app.gold-trading.ir` on the strength of this local foundation alone.

## 38. Writer Witness And Lease Foundation - 2026-07-14

### 38.1 Accepted contract

`docs/ADR_THREE_SITE_WEBAPP_WRITER_WITNESS_LEASE_20260714.md` resolves the
architecture part of T-004 with an Iran-reachable durable witness, monotonic
writer epochs, 180-second signed leases, 30-second renewal, a 15-second local
safety margin, and a 5-second maximum clock-offset gate. The witness database
clock decides expiry. A new site cannot acquire until the old lease expires,
and `drain` blocks renewal without pretending to revoke a proof already cached
by the old writer.

This chooses safety under ambiguity: if FI can still renew its Iran witness
lease, IR cannot promote merely because it cannot reach FI. If the witness is
unavailable, no new term is granted and the current writer stops before its
locally safe deadline.

### 38.2 Source delivered in this slice

- additive migration `d2e7f8a9b0c1` adds local signed-proof evidence plus
  durable witness singleton and replay-safe receipt tables;
- the witness state machine provides exact-request `acquire`, `renew`, and
  `drain`, uses the database clock, increments epoch only on acquisition, and
  returns Ed25519-signed proofs;
- local activation may import a validated global epoch even when that epoch is
  more than one ahead of a site's stale local observation;
- lease refresh updates proof/expiry without changing the local writer
  transition id, preventing routine renewal from rejecting in-flight commits;
- HTTP writes, startup mutations, authoritative background jobs, and the SQL
  commit guard enforce the witness lease when the feature gate is enabled;
- `/health/origin-ready` is ineligible while witness enforcement is disabled
  and reports witness lease identity/expiry when present;
- dry-run-first operator CLIs cover witness transitions and local proof import;
- witness state and receipts are explicitly internal bookkeeping and excluded
  from ordinary product sync.

### 38.3 Verification and remaining boundary

Focused tests cover signature tampering, wrong-site proofs, safety-margin
expiry, acquire/renew/drain behavior, term advancement after expiry, exact
request replay, local lease refresh, SQL commit expiry, origin readiness,
migration graph, runtime rendering, and sync-registry classification.

| Verification evidence | Result |
|---|---|
| Affected Python unit and integration-adjacent suite | 80 passed |
| Deployment-surface and real Nginx syntax suite | 31 passed outside the restricted socket sandbox |
| PostgreSQL writer/witness suite | 3 passed, including concurrent FI/IR acquisition with exactly one winner |
| Empty scratch database upgrade | passed to `d2e7f8a9b0c1`; FI bootstrap writer and vacant epoch-0 witness verified |
| Scratch downgrade and re-upgrade | passed; witness tables disappeared at `d1c6e7f8a9b0`, incompatible `lease_refresh` audit rows were removed, and head reapplied cleanly |
| Static checks | Python compile, shell syntax, Alembic graph, both CLI help paths, and `git diff --check` passed |

The complete checkout suite ran 3,372 tests. With the current host's
registration-v2 flags it retained the previously documented legacy sync
fixture failures. Turning only `REGISTRATION_SYNC_V2_ENABLED` off reduced the
result to 9 failures and 2 errors. None exercised the new witness/lease paths;
the failures included other enabled-registration flag assumptions plus the
restricted Nginx socket check, which passed when rerun outside the sandbox. The
80 affected tests and 31 deployment-surface tests are green, so these baseline
failures are recorded but are not treated as evidence for this feature.

The production gate remains closed. `WRITER_WITNESS_REQUIRED=false` is the
only approved runtime value in this slice. The following are not delivered:

1. a separately deployed authenticated witness process/API;
2. automatic FI renewal and atomic local proof refresh;
3. witness-only database credentials and hardware/secret-store key custody;
4. measured clock-offset and multi-vantage witness health evidence;
5. real three-database concurrent acquisition, delayed-packet, pause, and
   asymmetric-partition drills;
6. sync/parity/file gates, automated readiness evidence, or the final Arvan
   orchestration state machine.

Therefore T-004 is architecture-resolved but operationally open, Stage 4 is
not complete, and this commit authorizes no production migration, deploy,
WebApp-IR startup, or CDN mutation.

## 39. Dedicated Witness Transport And Automatic Renewal Source - 2026-07-15

### 39.1 Delivered source boundary

This slice advances T-004 without changing a running environment:

- `writer_witness_app:app` is a separately runnable private FastAPI process;
- its minimal settings do not import the product `core.config`, so the process
  does not require Telegram, JWT, Redis, product database, or WebApp secrets;
- `deploy/writer-witness/001_initial.sql` creates only the witness schema,
  singleton, command receipts, and schema-version marker;
- startup requires explicit `webapp_ir` placement, a distinct database user,
  safe lease timing, a `0600` Ed25519 key file whose public key matches, and
  independent HMAC credentials for `webapp_fi` and `webapp_ir`;
- signed requests bind version, physical site, key id, method, path, exact body
  hash, database-clock-checked timestamp, and stable request id;
- authenticated status and transition endpoints are separate from the public
  WebApp application and expose no browser CORS or API documentation surface;
- successful transitions and state-dependent rejections both persist durable
  one-shot receipts. A rejected acquisition cannot later succeed merely
  because the previous lease expired before a delayed replay arrived;
- the active WebApp background leader can renew through the private client,
  validate the Ed25519 proof, and atomically apply `lease_refresh` locally;
- an ambiguous timeout retries the exact request id, while a partition never
  extends local expiry and therefore converges to the existing fail-closed
  safety deadline;
- origin readiness now rejects witness enforcement when automatic renewal is
  disabled.

The public WebApp process receives only its site's pairwise HMAC client secret
and the Ed25519 public key. The witness signing key and the other site's HMAC
secret belong only to the separate witness process. The operational contract
and stop conditions are in `docs/WRITER_WITNESS_SERVICE_RUNBOOK.md`.

### 39.2 Verification evidence

Focused tests cover exact successful replay, tampered/stale HMAC requests,
site binding, durable negative receipts, delayed rejected acquisition after
expiry, atomic proof import, stable request id after an ambiguous timeout,
partition fail-closed behavior, background authority, process configuration
isolation, origin readiness, and existing writer fencing behavior.

The affected suite completed `95` tests: `91` passed and `4` PostgreSQL tests
were intentionally skipped before the guarded database run. The complete checkout suite
discovered `3,384` tests. Under the host's current registration feature flags
it retained the known legacy registration/sync fixture failures (`48` failures,
`11` errors, `67` skips). Turning only
`REGISTRATION_SYNC_V2_ENABLED` off reproduced the previously documented
baseline of `9` failures and `2` missing/host-dependent errors; none of those
failures exercised the witness service, authentication, renewal, or fencing
paths.

The source also passes Python compilation, CLI help loading, runtime identity
regression tests, environment rendering tests, sync-registry policy tests, and
`git diff --check`. The dedicated `001` schema was applied to a disposable
guard-named PostgreSQL database and two real PostgreSQL tests passed: concurrent
FI/IR acquisition produced exactly one winner, and a durable rejected command
remained rejected after lease expiry. The scratch database was then removed.
Independent-host drills remain separate gates, and local PostgreSQL/ASGI tests
are not substitutes for those drills.

### 39.3 Remaining production blockers

No witness process, database, key, credential, WebApp renewal flag, origin, or
Arvan setting has been deployed or changed. Keep
`WRITER_WITNESS_REQUIRED=false`,
`WRITER_WITNESS_AUTO_RENEW_ENABLED=false`, and
`WRITER_WITNESS_SERVICE_ENABLED=false` until all of these pass:

1. provision the dedicated database with a migration identity and demonstrate
   the runtime role has no DDL or product-table privileges;
2. deploy the service on a private Iran-reachable TLS/mTLS path with fixed
   ingress policy, process supervision, audit retention, backup, and restore;
3. store/rotate the Ed25519 and pairwise HMAC secrets through approved custody
   without ever mounting the signing key into a WebApp container;
4. measure multi-vantage witness reachability and clock offset, then block
   readiness when either exceeds the accepted bound;
5. run deterministic independent-database concurrent acquisition, asymmetric
   partition, delayed packet, witness restart, database pause, VM pause, clock
   jump, and lost-response tests;
6. finish stale-epoch binding at every command/event/side-effect destination,
   plus sync, parity, file, readiness-evidence, recovery, and Arvan orchestration
   stages;
7. obtain the operator RACI/two-person approval and measured lease/RTO decision.

Therefore the dedicated transport and renewal are source-complete, but T-004
and Stage 4 remain operationally open. This slice authorizes no production
migration, deployment, WebApp-IR startup, witness enablement, or CDN mutation.

## 40. Guarded Four-Database Witness Failure Drill - 2026-07-15

### 40.1 Delivered test boundary

`deploy/writer-witness-drill/docker-compose.yml` now creates four temporary,
non-published PostgreSQL endpoints for Bot-FI, WebApp-FI, WebApp-IR, and the
dedicated witness. The topology uses an internal Docker network, tmpfs database
storage, fixed drill-only credentials, no project `.env`, and guarded database
names. The runner refuses a missing, non-PostgreSQL, non-guarded, or duplicate
database endpoint before issuing destructive bootstrap SQL. The cleanup trap
unpauses and removes only the fixed `writer-witness-drill` Compose project.

The WebApp databases contain only the local writer-control subset required by
the current migration contract. The witness database uses the dedicated `001`
schema. The Bot database contains only an isolation marker. Boundary assertions
prove that the witness tables do not enter either WebApp product database, the
local writer tables do not enter Bot-FI, and no product offer table enters the
witness database.

The production renewal function now accepts explicit session, HTTP, public-key,
and timing dependencies for deterministic isolated testing. Its existing
runtime defaults remain unchanged.

### 40.2 Executed failure matrix

The complete `scripts/run_writer_witness_failure_drill.sh` run passed:

| Scenario | Observed result |
|---|---|
| Simultaneous FI/IR epoch-1 acquisition | exactly one accepted writer and one durable rejection |
| Authenticated IR acquisition while FI lease is live | rejected and stored as a one-shot negative receipt |
| FI renewal response lost after witness commit | retryable ambiguity; exact request replayed from the durable receipt |
| Witness service recreation before the retry | epoch, lease, and committed response survived recreation |
| Automatic FI renewal/import | local expiry advanced without changing epoch 1 |
| Asymmetric FI-to-witness partition | local proof did not advance; FI became ineligible at the 15-second safety deadline |
| Delayed replay of the earlier rejected IR request after expiry | remained rejected and created no second receipt |
| Fresh IR acquisition after actual FI expiry | witness granted epoch 2 and local IR activation accepted the signed proof |
| Post-failover eligibility | FI was ineligible and only WebApp-IR epoch 2 was eligible |
| Real pause of the witness PostgreSQL container | renewal could not complete, local lease stayed unchanged, and IR failed closed at its safety deadline |
| Witness PostgreSQL resume | holder, epoch, and negative receipts persisted; same-term IR renewal restored eligibility |
| Bot-FI isolation | its marker remained unchanged across the entire matrix |

Python compilation, Compose configuration validation, and `git diff --check`
passed. The focused writer/fencing/runtime suite completed 100 tests: 96 passed
and four guarded PostgreSQL tests skipped because their separate scratch URL
was not explicitly supplied. The four-database drill above independently used
its own real temporary PostgreSQL containers.

### 40.3 What this evidence does not authorize

This closes the deterministic one-host, independent-database subset of T-004,
T-103, and T-121. Containers on one Docker host are not independent machines,
availability zones, clocks, storage devices, or networks. The test clock is
advanced deterministically for lease boundaries; it is not evidence that
production NTP offset is within five seconds.

The remaining gates include private TLS/mTLS deployment, least-privilege DB
roles and secret custody, real multi-vantage clock measurement, directional
host-level network faults, witness process/VM pause and host loss, disk-full and
clock-jump behavior, stale-epoch binding across all business effects, complete
sync/parity/file recovery, signed readiness evidence, and controlled Arvan
orchestration. Production witness flags, migrations, services, WebApp-IR, and
CDN settings remain unchanged and disabled.

## 41. Dedicated Iran-Reachable Dark Witness Deployment - 2026-07-15

### 41.1 Deployed boundary

The replacement VPS at `185.231.182.6` passed the network condition that the
previous candidate could not satisfy: both WebApp-FI and WebApp-IR can reach it
directly. A repeatable deployment layer now provisions a dedicated PostgreSQL
16 database, least-privilege runtime identity, loopback-only single-worker
Uvicorn process, private TLS Nginx endpoint, fixed-source UFW policy, key-only
SSH, checksumed daily backups, and a destructive-isolated restore drill.

The service is intentionally dark. Its singleton remains
`webapp | no holder | epoch 0 | vacant`. No client credential or CA was added
to a WebApp runtime, no `WRITER_WITNESS_REQUIRED` or automatic-renewal flag was
enabled, no product migration ran, WebApp-IR remains stopped, and no Arvan
record or origin policy changed.

The deployment is reproducible from manifest-bound minimal source and a fully
locked CPython 3.12 wheelhouse. The wheelhouse became operationally necessary
when the Iran PyPI path returned no usable version for the pinned
`cryptography` dependency. A multi-file transfer also arrived incomplete; both
failures stopped before package/service activation because archive and
per-wheel checksums are mandatory. The successful path used one checksumed
archive and verified all 44 wheels on the destination before installation.

### 41.2 Real-host evidence

| Gate | Observed evidence |
|---|---|
| WebApp-FI to Witness TLS | `200`; Python 3.14 strict certificate validation passed after explicit CA/key-usage rotation |
| WebApp-IR to Witness TLS | `200`; private CA and IP SAN validation passed |
| Fixed ingress | FI and IR allowed; non-allowlisted source timed out on `443` |
| HMAC separation | independent FI and IR signed read-only status calls returned `200`; unsigned calls returned `401` |
| Clock safety | all three hosts reported synchronized NTP; measured offsets were about `-0.55s` and `-0.50s`, within the five-second bound |
| Runtime isolation | PostgreSQL and Uvicorn listen only on loopback; Nginx is the sole `443` listener |
| Database privilege | runtime role has no superuser, database-create, role-create, replication, or DDL authority |
| Backup/restore | multiple checksumed backups created during drills; sampled restores reproduced schema `001` and vacant singleton |
| Process restart | ready state returned and durable data remained unchanged |
| Real VM reboot | PostgreSQL, Nginx, Witness, UFW, and backup timer returned active; state and backups persisted |
| Secret boundary | signing key and pairwise HMAC credentials remained on Witness; no credential entered Git or a WebApp runtime |

### 41.3 Remaining activation gates

This closes the dedicated-host provisioning, private TLS, least-privilege,
multi-vantage reachability/clock, process restart, VM reboot, and basic
backup/restore portions of T-004. It does not authorize the first writer lease
or failover.

Before enabling either WebApp witness flag, the project still needs:

1. an approved pairwise credential delivery and rotation/revocation procedure;
2. a full replacement-host rebuild followed by restore from the now-verified
   encrypted off-host backup;
3. real-host concurrent acquire, lost-response, delayed-packet, asymmetric
   FI/IR partition, Witness process/DB/VM pause, disk-full, and clock-jump drills;
4. a measured lease/RTO decision and two-person production transition policy;
5. immutable writer epoch/site/transition provenance on every authoritative
   event, command, outbox item, replay, and side-effect destination;
6. complete sync lag, parity, file, recovery-barrier, and stable-connectivity
   gates before switching Arvan;
7. signed readiness evidence and the controlled Arvan state machine.

Until those gates close, the correct runtime state is a healthy dark Witness,
active WebApp-FI as the only WebApp writer, stopped WebApp-IR application
processes, and unchanged CDN routing.

## 42. Immutable Encrypted Witness Backup - 2026-07-15

### 42.1 Selected boundary

Hetzner Object Storage in HEL1 was selected instead of allocating a dedicated
backup VM or adding a 60 GiB Bot-FI volume. The bucket is private, versioned,
and created with default 90-day `COMPLIANCE` Object Lock. This choice is a
backup dependency only; it does not participate in writer election, request
routing, WebApp synchronization, or Arvan failover.

The published base cost at implementation time is `EUR 4.99/month` excluding
VAT (`USD 5.99` for USD billing), charged hourly while at least one Bucket is
active and capped at the monthly price. It includes up to 1 TB storage and
1 TB egress at full-month usage; ingress and S3 operations are free. The
current Witness objects are negligible relative to that quota, so the expected
steady-state incremental cost is the base price and no Bot-FI volume was
created. Pricing source: <https://www.hetzner.com/pressroom/object-storage/>.

Two distinct S3 credentials are used. The admin credential and `age` decryption
identity remain off the Witness. The credential copied to the Witness can only
perform `s3:PutObject` under `witness/`; explicit policy denies object listing,
read, delete, ACL, retention, lifecycle, policy, and bucket-control operations.
The policy was tested with real requests, not inferred from a successful policy
upload.

The Witness creates its ordinary checksumed custom-format PostgreSQL dump,
encrypts that file locally to the off-host public `age` recipient, uploads the
ciphertext, and records a root-only idempotency marker only after S3 returns a
version ID. The private decryption key never enters the Witness. If global
connectivity is unavailable, the local 30-day backup remains and the off-host
timer retries after connectivity returns.

### 42.2 Executed evidence

| Gate | Observed result |
|---|---|
| Bucket region and privacy | HEL1; private ACL; no anonymous or authenticated-public grant |
| Immutability | versioning enabled; default and uploaded-object retention are `COMPLIANCE` for 90 days |
| Uploader least privilege | upload under `witness/` succeeded; list, read, and delete were denied |
| Credential separation | admin and uploader keys are distinct; only uploader key is on Witness |
| Decryption custody | zero `AGE-SECRET-KEY` files under Witness configuration; recipient only |
| Live upload | `witness/writer-witness-20260715T114726Z.dump.age`, 13,711 bytes, version ID returned |
| Idempotency | immediate second service run reported `already_uploaded` and created no new version |
| Object retention | locked until `2026-10-13T14:11:36.178523941Z` |
| End-to-end restore | S3 checksum and retention verified, `age` authenticated, temporary restore passed with schema `001`, state `webapp:0:vacant`, zero receipts |
| Runtime safety | Writer Witness stayed dark; epoch `0`; no WebApp flag, product process, CDN, or Arvan setting changed |

Focused Writer Witness deployment/service tests passed before deployment. The
Object Storage provisioning and restore helpers redact credentials and fail
closed on an unexpected endpoint, region, bucket/key path, missing version ID,
checksum mismatch, absent retention, unsafe file mode, or excessive object
size.

### 42.3 Remaining boundary

Encrypted off-host custody, data-level restore, and clean replacement-host live
restore are now proven. The immutable object was restored through a separately
validated candidate database, swapped into the live name while preserving the
fresh database as a no-connection rollback, and survived a real VM reboot.

This does not authorize a lease. Authenticated replacement-host reachability
was repeated from both WebApp-FI and WebApp-IR. The earlier WebApp-IR operator
failure was a default-port assumption; the host accepts operator SSH on port
`37067`. Direct TCP `443`, private-CA TLS, HMAC authentication, rejection of an
unsigned request, and UTC/NTP clock alignment were then proven from WebApp-IR.
Independent escrow access, real fault injection, and the decision to retain or
retire either dark Witness host remain operational gates. Pairwise HMAC
rotation/revocation was subsequently proven from both WebApp sites without
delivering a credential to either WebApp runtime.

## 43. Replacement Witness VPS Provisioning Gate - 2026-07-15

### 43.1 Approved immutable request

The clean-host recovery drill is approved for one temporary Arvan Abrak in
`ir-thr-fr1`: Ubuntu 24.04, plan `eco-2-2-0`, 2 vCPU, 2 GiB RAM, and 30 GiB
disk. The live API quote at approval time was IRR 16,220 per hour and IRR
11,679,000 per month. The host is drill-only and must not receive a writer
lease, production WebApp flags, a CDN origin, or public application traffic.

Provisioning is dry-run first and idempotent by fixed server name. The bootstrap
delivers only the operator Ed25519 public key, disables password and interactive
SSH authentication, permits SSH only from Bot-FI/control, and permits HTTPS
only from WebApp-FI and WebApp-IR. Any API-generated bootstrap password is kept
outside the repository in an owner-only state file and is never printed.

### 43.2 Initial API block and manual resolution

The final preflight revalidated the region, quota, plan, image, default network,
absence of an existing same-name server, and the API quote. The live create
request was then rejected by Arvan with HTTP `403` because the current token
lacks create permission. A post-attempt read-back again found no same-name
server. Therefore that API operation created no VPS, Security Group, SSH key,
or billable resource.

The same token also cannot create a dedicated Security Group or manage panel
SSH keys. The prepared fallback can add a deny-by-default host firewall during
first boot, but using Arvan's broad default Security Group would reduce defense
in depth and is accepted only as a temporary drill fallback after server-create
authority exists. It is not the final production boundary.

The operator then created a VPS manually and supplied `185.206.95.94`. Live
host inspection confirmed Ubuntu 24.04, 2 vCPU, about 2 GiB RAM, and a 30 GiB
disk. The existing read-only token did not return this IP from its visible
server lists, so the provider region, resource-group ownership, and actual
invoice cannot be independently asserted through that token. The earlier API
quote is retained as planning evidence, not claimed as the verified bill for
the manually created host.

### 43.3 Mandatory API-key rotation and least-privilege closure

The current Arvan API key must be replaced; this is scheduled work, not waived
work. Rotation may be performed after the higher-priority recovery drill is
unblocked, but it must complete before any production CDN failover automation
or Witness activation. The replacement process is:

1. create a dedicated operator/machine identity instead of reusing the current
   general Arvan token;
2. grant only the exact temporary lifecycle permissions needed to read plans,
   images, networks, quota, servers, and Security Groups and to
   create/read/delete the one recovery server and its dedicated firewall;
3. store the new credential only in owner-only operator custody, never in Git,
   a WebApp container, Writer Witness runtime, logs, or evidence bundles;
4. repeat dry-run, create/read-back, dedicated-firewall verification, injected
   public-key verification, and deletion/revocation drills;
5. revoke the current token after the replacement credential and the existing
   CDN control path have both passed read-back tests;
6. retain only redacted key identifiers, permission manifests, timestamps, and
   pass/fail evidence.

Manual host creation unblocked the recovery drill without expanding the exposed
token's authority. API-key rotation and a dedicated provider-level firewall
remain open; the current token must not be elevated merely to reproduce a host
that now exists. `scripts/provision_arvan_witness_recovery_vps.py` remains a
dry-run-first reference for a future least-privilege lifecycle drill.

## 44. Clean Replacement-Host Live Restore - 2026-07-15

### 44.1 Host and deployment evidence

The supplied bootstrap password was subject to an enforced first-login change.
An operator Ed25519 key was installed and verified before password SSH was
disabled. Root SSH now accepts keys only, and host UFW permits `22/tcp` only
from Bot-FI/control `65.109.216.187`. HTTPS is permitted only from WebApp-FI
`65.109.220.59` and WebApp-IR `87.236.212.194`; a control-host HTTPS attempt
timed out after five seconds.

One checksumed archive delivered a manifest-bound release and 44 fully locked
CPython 3.12 wheels. Both the archive and every wheel were revalidated on the
destination before installation. PostgreSQL 16 and Uvicorn listen only on
loopback; Nginx is the only `443` listener. The final active release is
`20260715T1830Z-ee96f22e-recovery4`, built from commit `ee96f22e`.

The drill exposed and closed two runbook defects before evidence was accepted:

1. SSH was previously allowed broadly by UFW; provisioning now requires a
   validated fixed `WRITER_WITNESS_SSH_SOURCE_IP` and has no generic OpenSSH
   allow rule.
2. The root-only `0600` backup could not be opened directly by `pg_restore`
   running as PostgreSQL. The privileged shell now opens the file and passes
   only its bytes to the unprivileged restore process.

### 44.2 Immutable object and live database recovery

The replacement host restored the compliance-locked object
`witness/writer-witness-20260715T114726Z.dump.age` (13,711 bytes; ciphertext
SHA-256 `d30388b018d0d0b6fa9aed809ec328ed60b0d27ccbb5ff6bb019573178d4688b`;
retained until `2026-10-13T14:11:36.178523941Z`). The `age` identity remained
off the Witness.

The first restore used an isolated temporary database. The live restore then:

1. required explicit `--apply`, exact current-state, expected-state, and receipt
   count guards;
2. restored as the least-privilege migration identity into a candidate database;
3. verified schema `001`, state `webapp:0:vacant`, and zero receipts;
4. stopped only the dark Witness service and swapped database names;
5. preserved the former live database as
   `writer_witness_rollback_20260715181340_16024` with connections disabled;
6. returned the service ready and created a checksumed post-restore backup.

A deliberate wrong-current-state run was rejected before candidate creation or
service stop. Repeating that negative test after its cleanup-path correction
left only the live and disabled rollback databases, with the live state and
service unchanged.

The runtime database role has neither database-create nor superuser, role-create,
or replication authority. The live database and rollback both survived a real
VM reboot; Writer Witness, Nginx, PostgreSQL, UFW, and the backup timer returned
active, and state remained `webapp:0:vacant` with zero receipts.

### 44.3 Network and activation boundary

From WebApp-FI, authenticated TLS/HMAC status returned `200` with epoch `0` and
vacant state; the same endpoint without authentication returned `401`. The
temporary client credential and CA were held only under `/run`, removed after
the smoke test, and were not persisted in WebApp-FI configuration. WebApp-FI
production containers remained healthy. The original Witness at
`185.231.182.6` also remained healthy and unchanged at `webapp:0:vacant`.

WebApp-IR operator SSH is exposed on port `37067`, not the default port `22`.
After using the correct port, the host reported UTC with synchronized NTP and
direct TCP `443` to the replacement Witness succeeded. Its authenticated
private-CA TLS/HMAC status request returned `200` with `webapp:0:vacant`; the
same endpoint without authentication returned `401`. WebApp-IR and Witness
timestamps were within the same second. The temporary client credential,
private CA, and smoke helper existed only under `/run/witness-recovery-smoke`,
were removed by an exit trap, and the replacement IP was not persisted in the
WebApp-IR runtime configuration.

Therefore replacement-host reachability is evidence-complete from both WebApp
sites. No credential was delivered to a WebApp runtime, no Witness lease was
acquired, no WebApp flag was enabled, no old Witness was retired, and no CDN or
Arvan routing setting changed. The pairwise credential rotation/revocation gate
was subsequently closed; the real-host directional fault matrix remains
mandatory before activation.

## 45. Pairwise Witness HMAC Rotation And Revocation - 2026-07-15

### 45.1 Fail-closed procedure

`deploy/writer-witness/writer-witness-rotate-hmac.py` now provides four explicit
phases for one site at a time: `prepare`, external verification, `revoke`, and
`finish`, with `rollback` available until cleanup. It requires root, an exact
operator-supplied expected epoch, a database-confirmed vacant Witness, owner-only
runtime/client files, matching current service/client material, no pre-existing
overlap slot, and a monotonically incremented key generation.

`prepare` retains the old credential in the service's single previous slot,
generates a distinct new secret, atomically changes the root-only runtime and
client material, and restarts/readiness-checks the service. A failed restart
restores the original files. `revoke` removes only the verified previous slot;
a failed restart restores the overlap state. The original implementation put
root-only rollback material under `/run`; Section 47 supersedes that
crash-unsafe choice with persistent, atomically written state. Metadata contains
key IDs but no secrets, and `finish` removes the
rotation state only after revocation or rollback.

The installed helper on the replacement Witness matched the repository SHA-256
`82466be1f1f5a2111b3f4d438ff87a68ae68ceec4c02fb226c01ebb30c56d7d8`.

### 45.2 Executed evidence

| Gate | WebApp-FI | WebApp-IR |
|---|---|---|
| Initial generation | `webapp-fi-v1` | `webapp-ir-v1` |
| Prepared generation | `webapp-fi-v2` | `webapp-ir-v2` |
| Overlap proof | old `200`; new `200` | old `200`; new `200` |
| Post-revocation proof | new `200`; old `401` | new `200`; old `401` |
| Probe source | `65.109.220.59` | `87.236.212.194`, SSH port `37067` |
| Runtime persistence | none | none |
| Temporary material | removed from `/run` and operator host | removed from `/run` and operator host |

Each successful authenticated response reported `webapp:0:vacant`. After both
rotations, the service was active and ready with HTTP `200`, the runtime exposed
only the two `v2` key IDs, no previous slot remained, current client material
matched its service credential, FI and IR secrets remained distinct, the
database stayed `webapp:0:vacant`, and receipt count stayed zero. Values of all
HMAC secrets remained absent from command output, Git, roadmap evidence, and
WebApp runtime configuration.

### 45.3 Activation boundary

This drill changed only root-controlled credentials on the dark replacement
Witness. It acquired no lease, imported no proof, enabled no WebApp enforcement
or renewal flag, started no stopped WebApp-IR application process, changed no
product database, and mutated no CDN or Arvan route. Credential rotation is no
longer a T-004 blocker; the real-host directional fault matrix and the remaining
higher-level architecture gates still block activation.

## 46. Real-Host Matrix Preflight Preparation - 2026-07-16

### 46.1 Branch and scope boundary

The owner explicitly rejected merging this feature branch with `main` in
either direction before the real-host fault campaign. The retained matrix scope
is therefore the independently deployed dark Writer Witness control plane. No
result from this campaign may be described as proof that the feature branch is
integrated with the current production application release.

`scripts/plan_writer_witness_real_host_matrix.py` now provides separate
side-effect-free `plan` and read-only `preflight` modes. The runner is pinned to
`feature/arvan-controlled-origin-failover`, refuses a dirty checkout, records
that no `main` integration is authorized or claimed, and contains no execution
path for Witness transitions, firewall faults, service stops, reboot, disk
pressure, clock changes, WebApp flags, credentials, or Arvan mutations.

The planned campaign contains twelve explicit `RH-*` scenarios covering
concurrent acquisition, lost response, delayed replay, directional FI/IR
partitions, service/database/VM faults, isolated disk-full, clone/time-namespace
clock faults, key rotation during partition, and exact baseline restore. These
are catalog entries only until the clean preflight passes and a separate
fault-injection execution review is complete.

### 46.2 Verified rollback baseline

Replacement Witness `185.206.95.94` is the future matrix target. Original
Witness `185.231.182.6` remains a healthy unchanged reference/rollback host.
Both were observed NTP-synchronized, service-ready, `webapp:0:vacant`, schema
`001`, and at zero receipts. The replacement host retained the disabled
rollback database `writer_witness_rollback_20260715181340_16024`.

Immediately before preparing the harness, a fresh local backup was created on
the replacement host as
`writer-witness-20260716T054228Z.dump`. Its SHA-256 was
`dbf4c637041c576fc1977d0a8bd235a48ee5758b88f8a2d50f963edb1f9b7a1e`.
The checksum passed and an isolated restore-smoke reproduced schema `001`,
state `webapp:0:vacant`, and zero receipts; the live database remained vacant
with zero receipts afterward.

WebApp-FI remained healthy on its current production release with Witness
flags disabled. WebApp-IR application and sync-worker containers remained
stopped while its database stayed healthy. Both sites were NTP-synchronized
and retained direct reachability to the dark replacement Witness on `443`.
No lease, credential delivery, product-process start/stop, database mutation,
or CDN change occurred.

### 46.3 Source baseline and next gate

The focused preflight source baseline passed 96 tests with two guarded
PostgreSQL tests skipped because their separate scratch URL was not supplied.
Five tests cover the new plan/preflight safety contract, read-only command
surface, twelve-scenario catalog, secret-free artifact, and dirty/wrong-branch
fail-closed behavior.

Preparation was committed only on this feature branch as `9a34884c`; no `main`
merge or history rewrite occurred. The read-only preflight then passed from
that exact clean SHA with zero failed checks. The retained artifact records:

- WebApp-FI release `2d7c114d`, healthy application/database/API, disabled
  Witness flags, synchronized NTP, and direct Witness `443` reachability;
- WebApp-IR application and sync worker stopped, database running, disabled
  Witness flags, synchronized NTP, and direct Witness `443` reachability;
- replacement Witness services ready, seven-percent disk usage,
  `webapp:0:vacant`, zero receipts, the fresh backup, and one disabled rollback
  database;
- original Witness services ready, `webapp:0:vacant`, and zero receipts;
- the focused 96-test source gate passing with two guarded PostgreSQL skips.

The artifact secret scan found no password, HMAC/client secret, private key, or
API key assignment. The runner now forces artifact mode `0600`. A passing
preflight still authorizes no RH scenario. The plan now embeds nine immediate
abort conditions and a final success barrier. The historical rollback order
prepared in this section was superseded by the source-level contract in
Sections 47.5 and 47.6; it must not be used to authorize execution. The next
action is implementation and validation of the RH campaign itself, not another
pre-matrix infrastructure mutation.

## 47. External Review Remediation Before the Real-Host Matrix - 2026-07-16

Four independent external reviews agreed that the Writer Witness foundation
could continue, but RH-001 must not start from the catalog-only preflight and
rollback contract. This section supersedes the execution and rollback
conclusions at the end of Section 46; its historical evidence remains intact.

### 47.1 Zero-skip and frozen-source entry gate

Preflight now requires an explicitly supplied exact commit and records a hash
bundle for the controller, ephemeral client, restore, rotation, host-fault
helper, Nginx template, Compose drill, service code, and minimal Witness release
manifest. It rejects a dirty/different checkout or a replacement Witness whose
deployed release manifest differs from the frozen bundle.

The default-skipped PostgreSQL suites moved out of the permissive unittest path.
A dedicated guarded Compose database named
`stage4_registration_writerfence_matrix` receives both local-writer and
dedicated-Witness schemas. The gate requires exactly four tests, fails on any
skip, and is followed by the four-database pause/recovery drill. Its first local
execution passed 108 focused tests, all four PostgreSQL tests with zero skips,
and the complete four-database drill.

Preflight additionally records effective flags from env files and container
environments, certificate fingerprints observed from FI and IR, authenticated
FI/IR status `200`, unsigned `401`, certificate expiry, Nginx/firewall hashes,
complete state-manifest hashes, exact backup checksum, and absence of unfinished
rotation/restore state.

### 47.2 Executable one-scenario controller

`scripts/run_writer_witness_real_host_matrix.py` replaces the inert catalog as
the execution boundary. All RH-001 through RH-012 IDs have concrete handlers.
There is intentionally no all-scenarios mode: one process accepts one exact
scenario, one fresh passing preflight, distinct named operator, abort observer,
and incident commander, one reason, an exact commit, a preflight-bound observer
approval, and matching execution confirmations.

The controller creates a unique `wwm_*` ownership tag, stages pairwise material
only in controller-private temporary storage and WebApp `/run`, hashes every
command, retains mode-`0600` JSONL evidence, and executes cleanup after any
assertion failure. The current exact cleanup order is: stop and join requesters
while isolation remains; retain failure evidence; reconcile any live-restore
journal by PostgreSQL OID; recover HMAC state while the Writer Witness service
is stopped; resume only affected runtime components; remove isolated pressure;
remove tagged network faults; restore the exact vacant backup; and verify both
Witness/WebApp baselines plus absence of hidden resources.

The real-host handlers cover concurrent acquisition, durable positive/negative
replay, directional nft partitions, service/PostgreSQL stop/pause/restart,
replacement-host reboot, isolated tmpfs-backed PostgreSQL disk-full, client
clock-window boundaries, HMAC overlap/revocation, and restore fault injection.

### 47.3 Crash-safe restore and credential rotation

Live restore validates the exact backup checksum and a canonical full-state
manifest, not only epoch/status and receipt count. Before it publishes the
replacement dump, a durable mode-`0600` phase journal records the exact owned
input path and checksum. Recovery deletes only that journal-owned input and
refuses an orphan, foreign file, symlink, hard link, or otherwise ambiguous
input. The same journal records original and candidate PostgreSQL OIDs before
the first rename. Recovery compares live names to OIDs, making a crash before
or after any journal write unambiguous. Prior live state is restored
idempotently, retained candidates are connection-disabled, and
completed/recovered journals are archived.

RH-012 injects failure at twelve prerequisite points: input validation,
candidate creation, candidate restore, candidate validation, grant application,
prepared journal, service stop, current-database disable, current rename,
candidate promotion, candidate enable, and service start. Each failure must
recover the exact pre-attempt manifest before one successful restore to the
pinned epoch-zero manifest.

HMAC rotation state moved to
`/var/lib/trading-bot-witness/hmac-rotation`; secret copies and metadata use
atomic replace plus fsync. Durable request identity now binds to the
authenticated physical site rather than rotating key ID, while key ID remains
in logs. Exact requests can therefore replay across the overlap.

### 47.4 Remaining execution boundary

These changes remove source-level pre-Matrix blockers but do not yet authorize
RH-001. The updated release/helpers must be installed on the dark replacement
Witness, the manifest helper must exist on the reference host, all twelve live
restore failure points must pass on the epoch-zero target, and a new exact-SHA
preflight artifact must pass. No merge with `main`, WebApp writer activation,
product migration, Arvan change, or automatic reference-Witness use is
authorized.

### 47.5 Adversarial pre-Matrix blocker remediation

The second external review round found additional controller-lifecycle,
target-pinning, credential, restore, approval, and scenario-design blockers.
The source boundary now addresses them without merging `main` or enabling a
WebApp writer:

- descriptor-held `flock` ownership prevents a failed competitor from deleting
  another run's lock; a durable local/remote campaign identity is written
  before side effects, signals enter unconditional cleanup, and an exact-commit
  recovery mode reconciles SIGKILL or ambiguous SSH results;
- every HTTP request validates the exact replacement IP, port, CA, and leaf
  fingerprint on the same TLS connection that carries the signed request;
- each campaign receives unique FI/IR HMAC key IDs and secrets, proves the
  original Witness lacks those IDs, keeps copies only on verified tmpfs, and
  recovers persistent rotation phases including lost `prepare`/`revoke`
  responses;
- cleanup discovers resources from the fsynced journal, proves no requester or
  active socket remains, refuses restore while a partition cannot be removed,
  restores the exact credential hash and database manifest, deletes only
  tag-owned auxiliary databases after evidence retention, and reruns the full
  exact-SHA preflight;
- restore journals before candidate creation, covers twelve early/late
  failpoints, rejects liveness-only no-journal recovery, stops the service if
  recovered state validation fails, and fsyncs both archive directories;
- observer and incident commander approvals require different SSH signing
  keys, a distinct named operator, exact reason/change ID, one-time nonce,
  one-time preflight, bounded validity/maintenance window, concrete console,
  communications, restore identity, request ceiling, and enforced conservative
  DPI byte budget;
- RH-001 establishes both pinned TLS sessions before a common release instant
  and retains readiness/send timing; RH-010 combines authentication-window
  boundaries with an isolated PostgreSQL lease/epoch clock-jump probe.

These are source-level remediations, not a claim that the Matrix has passed.
The updated release and helpers must still be deployed to the dark replacement,
all regression/source/live prerequisites must pass at the new exact commit, and
the external reviewers must approve that exact diff before RH-001 starts.

### 47.6 Source-level campaign fencing and host-isolation remediation

The current milestone status is **source-level, pending validation**. This
subsection records the intended fail-closed contract implemented in the feature
worktree; it does not assert a commit, push, deployment, live restore drill,
RH-010 execution, preflight pass, external-review approval, or RH scenario pass.

Controller trust is no longer supplied as a per-command path. A root operator
must run `scripts/provision_writer_witness_matrix_controller.py` with an
observer identity/public-key file and a distinct incident-commander
identity/public-key file. The tool verifies two different public keys and
atomically installs the root-owned mode-`0600` canonical policy at
`/etc/trading-bot-witness-matrix/allowed_signers`; controller state/runtime
directories are root-owned mode `0700`. The two private signing keys remain on
independent operator devices and must never be copied to the controller,
repository, approval bundle, or evidence directory. Scenario execution refuses
an allowed-signers override away from the canonical path.

The replacement Witness campaign claim is a complete owner-only `active.json`
published with no-replace atomicity and directory fsync before side effects. Its
identity is the exact `tag + expected commit + scenario + not_after` tuple.
Claim, authorization consumption, and active mutation fail closed after the
replacement-Witness clock reaches `not_after`, while exact cleanup ownership
and release remain available. Successful release atomically moves that same claim to an exact append-only
`releases/<tag>.json` tombstone and fsyncs both directories. Recovery classifies
exact active, exact released, foreign, absent, and transport-ambiguous states;
generic absence cannot substitute for the exact release tombstone. Before
authorization indexes are published, an immutable intent globally reserves
both the approval nonce and preflight SHA-256. Either value therefore remains
single-use across all campaign tags, including a crash after only part of the
consumption sequence.

The abort path is a single ordered safety contract. Steps 1 through 4 are the
unconditional containment/revocation phase. An ambiguous requester stop is
recorded as critical but cannot prevent restore recovery or transient-HMAC
revocation. Steps 5 through 9 run only after every critical result is clear and
requester absence is proven:

1. Attempt to stop and join all Matrix requesters and inspect their
   sockets/retries while isolation remains.
2. Capture, redact, hash, and retain failure evidence before restoration.
3. Recover any active live restore by PostgreSQL OID and reconcile only its
   exact journal-owned input.
4. Recover HMAC rotation with `--leave-service-stopped`, then prove no owned
   key scope, staging file, claim, or deletion tombstone remains.
5. Resume only affected replacement-Witness runtime components.
6. Remove only Matrix-owned pressure, tmpfs, loop, and clone artifacts.
7. Remove only Matrix-owned firewall and traffic-control objects.
8. Restore the pinned checksumed full-manifest `webapp:0:vacant`, epoch-zero
   baseline.
9. Verify replacement and reference Witness manifests, FI health, stopped IR
   writers, disabled flags, zero receipts, restored paths, retained evidence,
   and absence of hidden Matrix resources.

The live-restore journal now records its input intent before exclusive
publication of the replacement dump, rejects unowned/linked/ambiguous inputs,
and keeps OID-based database identity across rename crash windows. All twelve
RH-012 failure points listed in Section 47.3 are prerequisites on the dark
epoch-zero target, not evidence already obtained.

RH-010 uses a disposable PostgreSQL cluster wholly on Matrix-owned tmpfs with
`listen_addresses=''` and a private Unix socket only. `libfaketime` is loaded
only into that disposable PostgreSQL child; the host clock, production Writer
Witness, and production PostgreSQL remain outside the clock fault. Exact
before/after fingerprints cover the production state manifest, database
inventory, credential bundle, PostgreSQL system identifier, process
identity/start ticks, and process maps, including proof that production
processes never loaded `libfaketime`.

The frozen-release preflight also attests the installed offsite backup/upload
helpers and both offsite systemd unit files byte-for-byte, and requires the
offsite timer to remain enabled and active. It verifies the packaged
`libfaketime` trust boundary and rejects active/dangling campaign state,
unfinished restore/HMAC state, and abandoned hidden temporary files.

The next gate remains unchanged in scope: complete local source regression and
preflight validation, install the exact reviewed release only on the dark
replacement Witness, execute and recover all twelve live-restore prerequisite
failures, obtain a fresh exact-SHA read-only preflight, and have external
reviewers approve that exact remediation delta. Until those gates pass, RH-001,
merge with `main`, production writer activation, WebApp product mutation, and
Arvan/CDN mutation remain prohibited.

### 47.7 Release, runtime, host-policy, and custody attestation boundary

The current feature-worktree remediation extends Section 47.6 with an exact
installation trust chain. The implementation is present at source level and
the focused verifier/deployment checks plus the hermetic combined source gate
have passed in the isolated feature worktree. This subsection does not
claim that the hardened release is
installed or attested on the replacement dark Witness.

The source-level contract is now:

1. **No online or floating Python install.** Provisioning requires a complete
   offline wheelhouse whose exact filenames and SHA-256 values match the
   release-bound `wheelhouse.sha256`. Extra and missing wheels are both errors.
   `pip` itself is exactly pinned in `requirements.lock` and supplied by the
   same wheelhouse; there is no separate unbound bootstrap upgrade or network
   fallback. The venv is created with `--without-pip`, and the first installer
   is loaded directly from the already-attested pinned pip wheel with isolated
   user/environment state.
2. **Exact release bytes and metadata.** The canonical release is accepted only
   when every hash matches and the complete tree is owned by the bound UID/GID,
   all directories are exactly `0755`, inert files are `0644`, only the seven
   reviewed scripts are `0755`, and every file has exactly one hard link.
   Ownership, mode, inode, link count, size, and timestamp stability are checked
   during both descriptor reads and the closing tree scan.
3. **Exact host and venv runtime bytes.** `python-runtime.json` is a
   release-bound Ubuntu 24.04 manifest for the canonical CPython executable,
   complete active stdlib and `lib-dynload` tree, external symlink targets,
   transitive ELF/shared-library closure using the pinned loader's actual
   `--list` resolution, loader cache/config/preload state, OS identity, and
   exact dpkg package identities plus verification of every packaged md5sums
   entry. `DT_RPATH`, `DT_RUNPATH`, and any `ld.so.preload` are rejected. Venv attestation
   then requires an exact lock-to-distribution match and verifies every
   RECORD-listed path, owner, metadata, size and digest while closing all other
   runtime nodes. Both host and venv inventories feed deterministic digests.
   Manifest generation also parses every native ELF member in the exact
   offline wheelhouse, and installed-runtime verification independently closes
   every venv ELF interpreter/`DT_NEEDED` edge into either a RECORD-bound venv
   object or a release-bound system shared library. The verifier starts from
   `env -i` with `-I -S -B -X utf8 -X
   pycache_prefix=/dev/null`, so inactive system bytecode cannot enter imports;
   unclaimed venv files, bytecode/cache entries and startup customizations fail.
4. **Installation-specific provenance.** Provisioning emits a secure,
   root-owned `runtime-provenance.json` that binds the release manifest,
   requirements lock, wheelhouse manifest, expected host-runtime manifest and
   digest, and the observed venv-runtime digest. It is dynamic evidence for one
   installation and remains separately attested from the immutable source
   release manifest.

This is fail-closed host integrity evidence, not TPM/remote attestation. The
kernel, root account, filesystem and root-owned Ubuntu/dpkg database remain the
explicit trusted-host boundary. The target may not self-generate its expected
manifest during provisioning; it compares against the reviewed artifact from
the canonical build image. A compromised root/kernel can invalidate that trust
base and is outside what this Python verifier can prove.

5. **One durable activation generation.** A single atomic
   `/opt/trading-bot-witness/active` pointer selects an immutable activation
   directory containing `release`, `venv`, and `runtime-provenance.json`.
   Service code and interpreter dependencies cannot be advanced through
   independent pointers; compatibility links and the running command must
   resolve through that same activation. Release, venv, provenance, activation,
   and their containing directories are fsynced before the pointer changes;
   nftables is attested before exposure. `begin`, `publish`, credential
   `finalize`, `commit`, and service `complete` are recorded across a root-only,
   fsynced journal. Nginx, Writer Witness, and backup timers are quiesced during
   publication. Any error or handled signal rolls back the complete
   code/runtime/config generation; a boot recovery unit and periodic watchdog
   roll back uncommitted state or finish a committed generation only after its
   services pass health checks. Genuine first install is a separately tested
   transaction rather than an implicit legacy migration.
6. **Credential publication is a separate two-phase transaction.** Initial
   database and pairwise HMAC material is created exclusively in a root-only
   bootstrap file. `prepare` renders candidate runtime and client files while
   preserving the currently rotated pairwise HMAC state; `finalize` records the
   live credential generation and durably scrubs bootstrap HMAC material before
   the activation commit. A committed journal remains recoverable until public
   services complete. A process-held
   descriptor lock covers the entire prepare-to-finalize interval and is the
   same lock used by HMAC rotation. Partial schemas, unknown fields, linked or
   unsafe files, a concurrent rotation, and any attempt to resurrect a scrubbed
   bootstrap secret all fail closed. Database passwords never enter the shell
   through `source`.
7. **Effective service state, not file presence.** Preflight binds the installed
   unit bytes and checks systemd's effective fragment path, requires no drop-in
   paths, and proves the effective user, group, working directory, command, and
   hardening properties. A matching base unit with an overriding drop-in is a
   failure.
8. **Complete effective firewall semantics.** The effective `nft -j list
   ruleset` is strictly parsed and compared with a release-bound semantic
   policy digest. Only nft metainfo, generated handles, and runtime counter
   totals are normalized; tables, chains, rules, expressions, order, and every
   other policy-bearing value remain bound. The expected IPv4/IPv6 policy is
   exclusive, so an added permissive table or rule cannot hide behind matching
   UFW text. An intentional host-policy change uses the isolated verifier's
   explicit `--emit-policy-binding` output, requires semantic review, and is
   committed separately; provisioning never re-pins itself.
9. **Exact restore-database ownership.** A database created or retained by a
   live-restore campaign is journal-owned only as its exact `name + PostgreSQL
   OID` pair. Cleanup rechecks that pair immediately before deletion and refuses
   same-name recreation, unowned tag-like databases, changed OIDs, or an
   inventory that differs from the pre-attempt baseline.
10. **Freshness starts after proof finishes.** `generated_at` is provenance for
   preflight start. The bounded execution window is calculated from
   `completed_at`, written only after all checks finish, so a long-running
   preflight cannot present stale observations as newly authorized evidence.
11. **Offsite custody is proven, not inferred.** Frozen helper/unit bytes, secure
   root-owned offsite configuration and age-recipient metadata, enabled/active
   timer state, the last successful service result, and a secure fresh upload
   marker must jointly bind the latest local backup basename and checksum. A
   configured timer without proof of the latest upload is not ready.
12. **DPI authorization covers every transport byte.** One scenario is bounded
   to `900` seconds locally and by a server-clock `not_after` durably embedded
   in the replacement Witness campaign identity. Claim/consume/active mutation
   assertions reject expiry; cleanup-only ownership remains available for
   revocation and rollback. Cleanup has its own bounded 900-second recovery
   window. Approval requires
   at least `64 MiB` and reserves `16 MiB` exclusively for cleanup. The journal
   conservatively charges HTTP control traffic, SSH handshakes and commands,
   SCP sessions and maximum file payloads, reboot reconnect attempts, abort
   probes, cleanup, and the final four-role postflight. Tmpfs-backed persistent
   SSH ControlMaster sockets avoid repeated handshakes, but a master is credited
   only after a successful transport and is charged again after failure.
13. **The source gate is closed and hermetic.** It re-executes through `env -i`,
   supplies only non-secret placeholder application settings, syntax-checks an
   explicit closed list of shell/Python sources without writing bytecode, runs
   the explicit unit-module list with zero skips, then requires four guarded
   PostgreSQL tests and the four-database failure drill.

14. **Signer trust is source-pinned, not controller-selected.** The canonical
   root-owned `allowed_signers` bytes must match an exact SHA-256 in the reviewed
   runner source in addition to containing two different public keys. The pin
   is intentionally unconfigured until the observer and incident commander
   supply independently custodied public keys. This blocks RH execution but
   does not block dark installation or restore prerequisites.

| Gate | Current status |
|---|---|
| Design and implementation in isolated feature branch | Implemented and committed at source level |
| Focused release/deployment/Matrix verifier groups | `39 + 140 + 97` passed, zero failures/skips |
| Hermetic source gate on the clean committed feature checkout | `397` unit tests passed with zero skips, followed by `4` guarded PostgreSQL tests and the four-database failure drill |
| Fresh release from the clean committed feature checkout | Passed; `release-manifest.json` SHA-256 is `d1dc462732a24ad8422ce036c83e9685ab2f3450166fcaa15f3e922ca84fd13f` |
| Commit/push of the source remediation | `273b4a96` pushed to the isolated feature branch; no `main` integration |
| Install exact release and offline wheelhouse on `185.206.95.94` | Pending |
| Live runtime/provenance, effective-systemd, nftables, and offsite proof | Pending |
| Twelve hard-kill live-restore prerequisite points | Pending |
| Fresh exact-SHA preflight and external re-review | Pending |
| Independently custodied signer policy and reviewed source SHA pin | Pending external key-custody input; RH execution fails closed |
| RH-001/RH-010 or any other real-host scenario | Not authorized and not executed |
| Full Matrix, merge with `main`, writer activation, WebApp or Arvan mutation | Not authorized and not executed |

The next permitted sequence is limited to: obtain external re-review of the
exact final feature delta; install that exact release only on the replacement dark
Witness; configure and prove offsite custody; run and recover the twelve restore
prerequisites; produce a fresh read-only exact-SHA preflight; and obtain
external approval of that exact delta. A successful
source gate or dark-host attestation still does not authorize Full Matrix,
`main` integration, a first lease, production WebApp changes, or CDN routing
changes.

### 47.8 Four-agent review reconciliation at `2675cb0c` - 2026-07-18

Four independent reports reviewed the exact `2675cb0c` feature snapshot.
Gemini approved the dark install; Claude, ChatGPT Pro, and ChatGPT Ultra
rejected that exact snapshot. Mechanical source verification confirmed the
shared install findings, so this roadmap treats the majority reject as the
correct gate. No install, live prerequisite, RH scenario, merge, deployment, or
CDN mutation occurred while remediating them.

The committed feature remediation closes the validated source defects as follows:

- release, wheelhouse, runtime, and nftables verification starts only through
  the pinned system Python under `env -i`, `-I -S -B`, UTF-8 mode, and disabled
  bytecode; the trust-anchor CLIs also reject an unisolated direct invocation;
- PostgreSQL role passwords no longer appear in process argv, Ed25519 private
  key creation is exclusive/owner-only from its first inode, and ordinary
  release activation neither rotates TLS nor mutates firewall policy;
- fresh install, legacy migration, uncommitted rollback, committed service
  completion, and power-loss/SIGKILL recovery are separate activation states.
  Credential finalize/scrub precedes commit, and a periodic watchdog owns the
  commit-to-service-completion gap;
- preflight now proves terminal activation, clean locks/journals, exact
  credential marker/bootstrap state, installed recovery/watchdog bytes and
  enabled watchdog timer before it can bless the dark host;
- system-runtime evidence verifies packaged file hashes and actual pinned-loader
  selection, rejects runtime search paths/preload, and suppresses service
  bytecode creation;
- restore input publication is capped while copying, metadata-less owned HMAC
  crash state is reclaimed without a permanent quarantine wedge, RH-011 proves
  exact vacancy with the service stopped, and RH-009 compares full production
  evidence before/after the disk-full probe;
- a failed/ambiguous requester stop cannot prevent attempted transient-HMAC
  revocation. Isolation remains until revocation/recovery is proven; reconnect
  and baseline restoration still stop on any critical ambiguity;
- ControlMaster accounting expires conservatively and forgets ambiguous
  sessions after timeout/failure. Scenario authorization is also bounded by a
  durable replacement-Witness `not_after`, while cleanup has a separate bounded
  recovery deadline;
- FI and IR preflight/continuous probes inspect both application and sync-worker
  containers for disabled Witness flags and absence of client credentials;
- the closed source list now covers every release-executed helper, with a unit
  equivalence guard to prevent a later helper addition from silently escaping
  syntax coverage.

Local evidence from the clean committed feature checkout is a passing
`397`-test zero-skip hermetic unit gate, `4` real PostgreSQL tests, and the
four-database failure drill. A fresh release build and `git diff --check` also
pass. Those results remain source evidence only; they are not live-host
attestation or authorization for an RH scenario.

Two gates deliberately remain external or later-phase:

1. **Independent signer custody (`BEFORE_RH001`).** Two people must provide
   distinct public keys while retaining the private keys on separate devices.
   The canonical `allowed_signers` SHA-256 must then replace the current
   `UNCONFIGURED` source pin in a separately reviewed commit. Until then approve
   and execute modes fail closed. The agent must not generate or custody both
   private keys.
2. **Total controller-host-loss capsule (`BEFORE_REMAINING_MATRIX`).** Current
   cleanup is safe with the durable controller journal and target-side
   operation journals, but a complete loss of the controller still lacks an
   independently retained, non-secret capsule that can reconstruct the tooling
   entrypoint. Select and prove an independent capsule destination before the
   Matrix advances beyond the scenarios whose approved gate requires it.

Current-main Alembic/semantic integration remains explicitly outside this
branch-remediation step. It must be resolved in a later integration candidate;
`main` must not be merged into this independent branch merely to run the dark
install or its guarded prerequisites.

### 47.9 Exact-SHA rejection at `21aae1de` and second source remediation - 2026-07-18

The two supplied exact-SHA ChatGPT reports for `21aae1de` both reject dark
installation and the guarded live prerequisites, classify RH-001 as not yet
authorizable, and require an explicit later current-main integration. The
reports authorize no SSH, installation, service/database/firewall/TLS mutation,
RH execution, merge, deployment, CDN/DNS change, or traffic movement. This
roadmap adopts that fail-closed verdict; no such action was taken while
remediating the source.

Mechanical verification confirmed these immediate source findings:

- a different-release `begin` could archive the only committed-but-not-service-
  complete activation journal, and boot recovery could synchronously start the
  services it was ordered before;
- watchdog recovery released the provision capability before service effects,
  while activation recovery did not share the HMAC-rotation lock;
- bootstrap, signing-key, and first-install TLS publication could be left in a
  non-reclaimable partial state after power loss;
- privileged installed helpers and the actual Writer process were not bound to
  the same isolated interpreter/runtime contract attested by the release;
- PostgreSQL clear passwords could enter statement/audit logs through role DDL;
- the campaign helper accepted up to 3,630 seconds and campaign expiry was not
  connected to the actual transition transaction;
- cleanup subprocesses could outlive their separate deadline, and delayed
  postflight-pending recovery entered remote inspection before cleanup mode;
- a transparently recreated OpenSSH ControlMaster could succeed without a new
  handshake reservation;
- candidate quiescence/publication still lacked strict inactive-state proof,
  durable per-candidate digest binding, and one crash point after every managed
  publication.

The current source remediation closes those code paths as follows:

1. A committed activation journal refuses every new `begin` until explicit
   service completion. Boot recovery now performs only direct rollback
   reconciliation before Nginx/Writer; the separately ordered watchdog handles
   service completion. Provision and HMAC locks remain held through recovery,
   daemon restart, health proof, and journal completion.
2. Candidate mode, size, and SHA-256 bindings are persisted before publication;
   every managed file and Nginx publication has a SIGKILL point. All loaded
   public units must prove `inactive` or `failed` before publication starts.
3. Bootstrap, signing-key, and TLS first publication use private helper-owned
   initialization/generation namespaces with fsynced atomic publication and
   narrowly validated retry reclamation. A surviving private signing key can
   deterministically recreate and validate its exact public key.
4. Installed root Python helpers self-reject ambient startup and run through the
   pinned system Python with `env -i`, `-I -S -B`, UTF-8 mode, and disabled
   bytecode. The Writer unit runs `uvicorn` through the exact activation venv
   Python with isolated environment handling; preflight binds its `/proc`
   executable, argv, and forbidden environment. The offsite S3 configurator
   also parses credential sources through the pinned clean interpreter, while
   the wheelhouse builder uses isolated pip and a closed verifier startup.
5. PostgreSQL role DDL receives locally generated SCRAM-SHA-256 verifiers, not
   clear passwords. Clear credentials remain limited to the actual authenticated
   client checks and never enter the DDL statement text.
6. Campaign claim and HMAC preparation both cap `not_after` at 900 Witness
   seconds. Scenario credentials persist that expiry in `runtime.env`; service
   startup validates the key/expiry pairing, and a fresh database-clock check
   inside the transition transaction rejects expiry before receipt lookup or
   Writer-state mutation. Reprovision preserves, rather than silently strips,
   an active campaign credential expiry.
7. Cleanup has one non-renewable deadline, every subprocess timeout is clamped
   to its remaining allowance, and every recovery path enters cleanup mode
   before postflight/release reconciliation. SSH/SCP accounting now reserves a
   complete handshake upper bound on every operation, independent of cached
   ControlMaster state.

These remain source changes only. The second-remediation worktree passed the
closed hermetic gate with `404` unit tests and zero skips, followed by all `4`
guarded real-PostgreSQL tests and the complete four-database failure drill. A
fresh minimal release passed closed-manifest verification with
`release-manifest.json` SHA-256
`bc36beda23a49a9423aef26a60dee9eeb417b4136dcb664ab4bac56ff498562b`;
`git diff --check` also passed. A new clean exact commit and independent
exact-delta review are still required before reconsidering a dark install.
Real systemd boot/timer behavior, fresh-host crash/retry and concurrency
evidence remain live prerequisites, not facts established by unit tests.

The independent signer-custody/source-pin gate remains `BEFORE_RH001`; the
controller-loss recovery capsule remains `BEFORE_REMAINING_MATRIX`; signer
policy, RH-007 topology proof, product fencing/identity, current-main migration
lineage, and Arvan/WebApp activation remain unchanged later gates. Full Matrix,
`main` integration, first lease, and traffic/CDN changes are still forbidden.

### 47.10 Exact-SHA split verdict at `bb3a17cf` and third source remediation - 2026-07-18

Four supplied reports reviewed exact feature SHA `bb3a17cf`. Gemini and Claude
approved a dark install, while both ChatGPT reports rejected it. The two reject
reports traced additional executable paths and crash boundaries to concrete
source. Mechanical inspection reproduced those paths, so the fail-closed
verdict controls. No SSH, package installation, live service/database/firewall
or TLS mutation, RH execution, deployment, merge, CDN/DNS change, or traffic
movement is authorized or performed by this remediation.

The source-confirmed immediate findings were:

- rollback data was archived before predecessor service/timer intent and
  health restoration became terminal; a crash in that gap could erase the
  only durable completion plan;
- ordinary provisioning invoked unpinned package-manager mutation before the
  release lock and activation journal, and could create the service account or
  repair prerequisite directories as part of a release attempt;
- publication stopped timers but not already-running backup/offsite oneshots,
  allowing old work to cross a helper-generation boundary;
- systemd and installed client paths retained pre-source shell/loader or
  ambient-interpreter injection paths; live Writer attestation did not close
  its complete mapped native-code set;
- a secret-bearing smoke client, the Matrix signer/client, and the PostgreSQL
  clock-jump probe were not all invoked by a fixed isolated interpreter with a
  closed environment and self-check;
- campaign time could be sampled before waiting on the Writer row lock, then
  used to authorize a mutation or replay after expiry;
- RH-011 overlap could retain the original unbounded credential as the
  previous key after the campaign expiry;
- cleanup's 900-second epoch lived only in one controller process and could be
  renewed by a later `--recover` process;
- clear secret-bearing shell input could still be exposed when an operator
  invoked provisioning with inherited xtrace;
- the supplied source evidence hard-coded four guarded PostgreSQL tests and did
  not prove the new post-lock timing barrier.

The third source remediation closes those paths as follows:

1. Activation schema v2 records the exact `load`, `active`, and `unit-file`
   intent of Nginx, Writer, both backup services, and both timers. Rollback now
   enters a durable `rolled_back_pending_service_completion` phase. Neither
   `recover` nor a new `begin` archives that journal; only exact service-state
   restoration and any required Writer health proof may call explicit rollback
   completion. Freshly absent units remain absent. Boot recovery fails closed
   while service completion is pending, preserving systemd ordering.
2. Provision obtains a host-wide lock before its first persistent release
   mutation. Ordinary release provisioning contains no `apt-get`, account
   creation, or prerequisite-directory repair. It attests an externally
   supplied exact dpkg inventory digest plus the pre-existing account/group and
   bootstrap directory trust contract. OS/package preparation is a distinct,
   explicitly approved image/bootstrap workflow.
3. All six managed units are stopped and runtime-masked during serial
   publication, including active oneshots. Rollback restores their exact prior
   enable/mask and active intent; completion cannot infer intent from the new
   release.
4. Secret-bearing shell entrypoints use absolute `/bin/bash`, immediately
   disable xtrace, and systemd clears the expanded shell, Python, glibc, and
   dynamic-loader environment set. The source tests invoke provisioning under
   inherited `bash -x` with a sentinel secret and require that it never appears
   in output.
5. Writer preflight now checks `/proc/<pid>/maps` through a fixed isolated
   verifier. Every mapped ELF object must belong to the release-bound system
   closure or the fully attested activation venv; deleted, escaped, writable,
   or otherwise unknown mappings fail closed. The installed smoke client,
   Matrix client, renderer, and host-fault clock probe each enforce their exact
   interpreter, isolation flags, prefix, and clean-environment boundary.
6. Campaign transitions lock the Writer row first, then obtain fresh database
   time inside the transaction, and reject expiry before campaign receipt
   lookup or mutation. The guarded PostgreSQL gate adds a real two-session
   barrier: one session holds the row lock while another transition waits,
   database time crosses expiry, and the released transition must create
   neither state change nor receipt. The gate's expected count is now five and
   the source/failure-drill evidence derives the observed count.
7. Campaign prepare assigns the same bounded Witness `not_after` to both the
   current and previous overlap credentials. Only exact campaign recovery may
   restore the original unbounded baseline after ownership is proven.
8. Cleanup start and expiry are fsynced into the campaign journal before
   cleanup effects. Every later controller reconstructs the remaining allowance
   from that UTC expiry rather than granting a new 900 seconds. Once expired,
   ordinary remote inspection and repair stay forbidden; a narrowly flagged,
   30-second maximum emergency path may only prove exact ownership, revoke the
   transient credential, reconcile its owned HMAC journal, and release the
   campaign tombstone. Any incomplete baseline repair remains dirty and needs
   new explicit authorization.

Current status is **source gate passed; clean commit/push and independent
exact-SHA review pending**. The complete hermetic worktree gate passed `412`
explicitly listed unit tests with zero failures and zero skips, all `5` guarded
real-PostgreSQL tests, and the complete four-database failure drill. The
PostgreSQL output names and passes the real post-lock expiry-barrier test. A
fresh minimal release passed its closed verifier with
`release-manifest.json` SHA-256
`7bd4fc9eb7d042fcdb3b9bcf8e84a9e46a67a15a839c841a380b023462665b9f`;
`git diff --check` and the closed shell/Python syntax list also passed. These
results bind the current tracked source/test bytes but do not replace a clean
commit/push or independent review of the resulting exact SHA.

The independent signer-custody/source-pin gate remains `BEFORE_RH001`; the
controller-loss recovery capsule remains `BEFORE_REMAINING_MATRIX`; RH-007
topology, product fencing/identity, replication/failback, current-main migration
lineage, and Arvan traffic controls remain later gates. A third-remediation
source pass cannot authorize Full Matrix, `main` integration, first lease,
production Writer activation, WebApp mutation, or traffic/CDN changes.

### 47.11 ChatGPT Pro rejection at `086affed` and fourth source remediation - 2026-07-18

The supplied ChatGPT Pro report reviewed exact feature SHA `086affed` and did
not approve a dark install, the guarded live prerequisites, or RH-001. Its
source traces were mechanically reproduced. This roadmap therefore adopts the
reject verdict and keeps SSH/install, live service/database/firewall/TLS
mutation, RH execution, deployment, `main` integration, CDN/DNS change, and
traffic movement unauthorized while the source is remediated.

The immediate findings and their source dispositions are:

1. The activation transaction no longer samples replay intent before the long
   release/runtime staging interval. `begin` owns only filesystem candidates;
   a new durable late-intent step runs immediately before the first systemd
   mutation, rejects transitional/failed intent, and is mandatory before
   publish. Backup timers freeze first. Any in-flight backup/offsite oneshot
   finishes under its old generation and is never stopped or replayed; its
   rollback intent is normalized to inactive. Rollback journal completion now
   requires the helper to compare all six resampled `load + active + unit-file`
   states with the durable intent. Crash recovery before the late snapshot
   archives only a no-service-change rollback, including a second crash during
   that cleanup.
2. The former ten-package version list is replaced by a release-bound host
   toolchain verifier. Its externally approved canonical inventory binds every
   privileged executable used by provision/recovery to the requested and
   resolved paths, exact file SHA-256, safe metadata, and owning package
   identity/version/architecture/status, plus the non-executable bootstrap
   packages. Every ELF tool's complete observable native dependency closure is
   included with the same byte/package evidence. Ordinary release provisioning only verifies this digest; it does
   not install or repair the host.
3. The Witness credential parser now accepts the actual Matrix preparation
   state: a campaign current key and its baseline previous key may coexist only
   when both carry the exact same bounded expiry. A non-campaign expiry outside
   that exact overlap still fails service startup. The rotation test now feeds
   the prepared `runtime.env` through the real service parser.
4. Live process-map attestation no longer trusts pathname membership alone.
   Every executable file mapping is tied to its `/proc` device/inode, the
   current file is read through a stable descriptor and checked against its
   system-manifest or venv SHA-256, and process start time plus the full maps
   payload must remain unchanged across the verification interval.
5. Cleanup now reserves `2 MiB` of the existing `16 MiB` cleanup allowance in
   a separate emergency bucket that ordinary recovery cannot consume. Exact
   post-expiry revocation has independent persisted operation/byte counters and
   one persisted, non-renewable 30-second aggregate deadline shared by all of
   its subprocesses; it no longer receives 30 seconds per command.
6. Activation recovery and watchdog units explicitly clear all activation
   test/failpoint environment variables in addition to the existing Python,
   shell, glibc, and loader injection set.

Local evidence remains source-only. The focused credential, HMAC, process-map,
controller, and toolchain groups passed `117` tests. The closed source gate
passed its syntax phase and all `421` explicitly listed unit tests with zero
failures and zero skips, all `5` guarded real-PostgreSQL tests, and the complete
four-database failure drill. A fresh minimal release passed its
closed verifier with `86` manifest entries, `9` executable entries, and
`release-manifest.json` SHA-256
`9f63102e74f6dcb3526c00c63c832119f7e781d53cd1aae6116a00f885fdbf20`.

The fourth remediation still requires independent review of its committed
exact SHA before reconsidering a dark install. Independent
signer custody remains `BEFORE_RH001`; controller-loss recovery remains
`BEFORE_REMAINING_MATRIX`; current-main migration lineage and all product/CDN
activation gates remain later. This work does not authorize Full Matrix,
`main` integration, a first lease, WebApp mutation, production deployment, or
traffic changes.

### 47.12 ChatGPT Pro rejection at `5bd5c884` and fifth source remediation - 2026-07-18

The supplied ChatGPT Pro report rejected dark installation, live prerequisites,
and RH-001 at exact SHA `5bd5c884`. The five reported paths were reproduced in
source. This remediation therefore remains source-only: it performs no SSH,
live installation, service/database/firewall/TLS mutation, RH scenario,
deployment, `main` integration, CDN/DNS change, or traffic movement.

The source dispositions are:

1. Package exclusion and the mutation actor now share one PID. The native
   apt/dpkg POSIX-lock process `execve`s into provision or watchdog instead of
   leaving an independently killable coprocess. Provision and watchdog run as
   the exact systemd `MainPID` under `KillMode=control-group`; child work cannot
   remain outside the transaction cgroup after actor death. The actor can prove
   its own four kernel lock records through `/proc/locks`, and preflight binds
   the installed actor bytes to the release manifest.
2. Every nonterminal v3 recovery requires the journal's host-toolchain digest
   at the activation-helper boundary. Ordinary provision recovery first reads
   the pending binding, requires it to equal the current externally approved
   digest, runs the journal-owned verifier while package locks are held, and
   only then permits rollback, service reconciliation, or completion. Digest
   A/B tests cover prepared, late-unit-intent, partial publication, activated,
   rollback-pending, and committed journals and require byte-identical journal
   and pointer state on rejection.
3. Activation exposes an explicit protocol version. One exact-hash adapter
   supports the `5bd5c884` bound predecessor and the separately authorized
   `2e4dc0b1` legacy helper; current completion receives the mandatory digest,
   while legacy syntax omits the unsupported option. Unknown helper bytes and
   unauthorized legacy migration fail before service action.
4. `Controller.command` records exception evidence on every subprocess exit
   path. A timeout, nonzero result, signal, or cancellation that crosses the
   persisted ordinary cleanup deadline becomes `MatrixAbort` before a generic
   error can mask it. Cleanup then enters its once-persisted 30-second exact
   revocation window. An independent shorter command timeout remains an
   ordinary failure while cleanup authority is still valid.
5. Matrix controller execution has a separate toolchain inventory and digest.
   It binds absolute `git`, `ssh`, `scp`, `ssh-keygen`, `findmnt`, Docker,
   systemd, exact Python, package metadata, and the complete observed native
   dependency closure. A clean isolated launcher holds native package locks,
   becomes the exact controller PID inside one `KillMode=control-group`
   transient service, and rejects ambient executables/environment. The
   controller digest is frozen in preflight, dual-signature approval, durable
   campaign journal, summary, recovery, and postflight.
6. The replacement-Witness inventory no longer claims controller-side SSH/SCP
   bytes. A generated AST artifact records every local process funnel and
   classifies remote call sites as controller, replacement Witness, rollback
   Witness, WebApp-FI, WebApp-IR, or the closed dynamic `role/site` domain.
   Source gate compares this artifact to actual call sites, so a new subprocess
   or host-role path is an explicit reviewed artifact change.

Local verification for this remediation passed `448` explicitly selected unit
tests with zero failures and zero skips, all `5` guarded real-PostgreSQL tests,
and the complete four-database failure drill. A real local transient-systemd
self-check also proved that the isolated controller interpreter was the exact
service `MainPID`, that `KillMode=control-group` was configured, that the same
PID owned all four native package locks, and that its canonical controller
toolchain digest matched. This is source/controller evidence only; it is not a
replacement for the later dark-host systemd crash matrix or live preflight.
A fresh minimal Writer Witness release passed closed manifest and metadata
verification with `87` manifest entries, `9` executable entries, and manifest
SHA-256 `1cf8051161cf793c613d1850006dc35ba7ceca23c7d2cb3dc702e4fe332a5c70`.

The fifth remediation is not self-authorizing. A clean commit/push, complete
closed source gate, fresh release verification, and another independent exact-
SHA review are required before reconsidering dark installation. Native
systemd/cgroup crash evidence remains a live prerequisite. Independent signer
custody remains `BEFORE_RH001`; controller-loss recovery remains
`BEFORE_REMAINING_MATRIX`; Full Matrix, `main` integration, first lease,
production activation, WebApp mutation, and CDN/traffic changes remain
forbidden.

## 48. Encrypted WebApp-IR Data-Ready Dark Standby - 2026-07-19

This section records the separately authorized emergency-readiness track. It
does not change the Writer Witness review verdict, authorize RH-001, merge the
feature into `main`, start a WebApp-IR writer, or mutate CDN/DNS.

### 48.1 Current infrastructure

- the two Iran-access-only Bamdad replacements were deleted;
- current WA-IR is Arvan Simin `185.206.95.250`, Ubuntu 24.04, 4 vCPU,
  8 GiB RAM, and 75 GB provisioned storage;
- primary Witness is `185.206.95.94`; `185.231.182.6` remains transitional;
- Bot-FI, WebApp-FI, WA-IR, and both Witnesses report `UTC` with synchronized
  NTP; application presentation remains `Asia/Tehran`;
- UFW on WA-IR permits SSH only from Bot-FI and the two Witness addresses and
  exposes no 80/443/8000/8100 listener.

### 48.2 Object Storage and encrypted release evidence

Private versioned bucket `production-sync-coin` is the bulk path. The exact
production release `2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5` was rebuilt,
bundled with frontend and Docker images, age-encrypted, uploaded, downloaded
directly by WA-IR, verified, decrypted, and installed without starting Compose.

- release ciphertext SHA-256:
  `b0316ed49b768d41e0eca701b22b27a88bb04d1abcbf10a6d755a2489e918f6e`;
- object version: `i09WelutxloIEN6YOmddHP7Ib4Mgx91`;
- anonymous GET: `403`;
- S3 HMAC credentials were not copied to WA-IR; only a root-owned `0600` age
  identity and a short-lived presigned GET were used.

### 48.3 Snapshot, restore, and parity evidence

At `20260719T103238Z`, WebApp-FI produced an online PostgreSQL/uploads/audit
backup. Redis was excluded from restore. The bundle was age-encrypted before
upload and restored only into an empty WA-IR database.

- snapshot ciphertext SHA-256:
  `746fdb9a58c1714a67510615d5d17a0854ca50f43a612c0d6144e6a407b36a9c`;
- object version: `Xrg-lpXSDtG3jGzSvLsHeSpJAw2NrPP`;
- anonymous GET: `403`;
- restored schema: 46 public tables at Alembic `f2c7d8e9a0b1`;
- reference-vs-WA-IR comparison: all tables and sequences matched across 83
  fingerprint lines; evidence SHA-256:
  `5fdedf21268cdf6b94c330053bcb5cc8f61e89546c7d84d56ec4b997dc42ca64`;
- one uploads file was restored; Redis was not restored;
- Compose safety overlay exposes only `db`; `app`, `sync_worker`, `migration`,
  and Redis remain behind `activation-forbidden`;
- final state is `data-ready-dark`, not origin-ready or writer-ready.

The operational contract and fail-closed manifest verifier are in
`docs/WEBAPP_IR_DARK_STANDBY_RUNBOOK.md` and
`deploy/production/webapp-ir-dark-standby.env.example`. A later refresh must
restore into an empty generation or use the approved incremental DR protocol;
this one-time snapshot must never be replayed over locally written state.

### 48.4 Domain and CDN boundary

- production root: `gold-trade.ir`;
- isolated CDN/failover-test root: `gold-trading.ir`;
- current Arvan account view: only `gold-trading.ir` is enrolled and active;
- current test records `app` and `switch-test`: proxied HTTPS origins still at
  WebApp-FI `65.109.220.59`;
- production root enrollment, DNS/CDN mutation, or traffic switching: not
  authorized and not performed.

The dark-standby manifest records both roots and fails validation if their
roles overlap, the test public host leaves the test namespace, or the Arvan
configured root points at production. The low-level origin-switch primitive is
also apply-locked to `gold-trading.ir` until the later post-Full-Matrix gate.
