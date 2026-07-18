"""Durable generic Telegram private-message outbox service."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import telegram_gateway
from core.server_routing import SERVER_FOREIGN
from core.services.bot_access_policy import evaluate_bot_access
from core.services.customer_relation_service import get_active_customer_relation_for_user
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    configured_telegram_delivery_runtime,
)
from core.telegram_delivery_notification_action_contract import (
    TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES,
    telegram_notification_action_policy,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.utils import utc_now
from models.telegram_notification_outbox import (
    TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES,
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User


TELEGRAM_NOTIFICATION_OUTBOX_WORKER_ID = "telegram-notification-outbox"
TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED = "project_user_joined"
TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE = "offer_repeat_response"

TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH = "menu_refresh"
TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_STALE_BUTTON = "stale_button"
TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT = "منو با آخرین وضعیت به‌روزرسانی شد"
TELEGRAM_OFFER_REPEAT_STALE_BUTTON_TEXT = (
    "متن این دکمه قدیمی است. منو با آخرین وضعیت به‌روزرسانی شد؛ "
    "دکمه جدید را بزنید."
)

TELEGRAM_NOTIFICATION_DELIVERY_STATUS_NO_ROW = "no_row"
TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SENT = "sent"
TELEGRAM_NOTIFICATION_DELIVERY_STATUS_RETRY_PENDING = "retry_pending"
TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SKIPPED = "skipped"
TELEGRAM_NOTIFICATION_DELIVERY_STATUS_TERMINAL_FAILED = "terminal_failed"
TELEGRAM_NOTIFICATION_DELIVERY_STATUS_ALREADY_TERMINAL = "already_terminal"
TELEGRAM_NOTIFICATION_DELIVERY_STATUS_BLOCKED_WRONG_SERVER = "blocked_wrong_server"

MIN_RETRY_DELAY_SECONDS = 1
MAX_RETRY_DELAY_SECONDS = 300
MAX_RATE_LIMIT_RETRY_AFTER_SECONDS = 24 * 60 * 60
MAX_RETRY_JITTER_SECONDS = 5
MAX_RETRY_ATTEMPTS = 8
TEXT_MAX_LENGTH = 4096
ALLOWED_NOTIFICATION_PARSE_MODES = frozenset({"Markdown", "MarkdownV2", "HTML"})

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
    "message text is empty",
    "message is too long",
    "text must be non-empty",
    "can't parse entities",
    "parse mode",
    "entity",
)

TelegramSendCallable = Callable[..., Awaitable[telegram_gateway.TelegramGatewayResult]]


def _assert_legacy_direct_delivery_owner() -> None:
    runtime = configured_telegram_delivery_runtime()
    if not runtime.legacy_workers_enabled or runtime.queue_worker_enabled:
        raise TelegramDeliveryRuntimeConfigurationError(
            "legacy_notification_outbox_direct_sender_is_not_runtime_owner"
        )


@dataclass(frozen=True, slots=True)
class TelegramNotificationRecipient:
    user_id: int
    telegram_id: int


@dataclass(frozen=True, slots=True)
class TelegramNotificationFailureClassification:
    status: TelegramNotificationOutboxStatus
    reason: str
    retry_after_seconds: int | None = None
    error_class: str | None = None
    error_message: str | None = None
    alert_required: bool = False


@dataclass(frozen=True, slots=True)
class TelegramNotificationDeliveryResult:
    status: str
    current_server: str
    outbox: TelegramNotificationOutbox | None = None
    recipient_user_id: int | None = None
    telegram_message_id: int | None = None
    reason: str | None = None
    retry_after_seconds: int | None = None
    alert_required: bool = False


@dataclass(frozen=True, slots=True)
class TelegramNotificationEnqueueResult:
    outbox: TelegramNotificationOutbox
    created: bool


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _coerce_int(value: Any) -> int | None:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _is_terminal(outbox: TelegramNotificationOutbox | Any | None) -> bool:
    return _enum_value(getattr(outbox, "status", None)) in TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES


def _sanitize_error_message(value: Any, *, max_length: int = 500) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:max_length] if text else None


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
    return min(MAX_RATE_LIMIT_RETRY_AFTER_SECONDS, max(MIN_RETRY_DELAY_SECONDS, retry_after))


def _stable_retry_jitter_seconds(
    *,
    outbox: TelegramNotificationOutbox | Any,
    attempt_count: int,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> int:
    if max_jitter_seconds <= 0:
        return 0
    seed = (
        f"{getattr(outbox, 'id', '')}:"
        f"{getattr(outbox, 'dedupe_key', '')}:"
        f"{getattr(outbox, 'recipient_user_id', '')}:"
        f"{attempt_count}"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (max_jitter_seconds + 1)


def bounded_retry_delay_seconds(
    classification: TelegramNotificationFailureClassification,
    *,
    outbox: TelegramNotificationOutbox | Any,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> int:
    if classification.retry_after_seconds is not None:
        base_delay = classification.retry_after_seconds
        max_delay = MAX_RATE_LIMIT_RETRY_AFTER_SECONDS
    else:
        attempt_count = max(1, int(getattr(outbox, "attempt_count", 0) or 0))
        base_delay = min(MAX_RETRY_DELAY_SECONDS, 2 ** min(attempt_count, 8))
        max_delay = MAX_RETRY_DELAY_SECONDS
    jitter = _stable_retry_jitter_seconds(
        outbox=outbox,
        attempt_count=max(1, int(getattr(outbox, "attempt_count", 0) or 0)),
        max_jitter_seconds=max_jitter_seconds,
    )
    return min(max_delay, max(MIN_RETRY_DELAY_SECONDS, base_delay + jitter))


def retry_attempts_exhausted(
    outbox: TelegramNotificationOutbox | Any,
    *,
    max_attempts: int = MAX_RETRY_ATTEMPTS,
) -> bool:
    return int(getattr(outbox, "attempt_count", 0) or 0) >= max(1, int(max_attempts))


def retry_attempts_should_terminalize(
    classification: TelegramNotificationFailureClassification,
    outbox: TelegramNotificationOutbox | Any,
    *,
    max_attempts: int = MAX_RETRY_ATTEMPTS,
) -> bool:
    if classification.reason == "telegram_rate_limited":
        return False
    return retry_attempts_exhausted(outbox, max_attempts=max_attempts)


def validate_telegram_notification_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("text_required")
    if len(cleaned) > TEXT_MAX_LENGTH:
        raise ValueError("text_too_long")
    return cleaned


def validate_telegram_notification_parse_mode(value: Any) -> str | None:
    if value is None:
        return None
    parse_mode = str(value).strip()
    if parse_mode not in ALLOWED_NOTIFICATION_PARSE_MODES:
        raise ValueError("telegram_notification_parse_mode_invalid")
    return parse_mode


def telegram_notification_dedupe_key(
    *,
    source_type: str,
    source_id: str | int | None,
    recipient_user_id: int,
) -> str:
    source = str(source_type or "").strip() or "generic"
    source_identity = str(source_id if source_id is not None else "none").strip() or "none"
    return f"telegram-notification:{source}:{source_identity}:{int(recipient_user_id)}"


def validate_offer_repeat_response_contract(
    *,
    text: str,
    response_kind: str,
) -> tuple[str, str]:
    kind = str(response_kind or "").strip().lower()
    expected_text = {
        TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH: (
            TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT
        ),
        TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_STALE_BUTTON: (
            TELEGRAM_OFFER_REPEAT_STALE_BUTTON_TEXT
        ),
    }.get(kind)
    if expected_text is None:
        raise ValueError("offer_repeat_response_kind_invalid")
    cleaned_text = validate_telegram_notification_text(text)
    if cleaned_text != expected_text:
        raise ValueError("offer_repeat_response_text_invalid")
    return kind, cleaned_text


def _validate_existing_notification_identity(
    outbox: TelegramNotificationOutbox,
    *,
    source_type: str,
    source_id: str | None,
    recipient_user_id: int,
    text: str,
    parse_mode: str | None,
    extra_payload: Mapping[str, Any],
) -> None:
    if (
        str(outbox.source_type or "") != source_type
        or str(outbox.source_id or "") != str(source_id or "")
        or int(outbox.recipient_user_id or 0) != recipient_user_id
        or str(outbox.text or "") != text
        or outbox.parse_mode != parse_mode
        or dict(outbox.extra_payload or {}) != dict(extra_payload)
    ):
        raise ValueError("telegram_notification_dedupe_payload_conflict")


async def enqueue_telegram_notification_once(
    db: AsyncSession,
    *,
    recipient: TelegramNotificationRecipient,
    text: str,
    source_type: str,
    source_id: str | int | None,
    parse_mode: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> TelegramNotificationEnqueueResult:
    """Create one idempotent outbox intent without hiding payload conflicts."""
    cleaned_text = validate_telegram_notification_text(text)
    source = str(source_type or "").strip() or "generic"
    source_identity = str(source_id if source_id is not None else "none").strip() or None
    if len(source) > 80 or (source_identity is not None and len(source_identity) > 120):
        raise ValueError("telegram_notification_source_identity_invalid")
    if (
        isinstance(recipient.user_id, bool)
        or not isinstance(recipient.user_id, int)
        or recipient.user_id <= 0
        or isinstance(recipient.telegram_id, bool)
        or not isinstance(recipient.telegram_id, int)
        or recipient.telegram_id <= 0
    ):
        raise ValueError("telegram_notification_recipient_invalid")
    recipient_user_id = recipient.user_id
    telegram_id = recipient.telegram_id
    payload = dict(extra_payload or {})
    dedupe_key = telegram_notification_dedupe_key(
        source_type=source,
        source_id=source_identity,
        recipient_user_id=recipient_user_id,
    )
    if len(dedupe_key) > 192:
        raise ValueError("telegram_notification_dedupe_key_too_long")

    existing = (
        await db.execute(
            select(TelegramNotificationOutbox).where(
                TelegramNotificationOutbox.dedupe_key == dedupe_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        _validate_existing_notification_identity(
            existing,
            source_type=source,
            source_id=source_identity,
            recipient_user_id=recipient_user_id,
            text=cleaned_text,
            parse_mode=parse_mode,
            extra_payload=payload,
        )
        return TelegramNotificationEnqueueResult(outbox=existing, created=False)

    row = TelegramNotificationOutbox(
        dedupe_key=dedupe_key,
        source_type=source,
        source_id=source_identity,
        recipient_user_id=recipient_user_id,
        telegram_id_at_enqueue=telegram_id,
        text=cleaned_text,
        parse_mode=parse_mode,
        status=TelegramNotificationOutboxStatus.PENDING,
        attempt_count=0,
        extra_payload=payload,
        created_at=utc_now(),
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(
                select(TelegramNotificationOutbox).where(
                    TelegramNotificationOutbox.dedupe_key == dedupe_key
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        _validate_existing_notification_identity(
            existing,
            source_type=source,
            source_id=source_identity,
            recipient_user_id=recipient_user_id,
            text=cleaned_text,
            parse_mode=parse_mode,
            extra_payload=payload,
        )
        return TelegramNotificationEnqueueResult(outbox=existing, created=False)
    return TelegramNotificationEnqueueResult(outbox=row, created=True)


async def enqueue_offer_repeat_response_notification(
    db: AsyncSession,
    *,
    recipient: TelegramNotificationRecipient,
    source_id: str,
    response_kind: str,
    text: str,
) -> TelegramNotificationEnqueueResult:
    normalized_source_id = str(source_id or "").strip()
    if not normalized_source_id or len(normalized_source_id) > 120:
        raise ValueError("offer_repeat_response_source_id_invalid")
    kind, cleaned_text = validate_offer_repeat_response_contract(
        text=text,
        response_kind=response_kind,
    )
    return await enqueue_telegram_notification_once(
        db,
        recipient=recipient,
        text=cleaned_text,
        source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
        source_id=normalized_source_id,
        parse_mode=None,
        extra_payload={"response_kind": kind},
    )


async def enqueue_telegram_action_notification_once(
    db: AsyncSession,
    *,
    recipient: TelegramNotificationRecipient,
    action: TelegramDeliveryAction | str,
    source_id: str,
    text: str,
    user_sync_version: int,
    parse_mode: str | None = None,
    reply_markup: Mapping[str, Any] | None = None,
) -> TelegramNotificationEnqueueResult:
    """Persist one allowlisted private ``sendMessage`` action intent."""
    policy = telegram_notification_action_policy(action)
    if policy.action == TelegramDeliveryAction.ACCOUNT_STATUS:
        raise ValueError("telegram_account_status_requires_state_contract")
    normalized_source_id = str(source_id or "").strip()
    if not normalized_source_id or len(normalized_source_id) > 120:
        raise ValueError("telegram_notification_action_source_id_invalid")
    normalized_parse_mode = validate_telegram_notification_parse_mode(parse_mode)
    if (
        isinstance(user_sync_version, bool)
        or not isinstance(user_sync_version, int)
        or user_sync_version <= 0
    ):
        raise ValueError("telegram_notification_action_user_version_invalid")
    extra_payload: dict[str, Any] = {
        "queue_action": policy.action.value,
        "user_sync_version": user_sync_version,
    }
    if reply_markup is not None:
        if not isinstance(reply_markup, Mapping):
            raise ValueError("telegram_notification_action_reply_markup_invalid")
        extra_payload["reply_markup"] = dict(reply_markup)
    return await enqueue_telegram_notification_once(
        db,
        recipient=recipient,
        text=text,
        source_type=policy.source_type,
        source_id=normalized_source_id,
        parse_mode=normalized_parse_mode,
        extra_payload=extra_payload,
    )


async def enqueue_account_status_telegram_notification_once(
    db: AsyncSession,
    *,
    recipient: TelegramNotificationRecipient,
    source_id: str,
    text: str,
    account_status: Any,
    messenger_blocked: bool,
    user_sync_version: int,
    parse_mode: str | None = "Markdown",
) -> TelegramNotificationEnqueueResult:
    """Persist an account notice tied to the user's current account state."""
    policy = telegram_notification_action_policy(TelegramDeliveryAction.ACCOUNT_STATUS)
    normalized_source_id = str(source_id or "").strip()
    if not normalized_source_id or len(normalized_source_id) > 120:
        raise ValueError("telegram_notification_action_source_id_invalid")
    normalized_status = str(
        getattr(account_status, "value", account_status) or ""
    ).strip().lower()
    if normalized_status not in {"active", "inactive"}:
        raise ValueError("telegram_account_status_state_invalid")
    if not isinstance(messenger_blocked, bool):
        raise ValueError("telegram_account_status_block_state_invalid")
    if (
        isinstance(user_sync_version, bool)
        or not isinstance(user_sync_version, int)
        or user_sync_version <= 0
    ):
        raise ValueError("telegram_account_status_user_version_invalid")
    return await enqueue_telegram_notification_once(
        db,
        recipient=recipient,
        text=text,
        source_type=policy.source_type,
        source_id=normalized_source_id,
        parse_mode=validate_telegram_notification_parse_mode(parse_mode),
        extra_payload={
            "account_status": normalized_status,
            "messenger_blocked": messenger_blocked,
            "queue_action": policy.action.value,
            "user_sync_version": user_sync_version,
        },
    )


async def enqueue_telegram_notifications(
    db: AsyncSession,
    *,
    recipients: Iterable[TelegramNotificationRecipient],
    text: str,
    source_type: str,
    source_id: str | int | None,
    parse_mode: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> list[TelegramNotificationOutbox]:
    cleaned_text = validate_telegram_notification_text(text)
    source = str(source_type or "").strip() or "generic"
    source_identity = str(source_id if source_id is not None else "none").strip() or None
    current_time = utc_now()
    rows: list[TelegramNotificationOutbox] = []
    seen_user_ids: set[int] = set()
    for recipient in recipients:
        user_id = int(recipient.user_id)
        telegram_id = int(recipient.telegram_id)
        if user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        rows.append(
            TelegramNotificationOutbox(
                dedupe_key=telegram_notification_dedupe_key(
                    source_type=source,
                    source_id=source_identity,
                    recipient_user_id=user_id,
                ),
                source_type=source,
                source_id=source_identity,
                recipient_user_id=user_id,
                telegram_id_at_enqueue=telegram_id,
                text=cleaned_text,
                parse_mode=parse_mode,
                status=TelegramNotificationOutboxStatus.PENDING,
                attempt_count=0,
                extra_payload=dict(extra_payload or {}),
                created_at=current_time,
            )
        )
    if rows:
        db.add_all(rows)
        await db.flush()
    return rows


def classify_telegram_notification_failure(
    result: telegram_gateway.TelegramGatewayResult,
) -> TelegramNotificationFailureClassification:
    status_code = result.status_code
    if status_code is None and isinstance(result.response_json, Mapping):
        status_code = _coerce_int(result.response_json.get("error_code"))

    error_text = _telegram_error_text(result)
    error_class = result.error or (f"HTTP{status_code}" if status_code is not None else "TelegramUnknownError")
    error_message = _sanitize_error_message(result.response_text or error_text or result.error)

    if result.error == "missing_bot_token":
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
            reason="telegram_missing_bot_token",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )
    if status_code == 429:
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.RETRYABLE_FAILED,
            reason="telegram_rate_limited",
            retry_after_seconds=_retry_after_from_result(result),
            error_class=error_class,
            error_message=error_message,
        )
    if status_code is not None and 500 <= status_code <= 599:
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.RETRYABLE_FAILED,
            reason="telegram_server_error",
            error_class=error_class,
            error_message=error_message,
        )
    if str(result.error or "").lower() in _RETRYABLE_ERROR_CLASSES:
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.RETRYABLE_FAILED,
            reason="telegram_transport_error",
            error_class=error_class,
            error_message=error_message,
        )
    if any(pattern in error_text for pattern in _USER_UNREACHABLE_PATTERNS):
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason="telegram_user_unreachable",
            error_class=error_class,
            error_message=error_message,
        )
    if status_code in {401, 404} or "unauthorized" in error_text or "invalid token" in error_text:
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
            reason="telegram_invalid_bot_config",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )
    if status_code == 400 and any(pattern in error_text for pattern in _MALFORMED_PAYLOAD_PATTERNS):
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
            reason="telegram_malformed_payload",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )
    if status_code is None:
        return TelegramNotificationFailureClassification(
            status=TelegramNotificationOutboxStatus.RETRYABLE_FAILED,
            reason="telegram_unknown_transport_failure",
            error_class=error_class,
            error_message=error_message,
        )
    return TelegramNotificationFailureClassification(
        status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
        reason="telegram_unhandled_error",
        error_class=error_class,
        error_message=error_message,
        alert_required=True,
    )


async def _mark_outbox(
    db: AsyncSession,
    *,
    outbox: TelegramNotificationOutbox,
    status: TelegramNotificationOutboxStatus,
    current_time: datetime,
    reason: str,
    telegram_id_at_send: int | None = None,
    telegram_message_id: int | None = None,
    retry_after_seconds: int | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    outbox.status = status
    outbox.reason = reason
    outbox.telegram_id_at_send = telegram_id_at_send
    outbox.telegram_message_id = telegram_message_id
    outbox.last_error_class = error_class
    outbox.last_error_message = error_message
    outbox.worker_id = None
    outbox.lease_until = None
    outbox.updated_at = current_time
    if status == TelegramNotificationOutboxStatus.RETRYABLE_FAILED:
        delay = max(MIN_RETRY_DELAY_SECONDS, int(retry_after_seconds or MIN_RETRY_DELAY_SECONDS))
        outbox.next_retry_at = current_time + timedelta(seconds=delay)
        outbox.terminal_at = None
    else:
        outbox.next_retry_at = None
        outbox.terminal_at = current_time
        if status == TelegramNotificationOutboxStatus.SENT:
            outbox.sent_at = current_time
    await db.flush()


async def claim_next_telegram_notification_outbox(
    db: AsyncSession,
    *,
    current_server: str,
    lease_seconds: int,
    worker_id: str = TELEGRAM_NOTIFICATION_OUTBOX_WORKER_ID,
    now: datetime | None = None,
) -> TelegramNotificationOutbox | None:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        return None

    current_time = now or utc_now()
    stmt = (
        select(TelegramNotificationOutbox)
        .where(
            TelegramNotificationOutbox.status.in_(
                [
                    TelegramNotificationOutboxStatus.PENDING,
                    TelegramNotificationOutboxStatus.RETRYABLE_FAILED,
                ]
            ),
            TelegramNotificationOutbox.queue_job_id.is_(None),
            TelegramNotificationOutbox.queue_handed_off_at.is_(None),
            TelegramNotificationOutbox.source_type.not_in(
                tuple(
                    sorted(
                        {
                            TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
                            TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
                            *TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES,
                        }
                    )
                )
            ),
            TelegramNotificationOutbox.worker_id.is_(None),
            TelegramNotificationOutbox.lease_until.is_(None),
            or_(
                TelegramNotificationOutbox.next_retry_at.is_(None),
                TelegramNotificationOutbox.next_retry_at <= current_time,
            ),
        )
        .order_by(TelegramNotificationOutbox.next_retry_at.asc().nullsfirst(), TelegramNotificationOutbox.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await db.execute(stmt)
    outbox = result.scalar_one_or_none()
    if outbox is None:
        return None
    outbox.status = TelegramNotificationOutboxStatus.SENDING
    outbox.worker_id = worker_id
    outbox.lease_until = current_time + timedelta(seconds=max(1, int(lease_seconds)))
    outbox.attempt_count = int(outbox.attempt_count or 0) + 1
    outbox.updated_at = current_time
    await db.flush()
    return outbox


async def recover_expired_telegram_notification_outbox_leases(
    db: AsyncSession,
    *,
    current_server: str,
    lease_seconds: int,
    max_rows: int = 100,
    now: datetime | None = None,
) -> list[TelegramNotificationOutbox]:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        return []
    current_time = now or utc_now()
    stmt = (
        select(TelegramNotificationOutbox)
        .where(
            TelegramNotificationOutbox.status == TelegramNotificationOutboxStatus.SENDING,
            TelegramNotificationOutbox.lease_until.is_not(None),
            TelegramNotificationOutbox.lease_until <= current_time,
        )
        .order_by(TelegramNotificationOutbox.lease_until.asc(), TelegramNotificationOutbox.id.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, int(max_rows)))
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    for row in rows:
        row.status = TelegramNotificationOutboxStatus.RETRYABLE_FAILED
        row.reason = "lease_expired"
        row.worker_id = None
        row.lease_until = None
        row.next_retry_at = current_time + timedelta(seconds=max(1, int(lease_seconds)))
        row.updated_at = current_time
    if rows:
        await db.flush()
    return rows


async def _outbox_excludes_current_customer(db: AsyncSession, outbox: TelegramNotificationOutbox, user: User) -> bool:
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, Mapping) or extra_payload.get("exclude_customers") is not True:
        return False
    relation = await get_active_customer_relation_for_user(db, int(user.id))
    return relation is not None


async def deliver_claimed_telegram_notification_outbox(
    db: AsyncSession,
    outbox: TelegramNotificationOutbox,
    *,
    current_server: str,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    bot_token: str | None = None,
    now: datetime | None = None,
) -> TelegramNotificationDeliveryResult:
    _assert_legacy_direct_delivery_owner()
    normalized_server = str(current_server or "").strip().lower()
    recipient_user_id = _coerce_int(getattr(outbox, "recipient_user_id", None))
    if normalized_server != SERVER_FOREIGN:
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_BLOCKED_WRONG_SERVER,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            reason="telegram_foreign_only",
        )

    current_time = now or utc_now()
    if _is_terminal(outbox):
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_ALREADY_TERMINAL,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            telegram_message_id=_coerce_int(getattr(outbox, "telegram_message_id", None)),
            reason=getattr(outbox, "reason", None),
        )

    try:
        message = validate_telegram_notification_text(outbox.text)
    except Exception:
        await _mark_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
            current_time=current_time,
            reason="notification_invalid_content",
            error_class="TelegramNotificationPayloadError",
            error_message="notification_invalid_content",
        )
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_TERMINAL_FAILED,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            reason="notification_invalid_content",
            alert_required=True,
        )

    user = await db.get(User, int(outbox.recipient_user_id)) if recipient_user_id is not None else None
    telegram_id = _coerce_int(getattr(user, "telegram_id", None))
    if user is None or telegram_id is None:
        reason = "telegram_user_missing_current" if user is None else "telegram_unlinked_current"
        await _mark_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            current_time=current_time,
            reason=reason,
            error_class="TelegramUserUnavailable",
            error_message=reason,
        )
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SKIPPED,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            reason=reason,
        )

    access_decision = await evaluate_bot_access(db, user)
    if not access_decision.allowed:
        reason = access_decision.reason or "bot_access_denied_current"
        await _mark_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            current_time=current_time,
            reason=reason,
            error_class="BotAccessDenied",
            error_message=reason,
        )
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SKIPPED,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            reason=reason,
        )

    if await _outbox_excludes_current_customer(db, outbox, user):
        await _mark_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            current_time=current_time,
            reason="customer_excluded_current",
            error_class="CustomerExcluded",
            error_message="customer_excluded_current",
        )
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SKIPPED,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            reason="customer_excluded_current",
        )

    correlation_key = f"telegram-notification:{outbox.dedupe_key}:attempt:{int(outbox.attempt_count or 0)}"
    gateway_result = await gateway_send(
        telegram_id,
        message,
        parse_mode=outbox.parse_mode,
        bot_token=bot_token,
        idempotency_key=correlation_key,
    )
    if gateway_result.ok:
        telegram_message_id = gateway_result.message_id
        await _mark_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SENT,
            current_time=current_time,
            reason="sent",
            telegram_id_at_send=telegram_id,
            telegram_message_id=telegram_message_id,
        )
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SENT,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            telegram_message_id=telegram_message_id,
            reason="sent",
        )

    classification = classify_telegram_notification_failure(gateway_result)
    if classification.status == TelegramNotificationOutboxStatus.RETRYABLE_FAILED and retry_attempts_should_terminalize(
        classification,
        outbox,
    ):
        await _mark_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
            current_time=current_time,
            reason="telegram_retry_exhausted",
            telegram_id_at_send=telegram_id,
            error_class=classification.error_class,
            error_message=classification.error_message,
        )
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_TERMINAL_FAILED,
            current_server=normalized_server,
            outbox=outbox,
            recipient_user_id=recipient_user_id,
            reason="telegram_retry_exhausted",
            alert_required=True,
        )

    retry_delay_seconds = (
        bounded_retry_delay_seconds(classification, outbox=outbox)
        if classification.status == TelegramNotificationOutboxStatus.RETRYABLE_FAILED
        else classification.retry_after_seconds
    )
    await _mark_outbox(
        db,
        outbox=outbox,
        status=classification.status,
        current_time=current_time,
        reason=classification.reason,
        telegram_id_at_send=telegram_id,
        retry_after_seconds=retry_delay_seconds,
        error_class=classification.error_class,
        error_message=classification.error_message,
    )
    if classification.status == TelegramNotificationOutboxStatus.RETRYABLE_FAILED:
        result_status = TELEGRAM_NOTIFICATION_DELIVERY_STATUS_RETRY_PENDING
    elif classification.status == TelegramNotificationOutboxStatus.SKIPPED:
        result_status = TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SKIPPED
    else:
        result_status = TELEGRAM_NOTIFICATION_DELIVERY_STATUS_TERMINAL_FAILED
    return TelegramNotificationDeliveryResult(
        status=result_status,
        current_server=normalized_server,
        outbox=outbox,
        recipient_user_id=recipient_user_id,
        reason=classification.reason,
        retry_after_seconds=retry_delay_seconds,
        alert_required=classification.alert_required,
    )


async def claim_and_deliver_next_telegram_notification_outbox(
    db: AsyncSession,
    *,
    current_server: str,
    lease_seconds: int,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    bot_token: str | None = None,
) -> TelegramNotificationDeliveryResult:
    outbox = await claim_next_telegram_notification_outbox(
        db,
        current_server=current_server,
        lease_seconds=lease_seconds,
    )
    if outbox is None:
        return TelegramNotificationDeliveryResult(
            status=TELEGRAM_NOTIFICATION_DELIVERY_STATUS_NO_ROW,
            current_server=str(current_server or "").strip().lower(),
        )
    return await deliver_claimed_telegram_notification_outbox(
        db,
        outbox,
        current_server=current_server,
        gateway_send=gateway_send,
        bot_token=bot_token,
    )
