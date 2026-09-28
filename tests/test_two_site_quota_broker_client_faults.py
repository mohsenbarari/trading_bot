"""Hermetic fault tests for quota continuation broker client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any
from unittest.mock import patch

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_provider_broker as broker
from scripts import two_site_quota_broker_client as client_mod
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


@pytest.mark.parametrize("crash_boundary", ["prequeue", "postqueue_fsync"])
def test_enqueue_quota_continuation_locked_client_faults(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    crash_boundary: str,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root, sc, now_epoch = ctx["root"], ctx["sc"], ctx["now_epoch"]
    resume_request, controller_run_id = ctx["resume_request"], ctx["controller_run_id"]

    _, status_path, requests, _, _ = broker.broker_paths(root)
    with patch("time.time", return_value=now_epoch):
        broker._write_status(status_path, "IDLE", quota_continuation_ready=True)

    queued_file = requests / f"{resume_request['request_id']}.json"
    p_atomic = patch.object(
        broker,
        "_atomic_new_json",
        side_effect=OSError("fault before request queue creation"),
    )
    orig_publish = client_mod.publish_quota_resume_submission_intent

    def publish_then_patch(*a: Any, **kw: Any) -> dict:
        intent = orig_publish(*a, **kw)
        p_atomic.start()
        return intent

    p_pub = patch.object(
        client_mod,
        "publish_quota_resume_submission_intent",
        side_effect=publish_then_patch,
    )
    p_fsync = patch.object(
        broker,
        "fsync_private_record_directory",
        side_effect=OSError("fault at client queue fsync"),
    )

    if crash_boundary == "prequeue":
        p_pub.start()
    else:
        p_fsync.start()

    try:
        with exclusive_supervisor_lock(sc["lock_path"]):
            with pytest.raises(OSError):
                enqueue_quota_continuation_locked(
                    root, resume_request, now_epoch=now_epoch
                )
    finally:
        if crash_boundary == "prequeue":
            p_pub.stop()
            try:
                p_atomic.stop()
            except RuntimeError:
                pass
        else:
            p_fsync.stop()

    intent_files = list(root.glob("**/quota-resume-submission.json"))
    assert len(intent_files) == 1
    intent_file = intent_files[0]
    orig_intent_bytes = intent_file.read_bytes()
    orig_intent_inode = intent_file.stat().st_ino

    read_intent = read_quota_resume_submission_intent(
        root, controller_run_id, resume_request
    )
    assert read_intent["request_id"] == resume_request["request_id"]

    if crash_boundary == "prequeue":
        assert not queued_file.exists()
        orig_queue_inode = None
        orig_queue_bytes = None
    else:
        assert queued_file.is_file()
        st = queued_file.stat()
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert st.st_nlink == 1
        assert json.loads(queued_file.read_bytes()) == resume_request
        orig_queue_inode = st.st_ino
        orig_queue_bytes = queued_file.read_bytes()

    with patch.object(
        broker, "_atomic_new_json", wraps=broker._atomic_new_json
    ) as spy_atomic:
        with exclusive_supervisor_lock(sc["lock_path"]):
            with pytest.raises(SubmissionIntentError, match="already exists"):
                enqueue_quota_continuation_locked(
                    root, resume_request, now_epoch=now_epoch
                )
        spy_atomic.assert_not_called()

    assert intent_file.stat().st_ino == orig_intent_inode
    assert intent_file.read_bytes() == orig_intent_bytes
    if crash_boundary == "prequeue":
        assert not queued_file.exists()
    else:
        assert queued_file.is_file()
        assert queued_file.stat().st_ino == orig_queue_inode
        assert queued_file.read_bytes() == orig_queue_bytes
