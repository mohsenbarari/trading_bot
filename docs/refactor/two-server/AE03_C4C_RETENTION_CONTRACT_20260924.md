# AE03 C4c: terminal quota receipt retention contract

Status: **DESIGN / NO PRUNER ACTIVATED**. C4b2 can read a strict receipt without mutation, but neither the live Broker nor Supervisor runs that new code. A time limit alone is not authority to delete a receipt: it may be the only durable proof that a Provider invocation returned quota rather than an ambiguous effect.

## Scenario-based rule

| Situation | Required handling |
| --- | --- |
| A receipt has no durably accepted Supervisor wait/disposition | Keep it. The gap between Broker publication and Supervisor acceptance is not an acknowledgement. |
| A matching wait is `WAITING_QUOTA`, `READY_TO_CHECK`, `RESUME_CLAIMED`, or outcome is ambiguous | Keep it regardless of age; report capacity pressure, never silently evict. |
| A matching wait is durably resolved and acknowledged, but the old request/claim/response or another state still references the receipt | Keep it and hard-stop pruning until references are reconciled. |
| A resolved, acknowledged, unreferenced receipt is less than 10 days past the durable acknowledgement time | Keep it for diagnosis. Use acknowledgement time, not file mtime or Provider reset time. |
| A resolved, acknowledged, unreferenced receipt is at least 10 days past acknowledgement | It may be pruned only by a single maintenance owner after strict identity, reference and filesystem checks, unlink followed by directory fsync. Record bounded deletion evidence; a crash may safely leave it intact, never remove an active receipt. |
| The 64-record directory cap is reached before safe pruning is possible | Stop new quota receipt publication with an explicit capacity blocker. Do not raise the cap, choose oldest, or discard an unresolved receipt. |

The 10-day policy applies only to *resolved and acknowledged* diagnostic evidence. Incident-held and ambiguous evidence has no time-based expiry until a separate explicit disposition resolves it. This is not a backup/log cleanup rule and does not permit deleting unrelated files.

## Sequencing and trust boundary

1. **C4c-a, pure classifier:** create a bounded read-only classification from strict receipt + a future typed Supervisor disposition. It returns `KEEP_ACTIVE`, `KEEP_UNACKNOWLEDGED`, `KEEP_AMBIGUOUS`, `KEEP_RECENT`, `ELIGIBLE` or fail-closed `INVALID`; it never unlinks. Fake-clock and malformed/symlink/hardlink tests prove time cannot override references. If C5's disposition schema is not yet frozen, only this document is accepted; do not invent a parallel state authority.
2. **C5 first, durable authority:** the Supervisor persists the seven-field reservation and receipt/request digest, wait lifecycle and final acknowledgement under its existing lock with fsync. It must make STOP and unresolved claims win. A reader must validate the exact receipt and Supervisor identity rather than infer acknowledgement from task success or a missing file.
3. **C4c-b, deletion last:** implement the pruner only after C5's state reader and reference index are accepted. Run it at a proven quiescent ownership boundary, never concurrently with an unbounded Supervisor wake or Broker publication. Strictly scan all bounded entries; check the selected file by no-follow descriptor and full stat/bytes before unlink, check queue and Supervisor references, unlink exact basename via pinned directory descriptor, fsync that directory, and report the exact request ID and acknowledgement digest. A failed check deletes nothing.

The current Broker `_prune_orphan_responses` removes old *response queue files*, not terminal receipts. It must not be re-used as receipt retention authority. C4c acceptance requires tests for publication→wait crash, STOP and restart, active wait older than 10 days, acknowledged receipt younger/older than 10 days, duplicate/invalid references, clock rollback, 64-entry pressure, path swaps, unlink/fsync faults and idempotent restart. No live cleanup starts from this design alone.


## Independently reviewed finite closeout extension (2026-10-20)

The following three frozen texts are adopted together as the finite C4c contract.
Their preserved DATA/NOT APPROVED headings describe their original preparation
status, not this normative adoption. Root and independent nonauthor review
approved the exact R1/NORMAL/cold-binding design pair and correction before
implementation. This append grants no live release or cleanup: an actual
registry-selected Root Assignment -> preaction -> release, all maintenance
holds and every predicate below remain mandatory. Publish this contract append
before the implementing Source under the fresh complete issued maintenance Scope.

Frozen R1 SHA: 75a12854403d1434368859fba1a35f30100f8dc8a5a1dacdfaa22611b7bcffda
NORMAL/UNKNOWN SHA: 99462c761ac68a331781a23dbe7943f3e8a32ea89c3ecd5624ba7e5e6074869c
Cold Root authority SHA: ceb1aaaf478492d0c89df625d8ed887cbcb4ead512ba32a22f6f98f8a60527e2

--- BEGIN APPROVED FROZEN R1 ---
# AE03 retirement extension R1 — DATA ONLY / NOT APPROVED

Preserve original b227ac3b9ea54318c17e2f5e6e92b00e658f26cf248500cc097aefb9cfb33ee0.
R1 contract needs Root+nonauthor approval BEFORE code. No execution authority.

## Authority and maintenance boundary

ONE irreversible certification makes seven internal receipt-read edges archival;
old bytes remain and outside refs block. No second ACK/journal/registry/service/
temp worktree/reset/Provider/parallel owner.

Require actual Supervisor STOPPED/no writer, existing Monitor gate, fresh LIVE
IDLE Broker, existing Supervisor/Broker-client/pipeline/Runner holds in accepted
order. The owned AP STOP is ALREADY recoverably archived by the existing Root
operation: pin/preserve archive+transition receipt. Do NOT require AP STOP present
(STOPPED+AP STOP exits ordinary Broker). Root STOP absent. ANY new root/AP STOP,
signal or monitor/ownership/quiescence drift blocks further unlink. Recheck before
intent, each unlink, completion and finally; never archive/remove a new STOP.

ONLY receipt-maintenance authority; no execution/replay/budget/reservation/Stage/
Task/adoption/start/checkpoint/incident authority. Retained bytes unchanged. Keep
64/32/512/128 bounds; no pressure eligibility or archive cap escape.

## Fixed roles, paths and edges

R=integration; S=shared checkout via existing Assignment resolver;
B=R/.local/two-site-refactor; A=B/autopilot; P=B/provider-broker.
O/C=original/continuation CID; I/Q=original/resume request ID; F=semantic fingerprint;
W/V=actual result Worker/Reviewer IDs; X=wait.quota_attempt.run_id;
T=reservation.task_path; D=historical Assignment ID. DERIVE/cross-bind, not caller lists.

| Retained role | Exact path |
| --- | --- |
| STARTUP | A/runs/O/preparation-startup.json |
| RESERVATION | A/runs/O/broker-reservation.json |
| WAIT | A/runs/O/quota-wait.json |
| CLAIM | A/runs/O/quota-resume-claim.json |
| SNAPSHOT | A/runs/O/quota-resume-lineage-snapshot.json |
| SUBMISSION | A/runs/O/quota-resume-submission.json |
| RESULT | A/runs/C/result.json |
| LINEAGE | A/runs/C/accepted-lineage.json |
| ACK | A/runs/C/final-ack.json |
| TASK | R/T; original RAW bytes, never snapshot reserialization |
| ASSIGNMENT | S/.local/two-site-refactor/assignments/D.md |
| QUOTA_STATUS | B/proposals/X/status.json |
| SEMANTIC_QUOTA | B/semantic-attempts/F.json |
| SEMANTIC_PIPELINE | B/pipeline/semantic-attempts/F.json |
| W_suffix/V_suffix | B/proposals/{W,V}/filename; exact suffix:filename pairs below |
| RELEASE | Root-reviewed exact review-metadata path/SHA pinned by preaction |
| STOP_ARCHIVE/STOP_TRANSITION | exact existing Root archive/receipt pins in preaction |

Actor suffix:filename closed set: TASK:task.json, RESULT:result.json,
STATUS:status.json, PROMPT:prompt.txt, STREAM:stream.jsonl, STDERR:stderr.txt,
SCHEMA:response-schema.json, COMPLETION:transport-completion.json,
DELTA:delta-result.json iff strict SDK requires it. Existing strict codecs apply.
Both semantic roots are distinct: preserve present, require actual route's one,
conflict=>KEEP; no copy/create/migrate. COMPLETE/no-commit has null actors/proposal.
Other optionality follows SDK, not caller flags. Unlisted required dependency
=>KEEP/unsupported, not omission/legacy PASS. Git anchors stay content-addressed.

Collectible EXACTLY two roles: ORIG_RECEIPT=P/terminal-quota/I.json;
RESUME_RECEIPT=P/terminal-continuation/Q.json. Both strict-present before intent.
No directory, queue, retained anchor or other lineage can be collected.
Superseded edges EXACTLY:

```
WAIT /terminal_receipt_sha256 -> ORIG_RECEIPT.raw
WAIT /response_sha256 -> ORIG_RECEIPT.response
SNAPSHOT /expected_wait/terminal_receipt_sha256 -> ORIG_RECEIPT.raw
SNAPSHOT /expected_wait/response_sha256 -> ORIG_RECEIPT.response
LINEAGE /upstream/terminal_receipt_sha256 -> RESUME_RECEIPT.raw
LINEAGE /upstream/response_sha256 -> RESUME_RECEIPT.response
ACK /terminal_receipt_sha256 -> RESUME_RECEIPT.raw
```

WAIT digests identify ORIGINAL; LINEAGE.upstream/ACK identify RESUME. All other
digest/ID/budget joins remain enforced.

## Concrete historical acceptance oracle

Current repository/Scope/refs/controls are separate unchanged drift guards, NOT
history. Pin BEFORE first discovery/SDK read and finally check even on errors.

1. WAIT.repository exact head/status=""/three refs/19 control blobs/old six-field
   Scope. validate_accepted_repository only normalizes shape, not acceptance.
2. Strict R/T RAW SHA matches reservation.task_sha256; canonical Task schema and
   request/claim/SNAPSHOT expected_* + actors agree; source materialization uses
   historical Git, not load_task's live files. Snapshot reserialization proves no
   raw SHA. Missing Task=>KEEP, never reconstruct.
3. parse_assignment_scope_bytes on strict ORIGINAL MD with D/integration;
   recompute canonical-contract+newline+every MD byte SHA, exact old Scope equality
   and validate_task_scope/path rules. Six-field dataclass alone proves nothing.
   Expired now may be valid then: require original valid temporal binding.
4. Read-only Git --no-replace-objects, fixed argv/env, bounded SDK process/time/
   output: verify old source/all19 control blobs, Task source blobs/modes, refs.
   Prove candidate after commit's ancestry/one parent/exact scoped bytes/modes via
   validate_scoped_gemini_chain (distinct sessions), validate_generation_completion,
   validate_scoped_committed_proposal, validate_control_changes_are_proposed.
   Missing policy/qualification/chain/Git=>KEEP, not current-policy historical proof.
5. Reproduce validate_transition predicates on OLD derived frames, not current
   validate_repository: unchanged old Scope/refs, exact commit/changed, CONTINUE
   changes; COMPLETE/no-change null commit/actors/proposal and identical controls.
6. derive_initial_wait_expected(original reservation/receipt,historical repo);
   strict claim/request/SNAPSHOT match. Cross-check
   collect_final_acceptance_upstream_lineage_from_snapshot_locked; only THEN give
   read_final_ack_record_candidate independent transition/context. Its delegated
   fileproof REQUIRES caller oracle; self-consistent result/ACK is insufficient.

Positive current HEAD AND Scope differ. Forged old Scope/MD/Task/source/actor/
commit or missing oracle fails. No restoration/new journal/ACK/fake live proof.

## Outside references and incident release

Fresh RAM index scans ONLY these canonical roots using existing typed codecs:

| Root | Recognition/bound |
| --- | --- |
| P/{requests,claimed} | exact ordinary/resume request schema;32 each |
| P/responses | exact response + authoritative request join;32 |
| P/{terminal-quota,terminal-continuation} | strict receipts;64 each |
| A/runs |512 runs/128 children; fixed lifecycle roles above, strict startup/status, UNKNOWN check records |
| A/status.json, A/accepted_checkpoint.json | existing typed evidence/run/digest pointers; status never proves resolution |
| A/rescope-<64hex> | existing intent1..3/receipt1..3 schemas;512 epochs, no arbitrary recursion |
| A/adoptions | existing rescope-epoch JSON codec/bounds;512 entries |
| B/semantic-attempts, B/pipeline/semantic-attempts | matching F denial records preserved, never expire RESERVED |

Join typed O/C/I/Q/F,digests,evidence pointers; no prose grep/broad archive scan.
Outside edge blocks. Unknown lifecycle/schema, duplicate/conflict/unbound response/
link/drift/cap/uncertainty=>KEEP, not zero refs. Pin/recheck dirs before effect and
finally; four holds exclude writers.

No current incident-clear codec; absence/status proves nothing. Require Root
release in existing review metadata with independently approved SHA pinned by
immutable canonical Root Assignment/preaction binding. Ownership/issuer text is
NOT unforgeable origin or caller CLEAR authority. Runtime cannot mint release;
actual incident hold needs separate Root disposition. Unknown/drift=>KEEP.

Release <=4096 bytes, exact keys: schema_version="1.0",
record_kind="ROOT_QUOTA_RETIREMENT_RELEASE", original_controller_run_id,
continuation_controller_run_id, original_request_id, resume_request_id,
semantic_task_fingerprint, final_ack_sha256, retained_anchor_sha256, archived_stop,
incident_disposition="CLEAR_NO_EXISTING_HOLD", authorized_at_utc.
archived_stop exact {relative_path,sha256,fingerprint}, matching pinned archive AND
transition receipt in preaction. Release IDs/ACK/whole-anchor digest must match;
UTC<=now, no rollback. Ordinary/runtime callers cannot mint approved bindings.

## Exact new records, authority predicate and crash handling

Original run directory, strict unique canonical UTF-8 JSON sorted/compact/
allow_nan=False, exact types/no bool-as-int, existing ID/SHA/UTC rules. Pinned0700
dirs,0600 regular euid/nlink1/no-follow files; bounded reads/full FD+path pre/post
fingerprints and both closes; preserve primary errors through cleanup.

Intent A/runs/O/quota-history-retirement.json <=16384 bytes, exact keys:
schema_version="1.0", record_kind="QUOTA_HISTORY_RETIREMENT_INTENT",
original_controller_run_id, continuation_controller_run_id, original_request_id,
resume_request_id, semantic_task_fingerprint, acknowledged_at_utc, certified_at_utc,
release_sha256, retained_anchor_sha256, accepted_binding, retained, collectible,
superseded_edges. accepted_binding exact {source_head,head_after,task_sha256,
assignment_sha256,accepted_state}; retained exact entries {role,relative_path,sha256},
sorted(role,path), <=48, fixed required roles ONLY. retained_anchor_sha256 hashes
canonical retained array EXCLUDING RELEASE to avoid circularity; release_sha256
binds RELEASE separately. collectible EXACTLY the two entries {role,relative_path,
sha256,response_sha256,fingerprint}; fingerprint exact {dev,ino,mode,uid,nlink,size,
mtime_ns,ctime_ns}. superseded_edges EXACTLY seven triples above. Constructor
DERIVES values, accepts no leaf list; unsupported/oversize closure KEEP, no truncation.

Completion A/runs/O/quota-history-retirement-complete.json <=2048 bytes, exact keys:
schema_version="1.0", record_kind="QUOTA_HISTORY_RETIREMENT_COMPLETE",
intent_sha256, completed_at_utc, deleted_receipts. deleted_receipts equals the
COMPLETE collectible projection {role,relative_path,sha256}, not a success subset.

No-overwrite: temp write+file fsync -> no-replace link -> cleanup+directory fsync
-> strict readback AND anchor/release/frame recheck. ONLY this successful full
predicate establishes irreversible certification BEFORE ANY unlink. Post-link
failure retains ambiguous intent, unlinks NOTHING in that invocation. Future
maintenance must revalidate/re-fsync/readback before deriving authority. Presence,
parse/hash success or caller 'published' flag alone never suffices.

UNRETIRED -> CERTIFIED_RETIRED_PENDING -> CERTIFIED_RETIRED_COMPLETE; no reverse,
reset, overwrite or renewal. Before first intent BOTH receipts strict-present,
whole oracle valid, no outside ref/incident, now>=ACK UTC+10*86400; time uncertainty
KEEP. Prune in role/path order: target fingerprint/raw SHA + fresh index/boundary
checks -> exact dirfd unlink -> directory fsync EACH. Failed precheck deletes
nothing. Post-unlink error may remove leaf: PENDING/ambiguous, not rollback/complete.

Restart maintenance revalidates intent/anchors/release/boundary/index. Present
target must match certified identity/bytes. Listed absence means ONLY intended
deletion reconciliation: fsync pinned directory, finish remaining leaves, require
all absent+durable, then publish completion by same strict predicate. Unlisted
absence/missing intent/changed anchor/new ref/new STOP blocks. No mutable progress
journal. Complete -> strict no-op; reappearing target blocks. completion UTC>=
certification UTC and no rollback. Runtime never prunes or reconciles pending.

Recovery validates intent/release/anchors and completion when present; reports
distinct CERTIFIED_RETIRED_PENDING/COMPLETE, never pretends missing receipt was
read. Pending remains visible/excluded from active count, not execution permission.
Unretired strict ACK/lineage readers unchanged.

## Denial coverage, files and acceptance

Central narrow retired-ID/F guard is reused but must cover BEFORE EFFECT:
normal+maintenance _submit_request; Broker _claim_next, serve/claim recovery,
execute_pipeline, original reservation and both terminal publications;
both public continuation-admission paths, execute_intent_bound_continuation,
actual role dispatch. Direct/public entrypoints cannot bypass guard. Preserve
semantic denial; renamed task/request/prefix/CID same identity gets zero Provider.
Malformed retirement metadata never means 'not retired'. Reached tests prove it.

Paths/history/UNKNOWN requirements remain b227 section4; only new module
two_site_quota_retention.py. Approved
tracked C4c contract BEFORE pruner code. Preserve golden real-fixture factory AST.
Exact50 native paths in b227 section6 (actual761N28+22), plus other767 gates; no
replacement. New test<=24000; physical fit/acceptance unclaimed. History2+active0/1,
active2 block; all corruption/custody/counterfactual proofs. UNKNOWN immutable
initial/budgets, five15/30/60/120/240 local checks, no sixth/execution/reset inference;
all original clocks/restart/STOP/fault proofs remain mandatory.

Retention actual hermetic SDK certification+unlink+cold retired recovery; external
Provider outcomes only simulated. Release fixture exercises real binding checks
with explicit test authority inputs, never claims operational Root approval.
9d/10d, held/unknown/outside/duplicate refs/cap64/clock; NEW STOP every boundary;
all path/FD/link/replacement/close faults; every intent/unlink/fsync/completion
crash, restart/no-op/missing/bad proof/new ref/reappearing/unlisted leaf; unchanged
anchors/budgets/refs, zero execution/Stage. Exact+alias negatives reach ALL entry
gates. SAME full witness -> reached mutation -> exact AssertionError/DIDNOTRAISE
-> one finally restore -> SAME witness+healthy. No changed input/oracle or fake live proof.

--- END APPROVED FROZEN R1 ---

--- BEGIN APPROVED NORMAL/UNKNOWN ADDENDUM ---
# AE03 fixed NORMAL profile and UNKNOWN actuator — DATA ONLY

NOT ISSUED. Root+independent review and fresh Scope at accepted consumer/AE00/B3
base BEFORE authoring. No live release/prune/start/Provider/CP/control authority.

Preserve these exact historical artifacts:
- C4c R1: 75a12854403d1434368859fba1a35f30100f8dc8a5a1dacdfaa22611b7bcffda.
- Original design: b227ac3b9ea54318c17e2f5e6e92b00e658f26cf248500cc097aefb9cfb33ee0.
- UNISSUED767A raw MD: 74ea1ecce03d8fc159372b985ea12b721b7299d6a3a82abaa5f351f0cdf144c8.
- 767A peer/addendum: 80b315187681ec6cd5bca374229180afebaacccd84a86491908f170668b97e0c.

Do not issue incomplete767A. Add ONLY three existing Source paths beyond767:
continuation_terminal_storage, quota_supervisor_resume_context, semantic_attempts.
No forwarding cascade/framework/service/cohort/Worker2 expansion.

## Fixed ContextVar contract

In continuation_admission add ONE private ContextVar and
normal_pipeline_context(root): fixed NORMAL=.local/two-site-refactor/pipeline,
exact canonical root, no caller namespace/map/mode/request/env. Selection is NOT
execution/acceptance authority; existing identity/STOP/Scope/claim/custody gates stay.

Validate root/module before effects. Same-root nesting allowed; cross-root fails.
Finally reset token on ALL exits/BaseException, preserving primary error. No SDK
global mutation or permanent configuration.

INSIDE NORMAL it is authoritative: forwarded/default/explicit pipeline_dir=B
cannot downgrade. Omitted and explicit literal B are indistinguishable; do NOT
claim preserved explicit-B override inside this NEW scope. Recognized B/NORMAL
resolve NORMAL; unexpected namespace/root fails. OUTSIDE preserve legacy B default,
explicit arguments/positional ABI/returns. No discovery/search/fallback/copy/reset.

Reject distinct scripts.two_site_quota_continuation_admission and bare
two_site_quota_continuation_admission objects at entry AND resolution. No foreign
module replacement or same-filename identity assumption. Launchers set PYTHONPATH
to integration root; prove package+actual CLI composition and mixed-alias hard stop.

Actual scopes:
1. Broker execute_intent_bound_continuation façade covers WHOLE dispatch,
   verifier/W0/W1/W2/remainders/latechecks. Executor is synchronous; heartbeat alone
   uses another thread.
2. Standing bridge covers collection->claim/enqueue->READY rechecks AND generator
   enter/yield/exit, not merely generator creation.
3. Processes enter independently; ContextVars do NOT cross processes. Actual
   callbacks are synchronous; no async/new-thread/deferred-after-exit claim.

All18 admission sites use the resolver; replace resume_context's hardcoded B with
SAME helper. Publisher's B default cannot undo scope. No pure-codec/mode fields.
History/maintenance explicitly select bound old profile, NEVER ambient authority.

## Mandatory denial and historical custody

reserve_semantic_attempt rejects exact retired F BEFORE publication, including
direct run_pipeline: legacy B reservation alone cannot protect B/pipeline. Preserve
reservations; no copy/reset/renewal/global block for unrelated histories. Ambiguous
affected proof grants no permission.

Direct publish_terminal_continuation_record needs pre-effect retired-Q denial;
it bypasses admission/Broker. Keep all R1 entrypoint/exact+alias zero-Provider
guards; absence/terminal success/status never proves acceptance or permission.

R1's seven original-versus-resume receipt edges, whole outside-reference index,
incident UNKNOWN=>KEEP, >=10days ACK age, archived-owned-STOP/newSTOP precedence,
four holds/fresh-live-IDLE Broker and irreversible intent/completion crash rules
are unchanged. Full fsync+strict readback+anchor recheck predicate precedes unlink;
pending intent permits ONLY explicit maintenance reconciliation, never execution.

Original Task=bound RAW R/task_path SHA, not current Task/reserialized snapshot.
Old source/controls/refs/proposal/transition need bounded independent Git/SDK;
Scope digest includes ORIGINAL MD, not six fields. Current HEAD/Scope only guard
drift. Missing old oracle=>KEEP/hard stop. Normal positive uses actual run_pipeline
reservation producer, not B-only seed/copy/root guess. Preserve legacy proofs and
golden final factory AST.

Root CLEAR release stays separately reviewed, SHA-pinned by canonical immutable
Assignment/preaction and bound to IDs/F/ACK/whole anchors/STOP archive. Ownership/
issuer/caller CLEAR is not provenance. No release here; real holds need separate
disposition. Historical profile is bound/custody checked, not store-existence choice.

## UNKNOWN actual actuator (no shell Source change)

QUOTA_READY explicitly means START EXISTING SUPERVISOR QUOTA BRANCH, not Provider
permission. Preflight remains readonly: no check publication/claim/budget/launch/probe.

Actual path: Monitor WAITING_QUOTA+optin -> strict read-only due preflight -> exact
QUOTA_READY output -> existing tmux start --quota-wait-enabled -> run_loop's
existing pending-rescope-intent branch -> resume_due_quota_cycle_locked.
Shell initializes provider_probe=false and does not enable it for quota_wait_due;
no paid Provider probe or Coordinator health cycle is added. No .sh edit/new flag.

Supervisor freshly rechecks root/Task/Scope/refs/control/STOP/claim absence and
derived due identity under its existing lock. For immutable UNKNOWN reset kind,
perform ONE local custody/binding health observation, durably publish it, update
display projection and RETURN before resume claim, allocation, run_cycle, Provider
or Coordinator. It cannot become KNOWN or infer timezone/reset/provider health.
Known-reset route remains separate and independently gated.

Preserve initial v1/schedule/claim/snapshot/resume budget. <=5 immutable checks bind
wait SHA/ordinal/prior digest/checked UTC/local outcome. Durable evidence ONLY
advances projected count:15/30/60/120/240min, one late tick/check, no catch-up.
Rollback/future/gaps/drift/ambiguous publish block; five exhausted/no sixth.
Status/Monitor telemetry is not authority.

Proof closes actual shell/launcher seam -> real run_loop, not mocked due only.
External launch may be controlled; scheduling/storage/bridge genuine. Prove readonly
preflight, one check/launch, zero execution/claim/queue through STOP/race/restart/
exhaustion. No manufactured live incident.

## Exact final Source and test amendment

AE03 Source set (12; NEW only quota_retention):
```
scripts/two_site_quota_wait_recovery.py
scripts/two_site_quota_wait_storage.py
scripts/two_site_quota_wait_due.py
scripts/two_site_quota_supervisor_due_inspect.py
scripts/two_site_quota_supervisor_bridge.py
scripts/two_site_autopilot_monitor_preflight.py
scripts/two_site_quota_continuation_admission.py
scripts/two_site_provider_broker.py
scripts/two_site_quota_continuation_terminal_storage.py
scripts/two_site_quota_supervisor_resume_context.py
scripts/two_site_semantic_attempts.py
scripts/two_site_quota_retention.py
```
Other existing whole-closeout Source paths remain:
```
scripts/two_site_codex_autopilot.py
scripts/two_site_cycle_metrics.py
scripts/two_site_accepted_work_cost_report.py
scripts/two_site_cycle_tools.py
```
Total16 Source paths, not34. No AE03 forwarding edits to other SDK files. Append
approved R1+this/addendum to tracked C4c contract BEFORE code; clarify quota-recovery
contract's health/readiness distinction. All other Scope paths/caps stay frozen.
Additive tests/test_two_site_quota_finalization_real_fixture.py; new collected:
test_two_site_quota_wait_recovery_final_ack_history.py,
test_two_site_quota_unknown_health_checks.py, test_two_site_quota_retention.py.
Each NEW collected test<=24000; existing limits unchanged. Actual complete Source/
context/review sizing and independent implementation/native acceptance still due.

## Exact AE03 whole-module native union (57)

```
tests/test_two_site_quota_supervisor_resume_context.py
tests/test_two_site_quota_supervisor_resume_submit.py
tests/test_two_site_quota_supervisor_due_inspect.py
tests/test_two_site_quota_continuation_broker_recovery.py
tests/test_two_site_quota_stop_pre_provider_guard.py
tests/test_two_site_supervisor_preparation_startup.py
tests/test_two_site_quota_run_cycle_finalization.py
tests/test_two_site_quota_broker_serve_gate.py
tests/test_two_site_autopilot_monitor_preflight.py
tests/test_two_site_autopilot_monitor_wiring.py
tests/test_two_site_provider_broker.py
tests/test_two_site_quota_supervisor_bridge.py
tests/test_two_site_quota_wait_startup_hook.py
tests/test_two_site_quota_finalization_real_fixture.py
tests/test_two_site_quota_finalization_stop.py
tests/test_two_site_quota_w2_common_root_crash_no_replay.py
tests/test_two_site_quota_continuation_terminal_storage.py
tests/test_two_site_quota_continuation_terminal_storage_tamper.py
tests/test_two_site_quota_continuation_terminal_storage_faults.py
tests/test_two_site_quota_continuation_broker_admission.py
tests/test_two_site_quota_resume_role_dispatch.py
tests/test_two_site_provider_broker_client_custody.py
tests/test_two_site_codex_autopilot.py
tests/test_two_site_quota_supervisor_hook.py
tests/test_two_site_autopilot_monitor.py
tests/test_two_site_autopilot_monitor_broker_preflight.py
tests/test_two_site_autopilot_tmux_stop.py
tests/test_two_site_autopilot_checkpoint.py
tests/test_two_site_quota_wait_schedule.py
tests/test_two_site_quota_wait_record.py
tests/test_two_site_quota_wait_storage.py
tests/test_two_site_quota_wait_expected.py
tests/test_two_site_quota_wait_sources.py
tests/test_two_site_quota_wait_intent.py
tests/test_two_site_quota_wait_admission.py
tests/test_two_site_quota_wait_admission_faults.py
tests/test_two_site_quota_wait_recovery.py
tests/test_two_site_quota_wait_due.py
tests/test_two_site_quota_wait_bound_smoke.py
tests/test_two_site_provider_broker_claim_durability.py
tests/test_two_site_provider_broker_claim_recovery.py
tests/test_two_site_provider_broker_terminal_publish.py
tests/test_two_site_provider_broker_terminal_read_bytes.py
tests/test_two_site_provider_broker_terminal_payload.py
tests/test_two_site_provider_broker_terminal_record.py
tests/test_two_site_quota_continuation_admission_storage_integration.py
tests/test_two_site_semantic_attempts.py
tests/test_two_site_quota_wait_recovery_final_ack_history.py
tests/test_two_site_quota_unknown_health_checks.py
tests/test_two_site_quota_retention.py
tests/test_two_site_proposal_pipeline.py
tests/test_two_site_proposal_pipeline_binding.py
tests/test_two_site_proposal_pipeline_guards.py
tests/test_two_site_proposal_pipeline_session_retry.py
tests/test_two_site_quota_broker_client.py
tests/test_two_site_quota_broker_client_faults.py
tests/test_two_site_quota_resume_submission_intent.py
```

Retain full R1 fault/native/SAME-mutant requirements, plus NORMAL callback lifetime,
legacy outside/default, explicit-B nondowngrade, root nesting/reset/BaseException,
canonical/bare alias and real CLI joins. This is 50+7, NOT forwarding design's73.
Union other767 component gates without replacing selectors. No acceptance, live
health, savings, cleanup or activation follows from this DATA artifact.

--- END APPROVED NORMAL/UNKNOWN ADDENDUM ---

--- BEGIN APPROVED COLD ROOT AUTHORITY CORRECTION ---
# C4c cold Root authority — finite DATA contract correction

This corrects only the approved R1's missing durable Root-release provenance.
It grants no live release/prune, Source/Scope/Task/start/Provider authority.
Preserve R1 75a12854403d1434368859fba1a35f30100f8dc8a5a1dacdfaa22611b7bcffda,
NORMAL/UNKNOWN addendum99462c761ac68a331781a23dbe7943f3e8a32ea89c3ecd5624ba7e5e6074869c,
and data-only preparation preactionR1 afaf8a790764a35465b12db7c0749bff5d62baf67bf8db13a10209aaa51c11c9.
All other clauses/bounds/roles/57 whole-native gates remain.

## Durable authority and noncircular construction

Add exactly two retained roles within the unchanged48-role/16KB intent bounds:
RETIREMENT_ASSIGNMENT and RETIREMENT_PREACTION. Existing ASSIGNMENT remains the
ORIGINAL business Scope; accepted_binding is unchanged. Add one intent key:
retirement_authority = {assignment_id,assignment_sha256}, both strict existing
Assignment-ID/typed-SHA rules. It is a binding, never caller-granted permission.

RETIREMENT_ASSIGNMENT is the immutable canonical shared-checkout
.local/two-site-refactor/assignments/<assignment_id>.md, resolved by the EXISTING
Assignment resolver. Both ASSIGNMENT roles' relative_path is relative to the
canonical shared checkout S; other roles remain relative to integration R.
The closed role determines the base; no caller-selected root or '..' path.
Full raw bytes/hash/stat plus parsed typed digest are checked. Validate historical
Root Scope temporal validity at certified_at_utc, not current wall-clock expiry.

Its MD contains exactly ONE line with prefix ROOT_QUOTA_RETIREMENT_PREACTION_V1:
followed by canonical compact strict JSON {relative_path,sha256}.
Reject missing/duplicate/malformed/unknown family-version markers. Path is one
canonical .local/two-site-refactor/reviews/<safe-basename>.json under R.
RETIREMENT_PREACTION is that exact read-only metadata file, <=4096 bytes,
strict nofollow/regular/euid/singlelink/full FD+path custody, unique canonical JSON.

Preaction has EXACT keys:
schema_version="1.0",
record_kind="ROOT_QUOTA_RETIREMENT_PREACTION",
assignment_id,maintenance_head,
original_controller_run_id,continuation_controller_run_id,
original_request_id,resume_request_id,semantic_task_fingerprint,
final_ack_sha256,retained_anchor_sha256,
release={relative_path,sha256},
authorized_at_utc.
IDs/SHA/UTC use existing strict rules. Release is the existing exact <=4096-byte
ROOT_QUOTA_RETIREMENT_RELEASE metadata role; no new incident-clear codec or ACK.
Preaction planned assignment_id equals the Scope ID. maintenance_head is the
actual clean Root-maintenance Git head and Scope base; validate its bounded Git
existence/ancestry on cold read, separate from OLD business source_head.
Preaction MUST NOT contain the same MD's raw/typed SHA: MD already pins it.

Compute retained_anchor_sha256 over sorted retained entries EXCLUDING ALL THREE
authority roles RELEASE, RETIREMENT_ASSIGNMENT, RETIREMENT_PREACTION; preserve
all business/history/STOP anchors. All three excluded rows are still retained,
independently pinned and revalidated, never omitted or collectible.
Finite order: core business+existing STOP anchors -> Root RELEASE -> Root
PREACTION -> canonical Root Assignment MD/typed digest + actual registry
selection -> strict constructor/immutable intent. This avoids both hash cycles.

## Before certification and cold history

Constructor derives ACTUAL registry-selected Root Scope from the existing
resolver/current canonical repository, pins MD and registry before first use
and rechecks on every effect/finally. Caller Scope, issuer text, UID or CLEAR
flag alone is not authority. Resolve preaction from actual MD marker, RELEASE
from preaction; match whole O/C/I/Q/F/finalACK/base-anchor/STOP/UTC bindings.
Preaction authorized_at_utc <= certified_at_utc, Scope valid then, and source/
current repository/holds/STOP/reference/release checks remain mandatory.

Cold history derives canonical MD from intent.retirement_authority, revalidates
full raw+typed MD, marker -> preaction -> RELEASE, canonical roots/retained rows,
old business oracle and certification-time bindings. Never use current Scope
as historical approval, reconstruct missing bytes, trust arbitrary self-supplied
paths, or weaken current drift guards. This is explicit Root-publication
authority in the existing protected canonical metadata namespace, NOT a claim
of cryptographic unforgeability against a privileged filesystem attacker.
No new registry/ACK/journal/service/crypto or automatic Scope publisher.

Before pruner Source, append this independently approved correction together
with prior approved contracts to the tracked C4c contract. Data-only drafts may
carry it only after exact Root+nonauthor design approval. Actual implementation
still needs fresh complete issued Scope/current-HEAD/dependency adoption,
whole independent review and complete PRE+POST/exactGit/four holds.

## Additional mandatory proofs

Constructor must reject caller/self-selected/fake or nonselected Root Scope.
Cold recovery after a genuinely different current Scope must succeed ONLY
through retained canonical Root MD/preaction/release; missing/replaced/wrong
marker/path/SHA/typed Scope/time/Git/ID/ACK/anchor/STOP binding fails closed.
Prove original business binding unchanged, all three authority rows preserved,
base-anchor exclusions exact, no circular digest/reconstruction/current-Scope
shortcut, full intent<=16384 or KEEP, and existing48-role bound.
Retirement execution denial/crash/unlink/phase/native requirements are unchanged.


--- END APPROVED COLD ROOT AUTHORITY CORRECTION ---
