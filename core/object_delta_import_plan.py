"""Pure transaction plan for a future atomic Object-delta importer.

This is deliberately *not* an importer.  It does not open a database
session, touch Object Storage, invoke the legacy ``/api/sync/receive`` route,
or emit Redis, realtime, Telegram, cache, or audit side effects.  A future
runtime adapter must first verify a source signature, its fixed Object Storage
endpoint/bucket binding, and authenticated age decryption; it must then obtain
the validated objects and lock-scoped database state, pass them here, and
execute the returned plan in one transaction.  A self-hash alone is not a
source-authentication mechanism.

The existing generic sync receiver cannot be reused for this purpose: it
handles compatibility fallbacks and performs post-commit side effects.  This
contract makes the required cursor and immutable-receipt semantics explicit
without introducing a migration or a live data path.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    AppendOnlySyncDeltaBatch,
)
from core.append_only_sync_delta_payload import (
    OBJECT_DELTA_PAYLOAD_SCHEMA,
    NormalizedObjectDeltaPayload,
    normalize_object_delta_payload,
)
from core.object_delta_mvp_canonical import validate_canonical_mvp_object_delta
from core.object_delta_receiver_mvp_handlers import (
    COMMODITIES_NATURAL_KEY,
    COMMODITIES_TABLE,
    INSERT,
    ObjectDeltaReceiverMvpHandlerError,
    ObjectDeltaReceiverMvpPlannedChange,
    compile_object_delta_mvp_receiver_planned_change,
    require_object_delta_mvp_receiver_planned_change,
)


IMPORT_ACTION_APPLY = "apply"
IMPORT_ACTION_REPLAY = "replay"

# The application server names are intentionally fixed here.  A future
# database adapter must not infer the source from an untrusted payload.
SOURCE_SERVER_BY_SITE = {
    "webapp_fi": "foreign",
    "webapp_ir": "iran",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REGISTRY_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")


class ObjectDeltaImportPlanError(ValueError):
    """Raised when a validated Object delta cannot be imported atomically."""


@dataclass(frozen=True)
class ReceiverStreamCursor:
    """The one durable receiver cursor for a source stream generation.

    Future schema key:
    ``(source_site, destination_site, campaign_id, release_sha,
    stream_generation_id)``.  ``last_batch_sha256`` is the chain predecessor
    required by the next batch, not a raw PostgreSQL ``ChangeLog.id``.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    last_sequence: int
    last_batch_sha256: str


@dataclass(frozen=True)
class ObjectDeltaImportReceipt:
    """Immutable receiver-side receipt for exactly one Object version.

    Future schema needs both unique constraints below:

    * ``(object_key, object_version_id)`` prevents an Object version from
      being applied twice under a different stream identity within the fixed,
      receiver-configured Object Storage bucket.  The bucket/endpoint itself
      must be signature-bound outside this data-only contract.
    * ``(source_site, destination_site, campaign_id, release_sha,
      stream_generation_id, first_sequence)`` makes a logical range
      idempotent even if transport retries use a different request.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    first_sequence: int
    last_sequence: int
    writer_epoch: int
    writer_lease_id: str
    prior_chain_sha256: str
    batch_sha256: str
    payload_sha256: str
    object_key: str
    object_version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


# The legacy sync-item mapping is intentionally not retained after planning.
# Only an opaque, exact handler capability can reach a dedicated receiver
# applier.  Keep this compatibility name local to the import-plan contract so
# downstream adapters do not accidentally regain access to generic payload
# fields.
PlannedObjectDeltaChange = ObjectDeltaReceiverMvpPlannedChange


@dataclass(frozen=True)
class AtomicObjectDeltaImportPlan:
    """A no-I/O decision that a future DB adapter can execute atomically."""

    action: str
    receipt_to_insert: ObjectDeltaImportReceipt | None
    cursor_to_write: ReceiverStreamCursor | None
    changes_to_apply: tuple[PlannedObjectDeltaChange, ...]


# These are intentionally data-only requirements.  The future adapter must
# execute them in this order, using the same AsyncSession transaction, and
# must pass the lock-scoped results back to ``plan_atomic_object_delta_import``.
REQUIRED_ATOMIC_TRANSACTION_STEPS = (
    "verify the source signature, fixed Object Storage endpoint/bucket binding, and authenticated age decryption before database work",
    "begin one database transaction",
    "acquire transaction-scoped advisory locks for the receiver stream and immutable object version in deterministic order",
    "select receiver cursor for update",
    "select receipts by immutable object version and logical first sequence for update",
    "re-run pure plan_atomic_object_delta_import with the locked rows",
    "apply every planned db_change only through a dedicated no-side-effect adapter",
    "insert immutable receipt and upsert receiver cursor in the same transaction",
    "when a signed delivery packet authorizes the import, lock/load and consume its (controller_key_id, nonce) receipt in that same transaction",
    "commit once after all changes, receipts, and cursor succeed",
)


def expected_import_receipt(batch: AppendOnlySyncDeltaBatch) -> ObjectDeltaImportReceipt:
    """Derive the only receiver receipt that can match a validated batch."""

    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaImportPlanError("validated delta batch is required")
    return ObjectDeltaImportReceipt(
        source_site=batch.source_site,
        destination_site=batch.destination_site,
        campaign_id=batch.campaign_id,
        release_sha=batch.release_sha,
        stream_generation_id=batch.stream.generation_id,
        first_sequence=batch.stream.first_sequence,
        last_sequence=batch.stream.last_sequence,
        writer_epoch=batch.writer_term.epoch,
        writer_lease_id=batch.writer_term.lease_id,
        prior_chain_sha256=batch.prior_chain_sha256,
        batch_sha256=batch.batch_sha256,
        payload_sha256=batch.payload_sha256,
        object_key=batch.immutable_receipt.object_key,
        object_version_id=batch.immutable_receipt.version_id,
        ciphertext_sha256=batch.immutable_receipt.ciphertext_sha256,
        ciphertext_bytes=batch.immutable_receipt.ciphertext_bytes,
    )


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaImportPlanError(f"{label} is invalid")
    return value


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ObjectDeltaImportPlanError(f"{label} is invalid")
    return value


def _require_site(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in SOURCE_SERVER_BY_SITE:
        raise ObjectDeltaImportPlanError(f"{label} is invalid")
    return value


def _require_registry_fingerprint(value: object) -> str:
    if not isinstance(value, str) or _REGISTRY_FINGERPRINT_RE.fullmatch(value) is None:
        raise ObjectDeltaImportPlanError("expected registry fingerprint is invalid")
    return value


def _require_expected_binding(value: object, *, actual: str, label: str) -> None:
    if not isinstance(value, str) or value != actual:
        raise ObjectDeltaImportPlanError(f"delta batch {label} does not match receiver binding")


def _validate_payload_binding(
    *,
    batch: AppendOnlySyncDeltaBatch,
    payload: NormalizedObjectDeltaPayload,
    expected_source_server: str,
    expected_registry_fingerprint: str,
) -> tuple[PlannedObjectDeltaChange, ...]:
    if type(payload) is not NormalizedObjectDeltaPayload:
        raise ObjectDeltaImportPlanError("normalized object delta payload is required")
    if type(payload.items) is not tuple or not payload.items:
        raise ObjectDeltaImportPlanError("normalized object delta payload is invalid")
    if payload.stream_generation_id != batch.stream.generation_id:
        raise ObjectDeltaImportPlanError("payload stream generation does not match the batch")

    expected_sequences = batch.stream.sequence_ids
    actual_sequences = tuple(item.logical_sequence for item in payload.items)
    if actual_sequences != expected_sequences:
        raise ObjectDeltaImportPlanError("payload logical sequence does not match the batch")

    # A public dataclass alone is not an execution boundary.  Re-run the
    # canonical payload normalizer using the release-bound expectations before
    # deriving any receiver handler intent, so a hand-built Normalized payload
    # cannot bypass source/protocol/field-policy checks.
    try:
        revalidated = normalize_object_delta_payload(
            {
                "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
                "stream_generation_id": payload.stream_generation_id,
                "items": [item.item for item in payload.items],
            },
            expected_stream_generation_id=batch.stream.generation_id,
            expected_stream_sequence_ids=expected_sequences,
            expected_source_server=expected_source_server,
            expected_registry_fingerprint=expected_registry_fingerprint,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaImportPlanError(
            "payload does not match the release-bound receiver registry"
        ) from exc
    if revalidated != payload:
        raise ObjectDeltaImportPlanError(
            "normalized payload is not canonically bound to the receiver registry"
        )

    planned: list[PlannedObjectDeltaChange] = []
    for item in revalidated.items:
        envelope = item.item
        if envelope.get("table") != COMMODITIES_TABLE:
            raise ObjectDeltaImportPlanError(
                "payload table has no release-pinned receiver handler"
            )
        if envelope.get("operation") != INSERT:
            raise ObjectDeltaImportPlanError(
                "payload operation has no release-pinned receiver handler"
            )
        data = envelope.get("data")
        record_id = envelope.get("id")
        if (
            not isinstance(data, dict)
            or set(data) != {"id", COMMODITIES_NATURAL_KEY}
            or data.get("id") != record_id
        ):
            raise ObjectDeltaImportPlanError(
                "commodities payload does not match the exact receiver handler contract"
            )
        try:
            descriptor = validate_canonical_mvp_object_delta(
                {
                    "table": COMMODITIES_TABLE,
                    "operation": INSERT,
                    "identity": {COMMODITIES_NATURAL_KEY: data[COMMODITIES_NATURAL_KEY]},
                    "fields": {},
                    "references": {},
                }
            )
            planned_change = compile_object_delta_mvp_receiver_planned_change(
                logical_sequence=item.logical_sequence,
                change_log_id=item.change_log_id,
                descriptor=descriptor,
            )
            planned.append(require_object_delta_mvp_receiver_planned_change(planned_change))
        except (KeyError, ObjectDeltaReceiverMvpHandlerError, ValueError) as exc:
            raise ObjectDeltaImportPlanError(
                "commodities payload does not match the exact receiver handler contract"
            ) from exc
    return tuple(planned)


def _validate_cursor_identity(cursor: ReceiverStreamCursor, batch: AppendOnlySyncDeltaBatch) -> None:
    if not isinstance(cursor, ReceiverStreamCursor):
        raise ObjectDeltaImportPlanError("receiver cursor is invalid")
    if (
        cursor.source_site,
        cursor.destination_site,
        cursor.campaign_id,
        cursor.release_sha,
        cursor.stream_generation_id,
    ) != (
        batch.source_site,
        batch.destination_site,
        batch.campaign_id,
        batch.release_sha,
        batch.stream.generation_id,
    ):
        raise ObjectDeltaImportPlanError("receiver cursor does not match the batch stream")
    _require_positive_int(cursor.last_sequence, label="receiver cursor last sequence")
    _require_sha256(cursor.last_batch_sha256, label="receiver cursor last batch hash")


def _coalesce_existing_receipt(
    *,
    expected: ObjectDeltaImportReceipt,
    receipt_by_object: ObjectDeltaImportReceipt | None,
    receipt_by_stream: ObjectDeltaImportReceipt | None,
) -> ObjectDeltaImportReceipt | None:
    if receipt_by_object is not None and not isinstance(receipt_by_object, ObjectDeltaImportReceipt):
        raise ObjectDeltaImportPlanError("object-version receipt is invalid")
    if receipt_by_stream is not None and not isinstance(receipt_by_stream, ObjectDeltaImportReceipt):
        raise ObjectDeltaImportPlanError("stream receipt is invalid")
    if receipt_by_object is not None and receipt_by_stream is not None and receipt_by_object != receipt_by_stream:
        raise ObjectDeltaImportPlanError("immutable object and logical stream receipt lookups disagree")
    existing = receipt_by_object if receipt_by_object is not None else receipt_by_stream
    if existing is not None and existing != expected:
        raise ObjectDeltaImportPlanError("existing immutable receipt conflicts with the batch")
    return existing


def _validate_fresh_chain(
    *, batch: AppendOnlySyncDeltaBatch, cursor: ReceiverStreamCursor | None
) -> ReceiverStreamCursor:
    if cursor is None:
        if (
            batch.stream.first_sequence != 1
            or batch.prior_chain_sha256 != GENESIS_PRIOR_CHAIN_SHA256
        ):
            raise ObjectDeltaImportPlanError(
                "a new receiver stream must begin at genesis sequence one"
            )
    else:
        _validate_cursor_identity(cursor, batch)
        if batch.stream.first_sequence != cursor.last_sequence + 1:
            raise ObjectDeltaImportPlanError("delta batch is not the next logical receiver sequence")
        if batch.prior_chain_sha256 != cursor.last_batch_sha256:
            raise ObjectDeltaImportPlanError("delta batch predecessor does not match the receiver cursor")
    return ReceiverStreamCursor(
        source_site=batch.source_site,
        destination_site=batch.destination_site,
        campaign_id=batch.campaign_id,
        release_sha=batch.release_sha,
        stream_generation_id=batch.stream.generation_id,
        last_sequence=batch.stream.last_sequence,
        last_batch_sha256=batch.batch_sha256,
    )


def _validate_replay_cursor(
    *, batch: AppendOnlySyncDeltaBatch, cursor: ReceiverStreamCursor | None
) -> None:
    if cursor is None:
        raise ObjectDeltaImportPlanError("existing immutable receipt has no receiver cursor")
    _validate_cursor_identity(cursor, batch)
    if cursor.last_sequence < batch.stream.last_sequence:
        raise ObjectDeltaImportPlanError("existing immutable receipt is ahead of the receiver cursor")
    if (
        cursor.last_sequence == batch.stream.last_sequence
        and cursor.last_batch_sha256 != batch.batch_sha256
    ):
        raise ObjectDeltaImportPlanError("receiver cursor terminal batch conflicts with immutable receipt")


def plan_atomic_object_delta_import(
    *,
    batch: AppendOnlySyncDeltaBatch,
    payload: NormalizedObjectDeltaPayload,
    local_site: str,
    expected_source_site: str,
    expected_campaign_id: str,
    expected_release_sha: str,
    expected_stream_generation_id: str,
    expected_writer_epoch: int,
    expected_writer_lease_id: str,
    expected_registry_fingerprint: str,
    receiver_cursor: ReceiverStreamCursor | None,
    receipt_by_object: ObjectDeltaImportReceipt | None,
    receipt_by_stream: ObjectDeltaImportReceipt | None,
) -> AtomicObjectDeltaImportPlan:
    """Return an apply or idempotent-replay decision without any I/O.

    The caller must already have verified the signed, fixed-bucket, age-
    authenticated transport bytes against ``batch`` and ``payload``.
    ``receiver_cursor`` and both receipt lookups must come from one database
    transaction after the caller acquired the stream-scoped advisory lock and
    selected the rows ``FOR UPDATE``.  Passing unlocked or incomplete lookup
    state is a caller bug; this pure function cannot query the database to
    repair it.

    Fresh batches require exact next logical sequence and predecessor hash.
    A generation change is never auto-adopted: the receiver must receive the
    exact expected generation from its authenticated control plane.  A cursor
    lookup alone is insufficient because a new generation would otherwise
    look like an empty stream and could restart at sequence one.  Replays
    apply *zero* changes and perform no writes.
    """

    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaImportPlanError("validated delta batch is required")
    _require_expected_binding(local_site, actual=batch.destination_site, label="destination site")
    _require_expected_binding(
        expected_source_site, actual=batch.source_site, label="source site"
    )
    _require_site(local_site, label="local site")
    _require_site(expected_source_site, label="expected source site")
    _require_expected_binding(
        expected_campaign_id, actual=batch.campaign_id, label="campaign"
    )
    _require_expected_binding(expected_release_sha, actual=batch.release_sha, label="release")
    _require_expected_binding(
        expected_stream_generation_id,
        actual=batch.stream.generation_id,
        label="stream generation",
    )
    if _require_positive_int(expected_writer_epoch, label="expected writer epoch") != batch.writer_term.epoch:
        raise ObjectDeltaImportPlanError("delta batch writer epoch does not match receiver binding")
    if not isinstance(expected_writer_lease_id, str) or expected_writer_lease_id != batch.writer_term.lease_id:
        raise ObjectDeltaImportPlanError("delta batch writer lease does not match receiver binding")
    registry_fingerprint = _require_registry_fingerprint(expected_registry_fingerprint)

    changes = _validate_payload_binding(
        batch=batch,
        payload=payload,
        expected_source_server=SOURCE_SERVER_BY_SITE[batch.source_site],
        expected_registry_fingerprint=registry_fingerprint,
    )
    expected_receipt = expected_import_receipt(batch)
    existing_receipt = _coalesce_existing_receipt(
        expected=expected_receipt,
        receipt_by_object=receipt_by_object,
        receipt_by_stream=receipt_by_stream,
    )
    if existing_receipt is not None:
        _validate_replay_cursor(batch=batch, cursor=receiver_cursor)
        return AtomicObjectDeltaImportPlan(
            action=IMPORT_ACTION_REPLAY,
            receipt_to_insert=None,
            cursor_to_write=None,
            changes_to_apply=(),
        )

    next_cursor = _validate_fresh_chain(batch=batch, cursor=receiver_cursor)
    return AtomicObjectDeltaImportPlan(
        action=IMPORT_ACTION_APPLY,
        receipt_to_insert=expected_receipt,
        cursor_to_write=next_cursor,
        changes_to_apply=changes,
    )
