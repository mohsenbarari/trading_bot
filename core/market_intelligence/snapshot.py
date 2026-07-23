"""Bounded local JSON snapshot loading for Shadow inference."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.market_intelligence.contracts import SnapshotUnavailableError


DEFAULT_MAXIMUM_SNAPSHOT_BYTES = 5 * 1024 * 1024


class AtomicJsonSnapshotProvider:
    """Read an operator-managed snapshot written through atomic replacement."""

    def __init__(
        self,
        path: str | Path,
        *,
        maximum_bytes: int = DEFAULT_MAXIMUM_SNAPSHOT_BYTES,
    ) -> None:
        self.path = Path(path)
        self.maximum_bytes = int(maximum_bytes)
        if self.maximum_bytes <= 0:
            raise ValueError("maximum snapshot size must be positive")

    def load(self) -> Mapping[str, Any]:
        try:
            before = self.path.stat()
        except (OSError, ValueError) as exc:
            raise SnapshotUnavailableError("snapshot_file_unavailable") from exc
        if not self.path.is_file():
            raise SnapshotUnavailableError("snapshot_path_is_not_file")
        if before.st_size <= 0:
            raise SnapshotUnavailableError("snapshot_file_empty")
        if before.st_size > self.maximum_bytes:
            raise SnapshotUnavailableError("snapshot_file_too_large")
        try:
            payload = self.path.read_bytes()
            after = self.path.stat()
        except OSError as exc:
            raise SnapshotUnavailableError("snapshot_read_failed") from exc
        if (
            before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise SnapshotUnavailableError("snapshot_changed_during_read")
        try:
            value: Any = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotUnavailableError("snapshot_json_invalid") from exc
        if not isinstance(value, dict):
            raise SnapshotUnavailableError("snapshot_root_must_be_object")
        if not value.get("generated_at_utc"):
            raise SnapshotUnavailableError("snapshot_generated_at_missing")
        settlements = value.get("settlements")
        if not isinstance(settlements, dict):
            raise SnapshotUnavailableError("snapshot_settlements_missing")
        return value
