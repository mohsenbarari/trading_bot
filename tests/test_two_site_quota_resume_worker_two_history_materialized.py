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

try:
    from scripts.two_site_proposal_runner import load_task
except ImportError:
    from two_site_proposal_runner import load_task  # type: ignore[no-redef]


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
    *,
    source_head: str = HEAD,
    task_data: dict[str, Any] | None = None,
    admitted_task_path: Path | str | None = None,
    selector: str = "test.py",
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    root = tmp_path.resolve()
    if admitted_task_path is None:
        task_path = root / "task.json"
    else:
        raw_task_path = Path(admitted_task_path)
        if not raw_task_path.is_absolute():
            candidate_task_path = (root / raw_task_path).resolve()
        else:
            candidate_task_path = raw_task_path.resolve()
        try:
            candidate_task_path.relative_to(root)
        except ValueError as exc:
            raise ProposalError(
                f"admitted_task_path outside root: {candidate_task_path}"
            ) from exc
        if candidate_task_path == root:
            raise ProposalError("admitted_task_path cannot be root directory itself")
        task_path = candidate_task_path

    if task_data is None:
        src_file = root / "src.py"
        src_file.write_text("# orig\n", encoding="utf-8")
        effective_task = {
            "task_id": "T1",
            "goal": "Goal description",
            "context_files": ["src.py"],
            "allowed_files": ["src.py"],
            "requirements": ["R1"],
            "test_selectors": [selector],
        }
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(json.dumps(effective_task), encoding="utf-8")
        loaded_task_id = "T1"
        real_sources = None
    elif admitted_task_path is not None and task_path.exists():
        loaded_task, real_sources = load_task(root, task_path)
        if loaded_task != task_data:
            raise ProposalError(
                f"admitted_task_path content does not match task_data: {task_path}"
            )
        loaded_task_id = loaded_task["task_id"]
    else:
        effective_task = copy.deepcopy(task_data)
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(json.dumps(effective_task), encoding="utf-8")
        loaded_task, real_sources = load_task(root, task_path)
        loaded_task_id = loaded_task["task_id"]

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
    if task_data is not None and real_sources is not None:
        for resp in responses:
            resp["task_override"] = {"sources": real_sources}

    runner, _ = make_runner(root, responses, source_head=source_head)

    w0_id = f"{orig_prefix}-worker-0"
    w0_dir = runner(root, task_path, "worker", w0_id, 300)
    w0_sha = hashlib.sha256((w0_dir / "result.json").read_bytes()).hexdigest()

    pipe_dir = root / ".local" / "two-site-refactor" / "pipeline" / orig_prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    receipt0_path = pipe_dir / "pre-review-tests-0.json"
    receipt0_data = passed_receipt(w0_sha)
    receipt0_path.write_text(json.dumps(receipt0_data), encoding="utf-8")
    receipt0_sha = hashlib.sha256(receipt0_path.read_bytes()).hexdigest()

    r0_id = f"{orig_prefix}-reviewer-0"
    runner(
        root,
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
        root,
        task_path,
        "worker",
        w1_id,
        300,
        feedback=fb1_path,
        proposal=w0_dir / "result.json",
    )
    w1_sha = hashlib.sha256((w1_dir / "result.json").read_bytes()).hexdigest()

    w1_artifacts = {
        "task_id": loaded_task_id,
        "source_head": source_head,
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": w1_id,
        "proposal_path": f".local/two-site-refactor/proposals/{w1_id}/result.json",
        "proposal_sha256": w1_sha,
        "correction_count": 1,
    }
    if task_data is None:
        with _patch_load_task():
            w1_info = load_prior_worker_chain(root, task_path, w1_artifacts)
    else:
        w1_info = load_prior_worker_chain(root, task_path, w1_artifacts)

    r1_id = f"{orig_prefix}-reviewer-1"
    r1_status = {"run_id": r1_id, "conversation_id": f"conv-{r1_id}"}

    artifacts = {
        "worker_index": 2,
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": w1_id,
        "final_reviewer_run_id": r1_id,
        "proposal_sha256": w1_sha,
        "task_id": loaded_task_id,
        "source_head": source_head,
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


def test_materialized_history_rejects_outside_root_admitted_task_path(
    tmp_path: Path,
) -> None:
    outside_name = f"outside_{tmp_path.name}_task.json"
    outside_path = tmp_path.parent / outside_name
    if outside_path.exists():
        pytest.fail(f"preexisting collision for outside path: {outside_path}")
    assert not outside_path.exists()

    with pytest.raises((ProposalError, SandboxError, ValueError)):
        _build_materialized_env(tmp_path, admitted_task_path=outside_path)

    assert not outside_path.exists()


def test_materialized_env_custom_selector(tmp_path: Path) -> None:
    _, _, task_path, orig_prefix = _build_materialized_env(
        tmp_path, task_data=None, selector="tests/test_a.py"
    )
    root_task = json.loads(task_path.read_text(encoding="utf-8"))
    assert root_task["test_selectors"] == ["tests/test_a.py"]
    for run_suffix in ("worker-0", "reviewer-0", "worker-1"):
        snap_path = (
            tmp_path / ".local" / "two-site-refactor" / "proposals" / f"{orig_prefix}-{run_suffix}" / "task.json"
        )
        snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
        assert snapshot["task"]["test_selectors"] == ["tests/test_a.py"]
        assert snapshot["source_head"] == HEAD
