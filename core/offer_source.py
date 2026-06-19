"""Offer creation source-surface authority helpers."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from core.server_routing import KNOWN_SERVERS, SERVER_FOREIGN, SERVER_IRAN, normalize_server


class OfferSourceSurface(str, Enum):
    TELEGRAM_BOT = "telegram_bot"
    WEBAPP = "webapp"
    INTERNAL_SYNC = "internal_sync"


def normalize_offer_source_surface(value: Optional[str | OfferSourceSurface]) -> OfferSourceSurface:
    if isinstance(value, OfferSourceSurface):
        return value
    if value is None:
        raise ValueError("source_surface is required")
    normalized = str(value).strip().lower()
    if not normalized:
        raise ValueError("source_surface is required")
    try:
        return OfferSourceSurface(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported source_surface: {value}") from exc


def offer_home_server_for_source(
    source_surface: Optional[str | OfferSourceSurface],
    *,
    incoming_home_server: Optional[str] = None,
) -> str:
    surface = normalize_offer_source_surface(source_surface)
    if surface == OfferSourceSurface.TELEGRAM_BOT:
        return SERVER_FOREIGN
    if surface == OfferSourceSurface.WEBAPP:
        return SERVER_IRAN

    normalized_home = normalize_server(incoming_home_server, default="")
    if normalized_home not in KNOWN_SERVERS:
        raise ValueError("internal_sync offers require a valid incoming home_server")
    return normalized_home
