#!/usr/bin/env python3
"""Run one deterministic Object Storage receive-and-restore cycle on WA-IR.

This wrapper is designed for a 15-30 second systemd timer.  It consumes only
the newest immutable age-encrypted S3 snapshot, asks the restore primitive to
create a new candidate DB/uploads volume, and leaves every application/public
activation surface untouched.  A non-blocking local lock prevents overlapping
timer invocations from selecting or overwriting the same candidate.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from restore_webapp_ir_snapshot import (
    DEFAULT_COMPOSE_FILE,
    RestoreError,
    parse_env_file,
    require_absolute_directory,
    require_config,
    require_no_restore_inflight,
    require_secure_regular_file,
    require_snapshot_maximum_age,
)
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.production_writer_lease import ProductionWriterLeaseError, load_production_writer_lease


DEFAULT_TRANSPORT_SCRIPT = REPO_ROOT / "scripts/manage_webapp_ir_snapshot.py"
DEFAULT_RESTORE_SCRIPT = REPO_ROOT / "scripts/restore_webapp_ir_snapshot.py"
SCHEMA_VERSION = "webapp_ir_snapshot_refresh_v1"
MAX_REFRESH_CYCLE_SECONDS = 25


def require_tool_file(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise RestoreError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RestoreError(f"{label} does not exist") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RestoreError(f"{label} must be a regular non-symlink file")
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise RestoreError(f"{label} must be root-owned and not group/world writable")
    return path.resolve(strict=True)


def parse_json_output(result: subprocess.CompletedProcess[str], *, label: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise RestoreError(f"{label} failed with exit {result.returncode}")
    for line in reversed(result.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RestoreError(f"{label} did not return JSON")


def run_json_command(
    arguments: Sequence[str], *, label: str, timeout_seconds: float
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(item) for item in arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=child_environment(),
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RestoreError(f"{label} could not start") from exc
    return parse_json_output(result, label=label)


@contextmanager
def refresh_lock(state_root: Path) -> Iterator[None]:
    path = state_root / "refresh.lock"
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_nlink != 1
        ):
            raise RestoreError("WA-IR snapshot refresh lock is not root-only")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RestoreError("another WA-IR snapshot refresh is already active") from exc
        yield
    finally:
        os.close(descriptor)


def active_ir_writer_lease(values: Mapping[str, str]) -> bool:
    """Return true only for a verified, still-live local WA-IR Writer term."""

    raw_path = values.get("WA_IR_WRITER_LEASE_FILE")
    if raw_path is None or not raw_path.strip():
        return False
    path = Path(raw_path)
    if not path.is_absolute():
        raise RestoreError("WA_IR_WRITER_LEASE_FILE must be absolute")
    require_secure_regular_file(path, label="WA_IR_WRITER_LEASE_FILE")
    try:
        lease = load_production_writer_lease(path)
    except ProductionWriterLeaseError as exc:
        raise RestoreError("WA-IR writer lease cannot be verified") from exc
    return lease.holder_site == "webapp_ir" and lease.expires_at > datetime.now(timezone.utc)


def child_environment() -> dict[str, str]:
    # Do not pass arbitrary secrets inherited from an interactive shell to
    # timer children.  The transport and restore scripts read root-only files.
    allowed = ("PATH", "LANG", "LC_ALL", "TZ")
    environment = {key: value for key in allowed if (value := os.environ.get(key))}
    environment.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    environment.setdefault("TZ", "UTC")
    return environment


def require_transport_maximum_snapshot_age(path: Path) -> int:
    """Keep the local final-pointer bound equal to the S3 consumer bound."""

    require_secure_regular_file(path, label="snapshot transport config")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreError("snapshot transport config is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RestoreError("snapshot transport config must be a JSON object")
    maximum_age = payload.get("maximum_snapshot_age_seconds")
    if isinstance(maximum_age, bool) or not isinstance(maximum_age, int):
        raise RestoreError("snapshot transport config must set maximum_snapshot_age_seconds")
    if not 15 <= maximum_age <= 30:
        raise RestoreError("snapshot transport maximum_snapshot_age_seconds must be between 15 and 30")
    return maximum_age


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standby-env", required=True)
    parser.add_argument("--transport-script", default=str(DEFAULT_TRANSPORT_SCRIPT))
    parser.add_argument("--restore-script", default=str(DEFAULT_RESTORE_SCRIPT))
    parser.add_argument("--transport-python", default=sys.executable)
    parser.add_argument("--restore-python", default=sys.executable)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-previous-running", action="store_true")
    parser.add_argument("--timer-interval-seconds", type=int, default=15)
    parser.add_argument("--json", action="store_true")
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.timer_interval_seconds < 15 or args.timer_interval_seconds > 30:
        raise RestoreError("timer-interval-seconds must be between 15 and 30")
    standby_env = Path(args.standby_env)
    values = parse_env_file(standby_env, label="standby env")
    workspace_root = require_absolute_directory(
        require_config(values, "WA_IR_SNAPSHOT_WORK_ROOT"), label="WA_IR_SNAPSHOT_WORK_ROOT"
    )
    state_root = require_absolute_directory(
        require_config(values, "WA_IR_SNAPSHOT_STATE_ROOT"), label="WA_IR_SNAPSHOT_STATE_ROOT"
    )
    transport_config = Path(require_config(values, "WA_IR_SNAPSHOT_TRANSPORT_CONFIG"))
    if not transport_config.is_absolute():
        raise RestoreError("WA_IR_SNAPSHOT_TRANSPORT_CONFIG must be absolute")
    maximum_snapshot_age_seconds = require_snapshot_maximum_age(values)
    transport_maximum_age_seconds = require_transport_maximum_snapshot_age(transport_config)
    if transport_maximum_age_seconds != maximum_snapshot_age_seconds:
        raise RestoreError("transport and restore snapshot freshness bounds must match")
    if args.timer_interval_seconds > maximum_snapshot_age_seconds:
        raise RestoreError("timer-interval-seconds may not exceed the snapshot freshness bound")
    transport_script = require_tool_file(Path(args.transport_script), label="transport-script")
    restore_script = require_tool_file(Path(args.restore_script), label="restore-script")
    for executable, label in ((args.transport_python, "transport-python"), (args.restore_python, "restore-python")):
        if not Path(executable).is_absolute() or not os.access(executable, os.X_OK):
            raise RestoreError(f"{label} must be an executable absolute path")
    plan = {
        "schema_version": SCHEMA_VERSION,
        "status": "planned" if not args.apply else "running",
        "timer_interval_seconds": args.timer_interval_seconds,
        "maximum_snapshot_age_seconds": maximum_snapshot_age_seconds,
        "maximum_cycle_seconds": MAX_REFRESH_CYCLE_SECONDS,
        "freshness_measured_from": "source_db_snapshot_started_at",
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "candidate_root": str(workspace_root),
        "app_started": False,
        "direct_sync_started": False,
        "migration_started": False,
        "public_routing_changed": False,
    }
    if not args.apply:
        return plan
    with refresh_lock(state_root):
        # A killed restore can leave bind volumes or a detached database
        # container behind.  Do not contact Object Storage or attempt a new
        # candidate until an explicit recovery has inspected that journal.
        require_no_restore_inflight(state_root)
        if active_ir_writer_lease(values):
            return {
                **plan,
                "status": "fenced_by_active_ir_writer",
                "local_writer_fenced": True,
            }
        deadline = time.monotonic() + MAX_REFRESH_CYCLE_SECONDS

        def remaining_cycle_time(*, label: str) -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RestoreError(f"WA-IR snapshot refresh exceeded its {MAX_REFRESH_CYCLE_SECONDS} second cycle bound before {label}")
            return remaining

        transport = run_json_command(
            [
                args.transport_python,
                str(transport_script),
                "consume",
                "--config",
                str(transport_config),
                "--source-site",
                "webapp_fi",
                "--destination-site",
                "webapp_ir",
                "--candidate-root",
                str(workspace_root),
            ],
            label="snapshot transport consume",
            timeout_seconds=remaining_cycle_time(label="snapshot transport consume"),
        )
        if transport.get("status") != "ready":
            raise RestoreError("snapshot transport did not produce a ready receipt")
        candidate_directory = transport.get("candidate_directory")
        if not isinstance(candidate_directory, str):
            raise RestoreError("snapshot transport omitted candidate_directory")
        receipt = Path(candidate_directory) / "snapshot-ready.json"
        try:
            receipt.resolve(strict=True).relative_to(workspace_root)
        except (ValueError, FileNotFoundError) as exc:
            raise RestoreError("snapshot transport receipt escaped the configured workspace") from exc
        restore_arguments = [
            args.restore_python,
            str(restore_script),
            "--standby-env",
            str(standby_env),
            "--receipt",
            str(receipt),
            "--compose-file",
            str(DEFAULT_COMPOSE_FILE),
            "--apply",
            "--json",
        ]
        if args.keep_previous_running:
            restore_arguments.append("--keep-previous-running")
        if active_ir_writer_lease(values):
            return {
                **plan,
                "status": "fenced_by_active_ir_writer",
                "local_writer_fenced": True,
                "transport_received": True,
            }
        restore = run_json_command(
            restore_arguments,
            label="snapshot candidate restore",
            timeout_seconds=remaining_cycle_time(label="snapshot candidate restore"),
        )
    if restore.get("status") != "ready":
        raise RestoreError("snapshot restore did not reach ready state")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "timer_interval_seconds": args.timer_interval_seconds,
        "transport": {
            "snapshot_id": transport.get("snapshot_id"),
            "release_sha": transport.get("release_sha"),
            "alembic_revision": transport.get("alembic_revision"),
            "snapshot_age_seconds": transport.get("snapshot_age_seconds"),
            "source_db_snapshot_age_seconds": transport.get("source_db_snapshot_age_seconds"),
            "source_db_snapshot_started_at": transport.get("source_db_snapshot_started_at"),
            "source_capture_completed_at": transport.get("source_capture_completed_at"),
            "manifest": transport.get("manifest"),
        },
        "restore": restore,
        "app_started": False,
        "direct_sync_started": False,
        "migration_started": False,
        "public_routing_changed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = execute(args)
    except RestoreError as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": str(exc),
            "app_started": False,
            "direct_sync_started": False,
            "migration_started": False,
            "public_routing_changed": False,
        }
        code = 1
    else:
        code = 0
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    else:
        print(f"WA-IR snapshot refresh: {payload['status']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
