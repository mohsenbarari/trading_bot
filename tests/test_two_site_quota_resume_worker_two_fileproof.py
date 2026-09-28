"""Materialized file-backed proof for Reviewer-backed Worker-2 resume lineage."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

try:
    from scripts.two_site_pre_review_receipt import validate_test_receipt
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        test_gate_feedback as _test_gate_feedback,
    )
    from scripts.two_site_proposal_runner import (
        load_task,
        validate_correction_feedback,
    )
    from scripts.two_site_quota_resume_worker_two_file_reader import (
        read_and_bind_worker_two_result,
    )
    from scripts.two_site_semantic_attempts import extract_semantic_identity
except ImportError:
    from two_site_pre_review_receipt import (  # type: ignore[no-redef]
        validate_test_receipt,
    )
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        test_gate_feedback as _test_gate_feedback,
    )
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        load_task,
        validate_correction_feedback,
    )
    from two_site_quota_resume_worker_two_file_reader import (  # type: ignore[no-redef]
        read_and_bind_worker_two_result,
    )
    from two_site_semantic_attempts import (  # type: ignore[no-redef]
        extract_semantic_identity,
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

_test_gate_feedback.__test__ = False


def _setup_materialized_worker_two_env(
    tmp_path: Path,
    *,
    source_head: str = HEAD,
    task_data: dict[str, Any] | None = None,
    admitted_task_path: Path | str | None = None,
    original_run_prefix: str | None = None,
    origin: str = "REVIEWER",
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = tmp_path.resolve()
    build_kwargs: dict[str, Any] = {
        "source_head": source_head,
        "task_data": task_data,
        "admitted_task_path": admitted_task_path,
    }
    if original_run_prefix is not None:
        build_kwargs["orig_prefix"] = original_run_prefix
    if origin == "TEST_FAILED" and task_data is None:
        build_kwargs["selector"] = "tests/test_a.py"
    artifacts, evidence, task_path, orig_prefix = _build_materialized_env(
        root,
        **build_kwargs,
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
    if origin == "TEST_FAILED":
        root_task = json.loads(task_path.read_text(encoding="utf-8"))
        receipt1_data = dict(passed_receipt(w1_sha))
        receipt1_data["state"] = "FAILED"
        receipt1_data["returncode"] = 1
        receipt1_data["selectors"] = list(root_task["test_selectors"])
        receipt1_data["stderr_tail"] = "FAILED tests/test_a.py::test_fail - AssertionError: fail\n"
        validate_test_receipt(receipt1_data, w1_sha)
        receipt1_path.write_text(json.dumps(receipt1_data), encoding="utf-8")
        receipt1_sha = hashlib.sha256(receipt1_path.read_bytes()).hexdigest()

        fb2_data = _test_gate_feedback(receipt1_data, w1_sha)
        validate_correction_feedback(json.dumps(fb2_data), w1_sha)
        fb2_path = pipe_dir / "feedback-correction-2.json"
        fb2_path.write_text(json.dumps(fb2_data), encoding="utf-8")
        fb2_sha = hashlib.sha256(fb2_path.read_bytes()).hexdigest()
        r1_id = None
        artifacts.pop("final_reviewer_run_id", None)
        evidence.pop("reviewer_status", None)
    else:
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

    if task_data is not None and actual_task is not None:
        sem_identity = extract_semantic_identity(actual_task)
        sem_fingerprint = sem_identity["fingerprint"]
    else:
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


def test_materialized_worker_two_test_failed_provenance(tmp_path: Path) -> None:
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        tmp_path, origin="TEST_FAILED"
    )
    root_task = json.loads((root / "task.json").read_text(encoding="utf-8"))
    root_src = (root / "src.py").read_bytes()
    root_src_sha = hashlib.sha256(root_src).hexdigest()
    assert root_task["test_selectors"] == ["tests/test_a.py"]

    w0_id = f"{meta['orig_prefix']}-worker-0"
    r0_id = f"{meta['orig_prefix']}-reviewer-0"
    w1_id = meta["w1_id"]
    w2_id = meta["w2_id"]
    prop_dir = root / ".local" / "two-site-refactor" / "proposals"

    runs = [w0_id, r0_id, w1_id, w2_id]
    for rid in runs:
        snap = json.loads((prop_dir / rid / "task.json").read_text(encoding="utf-8"))
        assert set(snap.keys()) >= {"source_head", "task", "sources"}
        assert snap["task"] == root_task
        assert snap["source_head"] == HEAD
        assert snap["sources"]["src.py"]["sha256"] == root_src_sha
        assert snap["sources"]["src.py"]["content"] == root_src.decode("utf-8")

        st = json.loads((prop_dir / rid / "status.json").read_text(encoding="utf-8"))
        assert "source_head" not in st
        assert st["head"] == HEAD
        res_bytes = (prop_dir / rid / "result.json").read_bytes()
        assert st["result_sha256"] == hashlib.sha256(res_bytes).hexdigest()

    assert "proposal" not in json.loads((prop_dir / w0_id / "task.json").read_text(encoding="utf-8"))
    assert "test_evidence" not in json.loads((prop_dir / w0_id / "task.json").read_text(encoding="utf-8"))
    assert "proposal" in json.loads((prop_dir / r0_id / "task.json").read_text(encoding="utf-8"))
    assert "test_evidence" in json.loads((prop_dir / r0_id / "task.json").read_text(encoding="utf-8"))
    assert "proposal" in json.loads((prop_dir / w1_id / "task.json").read_text(encoding="utf-8"))
    assert "proposal" in json.loads((prop_dir / w2_id / "task.json").read_text(encoding="utf-8"))

    w0_res_sha = json.loads((prop_dir / w0_id / "status.json").read_text(encoding="utf-8"))["result_sha256"]
    w1_res_sha = json.loads((prop_dir / w1_id / "status.json").read_text(encoding="utf-8"))["result_sha256"]

    r0_st = json.loads((prop_dir / r0_id / "status.json").read_text(encoding="utf-8"))
    assert r0_st["input_proposal_sha256"] == w0_res_sha

    fb1_bytes = (root / ".local" / "two-site-refactor" / "pipeline" / meta["orig_prefix"] / "feedback-correction-1.json").read_bytes()
    w1_st = json.loads((prop_dir / w1_id / "status.json").read_text(encoding="utf-8"))
    assert w1_st["input_proposal_sha256"] == w0_res_sha
    assert w1_st["input_feedback_sha256"] == hashlib.sha256(fb1_bytes).hexdigest()

    fb2_path = root / ".local" / "two-site-refactor" / "pipeline" / meta["orig_prefix"] / "feedback-correction-2.json"
    fb2_bytes = fb2_path.read_bytes()
    fb2_sha = hashlib.sha256(fb2_bytes).hexdigest()
    w2_st = json.loads((prop_dir / w2_id / "status.json").read_text(encoding="utf-8"))
    assert w2_st["input_proposal_sha256"] == w1_res_sha
    assert w2_st["input_feedback_sha256"] == fb2_sha

    rcpt_path = root / ".local" / "two-site-refactor" / "pipeline" / meta["orig_prefix"] / "pre-review-tests-1.json"
    rcpt = json.loads(rcpt_path.read_text(encoding="utf-8"))
    assert rcpt["state"] == "FAILED"
    assert rcpt["selectors"] == root_task["test_selectors"]
    assert rcpt["proposal_sha256"] == w1_res_sha

    fb2 = json.loads(fb2_bytes.decode("utf-8"))
    assert fb2["reviewer"] == "test-gate"
    assert fb2["reviewed_proposal_sha256"] == w1_res_sha

    r1_dir = prop_dir / f"{meta['orig_prefix']}-reviewer-1"
    assert not r1_dir.exists()
    assert not any("reviewer-1" in p.name for p in prop_dir.iterdir())
    assert not any("worker-3" in p.name for p in prop_dir.iterdir())


def test_materialized_worker_two_rejects_tampered_persisted_feedback_two(
    tmp_path: Path,
) -> None:
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(tmp_path)

    prop_dir = root / ".local" / "two-site-refactor" / "proposals"
    runs = [
        f"{meta['orig_prefix']}-worker-0",
        f"{meta['orig_prefix']}-reviewer-0",
        meta["w1_id"],
        meta["w2_id"],
    ]
    before_bytes = {
        (rid, fname): (prop_dir / rid / fname).read_bytes()
        for rid in runs
        for fname in ("result.json", "status.json")
    }

    fb2_path = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / meta["orig_prefix"]
        / "feedback-correction-2.json"
    )
    current_fb2 = json.loads(fb2_path.read_text(encoding="utf-8"))
    tampered_fb2 = copy.deepcopy(current_fb2)
    tampered_fb2["findings"] = ["Worker1 proposal needs correction for different edge cases"]
    tampered_bytes = json.dumps(tampered_fb2, indent=2).encode("utf-8")
    assert hashlib.sha256(tampered_bytes).hexdigest() != ctx["evidence"]["feedback_sha"]

    fb2_path.write_bytes(tampered_bytes)

    with _patch_load_task():
        with pytest.raises(ProposalError, match="SHA mismatch"):
            read_and_bind_worker_two_result(root, ctx, returned_state)

    for (rid, fname), orig_bytes in before_bytes.items():
        assert (prop_dir / rid / fname).read_bytes() == orig_bytes

    # Missing feedback-2 file
    fb2_path.unlink()
    with _patch_load_task():
        with pytest.raises(ProposalError):
            read_and_bind_worker_two_result(root, ctx, returned_state)

    for (rid, fname), orig_bytes in before_bytes.items():
        assert (prop_dir / rid / fname).read_bytes() == orig_bytes

    # Symlinked feedback-2 file
    other_fb2 = tmp_path / "other_feedback.json"
    other_fb2.write_bytes(json.dumps(current_fb2).encode("utf-8"))
    fb2_path.symlink_to(other_fb2)
    with _patch_load_task():
        with pytest.raises(ProposalError):
            read_and_bind_worker_two_result(root, ctx, returned_state)

    for (rid, fname), orig_bytes in before_bytes.items():
        assert (prop_dir / rid / fname).read_bytes() == orig_bytes
