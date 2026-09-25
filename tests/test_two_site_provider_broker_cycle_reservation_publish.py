"""Hermetic fault matrix and contract tests for publish_cycle_reservation."""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

try:
    from scripts.two_site_preparation_contract import (
        PreparationStartup,
    )
    from scripts.two_site_provider_broker import (
        BrokerError,
        publish_cycle_reservation,
    )
    import scripts.two_site_provider_broker as broker_mod
except ModuleNotFoundError:
    from two_site_preparation_contract import (  # type: ignore[no-redef]
        PreparationStartup,
    )
    from two_site_provider_broker import (  # type: ignore[no-redef]
        BrokerError,
        publish_cycle_reservation,
    )
    import two_site_provider_broker as broker_mod  # type: ignore[no-redef]


CYCLE_ID = "cycle-2026-20260925T010000Z-1"
SOURCE_HEAD = "a" * 40
ASSIGNMENT_SHA = "b" * 64
TASK_SHA = "c" * 64
REQUEST_ID = "1" * 32
CREATED_AT = 1700000000.5


def _setup_cycle_root(tmp_dir: str, cycle_id: str = CYCLE_ID) -> tuple[Path, Path]:
    root = Path(tmp_dir).resolve(strict=True)
    os.chmod(root, 0o700)
    runs_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / cycle_id
    runs_dir.mkdir(parents=True, mode=0o700)

    curr = root
    for comp in (".local", "two-site-refactor", "autopilot", "runs", cycle_id):
        curr = curr / comp
        os.chmod(curr, 0o700)

    return root, runs_dir


def _write_startup_json(
    runs_dir: Path,
    *,
    schema_version: Any = 1,
    route: str = "one_shot",
    cycle_id: str = CYCLE_ID,
    source_head: str = SOURCE_HEAD,
    assignment_sha256: str = ASSIGNMENT_SHA,
    raw_dict: dict[str, Any] | None = None,
    mode: int = 0o600,
) -> Path:
    startup_path = runs_dir / "preparation-startup.json"
    if raw_dict is not None:
        payload = raw_dict
    else:
        startup = PreparationStartup(
            schema_version=schema_version,
            route=route,
            cycle_id=cycle_id,
            source_head=source_head,
            assignment_sha256=assignment_sha256,
        )
        payload = startup.to_mapping()

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    startup_path.write_bytes(encoded)
    os.chmod(startup_path, mode)
    return startup_path


def _build_request(
    *,
    schema_version: str = "1.0",
    request_id: str = REQUEST_ID,
    task_path: str = ".local/two-site-refactor/tasks/fixture.json",
    task_sha256: str = TASK_SHA,
    run_prefix: str = "run-prefix-1",
    timeout: int = 120,
    source_head: str = SOURCE_HEAD,
    assignment_id: str = "assignment-1",
    assignment_sha256: str = ASSIGNMENT_SHA,
    controller_run_id: str = CYCLE_ID,
    created_at: float = CREATED_AT,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "request_id": request_id,
        "task_path": task_path,
        "task_sha256": task_sha256,
        "run_prefix": run_prefix,
        "timeout": timeout,
        "source_head": source_head,
        "assignment_id": assignment_id,
        "assignment_sha256": assignment_sha256,
        "controller_run_id": controller_run_id,
        "created_at": created_at,
    }


def test_publish_cycle_reservation_success_and_duplicate() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        request = _build_request()

        record = publish_cycle_reservation(root, request)

        expected_reservation = {
            "controller_run_id": request["controller_run_id"],
            "run_prefix": request["run_prefix"],
            "task_path": request["task_path"],
            "task_sha256": request["task_sha256"],
            "source_head": request["source_head"],
            "assignment_id": request["assignment_id"],
            "assignment_sha256": request["assignment_sha256"],
        }
        for v in expected_reservation.values():
            assert isinstance(v, str)
        assert len(expected_reservation) == 7

        canonical_req = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        expected_req_sha = hashlib.sha256(canonical_req).hexdigest()

        assert set(record.keys()) == {
            "schema_version",
            "controller_run_id",
            "request_id",
            "reservation",
            "request_sha256",
            "created_at",
        }
        assert record["schema_version"] == "1.0"
        assert record["controller_run_id"] == request["controller_run_id"]
        assert record["request_id"] == request["request_id"]
        assert record["reservation"] == expected_reservation
        assert record["request_sha256"] == expected_req_sha
        assert record["created_at"] == request["created_at"]

        target_file = runs_dir / "broker-reservation.json"
        assert target_file.is_file()
        assert not target_file.is_symlink()

        st = os.lstat(target_file)
        assert stat.S_ISREG(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert st.st_nlink == 1
        assert st.st_size <= 4096

        expected_record_bytes = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert target_file.read_bytes() == expected_record_bytes
        assert st.st_size == len(expected_record_bytes)

        queue_dir = root / ".local" / "two-site-refactor" / "provider-broker"
        assert not queue_dir.exists()

        with pytest.raises(BrokerError, match="broker reservation target already exists"):
            publish_cycle_reservation(root, request)

        st_after = os.lstat(target_file)
        assert st_after.st_ino == st.st_ino
        assert st_after.st_dev == st.st_dev
        assert st_after.st_size == st.st_size
        assert target_file.read_bytes() == expected_record_bytes
        assert not queue_dir.exists()


def test_publish_cycle_reservation_startup_and_request_failures() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        request = _build_request()
        with pytest.raises(BrokerError):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        real_startup = _write_startup_json(runs_dir)
        target_outside = root / "outside-startup.json"
        real_startup.rename(target_outside)
        os.symlink(target_outside, runs_dir / "preparation-startup.json")
        request = _build_request()
        with pytest.raises(BrokerError):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir, source_head="0" * 40)
        request = _build_request()
        with pytest.raises(BrokerError, match="preparation startup source HEAD does not match request"):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir, assignment_sha256="0" * 64)
        request = _build_request()
        with pytest.raises(BrokerError, match="preparation startup assignment SHA does not match request"):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        other_cycle = "cycle-2026-20260925T010000Z-99"
        _write_startup_json(runs_dir, cycle_id=other_cycle)
        request = _build_request()
        with pytest.raises(BrokerError, match="preparation startup cycle ID does not match controller run ID"):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        raw = {
            "schema_version": "1.0",
            "route": "one_shot",
            "cycle_id": CYCLE_ID,
            "source_head": SOURCE_HEAD,
            "assignment_sha256": ASSIGNMENT_SHA,
        }
        _write_startup_json(runs_dir, raw_dict=raw)
        request = _build_request()
        with pytest.raises(BrokerError, match="invalid preparation startup"):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        maint_id = "maint-20260925T010000Z-0123456789abcdef"
        request = _build_request(controller_run_id=maint_id)
        with pytest.raises(BrokerError, match="cycle reservation rejects maintenance run ID"):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        request = _build_request(created_at=float("nan"))
        with pytest.raises(BrokerError, match="claimed broker request created_at is invalid"):
            publish_cycle_reservation(root, request)
        assert not (runs_dir / "broker-reservation.json").exists()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()


def test_pre_link_file_fsync_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        request = _build_request()

        real_fsync = os.fsync
        fsync_called = False

        def failing_file_fsync(fd: int) -> None:
            nonlocal fsync_called
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                fsync_called = True
                raise OSError(errno.EIO, "simulated file fsync failure")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", failing_file_fsync)
        try:
            with pytest.raises(OSError, match="simulated file fsync failure"):
                publish_cycle_reservation(root, request)
        finally:
            monkeypatch.undo()

        assert fsync_called is True
        assert not (runs_dir / "broker-reservation.json").exists()
        assert list(runs_dir.glob(".*.tmp")) == []
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()


def test_pre_link_os_link_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        request = _build_request()

        link_called = False

        def failing_link(*args: Any, **kwargs: Any) -> None:
            nonlocal link_called
            link_called = True
            raise OSError(errno.EIO, "simulated link failure")

        monkeypatch.setattr(os, "link", failing_link)
        try:
            with pytest.raises(BrokerError, match="cannot safely link broker reservation"):
                publish_cycle_reservation(root, request)
        finally:
            monkeypatch.undo()

        assert link_called is True
        assert not (runs_dir / "broker-reservation.json").exists()
        assert list(runs_dir.glob(".*.tmp")) == []
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()


def test_post_link_dir_fsync_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        request = _build_request()

        real_fsync = os.fsync
        fsync_called = False

        def failing_dir_fsync(fd: int) -> None:
            nonlocal fsync_called
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode):
                fsync_called = True
                raise OSError(errno.EIO, "simulated dir fsync failure")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", failing_dir_fsync)
        try:
            with pytest.raises(BrokerError, match="cannot fsync run directory"):
                publish_cycle_reservation(root, request)
        finally:
            monkeypatch.undo()

        assert fsync_called is True
        target_file = runs_dir / "broker-reservation.json"
        assert target_file.is_file()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

        with pytest.raises(BrokerError, match="broker reservation target already exists"):
            publish_cycle_reservation(root, request)
        assert target_file.is_file()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()


def test_post_link_temp_unlink_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        request = _build_request()

        real_unlink = os.unlink
        unlink_called = False

        def failing_temp_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
            nonlocal unlink_called
            if isinstance(path, str) and path.startswith(".broker-reservation.json.") and path.endswith(".tmp"):
                unlink_called = True
                raise OSError(errno.EIO, "simulated unlink temp failure")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", failing_temp_unlink)
        try:
            with pytest.raises(BrokerError, match="cannot safely unlink temporary reservation file"):
                publish_cycle_reservation(root, request)
        finally:
            monkeypatch.undo()

        assert unlink_called is True
        target_file = runs_dir / "broker-reservation.json"
        assert target_file.is_file()
        assert len(list(runs_dir.glob(".*.tmp"))) == 1
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()

        with pytest.raises(BrokerError, match="broker reservation target already exists"):
            publish_cycle_reservation(root, request)
        assert target_file.is_file()
        assert not (root / ".local" / "two-site-refactor" / "provider-broker").exists()


def test_replace_pinned_ancestor_blocks_verify_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        request = _build_request()

        runs_parent = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
        renamed_runs = runs_parent.with_name("runs.orig")

        orig_read = broker_mod._read_cycle_private_json
        intercepted = False

        def intercept_readback(
            run_path: Path, run_fd: int, filename: str
        ) -> tuple[dict[str, Any], bytes]:
            nonlocal intercepted
            payload, raw_bytes = orig_read(run_path, run_fd, filename)
            if filename == "broker-reservation.json":
                intercepted = True
                os.rename(runs_parent, renamed_runs)
                os.mkdir(runs_parent, 0o700)
            return payload, raw_bytes

        monkeypatch.setattr(broker_mod, "_read_cycle_private_json", intercept_readback)

        try:
            with pytest.raises(BrokerError) as exc_info:
                publish_cycle_reservation(root, request)
            err_msg = str(exc_info.value)
            assert "directory" in err_msg and (
                "identity changed" in err_msg or "does not match path" in err_msg
            )
            assert intercepted is True

            original_target = renamed_runs / CYCLE_ID / "broker-reservation.json"
            assert original_target.is_file()
            assert not original_target.is_symlink()
            st = os.lstat(original_target)
            assert stat.S_IMODE(st.st_mode) == 0o600
            assert st.st_nlink == 1
            assert st.st_size <= 4096
        finally:
            monkeypatch.undo()
            if runs_parent.exists() and renamed_runs.exists():
                os.rmdir(runs_parent)
                os.rename(renamed_runs, runs_parent)
