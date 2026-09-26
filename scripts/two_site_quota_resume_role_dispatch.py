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
    from scripts.two_site_quota_resume_post_worker_zero import (
        _EXPECTED_RESULT_STATE_KEYS,
    )
except ImportError:
    from two_site_quota_resume_post_worker_zero import (  # type: ignore[no-redef]
        _EXPECTED_RESULT_STATE_KEYS,
    )

try:
    from scripts.two_site_quota_resume_post_worker_zero_remainder import (
        execute_worker_zero_post_result_remainder,
    )
except ImportError:
    from two_site_quota_resume_post_worker_zero_remainder import (  # type: ignore[no-redef]
        execute_worker_zero_post_result_remainder,
    )

try:
    from scripts.two_site_quota_resume_post_worker_one_remainder import (
        execute_worker_one_post_result_remainder,
    )
except ImportError:
    from two_site_quota_resume_post_worker_one_remainder import (  # type: ignore[no-redef]
        execute_worker_one_post_result_remainder,
    )

try:
    from scripts.two_site_quota_resume_post_worker_two_remainder import (
        execute_worker_two_post_result_remainder,
    )
except ImportError:
    from two_site_quota_resume_post_worker_two_remainder import (  # type: ignore[no-redef]
        execute_worker_two_post_result_remainder,
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
            intermediate = execute_worker_zero_once(root, admitted, cli)
            if type(intermediate) is not dict:
                raise ProposalError(
                    f"worker executor result must be dict, got {type(intermediate).__name__}"
                )
            if set(intermediate.keys()) != _EXPECTED_RESULT_STATE_KEYS:
                raise ProposalError("intermediate worker-0 keys do not match expected keys")
            if intermediate["state"] != "WORKER_PROPOSAL_READY":
                raise ProposalError(
                    f"mismatched returned route for worker-0: {intermediate!r}"
                )
            corr = intermediate["correction_count"]
            if type(corr) is not int or type(corr) is bool or corr != 0:
                raise ProposalError(
                    f"intermediate correction_count must be exact int 0, got {corr!r}"
                )
            for field in ("task_id", "source_head", "semantic_task_fingerprint"):
                try:
                    if (
                        intermediate[field] != admitted[field]
                        or intermediate[field] != artifacts[field]
                    ):
                        raise ProposalError(
                            f"intermediate {field} mismatch with admitted/artifacts"
                        )
                except KeyError as exc:
                    raise ProposalError(f"missing required lineage field {exc}") from exc

            result = execute_worker_zero_post_result_remainder(
                root, admitted, intermediate, cli
            )
            if type(result) is not dict:
                raise ProposalError(
                    f"worker remainder result must be dict, got {type(result).__name__}"
                )
            if result.get("state") != "READY_FOR_CODEX":
                raise ProposalError(
                    f"final state must be READY_FOR_CODEX, got {result.get('state')!r}"
                )
            reviewer_run_id = result.get("final_reviewer_run_id")
            if not isinstance(reviewer_run_id, str) or not reviewer_run_id.strip():
                raise ProposalError(
                    f"final_reviewer_run_id must be non-empty str, got {reviewer_run_id!r}"
                )
            for field in ("task_id", "source_head", "semantic_task_fingerprint"):
                try:
                    if (
                        result[field] != admitted[field]
                        or result[field] != artifacts[field]
                        or result[field] != intermediate[field]
                    ):
                        raise ProposalError(
                            f"final {field} mismatch with admitted/artifacts/intermediate"
                        )
                except KeyError as exc:
                    raise ProposalError(f"missing required final lineage field {exc}") from exc
            final_corr = result.get("correction_count")
            if (
                type(final_corr) is not int
                or type(final_corr) is bool
                or final_corr not in (0, 1, 2)
            ):
                raise ProposalError(
                    f"final correction_count must be exact int in 0..2, got {final_corr!r}"
                )
            if final_corr == 0:
                for field in (
                    "final_worker_run_id",
                    "proposal_sha256",
                    "changed_paths",
                    "proposal_path",
                ):
                    try:
                        if result[field] != intermediate[field]:
                            raise ProposalError(
                                f"uncorrected {field} mismatch: {field}"
                            )
                    except KeyError as exc:
                        raise ProposalError(f"missing uncorrected field {exc}") from exc
            return result

        if worker_index == 1:
            intermediate = execute_worker_one_once(root, admitted, cli)
            if type(intermediate) is not dict:
                raise ProposalError(
                    f"worker executor result must be dict, got {type(intermediate).__name__}"
                )
            if set(intermediate.keys()) != _EXPECTED_RESULT_STATE_KEYS:
                raise ProposalError("intermediate worker-1 keys do not match expected keys")
            if intermediate["state"] != "WORKER_PROPOSAL_READY":
                raise ProposalError(
                    f"mismatched returned route for worker-1: {intermediate!r}"
                )
            corr = intermediate["correction_count"]
            if type(corr) is not int or type(corr) is bool or corr != 1:
                raise ProposalError(
                    f"intermediate correction_count must be exact int 1, got {corr!r}"
                )
            for field in ("task_id", "source_head", "semantic_task_fingerprint"):
                try:
                    if (
                        intermediate[field] != admitted[field]
                        or intermediate[field] != artifacts[field]
                    ):
                        raise ProposalError(
                            f"intermediate {field} mismatch with admitted/artifacts"
                        )
                except KeyError as exc:
                    raise ProposalError(f"missing required lineage field {exc}") from exc

            result = execute_worker_one_post_result_remainder(
                root, admitted, intermediate, cli
            )
            if type(result) is not dict:
                raise ProposalError(
                    f"worker remainder result must be dict, got {type(result).__name__}"
                )
            if result.get("state") != "READY_FOR_CODEX":
                raise ProposalError(
                    f"final state must be READY_FOR_CODEX, got {result.get('state')!r}"
                )
            reviewer_run_id = result.get("final_reviewer_run_id")
            if not isinstance(reviewer_run_id, str) or not reviewer_run_id.strip():
                raise ProposalError(
                    f"final_reviewer_run_id must be non-empty str, got {reviewer_run_id!r}"
                )
            for field in ("task_id", "source_head", "semantic_task_fingerprint"):
                try:
                    if (
                        result[field] != admitted[field]
                        or result[field] != artifacts[field]
                        or result[field] != intermediate[field]
                    ):
                        raise ProposalError(
                            f"final {field} mismatch with admitted/artifacts/intermediate"
                        )
                except KeyError as exc:
                    raise ProposalError(f"missing required final lineage field {exc}") from exc
            final_corr = result.get("correction_count")
            if (
                type(final_corr) is not int
                or type(final_corr) is bool
                or final_corr not in (1, 2)
            ):
                raise ProposalError(
                    f"final correction_count must be exact int in (1, 2), got {final_corr!r}"
                )
            if final_corr == 1:
                for field in (
                    "final_worker_run_id",
                    "proposal_sha256",
                    "changed_paths",
                    "proposal_path",
                ):
                    try:
                        if result[field] != intermediate[field]:
                            raise ProposalError(
                                f"uncorrected {field} mismatch: {field}"
                            )
                    except KeyError as exc:
                        raise ProposalError(f"missing uncorrected field {exc}") from exc
            return result

        if worker_index == 2:
            intermediate = execute_worker_two_once(root, admitted, cli)
            if type(intermediate) is not dict:
                raise ProposalError(
                    f"worker executor result must be dict, got {type(intermediate).__name__}"
                )
            if set(intermediate.keys()) != _EXPECTED_RESULT_STATE_KEYS:
                raise ProposalError("intermediate worker-2 keys do not match expected keys")
            if intermediate["state"] != "WORKER_PROPOSAL_READY":
                raise ProposalError(
                    f"mismatched returned route for worker-2: {intermediate!r}"
                )
            corr = intermediate["correction_count"]
            if type(corr) is not int or type(corr) is bool or corr != 2:
                raise ProposalError(
                    f"intermediate correction_count must be exact int 2, got {corr!r}"
                )
            for field in ("task_id", "source_head", "semantic_task_fingerprint"):
                try:
                    if (
                        intermediate[field] != admitted[field]
                        or intermediate[field] != artifacts[field]
                    ):
                        raise ProposalError(
                            f"intermediate {field} mismatch with admitted/artifacts"
                        )
                except KeyError as exc:
                    raise ProposalError(f"missing required lineage field {exc}") from exc

            result = execute_worker_two_post_result_remainder(
                root, admitted, intermediate, cli
            )
            if type(result) is not dict:
                raise ProposalError(
                    f"worker remainder result must be dict, got {type(result).__name__}"
                )
            if result.get("state") != "READY_FOR_CODEX":
                raise ProposalError(
                    f"final state must be READY_FOR_CODEX, got {result.get('state')!r}"
                )
            reviewer_run_id = result.get("final_reviewer_run_id")
            if not isinstance(reviewer_run_id, str) or not reviewer_run_id.strip():
                raise ProposalError(
                    f"final_reviewer_run_id must be non-empty str, got {reviewer_run_id!r}"
                )
            for field in ("task_id", "source_head", "semantic_task_fingerprint"):
                try:
                    if (
                        result[field] != admitted[field]
                        or result[field] != artifacts[field]
                        or result[field] != intermediate[field]
                    ):
                        raise ProposalError(
                            f"final {field} mismatch with admitted/artifacts/intermediate"
                        )
                except KeyError as exc:
                    raise ProposalError(f"missing required final lineage field {exc}") from exc
            final_corr = result.get("correction_count")
            if (
                type(final_corr) is not int
                or type(final_corr) is bool
                or final_corr != 2
            ):
                raise ProposalError(
                    f"final correction_count must be exact int 2, got {final_corr!r}"
                )
            for field in (
                "final_worker_run_id",
                "proposal_sha256",
                "changed_paths",
                "proposal_path",
            ):
                try:
                    if result[field] != intermediate[field]:
                        raise ProposalError(
                            f"uncorrected {field} mismatch: {field}"
                        )
                except KeyError as exc:
                    raise ProposalError(f"missing uncorrected field {exc}") from exc
            return result

        raise ProposalError(
            f"worker_index must be 0, 1, or 2, got {worker_index!r}"
        )

    raise ProposalError(f"unsupported admitted role: {role!r}")
