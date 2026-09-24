# AE03: quota recovery contract (design, not activation)

Status: **DESIGN / NOT IMPLEMENTED** at `dc058d7b`. The [AE plan](AUTOMATION_EFFICIENCY_PLAN.md) remains authoritative. No change to `main`, production, DNS or Web Writer is authorized here.

## The boundary that matters

`d8462a89` classifies only one complete, model/session-bound Gemini error stream with zero reported usage and no response/tool event. `8b4f1c32` records that evidence in Runner status; `dc058d7b` carries it into Pipeline's `TASK_BLOCKED` output. This proves at most that **one Gemini invocation** did not return work. It does not prove that the whole Coordinator cycle, Broker claim, semantic reservation or Git state is effect-free. Quota evidence is therefore observation, not a replay permit.

Today the Pipeline reserves a semantic fingerprint before Provider contact and forbids the same fingerprint forever through the normal submit route. Broker claims a request before execution, converts an interrupted claim to an ambiguity error on restart, and removes a terminal response after the client reads it. Supervisor publishes a no-overwrite RESCOPE intent before Codex starts. Any quota wake design must account for all four facts; deleting an intent/reservation or calling normal submit again is not a solution.

## Scenario contract

| Scenario | Required result |
| --- | --- |
| Worker/Reviewer returns a complete zero-usage quota error; Broker delivers a terminal bound Pipeline response; Git/refs/task stay identical | Persist one private `WAITING_QUOTA` receipt linked to that response and the existing semantic reservation. No new Provider call before the reset/check time. |
| Reviewer hits quota after Worker proposal and pre-review tests passed | Resume **Reviewer only** using the exact validated Worker result, source snapshot, test receipt and remaining correction budget. Do not rerun Worker or reset the semantic reservation. |
| Runner writes quota status but Pipeline/Broker dies before a terminal Broker response is durable | The claim is **ambiguous**. Do not infer safety from Runner status alone, do not replay, and report `BLOCKED_AMBIGUOUS` with the exact request/run identity. |
| Broker delivered a terminal quota response, then Coordinator or Monitor restarts | Recover the original terminal response and its bound wait receipt. Do not rerun Codex task selection or create another semantic reservation. |
| Two monitor ticks reach the reset time together | One process obtains the existing lock and atomically claims one resume attempt. The other reports already claimed; it never calls Provider. |
| STOP arrives during wait, or HEAD/assignment/protected refs/control bytes/task SHA drift | STOP wins. Drift hard-blocks the old task; neither reset nor a healthy process silently changes its binding. |
| Reset is only `8:09 AM`, or the wall clock jumps | Do not guess UTC. Record a bounded next-check schedule and remaining probe budget; uncertain clock/probe result never authorizes replay. |
| Crash after resume claim but before a terminal result | Treat that resume as in-flight ambiguity. Do not clear the claim or reset the retry budget automatically. |

## Proposed durable protocol and order

1. **Terminal response custody.** Before a Broker response can be acknowledged/pruned, persist a bounded immutable terminal receipt binding request ID, exact request/task SHA, run prefix, controller/assignment, source HEAD, response digest/state and outcome time. Keep the full bounded response recoverable until acknowledgment; apply explicit retention after acknowledgment, never while active or incident-held. `BROKER_ERROR` and an interrupted claim are not quota terminal receipts. A terminal `TASK_BLOCKED` response is eligible only when its strict quota evidence matches the blocked Runner run and no proposal/result exists for that invocation.
2. **One wait record.** Publish it atomically under the existing private state tree with schema version, semantic fingerprint, task/source/assignment/control/ref bindings, terminal receipt digest, role/model/session, `KNOWN` reset UTC or `UNKNOWN`, next-check time, bounded probe/resume budget and lifecycle state. No prompt, provider error prose, secret or raw stream is copied. New/invalid/symlink/hardlinked/oversize records fail closed; never overwrite to manufacture a fresh budget.
3. **Explicit resume route.** Normal `submit` continues to reject a duplicate semantic fingerprint. A separate Broker/Pipeline resume operation accepts only the exact validated wait record and prior reservation, uses a new unique invocation/run ID within the **same lineage**, and atomically records `RESUME_CLAIMED` before contacting Provider. It cannot change task, scenario, sources or allowed files. An unresolved old claim or result forbids resume.
4. **Supervisor transition.** A validated terminal quota disposition must settle the old pre-model intent explicitly; it is not a RESCOPE success or a clean new epoch. On wake, the Supervisor deterministically resumes the bound Pipeline request and gives Codex only the resulting exact proposal/review bundle for final semantic acceptance. Broker rejects ordinary task submission while that resume disposition is active. Existing `CONTINUE`/`COMPLETE`/RESCOPE contracts and STOP priority remain intact.
5. **Clock and monitor.** A known timezone-qualified reset may schedule one wake no earlier than reset plus a small safety margin. An unknown reset uses a documented bounded 15/30/60/120/240-minute health-check sequence; no full Coordinator cycle is used as a probe. Monitor reads only the validated wait record, checks the current lock/process/STOP/ref state and reports `WAITING_QUOTA`, `READY_TO_CHECK`, `CLAIMED` or `BLOCKED_AMBIGUOUS`. A stale heartbeat alone never kills a live owner.

The resume route is deliberately **not** implemented by the current observational Pipeline output. Introducing a `WAITING_QUOTA` label before steps 1–4 would be a false safety claim.

## Implementation and acceptance order

1. Freeze exact receipt/state schemas and the event boundaries above; add pure validation and fake-clock tests first.
2. Add Broker terminal-response custody with crash-before/after-publication and retention tests, without changing normal submit behavior.
3. Add same-reservation resume under the existing locks, with duplicate wake, identity drift, ambiguous claim, no-output and terminal-response tests. No new semantic alias.
4. Add Supervisor quota disposition and Monitor reporting/wake; test STOP, restart, known/unknown reset, timezone and clock jumps. Existing full RESCOPE matrix must continue passing.
5. Only then run one necessary live smoke, inspect exact receipts/commit/no-duplicate outcome, and collect comparable 3/10/20-unit cohorts. Missing real quota evidence keeps the quota branch's live effect `UNKNOWN`; synthetic tests do not establish provider behavior or token savings.

If any step cannot prove a unique terminal response and unchanged repository identity, hard-block the affected task while other **dependency-independent** work may continue under the approved Stage DAG. This favors a visible bounded stop over replaying an unknown effect.
