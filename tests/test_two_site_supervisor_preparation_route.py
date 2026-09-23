from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
import sys

import pytest

try:
    from scripts.two_site_assignment_scope import AssignmentScope
    from scripts.two_site_codex_autopilot import (
        EXPECTED_CODEX_CLI_VERSION,
        PROTECTED_REFS,
        CONTROL_PATHS,
        AutopilotError,
        main,
        run_cycle,
        run_loop,
    )
    from scripts.two_site_preparation_contract import PreparationStartup
except ModuleNotFoundError:
    from two_site_assignment_scope import AssignmentScope  # type: ignore[no-redef]
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        EXPECTED_CODEX_CLI_VERSION,
        PROTECTED_REFS,
        CONTROL_PATHS,
        AutopilotError,
        main,
        run_cycle,
        run_loop,
    )
    from two_site_preparation_contract import PreparationStartup  # type: ignore[no-redef]


def _make_assignment_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="WA-TEST-SCOPE-001",
        stage_id="P1-TEST",
        allowed_paths=("tests/**",),
        forbidden_paths=("blocked/**",),
        expires_at="2099-01-01T00:00:00Z",
        sha256="a" * 64,
    )


def _make_before(head: str = "1" * 40, scope: AssignmentScope | None = None) -> dict[str, object]:
    active_scope = scope if scope is not None else _make_assignment_scope()
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: "2" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "3" * 40 for p in CONTROL_PATHS},
        "assignment_scope": active_scope,
    }


def _make_raw_result(state: str = "CONTINUE", commit: str = "b" * 40) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": state,
        "disposition": "NONE",
        "category": "NONE",
        "summary": "Completed bounded supervisor startup cycle successfully",
        "commit": commit,
        "next_action": "Execute subsequent verified refactor cycle",
        "blockers": [],
        "gemini_worker_run_id": "worker.001",
        "gemini_reviewer_run_id": "reviewer.001",
        "proposal_sha256": "c" * 64,
    }


class _FakePopen:
    def __init__(self, cmd: list[str], *args: object, **kwargs: object) -> None:
        self.cmd = cmd
        self.pid = 98765
        self.returncode = 0
        self.kwargs = kwargs

    def __enter__(self) -> _FakePopen:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _setup_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    state_root = root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
    codex = root / "bin" / "codex"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text("#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)
    template_path = root / "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("# AUTOPILOT PROMPT BASE")
    return root, codex, state_root


def test_fixed_route_and_default_compatibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    state_root = repo / ".local/two-site-refactor/autopilot"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
    codex = repo / "bin/codex"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text("#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)

    r1 = _make_raw_result(state="CONTINUE", commit="b" * 40)
    r1.update({"head_before": "1" * 40, "head_after": "b" * 40, "head_changed": True, "advisories": []})
    r2 = _make_raw_result(state="COMPLETE", commit="c" * 40)
    r2.update({"head_before": "b" * 40, "head_after": "c" * 40, "head_changed": True, "advisories": []})

    child_receipts = [r1, r2]
    observed_child_kwargs: list[dict[str, object]] = []
    events: list[str] = []

    def fake_run_cycle(
        _root: Path,
        _codex: Path,
        s_root: Path,
        cycle: int,
        _timeout: int,
        recovery: dict[str, object] | None = None,
        continuation: dict | None = None,
        **kwargs: object,
    ) -> tuple[Path, dict]:
        events.append(f"cycle_{cycle}")
        observed_child_kwargs.append(dict(kwargs))
        run_dir = s_root / "runs" / f"cycle-{cycle:04d}-20260923T000000Z-{os.getpid()}"
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt = child_receipts[cycle - 1]
        return run_dir, receipt

    def spy_delegate(_root: Path, _state_root: Path) -> None:
        events.append("delegate")

    try:
        import scripts.two_site_codex_autopilot as ap_mod
    except ModuleNotFoundError:
        import two_site_codex_autopilot as ap_mod  # type: ignore[no-redef]

    orig_write_state = ap_mod.write_supervisor_state

    def spied_write_state(s_root: Path, state_name: str, *args: object, **kwargs: object) -> None:
        if state_name == "RUNNING":
            events.append("running")
        orig_write_state(s_root, state_name, *args, **kwargs)

    heads = ["1" * 40, "b" * 40, "c" * 40]
    head_iter = iter(heads)
    monkeypatch.setattr(ap_mod, "git", lambda *args: next(head_iter, "c" * 40))
    monkeypatch.setattr(ap_mod, "ref_snapshot", lambda _root: {ref: "2" * 40 for ref in PROTECTED_REFS})
    monkeypatch.setattr(ap_mod, "persisted_continuation", lambda *args: None)
    monkeypatch.setattr(ap_mod, "prune_runs", lambda *args: [])
    monkeypatch.setattr(ap_mod, "_validate_legacy_preparation_startup", spy_delegate)
    monkeypatch.setattr(ap_mod, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(ap_mod, "write_supervisor_state", spied_write_state)

    monkeypatch.setenv("ROUTE", "tampered_env_route")
    monkeypatch.setenv("TWO_SITE_PREPARATION_ROUTE", "tampered_env_prep_route")

    rc = run_loop(repo, codex, max_cycles=5, timeout=300, once=False, preparation_route="legacy")
    assert rc == 0
    assert events.count("delegate") == 1
    first_running_index = events.index("running")
    delegate_index = events.index("delegate")
    assert delegate_index < first_running_index
    assert len(observed_child_kwargs) == 2
    assert observed_child_kwargs[0] == {"preparation_route": "legacy"}
    assert observed_child_kwargs[1] == {"preparation_route": "legacy"}

    old_child_called = False

    def old_fake_child(
        _r: Path, _c: Path, s_root: Path, cycle: int, _t: int,
        recovery: dict[str, object] | None = None,
        continuation: dict | None = None,
    ) -> tuple[Path, dict]:
        nonlocal old_child_called
        old_child_called = True
        run_dir = s_root / "runs" / f"cycle-{cycle:04d}-20260923T000000Z-{os.getpid()}"
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return run_dir, r2

    events.clear()
    monkeypatch.setattr(ap_mod, "run_cycle", old_fake_child)
    rc_default = run_loop(repo, codex, max_cycles=1, timeout=300, once=True)
    assert rc_default == 0
    assert old_child_called is True
    assert "delegate" not in events

    with pytest.raises(AutopilotError, match="invalid preparation route"):
        run_loop(repo, codex, 1, 300, True, preparation_route="invalid_route")
    with pytest.raises(AutopilotError, match="invalid preparation route"):
        run_loop(repo, codex, 1, 300, True, preparation_route=123)  # type: ignore[arg-type]
    with pytest.raises(AutopilotError, match="invalid preparation route"):
        run_cycle(repo, codex, state_root, 1, 300, preparation_route="unsupported")
    with pytest.raises(AutopilotError, match="invalid preparation route"):
        run_cycle(repo, codex, state_root, 1, 300, preparation_route=None)  # type: ignore[arg-type]


def test_real_run_cycle_selected_legacy_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    scope = before["assignment_scope"]
    assert isinstance(scope, AssignmentScope)

    try:
        import scripts.two_site_cycle_metrics as metrics_mod
    except ModuleNotFoundError:
        import two_site_cycle_metrics as metrics_mod  # type: ignore[no-redef]

    try:
        import scripts.two_site_codex_autopilot as ap_mod
    except ModuleNotFoundError:
        import two_site_codex_autopilot as ap_mod  # type: ignore[no-redef]

    events: list[str] = []
    final_repo = dict(before, head="b" * 40)

    def fake_validate_repo(_r: Path, require_clean: bool = True) -> dict[str, object]:
        events.append("validate_repo")
        return final_repo if "validate_transition" in events else before

    tamper_cycle_2 = False

    def fake_capture(process: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        events.append("child_capture")
        run_dir_path = _out.parent
        if tamper_cycle_2:
            startup_file = run_dir_path / "preparation-startup.json"
            tampered_data = json.loads(startup_file.read_text())
            tampered_data["route"] = "tampered_route"
            startup_file.write_text(json.dumps(tampered_data))
        (run_dir_path / "result.json").write_text(json.dumps(_make_raw_result()))

    def fake_validate_transition(_r: Path, _bef: dict, receipt: dict) -> dict[str, object]:
        events.append("validate_transition")
        res = dict(receipt)
        res["head_before"] = _bef["head"]
        res["head_after"] = receipt.get("commit") or _bef["head"]
        res["head_changed"] = res["head_before"] != res["head_after"]
        return res

    observed_env: dict[str, str] = {}
    observed_startup_bytes: bytes = b""
    observed_mode: int = 0

    class SpiedPopen(_FakePopen):
        def __enter__(self) -> SpiedPopen:
            events.append("child_open")
            nonlocal observed_env, observed_startup_bytes, observed_mode
            env = self.kwargs.get("env")
            if isinstance(env, dict):
                observed_env = dict(env)
            for item in (state_root / "runs").iterdir():
                startup_p = item / "preparation-startup.json"
                if startup_p.exists():
                    observed_startup_bytes = startup_p.read_bytes()
                    observed_mode = stat.S_IMODE(startup_p.stat().st_mode)
            return self

    monkeypatch.setattr(ap_mod, "validate_repository", fake_validate_repo)
    monkeypatch.setattr(
        ap_mod,
        "codex_identity",
        lambda _c, _t: {
            "path": str(codex),
            "version": EXPECTED_CODEX_CLI_VERSION,
            "sha256": "4" * 64,
            "model": "gpt-6-sol",
            "reasoning": "xhigh",
            "runtime": {},
        },
    )
    monkeypatch.setattr(ap_mod, "file_sha256", lambda _p: "4" * 64)
    monkeypatch.setattr(ap_mod, "codex_runtime_identity", lambda _c: {})
    monkeypatch.setattr(ap_mod.subprocess, "Popen", SpiedPopen)
    monkeypatch.setattr(ap_mod, "capture_bounded_process", fake_capture)
    monkeypatch.setattr(ap_mod, "validate_transition", fake_validate_transition)
    monkeypatch.setattr(ap_mod, "_heartbeat_running_cycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(metrics_mod, "record_cycle", lambda *args, **kwargs: None)

    run_dir, result = run_cycle(root, codex, state_root, 1, 300, preparation_route="legacy")
    assert events == ["validate_repo", "child_open", "child_capture", "validate_transition", "validate_repo"]
    assert observed_mode == 0o600
    assert len(observed_startup_bytes) <= 4096
    parsed_snapshot = json.loads(observed_startup_bytes.decode("utf-8"))
    expected_startup = PreparationStartup(1, "legacy", run_dir.name, str(before["head"]), scope.sha256)
    assert parsed_snapshot == expected_startup.to_mapping()
    assert parsed_snapshot["route"] == "legacy"
    assert observed_env.get("TWO_SITE_PREPARATION_STARTUP_SHA256") == expected_startup.fingerprint
    assert observed_env.get("TWO_SITE_AUTOPILOT_RUN_ID") == run_dir.name
    assert "ROUTE" not in observed_env
    assert "TWO_SITE_PREPARATION_ROUTE" not in observed_env

    status = json.loads((run_dir / "status.json").read_text())
    assert status["preparation_startup"] == expected_startup.to_mapping()
    assert status["preparation_startup_sha256"] == expected_startup.fingerprint
    assert status["state"] == "CONTINUE"
    assert result["commit"] == "b" * 40

    tamper_cycle_2 = True
    events.clear()
    with pytest.raises(AutopilotError, match="preparation startup"):
        run_cycle(root, codex, state_root, 2, 300, preparation_route="legacy")
    assert "validate_transition" not in events


def test_cli_canonical_state_check_and_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    repo_dir = tmp_path / "canonical_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    cli_state_root = repo_dir / ".local" / "two-site-refactor" / "autopilot"
    cli_state_root.mkdir(parents=True, exist_ok=True)
    (cli_state_root / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
    codex_bin = repo_dir / "bin/codex"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("#!/bin/sh\nexit 0\n")
    codex_bin.chmod(0o755)

    before = _make_before()
    readiness_count = 0

    try:
        import scripts.two_site_codex_autopilot as ap_mod
    except ModuleNotFoundError:
        import two_site_codex_autopilot as ap_mod  # type: ignore[no-redef]

    def fake_codex_identity(codex_p: Path, _temp_p: Path) -> dict[str, object]:
        nonlocal readiness_count
        readiness_count += 1
        return {
            "path": str(codex_p),
            "version": EXPECTED_CODEX_CLI_VERSION,
            "sha256": "4" * 64,
            "model": "gpt-6-sol",
            "reasoning": "xhigh",
            "runtime": {},
        }

    monkeypatch.setattr(ap_mod, "validate_repository", lambda *args, **kwargs: before)
    monkeypatch.setattr(ap_mod, "codex_identity", fake_codex_identity)

    delegate_calls: list[tuple[Path, Path]] = []

    def fake_delegate(r: Path, s: Path) -> None:
        delegate_calls.append((r, s))

    monkeypatch.setattr(ap_mod, "_validate_legacy_preparation_startup", fake_delegate)

    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["two_site_codex_autopilot.py", "--root", str(repo_dir), "--codex", str(codex_bin), "--check"])
    rc_default_check = main()
    assert rc_default_check == 0
    out_default_check, _ = capsys.readouterr()
    data_default_check = json.loads(out_default_check)
    assert data_default_check["state"] == "READY"
    assert len(delegate_calls) == 0
    assert readiness_count == 1

    lock_verified = False

    def lock_verifying_delegate(_r: Path, s: Path) -> None:
        nonlocal lock_verified
        lock_file = s / "autopilot.lock"
        fd = os.open(lock_file, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_verified = False
            except BlockingIOError:
                lock_verified = True
        finally:
            os.close(fd)

    monkeypatch.setattr(ap_mod, "_validate_legacy_preparation_startup", lock_verifying_delegate)
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["two_site_codex_autopilot.py", "--root", str(repo_dir), "--codex", str(codex_bin), "--check", "--preparation-route", "legacy"])
    rc_legacy_check = main()
    assert rc_legacy_check == 0
    assert lock_verified is True
    out_legacy_check, _ = capsys.readouterr()
    data_legacy_check = json.loads(out_legacy_check)
    assert data_legacy_check["state"] == "READY"
    assert readiness_count == 2

    def failing_delegate(_r: Path, _s: Path) -> None:
        raise AutopilotError("simulated broker gate failure")

    monkeypatch.setattr(ap_mod, "_validate_legacy_preparation_startup", failing_delegate)
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["two_site_codex_autopilot.py", "--root", str(repo_dir), "--codex", str(codex_bin), "--check", "--preparation-route", "legacy"])
    with pytest.raises(AutopilotError, match="simulated broker gate failure"):
        main()
    out_failing_check, _ = capsys.readouterr()
    assert "READY" not in out_failing_check
    assert readiness_count == 2

    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["two_site_codex_autopilot.py", "--root", str(repo_dir), "--preparation-route", "unsupported_route"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
    capsys.readouterr()

    stop_path = cli_state_root / "STOP"
    stop_path.write_text("STOP_SIGNAL\n")

    delegate_invoked = False
    child_invoked = False
    git_invoked = False

    def forbidden_delegate(*args: object, **kwargs: object) -> None:
        nonlocal delegate_invoked
        delegate_invoked = True

    def forbidden_child(*args: object, **kwargs: object) -> None:
        nonlocal child_invoked
        child_invoked = True

    def forbidden_git(*args: object, **kwargs: object) -> None:
        nonlocal git_invoked
        git_invoked = True

    monkeypatch.setattr(ap_mod, "_validate_legacy_preparation_startup", forbidden_delegate)
    monkeypatch.setattr(ap_mod, "run_cycle", forbidden_child)
    monkeypatch.setattr(ap_mod, "git", forbidden_git)

    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["two_site_codex_autopilot.py", "--root", str(repo_dir), "--codex", str(codex_bin), "--preparation-route", "legacy"])
    rc_stop = main()
    assert rc_stop == 0
    assert delegate_invoked is False
    assert child_invoked is False
    assert git_invoked is False

    out_stop, _ = capsys.readouterr()
    data_stop = json.loads(out_stop)
    assert data_stop["state"] == "STOPPED"
    status_file = cli_state_root / "status.json"
    status_data = json.loads(status_file.read_text())
    assert status_data["state"] == "STOPPED"
    stop_path.unlink()
    capsys.readouterr()

    broken_target = cli_state_root / "does_not_exist"
    stop_path.symlink_to(broken_target)

    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["two_site_codex_autopilot.py", "--root", str(repo_dir), "--codex", str(codex_bin), "--preparation-route", "legacy"])
    rc_broken = main()
    assert rc_broken == 2
    assert child_invoked is False
    assert delegate_invoked is False

    out_broken, _ = capsys.readouterr()
    data_broken = json.loads(out_broken)
    assert data_broken["state"] == "BLOCKED"
    stop_path.unlink()
    capsys.readouterr()
