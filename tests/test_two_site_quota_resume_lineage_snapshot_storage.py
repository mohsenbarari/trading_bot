"""Hermetic positive and reader rejection tests for scripts/two_site_quota_resume_lineage_snapshot_storage.py."""

from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import json
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

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
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
    publish_expected_lineage_snapshot,
    read_expected_lineage_snapshot,
    read_untrusted_expected_lineage_snapshot_candidate,
)
from scripts.two_site_quota_wait_sources import (
    collect_initial_wait_source_expected,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_resume_lineage_snapshot_record import (
    _build_valid_lineage_fixtures,
)
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
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


def test_two_site_quota_resume_lineage_snapshot_storage_real_broker_cycle(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root: Path = ctx["root"]
    controller_run_id: str = ctx["controller_run_id"]
    sc: dict[str, Any] = ctx["sc"]
    wait_record: dict[str, Any] = ctx["wait_record"]
    claim: dict[str, Any] = ctx["claim"]
    expected_claim: dict[str, Any] = ctx["expected_claim"]
    resume_request: dict[str, Any] = ctx["resume_request"]
    runs_dir: Path = ctx["runs_dir"]
    wait_file: Path = ctx["wait_file"]
    claim_file: Path = ctx["claim_file"]

    wait_bytes_before = wait_file.read_bytes()
    wait_mtime_before = wait_file.stat().st_mtime_ns
    claim_bytes_before = claim_file.read_bytes()
    claim_mtime_before = claim_file.stat().st_mtime_ns

    wait_before = copy.deepcopy(wait_record)
    claim_before = copy.deepcopy(claim)
    expected_claim_before = copy.deepcopy(expected_claim)
    request_before = copy.deepcopy(resume_request)

    with exclusive_supervisor_lock(sc["lock_path"]):
        expected_wait = collect_initial_wait_source_expected(
            root,
            controller_run_id,
            sc["rescope_epoch"],
            1,
        )
        expected_wait_before = copy.deepcopy(expected_wait)

        published = publish_expected_lineage_snapshot(
            root,
            controller_run_id,
            wait_record,
            expected_wait,
            claim,
            expected_claim,
            resume_request,
        )
        readback = read_expected_lineage_snapshot(
            root,
            controller_run_id,
            wait_record,
            claim,
        )
        candidate = read_untrusted_expected_lineage_snapshot_candidate(
            root,
            controller_run_id,
        )

    assert published["schema_version"] == "1.0"
    assert published["original_controller_run_id"] == controller_run_id
    assert readback == published
    assert readback is not published
    readback["mutated"] = True
    assert "mutated" not in published
    assert candidate == published
    assert candidate is not published
    candidate["mutated_candidate"] = True
    assert "mutated_candidate" not in published

    target_file = runs_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    assert target_file.is_file()
    st = os.stat(target_file)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1
    assert st.st_uid == os.geteuid()

    persisted_bytes = target_file.read_bytes()
    canonical_bytes = canonical_json_bytes(published)
    assert persisted_bytes == canonical_bytes

    temp_files = [
        f
        for f in os.listdir(runs_dir)
        if f.startswith(f".{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}.tmp")
    ]
    assert temp_files == []

    assert wait_record == wait_before
    assert expected_wait == expected_wait_before
    assert claim == claim_before
    assert expected_claim == expected_claim_before
    assert resume_request == request_before

    with exclusive_supervisor_lock(sc["lock_path"]):
        with pytest.raises(
            SnapshotStorageError,
            match=f"{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME} already exists",
        ):
            publish_expected_lineage_snapshot(
                root,
                controller_run_id,
                wait_record,
                expected_wait,
                claim,
                expected_claim,
                resume_request,
            )

    assert target_file.read_bytes() == persisted_bytes
    st_after = os.stat(target_file)
    assert st_after.st_ino == st.st_ino
    assert st_after.st_mtime_ns == st.st_mtime_ns

    assert wait_file.read_bytes() == wait_bytes_before
    assert wait_file.stat().st_mtime_ns == wait_mtime_before
    assert claim_file.read_bytes() == claim_bytes_before
    assert claim_file.stat().st_mtime_ns == claim_mtime_before

    assert not hasattr(storage_mod, "wake")
    assert not hasattr(storage_mod, "execute")
    assert not hasattr(storage_mod, "acquire_lock")
    assert not hasattr(storage_mod, "acquire_provider")
    assert set(storage_mod.__all__) == {
        "MAX_SNAPSHOT_RECORD_BYTES",
        "QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME",
        "SnapshotStorageError",
        "publish_expected_lineage_snapshot",
        "read_expected_lineage_snapshot",
        "read_untrusted_expected_lineage_snapshot_candidate",
    }


def test_reader_argument_validation(tmp_path: Path) -> None:
    wait_rec, _, claim_rec, _, _ = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]

    for args, match_str in [
        (("not_path", cid, wait_rec, claim_rec), "root must be a Path"),
        ((tmp_path, 123, wait_rec, claim_rec), "original_controller_run_id must be exact str"),
        ((tmp_path, "", wait_rec, claim_rec), "original_controller_run_id must be a non-empty string"),
        ((tmp_path, cid, [], claim_rec), "wait_record must be exact dict"),
        ((tmp_path, cid, wait_rec, []), "claim_record must be exact dict"),
    ]:
        with pytest.raises(SnapshotStorageError, match=match_str):
            read_expected_lineage_snapshot(*args)

    bad_wait = copy.deepcopy(wait_rec)
    bad_wait["controller_run_id"] = "cycle-0000-20260901T000000Z-999"
    with pytest.raises(
        SnapshotStorageError,
        match="wait_record controller_run_id .* does not match",
    ):
        read_expected_lineage_snapshot(tmp_path, cid, bad_wait, claim_rec)

    bad_claim = copy.deepcopy(claim_rec)
    bad_claim["original_controller_run_id"] = "cycle-0000-20260901T000000Z-999"
    with pytest.raises(
        SnapshotStorageError,
        match="claim_record original_controller_run_id .* does not match",
    ):
        read_expected_lineage_snapshot(tmp_path, cid, wait_rec, bad_claim)


def test_reader_cycle_run_directory_broker_and_identity_failures(
    tmp_path: Path,
) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)

    snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    target_file.write_bytes(canonical_json_bytes(snap))
    os.chmod(target_file, 0o600)

    def raise_broker(r, c):
        raise BrokerError("cannot open run directory")

    with mock.patch.object(
        storage_mod, "_open_existing_cycle_run_directory", side_effect=raise_broker
    ):
        with pytest.raises(
            SnapshotStorageError, match="cannot safely open cycle run directory"
        ):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    def fail_verify_before():
        raise BrokerError("directory identity corrupted before read")

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
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    call_count = 0

    def fail_verify_after():
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise BrokerError("directory identity corrupted after read")

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
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)


def test_reader_file_metadata_and_descriptor_safety_rejections(
    tmp_path: Path,
) -> None:
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
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    real_file = tmp_path / "real_snapshot.json"
    real_file.write_bytes(canonical_json_bytes(snap))
    os.chmod(real_file, 0o600)
    target_file.symlink_to(real_file)
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(SnapshotStorageError, match="is not a regular file"):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)
    target_file.unlink()

    target_file.write_bytes(canonical_json_bytes(snap))
    os.chmod(target_file, 0o600)
    hardlink = run_dir / "hardlink.json"
    os.link(target_file, hardlink)
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(SnapshotStorageError, match="has invalid link count"):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)
    hardlink.unlink()

    os.chmod(target_file, 0o644)
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(SnapshotStorageError, match="mode must be 0600"):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)
    os.chmod(target_file, 0o600)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch(
            "os.lstat",
            return_value=ConcreteStat(
                st_uid=os.geteuid() + 1000, st_size=len(canonical_json_bytes(snap))
            ),
        ):
            with pytest.raises(SnapshotStorageError, match="owner mismatch"):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    target_file.write_bytes(b"")
    os.chmod(target_file, 0o600)
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(SnapshotStorageError, match="is empty"):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    target_file.write_bytes(b"x" * (MAX_SNAPSHOT_RECORD_BYTES + 1))
    os.chmod(target_file, 0o600)
    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(SnapshotStorageError, match="exceeds byte limit"):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)


def test_reader_metadata_divergence_checks(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    raw_bytes = canonical_json_bytes(snap)
    target_file.write_bytes(raw_bytes)
    os.chmod(target_file, 0o600)

    real_stat = os.stat

    def fake_stat_dirfd_mismatch(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            return ConcreteStat(st_ino=999, st_size=len(raw_bytes))
        return real_stat(path, *args, **kwargs)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.stat", side_effect=fake_stat_dirfd_mismatch):
            with pytest.raises(
                SnapshotStorageError, match="dir_fd stat mismatch before open"
            ):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    real_fstat = os.fstat

    def fake_fstat_before(fd):
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            return ConcreteStat(st_ino=888, st_size=len(raw_bytes))
        return st

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.fstat", side_effect=fake_fstat_before):
            with pytest.raises(
                SnapshotStorageError, match="descriptor metadata mismatch before read"
            ):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    fstat_count = 0

    def fake_fstat_after(fd):
        nonlocal fstat_count
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            fstat_count += 1
            if fstat_count > 1:
                return ConcreteStat(st_ino=777, st_size=len(raw_bytes))
        return st

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.fstat", side_effect=fake_fstat_after):
            with pytest.raises(
                SnapshotStorageError, match="descriptor metadata changed after read"
            ):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    stat_count = 0

    def fake_stat_after(path, *args, **kwargs):
        nonlocal stat_count
        st = real_stat(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            stat_count += 1
            if stat_count > 1:
                return ConcreteStat(st_ino=666, st_size=len(raw_bytes))
        return st

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.stat", side_effect=fake_stat_after):
            with pytest.raises(
                SnapshotStorageError, match="dir_fd metadata changed after read"
            ):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    real_lstat = os.lstat
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
        with mock.patch("os.lstat", side_effect=fake_lstat_after):
            with pytest.raises(
                SnapshotStorageError, match="path metadata changed after read"
            ):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)


@pytest.mark.parametrize(
    ("raw_payload", "err_match"),
    [
        (b"\xff\xfe\xfd", "is not valid UTF-8"),
        (b"not json content", "is not valid JSON"),
        (b'{"schema_version":"1.0","schema_version":"1.0"}', "is not valid JSON"),
        (b'["not", "a", "dict"]', "must be a JSON object"),
        (b'{"schema_version": "1.0"}', "is not canonical JSON bytes"),
    ],
)
def test_reader_content_and_encoding_safety_rejections(
    tmp_path: Path,
    raw_payload: bytes,
    err_match: str,
) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
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
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)


def test_reader_lineage_validation_and_cid_mismatch(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    tampered = copy.deepcopy(snap)
    tampered["claim_sha256"] = "0" * 64
    target_file.write_bytes(canonical_json_bytes(tampered))
    os.chmod(target_file, 0o600)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(
            SnapshotStorageError, match="expected lineage snapshot validation failed"
        ):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    other_cid = "cycle-0000-20260901T000000Z-999"
    other_run_dir = tmp_path / other_cid
    other_run_dir.mkdir(parents=True, mode=0o700)
    other_target = other_run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    other_target.write_bytes(canonical_json_bytes(snap))
    os.chmod(other_target, 0o600)

    other_wait = copy.deepcopy(wait_rec)
    other_wait["controller_run_id"] = other_cid
    other_claim = copy.deepcopy(claim_rec)
    other_claim["original_controller_run_id"] = other_cid

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(other_run_dir),
    ):
        with pytest.raises(
            SnapshotStorageError,
            match="expected lineage snapshot validation failed: wait_record validation failed: reservation controller_run_id does not match outer controller_run_id",
        ):
            read_expected_lineage_snapshot(tmp_path, other_cid, other_wait, other_claim)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch.object(
            storage_mod,
            "validate_expected_lineage_snapshot",
            return_value={"original_controller_run_id": other_cid},
        ):
            with pytest.raises(
                SnapshotStorageError,
                match="snapshot original_controller_run_id .* does not match",
            ):
                read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)


def test_reader_descriptor_closed_on_failure(tmp_path: Path) -> None:
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

    real_close = os.close
    closed_fds: list[int] = []

    def track_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def fail_read(fd: int, n: int) -> bytes:
        raise OSError("simulated low-level read error")

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
                    read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

    assert len(closed_fds) >= 1


def test_untrusted_candidate_reader_argument_validation(tmp_path: Path) -> None:
    for args, match_str in [
        (("not_path", "run_id"), "root must be a Path"),
        ((tmp_path, 123), "original_controller_run_id must be exact str"),
        ((tmp_path, ""), "original_controller_run_id must be a non-empty string"),
    ]:
        with pytest.raises(SnapshotStorageError, match=match_str):
            read_untrusted_expected_lineage_snapshot_candidate(*args)


def test_untrusted_candidate_reader_detached_without_semantic_validation(tmp_path: Path) -> None:
    wait_rec, exp_wait, claim_rec, exp_claim, req = _build_valid_lineage_fixtures()
    cid = wait_rec["controller_run_id"]
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target_file = run_dir / QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    snap = build_expected_lineage_snapshot(
        wait_rec, exp_wait, claim_rec, exp_claim, req
    )
    tampered = copy.deepcopy(snap)
    tampered["claim_sha256"] = "0" * 64
    target_file.write_bytes(canonical_json_bytes(tampered))
    os.chmod(target_file, 0o600)

    with mock.patch.object(
        storage_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(
            SnapshotStorageError, match="expected lineage snapshot validation failed"
        ):
            read_expected_lineage_snapshot(tmp_path, cid, wait_rec, claim_rec)

        candidate = read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)
        assert candidate["claim_sha256"] == "0" * 64
        candidate["mutated"] = True
        assert "mutated" not in tampered

        second = read_untrusted_expected_lineage_snapshot_candidate(tmp_path, cid)
        assert "mutated" not in second
