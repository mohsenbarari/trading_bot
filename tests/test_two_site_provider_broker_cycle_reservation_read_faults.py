"""Hermetic fault matrix and contract tests for read_cycle_reservation nested payloads and file security."""
from __future__ import annotations

import copy
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
    import scripts.two_site_provider_broker as broker_mod
except ModuleNotFoundError:
    from two_site_preparation_contract import (  # type: ignore[no-redef]
        PreparationStartup,
    )
    from two_site_provider_broker import (  # type: ignore[no-redef]
        BrokerError,
        publish_cycle_reservation,
        read_cycle_reservation,
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


@pytest.mark.parametrize(
    ("case_name", "tamper_fn", "expected_match"),
    [
        (
            "missing_reservation_key",
            lambda r: r["reservation"].pop("run_prefix"),
            "broker reservation does not match the exact contract",
        ),
        (
            "extra_reservation_key",
            lambda r: r["reservation"].update({"unexpected_extra": "val"}),
            "broker reservation does not match the exact contract",
        ),
        (
            "reservation_not_a_dict",
            lambda r: r.update({"reservation": "not-a-dict"}),
            "broker reservation does not match the exact contract",
        ),
        (
            "wrong_type_task_path",
            lambda r: r["reservation"].update({"task_path": 12345}),
            "broker reservation field task_path must be a string",
        ),
        (
            "wrong_type_run_prefix",
            lambda r: r["reservation"].update({"run_prefix": True}),
            "broker reservation field run_prefix must be a string",
        ),
        (
            "reservation_controller_run_id_mismatch",
            lambda r: r["reservation"].update(
                {"controller_run_id": "cycle-2026-20260925T010000Z-9"}
            ),
            "reservation controller run ID mismatch",
        ),
        (
            "invalid_run_prefix",
            lambda r: r["reservation"].update({"run_prefix": "invalid run prefix!"}),
            "reservation run-prefix is invalid",
        ),
        (
            "invalid_task_sha",
            lambda r: r["reservation"].update({"task_sha256": "x" * 63}),
            "reservation task SHA is invalid",
        ),
        (
            "invalid_source_head",
            lambda r: r["reservation"].update({"source_head": "g" * 40}),
            "reservation source HEAD is invalid",
        ),
        (
            "invalid_assignment_id",
            lambda r: r["reservation"].update({"assignment_id": "invalid assignment @!"}),
            "reservation assignment ID is invalid",
        ),
        (
            "invalid_assignment_sha",
            lambda r: r["reservation"].update({"assignment_sha256": "bad-sha"}),
            "reservation assignment SHA is invalid",
        ),
        (
            "noncanonical_task_path_dot_dot",
            lambda r: r["reservation"].update(
                {"task_path": ".local/two-site-refactor/tasks/../tasks/fixture.json"}
            ),
            "broker task path must be canonical and relative",
        ),
        (
            "noncanonical_task_path_dot_slash",
            lambda r: r["reservation"].update(
                {"task_path": "./.local/two-site-refactor/tasks/fixture.json"}
            ),
            "broker task path must be canonical and relative",
        ),
        (
            "source_head_differs_from_startup",
            lambda r: r["reservation"].update({"source_head": "0" * 40}),
            "reservation source HEAD does not match preparation startup",
        ),
        (
            "assignment_sha_differs_from_startup",
            lambda r: r["reservation"].update({"assignment_sha256": "0" * 64}),
            "reservation assignment SHA does not match preparation startup",
        ),
    ],
)
def test_read_cycle_reservation_nested_tamper_matrix(
    case_name: str,
    tamper_fn: Any,
    expected_match: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        published = publish_cycle_reservation(root, _build_request())

        record = copy.deepcopy(published)
        tamper_fn(record)
        _write_reservation_record(runs_dir, record)

        with pytest.raises(BrokerError, match=expected_match):
            read_cycle_reservation(root, CYCLE_ID)

        queue_dir = root / ".local" / "two-site-refactor" / "provider-broker"
        assert not queue_dir.exists()


def test_read_cycle_reservation_startup_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        startup_file = runs_dir / "preparation-startup.json"
        startup_file.unlink()

        with pytest.raises(
            BrokerError, match="cannot lstat cycle private file preparation-startup.json"
        ):
            read_cycle_reservation(root, CYCLE_ID)

        queue_dir = root / ".local" / "two-site-refactor" / "provider-broker"
        assert not queue_dir.exists()


def test_read_cycle_reservation_startup_schema_version_string() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        bad_startup = {
            "schema_version": "1.0",
            "route": "one_shot",
            "cycle_id": CYCLE_ID,
            "source_head": SOURCE_HEAD,
            "assignment_sha256": ASSIGNMENT_SHA,
        }
        encoded = json.dumps(
            bad_startup, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        startup_file = runs_dir / "preparation-startup.json"
        startup_file.write_bytes(encoded)
        os.chmod(startup_file, 0o600)

        with pytest.raises(
            BrokerError,
            match=r"(?:invalid preparation startup|preparation startup schema version)",
        ):
            read_cycle_reservation(root, CYCLE_ID)

        queue_dir = root / ".local" / "two-site-refactor" / "provider-broker"
        assert not queue_dir.exists()


def test_read_cycle_reservation_startup_valid_different_shas() -> None:
    queue_subpath = Path(".local/two-site-refactor/provider-broker")

    # Different source_head in startup
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        diff_startup = PreparationStartup(
            schema_version=1,
            route="one_shot",
            cycle_id=CYCLE_ID,
            source_head="0" * 40,
            assignment_sha256=ASSIGNMENT_SHA,
        )
        encoded = json.dumps(
            diff_startup.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        startup_file = runs_dir / "preparation-startup.json"
        startup_file.write_bytes(encoded)
        os.chmod(startup_file, 0o600)

        with pytest.raises(
            BrokerError,
            match="reservation source HEAD does not match preparation startup",
        ):
            read_cycle_reservation(root, CYCLE_ID)
        assert not (root / queue_subpath).exists()

    # Different assignment_sha256 in startup
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        diff_startup = PreparationStartup(
            schema_version=1,
            route="one_shot",
            cycle_id=CYCLE_ID,
            source_head=SOURCE_HEAD,
            assignment_sha256="0" * 64,
        )
        encoded = json.dumps(
            diff_startup.to_mapping(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        startup_file = runs_dir / "preparation-startup.json"
        startup_file.write_bytes(encoded)
        os.chmod(startup_file, 0o600)

        with pytest.raises(
            BrokerError,
            match="reservation assignment SHA does not match preparation startup",
        ):
            read_cycle_reservation(root, CYCLE_ID)
        assert not (root / queue_subpath).exists()


def test_read_cycle_reservation_unsafe_record_file_guards() -> None:
    queue_subpath = Path(".local/two-site-refactor/provider-broker")

    # Symlink
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        target_file = runs_dir / "broker-reservation.json"
        outside_file = root / "real-reservation.json"
        target_file.rename(outside_file)
        os.symlink(outside_file, target_file)

        with pytest.raises(BrokerError, match="is not a regular file"):
            read_cycle_reservation(root, CYCLE_ID)

        assert target_file.is_symlink()
        assert not (root / queue_subpath).exists()

    # Hardlink (nlink 2)
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        target_file = runs_dir / "broker-reservation.json"
        extra_link = runs_dir / "extra-link.json"
        os.link(target_file, extra_link)

        with pytest.raises(BrokerError, match="has invalid link count"):
            read_cycle_reservation(root, CYCLE_ID)

        st = os.lstat(target_file)
        assert st.st_nlink == 2
        assert not (root / queue_subpath).exists()

    # Mode 0644
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        target_file = runs_dir / "broker-reservation.json"
        os.chmod(target_file, 0o644)

        with pytest.raises(BrokerError, match="mode must be 0600"):
            read_cycle_reservation(root, CYCLE_ID)

        st = os.lstat(target_file)
        assert stat.S_IMODE(st.st_mode) == 0o644
        assert not (root / queue_subpath).exists()


def test_read_cycle_reservation_parent_path_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root, runs_dir = _setup_cycle_root(tmp_dir)
        _write_startup_json(runs_dir)
        publish_cycle_reservation(root, _build_request())

        runs_parent = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
        renamed_runs = runs_parent.with_name("runs.orig")

        orig_read = broker_mod._read_cycle_private_json
        intercepted = False

        def intercept_read(
            run_path: Path, run_fd: int, filename: str
        ) -> tuple[dict[str, Any], bytes]:
            nonlocal intercepted
            payload, raw_bytes = orig_read(run_path, run_fd, filename)
            if filename == "broker-reservation.json":
                intercepted = True
                os.rename(runs_parent, renamed_runs)
                os.mkdir(runs_parent, 0o700)
            return payload, raw_bytes

        monkeypatch.setattr(broker_mod, "_read_cycle_private_json", intercept_read)

        try:
            with pytest.raises(BrokerError) as exc_info:
                read_cycle_reservation(root, CYCLE_ID)
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
            assert not (
                root / ".local" / "two-site-refactor" / "provider-broker"
            ).exists()
        finally:
            monkeypatch.undo()
            if runs_parent.exists() and renamed_runs.exists():
                os.rmdir(runs_parent)
                os.rename(renamed_runs, runs_parent)
