"""Durable, gap-aware receipt and identity-preserving relay for DR events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.dr_data_policy import event_policy_rejection_reason
from core.dr_event_protocol import (
    ReceiptDecision,
    ValidatedDrEnvelope,
    decide_receipt,
    relay_destinations,
    validate_envelope,
    validate_transport_path,
)
from core.dr_sync_auth import ValidatedDrRequest
from models.dr_event import (
    DrConflictQuarantine,
    DrEvent,
    DrEventDelivery,
    DrEventReceipt,
    DrReplayNonce,
    DrStreamCheckpoint,
)


class DrEventReceiveError(RuntimeError):
    """Raised when a DR batch cannot be durably and safely received."""


def _utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _event_values(envelope: ValidatedDrEnvelope) -> dict[str, Any]:
    item = envelope.payload
    # Keep source_xid structurally impossible on receive. It is a local
    # PostgreSQL transaction identity, not a transported field, and the
    # projection role intentionally has no INSERT privilege for that column.
    return {
        "event_id": item["event_id"],
        "protocol_version": item["protocol_version"],
        "origin_authority": item["origin_authority"],
        "origin_physical_site": item["origin_physical_site"],
        "producer_epoch": item["producer_epoch"],
        "producer_sequence": item["producer_sequence"],
        "aggregate_type": item["aggregate_type"],
        "aggregate_id": item["aggregate_id"],
        "aggregate_db_id": item["aggregate_db_id"],
        "aggregate_version": item["aggregate_version"],
        "operation": item["operation"],
        "canonical_payload": item["canonical_payload"],
        "canonical_payload_hash": item["canonical_payload_hash"],
        "envelope_hash": envelope.envelope_hash,
        "schema_version": item["schema_version"],
        "causation_id": item["causation_id"],
        "idempotency_key": item["idempotency_key"],
        "writer_epoch": item["writer_epoch"],
        "tombstone": item["tombstone"],
        "transaction_id": item.get("transaction_id"),
        "transaction_position": item.get("transaction_position"),
        "transaction_size": item.get("transaction_size"),
        "transaction_hash": item.get("transaction_hash"),
        "destination_streams": item.get("destination_streams"),
        "created_at": _utc_timestamp(item["created_at"]),
    }


async def reserve_replay_nonce(
    session: AsyncSession,
    *,
    request: ValidatedDrRequest,
    expires_at: datetime,
) -> None:
    existing = await session.get(DrReplayNonce, (request.key_id, request.nonce))
    if existing is not None:
        raise DrEventReceiveError("DR request nonce was already used")
    session.add(
        DrReplayNonce(
            key_id=request.key_id,
            nonce=request.nonce,
            source_site=request.source_site,
            destination_site=request.destination_site,
            request_hash=request.request_hash,
            expires_at=expires_at,
        )
    )


async def _checkpoint(
    session: AsyncSession,
    *,
    local_site: str,
    envelope: ValidatedDrEnvelope,
) -> DrStreamCheckpoint:
    key = (local_site, envelope.origin_physical_site, envelope.producer_epoch)
    checkpoint = await session.get(DrStreamCheckpoint, key, with_for_update=True)
    if checkpoint is None:
        checkpoint = DrStreamCheckpoint(
            destination_site=local_site,
            origin_physical_site=envelope.origin_physical_site,
            producer_epoch=envelope.producer_epoch,
            contiguous_received_sequence=0,
            contiguous_applied_sequence=0,
        )
        session.add(checkpoint)
        await session.flush()
    return checkpoint


async def _existing_hashes(
    session: AsyncSession,
    envelope: ValidatedDrEnvelope,
    *,
    local_site: str,
    destination_sequence: int,
) -> tuple[str | None, str | None, DrEventReceipt | None]:
    event = await session.get(DrEvent, envelope.event_id)
    event_hash = event.envelope_hash if event is not None else None
    receipt = await session.scalar(
        select(DrEventReceipt).where(
            DrEventReceipt.destination_site == local_site,
            DrEventReceipt.origin_physical_site == envelope.origin_physical_site,
            DrEventReceipt.producer_epoch == envelope.producer_epoch,
            DrEventReceipt.producer_sequence == destination_sequence,
        )
    )
    return event_hash, (receipt.envelope_hash if receipt is not None else None), receipt


async def _advance_received_checkpoint(
    session: AsyncSession,
    checkpoint: DrStreamCheckpoint,
) -> None:
    while True:
        next_sequence = int(checkpoint.contiguous_received_sequence) + 1
        receipt = await session.scalar(
            select(DrEventReceipt).where(
                DrEventReceipt.destination_site == checkpoint.destination_site,
                DrEventReceipt.origin_physical_site == checkpoint.origin_physical_site,
                DrEventReceipt.producer_epoch == checkpoint.producer_epoch,
                DrEventReceipt.producer_sequence == next_sequence,
                DrEventReceipt.status.in_(("received", "blocked_gap")),
            ).with_for_update()
        )
        if receipt is None:
            return
        receipt.status = "received"
        deliveries = (
            await session.execute(
                select(DrEventDelivery).where(
                    DrEventDelivery.event_id == receipt.event_id,
                    DrEventDelivery.status == "blocked_gap",
                ).with_for_update()
            )
        ).scalars().all()
        for delivery in deliveries:
            delivery.status = "pending"
        checkpoint.contiguous_received_sequence = next_sequence
        checkpoint.last_envelope_hash = receipt.envelope_hash


async def receive_envelope(
    session: AsyncSession,
    *,
    envelope: ValidatedDrEnvelope,
    local_site: str,
    received_from_site: str,
) -> dict[str, Any]:
    if envelope.origin_physical_site == local_site:
        raise DrEventReceiveError("origin event cannot be received back at its producer site")
    try:
        validate_transport_path(
            origin_site=envelope.origin_physical_site,
            sender_site=received_from_site,
            destination_site=local_site,
        )
    except Exception as exc:
        raise DrEventReceiveError("DR event arrived through a forbidden transport path") from exc
    policy_reason = event_policy_rejection_reason(
        table_name=str(envelope.payload["aggregate_type"]),
        origin_authority=str(envelope.payload["origin_authority"]),
        origin_site=envelope.origin_physical_site,
        destination_site=local_site,
        payload=envelope.payload["canonical_payload"],
    )
    if policy_reason:
        session.add(
            DrConflictQuarantine(
                quarantine_id=str(uuid4()),
                destination_site=local_site,
                origin_physical_site=envelope.origin_physical_site,
                producer_epoch=envelope.producer_epoch,
                producer_sequence=envelope.producer_sequence,
                event_id=envelope.event_id,
                reason=policy_reason,
                expected_hash=None,
                received_hash=envelope.envelope_hash,
                evidence={"received_from_site": received_from_site},
            )
        )
        return {
            "event_id": envelope.event_id,
            "status": "quarantined",
            "reason": policy_reason,
        }
    try:
        destination_stream = envelope.destination_stream(local_site)
        destination_sequence = int(destination_stream["sequence"])
    except Exception as exc:
        raise DrEventReceiveError("DR event lacks an authorized destination stream") from exc
    checkpoint = await _checkpoint(session, local_site=local_site, envelope=envelope)
    event_hash, sequence_hash, existing_receipt = await _existing_hashes(
        session,
        envelope,
        local_site=local_site,
        destination_sequence=destination_sequence,
    )
    decision: ReceiptDecision = decide_receipt(
        contiguous_sequence=int(checkpoint.contiguous_received_sequence),
        incoming=envelope,
        incoming_sequence=destination_sequence,
        existing_event_hash=event_hash,
        existing_sequence_hash=sequence_hash,
    )
    if decision.action == "duplicate":
        if (
            existing_receipt is None
            or existing_receipt.event_id != envelope.event_id
            or existing_receipt.envelope_hash != envelope.envelope_hash
        ):
            session.add(
                DrConflictQuarantine(
                    quarantine_id=str(uuid4()),
                    destination_site=local_site,
                    origin_physical_site=envelope.origin_physical_site,
                    producer_epoch=envelope.producer_epoch,
                    producer_sequence=destination_sequence,
                    event_id=envelope.event_id,
                    reason="duplicate_without_matching_receipt",
                    expected_hash=event_hash or sequence_hash,
                    received_hash=envelope.envelope_hash,
                    evidence={"received_from_site": received_from_site},
                )
            )
            return {
                "event_id": envelope.event_id,
                "status": "quarantined",
                "reason": "duplicate_without_matching_receipt",
            }
        # A retry is also the authenticated applied-receipt query.  The sender
        # may retire its delivery only after this destination transaction has
        # committed the business projection, never merely after durable input.
        result = {
            "event_id": envelope.event_id,
            "status": existing_receipt.status,
            "envelope_hash": envelope.envelope_hash,
            "contiguous_received_sequence": int(checkpoint.contiguous_received_sequence),
            "contiguous_applied_sequence": int(checkpoint.contiguous_applied_sequence),
        }
        if existing_receipt.status == "applied":
            checkpoint_evidence = {
                "destination_site": local_site,
                "origin_physical_site": envelope.origin_physical_site,
                "producer_epoch": envelope.producer_epoch,
                "contiguous_applied_sequence": int(
                    checkpoint.contiguous_applied_sequence
                ),
                "event_id": envelope.event_id,
                "envelope_hash": envelope.envelope_hash,
            }
            result["applied_checkpoint"] = {
                **checkpoint_evidence,
                "checkpoint_hash": hashlib.sha256(
                    json.dumps(
                        checkpoint_evidence,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
        if existing_receipt.status == "quarantined":
            result["reason"] = "destination_projection_quarantined"
        return result
    if decision.action == "quarantine":
        session.add(
            DrConflictQuarantine(
                quarantine_id=str(uuid4()),
                destination_site=local_site,
                origin_physical_site=envelope.origin_physical_site,
                producer_epoch=envelope.producer_epoch,
                producer_sequence=destination_sequence,
                event_id=envelope.event_id,
                reason=decision.reason,
                expected_hash=event_hash or sequence_hash,
                received_hash=envelope.envelope_hash,
                evidence={"received_from_site": received_from_site},
            )
        )
        return {"event_id": envelope.event_id, "status": "quarantined", "reason": decision.reason}

    await session.execute(insert(DrEvent).values(**_event_values(envelope)))
    status = "blocked_gap" if decision.action == "blocked_gap" else "received"
    session.add(
        DrEventReceipt(
            event_id=envelope.event_id,
            destination_site=local_site,
            origin_physical_site=envelope.origin_physical_site,
            producer_epoch=envelope.producer_epoch,
            producer_sequence=destination_sequence,
            envelope_hash=envelope.envelope_hash,
            received_from_site=received_from_site,
            relay_site=(
                received_from_site
                if received_from_site != envelope.origin_physical_site
                else None
            ),
            status=status,
        )
    )
    for destination in relay_destinations(
        envelope.origin_physical_site,
        local_site,
        aggregate_type=str(envelope.payload["aggregate_type"]),
    ):
        if int(envelope.payload["protocol_version"]) >= 2 and destination not in envelope.payload[
            "destination_streams"
        ]:
            raise DrEventReceiveError("relay destination is absent from the signed destination stream")
        session.add(
            DrEventDelivery(
                event_id=envelope.event_id,
                destination_site=destination,
                status="blocked_gap" if decision.action == "blocked_gap" else "pending",
                attempt_count=0,
                relay_site=local_site,
            )
        )
    await session.flush()
    await _advance_received_checkpoint(session, checkpoint)
    result = {
        "event_id": envelope.event_id,
        "status": status,
        "envelope_hash": envelope.envelope_hash,
        "contiguous_received_sequence": int(checkpoint.contiguous_received_sequence),
    }
    if decision.action == "blocked_gap":
        result["missing"] = {"from": decision.missing_from, "to": decision.missing_to}
    return result


async def receive_batch(
    session: AsyncSession,
    *,
    raw_envelopes: list[dict[str, Any]],
    local_site: str,
    request: ValidatedDrRequest,
    nonce_ttl_seconds: int,
) -> dict[str, Any]:
    if not raw_envelopes or len(raw_envelopes) > 500:
        raise DrEventReceiveError("DR batch size must be between 1 and 500")
    now = datetime.now(timezone.utc)
    await reserve_replay_nonce(
        session,
        request=request,
        expires_at=now + timedelta(seconds=max(60, int(nonce_ttl_seconds))),
    )
    envelopes = [validate_envelope(item) for item in raw_envelopes]
    envelopes.sort(key=lambda item: (item.origin_physical_site, item.producer_epoch, item.producer_sequence))
    results = []
    for envelope in envelopes:
        results.append(
            await receive_envelope(
                session,
                envelope=envelope,
                local_site=local_site,
                received_from_site=request.source_site,
            )
        )
    acknowledgement = {
        "destination_site": local_site,
        "source_site": request.source_site,
        "key_id": request.key_id,
        "request_hash": request.request_hash,
        "results": results,
    }
    acknowledgement["acknowledgement_hash"] = hashlib.sha256(
        json.dumps(acknowledgement, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return acknowledgement
