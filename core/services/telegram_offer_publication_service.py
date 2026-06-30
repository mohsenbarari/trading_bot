"""Idempotent Telegram channel publication for offers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import telegram_gateway
from core.config import settings
from core.offer_identity import ensure_offer_public_id
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.offer_publication_state_service import (
    apply_publication_state_update,
    build_offer_publication_state,
    normalize_publication_status,
    publication_dedupe_key,
)
from core.utils import utc_now_naive
from models.offer import OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class TelegramOfferSendResult:
    message_id: Optional[int]
    response_class: str = "unknown"
    status_code: int | None = None
    retry_after_seconds: int | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.message_id) and not self.error_code


SendOfferToChannelResult = Optional[int] | TelegramOfferSendResult
SendOfferToChannel = Callable[[Any, Any], Awaitable[SendOfferToChannelResult]]

SENT_TELEGRAM_PUBLICATION_STATUSES = {
    OfferPublicationStatus.SENT,
    OfferPublicationStatus.VISIBLE,
}


@dataclass(slots=True)
class TelegramOfferPublicationResult:
    message_id: Optional[int]
    status: OfferPublicationStatus | None
    sent_new_message: bool = False
    send_attempted: bool = False
    skipped_reason: str | None = None
    error_code: str | None = None
    response_class: str | None = None
    retry_after_seconds: int | None = None

    @property
    def ok(self) -> bool:
        return bool(self.message_id) and not self.error_code


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> Optional[int]:
    numeric_value = _coerce_int(value)
    if numeric_value is None or numeric_value <= 0:
        return None
    return numeric_value


def _gateway_status_code(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    status_code = result.status_code
    if status_code is None and isinstance(result.response_json, dict):
        status_code = _positive_int(result.response_json.get("error_code"))
    return status_code


def _retry_after_from_gateway_result(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    raw_retry_after = None
    if isinstance(result.response_json, dict):
        parameters = result.response_json.get("parameters")
        if isinstance(parameters, dict):
            raw_retry_after = parameters.get("retry_after")
    retry_after = _positive_int(raw_retry_after)
    if retry_after is None:
        return None
    return min(120, max(1, retry_after))


def _telegram_response_class(status_code: int | None, *, ok: bool, error: str | None = None) -> str:
    if ok:
        return "2xx"
    if status_code == 429:
        return "429"
    if status_code == 400:
        return "400"
    if status_code is not None and 400 <= status_code <= 499:
        return "4xx"
    if status_code is not None and 500 <= status_code <= 599:
        return "5xx"
    if error:
        return "transport"
    return "unknown"


def _telegram_error_code(response_class: str, *, error: str | None = None) -> str:
    if error == "missing_bot_token":
        return "telegram_missing_bot_token"
    if response_class == "429":
        return "telegram_rate_limited"
    if response_class == "400":
        return "telegram_bad_request"
    if response_class == "4xx":
        return "telegram_client_error"
    if response_class == "5xx":
        return "telegram_server_error"
    if response_class == "transport":
        return "telegram_transport_error"
    return "telegram_send_empty_result"


def telegram_offer_send_result_from_gateway(
    result: telegram_gateway.TelegramGatewayResult,
) -> TelegramOfferSendResult:
    status_code = _gateway_status_code(result)
    response_class = _telegram_response_class(status_code, ok=result.ok, error=result.error)
    message_id = result.message_id if result.ok else None
    return TelegramOfferSendResult(
        message_id=message_id,
        response_class=response_class,
        status_code=status_code,
        retry_after_seconds=_retry_after_from_gateway_result(result) if response_class == "429" else None,
        error_code=None if message_id else _telegram_error_code(response_class, error=result.error),
        error_message=result.response_text or result.error,
    )


def normalize_telegram_offer_send_result(value: SendOfferToChannelResult) -> TelegramOfferSendResult:
    if isinstance(value, TelegramOfferSendResult):
        return value
    message_id = _coerce_int(value)
    if message_id:
        return TelegramOfferSendResult(message_id=message_id, response_class="2xx")
    return TelegramOfferSendResult(
        message_id=None,
        response_class="unknown",
        error_code="telegram_send_empty_result",
    )


def _offer_status_value(offer: Any) -> str:
    return str(getattr(getattr(offer, "status", None), "value", getattr(offer, "status", None)) or "").lower()


def _offer_version_id(offer: Any) -> int | None:
    return _coerce_int(getattr(offer, "version_id", None))


def _telegram_message_id_from_state(state: OfferPublicationState | Any | None) -> Optional[int]:
    return _coerce_int(getattr(state, "telegram_message_id", None))


def telegram_publication_message_id(offer: Any, state: OfferPublicationState | Any | None = None) -> Optional[int]:
    """Return the channel message id from the offer or its publication state."""
    return _coerce_int(getattr(offer, "channel_message_id", None)) or _telegram_message_id_from_state(state)


def telegram_publication_is_sent(state: OfferPublicationState | Any | None) -> bool:
    if state is None:
        return False
    try:
        status = normalize_publication_status(getattr(state, "status", None))
    except Exception:
        return False
    return status in SENT_TELEGRAM_PUBLICATION_STATUSES and bool(_telegram_message_id_from_state(state))


def apply_existing_telegram_publication_to_offer(offer: Any, state: OfferPublicationState | Any | None) -> Optional[int]:
    """Backfill the legacy offer message id from publication state when needed."""
    message_id = telegram_publication_message_id(offer, state)
    if message_id and not _coerce_int(getattr(offer, "channel_message_id", None)):
        setattr(offer, "channel_message_id", message_id)
    return message_id


def mark_telegram_publication_success(
    state: OfferPublicationState,
    offer: Any,
    *,
    message_id: int,
    chat_id: int | None = None,
    now=None,
) -> None:
    apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=_offer_version_id(offer),
        requested_status=OfferPublicationStatus.SENT,
        now=now or utc_now_naive(),
        surface_resource_id=str(message_id),
        telegram_chat_id=chat_id,
        telegram_message_id=message_id,
    )
    setattr(offer, "channel_message_id", message_id)


def mark_telegram_publication_failure(
    state: OfferPublicationState,
    offer: Any,
    *,
    error_code: str,
    error_message: str | None = None,
    now=None,
) -> None:
    apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=_offer_version_id(offer),
        requested_status=OfferPublicationStatus.FAILED,
        now=now or utc_now_naive(),
        error_code=error_code,
        error_message=error_message,
    )


async def load_telegram_publication_state_for_update(
    db: AsyncSession,
    offer: Any,
) -> OfferPublicationState | None:
    offer_public_id = ensure_offer_public_id(offer)
    dedupe_key = publication_dedupe_key(offer_public_id, OfferPublicationSurface.TELEGRAM_CHANNEL)
    stmt = (
        select(OfferPublicationState)
        .where(OfferPublicationState.dedupe_key == dedupe_key)
        .with_for_update()
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_or_create_telegram_publication_state(
    db: AsyncSession,
    offer: Any,
) -> OfferPublicationState:
    state = await load_telegram_publication_state_for_update(db, offer)
    if state is not None:
        return state

    state = build_offer_publication_state(
        offer,
        OfferPublicationSurface.TELEGRAM_CHANNEL,
        status=OfferPublicationStatus.PENDING,
    )
    db.add(state)
    flush = getattr(db, "flush", None)
    if flush is not None:
        await flush()
    return state


async def publish_offer_to_telegram_channel_once(
    db: AsyncSession,
    offer: Any,
    user: Any,
    *,
    send_offer_to_channel: SendOfferToChannel,
    raise_send_errors: bool = False,
) -> TelegramOfferPublicationResult:
    """
    Publish an offer to Telegram at most once for its public id.

    The dedupe key lives in ``offer_publication_states`` and does not depend on
    ``Offer.channel_message_id``. The legacy field is still backfilled locally
    because older code paths use it to edit the channel post.
    """
    if current_server() != SERVER_FOREIGN:
        return TelegramOfferPublicationResult(
            message_id=None,
            status=None,
            skipped_reason="telegram_publication_foreign_only",
        )

    state = await get_or_create_telegram_publication_state(db, offer)
    existing_message_id = apply_existing_telegram_publication_to_offer(offer, state)
    if telegram_publication_is_sent(state):
        return TelegramOfferPublicationResult(
            message_id=existing_message_id,
            status=normalize_publication_status(state.status),
            skipped_reason="already_published",
        )

    if existing_message_id:
        mark_telegram_publication_success(
            state,
            offer,
            message_id=existing_message_id,
            chat_id=_coerce_int(settings.channel_id),
        )
        return TelegramOfferPublicationResult(
            message_id=existing_message_id,
            status=OfferPublicationStatus.SENT,
            skipped_reason="legacy_message_id_backfilled",
        )

    status_value = _offer_status_value(offer)
    if status_value and status_value != OfferStatus.ACTIVE.value:
        apply_publication_state_update(
            state,
            offer_status=getattr(offer, "status", None),
            offer_version_id=_offer_version_id(offer),
            requested_status=OfferPublicationStatus.DISABLED,
            now=utc_now_naive(),
            error_code="offer_not_active",
        )
        return TelegramOfferPublicationResult(
            message_id=None,
            status=OfferPublicationStatus.DISABLED,
            skipped_reason="offer_not_active",
            error_code="offer_not_active",
        )

    apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=_offer_version_id(offer),
        requested_status=OfferPublicationStatus.PENDING,
        now=utc_now_naive(),
    )

    try:
        raw_send_result = await send_offer_to_channel(offer, user)
    except Exception as exc:
        mark_telegram_publication_failure(
            state,
            offer,
            error_code="telegram_send_exception",
            error_message=type(exc).__name__,
        )
        logger.warning(
            "Telegram offer publication failed",
            extra={
                "event": "telegram.offer_publication_failed",
                "offer_id": getattr(offer, "id", None),
                "offer_public_id": getattr(offer, "offer_public_id", None),
                "error_type": type(exc).__name__,
            },
        )
        if raise_send_errors:
            raise
        return TelegramOfferPublicationResult(
            message_id=None,
            status=OfferPublicationStatus.FAILED,
            send_attempted=True,
            error_code="telegram_send_exception",
        )

    send_result = normalize_telegram_offer_send_result(raw_send_result)
    message_id = send_result.message_id
    if message_id:
        mark_telegram_publication_success(
            state,
            offer,
            message_id=int(message_id),
            chat_id=_coerce_int(settings.channel_id),
        )
        return TelegramOfferPublicationResult(
            message_id=int(message_id),
            status=OfferPublicationStatus.SENT,
            sent_new_message=True,
            send_attempted=True,
            response_class=send_result.response_class,
            retry_after_seconds=send_result.retry_after_seconds,
        )

    mark_telegram_publication_failure(
        state,
        offer,
        error_code=send_result.error_code or "telegram_send_empty_result",
        error_message=send_result.error_message,
    )
    return TelegramOfferPublicationResult(
        message_id=None,
        status=OfferPublicationStatus.FAILED,
        send_attempted=True,
        error_code=send_result.error_code or "telegram_send_empty_result",
        response_class=send_result.response_class,
        retry_after_seconds=send_result.retry_after_seconds,
    )
