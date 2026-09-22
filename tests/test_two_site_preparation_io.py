"""Tests for two-site preparation consumer I/O and telemetry."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import pytest
from scripts import two_site_preparation_io as prep_io
from scripts import two_site_proposal_runner as runner
from scripts.two_site_assignment_scope import AssignmentScope, AssignmentScopeError
from scripts.two_site_preparation_contract import PreparationConfigError, PreparationStartup
from scripts.two_site_proposal_runner import ProposalError
from tests.test_two_site_cycle_tools import repo
from tests.test_two_site_supervisor_preparation_startup import _make_assignment_scope


class _ArmedStr(str):
    pass


def _write_canonical_startup(root: Path, run_id: str, head: str, scope_sha: str, route: str = "one_shot"):
    startup = PreparationStartup(1, route, run_id, head, scope_sha)
    run_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    startup_file = run_dir / "preparation-startup.json"
    canonical_bytes = json.dumps(
        startup.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    startup_file.write_bytes(canonical_bytes)
    env = {
        "TWO_SITE_AUTOPILOT_RUN_ID": run_id,
        "TWO_SITE_PREPARATION_STARTUP_SHA256": startup.fingerprint,
    }
    return startup, env, startup_file


def test_canonical_load_and_manual_mode(repo, monkeypatch):
    root, git_fn = repo
    head = git_fn("rev-parse", "HEAD")
    scope = _make_assignment_scope()
    monkeypatch.setattr(prep_io, "load_active_assignment_scope", lambda r: scope)

    for idx, route in enumerate(("one_shot", "legacy"), start=1):
        run_id = f"cycle-0001-20260922T120000Z-{idx}"
        startup, env, _ = _write_canonical_startup(root, run_id, head, scope.sha256, route=route)
        loaded = prep_io.load_preparation_startup(root, environment=env)
        assert loaded is not None
        assert loaded.route == route

        env_with_flag = dict(env)
        env_with_flag["TWO_SITE_PREPARATION_ROUTE"] = "unsupported_flag"
        loaded_flag = prep_io.load_preparation_startup(root, environment=env_with_flag)
        assert loaded_flag is not None
        assert loaded_flag.route == route

    trap_git = MagicMock()
    trap_reader = MagicMock()
    trap_scope = MagicMock()
    monkeypatch.setattr(prep_io, "git", trap_git)
    monkeypatch.setattr(prep_io, "bounded_file_bytes", trap_reader)
    monkeypatch.setattr(prep_io, "load_active_assignment_scope", trap_scope)

    assert prep_io.load_preparation_startup(Path("/nonexistent"), environment={}) is None
    assert trap_git.call_count == 0
    assert trap_reader.call_count == 0
    assert trap_scope.call_count == 0


_INVALID_CASES = [
    "partial_run_id",
    "partial_sha",
    "null_run_id",
    "null_sha",
    "empty_run_id",
    "empty_sha",
    "subclass_run_id",
    "subclass_sha",
    "escape_run_id",
    "oversized",
    "malformed_json",
    "duplicate_json",
    "file_symlink",
    "ancestor_symlink",
    "hardlink",
    "fifo",
    "wrong_digest",
    "wrong_cycle",
    "wrong_head",
    "wrong_scope",
    "whitespace_extra",
    "sibling_only",
]


@pytest.mark.parametrize("case", _INVALID_CASES)
def test_invalid_binding_and_unsafe_files(repo, monkeypatch, case):
    root, git_fn = repo
    head = git_fn("rev-parse", "HEAD")
    scope = _make_assignment_scope()
    monkeypatch.setattr(prep_io, "load_active_assignment_scope", lambda r: scope)

    run_id = "cycle-0001-20260922T120000Z-1"
    startup, env, startup_file = _write_canonical_startup(root, run_id, head, scope.sha256)

    if case == "partial_run_id":
        env.pop("TWO_SITE_PREPARATION_STARTUP_SHA256")
    elif case == "partial_sha":
        env.pop("TWO_SITE_AUTOPILOT_RUN_ID")
    elif case == "null_run_id":
        env["TWO_SITE_AUTOPILOT_RUN_ID"] = None
    elif case == "null_sha":
        env["TWO_SITE_PREPARATION_STARTUP_SHA256"] = None
    elif case == "empty_run_id":
        env["TWO_SITE_AUTOPILOT_RUN_ID"] = ""
    elif case == "empty_sha":
        env["TWO_SITE_PREPARATION_STARTUP_SHA256"] = ""
    elif case == "subclass_run_id":
        env["TWO_SITE_AUTOPILOT_RUN_ID"] = _ArmedStr(run_id)
    elif case == "subclass_sha":
        env["TWO_SITE_PREPARATION_STARTUP_SHA256"] = _ArmedStr(env["TWO_SITE_PREPARATION_STARTUP_SHA256"])
    elif case == "escape_run_id":
        env["TWO_SITE_AUTOPILOT_RUN_ID"] = "../cycle-0001-20260922T120000Z-1"
    elif case == "oversized":
        startup_file.write_bytes(b"x" * 4097)
    elif case == "malformed_json":
        startup_file.write_bytes(b"{not json")
    elif case == "duplicate_json":
        bad = (
            f'{{"assignment_sha256":"{scope.sha256}","cycle_id":"{run_id}",'
            f'"route":"one_shot","route":"one_shot","schema_version":1,"source_head":"{head}"}}'
        )
        startup_file.write_bytes(bad.encode("ascii"))
    elif case == "file_symlink":
        real_f = startup_file.parent / "real.json"
        real_f.write_bytes(startup_file.read_bytes())
        startup_file.unlink()
        startup_file.symlink_to(real_f)
    elif case == "ancestor_symlink":
        run_dir = startup_file.parent
        real_dir = run_dir.parent / "real_run"
        run_dir.rename(real_dir)
        run_dir.symlink_to(real_dir)
    elif case == "hardlink":
        os.link(startup_file, startup_file.parent / "extra.json")
    elif case == "fifo":
        startup_file.unlink()
        os.mkfifo(startup_file)
    elif case == "wrong_digest":
        env["TWO_SITE_PREPARATION_STARTUP_SHA256"] = "0" * 64
    elif case == "wrong_cycle":
        cycle2 = "cycle-0002-20260922T120000Z-2"
        st2 = PreparationStartup(1, "one_shot", cycle2, head, scope.sha256)
        cbytes2 = json.dumps(st2.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        startup_file.write_bytes(cbytes2)
        env["TWO_SITE_PREPARATION_STARTUP_SHA256"] = st2.fingerprint
    elif case == "wrong_head":
        st_bad = PreparationStartup(1, "one_shot", run_id, "0" * 40, scope.sha256)
        cbytes = json.dumps(st_bad.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        startup_file.write_bytes(cbytes)
        env["TWO_SITE_PREPARATION_STARTUP_SHA256"] = st_bad.fingerprint
    elif case == "wrong_scope":
        st_bad = PreparationStartup(1, "one_shot", run_id, head, "0" * 64)
        cbytes = json.dumps(st_bad.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        startup_file.write_bytes(cbytes)
        env["TWO_SITE_PREPARATION_STARTUP_SHA256"] = st_bad.fingerprint
    elif case == "whitespace_extra":
        startup_file.write_bytes(startup_file.read_bytes() + b"\n")
    elif case == "sibling_only":
        sib_dir = startup_file.parent.parent / "cycle-0099-20260922T120000Z-9"
        sib_dir.mkdir(parents=True, exist_ok=True)
        (sib_dir / "preparation-startup.json").write_bytes(startup_file.read_bytes())
        startup_file.unlink()

    env_copy = dict(env)
    with pytest.raises(PreparationConfigError):
        prep_io.load_preparation_startup(root, environment=env)
    assert env == env_copy


def test_verify_drift_and_fixed_errors(repo, monkeypatch):
    root, git_fn = repo
    head = git_fn("rev-parse", "HEAD")
    scope = _make_assignment_scope()
    monkeypatch.setattr(prep_io, "load_active_assignment_scope", lambda r: scope)

    run_id = "cycle-0001-20260922T120000Z-1"
    startup, env, _ = _write_canonical_startup(root, run_id, head, scope.sha256, route="one_shot")

    prep_io.verify_preparation_startup(root, startup, environment=env)
    prep_io.verify_preparation_startup(root, None, environment={})

    with pytest.raises(PreparationConfigError, match="Expected bound startup but observed unbound startup"):
        prep_io.verify_preparation_startup(root, startup, environment={})

    with pytest.raises(PreparationConfigError, match="Expected unbound startup but observed bound startup"):
        prep_io.verify_preparation_startup(root, None, environment=env)

    startup_legacy = PreparationStartup(1, "legacy", run_id, head, scope.sha256)
    with pytest.raises(PreparationConfigError, match="Startup identity drift detected"):
        prep_io.verify_preparation_startup(root, startup_legacy, environment=env)

    sentinel = "SECRET_SENTINEL_XYZ_12345"
    monkeypatch.setattr(prep_io, "bounded_file_bytes", lambda *a, **k: (_ for _ in ()).throw(ProposalError(sentinel)))
    with pytest.raises(PreparationConfigError) as exc_reader:
        prep_io.load_preparation_startup(root, environment=env)
    assert sentinel not in str(exc_reader.value)

    monkeypatch.setattr(prep_io, "bounded_file_bytes", runner.bounded_file_bytes)
    monkeypatch.setattr(prep_io, "load_active_assignment_scope", lambda *a, **k: (_ for _ in ()).throw(AssignmentScopeError(sentinel)))
    with pytest.raises(PreparationConfigError) as exc_scope:
        prep_io.load_preparation_startup(root, environment=env)
    assert sentinel not in str(exc_scope.value)

    monkeypatch.setattr(prep_io, "bounded_file_bytes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unexpected failure")))
    with pytest.raises(RuntimeError, match="unexpected failure"):
        prep_io.load_preparation_startup(root, environment=env)

    monkeypatch.setattr(prep_io, "bounded_file_bytes", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        prep_io.load_preparation_startup(root, environment=env)

    class ExplodingMapping:
        def __contains__(self, key):
            raise RuntimeError("mapping error")

    with pytest.raises(RuntimeError, match="mapping error"):
        prep_io.load_preparation_startup(root, environment=ExplodingMapping())


def test_bounded_observational_stderr(repo, monkeypatch, capsys):
    root, git_fn = repo
    head = git_fn("rev-parse", "HEAD")
    run_id = "cycle-0001-20260922T120000Z-1"
    startup = PreparationStartup(1, "one_shot", run_id, head, "a" * 64)

    files_before = set(root.rglob("*"))

    res = prep_io.emit_preparation_telemetry(
        startup,
        outcome="TASK_READY",
        elapsed_seconds=1.25,
        source_file_count=5,
        source_bytes=100,
        task_bytes=50,
        issue_count=0,
    )
    assert res is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("AE01_PREPARATION_TELEMETRY_JSON:")
    assert len(captured.err.encode("ascii")) <= 4096
    data = json.loads(captured.err[len("AE01_PREPARATION_TELEMETRY_JSON:"):].strip())
    expected_keys = {
        "schema_version", "startup", "startup_sha256", "outcome",
        "elapsed_seconds", "source_file_count", "source_bytes",
        "task_bytes", "issue_count", "measurement_state", "efficiency",
    }
    assert set(data.keys()) == expected_keys

    class SecretPayload:
        def __str__(self): raise AssertionError("__str__ called")
        def __int__(self): raise AssertionError("__int__ called")

    secret = SecretPayload()
    res2 = prep_io.emit_preparation_telemetry(
        startup,
        outcome=secret,
        elapsed_seconds=secret,
        source_file_count=secret,
        source_bytes=secret,
        task_bytes=secret,
        issue_count=secret,
    )
    assert res2 is True
    data2 = json.loads(capsys.readouterr().err[len("AE01_PREPARATION_TELEMETRY_JSON:"):].strip())
    assert data2["outcome"] == "UNKNOWN"
    assert data2["elapsed_seconds"] is None
    assert data2["source_file_count"] is None
    assert data2["source_bytes"] is None
    assert data2["task_bytes"] is None
    assert data2["issue_count"] is None

    res3 = prep_io.emit_preparation_telemetry(startup, outcome="TASK_READY", source_bytes=0, task_bytes=None)
    assert res3 is True
    data3 = json.loads(capsys.readouterr().err[len("AE01_PREPARATION_TELEMETRY_JSON:"):].strip())
    assert data3["source_bytes"] is 0
    assert data3["task_bytes"] is None

    assert prep_io.emit_preparation_telemetry("invalid", outcome="TASK_READY") is False

    monkeypatch.setattr(sys.stderr, "write", lambda s: (_ for _ in ()).throw(OSError("write error")))
    assert prep_io.emit_preparation_telemetry(startup, outcome="TASK_READY") is False

    monkeypatch.setattr(sys.stderr, "write", lambda s: len(s) - 1)
    assert prep_io.emit_preparation_telemetry(startup, outcome="TASK_READY") is False

    monkeypatch.setattr(prep_io, "preparation_telemetry", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("formatter error")))
    assert prep_io.emit_preparation_telemetry(startup, outcome="TASK_READY") is False

    monkeypatch.setattr(prep_io, "preparation_telemetry", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        prep_io.emit_preparation_telemetry(startup, outcome="TASK_READY")

    monkeypatch.setattr(prep_io, "preparation_telemetry", lambda *a, **k: (_ for _ in ()).throw(SystemExit(1)))
    with pytest.raises(SystemExit):
        prep_io.emit_preparation_telemetry(startup, outcome="TASK_READY")

    assert set(root.rglob("*")) == files_before


def test_real_package_and_standalone_imports(repo):
    root, git_fn = repo
    scripts_dir = Path(runner.__file__).resolve().parent
    repo_root = scripts_dir.parent

    head = git_fn("rev-parse", "HEAD")
    run_id = "cycle-0001-20260922T120000Z-1"
    scope_sha = "a" * 64
    startup, env_dict, _ = _write_canonical_startup(root, run_id, head, scope_sha)

    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    clean_env.update(env_dict)
    clean_env["APP_ENV_FILE"] = "config/unit-test.env.example"

    code_pkg = (
        "import sys, os\n"
        "from pathlib import Path\n"
        "from unittest.mock import patch\n"
        "from scripts.two_site_assignment_scope import AssignmentScope\n"
        "from scripts import two_site_preparation_io as prep_io\n"
        "scope = AssignmentScope('WA-TEST-001', 'P1', (), (), '2099-01-01T00:00:00Z', 'a' * 64)\n"
        "with patch.object(prep_io, 'load_active_assignment_scope', return_value=scope):\n"
        "    res = prep_io.load_preparation_startup(Path(sys.argv[1]))\n"
        "    assert res is not None\n"
        "    prep_io.verify_preparation_startup(Path(sys.argv[1]), res)\n"
    )
    subprocess.run(
        [sys.executable, "-c", code_pkg, str(root)],
        cwd=str(repo_root),
        env=clean_env,
        capture_output=True,
        text=True,
        check=True,
    )

    code_standalone = (
        "import sys, os\n"
        "from pathlib import Path\n"
        "from unittest.mock import patch\n"
        "from two_site_assignment_scope import AssignmentScope\n"
        "import two_site_preparation_io as prep_io\n"
        "scope = AssignmentScope('WA-TEST-001', 'P1', (), (), '2099-01-01T00:00:00Z', 'a' * 64)\n"
        "with patch.object(prep_io, 'load_active_assignment_scope', return_value=scope):\n"
        "    res = prep_io.load_preparation_startup(Path(sys.argv[1]))\n"
        "    assert res is not None\n"
        "    prep_io.verify_preparation_startup(Path(sys.argv[1]), res)\n"
    )
    subprocess.run(
        [sys.executable, "-c", code_standalone, str(root)],
        cwd=str(scripts_dir),
        env=clean_env,
        capture_output=True,
        text=True,
        check=True,
    )
