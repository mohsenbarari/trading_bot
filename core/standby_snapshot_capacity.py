"""Fail-closed free-space checks for immutable WA-IR standby snapshots.

The snapshot transport deliberately retains committed source artifacts,
Object Storage objects, and restored candidates.  These helpers only inspect
the filesystem before a caller creates new data; they never delete or reclaim
anything.  Callers supply a conservative estimate of the *additional* bytes
their next operation needs and a minimum amount that must remain free after
the operation.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


AGE_CIPHERTEXT_OVERHEAD_RESERVATION_BYTES = 1024 * 1024
MAXIMUM_MANIFEST_PLAINTEXT_BYTES = 1024 * 1024


class SnapshotCapacityError(RuntimeError):
    """The configured filesystem cannot safely accommodate the next cycle."""


def _require_bytes(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotCapacityError(f"{field} must be a non-negative integer")
    return value


def require_capacity(
    path: Path,
    *,
    required_new_bytes: int,
    minimum_free_bytes: int,
    label: str,
) -> dict[str, Any]:
    """Return an evidence payload or fail before the caller creates data.

    ``shutil.disk_usage().free`` already accounts for all retained candidates
    and artifacts on the filesystem.  The caller therefore supplies only the
    bytes this new operation can add concurrently.
    """

    if not isinstance(label, str) or not label:
        raise SnapshotCapacityError("capacity label must be a non-empty string")
    required = _require_bytes(required_new_bytes, field="required_new_bytes")
    reserve = _require_bytes(minimum_free_bytes, field="minimum_free_bytes")
    if not path.is_absolute():
        raise SnapshotCapacityError("capacity path must be absolute")
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        raise SnapshotCapacityError(f"cannot inspect free space for {label}") from exc
    available = int(usage.free)
    needed = required + reserve
    if available < needed:
        raise SnapshotCapacityError(
            f"insufficient free space for {label}: available={available} required={required} reserve={reserve}"
        )
    return {
        "label": label,
        "path": str(path),
        "available_bytes": available,
        "required_new_bytes": required,
        "minimum_free_bytes": reserve,
        "remaining_bytes": available - required,
    }


def age_ciphertext_reservation_bytes(plaintext_bytes: int) -> int:
    """Return a conservative local reservation for one age ciphertext file.

    Age overhead for a single file is tiny, but reserving one MiB prevents a
    capacity proof from relying on an exact implementation detail of the
    encryptor.
    """

    plaintext = _require_bytes(plaintext_bytes, field="plaintext_bytes")
    return plaintext + AGE_CIPHERTEXT_OVERHEAD_RESERVATION_BYTES


MAXIMUM_MANIFEST_CIPHERTEXT_BYTES = age_ciphertext_reservation_bytes(
    MAXIMUM_MANIFEST_PLAINTEXT_BYTES
)


def manifest_workspace_reservation_bytes() -> int:
    """Reserve one ciphertext plus a worst-case plaintext decrypt output.

    Age does not compress, but a receive-side preflight must still reserve up
    to the ciphertext bound for each file before it rejects an oversized
    plaintext manifest after decrypting it.
    """

    return 2 * MAXIMUM_MANIFEST_CIPHERTEXT_BYTES
