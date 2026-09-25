"""Hermetic unit tests for scripts/two_site_quota_resume_claim_storage.py."""

from __future__ import annotations

import contextlib
import copy
import json
import os
from pathlib import Path
import stat
from typing import Any
from unittest import mock
import pytest

try:
    from scripts.two_site_provider_broker import BrokerError
    from scripts.two_site_quota_resume_claim_record import ResumeClaimRecordError
    import scripts.two_site_quota_resume_claim_storage as storage_mod
    from scripts.two_site_quota_resume_claim_storage import (
        ClaimStorageError,
        MAX_CLAIM_RECORD_BYTES,
        QUOTA_RESUME_CLAIM_FILENAME,
        read_known_reset_resume_claim,
    )
    from tests.test_two_site_quota_resume_claim_record import _build_valid_claim_fixtures
except ImportError:
    from two_site_provider_broker import BrokerError  # type: ignore
    from two_site_quota_resume_claim_record import ResumeClaimRecordError  # type: ignore
    import two_site_quota_resume_claim_storage as storage_mod  # type: ignore
    from two_site_quota_resume_claim_storage import (  # type: ignore
        ClaimStorageError,
        MAX_CLAIM_RECORD_BYTES,
        QUOTA_RESUME_CLAIM_FILENAME,
        read_known_reset_resume_claim,
    )
    from test_two_site_quota_resume_claim_record import _build_valid_claim_fixtures  # type: ignore


def _dump_canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@contextlib.contextmanager
def _mock_open_cycle_dir(run_path: Path, verify_callback: Any = None):
    fd = os.open(str(run_path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        def verify():
            if verify_callback is not None:
                verify_callback()
        yield run_path, fd, verify
    finally:
        os.close(fd)


def test_read_known_reset_resume_claim_success_and_detached_dict(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)

    claim_bytes = _dump_canonical_bytes(claim)
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME
    target_file.write_bytes(claim_bytes)
    os.chmod(target_file, 0o600)

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        result = read_known_reset_resume_claim(tmp_path, cid, expected)

    assert result == claim
    assert result is not claim
    result["mutated"] = True
    assert "mutated" not in claim
    assert target_file.read_bytes() == claim_bytes


def test_argument_pre_reading_validation(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]

    with pytest.raises(ClaimStorageError, match="root must be a Path"):
        read_known_reset_resume_claim("not_a_path", cid, expected)  # type: ignore

    with pytest.raises(ClaimStorageError, match="original_controller_run_id must be exact str"):
        read_known_reset_resume_claim(tmp_path, 12345, expected)  # type: ignore

    with pytest.raises(ClaimStorageError, match="original_controller_run_id must be a non-empty string"):
        read_known_reset_resume_claim(tmp_path, "", expected)

    with pytest.raises(ClaimStorageError, match="expected must be exact dict"):
        read_known_reset_resume_claim(tmp_path, cid, ["not", "a", "dict"])

    bad_keys_expected = copy.deepcopy(expected)
    bad_keys_expected.pop("task_id")
    with pytest.raises(ClaimStorageError, match="expected keys must exactly match"):
        read_known_reset_resume_claim(tmp_path, cid, bad_keys_expected)

    mismatched_cid_expected = copy.deepcopy(expected)
    mismatched_cid_expected["original_controller_run_id"] = "cycle-2026-20260925T010203Z-999"
    with pytest.raises(ClaimStorageError, match="expected original_controller_run_id .* does not match"):
        read_known_reset_resume_claim(tmp_path, cid, mismatched_cid_expected)


def test_cycle_run_directory_broker_and_identity_failures(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME
    target_file.write_bytes(_dump_canonical_bytes(claim))
    os.chmod(target_file, 0o600)

    def raise_broker_error(r, c):
        raise BrokerError("directory missing or corrupted")

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=raise_broker_error):
        with pytest.raises(ClaimStorageError, match="cannot safely open cycle run directory"):
            read_known_reset_resume_claim(tmp_path, cid, expected)

    def verify_fail_before():
        raise BrokerError("directory modified before read")

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=verify_fail_before)):
        with pytest.raises(ClaimStorageError, match="directory identity verification failed before read"):
            read_known_reset_resume_claim(tmp_path, cid, expected)

    verify_count = 0
    def verify_fail_after():
        nonlocal verify_count
        verify_count += 1
        if verify_count > 1:
            raise BrokerError("directory mutated after read")

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=verify_fail_after)):
        with pytest.raises(ClaimStorageError, match="directory identity verification failed after read"):
            read_known_reset_resume_claim(tmp_path, cid, expected)


def test_file_metadata_and_descriptor_safety_rejections(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME

    # Missing file
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="cannot lstat"):
            read_known_reset_resume_claim(tmp_path, cid, expected)

    # Symlink rejection
    real_file = tmp_path / "real_claim.json"
    real_file.write_bytes(_dump_canonical_bytes(claim))
    os.chmod(real_file, 0o600)
    target_file.symlink_to(real_file)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="not a regular file"):
            read_known_reset_resume_claim(tmp_path, cid, expected)
    target_file.unlink()

    # Hardlink rejection
    target_file.write_bytes(_dump_canonical_bytes(claim))
    os.chmod(target_file, 0o600)
    hardlink_file = run_dir / "hardlink_claim.json"
    os.link(target_file, hardlink_file)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="has invalid link count"):
            read_known_reset_resume_claim(tmp_path, cid, expected)
    hardlink_file.unlink()

    # Invalid mode (0644)
    os.chmod(target_file, 0o644)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="mode must be 0600"):
            read_known_reset_resume_claim(tmp_path, cid, expected)

    # Oversized file (> 8192 bytes)
    target_file.write_bytes(b"x" * 8193)
    os.chmod(target_file, 0o600)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="exceeds byte limit"):
            read_known_reset_resume_claim(tmp_path, cid, expected)


@pytest.mark.parametrize(
    ("raw_data", "match_snippet"),
    [
        (b"\xff\xfe\xfd", "is not valid UTF-8"),
        (b"not valid json", "is not valid JSON"),
        (b'{"schema_version":"1.0","schema_version":"1.0"}', "is not valid JSON"),
        (b'{"schema_version": "1.0"}', "is not canonical JSON bytes"),
        (b'["not", "a", "dict"]', "must be a JSON object"),
    ],
)
def test_content_and_encoding_safety_rejections(
    tmp_path: Path,
    raw_data: bytes,
    match_snippet: str,
) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME
    target_file.write_bytes(raw_data)
    os.chmod(target_file, 0o600)

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match=match_snippet):
            read_known_reset_resume_claim(tmp_path, cid, expected)


def test_payload_validation_and_expected_mismatch_fails_closed(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME

    # Claim content mismatch with expected
    claim["task_id"] = "task-mismatched"
    target_file.write_bytes(_dump_canonical_bytes(claim))
    os.chmod(target_file, 0o600)

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="known-reset resume claim validation failed"):
            read_known_reset_resume_claim(tmp_path, cid, expected)


def test_descriptor_closed_on_read_failure(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME
    target_file.write_bytes(_dump_canonical_bytes(claim))
    os.chmod(target_file, 0o600)

    real_close = os.close
    closed_fds: list[int] = []

    def track_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def fail_read(fd: int, n: int) -> bytes:
        raise OSError("simulated low-level read error")

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.read", side_effect=fail_read):
            with mock.patch("os.close", side_effect=track_close):
                with pytest.raises(ClaimStorageError, match="cannot read quota-resume-claim.json"):
                    read_known_reset_resume_claim(tmp_path, cid, expected)

    assert len(closed_fds) >= 1
