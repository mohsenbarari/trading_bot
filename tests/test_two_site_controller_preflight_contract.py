from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import two_site_cycle_tools as ct
from scripts import two_site_proposal_runner as runner
from scripts.two_site_assignment_scope import AssignmentScope
from tests.test_two_site_context_output_runner_gates import _make_live_session_fixture
from tests.test_two_site_cycle_tools import repo, task


def test_composed_cli_build_prepare_task_fallbacks(repo, monkeypatch, capsys):
    root, _ = repo
    spec_path = root / "spec.json"
    spec_data = {
        "task": task(),
        "gap_reference": "gap.md#gap-a",
        "snippets": [{"path": "code.py", "symbols": ["f"]}],
        "scenario_key": "scenario_a",
    }
    spec_path.write_text(json.dumps(spec_data), encoding="utf-8")
    output_path = root / ".local/two-site-refactor/tasks/out.json"

    class CustomDict(dict):
        pass

    cases = [
        CustomDict({"state": "TASK_READY", "schema_version": 1}),
        {"state": "UNKNOWN_STATE"},
        None,
    ]

    for mock_result in cases:
        capsys.readouterr()
        output_path.unlink(missing_ok=True)
        monkeypatch.setattr(ct, "prepare_task", lambda r, p: mock_result)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "ct",
                "--root",
                str(root),
                "build",
                "--spec",
                "spec.json",
                "--output",
                ".local/two-site-refactor/tasks/out.json",
            ],
        )
        exit_code = ct.main()
        assert exit_code == 1
        assert not output_path.exists()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["state"] == "TASK_BLOCKED"
        assert set(data.keys()) == {"schema_version", "state", "issues"}
        assert data == {"schema_version": 1, "state": "TASK_BLOCKED", "issues": []}


def test_real_valid_ready_task_build(repo, monkeypatch, capsys):
    root, _ = repo
    spec_path = root / "spec.json"
    spec_data = {
        "task": task(),
        "gap_reference": "gap.md#gap-a",
        "snippets": [{"path": "code.py", "symbols": ["f", "LIMIT"]}],
        "scenario_key": "scenario_a",
    }
    spec_path.write_text(json.dumps(spec_data), encoding="utf-8")
    output_path = root / ".local/two-site-refactor/tasks/ready_task.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ct",
            "--root",
            str(root),
            "build",
            "--spec",
            "spec.json",
            "--output",
            ".local/two-site-refactor/tasks/ready_task.json",
        ],
    )
    exit_code = ct.main()
    assert exit_code == 0
    assert output_path.is_file()
    assert output_path.stat().st_mode & 0o777 == 0o600

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["state"] == "TASK_READY"
    assert data["task_path"] == str(output_path)
    assert data["gap_reference"] == "gap.md#gap-a"
    assert data["excerpt_count"] == 1
    assert isinstance(data["semantic_task_fingerprint"], str)
    assert len(data["semantic_task_fingerprint"]) == 64
    assert "sizing_note" in data
    assert "source_file_count" in data
    assert "byte_count" in data
    assert "preparation_notes" in data


def test_real_fatal_schema_and_oversize_task_id(repo, monkeypatch, capsys):
    root, _ = repo

    task_empty_req = task()
    task_empty_req["requirements"] = []
    spec_empty = root / "spec_empty_req.json"
    spec_empty.write_text(
        json.dumps({
            "task": task_empty_req,
            "gap_reference": "gap.md#gap-a",
            "snippets": [{"path": "code.py", "symbols": ["f"]}],
            "scenario_key": "scenario_a",
        }),
        encoding="utf-8",
    )
    out_empty = root / ".local/two-site-refactor/tasks/empty_req.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ct",
            "--root",
            str(root),
            "build",
            "--spec",
            "spec_empty_req.json",
            "--output",
            ".local/two-site-refactor/tasks/empty_req.json",
        ],
    )
    exit_code = ct.main()
    assert exit_code == 1
    assert not out_empty.exists()
    captured = capsys.readouterr()
    res1 = json.loads(captured.out)
    assert res1["state"] == "TASK_BLOCKED"
    assert len(captured.out.encode("utf-8")) <= 16384
    assert "task_id" not in res1
    assert "task_path" not in res1
    assert any(iss.get("code") == "TASK_SCHEMA_INVALID" for iss in res1.get("issues", []))

    task_long_id = task()
    task_long_id["task_id"] = "x" * 20000
    spec_long = {
        "task": task_long_id,
        "gap_reference": "gap.md#gap-a",
        "snippets": [{"path": "code.py", "symbols": ["f"]}],
        "scenario_key": "scenario_a",
    }
    spec_long_bytes = json.dumps(spec_long).encode("utf-8")
    assert len(spec_long_bytes) < 160000
    spec_long_path = root / "spec_long_id.json"
    spec_long_path.write_bytes(spec_long_bytes)

    out_long = root / ".local/two-site-refactor/tasks/long_id.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ct",
            "--root",
            str(root),
            "build",
            "--spec",
            "spec_long_id.json",
            "--output",
            ".local/two-site-refactor/tasks/long_id.json",
        ],
    )
    exit_code = ct.main()
    assert exit_code == 1
    assert not out_long.exists()
    captured = capsys.readouterr()
    res2 = json.loads(captured.out)
    assert res2["state"] == "TASK_BLOCKED"
    assert len(captured.out.encode("utf-8")) <= 16384
    assert "task_id" not in res2
    assert "task_path" not in res2
    assert any(iss.get("code") == "TASK_SCHEMA_INVALID" for iss in res2.get("issues", []))


def test_actual_three_defect_fixture_and_exact_issues(repo, monkeypatch):
    root, git = repo
    task_fixture = _make_live_session_fixture(root)

    order_file = root / "models/order.py"
    order_file.parent.mkdir(parents=True, exist_ok=True)
    order_file.write_text("class Order:\n    pass\n", encoding="utf-8")

    task_fixture["allowed_files"] = ["tests/test_new.py"]
    task_fixture["test_selectors"] = ["tests/test_new.py"]
    task_fixture["context_files"] = ["tests/live_helper.py"]
    task_fixture["goal"] = task_fixture["goal"] + " Persist Order into database."

    git(
        "add",
        "--",
        "tests/live_helper.py",
        "api/live.py",
        "core/lookup.py",
        "core/patched.py",
        "models/order.py",
    )
    git("commit", "-m", "track five fixture files")

    spec_path = root / "spec_defects.json"
    spec_data = {
        "task": task_fixture,
        "gap_reference": "gap.md#gap-a",
        "snippets": [{"path": "code.py", "symbols": ["f"]}],
        "scenario_key": "scenario_a",
    }
    spec_path.write_text(json.dumps(spec_data), encoding="utf-8")

    scope = AssignmentScope(
        "ASG-1",
        "P1-00",
        ("tests/test_new.py",),
        (),
        "2099-01-01T00:00:00Z",
        "a" * 64,
    )
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_ID", "ASG-1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SHA256", "a" * 64)
    monkeypatch.setattr(
        runner,
        "load_expected_assignment_scope",
        lambda r, aid, sha: scope,
    )

    def _fail_run(*args, **kwargs):
        raise AssertionError("runner.run is forbidden")

    monkeypatch.setattr(runner, "run", _fail_run)

    import socket

    def _fail_socket(*args, **kwargs):
        raise AssertionError("network socket is forbidden during preflight")

    monkeypatch.setattr(socket, "socket", _fail_socket)

    real_prepare = ct.prepare_task
    prepare_calls = []

    def spy_prepare(*args, **kwargs):
        prepare_calls.append(args)
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(ct, "prepare_task", spy_prepare)

    head_before = git("rev-parse", "HEAD")
    status_before = git("status", "--porcelain=v1")

    output_path = root / ".local/two-site-refactor/tasks/defects_out.json"
    result = ct.build(root, spec_path, output_path)

    assert len(prepare_calls) == 1
    assert not output_path.exists()
    assert result["state"] == "TASK_BLOCKED"

    issue_codes = [iss["code"] for iss in result["issues"]]
    assert len(issue_codes) == 3
    assert set(issue_codes) == {
        "MODEL_CONTEXT_MISSING",
        "SESSION_CONTEXT_MISSING",
        "TASK_ASSIGNMENT_TOKEN_MISSING",
    }

    head_after = git("rev-parse", "HEAD")
    status_after = git("status", "--porcelain=v1")
    assert head_after == head_before
    assert status_after == status_before


def test_preexisting_file_preserved_and_unexpected_exception_cleanup(repo, monkeypatch):
    root, _ = repo
    spec_path = root / "spec.json"
    spec_path.write_text(
        json.dumps({
            "task": task(),
            "gap_reference": "gap.md#gap-a",
            "snippets": [{"path": "code.py", "symbols": ["f"]}],
            "scenario_key": "scenario_a",
        }),
        encoding="utf-8",
    )

    tasks_dir = root / ".local/two-site-refactor/tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    preexisting = tasks_dir / "preexisting.json"
    original_bytes = b'{"preexisting": true}\n'
    preexisting.write_bytes(original_bytes)
    preexisting.chmod(0o644)
    orig_mode = preexisting.stat().st_mode

    with pytest.raises(FileExistsError):
        ct.build(root, spec_path, preexisting)

    assert preexisting.is_file()
    assert preexisting.read_bytes() == original_bytes
    assert preexisting.stat().st_mode == orig_mode

    fresh_out = tasks_dir / "fresh_failure.json"
    fresh_out.unlink(missing_ok=True)
    custom_exc = RuntimeError("unexpected prepare_task explosion")

    monkeypatch.setattr(
        ct,
        "prepare_task",
        lambda r, p: (_ for _ in ()).throw(custom_exc),
    )

    with pytest.raises(RuntimeError) as exc_info:
        ct.build(root, spec_path, fresh_out)

    assert exc_info.value is custom_exc
    assert not fresh_out.exists()


def test_separate_cli_review_stub_does_not_call_chain_when_build_mocked(repo, monkeypatch, capsys):
    root, _ = repo

    def _trap_review(*args, **kwargs):
        raise AssertionError("ct.review must not be called during build")

    monkeypatch.setattr(ct, "review", _trap_review)
    monkeypatch.setattr(ct, "build", lambda r, s, o: {"state": "TASK_READY", "schema_version": 1})

    monkeypatch.setattr(
        sys,
        "argv",
        ["ct", "--root", str(root), "build", "--spec", "spec.json", "--output", ".local/two-site-refactor/tasks/out.json"],
    )
    exit_build = ct.main()
    assert exit_build == 0
    capsys.readouterr()

    review_calls = []

    def _stub_review(r, task_p, worker, rev, sha, applied):
        review_calls.append((task_p, worker, rev, sha, applied))
        return {"state": "BINDINGS_VALID", "head": "mockhead"}

    monkeypatch.setattr(ct, "review", _stub_review)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ct",
            "--root",
            str(root),
            "review",
            "--task",
            "task.json",
            "--worker",
            "w1",
            "--reviewer",
            "r1",
            "--sha",
            "a" * 64,
        ],
    )
    exit_review = ct.main()
    assert exit_review == 0
    assert len(review_calls) == 1
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["state"] == "BINDINGS_VALID"


def test_fresh_child_process_isolated_path_and_runner_identity(repo):
    root, _ = repo
    repo_root = runner.ROOT
    py_code = """
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
valid_root = Path(sys.argv[2]).resolve()
scripts_dir = str(repo_root / "scripts")
parent_root = str(repo_root)

sys.path = [scripts_dir] + [p for p in sys.path if p != parent_root and p != ""]

import two_site_cycle_tools as ct
import two_site_task_preflight as preflight

assert ct.runner is preflight.runner, f"{ct.runner} is not {preflight.runner}"

from unittest.mock import patch

with patch.object(ct, "build", return_value={"state": "TASK_READY", "schema_version": 1}):
    sys.argv = ["ct", "--root", str(valid_root), "build", "--spec", "spec.json", "--output", ".local/two-site-refactor/tasks/out.json"]
    ret_ready = ct.main()
    assert ret_ready == 0, f"expected exit 0 for TASK_READY, got {ret_ready}"

with patch.object(ct, "build", return_value={"state": "TASK_BLOCKED", "schema_version": 1, "issues": []}):
    sys.argv = ["ct", "--root", str(valid_root), "build", "--spec", "spec.json", "--output", ".local/two-site-refactor/tasks/out.json"]
    ret_blocked = ct.main()
    assert ret_blocked == 1, f"expected exit 1 for TASK_BLOCKED, got {ret_blocked}"

print("CHILD_OK")
"""
    cmd = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        py_code,
        str(repo_root),
        str(root),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "CHILD_OK" in proc.stdout
