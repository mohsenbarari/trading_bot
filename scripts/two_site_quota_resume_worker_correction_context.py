from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from typing import Any, Iterator

try:
    from scripts.two_site_proposal_runner import MAX_CONTEXT, bounded_file_bytes
except ImportError:
    try:
        from scripts.two_site_proposal_pipeline import MAX_CONTEXT  # type: ignore[assignment]
    except ImportError:
        MAX_CONTEXT = 256 * 1024
    try:
        from scripts.two_site_proposal_runner import bounded_file_bytes
    except ImportError:
        from two_site_proposal_runner import bounded_file_bytes  # type: ignore[no-redef]

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
from scripts.two_site_quota_resume_worker_evidence import (
    load_worker_correction_evidence,
)


def _validate_origin_and_reviewer_status(
    root: Path,
    artifacts: dict[str, Any],
    evidence: dict[str, Any],
    *,
    readmission: bool = False,
) -> bytes | None:
    origin = artifacts.get("correction_origin")
    if origin is None:
        origin = "REVIEWER"

    if origin == "REVIEWER":
        reviewer_path = artifacts.get("reviewer_path")
        if not isinstance(reviewer_path, (str, Path)):
            suffix = " on re-admission" if readmission else ""
            raise ProposalError(f"artifacts missing reviewer_path{suffix}")
        status_path = Path(reviewer_path).with_name("status.json")
        return bounded_file_bytes(
            root,
            status_path,
            MAX_CONTEXT,
            "prior reviewer status",
        )

    if origin in ("BOOTSTRAP", "TEST_FAILED"):
        if artifacts.get("reviewer_path") is not None:
            raise ProposalError(
                f"artifacts reviewer_path must be None for {origin}, got {artifacts.get('reviewer_path')!r}"
            )
        if artifacts.get("final_reviewer_run_id") is not None:
            raise ProposalError(
                f"artifacts final_reviewer_run_id must be None for {origin}, got {artifacts.get('final_reviewer_run_id')!r}"
            )
        for key in ("reviewer_status", "reviewer_result", "reviewer_sha"):
            if evidence.get(key) is not None:
                raise ProposalError(
                    f"evidence {key} must be None for {origin}, got {evidence.get(key)!r}"
                )
        return None

    raise ProposalError(f"artifacts correction_origin invalid: {origin!r}")


@contextmanager
def worker_correction_continuation_context(
    root: Path,
    request: dict,
) -> Iterator[dict[str, Any]]:
    """Yield detached worker correction continuation context under pipeline_lock.

    Validates Worker-1 and Worker-2 continuation requests under lock with strict double
    admission, receipt/evidence re-reads, reviewer status byte check, and check_state.
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

        orig_request_id_1 = admitted_1.get("original_request_id")
        if not isinstance(orig_request_id_1, str):
            raise ProposalError("missing original_request_id in admitted request")

        terminal_receipt_1 = read_terminal_quota_record(root, orig_request_id_1)
        artifacts_1 = derive_worker_resume_artifacts(admitted_1, terminal_receipt_1)

        worker_index_1 = artifacts_1.get("worker_index")
        if (
            type(worker_index_1) is not int
            or type(worker_index_1) is bool
            or worker_index_1 not in (1, 2)
        ):
            raise ProposalError(
                f"worker_index must be 1 or 2, got {worker_index_1!r}"
            )

        evidence_1 = load_worker_correction_evidence(root, task_path_1, artifacts_1)

        initial_refs = ref_snapshot(root)
        task = evidence_1.get("task")
        sources = evidence_1.get("sources")
        if task is None or sources is None:
            task, sources = load_task(root, task_path_1)

        check_state(
            root,
            artifacts_1["source_head"],
            initial_refs,
            task_path_1,
            task,
            sources,
        )

        status_bytes_1 = _validate_origin_and_reviewer_status(
            root,
            artifacts_1,
            evidence_1,
            readmission=False,
        )

        admitted_2, task_path_2, _ = validate_quota_continuation_request(
            root, request
        )
        if admitted_2 != admitted_1:
            raise ProposalError("continuation request drifted across re-admission")
        if task_path_2 != task_path_1:
            raise ProposalError("admitted task path drifted across re-admission")

        orig_request_id_2 = admitted_2.get("original_request_id")
        if not isinstance(orig_request_id_2, str):
            raise ProposalError("missing original_request_id in re-admitted request")

        terminal_receipt_2 = read_terminal_quota_record(root, orig_request_id_2)
        if terminal_receipt_2 != terminal_receipt_1:
            raise ProposalError("terminal receipt drifted across re-admission")

        artifacts_2 = derive_worker_resume_artifacts(admitted_2, terminal_receipt_2)
        if artifacts_2 != artifacts_1:
            raise ProposalError("resume artifacts drifted across re-admission")

        worker_index_2 = artifacts_2.get("worker_index")
        if (
            type(worker_index_2) is not int
            or type(worker_index_2) is bool
            or worker_index_2 not in (1, 2)
        ):
            raise ProposalError(
                f"worker_index must be 1 or 2, got {worker_index_2!r}"
            )

        evidence_2 = load_worker_correction_evidence(root, task_path_2, artifacts_2)
        if evidence_2 != evidence_1:
            raise ProposalError("correction evidence drifted across re-admission")

        status_bytes_2 = _validate_origin_and_reviewer_status(
            root,
            artifacts_2,
            evidence_2,
            readmission=True,
        )
        if status_bytes_2 != status_bytes_1:
            raise ProposalError(
                "prior reviewer status bytes drifted across re-admission"
            )

        check_state(
            root,
            artifacts_2["source_head"],
            initial_refs,
            task_path_2,
            task,
            sources,
        )

        yield {
            "continuation_run_prefix": admitted_2["run_prefix"],
            "original_run_prefix": artifacts_2["original_run_prefix"],
            "source_head": artifacts_2["source_head"],
            "initial_refs": copy.deepcopy(initial_refs),
            "original_receipt": copy.deepcopy(terminal_receipt_2),
            "original_terminal_receipt": copy.deepcopy(terminal_receipt_2),
            "admitted_request": copy.deepcopy(admitted_2),
            "admitted_task_path": task_path_2,
            "artifacts": copy.deepcopy(artifacts_2),
            "evidence": copy.deepcopy(evidence_2),
            "task": copy.deepcopy(task),
            "sources": copy.deepcopy(sources),
        }


def prepare_worker_correction_continuation(
    root: Path,
    request: dict,
) -> dict[str, Any]:
    """Prepare and return detached worker correction continuation context under lock."""
    with worker_correction_continuation_context(root, request) as ctx:
        return ctx
