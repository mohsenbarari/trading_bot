"""Pure source-side sealing of a verified Object-delta ciphertext receipt.

The source publisher has a deliberately split responsibility.  It first
assembles canonical plaintext from durable outbox rows, encrypts it for the
fixed receiver, uploads it with create-only semantics, and reads back the
exact Object version.  Only then may it call this module to construct the
append-only batch descriptor and immutable source-ledger candidate.

This module performs none of those external operations.  In particular, it
does not read an age key, encrypt, open a database transaction, load a
credential, contact Object Storage, or write a ledger row.  The caller must
independently prove the supplied ciphertext receipt by an exact VersionId
read-back before passing it here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.append_only_sync_delta_batch import (
    AppendOnlySyncDeltaBatch,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    DELTA_OBJECT_KIND,
    build_delta_batch,
    sha256_bytes,
    validate_delta_batch,
)
from core.append_only_sync_delta_payload import parse_object_delta_payload
from core.object_delta_batch_assembler import PreparedObjectDeltaPayload
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_batch_ledger import (
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportBinding,
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
    validate_object_delta_transport_policy,
)


class ObjectDeltaSourceBatchPublicationError(ValueError):
    """A source batch cannot be safely bound to its verified Object receipt."""


_PREPARED_OBJECT_DELTA_SOURCE_BATCH_CAPABILITY = object()


@dataclass(frozen=True)
class PreparedObjectDeltaSourceBatch:
    """Low-level, non-delivery-ready source batch metadata.

    ``prepare_object_delta_source_batch`` mints private provenance after it
    has revalidated canonical plaintext under one source binding.  The public
    fields remain intentionally inspectable so the existing ledger primitive
    and isolated contract tests can use them, but a future delivery path must
    pass the value through the cutover-publication gate before it can treat
    it as eligible for a ledger append or source attestation.
    """

    batch: AppendOnlySyncDeltaBatch
    transport_binding: ObjectDeltaTransportBinding
    ledger_entry: SourceBatchLedgerEntry
    _binding: ObjectDeltaSourceRuntimeBinding | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


def _normalized_binding(binding: object) -> ObjectDeltaSourceRuntimeBinding:
    """Reconstruct an immutable source binding before trusting its fields."""

    if not isinstance(binding, ObjectDeltaSourceRuntimeBinding):
        raise ObjectDeltaSourceBatchPublicationError("Object-delta source binding is invalid")
    try:
        return ObjectDeltaSourceRuntimeBinding(
            source_site=binding.source_site,
            destination_site=binding.destination_site,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            stream_generation_id=binding.stream_generation_id,
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceBatchPublicationError("Object-delta source binding is invalid") from exc


def require_prepared_object_delta_source_batch_provenance(
    prepared: object,
    *,
    binding: ObjectDeltaSourceRuntimeBinding,
) -> PreparedObjectDeltaSourceBatch:
    """Require provenance minted by this module for one exact binding.

    This is deliberately narrower than source-cutover authorization: it only
    proves that this module previously checked plaintext canonicality and the
    registry pin for ``binding``.  The standard delivery-facing API is the
    separate cutover-publication gate, which additionally verifies signed
    baseline evidence and a root-controlled source-key pin.
    """

    normalized_binding = _normalized_binding(binding)
    if type(prepared) is not PreparedObjectDeltaSourceBatch:
        raise ObjectDeltaSourceBatchPublicationError("prepared Object-delta source batch is invalid")
    if prepared._capability is not _PREPARED_OBJECT_DELTA_SOURCE_BATCH_CAPABILITY:
        raise ObjectDeltaSourceBatchPublicationError(
            "prepared Object-delta source batch has no verified provenance"
        )
    if prepared._binding != normalized_binding:
        raise ObjectDeltaSourceBatchPublicationError(
            "prepared Object-delta source batch provenance does not match the source binding"
        )
    return prepared


def _stream_from_binding(binding: ObjectDeltaSourceRuntimeBinding) -> SourceStreamIdentity:
    binding = _normalized_binding(binding)
    try:
        return SourceStreamIdentity(
            source_site=binding.source_site,
            destination_site=binding.destination_site,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            stream_generation_id=binding.stream_generation_id,
        )
    except Exception as exc:
        raise ObjectDeltaSourceBatchPublicationError("Object-delta source stream binding is invalid") from exc


def _validated_prepared_payload(
    prepared: PreparedObjectDeltaPayload,
    *,
    binding: ObjectDeltaSourceRuntimeBinding,
) -> PreparedObjectDeltaPayload:
    if not isinstance(prepared, PreparedObjectDeltaPayload):
        raise ObjectDeltaSourceBatchPublicationError("Object-delta prepared payload is invalid")
    expected_stream = _stream_from_binding(binding)
    if prepared.stream != expected_stream:
        raise ObjectDeltaSourceBatchPublicationError(
            "Object-delta prepared payload stream does not match the source binding"
        )
    if (
        prepared.first_sequence < 1
        or prepared.last_sequence < prepared.first_sequence
        or prepared.sequence_ids != tuple(range(prepared.first_sequence, prepared.last_sequence + 1))
    ):
        raise ObjectDeltaSourceBatchPublicationError("Object-delta prepared payload sequence is invalid")
    if not isinstance(prepared.payload, bytes) or not prepared.payload:
        raise ObjectDeltaSourceBatchPublicationError("Object-delta prepared payload bytes are invalid")
    if sha256_bytes(prepared.payload) != prepared.payload_sha256:
        raise ObjectDeltaSourceBatchPublicationError("Object-delta prepared payload hash is invalid")
    try:
        normalized = parse_object_delta_payload(
            # The assembler keeps the plaintext as canonical JSON bytes. The
            # parser's wire form is the same canonical bytes with one final
            # newline, so appending it here validates both representations
            # without accepting an already newline-terminated payload.
            prepared.payload + b"\n",
            expected_stream_generation_id=binding.stream_generation_id,
            expected_stream_sequence_ids=prepared.sequence_ids,
            expected_source_server=binding.source_server,
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
        )
    except Exception as exc:
        raise ObjectDeltaSourceBatchPublicationError("Object-delta prepared payload is invalid") from exc
    if tuple(item.logical_sequence for item in normalized.items) != prepared.sequence_ids:
        raise ObjectDeltaSourceBatchPublicationError("Object-delta prepared payload sequence is invalid")
    # ``parse_object_delta_payload`` proves canonical content.  Reconstructing
    # the delta descriptor below independently proves the stored hash/length.
    return prepared


def _immutable_receipt_mapping(
    value: object,
    *,
    expected_object_key: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ObjectDeltaSourceBatchPublicationError("Object-delta ciphertext receipt is invalid")
    receipt = dict(value)
    if receipt.get("object_key") != expected_object_key:
        raise ObjectDeltaSourceBatchPublicationError(
            "Object-delta ciphertext receipt key does not match the deterministic route"
        )
    # ``build_delta_batch`` performs strict field, hash, VersionId, and size
    # validation.  Pin the semantic constants here so caller input cannot
    # repurpose this publisher helper for a different object kind.
    if (
        receipt.get("schema") != IMMUTABLE_RECEIPT_SCHEMA
        or receipt.get("status") != IMMUTABLE_RECEIPT_STATUS
        or receipt.get("object_kind") != DELTA_OBJECT_KIND
    ):
        raise ObjectDeltaSourceBatchPublicationError("Object-delta ciphertext receipt is invalid")
    return receipt


def prepare_object_delta_source_batch(
    *,
    binding: ObjectDeltaSourceRuntimeBinding,
    policy: ObjectDeltaTransportPolicy,
    prepared_payload: PreparedObjectDeltaPayload,
    prior_chain_sha256: str,
    verified_ciphertext_receipt: Mapping[str, Any],
) -> PreparedObjectDeltaSourceBatch:
    """Bind one verified ciphertext Object to a low-level source batch.

    ``verified_ciphertext_receipt`` is accepted only after the caller has
    performed create-only upload and exact Object-version read-back.  A retry
    with a different Object key, VersionId, ciphertext digest, or byte count
    must therefore fail later at the immutable ledger append boundary rather
    than silently replacing an earlier source batch.
    The returned value is not delivery-ready: this helper does not verify a
    signed source cutover or establish that the baseline gate remains valid.
    A future source path must use the cutover-publication gate before ledger
    persistence or source attestation.
    """

    normalized_binding = _normalized_binding(binding)
    prepared = _validated_prepared_payload(prepared_payload, binding=normalized_binding)
    try:
        transport_policy = validate_object_delta_transport_policy(policy)
        expected_object_key = derive_object_delta_object_key(
            transport_policy,
            source_site=normalized_binding.source_site,
            destination_site=normalized_binding.destination_site,
            campaign_id=normalized_binding.campaign_id,
            release_sha=normalized_binding.release_sha,
            stream_generation_id=normalized_binding.stream_generation_id,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            payload_sha256=prepared.payload_sha256,
        )
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceBatchPublicationError("Object-delta transport policy is invalid") from exc

    receipt = _immutable_receipt_mapping(
        verified_ciphertext_receipt,
        expected_object_key=expected_object_key,
    )
    try:
        raw_batch = build_delta_batch(
            source_site=normalized_binding.source_site,
            destination_site=normalized_binding.destination_site,
            campaign_id=normalized_binding.campaign_id,
            release_sha=normalized_binding.release_sha,
            writer_epoch=prepared.writer_term.epoch,
            writer_lease_id=prepared.writer_term.lease_id,
            stream_generation_id=normalized_binding.stream_generation_id,
            stream_sequence_ids=prepared.sequence_ids,
            payload=prepared.payload,
            prior_chain_sha256=prior_chain_sha256,
            immutable_receipt=receipt,
        )
        batch = validate_delta_batch(
            raw_batch,
            expected_source_site=normalized_binding.source_site,
            expected_destination_site=normalized_binding.destination_site,
            expected_campaign_id=normalized_binding.campaign_id,
            expected_release_sha=normalized_binding.release_sha,
            expected_writer_epoch=prepared.writer_term.epoch,
            expected_writer_lease_id=prepared.writer_term.lease_id,
            expected_stream_generation_id=normalized_binding.stream_generation_id,
            expected_first_stream_sequence=prepared.first_sequence,
        )
        transport_binding = bind_object_delta_batch(transport_policy, batch)
        ledger_entry = SourceBatchLedgerEntry(
            stream=_stream_from_binding(normalized_binding),
            first_sequence=batch.stream.first_sequence,
            last_sequence=batch.stream.last_sequence,
            writer_epoch=batch.writer_term.epoch,
            writer_lease_id=batch.writer_term.lease_id,
            prior_chain_sha256=batch.prior_chain_sha256,
            batch_sha256=batch.batch_sha256,
            payload_sha256=batch.payload_sha256,
            payload_bytes=batch.payload_bytes,
            object_key=batch.immutable_receipt.object_key,
            object_version_id=batch.immutable_receipt.version_id,
            ciphertext_sha256=batch.immutable_receipt.ciphertext_sha256,
            ciphertext_bytes=batch.immutable_receipt.ciphertext_bytes,
        )
    except (ObjectDeltaTransportBindingError, ValueError) as exc:
        raise ObjectDeltaSourceBatchPublicationError(
            "Object-delta ciphertext receipt cannot be bound to a source batch"
        ) from exc
    result = PreparedObjectDeltaSourceBatch(
        batch=batch,
        transport_binding=transport_binding,
        ledger_entry=ledger_entry,
    )
    object.__setattr__(result, "_binding", normalized_binding)
    object.__setattr__(result, "_capability", _PREPARED_OBJECT_DELTA_SOURCE_BATCH_CAPABILITY)
    return result
