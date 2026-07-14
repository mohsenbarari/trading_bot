"""No-commit receipt primitives for forwarded offer-expiry commands."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
from uuid import UUID

from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.offer_expiry_command_receipt import OfferExpiryCommandReceipt


OFFER_EXPIRY_COMMAND_RECEIPT_RETENTION_DAYS = 365


class OfferExpiryReceiptOutcome(str, Enum):
    EXPIRED = "expired"


class OfferExpiryCommandReplayConflict(RuntimeError):
    pass


class OfferExpiryCommandReceiptIncomplete(RuntimeError):
    pass


def offer_expiry_command_lock_keys(
    *,
    command_id: UUID,
    idempotency_key: str,
) -> tuple[str, str]:
    def lock_key(namespace: str, value: object) -> str:
        digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).hexdigest()
        return f"offer-expiry:{digest}"

    return tuple(
        sorted(
            (
                lock_key("command", command_id),
                lock_key("idempotency", idempotency_key),
            )
        )
    )


async def acquire_offer_expiry_command_locks(
    db: AsyncSession,
    *,
    command_id: UUID,
    idempotency_key: str,
) -> None:
    for lock_key in offer_expiry_command_lock_keys(
        command_id=command_id,
        idempotency_key=idempotency_key,
    ):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )


async def load_offer_expiry_command_receipt(
    db: AsyncSession,
    *,
    command_id: UUID,
    idempotency_key: str,
) -> OfferExpiryCommandReceipt | None:
    stmt = (
        select(OfferExpiryCommandReceipt)
        .where(
            or_(
                OfferExpiryCommandReceipt.command_id == command_id,
                OfferExpiryCommandReceipt.idempotency_key == idempotency_key,
            )
        )
        .with_for_update()
    )
    receipts = list((await db.execute(stmt)).scalars().all())
    if not receipts:
        return None
    receipt = receipts[0]
    if (
        len(receipts) != 1
        or receipt.command_id != command_id
        or receipt.idempotency_key != idempotency_key
    ):
        raise OfferExpiryCommandReplayConflict("command_identity_conflict")
    return receipt


async def prepare_offer_expiry_command_receipt(
    db: AsyncSession,
    *,
    command_id: UUID,
    idempotency_key: str,
    request_hash: str,
    offer_public_id: str,
    source_server: str,
    source_surface: str,
    expire_reason: str,
) -> tuple[OfferExpiryCommandReceipt, bool]:
    if len(request_hash) != 64:
        raise OfferExpiryCommandReplayConflict("invalid_request_hash")

    await acquire_offer_expiry_command_locks(
        db,
        command_id=command_id,
        idempotency_key=idempotency_key,
    )
    existing = await load_offer_expiry_command_receipt(
        db,
        command_id=command_id,
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise OfferExpiryCommandReplayConflict("changed_payload_replay")
        if (
            existing.offer_public_id != offer_public_id
            or existing.source_server != source_server
            or existing.source_surface != source_surface
            or existing.expire_reason != expire_reason
        ):
            raise OfferExpiryCommandReplayConflict("receipt_payload_conflict")
        if existing.completed_at is None or existing.outcome_code is None:
            raise OfferExpiryCommandReceiptIncomplete("receipt_incomplete")
        return existing, True

    receipt = OfferExpiryCommandReceipt(
        command_id=command_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        offer_public_id=offer_public_id,
        source_server=source_server,
        source_surface=source_surface,
        expire_reason=expire_reason,
    )
    db.add(receipt)
    await db.flush()
    return receipt, False


def finalize_offer_expiry_command_receipt(
    receipt: OfferExpiryCommandReceipt,
    *,
    outcome: OfferExpiryReceiptOutcome,
    completed_at: datetime | None = None,
) -> None:
    receipt.outcome_code = outcome.value
    receipt.completed_at = completed_at or datetime.now(timezone.utc)


def replay_offer_expiry_receipt_outcome(
    receipt: OfferExpiryCommandReceipt,
) -> OfferExpiryReceiptOutcome:
    if receipt.outcome_code is None or receipt.completed_at is None:
        raise OfferExpiryCommandReceiptIncomplete("receipt_incomplete")
    try:
        return OfferExpiryReceiptOutcome(receipt.outcome_code)
    except ValueError as exc:
        raise OfferExpiryCommandReceiptIncomplete("receipt_outcome_invalid") from exc


def offer_expiry_side_effect_dedupe_key(
    *,
    command_id: UUID,
    offer_public_id: str,
    offer_version: int,
) -> str:
    return f"offer-expiry-side-effects:v1:{command_id}:{offer_public_id}:v{int(offer_version)}"


def terminal_offer_expiry_receipt_cleanup_statement(
    *,
    current_time: datetime | None = None,
    retention_days: int = OFFER_EXPIRY_COMMAND_RECEIPT_RETENTION_DAYS,
):
    now = current_time or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(retention_days)))
    return delete(OfferExpiryCommandReceipt).where(
        OfferExpiryCommandReceipt.completed_at.is_not(None),
        OfferExpiryCommandReceipt.completed_at < cutoff,
    )
