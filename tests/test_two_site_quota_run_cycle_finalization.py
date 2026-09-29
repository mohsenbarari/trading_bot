"""Hermetic tests for autopilot quota continuation run cycle finalization hook.

Validates the default-disabled quota finalization hook in scripts/two_site_codex_autopilot.py.
Hook ordering ensures finalization occurs before terminal status and accepted_checkpoint.json
(the initial RUNNING status is published prior to child execution).

Open Safety Gates:
As documented in docs/refactor/two-server/AE03_C5E_LIVE_HOOK_PATCH_20260929.md, restart custody
and monitor/status-write failure gates remain OPEN and unresolved.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Canonical hermetic network fence bootstrap - must run BEFORE application imports
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (  # noqa: E402
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from scripts.two_site_assignment_scope import AssignmentScope  # noqa: E402
from scripts.two_site_codex_autopilot import (  # noqa: E402
    EXPECTED_CODEX_CLI_VERSION,
    AutopilotError,
    QuotaFinalizationHardStopError,
    run_cycle,
    run_loop,
)
from scripts.two_site_quota_supervisor_finalization import (  # noqa: E402
    SupervisorFinalizationAmbiguousError,
)
from tests.test_two_site_supervisor_preparation_startup import (  # noqa: E402
    _FakePopen,
    _make_before,
    _make_raw_result,
    _setup_workspace,
)

VALID_ORIGINAL_RUN_ID = "cycle-0001-20260922T210000Z-12345"


def _fake_validate_transition(_r: Path, _bef: dict, receipt: dict) -> dict[str, object]:
    res = dict(receipt)
    res["head_before"] = _bef["head"]
    res["head_after"] = receipt.get("commit") or _bef["head"]
    res["head_changed"] = res["head_before"] != res["head_after"]
    return res


@contextmanager
def _mock_cycle_environment(
    codex: Path,
    validate_repo_side_effect: Any = None,
    capture_side_effect: Any = None,
):
    with patch(
        "scripts.two_site_codex_autopilot.validate_repository",
        side_effect=validate_repo_side_effect,
    ), patch(
        "scripts.two_site_codex_autopilot.codex_identity",
        return_value={
            "path": str(codex),
            "version": EXPECTED_CODEX_CLI_VERSION,
            "sha256": "4" * 64,
            "model": "gpt-6-sol",
            "reasoning": "xhigh",
            "runtime": {},
        },
    ), patch(
        "scripts.two_site_codex_autopilot.file_sha256",
        return_value="4" * 64,
    ), patch(
        "scripts.two_site_codex_autopilot.codex_runtime_identity",
        return_value={},
    ), patch(
        "scripts.two_site_codex_autopilot.subprocess.Popen",
        side_effect=_FakePopen,
    ), patch(
        "scripts.two_site_codex_autopilot.capture_bounded_process",
        side_effect=capture_side_effect,
    ), patch(
        "scripts.two_site_codex_autopilot.validate_transition",
        side_effect=_fake_validate_transition,
    ), patch(
        "scripts.two_site_codex_autopilot._heartbeat_running_cycle",
    ), patch(
        "scripts.two_site_cycle_metrics.record_cycle",
        create=True,
    ):
        yield


@pytest.mark.parametrize(
    "bad_id",
    [
        12345,
        ["cycle-0001-20260922T210000Z-12345"],
        {"run_id": "cycle-0001"},
        "cycle-bad-run-id",
        "../../etc/passwd",
        "SECRET_LEAK_TOKEN_98765",
        "",
    ],
)
def test_invalid_run_id_fails_before_io_and_does_not_leak_identifier(
    tmp_path: Path, bad_id: Any
) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    mock_validate_repo = MagicMock()
    mock_popen = MagicMock()

    with patch(
        "scripts.two_site_codex_autopilot.validate_repository",
        mock_validate_repo,
    ), patch(
        "scripts.two_site_codex_autopilot.subprocess.Popen",
        mock_popen,
    ):
        with pytest.raises(QuotaFinalizationHardStopError) as exc_info:
            run_cycle(
                root,
                codex,
                state_root,
                1,
                300,
                quota_original_controller_run_id=bad_id,
            )

    assert "invalid quota_original_controller_run_id" in str(exc_info.value)
    if isinstance(bad_id, str) and bad_id:
        assert bad_id not in str(exc_info.value)
    assert "SECRET_LEAK_TOKEN_98765" not in str(exc_info.value)
    assert mock_validate_repo.call_count == 0
    assert mock_popen.call_count == 0
    assert list((state_root / "runs").iterdir()) == []


def test_valid_continue_calls_finalization_once_with_exact_contract(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    after_commit = "b" * 40
    final_repo = dict(before, head=after_commit)
    events: list[str] = []
    status_during_finalization: str | None = None
    no_checkpoint_during_finalization: bool = False
    observed_kwargs: dict[str, Any] = {}

    def fake_validate_repo(_r: Path, require_clean: bool = True) -> dict[str, object]:
        events.append("validate_repo")
        return final_repo if "child_capture" in events else before

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        events.append("child_capture")
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state="CONTINUE", commit=after_commit)),
            encoding="utf-8",
        )

    def fake_finalization(**kwargs: Any) -> dict[str, Any]:
        events.append("finalize_locked")
        nonlocal status_during_finalization, no_checkpoint_during_finalization, observed_kwargs
        observed_kwargs = dict(kwargs)
        run_dir_path = state_root / "runs" / str(kwargs.get("continuation_controller_run_id"))
        status_file = run_dir_path / "status.json"
        if status_file.exists():
            status_during_finalization = json.loads(status_file.read_text(encoding="utf-8")).get("state")
        no_checkpoint_during_finalization = (
            not (run_dir_path / "accepted_checkpoint.json").exists()
            and not (state_root / "accepted_checkpoint.json").exists()
        )
        return {"acknowledged": True}

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=fake_validate_repo,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked",
        side_effect=fake_finalization,
    ) as mock_finalization:
        run_dir, result = run_cycle(
            root,
            codex,
            state_root,
            1,
            300,
            quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
        )

    assert mock_finalization.call_count == 1
    assert status_during_finalization == "RUNNING"
    assert no_checkpoint_during_finalization is True
    assert events == [
        "validate_repo",
        "child_capture",
        "validate_repo",
        "finalize_locked",
        "validate_repo",
    ]
    assert observed_kwargs["root"] == root
    assert observed_kwargs["original_controller_run_id"] == VALID_ORIGINAL_RUN_ID
    assert observed_kwargs["continuation_controller_run_id"] == run_dir.name
    assert observed_kwargs["before_repository"] is before
    assert observed_kwargs["expected_transition_result"] is result
    assert re.fullmatch(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", observed_kwargs["acknowledged_at_utc"])
    final_status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert final_status["state"] == "CONTINUE"
    assert result["state"] == "CONTINUE"


def test_valid_complete_calls_finalization_once_with_exact_contract(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    events: list[str] = []
    status_during_finalization: str | None = None
    no_checkpoint_during_finalization: bool = False
    observed_kwargs: dict[str, Any] = {}

    def fake_validate_repo(_r: Path, require_clean: bool = True) -> dict[str, object]:
        events.append("validate_repo")
        return before

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        events.append("child_capture")
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state="COMPLETE", commit=str(before["head"]))),
            encoding="utf-8",
        )

    def fake_finalization(**kwargs: Any) -> dict[str, Any]:
        events.append("finalize_locked")
        nonlocal status_during_finalization, no_checkpoint_during_finalization, observed_kwargs
        observed_kwargs = dict(kwargs)
        run_dir_path = state_root / "runs" / str(kwargs.get("continuation_controller_run_id"))
        status_file = run_dir_path / "status.json"
        if status_file.exists():
            status_during_finalization = json.loads(status_file.read_text(encoding="utf-8")).get("state")
        no_checkpoint_during_finalization = (
            not (run_dir_path / "accepted_checkpoint.json").exists()
            and not (state_root / "accepted_checkpoint.json").exists()
        )
        return {"acknowledged": True}

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=fake_validate_repo,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked",
        side_effect=fake_finalization,
    ) as mock_finalization:
        run_dir, result = run_cycle(
            root,
            codex,
            state_root,
            1,
            300,
            quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
        )

    assert mock_finalization.call_count == 1
    assert status_during_finalization == "RUNNING"
    assert no_checkpoint_during_finalization is True
    assert events == ["validate_repo", "child_capture", "validate_repo", "finalize_locked"]
    assert observed_kwargs["root"] == root
    assert observed_kwargs["original_controller_run_id"] == VALID_ORIGINAL_RUN_ID
    assert observed_kwargs["continuation_controller_run_id"] == run_dir.name
    assert observed_kwargs["before_repository"] is before
    assert observed_kwargs["expected_transition_result"] is result
    assert re.fullmatch(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", observed_kwargs["acknowledged_at_utc"])
    final_status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert final_status["state"] == "COMPLETE"
    assert result["state"] == "COMPLETE"


@pytest.mark.parametrize("ineligible_state", ["RESCOPE", "STOP", "UNKNOWN"])
def test_schema_invalid_states_fail_before_hook_with_autopilot_error(
    tmp_path: Path, ineligible_state: str
) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state=ineligible_state)),
            encoding="utf-8",
        )

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=lambda _r, require_clean=True: before,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked"
    ) as mock_finalization:
        with pytest.raises(AutopilotError) as exc_info:
            run_cycle(
                root,
                codex,
                state_root,
                1,
                300,
                quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
            )

    assert not isinstance(exc_info.value, QuotaFinalizationHardStopError)
    assert mock_finalization.call_count == 0


def test_ineligible_blocked_state_triggers_hard_stop_at_hook(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    raw = _make_raw_result(state="BLOCKED")

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(json.dumps(raw), encoding="utf-8")

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=lambda _r, require_clean=True: before,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_codex_autopilot.validate_result",
        return_value=raw,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked"
    ) as mock_finalization:
        with pytest.raises(QuotaFinalizationHardStopError) as exc_info:
            run_cycle(
                root,
                codex,
                state_root,
                1,
                300,
                quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
            )

    assert mock_finalization.call_count == 0
    assert isinstance(exc_info.value.__cause__, AutopilotError)
    assert "quota continuation requires state CONTINUE or COMPLETE, got 'BLOCKED'" in str(exc_info.value)
    run_dirs = list((state_root / "runs").iterdir())
    assert len(run_dirs) == 1
    status_p = run_dirs[0] / "status.json"
    if status_p.exists():
        status = json.loads(status_p.read_text(encoding="utf-8"))
        assert status["state"] == "FAILED"


def test_repository_protected_refs_drift_prevents_finalization(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    drifted_refs = dict(before["protected_refs"])
    drifted_refs["refs/heads/main"] = "9" * 40
    quota_repo = dict(before, protected_refs=drifted_refs)
    repo_calls = 0

    def fake_validate_repo(_r: Path, require_clean: bool = True) -> dict[str, object]:
        nonlocal repo_calls
        repo_calls += 1
        return before if repo_calls == 1 else quota_repo

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state="CONTINUE")),
            encoding="utf-8",
        )

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=fake_validate_repo,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked"
    ) as mock_finalization:
        with pytest.raises(QuotaFinalizationHardStopError) as exc_info:
            run_cycle(
                root,
                codex,
                state_root,
                1,
                300,
                quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
            )

    assert mock_finalization.call_count == 0
    assert isinstance(exc_info.value.__cause__, AutopilotError)
    assert "protected_refs changed before quota finalization" in str(exc_info.value)


def test_repository_assignment_scope_drift_prevents_finalization(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    drifted_scope = AssignmentScope(
        assignment_id="WA-TEST-SCOPE-DRIFTED",
        stage_id="P1-TEST",
        allowed_paths=("tests/**",),
        forbidden_paths=("blocked/**",),
        expires_at="2099-01-01T00:00:00Z",
        sha256="e" * 64,
    )
    quota_repo = dict(before, assignment_scope=drifted_scope)
    repo_calls = 0

    def fake_validate_repo(_r: Path, require_clean: bool = True) -> dict[str, object]:
        nonlocal repo_calls
        repo_calls += 1
        return before if repo_calls == 1 else quota_repo

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state="CONTINUE")),
            encoding="utf-8",
        )

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=fake_validate_repo,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked"
    ) as mock_finalization:
        with pytest.raises(QuotaFinalizationHardStopError) as exc_info:
            run_cycle(
                root,
                codex,
                state_root,
                1,
                300,
                quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
            )

    assert mock_finalization.call_count == 0
    assert isinstance(exc_info.value.__cause__, AutopilotError)
    assert "assignment_scope changed before quota finalization" in str(exc_info.value)


def test_finalization_helper_exception_produces_hard_stop_with_exact_cause(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    bridge_error = SupervisorFinalizationAmbiguousError("ambiguous finalization failure on locked ack")

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state="CONTINUE")),
            encoding="utf-8",
        )

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=lambda _r, require_clean=True: before,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked",
        side_effect=bridge_error,
    ):
        with pytest.raises(QuotaFinalizationHardStopError) as exc_info:
            run_cycle(
                root,
                codex,
                state_root,
                1,
                300,
                quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
            )

    assert exc_info.value.__cause__ is bridge_error
    assert "quota finalization failed: ambiguous finalization failure on locked ack" in str(exc_info.value)
    run_dirs = list((state_root / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert not (run_dir / "accepted_checkpoint.json").exists()
    assert not (state_root / "accepted_checkpoint.json").exists()
    status_p = run_dir / "status.json"
    if status_p.exists():
        status = json.loads(status_p.read_text(encoding="utf-8"))
        assert status["state"] == "FAILED"


@pytest.mark.parametrize("signal_exc", [KeyboardInterrupt(), SystemExit(42)])
def test_fatal_signals_remain_fatal(tmp_path: Path, signal_exc: BaseException) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state="CONTINUE")),
            encoding="utf-8",
        )

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=lambda _r, require_clean=True: before,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked",
        side_effect=signal_exc,
    ):
        with pytest.raises(type(signal_exc)) as exc_info:
            run_cycle(
                root,
                codex,
                state_root,
                1,
                300,
                quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID,
            )

    assert exc_info.value is signal_exc


def test_run_loop_hard_stop_writes_blocked_ambiguous_without_retry(tmp_path: Path) -> None:
    root, codex, _ = _setup_workspace(tmp_path)
    head = "1" * 40
    before = _make_before(head=head)
    expected_state_root = root / ".local" / "two-site-refactor" / "autopilot"
    hard_stop_err = QuotaFinalizationHardStopError("quota finalization failed: bridge failure")
    mock_run_cycle = MagicMock(side_effect=hard_stop_err)
    mock_write_state = MagicMock()

    with patch(
        "scripts.two_site_codex_autopilot.validate_repository",
        return_value=before,
    ), patch(
        "scripts.two_site_codex_autopilot.git",
        side_effect=lambda r, *a: head if "rev-parse" in a else ("" if "status" in a else "2" * 40),
    ), patch(
        "scripts.two_site_codex_autopilot.ref_snapshot",
        return_value={"refs/heads/main": head},
    ), patch(
        "scripts.two_site_codex_autopilot.prune_runs",
    ), patch(
        "scripts.two_site_codex_autopilot.run_cycle",
        mock_run_cycle,
    ), patch(
        "scripts.two_site_codex_autopilot.write_supervisor_state",
        mock_write_state,
    ):
        ret = run_loop(root, codex, max_cycles=5, timeout=300, once=False)

    assert ret == 2
    assert mock_run_cycle.call_count == 1
    mock_write_state.assert_called_with(
        expected_state_root,
        "BLOCKED",
        cycle=1,
        head=head,
        blockers=["QuotaFinalizationHardStopError: quota finalization failed: bridge failure"],
        dirty=False,
        ambiguous_change=True,
    )


def test_default_none_path_skips_finalization_and_no_live_wake(tmp_path: Path) -> None:
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    final_repo = dict(before, head="b" * 40)

    def fake_validate_repo(_r: Path, require_clean: bool = True) -> dict[str, object]:
        return final_repo if "child_capture" in events else before

    events: list[str] = []

    def fake_capture(_p: _FakePopen, _in: bytes, _out: Path, _err: Path, _t: int, max_output: int = 0) -> None:
        events.append("child_capture")
        (_out.parent / "result.json").write_text(
            json.dumps(_make_raw_result(state="CONTINUE", commit="b" * 40)),
            encoding="utf-8",
        )

    with _mock_cycle_environment(
        codex,
        validate_repo_side_effect=fake_validate_repo,
        capture_side_effect=fake_capture,
    ), patch(
        "scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked",
    ) as mock_finalization:
        run_dir, result = run_cycle(root, codex, state_root, 1, 300)

    assert mock_finalization.call_count == 0
    assert result["state"] == "CONTINUE"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "CONTINUE"
