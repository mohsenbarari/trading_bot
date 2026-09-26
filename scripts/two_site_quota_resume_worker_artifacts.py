from __future__ import annotations

import json
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        _QUOTA_CONTINUATION_KEYS,
        unique_json,
    )
except ImportError:
    from two_site_proposal_pipeline import (
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        _QUOTA_CONTINUATION_KEYS,
        unique_json,
    )

try:
    from scripts.two_site_provider_broker import (
        PIPELINE_QUOTA_KEYS,
        QUOTA_ATTEMPT_KEYS,
        RECEIPT_KEYS,
        REQUEST_KEYS,
    )
except ImportError:
    from two_site_provider_broker import (
        PIPELINE_QUOTA_KEYS,
        QUOTA_ATTEMPT_KEYS,
        RECEIPT_KEYS,
        REQUEST_KEYS,
    )

try:
    from scripts.two_site_quota_resume_worker_paths import (
        validate_prior_worker_no_reviewer_paths,
        validate_prior_worker_paths,
    )
except ImportError:
    from two_site_quota_resume_worker_paths import (
        validate_prior_worker_no_reviewer_paths,
        validate_prior_worker_paths,
    )

QUOTA_EVIDENCE_KEYS = frozenset(
    {"kind", "provider", "model", "conversation_id", "reset_at_utc"}
)

WORKER_ZERO_NONE_FIELDS = (
    "final_worker_run_id",
    "final_reviewer_run_id",
    "proposal_sha256",
    "proposal_path",
    "reviewer_path",
    "pre_review_test_path",
    "pre_review_test_sha256",
    "pre_review_test_state",
)

LINEAGE_BINDINGS = (
    ("request_id", "original_request_id"),
    ("controller_run_id", "original_controller_run_id"),
    ("task_path", "task_path"),
    ("task_sha256", "task_sha256"),
    ("source_head", "source_head"),
    ("assignment_id", "assignment_id"),
    ("assignment_sha256", "assignment_sha256"),
)


def _validate_common_worker_preconditions(
    admitted_request: Mapping[str, Any],
    validated_terminal_receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any], str, str]:
    if not isinstance(admitted_request, dict):
        raise ProposalError(f"admitted_request must be dict, got {type(admitted_request).__name__}")
    if not isinstance(validated_terminal_receipt, dict):
        raise ProposalError(
            f"validated_terminal_receipt must be dict, got {type(validated_terminal_receipt).__name__}"
        )

    if set(admitted_request.keys()) != _QUOTA_CONTINUATION_KEYS:
        raise ProposalError("admitted_request keys mismatch with QUOTA_CONTINUATION schema")
    if admitted_request.get("schema_version") != "1.0":
        raise ProposalError("admitted_request schema_version must be '1.0'")
    if admitted_request.get("request_type") != "QUOTA_CONTINUATION":
        raise ProposalError("admitted_request request_type must be 'QUOTA_CONTINUATION'")
    if admitted_request.get("role") != "worker":
        raise ProposalError(f"admitted_request role must be 'worker', got {admitted_request.get('role')!r}")

    if set(validated_terminal_receipt.keys()) != RECEIPT_KEYS:
        raise ProposalError("validated_terminal_receipt keys mismatch with RECEIPT_KEYS")

    receipt_req = validated_terminal_receipt.get("request")
    if not isinstance(receipt_req, dict) or set(receipt_req.keys()) != REQUEST_KEYS:
        raise ProposalError("receipt request keys mismatch with ordinary REQUEST_KEYS")

    for req_field, admitted_field in LINEAGE_BINDINGS:
        req_val = receipt_req.get(req_field)
        admitted_val = admitted_request.get(admitted_field)
        if type(req_val) is not type(admitted_val) or req_val != admitted_val:
            raise ProposalError(
                f"Receipt request lineage field {req_field!r} mismatch: "
                f"{req_val!r} != admitted {admitted_field!r} ({admitted_val!r})"
            )

    orig_run_prefix = receipt_req.get("run_prefix")
    if not isinstance(orig_run_prefix, str) or not RUN_PREFIX_RE.fullmatch(orig_run_prefix):
        raise ProposalError(f"receipt request run_prefix invalid: {orig_run_prefix!r}")

    response = validated_terminal_receipt.get("response")
    if not isinstance(response, dict):
        raise ProposalError("receipt response must be dict")
    stdout = response.get("stdout")
    if not isinstance(stdout, str):
        raise ProposalError("receipt response stdout must be str")

    try:
        pipeline_state = unique_json(stdout)
    except Exception as exc:
        raise ProposalError(f"failed to parse pipeline state from stdout: {exc}") from exc

    if not isinstance(pipeline_state, dict) or set(pipeline_state.keys()) != PIPELINE_QUOTA_KEYS:
        raise ProposalError("pipeline state keys mismatch with PIPELINE_QUOTA_KEYS")

    if pipeline_state.get("state") != "TASK_BLOCKED":
        raise ProposalError(f"pipeline state must be 'TASK_BLOCKED', got {pipeline_state.get('state')!r}")
    if pipeline_state.get("blockers") != ["observational quota, no replay authority"]:
        raise ProposalError(f"pipeline blockers sentinel mismatch: {pipeline_state.get('blockers')!r}")

    for bound_field in ("task_id", "semantic_task_fingerprint", "source_head"):
        state_val = pipeline_state.get(bound_field)
        admitted_val = admitted_request.get(bound_field)
        if type(state_val) is not type(admitted_val) or state_val != admitted_val:
            raise ProposalError(
                f"pipeline state field {bound_field!r} mismatch: {state_val!r} != {admitted_val!r}"
            )

    quota_attempt = pipeline_state.get("quota_attempt")
    if not isinstance(quota_attempt, dict) or set(quota_attempt.keys()) != QUOTA_ATTEMPT_KEYS:
        raise ProposalError("pipeline quota_attempt keys mismatch with QUOTA_ATTEMPT_KEYS")
    if quota_attempt.get("role") != "worker":
        raise ProposalError(f"quota_attempt.role must be 'worker', got {quota_attempt.get('role')!r}")

    status_sha = quota_attempt.get("status_sha256")
    if not isinstance(status_sha, str) or not SHA_PATTERN.fullmatch(status_sha):
        raise ProposalError(f"quota_attempt.status_sha256 must be 64 lowercase hex, got {status_sha!r}")

    worker_run_id = quota_attempt.get("run_id")
    if not isinstance(worker_run_id, str):
        raise ProposalError(f"quota_attempt.run_id must be str, got {type(worker_run_id).__name__}")

    receipt_evidence = validated_terminal_receipt.get("quota_evidence")
    if not isinstance(receipt_evidence, dict) or set(receipt_evidence.keys()) != QUOTA_EVIDENCE_KEYS:
        raise ProposalError("receipt quota_evidence keys mismatch with QUOTA_EVIDENCE_KEYS")

    quota_evidence = pipeline_state.get("quota_evidence")
    if not isinstance(quota_evidence, dict) or set(quota_evidence.keys()) != QUOTA_EVIDENCE_KEYS:
        raise ProposalError("pipeline quota_evidence keys mismatch with QUOTA_EVIDENCE_KEYS")

    if quota_evidence != receipt_evidence:
        raise ProposalError("pipeline quota_evidence must equal receipt quota_evidence")

    return pipeline_state, orig_run_prefix, quota_evidence, worker_run_id, status_sha


def derive_worker_zero_resume_artifacts(
    admitted_request: Mapping[str, Any],
    validated_terminal_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure derivation of Worker-0 resume artifacts from continuation request and receipt."""
    pipeline_state, orig_run_prefix, quota_evidence, worker_run_id, status_sha = (
        _validate_common_worker_preconditions(admitted_request, validated_terminal_receipt)
    )

    worker_match = re.fullmatch(
        rf"^{re.escape(orig_run_prefix)}-worker-0(?:-retry-[12])?$",
        worker_run_id,
    )
    if not worker_match:
        raise ProposalError(
            f"quota_attempt.run_id {worker_run_id!r} does not match original run_prefix "
            f"{orig_run_prefix!r} worker-0 pattern"
        )

    correction_count = pipeline_state.get("correction_count")
    if type(correction_count) is not int or type(correction_count) is bool or correction_count != 0:
        raise ProposalError(f"correction_count must be exact int 0, got {correction_count!r}")

    for none_field in WORKER_ZERO_NONE_FIELDS:
        if pipeline_state.get(none_field) is not None:
            raise ProposalError(
                f"{none_field!r} must be None at blocked worker-0, got {pipeline_state.get(none_field)!r}"
            )

    changed_paths = pipeline_state.get("changed_paths")
    if not isinstance(changed_paths, list) or len(changed_paths) != 0:
        raise ProposalError(f"changed_paths must be empty list, got {changed_paths!r}")

    return {
        "task_id": pipeline_state["task_id"],
        "semantic_task_fingerprint": pipeline_state["semantic_task_fingerprint"],
        "source_head": pipeline_state["source_head"],
        "original_run_prefix": orig_run_prefix,
        "attempted_run_id": worker_run_id,
        "status_sha256": status_sha,
        "status_sha": status_sha,
        "quota_evidence": json.loads(json.dumps(quota_evidence)),
        "worker_index": 0,
    }


def derive_worker_resume_artifacts(
    admitted_request: Mapping[str, Any],
    validated_terminal_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure derivation of Worker 0..2 resume artifacts from continuation request and receipt."""
    pipeline_state, orig_run_prefix, quota_evidence, worker_run_id, status_sha = (
        _validate_common_worker_preconditions(admitted_request, validated_terminal_receipt)
    )

    worker_match = re.fullmatch(
        rf"^{re.escape(orig_run_prefix)}-worker-([012])(?:-retry-[12])?$",
        worker_run_id,
    )
    if not worker_match:
        raise ProposalError(
            f"quota_attempt.run_id {worker_run_id!r} does not match original run_prefix "
            f"{orig_run_prefix!r} worker-0..2 pattern"
        )

    attempted_index = int(worker_match.group(1))

    if attempted_index == 0:
        correction_count = pipeline_state.get("correction_count")
        if type(correction_count) is not int or type(correction_count) is bool or correction_count != 0:
            raise ProposalError(f"correction_count must be exact int 0, got {correction_count!r}")

        for none_field in WORKER_ZERO_NONE_FIELDS:
            if pipeline_state.get(none_field) is not None:
                raise ProposalError(
                    f"{none_field!r} must be None at blocked worker-0, got {pipeline_state.get(none_field)!r}"
                )

        changed_paths = pipeline_state.get("changed_paths")
        if not isinstance(changed_paths, list) or len(changed_paths) != 0:
            raise ProposalError(f"changed_paths must be empty list, got {changed_paths!r}")

        return {
            "task_id": pipeline_state["task_id"],
            "semantic_task_fingerprint": pipeline_state["semantic_task_fingerprint"],
            "source_head": pipeline_state["source_head"],
            "original_run_prefix": orig_run_prefix,
            "attempted_run_id": worker_run_id,
            "status_sha256": status_sha,
            "status_sha": status_sha,
            "quota_evidence": json.loads(json.dumps(quota_evidence)),
            "worker_index": 0,
        }

    expected_correction_count = attempted_index - 1
    correction_count = pipeline_state.get("correction_count")
    if (
        type(correction_count) is not int
        or type(correction_count) is bool
        or correction_count != expected_correction_count
    ):
        raise ProposalError(
            f"correction_count must be exact int {expected_correction_count}, got {correction_count!r}"
        )

    prior_worker_id = pipeline_state.get("final_worker_run_id")
    prior_reviewer_id = pipeline_state.get("final_reviewer_run_id")
    reviewer_path = pipeline_state.get("reviewer_path")
    pre_review_test_path = pipeline_state.get("pre_review_test_path")
    pre_review_test_sha = pipeline_state.get("pre_review_test_sha256")
    pre_review_test_state = pipeline_state.get("pre_review_test_state")

    proposal_sha = pipeline_state.get("proposal_sha256")
    if not isinstance(proposal_sha, str) or not SHA_PATTERN.fullmatch(proposal_sha):
        raise ProposalError(
            f"proposal_sha256 must be 64 lowercase hex, got {proposal_sha!r}"
        )

    changed_paths = pipeline_state.get("changed_paths")
    if not isinstance(changed_paths, list) or len(changed_paths) == 0:
        raise ProposalError(f"changed_paths must be non-empty list, got {changed_paths!r}")
    for item in changed_paths:
        if not isinstance(item, str) or len(item) == 0:
            raise ProposalError(f"changed_paths must contain non-empty strings, got {item!r}")

    if prior_reviewer_id is not None:
        if reviewer_path is None:
            raise ProposalError(
                "prior worker/reviewer paths validation failed: reviewer_path cannot be None when final_reviewer_run_id is present"
            )
        if pre_review_test_state != "PASSED":
            raise ProposalError(
                f"pre_review_test_state must be 'PASSED' for reviewer-backed resume, got {pre_review_test_state!r}"
            )

        try:
            canonical_paths = validate_prior_worker_paths(
                orig_run_prefix,
                attempted_index,
                prior_worker_id,
                prior_reviewer_id,
            )
        except (ValueError, TypeError) as exc:
            raise ProposalError(f"prior worker/reviewer paths validation failed: {exc}") from exc

        for path_key in ("proposal_path", "reviewer_path", "pre_review_test_path"):
            state_path = pipeline_state.get(path_key)
            expected_path = canonical_paths[path_key]
            if state_path != expected_path:
                raise ProposalError(
                    f"{path_key} mismatch: expected {expected_path!r}, got {state_path!r}"
                )

        if not isinstance(pre_review_test_sha, str) or not SHA_PATTERN.fullmatch(pre_review_test_sha):
            raise ProposalError(
                f"pre_review_test_sha256 must be 64 lowercase hex, got {pre_review_test_sha!r}"
            )

        return {
            "task_id": pipeline_state["task_id"],
            "semantic_task_fingerprint": pipeline_state["semantic_task_fingerprint"],
            "source_head": pipeline_state["source_head"],
            "original_run_prefix": orig_run_prefix,
            "attempted_run_id": worker_run_id,
            "status_sha256": status_sha,
            "status_sha": status_sha,
            "quota_evidence": json.loads(json.dumps(quota_evidence)),
            "worker_index": attempted_index,
            "correction_origin": "REVIEWER",
            "final_worker_run_id": prior_worker_id,
            "final_reviewer_run_id": prior_reviewer_id,
            "proposal_path": canonical_paths["proposal_path"],
            "proposal_sha256": proposal_sha,
            "reviewer_path": canonical_paths["reviewer_path"],
            "pre_review_test_path": canonical_paths["pre_review_test_path"],
            "pre_review_test_sha256": pre_review_test_sha,
            "pre_review_test_state": "PASSED",
            "changed_paths": list(changed_paths),
            "expected_feedback_path": canonical_paths["expected_feedback_path"],
        }

    # No-reviewer branches: BOOTSTRAP and TEST_FAILED
    if reviewer_path is not None:
        raise ProposalError(
            "prior worker/reviewer paths validation failed: reviewer_path must be None when final_reviewer_run_id is None"
        )

    try:
        canonical_paths = validate_prior_worker_no_reviewer_paths(
            orig_run_prefix,
            attempted_index,
            prior_worker_id,
        )
    except (ValueError, TypeError) as exc:
        raise ProposalError(f"prior worker/reviewer paths validation failed: {exc}") from exc

    state_proposal_path = pipeline_state.get("proposal_path")
    expected_proposal_path = canonical_paths["proposal_path"]
    if state_proposal_path != expected_proposal_path:
        raise ProposalError(
            f"proposal_path mismatch: expected {expected_proposal_path!r}, got {state_proposal_path!r}"
        )

    if pre_review_test_state is None:
        if pre_review_test_path is not None or pre_review_test_sha is not None:
            raise ProposalError(
                "pre_review_test_path and pre_review_test_sha256 must be None for bootstrap resume"
            )

        return {
            "task_id": pipeline_state["task_id"],
            "semantic_task_fingerprint": pipeline_state["semantic_task_fingerprint"],
            "source_head": pipeline_state["source_head"],
            "original_run_prefix": orig_run_prefix,
            "attempted_run_id": worker_run_id,
            "status_sha256": status_sha,
            "status_sha": status_sha,
            "quota_evidence": json.loads(json.dumps(quota_evidence)),
            "worker_index": attempted_index,
            "correction_origin": "BOOTSTRAP",
            "final_worker_run_id": prior_worker_id,
            "final_reviewer_run_id": None,
            "proposal_path": canonical_paths["proposal_path"],
            "proposal_sha256": proposal_sha,
            "reviewer_path": None,
            "pre_review_test_path": None,
            "pre_review_test_sha256": None,
            "pre_review_test_state": None,
            "changed_paths": list(changed_paths),
            "expected_feedback_path": canonical_paths["expected_feedback_path"],
        }

    if pre_review_test_state == "FAILED":
        state_test_path = pipeline_state.get("pre_review_test_path")
        expected_test_path = canonical_paths["pre_review_test_path"]
        if state_test_path != expected_test_path:
            raise ProposalError(
                f"pre_review_test_path mismatch: expected {expected_test_path!r}, got {state_test_path!r}"
            )

        if not isinstance(pre_review_test_sha, str) or not SHA_PATTERN.fullmatch(pre_review_test_sha):
            raise ProposalError(
                f"pre_review_test_sha256 must be 64 lowercase hex, got {pre_review_test_sha!r}"
            )

        return {
            "task_id": pipeline_state["task_id"],
            "semantic_task_fingerprint": pipeline_state["semantic_task_fingerprint"],
            "source_head": pipeline_state["source_head"],
            "original_run_prefix": orig_run_prefix,
            "attempted_run_id": worker_run_id,
            "status_sha256": status_sha,
            "status_sha": status_sha,
            "quota_evidence": json.loads(json.dumps(quota_evidence)),
            "worker_index": attempted_index,
            "correction_origin": "TEST_FAILED",
            "final_worker_run_id": prior_worker_id,
            "final_reviewer_run_id": None,
            "proposal_path": canonical_paths["proposal_path"],
            "proposal_sha256": proposal_sha,
            "reviewer_path": None,
            "pre_review_test_path": canonical_paths["pre_review_test_path"],
            "pre_review_test_sha256": pre_review_test_sha,
            "pre_review_test_state": "FAILED",
            "changed_paths": list(changed_paths),
            "expected_feedback_path": canonical_paths["expected_feedback_path"],
        }

    raise ProposalError(
        f"pre_review_test_state {pre_review_test_state!r} invalid when final_reviewer_run_id is None"
    )


_derive_worker_zero_resume_artifacts = derive_worker_zero_resume_artifacts
