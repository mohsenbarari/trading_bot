"""Real-file positive, STOP, and NOT_DUE tests for supervisor resume context."""
from __future__ import annotations

from datetime import datetime, timedelta
import os
from typing import Any
import unittest.mock

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_provider_broker import broker_paths
from scripts.two_site_quota_supervisor_resume_context import (
    ResumeContextError,
    collect_known_reset_resume_context_locked,
)
from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
from scripts.two_site_quota_wait_storage import read_initial_wait_record_bytes
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_real_source_join_fileproof import (
    _setup_scenario_through_receipt,
)


def _collect_locked(env: dict[str, Any], timestamp: str) -> dict[str, Any]:
    with exclusive_supervisor_lock(env["autopilot_dir"] / "supervisor.lock"):
        return collect_known_reset_resume_context_locked(
            env["root"], env["original_ctrl_id"], timestamp
        )


def _admit_wait(sc: dict[str, Any]) -> dict[str, Any]:
    with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
        return admit_initial_quota_wait_locked(
            sc["root"],
            sc["original_ctrl_id"],
            clock=lambda: sc["now_utc"] - timedelta(seconds=120),
        )


def test_quota_supervisor_resume_context_positive(broker_repo: Any) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    req_dir = broker_paths(sc["root"])[2]
    initial_requests = sorted(os.listdir(req_dir))
    claim_path = sc["runs_dir"] / "quota-resume-claim.json"
    sub_path = sc["runs_dir"] / "quota-resume-submission.json"
    assert not claim_path.exists() and not sub_path.exists()

    wait_record = _admit_wait(sc)
    with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
        expected_wait = collect_initial_wait_source_expected(
            sc["root"], sc["original_ctrl_id"], sc["intent_epoch"], 1
        )
        raw_wait_bytes = read_initial_wait_record_bytes(
            sc["root"], sc["original_ctrl_id"]
        )

    next_check = datetime.fromisoformat(
        wait_record["schedule"]["next_check_at_utc"].replace("Z", "+00:00")
    )
    timestamp = (next_check + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    res = _collect_locked(sc, timestamp)
    assert set(res) == {
        "original_controller_run_id",
        "wait_record",
        "expected_wait",
        "raw_wait_bytes",
        "semantic_attempt",
        "expected_task_id",
        "due",
    }
    assert res["original_controller_run_id"] == sc["original_ctrl_id"]
    assert res["wait_record"] == wait_record
    assert res["expected_wait"] == expected_wait
    assert res["raw_wait_bytes"] == raw_wait_bytes
    assert res["semantic_attempt"]["fingerprint"] == sc["identity"]["fingerprint"]
    assert res["expected_task_id"] == sc["loaded_task"]["task_id"]
    assert res["due"]["state"] == "KNOWN_RESET_REVALIDATION_DUE"

    assert not claim_path.exists() and not sub_path.exists()
    assert sorted(os.listdir(req_dir)) == initial_requests


def test_quota_supervisor_resume_context_stop(
    broker_repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    wait_record = _admit_wait(sc)
    next_check = datetime.fromisoformat(
        wait_record["schedule"]["next_check_at_utc"].replace("Z", "+00:00")
    )
    timestamp = (next_check + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    (sc["autopilot_dir"] / "STOP").write_text("STOP\n")
    (sc["root"] / "STOP").write_text("STOP\n")

    mock_load = unittest.mock.MagicMock()
    mock_semantic = unittest.mock.MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.load_task", mock_load
    )
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.read_existing_semantic_attempt",
        mock_semantic,
    )

    with pytest.raises(ResumeContextError) as exc_info:
        _collect_locked(sc, timestamp)

    assert "STOP" in str(exc_info.value)
    mock_load.assert_not_called()
    mock_semantic.assert_not_called()


def test_quota_supervisor_resume_context_not_due(
    broker_repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    wait_record = _admit_wait(sc)
    created_at_utc = wait_record["schedule"]["created_at_utc"]

    mock_load = unittest.mock.MagicMock()
    mock_semantic = unittest.mock.MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.load_task", mock_load
    )
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.read_existing_semantic_attempt",
        mock_semantic,
    )

    with pytest.raises(ResumeContextError) as exc_info:
        _collect_locked(sc, created_at_utc)

    assert "NOT_DUE" in str(exc_info.value)
    mock_load.assert_not_called()
    mock_semantic.assert_not_called()
