"""Materialized end-to-end proofs for Gemini Reviewer0 history completion."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch
import pytest

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_quota_resume_worker_two_history_complete import (
        complete_worker_two_history,
    )
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    from two_site_quota_resume_worker_two_history_complete import (  # type: ignore[no-redef]
        complete_worker_two_history,
    )
    from two_site_quota_resume_worker_reader import (  # type: ignore[no-redef]
        load_prior_worker_chain,
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
    from tests.test_two_site_proposal_pipeline import HEAD, make_runner, passed_receipt
except ImportError:
    from test_two_site_proposal_pipeline import (  # type: ignore[no-redef]
        HEAD,
        make_runner,
        passed_receipt,
    )


def _fake_load_task(root: Path | str, task_path: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    root_p = Path(root)
    t_path = Path(task_path)
    if not t_path.is_absolute():
        t_path = root_p / t_path
    task_dict = json.loads(t_path.read_text(encoding="utf-8"))
    src_p = root_p / "src.py"
    src_content = src_p.read_text(encoding="utf-8")
    src_sha = hashlib.sha256(src_content.encode("utf-8")).hexdigest()
    return (
        copy.deepcopy(task_dict),
        {"src.py": {"sha256": src_sha, "content": src_content}},
    )


@contextmanager
def _patch_load_task():
    hist_mod = sys.modules[complete_worker_two_history.__module__]
    reader_mod = sys.modules[load_prior_worker_chain.__module__]
    patchers = [
        patch.object(hist_mod, "load_task", side_effect=_fake_load_task),
        patch.object(reader_mod, "load_task", side_effect=_fake_load_task),
    ]
    pipe_mod = sys.modules.get("scripts.two_site_proposal_pipeline") or sys.modules.get(
        "two_site_proposal_pipeline"
    )
    if pipe_mod is not None and hasattr(pipe_mod, "load_task"):
        patchers.append(patch.object(pipe_mod, "load_task", side_effect=_fake_load_task))
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patchers):
            p.stop()


def _build_materialized_env(
    tmp_path: Path,
    orig_prefix: str = "run-20260925-mat",
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    src_file = tmp_path / "src.py"
    src_file.write_text("# orig\n", encoding="utf-8")

    task_path = tmp_path / "task.json"
    task_data = {
        "task_id": "T1",
        "goal": "Goal description",
        "context_files": ["src.py"],
        "allowed_files": ["src.py"],
        "requirements": ["R1"],
        "test_selectors": ["test.py"],
    }
    task_path.write_text(json.dumps(task_data), encoding="utf-8")

    findings = ["Worker0 proposal needs correction"]
    responses = [
        {"content": "# worker-0\n", "conv_id": "conv-w0"},
        {
            "status": "CHANGES_REQUIRED",
            "findings": findings,
            "conv_id": "conv-r0",
        },
        {"content": "# worker-1\n", "conv_id": "conv-w1"},
    ]
    runner, _ = make_runner(tmp_path, responses)

    w0_id = f"{orig_prefix}-worker-0"
    w0_dir = runner(tmp_path, task_path, "worker", w0_id, 300)
    w0_sha = hashlib.sha256((w0_dir / "result.json").read_bytes()).hexdigest()

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / orig_prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    receipt0_path = pipe_dir / "pre-review-tests-0.json"
    receipt0_data = passed_receipt(w0_sha)
    receipt0_path.write_text(json.dumps(receipt0_data), encoding="utf-8")
    receipt0_sha = hashlib.sha256(receipt0_path.read_bytes()).hexdigest()

    r0_id = f"{orig_prefix}-reviewer-0"
    runner(
        tmp_path,
        task_path,
        "verifier",
        r0_id,
        300,
        proposal=w0_dir / "result.json",
        test_evidence=receipt0_path,
    )

    fb1_path = pipe_dir / "feedback-correction-1.json"
    fb1_data = {
        "reviewer": "gemini",
        "reviewed_proposal_sha256": w0_sha,
        "findings": findings,
        "test_evidence": [
            f"receipt_sha256={receipt0_sha} proposal_sha256={w0_sha} selector_count=1"
        ],
    }
    fb1_path.write_text(json.dumps(fb1_data), encoding="utf-8")

    w1_id = f"{orig_prefix}-worker-1"
    w1_dir = runner(
        tmp_path,
        task_path,
        "worker",
        w1_id,
        300,
        feedback=fb1_path,
        proposal=w0_dir / "result.json",
    )
    w1_sha = hashlib.sha256((w1_dir / "result.json").read_bytes()).hexdigest()

    w1_artifacts = {
        "task_id": "T1",
        "source_head": HEAD,
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": w1_id,
        "proposal_path": f".local/two-site-refactor/proposals/{w1_id}/result.json",
        "proposal_sha256": w1_sha,
        "correction_count": 1,
    }
    with _patch_load_task():
        w1_info = load_prior_worker_chain(tmp_path, task_path, w1_artifacts)

    r1_id = f"{orig_prefix}-reviewer-1"
    r1_status = {"run_id": r1_id, "conversation_id": f"conv-{r1_id}"}

    artifacts = {
        "worker_index": 2,
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": w1_id,
        "final_reviewer_run_id": r1_id,
        "proposal_sha256": w1_sha,
        "task_id": "T1",
        "source_head": HEAD,
    }
    evidence = {
        "worker_info": w1_info,
        "reviewer_status": r1_status,
    }
    return artifacts, evidence, task_path, orig_prefix


def test_materialized_gemini_history_completion_happy_path(tmp_path: Path) -> None:
    artifacts, evidence, task_path, orig_prefix = _build_materialized_env(tmp_path)

    with _patch_load_task():
        res = complete_worker_two_history(tmp_path, task_path, artifacts, evidence)

    assert res["complete"] is True
    assert res["feedback1_kind"] == "gemini"
    assert res["reviewer0_id"] == f"{orig_prefix}-reviewer-0"
    assert res["reviewer0_run_id"] == f"{orig_prefix}-reviewer-0"
    assert res["reviewer0_conversation_id"] == "conv-r0"
    assert res["conversation_ids"] == ["conv-w0", "conv-r0", "conv-w1", f"conv-{orig_prefix}-reviewer-1"]
    assert res["ordered_conversation_ids"] == ["conv-w0", "conv-r0", "conv-w1", f"conv-{orig_prefix}-reviewer-1"]
    assert res["reviewer0_result"]["status"] == "CHANGES_REQUIRED"
    assert res["reviewer0_status"]["state"] == "CHANGES_REQUIRED"
    assert res["reviewer0_status"]["role"] == "verifier"
    assert res["reviewer0_result"]["findings"] == ["Worker0 proposal needs correction"]


def test_materialized_gemini_rejects_tampered_reviewer0_status_binding(tmp_path: Path) -> None:
    artifacts, evidence, task_path, orig_prefix = _build_materialized_env(tmp_path)

    r0_status_path = (
        tmp_path / ".local" / "two-site-refactor" / "proposals" / f"{orig_prefix}-reviewer-0" / "status.json"
    )
    st = json.loads(r0_status_path.read_text(encoding="utf-8"))
    st["input_proposal_sha256"] = "0" * 64
    r0_status_path.write_text(json.dumps(st), encoding="utf-8")

    with _patch_load_task():
        with pytest.raises((ProposalError, SandboxError)):
            complete_worker_two_history(tmp_path, task_path, artifacts, evidence)


def test_materialized_gemini_rejects_tampered_passed_receipt(tmp_path: Path) -> None:
    artifacts, evidence, task_path, orig_prefix = _build_materialized_env(tmp_path)

    receipt_path = (
        tmp_path / ".local" / "two-site-refactor" / "pipeline" / orig_prefix / "pre-review-tests-0.json"
    )
    rcpt = json.loads(receipt_path.read_text(encoding="utf-8"))
    rcpt["state"] = "FAILED"
    rcpt["returncode"] = 1
    receipt_path.write_text(json.dumps(rcpt), encoding="utf-8")

    with _patch_load_task():
        with pytest.raises((ProposalError, SandboxError)):
            complete_worker_two_history(tmp_path, task_path, artifacts, evidence)


def test_materialized_gemini_rejects_tampered_feedback1_findings(tmp_path: Path) -> None:
    artifacts, evidence, task_path, orig_prefix = _build_materialized_env(tmp_path)

    fb1_path = (
        tmp_path / ".local" / "two-site-refactor" / "pipeline" / orig_prefix / "feedback-correction-1.json"
    )
    fb = json.loads(fb1_path.read_text(encoding="utf-8"))
    fb["findings"] = ["tampered divergent finding"]
    fb1_path.write_text(json.dumps(fb), encoding="utf-8")

    with _patch_load_task():
        with pytest.raises((ProposalError, SandboxError), match="Reviewer0 findings mismatch"):
            complete_worker_two_history(tmp_path, task_path, artifacts, evidence)
