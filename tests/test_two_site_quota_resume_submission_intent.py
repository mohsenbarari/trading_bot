"""Hermetic tests for one-shot durable quota resume submission intent artifact."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any
from unittest.mock import patch

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_provider_broker import read_terminal_quota_bytes
from scripts.two_site_quota_resume_submission_intent import (
    MAX_SUBMISSION_RECORD_BYTES,
    QUOTA_RESUME_SUBMISSION_FILENAME,
    SubmissionIntentError,
    build_quota_resume_submission_record,
    compute_canonical_request_sha256,
    publish_quota_resume_submission_intent,
    read_quota_resume_submission_intent,
    read_quota_resume_submission_intent_bytes,
    validate_quota_resume_submission_record,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


def test_pure_submission_record_validation(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    full_request = ctx["resume_request"]
    assert isinstance(full_request, dict)
    assert len(full_request) == 20

    # Positive: build and validate with real full request
    built = build_quota_resume_submission_record(full_request)
    assert built["schema_version"] == "1.0"
    assert built["request_id"] == full_request["request_id"]
    assert built["original_controller_run_id"] == full_request["original_controller_run_id"]
    assert built["claim_sha256"] == full_request["claim_sha256"]
    expected_req_sha = compute_canonical_request_sha256(full_request)
    assert built["request_sha256"] == expected_req_sha

    validated = validate_quota_resume_submission_record(built, expected_request=full_request)
    assert validated == built

    # Partial request rejection before accepting record
    partial_six_keys = {
        "schema_version": "1.0",
        "request_type": "QUOTA_CONTINUATION",
        "request_id": full_request["request_id"],
        "controller_run_id": full_request["controller_run_id"],
        "original_controller_run_id": full_request["original_controller_run_id"],
        "claim_sha256": full_request["claim_sha256"],
    }
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        build_quota_resume_submission_record(partial_six_keys)
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        validate_quota_resume_submission_record(built, expected_request=partial_six_keys)
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        compute_canonical_request_sha256(partial_six_keys)

    # Missing individual keys in request
    missing_task_id = dict(full_request)
    del missing_task_id["task_id"]
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        build_quota_resume_submission_record(missing_task_id)
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        validate_quota_resume_submission_record(built, expected_request=missing_task_id)

    # Wrong request_type in request
    wrong_req_type = dict(full_request, request_type="QUOTA_INITIAL")
    with pytest.raises(SubmissionIntentError, match="request_type"):
        build_quota_resume_submission_record(wrong_req_type)
    with pytest.raises(SubmissionIntentError, match="request_type"):
        validate_quota_resume_submission_record(built, expected_request=wrong_req_type)

    # Extra key in request
    extra_key_req = dict(full_request, unexpected="extra")
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        build_quota_resume_submission_record(extra_key_req)
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        validate_quota_resume_submission_record(built, expected_request=extra_key_req)

    # Standalone record-schema rejection (without expected request)
    bad_rec_extra = dict(built, extra="field")
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        validate_quota_resume_submission_record(bad_rec_extra)

    bad_rec_missing = dict(built)
    del bad_rec_missing["request_sha256"]
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        validate_quota_resume_submission_record(bad_rec_missing)

    bad_rec_version = dict(built, schema_version="2.0")
    with pytest.raises(SubmissionIntentError, match="schema_version"):
        validate_quota_resume_submission_record(bad_rec_version)

    bad_rec_req_id = dict(built, request_id="invalid-hex")
    with pytest.raises(SubmissionIntentError, match="request_id"):
        validate_quota_resume_submission_record(bad_rec_req_id)

    bad_rec_claim_sha = dict(built, claim_sha256="X" * 64)
    with pytest.raises(SubmissionIntentError, match="claim_sha256"):
        validate_quota_resume_submission_record(bad_rec_claim_sha)

    bad_rec_req_sha = dict(built, request_sha256=12345)
    with pytest.raises(SubmissionIntentError, match="request_sha256"):
        validate_quota_resume_submission_record(bad_rec_req_sha)

    # Binding mismatch against full request
    mismatched_req_id = dict(built, request_id="f" * 32)
    with pytest.raises(SubmissionIntentError, match="request_id mismatch"):
        validate_quota_resume_submission_record(mismatched_req_id, expected_request=full_request)

    mismatched_orig_cid = dict(built, original_controller_run_id="cycle-0001-20990101T000000Z-999")
    with pytest.raises(SubmissionIntentError, match="original_controller_run_id mismatch"):
        validate_quota_resume_submission_record(mismatched_orig_cid, expected_request=full_request)

    mismatched_claim_sha = dict(built, claim_sha256="f" * 64)
    with pytest.raises(SubmissionIntentError, match="claim_sha256 mismatch"):
        validate_quota_resume_submission_record(mismatched_claim_sha, expected_request=full_request)

    mismatched_req_sha = dict(built, request_sha256="f" * 64)
    with pytest.raises(SubmissionIntentError, match="request_sha256 mismatch"):
        validate_quota_resume_submission_record(mismatched_req_sha, expected_request=full_request)


def test_publish_and_read_submission_intent_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root = ctx["root"]
    task = ctx["task"]
    sc = ctx["sc"]
    runs_dir = ctx["runs_dir"]
    controller_run_id = ctx["controller_run_id"]
    wait_file = ctx["wait_file"]
    raw_wait_bytes = ctx["raw_wait_bytes"]
    orig_wait_sha256 = ctx["orig_wait_sha256"]
    claim_file = ctx["claim_file"]
    persisted_claim_bytes = ctx["persisted_claim_bytes"]
    claim_sha256 = ctx["claim_sha256"]
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]
    expected_runs = ctx["expected_runs"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        published = publish_quota_resume_submission_intent(
            root,
            resume_request,
            now_epoch=now_epoch,
        )

    expected_request_sha = compute_canonical_request_sha256(resume_request)
    assert published["schema_version"] == "1.0"
    assert published["request_id"] == resume_request["request_id"]
    assert published["original_controller_run_id"] == controller_run_id
    assert published["claim_sha256"] == claim_sha256
    assert published["request_sha256"] == expected_request_sha

    submission_file = runs_dir / QUOTA_RESUME_SUBMISSION_FILENAME
    assert submission_file.is_file()
    st = submission_file.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1
    assert st.st_size <= MAX_SUBMISSION_RECORD_BYTES

    readback = read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    assert readback == published

    raw_bytes = read_quota_resume_submission_intent_bytes(root, controller_run_id)
    canonical_expected = json.dumps(
        published,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert raw_bytes == canonical_expected

    # Assert all original artifacts and admissions remain unchanged
    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
    assert wait_file.read_bytes() == raw_wait_bytes
    assert hashlib.sha256(wait_file.read_bytes()).hexdigest() == orig_wait_sha256
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert hashlib.sha256(claim_file.read_bytes()).hexdigest() == claim_sha256

    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert hashlib.sha256(task.read_bytes()).hexdigest() == sc["task_sha_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
    )
    assert (
        hashlib.sha256(sc["raw_receipt_bytes"]).hexdigest()
        == sc["receipt_sha_snapshot"]
    )
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()


def test_duplicate_publication_fails_no_overwrite(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root = ctx["root"]
    runs_dir = ctx["runs_dir"]
    sc = ctx["sc"]
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        first = publish_quota_resume_submission_intent(
            root,
            resume_request,
            now_epoch=now_epoch,
        )

    submission_file = runs_dir / QUOTA_RESUME_SUBMISSION_FILENAME
    first_bytes = submission_file.read_bytes()
    first_stat = submission_file.stat()

    with exclusive_supervisor_lock(sc["lock_path"]):
        with pytest.raises(SubmissionIntentError, match="already exists"):
            publish_quota_resume_submission_intent(
                root,
                resume_request,
                now_epoch=now_epoch,
            )

    # No overwrite or deletion occurred
    assert submission_file.is_file()
    assert submission_file.read_bytes() == first_bytes
    second_stat = submission_file.stat()
    assert second_stat.st_ino == first_stat.st_ino
    assert second_stat.st_size == first_stat.st_size
    assert second_stat.st_mtime_ns == first_stat.st_mtime_ns


def test_reader_rejects_tampered_or_invalid_modes(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root = ctx["root"]
    runs_dir = ctx["runs_dir"]
    controller_run_id = ctx["controller_run_id"]
    sc = ctx["sc"]
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        publish_quota_resume_submission_intent(
            root,
            resume_request,
            now_epoch=now_epoch,
        )

    submission_file = runs_dir / QUOTA_RESUME_SUBMISSION_FILENAME
    original_bytes = submission_file.read_bytes()

    # 1. Mode 0644 rejected
    submission_file.chmod(0o644)
    with pytest.raises(SubmissionIntentError, match="mode must be 0600"):
        read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    submission_file.chmod(0o600)

    # 2. Hardlink rejected
    extra_link = runs_dir / "hardlink.json"
    os.link(submission_file, extra_link)
    try:
        with pytest.raises(SubmissionIntentError, match="invalid link count"):
            read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    finally:
        extra_link.unlink()

    # 3. Symlink rejected
    backup_file = runs_dir / "backup.json"
    backup_file.write_bytes(original_bytes)
    submission_file.unlink()
    submission_file.symlink_to(backup_file.name)
    try:
        with pytest.raises(SubmissionIntentError, match="not a regular file"):
            read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    finally:
        submission_file.unlink()
        submission_file.write_bytes(original_bytes)
        submission_file.chmod(0o600)
        backup_file.unlink()

    # 4. Oversize rejected
    submission_file.write_bytes(b"{" + b" " * 5000 + b"}")
    submission_file.chmod(0o600)
    with pytest.raises(SubmissionIntentError, match="exceeds byte limit"):
        read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    submission_file.write_bytes(original_bytes)
    submission_file.chmod(0o600)

    # 5. Non-canonical JSON bytes rejected
    non_canonical = original_bytes + b" "
    submission_file.write_bytes(non_canonical)
    submission_file.chmod(0o600)
    with pytest.raises(SubmissionIntentError, match="not canonical JSON bytes"):
        read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    submission_file.write_bytes(original_bytes)
    submission_file.chmod(0o600)

    # 6. Tampered hash inside valid JSON rejected
    tampered_data = json.loads(original_bytes.decode("utf-8"))
    tampered_data["request_sha256"] = "0" * 64
    tampered_bytes = json.dumps(
        tampered_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    submission_file.write_bytes(tampered_bytes)
    submission_file.chmod(0o600)
    with pytest.raises(SubmissionIntentError, match="request_sha256 mismatch"):
        read_quota_resume_submission_intent(root, controller_run_id, resume_request)
    submission_file.write_bytes(original_bytes)
    submission_file.chmod(0o600)

    # 7. Unmatched expected request rejected
    unmatched_request = dict(resume_request, request_id="0" * 32)
    with pytest.raises(SubmissionIntentError, match="request_id mismatch"):
        read_quota_resume_submission_intent(root, controller_run_id, unmatched_request)

    # 8. Partial expected request to reader rejected
    partial_request = {
        "schema_version": "1.0",
        "request_type": "QUOTA_CONTINUATION",
        "request_id": resume_request["request_id"],
    }
    with pytest.raises(SubmissionIntentError, match="keys mismatch"):
        read_quota_resume_submission_intent(root, controller_run_id, partial_request)


def test_crash_at_publication_boundary(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root = ctx["root"]
    runs_dir = ctx["runs_dir"]
    sc = ctx["sc"]
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]

    submission_file = runs_dir / QUOTA_RESUME_SUBMISSION_FILENAME
    assert not submission_file.exists()

    # Pre-link crash: error during write cleans up temp file and leaves no intent
    def failing_write(fd: int, data: bytes) -> int:
        raise OSError("disk write failure simulation")

    with patch("os.write", side_effect=failing_write):
        with exclusive_supervisor_lock(sc["lock_path"]):
            with pytest.raises(SubmissionIntentError, match="disk write failure simulation"):
                publish_quota_resume_submission_intent(
                    root,
                    resume_request,
                    now_epoch=now_epoch,
                )

    assert not submission_file.exists()
    # Verify no leaked temp files
    temp_files = [p for p in runs_dir.iterdir() if p.name.startswith(".quota-resume-submission")]
    assert temp_files == []

    # Post-link crash: error during post-link unlink leaves intent intact and raises ambiguous failure
    real_unlink = os.unlink

    def failing_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        str_path = str(path)
        if ".tmp." in str_path or ".quota-resume-submission.tmp." in str_path:
            raise OSError("simulated post-link unlink failure")
        return real_unlink(path, *args, **kwargs)

    with patch("os.unlink", side_effect=failing_unlink):
        with exclusive_supervisor_lock(sc["lock_path"]):
            with pytest.raises(
                SubmissionIntentError, match="ambiguous post-link publication failure"
            ):
                publish_quota_resume_submission_intent(
                    root,
                    resume_request,
                    now_epoch=now_epoch,
                )

    # Target file is durably linked and NOT cleaned up or overwritten
    assert submission_file.is_file()
    assert stat.S_IMODE(submission_file.stat().st_mode) == 0o600

    # A second attempt to publish sees the existing target and refuses to retry automatically
    with exclusive_supervisor_lock(sc["lock_path"]):
        with pytest.raises(SubmissionIntentError, match="already exists"):
            publish_quota_resume_submission_intent(
                root,
                resume_request,
                now_epoch=now_epoch,
            )
