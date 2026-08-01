"""Fail-closed tripwire: the Object-delta MVP is not a full data mirror.

The repository contains useful Object-delta source/receiver scaffolding, but
the currently executable receiver registry is intentionally much narrower
than the 23-table source registry.  A future campaign, deployment renderer,
or readiness report must not mistake that scaffolding for the project's
required FI/IR full-mirror data plane.

This module is a pure inventory check.  It neither enables a receiver nor
opens a database, reads settings/files, contacts a provider/Object Storage,
or performs a mutation.  It is deliberately tied to the MVP receiver
registry; a future physical PostgreSQL/WAL data plane needs its own positive
readiness evidence and must not make this check report ``ready``.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.append_only_sync_delta_payload import OBJECT_DELTA_SYNC_TABLES
from core.object_delta_receiver_mvp_handlers import (
    OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY,
)
from core.object_delta_receiver_registry import (
    ReceiverApplyStatus,
    receiver_table_specs,
)


OBJECT_DELTA_MVP_FULL_MIRROR_FENCE_SCHEMA = (
    "gold-trade-object-delta-mvp-full-mirror-fence-v1"
)


class ObjectDeltaMvpFullMirrorFenceError(RuntimeError):
    """The MVP was offered where a complete data mirror is required."""


@dataclass(frozen=True)
class ObjectDeltaMvpFullMirrorAssessment:
    """Pure diagnostic facts proving the current receiver is incomplete."""

    source_tables: tuple[str, ...]
    receiver_tables: tuple[str, ...]
    unavailable_receiver_tables: tuple[str, ...]
    executable_receiver_slots: tuple[tuple[str, str], ...]
    missing_receiver_slots: tuple[tuple[str, str], ...]

    @property
    def status(self) -> str:
        """Always blocked for the current MVP; this is not a readiness type."""

        return "blocked"


def assess_object_delta_mvp_full_mirror() -> ObjectDeltaMvpFullMirrorAssessment:
    """Inventory MVP coverage without granting any runtime authority.

    The source payload registry and receiver declaration registry must at
    least name the same tables.  A mismatch is itself a configuration error;
    a matching registry remains blocked until every declared receiver slot is
    executable and a separate full-data-plane contract supplies positive
    evidence for non-database continuity.
    """

    source_tables = tuple(sorted(OBJECT_DELTA_SYNC_TABLES))
    specs = receiver_table_specs()
    receiver_tables = tuple(sorted(specs))
    if receiver_tables != source_tables:
        raise ObjectDeltaMvpFullMirrorFenceError(
            "Object-delta source and receiver table registries differ"
        )
    unavailable = tuple(
        table
        for table in receiver_tables
        if specs[table].apply_status is ReceiverApplyStatus.UNAVAILABLE
    )
    executable = tuple(sorted(OBJECT_DELTA_RECEIVER_MVP_EXECUTION_REGISTRY))
    declared_slots = tuple(
        sorted(
            (table, operation)
            for table in receiver_tables
            for operation in specs[table].allowed_operations
        )
    )
    executable_set = set(executable)
    missing = tuple(slot for slot in declared_slots if slot not in executable_set)
    return ObjectDeltaMvpFullMirrorAssessment(
        source_tables=source_tables,
        receiver_tables=receiver_tables,
        unavailable_receiver_tables=unavailable,
        executable_receiver_slots=executable,
        missing_receiver_slots=missing,
    )


def require_object_delta_mvp_not_full_mirror() -> ObjectDeltaMvpFullMirrorAssessment:
    """Fail closed whenever a caller tries to use the MVP as a full mirror."""

    assessment = assess_object_delta_mvp_full_mirror()
    if (
        assessment.unavailable_receiver_tables
        or assessment.missing_receiver_slots
        or assessment.executable_receiver_slots
        != (("commodities", "INSERT"),)
    ):
        raise ObjectDeltaMvpFullMirrorFenceError(
            "Object-delta MVP is not a complete FI/IR data mirror; "
            "a physical PostgreSQL/WAL data-plane readiness proof is required"
        )
    # Defensive: if the shape unexpectedly became complete, the caller still
    # cannot reinterpret this negative MVP tripwire as positive readiness.
    raise ObjectDeltaMvpFullMirrorFenceError(
        "Object-delta MVP fence cannot certify full-mirror readiness"
    )


__all__ = (
    "OBJECT_DELTA_MVP_FULL_MIRROR_FENCE_SCHEMA",
    "ObjectDeltaMvpFullMirrorAssessment",
    "ObjectDeltaMvpFullMirrorFenceError",
    "assess_object_delta_mvp_full_mirror",
    "require_object_delta_mvp_not_full_mirror",
)
