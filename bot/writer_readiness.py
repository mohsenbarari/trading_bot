"""Term-bound readiness marker for the fenced Telegram bot.

The marker is deliberately container-local and contains no credentials.  It
is written only after a successful Telegram long-poll request, then checked by
the Docker healthcheck together with the *live* local Writer Witness term.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any

from core.application_writer_term import ApplicationWriterTermError, ValidatedWriterTerm
from core.db import require_application_writer_term


MARKER_SCHEMA = "gold-trade-bot-writer-readiness-v1"
MARKER_STATUS = "ready"
MAX_MARKER_BYTES = 8 * 1024


class BotWriterReadinessError(RuntimeError):
    """Raised when a bot readiness marker cannot be securely proved."""


def configured_marker_path(value: object) -> Path | None:
    """Normalize the optional marker path without opening the filesystem."""

    if value is None or value == "":
        return None
    if isinstance(value, Path):
        path = value
    elif isinstance(value, str):
        path = Path(value)
    else:
        raise BotWriterReadinessError("bot readiness marker path is invalid")
    if not path.is_absolute() or path.name in {"", ".", ".."} or ".." in path.parts:
        raise BotWriterReadinessError("bot readiness marker path must be absolute and closed")
    return path


def _validate_directory(info: os.stat_result) -> None:
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise BotWriterReadinessError("bot readiness marker ancestor is not a directory")
    if info.st_uid != 0:
        raise BotWriterReadinessError("bot readiness marker ancestor is not root owned")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
        raise BotWriterReadinessError("bot readiness marker ancestor is writable")


def _validate_parent(path: Path) -> None:
    if os.geteuid() != 0:
        raise BotWriterReadinessError("bot readiness marker must be managed by root")
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        if component in {"", ".", ".."}:
            raise BotWriterReadinessError("bot readiness marker path is not canonical")
        current = current / component
        try:
            info = current.lstat()
        except OSError as exc:
            raise BotWriterReadinessError("bot readiness marker parent is unavailable") from exc
        _validate_directory(info)


def _validate_marker_leaf(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_nlink != 1
        or info.st_size < 1
        or info.st_size > MAX_MARKER_BYTES
    ):
        raise BotWriterReadinessError("bot readiness marker is not a root-only regular file")


def clear_writer_ready_marker(path: Path | None) -> None:
    """Remove only a previously validated private marker."""

    if path is None:
        return
    _validate_parent(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BotWriterReadinessError("cannot inspect bot readiness marker") from exc
    _validate_marker_leaf(info)
    try:
        path.unlink()
    except OSError as exc:
        raise BotWriterReadinessError("cannot remove prior bot readiness marker") from exc


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise BotWriterReadinessError("bot readiness marker cannot be encoded") from exc


def _parse_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BotWriterReadinessError(f"bot readiness marker {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BotWriterReadinessError(f"bot readiness marker {label} is invalid") from exc
    if parsed.tzinfo is None:
        raise BotWriterReadinessError(f"bot readiness marker {label} is invalid")
    return parsed.astimezone(timezone.utc)


def _marker_payload(term: ValidatedWriterTerm, *, now: datetime) -> dict[str, Any]:
    return {
        "schema": MARKER_SCHEMA,
        "status": MARKER_STATUS,
        "pid": os.getpid(),
        "writer_epoch": term.writer_epoch,
        "lease_id": term.lease_id,
        "lease_expires_at": term.expires_at.astimezone(timezone.utc).isoformat(),
        "observed_at": now.astimezone(timezone.utc).isoformat(),
    }


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise BotWriterReadinessError("bot readiness marker write was incomplete")
        offset += written


def write_writer_ready_marker(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Atomically mark this live bot process ready under its active term."""

    _validate_parent(path)
    try:
        term = require_application_writer_term()
    except ApplicationWriterTermError as exc:
        raise BotWriterReadinessError("bot readiness marker has no active Writer Witness term") from exc
    if term is None:
        raise BotWriterReadinessError("bot readiness marker requires enabled Writer Witness enforcement")
    observed_at = now or datetime.now(timezone.utc)
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise BotWriterReadinessError("bot readiness marker clock is invalid")

    encoded = _canonical_bytes(_marker_payload(term, now=observed_at))
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BotWriterReadinessError:
        raise
    except OSError as exc:
        raise BotWriterReadinessError("cannot write bot readiness marker") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return _marker_payload(term, now=observed_at)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BotWriterReadinessError("bot readiness marker contains duplicate JSON keys")
        result[key] = value
    return result


def _read_marker(path: Path) -> dict[str, Any]:
    _validate_parent(path)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        _validate_marker_leaf(before)
        payload = os.read(descriptor, MAX_MARKER_BYTES + 1)
        if len(payload) > MAX_MARKER_BYTES:
            raise BotWriterReadinessError("bot readiness marker is oversized")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
        ):
            raise BotWriterReadinessError("bot readiness marker changed while being read")
    except BotWriterReadinessError:
        raise
    except OSError as exc:
        raise BotWriterReadinessError("cannot securely read bot readiness marker") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except BotWriterReadinessError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BotWriterReadinessError("bot readiness marker is invalid JSON") from exc
    expected_fields = {
        "schema",
        "status",
        "pid",
        "writer_epoch",
        "lease_id",
        "lease_expires_at",
        "observed_at",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise BotWriterReadinessError("bot readiness marker schema is invalid")
    return value


def check_writer_ready_marker(
    path: Path,
    *,
    maximum_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check marker freshness, process liveness, and its live term binding."""

    if type(maximum_age_seconds) is not int or not 1 <= maximum_age_seconds <= 60:
        raise BotWriterReadinessError("bot readiness marker maximum age is invalid")
    observed_now = now or datetime.now(timezone.utc)
    if not isinstance(observed_now, datetime) or observed_now.tzinfo is None:
        raise BotWriterReadinessError("bot readiness marker clock is invalid")
    try:
        term = require_application_writer_term()
    except ApplicationWriterTermError as exc:
        raise BotWriterReadinessError("bot readiness marker Writer Witness term is invalid") from exc
    if term is None:
        raise BotWriterReadinessError("bot readiness marker requires enabled Writer Witness enforcement")

    marker = _read_marker(path)
    if marker["schema"] != MARKER_SCHEMA or marker["status"] != MARKER_STATUS:
        raise BotWriterReadinessError("bot readiness marker status is invalid")
    if type(marker["pid"]) is not int or marker["pid"] <= 1:
        raise BotWriterReadinessError("bot readiness marker pid is invalid")
    if type(marker["writer_epoch"]) is not int or marker["writer_epoch"] != term.writer_epoch:
        raise BotWriterReadinessError("bot readiness marker term epoch does not match")
    if not isinstance(marker["lease_id"], str) or marker["lease_id"] != term.lease_id:
        raise BotWriterReadinessError("bot readiness marker lease does not match")
    if _parse_timestamp(marker["lease_expires_at"], label="lease expiry") != term.expires_at:
        raise BotWriterReadinessError("bot readiness marker lease expiry does not match")
    age_seconds = (observed_now.astimezone(timezone.utc) - _parse_timestamp(marker["observed_at"], label="observed_at")).total_seconds()
    if age_seconds < -1 or age_seconds > maximum_age_seconds:
        raise BotWriterReadinessError("bot readiness marker is stale")
    try:
        os.kill(marker["pid"], 0)
    except OSError as exc:
        raise BotWriterReadinessError("bot readiness marker process is not alive") from exc
    return marker


def main(argv: list[str] | None = None) -> int:
    """Docker healthcheck entrypoint; it never prints marker contents."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["--healthcheck"]:
        return 2
    from core.config import settings

    try:
        path = configured_marker_path(getattr(settings, "bot_writer_ready_marker_path", None))
        if path is None:
            raise BotWriterReadinessError("bot readiness marker is not configured")
        check_writer_ready_marker(
            path,
            maximum_age_seconds=getattr(settings, "bot_writer_ready_marker_max_age_seconds", 45),
        )
    except BotWriterReadinessError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
