"""Materialized outer test-gate proof for Worker-2 resume lineage.

This is an isolated test double proof, NOT common Broker request admission,
real Worker/Reviewer Provider, real candidate sandbox,
Broker/Supervisor live hook, replay authority or full C5d completion.
Candidate sandbox/Provider and Broker/Supervisor live hooks are mocked/unhooked.
Design note: observational quota, no replay authority, no product/production change.
"""

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
    import scripts.two_site_proposal_pipeline as pipeline_mod
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        pipeline_lock,
        test_gate_feedback as producer_test_gate_feedback,
    )
    from scripts.two_site_proposal_runner import validate_correction_feedback
    from scripts.two_site_pre_review_receipt import (
        p1_00_characterization_bootstrap_findings,
        validate_test_receipt,
    )
    import scripts.two_site_quota_resume_post_worker_two as post_mod
    import scripts.two_site_quota_resume_role_dispatch as dispatch_mod
    from scripts.two_site_quota_resume_role_dispatch import (
        dispatch_quota_continuation_once,
    )
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
    from scripts.two_site_quota_resume_worker_two_history_complete import (
        complete_worker_two_history,
    )
except ImportError:
    import two_site_proposal_pipeline as pipeline_mod  # type: ignore[no-redef]
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        pipeline_lock,
        test_gate_feedback as producer_test_gate_feedback,
    )
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        validate_correction_feedback,
    )
    from two_site_pre_review_receipt import (  # type: ignore[no-redef]
        p1_00_characterization_bootstrap_findings,
        validate_test_receipt,
    )
    import two_site_quota_resume_post_worker_two as post_mod  # type: ignore[no-redef]
    import two_site_quota_resume_role_dispatch as dispatch_mod  # type: ignore[no-redef]
    from two_site_quota_resume_role_dispatch import (  # type: ignore[no-redef]
        dispatch_quota_continuation_once,
    )
    from two_site_quota_resume_worker_reader import (  # type: ignore[no-redef]
        load_prior_worker_chain,
    )
    from two_site_quota_resume_worker_two_history_complete import (  # type: ignore[no-redef]
        complete_worker_two_history,
    )

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


def _fake_load_task(
    root: Path | str, task_path: Path | str
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_p = Path(root)
    t_path = Path(task_path)
    if not t_path.is_absolute():
        t_path = root_p / t_path
    raw = json.loads(t_path.read_text(encoding="utf-8"))
    task_dict = raw.get("task", raw) if isinstance(raw, dict) and "task" in raw and isinstance(raw["task"], dict) else raw
    sources: dict[str, Any] = {}
    for name in dict.fromkeys(
        task_dict.get("context_files", []) + task_dict.get("allowed_files", [])
    ):
        p = root_p / name
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            sources[name] = {
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content": content,
            }
        else:
            sources[name] = {"sha256": None, "content": None}
    return copy.deepcopy(task_dict), sources


@contextmanager
def _patch_load_task():
    mods = [
        sys.modules.get(complete_worker_two_history.__module__),
        sys.modules.get(load_prior_worker_chain.__module__),
        sys.modules.get("scripts.two_site_proposal_pipeline"),
        sys.modules.get("two_site_proposal_pipeline"),
        sys.modules.get("scripts.two_site_proposal_runner"),
        sys.modules.get("two_site_proposal_runner"),
        sys.modules.get("scripts.two_site_quota_resume_post_worker_two"),
        sys.modules.get("two_site_quota_resume_post_worker_two"),
        sys.modules.get("scripts.two_site_quota_resume_post_worker_two_remainder"),
        sys.modules.get("two_site_quota_resume_post_worker_two_remainder"),
        sys.modules.get("scripts.two_site_quota_resume_worker_two_file_reader"),
        sys.modules.get("two_site_quota_resume_worker_two_file_reader"),
        sys.modules.get("scripts.two_site_quota_resume_role_dispatch"),
        sys.modules.get("two_site_quota_resume_role_dispatch"),
    ]
    patchers = [
        patch.object(m, "load_task", side_effect=_fake_load_task)
        for m in dict.fromkeys(m for m in mods if m and hasattr(m, "load_task"))
    ]
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patchers):
            p.stop()


def _setup_env(root: Path, origin: str) -> dict[str, Any]:
    src_content = "# orig\n"
    (root / "src.py").write_text(src_content, encoding="utf-8")
    src_sha = hashlib.sha256(src_content.encode("utf-8")).hexdigest()

    orig_prefix = f"proof-gate-{origin.lower()}"
    cont_prefix = f"{orig_prefix}-cont"
    sem_fp = f"fp-proof-{origin.lower()}-5e92fa77"
    task_path = root / "task.json"

    new_test = "tests/test_p1_00_characterization_example.py"
    valid_bootstrap_content = (
        "import os\n"
        "os.environ['APP_ENV_FILE'] = 'config/unit-test.env.example'\n"
        "from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence\n"
        "install_p1_00_network_fence()\n\n"
        "def test_example() -> None:\n"
        "    assert True\n"
    )

    sources: dict[str, Any] = {"src.py": {"sha256": src_sha, "content": src_content}}

    if origin == "BOOTSTRAP":
        task = {
            "task_id": "T1",
            "goal": "G",
            "context_files": ["src.py"],
            "allowed_files": ["src.py", new_test],
            "requirements": ["R1"],
            "test_selectors": ["tests/test_a.py"],
        }
        sources[new_test] = {"sha256": None, "content": None}
        task_override = {"sources": copy.deepcopy(sources)}
        w0_resp = {"content": "# worker-0\n", "conv_id": "conv-w0", "task_override": copy.deepcopy(task_override)}
        r0_resp = {"status": "CHANGES_REQUIRED", "findings": ["W0 fix needed"], "conv_id": "conv-r0", "task_override": copy.deepcopy(task_override)}
        w1_resp = {
            "result_override": {
                "status": "PROPOSAL_READY",
                "summary": "w1 bootstrap defect",
                "covered_requirements": [1],
                "findings": [],
                "changes": [{"path": new_test, "original_sha256": None, "content": "import main\n"}],
            },
            "conv_id": "conv-w1",
            "task_override": copy.deepcopy(task_override),
        }
        w2_resp = {
            "result_override": {
                "status": "PROPOSAL_READY",
                "summary": "w2 valid bootstrap correction",
                "covered_requirements": [1],
                "findings": [],
                "changes": [{"path": new_test, "original_sha256": None, "content": valid_bootstrap_content}],
            },
            "conv_id": "conv-w2",
            "task_override": copy.deepcopy(task_override),
        }
    else:
        task = {
            "task_id": "T1",
            "goal": "G",
            "context_files": ["src.py"],
            "allowed_files": ["src.py"],
            "requirements": ["R1"],
            "test_selectors": ["tests/test_a.py"],
        }
        w0_resp = {"content": "# worker-0\n", "conv_id": "conv-w0"}
        r0_resp = {"status": "CHANGES_REQUIRED", "findings": ["W0 fix needed"], "conv_id": "conv-r0"}
        w1_resp = {"content": "# worker-1\n", "conv_id": "conv-w1"}
        w2_resp = {"content": "# worker-2 materialized correction\n", "conv_id": "conv-w2"}

    task_path.write_text(json.dumps(task), encoding="utf-8")
    runner, _ = make_runner(root, [w0_resp, r0_resp, w1_resp, w2_resp])

    w0_id = f"{orig_prefix}-worker-0"
    w0_dir = runner(root, task_path, "worker", w0_id, 300)
    w0_sha = json.loads((w0_dir / "status.json").read_text(encoding="utf-8"))["result_sha256"]

    pipe_dir = root / ".local/two-site-refactor/pipeline" / orig_prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    rcpt0_path = pipe_dir / "pre-review-tests-0.json"
    rcpt0_path.write_text(json.dumps(passed_receipt(w0_sha)), encoding="utf-8")
    rcpt0_sha = hashlib.sha256(rcpt0_path.read_bytes()).hexdigest()

    r0_id = f"{orig_prefix}-reviewer-0"
    runner(root, task_path, "verifier", r0_id, 300, proposal=w0_dir / "result.json", test_evidence=rcpt0_path)

    fb1_path = pipe_dir / "feedback-correction-1.json"
    fb1_data = {
        "reviewer": "gemini",
        "reviewed_proposal_sha256": w0_sha,
        "findings": ["W0 fix needed"],
        "test_evidence": [f"receipt_sha256={rcpt0_sha} proposal_sha256={w0_sha} selector_count=1"],
    }
    fb1_path.write_text(json.dumps(fb1_data), encoding="utf-8")

    w1_id = f"{orig_prefix}-worker-1"
    w1_dir = runner(root, task_path, "worker", w1_id, 300, feedback=fb1_path, proposal=w0_dir / "result.json")
    w1_res = json.loads((w1_dir / "result.json").read_text(encoding="utf-8"))
    w1_sha = json.loads((w1_dir / "status.json").read_text(encoding="utf-8"))["result_sha256"]

    if origin == "BOOTSTRAP":
        findings = p1_00_characterization_bootstrap_findings(w1_res)
        assert len(findings) > 0
        fb2_data = validate_correction_feedback(
            json.dumps({
                "reviewer": "test-gate",
                "reviewed_proposal_sha256": w1_sha,
                "findings": findings,
                "test_evidence": [
                    "gate=P1-00-characterization-bootstrap",
                    f"proposal_sha256={w1_sha}",
                    f"finding_count={len(findings)}",
                ],
            }),
            w1_sha,
        )
        receipt1, rcpt1_path_str, rcpt1_sha, rcpt1_state = None, None, None, None
    else:
        raw_rcpt = {
            "state": "FAILED",
            "proposal_sha256": w1_sha,
            "selectors": ["tests/test_a.py"],
            "unsupported_specs": [],
            "returncode": 1,
            "stdout_tail": "FAILED tests/test_a.py::test_case",
            "stderr_tail": "",
            "snapshot_files": 1,
            "snapshot_bytes": 10,
            "isolation": "test-double",
        }
        receipt1 = validate_test_receipt(raw_rcpt, w1_sha)
        rcpt1_file = pipe_dir / "pre-review-tests-1.json"
        rcpt1_file.write_text(json.dumps(receipt1), encoding="utf-8")
        rcpt1_sha = hashlib.sha256(rcpt1_file.read_bytes()).hexdigest()
        rcpt1_path_str = rcpt1_file.relative_to(root).as_posix()
        rcpt1_state = "FAILED"
        fb2_data = producer_test_gate_feedback(receipt1, w1_sha)

    fb2_path = pipe_dir / "feedback-correction-2.json"
    fb2_path.write_text(json.dumps(fb2_data), encoding="utf-8")
    fb2_sha = hashlib.sha256(fb2_path.read_bytes()).hexdigest()

    w2_id = f"{cont_prefix}-worker-2"
    w2_dir = runner(root, task_path, "worker", w2_id, 300, feedback=fb2_path, proposal=w1_dir / "result.json")
    w2_raw = (w2_dir / "result.json").read_bytes()
    w2_sha = hashlib.sha256(w2_raw).hexdigest()
    w2_res = json.loads(w2_raw.decode("utf-8"))
    w2_st = json.loads((w2_dir / "status.json").read_text(encoding="utf-8"))

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
        w1_info = load_prior_worker_chain(root, task_path, w1_artifacts)

    artifacts: dict[str, Any] = {
        "worker_index": 2,
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": w1_id,
        "proposal_sha256": w1_sha,
        "correction_origin": origin,
        "reviewer_path": None,
        "final_reviewer_run_id": None,
        "semantic_task_fingerprint": sem_fp,
        "task_id": "T1",
        "source_head": HEAD,
        "pre_review_test_path": rcpt1_path_str,
        "pre_review_test_sha256": rcpt1_sha,
        "pre_review_test_state": rcpt1_state,
    }
    evidence: dict[str, Any] = {
        "worker_info": w1_info,
        "reviewer_status": None,
        "reviewer_result": None,
        "reviewer_sha": None,
        "feedback": copy.deepcopy(fb2_data),
        "feedback_sha": fb2_sha,
        "receipt": copy.deepcopy(receipt1),
    }
    admitted = {
        "role": "worker",
        "original_request_id": f"orig-{origin.lower()}-req",
        "task_id": "T1",
        "source_head": HEAD,
        "semantic_task_fingerprint": sem_fp,
        "timeout": 300,
        "run_prefix": cont_prefix,
    }
    orig_terminal_rcpt = {
        "terminal_quota": f"receipt-{origin.lower()}",
        "status": "TERMINAL_RECORDED",
        "request_id": f"orig-{origin.lower()}-req",
    }
    ctx = {
        "continuation_run_prefix": cont_prefix,
        "source_head": HEAD,
        "task": copy.deepcopy(task),
        "sources": copy.deepcopy(sources),
        "admitted_request": admitted,
        "admitted_task_path": task_path,
        "original_terminal_receipt": orig_terminal_rcpt,
        "initial_refs": {},
        "artifacts": artifacts,
        "evidence": evidence,
    }
    returned_state = {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": "T1",
        "source_head": HEAD,
        "final_worker_run_id": w2_id,
        "proposal_sha256": w2_sha,
        "changed_paths": [c["path"] for c in w2_res.get("changes", [])],
        "proposal_path": f".local/two-site-refactor/proposals/{w2_id}/result.json",
        "correction_count": 2,
        "semantic_task_fingerprint": sem_fp,
    }
    return {
        "ctx": ctx,
        "returned_state": returned_state,
        "request": {"role": "worker", "original_request_id": f"orig-{origin.lower()}-req", "task_id": "T1"},
        "admitted": admitted,
        "original_terminal_receipt": orig_terminal_rcpt,
        "task_path": task_path,
        "w1_id": w1_id,
        "w1_sha": w1_sha,
        "w2_id": w2_id,
        "w2_sha": w2_sha,
        "w2_conv_id": w2_st["conversation_id"],
        "w2_changed_paths": returned_state["changed_paths"],
        "w2_status_path": w2_dir / "status.json",
        "fb2_path": fb2_path,
        "fb2_sha": fb2_sha,
        "orig_prefix": orig_prefix,
        "cont_prefix": cont_prefix,
        "origin": origin,
    }


@contextmanager
def _patch_dispatch_env(
    env: dict[str, Any], *, run_tests_fn: Any, run_provider_fn: Any
):
    @contextmanager
    def fake_worker_ctx(r: Path, req: dict[str, Any]):
        with pipeline_lock(r):
            yield env["ctx"]

    def fake_evidence(r: Path, tp: Path, art: dict[str, Any]) -> dict[str, Any]:
        ev = copy.deepcopy(env["ctx"]["evidence"])
        fb_file = env["fb2_path"]
        if fb_file.is_file():
            raw = fb_file.read_bytes()
            ev["feedback_sha"] = hashlib.sha256(raw).hexdigest()
            try:
                ev["feedback"] = json.loads(raw.decode("utf-8"))
            except Exception:
                ev["feedback"] = {"raw": raw.decode("utf-8", errors="replace")}
        return ev

    ret_adm = (env["admitted"], env["task_path"], None)
    patchers = [
        patch.object(dispatch_mod, "validate_quota_continuation_request", return_value=ret_adm),
        patch.object(dispatch_mod, "read_terminal_quota_record", return_value=env["original_terminal_receipt"]),
        patch.object(dispatch_mod, "derive_worker_resume_artifacts", return_value=env["ctx"]["artifacts"]),
        patch.object(dispatch_mod, "execute_worker_two_once", return_value=copy.deepcopy(env["returned_state"])),
        patch.object(post_mod, "worker_correction_continuation_context", side_effect=fake_worker_ctx),
        patch.object(post_mod, "validate_quota_continuation_request", return_value=ret_adm),
        patch.object(post_mod, "read_terminal_quota_record", return_value=env["original_terminal_receipt"]),
        patch.object(post_mod, "derive_worker_resume_artifacts", return_value=env["ctx"]["artifacts"]),
        patch.object(post_mod, "load_worker_correction_evidence", side_effect=fake_evidence),
        patch.object(post_mod, "check_state", return_value=None),
        patch.object(pipeline_mod, "check_state", return_value=None),
        patch.object(pipeline_mod, "run_candidate_tests", side_effect=run_tests_fn),
        patch.object(pipeline_mod, "run_provider_session", side_effect=run_provider_fn),
    ]
    with _patch_load_task():
        for p in patchers:
            p.start()
        try:
            yield
        finally:
            for p in reversed(patchers):
                p.stop()


def _run_positive_proof(root: Path, origin: str) -> None:
    env = _setup_env(root, origin)
    lock_checked = [False]
    verifier_calls: list[str] = []

    def fake_candidate_tests(r: Path, t: dict, w_res: dict, w_sha: str, **kw: Any) -> dict:
        return passed_receipt(w_sha)

    def fake_provider_session(r: Path, tp: Path, role: str, base_id: str, timeout: int, state_check: Any, **kw: Any) -> tuple[str, Path]:
        state_check()
        with pytest.raises(ProposalError, match="another proposal pipeline is already active"):
            with pipeline_lock(r):
                pass
        lock_checked[0] = True
        if role == "verifier":
            verifier_calls.append(base_id)
        resp: dict[str, Any] = {"status": "VERIFIED", "task_override": {"sources": copy.deepcopy(env["ctx"]["sources"])}}
        runner, _ = make_runner(r, [resp])
        out = runner(r, tp, role, base_id, timeout, feedback=kw.get("feedback"), proposal=kw.get("proposal"), cli=kw.get("cli"), test_evidence=kw.get("test_evidence"))
        return base_id, out

    with _patch_dispatch_env(env, run_tests_fn=fake_candidate_tests, run_provider_fn=fake_provider_session):
        final_state = dispatch_quota_continuation_once(root, env["request"])

    assert lock_checked[0] is True
    with pipeline_lock(root):
        pass

    assert final_state["state"] == "READY_FOR_CODEX"
    assert final_state["task_id"] == "T1"
    assert final_state["source_head"] == env["returned_state"]["source_head"]
    assert final_state["semantic_task_fingerprint"] == env["returned_state"]["semantic_task_fingerprint"]
    assert final_state["final_worker_run_id"] == env["w2_id"]
    assert final_state["proposal_sha256"] == env["w2_sha"]
    assert final_state["changed_paths"] == env["w2_changed_paths"]
    assert final_state["proposal_path"] == env["returned_state"]["proposal_path"]
    assert final_state["correction_count"] == 2
    assert final_state["blockers"] == []

    prop_dir = root / ".local/two-site-refactor/proposals"
    assert env["w1_sha"] == json.loads((prop_dir / env["w1_id"] / "status.json").read_text())["result_sha256"]
    assert env["fb2_sha"] == hashlib.sha256(env["fb2_path"].read_bytes()).hexdigest()
    assert env["w2_sha"] == json.loads((prop_dir / env["w2_id"] / "status.json").read_text())["result_sha256"]

    w2_st = json.loads((prop_dir / env["w2_id"] / "status.json").read_text())
    assert w2_st["input_proposal_sha256"] == env["w1_sha"]
    assert w2_st["input_feedback_sha256"] == env["fb2_sha"]
    assert w2_st["input_worker_run_id"] == env["w1_id"]
    assert not (prop_dir / f"{env['orig_prefix']}-reviewer-1").exists()

    expected_rev_id = f"{env['cont_prefix']}-reviewer-2"
    assert final_state["final_reviewer_run_id"] == expected_rev_id
    assert final_state["reviewer_path"] == f".local/two-site-refactor/proposals/{expected_rev_id}/result.json"
    assert final_state["pre_review_test_state"] == "PASSED"

    rcpt_path = root / final_state["pre_review_test_path"]
    assert rcpt_path.is_file()
    rcpt_bytes = rcpt_path.read_bytes()
    assert final_state["pre_review_test_sha256"] == hashlib.sha256(rcpt_bytes).hexdigest()
    rcpt_data = json.loads(rcpt_bytes.decode("utf-8"))
    assert rcpt_data["state"] == "PASSED"
    assert rcpt_data["proposal_sha256"] == env["w2_sha"]

    assert verifier_calls == [expected_rev_id]

    reviewer_dir = prop_dir / expected_rev_id
    assert (reviewer_dir / "status.json").is_file()
    rev_st = json.loads((reviewer_dir / "status.json").read_text())
    assert rev_st["state"] == "VERIFIED"
    assert rev_st["input_proposal_sha256"] == env["w2_sha"]
    assert rev_st["input_worker_run_id"] == env["w2_id"]
    assert rev_st["input_worker_conversation_id"] == env["w2_conv_id"]

    assert not (prop_dir / f"{env['cont_prefix']}-worker-3").exists()
    assert not (root / ".local/two-site-refactor/pipeline" / env["cont_prefix"] / "feedback-correction-3.json").exists()


def _run_negative_drift(root: Path, origin: str, *, drift_target: str) -> None:
    env = _setup_env(root, origin)

    if drift_target == "feedback_sha":
        w2_st_path = env["w2_status_path"]
        st = json.loads(w2_st_path.read_text(encoding="utf-8"))
        st["input_feedback_sha256"] = "0" * 64
        w2_st_path.write_text(json.dumps(st), encoding="utf-8")
    elif drift_target == "feedback_bytes":
        env["fb2_path"].write_bytes(b'{"tampered": true}')
    else:
        raise ValueError(f"unknown drift_target: {drift_target}")

    sandbox_called = [False]
    provider_called = [False]

    def bad_tests(*a: Any, **kw: Any) -> Any:
        sandbox_called[0] = True
        raise AssertionError("run_candidate_tests must not be called")

    def bad_provider(*a: Any, **kw: Any) -> Any:
        provider_called[0] = True
        raise AssertionError("run_provider_session must not be called")

    with _patch_dispatch_env(env, run_tests_fn=bad_tests, run_provider_fn=bad_provider):
        with pytest.raises(ProposalError):
            dispatch_quota_continuation_once(root, env["request"])

    assert not sandbox_called[0]
    assert not provider_called[0]
    with pipeline_lock(root):
        pass

    assert not (root / ".local/two-site-refactor/proposals" / f"{env['cont_prefix']}-reviewer-2").exists()
    assert not (root / ".local/two-site-refactor/proposals" / f"{env['cont_prefix']}-worker-3").exists()


def test_worker_two_outer_test_gate_positive_bootstrap(tmp_path: Path) -> None:
    _run_positive_proof(tmp_path.resolve(), "BOOTSTRAP")


def test_worker_two_outer_test_gate_positive_test_failed(tmp_path: Path) -> None:
    _run_positive_proof(tmp_path.resolve(), "TEST_FAILED")


def test_worker_two_outer_test_gate_negative_feedback_sha_bootstrap(tmp_path: Path) -> None:
    _run_negative_drift(tmp_path.resolve(), "BOOTSTRAP", drift_target="feedback_sha")


def test_worker_two_outer_test_gate_negative_feedback_sha_test_failed(tmp_path: Path) -> None:
    _run_negative_drift(tmp_path.resolve(), "TEST_FAILED", drift_target="feedback_sha")


def test_worker_two_outer_test_gate_negative_feedback_bytes_bootstrap(tmp_path: Path) -> None:
    _run_negative_drift(tmp_path.resolve(), "BOOTSTRAP", drift_target="feedback_bytes")


def test_worker_two_outer_test_gate_negative_feedback_bytes_test_failed(tmp_path: Path) -> None:
    _run_negative_drift(tmp_path.resolve(), "TEST_FAILED", drift_target="feedback_bytes")
