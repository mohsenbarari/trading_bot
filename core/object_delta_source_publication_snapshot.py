"""Locked, local-only source snapshot for a future Object-delta publisher.

This module takes a caller-owned active SQLAlchemy transaction and obtains a
single locked view of the next source publication prefix.  Its fixed lock
order is:

``stream advisory lock -> stream -> published cutover -> terminal ledger -> outbox prefix``.

It validates the release-bound source binding, durable baseline evidence,
registry fingerprint, Writer Witness term, ledger frontier, and a contiguous
same-term outbox prefix before asking the pure assembler for canonical
plaintext.  The returned object is *not* a capability or publication
authorization: it is usable only while the caller retains the same database
transaction and a future root-only pre-upload gate has independently
validated live Writer Witness and signed cutover evidence.

There is intentionally no persistence, gate construction, filesystem/spool
access, encryption, Object Storage, credentials, network, background worker,
or runtime activation in this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from uuid import UUID

from sqlalchemy import func, select

from core.append_only_sync_delta_batch import (
    LEASE_ID_RE,
    MAX_DELTA_PAYLOAD_BYTES,
    MAX_STREAM_SEQUENCE_IDS,
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
    WriterTermBinding,
    sha256_bytes,
)
from core.append_only_sync_delta_payload import (
    REGISTRY_FINGERPRINT_RE,
    ObjectDeltaPayloadError,
    parse_object_delta_payload,
)
from core.object_delta_batch_assembler import (
    ObjectDeltaBatchAssemblyError,
    PreparedObjectDeltaPayload,
    SourceOutboxDeltaItem,
    assemble_object_delta_payload,
)
from core.object_delta_outbox_allocator import (
    ObjectDeltaStreamIdentity,
    SOURCE_SERVER_BY_SITE,
    canonical_sync_item_sha256,
    stream_advisory_lock_key,
)
from core.object_delta_runtime_binding import (
    ObjectDeltaRuntimeBindingError,
    ObjectDeltaSourceRuntimeBinding,
)
from core.object_delta_source_batch_ledger import (
    GENESIS_PRIOR_CHAIN_SHA256,
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


SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE = "baseline_published"
_SOURCE_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16,64}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-z]{8,64}$")
_LOCKED_SOURCE_PUBLICATION_SNAPSHOT_CAPABILITY = object()


class ObjectDeltaLockedSourcePublicationSnapshotError(RuntimeError):
    """The locked source state cannot safely yield a canonical prefix."""


@dataclass(frozen=True)
class ObjectDeltaLockedSourcePublicationSnapshot:
    """One transaction-scoped, non-authoritative source publication view.

    ``prepared_payload is None`` is an explicit idle-frontier result only:
    it is returned after a matching stream and complete ``baseline_published``
    cutover were locked and the allocator frontier was proven empty.  A
    missing stream/cutover is a readiness/configuration failure, never
    no-work.  This value does not survive commit/rollback as a lock guarantee,
    is not signed, and cannot be passed directly to storage, encryption, or
    source-ledger persistence.  A future pre-upload gate may consume it only
    in this same transaction after it adds its own root-pinned/live-term
    authority checks.
    """

    binding: ObjectDeltaSourceRuntimeBinding
    stream: SourceStreamIdentity
    source_stream_id: int
    cutover_writer_term: WriterTermBinding
    terminal_ledger_entry: SourceBatchLedgerEntry | None
    prior_chain_sha256: str
    prepared_payload: PreparedObjectDeltaPayload | None
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def no_work(self) -> bool:
        return self.prepared_payload is None


@dataclass(frozen=True)
class _PublishedCutover:
    writer_term: WriterTermBinding


def _session_has_active_transaction(session: object) -> bool:
    probe = getattr(session, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def _require_positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            f"locked Object-delta source {label} is invalid"
        )
    return value


def _require_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            f"locked Object-delta source {label} is invalid"
        )
    return value


def _require_object_key(value: object, *, label: str) -> str:
    result = _require_text(value, label=label, pattern=OBJECT_KEY_RE)
    if ".." in result.split("/"):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            f"locked Object-delta source {label} is invalid"
        )
    return result


def _normalized_writer_term(
    *,
    epoch: object,
    lease_id: object,
    label: str,
) -> WriterTermBinding:
    """Reconstruct a Writer Witness term; its public dataclass is not a capability."""

    return WriterTermBinding(
        epoch=_require_positive_int(epoch, label=f"{label} Writer Witness epoch"),
        lease_id=_require_text(
            lease_id,
            label=f"{label} Writer Witness lease",
            pattern=LEASE_ID_RE,
        ),
    )


def _normalized_binding(
    binding: object,
) -> tuple[ObjectDeltaSourceRuntimeBinding, SourceStreamIdentity]:
    if not isinstance(binding, ObjectDeltaSourceRuntimeBinding):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source binding is invalid"
        )
    try:
        normalized = ObjectDeltaSourceRuntimeBinding(
            source_site=binding.source_site,
            destination_site=binding.destination_site,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            stream_generation_id=binding.stream_generation_id,
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
        )
        identity = SourceStreamIdentity(
            source_site=normalized.source_site,
            destination_site=normalized.destination_site,
            campaign_id=normalized.campaign_id,
            release_sha=normalized.release_sha,
            stream_generation_id=normalized.stream_generation_id,
        )
    except (
        AttributeError,
        TypeError,
        ObjectDeltaRuntimeBindingError,
        ObjectDeltaSourceLedgerError,
    ) as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source binding is invalid"
        ) from exc
    return normalized, identity


def _stream_lock_identity(stream: SourceStreamIdentity) -> ObjectDeltaStreamIdentity:
    try:
        return ObjectDeltaStreamIdentity(
            source_site=stream.source_site,
            destination_site=stream.destination_site,
            campaign_id=stream.campaign_id,
            release_sha=stream.release_sha,
            stream_generation_id=stream.stream_generation_id,
        )
    except Exception as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source stream identity is invalid"
        ) from exc


async def _scalar_one_or_none(session: object, statement: object, *, label: str):
    try:
        result = await session.execute(statement)
        return result.scalar_one_or_none()
    except Exception as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            f"locked Object-delta source {label} query failed"
        ) from exc


async def _rows(session: object, statement: object, *, label: str) -> tuple[object, ...]:
    try:
        result = await session.execute(statement)
        return tuple(result.scalars().all())
    except Exception as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            f"locked Object-delta source {label} query failed"
        ) from exc


async def _lock_stream_advisory(session: object, stream: SourceStreamIdentity) -> None:
    await _scalar_one_or_none(
        session,
        select(func.pg_advisory_xact_lock(stream_advisory_lock_key(_stream_lock_identity(stream)))),
        label="stream advisory lock",
    )


async def _load_stream_for_update(
    session: object,
    *,
    stream: SourceStreamIdentity,
) -> ObjectDeltaStream | None:
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaStream)
        .where(
            ObjectDeltaStream.source_site == stream.source_site,
            ObjectDeltaStream.destination_site == stream.destination_site,
            ObjectDeltaStream.campaign_id == stream.campaign_id,
            ObjectDeltaStream.release_sha == stream.release_sha,
            ObjectDeltaStream.stream_generation_id == stream.stream_generation_id,
        )
        .with_for_update(),
        label="stream lock",
    )


def _require_stream(
    row: ObjectDeltaStream | object,
    *,
    expected: SourceStreamIdentity,
) -> ObjectDeltaStream:
    if not isinstance(row, ObjectDeltaStream):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source stream is invalid"
        )
    try:
        actual = SourceStreamIdentity(
            source_site=row.source_site,
            destination_site=row.destination_site,
            campaign_id=row.campaign_id,
            release_sha=row.release_sha,
            stream_generation_id=row.stream_generation_id,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source stream is invalid"
        ) from exc
    if actual != expected:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source stream does not match the runtime binding"
        )
    _require_positive_int(row.id, label="stream id")
    _require_positive_int(row.next_sequence, label="stream next sequence")
    return row


async def _load_cutover_for_update(
    session: object,
    *,
    stream_id: int,
) -> ObjectDeltaSourceCutover | None:
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaSourceCutover)
        .where(ObjectDeltaSourceCutover.stream_id == stream_id)
        .with_for_update(),
        label="source-cutover lock",
    )


def _require_canonical_uuid(value: object, *, label: str) -> str:
    try:
        result = str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            f"locked Object-delta source {label} is invalid"
        ) from exc
    if isinstance(value, str) and value != result:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            f"locked Object-delta source {label} is invalid"
        )
    return result


def _require_published_cutover(
    row: ObjectDeltaSourceCutover | object,
    *,
    stream: ObjectDeltaStream,
    identity: SourceStreamIdentity,
    binding: ObjectDeltaSourceRuntimeBinding,
) -> _PublishedCutover:
    if not isinstance(row, ObjectDeltaSourceCutover):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source stream has no durable source cutover"
        )
    try:
        actual_identity = SourceStreamIdentity(
            source_site=row.source_site,
            destination_site=row.destination_site,
            campaign_id=row.campaign_id,
            release_sha=row.release_sha,
            stream_generation_id=row.stream_generation_id,
        )
        term = _normalized_writer_term(
            epoch=row.writer_epoch,
            lease_id=row.writer_lease_id,
            label="cutover",
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError, ValueError) as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source cutover is invalid"
        ) from exc
    if (
        row.stream_id != stream.id
        or actual_identity != identity
        or row.state != SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE
    ):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source cutover does not match the source stream"
        )
    registry = _require_text(
        row.registry_fingerprint,
        label="cutover registry fingerprint",
        pattern=REGISTRY_FINGERPRINT_RE,
    )
    if registry != binding.expected_registry_fingerprint:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source cutover registry fingerprint does not match the runtime binding"
        )
    _require_canonical_uuid(row.write_gate_id, label="cutover write gate")
    _require_text(row.source_generation, label="cutover source generation", pattern=_SOURCE_GENERATION_RE)
    _require_text(row.snapshot_id, label="cutover snapshot id", pattern=_SNAPSHOT_ID_RE)
    _require_text(row.alembic_revision, label="cutover Alembic revision", pattern=_ALEMBIC_REVISION_RE)
    for value, label in (
        (row.snapshot_manifest_object_key, "cutover snapshot manifest key"),
        (row.baseline_manifest_object_key, "cutover baseline manifest key"),
    ):
        _require_object_key(value, label=label)
    for value, label in (
        (row.snapshot_manifest_object_version_id, "cutover snapshot manifest version"),
        (row.baseline_manifest_object_version_id, "cutover baseline manifest version"),
    ):
        version = _require_text(value, label=label, pattern=VERSION_ID_RE)
        if version.lower() == "null":
            raise ObjectDeltaLockedSourcePublicationSnapshotError(
                f"locked Object-delta source {label} is invalid"
            )
    for value, label in (
        (row.snapshot_manifest_ciphertext_sha256, "cutover snapshot manifest hash"),
        (row.baseline_manifest_ciphertext_sha256, "cutover baseline manifest hash"),
        (row.database_sha256, "cutover database hash"),
        (row.uploads_sha256, "cutover uploads hash"),
    ):
        _require_text(value, label=label, pattern=SHA256_RE)
    for value, label in (
        (row.snapshot_manifest_ciphertext_bytes, "cutover snapshot manifest bytes"),
        (row.baseline_manifest_ciphertext_bytes, "cutover baseline manifest bytes"),
    ):
        _require_positive_int(value, label=label)
    return _PublishedCutover(writer_term=term)


async def _load_terminal_ledger_for_update(
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


def _ledger_entry_from_row(
    row: ObjectDeltaSourceBatchLedger | object,
    *,
    stream: ObjectDeltaStream,
    identity: SourceStreamIdentity,
) -> SourceBatchLedgerEntry:
    if not isinstance(row, ObjectDeltaSourceBatchLedger) or row.stream_id != stream.id:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta terminal ledger row is invalid"
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
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta terminal ledger row is invalid"
        ) from exc


async def _load_outbox_prefix_for_update(
    session: object,
    *,
    stream_id: int,
    expected_first_sequence: int,
    max_items: int,
) -> tuple[object, ...]:
    return await _rows(
        session,
        select(ObjectDeltaOutboxEntry)
        .where(
            ObjectDeltaOutboxEntry.stream_id == stream_id,
            ObjectDeltaOutboxEntry.logical_sequence >= expected_first_sequence,
        )
        .order_by(
            ObjectDeltaOutboxEntry.logical_sequence.asc(),
            ObjectDeltaOutboxEntry.id.asc(),
        )
        .limit(max_items)
        .with_for_update(),
        label="outbox prefix lock",
    )


def _outbox_item_from_row(
    row: ObjectDeltaOutboxEntry | object,
    *,
    stream: ObjectDeltaStream,
) -> SourceOutboxDeltaItem:
    if not isinstance(row, ObjectDeltaOutboxEntry) or row.stream_id != stream.id:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source outbox row is invalid"
        )
    try:
        sequence = _require_positive_int(row.logical_sequence, label="outbox logical sequence")
        change_log_id = _require_positive_int(row.change_log_id, label="outbox ChangeLog id")
        term = _normalized_writer_term(
            epoch=row.writer_epoch,
            lease_id=row.writer_lease_id,
            label="outbox",
        )
        if not isinstance(row.canonical_sync_item, Mapping):
            raise ValueError("sync item is not a mapping")
        item = dict(row.canonical_sync_item)
        if "logical_sequence" in item:
            raise ValueError("sync item carries logical sequence")
        if row.sync_item_sha256 != canonical_sync_item_sha256(item):
            raise ValueError("sync item hash differs")
    except (
        AttributeError,
        TypeError,
        ValueError,
        ObjectDeltaLockedSourcePublicationSnapshotError,
    ) as exc:
        if isinstance(exc, ObjectDeltaLockedSourcePublicationSnapshotError):
            raise
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source outbox row is invalid"
        ) from exc
    return SourceOutboxDeltaItem(
        logical_sequence=sequence,
        change_log_id=change_log_id,
        writer_epoch=term.epoch,
        writer_lease_id=term.lease_id,
        canonical_sync_item=item,
    )


def _select_contiguous_cutover_term_prefix(
    rows: tuple[object, ...],
    *,
    stream: ObjectDeltaStream,
    expected_first_sequence: int,
    max_items: int,
    cutover_term: WriterTermBinding,
) -> tuple[SourceOutboxDeltaItem, ...]:
    if not rows or len(rows) > max_items:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source outbox prefix is invalid"
        )
    selected: list[SourceOutboxDeltaItem] = []
    next_sequence = expected_first_sequence
    for row in rows:
        item = _outbox_item_from_row(row, stream=stream)
        if item.logical_sequence < expected_first_sequence:
            raise ObjectDeltaLockedSourcePublicationSnapshotError(
                "locked Object-delta source outbox row precedes the ledger frontier"
            )
        if item.logical_sequence != next_sequence:
            raise ObjectDeltaLockedSourcePublicationSnapshotError(
                "locked Object-delta source outbox prefix is not contiguous from the ledger frontier"
            )
        if item.logical_sequence >= stream.next_sequence:
            raise ObjectDeltaLockedSourcePublicationSnapshotError(
                "locked Object-delta source stream next sequence is inconsistent with its outbox"
            )
        if (item.writer_epoch, item.writer_lease_id) != (
            cutover_term.epoch,
            cutover_term.lease_id,
        ):
            raise ObjectDeltaLockedSourcePublicationSnapshotError(
                "locked Object-delta source outbox Writer Witness term does not match the published cutover"
            )
        selected.append(item)
        next_sequence += 1
    # A short query is the complete suffix.  It must reach the allocator's
    # next sequence; otherwise a missing row is hidden after the returned
    # prefix.  A full query may intentionally be a bounded prefix.
    if len(rows) < max_items and next_sequence != stream.next_sequence:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source outbox is missing a sequence before the allocator frontier"
        )
    return tuple(selected)


def _result(
    *,
    binding: ObjectDeltaSourceRuntimeBinding,
    stream: SourceStreamIdentity,
    source_stream_id: int,
    cutover_term: WriterTermBinding,
    terminal: SourceBatchLedgerEntry | None,
    prepared: PreparedObjectDeltaPayload | None,
) -> ObjectDeltaLockedSourcePublicationSnapshot:
    result = ObjectDeltaLockedSourcePublicationSnapshot(
        binding=binding,
        stream=stream,
        source_stream_id=source_stream_id,
        cutover_writer_term=cutover_term,
        terminal_ledger_entry=terminal,
        prior_chain_sha256=(terminal.batch_sha256 if terminal is not None else GENESIS_PRIOR_CHAIN_SHA256),
        prepared_payload=prepared,
    )
    object.__setattr__(result, "_capability", _LOCKED_SOURCE_PUBLICATION_SNAPSHOT_CAPABILITY)
    return require_locked_object_delta_source_publication_snapshot(result)


def _normalized_terminal_entry(
    value: object,
    *,
    stream: SourceStreamIdentity,
) -> SourceBatchLedgerEntry | None:
    if value is None:
        return None
    if type(value) is not SourceBatchLedgerEntry:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot terminal ledger is invalid"
        )
    try:
        result = SourceBatchLedgerEntry(
            stream=value.stream,
            first_sequence=value.first_sequence,
            last_sequence=value.last_sequence,
            writer_epoch=value.writer_epoch,
            writer_lease_id=value.writer_lease_id,
            prior_chain_sha256=value.prior_chain_sha256,
            batch_sha256=value.batch_sha256,
            payload_sha256=value.payload_sha256,
            payload_bytes=value.payload_bytes,
            object_key=value.object_key,
            object_version_id=value.object_version_id,
            ciphertext_sha256=value.ciphertext_sha256,
            ciphertext_bytes=value.ciphertext_bytes,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot terminal ledger is invalid"
        ) from exc
    if result.stream != stream:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot terminal ledger does not match the stream"
        )
    return result


def _normalized_prepared_payload(
    value: object,
    *,
    binding: ObjectDeltaSourceRuntimeBinding,
    stream: SourceStreamIdentity,
    cutover_term: WriterTermBinding,
    terminal: SourceBatchLedgerEntry | None,
) -> PreparedObjectDeltaPayload | None:
    if value is None:
        return None
    if type(value) is not PreparedObjectDeltaPayload:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot prepared payload is invalid"
        )
    try:
        if type(value.stream) is not SourceStreamIdentity or value.stream != stream:
            raise ValueError("stream differs")
        if type(value.writer_term) is not WriterTermBinding:
            raise ValueError("writer term is invalid")
        term = _normalized_writer_term(
            epoch=value.writer_term.epoch,
            lease_id=value.writer_term.lease_id,
            label="prepared payload",
        )
        if term != cutover_term:
            raise ValueError("term differs")
        if not isinstance(value.sequence_ids, tuple) or not value.sequence_ids:
            raise ValueError("sequences are invalid")
        sequence_ids = tuple(
            _require_positive_int(
                sequence,
                label="prepared payload logical sequence",
                maximum=MAX_STREAM_SEQUENCE_IDS * MAX_STREAM_SEQUENCE_IDS,
            )
            for sequence in value.sequence_ids
        )
        if len(sequence_ids) > MAX_STREAM_SEQUENCE_IDS or any(
            current != previous + 1 for previous, current in zip(sequence_ids, sequence_ids[1:])
        ):
            raise ValueError("sequences are not contiguous")
        expected_first = terminal.last_sequence + 1 if terminal is not None else 1
        if (
            value.first_sequence != sequence_ids[0]
            or value.last_sequence != sequence_ids[-1]
            or value.first_sequence != expected_first
        ):
            raise ValueError("frontier differs")
        if not isinstance(value.payload, bytes) or not 1 <= len(value.payload) <= MAX_DELTA_PAYLOAD_BYTES:
            raise ValueError("payload is invalid")
        if value.payload_sha256 != sha256_bytes(value.payload):
            raise ValueError("payload hash differs")
        # The assembler's internal canonical plaintext is deliberately the
        # newline-less canonical JSON form.  ``parse_object_delta_payload``
        # validates the transport representation, whose exact framing is the
        # same bytes plus one terminal newline.
        parse_object_delta_payload(
            value.payload + b"\n",
            expected_stream_generation_id=stream.stream_generation_id,
            expected_stream_sequence_ids=sequence_ids,
            expected_source_server=SOURCE_SERVER_BY_SITE[stream.source_site],
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
        )
    except (
        AttributeError,
        TypeError,
        ValueError,
        ObjectDeltaPayloadError,
        ObjectDeltaLockedSourcePublicationSnapshotError,
    ) as exc:
        if isinstance(exc, ObjectDeltaLockedSourcePublicationSnapshotError):
            raise
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot prepared payload is invalid"
        ) from exc
    return value


def require_locked_object_delta_source_publication_snapshot(
    value: object,
) -> ObjectDeltaLockedSourcePublicationSnapshot:
    """Require a provenance-minted, internally consistent locked snapshot.

    This performs pure structural/canonical revalidation only.  It never
    queries a database and cannot prove that locks are still held after the
    caller's transaction ends; a future gate must consume it in the same
    transaction and add its independent root-pinned/live-term checks.
    """

    if type(value) is not ObjectDeltaLockedSourcePublicationSnapshot:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "opaque locked Object-delta publication snapshot capability is required"
        )
    if value._capability is not _LOCKED_SOURCE_PUBLICATION_SNAPSHOT_CAPABILITY:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot was not minted by the lock adapter"
        )
    if type(value.binding) is not ObjectDeltaSourceRuntimeBinding:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot binding is invalid"
        )
    binding, stream = _normalized_binding(value.binding)
    if value.binding != binding or value.stream != stream:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot binding is not normalized"
        )
    source_stream_id = _require_positive_int(value.source_stream_id, label="snapshot stream id")
    if type(value.cutover_writer_term) is not WriterTermBinding:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot cutover term is invalid"
        )
    cutover_term = _normalized_writer_term(
        epoch=value.cutover_writer_term.epoch,
        lease_id=value.cutover_writer_term.lease_id,
        label="snapshot cutover",
    )
    if value.cutover_writer_term != cutover_term:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot cutover term is not normalized"
        )
    terminal = _normalized_terminal_entry(value.terminal_ledger_entry, stream=stream)
    if terminal is not None and (
        terminal.writer_epoch,
        terminal.writer_lease_id,
    ) != (cutover_term.epoch, cutover_term.lease_id):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot terminal term does not match the cutover"
        )
    expected_prior = terminal.batch_sha256 if terminal is not None else GENESIS_PRIOR_CHAIN_SHA256
    if value.prior_chain_sha256 != expected_prior:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta publication snapshot prior chain does not match the terminal ledger"
        )
    _normalized_prepared_payload(
        value.prepared_payload,
        binding=binding,
        stream=stream,
        cutover_term=cutover_term,
        terminal=terminal,
    )
    return value


async def snapshot_locked_object_delta_source_publication(
    session: object,
    binding: ObjectDeltaSourceRuntimeBinding,
    *,
    max_items: int,
    maximum_payload_bytes: int,
) -> ObjectDeltaLockedSourcePublicationSnapshot:
    """Return a locked, canonical next prefix or explicit no-work result.

    No row is inserted, updated, or deleted.  The caller keeps the outer
    transaction open if a future root-only pre-upload gate is to consume this
    snapshot; otherwise it should end the transaction normally.  This
    function neither validates a live lease nor authorizes publication.
    """

    if not _session_has_active_transaction(session):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source publication snapshot requires an active caller-owned transaction"
        )
    normalized_binding, identity = _normalized_binding(binding)
    limit = _require_positive_int(
        max_items,
        label="maximum item count",
        maximum=MAX_STREAM_SEQUENCE_IDS,
    )
    payload_limit = _require_positive_int(
        maximum_payload_bytes,
        label="maximum payload bytes",
        maximum=MAX_DELTA_PAYLOAD_BYTES,
    )

    await _lock_stream_advisory(session, identity)
    stream_row = await _load_stream_for_update(session, stream=identity)
    if stream_row is None:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source stream does not exist for the active runtime binding"
        )
    source_stream = _require_stream(stream_row, expected=identity)
    cutover_row = await _load_cutover_for_update(session, stream_id=source_stream.id)
    cutover = _require_published_cutover(
        cutover_row,
        stream=source_stream,
        identity=identity,
        binding=normalized_binding,
    )
    terminal_row = await _load_terminal_ledger_for_update(session, stream_id=source_stream.id)
    terminal = (
        _ledger_entry_from_row(terminal_row, stream=source_stream, identity=identity)
        if terminal_row is not None
        else None
    )
    if terminal is not None and (
        terminal.writer_epoch,
        terminal.writer_lease_id,
    ) != (cutover.writer_term.epoch, cutover.writer_term.lease_id):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta terminal ledger Writer Witness term does not match the published cutover"
        )
    expected_first_sequence = terminal.last_sequence + 1 if terminal is not None else 1
    if source_stream.next_sequence < expected_first_sequence:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source stream next sequence precedes its ledger frontier"
        )
    rows = await _load_outbox_prefix_for_update(
        session,
        stream_id=source_stream.id,
        expected_first_sequence=expected_first_sequence,
        max_items=limit,
    )
    if not rows:
        if source_stream.next_sequence != expected_first_sequence:
            raise ObjectDeltaLockedSourcePublicationSnapshotError(
                "locked Object-delta source outbox is missing the next ledger sequence"
            )
        return _result(
            binding=normalized_binding,
            stream=identity,
            source_stream_id=source_stream.id,
            cutover_term=cutover.writer_term,
            terminal=terminal,
            prepared=None,
        )
    selected = _select_contiguous_cutover_term_prefix(
        rows,
        stream=source_stream,
        expected_first_sequence=expected_first_sequence,
        max_items=limit,
        cutover_term=cutover.writer_term,
    )
    try:
        prepared = assemble_object_delta_payload(
            stream=identity,
            outbox_items=selected,
            expected_registry_fingerprint=normalized_binding.expected_registry_fingerprint,
            maximum_payload_bytes=payload_limit,
        )
    except ObjectDeltaBatchAssemblyError as exc:
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta source outbox prefix cannot form canonical payload"
        ) from exc
    if (prepared.writer_term.epoch, prepared.writer_term.lease_id) != (
        cutover.writer_term.epoch,
        cutover.writer_term.lease_id,
    ):
        raise ObjectDeltaLockedSourcePublicationSnapshotError(
            "locked Object-delta canonical payload Writer Witness term does not match the published cutover"
        )
    return _result(
        binding=normalized_binding,
        stream=identity,
        source_stream_id=source_stream.id,
        cutover_term=cutover.writer_term,
        terminal=terminal,
        prepared=prepared,
    )


__all__ = (
    "ObjectDeltaLockedSourcePublicationSnapshot",
    "ObjectDeltaLockedSourcePublicationSnapshotError",
    "require_locked_object_delta_source_publication_snapshot",
    "snapshot_locked_object_delta_source_publication",
)
