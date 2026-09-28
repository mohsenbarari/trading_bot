"""Tests for two-site quota broker route adapter.

observational quota, no replay authority
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_provider_broker import validate_response
from scripts.two_site_quota_broker_route import execute_intent_bound_continuation
from scripts.two_site_quota_continuation_admission import ContinuationAdmissionError
from scripts.two_site_quota_resume_submission_intent import (
    QUOTA_RESUME_SUBMISSION_FILENAME,
    SubmissionIntentError,
    publish_quota_resume_submission_intent,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)

# observational quota, no replay authority


def test_invalid_request_rejects_before_intent_or_dispatch(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root = ctx["root"]

    dispatcher_spy = MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.dispatch_quota_continuation_once",
        dispatcher_spy,
    )

    with pytest.raises(ContinuationAdmissionError):
        execute_intent_bound_continuation(root, {"invalid": "request"})

    dispatcher_spy.assert_not_called()


def test_quota_broker_route_same_root_lifecycle(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root = ctx["root"]
    resume_request = ctx["resume_request"]
    runs_dir = ctx["runs_dir"]
    stub_result = {
        "state": "READY_FOR_CODEX",
        "summary": "Hermetic dispatcher test pass",
    }

    dispatcher_spy = MagicMock(return_value=stub_result)
    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.dispatch_quota_continuation_once",
        dispatcher_spy,
    )

    # 1. Missing intent rejects before monkeypatched dispatcher spy
    with pytest.raises(SubmissionIntentError):
        execute_intent_bound_continuation(root, resume_request)
    dispatcher_spy.assert_not_called()

    # 2. Publish real intent under existing exclusive_supervisor_lock
    with exclusive_supervisor_lock(ctx["sc"]["lock_path"]):
        published = publish_quota_resume_submission_intent(
            root,
            resume_request,
            now_epoch=ctx["now_epoch"],
        )
    assert published["request_id"] == resume_request["request_id"]

    # Response path succeeds with only dispatcher stub returning READY_FOR_CODEX
    response = execute_intent_bound_continuation(root, resume_request)

    dispatcher_spy.assert_called_once()
    call_args = dispatcher_spy.call_args
    assert call_args[0][0] == root
    assert call_args[0][1]["request_id"] == resume_request["request_id"]
    assert call_args[0][1]["request_type"] == "QUOTA_CONTINUATION"

    # Exact 9-key broker response fields validated with validate_response
    assert response["schema_version"] == "1.0"
    assert response["request_id"] == resume_request["request_id"]
    assert response["run_prefix"] == resume_request["run_prefix"]
    assert response["source_head"] == resume_request["source_head"]
    assert response["state"] == "COMPLETED"
    assert response["exit_code"] == 0
    assert response["stderr"] == ""
    assert json.loads(response["stdout"]) == stub_result
    assert validate_response(resume_request, response) == response

    # 3. Tamper canonical intent request_sha256 and prove rejection before additional dispatch
    submission_file = runs_dir / QUOTA_RESUME_SUBMISSION_FILENAME
    assert submission_file.is_file()
    payload = json.loads(submission_file.read_text(encoding="utf-8"))
    payload["request_sha256"] = "0" * 64
    canonical_tampered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    submission_file.write_bytes(canonical_tampered)

    with pytest.raises(SubmissionIntentError):
        execute_intent_bound_continuation(root, resume_request)

    assert dispatcher_spy.call_count == 1
