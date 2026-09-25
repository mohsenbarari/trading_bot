"""Hermetic unit tests for scripts/two_site_quota_resume_request_record.py."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
import pytest

try:
    from scripts.two_site_quota_resume_request_record import (
        ResumeRequestRecordError,
        validate_quota_resume_request,
    )
    from tests.test_two_site_quota_resume_claim_record import _build_valid_claim_fixtures
except ImportError:
    from two_site_quota_resume_request_record import (
        ResumeRequestRecordError,
        validate_quota_resume_request,
    )
    from test_two_site_quota_resume_claim_record import _build_valid_claim_fixtures


def _build_valid_request_fixtures() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, float]:
    claim, expected_claim = _build_valid_claim_fixtures()
    claim_sha = "a" * 64
    claim_dt = datetime.fromisoformat(claim["claimed_at_utc"].replace("Z", "+00:00"))
    now_epoch = claim_dt.timestamp() + 5.0
    created_at = now_epoch
    request: dict[str, Any] = {
        "schema_version": "1.0",
        "request_type": "QUOTA_CONTINUATION",
        "request_id": claim["resume_request_id"],
        "controller_run_id": claim["continuation_controller_run_id"],
        "original_controller_run_id": claim["original_controller_run_id"],
        "original_request_id": claim["original_request_id"],
        "claim_sha256": claim_sha,
        "original_wait_sha256": claim["original_wait_sha256"],
        "semantic_task_fingerprint": claim["semantic_task_fingerprint"],
        "task_id": claim["task_id"],
        "task_path": claim["task_path"],
        "task_sha256": claim["task_sha256"],
        "source_head": claim["source_head"],
        "assignment_id": claim["assignment_id"],
        "assignment_sha256": claim["assignment_sha256"],
        "run_prefix": claim["resume_run_prefix"],
        "role": claim["role"],
        "timeout": 120,
        "created_at": created_at,
    }
    return request, claim, expected_claim, claim_sha, now_epoch


def test_valid_roundtrip_detached_and_boundary() -> None:
    req, claim, exp, sha, now = _build_valid_request_fixtures()
    req_snap, claim_snap, exp_snap = copy.deepcopy(req), copy.deepcopy(claim), copy.deepcopy(exp)
    res = validate_quota_resume_request(req, claim, exp, sha, now)
    assert res == req and res is not req
    assert req == req_snap and claim == claim_snap and exp == exp_snap
    res["schema_version"] = "MUTATED"
    assert req["schema_version"] == "1.0"

    claim_dt = datetime.fromisoformat(claim["claimed_at_utc"].replace("Z", "+00:00"))
    boundary_epoch = claim_dt.timestamp()
    req["created_at"] = boundary_epoch
    assert validate_quota_resume_request(req, claim, exp, sha, boundary_epoch)["created_at"] == boundary_epoch


def test_chronology_and_clock_regression_rejections() -> None:
    req, claim, exp, sha, now = _build_valid_request_fixtures()
    claim_dt = datetime.fromisoformat(claim["claimed_at_utc"].replace("Z", "+00:00"))
    claim_epoch = claim_dt.timestamp()

    req["created_at"] = claim_epoch - 1.0
    with pytest.raises(ResumeRequestRecordError, match="Chronology violation"):
        validate_quota_resume_request(req, claim, exp, sha, now)

    req["created_at"] = claim_epoch - 1.0
    with pytest.raises(ResumeRequestRecordError, match="Clock regression"):
        validate_quota_resume_request(req, claim, exp, sha, claim_epoch - 1.0)


@pytest.mark.parametrize("delta,match_snip", [(-301, "outside freshness"), (31, "outside freshness")])
def test_stale_and_future_created_at_rejected(delta: float, match_snip: str) -> None:
    req, claim, exp, sha, now = _build_valid_request_fixtures()
    claim_dt = datetime.fromisoformat(claim["claimed_at_utc"].replace("Z", "+00:00"))
    high_now = claim_dt.timestamp() + 500.0
    req["created_at"] = high_now + delta
    with pytest.raises(ResumeRequestRecordError, match=match_snip):
        validate_quota_resume_request(req, claim, exp, sha, high_now)


def test_bad_claim_expected_and_digest_rejected() -> None:
    req, claim, exp, sha, now = _build_valid_request_fixtures()
    with pytest.raises(ResumeRequestRecordError, match="expected_claim_sha256 must be 64"):
        validate_quota_resume_request(req, claim, exp, "bad-sha", now)
    with pytest.raises(ResumeRequestRecordError, match="expected_claim_sha256 must be 64"):
        validate_quota_resume_request(req, claim, exp, "A" * 64, now)
    with pytest.raises(ResumeRequestRecordError, match="claim_sha256 mismatch"):
        validate_quota_resume_request(req, claim, exp, "b" * 64, now)
    with pytest.raises(ResumeRequestRecordError, match="claim_sha256 must be 64"):
        bad_req = dict(req, claim_sha256="bad")
        validate_quota_resume_request(bad_req, claim, exp, sha, now)
    with pytest.raises(ResumeRequestRecordError, match="claim validation failed"):
        bad_claim = dict(claim, schema_version="2.0")
        validate_quota_resume_request(req, bad_claim, exp, sha, now)
    with pytest.raises(ResumeRequestRecordError, match="claim validation failed"):
        bad_exp = dict(exp, task_id="mismatch-id")
        validate_quota_resume_request(req, claim, bad_exp, sha, now)


@pytest.mark.parametrize(
    ("mutator", "match_snip"),
    [
        (lambda r: r.pop("schema_version"), "request keys mismatch"),
        (lambda r: r.__setitem__("extra_key", 1), "request keys mismatch"),
        (lambda r: r.__setitem__("schema_version", "2.0"), "schema_version must be '1.0'"),
        (lambda r: r.__setitem__("request_type", "OTHER"), "request_type must be 'QUOTA_CONTINUATION'"),
        (lambda r: r.__setitem__("timeout", 9), "timeout must be exact int"),
        (lambda r: r.__setitem__("timeout", 901), "timeout must be exact int"),
        (lambda r: r.__setitem__("timeout", True), "timeout must be exact int"),
        (lambda r: r.__setitem__("timeout", 120.0), "timeout must be exact int"),
        (lambda r: r.__setitem__("created_at", True), "created_at must be finite numeric"),
        (lambda r: r.__setitem__("created_at", float("inf")), "created_at must be finite numeric"),
        (lambda r: r.__setitem__("created_at", "2026-09-25T00:00:00Z"), "created_at must be finite numeric"),
    ],
)
def test_request_structure_and_types_rejected(mutator: Any, match_snip: str) -> None:
    req, claim, exp, sha, now = _build_valid_request_fixtures()
    mutator(req)
    with pytest.raises(ResumeRequestRecordError, match=match_snip):
        validate_quota_resume_request(req, claim, exp, sha, now)


def test_invalid_now_epoch_rejected() -> None:
    req, claim, exp, sha, now = _build_valid_request_fixtures()
    for bad_now in (True, False, "123", float("nan"), float("inf")):
        with pytest.raises(ResumeRequestRecordError, match="now_epoch must be finite numeric"):
            validate_quota_resume_request(req, claim, exp, sha, bad_now)


def test_non_dict_request_rejected() -> None:
    _, claim, exp, sha, now = _build_valid_request_fixtures()
    with pytest.raises(ResumeRequestRecordError, match="request must be dict"):
        validate_quota_resume_request(["not", "dict"], claim, exp, sha, now)


@pytest.mark.parametrize(
    ("key", "val"),
    [
        ("request_id", "0" * 32),
        ("controller_run_id", "cycle-2026-20260925T010000Z-000"),
        ("original_controller_run_id", "cycle-2026-20260925T010000Z-000"),
        ("original_request_id", "0" * 32),
        ("original_wait_sha256", "0" * 64),
        ("semantic_task_fingerprint", "0" * 64),
        ("task_id", "task-drift"),
        ("task_path", ".local/two-site-refactor/tasks/other.json"),
        ("task_sha256", "0" * 64),
        ("source_head", "0" * 40),
        ("assignment_id", "other-assignment"),
        ("assignment_sha256", "0" * 64),
        ("run_prefix", "qresume-other"),
        ("role", "verifier"),
    ],
)
def test_lineage_field_mismatch_rejected(key: str, val: Any) -> None:
    req, claim, exp, sha, now = _build_valid_request_fixtures()
    req[key] = val
    with pytest.raises(ResumeRequestRecordError, match="mismatch"):
        validate_quota_resume_request(req, claim, exp, sha, now)
