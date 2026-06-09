"""Durable append-only audit sink with hash-chain integrity metadata."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.log_redaction import REDACTED, redact


_logger = logging.getLogger(__name__)
_write_lock = threading.RLock()


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
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            buffer = bytearray()
            while position > 0:
                position -= 1
                handle.seek(position)
                char = handle.read(1)
                if char == b"\n" and buffer:
                    break
                if char != b"\n":
                    buffer.extend(char)
            line = bytes(reversed(buffer)).decode("utf-8")
        record = json.loads(line)
        event_hash = record.get("event_hash")
        return str(event_hash) if event_hash else None
    except Exception:
        return None


def build_audit_record(payload: dict[str, Any], *, previous_hash: str | None = None) -> dict[str, Any]:
    safe_payload = redact(payload)
    if not isinstance(safe_payload, dict):
        safe_payload = {"redacted": REDACTED}
    record = {
        "audit_event_id": str(uuid.uuid4()),
        "audit_recorded_at": _utc_now_iso(),
        "previous_hash": previous_hash,
        "payload": safe_payload,
    }
    record["event_hash"] = _hash_payload(record)
    return record


def write_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    path_raw = _audit_trail_path()
    if not path_raw:
        return build_audit_record(payload)

    path = Path(path_raw)
    with _write_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = build_audit_record(payload, previous_hash=_last_event_hash(path))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical_json(record) + "\n")
            return record
        except Exception as exc:
            _logger.warning(
                "Durable audit sink write failed",
                extra={
                    "event": "audit.sink.write_failed",
                    "log_class": "audit",
                    "error_type": type(exc).__name__,
                },
            )
            return build_audit_record(payload)

