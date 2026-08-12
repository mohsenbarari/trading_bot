"""Opaque public identifier for rows in the offer-request ledger.

``OfferRequest.id`` is a sequential integer, so handing it to a client would let
anyone walk other people's requests. Clients and callback payloads therefore
only ever see this identifier, and holding one grants nothing on its own: every
endpoint still authorizes against the caller's authenticated identity.
"""
from __future__ import annotations

import secrets

OFFER_REQUEST_PUBLIC_ID_PREFIX = "req"
OFFER_REQUEST_PUBLIC_ID_BYTES = 18


def generate_offer_request_public_id() -> str:
    """Return an opaque URL-safe request identifier that does not expose DB ids."""
    return f"{OFFER_REQUEST_PUBLIC_ID_PREFIX}_{secrets.token_urlsafe(OFFER_REQUEST_PUBLIC_ID_BYTES)}"


def is_offer_request_public_id_shape(value: str | None) -> bool:
    return bool(
        value
        and value.startswith(f"{OFFER_REQUEST_PUBLIC_ID_PREFIX}_")
        and len(value) > len(OFFER_REQUEST_PUBLIC_ID_PREFIX) + 8
    )
