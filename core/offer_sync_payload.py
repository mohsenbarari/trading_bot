"""Canonical sync payload builder for offers."""
from __future__ import annotations

from typing import Any


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _isoformat_or_none(value: Any) -> str | None:
    return value.isoformat() if value else None


def build_offer_sync_payload(offer: Any) -> dict[str, Any]:
    return {
        "id": offer.id,
        "offer_public_id": getattr(offer, "offer_public_id", None),
        "version_id": getattr(offer, "version_id", None) or 1,
        "user_id": offer.user_id,
        "actor_user_id": getattr(offer, "actor_user_id", None),
        "home_server": offer.home_server,
        "offer_type": _enum_value(offer.offer_type) if getattr(offer, "offer_type", None) else None,
        "settlement_type": _enum_value(getattr(offer, "settlement_type", None)),
        "commodity_id": offer.commodity_id,
        "quantity": offer.quantity,
        "remaining_quantity": offer.remaining_quantity,
        "price": offer.price,
        "exclude_from_competitive_price": getattr(offer, "exclude_from_competitive_price", False),
        "price_warning_type": getattr(offer, "price_warning_type", None),
        "is_wholesale": offer.is_wholesale,
        "lot_sizes": offer.lot_sizes,
        "original_lot_sizes": offer.original_lot_sizes,
        "expire_reason": getattr(offer, "expire_reason", None),
        "expired_by_user_id": getattr(offer, "expired_by_user_id", None),
        "expired_by_actor_user_id": getattr(offer, "expired_by_actor_user_id", None),
        "expire_source_surface": getattr(offer, "expire_source_surface", None),
        "expire_source_server": getattr(offer, "expire_source_server", None),
        "notes": offer.notes,
        "status": _enum_value(offer.status) if getattr(offer, "status", None) else None,
        "channel_message_id": offer.channel_message_id,
        "republished_offer_id": offer.republished_offer_id,
        "republished_offer_public_id": getattr(offer, "republished_offer_public_id", None),
        "created_at": _isoformat_or_none(offer.created_at),
        "updated_at": _isoformat_or_none(offer.updated_at),
        "expired_at": _isoformat_or_none(getattr(offer, "expired_at", None)),
        "idempotency_key": offer.idempotency_key,
        "archived": offer.archived,
    }
