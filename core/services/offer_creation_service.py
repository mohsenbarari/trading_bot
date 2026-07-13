"""Shared command surface for authoritative offer creation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import SettlementType
from core.offer_identity import generate_offer_public_id
from core.offer_settlement import normalize_settlement_type
from core.offer_source import OfferSourceSurface, normalize_offer_source_surface, offer_home_server_for_source
from models.offer import Offer, OfferStatus, OfferType


class OfferCreationValidationError(ValueError):
    """Raised when a shared offer creation command violates market rules."""


@dataclass(frozen=True)
class OfferCreationCommand:
    source_surface: OfferSourceSurface | str
    owner_user_id: int | None
    actor_user_id: int | None
    offer_type: OfferType | str
    commodity_id: int
    quantity: int
    price: int
    settlement_type: SettlementType | str = SettlementType.CASH
    is_wholesale: bool = True
    lot_sizes: Sequence[int] | None = None
    original_lot_sizes: Sequence[int] | None = None
    notes: str | None = None
    idempotency_key: str | None = None
    status: OfferStatus | str = OfferStatus.ACTIVE
    remaining_quantity: int | None = None
    exclude_from_competitive_price: bool = False
    price_warning_type: str | None = None
    incoming_home_server: str | None = None
    offer_public_id: str | None = None


def _normalize_offer_type(value: OfferType | str) -> OfferType:
    if isinstance(value, OfferType):
        return value
    return OfferType(str(value).strip().lower())


def _normalize_offer_status(value: OfferStatus | str) -> OfferStatus:
    if isinstance(value, OfferStatus):
        return value
    return OfferStatus(str(value).strip().lower())


def _list_or_none(value: Sequence[int] | None) -> list[int] | None:
    return list(value) if value is not None else None


async def validate_offer_creation_command(db: AsyncSession, command: OfferCreationCommand) -> None:
    source_surface = normalize_offer_source_surface(command.source_surface)
    if source_surface == OfferSourceSurface.INTERNAL_SYNC:
        return

    from core.services import trade_service

    is_valid_qty, err_qty = trade_service.validate_quantity(command.quantity)
    if not is_valid_qty:
        raise OfferCreationValidationError(err_qty)

    is_valid_price, err_price = trade_service.validate_price(command.price)
    if not is_valid_price:
        raise OfferCreationValidationError(err_price)

    if not command.is_wholesale:
        if not command.lot_sizes:
            raise OfferCreationValidationError("برای آفر خُرد باید لات‌ها مشخص شوند.")
        is_valid_lots, err_lots, _suggested_lots = trade_service.validate_lot_sizes(command.quantity, command.lot_sizes)
        if not is_valid_lots:
            raise OfferCreationValidationError(err_lots)

    is_valid_comp, err_comp = await trade_service.validate_competitive_price(
        db=db,
        offer_type=_normalize_offer_type(command.offer_type).value,
        commodity_id=command.commodity_id,
        quantity=command.quantity,
        proposed_price=command.price,
        user_id=command.owner_user_id,
    )
    if not is_valid_comp:
        raise OfferCreationValidationError(err_comp)


def build_authoritative_offer(command: OfferCreationCommand) -> Offer:
    source_surface = normalize_offer_source_surface(command.source_surface)
    home_server = offer_home_server_for_source(
        source_surface,
        incoming_home_server=command.incoming_home_server,
    )
    lot_sizes = _list_or_none(command.lot_sizes)
    original_lot_sizes = _list_or_none(command.original_lot_sizes)
    if original_lot_sizes is None:
        original_lot_sizes = _list_or_none(command.lot_sizes)

    offer_public_id = (command.offer_public_id or "").strip() or generate_offer_public_id()
    if source_surface == OfferSourceSurface.INTERNAL_SYNC and not command.offer_public_id:
        raise ValueError("internal_sync offers require incoming offer_public_id")

    return Offer(
        offer_public_id=offer_public_id,
        user_id=command.owner_user_id,
        actor_user_id=command.actor_user_id,
        home_server=home_server,
        offer_type=_normalize_offer_type(command.offer_type),
        settlement_type=normalize_settlement_type(command.settlement_type),
        commodity_id=command.commodity_id,
        quantity=command.quantity,
        remaining_quantity=command.remaining_quantity if command.remaining_quantity is not None else command.quantity,
        price=command.price,
        exclude_from_competitive_price=bool(command.exclude_from_competitive_price),
        price_warning_type=command.price_warning_type,
        is_wholesale=bool(command.is_wholesale),
        lot_sizes=lot_sizes,
        original_lot_sizes=original_lot_sizes,
        notes=command.notes,
        idempotency_key=command.idempotency_key,
        status=_normalize_offer_status(command.status),
    )


async def create_authoritative_offer(
    db: AsyncSession,
    command: OfferCreationCommand,
    *,
    commit: bool = True,
    refresh: bool = True,
    validate_market: bool = True,
) -> Offer:
    if validate_market:
        await validate_offer_creation_command(db, command)
    offer = build_authoritative_offer(command)
    db.add(offer)
    if commit:
        await db.commit()
        if refresh:
            await db.refresh(offer)
    return offer
