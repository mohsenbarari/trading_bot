#!/usr/bin/env python3
"""Run one production deploy behind durable lock and result fencing.

The controller may be killed with SIGKILL.  This supervisor intentionally
survives that event, retains both inherited flock descriptors, runs exactly
one deploy child, and commits a value-free terminal result journal.
"""

from __future__ import annotations

import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SCHEMA = "production_deploy_child_fence/1.0"
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "SUPERVISOR_FAILED"})


class FenceError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _read_initial(path: Path, expected_digest: str) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        path_info = path.lstat()
        payload = os.read(descriptor, 262145)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= 262144
            or len(payload) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (path_info.st_dev, path_info.st_ino) != (before.st_dev, before.st_ino)
            or _digest(payload) != expected_digest
        ):
            raise FenceError("deploy_fence_invalid")
        value = json.loads(payload)
    finally:
        os.close(descriptor)
    if not isinstance(value, dict):
        raise FenceError("deploy_fence_invalid")
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    body = _render(value)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _process_identity(pid: int) -> dict[str, object]:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
    if not boot_id or len(fields) < 22 or not fields[21].isdigit():
        raise FenceError("deploy_process_identity_unavailable")
    return {"pid": pid, "boot_id": boot_id, "start_ticks": fields[21]}


def _verify_lock(descriptor: int, binding: object) -> str:
    if not isinstance(binding, Mapping):
        raise FenceError("deploy_fence_lock_invalid")
    metadata = os.fstat(descriptor)
    try:
        descriptor_path = Path(
            os.readlink(f"/proc/self/fd/{descriptor}")
        ).resolve(strict=True)
        path_metadata = descriptor_path.lstat()
    except (OSError, RuntimeError) as exc:
        raise FenceError("deploy_fence_lock_invalid") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_dev != binding.get("device")
        or metadata.st_ino != binding.get("inode")
        or not descriptor_path.is_absolute()
        or descriptor_path.is_symlink()
        or not stat.S_ISREG(path_metadata.st_mode)
        or (path_metadata.st_dev, path_metadata.st_ino)
        != (metadata.st_dev, metadata.st_ino)
        or _digest(str(descriptor_path).encode("utf-8"))
        != binding.get("path_sha256")
    ):
        raise FenceError("deploy_fence_lock_invalid")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise FenceError("deploy_fence_lock_not_inherited") from exc
    return str(descriptor_path)


def _verify_deploy_script(
    descriptor: int,
    *,
    expected_digest: str,
    expected_path: Path,
    command: Sequence[str],
) -> tuple[str, str]:
    """Verify and retain the exact checkout script inherited from the parent."""

    try:
        metadata = os.fstat(descriptor)
        descriptor_path = Path(
            os.readlink(f"/proc/self/fd/{descriptor}")
        ).resolve(strict=True)
        path_metadata = descriptor_path.lstat()
    except (OSError, RuntimeError) as exc:
        raise FenceError("deploy_script_fd_invalid") from exc
    hasher = sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            break
        hasher.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    observed = hasher.hexdigest()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or descriptor_path != expected_path
        or descriptor_path.is_symlink()
        or not stat.S_ISREG(path_metadata.st_mode)
        or (path_metadata.st_dev, path_metadata.st_ino)
        != (metadata.st_dev, metadata.st_ino)
        or (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        or offset != metadata.st_size
        or len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
        or observed != expected_digest
        or len(command) < 2
        or command[0] != "bash"
        or command[1] != f"/proc/self/fd/{descriptor}"
    ):
        raise FenceError("deploy_script_fd_invalid")
    return (
        f"{metadata.st_dev}:{metadata.st_ino}",
        _digest(str(descriptor_path).encode("utf-8")),
    )


def _private_primary_readiness(lines: list[dict[str, Any]]) -> dict[str, object]:
    candidates = [
        item
        for item in lines
        if item.get("status") == "READY"
        and item.get("authority") == "PRIVATE_PRIMARY"
        and item.get("rate_cell_count") == 14
        and item.get("required_source_input_trace_count") == 9
    ]
    if len(candidates) != 3:
        raise FenceError("private_primary_readiness_count_invalid")
    exact_fields = (
        "snapshot_digest",
        "snapshot_hash",
        "snapshot_version",
        "source_input_trace_sha256",
        "required_source_input_trace_count",
    )
    first = candidates[0]
    if any(
        any(item.get(field) != first.get(field) for field in exact_fields)
        for item in candidates[1:]
    ):
        raise FenceError("private_primary_readiness_identity_mismatch")
    ages = [item.get("snapshot_age_seconds") for item in candidates]
    if any(
        isinstance(age, bool)
        or not isinstance(age, (int, float))
        or not 0.0 <= float(age) <= 120.0
        for age in ages
    ):
        raise FenceError("private_primary_readiness_age_invalid")
    for field in ("snapshot_digest", "snapshot_hash", "source_input_trace_sha256"):
        value = first.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(
            token not in "0123456789abcdef" for token in value
        ):
            raise FenceError("private_primary_readiness_digest_invalid")
    return {
        "consumer_count": 3,
        "snapshot_digest": first["snapshot_digest"],
        "snapshot_hash": first["snapshot_hash"],
        "snapshot_version": first["snapshot_version"],
        "maximum_snapshot_age_seconds": round(max(float(age) for age in ages), 3),
        "required_source_input_trace_count": 9,
        "source_input_trace_sha256": first["source_input_trace_sha256"],
    }


def run(args: argparse.Namespace) -> int:
    journal = Path(args.journal)
    if not journal.is_absolute() or journal.is_symlink():
        raise FenceError("deploy_fence_path_invalid")
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise FenceError("deploy_command_missing")
    cwd = Path(args.cwd)
    if (
        not cwd.is_absolute()
        or cwd.is_symlink()
        or cwd.resolve(strict=True) != cwd
    ):
        raise FenceError("deploy_cwd_invalid")
    value = _read_initial(journal, args.expected_journal_sha256)
    command_sha256 = _digest(json.dumps(command, separators=(",", ":")).encode())
    if (
        value.get("schema") != SCHEMA
        or value.get("status") != "PREPARED"
        or value.get("command_sha256") != command_sha256
        or value.get("deploy_script_sha256")
        != args.expected_deploy_script_sha256
        or value.get("secrets_disclosed") is not False
    ):
        raise FenceError("deploy_fence_binding_invalid")
    _verify_lock(args.run_lock_fd, value.get("run_lock"))
    _verify_lock(args.source_lock_fd, value.get("source_lock"))
    deploy_identity, deploy_path_sha256 = _verify_deploy_script(
        args.deploy_script_fd,
        expected_digest=args.expected_deploy_script_sha256,
        expected_path=cwd / "scripts/production_deploy_online.sh",
        command=command,
    )
    supervisor = _process_identity(os.getpid())
    value.update(
        {
            "status": "RUNNING",
            "started_at_utc": _now(),
            "supervisor": supervisor,
            "deploy_script_sha256": args.expected_deploy_script_sha256,
        }
    )
    _atomic_write(journal, value)
    run_metadata = os.fstat(args.run_lock_fd)
    source_metadata = os.fstat(args.source_lock_fd)
    wrapper = [
        "bash",
        "-c",
        "set -euo pipefail; "
        "test -e \"/proc/self/fd/$PRODUCTION_RUN_LOCK_FD\"; "
        "test -e \"/proc/self/fd/$PRODUCTION_SOURCE_LOCK_FD\"; "
        "test \"$(stat -Lc '%d:%i' \"/proc/self/fd/$PRODUCTION_RUN_LOCK_FD\")\" = \"$PRODUCTION_RUN_LOCK_IDENTITY\"; "
        "test \"$(stat -Lc '%d:%i' \"/proc/self/fd/$PRODUCTION_SOURCE_LOCK_FD\")\" = \"$PRODUCTION_SOURCE_LOCK_IDENTITY\"; "
        "test \"$(readlink -e \"/proc/self/fd/$PRODUCTION_RUN_LOCK_FD\" | tr -d '\\n' | sha256sum | awk '{print $1}')\" = \"$PRODUCTION_RUN_LOCK_PATH_SHA256\"; "
        "test \"$(readlink -e \"/proc/self/fd/$PRODUCTION_SOURCE_LOCK_FD\" | tr -d '\\n' | sha256sum | awk '{print $1}')\" = \"$PRODUCTION_SOURCE_LOCK_PATH_SHA256\"; "
        "test -e \"/proc/self/fd/$PRODUCTION_DEPLOY_SCRIPT_FD\"; "
        "test \"$(stat -Lc '%d:%i' \"/proc/self/fd/$PRODUCTION_DEPLOY_SCRIPT_FD\")\" = \"$PRODUCTION_DEPLOY_SCRIPT_IDENTITY\"; "
        "test \"$(readlink -e \"/proc/self/fd/$PRODUCTION_DEPLOY_SCRIPT_FD\" | tr -d '\\n' | sha256sum | awk '{print $1}')\" = \"$PRODUCTION_DEPLOY_SCRIPT_PATH_SHA256\"; "
        "test \"$(sha256sum \"/proc/self/fd/$PRODUCTION_DEPLOY_SCRIPT_FD\" | awk '{print $1}')\" = \"$PRODUCTION_DEPLOY_SCRIPT_SHA256\"; "
        "exec \"$@\"",
        "fenced-production-deploy",
        *command,
    ]
    child_env = dict(os.environ)
    child_env.update(
        {
            "PRODUCTION_RUN_LOCK_FD": str(args.run_lock_fd),
            "PRODUCTION_SOURCE_LOCK_FD": str(args.source_lock_fd),
            "PRODUCTION_RUN_LOCK_IDENTITY": (
                f"{run_metadata.st_dev}:{run_metadata.st_ino}"
            ),
            "PRODUCTION_SOURCE_LOCK_IDENTITY": (
                f"{source_metadata.st_dev}:{source_metadata.st_ino}"
            ),
            "PRODUCTION_RUN_LOCK_PATH_SHA256": str(
                value["run_lock"]["path_sha256"]
            ),
            "PRODUCTION_SOURCE_LOCK_PATH_SHA256": str(
                value["source_lock"]["path_sha256"]
            ),
            "PRODUCTION_DEPLOY_SCRIPT_FD": str(args.deploy_script_fd),
            "PRODUCTION_DEPLOY_SCRIPT_IDENTITY": deploy_identity,
            "PRODUCTION_DEPLOY_SCRIPT_PATH_SHA256": deploy_path_sha256,
            "PRODUCTION_DEPLOY_SCRIPT_SHA256": (
                args.expected_deploy_script_sha256
            ),
        }
    )
    child = subprocess.Popen(
        wrapper,
        cwd=args.cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=child_env,
        pass_fds=(
            args.run_lock_fd,
            args.source_lock_fd,
            args.deploy_script_fd,
        ),
        text=True,
    )
    value["deploy_child"] = _process_identity(child.pid)
    value["deploy_process_group_id"] = child.pid
    _atomic_write(journal, value)
    readiness_lines: list[dict[str, Any]] = []
    if child.stdout is not None:
        for raw_line in child.stdout:
            line = raw_line.strip()
            if not line.startswith("{") or not line.endswith("}"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                readiness_lines.append(item)
        child.stdout.close()
    deploy_returncode = child.wait()
    returncode = deploy_returncode
    _verify_deploy_script(
        args.deploy_script_fd,
        expected_digest=args.expected_deploy_script_sha256,
        expected_path=cwd / "scripts/production_deploy_online.sh",
        command=command,
    )
    if value.get("private_primary_required") is True:
        try:
            value["product_readiness"] = _private_primary_readiness(readiness_lines)
        except FenceError:
            returncode = 96
            value["failure_code"] = "PRIVATE_PRIMARY_READINESS_EVIDENCE_INVALID"
    value.update(
        {
            "status": "SUCCEEDED" if returncode == 0 else "FAILED",
            "finished_at_utc": _now(),
            "returncode": returncode,
            "deploy_returncode": deploy_returncode,
        }
    )
    _atomic_write(journal, value)
    print(
        json.dumps(
            {
                "status": value["status"],
                "journal_sha256": _digest(_render(value)),
                "secrets_disclosed": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if returncode == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", required=True)
    parser.add_argument("--expected-journal-sha256", required=True)
    parser.add_argument("--run-lock-fd", required=True, type=int)
    parser.add_argument("--source-lock-fd", required=True, type=int)
    parser.add_argument("--deploy-script-fd", required=True, type=int)
    parser.add_argument("--expected-deploy-script-sha256", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (FenceError, OSError, ValueError, json.JSONDecodeError):
        print(json.dumps({"status": "SUPERVISOR_FAILED", "secrets_disclosed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
