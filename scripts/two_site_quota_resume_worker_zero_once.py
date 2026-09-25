"""Single execution of quota continuation in worker-0 role under pipeline lock."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError, QuotaBlockError, _extract_bound_quota_evidence,
        check_state, ref_snapshot, validate_run,
    )
    from scripts.two_site_proposal_runner import run as runner_run
    from scripts.two_site_provider_broker import read_terminal_quota_record
    from scripts.two_site_quota_continuation_admission import validate_quota_continuation_request
    from scripts.two_site_quota_resume_worker_artifacts import derive_worker_resume_artifacts
    from scripts.two_site_quota_resume_worker_context import worker_zero_continuation_context
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError, QuotaBlockError, _extract_bound_quota_evidence,
        check_state, ref_snapshot, validate_run,
    )
    from two_site_proposal_runner import run as runner_run  # type: ignore[no-redef]
    from two_site_provider_broker import read_terminal_quota_record  # type: ignore[no-redef]
    from two_site_quota_continuation_admission import validate_quota_continuation_request  # type: ignore[no-redef]
    from two_site_quota_resume_worker_artifacts import derive_worker_resume_artifacts  # type: ignore[no-redef]
    from two_site_quota_resume_worker_context import worker_zero_continuation_context  # type: ignore[no-redef]

__all__ = ["execute_worker_zero_once"]


def _build_result_state(
    root: Path,
    ctx: dict[str, Any],
    run_id: str,
    run_dir: Path,
    worker_res: dict[str, Any],
    result_sha: str,
) -> dict[str, Any]:
    status = worker_res.get("status")
    if status != "PROPOSAL_READY":
        raise ProposalError(f"unexpected worker result status: {status!r}")

    proposal_path = (run_dir / "result.json").relative_to(root).as_posix()
    artifacts = ctx["artifacts"]
    return {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": ctx["task"]["task_id"],
        "source_head": ctx["source_head"],
        "final_worker_run_id": run_id,
        "proposal_sha256": result_sha,
        "changed_paths": [c["path"] for c in worker_res.get("changes", [])],
        "proposal_path": proposal_path,
        "correction_count": 0,
        "semantic_task_fingerprint": artifacts["semantic_task_fingerprint"],
    }


def execute_worker_zero_once(
    root: Path, continuation_request: dict[str, Any], cli: Path | None = None,
) -> dict[str, Any]:
    """Execute worker-0 continuation once under the pipeline lock without retry."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ProposalError(f"root directory does not exist: {root}")

    with worker_zero_continuation_context(root, continuation_request) as ctx:
        run_prefix = ctx["continuation_run_prefix"]
        orig_prefix = ctx["original_run_prefix"]
        attempted_run_id = ctx["artifacts"]["attempted_run_id"]
        run_id = f"{run_prefix}-worker-0"
        if run_id == attempted_run_id or run_id == f"{orig_prefix}-worker-0":
            raise ProposalError(f"continuation run ID {run_id!r} cannot match original run ID")
        if len(run_id) > 80:
            raise ProposalError(f"run_id exceeds 80 characters: {run_id!r}")

        proposals_dir = root / ".local/two-site-refactor/proposals"
        for attempt_id in (run_id, f"{run_id}-retry-1", f"{run_id}-retry-2"):
            if os.path.lexists(proposals_dir / attempt_id):
                raise ProposalError(f"worker run ID already exists: {attempt_id}")

        head = ctx["source_head"]
        task_path = ctx["admitted_task_path"]
        task = ctx["task"]
        sources = ctx["sources"]
        initial_refs = ref_snapshot(root)
        check_state(root, head, initial_refs, task_path, task, sources)

        def pre_provider_check() -> None:
            admitted, t_path, _ = validate_quota_continuation_request(root, continuation_request)
            if admitted != ctx["admitted_request"]:
                raise ProposalError("continuation request drifted across re-admission")
            if t_path != task_path:
                raise ProposalError("admitted task path drifted across re-admission")
            check_state(root, head, initial_refs, task_path, task, sources)
            orig_req_id = admitted.get("original_request_id")
            if not isinstance(orig_req_id, str):
                raise ProposalError("missing original_request_id in admitted request")
            rec = read_terminal_quota_record(root, orig_req_id)
            if rec != ctx["original_terminal_receipt"]:
                raise ProposalError("original terminal receipt drifted in pre_provider_check")
            re_artifacts = derive_worker_resume_artifacts(admitted, rec)
            if re_artifacts.get("worker_index") != 0:
                raise ProposalError(f"worker_index must be 0, got {re_artifacts.get('worker_index')!r}")
            if re_artifacts != ctx["artifacts"]:
                raise ProposalError("worker resume artifacts drifted in pre_provider_check")
            check_state(root, head, initial_refs, task_path, task, sources)

        timeout = int(ctx["admitted_request"]["timeout"])

        try:
            run_dir = runner_run(
                root, task_path, "worker", run_id, timeout,
                proposal=None,
                feedback=None,
                test_evidence=None,
                cli=cli,
                pre_provider_check=pre_provider_check,
            )
        except QuotaBlockError:
            raise
        except ProposalError:
            check_state(root, head, initial_refs, task_path, task, sources)
            extracted = _extract_bound_quota_evidence(
                root, proposals_dir / run_id, run_id, "worker", head
            )
            if extracted is not None:
                quota_ev, quota_att = extracted
                raise QuotaBlockError(quota_ev, quota_att) from None
            raise

        if not isinstance(run_dir, Path):
            raise ProposalError(f"run_dir must be Path, got {type(run_dir).__name__}")
        if run_dir.is_symlink() or os.path.islink(run_dir):
            raise ProposalError("run_dir cannot be a symlink")
        expected_run_dir = (proposals_dir / run_id).resolve()
        if run_dir.resolve() != expected_run_dir or not run_dir.is_dir():
            raise ProposalError(f"run_dir mismatch: {run_dir} != {expected_run_dir}")

        check_state(root, head, initial_refs, task_path, task, sources)

        seen_convs: set[str] = set()
        worker_res, _, result_sha, _ = validate_run(
            root=root,
            run_dir=run_dir,
            role="worker",
            expected_run_id=run_id,
            expected_head=head,
            expected_task=task,
            expected_sources=sources,
            seen_convs=seen_convs,
            prior_worker=None,
        )

        check_state(root, head, initial_refs, task_path, task, sources)
        return _build_result_state(root, ctx, run_id, run_dir, worker_res, result_sha)
