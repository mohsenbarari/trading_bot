"""Read-only helper to complete Worker-2 history."""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        MAX_CONTEXT,
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        _load_verifier_pre_review_receipt,
        load_task,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        _load_verifier_pre_review_receipt,
        load_task,
    )

try:
    from scripts.two_site_proposal_runner import bounded_file_bytes
except ImportError:
    try:
        from two_site_proposal_runner import bounded_file_bytes  # type: ignore[no-redef]
    except ImportError:
        from scripts.two_site_proposal_pipeline import bounded_file_bytes  # type: ignore[no-redef]

try:
    from scripts.two_site_quota_resume_worker_two_history_base import (
        derive_worker_two_history_base,
    )
except ImportError:
    from two_site_quota_resume_worker_two_history_base import (  # type: ignore[no-redef]
        derive_worker_two_history_base,
    )

try:
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_zero
except ImportError:
    from two_site_quota_resume_worker_reader import load_prior_worker_zero  # type: ignore[no-redef]

try:
    from scripts.two_site_quota_resume_reviewer_call import (
        validate_bound_prior_reviewer,
    )
except ImportError:
    from two_site_quota_resume_reviewer_call import (  # type: ignore[no-redef]
        validate_bound_prior_reviewer,
    )

_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")


def complete_worker_two_history(
    root: Path | str,
    admitted_task_path: Path | str,
    artifacts: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Complete detached read-only history for Worker-2 resume."""
    if not isinstance(root, (str, Path)):
        raise ProposalError(f"root must be str or Path, got {type(root).__name__}")
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ProposalError(f"canonical root directory does not exist: {root_path}")

    if not isinstance(artifacts, Mapping):
        raise ProposalError(f"artifacts must be Mapping, got {type(artifacts).__name__}")
    if not isinstance(evidence, Mapping):
        raise ProposalError(f"evidence must be Mapping, got {type(evidence).__name__}")

    base = derive_worker_two_history_base(root_path, artifacts, evidence)
    if not isinstance(base, dict):
        raise ProposalError(f"derive_worker_two_history_base returned {type(base).__name__}, expected dict")

    orig_prefix = artifacts.get("original_run_prefix")
    if not isinstance(orig_prefix, str) or not RUN_PREFIX_RE.fullmatch(orig_prefix):
        raise ProposalError(f"invalid original_run_prefix in artifacts: {orig_prefix!r}")

    candidate_ids = (
        f"{orig_prefix}-reviewer-0",
        f"{orig_prefix}-reviewer-0-retry-1",
        f"{orig_prefix}-reviewer-0-retry-2",
    )

    fb_kind = base.get("feedback1_kind")
    needs_reviewer0 = base.get("needs_reviewer0")

    if fb_kind == "test-gate":
        if needs_reviewer0 is not False:
            raise ProposalError(
                f"test-gate history requires needs_reviewer0 False, got {needs_reviewer0!r}"
            )
        for cid in candidate_ids:
            cand_path = root_path / ".local" / "two-site-refactor" / "proposals" / cid / "result.json"
            if os.path.lexists(cand_path):
                raise ProposalError(
                    f"unexpected Reviewer0 result candidate found in test-gate history: {cid}"
                )

        w0_conv = base["worker0_conversation_id"]
        w1_conv = base["worker1_conversation_id"]
        r1_conv = base["reviewer1_conversation_id"]
        ordered_convs = [w0_conv, w1_conv, r1_conv]

        out = copy.deepcopy(base)
        out["complete"] = True
        out["conversation_ids"] = ordered_convs
        out["ordered_conversation_ids"] = list(ordered_convs)
        return out

    if fb_kind == "gemini":
        if needs_reviewer0 is not True:
            raise ProposalError(
                f"gemini history requires needs_reviewer0 True, got {needs_reviewer0!r}"
            )

        task_id = artifacts.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProposalError(f"invalid or missing task_id in artifacts: {task_id!r}")

        source_head = artifacts.get("source_head")
        if not isinstance(source_head, str) or not _HEAD_RE.fullmatch(source_head):
            raise ProposalError(f"invalid source_head in artifacts: {source_head!r}")

        worker0_id = base["worker0_run_id"]
        worker0_sha = base["prior_worker0_sha"]
        worker0_proposal_path = f".local/two-site-refactor/proposals/{worker0_id}/result.json"

        worker0_artifacts = {
            "correction_count": 0,
            "task_id": str(task_id),
            "source_head": str(source_head),
            "original_run_prefix": str(orig_prefix),
            "final_worker_run_id": str(worker0_id),
            "proposal_path": str(worker0_proposal_path),
            "proposal_sha256": str(worker0_sha),
        }

        worker0_info = load_prior_worker_zero(root_path, admitted_task_path, worker0_artifacts)
        if not isinstance(worker0_info, Mapping):
            raise ProposalError("load_prior_worker_zero must return Mapping")
        if worker0_info.get("conversation_id") != base["worker0_conversation_id"]:
            raise ProposalError(
                f"Worker0 conversation_id mismatch: {worker0_info.get('conversation_id')!r} "
                f"!= {base['worker0_conversation_id']!r}"
            )

        test_rel_path = f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-0.json"
        raw_test_bytes = bounded_file_bytes(
            root_path,
            Path(test_rel_path),
            MAX_CONTEXT,
            "pre-review test receipt",
        )
        receipt_sha = hashlib.sha256(raw_test_bytes).hexdigest()

        test_artifacts = {
            "original_run_prefix": str(orig_prefix),
            "correction_count": 0,
            "pre_review_test_path": str(test_rel_path),
            "proposal_sha256": str(worker0_sha),
            "pre_review_test_sha256": str(receipt_sha),
            "pre_review_test_state": "PASSED",
        }
        test_receipt = _load_verifier_pre_review_receipt(root_path, test_artifacts)
        if not isinstance(test_receipt, Mapping):
            raise ProposalError("test receipt must be a Mapping")
        if test_receipt.get("state") != "PASSED":
            raise ProposalError(
                f"pre-review test receipt state must be 'PASSED', got {test_receipt.get('state')!r}"
            )
        if test_receipt.get("proposal_sha256") != worker0_sha:
            raise ProposalError(
                f"pre-review test receipt proposal_sha256 mismatch: "
                f"{test_receipt.get('proposal_sha256')!r} != {worker0_sha!r}"
            )

        task, sources = load_task(root_path, admitted_task_path)

        valid_candidates: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []
        for cid in candidate_ids:
            cand_rel_path = f".local/two-site-refactor/proposals/{cid}/result.json"
            cand_full_path = root_path / ".local" / "two-site-refactor" / "proposals" / cid / "result.json"
            if not os.path.lexists(cand_full_path):
                continue

            rev_artifacts = {
                "worker_index": 1,
                "original_run_prefix": str(orig_prefix),
                "final_worker_run_id": str(worker0_id),
                "final_reviewer_run_id": str(cid),
                "proposal_path": str(worker0_proposal_path),
                "reviewer_path": str(cand_rel_path),
                "pre_review_test_path": str(test_rel_path),
                "proposal_sha256": str(worker0_sha),
                "pre_review_test_sha256": str(receipt_sha),
                "source_head": str(source_head),
            }

            rev_result, rev_status, rev_result_sha = validate_bound_prior_reviewer(
                root=root_path,
                artifacts=rev_artifacts,
                worker_info=worker0_info,
                test_receipt=test_receipt,
                task=task,
                sources=sources,
            )
            valid_candidates.append((cid, rev_result, rev_status, rev_result_sha))

        if len(valid_candidates) == 0:
            raise ProposalError(f"no valid Reviewer0 candidate found for prefix {orig_prefix!r}")
        if len(valid_candidates) > 1:
            found_cids = [c[0] for c in valid_candidates]
            raise ProposalError(f"multiple valid Reviewer0 candidates found: {found_cids}")

        chosen_cid, rev_result, rev_status, rev_result_sha = valid_candidates[0]

        expected_findings = base.get("feedback1", {}).get("findings")
        actual_findings = rev_result.get("findings")
        if actual_findings != expected_findings:
            raise ProposalError(
                f"Reviewer0 findings mismatch: validated reviewer findings {actual_findings!r} "
                f"!= feedback1 findings {expected_findings!r}"
            )

        rev_conv_id = rev_status.get("conversation_id")
        if not isinstance(rev_conv_id, str) or not rev_conv_id.strip():
            raise ProposalError(f"invalid Reviewer0 conversation_id: {rev_conv_id!r}")

        w0_conv = base["worker0_conversation_id"]
        w1_conv = base["worker1_conversation_id"]
        r1_conv = base["reviewer1_conversation_id"]
        ordered_convs = [w0_conv, rev_conv_id, w1_conv, r1_conv]
        if len(set(ordered_convs)) != 4:
            raise ProposalError(
                f"duplicate conversation IDs detected across Worker0, Reviewer0, Worker1, Reviewer1: {ordered_convs}"
            )

        out = copy.deepcopy(base)
        out["complete"] = True
        out["reviewer0_id"] = str(chosen_cid)
        out["reviewer0_run_id"] = str(chosen_cid)
        out["reviewer0_conversation_id"] = str(rev_conv_id)
        out["conversation_ids"] = ordered_convs
        out["ordered_conversation_ids"] = list(ordered_convs)
        out["reviewer0_result"] = copy.deepcopy(rev_result)
        out["reviewer0_status"] = copy.deepcopy(rev_status)
        out["reviewer0_result_sha"] = str(rev_result_sha)
        return out

    raise ProposalError(f"unsupported feedback1_kind: {fb_kind!r}")
