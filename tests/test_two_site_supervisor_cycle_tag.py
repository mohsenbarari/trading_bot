from __future__ import annotations

import os
from pathlib import Path
import time
from unittest import mock

import pytest

try:
    from scripts.two_site_assignment_scope import AssignmentScope
    from scripts.two_site_codex_autopilot import (
        RUN_ID,
        AutopilotError,
        run_cycle,
    )
except ModuleNotFoundError:
    from two_site_assignment_scope import AssignmentScope  # type: ignore[no-redef]
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        RUN_ID,
        AutopilotError,
        run_cycle,
    )


class _HaltCycle(Exception):
    """Sentinel exception to halt cycle execution at child process invocation."""


def _setup_mock_repo(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
    codex = tmp_path / "codex"
    codex.touch(mode=0o755)

    mock_scope = mock.MagicMock(spec=AssignmentScope)
    mock_scope.assignment_id = "test-assignment-id-1234"
    mock_scope.stage_id = "P1-00b"
    mock_scope.sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    mock_scope.expires_at = 1800000000
    mock_scope.allowed_paths = ["scripts/two_site_codex_autopilot.py"]
    mock_scope.forbidden_paths = []

    repo_state = {
        "head": "1234567890123456789012345678901234567890",
        "status": "",
        "protected_refs": {},
        "control_blobs": {},
        "assignment_scope": mock_scope,
    }
    return root, state_root, codex, mock_scope, repo_state


def test_supervisor_cycle_tag_canonical_environment_injection(tmp_path: Path):
    root, state_root, codex, mock_scope, repo_state = _setup_mock_repo(tmp_path)
    captured_env: dict[str, str] = {}

    def fake_popen(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        raise _HaltCycle("intercepted child process launch")

    with mock.patch("scripts.two_site_codex_autopilot.validate_repository", return_value=repo_state), \
         mock.patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"sha256": "dummy", "runtime": {}}), \
         mock.patch("scripts.two_site_codex_autopilot.bounded_file_text", return_value="TEMPLATE_PROMPT_BODY"), \
         mock.patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=fake_popen):
        with pytest.raises(_HaltCycle):
            run_cycle(root, codex, state_root, cycle=1, timeout=300)

    runs = list((state_root / "runs").iterdir())
    assert len(runs) == 1
    run_dir = runs[0]

    cycle_tag = captured_env.get("TWO_SITE_AUTOPILOT_RUN_ID")
    assert cycle_tag is not None
    assert cycle_tag == run_dir.name
    assert not os.path.isabs(cycle_tag)
    assert "/" not in cycle_tag
    assert "\\" not in cycle_tag
    assert RUN_ID.fullmatch(cycle_tag) is not None

    assert captured_env.get("APP_ENV_FILE") == "config/unit-test.env.example"
    assert captured_env.get("PYTHONDONTWRITEBYTECODE") == "1"
    assert captured_env.get("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED") == "1"
    assert captured_env.get("TWO_SITE_ASSIGNMENT_ID") == mock_scope.assignment_id
    assert captured_env.get("TWO_SITE_ASSIGNMENT_SHA256") == mock_scope.sha256


def test_supervisor_cycle_tag_overwrites_parent_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, state_root, codex, mock_scope, repo_state = _setup_mock_repo(tmp_path)
    monkeypatch.setenv("TWO_SITE_AUTOPILOT_RUN_ID", "untrusted-task-or-model-output")
    captured_env: dict[str, str] = {}

    def fake_popen(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        raise _HaltCycle("intercepted child process launch")

    with mock.patch("scripts.two_site_codex_autopilot.validate_repository", return_value=repo_state), \
         mock.patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"sha256": "dummy", "runtime": {}}), \
         mock.patch("scripts.two_site_codex_autopilot.bounded_file_text", return_value="TEMPLATE_PROMPT_BODY"), \
         mock.patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=fake_popen):
        with pytest.raises(_HaltCycle):
            run_cycle(root, codex, state_root, cycle=2, timeout=300)

    runs = list((state_root / "runs").iterdir())
    assert len(runs) == 1
    run_dir = runs[0]

    cycle_tag = captured_env.get("TWO_SITE_AUTOPILOT_RUN_ID")
    assert cycle_tag != "untrusted-task-or-model-output"
    assert cycle_tag == run_dir.name


def test_supervisor_cycle_tag_absence_from_prompt_text(tmp_path: Path):
    root, state_root, codex, _mock_scope, repo_state = _setup_mock_repo(tmp_path)

    def fake_popen(*args, **kwargs):
        raise _HaltCycle("intercepted child process launch")

    with mock.patch("scripts.two_site_codex_autopilot.validate_repository", return_value=repo_state), \
         mock.patch("scripts.two_site_codex_autopilot.codex_identity", return_value={"sha256": "dummy", "runtime": {}}), \
         mock.patch("scripts.two_site_codex_autopilot.bounded_file_text", return_value="AUTOPILOT_PROMPT_HEADER"), \
         mock.patch("scripts.two_site_codex_autopilot.subprocess.Popen", side_effect=fake_popen):
        with pytest.raises(_HaltCycle):
            run_cycle(root, codex, state_root, cycle=1, timeout=300)

    runs = list((state_root / "runs").iterdir())
    assert len(runs) == 1
    run_dir = runs[0]
    prompt_file = run_dir / "prompt.txt"
    assert prompt_file.exists()
    prompt_content = prompt_file.read_text(encoding="utf-8")

    assert "TWO_SITE_AUTOPILOT_RUN_ID" not in prompt_content
    assert run_dir.name not in prompt_content


def test_supervisor_cycle_tag_local_rejection_negative_cycle(tmp_path: Path):
    root, state_root, codex, _mock_scope, repo_state = _setup_mock_repo(tmp_path)
    mock_popen = mock.MagicMock()

    with mock.patch("scripts.two_site_codex_autopilot.validate_repository", return_value=repo_state), \
         mock.patch("scripts.two_site_codex_autopilot.subprocess.Popen", mock_popen):
        with pytest.raises(AutopilotError, match="invalid cycle run ID"):
            run_cycle(root, codex, state_root, cycle=-1, timeout=300)

    mock_popen.assert_not_called()


def test_supervisor_cycle_tag_local_rejection_malformed_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, state_root, codex, _mock_scope, repo_state = _setup_mock_repo(tmp_path)
    monkeypatch.setattr(time, "strftime", lambda *args: "not-a-timestamp")
    mock_popen = mock.MagicMock()

    with mock.patch("scripts.two_site_codex_autopilot.validate_repository", return_value=repo_state), \
         mock.patch("scripts.two_site_codex_autopilot.subprocess.Popen", mock_popen):
        with pytest.raises(AutopilotError, match="invalid cycle run ID"):
            run_cycle(root, codex, state_root, cycle=1, timeout=300)

    mock_popen.assert_not_called()
