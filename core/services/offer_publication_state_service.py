"""Helpers for offer surface publication state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.utils import utc_now_naive
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


@dataclass(frozen=True)
class PublicationStateApplyResult:
    applied: bool
    reason: str
    status: OfferPublicationStatus


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


def build_offer_publication_state(
    offer: Any,
    surface: Any,
    *,
    status: Any = OfferPublicationStatus.PENDING,
    now: datetime | None = None,
) -> OfferPublicationState:
    normalized_surface = normalize_publication_surface(surface)
    normalized_status = normalize_publication_status(status)
    offer_public_id = str(getattr(offer, "offer_public_id", "") or "").strip()
    if not offer_public_id:
        raise ValueError("offer_public_id is required for publication state")

    current_time = now or utc_now_naive()
    state = OfferPublicationState(
        offer_id=getattr(offer, "id", None),
        offer_public_id=offer_public_id,
        offer_home_server=str(getattr(offer, "home_server", "") or ""),
        surface=normalized_surface,
        publication_owner_server=publication_owner_server_for_surface(normalized_surface),
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

    normalized_offer_status = normalize_offer_status(offer_status)
    normalized_requested_status = normalize_publication_status(requested_status)
    applied_status = normalized_requested_status
    reason = "applied"

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
