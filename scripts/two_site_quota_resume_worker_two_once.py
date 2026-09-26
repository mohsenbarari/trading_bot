"""Single execution of quota continuation in worker-2 role under pipeline lock."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        QuotaBlockError,
        _extract_bound_quota_evidence,
        check_state,
        ref_snapshot,
        validate_run,
    )
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        bounded_file_bytes,
        run as runner_run,
    )
    from scripts.two_site_provider_broker import read_terminal_quota_record
    from scripts.two_site_quota_continuation_admission import (
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
    from scripts.two_site_quota_resume_worker_two_history_complete import (
        complete_worker_two_history,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        QuotaBlockError,
        _extract_bound_quota_evidence,
        check_state,
        ref_snapshot,
        validate_run,
    )
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        bounded_file_bytes,
        run as runner_run,
    )
    from two_site_provider_broker import read_terminal_quota_record  # type: ignore[no-redef]
    from two_site_quota_continuation_admission import (  # type: ignore[no-redef]
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
    from two_site_quota_resume_worker_two_history_complete import (  # type: ignore[no-redef]
        complete_worker_two_history,
    )

__all__ = ["execute_worker_two_once"]


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
        "correction_count": 2,
        "semantic_task_fingerprint": artifacts["semantic_task_fingerprint"],
    }


def execute_worker_two_once(
    root: Path,
    request: dict[str, Any],
    cli: Path | None = None,
) -> dict[str, Any]:
    """Execute worker-2 continuation once under the pipeline lock without retry."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ProposalError(f"root directory does not exist: {root}")

    with worker_correction_continuation_context(root, request) as ctx:
        artifacts = ctx["artifacts"]
        worker_index = artifacts.get("worker_index")
        if (
            type(worker_index) is not int
            or type(worker_index) is bool
            or worker_index != 2
        ):
            raise ProposalError(f"worker_index must be 2, got {worker_index!r}")

        run_prefix = ctx["continuation_run_prefix"]
        orig_prefix = ctx["original_run_prefix"]
        attempted_run_id = artifacts.get("attempted_run_id")
        run_id = f"{run_prefix}-worker-2"
        if run_id == attempted_run_id or run_id == f"{orig_prefix}-worker-2":
            raise ProposalError(f"continuation run ID {run_id!r} cannot match original run ID")
        if len(run_id) > 80:
            raise ProposalError(f"run_id exceeds 80 characters: {run_id!r}")

        proposals_dir = root / ".local/two-site-refactor/proposals"
        for attempt_id in (run_id, f"{run_id}-retry-1", f"{run_id}-retry-2"):
            if os.path.lexists(proposals_dir / attempt_id):
                raise ProposalError(f"worker run ID already exists: {attempt_id}")

        evidence = ctx["evidence"]
        if not isinstance(evidence, dict):
            raise ProposalError("missing or invalid evidence in context")
        prior_worker_info = evidence.get("worker_info")
        if not isinstance(prior_worker_info, dict):
            raise ProposalError("missing or invalid worker_info in evidence")
        expected_worker_info_keys = {"result", "status", "result_sha", "result_text", "conversation_id"}
        if set(prior_worker_info.keys()) != expected_worker_info_keys:
            raise ProposalError(
                f"worker_info schema keys mismatch: expected {expected_worker_info_keys!r}, got {set(prior_worker_info.keys())!r}"
            )

        prior_result = prior_worker_info["result"]
        if not isinstance(prior_result, dict):
            raise ProposalError("prior worker_info['result'] must be a dict")
        if "changes" not in prior_result or not isinstance(prior_result["changes"], list):
            raise ProposalError("prior worker_info['result']['changes'] must be a list")
        prior_changes = prior_result["changes"]

        prior_result_sha = prior_worker_info["result_sha"]
        if not isinstance(prior_result_sha, str):
            raise ProposalError("prior worker_info['result_sha'] must be a str")

        prior_result_text = prior_worker_info["result_text"]
        if not isinstance(prior_result_text, str):
            raise ProposalError("prior worker_info['result_text'] must be a str")

        prior_worker_conv = prior_worker_info["conversation_id"]
        if not isinstance(prior_worker_conv, str) or not prior_worker_conv.strip():
            raise ProposalError("prior worker_info['conversation_id'] missing or empty")

        prior_run_id = artifacts.get("final_worker_run_id")
        if not isinstance(prior_run_id, str) or not prior_run_id.strip():
            raise ProposalError("artifacts missing or invalid final_worker_run_id")

        prior_sha = artifacts.get("proposal_sha256")
        if not isinstance(prior_sha, str) or not prior_sha.strip():
            raise ProposalError("artifacts missing or invalid proposal_sha256")

        if prior_result_sha != prior_sha:
            raise ProposalError(
                f"prior result_sha mismatch: worker_info {prior_result_sha!r} != artifacts {prior_sha!r}"
            )

        raw_origin = artifacts.get("correction_origin")
        if "correction_origin" in evidence and evidence.get("correction_origin") != raw_origin:
            raise ProposalError(
                f"correction_origin mismatch: artifacts {raw_origin!r} != evidence {evidence.get('correction_origin')!r}"
            )
        if raw_origin is None or raw_origin == "REVIEWER":
            origin = "REVIEWER"
        elif raw_origin in ("BOOTSTRAP", "TEST_FAILED"):
            origin = raw_origin
        else:
            raise ProposalError(f"unknown correction_origin: {raw_origin!r}")

        if origin == "REVIEWER":
            reviewer_status = evidence.get("reviewer_status")
            if not isinstance(reviewer_status, dict):
                raise ProposalError("missing or invalid reviewer_status in evidence")
            reviewer_conv = reviewer_status.get("conversation_id")
            if not isinstance(reviewer_conv, str) or not reviewer_conv.strip():
                raise ProposalError("reviewer conversation_id missing or empty")
            if reviewer_conv == prior_worker_conv:
                raise ProposalError("reviewer conversation_id must differ from prior worker conversation_id")

            reviewer_path = artifacts.get("reviewer_path")
            if not isinstance(reviewer_path, (str, Path)):
                raise ProposalError("artifacts missing reviewer_path")
            reviewer_status_path: Path | None = Path(reviewer_path).with_name("status.json")
            initial_reviewer_status_bytes: bytes | None = bounded_file_bytes(
                root,
                reviewer_status_path,
                MAX_CONTEXT,
                "prior reviewer status",
            )
        else:
            if artifacts.get("reviewer_path") is not None:
                raise ProposalError(
                    f"artifacts reviewer_path must be None for {origin}, got {artifacts.get('reviewer_path')!r}"
                )
            if artifacts.get("final_reviewer_run_id") is not None:
                raise ProposalError(
                    f"artifacts final_reviewer_run_id must be None for {origin}, got {artifacts.get('final_reviewer_run_id')!r}"
                )
            for key in (
                "reviewer_status",
                "reviewer_result",
                "reviewer_sha",
                "reviewer_sha256",
                "reviewer_result_sha",
            ):
                if evidence.get(key) is not None:
                    raise ProposalError(f"evidence {key} must be None for {origin}, got {evidence.get(key)!r}")
            reviewer_status_path = None
            initial_reviewer_status_bytes = None

        head = ctx["source_head"]
        task_path = ctx["admitted_task_path"]
        task = ctx["task"]
        sources = ctx["sources"]
        initial_refs = ref_snapshot(root)
        check_state(root, head, initial_refs, task_path, task, sources)

        history = complete_worker_two_history(root, task_path, artifacts, evidence)
        if not isinstance(history, dict) or history.get("complete") is not True:
            raise ProposalError("worker-2 history must be complete")

        def pre_provider_check() -> None:
            admitted, t_path, _ = validate_quota_continuation_request(root, request)
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
            if re_artifacts.get("worker_index") != 2:
                raise ProposalError(f"worker_index must be 2, got {re_artifacts.get('worker_index')!r}")
            if re_artifacts != ctx["artifacts"]:
                raise ProposalError("worker resume artifacts drifted in pre_provider_check")
            re_evidence = load_worker_correction_evidence(root, t_path, re_artifacts)
            if re_evidence != ctx["evidence"]:
                raise ProposalError("worker correction evidence drifted in pre_provider_check")
            if origin == "REVIEWER":
                if reviewer_status_path is None:
                    raise ProposalError("missing reviewer_status_path in pre_provider_check")
                curr_status_bytes = bounded_file_bytes(
                    root,
                    reviewer_status_path,
                    MAX_CONTEXT,
                    "prior reviewer status",
                )
                if curr_status_bytes != initial_reviewer_status_bytes:
                    raise ProposalError("prior reviewer status bytes drifted in pre_provider_check")
            re_history = complete_worker_two_history(root, t_path, re_artifacts, re_evidence)
            if not isinstance(re_history, dict) or re_history.get("complete") is not True:
                raise ProposalError("worker-2 history must be complete in pre_provider_check")
            if re_history != history:
                raise ProposalError("worker-2 history drifted in pre_provider_check")
            check_state(root, head, initial_refs, task_path, task, sources)

        proposal_arg = root / artifacts["proposal_path"]
        feedback_arg = root / evidence["feedback_path"]
        timeout = int(ctx["admitted_request"]["timeout"])

        try:
            run_dir = runner_run(
                root,
                task_path,
                "worker",
                run_id,
                timeout,
                proposal=proposal_arg,
                feedback=feedback_arg,
                test_evidence=None,
                cli=cli,
                pre_provider_check=pre_provider_check,
            )
        except QuotaBlockError:
            raise
        except ProposalError:
            check_state(root, head, initial_refs, task_path, task, sources)
            extracted = _extract_bound_quota_evidence(
                root, proposals_dir / run_id, run_id, "worker", head,
                expected_task_id=task["task_id"],
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

        ordered_convs = history.get("ordered_conversation_ids")
        if not isinstance(ordered_convs, list) or not ordered_convs:
            raise ProposalError("history missing or empty ordered_conversation_ids")
        seen_convs: set[str] = set(ordered_convs)
        prior_worker_tuple = (prior_run_id, prior_sha, prior_result_text, prior_worker_conv)
        expected_feedback_sha = evidence["feedback_sha"]

        worker_res, _, result_sha, _ = validate_run(
            root=root,
            run_dir=run_dir,
            role="worker",
            expected_run_id=run_id,
            expected_head=head,
            expected_task=task,
            expected_sources=sources,
            seen_convs=seen_convs,
            prior_worker=prior_worker_tuple,
            expected_feedback_sha=expected_feedback_sha,
        )

        if result_sha == prior_sha:
            raise ProposalError(f"correction proposal SHA must differ from prior in {run_id}")
        if worker_res.get("changes") == prior_changes:
            raise ProposalError(f"correction proposal changes must differ from prior in {run_id}")

        check_state(root, head, initial_refs, task_path, task, sources)
        return _build_result_state(root, ctx, run_id, run_dir, worker_res, result_sha)
