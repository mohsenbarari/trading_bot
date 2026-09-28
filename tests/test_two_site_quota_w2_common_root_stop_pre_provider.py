"""Hermetic test proof for autopilot STOP sentinel rejection prior to Worker2 execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_proposal_pipeline import pipeline_lock
from scripts.two_site_provider_broker import read_terminal_quota_bytes
from scripts.two_site_quota_continuation_admission import (
    ContinuationAdmissionError,
)
from scripts.two_site_quota_resume_role_dispatch import (
    dispatch_quota_continuation_once,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)

# observational quota, no replay authority


@pytest.mark.parametrize("origin", ["REVIEWER", "BOOTSTRAP", "TEST_FAILED"])
def test_two_site_quota_w2_common_root_stop_pre_provider(
    origin: str,
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(
        broker_repo,
        frozen_clock_dt,
        origin=origin,
    )

    root: Path = ctx["root"]
    task: Path = ctx["task"]
    sc: dict[str, Any] = ctx["sc"]
    runs_dir: Path = ctx["runs_dir"]
    controller_run_id: str = ctx["controller_run_id"]
    wait_file: Path = ctx["wait_file"]
    raw_wait_bytes: bytes = ctx["raw_wait_bytes"]
    orig_wait_sha256: str = ctx["orig_wait_sha256"]
    claim_file: Path = ctx["claim_file"]
    persisted_claim_bytes: bytes = ctx["persisted_claim_bytes"]
    claim_sha256: str = ctx["claim_sha256"]
    resume_request: dict[str, Any] = ctx["resume_request"]
    expected_runs: set[str] = ctx["expected_runs"]

    stop_dir = root / ".local" / "two-site-refactor" / "autopilot"
    stop_dir.mkdir(parents=True, exist_ok=True)
    stop_file = stop_dir / "STOP"
    stop_content = "STOP\n"
    stop_file.write_text(stop_content, encoding="utf-8")

    def _fail_worker_runner(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "Worker2 runner must not be called when STOP sentinel exists"
        )

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_two_once.runner_run",
        _fail_worker_runner,
    )

    with pytest.raises(ContinuationAdmissionError) as exc_info:
        dispatch_quota_continuation_once(root, resume_request)
    assert "autopilot STOP sentinel exists" in str(exc_info.value)

    assert stop_file.is_file()
    assert stop_file.read_text(encoding="utf-8") == stop_content

    expected_worker_id = f"{resume_request['run_prefix']}-worker-2"
    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    pipeline_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / resume_request["run_prefix"]
    )
    expected_test_receipt_path = pipeline_dir / "pre-review-tests-2.json"

    assert not (proposals_dir / expected_worker_id).exists()
    assert not expected_test_receipt_path.exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-reviewer-2").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-worker-3").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-reviewer-3").exists()
    assert not (pipeline_dir / "feedback-correction-3.json").exists()

    assert set(os.listdir(proposals_dir)) == expected_runs

    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
    assert wait_file.read_bytes() == raw_wait_bytes
    assert hashlib.sha256(wait_file.read_bytes()).hexdigest() == orig_wait_sha256
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert hashlib.sha256(claim_file.read_bytes()).hexdigest() == claim_sha256

    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert hashlib.sha256(task.read_bytes()).hexdigest() == sc["task_sha_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
    )
    assert (
        hashlib.sha256(sc["raw_receipt_bytes"]).hexdigest()
        == sc["receipt_sha_snapshot"]
    )
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    if origin == "REVIEWER":
        assert sc["r1_status_path"] is not None
        assert sc["r1_bytes_snapshot"] is not None
        assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    elif origin == "BOOTSTRAP":
        assert sc.get("r1_status_path") is None
        assert sc.get("r1_bytes_snapshot") is None
        assert not (
            proposals_dir / f"{sc['request']['run_prefix']}-reviewer-1"
        ).exists()
        assert not (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["request"]["run_prefix"]
            / "pre-review-tests-1.json"
        ).exists()
    elif origin == "TEST_FAILED":
        assert sc.get("r1_status_path") is None
        assert sc.get("r1_bytes_snapshot") is None
        assert not (
            proposals_dir / f"{sc['request']['run_prefix']}-reviewer-1"
        ).exists()
        receipt1_path = (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["request"]["run_prefix"]
            / "pre-review-tests-1.json"
        )
        assert receipt1_path.is_file()
        receipt1_bytes = receipt1_path.read_bytes()
        receipt1_data = json.loads(receipt1_bytes.decode("utf-8"))
        assert receipt1_data["state"] == "FAILED"
        assert int(receipt1_data["returncode"]) != 0
        expected_test_sha = json.loads(sc["response"]["stdout"])
        assert (
            hashlib.sha256(receipt1_bytes).hexdigest()
            == expected_test_sha["pre_review_test_sha256"]
        )
    else:
        raise AssertionError(f"unexpected origin: {origin!r}")

    with pipeline_lock(root):
        pass

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}
