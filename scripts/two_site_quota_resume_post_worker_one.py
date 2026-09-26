from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from typing import Any, Iterator

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        bounded_file_bytes,
    )
    from scripts.two_site_quota_resume_execution import (
        ProposalError,
        check_state,
        read_terminal_quota_record,
        validate_quota_continuation_request,
    )
    from scripts.two_site_quota_resume_worker_artifacts import (
        derive_worker_resume_artifacts,
    )
    from scripts.two_site_quota_resume_worker_correction_context import (
        worker_correction_continuation_context,
    )
    from scripts.two_site_quota_resume_worker_evidence import (
        load_worker_correction_evidence,
    )
    from scripts.two_site_quota_resume_worker_one_file_reader import (
        read_and_bind_worker_one_result,
    )
except ImportError:
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        bounded_file_bytes,
    )
    from two_site_quota_resume_execution import (  # type: ignore[no-redef]
        ProposalError,
        check_state,
        read_terminal_quota_record,
        validate_quota_continuation_request,
    )
    from two_site_quota_resume_worker_artifacts import (  # type: ignore[no-redef]
        derive_worker_resume_artifacts,
    )
    from two_site_quota_resume_worker_correction_context import (  # type: ignore[no-redef]
        worker_correction_continuation_context,
    )
    from two_site_quota_resume_worker_evidence import (  # type: ignore[no-redef]
        load_worker_correction_evidence,
    )
    from two_site_quota_resume_worker_one_file_reader import (  # type: ignore[no-redef]
        read_and_bind_worker_one_result,
    )

__all__ = ["worker_one_post_result_context"]

_EXPECTED_BOUND_KEYS = frozenset({
    "worker_info",
    "returned_state",
    "current_worker",
    "seen_convs",
    "seen_norm_findings",
    "seen_test_signatures",
    "seen_bootstrap_signatures",
})

_EXPECTED_SET_KEYS = (
    "seen_convs",
    "seen_norm_findings",
    "seen_test_signatures",
    "seen_bootstrap_signatures",
)


@contextmanager
def worker_one_post_result_context(
    root: Path,
    request: dict,
    returned_state: dict,
) -> Iterator[dict[str, Any]]:
    """Yield detached post-result worker-1 binding context under pipeline lock."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    if type(request) is not dict:
        raise ProposalError(f"request must be dict, got {type(request).__name__}")
    if type(returned_state) is not dict:
        raise ProposalError(
            f"returned_state must be dict, got {type(returned_state).__name__}"
        )

    snapshot = copy.deepcopy(returned_state)

    with worker_correction_continuation_context(root, request) as ctx:
        if type(ctx) is not dict:
            raise ProposalError(f"ctx must be dict, got {type(ctx).__name__}")

        admitted_request = ctx.get("admitted_request")
        if (
            type(admitted_request) is not dict
            or admitted_request.get("role") != "worker"
        ):
            raise ProposalError("invalid admitted_request in context")

        artifacts = ctx.get("artifacts")
        if type(artifacts) is not dict:
            raise ProposalError("invalid artifacts in context")

        worker_index = artifacts.get("worker_index")
        if (
            type(worker_index) is not int
            or type(worker_index) is bool
            or worker_index != 1
        ):
            raise ProposalError(f"worker_index must be 1, got {worker_index!r}")

        reviewer_path = artifacts.get("reviewer_path")
        if not isinstance(reviewer_path, (str, Path)):
            raise ProposalError("artifacts missing reviewer_path")
        status_path = Path(reviewer_path).with_name("status.json")
        prior_status_bytes = bounded_file_bytes(
            root,
            status_path,
            MAX_CONTEXT,
            "prior reviewer status",
        )

        bound = read_and_bind_worker_one_result(root, ctx, snapshot)
        if not isinstance(bound, dict) or set(bound.keys()) != _EXPECTED_BOUND_KEYS:
            raise ProposalError("bound keys mismatch")

        for s_key in _EXPECTED_SET_KEYS:
            if not isinstance(bound[s_key], set):
                raise ProposalError(f"{s_key} must be a set")

        current_worker = bound["current_worker"]
        if not isinstance(current_worker, tuple) or len(current_worker) != 4:
            raise ProposalError("current_worker must be a 4-tuple")

        def state_check() -> None:
            admitted, task_path, _ = validate_quota_continuation_request(
                root, request
            )
            if admitted != ctx["admitted_request"]:
                raise ProposalError("admitted request drifted across recheck")
            if task_path != ctx["admitted_task_path"]:
                raise ProposalError("admitted task path drifted across recheck")

            orig_id = admitted.get("original_request_id")
            if not isinstance(orig_id, str):
                raise ProposalError(
                    "missing original_request_id in admitted request"
                )

            receipt = read_terminal_quota_record(root, orig_id)
            if receipt != ctx["original_terminal_receipt"]:
                raise ProposalError(
                    "original terminal quota record drifted across recheck"
                )

            derived_artifacts = derive_worker_resume_artifacts(admitted, receipt)
            if derived_artifacts != ctx["artifacts"]:
                raise ProposalError(
                    "worker resume artifacts drifted across recheck"
                )

            derived_worker_index = derived_artifacts.get("worker_index")
            if (
                type(derived_worker_index) is not int
                or type(derived_worker_index) is bool
                or derived_worker_index != 1
            ):
                raise ProposalError(
                    f"worker_index must be 1 on recheck, got {derived_worker_index!r}"
                )

            reloaded_evidence = load_worker_correction_evidence(
                root, task_path, derived_artifacts
            )
            if reloaded_evidence != ctx["evidence"]:
                raise ProposalError("correction evidence drifted across recheck")

            reloaded_reviewer_path = derived_artifacts.get("reviewer_path")
            if not isinstance(reloaded_reviewer_path, (str, Path)):
                raise ProposalError("artifacts missing reviewer_path on recheck")
            reloaded_status_path = Path(reloaded_reviewer_path).with_name("status.json")
            reloaded_status_bytes = bounded_file_bytes(
                root,
                reloaded_status_path,
                MAX_CONTEXT,
                "prior reviewer status",
            )
            if reloaded_status_bytes != prior_status_bytes:
                raise ProposalError(
                    "prior reviewer status bytes drifted across recheck"
                )

            check_state(
                root,
                ctx["source_head"],
                ctx["initial_refs"],
                ctx["admitted_task_path"],
                ctx["task"],
                ctx["sources"],
            )

            rechecked_bound = read_and_bind_worker_one_result(
                root, ctx, snapshot
            )
            if rechecked_bound != bound:
                raise ProposalError(
                    "worker-1 result binding drifted across recheck"
                )

        state_check()

        yield {
            "base_context": copy.deepcopy(ctx),
            "worker_info": copy.deepcopy(bound["worker_info"]),
            "returned_state": copy.deepcopy(bound["returned_state"]),
            "current_worker": copy.deepcopy(bound["current_worker"]),
            "seen_convs": copy.deepcopy(bound["seen_convs"]),
            "seen_norm_findings": copy.deepcopy(bound["seen_norm_findings"]),
            "seen_test_signatures": copy.deepcopy(bound["seen_test_signatures"]),
            "seen_bootstrap_signatures": copy.deepcopy(bound["seen_bootstrap_signatures"]),
            "state_check": state_check,
        }
