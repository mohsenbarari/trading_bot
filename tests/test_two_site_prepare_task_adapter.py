from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.two_site_proposal_runner as runner
from scripts.two_site_task_preflight import prepare_task, preflight_task
from tests.two_site_preflight_support import make_scoped_task


def test_prepare_task_ready_parity_and_persian_zwnj(tmp_path, monkeypatch):
    root, task_path, task = make_scoped_task(tmp_path, monkeypatch)
    persian_comment = "# Persian ZWNJ: \u200c test\n"
    (root / "a.py").write_text(persian_comment, encoding="utf-8")

    check_res = runner.check(root, task_path)
    prep_res = prepare_task(root, task_path)

    assert prep_res == check_res
    assert set(prep_res.keys()) == {
        "state",
        "task_id",
        "source_file_count",
        "byte_count",
        "preparation_notes",
    }
    assert prep_res["state"] == "TASK_READY"
    assert prep_res["task_id"] == "T1"
    assert prep_res["source_file_count"] == len(task["context_files"])
    assert prep_res["byte_count"] == check_res["byte_count"]
    assert prep_res["byte_count"] == 315

    preflight_res = preflight_task(root, task_path)
    assert preflight_res["state"] == "TASK_READY"
    assert preflight_res["schema_version"] == 1
    assert preflight_res["task_id"] == "T1"
    assert preflight_res["issues"] == []


def test_single_call_same_object_identities_and_no_second_read(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    calls = {"snapshot": 0, "notes": 0, "enrich": 0, "validator": 0, "check": 0}
    recorded: dict[str, object] = {}

    real_snapshot = runner._load_task_snapshot

    def spy_snapshot(*args, **kwargs):
        calls["snapshot"] += 1
        res = real_snapshot(*args, **kwargs)
        recorded["snapshot_task"], recorded["snapshot_sources"] = res
        return res

    real_notes = runner.task_preparation_notes

    def spy_notes(*args, **kwargs):
        calls["notes"] += 1
        recorded["notes_args"] = args
        res = real_notes(*args, **kwargs)
        recorded["notes_res"] = res
        return res

    real_enrich = runner.enrich_repository_dependency_context

    def spy_enrich(*args, **kwargs):
        calls["enrich"] += 1
        recorded["enrich_args"] = args
        res = real_enrich(*args, **kwargs)
        recorded["enrich_res"] = res
        return res

    real_validator = runner.validate_task_readiness

    def spy_validator(*args, **kwargs):
        calls["validator"] += 1
        recorded["validator_args"] = args
        return real_validator(*args, **kwargs)

    def trap_check(*args, **kwargs):
        calls["check"] += 1
        raise AssertionError("runner.check must not be called")

    monkeypatch.setattr(runner, "_load_task_snapshot", spy_snapshot)
    monkeypatch.setattr(runner, "task_preparation_notes", spy_notes)
    monkeypatch.setattr(runner, "enrich_repository_dependency_context", spy_enrich)
    monkeypatch.setattr(runner, "validate_task_readiness", spy_validator)
    monkeypatch.setattr(runner, "check", trap_check)

    res = prepare_task(root, task_path)
    assert res["state"] == "TASK_READY"

    assert calls["snapshot"] == 1
    assert calls["notes"] == 1
    assert calls["enrich"] == 1
    assert calls["validator"] == 1
    assert calls["check"] == 0

    assert recorded["notes_args"][0] is recorded["snapshot_task"]
    assert recorded["notes_args"][1] is recorded["snapshot_sources"]
    assert recorded["enrich_args"][1] is recorded["snapshot_task"]
    assert recorded["enrich_args"][2] is recorded["snapshot_sources"]
    assert recorded["enrich_args"][3] is recorded["notes_res"]
    assert recorded["validator_args"][0] is recorded["enrich_res"]
    assert res["preparation_notes"] is recorded["enrich_res"]


@pytest.mark.parametrize(
    "case_kind",
    ["empty_requirements", "missing_token", "path_traversal", "hardlink"],
)
def test_real_blocked_cases_parity_and_immutability(tmp_path, monkeypatch, case_kind):
    import socket

    def _fail_socket(*args, **kwargs):
        raise AssertionError("network socket is forbidden during prepare_task")

    monkeypatch.setattr(socket, "socket", _fail_socket)

    root, task_path, task = make_scoped_task(tmp_path / case_kind, monkeypatch)

    if case_kind == "empty_requirements":
        task["requirements"] = []
        task_path.write_text(json.dumps(task), encoding="utf-8")
    elif case_kind == "missing_token":
        task["goal"] = "safe goal without token"
        task_path.write_text(json.dumps(task), encoding="utf-8")
    elif case_kind == "path_traversal":
        task["context_files"] = ["../outside.py"]
        task_path.write_text(json.dumps(task), encoding="utf-8")
    elif case_kind == "hardlink":
        hard_a = root / "hard_a.py"
        os.link(root / "a.py", hard_a)
        task["context_files"] = ["hard_a.py", "b.py"]
        task_path.write_text(json.dumps(task), encoding="utf-8")

    def _snapshot_tree():
        return {
            str(p.relative_to(root)): (p.is_file(), p.stat().st_mtime_ns)
            for p in root.rglob("*")
            if p.exists()
        }

    before_fs = _snapshot_tree()

    prep_rep = prepare_task(root, task_path)
    preflight_rep = preflight_task(root, task_path)

    assert prep_rep == preflight_rep
    assert prep_rep["schema_version"] == 1
    assert prep_rep["state"] == "TASK_BLOCKED"
    assert "source_file_count" not in prep_rep
    assert "byte_count" not in prep_rep
    assert "preparation_notes" not in prep_rep
    assert len(prep_rep["issues"]) >= 1

    after_fs = _snapshot_tree()
    assert before_fs == after_fs


def test_unknown_exception_re_raised_same_identity(tmp_path, monkeypatch):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)
    custom_exc = RuntimeError("unhandled snapshot error")

    monkeypatch.setattr(
        runner,
        "_load_task_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(custom_exc),
    )

    with pytest.raises(RuntimeError) as exc_info:
        prepare_task(root, task_path)
    assert exc_info.value is custom_exc


def test_fatal_invalid_schema_uses_actual_loader_and_does_not_call_notes(
    tmp_path, monkeypatch
):
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)
    task_path.write_text("{broken json", encoding="utf-8")

    def trap_notes(*args, **kwargs):
        raise AssertionError("task_preparation_notes must not be called on fatal invalid schema")

    monkeypatch.setattr(runner, "task_preparation_notes", trap_notes)

    res = prepare_task(root, task_path)
    assert res["state"] == "TASK_BLOCKED"
    assert res["schema_version"] == 1
    assert "task_id" not in res
    assert len(res["issues"]) == 1
    assert res["issues"][0]["code"] == "TASK_SCHEMA_INVALID"


def test_standalone_child_import():
    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = repo_root / "scripts"

    code = f"""
import sys
from pathlib import Path

repo_str = {str(repo_root)!r}
scripts_str = {str(scripts_dir)!r}

sys.path = [p for p in sys.path if Path(p).resolve() != Path(repo_str).resolve()]
if scripts_str not in sys.path:
    sys.path.insert(0, scripts_str)

import two_site_task_preflight as preflight
import two_site_readiness_facts as facts
import two_site_proposal_runner as runner
import two_site_assignment_scope as scope

assert preflight.runner is runner, f"preflight.runner {{preflight.runner}} is not standalone runner {{runner}}"
assert facts.ProposalError is runner.ProposalError, "facts.ProposalError is not runner.ProposalError"
assert preflight.AssignmentFact is scope.AssignmentFact, "preflight.AssignmentFact is not scope.AssignmentFact"
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
