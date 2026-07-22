from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core import telegram_gateway
from core.config import settings
from core.events import publish_event_sync
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpiryResult,
    OfferExpirySourceSurface,
    expire_offers_authoritatively,
)
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeMode,
    assert_telegram_provider_execution_authority,
    configured_telegram_delivery_producer_mode,
    configured_telegram_delivery_runtime,
)
from core.trading_settings import get_trading_settings_async
from core.utils import utc_now
from models.market_channel_notice_receipt import MarketChannelNoticeReceipt
from models.market_runtime_state import MarketRuntimeState
from models.market_schedule_override import MarketScheduleOverride
from models.offer import Offer, OfferStatus

from .market_schedule_service import (
    MarketScheduleEvaluation,
    NEXT_TRANSITION_SEARCH_DAYS,
    evaluate_market_schedule,
    get_market_timezone_name,
)


logger = logging.getLogger(__name__)

MARKET_OPENED_CHANNEL_NOTICE = "🟢 شروع فعالیت بازار"
MARKET_CLOSED_CHANNEL_NOTICE = "🔴 پایان فعالیت بازار"
MARKET_NOTICE_TRANSITION_OPENED = "opened"
MARKET_NOTICE_TRANSITION_CLOSED = "closed"
MARKET_NOTICE_STATUS_PENDING = "pending"
MARKET_NOTICE_STATUS_SENT = "sent"
MARKET_NOTICE_STATUS_FAILED = "failed"
MARKET_NOTICE_STATUS_SKIPPED = "skipped"
MARKET_NOTICE_STATUS_SUPPRESSED_STALE = "suppressed_stale"
MARKET_NOTICE_RETRY_LIMIT = 20
MARKET_NOTICE_DISABLE_VALUES = {"1", "true", "yes", "on", "disabled"}
MARKET_NOTICE_STALENESS_SECONDS = 120
MARKET_FOREIGN_INDEPENDENT_GRACE_SECONDS = 30
MARKET_RUNTIME_ADVISORY_LOCK_KEY = 202605220901
MARKET_OFFER_ADMISSION_LOCK_TIMEOUT_MS = 5000
MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS = float(
    os.getenv("TRADING_BOT_MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS", "1.0")
)


class MarketOfferAdmissionError(RuntimeError):
    """Base error for final offer-admission rejection."""


class MarketOfferAdmissionClosedError(MarketOfferAdmissionError):
    """Raised when the market closes before an offer creation can commit."""


class MarketOfferAdmissionUnavailableError(MarketOfferAdmissionError):
    """Raised when final admission cannot safely obtain the market fence."""


@dataclass(slots=True)
class MarketTransitionResult:
    changed: bool
    transition: str | None
    state: MarketRuntimeState | None
    expired_offer_ids: tuple[int, ...] = ()


@dataclass(slots=True)
class MarketRuntimeView:
    is_open: bool
    active_web_notice_visible: bool
    offers_since_last_open: int
    last_transition_at: datetime | None
    next_transition_at: datetime | None


@dataclass(slots=True)
class MarketChannelNoticeResult:
    status: str
    dedupe_key: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class MarketChannelNoticeRetrySummary:
    checked: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    disabled: bool = False


_market_runtime_view_cache: tuple[float, MarketRuntimeView] | None = None


def _market_notice_queue_mode() -> bool:
    return (
        configured_telegram_delivery_producer_mode()
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    )


def _legacy_market_notice_has_credentials() -> bool:
    return bool(getattr(settings, "bot_token", None) or os.getenv("BOT_TOKEN"))


def _assert_legacy_market_notice_owner() -> None:
    assert_telegram_provider_execution_authority()
    runtime = configured_telegram_delivery_runtime()
    if (
        not runtime.legacy_workers_enabled
        or runtime.queue_worker_enabled
        or not _legacy_market_notice_has_credentials()
    ):
        raise TelegramDeliveryRuntimeConfigurationError(
            "legacy_market_notice_sender_is_not_runtime_owner"
        )


def invalidate_market_runtime_view_cache() -> None:
    global _market_runtime_view_cache
    _market_runtime_view_cache = None


def _get_cached_market_runtime_view() -> MarketRuntimeView | None:
    if MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS <= 0:
        return None
    cached = _market_runtime_view_cache
    if cached is None:
        return None
    cached_at, value = cached
    if (time.monotonic() - cached_at) <= MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS:
        return value
    invalidate_market_runtime_view_cache()
    return None


def _set_cached_market_runtime_view(value: MarketRuntimeView) -> None:
    global _market_runtime_view_cache
    if MARKET_RUNTIME_VIEW_CACHE_TTL_SECONDS <= 0:
        return
    _market_runtime_view_cache = (time.monotonic(), value)


def _coerce_utc_now(current_time: datetime | None = None) -> datetime:
    if current_time is None:
        return utc_now()
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=ZoneInfo("UTC"))
    return current_time.astimezone(ZoneInfo("UTC"))


def _build_market_event_payload(
    state: MarketRuntimeState,
    *,
    transition: str,
    notice_text: str | None,
) -> dict:
    return {
        "is_open": state.is_open,
        "active_web_notice_visible": state.active_web_notice_visible,
        "offers_since_last_open": state.offers_since_last_open,
        "last_transition_at": state.last_transition_at.isoformat() if state.last_transition_at else None,
        "transition": transition,
        "notice_text": notice_text,
    }


def _build_initial_market_runtime_state(
    evaluation: MarketScheduleEvaluation,
    *,
    current_time: datetime | None = None,
) -> MarketRuntimeState:
    return MarketRuntimeState(
        id=1,
        is_open=evaluation.is_open,
        active_web_notice_visible=False,
        offers_since_last_open=0,
        last_transition_at=_coerce_utc_now(current_time),
    )


async def _acquire_market_runtime_lock(
    db: AsyncSession,
    *,
    lock_timeout_ms: int | None = None,
) -> None:
    if lock_timeout_ms is not None:
        await db.execute(
            text("SELECT set_config('lock_timeout', :lock_timeout, true)"),
            {"lock_timeout": f"{max(1, int(lock_timeout_ms))}ms"},
        )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": MARKET_RUNTIME_ADVISORY_LOCK_KEY},
    )


async def acquire_market_offer_admission_fence(
    db: AsyncSession,
    *,
    current_time: datetime | None = None,
) -> MarketScheduleEvaluation:
    """Serialize final offer admission with local market transitions."""
    try:
        await _acquire_market_runtime_lock(
            db,
            lock_timeout_ms=_market_offer_admission_lock_timeout_ms(),
        )
    except DBAPIError as exc:
        raise MarketOfferAdmissionUnavailableError(
            "market_offer_admission_fence_unavailable"
        ) from exc
    evaluation = await evaluate_current_market_schedule(db, current_time=current_time)
    if not evaluation.is_open:
        raise MarketOfferAdmissionClosedError("market_closed_during_offer_admission")
    return evaluation


def _market_notice_text_for_transition(transition: str) -> str:
    if transition == MARKET_NOTICE_TRANSITION_OPENED:
        return MARKET_OPENED_CHANNEL_NOTICE
    if transition == MARKET_NOTICE_TRANSITION_CLOSED:
        return MARKET_CLOSED_CHANNEL_NOTICE
    raise ValueError(f"unsupported market notice transition: {transition}")


def _market_notice_transition_for_state(state: MarketRuntimeState) -> str:
    return MARKET_NOTICE_TRANSITION_OPENED if state.is_open else MARKET_NOTICE_TRANSITION_CLOSED


def _market_notice_transition_at_for_state(state: MarketRuntimeState) -> datetime | None:
    transition_at = getattr(state, "last_transition_at", None)
    if transition_at is None:
        return None
    return _coerce_utc_now(transition_at)


def market_channel_notice_dedupe_key(
    *,
    transition: str,
    transition_at: datetime,
    notice_text: str,
) -> str:
    transition_time = _coerce_utc_now(transition_at).isoformat().replace("+00:00", "Z")
    digest = hashlib.sha256(f"{transition}|{transition_time}|{notice_text}".encode("utf-8")).hexdigest()[:16]
    return f"market-channel-notice:{transition}:{transition_time}:{digest}"


def market_channel_notice_delivery_disabled() -> bool:
    raw = os.getenv("TRADING_BOT_MARKET_CHANNEL_NOTICE_DISABLED", "").strip().lower()
    return raw in MARKET_NOTICE_DISABLE_VALUES


def _market_notice_retry_limit() -> int:
    raw = os.getenv("TRADING_BOT_MARKET_NOTICE_RETRY_LIMIT")
    if raw is None:
        return MARKET_NOTICE_RETRY_LIMIT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid TRADING_BOT_MARKET_NOTICE_RETRY_LIMIT; falling back to default",
            extra={"event": "market.channel_notice_retry_limit_invalid", "value": raw},
        )
        return MARKET_NOTICE_RETRY_LIMIT


def _market_notice_staleness_seconds() -> int:
    raw = os.getenv("TRADING_BOT_MARKET_NOTICE_STALENESS_SECONDS")
    if raw is None:
        return MARKET_NOTICE_STALENESS_SECONDS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid TRADING_BOT_MARKET_NOTICE_STALENESS_SECONDS; falling back to default",
            extra={"event": "market.channel_notice_staleness_invalid", "value": raw},
        )
        return MARKET_NOTICE_STALENESS_SECONDS


def market_channel_notice_freshness_deadline(
    transition_at: datetime,
) -> datetime | None:
    max_age_seconds = _market_notice_staleness_seconds()
    if max_age_seconds <= 0:
        return None
    return _coerce_utc_now(transition_at) + timedelta(seconds=max_age_seconds)


def _foreign_independent_grace_seconds() -> int:
    raw = os.getenv("TRADING_BOT_MARKET_FOREIGN_INDEPENDENT_GRACE_SECONDS")
    if raw is None:
        return MARKET_FOREIGN_INDEPENDENT_GRACE_SECONDS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid TRADING_BOT_MARKET_FOREIGN_INDEPENDENT_GRACE_SECONDS; falling back to default",
            extra={"event": "market.foreign_independent_grace_invalid", "value": raw},
        )
        return MARKET_FOREIGN_INDEPENDENT_GRACE_SECONDS


def _market_offer_admission_lock_timeout_ms() -> int:
    raw = os.getenv("TRADING_BOT_MARKET_OFFER_ADMISSION_LOCK_TIMEOUT_MS")
    if raw is None:
        return MARKET_OFFER_ADMISSION_LOCK_TIMEOUT_MS
    try:
        return min(30000, max(250, int(raw)))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid TRADING_BOT_MARKET_OFFER_ADMISSION_LOCK_TIMEOUT_MS; falling back to default",
            extra={"event": "market.offer_admission_lock_timeout_invalid", "value": raw},
        )
        return MARKET_OFFER_ADMISSION_LOCK_TIMEOUT_MS


def _market_notice_is_stale(*, transition_at: datetime, now: datetime) -> bool:
    deadline = market_channel_notice_freshness_deadline(transition_at)
    return deadline is not None and _coerce_utc_now(now) > deadline


async def _suppress_stale_market_notice(
    db: AsyncSession,
    receipt: MarketChannelNoticeReceipt,
    *,
    transition: str,
    transition_at: datetime,
    notice_text: str,
    source: str,
    dedupe_key: str,
    now: datetime,
) -> MarketChannelNoticeResult:
    receipt.status = MARKET_NOTICE_STATUS_SUPPRESSED_STALE
    receipt.source = source
    receipt.notice_text = notice_text
    receipt.transition = transition
    receipt.transition_at = transition_at
    receipt.next_retry_at = None
    receipt.last_error_class = "stale_transition"
    receipt.last_error = (
        "Market channel notice suppressed because the transition is older than "
        "TRADING_BOT_MARKET_NOTICE_STALENESS_SECONDS"
    )
    await db.commit()
    logger.info(
        "Suppressed stale market channel notice",
        extra={
            "event": "market.channel_notice_suppressed_stale",
            "transition": transition,
            "dedupe_key": dedupe_key,
            "source": source,
            "transition_at": _coerce_utc_now(transition_at).isoformat(),
            "now": now.isoformat(),
        },
    )
    return MarketChannelNoticeResult(status="skipped", dedupe_key=dedupe_key, reason="stale_transition")


async def _send_market_channel_notice(
    text: str,
    *,
    idempotency_key: str | None = None,
    raise_on_failure: bool = True,
) -> telegram_gateway.TelegramGatewayResult | None:
    channel_id = settings.channel_id
    if not channel_id:
        return None
    _assert_legacy_market_notice_owner()

    result = await telegram_gateway.send_message(
        channel_id,
        text,
        idempotency_key=idempotency_key or f"market-channel-notice:{text}",
    )
    if not result.ok and raise_on_failure:
        raise RuntimeError(f"Telegram market notice failed: {result.error or result.status_code}")
    return result


async def _get_or_create_market_notice_receipt(
    db: AsyncSession,
    *,
    dedupe_key: str,
    transition: str,
    transition_at: datetime,
    notice_text: str,
    source: str,
) -> MarketChannelNoticeReceipt:
    insert_stmt = (
        pg_insert(MarketChannelNoticeReceipt)
        .values(
            dedupe_key=dedupe_key,
            transition=transition,
            transition_at=transition_at,
            notice_text=notice_text,
            source=source,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    await db.execute(insert_stmt)

    result = await db.execute(
        select(MarketChannelNoticeReceipt)
        .where(MarketChannelNoticeReceipt.dedupe_key == dedupe_key)
        .with_for_update()
    )
    receipt = result.scalars().first()
    if receipt is None:
        raise RuntimeError("market notice receipt was not created")
    return receipt


def _compact_market_notice_error(value: object | None) -> str | None:
    if value is None:
        return None
    text_value = str(value)
    return text_value[:500]


async def reconcile_market_channel_notice_for_state(
    db: AsyncSession,
    state: MarketRuntimeState,
    *,
    source: str,
    current_time: datetime | None = None,
) -> MarketChannelNoticeResult:
    """Send or repair the foreign-owned Telegram market notice for one state."""
    if current_server() != SERVER_FOREIGN:
        return MarketChannelNoticeResult(status="skipped", reason="non_foreign_server")
    if market_channel_notice_delivery_disabled():
        return MarketChannelNoticeResult(status="skipped", reason="disabled")
    if not _market_notice_queue_mode() and not _legacy_market_notice_has_credentials():
        # Tokenless API processes observe/produce market state but never create
        # a terminal receipt for an effect they cannot execute.  The
        # credentialed Bot worker materializes and delivers the receipt.
        return MarketChannelNoticeResult(
            status="skipped",
            reason="legacy_executor_not_credentialed",
        )

    transition_at = _market_notice_transition_at_for_state(state)
    if transition_at is None:
        return MarketChannelNoticeResult(status="skipped", reason="missing_transition_at")

    transition = _market_notice_transition_for_state(state)
    notice_text = _market_notice_text_for_transition(transition)
    dedupe_key = market_channel_notice_dedupe_key(
        transition=transition,
        transition_at=transition_at,
        notice_text=notice_text,
    )
    receipt = await _get_or_create_market_notice_receipt(
        db,
        dedupe_key=dedupe_key,
        transition=transition,
        transition_at=transition_at,
        notice_text=notice_text,
        source=source,
    )

    if receipt.status == MARKET_NOTICE_STATUS_SENT:
        return MarketChannelNoticeResult(status="skipped", dedupe_key=dedupe_key, reason="already_sent")
    if receipt.status == MARKET_NOTICE_STATUS_SUPPRESSED_STALE:
        return MarketChannelNoticeResult(status="skipped", dedupe_key=dedupe_key, reason="stale_transition")
    if receipt.status == MARKET_NOTICE_STATUS_SKIPPED and receipt.last_error_class == "missing_channel_id":
        return MarketChannelNoticeResult(status="skipped", dedupe_key=dedupe_key, reason="missing_channel_id")

    now = _coerce_utc_now(current_time)
    if _market_notice_is_stale(transition_at=transition_at, now=now):
        return await _suppress_stale_market_notice(
            db,
            receipt,
            transition=transition,
            transition_at=transition_at,
            notice_text=notice_text,
            source=source,
            dedupe_key=dedupe_key,
            now=now,
        )

    receipt.source = source
    receipt.notice_text = notice_text
    receipt.transition = transition
    receipt.transition_at = transition_at
    queue_mode = _market_notice_queue_mode()
    if not queue_mode:
        receipt.last_attempt_at = now
        receipt.attempt_count = int(receipt.attempt_count or 0) + 1

    channel_id = settings.channel_id
    if not channel_id:
        receipt.status = MARKET_NOTICE_STATUS_SKIPPED
        receipt.channel_id = None
        receipt.last_error_class = "missing_channel_id"
        receipt.last_error = "Telegram channel_id is not configured"
        await db.commit()
        logger.info(
            "Skipped market channel notice because no Telegram channel is configured",
            extra={
                "event": "market.channel_notice_skipped",
                "reason": "missing_channel_id",
                "transition": transition,
                "dedupe_key": dedupe_key,
                "source": source,
            },
        )
        return MarketChannelNoticeResult(status="skipped", dedupe_key=dedupe_key, reason="missing_channel_id")

    receipt.channel_id = str(channel_id)
    if queue_mode:
        # The market transition worker remains the domain producer, but queue-v1
        # exclusively owns every Telegram side effect, retry, and limiter gate.
        # Existing failed receipts retain their durable retry deadline; new
        # pending receipts are immediately visible to the subordinate feeder.
        if receipt.status == MARKET_NOTICE_STATUS_PENDING:
            receipt.next_retry_at = None
        await db.commit()
        return MarketChannelNoticeResult(
            status="queued",
            dedupe_key=dedupe_key,
            reason="telegram_delivery_queue",
        )

    _assert_legacy_market_notice_owner()
    try:
        result = await _send_market_channel_notice(
            notice_text,
            idempotency_key=dedupe_key,
            raise_on_failure=False,
        )
    except Exception as exc:
        receipt.status = MARKET_NOTICE_STATUS_FAILED
        receipt.last_error_class = type(exc).__name__
        receipt.last_error = _compact_market_notice_error(exc)
        receipt.next_retry_at = now + timedelta(seconds=60)
        await db.commit()
        logger.warning(
            "Failed to publish market channel notice",
            extra={
                "event": "market.channel_notice_failed",
                "transition": transition,
                "dedupe_key": dedupe_key,
                "source": source,
                "error_class": type(exc).__name__,
            },
        )
        return MarketChannelNoticeResult(status="failed", dedupe_key=dedupe_key, reason=type(exc).__name__)

    if result is not None and result.ok:
        receipt.status = MARKET_NOTICE_STATUS_SENT
        receipt.sent_at = now
        receipt.next_retry_at = None
        receipt.last_error_class = None
        receipt.last_error = None
        receipt.telegram_message_id = result.message_id
        await db.commit()
        logger.info(
            "Published market channel notice",
            extra={
                "event": "market.channel_notice_sent",
                "transition": transition,
                "dedupe_key": dedupe_key,
                "source": source,
                "telegram_message_id": result.message_id,
            },
        )
        return MarketChannelNoticeResult(status="sent", dedupe_key=dedupe_key)

    error_class = result.error if result is not None and result.error else "telegram_send_failed"
    error_text = result.response_text if result is not None else None
    receipt.status = MARKET_NOTICE_STATUS_FAILED
    receipt.last_error_class = str(error_class)
    receipt.last_error = _compact_market_notice_error(error_text or error_class)
    receipt.next_retry_at = now + timedelta(seconds=60)
    await db.commit()
    logger.warning(
        "Failed to publish market channel notice",
        extra={
            "event": "market.channel_notice_failed",
            "transition": transition,
            "dedupe_key": dedupe_key,
            "source": source,
            "error_class": error_class,
            "status_code": getattr(result, "status_code", None) if result is not None else None,
        },
    )
    return MarketChannelNoticeResult(status="failed", dedupe_key=dedupe_key, reason=str(error_class))


def _market_runtime_state_from_notice_receipt(receipt: MarketChannelNoticeReceipt) -> MarketRuntimeState:
    return MarketRuntimeState(
        id=1,
        is_open=receipt.transition == MARKET_NOTICE_TRANSITION_OPENED,
        active_web_notice_visible=True,
        offers_since_last_open=0,
        last_transition_at=receipt.transition_at,
    )


async def reconcile_due_market_channel_notice_receipts(
    db: AsyncSession,
    *,
    source: str,
    current_time: datetime | None = None,
    limit: int | None = None,
) -> MarketChannelNoticeRetrySummary:
    if current_server() != SERVER_FOREIGN:
        return MarketChannelNoticeRetrySummary()
    if market_channel_notice_delivery_disabled():
        return MarketChannelNoticeRetrySummary(disabled=True)
    if _market_notice_queue_mode():
        return MarketChannelNoticeRetrySummary()

    now = _coerce_utc_now(current_time)
    max_rows = max(1, int(limit)) if limit is not None else _market_notice_retry_limit()
    result = await db.execute(
        select(MarketChannelNoticeReceipt)
        .where(
            MarketChannelNoticeReceipt.status == MARKET_NOTICE_STATUS_FAILED,
            MarketChannelNoticeReceipt.next_retry_at.isnot(None),
            MarketChannelNoticeReceipt.next_retry_at <= now,
        )
        .order_by(MarketChannelNoticeReceipt.next_retry_at.asc(), MarketChannelNoticeReceipt.id.asc())
        .limit(max_rows)
        .with_for_update(skip_locked=True)
    )
    receipts = list(result.scalars().all())
    summary = MarketChannelNoticeRetrySummary(checked=len(receipts))

    for receipt in receipts:
        retry_result = await reconcile_market_channel_notice_for_state(
            db,
            _market_runtime_state_from_notice_receipt(receipt),
            source=source,
            current_time=now,
        )
        if retry_result.status == "sent":
            summary.sent += 1
        elif retry_result.status == "failed":
            summary.failed += 1
        else:
            summary.skipped += 1

    return summary


async def reconcile_market_channel_notice_for_current_state(
    db: AsyncSession,
    *,
    source: str,
) -> MarketChannelNoticeResult:
    state = await get_market_runtime_state(db)
    if state is None:
        return MarketChannelNoticeResult(status="skipped", reason="missing_runtime_state")
    return await reconcile_market_channel_notice_for_state(db, state, source=source)


async def _expire_active_local_offers_for_market_close(
    db: AsyncSession,
    *,
    now: datetime,
) -> tuple[OfferExpiryResult, list[int]]:
    active_offers = await _load_active_local_offers(db)
    expired_user_ids: list[int] = []

    expiry_result = await expire_offers_authoritatively(
        db,
        active_offers,
        OfferExpiryCommand(
            reason=OfferExpiryReason.MARKET_CLOSED,
            source_surface=OfferExpirySourceSurface.SYSTEM,
            source_server=current_server(),
            expired_by_user_id=None,
            expired_by_actor_user_id=None,
        ),
        commit=False,
        now=now,
    )

    for offer in expiry_result.expired_offers:
        if offer.user_id:
            expired_user_ids.append(int(offer.user_id))

    return expiry_result, expired_user_ids


async def _apply_market_close_expiry_side_effects(
    expired_offers: tuple[Offer, ...],
    expired_user_ids: list[int],
) -> None:
    for offer in expired_offers:
        try:
            await apply_offer_channel_state(offer, reason="market_close_expire")
        except Exception as exc:
            logger.warning("Failed to apply channel state for market-close expiry %s: %s", offer.id, exc)

    if expired_user_ids:
        try:
            from core.cache import decr_active_offer_count

            for user_id in expired_user_ids:
                await decr_active_offer_count(user_id)
        except Exception as exc:
            logger.warning("Failed to update active-offer cache after market close: %s", exc)


async def reconcile_market_runtime_side_effects_for_state(
    db: AsyncSession,
    state: MarketRuntimeState | None,
    *,
    source: str,
) -> MarketTransitionResult:
    """Reconcile local side effects from the latest synced market runtime state."""
    if state is None:
        return MarketTransitionResult(changed=False, transition=None, state=None)

    expired_offer_ids: tuple[int, ...] = ()
    transition: str | None = None
    if current_server() == SERVER_FOREIGN and not state.is_open:
        await _acquire_market_runtime_lock(db)
        close_time = _market_notice_transition_at_for_state(state) or utc_now()
        expiry_result, expired_user_ids = await _expire_active_local_offers_for_market_close(
            db,
            now=close_time,
        )
        await db.commit()
        if expiry_result.expired_count:
            await _apply_market_close_expiry_side_effects(
                expiry_result.expired_offers,
                expired_user_ids,
            )
            expired_offer_ids = tuple(offer.id for offer in expiry_result.expired_offers)
            transition = "closed_local_offer_expiry"

    try:
        await reconcile_market_channel_notice_for_state(db, state, source=source)
    except Exception as exc:
        logger.warning("Failed to reconcile market channel notice from runtime side effects: %s", exc)

    return MarketTransitionResult(
        changed=bool(expired_offer_ids),
        transition=transition,
        state=state,
        expired_offer_ids=expired_offer_ids,
    )


async def reconcile_market_runtime_side_effects_for_current_state(
    db: AsyncSession,
    *,
    source: str,
) -> MarketTransitionResult:
    state = await get_market_runtime_state(db)
    return await reconcile_market_runtime_side_effects_for_state(db, state, source=source)


async def reconcile_foreign_market_schedule_autonomy(
    db: AsyncSession,
    evaluation: MarketScheduleEvaluation,
    *,
    current_time: datetime | None = None,
    source: str,
) -> MarketTransitionResult:
    """Run foreign-local Telegram market transition side effects after grace.

    This function intentionally does not write ``market_runtime_state``. Iran
    remains authoritative for product runtime state; foreign only protects the
    Telegram surface when the synced schedule is known and Iran's runtime-state
    transition has not arrived within the configured short-outage grace window.
    """
    if current_server() != SERVER_FOREIGN:
        return MarketTransitionResult(changed=False, transition=None, state=None)

    scheduled_transition_at = getattr(evaluation, "current_transition_at", None)
    if scheduled_transition_at is None:
        return MarketTransitionResult(changed=False, transition=None, state=None)

    transition_at = _coerce_utc_now(scheduled_transition_at)
    now = _coerce_utc_now(current_time)
    grace_seconds = _foreign_independent_grace_seconds()
    if now < transition_at + timedelta(seconds=grace_seconds):
        return MarketTransitionResult(changed=False, transition=None, state=None)

    synced_state = await get_market_runtime_state(db)
    synced_transition_at = getattr(synced_state, "last_transition_at", None) if synced_state is not None else None
    if synced_transition_at is not None:
        synced_transition_at = _coerce_utc_now(synced_transition_at)
        if synced_transition_at >= transition_at and bool(getattr(synced_state, "is_open", False)) == bool(evaluation.is_open):
            return MarketTransitionResult(changed=False, transition=None, state=synced_state)
        if synced_transition_at > transition_at and bool(getattr(synced_state, "is_open", False)) != bool(evaluation.is_open):
            logger.warning(
                "Skipping foreign market schedule autonomy because newer synced state contradicts schedule evaluation",
                extra={
                    "event": "market.foreign_schedule_autonomy_conflict",
                    "schedule_is_open": bool(evaluation.is_open),
                    "schedule_transition_at": transition_at.isoformat(),
                    "synced_is_open": bool(getattr(synced_state, "is_open", False)),
                    "synced_transition_at": synced_transition_at.isoformat(),
                },
            )
            return MarketTransitionResult(changed=False, transition=None, state=synced_state)

    local_state = MarketRuntimeState(
        id=1,
        is_open=bool(evaluation.is_open),
        active_web_notice_visible=True,
        offers_since_last_open=0,
        last_transition_at=transition_at,
    )
    return await reconcile_market_runtime_side_effects_for_state(
        db,
        local_state,
        source=source,
    )


async def get_market_runtime_state(db: AsyncSession) -> MarketRuntimeState | None:
    result = await db.execute(
        select(MarketRuntimeState)
        .order_by(MarketRuntimeState.id.asc())
        .limit(1)
    )
    return result.scalars().first()


async def get_or_create_market_runtime_state(
    db: AsyncSession,
    *,
    evaluation: MarketScheduleEvaluation,
    current_time: datetime | None = None,
) -> tuple[MarketRuntimeState, bool]:
    state = await get_market_runtime_state(db)
    if state is not None:
        return state, False

    state = _build_initial_market_runtime_state(evaluation, current_time=current_time)
    db.add(state)
    await db.commit()
    return state, True


async def load_market_schedule_overrides_window(
    db: AsyncSession,
    *,
    timezone_name: str,
    current_time: datetime | None = None,
    lookahead_days: int = NEXT_TRANSITION_SEARCH_DAYS,
) -> list[MarketScheduleOverride]:
    timezone_info = ZoneInfo(timezone_name)
    local_now = current_time.astimezone(timezone_info) if current_time and current_time.tzinfo else datetime.now(timezone_info)
    start_date = local_now.date()
    end_date = start_date + timedelta(days=lookahead_days)
    result = await db.execute(
        select(MarketScheduleOverride)
        .where(
            MarketScheduleOverride.date >= start_date,
            MarketScheduleOverride.date <= end_date,
        )
        .order_by(MarketScheduleOverride.date.asc(), MarketScheduleOverride.id.asc())
    )
    return list(result.scalars().all())


async def evaluate_current_market_schedule(
    db: AsyncSession,
    *,
    current_time: datetime | None = None,
) -> MarketScheduleEvaluation:
    trading_settings = await get_trading_settings_async()
    timezone_name = get_market_timezone_name(trading_settings)
    overrides = await load_market_schedule_overrides_window(
        db,
        timezone_name=timezone_name,
        current_time=current_time,
    )
    return evaluate_market_schedule(
        trading_settings,
        current_time=current_time,
        overrides=overrides,
    )


async def get_market_runtime_view(
    db: AsyncSession,
    *,
    current_time: datetime | None = None,
) -> MarketRuntimeView:
    if current_time is None:
        cached_view = _get_cached_market_runtime_view()
        if cached_view is not None:
            return cached_view

    evaluation = await evaluate_current_market_schedule(db, current_time=current_time)
    state = await get_market_runtime_state(db)
    view = MarketRuntimeView(
        is_open=evaluation.is_open,
        active_web_notice_visible=bool(getattr(state, "active_web_notice_visible", False)),
        offers_since_last_open=int(getattr(state, "offers_since_last_open", 0) or 0),
        last_transition_at=getattr(state, "last_transition_at", None),
        next_transition_at=evaluation.next_transition_at,
    )
    if current_time is None:
        _set_cached_market_runtime_view(view)
    return view


async def register_market_offer_created(
    db: AsyncSession,
    *,
    current_time: datetime | None = None,
) -> MarketRuntimeState:
    await _acquire_market_runtime_lock(db)
    evaluation = await evaluate_current_market_schedule(db, current_time=current_time)
    state = await get_market_runtime_state(db)
    if state is None:
        if not evaluation.is_open:
            await db.rollback()
            return _build_initial_market_runtime_state(
                evaluation,
                current_time=current_time,
            )
        state = _build_initial_market_runtime_state(evaluation, current_time=current_time)
        db.add(state)
    elif not evaluation.is_open or not state.is_open:
        await db.rollback()
        return state

    state.offers_since_last_open = int(state.offers_since_last_open or 0) + 1
    should_hide_notice = bool(
        state.active_web_notice_visible and state.offers_since_last_open >= 2
    )
    if should_hide_notice:
        state.active_web_notice_visible = False
    await db.commit()
    invalidate_market_runtime_view_cache()

    if should_hide_notice:
        try:
            publish_event_sync(
                "market:notice_hidden",
                _build_market_event_payload(
                    state,
                    transition="notice_hidden",
                    notice_text=None,
                ),
            )
        except Exception as exc:
            logger.warning("Failed to publish market:notice_hidden event: %s", exc)

    return state


async def _load_active_local_offers(db: AsyncSession) -> list[Offer]:
    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(
            Offer.status == OfferStatus.ACTIVE,
            Offer.home_server == current_server(),
        )
        .order_by(Offer.id.asc())
    )
    return list(result.scalars().all())


async def _apply_market_open_transition(
    db: AsyncSession,
    state: MarketRuntimeState,
    *,
    current_time: datetime | None = None,
) -> MarketTransitionResult:
    now = _coerce_utc_now(current_time)
    state.is_open = True
    state.active_web_notice_visible = True
    state.offers_since_last_open = 0
    state.last_transition_at = now
    await db.commit()
    invalidate_market_runtime_view_cache()

    notice_text = MARKET_OPENED_CHANNEL_NOTICE
    try:
        await reconcile_market_channel_notice_for_state(db, state, source="local_transition")
    except Exception as exc:
        logger.warning("Failed to publish market-open channel notice: %s", exc)

    try:
        publish_event_sync("market:opened", _build_market_event_payload(state, transition="opened", notice_text=notice_text))
    except Exception as exc:
        logger.warning("Failed to publish market:opened event: %s", exc)

    return MarketTransitionResult(changed=True, transition="opened", state=state)


async def _apply_market_closed_transition(
    db: AsyncSession,
    state: MarketRuntimeState,
    *,
    current_time: datetime | None = None,
) -> MarketTransitionResult:
    now = _coerce_utc_now(current_time)
    expiry_result, expired_user_ids = await _expire_active_local_offers_for_market_close(
        db,
        now=now,
    )
    expired_offer_ids = [offer.id for offer in expiry_result.expired_offers]

    state.is_open = False
    state.active_web_notice_visible = True
    state.offers_since_last_open = 0
    state.last_transition_at = now
    await db.commit()
    invalidate_market_runtime_view_cache()

    await _apply_market_close_expiry_side_effects(
        expiry_result.expired_offers,
        expired_user_ids,
    )

    notice_text = MARKET_CLOSED_CHANNEL_NOTICE
    try:
        await reconcile_market_channel_notice_for_state(db, state, source="local_transition")
    except Exception as exc:
        logger.warning("Failed to publish market-close channel notice: %s", exc)

    try:
        publish_event_sync("market:closed", _build_market_event_payload(state, transition="closed", notice_text=notice_text))
    except Exception as exc:
        logger.warning("Failed to publish market:closed event: %s", exc)

    return MarketTransitionResult(
        changed=True,
        transition="closed",
        state=state,
        expired_offer_ids=tuple(expired_offer_ids),
    )


async def apply_market_schedule_transition(
    db: AsyncSession,
    evaluation: MarketScheduleEvaluation,
    *,
    current_time: datetime | None = None,
) -> MarketTransitionResult:
    if current_server() == SERVER_FOREIGN:
        return await reconcile_market_runtime_side_effects_for_current_state(
            db,
            source="foreign_schedule_guard",
        )

    await _acquire_market_runtime_lock(db)
    state = await get_market_runtime_state(db)
    if state is None:
        state = _build_initial_market_runtime_state(evaluation, current_time=current_time)
        db.add(state)
        await db.commit()
        invalidate_market_runtime_view_cache()
        return MarketTransitionResult(changed=False, transition=None, state=state)

    if state.is_open == evaluation.is_open:
        return MarketTransitionResult(changed=False, transition=None, state=state)

    if evaluation.is_open:
        return await _apply_market_open_transition(db, state, current_time=current_time)

    return await _apply_market_closed_transition(db, state, current_time=current_time)
