"""Single execution of quota continuation in verifier role under pipeline lock."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError, QuotaBlockError, _extract_bound_quota_evidence,
        _load_verifier_pre_review_receipt, check_state, ref_snapshot, validate_run,
    )
    from scripts.two_site_proposal_runner import run as runner_run
    from scripts.two_site_provider_broker import read_terminal_quota_record
    from scripts.two_site_quota_continuation_admission import validate_quota_continuation_request
    from scripts.two_site_quota_resume_execution import verifier_continuation_context
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError, QuotaBlockError, _extract_bound_quota_evidence,
        _load_verifier_pre_review_receipt, check_state, ref_snapshot, validate_run,
    )
    from two_site_proposal_runner import run as runner_run  # type: ignore[no-redef]
    from two_site_provider_broker import read_terminal_quota_record  # type: ignore[no-redef]
    from two_site_quota_continuation_admission import validate_quota_continuation_request  # type: ignore[no-redef]
    from two_site_quota_resume_execution import verifier_continuation_context  # type: ignore[no-redef]
    from two_site_quota_resume_worker_reader import load_prior_worker_chain  # type: ignore[no-redef]

__all__ = ["execute_verifier_once"]


def _build_result_state(
    root: Path, ctx: dict[str, Any], run_id: str, run_dir: Path, reviewer_res: dict[str, Any],
) -> dict[str, Any]:
    status = reviewer_res.get("status")
    if status == "VERIFIED":
        if reviewer_res.get("findings"):
            raise ProposalError("verified result cannot contain findings")
        state, blockers = "READY_FOR_CODEX", []
    elif status == "CHANGES_REQUIRED":
        state, blockers = "TASK_BLOCKED", ["correction required"]
    else:
        raise ProposalError(f"unexpected reviewer result status: {status!r}")

    worker_result = ctx["validated_worker"]["result"]
    artifacts = ctx["artifacts"]
    return {
        "state": state,
        "task_id": ctx["task"]["task_id"],
        "source_head": ctx["source_head"],
        "final_worker_run_id": ctx["final_worker_run_id"],
        "final_reviewer_run_id": run_id,
        "proposal_sha256": ctx["proposal_sha256"],
        "changed_paths": [c["path"] for c in worker_result.get("changes", [])],
        "proposal_path": ctx["proposal_path"],
        "reviewer_path": (run_dir / "result.json").relative_to(root).as_posix(),
        "correction_count": artifacts["correction_count"],
        "blockers": blockers,
        "pre_review_test_path": ctx["pre_review_test_path"],
        "pre_review_test_sha256": ctx["pre_review_test_sha256"],
        "pre_review_test_state": "PASSED",
        "semantic_task_fingerprint": artifacts["semantic_task_fingerprint"],
    }


def execute_verifier_once(
    root: Path, continuation_request: dict[str, Any], cli: Path | None = None,
) -> dict[str, Any]:
    """Execute verifier continuation once under the pipeline lock."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ProposalError(f"root directory does not exist: {root}")

    with verifier_continuation_context(root, continuation_request) as ctx:
        run_prefix = ctx["continuation_run_prefix"]
        orig_prefix = ctx["original_run_prefix"]
        correction_count = ctx["artifacts"]["correction_count"]
        run_id = f"{run_prefix}-reviewer-{correction_count}"
        if run_id == f"{orig_prefix}-reviewer-{correction_count}":
            raise ProposalError(f"continuation run ID {run_id!r} cannot match original run ID")
        if len(run_id) > 80:
            raise ProposalError(f"run_id exceeds 80 characters: {run_id!r}")

        proposals_dir = root / ".local/two-site-refactor/proposals"
        for attempt_id in (run_id, f"{run_id}-retry-1", f"{run_id}-retry-2"):
            if os.path.lexists(proposals_dir / attempt_id):
                raise ProposalError(f"reviewer run ID already exists: {attempt_id}")

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
            trec = _load_verifier_pre_review_receipt(root, ctx["artifacts"])
            if trec != ctx["validated_test_receipt"]:
                raise ProposalError("pre-review test receipt drifted in pre_provider_check")
            wchain = load_prior_worker_chain(root, task_path, ctx["artifacts"])
            if wchain != ctx["validated_worker"]:
                raise ProposalError("prior worker chain drifted in pre_provider_check")

        proposal_file = root / ctx["proposal_path"]
        test_evidence_file = root / ctx["pre_review_test_path"]
        timeout = int(ctx["admitted_request"]["timeout"])

        try:
            run_dir = runner_run(
                root, task_path, "verifier", run_id, timeout,
                proposal=proposal_file, cli=cli, test_evidence=test_evidence_file,
                pre_provider_check=pre_provider_check,
            )
        except QuotaBlockError:
            raise
        except ProposalError:
            check_state(root, head, initial_refs, task_path, task, sources)
            extracted = _extract_bound_quota_evidence(
                root, proposals_dir / run_id, run_id, "verifier", head
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

        worker_info = ctx["validated_worker"]
        worker_conv = worker_info.get("conversation_id")
        prior_worker = (
            ctx["final_worker_run_id"], ctx["proposal_sha256"],
            worker_info["result_text"], worker_conv,
        )
        seen_convs = {worker_conv} if isinstance(worker_conv, str) and worker_conv else set()

        reviewer_res, _, _, _ = validate_run(
            root=root, run_dir=run_dir, role="verifier", expected_run_id=run_id,
            expected_head=head, expected_task=task, expected_sources=sources,
            seen_convs=seen_convs, prior_worker=prior_worker,
            expected_test_receipt=ctx["validated_test_receipt"],
            expected_test_evidence_sha=ctx["pre_review_test_sha256"],
        )

        check_state(root, head, initial_refs, task_path, task, sources)
        return _build_result_state(root, ctx, run_id, run_dir, reviewer_res)
