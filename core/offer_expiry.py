# core/offer_expiry.py
"""
Background task for auto-expiring offers based on trading settings.

This task runs periodically and expires any ACTIVE offers that have 
exceeded their time limit (offer_expiry_minutes).

It also applies the terminal channel message state so users can no longer
interact with expired offers and see the right history marker.
"""
import asyncio
import logging
import time
from datetime import timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from core.db import AsyncSessionLocal
from core.config import settings
from core.background_job_authority import JOB_OFFER_EXPIRY, assert_background_job_authority
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core import telegram_gateway
from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    expire_offers_authoritatively,
)
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from core.utils import utc_now_naive
from models.offer import Offer, OfferStatus

logger = logging.getLogger(__name__)

# Maximum idle sleep when there is no near expiry deadline.
CHECK_INTERVAL = 2.0
MIN_DEADLINE_SLEEP_SECONDS = 0.1
STALE_EXPIRY_RETRY_ATTEMPTS = 1
_loop_errors = RepeatedErrorLogger(every=10)
REMOTE_CHANNEL_EXPIRY_PRESENTATION_TTL_SECONDS = 60 * 60
REMOTE_CHANNEL_EXPIRY_PRESENTATION_MAX_KEYS = 5000
_remote_channel_expiry_presented_at: dict[int, float] = {}


def _remember_remote_channel_expiry_result(offer_id: int, applied: bool) -> None:
    if not applied:
        return
    now = time.monotonic()
    _remote_channel_expiry_presented_at[offer_id] = now
    if len(_remote_channel_expiry_presented_at) <= REMOTE_CHANNEL_EXPIRY_PRESENTATION_MAX_KEYS:
        return
    cutoff = now - REMOTE_CHANNEL_EXPIRY_PRESENTATION_TTL_SECONDS
    for key, applied_at in list(_remote_channel_expiry_presented_at.items()):
        if applied_at < cutoff or len(_remote_channel_expiry_presented_at) > REMOTE_CHANNEL_EXPIRY_PRESENTATION_MAX_KEYS:
            _remote_channel_expiry_presented_at.pop(key, None)


def _remote_channel_expiry_recently_presented(offer_id: int) -> bool:
    applied_at = _remote_channel_expiry_presented_at.get(offer_id)
    if applied_at is None:
        return False
    if time.monotonic() - applied_at <= REMOTE_CHANNEL_EXPIRY_PRESENTATION_TTL_SECONDS:
        return True
    _remote_channel_expiry_presented_at.pop(offer_id, None)
    return False


async def remove_channel_buttons(channel_message_id: int) -> None:
    """Remove inline keyboard from a channel message via Telegram API."""
    if current_server() != "foreign":
        return

    channel_id = settings.channel_id
    
    if not channel_id:
        return
    
    try:
        result = await telegram_gateway.edit_message_reply_markup(
            channel_id,
            channel_message_id,
            idempotency_key=f"offer-expiry-remove-buttons:{channel_message_id}",
        )
        if not result.ok:
            logger.debug(
                "Failed to remove channel buttons for msg %s: %s",
                channel_message_id,
                result.error or result.status_code,
            )
    except Exception as e:
        logger.debug(f"Failed to remove channel buttons for msg {channel_message_id}: {e}")


async def expire_stale_offers() -> int:
    """
    Find and expire all offers that have exceeded their time limit.
    
    Returns the number of offers expired.
    """
    assert_background_job_authority(JOB_OFFER_EXPIRY)
    from core.trading_settings import get_trading_settings_async
    
    ts = await get_trading_settings_async()
    expiry_minutes = ts.offer_expiry_minutes
    
    if expiry_minutes <= 0:
        return 0
    
    now = utc_now_naive()
    cutoff_time = now - timedelta(minutes=expiry_minutes)
    
    async with AsyncSessionLocal() as session:
        expiry_result = None
        for attempt in range(STALE_EXPIRY_RETRY_ATTEMPTS + 1):
            expired_offers = await _load_local_stale_active_offers(session, cutoff_time)

            if not expired_offers:
                await apply_remote_stale_channel_state(session, cutoff_time)
                return 0

            try:
                expiry_result = await expire_offers_authoritatively(
                    session,
                    expired_offers,
                    OfferExpiryCommand(
                        reason=OfferExpiryReason.TIME_LIMIT,
                        source_surface=OfferExpirySourceSurface.SYSTEM,
                        source_server=current_server(),
                        expired_by_user_id=None,
                        expired_by_actor_user_id=None,
                    ),
                    now=now,
                )
                break
            except StaleDataError:
                await session.rollback()
                if attempt >= STALE_EXPIRY_RETRY_ATTEMPTS:
                    raise
                logger.info(
                    "Offer expiry hit a stale row conflict; retrying active offer scan",
                    extra={"event": "offer_expiry.stale_retry", "attempt": attempt + 1},
                )

        if expiry_result is None:
            await apply_remote_stale_channel_state(session, cutoff_time)
            return 0

        count = expiry_result.expired_count
        offer_ids = [o.id for o in expiry_result.expired_offers]
        
        logger.info(f"⏰ Auto-expired {count} offers: {offer_ids}")
        
        # Apply terminal channel state on foreign and remove interactive buttons.
        for offer in expiry_result.expired_offers:
            await apply_offer_channel_state(offer, reason="auto_expire_time_limit")
        
        # Publish realtime events for each expired offer
        try:
            from core.events import publish_event_sync
            for offer_id in offer_ids:
                publish_event_sync("offer:expired", {"id": offer_id})
        except Exception as e:
            logger.warning(f"Failed to publish expire events: {e}")
        
        # Update Redis cache for affected users
        try:
            from core.cache import decr_active_offer_count
            user_ids = set(o.user_id for o in expiry_result.expired_offers if o.user_id)
            for uid in user_ids:
                user_expired_count = sum(1 for o in expiry_result.expired_offers if o.user_id == uid)
                for _ in range(user_expired_count):
                    await decr_active_offer_count(uid)
        except Exception as e:
            logger.debug(f"Failed to update offer count cache: {e}")

        await apply_remote_stale_channel_state(session, cutoff_time)
    
    return count


async def _load_local_stale_active_offers(session, cutoff_time):
    stmt = (
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(
            Offer.status == OfferStatus.ACTIVE,
            Offer.home_server == current_server(),
            Offer.created_at <= cutoff_time,
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def apply_remote_stale_channel_state(session, cutoff_time) -> int:
    """
    Presentation-only Telegram convergence for remote-home offers on foreign.

    Foreign must not authoritatively expire Iran-owned offers. It may still
    remove channel interaction and show the expired marker once the same
    time-limit has elapsed, while the real row state converges through sync.
    """
    if current_server() != "foreign":
        return 0

    stmt = (
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(
            Offer.status == OfferStatus.ACTIVE,
            Offer.home_server.isnot(None),
            Offer.home_server != current_server(),
            Offer.channel_message_id.isnot(None),
            Offer.created_at <= cutoff_time,
        )
        .limit(100)
    )
    result = await session.execute(stmt)
    remote_stale_offers = result.scalars().all()
    applied_count = 0
    for offer in remote_stale_offers:
        offer_id = int(getattr(offer, "id", 0) or 0)
        if not offer_id or _remote_channel_expiry_recently_presented(offer_id):
            continue
        presentation_offer = SimpleNamespace(
            id=offer.id,
            offer_type=offer.offer_type,
            commodity=getattr(offer, "commodity", None),
            quantity=offer.quantity,
            remaining_quantity=offer.remaining_quantity,
            price=offer.price,
            is_wholesale=offer.is_wholesale,
            lot_sizes=offer.lot_sizes,
            notes=offer.notes,
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            channel_message_id=offer.channel_message_id,
        )
        applied = await apply_offer_channel_state(
            presentation_offer,
            reason="remote_auto_expire_time_limit_presentation",
        )
        _remember_remote_channel_expiry_result(offer_id, applied)
        if applied:
            applied_count += 1
    if applied_count:
        logger.info("⏰ Applied remote-home channel expiry presentation to %s offers", applied_count)
    return applied_count


def _as_naive_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


async def get_next_expiry_delay_seconds() -> float:
    """Return a low-cost deadline-aware sleep interval for the expiry loop."""
    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()
    expiry_minutes = ts.offer_expiry_minutes
    if expiry_minutes <= 0:
        return CHECK_INTERVAL

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.min(Offer.created_at)).where(
                Offer.status == OfferStatus.ACTIVE,
                Offer.home_server == current_server(),
            )
        )
        next_created_at = result.scalar_one_or_none()

    if next_created_at is None:
        return CHECK_INTERVAL

    now = utc_now_naive()
    next_created_at = _as_naive_utc(next_created_at)
    next_expiry_at = next_created_at + timedelta(minutes=expiry_minutes)
    delay = (next_expiry_at - now).total_seconds()
    if delay <= MIN_DEADLINE_SLEEP_SECONDS:
        return MIN_DEADLINE_SLEEP_SECONDS
    return min(delay, CHECK_INTERVAL)


async def offer_expiry_loop() -> None:
    """
    Background loop that periodically checks and expires stale offers.
    
    Should be started as an asyncio task in the app lifespan.
    """
    logger.info(f"⏰ Offer expiry loop started (deadline-aware, max sleep {CHECK_INTERVAL}s)")
    iteration = 0
    
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context("offer_expiry", iteration=iteration) as run_id:
            try:
                count = await expire_stale_offers()
                duration_ms = duration_ms_since(start_time)
                if count > 0:
                    logger.info(f"⏰ Expiry cycle: {count} offers expired")
                    logger.info(
                        "Offer expiry cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": "offer_expiry",
                            "run_id": run_id,
                            "iteration": iteration,
                            "expired_count": count,
                            "duration_ms": duration_ms,
                        },
                    )
            except Exception as e:
                _loop_errors.log(logger, "❌ Error in offer expiry loop: %s", e, job_name="offer_expiry", run_id=run_id)

        try:
            sleep_seconds = await get_next_expiry_delay_seconds()
        except Exception as e:
            _loop_errors.log(logger, "❌ Error computing offer expiry deadline: %s", e, job_name="offer_expiry", run_id=run_id)
            sleep_seconds = CHECK_INTERVAL

        await asyncio.sleep(sleep_seconds)
