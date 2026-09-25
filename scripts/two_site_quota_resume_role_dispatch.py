"""Single execution quota continuation role dispatch unhooked from Broker/Supervisor.

Note for future integration:
Broker must import this module lazily inside continuation handler functions
to avoid circular import issues, as this module imports `read_terminal_quota_record`
from the broker module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from scripts.two_site_proposal_pipeline import ProposalError
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]

try:
    from scripts.two_site_provider_broker import read_terminal_quota_record
except ImportError:
    from two_site_provider_broker import read_terminal_quota_record  # type: ignore[no-redef]

try:
    from scripts.two_site_quota_continuation_admission import (
        validate_quota_continuation_request,
    )
except ImportError:
    from two_site_quota_continuation_admission import (  # type: ignore[no-redef]
        validate_quota_continuation_request,
    )

try:
    from scripts.two_site_quota_resume_execution import (
        _derive_verifier_resume_artifacts,
    )
except (ImportError, AttributeError):
    try:
        from two_site_quota_resume_execution import (  # type: ignore[no-redef]
            _derive_verifier_resume_artifacts,
        )
    except (ImportError, AttributeError):
        try:
            from scripts.two_site_proposal_pipeline import (  # type: ignore[no-redef]
                _derive_verifier_resume_artifacts,
            )
        except (ImportError, AttributeError):
            from two_site_proposal_pipeline import (  # type: ignore[no-redef]
                _derive_verifier_resume_artifacts,
            )

try:
    from scripts.two_site_quota_resume_worker_artifacts import (
        derive_worker_resume_artifacts,
    )
except ImportError:
    from two_site_quota_resume_worker_artifacts import (  # type: ignore[no-redef]
        derive_worker_resume_artifacts,
    )

try:
    from scripts.two_site_quota_resume_verifier import execute_verifier_once
except ImportError:
    from two_site_quota_resume_verifier import execute_verifier_once  # type: ignore[no-redef]

try:
    from scripts.two_site_quota_resume_worker_zero_once import (
        execute_worker_zero_once,
    )
except ImportError:
    from two_site_quota_resume_worker_zero_once import (  # type: ignore[no-redef]
        execute_worker_zero_once,
    )

try:
    from scripts.two_site_quota_resume_worker_one_once import (
        execute_worker_one_once,
    )
except ImportError:
    from two_site_quota_resume_worker_one_once import (  # type: ignore[no-redef]
        execute_worker_one_once,
    )

try:
    from scripts.two_site_quota_resume_worker_two_once import (
        execute_worker_two_once,
    )
except ImportError:
    from two_site_quota_resume_worker_two_once import (  # type: ignore[no-redef]
        execute_worker_two_once,
    )

__all__ = ["dispatch_quota_continuation_once"]


def dispatch_quota_continuation_once(
    root: Path,
    request: dict[str, Any],
    cli: Path | None = None,
) -> dict[str, Any]:
    """Dispatch a quota continuation request to exactly one verifier or worker executor."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    if not isinstance(request, dict):
        raise ProposalError(f"request must be a dict, got {type(request).__name__}")

    admitted, _task_path, _scope = validate_quota_continuation_request(root, request)
    if not isinstance(admitted, dict):
        raise ProposalError(f"admitted request must be dict, got {type(admitted).__name__}")

    for key in ("role", "original_request_id", "task_id"):
        if key in request and key in admitted and request[key] != admitted[key]:
            raise ProposalError(
                f"raw request {key!r} mismatch with admitted {key!r}: "
                f"{request[key]!r} != {admitted[key]!r}"
            )

    orig_req_id = admitted.get("original_request_id")
    if not isinstance(orig_req_id, str) or not orig_req_id:
        raise ProposalError(
            f"admitted request missing original_request_id: {orig_req_id!r}"
        )

    receipt = read_terminal_quota_record(root, orig_req_id)
    if not isinstance(receipt, dict):
        raise ProposalError(
            f"terminal quota record must be dict, got {type(receipt).__name__}"
        )

    role = admitted.get("role")
    if role == "verifier":
        artifacts = _derive_verifier_resume_artifacts(admitted, receipt)
        if not isinstance(artifacts, dict):
            raise ProposalError(
                f"verifier resume artifacts must be dict, got {type(artifacts).__name__}"
            )
        result = execute_verifier_once(root, admitted, cli)
        if not isinstance(result, dict):
            raise ProposalError(
                f"verifier executor result must be dict, got {type(result).__name__}"
            )
        if "final_reviewer_run_id" not in result or result.get("state") not in (
            "READY_FOR_CODEX",
            "TASK_BLOCKED",
        ):
            raise ProposalError(
                f"mismatched returned route for verifier: {result!r}"
            )
        return result

    if role == "worker":
        artifacts = derive_worker_resume_artifacts(admitted, receipt)
        if not isinstance(artifacts, dict):
            raise ProposalError(
                f"worker resume artifacts must be dict, got {type(artifacts).__name__}"
            )
        worker_index = artifacts.get("worker_index")
        if type(worker_index) is not int or type(worker_index) is bool:
            raise ProposalError(
                f"worker_index must be built-in int, got {type(worker_index).__name__}"
            )

        if worker_index == 0:
            result = execute_worker_zero_once(root, admitted, cli)
        elif worker_index == 1:
            result = execute_worker_one_once(root, admitted, cli)
        elif worker_index == 2:
            result = execute_worker_two_once(root, admitted, cli)
        else:
            raise ProposalError(
                f"worker_index must be 0, 1, or 2, got {worker_index!r}"
            )

        if not isinstance(result, dict):
            raise ProposalError(
                f"worker executor result must be dict, got {type(result).__name__}"
            )
        if (
            "final_reviewer_run_id" in result
            or result.get("state") != "WORKER_PROPOSAL_READY"
            or result.get("correction_count") != worker_index
        ):
            raise ProposalError(
                f"mismatched returned route for worker-{worker_index}: {result!r}"
            )
        return result

    raise ProposalError(f"unsupported admitted role: {role!r}")
