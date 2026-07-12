"""Durable append-only audit sink with hash-chain integrity metadata."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.log_redaction import REDACTED, redact


_logger = logging.getLogger(__name__)
_write_lock = threading.RLock()
_EVENT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AuditTrailIntegrityError(RuntimeError):
    """The existing append-only trail cannot be extended without hiding corruption."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _audit_trail_path() -> str | None:
    try:
        from core.config import settings

        return getattr(settings, "audit_trail_path", None)
    except Exception:
        return os.getenv("AUDIT_TRAIL_PATH") or None


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_payload(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _last_event_hash(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) != b"\n":
            raise AuditTrailIntegrityError("audit trail has a partial final line")
        position = handle.tell() - 1
        buffer = bytearray()
        while position > 0:
            position -= 1
            handle.seek(position)
            char = handle.read(1)
            if char == b"\n":
                break
            buffer.extend(char)
        try:
            line = bytes(reversed(buffer)).decode("utf-8", errors="strict")
            record = json.loads(line)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise AuditTrailIntegrityError("audit trail final line is invalid") from exc
    if not isinstance(record, dict):
        raise AuditTrailIntegrityError("audit trail final record is not an object")
    event_hash = record.get("event_hash")
    if not isinstance(event_hash, str) or not _EVENT_HASH_PATTERN.fullmatch(event_hash):
        raise AuditTrailIntegrityError("audit trail final record has no valid event hash")
    hash_input = dict(record)
    hash_input.pop("event_hash", None)
    if not hmac.compare_digest(event_hash, _hash_payload(hash_input)):
        raise AuditTrailIntegrityError("audit trail final record hash does not match")
    return event_hash


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _append_durable_line(path: Path, encoded_line: bytes) -> None:
    existed = path.exists()
    original_size = path.stat().st_size if existed else 0
    file_fd: int | None = None
    append_started = False
    try:
        file_fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        append_started = True
        written = 0
        while written < len(encoded_line):
            count = os.write(file_fd, encoded_line[written:])
            if count <= 0:
                raise OSError("audit trail write made no progress")
            written += count
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        if not existed:
            _fsync_directory(path.parent)
    except Exception:
        if file_fd is not None:
            os.close(file_fd)
        if append_started:
            try:
                if existed:
                    recovery_fd = os.open(path, os.O_WRONLY)
                    try:
                        os.ftruncate(recovery_fd, original_size)
                        os.fsync(recovery_fd)
                    finally:
                        os.close(recovery_fd)
                elif path.exists():
                    path.unlink()
                    _fsync_directory(path.parent)
            except Exception:
                _logger.exception(
                    "Audit trail append rollback failed",
                    extra={"event": "audit.sink.rollback_failed", "log_class": "audit"},
                )
        raise


def build_audit_record(
    payload: dict[str, Any],
    *,
    previous_hash: str | None = None,
    durable_written: bool,
    durable_reason: str | None = None,
    durable_path: str | None = None,
    durable_error_type: str | None = None,
) -> dict[str, Any]:
    safe_payload = redact(payload)
    if not isinstance(safe_payload, dict):
        safe_payload = {"redacted": REDACTED}
    record = {
        "audit_event_id": str(uuid.uuid4()),
        "audit_recorded_at": _utc_now_iso(),
        "audit_durable": durable_written,
        "previous_hash": previous_hash,
        "payload": safe_payload,
    }
    if durable_reason:
        record["audit_durable_reason"] = durable_reason
    if durable_path:
        record["audit_trail_path"] = durable_path
    if durable_error_type:
        record["audit_durable_error_type"] = durable_error_type
    record["event_hash"] = _hash_payload(record)
    return record


def write_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    path_raw = _audit_trail_path()
    if not path_raw:
        return build_audit_record(payload, durable_written=False, durable_reason="audit_trail_unconfigured")

    path = Path(path_raw)
    with _write_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.with_name(f"{path.name}.lock")
            with lock_path.open("a", encoding="utf-8") as process_lock:
                fcntl.flock(process_lock.fileno(), fcntl.LOCK_EX)
                try:
                    record = build_audit_record(
                        payload,
                        previous_hash=_last_event_hash(path),
                        durable_written=True,
                        durable_reason=None,
                        durable_path=str(path),
                    )
                    _append_durable_line(
                        path,
                        (_canonical_json(record) + "\n").encode("utf-8"),
                    )
                finally:
                    fcntl.flock(process_lock.fileno(), fcntl.LOCK_UN)
            return record
        except Exception as exc:
            integrity_failure = isinstance(exc, AuditTrailIntegrityError)
            reason = (
                "audit_trail_integrity_failed"
                if integrity_failure
                else "audit_trail_write_failed"
            )
            _logger.warning(
                "Durable audit sink integrity check failed"
                if integrity_failure
                else "Durable audit sink write failed",
                extra={
                    "event": (
                        "audit.sink.integrity_failed"
                        if integrity_failure
                        else "audit.sink.write_failed"
                    ),
                    "log_class": "audit",
                    "error_type": type(exc).__name__,
                },
            )
            return build_audit_record(
                payload,
                durable_written=False,
                durable_reason=reason,
                durable_path=str(path),
                durable_error_type=type(exc).__name__,
            )
