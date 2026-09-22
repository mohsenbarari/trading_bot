from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import pytest

from scripts import two_site_cycle_tools as ct
from scripts import two_site_preparation_io as prep_io
from scripts import two_site_proposal_runner as runner
from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_preparation_contract import PreparationConfigError
from tests.test_two_site_cycle_tools import repo, task
from tests.test_two_site_preparation_io import _write_canonical_startup


def _make_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="test_assignment_binding",
        stage_id="stage_a",
        allowed_paths=["tests/test_new.py"],
        forbidden_paths=[],
        expires_at="2099-01-01T00:00:00Z",
        sha256="a" * 64,
    )


def _setup_bound(root: Path, monkeypatch, route: str = "one_shot"):
    for k in list(os.environ.keys()):
        if k.startswith("TWO_SITE_ASSIGNMENT_") or k in (
            "TWO_SITE_AUTOPILOT_RUN_ID",
            "TWO_SITE_PREPARATION_STARTUP_SHA256",
        ):
            monkeypatch.delenv(k, raising=False)
    scope = _make_scope()
    monkeypatch.setattr(prep_io, "load_active_assignment_scope", lambda r: scope)
    try:
        import two_site_preparation_io
        monkeypatch.setattr(two_site_preparation_io, "load_active_assignment_scope", lambda r: scope)
    except (ImportError, AttributeError):
        pass

    head = runner.git(root, "rev-parse", "HEAD")
    run_id = f"cycle-2026-20260922T120000Z-{os.getpid()}"
    startup, env, startup_file = _write_canonical_startup(
        root, run_id, head, scope.sha256, route=route
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return startup, startup_file


def _setup_unbound(monkeypatch):
    for k in list(os.environ.keys()):
        if k.startswith("TWO_SITE_ASSIGNMENT_") or k in (
            "TWO_SITE_AUTOPILOT_RUN_ID",
            "TWO_SITE_PREPARATION_STARTUP_SHA256",
        ):
            monkeypatch.delenv(k, raising=False)


def _write_spec(root: Path, filename: str = "spec.json") -> Path:
    spec_path = root / filename
    spec_data = {
        "task": task(),
        "gap_reference": "gap.md#gap-a",
        "snippets": [{"path": "code.py", "symbols": ["f", "LIMIT"]}],
        "scenario_key": "scenario_a",
    }
    spec_path.write_text(json.dumps(spec_data), encoding="utf-8")
    return spec_path


def test_bound_routes_and_manual_ready_parity(repo, monkeypatch):
    root, _ = repo
    spec_path = _write_spec(root)

    orig_prepare_task = ct.prepare_task
    orig_check = ct.runner.check
    orig_emit = ct.emit_preparation_telemetry

    call_counts = {"prepare_task": 0, "check": 0, "emit": 0}

    def spy_prepare_task(r, o):
        call_counts["prepare_task"] += 1
        return orig_prepare_task(r, o)

    def spy_check(r, o):
        call_counts["check"] += 1
        return orig_check(r, o)

    def spy_emit(*args, **kwargs):
        call_counts["emit"] += 1
        return orig_emit(*args, **kwargs)

    monkeypatch.setattr(ct, "prepare_task", spy_prepare_task)
    monkeypatch.setattr(ct.runner, "check", spy_check)
    monkeypatch.setattr(ct, "emit_preparation_telemetry", spy_emit)

    # 1. Bound one_shot route
    _setup_bound(root, monkeypatch, route="one_shot")
    monkeypatch.setenv("ROUTE", "legacy")  # Runtime env route flags ignored
    out_oneshot = root / ".local/two-site-refactor/tasks/out_oneshot.json"
    call_counts.update(prepare_task=0, check=0, emit=0)
    res_oneshot = ct.build(root, spec_path, out_oneshot)
    assert res_oneshot.get("state") == "TASK_READY"
    assert call_counts["prepare_task"] == 1
    assert call_counts["check"] == 0
    assert call_counts["emit"] == 1
    assert out_oneshot.is_file()

    # 2. Bound legacy route
    _setup_bound(root, monkeypatch, route="legacy")
    monkeypatch.setenv("ROUTE", "one_shot")  # Runtime env route flags ignored
    out_legacy = root / ".local/two-site-refactor/tasks/out_legacy.json"
    call_counts.update(prepare_task=0, check=0, emit=0)
    res_legacy = ct.build(root, spec_path, out_legacy)
    assert res_legacy.get("state") == "TASK_READY"
    assert call_counts["prepare_task"] == 0
    assert call_counts["check"] == 1
    assert call_counts["emit"] == 1
    assert out_legacy.is_file()

    # 3. Unbound manual mode
    _setup_unbound(monkeypatch)
    out_manual = root / ".local/two-site-refactor/tasks/out_manual.json"
    call_counts.update(prepare_task=0, check=0, emit=0)
    res_manual = ct.build(root, spec_path, out_manual)
    assert res_manual.get("state") == "TASK_READY"
    assert call_counts["prepare_task"] == 1
    assert call_counts["check"] == 0
    assert call_counts["emit"] == 0  # Unbound manual emits none
    assert out_manual.is_file()

    # Parity comparisons: public payloads match except task_path
    p_oneshot = {k: v for k, v in res_oneshot.items() if k != "task_path"}
    p_legacy = {k: v for k, v in res_legacy.items() if k != "task_path"}
    p_manual = {k: v for k, v in res_manual.items() if k != "task_path"}
    assert p_oneshot == p_legacy == p_manual

    t_oneshot, s_oneshot = runner.load_task(root, out_oneshot)
    t_legacy, s_legacy = runner.load_task(root, out_legacy)
    t_manual, s_manual = runner.load_task(root, out_manual)
    assert t_oneshot == t_legacy == t_manual
    assert s_oneshot == s_legacy == s_manual


def test_bound_blocked_schema_and_single_selected_call(repo, monkeypatch):
    root, _ = repo
    spec_path = _write_spec(root)
    out_path = root / ".local/two-site-refactor/tasks/out_blocked.json"

    calls = {"prep": 0, "check": 0}

    # 1. Controlled one_shot blocked
    _setup_bound(root, monkeypatch, route="one_shot")
    monkeypatch.setattr(
        ct,
        "prepare_task",
        lambda r, o: {"state": "TASK_BLOCKED", "issues": [{"message": "blocked_one_shot"}]},
    )
    monkeypatch.setattr(ct.runner, "check", lambda r, o: calls.__setitem__("check", calls["check"] + 1))

    out_path.unlink(missing_ok=True)
    res = ct.build(root, spec_path, out_path)
    assert res == {"state": "TASK_BLOCKED", "issues": [{"message": "blocked_one_shot"}]}
    assert not out_path.exists()
    assert calls["check"] == 0

    # 2. Controlled legacy blocked
    _setup_bound(root, monkeypatch, route="legacy")
    monkeypatch.setattr(ct, "prepare_task", lambda r, o: calls.__setitem__("prep", calls["prep"] + 1))
    monkeypatch.setattr(
        ct.runner,
        "check",
        lambda r, o: {"state": "TASK_BLOCKED", "issues": [{"message": "blocked_legacy"}]},
    )

    out_path.unlink(missing_ok=True)
    res = ct.build(root, spec_path, out_path)
    assert res == {"state": "TASK_BLOCKED", "issues": [{"message": "blocked_legacy"}]}
    assert not out_path.exists()
    assert calls["prep"] == 0

    # 3. Unknown result cases: None, CustomDict, unknown state
    class CustomDict(dict):
        pass

    for bad in (None, CustomDict({"state": "TASK_READY"}), {"state": "UNKNOWN_STATE"}):
        out_path.unlink(missing_ok=True)
        monkeypatch.setattr(ct.runner, "check", lambda r, o: bad)
        res = ct.build(root, spec_path, out_path)
        assert not out_path.exists()
        assert res == {"schema_version": 1, "state": "TASK_BLOCKED", "issues": []}


def test_startup_drift_cleans_only_owned_output(repo, monkeypatch):
    root, _ = repo
    spec_path = _write_spec(root)
    out_path = root / ".local/two-site-refactor/tasks/drift_test.json"

    # 1. Startup rewritten during selected prep call: verify rejects and task removed
    _, startup_file = _setup_bound(root, monkeypatch, route="one_shot")
    orig_prepare = ct.prepare_task

    def prep_and_corrupt(r, o):
        res = orig_prepare(r, o)
        startup_file.write_text("{\"corrupted\": true}", encoding="utf-8")
        return res

    monkeypatch.setattr(ct, "prepare_task", prep_and_corrupt)
    out_path.unlink(missing_ok=True)

    with pytest.raises(PreparationConfigError):
        ct.build(root, spec_path, out_path)
    assert not out_path.exists()

    # 2. Preexisting output collision is never deleted
    _setup_bound(root, monkeypatch, route="one_shot")
    collision_path = root / ".local/two-site-refactor/tasks/collision.json"
    collision_path.parent.mkdir(parents=True, exist_ok=True)
    collision_path.write_bytes(b"SENTINEL_EXISTING_OUTPUT")

    monkeypatch.setattr(ct, "prepare_task", orig_prepare)
    with pytest.raises(FileExistsError):
        ct.build(root, spec_path, collision_path)
    assert collision_path.exists()
    assert collision_path.read_bytes() == b"SENTINEL_EXISTING_OUTPUT"


def test_telemetry_observer_cannot_change_build_result(repo, monkeypatch, capsys):
    root, _ = repo
    spec_path = _write_spec(root)
    out_path = root / ".local/two-site-refactor/tasks/observer_test.json"

    # 1. Validate stderr telemetry prefix and strict payload structure
    _setup_bound(root, monkeypatch, route="one_shot")
    capsys.readouterr()
    out_path.unlink(missing_ok=True)
    res = ct.build(root, spec_path, out_path)
    assert res.get("state") == "TASK_READY"

    captured = capsys.readouterr()
    lines = [ln for ln in captured.err.splitlines() if ln.startswith("AE01_PREPARATION_TELEMETRY_JSON:")]
    assert len(lines) == 1
    tel = json.loads(lines[0][len("AE01_PREPARATION_TELEMETRY_JSON:"):])
    assert tel["outcome"] == "TASK_READY"
    assert tel["source_bytes"] is None
    assert tel["issue_count"] == 0
    assert isinstance(tel["source_file_count"], int)
    assert isinstance(tel["task_bytes"], int)
    assert tel["task_bytes"] != res["byte_count"]
    assert tel["efficiency"] == "UNKNOWN"
    assert "Test method f" not in lines[0]  # No goal text leak
    assert "LIMIT = 2" not in lines[0]  # No source snippet leak

    # 2. Observer clock RuntimeError does not alter build result
    out_path.unlink(missing_ok=True)
    monkeypatch.setattr(ct.time, "monotonic", lambda: (_ for _ in ()).throw(RuntimeError("clock broken")))
    res_clock = ct.build(root, spec_path, out_path)
    assert res_clock == res

    # 3. Broken emitter RuntimeError does not alter build result
    out_path.unlink(missing_ok=True)
    monkeypatch.setattr(ct, "emit_preparation_telemetry", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broken emitter")))
    res_emit = ct.build(root, spec_path, out_path)
    assert res_emit == res

    # 4. Malicious metric object __int__ / __str__ not called
    class PoisonMetric:
        def __int__(self):
            raise AssertionError("called __int__")

        def __str__(self):
            raise AssertionError("called __str__")

    out_path.unlink(missing_ok=True)
    monkeypatch.setattr(
        ct,
        "prepare_task",
        lambda r, o: {"state": "TASK_READY", "source_file_count": PoisonMetric()},
    )
    res_poison = ct.build(root, spec_path, out_path)
    assert res_poison.get("state") == "TASK_READY"

    # 5. Genuine prepare_task exception preserved with failed observer
    marker = RuntimeError("genuine_prep_error")
    monkeypatch.setattr(ct, "prepare_task", lambda r, o: (_ for _ in ()).throw(marker))
    out_path.unlink(missing_ok=True)
    with pytest.raises(RuntimeError) as exc_info:
        ct.build(root, spec_path, out_path)
    assert exc_info.value is marker

    # 6. KeyboardInterrupt in emitter propagates
    out_path.unlink(missing_ok=True)
    monkeypatch.setattr(ct, "prepare_task", ct.prepare_task)
    monkeypatch.setattr(ct, "emit_preparation_telemetry", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        ct.build(root, spec_path, out_path)


def test_bound_cli_keeps_public_stdout_contract(repo, monkeypatch, capsys):
    root, _ = repo
    spec_path = _write_spec(root, "spec_cli.json")
    out_path = root / ".local/two-site-refactor/tasks/cli_task.json"

    # 1. Genuine READY build via ct.main
    _setup_bound(root, monkeypatch, route="one_shot")
    out_path.unlink(missing_ok=True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ct",
            "--root",
            str(root),
            "build",
            "--spec",
            "spec_cli.json",
            "--output",
            ".local/two-site-refactor/tasks/cli_task.json",
        ],
    )
    capsys.readouterr()
    exit_code = ct.main()
    assert exit_code == 0
    assert out_path.is_file()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data.get("state") == "TASK_READY"
    assert captured.out.strip() == json.dumps(data)
    assert "AE01_PREPARATION_TELEMETRY_JSON:" in captured.err

    # 2. Controlled BLOCKED build via ct.main
    monkeypatch.setattr(
        ct,
        "prepare_task",
        lambda r, o: {"state": "TASK_BLOCKED", "issues": [{"message": "blocked_cli"}]},
    )
    bad_out = root / ".local/two-site-refactor/tasks/cli_bad.json"
    bad_out.unlink(missing_ok=True)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ct",
            "--root",
            str(root),
            "build",
            "--spec",
            "spec_cli.json",
            "--output",
            ".local/two-site-refactor/tasks/cli_bad.json",
        ],
    )
    capsys.readouterr()
    exit_code = ct.main()
    assert exit_code == 1
    assert not bad_out.exists()

    captured_bad = capsys.readouterr()
    data_bad = json.loads(captured_bad.out)
    assert data_bad.get("state") == "TASK_BLOCKED"
    assert data_bad == {"state": "TASK_BLOCKED", "issues": [{"message": "blocked_cli"}]}
    assert captured_bad.out.strip() == json.dumps(data_bad)
    assert "AE01_PREPARATION_TELEMETRY_JSON:" in captured_bad.err
