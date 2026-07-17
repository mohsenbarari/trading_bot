"""Repeatable-offer eligibility and immutable republish provenance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from core.offer_settlement import settlement_type_value
from core.server_routing import KNOWN_SERVERS
from core.services.market_transition_service import (
    evaluate_current_market_schedule,
    get_market_runtime_state,
)
from core.utils import utc_now_naive
from models.offer import Offer, OfferStatus


REPEATABLE_EXPIRE_REASONS = ("time_limit", "manual")


class OfferNotRepeatableError(ValueError):
    """Raised when an offer no longer satisfies the repeat contract."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class OfferRepeatWindow:
    opened_at: datetime
    recent_cutoff: datetime


def offer_remaining_quantity(offer: Offer | object) -> int:
    remaining = getattr(offer, "remaining_quantity", None)
    if remaining is None:
        remaining = getattr(offer, "quantity", 0)
    try:
        return int(remaining or 0)
    except (TypeError, ValueError):
        return 0


def offer_remaining_lot_sizes(offer: Offer | object) -> list[int] | None:
    if bool(getattr(offer, "is_wholesale", True)):
        return None
    lots = getattr(offer, "lot_sizes", None)
    if not lots:
        return None
    return [int(value) for value in lots]


async def load_offer_repeat_window(
    db: AsyncSession,
    *,
    since_hours: int = 1,
    market_is_open: bool | None = None,
) -> OfferRepeatWindow | None:
    if market_is_open is None:
        evaluation = await evaluate_current_market_schedule(db)
        market_is_open = bool(evaluation.is_open)
    if not market_is_open:
        return None

    state = await get_market_runtime_state(db)
    opened_at = getattr(state, "last_transition_at", None) if state is not None else None
    if state is None or not bool(getattr(state, "is_open", False)) or opened_at is None:
        return None

    return OfferRepeatWindow(
        opened_at=opened_at,
        recent_cutoff=utc_now_naive() - timedelta(hours=since_hours),
    )


def repeatable_offer_conditions(
    *,
    owner_user_id: int,
    window: OfferRepeatWindow,
    replacement_home_server: str,
) -> Sequence[object]:
    replacement = aliased(Offer)
    terminal_at = func.coalesce(Offer.expired_at, Offer.updated_at, Offer.created_at)
    replacement_conditions = [
        replacement.republished_from_offer_public_id == Offer.offer_public_id,
    ]
    normalized_replacement_home = str(replacement_home_server or "").strip().lower()
    if normalized_replacement_home not in KNOWN_SERVERS:
        raise ValueError("replacement_home_server must be 'iran' or 'foreign'")
    replacement_conditions.append(replacement.home_server == normalized_replacement_home)
    return (
        Offer.user_id == owner_user_id,
        Offer.status == OfferStatus.EXPIRED,
        Offer.expire_reason.in_(REPEATABLE_EXPIRE_REASONS),
        Offer.created_at >= window.opened_at,
        terminal_at >= window.opened_at,
        terminal_at >= window.recent_cutoff,
        func.coalesce(Offer.remaining_quantity, Offer.quantity) > 0,
        or_(Offer.archived.is_(False), Offer.archived.is_(None)),
        Offer.republished_offer_id.is_(None),
        ~select(replacement.id).where(*replacement_conditions).exists(),
    )


async def list_repeatable_offers(
    db: AsyncSession,
    *,
    owner_user_id: int,
    limit: int = 3,
    since_hours: int = 1,
    options: Sequence[object] = (),
    replacement_home_server: str,
) -> list[Offer]:
    window = await load_offer_repeat_window(db, since_hours=since_hours)
    if window is None:
        return []

    terminal_at = func.coalesce(Offer.expired_at, Offer.updated_at, Offer.created_at)
    stmt = (
        select(Offer)
        .options(*options)
        .where(
            *repeatable_offer_conditions(
                owner_user_id=owner_user_id,
                window=window,
                replacement_home_server=replacement_home_server,
            )
        )
        .order_by(terminal_at.desc(), Offer.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def lock_repeatable_offer(
    db: AsyncSession,
    *,
    owner_user_id: int,
    offer_public_id: str,
    expected_local_id: int | None,
    market_is_open: bool,
    since_hours: int = 1,
    replacement_home_server: str,
) -> Offer:
    window = await load_offer_repeat_window(
        db,
        since_hours=since_hours,
        market_is_open=market_is_open,
    )
    if window is None:
        raise OfferNotRepeatableError("market_session_unavailable")

    stmt = (
        select(Offer)
        .where(
            Offer.offer_public_id == offer_public_id,
            *repeatable_offer_conditions(
                owner_user_id=owner_user_id,
                window=window,
                replacement_home_server=replacement_home_server,
            ),
        )
        .with_for_update()
    )
    offer = (await db.execute(stmt)).scalar_one_or_none()
    if offer is None:
        raise OfferNotRepeatableError("offer_ineligible")
    if expected_local_id is not None and int(offer.id) != int(expected_local_id):
        raise OfferNotRepeatableError("offer_identity_mismatch")
    return offer


def ensure_republish_payload_matches_source(
    source: Offer | object,
    *,
    offer_type: str,
    settlement_type: str,
    commodity_id: int,
    quantity: int,
    price: int,
    is_wholesale: bool,
    lot_sizes: Sequence[int] | None,
    notes: str | None,
) -> None:
    source_offer_type = str(
        getattr(getattr(source, "offer_type", None), "value", getattr(source, "offer_type", ""))
    )
    expected_lots = offer_remaining_lot_sizes(source)
    submitted_lots = [int(value) for value in lot_sizes] if lot_sizes else None
    mismatches = []

    if source_offer_type != str(offer_type):
        mismatches.append("offer_type")
    if settlement_type_value(getattr(source, "settlement_type", None)) != str(settlement_type).lower():
        mismatches.append("settlement_type")
    if int(getattr(source, "commodity_id", 0) or 0) != int(commodity_id):
        mismatches.append("commodity_id")
    if offer_remaining_quantity(source) != int(quantity):
        mismatches.append("quantity")
    if int(getattr(source, "price", 0) or 0) != int(price):
        mismatches.append("price")
    if bool(getattr(source, "is_wholesale", True)) != bool(is_wholesale):
        mismatches.append("is_wholesale")
    if expected_lots != submitted_lots:
        mismatches.append("lot_sizes")
    if getattr(source, "notes", None) != notes:
        mismatches.append("notes")

    if not bool(getattr(source, "is_wholesale", True)) and not expected_lots:
        raise OfferNotRepeatableError("source_lot_quantity_mismatch")
    if expected_lots is not None and sum(expected_lots) != offer_remaining_quantity(source):
        raise OfferNotRepeatableError("source_lot_quantity_mismatch")
    if mismatches:
        raise OfferNotRepeatableError("payload_mismatch:" + ",".join(mismatches))
