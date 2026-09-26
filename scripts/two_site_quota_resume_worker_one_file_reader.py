"""Worker-1 file-backed run validation and lineage binding reader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        validate_run,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        validate_run,
    )

try:
    from scripts.two_site_quota_resume_worker_one_result_contract import (
        bind_worker_one_result,
    )
except ImportError:
    from two_site_quota_resume_worker_one_result_contract import (  # type: ignore[no-redef]
        bind_worker_one_result,
    )

__all__ = ["read_and_bind_worker_one_result"]

_EXPECTED_WORKER_INFO_KEYS = frozenset({
    "result",
    "status",
    "result_sha",
    "result_text",
    "conversation_id",
})


def read_and_bind_worker_one_result(
    root: Path,
    ctx: dict[str, Any],
    returned_state: dict[str, Any],
) -> dict[str, Any]:
    """Validate and bind Worker-1 file-backed run result under caller's lock."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    if not isinstance(ctx, dict):
        raise ProposalError(f"ctx must be dict, got {type(ctx).__name__}")
    if not isinstance(returned_state, dict):
        raise ProposalError(f"returned_state must be dict, got {type(returned_state).__name__}")

    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"root directory does not exist: {exc}") from exc
    if not root.is_dir() or resolved_root != root:
        raise ProposalError("root must be a resolved canonical directory")

    continuation_run_prefix = ctx.get("continuation_run_prefix")
    if not isinstance(continuation_run_prefix, str) or not continuation_run_prefix.strip():
        raise ProposalError("ctx missing or invalid continuation_run_prefix")
    expected_run_id = f"{continuation_run_prefix}-worker-1"

    proposals_dir = root / ".local/two-site-refactor/proposals"
    run_dir = proposals_dir / expected_run_id

    if run_dir.is_symlink() or os.path.islink(run_dir):
        raise ProposalError("run_dir cannot be a symlink")
    if not run_dir.is_dir():
        raise ProposalError(f"run_dir does not exist or is not a directory: {run_dir}")
    try:
        expected_run_dir = run_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"cannot resolve run_dir: {exc}") from exc
    if expected_run_dir != run_dir:
        raise ProposalError("run_dir must be a canonical directory")

    if returned_state.get("final_worker_run_id") != expected_run_id:
        raise ProposalError(
            f"final_worker_run_id mismatch: expected {expected_run_id!r}, "
            f"got {returned_state.get('final_worker_run_id')!r}"
        )
    expected_proposal_path = f".local/two-site-refactor/proposals/{expected_run_id}/result.json"
    if returned_state.get("proposal_path") != expected_proposal_path:
        raise ProposalError(
            f"proposal_path mismatch: expected {expected_proposal_path!r}, "
            f"got {returned_state.get('proposal_path')!r}"
        )

    artifacts = ctx.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ProposalError("ctx missing or invalid artifacts")

    prior_run_id = artifacts.get("final_worker_run_id")
    if not isinstance(prior_run_id, str) or not prior_run_id.strip():
        raise ProposalError("artifacts missing or invalid final_worker_run_id")

    prior_sha = artifacts.get("proposal_sha256")
    if not isinstance(prior_sha, str) or not prior_sha.strip():
        raise ProposalError("artifacts missing or invalid proposal_sha256")

    evidence = ctx.get("evidence")
    if not isinstance(evidence, dict):
        raise ProposalError("ctx missing or invalid evidence")

    expected_feedback_sha = evidence.get("feedback_sha")
    if not isinstance(expected_feedback_sha, str) or not expected_feedback_sha.strip():
        raise ProposalError("evidence missing or invalid feedback_sha")

    prior_worker_info = evidence.get("worker_info")
    if not isinstance(prior_worker_info, dict):
        raise ProposalError("evidence missing or invalid worker_info")

    if set(prior_worker_info.keys()) != _EXPECTED_WORKER_INFO_KEYS:
        raise ProposalError(
            f"prior worker_info keys mismatch: expected {_EXPECTED_WORKER_INFO_KEYS!r}, "
            f"got {set(prior_worker_info.keys())!r}"
        )

    prior_result = prior_worker_info["result"]
    if not isinstance(prior_result, dict):
        raise ProposalError("prior worker_info['result'] must be a dict")
    if "changes" not in prior_result or not isinstance(prior_result["changes"], list):
        raise ProposalError("prior worker_info['result']['changes'] must be a list")
    prior_changes = prior_result["changes"]

    prior_status = prior_worker_info["status"]
    if not isinstance(prior_status, dict):
        raise ProposalError("prior worker_info['status'] must be a dict")

    prior_result_sha = prior_worker_info["result_sha"]
    if not isinstance(prior_result_sha, str):
        raise ProposalError("prior worker_info['result_sha'] must be a str")
    if prior_result_sha != prior_sha:
        raise ProposalError(
            f"prior result_sha mismatch: worker_info {prior_result_sha!r} != artifacts {prior_sha!r}"
        )
    if "result_sha256" in prior_status and prior_status["result_sha256"] != prior_sha:
        raise ProposalError("prior status result_sha256 does not match proposal_sha256")

    prior_result_text = prior_worker_info["result_text"]
    if not isinstance(prior_result_text, str):
        raise ProposalError("prior worker_info['result_text'] must be a str")

    prior_worker_conv = prior_worker_info["conversation_id"]
    if not isinstance(prior_worker_conv, str) or not prior_worker_conv.strip():
        raise ProposalError("prior worker_info['conversation_id'] missing or empty")

    reviewer_status = evidence.get("reviewer_status")
    if not isinstance(reviewer_status, dict):
        raise ProposalError("evidence missing or invalid reviewer_status")
    reviewer_conv = reviewer_status.get("conversation_id")
    if not isinstance(reviewer_conv, str) or not reviewer_conv.strip():
        raise ProposalError("reviewer conversation_id missing or empty")
    if reviewer_conv == prior_worker_conv:
        raise ProposalError("reviewer conversation_id must differ from prior worker conversation_id")

    source_head = ctx.get("source_head")
    if not isinstance(source_head, str) or not source_head.strip():
        raise ProposalError("ctx missing or invalid source_head")

    task = ctx.get("task")
    if not isinstance(task, dict):
        raise ProposalError("ctx missing or invalid task")

    sources = ctx.get("sources")
    if not isinstance(sources, dict):
        raise ProposalError("ctx missing or invalid sources")

    seen_convs: set[str] = {prior_worker_conv, reviewer_conv}
    prior_worker_tuple = (prior_run_id, prior_sha, prior_result_text, prior_worker_conv)

    validated = validate_run(
        root,
        run_dir,
        "worker",
        expected_run_id,
        source_head,
        task,
        sources,
        seen_convs=seen_convs,
        prior_worker=prior_worker_tuple,
        expected_feedback_sha=expected_feedback_sha,
    )

    if not isinstance(validated, (tuple, list)) or len(validated) != 4:
        raise ProposalError(f"validate_run must return a 4-tuple, got {type(validated).__name__}")

    worker_result, _, result_sha, _ = validated

    if not isinstance(worker_result, dict):
        raise ProposalError("validate_run result must be a dict")
    if not isinstance(result_sha, str):
        raise ProposalError("validate_run result_sha must be a str")

    if result_sha == prior_sha:
        raise ProposalError(f"correction proposal SHA must differ from prior in {expected_run_id}")
    if worker_result.get("changes") == prior_changes:
        raise ProposalError(f"correction proposal changes must differ from prior in {expected_run_id}")

    return bind_worker_one_result(ctx, returned_state, validated)
