#!/usr/bin/env python3
"""Durable ownership and hard-kill recovery for isolated Matrix host faults."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Sequence


SCHEMA = "writer_witness_matrix_host_fault_state_v1"
TAG_PATTERN = re.compile(r"wwm_[0-9a-f]{12}\Z")
KINDS = {
    "disk": {"action": "disk-full", "port": 55439},
    "clock": {"action": "clock-jump", "port": 55440},
}
PHASES = {"claimed", "mounted", "initialized", "postgres_started", "running", "completed"}
DEFAULT_STATE_ROOT = Path("/var/lib/trading-bot-witness/matrix-host-faults")
DEFAULT_RUNTIME_ROOT = Path("/run")
HELPER_BASENAME = "writer-witness-matrix-host-faults"


class RecoveryError(RuntimeError):
    """A tagged resource could not be safely attributed or reconciled."""


def _assert_isolated_runtime(*, test_mode: bool) -> None:
    if not test_mode and Path(sys.executable).resolve(strict=True) != Path("/usr/bin/python3.12"):
        raise RecoveryError("host-fault helper is not using the pinned system Python")
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.ignore_environment
        or not sys.flags.dont_write_bytecode
        or not sys.flags.utf8_mode
        or sys.pycache_prefix != "/dev/null"
    ):
        raise RecoveryError("host-fault helper startup is not isolated")
    if any(
        os.environ.get(name)
        for name in (
            "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
            "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH",
        )
    ):
        raise RecoveryError("host-fault helper inherited a forbidden runtime environment")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_directory(path: Path, *, uid: int, gid: int, mode: int) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise RecoveryError(f"unsafe state directory type: {path}")
    if metadata.st_uid != uid or metadata.st_gid != gid:
        raise RecoveryError(f"unsafe state directory owner: {path}")
    if stat.S_IMODE(metadata.st_mode) != mode:
        raise RecoveryError(f"unsafe state directory mode: {path}")


def _ensure_state_root(path: Path, *, owner_uid: int, owner_gid: int) -> None:
    if path.exists() or path.is_symlink():
        _validate_directory(path, uid=owner_uid, gid=owner_gid, mode=0o700)
        return
    path.mkdir(mode=0o700, parents=True)
    if os.geteuid() == 0:
        os.chown(path, owner_uid, owner_gid)
    _validate_directory(path, uid=owner_uid, gid=owner_gid, mode=0o700)


def _process_start_ticks(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    end = raw.rfind(")")
    if end < 0:
        raise RecoveryError(f"cannot parse process identity for PID {pid}")
    fields = raw[end + 2 :].split()
    if len(fields) < 20:
        raise RecoveryError(f"incomplete process identity for PID {pid}")
    return int(fields[19])


def _process_is_active(pid: int, start_ticks: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    end = raw.rfind(")")
    if end < 0:
        return False
    fields = raw[end + 2 :].split()
    return len(fields) >= 20 and fields[0] != "Z" and int(fields[19]) == start_ticks


def _process_cmdline(pid: int) -> list[str]:
    try:
        value = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return []
    return [item.decode("utf-8", "replace") for item in value.split(b"\0") if item]


def _expected(tag: str, kind: str, runtime_parent: Path) -> dict[str, object]:
    if not TAG_PATTERN.fullmatch(tag) or kind not in KINDS:
        raise RecoveryError("unsafe host-fault ownership identity")
    root = runtime_parent / f"{tag}-{kind}"
    return {
        "tag": tag,
        "kind": kind,
        "action": KINDS[kind]["action"],
        "port": KINDS[kind]["port"],
        "root": str(root),
        "data": str(root / "pgdata"),
        "socket_dir": str(root / "socket"),
        "mount_source": tag,
    }


def _state_directory(state_root: Path, tag: str, kind: str) -> Path:
    return state_root / f"{tag}-{kind}"


def _metadata_path(state_root: Path, tag: str, kind: str) -> Path:
    return _state_directory(state_root, tag, kind) / "metadata.json"


def _claim_staging_directories(state_root: Path, tag: str, kind: str) -> list[Path]:
    pattern = re.compile(
        rf"\.{re.escape(tag)}-{re.escape(kind)}\.claim\.[0-9]+\.tmp\Z"
    )
    return sorted(
        path for path in state_root.iterdir() if pattern.fullmatch(path.name)
    )


def _remove_claim_staging(
    state_root: Path,
    tag: str,
    kind: str,
    *,
    owner_uid: int,
    owner_gid: int,
) -> None:
    for directory in _claim_staging_directories(state_root, tag, kind):
        _validate_directory(directory, uid=owner_uid, gid=owner_gid, mode=0o700)
        for path in directory.iterdir():
            metadata = path.lstat()
            allowed_name = path.name == "metadata.json" or re.fullmatch(
                r"\.metadata\.json\.[0-9]+\.tmp", path.name
            )
            if (
                not allowed_name
                or path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != owner_uid
                or metadata.st_gid != owner_gid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise RecoveryError(
                    f"unowned file exists in host-fault claim staging: {path.name}"
                )
            path.unlink()
        directory.rmdir()
    _fsync_directory(state_root)


def _remove_owned_metadata_temps(
    directory: Path, *, owner_uid: int, owner_gid: int
) -> None:
    pattern = re.compile(r"\.metadata\.json\.[0-9]+\.tmp\Z")
    for path in directory.iterdir():
        if path.name == "metadata.json":
            continue
        metadata = path.lstat()
        if (
            not pattern.fullmatch(path.name)
            or path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != owner_uid
            or metadata.st_gid != owner_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RecoveryError(f"unowned file exists in host-fault state: {path.name}")
        path.unlink()
    _fsync_directory(directory)


def _atomic_write(
    path: Path, payload: dict[str, object], *, owner_uid: int, owner_gid: int
) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        if os.geteuid() == 0:
            os.fchown(descriptor, owner_uid, owner_gid)
        value = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _load_metadata(
    state_root: Path,
    runtime_parent: Path,
    tag: str,
    kind: str,
    *,
    owner_uid: int,
    owner_gid: int,
) -> dict[str, object]:
    directory = _state_directory(state_root, tag, kind)
    _validate_directory(directory, uid=owner_uid, gid=owner_gid, mode=0o700)
    path = _metadata_path(state_root, tag, kind)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or metadata.st_nlink != 1:
        raise RecoveryError("host-fault metadata is not a single regular file")
    if metadata.st_uid != owner_uid or metadata.st_gid != owner_gid:
        raise RecoveryError("host-fault metadata ownership mismatch")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RecoveryError("host-fault metadata mode mismatch")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError("host-fault metadata is unreadable") from exc
    expected = _expected(tag, kind, runtime_parent)
    expected_keys = {
        "schema",
        *expected.keys(),
        "phase",
        "helper_pid",
        "helper_start_ticks",
        "postgres_pid",
        "postgres_start_ticks",
        "mount_expected",
        "revision",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise RecoveryError("host-fault metadata shape mismatch")
    if payload.get("schema") != SCHEMA or any(payload.get(key) != value for key, value in expected.items()):
        raise RecoveryError("host-fault metadata ownership identity mismatch")
    if payload.get("phase") not in PHASES:
        raise RecoveryError("host-fault metadata phase mismatch")
    for key in ("helper_pid", "helper_start_ticks", "postgres_pid", "postgres_start_ticks", "revision"):
        if type(payload.get(key)) is not int or int(payload[key]) < 0:
            raise RecoveryError(f"host-fault metadata field is invalid: {key}")
    if int(payload["helper_pid"]) <= 1 or int(payload["helper_start_ticks"]) <= 0:
        raise RecoveryError("host-fault helper identity is invalid")
    if int(payload["revision"]) < 1:
        raise RecoveryError("host-fault metadata revision is invalid")
    if (int(payload["postgres_pid"]) == 0) != (int(payload["postgres_start_ticks"]) == 0):
        raise RecoveryError("isolated PostgreSQL identity is incomplete")
    if payload["phase"] in {"postgres_started", "running", "completed"} and not int(
        payload["postgres_pid"]
    ):
        raise RecoveryError("isolated PostgreSQL identity is missing for the recorded phase")
    if type(payload.get("mount_expected")) is not bool:
        raise RecoveryError("host-fault mount expectation is invalid")
    return payload


def claim(
    *,
    state_root: Path,
    runtime_parent: Path,
    tag: str,
    kind: str,
    helper_pid: int,
    owner_uid: int,
    owner_gid: int,
    mount_expected: bool,
    test_failpoint: str | None = None,
) -> None:
    expected = _expected(tag, kind, runtime_parent)
    helper_start_ticks = _process_start_ticks(helper_pid)
    if helper_pid <= 1 or helper_start_ticks is None:
        raise RecoveryError("host-fault helper process identity is not live")
    _ensure_state_root(state_root, owner_uid=owner_uid, owner_gid=owner_gid)
    directory = _state_directory(state_root, tag, kind)
    root = Path(str(expected["root"]))
    if (
        directory.exists()
        or directory.is_symlink()
        or root.exists()
        or root.is_symlink()
        or _claim_staging_directories(state_root, tag, kind)
    ):
        raise RecoveryError("tagged host-fault resource already exists")
    staging = state_root / f".{tag}-{kind}.claim.{os.getpid()}.tmp"
    staging.mkdir(mode=0o700)
    if os.geteuid() == 0:
        os.chown(staging, owner_uid, owner_gid)
    _fsync_directory(state_root)
    if test_failpoint == "after_staging_mkdir":
        os._exit(97)
    payload: dict[str, object] = {
        "schema": SCHEMA,
        **expected,
        "phase": "claimed",
        "helper_pid": helper_pid,
        "helper_start_ticks": helper_start_ticks,
        "postgres_pid": 0,
        "postgres_start_ticks": 0,
        "mount_expected": mount_expected,
        "revision": 1,
    }
    _atomic_write(
        staging / "metadata.json",
        payload,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    if test_failpoint == "after_staging_metadata":
        os._exit(98)
    os.rename(staging, directory)
    _fsync_directory(state_root)


def update(
    *,
    state_root: Path,
    runtime_parent: Path,
    tag: str,
    kind: str,
    helper_pid: int,
    phase: str,
    postgres_pid: int,
    owner_uid: int,
    owner_gid: int,
) -> None:
    if phase not in PHASES:
        raise RecoveryError("unsafe host-fault phase")
    payload = _load_metadata(
        state_root,
        runtime_parent,
        tag,
        kind,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    if payload["helper_pid"] != helper_pid or payload["helper_start_ticks"] != _process_start_ticks(helper_pid):
        raise RecoveryError("host-fault helper ownership changed")
    postgres_start_ticks = 0
    if postgres_pid:
        postgres_start_ticks = _process_start_ticks(postgres_pid) or 0
        if postgres_start_ticks == 0:
            raise RecoveryError("isolated PostgreSQL process identity is not live")
    payload.update(
        phase=phase,
        postgres_pid=postgres_pid,
        postgres_start_ticks=postgres_start_ticks,
        revision=int(payload["revision"]) + 1,
    )
    _atomic_write(
        _metadata_path(state_root, tag, kind),
        payload,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )


def _under_root(candidate: str, root: Path) -> bool:
    cleaned = candidate.removesuffix(" (deleted)")
    return cleaned == str(root) or cleaned.startswith(str(root) + "/")


def _process_references_root(pid: int, root: Path) -> bool:
    if any(_under_root(argument, root) for argument in _process_cmdline(pid)):
        return True
    for name in ("cwd", "root"):
        try:
            if _under_root(os.readlink(f"/proc/{pid}/{name}"), root):
                return True
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            pass
    try:
        descriptors = list(Path(f"/proc/{pid}/fd").iterdir())
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    for descriptor in descriptors:
        try:
            if _under_root(os.readlink(descriptor), root):
                return True
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
    try:
        mappings = Path(f"/proc/{pid}/maps").read_text(
            encoding="utf-8", errors="replace"
        )
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        mappings = ""
    if any(_under_root(line.rsplit(maxsplit=1)[-1], root) for line in mappings.splitlines()):
        return True
    return False


def _discover_owned_processes(
    root: Path,
    *,
    workload_uid: int,
    ignored_pids: set[int],
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in ignored_pids or not _process_references_root(pid, root):
            continue
        try:
            uid = entry.stat().st_uid
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        cmdline = _process_cmdline(pid)
        allowed_runuser = (
            uid == 0
            and any(Path(item).name == "runuser" for item in cmdline[:2])
            and "postgres" in cmdline
        )
        allowed_root_tool = (
            uid == 0
            and any(
                Path(item).name in {"mount", "umount", "install", "python3", "grep", "sed"}
                for item in cmdline[:2]
            )
            and any(_under_root(item, root) for item in cmdline)
        )
        if uid != workload_uid and not allowed_runuser and not allowed_root_tool:
            raise RecoveryError(f"unowned process {pid} references tagged root")
        start_ticks = _process_start_ticks(pid)
        if start_ticks is not None:
            result.append((pid, start_ticks))
    return result


def _terminate_pid(pid: int, start_ticks: int, *, force: bool = False) -> None:
    if not _process_is_active(pid, start_ticks):
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return


def _wait_for_exit(processes: list[tuple[int, int]], timeout: float) -> list[tuple[int, int]]:
    deadline = time.monotonic() + timeout
    remaining = processes
    while remaining and time.monotonic() < deadline:
        remaining = [item for item in remaining if _process_is_active(item[0], item[1])]
        if remaining:
            time.sleep(0.05)
    return remaining


def _mount_records(root: Path) -> list[tuple[str, str, str]]:
    records: list[tuple[str, str, str]] = []
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RecoveryError("cannot inspect mount ownership") from exc
    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 5 or len(right_fields) < 2:
            continue
        target = left_fields[4].replace("\\040", " ")
        if _under_root(target, root):
            records.append((target, right_fields[0], right_fields[1]))
    return records


def _port_is_listening(port: int) -> bool:
    expected = f"{port:04X}"
    for source in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = source.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) >= 4 and fields[1].rsplit(":", 1)[-1] == expected and fields[3] == "0A":
                return True
    return False


def _remove_tree(root: Path, *, workload_uid: int, workload_gid: int) -> None:
    if not root.exists() and not root.is_symlink():
        return
    metadata = root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise RecoveryError("tagged runtime root is not a real directory")
    if metadata.st_uid != workload_uid or metadata.st_gid != workload_gid:
        raise RecoveryError("tagged runtime root ownership mismatch")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RecoveryError("tagged runtime root mode mismatch")
    shutil.rmtree(root)


def recover_one(
    *,
    state_root: Path,
    runtime_parent: Path,
    tag: str,
    kind: str,
    caller_pid: int,
    owner_uid: int,
    owner_gid: int,
    workload_uid: int,
    workload_gid: int,
    test_mode: bool,
) -> None:
    expected = _expected(tag, kind, runtime_parent)
    directory = _state_directory(state_root, tag, kind)
    root = Path(str(expected["root"]))
    metadata_path = _metadata_path(state_root, tag, kind)
    if not directory.exists() and not directory.is_symlink():
        if (
            root.exists()
            or root.is_symlink()
            or (not test_mode and _port_is_listening(int(expected["port"])))
        ):
            raise RecoveryError("unowned isolated host-fault resource exists")
        _remove_claim_staging(
            state_root,
            tag,
            kind,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )
        return
    _validate_directory(directory, uid=owner_uid, gid=owner_gid, mode=0o700)
    if not metadata_path.exists() or metadata_path.is_symlink():
        if (
            root.exists()
            or root.is_symlink()
            or (not test_mode and _port_is_listening(int(expected["port"])))
        ):
            raise RecoveryError("incomplete host-fault ownership metadata")
        _remove_owned_metadata_temps(
            directory, owner_uid=owner_uid, owner_gid=owner_gid
        )
        if any(directory.iterdir()):
            raise RecoveryError("incomplete host-fault ownership metadata")
        directory.rmdir()
        _remove_claim_staging(
            state_root,
            tag,
            kind,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )
        _fsync_directory(state_root)
        return
    payload = _load_metadata(
        state_root,
        runtime_parent,
        tag,
        kind,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    if not test_mode and payload["mount_expected"] is not True:
        raise RecoveryError("production host-fault metadata does not require its tagged mount")

    helper_pid = int(payload["helper_pid"])
    helper_start = int(payload["helper_start_ticks"])
    if helper_pid != caller_pid and _process_is_active(helper_pid, helper_start):
        cmdline = "\0".join(_process_cmdline(helper_pid))
        if not test_mode and (
            HELPER_BASENAME not in cmdline
            or str(payload["action"]) not in cmdline
            or tag not in cmdline
        ):
            raise RecoveryError("recorded helper PID no longer has the owned command identity")
        _terminate_pid(helper_pid, helper_start, force=True)
        if _wait_for_exit([(helper_pid, helper_start)], 3):
            raise RecoveryError("recorded host-fault helper did not terminate")

    ignored = {os.getpid(), os.getppid(), caller_pid}
    processes = _discover_owned_processes(
        root,
        workload_uid=workload_uid,
        ignored_pids=ignored,
    )
    recorded_postgres_pid = int(payload["postgres_pid"])
    recorded_postgres_start = int(payload["postgres_start_ticks"])
    if recorded_postgres_pid and _process_is_active(
        recorded_postgres_pid, recorded_postgres_start
    ):
        try:
            recorded_uid = Path(f"/proc/{recorded_postgres_pid}").stat().st_uid
        except (FileNotFoundError, ProcessLookupError, PermissionError) as exc:
            raise RecoveryError("recorded PostgreSQL identity became ambiguous") from exc
        if recorded_uid != workload_uid or not _process_references_root(
            recorded_postgres_pid, root
        ):
            raise RecoveryError("recorded PostgreSQL PID no longer owns the tagged root")
        if (recorded_postgres_pid, recorded_postgres_start) not in processes:
            processes.append((recorded_postgres_pid, recorded_postgres_start))
    for pid, start_ticks in processes:
        _terminate_pid(pid, start_ticks)
    remaining = _wait_for_exit(processes, 3)
    for pid, start_ticks in remaining:
        _terminate_pid(pid, start_ticks, force=True)
    remaining = _wait_for_exit(remaining, 3)
    if remaining:
        raise RecoveryError("tag-owned host-fault processes remain alive")

    mounts = _mount_records(root)
    if mounts:
        if len(mounts) != 1 or mounts[0] != (str(root), "tmpfs", str(payload["mount_source"])):
            raise RecoveryError("tagged mount ownership mismatch")
        completed = subprocess.run(("umount", "--", str(root)), capture_output=True, text=True)
        if completed.returncode != 0:
            raise RecoveryError(f"tagged tmpfs unmount failed: {completed.stderr.strip()}")
        if _mount_records(root):
            raise RecoveryError("tagged tmpfs remains mounted")
    elif bool(payload["mount_expected"]) and payload["phase"] not in {"claimed"}:
        # A hard kill can occur immediately before/after mount. Absence is safe only
        # once no process, listener, or nested mount remains and ownership is exact.
        pass

    _remove_tree(root, workload_uid=workload_uid, workload_gid=workload_gid)
    if not test_mode and _port_is_listening(int(payload["port"])):
        raise RecoveryError("isolated PostgreSQL port remains listening")
    _remove_owned_metadata_temps(
        directory, owner_uid=owner_uid, owner_gid=owner_gid
    )
    metadata_path.unlink()
    _fsync_directory(directory)
    directory.rmdir()
    _remove_claim_staging(
        state_root,
        tag,
        kind,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    _fsync_directory(state_root)


def _paths(args: argparse.Namespace) -> tuple[Path, Path, int, int, int, int, bool]:
    test_mode = bool(args.test_mode)
    if test_mode:
        if args.state_root is None or args.runtime_root is None:
            raise RecoveryError("test mode requires explicit state and runtime roots")
        state_root = args.state_root.resolve()
        runtime_root = args.runtime_root.resolve()
        owner_uid = os.geteuid()
        owner_gid = os.getegid()
        workload_uid = os.geteuid() if args.workload_uid is None else args.workload_uid
        workload_gid = os.getegid()
    else:
        if args.state_root is not None or args.runtime_root is not None or args.workload_uid is not None:
            raise RecoveryError("production host-fault roots cannot be overridden")
        if os.geteuid() != 0:
            raise RecoveryError("host-fault recovery must run as root")
        state_root = DEFAULT_STATE_ROOT
        runtime_root = DEFAULT_RUNTIME_ROOT
        owner_uid = 0
        owner_gid = 0
        import pwd

        postgres = pwd.getpwnam("postgres")
        workload_uid = postgres.pw_uid
        workload_gid = postgres.pw_gid
    return (
        state_root,
        runtime_root,
        owner_uid,
        owner_gid,
        workload_uid,
        workload_gid,
        test_mode,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("claim", "update", "recover"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--kind", choices=tuple(KINDS))
    parser.add_argument("--helper-pid", type=int)
    parser.add_argument("--phase", choices=tuple(sorted(PHASES)))
    parser.add_argument("--postgres-pid", type=int, default=0)
    parser.add_argument("--caller-pid", type=int, default=0)
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--workload-uid", type=int)
    parser.add_argument("--no-mount", action="store_true")
    parser.add_argument(
        "--test-failpoint",
        choices=("after_staging_mkdir", "after_staging_metadata"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        (
            state_root,
            runtime_root,
            owner_uid,
            owner_gid,
            workload_uid,
            workload_gid,
            test_mode,
        ) = _paths(args)
        _assert_isolated_runtime(test_mode=test_mode)
        if args.no_mount and not test_mode:
            raise RecoveryError("--no-mount is test-only")
        if args.test_failpoint and not test_mode:
            raise RecoveryError("host-fault claim failpoints are test-only")
        if not TAG_PATTERN.fullmatch(args.tag):
            raise RecoveryError("unsafe matrix ownership tag")
        if args.command in {"claim", "update"} and args.kind is None:
            raise RecoveryError("claim/update requires --kind")
        if args.command == "claim":
            if not args.helper_pid or args.phase or args.postgres_pid:
                raise RecoveryError("invalid host-fault claim arguments")
            claim(
                state_root=state_root,
                runtime_parent=runtime_root,
                tag=args.tag,
                kind=args.kind,
                helper_pid=args.helper_pid,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
                mount_expected=not args.no_mount,
                test_failpoint=args.test_failpoint,
            )
        elif args.command == "update":
            if not args.helper_pid or not args.phase or args.test_failpoint:
                raise RecoveryError("invalid host-fault update arguments")
            update(
                state_root=state_root,
                runtime_parent=runtime_root,
                tag=args.tag,
                kind=args.kind,
                helper_pid=args.helper_pid,
                phase=args.phase,
                postgres_pid=args.postgres_pid,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
        else:
            if (
                args.helper_pid
                or args.phase
                or args.postgres_pid
                or args.no_mount
                or args.test_failpoint
            ):
                raise RecoveryError("invalid host-fault recovery arguments")
            kinds = (args.kind,) if args.kind else tuple(KINDS)
            _ensure_state_root(
                state_root, owner_uid=owner_uid, owner_gid=owner_gid
            )
            for kind in kinds:
                recover_one(
                    state_root=state_root,
                    runtime_parent=runtime_root,
                    tag=args.tag,
                    kind=kind,
                    caller_pid=args.caller_pid,
                    owner_uid=owner_uid,
                    owner_gid=owner_gid,
                    workload_uid=workload_uid,
                    workload_gid=workload_gid,
                    test_mode=test_mode,
                )
    except (RecoveryError, OSError, subprocess.SubprocessError) as exc:
        print(f"host-fault recovery failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
