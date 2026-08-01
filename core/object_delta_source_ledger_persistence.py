"""Low-level caller-owned persistence adapter for immutable source ledgers.

This module persists only a batch that has already been prepared from a
verified immutable Object receipt.  It deliberately does not assemble a
payload, encrypt, publish, read Object Storage, use age, fetch credentials,
or acknowledge a receiver.  The caller owns the surrounding SQLAlchemy
transaction and remains responsible for commit or rollback.

The caller must validate the live Writer Witness term before preparing and
persisting a batch.  This adapter records the term already bound into the
prepared immutable descriptor; it does not read or revalidate a live lease.
It also does not verify signed source-cutover/baseline evidence.  The former
public persistence entrypoint is now hard-disabled because it could not prove
the required locked source snapshot or fresh live Writer Witness authority.
Its mechanics remain only under an explicitly named private test-contract
function; they cannot confer delivery authority.

The lock sequence is fixed: advisory stream lock, source stream, terminal
ledger row, same logical range, same batch hash, then same Object version.
The pure ledger planner receives only these lock-scoped rows and determines
whether a new immutable row may be inserted or an exact retry is a replay.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from core.append_only_sync_delta_batch import AppendOnlySyncDeltaBatch
from core.legacy_source_publication_fence import (
    LegacyObjectDeltaSourcePublicationDisabledError,
    reject_legacy_object_delta_source_publication_runtime,
)
from core.object_delta_outbox_allocator import (
    ObjectDeltaStreamIdentity,
    stream_advisory_lock_key,
)
from core.object_delta_source_batch_ledger import (
    SOURCE_BATCH_APPEND_ACTION_APPEND,
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
    ObjectDeltaSourceLedgerError,
    plan_source_batch_ledger_append,
)
from core.object_delta_source_batch_publication import PreparedObjectDeltaSourceBatch
from core.object_delta_transport_binding import ObjectDeltaTransportBinding
from models.object_delta import ObjectDeltaStream
from models.object_delta_source_batch import ObjectDeltaSourceBatchLedger


class ObjectDeltaSourceLedgerPersistenceError(RuntimeError):
    """A caller-owned source-ledger transaction cannot safely proceed."""


@dataclass(frozen=True)
class ObjectDeltaSourceLedgerPersistenceResult:
    """The immutable append/replay decision and the corresponding ORM row."""

    action: str
    ledger_entry: SourceBatchLedgerEntry
    ledger_row: ObjectDeltaSourceBatchLedger


__all__ = (
    "LegacyObjectDeltaSourcePublicationDisabledError",
    "ObjectDeltaSourceLedgerPersistenceError",
    "ObjectDeltaSourceLedgerPersistenceResult",
)


def _session_has_active_transaction(session: object) -> bool:
    probe = getattr(session, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def _source_stream_identity(stream: ObjectDeltaStream | object) -> SourceStreamIdentity:
    if not isinstance(stream, ObjectDeltaStream) or type(stream.id) is not int or stream.id < 1:
        raise ObjectDeltaSourceLedgerPersistenceError("locked Object-delta source stream is invalid")
    try:
        return SourceStreamIdentity(
            source_site=stream.source_site,
            destination_site=stream.destination_site,
            campaign_id=stream.campaign_id,
            release_sha=stream.release_sha,
            stream_generation_id=stream.stream_generation_id,
        )
    except ObjectDeltaSourceLedgerError as exc:
        raise ObjectDeltaSourceLedgerPersistenceError(
            "locked Object-delta source stream is invalid"
        ) from exc


def _stream_identity_for_advisory_lock(stream: SourceStreamIdentity) -> ObjectDeltaStreamIdentity:
    try:
        return ObjectDeltaStreamIdentity(
            source_site=stream.source_site,
            destination_site=stream.destination_site,
            campaign_id=stream.campaign_id,
            release_sha=stream.release_sha,
            stream_generation_id=stream.stream_generation_id,
        )
    except Exception as exc:
        raise ObjectDeltaSourceLedgerPersistenceError(
            "Object-delta source stream identity is invalid"
        ) from exc


def _validated_prepared_candidate(
    prepared: PreparedObjectDeltaSourceBatch,
) -> SourceBatchLedgerEntry:
    """Reject a manually mixed prepared result before issuing database SQL."""

    if not isinstance(prepared, PreparedObjectDeltaSourceBatch):
        raise ObjectDeltaSourceLedgerPersistenceError("prepared Object-delta source batch is invalid")
    if (
        not isinstance(prepared.batch, AppendOnlySyncDeltaBatch)
        or not isinstance(prepared.transport_binding, ObjectDeltaTransportBinding)
        or not isinstance(prepared.ledger_entry, SourceBatchLedgerEntry)
    ):
        raise ObjectDeltaSourceLedgerPersistenceError("prepared Object-delta source batch is invalid")
    candidate = prepared.ledger_entry
    batch = prepared.batch
    transport = prepared.transport_binding
    try:
        expected_stream = SourceStreamIdentity(
            source_site=batch.source_site,
            destination_site=batch.destination_site,
            campaign_id=batch.campaign_id,
            release_sha=batch.release_sha,
            stream_generation_id=batch.stream.generation_id,
        )
    except ObjectDeltaSourceLedgerError as exc:
        raise ObjectDeltaSourceLedgerPersistenceError("prepared Object-delta batch stream is invalid") from exc
    expected_candidate = (
        expected_stream,
        batch.stream.first_sequence,
        batch.stream.last_sequence,
        batch.writer_term.epoch,
        batch.writer_term.lease_id,
        batch.prior_chain_sha256,
        batch.batch_sha256,
        batch.payload_sha256,
        batch.payload_bytes,
        batch.immutable_receipt.object_key,
        batch.immutable_receipt.version_id,
        batch.immutable_receipt.ciphertext_sha256,
        batch.immutable_receipt.ciphertext_bytes,
    )
    actual_candidate = (
        candidate.stream,
        candidate.first_sequence,
        candidate.last_sequence,
        candidate.writer_epoch,
        candidate.writer_lease_id,
        candidate.prior_chain_sha256,
        candidate.batch_sha256,
        candidate.payload_sha256,
        candidate.payload_bytes,
        candidate.object_key,
        candidate.object_version_id,
        candidate.ciphertext_sha256,
        candidate.ciphertext_bytes,
    )
    if actual_candidate != expected_candidate:
        raise ObjectDeltaSourceLedgerPersistenceError(
            "prepared Object-delta ledger entry does not match its batch"
        )
    expected_transport = (
        batch.source_site,
        batch.destination_site,
        batch.immutable_receipt.object_key,
        batch.stream.generation_id,
        batch.stream.first_sequence,
        batch.stream.last_sequence,
        batch.payload_sha256,
        batch.immutable_receipt.ciphertext_sha256,
        batch.immutable_receipt.ciphertext_bytes,
        batch.immutable_receipt.version_id,
    )
    actual_transport = (
        transport.source_site,
        transport.destination_site,
        transport.object_key,
        transport.stream_generation_id,
        transport.first_sequence,
        transport.last_sequence,
        transport.payload_sha256,
        transport.ciphertext_sha256,
        transport.ciphertext_bytes,
        transport.object_version_id,
    )
    if actual_transport != expected_transport:
        raise ObjectDeltaSourceLedgerPersistenceError(
            "prepared Object-delta transport binding does not match its batch"
        )
    return candidate


def _require_matching_stream(
    stream: ObjectDeltaStream | object,
    *,
    expected: SourceStreamIdentity,
) -> ObjectDeltaStream:
    actual = _source_stream_identity(stream)
    if actual != expected:
        raise ObjectDeltaSourceLedgerPersistenceError(
            "locked Object-delta source stream does not match the prepared batch"
        )
    return stream


def _ledger_entry_from_row(
    row: ObjectDeltaSourceBatchLedger | object,
    *,
    stream: ObjectDeltaStream,
    identity: SourceStreamIdentity,
) -> SourceBatchLedgerEntry:
    if not isinstance(row, ObjectDeltaSourceBatchLedger):
        raise ObjectDeltaSourceLedgerPersistenceError("locked source ledger row is invalid")
    if row.stream_id != stream.id:
        raise ObjectDeltaSourceLedgerPersistenceError(
            "locked source ledger row belongs to a different source stream"
        )
    try:
        return SourceBatchLedgerEntry(
            stream=identity,
            first_sequence=row.first_sequence,
            last_sequence=row.last_sequence,
            writer_epoch=row.writer_epoch,
            writer_lease_id=row.writer_lease_id,
            prior_chain_sha256=row.prior_chain_sha256,
            batch_sha256=row.batch_sha256,
            payload_sha256=row.payload_sha256,
            payload_bytes=row.payload_bytes,
            object_key=row.object_key,
            object_version_id=row.object_version_id,
            ciphertext_sha256=row.ciphertext_sha256,
            ciphertext_bytes=row.ciphertext_bytes,
        )
    except (ObjectDeltaSourceLedgerError, TypeError) as exc:
        raise ObjectDeltaSourceLedgerPersistenceError("locked source ledger row is invalid") from exc


async def _scalar_one_or_none(session: object, statement: object, *, label: str):
    try:
        result = await session.execute(statement)
        return result.scalar_one_or_none()
    except Exception as exc:
        raise ObjectDeltaSourceLedgerPersistenceError(
            f"Object-delta source ledger {label} query failed"
        ) from exc


async def _lock_stream_advisory(session: object, identity: SourceStreamIdentity) -> None:
    await _scalar_one_or_none(
        session,
        select(func.pg_advisory_xact_lock(stream_advisory_lock_key(_stream_identity_for_advisory_lock(identity)))),
        label="stream advisory lock",
    )


async def _load_source_stream_for_update(
    session: object,
    *,
    identity: SourceStreamIdentity,
) -> ObjectDeltaStream | None:
    row = await _scalar_one_or_none(
        session,
        select(ObjectDeltaStream)
        .where(
            ObjectDeltaStream.source_site == identity.source_site,
            ObjectDeltaStream.destination_site == identity.destination_site,
            ObjectDeltaStream.campaign_id == identity.campaign_id,
            ObjectDeltaStream.release_sha == identity.release_sha,
            ObjectDeltaStream.stream_generation_id == identity.stream_generation_id,
        )
        .with_for_update(),
        label="stream lock",
    )
    return row


async def _load_terminal_entry_for_update(
    session: object,
    *,
    stream_id: int,
) -> ObjectDeltaSourceBatchLedger | None:
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaSourceBatchLedger)
        .where(ObjectDeltaSourceBatchLedger.stream_id == stream_id)
        .order_by(
            ObjectDeltaSourceBatchLedger.last_sequence.desc(),
            ObjectDeltaSourceBatchLedger.id.desc(),
        )
        .limit(1)
        .with_for_update(),
        label="terminal ledger lock",
    )


async def _load_same_range_for_update(
    session: object,
    *,
    stream_id: int,
    first_sequence: int,
) -> ObjectDeltaSourceBatchLedger | None:
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaSourceBatchLedger)
        .where(
            ObjectDeltaSourceBatchLedger.stream_id == stream_id,
            ObjectDeltaSourceBatchLedger.first_sequence == first_sequence,
        )
        .with_for_update(),
        label="same-range ledger lock",
    )


async def _load_same_batch_for_update(
    session: object,
    *,
    stream_id: int,
    batch_sha256: str,
) -> ObjectDeltaSourceBatchLedger | None:
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaSourceBatchLedger)
        .where(
            ObjectDeltaSourceBatchLedger.stream_id == stream_id,
            ObjectDeltaSourceBatchLedger.batch_sha256 == batch_sha256,
        )
        .with_for_update(),
        label="same-batch ledger lock",
    )


async def _load_same_object_for_update(
    session: object,
    *,
    object_key: str,
    object_version_id: str,
) -> ObjectDeltaSourceBatchLedger | None:
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaSourceBatchLedger)
        .where(
            ObjectDeltaSourceBatchLedger.object_key == object_key,
            ObjectDeltaSourceBatchLedger.object_version_id == object_version_id,
        )
        .with_for_update(),
        label="same-Object ledger lock",
    )


def _model_from_entry(
    *,
    stream_id: int,
    entry: SourceBatchLedgerEntry,
) -> ObjectDeltaSourceBatchLedger:
    return ObjectDeltaSourceBatchLedger(
        stream_id=stream_id,
        first_sequence=entry.first_sequence,
        last_sequence=entry.last_sequence,
        writer_epoch=entry.writer_epoch,
        writer_lease_id=entry.writer_lease_id,
        prior_chain_sha256=entry.prior_chain_sha256,
        batch_sha256=entry.batch_sha256,
        payload_sha256=entry.payload_sha256,
        payload_bytes=entry.payload_bytes,
        object_key=entry.object_key,
        object_version_id=entry.object_version_id,
        ciphertext_sha256=entry.ciphertext_sha256,
        ciphertext_bytes=entry.ciphertext_bytes,
    )


async def _legacy_test_only_persist_prepared_object_delta_source_batch_ledger(
    session: object,
    prepared: PreparedObjectDeltaSourceBatch,
) -> ObjectDeltaSourceLedgerPersistenceResult:
    """Test-only mechanics for append/replay of a legacy prepared batch.

    This private contract helper never invokes ``begin``, ``commit``, or
    ``rollback``.  It exists solely to retain isolated state-machine tests
    while the legacy runtime route is fenced.  Do not import or wire it from
    application, worker, script, or deployment code.
    """

    if not _session_has_active_transaction(session):
        raise ObjectDeltaSourceLedgerPersistenceError(
            "source ledger persistence requires an active caller-owned transaction"
        )
    candidate = _validated_prepared_candidate(prepared)
    await _lock_stream_advisory(session, candidate.stream)
    stream = await _load_source_stream_for_update(session, identity=candidate.stream)
    if stream is None:
        raise ObjectDeltaSourceLedgerPersistenceError("prepared source stream does not exist")
    stream = _require_matching_stream(stream, expected=candidate.stream)
    terminal_row = await _load_terminal_entry_for_update(session, stream_id=stream.id)
    range_row = await _load_same_range_for_update(
        session,
        stream_id=stream.id,
        first_sequence=candidate.first_sequence,
    )
    batch_row = await _load_same_batch_for_update(
        session,
        stream_id=stream.id,
        batch_sha256=candidate.batch_sha256,
    )
    object_row = await _load_same_object_for_update(
        session,
        object_key=candidate.object_key,
        object_version_id=candidate.object_version_id,
    )
    previous_entry = (
        _ledger_entry_from_row(terminal_row, stream=stream, identity=candidate.stream)
        if terminal_row is not None
        else None
    )
    existing_by_first_sequence = (
        _ledger_entry_from_row(range_row, stream=stream, identity=candidate.stream)
        if range_row is not None
        else None
    )
    existing_by_batch_sha256 = (
        _ledger_entry_from_row(batch_row, stream=stream, identity=candidate.stream)
        if batch_row is not None
        else None
    )
    existing_by_object_version = (
        _ledger_entry_from_row(object_row, stream=stream, identity=candidate.stream)
        if object_row is not None
        else None
    )
    try:
        plan = plan_source_batch_ledger_append(
            candidate=candidate,
            previous_entry=previous_entry,
            existing_by_first_sequence=existing_by_first_sequence,
            existing_by_batch_sha256=existing_by_batch_sha256,
            existing_by_object_version=existing_by_object_version,
        )
    except ObjectDeltaSourceLedgerError as exc:
        raise ObjectDeltaSourceLedgerPersistenceError(
            "prepared source batch conflicts with the immutable ledger"
        ) from exc
    if plan.action != SOURCE_BATCH_APPEND_ACTION_APPEND:
        replay_row = range_row or batch_row or object_row
        if replay_row is None:
            raise ObjectDeltaSourceLedgerPersistenceError("source ledger replay row is missing")
        return ObjectDeltaSourceLedgerPersistenceResult(
            action=plan.action,
            ledger_entry=candidate,
            ledger_row=replay_row,
        )
    if plan.entry_to_insert != candidate:
        raise ObjectDeltaSourceLedgerPersistenceError("source ledger append plan is invalid")
    row = _model_from_entry(stream_id=stream.id, entry=plan.entry_to_insert)
    try:
        session.add(row)
        await session.flush()
    except Exception as exc:
        raise ObjectDeltaSourceLedgerPersistenceError("source ledger insert failed") from exc
    return ObjectDeltaSourceLedgerPersistenceResult(
        action=plan.action,
        ledger_entry=plan.entry_to_insert,
        ledger_row=row,
    )


async def persist_prepared_object_delta_source_batch_ledger(
    session: object,
    prepared: PreparedObjectDeltaSourceBatch,
) -> ObjectDeltaSourceLedgerPersistenceResult:
    """Reject the superseded raw-prepared source-ledger runtime route.

    This compatibility name deliberately has no delegation path.  The future
    root-only coordinator must use a new authority that carries the locked
    snapshot and freshly validated Writer Witness term instead.
    """

    del session, prepared
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="persist_prepared_object_delta_source_batch_ledger"
    )
