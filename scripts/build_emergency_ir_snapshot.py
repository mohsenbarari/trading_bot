#!/usr/bin/env python3
"""Capture a read-only, validated PostgreSQL custom dump for Emergency IR.

This helper is for the WA-FI build host only.  It refuses every container
except the known standalone production database, streams ``pg_dump -Fc`` to a
create-only root-owned file, then validates the archive with ``pg_restore
--list`` before finalizing it.  It never restores, changes, stops, or restarts
the source database and has no network, S3, SSH, DNS, or deployment client.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, BinaryIO, Callable


PRODUCTION_DB_CONTAINER = "trading_bot_db"
EXPECTED_SOURCE_IMAGE = "postgres:15-alpine"
DOCKER_BINARY = Path("/usr/bin/docker")
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024 * 1024
PG_DUMP_COMMAND = 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges'
PG_RESTORE_LIST_COMMAND = "exec pg_restore --list"
CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
SOURCE_INSPECT_FORMAT = "{{.Id}}\\n{{.Config.Image}}\\n{{.State.Running}}"


class EmergencySnapshotError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class SnapshotResult:
    output: Path
    sha256: str
    bytes: int


@dataclasses.dataclass(frozen=True)
class ProductionDatabaseSource:
    container_id: str


def _fail(message: str) -> None:
    raise EmergencySnapshotError(message)


def _secure_output_directory(path: Path) -> None:
    if not path.is_absolute():
        _fail("snapshot output directory must be an owner-controlled absolute directory")
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise EmergencySnapshotError("snapshot output directory cannot be inspected") from exc
        sticky_tmp = (
            current == Path("/tmp")
            and state.st_uid == 0
            and bool(stat.S_IMODE(state.st_mode) & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid not in {0, os.geteuid()}
            or (stat.S_IMODE(state.st_mode) & 0o022 and (current == path or not sticky_tmp))
        ):
            _fail("snapshot output directory must be an owner-controlled absolute directory")
    try:
        final_state = path.lstat()
    except OSError as exc:
        raise EmergencySnapshotError("snapshot output directory cannot be inspected") from exc
    if final_state.st_uid != os.geteuid():
        _fail("snapshot output directory must be an owner-controlled absolute directory")


def _digest(path: Path) -> tuple[str, int]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencySnapshotError("snapshot cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o077
        or not 5 <= before.st_size <= MAX_SNAPSHOT_BYTES
    ):
        _fail("snapshot must be one bounded root-only regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail("snapshot changed while being opened")
        digest = hashlib.sha256()
        total = 0
        prefix = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if len(prefix) < 5:
                prefix.extend(chunk[: 5 - len(prefix)])
            total += len(chunk)
            if total > MAX_SNAPSHOT_BYTES:
                _fail("snapshot exceeds its fixed size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if total != opened.st_size or any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail("snapshot changed while being read")
        if bytes(prefix) != b"PGDMP":
            _fail("pg_dump did not produce a PostgreSQL custom archive")
        return digest.hexdigest(), total
    except OSError as exc:
        raise EmergencySnapshotError("snapshot cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_docker_binary() -> None:
    try:
        state = DOCKER_BINARY.lstat()
    except OSError as exc:
        raise EmergencySnapshotError("Docker binary is unavailable") from exc
    if (
        not DOCKER_BINARY.is_absolute()
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o022
        or not os.access(DOCKER_BINARY, os.X_OK)
    ):
        _fail("Docker binary is unavailable")


def _inspect_source(*, runner: Callable[..., Any]) -> ProductionDatabaseSource:
    try:
        inspected = runner(
            [str(DOCKER_BINARY), "inspect", "-f", SOURCE_INSPECT_FORMAT, PRODUCTION_DB_CONTAINER],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySnapshotError("production database source cannot be inspected") from exc
    fields = str(getattr(inspected, "stdout", "")).splitlines()
    if (
        getattr(inspected, "returncode", 1) != 0
        or len(fields) != 3
        or CONTAINER_ID_RE.fullmatch(fields[0]) is None
        or fields[1] != EXPECTED_SOURCE_IMAGE
        or fields[2] != "true"
    ):
        _fail("production database source does not match the Emergency snapshot contract")
    return ProductionDatabaseSource(container_id=fields[0])


def _run_dump(*, temporary: Path, source: ProductionDatabaseSource, runner: Callable[..., Any]) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = None
            try:
                completed = runner(
                    [
                        str(DOCKER_BINARY),
                        "exec",
                        source.container_id,
                        "sh",
                        "-ec",
                        PG_DUMP_COMMAND,
                    ],
                    check=False,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=7200,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise EmergencySnapshotError("read-only production pg_dump failed") from exc
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise EmergencySnapshotError("refusing to overwrite an existing partial snapshot") from exc
    except OSError as exc:
        raise EmergencySnapshotError("snapshot temporary file cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if getattr(completed, "returncode", 1) != 0:
        _fail("read-only production pg_dump failed")


def _validate_archive(*, temporary: Path, source: ProductionDatabaseSource, runner: Callable[..., Any]) -> None:
    try:
        with temporary.open("rb") as archive_source:
            completed = runner(
                [
                    str(DOCKER_BINARY),
                    "exec",
                    "-i",
                    source.container_id,
                    "sh",
                    "-ec",
                    PG_RESTORE_LIST_COMMAND,
                ],
                check=False,
                stdin=archive_source,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=7200,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySnapshotError("snapshot pg_restore validation failed") from exc
    if getattr(completed, "returncode", 1) != 0:
        _fail("snapshot pg_restore validation failed")


def capture_snapshot(*, output: Path, runner: Callable[..., Any] = subprocess.run) -> SnapshotResult:
    if not output.is_absolute():
        _fail("snapshot output must be absolute")
    _secure_output_directory(output.parent)
    if output.exists() or output.is_symlink():
        _fail("refusing to overwrite an existing Emergency snapshot")
    _validate_docker_binary()
    temporary = output.with_name(f".{output.name}.{os.getpid()}.part")
    if temporary.exists() or temporary.is_symlink():
        _fail("refusing to overwrite an existing partial snapshot")
    source = _inspect_source(runner=runner)
    _run_dump(temporary=temporary, source=source, runner=runner)
    digest, size = _digest(temporary)
    _validate_archive(temporary=temporary, source=source, runner=runner)
    try:
        os.link(temporary, output, follow_symlinks=False)
    except FileExistsError as exc:
        raise EmergencySnapshotError("refusing to overwrite an existing Emergency snapshot") from exc
    except OSError as exc:
        raise EmergencySnapshotError("validated snapshot cannot be finalized") from exc
    try:
        temporary.unlink()
    except OSError as exc:
        raise EmergencySnapshotError("validated snapshot temporary cannot be finalized") from exc
    return SnapshotResult(output=output, sha256=digest, bytes=size)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        if not sys.flags.isolated:
            _fail("Emergency snapshot builder must be launched with python3 -I -B")
        args = parse_args(argv)
        result = capture_snapshot(output=args.output)
    except EmergencySnapshotError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps({"status": "captured-local-only", "output": str(result.output), "sha256": result.sha256, "bytes": result.bytes}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
