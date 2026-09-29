"""Hermetic unit tests for scripts/two_site_quota_resume_lineage_snapshot_record.py."""

from __future__ import annotations

import copy
from typing import Any
import pytest

try:
    from scripts.two_site_provider_broker import MAX_REQUEST_BYTES
    from scripts.two_site_quota_resume_claim_admission import prepare_known_reset_resume_claim
    from scripts.two_site_quota_resume_claim_storage import MAX_CLAIM_RECORD_BYTES
    from scripts.two_site_quota_resume_lineage_snapshot_record import (
        MAX_SNAPSHOT_RECORD_BYTES,
        LineageSnapshotRecordError,
        SnapshotRecordError,
        build_expected_lineage_snapshot,
        canonical_json_bytes,
        compute_canonical_sha256,
        validate_expected_lineage_snapshot,
    )
    from scripts.two_site_quota_resume_request_record import build_quota_resume_request
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
    from scripts.two_site_quota_wait_storage import MAX_WAIT_RECORD_BYTES
    from tests.test_two_site_quota_resume_claim_admission import (
        _CONT_CYCLE_ID,
        _RESUME_REQUEST_ID,
        _TASK_ID,
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
    )
except ImportError:
    from two_site_provider_broker import MAX_REQUEST_BYTES
    from two_site_quota_resume_claim_admission import prepare_known_reset_resume_claim
    from two_site_quota_resume_claim_storage import MAX_CLAIM_RECORD_BYTES
    from two_site_quota_resume_lineage_snapshot_record import (
        MAX_SNAPSHOT_RECORD_BYTES,
        LineageSnapshotRecordError,
        SnapshotRecordError,
        build_expected_lineage_snapshot,
        canonical_json_bytes,
        compute_canonical_sha256,
        validate_expected_lineage_snapshot,
    )
    from two_site_quota_resume_request_record import build_quota_resume_request
    from two_site_quota_wait_schedule import _parse_utc_iso
    from two_site_quota_wait_storage import MAX_WAIT_RECORD_BYTES
    from test_two_site_quota_resume_claim_admission import (
        _CONT_CYCLE_ID,
        _RESUME_REQUEST_ID,
        _TASK_ID,
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
    )


class _IntSubclass(int):
    """Subclass of int to verify strict exact-type checking in _validate_max_bytes."""


def _build_valid_lineage_fixtures() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    wait_record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    actual_wait_sha = compute_canonical_sha256(wait_record, max_bytes=MAX_WAIT_RECORD_BYTES)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = wait_record["schedule"]["next_check_at_utc"]

    claim_bundle = prepare_known_reset_resume_claim(
        wait_record=wait_record,
        expected_wait=expected_wait,
        semantic_attempt=semantic_attempt,
        expected_task_id=_TASK_ID,
        original_wait_sha256=actual_wait_sha,
        continuation_controller_run_id=_CONT_CYCLE_ID,
        resume_request_id=_RESUME_REQUEST_ID,
        claimed_at_utc=due_time,
    )
    claim_record = claim_bundle["claim"]
    expected_claim = claim_bundle["expected"]
    actual_claim_sha = compute_canonical_sha256(claim_record, max_bytes=MAX_CLAIM_RECORD_BYTES)
    claim_epoch = _parse_utc_iso(claim_record["claimed_at_utc"]).timestamp()

    request = build_quota_resume_request(
        claim=claim_record,
        expected_claim=expected_claim,
        expected_claim_sha256=actual_claim_sha,
        timeout=60,
        now_epoch=claim_epoch,
    )
    return wait_record, expected_wait, claim_record, expected_claim, request


def test_alias_and_exact_schema_roundtrip_detachment() -> None:
    assert SnapshotRecordError is LineageSnapshotRecordError
    wait_record, expected_wait, claim_record, expected_claim, request = _build_valid_lineage_fixtures()

    wait_snap = copy.deepcopy(wait_record)
    expected_wait_snap = copy.deepcopy(expected_wait)
    claim_snap = copy.deepcopy(claim_record)
    expected_claim_snap = copy.deepcopy(expected_claim)
    request_snap = copy.deepcopy(request)

    snapshot = build_expected_lineage_snapshot(
        wait_record=wait_record,
        expected_wait=expected_wait,
        claim_record=claim_record,
        expected_claim=expected_claim,
        request=request,
    )

    exact_keys = {
        "schema_version",
        "original_controller_run_id",
        "resume_request_id",
        "expected_wait",
        "expected_claim",
        "expected_request",
        "original_wait_sha256",
        "claim_sha256",
        "request_sha256",
    }
    assert set(snapshot.keys()) == exact_keys
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["original_controller_run_id"] == wait_record["controller_run_id"]
    assert snapshot["resume_request_id"] == claim_record["resume_request_id"]
    assert snapshot["original_wait_sha256"] == compute_canonical_sha256(wait_record, max_bytes=MAX_WAIT_RECORD_BYTES)
    assert snapshot["claim_sha256"] == compute_canonical_sha256(claim_record, max_bytes=MAX_CLAIM_RECORD_BYTES)
    assert snapshot["request_sha256"] == compute_canonical_sha256(request, max_bytes=MAX_REQUEST_BYTES)

    validated = validate_expected_lineage_snapshot(snapshot, wait_record, claim_record)
    assert validated == snapshot
    assert validated is not snapshot

    assert wait_record == wait_snap
    assert expected_wait == expected_wait_snap
    assert claim_record == claim_snap
    assert expected_claim == expected_claim_snap
    assert request == request_snap

    snapshot["mutated"] = True
    assert "mutated" not in validated


@pytest.mark.parametrize(
    ("key", "bad_val"),
    [
        ("original_wait_sha256", "0" * 64),
        ("claim_sha256", "0" * 64),
        ("request_sha256", "0" * 64),
        ("original_controller_run_id", "cycle-2026-20260925T999999Z-999"),
        ("resume_request_id", "9" * 32),
        ("schema_version", "2.0"),
    ],
)
def test_snapshot_tamper_fails_validation(key: str, bad_val: Any) -> None:
    wait_record, expected_wait, claim_record, expected_claim, request = _build_valid_lineage_fixtures()
    snapshot = build_expected_lineage_snapshot(wait_record, expected_wait, claim_record, expected_claim, request)
    snapshot[key] = bad_val
    with pytest.raises(LineageSnapshotRecordError):
        validate_expected_lineage_snapshot(snapshot, wait_record, claim_record)


def test_hash_and_identity_cross_bindings_fail_closed() -> None:
    wait_record, expected_wait, claim_record, expected_claim, request = _build_valid_lineage_fixtures()
    snapshot = build_expected_lineage_snapshot(wait_record, expected_wait, claim_record, expected_claim, request)

    bad_claim = copy.deepcopy(claim_record)
    bad_claim["original_wait_sha256"] = "f" * 64
    with pytest.raises(LineageSnapshotRecordError):
        validate_expected_lineage_snapshot(snapshot, wait_record, bad_claim)

    bad_snapshot = copy.deepcopy(snapshot)
    bad_snapshot["expected_request"]["claim_sha256"] = "f" * 64
    with pytest.raises(LineageSnapshotRecordError):
        validate_expected_lineage_snapshot(bad_snapshot, wait_record, claim_record)

    bad_snapshot_role = copy.deepcopy(snapshot)
    bad_snapshot_role["expected_request"]["role"] = "verifier"
    with pytest.raises(LineageSnapshotRecordError):
        validate_expected_lineage_snapshot(bad_snapshot_role, wait_record, claim_record)

    bad_snapshot_fp = copy.deepcopy(snapshot)
    bad_snapshot_fp["expected_request"]["semantic_task_fingerprint"] = "f" * 64
    with pytest.raises(LineageSnapshotRecordError):
        validate_expected_lineage_snapshot(bad_snapshot_fp, wait_record, claim_record)

    bad_snapshot_tp = copy.deepcopy(snapshot)
    bad_snapshot_tp["expected_request"]["task_path"] = ".local/two-site-refactor/tasks/other.json"
    with pytest.raises(LineageSnapshotRecordError):
        validate_expected_lineage_snapshot(bad_snapshot_tp, wait_record, claim_record)

    bad_claim_sch = copy.deepcopy(claim_record)
    bad_claim_sch["next_check_at_utc"] = "2026-09-25T03:00:00Z"
    with pytest.raises(LineageSnapshotRecordError):
        validate_expected_lineage_snapshot(snapshot, wait_record, bad_claim_sch)


@pytest.mark.parametrize(
    "bad_created_at",
    [True, False, float("nan"), float("inf"), float("-inf"), "2026-09-25T01:45:00Z"],
)
def test_reject_bool_and_nonfinite_timestamps(bad_created_at: Any) -> None:
    wait_record, expected_wait, claim_record, expected_claim, request = _build_valid_lineage_fixtures()
    bad_req = copy.deepcopy(request)
    bad_req["created_at"] = bad_created_at
    with pytest.raises(LineageSnapshotRecordError):
        build_expected_lineage_snapshot(wait_record, expected_wait, claim_record, expected_claim, bad_req)


def test_reject_non_dict_inputs_and_extra_missing_keys() -> None:
    wait_record, expected_wait, claim_record, expected_claim, request = _build_valid_lineage_fixtures()
    snapshot = build_expected_lineage_snapshot(wait_record, expected_wait, claim_record, expected_claim, request)

    with pytest.raises(LineageSnapshotRecordError, match="snapshot must be dict"):
        validate_expected_lineage_snapshot(["not", "dict"], wait_record, claim_record)

    with pytest.raises(LineageSnapshotRecordError, match="wait_record must be dict"):
        validate_expected_lineage_snapshot(snapshot, "not-dict", claim_record)

    with pytest.raises(LineageSnapshotRecordError, match="claim_record must be dict"):
        validate_expected_lineage_snapshot(snapshot, wait_record, 12345)

    extra_snap = copy.deepcopy(snapshot)
    extra_snap["unexpected"] = True
    with pytest.raises(LineageSnapshotRecordError, match="keys mismatch"):
        validate_expected_lineage_snapshot(extra_snap, wait_record, claim_record)

    missing_snap = copy.deepcopy(snapshot)
    missing_snap.pop("expected_wait")
    with pytest.raises(LineageSnapshotRecordError, match="keys mismatch"):
        validate_expected_lineage_snapshot(missing_snap, wait_record, claim_record)


def test_bounded_incremental_encoding_and_serialization_errors() -> None:
    data = {"k": "v" * 100}
    encoded = canonical_json_bytes(data, max_bytes=200)
    assert isinstance(encoded, bytes)

    with pytest.raises(LineageSnapshotRecordError, match="exceeds byte limit"):
        canonical_json_bytes(data, max_bytes=20)

    with pytest.raises(LineageSnapshotRecordError, match="cannot be serialized"):
        canonical_json_bytes({"bad_float": float("nan")})

    with pytest.raises(LineageSnapshotRecordError, match="cannot be serialized"):
        canonical_json_bytes({"unsupported": {1, 2, 3}})


@pytest.mark.parametrize(
    "bad_limit",
    [
        None,
        True,
        False,
        0,
        -1,
        -100,
        MAX_SNAPSHOT_RECORD_BYTES + 1,
        MAX_SNAPSHOT_RECORD_BYTES + 10000,
        100.0,
        "200",
        _IntSubclass(100),
    ],
)
def test_reject_invalid_and_oversized_max_bytes(bad_limit: Any) -> None:
    data = {"k": "v"}
    with pytest.raises(LineageSnapshotRecordError):
        canonical_json_bytes(data, max_bytes=bad_limit)
    with pytest.raises(LineageSnapshotRecordError):
        compute_canonical_sha256(data, max_bytes=bad_limit)


def test_component_and_snapshot_byte_bounds_enforced() -> None:
    wait_record, expected_wait, claim_record, expected_claim, request = _build_valid_lineage_fixtures()

    bad_wait = copy.deepcopy(wait_record)
    bad_wait_expected = copy.deepcopy(expected_wait)
    allowed = ["tests/test_" + str(i) + ".py" for i in range(1500)]
    bad_wait["repository"]["assignment_scope"]["allowed_paths"] = allowed
    bad_wait_expected["repository"]["assignment_scope"]["allowed_paths"] = allowed

    with pytest.raises(LineageSnapshotRecordError, match="MAX_WAIT_RECORD_BYTES"):
        build_expected_lineage_snapshot(bad_wait, bad_wait_expected, claim_record, expected_claim, request)

    oversized_dict = {"pad": "x" * (MAX_SNAPSHOT_RECORD_BYTES + 10)}
    with pytest.raises(LineageSnapshotRecordError, match="exceeds byte limit"):
        canonical_json_bytes(oversized_dict, max_bytes=MAX_SNAPSHOT_RECORD_BYTES)
