from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        MAX_CONTEXT,
        ProposalError,
        SHA_PATTERN,
        _load_verifier_pre_review_receipt,
        bounded_file_bytes,
        unique_json,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        ProposalError,
        SHA_PATTERN,
        _load_verifier_pre_review_receipt,
        bounded_file_bytes,
        unique_json,
    )

try:
    from scripts.two_site_pre_review_receipt import (
        SandboxError,
        validate_test_receipt,
    )
except ImportError:
    from two_site_pre_review_receipt import (  # type: ignore[no-redef]
        SandboxError,
        validate_test_receipt,
    )

try:
    from scripts.two_site_quota_resume_worker_paths import (
        validate_prior_worker_no_reviewer_paths,
    )
except ImportError:
    from two_site_quota_resume_worker_paths import (  # type: ignore[no-redef]
        validate_prior_worker_no_reviewer_paths,
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

    origin = artifacts.get("correction_origin")
    if origin is None:
        origin = "REVIEWER"
    elif origin not in ("REVIEWER", "BOOTSTRAP", "TEST_FAILED"):
        raise ProposalError(f"artifacts correction_origin invalid: {origin!r}")

    if origin == "REVIEWER":
        if artifacts.get("final_reviewer_run_id") is None:
            raise ProposalError("final_reviewer_run_id cannot be None for REVIEWER origin")
        if artifacts.get("reviewer_path") is None:
            raise ProposalError("reviewer_path cannot be None for REVIEWER origin")
        if artifacts.get("pre_review_test_state") != "PASSED":
            raise ProposalError(
                f"pre_review_test_state must be 'PASSED' for REVIEWER origin, got {artifacts.get('pre_review_test_state')!r}"
            )
    else:
        if artifacts.get("final_reviewer_run_id") is not None:
            raise ProposalError(f"final_reviewer_run_id must be None for {origin} origin")
        if artifacts.get("reviewer_path") is not None:
            raise ProposalError(f"reviewer_path must be None for {origin} origin")

        orig_run_prefix = artifacts.get("original_run_prefix")
        prior_worker_id = artifacts.get("final_worker_run_id")
        try:
            canonical_paths = validate_prior_worker_no_reviewer_paths(
                orig_run_prefix,
                worker_index,
                prior_worker_id,
            )
        except (ValueError, TypeError) as exc:
            raise ProposalError(f"prior worker paths validation failed: {exc}") from exc

        if artifacts.get("proposal_path") != canonical_paths["proposal_path"]:
            raise ProposalError(
                f"proposal_path mismatch: expected {canonical_paths['proposal_path']!r}, got {artifacts.get('proposal_path')!r}"
            )
        if artifacts.get("expected_feedback_path") != canonical_paths["expected_feedback_path"]:
            raise ProposalError(
                f"expected_feedback_path mismatch: expected {canonical_paths['expected_feedback_path']!r}, got {artifacts.get('expected_feedback_path')!r}"
            )

        if origin == "BOOTSTRAP":
            if artifacts.get("pre_review_test_path") is not None:
                raise ProposalError("pre_review_test_path must be None for BOOTSTRAP origin")
            if artifacts.get("pre_review_test_sha256") is not None:
                raise ProposalError("pre_review_test_sha256 must be None for BOOTSTRAP origin")
            if artifacts.get("pre_review_test_state") is not None:
                raise ProposalError("pre_review_test_state must be None for BOOTSTRAP origin")
        elif origin == "TEST_FAILED":
            if artifacts.get("pre_review_test_state") != "FAILED":
                raise ProposalError(
                    f"pre_review_test_state must be 'FAILED' for TEST_FAILED origin, got {artifacts.get('pre_review_test_state')!r}"
                )
            if artifacts.get("pre_review_test_path") != canonical_paths["pre_review_test_path"]:
                raise ProposalError(
                    f"pre_review_test_path mismatch: expected {canonical_paths['pre_review_test_path']!r}, got {artifacts.get('pre_review_test_path')!r}"
                )
            receipt_sha = artifacts.get("pre_review_test_sha256")
            if not isinstance(receipt_sha, str) or not SHA_PATTERN.fullmatch(receipt_sha):
                raise ProposalError(
                    f"artifacts pre_review_test_sha256 must be 64 lowercase hex, got {receipt_sha!r}"
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

    if origin == "BOOTSTRAP":
        return {
            "worker_info": copy.deepcopy(worker_info),
            "receipt": None,
            "prior_index": prior_index,
        }

    if origin == "REVIEWER":
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

    # TEST_FAILED
    receipt_rel = canonical_paths["pre_review_test_path"]
    receipt_path = resolved_root / receipt_rel

    if receipt_path.is_symlink() or os.path.islink(receipt_path):
        raise ProposalError("pre-review test receipt cannot be a symlink")

    if not receipt_path.exists() and not os.path.lexists(receipt_path):
        raise ProposalError(f"pre-review test receipt does not exist: {receipt_path}")

    try:
        resolved_receipt_path = receipt_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"pre-review test receipt does not exist: {exc}") from exc

    if not resolved_receipt_path.is_relative_to(resolved_root):
        raise ProposalError("pre-review test receipt escapes project root")

    if resolved_receipt_path != receipt_path:
        raise ProposalError("pre-review test receipt cannot be a symlink")

    try:
        st = receipt_path.stat()
    except OSError as exc:
        raise ProposalError(f"cannot stat pre-review test receipt: {exc}") from exc

    if not stat.S_ISREG(st.st_mode):
        raise ProposalError("pre-review test receipt must be a regular file")

    if st.st_nlink != 1:
        raise ProposalError("pre-review test receipt cannot be a hardlink")

    raw_bytes = bounded_file_bytes(
        resolved_root,
        Path(receipt_rel),
        MAX_CONTEXT,
        "pre-review test receipt",
    )

    computed_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_sha != receipt_sha:
        raise ProposalError(
            f"pre-review test receipt SHA mismatch: computed {computed_sha!r} != expected {receipt_sha!r}"
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProposalError("pre-review test receipt is not valid UTF-8") from exc

    try:
        parsed = unique_json(text)
    except (ValueError, TypeError, KeyError) as exc:
        raise ProposalError(f"failed to parse pre-review test receipt JSON: {exc}") from exc

    try:
        normalized = validate_test_receipt(parsed, proposal_sha, require_passed=False)
    except (SandboxError, ValueError, TypeError, KeyError) as exc:
        raise ProposalError(f"pre-review test receipt validation failed: {exc}") from exc

    receipt_state = (
        normalized.get("state")
        if isinstance(normalized, Mapping)
        else getattr(normalized, "state", None)
    )
    if receipt_state != "FAILED":
        raise ProposalError(
            f"validated receipt state must be 'FAILED', got {receipt_state!r}"
        )

    return {
        "worker_info": copy.deepcopy(worker_info),
        "receipt": copy.deepcopy(normalized),
        "prior_index": prior_index,
    }
