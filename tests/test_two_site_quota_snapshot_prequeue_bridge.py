"""Hermetic fault and crash-boundary integration tests for quota snapshot prequeue bridge."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
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
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_supervisor_resume_submit import (
    _call_submit,
    _setup_admitted_env,
)


def test_fault_snapshot_publisher_failure_aborts_before_enqueue(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Prove that publisher failure after claim publication never calls enqueue and leaves claim durable."""
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, receipts_dir = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    status_bytes_before = status_path.read_bytes()
    receipts_before = sorted(p.name for p in receipts_dir.iterdir())

    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    wait_p = sc["runs_dir"] / "quota-wait.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    wait_bytes_before = wait_p.read_bytes()
    wait_mtime_before = wait_p.stat().st_mtime_ns

    enqueue_calls: list[dict[str, Any]] = []

    def _spy_enqueue(*args: Any, **kwargs: Any) -> Any:
        enqueue_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("enqueue must never be called when snapshot publisher fails")

    with patch.object(
        submit_mod,
        "publish_expected_lineage_snapshot",
        side_effect=SnapshotStorageError("injected publisher failure"),
    ), patch.object(
        submit_mod,
        "enqueue_quota_continuation_locked",
        side_effect=_spy_enqueue,
    ):
        with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
            with pytest.raises(SnapshotStorageError, match="injected publisher failure"):
                _call_submit(env)

    assert len(enqueue_calls) == 0

    assert claim_p.is_file()
    st_c = claim_p.stat()
    assert stat.S_IMODE(st_c.st_mode) == 0o600
    assert st_c.st_nlink == 1
    claim_data = json.loads(claim_p.read_text(encoding="utf-8"))
    assert claim_data["resume_request_id"] == req_id
    assert claim_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert claim_data["continuation_controller_run_id"] == sc["continuation_ctrl_id"]

    assert not snap_p.exists()
    assert not intent_p.exists()
    assert not queue_p.exists()
    assert list(requests_dir.iterdir()) == []

    assert wait_p.read_bytes() == wait_bytes_before
    assert wait_p.stat().st_mtime_ns == wait_mtime_before
    assert status_path.read_bytes() == status_bytes_before
    assert sorted(p.name for p in receipts_dir.iterdir()) == receipts_before


def test_fault_preexisting_snapshot_no_overwrite_collision(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Prove real publisher detects pre-existing snapshot file, fails closed, and leaves files untouched."""
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, receipts_dir = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    status_bytes_before = status_path.read_bytes()
    receipts_before = sorted(p.name for p in receipts_dir.iterdir())

    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    wait_p = sc["runs_dir"] / "quota-wait.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    preexisting_bytes = b'{"preexisting_marker": true, "payload": "collision-sentinel"}'
    snap_p.write_bytes(preexisting_bytes)
    os.chmod(snap_p, 0o600)
    snap_st_before = snap_p.stat()
    snap_ino_before = snap_st_before.st_ino
    snap_mtime_before = snap_st_before.st_mtime_ns

    wait_bytes_before = wait_p.read_bytes()
    wait_mtime_before = wait_p.stat().st_mtime_ns

    enqueue_calls: list[dict[str, Any]] = []

    def _spy_enqueue(*args: Any, **kwargs: Any) -> Any:
        enqueue_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("enqueue must never be called on pre-existing snapshot collision")

    with patch.object(
        submit_mod,
        "enqueue_quota_continuation_locked",
        side_effect=_spy_enqueue,
    ):
        with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
            with pytest.raises(
                SnapshotStorageError,
                match=f"{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME} already exists",
            ):
                _call_submit(env)

    assert len(enqueue_calls) == 0

    assert snap_p.is_file()
    assert snap_p.read_bytes() == preexisting_bytes
    snap_st_after = snap_p.stat()
    assert snap_st_after.st_ino == snap_ino_before
    assert snap_st_after.st_mtime_ns == snap_mtime_before
    assert stat.S_IMODE(snap_st_after.st_mode) == 0o600

    assert claim_p.is_file()
    st_c = claim_p.stat()
    assert stat.S_IMODE(st_c.st_mode) == 0o600
    assert st_c.st_nlink == 1
    claim_data = json.loads(claim_p.read_text(encoding="utf-8"))
    assert claim_data["resume_request_id"] == req_id

    assert not intent_p.exists()
    assert not queue_p.exists()
    assert list(requests_dir.iterdir()) == []

    assert wait_p.read_bytes() == wait_bytes_before
    assert wait_p.stat().st_mtime_ns == wait_mtime_before
    assert status_path.read_bytes() == status_bytes_before
    assert sorted(p.name for p in receipts_dir.iterdir()) == receipts_before


def test_fault_post_publication_pre_enqueue_fault_retains_snapshot(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Prove that fault after real publication but before enqueue retains canonical snapshot and leaves queue empty."""
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, receipts_dir = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    status_bytes_before = status_path.read_bytes()
    receipts_before = sorted(p.name for p in receipts_dir.iterdir())

    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    wait_p = sc["runs_dir"] / "quota-wait.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    wait_bytes_before = wait_p.read_bytes()
    wait_mtime_before = wait_p.stat().st_mtime_ns

    real_publish = submit_mod.publish_expected_lineage_snapshot
    publish_calls: list[dict[str, Any]] = []

    def _faulty_publisher_wrapper(*args: Any, **kwargs: Any) -> Any:
        res = real_publish(*args, **kwargs)
        publish_calls.append({"result": res})
        raise SnapshotStorageError("simulated crash after real publication before enqueue")

    enqueue_calls: list[dict[str, Any]] = []

    def _spy_enqueue(*args: Any, **kwargs: Any) -> Any:
        enqueue_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("enqueue must not be reached when publisher raises post-publication fault")

    with patch.object(
        submit_mod,
        "publish_expected_lineage_snapshot",
        side_effect=_faulty_publisher_wrapper,
    ), patch.object(
        submit_mod,
        "enqueue_quota_continuation_locked",
        side_effect=_spy_enqueue,
    ):
        with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
            with pytest.raises(
                SnapshotStorageError,
                match="simulated crash after real publication before enqueue",
            ):
                _call_submit(env)

    assert len(publish_calls) == 1
    assert len(enqueue_calls) == 0

    assert snap_p.is_file()
    st_s = snap_p.stat()
    assert stat.S_IMODE(st_s.st_mode) == 0o600
    assert st_s.st_nlink == 1

    snap_data = json.loads(snap_p.read_text(encoding="utf-8"))
    assert snap_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert snap_data["resume_request_id"] == req_id
    assert snap_data["original_wait_sha256"] == hashlib.sha256(wait_p.read_bytes()).hexdigest()
    assert snap_data["claim_sha256"] == hashlib.sha256(claim_p.read_bytes()).hexdigest()

    assert claim_p.is_file()
    st_c = claim_p.stat()
    assert stat.S_IMODE(st_c.st_mode) == 0o600
    assert st_c.st_nlink == 1

    assert not intent_p.exists()
    assert not queue_p.exists()
    assert list(requests_dir.iterdir()) == []

    assert wait_p.read_bytes() == wait_bytes_before
    assert wait_p.stat().st_mtime_ns == wait_mtime_before
    assert status_path.read_bytes() == status_bytes_before
    assert sorted(p.name for p in receipts_dir.iterdir()) == receipts_before


def test_fault_enqueue_failure_preserves_snapshot_and_blocks_replay(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Prove that enqueue fault leaves snapshot and claim durable, and subsequent attempt blocks replay."""
    env = _setup_admitted_env(broker_repo)
    sc, req_id = env["sc"], env["req_id"]
    _, status_path, requests_dir, _, receipts_dir = broker.broker_paths(sc["root"])
    broker._write_status(
        status_path, "IDLE", quota_continuation_ready=True, updated_at=env["now_epoch"]
    )
    status_bytes_before = status_path.read_bytes()
    receipts_before = sorted(p.name for p in receipts_dir.iterdir())

    claim_p = sc["runs_dir"] / "quota-resume-claim.json"
    intent_p = sc["runs_dir"] / "quota-resume-submission.json"
    queue_p = requests_dir / f"{req_id}.json"
    wait_p = sc["runs_dir"] / "quota-wait.json"
    snap_p = sc["runs_dir"] / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    wait_bytes_before = wait_p.read_bytes()
    wait_mtime_before = wait_p.stat().st_mtime_ns
    lock_path = sc["autopilot_dir"] / "supervisor.lock"

    enqueue_invocations: list[dict[str, Any]] = []

    def _failing_enqueue(root: Path, request: dict[str, Any], *, now_epoch: float) -> dict[str, Any]:
        enqueue_invocations.append({"request": copy.deepcopy(request), "now_epoch": now_epoch})
        raise broker.BrokerError("simulated enqueue broker transport failure")

    with patch.object(
        submit_mod,
        "enqueue_quota_continuation_locked",
        side_effect=_failing_enqueue,
    ):
        with exclusive_supervisor_lock(lock_path):
            with pytest.raises(broker.BrokerError, match="simulated enqueue broker transport failure"):
                _call_submit(env)

    assert len(enqueue_invocations) == 1

    assert claim_p.is_file()
    st_c = claim_p.stat()
    assert stat.S_IMODE(st_c.st_mode) == 0o600
    assert st_c.st_nlink == 1

    assert snap_p.is_file()
    st_s = snap_p.stat()
    assert stat.S_IMODE(st_s.st_mode) == 0o600
    assert st_s.st_nlink == 1
    snap_data = json.loads(snap_p.read_text(encoding="utf-8"))
    assert snap_data["original_controller_run_id"] == sc["original_ctrl_id"]
    assert snap_data["resume_request_id"] == req_id
    assert snap_data["original_wait_sha256"] == hashlib.sha256(wait_p.read_bytes()).hexdigest()
    assert snap_data["claim_sha256"] == hashlib.sha256(claim_p.read_bytes()).hexdigest()

    assert not intent_p.exists()
    assert not queue_p.exists()
    assert list(requests_dir.iterdir()) == []

    claim_bytes = claim_p.read_bytes()
    claim_ino = st_c.st_ino
    claim_mtime_ns = st_c.st_mtime_ns

    snap_bytes = snap_p.read_bytes()
    snap_ino = st_s.st_ino
    snap_mtime_ns = st_s.st_mtime_ns

    # Attempt 2 with identical parameters: rejected at claim stage
    with patch.object(
        submit_mod,
        "enqueue_quota_continuation_locked",
        side_effect=_failing_enqueue,
    ):
        with exclusive_supervisor_lock(lock_path):
            with pytest.raises(ClaimRequestError):
                _call_submit(env)

    # Attempt 3 with new request_id for same cycle: still rejected at claim stage
    env_retry = copy.deepcopy(env)
    env_retry["req_id"] = hashlib.sha256(b"two_site_resume_req_id_002_replay").hexdigest()[:32]
    with patch.object(
        submit_mod,
        "enqueue_quota_continuation_locked",
        side_effect=_failing_enqueue,
    ):
        with exclusive_supervisor_lock(lock_path):
            with pytest.raises(ClaimRequestError):
                _call_submit(env_retry)

    # Exact boundary observed: no subsequent enqueue call
    assert len(enqueue_invocations) == 1

    assert claim_p.read_bytes() == claim_bytes
    assert claim_p.stat().st_ino == claim_ino
    assert claim_p.stat().st_mtime_ns == claim_mtime_ns

    assert snap_p.read_bytes() == snap_bytes
    assert snap_p.stat().st_ino == snap_ino
    assert snap_p.stat().st_mtime_ns == snap_mtime_ns

    assert not intent_p.exists()
    assert not queue_p.exists()
    assert list(requests_dir.iterdir()) == []

    assert wait_p.read_bytes() == wait_bytes_before
    assert wait_p.stat().st_mtime_ns == wait_mtime_before
    assert status_path.read_bytes() == status_bytes_before
    assert sorted(p.name for p in receipts_dir.iterdir()) == receipts_before
