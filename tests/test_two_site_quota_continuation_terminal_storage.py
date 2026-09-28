"""Hermetic tests for two-site quota continuation terminal storage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_continuation_terminal_storage import (
    TERMINAL_DIR_NAME,
    TerminalContinuationStorageError,
    publish_terminal_continuation_record,
    read_terminal_continuation_record,
)
from scripts.two_site_quota_resume_submission_intent import (
    publish_quota_resume_submission_intent,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


@pytest.mark.parametrize(
    ("inner_state", "expected_exit", "pad_len"),
    [
        ("READY_FOR_CODEX", 0, 70_000),
        ("TASK_BLOCKED", 1, 0),
    ],
)
def test_publish_and_read_terminal_continuation_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    inner_state: str,
    expected_exit: int,
    pad_len: int,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root = ctx["root"]
    sc = ctx["sc"]
    resume_request = ctx["resume_request"]
    now_epoch = ctx["now_epoch"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        publish_quota_resume_submission_intent(
            root, resume_request, now_epoch=now_epoch
        )

    result_payload: dict[str, Any] = {"state": inner_state}
    if pad_len > 0:
        result_payload["large_pad"] = "x" * pad_len

    response = build_continuation_broker_response(
        resume_request, result_payload, now_epoch=now_epoch
    )
    assert response["state"] == "COMPLETED"
    assert response["exit_code"] == expected_exit
    inner_payload = json.loads(response["stdout"])
    assert inner_payload["state"] == inner_state
    if pad_len > 0:
        assert len(response["stdout"].encode("utf-8")) > 65_536

    published = publish_terminal_continuation_record(root, resume_request, response)
    canonical_keys = {
        "schema_version",
        "request_id",
        "request",
        "request_sha256",
        "response",
        "response_sha256",
        "submission_intent_sha256",
    }
    assert set(published.keys()) == canonical_keys
    assert published["request_id"] == resume_request["request_id"]
    assert published["response"]["state"] == "COMPLETED"
    assert (
        json.loads(published["response"]["stdout"])["state"]
        == inner_state
    )

    term_file = (
        root
        / ".local"
        / "two-site-refactor"
        / "provider-broker"
        / TERMINAL_DIR_NAME
        / f"{resume_request['request_id']}.json"
    )
    assert term_file.is_file()
    st = term_file.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1
    assert st.st_uid == os.geteuid()
    assert term_file.read_bytes() == json.dumps(
        published,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if pad_len > 0:
        assert st.st_size > 65_536

    readback = read_terminal_continuation_record(
        root, resume_request["request_id"], expected_request=resume_request
    )
    assert readback == published

    readback["schema_version"] = "9.9"
    readback["request"]["timeout"] = -1
    fresh_read = read_terminal_continuation_record(
        root, resume_request["request_id"], expected_request=resume_request
    )
    assert fresh_read["schema_version"] == "1.0"
    assert fresh_read["request"]["timeout"] == resume_request["timeout"]
    assert published["schema_version"] == "1.0"


def test_terminal_continuation_storage_negatives(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root = ctx["root"]
    sc = ctx["sc"]
    resume_request = ctx["resume_request"]
    now_epoch = ctx["now_epoch"]
    term_dir = (
        root / ".local" / "two-site-refactor" / "provider-broker" / TERMINAL_DIR_NAME
    )

    assert not term_dir.exists()
    with pytest.raises(
        TerminalContinuationStorageError,
        match="cannot lstat terminal-continuation",
    ):
        read_terminal_continuation_record(root, resume_request["request_id"])
    assert not term_dir.exists()

    resp = build_continuation_broker_response(
        resume_request, {"state": "READY_FOR_CODEX"}, now_epoch=now_epoch
    )

    with pytest.raises(
        TerminalContinuationStorageError,
        match="cannot read persisted submission intent",
    ):
        publish_terminal_continuation_record(root, resume_request, resp)

    bad_req = dict(resume_request, request_id="not_32_hex")
    with pytest.raises(
        TerminalContinuationStorageError,
        match="request request_id must be 32 lowercase hex",
    ):
        publish_terminal_continuation_record(root, bad_req, resp)
    with pytest.raises(
        TerminalContinuationStorageError,
        match="request_id must be 32 lowercase hex",
    ):
        read_terminal_continuation_record(root, "not_32_hex")

    with exclusive_supervisor_lock(sc["lock_path"]):
        publish_quota_resume_submission_intent(
            root, resume_request, now_epoch=now_epoch
        )

    published = publish_terminal_continuation_record(root, resume_request, resp)
    assert published["request_id"] == resume_request["request_id"]

    term_file = term_dir / f"{resume_request['request_id']}.json"
    first_bytes = term_file.read_bytes()
    first_stat = term_file.stat()

    with pytest.raises(
        TerminalContinuationStorageError, match="terminal record already exists"
    ):
        publish_terminal_continuation_record(root, resume_request, resp)
    assert term_file.read_bytes() == first_bytes
    second_stat = term_file.stat()
    assert (second_stat.st_ino, second_stat.st_size, second_stat.st_mtime_ns) == (
        first_stat.st_ino,
        first_stat.st_size,
        first_stat.st_mtime_ns,
    )

    mismatched_request = dict(
        resume_request, timeout=resume_request["timeout"] + 10
    )
    with pytest.raises(
        TerminalContinuationStorageError,
        match="terminal continuation record validation failed",
    ):
        read_terminal_continuation_record(
            root, resume_request["request_id"], expected_request=mismatched_request
        )
