# AE03 C5d Test-Gate Quota Resume Support Matrix

## 1. Six-Case Three-Stage Matrix (Worker 1/2 × Pre-Review State)

| Case | Description | (A) Artifact/Admission Supported? | (B) Worker Once Supported? | (C) Final Ready-for-Codex Dispatch Supported? | Status / Disposition |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **W1-REV** | Worker-1, Reviewer-backed | yes | yes | yes | Supported (UNHOOKED end-to-end via post-result remainder) |
| **W1-BOOT** | Worker-1, bootstrap failure | yes | yes | yes | Code path UNHOOKED but materialized end-to-end fault proof pending |
| **W1-FAIL** | Worker-1, candidate test FAILED | yes | yes | yes | Code path UNHOOKED but materialized end-to-end fault proof pending |
| **W2-REV** | Worker-2, Reviewer-backed | yes | yes | yes | Direct canonical remainder branch tested in `tests/test_two_site_quota_resume_worker_two_reviewer_terminal.py` (candidate tests simulated at sandbox seam via mocked `run_candidate_tests`, mocked `run_provider_session` with valid Reviewer artifact materialization, real unmocked remainder and `validate_run`); full file-backed W2 outer binder-to-remainder integration, STOP/race/outer-lock faults and Broker/Supervisor live hook remain OPEN |
| **W2-BOOT** | Worker-2, bootstrap failure | yes | yes | yes | Direct canonical remainder branch tested in `tests/test_two_site_quota_resume_worker_two_bootstrap_terminal.py` (bootstrap failure branch with no sandbox run, real unmocked `p1_00_characterization_bootstrap_findings` and remainder, mocked provider); full file-backed W2 outer binder-to-remainder integration, STOP/race/outer-lock faults and Broker/Supervisor live hook remain OPEN |
| **W2-FAIL** | Worker-2, candidate test FAILED | yes | yes | yes | Direct canonical remainder branch tested in `tests/test_two_site_quota_resume_worker_two_failed_receipt_terminal.py` (candidate tests simulated at sandbox seam via mocked `run_candidate_tests`, real unmocked remainder, receipt validation, and failure signatures, mocked provider); full file-backed W2 outer binder-to-remainder integration, STOP/race/outer-lock faults and Broker/Supervisor live hook remain OPEN |

> **Critical Reviewer Attestation**: Reviewer must explicitly attest that the literal sentinel `observational quota, no replay authority` remains, no false source or test completion claim is made, and no "real candidate test runner" claim or equivalent overclaim is made (reviewer must inspect actual `patch()` calls in the supplied test files). Direct canonical remainder branches have been tested across the exact three test paths: `tests/test_two_site_quota_resume_worker_two_bootstrap_terminal.py`, `tests/test_two_site_quota_resume_worker_two_failed_receipt_terminal.py`, and `tests/test_two_site_quota_resume_worker_two_reviewer_terminal.py`. These tests directly invoke real `_run_post_worker_remainder` and leave real `p1_00_characterization_bootstrap_findings`, `persist_pre_review_tests`, `validate_test_receipt`, `test_failure_signature`, `test_gate_feedback`, and (in positive reviewer test) `validate_run` unmocked. They mock external `run_candidate_tests` (candidate tests are simulated at the sandbox seam; in the bootstrap branch no sandbox runs) and external `run_provider_session` (reviewer test materializes valid Reviewer result/status/task through existing fixture runner, not a live provider). Full file-backed W2 outer binder-to-remainder integration, STOP/race/outer-lock faults, and Broker/Supervisor live hook remain OPEN. No Broker/Supervisor live hook or replay authority exists, and any claim of full C5d completion must still be rejected.

## 2. Persisted State Fields at Pipeline Quota Block

When `run_pipeline` halts with `QuotaBlockError`, `state['correction_count']` stays pinned at `attempted_index - 1` (e.g. `0` for Worker-1 quota attempt, `1` for Worker-2 quota attempt). State field invariants across the three trigger categories:

| Trigger Category | `pre_review_test_state` | `pre_review_test_path` / SHA | `final_reviewer_run_id` | `reviewer_path` | `correction_count` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Bootstrap Failure** | `None` | `None` / `None` | `None` | `None` | `attempted_index - 1` |
| **Test FAILED** | `"FAILED"` | Present / 64-hex SHA | `None` | `None` | `attempted_index - 1` |
| **Reviewer-Backed** | `"PASSED"` | Present / 64-hex SHA | Present (string) | Present (path) | `attempted_index - 1` |

## 3. Exact Current Failing Guards for Non-Reviewer Cases

Artifact derivation (`derive_worker_resume_artifacts`) and evidence loading (`load_worker_correction_evidence`) support non-Reviewer cases. Those guards resolved in isolated helpers for BOOTSTRAP/TEST_FAILED, and W2 final unhooked dispatch code path exists. Direct canonical remainder branches have been tested across the exact three test paths (`tests/test_two_site_quota_resume_worker_two_bootstrap_terminal.py`, `tests/test_two_site_quota_resume_worker_two_failed_receipt_terminal.py`, and `tests/test_two_site_quota_resume_worker_two_reviewer_terminal.py`), directly invoking real `_run_post_worker_remainder` and leaving real `p1_00_characterization_bootstrap_findings`, `persist_pre_review_tests`, `validate_test_receipt`, `test_failure_signature`, `test_gate_feedback`, and `validate_run` unmocked, while candidate tests are simulated at the sandbox seam via mocked `run_candidate_tests` (no sandbox run on bootstrap branch) and provider sessions are mocked via `run_provider_session`. However, full file-backed W2 outer binder-to-remainder integration, STOP/race/outer-lock faults, and Broker/Supervisor live hook remain OPEN:
1. Materialized W2 Integration and Fault Proof:
   - Direct canonical remainder branches are tested, but full file-backed W2 outer binder-to-remainder integration, STOP/race/outer-lock faults, and crash recovery remain OPEN across reviewer, bootstrap failure, and test-FAILED origins.
2. Broker / Supervisor Live Hook:
   - Broker/Supervisor live hook absent; continuation dispatcher is unhooked from live Broker and Supervisor continuation handlers with no live replay authority.

Rejection is strict and fail-closed; non-supported cases must never be conflated with no-op support.

## 4. Ordered Implementation and Validation Steps

1. **Step 1 - W1 Materialized Fault Proof**: Materialize end-to-end fault proof for Worker-1 non-reviewer (bootstrap and test-FAILED) code path.
2. **Step 2 - W2 Test-Gate History / Once**: Implemented code (`execute_worker_two_once` under lock-held context), while full materialized history/outer-lock fault proof remains pending; Step 4+ open.
3. **Step 3 - W2 Final Remainder**: Implemented code (`execute_worker_two_post_result_remainder` and `READY_FOR_CODEX` final route wired in `dispatch_quota_continuation_once` for `worker_index == 2`). Direct canonical remainder branches have been tested in `tests/test_two_site_quota_resume_worker_two_bootstrap_terminal.py`, `tests/test_two_site_quota_resume_worker_two_failed_receipt_terminal.py`, and `tests/test_two_site_quota_resume_worker_two_reviewer_terminal.py` (real unmocked `_run_post_worker_remainder` and receipt validation; candidate tests simulated at the sandbox seam via mocked `run_candidate_tests` and mocked `run_provider_session`). Full file-backed W2 outer binder-to-remainder integration, STOP/race/outer-lock faults, and Broker/Supervisor live hook remain OPEN; Step 4+ open.
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
