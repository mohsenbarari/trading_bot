"""Hermetic unit tests for scripts/two_site_quota_continuation_admission.py."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest import mock
import pytest

from scripts.two_site_quota_continuation_admission import (
    ContinuationAdmissionError,
    _check_stop,
    validate_quota_continuation_request,
)
from scripts.two_site_quota_wait_record import validate_initial_wait_record
from scripts.two_site_quota_resume_claim_admission import prepare_known_reset_resume_claim
from scripts.two_site_quota_resume_request_record import build_quota_resume_request
from tests.test_two_site_quota_resume_claim_admission import (
    _build_valid_semantic_attempt,
    _build_valid_wait_fixtures,
    _step_iso_seconds,
)

_CYCLE_ID = "cycle-2026-20260925T010203Z-123"
_CONT_CYCLE_ID = "cycle-2026-20260925T020304Z-456"
_REQUEST_ID = "0123456789abcdef0123456789abcdef"
_RESUME_REQUEST_ID = "fedcba9876543210fedcba9876543210"
_TASK_ID = "task-ae03-c5b-001"
_RUN_PREFIX = "ae03.c5b-test"


class _FakeScope:
    def __init__(self, assignment_id: str, sha256: str):
        self.assignment_id = assignment_id
        self.sha256 = sha256

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _FakeScope):
            return False
        return self.assignment_id == other.assignment_id and self.sha256 == other.sha256


def _canonical_bytes(data: Any) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _setup_valid_scenario(
    tmp_path: Path,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    raw_wait_bytes = _canonical_bytes(record)
    wait_sha = hashlib.sha256(raw_wait_bytes).hexdigest()

    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]
    claimed_at = _step_iso_seconds(due_time, 10)
    claim_dt = datetime.fromisoformat(claimed_at.replace("Z", "+00:00"))
    claim_epoch = claim_dt.timestamp()
    effective_now = claim_epoch + 5.0 if now_epoch is None else now_epoch

    prep = prepare_known_reset_resume_claim(
        wait_record=record,
        expected_wait=expected_wait,
        semantic_attempt=semantic_attempt,
        expected_task_id=_TASK_ID,
        original_wait_sha256=wait_sha,
        continuation_controller_run_id=_CONT_CYCLE_ID,
        resume_request_id=_RESUME_REQUEST_ID,
        claimed_at_utc=claimed_at,
    )
    claim = prep["claim"]
    expected_claim = prep["expected"]
    claim_bytes = _canonical_bytes(claim)
    claim_sha = hashlib.sha256(claim_bytes).hexdigest()

    request = build_quota_resume_request(
        claim=claim,
        expected_claim=expected_claim,
        expected_claim_sha256=claim_sha,
        timeout=60,
        now_epoch=effective_now,
    )

    task_obj = {"task_id": _TASK_ID, "goal": "test goal"}
    intent_epoch = record["rescope_epoch"]
    intent_attempt = record["rescope_attempt"]
    intent_repo = copy.deepcopy(expected_wait["repository"])

    fake_scope = _FakeScope(record["reservation"]["assignment_id"], record["reservation"]["assignment_sha256"])
    task_path = tmp_path / record["reservation"]["task_path"]

    return {
        "record": record,
        "expected_wait": expected_wait,
        "raw_wait_bytes": raw_wait_bytes,
        "wait_sha": wait_sha,
        "semantic_attempt": semantic_attempt,
        "claim": claim,
        "expected_claim": expected_claim,
        "claim_sha": claim_sha,
        "request": request,
        "task_obj": task_obj,
        "intent_epoch": intent_epoch,
        "intent_attempt": intent_attempt,
        "intent_repo": intent_repo,
        "fake_scope": fake_scope,
        "task_path": task_path,
        "now_epoch": effective_now,
    }


def test_quota_continuation_admission_success(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    request = sc["request"]
    now_epoch = sc["now_epoch"]

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent") as mock_intent,
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected") as mock_collect,
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes") as mock_bytes,
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record") as mock_wait,
        mock.patch("scripts.two_site_quota_continuation_admission.load_task") as mock_task,
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt") as mock_sem,
        mock.patch("scripts.two_site_quota_continuation_admission.read_known_reset_resume_claim") as mock_claim,
    ):
        mock_val.return_value = ({k: request[k] for k in request if k != "request_type"}, sc["task_path"], sc["fake_scope"])
        mock_intent.return_value = (sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])
        mock_collect.return_value = sc["expected_wait"]
        mock_bytes.return_value = sc["raw_wait_bytes"]
        mock_wait.return_value = sc["record"]
        mock_task.return_value = (sc["task_obj"], {})
        mock_sem.return_value = sc["semantic_attempt"]
        mock_claim.return_value = sc["claim"]

        res, out_task_path, out_scope = validate_quota_continuation_request(
            tmp_path,
            request,
            now_epoch=now_epoch,
        )

        assert res["request_id"] == sc["request"]["request_id"]
        assert res["request_type"] == "QUOTA_CONTINUATION"
        assert out_task_path == sc["task_path"]
        assert out_scope == sc["fake_scope"]
        assert mock_val.call_count == 2
        assert mock_intent.call_count == 2
        assert mock_collect.call_count == 2
        assert mock_bytes.call_count == 2
        assert mock_wait.call_count == 2
        assert mock_task.call_count == 2
        assert mock_sem.call_count == 2
        assert mock_claim.call_count == 2


def test_stop_sentinel_initial_rejection(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    stop_file = tmp_path / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("STOP\n", encoding="utf-8")

    with mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val:
        with pytest.raises(ContinuationAdmissionError, match="autopilot STOP sentinel exists"):
            validate_quota_continuation_request(tmp_path, sc["request"], now_epoch=sc["now_epoch"])
        assert mock_val.call_count == 0


def test_stop_sentinel_dangling_symlink_rejection(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    stop_file = tmp_path / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(tmp_path / "nonexistent-target", stop_file)

    assert os.path.lexists(stop_file)
    with pytest.raises(FileNotFoundError):
        stop_file.stat()

    with pytest.raises(ContinuationAdmissionError, match="autopilot STOP sentinel exists"):
        _check_stop(tmp_path)

    with mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val:
        with pytest.raises(ContinuationAdmissionError, match="autopilot STOP sentinel exists"):
            validate_quota_continuation_request(tmp_path, sc["request"], now_epoch=sc["now_epoch"])
        assert mock_val.call_count == 0


def test_stop_sentinel_oserror_rejection(tmp_path: Path) -> None:
    real_lstat = os.lstat

    def _faulty_lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        if "STOP" in str(path):
            raise PermissionError("permission denied")
        return real_lstat(path, *args, **kwargs)

    with mock.patch("os.lstat", side_effect=_faulty_lstat):
        with pytest.raises(ContinuationAdmissionError, match="failed to inspect autopilot STOP sentinel"):
            _check_stop(tmp_path)

    with mock.patch("scripts.two_site_quota_continuation_admission.canonical_root", side_effect=PermissionError("permission denied")):
        with pytest.raises(ContinuationAdmissionError, match="failed to inspect autopilot STOP sentinel"):
            _check_stop(tmp_path)


def test_stop_sentinel_introduced_during_reads(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    request = sc["request"]
    now_epoch = sc["now_epoch"]
    stop_file = tmp_path / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)

    def _inject_stop(*_args: Any, **_kwargs: Any) -> tuple[str, int, dict[str, Any]]:
        stop_file.write_text("STOP\n", encoding="utf-8")
        return (sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", side_effect=_inject_stop),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes", return_value=sc["raw_wait_bytes"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record", return_value=sc["record"]),
        mock.patch("scripts.two_site_quota_continuation_admission.load_task", return_value=(sc["task_obj"], {})),
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt", return_value=sc["semantic_attempt"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_known_reset_resume_claim", return_value=sc["claim"]),
    ):
        mock_val.return_value = ({k: request[k] for k in request if k != "request_type"}, sc["task_path"], sc["fake_scope"])
        with pytest.raises(ContinuationAdmissionError, match="autopilot STOP sentinel exists"):
            validate_quota_continuation_request(tmp_path, request, now_epoch=now_epoch)


def test_common_scope_drift_rejection(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    request = sc["request"]
    now_epoch = sc["now_epoch"]
    drifted_scope = _FakeScope(sc["record"]["reservation"]["assignment_id"], "f" * 64)

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", return_value=(sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes", return_value=sc["raw_wait_bytes"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record", return_value=sc["record"]),
        mock.patch("scripts.two_site_quota_continuation_admission.load_task", return_value=(sc["task_obj"], {})),
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt", return_value=sc["semantic_attempt"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_known_reset_resume_claim", return_value=sc["claim"]),
    ):
        common_dict = {k: request[k] for k in request if k != "request_type"}
        mock_val.side_effect = [
            (common_dict, sc["task_path"], sc["fake_scope"]),
            (common_dict, sc["task_path"], drifted_scope),
        ]
        with pytest.raises(ContinuationAdmissionError, match="assignment scope drifted"):
            validate_quota_continuation_request(tmp_path, request, now_epoch=now_epoch)


def test_common_projection_and_task_path_drift(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    request = sc["request"]
    now_epoch = sc["now_epoch"]

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", return_value=(sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes", return_value=sc["raw_wait_bytes"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record", return_value=sc["record"]),
        mock.patch("scripts.two_site_quota_continuation_admission.load_task", return_value=(sc["task_obj"], {})),
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt", return_value=sc["semantic_attempt"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_known_reset_resume_claim", return_value=sc["claim"]),
    ):
        common_dict_1 = {k: request[k] for k in request if k != "request_type"}
        common_dict_2 = dict(common_dict_1)
        common_dict_2["timeout"] = 99

        mock_val.side_effect = [
            (common_dict_1, sc["task_path"], sc["fake_scope"]),
            (common_dict_2, sc["task_path"], sc["fake_scope"]),
        ]
        with pytest.raises(ContinuationAdmissionError, match="common validated request projection drifted"):
            validate_quota_continuation_request(tmp_path, request, now_epoch=now_epoch)

        other_path = tmp_path / "other_task.json"
        mock_val.side_effect = [
            (common_dict_1, sc["task_path"], sc["fake_scope"]),
            (common_dict_1, other_path, sc["fake_scope"]),
        ]
        with pytest.raises(ContinuationAdmissionError, match="task path drifted"):
            validate_quota_continuation_request(tmp_path, request, now_epoch=now_epoch)


def test_repository_intent_drift_rejection(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    drifted_repo = copy.deepcopy(sc["intent_repo"])
    drifted_repo["head"] = "f" * 40
    now_epoch = sc["now_epoch"]

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request", return_value=({k: sc["request"][k] for k in sc["request"] if k != "request_type"}, sc["task_path"], sc["fake_scope"])),
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", return_value=(sc["intent_epoch"], sc["intent_attempt"], drifted_repo)),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
    ):
        with pytest.raises(ContinuationAdmissionError, match="expected wait repository does not match rescope intent repository"):
            validate_quota_continuation_request(tmp_path, sc["request"], now_epoch=now_epoch)


def test_lineage_mismatches(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    now_epoch = sc["now_epoch"]

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request", return_value=({k: sc["request"][k] for k in sc["request"] if k != "request_type"}, sc["task_path"], sc["fake_scope"])),
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", return_value=(sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes", return_value=b'{"wrong": 1}'),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record", return_value=sc["record"]),
    ):
        with pytest.raises(ContinuationAdmissionError, match="original wait SHA mismatch"):
            validate_quota_continuation_request(tmp_path, sc["request"], now_epoch=now_epoch)

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request", return_value=({k: sc["request"][k] for k in sc["request"] if k != "request_type"}, sc["task_path"], sc["fake_scope"])),
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", return_value=(sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes", return_value=sc["raw_wait_bytes"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record", return_value=sc["record"]),
        mock.patch("scripts.two_site_quota_continuation_admission.load_task", return_value=({"task_id": "wrong-task"}, {})),
    ):
        with pytest.raises(ContinuationAdmissionError, match="task_id mismatch"):
            validate_quota_continuation_request(tmp_path, sc["request"], now_epoch=now_epoch)


def test_request_envelope_rejections(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    req = sc["request"]
    now_epoch = sc["now_epoch"]

    with pytest.raises(ContinuationAdmissionError, match="must be dict"):
        validate_quota_continuation_request(tmp_path, ["not", "a", "dict"], now_epoch=now_epoch)

    dup_req_id = copy.deepcopy(req)
    dup_req_id["request_id"] = dup_req_id["original_request_id"]
    with pytest.raises(ContinuationAdmissionError, match="request_id must differ"):
        validate_quota_continuation_request(tmp_path, dup_req_id, now_epoch=now_epoch)

    dup_ctrl_id = copy.deepcopy(req)
    dup_ctrl_id["controller_run_id"] = dup_ctrl_id["original_controller_run_id"]
    with pytest.raises(ContinuationAdmissionError, match="controller_run_id must differ"):
        validate_quota_continuation_request(tmp_path, dup_ctrl_id, now_epoch=now_epoch)

    bad_type = copy.deepcopy(req)
    bad_type["request_type"] = "OTHER"
    with pytest.raises(ContinuationAdmissionError, match="request_type must be 'QUOTA_CONTINUATION'"):
        validate_quota_continuation_request(tmp_path, bad_type, now_epoch=now_epoch)

    extra_key = copy.deepcopy(req)
    extra_key["extra"] = 1
    with pytest.raises(ContinuationAdmissionError, match="keys mismatch"):
        validate_quota_continuation_request(tmp_path, extra_key, now_epoch=now_epoch)

    with pytest.raises(ContinuationAdmissionError, match="now_epoch must be finite numeric"):
        validate_quota_continuation_request(tmp_path, req, now_epoch=True)


def test_wait_pair_cross_read_mismatch_adversarial_rejection(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    record_a = sc["record"]
    raw_wait_bytes_a = sc["raw_wait_bytes"]
    wait_sha_a = sc["wait_sha"]

    record_b = copy.deepcopy(record_a)
    record_b["terminal_receipt_sha256"] = "3" * 64
    assert record_a != record_b

    expected_b = copy.deepcopy(sc["expected_wait"])
    expected_b["terminal_receipt_sha256"] = "3" * 64
    assert validate_initial_wait_record(record_b, expected_b)["terminal_receipt_sha256"] == "3" * 64

    request = sc["request"]
    assert request["original_wait_sha256"] == wait_sha_a

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", return_value=(sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes") as mock_bytes,
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record") as mock_wait,
        mock.patch("scripts.two_site_quota_continuation_admission.load_task") as mock_task,
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt") as mock_sem,
        mock.patch("scripts.two_site_quota_continuation_admission.read_known_reset_resume_claim") as mock_claim,
    ):
        mock_val.return_value = ({k: request[k] for k in request if k != "request_type"}, sc["task_path"], sc["fake_scope"])
        mock_bytes.return_value = raw_wait_bytes_a
        mock_wait.return_value = record_b

        with pytest.raises(ContinuationAdmissionError, match="initial wait record bytes do not match canonical validated record"):
            validate_quota_continuation_request(tmp_path, request, now_epoch=sc["now_epoch"])

        assert mock_bytes.call_count == 1
        assert mock_wait.call_count == 1
        assert mock_claim.call_count == 0
        assert mock_task.call_count == 0
        assert mock_sem.call_count == 0


def test_wait_pair_second_pass_cross_read_mismatch_rejection(tmp_path: Path) -> None:
    sc = _setup_valid_scenario(tmp_path)
    record_a = sc["record"]
    raw_wait_bytes_a = sc["raw_wait_bytes"]

    record_b = copy.deepcopy(record_a)
    record_b["terminal_receipt_sha256"] = "3" * 64

    request = sc["request"]

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent", return_value=(sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])),
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected", return_value=sc["expected_wait"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record_bytes") as mock_bytes,
        mock.patch("scripts.two_site_quota_continuation_admission.read_initial_wait_record") as mock_wait,
        mock.patch("scripts.two_site_quota_continuation_admission.load_task", return_value=(sc["task_obj"], {})),
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt", return_value=sc["semantic_attempt"]),
        mock.patch("scripts.two_site_quota_continuation_admission.read_known_reset_resume_claim", return_value=sc["claim"]) as mock_claim,
    ):
        mock_val.return_value = ({k: request[k] for k in request if k != "request_type"}, sc["task_path"], sc["fake_scope"])
        mock_bytes.side_effect = [raw_wait_bytes_a, raw_wait_bytes_a]
        mock_wait.side_effect = [record_a, record_b]

        with pytest.raises(ContinuationAdmissionError, match="initial wait record re-read bytes do not match canonical validated record"):
            validate_quota_continuation_request(tmp_path, request, now_epoch=sc["now_epoch"])

        assert mock_bytes.call_count == 2
        assert mock_wait.call_count == 2
        assert mock_claim.call_count == 1
