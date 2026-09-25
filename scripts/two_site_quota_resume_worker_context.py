from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from typing import Any, Iterator

from scripts.two_site_quota_resume_execution import (
    ProposalError,
    check_state,
    load_task,
    pipeline_lock,
    read_terminal_quota_record,
    ref_snapshot,
    validate_quota_continuation_request,
)
from scripts.two_site_quota_resume_worker_artifacts import (
    derive_worker_resume_artifacts,
)


@contextmanager
def worker_zero_continuation_context(
    root: Path,
    request: dict,
) -> Iterator[dict[str, Any]]:
    """Yield detached worker-0 continuation context while holding pipeline_lock.

    Lock-file creation (.pipeline.lock via pipeline_lock) is the only allowed persistence.
    Produces pure detached context without provider execution authority.
    """
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    if type(request) is not dict:
        raise ProposalError(f"request must be dict, got {type(request).__name__}")
    if request.get("role") != "worker":
        raise ProposalError(
            f"role must be 'worker', got {request.get('role')!r}"
        )

    with pipeline_lock(root):
        admitted_1, task_path_1, _ = validate_quota_continuation_request(
            root, request
        )
        if admitted_1.get("role") != "worker":
            raise ProposalError(
                f"admitted request role must be 'worker', got {admitted_1.get('role')!r}"
            )

        orig_request_id = admitted_1.get("original_request_id")
        if not isinstance(orig_request_id, str):
            raise ProposalError("missing original_request_id in admitted request")

        terminal_receipt = read_terminal_quota_record(root, orig_request_id)
        artifacts = derive_worker_resume_artifacts(admitted_1, terminal_receipt)
        if artifacts.get("worker_index") != 0:
            raise ProposalError(
                f"worker_index must be 0, got {artifacts.get('worker_index')!r}"
            )

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
            root, request
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
            "source_head": artifacts["source_head"],
            "initial_refs": copy.deepcopy(initial_refs),
            "original_receipt": copy.deepcopy(terminal_receipt),
            "original_terminal_receipt": copy.deepcopy(terminal_receipt),
            "admitted_request": copy.deepcopy(admitted_2),
            "admitted_task_path": task_path_2,
            "artifacts": copy.deepcopy(artifacts),
            "task": copy.deepcopy(task_2),
            "sources": copy.deepcopy(sources_2),
        }


def prepare_worker_zero_continuation(
    root: Path,
    request: dict,
) -> dict[str, Any]:
    """Prepare and return detached worker zero continuation context under lock."""
    with worker_zero_continuation_context(root, request) as ctx:
        return ctx
