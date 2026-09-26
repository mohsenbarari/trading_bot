"""Worker-2 file-backed run validation and lineage binding reader."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import re
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
    from scripts.two_site_quota_resume_worker_two_history_complete import (
        complete_worker_two_history,
    )
except ImportError:
    from two_site_quota_resume_worker_two_history_complete import (  # type: ignore[no-redef]
        complete_worker_two_history,
    )

__all__ = ["read_and_bind_worker_two_result"]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

_EXPECTED_RESULT_STATE_KEYS = frozenset({
    "state",
    "task_id",
    "source_head",
    "final_worker_run_id",
    "proposal_sha256",
    "changed_paths",
    "proposal_path",
    "correction_count",
    "semantic_task_fingerprint",
})

_EXPECTED_WORKER_INFO_KEYS = frozenset({
    "result",
    "status",
    "result_sha",
    "result_text",
    "conversation_id",
})

_EXPECTED_OUTPUT_KEYS = frozenset({
    "worker_info",
    "returned_state",
    "current_worker",
    "seen_convs",
    "complete_history",
})


def read_and_bind_worker_two_result(
    root: Path,
    ctx: dict[str, Any],
    returned_state: dict[str, Any],
) -> dict[str, Any]:
    """Validate and bind Worker-2 file-backed run result under caller's lock."""
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

    if set(returned_state.keys()) != _EXPECTED_RESULT_STATE_KEYS:
        raise ProposalError(
            f"returned_state keys mismatch: expected {_EXPECTED_RESULT_STATE_KEYS!r}, "
            f"got {set(returned_state.keys())!r}"
        )

    if returned_state.get("state") != "WORKER_PROPOSAL_READY":
        raise ProposalError(
            f"returned_state state must be 'WORKER_PROPOSAL_READY', got {returned_state.get('state')!r}"
        )

    corr_count = returned_state.get("correction_count")
    if type(corr_count) is not int or type(corr_count) is bool or corr_count != 2:
        raise ProposalError(
            f"returned_state correction_count must be exact int 2, got {corr_count!r}"
        )

    continuation_run_prefix = ctx.get("continuation_run_prefix")
    if not isinstance(continuation_run_prefix, str) or not continuation_run_prefix.strip():
        raise ProposalError("ctx missing or invalid continuation_run_prefix")
    expected_run_id = f"{continuation_run_prefix}-worker-2"

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

    task = ctx.get("task")
    if not isinstance(task, dict):
        raise ProposalError("ctx missing or invalid task")
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ProposalError("task missing or invalid task_id")
    if returned_state.get("task_id") != task_id:
        raise ProposalError(
            f"task_id mismatch: expected {task_id!r}, got {returned_state.get('task_id')!r}"
        )

    source_head = ctx.get("source_head")
    if not isinstance(source_head, str) or not source_head.strip():
        raise ProposalError("ctx missing or invalid source_head")
    if returned_state.get("source_head") != source_head:
        raise ProposalError(
            f"source_head mismatch: expected {source_head!r}, got {returned_state.get('source_head')!r}"
        )

    artifacts = ctx.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ProposalError("ctx missing or invalid artifacts")

    sem_fingerprint = artifacts.get("semantic_task_fingerprint")
    if not isinstance(sem_fingerprint, str) or not sem_fingerprint.strip():
        raise ProposalError("artifacts missing or invalid semantic_task_fingerprint")
    if returned_state.get("semantic_task_fingerprint") != sem_fingerprint:
        raise ProposalError(
            f"semantic_task_fingerprint mismatch: expected {sem_fingerprint!r}, "
            f"got {returned_state.get('semantic_task_fingerprint')!r}"
        )

    sources = ctx.get("sources")
    if not isinstance(sources, dict):
        raise ProposalError("ctx missing or invalid sources")

    admitted_task_path = ctx.get("admitted_task_path")
    if not isinstance(admitted_task_path, (str, Path)):
        raise ProposalError("ctx missing or invalid admitted_task_path")

    evidence = ctx.get("evidence")
    if not isinstance(evidence, dict):
        raise ProposalError("ctx missing or invalid evidence")

    prior_run_id = artifacts.get("final_worker_run_id")
    if not isinstance(prior_run_id, str) or not prior_run_id.strip():
        raise ProposalError("artifacts missing or invalid final_worker_run_id")

    prior_sha = artifacts.get("proposal_sha256")
    if not isinstance(prior_sha, str) or not _SHA_RE.fullmatch(prior_sha):
        raise ProposalError("artifacts missing or invalid proposal_sha256")

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
    if "result_sha256" in prior_status and prior_status["result_sha256"] != prior_sha:
        raise ProposalError("prior status result_sha256 does not match proposal_sha256")

    prior_result_sha = prior_worker_info["result_sha"]
    if not isinstance(prior_result_sha, str):
        raise ProposalError("prior worker_info['result_sha'] must be a str")
    if prior_result_sha != prior_sha:
        raise ProposalError(
            f"prior result_sha mismatch: worker_info {prior_result_sha!r} != artifacts {prior_sha!r}"
        )

    prior_result_text = prior_worker_info["result_text"]
    if not isinstance(prior_result_text, str):
        raise ProposalError("prior worker_info['result_text'] must be a str")

    prior_worker_conv = prior_worker_info["conversation_id"]
    if not isinstance(prior_worker_conv, str) or not prior_worker_conv.strip():
        raise ProposalError("prior worker_info['conversation_id'] missing or empty")

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

    result_file = run_dir / "result.json"
    if result_file.is_symlink() or os.path.islink(result_file):
        raise ProposalError("result file cannot be a symlink")
    if not result_file.is_file():
        raise ProposalError(f"result file does not exist: {result_file}")
    try:
        expected_result_file = result_file.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"cannot resolve result file: {exc}") from exc
    if expected_result_file != result_file:
        raise ProposalError("result file must be a canonical path")

    history = complete_worker_two_history(root, admitted_task_path, artifacts, evidence)
    if not isinstance(history, dict) or history.get("complete") is not True:
        raise ProposalError("worker-2 history must be complete")

    ordered_convs = history.get("ordered_conversation_ids")
    if not isinstance(ordered_convs, list) or not ordered_convs:
        raise ProposalError("history missing or empty ordered_conversation_ids")
    for cid in ordered_convs:
        if not isinstance(cid, str) or not cid.strip():
            raise ProposalError("ordered_conversation_ids contains empty or invalid conversation_id")
    if len(set(ordered_convs)) != len(ordered_convs):
        raise ProposalError(f"duplicate conversation IDs in history: {ordered_convs}")

    initial_seen_convs: set[str] = set(ordered_convs)
    seen_convs: set[str] = set(ordered_convs)
    prior_worker_tuple = (prior_run_id, prior_sha, prior_result_text, prior_worker_conv)

    validated = validate_run(
        root=root,
        run_dir=run_dir,
        role="worker",
        expected_run_id=expected_run_id,
        expected_head=source_head,
        expected_task=task,
        expected_sources=sources,
        seen_convs=seen_convs,
        prior_worker=prior_worker_tuple,
        expected_feedback_sha=expected_feedback_sha,
    )

    if not isinstance(validated, (tuple, list)) or len(validated) != 4:
        raise ProposalError(f"validate_run must return a 4-tuple, got {type(validated).__name__}")

    worker_result, worker_status, result_sha, result_text = validated

    if not isinstance(worker_result, dict):
        raise ProposalError("validate_run result must be a dict")
    if not isinstance(worker_status, dict):
        raise ProposalError("validate_run status must be a dict")
    if not isinstance(result_sha, str) or not _SHA_RE.fullmatch(result_sha):
        raise ProposalError(f"validate_run result_sha must be 64-hex SHA string, got {result_sha!r}")
    if not isinstance(result_text, str):
        raise ProposalError("validate_run result_text must be a str")

    if result_sha == prior_sha:
        raise ProposalError(f"correction proposal SHA must differ from prior in {expected_run_id}")
    if worker_result.get("changes") == prior_changes:
        raise ProposalError(f"correction proposal changes must differ from prior in {expected_run_id}")

    w2_conv_id = worker_status.get("conversation_id")
    if not isinstance(w2_conv_id, str) or not w2_conv_id.strip():
        raise ProposalError(f"worker_status conversation_id missing or empty in {expected_run_id}")

    if w2_conv_id in initial_seen_convs:
        raise ProposalError(f"reused conversation_id in {expected_run_id}")
    seen_convs.add(str(w2_conv_id))

    if returned_state.get("proposal_sha256") != result_sha:
        raise ProposalError(
            f"returned_state proposal_sha256 mismatch: expected {result_sha!r}, "
            f"got {returned_state.get('proposal_sha256')!r}"
        )

    expected_changed_paths = [c["path"] for c in worker_result.get("changes", [])]
    if returned_state.get("changed_paths") != expected_changed_paths:
        raise ProposalError(
            f"returned_state changed_paths mismatch: expected {expected_changed_paths!r}, "
            f"got {returned_state.get('changed_paths')!r}"
        )

    worker_info = {
        "result": copy.deepcopy(worker_result),
        "status": copy.deepcopy(worker_status),
        "result_sha": str(result_sha),
        "result_text": str(result_text),
        "conversation_id": str(w2_conv_id),
    }

    current_worker = (expected_run_id, str(result_sha), str(result_text), str(w2_conv_id))

    return {
        "worker_info": worker_info,
        "returned_state": copy.deepcopy(returned_state),
        "current_worker": current_worker,
        "seen_convs": set(seen_convs),
        "complete_history": copy.deepcopy(history),
    }
