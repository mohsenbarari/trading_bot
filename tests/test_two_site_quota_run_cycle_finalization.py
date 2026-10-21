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
# Resolve from-import consumers BEFORE any scoped mock can be retained on first
# import. In particular, due inspection must keep the real recovery reader and
# submission/client must keep the real STOP guard after these wiring tests.
from scripts import two_site_quota_broker_route as quota_route  # noqa: E402
from scripts import two_site_quota_broker_client as quota_client  # noqa: E402
from scripts import two_site_quota_wait_recovery as wait_recovery  # noqa: E402
from scripts import two_site_quota_supervisor_due_inspect as due_inspect  # noqa: E402
from scripts import two_site_quota_supervisor_resume_context as resume_context  # noqa: E402
from scripts import two_site_quota_supervisor_resume_submit as resume_submit  # noqa: E402
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
def _prior_finalization_only_hold(*_args: Any):
    # These inherited tests isolate the existing post-Codex finalization hook.
    # The acquisition/client-hold seam is exercised separately below.
    yield None


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
    ), patch(
        "scripts.two_site_quota_supervisor_bridge.quota_codex_review_locked",
        side_effect=_prior_finalization_only_hold,
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


def test_quota_actual_allocation_hold_finalization_then_checkpoint(tmp_path: Path) -> None:
    """Controlled child only; not a claim of live Provider or crash recovery."""
    root, codex, state_root = _setup_workspace(tmp_path)
    template = Path(__file__).resolve().parents[1] / "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md"
    (root / "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md").write_bytes(template.read_bytes())
    before = _make_before()
    events: list[str] = []
    allocated: list[str] = []
    active = False

    @contextmanager
    def hold(_root: Path, original: str, continuation: str, timeout: int):
        nonlocal active
        assert original == VALID_ORIGINAL_RUN_ID and timeout == 300
        run_dir = state_root / "runs" / continuation
        assert len(list((state_root / "runs").iterdir())) == 1
        startup = json.loads((run_dir / "preparation-startup.json").read_text())
        assert startup["cycle_id"] == continuation
        allocated.append(continuation)
        events.append("acquire_ready_and_client_hold")
        active = True
        try:
            yield {"controller_run_id": continuation, "original_controller_run_id": original,
                   "ready": {"state": "READY_FOR_CODEX"}}
        finally:
            active = False
            events.append("release_client_hold")

    def capture(_p, _in, out, _err, _t, max_output=0):
        assert active
        prompt = (out.parent / "prompt.txt").read_text()
        assert "2Q. QUOTA FINAL REVIEW ONLY" in prompt
        assert "two_site_provider_broker.py submit --task" not in prompt
        events.append("codex")
        (out.parent / "result.json").write_text(json.dumps(_make_raw_result()))

    def finalize(**kwargs):
        assert active and kwargs["continuation_controller_run_id"] == allocated[0]
        assert kwargs["original_controller_run_id"] == VALID_ORIGINAL_RUN_ID
        events.append("lineage_then_ack")

    def checkpoint(*_args):
        assert not active
        events.append("checkpoint")

    def repo(_root, require_clean=True):
        return dict(before, head="b" * 40) if "codex" in events else before

    with _mock_cycle_environment(codex, repo, capture), \
         patch("scripts.two_site_quota_supervisor_bridge.quota_codex_review_locked", side_effect=hold), \
         patch("scripts.two_site_quota_supervisor_finalization.finalize_accepted_quota_continuation_locked", side_effect=finalize), \
         patch("scripts.two_site_codex_autopilot.publish_accepted_checkpoint", side_effect=checkpoint):
        run_dir, result = run_cycle(root, codex, state_root, 1, 300,
                                   quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID)
    assert allocated == [run_dir.name]
    assert events == ["acquire_ready_and_client_hold", "codex", "lineage_then_ack", "release_client_hold", "checkpoint"]
    assert result["state"] == "CONTINUE"


@pytest.mark.parametrize("due_state,claim_exists", [
    ("NOT_DUE", False), ("KNOWN_RESET_REVALIDATION_DUE", False),
    ("KNOWN_RESET_REVALIDATION_DUE", True),
])
def test_standing_quota_due_allocates_once_or_halts_ambiguous(tmp_path: Path, due_state: str, claim_exists: bool) -> None:
    from scripts import two_site_quota_supervisor_bridge as bridge
    from scripts import two_site_provider_broker as broker
    import time
    real_recovery = wait_recovery.recover_durable_initial_wait_locked
    assert due_inspect.recover_durable_initial_wait_locked is real_recovery
    assert resume_context.recover_durable_initial_wait_locked is real_recovery
    root, codex, _unused_state = _setup_workspace(tmp_path)
    state_root = root / ".local/two-site-refactor/autopilot"
    original_dir = state_root / "runs" / VALID_ORIGINAL_RUN_ID
    original_dir.mkdir(parents=True, mode=0o700)
    original_dir.chmod(0o700)
    continuation = state_root / "runs/cycle-0001-20261020T120000Z-234"
    record = {"state": "WAITING_QUOTA", "controller_run_id": VALID_ORIGINAL_RUN_ID,
              "request_id": "a" * 32, "schedule": {"state": "fixture"}}
    before = _make_before()
    broker._atomic_new_json(broker.broker_paths(root)[1], {"state": "IDLE", "pid": os.getpid(),
        "started_at": time.time(), "updated_at": time.time(), "quota_continuation_ready": True}, broker.MAX_REQUEST_BYTES)
    if claim_exists:
        (original_dir / "quota-resume-claim.json").write_text("retained ambiguous claim")

    @contextmanager
    def pinned(*_args):
        fd = os.open(original_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield original_dir, fd, lambda: None
        finally:
            os.close(fd)

    result = dict(_make_raw_result(), head_after="b" * 40)
    def cycle_once(*_args, **_kwargs):
        (original_dir / "quota-resume-claim.json").write_text("controlled first claim, not replay authority")
        return continuation, result

    with patch("scripts.two_site_quota_broker_route._check_pre_dispatch_stop"), \
         patch.object(wait_recovery, "recover_durable_initial_wait_locked", return_value=(VALID_ORIGINAL_RUN_ID, record)), \
         patch.object(due_inspect, "inspect_durable_quota_wait_due_locked", return_value={"controller_run_id": VALID_ORIGINAL_RUN_ID, "due": {"state": due_state}}), \
         patch.object(bridge, "_open_existing_cycle_run_directory", side_effect=pinned), \
         patch("scripts.two_site_codex_autopilot.run_cycle", side_effect=cycle_once) as cycle, \
         patch("scripts.two_site_codex_autopilot.write_supervisor_state") as status, \
         patch("scripts.two_site_codex_autopilot.git", return_value=before["head"]), \
         patch("scripts.two_site_codex_autopilot.validate_repository", return_value=before):
        code = bridge.resume_due_quota_cycle_locked(root, codex, state_root, before, 300, "one_shot")
        if due_state == "KNOWN_RESET_REVALIDATION_DUE" and not claim_exists:
            assert status.call_args.args[1] == "CONTINUE"
            assert bridge.resume_due_quota_cycle_locked(root, codex, state_root, before, 300, "one_shot") == 2
    assert wait_recovery.recover_durable_initial_wait_locked is real_recovery
    assert due_inspect.recover_durable_initial_wait_locked is real_recovery
    assert resume_context.recover_durable_initial_wait_locked is real_recovery
    assert code == 2
    if due_state == "KNOWN_RESET_REVALIDATION_DUE" and not claim_exists:
        cycle.assert_called_once_with(root, codex, state_root, 1, 300,
            preparation_route="one_shot", quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID)
        assert status.call_args.args[1] == "BLOCKED"
        assert status.call_args.kwargs["ambiguous_change"] is True
    else:
        cycle.assert_not_called()
        assert status.call_args.args[1] == ("BLOCKED" if claim_exists else "WAITING_QUOTA")
        if claim_exists:
            assert status.call_args.kwargs["ambiguous_change"] is True
            assert (original_dir / "quota-resume-claim.json").read_text() == "retained ambiguous claim"


def test_quota_ready_cleanup_is_under_client_hold_and_uses_canonical_terminal(tmp_path: Path) -> None:
    from scripts import two_site_quota_supervisor_bridge as bridge
    from scripts import two_site_provider_broker as broker
    from tests.test_two_site_quota_broker_claim_recovery import _make_continuation_request
    real_stop = quota_route._check_pre_dispatch_stop
    assert resume_submit._check_pre_dispatch_stop is real_stop
    assert quota_client._check_pre_dispatch_stop is real_stop
    request = _make_continuation_request()
    root = tmp_path.resolve()
    status = {"state": "IDLE", "pid": 123, "started_at": 100.0, "quota_continuation_ready": True}
    response = {"request_id": request["request_id"], "fixture": "strict Q1 already checked"}
    terminal = {"request": request, "response": response}
    receipt_sha = bridge.hashlib.sha256(bridge._canonical(terminal)).hexdigest()
    acquired = {"request": request, "response": response, "ready": {"state": "READY_FOR_CODEX"},
                "terminal_receipt_sha256": receipt_sha}
    queue = broker.broker_paths(root)[4] / (request["request_id"] + ".json")
    broker._atomic_new_json(queue, response, broker.MAX_RESPONSE_BYTES)
    events = []

    @contextmanager
    def held(_root):
        events.append("hold")
        try:
            yield
        finally:
            events.append("release")

    def terminal_read(*_args, **_kwargs):
        assert events == ["hold"]
        # Raw indentation/newline is intentionally not the canonical receipt SHA.
        assert bridge.hashlib.sha256((json.dumps(terminal, indent=2) + "\n").encode()).hexdigest() != receipt_sha
        return terminal

    with patch.object(broker, "_read_broker_status", return_value=status), \
         patch("scripts.two_site_quota_broker_route._check_pre_dispatch_stop"), \
         patch.object(resume_submit, "submit_known_reset_resume_once_locked", return_value={"request": request}) as submit, \
         patch.object(resume_context, "wait_for_known_reset_resume_ready_locked", return_value=acquired) as wait, \
         patch("scripts.two_site_cold_start_broker_quiescence.hold_idle_broker_client_lock_for_cold_adoption", side_effect=held), \
         patch("scripts.two_site_quota_continuation_terminal_storage.read_terminal_continuation_record", side_effect=terminal_read):
        with bridge.quota_codex_review_locked(root, request["original_controller_run_id"], request["controller_run_id"], 300) as context:
            assert events == ["hold"] and not queue.exists()
            assert context["terminal_receipt_sha256"] == receipt_sha
            assert context["ready"] is acquired["ready"]
        assert events == ["hold", "release"]
    assert quota_route._check_pre_dispatch_stop is real_stop
    assert resume_submit._check_pre_dispatch_stop is real_stop
    assert quota_client._check_pre_dispatch_stop is real_stop
    submit.assert_called_once()
    assert submit.call_args.args[2] == request["controller_run_id"]
    wait.assert_called_once_with(root, request, expected_broker_identity=(123, 100.0))


def test_quota_response_drift_never_unlinks_queue(tmp_path: Path) -> None:
    from scripts import two_site_quota_supervisor_bridge as bridge
    from scripts import two_site_provider_broker as broker
    from tests.test_two_site_quota_broker_claim_recovery import _make_continuation_request
    root = tmp_path.resolve()
    request = _make_continuation_request()
    terminal = {"response": {"original": True}}
    queue = broker.broker_paths(root)[4] / (request["request_id"] + ".json")
    broker._atomic_new_json(queue, {"drifted": True}, broker.MAX_RESPONSE_BYTES)
    raw = queue.read_bytes()
    acquired = {"request": request, "response": terminal["response"],
                "terminal_receipt_sha256": bridge.hashlib.sha256(bridge._canonical(terminal)).hexdigest()}
    with patch("scripts.two_site_quota_continuation_terminal_storage.read_terminal_continuation_record", return_value=terminal):
        with pytest.raises(bridge.QuotaSupervisorBridgeError, match="queue/terminal response mismatch"):
            bridge._consume_exact_quota_response(root, acquired)
    assert queue.read_bytes() == raw


def test_quota_capture_cleanup_timeout_preserves_first_error_and_releases_hold(tmp_path: Path) -> None:
    """Never enter Popen.__exit__ while the quota client hold is owned."""
    from scripts import two_site_quota_supervisor_bridge as bridge
    import subprocess
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    events = []
    original = RuntimeError("first capture failure")

    class StuckChild:
        pid = 98765
        returncode = None
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        def wait(self, *, timeout):
            assert timeout == 10
            events.append("bounded_wait")
            raise subprocess.TimeoutExpired("controlled child", timeout)
        def __exit__(self, *_args):
            raise AssertionError("unbounded Popen exit must never be entered")

    @contextmanager
    def held(*_args):
        events.append("hold")
        try:
            yield None  # Isolate capture ownership from strict Q1 acquisition.
        finally:
            events.append("release")

    stuck = StuckChild()
    with _mock_cycle_environment(codex, lambda *_a, **_k: before, lambda *_a, **_k: (_ for _ in ()).throw(original)), \
         patch.object(bridge, "quota_codex_review_locked", side_effect=held), \
         patch("scripts.two_site_codex_autopilot.subprocess.Popen", return_value=stuck), \
         patch("scripts.two_site_proposal_runner.terminate_process", side_effect=subprocess.TimeoutExpired("initial cleanup", 10)) as terminate, \
         patch.object(bridge.os, "killpg") as kill, \
         patch("scripts.two_site_codex_autopilot.publish_accepted_checkpoint") as checkpoint:
        with pytest.raises(RuntimeError) as caught:
            run_cycle(root, codex, state_root, 1, 300,
                      quota_original_controller_run_id=VALID_ORIGINAL_RUN_ID)
    assert caught.value is original
    assert events == ["hold", "bounded_wait", "release"]
    terminate.assert_called_once_with(stuck)
    kill.assert_called_once()
    for stream in (stuck.stdin, stuck.stdout, stuck.stderr):
        stream.close.assert_called_once()
    checkpoint.assert_not_called()


def test_quota_blocked_status_keeps_entry_context_and_primary_error(tmp_path: Path) -> None:
    from scripts import two_site_quota_supervisor_bridge as bridge
    from scripts import two_site_codex_autopilot as ap
    root, codex, state_root = _setup_workspace(tmp_path)
    before = _make_before()
    primary = ValueError("original invalid durable quota wait")
    secondary = OSError("controlled BLOCKED status write failure")
    with patch.object(bridge, "_check_resume_stop", side_effect=primary), \
         patch.object(ap, "git", side_effect=AssertionError("failure publication must not query Git")) as git, \
         patch.object(ap, "run_cycle", side_effect=AssertionError("no continuation allocation")) as cycle, \
         patch.object(ap, "publish_accepted_checkpoint", side_effect=AssertionError("no checkpoint")) as checkpoint, \
         patch("scripts.two_site_quota_supervisor_resume_submit.submit_known_reset_resume_once_locked", side_effect=AssertionError("no claim or submit")) as submit, \
         patch.object(ap, "write_supervisor_state") as status:
        assert bridge.resume_due_quota_cycle_locked(root, codex, state_root, before, 300, "one_shot") == 2
        assert status.call_args.args == (state_root, "BLOCKED")
        assert status.call_args.kwargs["head"] == before["head"]
        assert status.call_args.kwargs["ambiguous_change"] is True
        assert "validated entry context, not a current observation" in status.call_args.kwargs["summary"]
        assert status.call_args.kwargs["blockers"] == ["quota resume hard stop: ValueError: " + str(primary)]
        status.side_effect = secondary
        with pytest.raises(ValueError) as caught:
            bridge.resume_due_quota_cycle_locked(root, codex, state_root, before, 300, "one_shot")
        assert caught.value is primary and caught.value.__cause__ is secondary
    git.assert_not_called()
    cycle.assert_not_called()
    checkpoint.assert_not_called()
    submit.assert_not_called()
