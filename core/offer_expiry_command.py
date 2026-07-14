"""Stable identities and fingerprints for forwarded offer-expiry commands."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping
from uuid import UUID, uuid5


_OFFER_EXPIRY_COMMAND_NAMESPACE = UUID("8aa27967-3975-4ec8-9bc1-afdc1f3db469")


@dataclass(frozen=True)
class RepublishCommandIdentity:
    command_id: UUID
    idempotency_key: str
    replacement_offer_public_id: str


def build_republish_command_identity(
    *,
    owner_user_id: int,
    source_offer_public_id: str,
    create_idempotency_key: str,
) -> RepublishCommandIdentity:
    source_public_id = str(source_offer_public_id or "").strip()
    create_key = str(create_idempotency_key or "").strip()
    if not source_public_id:
        raise ValueError("source_offer_public_id is required")
    if not create_key:
        raise ValueError("create_idempotency_key is required")

    command_id = uuid5(
        _OFFER_EXPIRY_COMMAND_NAMESPACE,
        f"republish:{int(owner_user_id)}:{source_public_id}:{create_key}",
    )
    return RepublishCommandIdentity(
        command_id=command_id,
        idempotency_key=f"offer-republish:{command_id}",
        replacement_offer_public_id=f"ofr_rp_{command_id.hex[:24]}",
    )


def offer_expiry_command_hash(payload: Mapping[str, Any]) -> str:
    """Hash canonical business fields without peer-local integer identifiers."""
    canonical = {
        "command_id": str(payload.get("command_id") or "").strip(),
        "idempotency_key": str(payload.get("idempotency_key") or "").strip(),
        "offer_public_id": str(payload.get("offer_public_id") or "").strip(),
        "owner_user_id": int(payload.get("owner_user_id") or 0),
        "actor_user_id": int(payload.get("actor_user_id") or 0),
        "source_surface": str(payload.get("source_surface") or "").strip().lower(),
        "source_server": str(payload.get("source_server") or "").strip().lower(),
        "expire_reason": str(payload.get("expire_reason") or "").strip().lower(),
        "replacement_offer_public_id": str(payload.get("replacement_offer_public_id") or "").strip(),
    }
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
