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


def _validate_origin_and_reviewer_status(
    root: Path,
    artifacts: dict[str, Any],
    evidence: dict[str, Any],
    ctx: dict[str, Any] | None = None,
    *,
    expected_origin: str | None = None,
    recheck: bool = False,
) -> tuple[str, bytes | None]:
    correction_origin = artifacts.get("correction_origin")
    if (
        "correction_origin" in evidence
        and evidence.get("correction_origin") != correction_origin
    ):
        raise ProposalError("mixed correction_origin between artifacts and evidence")
    if (
        ctx is not None
        and "correction_origin" in ctx
        and ctx.get("correction_origin") != correction_origin
    ):
        raise ProposalError("mixed correction_origin between artifacts and ctx")

    if correction_origin is None:
        origin = "REVIEWER"
    elif correction_origin in ("REVIEWER", "BOOTSTRAP", "TEST_FAILED"):
        origin = correction_origin
    else:
        raise ProposalError(f"unknown correction_origin: {correction_origin!r}")

    if expected_origin is not None and origin != expected_origin:
        raise ProposalError("correction origin drifted across recheck")

    if origin == "REVIEWER":
        reviewer_path = artifacts.get("reviewer_path")
        if not isinstance(reviewer_path, (str, Path)):
            suffix = " on recheck" if recheck else ""
            raise ProposalError(f"artifacts missing reviewer_path{suffix}")
        status_path = Path(reviewer_path).with_name("status.json")
        status_bytes = bounded_file_bytes(
            root,
            status_path,
            MAX_CONTEXT,
            "prior reviewer status",
        )
        return origin, status_bytes

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
    if (
        evidence.get("reviewer_sha256") is not None
        or evidence.get("reviewer_result_sha") is not None
    ):
        raise ProposalError(f"evidence reviewer sha must be None for {origin}")

    return origin, None


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

        evidence = ctx.get("evidence")
        if type(evidence) is not dict:
            raise ProposalError("invalid evidence in context")

        origin, prior_status_bytes = _validate_origin_and_reviewer_status(
            root,
            artifacts,
            evidence,
            ctx,
            recheck=False,
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

            _, reloaded_status_bytes = _validate_origin_and_reviewer_status(
                root,
                derived_artifacts,
                reloaded_evidence,
                ctx,
                expected_origin=origin,
                recheck=True,
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
