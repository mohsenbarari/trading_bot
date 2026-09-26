"""Hermetic boundary tests for quota continuation common broker request validation.

This suite proves that a valid quota continuation request built from real Broker
fixture facts passes real, unpatched common request validation (branch, HEAD, clean Git
worktree, task SHA, task schema, assignment binding) and reaches the first
pending-intent read.

Boundary scope note:
This verifies only the observational quota, no replay authority boundary. It does NOT
test or assert joined W2 success, Provider execution, Supervisor coordination, live hooks,
wait/claim publication, replay authority, or C5d completion.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from unittest import mock
import uuid

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_provider_broker import BrokerError
from scripts.two_site_quota_continuation_admission import (
    ContinuationAdmissionError,
    validate_quota_continuation_request,
)
from tests.test_two_site_provider_broker import broker_repo, make_valid_request


def _build_continuation_request(
    root: Path,
    task: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    base_request = make_valid_request(root, task, environment)
    task_payload = json.loads(task.read_text(encoding="utf-8"))
    task_id = task_payload["task_id"]

    return {
        # Shared 11 keys from valid ordinary broker request
        "schema_version": base_request["schema_version"],
        "request_id": uuid.uuid4().hex,
        "task_path": base_request["task_path"],
        "task_sha256": base_request["task_sha256"],
        "run_prefix": base_request["run_prefix"],
        "timeout": base_request["timeout"],
        "source_head": base_request["source_head"],
        "assignment_id": base_request["assignment_id"],
        "assignment_sha256": base_request["assignment_sha256"],
        "controller_run_id": "cycle-0002-20990101T000000Z-456",
        "created_at": base_request["created_at"],
        # 9 continuation-specific keys
        "request_type": "QUOTA_CONTINUATION",
        "original_request_id": base_request["request_id"],
        "original_controller_run_id": base_request["controller_run_id"],
        "claim_sha256": "c" * 64,
        "original_wait_sha256": "d" * 64,
        "semantic_task_fingerprint": "e" * 64,
        "task_id": task_id,
        "role": "worker",
        "claimed_at_utc": "2099-01-01T00:00:00Z",
    }


def test_quota_continuation_common_validation_boundary_positive(broker_repo):
    """A valid continuation request passes real common validation and reaches pending intent read."""
    root, task, scope, environment = broker_repo
    continuation_req = _build_continuation_request(root, task, environment)
    sentinel = RuntimeError("sentinel pending intent reader failure")

    with mock.patch(
        "scripts.two_site_quota_continuation_admission.read_pending_rescope_intent",
        side_effect=sentinel,
    ) as mock_read_intent:
        with pytest.raises(ContinuationAdmissionError) as exc_info:
            validate_quota_continuation_request(
                root,
                continuation_req,
                scope_loader=lambda _: scope,
                now_epoch=time.time(),
            )

    assert exc_info.value.__cause__ is sentinel
    assert "failed to read pending rescope intent:" in str(exc_info.value)
    mock_read_intent.assert_called_once_with(root.resolve())


def test_quota_continuation_common_validation_rejects_task_sha_drift(broker_repo):
    """Task SHA drift is rejected at common validation before pending intent reader."""
    root, task, scope, environment = broker_repo
    continuation_req = _build_continuation_request(root, task, environment)
    original_task_bytes = task.read_bytes()

    bad_request = dict(continuation_req)
    bad_request["task_sha256"] = "0" * 64 if continuation_req["task_sha256"] != "0" * 64 else "1" * 64

    sentinel = RuntimeError("unexpected call to pending intent reader")
    with mock.patch(
        "scripts.two_site_quota_continuation_admission.read_pending_rescope_intent",
        side_effect=sentinel,
    ) as mock_read_intent:
        with pytest.raises(ContinuationAdmissionError) as exc_info:
            validate_quota_continuation_request(
                root,
                bad_request,
                scope_loader=lambda _: scope,
                now_epoch=time.time(),
            )

    assert "initial common request validation failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, BrokerError)
    assert "broker task SHA drift" in str(exc_info.value.__cause__)
    mock_read_intent.assert_not_called()

    # Verify task bytes on disk and Git HEAD remain unchanged
    assert task.read_bytes() == original_task_bytes
    git_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert git_head == continuation_req["source_head"]


def test_quota_continuation_common_validation_rejects_assignment_mismatch(broker_repo):
    """Assignment binding mismatch is rejected at common validation before pending intent reader."""
    root, task, scope, environment = broker_repo
    continuation_req = _build_continuation_request(root, task, environment)

    bad_request = dict(continuation_req)
    bad_request["assignment_sha256"] = "b" * 64

    sentinel = RuntimeError("unexpected call to pending intent reader")
    with mock.patch(
        "scripts.two_site_quota_continuation_admission.read_pending_rescope_intent",
        side_effect=sentinel,
    ) as mock_read_intent:
        with pytest.raises(ContinuationAdmissionError) as exc_info:
            validate_quota_continuation_request(
                root,
                bad_request,
                scope_loader=lambda _: scope,
                now_epoch=time.time(),
            )

    assert "initial common request validation failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, BrokerError)
    assert "broker assignment binding mismatch" in str(exc_info.value.__cause__)
    mock_read_intent.assert_not_called()
