"""Stable public identifiers and links for offers."""
from __future__ import annotations

import secrets
from urllib.parse import quote

OFFER_PUBLIC_ID_PREFIX = "ofr"
OFFER_PUBLIC_ID_BYTES = 18
OFFER_PUBLIC_LINK_PATH = "/market"


def generate_offer_public_id() -> str:
    """Return an opaque URL-safe offer identifier that does not expose DB ids."""
    return f"{OFFER_PUBLIC_ID_PREFIX}_{secrets.token_urlsafe(OFFER_PUBLIC_ID_BYTES)}"


def ensure_offer_public_id(offer) -> str:
    offer_public_id = (getattr(offer, "offer_public_id", None) or getattr(offer, "public_id", None) or "").strip()
    if not offer_public_id:
        offer_public_id = generate_offer_public_id()
    setattr(offer, "offer_public_id", offer_public_id)
    return offer_public_id


def build_offer_public_path(offer_public_id: str) -> str:
    return f"{OFFER_PUBLIC_LINK_PATH}?offer={quote(offer_public_id, safe='')}"


def build_offer_public_link(offer_public_id: str, frontend_url: str | None = None) -> str:
    base_url = (frontend_url or "").strip().rstrip("/")
    path = build_offer_public_path(offer_public_id)
    if not base_url:
        return path
    return f"{base_url}{path}"


def is_offer_public_id_shape(value: str | None) -> bool:
    return bool(value and value.startswith(f"{OFFER_PUBLIC_ID_PREFIX}_") and len(value) > len(OFFER_PUBLIC_ID_PREFIX) + 8)
