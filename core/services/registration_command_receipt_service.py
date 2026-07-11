"""No-commit command receipt primitives for authoritative registration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.registration_contracts import (
    TelegramRegistrationCommand,
    TelegramRegistrationOutcome,
    invitation_token_hash,
    registration_command_hash,
)
from core.server_routing import SERVER_FOREIGN
from models.telegram_registration_command_receipt import TelegramRegistrationCommandReceipt


class RegistrationCommandReplayConflict(RuntimeError):
    pass


def registration_command_lock_keys(
    *,
    command_id: UUID,
    idempotency_key: str,
) -> tuple[str, str]:
    def lock_key(namespace: str, value: object) -> str:
        digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).hexdigest()
        return f"telegram-registration:{digest}"

    return tuple(
        sorted(
            (
                lock_key("command", command_id),
                lock_key("idempotency", idempotency_key),
            )
        )
    )


async def acquire_registration_command_locks(
    db: AsyncSession,
    *,
    command_id: UUID,
    idempotency_key: str,
) -> None:
    for lock_key in registration_command_lock_keys(
        command_id=command_id,
        idempotency_key=idempotency_key,
    ):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )


async def load_registration_command_receipt(
    db: AsyncSession,
    *,
    command_id: UUID,
    idempotency_key: str,
) -> TelegramRegistrationCommandReceipt | None:
    stmt = select(TelegramRegistrationCommandReceipt).where(
        or_(
            TelegramRegistrationCommandReceipt.command_id == command_id,
            TelegramRegistrationCommandReceipt.idempotency_key == idempotency_key,
        )
    )
    receipts = list((await db.execute(stmt)).scalars().all())
    if not receipts:
        return None
    first = receipts[0]
    if len(receipts) != 1 or first.command_id != command_id or first.idempotency_key != idempotency_key:
        raise RegistrationCommandReplayConflict("command_identity_conflict")
    return first


async def prepare_registration_command_receipt(
    db: AsyncSession,
    *,
    command: TelegramRegistrationCommand,
    source_server: str,
) -> tuple[TelegramRegistrationCommandReceipt, bool]:
    if str(source_server or "").strip().lower() != SERVER_FOREIGN:
        raise RegistrationCommandReplayConflict("source_server_forbidden")

    await acquire_registration_command_locks(
        db,
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
    )
    request_hash = registration_command_hash(command)
    existing = await load_registration_command_receipt(
        db,
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise RegistrationCommandReplayConflict(
                TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY.value
            )
        return existing, True

    receipt = TelegramRegistrationCommandReceipt(
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        request_hash=request_hash,
        invitation_token_hash=invitation_token_hash(command.invitation_token),
        source_server=SERVER_FOREIGN,
    )
    db.add(receipt)
    await db.flush()
    return receipt, False


def finalize_registration_command_receipt(
    receipt: TelegramRegistrationCommandReceipt,
    *,
    outcome: TelegramRegistrationOutcome,
    authoritative_user_id: int | None,
    completed_at: datetime | None = None,
) -> None:
    receipt.outcome_code = outcome.value
    receipt.authoritative_user_id = authoritative_user_id
    receipt.completed_at = completed_at or datetime.now(timezone.utc)
