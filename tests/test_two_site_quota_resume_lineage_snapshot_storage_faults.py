"""Hermetic publisher fault and BaseException tests for scripts/two_site_quota_resume_lineage_snapshot_storage.py."""

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

    install_p1_00_network_fence()
except ImportError:
    try:
        from test_p1_00_characterization_network_isolation import (
            install_p1_00_network_fence,
        )

        install_p1_00_network_fence()
    except ImportError:
        pass

from scripts.two_site_provider_broker import BrokerError
from scripts.two_site_quota_resume_lineage_snapshot_record import (
    LineageSnapshotRecordError,
    build_expected_lineage_snapshot,
    canonical_json_bytes,
)
import scripts.two_site_quota_resume_lineage_snapshot_storage as storage_mod
from scripts.two_site_quota_resume_lineage_snapshot_storage import (
    MAX_SNAPSHOT_RECORD_BYTES,
    QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME,
    SnapshotStorageError,
    publish_expected_lineage_snapshot,
    read_expected_lineage_snapshot,
)
from tests.test_two_site_quota_resume_lineage_snapshot_record import (
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


def test_publish_argument_pre_io_validation(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]

    for args, match_str in [
        (("invalid_path", cid, wait_rec, exp_wait, claim_rec, exp_claim, req), "root must be a Path"),
        ((tmp_path, 12345, wait_rec, exp_wait, claim_rec, exp_claim, req), "original_controller_run_id must be exact str"),
        ((tmp_path, "", wait_rec, exp_wait, claim_rec, exp_claim, req), "original_controller_run_id must be a non-empty string"),
        ((tmp_path, cid, [], exp_wait, claim_rec, exp_claim, req), "wait_record must be exact dict"),
        ((tmp_path, cid, wait_rec, [], claim_rec, exp_claim, req), "expected_wait must be exact dict"),
        ((tmp_path, cid, wait_rec, exp_wait, [], exp_claim, req), "claim_record must be exact dict"),
        ((tmp_path, cid, wait_rec, exp_wait, claim_rec, [], req), "expected_claim must be exact dict"),
        ((tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, []), "request must be exact dict"),
    ]:
        with pytest.raises(SnapshotStorageError, match=match_str):
            publish_expected_lineage_snapshot(*args)

    with mock.patch.object(
        storage_mod,
        "build_expected_lineage_snapshot",
        side_effect=LineageSnapshotRecordError("invalid schema"),
    ):
        with pytest.raises(
            SnapshotStorageError, match="lineage snapshot construction failed"
        ):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )

    fake_snap = {"original_controller_run_id": "cycle-0000-20260901T000000Z-999"}
    with mock.patch.object(
        storage_mod, "build_expected_lineage_snapshot", return_value=fake_snap
    ):
        with pytest.raises(SnapshotStorageError, match="does not match snapshot"):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )

    valid_snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    with mock.patch.object(
        storage_mod, "build_expected_lineage_snapshot", return_value=valid_snap
    ):
        with mock.patch.object(
            storage_mod,
            "canonical_json_bytes",
            side_effect=LineageSnapshotRecordError("encoding cap exceeded"),
        ):
            with pytest.raises(
                SnapshotStorageError, match="lineage snapshot serialization failed"
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )

    with mock.patch.object(
        storage_mod, "build_expected_lineage_snapshot", return_value=valid_snap
    ):
        with mock.patch.object(
            storage_mod,
            "canonical_json_bytes",
            side_effect=ValueError("bad value"),
        ):
            with pytest.raises(
                SnapshotStorageError,
                match="lineage snapshot cannot be serialized to canonical JSON",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )

    with mock.patch.object(
        storage_mod, "build_expected_lineage_snapshot", return_value=valid_snap
    ):
        with mock.patch.object(storage_mod, "canonical_json_bytes", return_value=b""):
            with pytest.raises(
                SnapshotStorageError, match="lineage snapshot is empty"
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )

    with mock.patch.object(
        storage_mod, "build_expected_lineage_snapshot", return_value=valid_snap
    ):
        with mock.patch.object(
            storage_mod,
            "canonical_json_bytes",
            return_value=b"x" * (MAX_SNAPSHOT_RECORD_BYTES + 1),
        ):
            with pytest.raises(
                SnapshotStorageError, match="lineage snapshot exceeds byte limit"
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )

    assert list(tmp_path.iterdir()) == []


def test_publish_target_already_exists_and_directory_identity(
    tmp_path: Path,
) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    target_file.write_bytes(b"existing-untouched")
    os.chmod(target_file, 0o600)
    st_before = os.stat(target_file)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match=f"{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME} already exists",
        ):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )

    assert target_file.read_bytes() == b"existing-untouched"
    st_after = os.stat(target_file)
    assert st_before.st_ino == st_after.st_ino
    assert st_before.st_mtime_ns == st_after.st_mtime_ns
    target_file.unlink()

    target_file.symlink_to(run_dir / "other.json")
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match=f"{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME} already exists",
        ):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )
    assert target_file.is_symlink()
    target_file.unlink()

    def fail_verify_before():
        raise BrokerError("identity invalid before write")

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(
            run_dir, verify_callback=fail_verify_before
        ),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match="directory identity verification failed before write",
        ):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )

    def raise_broker(r, c):
        raise BrokerError("cycle directory missing")

    with mock.patch.object(
        storage_mod, "_open_existing_cycle_run_directory", side_effect=raise_broker
    ):
        with pytest.raises(
            SnapshotStorageError, match="cannot safely open cycle run directory"
        ):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )


def test_publish_temp_file_creation_and_write_faults(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)

    real_open = os.open
    real_fstat = os.fstat
    real_fsync = os.fsync
    real_close = os.close

    def call_pub():
        publish_expected_lineage_snapshot(
            tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
        )

    def assert_clean():
        call_pub()
        assert [
            f for f in os.listdir(run_dir)
            if f.startswith(f".{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}.tmp")
        ] == []

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        def fail_temp_open(path, flags, *args, **kwargs):
            if str(path).startswith(f".{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}.tmp"):
                raise OSError("open error")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch("os.open", side_effect=fail_temp_open):
            with pytest.raises(SnapshotStorageError, match="cannot safely create temp file"):
                assert_clean()

        with mock.patch("os.write", side_effect=OSError("write disk error")):
            with pytest.raises(SnapshotStorageError, match="cannot write"):
                assert_clean()

        with mock.patch("os.write", return_value=0):
            with pytest.raises(SnapshotStorageError, match="write stalled"):
                assert_clean()

        with mock.patch("os.fchmod", side_effect=OSError("fchmod error")):
            with pytest.raises(SnapshotStorageError, match="cannot fchmod"):
                assert_clean()

        def fail_fstat_reg(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                raise OSError("fstat error")
            return st

        with mock.patch("os.fstat", side_effect=fail_fstat_reg):
            with pytest.raises(SnapshotStorageError, match="cannot fstat"):
                assert_clean()

        def size_mismatch_fstat(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                return ConcreteStat(st_size=st.st_size + 10)
            return st

        with mock.patch("os.fstat", side_effect=size_mismatch_fstat):
            with pytest.raises(SnapshotStorageError, match="size mismatch"):
                assert_clean()

        def fail_fsync_file(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                raise OSError("fsync file error")
            return real_fsync(fd)

        with mock.patch("os.fsync", side_effect=fail_fsync_file):
            with pytest.raises(SnapshotStorageError, match="cannot fsync"):
                assert_clean()

        def fail_file_close(fd):
            try:
                st = real_fstat(fd)
                is_reg = stat.S_ISREG(st.st_mode)
            except OSError:
                is_reg = False
            real_close(fd)
            if is_reg:
                raise OSError("close file error")

        with mock.patch("os.close", side_effect=fail_file_close):
            with pytest.raises(SnapshotStorageError, match="cannot safely close temp file"):
                assert_clean()


def test_publish_link_failures_and_cleanup_failure(tmp_path: Path) -> None:
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
        with mock.patch("os.link", side_effect=FileExistsError("link exists")):
            with pytest.raises(
                SnapshotStorageError,
                match=f"cannot link {QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}: target already exists",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
    assert not target_file.exists()
    assert [
        f for f in os.listdir(run_dir)
        if f.startswith(f".{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}.tmp")
    ] == []

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.link", side_effect=OSError("link generic error")):
            with pytest.raises(
                SnapshotStorageError,
                match=f"cannot link {QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}: link generic error",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
    assert not target_file.exists()
    assert [
        f for f in os.listdir(run_dir)
        if f.startswith(f".{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}.tmp")
    ] == []

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.write", side_effect=OSError("initial write failure")):
            with mock.patch("os.unlink", side_effect=OSError("unlink cleanup failure")):
                with pytest.raises(
                    SnapshotStorageError, match="failed to clean up temp file"
                ):
                    publish_expected_lineage_snapshot(
                        tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                    )

    assert not target_file.exists()
    remaining_temps = [
        f for f in os.listdir(run_dir)
        if f.startswith(f".{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}.tmp")
    ]
    assert len(remaining_temps) == 1


def test_publish_post_link_faults_ambiguity_and_retention(
    tmp_path: Path,
) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    expected_bytes = canonical_json_bytes(snap)

    def reset_dir():
        run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        for p in list(run_dir.iterdir()):
            if p.is_file() or p.is_symlink():
                p.unlink()

    reset_dir()
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.unlink", side_effect=OSError("unlink temp failed")):
            with pytest.raises(
                SnapshotStorageError,
                match="ambiguous post-link publication failure: cannot unlink",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
    assert target_file.exists()
    assert target_file.read_bytes() == expected_bytes

    reset_dir()
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        real_fsync = os.fsync
        real_fstat = os.fstat

        def fail_dir_fsync(fd):
            st = real_fstat(fd)
            if stat.S_ISDIR(st.st_mode):
                raise OSError("dir fsync failed")
            return real_fsync(fd)

        with mock.patch("os.fsync", side_effect=fail_dir_fsync):
            with pytest.raises(
                SnapshotStorageError,
                match="ambiguous post-link publication failure: cannot fsync directory",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
    assert target_file.exists()
    assert target_file.read_bytes() == expected_bytes

    reset_dir()
    verify_count = 0

    def fail_verify_after():
        nonlocal verify_count
        verify_count += 1
        if verify_count > 1:
            raise BrokerError("identity corrupted post-link")

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(
            run_dir, verify_callback=fail_verify_after
        ),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match="ambiguous post-link publication failure: directory identity verification failed after write",
        ):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )
    assert target_file.exists()
    assert target_file.read_bytes() == expected_bytes

    reset_dir()
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch.object(
            storage_mod,
            "read_expected_lineage_snapshot",
            side_effect=SnapshotStorageError("readback corrupted"),
        ):
            with pytest.raises(
                SnapshotStorageError,
                match="ambiguous post-link publication failure: readback failed",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
    assert target_file.exists()
    assert target_file.read_bytes() == expected_bytes

    reset_dir()
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch.object(
            storage_mod,
            "read_expected_lineage_snapshot",
            side_effect=RuntimeError("unexpected readback crash"),
        ):
            with pytest.raises(
                SnapshotStorageError,
                match="ambiguous post-link publication failure: readback unexpected error",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
    assert target_file.exists()
    assert target_file.read_bytes() == expected_bytes

    reset_dir()
    mismatched = copy.deepcopy(snap)
    mismatched["schema_version"] = "1.0.0"
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch.object(
            storage_mod, "read_expected_lineage_snapshot", return_value=mismatched
        ):
            with pytest.raises(
                SnapshotStorageError,
                match="ambiguous post-link publication failure: readback snapshot mismatch",
            ):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
    assert target_file.exists()
    assert target_file.read_bytes() == expected_bytes


def test_base_exception_reachability(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    target_file.write_bytes(canonical_json_bytes(snap))
    os.chmod(target_file, 0o600)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch.object(
            storage_mod, "unique_json", side_effect=KeyboardInterrupt()
        ):
            with pytest.raises(KeyboardInterrupt):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch.object(
            storage_mod,
            "validate_expected_lineage_snapshot",
            side_effect=SystemExit(2),
        ):
            with pytest.raises(SystemExit):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    with mock.patch.object(
        storage_mod,
        "build_expected_lineage_snapshot",
        side_effect=KeyboardInterrupt(),
    ):
        with pytest.raises(KeyboardInterrupt):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )

    with mock.patch.object(
        storage_mod, "canonical_json_bytes", side_effect=SystemExit(3)
    ):
        with pytest.raises(SystemExit):
            publish_expected_lineage_snapshot(
                tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
            )

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch.object(
            storage_mod,
            "read_expected_lineage_snapshot",
            side_effect=KeyboardInterrupt(),
        ):
            target_file.unlink(missing_ok=True)
            with pytest.raises(KeyboardInterrupt):
                publish_expected_lineage_snapshot(
                    tmp_path, cid, wait_rec, exp_wait, claim_rec, exp_claim, req
                )
