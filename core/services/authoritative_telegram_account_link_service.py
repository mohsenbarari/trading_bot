"""Iran-owned transaction for legacy WebApp-to-Telegram account linking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.registration_contracts import TelegramRegistrationOutcome
from core.registration_identity import normalize_mobile_number
from core.server_routing import SERVER_IRAN, current_server
from core.services.authoritative_registration_service import (
    AuthoritativeRegistrationError,
    validate_current_telegram_eligibility,
)
from core.services.chat_room_service import ensure_mandatory_channel_membership
from core.services.invitation_identity_reservation_service import (
    acquire_invitation_transition_locks,
    normalize_invitation_identity,
)
from core.services.registration_command_receipt_service import (
    RegistrationCommandReplayConflict,
    finalize_registration_command_receipt,
    prepare_internal_registration_command_receipt,
)
from core.services.telegram_link_token_service import hash_telegram_link_token
from core.telegram_account_link_contracts import (
    TelegramAccountLinkCommand,
    account_link_command_hash,
    account_link_credential_hash,
)
from core.utils import utc_now
from models.telegram_link_token import TelegramLinkToken, TelegramLinkTokenStatus
from models.user import User, set_legacy_has_bot_access_compatibility


INCOMPLETE_ADDRESS_SENTINELS = {"System Default", "REGISTRATION_PENDING"}


@dataclass(frozen=True, slots=True)
class AuthoritativeTelegramAccountLinkResult:
    outcome: TelegramRegistrationOutcome
    authoritative_user_id: int | None
    replayed: bool = False
    first_terminal_transition: bool = False


class AuthoritativeTelegramAccountLinkError(RuntimeError):
    def __init__(self, outcome: TelegramRegistrationOutcome, detail: str):
        self.outcome = outcome
        self.detail = detail
        super().__init__(outcome.value)


def _error(outcome: TelegramRegistrationOutcome, detail: str) -> AuthoritativeTelegramAccountLinkError:
    return AuthoritativeTelegramAccountLinkError(outcome, detail)


def _address_is_incomplete(user: User) -> bool:
    address = str(getattr(user, "address", "") or "").strip()
    return not address or address in INCOMPLETE_ADDRESS_SENTINELS


def _receipt_result(receipt) -> AuthoritativeTelegramAccountLinkResult:
    if receipt.completed_at is None or receipt.outcome_code is None:
        raise RuntimeError("registration_receipt_incomplete")
    return AuthoritativeTelegramAccountLinkResult(
        outcome=TelegramRegistrationOutcome(receipt.outcome_code),
        authoritative_user_id=receipt.authoritative_user_id,
        replayed=True,
    )


async def _load_token_candidate(
    db: AsyncSession,
    command: TelegramAccountLinkCommand,
) -> tuple[TelegramLinkToken, User]:
    token_hash = hash_telegram_link_token(command.link_token or "")
    token_probe = (
        await db.execute(select(TelegramLinkToken).where(TelegramLinkToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if token_probe is None:
        raise _error(TelegramRegistrationOutcome.LINK_TOKEN_NOT_FOUND, "لینک اتصال نامعتبر است")
    user_probe = await db.get(User, token_probe.user_id)
    if user_probe is None:
        raise _error(
            TelegramRegistrationOutcome.AUTHORITATIVE_USER_MISSING,
            "حساب کاربری اتصال یافت نشد",
        )
    identity = normalize_invitation_identity(
        mobile_number=user_probe.mobile_number,
        account_name=user_probe.account_name,
    )
    await acquire_invitation_transition_locks(
        db,
        invitation_token=None,
        identity=identity,
        telegram_id=command.telegram_id,
    )
    user = (
        await db.execute(
            select(User)
            .where(User.id == token_probe.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if user is None:
        raise _error(
            TelegramRegistrationOutcome.AUTHORITATIVE_USER_MISSING,
            "حساب کاربری اتصال یافت نشد",
        )
    token = (
        await db.execute(
            select(TelegramLinkToken)
            .where(TelegramLinkToken.token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if token is None or token.user_id != user.id:
        raise _error(TelegramRegistrationOutcome.LINK_TOKEN_NOT_FOUND, "لینک اتصال نامعتبر است")
    return token, user


async def _load_existing_linked_user(
    db: AsyncSession,
    command: TelegramAccountLinkCommand,
) -> User:
    probe_stmt = select(User).where(
        or_(
            User.normalized_mobile_number == command.mobile_number,
            User.telegram_id == command.telegram_id,
        )
    )
    probes = list((await db.execute(probe_stmt)).scalars().all())
    exact = [
        user
        for user in probes
        if normalize_mobile_number(user.mobile_number) == command.mobile_number
        and user.telegram_id == command.telegram_id
    ]
    if len(exact) != 1:
        raise _error(TelegramRegistrationOutcome.IDENTITY_CONFLICT, "هویت حساب قابل تایید نیست")
    identity = normalize_invitation_identity(
        mobile_number=exact[0].mobile_number,
        account_name=exact[0].account_name,
    )
    await acquire_invitation_transition_locks(
        db,
        invitation_token=None,
        identity=identity,
        telegram_id=command.telegram_id,
    )
    user = (
        await db.execute(
            select(User)
            .where(User.id == exact[0].id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if normalize_mobile_number(user.mobile_number) != command.mobile_number or user.telegram_id != command.telegram_id:
        raise _error(TelegramRegistrationOutcome.IDENTITY_CONFLICT, "هویت حساب قابل تایید نیست")
    return user


async def _validate_token_state(
    token: TelegramLinkToken,
    user: User,
    command: TelegramAccountLinkCommand,
) -> TelegramRegistrationOutcome | None:
    now = utc_now()
    expires_at = token.expires_at
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if token.status == TelegramLinkTokenStatus.REVOKED:
        raise _error(TelegramRegistrationOutcome.LINK_TOKEN_REVOKED, "لینک اتصال لغو شده است")
    if token.status == TelegramLinkTokenStatus.EXPIRED or expires_at <= now:
        if token.status == TelegramLinkTokenStatus.PENDING:
            token.status = TelegramLinkTokenStatus.EXPIRED
        raise _error(TelegramRegistrationOutcome.LINK_TOKEN_EXPIRED, "لینک اتصال منقضی شده است")
    if token.status == TelegramLinkTokenStatus.USED:
        if token.used_telegram_id == command.telegram_id and user.telegram_id == command.telegram_id:
            return TelegramRegistrationOutcome.ALREADY_LINKED
        raise _error(
            TelegramRegistrationOutcome.LINK_TOKEN_ALREADY_USED,
            "لینک اتصال قبلاً استفاده شده است",
        )
    if token.status != TelegramLinkTokenStatus.PENDING:
        raise _error(TelegramRegistrationOutcome.INVALID_COMMAND, "وضعیت لینک اتصال نامعتبر است")
    return None


async def complete_authoritative_telegram_account_link(
    db: AsyncSession,
    *,
    command: TelegramAccountLinkCommand,
    source_server: str,
) -> AuthoritativeTelegramAccountLinkResult:
    if current_server() != SERVER_IRAN:
        raise RuntimeError("authoritative_telegram_account_link_requires_iran")

    receipt = None
    try:
        receipt, replayed = await prepare_internal_registration_command_receipt(
            db,
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            request_hash=account_link_command_hash(command),
            credential_hash=account_link_credential_hash(command),
            source_server=source_server,
        )
        if replayed:
            result = _receipt_result(receipt)
            await db.commit()
            return result

        token: TelegramLinkToken | None = None
        if command.mode == "link_token":
            token, user = await _load_token_candidate(db, command)
            replay_outcome = await _validate_token_state(token, user, command)
        else:
            user = await _load_existing_linked_user(db, command)
            replay_outcome = TelegramRegistrationOutcome.ALREADY_LINKED

        try:
            await validate_current_telegram_eligibility(db, user=user)
        except AuthoritativeRegistrationError as exc:
            raise _error(exc.outcome, exc.public_detail) from exc
        if normalize_mobile_number(user.mobile_number) != command.mobile_number:
            raise _error(
                TelegramRegistrationOutcome.CONTACT_MOBILE_MISMATCH,
                "شماره تماس با حساب انتخاب‌شده مطابقت ندارد",
            )

        duplicate = (
            await db.execute(
                select(User)
                .where(User.telegram_id == command.telegram_id, User.id != user.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise _error(
                TelegramRegistrationOutcome.TELEGRAM_ID_ALREADY_USED,
                "این حساب تلگرام قبلاً به کاربر دیگری متصل شده است",
            )
        if user.telegram_id not in (None, command.telegram_id):
            raise _error(
                TelegramRegistrationOutcome.TELEGRAM_ACCOUNT_CONFLICT,
                "این حساب به یک حساب تلگرام دیگر متصل است",
            )

        outcome = replay_outcome or TelegramRegistrationOutcome.LINKED_EXISTING
        if user.telegram_id is None:
            user.telegram_id = command.telegram_id
            user.username = command.telegram_username
        elif command.telegram_username is not None:
            user.username = command.telegram_username
        if command.address is not None and _address_is_incomplete(user):
            user.address = command.address
        set_legacy_has_bot_access_compatibility(user, enabled=True)
        await ensure_mandatory_channel_membership(db, user=user)

        if token is not None and token.status == TelegramLinkTokenStatus.PENDING:
            token.status = TelegramLinkTokenStatus.USED
            token.used_at = utc_now()
            token.used_telegram_id = command.telegram_id

        finalize_registration_command_receipt(
            receipt,
            outcome=outcome,
            authoritative_user_id=user.id,
        )
        await db.flush()
        await db.commit()
        return AuthoritativeTelegramAccountLinkResult(
            outcome=outcome,
            authoritative_user_id=user.id,
            first_terminal_transition=True,
        )
    except RegistrationCommandReplayConflict as exc:
        await db.rollback()
        outcome = (
            TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY
            if str(exc) == TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY.value
            else TelegramRegistrationOutcome.INVALID_COMMAND
        )
        return AuthoritativeTelegramAccountLinkResult(outcome=outcome, authoritative_user_id=None)
    except AuthoritativeTelegramAccountLinkError as exc:
        if receipt is None:
            await db.rollback()
            raise
        finalize_registration_command_receipt(
            receipt,
            outcome=exc.outcome,
            authoritative_user_id=None,
        )
        await db.flush()
        await db.commit()
        return AuthoritativeTelegramAccountLinkResult(
            outcome=exc.outcome,
            authoritative_user_id=None,
            first_terminal_transition=True,
        )
    except Exception:
        await db.rollback()
        raise
