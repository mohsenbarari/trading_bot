"""Transactional source allocator for the future Object-Storage delta plane.

This module is deliberately a database-local primitive.  It never opens a
session or a connection, commits or rolls back a transaction, publishes an
Object, reads credentials, starts a worker, or invokes the legacy peer HTTP
sync path.  Its caller must already be inside the same caller-owned database
transaction that made the authoritative mutation and inserted its
``ChangeLog`` evidence.

The durable idempotency key is ``(stream_id, change_log_id)``.  An exact retry
returns the prior logical sequence without advancing ``next_sequence``.  A
retry whose Writer term or canonical evidence differs fails closed.  Fresh
allocations serialize on a PostgreSQL transaction advisory lock and the
``ObjectDeltaStream`` row lock, then reserve ``next_sequence`` and insert the
matching immutable outbox entry in that same transaction.

This is not wired into the existing runtime yet.  A later, separately reviewed
adapter must invoke it only after Writer-term validation and before its outer
transaction commits.  That adapter remains responsible for batching, age
encryption, Object Storage publication, receiver import, and all live rollout
authority.

The current generic ``core.events.log_change`` mapper listeners run on a
synchronous SQLAlchemy connection, so they must not call the async session
allocator directly.  They expose the inserted ``ChangeLog.id`` only as a
hand-off point.  A writer-only adapter may use the synchronous Connection
primitive below after its caller's flush, build a trusted request, and allocate
before the same outer transaction commits.  There is intentionally no
default-on event hook or background fallback here.

This application-level gate is not a substitute for database authority
control.  A principal that can issue raw SQL against the source tables could
otherwise bypass it, so production enablement still requires the separately
reviewed database trigger and least-privilege role boundary for stream,
cutover, and outbox mutation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
    sha256_bytes,
)
from core.append_only_sync_delta_payload import (
    OBJECT_DELTA_PAYLOAD_SCHEMA,
    ObjectDeltaPayloadError,
    REGISTRY_FINGERPRINT_RE,
    normalize_object_delta_payload,
)
from core.sync_field_policy import sanitize_sync_payload
from core.sync_metadata import deserialize_sync_data
from models.change_log import ChangeLog
from models.object_delta import (
    ObjectDeltaOutboxEntry,
    ObjectDeltaSourceCutover,
    ObjectDeltaStream,
)


ALLOCATION_ACTION_ALLOCATED = "allocated"
ALLOCATION_ACTION_REPLAY = "replay"
SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE = "baseline_published"

_SOURCE_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16,64}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-z]{8,64}$")

# Do not infer this from configuration or transport data.  The source site is
# a durable stream binding, and the payload validator independently enforces
# this exact producer value.
SOURCE_SERVER_BY_SITE = {
    "webapp_fi": "foreign",
    "webapp_ir": "iran",
}

REQUIRED_ATOMIC_ALLOCATION_STEPS = (
    "begin and own one database transaction around the authoritative write and ChangeLog insert",
    "validate the active Writer Witness term outside this allocator and pass its exact epoch and lease id",
    "load the trusted release-bound registry fingerprint outside this allocator; never derive it from the sync item",
    "call allocate_object_delta_outbox_entry before the outer transaction commits",
    "hold the transaction-scoped stream advisory lock and ObjectDeltaStream row lock",
    "lock and validate the pre-existing baseline_published source-cutover row for the exact stream, registry, and Writer term",
    "insert the outbox row and advance next_sequence in the same outer transaction",
    "commit once; on any error roll back the outer transaction",
)


class ObjectDeltaOutboxAllocationError(ValueError):
    """Raised when source evidence cannot safely receive a logical sequence."""


@dataclass(frozen=True)
class ObjectDeltaStreamIdentity:
    """The exact allocator scope; a new generation never reuses a cursor."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str


@dataclass(frozen=True)
class ObjectDeltaOutboxRequest:
    """One canonical change-log-backed item awaiting a logical sequence.

    ``canonical_sync_item`` must be the normal sync envelope *without* a
    ``logical_sequence`` field.  The allocator validates and canonicalizes it
    before persistence, so the outbox itself never treats caller-provided JSON
    formatting as authoritative.  ``expected_registry_fingerprint`` must come
    from the caller's trusted, release-bound control-plane binding; it is never
    inferred from the untrusted item being allocated.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    expected_registry_fingerprint: str
    stream_generation_id: str
    writer_epoch: int
    writer_lease_id: str
    change_log_id: int
    canonical_sync_item: Mapping[str, Any]

    @property
    def stream_identity(self) -> ObjectDeltaStreamIdentity:
        return ObjectDeltaStreamIdentity(
            source_site=self.source_site,
            destination_site=self.destination_site,
            campaign_id=self.campaign_id,
            release_sha=self.release_sha,
            stream_generation_id=self.stream_generation_id,
        )


@dataclass(frozen=True)
class ObjectDeltaOutboxAllocation:
    """The durable row selected or created by one outer transaction."""

    action: str
    stream: ObjectDeltaStream
    outbox_entry: ObjectDeltaOutboxEntry
    logical_sequence: int


@dataclass(frozen=True)
class _ValidatedAllocationRequest:
    request: ObjectDeltaOutboxRequest
    identity: ObjectDeltaStreamIdentity
    canonical_sync_item: dict[str, Any]
    sync_item_sha256: str


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaOutboxAllocationError(f"{label} is invalid")
    return value


def _require_text(value: object, *, label: str, pattern) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaOutboxAllocationError(f"{label} is invalid")
    return value


def _validate_stream_identity(identity: ObjectDeltaStreamIdentity) -> ObjectDeltaStreamIdentity:
    if not isinstance(identity, ObjectDeltaStreamIdentity):
        raise ObjectDeltaOutboxAllocationError("object-delta stream identity is invalid")
    if identity.source_site not in WEBAPP_SITES:
        raise ObjectDeltaOutboxAllocationError("object-delta source site is invalid")
    if identity.destination_site not in WEBAPP_SITES or identity.destination_site == identity.source_site:
        raise ObjectDeltaOutboxAllocationError("object-delta destination site is invalid")
    return ObjectDeltaStreamIdentity(
        source_site=identity.source_site,
        destination_site=identity.destination_site,
        campaign_id=_require_text(
            identity.campaign_id,
            label="object-delta campaign id",
            pattern=CAMPAIGN_ID_RE,
        ),
        release_sha=_require_text(
            identity.release_sha,
            label="object-delta release sha",
            pattern=RELEASE_SHA_RE,
        ),
        stream_generation_id=_require_text(
            identity.stream_generation_id,
            label="object-delta stream generation id",
            pattern=STREAM_GENERATION_ID_RE,
        ),
    )


def canonical_sync_item_sha256(value: Mapping[str, Any]) -> str:
    """Hash an already canonical sync item without opening any external resource."""

    if not isinstance(value, Mapping):
        raise ObjectDeltaOutboxAllocationError("canonical sync item is invalid")
    try:
        return sha256_bytes(canonical_json_bytes(dict(value)))
    except Exception as exc:
        raise ObjectDeltaOutboxAllocationError("canonical sync item is invalid") from exc


def stream_advisory_lock_key(identity: ObjectDeltaStreamIdentity) -> int:
    """Return the stable signed bigint key used for PostgreSQL xact locking.

    A hash collision only serializes otherwise independent streams; the unique
    database identity constraint remains the correctness boundary.
    """

    normalized = _validate_stream_identity(identity)
    material = canonical_json_bytes(
        {
            "namespace": "gold-trade-object-delta-stream-v1",
            "source_site": normalized.source_site,
            "destination_site": normalized.destination_site,
            "campaign_id": normalized.campaign_id,
            "release_sha": normalized.release_sha,
            "stream_generation_id": normalized.stream_generation_id,
        }
    )
    return int.from_bytes(hashlib.blake2b(material, digest_size=8).digest(), "big", signed=True)


def _canonicalize_request(request: ObjectDeltaOutboxRequest) -> _ValidatedAllocationRequest:
    if not isinstance(request, ObjectDeltaOutboxRequest):
        raise ObjectDeltaOutboxAllocationError("object-delta outbox request is invalid")
    identity = _validate_stream_identity(request.stream_identity)
    expected_registry_fingerprint = _require_text(
        request.expected_registry_fingerprint,
        label="expected object-delta registry fingerprint",
        pattern=REGISTRY_FINGERPRINT_RE,
    )
    writer_epoch = _require_positive_int(request.writer_epoch, label="object-delta writer epoch")
    writer_lease_id = _require_text(
        request.writer_lease_id,
        label="object-delta writer lease id",
        pattern=LEASE_ID_RE,
    )
    change_log_id = _require_positive_int(
        request.change_log_id,
        label="object-delta ChangeLog evidence id",
    )
    if not isinstance(request.canonical_sync_item, Mapping):
        raise ObjectDeltaOutboxAllocationError("canonical sync item is invalid")
    raw_item = dict(request.canonical_sync_item)
    if "logical_sequence" in raw_item:
        raise ObjectDeltaOutboxAllocationError(
            "canonical sync item must not contain a logical sequence"
        )
    try:
        normalized = normalize_object_delta_payload(
            {
                "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
                "stream_generation_id": identity.stream_generation_id,
                "items": [{"logical_sequence": 1, **raw_item}],
            },
            expected_stream_generation_id=identity.stream_generation_id,
            expected_stream_sequence_ids=(1,),
            expected_source_server=SOURCE_SERVER_BY_SITE[identity.source_site],
            expected_registry_fingerprint=expected_registry_fingerprint,
        )
    except ObjectDeltaPayloadError as exc:
        raise ObjectDeltaOutboxAllocationError("canonical sync item is invalid") from exc
    item = normalized.items[0]
    if item.change_log_id != change_log_id:
        raise ObjectDeltaOutboxAllocationError(
            "canonical sync item ChangeLog evidence does not match the request"
        )
    canonical_item = item.as_sync_item()
    # Retain only validated scalar bindings.  This makes an accidental mutable
    # request object unable to alter the values used after validation.
    normalized_request = ObjectDeltaOutboxRequest(
        source_site=identity.source_site,
        destination_site=identity.destination_site,
        campaign_id=identity.campaign_id,
        release_sha=identity.release_sha,
        expected_registry_fingerprint=expected_registry_fingerprint,
        stream_generation_id=identity.stream_generation_id,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        change_log_id=change_log_id,
        canonical_sync_item=canonical_item,
    )
    return _ValidatedAllocationRequest(
        request=normalized_request,
        identity=identity,
        canonical_sync_item=canonical_item,
        sync_item_sha256=canonical_sync_item_sha256(canonical_item),
    )


def _session_has_active_transaction(session: object) -> bool:
    probe = getattr(session, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def _require_matching_change_log(
    change_log: ChangeLog | object,
    validated: _ValidatedAllocationRequest,
) -> None:
    if change_log is None:
        raise ObjectDeltaOutboxAllocationError("source ChangeLog evidence does not exist")
    request = validated.request
    try:
        expected_data = sanitize_sync_payload(
            change_log.table_name,
            deserialize_sync_data(change_log.data),
        )
        expected_timestamp = change_log.timestamp.timestamp()
        expected = {
            "change_log_id": change_log.id,
            "operation": change_log.operation,
            "table": change_log.table_name,
            "id": change_log.record_id,
            "data": expected_data,
            "hash": change_log.hash,
            "timestamp": expected_timestamp,
        }
        # Python considers values such as ``1`` and ``1.0`` equal even though
        # their canonical JSON (and therefore the durable outbox digest)
        # differs.  Compare canonical bytes so an initially accepted item can
        # always be retried with the exact same idempotency fingerprint.
        actual_evidence = {
            field: validated.canonical_sync_item.get(field)
            for field in expected
        }
        evidence_matches = canonical_json_bytes(actual_evidence) == canonical_json_bytes(expected)
    except Exception as exc:
        raise ObjectDeltaOutboxAllocationError("source ChangeLog evidence is invalid") from exc
    if expected["change_log_id"] != request.change_log_id:
        raise ObjectDeltaOutboxAllocationError("source ChangeLog evidence does not match the request")
    if not evidence_matches:
        raise ObjectDeltaOutboxAllocationError(
            "canonical sync item does not match the locked ChangeLog evidence"
        )


def _require_matching_stream(
    stream: ObjectDeltaStream | object,
    identity: ObjectDeltaStreamIdentity,
) -> ObjectDeltaStream:
    if not isinstance(stream, ObjectDeltaStream):
        raise ObjectDeltaOutboxAllocationError("object-delta stream row is invalid")
    actual = ObjectDeltaStreamIdentity(
        source_site=stream.source_site,
        destination_site=stream.destination_site,
        campaign_id=stream.campaign_id,
        release_sha=stream.release_sha,
        stream_generation_id=stream.stream_generation_id,
    )
    if actual != identity:
        raise ObjectDeltaOutboxAllocationError("locked object-delta stream does not match the request")
    _require_positive_int(stream.id, label="object-delta stream id")
    _require_positive_int(stream.next_sequence, label="object-delta next sequence")
    return stream


def _require_canonical_uuid(value: object, *, label: str) -> str:
    try:
        normalized = str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaOutboxAllocationError(f"{label} is invalid") from exc
    if isinstance(value, str) and value != normalized:
        raise ObjectDeltaOutboxAllocationError(f"{label} is invalid")
    return normalized


def _require_object_key(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or OBJECT_KEY_RE.fullmatch(value) is None
        or ".." in value.split("/")
    ):
        raise ObjectDeltaOutboxAllocationError(f"{label} is invalid")
    return value


def _require_published_source_cutover(
    cutover: ObjectDeltaSourceCutover | object,
    *,
    stream: ObjectDeltaStream,
    validated: _ValidatedAllocationRequest,
) -> ObjectDeltaSourceCutover:
    """Validate one locked, durable cutover before allocating any sequence.

    A source stream is deliberately *not* self-bootstrapping: the root-only
    cutover coordinator must pre-create its stream and commit a complete
    ``baseline_published`` row before the normal application runtime may
    allocate a first delta.  This closes the crash/restart gap where a lazy
    allocator could otherwise create sequence one before a receiver has a
    durable baseline.

    The caller must already hold the stream advisory lock and the exact stream
    row lock.  The concrete sync/async loaders below also lock this cutover row
    with ``FOR UPDATE`` so a coordinator cannot revise its publication state
    between the check and the outbox insert in the same outer transaction.
    """

    if not isinstance(cutover, ObjectDeltaSourceCutover):
        raise ObjectDeltaOutboxAllocationError(
            "object-delta source stream has no durable source cutover"
        )
    request = validated.request
    identity = validated.identity
    try:
        cutover_identity = (
            cutover.source_site,
            cutover.destination_site,
            cutover.campaign_id,
            cutover.release_sha,
            cutover.stream_generation_id,
        )
    except AttributeError as exc:
        raise ObjectDeltaOutboxAllocationError("object-delta source cutover is invalid") from exc
    expected_identity = (
        identity.source_site,
        identity.destination_site,
        identity.campaign_id,
        identity.release_sha,
        identity.stream_generation_id,
    )
    cutover_stream_id = _require_positive_int(
        cutover.stream_id,
        label="object-delta source cutover stream id",
    )
    if cutover_stream_id != stream.id or cutover_identity != expected_identity:
        raise ObjectDeltaOutboxAllocationError(
            "locked object-delta source cutover does not match the stream"
        )
    if cutover.state != SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta source cutover is not baseline published"
        )
    registry_fingerprint = _require_text(
        cutover.registry_fingerprint,
        label="object-delta source cutover registry fingerprint",
        pattern=REGISTRY_FINGERPRINT_RE,
    )
    if registry_fingerprint != request.expected_registry_fingerprint:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta source cutover registry fingerprint does not match the request"
        )
    writer_epoch = _require_positive_int(
        cutover.writer_epoch,
        label="object-delta source cutover writer epoch",
    )
    writer_lease_id = _require_text(
        cutover.writer_lease_id,
        label="object-delta source cutover writer lease id",
        pattern=LEASE_ID_RE,
    )
    if (
        writer_epoch != request.writer_epoch
        or writer_lease_id != request.writer_lease_id
    ):
        raise ObjectDeltaOutboxAllocationError(
            "object-delta source cutover Writer Witness term does not match the request"
        )

    # PostgreSQL constraints protect a real row, but revalidate every durable
    # evidence field here as well.  This is the allocator's last in-process
    # boundary before it makes a new sequence visible to a future publisher.
    _require_canonical_uuid(cutover.write_gate_id, label="object-delta source cutover write gate")
    _require_text(
        cutover.source_generation,
        label="object-delta source cutover source generation",
        pattern=_SOURCE_GENERATION_RE,
    )
    _require_text(
        cutover.snapshot_id,
        label="object-delta source cutover snapshot id",
        pattern=_SNAPSHOT_ID_RE,
    )
    _require_text(
        cutover.alembic_revision,
        label="object-delta source cutover Alembic revision",
        pattern=_ALEMBIC_REVISION_RE,
    )
    for value, label in (
        (cutover.snapshot_manifest_object_key, "object-delta source cutover snapshot manifest key"),
        (cutover.baseline_manifest_object_key, "object-delta source cutover baseline manifest key"),
    ):
        _require_object_key(value, label=label)
    for value, label in (
        (cutover.snapshot_manifest_object_version_id, "object-delta source cutover snapshot manifest version"),
        (cutover.baseline_manifest_object_version_id, "object-delta source cutover baseline manifest version"),
    ):
        _require_text(value, label=label, pattern=VERSION_ID_RE)
    for value, label in (
        (cutover.snapshot_manifest_ciphertext_sha256, "object-delta source cutover snapshot manifest hash"),
        (cutover.baseline_manifest_ciphertext_sha256, "object-delta source cutover baseline manifest hash"),
        (cutover.database_sha256, "object-delta source cutover database hash"),
        (cutover.uploads_sha256, "object-delta source cutover uploads hash"),
    ):
        _require_text(value, label=label, pattern=SHA256_RE)
    for value, label in (
        (cutover.snapshot_manifest_ciphertext_bytes, "object-delta source cutover snapshot manifest bytes"),
        (cutover.baseline_manifest_ciphertext_bytes, "object-delta source cutover baseline manifest bytes"),
    ):
        _require_positive_int(value, label=label)
    return cutover


def _existing_entry_is_exact(
    entry: ObjectDeltaOutboxEntry | object,
    *,
    stream: ObjectDeltaStream,
    validated: _ValidatedAllocationRequest,
) -> bool:
    if not isinstance(entry, ObjectDeltaOutboxEntry):
        return False
    request = validated.request
    try:
        logical_sequence = _require_positive_int(
            entry.logical_sequence,
            label="existing logical sequence",
        )
        return (
            entry.stream_id == stream.id
            # A committed allocator always advances next_sequence in the same
            # transaction.  Treat a damaged counter as evidence conflict
            # rather than returning a replay that could later overlap.
            and logical_sequence < stream.next_sequence
            and entry.change_log_id == request.change_log_id
            and entry.writer_epoch == request.writer_epoch
            and entry.writer_lease_id == request.writer_lease_id
            and entry.canonical_sync_item == validated.canonical_sync_item
            and entry.sync_item_sha256 == validated.sync_item_sha256
            and canonical_sync_item_sha256(entry.canonical_sync_item) == validated.sync_item_sha256
        )
    except Exception:
        return False


@dataclass(frozen=True)
class _ConnectionChangeLogEvidence:
    """The minimum locked ChangeLog projection needed by shared validation."""

    id: Any
    operation: Any
    table_name: Any
    record_id: Any
    data: Any
    timestamp: Any
    hash: Any


def _connection_has_active_transaction(connection: object) -> bool:
    """Require an already-open SQLAlchemy Connection transaction.

    Calling ``Connection.execute`` without this check may autobegin a new
    transaction.  The synchronous adapter is only valid inside the caller's
    authoritative write transaction, so it must reject that case before any
    SQL is issued.
    """

    probe = getattr(connection, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def _connection_mapping_one_or_none(
    connection: object,
    statement: object,
    *,
    label: str,
) -> dict[str, Any] | None:
    """Execute one Core statement and obtain at most one mapping row.

    This intentionally accepts only the Connection-level ``mappings`` API.
    It prevents a mapper-event integration from silently relying on a Session
    or creating its own transaction scope.
    """

    try:
        result = connection.execute(statement)
        mappings = result.mappings()
        row = mappings.one_or_none()
    except Exception as exc:
        raise ObjectDeltaOutboxAllocationError(
            f"object-delta {label} query failed"
        ) from exc
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise ObjectDeltaOutboxAllocationError(
            f"object-delta {label} query returned an invalid row"
        )
    return dict(row)


def _connection_execute(connection: object, statement: object, *, label: str) -> None:
    try:
        connection.execute(statement)
    except Exception as exc:
        raise ObjectDeltaOutboxAllocationError(
            f"object-delta {label} query failed"
        ) from exc


def _connection_change_log_evidence(row: Mapping[str, Any]) -> _ConnectionChangeLogEvidence:
    try:
        return _ConnectionChangeLogEvidence(
            id=row["id"],
            operation=row["operation"],
            table_name=row["table_name"],
            record_id=row["record_id"],
            data=row["data"],
            timestamp=row["timestamp"],
            hash=row["hash"],
        )
    except (KeyError, TypeError) as exc:
        raise ObjectDeltaOutboxAllocationError(
            "source ChangeLog evidence is invalid"
        ) from exc


def _connection_stream_from_row(
    row: Mapping[str, Any],
    identity: ObjectDeltaStreamIdentity,
) -> ObjectDeltaStream:
    try:
        stream = ObjectDeltaStream(
            id=row["id"],
            source_site=row["source_site"],
            destination_site=row["destination_site"],
            campaign_id=row["campaign_id"],
            release_sha=row["release_sha"],
            stream_generation_id=row["stream_generation_id"],
            next_sequence=row["next_sequence"],
        )
    except (KeyError, TypeError) as exc:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta stream row is invalid"
        ) from exc
    return _require_matching_stream(stream, identity)


def _connection_outbox_from_row(row: Mapping[str, Any]) -> ObjectDeltaOutboxEntry:
    try:
        return ObjectDeltaOutboxEntry(
            id=row["id"],
            stream_id=row["stream_id"],
            logical_sequence=row["logical_sequence"],
            change_log_id=row["change_log_id"],
            writer_epoch=row["writer_epoch"],
            writer_lease_id=row["writer_lease_id"],
            canonical_sync_item=row["canonical_sync_item"],
            sync_item_sha256=row["sync_item_sha256"],
        )
    except (KeyError, TypeError) as exc:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta outbox row is invalid"
        ) from exc


def _connection_lock_source_change_log(
    connection: object,
    change_log_id: int,
) -> _ConnectionChangeLogEvidence:
    table = ChangeLog.__table__
    row = _connection_mapping_one_or_none(
        connection,
        select(table).where(table.c.id == change_log_id).with_for_update(),
        label="source ChangeLog lock",
    )
    if row is None:
        raise ObjectDeltaOutboxAllocationError("source ChangeLog evidence does not exist")
    return _connection_change_log_evidence(row)


def _connection_lock_stream_advisory(
    connection: object,
    identity: ObjectDeltaStreamIdentity,
) -> None:
    # PostgreSQL retains this lock until the caller's outer transaction ends.
    # There is deliberately no fallback that would weaken stream ordering.
    _connection_execute(
        connection,
        select(func.pg_advisory_xact_lock(stream_advisory_lock_key(identity))),
        label="stream advisory lock",
    )


def _connection_load_stream_for_update(
    connection: object,
    identity: ObjectDeltaStreamIdentity,
) -> ObjectDeltaStream | None:
    table = ObjectDeltaStream.__table__
    row = _connection_mapping_one_or_none(
        connection,
        select(table)
        .where(
            table.c.source_site == identity.source_site,
            table.c.destination_site == identity.destination_site,
            table.c.campaign_id == identity.campaign_id,
            table.c.release_sha == identity.release_sha,
            table.c.stream_generation_id == identity.stream_generation_id,
        )
        .with_for_update(),
        label="stream lock",
    )
    return _connection_stream_from_row(row, identity) if row is not None else None


def _connection_lock_published_source_cutover(
    connection: object,
    *,
    stream: ObjectDeltaStream,
    validated: _ValidatedAllocationRequest,
) -> ObjectDeltaSourceCutover:
    """Lock and verify the pre-existing cutover after the stream row lock."""

    table = ObjectDeltaSourceCutover.__table__
    row = _connection_mapping_one_or_none(
        connection,
        select(table)
        .where(table.c.stream_id == stream.id)
        .with_for_update(),
        label="source cutover lock",
    )
    if row is None:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta source stream has no durable source cutover"
        )
    try:
        cutover = ObjectDeltaSourceCutover(**row)
    except (TypeError, ValueError) as exc:
        raise ObjectDeltaOutboxAllocationError("object-delta source cutover row is invalid") from exc
    return _require_published_source_cutover(
        cutover,
        stream=stream,
        validated=validated,
    )


def _connection_load_existing_outbox_for_update(
    connection: object,
    *,
    stream_id: int,
    change_log_id: int,
) -> ObjectDeltaOutboxEntry | None:
    table = ObjectDeltaOutboxEntry.__table__
    row = _connection_mapping_one_or_none(
        connection,
        select(table)
        .where(
            table.c.stream_id == stream_id,
            table.c.change_log_id == change_log_id,
        )
        .with_for_update(),
        label="outbox lock",
    )
    return _connection_outbox_from_row(row) if row is not None else None


def _connection_reserve_stream_sequence(
    connection: object,
    *,
    stream: ObjectDeltaStream,
    sequence: int,
    identity: ObjectDeltaStreamIdentity,
) -> ObjectDeltaStream:
    table = ObjectDeltaStream.__table__
    row = _connection_mapping_one_or_none(
        connection,
        update(table)
        .where(table.c.id == stream.id, table.c.next_sequence == sequence)
        .values(next_sequence=sequence + 1)
        .returning(*table.c),
        label="stream sequence reservation",
    )
    if row is None:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta stream sequence changed while locked"
        )
    return _connection_stream_from_row(row, identity)


def _connection_insert_outbox_entry(
    connection: object,
    *,
    stream_id: int,
    sequence: int,
    validated: _ValidatedAllocationRequest,
) -> ObjectDeltaOutboxEntry:
    table = ObjectDeltaOutboxEntry.__table__
    row = _connection_mapping_one_or_none(
        connection,
        insert(table)
        .values(
            stream_id=stream_id,
            logical_sequence=sequence,
            change_log_id=validated.request.change_log_id,
            writer_epoch=validated.request.writer_epoch,
            writer_lease_id=validated.request.writer_lease_id,
            canonical_sync_item=validated.canonical_sync_item,
            sync_item_sha256=validated.sync_item_sha256,
        )
        .returning(*table.c),
        label="outbox insert",
    )
    if row is None:
        raise ObjectDeltaOutboxAllocationError("object-delta outbox insert returned no row")
    return _connection_outbox_from_row(row)


def allocate_object_delta_outbox_entry_sync(
    connection: object,
    request: ObjectDeltaOutboxRequest,
) -> ObjectDeltaOutboxAllocation:
    """Synchronously allocate one entry in a caller-owned Connection transaction.

    This is the mapper/after-flush compatible counterpart to
    :func:`allocate_object_delta_outbox_entry`.  It never calls ``begin``,
    ``commit``, or ``rollback``.  A caller that catches an allocation error
    must still roll back its outer authoritative transaction; committing after
    an error would violate the source/outbox atomicity contract.

    Returned ORM-shaped values are detached snapshots of rows read or written
    through Core SQL.  They are evidence for the caller and must not be treated
    as Session-managed instances.
    """

    if not _connection_has_active_transaction(connection):
        raise ObjectDeltaOutboxAllocationError(
            "object-delta allocation requires an active caller-owned transaction"
        )
    validated = _canonicalize_request(request)
    change_log = _connection_lock_source_change_log(
        connection,
        validated.request.change_log_id,
    )
    _require_matching_change_log(change_log, validated)
    _connection_lock_stream_advisory(connection, validated.identity)
    stream = _connection_load_stream_for_update(connection, validated.identity)
    if stream is None:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta source stream must be pre-created by a published cutover"
        )
    _connection_lock_published_source_cutover(
        connection,
        stream=stream,
        validated=validated,
    )

    existing = _connection_load_existing_outbox_for_update(
        connection,
        stream_id=stream.id,
        change_log_id=validated.request.change_log_id,
    )
    if existing is not None:
        if not _existing_entry_is_exact(existing, stream=stream, validated=validated):
            raise ObjectDeltaOutboxAllocationError(
                "existing object-delta outbox entry conflicts with source evidence"
            )
        return ObjectDeltaOutboxAllocation(
            action=ALLOCATION_ACTION_REPLAY,
            stream=stream,
            outbox_entry=existing,
            logical_sequence=existing.logical_sequence,
        )

    sequence = _require_positive_int(stream.next_sequence, label="object-delta next sequence")
    entry = _connection_insert_outbox_entry(
        connection,
        stream_id=stream.id,
        sequence=sequence,
        validated=validated,
    )
    reserved_stream = _connection_reserve_stream_sequence(
        connection,
        stream=stream,
        sequence=sequence,
        identity=validated.identity,
    )
    return ObjectDeltaOutboxAllocation(
        action=ALLOCATION_ACTION_ALLOCATED,
        stream=reserved_stream,
        outbox_entry=entry,
        logical_sequence=sequence,
    )


def allocate_object_delta_outbox_entry_on_connection(
    connection: object,
    request: ObjectDeltaOutboxRequest,
) -> ObjectDeltaOutboxAllocation:
    """Compatibility spelling for the public synchronous allocator."""

    return allocate_object_delta_outbox_entry_sync(connection, request)


async def _lock_source_change_log(session: object, change_log_id: int) -> ChangeLog:
    result = await session.execute(
        select(ChangeLog).where(ChangeLog.id == change_log_id).with_for_update()
    )
    change_log = result.scalar_one_or_none()
    if change_log is None:
        raise ObjectDeltaOutboxAllocationError("source ChangeLog evidence does not exist")
    return change_log


async def _lock_stream_advisory(session: object, identity: ObjectDeltaStreamIdentity) -> None:
    # PostgreSQL holds pg_advisory_xact_lock until the *outer* transaction ends.
    # The allocator intentionally has no fallback: this future data plane is
    # PostgreSQL-backed, and silently dropping the lock would violate ordering.
    await session.execute(select(func.pg_advisory_xact_lock(stream_advisory_lock_key(identity))))


async def _load_stream_for_update(
    session: object,
    identity: ObjectDeltaStreamIdentity,
) -> ObjectDeltaStream | None:
    result = await session.execute(
        select(ObjectDeltaStream)
        .where(
            ObjectDeltaStream.source_site == identity.source_site,
            ObjectDeltaStream.destination_site == identity.destination_site,
            ObjectDeltaStream.campaign_id == identity.campaign_id,
            ObjectDeltaStream.release_sha == identity.release_sha,
            ObjectDeltaStream.stream_generation_id == identity.stream_generation_id,
        )
        .with_for_update()
    )
    stream = result.scalar_one_or_none()
    return _require_matching_stream(stream, identity) if stream is not None else None


async def _lock_published_source_cutover(
    session: object,
    *,
    stream: ObjectDeltaStream,
    validated: _ValidatedAllocationRequest,
) -> ObjectDeltaSourceCutover:
    """Lock and validate the source cutover after the stream lock is held."""

    result = await session.execute(
        select(ObjectDeltaSourceCutover)
        .where(ObjectDeltaSourceCutover.stream_id == stream.id)
        .with_for_update()
    )
    cutover = result.scalar_one_or_none()
    return _require_published_source_cutover(
        cutover,
        stream=stream,
        validated=validated,
    )


async def _load_existing_outbox_for_update(
    session: object,
    *,
    stream_id: int,
    change_log_id: int,
) -> ObjectDeltaOutboxEntry | None:
    result = await session.execute(
        select(ObjectDeltaOutboxEntry)
        .where(
            ObjectDeltaOutboxEntry.stream_id == stream_id,
            ObjectDeltaOutboxEntry.change_log_id == change_log_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def allocate_object_delta_outbox_entry(
    session: object,
    request: ObjectDeltaOutboxRequest,
) -> ObjectDeltaOutboxAllocation:
    """Allocate exactly one logical sequence inside a caller-owned transaction.

    The function intentionally does not call ``begin``, ``commit``, or
    ``rollback``.  It must run after the authoritative mutation and its
    ``ChangeLog`` row exist in the caller's open transaction.  Any exception
    must abort that outer transaction; proceeding after a failed allocation
    would create a source change without the future Object-delta outbox.
    """

    if not _session_has_active_transaction(session):
        raise ObjectDeltaOutboxAllocationError(
            "object-delta allocation requires an active caller-owned transaction"
        )
    validated = _canonicalize_request(request)
    change_log = await _lock_source_change_log(session, validated.request.change_log_id)
    _require_matching_change_log(change_log, validated)
    await _lock_stream_advisory(session, validated.identity)
    stream = await _load_stream_for_update(session, validated.identity)
    if stream is None:
        raise ObjectDeltaOutboxAllocationError(
            "object-delta source stream must be pre-created by a published cutover"
        )
    await _lock_published_source_cutover(
        session,
        stream=stream,
        validated=validated,
    )

    existing = await _load_existing_outbox_for_update(
        session,
        stream_id=stream.id,
        change_log_id=validated.request.change_log_id,
    )
    if existing is not None:
        if not _existing_entry_is_exact(existing, stream=stream, validated=validated):
            raise ObjectDeltaOutboxAllocationError(
                "existing object-delta outbox entry conflicts with source evidence"
            )
        return ObjectDeltaOutboxAllocation(
            action=ALLOCATION_ACTION_REPLAY,
            stream=stream,
            outbox_entry=existing,
            logical_sequence=existing.logical_sequence,
        )

    sequence = _require_positive_int(stream.next_sequence, label="object-delta next sequence")
    entry = ObjectDeltaOutboxEntry(
        stream_id=stream.id,
        logical_sequence=sequence,
        change_log_id=validated.request.change_log_id,
        writer_epoch=validated.request.writer_epoch,
        writer_lease_id=validated.request.writer_lease_id,
        canonical_sync_item=validated.canonical_sync_item,
        sync_item_sha256=validated.sync_item_sha256,
    )
    stream.next_sequence = sequence + 1
    session.add(entry)
    await session.flush()
    return ObjectDeltaOutboxAllocation(
        action=ALLOCATION_ACTION_ALLOCATED,
        stream=stream,
        outbox_entry=entry,
        logical_sequence=sequence,
    )
