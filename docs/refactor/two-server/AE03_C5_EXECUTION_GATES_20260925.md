# AE03 C5: durable quota wait and no-replay execution gates

Status: **IMPLEMENTATION PLAN / NO LIVE WAKE**. C4b2 reads an old receipt only if a separate caller provides seven trusted reservation fields. The current Supervisor run record does not persist task path/SHA or run prefix, and its `status.json` is heartbeat-overwritten. Therefore C4b2 cannot yet be called from Supervisor after a crash. Deriving the missing fields from a receipt would defeat the independent binding.

## Concrete sequence and crash semantics

| Boundary | Required durable evidence | If interrupted |
| --- | --- | --- |
| Before Broker queues a cycle request | One no-overwrite, fsynced reservation in that Supervisor run directory binding the seven C4b2 fields and exact request ID; validate run directory/owner/mode and active startup identity. Maintenance requests retain their separate existing path. | A reservation with no terminal receipt is ambiguous, never a replay permit. |
| Broker returns strict terminal quota | Immutable Broker receipt from C1–C4b2, exact seven-field match, request ID and response digest, unchanged task/assignment/HEAD/refs and bound Runner status witness. | Missing/conflicting receipt or claim hard-stops. |
| Supervisor accepts quota disposition | Under existing Supervisor lock, publish a versioned durable `WAITING_QUOTA` record that binds reservation, terminal receipt SHA, old semantic fingerprint/RESCOPE intent, role/model/session, UTC reset or bounded UNKNOWN check, remaining budget and control/ref hashes. Fsync file and directory before reporting wait. The ordinary `status.json` is display only. | No durable wait means no wake; keep receipt. |
| Timer or monitor tick | STOP and identity drift win. Lock owner validates the entire wait/receipt/queue/refs/control chain and claims one resume attempt durably before Provider. A second tick sees claimed state and does nothing. | Claimed but no terminal resume result is ambiguous; no automatic reset of budget. |
| Resume result | Reviewer-quota resumes Reviewer with exact existing Worker result/test receipt; Worker-quota resumes Worker from same semantic reservation. Codex receives the resulting exact proposal for final review. | Never call ordinary submit, regenerate task alias, delete an intent or silently replay a possibly completed invocation. |

### C5a reservation record v1

The exact private target is `.local/two-site-refactor/autopilot/runs/<cycle-run-id>/broker-reservation.json` (<=4096 bytes, regular single-link 0600). Its exact keys are `schema_version="1.0"`, `controller_run_id`, `request_id`, `reservation` (the seven C4b2 strings), `request_sha256` (canonical full Broker request SHA-256) and `created_at` (the same finite request timestamp). The cycle ID, source HEAD and assignment SHA must match the existing strict `preparation-startup.json` in that same 0700 run directory. A run admits at most one Broker request; a pre-existing reservation is a conflict, not a retry. The record is an intent only: it cannot assert that Provider ran, quota occurred or a response exists. The client publishes/fsyncs it before enqueue and never removes it to manufacture permission for a second request.

Publishing must walk path components with no-follow directory descriptors from the canonical root, pin the run directory and write/link the exact basename with descriptor-relative no-overwrite operations, fsync the file and directory, and verify the final directory identity. `broker_paths()` and heartbeat `status.json` are not validation of this record. Maintenance submission uses its existing auditable record and does not create a cycle reservation.

## Small implementation units and acceptance

1. **C5a—reservation admission:** Broker client publishes the seven-field cycle reservation plus request ID *before* `_atomic_new_json(requests/...)`; no overwrite or hidden mkdir/chmod. Maintenance path and normal successful Broker behavior remain unchanged. Fault tests at file creation/fsync/queue boundary and duplicate cycle request must prove Provider is never called after uncertain admission. This is a new *pre-execution intent*, not a second terminal result journal.
2. **C5b—strict wait schema and reader:** pure typed validation first, then no-follow bounded persisted-state reader/writer under Supervisor lock. Validate exact reset timezone, receipt hash, run/request/assignment/ref/control bindings and monotonic budget. Add fake-clock, corrupt/linked/oversized record and crash-after-write tests. Do not connect a timer yet.
3. **C5c—quota disposition:** only on a validated terminal Broker receipt and same reservation may Supervisor settle the old RESCOPE intent into `WAITING_QUOTA`; normal `CONTINUE/COMPLETE/RESCOPE` paths remain unchanged. A missing response or exception must not be relabeled quota. Restart recovers the same wait instead of creating a new Coordinator task.
4. **C5d—same-lineage resume:** add an explicit Pipeline/Broker route under existing lock and semantic reservation. Resume role-specific prior artifacts; atomically claim budget before Provider. Test two wakeups, STOP, assignment/ref/task/control drift, lost response, claim crash, Reviewer-only continuation, and known/UNKNOWN reset schedules.
   - **One-shot durable submission intent (`quota-resume-submission.json`):** Existing immutable `quota-resume-claim.json` is not a spent-token marker because Broker consumes queue responses and claimed files; without a distinct submission-intent marker, the same request ID could be enqueued again after a completed response. Under caller-held Supervisor lock, after strictly validating continuation admission via `validate_quota_continuation_request`, the publisher atomically links and fsyncs a canonical 0600 single-link no-overwrite `quota-resume-submission.json` in the original cycle run directory using pinned no-follow directory descriptors. The record binds `request_id`, `original_controller_run_id`, `claim_sha256`, and canonical `request_sha256` without timestamps or secret bytes.
   - **Crash semantics and duplicate rejection:** Duplicate publication always fails immediately without overwrite, deletion, expiry, or recovery action. If a crash occurs after durable intent publication before queue publication, the system does not automatically retry; it treats this as a visible ambiguous stop pending manual operator resolution.
   - **Disabled gates:** No Broker queue submission, Provider call, Supervisor wake, replay authority, or live route is enabled in this unit. Observational quota discipline is strictly preserved, and remaining live-hook/fault-matrix gates stay disabled until C5e.
5. **C5e—Monitor and activation:** Monitor reads validated wait state but never grants retry authority. Run full existing Supervisor/RESCOPE and Broker suites; then a bounded synthetic fault matrix, one necessary live smoke, and comparable 1/3/10/20 cohorts. Restart long-lived Broker only after all code gates; no production/DNS/Web Writer action is part of this work.

The old Broker process still runs pre-C1 code and Supervisor remains STOPPED. C5a alone does not authorize a live wait. The [retention contract](AE03_C4C_RETENTION_CONTRACT_20260924.md) allows pruning only after C5 has durable final acknowledgement and reference validation; C4c cleanup must remain disabled before then. If a proof is missing, the affected task stops visibly while dependency-independent work can proceed; a visible stop is safer than accidental duplicate Provider work.

## Continuation terminal custody before live wake

Status: **PARTIALLY IMPLEMENTED / NO LIVE WAKE**. No live CLI, Supervisor, or Broker continuation route is activated.

### Implementation checkpoints and evidence

| Checkpoint | Commit | Path | Verified evidence / bounded interpretation |
| --- | --- | --- | --- |
| Accepted Broker fail-closed post-dispatch | `b53ef895` | `scripts/two_site_provider_broker.py`, `tests/test_two_site_quota_broker_serve_gate.py` | Scoped to observable Broker hard-stop, no-response, and retained-claim behavior when continuation dispatch fails or raises an exception: exceptions during or after provider call suppress replay, emit no response file, retain claimed file, and fail closed. |
| Synthetic test preparation | `a7db86b5` / `19efa9d9` | `tests/test_two_site_quota_broker_serve_gate.py` (`a7db86b5`), `tests/test_two_site_quota_broker_serve_fsync.py` (`19efa9d9`) | Narrow synthetic publisher mocks added in serve gate and serve fsync tests to verify Broker continuation execution without invoking live terminal storage. |
| Receipt-first source | `6103b91d` | `scripts/two_site_provider_broker.py` | Broker `serve()` invokes the previously existing storage publisher (`publish_terminal_continuation_record`) after COMPLETED validation and before queue response emission and claim unlink (receipt-first ordering). |
| Real terminal receipt & cold recovery tests | `cb17037f` | `tests/test_two_site_quota_continuation_broker_recovery.py` | Passing unit tests: `test_serve_continuation_success_receipt_persisted_before_response_and_unlink`, `test_serve_continuation_terminal_publisher_failure_fails_closed_without_replay`, and `test_serve_continuation_response_write_failure_recovers_from_receipt_without_replay`. |
| Observed STOP-before-dispatch guard | `38b2b341` | `scripts/two_site_provider_broker.py` | Guard in `serve()` checks `_STOP or os.path.lexists(stop_path)` prior to continuation execution, raising `BrokerError("stop requested before continuation dispatch")`. |
| Real-fixture STOP tests | `3b1ff8b0` | `tests/test_two_site_quota_continuation_broker_recovery.py` | Passing unit tests: `test_serve_continuation_pre_dispatch_stop_fails_closed` (verifying zero provider calls on pre-dispatch STOP) and `test_serve_continuation_post_dispatch_stop_preserves_terminal_custody_and_response` (verifying receipt publication and response write when STOP arrives during execution). |

### Still open / remaining gates

The following items remain strictly open and unverified; NO LIVE WAKE is permitted:
- **Atomic race semantics of concurrent STOP vs actual Provider start:** Window where STOP file appears or signal fires concurrently with Provider process spawn requires formal race resolution.
- **Supervisor durable final acknowledgement/wake integration:** Supervisor integration to read terminal continuation receipts, log final acknowledged Codex acceptance, and trigger safe wake remains unintegrated.
- **Full synthetic fault matrix plus necessary bounded live smoke/cohorts:** Comprehensive fault-injection matrix across all failure boundaries and bounded live 1/3/10/20 cohort verification are not yet run.
- **C4c retention:** Retention pruning remains dormant; terminal continuation receipts must remain reference-free for >=10 days and await durable final acknowledgement before any cleanup.
- **No authority for auto replay/production activation:** Automatic replay remains strictly forbidden; no production/DNS/Web Writer action or live route authorization is granted. AE03/C5 is not complete.

### 1. Broker-owned path and custody binding
Terminal continuation receipts are stored under Broker-owned state at `.local/two-site-refactor/provider-broker/terminal-continuation/<request-id>.json` (under Broker `STATE_REL = .local/two-site-refactor/provider-broker`, never under `autopilot/`).
The receipt binds:
- Exact full admitted 20-key continuation request and its canonical SHA-256 (`request_sha256`).
- Exact full 9-key `COMPLETED` response (`RESPONSE_KEYS`) and its canonical SHA-256 (`response_sha256`).
- Claim and submission-intent linkage (`original_controller_run_id`, `claim_sha256`) and exact `request_id`.
No `BROKER_ERROR` receipt is ever written; errors fail closed without terminal receipt creation.

### 2. Descriptor-relative atomic I/O and derived capacity bounds
The current `_atomic_new_json` helper in `scripts/two_site_provider_broker.py` operates on a `Path`, using a temporary file with `O_NOFOLLOW` and `os.link`, but **does not pin the parent directory with a directory file descriptor**. Terminal custody requires a new or adapted descriptor-relative, no-follow, parent-pinned atomic no-overwrite fsynced writer and reader (`openat`/`linkat` semantics from canonical root).

Because `MAX_RESPONSE_BYTES = 2_200_000` (with `MAX_PIPELINE_OUTPUT_BYTES = 2_000_000`) and `MAX_REQUEST_BYTES = 64_000`, the terminal receipt cannot use a fixed 64 KiB cap. The size cap is derived as:
`MAX_RESPONSE_BYTES + MAX_REQUEST_BYTES + 16_384` = 2,280,384 bytes (>2.264 MB).
Directory entry count is bounded (e.g. 64 records); reaching capacity triggers an explicit capacity stop, rejecting new admissions rather than evicting receipts.

### 3. Strict operational order and STOP custody
Broker execution enforces strict serialization:
1. Admit request, verify intent (`quota-resume-submission.json` bound to claim).
2. Check STOP: if STOP arrives before dispatch, halt immediately without invoking Provider.
3. Dispatch Provider continuation exactly once.
4. Validate full 9-key `COMPLETED` response.
5. If STOP arrives after completed Provider execution, persist and fsync the terminal receipt before honoring STOP.
6. Publish and fsync queue response to `responses/`.
7. Unlink claimed request from `claimed/` and fsync directory.

If receipt publication crashes or fails after Provider dispatch, the state is recorded as ambiguous—never terminal and strictly forbidding automatic replay.

### 4. Restart recovery, retention, and fault matrix
- **Restart recovery:** `_recover_interrupted_claims` inspects claims against `terminal-continuation/`. An exact validated receipt restores missing `responses/` files without re-invoking the Provider. Absent or malformed receipts preserve visible ambiguity and forbid replay.
- **Retention separation:** Queue responses in `responses/` expire after `ORPHAN_RESPONSE_RETENTION_SECONDS = 86_400`. Terminal continuation receipts remain intact until Supervisor logs final acknowledged Codex acceptance with unchanged refs, task, and assignment; thereafter, dormant C4c retention (reference-free >=10 days) governs pruning.
- **Fault matrix:**
  - *Crash boundaries:* Crash before receipt = ambiguous; crash after receipt before response = recovered from receipt; crash after response before unlink = claim unlinked cleanly on restart.
  - *Integrity:* Symlink, non-regular file, wrong UID/mode (0600), corrupted JSON, hash mismatch, or oversized payload (>2.28MB) causes immediate fail-closed stop.
  - *STOP / Drift:* STOP before dispatch stops execution; STOP after completed Provider preserves custody. Any ref, HEAD, or assignment drift halts execution without replay.
