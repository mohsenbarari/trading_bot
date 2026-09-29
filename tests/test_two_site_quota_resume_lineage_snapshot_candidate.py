"""Hermetic fault matrix and semantic detachment tests for untrusted candidate lineage snapshot reading."""

from __future__ import annotations

import contextlib
import copy
import os
from pathlib import Path
import stat
from typing import Any
from unittest import mock
import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
try:
    from tests.test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )
except ImportError:
    from test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )

install_p1_00_network_fence()

from scripts.two_site_provider_broker import BrokerError
from scripts.two_site_quota_resume_lineage_snapshot_record import (
    build_expected_lineage_snapshot,
    canonical_json_bytes,
)
import scripts.two_site_quota_resume_lineage_snapshot_storage as storage_mod
from scripts.two_site_quota_resume_lineage_snapshot_storage import (
    MAX_SNAPSHOT_RECORD_BYTES,
    QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME,
    SnapshotStorageError,
    read_expected_lineage_snapshot,
    read_untrusted_expected_lineage_snapshot_candidate,
)
try:
    from tests.test_two_site_quota_resume_lineage_snapshot_record import (
        _build_valid_lineage_fixtures,
    )
except ImportError:
    from test_two_site_quota_resume_lineage_snapshot_record import (
        _build_valid_lineage_fixtures,
    )


class ConcreteStat:
    """Concrete stat object with strict integer fields avoiding MagicMock stat TypeError."""

    def __init__(
        self,
        st_mode: int = 0o100600,
        st_uid: int | None = None,
        st_nlink: int = 1,
        st_size: int = 100,
        st_dev: int = 1,
        st_ino: int = 1,
        st_mtime_ns: int = 1000,
        st_ctime_ns: int = 1000,
    ):
        self.st_mode = int(st_mode)
        self.st_uid = os.geteuid() if st_uid is None else int(st_uid)
        self.st_nlink = int(st_nlink)
        self.st_size = int(st_size)
        self.st_dev = int(st_dev)
        self.st_ino = int(st_ino)
        self.st_mtime_ns = int(st_mtime_ns)
        self.st_ctime_ns = int(st_ctime_ns)


@contextlib.contextmanager
def _mock_open_cycle_dir(run_path: Path, verify_callback: Any = None):
    fd = os.open(
        str(run_path),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        def verify():
            if verify_callback is not None:
                verify_callback()

        yield run_path, fd, verify
    finally:
        os.close(fd)


def test_untrusted_candidate_reader_argument_validation(tmp_path: Path) -> None:
    for args, match_str in [
        (("not_path", "run_id"), "root must be a Path"),
        ((tmp_path, 12345), "original_controller_run_id must be exact str"),
        ((tmp_path, ""), "original_controller_run_id must be a non-empty string"),
    ]:
        with pytest.raises(SnapshotStorageError, match=match_str):
            read_untrusted_expected_lineage_snapshot_candidate(*args)


def test_candidate_missing_symlink_hardlink_faults(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match=f"cannot lstat {QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}",
        ):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

        snap = build_expected_lineage_snapshot(wait_rec, exp_wait, claim_rec, exp_claim, req)
        real_file = tmp_path / "real_snap.json"
        real_file.write_bytes(canonical_json_bytes(snap))
        os.chmod(real_file, 0o600)

        target_file.symlink_to(real_file)
        with pytest.raises(SnapshotStorageError, match="is not a regular file"):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)
        target_file.unlink()

        target_file.write_bytes(canonical_json_bytes(snap))
        os.chmod(target_file, 0o600)
        hardlink = run_dir / "hardlink_snap.json"
        os.link(target_file, hardlink)
        with pytest.raises(SnapshotStorageError, match="has invalid link count"):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)
        hardlink.unlink()


def test_candidate_mode_and_owner_faults(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    snap = build_expected_lineage_snapshot(wait_rec, exp_wait, claim_rec, exp_claim, req)
    raw_bytes = canonical_json_bytes(snap)
    target_file.write_bytes(raw_bytes)

    os.chmod(target_file, 0o644)
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(SnapshotStorageError, match="mode must be 0600"):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)
        os.chmod(target_file, 0o600)

        # Unprivileged test environment cannot execute real chown; verified via deterministic mock stat.
        with mock.patch(
            "os.lstat",
            return_value=ConcreteStat(
                st_uid=os.geteuid() + 1000,
                st_size=len(raw_bytes),
            ),
        ):
            with pytest.raises(SnapshotStorageError, match="owner mismatch"):
                read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)


def test_candidate_empty_and_oversized_faults(tmp_path: Path) -> None:
    wait_rec, _, _, _, _ = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        target_file.write_bytes(b"")
        os.chmod(target_file, 0o600)
        with pytest.raises(SnapshotStorageError, match="is empty"):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

        target_file.write_bytes(b"x" * (MAX_SNAPSHOT_RECORD_BYTES + 1))
        os.chmod(target_file, 0o600)
        with pytest.raises(SnapshotStorageError, match="exceeds byte limit"):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)


@pytest.mark.parametrize(
    ("raw_payload", "err_match"),
    [
        (b"\xff\xfe\xfd", "is not valid UTF-8"),
        (b"plain non-json string", "is not valid JSON"),
        (b'{"schema_version":"1.0","schema_version":"1.0"}', "is not valid JSON"),
        (b'["not", "a", "json", "object"]', "must be a JSON object"),
        (b'{"schema_version": "1.0"}', "is not canonical JSON bytes"),
    ],
)
def test_candidate_encoding_and_json_safety_faults(
    tmp_path: Path,
    raw_payload: bytes,
    err_match: str,
) -> None:
    wait_rec, _, _, _, _ = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    target_file.write_bytes(raw_payload)
    os.chmod(target_file, 0o600)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(SnapshotStorageError, match=err_match):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)


def test_candidate_metadata_divergence_and_identity_faults(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    snap = build_expected_lineage_snapshot(wait_rec, exp_wait, claim_rec, exp_claim, req)
    raw_bytes = canonical_json_bytes(snap)
    target_file.write_bytes(raw_bytes)
    os.chmod(target_file, 0o600)

    real_stat, real_fstat, real_lstat = os.stat, os.fstat, os.lstat

    def fake_stat_dirfd_mismatch(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            return ConcreteStat(st_ino=999, st_size=len(raw_bytes))
        return real_stat(path, *args, **kwargs)

    def fake_fstat_before(fd):
        st = real_fstat(fd)
        return ConcreteStat(st_ino=888, st_size=len(raw_bytes)) if stat.S_ISREG(st.st_mode) else st

    fstat_count = 0

    def fake_fstat_after(fd):
        nonlocal fstat_count
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            fstat_count += 1
            if fstat_count > 1:
                return ConcreteStat(st_ino=777, st_size=len(raw_bytes))
        return st

    stat_count = 0

    def fake_stat_after(path, *args, **kwargs):
        nonlocal stat_count
        st = real_stat(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            stat_count += 1
            if stat_count > 1:
                return ConcreteStat(st_ino=666, st_size=len(raw_bytes))
        return st

    lstat_count = 0

    def fake_lstat_after(path, *args, **kwargs):
        nonlocal lstat_count
        st = real_lstat(path, *args, **kwargs)
        lstat_count += 1
        if lstat_count > 1:
            return ConcreteStat(st_ino=555, st_size=len(raw_bytes))
        return st

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.stat", side_effect=fake_stat_dirfd_mismatch):
            with pytest.raises(SnapshotStorageError, match="dir_fd stat mismatch before open"):
                read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

        with mock.patch("os.fstat", side_effect=fake_fstat_before):
            with pytest.raises(
                SnapshotStorageError, match="descriptor metadata mismatch before read"
            ):
                read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

        with mock.patch("os.fstat", side_effect=fake_fstat_after):
            with pytest.raises(
                SnapshotStorageError, match="descriptor metadata changed after read"
            ):
                read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

        with mock.patch("os.stat", side_effect=fake_stat_after):
            with pytest.raises(
                SnapshotStorageError, match="dir_fd metadata changed after read"
            ):
                read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

        with mock.patch("os.lstat", side_effect=fake_lstat_after):
            with pytest.raises(
                SnapshotStorageError, match="path metadata changed after read"
            ):
                read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

    def fail_verify_before():
        raise BrokerError("directory identity verification failed before read")

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(
            run_dir, verify_callback=fail_verify_before
        ),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match="directory identity verification failed before read",
        ):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

    verify_count = 0

    def fail_verify_after():
        nonlocal verify_count
        verify_count += 1
        if verify_count > 1:
            raise BrokerError("directory identity verification failed after read")

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(
            run_dir, verify_callback=fail_verify_after
        ),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match="directory identity verification failed after read",
        ):
            read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)


def test_candidate_descriptor_closure_on_failure(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    snap = build_expected_lineage_snapshot(wait_rec, exp_wait, claim_rec, exp_claim, req)
    target_file.write_bytes(canonical_json_bytes(snap))
    os.chmod(target_file, 0o600)

    real_close = os.close
    closed_fds: list[int] = []

    def track_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def fail_read(fd: int, n: int) -> bytes:
        raise OSError("simulated candidate read failure")

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.read", side_effect=fail_read):
            with mock.patch("os.close", side_effect=track_close):
                with pytest.raises(
                    SnapshotStorageError,
                    match=f"cannot read {QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}",
                ):
                    read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

    assert len(closed_fds) >= 1


def test_untrusted_candidate_semantic_invalid_contrast_and_immutability(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    snap = build_expected_lineage_snapshot(wait_rec, exp_wait, claim_rec, exp_claim, req)
    tampered = copy.deepcopy(snap)
    tampered["claim_sha256"] = "0" * 64
    tampered_bytes = canonical_json_bytes(tampered)
    target_file.write_bytes(tampered_bytes)
    os.chmod(target_file, 0o600)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match="expected lineage snapshot validation failed",
        ):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

        candidate_1 = read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)
        assert candidate_1["claim_sha256"] == "0" * 64
        assert candidate_1 == tampered
        assert candidate_1 is not tampered

        candidate_1["claim_sha256"] = "mutated_value"
        candidate_1["extra_key"] = "forbidden"

        assert target_file.read_bytes() == tampered_bytes

        candidate_2 = read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)
        assert candidate_2["claim_sha256"] == "0" * 64
        assert "extra_key" not in candidate_2


def test_candidate_no_authority_or_live_side_effects(tmp_path: Path) -> None:
    forbidden_names = ["wake", "ack", "replay", "execute", "acquire_lock", "acquire_provider"]
    for name in forbidden_names:
        assert not hasattr(storage_mod, name)

    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    snap = build_expected_lineage_snapshot(wait_rec, exp_wait, claim_rec, exp_claim, req)
    target_file.write_bytes(canonical_json_bytes(snap))
    os.chmod(target_file, 0o600)

    dir_items_before = sorted(os.listdir(run_dir))
    stat_before = os.stat(target_file)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        candidate = read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)

    assert candidate["schema_version"] == "1.0"
    assert sorted(os.listdir(run_dir)) == dir_items_before
    stat_after = os.stat(target_file)
    assert stat_before.st_ino == stat_after.st_ino
    assert stat_before.st_mtime_ns == stat_after.st_mtime_ns
