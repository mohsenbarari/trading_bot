"""Hermetic unit tests for scripts/two_site_quota_resume_claim_request.py."""

from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any
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

try:
    import scripts.two_site_quota_resume_claim_request as req_mod
    from scripts.two_site_quota_resume_claim_request import (
        ClaimRequestError,
        MAX_WAIT_RECORD_BYTES,
        compose_and_publish_known_reset_resume_request_locked,
    )
    from scripts.two_site_quota_resume_claim_storage import ClaimStorageError
    from tests.test_two_site_quota_resume_claim_admission import (
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
        _step_iso_seconds,
    )
except ImportError:
    import two_site_quota_resume_claim_request as req_mod  # type: ignore
    from two_site_quota_resume_claim_request import (  # type: ignore
        ClaimRequestError,
        MAX_WAIT_RECORD_BYTES,
        compose_and_publish_known_reset_resume_request_locked,
    )
    from two_site_quota_resume_claim_storage import ClaimStorageError  # type: ignore
    from test_two_site_quota_resume_claim_admission import (  # type: ignore
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
        _step_iso_seconds,
    )


def _build_valid_test_context() -> dict[str, Any]:
    wait_record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    raw_wait_bytes = json.dumps(
        wait_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = wait_record["schedule"]["next_check_at_utc"]
    claimed_at_utc = _step_iso_seconds(due_time, 5)
    claim_dt = datetime.fromisoformat(claimed_at_utc.replace("Z", "+00:00"))
    now_epoch = claim_dt.timestamp() + 5.0
    return {
        "root": Path("/tmp/two-site-test-root"),
        "original_controller_run_id": wait_record["controller_run_id"],
        "wait_record": wait_record,
        "expected_wait": expected_wait,
        "raw_wait_bytes": raw_wait_bytes,
        "semantic_attempt": semantic_attempt,
        "expected_task_id": semantic_attempt["task_id"],
        "continuation_controller_run_id": "cycle-2026-20260925T020304Z-456",
        "resume_request_id": "fedcba9876543210fedcba9876543210",
        "claimed_at_utc": claimed_at_utc,
        "timeout": 120,
        "now_epoch": now_epoch,
    }


def test_valid_roundtrip_publisher_called_once_and_detached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _build_valid_test_context()
    record_snap = copy.deepcopy(ctx["wait_record"])
    exp_snap = copy.deepcopy(ctx["expected_wait"])
    sem_snap = copy.deepcopy(ctx["semantic_attempt"])

    publish_calls: list[dict[str, Any]] = []

    def mock_publish(root: Any, original_controller_run_id: Any, claim: Any, expected: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        publish_calls.append({
            "root": root,
            "cid": original_controller_run_id,
            "claim": copy.deepcopy(claim),
            "expected": copy.deepcopy(expected),
        })
        return copy.deepcopy(claim)

    monkeypatch.setattr(req_mod, "publish_known_reset_resume_claim", mock_publish)

    result = compose_and_publish_known_reset_resume_request_locked(**ctx)

    assert len(publish_calls) == 1
    pub = publish_calls[0]
    assert pub["root"] == ctx["root"]
    assert pub["cid"] == ctx["original_controller_run_id"]
    assert pub["claim"]["resume_request_id"] == ctx["resume_request_id"]
    assert pub["expected"]["resume_request_id"] == ctx["resume_request_id"]

    assert set(result.keys()) == {
        "claim",
        "expected_claim",
        "claim_sha256",
        "request",
        "original_wait_sha256",
    }
    assert result["claim"]["state"] == "RESUME_CLAIMED"
    assert result["expected_claim"] == pub["expected"]
    assert result["expected_claim"]["resume_request_id"] == ctx["resume_request_id"]
    assert result["request"]["request_type"] == "QUOTA_CONTINUATION"
    assert result["request"]["claim_sha256"] == result["claim_sha256"]
    assert result["request"]["original_wait_sha256"] == result["original_wait_sha256"]

    assert ctx["wait_record"] == record_snap
    assert ctx["expected_wait"] == exp_snap
    assert ctx["semantic_attempt"] == sem_snap

    result["claim"]["state"] = "MUTATED"
    result["request"]["request_type"] = "MUTATED"
    result["expected_claim"]["resume_request_id"] = "MUTATED"
    assert pub["claim"]["state"] == "RESUME_CLAIMED"
    assert pub["expected"]["resume_request_id"] == ctx["resume_request_id"]


@pytest.mark.parametrize(
    ("mutator", "match_snippet"),
    [
        (lambda c: c.__setitem__("raw_wait_bytes", "not-bytes"), "must be bytes"),
        (lambda c: c.__setitem__("raw_wait_bytes", b""), "outside 1.."),
        (lambda c: c.__setitem__("raw_wait_bytes", b" " * (MAX_WAIT_RECORD_BYTES + 1)), "outside 1.."),
        (lambda c: c.__setitem__("raw_wait_bytes", json.dumps(c["wait_record"], indent=2).encode("utf-8")), "does not match canonical"),
        (lambda c: c.__setitem__("raw_wait_bytes", b"{\"corrupted\":true}"), "does not match canonical"),
        (lambda c: c.__setitem__("original_controller_run_id", "cycle-2026-20260925T999999Z-999"), "does not match original_controller_run_id"),
        (lambda c: c.__setitem__("root", "/not/a/path"), "root must be a Path"),
    ],
)
def test_raw_wait_and_controller_run_id_failures_prevent_publish(
    monkeypatch: pytest.MonkeyPatch,
    mutator: Any,
    match_snippet: str,
) -> None:
    ctx = _build_valid_test_context()
    mutator(ctx)

    publisher_called = False

    def mock_publish(*args: Any, **kwargs: Any) -> Any:
        nonlocal publisher_called
        publisher_called = True
        raise AssertionError("Publisher must not be called on prepublication failure")

    monkeypatch.setattr(req_mod, "publish_known_reset_resume_claim", mock_publish)

    with pytest.raises(ClaimRequestError, match=match_snippet):
        compose_and_publish_known_reset_resume_request_locked(**ctx)

    assert not publisher_called


@pytest.mark.parametrize(
    ("mutator", "match_snippet"),
    [
        (lambda c: c["semantic_attempt"].__setitem__("task_id", "wrong-task"), "does not match expected_task_id"),
        (lambda c: c.__setitem__("continuation_controller_run_id", c["original_controller_run_id"]), "continuation_controller_run_id must differ"),
        (lambda c: c.__setitem__("now_epoch", datetime.fromisoformat(c["claimed_at_utc"].replace("Z", "+00:00")).timestamp() - 1.0), "Clock regression"),
        (lambda c: c.__setitem__("now_epoch", True), "now_epoch must be finite numeric"),
        (lambda c: c.__setitem__("now_epoch", float("nan")), "now_epoch must be finite numeric"),
        (lambda c: c.__setitem__("timeout", 5), "timeout must be exact int"),
        (lambda c: c.__setitem__("timeout", 905), "timeout must be exact int"),
        (lambda c: c.__setitem__("timeout", True), "timeout must be exact int"),
    ],
)
def test_prepublication_semantic_and_clock_failures_prevent_publish(
    monkeypatch: pytest.MonkeyPatch,
    mutator: Any,
    match_snippet: str,
) -> None:
    ctx = _build_valid_test_context()
    mutator(ctx)

    publisher_called = False

    def mock_publish(*args: Any, **kwargs: Any) -> Any:
        nonlocal publisher_called
        publisher_called = True
        raise AssertionError("Publisher must not be called on prepublication failure")

    monkeypatch.setattr(req_mod, "publish_known_reset_resume_claim", mock_publish)

    with pytest.raises(ClaimRequestError, match=match_snippet):
        compose_and_publish_known_reset_resume_request_locked(**ctx)

    assert not publisher_called


def test_publisher_error_and_readback_mismatch_fail_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _build_valid_test_context()

    def mock_publish_err(*args: Any, **kwargs: Any) -> Any:
        raise ClaimStorageError("disk full simulated failure")

    monkeypatch.setattr(req_mod, "publish_known_reset_resume_claim", mock_publish_err)
    with pytest.raises(ClaimRequestError, match="Claim publication failed"):
        compose_and_publish_known_reset_resume_request_locked(**ctx)

    def mock_publish_mismatch(root: Any, original_controller_run_id: Any, claim: Any, expected: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        mutated = copy.deepcopy(claim)
        mutated["task_id"] = "corrupted-id"
        return mutated

    monkeypatch.setattr(req_mod, "publish_known_reset_resume_claim", mock_publish_mismatch)
    with pytest.raises(ClaimRequestError, match="Published claim validation failed"):
        compose_and_publish_known_reset_resume_request_locked(**ctx)

    def mock_publish_nondict(*args: Any, **kwargs: Any) -> Any:
        return ["not", "a", "dict"]

    monkeypatch.setattr(req_mod, "publish_known_reset_resume_claim", mock_publish_nondict)
    with pytest.raises(ClaimRequestError, match="Published claim validation failed"):
        compose_and_publish_known_reset_resume_request_locked(**ctx)


def test_no_hidden_clock_and_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _build_valid_test_context()

    def _forbidden_time() -> float:
        raise AssertionError("time.time() was called during compose_and_publish")

    monkeypatch.setattr("time.time", _forbidden_time)

    publish_count = 0

    def mock_publish(root: Any, original_controller_run_id: Any, claim: Any, expected: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal publish_count
        publish_count += 1
        return copy.deepcopy(claim)

    monkeypatch.setattr(req_mod, "publish_known_reset_resume_claim", mock_publish)

    result = compose_and_publish_known_reset_resume_request_locked(**ctx)
    assert publish_count == 1
    assert result["request"]["created_at"] == ctx["now_epoch"]
