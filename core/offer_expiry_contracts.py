"""Canonical contract for cross-server offer-expiry commands."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from uuid import UUID, uuid5

from core.offer_identity import is_offer_public_id_shape


OFFER_EXPIRY_COMMAND_CONTRACT_VERSION = 1
OFFER_EXPIRY_COMMAND_NAMESPACE = UUID("7f8b45c0-42e7-4d25-93c9-25f3f842f354")
OFFER_EXPIRY_IDEMPOTENCY_PREFIX = "offer-expiry:v1:"
_COMMAND_LABEL_PATTERN = re.compile(r"^[a-z0-9_]+$")


class OfferExpiryCommandIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OfferExpiryCommandIdentity:
    command_id: UUID
    idempotency_key: str
    request_hash: str


def _positive_int(value: object, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise OfferExpiryCommandIdentityError(f"{field_name}_invalid") from exc
    if normalized <= 0:
        raise OfferExpiryCommandIdentityError(f"{field_name}_invalid")
    return normalized


def _command_label(value: object, *, field_name: str, max_length: int) -> str:
    normalized = str(getattr(value, "value", value) or "").strip().lower()
    if (
        not normalized
        or len(normalized) > max_length
        or _COMMAND_LABEL_PATTERN.fullmatch(normalized) is None
    ):
        raise OfferExpiryCommandIdentityError(f"{field_name}_invalid")
    return normalized


def canonical_offer_expiry_command_payload(
    *,
    offer_public_id: object,
    owner_user_id: object,
    actor_user_id: object | None,
    source_surface: object,
    source_server: object,
    expire_reason: object,
) -> dict[str, Any]:
    public_id = str(offer_public_id or "").strip()
    if len(public_id) > 40 or not is_offer_public_id_shape(public_id):
        raise OfferExpiryCommandIdentityError("offer_public_id_invalid")

    owner_id = _positive_int(owner_user_id, field_name="owner_user_id")
    actor_id = owner_id if actor_user_id is None else _positive_int(
        actor_user_id,
        field_name="actor_user_id",
    )
    normalized_server = _command_label(
        source_server,
        field_name="source_server",
        max_length=16,
    )
    if normalized_server not in {"iran", "foreign"}:
        raise OfferExpiryCommandIdentityError("source_server_invalid")

    return {
        "contract_version": OFFER_EXPIRY_COMMAND_CONTRACT_VERSION,
        "offer_public_id": public_id,
        "owner_user_id": owner_id,
        "actor_user_id": actor_id,
        "source_surface": _command_label(
            source_surface,
            field_name="source_surface",
            max_length=32,
        ),
        "source_server": normalized_server,
        "expire_reason": _command_label(
            expire_reason,
            field_name="expire_reason",
            max_length=32,
        ),
    }


def canonical_offer_expiry_command_bytes(**kwargs: object) -> bytes:
    payload = canonical_offer_expiry_command_payload(**kwargs)
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_offer_expiry_command_identity(**kwargs: object) -> OfferExpiryCommandIdentity:
    canonical = canonical_offer_expiry_command_bytes(**kwargs)
    command_id = uuid5(OFFER_EXPIRY_COMMAND_NAMESPACE, canonical.decode("utf-8"))
    return OfferExpiryCommandIdentity(
        command_id=command_id,
        idempotency_key=f"{OFFER_EXPIRY_IDEMPOTENCY_PREFIX}{command_id.hex}",
        request_hash=hashlib.sha256(canonical).hexdigest(),
    )


def validate_offer_expiry_command_identity(
    *,
    command_id: UUID,
    idempotency_key: str,
    **kwargs: object,
) -> OfferExpiryCommandIdentity:
    expected = build_offer_expiry_command_identity(**kwargs)
    if command_id != expected.command_id or idempotency_key != expected.idempotency_key:
        raise OfferExpiryCommandIdentityError("command_identity_mismatch")
    return expected


def build_offer_expiry_forward_payload(
    offer: object,
    *,
    owner_user_id: int,
    actor_user_id: int | None,
    source_surface: object,
    source_server: str,
    expire_reason: str,
    include_command_identity: bool,
) -> dict[str, Any]:
    offer_id = _positive_int(getattr(offer, "id", None), field_name="offer_id")
    public_id = str(getattr(offer, "offer_public_id", None) or "").strip() or None
    payload: dict[str, Any] = {
        "offer_id": offer_id,
        "offer_public_id": public_id,
        "owner_user_id": owner_user_id,
        "actor_user_id": actor_user_id,
        "source_surface": str(getattr(source_surface, "value", source_surface)),
        "source_server": source_server,
        "expire_reason": expire_reason,
    }
    if not include_command_identity:
        return payload

    identity = build_offer_expiry_command_identity(
        offer_public_id=public_id,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        source_surface=source_surface,
        source_server=source_server,
        expire_reason=expire_reason,
    )
    payload["actor_user_id"] = canonical_offer_expiry_command_payload(
        offer_public_id=public_id,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        source_surface=source_surface,
        source_server=source_server,
        expire_reason=expire_reason,
    )["actor_user_id"]
    payload["command_id"] = str(identity.command_id)
    payload["idempotency_key"] = identity.idempotency_key
    return payload
