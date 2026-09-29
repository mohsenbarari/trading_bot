"""Real-file hermetic tests for supervisor resume submit bridge."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any
from unittest.mock import patch

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

try:
    from tests.test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )
except ImportError:
    from test_p1_00_characterization_network_isolation import (  # type: ignore
        install_p1_00_network_fence,
    )

install_p1_00_network_fence()

from scripts import two_site_provider_broker as broker
from scripts import two_site_quota_supervisor_resume_submit as submit_mod
from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_resume_claim_request import ClaimRequestError
from scripts.two_site_quota_resume_lineage_snapshot_storage import (
    QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME,
    SnapshotStorageError,
)
from scripts.two_site_quota_supervisor_resume_submit import (
    submit_known_reset_resume_once_locked,
)
from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_real_source_join_fileproof import (
    _setup_scenario_through_receipt,
)


def _setup_admitted_env(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> dict[str, Any]:
    sc = _setup_scenario_through_receipt(*broker_repo)
    with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
        wait_rec = admit_initial_quota_wait_locked(
            sc["root"],
            sc["original_ctrl_id"],
            clock=lambda: sc["now_utc"] - timedelta(seconds=120),
        )
    next_check = datetime.fromisoformat(
        wait_rec["schedule"]["next_check_at_utc"].replace("Z", "+00:00")
    )
    claimed_dt = next_check + timedelta(seconds=10)
    claimed_utc = claimed_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_epoch = max(time.time(), claimed_dt.timestamp() + 1.0)
    req_id = hashlib.sha256(b"two_site_resume_req_id_001").hexdigest()[:32]
    assert req_id != sc["request"]["request_id"]
    return {
        "sc": sc,
        "wait_rec": wait_rec,
        "claimed_utc": claimed_utc,
        "now_epoch": now_epoch,
        "req_id": req_id,
    }


def _call_submit(env: dict[str, Any]) -> dict[str, Any]:
    sc = env["sc"]
    return submit_known_reset_resume_once_locked(
        sc["root"],
        sc["original_ctrl_id"],
        sc["continuation_ctrl_id"],
        env["req_id"],
        env["claimed_utc"],
        60,
        env["now_epoch"],
    )


def test_submit_known_reset_resume_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, _ = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    wait_p = sc["runs_dir"] / "quota-wait.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    real_enqueue = submit_mod.enqueue_quota_continuation_locked
    enqueue_calls: list[dict[str, Any]] = []

    def spy_enqueue(root: Path, request: dict[str, Any], *, now_epoch: float) -> dict[str, Any]:
        assert snap_p.is_file()
        st_s = snap_p.stat()
        assert stat.S_IMODE(st_s.st_mode) == 0o600
        assert st_s.st_nlink == 1
        snap_data = json.loads(snap_p.read_text(encoding="utf-8"))
        assert snap_data["original_controller_run_id"] == sc["original_ctrl_id"]
        assert snap_data["resume_request_id"] == req_id
        assert snap_data["original_wait_sha256"] == hashlib.sha256(wait_p.read_bytes()).hexdigest()
        assert snap_data["claim_sha256"] == hashlib.sha256(claim_p.read_bytes()).hexdigest()
        assert snap_data["expected_request"] == request
        assert not queue_p.exists()
        enqueue_calls.append({"request": copy.deepcopy(request)})
        return real_enqueue(root, request, now_epoch=now_epoch)

    with patch.object(submit_mod, "enqueue_quota_continuation_locked", side_effect=spy_enqueue):
        with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
            res = _call_submit(env)

    assert len(enqueue_calls) == 1
    assert set(res.keys()) == {
        "claim",
        "request",
        "submission_intent",
        "original_wait_sha256",
    }
    assert res["original_wait_sha256"] == hashlib.sha256(wait_p.read_bytes()).hexdigest()
    assert res["claim"]["resume_request_id"] == req_id
    assert res["submission_intent"]["request_id"] == req_id
    assert res["request"]["request_id"] == req_id

    assert claim_p.is_file() and stat.S_IMODE(claim_p.stat().st_mode) == 0o600
    assert json.loads(claim_p.read_text(encoding="utf-8")) == res["claim"]

    assert snap_p.is_file() and stat.S_IMODE(snap_p.stat().st_mode) == 0o600 and snap_p.stat().st_nlink == 1
    snap_data = json.loads(snap_p.read_text(encoding="utf-8"))
    assert snap_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert snap_data["resume_request_id"] == req_id
    assert snap_data["original_wait_sha256"] == res["original_wait_sha256"]
    assert snap_data["claim_sha256"] == hashlib.sha256(claim_p.read_bytes()).hexdigest()
    assert snap_data["expected_request"] == res["request"]

    assert intent_p.is_file() and stat.S_IMODE(intent_p.stat().st_mode) == 0o600
    assert json.loads(intent_p.read_text(encoding="utf-8")) == res["submission_intent"]

    assert queue_p.is_file()
    st = queue_p.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1
    assert json.loads(queue_p.read_text(encoding="utf-8")) == res["request"]


def test_submit_known_reset_resume_duplicate(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, _ = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    lock_path = sc["autopilot_dir"] / "supervisor.lock"
    with exclusive_supervisor_lock(lock_path):
        _call_submit(env)

    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    claim_bytes = claim_p.read_bytes()
    intent_bytes = intent_p.read_bytes()
    queue_bytes = queue_p.read_bytes()
    snap_bytes = snap_p.read_bytes()
    queue_ino = queue_p.stat().st_ino
    snap_ino = snap_p.stat().st_ino
    initial_queued = sorted(requests_dir.iterdir())

    with exclusive_supervisor_lock(lock_path):
        with pytest.raises(ClaimRequestError):
            _call_submit(env)

    assert claim_p.read_bytes() == claim_bytes
    assert intent_p.read_bytes() == intent_bytes
    assert queue_p.read_bytes() == queue_bytes
    assert queue_p.stat().st_ino == queue_ino
    assert snap_p.read_bytes() == snap_bytes
    assert snap_p.stat().st_ino == snap_ino
    assert sorted(requests_dir.iterdir()) == initial_queued
    assert len(initial_queued) == 1


def test_submit_known_reset_resume_broker_not_ready(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, _ = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=False, updated_at=env["now_epoch"]
    )
    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    wait_p = sc["runs_dir"] / "quota-wait.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
        with pytest.raises(broker.BrokerError, match="broker status is not ready idle"):
            _call_submit(env)

    assert claim_p.is_file() and stat.S_IMODE(claim_p.stat().st_mode) == 0o600
    claim_data = json.loads(claim_p.read_text(encoding="utf-8"))
    assert claim_data["resume_request_id"] == req_id
    assert claim_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert claim_data["continuation_controller_run_id"] == sc["continuation_ctrl_id"]

    assert snap_p.is_file()
    st_s = snap_p.stat()
    assert stat.S_IMODE(st_s.st_mode) == 0o600 and st_s.st_nlink == 1
    snap_data = json.loads(snap_p.read_text(encoding="utf-8"))
    assert snap_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert snap_data["resume_request_id"] == req_id
    assert snap_data["original_wait_sha256"] == hashlib.sha256(wait_p.read_bytes()).hexdigest()
    assert snap_data["claim_sha256"] == hashlib.sha256(claim_p.read_bytes()).hexdigest()

    assert not intent_p.exists()
    assert not queue_p.exists()
    assert list(requests_dir.iterdir()) == []


@pytest.mark.parametrize("crash_boundary", ["prequeue", "postqueue_fsync"])
def test_submit_known_reset_resume_direct_bridge_faults(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    crash_boundary: str,
) -> None:
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, _ = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    wait_p = sc["runs_dir"] / "quota-wait.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    lock_path = sc["autopilot_dir"] / "supervisor.lock"

    target = (
        "_atomic_new_json"
        if crash_boundary == "prequeue"
        else "fsync_private_record_directory"
    )
    with exclusive_supervisor_lock(lock_path):
        with patch.object(broker, target, side_effect=OSError(f"fault at {crash_boundary}")):
            with pytest.raises(OSError, match=f"fault at {crash_boundary}"):
                _call_submit(env)

    assert claim_p.is_file()
    st_c = claim_p.stat()
    assert stat.S_IMODE(st_c.st_mode) == 0o600 and st_c.st_nlink == 1
    claim_data = json.loads(claim_p.read_text(encoding="utf-8"))
    assert claim_data["resume_request_id"] == req_id
    assert claim_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert claim_data["continuation_controller_run_id"] == sc["continuation_ctrl_id"]

    assert snap_p.is_file()
    st_s = snap_p.stat()
    assert stat.S_IMODE(st_s.st_mode) == 0o600 and st_s.st_nlink == 1
    snap_data = json.loads(snap_p.read_text(encoding="utf-8"))
    assert snap_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert snap_data["resume_request_id"] == req_id
    assert snap_data["original_wait_sha256"] == hashlib.sha256(wait_p.read_bytes()).hexdigest()
    assert snap_data["claim_sha256"] == hashlib.sha256(claim_p.read_bytes()).hexdigest()
    snap_bytes, snap_ino = snap_p.read_bytes(), st_s.st_ino

    assert intent_p.is_file()
    st_i = intent_p.stat()
    assert stat.S_IMODE(st_i.st_mode) == 0o600 and st_i.st_nlink == 1
    intent_data = json.loads(intent_p.read_text(encoding="utf-8"))
    assert intent_data["request_id"] == req_id

    claim_bytes, claim_ino = claim_p.read_bytes(), st_c.st_ino
    intent_bytes, intent_ino = intent_p.read_bytes(), st_i.st_ino

    if crash_boundary == "prequeue":
        assert not queue_p.exists()
        assert list(requests_dir.iterdir()) == []
        queue_bytes = queue_ino = None
    else:
        assert queue_p.is_file()
        st_q = queue_p.stat()
        assert stat.S_IMODE(st_q.st_mode) == 0o600 and st_q.st_nlink == 1
        queue_data = json.loads(queue_p.read_text(encoding="utf-8"))
        assert queue_data["request_id"] == req_id
        queue_bytes, queue_ino = queue_p.read_bytes(), st_q.st_ino
        assert [p.name for p in requests_dir.iterdir()] == [f"{req_id}.json"]

    with exclusive_supervisor_lock(lock_path):
        with pytest.raises(ClaimRequestError):
            _call_submit(env)

    assert claim_p.read_bytes() == claim_bytes
    assert claim_p.stat().st_ino == claim_ino
    assert snap_p.read_bytes() == snap_bytes
    assert snap_p.stat().st_ino == snap_ino
    assert intent_p.read_bytes() == intent_bytes
    assert intent_p.stat().st_ino == intent_ino
    if crash_boundary == "prequeue":
        assert not queue_p.exists()
        assert list(requests_dir.iterdir()) == []
    else:
        assert queue_p.read_bytes() == queue_bytes
        assert queue_p.stat().st_ino == queue_ino
        assert [p.name for p in requests_dir.iterdir()] == [f"{req_id}.json"]


def test_submit_known_reset_resume_snapshot_failure_prevents_enqueue(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, _ = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    lock_path = sc["autopilot_dir"] / "supervisor.lock"

    enqueue_called = False

    def _forbidden_enqueue(*args: Any, **kwargs: Any) -> Any:
        nonlocal enqueue_called
        enqueue_called = True
        raise AssertionError("enqueue must not be called when snapshot publication fails")

    with patch.object(
        submit_mod,
        "publish_expected_lineage_snapshot",
        side_effect=SnapshotStorageError("simulated snapshot publication failure"),
    ), patch.object(
        submit_mod,
        "enqueue_quota_continuation_locked",
        side_effect=_forbidden_enqueue,
    ):
        with exclusive_supervisor_lock(lock_path):
            with pytest.raises(SnapshotStorageError, match="simulated snapshot publication failure"):
                _call_submit(env)

    assert not enqueue_called
    assert claim_p.is_file()
    assert not snap_p.exists()
    assert not intent_p.exists()
    assert not queue_p.exists()
    assert list(requests_dir.iterdir()) == []
