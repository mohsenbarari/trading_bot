from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        load_task,
        validate_run,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        load_task,
        validate_run,
    )

HEAD_RE = re.compile(r"^[0-9a-f]{40}$")


def load_prior_worker_zero(
    root: Path | str,
    admitted_task_path: Path | str,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(root, (str, Path)):
        raise ProposalError(f"root must be str or Path, got {type(root).__name__}")
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ProposalError(f"canonical root directory does not exist: {root_path}")

    if not isinstance(artifacts, Mapping):
        raise ProposalError(f"artifacts must be a Mapping, got {type(artifacts).__name__}")

    if "correction_count" not in artifacts:
        raise ProposalError("missing correction_count in artifacts")
    corr = artifacts["correction_count"]
    if isinstance(corr, bool) or not isinstance(corr, int):
        raise ProposalError(f"invalid correction_count type: {type(corr).__name__}")
    if corr in (1, 2):
        raise ProposalError(f"unsupported correction_count: {corr}")
    if corr != 0:
        raise ProposalError(f"invalid correction_count index: {corr}")

    task_id = artifacts.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ProposalError(f"invalid or missing task_id: {task_id!r}")

    source_head = artifacts.get("source_head")
    if not isinstance(source_head, str) or not HEAD_RE.match(source_head):
        raise ProposalError(f"invalid source_head: {source_head!r}")

    orig_prefix = artifacts.get("original_run_prefix")
    if not isinstance(orig_prefix, str) or not RUN_PREFIX_RE.match(orig_prefix):
        raise ProposalError(f"invalid original_run_prefix: {orig_prefix!r}")

    final_worker_run_id = artifacts.get("final_worker_run_id")
    if not isinstance(final_worker_run_id, str):
        raise ProposalError("missing or non-string final_worker_run_id")
    worker_id_re = re.compile(f"^{re.escape(orig_prefix)}-worker-0(-retry-[12])?$")
    if not worker_id_re.match(final_worker_run_id):
        raise ProposalError(f"invalid final_worker_run_id: {final_worker_run_id!r}")

    expected_proposal_path = f".local/two-site-refactor/proposals/{final_worker_run_id}/result.json"
    proposal_path = artifacts.get("proposal_path")
    if proposal_path != expected_proposal_path:
        raise ProposalError(
            f"invalid proposal_path: expected {expected_proposal_path!r}, got {proposal_path!r}"
        )

    proposal_sha256 = artifacts.get("proposal_sha256")
    if not isinstance(proposal_sha256, str) or not SHA_PATTERN.match(proposal_sha256):
        raise ProposalError(f"invalid proposal_sha256: {proposal_sha256!r}")

    run_dir = root_path / ".local" / "two-site-refactor" / "proposals" / final_worker_run_id
    if run_dir.is_symlink():
        raise ProposalError(f"run_dir cannot be a symlink: {run_dir}")

    task, sources = load_task(root_path, admitted_task_path)
    if not isinstance(task, Mapping) or task.get("task_id") != task_id:
        actual_task_id = task.get("task_id") if isinstance(task, Mapping) else None
        raise ProposalError(f"task task_id mismatch: task={actual_task_id!r} != artifacts={task_id!r}")

    result, status, result_sha, result_text = validate_run(
        root_path,
        run_dir,
        "worker",
        final_worker_run_id,
        source_head,
        task,
        sources,
        seen_convs=set(),
    )

    if result_sha != proposal_sha256:
        raise ProposalError(
            f"proposal SHA mismatch: validated {result_sha} != artifacts {proposal_sha256}"
        )

    return {
        "result": copy.deepcopy(result),
        "status": copy.deepcopy(status),
        "result_sha": result_sha,
        "result_text": result_text,
        "conversation_id": status.get("conversation_id"),
    }
