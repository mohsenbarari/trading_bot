"""Integration tests verifying real file-backed storage admission for quota continuation requests."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
from typing import Any
from unittest import mock

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

try:
    from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
    from scripts.two_site_quota_continuation_admission import (
        ContinuationAdmissionError,
        validate_quota_continuation_request,
    )
    from scripts.two_site_quota_resume_claim_storage import (
        publish_known_reset_resume_claim,
    )
    from scripts.two_site_quota_wait_storage import (
        publish_initial_wait_record_bytes,
    )
except ModuleNotFoundError:
    from two_site_codex_autopilot import exclusive_supervisor_lock  # type: ignore[no-redef]
    from two_site_quota_continuation_admission import (  # type: ignore[no-redef]
        ContinuationAdmissionError,
        validate_quota_continuation_request,
    )
    from two_site_quota_resume_claim_storage import (  # type: ignore[no-redef]
        publish_known_reset_resume_claim,
    )
    from two_site_quota_wait_storage import (  # type: ignore[no-redef]
        publish_initial_wait_record_bytes,
    )

from tests.test_two_site_provider_broker_cycle_reservation_read import (
    _setup_cycle_root,
    _write_startup_json,
)
from tests.test_two_site_quota_continuation_broker_admission import (
    _canonical_bytes,
    _setup_valid_scenario,
)


def _materialize_and_publish_storage(
    tmp_path: Path,
    sc: dict[str, Any],
) -> tuple[Path, Path, Path, Path, bytes]:
    orig_ctrl_run_id = sc["record"]["controller_run_id"]
    root, runs_dir = _setup_cycle_root(str(tmp_path), orig_ctrl_run_id)
    _write_startup_json(
        runs_dir,
        cycle_id=orig_ctrl_run_id,
        source_head=sc["record"]["reservation"]["source_head"],
        assignment_sha256=sc["record"]["reservation"]["assignment_sha256"],
    )
    autopilot_dir = root / ".local" / "two-site-refactor" / "autopilot"
    lock_file = autopilot_dir / "supervisor.lock"

    with exclusive_supervisor_lock(lock_file):
        published_wait_bytes = publish_initial_wait_record_bytes(
            root,
            orig_ctrl_run_id,
            sc["raw_wait_bytes"],
        )
        published_claim = publish_known_reset_resume_claim(
            root,
            orig_ctrl_run_id,
            sc["claim"],
            sc["expected_claim"],
        )

    assert published_wait_bytes == sc["raw_wait_bytes"]
    assert published_claim == sc["claim"]

    wait_file = runs_dir / "quota-wait.json"
    claim_file = runs_dir / "quota-resume-claim.json"
    claim_bytes = _canonical_bytes(sc["claim"])

    assert wait_file.is_file()
    assert claim_file.is_file()
    assert wait_file.read_bytes() == sc["raw_wait_bytes"]
    assert claim_file.read_bytes() == claim_bytes
    assert stat.S_IMODE(os.lstat(wait_file).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(claim_file).st_mode) == 0o600

    return root, runs_dir, wait_file, claim_file, claim_bytes


def test_quota_continuation_admission_storage_integration_success(
    tmp_path: Path,
) -> None:
    sc = _setup_valid_scenario(tmp_path)
    root, _runs_dir, wait_file, claim_file, claim_bytes = _materialize_and_publish_storage(
        tmp_path, sc
    )

    request = sc["request"]
    now_epoch = sc["now_epoch"]

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent") as mock_intent,
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected") as mock_collect,
        mock.patch("scripts.two_site_quota_continuation_admission.load_task") as mock_task,
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt") as mock_sem,
    ):
        mock_val.return_value = (
            {k: request[k] for k in request if k != "request_type"},
            sc["task_path"],
            sc["fake_scope"],
        )
        mock_intent.return_value = (sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])
        mock_collect.return_value = sc["expected_wait"]
        mock_task.return_value = (sc["task_obj"], {})
        mock_sem.return_value = sc["semantic_attempt"]

        res, out_task_path, out_scope = validate_quota_continuation_request(
            root,
            request,
            now_epoch=now_epoch,
        )

        assert res["request_id"] == sc["request"]["request_id"]
        assert res["request_type"] == "QUOTA_CONTINUATION"
        assert res == sc["request"]
        assert out_task_path == sc["task_path"]
        assert out_scope == sc["fake_scope"]

        assert mock_val.call_count == 2
        assert mock_intent.call_count == 2
        assert mock_collect.call_count == 2
        assert mock_task.call_count == 2
        assert mock_sem.call_count == 2

        assert wait_file.read_bytes() == sc["raw_wait_bytes"]
        assert claim_file.read_bytes() == claim_bytes
        assert stat.S_IMODE(os.lstat(wait_file).st_mode) == 0o600
        assert stat.S_IMODE(os.lstat(claim_file).st_mode) == 0o600


@pytest.mark.parametrize(
    ("tamper_mode", "expected_pattern", "expected_task_calls"),
    [
        (
            "wait_mode_mismatch",
            "failed to read initial wait record:.*mode must be 0600",
            0,
        ),
        (
            "wait_content_tamper",
            "failed to read initial wait record: initial wait record validation failed",
            0,
        ),
        (
            "claim_tamper",
            "failed to read known reset resume claim: known-reset resume claim validation failed",
            1,
        ),
    ],
)
def test_quota_continuation_admission_storage_integration_persisted_tamper_rejection(
    tmp_path: Path,
    tamper_mode: str,
    expected_pattern: str,
    expected_task_calls: int,
) -> None:
    sc = _setup_valid_scenario(tmp_path)
    root, _runs_dir, wait_file, claim_file, claim_bytes = _materialize_and_publish_storage(
        tmp_path, sc
    )

    request = sc["request"]
    now_epoch = sc["now_epoch"]

    if tamper_mode == "wait_mode_mismatch":
        os.chmod(wait_file, 0o644)
    elif tamper_mode == "wait_content_tamper":
        tampered_record = copy.deepcopy(sc["record"])
        tampered_record["terminal_receipt_sha256"] = "0" * 64
        tampered_bytes = _canonical_bytes(tampered_record)
        wait_file.write_bytes(tampered_bytes)
        os.chmod(wait_file, 0o600)
    elif tamper_mode == "claim_tamper":
        tampered_claim = copy.deepcopy(sc["claim"])
        tampered_claim["remaining_resume_budget_after"] = 99
        tampered_claim_bytes = _canonical_bytes(tampered_claim)
        claim_file.write_bytes(tampered_claim_bytes)
        os.chmod(claim_file, 0o600)
    else:
        raise ValueError(f"unknown tamper mode {tamper_mode}")

    persisted_wait_bytes_before = wait_file.read_bytes()
    persisted_wait_mode_before = stat.S_IMODE(os.lstat(wait_file).st_mode)
    persisted_claim_bytes_before = claim_file.read_bytes()
    persisted_claim_mode_before = stat.S_IMODE(os.lstat(claim_file).st_mode)

    with (
        mock.patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        mock.patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent") as mock_intent,
        mock.patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected") as mock_collect,
        mock.patch("scripts.two_site_quota_continuation_admission.load_task") as mock_task,
        mock.patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt") as mock_sem,
    ):
        mock_val.return_value = (
            {k: request[k] for k in request if k != "request_type"},
            sc["task_path"],
            sc["fake_scope"],
        )
        mock_intent.return_value = (sc["intent_epoch"], sc["intent_attempt"], sc["intent_repo"])
        mock_collect.return_value = sc["expected_wait"]
        mock_task.return_value = (sc["task_obj"], {})
        mock_sem.return_value = sc["semantic_attempt"]

        with pytest.raises(ContinuationAdmissionError, match=expected_pattern):
            validate_quota_continuation_request(
                root,
                request,
                now_epoch=now_epoch,
            )

        assert mock_task.call_count == expected_task_calls
        assert mock_sem.call_count == expected_task_calls
        assert mock_val.call_count == 1

        assert wait_file.read_bytes() == persisted_wait_bytes_before
        assert stat.S_IMODE(os.lstat(wait_file).st_mode) == persisted_wait_mode_before
        assert claim_file.read_bytes() == persisted_claim_bytes_before
        assert stat.S_IMODE(os.lstat(claim_file).st_mode) == persisted_claim_mode_before
