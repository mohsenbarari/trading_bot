from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import ProposalError, validate_run
except ImportError:
    from two_site_proposal_pipeline import ProposalError, validate_run

try:
    from scripts.two_site_quota_resume_worker_paths import validate_prior_worker_paths
except ImportError:
    from two_site_quota_resume_worker_paths import validate_prior_worker_paths

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_ALIAS_KEYS = frozenset({
    "original_prefix",
    "prior_worker_id",
    "prior_reviewer_id",
    "pre_review_sha",
    "proposal_sha",
})
_REQUIRED_ARTIFACT_KEYS = (
    "worker_index",
    "original_run_prefix",
    "final_worker_run_id",
    "final_reviewer_run_id",
    "proposal_path",
    "reviewer_path",
    "pre_review_test_path",
    "proposal_sha256",
    "pre_review_test_sha256",
    "source_head",
)


def validate_bound_prior_reviewer(
    root: str | Path,
    artifacts: Mapping[str, Any],
    worker_info: Mapping[str, Any],
    test_receipt: Mapping[str, Any],
    task: Mapping[str, Any],
    sources: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Validate bound prior reviewer execution and return detached results."""
    if not isinstance(artifacts, Mapping):
        raise ProposalError(f"artifacts must be Mapping, got {type(artifacts).__name__}")

    if any(k in artifacts for k in _ALIAS_KEYS):
        raise ProposalError(
            f"alias keys not permitted in artifacts: {sorted(set(artifacts.keys()) & _ALIAS_KEYS)}"
        )

    for k in _REQUIRED_ARTIFACT_KEYS:
        if k not in artifacts:
            raise ProposalError(f"missing required artifact key: {k!r}")

    worker_index = artifacts["worker_index"]
    if type(worker_index) is not int or type(worker_index) is bool or worker_index not in (1, 2):
        raise ProposalError(f"worker_index must be exact built-in int in (1, 2), got {worker_index!r}")

    source_head = artifacts["source_head"]
    if not isinstance(source_head, str) or not _HEX_40_RE.fullmatch(source_head):
        raise ProposalError(f"source_head must be 40 lowercase hex, got {source_head!r}")

    proposal_sha256 = artifacts["proposal_sha256"]
    if not isinstance(proposal_sha256, str) or not _HEX_64_RE.fullmatch(proposal_sha256):
        raise ProposalError(f"proposal_sha256 must be 64 lowercase hex, got {proposal_sha256!r}")

    pre_review_test_sha256 = artifacts["pre_review_test_sha256"]
    if not isinstance(pre_review_test_sha256, str) or not _HEX_64_RE.fullmatch(pre_review_test_sha256):
        raise ProposalError(f"pre_review_test_sha256 must be 64 lowercase hex, got {pre_review_test_sha256!r}")

    orig_run_prefix = artifacts["original_run_prefix"]
    final_worker_run_id = artifacts["final_worker_run_id"]
    final_reviewer_run_id = artifacts["final_reviewer_run_id"]

    try:
        canonical_paths = validate_prior_worker_paths(
            original_prefix=orig_run_prefix,
            attempted_index=worker_index,
            prior_worker_id=final_worker_run_id,
            prior_reviewer_id=final_reviewer_run_id,
        )
    except (ValueError, TypeError) as exc:
        raise ProposalError(f"prior worker paths validation failed: {exc}") from exc

    for path_key in ("proposal_path", "reviewer_path", "pre_review_test_path"):
        artifact_path = artifacts[path_key]
        expected_path = canonical_paths[path_key]
        if artifact_path != expected_path:
            raise ProposalError(
                f"{path_key} mismatch: expected {expected_path!r}, got {artifact_path!r}"
            )

    if not isinstance(worker_info, Mapping):
        raise ProposalError(f"worker_info must be Mapping, got {type(worker_info).__name__}")

    worker_status = worker_info.get("status")
    if not isinstance(worker_status, Mapping):
        raise ProposalError(f"worker_info status must be Mapping, got {type(worker_status).__name__}")

    conv_id = worker_info.get("conversation_id")
    status_conv_id = worker_status.get("conversation_id")
    if conv_id is None:
        conv_id = status_conv_id
    elif status_conv_id is not None and conv_id != status_conv_id:
        raise ProposalError("conversation_id mismatch between worker_info and status")
    if not isinstance(conv_id, str) or not conv_id.strip():
        raise ProposalError("conversation_id missing or invalid in worker_info")

    result_text = worker_info.get("result_text")
    if not isinstance(result_text, str) or not result_text.strip():
        raise ProposalError("worker_info result_text missing or invalid")

    result_sha = worker_info.get("result_sha")
    if not isinstance(result_sha, str) or result_sha != proposal_sha256:
        raise ProposalError(
            f"worker_info result_sha {result_sha!r} must equal proposal_sha256 {proposal_sha256!r}"
        )

    if not isinstance(test_receipt, Mapping):
        raise ProposalError(f"test_receipt must be Mapping, got {type(test_receipt).__name__}")
    if not isinstance(task, Mapping):
        raise ProposalError(f"task must be Mapping, got {type(task).__name__}")
    if not isinstance(sources, Mapping):
        raise ProposalError(f"sources must be Mapping, got {type(sources).__name__}")

    root_path = Path(root)
    run_dir = root_path / Path(canonical_paths["reviewer_path"]).parent

    seen_convs = {conv_id}
    prior_worker = (final_worker_run_id, proposal_sha256, result_text, conv_id)

    result, status, run_result_sha, _ = validate_run(
        root=root_path,
        run_dir=run_dir,
        role="verifier",
        expected_run_id=final_reviewer_run_id,
        expected_head=source_head,
        expected_task=dict(task),
        expected_sources=dict(sources),
        seen_convs=seen_convs,
        prior_worker=prior_worker,
        expected_test_receipt=dict(test_receipt),
        expected_test_evidence_sha=pre_review_test_sha256,
    )

    if result.get("status") != "CHANGES_REQUIRED" or status.get("state") != "CHANGES_REQUIRED":
        raise ProposalError(
            f"reviewer must be CHANGES_REQUIRED, got result status {result.get('status')!r} "
            f"and state {status.get('state')!r}"
        )

    findings = result.get("findings")
    if not isinstance(findings, list) or len(findings) == 0:
        raise ProposalError("reviewer findings must be non-empty list for CHANGES_REQUIRED")

    return copy.deepcopy(result), copy.deepcopy(status), str(run_result_sha)
