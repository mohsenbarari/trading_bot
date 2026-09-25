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
        publish_known_reset_resume_claim,
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
        publish_known_reset_resume_claim,
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


def test_publish_known_reset_resume_claim_success(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        result = publish_known_reset_resume_claim(tmp_path, cid, claim, expected)

    assert result == claim
    assert result is not claim
    result["mutated"] = True
    assert "mutated" not in claim

    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME
    assert target_file.is_file()
    st = os.stat(target_file)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1
    assert target_file.read_bytes() == _dump_canonical_bytes(claim)

    temp_files = [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")]
    assert temp_files == []


def test_publish_argument_pre_io_validation(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]

    with pytest.raises(ClaimStorageError, match="root must be a Path"):
        publish_known_reset_resume_claim("not_a_path", cid, claim, expected)  # type: ignore

    with pytest.raises(ClaimStorageError, match="original_controller_run_id must be exact str"):
        publish_known_reset_resume_claim(tmp_path, 12345, claim, expected)  # type: ignore

    with pytest.raises(ClaimStorageError, match="original_controller_run_id must be a non-empty string"):
        publish_known_reset_resume_claim(tmp_path, "", claim, expected)

    with pytest.raises(ClaimStorageError, match="claim must be exact dict"):
        publish_known_reset_resume_claim(tmp_path, cid, ["not", "a", "dict"], expected)  # type: ignore

    with pytest.raises(ClaimStorageError, match="expected must be exact dict"):
        publish_known_reset_resume_claim(tmp_path, cid, claim, ["not", "a", "dict"])  # type: ignore

    bad_keys_expected = copy.deepcopy(expected)
    bad_keys_expected.pop("task_id")
    with pytest.raises(ClaimStorageError, match="expected keys must exactly match"):
        publish_known_reset_resume_claim(tmp_path, cid, claim, bad_keys_expected)

    mismatched_cid_expected = copy.deepcopy(expected)
    mismatched_cid_expected["original_controller_run_id"] = "cycle-2026-20260925T010203Z-999"
    with pytest.raises(ClaimStorageError, match="expected original_controller_run_id .* does not match"):
        publish_known_reset_resume_claim(tmp_path, cid, claim, mismatched_cid_expected)

    invalid_claim = copy.deepcopy(claim)
    invalid_claim["schema_version"] = "2.0"
    with pytest.raises(ClaimStorageError, match="known-reset resume claim validation failed"):
        publish_known_reset_resume_claim(tmp_path, cid, invalid_claim, expected)

    with mock.patch.object(storage_mod, "validate_known_reset_resume_claim", return_value={"large": "x" * 8500}):
        with pytest.raises(ClaimStorageError, match="known-reset resume claim exceeds byte limit"):
            publish_known_reset_resume_claim(tmp_path, cid, claim, expected)

    assert list(tmp_path.iterdir()) == []


def test_publish_rejects_preexisting_target_untouched(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME

    # Pre-existing regular file
    preexisting_content = b"preexisting-untouched"
    target_file.write_bytes(preexisting_content)
    os.chmod(target_file, 0o600)
    target_stat_before = os.stat(target_file)

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="quota-resume-claim.json already exists"):
            publish_known_reset_resume_claim(tmp_path, cid, claim, expected)

    assert target_file.read_bytes() == preexisting_content
    target_stat_after = os.stat(target_file)
    assert target_stat_before.st_ino == target_stat_after.st_ino
    assert target_stat_before.st_mtime_ns == target_stat_after.st_mtime_ns
    target_file.unlink()

    # Pre-existing dangling symlink
    target_file.symlink_to(run_dir / "nonexistent.target")
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="quota-resume-claim.json already exists"):
            publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert target_file.is_symlink()
    target_file.unlink()

    # Pre-existing valid symlink
    other_target = run_dir / "other.json"
    other_target.write_bytes(b"{}")
    target_file.symlink_to(other_target)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with pytest.raises(ClaimStorageError, match="quota-resume-claim.json already exists"):
            publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert target_file.is_symlink()
    target_file.unlink()
    other_target.unlink()

    # Verify directory identity pre-check failure rejects before target check
    def verify_fail():
        raise BrokerError("identity check failed before publish")

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=verify_fail)):
        with pytest.raises(ClaimStorageError, match="directory identity verification failed before write"):
            publish_known_reset_resume_claim(tmp_path, cid, claim, expected)


def test_publish_temp_file_faults_and_cleanup(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)

    # 1. Fault during os.open
    real_open = os.open
    def fail_temp_open(path, flags, *args, **kwargs):
        if str(path).startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp"):
            raise OSError("cannot create file")
        return real_open(path, flags, *args, **kwargs)

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.open", side_effect=fail_temp_open):
            with pytest.raises(ClaimStorageError, match="cannot safely create temp file"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # 2. Fault during os.write
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.write", side_effect=OSError("disk full")):
            with pytest.raises(ClaimStorageError, match="cannot write .quota-resume-claim.json.tmp"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # 3. Write stall (written <= 0)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.write", return_value=0):
            with pytest.raises(ClaimStorageError, match="write stalled"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # 4. Fault during os.fchmod
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.fchmod", side_effect=OSError("fchmod failure")):
            with pytest.raises(ClaimStorageError, match="cannot fchmod"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # 5. Fault during os.fstat
    real_fstat = os.fstat
    def fail_fstat_file(fd):
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            raise OSError("fstat failure")
        return st
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.fstat", side_effect=fail_fstat_file):
            with pytest.raises(ClaimStorageError, match="cannot fstat"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # 6. Fault during os.fsync(file_fd)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        real_fsync = os.fsync
        def fail_fsync_file(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                raise OSError("fsync failure")
            return real_fsync(fd)
        with mock.patch("os.fsync", side_effect=fail_fsync_file):
            with pytest.raises(ClaimStorageError, match="cannot fsync .quota-resume-claim.json.tmp"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # 7. Fault during os.close(file_fd)
    real_close = os.close
    def fail_file_close(fd):
        try:
            st = real_fstat(fd)
            is_reg = stat.S_ISREG(st.st_mode)
        except OSError:
            is_reg = False
        real_close(fd)
        if is_reg:
            raise OSError("close failure")
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.close", side_effect=fail_file_close):
            with pytest.raises(ClaimStorageError, match="cannot safely close temp file"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # 8. Cleanup failure makes error visible
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.write", side_effect=OSError("initial write failure")):
            with mock.patch("os.unlink", side_effect=OSError("unlink cleanup failure")):
                with pytest.raises(ClaimStorageError, match="failed to clean up temp file"):
                    publish_known_reset_resume_claim(tmp_path, cid, claim, expected)


def test_publish_link_collision_and_link_fault(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)

    # Link collision (FileExistsError)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.link", side_effect=FileExistsError("target already linked")):
            with pytest.raises(ClaimStorageError, match="cannot link quota-resume-claim.json: target already exists"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert not (run_dir / QUOTA_RESUME_CLAIM_FILENAME).exists()
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []

    # Generic link OSError
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.link", side_effect=OSError("link failed")):
            with pytest.raises(ClaimStorageError, match="cannot link quota-resume-claim.json: link failed"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert not (run_dir / QUOTA_RESUME_CLAIM_FILENAME).exists()
    assert [f for f in os.listdir(run_dir) if f.startswith(f".{QUOTA_RESUME_CLAIM_FILENAME}.tmp")] == []


def test_publish_post_link_faults_raise_ambiguity_and_retain_target(tmp_path: Path) -> None:
    claim, expected = _build_valid_claim_fixtures()
    cid = expected["original_controller_run_id"]
    run_dir = tmp_path / cid
    target_file = run_dir / QUOTA_RESUME_CLAIM_FILENAME

    # 1. Post-link temp unlink failure: raises ambiguity error and retains target file
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch("os.unlink", side_effect=OSError("unlink temp failed")):
            with pytest.raises(ClaimStorageError, match="ambiguous post-link publication failure: cannot unlink"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert target_file.exists()
    assert target_file.read_bytes() == _dump_canonical_bytes(claim)
    target_file.unlink()
    for f in list(run_dir.iterdir()):
        f.unlink()

    # 2. Post-link dir fsync failure: raises ambiguity error and retains target file
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        real_fsync = os.fsync
        real_fstat = os.fstat
        def fail_dir_fsync(fd):
            st = real_fstat(fd)
            if stat.S_ISDIR(st.st_mode):
                raise OSError("dir fsync failed")
            return real_fsync(fd)
        with mock.patch("os.fsync", side_effect=fail_dir_fsync):
            with pytest.raises(ClaimStorageError, match="ambiguous post-link publication failure: cannot fsync directory"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert target_file.exists()
    assert target_file.read_bytes() == _dump_canonical_bytes(claim)
    target_file.unlink()
    for f in list(run_dir.iterdir()):
        f.unlink()

    # 3. Post-link verify_identity failure: raises ambiguity error and retains target file
    verify_count = 0
    def fail_verify_after():
        nonlocal verify_count
        verify_count += 1
        if verify_count > 1:
            raise BrokerError("post-write identity invalid")

    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=fail_verify_after)):
        with pytest.raises(ClaimStorageError, match="ambiguous post-link publication failure: directory identity verification failed after write"):
            publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert target_file.exists()
    assert target_file.read_bytes() == _dump_canonical_bytes(claim)
    target_file.unlink()
    for f in list(run_dir.iterdir()):
        f.unlink()

    # 4. Post-link readback failure: raises ambiguity error and retains target file
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch.object(storage_mod, "read_known_reset_resume_claim", side_effect=ClaimStorageError("readback corrupted")):
            with pytest.raises(ClaimStorageError, match="ambiguous post-link publication failure: readback failed"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert target_file.exists()
    assert target_file.read_bytes() == _dump_canonical_bytes(claim)
    target_file.unlink()
    for f in list(run_dir.iterdir()):
        f.unlink()

    # 5. Post-link readback type-sensitive mismatch: raises ambiguity error and retains target file
    mismatched_claim = copy.deepcopy(claim)
    mismatched_claim["schema_version"] = "1.0.0"
    with mock.patch.object(storage_mod, "_open_existing_cycle_run_directory", side_effect=lambda r, c: _mock_open_cycle_dir(run_dir)):
        with mock.patch.object(storage_mod, "read_known_reset_resume_claim", return_value=mismatched_claim):
            with pytest.raises(ClaimStorageError, match="ambiguous post-link publication failure: readback claim mismatch"):
                publish_known_reset_resume_claim(tmp_path, cid, claim, expected)
    assert target_file.exists()
    assert target_file.read_bytes() == _dump_canonical_bytes(claim)


def test_publish_no_provider_no_wake() -> None:
    # Verify storage module has no wake, Provider, or lock acquisition authority
    assert not hasattr(storage_mod, "wake")
    assert not hasattr(storage_mod, "acquire_lock")
    assert not hasattr(storage_mod, "acquire_provider")
    assert "publish_known_reset_resume_claim" in storage_mod.__all__
    assert "read_known_reset_resume_claim" in storage_mod.__all__
    assert len(storage_mod.__all__) == 5
