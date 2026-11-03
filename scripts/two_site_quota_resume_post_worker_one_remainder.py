from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_proposal_pipeline import (
        RUN_PREFIX_RE,
        ProposalError,
        _run_post_worker_remainder,
        trusted_qoder_final_review_hook,
        _branch_root_failure_factor,
    )
    from scripts.two_site_quota_resume_post_worker_one import (
        worker_one_post_result_context,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        RUN_PREFIX_RE,
        ProposalError,
        _run_post_worker_remainder,
        trusted_qoder_final_review_hook,
        _branch_root_failure_factor,
    )
    from two_site_quota_resume_post_worker_one import (  # type: ignore[no-redef]
        worker_one_post_result_context,
    )

__all__ = ["execute_worker_one_post_result_remainder"]


def execute_worker_one_post_result_remainder(
    root: Path,
    continuation_request: dict,
    returned_state: dict,
    cli: Path | None = None,
) -> dict:
    """Execute post-worker-1 remainder under pipeline lock with bound context."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"project root does not exist: {exc}") from exc
    if not root.is_dir() or resolved_root != root:
        raise ProposalError("root must be a resolved canonical directory")
    if root.is_symlink() or os.path.islink(root):
        raise ProposalError("root cannot be a symlink")

    if cli is not None:
        if not isinstance(cli, Path):
            raise ProposalError(f"cli must be Path or None, got {type(cli).__name__}")
        if not cli.is_file() or cli.is_symlink() or os.path.islink(cli):
            raise ProposalError(f"cli must be an existing regular non-symlink file: {cli}")

    with worker_one_post_result_context(
        root, continuation_request, returned_state
    ) as post_ctx:
        del continuation_request, returned_state

        bound_returned_state = post_ctx["returned_state"]
        base_context = post_ctx["base_context"]
        worker_info = post_ctx["worker_info"]
        current_worker = post_ctx["current_worker"]
        seen_convs = post_ctx["seen_convs"]
        seen_norm_findings = post_ctx["seen_norm_findings"]
        seen_test_signatures = post_ctx["seen_test_signatures"]
        seen_bootstrap_signatures = post_ctx["seen_bootstrap_signatures"]
        state_check = post_ctx["state_check"]

        admitted_task_path = base_context.get("admitted_task_path")
        if not isinstance(admitted_task_path, Path):
            raise ProposalError(
                f"admitted task path must be Path, got {type(admitted_task_path).__name__}"
            )
        if not admitted_task_path.is_absolute():
            raise ProposalError(
                f"admitted task path must be absolute: {admitted_task_path}"
            )
        if not admitted_task_path.is_relative_to(root):
            raise ProposalError(
                f"admitted task path must be inside root: {admitted_task_path}"
            )
        if any(part in (".", "..") for part in admitted_task_path.parts):
            raise ProposalError(
                f"admitted task path must not contain '.' or '..': {admitted_task_path}"
            )
        try:
            resolved_task_path = admitted_task_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProposalError(f"admitted task path does not exist: {exc}") from exc
        if resolved_task_path != admitted_task_path:
            raise ProposalError(
                f"admitted task path is not canonical: {admitted_task_path}"
            )
        if (
            not admitted_task_path.is_file()
            or admitted_task_path.is_symlink()
            or os.path.islink(admitted_task_path)
        ):
            raise ProposalError(
                f"admitted task path must be a regular non-symlink file: {admitted_task_path}"
            )

        bound_run_id = bound_returned_state["final_worker_run_id"]
        expected_proposal_path = (
            f".local/two-site-refactor/proposals/{bound_run_id}/result.json"
        )
        bound_proposal_path = bound_returned_state["proposal_path"]
        if bound_proposal_path != expected_proposal_path:
            raise ProposalError(
                f"proposal_path mismatch: expected {expected_proposal_path!r}, "
                f"got {bound_proposal_path!r}"
            )

        proposal_file = root / bound_proposal_path
        worker_dir = proposal_file.parent
        try:
            resolved_worker_dir = worker_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProposalError(f"worker_dir does not exist: {exc}") from exc
        if not worker_dir.is_dir() or resolved_worker_dir != worker_dir:
            raise ProposalError("worker_dir must be an existing canonical directory")
        if worker_dir.is_symlink() or os.path.islink(worker_dir):
            raise ProposalError("worker_dir cannot be a symlink")
        if not worker_dir.is_relative_to(root) or worker_dir == root:
            raise ProposalError("worker_dir must be inside root")
        if (
            not proposal_file.is_file()
            or proposal_file.is_symlink()
            or os.path.islink(proposal_file)
        ):
            raise ProposalError(
                f"proposal file must be an existing regular non-symlink file: {proposal_file}"
            )

        admitted_request = base_context["admitted_request"]
        if "timeout" not in admitted_request:
            raise ProposalError("missing timeout in admitted_request")
        timeout = admitted_request["timeout"]
        if type(timeout) is not int or isinstance(timeout, bool):
            raise ProposalError(
                f"timeout must be exact built-in int, got {type(timeout).__name__}"
            )
        if not (10 <= timeout <= 900):
            raise ProposalError(
                f"timeout must be between 10 and 900, got {timeout!r}"
            )

        run_prefix = base_context["continuation_run_prefix"]
        if not isinstance(run_prefix, str) or not RUN_PREFIX_RE.fullmatch(run_prefix):
            raise ProposalError(f"invalid run_prefix in base_context: {run_prefix!r}")

        state_check()

        artifacts = base_context.get("artifacts")
        evidence = base_context.get("evidence")
        if type(artifacts) is not dict or type(evidence) is not dict:
            raise ProposalError("missing or invalid bound correction artifacts/evidence")
        origin = artifacts.get("correction_origin")
        if origin is None:
            origin = "REVIEWER"
        if origin not in ("REVIEWER", "BOOTSTRAP", "TEST_FAILED"):
            raise ProposalError(f"unknown bound correction origin: {origin!r}")
        seen_branch_factors: set[str] = set()
        if origin == "TEST_FAILED":
            if "receipt" not in evidence:
                raise ProposalError("missing bound receipt for TEST_FAILED origin")
            factor = _branch_root_failure_factor(evidence["receipt"])
            if factor is not None:
                seen_branch_factors.add(factor)

        pipeline_dir = root / ".local/two-site-refactor/pipeline"
        try:
            resolved_pipeline_dir = pipeline_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProposalError(
                f"pipeline parent directory does not exist: {exc}"
            ) from exc
        if not pipeline_dir.is_dir() or resolved_pipeline_dir != pipeline_dir:
            raise ProposalError(
                "pipeline parent directory must be an existing canonical directory"
            )
        if pipeline_dir.is_symlink() or os.path.islink(pipeline_dir):
            raise ProposalError("pipeline parent directory cannot be a symlink")
        if not pipeline_dir.is_relative_to(root) or pipeline_dir == root:
            raise ProposalError("pipeline parent directory must be inside root")

        evidence_dir = pipeline_dir / run_prefix
        if os.path.lexists(evidence_dir):
            raise ProposalError(
                f"pipeline evidence directory already exists: {evidence_dir}"
            )
        evidence_dir.mkdir(mode=0o700)

        state: dict[str, Any] = {
            "state": bound_returned_state["state"],
            "task_id": bound_returned_state["task_id"],
            "source_head": bound_returned_state["source_head"],
            "final_worker_run_id": bound_returned_state["final_worker_run_id"],
            "final_reviewer_run_id": None,
            "proposal_sha256": bound_returned_state["proposal_sha256"],
            "changed_paths": list(bound_returned_state["changed_paths"]),
            "proposal_path": bound_returned_state["proposal_path"],
            "reviewer_path": None,
            "correction_count": bound_returned_state["correction_count"],
            "blockers": [],
            "pre_review_test_path": None,
            "pre_review_test_sha256": None,
            "pre_review_test_state": None,
            "semantic_task_fingerprint": bound_returned_state["semantic_task_fingerprint"],
        }

        _run_post_worker_remainder(
            root=root,
            task_candidate=admitted_task_path,
            task=base_context["task"],
            sources=base_context["sources"],
            initial_head=base_context["source_head"],
            initial_refs=base_context["initial_refs"],
            run_prefix=run_prefix,
            timeout=timeout,
            cli=cli,
            state=state,
            evidence_dir=evidence_dir,
            state_check=state_check,
            worker_dir=worker_dir,
            worker_res=worker_info["result"],
            current_worker=current_worker,
            seen_convs=seen_convs,
            correction_count=1,
            seen_norm_findings=seen_norm_findings,
            seen_test_signatures=seen_test_signatures,
            seen_bootstrap_signatures=seen_bootstrap_signatures,
            seen_branch_factors=seen_branch_factors,
            **({"final_review_hook": trusted_qoder_final_review_hook()}
               if os.environ.get("TWO_SITE_QODER_FINAL_REVIEW_REQUIRED") == "1" else {}),
        )

        state_check()

        if state.get("state") != "READY_FOR_CODEX":
            raise ProposalError(
                f"remainder did not complete READY_FOR_CODEX, got {state.get('state')!r}"
            )

        reviewer_run_id = state.get("final_reviewer_run_id")
        if not isinstance(reviewer_run_id, str) or not reviewer_run_id.strip():
            raise ProposalError(
                f"final_reviewer_run_id must be non-empty str, got {reviewer_run_id!r}"
            )

        return state
