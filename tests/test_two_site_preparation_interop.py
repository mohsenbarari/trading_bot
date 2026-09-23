from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any

import pytest

import scripts.two_site_codex_autopilot as ap
import scripts.two_site_cycle_metrics as cycle_metrics
from scripts.two_site_preparation_contract import (
    PreparationConfigError,
    PreparationStartup,
)
import scripts.two_site_preparation_io as prep_io
from tests.test_two_site_supervisor_preparation_startup import (
    _FakePopen,
    _make_assignment_scope,
    _make_before,
    _make_raw_result,
)


@pytest.fixture
def repo(tmp_path: Path):
    root = tmp_path.resolve()

    def git_fn(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL, text=True
        ).strip()

    git_fn("init")
    git_fn("config", "user.email", "offline@example.invalid")
    git_fn("config", "user.name", "Offline Fixture")
    (root / ".gitignore").write_text(".local/\n")
    (root / "gap.md").write_text("# Gap\nCharacterize method.\n")
    (root / "code.py").write_text("def f(): return 1\n")
    (root / "tests").mkdir()
    git_fn("add", "--", ".gitignore", "gap.md", "code.py")
    git_fn("commit", "-m", "init")
    return root, git_fn


@pytest.fixture
def interop_fixture(repo, monkeypatch):
    root, git_fn = repo
    real_head = git_fn("rev-parse", "HEAD")
    real_scope = _make_assignment_scope()
    before = _make_before(head=real_head, scope=real_scope)

    state_root = root / ".local" / "two-site-refactor" / "autopilot"
    (state_root / "runs").mkdir(parents=True, exist_ok=True)

    codex = root / ".local" / "tools" / "codex"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text("#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)

    monkeypatch.setattr(prep_io, "load_active_assignment_scope", lambda r: real_scope)

    orig_prep_io_git = prep_io.git
    git_call_count = [0]

    def spied_git(*args: Any, **kwargs: Any) -> str:
        git_call_count[0] += 1
        return orig_prep_io_git(*args, **kwargs)

    monkeypatch.setattr(prep_io, "git", spied_git)

    orig_popen = subprocess.Popen
    codex_calls: list[list[str]] = []

    class DispatchingPopen:
        def __new__(cls, cmd: Any, *args: Any, **kwargs: Any) -> Any:
            cmd0 = (
                str(cmd[0])
                if isinstance(cmd, (list, tuple)) and cmd
                else str(cmd).split()[0]
            )
            if cmd0 in (str(codex), str(codex.resolve())):
                codex_calls.append(cmd)
                return _FakePopen(cmd, *args, **kwargs)
            return orig_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", DispatchingPopen)

    monkeypatch.setattr(
        cycle_metrics, "record_cycle", lambda *args, **kwargs: None, raising=False
    )

    monkeypatch.setattr(ap, "validate_repository", lambda r, require_clean=True: before)
    codex_id = {
        "path": str(codex),
        "version": ap.EXPECTED_CODEX_CLI_VERSION,
        "sha256": "4" * 64,
        "model": "gpt-6-sol",
        "reasoning": "xhigh",
        "runtime": {},
    }
    monkeypatch.setattr(ap, "codex_identity", lambda c, r: codex_id)
    monkeypatch.setattr(ap, "file_sha256", lambda p: "4" * 64)
    monkeypatch.setattr(ap, "codex_runtime_identity", lambda p: {})
    monkeypatch.setattr(ap, "prompt_for_cycle", lambda *args, **kwargs: "PROMPT_CYCLE")
    monkeypatch.setattr(ap, "_heartbeat_running_cycle", lambda *args, **kwargs: None)

    transition_calls: list[dict[str, Any]] = []

    def fake_validate_transition(
        r: Path, bef: dict[str, Any], receipt: dict[str, Any]
    ) -> dict[str, Any]:
        transition_calls.append(receipt)
        res = dict(receipt)
        res["head_before"] = bef["head"]
        res["head_after"] = receipt.get("commit") or bef["head"]
        res["head_changed"] = res["head_before"] != res["head_after"]
        return res

    monkeypatch.setattr(ap, "validate_transition", fake_validate_transition)

    return {
        "root": root,
        "codex": codex,
        "state_root": state_root,
        "real_head": real_head,
        "real_scope": real_scope,
        "git_call_count": git_call_count,
        "codex_calls": codex_calls,
        "transition_calls": transition_calls,
    }


def test_supervisor_to_consumer_interop_success(interop_fixture, monkeypatch) -> None:
    ctx = interop_fixture
    root: Path = ctx["root"]
    codex: Path = ctx["codex"]
    state_root: Path = ctx["state_root"]
    real_head: str = ctx["real_head"]
    real_scope = ctx["real_scope"]
    git_call_count: list[int] = ctx["git_call_count"]
    codex_calls: list[list[str]] = ctx["codex_calls"]
    transition_calls: list[dict[str, Any]] = ctx["transition_calls"]

    consumer_called = [False]
    raw_result = _make_raw_result()
    orig_before = ap.validate_repository(root)
    after = _make_before(head=raw_result["commit"], scope=real_scope)
    after.update({
        "head": raw_result["commit"],
        "status": "",
        "protected_refs": orig_before["protected_refs"],
        "control_blobs": {
            (p.as_posix() if hasattr(p, "as_posix") else str(p)): "0" * 40
            for p in ap.CONTROL_PATHS
        },
        "assignment_scope": real_scope,
    })
    repo_snapshots = [orig_before, after]
    monkeypatch.setattr(
        ap,
        "validate_repository",
        lambda r, require_clean=True: repo_snapshots.pop(0) if repo_snapshots else after,
    )

    def fake_capture(
        process: _FakePopen,
        _in: bytes,
        _out: Path,
        _err: Path,
        _t: int,
        max_output: int = 0,
    ) -> None:
        run_dir = _out.parent
        env = process.kwargs.get("env")
        assert isinstance(env, dict)

        startup = prep_io.load_preparation_startup(root, environment=env)
        assert startup is not None
        consumer_called[0] = True

        prep_io.verify_preparation_startup(root, startup, environment=env)

        assert startup.route == "one_shot"
        assert startup.cycle_id == run_dir.name
        assert startup.source_head == real_head
        assert startup.assignment_sha256 == real_scope.sha256

        startup_file = run_dir / "preparation-startup.json"
        assert startup_file.exists()
        assert stat.S_IMODE(startup_file.stat().st_mode) == 0o600
        startup_bytes = startup_file.read_bytes()
        canonical_bytes = json.dumps(
            startup.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        assert startup_bytes == canonical_bytes
        assert startup.fingerprint == hashlib.sha256(canonical_bytes).hexdigest()

        assert env.get("TWO_SITE_AUTOPILOT_RUN_ID") == run_dir.name
        assert env.get("TWO_SITE_PREPARATION_STARTUP_SHA256") == startup.fingerprint
        assert env.get("TWO_SITE_ASSIGNMENT_SHA256") == real_scope.sha256
        assert env.get("TWO_SITE_ASSIGNMENT_ID") == real_scope.assignment_id
        assert env.get("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED") == "1"
        assert "ROUTE" not in env
        assert "TWO_SITE_PREPARATION_ROUTE" not in env

        (run_dir / "result.json").write_text(json.dumps(raw_result))

    monkeypatch.setattr(ap, "capture_bounded_process", fake_capture)

    run_dir, result = ap.run_cycle(root, codex, state_root, 1, 300)

    assert result["state"] == "CONTINUE"
    assert len(codex_calls) == 1
    assert consumer_called[0] is True
    assert git_call_count[0] >= 2
    assert len(transition_calls) == 1

    status = json.loads((run_dir / "status.json").read_text())
    expected_startup = PreparationStartup(
        1, "one_shot", run_dir.name, real_head, real_scope.sha256
    )
    assert status["preparation_startup"] == expected_startup.to_mapping()
    assert status["preparation_startup_sha256"] == expected_startup.fingerprint
    assert status["state"] == "CONTINUE"


@pytest.mark.parametrize("tamper_mode", ["whitespace", "mismatched_digest"])
def test_supervisor_to_consumer_interop_tamper_rejected(
    interop_fixture, monkeypatch, tamper_mode: str
) -> None:
    ctx = interop_fixture
    root: Path = ctx["root"]
    codex: Path = ctx["codex"]
    state_root: Path = ctx["state_root"]
    codex_calls: list[list[str]] = ctx["codex_calls"]
    transition_calls: list[dict[str, Any]] = ctx["transition_calls"]

    def fake_capture(
        process: _FakePopen,
        _in: bytes,
        _out: Path,
        _err: Path,
        _t: int,
        max_output: int = 0,
    ) -> None:
        run_dir = _out.parent
        env = process.kwargs.get("env")
        assert isinstance(env, dict)

        startup_file = run_dir / "preparation-startup.json"
        assert startup_file.exists()

        if tamper_mode == "whitespace":
            startup_file.write_bytes(startup_file.read_bytes() + b" ")
        elif tamper_mode == "mismatched_digest":
            data = json.loads(startup_file.read_text())
            data["source_head"] = "0" * 40
            tampered_bytes = json.dumps(
                data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("ascii")
            startup_file.write_bytes(tampered_bytes)

        with pytest.raises(PreparationConfigError):
            prep_io.load_preparation_startup(root, environment=env)

        (run_dir / "result.json").write_text(json.dumps(_make_raw_result()))

    monkeypatch.setattr(ap, "capture_bounded_process", fake_capture)

    with pytest.raises(ap.AutopilotError):
        ap.run_cycle(root, codex, state_root, 1, 300)

    assert len(transition_calls) == 0
    assert len(codex_calls) == 1

    runs = list((state_root / "runs").iterdir())
    assert len(runs) == 1
    status = json.loads((runs[0] / "status.json").read_text())
    assert status["state"] == "FAILED"
    assert "AutopilotError" in status["error"]
