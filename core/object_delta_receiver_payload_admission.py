"""Pure, default-off canonical-payload admission for an authorized receiver.

The signed batch and controller packet deliberately do not duplicate the
sync-registry fingerprint: the source batch attestation already commits to
the exact batch descriptor, including its plaintext payload hash and length.
This module closes the receiver-side link by taking the fingerprint only from
the root-only, release-bound receiver delivery binding and passing it to the
canonical payload parser.  It then exposes the only planning wrapper that
derives every import-plan expectation from that authorized binding.

No adapter currently calls this module.  It does not read a permit, decrypt
age data, access Object Storage, open a database transaction, apply a change,
or enable a worker.  A future adapter must obtain the decrypted exact payload
bytes, mint this capability, acquire its lock-scoped rows, and use the
planning wrapper below; sequence-one planning additionally needs the separate
genesis-admission gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from core.append_only_sync_delta_batch import AppendOnlySyncDeltaBatch, canonical_json_bytes
from core.append_only_sync_delta_payload import (
    OBJECT_DELTA_PAYLOAD_SCHEMA,
    REGISTRY_FINGERPRINT_RE,
    NormalizedObjectDeltaPayload,
    ObjectDeltaPayloadError,
    parse_object_delta_payload,
)
from core.object_delta_import_plan import (
    AtomicObjectDeltaImportPlan,
    ObjectDeltaImportReceipt,
    ReceiverStreamCursor,
    SOURCE_SERVER_BY_SITE,
    plan_atomic_object_delta_import,
)
from core.object_delta_receiver_apply_scope import (
    AuthorizedObjectDeltaReceiverDelivery,
    ObjectDeltaReceiverApplyScopeError,
    validate_authorized_object_delta_receiver_delivery,
)


OBJECT_DELTA_RECEIVER_PAYLOAD_ADMISSION_CONTRACT = (
    "gold-trade-object-delta-receiver-payload-admission-v1"
)
OBJECT_DELTA_RECEIVER_PAYLOAD_ADMISSION_DEFAULT_ENABLED = False
OBJECT_DELTA_RECEIVER_PAYLOAD_ADMISSION_ENABLES_RUNTIME = False

_PAYLOAD_ADMISSION_CAPABILITY = object()


class ObjectDeltaReceiverPayloadAdmissionError(ValueError):
    """A payload cannot be bound to the receiver's release-local pin."""


@dataclass(frozen=True)
class AuthorizedObjectDeltaReceiverPayload:
    """Opaque, hash-bound canonical plaintext for one authorized delivery.

    ``payload_had_terminal_newline`` records the exact plaintext representation
    committed by the batch descriptor.  The source assembler currently emits
    canonical JSON without that newline, whereas some older pure fixtures use
    the canonical wire form with it.  Both remain safe only when the exact
    supplied byte string matches the signed batch hash and byte count.
    """

    authorization: AuthorizedObjectDeltaReceiverDelivery
    payload: NormalizedObjectDeltaPayload
    registry_fingerprint: str
    payload_sha256: str
    payload_bytes: int
    payload_had_terminal_newline: bool
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _expected_registry_fingerprint(authorization: AuthorizedObjectDeltaReceiverDelivery) -> str:
    value = authorization.binding.expected_registry_fingerprint
    if not isinstance(value, str) or REGISTRY_FINGERPRINT_RE.fullmatch(value) is None:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "receiver delivery expected registry fingerprint is invalid"
        )
    return value


def _canonical_wire_payload_bytes(
    payload: NormalizedObjectDeltaPayload,
    *,
    terminal_newline: bool,
) -> bytes:
    if not isinstance(payload, NormalizedObjectDeltaPayload):
        raise ObjectDeltaReceiverPayloadAdmissionError("normalized Object-delta payload is invalid")
    if type(terminal_newline) is not bool:
        raise ObjectDeltaReceiverPayloadAdmissionError("payload terminal newline marker is invalid")
    try:
        value = {
            "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
            "stream_generation_id": payload.stream_generation_id,
            "items": [item.item for item in payload.items],
        }
        canonical = canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "normalized Object-delta payload is invalid"
        ) from exc
    return canonical + (b"\n" if terminal_newline else b"")


def _parse_payload_for_authorization(
    *,
    authorization: AuthorizedObjectDeltaReceiverDelivery,
    raw_payload: object,
) -> tuple[NormalizedObjectDeltaPayload, str, int, bool]:
    if not isinstance(raw_payload, bytes) or not raw_payload:
        raise ObjectDeltaReceiverPayloadAdmissionError("Object-delta payload bytes are invalid")
    batch = authorization.batch
    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaReceiverPayloadAdmissionError("authorized Object-delta batch is invalid")
    if len(raw_payload) != batch.payload_bytes:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "Object-delta payload byte count does not match the authorized batch"
        )
    actual_sha256 = hashlib.sha256(raw_payload).hexdigest()
    if actual_sha256 != batch.payload_sha256:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "Object-delta payload hash does not match the authorized batch"
        )
    expected_registry_fingerprint = _expected_registry_fingerprint(authorization)
    try:
        expected_source_server = SOURCE_SERVER_BY_SITE[batch.source_site]
    except KeyError as exc:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "authorized Object-delta source site is invalid"
        ) from exc
    terminal_newline = raw_payload.endswith(b"\n")
    parser_input = raw_payload if terminal_newline else raw_payload + b"\n"
    try:
        payload = parse_object_delta_payload(
            parser_input,
            expected_stream_generation_id=batch.stream.generation_id,
            expected_stream_sequence_ids=batch.stream.sequence_ids,
            expected_source_server=expected_source_server,
            expected_registry_fingerprint=expected_registry_fingerprint,
        )
    except ObjectDeltaPayloadError as exc:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "Object-delta payload does not match the receiver release registry pin"
        ) from exc
    return payload, expected_registry_fingerprint, len(raw_payload), terminal_newline


def authorize_object_delta_receiver_payload(
    *,
    authorization: AuthorizedObjectDeltaReceiverDelivery,
    raw_payload: bytes,
) -> AuthorizedObjectDeltaReceiverPayload:
    """Mint payload authority only after exact byte/hash and registry checks.

    The caller cannot choose a registry fingerprint.  It comes exclusively
    from the already-validated root-only receiver binding carried by the
    opaque delivery authority.  The raw plaintext must exactly match the
    source-attested batch hash before its canonical item metadata is parsed.
    """

    try:
        verified_authorization = validate_authorized_object_delta_receiver_delivery(authorization)
    except ObjectDeltaReceiverApplyScopeError as exc:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "Object-delta delivery is not authorized for payload admission"
        ) from exc
    payload, registry_fingerprint, payload_bytes, terminal_newline = _parse_payload_for_authorization(
        authorization=verified_authorization,
        raw_payload=raw_payload,
    )
    admitted = AuthorizedObjectDeltaReceiverPayload(
        authorization=verified_authorization,
        payload=payload,
        registry_fingerprint=registry_fingerprint,
        payload_sha256=verified_authorization.batch.payload_sha256,
        payload_bytes=payload_bytes,
        payload_had_terminal_newline=terminal_newline,
    )
    object.__setattr__(admitted, "_capability", _PAYLOAD_ADMISSION_CAPABILITY)
    return require_authorized_object_delta_receiver_payload(admitted)


def require_authorized_object_delta_receiver_payload(
    value: object,
) -> AuthorizedObjectDeltaReceiverPayload:
    """Revalidate the opaque payload capability before planning an import."""

    if type(value) is not AuthorizedObjectDeltaReceiverPayload:
        raise ObjectDeltaReceiverPayloadAdmissionError("authorized Object-delta payload is invalid")
    if value._capability is not _PAYLOAD_ADMISSION_CAPABILITY:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "Object-delta payload was not admitted from authorized evidence"
        )
    try:
        authorization = validate_authorized_object_delta_receiver_delivery(value.authorization)
    except ObjectDeltaReceiverApplyScopeError as exc:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "authorized Object-delta payload delivery is no longer valid"
        ) from exc
    expected_registry_fingerprint = _expected_registry_fingerprint(authorization)
    if value.registry_fingerprint != expected_registry_fingerprint:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "authorized Object-delta payload registry fingerprint does not match the receiver pin"
        )
    if value.payload_sha256 != authorization.batch.payload_sha256:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "authorized Object-delta payload hash does not match the delivery"
        )
    raw_payload = _canonical_wire_payload_bytes(
        value.payload,
        terminal_newline=value.payload_had_terminal_newline,
    )
    if len(raw_payload) != value.payload_bytes or hashlib.sha256(raw_payload).hexdigest() != value.payload_sha256:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "authorized Object-delta payload bytes do not match the delivery"
        )
    parsed, registry_fingerprint, _payload_bytes, _terminal_newline = _parse_payload_for_authorization(
        authorization=authorization,
        raw_payload=raw_payload,
    )
    if parsed != value.payload or registry_fingerprint != value.registry_fingerprint:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "authorized Object-delta payload is not canonically bound to the receiver pin"
        )
    return value


def plan_authorized_object_delta_receiver_payload_import(
    *,
    payload_admission: AuthorizedObjectDeltaReceiverPayload,
    receiver_cursor: ReceiverStreamCursor | None,
    receipt_by_object: ObjectDeltaImportReceipt | None,
    receipt_by_stream: ObjectDeltaImportReceipt | None,
) -> AtomicObjectDeltaImportPlan:
    """Derive an import plan with no caller-selectable release expectations.

    This does not begin a transaction or apply a mutation.  The caller must
    provide lock-scoped cursor/receipt rows, and a future sequence-one adapter
    must separately require genesis admission before executing the plan.
    """

    admitted = require_authorized_object_delta_receiver_payload(payload_admission)
    binding = admitted.authorization.binding
    permit = binding.permit
    try:
        return plan_atomic_object_delta_import(
            batch=admitted.authorization.batch,
            payload=admitted.payload,
            local_site=permit.destination_site,
            expected_source_site=permit.source_site,
            expected_campaign_id=permit.campaign_id,
            expected_release_sha=permit.release_sha,
            expected_stream_generation_id=permit.stream_generation_id,
            expected_writer_epoch=permit.writer_epoch,
            expected_writer_lease_id=permit.writer_lease_id,
            expected_registry_fingerprint=admitted.registry_fingerprint,
            receiver_cursor=receiver_cursor,
            receipt_by_object=receipt_by_object,
            receipt_by_stream=receipt_by_stream,
        )
    except Exception as exc:
        raise ObjectDeltaReceiverPayloadAdmissionError(
            "authorized Object-delta payload cannot derive an import plan"
        ) from exc
