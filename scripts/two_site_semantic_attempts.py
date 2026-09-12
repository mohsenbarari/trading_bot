"""Immutable semantic-attempt dedup records; caller owns orchestration locking."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any

try:
    from scripts.two_site_proposal_runner import bounded_file_bytes, unique_json
    from scripts.two_site_semantic_task import validate_identity
except ModuleNotFoundError:
    from two_site_proposal_runner import bounded_file_bytes, unique_json
    from two_site_semantic_task import validate_identity

__all__ = ["SemanticAttemptError", "extract_semantic_identity", "reserve_semantic_attempt"]

MARKER = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
_TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
_KEYS = frozenset({
    "schema_version", "fingerprint", "source_sha256", "stage_id",
    "gap_reference", "scenario_key", "task_id", "run_prefix", "state", "created_at",
})


class SemanticAttemptError(ValueError):
    """Raised when semantic identity or immutable attempt evidence is unsafe."""


def extract_semantic_identity(task: Any) -> dict[str, Any]:
    try:
        if not isinstance(task, dict) or not isinstance(task.get("goal"), str):
            raise SemanticAttemptError("task goal is invalid")
        goal = task["goal"]
        if goal.count(MARKER) != 1:
            raise SemanticAttemptError("task needs exactly one semantic marker")
        package = unique_json(goal.split(MARKER, 1)[1])
        if not isinstance(package, dict) or not isinstance(package.get("semantic_identity"), dict):
            raise SemanticAttemptError("semantic identity package is invalid")
        return copy.deepcopy(validate_identity(package["semantic_identity"]))
    except (KeyboardInterrupt, SystemExit, SemanticAttemptError):
        raise
    except Exception as exc:
        raise SemanticAttemptError(f"semantic identity extraction failed: {exc}") from exc


def _canonical_dirs(root: Any, pipeline_dir: Any) -> tuple[Path, Path, Path]:
    root_path = Path(root).resolve(strict=True)
    raw_pipeline = Path(pipeline_dir)
    candidate = Path(os.path.abspath(raw_pipeline if raw_pipeline.is_absolute() else root_path / raw_pipeline))
    resolved = candidate.resolve(strict=True)
    if (not root_path.is_dir() or not resolved.is_dir() or resolved != candidate
            or resolved == root_path or not resolved.is_relative_to(root_path)):
        raise SemanticAttemptError("pipeline directory is not canonical inside root")
    attempts = resolved / "semantic-attempts"
    try:
        os.mkdir(attempts, 0o700)
    except FileExistsError:
        pass
    before = attempts.lstat()
    if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid()
            or (before.st_mode & 0o777) != 0o700 or attempts.resolve(strict=True) != attempts):
        raise SemanticAttemptError("semantic attempts directory is untrusted")
    return root_path, resolved, attempts


def _record(identity: dict[str, Any], task_id: str, run_prefix: str, created_at: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fingerprint": identity["fingerprint"],
        "source_sha256": identity["source_sha256"],
        "stage_id": identity["stage_id"],
        "gap_reference": identity["gap_reference"],
        "scenario_key": identity["scenario_key"],
        "task_id": task_id,
        "run_prefix": run_prefix,
        "state": "RESERVED",
        "created_at": created_at,
    }


def _validate_existing(root: Path, path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    raw = bounded_file_bytes(root, path, 4096, "semantic attempt")
    found = unique_json(raw.decode("utf-8"))
    current = path.lstat()
    if (not isinstance(found, dict) or set(found) != _KEYS or found.get("schema_version") != 1
            or found.get("state") != "RESERVED" or current.st_uid != os.geteuid()
            or (current.st_mode & 0o777) != 0o600):
        raise SemanticAttemptError("existing semantic attempt record is invalid")
    expected = {key: identity[key] for key in (
        "fingerprint", "source_sha256", "stage_id", "gap_reference", "scenario_key",
    )}
    if any(found.get(key) != value for key, value in expected.items()):
        raise SemanticAttemptError("existing semantic attempt identity mismatch")
    if (not isinstance(found.get("task_id"), str) or not _TASK_RE.fullmatch(found["task_id"])
            or not isinstance(found.get("run_prefix"), str) or not _PREFIX_RE.fullmatch(found["run_prefix"])
            or isinstance(found.get("created_at"), bool)
            or not isinstance(found.get("created_at"), (int, float))
            or not math.isfinite(found["created_at"])):
        raise SemanticAttemptError("existing semantic attempt metadata is invalid")
    return found


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write made no progress")
        view = view[written:]


def reserve_semantic_attempt(
    root: Any, pipeline_dir: Any, task: Any, task_id: Any,
    run_prefix: Any, now: Any = None,
) -> dict[str, Any]:
    temp: Path | None = None
    try:
        if not isinstance(task_id, str) or not _TASK_RE.fullmatch(task_id):
            raise SemanticAttemptError("invalid task_id")
        if not isinstance(run_prefix, str) or not _PREFIX_RE.fullmatch(run_prefix):
            raise SemanticAttemptError("invalid run_prefix")
        created_at = time.time() if now is None else now
        if (isinstance(created_at, bool) or not isinstance(created_at, (int, float))
                or not math.isfinite(created_at)):
            raise SemanticAttemptError("invalid reservation time")
        identity = extract_semantic_identity(task)
        root_path, _, attempts = _canonical_dirs(root, pipeline_dir)
        target = attempts / f"{identity['fingerprint']}.json"
        if os.path.lexists(target):
            found = _validate_existing(root_path, target, identity)
            raise SemanticAttemptError(
                f"duplicate semantic task: {identity['fingerprint']} "
                f"task={found['task_id']} run={found['run_prefix']}"
            )
        record = _record(identity, task_id, run_prefix, float(created_at))
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        temp = attempts / f".{identity['fingerprint']}.{os.getpid()}.{time.time_ns()}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temp, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            value = os.fstat(descriptor)
            if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid()
                    or value.st_nlink != 1 or (value.st_mode & 0o777) != 0o600):
                raise SemanticAttemptError("temporary semantic attempt is untrusted")
        finally:
            os.close(descriptor)
        try:
            os.link(temp, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise SemanticAttemptError(f"duplicate semantic task: {identity['fingerprint']}") from exc
        os.unlink(temp)
        temp = None
        directory_fd = os.open(attempts, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return copy.deepcopy(record)
    except (KeyboardInterrupt, SystemExit, SemanticAttemptError):
        raise
    except Exception as exc:
        raise SemanticAttemptError(f"semantic attempt reservation failed: {exc}") from exc
    finally:
        if temp is not None:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
