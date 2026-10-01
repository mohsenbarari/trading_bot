"""Focused tests for pure version 1.0 quarantine recovery record codec under AE03."""

from __future__ import annotations

import copy
import json
import pytest

try:
    from scripts.two_site_codex_autopilot import PROTECTED_REFS
except ImportError:
    from two_site_codex_autopilot import PROTECTED_REFS  # type: ignore[no-redef]

try:
    from scripts.two_site_quarantine_recovery_record import (
        MAX_FOLLOWUP_COMMITS,
        MAX_QUARANTINE_RECOVERY_RECORD_BYTES,
        MIN_FOLLOWUP_COMMITS,
        QUARANTINE_RECOVERY_KIND,
        QUARANTINE_RECOVERY_SCHEMA_VERSION,
        QuarantineRecoveryRecordError,
        build_quarantine_recovery_record,
        parse_quarantine_recovery_record,
        serialize_quarantine_recovery_record,
        validate_quarantine_recovery_record,
    )
except ImportError:
    from two_site_quarantine_recovery_record import (  # type: ignore[no-redef]
        MAX_FOLLOWUP_COMMITS,
        MAX_QUARANTINE_RECOVERY_RECORD_BYTES,
        MIN_FOLLOWUP_COMMITS,
        QUARANTINE_RECOVERY_KIND,
        QUARANTINE_RECOVERY_SCHEMA_VERSION,
        QuarantineRecoveryRecordError,
        build_quarantine_recovery_record,
        parse_quarantine_recovery_record,
        serialize_quarantine_recovery_record,
        validate_quarantine_recovery_record,
    )


def _make_valid_record() -> dict:
    return {
        "schema_version": QUARANTINE_RECOVERY_SCHEMA_VERSION,
        "kind": QUARANTINE_RECOVERY_KIND,
        "prior_checkpoint_sha256": "6d4ad41d24afd7451157ebe724a1418d4050e22e" + "0" * 24,
        "prior_maintenance_transition_sha256": "4b5bf7e7563822fa3d7dd7d55015469ca52bf92b" + "1" * 24,
        "failed_run_id": "supervisor-failed-001",
        "failed_result_sha256": "f" * 64,
        "failed_commit_head": "eef04f489f85a5e7ee4a1c2ebb8703d2038af5b4",
        "repair_commit_head": "3e8fd101e41697b6ed8cc53c8f19f11fb3cf591c",
        "new_repository": {
            "assignment_scope": {
                "allowed_paths": ["scripts/two_site_quarantine_recovery_record.py"],
                "assignment_id": "WA-AE03-082",
                "expires_at": "2026-10-01T12:00:00Z",
                "forbidden_paths": ["deploy/prod.sh"],
                "sha256": "c" * 64,
                "stage_id": "P1-00b",
            },
            "control_blobs": {
                "scripts/two_site_codex_autopilot.py": "b" * 40,
            },
            "head": "de24df2cb4ddd18f145080990aa67c9c3f86a6bb",
            "protected_refs": {ref: "a" * 40 for ref in sorted(PROTECTED_REFS)},
            "status": "",
        },
        "followup_commits": [
            {
                "head": "de24df2cb4ddd18f145080990aa67c9c3f86a6bb",
                "parent": "3e8fd101e41697b6ed8cc53c8f19f11fb3cf591c",
                "paths": ["scripts/two_site_quarantine_recovery_record.py"],
                "proposal_sha256": "1" * 64,
                "reviewer_run_id": "reviewer-run-001",
                "test_evidence_sha256": "2" * 64,
                "worker_run_id": "worker-run-001",
            }
        ],
        "final_validation_sha256": "e" * 64,
    }


def test_valid_record_build_serialize_parse_roundtrip() -> None:
    record_dict = _make_valid_record()
    built = build_quarantine_recovery_record(
        prior_checkpoint_sha256=record_dict["prior_checkpoint_sha256"],
        prior_maintenance_transition_sha256=record_dict["prior_maintenance_transition_sha256"],
        failed_run_id=record_dict["failed_run_id"],
        failed_result_sha256=record_dict["failed_result_sha256"],
        failed_commit_head=record_dict["failed_commit_head"],
        repair_commit_head=record_dict["repair_commit_head"],
        new_repository=record_dict["new_repository"],
        followup_commits=record_dict["followup_commits"],
        final_validation_sha256=record_dict["final_validation_sha256"],
    )
    assert built["kind"] == QUARANTINE_RECOVERY_KIND
    assert built["schema_version"] == QUARANTINE_RECOVERY_SCHEMA_VERSION

    # Deepcopy isolation
    built["new_repository"]["head"] = "0" * 40
    re_val = validate_quarantine_recovery_record(record_dict)
    assert re_val["new_repository"]["head"] == "de24df2cb4ddd18f145080990aa67c9c3f86a6bb"

    # Canonical serialization and parsing roundtrip
    raw_bytes = serialize_quarantine_recovery_record(re_val)
    assert raw_bytes.endswith(b"\n")
    assert len(raw_bytes) <= MAX_QUARANTINE_RECOVERY_RECORD_BYTES
    parsed = parse_quarantine_recovery_record(raw_bytes)
    assert parsed == re_val

    # Multi-commit chain (2 commits)
    multi_record = copy.deepcopy(record_dict)
    mid_head = "1" * 40
    multi_record["followup_commits"] = [
        {
            "head": mid_head,
            "parent": multi_record["repair_commit_head"],
            "paths": ["scripts/two_site_quarantine_recovery_record.py"],
            "proposal_sha256": "1" * 64,
            "reviewer_run_id": "reviewer-1",
            "test_evidence_sha256": "2" * 64,
            "worker_run_id": "worker-1",
        },
        {
            "head": multi_record["new_repository"]["head"],
            "parent": mid_head,
            "paths": ["tests/test_two_site_quarantine_recovery_record.py"],
            "proposal_sha256": "3" * 64,
            "reviewer_run_id": "reviewer-2",
            "test_evidence_sha256": "4" * 64,
            "worker_run_id": "worker-2",
        },
    ]
    multi_val = validate_quarantine_recovery_record(multi_record)
    assert len(multi_val["followup_commits"]) == 2


def test_commit_topology_and_head_rejections() -> None:
    record = _make_valid_record()

    # False claim: failed commit head is repair commit head
    bad = copy.deepcopy(record)
    bad["repair_commit_head"] = bad["failed_commit_head"]
    with pytest.raises(QuarantineRecoveryRecordError, match="failed_commit_head cannot equal repair_commit_head"):
        validate_quarantine_recovery_record(bad)

    # False claim: failed commit head is accepted in new_repository
    bad = copy.deepcopy(record)
    bad["new_repository"]["head"] = bad["failed_commit_head"]
    with pytest.raises(QuarantineRecoveryRecordError, match="failed_commit_head cannot equal new_repository head"):
        validate_quarantine_recovery_record(bad)

    # First commit parent does not match repair head
    bad = copy.deepcopy(record)
    bad["followup_commits"][0]["parent"] = "0" * 40
    with pytest.raises(QuarantineRecoveryRecordError, match="first followup commit parent must match repair_commit_head"):
        validate_quarantine_recovery_record(bad)

    # Final commit head does not match new_repository head
    bad = copy.deepcopy(record)
    bad["followup_commits"][0]["head"] = "0" * 40
    with pytest.raises(QuarantineRecoveryRecordError, match="final commit head must match new_repository head"):
        validate_quarantine_recovery_record(bad)

    # Head equals parent (zero diff cycle)
    bad = copy.deepcopy(record)
    bad["repair_commit_head"] = bad["new_repository"]["head"]
    bad["followup_commits"][0]["parent"] = bad["new_repository"]["head"]
    with pytest.raises(QuarantineRecoveryRecordError, match="commit head cannot equal parent"):
        validate_quarantine_recovery_record(bad)

    # Followup commit head cannot claim failed commit
    bad = copy.deepcopy(record)
    bad["followup_commits"][0]["head"] = bad["failed_commit_head"]
    with pytest.raises(QuarantineRecoveryRecordError, match="commit head cannot equal failed_commit_head"):
        validate_quarantine_recovery_record(bad)

    # Bounds on commits count
    bad = copy.deepcopy(record)
    bad["followup_commits"] = []
    with pytest.raises(QuarantineRecoveryRecordError, match="followup_commits count out of bounds"):
        validate_quarantine_recovery_record(bad)


def test_path_validation_and_rejections() -> None:
    record = _make_valid_record()

    # Path traversal and dot segments
    for bad_path in [
        "../evil.py",
        "scripts/two_site_../evil.py",
        "scripts/two_site_./test.py",
        "\\scripts\\two_site_test.py",
        "/scripts/two_site_test.py",
        "deploy/two_site_deploy.py",
        "config/two_site_config.py",
        "scripts/two_site_foo.sh",
        "src/two_site_code.py",
    ]:
        bad = copy.deepcopy(record)
        bad["followup_commits"][0]["paths"] = [bad_path]
        with pytest.raises(QuarantineRecoveryRecordError):
            validate_quarantine_recovery_record(bad)

    # Duplicate paths and unsorted paths
    bad = copy.deepcopy(record)
    bad["followup_commits"][0]["paths"] = [
        "scripts/two_site_quarantine_recovery_record.py",
        "scripts/two_site_quarantine_recovery_record.py",
    ]
    with pytest.raises(QuarantineRecoveryRecordError, match="duplicate commit path detected"):
        validate_quarantine_recovery_record(bad)

    bad = copy.deepcopy(record)
    bad["followup_commits"][0]["paths"] = [
        "tests/test_two_site_quarantine_recovery_record.py",
        "scripts/two_site_quarantine_recovery_record.py",
    ]
    with pytest.raises(QuarantineRecoveryRecordError, match="commit paths must be sorted"):
        validate_quarantine_recovery_record(bad)


def test_run_id_and_evidence_rejections() -> None:
    record = _make_valid_record()

    # Distinct worker and reviewer run IDs
    bad = copy.deepcopy(record)
    bad["followup_commits"][0]["worker_run_id"] = "same-run-id"
    bad["followup_commits"][0]["reviewer_run_id"] = "same-run-id"
    with pytest.raises(QuarantineRecoveryRecordError, match="worker_run_id and reviewer_run_id must be distinct"):
        validate_quarantine_recovery_record(bad)

    # Malformed worker_run_id
    bad = copy.deepcopy(record)
    bad["followup_commits"][0]["worker_run_id"] = "-invalid_lead"
    with pytest.raises(QuarantineRecoveryRecordError, match="worker_run_id format invalid"):
        validate_quarantine_recovery_record(bad)


def test_serialization_and_tampering_rejections() -> None:
    record = _make_valid_record()
    raw = serialize_quarantine_recovery_record(record)

    # Oversized raw bytes
    with pytest.raises(QuarantineRecoveryRecordError, match="raw bytes exceed maximum allowed length"):
        parse_quarantine_recovery_record(b" " * (MAX_QUARANTINE_RECOVERY_RECORD_BYTES + 1))

    # Empty bytes
    with pytest.raises(QuarantineRecoveryRecordError, match="raw bytes cannot be empty"):
        parse_quarantine_recovery_record(b"")

    # Non-canonical formatting (extra whitespace)
    with pytest.raises(QuarantineRecoveryRecordError, match="raw bytes do not match canonical JSON serialization"):
        parse_quarantine_recovery_record(raw.replace(b":", b": "))

    # Duplicate JSON keys
    dup_json = b'{"schema_version":"1.0","schema_version":"1.0"}'
    with pytest.raises(QuarantineRecoveryRecordError, match="Failed to parse JSON or duplicate key detected"):
        parse_quarantine_recovery_record(dup_json)

    # Booleans in place of string fields
    bad = copy.deepcopy(record)
    bad["schema_version"] = True
    with pytest.raises(QuarantineRecoveryRecordError, match="schema_version must be 1.0"):
        validate_quarantine_recovery_record(bad)

    # Unknown field in record
    bad = copy.deepcopy(record)
    bad["unexpected_field"] = "unauthorized"
    with pytest.raises(QuarantineRecoveryRecordError, match="record keys mismatch or unknown fields present"):
        validate_quarantine_recovery_record(bad)

    # Malformed hash (uppercase hex or incorrect length)
    bad = copy.deepcopy(record)
    bad["failed_result_sha256"] = "F" * 64
    with pytest.raises(QuarantineRecoveryRecordError, match="failed_result_sha256 must be 64 lowercase hex characters"):
        validate_quarantine_recovery_record(bad)
