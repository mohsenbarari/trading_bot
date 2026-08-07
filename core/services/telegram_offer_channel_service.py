"""Telegram channel offer rendering and terminal-state side effects."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, Optional

from core import telegram_gateway
from core.config import settings
from core.offer_settlement import build_offer_summary_text
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.telegram_offer_publication_service import telegram_publication_message_id
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeMode,
    assert_telegram_provider_execution_authority,
    configured_telegram_delivery_producer_mode,
    configured_telegram_delivery_runtime,
)
from core.services.trade_service import get_available_trade_amounts
from core.telegram_trade_callbacks import build_channel_trade_callback_data
from models.offer import OfferStatus

logger = logging.getLogger(__name__)

INVISIBLE_CHANNEL_PADDING = "\u2800" * 35
TELEGRAM_MESSAGE_NOT_MODIFIED = "message is not modified"
TELEGRAM_OFFER_FULLY_TRADED_TAG = "🤝 ✅"
TELEGRAM_OFFER_EXPIRED_TAG = "❌"
# Inventory M38 — channel overtime marker; never replaces trade history tags.
TELEGRAM_OFFER_OVERTIME_MARKER = "⏳"
CHANNEL_LIFECYCLE_METADATA_KEY = "channel_lifecycle_phase"


def _assert_legacy_channel_editor_owner() -> None:
    assert_telegram_provider_execution_authority()
    runtime = configured_telegram_delivery_runtime()
    if not runtime.legacy_workers_enabled or runtime.queue_worker_enabled:
        raise TelegramDeliveryRuntimeConfigurationError(
            "legacy_offer_channel_editor_is_not_runtime_owner"
        )


@dataclass(frozen=True, slots=True)
class OfferChannelStateApplyResult:
    ok: bool
    response_class: str
    status_code: int | None = None
    reason: str = "unknown"
    retry_after_seconds: int | None = None
    error: str | None = None
    method: str | None = None


def _status_value(status: Any) -> str:
    return str(getattr(status, "value", status) or "").strip().lower()


def _offer_type_value(offer_type: Any) -> str:
    return str(getattr(offer_type, "value", offer_type) or "").strip().lower()


def _finite_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        return None
    return numeric_value


def _positive_int(value: Any) -> Optional[int]:
    numeric_value = _finite_int(value)
    if numeric_value is None or numeric_value <= 0:
        return None
    return numeric_value


def _gateway_status_code(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    status_code = result.status_code
    if status_code is None and isinstance(result.response_json, Mapping):
        status_code = _positive_int(result.response_json.get("error_code"))
    return status_code


def _retry_after_from_result(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    raw_retry_after = None
    if isinstance(result.response_json, Mapping):
        parameters = result.response_json.get("parameters")
        if isinstance(parameters, Mapping):
            raw_retry_after = parameters.get("retry_after")
    retry_after = _positive_int(raw_retry_after)
    if retry_after is None:
        return None
    return min(120, max(1, retry_after))


def _classify_gateway_result(
    result: telegram_gateway.TelegramGatewayResult,
) -> OfferChannelStateApplyResult:
    status_code = _gateway_status_code(result)
    if result.ok:
        return OfferChannelStateApplyResult(
            ok=True,
            response_class="2xx",
            status_code=status_code,
            reason="ok",
            method=result.method,
        )

    if status_code == 429:
        return OfferChannelStateApplyResult(
            ok=False,
            response_class="429",
            status_code=status_code,
            reason="telegram_rate_limited",
            retry_after_seconds=_retry_after_from_result(result),
            error=result.error,
            method=result.method,
        )
    if status_code == 400:
        response_json = result.response_json if isinstance(result.response_json, Mapping) else {}
        detail = (
            result.response_text
            or response_json.get("description")
            or result.error
        )
        detail_text = str(detail or "")
        reason = "telegram_bad_request"
        lowered = detail_text.lower()
        if "message_id_invalid" in lowered or "message to edit not found" in lowered:
            reason = "telegram_message_id_invalid"
        elif TELEGRAM_MESSAGE_NOT_MODIFIED in lowered:
            reason = "telegram_message_not_modified"
        return OfferChannelStateApplyResult(
            ok=False,
            response_class="400",
            status_code=status_code,
            reason=reason,
            error=detail_text or result.error,
            method=result.method,
        )
    if status_code is not None and 400 <= status_code <= 499:
        return OfferChannelStateApplyResult(
            ok=False,
            response_class="4xx",
            status_code=status_code,
            reason="telegram_client_error",
            error=result.error,
            method=result.method,
        )
    if status_code is not None and 500 <= status_code <= 599:
        return OfferChannelStateApplyResult(
            ok=False,
            response_class="5xx",
            status_code=status_code,
            reason="telegram_server_error",
            error=result.error,
            method=result.method,
        )
    if result.error:
        return OfferChannelStateApplyResult(
            ok=False,
            response_class="transport",
            status_code=status_code,
            reason="telegram_transport_error",
            error=result.error,
            method=result.method,
        )
    return OfferChannelStateApplyResult(
        ok=False,
        response_class="unknown",
        status_code=status_code,
        reason="telegram_unknown_error",
        method=result.method,
    )


def infer_traded_quantity_from_offer(offer: Any) -> int:
    """Infer completed quantity from offer state when trade aggregate is unavailable."""
    quantity = _finite_int(getattr(offer, "quantity", None))
    remaining = _finite_int(getattr(offer, "remaining_quantity", None))
    if quantity is None or remaining is None:
        return 0
    return max(0, quantity - remaining)


def get_offer_channel_history_tag(offer: Any, traded_quantity: Optional[int] = None) -> Optional[str]:
    """Return the terminal Telegram emoji tag for channel history posts."""
    status = _status_value(getattr(offer, "status", None))
    if status == OfferStatus.COMPLETED.value:
        return TELEGRAM_OFFER_FULLY_TRADED_TAG

    if status != OfferStatus.EXPIRED.value:
        return None

    quantity = traded_quantity if traded_quantity is not None else infer_traded_quantity_from_offer(offer)
    if quantity and quantity > 0:
        return f"🤝 {quantity:,} تا ✅"
    return TELEGRAM_OFFER_EXPIRED_TAG


def _terminal_channel_edit_should_clear_buttons(
    offer: Any,
    *,
    traded_quantity: Optional[int] = None,
) -> bool:
    """Whether a terminal channel edit should clear inline buttons.

    Pure time-limit expiry only tags the post and leaves buttons alone.
    Completed / cancelled / any partial-trade terminal state clears buttons in
    the same editMessageText call as the text update.
    """
    status = _status_value(getattr(offer, "status", None))
    if status == OfferStatus.EXPIRED.value:
        quantity = (
            traded_quantity
            if traded_quantity is not None
            else infer_traded_quantity_from_offer(offer)
        )
        return bool(quantity and quantity > 0)
    return True


def channel_edit_is_trade_priority(
    offer: Any,
    *,
    traded_quantity: Optional[int] = None,
) -> bool:
    """True when this channel edit must preempt the send backlog.

    Fully traded and still-active partial trades are high priority (same family
    as trade-result delivery). Expiry / overtime / cancel stay deferred while
    unpublished offers are waiting to be sent.
    """
    status = _status_value(getattr(offer, "status", None))
    if status == OfferStatus.COMPLETED.value:
        return True
    if status == OfferStatus.ACTIVE.value:
        quantity = _finite_int(getattr(offer, "quantity", None))
        remaining = _finite_int(getattr(offer, "remaining_quantity", None))
        return (
            quantity is not None
            and remaining is not None
            and remaining < quantity
        )
    return False


async def publication_send_backlog_due_count() -> int:
    """How many Telegram channel publishes are due right now (foreign only)."""
    if current_server() != SERVER_FOREIGN:
        return 0
    from core.db import AsyncSessionLocal
    from core.services.offer_publication_reconciliation_service import (
        publication_reconciliation_backlog,
    )

    async with AsyncSessionLocal() as db:
        backlog = await publication_reconciliation_backlog(db, server_mode="foreign")
    return int(getattr(backlog, "due", 0) or 0)


def project_offer_channel_lifecycle(
    offer: Any,
    *,
    normal_lifetime_minutes: int,
    as_of: Any | None = None,
):
    """Project lifecycle for channel rendering without a DB tail-request lookup.

    An ACTIVE offer past the final deadline is treated as final-tail: expiry
    would otherwise have terminalized it already.
    """
    from datetime import datetime, timezone

    from core.offer_lifecycle import compute_lifecycle_deadlines, project_offer_lifecycle

    raw_now = as_of if isinstance(as_of, datetime) else datetime.utcnow()
    # Deadlines are naive-UTC; normalize wall-clock the same way so feeder/DB
    # timestamps (often aware) do not raise on comparison.
    if getattr(raw_now, "tzinfo", None) is not None:
        now = raw_now.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        now = raw_now
    _normal_deadline, final_deadline = compute_lifecycle_deadlines(
        getattr(offer, "created_at", None),
        normal_lifetime_minutes=int(normal_lifetime_minutes or 0),
        overtime_minutes_snapshot=int(getattr(offer, "overtime_minutes_snapshot", 0) or 0),
    )
    status = _status_value(getattr(offer, "status", None))
    has_final_tail = bool(
        status == OfferStatus.ACTIVE.value
        and final_deadline is not None
        and now >= final_deadline
    )
    return project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=int(normal_lifetime_minutes or 0),
        as_of=now,
        has_final_tail_request=has_final_tail,
    )


def offer_channel_overtime_marker_visible(
    offer: Any,
    *,
    lifecycle_phase: str | None = None,
) -> bool:
    """Whether the channel post should include inventory M38 ``⏳``.

    Active overtime/final-tail keep the marker. Terminal posts keep it only when
    an overtime request committed a trade.
    """
    status = _status_value(getattr(offer, "status", None))
    if status in {
        OfferStatus.COMPLETED.value,
        OfferStatus.EXPIRED.value,
        OfferStatus.CANCELLED.value,
    }:
        return bool(getattr(offer, "overtime_trade_committed", False))
    phase = str(lifecycle_phase or "").strip().lower()
    return phase in {"overtime", "final_tail"}


def build_offer_channel_message(
    offer: Any,
    *,
    history_tag: Optional[str] = None,
    include_overtime_marker: bool | None = None,
    lifecycle_phase: str | None = None,
) -> str:
    """Build the canonical channel post text for active and terminal offer states."""
    offer_type = _offer_type_value(getattr(offer, "offer_type", None))
    commodity = getattr(offer, "commodity", None)
    commodity_name = getattr(commodity, "name", None) or "نامشخص"
    quantity = _finite_int(getattr(offer, "quantity", None)) or 0
    price = _finite_int(getattr(offer, "price", None)) or 0

    message = build_offer_summary_text(
        offer_type=offer_type,
        settlement_type=getattr(offer, "settlement_type", None),
        commodity_name=commodity_name,
        quantity=quantity,
        price=price,
    )
    notes = (getattr(offer, "notes", None) or "").strip()
    if notes:
        message += f"\nتوضیحات: {notes}"
    show_marker = include_overtime_marker
    if show_marker is None:
        show_marker = offer_channel_overtime_marker_visible(
            offer,
            lifecycle_phase=lifecycle_phase,
        )
    if show_marker:
        # Trailing marker line; never replaces cash/future text or trade tags.
        message += f"\n{TELEGRAM_OFFER_OVERTIME_MARKER}"
    if history_tag:
        message += f"\n{history_tag}"
    return f"{message}\n{INVISIBLE_CHANNEL_PADDING}"


def build_offer_channel_reply_markup(
    offer: Any,
    *,
    accepts_new_public_interaction: bool | None = None,
) -> Optional[dict[str, Any]]:
    """Build Telegram inline keyboard for active offers."""
    status = _status_value(getattr(offer, "status", None))
    if status and status != OfferStatus.ACTIVE.value:
        return None
    if accepts_new_public_interaction is False:
        return None

    offer_id = _finite_int(getattr(offer, "id", None))
    quantity = _finite_int(getattr(offer, "quantity", None)) or 0
    raw_remaining = _finite_int(getattr(offer, "remaining_quantity", None))
    remaining = quantity if raw_remaining is None else raw_remaining
    if not offer_id or remaining <= 0:
        return None

    is_wholesale = bool(getattr(offer, "is_wholesale", True))
    raw_lot_sizes = getattr(offer, "lot_sizes", None)
    lot_sizes = sorted(raw_lot_sizes, reverse=True) if raw_lot_sizes else None

    if is_wholesale or not lot_sizes:
        amounts = [remaining]
    else:
        amounts = get_available_trade_amounts(
            quantity=quantity,
            remaining_quantity=remaining,
            is_wholesale=False,
            lot_sizes=lot_sizes,
        )

    seen: set[int] = set()
    buttons = []
    for amount in amounts:
        numeric_amount = _finite_int(amount)
        if numeric_amount is None or numeric_amount <= 0 or numeric_amount in seen:
            continue
        seen.add(numeric_amount)
        buttons.append({
            "text": f"{numeric_amount} عدد",
            "callback_data": build_channel_trade_callback_data(
                offer_id=offer_id,
                offer_public_id=getattr(offer, "offer_public_id", None),
                amount=numeric_amount,
            ),
        })

    return {"inline_keyboard": [buttons]} if buttons else None


async def apply_offer_channel_state(
    offer: Any,
    *,
    publication_state: Any | None = None,
    traded_quantity: Optional[int] = None,
    reason: str = "",
    timeout: float = 10,
) -> bool:
    """
    Apply the current offer state to its Telegram channel post.

    This function is intentionally foreign-only. Iran may render WebApp history,
    but must not call Telegram for channel post mutations.
    """
    result = await apply_offer_channel_state_with_result(
        offer,
        publication_state=publication_state,
        traded_quantity=traded_quantity,
        reason=reason,
        timeout=timeout,
    )
    return result.ok


async def apply_offer_channel_state_with_result(
    offer: Any,
    *,
    publication_state: Any | None = None,
    traded_quantity: Optional[int] = None,
    reason: str = "",
    timeout: float = 10,
) -> OfferChannelStateApplyResult:
    """
    Apply the current offer state to its Telegram channel post and classify the
    Telegram response for worker pacing/retry decisions.
    """
    if current_server() != SERVER_FOREIGN:
        return OfferChannelStateApplyResult(ok=False, response_class="skipped", reason="non_foreign_server")
    if (
        configured_telegram_delivery_producer_mode()
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        # The Offer mutation is already durable. The Offer edit feeder owns the
        # one eventual channel edit and combines text plus keyboard state.
        return OfferChannelStateApplyResult(
            ok=True,
            response_class="queued",
            reason="telegram_delivery_queue_owned",
        )
    _assert_legacy_channel_editor_owner()

    channel_message_id = telegram_publication_message_id(offer, publication_state)
    if not channel_message_id:
        return OfferChannelStateApplyResult(ok=False, response_class="skipped", reason="missing_channel_message_id")
    if not _finite_int(getattr(offer, "channel_message_id", None)):
        setattr(offer, "channel_message_id", channel_message_id)

    channel_id = settings.channel_id
    if not channel_id:
        return OfferChannelStateApplyResult(ok=False, response_class="skipped", reason="missing_channel_id")

    status = _status_value(getattr(offer, "status", None))
    history_tag = get_offer_channel_history_tag(offer, traded_quantity=traded_quantity)
    # While unpublished offers are waiting, only trade presentation may spend
    # the publisher bot quota. Expiry/overtime/cancel defer to reconciliation.
    if not channel_edit_is_trade_priority(offer, traded_quantity=traded_quantity):
        try:
            send_backlog_due = await publication_send_backlog_due_count()
        except Exception as exc:
            logger.debug(
                "Failed to read publication backlog before channel edit",
                extra={
                    "event": "telegram.offer_channel_edit_backlog_check_failed",
                    "offer_id": getattr(offer, "id", None),
                    "reason": reason,
                    "error_class": type(exc).__name__,
                },
            )
            send_backlog_due = 0
        if send_backlog_due > 0:
            return OfferChannelStateApplyResult(
                ok=False,
                response_class="deferred",
                reason="send_backlog_defers_non_trade_edit",
            )

    # Offer posts are published with the primary bot's inline keyboard. Telegram
    # rejects a second (editor) bot editing those messages (MESSAGE_ID_INVALID),
    # even for text-only edits. Always mutate with the publisher bot.
    publisher_bot_token = None

    try:
        if status and status != OfferStatus.ACTIVE.value:
            text = build_offer_channel_message(offer, history_tag=history_tag)
            # Pure expiry: tag the post only; leave buttons untouched.
            # Trade / cancel / partial-trade-then-expire: one editMessageText that
            # updates text and clears the keyboard together.
            clear_buttons = _terminal_channel_edit_should_clear_buttons(
                offer,
                traded_quantity=traded_quantity,
            )
            if clear_buttons:
                result = await telegram_gateway.edit_message_text(
                    channel_id,
                    channel_message_id,
                    text,
                    reply_markup={"inline_keyboard": []},
                    timeout=timeout,
                    bot_token=publisher_bot_token,
                    idempotency_key=f"offer-channel-state:{getattr(offer, 'id', '')}:{status}",
                )
            else:
                result = await telegram_gateway.edit_message_text(
                    channel_id,
                    channel_message_id,
                    text,
                    timeout=timeout,
                    bot_token=publisher_bot_token,
                    idempotency_key=f"offer-channel-state:{getattr(offer, 'id', '')}:{status}",
                )
        else:
            from core.trading_settings import get_trading_settings

            projection = project_offer_channel_lifecycle(
                offer,
                normal_lifetime_minutes=int(
                    getattr(get_trading_settings(), "offer_expiry_minutes", 0) or 0
                ),
            )
            phase = projection.phase.value
            markup = build_offer_channel_reply_markup(
                offer,
                accepts_new_public_interaction=projection.accepts_new_public_interaction,
            )
            needs_text_edit = (
                offer_channel_overtime_marker_visible(offer, lifecycle_phase=phase)
                or not projection.accepts_new_public_interaction
            )
            if needs_text_edit:
                result = await telegram_gateway.edit_message_text(
                    channel_id,
                    channel_message_id,
                    build_offer_channel_message(
                        offer,
                        lifecycle_phase=phase,
                    ),
                    reply_markup=markup or {"inline_keyboard": []},
                    timeout=timeout,
                    bot_token=publisher_bot_token,
                    idempotency_key=(
                        f"offer-channel-lifecycle:{getattr(offer, 'id', '')}:{phase}"
                    ),
                )
            else:
                result = await telegram_gateway.edit_message_reply_markup(
                    channel_id,
                    channel_message_id,
                    reply_markup=markup,
                    timeout=timeout,
                    bot_token=publisher_bot_token,
                    idempotency_key=f"offer-channel-buttons:{getattr(offer, 'id', '')}",
                )
        return _classify_gateway_result(result)
    except Exception as exc:
        logger.debug(
            "Failed to apply Telegram channel offer state",
            extra={
                "event": "telegram.offer_channel_state_failed",
                "offer_id": getattr(offer, "id", None),
                "reason": reason,
                "error_class": type(exc).__name__,
            },
        )
        return OfferChannelStateApplyResult(
            ok=False,
            response_class="transport",
            reason="unexpected_exception",
            error=type(exc).__name__,
        )
