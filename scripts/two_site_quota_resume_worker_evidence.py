"""Read-only loader and validator for Worker correction evidence in two-site quota resume."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import ProposalError
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]

try:
    from scripts.two_site_proposal_runner import load_task
except ImportError:
    from two_site_proposal_runner import load_task  # type: ignore[no-redef]

try:
    from scripts.two_site_quota_resume_worker_prerequisites import (
        load_prior_worker_and_receipt,
    )
except ImportError:
    from two_site_quota_resume_worker_prerequisites import (  # type: ignore[no-redef]
        load_prior_worker_and_receipt,
    )

try:
    from scripts.two_site_quota_resume_reviewer_call import (
        validate_bound_prior_reviewer,
    )
except ImportError:
    from two_site_quota_resume_reviewer_call import (  # type: ignore[no-redef]
        validate_bound_prior_reviewer,
    )

try:
    from scripts.two_site_quota_resume_worker_feedback import (
        load_bound_correction_feedback,
        load_bound_test_gate_feedback,
    )
except ImportError:
    from two_site_quota_resume_worker_feedback import (  # type: ignore[no-redef]
        load_bound_correction_feedback,
        load_bound_test_gate_feedback,
    )


def load_worker_correction_evidence(
    root: Path,
    admitted_task_path: str | Path,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    """Load, validate, and return detached correction evidence for attempted Worker-1/2."""
    prior_bundle = load_prior_worker_and_receipt(root, admitted_task_path, artifacts)
    worker_info = prior_bundle["worker_info"]
    receipt = prior_bundle["receipt"]

    task, sources = load_task(root, admitted_task_path)

    origin = artifacts.get("correction_origin")
    if origin is None:
        origin = "REVIEWER"

    if origin == "REVIEWER":
        reviewer_result, reviewer_status, reviewer_sha = validate_bound_prior_reviewer(
            root, artifacts, worker_info, receipt, task, sources
        )
        feedback, feedback_sha, feedback_path = load_bound_correction_feedback(
            root, artifacts, reviewer_result, receipt
        )
        detached_reviewer_result = copy.deepcopy(reviewer_result)
        detached_reviewer_status = copy.deepcopy(reviewer_status)
        detached_reviewer_sha: str | None = str(reviewer_sha)
    elif origin in ("BOOTSTRAP", "TEST_FAILED"):
        feedback, feedback_sha, feedback_path = load_bound_test_gate_feedback(
            root, artifacts, worker_info, receipt
        )
        detached_reviewer_result = None
        detached_reviewer_status = None
        detached_reviewer_sha = None
    else:
        raise ProposalError(f"artifacts correction_origin invalid: {origin!r}")

    try:
        expected_feedback_path = artifacts["expected_feedback_path"]
    except (KeyError, TypeError) as exc:
        raise ProposalError(f"artifacts missing expected_feedback_path: {exc}") from exc

    if feedback_path != expected_feedback_path:
        raise ProposalError(
            f"feedback path mismatch: expected {expected_feedback_path!r}, got {feedback_path!r}"
        )

    return {
        "artifacts": copy.deepcopy(dict(artifacts)),
        "feedback": copy.deepcopy(feedback),
        "feedback_path": str(feedback_path),
        "feedback_sha": str(feedback_sha),
        "receipt": copy.deepcopy(receipt),
        "reviewer_result": detached_reviewer_result,
        "reviewer_sha": detached_reviewer_sha,
        "reviewer_status": detached_reviewer_status,
        "sources": copy.deepcopy(sources),
        "task": copy.deepcopy(task),
        "worker_info": copy.deepcopy(worker_info),
    }
