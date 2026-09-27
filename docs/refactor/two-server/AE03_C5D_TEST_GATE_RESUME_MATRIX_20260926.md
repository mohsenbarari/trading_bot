# AE03 C5d Test-Gate Quota Resume Support Matrix

## 1. Six-Case Three-Stage Matrix (Worker 1/2 × Pre-Review State)

| Case | Description | (A) Artifact/Admission Supported? | (B) Worker Once Supported? | (C) Final Ready-for-Codex Dispatch Supported? | Status / Disposition |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **W1-REV** | Worker-1, Reviewer-backed | yes | yes | yes | Supported (UNHOOKED end-to-end via post-result remainder) |
| **W1-BOOT** | Worker-1, bootstrap failure | yes | yes | yes | Accepted synthetic outer→real W1 once→remainder positive and status-SHA fault proof across commits 49dd707b/1a306053; common, live hook, STOP/race/crash, replay authority, and full C5d remain open; see Section 1.9. |
| **W1-FAIL** | Worker-1, candidate test FAILED | yes | yes | yes | Accepted synthetic outer→real W1 once→remainder positive and status-SHA fault proof across commits 49dd707b/1a306053; common, live hook, STOP/race/crash, replay authority, and full C5d remain open; see Section 1.9. |
| **W2-REV** | Worker-2, Reviewer-backed | yes | yes | yes | Current proof through intercepted external `runner_run` executing real `pre_provider_check` is in authoritative Section 1.10. Post-Runner same-root remainder, candidate sandbox, live Provider, Broker/Supervisor hook, STOP/race/crash, C5e/C4c, replay authority, and full C5d completion remain OPEN |
| **W2-BOOT** | Worker-2, bootstrap failure | yes | yes | yes | Direct canonical remainder branch tested in `tests/test_two_site_quota_resume_worker_two_bootstrap_terminal.py`; outer binder-to-remainder test-gate proof accepted in `tests/test_two_site_quota_resume_worker_two_outer_test_gate_fileproof.py` (`fb8e96ba`) with synthetic context/acquisition, mocked common request admission/terminal receipt/artifact derivation, patched `load_task`, mocked candidate sandbox, mocked Provider, and patched `execute_worker_two_once` returning already-materialized W2 state. Real Broker common admission, real storage seams joined to outer, candidate sandbox, live Provider, Broker/Supervisor live hook, complete STOP/race/crash matrix, replay authority, and full C5d completion remain OPEN |
| **W2-FAIL** | Worker-2, candidate test FAILED | yes | yes | yes | Direct canonical remainder branch tested in `tests/test_two_site_quota_resume_worker_two_failed_receipt_terminal.py`; outer binder-to-remainder test-gate proof accepted in `tests/test_two_site_quota_resume_worker_two_outer_test_gate_fileproof.py` (`fb8e96ba`) with synthetic context/acquisition, mocked common request admission/terminal receipt/artifact derivation, patched `load_task`, mocked candidate sandbox, mocked Provider, and patched `execute_worker_two_once` returning already-materialized W2 state. Real Broker common admission, real storage seams joined to outer, candidate sandbox, live Provider, Broker/Supervisor live hook, complete STOP/race/crash matrix, replay authority, and full C5d completion remain OPEN |

> **Critical Reviewer Attestation**: Reviewer must explicitly attest that the literal sentinel `observational quota, no replay authority` remains, no live Provider or candidate sandbox claim is made, and actual patch sites are inspected. Historical separate W2-REV synthetic remainder tests (`tests/test_two_site_quota_resume_worker_two_reviewer_terminal.py` and `tests/test_two_site_quota_resume_worker_two_outer_remainder_fileproof.py`) are distinct from old storage proofs in Section 1.1 and W2-BOOT/FAIL synthetic outer test-gate proofs in Section 1.2. These synthetic remainder and test-gate proofs cannot be combined with current same-root pre-Runner success (Section 1.10) to infer READY_FOR_CODEX or full C5d completion. Post-Runner same-root remainder, candidate sandbox, live Provider, Broker/Supervisor live hook, STOP/race/crash fault matrix, C5e/C4c, replay authority, and full C5d completion remain strictly OPEN.

### 1.1 W2-REV Accepted Proof Evidence (Commits c5c136fa, 4116be61, a68a477d, f3fddf23, 03ba6938)

- **Commit `c5c136fa`** (`tests/test_two_site_quota_continuation_admission_storage_integration.py`):
  Proves (a) standalone persisted wait/claim admission proof, verifying real file-backed storage publication and admission validation for `publish_initial_wait_record_bytes` and `publish_known_reset_resume_claim` under supervisor lock, including 0600 mode and payload tamper rejection.
- **Commit `4116be61`** (`tests/test_two_site_quota_resume_worker_two_terminal_receipt_fileproof.py`):
  Proves (b) W2 real terminal receipt lineage, verifying real Broker `publish_terminal_quota_record` and `read_terminal_quota_record` with exact status witness in `.local/two-site-refactor/proposals/{orig_prefix}-worker-2`, feeding real `derive_worker_resume_artifacts` and `load_worker_correction_evidence`, with tamper rejection failing closed before derivation/Provider while leaving prior W1/R1 status unaltered.
- **Commit `a68a477d`** (`tests/test_two_site_quota_resume_worker_two_joined_admission_fileproof.py`):
  Proves (c) joined W2 receipt→wait→claim→admission→artifact/evidence, verifying file-backed continuity across real Broker terminal quota receipt publishing/readback, initial wait record derivation/validation/storage, known-reset claim preparation/storage, continuation request construction, and admission validation.
- **Commit `f3fddf23`** (`tests/test_two_site_quota_resume_worker_two_joined_admission_fileproof.py`):
  Proves (d) file-backed semantic attempt, verifying immutable attempt reservation and readback via `reserve_semantic_attempt` and `read_existing_semantic_attempt` bound to task ID, run prefix, and semantic fingerprint.
- **Commit `03ba6938`** (`tests/test_two_site_quota_resume_worker_two_joined_admission_fileproof.py`):
  Proves (e) real published/read pending rescope intent, verifying `publish_rescope_intent` and `read_pending_rescope_intent` marker verification under supervisor lock, and fail-closed rejection on tampered permissions before Provider.
- **Patched External Seams in Joined Test**:
  The joined test patches strictly: common request validator (`validate_request`), Git repository facts (`validate_repository`), expected wait source (`collect_initial_wait_source_expected`), task snapshot (`load_task`), and `load_task` when reading Worker evidence (`load_worker_correction_evidence`).
- **Unproven Boundaries & Open Gates**:
  The joined test does NOT prove full real common Broker request admission, full outer binder→remainder with these real storage seams, candidate sandbox, live Provider, Broker/Supervisor live hook, STOP/race/crash matrix, replay authority, or full C5d completion. These boundaries and open gates remain strictly OPEN under sentinel `observational quota, no replay authority`.

### 1.2 W2-BOOT and W2-FAIL Accepted Outer Test-Gate Proof Evidence (Commit fb8e96ba)

- **Commit `fb8e96ba`** (`tests/test_two_site_quota_resume_worker_two_outer_test_gate_fileproof.py`):
  Proves materialized outer test-gate proof for Worker-2 resume lineage across both pre-W2 test-gate origins `BOOTSTRAP` and `TEST_FAILED`.
  - Materializes both pre-W2 origins `BOOTSTRAP` and `TEST_FAILED` from real disk files (`src.py`, `task.json`, W0/R0/W1/W2 `result.json`, `status.json`, `pre-review-tests-*.json`, and `feedback-correction-*.json`).
  - Executes real unpatched `dispatch_quota_continuation_once` W2 branch, real `worker_two_post_result_context`/`read_and_bind_worker_two_result`, and `execute_worker_two_post_result_remainder` through to `READY_FOR_CODEX`.
  - Proves negative tamper guards: rejects feedback-SHA and feedback-byte tamper before candidate sandbox or Reviewer Provider, failing closed with pipeline lock released and no subsequent proposal artifacts or reviewer sessions created.
  - Verifies pipeline lock held during provider execution and released cleanly upon completion or error.
- **Patched External Seams in Outer Test-Gate Proof**:
  Uses synthetic context/acquisition (`_setup_env`), mocked common request admission (`validate_quota_continuation_request`), mocked terminal quota receipt (`read_terminal_quota_record`), mocked resume artifact derivation (`derive_worker_resume_artifacts`), patched `load_task`, mocked candidate sandbox (`run_candidate_tests`), mocked Provider (`run_provider_session`), and patched `execute_worker_two_once` returning already-materialized W2 state.
- **Unproven Boundaries & Open Gates**:
  Does NOT prove real Broker common request admission, real storage seams joined to outer, real candidate sandbox, live Provider, Broker/Supervisor live hook, complete STOP/race/crash matrix, replay authority, or full C5d completion. These boundaries and open gates remain strictly OPEN under sentinel `observational quota, no replay authority`.

### 1.3 Standalone Common Broker Request Validation Boundary Proof Evidence (Commit d88c6b2a)

- **Commit `d88c6b2a`** (`tests/test_two_site_quota_continuation_common_broker_boundary.py`):
  Proves standalone common Broker request validation boundary on clean base `d88c6b2a045a87e105264502df8ab3ad0a9dff00` (1085 related quota/Broker/pipeline tests passed at Codex final verification).
  - **Positive Boundary Case** (`test_quota_continuation_common_validation_boundary_positive`): A continuation-shaped request assembled from real hermetic Broker fixture facts passes real unpatched common `validate_request` (including actual Git branch, HEAD, and clean worktree state, file-backed task bytes, task SHA, and schema, plus an injected valid `AssignmentScope`); execution then reaches the first `read_pending_rescope_intent` call, deliberately intercepted by a sentinel `RuntimeError`.
  - **Negative Task SHA Drift** (`test_quota_continuation_common_validation_rejects_task_sha_drift`): Task SHA drift fails closed during real common validation with `BrokerError` ("broker task SHA drift") before the pending-intent reader is called, leaving disk bytes and Git HEAD unchanged.
  - **Negative Assignment SHA Drift** (`test_quota_continuation_common_validation_rejects_assignment_mismatch`): Assignment SHA drift fails closed during real common validation with `BrokerError` ("broker assignment binding mismatch") before the pending-intent reader is called.
- **Injected Scope & Deliberate Sentinels**:
  This is a STANDALONE boundary only. Reviewer must inspect the test source and confirm the sentinel `RuntimeError` and remaining mocked seam (`scope_loader` is injected; no real assignment scope registry; `read_pending_rescope_intent` is deliberately intercepted by a sentinel).
- **Unproven Boundaries & Open Gates**:
  Does NOT claim a valid wait/claim, completed quota admission, real scope registry, joined W2 full success, candidate sandbox/Provider, live Broker/Supervisor hook, replay authority, or full C5d completion. Full common Broker request admission remains strictly OPEN (see Section 1.1/1.2) because the successful path is stopped at pending intent. All quota continuation processing remains strictly under sentinel `observational quota, no replay authority`.

### 1.4 Real Common Request Validation and Intent Marker Fileproof Evidence (Commit 8cab47ca)

- **Commit `8cab47ca`** (`tests/test_two_site_quota_continuation_common_intent_fileproof.py`):
  Proves common request validation coupled with durable file-backed pending rescope intent marker verification across two hermetic tests:
  - **Positive Boundary Case** (`test_quota_continuation_common_intent_fileproof_positive`): Uses real unpatched Broker common `validate_request` over an actual Git/task/assignment fixture and a real file-backed `0600` marker published by `ensure_rescope_epoch_dir` and `publish_rescope_intent`. The marker is read by unpatched `read_pending_rescope_intent`, successfully passing through to a deliberate sentinel intercepted at `collect_initial_wait_source_expected`.
  - **Negative Permission Tamper Case** (`test_quota_continuation_common_intent_fileproof_negative_permission_tamper`): Tampering marker permissions to `0644` fails closed with `WaitIntentError` before expected-wait collection, leaving task bytes, marker bytes, and Git HEAD preserved.
- **Explicit Disclosures & Patched Seams**:
  - `scripts.two_site_quota_wait_intent.validate_repository` is patched using constructed facts with placeholder protected-ref and control-blob SHAs; this does NOT prove real Autopilot repository validation.
  - `collect_initial_wait_source_expected` is patched as a deliberate sentinel boundary. Reviewer must inspect actual test patch sites and reject overclaim.
- **Unproven Boundaries & Open Gates**:
  Does NOT prove wait/claim storage admission, full common continuation admission, W2 joined path, candidate sandbox, Provider execution, live Broker/Supervisor hook, STOP/race/crash fault recovery, replay authority, or C5d completion. Full common admission, wait/claim, W2 join, Provider/live hook and replay authority remain explicitly OPEN under sentinel `observational quota, no replay authority`.

### 1.5 Canonical Broker Task and Semantic Attempt Fixture (Commit d810490f)

- `tests/test_two_site_quota_join_semantic_fixture.py` builds a hermetic Git repository with a tracked gap and execution-order file, an ignored real active Assignment, and the full four-key HEAD-bound excerpt package. Real `load_task`, Broker `validate_request`, `build_task_identity`, `reserve_semantic_attempt`, and `read_existing_semantic_attempt` succeed against that same task; the persisted attempt is mode `0600`.
- Independent Gemini Reviewer verified the first Worker proposal; Codex checked exact SHA/bytes and 108 focused Broker/semantic tests passed. Section 1.6 reuses this fixture.
- This proves only common Broker/task/Assignment validation plus semantic-attempt storage. It does **not** prove wait/claim, terminal receipt, full continuation admission, W2, Provider, live Broker/Supervisor hook, replay authority, or C5d completion. Sentinel: `observational quota, no replay authority`.

### 1.6 Common Broker Through Persisted Wait/Claim Admission (Commit f1dfb4d0)

- `tests/test_two_site_quota_common_wait_claim_join_fileproof.py` joins the real canonical Broker/Assignment/task and persisted semantic attempt to real pending-intent, wait-file, claim-file and continuation-admission readers. The positive case succeeds; a `0644` wait file fails closed after common validation, before claim acceptance.
- The only patched sources are Autopilot repository-fact collection for the tiny fixture and `collect_initial_wait_source_expected`. Original terminal-receipt/response hashes are synthetic, **not** real receipt evidence. This is not a complete original-provider provenance proof.
- Gemini Worker and independent Reviewer passed first try; Codex checked exact SHA/bytes and 1,034 quota-family tests passed.
- At this checkpoint the real original wait-source collector and terminal receipt were still open; Section 1.7 closes that hermetic storage/source join. Common→W2 outer join, candidate sandbox/Provider, Broker/Supervisor live hook, STOP/race/crash cohorts, replay authority and C5d completion remain open. Sentinel: `observational quota, no replay authority`.

### 1.7 Real Terminal Receipt and Initial Source Collector Join (Commit c536f4ee)

- `tests/test_two_site_quota_real_source_join_fileproof.py` connects the canonical Broker/Task/Assignment/semantic fixture to a real persisted cycle reservation, terminal-quota receipt, unpatched `collect_initial_wait_source_expected`, unpatched `admit_initial_quota_wait_locked`, persisted wait/claim, and unpatched continuation admission. A post-receipt status-witness tamper fails closed before wait/claim publication.
- The original quota Provider payload is synthetic test data, not a live Provider run. At commit `c536f4ee`, the two module-local Autopilot `validate_repository` calls were patched to accepted facts because the tiny fixture lacks the full control-file tree; commit `9772f65e` later removes those mocks from this same test (Section 1.8). No terminal receipt, collector, Broker common validator, task/semantic/wait/claim reader or admission function is patched.
- Gemini Worker needed two bounded test-gate corrections for nonexistent receipt keys; independent Reviewer then verified the exact proposal. Codex checked SHA/bytes and 1,036 quota-family tests passed.
- At that checkpoint, full-tree facts were still open; Section 1.8 closes only this common-chain test gap. The distinct W2 joined proof still patches common request validator, Git repo facts, expected wait source, task snapshot/load_task; live Provider/candidate sandbox, common→W2 outer join, Broker/Supervisor live hook, STOP/race/crash, replay authority and C5d completion remain open. Sentinel: `observational quota, no replay authority`.

### 1.8 Real Full-Tree Repository Facts and Common Source Join Fileproof (Commits 2ef9ca50, 9772f65e)

- Commit `2ef9ca50` proves standalone full-tree real `validate_repository`/`validate_accepted_repository` and hardlink rejection in `tests/test_two_site_quota_full_tree_repository_facts.py`.
- Commit `9772f65e` proves common Broker receipt→initial wait→wait/claim→continuation positive and status-witness-tamper negative with real unpatched facts and real pending intent in `tests/test_two_site_quota_real_source_join_fileproof.py`; Gemini Worker/Reviewer verified, Codex byte-bound, 50 related tests passed for the latter.
- Distinct W2 joined proof in `tests/test_two_site_quota_resume_worker_two_joined_admission_fileproof.py` STILL patches common request validator, Git facts, expected wait source, task snapshot/load_task.
- Original Provider outcome synthetic; candidate sandbox, live Provider, W2 outer join, Broker/Supervisor hook, STOP/race/crash, replay authority and full C5d completion OPEN. Sentinel: `observational quota, no replay authority`.

### 1.9 Worker-1 Test-Gate Once and Outer Join Fileproof (Commits 9dc6b1c0, 0a9f5304, 49dd707b, 1a306053)

- Commit `9dc6b1c0` created the initial Worker-1 `BOOTSTRAP` once proof in `tests/test_two_site_quota_resume_worker_one_bootstrap_once_fileproof.py`.
- Commit `0a9f5304` refactored the shared test-gate helper and added coverage for the `TEST_FAILED` origin; independent Gemini Reviewer and Codex exact-byte/test/Git gates passed with 30 related tests and exactly 2 collected in the target file.
- Executes real unpatched `execute_worker_one_once`, `validate_run`, and `pipeline_lock` under file-backed W0 status/feedback fixtures with simulated Provider execution.
- Seams explicitly mocked: request acquisition (`worker_correction_continuation_context`), common request admission (`validate_quota_continuation_request`), terminal receipt (`read_terminal_quota_record`), artifact derivation (`derive_worker_resume_artifacts`), evidence derivation (`load_worker_correction_evidence`), Git repository refs (`ref_snapshot`), pipeline state check (`check_state`), and runner/provider execution (`runner_run`).
- Enforces mandatory `pre_provider_check` callback called before simulated Provider entry; verifies pipeline lock is held during execution and cleanly released afterward.
- Resulting status strictly binds prior W0 proposal SHA (`input_proposal_sha256`), feedback SHA (`input_feedback_sha256`), and newly materialized W1 result SHA (`result_sha256`).
- Commit `49dd707b` (`tests/test_two_site_quota_resume_worker_one_outer_once_join_fileproof.py`): Proves real unpatched `dispatch_quota_continuation_once`→once→remainder positive execution for `BOOTSTRAP` and `TEST_FAILED` origins to `READY_FOR_CODEX` under synthetic seams.
- Commit `1a306053`: Proves new W1 status `input_feedback_sha256` tamper rejects before candidate sandbox or Reviewer Provider, old W0/W1/feedback bytes preserved, pipeline lock released; 34 related tests pass with 4 collected in fileproof.
- Mocked seams: dispatch admission (`validate_quota_continuation_request`), terminal receipt (`read_terminal_quota_record`), artifact derivation (`derive_worker_resume_artifacts`), once/post continuation context (`worker_correction_continuation_context`), repo refs (`ref_snapshot`), state checks (`check_state`), evidence loading (`load_worker_correction_evidence`), Runner Worker (`runner_run`), candidate sandbox (`run_candidate_tests`), and Reviewer Provider (`run_provider_session`). Real Broker admission, real storage sources, candidate sandbox, live Provider, Broker/Supervisor live hook, STOP/race/crash fault matrix, replay authority, and full C5d completion remain OPEN under sentinel `observational quota, no replay authority`.

### 1.10 Real Same-Root W2-REV Admission Through Runner Boundary (Commits d850a1cc, 9724f3d8, 98fdb2ce, 1c9e9bda, 4d70ba70)

- **Lineage and Context**: Builds on earlier lineage `89aae8bd` (receipt), `784bd0ea` (wait), and `91d6a45f` (claim), establishing real same-root W2-REV continuity.
- **Commit `d850a1cc`** (`tests/test_two_site_quota_w2_common_root_admission.py`): Proves real same-root W2 continuation admission under broker repository fixtures.
- **Commit `9724f3d8`**: Proves terminal receipt, resume artifact derivation, and worker correction evidence loading on the same root.
- **Commit `98fdb2ce`** (`tests/test_two_site_quota_w2_common_root_context.py`): Proves locked worker continuation context and history completion with tampered feedback-SHA rejection.
- **Commit `1c9e9bda`**: Fixture-only optimization reducing test execution from 78.60s to 60.17s across the same ten tests without changing semantics.
- **Commit `4d70ba70`** (`tests/test_two_site_quota_w2_common_root_outer_boundary.py`): Proves unpatched outer `dispatch_quota_continuation_once` routing through real inner Worker-2 once to external `runner_run`, executing real `pre_provider_check` under lock, with feedback-SHA tamper rejection before runner invocation.
- **Boundary Seam & Open Gates**: All tests are hermetic with synthetic Provider quota outcome; no live Provider or candidate sandbox. External `runner_run` is the only patch in the newest outer test. Post-Runner same-root remainder, candidate sandbox, live Provider, Broker/Supervisor hook, STOP/race/crash fault recovery, C5e/C4c, replay authority, and full C5d completion remain strictly OPEN under sentinel `observational quota, no replay authority`.

## 2. Persisted State Fields at Pipeline Quota Block

When `run_pipeline` halts with `QuotaBlockError`, `state['correction_count']` stays pinned at `attempted_index - 1` (e.g. `0` for Worker-1 quota attempt, `1` for Worker-2 quota attempt). State field invariants across the three trigger categories:

| Trigger Category | `pre_review_test_state` | `pre_review_test_path` / SHA | `final_reviewer_run_id` | `reviewer_path` | `correction_count` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Bootstrap Failure** | `None` | `None` / `None` | `None` | `None` | `attempted_index - 1` |
| **Test FAILED** | `"FAILED"` | Present / 64-hex SHA | `None` | `None` | `attempted_index - 1` |
| **Reviewer-Backed** | `"PASSED"` | Present / 64-hex SHA | Present (string) | Present (path) | `attempted_index - 1` |

## 3. Exact Current Failing Guards for Non-Reviewer Cases

Artifact derivation (`derive_worker_resume_artifacts`) and evidence loading (`load_worker_correction_evidence`) support non-Reviewer cases. For W2-REV, accepted same-root admission and outer dispatch to intercepted `runner_run` are established in Section 1.10. For W2-BOOT/FAIL, real common admission and storage seams joined to outer remain OPEN. Direct canonical remainder branches have been tested across the three terminal test paths (`tests/test_two_site_quota_resume_worker_two_bootstrap_terminal.py`, `tests/test_two_site_quota_resume_worker_two_failed_receipt_terminal.py`, and `tests/test_two_site_quota_resume_worker_two_reviewer_terminal.py`), directly invoking real `_run_post_worker_remainder` and leaving real validation unmocked while candidate sandbox and Provider sessions are mocked. For W2-BOOT and W2-FAIL, real common admission and storage seams joined to outer, candidate sandbox, live Provider, STOP/race/crash faults, and Broker/Supervisor live hook remain OPEN:
1. Materialized W2 Integration and Fault Proof:
   - Direct canonical remainder branches are tested for W2-BOOT and W2-FAIL (`tests/test_two_site_quota_resume_worker_two_bootstrap_terminal.py` and `tests/test_two_site_quota_resume_worker_two_failed_receipt_terminal.py`); outer binder-to-remainder test-gate proof is accepted in `tests/test_two_site_quota_resume_worker_two_outer_test_gate_fileproof.py` (`fb8e96ba`) with synthetic context/acquisition, mocked common request admission/terminal receipt/artifact derivation, patched `load_task`, mocked candidate sandbox, mocked Provider, and patched `execute_worker_two_once` returning already-materialized W2 state. For W2-BOOT/FAIL, real Broker common admission, real storage seams joined to outer, candidate sandbox, live Provider, Broker/Supervisor live hook, complete STOP/race/crash matrix, replay authority, and full C5d completion remain OPEN.
   - For W2-REV, accepted same-root admission and outer-to-Runner dispatch are detailed in Section 1.10; post-Runner same-root remainder, candidate sandbox, live Provider, Broker/Supervisor live hook, STOP/race/crash matrix, replay authority, and full C5d completion remain OPEN.
   - Complete STOP/race/crash recovery fault matrix remains OPEN across all cases.
2. Broker / Supervisor Live Hook:
   - Broker/Supervisor live hook absent; continuation dispatcher is unhooked from live Broker and Supervisor continuation handlers with no live replay authority.

Rejection is strict and fail-closed; non-supported cases must never be conflated with no-op support.

## 4. Ordered Implementation and Validation Steps

1. **Step 1 - W1 Materialized Fault Proof**: Commits `9dc6b1c0`, `0a9f5304`, `49dd707b`, and `1a306053` accept Worker-1 once execution, synthetic outer dispatcher join, and one status-SHA fault. Full fault matrix, candidate sandbox, live Provider, Broker/Supervisor live hook, and replay authority remain open; see Section 1.9.
2. **Step 2 - W2 Test-Gate History / Once**: Implemented code (`execute_worker_two_once` under lock-held context), while full materialized history/outer-lock fault proof remains pending; Step 4+ open.
3. **Step 3 - W2 Final Remainder**: Implemented W2 remainder code (`execute_worker_two_post_result_remainder` and final route in `dispatch_quota_continuation_once` for `worker_index == 2`) and separate synthetic remainder tests (`tests/test_two_site_quota_resume_worker_two_reviewer_terminal.py`, `tests/test_two_site_quota_resume_worker_two_outer_remainder_fileproof.py`, and W2-BOOT/FAIL in `fb8e96ba`), but current same-root proof stops at the Runner seam (Section 1.10), so no joined READY_FOR_CODEX exists. Post-Runner same-root remainder, candidate sandbox, live Provider, Broker/Supervisor live hook, STOP/race/crash matrix, replay authority, and full C5d completion remain open; Step 4+ open.
4. **Step 4 - Broker / Supervisor Hook**: Wire continuation dispatcher into live Broker and Supervisor continuation handlers (open; no live replay authority).
5. **Step 5 - C5e Fault / Cohorts**: Hermetic fault-injection and cohort validation (open).
6. **Step 6 - C4c Retention**: State and artifact retention validation under pipeline lifecycle policies (open).

## 5. Fault Cases and Invariants

- **F1 (Mismatched Test State)**: Quota state presenting `pre_review_test_state != "PASSED"` during reviewer resume triggers immediate `ProposalError`.
- **F2 (Missing Reviewer Lineage)**: Missing `final_reviewer_run_id` on reviewer-backed continuation triggers validation failure in `validate_prior_worker_paths`.
- **F3 (Premature Final Route Claim)**: Worker-2 returning `READY_FOR_CODEX` without exact intermediate validation, remainder execution, and strict final identity checks (exact lineage, `correction_count == 2`, non-empty `final_reviewer_run_id`, and uncorrected worker fields matching intermediate) violates dispatcher contract and fails closed.
- **F4 (State Correction Count Drift)**: Any mutation of `correction_count` before completed worker execution fails line-check assertions against `attempted_index - 1`.

## 6. Execution Authority and Gate Controls

> **Explicit No-Live / No-Replay Gate**: All quota continuation processing operates strictly under the sentinel blocker:
> `"observational quota, no replay authority"`
> This sentinel is a blocker, not replay authority. No live provider execution or replay authority is granted or claimed. Isolated continuation helpers exist but live Broker/Supervisor route remains unhooked; no main/production/DNS/Web Writer change.
