from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.two_site_quota_resume_execution import (
    ContinuationAdmissionError,
    ProposalError,
)
from scripts.two_site_quota_resume_worker_context import (
    prepare_worker_zero_continuation,
    worker_zero_continuation_context,
)


def _base_fixtures():
    req = {"role": "worker", "run_prefix": "run-w0", "original_request_id": "orig-42"}
    admitted = dict(req)
    receipt = {"request_id": "orig-42", "status": "QUOTA"}
    artifacts = {
        "worker_index": 0,
        "source_head": "c44e63",
        "original_run_prefix": "run-orig",
    }
    refs = {"refs/heads/main": "c44e63"}
    task = {"goal": "work"}
    sources = {"file.py": "print(1)"}
    return req, admitted, receipt, artifacts, refs, task, sources


class MockLock:
    def __init__(self):
        self.locked = False
        self.acquire_count = 0
        self.release_count = 0

    @contextmanager
    def __call__(self, root: Path):
        self.locked = True
        self.acquire_count += 1
        try:
            yield
        finally:
            self.locked = False
            self.release_count += 1


def test_invalid_before_lock():
    lock = MockLock()
    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock):
        with pytest.raises(ProposalError, match="root must be a Path"):
            with worker_zero_continuation_context("/not/a/path", {"role": "worker"}):
                pass
        with pytest.raises(ProposalError, match="request must be dict"):
            with worker_zero_continuation_context(Path("/test"), "not-dict"):
                pass
        with pytest.raises(ProposalError, match="role must be 'worker'"):
            with worker_zero_continuation_context(Path("/test"), {"role": "verifier"}):
                pass
        assert lock.acquire_count == 0


def test_success_and_lock_held_during_body(tmp_path: Path):
    req, admitted, receipt, artifacts, refs, task, sources = _base_fixtures()
    lock = MockLock()
    inside_body_locked = False

    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", return_value=(admitted, "t.json", "res")), \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt), \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts), \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot", return_value=refs), \
         patch("scripts.two_site_quota_resume_worker_context.load_task", return_value=(task, sources)), \
         patch("scripts.two_site_quota_resume_worker_context.check_state") as mock_check_state:

        with worker_zero_continuation_context(tmp_path, req) as ctx:
            inside_body_locked = lock.locked
            assert ctx["continuation_run_prefix"] == "run-w0"
            assert ctx["original_run_prefix"] == "run-orig"
            assert ctx["source_head"] == "c44e63"
            assert ctx["initial_refs"] == refs
            assert ctx["original_receipt"] == receipt
            assert ctx["admitted_request"] == admitted
            assert ctx["admitted_task_path"] == "t.json"
            assert ctx["artifacts"] == artifacts
            assert ctx["task"] == task
            assert ctx["sources"] == sources

            # Validate deep copy
            ctx["task"]["goal"] = "mutated"
            assert task["goal"] == "work"

        assert inside_body_locked is True
        assert lock.locked is False
        assert lock.acquire_count == 1
        assert lock.release_count == 1
        assert mock_check_state.call_count == 2

        prepared = prepare_worker_zero_continuation(tmp_path, req)
        assert prepared["continuation_run_prefix"] == "run-w0"
        assert lock.locked is False


def test_lock_released_on_body_exception(tmp_path: Path):
    req, admitted, receipt, artifacts, refs, task, sources = _base_fixtures()
    lock = MockLock()

    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", return_value=(admitted, "t.json", "res")), \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt), \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts), \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot", return_value=refs), \
         patch("scripts.two_site_quota_resume_worker_context.load_task", return_value=(task, sources)), \
         patch("scripts.two_site_quota_resume_worker_context.check_state"):

        with pytest.raises(RuntimeError, match="simulated caller error"):
            with worker_zero_continuation_context(tmp_path, req):
                assert lock.locked is True
                raise RuntimeError("simulated caller error")

        assert lock.locked is False
        assert lock.release_count == 1


def test_worker_1_refused(tmp_path: Path):
    req, admitted, receipt, _, refs, task, sources = _base_fixtures()
    artifacts_worker_1 = {"worker_index": 1, "source_head": "c44e63", "original_run_prefix": "run-orig"}
    lock = MockLock()

    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", return_value=(admitted, "t.json", "res")), \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt), \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts_worker_1):

        with pytest.raises(ProposalError, match="worker_index must be 0, got 1"):
            with worker_zero_continuation_context(tmp_path, req):
                pass

        assert lock.locked is False


def test_drift_refused(tmp_path: Path):
    req, admitted, receipt, artifacts, refs, task, sources = _base_fixtures()
    lock = MockLock()

    # 1. Admitted request drift
    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", side_effect=[
             (admitted, "t.json", "res"),
             ({**admitted, "mutated": True}, "t.json", "res"),
         ]), \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt), \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts), \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot", return_value=refs), \
         patch("scripts.two_site_quota_resume_worker_context.load_task", return_value=(task, sources)), \
         patch("scripts.two_site_quota_resume_worker_context.check_state"):
        with pytest.raises(ProposalError, match="continuation request drifted"):
            with worker_zero_continuation_context(tmp_path, req):
                pass

    # 2. Task path drift
    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", side_effect=[
             (admitted, "t1.json", "res"),
             (admitted, "t2.json", "res"),
         ]), \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt), \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts), \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot", return_value=refs), \
         patch("scripts.two_site_quota_resume_worker_context.load_task", return_value=(task, sources)), \
         patch("scripts.two_site_quota_resume_worker_context.check_state"):
        with pytest.raises(ProposalError, match="admitted task path drifted"):
            with worker_zero_continuation_context(tmp_path, req):
                pass

    # 3. Task / sources drift
    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", return_value=(admitted, "t.json", "res")), \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt), \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts), \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot", return_value=refs), \
         patch("scripts.two_site_quota_resume_worker_context.load_task", side_effect=[
             (task, sources),
             ({"goal": "changed"}, sources),
         ]), \
         patch("scripts.two_site_quota_resume_worker_context.check_state"):
        with pytest.raises(ProposalError, match="task or sources drifted"):
            with worker_zero_continuation_context(tmp_path, req):
                pass

    # 4. Refs drift detected by check_state
    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", return_value=(admitted, "t.json", "res")), \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt), \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts), \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot", return_value=refs), \
         patch("scripts.two_site_quota_resume_worker_context.load_task", return_value=(task, sources)), \
         patch("scripts.two_site_quota_resume_worker_context.check_state", side_effect=[None, ProposalError("git ref mismatch")]):
        with pytest.raises(ProposalError, match="git ref mismatch"):
            with worker_zero_continuation_context(tmp_path, req):
                pass


def test_no_provider_calls_or_execution_authority():
    import scripts.two_site_quota_resume_worker_context as mod
    forbidden = [
        "runner_run",
        "run_provider_session",
        "execute_pipeline",
        "run_pipeline",
    ]
    for symbol in forbidden:
        assert not hasattr(mod, symbol), f"forbidden symbol {symbol} exposed in worker context"


def test_first_admission_stop_propagation(tmp_path: Path):
    req, _, _, _, _, _, _ = _base_fixtures()
    lock = MockLock()
    stop_msg = "autopilot STOP sentinel exists: .local/two-site-refactor/autopilot/STOP"
    stop_err = ContinuationAdmissionError(stop_msg)

    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", side_effect=stop_err) as mock_admission, \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record") as mock_read_record, \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts") as mock_derive_artifacts, \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot") as mock_ref_snapshot, \
         patch("scripts.two_site_quota_resume_worker_context.load_task") as mock_load_task, \
         patch("scripts.two_site_quota_resume_worker_context.check_state") as mock_check_state:

        body_entered = False
        with pytest.raises(ContinuationAdmissionError) as exc_info:
            with worker_zero_continuation_context(tmp_path, req):
                body_entered = True

        assert exc_info.value is stop_err
        assert str(exc_info.value) == stop_msg
        assert body_entered is False
        assert lock.locked is False
        assert lock.acquire_count == 1
        assert lock.release_count == 1

        mock_admission.assert_called_once_with(tmp_path, req)
        mock_read_record.assert_not_called()
        mock_derive_artifacts.assert_not_called()
        mock_ref_snapshot.assert_not_called()
        mock_load_task.assert_not_called()
        mock_check_state.assert_not_called()

        import scripts.two_site_quota_resume_worker_context as mod
        for symbol in ("runner_run", "run_provider_session", "execute_pipeline", "run_pipeline"):
            assert not hasattr(mod, symbol)


def test_second_admission_stop_propagation(tmp_path: Path):
    req, admitted, receipt, artifacts, refs, task, sources = _base_fixtures()
    lock = MockLock()
    stop_msg = "autopilot STOP sentinel exists: .local/two-site-refactor/autopilot/STOP"
    stop_err = ContinuationAdmissionError(stop_msg)

    with patch("scripts.two_site_quota_resume_worker_context.pipeline_lock", lock), \
         patch("scripts.two_site_quota_resume_worker_context.validate_quota_continuation_request", side_effect=[(admitted, "t.json", "res"), stop_err]) as mock_admission, \
         patch("scripts.two_site_quota_resume_worker_context.read_terminal_quota_record", return_value=receipt) as mock_read_record, \
         patch("scripts.two_site_quota_resume_worker_context.derive_worker_resume_artifacts", return_value=artifacts) as mock_derive_artifacts, \
         patch("scripts.two_site_quota_resume_worker_context.ref_snapshot", return_value=refs) as mock_ref_snapshot, \
         patch("scripts.two_site_quota_resume_worker_context.load_task", return_value=(task, sources)) as mock_load_task, \
         patch("scripts.two_site_quota_resume_worker_context.check_state") as mock_check_state:

        body_entered = False
        with pytest.raises(ContinuationAdmissionError) as exc_info:
            with worker_zero_continuation_context(tmp_path, req):
                body_entered = True

        assert exc_info.value is stop_err
        assert str(exc_info.value) == stop_msg
        assert body_entered is False
        assert lock.locked is False
        assert lock.acquire_count == 1
        assert lock.release_count == 1

        assert mock_admission.call_count == 2
        mock_read_record.assert_called_once_with(tmp_path, "orig-42")
        mock_derive_artifacts.assert_called_once_with(admitted, receipt)
        mock_ref_snapshot.assert_called_once_with(tmp_path)
        mock_load_task.assert_called_once_with(tmp_path, "t.json")
        mock_check_state.assert_called_once_with(
            tmp_path,
            artifacts["source_head"],
            refs,
            "t.json",
            task,
            sources,
        )

        import scripts.two_site_quota_resume_worker_context as mod
        for symbol in ("runner_run", "run_provider_session", "execute_pipeline", "run_pipeline"):
            assert not hasattr(mod, symbol)
