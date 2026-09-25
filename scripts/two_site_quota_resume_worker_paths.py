"""Canonical helper for validating and resolving prior worker and reviewer paths."""

from __future__ import annotations

import re

from scripts.two_site_proposal_pipeline import RUN_PREFIX_RE


def validate_prior_worker_paths(
    original_prefix: str,
    attempted_index: int,
    prior_worker_id: str,
    prior_reviewer_id: str,
) -> dict[str, str]:
    """Validate prior worker/reviewer IDs and return canonical pipeline paths."""
    if not isinstance(original_prefix, str) or not RUN_PREFIX_RE.fullmatch(original_prefix):
        raise ValueError(f"Invalid original_prefix: {original_prefix!r}")

    if isinstance(attempted_index, bool) or not isinstance(attempted_index, int):
        raise TypeError(f"attempted_index must be int, not bool: {attempted_index!r}")
    if attempted_index not in (1, 2):
        raise ValueError(f"attempted_index must be in {{1, 2}}, got: {attempted_index!r}")

    if not isinstance(prior_worker_id, str):
        raise TypeError(f"prior_worker_id must be str, got: {type(prior_worker_id).__name__}")
    if not isinstance(prior_reviewer_id, str):
        raise TypeError(f"prior_reviewer_id must be str, got: {type(prior_reviewer_id).__name__}")

    prior_index = attempted_index - 1
    prefix_esc = re.escape(original_prefix)

    worker_re = rf"^{prefix_esc}-worker-{prior_index}(-retry-[12])?$"
    if not re.fullmatch(worker_re, prior_worker_id):
        raise ValueError(
            f"prior_worker_id {prior_worker_id!r} does not match expected pattern for index {prior_index}"
        )

    reviewer_re = rf"^{prefix_esc}-reviewer-{prior_index}(-retry-[12])?$"
    if not re.fullmatch(reviewer_re, prior_reviewer_id):
        raise ValueError(
            f"prior_reviewer_id {prior_reviewer_id!r} does not match expected pattern for index {prior_index}"
        )

    return {
        "proposal_path": f".local/two-site-refactor/proposals/{prior_worker_id}/result.json",
        "reviewer_path": f".local/two-site-refactor/proposals/{prior_reviewer_id}/result.json",
        "pre_review_test_path": f".local/two-site-refactor/pipeline/{original_prefix}/pre-review-tests-{prior_index}.json",
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{original_prefix}/feedback-correction-{attempted_index}.json",
    }
