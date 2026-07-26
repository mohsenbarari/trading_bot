#!/usr/bin/env python3
"""Closed, reversible capacity-fault actuator for the disposable WA-FI host.

The actuator reserves blocks only in the campaign's dedicated mounted storage
filesystem.  It never resizes, detaches, formats, deletes, or otherwise
mutates a volume.  A root-owned marker is installed *before* allocation so
the WebApp writer fence closes before the mount reaches its hard watermark;
cleanup releases the reserve before removing that marker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any


ROLE = "webapp_fi"
REPO_ROOT = Path("/srv/trading-bot-three-site/current")
ENV_FILE = Path("/root/secure-envs/full-matrix/roles/webapp-fi.env")
STATE_FILE = Path("/root/secure-envs/full-matrix/capacity-fault-webapp-fi.json")
DATA_ROOT = Path("/srv/trading-bot-three-site-staging-data")
MARKER_NAME = "guard.json"
RESERVE_NAME = "capacity-reserve.bin"
MARKER_SCHEMA = "three-site-full-matrix-capacity-guard-v1"
STATE_SCHEMA = "three-site-full-matrix-capacity-fault-state-v1"
MINIMUM_FREE_BYTES = 1024 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,190}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class CapacityFaultError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CapacityFaultError("duplicate JSON field")
        value[key] = item
    return value


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, value: dict[str, Any], *, mode: int) -> None:
    parent = path.parent
    metadata = parent.lstat()
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise CapacityFaultError("capacity fault parent is unsafe")
    raw = _json_bytes(value)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise CapacityFaultError("capacity fault state write was incomplete")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise CapacityFaultError("capacity fault state mode is unsafe")


def _read_private_json(path: Path, *, label: str) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 2 <= metadata.st_size <= 16 * 1024
        ):
            raise CapacityFaultError(f"{label} is unsafe")
        raw = os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise CapacityFaultError(f"{label} changed while reading")
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapacityFaultError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise CapacityFaultError(f"{label} is not an object")
    return value


def _read_env() -> dict[str, str]:
    descriptor = os.open(ENV_FILE, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 2 <= metadata.st_size <= 1024 * 1024
        ):
            raise CapacityFaultError("pinned role environment is unsafe")
        raw = os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise CapacityFaultError("pinned role environment changed while reading")
    values: dict[str, str] = {}
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise CapacityFaultError("pinned role environment is not UTF-8") from exc
    for line in lines:
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name in values or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise CapacityFaultError("pinned role environment is malformed")
        values[name] = value
    required = {"STAGING_DATA_ROOT", "STAGING_STORAGE_NAMESPACE", "STAGING_RELEASE_SHA"}
    if not required <= set(values):
        raise CapacityFaultError("pinned role environment lacks capacity binding")
    return values


def _verify_release(release_sha: str) -> None:
    if SHA40_RE.fullmatch(release_sha) is None:
        raise CapacityFaultError("release SHA is invalid")
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode != 0 or result.stderr or result.stdout.strip() != release_sha:
        raise CapacityFaultError("pinned host release differs")
    dirty = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain=v1", "--untracked-files=all"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=30,
    )
    if dirty.returncode != 0 or dirty.stderr or dirty.stdout:
        raise CapacityFaultError("pinned host release is dirty")


def _storage_context(release_sha: str) -> tuple[Path, Path, Path]:
    values = _read_env()
    if values["STAGING_RELEASE_SHA"] != release_sha:
        raise CapacityFaultError("requested release differs from role environment")
    root = Path(values["STAGING_DATA_ROOT"])
    namespace = values["STAGING_STORAGE_NAMESPACE"]
    if root != DATA_ROOT or NAME_RE.fullmatch(namespace) is None:
        raise CapacityFaultError("role storage identity is not the dedicated campaign mount")
    storage = root / namespace / ROLE
    guard = storage / "capacity-guard"
    for candidate in (root, root / namespace, storage):
        metadata = candidate.lstat()
        if candidate.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0:
            raise CapacityFaultError("campaign storage topology is unsafe")
    guard.mkdir(mode=0o755, exist_ok=True)
    metadata = guard.lstat()
    if (
        guard.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise CapacityFaultError("capacity guard directory is unsafe")
    # The reserve and every affected service plane must live on one dedicated
    # mount.  This proves the test is not an unrelated root-disk simulation.
    device = guard.stat().st_dev
    for name in ("postgres", "redis", "uploads", "audit"):
        candidate = storage / name
        metadata = candidate.lstat()
        if candidate.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or candidate.stat().st_dev != device:
            raise CapacityFaultError("capacity resource plane is outside the guarded mount")
    return storage, guard, guard / RESERVE_NAME


def _space(path: Path) -> tuple[int, int]:
    state = os.statvfs(path)
    block = int(state.f_frsize)
    return int(state.f_blocks) * block, int(state.f_bavail) * block


def _state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "release_sha", "operation_id", "phase",
        "hard_limit_bytes", "storage_total_bytes", "reserve_name",
    }
    if (
        set(value) != fields
        or value.get("schema") != STATE_SCHEMA
        or NAME_RE.fullmatch(str(value.get("campaign_id") or "")) is None
        or SHA40_RE.fullmatch(str(value.get("release_sha") or "")) is None
        or UUID_RE.fullmatch(str(value.get("operation_id") or "")) is None
        or value.get("phase") not in {"preparing", "armed"}
        or value.get("reserve_name") != RESERVE_NAME
        or any(type(value.get(name)) is not int or int(value[name]) <= 0 for name in ("hard_limit_bytes", "storage_total_bytes"))
        or int(value["hard_limit_bytes"]) >= int(value["storage_total_bytes"])
    ):
        raise CapacityFaultError("capacity fault retained state is invalid")
    return dict(value)


def _marker(state: dict[str, Any], *, available: int) -> dict[str, Any]:
    return {
        "schema": MARKER_SCHEMA,
        "state": state["phase"],
        "campaign_id": state["campaign_id"],
        "release_sha": state["release_sha"],
        "operation_id": state["operation_id"],
        "role": ROLE,
        "storage_total_bytes": state["storage_total_bytes"],
        "available_bytes": available,
        "hard_limit_bytes": state["hard_limit_bytes"],
    }


def _state_matches(state: dict[str, Any], args: argparse.Namespace) -> None:
    if (
        state["campaign_id"] != args.campaign_id
        or state["release_sha"] != args.release_sha
        or state["operation_id"] != args.operation_id
    ):
        raise CapacityFaultError("capacity fault request does not own retained state")


def _arm(args: argparse.Namespace) -> dict[str, Any]:
    if _read_private_json(STATE_FILE, label="capacity fault state") is not None:
        raise CapacityFaultError("another capacity fault is still retained")
    _verify_release(args.release_sha)
    _, guard, reserve = _storage_context(args.release_sha)
    marker = guard / MARKER_NAME
    if marker.exists() or marker.is_symlink() or reserve.exists() or reserve.is_symlink():
        raise CapacityFaultError("capacity guard has unowned residue")
    total, available = _space(guard)
    hard_limit = max(MINIMUM_FREE_BYTES, total // 50)
    if available <= hard_limit + 64 * 1024 * 1024:
        raise CapacityFaultError("dedicated storage lacks the protected cleanup floor")
    state = {
        "schema": STATE_SCHEMA,
        "campaign_id": args.campaign_id,
        "release_sha": args.release_sha,
        "operation_id": args.operation_id,
        "phase": "preparing",
        "hard_limit_bytes": hard_limit,
        "storage_total_bytes": total,
        "reserve_name": RESERVE_NAME,
    }
    _write_atomic(STATE_FILE, state, mode=0o600)
    # Closing the marker first is intentional: concurrent API writers are
    # fenced before block allocation can reduce the filesystem headroom.
    _write_atomic(marker, _marker(state, available=available), mode=0o444)
    descriptor = os.open(reserve, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.posix_fallocate(descriptor, 0, available - hard_limit)
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    else:
        os.close(descriptor)
    total_after, available_after = _space(guard)
    if total_after != total or available_after > hard_limit:
        raise CapacityFaultError("reserve did not establish the bounded hard watermark")
    state["phase"] = "armed"
    _write_atomic(STATE_FILE, state, mode=0o600)
    _write_atomic(marker, _marker(state, available=available_after), mode=0o444)
    return {
        "status": "armed",
        "operation_id": args.operation_id,
        "storage_total_bytes": total,
        "available_bytes": available_after,
        "hard_limit_bytes": hard_limit,
        "marker_sha256": hashlib.sha256(marker.read_bytes()).hexdigest(),
    }


def _status(args: argparse.Namespace) -> dict[str, Any]:
    value = _read_private_json(STATE_FILE, label="capacity fault state")
    if value is None:
        return {"status": "clear", "operation_id": args.operation_id}
    state = _state(value)
    _state_matches(state, args)
    _, guard, reserve = _storage_context(args.release_sha)
    marker = guard / MARKER_NAME
    total, available = _space(guard)
    if total != state["storage_total_bytes"] or not reserve.is_file() or not marker.is_file():
        raise CapacityFaultError("retained capacity fault no longer proves its storage state")
    return {
        "status": state["phase"],
        "operation_id": args.operation_id,
        "storage_total_bytes": total,
        "available_bytes": available,
        "hard_limit_bytes": state["hard_limit_bytes"],
        "marker_sha256": hashlib.sha256(marker.read_bytes()).hexdigest(),
    }


def _disarm(args: argparse.Namespace) -> dict[str, Any]:
    value = _read_private_json(STATE_FILE, label="capacity fault state")
    if value is None:
        return {"status": "clear", "operation_id": args.operation_id}
    state = _state(value)
    _state_matches(state, args)
    _verify_release(args.release_sha)
    _, guard, reserve = _storage_context(args.release_sha)
    marker = guard / MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        raise CapacityFaultError("capacity fault cleanup cannot prove exact owned residue")
    if reserve.exists() or reserve.is_symlink():
        if reserve.is_symlink() or not reserve.is_file():
            raise CapacityFaultError("capacity reserve is unsafe")
        metadata = reserve.lstat()
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise CapacityFaultError("capacity reserve is unsafe")
        # Preserve the writer fence while blocks are released and only open it
        # after the hard limit is demonstrably cleared.
        reserve.unlink()
        _fsync_directory(guard)
    elif state["phase"] != "preparing":
        raise CapacityFaultError("armed capacity fault lost its exact reserve")
    # A crash after marker/state creation but before reserve creation is also
    # recoverable: the marker held writers closed, and this re-check proves the
    # protected floor before it can be removed.
    total, available = _space(guard)
    if total != state["storage_total_bytes"] or available <= state["hard_limit_bytes"]:
        raise CapacityFaultError("capacity reserve release did not restore protected headroom")
    marker.unlink()
    _fsync_directory(guard)
    STATE_FILE.unlink()
    _fsync_directory(STATE_FILE.parent)
    return {
        "status": "cleared",
        "operation_id": args.operation_id,
        "storage_total_bytes": total,
        "available_bytes": available,
        "hard_limit_bytes": state["hard_limit_bytes"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("arm", "status", "disarm"))
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    if (
        NAME_RE.fullmatch(args.campaign_id) is None
        or SHA40_RE.fullmatch(args.release_sha) is None
        or UUID_RE.fullmatch(args.operation_id) is None
    ):
        raise CapacityFaultError("capacity fault request identity is invalid")
    if args.action in {"arm", "disarm"}:
        expected = f"capacity-fault:{args.operation_id}:{ROLE}:{args.action}:{args.release_sha}"
        if not args.apply or args.confirm != expected:
            raise CapacityFaultError("capacity fault mutation lacks exact confirmation")
    result = {"arm": _arm, "status": _status, "disarm": _disarm}[args.action](args)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CapacityFaultError, OSError, subprocess.SubprocessError):
        print(json.dumps({"status": "blocked", "error_class": "CapacityFaultError"}, sort_keys=True))
        raise SystemExit(1)
