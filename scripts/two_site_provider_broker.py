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
    from scripts.two_site_proposal_pipeline import validate_quota_evidence
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
    from two_site_proposal_pipeline import validate_quota_evidence  # type: ignore[no-redef]
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
    executor: Callable[[Path, dict, Path, AssignmentScope], dict],
    root: Path,
    request: dict,
    task_path: Path,
    scope: AssignmentScope,
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
        response = executor(root, request, task_path, scope)
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=HEARTBEAT_SECONDS + 1)
    if heartbeat_errors:
        raise BrokerError("provider broker heartbeat failed") from heartbeat_errors[0]
    return response


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


def _claim_next(requests: Path, claimed: Path) -> Path | None:
    for name in _queue_names(requests):
        if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
            raise BrokerError("broker request queue contains an invalid entry")
        source = requests / name
        destination = claimed / name
        if not _published_queue_file(source, "broker request"):
            return None
        if os.path.lexists(destination):
            raise BrokerError("broker claimed request already exists")
        os.replace(source, destination)
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


def _recover_interrupted_claims(root: Path, claimed: Path, responses: Path) -> None:
    """Fail ambiguous in-flight work closed; never repeat its provider call."""
    for name in _queue_names(claimed):
        if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
            raise BrokerError("broker claimed queue contains an invalid entry")
        path = claimed / name
        request: object = {}
        try:
            request = _read_json_file(root, path, MAX_REQUEST_BYTES, "claimed broker request")
            request_id = request.get("request_id") if isinstance(request, dict) else None
            if request_id != name[:-5]:
                raise BrokerError("claimed broker request filename binding is invalid")
            response_path = _response_path(responses, request_id)
            if not os.path.lexists(response_path):
                _atomic_new_json(
                    response_path,
                    _error_response(request, BrokerError(
                        "broker restarted after an ambiguous in-flight provider call; refusing duplicate execution"
                    )),
                    MAX_RESPONSE_BYTES,
                )
        finally:
            path.unlink(missing_ok=True)


def serve(
    root: Path,
    *,
    once: bool = False,
    executor: Callable[[Path, dict, Path, AssignmentScope], dict] = execute_pipeline,
    scope_loader: Callable[[Path], AssignmentScope] = load_active_assignment_scope,
    install_signal_handlers: bool = True,
) -> int:
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
        _write_status(status_path, "IDLE", started_at=started_at)
        try:
            # A normal STOP is a supervisor boundary request, not a cancellation
            # of its active broker call.  Exit only after that supervisor has
            # acknowledged STOPPED; ``once`` remains an explicit test/CLI mode.
            while not _STOP and not (
                stop_path.exists()
                and not once
                and _supervisor_stop_acknowledged(root)
            ):
                claimed_path = _claim_next(requests, claimed)
                if claimed_path is None:
                    if time.time() - last_prune >= 60:
                        _prune_orphan_responses(responses)
                        last_prune = time.time()
                    if time.time() - last_heartbeat >= 10:
                        _write_status(status_path, "IDLE", started_at=started_at)
                        last_heartbeat = time.time()
                    time.sleep(POLL_SECONDS)
                    continue
                request: object = {}
                response: dict
                try:
                    request = _read_json_file(
                        root, claimed_path, MAX_REQUEST_BYTES, "broker request"
                    )
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
                request_id = claimed_path.stem
                response_path = _response_path(responses, request_id)
                try:
                    _atomic_new_json(response_path, response, MAX_RESPONSE_BYTES)
                finally:
                    claimed_path.unlink(missing_ok=True)
                _write_status(
                    status_path, "IDLE", started_at=started_at,
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

        expected_model = WORKER_MODEL if role == "worker" else MODEL
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
        try:
            response = _read_json_file(
                root, response_path, MAX_RESPONSE_BYTES, "broker response"
            )
        finally:
            response_path.unlink(missing_ok=True)
    validated = validate_response(request, response)
    if validated["stdout"]:
        sys.stdout.write(validated["stdout"])
        sys.stdout.flush()
    if validated["stderr"]:
        sys.stderr.write(validated["stderr"])
        if not validated["stderr"].endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()
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
