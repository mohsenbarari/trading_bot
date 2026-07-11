"""Foreign-local durable intent and projection primitives for Telegram registration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.registration_contracts import TelegramRegistrationCommand, TelegramRegistrationOutcome
from core.registration_identity import normalize_account_name, normalize_mobile_number
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.bot_access_policy import evaluate_bot_access_projection
from core.utils import utc_now
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.invitation import Invitation, InvitationKind
from models.telegram_registration_intent import (
    TelegramRegistrationIntent,
    TelegramRegistrationIntentStatus,
)
from models.user import User


_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_.:-]{1,96}$")
TERMINAL_INTENT_STATUSES = frozenset(
    {
        TelegramRegistrationIntentStatus.RECONCILED_CREATED,
        TelegramRegistrationIntentStatus.RECONCILED_LINKED_EXISTING,
        TelegramRegistrationIntentStatus.RECONCILED_ALREADY_LINKED,
        TelegramRegistrationIntentStatus.REJECTED,
        TelegramRegistrationIntentStatus.EXPIRED,
    }
)
SUCCESS_OUTCOME_TO_STATUS = {
    TelegramRegistrationOutcome.CREATED: TelegramRegistrationIntentStatus.RECONCILED_CREATED,
    TelegramRegistrationOutcome.LINKED_EXISTING: TelegramRegistrationIntentStatus.RECONCILED_LINKED_EXISTING,
    TelegramRegistrationOutcome.ALREADY_LINKED: TelegramRegistrationIntentStatus.RECONCILED_ALREADY_LINKED,
}


class TelegramRegistrationIntentError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TelegramRegistrationIntentCreationResult:
    intent: TelegramRegistrationIntent
    created: bool


@dataclass(frozen=True, slots=True)
class TelegramRegistrationIntentAttempt:
    intent_id: UUID
    attempt: int
    command: TelegramRegistrationCommand


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_code(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if _SAFE_ERROR_CODE.fullmatch(normalized) else "internal_error"


def _intent_identity(
    *,
    invitation_token: str,
    telegram_id: int,
) -> tuple[UUID, str]:
    token_digest = hashlib.sha256(str(invitation_token or "").encode("utf-8")).hexdigest()
    material = f"{token_digest}:{int(telegram_id)}"
    command_id = uuid5(NAMESPACE_URL, f"trading-bot:telegram-registration:{material}")
    key_digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return command_id, f"telegram-registration:{key_digest[:40]}"


async def get_registration_intent_for_invitation(
    db: AsyncSession,
    *,
    invitation_token: str,
    telegram_id: int,
) -> TelegramRegistrationIntent | None:
    """Read the deterministic foreign-local intent without changing retry state."""

    if current_server() != SERVER_FOREIGN:
        raise TelegramRegistrationIntentError("foreign_authority_required")
    command_id, idempotency_key = _intent_identity(
        invitation_token=invitation_token,
        telegram_id=telegram_id,
    )
    intent = (
        await db.execute(
            select(TelegramRegistrationIntent).where(
                TelegramRegistrationIntent.id == command_id,
                TelegramRegistrationIntent.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if intent is not None and (
        intent.invitation_token != invitation_token
        or int(intent.telegram_id) != int(telegram_id)
    ):
        raise TelegramRegistrationIntentError("intent_identity_conflict")
    return intent


def intent_to_command(intent: TelegramRegistrationIntent) -> TelegramRegistrationCommand:
    if (
        intent.address is None
        or intent.contact_verified_at is None
        or intent.completed_at is None
    ):
        raise TelegramRegistrationIntentError("intent_not_ready")
    return TelegramRegistrationCommand(
        command_id=intent.id,
        idempotency_key=intent.idempotency_key,
        invitation_token=intent.invitation_token,
        mobile_number=intent.normalized_mobile,
        telegram_id=intent.telegram_id,
        telegram_username=intent.telegram_username,
        telegram_full_name=intent.telegram_full_name,
        address=intent.address,
        contact_verified_at=_utc(intent.contact_verified_at),
        local_completed_at=_utc(intent.completed_at),
        invitation_expires_at_snapshot=_utc(intent.invitation_expires_at_snapshot),
    )


def _intent_matches_command(
    intent: TelegramRegistrationIntent,
    command: TelegramRegistrationCommand,
) -> bool:
    try:
        existing = intent_to_command(intent)
    except (TelegramRegistrationIntentError, ValueError):
        return False
    existing_payload = existing.model_dump(mode="json")
    candidate_payload = command.model_dump(mode="json")
    for snapshot_field in (
        "telegram_username",
        "telegram_full_name",
        "contact_verified_at",
        "local_completed_at",
    ):
        existing_payload.pop(snapshot_field, None)
        candidate_payload.pop(snapshot_field, None)
    return existing_payload == candidate_payload


async def create_or_reuse_ready_registration_intent(
    db: AsyncSession,
    *,
    invitation_token: str,
    mobile_number: str,
    telegram_id: int,
    telegram_username: str | None,
    telegram_full_name: str | None,
    address: str,
    contact_verified_at: datetime,
    completed_at: datetime,
    invitation_expires_at_snapshot: datetime,
) -> TelegramRegistrationIntentCreationResult:
    """Create a ready intent without committing; caller commits before clearing bot FSM."""

    if current_server() != SERVER_FOREIGN:
        raise TelegramRegistrationIntentError("foreign_authority_required")
    command_id, idempotency_key = _intent_identity(
        invitation_token=invitation_token,
        telegram_id=telegram_id,
    )
    command = TelegramRegistrationCommand(
        command_id=command_id,
        idempotency_key=idempotency_key,
        invitation_token=invitation_token,
        mobile_number=mobile_number,
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        telegram_full_name=telegram_full_name,
        address=address,
        contact_verified_at=contact_verified_at,
        local_completed_at=completed_at,
        invitation_expires_at_snapshot=invitation_expires_at_snapshot,
    )
    lock_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"telegram-registration-intent:{lock_digest}"},
    )
    existing = (
        await db.execute(
            select(TelegramRegistrationIntent)
            .where(TelegramRegistrationIntent.idempotency_key == idempotency_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        if _intent_matches_command(existing, command):
            return TelegramRegistrationIntentCreationResult(intent=existing, created=False)
        raise TelegramRegistrationIntentError("changed_payload_replay")

    intent = TelegramRegistrationIntent(
        id=command.command_id,
        idempotency_key=command.idempotency_key,
        invitation_token=command.invitation_token,
        normalized_mobile=command.mobile_number,
        telegram_id=command.telegram_id,
        telegram_username=command.telegram_username,
        telegram_full_name=command.telegram_full_name,
        address=command.address,
        contact_verified_at=command.contact_verified_at,
        completed_at=command.local_completed_at,
        invitation_expires_at_snapshot=command.invitation_expires_at_snapshot,
        status=TelegramRegistrationIntentStatus.READY,
        retry_count=0,
        next_retry_at=None,
    )
    db.add(intent)
    await db.flush()
    return TelegramRegistrationIntentCreationResult(intent=intent, created=True)


async def claim_due_registration_intents(
    db: AsyncSession,
    *,
    limit: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> list[TelegramRegistrationIntentAttempt]:
    if current_server() != SERVER_FOREIGN:
        raise TelegramRegistrationIntentError("foreign_authority_required")
    claimed_at = _utc(now or utc_now())
    due = or_(
        and_(
            TelegramRegistrationIntent.status.in_(
                (
                    TelegramRegistrationIntentStatus.READY,
                    TelegramRegistrationIntentStatus.RETRY_WAIT,
                )
            ),
            or_(
                TelegramRegistrationIntent.next_retry_at.is_(None),
                TelegramRegistrationIntent.next_retry_at <= claimed_at,
            ),
        ),
        and_(
            TelegramRegistrationIntent.status == TelegramRegistrationIntentStatus.FORWARDING,
            TelegramRegistrationIntent.next_retry_at.is_not(None),
            TelegramRegistrationIntent.next_retry_at <= claimed_at,
        ),
    )
    stmt = (
        select(TelegramRegistrationIntent)
        .where(due)
        .order_by(
            TelegramRegistrationIntent.next_retry_at.asc().nullsfirst(),
            TelegramRegistrationIntent.created_at.asc(),
            TelegramRegistrationIntent.id.asc(),
        )
        .limit(max(1, int(limit)))
        .with_for_update(skip_locked=True)
    )
    intents = list((await db.execute(stmt)).scalars().all())
    attempts: list[TelegramRegistrationIntentAttempt] = []
    for intent in intents:
        intent.retry_count = int(intent.retry_count or 0) + 1
        intent.status = TelegramRegistrationIntentStatus.FORWARDING
        intent.next_retry_at = claimed_at + timedelta(seconds=max(1, int(lease_seconds)))
        intent.last_error_code = None
        try:
            command = intent_to_command(intent)
        except (TelegramRegistrationIntentError, ValueError):
            intent.status = TelegramRegistrationIntentStatus.REJECTED
            intent.next_retry_at = None
            intent.last_error_code = "invalid_local_intent"
            continue
        attempts.append(
            TelegramRegistrationIntentAttempt(
                intent_id=intent.id,
                attempt=intent.retry_count,
                command=command,
            )
        )
    await db.flush()
    return attempts


async def schedule_registration_intent_retry(
    db: AsyncSession,
    *,
    intent_id: UUID,
    attempt: int,
    error_code: str,
    next_retry_at: datetime,
    authoritative_user_id: int | None = None,
) -> bool:
    intent = (
        await db.execute(
            select(TelegramRegistrationIntent)
            .where(TelegramRegistrationIntent.id == intent_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if intent is None or intent.status in TERMINAL_INTENT_STATUSES:
        return False
    if intent.status != TelegramRegistrationIntentStatus.FORWARDING or intent.retry_count != int(attempt):
        return False
    intent.status = TelegramRegistrationIntentStatus.RETRY_WAIT
    intent.next_retry_at = _utc(next_retry_at)
    intent.last_error_code = _safe_code(error_code)
    if authoritative_user_id is not None:
        intent.authoritative_user_id = int(authoritative_user_id)
    await db.flush()
    return True


async def finalize_registration_intent(
    db: AsyncSession,
    *,
    intent_id: UUID,
    attempt: int,
    outcome: TelegramRegistrationOutcome,
    authoritative_user_id: int | None,
) -> bool:
    intent = (
        await db.execute(
            select(TelegramRegistrationIntent)
            .where(TelegramRegistrationIntent.id == intent_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if intent is None or intent.status in TERMINAL_INTENT_STATUSES:
        return False
    if intent.status != TelegramRegistrationIntentStatus.FORWARDING or intent.retry_count != int(attempt):
        return False
    success_status = SUCCESS_OUTCOME_TO_STATUS.get(outcome)
    if success_status is not None:
        if authoritative_user_id is None:
            raise TelegramRegistrationIntentError("success_user_missing")
        intent.status = success_status
        intent.authoritative_user_id = int(authoritative_user_id)
        intent.last_error_code = None
    else:
        if authoritative_user_id is not None:
            raise TelegramRegistrationIntentError("failure_user_present")
        intent.status = (
            TelegramRegistrationIntentStatus.EXPIRED
            if outcome == TelegramRegistrationOutcome.INVITATION_EXPIRED
            else TelegramRegistrationIntentStatus.REJECTED
        )
        intent.last_error_code = _safe_code(outcome.value)
    intent.next_retry_at = None
    await db.flush()
    return True


async def registration_projection_is_ready(
    db: AsyncSession,
    *,
    command: TelegramRegistrationCommand,
) -> bool:
    invitation = (
        await db.execute(select(Invitation).where(Invitation.token == command.invitation_token))
    ).scalar_one_or_none()
    if (
        invitation is None
        or not invitation.is_used
        or invitation.revoked_at is not None
        or invitation.completed_at is None
    ):
        return False
    users = list(
        (
            await db.execute(
                select(User).where(
                    or_(
                        User.normalized_mobile_number == command.mobile_number,
                        User.telegram_id == command.telegram_id,
                    )
                )
            )
        ).scalars().all()
    )
    exact_users = [
        user
        for user in users
        if normalize_mobile_number(user.mobile_number) == command.mobile_number
        and user.telegram_id == command.telegram_id
    ]
    if len(exact_users) != 1:
        return False
    user = exact_users[0]
    if (
        user.role != invitation.role
        or normalize_account_name(user.account_name)
        != normalize_account_name(invitation.account_name)
    ):
        return False

    accountant_relations = list(
        (
            await db.execute(
                select(AccountantRelation).where(
                    AccountantRelation.invitation_token == invitation.token,
                    AccountantRelation.status == AccountantRelationStatus.ACTIVE,
                    AccountantRelation.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    customer_relations = list(
        (
            await db.execute(
                select(CustomerRelation).where(
                    CustomerRelation.invitation_token == invitation.token,
                    CustomerRelation.status == CustomerRelationStatus.ACTIVE,
                    CustomerRelation.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    if len(accountant_relations) > 1 or len(customer_relations) > 1:
        return False

    try:
        kind = InvitationKind(str(getattr(invitation.kind, "value", invitation.kind)))
    except ValueError:
        return False
    if kind == InvitationKind.STANDARD:
        if accountant_relations or customer_relations:
            return False
        decision = evaluate_bot_access_projection(
            user,
            is_accountant=False,
            customer_relation_present=False,
            customer_tier=None,
        )
        return decision.allowed
    if kind == InvitationKind.CUSTOMER:
        if accountant_relations or len(customer_relations) != 1:
            return False
        relation = customer_relations[0]
        if relation.customer_user_id != user.id:
            return False
        decision = evaluate_bot_access_projection(
            user,
            is_accountant=False,
            customer_relation_present=True,
            customer_tier=relation.customer_tier,
        )
        return decision.allowed
    return False
