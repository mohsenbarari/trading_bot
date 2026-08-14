"""Helpers for offer surface publication state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.utils import utc_now_naive
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES
from models.offer import OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)


TERMINAL_OFFER_STATUSES = {
    OfferStatus.COMPLETED.value,
    OfferStatus.CANCELLED.value,
    OfferStatus.EXPIRED.value,
}
ACTIVE_PUBLICATION_STATUSES = {
    OfferPublicationStatus.PENDING.value,
    OfferPublicationStatus.SENT.value,
    OfferPublicationStatus.VISIBLE.value,
    OfferPublicationStatus.LAGGED.value,
}
TELEGRAM_PRIMARY_PUBLISHER_BOT_IDENTITY = "primary"
TELEGRAM_PUBLICATION_PUBLISHER_BOT_IDENTITIES = frozenset(
    (TELEGRAM_PRIMARY_PUBLISHER_BOT_IDENTITY, *TELEGRAM_PUBLISHER_IDENTITIES)
)
_DEFAULT_TELEGRAM_PUBLICATION_PUBLISHER = object()


class CanonicalPublicationIdentityError(ValueError):
    """Raised when a publication tries to replace canonical provider identity."""


@dataclass(frozen=True, slots=True)
class PublicationStateApplyResult:
    applied: bool
    reason: str
    status: OfferPublicationStatus


@dataclass(frozen=True, slots=True)
class TelegramPublicationIdentity:
    publisher_bot_identity: str
    destination_chat_id: int
    message_id: int


def normalize_publication_surface(value: Any) -> OfferPublicationSurface:
    if isinstance(value, OfferPublicationSurface):
        return value
    return OfferPublicationSurface(str(value))


def normalize_publication_status(value: Any) -> OfferPublicationStatus:
    if isinstance(value, OfferPublicationStatus):
        return value
    return OfferPublicationStatus(str(value))


def normalize_offer_status(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def publication_owner_server_for_surface(surface: Any) -> str:
    normalized = normalize_publication_surface(surface)
    if normalized == OfferPublicationSurface.TELEGRAM_CHANNEL:
        return "foreign"
    if normalized == OfferPublicationSurface.WEBAPP_MARKET:
        return "iran"
    raise ValueError(f"unsupported publication surface: {surface}")


def publication_dedupe_key(offer_public_id: str, surface: Any) -> str:
    normalized = normalize_publication_surface(surface)
    return f"offer-publication:{normalized.value}:{offer_public_id}"


def _strict_provider_integer(
    value: Any,
    *,
    field: str,
    allow_negative: bool,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CanonicalPublicationIdentityError(
            f"canonical_telegram_{field}_invalid"
        )
    if value == 0 or (not allow_negative and value < 0):
        raise CanonicalPublicationIdentityError(
            f"canonical_telegram_{field}_invalid"
        )
    return value


def _identity_publication_surface(state: OfferPublicationState) -> OfferPublicationSurface:
    try:
        return normalize_publication_surface(getattr(state, "surface", None))
    except (TypeError, ValueError) as exc:
        raise CanonicalPublicationIdentityError(
            "canonical_telegram_surface_mismatch"
        ) from exc


def normalize_telegram_publication_publisher_identity(value: Any) -> str:
    publisher = str(value or "").strip()
    if publisher not in TELEGRAM_PUBLICATION_PUBLISHER_BOT_IDENTITIES:
        raise CanonicalPublicationIdentityError(
            "canonical_telegram_publisher_mismatch"
        )
    return publisher


def ensure_telegram_publication_publisher_identity(
    state: OfferPublicationState,
    *,
    publisher_bot_identity: str | None = None,
) -> str:
    surface = _identity_publication_surface(state)
    if surface != OfferPublicationSurface.TELEGRAM_CHANNEL:
        raise CanonicalPublicationIdentityError(
            "canonical_telegram_surface_mismatch"
        )
    current = str(getattr(state, "publisher_bot_identity", "") or "").strip()
    incoming = (
        normalize_telegram_publication_publisher_identity(publisher_bot_identity)
        if publisher_bot_identity is not None
        else None
    )
    if current:
        normalized_current = normalize_telegram_publication_publisher_identity(current)
        if incoming is not None and incoming != normalized_current:
            raise CanonicalPublicationIdentityError(
                "canonical_telegram_publisher_mismatch"
            )
        return normalized_current
    assigned = incoming or TELEGRAM_PRIMARY_PUBLISHER_BOT_IDENTITY
    state.publisher_bot_identity = assigned
    return assigned


def canonical_telegram_publication_identity(
    state: OfferPublicationState,
) -> TelegramPublicationIdentity:
    surface = _identity_publication_surface(state)
    if surface != OfferPublicationSurface.TELEGRAM_CHANNEL:
        raise CanonicalPublicationIdentityError(
            "canonical_telegram_surface_mismatch"
        )
    publisher = str(
        getattr(state, "publisher_bot_identity", "") or ""
    ).strip()
    publisher = normalize_telegram_publication_publisher_identity(publisher)
    chat_id = _strict_provider_integer(
        getattr(state, "telegram_chat_id", None),
        field="chat_id",
        allow_negative=True,
    )
    message_id = _strict_provider_integer(
        getattr(state, "telegram_message_id", None),
        field="message_id",
        allow_negative=False,
    )
    return TelegramPublicationIdentity(
        publisher_bot_identity=publisher,
        destination_chat_id=chat_id,
        message_id=message_id,
    )


def _validate_canonical_telegram_update(
    state: OfferPublicationState,
    *,
    publisher_bot_identity: str | None,
    surface_resource_id: str | None,
    telegram_chat_id: int | None,
    telegram_message_id: int | None,
) -> bool:
    surface = _identity_publication_surface(state)
    if surface != OfferPublicationSurface.TELEGRAM_CHANNEL:
        if any(
            value is not None
            for value in (
                publisher_bot_identity,
                telegram_chat_id,
                telegram_message_id,
            )
        ):
            raise CanonicalPublicationIdentityError(
                "canonical_telegram_fields_on_non_telegram_surface"
            )
        if getattr(state, "publisher_bot_identity", None) is not None:
            raise CanonicalPublicationIdentityError(
                "canonical_telegram_publisher_on_non_telegram_surface"
            )
        return False

    current_raw = str(getattr(state, "publisher_bot_identity", "") or "").strip()
    current_publisher = (
        normalize_telegram_publication_publisher_identity(current_raw)
        if current_raw
        else None
    )
    incoming_publisher = (
        normalize_telegram_publication_publisher_identity(publisher_bot_identity)
        if publisher_bot_identity is not None
        else None
    )
    if (
        current_publisher is not None
        and incoming_publisher is not None
        and current_publisher != incoming_publisher
    ):
        raise CanonicalPublicationIdentityError(
            "canonical_telegram_publisher_mismatch"
        )
    resolved_publisher = current_publisher or incoming_publisher

    incoming_values = (
        ("chat_id", telegram_chat_id, True, "telegram_chat_id"),
        ("message_id", telegram_message_id, False, "telegram_message_id"),
    )
    for field, incoming, allow_negative, attribute in incoming_values:
        if incoming is None:
            continue
        validated = _strict_provider_integer(
            incoming,
            field=field,
            allow_negative=allow_negative,
        )
        existing = getattr(state, attribute, None)
        if existing is not None:
            existing_value = _strict_provider_integer(
                existing,
                field=field,
                allow_negative=allow_negative,
            )
            if existing_value != validated:
                raise CanonicalPublicationIdentityError(
                    f"canonical_telegram_{field}_mismatch"
                )

    if (
        resolved_publisher is None
        and (
            telegram_message_id is not None
            or getattr(state, "telegram_message_id", None) is not None
        )
    ):
        raise CanonicalPublicationIdentityError(
            "canonical_telegram_publisher_missing"
        )

    if surface_resource_id is not None:
        resource = str(surface_resource_id)
        existing_resource = getattr(state, "surface_resource_id", None)
        if existing_resource is not None and str(existing_resource) != resource:
            raise CanonicalPublicationIdentityError(
                "canonical_telegram_surface_resource_mismatch"
            )
        if telegram_message_id is not None and resource != str(telegram_message_id):
            raise CanonicalPublicationIdentityError(
                "canonical_telegram_surface_resource_message_mismatch"
            )
    return True


def build_offer_publication_state(
    offer: Any,
    surface: Any,
    *,
    status: Any = OfferPublicationStatus.PENDING,
    now: datetime | None = None,
    publisher_bot_identity: str | None | object = _DEFAULT_TELEGRAM_PUBLICATION_PUBLISHER,
) -> OfferPublicationState:
    normalized_surface = normalize_publication_surface(surface)
    normalized_status = normalize_publication_status(status)
    offer_public_id = str(getattr(offer, "offer_public_id", "") or "").strip()
    if not offer_public_id:
        raise ValueError("offer_public_id is required for publication state")

    if normalized_surface == OfferPublicationSurface.TELEGRAM_CHANNEL:
        requested_publisher = (
            TELEGRAM_PRIMARY_PUBLISHER_BOT_IDENTITY
            if publisher_bot_identity is _DEFAULT_TELEGRAM_PUBLICATION_PUBLISHER
            else publisher_bot_identity
        )
        publisher = (
            normalize_telegram_publication_publisher_identity(requested_publisher)
            if requested_publisher is not None
            else None
        )
    elif publisher_bot_identity is not _DEFAULT_TELEGRAM_PUBLICATION_PUBLISHER and publisher_bot_identity is not None:
        raise CanonicalPublicationIdentityError(
            "canonical_telegram_fields_on_non_telegram_surface"
        )
    else:
        publisher = None

    current_time = now or utc_now_naive()
    state = OfferPublicationState(
        offer_id=getattr(offer, "id", None),
        offer_public_id=offer_public_id,
        offer_home_server=str(getattr(offer, "home_server", "") or ""),
        surface=normalized_surface,
        publication_owner_server=publication_owner_server_for_surface(normalized_surface),
        publisher_bot_identity=publisher,
        status=normalized_status,
        dedupe_key=publication_dedupe_key(offer_public_id, normalized_surface),
        offer_version_id=getattr(offer, "version_id", None),
        last_known_offer_status=normalize_offer_status(getattr(offer, "status", None)) or None,
        created_at=current_time,
    )
    return state


def apply_publication_state_update(
    state: OfferPublicationState,
    *,
    offer_status: Any,
    offer_version_id: int | None,
    requested_status: Any,
    now: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    publisher_bot_identity: str | None = None,
    surface_resource_id: str | None = None,
    telegram_chat_id: int | None = None,
    telegram_message_id: int | None = None,
) -> PublicationStateApplyResult:
    current_version = _coerce_int(getattr(state, "offer_version_id", None))
    incoming_version = _coerce_int(offer_version_id)
    if current_version is not None and incoming_version is not None and incoming_version < current_version:
        return PublicationStateApplyResult(
            applied=False,
            reason="stale_offer_version",
            status=normalize_publication_status(state.status),
        )

    is_telegram_publication = _validate_canonical_telegram_update(
        state,
        publisher_bot_identity=publisher_bot_identity,
        surface_resource_id=surface_resource_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
    )

    normalized_offer_status = normalize_offer_status(offer_status)
    normalized_requested_status = normalize_publication_status(requested_status)
    applied_status = normalized_requested_status
    reason = "applied"

    if is_telegram_publication and publisher_bot_identity is not None:
        ensure_telegram_publication_publisher_identity(
            state,
            publisher_bot_identity=publisher_bot_identity,
        )

    if (
        normalized_offer_status in TERMINAL_OFFER_STATUSES
        and normalized_requested_status.value in ACTIVE_PUBLICATION_STATUSES
    ):
        applied_status = OfferPublicationStatus.DISABLED
        error_code = error_code or "offer_terminal"
        reason = "converted_terminal_offer"

    current_time = now or utc_now_naive()
    state.status = applied_status
    if incoming_version is not None:
        state.offer_version_id = incoming_version
    state.last_known_offer_status = normalized_offer_status or None
    state.error_code = error_code
    state.error_message = error_message

    if applied_status in {OfferPublicationStatus.PENDING, OfferPublicationStatus.FAILED}:
        state.last_attempt_at = current_time
    if applied_status in {OfferPublicationStatus.SENT, OfferPublicationStatus.VISIBLE}:
        state.last_success_at = current_time
    if applied_status == OfferPublicationStatus.DISABLED:
        state.disabled_at = current_time
    if applied_status == OfferPublicationStatus.LAGGED:
        state.lagged_at = current_time

    if surface_resource_id is not None:
        state.surface_resource_id = surface_resource_id
    if telegram_chat_id is not None:
        state.telegram_chat_id = telegram_chat_id
    if telegram_message_id is not None:
        state.telegram_message_id = telegram_message_id

    return PublicationStateApplyResult(applied=True, reason=reason, status=applied_status)


def mark_publication_lagged_if_overdue(
    state: OfferPublicationState,
    *,
    now: datetime,
    threshold_seconds: int,
) -> bool:
    if normalize_publication_status(state.status) != OfferPublicationStatus.PENDING:
        return False
    anchor = getattr(state, "last_attempt_at", None) or getattr(state, "created_at", None)
    if anchor is None:
        return False
    age_seconds = (now - anchor).total_seconds()
    if age_seconds <= threshold_seconds:
        return False

    state.status = OfferPublicationStatus.LAGGED
    state.lagged_at = now
    state.error_code = state.error_code or "publication_lagged"
    return True


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
