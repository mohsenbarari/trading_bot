"""Closed database-role scopes for private three-site DR processes."""

from __future__ import annotations


PROJECTION_SERVICE_SCOPES = {
    "dr_receiver": "receiver",
    "dr_delivery_worker": "delivery",
    "dr_projection_worker": "projector",
    "dr_blob_worker": "blob",
    "dr_effect_worker": "effect",
}


def projection_scope_for_service(service: str | None) -> str:
    normalized = str(service or "").strip()
    try:
        return PROJECTION_SERVICE_SCOPES[normalized]
    except KeyError as exc:
        raise RuntimeError(
            f"service {normalized or '<missing>'} has no private DR database scope"
        ) from exc
