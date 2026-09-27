"""Materialized file-backed proof for Reviewer-backed Worker-2 resume lineage."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_proposal_runner import load_task
    from scripts.two_site_quota_resume_worker_two_file_reader import (
        read_and_bind_worker_two_result,
    )
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    from two_site_proposal_runner import load_task  # type: ignore[no-redef]
    from two_site_quota_resume_worker_two_file_reader import (  # type: ignore[no-redef]
        read_and_bind_worker_two_result,
    )

try:
    from scripts.two_site_agent_sandbox import SandboxError
except ImportError:
    try:
        from two_site_agent_sandbox import SandboxError  # type: ignore[no-redef]
    except ImportError:
        try:
            from scripts.two_site_proposal_pipeline import SandboxError  # type: ignore[no-redef]
        except ImportError:
            SandboxError = type("SandboxError", (Exception,), {})  # type: ignore[misc,assignment]

try:
    from tests.test_two_site_proposal_pipeline import (
        HEAD,
        make_runner,
        passed_receipt,
    )
except ImportError:
    from test_two_site_proposal_pipeline import (  # type: ignore[no-redef]
        HEAD,
        make_runner,
        passed_receipt,
    )

try:
    from tests.test_two_site_quota_resume_worker_two_history_materialized import (
        _build_materialized_env,
        _patch_load_task,
    )
except ImportError:
    from test_two_site_quota_resume_worker_two_history_materialized import (  # type: ignore[no-redef]
        _build_materialized_env,
        _patch_load_task,
    )


def _setup_materialized_worker_two_env(
    tmp_path: Path,
    *,
    source_head: str = HEAD,
    task_data: dict[str, Any] | None = None,
    admitted_task_path: Path | str | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = tmp_path.resolve()
    artifacts, evidence, task_path, orig_prefix = _build_materialized_env(
        root,
        source_head=source_head,
        task_data=task_data,
        admitted_task_path=admitted_task_path,
    )

    if task_data is not None:
        actual_task, real_sources = load_task(root, task_path)
    else:
        actual_task = None
        real_sources = None

    w1_id = artifacts["final_worker_run_id"]
    w1_sha = artifacts["proposal_sha256"]
    w1_dir = root / ".local" / "two-site-refactor" / "proposals" / w1_id
    w1_result_path = w1_dir / "result.json"

    pipe_dir = root / ".local" / "two-site-refactor" / "pipeline" / orig_prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)

    receipt1_path = pipe_dir / "pre-review-tests-1.json"
    receipt1_data = passed_receipt(w1_sha)
    receipt1_path.write_text(json.dumps(receipt1_data), encoding="utf-8")
    receipt1_sha = hashlib.sha256(receipt1_path.read_bytes()).hexdigest()

    r1_id = artifacts["final_reviewer_run_id"]
    r1_findings = ["Worker1 proposal needs correction for edge cases"]
    r1_response: dict[str, Any] = {
        "status": "CHANGES_REQUIRED",
        "findings": r1_findings,
        "conv_id": f"conv-{r1_id}",
    }
    if task_data is not None and real_sources is not None:
        r1_response["task_override"] = {"sources": real_sources}
    r1_runner, _ = make_runner(root, [r1_response], source_head=source_head)
    r1_dir = r1_runner(
        root,
        task_path,
        "verifier",
        r1_id,
        300,
        proposal=w1_result_path,
        test_evidence=receipt1_path,
    )
    r1_status = json.loads((r1_dir / "status.json").read_text(encoding="utf-8"))
    evidence["reviewer_status"] = r1_status

    fb2_data = {
        "reviewer": "gemini",
        "reviewed_proposal_sha256": w1_sha,
        "findings": r1_findings,
        "test_evidence": [
            f"receipt_sha256={receipt1_sha} proposal_sha256={w1_sha} selector_count=1"
        ],
    }
    fb2_path = pipe_dir / "feedback-correction-2.json"
    fb2_path.write_text(json.dumps(fb2_data), encoding="utf-8")
    fb2_sha = hashlib.sha256(fb2_path.read_bytes()).hexdigest()

    continuation_run_prefix = f"{orig_prefix}-cont"
    w2_id = f"{continuation_run_prefix}-worker-2"
    w2_conv_id = f"conv-{w2_id}"
    w2_response: dict[str, Any] = {
        "content": "# worker-2 materialized correction\n",
        "conv_id": w2_conv_id,
    }
    if task_data is not None and real_sources is not None:
        w2_response["task_override"] = {"sources": real_sources}
    w2_runner, _ = make_runner(root, [w2_response], source_head=source_head)
    w2_dir = w2_runner(
        root,
        task_path,
        "worker",
        w2_id,
        300,
        feedback=fb2_path,
        proposal=w1_result_path,
    )
    w2_result_raw = (w2_dir / "result.json").read_bytes()
    w2_sha = hashlib.sha256(w2_result_raw).hexdigest()
    w2_result = json.loads(w2_result_raw.decode("utf-8"))
    w2_changed_paths = [c["path"] for c in w2_result.get("changes", [])]

    sem_fingerprint = "ae03-c5d-w2-fingerprint-20260926"
    artifacts["semantic_task_fingerprint"] = sem_fingerprint
    evidence["feedback_sha"] = fb2_sha

    if task_data is None:
        effective_task = json.loads(task_path.read_text(encoding="utf-8"))
        src_path = root / "src.py"
        src_content = src_path.read_text(encoding="utf-8")
        src_sha = hashlib.sha256(src_content.encode("utf-8")).hexdigest()
        effective_sources = {"src.py": {"sha256": src_sha, "content": src_content}}
    else:
        effective_task = copy.deepcopy(actual_task)
        effective_sources = copy.deepcopy(real_sources)

    ctx = {
        "continuation_run_prefix": continuation_run_prefix,
        "task": copy.deepcopy(effective_task),
        "source_head": source_head,
        "sources": copy.deepcopy(effective_sources),
        "admitted_task_path": task_path,
        "artifacts": artifacts,
        "evidence": evidence,
    }

    returned_state = {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": effective_task["task_id"],
        "source_head": source_head,
        "final_worker_run_id": w2_id,
        "proposal_sha256": w2_sha,
        "changed_paths": w2_changed_paths,
        "proposal_path": f".local/two-site-refactor/proposals/{w2_id}/result.json",
        "correction_count": 2,
        "semantic_task_fingerprint": sem_fingerprint,
    }

    meta = {
        "orig_prefix": orig_prefix,
        "continuation_run_prefix": continuation_run_prefix,
        "w1_id": w1_id,
        "w1_sha": w1_sha,
        "r1_id": r1_id,
        "w2_id": w2_id,
        "w2_sha": w2_sha,
        "w2_conv_id": w2_conv_id,
        "w2_result_text": w2_result_raw.decode("utf-8"),
    }

    return root, ctx, returned_state, meta


def test_materialized_worker_two_read_and_bind_success(tmp_path: Path) -> None:
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(tmp_path)

    with _patch_load_task():
        res = read_and_bind_worker_two_result(root, ctx, returned_state)

    expected_current_worker = (
        meta["w2_id"],
        meta["w2_sha"],
        meta["w2_result_text"],
        meta["w2_conv_id"],
    )
    assert res["current_worker"] == expected_current_worker

    expected_ordered_convs = [
        "conv-w0",
        "conv-r0",
        "conv-w1",
        f"conv-{meta['r1_id']}",
        meta["w2_conv_id"],
    ]
    assert res["seen_convs"] == set(expected_ordered_convs)
    assert res["complete_history"]["ordered_conversation_ids"] == [
        "conv-w0",
        "conv-r0",
        "conv-w1",
        f"conv-{meta['r1_id']}",
    ]
    assert res["complete_history"]["complete"] is True

    assert res["returned_state"]["state"] == "WORKER_PROPOSAL_READY"
    assert res["returned_state"]["correction_count"] == 2
    assert res["returned_state"]["proposal_sha256"] == meta["w2_sha"]
    assert res["returned_state"]["final_worker_run_id"] == meta["w2_id"]

    worker_info = res["worker_info"]
    assert worker_info["result_sha"] == meta["w2_sha"]
    assert worker_info["conversation_id"] == meta["w2_conv_id"]
    assert worker_info["result"]["status"] == "PROPOSAL_READY"
    assert worker_info["status"]["state"] == "PROPOSAL_READY"
    assert worker_info["status"]["run_id"] == meta["w2_id"]


def test_materialized_worker_two_rejects_tampered_r0_status_binding(
    tmp_path: Path,
) -> None:
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(tmp_path)

    r0_status_path = (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / f"{meta['orig_prefix']}-reviewer-0"
        / "status.json"
    )
    st = json.loads(r0_status_path.read_text(encoding="utf-8"))
    st["input_proposal_sha256"] = "0" * 64
    r0_status_path.write_text(json.dumps(st), encoding="utf-8")

    with _patch_load_task():
        with pytest.raises((ProposalError, SandboxError)):
            read_and_bind_worker_two_result(root, ctx, returned_state)


def test_materialized_worker_two_rejects_tampered_w2_feedback_sha(
    tmp_path: Path,
) -> None:
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(tmp_path)

    w2_status_path = (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / meta["w2_id"]
        / "status.json"
    )
    st = json.loads(w2_status_path.read_text(encoding="utf-8"))
    st["input_feedback_sha256"] = "f" * 64
    w2_status_path.write_text(json.dumps(st), encoding="utf-8")

    with _patch_load_task():
        with pytest.raises(ProposalError, match="feedback SHA mismatch"):
            read_and_bind_worker_two_result(root, ctx, returned_state)


def test_materialized_worker_two_rejects_outside_root_admitted_task_path(
    tmp_path: Path,
) -> None:
    outside_name = f"outside_{tmp_path.name}_task.json"
    outside_path = tmp_path.parent / outside_name
    if outside_path.exists():
        pytest.fail(f"preexisting collision for outside path: {outside_path}")
    assert not outside_path.exists()

    with pytest.raises((ProposalError, SandboxError, ValueError)):
        _setup_materialized_worker_two_env(
            tmp_path, admitted_task_path=outside_path
        )

    assert not outside_path.exists()
