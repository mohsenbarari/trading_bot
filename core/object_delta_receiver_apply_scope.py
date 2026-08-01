"""Narrow in-process authority for a verified standby Object-delta import.

The normal application Writer Witness fence deliberately protects every
database mutation, including legacy sync.  A cold standby nevertheless must
apply a source-authenticated delta while it is *not* the local writer.  This
module is the only supported bridge for that exception.

It accepts neither URLs nor credentials.  Before a scope can be entered, the
caller must already have verified a controller-signed delivery packet, loaded
the root-only receiver binding, age-decrypted the immutable Object, and bound
the parsed batch to both packet and permit.  The scope then marks exactly one
fresh ``AsyncSession`` connection with an unforgeable in-process marker.  The
database fence accepts the marker only while the matching context is active.

This is not a generic Writer Witness bypass and it is not a transaction or
transport implementation.  Production activation still requires a separate
receiver adapter; no application runtime imports this module today.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping

from core.append_only_sync_delta_batch import AppendOnlySyncDeltaBatch
from core.object_delta_delivery_control_packet import (
    VerifiedObjectDeltaDeliveryControlPacket,
    assert_verified_delivery_matches_batch,
    assert_verified_delivery_matches_receiver_permit,
    controller_key_id_from_public_key,
    revalidate_verified_object_delta_delivery_control_packet,
)
from core.object_delta_receiver_delivery_binding import (
    ObjectDeltaReceiverDeliveryBinding,
)
from core.object_delta_source_batch_attestation import (
    VerifiedObjectDeltaSourceBatchAttestation,
    verify_object_delta_source_batch_attestation,
)
from core.object_delta_transport_binding import ObjectDeltaTransportBinding


OBJECT_DELTA_RECEIVER_APPLY_EXECUTION_OPTION = "_object_delta_receiver_apply_marker"
OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY = "_object_delta_receiver_apply_marker"
_ACTIVE_MARKER: ContextVar[object | None] = ContextVar(
    "object_delta_receiver_apply_marker",
    default=None,
)
_AUTHORIZED_DELIVERY_CAPABILITY = object()


class ObjectDeltaReceiverApplyScopeError(RuntimeError):
    """A dedicated standby import cannot safely enter the Writer exception."""


@dataclass(frozen=True)
class AuthorizedObjectDeltaReceiverDelivery:
    """One fully bound delivery that may enter the dedicated DB scope.

    The contained batch is descriptor metadata, not plaintext payload bytes.
    Its construction is intentionally the required post-decryption gate.
    """

    binding: ObjectDeltaReceiverDeliveryBinding
    verified_packet: VerifiedObjectDeltaDeliveryControlPacket
    batch: AppendOnlySyncDeltaBatch
    transport_binding: ObjectDeltaTransportBinding
    source_attestation: VerifiedObjectDeltaSourceBatchAttestation
    # This is intentionally not an ``__init__`` parameter.  Only the
    # signature-verifying constructor below can mint the capability accepted
    # by the DB writer-fence bypass scope.
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def authorize_object_delta_receiver_delivery(
    *,
    binding: ObjectDeltaReceiverDeliveryBinding,
    verified_packet: VerifiedObjectDeltaDeliveryControlPacket,
    batch: AppendOnlySyncDeltaBatch,
    source_attestation: Mapping[str, Any],
) -> AuthorizedObjectDeltaReceiverDelivery:
    """Return narrow authority only after controller *and source* evidence agrees.

    The source envelope is signature-verified against the root-only local pin
    here.  A controller packet therefore cannot turn an unsigned or a
    differently sourced batch into a receiver-authorized import.
    """

    if not isinstance(binding, ObjectDeltaReceiverDeliveryBinding):
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver binding is invalid")
    try:
        verified_packet = revalidate_verified_object_delta_delivery_control_packet(verified_packet)
    except Exception as exc:
        raise ObjectDeltaReceiverApplyScopeError(
            "Object-delta verified delivery packet is invalid"
        ) from exc
    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaReceiverApplyScopeError("Object-delta validated batch is invalid")
    try:
        assert_verified_delivery_matches_receiver_permit(
            verified_packet,
            policy=binding.policy,
            permit=binding.permit,
        )
        if (
            controller_key_id_from_public_key(binding.controller_public_key)
            != verified_packet.controller_key_id
        ):
            raise ObjectDeltaReceiverApplyScopeError(
                "Object-delta controller key does not match the verified packet"
            )
        transport_binding = assert_verified_delivery_matches_batch(
            verified_packet,
            policy=binding.policy,
            batch=batch,
        )
        verified_source_attestation = verify_object_delta_source_batch_attestation(
            source_attestation,
            expected_source_public_key=binding.source_public_key,
            expected_transport_policy=binding.policy,
        )
        if (
            verified_source_attestation.source_key_id != binding.source_key_id
            or verified_source_attestation.batch != batch
            or verified_source_attestation.transport_binding != transport_binding
        ):
            raise ObjectDeltaReceiverApplyScopeError(
                "Object-delta source attestation does not match the authorized delivery"
            )
    except Exception as exc:
        raise ObjectDeltaReceiverApplyScopeError(
            "Object-delta delivery does not match the local receiver authority"
        ) from exc
    authorized = AuthorizedObjectDeltaReceiverDelivery(
        binding=binding,
        verified_packet=verified_packet,
        batch=batch,
        transport_binding=transport_binding,
        source_attestation=verified_source_attestation,
    )
    object.__setattr__(authorized, "_capability", _AUTHORIZED_DELIVERY_CAPABILITY)
    return authorized


def validate_authorized_object_delta_receiver_delivery(
    authorization: object,
) -> AuthorizedObjectDeltaReceiverDelivery:
    """Recheck every non-secret binding on an already authorized delivery.

    Signature verification happens in :func:`authorize_object_delta_receiver_delivery`
    while the canonical decrypted envelope is available.  Later transaction
    layers receive the immutable verified value only, and must still prove it
    remains bound to the exact local permit, controller packet, and batch.
    """

    if type(authorization) is not AuthorizedObjectDeltaReceiverDelivery:
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver authority is invalid")
    if authorization._capability is not _AUTHORIZED_DELIVERY_CAPABILITY:
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver authority was not authorized")
    binding = authorization.binding
    packet = authorization.verified_packet
    batch = authorization.batch
    source_attestation = authorization.source_attestation
    if (
        not isinstance(binding, ObjectDeltaReceiverDeliveryBinding)
        or type(packet) is not VerifiedObjectDeltaDeliveryControlPacket
        or not isinstance(batch, AppendOnlySyncDeltaBatch)
        or not isinstance(source_attestation, VerifiedObjectDeltaSourceBatchAttestation)
    ):
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver authority is invalid")
    try:
        revalidate_verified_object_delta_delivery_control_packet(packet)
        assert_verified_delivery_matches_receiver_permit(
            packet,
            policy=binding.policy,
            permit=binding.permit,
        )
        if controller_key_id_from_public_key(binding.controller_public_key) != packet.controller_key_id:
            raise ObjectDeltaReceiverApplyScopeError(
                "Object-delta controller key does not match the verified packet"
            )
        transport_binding = assert_verified_delivery_matches_batch(
            packet,
            policy=binding.policy,
            batch=batch,
        )
    except Exception as exc:
        raise ObjectDeltaReceiverApplyScopeError(
            "Object-delta delivery does not match the local receiver authority"
        ) from exc
    if (
        authorization.transport_binding != transport_binding
        or source_attestation.source_public_key != binding.source_public_key
        or source_attestation.source_key_id != binding.source_key_id
        or source_attestation.transport_policy != binding.policy
        or source_attestation.batch != batch
        or source_attestation.transport_binding != transport_binding
    ):
        raise ObjectDeltaReceiverApplyScopeError(
            "Object-delta source attestation does not match the authorized delivery"
        )
    return authorization


def _session_info(session: object) -> dict[object, object]:
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver session has no mutable info")
    return info


def _session_has_active_transaction(session: object) -> bool:
    checker = getattr(session, "in_transaction", None)
    if not callable(checker):
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver session transaction state is unavailable")
    try:
        return bool(checker())
    except Exception as exc:
        raise ObjectDeltaReceiverApplyScopeError(
            "Object-delta receiver session transaction state is unavailable"
        ) from exc


def session_is_authorized_for_object_delta_receiver_apply(session: object) -> bool:
    """Return true only for the fresh session paired to the active marker."""

    marker = _ACTIVE_MARKER.get()
    if marker is None:
        return False
    info = getattr(session, "info", None)
    return isinstance(info, Mapping) and info.get(OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY) is marker


def execution_is_authorized_for_object_delta_receiver_apply(execution_context: object) -> bool:
    """Match the active marker to the connection execution options exactly."""

    marker = _ACTIVE_MARKER.get()
    if marker is None:
        return False
    options = getattr(execution_context, "execution_options", None)
    return isinstance(options, Mapping) and options.get(
        OBJECT_DELTA_RECEIVER_APPLY_EXECUTION_OPTION
    ) is marker


@asynccontextmanager
async def authorized_object_delta_receiver_apply_scope(
    session: object,
    *,
    authorization: AuthorizedObjectDeltaReceiverDelivery,
) -> AsyncIterator[None]:
    """Mark one fresh ``AsyncSession`` transaction for a verified delta apply.

    Opening the connection is intentionally part of scope entry.  It applies
    the opaque marker as a connection execution option before any DB statement
    exists, so direct SQL from a separate connection in the same task cannot
    inherit the exception.  The caller owns commit or rollback while this
    scope remains active.
    """

    authorization = validate_authorized_object_delta_receiver_delivery(authorization)
    if _ACTIVE_MARKER.get() is not None:
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver apply scopes cannot nest")
    info = _session_info(session)
    if OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY in info:
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver session is already marked")
    if _session_has_active_transaction(session):
        raise ObjectDeltaReceiverApplyScopeError(
            "Object-delta receiver scope requires a fresh session transaction"
        )
    connection_factory = getattr(session, "connection", None)
    if not callable(connection_factory):
        raise ObjectDeltaReceiverApplyScopeError("Object-delta receiver session connection is unavailable")

    marker = object()
    reset_token: Token[object | None] = _ACTIVE_MARKER.set(marker)
    info[OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY] = marker
    try:
        connection = connection_factory(
            execution_options={OBJECT_DELTA_RECEIVER_APPLY_EXECUTION_OPTION: marker}
        )
        if not hasattr(connection, "__await__"):
            raise ObjectDeltaReceiverApplyScopeError(
                "Object-delta receiver session connection is not asynchronous"
            )
        await connection
        yield
    finally:
        if info.get(OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY) is marker:
            info.pop(OBJECT_DELTA_RECEIVER_APPLY_SESSION_INFO_KEY, None)
        _ACTIVE_MARKER.reset(reset_token)
