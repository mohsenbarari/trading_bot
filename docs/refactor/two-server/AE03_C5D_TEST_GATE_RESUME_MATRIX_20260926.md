# AE03 C5d Test-Gate Quota Resume Support Matrix

## 1. Six-Case Three-Stage Matrix (Worker 1/2 × Pre-Review State)

| Case | Description | (A) Artifact/Admission Supported? | (B) Worker Once Supported? | (C) Final Ready-for-Codex Dispatch Supported? | Status / Disposition |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **W1-REV** | Worker-1, Reviewer-backed | yes | yes | yes | Supported (UNHOOKED end-to-end via post-result remainder) |
| **W1-BOOT** | Worker-1, bootstrap failure | no | no | no | Rejected (fail closed; no-op/fallback prohibited) |
| **W1-FAIL** | Worker-1, candidate test FAILED | no | no | no | Rejected (fail closed; no-op/fallback prohibited) |
| **W2-REV** | Worker-2, Reviewer-backed | yes | yes | NO | Intermediate only (`WORKER_PROPOSAL_READY`; final dispatch absent) |
| **W2-BOOT** | Worker-2, bootstrap failure | no | no | no | Rejected (fail closed; no-op/fallback prohibited) |
| **W2-FAIL** | Worker-2, candidate test FAILED | no | no | no | Rejected (fail closed; no-op/fallback prohibited) |

> **Critical Reviewer Attestation**: Reviewer must explicitly attest that the W2-REV final route is absent in `scripts/two_site_quota_resume_role_dispatch.py`. Current dispatcher for `worker_index == 2` calls `execute_worker_two_once` and directly returns intermediate `WORKER_PROPOSAL_READY`, with NO `execute_worker_two_post_result_remainder` or `READY_FOR_CODEX` final route. Any claim of full C5d completion must be rejected.

## 2. Persisted State Fields at Pipeline Quota Block

When `run_pipeline` halts with `QuotaBlockError`, `state['correction_count']` stays pinned at `attempted_index - 1` (e.g. `0` for Worker-1 quota attempt, `1` for Worker-2 quota attempt). State field invariants across the three trigger categories:

| Trigger Category | `pre_review_test_state` | `pre_review_test_path` / SHA | `final_reviewer_run_id` | `reviewer_path` | `correction_count` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Bootstrap Failure** | `None` | `None` / `None` | `None` | `None` | `attempted_index - 1` |
| **Test FAILED** | `"FAILED"` | Present / 64-hex SHA | `None` | `None` | `attempted_index - 1` |
| **Reviewer-Backed** | `"PASSED"` | Present / 64-hex SHA | Present (string) | Present (path) | `attempted_index - 1` |

## 3. Exact Current Failing Guards for Non-Reviewer Cases

Current codebase enforces strict fail-closed guards blocking bootstrap and test-FAILED cases:
1. `derive_worker_resume_artifacts` (`scripts/two_site_quota_resume_worker_artifacts.py`):
   - Requires `prior_reviewer_id` in `validate_prior_worker_paths`; missing reviewer raises `ProposalError`.
   - Requires `pre_review_test_state == "PASSED"`; `None` or `"FAILED"` raises `ProposalError`.
2. `load_worker_correction_evidence` (`scripts/two_site_quota_resume_worker_evidence.py`):
   - Unconditionally calls `validate_bound_prior_reviewer`, which fails closed if reviewer artifacts are absent.
3. `execute_worker_one_once` / `execute_worker_two_once`:
   - Pre-condition checks require valid reviewer status and verified feedback bindings.
4. Worker-2 History Lineage:
   - Worker-2 history validation (`Worker2 history complete`) assumes Reviewer1 exists for current correction.

Rejection is strict and fail-closed; non-supported cases must never be conflated with no-op support.

## 4. Ordered Implementation and Validation Steps

1. **Step 1 - Artifact Derivation Generalization**: Extend `derive_worker_resume_artifacts` to distinguish bootstrap, test-FAILED, and reviewer-backed paths while preserving fail-closed behavior for unadmitted patterns.
2. **Step 2 - Evidence Loader Branching**: Update `load_worker_correction_evidence` to support test-gate feedback parsing for test-FAILED and bootstrap evidence alongside reviewer feedback.
3. **Step 3 - Worker Execution Adaptation**: Ensure `execute_worker_one_once` and `execute_worker_two_once` consume test-gate feedback when `reviewer` is `test-gate`.
4. **Step 4 - Worker-2 Post-Result Remainder**: Implement `execute_worker_two_post_result_remainder` and wire the `READY_FOR_CODEX` final route in `dispatch_quota_continuation_once` for `worker_index == 2`.
5. **Step 5 - Hermetic Validation**: Run unit test suite `tests/test_two_site_quota_resume_worker_artifacts.py` and downstream dispatcher suites.

## 5. Fault Cases and Invariants

- **F1 (Mismatched Test State)**: Quota state presenting `pre_review_test_state != "PASSED"` during reviewer resume triggers immediate `ProposalError`.
- **F2 (Missing Reviewer Lineage)**: Missing `final_reviewer_run_id` on reviewer-backed continuation triggers validation failure in `validate_prior_worker_paths`.
- **F3 (Premature Final Route Claim)**: Worker-2 returning `READY_FOR_CODEX` without remainder execution violates dispatcher contract and fails closed.
- **F4 (State Correction Count Drift)**: Any mutation of `correction_count` before completed worker execution fails line-check assertions against `attempted_index - 1`.

## 6. Execution Authority and Gate Controls

> **Explicit No-Live / No-Replay Gate**: All quota continuation processing operates strictly under the sentinel blocker:
> `"observational quota, no replay authority"`
> No live provider execution or replay authority is granted or claimed. This specification is documentation-only under clean base `5afea833`.
