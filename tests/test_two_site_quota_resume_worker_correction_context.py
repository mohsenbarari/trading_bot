from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.two_site_quota_resume_execution import (
    ContinuationAdmissionError,
    ProposalError,
)
from scripts.two_site_quota_resume_worker_correction_context import (
    prepare_worker_correction_continuation,
    worker_correction_continuation_context,
)


def _base_fixtures(worker_index: int = 1):
    req = {
        "role": "worker",
        "run_prefix": f"run-w{worker_index}",
        "original_request_id": "orig-42",
    }
    admitted = dict(req)
    receipt = {"request_id": "orig-42", "status": "QUOTA"}
    artifacts = {
        "worker_index": worker_index,
        "source_head": "c44e63",
        "original_run_prefix": "run-orig",
        "reviewer_path": ".local/two-site-refactor/runs/run-rev/reviewer_result.json",
    }
    evidence = {
        "task": {"goal": "fix bug"},
        "sources": {"file.py": "print(2)"},
        "feedback": {"notes": "addressed finding"},
    }
    status_bytes = b'{"status": "CHANGES_REQUIRED"}'
    refs = {"refs/heads/main": "c44e63"}
    return req, admitted, receipt, artifacts, evidence, status_bytes, refs


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
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ):
        with pytest.raises(ProposalError, match="root must be a Path"):
            with worker_correction_continuation_context(
                "/not/a/path", {"role": "worker"}
            ):
                pass
        with pytest.raises(ProposalError, match="request must be dict"):
            with worker_correction_continuation_context(
                Path("/test"), "not-dict"
            ):
                pass
        with pytest.raises(ProposalError, match="role must be 'worker'"):
            with worker_correction_continuation_context(
                Path("/test"), {"role": "reviewer"}
            ):
                pass
        assert lock.acquire_count == 0


@pytest.mark.parametrize("worker_index", [1, 2])
def test_happy_worker_correction_lock_held(tmp_path: Path, worker_index: int):
    req, admitted, receipt, artifacts, evidence, status_bytes, refs = (
        _base_fixtures(worker_index)
    )
    lock = MockLock()
    inside_body_locked = False

    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ) as mock_admission, patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ) as mock_read_record, patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ) as mock_derive_artifacts, patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ) as mock_load_evidence, patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ) as mock_bounded_bytes, patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ) as mock_ref_snapshot, patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ) as mock_check_state:

        with worker_correction_continuation_context(tmp_path, req) as ctx:
            inside_body_locked = lock.locked
            assert ctx["continuation_run_prefix"] == f"run-w{worker_index}"
            assert ctx["original_run_prefix"] == "run-orig"
            assert ctx["source_head"] == "c44e63"
            assert ctx["initial_refs"] == refs
            assert ctx["original_receipt"] == receipt
            assert ctx["original_terminal_receipt"] == receipt
            assert ctx["admitted_request"] == admitted
            assert ctx["admitted_task_path"] == "t.json"
            assert ctx["artifacts"] == artifacts
            assert ctx["evidence"] == evidence
            assert ctx["task"] == evidence["task"]
            assert ctx["sources"] == evidence["sources"]

            # Validate deep copy
            ctx["task"]["goal"] = "mutated"
            assert evidence["task"]["goal"] == "fix bug"

        assert inside_body_locked is True
        assert lock.locked is False
        assert lock.acquire_count == 1
        assert lock.release_count == 1
        assert mock_admission.call_count == 2
        assert mock_read_record.call_count == 2
        assert mock_derive_artifacts.call_count == 2
        assert mock_load_evidence.call_count == 2
        assert mock_bounded_bytes.call_count == 2
        assert mock_ref_snapshot.call_count == 1
        assert mock_check_state.call_count == 2

        prepared = prepare_worker_correction_continuation(tmp_path, req)
        assert prepared["continuation_run_prefix"] == f"run-w{worker_index}"
        assert lock.locked is False


def test_worker_0_refused(tmp_path: Path):
    req, admitted, receipt, _, evidence, status_bytes, refs = _base_fixtures(0)
    artifacts_w0 = {
        "worker_index": 0,
        "source_head": "c44e63",
        "original_run_prefix": "run-orig",
        "reviewer_path": ".local/two-site-refactor/runs/run-rev/reviewer_result.json",
    }
    lock = MockLock()

    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts_w0,
    ):
        with pytest.raises(ProposalError, match="worker_index must be 1 or 2, got 0"):
            with worker_correction_continuation_context(tmp_path, req):
                pass

        assert lock.locked is False


@pytest.mark.parametrize("invalid_index", [-1, 3, "1", True, None])
def test_worker_invalid_index_refused(tmp_path: Path, invalid_index):
    req, admitted, receipt, _, evidence, status_bytes, refs = _base_fixtures(1)
    artifacts_invalid = {
        "worker_index": invalid_index,
        "source_head": "c44e63",
        "original_run_prefix": "run-orig",
        "reviewer_path": ".local/two-site-refactor/runs/run-rev/reviewer_result.json",
    }
    lock = MockLock()

    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts_invalid,
    ):
        with pytest.raises(ProposalError, match="worker_index must be 1 or 2"):
            with worker_correction_continuation_context(tmp_path, req):
                pass

        assert lock.locked is False


def test_drift_refused(tmp_path: Path):
    req, admitted, receipt, artifacts, evidence, status_bytes, refs = _base_fixtures(1)
    lock = MockLock()

    # 1. Admitted request drift
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        side_effect=[
            (admitted, "t.json", "res"),
            ({**admitted, "drift": True}, "t.json", "res"),
        ],
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(ProposalError, match="continuation request drifted"):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False

    # 2. Admitted task path drift
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        side_effect=[
            (admitted, "t1.json", "res"),
            (admitted, "t2.json", "res"),
        ],
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(ProposalError, match="admitted task path drifted"):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False

    # 3. Terminal receipt drift
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        side_effect=[receipt, {**receipt, "status": "MUTATED"}],
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(ProposalError, match="terminal receipt drifted"):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False

    # 4. Artifacts drift
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        side_effect=[artifacts, {**artifacts, "source_head": "drifted"}],
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(ProposalError, match="resume artifacts drifted"):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False

    # 5. Evidence drift
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        side_effect=[evidence, {**evidence, "feedback": {"mutated": True}}],
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(ProposalError, match="correction evidence drifted"):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False

    # 6. Status bytes drift
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        side_effect=[status_bytes, b'{"status": "DIFFERENT"}'],
    ):
        with pytest.raises(
            ProposalError, match="prior reviewer status bytes drifted"
        ):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False

    # 7. Check state drift on second verification
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
        side_effect=[None, ProposalError("git ref mismatch")],
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(ProposalError, match="git ref mismatch"):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False


def test_lock_released_on_body_exception(tmp_path: Path):
    req, admitted, receipt, artifacts, evidence, status_bytes, refs = (
        _base_fixtures(1)
    )
    lock = MockLock()

    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(RuntimeError, match="simulated caller error"):
            with worker_correction_continuation_context(tmp_path, req):
                assert lock.locked is True
                raise RuntimeError("simulated caller error")

        assert lock.locked is False
        assert lock.release_count == 1


def test_first_and_second_admission_stop_propagation(tmp_path: Path):
    req, admitted, receipt, artifacts, evidence, status_bytes, refs = (
        _base_fixtures(1)
    )
    lock = MockLock()
    stop_err = ContinuationAdmissionError("autopilot STOP sentinel exists")

    # First admission stop
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        side_effect=stop_err,
    ):
        with pytest.raises(
            ContinuationAdmissionError, match="autopilot STOP sentinel exists"
        ):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False
        assert lock.acquire_count == 1
        assert lock.release_count == 1

    # Second admission stop
    lock2 = MockLock()
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock2,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        side_effect=[(admitted, "t.json", "res"), stop_err],
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
        return_value=status_bytes,
    ):
        with pytest.raises(
            ContinuationAdmissionError, match="autopilot STOP sentinel exists"
        ):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock2.locked is False
        assert lock2.acquire_count == 1
        assert lock2.release_count == 1


def test_no_provider_calls_or_execution_authority():
    import scripts.two_site_quota_resume_worker_correction_context as mod

    forbidden = [
        "runner_run",
        "run_provider_session",
        "execute_pipeline",
        "run_pipeline",
    ]
    for symbol in forbidden:
        assert not hasattr(
            mod, symbol
        ), f"forbidden symbol {symbol} exposed in worker correction context"


@pytest.mark.parametrize("worker_index", [1, 2])
@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
def test_happy_test_gate_origin_lock_held(
    tmp_path: Path, worker_index: int, origin: str
):
    req, admitted, receipt, artifacts, evidence, _, refs = _base_fixtures(
        worker_index
    )
    artifacts.update({
        "correction_origin": origin,
        "reviewer_path": None,
        "final_reviewer_run_id": None,
    })
    evidence.update({
        "reviewer_status": None,
        "reviewer_result": None,
        "reviewer_sha": None,
    })
    lock = MockLock()
    inside_body_locked = False

    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ) as mock_admission, patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ) as mock_read_record, patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ) as mock_derive_artifacts, patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ) as mock_load_evidence, patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
    ) as mock_bounded_bytes, patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ) as mock_ref_snapshot, patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ) as mock_check_state:
        with worker_correction_continuation_context(tmp_path, req) as ctx:
            inside_body_locked = lock.locked
            assert ctx["continuation_run_prefix"] == f"run-w{worker_index}"
            assert ctx["original_run_prefix"] == "run-orig"
            assert ctx["source_head"] == "c44e63"
            assert ctx["initial_refs"] == refs
            assert ctx["original_receipt"] == receipt
            assert ctx["original_terminal_receipt"] == receipt
            assert ctx["admitted_request"] == admitted
            assert ctx["admitted_task_path"] == "t.json"
            assert ctx["artifacts"] == artifacts
            assert ctx["evidence"] == evidence
            assert ctx["task"] == evidence["task"]
            assert ctx["sources"] == evidence["sources"]
            assert ctx["artifacts"]["correction_origin"] == origin
            assert ctx["artifacts"]["reviewer_path"] is None
            assert ctx["artifacts"]["final_reviewer_run_id"] is None
            assert ctx["evidence"]["reviewer_status"] is None
            assert ctx["evidence"]["reviewer_result"] is None
            assert ctx["evidence"]["reviewer_sha"] is None

        assert inside_body_locked is True
        assert lock.locked is False
        assert lock.acquire_count == 1
        assert lock.release_count == 1
        assert mock_admission.call_count == 2
        assert mock_read_record.call_count == 2
        assert mock_derive_artifacts.call_count == 2
        assert mock_load_evidence.call_count == 2
        assert mock_bounded_bytes.call_count == 0
        assert mock_ref_snapshot.call_count == 1
        assert mock_check_state.call_count == 2

        prepared = prepare_worker_correction_continuation(tmp_path, req)
        assert prepared["continuation_run_prefix"] == f"run-w{worker_index}"
        assert prepared["artifacts"]["correction_origin"] == origin
        assert lock.locked is False


@pytest.mark.parametrize(
    "origin, artifact_patch, evidence_patch, expected_match",
    [
        (
            "BOOTSTRAP",
            {"reviewer_path": ".local/rev.json"},
            {},
            "artifacts reviewer_path must be None for BOOTSTRAP",
        ),
        (
            "TEST_FAILED",
            {"reviewer_path": ".local/rev.json"},
            {},
            "artifacts reviewer_path must be None for TEST_FAILED",
        ),
        (
            "BOOTSTRAP",
            {"final_reviewer_run_id": "run-rev-1"},
            {},
            "artifacts final_reviewer_run_id must be None for BOOTSTRAP",
        ),
        (
            "TEST_FAILED",
            {"final_reviewer_run_id": "run-rev-2"},
            {},
            "artifacts final_reviewer_run_id must be None for TEST_FAILED",
        ),
        (
            "BOOTSTRAP",
            {},
            {"reviewer_status": "FAILED"},
            "evidence reviewer_status must be None for BOOTSTRAP",
        ),
        (
            "TEST_FAILED",
            {},
            {"reviewer_result": {"status": "error"}},
            "evidence reviewer_result must be None for TEST_FAILED",
        ),
        (
            "BOOTSTRAP",
            {},
            {"reviewer_sha": "abc123"},
            "evidence reviewer_sha must be None for BOOTSTRAP",
        ),
        (
            "UNKNOWN_ORIGIN",
            {},
            {},
            "artifacts correction_origin invalid: 'UNKNOWN_ORIGIN'",
        ),
    ],
)
def test_test_gate_origin_validation_negatives(
    tmp_path: Path,
    origin: str,
    artifact_patch: dict,
    evidence_patch: dict,
    expected_match: str,
):
    req, admitted, receipt, artifacts, evidence, _, refs = _base_fixtures(1)
    artifacts.update({
        "correction_origin": origin,
        "reviewer_path": None,
        "final_reviewer_run_id": None,
        **artifact_patch,
    })
    evidence.update({
        "reviewer_status": None,
        "reviewer_result": None,
        "reviewer_sha": None,
        **evidence_patch,
    })
    lock = MockLock()
    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        return_value=artifacts,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        return_value=evidence,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ):
        with pytest.raises(ProposalError, match=expected_match):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False
        assert lock.release_count == 1


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
@pytest.mark.parametrize(
    "drift_type, expected_match",
    [
        ("artifacts", "resume artifacts drifted across re-admission"),
        ("evidence", "correction evidence drifted across re-admission"),
    ],
)
def test_test_gate_second_pass_drift_refused(
    tmp_path: Path, origin: str, drift_type: str, expected_match: str
):
    req, admitted, receipt, artifacts, evidence, _, refs = _base_fixtures(1)
    artifacts.update({
        "correction_origin": origin,
        "reviewer_path": None,
        "final_reviewer_run_id": None,
    })
    evidence.update({
        "reviewer_status": None,
        "reviewer_result": None,
        "reviewer_sha": None,
    })
    lock = MockLock()
    drifted_artifacts = {**artifacts, "source_head": "drifted"}
    drifted_evidence = {**evidence, "feedback": {"mutated": True}}

    derive_side_effect = (
        [artifacts, drifted_artifacts]
        if drift_type == "artifacts"
        else [artifacts, artifacts]
    )
    load_evidence_side_effect = (
        [evidence, drifted_evidence]
        if drift_type == "evidence"
        else [evidence, evidence]
    )

    with patch(
        "scripts.two_site_quota_resume_worker_correction_context.pipeline_lock",
        lock,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.validate_quota_continuation_request",
        return_value=(admitted, "t.json", "res"),
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.read_terminal_quota_record",
        return_value=receipt,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.derive_worker_resume_artifacts",
        side_effect=derive_side_effect,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.load_worker_correction_evidence",
        side_effect=load_evidence_side_effect,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.ref_snapshot",
        return_value=refs,
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.check_state",
    ), patch(
        "scripts.two_site_quota_resume_worker_correction_context.bounded_file_bytes",
    ) as mock_bounded:
        with pytest.raises(ProposalError, match=expected_match):
            with worker_correction_continuation_context(tmp_path, req):
                pass
        assert lock.locked is False
        assert lock.release_count == 1
        assert mock_bounded.call_count == 0
