"""Tests for required-test admission wiring and direct post-worker witness."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import unittest.mock

import pytest

from scripts.two_site_pre_review_receipt import EXPECTED_RECEIPT_KEYS
import scripts.two_site_proposal_pipeline as pipeline_mod
from scripts.two_site_proposal_pipeline import (
    _run_post_worker_remainder,
    check_state,
    load_task,
    validate_run,
)
from scripts.two_site_proposal_runner import (
    MODEL,
    WORKER_MODEL,
    ProposalError,
    file_sha256,
    write_json,
)

HEAD = "a" * 40
BRANCH = "refactor/two-site-architecture"
TASK_ID = "T-PRE-REVIEW-GATE"
PROTECTED_REFS = (
    "refs/heads/main",
    "refs/remotes/origin/main",
    "refs/heads/candidate/wa-ir-standby-v1",
)


def fake_git(_root: Path, *args: str) -> str:
    cmd = " ".join(args)
    if "branch --show-current" in cmd:
        return BRANCH
    if "rev-parse HEAD" in cmd:
        return HEAD
    if "status --porcelain" in cmd:
        return ""
    return "b" * 40 if "show-ref" in cmd else ""


@contextmanager
def isolated_assignment_scope_env():
    scope_keys = (
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
        "TWO_SITE_ASSIGNMENT_ID",
        "TWO_SITE_ASSIGNMENT_SHA256",
    )
    saved = {k: os.environ[k] for k in scope_keys if k in os.environ}
    for k in scope_keys:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k in scope_keys:
            os.environ.pop(k, None)
        os.environ.update(saved)


def setup_test_workspace(
    tmp_path: Path,
    *,
    test_selectors: list[str] | None = None,
) -> tuple[Path, dict, dict]:
    root = tmp_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    src_path = root / "src.py"
    src_path.write_text("# orig\n")
    if test_selectors is None:
        selectors = [
            "tests/test_existing.py",
            "tests/test_required.py",
        ]
    else:
        selectors = list(test_selectors)
    task = {
        "task_id": TASK_ID,
        "goal": f"Goal for {TASK_ID}",
        "context_files": ["src.py"],
        "allowed_files": ["src.py", "tests/test_required.py"],
        "requirements": ["R1: implement required test gate"],
        "test_selectors": selectors,
    }
    task_path = root / "task.json"
    write_json(task_path, task)
    with isolated_assignment_scope_env():
        loaded_task, loaded_sources = load_task(root, task_path)
    return root, loaded_task, loaded_sources


from tests.two_site_generation_fixture import seal_completion_fixture


def make_role_runner(
    root: Path,
    expected_head: str,
    task: dict,
    sources: dict,
    worker_proposals: dict[str, list[dict[str, Any]]],
    call_log: list[tuple[str, str]],
):
    def mock_run(
        _r: Path,
        _t: Path,
        role: str,
        run_id: str,
        timeout: int,
        feedback: Path | None = None,
        proposal: Path | None = None,
        cli: Path | None = None,
        test_evidence: Path | None = None,
        **_kwargs: Any,
    ) -> Path:
        call_log.append((role, run_id))
        out = root / ".local/two-site-refactor/proposals" / run_id
        out.mkdir(parents=True, exist_ok=True)
        if role == "worker":
            st_val = "PROPOSAL_READY"
            changes = worker_proposals.get(
                run_id,
                [{"path": "src.py", "original_sha256": sources["src.py"]["sha256"], "content": f"# c {run_id}\n"}],
            )
            result = {
                "status": st_val,
                "summary": f"worker {run_id}",
                "covered_requirements": [1],
                "findings": [],
                "changes": changes,
            }
        else:
            st_val = "VERIFIED"
            result = {
                "status": st_val,
                "summary": f"verifier {run_id}",
                "covered_requirements": [1],
                "findings": [],
                "changes": [],
            }
        write_json(out / "result.json", result)
        res_sha = file_sha256(out / "result.json")

        snapshot: dict[str, Any] = {
            "source_head": expected_head,
            "task": task,
            "sources": sources,
        }
        if proposal is not None:
            snapshot["proposal"] = proposal.read_text()
        if test_evidence is not None:
            snapshot["test_evidence"] = json.loads(Path(test_evidence).read_text())
        write_json(out / "task.json", snapshot)

        st: dict[str, Any] = {
            "run_id": run_id,
            "role": role,
            "head": expected_head,
            "model": WORKER_MODEL if role == "worker" else MODEL,
            "result_sha256": res_sha,
            "conversation_id": f"conv-{run_id}",
            "state": st_val,
        }
        if proposal is not None:
            pst = json.loads((proposal.parent / "status.json").read_text())
            st.update(
                input_worker_run_id=pst["run_id"],
                input_worker_conversation_id=pst["conversation_id"],
                input_proposal_sha256=pst["result_sha256"],
            )
        if feedback is not None:
            snapshot["feedback"] = json.loads(feedback.read_text())
            write_json(out / "task.json", snapshot)
            st["input_feedback_sha256"] = file_sha256(feedback)
        if test_evidence is not None:
            st["input_test_evidence_sha256"] = file_sha256(test_evidence)
        write_json(out / "status.json", st)
        seal_completion_fixture(root, out)
        return out

    return mock_run


def _execute_callback_witness(
    root: Path,
    task: dict,
    sources: dict,
    run_prefix: str,
    worker_proposals: dict[str, list[dict[str, Any]]],
    fake_sandbox_dispatch: Any,
) -> tuple[dict[str, Any], list[tuple[str, str]], set[str], Path]:
    call_log: list[tuple[str, str]] = []
    mock_run = make_role_runner(root, HEAD, task, sources, worker_proposals, call_log)
    initial_refs = {ref: "b" * 40 for ref in PROTECTED_REFS}
    task_candidate = root / "task.json"
    evidence_dir = root / ".local/two-site-refactor/pipeline" / run_prefix
    evidence_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "state": "TASK_BLOCKED",
        "task_id": TASK_ID,
        "source_head": HEAD,
        "final_worker_run_id": None,
        "final_reviewer_run_id": None,
        "proposal_sha256": None,
        "changed_paths": [],
        "proposal_path": None,
        "reviewer_path": None,
        "correction_count": 0,
        "blockers": [],
        "pre_review_test_path": None,
        "pre_review_test_sha256": None,
        "pre_review_test_state": None,
        "semantic_task_fingerprint": "0" * 64,
    }

    def state_check() -> None:
        check_state(root, HEAD, initial_refs, task_candidate, task, sources)

    with isolated_assignment_scope_env():
        with unittest.mock.patch("scripts.two_site_proposal_pipeline.git", side_effect=fake_git):
            with unittest.mock.patch("scripts.two_site_proposal_pipeline.runner_run", side_effect=mock_run):
                with unittest.mock.patch("scripts.two_site_pre_review_tests.source_files", return_value=["src.py", "tests/test_existing.py"]):
                    with unittest.mock.patch("scripts.two_site_agent_sandbox.run_tests", side_effect=fake_sandbox_dispatch):
                        w0_id = f"{run_prefix}-worker-0"
                        w0_dir = mock_run(root, task_candidate, "worker", w0_id, 300)
                        seen_convs: set[str] = set()
                        w0_res, w0_st, w0_sha, w0_txt = validate_run(
                            root, w0_dir, "worker", w0_id, HEAD, task, sources, seen_convs
                        )
                        current_worker = (w0_id, w0_sha, w0_txt, w0_st["conversation_id"])
                        state.update(
                            final_worker_run_id=w0_id,
                            proposal_sha256=w0_sha,
                            changed_paths=[c["path"] for c in w0_res["changes"]],
                            proposal_path=(w0_dir / "result.json").relative_to(root).as_posix(),
                        )
                        _run_post_worker_remainder(
                            root=root,
                            task_candidate=task_candidate,
                            task=task,
                            sources=sources,
                            initial_head=HEAD,
                            initial_refs=initial_refs,
                            run_prefix=run_prefix,
                            timeout=300,
                            cli=None,
                            state=state,
                            evidence_dir=evidence_dir,
                            state_check=state_check,
                            worker_dir=w0_dir,
                            worker_res=w0_res,
                            current_worker=current_worker,
                            seen_convs=seen_convs,
                            correction_count=0,
                            seen_norm_findings=set(),
                            seen_test_signatures=set(),
                            seen_bootstrap_signatures=set(),
                        )

    return state, call_log, seen_convs, evidence_dir


def _positive_witness_flow(tmp_path: Path, run_prefix: str) -> None:
    root, task, sources = setup_test_workspace(tmp_path)
    src_sha = sources["src.py"]["sha256"]

    expected_roles = [
        ("worker", f"{run_prefix}-worker-0"),
        ("worker", f"{run_prefix}-worker-1"),
        ("verifier", f"{run_prefix}-reviewer-1"),
    ]
    expected_initial_selectors = ["tests/test_existing.py"]
    expected_initial_unsupported = ["tests/test_required.py"]
    expected_final_selectors = ["tests/test_existing.py", "tests/test_required.py"]
    expected_final_unsupported: list[str] = []
    expected_correction_count = 1

    worker_proposals = {
        f"{run_prefix}-worker-0": [
            {"path": "src.py", "original_sha256": src_sha, "content": "# worker0\n"},
        ],
        f"{run_prefix}-worker-1": [
            {"path": "src.py", "original_sha256": src_sha, "content": "# worker1\n"},
            {"path": "tests/test_required.py", "original_sha256": None, "content": "def test_required(): pass\n"},
        ],
    }

    def fake_sandbox(_worktree: Path, _selectors: list[str], **_kw: Any) -> dict[str, Any]:
        return {
            "returncode": 0,
            "stdout": "tests/test_existing.py PASSED\n",
            "stderr": "",
            "snapshot_files": 1,
            "snapshot_bytes": 100,
            "isolation": "sandboxed",
        }

    state, call_log, seen_convs, evidence_dir = _execute_callback_witness(
        root, task, sources, run_prefix, worker_proposals, fake_sandbox
    )

    assert call_log == expected_roles
    assert state["correction_count"] == expected_correction_count
    assert state["state"] == "READY_FOR_CODEX"
    assert state["blockers"] == []
    assert state["final_reviewer_run_id"] == f"{run_prefix}-reviewer-1"

    receipt0_file = evidence_dir / "pre-review-tests-0.json"
    assert receipt0_file.is_file()
    receipt0 = json.loads(receipt0_file.read_text())
    assert set(receipt0.keys()) == EXPECTED_RECEIPT_KEYS
    assert len(receipt0) == 10
    assert receipt0["state"] == "PASSED"
    assert receipt0["returncode"] == 0
    assert receipt0["selectors"] == expected_initial_selectors
    assert receipt0["unsupported_specs"] == expected_initial_unsupported

    receipt1_file = evidence_dir / "pre-review-tests-1.json"
    assert receipt1_file.is_file()
    receipt1 = json.loads(receipt1_file.read_text())
    assert set(receipt1.keys()) == EXPECTED_RECEIPT_KEYS
    assert len(receipt1) == 10
    assert receipt1["state"] == "PASSED"
    assert receipt1["returncode"] == 0
    assert receipt1["selectors"] == expected_final_selectors
    assert receipt1["unsupported_specs"] == expected_final_unsupported

    w0_sha = file_sha256(root / ".local/two-site-refactor/proposals" / f"{run_prefix}-worker-0" / "result.json")
    fb_file = evidence_dir / "feedback-correction-1.json"
    assert fb_file.is_file()
    fb_data = json.loads(fb_file.read_text())
    assert fb_data["reviewer"] == "test-gate"
    assert fb_data["reviewed_proposal_sha256"] == w0_sha
    assert file_sha256(fb_file) == hashlib.sha256(fb_file.read_bytes()).hexdigest()

    assert len(seen_convs) == 3
    assert seen_convs == {f"conv-{r[1]}" for r in expected_roles}

    rev_status = json.loads((root / ".local/two-site-refactor/proposals" / f"{run_prefix}-reviewer-1" / "status.json").read_text())
    w1_sha = file_sha256(root / ".local/two-site-refactor/proposals" / f"{run_prefix}-worker-1" / "result.json")
    r1_sha = file_sha256(receipt1_file)
    assert rev_status["input_proposal_sha256"] == w1_sha
    assert rev_status["input_test_evidence_sha256"] == r1_sha
    assert rev_status["state"] == "VERIFIED"

    assert (root / "src.py").read_text() == "# orig\n"
    assert not (root / "tests/test_required.py").exists()


def test_actual_missing_required_receipt_corrects_before_reviewer(tmp_path: Path) -> None:
    _positive_witness_flow(tmp_path, "run-pos")


def test_same_canonical_missing_root_stops_before_second_feedback(tmp_path: Path) -> None:
    run_prefix = "run-same-root"
    root, task, sources = setup_test_workspace(
        tmp_path,
        test_selectors=["tests/test_existing.py", "python3 -m pytest tests.test_required"],
    )
    src_sha = sources["src.py"]["sha256"]

    worker_proposals = {
        f"{run_prefix}-worker-0": [
            {"path": "src.py", "original_sha256": src_sha, "content": "# worker0 body\n"},
        ],
        f"{run_prefix}-worker-1": [
            {"path": "src.py", "original_sha256": src_sha, "content": "# worker1 distinct body\n"},
        ],
    }

    def fake_sandbox(_worktree: Path, _selectors: list[str], **_kw: Any) -> dict[str, Any]:
        return {
            "returncode": 0,
            "stdout": "tests/test_existing.py PASSED\n",
            "stderr": "",
            "snapshot_files": 1,
            "snapshot_bytes": 100,
            "isolation": "sandboxed",
        }

    with pytest.raises(ProposalError, match="repeated test bootstrap failure signature"):
        _execute_callback_witness(root, task, sources, run_prefix, worker_proposals, fake_sandbox)

    evidence_dir = root / ".local/two-site-refactor/pipeline" / run_prefix
    r0_file = evidence_dir / "pre-review-tests-0.json"
    r1_file = evidence_dir / "pre-review-tests-1.json"
    assert r0_file.is_file()
    assert r1_file.is_file()
    r0_data = json.loads(r0_file.read_text())
    r1_data = json.loads(r1_file.read_text())
    assert r0_data["unsupported_specs"] == ["python3 -m pytest tests.test_required"]
    assert r1_data["unsupported_specs"] == ["python3 -m pytest tests.test_required"]
    assert r0_data["state"] == "PASSED"
    assert r1_data["state"] == "PASSED"

    assert (evidence_dir / "feedback-correction-1.json").is_file()
    assert not (evidence_dir / "feedback-correction-2.json").exists()

    proposals_dir = root / ".local/two-site-refactor/proposals"
    assert (proposals_dir / f"{run_prefix}-worker-0").is_dir()
    assert (proposals_dir / f"{run_prefix}-worker-1").is_dir()
    assert not (proposals_dir / f"{run_prefix}-worker-2").exists()
    assert not (proposals_dir / f"{run_prefix}-reviewer-0").exists()
    assert not (proposals_dir / f"{run_prefix}-reviewer-1").exists()


def test_removed_admission_guard_fails_same_independent_witness(tmp_path: Path) -> None:
    _positive_witness_flow(tmp_path / "first", "run-guard-pass")

    with unittest.mock.patch.object(pipeline_mod, "required_selector_gate_feedback", return_value=None):
        with pytest.raises(AssertionError):
            _positive_witness_flow(tmp_path / "second", "run-guard-fail")

    _positive_witness_flow(tmp_path / "third", "run-guard-restored")


def test_legacy_and_failed_receipts_keep_existing_admission(tmp_path: Path) -> None:
    table_scenarios = [
        {
            "case": "legacy_prose",
            "run_prefix": "run-legacy-prose",
            "selectors": ["tests/test_existing.py", "please run unit tests"],
            "w0_changes": lambda sha: [{"path": "src.py", "original_sha256": sha, "content": "# w0 prose\n"}],
            "w1_changes": None,
            "sandbox_rc": [0],
            "sandbox_stdout": ["tests/test_existing.py PASSED\n"],
            "expected_calls": [("worker", "run-legacy-prose-worker-0"), ("verifier", "run-legacy-prose-reviewer-0")],
            "expected_correction_count": 0,
            "expected_state": "READY_FOR_CODEX",
            "expected_unsupported": ["please run unit tests"],
            "expected_reviewer_role": "test-gate",
        },
        {
            "case": "mandatory_plus_failed",
            "run_prefix": "run-mand-fail",
            "selectors": ["tests/test_existing.py", "tests/test_required.py"],
            "w0_changes": lambda sha: [{"path": "src.py", "original_sha256": sha, "content": "# w0 fail\n"}],
            "w1_changes": lambda sha: [
                {"path": "src.py", "original_sha256": sha, "content": "# w1 fix\n"},
                {"path": "tests/test_required.py", "original_sha256": None, "content": "def test_required(): pass\n"},
            ],
            "sandbox_rc": [1, 0],
            "sandbox_stdout": ["tests/test_existing.py FAILED\n", "tests/test_existing.py PASSED\ntests/test_required.py PASSED\n"],
            "expected_calls": [
                ("worker", "run-mand-fail-worker-0"),
                ("worker", "run-mand-fail-worker-1"),
                ("verifier", "run-mand-fail-reviewer-1"),
            ],
            "expected_correction_count": 1,
            "expected_state": "READY_FOR_CODEX",
            "expected_unsupported": ["tests/test_required.py"],
            "expected_reviewer_role": "test-gate",
        },
        {
            "case": "pytest_failure_no_unsupported",
            "run_prefix": "run-test-fail",
            "selectors": ["tests/test_existing.py"],
            "w0_changes": lambda sha: [{"path": "src.py", "original_sha256": sha, "content": "# w0 fail only\n"}],
            "w1_changes": lambda sha: [{"path": "src.py", "original_sha256": sha, "content": "# w1 fix only\n"}],
            "sandbox_rc": [1, 0],
            "sandbox_stdout": [
                "FAILED tests/test_existing.py::test_fn - AssertionError: boom\n> assert 1 == 2\nE AssertionError: boom\n",
                "tests/test_existing.py PASSED\n",
            ],
            "expected_calls": [
                ("worker", "run-test-fail-worker-0"),
                ("worker", "run-test-fail-worker-1"),
                ("verifier", "run-test-fail-reviewer-1"),
            ],
            "expected_correction_count": 1,
            "expected_state": "READY_FOR_CODEX",
            "expected_unsupported": [],
            "expected_reviewer_role": "test-gate",
        },
    ]

    for item in table_scenarios:
        sub_root = tmp_path / item["case"]
        sub_root.mkdir(parents=True, exist_ok=True)
        root, task, sources = setup_test_workspace(sub_root, test_selectors=item["selectors"])
        src_sha = sources["src.py"]["sha256"]
        run_prefix = item["run_prefix"]

        worker_proposals: dict[str, list[dict[str, Any]]] = {
            f"{run_prefix}-worker-0": item["w0_changes"](src_sha),
        }
        if item["w1_changes"] is not None:
            worker_proposals[f"{run_prefix}-worker-1"] = item["w1_changes"](src_sha)

        sandbox_call_idx = 0
        rc_list = item["sandbox_rc"]
        stdout_list = item["sandbox_stdout"]

        def fake_sandbox(_worktree: Path, _selectors: list[str], **_kw: Any) -> dict[str, Any]:
            nonlocal sandbox_call_idx
            idx = min(sandbox_call_idx, len(rc_list) - 1)
            rc = rc_list[idx]
            out = stdout_list[idx]
            sandbox_call_idx += 1
            return {
                "returncode": rc,
                "stdout": out,
                "stderr": "",
                "snapshot_files": 1,
                "snapshot_bytes": 100,
                "isolation": "sandboxed",
            }

        state, call_log, _seen_convs, evidence_dir = _execute_callback_witness(
            root, task, sources, run_prefix, worker_proposals, fake_sandbox
        )

        assert call_log == item["expected_calls"]
        assert state["correction_count"] == item["expected_correction_count"]
        assert state["state"] == item["expected_state"]
        assert state["blockers"] == []

        r0_file = evidence_dir / "pre-review-tests-0.json"
        assert r0_file.is_file()
        r0 = json.loads(r0_file.read_text())
        assert set(r0.keys()) == EXPECTED_RECEIPT_KEYS
        assert len(r0) == 10
        assert r0["unsupported_specs"] == item["expected_unsupported"]

        if item["expected_correction_count"] > 0:
            fb_file = evidence_dir / "feedback-correction-1.json"
            assert fb_file.is_file()
            fb = json.loads(fb_file.read_text())
            assert fb["reviewer"] == item["expected_reviewer_role"]
            if item["case"] == "mandatory_plus_failed":
                assert "Unresolved required test selectors" in fb["findings"][0]
            elif item["case"] == "pytest_failure_no_unsupported":
                assert "Pre-review candidate tests failed with exit code 1" in fb["findings"][0]
