"""Hermetic contract tests for read_cycle_reservation top-level schema validation."""
from __future__ import annotations

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
        read_cycle_reservation,
    )
except ModuleNotFoundError:
    from two_site_preparation_contract import (  # type: ignore[no-redef]
        PreparationStartup,
    )
    from two_site_provider_broker import (  # type: ignore[no-redef]
        BrokerError,
        publish_cycle_reservation,
        read_cycle_reservation,
    )

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
    cycle_id: str = CYCLE_ID,
    source_head: str = SOURCE_HEAD,
    assignment_sha256: str = ASSIGNMENT_SHA,
) -> Path:
    startup = PreparationStartup(
        schema_version=1,
        route="one_shot",
        cycle_id=cycle_id,
        source_head=source_head,
        assignment_sha256=assignment_sha256,
    )
    payload = startup.to_mapping()
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    startup_path = runs_dir / "preparation-startup.json"
    startup_path.write_bytes(encoded)
    os.chmod(startup_path, 0o600)
    return startup_path


def _build_request(
    *,
    controller_run_id: str = CYCLE_ID,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "request_id": REQUEST_ID,
        "task_path": ".local/two-site-refactor/tasks/fixture.json",
        "task_sha256": TASK_SHA,
        "run_prefix": "run-prefix-1",
        "timeout": 120,
        "source_head": SOURCE_HEAD,
        "assignment_id": "assignment-1",
        "assignment_sha256": ASSIGNMENT_SHA,
        "controller_run_id": controller_run_id,
        "created_at": CREATED_AT,
    }


def _write_reservation_record(
    runs_dir: Path,
    record: dict[str, Any] | bytes,
) -> Path:
    target_file = runs_dir / "broker-reservation.json"
    if isinstance(record, bytes):
        encoded = record
    else:
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    target_file.write_bytes(encoded)
    os.chmod(target_file, 0o600)
    return target_file


def test_read_cycle_reservation_positive_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        req = _build_request()
        published = publish_cycle_reservation(root, req)

        target_file = runs_dir / "broker-reservation.json"
        assert target_file.is_file()
        st_before = os.lstat(target_file)
        entries_before = sorted(os.listdir(runs_dir))

        read_back = read_cycle_reservation(root, CYCLE_ID)

        st_after = os.lstat(target_file)
        entries_after = sorted(os.listdir(runs_dir))

        assert read_back == published
        assert entries_after == entries_before
        assert st_after.st_mtime_ns == st_before.st_mtime_ns
        assert st_after.st_ctime_ns == st_before.st_ctime_ns
        assert st_after.st_size == st_before.st_size
        assert st_after.st_ino == st_before.st_ino
        assert stat.S_IMODE(st_after.st_mode) == 0o600


@pytest.mark.parametrize(
    ("bad_run_id", "expected_match"),
    [
        (12345, "controller run ID must be a string"),
        (None, "controller run ID must be a string"),
        (
            "maint-20260925T010000Z-0123456789abcdef",
            "cycle reservation rejects maintenance run ID; controller run ID must be a cycle run ID",
        ),
        ("invalid-cycle-id", "controller run ID must be a cycle run ID"),
        ("cycle-bad!id", "controller run ID must be a cycle run ID"),
    ],
)
def test_read_cycle_reservation_controller_run_id_validation(
    bad_run_id: Any,
    expected_match: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir).resolve(strict=True)
        with pytest.raises(BrokerError, match=expected_match):
            read_cycle_reservation(root, bad_run_id)  # type: ignore[arg-type]


def test_read_cycle_reservation_byte_noncanonical_rejection() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        published = publish_cycle_reservation(root, _build_request())

        whitespace_bytes = json.dumps(
            published,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        _write_reservation_record(runs_dir, whitespace_bytes)
        with pytest.raises(
            BrokerError,
            match="broker reservation record is not byte-for-byte canonical JSON",
        ):
            read_cycle_reservation(root, CYCLE_ID)

    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        published = publish_cycle_reservation(root, _build_request())

        reversed_keys = {k: published[k] for k in sorted(published.keys(), reverse=True)}
        non_sorted_bytes = json.dumps(
            reversed_keys,
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _write_reservation_record(runs_dir, non_sorted_bytes)
        with pytest.raises(
            BrokerError,
            match="broker reservation record is not byte-for-byte canonical JSON",
        ):
            read_cycle_reservation(root, CYCLE_ID)


@pytest.mark.parametrize(
    ("tamper_fn", "expected_match"),
    [
        (
            lambda r: r.pop("created_at"),
            "broker reservation record does not match the exact contract",
        ),
        (
            lambda r: r.pop("request_sha256"),
            "broker reservation record does not match the exact contract",
        ),
        (
            lambda r: r.update({"extra_key": "unexpected"}),
            "broker reservation record does not match the exact contract",
        ),
        (
            lambda r: r.update({"schema_version": "2.0"}),
            "broker reservation schema version is invalid",
        ),
        (
            lambda r: r.update({"schema_version": 1}),
            "broker reservation schema version is invalid",
        ),
        (
            lambda r: r.update({"controller_run_id": "cycle-2026-20260925T010000Z-9"}),
            "broker reservation controller run ID mismatch",
        ),
        (
            lambda r: r.update({"controller_run_id": 12345}),
            "broker reservation controller run ID mismatch",
        ),
        (
            lambda r: r.update({"request_id": "short"}),
            "broker reservation request ID is invalid",
        ),
        (
            lambda r: r.update({"request_id": 12345}),
            "broker reservation request ID is invalid",
        ),
        (
            lambda r: r.update({"request_sha256": "x" * 63}),
            "broker reservation request SHA is invalid",
        ),
        (
            lambda r: r.update({"request_sha256": 12345}),
            "broker reservation request SHA is invalid",
        ),
        (
            lambda r: r.update({"created_at": True}),
            "broker reservation created_at is invalid",
        ),
        (
            lambda r: r.update({"created_at": "1700000000.5"}),
            "broker reservation created_at is invalid",
        ),
    ],
)
def test_read_cycle_reservation_top_level_tamper_matrix(
    tamper_fn: Any,
    expected_match: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        published = publish_cycle_reservation(root, _build_request())

        record = dict(published)
        tamper_fn(record)
        _write_reservation_record(runs_dir, record)

        with pytest.raises(BrokerError, match=expected_match):
            read_cycle_reservation(root, CYCLE_ID)
