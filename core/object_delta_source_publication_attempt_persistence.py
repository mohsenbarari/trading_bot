"""Caller-owned durable transitions for source Object-delta publication attempts.

This module is deliberately *not* a publisher.  It has no filesystem/spool
I/O, encryption, Object Storage, signing, credentials, network, worker, or
runtime-enable behaviour.  A caller owns the surrounding SQLAlchemy
transaction and must commit or roll it back itself.

It persists the immutable state-machine facts from
``core.object_delta_source_publication_attempt`` in this order:

``reservation -> spool seal -> exact Object receipt -> signed attestation -> ledger binding``.

The two reservation identities (deterministic ``attempt_id`` and immutable
Object key) are both advisory-serialized and loaded ``FOR UPDATE`` before a
reserve/replay decision.  Every later transition repeats those locks and
loads stage rows in the same order.  This makes a missing, conflicting, or
out-of-order durable fact fail closed rather than being repaired by a fresh
encryption or Object write.

The sole public pre-upload transition is an opaque coordinator-to-persistence
seam. It accepts neither a raw intent nor a raw attempt. A root-only,
default-off coordinator must first combine the pre-upload gate's locked
snapshot with a fresh live Writer Witness validation, then mint the separate
non-public persistence authority consumed here. The three lower-level
pre-upload transition helpers remain private test-contract mechanics.

The former public attestation and terminal-ledger boundaries are hard-disabled:
their old capability did not prove a locked source snapshot or fresh live
Writer Witness authority. They retain only explicitly named private
test-contract implementations. No function in this module authorizes an
encryption, storage, network, worker, or runtime-publication action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json

from sqlalchemy import func, select

from core.append_only_sync_delta_batch import sha256_bytes
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
    SOURCE_BATCH_APPEND_ACTION_REPLAY,
    ObjectDeltaSourceLedgerError,
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
)
from core.object_delta_source_cutover_publication_gate import (
    AuthorizedObjectDeltaSourceAttestation,
    ObjectDeltaSourceCutoverPublicationGateError,
    _legacy_test_only_authorized_object_delta_source_batch_attestation_artifact,
    _legacy_test_only_authorized_object_delta_source_ledger_entry,
    _legacy_test_only_require_authorized_object_delta_source_batch_attestation,
    _legacy_test_only_require_authorized_object_delta_source_cutover_batch,
)
from core.object_delta_source_ledger_persistence import (
    ObjectDeltaSourceLedgerPersistenceError,
    ObjectDeltaSourceLedgerPersistenceResult,
    _legacy_test_only_persist_prepared_object_delta_source_batch_ledger,
)
from core.object_delta_source_publication_attempt import (
    SOURCE_PUBLICATION_ATTESTATION_ACTION_RECORD,
    SOURCE_PUBLICATION_CIPHERTEXT_ACTION_SEAL,
    SOURCE_PUBLICATION_LEDGER_ACTION_APPEND,
    SOURCE_PUBLICATION_LEDGER_ACTION_REPLAY,
    SOURCE_PUBLICATION_UPLOAD_ACTION_RECORD,
    ObjectDeltaSourcePublicationAttempt as PublicationAttempt,
    ObjectDeltaSourcePublicationAttemptError,
    ObjectDeltaSourcePublicationAttestationArtifact,
    ObjectDeltaSourcePublicationAttestedAttempt,
    ObjectDeltaSourcePublicationCiphertextSpool,
    ObjectDeltaSourcePublicationExactReceipt,
    ObjectDeltaSourcePublicationIntent,
    ObjectDeltaSourcePublicationLedgeredAttempt,
    ObjectDeltaSourcePublicationSealedAttempt,
    ObjectDeltaSourcePublicationState,
    ObjectDeltaSourcePublicationUploadedAttempt,
    build_object_delta_source_publication_attempt,
    derive_object_delta_source_transport_policy_sha256,
    plan_object_delta_source_publication_attempt,
    plan_object_delta_source_publication_attestation,
    plan_object_delta_source_publication_exact_upload,
    plan_object_delta_source_publication_ledger,
    plan_object_delta_source_publication_seal,
)
from core.application_writer_term import ValidatedWriterTerm
from core.object_delta_source_preupload_authorization import (
    AuthorizedObjectDeltaSourcePreupload,
    ObjectDeltaSourcePreuploadAuthorizationError,
    project_authorized_object_delta_source_preupload_attempt,
    require_authorized_object_delta_source_preupload,
)
from models.object_delta import ObjectDeltaSourceCutover, ObjectDeltaStream
from models.object_delta_source_batch import ObjectDeltaSourceBatchLedger
from models.object_delta_source_publication_attempt import (
    ObjectDeltaSourcePublicationAttempt as PublicationAttemptRow,
    ObjectDeltaSourcePublicationAttestation as PublicationAttestationRow,
    ObjectDeltaSourcePublicationLedgerBinding as PublicationLedgerBindingRow,
    ObjectDeltaSourcePublicationReceipt as PublicationReceiptRow,
    ObjectDeltaSourcePublicationSeal as PublicationSealRow,
)


class ObjectDeltaSourcePublicationAttemptPersistenceError(RuntimeError):
    """A caller-owned publication-attempt transaction cannot proceed safely."""


_AUTHORIZED_SOURCE_PREUPLOAD_RESERVATION_CAPABILITY = object()


REQUIRED_OBJECT_DELTA_SOURCE_PREUPLOAD_RESERVATION_AUTHORIZATION = (
    "a non-public root-only coordinator authority combining the release-pinned source runtime binding and an already verified source pin",
    "raw canonical signed source-cutover evidence verified against that pin, including the nested baseline manifest and exact canonical artifact hash/byte count",
    "a locked durable baseline_published source cutover matching the same stream identity, registry fingerprint, Writer Witness epoch/lease, and immutable baseline receipts",
    "a fresh live Writer Witness validation for the same term, performed immediately before this persistence seam by the root-only coordinator",
    "a locked contiguous outbox selection and source-ledger frontier that bind prior-chain hash, first/last sequence, canonical payload hash/bytes, deterministic Object key, and destination recipient",
    "a private persistence capability whose exact immutable intent is the only value permitted to call the pre-upload reserve, seal, and exact-receipt persistence helpers",
)
"""Requirements bound into the coordinator-to-persistence capability.

Its minting helper is private to the root-only coordinator. A plain dataclass,
raw intent, caller-supplied term, or boolean is a weaker replacement for the
locked snapshot plus live Writer Witness boundary and is rejected by design.
"""


__all__ = (
    "AuthorizedObjectDeltaSourcePreuploadReservation",
    "LegacyObjectDeltaSourcePublicationDisabledError",
    "ObjectDeltaSourcePublicationAttemptPersistenceError",
    "ObjectDeltaSourcePublicationAttemptPersistenceResult",
    "REQUIRED_OBJECT_DELTA_SOURCE_PREUPLOAD_RESERVATION_AUTHORIZATION",
    "reserve_authorized_object_delta_source_preupload_attempt",
    "source_publication_attempt_advisory_lock_keys",
)


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """One immutable transition/replay and all locked durable evidence rows."""

    action: str
    state: ObjectDeltaSourcePublicationState
    attempt_row: PublicationAttemptRow
    seal_row: PublicationSealRow | None
    receipt_row: PublicationReceiptRow | None
    attestation_row: PublicationAttestationRow | None
    ledger_binding_row: PublicationLedgerBindingRow | None
    ledger_row: ObjectDeltaSourceBatchLedger | None


@dataclass(frozen=True)
class AuthorizedObjectDeltaSourcePreuploadReservation:
    """Opaque coordinator-to-persistence authority for one exact reservation.

    This is not a transport or storage capability. Its constructor cannot
    create a usable value: the private coordinator minting helper binds it to
    an already revalidated locked pre-upload authorization and the exact live
    Writer Witness term that was checked immediately before the durable
    reservation call.
    """

    authorization: AuthorizedObjectDeltaSourcePreupload
    attempt: PublicationAttempt
    writer_term: ValidatedWriterTerm
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class _LockedAttemptRows:
    """Rows loaded in fixed phase order under one attempt-identity lock set."""

    attempt_row: PublicationAttemptRow | None
    object_key_row: PublicationAttemptRow | None
    seal_row: PublicationSealRow | None
    receipt_row: PublicationReceiptRow | None
    attestation_row: PublicationAttestationRow | None
    ledger_binding_row: PublicationLedgerBindingRow | None
    ledger_row: ObjectDeltaSourceBatchLedger | None


@dataclass(frozen=True)
class _AuthorizedSourcePublicationFacts:
    """Facts extracted only after opaque source-gate revalidation."""

    attempt: PublicationAttempt
    attestation: ObjectDeltaSourcePublicationAttestationArtifact
    ledger_entry: SourceBatchLedgerEntry
    prepared: object
    expected_registry_fingerprint: str


def _session_has_active_transaction(session: object) -> bool:
    probe = getattr(session, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def _require_active_transaction(session: object) -> None:
    if not _session_has_active_transaction(session):
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication-attempt persistence requires an active caller-owned transaction"
        )


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
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "Object-delta source publication stream identity is invalid"
        ) from exc


def _advisory_lock_key(*, kind: str, value: str) -> int:
    """Produce a stable signed bigint for one uniqueness namespace/value."""

    digest = hashlib.sha256(
        json.dumps(
            {
                "namespace": "gold-trade-object-delta-source-publication-attempt-persistence-v1",
                "kind": kind,
                "value": value,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def source_publication_attempt_advisory_lock_keys(
    attempt: PublicationAttempt,
) -> tuple[int, int]:
    """Return sorted locks for the attempt-ID and Object-key uniqueness domains.

    The function is public for transaction tests and must remain independent
    of SQLAlchemy/session state.  It does not confer publication authority.
    """

    try:
        normalized = PublicationAttempt(intent=attempt.intent, attempt_id=attempt.attempt_id)
    except (AttributeError, TypeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "Object-delta source publication attempt is invalid"
        ) from exc
    return tuple(
        sorted(
            (
                _advisory_lock_key(kind="attempt_id", value=normalized.attempt_id),
                _advisory_lock_key(kind="object_key", value=normalized.intent.object_key),
            )
        )
    )


async def _scalar_one_or_none(session: object, statement: object, *, label: str):
    try:
        result = await session.execute(statement)
        return result.scalar_one_or_none()
    except Exception as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            f"Object-delta source publication {label} query failed"
        ) from exc


async def _lock_stream_advisory(session: object, stream: SourceStreamIdentity) -> None:
    await _scalar_one_or_none(
        session,
        select(
            func.pg_advisory_xact_lock(
                stream_advisory_lock_key(_stream_identity_for_advisory_lock(stream))
            )
        ),
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


def _require_matching_stream(
    row: ObjectDeltaStream | object,
    *,
    expected: SourceStreamIdentity,
) -> ObjectDeltaStream:
    if not isinstance(row, ObjectDeltaStream) or type(row.id) is not int or row.id < 1:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
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
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked Object-delta source stream is invalid"
        ) from exc
    if actual != expected:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked Object-delta source stream does not match the publication attempt"
        )
    return row


async def _lock_attempt_identity_advisories(
    session: object,
    *,
    attempt: PublicationAttempt,
) -> None:
    for key in source_publication_attempt_advisory_lock_keys(attempt):
        await _scalar_one_or_none(
            session,
            select(func.pg_advisory_xact_lock(key)),
            label="attempt identity advisory lock",
        )


async def _load_attempt_id_for_update(
    session: object,
    *,
    attempt_id: str,
) -> PublicationAttemptRow | None:
    return await _scalar_one_or_none(
        session,
        select(PublicationAttemptRow)
        .where(PublicationAttemptRow.attempt_id == attempt_id)
        .with_for_update(),
        label="attempt-ID reservation lock",
    )


async def _load_object_key_for_update(
    session: object,
    *,
    object_key: str,
) -> PublicationAttemptRow | None:
    return await _scalar_one_or_none(
        session,
        select(PublicationAttemptRow)
        .where(PublicationAttemptRow.object_key == object_key)
        .with_for_update(),
        label="Object-key reservation lock",
    )


async def _load_seal_for_update(
    session: object,
    *,
    attempt_id: str,
) -> PublicationSealRow | None:
    return await _scalar_one_or_none(
        session,
        select(PublicationSealRow)
        .where(PublicationSealRow.attempt_id == attempt_id)
        .with_for_update(),
        label="seal lock",
    )


async def _load_receipt_for_update(
    session: object,
    *,
    attempt_id: str,
) -> PublicationReceiptRow | None:
    return await _scalar_one_or_none(
        session,
        select(PublicationReceiptRow)
        .where(PublicationReceiptRow.attempt_id == attempt_id)
        .with_for_update(),
        label="receipt lock",
    )


async def _load_attestation_for_update(
    session: object,
    *,
    attempt_id: str,
) -> PublicationAttestationRow | None:
    return await _scalar_one_or_none(
        session,
        select(PublicationAttestationRow)
        .where(PublicationAttestationRow.attempt_id == attempt_id)
        .with_for_update(),
        label="attestation lock",
    )


async def _load_ledger_binding_for_update(
    session: object,
    *,
    attempt_id: str,
) -> PublicationLedgerBindingRow | None:
    return await _scalar_one_or_none(
        session,
        select(PublicationLedgerBindingRow)
        .where(PublicationLedgerBindingRow.attempt_id == attempt_id)
        .with_for_update(),
        label="ledger-binding lock",
    )


async def _load_bound_ledger_for_update(
    session: object,
    *,
    binding: PublicationLedgerBindingRow,
) -> ObjectDeltaSourceBatchLedger | None:
    if not isinstance(binding, PublicationLedgerBindingRow) or type(binding.source_batch_ledger_id) is not int:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication ledger binding is invalid"
        )
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaSourceBatchLedger)
        .where(ObjectDeltaSourceBatchLedger.id == binding.source_batch_ledger_id)
        .with_for_update(),
        label="bound source-ledger lock",
    )


async def _load_locked_attempt_rows(
    session: object,
    *,
    attempt: PublicationAttempt,
) -> _LockedAttemptRows:
    """Lock both reservation identities, then all phase rows in fixed order."""

    await _lock_attempt_identity_advisories(session, attempt=attempt)
    attempt_row = await _load_attempt_id_for_update(session, attempt_id=attempt.attempt_id)
    object_key_row = await _load_object_key_for_update(
        session,
        object_key=attempt.intent.object_key,
    )
    # Do not query descendants without a parent: the pure planner needs the
    # two independent reservation lookup facts first, and a child without a
    # parent is already an invariant failure.
    if attempt_row is None and object_key_row is None:
        return _LockedAttemptRows(
            attempt_row=None,
            object_key_row=None,
            seal_row=None,
            receipt_row=None,
            attestation_row=None,
            ledger_binding_row=None,
            ledger_row=None,
        )
    anchor = attempt_row or object_key_row
    if anchor is None:  # pragma: no cover - retained for type narrowing.
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication reservation lock is invalid"
        )
    seal_row = await _load_seal_for_update(session, attempt_id=anchor.attempt_id)
    receipt_row = await _load_receipt_for_update(session, attempt_id=anchor.attempt_id)
    attestation_row = await _load_attestation_for_update(session, attempt_id=anchor.attempt_id)
    binding_row = await _load_ledger_binding_for_update(session, attempt_id=anchor.attempt_id)
    ledger_row = (
        await _load_bound_ledger_for_update(session, binding=binding_row)
        if binding_row is not None
        else None
    )
    return _LockedAttemptRows(
        attempt_row=attempt_row,
        object_key_row=object_key_row,
        seal_row=seal_row,
        receipt_row=receipt_row,
        attestation_row=attestation_row,
        ledger_binding_row=binding_row,
        ledger_row=ledger_row,
    )


def _attempt_from_row(row: PublicationAttemptRow | object) -> PublicationAttempt:
    if not isinstance(row, PublicationAttemptRow) or type(row.stream_id) is not int or row.stream_id < 1:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication reservation is invalid"
        )
    try:
        intent = ObjectDeltaSourcePublicationIntent(
            stream=SourceStreamIdentity(
                source_site=row.source_site,
                destination_site=row.destination_site,
                campaign_id=row.campaign_id,
                release_sha=row.release_sha,
                stream_generation_id=row.stream_generation_id,
            ),
            writer_epoch=row.writer_epoch,
            writer_lease_id=row.writer_lease_id,
            first_sequence=row.first_sequence,
            last_sequence=row.last_sequence,
            prior_chain_sha256=row.prior_chain_sha256,
            payload_sha256=row.payload_sha256,
            payload_bytes=row.payload_bytes,
            object_key=row.object_key,
            destination_age_recipient=row.destination_age_recipient,
            transport_policy_sha256=row.transport_policy_sha256,
            source_cutover_artifact_sha256=row.source_cutover_artifact_sha256,
            source_cutover_artifact_bytes=row.source_cutover_artifact_bytes,
        )
        return PublicationAttempt(intent=intent, attempt_id=row.attempt_id)
    except (
        AttributeError,
        TypeError,
        ObjectDeltaSourceLedgerError,
        ObjectDeltaSourcePublicationAttemptError,
    ) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication reservation is invalid"
        ) from exc


def _require_matching_row_attempt(
    row: PublicationAttemptRow | object,
    *,
    expected: PublicationAttempt,
    stream: ObjectDeltaStream,
) -> PublicationAttempt:
    actual = _attempt_from_row(row)
    if actual != expected or getattr(row, "stream_id", None) != stream.id:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication reservation conflicts with the requested attempt"
        )
    return actual


def _sealed_from_row(
    row: PublicationSealRow | object,
    *,
    attempt: PublicationAttempt,
) -> ObjectDeltaSourcePublicationSealedAttempt:
    if not isinstance(row, PublicationSealRow) or row.attempt_id != attempt.attempt_id:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication seal is invalid"
        )
    try:
        return ObjectDeltaSourcePublicationSealedAttempt(
            attempt=attempt,
            ciphertext=ObjectDeltaSourcePublicationCiphertextSpool(
                attempt_id=row.attempt_id,
                ciphertext_sha256=row.ciphertext_sha256,
                ciphertext_bytes=row.ciphertext_bytes,
                spool_sha256=row.spool_sha256,
                spool_bytes=row.spool_bytes,
            ),
        )
    except (AttributeError, TypeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication seal is invalid"
        ) from exc


def _uploaded_from_rows(
    *,
    sealed: ObjectDeltaSourcePublicationSealedAttempt,
    row: PublicationReceiptRow | object,
) -> ObjectDeltaSourcePublicationUploadedAttempt:
    if not isinstance(row, PublicationReceiptRow) or row.attempt_id != sealed.attempt.attempt_id:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication receipt is invalid"
        )
    try:
        return ObjectDeltaSourcePublicationUploadedAttempt(
            sealed=sealed,
            receipt=ObjectDeltaSourcePublicationExactReceipt(
                attempt_id=row.attempt_id,
                object_key=row.object_key,
                object_version_id=row.object_version_id,
                ciphertext_sha256=row.ciphertext_sha256,
                ciphertext_bytes=row.ciphertext_bytes,
                transport_receipt_artifact_sha256=row.transport_receipt_artifact_sha256,
                transport_receipt_artifact_bytes=row.transport_receipt_artifact_bytes,
            ),
        )
    except (AttributeError, TypeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication receipt is invalid"
        ) from exc


def _attested_from_rows(
    *,
    uploaded: ObjectDeltaSourcePublicationUploadedAttempt,
    row: PublicationAttestationRow | object,
) -> ObjectDeltaSourcePublicationAttestedAttempt:
    if not isinstance(row, PublicationAttestationRow) or row.attempt_id != uploaded.sealed.attempt.attempt_id:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication attestation is invalid"
        )
    try:
        return ObjectDeltaSourcePublicationAttestedAttempt(
            uploaded=uploaded,
            attestation=ObjectDeltaSourcePublicationAttestationArtifact(
                attempt_id=row.attempt_id,
                source_key_id=row.source_key_id,
                batch_sha256=row.batch_sha256,
                source_attestation_artifact_sha256=row.source_attestation_artifact_sha256,
                source_attestation_artifact_bytes=row.source_attestation_artifact_bytes,
            ),
        )
    except (AttributeError, TypeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication attestation is invalid"
        ) from exc


def _ledger_entry_from_row(
    row: ObjectDeltaSourceBatchLedger | object,
    *,
    stream: ObjectDeltaStream,
    identity: SourceStreamIdentity,
) -> SourceBatchLedgerEntry:
    if not isinstance(row, ObjectDeltaSourceBatchLedger) or row.stream_id != stream.id:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication ledger row is invalid"
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
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source publication ledger row is invalid"
        ) from exc


def _state_from_locked_rows(
    rows: _LockedAttemptRows,
    *,
    expected: PublicationAttempt,
    stream: ObjectDeltaStream,
) -> ObjectDeltaSourcePublicationState | None:
    """Reconstruct only a contiguous immutable phase chain; otherwise block."""

    if rows.attempt_row is None and rows.object_key_row is None:
        return None
    if rows.attempt_row is None or rows.object_key_row is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication attempt-ID and Object-key reservations disagree"
        )
    attempt = _require_matching_row_attempt(rows.attempt_row, expected=expected, stream=stream)
    object_key_attempt = _require_matching_row_attempt(
        rows.object_key_row,
        expected=expected,
        stream=stream,
    )
    if object_key_attempt != attempt:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication attempt-ID and Object-key reservations disagree"
        )
    if rows.seal_row is None:
        if any(
            value is not None
            for value in (
                rows.receipt_row,
                rows.attestation_row,
                rows.ledger_binding_row,
                rows.ledger_row,
            )
        ):
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source publication durable phases are out of order"
            )
        return attempt
    sealed = _sealed_from_row(rows.seal_row, attempt=attempt)
    if rows.receipt_row is None:
        if any(value is not None for value in (rows.attestation_row, rows.ledger_binding_row, rows.ledger_row)):
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source publication durable phases are out of order"
            )
        return sealed
    uploaded = _uploaded_from_rows(sealed=sealed, row=rows.receipt_row)
    if rows.attestation_row is None:
        if any(value is not None for value in (rows.ledger_binding_row, rows.ledger_row)):
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source publication durable phases are out of order"
            )
        return uploaded
    attested = _attested_from_rows(uploaded=uploaded, row=rows.attestation_row)
    if rows.ledger_binding_row is None:
        if rows.ledger_row is not None:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source publication ledger row is present without a terminal binding"
            )
        return attested
    if rows.ledger_binding_row.attempt_id != attempt.attempt_id or rows.ledger_row is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication terminal ledger binding is invalid"
        )
    ledger_entry = _ledger_entry_from_row(
        rows.ledger_row,
        stream=stream,
        identity=attempt.intent.stream,
    )
    try:
        return ObjectDeltaSourcePublicationLedgeredAttempt(
            attested=attested,
            ledger_entry=ledger_entry,
        )
    except ObjectDeltaSourcePublicationAttemptError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication terminal ledger binding is invalid"
        ) from exc


def _reservation_row_from_attempt(
    *,
    attempt: PublicationAttempt,
    stream_id: int,
) -> PublicationAttemptRow:
    if type(stream_id) is not int or stream_id < 1:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked source stream identifier is invalid"
        )
    value = attempt.intent
    return PublicationAttemptRow(
        attempt_id=attempt.attempt_id,
        stream_id=stream_id,
        source_site=value.stream.source_site,
        destination_site=value.stream.destination_site,
        campaign_id=value.stream.campaign_id,
        release_sha=value.stream.release_sha,
        stream_generation_id=value.stream.stream_generation_id,
        writer_epoch=value.writer_epoch,
        writer_lease_id=value.writer_lease_id,
        first_sequence=value.first_sequence,
        last_sequence=value.last_sequence,
        prior_chain_sha256=value.prior_chain_sha256,
        payload_sha256=value.payload_sha256,
        payload_bytes=value.payload_bytes,
        object_key=value.object_key,
        destination_age_recipient=value.destination_age_recipient,
        transport_policy_sha256=value.transport_policy_sha256,
        source_cutover_artifact_sha256=value.source_cutover_artifact_sha256,
        source_cutover_artifact_bytes=value.source_cutover_artifact_bytes,
    )


def _seal_row_from_state(state: ObjectDeltaSourcePublicationSealedAttempt) -> PublicationSealRow:
    value = state.ciphertext
    return PublicationSealRow(
        attempt_id=state.attempt.attempt_id,
        ciphertext_sha256=value.ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        spool_sha256=value.spool_sha256,
        spool_bytes=value.spool_bytes,
    )


def _receipt_row_from_state(state: ObjectDeltaSourcePublicationUploadedAttempt) -> PublicationReceiptRow:
    value = state.receipt
    return PublicationReceiptRow(
        attempt_id=value.attempt_id,
        object_key=value.object_key,
        object_version_id=value.object_version_id,
        ciphertext_sha256=value.ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        transport_receipt_artifact_sha256=value.transport_receipt_artifact_sha256,
        transport_receipt_artifact_bytes=value.transport_receipt_artifact_bytes,
    )


def _attestation_row_from_state(state: ObjectDeltaSourcePublicationAttestedAttempt) -> PublicationAttestationRow:
    value = state.attestation
    return PublicationAttestationRow(
        attempt_id=value.attempt_id,
        source_key_id=value.source_key_id,
        batch_sha256=value.batch_sha256,
        source_attestation_artifact_sha256=value.source_attestation_artifact_sha256,
        source_attestation_artifact_bytes=value.source_attestation_artifact_bytes,
    )


async def _flush_insert(session: object, row: object, *, label: str) -> None:
    try:
        session.add(row)
        await session.flush()
    except Exception as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            f"source publication {label} insert failed"
        ) from exc


async def _lock_stream_and_rows(
    session: object,
    *,
    attempt: PublicationAttempt,
) -> tuple[ObjectDeltaStream, _LockedAttemptRows]:
    await _lock_stream_advisory(session, attempt.intent.stream)
    stream = await _load_stream_for_update(session, stream=attempt.intent.stream)
    if stream is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication stream does not exist"
        )
    stream = _require_matching_stream(stream, expected=attempt.intent.stream)
    rows = await _load_locked_attempt_rows(session, attempt=attempt)
    return stream, rows


def _result(
    *,
    action: str,
    state: ObjectDeltaSourcePublicationState,
    rows: _LockedAttemptRows,
    attempt_row: PublicationAttemptRow,
    seal_row: PublicationSealRow | None = None,
    receipt_row: PublicationReceiptRow | None = None,
    attestation_row: PublicationAttestationRow | None = None,
    ledger_binding_row: PublicationLedgerBindingRow | None = None,
    ledger_row: ObjectDeltaSourceBatchLedger | None = None,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    return ObjectDeltaSourcePublicationAttemptPersistenceResult(
        action=action,
        state=state,
        attempt_row=attempt_row,
        seal_row=seal_row if seal_row is not None else rows.seal_row,
        receipt_row=receipt_row if receipt_row is not None else rows.receipt_row,
        attestation_row=attestation_row if attestation_row is not None else rows.attestation_row,
        ledger_binding_row=(
            ledger_binding_row if ledger_binding_row is not None else rows.ledger_binding_row
        ),
        ledger_row=ledger_row if ledger_row is not None else rows.ledger_row,
    )


def _normalized_attempt(value: object) -> PublicationAttempt:
    if not isinstance(value, PublicationAttempt):
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "Object-delta source publication attempt is invalid"
        )
    try:
        return PublicationAttempt(intent=value.intent, attempt_id=value.attempt_id)
    except (AttributeError, TypeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "Object-delta source publication attempt is invalid"
        ) from exc


def _validated_authorized_object_delta_source_preupload_reservation(
    value: object,
) -> PublicationAttempt:
    """Recover the exact attempt only from a coordinator-minted capability."""

    if type(value) is not AuthorizedObjectDeltaSourcePreuploadReservation:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation capability is required"
        )
    if value._capability is not _AUTHORIZED_SOURCE_PREUPLOAD_RESERVATION_CAPABILITY:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation capability was not verified"
        )
    try:
        authorization = require_authorized_object_delta_source_preupload(value.authorization)
        attempt = project_authorized_object_delta_source_preupload_attempt(authorization)
    except ObjectDeltaSourcePreuploadAuthorizationError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation has invalid locked evidence"
        ) from exc
    if type(value.attempt) is not PublicationAttempt or value.attempt != attempt:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation attempt does not match locked evidence"
        )
    if type(value.writer_term) is not ValidatedWriterTerm:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation Writer Witness term is invalid"
        )
    try:
        expected = (
            authorization.pin.binding.source_site,
            attempt.intent.writer_epoch,
            attempt.intent.writer_lease_id,
        )
        actual = (
            value.writer_term.holder_site,
            value.writer_term.writer_epoch,
            value.writer_term.lease_id,
        )
    except AttributeError as exc:  # pragma: no cover - exact type is checked above.
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation Writer Witness term is invalid"
        ) from exc
    if actual != expected:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation Writer Witness term does not match locked evidence"
        )
    return attempt


def _mint_authorized_object_delta_source_preupload_reservation(
    authorization: object,
    *,
    writer_term: ValidatedWriterTerm,
) -> AuthorizedObjectDeltaSourcePreuploadReservation:
    """Private bridge used only after the root coordinator reads a live term.

    The ``writer_term`` parameter is intentionally private to this module's
    coordinator bridge. The public persistence seam below never accepts a
    caller-supplied term, intent, or attempt.
    """

    try:
        verified = require_authorized_object_delta_source_preupload(authorization)
        attempt = project_authorized_object_delta_source_preupload_attempt(verified)
    except ObjectDeltaSourcePreuploadAuthorizationError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation has invalid locked evidence"
        ) from exc
    if type(writer_term) is not ValidatedWriterTerm:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation Writer Witness term is invalid"
        )
    if (
        writer_term.holder_site,
        writer_term.writer_epoch,
        writer_term.lease_id,
    ) != (
        verified.pin.binding.source_site,
        attempt.intent.writer_epoch,
        attempt.intent.writer_lease_id,
    ):
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source pre-upload reservation Writer Witness term does not match locked evidence"
        )
    result = AuthorizedObjectDeltaSourcePreuploadReservation(
        authorization=verified,
        attempt=attempt,
        writer_term=writer_term,
    )
    object.__setattr__(result, "_capability", _AUTHORIZED_SOURCE_PREUPLOAD_RESERVATION_CAPABILITY)
    _validated_authorized_object_delta_source_preupload_reservation(result)
    return result


def _sealed_from_state(
    state: ObjectDeltaSourcePublicationState,
) -> ObjectDeltaSourcePublicationSealedAttempt:
    if isinstance(state, ObjectDeltaSourcePublicationSealedAttempt):
        return state
    if isinstance(state, ObjectDeltaSourcePublicationUploadedAttempt):
        return state.sealed
    if isinstance(state, ObjectDeltaSourcePublicationAttestedAttempt):
        return state.uploaded.sealed
    if isinstance(state, ObjectDeltaSourcePublicationLedgeredAttempt):
        return state.attested.uploaded.sealed
    raise ObjectDeltaSourcePublicationAttemptPersistenceError(
        "source publication exact receipt requires a durable ciphertext seal"
    )


def _uploaded_from_state(
    state: ObjectDeltaSourcePublicationState,
) -> ObjectDeltaSourcePublicationUploadedAttempt:
    if isinstance(state, ObjectDeltaSourcePublicationUploadedAttempt):
        return state
    if isinstance(state, ObjectDeltaSourcePublicationAttestedAttempt):
        return state.uploaded
    if isinstance(state, ObjectDeltaSourcePublicationLedgeredAttempt):
        return state.attested.uploaded
    raise ObjectDeltaSourcePublicationAttemptPersistenceError(
        "authorized source attestation requires a durable exact receipt"
    )


async def _reserve_exact_object_delta_source_publication_attempt(
    session: object,
    *,
    attempt: PublicationAttempt,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Durably reserve/replay an already-authorized deterministic attempt."""

    _require_active_transaction(session)
    candidate = _normalized_attempt(attempt)
    stream, rows = await _lock_stream_and_rows(session, attempt=candidate)
    existing_state = _state_from_locked_rows(rows, expected=candidate, stream=stream)
    try:
        plan = plan_object_delta_source_publication_attempt(
            intent=candidate.intent,
            existing_state=existing_state,
            existing_object_key_state=existing_state,
        )
    except ObjectDeltaSourcePublicationAttemptError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication reservation conflicts with immutable evidence"
        ) from exc
    if plan.attempt_to_insert is None:
        if rows.attempt_row is None or existing_state is None:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source publication reservation replay row is missing"
            )
        return _result(
            action=plan.action,
            state=existing_state,
            rows=rows,
            attempt_row=rows.attempt_row,
        )
    inserted = _reservation_row_from_attempt(attempt=plan.attempt_to_insert, stream_id=stream.id)
    await _flush_insert(session, inserted, label="reservation")
    return _result(
        action=plan.action,
        state=plan.attempt_to_insert,
        rows=rows,
        attempt_row=inserted,
    )


async def reserve_authorized_object_delta_source_preupload_attempt(
    session: object,
    authorization: object,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Reserve/replay only a root-coordinator-minted pre-upload capability.

    The caller still owns the existing transaction and must commit or roll it
    back. This function neither opens nor ends a transaction, and it performs
    no encryption, spool, Object Storage, or network operation.
    """

    candidate = _validated_authorized_object_delta_source_preupload_reservation(authorization)
    return await _reserve_exact_object_delta_source_publication_attempt(
        session,
        attempt=candidate,
    )


async def _legacy_test_only_reserve_object_delta_source_publication_attempt(
    session: object,
    intent: ObjectDeltaSourcePublicationIntent,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Private test-contract reserve/replay mechanics for a raw intent."""

    try:
        candidate = build_object_delta_source_publication_attempt(intent)
    except (TypeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "Object-delta source publication intent is invalid"
        ) from exc
    return await _reserve_exact_object_delta_source_publication_attempt(
        session,
        attempt=candidate,
    )


async def _legacy_test_only_seal_object_delta_source_publication_attempt(
    session: object,
    *,
    attempt: PublicationAttempt,
    ciphertext: ObjectDeltaSourcePublicationCiphertextSpool,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Low-level sealed-spool mechanics; not a storage-action authorization."""

    _require_active_transaction(session)
    candidate = _normalized_attempt(attempt)
    stream, rows = await _lock_stream_and_rows(session, attempt=candidate)
    existing_state = _state_from_locked_rows(rows, expected=candidate, stream=stream)
    try:
        plan = plan_object_delta_source_publication_seal(
            attempt=candidate,
            ciphertext=ciphertext,
            existing_state=existing_state,
        )
    except ObjectDeltaSourcePublicationAttemptError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication ciphertext seal conflicts with immutable evidence"
        ) from exc
    if plan.sealed_attempt_to_write is None:
        if rows.attempt_row is None or existing_state is None:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source publication ciphertext-seal replay row is missing"
            )
        return _result(
            action=plan.action,
            state=existing_state,
            rows=rows,
            attempt_row=rows.attempt_row,
        )
    if rows.attempt_row is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication ciphertext-seal reservation row is missing"
        )
    inserted = _seal_row_from_state(plan.sealed_attempt_to_write)
    await _flush_insert(session, inserted, label="ciphertext seal")
    return _result(
        action=plan.action,
        state=plan.sealed_attempt_to_write,
        rows=rows,
        attempt_row=rows.attempt_row,
        seal_row=inserted,
    )


async def _legacy_test_only_record_object_delta_source_publication_exact_receipt(
    session: object,
    *,
    attempt: PublicationAttempt,
    receipt: ObjectDeltaSourcePublicationExactReceipt,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Low-level exact-receipt mechanics after a sealed spool.

    The caller must have performed the exact Object read-back first.  This
    adapter records only its normalized non-secret evidence and never opens
    Object Storage itself.  It remains private until the pre-upload authority
    can bind the earlier reservation and seal transitions.
    """

    _require_active_transaction(session)
    candidate = _normalized_attempt(attempt)
    stream, rows = await _lock_stream_and_rows(session, attempt=candidate)
    existing_state = _state_from_locked_rows(rows, expected=candidate, stream=stream)
    if existing_state is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication exact receipt has no durable reservation"
        )
    try:
        sealed = _sealed_from_state(existing_state)
        plan = plan_object_delta_source_publication_exact_upload(
            sealed_attempt=sealed,
            receipt=receipt,
            existing_state=existing_state,
        )
    except (AttributeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication exact receipt conflicts with immutable evidence"
        ) from exc
    if plan.uploaded_attempt_to_write is None:
        if rows.attempt_row is None:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source publication exact-receipt replay row is missing"
            )
        return _result(
            action=plan.action,
            state=existing_state,
            rows=rows,
            attempt_row=rows.attempt_row,
        )
    if rows.attempt_row is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication exact-receipt reservation row is missing"
        )
    inserted = _receipt_row_from_state(plan.uploaded_attempt_to_write)
    await _flush_insert(session, inserted, label="exact receipt")
    return _result(
        action=plan.action,
        state=plan.uploaded_attempt_to_write,
        rows=rows,
        attempt_row=rows.attempt_row,
        receipt_row=inserted,
    )


async def _load_source_cutover_for_update(
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


def _authorized_facts(
    authorization: object,
) -> _AuthorizedSourcePublicationFacts:
    """Revalidate opaque source authority and derive its exact durable intent.

    This is the only bridge to the low-level ledger adapter.  It never accepts
    a raw prepared batch from this module's public API.
    """

    try:
        verified = _legacy_test_only_require_authorized_object_delta_source_batch_attestation(
            authorization
        )
        # Revalidate the nested cutover capability separately before accessing
        # its public fields.  The gate performs signature/provenance/pin checks
        # on both accessors.
        cutover_authorization = _legacy_test_only_require_authorized_object_delta_source_cutover_batch(
            verified.batch_authorization
        )
        ledger_entry = _legacy_test_only_authorized_object_delta_source_ledger_entry(verified)
        artifact = _legacy_test_only_authorized_object_delta_source_batch_attestation_artifact(
            verified
        )
    except ObjectDeltaSourceCutoverPublicationGateError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source attestation capability is invalid"
        ) from exc
    if type(verified) is not AuthorizedObjectDeltaSourceAttestation:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source attestation capability is invalid"
        )
    try:
        raw_cutover = cutover_authorization.source_cutover_attestation
        prepared = cutover_authorization.prepared
        transport = prepared.transport_binding
        if (
            prepared.ledger_entry != ledger_entry
            or transport.object_key != ledger_entry.object_key
            or transport.destination_age_recipient is None
        ):
            raise ValueError("authorized source batch facts disagree")
        intent = ObjectDeltaSourcePublicationIntent(
            stream=ledger_entry.stream,
            writer_epoch=ledger_entry.writer_epoch,
            writer_lease_id=ledger_entry.writer_lease_id,
            first_sequence=ledger_entry.first_sequence,
            last_sequence=ledger_entry.last_sequence,
            prior_chain_sha256=ledger_entry.prior_chain_sha256,
            payload_sha256=ledger_entry.payload_sha256,
            payload_bytes=ledger_entry.payload_bytes,
            object_key=ledger_entry.object_key,
            destination_age_recipient=transport.destination_age_recipient,
            transport_policy_sha256=derive_object_delta_source_transport_policy_sha256(
                cutover_authorization.pin.transport_policy
            ),
            source_cutover_artifact_sha256=sha256_bytes(raw_cutover),
            source_cutover_artifact_bytes=len(raw_cutover),
        )
        attempt = build_object_delta_source_publication_attempt(intent)
        attestation = ObjectDeltaSourcePublicationAttestationArtifact(
            attempt_id=attempt.attempt_id,
            source_key_id=artifact.source_key_id,
            batch_sha256=artifact.batch_sha256,
            source_attestation_artifact_sha256=artifact.source_attestation_artifact_sha256,
            source_attestation_artifact_bytes=artifact.source_attestation_artifact_bytes,
        )
    except (
        AttributeError,
        TypeError,
        ValueError,
        ObjectDeltaSourcePublicationAttemptError,
    ) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source attestation does not yield a valid publication attempt"
        ) from exc
    return _AuthorizedSourcePublicationFacts(
        attempt=attempt,
        attestation=attestation,
        ledger_entry=ledger_entry,
        prepared=prepared,
        expected_registry_fingerprint=cutover_authorization.pin.binding.expected_registry_fingerprint,
    )


def _require_matching_locked_cutover(
    row: ObjectDeltaSourceCutover | object,
    *,
    stream: ObjectDeltaStream,
    facts: _AuthorizedSourcePublicationFacts,
) -> ObjectDeltaSourceCutover:
    if not isinstance(row, ObjectDeltaSourceCutover):
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source publication has no locked durable cutover"
        )
    intent = facts.attempt.intent
    expected_identity = (
        intent.stream.source_site,
        intent.stream.destination_site,
        intent.stream.campaign_id,
        intent.stream.release_sha,
        intent.stream.stream_generation_id,
    )
    actual_identity = (
        row.source_site,
        row.destination_site,
        row.campaign_id,
        row.release_sha,
        row.stream_generation_id,
    )
    if (
        row.stream_id != stream.id
        or actual_identity != expected_identity
        or row.state != "baseline_published"
        or row.registry_fingerprint != facts.expected_registry_fingerprint
        or (row.writer_epoch, row.writer_lease_id)
        != (intent.writer_epoch, intent.writer_lease_id)
    ):
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "locked durable source cutover does not match the authorized publication"
        )
    # The migration permits historical pending rows, but a normal publisher
    # must never treat a merely labelled row as a cutover.  Recheck the four
    # immutable baseline receipt fields while holding the row lock.
    for value in (
        row.snapshot_manifest_object_key,
        row.snapshot_manifest_object_version_id,
        row.snapshot_manifest_ciphertext_sha256,
        row.snapshot_manifest_ciphertext_bytes,
        row.baseline_manifest_object_key,
        row.baseline_manifest_object_version_id,
        row.baseline_manifest_ciphertext_sha256,
        row.baseline_manifest_ciphertext_bytes,
    ):
        if value is None:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "locked durable source cutover lacks complete baseline evidence"
            )
    return row


async def _lock_authorized_stream_rows(
    session: object,
    *,
    facts: _AuthorizedSourcePublicationFacts,
) -> tuple[ObjectDeltaStream, _LockedAttemptRows]:
    # Keep the global source-side order aligned with the allocator and ledger
    # adapter: stream advisory lock, stream row, published cutover, then this
    # attempt's dual reservation identities and phase rows.
    await _lock_stream_advisory(session, facts.attempt.intent.stream)
    stream = await _load_stream_for_update(session, stream=facts.attempt.intent.stream)
    if stream is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "source publication stream does not exist"
        )
    stream = _require_matching_stream(stream, expected=facts.attempt.intent.stream)
    cutover = await _load_source_cutover_for_update(session, stream_id=stream.id)
    _require_matching_locked_cutover(cutover, stream=stream, facts=facts)
    rows = await _load_locked_attempt_rows(session, attempt=facts.attempt)
    return stream, rows


async def _legacy_test_only_record_authorized_object_delta_source_publication_attestation(
    session: object,
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Test-only mechanics for legacy gated source-attestation persistence.

    The legacy opaque authorization is deliberately not sufficient for
    runtime publication because it lacks the locked-snapshot/live-Witness
    authority required by the three-server architecture.
    """

    _require_active_transaction(session)
    facts = _authorized_facts(authorization)
    stream, rows = await _lock_authorized_stream_rows(session, facts=facts)
    existing_state = _state_from_locked_rows(rows, expected=facts.attempt, stream=stream)
    if existing_state is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source attestation has no durable publication reservation"
        )
    try:
        uploaded = _uploaded_from_state(existing_state)
        plan = plan_object_delta_source_publication_attestation(
            uploaded_attempt=uploaded,
            attestation=facts.attestation,
            existing_state=existing_state,
        )
    except (AttributeError, ObjectDeltaSourcePublicationAttemptError) as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source attestation conflicts with immutable publication evidence"
        ) from exc
    if plan.attested_attempt_to_write is None:
        if rows.attempt_row is None:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "authorized source-attestation replay reservation row is missing"
            )
        return _result(
            action=plan.action,
            state=existing_state,
            rows=rows,
            attempt_row=rows.attempt_row,
        )
    if rows.attempt_row is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source-attestation reservation row is missing"
        )
    inserted = _attestation_row_from_state(plan.attested_attempt_to_write)
    await _flush_insert(session, inserted, label="source attestation")
    return _result(
        action=plan.action,
        state=plan.attested_attempt_to_write,
        rows=rows,
        attempt_row=rows.attempt_row,
        attestation_row=inserted,
    )


async def record_authorized_object_delta_source_publication_attestation(
    session: object,
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Reject the superseded legacy source-attestation runtime route."""

    del session, authorization
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="record_authorized_object_delta_source_publication_attestation"
    )


async def _legacy_test_only_bind_authorized_object_delta_source_publication_ledger(
    session: object,
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Test-only mechanics for legacy source-ledger terminalization.

    The legacy authorization cannot establish the mandatory locked snapshot
    and fresh live Writer Witness authority.  This private helper is retained
    only for isolated contract tests; no application/runtime code may call it.
    """

    _require_active_transaction(session)
    facts = _authorized_facts(authorization)
    stream, rows = await _lock_authorized_stream_rows(session, facts=facts)
    existing_state = _state_from_locked_rows(rows, expected=facts.attempt, stream=stream)
    if existing_state is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source ledger has no durable publication reservation"
        )
    if not isinstance(existing_state, (ObjectDeltaSourcePublicationAttestedAttempt, ObjectDeltaSourcePublicationLedgeredAttempt)):
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source ledger requires a durable source attestation"
        )
    # For an unbound attempt validate the pure candidate before invoking the
    # ledger adapter.  This makes any mismatch fail before it can append a
    # source-ledger row; the following adapter call then supplies the locked
    # append/replay fact used for terminalization.
    if isinstance(existing_state, ObjectDeltaSourcePublicationAttestedAttempt):
        try:
            pre_plan = plan_object_delta_source_publication_ledger(
                attested_attempt=existing_state,
                candidate_ledger_entry=facts.ledger_entry,
                existing_state=existing_state,
                existing_ledger_entry=None,
            )
        except ObjectDeltaSourcePublicationAttemptError as exc:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "authorized source ledger conflicts with immutable publication evidence"
            ) from exc
        if pre_plan.action != SOURCE_PUBLICATION_LEDGER_ACTION_APPEND:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "authorized source ledger append plan is invalid"
            )
    try:
        ledger_result: ObjectDeltaSourceLedgerPersistenceResult = (
            await _legacy_test_only_persist_prepared_object_delta_source_batch_ledger(
                session,
                facts.prepared,
            )
        )
    except ObjectDeltaSourceLedgerPersistenceError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source ledger persistence failed"
        ) from exc
    if ledger_result.ledger_entry != facts.ledger_entry:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "authorized source ledger adapter returned a mismatched ledger entry"
        )
    if isinstance(existing_state, ObjectDeltaSourcePublicationAttestedAttempt):
        if ledger_result.action != SOURCE_BATCH_APPEND_ACTION_APPEND:
            # A pre-existing ledger without the attempt binding is an orphan,
            # not a justification to silently attach it on a retry.
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "source ledger exists without a terminal publication binding"
            )
        try:
            plan = plan_object_delta_source_publication_ledger(
                attested_attempt=existing_state,
                candidate_ledger_entry=facts.ledger_entry,
                existing_state=existing_state,
                existing_ledger_entry=None,
            )
        except ObjectDeltaSourcePublicationAttemptError as exc:  # pragma: no cover - guarded above.
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "authorized source ledger append plan is invalid"
            ) from exc
        if plan.ledgered_attempt_to_write is None or rows.attempt_row is None:
            raise ObjectDeltaSourcePublicationAttemptPersistenceError(
                "authorized source ledger terminal binding plan is invalid"
            )
        binding = PublicationLedgerBindingRow(
            attempt_id=facts.attempt.attempt_id,
            source_batch_ledger_id=ledger_result.ledger_row.id,
        )
        await _flush_insert(session, binding, label="terminal ledger binding")
        return _result(
            action=plan.action,
            state=plan.ledgered_attempt_to_write,
            rows=rows,
            attempt_row=rows.attempt_row,
            ledger_binding_row=binding,
            ledger_row=ledger_result.ledger_row,
        )
    # A terminal retry must make the ledger adapter replay its exact immutable
    # row and then satisfy the pure terminal-state planner with that row.
    if ledger_result.action != SOURCE_BATCH_APPEND_ACTION_REPLAY:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "terminal publication binding unexpectedly appended a new source ledger row"
        )
    try:
        plan = plan_object_delta_source_publication_ledger(
            attested_attempt=existing_state.attested,
            candidate_ledger_entry=facts.ledger_entry,
            existing_state=existing_state,
            existing_ledger_entry=ledger_result.ledger_entry,
        )
    except ObjectDeltaSourcePublicationAttemptError as exc:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "terminal source publication ledger replay conflicts with immutable evidence"
        ) from exc
    if plan.action != SOURCE_PUBLICATION_LEDGER_ACTION_REPLAY or rows.attempt_row is None:
        raise ObjectDeltaSourcePublicationAttemptPersistenceError(
            "terminal source publication ledger replay plan is invalid"
        )
    return _result(
        action=plan.action,
        state=existing_state,
        rows=rows,
        attempt_row=rows.attempt_row,
        ledger_row=ledger_result.ledger_row,
    )


async def bind_authorized_object_delta_source_publication_ledger(
    session: object,
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> ObjectDeltaSourcePublicationAttemptPersistenceResult:
    """Reject the superseded legacy source-ledger runtime route."""

    del session, authorization
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="bind_authorized_object_delta_source_publication_ledger"
    )
