"""Fail-closed writer guard for the dedicated Full Matrix capacity drill.

This module deliberately has no allocator and no remote-control surface.  It
only consumes a root-owned marker that is bind-mounted read-only from the
same dedicated staging filesystem as the WebApp data planes.  Therefore a
capacity fault is stopped at the HTTP writer fence before PostgreSQL, Redis,
the event outbox, or blob storage are asked to accept another mutation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any


SCHEMA = "three-site-full-matrix-capacity-guard-v1"
MAX_MARKER_BYTES = 8 * 1024


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate marker field")
        result[key] = value
    return result


def _read_marker(path: Path) -> dict[str, Any]:
    """Read only a root-owned, immutable regular marker without following links."""

    parent = path.parent.lstat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise ValueError("capacity guard directory is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o222
            or not 2 <= metadata.st_size <= MAX_MARKER_BYTES
        ):
            raise ValueError("capacity guard marker is unsafe")
        raw = os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise ValueError("capacity guard marker changed while reading")
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("capacity guard marker is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("capacity guard marker is not an object")
    return value


def capacity_guard_reasons(
    *,
    marker_file: str | None,
    release_sha: str | None,
    physical_site: str | None,
    three_site_enabled: bool,
) -> tuple[str, ...]:
    """Return a stable fence reason if the bounded capacity drill is active.

    Missing configuration is deliberately an inactive guard.  Once configured,
    any marker issue is fail-closed: removing the reserve before its marker is
    removed cannot accidentally re-open writers while the filesystem is still
    under pressure.
    """

    if not three_site_enabled or not marker_file:
        return ()
    if physical_site not in {"webapp_fi", "webapp_ir"}:
        return ()
    path = Path(marker_file)
    if not path.is_absolute():
        return ("full_matrix_capacity_guard_invalid",)
    try:
        if not path.exists() and not path.is_symlink():
            return ()
        marker = _read_marker(path)
        expected = {
            "schema",
            "state",
            "campaign_id",
            "release_sha",
            "operation_id",
            "role",
            "storage_total_bytes",
            "available_bytes",
            "hard_limit_bytes",
        }
        if (
            set(marker) != expected
            or marker.get("schema") != SCHEMA
            or marker.get("state") not in {"preparing", "armed"}
            or marker.get("release_sha") != release_sha
            or marker.get("role") != physical_site
            or not isinstance(marker.get("campaign_id"), str)
            or not isinstance(marker.get("operation_id"), str)
            or any(
                type(marker[name]) is not int or int(marker[name]) <= 0
                for name in ("storage_total_bytes", "available_bytes", "hard_limit_bytes")
            )
            or int(marker["hard_limit_bytes"]) >= int(marker["storage_total_bytes"])
        ):
            return ("full_matrix_capacity_guard_invalid",)
        filesystem = os.statvfs(path.parent)
        available = int(filesystem.f_bavail) * int(filesystem.f_frsize)
        # A marker is an explicit administrative safety interlock.  It stays
        # closed even if someone frees space out-of-band; only the exact agent
        # cleanup may remove it after rechecking the reserve is gone.
        if available > int(marker["hard_limit_bytes"]):
            return ("full_matrix_capacity_guard_inconsistent",)
        return ("full_matrix_capacity_hard_limit",)
    except (OSError, ValueError, TypeError):
        return ("full_matrix_capacity_guard_invalid",)
