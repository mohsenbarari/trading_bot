from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        MAX_CONTEXT,
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        load_task,
        validate_run,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
        load_task,
        validate_run,
    )

try:
    from scripts.two_site_proposal_pipeline import unique_json
except ImportError:
    try:
        from two_site_proposal_pipeline import unique_json  # type: ignore[no-redef]
    except ImportError:
        try:
            from scripts.two_site_proposal_runner import unique_json  # type: ignore[no-redef]
        except ImportError:
            from two_site_proposal_runner import unique_json  # type: ignore[no-redef]

try:
    from scripts.two_site_proposal_runner import (
        bounded_file_bytes,
        validate_correction_feedback,
    )
except ImportError:
    try:
        from two_site_proposal_runner import (  # type: ignore[no-redef]
            bounded_file_bytes,
            validate_correction_feedback,
        )
    except ImportError:
        from scripts.two_site_proposal_pipeline import (  # type: ignore[no-redef]
            bounded_file_bytes,
            validate_correction_feedback,
        )

HEAD_RE = re.compile(r"^[0-9a-f]{40}$")


def load_prior_worker_zero(
    root: Path | str,
    admitted_task_path: Path | str,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(root, (str, Path)):
        raise ProposalError(f"root must be str or Path, got {type(root).__name__}")
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ProposalError(f"canonical root directory does not exist: {root_path}")

    if not isinstance(artifacts, Mapping):
        raise ProposalError(f"artifacts must be a Mapping, got {type(artifacts).__name__}")

    if "correction_count" not in artifacts:
        raise ProposalError("missing correction_count in artifacts")
    corr = artifacts["correction_count"]
    if isinstance(corr, bool) or not isinstance(corr, int):
        raise ProposalError(f"invalid correction_count type: {type(corr).__name__}")
    if corr in (1, 2):
        raise ProposalError(f"unsupported correction_count: {corr}")
    if corr != 0:
        raise ProposalError(f"invalid correction_count index: {corr}")

    task_id = artifacts.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ProposalError(f"invalid or missing task_id: {task_id!r}")

    source_head = artifacts.get("source_head")
    if not isinstance(source_head, str) or not HEAD_RE.match(source_head):
        raise ProposalError(f"invalid source_head: {source_head!r}")

    orig_prefix = artifacts.get("original_run_prefix")
    if not isinstance(orig_prefix, str) or not RUN_PREFIX_RE.match(orig_prefix):
        raise ProposalError(f"invalid original_run_prefix: {orig_prefix!r}")

    final_worker_run_id = artifacts.get("final_worker_run_id")
    if not isinstance(final_worker_run_id, str):
        raise ProposalError("missing or non-string final_worker_run_id")
    worker_id_re = re.compile(f"^{re.escape(orig_prefix)}-worker-0(-retry-[12])?$")
    if not worker_id_re.match(final_worker_run_id):
        raise ProposalError(f"invalid final_worker_run_id: {final_worker_run_id!r}")

    expected_proposal_path = f".local/two-site-refactor/proposals/{final_worker_run_id}/result.json"
    proposal_path = artifacts.get("proposal_path")
    if proposal_path != expected_proposal_path:
        raise ProposalError(
            f"invalid proposal_path: expected {expected_proposal_path!r}, got {proposal_path!r}"
        )

    proposal_sha256 = artifacts.get("proposal_sha256")
    if not isinstance(proposal_sha256, str) or not SHA_PATTERN.match(proposal_sha256):
        raise ProposalError(f"invalid proposal_sha256: {proposal_sha256!r}")

    run_dir = root_path / ".local" / "two-site-refactor" / "proposals" / final_worker_run_id
    if run_dir.is_symlink():
        raise ProposalError(f"run_dir cannot be a symlink: {run_dir}")

    task, sources = load_task(root_path, admitted_task_path)
    if not isinstance(task, Mapping) or task.get("task_id") != task_id:
        actual_task_id = task.get("task_id") if isinstance(task, Mapping) else None
        raise ProposalError(f"task task_id mismatch: task={actual_task_id!r} != artifacts={task_id!r}")

    result, status, result_sha, result_text = validate_run(
        root_path,
        run_dir,
        "worker",
        final_worker_run_id,
        source_head,
        task,
        sources,
        seen_convs=set(),
    )

    if result_sha != proposal_sha256:
        raise ProposalError(
            f"proposal SHA mismatch: validated {result_sha} != artifacts {proposal_sha256}"
        )

    return {
        "result": copy.deepcopy(result),
        "status": copy.deepcopy(status),
        "result_sha": result_sha,
        "result_text": result_text,
        "conversation_id": status.get("conversation_id"),
    }


def load_prior_worker_chain(
    root: Path | str,
    admitted_task_path: Path | str,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(root, (str, Path)):
        raise ProposalError(f"root must be str or Path, got {type(root).__name__}")
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ProposalError(f"canonical root directory does not exist: {root_path}")

    if not isinstance(artifacts, Mapping):
        raise ProposalError(f"artifacts must be a Mapping, got {type(artifacts).__name__}")

    if "correction_count" not in artifacts:
        raise ProposalError("missing correction_count in artifacts")
    corr = artifacts["correction_count"]
    if isinstance(corr, bool) or not isinstance(corr, int):
        raise ProposalError(f"invalid correction_count type: {type(corr).__name__}")
    if corr not in (0, 1, 2):
        raise ProposalError(f"invalid correction_count index: {corr}")

    if corr == 0:
        return load_prior_worker_zero(root, admitted_task_path, artifacts)

    task_id = artifacts.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ProposalError(f"invalid or missing task_id: {task_id!r}")

    source_head = artifacts.get("source_head")
    if not isinstance(source_head, str) or not HEAD_RE.match(source_head):
        raise ProposalError(f"invalid source_head: {source_head!r}")

    orig_prefix = artifacts.get("original_run_prefix")
    if not isinstance(orig_prefix, str) or not RUN_PREFIX_RE.match(orig_prefix):
        raise ProposalError(f"invalid original_run_prefix: {orig_prefix!r}")

    final_worker_run_id = artifacts.get("final_worker_run_id")
    if not isinstance(final_worker_run_id, str):
        raise ProposalError("missing or non-string final_worker_run_id")
    worker_id_re = re.compile(f"^{re.escape(orig_prefix)}-worker-{corr}(-retry-[12])?$")
    if not worker_id_re.match(final_worker_run_id):
        raise ProposalError(f"invalid final_worker_run_id: {final_worker_run_id!r}")

    expected_proposal_path = f".local/two-site-refactor/proposals/{final_worker_run_id}/result.json"
    proposal_path = artifacts.get("proposal_path")
    if proposal_path != expected_proposal_path:
        raise ProposalError(
            f"invalid proposal_path: expected {expected_proposal_path!r}, got {proposal_path!r}"
        )

    proposal_sha256 = artifacts.get("proposal_sha256")
    if not isinstance(proposal_sha256, str) or not SHA_PATTERN.match(proposal_sha256):
        raise ProposalError(f"invalid proposal_sha256: {proposal_sha256!r}")

    task, sources = load_task(root_path, admitted_task_path)
    if not isinstance(task, Mapping) or task.get("task_id") != task_id:
        actual_task_id = task.get("task_id") if isinstance(task, Mapping) else None
        raise ProposalError(f"task task_id mismatch: task={actual_task_id!r} != artifacts={task_id!r}")

    worker_ids: list[str] = ["" for _ in range(corr + 1)]
    run_dirs: list[Path] = [Path() for _ in range(corr + 1)]
    status_raw: list[bytes] = [b"" for _ in range(corr + 1)]
    status_hints: list[dict[str, Any]] = [{} for _ in range(corr + 1)]
    input_shas: list[str] = ["" for _ in range(corr + 1)]

    worker_ids[corr] = final_worker_run_id
    run_dirs[corr] = root_path / ".local" / "two-site-refactor" / "proposals" / final_worker_run_id

    for j in range(corr, 0, -1):
        curr_run_dir = run_dirs[j]
        if curr_run_dir.is_symlink():
            raise ProposalError(f"run_dir cannot be a symlink: {curr_run_dir}")

        raw_hint_bytes = bounded_file_bytes(
            root_path,
            curr_run_dir / "status.json",
            MAX_CONTEXT,
            f"worker {worker_ids[j]} status hint",
        )
        status_raw[j] = raw_hint_bytes
        hint = unique_json(raw_hint_bytes.decode("utf-8"))
        if not isinstance(hint, dict):
            raise ProposalError(f"status hint must be a dict in {worker_ids[j]}")
        status_hints[j] = hint

        prior_id = hint.get("input_worker_run_id")
        prior_id_re = re.compile(f"^{re.escape(orig_prefix)}-worker-{j - 1}(-retry-[12])?$")
        if not isinstance(prior_id, str) or not prior_id_re.match(prior_id):
            raise ProposalError(f"invalid input_worker_run_id in {worker_ids[j]}: {prior_id!r}")

        prior_sha = hint.get("input_proposal_sha256")
        if not isinstance(prior_sha, str) or not SHA_PATTERN.match(prior_sha):
            raise ProposalError(f"invalid input_proposal_sha256 in {worker_ids[j]}: {prior_sha!r}")

        worker_ids[j - 1] = prior_id
        input_shas[j] = prior_sha
        run_dirs[j - 1] = root_path / ".local" / "two-site-refactor" / "proposals" / prior_id

    if run_dirs[0].is_symlink():
        raise ProposalError(f"run_dir cannot be a symlink: {run_dirs[0]}")
    raw_hint_bytes_0 = bounded_file_bytes(
        root_path,
        run_dirs[0] / "status.json",
        MAX_CONTEXT,
        f"worker {worker_ids[0]} status hint",
    )
    status_raw[0] = raw_hint_bytes_0
    hint_0 = unique_json(raw_hint_bytes_0.decode("utf-8"))
    if not isinstance(hint_0, dict):
        raise ProposalError(f"status hint must be a dict in {worker_ids[0]}")
    status_hints[0] = hint_0

    seen_convs: set[str] = set()
    prior_worker: tuple[str, str, str, str] | None = None
    prior_result: dict[str, Any] | None = None
    last_validated: dict[str, Any] | None = None

    for j in range(corr + 1):
        curr_id = worker_ids[j]
        curr_run_dir = run_dirs[j]

        if j == 0:
            expected_feedback_sha = None
        else:
            feedback_rel = Path(".local/two-site-refactor/pipeline") / orig_prefix / f"feedback-correction-{j}.json"
            feedback_bytes = bounded_file_bytes(
                root_path,
                feedback_rel,
                MAX_CONTEXT,
                f"correction {j} feedback",
            )
            expected_feedback_sha = hashlib.sha256(feedback_bytes).hexdigest()
            feedback_text = feedback_bytes.decode("utf-8")
            prior_sha = prior_worker[1]
            validate_correction_feedback(feedback_text, prior_sha)

        result, status, result_sha, result_text = validate_run(
            root=root_path,
            run_dir=curr_run_dir,
            role="worker",
            expected_run_id=curr_id,
            expected_head=source_head,
            expected_task=task,
            expected_sources=sources,
            seen_convs=seen_convs,
            prior_worker=prior_worker,
            expected_feedback_sha=expected_feedback_sha,
        )

        if status != status_hints[j]:
            raise ProposalError(f"validated status does not match pre-read hint in {curr_id}")

        post_raw = bounded_file_bytes(
            root_path,
            curr_run_dir / "status.json",
            MAX_CONTEXT,
            f"worker {curr_id} status drift check",
        )
        if post_raw != status_raw[j]:
            raise ProposalError(f"status file drift detected in {curr_id}")

        if j > 0:
            prior_run_id, prior_run_sha, prior_text, prior_conv = prior_worker
            if status.get("input_worker_run_id") != prior_run_id:
                raise ProposalError(f"input_worker_run_id mismatch in {curr_id}")
            if status.get("input_proposal_sha256") != prior_run_sha:
                raise ProposalError(f"input_proposal_sha256 mismatch in {curr_id}")
            if status.get("input_worker_conversation_id") != prior_conv:
                raise ProposalError(f"input_worker_conversation_id mismatch in {curr_id}")
            if input_shas[j] != prior_run_sha:
                raise ProposalError(f"pre-read input proposal SHA mismatch in {curr_id}")
            if result_sha == prior_run_sha:
                raise ProposalError(f"correction proposal SHA must differ from prior in {curr_id}")
            if result.get("changes") == prior_result.get("changes"):
                raise ProposalError(f"correction proposal changes must differ from prior in {curr_id}")

        prior_worker = (curr_id, result_sha, result_text, status.get("conversation_id"))
        prior_result = result
        last_validated = {
            "result": copy.deepcopy(result),
            "status": copy.deepcopy(status),
            "result_sha": result_sha,
            "result_text": result_text,
            "conversation_id": status.get("conversation_id"),
        }

    if last_validated is None or last_validated["result_sha"] != proposal_sha256:
        actual_sha = last_validated["result_sha"] if last_validated else None
        raise ProposalError(
            f"proposal SHA mismatch: validated {actual_sha} != artifacts {proposal_sha256}"
        )

    return last_validated
