"""Hermetic tests for quota continuation broker client."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import time
from typing import Any
from unittest.mock import patch

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_provider_broker as broker
from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_broker_client import enqueue_quota_continuation_locked
from scripts.two_site_quota_resume_submission_intent import (
    SubmissionIntentError,
    read_quota_resume_submission_intent,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


def test_enqueue_quota_continuation_locked_ordering_and_lock_held(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root = broker_repo[0]
    _, status_path, _, _, _ = broker.broker_paths(root)
    broker._write_status(status_path, "IDLE", quota_continuation_ready=True)

    lock_active = False
    events: list[tuple[str, bool]] = []
    orig_lock = broker.exclusive_broker_lock

    @contextmanager
    def instrumented_lock(path: Path):
        nonlocal lock_active
        with orig_lock(path) as val:
            lock_active = True
            events.append(("lock_enter", True))
            try:
                yield val
            finally:
                lock_active = False
                events.append(("lock_exit", False))

    orig_read_status = broker._read_broker_status

    def spy_read_status(r: Path, sp: Path) -> dict:
        events.append(("read", lock_active))
        return orig_read_status(r, sp)

    def stub_publisher(r: Path, req: dict, *, now_epoch: float | None = None) -> dict:
        events.append(("publisher", lock_active))
        return {"request_id": "test-req-001"}

    with (
        patch.object(broker, "exclusive_broker_lock", instrumented_lock),
        patch.object(broker, "_read_broker_status", spy_read_status),
        patch(
            "scripts.two_site_quota_broker_client.publish_quota_resume_submission_intent",
            stub_publisher,
        ),
        patch.object(
            broker,
            "_atomic_new_json",
            lambda target, payload, max_bytes: events.append(("atomic", lock_active)),
        ),
        patch.object(
            broker,
            "fsync_private_record_directory",
            lambda d: events.append(("fsync", lock_active)),
        ),
    ):
        intent = enqueue_quota_continuation_locked(root, {"request_id": "test-req-001"})

    assert intent == {"request_id": "test-req-001"}
    assert events == [
        ("lock_enter", True),
        ("read", True),
        ("publisher", True),
        ("atomic", True),
        ("fsync", True),
        ("lock_exit", False),
    ]
    assert all(active for _, active in events[1:5])


def test_invalid_or_stale_status_rejected_before_publisher_or_queue(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root = broker_repo[0]
    _, status_path, requests, _, _ = broker.broker_paths(root)

    with patch(
        "scripts.two_site_quota_broker_client.publish_quota_resume_submission_intent"
    ) as mock_pub:
        # Missing, false, or nonbool quota_continuation_ready
        bad_ready: list[dict[str, Any]] = [{}, {"quota_continuation_ready": False}]
        bad_ready.extend({"quota_continuation_ready": v} for v in ("true", 1, None))
        for bad_kw in bad_ready:
            broker._write_status(status_path, "IDLE", **bad_kw)
            with pytest.raises(broker.BrokerError, match="broker status is not ready idle"):
                enqueue_quota_continuation_locked(root, {})

        # Forged old aliases status/readiness
        for aliases in (
            {"readiness": True, "status": "READY"},
            {"readiness": True, "status": "IDLE", "quota_continuation_ready": False},
        ):
            broker._write_status(status_path, "IDLE", **aliases)
            with pytest.raises(broker.BrokerError, match="broker status is not ready idle"):
                enqueue_quota_continuation_locked(root, {})

        # Stale and future timestamp
        for delta in (-(broker.STATUS_MAX_AGE_SECONDS + 10.0), 60.0):
            broker._write_status(
                status_path, "IDLE", quota_continuation_ready=True, updated_at=time.time() + delta
            )
            with pytest.raises(broker.BrokerError, match="broker status is not fresh"):
                enqueue_quota_continuation_locked(root, {})

        # Nonbool / non-numeric timestamp
        for bad_ts in (True, "yesterday", None):
            broker._write_status(
                status_path, "IDLE", quota_continuation_ready=True, updated_at=bad_ts
            )
            with pytest.raises(
                broker.BrokerError, match="broker status updated_at must be finite numeric nonbool"
            ):
                enqueue_quota_continuation_locked(root, {})

        # Invalid pid
        for bad_pid in (True, "1234", None, 1.5):
            broker._write_status(
                status_path, "IDLE", quota_continuation_ready=True, pid=bad_pid
            )
            with pytest.raises(broker.BrokerError, match="broker status is not ready idle"):
                enqueue_quota_continuation_locked(root, {})

        mock_pub.assert_not_called()
        assert list(requests.iterdir()) == []


def test_full_admission_produces_private_queue_and_immutable_intent(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root, sc, now_epoch = ctx["root"], ctx["sc"], ctx["now_epoch"]
    resume_request, controller_run_id = ctx["resume_request"], ctx["controller_run_id"]

    _, status_path, requests, _, _ = broker.broker_paths(root)
    with patch("time.time", return_value=now_epoch):
        broker._write_status(status_path, "IDLE", quota_continuation_ready=True)

    with exclusive_supervisor_lock(sc["lock_path"]):
        intent = enqueue_quota_continuation_locked(root, resume_request, now_epoch=now_epoch)

    queued_file = requests / f"{intent['request_id']}.json"
    assert queued_file.is_file()
    st = queued_file.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1
    assert json.loads(queued_file.read_bytes()) == resume_request

    readback = read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    assert readback == intent

    orig_inode, orig_bytes = st.st_ino, queued_file.read_bytes()
    with exclusive_supervisor_lock(sc["lock_path"]):
        with pytest.raises(SubmissionIntentError, match="already exists"):
            enqueue_quota_continuation_locked(root, resume_request, now_epoch=now_epoch)

    assert queued_file.stat().st_ino == orig_inode
    assert queued_file.read_bytes() == orig_bytes


def test_enqueue_quota_continuation_locked_stop_recheck_ordering(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root = broker_repo[0]
    _, status_path, _, _, _ = broker.broker_paths(root)
    broker._write_status(status_path, "IDLE", quota_continuation_ready=True)

    lock_active = False
    events: list[tuple[str, bool]] = []
    orig_lock = broker.exclusive_broker_lock

    @contextmanager
    def instrumented_lock(path: Path):
        nonlocal lock_active
        with orig_lock(path) as val:
            lock_active = True
            events.append(("lock_enter", True))
            try:
                yield val
            finally:
                lock_active = False
                events.append(("lock_exit", False))

    orig_read_status = broker._read_broker_status

    def spy_read_status(r: Path, sp: Path) -> dict:
        events.append(("read", lock_active))
        return orig_read_status(r, sp)

    from scripts import two_site_quota_broker_client as client_mod
    orig_check_stop = client_mod._check_pre_dispatch_stop

    def spy_check_stop(r: Path) -> None:
        events.append(("check_stop", lock_active))
        return orig_check_stop(r)

    def stub_publisher(r: Path, req: dict, *, now_epoch: float | None = None) -> dict:
        events.append(("publisher", lock_active))
        return {"request_id": "test-req-001"}

    with (
        patch.object(broker, "exclusive_broker_lock", instrumented_lock),
        patch.object(broker, "_read_broker_status", spy_read_status),
        patch.object(client_mod, "_check_pre_dispatch_stop", spy_check_stop),
        patch(
            "scripts.two_site_quota_broker_client.publish_quota_resume_submission_intent",
            stub_publisher,
        ),
        patch.object(
            broker,
            "_atomic_new_json",
            lambda target, payload, max_bytes: events.append(("atomic", lock_active)),
        ),
        patch.object(
            broker,
            "fsync_private_record_directory",
            lambda d: events.append(("fsync", lock_active)),
        ),
    ):
        intent = enqueue_quota_continuation_locked(root, {"request_id": "test-req-001"})

    assert intent == {"request_id": "test-req-001"}
    assert events == [
        ("lock_enter", True),
        ("read", True),
        ("check_stop", True),
        ("publisher", True),
        ("atomic", True),
        ("fsync", True),
        ("lock_exit", False),
    ]
    assert all(active for _, active in events[1:6])


@pytest.mark.parametrize(
    "stop_kind",
    ["regular_file", "broken_symlink", "broker_stop_true", "broker_stop_malformed"],
)
def test_enqueue_quota_continuation_locked_rejects_stop_conditions(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    stop_kind: str,
) -> None:
    root = broker_repo[0]
    _, status_path, requests, _, _ = broker.broker_paths(root)
    broker._write_status(status_path, "IDLE", quota_continuation_ready=True)

    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    stop_path.parent.mkdir(parents=True, exist_ok=True)

    patcher = None
    if stop_kind == "regular_file":
        stop_path.write_text("STOP\n", encoding="utf-8")
    elif stop_kind == "broken_symlink":
        stop_path.symlink_to(root / "nonexistent-target")
    elif stop_kind == "broker_stop_true":
        patcher = patch.object(broker, "_STOP", True)
        patcher.start()
    elif stop_kind == "broker_stop_malformed":
        patcher = patch.object(broker, "_STOP", 12345)
        patcher.start()

    publisher_called = False

    def stub_publisher(r: Path, req: dict, *, now_epoch: float | None = None) -> dict:
        nonlocal publisher_called
        publisher_called = True
        return {"request_id": "test-req-stop"}

    try:
        with patch(
            "scripts.two_site_quota_broker_client.publish_quota_resume_submission_intent",
            stub_publisher,
        ):
            with pytest.raises(
                broker.BrokerError, match="^stop requested before continuation dispatch$"
            ):
                enqueue_quota_continuation_locked(root, {"request_id": "test-req-stop"})
    finally:
        if patcher is not None:
            patcher.stop()
        if os.path.lexists(stop_path):
            stop_path.unlink()

    assert not publisher_called
    assert list(requests.iterdir()) == []
