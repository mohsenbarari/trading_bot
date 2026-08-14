"""Shared authority-aware offer expiry command service."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import current_server, normalize_server
from core.utils import utc_now_naive
from models.offer import Offer, OfferStatus


class OfferExpirySourceSurface(str, Enum):
    WEBAPP = "webapp"
    TELEGRAM_BOT = "telegram_bot"
    SYSTEM = "system"
    ADMIN = "admin"
    INTERNAL_FORWARD = "internal_forward"


class OfferExpiryReason:
    MANUAL = "manual"
    CANCEL_ALL = "cancel_all"
    BOT_CANCEL_ALL = "bot_cancel_all"
    TIME_LIMIT = "time_limit"
    MARKET_CLOSED = "market_closed"
    REPUBLISHED = "republished"
    TELEGRAM_SEND_FAILED = "telegram_send_failed"
    RECOVERY_FINALIZATION = "recovery_finalization"
    ADMIN_ACTION = "admin_action"
    USER_DELETED = "user_deleted"


class OfferExpiryError(ValueError):
    pass


class OfferNotAuthoritativeError(OfferExpiryError):
    def __init__(self, home_server: str):
        super().__init__("offer must be expired on its home server")
        self.home_server = home_server


class OfferAlreadyInactiveError(OfferExpiryError):
    pass


class OfferExpiryLockBusyError(OfferExpiryError):
    pass


@dataclass(frozen=True)
class OfferExpiryCommand:
    reason: str
    source_surface: OfferExpirySourceSurface | str
    source_server: str | None = None
    expired_by_user_id: int | None = None
    expired_by_actor_user_id: int | None = None
    require_active: bool = True


@dataclass(frozen=True)
class OfferExpiryResult:
    expired_offers: tuple[Offer, ...]

    @property
    def expired_count(self) -> int:
        return len(self.expired_offers)


def normalize_expire_source_surface(value: OfferExpirySourceSurface | str) -> str:
    if isinstance(value, OfferExpirySourceSurface):
        return value.value
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("expire source_surface is required")
    return normalized


def _command_source_server(command: OfferExpiryCommand) -> str:
    return normalize_server(command.source_server, current_server())


def is_offer_expiry_lock_busy(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    if code == "55P03":
        return True
    return "could not obtain lock" in str(exc).lower()


def ensure_offer_expiry_authority(offer: Offer) -> None:
    home_server = normalize_server(getattr(offer, "home_server", None), current_server())
    if home_server != current_server():
        raise OfferNotAuthoritativeError(home_server)


def apply_offer_expiry(
    offer: Offer,
    command: OfferExpiryCommand,
    *,
    now=None,
    require_authority: bool = True,
) -> Offer:
    if require_authority:
        ensure_offer_expiry_authority(offer)
    if command.require_active and getattr(offer, "status", None) != OfferStatus.ACTIVE:
        raise OfferAlreadyInactiveError("offer is not active")

    expired_at = now or utc_now_naive()
    offer.status = OfferStatus.EXPIRED
    offer.expired_at = expired_at
    offer.expire_reason = command.reason
    offer.expired_by_user_id = command.expired_by_user_id
    offer.expired_by_actor_user_id = command.expired_by_actor_user_id
    offer.expire_source_surface = normalize_expire_source_surface(command.source_surface)
    offer.expire_source_server = _command_source_server(command)
    return offer


async def _invalidate_overtime_after_offer_expiry(
    db: AsyncSession,
    offers: Iterable[Offer],
    *,
    reason: str,
    now=None,
) -> None:
    """Drop nonterminal overtime rows for offers that just became terminal.

    All offers in the command are invalidated first; owner-seat promotion runs
    once per freed scope afterward so a queued sibling offer can advance only
    if it remains valid under ``promote_next_for_owner`` revalidation.
    """
    from core.services.offer_overtime_request_service import (
        invalidate_overtime_requests_for_offer,
        promote_next_for_owner,
    )
    from core.server_routing import normalize_server
    from core.trading_settings import get_trading_settings_async

    offer_list = [offer for offer in offers if offer is not None]
    if not offer_list:
        return
    ts = await get_trading_settings_async()
    normal_minutes = int(getattr(ts, "offer_expiry_minutes", 0) or 0)
    scopes: set[tuple[int, str]] = set()
    for offer in offer_list:
        rows = await invalidate_overtime_requests_for_offer(
            db,
            offer,
            reason=reason,
            now=now,
            promote_next=False,
            normal_lifetime_minutes=normal_minutes,
            flush=True,
        )
        for row in rows:
            owner_id = getattr(row, "offer_owner_user_id", None)
            home = getattr(row, "request_home_server", None)
            if owner_id is not None and home:
                scopes.add((int(owner_id), normalize_server(home, current_server())))
    for owner_id, home in sorted(scopes):
        await promote_next_for_owner(
            db,
            request_home_server=home,
            offer_owner_user_id=owner_id,
            normal_lifetime_minutes=normal_minutes,
            now=now,
            flush=True,
        )


async def expire_offer_authoritatively(
    db: AsyncSession,
    offer: Offer,
    command: OfferExpiryCommand,
    *,
    commit: bool = True,
    now=None,
    require_authority: bool = True,
) -> OfferExpiryResult:
    expiry_time = now or utc_now_naive()
    apply_offer_expiry(offer, command, now=expiry_time, require_authority=require_authority)
    await _invalidate_overtime_after_offer_expiry(
        db,
        (offer,),
        reason=f"offer_expired:{command.reason}",
        now=expiry_time,
    )
    if commit:
        await db.commit()
    return OfferExpiryResult(expired_offers=(offer,))


async def expire_offers_authoritatively(
    db: AsyncSession,
    offers: Iterable[Offer],
    command: OfferExpiryCommand,
    *,
    commit: bool = True,
    now=None,
    require_authority: bool = True,
) -> OfferExpiryResult:
    expired = []
    expiry_time = now or utc_now_naive()
    for offer in offers:
        if getattr(offer, "status", None) != OfferStatus.ACTIVE:
            if command.require_active:
                raise OfferAlreadyInactiveError("offer is not active")
            continue
        apply_offer_expiry(offer, command, now=expiry_time, require_authority=require_authority)
        expired.append(offer)
    if expired:
        await _invalidate_overtime_after_offer_expiry(
            db,
            expired,
            reason=f"offer_expired:{command.reason}",
            now=expiry_time,
        )
    if expired and commit:
        await db.commit()
    return OfferExpiryResult(expired_offers=tuple(expired))
