# AE03 C5d Test-Gate Quota Resume Support Matrix

## 1. Six-Case Three-Stage Matrix (Worker 1/2 × Pre-Review State)

| Case | Description | (A) Artifact/Admission Supported? | (B) Worker Once Supported? | (C) Final Ready-for-Codex Dispatch Supported? | Status / Disposition |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **W1-REV** | Worker-1, Reviewer-backed | yes | yes | yes | Supported (UNHOOKED end-to-end via post-result remainder) |
| **W1-BOOT** | Worker-1, bootstrap failure | yes | yes | yes | Code path UNHOOKED but materialized end-to-end fault proof pending |
| **W1-FAIL** | Worker-1, candidate test FAILED | yes | yes | yes | Code path UNHOOKED but materialized end-to-end fault proof pending |
| **W2-REV** | Worker-2, Reviewer-backed | yes | yes | no | Intermediate only (`WORKER_PROPOSAL_READY`; final dispatch absent) |
| **W2-BOOT** | Worker-2, bootstrap failure | yes | yes | no | Intermediate only (`WORKER_PROPOSAL_READY`; final dispatch absent) |
| **W2-FAIL** | Worker-2, candidate test FAILED | yes | yes | no | Intermediate only (`WORKER_PROPOSAL_READY`; final dispatch absent) |

> **Critical Reviewer Attestation**: Reviewer must explicitly attest that the literal sentinel `observational quota, no replay authority` remains, no false source or test completion claim is made, and the W2-REV final route is absent in `scripts/two_site_quota_resume_role_dispatch.py`. Current dispatcher for `worker_index == 2` calls `execute_worker_two_once` and directly returns intermediate `WORKER_PROPOSAL_READY`, with NO `execute_worker_two_post_result_remainder` or `READY_FOR_CODEX` final route. Any claim of full C5d completion must be rejected.

## 2. Persisted State Fields at Pipeline Quota Block

When `run_pipeline` halts with `QuotaBlockError`, `state['correction_count']` stays pinned at `attempted_index - 1` (e.g. `0` for Worker-1 quota attempt, `1` for Worker-2 quota attempt). State field invariants across the three trigger categories:

| Trigger Category | `pre_review_test_state` | `pre_review_test_path` / SHA | `final_reviewer_run_id` | `reviewer_path` | `correction_count` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Bootstrap Failure** | `None` | `None` / `None` | `None` | `None` | `attempted_index - 1` |
| **Test FAILED** | `"FAILED"` | Present / 64-hex SHA | `None` | `None` | `attempted_index - 1` |
| **Reviewer-Backed** | `"PASSED"` | Present / 64-hex SHA | Present (string) | Present (path) | `attempted_index - 1` |

## 3. Exact Current Failing Guards for Non-Reviewer Cases

Artifact derivation (`derive_worker_resume_artifacts`) and evidence loading (`load_worker_correction_evidence`) support non-Reviewer cases. Those guards resolved in isolated helpers for BOOTSTRAP/TEST_FAILED, but W2 final dispatch absent, full materialized history/outer-lock/fault proof remains pending, Broker/Supervisor live hook absent:
1. Worker-2 Final Dispatch (`scripts/two_site_quota_resume_role_dispatch.py`):
   - Final remainder execution and `READY_FOR_CODEX` route are absent for Worker-2.
2. Materialized Proof:
   - Full materialized history/outer-lock/fault proof remains pending.
3. Live Hook:
   - Broker/Supervisor live hook absent.

Rejection is strict and fail-closed; non-supported cases must never be conflated with no-op support.

## 4. Ordered Implementation and Validation Steps

1. **Step 1 - W1 Materialized Fault Proof**: Materialize end-to-end fault proof for Worker-1 non-reviewer (bootstrap and test-FAILED) code path.
2. **Step 2 - W2 Test-Gate History / Once**: Helper-level implemented while materialized proof remains; Step 3/4 still pending.
3. **Step 3 - W2 Final Remainder**: Implement `execute_worker_two_post_result_remainder` and wire the `READY_FOR_CODEX` final route in `dispatch_quota_continuation_once` for `worker_index == 2`.
4. **Step 4 - Broker / Supervisor Hook**: Wire continuation dispatcher into live Broker and Supervisor continuation handlers.
5. **Step 5 - C5e Fault / Cohorts**: Hermetic fault-injection and cohort validation.
6. **Step 6 - C4c Retention**: State and artifact retention validation under pipeline lifecycle policies.

## 5. Fault Cases and Invariants

- **F1 (Mismatched Test State)**: Quota state presenting `pre_review_test_state != "PASSED"` during reviewer resume triggers immediate `ProposalError`.
- **F2 (Missing Reviewer Lineage)**: Missing `final_reviewer_run_id` on reviewer-backed continuation triggers validation failure in `validate_prior_worker_paths`.
- **F3 (Premature Final Route Claim)**: Worker-2 returning `READY_FOR_CODEX` without remainder execution violates dispatcher contract and fails closed.
- **F4 (State Correction Count Drift)**: Any mutation of `correction_count` before completed worker execution fails line-check assertions against `attempted_index - 1`.

## 6. Execution Authority and Gate Controls

> **Explicit No-Live / No-Replay Gate**: All quota continuation processing operates strictly under the sentinel blocker:
> `"observational quota, no replay authority"`
> This sentinel is a blocker, not replay authority. No live provider execution or replay authority is granted or claimed. Isolated continuation helpers exist but live Broker/Supervisor route remains unhooked; no main/production/DNS/Web Writer change.
