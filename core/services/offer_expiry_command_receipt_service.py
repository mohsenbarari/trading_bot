"""No-commit receipt primitives for authoritative offer-expiry commands."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.offer_expiry_command_receipt import OfferExpiryCommandReceipt


class OfferExpiryCommandReplayConflict(RuntimeError):
    pass


def offer_expiry_command_lock_keys(*, command_id: UUID, idempotency_key: str) -> tuple[str, str]:
    def lock_key(namespace: str, value: object) -> str:
        digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).hexdigest()
        return f"offer-expiry:{digest}"

    return tuple(sorted((lock_key("command", command_id), lock_key("idempotency", idempotency_key))))


async def acquire_offer_expiry_command_locks(
    db: AsyncSession,
    *,
    command_id: UUID,
    idempotency_key: str,
) -> None:
    for lock_key in offer_expiry_command_lock_keys(command_id=command_id, idempotency_key=idempotency_key):
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
    stmt = select(OfferExpiryCommandReceipt).where(
        or_(
            OfferExpiryCommandReceipt.command_id == command_id,
            OfferExpiryCommandReceipt.idempotency_key == idempotency_key,
        )
    )
    receipts = list((await db.execute(stmt)).scalars().all())
    if not receipts:
        return None
    first = receipts[0]
    if len(receipts) != 1 or first.command_id != command_id or first.idempotency_key != idempotency_key:
        raise OfferExpiryCommandReplayConflict("command_identity_conflict")
    return first


async def prepare_offer_expiry_command_receipt(
    db: AsyncSession,
    *,
    command_id: UUID,
    idempotency_key: str,
    request_hash: str,
    offer_public_id: str,
    replacement_offer_public_id: str | None,
    source_server: str,
    source_surface: str,
    expire_reason: str,
) -> tuple[OfferExpiryCommandReceipt, bool]:
    if len(str(request_hash or "")) != 64:
        raise OfferExpiryCommandReplayConflict("invalid_command_hash")

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
        return existing, True

    receipt = OfferExpiryCommandReceipt(
        command_id=command_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        offer_public_id=offer_public_id,
        replacement_offer_public_id=replacement_offer_public_id,
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
    outcome_code: str,
    completed_at: datetime | None = None,
) -> None:
    receipt.outcome_code = str(outcome_code or "").strip()
    if not receipt.outcome_code:
        raise ValueError("outcome_code is required")
    receipt.completed_at = completed_at or datetime.now(timezone.utc)
