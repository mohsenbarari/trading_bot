from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        SHA_PATTERN,
        _load_verifier_pre_review_receipt,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        SHA_PATTERN,
        _load_verifier_pre_review_receipt,
    )

try:
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
except ImportError:
    from two_site_quota_resume_worker_reader import load_prior_worker_chain  # type: ignore[no-redef]


def load_prior_worker_and_receipt(
    root: Path,
    admitted_task_path: str | Path,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    """Load prior worker chain and verifier pre-review receipt without mutating inputs."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"project root does not exist: {exc}") from exc
    if not resolved_root.is_dir():
        raise ProposalError("project root must be a directory")

    if not isinstance(admitted_task_path, (str, Path)):
        raise ProposalError(
            f"admitted_task_path must be str or Path, got {type(admitted_task_path).__name__}"
        )

    if not isinstance(artifacts, Mapping) or isinstance(artifacts, (str, bytes)):
        raise ProposalError(f"artifacts must be a mapping, got {type(artifacts).__name__}")

    worker_index = artifacts.get("worker_index")
    if type(worker_index) is not int or type(worker_index) is bool or worker_index not in (1, 2):
        raise ProposalError(
            f"artifacts worker_index must be exact int 1 or 2, got {worker_index!r}"
        )

    proposal_sha = artifacts.get("proposal_sha256")
    if not isinstance(proposal_sha, str) or not SHA_PATTERN.fullmatch(proposal_sha):
        raise ProposalError(
            f"artifacts proposal_sha256 must be 64 lowercase hex, got {proposal_sha!r}"
        )

    prior_index = worker_index - 1
    prior_artifacts = copy.deepcopy(dict(artifacts))
    prior_artifacts["correction_count"] = prior_index

    worker_info = load_prior_worker_chain(resolved_root, admitted_task_path, prior_artifacts)
    worker_sha = (
        worker_info.get("result_sha")
        if isinstance(worker_info, Mapping)
        else getattr(worker_info, "result_sha", None)
    )
    if worker_sha != proposal_sha:
        raise ProposalError(
            f"worker_info.result_sha mismatch: {worker_sha!r} != {proposal_sha!r}"
        )

    receipt = _load_verifier_pre_review_receipt(resolved_root, prior_artifacts)
    receipt_state = (
        receipt.get("state")
        if isinstance(receipt, Mapping)
        else getattr(receipt, "state", None)
    )
    if receipt_state != "PASSED":
        raise ProposalError(
            f"validated receipt state must be 'PASSED', got {receipt_state!r}"
        )

    receipt_sha = (
        receipt.get("proposal_sha256")
        if isinstance(receipt, Mapping)
        else getattr(receipt, "proposal_sha256", None)
    )
    if receipt_sha != proposal_sha:
        raise ProposalError(
            f"validated receipt proposal_sha256 mismatch: {receipt_sha!r} != {proposal_sha!r}"
        )

    return {
        "worker_info": copy.deepcopy(worker_info),
        "receipt": copy.deepcopy(receipt),
        "prior_index": prior_index,
    }
