from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from unittest.mock import MagicMock, patch

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import (
    EXPECTED_CODEX_CLI_VERSION,
    PROTECTED_REFS,
    CONTROL_PATHS,
    AutopilotError,
    prompt_for_cycle,
    run_cycle,
)
from scripts.two_site_preparation_contract import PreparationStartup


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


def test_run_cycle_publishes_validated_frozen_startup(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    scope = before["assignment_scope"]
    assert isinstance(scope, AssignmentScope)
    events: list[str] = []

    def fake_validate_repo(_r: Path, require_clean: bool = True) -> dict[str, object]:
        events.append("validate_repo")
        return before

    def fake_capture(process: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        events.append("child_capture")
        run_dir_path = _out.parent
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

    with patch("scripts.two_site_codex_autopilot.validate_repository", side_effect=fake_validate_repo), \
         patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"path": str(codex), "version": EXPECTED_CODEX_CLI_VERSION, "sha256": "4" * 64, "model": "gpt-6-sol", "reasoning": "xhigh", "runtime": {}}), \
         patch("scripts.two_site_codex_autopilot.file_sha256", return_value="4" * 64), \
         patch("scripts.two_site_codex_autopilot.codex_runtime_identity", return_value={}), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=SpiedPopen), \
         patch("scripts.two_site_codex_autopilot.capture_bounded_process", side_effect=fake_capture), \
         patch("scripts.two_site_codex_autopilot.validate_transition", side_effect=fake_validate_transition), \
         patch("scripts.two_site_codex_autopilot._heartbeat_running_cycle"), \
         patch("scripts.two_site_cycle_metrics.record_cycle", create=True):
        run_dir, result = run_cycle(root, codex, state_root, 1, 300)

    assert events == ["validate_repo", "child_open", "child_capture", "validate_transition"]
    assert observed_mode == 0o600
    assert len(observed_startup_bytes) <= 4096
    parsed_snapshot = json.loads(observed_startup_bytes.decode("utf-8"))
    expected_startup = PreparationStartup(1, "one_shot", run_dir.name, str(before["head"]), scope.sha256)
    assert parsed_snapshot == expected_startup.to_mapping()
    assert observed_env.get("TWO_SITE_PREPARATION_STARTUP_SHA256") == expected_startup.fingerprint
    assert observed_env.get("TWO_SITE_AUTOPILOT_RUN_ID") == run_dir.name
    assert observed_env.get("TWO_SITE_ASSIGNMENT_SHA256") == scope.sha256
    assert "ROUTE" not in observed_env
    assert "TWO_SITE_PREPARATION_ROUTE" not in observed_env

    status = json.loads((run_dir / "status.json").read_text())
    assert status["preparation_startup"] == expected_startup.to_mapping()
    assert status["preparation_startup_sha256"] == expected_startup.fingerprint
    assert status["state"] == "CONTINUE"
    assert result["commit"] == "b" * 40


@pytest.mark.parametrize(
    "mutation",
    ["route_change", "whitespace_rewrite", "delete", "symlink", "hardlink", "oversized"],
)
def test_startup_tampering_blocks_transition(tmp_path: Path, mutation: str) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    transition_mock = MagicMock()

    def fake_capture(process: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        run_dir_path = _out.parent
        (run_dir_path / "result.json").write_text(json.dumps(_make_raw_result()))
        startup_p = run_dir_path / "preparation-startup.json"
        if mutation == "route_change":
            mapping = json.loads(startup_p.read_text())
            mapping["route"] = "legacy"
            startup_p.write_bytes(json.dumps(mapping, separators=(",", ":")).encode("utf-8"))
        elif mutation == "whitespace_rewrite":
            startup_p.write_bytes(startup_p.read_bytes() + b" ")
        elif mutation == "delete":
            startup_p.unlink()
        elif mutation == "symlink":
            target = run_dir_path / "target.json"
            target.write_bytes(startup_p.read_bytes())
            startup_p.unlink()
            startup_p.symlink_to(target)
        elif mutation == "hardlink":
            other = run_dir_path / "other.json"
            os.link(startup_p, other)
        elif mutation == "oversized":
            startup_p.write_bytes(b"x" * 4097)

    with patch("scripts.two_site_codex_autopilot.validate_repository", return_value=before), \
         patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"path": str(codex), "version": EXPECTED_CODEX_CLI_VERSION, "sha256": "4" * 64, "model": "gpt-6-sol", "reasoning": "xhigh", "runtime": {}}), \
         patch("scripts.two_site_codex_autopilot.file_sha256", return_value="4" * 64), \
         patch("scripts.two_site_codex_autopilot.codex_runtime_identity", return_value={}), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=_FakePopen), \
         patch("scripts.two_site_codex_autopilot.capture_bounded_process", side_effect=fake_capture), \
         patch("scripts.two_site_codex_autopilot.validate_transition", transition_mock), \
         patch("scripts.two_site_codex_autopilot._heartbeat_running_cycle"), \
         patch("scripts.two_site_cycle_metrics.record_cycle", create=True):
        with pytest.raises(AutopilotError):
            run_cycle(root, codex, state_root, 1, 300)

    assert transition_mock.call_count == 0


def test_valid_commit_keeps_original_source_identity(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before_head = "1" * 40
    after_commit = "b" * 40
    before = _make_before(head=before_head)
    transition_calls: list[dict] = []

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(json.dumps(_make_raw_result(commit=after_commit)))

    def fake_validate_transition(_r: Path, _bef: dict, receipt: dict) -> dict[str, object]:
        transition_calls.append(dict(_bef))
        res = dict(receipt)
        res["head_before"] = _bef["head"]
        res["head_after"] = after_commit
        res["head_changed"] = True
        return res

    with patch("scripts.two_site_codex_autopilot.validate_repository", return_value=before), \
         patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"path": str(codex), "version": EXPECTED_CODEX_CLI_VERSION, "sha256": "4" * 64, "model": "gpt-6-sol", "reasoning": "xhigh", "runtime": {}}), \
         patch("scripts.two_site_codex_autopilot.file_sha256", return_value="4" * 64), \
         patch("scripts.two_site_codex_autopilot.codex_runtime_identity", return_value={}), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=_FakePopen), \
         patch("scripts.two_site_codex_autopilot.capture_bounded_process", side_effect=fake_capture), \
         patch("scripts.two_site_codex_autopilot.validate_transition", side_effect=fake_validate_transition), \
         patch("scripts.two_site_codex_autopilot._heartbeat_running_cycle"), \
         patch("scripts.two_site_cycle_metrics.record_cycle", create=True):
        run_dir, result = run_cycle(root, codex, state_root, 1, 300)

    assert len(transition_calls) == 1
    assert transition_calls[0]["head"] == before_head
    assert result["head_before"] == before_head
    assert result["head_after"] == after_commit
    assert result["commit"] == after_commit
    startup_data = json.loads((run_dir / "preparation-startup.json").read_text())
    assert startup_data["source_head"] == before_head
    status = json.loads((run_dir / "status.json").read_text())
    assert status["preparation_startup"]["source_head"] == before_head


def test_prompt_and_environment_have_no_route_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    before_keys = set(before.keys())

    prompt_default = prompt_for_cycle(root, 1, before)
    ctx_default = json.loads(prompt_default.split("\n\nSUPERVISOR_CONTEXT_JSON:\n")[1])
    assert "preparation_startup" not in ctx_default
    assert "preparation_startup_sha256" not in ctx_default
    assert set(before.keys()) == before_keys

    startup = PreparationStartup(1, "one_shot", "cycle-0001-20260922T210000Z-12345", str(before["head"]), "a" * 64)
    prompt_explicit = prompt_for_cycle(root, 1, before, preparation_startup=startup)
    ctx_explicit = json.loads(prompt_explicit.split("\n\nSUPERVISOR_CONTEXT_JSON:\n")[1])
    assert ctx_explicit["preparation_startup"] == startup.to_mapping()
    assert ctx_explicit["preparation_startup_sha256"] == startup.fingerprint
    assert set(before.keys()) == before_keys

    with pytest.raises(AutopilotError):
        prompt_for_cycle(root, 1, before, preparation_startup="invalid_type")  # type: ignore[arg-type]

    monkeypatch.setenv("ROUTE", "legacy")
    monkeypatch.setenv("PREPARATION_ROUTE", "legacy")
    monkeypatch.setenv("TWO_SITE_PREPARATION_STARTUP_SHA256", "deadbeef" * 8)

    captured_env: dict[str, str] = {}

    class EnvCapturePopen(_FakePopen):
        def __enter__(self) -> EnvCapturePopen:
            nonlocal captured_env
            env = self.kwargs.get("env")
            if isinstance(env, dict):
                captured_env = dict(env)
            return self

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(json.dumps(_make_raw_result()))

    def fake_validate_transition(_r: Path, _bef: dict, receipt: dict) -> dict[str, object]:
        res = dict(receipt)
        res["head_before"] = _bef["head"]
        res["head_after"] = receipt.get("commit") or _bef["head"]
        res["head_changed"] = res["head_before"] != res["head_after"]
        return res

    with patch("scripts.two_site_codex_autopilot.validate_repository", return_value=before), \
         patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"path": str(codex), "version": EXPECTED_CODEX_CLI_VERSION, "sha256": "4" * 64, "model": "gpt-6-sol", "reasoning": "xhigh", "runtime": {}}), \
         patch("scripts.two_site_codex_autopilot.file_sha256", return_value="4" * 64), \
         patch("scripts.two_site_codex_autopilot.codex_runtime_identity", return_value={}), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=EnvCapturePopen), \
         patch("scripts.two_site_codex_autopilot.capture_bounded_process", side_effect=fake_capture), \
         patch("scripts.two_site_codex_autopilot.validate_transition", side_effect=fake_validate_transition), \
         patch("scripts.two_site_codex_autopilot._heartbeat_running_cycle"), \
         patch("scripts.two_site_cycle_metrics.record_cycle", create=True):
        run_dir, _ = run_cycle(root, codex, state_root, 1, 300)

    actual_startup = PreparationStartup(1, "one_shot", run_dir.name, str(before["head"]), "a" * 64)
    assert captured_env["TWO_SITE_PREPARATION_STARTUP_SHA256"] == actual_startup.fingerprint
    assert "TWO_SITE_PREPARATION_ROUTE" not in captured_env
    status = json.loads((run_dir / "status.json").read_text())
    assert status["preparation_startup"]["route"] == "one_shot"
    assert set(before.keys()) == before_keys


def test_snapshot_publication_refuses_existing_and_unsafe_targets(tmp_path: Path) -> None:
    root_col, codex_col, state_root_col = _setup_workspace(tmp_path / "collision")
    before_col = _make_before()
    child_called = False

    class GuardPopen(_FakePopen):
        def __enter__(self) -> GuardPopen:
            nonlocal child_called
            child_called = True
            return self

    precreated_content = b'{"preexisting":true}'
    orig_mkdir = Path.mkdir

    def collision_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        orig_mkdir(self, *args, **kwargs)
        if self.parent.name == "runs":
            (self / "preparation-startup.json").write_bytes(precreated_content)

    with patch("scripts.two_site_codex_autopilot.validate_repository", return_value=before_col), \
         patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"path": str(codex_col), "version": EXPECTED_CODEX_CLI_VERSION, "sha256": "4" * 64, "model": "gpt-6-sol", "reasoning": "xhigh", "runtime": {}}), \
         patch("scripts.two_site_codex_autopilot.file_sha256", return_value="4" * 64), \
         patch("scripts.two_site_codex_autopilot.codex_runtime_identity", return_value={}), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=GuardPopen), \
         patch.object(Path, "mkdir", side_effect=collision_mkdir, autospec=True):
        with pytest.raises(AutopilotError):
            run_cycle(root_col, codex_col, state_root_col, 1, 300)

    assert child_called is False
    for item in (state_root_col / "runs").iterdir():
        sf = item / "preparation-startup.json"
        if sf.exists():
            assert sf.read_bytes() == precreated_content

    root_sym, codex_sym, state_root_sym = _setup_workspace(tmp_path / "symlink")
    before_sym = _make_before()
    child_called = False

    def symlink_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        orig_mkdir(self, *args, **kwargs)
        if self.parent.name == "runs":
            alias = self.parent / (self.name + ".real")
            orig_mkdir(alias, mode=0o700)
            os.rmdir(self)
            os.symlink(alias, self)

    with patch("scripts.two_site_codex_autopilot.validate_repository", return_value=before_sym), \
         patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"path": str(codex_sym), "version": EXPECTED_CODEX_CLI_VERSION, "sha256": "4" * 64, "model": "gpt-6-sol", "reasoning": "xhigh", "runtime": {}}), \
         patch("scripts.two_site_codex_autopilot.file_sha256", return_value="4" * 64), \
         patch("scripts.two_site_codex_autopilot.codex_runtime_identity", return_value={}), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=GuardPopen), \
         patch.object(Path, "mkdir", side_effect=symlink_mkdir, autospec=True):
        with pytest.raises(AutopilotError):
            run_cycle(root_sym, codex_sym, state_root_sym, 1, 300)

    assert child_called is False

    root_ext, codex_ext, _ = _setup_workspace(tmp_path / "external_repo")
    before_ext = _make_before()
    child_called = False
    external_state = tmp_path / "external_state"
    external_state.mkdir(parents=True, exist_ok=True)
    (external_state / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
    with patch("scripts.two_site_codex_autopilot.validate_repository", return_value=before_ext), \
         patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"path": str(codex_ext), "version": EXPECTED_CODEX_CLI_VERSION, "sha256": "4" * 64, "model": "gpt-6-sol", "reasoning": "xhigh", "runtime": {}}), \
         patch("scripts.two_site_codex_autopilot.file_sha256", return_value="4" * 64), \
         patch("scripts.two_site_codex_autopilot.codex_runtime_identity", return_value={}), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=GuardPopen):
        with pytest.raises(AutopilotError):
            run_cycle(root_ext, codex_ext, external_state, 1, 300)

    assert child_called is False
