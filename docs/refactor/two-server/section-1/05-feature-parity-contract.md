# Feature Parity Contract

Status: historical owner review accepted; characterization/evidence and Codex Final Review open

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
bug discovered here is recorded as baseline drift and either preserved during the
topology refactor or fixed in a separately approved change set.

## Exact static surface inventory

The inventory is now decorator/route/job-level rather than module-level. Every
discovered live item has a unique family-seeded `behavior_id`; none is
`UNCLASSIFIED`.

| Surface | Exact discovered count | Static test evidence | Current state |
| --- | ---: | ---: | --- |
| FastAPI router decorators | 213 across 18 mounted router modules | included below | seeded |
| main FastAPI app decorators | 4 | included below | seeded |
| total live FastAPI decorators | 217 | 216 function/path references; 1 review item | seeded, variants open |
| Bot handler decorators | 203 across 18 active/conditional modules | 198 function/filter references; 5 review items | seeded, variants open |
| frontend router paths | 30 | 30 direct route-name references | seeded, action mapping open |
| background authority entries | 15: 14 required classes plus local sync worker | 15 direct name references | authority baseline seeded |
| ORM model tables | 59 | registry coverage complete | 23 sync, 33 local, 3 bookkeeping |

The earlier 212/202 counts omitted one FastAPI WebSocket decorator and one Bot
`chat_join_request` decorator. The four main-app routes were also outside that
router-only count. Correcting inventory is not a behavior change.

“Direct static reference” is a function/path/filter evidence hint, not a coverage
percentage. A missing reference may still be tested through a higher-level
scenario; each such item must be manually linked or receive a characterization
test before the gate closes.

## Machine-readable contract seeds

| Artifact | Contents |
| --- | --- |
| `inventory/surface-behavior-inventory.json` | counts, taxonomy, family authority/risk and inventory boundaries |
| `inventory/surface-api.json` | method/path/handler/source/family/test references for 217 decorators |
| `inventory/surface-bot.json` | handler kind/filter/function/source/family/test references for 203 decorators |
| `inventory/surface-web.json` | path/name/component/guard/family/test references for 30 routes |
| `inventory/surface-jobs.json` | current/target authority, tables and side effects for 15 registered jobs |
| `inventory/runtime-task-ownership.json` | API/Bot/Market child process, poller, worker and timer ownership seeds |

These files are generated from commit `19087ff0...`. A later code change that
adds/removes a route, handler or job must regenerate the inventory and fail CI if
the new item has no family/behavior seed.

## Human-readable scenario contract

Codex Final Reviewer reviews these domain scenarios, not hundreds of decorator lines. The
machine manifests prove that every line is assigned to one of them.

| Family | Real user/operator scenario | State and side effects that must remain equivalent |
| --- | --- | --- |
| `AUTH` | user requests OTP, logs in, approves/recovers/revokes a session or logs out | session and login-request state, quota, SMS/Telegram delivery, copy, expiry and revocation ordering |
| `IDENTITY` | invited user registers/links Telegram; owner manages customer/accountant relation, block or flag | stable identity, relation permission, visibility, session consequences and notification audience |
| `OFFER` | tier-1/2 user creates, parses, repeats, expires, cancels or republishes a Web/Bot Offer | immutable origin, `home_site`, timestamps, price guard, publication/cache/outbox and overtime policy |
| `REQUEST` | a Web or Bot user requests the opposite-surface Offer and owner approves/rejects/cancels | both origins, actor/tier/policy snapshots, idempotency, timeout and confirmation notifications |
| `TRADE` | approved request becomes a trade; users view/export history and receive results | immutable provenance snapshot, quantity/inventory/commission/settlement, receipts and Web/Telegram delivery |
| `MARKET` | schedule changes, facts/snapshots arrive, estimate confidence changes or price guard evaluates | schedule authority, fact timing, widening/fail-open rules, estimator mode, notice and guard decision |
| `MSG` | user opens Messenger, sends/edits/deletes/reacts, uploads media and reconnects realtime | local rows/media hashes, membership, ordering, unread/seen, WebSocket/poll and Web Push |
| `NOTIFY` | product creates a notification; user reads/deletes/preferences/subscription change | audience, unread count, shared notification row, local Push subscription and no duplicate delivery |
| `TG` | Bot receives command/callback, publisher dispatch or provider retry | callback ACK, FSM/anchor state, queue claim/dedupe, exact bot identity and single Telegram owner |
| `ADMIN` | admin edits users/commodities/settings or sends market/broadcast message | authorization, audit row, shared state, cache invalidation and broadcast fanout |
| `SYNC` | a committed event emits, retries, applies, detects gap/conflict or repairs | stable identity, sequence/hash/signature, exactly-once business apply, quarantine and checkpoint |
| `OPS` | process starts/restarts, readiness fails, backup/restores or frontend shell recovers | independent process lifecycle, fail-closed readiness, exact release/schema/config and recovery evidence |

## Mandatory Web/Bot combination matrix

Offer and Request parity is not satisfied by testing “one Web case and one Bot
case.” Every row below expands across owner/customer/accountant/admin where
allowed, tier 1/2, normal/overtime/closed time and success/reject/retry/concurrent
paths.

| Offer origin | Request/action origin | Required result |
| --- | --- | --- |
| Web | Web | Web policy, confirmation, notification and provenance unchanged |
| Web | Bot | Offer stays Web-origin; Request records Bot-origin and Bot reply/callback behavior |
| Bot | Web | Offer stays Bot-origin; Web Request and Web delivery do not rewrite it |
| Bot | Bot | sole Telegram execution/publication owner and Bot-specific policy remain unchanged |
| Internal/system | Web or Bot | system action provenance is separate; it cannot impersonate user surface |

The approved immutable Offer and Trade provenance contract in
`04-surface-policy-matrix.md` is part of every row.

## Mandatory failure timeline

Each mutating behavior must be characterized at these boundaries:

1. **failure before DB commit:** no durable business state and no external side
   effect;
2. **commit before event/side effect:** outbox/receipt remains retryable and user
   must not receive a false terminal failure;
3. **side effect before ACK:** retry sees the idempotency/receipt ledger and does
   not send or apply twice;
4. **two concurrent commands:** one authoritative result; loser gets deterministic
   conflict/already-completed behavior;
5. **process restart:** durable queue/FSM/reconciliation resumes without lost
   mutation or duplicate Telegram/Web delivery;
6. **unknown authority/schema/hash:** fail closed and visible; no implicit
   failover, LWW or synthetic evidence.

## Authority and restart invariants

- Web Writer fencing never disables permitted Finland Bot-home commands.
- only one Telegram poller/executor/provider owner may perform side effects;
- one API leader owns recurring API jobs, but authority is checked again by each
  job/domain command;
- Web/API and Bot restart independently; neither restart is a deployment
  prerequisite for the other;
- shared PostgreSQL/Redis failure affects both and must be exposed by readiness,
  not masked as successful behavior;
- moving from historical `iran/foreign` labels to capabilities cannot enable a
  previously forbidden job or suppress a required one.

## Static review queue

No surface is unclassified, but six items have no direct function/path/filter
reference in the test corpus and require manual evidence linking or characterization:

- API: the admin test-Web-Push command;
- Bot: one admin broadcast group-toggle, one overtime-preference entry action,
  one conditional publisher public-trade callback and two trade wizard back
  actions.

An evidence gap is not permission to delete or alter the handler. Each item is
preserved until a direct test or approved higher-level scenario proves behavior.

## Runtime-task ownership status

The 15-entry background authority registry does not enumerate every live child
coroutine or external timer. Current composition additionally includes:

- API leader election and conditional job factories;
- primary/publisher Telegram pollers, trade-suggestion listener and owner monitor;
- Queue-v1 executor/OTP worker or the mutually exclusive five legacy delivery
  workers;
- conditional publisher dispatcher/pollers;
- Market capture/store/processor/estimator/snapshot containers;
- sync-health, snapshot relay/publish and legacy bridge systemd timers.

They are now recorded in `inventory/runtime-task-ownership.json`, including the
conditional and mutually exclusive runtime sets. What remains open is the target
binding `task seed → exact compose service/image → credential mount → DB/Redis
pool → readiness/restart policy`. That binding belongs to `P1-03`; any unknown
current task or overlapping owner still blocks `P1-00`.

## Contract record required for each scenario variant

```yaml
behavior_id: OFFER-WEB-CREATE-TIER2-NORMAL
seed_ids: [API-OFFER-POST-OFFERS-CREATE-OFFER-L...]
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

## Initial golden evidence

The audit ran 81 focused tests covering background-job authority, Bot/Web
candidate behavior, deployment-surface guards, Offer/Request source policy,
routing/trade forwarding, sync field/registry policy and Telegram runtime role.
All passed. These are useful invariants, not the entire characterization corpus.

At minimum the target must preserve:

- exactly one Telegram executor and no Bot restart dependency on Web/API;
- Web/Bot provenance and tier/overtime policy inputs;
- home-authority checks for Offer mutation and expiry;
- request idempotency and source snapshots;
- no duplicate Telegram/Web notification after retry;
- local session/Messenger state and explicit cross-site re-login semantics;
- shared-table business hashes and money/inventory/settlement invariants;
- fail-closed behavior for unknown authority, migration mismatch and data gaps.

## Closure ledger

| Work item | Status |
| --- | --- |
| deterministic decorator/route/job extraction | `COMPLETE` |
| unique family-seeded IDs and zero unclassified items | `COMPLETE` |
| static function/path/filter test-reference scan | `COMPLETE` |
| manual resolution of 6 evidence-review items | `OPEN` |
| current runtime child/poller/timer owner seed manifest | `COMPLETE` |
| exact target compose/credential/readiness binding | future `P1-03` |
| persona/tier/time/failure scenario records | `OPEN` |
| owner approval of human-readable scenario contract | `COMPLETE — 2026-09-02` |
| current-vs-target differential execution | future `P1-06` |

`P1-00` becomes complete only after the open current-state items have evidence or
a Codex-approved blocker. `P1-06` later proves the target against this frozen
contract; it does not invent the contract after implementation.

## Owner review gate

The human review is limited to these four assertions:

1. the twelve scenario families above cover the user/operator capabilities that
   must survive consolidation;
2. Web/Bot differences, Offer/Request/Trade provenance and Bot independence are
   intentional contracts, not cleanup candidates;
3. the six evidence gaps must receive direct characterization/evidence before a
   dependent behavior-changing Stage and cannot be removed as “probably unused”;
4. the current runtime ownership seed is accepted as the baseline, while exact
   target compose/credential/readiness bindings remain work for `P1-03`.

Approval of this gate freezes the human-readable baseline; it is not approval to
implement, deploy, delete a handler or waive any evidence gap.

**Decision (2026-09-02): APPROVED.** The owner accepted all four assertions. The
six evidence items and concrete scenario records remain mandatory technical
gates; this approval grants no operational or implementation authorization.
