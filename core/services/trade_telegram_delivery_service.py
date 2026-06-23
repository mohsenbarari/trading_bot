"""Receipt-backed Telegram delivery for trade-completion private messages."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import telegram_gateway
from core.server_routing import SERVER_FOREIGN, current_server as get_current_server
from core.services.bot_access_policy import evaluate_bot_access
from core.services.trade_delivery_receipt_service import (
    TELEGRAM_DESTINATION_SERVER,
    TRADE_COMPLETED_EVENT_TYPE,
    claim_next_receipt_for_delivery,
    claim_receipt_by_identity_for_delivery,
    transition_receipt_status,
    upsert_trade_delivery_receipt,
)
from core.services.trade_notification_audience_service import (
    TELEGRAM_CHANNEL,
    build_trade_completion_notification_audience,
)
from core.utils import utc_now
from models.trade import Trade
from models.trade_delivery_receipt import (
    TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES,
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from models.user import User


logger = logging.getLogger(__name__)

TELEGRAM_TRADE_DELIVERY_WORKER_ID = "telegram-trade-delivery"

TELEGRAM_DELIVERY_STATUS_SENT = "sent"
TELEGRAM_DELIVERY_STATUS_ALREADY_SENT = "already_sent"
TELEGRAM_DELIVERY_STATUS_QUEUED_FOR_FOREIGN = "queued_for_foreign"
TELEGRAM_DELIVERY_STATUS_CLAIM_BUSY = "claim_busy"
TELEGRAM_DELIVERY_STATUS_TERMINAL_PRESERVED = "terminal_preserved"
TELEGRAM_DELIVERY_STATUS_NOT_REQUIRED = "not_required"
TELEGRAM_DELIVERY_STATUS_RETRY_PENDING = "retry_pending"
TELEGRAM_DELIVERY_STATUS_SKIPPED = "skipped"
TELEGRAM_DELIVERY_STATUS_PERMANENT_FAILED = "permanent_failed"
TELEGRAM_DELIVERY_STATUS_BLOCKED_WRONG_SERVER = "blocked_wrong_server"
TELEGRAM_DELIVERY_STATUS_NO_RECEIPT = "no_receipt"

MIN_RETRY_DELAY_SECONDS = 1
MAX_RETRY_DELAY_SECONDS = 300
MAX_RETRY_JITTER_SECONDS = 5

_RETRYABLE_ERROR_CLASSES = {
    "connecterror",
    "connecttimeout",
    "networkerror",
    "pooltimeout",
    "readerror",
    "readtimeout",
    "remoteprotocolerror",
    "timeouterror",
    "writeerror",
    "writetimeout",
}

_USER_UNREACHABLE_PATTERNS = (
    "bot was blocked",
    "blocked by the user",
    "bot can't initiate conversation",
    "bot cannot initiate conversation",
    "chat not found",
    "user is deactivated",
    "user not found",
    "bot was kicked",
    "have no rights",
    "forbidden: bot",
)

_MALFORMED_PAYLOAD_PATTERNS = (
    "can't parse entities",
    "can't parse message text",
    "unsupported parse_mode",
    "message text is empty",
    "message is too long",
    "text must be non-empty",
    "can't find end tag",
    "entity",
    "parse mode",
)


TelegramSendCallable = Callable[..., Awaitable[telegram_gateway.TelegramGatewayResult]]


@dataclass(frozen=True, slots=True)
class TelegramDeliveryClassification:
    status: TradeDeliveryReceiptStatus
    reason: str
    error_class: str | None = None
    error_message: str | None = None
    retry_after_seconds: int | None = None
    alert_required: bool = False


@dataclass(frozen=True, slots=True)
class TelegramTradeDeliveryResult:
    status: str
    trade_number: int | None
    recipient_user_id: int | None
    current_server: str
    destination_server: str
    receipt: TradeDeliveryReceipt | Any | None = None
    receipt_created: bool = False
    receipt_changed: bool = False
    telegram_message_id: int | None = None
    retry_after_seconds: int | None = None
    next_retry_at: datetime | None = None
    reason: str | None = None
    alert_required: bool = False


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _receipt_status(receipt: TradeDeliveryReceipt | Any | None) -> str | None:
    if receipt is None:
        return None
    return _enum_value(getattr(receipt, "status", None))


def _is_sent(receipt: TradeDeliveryReceipt | Any | None) -> bool:
    return _receipt_status(receipt) == TradeDeliveryReceiptStatus.SENT.value


def _is_terminal(receipt: TradeDeliveryReceipt | Any | None) -> bool:
    status = _receipt_status(receipt)
    return bool(status and status in TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES)


def _normalize_current_server(value: str | None) -> str:
    return str(value or "").strip().lower()


def _sanitize_error_message(value: Any, *, max_length: int = 500) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text[:max_length]


def _telegram_error_text(result: telegram_gateway.TelegramGatewayResult) -> str:
    parts: list[str] = []
    if result.response_text:
        parts.append(str(result.response_text))
    if result.response_json:
        for key in ("description", "message", "error"):
            raw = result.response_json.get(key)
            if raw:
                parts.append(str(raw))
    if result.error:
        parts.append(str(result.error))
    return " ".join(parts).lower()


def _retry_after_from_result(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    raw_retry_after = None
    if isinstance(result.response_json, Mapping):
        parameters = result.response_json.get("parameters")
        if isinstance(parameters, Mapping):
            raw_retry_after = parameters.get("retry_after")
    retry_after = _coerce_int(raw_retry_after)
    if retry_after is None:
        return None
    return min(MAX_RETRY_DELAY_SECONDS, max(MIN_RETRY_DELAY_SECONDS, retry_after))


def _classify_telegram_failure(
    result: telegram_gateway.TelegramGatewayResult,
) -> TelegramDeliveryClassification:
    status_code = result.status_code
    if status_code is None and isinstance(result.response_json, Mapping):
        status_code = _coerce_int(result.response_json.get("error_code"))

    error_text = _telegram_error_text(result)
    error_class = result.error or (f"HTTP{status_code}" if status_code is not None else "TelegramUnknownError")
    error_message = _sanitize_error_message(result.response_text or error_text or result.error)

    if result.error == "missing_bot_token":
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            reason="telegram_missing_bot_token",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )

    normalized_error_class = str(result.error or "").lower()
    if status_code == 429:
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.RETRY_PENDING,
            reason="telegram_rate_limited",
            error_class=error_class,
            error_message=error_message,
            retry_after_seconds=_retry_after_from_result(result),
        )

    if status_code is not None and 500 <= status_code <= 599:
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.RETRY_PENDING,
            reason="telegram_server_error",
            error_class=error_class,
            error_message=error_message,
        )

    if normalized_error_class in _RETRYABLE_ERROR_CLASSES:
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.RETRY_PENDING,
            reason="telegram_transport_error",
            error_class=error_class,
            error_message=error_message,
        )

    if any(pattern in error_text for pattern in _USER_UNREACHABLE_PATTERNS):
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.SKIPPED,
            reason="telegram_user_unreachable",
            error_class=error_class,
            error_message=error_message,
        )

    if status_code in {401, 404} or "unauthorized" in error_text or "invalid token" in error_text:
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            reason="telegram_invalid_bot_config",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )

    if status_code == 400 and any(pattern in error_text for pattern in _MALFORMED_PAYLOAD_PATTERNS):
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            reason="telegram_malformed_payload",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )

    if status_code == 400:
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            reason="telegram_bad_request_permanent",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )

    return TelegramDeliveryClassification(
        status=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
        reason="telegram_unknown_failure",
        error_class=error_class,
        error_message=error_message,
        alert_required=True,
    )


def classify_telegram_gateway_result(
    result: telegram_gateway.TelegramGatewayResult,
) -> TelegramDeliveryClassification:
    if result.ok:
        return TelegramDeliveryClassification(
            status=TradeDeliveryReceiptStatus.SENT,
            reason="telegram_sent",
        )
    return _classify_telegram_failure(result)


def _stable_retry_jitter_seconds(
    *,
    receipt: TradeDeliveryReceipt | Any,
    attempt_count: int,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> int:
    if max_jitter_seconds <= 0:
        return 0
    seed = (
        f"{getattr(receipt, 'id', '')}:"
        f"{getattr(receipt, 'trade_number', '')}:"
        f"{getattr(receipt, 'recipient_user_id', '')}:"
        f"{attempt_count}"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (max_jitter_seconds + 1)


def bounded_retry_delay_seconds(
    classification: TelegramDeliveryClassification,
    *,
    receipt: TradeDeliveryReceipt | Any,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> int:
    if classification.retry_after_seconds is not None:
        base_delay = classification.retry_after_seconds
    else:
        attempt_count = max(1, int(getattr(receipt, "attempt_count", 0) or 0))
        base_delay = min(MAX_RETRY_DELAY_SECONDS, 2 ** min(attempt_count, 8))

    jitter = _stable_retry_jitter_seconds(
        receipt=receipt,
        attempt_count=max(1, int(getattr(receipt, "attempt_count", 0) or 0)),
        max_jitter_seconds=max_jitter_seconds,
    )
    return min(MAX_RETRY_DELAY_SECONDS, max(MIN_RETRY_DELAY_SECONDS, base_delay + jitter))


async def _flush_if_available(db: AsyncSession | Any) -> None:
    flush = getattr(db, "flush", None)
    if callable(flush):
        await flush()


async def _commit_if_requested(db: AsyncSession | Any, commit: bool) -> None:
    if commit and callable(getattr(db, "commit", None)):
        await db.commit()


def _result_scalar_one_or_none(result: Any) -> Any:
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        return scalar_one_or_none()
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        scalar_result = scalars()
        first = getattr(scalar_result, "first", None)
        if callable(first):
            return first()
    return None


async def _load_current_user_for_receipt(db: AsyncSession, receipt: TradeDeliveryReceipt | Any) -> User | None:
    result = await db.execute(
        select(User)
        .where(User.id == int(getattr(receipt, "recipient_user_id")))
        .limit(1)
    )
    return _result_scalar_one_or_none(result)


def _audit_payload(receipt: TradeDeliveryReceipt | Any) -> Mapping[str, Any]:
    payload = getattr(receipt, "audit_payload", None)
    return payload if isinstance(payload, Mapping) else {}


def _message_from_receipt(receipt: TradeDeliveryReceipt | Any) -> str | None:
    message = _audit_payload(receipt).get("message")
    if not isinstance(message, str):
        return None
    normalized = message.strip()
    return normalized or None


async def _mark_telegram_receipt(
    db: AsyncSession,
    *,
    receipt: TradeDeliveryReceipt | Any,
    status: TradeDeliveryReceiptStatus,
    current_time: datetime,
    reason: str,
    telegram_message_id: int | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    next_retry_at: datetime | None = None,
) -> None:
    transition_receipt_status(
        receipt,
        status,
        current_server=TELEGRAM_DESTINATION_SERVER,
        now=current_time,
        reason=reason,
        telegram_message_id=telegram_message_id,
        error_class=error_class,
        error_message=error_message,
        next_retry_at=next_retry_at,
    )
    if status != TradeDeliveryReceiptStatus.RETRY_PENDING:
        receipt.next_retry_at = None
    await _flush_if_available(db)


async def deliver_claimed_telegram_receipt(
    db: AsyncSession,
    *,
    receipt: TradeDeliveryReceipt,
    current_server: str,
    bot_token: str | None = None,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    commit: bool = True,
    now: datetime | None = None,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> TelegramTradeDeliveryResult:
    """Deliver one already-claimed Telegram receipt on the foreign server only."""
    normalized_current_server = _normalize_current_server(current_server)
    trade_number = _coerce_int(getattr(receipt, "trade_number", None))
    recipient_user_id = _coerce_int(getattr(receipt, "recipient_user_id", None))

    if normalized_current_server != SERVER_FOREIGN or _normalize_current_server(getattr(receipt, "destination_server", None)) != SERVER_FOREIGN:
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_BLOCKED_WRONG_SERVER,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            reason="telegram_foreign_only",
        )

    current_time = now or utc_now()
    if _enum_value(getattr(receipt, "channel", None)) != TradeDeliveryChannel.TELEGRAM.value:
        await _mark_telegram_receipt(
            db,
            receipt=receipt,
            status=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            current_time=current_time,
            reason="receipt_channel_is_not_telegram",
            error_class="ReceiptLifecycleError",
            error_message="receipt_channel_is_not_telegram",
        )
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_PERMANENT_FAILED,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            reason="receipt_channel_is_not_telegram",
            alert_required=True,
        )

    if _is_terminal(receipt):
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_ALREADY_SENT if _is_sent(receipt) else TELEGRAM_DELIVERY_STATUS_TERMINAL_PRESERVED,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            telegram_message_id=_coerce_int(getattr(receipt, "telegram_message_id", None)),
            reason=getattr(receipt, "reason", None),
        )

    message = _message_from_receipt(receipt)
    if message is None:
        await _mark_telegram_receipt(
            db,
            receipt=receipt,
            status=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            current_time=current_time,
            reason="telegram_missing_message",
            error_class="TelegramPayloadError",
            error_message="telegram_missing_message",
        )
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_PERMANENT_FAILED,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            reason="telegram_missing_message",
            alert_required=True,
        )

    user = await _load_current_user_for_receipt(db, receipt)
    telegram_id = _coerce_int(getattr(user, "telegram_id", None))
    if user is None or telegram_id is None:
        reason = "telegram_user_missing_current" if user is None else "telegram_unlinked_current"
        await _mark_telegram_receipt(
            db,
            receipt=receipt,
            status=TradeDeliveryReceiptStatus.SKIPPED,
            current_time=current_time,
            reason=reason,
            error_class="TelegramUserUnavailable",
            error_message=reason,
        )
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_SKIPPED,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            reason=reason,
        )

    access_decision = await evaluate_bot_access(db, user)
    if not access_decision.allowed:
        reason = access_decision.reason or "bot_access_denied_current"
        await _mark_telegram_receipt(
            db,
            receipt=receipt,
            status=TradeDeliveryReceiptStatus.SKIPPED,
            current_time=current_time,
            reason=reason,
            error_class="BotAccessDenied",
            error_message=reason,
        )
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_SKIPPED,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            reason=reason,
        )

    idempotency_key = (
        f"trade-telegram:{trade_number}:{recipient_user_id}:"
        f"{getattr(receipt, 'id', 'pending')}:attempt:{getattr(receipt, 'attempt_count', 0) or 0}"
    )
    try:
        gateway_result = await gateway_send(
            telegram_id,
            message,
            parse_mode="HTML",
            bot_token=bot_token,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        gateway_result = telegram_gateway.TelegramGatewayResult(
            ok=False,
            method="sendMessage",
            idempotency_key=idempotency_key,
            error=type(exc).__name__,
        )

    classification = classify_telegram_gateway_result(gateway_result)
    if classification.status == TradeDeliveryReceiptStatus.SENT:
        telegram_message_id = gateway_result.message_id
        await _mark_telegram_receipt(
            db,
            receipt=receipt,
            status=TradeDeliveryReceiptStatus.SENT,
            current_time=current_time,
            reason=classification.reason,
            telegram_message_id=telegram_message_id,
        )
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_SENT,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            telegram_message_id=telegram_message_id,
            reason=classification.reason,
        )

    if classification.status == TradeDeliveryReceiptStatus.RETRY_PENDING:
        delay_seconds = bounded_retry_delay_seconds(
            classification,
            receipt=receipt,
            max_jitter_seconds=max_jitter_seconds,
        )
        next_retry_at = current_time + timedelta(seconds=delay_seconds)
        await _mark_telegram_receipt(
            db,
            receipt=receipt,
            status=TradeDeliveryReceiptStatus.RETRY_PENDING,
            current_time=current_time,
            reason=classification.reason,
            error_class=classification.error_class,
            error_message=classification.error_message,
            next_retry_at=next_retry_at,
        )
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_RETRY_PENDING,
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            retry_after_seconds=delay_seconds,
            next_retry_at=next_retry_at,
            reason=classification.reason,
        )

    await _mark_telegram_receipt(
        db,
        receipt=receipt,
        status=classification.status,
        current_time=current_time,
        reason=classification.reason,
        error_class=classification.error_class,
        error_message=classification.error_message,
    )
    await _commit_if_requested(db, commit)
    return TelegramTradeDeliveryResult(
        status=(
            TELEGRAM_DELIVERY_STATUS_SKIPPED
            if classification.status == TradeDeliveryReceiptStatus.SKIPPED
            else TELEGRAM_DELIVERY_STATUS_PERMANENT_FAILED
        ),
        trade_number=trade_number,
        recipient_user_id=recipient_user_id,
        current_server=normalized_current_server,
        destination_server=TELEGRAM_DESTINATION_SERVER,
        receipt=receipt,
        reason=classification.reason,
        alert_required=classification.alert_required,
    )


async def deliver_telegram_trade_notification(
    db: AsyncSession,
    *,
    trade_number: int,
    recipient_user_id: int,
    message: str | None,
    current_server: str,
    required: bool = True,
    telegram_id: int | None = None,
    trade_id: int | None = None,
    offer_id: int | None = None,
    recipient_role: str = "trade_recipient",
    principal_user_id: int | None = None,
    side: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    event_created_at: datetime | None = None,
    reason: str = "telegram_required",
    worker_id: str = TELEGRAM_TRADE_DELIVERY_WORKER_ID,
    lease_seconds: int = 30,
    bot_token: str | None = None,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    commit: bool = True,
    now: datetime | None = None,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> TelegramTradeDeliveryResult:
    """Upsert, claim, and send one trade Telegram receipt on foreign only."""
    normalized_trade_number = int(trade_number)
    normalized_recipient_user_id = int(recipient_user_id)
    normalized_current_server = _normalize_current_server(current_server)
    current_time = now or utc_now()
    payload = dict(extra_payload or {})
    payload.setdefault("trade_id", trade_id)
    payload.setdefault("trade_number", normalized_trade_number)
    payload.setdefault("offer_id", offer_id)
    payload.setdefault("recipient_user_id", normalized_recipient_user_id)
    payload.setdefault("recipient_role", recipient_role)
    if principal_user_id is not None:
        payload.setdefault("principal_user_id", principal_user_id)
    if side:
        payload.setdefault("side", side)

    upsert_result = await upsert_trade_delivery_receipt(
        db,
        event_type=TRADE_COMPLETED_EVENT_TYPE,
        trade_number=normalized_trade_number,
        recipient_user_id=normalized_recipient_user_id,
        recipient_role=recipient_role,
        channel=TradeDeliveryChannel.TELEGRAM,
        destination_server=TELEGRAM_DESTINATION_SERVER,
        required=required,
        reason=reason,
        trade_id=trade_id,
        offer_id=offer_id,
        event_created_at=event_created_at or current_time,
        audit_payload={
            "message": message,
            "telegram_id_at_audience_build": telegram_id,
            "extra_payload": payload,
        },
        now=current_time,
    )
    receipt = upsert_result.receipt

    if not required:
        if _is_sent(receipt):
            result_status = TELEGRAM_DELIVERY_STATUS_ALREADY_SENT
        elif _is_terminal(receipt) and _receipt_status(receipt) != TradeDeliveryReceiptStatus.NOT_REQUIRED.value:
            result_status = TELEGRAM_DELIVERY_STATUS_TERMINAL_PRESERVED
        else:
            result_status = TELEGRAM_DELIVERY_STATUS_NOT_REQUIRED
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=result_status,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
            reason=reason,
        )

    if normalized_current_server != SERVER_FOREIGN:
        await _commit_if_requested(db, commit)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_QUEUED_FOR_FOREIGN,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
            reason=reason,
        )

    if _is_sent(receipt):
        await _commit_if_requested(db, commit and upsert_result.changed)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_ALREADY_SENT,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            telegram_message_id=_coerce_int(getattr(receipt, "telegram_message_id", None)),
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
            reason=getattr(receipt, "reason", None),
        )

    if upsert_result.terminal_preserved:
        await _commit_if_requested(db, commit and upsert_result.changed)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_TERMINAL_PRESERVED,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
            reason=getattr(receipt, "reason", None),
        )

    claimed_receipt = await claim_receipt_by_identity_for_delivery(
        db,
        event_type=TRADE_COMPLETED_EVENT_TYPE,
        trade_number=normalized_trade_number,
        recipient_user_id=normalized_recipient_user_id,
        channel=TradeDeliveryChannel.TELEGRAM,
        destination_server=TELEGRAM_DESTINATION_SERVER,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=current_time,
    )
    if claimed_receipt is None:
        await _commit_if_requested(db, commit and upsert_result.changed)
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_CLAIM_BUSY,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            receipt=receipt,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
            reason="telegram_claim_busy",
        )

    result = await deliver_claimed_telegram_receipt(
        db,
        receipt=claimed_receipt,
        current_server=normalized_current_server,
        bot_token=bot_token,
        gateway_send=gateway_send,
        commit=commit,
        now=current_time,
        max_jitter_seconds=max_jitter_seconds,
    )
    return replace(
        result,
        receipt_created=upsert_result.created,
        receipt_changed=True,
    )


async def claim_and_deliver_next_telegram_receipt(
    db: AsyncSession,
    *,
    current_server: str | None = None,
    worker_id: str = TELEGRAM_TRADE_DELIVERY_WORKER_ID,
    lease_seconds: int = 30,
    bot_token: str | None = None,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    commit: bool = True,
    now: datetime | None = None,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> TelegramTradeDeliveryResult:
    normalized_current_server = _normalize_current_server(current_server or get_current_server())
    if normalized_current_server != SERVER_FOREIGN:
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_BLOCKED_WRONG_SERVER,
            trade_number=None,
            recipient_user_id=None,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            reason="telegram_foreign_only",
        )

    current_time = now or utc_now()
    receipt = await claim_next_receipt_for_delivery(
        db,
        destination_server=TELEGRAM_DESTINATION_SERVER,
        channel=TradeDeliveryChannel.TELEGRAM,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=current_time,
    )
    if receipt is None:
        return TelegramTradeDeliveryResult(
            status=TELEGRAM_DELIVERY_STATUS_NO_RECEIPT,
            trade_number=None,
            recipient_user_id=None,
            current_server=normalized_current_server,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            reason="no_due_telegram_receipt",
        )

    return await deliver_claimed_telegram_receipt(
        db,
        receipt=receipt,
        current_server=normalized_current_server,
        bot_token=bot_token,
        gateway_send=gateway_send,
        commit=commit,
        now=current_time,
        max_jitter_seconds=max_jitter_seconds,
    )


async def repair_telegram_trade_delivery_for_trade(
    db: AsyncSession,
    trade: Trade | Any,
    *,
    current_server: str,
    commit: bool = True,
    bot_token: str | None = None,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    now: datetime | None = None,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> tuple[TelegramTradeDeliveryResult, ...]:
    audience = await build_trade_completion_notification_audience(db, trade)
    if audience.skipped_reason:
        return ()
    trade_number = _coerce_int(audience.trade_number)
    if trade_number is None:
        return ()

    event_created_at = getattr(trade, "created_at", None)
    results: list[TelegramTradeDeliveryResult] = []
    for recipient in audience.recipients:
        recipient_user_id = _coerce_int(recipient.recipient_user_id)
        if recipient_user_id is None:
            continue
        for requirement in recipient.channel_requirements:
            if _enum_value(requirement.channel) != TELEGRAM_CHANNEL:
                continue
            results.append(
                await deliver_telegram_trade_notification(
                    db,
                    trade_number=trade_number,
                    recipient_user_id=recipient_user_id,
                    message=requirement.message,
                    current_server=current_server,
                    required=bool(requirement.required),
                    telegram_id=requirement.telegram_id,
                    trade_id=_coerce_int(audience.trade_id),
                    offer_id=_coerce_int(audience.offer_id),
                    recipient_role=recipient.recipient_role,
                    principal_user_id=_coerce_int(recipient.principal_user_id),
                    side=recipient.side,
                    extra_payload=recipient.extra_payload,
                    event_created_at=event_created_at,
                    reason=requirement.reason,
                    commit=commit,
                    bot_token=bot_token,
                    gateway_send=gateway_send,
                    now=now,
                    max_jitter_seconds=max_jitter_seconds,
                )
            )
    return tuple(results)


async def repair_telegram_trade_delivery_for_trades(
    db: AsyncSession,
    trades: Sequence[Trade | Any],
    *,
    current_server: str,
    commit: bool = True,
    bot_token: str | None = None,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    now: datetime | None = None,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> tuple[TelegramTradeDeliveryResult, ...]:
    results: list[TelegramTradeDeliveryResult] = []
    for trade in trades:
        results.extend(
            await repair_telegram_trade_delivery_for_trade(
                db,
                trade,
                current_server=current_server,
                commit=commit,
                bot_token=bot_token,
                gateway_send=gateway_send,
                now=now,
                max_jitter_seconds=max_jitter_seconds,
            )
        )
    return tuple(results)
