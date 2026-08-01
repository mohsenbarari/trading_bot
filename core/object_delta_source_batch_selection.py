"""Read-only selection of one contiguous source Object-delta outbox prefix.

This adapter is deliberately narrower than a publisher.  It only reads a
caller-owned transaction snapshot, maps durable rows into the existing pure
batch assembler, and returns canonical plaintext ready for a later, separate
step.  It does not create a stream, take row or advisory locks, mutate a
session, or interact with any external system.

The result is optimistic evidence, not publication authority.  A later
caller must independently validate its live Writer Witness term, re-check the
published source cutover, and re-check the immutable source ledger under its
transaction locks before it records an Object receipt.  Keeping this selector
read-only also prevents a harmless no-work probe from changing source state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from uuid import UUID

from sqlalchemy import select

from core.append_only_sync_delta_batch import (
    LEASE_ID_RE,
    MAX_DELTA_PAYLOAD_BYTES,
    MAX_STREAM_SEQUENCE_IDS,
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
)
from core.append_only_sync_delta_payload import REGISTRY_FINGERPRINT_RE
from core.object_delta_batch_assembler import (
    ObjectDeltaBatchAssemblyError,
    PreparedObjectDeltaPayload,
    SourceOutboxDeltaItem,
    assemble_object_delta_payload,
)
from core.object_delta_outbox_allocator import canonical_sync_item_sha256
from core.object_delta_runtime_binding import (
    ObjectDeltaRuntimeBindingError,
    ObjectDeltaSourceRuntimeBinding,
)
from core.object_delta_source_batch_ledger import (
    ObjectDeltaSourceLedgerError,
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
)
from models.object_delta import (
    ObjectDeltaOutboxEntry,
    ObjectDeltaSourceCutover,
    ObjectDeltaStream,
)
from models.object_delta_source_batch import ObjectDeltaSourceBatchLedger


class ObjectDeltaSourceBatchSelectionError(RuntimeError):
    """The read-only source outbox snapshot cannot safely form a batch."""


# Keep this selector independent of the allocator's private request type.  A
# publisher must re-check the same record under locks later, but even this
# optimistic read path must never prepare a batch from a stream that lacks the
# source's published baseline evidence.
SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE = "baseline_published"
_SOURCE_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16,64}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-z]{8,64}$")


@dataclass(frozen=True)
class _PublishedSourceCutoverSnapshot:
    """The small, normalized cutover projection needed by this read path.

    It is intentionally private: it is not a portable proof and cannot grant
    publication authority.  The eventual root-only publisher must verify a
    pinned source-signed cutover attestation and re-read this durable record
    under its transaction locks before it encrypts or uploads anything.
    """

    writer_epoch: int
    writer_lease_id: str


@dataclass(frozen=True)
class ObjectDeltaSourceBatchSelectionResult:
    """One source-stream snapshot and its optional next canonical payload.

    ``prepared_payload is None`` is the explicit no-work result.  The optional
    terminal entry describes exactly the ledger frontier observed in the same
    caller-owned transaction snapshot; it is never advanced here.
    """

    stream: SourceStreamIdentity
    terminal_ledger_entry: SourceBatchLedgerEntry | None
    prepared_payload: PreparedObjectDeltaPayload | None

    def __post_init__(self) -> None:
        if not isinstance(self.stream, SourceStreamIdentity):
            raise ObjectDeltaSourceBatchSelectionError("selected Object-delta stream is invalid")
        if (
            self.terminal_ledger_entry is not None
            and (
                not isinstance(self.terminal_ledger_entry, SourceBatchLedgerEntry)
                or self.terminal_ledger_entry.stream != self.stream
            )
        ):
            raise ObjectDeltaSourceBatchSelectionError(
                "selected Object-delta terminal ledger entry is invalid"
            )
        if (
            self.prepared_payload is not None
            and (
                not isinstance(self.prepared_payload, PreparedObjectDeltaPayload)
                or self.prepared_payload.stream != self.stream
            )
        ):
            raise ObjectDeltaSourceBatchSelectionError(
                "selected Object-delta prepared payload is invalid"
            )

    @property
    def no_work(self) -> bool:
        """Whether this snapshot had no safe contiguous prefix to prepare."""

        return self.prepared_payload is None


def _session_has_active_transaction(session: object) -> bool:
    probe = getattr(session, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def _validated_binding_stream(
    binding: ObjectDeltaSourceRuntimeBinding,
) -> tuple[ObjectDeltaSourceRuntimeBinding, SourceStreamIdentity]:
    """Reconstruct binding fields before any database query is issued."""

    if not isinstance(binding, ObjectDeltaSourceRuntimeBinding):
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source binding is invalid")
    try:
        normalized_binding = ObjectDeltaSourceRuntimeBinding(
            source_site=binding.source_site,
            destination_site=binding.destination_site,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            stream_generation_id=binding.stream_generation_id,
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
        )
        stream = SourceStreamIdentity(
            source_site=normalized_binding.source_site,
            destination_site=normalized_binding.destination_site,
            campaign_id=normalized_binding.campaign_id,
            release_sha=normalized_binding.release_sha,
            stream_generation_id=normalized_binding.stream_generation_id,
        )
    except (AttributeError, TypeError, ObjectDeltaRuntimeBindingError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source binding is invalid") from exc
    return normalized_binding, stream


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaSourceBatchSelectionError(f"Object-delta {label} is invalid")
    return value


def _require_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaSourceBatchSelectionError(f"Object-delta {label} is invalid")
    return value


def _require_canonical_uuid(value: object, *, label: str) -> str:
    try:
        normalized = str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceBatchSelectionError(f"Object-delta {label} is invalid") from exc
    if isinstance(value, str) and value != normalized:
        raise ObjectDeltaSourceBatchSelectionError(f"Object-delta {label} is invalid")
    return normalized


def _require_object_key(value: object, *, label: str) -> str:
    key = _require_text(value, label=label, pattern=OBJECT_KEY_RE)
    if ".." in key.split("/"):
        raise ObjectDeltaSourceBatchSelectionError(f"Object-delta {label} is invalid")
    return key


def _published_source_cutover_from_row(
    row: ObjectDeltaSourceCutover | object,
    *,
    stream: ObjectDeltaStream,
    identity: SourceStreamIdentity,
    binding: ObjectDeltaSourceRuntimeBinding,
) -> _PublishedSourceCutoverSnapshot:
    """Validate the complete durable baseline gate from one read snapshot.

    The selector deliberately takes no locks, because its result remains
    optimistic evidence rather than a publish reservation.  Still, accepting
    a stream/outbox that merely *looks* complete would let direct SQL inserts
    bypass the allocator's cutover boundary.  Validate the same immutable
    identity, registry, term, and baseline receipt shape before any rows can
    be assembled into plaintext.
    """

    if not isinstance(row, ObjectDeltaSourceCutover):
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source stream has no durable source cutover"
        )
    try:
        cutover_identity = SourceStreamIdentity(
            source_site=row.source_site,
            destination_site=row.destination_site,
            campaign_id=row.campaign_id,
            release_sha=row.release_sha,
            stream_generation_id=row.stream_generation_id,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source cutover is invalid") from exc
    if _require_positive_int(row.stream_id, label="source cutover stream id") != stream.id:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source cutover does not match the source stream"
        )
    if cutover_identity != identity:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source cutover does not match the runtime binding"
        )
    if row.state != SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source cutover is not baseline published"
        )
    registry_fingerprint = _require_text(
        row.registry_fingerprint,
        label="source cutover registry fingerprint",
        pattern=REGISTRY_FINGERPRINT_RE,
    )
    if registry_fingerprint != binding.expected_registry_fingerprint:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source cutover registry fingerprint does not match the runtime binding"
        )
    writer_epoch = _require_positive_int(row.writer_epoch, label="source cutover writer epoch")
    writer_lease_id = _require_text(
        row.writer_lease_id,
        label="source cutover writer lease",
        pattern=LEASE_ID_RE,
    )

    # The database schema constrains these fields, but the selector receives
    # arbitrary ORM-shaped values in tests and must not turn a malformed or
    # half-published record into signable plaintext.
    _require_canonical_uuid(row.write_gate_id, label="source cutover write gate")
    _require_text(
        row.source_generation,
        label="source cutover source generation",
        pattern=_SOURCE_GENERATION_RE,
    )
    _require_text(row.snapshot_id, label="source cutover snapshot id", pattern=_SNAPSHOT_ID_RE)
    _require_text(
        row.alembic_revision,
        label="source cutover Alembic revision",
        pattern=_ALEMBIC_REVISION_RE,
    )
    for value, label in (
        (row.snapshot_manifest_object_key, "source cutover snapshot manifest key"),
        (row.baseline_manifest_object_key, "source cutover baseline manifest key"),
    ):
        _require_object_key(value, label=label)
    for value, label in (
        (row.snapshot_manifest_object_version_id, "source cutover snapshot manifest version"),
        (row.baseline_manifest_object_version_id, "source cutover baseline manifest version"),
    ):
        version_id = _require_text(value, label=label, pattern=VERSION_ID_RE)
        if version_id.lower() == "null":
            raise ObjectDeltaSourceBatchSelectionError(f"Object-delta {label} is invalid")
    for value, label in (
        (row.snapshot_manifest_ciphertext_sha256, "source cutover snapshot manifest hash"),
        (row.baseline_manifest_ciphertext_sha256, "source cutover baseline manifest hash"),
        (row.database_sha256, "source cutover database hash"),
        (row.uploads_sha256, "source cutover uploads hash"),
    ):
        _require_text(value, label=label, pattern=SHA256_RE)
    for value, label in (
        (row.snapshot_manifest_ciphertext_bytes, "source cutover snapshot manifest bytes"),
        (row.baseline_manifest_ciphertext_bytes, "source cutover baseline manifest bytes"),
    ):
        _require_positive_int(value, label=label)
    return _PublishedSourceCutoverSnapshot(
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
    )


def _require_cutover_writer_term(
    cutover: _PublishedSourceCutoverSnapshot,
    *,
    writer_epoch: int,
    writer_lease_id: str,
    label: str,
) -> None:
    if (writer_epoch, writer_lease_id) != (
        cutover.writer_epoch,
        cutover.writer_lease_id,
    ):
        raise ObjectDeltaSourceBatchSelectionError(
            f"Object-delta {label} Writer Witness term does not match the published source cutover"
        )


def _validated_max_items(value: object) -> int:
    if type(value) is not int or value < 1 or value > MAX_STREAM_SEQUENCE_IDS:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source batch max item count is invalid")
    return value


def _validated_maximum_payload_bytes(value: object) -> int:
    if type(value) is not int or value < 1 or value > MAX_DELTA_PAYLOAD_BYTES:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source batch maximum payload bytes is invalid"
        )
    return value


def _stream_from_row(
    row: ObjectDeltaStream | object,
    *,
    expected: SourceStreamIdentity,
) -> ObjectDeltaStream:
    if not isinstance(row, ObjectDeltaStream):
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source stream row is invalid")
    try:
        actual = SourceStreamIdentity(
            source_site=row.source_site,
            destination_site=row.destination_site,
            campaign_id=row.campaign_id,
            release_sha=row.release_sha,
            stream_generation_id=row.stream_generation_id,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source stream row is invalid") from exc
    if actual != expected:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source stream does not match the runtime binding"
        )
    _require_positive_int(row.id, label="source stream id")
    _require_positive_int(row.next_sequence, label="source stream next sequence")
    return row


def _ledger_entry_from_row(
    row: ObjectDeltaSourceBatchLedger | object,
    *,
    stream: ObjectDeltaStream,
    identity: SourceStreamIdentity,
) -> SourceBatchLedgerEntry:
    if not isinstance(row, ObjectDeltaSourceBatchLedger):
        raise ObjectDeltaSourceBatchSelectionError("Object-delta terminal ledger row is invalid")
    if row.stream_id != stream.id:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta terminal ledger row belongs to a different source stream"
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
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta terminal ledger row is invalid") from exc


def _outbox_item_from_row(
    row: ObjectDeltaOutboxEntry | object,
    *,
    stream: ObjectDeltaStream,
) -> SourceOutboxDeltaItem:
    if not isinstance(row, ObjectDeltaOutboxEntry):
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source outbox row is invalid")
    if row.stream_id != stream.id:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source outbox row belongs to a different source stream"
        )
    try:
        logical_sequence = _require_positive_int(
            row.logical_sequence,
            label="source outbox logical sequence",
        )
        change_log_id = _require_positive_int(
            row.change_log_id,
            label="source outbox ChangeLog id",
        )
        writer_epoch = _require_positive_int(
            row.writer_epoch,
            label="source outbox writer epoch",
        )
        if not isinstance(row.writer_lease_id, str) or LEASE_ID_RE.fullmatch(row.writer_lease_id) is None:
            raise ObjectDeltaSourceBatchSelectionError("Object-delta source outbox writer lease is invalid")
        if not isinstance(row.canonical_sync_item, Mapping):
            raise ObjectDeltaSourceBatchSelectionError("Object-delta source outbox sync item is invalid")
        canonical_sync_item = dict(row.canonical_sync_item)
        if "logical_sequence" in canonical_sync_item:
            raise ObjectDeltaSourceBatchSelectionError(
                "Object-delta source outbox sync item has a logical sequence"
            )
        digest = canonical_sync_item_sha256(canonical_sync_item)
        if not isinstance(row.sync_item_sha256, str) or row.sync_item_sha256 != digest:
            raise ObjectDeltaSourceBatchSelectionError(
                "Object-delta source outbox sync item digest does not match"
            )
    except ObjectDeltaSourceBatchSelectionError:
        raise
    except Exception as exc:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source outbox row is invalid") from exc
    return SourceOutboxDeltaItem(
        logical_sequence=logical_sequence,
        change_log_id=change_log_id,
        writer_epoch=writer_epoch,
        writer_lease_id=row.writer_lease_id,
        canonical_sync_item=canonical_sync_item,
    )


async def _read_scalar_one_or_none(session: object, statement: object, *, label: str):
    try:
        result = await session.execute(statement)
        return result.scalar_one_or_none()
    except Exception as exc:
        raise ObjectDeltaSourceBatchSelectionError(
            f"Object-delta source {label} query failed"
        ) from exc


async def _read_outbox_rows(session: object, statement: object) -> tuple[object, ...]:
    try:
        result = await session.execute(statement)
        return tuple(result.scalars().all())
    except Exception as exc:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source outbox query failed") from exc


async def _read_source_stream(
    session: object,
    *,
    identity: SourceStreamIdentity,
) -> ObjectDeltaStream | None:
    statement = (
        select(ObjectDeltaStream)
        .where(
            ObjectDeltaStream.source_site == identity.source_site,
            ObjectDeltaStream.destination_site == identity.destination_site,
            ObjectDeltaStream.campaign_id == identity.campaign_id,
            ObjectDeltaStream.release_sha == identity.release_sha,
            ObjectDeltaStream.stream_generation_id == identity.stream_generation_id,
        )
        .execution_options(autoflush=False)
    )
    return await _read_scalar_one_or_none(session, statement, label="stream")


async def _read_source_cutover(
    session: object,
    *,
    stream_id: int,
) -> ObjectDeltaSourceCutover | None:
    """Read the one source cutover without reserving or locking it.

    The unique ``stream_id`` key makes one row the only possible baseline
    gate for this optimistic snapshot.  The eventual publisher must repeat
    this query with ``FOR UPDATE`` after its stream advisory lock; this helper
    intentionally has no such side effect.
    """

    statement = (
        select(ObjectDeltaSourceCutover)
        .where(ObjectDeltaSourceCutover.stream_id == stream_id)
        .execution_options(autoflush=False)
    )
    return await _read_scalar_one_or_none(session, statement, label="source cutover")


async def _read_terminal_ledger_entry(
    session: object,
    *,
    stream_id: int,
) -> ObjectDeltaSourceBatchLedger | None:
    statement = (
        select(ObjectDeltaSourceBatchLedger)
        .where(ObjectDeltaSourceBatchLedger.stream_id == stream_id)
        .order_by(
            ObjectDeltaSourceBatchLedger.last_sequence.desc(),
            ObjectDeltaSourceBatchLedger.id.desc(),
        )
        .limit(1)
        .execution_options(autoflush=False)
    )
    return await _read_scalar_one_or_none(session, statement, label="terminal ledger")


async def _read_outbox_candidates(
    session: object,
    *,
    stream_id: int,
    first_sequence: int,
    max_items: int,
) -> tuple[object, ...]:
    statement = (
        select(ObjectDeltaOutboxEntry)
        .where(
            ObjectDeltaOutboxEntry.stream_id == stream_id,
            ObjectDeltaOutboxEntry.logical_sequence >= first_sequence,
        )
        .order_by(
            ObjectDeltaOutboxEntry.logical_sequence.asc(),
            ObjectDeltaOutboxEntry.id.asc(),
        )
        .limit(max_items)
        .execution_options(autoflush=False)
    )
    return await _read_outbox_rows(session, statement)


def _select_contiguous_same_term_prefix(
    rows: tuple[object, ...],
    *,
    stream: ObjectDeltaStream,
    expected_first_sequence: int,
    max_items: int,
) -> tuple[SourceOutboxDeltaItem, ...]:
    if len(rows) > max_items:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source outbox query exceeded its configured limit"
        )
    selected: list[SourceOutboxDeltaItem] = []
    expected_sequence = expected_first_sequence
    writer_term: tuple[int, str] | None = None
    for row in rows:
        item = _outbox_item_from_row(row, stream=stream)
        if item.logical_sequence != expected_sequence:
            raise ObjectDeltaSourceBatchSelectionError(
                "Object-delta source outbox sequence is not contiguous from the ledger frontier"
            )
        if item.logical_sequence >= stream.next_sequence:
            raise ObjectDeltaSourceBatchSelectionError(
                "Object-delta source stream next sequence is inconsistent with its outbox"
            )
        candidate_term = (item.writer_epoch, item.writer_lease_id)
        if writer_term is None:
            writer_term = candidate_term
        elif candidate_term != writer_term:
            # A new Writer Witness term begins the following immutable batch.
            # Its first row was still required to be contiguous, so a term
            # boundary cannot conceal a skipped sequence before this prefix.
            break
        selected.append(item)
        expected_sequence += 1
    return tuple(selected)


async def select_object_delta_source_batch(
    session: object,
    binding: ObjectDeltaSourceRuntimeBinding,
    *,
    max_items: int,
    maximum_payload_bytes: int,
) -> ObjectDeltaSourceBatchSelectionResult:
    """Read one bounded, same-term source outbox prefix without mutation.

    A missing pre-created source stream is a no-work result.  Once a stream
    exists, it must have a complete ``baseline_published`` source-cutover row
    matching the binding and the selected Writer Witness term.  A missing
    outbox row is no-work only when ``next_sequence`` equals the ledger
    frontier.  A higher counter would mean a missing required row and is
    rejected rather than silently skipping data.  Gaps, malformed rows,
    ledger rows from another stream, and binding mismatches all fail closed.
    """

    if not _session_has_active_transaction(session):
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source batch selection requires an active caller-owned transaction"
        )
    normalized_binding, identity = _validated_binding_stream(binding)
    limit = _validated_max_items(max_items)
    payload_limit = _validated_maximum_payload_bytes(maximum_payload_bytes)

    stream_row = await _read_source_stream(session, identity=identity)
    if stream_row is None:
        return ObjectDeltaSourceBatchSelectionResult(
            stream=identity,
            terminal_ledger_entry=None,
            prepared_payload=None,
        )
    stream = _stream_from_row(stream_row, expected=identity)
    cutover_row = await _read_source_cutover(session, stream_id=stream.id)
    cutover = _published_source_cutover_from_row(
        cutover_row,
        stream=stream,
        identity=identity,
        binding=normalized_binding,
    )
    terminal_row = await _read_terminal_ledger_entry(session, stream_id=stream.id)
    terminal_entry = (
        _ledger_entry_from_row(terminal_row, stream=stream, identity=identity)
        if terminal_row is not None
        else None
    )
    if terminal_entry is not None:
        _require_cutover_writer_term(
            cutover,
            writer_epoch=terminal_entry.writer_epoch,
            writer_lease_id=terminal_entry.writer_lease_id,
            label="terminal source ledger",
        )
    expected_first_sequence = (
        terminal_entry.last_sequence + 1 if terminal_entry is not None else 1
    )
    if stream.next_sequence < expected_first_sequence:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta source stream next sequence precedes its ledger frontier"
        )
    rows = await _read_outbox_candidates(
        session,
        stream_id=stream.id,
        first_sequence=expected_first_sequence,
        max_items=limit,
    )
    if not rows:
        if stream.next_sequence != expected_first_sequence:
            raise ObjectDeltaSourceBatchSelectionError(
                "Object-delta source outbox is missing the next ledger sequence"
            )
        return ObjectDeltaSourceBatchSelectionResult(
            stream=identity,
            terminal_ledger_entry=terminal_entry,
            prepared_payload=None,
        )
    selected = _select_contiguous_same_term_prefix(
        rows,
        stream=stream,
        expected_first_sequence=expected_first_sequence,
        max_items=limit,
    )
    if not selected:
        raise ObjectDeltaSourceBatchSelectionError("Object-delta source outbox selection is empty")
    try:
        prepared_payload = assemble_object_delta_payload(
            stream=identity,
            outbox_items=selected,
            expected_registry_fingerprint=normalized_binding.expected_registry_fingerprint,
            maximum_payload_bytes=payload_limit,
        )
    except ObjectDeltaBatchAssemblyError as exc:
        raise ObjectDeltaSourceBatchSelectionError(
            "Object-delta selected source outbox rows are invalid"
        ) from exc
    _require_cutover_writer_term(
        cutover,
        writer_epoch=prepared_payload.writer_term.epoch,
        writer_lease_id=prepared_payload.writer_term.lease_id,
        label="selected source outbox",
    )
    return ObjectDeltaSourceBatchSelectionResult(
        stream=identity,
        terminal_ledger_entry=terminal_entry,
        prepared_payload=prepared_payload,
    )
