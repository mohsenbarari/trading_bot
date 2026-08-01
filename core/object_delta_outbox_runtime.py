"""Default-off, same-transaction bridge from ChangeLog to Object-delta outbox.

``core.sync_outbox_guard`` invokes this module only after it has proved that a
flush produced the mandatory ChangeLog rows. The bridge then runs on the same
synchronous SQLAlchemy Connection and transaction that performed the
authoritative write. It deliberately has no background fallback: a source
write is either paired with its Object-delta outbox entry before commit or the
outer transaction fails.

This bridge has no Object Storage, age, network, SSH, or worker behaviour. A
later publisher consumes the durable rows it creates.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from core.object_delta_outbox_allocator import (
    ObjectDeltaOutboxAllocation,
    ObjectDeltaOutboxAllocationError,
    ObjectDeltaOutboxRequest,
    allocate_object_delta_outbox_entry_sync,
)
from core.object_delta_runtime_binding import (
    ObjectDeltaRuntimeBindingError,
    binding_from_settings,
)
from models.change_log import ChangeLog


class ObjectDeltaOutboxRuntimeError(RuntimeError):
    """Raised when an enabled source write cannot be atomically projected."""


@dataclass(frozen=True)
class ObjectDeltaOutboxRuntimeResult:
    """Non-secret allocation summary for one completed flush guard."""

    allocations: tuple[ObjectDeltaOutboxAllocation, ...]


def _require_change_log_ids(
    change_log_ids: Iterable[object],
    *,
    expected_count: int,
) -> tuple[int, ...]:
    if type(expected_count) is not int or expected_count < 0:
        raise ObjectDeltaOutboxRuntimeError("object-delta expected ChangeLog count is invalid")
    try:
        values = tuple(change_log_ids)
    except TypeError as exc:
        raise ObjectDeltaOutboxRuntimeError("object-delta ChangeLog ids are invalid") from exc
    if len(values) != expected_count:
        raise ObjectDeltaOutboxRuntimeError(
            "object-delta ChangeLog hand-off count does not match the verified sync outbox"
        )
    normalized: list[int] = []
    for value in values:
        if type(value) is not int or value < 1:
            raise ObjectDeltaOutboxRuntimeError("object-delta ChangeLog id is invalid")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ObjectDeltaOutboxRuntimeError("object-delta ChangeLog hand-off repeats an id")
    return tuple(normalized)


def _mapping_one_or_none(connection: object, statement: object, *, label: str) -> Mapping[str, Any] | None:
    try:
        result = connection.execute(statement)
        row = result.mappings().one_or_none()
    except Exception as exc:
        raise ObjectDeltaOutboxRuntimeError(f"object-delta {label} lookup failed") from exc
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise ObjectDeltaOutboxRuntimeError(f"object-delta {label} lookup returned an invalid row")
    return dict(row)


def _locked_change_log_entry(connection: object, change_log_id: int) -> object:
    table = ChangeLog.__table__
    row = _mapping_one_or_none(
        connection,
        select(table).where(table.c.id == change_log_id).with_for_update(),
        label="ChangeLog",
    )
    if row is None:
        raise ObjectDeltaOutboxRuntimeError("object-delta ChangeLog evidence disappeared before commit")
    required = {"id", "operation", "table_name", "record_id", "data", "timestamp", "hash"}
    if not required.issubset(row):
        raise ObjectDeltaOutboxRuntimeError("object-delta ChangeLog evidence is incomplete")
    return SimpleNamespace(**row)


def _same_writer_term(first: object, second: object) -> bool:
    return (
        getattr(first, "holder_site", None),
        getattr(first, "writer_epoch", None),
        getattr(first, "lease_id", None),
        getattr(first, "witness_transition_id", None),
    ) == (
        getattr(second, "holder_site", None),
        getattr(second, "writer_epoch", None),
        getattr(second, "lease_id", None),
        getattr(second, "witness_transition_id", None),
    )


def allocate_verified_object_delta_outbox_entries(
    connection: object,
    *,
    change_log_ids: Iterable[object],
    expected_count: int,
) -> ObjectDeltaOutboxRuntimeResult:
    """Allocate verified ChangeLog IDs in the current outer transaction.

    The disabled branch returns before loading a binding, checking a Writer
    Witness term, querying a ChangeLog row, or mutating a stream. Once
    enabled, every ID must have been recorded by the same
    ``sync_outbox_guard`` flush token; an incomplete hand-off is fatal rather
    than silently leaving an authoritative change outside the append-only
    stream.
    """

    from core.config import settings

    try:
        binding = binding_from_settings(settings)
    except ObjectDeltaRuntimeBindingError as exc:
        raise ObjectDeltaOutboxRuntimeError("object-delta source binding is invalid") from exc
    if binding is None:
        return ObjectDeltaOutboxRuntimeResult(allocations=())

    ids = _require_change_log_ids(change_log_ids, expected_count=expected_count)
    if not ids:
        return ObjectDeltaOutboxRuntimeResult(allocations=())

    # Import only after the explicit runtime binding says this process is the
    # release-bound source. The Writer Witness policy is enforced again by the
    # application engine for each Core write below.
    from core.db import require_application_writer_term
    from core.sync_worker import change_log_entry_to_sync_item

    initial_term = require_application_writer_term()
    if initial_term is None or initial_term.holder_site != binding.source_site:
        raise ObjectDeltaOutboxRuntimeError(
            "object-delta source runtime has no active matching Writer Witness term"
        )

    allocations: list[ObjectDeltaOutboxAllocation] = []
    for change_log_id in ids:
        entry = _locked_change_log_entry(connection, change_log_id)
        item = change_log_entry_to_sync_item(entry)
        request = ObjectDeltaOutboxRequest(
            source_site=binding.source_site,
            destination_site=binding.destination_site,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
            stream_generation_id=binding.stream_generation_id,
            writer_epoch=initial_term.writer_epoch,
            writer_lease_id=initial_term.lease_id,
            change_log_id=change_log_id,
            canonical_sync_item=item,
        )
        try:
            allocations.append(allocate_object_delta_outbox_entry_sync(connection, request))
        except ObjectDeltaOutboxAllocationError as exc:
            raise ObjectDeltaOutboxRuntimeError("object-delta source allocation failed") from exc

    final_term = require_application_writer_term()
    if final_term is None or not _same_writer_term(initial_term, final_term):
        raise ObjectDeltaOutboxRuntimeError("object-delta Writer Witness term changed before commit")
    return ObjectDeltaOutboxRuntimeResult(allocations=tuple(allocations))
