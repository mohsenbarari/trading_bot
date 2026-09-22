from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts.two_site_assignment_scope import (
    AssignmentFact,
    AssignmentScope,
    AssignmentScopeError,
)
from scripts.two_site_proposal_runner import ProposalError
import scripts.two_site_proposal_runner as runner
from scripts.two_site_task_preflight import preflight_task
from tests.test_two_site_context_output_runner_gates import (
    _make_live_session_fixture,
    _write_task,
)
from tests.two_site_preflight_support import make_scoped_task, scope_failure


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    def _fail_popen(*args, **kwargs):
        raise AssertionError("subprocess.Popen is forbidden during preflight")

    monkeypatch.setattr(subprocess, "Popen", _fail_popen)

    import socket

    def _fail_socket(*args, **kwargs):
        raise AssertionError("network socket is forbidden during preflight")

    monkeypatch.setattr(socket, "socket", _fail_socket)


def test_actual_three_defect_fixture_and_ready_transition(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    task = _make_live_session_fixture(root)

    order_file = root / "models/order.py"
    order_file.parent.mkdir(parents=True, exist_ok=True)
    order_file.write_text("class Order:\n    pass\n", encoding="utf-8")

    task["goal"] = task["goal"] + " Persist Order into database."
    task_path = _write_task(root, "task.json", task)

    scope = AssignmentScope(
        "ASG-1",
        "P1-00",
        ("tests/live_helper.py",),
        (),
        "2099-01-01T00:00:00Z",
        "a" * 64,
    )
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_ID", "ASG-1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SHA256", "a" * 64)
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.load_expected_assignment_scope",
        lambda r, aid, sha: scope,
    )

    res_blocked = preflight_task(root, task_path)
    assert res_blocked["schema_version"] == 1
    assert res_blocked["state"] == "TASK_BLOCKED"
    assert res_blocked["task_id"] == "task-live-session-closure"

    issue_codes = {iss["code"] for iss in res_blocked["issues"]}
    assert "MODEL_CONTEXT_MISSING" in issue_codes
    assert "SESSION_CONTEXT_MISSING" in issue_codes
    assert "TASK_ASSIGNMENT_TOKEN_MISSING" in issue_codes

    model_issue = next(
        iss for iss in res_blocked["issues"] if iss["code"] == "MODEL_CONTEXT_MISSING"
    )
    assert model_issue.get("path") == "models/order.py"

    # Fix all 3 defects: append token to goal and add context files
    task["goal"] = task["goal"] + " ASG-1"
    task["context_files"] = [
        "tests/live_helper.py",
        "models/order.py",
        "api/live.py",
        "core/lookup.py",
    ]
    _write_task(root, "task.json", task)

    # Stable repeated TASK_READY
    for _ in range(2):
        res_ready = preflight_task(root, task_path)
        assert res_ready["schema_version"] == 1
        assert res_ready["state"] == "TASK_READY"
        assert res_ready["task_id"] == "task-live-session-closure"
        assert res_ready["issues"] == []

    original_check = runner.check(root, task_path)
    assert original_check["state"] == "TASK_READY"


def test_spies_and_single_execution_call_identities(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    calls = {"snapshot": 0, "notes": 0, "enrich": 0, "validator": 0}
    captured: dict[str, object] = {}

    real_snapshot = runner._load_task_snapshot

    def spy_snapshot(*args, **kwargs):
        calls["snapshot"] += 1
        res = real_snapshot(*args, **kwargs)
        captured["snapshot_res"] = res
        return res

    real_notes = runner.task_preparation_notes

    def spy_notes(*args, **kwargs):
        calls["notes"] += 1
        captured["notes_args"] = args
        res = real_notes(*args, **kwargs)
        captured["notes_res"] = res
        return res

    real_enrich = runner.enrich_repository_dependency_context

    def spy_enrich(*args, **kwargs):
        calls["enrich"] += 1
        captured["enrich_args"] = args
        res = real_enrich(*args, **kwargs)
        captured["enrich_res"] = res
        return res

    real_validator = runner.validate_task_readiness

    def spy_validator(*args, **kwargs):
        calls["validator"] += 1
        captured["validator_args"] = args
        return real_validator(*args, **kwargs)

    monkeypatch.setattr(runner, "_load_task_snapshot", spy_snapshot)
    monkeypatch.setattr(runner, "task_preparation_notes", spy_notes)
    monkeypatch.setattr(runner, "enrich_repository_dependency_context", spy_enrich)
    monkeypatch.setattr(runner, "validate_task_readiness", spy_validator)

    res = preflight_task(root, task_path)
    assert res["state"] == "TASK_READY"

    assert calls["snapshot"] == 1
    task_obj, sources_obj = captured["snapshot_res"]

    assert calls["notes"] == 1
    assert captured["notes_args"][0] is task_obj
    assert captured["notes_args"][1] is sources_obj
    notes_obj = captured["notes_res"]

    assert calls["enrich"] == 1
    assert captured["enrich_args"][1] is task_obj
    assert captured["enrich_args"][2] is sources_obj
    assert captured["enrich_args"][3] is notes_obj
    enriched_obj = captured["enrich_res"]

    assert calls["validator"] == 1
    assert captured["validator_args"][0] is enriched_obj


def test_missing_token_then_hardlink_stops_before_b(tmp_path, monkeypatch):
    root, task_path, task = make_scoped_task(tmp_path, monkeypatch)
    task["goal"] = "safe goal without token"

    hard_a = root / "hard_a.py"
    os.link(root / "a.py", hard_a)
    task["context_files"] = ["hard_a.py", "b.py"]
    _write_task(root, "task.json", task)

    real_bounded = runner.bounded_file_bytes
    bounded_paths: list[str] = []

    def _spy_bounded(r, p, max_b, desc, failure_callback=None):
        bounded_paths.append(str(p))
        return real_bounded(r, p, max_b, desc, failure_callback=failure_callback)

    monkeypatch.setattr(runner, "bounded_file_bytes", _spy_bounded)

    def _trap(*args, **kwargs):
        raise AssertionError("preparation notes or enrichment must not be called")

    monkeypatch.setattr(runner, "task_preparation_notes", _trap)
    monkeypatch.setattr(runner, "enrich_repository_dependency_context", _trap)

    res = preflight_task(root, task_path)
    assert res["state"] == "TASK_BLOCKED"
    assert "task_id" not in res
    assert res["issues"] == [{
        "code": "TASK_FILE_HARDLINKED",
        "path": "hard_a.py",
        "detail": "is hardlinked and cannot be trusted",
    }]
    assert not any("b.py" in p for p in bounded_paths)


@pytest.mark.parametrize(
    ("setup_kind", "expected_code", "expected_path"),
    [
        ("malformed_json", "TASK_SCHEMA_INVALID", None),
        ("symlink", "TASK_FILE_SYMLINK", "sym.py"),
        ("traversal", "TASK_PATH_OUTSIDE_SCOPE", None),
        ("oversize", "TASK_BUDGET_EXCEEDED", "task.json"),
        ("outside_root", "TASK_FILE_OUTSIDE_ROOT", None),
    ],
)
def test_fatal_singleton_fail_closed_cases(
    tmp_path, monkeypatch, setup_kind, expected_code, expected_path
):
    root, task_path, task = make_scoped_task(tmp_path / "repo", monkeypatch)

    def _trap(*args, **kwargs):
        raise AssertionError("preparation or enrichment trap triggered")

    monkeypatch.setattr(runner, "task_preparation_notes", _trap)
    monkeypatch.setattr(runner, "enrich_repository_dependency_context", _trap)

    if setup_kind == "malformed_json":
        task_path.write_text("{broken json", encoding="utf-8")
        target_path = task_path
    elif setup_kind == "symlink":
        sym = root / "sym.py"
        sym.symlink_to(root / "a.py")
        task["context_files"] = ["sym.py"]
        target_path = _write_task(root, "task.json", task)
    elif setup_kind == "traversal":
        task["context_files"] = ["../outside.py"]
        target_path = _write_task(root, "task.json", task)
    elif setup_kind == "oversize":
        oversize_bytes = getattr(runner, "MAX_CONTEXT", 262144) + 1024
        task_path.write_text("x" * oversize_bytes, encoding="utf-8")
        target_path = task_path
    elif setup_kind == "outside_root":
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps(task), encoding="utf-8")
        target_path = outside
    else:
        raise ValueError(f"unknown setup_kind: {setup_kind}")

    res = preflight_task(root, target_path)
    assert res["state"] == "TASK_BLOCKED"
    assert "task_id" not in res
    assert len(res["issues"]) == 1
    assert res["issues"][0]["code"] == expected_code
    assert res["issues"][0].get("path") == expected_path


def test_typed_scope_expired(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "validate_assignment_scope",
        lambda r, t: (_ for _ in ()).throw(scope_failure("ASSIGNMENT_EXPIRED")),
    )
    res = preflight_task(root, task_path)
    assert res["state"] == "TASK_BLOCKED"
    assert res["issues"] == [{
        "code": "ASSIGNMENT_EXPIRED",
        "detail": "assignment has expired",
    }]


def test_typed_scope_long_path_constructor_drops_path(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)
    fact = AssignmentFact("TASK_PATH_OUTSIDE_SCOPE", "x" * 300)
    cause = AssignmentScopeError("outside scope", fact=fact)
    err = ProposalError("outside scope")
    err.__cause__ = cause
    monkeypatch.setattr(
        runner,
        "validate_assignment_scope",
        lambda r, t: (_ for _ in ()).throw(err),
    )
    res = preflight_task(root, task_path)
    assert res["state"] == "TASK_BLOCKED"
    assert res["issues"] == [{
        "code": "TASK_PATH_OUTSIDE_SCOPE",
        "detail": "task path is outside assignment scope",
    }]
    assert "path" not in res["issues"][0]


def test_factless_and_untyped_exceptions_re_raised_same_identity(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    factless_err = ProposalError("factless")
    monkeypatch.setattr(
        runner,
        "_load_task_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(factless_err),
    )
    with pytest.raises(ProposalError) as exc_info:
        preflight_task(root, task_path)
    assert exc_info.value is factless_err

    scope_no_fact_err = ProposalError("scope without fact")
    scope_no_fact_err.__cause__ = AssignmentScopeError("no fact", fact=None)
    monkeypatch.setattr(
        runner,
        "_load_task_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(scope_no_fact_err),
    )
    with pytest.raises(ProposalError) as exc_info2:
        preflight_task(root, task_path)
    assert exc_info2.value is scope_no_fact_err

    runtime_err = RuntimeError("unexpected runtime")
    monkeypatch.setattr(
        runner,
        "_load_task_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(runtime_err),
    )
    with pytest.raises(RuntimeError) as exc_info3:
        preflight_task(root, task_path)
    assert exc_info3.value is runtime_err


def test_legacy_validator_not_called_on_issues(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_collect_model_readiness_facts",
        lambda dep: [{"code": "MODEL_CONTEXT_INVALID", "path": None}],
    )
    spy_validator = runner.validate_task_readiness
    validator_called = []

    def _spy_val(*args, **kwargs):
        validator_called.append(args)
        return spy_validator(*args, **kwargs)

    monkeypatch.setattr(runner, "validate_task_readiness", _spy_val)

    res = preflight_task(root, task_path)
    assert res["state"] == "TASK_BLOCKED"
    assert len(validator_called) == 0


def test_controlled_metadata_cap16_dedup_order_and_bounds(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    fake_facts = [
        {"code": "MODEL_CONTEXT_MISSING", "path": f"models/m{i:02d}.py"}
        for i in range(20)
    ]
    fake_facts.extend(fake_facts[:5])
    fake_facts.append({"code": "MODEL_CONTEXT_INVALID", "path": None})

    monkeypatch.setattr(
        runner,
        "_collect_model_readiness_facts",
        lambda dep: copy.deepcopy(fake_facts),
    )

    res = preflight_task(root, task_path)
    assert res["state"] == "TASK_BLOCKED"
    issues = res["issues"]
    assert len(issues) == 16

    keys = [(iss["code"], iss.get("path")) for iss in issues]
    assert len(set(keys)) == 16

    for idx in range(len(issues) - 1):
        k1 = (
            issues[idx]["code"],
            0 if issues[idx].get("path") is None else 1,
            issues[idx].get("path") or "",
        )
        k2 = (
            issues[idx + 1]["code"],
            0 if issues[idx + 1].get("path") is None else 1,
            issues[idx + 1].get("path") or "",
        )
        assert k1 <= k2

    raw_bytes = json.dumps(res, ensure_ascii=False).encode("utf-8")
    assert len(raw_bytes) <= 16384

    issues[0]["code"] = "TAMPERED"
    res2 = preflight_task(root, task_path)
    assert res2["issues"][0]["code"] != "TAMPERED"


def test_filesystem_unchanged_across_preflight(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    def _snapshot():
        return {
            str(p.relative_to(root)): (p.is_file(), p.stat().st_mtime_ns)
            for p in root.rglob("*")
            if p.exists()
        }

    before = _snapshot()
    res = preflight_task(root, task_path)
    assert res["state"] == "TASK_READY"
    after = _snapshot()
    assert before == after
