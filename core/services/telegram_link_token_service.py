"""WebApp-issued Telegram account-link token service."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.server_routing import SERVER_IRAN, current_server
from core.services.bot_access_policy import (
    BOT_ACCESS_REASON_SYNC_PENDING,
    BotAccessDecision,
    evaluate_bot_access,
    normalize_telegram_datetime,
)
from core.services.invitation_identity_reservation_service import (
    acquire_invitation_transition_locks,
    normalize_invitation_identity,
)
from core.utils import utc_now
from models.telegram_link_token import TelegramLinkToken, TelegramLinkTokenStatus
from models.user import User, set_legacy_has_bot_access_compatibility


TELEGRAM_LINK_TOKEN_TTL_SECONDS = 600


class TelegramLinkTokenError(PermissionError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class TelegramLinkTokenIssueResult:
    token: str
    token_hash: str
    record: TelegramLinkToken


def hash_telegram_link_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_telegram_link_token() -> str:
    return secrets.token_urlsafe(32)


def build_telegram_start_parameter(token: str) -> str:
    return f"link_{token}"


def build_telegram_deep_link(bot_username: str, token: str) -> str:
    username = bot_username.strip().lstrip("@")
    return f"https://t.me/{username}?start={build_telegram_start_parameter(token)}"


async def create_telegram_link_token(
    db: AsyncSession,
    user: User,
    *,
    ttl_seconds: int = TELEGRAM_LINK_TOKEN_TTL_SECONDS,
) -> TelegramLinkTokenIssueResult:
    user_id = getattr(user, "id", None)
    if user_id is None:
        raise TelegramLinkTokenError(BOT_ACCESS_REASON_SYNC_PENDING)

    identity = normalize_invitation_identity(
        mobile_number=getattr(user, "mobile_number", None),
        account_name=getattr(user, "account_name", None),
    )
    await acquire_invitation_transition_locks(
        db,
        invitation_token=None,
        identity=identity,
        telegram_id=getattr(user, "telegram_id", None),
    )
    locked_user_stmt = (
        select(User)
        .where(User.id == int(user_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked_user = (await db.execute(locked_user_stmt)).scalar_one_or_none()
    if locked_user is None:
        raise TelegramLinkTokenError(BOT_ACCESS_REASON_SYNC_PENDING)
    user = locked_user

    decision = await evaluate_bot_access(db, user)
    if not decision.allowed:
        raise TelegramLinkTokenError(decision.reason or BOT_ACCESS_REASON_SYNC_PENDING)

    now = utc_now()
    revoke_stmt = (
        select(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == user.id,
            TelegramLinkToken.status == TelegramLinkTokenStatus.PENDING,
        )
        .order_by(TelegramLinkToken.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    existing_pending = list((await db.execute(revoke_stmt)).scalars().all())
    for record in existing_pending:
        record.status = TelegramLinkTokenStatus.REVOKED
        record.revoked_at = now

    token = generate_telegram_link_token()
    token_hash = hash_telegram_link_token(token)
    record = TelegramLinkToken(
        user_id=user.id,
        token_hash=token_hash,
        status=TelegramLinkTokenStatus.PENDING,
        issued_by_server=current_server(),
        expires_at=now + timedelta(seconds=max(60, int(ttl_seconds))),
    )
    db.add(record)
    await db.flush()
    return TelegramLinkTokenIssueResult(token=token, token_hash=token_hash, record=record)


async def load_pending_telegram_link_token_user_for_update(
    db: AsyncSession,
    raw_token: str,
) -> tuple[TelegramLinkToken, User, BotAccessDecision]:
    token_hash = hash_telegram_link_token(raw_token)
    token_probe = (
        await db.execute(
            select(TelegramLinkToken).where(TelegramLinkToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if token_probe is None:
        raise TelegramLinkTokenError("invalid")
    user_stmt = (
        select(User)
        .where(User.id == token_probe.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    stmt = (
        select(TelegramLinkToken)
        .where(TelegramLinkToken.token_hash == token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    token_record = (await db.execute(stmt)).scalar_one_or_none()
    if token_record is None or user is None or token_record.user_id != user.id:
        raise TelegramLinkTokenError("invalid")
    if token_record.status != TelegramLinkTokenStatus.PENDING:
        raise TelegramLinkTokenError(str(getattr(token_record.status, "value", token_record.status)))

    now = utc_now()
    expires_at = normalize_telegram_datetime(getattr(token_record, "expires_at", None))
    if expires_at is None or expires_at <= now:
        token_record.status = TelegramLinkTokenStatus.EXPIRED
        raise TelegramLinkTokenError("expired")

    decision = await evaluate_bot_access(db, user)
    if not decision.allowed:
        raise TelegramLinkTokenError(decision.reason or BOT_ACCESS_REASON_SYNC_PENDING)
    return token_record, user, decision


async def consume_telegram_link_token(
    db: AsyncSession,
    token_record: TelegramLinkToken,
    user: User,
    *,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
) -> None:
    if current_server() != SERVER_IRAN and settings.registration_sync_v2_enabled:
        raise TelegramLinkTokenError("iran_authority_required")
    duplicate_stmt = select(User).where(
        User.telegram_id == telegram_id,
        User.id != user.id,
    )
    duplicate_user = (await db.execute(duplicate_stmt)).scalar_one_or_none()
    if duplicate_user is not None:
        raise TelegramLinkTokenError("telegram_id_already_used")

    now = utc_now()
    user.telegram_id = telegram_id
    user.username = username
    if getattr(user, "full_name", None) == getattr(user, "account_name", None) and full_name:
        user.full_name = full_name
    set_legacy_has_bot_access_compatibility(user, enabled=True)
    token_record.status = TelegramLinkTokenStatus.USED
    token_record.used_at = now
    token_record.used_telegram_id = telegram_id
