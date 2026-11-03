"""Worker-1 post-validation result binding contract for two-site quota resume.

Authoritative producer context sources:
- scripts/two_site_quota_resume_worker_evidence.py
- scripts/two_site_quota_resume_worker_one_once.py
- scripts/two_site_quota_resume_post_worker_zero.py
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        SHA_PATTERN,
        normalize_finding,
        test_failure_signature,
        test_gate_feedback,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        SHA_PATTERN,
        normalize_finding,
        test_failure_signature,
        test_gate_feedback,
    )

try:
    from scripts.two_site_proposal_runner import (
        validate_correction_feedback,
    )
except ImportError:
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        validate_correction_feedback,
    )

try:
    from scripts.two_site_pre_review_receipt import (
        p1_00_characterization_bootstrap_findings,
        validate_test_receipt,
    )
except ImportError:
    from two_site_pre_review_receipt import (  # type: ignore[no-redef]
        p1_00_characterization_bootstrap_findings,
        validate_test_receipt,
    )

try:
    from scripts.two_site_quota_resume_post_worker_zero import (
        _EXPECTED_RESULT_STATE_KEYS,
    )
except ImportError:
    from two_site_quota_resume_post_worker_zero import (  # type: ignore[no-redef]
        _EXPECTED_RESULT_STATE_KEYS,
    )

__all__ = ["bind_worker_one_result"]

_EXPECTED_WORKER_INFO_KEYS = frozenset({
    "result",
    "status",
    "result_sha",
    "result_text",
    "conversation_id",
})


def bind_worker_one_result(
    ctx: dict[str, Any],
    returned_state: dict[str, Any],
    validated: tuple[dict[str, Any], dict[str, Any], str, str],
) -> dict[str, Any]:
    """Purely bind and validate worker-1 continuation result against context."""
    if not isinstance(ctx, dict):
        raise ProposalError(f"ctx must be dict, got {type(ctx).__name__}")
    if not isinstance(returned_state, dict):
        raise ProposalError(f"returned_state must be dict, got {type(returned_state).__name__}")
    if not isinstance(validated, (tuple, list)) or len(validated) != 4:
        raise ProposalError(f"validated must be 4-tuple, got {type(validated).__name__}")

    worker_result, current_status, result_sha, result_text = validated
    if not isinstance(worker_result, dict) or not isinstance(current_status, dict):
        raise ProposalError("validated result and status must be dicts")
    if not isinstance(result_sha, str) or not isinstance(result_text, str):
        raise ProposalError("validated result_sha and result_text must be strings")

    try:
        parsed_result_json = json.loads(result_text)
        if not isinstance(parsed_result_json, dict):
            raise ProposalError("result_text must encode a JSON object")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProposalError(f"result_text must be valid JSON: {exc}") from exc

    if not SHA_PATTERN.match(result_sha) or result_sha != result_sha.lower():
        raise ProposalError(f"invalid result_sha format: {result_sha!r}")
    if hashlib.sha256(result_text.encode("utf-8")).hexdigest() != result_sha:
        raise ProposalError("result_text SHA256 does not match result_sha")

    artifacts = ctx.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ProposalError("ctx missing or invalid artifacts")
    worker_index = artifacts.get("worker_index")
    if type(worker_index) is not int or type(worker_index) is bool or worker_index != 1:
        raise ProposalError(f"worker_index must be exact int 1, got {worker_index!r}")

    prior_run_id = artifacts.get("final_worker_run_id")
    if not isinstance(prior_run_id, str) or not prior_run_id.strip():
        raise ProposalError("artifacts missing or invalid final_worker_run_id")

    continuation_run_prefix = ctx.get("continuation_run_prefix")
    if not isinstance(continuation_run_prefix, str) or not continuation_run_prefix.strip():
        raise ProposalError("ctx missing or invalid continuation_run_prefix")
    expected_run_id = f"{continuation_run_prefix}-worker-1"

    evidence = ctx.get("evidence")
    if not isinstance(evidence, dict):
        raise ProposalError("ctx missing or invalid evidence")

    prior_worker_info = evidence.get("worker_info")
    if not isinstance(prior_worker_info, dict):
        raise ProposalError("evidence missing or invalid worker_info")
    if "run_id" in prior_worker_info:
        raise ProposalError("prior worker_info must not contain run_id")
    if "status" not in prior_worker_info or not isinstance(prior_worker_info["status"], dict):
        raise ProposalError("prior worker_info missing status dict")
    if set(prior_worker_info.keys()) != _EXPECTED_WORKER_INFO_KEYS:
        raise ProposalError(
            f"prior worker_info keys mismatch: expected {_EXPECTED_WORKER_INFO_KEYS!r}, got {set(prior_worker_info.keys())!r}"
        )

    prior_worker_conv = prior_worker_info.get("conversation_id")
    curr_conv = current_status.get("conversation_id")

    for label, cid in (
        ("prior worker", prior_worker_conv),
        ("current worker", curr_conv),
    ):
        if not isinstance(cid, str) or not cid.strip():
            raise ProposalError(f"{label} conversation_id must be non-empty string")

    correction_origin = artifacts.get("correction_origin")
    if "correction_origin" in evidence and evidence.get("correction_origin") != correction_origin:
        raise ProposalError("mixed correction_origin between artifacts and evidence")
    if "correction_origin" in ctx and ctx.get("correction_origin") != correction_origin:
        raise ProposalError("mixed correction_origin between artifacts and ctx")

    if correction_origin is None:
        origin = "REVIEWER"
    elif correction_origin in ("REVIEWER", "BOOTSTRAP", "TEST_FAILED"):
        origin = correction_origin
    else:
        raise ProposalError(f"unknown correction_origin: {correction_origin!r}")

    feedback = evidence.get("feedback")
    if not isinstance(feedback, dict):
        raise ProposalError("evidence missing or invalid feedback")

    seen_norm_findings: set[str] = set()
    seen_test_signatures: set[str] = set()
    seen_bootstrap_signatures: set[str] = set()

    if origin == "REVIEWER":
        reviewer_status = evidence.get("reviewer_status")
        if not isinstance(reviewer_status, dict):
            raise ProposalError("evidence missing or invalid reviewer_status")
        reviewer_conv = reviewer_status.get("conversation_id")
        if not isinstance(reviewer_conv, str) or not reviewer_conv.strip():
            raise ProposalError("reviewer conversation_id must be non-empty string")

        if len({prior_worker_conv, reviewer_conv, curr_conv}) != 3:
            raise ProposalError("conversation IDs must be distinct across prior worker, reviewer, and current worker")

        reviewer_result = evidence.get("reviewer_result")
        if not isinstance(reviewer_result, dict):
            raise ProposalError("evidence missing or invalid reviewer_result")
        feedback_findings = feedback.get("findings")
        qoder_session = None
        if feedback.get("reviewer") == "qoder":
            qoder = evidence.get("qoder_review")
            qvalue = qoder.get("result", {}) if isinstance(qoder, dict) else {}
            bindings = qoder.get("bindings", {}) if isinstance(qoder, dict) else {}
            qoder_session = qoder.get("proof", {}).get("session_id") if isinstance(qoder, dict) else None
            if (qvalue.get("decision") != "REQUEST_CHANGES"
                    or qvalue.get("proposal_sha256") != artifacts.get("proposal_sha256")
                    or qvalue.get("source_head") != artifacts.get("source_head")
                    or bindings.get("worker_run_id") != prior_run_id
                    or bindings.get("reviewer_run_id") != artifacts.get("final_reviewer_run_id")
                    or reviewer_result.get("status") != "VERIFIED" or reviewer_result.get("findings")
                    or not isinstance(qoder_session, str) or not qoder_session.strip()):
                raise ProposalError("Qoder correction evidence is not bound to genuine Gemini VERIFIED")
            reviewer_findings = qvalue.get("findings")
        elif feedback.get("reviewer") == "gemini":
            reviewer_findings = reviewer_result.get("findings")
        else:
            raise ProposalError("feedback reviewer must be 'gemini' or validated 'qoder'")
        if feedback_findings != reviewer_findings:
            raise ProposalError("feedback findings do not equal reviewer_result findings")
        if not isinstance(feedback_findings, list):
            raise ProposalError("feedback findings must be a list")

        for item in feedback_findings:
            if not isinstance(item, str):
                raise ProposalError(f"finding must be a string, got {type(item).__name__}")
            norm = normalize_finding(item)
            if norm in seen_norm_findings:
                raise ProposalError(f"duplicate normalized finding: {norm!r}")
            seen_norm_findings.add(norm)

        seen_convs = {prior_worker_conv, reviewer_conv, curr_conv}
        if qoder_session is not None:
            if qoder_session in seen_convs:
                raise ProposalError("Qoder correction conversation must remain distinct")
            seen_convs.add(qoder_session)
    else:
        if curr_conv == prior_worker_conv:
            raise ProposalError("current worker conversation must differ from prior Worker conversation")
        seen_convs = {prior_worker_conv, curr_conv}

        if artifacts.get("reviewer_path") is not None:
            raise ProposalError(f"artifacts reviewer_path must be None for {origin}, got {artifacts.get('reviewer_path')!r}")
        if artifacts.get("final_reviewer_run_id") is not None:
            raise ProposalError(
                f"artifacts final_reviewer_run_id must be None for {origin}, got {artifacts.get('final_reviewer_run_id')!r}"
            )
        if evidence.get("reviewer_status") is not None:
            raise ProposalError(f"evidence reviewer_status must be None for {origin}")
        if evidence.get("reviewer_result") is not None:
            raise ProposalError(f"evidence reviewer_result must be None for {origin}")
        if (
            evidence.get("reviewer_sha") is not None
            or evidence.get("reviewer_sha256") is not None
            or evidence.get("reviewer_result_sha") is not None
        ):
            raise ProposalError(f"evidence reviewer sha must be None for {origin}")

        if feedback.get("reviewer") != "test-gate":
            raise ProposalError(f"feedback reviewer must be 'test-gate', got {feedback.get('reviewer')!r}")

        artifacts_proposal_sha = artifacts.get("proposal_sha256")
        if not isinstance(artifacts_proposal_sha, str) or not SHA_PATTERN.fullmatch(artifacts_proposal_sha):
            raise ProposalError(f"artifacts proposal_sha256 invalid: {artifacts_proposal_sha!r}")
        if feedback.get("reviewed_proposal_sha256") != artifacts_proposal_sha:
            raise ProposalError("feedback reviewed_proposal_sha256 does not match artifacts proposal_sha256")
        if prior_worker_info.get("result_sha") != artifacts_proposal_sha:
            raise ProposalError("prior worker_info result_sha does not match artifacts proposal_sha256")

        if origin == "BOOTSTRAP":
            if evidence.get("receipt") is not None or evidence.get("test_receipt") is not None:
                raise ProposalError("BOOTSTRAP origin requires receipt to be None")
            if (
                artifacts.get("pre_review_test_path") is not None
                or artifacts.get("pre_review_test_sha256") is not None
                or artifacts.get("pre_review_test_state") is not None
            ):
                raise ProposalError("BOOTSTRAP artifacts pre_review_test fields must be None")

            prior_worker_result = prior_worker_info.get("result")
            if not isinstance(prior_worker_result, dict):
                raise ProposalError("prior worker_info result must be dict")
            bootstrap_findings = p1_00_characterization_bootstrap_findings(prior_worker_result)
            if not bootstrap_findings:
                raise ProposalError("p1_00_characterization_bootstrap_findings returned empty findings")

            expected_feedback_payload = {
                "reviewer": "test-gate",
                "reviewed_proposal_sha256": artifacts_proposal_sha,
                "findings": bootstrap_findings,
                "test_evidence": [
                    "gate=P1-00-characterization-bootstrap",
                    f"proposal_sha256={artifacts_proposal_sha}",
                    f"finding_count={len(bootstrap_findings)}",
                ],
            }
            expected_feedback = validate_correction_feedback(
                json.dumps(expected_feedback_payload), artifacts_proposal_sha
            )
            if feedback.get("findings") != expected_feedback.get("findings"):
                raise ProposalError("test-gate feedback findings do not match expected findings")
            if feedback.get("test_evidence") != expected_feedback.get("test_evidence"):
                raise ProposalError("test-gate feedback test_evidence does not match expected evidence")
            if feedback != expected_feedback:
                raise ProposalError("test-gate feedback does not match accepted producer payload")

            signature = hashlib.sha256(
                json.dumps(bootstrap_findings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            seen_bootstrap_signatures.add(signature)
        else:
            receipt = evidence.get("receipt")
            if receipt is None and "test_receipt" in evidence:
                receipt = evidence.get("test_receipt")
            if receipt is None:
                raise ProposalError("evidence missing receipt for TEST_FAILED origin")
            if "receipt" in evidence and "test_receipt" in evidence and evidence.get("receipt") != evidence.get("test_receipt"):
                raise ProposalError("evidence contains conflicting receipt and test_receipt")
            if not isinstance(receipt, dict):
                raise ProposalError("receipt must be dict")

            validated_receipt = validate_test_receipt(receipt, artifacts_proposal_sha)
            if validated_receipt.get("state") != "FAILED":
                raise ProposalError(f"validated test_receipt state must be 'FAILED', got {validated_receipt.get('state')!r}")

            if artifacts.get("pre_review_test_state") is not None and artifacts.get("pre_review_test_state") != "FAILED":
                raise ProposalError(
                    f"artifacts pre_review_test_state must be 'FAILED', got {artifacts.get('pre_review_test_state')!r}"
                )

            expected_feedback = test_gate_feedback(validated_receipt, artifacts_proposal_sha)
            if feedback != expected_feedback:
                raise ProposalError("feedback does not match expected test_gate_feedback payload")

            sig = test_failure_signature(validated_receipt)
            seen_test_signatures.add(sig)

    if set(returned_state.keys()) != _EXPECTED_RESULT_STATE_KEYS:
        raise ProposalError(
            f"returned_state keys mismatch: expected {_EXPECTED_RESULT_STATE_KEYS!r}, got {set(returned_state.keys())!r}"
        )
    if returned_state.get("state") != "WORKER_PROPOSAL_READY":
        raise ProposalError(f"unexpected state in returned_state: {returned_state.get('state')!r}")

    corr = returned_state.get("correction_count")
    if type(corr) is not int or type(corr) is bool or corr != 1:
        raise ProposalError(f"correction_count must be exact int 1, got {corr!r}")

    task = ctx.get("task")
    if not isinstance(task, dict) or returned_state.get("task_id") != task.get("task_id"):
        raise ProposalError("task_id mismatch between returned_state and ctx")
    if returned_state.get("source_head") != ctx.get("source_head"):
        raise ProposalError("source_head mismatch between returned_state and ctx")
    if returned_state.get("semantic_task_fingerprint") != artifacts.get("semantic_task_fingerprint"):
        raise ProposalError("semantic_task_fingerprint mismatch between returned_state and artifacts")
    if returned_state.get("final_worker_run_id") != expected_run_id:
        raise ProposalError(
            f"final_worker_run_id mismatch: expected {expected_run_id!r}, got {returned_state.get('final_worker_run_id')!r}"
        )

    expected_proposal_path = f".local/two-site-refactor/proposals/{expected_run_id}/result.json"
    if returned_state.get("proposal_path") != expected_proposal_path:
        raise ProposalError(
            f"proposal_path mismatch: expected {expected_proposal_path!r}, got {returned_state.get('proposal_path')!r}"
        )
    if returned_state.get("proposal_sha256") != result_sha:
        raise ProposalError(
            f"proposal_sha256 mismatch: expected {result_sha!r}, got {returned_state.get('proposal_sha256')!r}"
        )

    if worker_result.get("status") != "PROPOSAL_READY":
        raise ProposalError(f"worker_result status must be PROPOSAL_READY, got {worker_result.get('status')!r}")

    changes = worker_result.get("changes")
    if not isinstance(changes, list):
        raise ProposalError("worker_result changes must be a list")
    expected_changed_paths = [c["path"] for c in changes if isinstance(c, dict) and "path" in c]
    if len(expected_changed_paths) != len(changes):
        raise ProposalError("invalid entry in worker_result changes")
    if returned_state.get("changed_paths") != expected_changed_paths:
        raise ProposalError(
            f"changed_paths mismatch: expected {expected_changed_paths!r}, got {returned_state.get('changed_paths')!r}"
        )

    curr_worker_info = {
        "result": copy.deepcopy(worker_result),
        "status": copy.deepcopy(current_status),
        "result_sha": result_sha,
        "result_text": result_text,
        "conversation_id": curr_conv,
    }
    return {
        "worker_info": curr_worker_info,
        "returned_state": copy.deepcopy(returned_state),
        "current_worker": (expected_run_id, result_sha, result_text, curr_conv),
        "seen_convs": seen_convs,
        "seen_norm_findings": seen_norm_findings,
        "seen_test_signatures": seen_test_signatures,
        "seen_bootstrap_signatures": seen_bootstrap_signatures,
    }
