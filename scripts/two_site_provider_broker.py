#!/usr/bin/env python3
"""Bounded local bridge from sandboxed Codex to the pinned Gemini pipeline.

The long-lived server is started outside the Codex sandbox.  Its atomic,
single-consumer filesystem queue accepts only one exact proposal-pipeline
operation; it never accepts shell text, arbitrary executables, CLI overrides,
Git operations, or paths outside the integration task directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable, Mapping

try:
    from scripts.two_site_assignment_scope import (
        AssignmentScope,
        AssignmentScopeError,
        load_active_assignment_scope,
        validate_task_scope,
    )
    from scripts.two_site_preparation_contract import (
        PreparationConfigError,
        PreparationStartup,
    )
    from scripts.two_site_proposal_pipeline import (
        expected_role_model,
        validate_quota_evidence,
    )
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        MODEL,
        WORKER_MODEL,
        ProposalError,
        bounded_file_bytes,
        capture_bounded_process,
        git,
        load_task,
        unique_json,
        write_json,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from two_site_assignment_scope import (  # type: ignore[no-redef]
        AssignmentScope,
        AssignmentScopeError,
        load_active_assignment_scope,
        validate_task_scope,
    )
    from two_site_preparation_contract import (  # type: ignore[no-redef]
        PreparationConfigError,
        PreparationStartup,
    )
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        expected_role_model,
        validate_quota_evidence,
    )
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        MODEL,
        WORKER_MODEL,
        ProposalError,
        bounded_file_bytes,
        capture_bounded_process,
        git,
        load_task,
        unique_json,
        write_json,
    )


ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refactor/two-site-architecture"
STATE_REL = Path(".local/two-site-refactor/provider-broker")
PIPELINE_REL = Path("scripts/two_site_proposal_pipeline.py")
TASK_PREFIX = Path(".local/two-site-refactor/tasks")
REQUEST_KEYS = {
    "schema_version", "request_id", "task_path", "task_sha256", "run_prefix",
    "timeout", "source_head", "assignment_id", "assignment_sha256",
    "controller_run_id", "created_at",
}
RESPONSE_KEYS = {
    "schema_version", "request_id", "run_prefix", "source_head", "state",
    "exit_code", "stdout", "stderr", "finished_at",
}
RECEIPT_KEYS = {
    "schema_version", "request", "request_sha256", "response",
    "response_sha256", "quota_evidence", "finished_at",
}
REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}")
RUN_PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}")
HEAD_RE = re.compile(r"[0-9a-f]{40}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
CYCLE_RUN_RE = re.compile(r"cycle-[0-9]{4,8}-[0-9]{8}T[0-9]{6}Z-[0-9]{1,10}")
MAINTENANCE_RUN_RE = re.compile(r"maint-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}")
CONTROLLER_RUN_RE = re.compile(
    r"(?:cycle-[0-9]{4,8}-[0-9]{8}T[0-9]{6}Z-[0-9]{1,10}"
    r"|maint-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16})"
)
MAX_REQUEST_BYTES = 64_000
MAX_RESPONSE_BYTES = 2_200_000
MAX_PIPELINE_OUTPUT_BYTES = 2_000_000
MAX_ERROR_BYTES = 16_000
MAX_QUOTA_STDOUT_BYTES = 16_384
CANONICAL_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PIPELINE_BASE_KEYS = frozenset({
    "state",
    "task_id",
    "source_head",
    "final_worker_run_id",
    "final_reviewer_run_id",
    "proposal_sha256",
    "changed_paths",
    "proposal_path",
    "reviewer_path",
    "correction_count",
    "blockers",
    "pre_review_test_path",
    "pre_review_test_sha256",
    "pre_review_test_state",
    "semantic_task_fingerprint",
})
QUOTA_ATTEMPT_KEYS = frozenset({"role", "run_id", "status_sha256"})
PIPELINE_QUOTA_KEYS = PIPELINE_BASE_KEYS | {"quota_evidence", "quota_attempt"}
STATUS_MAX_AGE_SECONDS = 30
HEARTBEAT_SECONDS = 10
QUEUE_SCAN_LIMIT = 32
POLL_SECONDS = 0.25
ORPHAN_RESPONSE_RETENTION_SECONDS = 86_400
MAINTENANCE_RECORD_RETENTION_SECONDS = 10 * 86_400
MAX_MAINTENANCE_RECORD_BYTES = 4096
MAX_RECORD_SCAN_ENTRIES = 64
MAX_RECORD_BYTES = 32768
RECORD_FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.json$")
# ``bounded_file_bytes`` deliberately rejects a pathname whose inode changes
# while it is being read.  Status publication replaces the inode atomically,
# so a reader can legitimately observe that protective check in the tiny
# publish window.  Retry only that identifiable transition; malformed or
# untrusted status documents remain terminal errors.
STATUS_READ_RETRY_COUNT = 4
STATUS_READ_RETRY_SECONDS = 0.05
_STOP = False


class BrokerError(ProposalError):
    """Fail-closed local broker contract error."""


def canonical_root(root: Path) -> Path:
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BrokerError("broker root is unavailable") from exc
    if resolved != root or not root.is_dir():
        raise BrokerError("broker root must be a canonical directory")
    return root


def _trusted_directory(path: Path, label: str) -> Path:
    if path.exists() and path.resolve(strict=True) != path:
        raise BrokerError(f"{label} cannot be a symlink")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.resolve(strict=True) != path:
        raise BrokerError(f"{label} cannot be a symlink")
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise BrokerError(f"{label} is not trusted")
    os.chmod(path, 0o700)
    return path


def broker_paths(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = canonical_root(root)
    _trusted_directory(root / ".local", "broker local directory")
    state_root = _trusted_directory(root / STATE_REL, "broker state directory")
    requests = _trusted_directory(state_root / "requests", "broker request directory")
    claimed = _trusted_directory(state_root / "claimed", "broker claimed directory")
    responses = _trusted_directory(state_root / "responses", "broker response directory")
    return state_root, state_root / "status.json", requests, claimed, responses


@contextmanager
def exclusive_broker_lock(path: Path):
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BrokerError("cannot safely open provider broker lock") from exc
    owned = False
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid()):
            raise BrokerError("provider broker lock is not trusted")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BrokerError("another provider broker already owns the lock") from exc
        owned = True
        yield
    finally:
        if owned:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_json_file(root: Path, path: Path, limit: int, label: str) -> dict:
    try:
        value = unique_json(bounded_file_bytes(root, path, limit, label).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BrokerError(f"{label} is not one valid JSON document") from exc
    if not isinstance(value, dict):
        raise BrokerError(f"{label} must be a JSON object")
    return value


def _is_atomic_read_transition(error: BrokerError) -> bool:
    """Whether a guarded reader lost only an atomic replace race.

    ``bounded_file_bytes`` binds both the opened descriptor and the final
    pathname.  It raises ``ProposalError(... changed while it was being
    read)`` if an atomic publisher wins between those observations.  That is
    not a malformed document and it is safe to re-read; every other error
    remains fail-closed.
    """
    cause = error.__cause__
    return isinstance(cause, ProposalError) and str(cause).endswith(
        " changed while it was being read"
    )


def _read_broker_status(root: Path, status_path: Path) -> dict:
    """Read one stable broker-status snapshot without weakening validation."""
    for attempt in range(STATUS_READ_RETRY_COUNT + 1):
        try:
            return _read_json_file(root, status_path, MAX_REQUEST_BYTES, "broker status")
        except BrokerError as exc:
            if not _is_atomic_read_transition(exc) or attempt >= STATUS_READ_RETRY_COUNT:
                raise
            time.sleep(STATUS_READ_RETRY_SECONDS)
    raise AssertionError("unreachable broker-status retry loop")


def _supervisor_stop_acknowledged(root: Path) -> bool:
    """Return true only after the supervisor has durably stopped at a boundary.

    ``STOP`` asks the supervisor not to begin another cycle.  It must not make
    the broker disappear while that supervisor is still waiting for the one
    bounded provider request already in flight.  The supervisor status is
    untrusted operational input, so malformed or unsafe data is fail-closed:
    keep serving rather than mistake it for a completed boundary.
    """
    status_path = root / ".local/two-site-refactor/autopilot/status.json"
    try:
        status = _read_json_file(
            root, status_path, MAX_REQUEST_BYTES, "supervisor stop status"
        )
    except (OSError, ProposalError):
        return False
    return status.get("state") == "STOPPED"


def _atomic_new_json(path: Path, value: Mapping[str, object], limit: int) -> None:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > limit:
        raise BrokerError("broker queue document exceeds byte limit")
    if os.path.lexists(path):
        raise BrokerError("broker queue target already exists")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(temporary, flags, 0o600)
    try:
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise BrokerError("broker queue write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        # link(2) is the no-overwrite publication primitive available in the
        # Python stdlib.  The temporary name is removed immediately, leaving
        # the published queue file single-linked for bounded_file_bytes().
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise BrokerError("broker queue target appeared during publication") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _maintenance_record_directory(root: Path) -> Path:
    state_root, _status, _requests, _claimed, _responses = broker_paths(root)
    return _trusted_directory(state_root / "maintenance-runs", "maintenance record directory")


def _require_maintenance_stopped(root: Path) -> None:
    """A maintenance request is never permission to wake a stopped Supervisor."""
    state_root = root / ".local/two-site-refactor/autopilot"
    if os.path.lexists(state_root / "STOP"):
        raise BrokerError("maintenance requires an explicitly cleared STOP")
    status = _read_json_file(
        root, state_root / "status.json", MAX_REQUEST_BYTES, "supervisor status"
    )
    if (
        status.get("state") != "STOPPED"
        or type(status.get("cycle")) is not int
        or status["cycle"] < 1
    ):
        raise BrokerError("maintenance requires a stopped Supervisor")


@contextmanager
def _exclusive_maintenance_supervisor_lock(root: Path):
    state_root = _trusted_directory(
        root / ".local/two-site-refactor/autopilot", "autopilot state directory"
    )
    with exclusive_broker_lock(state_root / "autopilot.lock"):
        yield


def _maintenance_record_path(root: Path, run_id: str) -> Path:
    if not MAINTENANCE_RUN_RE.fullmatch(run_id):
        raise BrokerError("maintenance run ID is invalid")
    return _maintenance_record_directory(root) / f"{run_id}.json"


def _publish_maintenance_record(root: Path, request: dict) -> None:
    path = _maintenance_record_path(root, request["controller_run_id"])
    _atomic_new_json(
        path, {"schema_version": "1.0", "request": request},
        MAX_MAINTENANCE_RECORD_BYTES,
    )


def _validate_maintenance_record(root: Path, request: dict) -> None:
    _require_maintenance_stopped(root)
    record = _read_json_file(
        root, _maintenance_record_path(root, request["controller_run_id"]),
        MAX_MAINTENANCE_RECORD_BYTES, "maintenance record",
    )
    if (
        set(record) != {"schema_version", "request"}
        or record["schema_version"] != "1.0"
        or record["request"] != request
    ):
        raise BrokerError("maintenance record binding mismatch")


def _prune_maintenance_records(root: Path, *, now: float | None = None) -> int:
    """Bounded removal of only our own aged, regular maintenance records."""
    directory = _maintenance_record_directory(root)
    cutoff = (time.time() if now is None else now) - MAINTENANCE_RECORD_RETENTION_SECONDS
    removed = 0
    inspected = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            inspected += 1
            if inspected > 1024:
                raise BrokerError("maintenance record directory exceeds scan limit")
            if not entry.name.endswith(".json"):
                continue
            run_id = entry.name[:-5]
            if not MAINTENANCE_RUN_RE.fullmatch(run_id):
                continue
            info = entry.stat(follow_symlinks=False)
            if (
                stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.geteuid()
                and info.st_size <= MAX_MAINTENANCE_RECORD_BYTES
                and info.st_mtime < cutoff
            ):
                Path(entry.path).unlink()
                removed += 1
    return removed


def scan_private_record_directory(path: Path) -> list[str]:
    if not isinstance(path, Path):
        raise BrokerError("record directory path must be a Path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BrokerError("record directory is unavailable") from exc
    if resolved != path:
        raise BrokerError("record directory must be a canonical path without symlinks")
    try:
        lstat_before = os.lstat(path)
    except OSError as exc:
        raise BrokerError("cannot lstat record directory") from exc
    if not stat.S_ISDIR(lstat_before.st_mode) or stat.S_ISLNK(lstat_before.st_mode):
        raise BrokerError("record directory must be a directory and not a symlink")
    if lstat_before.st_uid != os.geteuid():
        raise BrokerError("record directory is not owned by current euid")
    if stat.S_IMODE(lstat_before.st_mode) != 0o700:
        raise BrokerError("record directory mode must be 0700")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BrokerError(f"cannot safely open record directory: {exc}") from exc
    try:
        fd_stat = os.fstat(fd)
        if not stat.S_ISDIR(fd_stat.st_mode):
            raise BrokerError("record directory descriptor is not a directory")
        if fd_stat.st_uid != os.geteuid():
            raise BrokerError("record directory descriptor owner mismatch")
        if stat.S_IMODE(fd_stat.st_mode) != 0o700:
            raise BrokerError("record directory descriptor mode mismatch")
        if (fd_stat.st_dev, fd_stat.st_ino) != (lstat_before.st_dev, lstat_before.st_ino):
            raise BrokerError("record directory descriptor does not match path before scan")

        names: list[str] = []
        entry_count = 0
        with os.scandir(fd) as scanner:
            for entry in scanner:
                entry_count += 1
                if entry_count > MAX_RECORD_SCAN_ENTRIES:
                    raise BrokerError("record directory exceeds scan entry limit")
                if not RECORD_FILENAME_RE.fullmatch(entry.name):
                    raise BrokerError(f"record directory contains invalid entry name: {entry.name}")
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise BrokerError(f"cannot stat record entry {entry.name}") from exc
                if entry.is_symlink() or stat.S_ISLNK(entry_stat.st_mode):
                    raise BrokerError(f"record entry {entry.name} cannot be a symlink")
                if not stat.S_ISREG(entry_stat.st_mode):
                    raise BrokerError(f"record entry {entry.name} is not a regular file")
                if entry_stat.st_uid != os.geteuid():
                    raise BrokerError(f"record entry {entry.name} owner mismatch")
                if stat.S_IMODE(entry_stat.st_mode) != 0o600:
                    raise BrokerError(f"record entry {entry.name} mode must be 0600")
                if entry_stat.st_nlink != 1:
                    raise BrokerError(f"record entry {entry.name} has invalid link count")
                if entry_stat.st_size > MAX_RECORD_BYTES:
                    raise BrokerError(f"record entry {entry.name} exceeds byte limit")
                names.append(entry.name)

        try:
            lstat_after = os.lstat(path)
        except OSError as exc:
            raise BrokerError("cannot lstat record directory after scan") from exc
        if not stat.S_ISDIR(lstat_after.st_mode) or stat.S_ISLNK(lstat_after.st_mode):
            raise BrokerError("record directory changed after scan")
        if lstat_after.st_uid != os.geteuid():
            raise BrokerError("record directory owner changed after scan")
        if stat.S_IMODE(lstat_after.st_mode) != 0o700:
            raise BrokerError("record directory mode changed after scan")
        if (lstat_after.st_dev, lstat_after.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
            raise BrokerError("record directory descriptor does not match path after scan")

        return sorted(names)
    finally:
        os.close(fd)


def fsync_private_record_directory(path: Path) -> None:
    if not isinstance(path, Path):
        raise BrokerError("record directory path must be a Path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BrokerError("record directory is unavailable") from exc
    if resolved != path:
        raise BrokerError("record directory must be a canonical path without symlinks")
    try:
        lstat_before = os.lstat(path)
    except OSError as exc:
        raise BrokerError(f"cannot lstat record directory: {exc}") from exc
    if not stat.S_ISDIR(lstat_before.st_mode) or stat.S_ISLNK(lstat_before.st_mode):
        raise BrokerError("record directory must be a directory and not a symlink")
    if lstat_before.st_uid != os.geteuid():
        raise BrokerError("record directory is not owned by current euid")
    if stat.S_IMODE(lstat_before.st_mode) != 0o700:
        raise BrokerError("record directory mode must be 0700")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BrokerError(f"cannot safely open record directory: {exc}") from exc
    try:
        try:
            fd_stat = os.fstat(fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat record directory: {exc}") from exc
        if not stat.S_ISDIR(fd_stat.st_mode):
            raise BrokerError("record directory descriptor is not a directory")
        if fd_stat.st_uid != os.geteuid():
            raise BrokerError("record directory descriptor owner mismatch")
        if stat.S_IMODE(fd_stat.st_mode) != 0o700:
            raise BrokerError("record directory descriptor mode mismatch")
        if (fd_stat.st_dev, fd_stat.st_ino) != (lstat_before.st_dev, lstat_before.st_ino):
            raise BrokerError("record directory descriptor does not match path before fsync")

        try:
            os.fsync(fd)
        except OSError as exc:
            raise BrokerError(f"cannot fsync record directory: {exc}") from exc

        try:
            lstat_after = os.lstat(path)
        except OSError as exc:
            raise BrokerError(f"cannot lstat record directory after fsync: {exc}") from exc
        if not stat.S_ISDIR(lstat_after.st_mode) or stat.S_ISLNK(lstat_after.st_mode):
            raise BrokerError("record directory changed after fsync")
        if lstat_after.st_uid != os.geteuid():
            raise BrokerError("record directory owner changed after fsync")
        if stat.S_IMODE(lstat_after.st_mode) != 0o700:
            raise BrokerError("record directory mode changed after fsync")
        if (lstat_after.st_dev, lstat_after.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
            raise BrokerError("record directory descriptor does not match path after fsync")
    finally:
        os.close(fd)


def _task_relative_path(raw: object) -> Path:
    if not isinstance(raw, str) or not raw or raw != raw.strip() or "\\" in raw:
        raise BrokerError("broker task path is invalid")
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.as_posix() != raw or ".." in candidate.parts:
        raise BrokerError("broker task path must be canonical and relative")
    try:
        candidate.relative_to(TASK_PREFIX)
    except ValueError as exc:
        raise BrokerError("broker task path is outside the task directory") from exc
    if candidate == TASK_PREFIX:
        raise BrokerError("broker task path must name a task file")
    return candidate


def _assignment_from_environment(environment: Mapping[str, str]) -> tuple[str, str]:
    assignment_id = environment.get("TWO_SITE_ASSIGNMENT_ID", "")
    assignment_sha = environment.get("TWO_SITE_ASSIGNMENT_SHA256", "")
    required = environment.get("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "")
    if (required != "1" or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", assignment_id)
            or not SHA_RE.fullmatch(assignment_sha)):
        raise BrokerError("broker requires the active assignment environment")
    return assignment_id, assignment_sha


def make_request(
    root: Path,
    task_path: Path,
    run_prefix: str,
    timeout: int,
    environment: Mapping[str, str],
    *,
    maintenance: bool = False,
) -> dict:
    root = canonical_root(root)
    try:
        raw_task = task_path.relative_to(root).as_posix() if task_path.is_absolute() else task_path.as_posix()
    except ValueError as exc:
        raise BrokerError("broker task path is outside the project root") from exc
    task_rel = _task_relative_path(raw_task)
    if not RUN_PREFIX_RE.fullmatch(run_prefix) or not 10 <= timeout <= 900:
        raise BrokerError("broker run-prefix or timeout is invalid")
    if maintenance:
        if "TWO_SITE_AUTOPILOT_RUN_ID" in environment:
            raise BrokerError("ambiguous controller run ID environment")
        controller_run_id = environment.get("TWO_SITE_MAINTENANCE_RUN_ID", "")
        valid_id = (
            isinstance(controller_run_id, str)
            and MAINTENANCE_RUN_RE.fullmatch(controller_run_id)
        )
    else:
        if "TWO_SITE_MAINTENANCE_RUN_ID" in environment:
            if "TWO_SITE_AUTOPILOT_RUN_ID" in environment:
                raise BrokerError("ambiguous controller run ID environment")
            raise BrokerError("normal broker submit rejects maintenance run ID")
        controller_run_id = environment.get("TWO_SITE_AUTOPILOT_RUN_ID", "")
        valid_id = (
            isinstance(controller_run_id, str)
            and CYCLE_RUN_RE.fullmatch(controller_run_id)
        )
    if not valid_id:
        raise BrokerError("broker requires a canonical controller run ID")
    assignment_id, assignment_sha = _assignment_from_environment(environment)
    task_bytes = bounded_file_bytes(root, root / task_rel, MAX_CONTEXT, "broker task")
    return {
        "schema_version": "1.0",
        "request_id": uuid.uuid4().hex,
        "task_path": task_rel.as_posix(),
        "task_sha256": hashlib.sha256(task_bytes).hexdigest(),
        "run_prefix": run_prefix,
        "timeout": timeout,
        "source_head": git(root, "rev-parse", "HEAD"),
        "assignment_id": assignment_id,
        "assignment_sha256": assignment_sha,
        "controller_run_id": controller_run_id,
        "created_at": time.time(),
    }


def validate_request(
    root: Path,
    request: object,
    *,
    scope_loader: Callable[[Path], AssignmentScope] = load_active_assignment_scope,
) -> tuple[dict, Path, AssignmentScope]:
    root = canonical_root(root)
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise BrokerError("broker request does not match the exact contract")
    if request.get("schema_version") != "1.0":
        raise BrokerError("broker request schema version is invalid")
    if not isinstance(request.get("request_id"), str) or not REQUEST_ID_RE.fullmatch(request["request_id"]):
        raise BrokerError("broker request ID is invalid")
    if not isinstance(request.get("run_prefix"), str) or not RUN_PREFIX_RE.fullmatch(request["run_prefix"]):
        raise BrokerError("broker run-prefix is invalid")
    if type(request.get("timeout")) is not int or not 10 <= request["timeout"] <= 900:
        raise BrokerError("broker timeout is invalid")
    if not isinstance(request.get("source_head"), str) or not HEAD_RE.fullmatch(request["source_head"]):
        raise BrokerError("broker source HEAD is invalid")
    if not isinstance(request.get("task_sha256"), str) or not SHA_RE.fullmatch(request["task_sha256"]):
        raise BrokerError("broker task SHA is invalid")
    if (not isinstance(request.get("controller_run_id"), str)
            or not CONTROLLER_RUN_RE.fullmatch(request["controller_run_id"])):
        raise BrokerError("broker controller run ID is invalid")
    created_at = request.get("created_at")
    if (not isinstance(created_at, (int, float)) or isinstance(created_at, bool)
            or created_at > time.time() + 30 or created_at < time.time() - 300):
        raise BrokerError("broker request is stale or future-dated")
    if git(root, "branch", "--show-current") != BRANCH:
        raise BrokerError(f"broker requires branch {BRANCH}")
    if git(root, "rev-parse", "HEAD") != request["source_head"]:
        raise BrokerError("broker request HEAD drift")
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BrokerError("broker requires a clean integration worktree")

    task_rel = _task_relative_path(request.get("task_path"))
    task_path = root / task_rel
    task_bytes = bounded_file_bytes(root, task_path, MAX_CONTEXT, "broker task")
    if hashlib.sha256(task_bytes).hexdigest() != request["task_sha256"]:
        raise BrokerError("broker task SHA drift")
    try:
        scope = scope_loader(root)
        if (request.get("assignment_id") != scope.assignment_id
                or request.get("assignment_sha256") != scope.sha256):
            raise BrokerError("broker assignment binding mismatch")
        task, _sources = load_task(root, task_path)
        validate_task_scope(scope, task)
    except AssignmentScopeError as exc:
        raise BrokerError("broker assignment validation failed") from exc
    if MAINTENANCE_RUN_RE.fullmatch(request["controller_run_id"]):
        _validate_maintenance_record(root, request)
    return dict(request), task_path, scope


def _overall_timeout(provider_timeout: int) -> int:
    return min(14_400, max(1800, provider_timeout * 12 + 3600))


def execute_pipeline(root: Path, request: dict, task_path: Path, scope: AssignmentScope) -> dict:
    state_root, _status_path, _requests, _claimed, _responses = broker_paths(root)
    pipeline = root / PIPELINE_REL
    if (not pipeline.is_file() or pipeline.resolve(strict=True) != pipeline
            or pipeline.stat(follow_symlinks=False).st_nlink != 1):
        raise BrokerError("proposal pipeline executable is missing or unsafe")
    command = [
        "/usr/bin/python3", str(pipeline), "--root", str(root),
        "--task", str(task_path), "--run-prefix", request["run_prefix"],
        "--timeout", str(request["timeout"]),
    ]
    environment = os.environ.copy()
    environment.pop("TWO_SITE_AUTOPILOT_RUN_ID", None)
    environment.pop("TWO_SITE_MAINTENANCE_RUN_ID", None)
    environment.update({
        "APP_ENV_FILE": "config/unit-test.env.example",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED": "1",
        "TWO_SITE_ASSIGNMENT_ID": scope.assignment_id,
        "TWO_SITE_ASSIGNMENT_SHA256": scope.sha256,
    })
    provenance_variable = (
        "TWO_SITE_MAINTENANCE_RUN_ID"
        if MAINTENANCE_RUN_RE.fullmatch(request["controller_run_id"])
        else "TWO_SITE_AUTOPILOT_RUN_ID"
    )
    environment[provenance_variable] = request["controller_run_id"]
    with tempfile.TemporaryDirectory(prefix="request-", dir=state_root) as temporary:
        temporary_path = Path(temporary)
        with subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            capture_bounded_process(
                process, b"", temporary_path / "stdout", temporary_path / "stderr",
                _overall_timeout(request["timeout"]), max_output=MAX_PIPELINE_OUTPUT_BYTES,
            )
        stdout = bounded_file_bytes(
            state_root, temporary_path / "stdout", MAX_PIPELINE_OUTPUT_BYTES,
            "broker pipeline stdout",
        ).decode("utf-8")
        stderr = bounded_file_bytes(
            state_root, temporary_path / "stderr", MAX_PIPELINE_OUTPUT_BYTES,
            "broker pipeline stderr",
        ).decode("utf-8")
    return {
        "schema_version": "1.0",
        "request_id": request["request_id"],
        "run_prefix": request["run_prefix"],
        "source_head": request["source_head"],
        "state": "COMPLETED",
        "exit_code": int(process.returncode),
        "stdout": stdout,
        "stderr": stderr,
        "finished_at": time.time(),
    }


def _error_response(request: object, exc: BaseException) -> dict:
    request = request if isinstance(request, dict) else {}
    message = (type(exc).__name__ + ": " + str(exc)).encode("utf-8")[:MAX_ERROR_BYTES]
    error = message.decode("utf-8", errors="ignore")
    return {
        "schema_version": "1.0",
        "request_id": request.get("request_id") if isinstance(request.get("request_id"), str) else "0" * 32,
        "run_prefix": request.get("run_prefix") if isinstance(request.get("run_prefix"), str) else "invalid",
        "source_head": request.get("source_head") if isinstance(request.get("source_head"), str) else "0" * 40,
        "state": "BROKER_ERROR",
        "exit_code": 2,
        "stdout": "",
        "stderr": error,
        "finished_at": time.time(),
    }


def _write_status(path: Path, state: str, **fields: object) -> None:
    # The shared runner helper uses one deterministic ``.tmp`` sibling.  A
    # broker heartbeat runs concurrently with a bounded client read, so use a
    # private replacement name here.  Publication is still an atomic
    # rename and readers reject any snapshot that changes underneath them.
    value = {
        "schema_version": "1.0", "state": state, "pid": os.getpid(),
        "updated_at": time.time(), **fields,
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise BrokerError("broker status document exceeds byte limit")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise BrokerError("broker status write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        directory_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                           | getattr(os, "O_CLOEXEC", 0))
        directory = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _signal_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _execute_with_heartbeat(
    status_path: Path,
    executor: Callable[..., dict],
    root: Path,
    request: dict,
    task_path: Path | None = None,
    scope: AssignmentScope | None = None,
) -> dict:
    stopped = threading.Event()
    heartbeat_errors: list[BaseException] = []

    def heartbeat() -> None:
        while not stopped.wait(HEARTBEAT_SECONDS):
            try:
                _write_status(
                    status_path, "RUNNING", request_id=request["request_id"],
                    run_prefix=request["run_prefix"], source_head=request["source_head"],
                )
            except BaseException as exc:  # Preserve failure after the provider call returns.
                heartbeat_errors.append(exc)
                return

    heartbeat_thread = threading.Thread(
        target=heartbeat, name="provider-broker-heartbeat", daemon=True
    )
    heartbeat_thread.start()
    try:
        if task_path is not None and scope is not None:
            response = executor(root, request, task_path, scope)
        else:
            response = executor(root, request)
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=HEARTBEAT_SECONDS + 1)
    if heartbeat_errors:
        raise BrokerError("provider broker heartbeat failed") from heartbeat_errors[0]
    return response


def execute_intent_bound_continuation(root: Path, request: object) -> dict:
    try:
        from scripts.two_site_quota_broker_route import (
            execute_intent_bound_continuation as _impl,
        )
    except ModuleNotFoundError:
        from two_site_quota_broker_route import (  # type: ignore[no-redef]
            execute_intent_bound_continuation as _impl,
        )
    return _impl(root, request)


def _queue_names(path: Path) -> list[str]:
    names: list[str] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name.startswith(".") and entry.name.endswith(".tmp"):
                continue
            names.append(entry.name)
            if len(names) > QUEUE_SCAN_LIMIT:
                raise BrokerError("broker queue exceeds its bounded entry limit")
    return sorted(names)


def _published_queue_file(path: Path, label: str) -> bool:
    """Return false only during atomic link publication; reject other drift."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise BrokerError(f"{label} is not a trusted queue file")
    if info.st_nlink == 2:
        return False
    if info.st_nlink != 1:
        raise BrokerError(f"{label} has an invalid hardlink count")
    return True


def _claim_next(
    requests: Path,
    claimed: Path,
    responses: Path | None = None,
    terminal_quota: Path | None = None,
) -> Path | None:
    if responses is None:
        responses = requests.parent / "responses"
    if terminal_quota is None:
        terminal_quota = requests.parent / "terminal-quota"

    for name in _queue_names(requests):
        if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
            raise BrokerError("broker request queue contains an invalid entry")
        source = requests / name
        destination = claimed / name
        if not _published_queue_file(source, "broker request"):
            return None
        try:
            source_stat = os.lstat(source)
        except OSError as exc:
            raise BrokerError(f"cannot lstat source request file: {exc}") from exc
        if (
            stat.S_ISLNK(source_stat.st_mode)
            or not stat.S_ISREG(source_stat.st_mode)
            or source_stat.st_uid != os.geteuid()
            or stat.S_IMODE(source_stat.st_mode) != 0o600
            or source_stat.st_nlink != 1
        ):
            raise BrokerError("broker request is not a trusted queue file")

        if os.path.lexists(destination):
            raise BrokerError("broker claimed request already exists")
        if os.path.lexists(responses / name):
            raise BrokerError("broker response already exists for request")
        if os.path.lexists(terminal_quota):
            try:
                tq_stat = os.lstat(terminal_quota)
            except OSError as exc:
                raise BrokerError(f"cannot lstat terminal quota directory: {exc}") from exc
            if stat.S_ISLNK(tq_stat.st_mode):
                raise BrokerError("terminal quota directory cannot be a symlink")
            if not stat.S_ISDIR(tq_stat.st_mode):
                raise BrokerError("terminal quota directory must be a directory")
            if tq_stat.st_uid != os.geteuid():
                raise BrokerError("terminal quota directory owner mismatch")
            if stat.S_IMODE(tq_stat.st_mode) != 0o700:
                raise BrokerError("terminal quota directory mode must be 0700")
            try:
                resolved_tq = terminal_quota.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise BrokerError("terminal quota directory is unavailable") from exc
            if resolved_tq != terminal_quota:
                raise BrokerError("terminal quota directory must be a canonical path without symlinks")
            if os.path.lexists(terminal_quota / name):
                raise BrokerError("terminal quota record already exists for request")

        os.replace(source, destination)
        try:
            claimed_stat = os.lstat(destination)
        except OSError as exc:
            raise BrokerError(f"cannot lstat claimed request: {exc}") from exc
        if (
            stat.S_ISLNK(claimed_stat.st_mode)
            or not stat.S_ISREG(claimed_stat.st_mode)
            or claimed_stat.st_uid != os.geteuid()
            or stat.S_IMODE(claimed_stat.st_mode) != 0o600
            or claimed_stat.st_nlink != 1
            or (claimed_stat.st_dev, claimed_stat.st_ino, claimed_stat.st_size)
            != (source_stat.st_dev, source_stat.st_ino, source_stat.st_size)
        ):
            raise BrokerError("claimed request file verification failed")
        fsync_private_record_directory(claimed)
        fsync_private_record_directory(requests)
        return destination
    return None


def _response_path(responses: Path, request_id: str) -> Path:
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise BrokerError("broker response request ID is invalid")
    return responses / f"{request_id}.json"


def _prune_orphan_responses(responses: Path, *, now: float | None = None) -> int:
    """Bound cleanup work while preventing abandoned responses from living forever."""
    current = time.time() if now is None else now
    removed = 0
    inspected = 0
    with os.scandir(responses) as entries:
        for entry in entries:
            if inspected >= QUEUE_SCAN_LIMIT:
                break
            inspected += 1
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if current - info.st_mtime <= ORPHAN_RESPONSE_RETENTION_SECONDS:
                continue
            if stat.S_ISDIR(info.st_mode):
                continue
            os.unlink(entry.path)
            removed += 1
    return removed


def _validate_claimed_request_shape(request: object, expected_request_id: str) -> dict:
    if not isinstance(request, dict):
        raise BrokerError("claimed broker request does not match the exact contract")
    if request.get("request_type") == "QUOTA_CONTINUATION":
        try:
            from scripts.two_site_quota_resume_submission_intent import (
                compute_canonical_request_sha256,
            )
        except ModuleNotFoundError:
            from two_site_quota_resume_submission_intent import (
                compute_canonical_request_sha256,
            )
        try:
            compute_canonical_request_sha256(request)
        except Exception as exc:
            raise BrokerError(f"invalid continuation request shape: {exc}") from exc
    elif set(request) != REQUEST_KEYS:
        raise BrokerError("claimed broker request does not match the exact contract")
    if request.get("schema_version") != "1.0":
        raise BrokerError("claimed broker request schema version is invalid")
    if not isinstance(request.get("request_id"), str) or not REQUEST_ID_RE.fullmatch(request["request_id"]):
        raise BrokerError("claimed broker request ID is invalid")
    if request["request_id"] != expected_request_id:
        raise BrokerError("claimed broker request filename binding is invalid")
    if not isinstance(request.get("run_prefix"), str) or not RUN_PREFIX_RE.fullmatch(request["run_prefix"]):
        raise BrokerError("claimed broker request run-prefix is invalid")
    if type(request.get("timeout")) is not int or not 10 <= request["timeout"] <= 900:
        raise BrokerError("claimed broker request timeout is invalid")
    if not isinstance(request.get("source_head"), str) or not HEAD_RE.fullmatch(request["source_head"]):
        raise BrokerError("claimed broker request source HEAD is invalid")
    if not isinstance(request.get("task_sha256"), str) or not SHA_RE.fullmatch(request["task_sha256"]):
        raise BrokerError("claimed broker request task SHA is invalid")
    if (
        not isinstance(request.get("assignment_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request["assignment_id"])
    ):
        raise BrokerError("claimed broker request assignment ID is invalid")
    if not isinstance(request.get("assignment_sha256"), str) or not SHA_RE.fullmatch(request["assignment_sha256"]):
        raise BrokerError("claimed broker request assignment SHA is invalid")
    if (
        not isinstance(request.get("controller_run_id"), str)
        or not CONTROLLER_RUN_RE.fullmatch(request["controller_run_id"])
    ):
        raise BrokerError("claimed broker request controller run ID is invalid")
    created_at = request.get("created_at")
    if (
        not isinstance(created_at, (int, float))
        or isinstance(created_at, bool)
        or not math.isfinite(created_at)
    ):
        raise BrokerError("claimed broker request created_at is invalid")
    _task_relative_path(request.get("task_path"))
    return request


def _safe_read_existing_response(root: Path, path: Path) -> dict:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BrokerError(f"cannot lstat existing response: {exc}") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size > MAX_RESPONSE_BYTES
    ):
        raise BrokerError("existing broker response is not a trusted queue file")
    return _read_json_file(root, path, MAX_RESPONSE_BYTES, "broker response")


def _recover_interrupted_claims(
    root: Path,
    claimed: Path,
    responses: Path,
    requests: Path | None = None,
    terminal_quota: Path | None = None,
) -> None:
    """Fail ambiguous in-flight work closed; never repeat its provider call."""
    root = canonical_root(root)
    if requests is None:
        requests = claimed.parent / "requests"
    if terminal_quota is None:
        terminal_quota = claimed.parent / "terminal-quota"

    for name in _queue_names(claimed):
        if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
            raise BrokerError("broker claimed queue contains an invalid entry")
        path = claimed / name
        try:
            claim_stat = os.lstat(path)
        except OSError as exc:
            raise BrokerError(f"cannot lstat claimed request: {exc}") from exc
        if (
            stat.S_ISLNK(claim_stat.st_mode)
            or not stat.S_ISREG(claim_stat.st_mode)
            or claim_stat.st_uid != os.geteuid()
            or stat.S_IMODE(claim_stat.st_mode) != 0o600
            or claim_stat.st_nlink != 1
            or claim_stat.st_size > MAX_REQUEST_BYTES
        ):
            raise BrokerError("broker claimed request is not a trusted queue file")

        request = _read_json_file(root, path, MAX_REQUEST_BYTES, "claimed broker request")
        request_id = name[:-5]
        _validate_claimed_request_shape(request, request_id)

        if os.path.lexists(requests / name):
            raise BrokerError("broker request collision in requests directory")

        matching_receipt_present = False
        if os.path.lexists(terminal_quota):
            entries = scan_private_record_directory(terminal_quota)
            if name in entries:
                matching_receipt_present = True

        response_path = _response_path(responses, request_id)

        if request.get("request_type") == "QUOTA_CONTINUATION" and matching_receipt_present:
            raise BrokerError("unexpected terminal quota receipt for continuation request")

        if matching_receipt_present:
            receipt = read_terminal_quota_record(root, request_id)
            try:
                claimed_canonical = json.dumps(
                    request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
                receipt_req_canonical = json.dumps(
                    receipt["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            except (ValueError, TypeError) as exc:
                raise BrokerError("request contains non-finite numbers") from exc

            if claimed_canonical != receipt_req_canonical:
                raise BrokerError("claimed request does not match receipt request")
            if receipt.get("request_sha256") != hashlib.sha256(claimed_canonical).hexdigest():
                raise BrokerError("claimed request digest mismatch")

            if not os.path.lexists(response_path):
                _atomic_new_json(response_path, receipt["response"], MAX_RESPONSE_BYTES)
                fsync_private_record_directory(responses)
                path.unlink()
                fsync_private_record_directory(claimed)
            else:
                existing_resp = _safe_read_existing_response(root, response_path)
                try:
                    existing_resp_canonical = json.dumps(
                        existing_resp, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode("utf-8")
                    receipt_resp_canonical = json.dumps(
                        receipt["response"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode("utf-8")
                except (ValueError, TypeError) as exc:
                    raise BrokerError("response contains non-finite numbers") from exc

                if existing_resp_canonical != receipt_resp_canonical:
                    raise BrokerError("existing response does not match receipt response")
                if receipt.get("response_sha256") != hashlib.sha256(existing_resp_canonical).hexdigest():
                    raise BrokerError("existing response digest mismatch")

                fsync_private_record_directory(responses)
                path.unlink()
                fsync_private_record_directory(claimed)
        else:
            if not os.path.lexists(response_path):
                _atomic_new_json(
                    response_path,
                    _error_response(
                        request,
                        BrokerError(
                            "broker restarted after an ambiguous in-flight provider call; refusing duplicate execution"
                        ),
                    ),
                    MAX_RESPONSE_BYTES,
                )
                fsync_private_record_directory(responses)
                path.unlink()
                fsync_private_record_directory(claimed)
            else:
                existing_resp = _safe_read_existing_response(root, response_path)
                validate_response(request, existing_resp)
                fsync_private_record_directory(responses)
                path.unlink()
                fsync_private_record_directory(claimed)


def serve(
    root: Path,
    *,
    once: bool = False,
    executor: Callable[[Path, dict, Path, AssignmentScope], dict] = execute_pipeline,
    scope_loader: Callable[[Path], AssignmentScope] = load_active_assignment_scope,
    install_signal_handlers: bool = True,
    quota_continuation_enabled: bool = False,
) -> int:
    if type(quota_continuation_enabled) is not bool:
        raise BrokerError("quota_continuation_enabled must be an exact bool")
    global _STOP
    _STOP = False
    root = canonical_root(root)
    state_root, status_path, requests, claimed, responses = broker_paths(root)
    stop_path = root / ".local/two-site-refactor/autopilot/STOP"
    if install_signal_handlers:
        for handled in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(handled, _signal_stop)
    with exclusive_broker_lock(state_root / "broker.lock"):
        _recover_interrupted_claims(root, claimed, responses)
        _prune_orphan_responses(responses)
        started_at = time.time()
        last_heartbeat = 0.0
        last_prune = time.time()
        _write_status(
            status_path, "IDLE", started_at=started_at,
            quota_continuation_ready=quota_continuation_enabled,
        )
        try:
            # A normal STOP is a supervisor boundary request, not a cancellation
            # of its active broker call.  Exit only after that supervisor has
            # acknowledged STOPPED; ``once`` remains an explicit test/CLI mode.
            while not _STOP and not (
                stop_path.exists()
                and not once
                and _supervisor_stop_acknowledged(root)
            ):
                claimed_path = _claim_next(
                    requests,
                    claimed,
                    responses=responses,
                    terminal_quota=state_root / "terminal-quota",
                )
                if claimed_path is None:
                    if time.time() - last_prune >= 60:
                        _prune_orphan_responses(responses)
                        last_prune = time.time()
                    if time.time() - last_heartbeat >= 10:
                        _write_status(
                            status_path, "IDLE", started_at=started_at,
                            quota_continuation_ready=quota_continuation_enabled,
                        )
                        last_heartbeat = time.time()
                    time.sleep(POLL_SECONDS)
                    continue
                is_continuation = False
                request: object = {}
                response: dict
                try:
                    request = _read_json_file(
                        root, claimed_path, MAX_REQUEST_BYTES, "broker request"
                    )
                    is_continuation = (
                        isinstance(request, dict)
                        and request.get("request_type") == "QUOTA_CONTINUATION"
                    )
                    if is_continuation:
                        if not quota_continuation_enabled:
                            raise BrokerError("quota continuation is disabled")
                        validated = _validate_claimed_request_shape(
                            request, claimed_path.stem
                        )
                        _write_status(
                            status_path, "RUNNING", request_id=validated["request_id"],
                            run_prefix=validated["run_prefix"],
                            source_head=validated["source_head"],
                        )
                        response = _execute_with_heartbeat(
                            status_path, execute_intent_bound_continuation, root, validated
                        )
                        if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
                            raise BrokerError("broker executor returned an invalid response")
                        validate_response(validated, response)
                    else:
                        if request.get("request_id") != claimed_path.stem:
                            raise BrokerError("broker request filename binding is invalid")
                        maintenance = (
                            isinstance(request.get("controller_run_id"), str)
                            and MAINTENANCE_RUN_RE.fullmatch(request["controller_run_id"])
                        )
                        lock = _exclusive_maintenance_supervisor_lock(root) if maintenance else nullcontext()
                        with lock:
                            validated, task_path, scope = validate_request(
                                root, request, scope_loader=scope_loader
                            )
                            _write_status(
                                status_path, "RUNNING", request_id=validated["request_id"],
                                run_prefix=validated["run_prefix"],
                                source_head=validated["source_head"],
                            )
                            response = _execute_with_heartbeat(
                                status_path, executor, root, validated, task_path, scope
                            )
                            if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
                                raise BrokerError("broker executor returned an invalid response")
                except Exception as exc:
                    response = _error_response(request, exc)
                else:
                    if not is_continuation and detect_quota_response(root, validated, response) is not None:
                        publish_terminal_quota_record(root, validated, response)
                request_id = claimed_path.stem
                response_path = _response_path(responses, request_id)
                _atomic_new_json(response_path, response, MAX_RESPONSE_BYTES)
                if is_continuation:
                    fsync_private_record_directory(responses)
                    claimed_path.unlink(missing_ok=True)
                    fsync_private_record_directory(claimed)
                else:
                    claimed_path.unlink(missing_ok=True)
                _write_status(
                    status_path, "IDLE", started_at=started_at,
                    quota_continuation_ready=quota_continuation_enabled,
                    last_request_id=response["request_id"],
                    last_run_prefix=response["run_prefix"],
                    last_exit_code=response["exit_code"],
                    last_finished_at=response["finished_at"],
                )
                if once:
                    break
        finally:
            _write_status(status_path, "STOPPED")
    return 0


def validate_response(request: dict, response: object) -> dict:
    if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
        raise BrokerError("broker response does not match the exact contract")
    if (response.get("schema_version") != "1.0"
            or response.get("request_id") != request["request_id"]
            or response.get("run_prefix") != request["run_prefix"]
            or response.get("source_head") != request["source_head"]
            or response.get("state") not in {"COMPLETED", "BROKER_ERROR"}
            or type(response.get("exit_code")) is not int
            or not isinstance(response.get("stdout"), str)
            or not isinstance(response.get("stderr"), str)
            or not isinstance(response.get("finished_at"), (int, float))):
        raise BrokerError("broker response binding is invalid")
    if len((response["stdout"] + response["stderr"]).encode("utf-8")) > MAX_PIPELINE_OUTPUT_BYTES:
        raise BrokerError("broker response output exceeds byte limit")
    if response["state"] == "BROKER_ERROR" and response["exit_code"] != 2:
        raise BrokerError("broker error response has an invalid exit code")
    return response


def detect_quota_response(
    root: Path, request: object, response: object
) -> dict | None:
    """Pure predicate/detector for a terminal observational Gemini quota Broker response.

    Returns sanitized exact quota evidence dict for a valid response, else None.
    Fails closed on any contract violation, schema drift, or unverified witness.
    Observation only; provides NO replay authority or durable receipt.
    """
    try:
        if not isinstance(request, dict):
            return None
        canonical = canonical_root(root)
        validated = validate_response(request, response)
        if validated.get("state") != "COMPLETED":
            return None
        if type(validated.get("exit_code")) is not int or validated.get("exit_code") != 1:
            return None
        if validated.get("stderr") != "":
            return None
        raw_stdout = validated.get("stdout")
        if not isinstance(raw_stdout, str):
            return None
        stdout_bytes = raw_stdout.encode("utf-8")
        if len(stdout_bytes) > MAX_QUOTA_STDOUT_BYTES:
            return None
        if not raw_stdout.strip():
            return None

        payload = unique_json(raw_stdout)
        if not isinstance(payload, dict) or set(payload.keys()) != PIPELINE_QUOTA_KEYS:
            return None

        if payload.get("state") != "TASK_BLOCKED":
            return None
        if payload.get("blockers") != ["observational quota, no replay authority"]:
            return None
        if payload.get("source_head") != request.get("source_head"):
            return None

        task_rel = _task_relative_path(request.get("task_path"))
        task_path = canonical / task_rel
        task_bytes = bounded_file_bytes(canonical, task_path, MAX_CONTEXT, "broker task")
        if hashlib.sha256(task_bytes).hexdigest() != request.get("task_sha256"):
            return None
        task_data = unique_json(task_bytes.decode("utf-8"))
        if not isinstance(task_data, dict):
            return None
        task_id = task_data.get("task_id")
        if not isinstance(task_id, str) or not task_id or payload.get("task_id") != task_id:
            return None

        attempt = payload.get("quota_attempt")
        if not isinstance(attempt, dict) or set(attempt.keys()) != QUOTA_ATTEMPT_KEYS:
            return None
        role = attempt.get("role")
        if role not in ("worker", "verifier"):
            return None

        run_id = attempt.get("run_id")
        if not isinstance(run_id, str):
            return None
        run_prefix = request.get("run_prefix")
        if not isinstance(run_prefix, str) or not RUN_PREFIX_RE.fullmatch(run_prefix):
            return None

        role_pattern = r"worker" if role == "worker" else r"(?:reviewer|verifier)"
        run_pattern = rf"^{re.escape(run_prefix)}-{role_pattern}-[0-2](?:-retry-[1-2])?$"
        if not re.fullmatch(run_pattern, run_id):
            return None

        status_sha = attempt.get("status_sha256")
        if not isinstance(status_sha, str) or not SHA_RE.fullmatch(status_sha):
            return None

        run_dir = canonical / ".local/two-site-refactor/proposals" / run_id
        if os.path.islink(run_dir) or run_dir.is_symlink() or not run_dir.is_dir():
            return None
        if os.path.lexists(run_dir / "result.json") or os.path.lexists(run_dir / "proposal.patch"):
            return None

        status_path = run_dir / "status.json"
        if os.path.islink(status_path) or status_path.is_symlink():
            return None
        status_bytes = bounded_file_bytes(canonical, status_path, MAX_CONTEXT, "quota status")
        if hashlib.sha256(status_bytes).hexdigest() != status_sha:
            return None

        status = unique_json(status_bytes.decode("utf-8"))
        if not isinstance(status, dict):
            return None

        expected_model = expected_role_model(role, task_id)
        if status.get("run_id") != run_id:
            return None
        if status.get("role") != role:
            return None
        if status.get("head") != request.get("source_head"):
            return None
        if status.get("model") != expected_model:
            return None
        if status.get("state") != "BLOCKED":
            return None
        if status.get("task_id") != task_id:
            return None

        evidence = payload.get("quota_evidence")
        if status.get("quota_evidence") != evidence:
            return None

        if status.get("conversation_id") is not None:
            if not isinstance(status["conversation_id"], str) or status["conversation_id"] != (
                evidence.get("conversation_id") if isinstance(evidence, dict) else None
            ):
                return None

        return validate_quota_evidence(evidence, expected_model)
    except Exception:
        return None


detect_terminal_quota_response = detect_quota_response


def _terminal_quota_directory(root: Path) -> Path:
    state_root, _status, _requests, _claimed, _responses = broker_paths(root)
    directory = state_root / "terminal-quota"
    if not os.path.lexists(directory):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise BrokerError(f"cannot create terminal quota directory: {exc}") from exc
    return directory


def publish_terminal_quota_record(
    root: Path, request: dict, response: dict
) -> dict:
    root = canonical_root(root)
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise BrokerError("broker request does not match the exact contract")
    if request.get("schema_version") != "1.0":
        raise BrokerError("broker request schema version is invalid")
    if not isinstance(request.get("request_id"), str) or not REQUEST_ID_RE.fullmatch(request["request_id"]):
        raise BrokerError("broker request ID is invalid")
    if not isinstance(request.get("run_prefix"), str) or not RUN_PREFIX_RE.fullmatch(request["run_prefix"]):
        raise BrokerError("broker run-prefix is invalid")
    if type(request.get("timeout")) is not int or not 10 <= request["timeout"] <= 900:
        raise BrokerError("broker timeout is invalid")
    if not isinstance(request.get("source_head"), str) or not HEAD_RE.fullmatch(request["source_head"]):
        raise BrokerError("broker source HEAD is invalid")
    if not isinstance(request.get("task_sha256"), str) or not SHA_RE.fullmatch(request["task_sha256"]):
        raise BrokerError("broker task SHA is invalid")
    if (
        not isinstance(request.get("assignment_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request["assignment_id"])
    ):
        raise BrokerError("broker assignment ID is invalid")
    if not isinstance(request.get("assignment_sha256"), str) or not SHA_RE.fullmatch(request["assignment_sha256"]):
        raise BrokerError("broker assignment SHA is invalid")
    if (
        not isinstance(request.get("controller_run_id"), str)
        or not CONTROLLER_RUN_RE.fullmatch(request["controller_run_id"])
    ):
        raise BrokerError("broker controller run ID is invalid")
    created_at = request.get("created_at")
    if (
        not isinstance(created_at, (int, float))
        or isinstance(created_at, bool)
        or not math.isfinite(created_at)
    ):
        raise BrokerError("broker request created_at is invalid")
    _task_relative_path(request.get("task_path"))

    validated_response = validate_response(request, response)
    if validated_response.get("state") != "COMPLETED" or validated_response.get("exit_code") != 1:
        raise BrokerError("terminal quota record requires COMPLETED exit_code=1")
    finished_at = validated_response.get("finished_at")
    if (
        not isinstance(finished_at, (int, float))
        or isinstance(finished_at, bool)
        or not math.isfinite(finished_at)
    ):
        raise BrokerError("broker response finished_at is invalid")

    evidence = detect_quota_response(root, request, validated_response)
    if evidence is None:
        raise BrokerError("terminal quota response not detected")

    directory = _terminal_quota_directory(root)
    existing_entries = scan_private_record_directory(directory)
    if len(existing_entries) >= MAX_RECORD_SCAN_ENTRIES:
        raise BrokerError("terminal quota directory exceeds scan entry limit")

    request_id = request["request_id"]
    filename = f"{request_id}.json"
    if filename in existing_entries:
        raise BrokerError("terminal quota record already exists")
    record_path = directory / filename
    if os.path.lexists(record_path):
        raise BrokerError("terminal quota record target already exists")

    try:
        req_canonical = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        resp_canonical = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except ValueError as exc:
        raise BrokerError("request or response contains non-finite numbers") from exc

    receipt = {
        "schema_version": "1.0",
        "request": request,
        "request_sha256": hashlib.sha256(req_canonical).hexdigest(),
        "response": response,
        "response_sha256": hashlib.sha256(resp_canonical).hexdigest(),
        "quota_evidence": evidence,
        "finished_at": finished_at,
    }

    try:
        encoded_receipt = json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except ValueError as exc:
        raise BrokerError("receipt contains non-finite numbers") from exc
    if len(encoded_receipt) > MAX_RECORD_BYTES:
        raise BrokerError("terminal quota record exceeds maximum record bytes")

    _atomic_new_json(record_path, receipt, MAX_RECORD_BYTES)
    fsync_private_record_directory(directory)
    return receipt


def read_terminal_quota_bytes(root: Path, request_id: str) -> bytes:
    root = canonical_root(root)
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise BrokerError("terminal quota request ID is invalid")

    state_root = root / STATE_REL
    directory = state_root / "terminal-quota"
    try:
        dir_lstat_initial = os.lstat(directory)
    except OSError as exc:
        raise BrokerError("terminal quota directory is unavailable") from exc
    if not stat.S_ISDIR(dir_lstat_initial.st_mode) or stat.S_ISLNK(dir_lstat_initial.st_mode):
        raise BrokerError("terminal quota directory must be a directory and not a symlink")
    if dir_lstat_initial.st_uid != os.geteuid():
        raise BrokerError("terminal quota directory owner mismatch")
    if stat.S_IMODE(dir_lstat_initial.st_mode) != 0o700:
        raise BrokerError("terminal quota directory mode must be 0700")

    try:
        resolved_dir = directory.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BrokerError("terminal quota directory is unavailable") from exc
    if resolved_dir != directory:
        raise BrokerError("terminal quota directory must be a canonical path without symlinks")
    target_filename = f"{request_id}.json"
    target_path = directory / target_filename

    try:
        target_lstat_initial = os.lstat(target_path)
    except OSError as exc:
        raise BrokerError(f"terminal quota record {target_filename} is unavailable") from exc
    if not stat.S_ISREG(target_lstat_initial.st_mode) or stat.S_ISLNK(target_lstat_initial.st_mode):
        raise BrokerError("terminal quota record is not a regular file")
    if target_lstat_initial.st_uid != os.geteuid():
        raise BrokerError("terminal quota record owner mismatch")
    if stat.S_IMODE(target_lstat_initial.st_mode) != 0o600:
        raise BrokerError("terminal quota record mode must be 0600")
    if target_lstat_initial.st_nlink != 1:
        raise BrokerError("terminal quota record has invalid link count")
    if target_lstat_initial.st_size > MAX_RECORD_BYTES:
        raise BrokerError("terminal quota record exceeds byte limit")

    existing_entries = scan_private_record_directory(directory)
    if target_filename not in existing_entries:
        raise BrokerError(f"terminal quota record {target_filename} not found")

    dir_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        dir_fd = os.open(directory, dir_flags)
    except OSError as exc:
        raise BrokerError(f"cannot safely open terminal quota directory: {exc}") from exc

    file_fd = -1
    try:
        try:
            dir_fstat_before = os.fstat(dir_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat terminal quota directory: {exc}") from exc
        if not stat.S_ISDIR(dir_fstat_before.st_mode) or stat.S_ISLNK(dir_fstat_before.st_mode):
            raise BrokerError("terminal quota directory descriptor is not a directory")
        if dir_fstat_before.st_uid != os.geteuid():
            raise BrokerError("terminal quota directory descriptor owner mismatch")
        if stat.S_IMODE(dir_fstat_before.st_mode) != 0o700:
            raise BrokerError("terminal quota directory descriptor mode mismatch")
        if (dir_fstat_before.st_dev, dir_fstat_before.st_ino) != (dir_lstat_initial.st_dev, dir_lstat_initial.st_ino):
            raise BrokerError("terminal quota directory descriptor does not match path before open")

        try:
            dir_lstat_before_open = os.lstat(directory)
        except OSError as exc:
            raise BrokerError("cannot lstat terminal quota directory before open") from exc
        if (dir_lstat_before_open.st_dev, dir_lstat_before_open.st_ino) != (dir_fstat_before.st_dev, dir_fstat_before.st_ino):
            raise BrokerError("terminal quota directory path changed before open")
        if not stat.S_ISDIR(dir_lstat_before_open.st_mode) or stat.S_ISLNK(dir_lstat_before_open.st_mode):
            raise BrokerError("terminal quota directory path changed before open")
        if dir_lstat_before_open.st_uid != os.geteuid():
            raise BrokerError("terminal quota directory path owner mismatch before open")
        if stat.S_IMODE(dir_lstat_before_open.st_mode) != 0o700:
            raise BrokerError("terminal quota directory path mode mismatch before open")

        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            file_fd = os.open(target_filename, file_flags, dir_fd=dir_fd)
        except OSError as exc:
            raise BrokerError(f"cannot safely open terminal quota record: {exc}") from exc

        try:
            file_fstat_before = os.fstat(file_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat terminal quota record: {exc}") from exc
        if not stat.S_ISREG(file_fstat_before.st_mode) or stat.S_ISLNK(file_fstat_before.st_mode):
            raise BrokerError("terminal quota record is not a regular file")
        if file_fstat_before.st_uid != os.geteuid():
            raise BrokerError("terminal quota record owner mismatch")
        if stat.S_IMODE(file_fstat_before.st_mode) != 0o600:
            raise BrokerError("terminal quota record mode must be 0600")
        if file_fstat_before.st_nlink != 1:
            raise BrokerError("terminal quota record has invalid link count")
        if file_fstat_before.st_size > MAX_RECORD_BYTES:
            raise BrokerError("terminal quota record exceeds byte limit")
        if (
            (file_fstat_before.st_dev, file_fstat_before.st_ino) != (target_lstat_initial.st_dev, target_lstat_initial.st_ino)
            or file_fstat_before.st_mode != target_lstat_initial.st_mode
            or file_fstat_before.st_uid != target_lstat_initial.st_uid
            or file_fstat_before.st_nlink != target_lstat_initial.st_nlink
            or file_fstat_before.st_size != target_lstat_initial.st_size
            or file_fstat_before.st_mtime_ns != target_lstat_initial.st_mtime_ns
            or file_fstat_before.st_ctime_ns != target_lstat_initial.st_ctime_ns
        ):
            raise BrokerError("terminal quota record changed between scan and open")

        try:
            file_stat_before = os.stat(target_filename, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            raise BrokerError(f"cannot stat terminal quota record path: {exc}") from exc
        if (
            (file_fstat_before.st_dev, file_fstat_before.st_ino) != (file_stat_before.st_dev, file_stat_before.st_ino)
            or file_fstat_before.st_mode != file_stat_before.st_mode
            or file_fstat_before.st_uid != file_stat_before.st_uid
            or file_fstat_before.st_nlink != file_stat_before.st_nlink
            or file_fstat_before.st_size != file_stat_before.st_size
            or file_fstat_before.st_mtime_ns != file_stat_before.st_mtime_ns
            or file_fstat_before.st_ctime_ns != file_stat_before.st_ctime_ns
        ):
            raise BrokerError("terminal quota record descriptor does not match path before read")

        try:
            target_lstat_before = os.lstat(target_path)
        except OSError as exc:
            raise BrokerError(f"cannot lstat terminal quota record path: {exc}") from exc
        if (
            (file_fstat_before.st_dev, file_fstat_before.st_ino) != (target_lstat_before.st_dev, target_lstat_before.st_ino)
            or file_fstat_before.st_mode != target_lstat_before.st_mode
            or file_fstat_before.st_uid != target_lstat_before.st_uid
            or file_fstat_before.st_nlink != target_lstat_before.st_nlink
            or file_fstat_before.st_size != target_lstat_before.st_size
            or file_fstat_before.st_mtime_ns != target_lstat_before.st_mtime_ns
            or file_fstat_before.st_ctime_ns != target_lstat_before.st_ctime_ns
        ):
            raise BrokerError("terminal quota record descriptor does not match canonical path before read")

        expected_size = file_fstat_before.st_size
        chunks: list[bytes] = []
        total_read = 0
        limit = MAX_RECORD_BYTES + 1
        while total_read < limit:
            try:
                chunk = os.read(file_fd, limit - total_read)
            except OSError as exc:
                raise BrokerError(f"cannot read terminal quota record: {exc}") from exc
            if not chunk:
                break
            chunks.append(chunk)
            total_read += len(chunk)

        data = b"".join(chunks)
        if len(data) > MAX_RECORD_BYTES:
            raise BrokerError("terminal quota record exceeds maximum record bytes")
        if len(data) != expected_size:
            raise BrokerError("terminal quota record read size mismatch")

        try:
            file_fstat_after = os.fstat(file_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat terminal quota record after read: {exc}") from exc
        if (
            (file_fstat_after.st_dev, file_fstat_after.st_ino) != (file_fstat_before.st_dev, file_fstat_before.st_ino)
            or file_fstat_after.st_mode != file_fstat_before.st_mode
            or file_fstat_after.st_uid != file_fstat_before.st_uid
            or file_fstat_after.st_nlink != file_fstat_before.st_nlink
            or file_fstat_after.st_size != file_fstat_before.st_size
            or file_fstat_after.st_mtime_ns != file_fstat_before.st_mtime_ns
            or file_fstat_after.st_ctime_ns != file_fstat_before.st_ctime_ns
        ):
            raise BrokerError("terminal quota record descriptor metadata drift after read")

        try:
            file_stat_after = os.stat(target_filename, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            raise BrokerError(f"cannot stat terminal quota record path after read: {exc}") from exc
        if (
            (file_stat_after.st_dev, file_stat_after.st_ino) != (file_fstat_after.st_dev, file_fstat_after.st_ino)
            or file_stat_after.st_mode != file_fstat_after.st_mode
            or file_stat_after.st_uid != file_fstat_after.st_uid
            or file_stat_after.st_nlink != file_fstat_after.st_nlink
            or file_stat_after.st_size != file_fstat_after.st_size
            or file_stat_after.st_mtime_ns != file_fstat_after.st_mtime_ns
            or file_stat_after.st_ctime_ns != file_fstat_after.st_ctime_ns
        ):
            raise BrokerError("terminal quota record path metadata mismatch after read")

        try:
            target_lstat_after = os.lstat(target_path)
        except OSError as exc:
            raise BrokerError(f"cannot lstat terminal quota record path after read: {exc}") from exc
        if (
            (target_lstat_after.st_dev, target_lstat_after.st_ino) != (file_fstat_after.st_dev, file_fstat_after.st_ino)
            or target_lstat_after.st_mode != file_fstat_after.st_mode
            or target_lstat_after.st_uid != file_fstat_after.st_uid
            or target_lstat_after.st_nlink != file_fstat_after.st_nlink
            or target_lstat_after.st_size != file_fstat_after.st_size
            or target_lstat_after.st_mtime_ns != file_fstat_after.st_mtime_ns
            or target_lstat_after.st_ctime_ns != file_fstat_after.st_ctime_ns
        ):
            raise BrokerError("terminal quota record canonical path mismatch after read")

        try:
            dir_fstat_after = os.fstat(dir_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat terminal quota directory after read: {exc}") from exc
        if not stat.S_ISDIR(dir_fstat_after.st_mode) or stat.S_ISLNK(dir_fstat_after.st_mode):
            raise BrokerError("terminal quota directory descriptor is not a directory after read")
        if dir_fstat_after.st_uid != os.geteuid():
            raise BrokerError("terminal quota directory descriptor owner mismatch after read")
        if stat.S_IMODE(dir_fstat_after.st_mode) != 0o700:
            raise BrokerError("terminal quota directory descriptor mode mismatch after read")
        if (dir_fstat_after.st_dev, dir_fstat_after.st_ino) != (dir_fstat_before.st_dev, dir_fstat_before.st_ino):
            raise BrokerError("terminal quota directory descriptor changed after read")

        try:
            dir_lstat_after = os.lstat(directory)
        except OSError as exc:
            raise BrokerError(f"cannot lstat terminal quota directory after read: {exc}") from exc
        if not stat.S_ISDIR(dir_lstat_after.st_mode) or stat.S_ISLNK(dir_lstat_after.st_mode):
            raise BrokerError("terminal quota directory path changed after read")
        if dir_lstat_after.st_uid != os.geteuid():
            raise BrokerError("terminal quota directory path owner mismatch after read")
        if stat.S_IMODE(dir_lstat_after.st_mode) != 0o700:
            raise BrokerError("terminal quota directory path mode mismatch after read")
        if (dir_lstat_after.st_dev, dir_lstat_after.st_ino) != (dir_fstat_after.st_dev, dir_fstat_after.st_ino):
            raise BrokerError("terminal quota directory path does not match descriptor after read")

        return data
    finally:
        close_errors: list[OSError] = []
        if file_fd >= 0:
            try:
                os.close(file_fd)
            except OSError as exc:
                close_errors.append(exc)
        if dir_fd >= 0:
            try:
                os.close(dir_fd)
            except OSError as exc:
                close_errors.append(exc)
        if close_errors:
            raise BrokerError(f"cannot safely close descriptor: {close_errors[0]}") from close_errors[0]


def validate_terminal_quota_payload(
    root: Path, expected_request_id: str, raw_bytes: bytes
) -> dict:
    if not isinstance(raw_bytes, bytes):
        raise BrokerError("terminal quota raw payload must be bytes")
    if len(raw_bytes) > MAX_RECORD_BYTES:
        raise BrokerError("terminal quota raw payload exceeds maximum record bytes")

    root = canonical_root(root)
    if not isinstance(expected_request_id, str) or not REQUEST_ID_RE.fullmatch(expected_request_id):
        raise BrokerError("expected request ID is invalid")

    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrokerError("terminal quota payload is not valid UTF-8") from exc

    try:
        receipt = unique_json(decoded)
    except (json.JSONDecodeError, ValueError) as exc:
        raise BrokerError(f"terminal quota payload is not valid JSON: {exc}") from exc

    if not isinstance(receipt, dict):
        raise BrokerError("terminal quota payload must be a JSON object")

    try:
        canonical_bytes = json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise BrokerError("terminal quota payload cannot be canonically encoded") from exc

    if canonical_bytes != raw_bytes:
        raise BrokerError("terminal quota payload is not byte-for-byte canonical JSON")

    if set(receipt) != RECEIPT_KEYS:
        raise BrokerError("terminal quota receipt keys do not match exact contract")
    if receipt.get("schema_version") != "1.0":
        raise BrokerError("terminal quota receipt schema version is invalid")

    request = receipt.get("request")
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise BrokerError("broker request does not match the exact contract")
    if request.get("schema_version") != "1.0":
        raise BrokerError("broker request schema version is invalid")
    if not isinstance(request.get("request_id"), str) or not REQUEST_ID_RE.fullmatch(request["request_id"]):
        raise BrokerError("broker request ID is invalid")
    if request["request_id"] != expected_request_id:
        raise BrokerError("receipt request ID does not match expected request ID")
    if not isinstance(request.get("run_prefix"), str) or not RUN_PREFIX_RE.fullmatch(request["run_prefix"]):
        raise BrokerError("broker run-prefix is invalid")
    if type(request.get("timeout")) is not int or not 10 <= request["timeout"] <= 900:
        raise BrokerError("broker timeout is invalid")
    if not isinstance(request.get("source_head"), str) or not HEAD_RE.fullmatch(request["source_head"]):
        raise BrokerError("broker source HEAD is invalid")
    if not isinstance(request.get("task_sha256"), str) or not SHA_RE.fullmatch(request["task_sha256"]):
        raise BrokerError("broker task SHA is invalid")
    if (
        not isinstance(request.get("assignment_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request["assignment_id"])
    ):
        raise BrokerError("broker assignment ID is invalid")
    if not isinstance(request.get("assignment_sha256"), str) or not SHA_RE.fullmatch(request["assignment_sha256"]):
        raise BrokerError("broker assignment SHA is invalid")
    if (
        not isinstance(request.get("controller_run_id"), str)
        or not CONTROLLER_RUN_RE.fullmatch(request["controller_run_id"])
    ):
        raise BrokerError("broker controller run ID is invalid")
    created_at = request.get("created_at")
    if (
        not isinstance(created_at, (int, float))
        or isinstance(created_at, bool)
        or not math.isfinite(created_at)
    ):
        raise BrokerError("broker request created_at is invalid")
    _task_relative_path(request.get("task_path"))

    response = receipt.get("response")
    if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
        raise BrokerError("broker response does not match the exact contract")

    validated_response = validate_response(request, response)
    if validated_response.get("state") != "COMPLETED":
        raise BrokerError("terminal quota response state must be COMPLETED")
    if type(validated_response.get("exit_code")) is not int or validated_response.get("exit_code") != 1:
        raise BrokerError("terminal quota response exit_code must be 1")
    if validated_response.get("stderr") != "":
        raise BrokerError("terminal quota response stderr must be empty")
    resp_finished_at = validated_response.get("finished_at")
    if (
        not isinstance(resp_finished_at, (int, float))
        or isinstance(resp_finished_at, bool)
        or not math.isfinite(resp_finished_at)
    ):
        raise BrokerError("broker response finished_at is invalid")

    receipt_finished_at = receipt.get("finished_at")
    if (
        not isinstance(receipt_finished_at, (int, float))
        or isinstance(receipt_finished_at, bool)
        or not math.isfinite(receipt_finished_at)
    ):
        raise BrokerError("terminal quota receipt finished_at is invalid")
    if receipt_finished_at != resp_finished_at or type(receipt_finished_at) is not type(resp_finished_at):
        raise BrokerError("terminal quota receipt finished_at mismatch")

    try:
        req_canonical = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        resp_canonical = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise BrokerError("request or response contains non-finite numbers") from exc

    req_sha = receipt.get("request_sha256")
    if not isinstance(req_sha, str) or not SHA_RE.fullmatch(req_sha):
        raise BrokerError("receipt request_sha256 is invalid")
    if req_sha != hashlib.sha256(req_canonical).hexdigest():
        raise BrokerError("receipt request_sha256 digest mismatch")

    resp_sha = receipt.get("response_sha256")
    if not isinstance(resp_sha, str) or not SHA_RE.fullmatch(resp_sha):
        raise BrokerError("receipt response_sha256 is invalid")
    if resp_sha != hashlib.sha256(resp_canonical).hexdigest():
        raise BrokerError("receipt response_sha256 digest mismatch")

    evidence = detect_quota_response(root, request, validated_response)
    if evidence is None:
        raise BrokerError("terminal quota response not detected")
    if receipt.get("quota_evidence") != evidence:
        raise BrokerError("receipt quota evidence mismatch")

    return receipt


def read_terminal_quota_record(root: Path, request_id: str) -> dict:
    raw_bytes = read_terminal_quota_bytes(root, request_id)
    return validate_terminal_quota_payload(root, request_id, raw_bytes)


def read_terminal_quota_for_exact_request(
    root: Path, expected_request: object
) -> dict:
    root = canonical_root(root)
    if not isinstance(expected_request, dict) or set(expected_request) != REQUEST_KEYS:
        raise BrokerError("broker request does not match the exact contract")
    if expected_request.get("schema_version") != "1.0":
        raise BrokerError("broker request schema version is invalid")
    request_id = expected_request.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise BrokerError("broker request ID is invalid")
    run_prefix = expected_request.get("run_prefix")
    if not isinstance(run_prefix, str) or not RUN_PREFIX_RE.fullmatch(run_prefix):
        raise BrokerError("broker run-prefix is invalid")
    timeout = expected_request.get("timeout")
    if type(timeout) is not int or not 10 <= timeout <= 900:
        raise BrokerError("broker timeout is invalid")
    source_head = expected_request.get("source_head")
    if not isinstance(source_head, str) or not HEAD_RE.fullmatch(source_head):
        raise BrokerError("broker source HEAD is invalid")
    task_sha256 = expected_request.get("task_sha256")
    if not isinstance(task_sha256, str) or not SHA_RE.fullmatch(task_sha256):
        raise BrokerError("broker task SHA is invalid")
    assignment_id = expected_request.get("assignment_id")
    if not isinstance(assignment_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", assignment_id):
        raise BrokerError("broker assignment ID is invalid")
    assignment_sha256 = expected_request.get("assignment_sha256")
    if not isinstance(assignment_sha256, str) or not SHA_RE.fullmatch(assignment_sha256):
        raise BrokerError("broker assignment SHA is invalid")
    controller_run_id = expected_request.get("controller_run_id")
    if not isinstance(controller_run_id, str) or not CONTROLLER_RUN_RE.fullmatch(controller_run_id):
        raise BrokerError("broker controller run ID is invalid")
    created_at = expected_request.get("created_at")
    if (
        not isinstance(created_at, (int, float))
        or isinstance(created_at, bool)
        or not math.isfinite(created_at)
    ):
        raise BrokerError("broker request created_at is invalid")
    _task_relative_path(expected_request.get("task_path"))

    receipt = read_terminal_quota_record(root, request_id)

    try:
        expected_canonical = json.dumps(
            expected_request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        receipt_req_canonical = json.dumps(
            receipt["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise BrokerError("request contains non-finite numbers") from exc

    if expected_canonical != receipt_req_canonical:
        raise BrokerError("expected request does not match receipt request")
    expected_req_sha = hashlib.sha256(expected_canonical).hexdigest()
    if receipt.get("request_sha256") != expected_req_sha:
        raise BrokerError("receipt request digest mismatch")

    state_root = root / STATE_REL
    responses = state_root / "responses"

    try:
        dir_lstat_initial = os.lstat(responses)
    except OSError as exc:
        raise BrokerError("responses directory is unavailable") from exc
    if not stat.S_ISDIR(dir_lstat_initial.st_mode) or stat.S_ISLNK(dir_lstat_initial.st_mode):
        raise BrokerError("responses directory must be a directory and not a symlink")
    if dir_lstat_initial.st_uid != os.geteuid():
        raise BrokerError("responses directory owner mismatch")
    if stat.S_IMODE(dir_lstat_initial.st_mode) != 0o700:
        raise BrokerError("responses directory mode must be 0700")

    try:
        resolved_responses = responses.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BrokerError("responses directory is unavailable") from exc
    if resolved_responses != responses:
        raise BrokerError("responses directory must be a canonical path without symlinks")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    dir_fd = -1
    try:
        try:
            dir_fd = os.open(responses, flags)
        except OSError as exc:
            raise BrokerError(f"cannot safely open responses directory: {exc}") from exc

        try:
            dir_fstat = os.fstat(dir_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat responses directory: {exc}") from exc
        if not stat.S_ISDIR(dir_fstat.st_mode) or stat.S_ISLNK(dir_fstat.st_mode):
            raise BrokerError("responses directory descriptor is not a directory")
        if dir_fstat.st_uid != os.geteuid():
            raise BrokerError("responses directory descriptor owner mismatch")
        if stat.S_IMODE(dir_fstat.st_mode) != 0o700:
            raise BrokerError("responses directory descriptor mode mismatch")
        if (dir_fstat.st_dev, dir_fstat.st_ino) != (dir_lstat_initial.st_dev, dir_lstat_initial.st_ino):
            raise BrokerError("responses directory descriptor does not match path before scan")

        try:
            dir_lstat_after_open = os.lstat(responses)
        except OSError as exc:
            raise BrokerError(f"cannot lstat responses directory after open: {exc}") from exc
        if (dir_lstat_after_open.st_dev, dir_lstat_after_open.st_ino) != (dir_fstat.st_dev, dir_fstat.st_ino):
            raise BrokerError("responses directory path changed during open")
        if not stat.S_ISDIR(dir_lstat_after_open.st_mode) or stat.S_ISLNK(dir_lstat_after_open.st_mode):
            raise BrokerError("responses directory path changed during open")
        if dir_lstat_after_open.st_uid != os.geteuid():
            raise BrokerError("responses directory path owner mismatch during open")
        if stat.S_IMODE(dir_lstat_after_open.st_mode) != 0o700:
            raise BrokerError("responses directory path mode mismatch during open")

        response_filename = f"{request_id}.json"
        response_path = responses / response_filename

        response_present = False
        entry_stat: os.stat_result | None = None
        try:
            entry_stat = os.stat(response_filename, dir_fd=dir_fd, follow_symlinks=False)
            response_present = True
        except FileNotFoundError:
            response_present = False
        except OSError as exc:
            raise BrokerError(f"cannot stat response file via descriptor: {exc}") from exc

        if response_present:
            assert entry_stat is not None
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
                raise BrokerError("existing response is not a regular file")
            if entry_stat.st_uid != os.geteuid():
                raise BrokerError("existing response owner mismatch")
            if stat.S_IMODE(entry_stat.st_mode) != 0o600:
                raise BrokerError("existing response mode must be 0600")
            if entry_stat.st_nlink != 1:
                raise BrokerError("existing response has invalid link count")
            if entry_stat.st_size > MAX_RESPONSE_BYTES:
                raise BrokerError("existing response exceeds byte limit")

            try:
                path_stat_before = os.lstat(response_path)
            except OSError as exc:
                raise BrokerError(f"cannot lstat existing response path: {exc}") from exc
            if (
                (path_stat_before.st_dev, path_stat_before.st_ino) != (entry_stat.st_dev, entry_stat.st_ino)
                or path_stat_before.st_mode != entry_stat.st_mode
                or path_stat_before.st_uid != entry_stat.st_uid
                or path_stat_before.st_nlink != entry_stat.st_nlink
                or path_stat_before.st_size != entry_stat.st_size
                or path_stat_before.st_mtime_ns != entry_stat.st_mtime_ns
                or path_stat_before.st_ctime_ns != entry_stat.st_ctime_ns
            ):
                raise BrokerError("existing response path metadata does not match descriptor entry")

            existing_resp = _safe_read_existing_response(root, response_path)

            try:
                path_stat_after = os.lstat(response_path)
            except OSError as exc:
                raise BrokerError(f"cannot lstat existing response path after read: {exc}") from exc
            if (path_stat_after.st_dev, path_stat_after.st_ino) != (entry_stat.st_dev, entry_stat.st_ino):
                raise BrokerError("existing response path changed during read")

            try:
                entry_stat_after = os.stat(response_filename, dir_fd=dir_fd, follow_symlinks=False)
            except OSError as exc:
                raise BrokerError(f"cannot stat response file via descriptor after read: {exc}") from exc
            if (entry_stat_after.st_dev, entry_stat_after.st_ino) != (entry_stat.st_dev, entry_stat.st_ino):
                raise BrokerError("existing response descriptor entry changed during read")

            try:
                existing_canonical = json.dumps(
                    existing_resp, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
                receipt_resp_canonical = json.dumps(
                    receipt["response"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            except (ValueError, TypeError) as exc:
                raise BrokerError("response contains non-finite numbers") from exc

            if existing_canonical != receipt_resp_canonical:
                raise BrokerError("existing response does not match receipt response")
            existing_sha = hashlib.sha256(existing_canonical).hexdigest()
            if receipt.get("response_sha256") != existing_sha:
                raise BrokerError("existing response digest mismatch")

        try:
            final_dir_lstat = os.lstat(responses)
        except OSError as exc:
            raise BrokerError(f"cannot lstat responses directory before return: {exc}") from exc
        if not stat.S_ISDIR(final_dir_lstat.st_mode) or stat.S_ISLNK(final_dir_lstat.st_mode):
            raise BrokerError("responses directory changed or became symlink")
        if final_dir_lstat.st_uid != os.geteuid():
            raise BrokerError("responses directory owner changed")
        if stat.S_IMODE(final_dir_lstat.st_mode) != 0o700:
            raise BrokerError("responses directory mode changed")
        if (final_dir_lstat.st_dev, final_dir_lstat.st_ino) != (dir_fstat.st_dev, dir_fstat.st_ino):
            raise BrokerError("responses directory identity mismatch between path and descriptor")

        try:
            final_dir_fstat = os.fstat(dir_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat responses directory descriptor before return: {exc}") from exc
        if (final_dir_fstat.st_dev, final_dir_fstat.st_ino) != (dir_fstat.st_dev, dir_fstat.st_ino):
            raise BrokerError("responses directory descriptor changed")

        return receipt["response"]
    finally:
        close_errors: list[OSError] = []
        if dir_fd >= 0:
            try:
                os.close(dir_fd)
            except OSError as exc:
                close_errors.append(exc)
        if close_errors:
            raise BrokerError(f"cannot safely close responses directory descriptor: {close_errors[0]}") from close_errors[0]


RESERVATION_KEYS = frozenset({
    "controller_run_id",
    "run_prefix",
    "task_path",
    "task_sha256",
    "source_head",
    "assignment_id",
    "assignment_sha256",
})


def find_terminal_quota_for_reservation(
    root: Path, expected_reservation: object
) -> dict:
    root = canonical_root(root)
    if not isinstance(expected_reservation, dict) or set(expected_reservation) != RESERVATION_KEYS:
        raise BrokerError("reservation does not match the exact contract")
    for key in RESERVATION_KEYS:
        val = expected_reservation[key]
        if type(val) is not str:
            raise BrokerError(f"reservation field {key} must be a string")
    if not CONTROLLER_RUN_RE.fullmatch(expected_reservation["controller_run_id"]):
        raise BrokerError("reservation controller_run_id is invalid")
    if not RUN_PREFIX_RE.fullmatch(expected_reservation["run_prefix"]):
        raise BrokerError("reservation run_prefix is invalid")
    if not HEAD_RE.fullmatch(expected_reservation["source_head"]):
        raise BrokerError("reservation source_head is invalid")
    if not SHA_RE.fullmatch(expected_reservation["task_sha256"]):
        raise BrokerError("reservation task_sha256 is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", expected_reservation["assignment_id"]):
        raise BrokerError("reservation assignment_id is invalid")
    if not SHA_RE.fullmatch(expected_reservation["assignment_sha256"]):
        raise BrokerError("reservation assignment_sha256 is invalid")
    task_rel = _task_relative_path(expected_reservation["task_path"])
    if task_rel.as_posix() != expected_reservation["task_path"]:
        raise BrokerError("reservation task_path is not canonical")

    state_root = root / STATE_REL
    directory = state_root / "terminal-quota"

    try:
        dir_stat_start = os.lstat(directory)
    except OSError as exc:
        raise BrokerError("terminal quota directory is unavailable") from exc
    if not stat.S_ISDIR(dir_stat_start.st_mode) or stat.S_ISLNK(dir_stat_start.st_mode):
        raise BrokerError("terminal quota directory must be a directory and not a symlink")
    if dir_stat_start.st_uid != os.geteuid():
        raise BrokerError("terminal quota directory owner mismatch")
    if stat.S_IMODE(dir_stat_start.st_mode) != 0o700:
        raise BrokerError("terminal quota directory mode must be 0700")

    try:
        resolved_dir = directory.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BrokerError("terminal quota directory is unavailable") from exc
    if resolved_dir != directory:
        raise BrokerError("terminal quota directory must be a canonical path without symlinks")

    entry_names = scan_private_record_directory(directory)

    read_entries: list[tuple[str, dict, bytes, os.stat_result]] = []
    for entry_name in entry_names:
        req_id = entry_name[:-5]
        entry_path = directory / entry_name
        try:
            entry_stat_before = os.lstat(entry_path)
        except OSError as exc:
            raise BrokerError(f"cannot lstat receipt file {entry_name}: {exc}") from exc
        if (
            stat.S_ISLNK(entry_stat_before.st_mode)
            or not stat.S_ISREG(entry_stat_before.st_mode)
            or entry_stat_before.st_uid != os.geteuid()
            or stat.S_IMODE(entry_stat_before.st_mode) != 0o600
            or entry_stat_before.st_nlink != 1
            or entry_stat_before.st_size > MAX_RECORD_BYTES
        ):
            raise BrokerError(f"receipt file {entry_name} stat mismatch before read")
        raw_bytes = read_terminal_quota_bytes(root, req_id)
        receipt = validate_terminal_quota_payload(root, req_id, raw_bytes)
        try:
            entry_stat = os.lstat(entry_path)
        except OSError as exc:
            raise BrokerError(f"cannot lstat receipt file {entry_name}: {exc}") from exc
        if (
            stat.S_ISLNK(entry_stat.st_mode)
            or not stat.S_ISREG(entry_stat.st_mode)
            or entry_stat.st_uid != os.geteuid()
            or stat.S_IMODE(entry_stat.st_mode) != 0o600
            or entry_stat.st_nlink != 1
            or entry_stat.st_size != len(raw_bytes)
        ):
            raise BrokerError(f"receipt file {entry_name} stat mismatch")
        if (
            (
                entry_stat.st_dev,
                entry_stat.st_ino,
                entry_stat.st_mode,
                entry_stat.st_uid,
                entry_stat.st_nlink,
                entry_stat.st_size,
                entry_stat.st_mtime_ns,
                entry_stat.st_ctime_ns,
            )
            != (
                entry_stat_before.st_dev,
                entry_stat_before.st_ino,
                entry_stat_before.st_mode,
                entry_stat_before.st_uid,
                entry_stat_before.st_nlink,
                entry_stat_before.st_size,
                entry_stat_before.st_mtime_ns,
                entry_stat_before.st_ctime_ns,
            )
        ):
            raise BrokerError(f"receipt file {entry_name} identity or metadata changed during read")
        read_entries.append((entry_name, receipt, raw_bytes, entry_stat))

    matching_entries: list[tuple[str, dict, bytes, os.stat_result]] = []
    for entry_name, receipt, raw_bytes, entry_stat in read_entries:
        req = receipt["request"]
        matches = True
        for key in RESERVATION_KEYS:
            actual_val = req.get(key)
            if type(actual_val) is not str or actual_val != expected_reservation[key]:
                matches = False
                break
        if matches:
            matching_entries.append((entry_name, receipt, raw_bytes, entry_stat))

    if len(matching_entries) == 0:
        raise BrokerError("no terminal quota record matched reservation")
    if len(matching_entries) > 1:
        raise BrokerError("multiple terminal quota records matched reservation")

    selected_entry_name, selected_receipt, original_raw_bytes, stat_before_c4b1 = matching_entries[0]
    selected_request = selected_receipt["request"]
    selected_path = directory / selected_entry_name

    pinned_dev = stat_before_c4b1.st_dev
    pinned_ino = stat_before_c4b1.st_ino
    pinned_mode = stat_before_c4b1.st_mode
    pinned_uid = stat_before_c4b1.st_uid
    pinned_nlink = stat_before_c4b1.st_nlink
    pinned_size = stat_before_c4b1.st_size
    pinned_mtime_ns = stat_before_c4b1.st_mtime_ns
    pinned_ctime_ns = stat_before_c4b1.st_ctime_ns

    try:
        dir_stat_mid = os.lstat(directory)
    except OSError as exc:
        raise BrokerError("cannot lstat terminal quota directory before C4b1 read") from exc
    if (
        dir_stat_mid.st_dev != dir_stat_start.st_dev
        or dir_stat_mid.st_ino != dir_stat_start.st_ino
        or dir_stat_mid.st_uid != dir_stat_start.st_uid
        or stat.S_IMODE(dir_stat_mid.st_mode) != 0o700
        or not stat.S_ISDIR(dir_stat_mid.st_mode)
        or stat.S_ISLNK(dir_stat_mid.st_mode)
    ):
        raise BrokerError("terminal quota directory identity changed before C4b1 read")

    c4b1_response = read_terminal_quota_for_exact_request(root, selected_request)

    try:
        c4b1_resp_canonical = json.dumps(
            c4b1_response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        receipt_resp_canonical = json.dumps(
            selected_receipt["response"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise BrokerError("response contains non-finite numbers") from exc

    if c4b1_resp_canonical != receipt_resp_canonical:
        raise BrokerError("C4b1 response canonical bytes mismatch with receipt response")

    try:
        stat_after_c4b1 = os.lstat(selected_path)
    except OSError as exc:
        raise BrokerError(f"cannot lstat receipt file {selected_entry_name} after C4b1 read: {exc}") from exc

    if (
        stat_after_c4b1.st_dev != pinned_dev
        or stat_after_c4b1.st_ino != pinned_ino
        or stat_after_c4b1.st_mode != pinned_mode
        or stat_after_c4b1.st_uid != pinned_uid
        or stat_after_c4b1.st_nlink != pinned_nlink
        or stat_after_c4b1.st_size != pinned_size
        or stat_after_c4b1.st_mtime_ns != pinned_mtime_ns
        or stat_after_c4b1.st_ctime_ns != pinned_ctime_ns
    ):
        raise BrokerError("receipt file identity or metadata changed across C4b1 read")

    re_read_raw_bytes = read_terminal_quota_bytes(root, selected_request["request_id"])
    re_read_receipt = validate_terminal_quota_payload(root, selected_request["request_id"], re_read_raw_bytes)

    if re_read_raw_bytes != original_raw_bytes:
        raise BrokerError("receipt canonical bytes changed across C4b1 read")
    if re_read_receipt != selected_receipt:
        raise BrokerError("receipt structure changed across C4b1 read")

    try:
        stat_after_reread = os.lstat(selected_path)
    except OSError as exc:
        raise BrokerError(f"cannot lstat receipt file {selected_entry_name} after re-read: {exc}") from exc

    if (
        stat_after_reread.st_dev != pinned_dev
        or stat_after_reread.st_ino != pinned_ino
        or stat_after_reread.st_mode != pinned_mode
        or stat_after_reread.st_uid != pinned_uid
        or stat_after_reread.st_nlink != pinned_nlink
        or stat_after_reread.st_size != pinned_size
        or stat_after_reread.st_mtime_ns != pinned_mtime_ns
        or stat_after_reread.st_ctime_ns != pinned_ctime_ns
    ):
        raise BrokerError("receipt file identity or metadata changed after re-read")

    try:
        dir_stat_end = os.lstat(directory)
    except OSError as exc:
        raise BrokerError("cannot lstat terminal quota directory after discovery") from exc
    if not stat.S_ISDIR(dir_stat_end.st_mode) or stat.S_ISLNK(dir_stat_end.st_mode):
        raise BrokerError("terminal quota directory changed or became symlink")
    if dir_stat_end.st_uid != dir_stat_start.st_uid:
        raise BrokerError("terminal quota directory owner changed")
    if stat.S_IMODE(dir_stat_end.st_mode) != 0o700:
        raise BrokerError("terminal quota directory mode changed")
    if (dir_stat_end.st_dev, dir_stat_end.st_ino) != (dir_stat_start.st_dev, dir_stat_start.st_ino):
        raise BrokerError("terminal quota directory identity changed across discovery")

    final_entry_names = scan_private_record_directory(directory)
    if final_entry_names != entry_names:
        raise BrokerError("terminal quota directory entries changed during discovery")

    return c4b1_response


def _verify_pinned_directory(
    path: Path,
    fd: int,
    *,
    is_final: bool,
    expected_dev: int | None = None,
    expected_ino: int | None = None,
) -> tuple[int, int]:
    try:
        lstat_info = os.lstat(path)
    except OSError as exc:
        raise BrokerError(f"cannot lstat directory {path}: {exc}") from exc
    if not stat.S_ISDIR(lstat_info.st_mode) or stat.S_ISLNK(lstat_info.st_mode):
        raise BrokerError(f"directory {path} must be a directory and not a symlink")
    if lstat_info.st_uid != os.geteuid():
        raise BrokerError(f"directory {path} is not owned by current euid")

    try:
        fstat_info = os.fstat(fd)
    except OSError as exc:
        raise BrokerError(f"cannot fstat directory descriptor for {path}: {exc}") from exc
    if not stat.S_ISDIR(fstat_info.st_mode):
        raise BrokerError(f"directory descriptor for {path} is not a directory")
    if fstat_info.st_uid != os.geteuid():
        raise BrokerError(f"directory descriptor for {path} is not owned by current euid")

    if is_final:
        if stat.S_IMODE(lstat_info.st_mode) != 0o700:
            raise BrokerError(f"run directory {path} mode must be 0700")
        if stat.S_IMODE(fstat_info.st_mode) != 0o700:
            raise BrokerError(f"run directory descriptor for {path} mode must be 0700")
    else:
        if stat.S_IMODE(lstat_info.st_mode) & 0o022 != 0:
            raise BrokerError(f"directory {path} has group or world write bits")
        if stat.S_IMODE(fstat_info.st_mode) & 0o022 != 0:
            raise BrokerError(f"directory descriptor for {path} has group or world write bits")

    if (fstat_info.st_dev, fstat_info.st_ino) != (lstat_info.st_dev, lstat_info.st_ino):
        raise BrokerError(f"directory descriptor for {path} does not match path")

    if expected_dev is not None and expected_ino is not None:
        if (
            (fstat_info.st_dev, fstat_info.st_ino) != (expected_dev, expected_ino)
            or (lstat_info.st_dev, lstat_info.st_ino) != (expected_dev, expected_ino)
        ):
            raise BrokerError(f"directory {path} identity changed")

    return fstat_info.st_dev, fstat_info.st_ino


@contextmanager
def _open_existing_cycle_run_directory(
    root: Path, controller_run_id: str
):
    if not isinstance(root, Path):
        raise BrokerError("broker root must be a Path")
    root = canonical_root(root)
    if not isinstance(controller_run_id, str) or not CYCLE_RUN_RE.fullmatch(controller_run_id):
        raise BrokerError("controller run ID must be a cycle run ID")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptors: list[int] = []
    pinned: list[tuple[Path, int, bool, int, int]] = []
    closed = False

    def verify_identity() -> None:
        if closed:
            raise BrokerError("cycle run directory context is closed")
        for p, d, fin, exp_dev, exp_ino in pinned:
            _verify_pinned_directory(
                p,
                d,
                is_final=fin,
                expected_dev=exp_dev,
                expected_ino=exp_ino,
            )

    try:
        try:
            root_fd = os.open(root, flags)
        except OSError as exc:
            raise BrokerError(f"cannot safely open root directory: {exc}") from exc
        descriptors.append(root_fd)
        dev, ino = _verify_pinned_directory(root, root_fd, is_final=False)
        pinned.append((root, root_fd, False, dev, ino))

        current_path = root
        parent_fd = root_fd
        components = [".local", "two-site-refactor", "autopilot", "runs", controller_run_id]
        for idx, comp in enumerate(components):
            current_path = current_path / comp
            is_final = (idx == len(components) - 1)
            try:
                comp_fd = os.open(comp, flags, dir_fd=parent_fd)
            except OSError as exc:
                raise BrokerError(f"cannot safely open directory {comp}: {exc}") from exc
            descriptors.append(comp_fd)
            dev, ino = _verify_pinned_directory(current_path, comp_fd, is_final=is_final)
            pinned.append((current_path, comp_fd, is_final, dev, ino))
            parent_fd = comp_fd

        run_path = current_path
        run_fd = descriptors[-1]
        yield run_path, run_fd, verify_identity
    finally:
        closed = True
        close_errors: list[OSError] = []
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError as exc:
                close_errors.append(exc)
        if close_errors and sys.exc_info()[1] is None:
            raise BrokerError(f"cannot safely close descriptor: {close_errors[0]}") from close_errors[0]


def _stat_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_cycle_private_json(
    run_path: Path, run_fd: int, filename: str
) -> tuple[dict, bytes]:
    if not isinstance(run_path, Path):
        raise BrokerError("run directory path must be a Path")
    if type(run_fd) is not int or run_fd < 0:
        raise BrokerError("run directory descriptor must be a non-negative integer")
    if filename not in ("preparation-startup.json", "broker-reservation.json"):
        raise BrokerError("cycle private filename must be 'preparation-startup.json' or 'broker-reservation.json'")

    file_path = run_path / filename

    def _validate_stat(info: os.stat_result, label: str) -> None:
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise BrokerError(f"{label} is not a regular file")
        if info.st_uid != os.geteuid():
            raise BrokerError(f"{label} owner mismatch")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise BrokerError(f"{label} mode must be 0600")
        if info.st_nlink != 1:
            raise BrokerError(f"{label} has invalid link count")
        if info.st_size > 4096:
            raise BrokerError(f"{label} exceeds byte limit")

    try:
        lstat_before = os.lstat(file_path)
    except OSError as exc:
        raise BrokerError(f"cannot lstat cycle private file {filename}: {exc}") from exc
    _validate_stat(lstat_before, f"cycle private file {filename} path before open")
    initial_fingerprint = _stat_fingerprint(lstat_before)

    try:
        dirfd_stat_before = os.stat(filename, dir_fd=run_fd, follow_symlinks=False)
    except OSError as exc:
        raise BrokerError(f"cannot stat cycle private file {filename} via dir_fd before open: {exc}") from exc
    _validate_stat(dirfd_stat_before, f"cycle private file {filename} via dir_fd before open")
    if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
        raise BrokerError(f"cycle private file {filename} dir_fd stat mismatch before open")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        file_fd = os.open(filename, flags, dir_fd=run_fd)
    except OSError as exc:
        raise BrokerError(f"cannot safely open cycle private file {filename}: {exc}") from exc

    try:
        try:
            fstat_before = os.fstat(file_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat cycle private file {filename} before read: {exc}") from exc
        _validate_stat(fstat_before, f"cycle private file {filename} descriptor before read")
        if _stat_fingerprint(fstat_before) != initial_fingerprint:
            raise BrokerError(f"cycle private file {filename} descriptor metadata mismatch before read")

        expected_size = initial_fingerprint[5]
        chunks: list[bytes] = []
        total_read = 0
        limit = 4097
        while total_read < limit:
            try:
                chunk = os.read(file_fd, limit - total_read)
            except OSError as exc:
                raise BrokerError(f"cannot read cycle private file {filename}: {exc}") from exc
            if not chunk:
                break
            chunks.append(chunk)
            total_read += len(chunk)

        data = b"".join(chunks)
        if len(data) > 4096:
            raise BrokerError(f"cycle private file {filename} exceeds byte limit")
        if len(data) != expected_size:
            raise BrokerError(f"cycle private file {filename} read size mismatch")

        try:
            fstat_after = os.fstat(file_fd)
        except OSError as exc:
            raise BrokerError(f"cannot fstat cycle private file {filename} after read: {exc}") from exc
        _validate_stat(fstat_after, f"cycle private file {filename} descriptor after read")
        if _stat_fingerprint(fstat_after) != initial_fingerprint:
            raise BrokerError(f"cycle private file {filename} descriptor metadata changed after read")

        try:
            dirfd_stat_after = os.stat(filename, dir_fd=run_fd, follow_symlinks=False)
        except OSError as exc:
            raise BrokerError(f"cannot stat cycle private file {filename} via dir_fd after read: {exc}") from exc
        _validate_stat(dirfd_stat_after, f"cycle private file {filename} via dir_fd after read")
        if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
            raise BrokerError(f"cycle private file {filename} dir_fd metadata changed after read")

        try:
            lstat_after = os.lstat(file_path)
        except OSError as exc:
            raise BrokerError(f"cannot lstat cycle private file {filename} after read: {exc}") from exc
        _validate_stat(lstat_after, f"cycle private file {filename} path after read")
        if _stat_fingerprint(lstat_after) != initial_fingerprint:
            raise BrokerError(f"cycle private file {filename} path metadata changed after read")

        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BrokerError(f"cycle private file {filename} is not valid UTF-8") from exc

        try:
            payload = unique_json(decoded)
        except (json.JSONDecodeError, ValueError) as exc:
            raise BrokerError(f"cycle private file {filename} is not valid JSON: {exc}") from exc

        if not isinstance(payload, dict) or type(payload) is not dict:
            raise BrokerError(f"cycle private file {filename} must be a JSON object")

        return payload, data
    finally:
        try:
            os.close(file_fd)
        except OSError as exc:
            if sys.exc_info()[1] is None:
                raise BrokerError(f"cannot safely close cycle private file {filename} descriptor: {exc}") from exc


def _parse_cycle_preparation_startup(
    startup: object, controller_run_id: str
) -> PreparationStartup:
    if not isinstance(controller_run_id, str) or not CYCLE_RUN_RE.fullmatch(controller_run_id):
        raise BrokerError("controller run ID must be a cycle run ID")
    try:
        parsed = PreparationStartup.from_mapping(startup)
    except PreparationConfigError as exc:
        raise BrokerError(f"invalid preparation startup: {exc}") from exc
    if parsed.cycle_id != controller_run_id:
        raise BrokerError("preparation startup cycle ID does not match controller run ID")
    return parsed


def publish_cycle_reservation(root: Path, request: dict) -> dict:
    if not isinstance(request, dict):
        raise BrokerError("claimed broker request does not match the exact contract")
    req_id = request.get("request_id")
    if not isinstance(req_id, str):
        req_id = ""
    _validate_claimed_request_shape(request, req_id)

    controller_run_id = request["controller_run_id"]
    if MAINTENANCE_RUN_RE.fullmatch(controller_run_id):
        raise BrokerError("cycle reservation rejects maintenance run ID; controller run ID must be a cycle run ID")
    if not CYCLE_RUN_RE.fullmatch(controller_run_id):
        raise BrokerError("controller run ID must be a cycle run ID")

    with _open_existing_cycle_run_directory(root, controller_run_id) as (
        run_path, run_fd, verify_identity
    ):
        startup_payload, _startup_bytes = _read_cycle_private_json(
            run_path, run_fd, "preparation-startup.json"
        )
        startup = _parse_cycle_preparation_startup(startup_payload, controller_run_id)
        if startup.source_head != request["source_head"]:
            raise BrokerError("preparation startup source HEAD does not match request")
        if startup.assignment_sha256 != request["assignment_sha256"]:
            raise BrokerError("preparation startup assignment SHA does not match request")

        target_filename = "broker-reservation.json"
        target_path = run_path / target_filename
        try:
            os.stat(target_filename, dir_fd=run_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise BrokerError(f"cannot stat broker reservation via dir_fd: {exc}") from exc
        else:
            raise BrokerError("broker reservation target already exists")

        if os.path.lexists(target_path):
            raise BrokerError("broker reservation target already exists")

        reservation = {
            "controller_run_id": request["controller_run_id"],
            "run_prefix": request["run_prefix"],
            "task_path": request["task_path"],
            "task_sha256": request["task_sha256"],
            "source_head": request["source_head"],
            "assignment_id": request["assignment_id"],
            "assignment_sha256": request["assignment_sha256"],
        }

        try:
            request_canonical = json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise BrokerError("request contains non-finite numbers") from exc

        request_sha256 = hashlib.sha256(request_canonical).hexdigest()

        record = {
            "schema_version": "1.0",
            "controller_run_id": request["controller_run_id"],
            "request_id": request["request_id"],
            "reservation": reservation,
            "request_sha256": request_sha256,
            "created_at": request["created_at"],
        }

        try:
            encoded_record = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise BrokerError("record contains non-finite numbers") from exc

        if len(encoded_record) > 4096:
            raise BrokerError("broker reservation record exceeds maximum 4096 bytes")

        verify_identity()

        temp_filename = f".{target_filename}.{uuid.uuid4().hex}.tmp"
        temp_fd = -1
        linked = False
        try:
            temp_flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                temp_fd = os.open(temp_filename, temp_flags, 0o600, dir_fd=run_fd)
            except OSError as exc:
                raise BrokerError(f"cannot safely open temporary reservation file: {exc}") from exc

            try:
                view = memoryview(encoded_record)
                while view:
                    written = os.write(temp_fd, view)
                    if written <= 0:
                        raise BrokerError("broker reservation write made no progress")
                    view = view[written:]
                os.fchmod(temp_fd, 0o600)
                os.fsync(temp_fd)
            finally:
                try:
                    os.close(temp_fd)
                except OSError as exc:
                    if sys.exc_info()[1] is None:
                        raise BrokerError(f"cannot safely close temporary reservation file: {exc}") from exc
                temp_fd = -1

            try:
                os.link(
                    temp_filename,
                    target_filename,
                    src_dir_fd=run_fd,
                    dst_dir_fd=run_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise BrokerError("broker reservation target already exists") from exc
            except OSError as exc:
                raise BrokerError(f"cannot safely link broker reservation: {exc}") from exc
            linked = True

            try:
                os.unlink(temp_filename, dir_fd=run_fd)
            except OSError as exc:
                raise BrokerError(f"cannot safely unlink temporary reservation file: {exc}") from exc

            try:
                os.fsync(run_fd)
            except OSError as exc:
                raise BrokerError(f"cannot fsync run directory: {exc}") from exc

            read_payload, read_bytes = _read_cycle_private_json(run_path, run_fd, target_filename)
            if read_bytes != encoded_record:
                raise BrokerError("broker reservation readback byte mismatch")
            if read_payload != record:
                raise BrokerError("broker reservation readback record mismatch")

            verify_identity()
            return record
        finally:
            if temp_fd >= 0:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            if not linked:
                try:
                    os.unlink(temp_filename, dir_fd=run_fd)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise BrokerError(f"cannot safely clean temporary reservation file: {exc}") from exc


def read_cycle_reservation(root: Path, controller_run_id: str) -> dict:
    if not isinstance(controller_run_id, str):
        raise BrokerError("controller run ID must be a string")
    if MAINTENANCE_RUN_RE.fullmatch(controller_run_id):
        raise BrokerError("cycle reservation rejects maintenance run ID; controller run ID must be a cycle run ID")
    if not CYCLE_RUN_RE.fullmatch(controller_run_id):
        raise BrokerError("controller run ID must be a cycle run ID")

    with _open_existing_cycle_run_directory(root, controller_run_id) as (
        run_path, run_fd, verify_identity
    ):
        startup_payload, _startup_bytes = _read_cycle_private_json(
            run_path, run_fd, "preparation-startup.json"
        )
        record_payload, raw_record_bytes = _read_cycle_private_json(
            run_path, run_fd, "broker-reservation.json"
        )

        startup = _parse_cycle_preparation_startup(startup_payload, controller_run_id)
        if startup_payload.get("schema_version") != 1 or type(startup_payload.get("schema_version")) is not int:
            raise BrokerError("preparation startup schema version must be integer 1")
        if getattr(startup, "schema_version", 1) != 1:
            raise BrokerError("preparation startup schema version must be integer 1")
        if startup.cycle_id != controller_run_id:
            raise BrokerError("preparation startup cycle ID does not match controller run ID")

        try:
            canonical_bytes = json.dumps(
                record_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise BrokerError("broker reservation record cannot be canonically encoded") from exc
        if canonical_bytes != raw_record_bytes:
            raise BrokerError("broker reservation record is not byte-for-byte canonical JSON")

        if set(record_payload) != {
            "schema_version",
            "controller_run_id",
            "request_id",
            "reservation",
            "request_sha256",
            "created_at",
        }:
            raise BrokerError("broker reservation record does not match the exact contract")

        if record_payload.get("schema_version") != "1.0" or type(record_payload.get("schema_version")) is not str:
            raise BrokerError("broker reservation schema version is invalid")
        if record_payload.get("controller_run_id") != controller_run_id or type(record_payload.get("controller_run_id")) is not str:
            raise BrokerError("broker reservation controller run ID mismatch")
        request_id = record_payload.get("request_id")
        if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
            raise BrokerError("broker reservation request ID is invalid")

        reservation = record_payload.get("reservation")
        if not isinstance(reservation, dict) or set(reservation) != RESERVATION_KEYS:
            raise BrokerError("broker reservation does not match the exact contract")
        for key in RESERVATION_KEYS:
            val = reservation[key]
            if type(val) is not str:
                raise BrokerError(f"broker reservation field {key} must be a string")

        if reservation["controller_run_id"] != controller_run_id:
            raise BrokerError("reservation controller run ID mismatch")
        if not RUN_PREFIX_RE.fullmatch(reservation["run_prefix"]):
            raise BrokerError("reservation run-prefix is invalid")
        task_rel = _task_relative_path(reservation["task_path"])
        if task_rel.as_posix() != reservation["task_path"]:
            raise BrokerError("reservation task_path is not canonical")
        if not SHA_RE.fullmatch(reservation["task_sha256"]):
            raise BrokerError("reservation task SHA is invalid")
        if not HEAD_RE.fullmatch(reservation["source_head"]):
            raise BrokerError("reservation source HEAD is invalid")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reservation["assignment_id"]):
            raise BrokerError("reservation assignment ID is invalid")
        if not SHA_RE.fullmatch(reservation["assignment_sha256"]):
            raise BrokerError("reservation assignment SHA is invalid")

        request_sha256 = record_payload.get("request_sha256")
        if not isinstance(request_sha256, str) or not SHA_RE.fullmatch(request_sha256):
            raise BrokerError("broker reservation request SHA is invalid")

        created_at = record_payload.get("created_at")
        if (
            not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
            or not math.isfinite(created_at)
        ):
            raise BrokerError("broker reservation created_at is invalid")

        if reservation["source_head"] != startup.source_head:
            raise BrokerError("reservation source HEAD does not match preparation startup")
        if reservation["assignment_sha256"] != startup.assignment_sha256:
            raise BrokerError("reservation assignment SHA does not match preparation startup")

        verify_identity()
        return record_payload


def _submit_request(root: Path, request: dict, timeout: int, *, maintenance: bool) -> int:
    state_root, status_path, requests, _claimed, responses = broker_paths(root)
    with exclusive_broker_lock(state_root / "client.lock"):
        status_value = _read_broker_status(root, status_path)
        if (status_value.get("state") != "IDLE"
                or type(status_value.get("pid")) is not int
                or not isinstance(status_value.get("updated_at"), (int, float))):
            raise BrokerError("provider broker is not ready")
        if time.time() - status_value["updated_at"] > STATUS_MAX_AGE_SECONDS:
            raise BrokerError("provider broker status is stale")
        if maintenance:
            with _exclusive_maintenance_supervisor_lock(root):
                _require_maintenance_stopped(root)
                scope = load_active_assignment_scope(root)
                if (request["assignment_id"] != scope.assignment_id
                        or request["assignment_sha256"] != scope.sha256):
                    raise BrokerError("broker assignment binding mismatch")
                task, _sources = load_task(root, root / request["task_path"])
                validate_task_scope(scope, task)
                _prune_maintenance_records(root)
                _publish_maintenance_record(root, request)
        else:
            publish_cycle_reservation(root, request)
        request_path = requests / f"{request['request_id']}.json"
        response_path = _response_path(responses, request["request_id"])
        _atomic_new_json(request_path, request, MAX_REQUEST_BYTES)
        deadline = time.monotonic() + _overall_timeout(timeout) + 30
        last_health_check = 0.0
        while not _published_queue_file(response_path, "broker response"):
            if time.monotonic() >= deadline:
                raise BrokerError("provider broker response deadline exceeded")
            if time.monotonic() - last_health_check >= 1:
                current_status = _read_broker_status(root, status_path)
                if (current_status.get("state") not in {"IDLE", "RUNNING"}
                        or current_status.get("pid") != status_value["pid"]
                        or not isinstance(current_status.get("updated_at"), (int, float))
                        or time.time() - current_status["updated_at"] > STATUS_MAX_AGE_SECONDS
                        or (current_status.get("state") == "RUNNING"
                            and current_status.get("request_id") != request["request_id"])):
                    raise BrokerError("provider broker heartbeat is unavailable or mismatched")
                last_health_check = time.monotonic()
            time.sleep(POLL_SECONDS)
        response = _safe_read_existing_response(root, response_path)
        validated = validate_response(request, response)
        if validated["stdout"]:
            sys.stdout.write(validated["stdout"])
            sys.stdout.flush()
        if validated["stderr"]:
            sys.stderr.write(validated["stderr"])
            if not validated["stderr"].endswith("\n"):
                sys.stderr.write("\n")
            sys.stderr.flush()
        response_path.unlink()
    return validated["exit_code"]


def submit(
    root: Path,
    task_path: Path,
    run_prefix: str,
    timeout: int,
    environment: Mapping[str, str] = os.environ,
) -> int:
    request = make_request(root, task_path, run_prefix, timeout, environment)
    return _submit_request(root, request, timeout, maintenance=False)


def submit_maintenance(root: Path, task_path: Path, run_prefix: str, timeout: int) -> int:
    """Create a distinct, auditable controller identity inside the trusted client."""
    root = canonical_root(root)
    scope = load_active_assignment_scope(root)
    run_id = (
        f"maint-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
        f"{uuid.uuid4().hex[:16]}"
    )
    environment = {
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED": "1",
        "TWO_SITE_ASSIGNMENT_ID": scope.assignment_id,
        "TWO_SITE_ASSIGNMENT_SHA256": scope.sha256,
        "TWO_SITE_MAINTENANCE_RUN_ID": run_id,
    }
    request = make_request(
        root, task_path, run_prefix, timeout, environment, maintenance=True
    )
    return _submit_request(root, request, timeout, maintenance=True)


def status(root: Path) -> int:
    _state_root, status_path, _requests, _claimed, _responses = broker_paths(root)
    try:
        value = _read_broker_status(root, status_path)
        pid = value.get("pid") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get("state") not in {"IDLE", "RUNNING"}
                or type(pid) is not int
                or not isinstance(value.get("updated_at"), (int, float))
                or time.time() - value["updated_at"] > STATUS_MAX_AGE_SECONDS):
            raise BrokerError("provider broker is not healthy")
    except (OSError, ValueError, json.JSONDecodeError, BrokerError) as exc:
        print(json.dumps({"state": "UNAVAILABLE", "reason": str(exc)}))
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve")
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--task", type=Path, required=True)
    submit_parser.add_argument("--run-prefix", required=True)
    submit_parser.add_argument("--timeout", type=int, default=300)
    maintenance_parser = subparsers.add_parser("submit-maintenance")
    maintenance_parser.add_argument("--task", type=Path, required=True)
    maintenance_parser.add_argument("--run-prefix", required=True)
    maintenance_parser.add_argument("--timeout", type=int, default=300)
    subparsers.add_parser("status")
    args = parser.parse_args()
    root = canonical_root(args.root)
    try:
        if args.command == "serve":
            return serve(root)
        if args.command == "submit":
            return submit(root, args.task, args.run_prefix, args.timeout)
        if args.command == "submit-maintenance":
            return submit_maintenance(root, args.task, args.run_prefix, args.timeout)
        return status(root)
    except (BrokerError, ProposalError, AssignmentScopeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "BROKER_ERROR", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
