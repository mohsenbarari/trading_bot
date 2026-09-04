# Feature Parity Contract

Status: historical owner review accepted; static inventories verified and scenario contracts corrected on 2026-09-03; Codex Final Review of the correction commit is open

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
| FastAPI router decorators | 213 across 18 mounted router modules | included below | refreshed |
| main FastAPI app decorators | 4 | included below | refreshed |
| total live FastAPI decorators | 217 | 217 function/path references | refreshed; six-item queue closed |
| Bot handler decorators | 203 across 18 active/conditional modules | 203 function/filter references | refreshed; six-item queue closed |
| frontend router paths | 30 | 30 direct route-name references | refreshed |
| background authority entries | 15: 14 required classes plus local sync worker | 15 direct name references | refreshed |
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
| `inventory/scenario-records.json` | compact source: family×operation_kind contracts including shadow-observation exceptions, 465-seed mapping, 180 admission cells, 180 composed_origin_matrix rows with one offer_origin per key, 72 failure cells injected on the family-owned path or NOT_APPLICABLE |

Surface inventories keep their original `generated_from_commit`
`19087ff0686c9260b55f1c6c35782b758be6673e` and record
`verified_against_commit` `aa2c06a573dfd60051e142146f55d7965f2242a7`. They are
not claimed as a fresh regeneration. A later code change that adds/removes a
route, handler or job must refresh verification and fail CI if the new item has
no family/behavior seed.

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
case.” Every row below expands across these definite decisions: owner and tier-1
are ELIGIBLE; tier-2 is ELIGIBLE_FOR_REQUEST_ONLY on Web and INELIGIBLE on Bot;
accountant is INELIGIBLE; admin is ELIGIBLE with command or Bot-role guards;
unlinked Bot is INELIGIBLE. Cover normal/overtime/closed time and
success/reject/retry/concurrent paths.

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

No surface is unclassified. The six former empty-`test_refs` items now have
direct characterization in `tests/test_p1_00_characterization_six_gaps.py`:

- API: the admin test-Web-Push command;
- Bot: one admin broadcast group-toggle, one overtime-preference entry action,
  one conditional publisher public-trade callback and two trade wizard back
  actions.

Those handlers remain preserved product surfaces. The new tests record current
behavior only; they are not permission to change or delete them.

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

Empty per-row Cartesian records are forbidden. The reviewable source is
`inventory/scenario-records.json` schema `two-server-refactor.scenario-records.v2`:

- `behavior_contracts` hold the real current fields (`preconditions` through
  `failure_cases`, evidence and tests). Irrelevant fields are
  `NOT_APPLICABLE` with a reason, never empty.
- `variants` / `composed_eligibility` bind persona, tier, time, request origin,
  telegram-link, outcome and independent offer-origin provenance to a
  `contract_id` with a definite eligibility decision.
- each of the 465 surface seeds maps to exactly one family×operation_kind
  contract; a `MENU_NAV` / `READ_QUERY` seed must not share a `BROADCAST` or
  `CRUD_MUTATION` contract, and a send-document seed must not share a no-send
  `READ_QUERY` contract. Seed-specific evidence is the inventory `test_refs`,
  not the schema meta-test.
- Cartesian expansion, if needed later, is generated outside Git.

```yaml
contract_id: BC-OFFER-CRUD_MUTATION-V1
family: OFFER
operation_kind: CRUD_MUTATION
mutation_class: MUTATION_OR_COMMAND
preconditions: {status: APPLICABLE, rules: [...]}
input: {status: APPLICABLE, rules: [...]}
response: {status: APPLICABLE, rules: [...]}
db_mutations: {status: APPLICABLE, rules: [...]}
events_outbox: {status: APPLICABLE, rules: [...]}
side_effects: {status: APPLICABLE, rules: [...]}
ordering: {status: APPLICABLE, rules: [...]}
timeouts_retries: {status: APPLICABLE, rules: [...]}
idempotency_key: {status: APPLICABLE, rules: [...]}
failure_cases: {status: APPLICABLE, rules: [...]}
reference_evidence: {status: EVIDENCED, sources: [...]}
regression_tests: [existing behavioral tests]
composed_eligibility.PERSONA-CUSTOMER-TIER2.WEBAPP.TIME-NORMAL: ELIGIBLE_FOR_REQUEST_ONLY
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
| manual resolution of 6 evidence-review items | `COMPLETE — characterization linked` |
| current runtime child/poller/timer owner seed manifest | `COMPLETE` |
| exact target compose/credential/readiness binding | future `P1-03` |
| persona/tier/time/failure scenario records | `IN_PROGRESS` under `WA-P1-00B-FREEZE-20260904-001` — family overlap/reload including MSG two-row overlap; one hundred twenty-seven exact handlers evidenced and 338 seeds High; channel-trade mocked execute not evidenced; origin SUCCESS/policy/notification/side-effect-retry remain High; Telegram unlinked entry evidenced; 3 Web unlinked-Bot `NOT_APPLICABLE`; `target_result NOT_RUN`; `P1-00b` not `COMPLETE` |
| owner approval of human-readable scenario contract | `COMPLETE — 2026-09-02` |
| deploy/restart/backup/restore duration (`DR-015`) | `DEFERRED_BLOCKER` / `DPL-1` / `NOT_OBSERVED` |
| current-vs-target differential execution | future `P1-06` |

`P1-00` becomes complete only after Codex approves the exact commit whose
amended exit gate has no unresolved High. Leftover current-behavior freeze is
the new non-implementation Stage `P1-00b`, a prerequisite of `P1-01`, `P1-04`
and `P1-06`. `P1-06` later proves the target against this frozen contract; it
does not invent the contract after implementation. Assignment
`WA-P1-00-SCOPE-20260903-002` authorized adding `P1-00b` to
`execution-order.yaml`. Leftover freeze executes under
`WA-P1-00B-FREEZE-20260904-001`. Do not mark `P1-00b` `COMPLETE` in a Worker
commit.

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
