"""Read-only validator for bound correction feedback in two-site quota resume."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
    )
except ImportError:
    from two_site_proposal_pipeline import (
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
    )

try:
    from scripts.two_site_proposal_runner import (
        bounded_file_bytes,
        validate_correction_feedback,
    )
except ImportError:
    from two_site_proposal_runner import (
        bounded_file_bytes,
        validate_correction_feedback,
    )

try:
    from scripts.two_site_pre_review_receipt import validate_test_receipt
except ImportError:
    from two_site_pre_review_receipt import validate_test_receipt

try:
    from scripts.two_site_quota_resume_worker_paths import validate_prior_worker_paths
except ImportError:
    from two_site_quota_resume_worker_paths import validate_prior_worker_paths

MAX_FEEDBACK_BYTES = 65536
DEFAULT_MAX_FEEDBACK_BYTES = MAX_FEEDBACK_BYTES

FORBIDDEN_ALIAS_KEYS = frozenset(
    {"attempted_index", "original_prefix", "prior_worker_id", "prior_reviewer_id"}
)

REQUIRED_ARTIFACT_KEYS = (
    "worker_index",
    "original_run_prefix",
    "final_worker_run_id",
    "final_reviewer_run_id",
    "proposal_path",
    "reviewer_path",
    "pre_review_test_path",
    "expected_feedback_path",
    "proposal_sha256",
    "pre_review_test_sha256",
    "pre_review_test_state",
)


def load_bound_correction_feedback(
    root: Path | str,
    artifacts: Mapping[str, Any],
    reviewer_result: Mapping[str, Any],
    test_receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """Validate and load bound correction feedback for Worker resume (read-only)."""
    if not isinstance(root, (Path, str)):
        raise ProposalError("root must be a Path or str")
    root_path = Path(root)

    if not isinstance(artifacts, Mapping) or isinstance(artifacts, (str, bytes)):
        raise ProposalError("artifacts must be a mapping")

    if any(k in artifacts for k in FORBIDDEN_ALIAS_KEYS):
        raise ProposalError("artifacts contains forbidden alias keys from task 250")

    for key in REQUIRED_ARTIFACT_KEYS:
        if key not in artifacts:
            raise ProposalError(f"artifacts missing required real key: {key!r}")

    worker_index = artifacts["worker_index"]
    if isinstance(worker_index, bool) or not isinstance(worker_index, int):
        raise ProposalError("artifacts worker_index must be int, not bool")
    if worker_index not in (1, 2):
        raise ProposalError(f"artifacts worker_index must be in {{1, 2}}, got {worker_index!r}")

    orig_prefix = artifacts["original_run_prefix"]
    if not isinstance(orig_prefix, str) or not RUN_PREFIX_RE.fullmatch(orig_prefix):
        raise ProposalError(f"artifacts original_run_prefix is invalid: {orig_prefix!r}")

    final_worker_id = artifacts["final_worker_run_id"]
    if not isinstance(final_worker_id, str):
        raise ProposalError("artifacts final_worker_run_id must be str")

    final_reviewer_id = artifacts["final_reviewer_run_id"]
    if not isinstance(final_reviewer_id, str):
        raise ProposalError("artifacts final_reviewer_run_id must be str")

    try:
        canonical_paths = validate_prior_worker_paths(
            orig_prefix,
            worker_index,
            final_worker_id,
            final_reviewer_id,
        )
    except (ValueError, TypeError) as exc:
        raise ProposalError(f"prior worker/reviewer paths validation failed: {exc}") from exc

    for path_key in ("proposal_path", "reviewer_path", "pre_review_test_path", "expected_feedback_path"):
        state_path = artifacts.get(path_key)
        expected_path = canonical_paths[path_key]
        if state_path != expected_path:
            raise ProposalError(
                f"artifacts {path_key} mismatch: expected {expected_path!r}, got {state_path!r}"
            )

    proposal_sha = artifacts["proposal_sha256"]
    if not isinstance(proposal_sha, str) or not SHA_PATTERN.fullmatch(proposal_sha):
        raise ProposalError(f"artifacts proposal_sha256 must be 64 lowercase hex: {proposal_sha!r}")

    pre_review_test_sha = artifacts["pre_review_test_sha256"]
    if not isinstance(pre_review_test_sha, str) or not SHA_PATTERN.fullmatch(pre_review_test_sha):
        raise ProposalError(
            f"artifacts pre_review_test_sha256 must be 64 lowercase hex: {pre_review_test_sha!r}"
        )

    if artifacts["pre_review_test_state"] != "PASSED":
        raise ProposalError(
            f"artifacts pre_review_test_state must be 'PASSED', got {artifacts['pre_review_test_state']!r}"
        )

    if not isinstance(reviewer_result, Mapping) or isinstance(reviewer_result, (str, bytes)):
        raise ProposalError("reviewer_result must be a mapping")

    if reviewer_result.get("status") != "CHANGES_REQUIRED":
        raise ProposalError(
            f"reviewer_result status must be 'CHANGES_REQUIRED', got {reviewer_result.get('status')!r}"
        )

    reviewer_findings = reviewer_result.get("findings")
    if (
        not isinstance(reviewer_findings, list)
        or not reviewer_findings
        or len(reviewer_findings) > 32
        or any(not isinstance(f, str) or not f.strip() for f in reviewer_findings)
    ):
        raise ProposalError("reviewer_result findings must be a nonempty list of strings (max 32)")

    validated_receipt = validate_test_receipt(
        test_receipt,
        proposal_sha,
        require_passed=True,
    )

    feedback_rel_path = canonical_paths["expected_feedback_path"]
    raw_bytes = bounded_file_bytes(
        root=root_path,
        path=Path(feedback_rel_path),
        limit=MAX_FEEDBACK_BYTES,
        label="correction feedback",
    )
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    try:
        feedback_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProposalError(f"correction feedback is not valid UTF-8: {exc}") from exc

    feedback = validate_correction_feedback(feedback_text, proposal_sha)

    if feedback.get("reviewer") != "gemini":
        raise ProposalError(f"correction feedback reviewer must be 'gemini', got {feedback.get('reviewer')!r}")

    if feedback.get("findings") != reviewer_findings:
        raise ProposalError("correction feedback findings do not match reviewer findings in exact order")

    selectors = validated_receipt["selectors"]
    expected_evidence = (
        f"receipt_sha256={pre_review_test_sha} "
        f"proposal_sha256={proposal_sha} "
        f"selector_count={len(selectors)}"
    )
    if feedback.get("test_evidence") != [expected_evidence]:
        raise ProposalError(
            f"correction feedback test_evidence mismatch: expected [{expected_evidence!r}], "
            f"got {feedback.get('test_evidence')!r}"
        )

    detached_feedback = json.loads(json.dumps(feedback))
    return detached_feedback, raw_sha, feedback_rel_path
