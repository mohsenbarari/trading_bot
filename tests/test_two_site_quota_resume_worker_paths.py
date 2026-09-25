"""Hermetic tests for two_site_quota_resume_worker_paths."""

from __future__ import annotations

import pytest

from scripts.two_site_quota_resume_worker_paths import validate_prior_worker_paths


@pytest.mark.parametrize("attempted_index", [1, 2])
@pytest.mark.parametrize("worker_suffix", ["", "-retry-1", "-retry-2"])
@pytest.mark.parametrize("reviewer_suffix", ["", "-retry-1", "-retry-2"])
def test_validate_prior_worker_paths_positive(
    attempted_index: int, worker_suffix: str, reviewer_suffix: str
) -> None:
    prefix = "run-20260925-abc"
    prior_idx = attempted_index - 1
    worker_id = f"{prefix}-worker-{prior_idx}{worker_suffix}"
    reviewer_id = f"{prefix}-reviewer-{prior_idx}{reviewer_suffix}"

    paths = validate_prior_worker_paths(prefix, attempted_index, worker_id, reviewer_id)

    assert paths == {
        "proposal_path": f".local/two-site-refactor/proposals/{worker_id}/result.json",
        "reviewer_path": f".local/two-site-refactor/proposals/{reviewer_id}/result.json",
        "pre_review_test_path": f".local/two-site-refactor/pipeline/{prefix}/pre-review-tests-{prior_idx}.json",
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{prefix}/feedback-correction-{attempted_index}.json",
    }
    assert worker_id in paths["proposal_path"]
    assert reviewer_id in paths["reviewer_path"]
    assert len(paths) == 4


@pytest.mark.parametrize(
    ("prefix", "idx", "worker_id", "reviewer_id"),
    [
        ("run-1", 1, "run-1-worker-0-retry-3", "run-1-reviewer-0"),
        ("run-1", 1, "run-1-worker-0", "run-1-reviewer-0-retry-3"),
        ("run-1", 1, "run-1-worker-1", "run-1-reviewer-0"),
        ("run-1", 2, "run-1-worker-0", "run-1-reviewer-1"),
        ("run-1", 1, "run-1-worker-0", "run-1-reviewer-1"),
        ("run-1", 1, "run-1-reviewer-0", "run-1-reviewer-0"),
        ("run-1", 1, "run-1-worker-0", "run-1-worker-0"),
        ("run-1", 1, "other-worker-0", "run-1-reviewer-0"),
        ("run-1", 1, "run-1-worker-0", "other-reviewer-0"),
        ("run-1", True, "run-1-worker-0", "run-1-reviewer-0"),
        ("run-1", False, "run-1-worker-0", "run-1-reviewer-0"),
        ("run-1", 0, "run-1-worker-0", "run-1-reviewer-0"),
        ("run-1", 3, "run-1-worker-0", "run-1-reviewer-0"),
        ("run-1", "1", "run-1-worker-0", "run-1-reviewer-0"),
        ("", 1, "-worker-0", "-reviewer-0"),
        ("-bad", 1, "-bad-worker-0", "-bad-reviewer-0"),
        (123, 1, "123-worker-0", "123-reviewer-0"),
        ("run-1", 1, None, "run-1-reviewer-0"),
        ("run-1", 1, "run-1-worker-0", None),
    ],
)
def test_validate_prior_worker_paths_negative(
    prefix: object, idx: object, worker_id: object, reviewer_id: object
) -> None:
    with pytest.raises((ValueError, TypeError)):
        validate_prior_worker_paths(prefix, idx, worker_id, reviewer_id)  # type: ignore[arg-type]
