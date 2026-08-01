"""Caller-owned persistence for Object-delta receiver delivery nonces.

This adapter has one purpose: under a transaction-scoped advisory lock, make
the controller's ``(key_id, nonce)`` consumption durable.  It intentionally
does not download Objects, decrypt age payloads, plan application mutations,
or commit a transaction.  A future receiver must call it in the *same*
transaction as its immutable import receipt and cursor update.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import func, select

from core.object_delta_receiver_delivery_nonce import (
    RECEIVER_DELIVERY_NONCE_ACTION_CONSUME,
    ObjectDeltaReceiverDeliveryNonceError,
    ObjectDeltaReceiverDeliveryNonceReceipt as NonceReceipt,
    plan_object_delta_receiver_delivery_nonce_consumption,
    validate_object_delta_receiver_delivery_nonce_receipt,
)
from models.object_delta_receiver_delivery import (
    ObjectDeltaReceiverDeliveryNonceReceipt,
)


class ObjectDeltaReceiverDeliveryNoncePersistenceError(RuntimeError):
    """A caller-owned nonce transaction cannot safely proceed."""


@dataclass(frozen=True)
class ObjectDeltaReceiverDeliveryNoncePersistenceResult:
    """The exact nonce append/replay decision and its ORM row."""

    action: str
    receipt: NonceReceipt
    row: ObjectDeltaReceiverDeliveryNonceReceipt


def _session_has_active_transaction(session: object) -> bool:
    probe = getattr(session, "in_transaction", None)
    try:
        state = probe() if callable(probe) else probe
    except Exception:
        return False
    return bool(state)


def receiver_delivery_nonce_advisory_lock_key(receipt: NonceReceipt) -> int:
    """Return a stable signed bigint key scoped to one controller nonce."""

    normalized = validate_object_delta_receiver_delivery_nonce_receipt(receipt)
    payload = {
        "namespace": "gold-trade-object-delta-receiver-delivery-nonce-v1",
        "controller_key_id": normalized.controller_key_id,
        "nonce": normalized.nonce,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _receipt_from_row(row: object) -> NonceReceipt:
    if not isinstance(row, ObjectDeltaReceiverDeliveryNonceReceipt):
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            "locked delivery nonce row is invalid"
        )
    try:
        return validate_object_delta_receiver_delivery_nonce_receipt(
            NonceReceipt(
                controller_key_id=row.controller_key_id,
                nonce=row.nonce,
                packet_claim_sha256=row.packet_claim_sha256,
                bucket=row.bucket,
                source_site=row.source_site,
                destination_site=row.destination_site,
                destination_age_recipient=row.destination_age_recipient,
                campaign_id=row.campaign_id,
                release_sha=row.release_sha,
                stream_generation_id=row.stream_generation_id,
                writer_epoch=row.writer_epoch,
                writer_lease_id=row.writer_lease_id,
                first_sequence=row.first_sequence,
                last_sequence=row.last_sequence,
                batch_sha256=row.batch_sha256,
                object_key=row.object_key,
                object_version_id=row.object_version_id,
                expires_at=row.expires_at,
            )
        )
    except ObjectDeltaReceiverDeliveryNonceError as exc:
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            "locked delivery nonce row is invalid"
        ) from exc


def _model_from_receipt(receipt: NonceReceipt) -> ObjectDeltaReceiverDeliveryNonceReceipt:
    return ObjectDeltaReceiverDeliveryNonceReceipt(
        controller_key_id=receipt.controller_key_id,
        nonce=receipt.nonce,
        packet_claim_sha256=receipt.packet_claim_sha256,
        bucket=receipt.bucket,
        source_site=receipt.source_site,
        destination_site=receipt.destination_site,
        destination_age_recipient=receipt.destination_age_recipient,
        campaign_id=receipt.campaign_id,
        release_sha=receipt.release_sha,
        stream_generation_id=receipt.stream_generation_id,
        writer_epoch=receipt.writer_epoch,
        writer_lease_id=receipt.writer_lease_id,
        first_sequence=receipt.first_sequence,
        last_sequence=receipt.last_sequence,
        batch_sha256=receipt.batch_sha256,
        object_key=receipt.object_key,
        object_version_id=receipt.object_version_id,
        expires_at=receipt.expires_at,
    )


async def _scalar_one_or_none(session: object, statement: object, *, label: str):
    try:
        result = await session.execute(statement)
        return result.scalar_one_or_none()
    except Exception as exc:
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            f"Object-delta receiver delivery nonce {label} query failed"
        ) from exc


async def _lock_nonce_advisory(session: object, receipt: NonceReceipt) -> None:
    await _scalar_one_or_none(
        session,
        select(func.pg_advisory_xact_lock(receiver_delivery_nonce_advisory_lock_key(receipt))),
        label="advisory lock",
    )


async def _load_nonce_for_update(
    session: object,
    receipt: NonceReceipt,
) -> ObjectDeltaReceiverDeliveryNonceReceipt | None:
    return await _scalar_one_or_none(
        session,
        select(ObjectDeltaReceiverDeliveryNonceReceipt)
        .where(
            ObjectDeltaReceiverDeliveryNonceReceipt.controller_key_id
            == receipt.controller_key_id,
            ObjectDeltaReceiverDeliveryNonceReceipt.nonce == receipt.nonce,
        )
        .with_for_update(),
        label="receipt lock",
    )


async def persist_object_delta_receiver_delivery_nonce(
    session: object,
    receipt: NonceReceipt,
) -> ObjectDeltaReceiverDeliveryNoncePersistenceResult:
    """Insert or exact-replay one nonce without owning transaction boundaries."""

    if not _session_has_active_transaction(session):
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            "delivery nonce persistence requires an active caller-owned transaction"
        )
    try:
        expected = validate_object_delta_receiver_delivery_nonce_receipt(receipt)
    except ObjectDeltaReceiverDeliveryNonceError as exc:
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            "expected delivery nonce receipt is invalid"
        ) from exc
    await _lock_nonce_advisory(session, expected)
    row = await _load_nonce_for_update(session, expected)
    existing = _receipt_from_row(row) if row is not None else None
    try:
        plan = plan_object_delta_receiver_delivery_nonce_consumption(
            expected=expected,
            existing=existing,
        )
    except ObjectDeltaReceiverDeliveryNonceError as exc:
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            "delivery nonce receipt conflicts with the packet"
        ) from exc
    if plan.action != RECEIVER_DELIVERY_NONCE_ACTION_CONSUME:
        if row is None:
            raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
                "delivery nonce replay row is missing"
            )
        return ObjectDeltaReceiverDeliveryNoncePersistenceResult(
            action=plan.action,
            receipt=expected,
            row=row,
        )
    if plan.receipt_to_insert != expected:
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            "delivery nonce append plan is invalid"
        )
    inserted = _model_from_receipt(expected)
    try:
        session.add(inserted)
        await session.flush()
    except Exception as exc:
        raise ObjectDeltaReceiverDeliveryNoncePersistenceError(
            "delivery nonce receipt insert failed"
        ) from exc
    return ObjectDeltaReceiverDeliveryNoncePersistenceResult(
        action=plan.action,
        receipt=expected,
        row=inserted,
    )
