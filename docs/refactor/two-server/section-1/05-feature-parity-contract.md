# Feature Parity Contract

Status: domain baseline present; endpoint/callback coverage incomplete

## Definition of parity

For the same precondition and input, current topology and target topology must be
equivalent after normalizing only approved nondeterministic values. Equivalence
includes all of:

```text
response status/schema/copy
database mutation and transaction boundary
event/outbox payload and ordering
notification audience and external side effect
timeout/retry/idempotency
failure and rollback behavior
```

A green HTTP response or matching screenshot alone is not parity. An existing
bug discovered here is recorded as a baseline drift and either preserved for the
topology refactor or fixed in a separately approved change set.

## Static surface coverage baseline

| Surface | Static inventory | Coverage state |
| --- | ---: | --- |
| FastAPI route decorators | 212 across 18 router modules | module inventory complete; per-route behavior mapping open |
| Bot handler decorators | 202 across 18 handler modules | module inventory complete; per-handler behavior mapping open |
| Frontend router paths | 30 | route inventory complete; action/API mapping open |
| recurring background jobs in authority registry | 15 entries: 14 required classes plus local sync worker | authority reviewed; target capability mapping open |
| ORM model tables | 59 | sync policy classification complete |

Because per-route/per-callback IDs are not complete, the `P1-00` coverage gate is
currently **BLOCKED**, not silently treated as 100%.

## Required behavior families

Every concrete behavior later receives an ID beneath one of these stable
families. The suffix must identify the route/command/action and persona; one ID
must never represent several different side effects.

| Family prefix | Scenarios that must be characterized |
| --- | --- |
| `AUTH-*` | setup password, login, OTP/SMS, session approval/recovery/expiry, logout, revoked/deleted user |
| `IDENTITY-*` | invitation, registration, Telegram linking, user/accountant/customer relations and blocks |
| `OFFER-*` | create/edit/cancel/expire/republish, Web/Bot provenance, tier/overtime, publication and cache |
| `REQUEST-*` | Web/Bot requests, confirmation/approval, duplicate/concurrent action, timeout and policy snapshots |
| `TRADE-*` | create/execute/manage/history, settlement/commission, receipt/delivery and conflict handling |
| `MARKET-*` | schedule/open/close, capture/facts, guard, estimator mode, widening/confidence and snapshot delivery |
| `MSG-*` | chats/messages, files/uploads/downloads, realtime/reconnect/unread and Web Push |
| `TG-*` | commands/callbacks, publisher/channel state, queue/retry/provider outcome and sole executor guard |
| `ADMIN-*` | users, commodities, invitations, broadcasts, settings, system operations and audit |
| `SYNC-*` | emit/apply/dedupe/gap/block/repair/parity, peer outage and restart/resume |
| `OPS-*` | readiness, restart isolation, backup/restore, migration, rollback, disk/log/retention and monitoring |

## Contract record required for each concrete behavior

```yaml
behavior_id: OFFER-WEB-CREATE-TIER2-NORMAL
surface: WEBAPP
persona_role: customer
customer_tier: 2
time_context: normal
preconditions: []
input: {}
response: {status: null, schema: null, copy_key: null}
db_mutations: []
events_outbox: []
side_effects: []
ordering: []
timeouts_retries: {}
idempotency_key: null
failure_cases: []
reference_evidence: null
regression_tests: []
target_result: NOT_RUN
```

IDs and fixtures must be machine-readable and generated coverage must fail when
a discovered mutation route, callback, recurring job or external side effect has
no ID.

## Initial golden invariants already supported by focused tests

The audit ran 81 focused tests covering background-job authority, Bot/Web
candidate behavior, deployment surface guards, Offer/Request source policy,
server routing/trade forwarding, sync field/registry policy and Telegram runtime
role. All passed. These tests establish useful invariants but are not the full
contract.

At minimum the target must preserve:

- exactly one Telegram executor and no Bot restart dependency on Web/API;
- Web/Bot provenance and tier/overtime policy inputs;
- home-authority checks for Offer mutation and expiry;
- request idempotency and source snapshots;
- no duplicate Telegram/Web notification after retry;
- local session/Messenger state handling and explicit re-login semantics where
  cross-site sessions are intentionally not transferred;
- all shared-table business hashes and money/inventory/settlement invariants;
- fail-closed behavior for unknown authority, migration mismatch and data gaps.

## Closure method

1. extract a deterministic manifest of every route, callback, scheduled job,
   consumer and external adapter;
2. map each item to one or more concrete `behavior_id` records and existing tests;
3. add side-effect-free characterization tests for uncovered high-risk behavior;
4. run one corpus against current topology and target topology;
5. classify every diff as `EXPECTED_TOPOLOGY`, `KNOWN_BASELINE_BUG`,
   `REGRESSION` or `NONDETERMINISTIC_TEST`;
6. require owner approval for every baseline bug/waiver and zero unexplained
   regressions before `P1-06` can complete.
