"""Execution preparation for quota continuation in verifier role.

Lock-file creation (.pipeline.lock via pipeline_lock) is the ONLY allowed persistence.
No Provider session, claim publication, semantic reservation, Broker queue,
ordinary run_pipeline, auto-wake or live hook execution authority is granted.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from typing import Any, Iterator

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        check_state,
        pipeline_lock,
        ref_snapshot,
        _derive_verifier_resume_artifacts,
        _load_verifier_pre_review_receipt,
    )
    from scripts.two_site_proposal_runner import load_task
    from scripts.two_site_provider_broker import read_terminal_quota_record
    from scripts.two_site_quota_continuation_admission import (
        ContinuationAdmissionError,
        validate_quota_continuation_request,
    )
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
except ImportError:
    from two_site_proposal_pipeline import (
        ProposalError,
        check_state,
        pipeline_lock,
        ref_snapshot,
        _derive_verifier_resume_artifacts,
        _load_verifier_pre_review_receipt,
    )
    from two_site_proposal_runner import load_task
    from two_site_provider_broker import read_terminal_quota_record
    from two_site_quota_continuation_admission import (
        ContinuationAdmissionError,
        validate_quota_continuation_request,
    )
    from two_site_quota_resume_worker_reader import load_prior_worker_chain

__all__ = [
    "prepare_verifier_continuation",
    "verifier_continuation_context",
]


@contextmanager
def verifier_continuation_context(
    root: Path,
    continuation_request: dict,
) -> Iterator[dict[str, Any]]:
    """Yield detached verifier continuation context while holding pipeline_lock.

    Lock-file creation (.pipeline.lock via pipeline_lock) is the only allowed persistence.
    Produces pure detached context without provider execution authority.
    """
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    if type(continuation_request) is not dict:
        raise ProposalError(f"continuation_request must be dict, got {type(continuation_request).__name__}")
    if continuation_request.get("role") != "verifier":
        raise ProposalError(
            f"role must be 'verifier', got {continuation_request.get('role')!r}"
        )

    with pipeline_lock(root):
        admitted_1, task_path_1, _ = validate_quota_continuation_request(
            root, continuation_request
        )
        if admitted_1.get("role") != "verifier":
            raise ProposalError(
                f"admitted request role must be 'verifier', got {admitted_1.get('role')!r}"
            )

        orig_request_id = admitted_1.get("original_request_id")
        if not isinstance(orig_request_id, str):
            raise ProposalError("missing original_request_id in admitted request")

        terminal_receipt = read_terminal_quota_record(root, orig_request_id)
        artifacts = _derive_verifier_resume_artifacts(admitted_1, terminal_receipt)

        test_receipt = _load_verifier_pre_review_receipt(root, artifacts)
        worker_info = load_prior_worker_chain(root, task_path_1, artifacts)

        initial_refs = ref_snapshot(root)
        task, sources = load_task(root, task_path_1)
        check_state(
            root,
            artifacts["source_head"],
            initial_refs,
            task_path_1,
            task,
            sources,
        )

        admitted_2, task_path_2, _ = validate_quota_continuation_request(
            root, continuation_request
        )
        if admitted_2 != admitted_1:
            raise ProposalError("continuation request drifted across re-admission")
        if task_path_2 != task_path_1:
            raise ProposalError("admitted task path drifted across re-admission")

        task_2, sources_2 = load_task(root, task_path_2)
        if task_2 != task or sources_2 != sources:
            raise ProposalError("task or sources drifted across re-admission")

        check_state(
            root,
            artifacts["source_head"],
            initial_refs,
            task_path_2,
            task_2,
            sources_2,
        )

        yield {
            "continuation_run_prefix": admitted_2["run_prefix"],
            "original_run_prefix": artifacts["original_run_prefix"],
            "worker_run_id": artifacts["final_worker_run_id"],
            "final_worker_run_id": artifacts["final_worker_run_id"],
            "proposal_sha256": artifacts["proposal_sha256"],
            "proposal_path": artifacts["proposal_path"],
            "pre_review_test_sha256": artifacts["pre_review_test_sha256"],
            "pre_review_test_path": artifacts["pre_review_test_path"],
            "test_receipt_sha256": artifacts["pre_review_test_sha256"],
            "test_receipt_path": artifacts["pre_review_test_path"],
            "source_head": artifacts["source_head"],
            "original_receipt": copy.deepcopy(terminal_receipt),
            "original_terminal_receipt": copy.deepcopy(terminal_receipt),
            "admitted_request": copy.deepcopy(admitted_2),
            "admitted_task_path": task_path_2,
            "artifacts": copy.deepcopy(artifacts),
            "validated_test_receipt": copy.deepcopy(test_receipt),
            "validated_worker": copy.deepcopy(worker_info),
            "task": copy.deepcopy(task_2),
            "sources": copy.deepcopy(sources_2),
        }


def prepare_verifier_continuation(
    root: Path,
    continuation_request: dict,
) -> dict[str, Any]:
    """Prepare detached verifier continuation context under pipeline_lock.

    Lock-file creation (.pipeline.lock via pipeline_lock) is the only allowed persistence.
    Produces pure detached context without provider execution authority.
    """
    with verifier_continuation_context(root, continuation_request) as context:
        return context
