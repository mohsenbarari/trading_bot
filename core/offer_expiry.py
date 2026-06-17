# core/offer_expiry.py
"""
Background task for auto-expiring offers based on trading settings.

This task runs periodically and expires any ACTIVE offers that have 
exceeded their time limit (offer_expiry_minutes).

It also removes the inline keyboard from the channel message so 
users can no longer interact with expired offers.
"""
import asyncio
import logging
import os
import time
from datetime import timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from core.db import AsyncSessionLocal
from core.config import settings
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core.utils import utc_now_naive
from models.offer import Offer, OfferStatus

logger = logging.getLogger(__name__)

# Maximum idle sleep when there is no near expiry deadline.
CHECK_INTERVAL = 2.0
MIN_DEADLINE_SLEEP_SECONDS = 0.1
_loop_errors = RepeatedErrorLogger(every=10)


async def remove_channel_buttons(channel_message_id: int) -> None:
    """Remove inline keyboard from a channel message via Telegram API."""
    bot_token = settings.bot_token or os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id:
        return
    
    try:
        import httpx
        url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
        payload = {
            "chat_id": channel_id,
            "message_id": channel_message_id
        }
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.debug(f"Failed to remove channel buttons for msg {channel_message_id}: {e}")


async def expire_stale_offers() -> int:
    """
    Find and expire all offers that have exceeded their time limit.
    
    Returns the number of offers expired.
    """
    from core.trading_settings import get_trading_settings_async
    
    ts = await get_trading_settings_async()
    expiry_minutes = ts.offer_expiry_minutes
    
    if expiry_minutes <= 0:
        return 0
    
    now = utc_now_naive()
    cutoff_time = now - timedelta(minutes=expiry_minutes)
    
    async with AsyncSessionLocal() as session:
        # Find active offers older than cutoff
        stmt = (
            select(Offer)
            .where(
                Offer.status == OfferStatus.ACTIVE,
                Offer.home_server == current_server(),
                Offer.created_at <= cutoff_time
            )
        )
        result = await session.execute(stmt)
        expired_offers = result.scalars().all()
        
        if not expired_offers:
            return 0
        
        count = len(expired_offers)
        offer_ids = [o.id for o in expired_offers]
        channel_msg_ids = [o.channel_message_id for o in expired_offers if o.channel_message_id]
        
        # Bulk update status to EXPIRED
        await session.execute(
            update(Offer)
            .where(Offer.id.in_(offer_ids), Offer.home_server == current_server())
            .values(status=OfferStatus.EXPIRED, expire_reason="time_limit", expired_at=now)
        )
        await session.commit()
        
        logger.info(f"⏰ Auto-expired {count} offers: {offer_ids}")
        
        # Remove channel buttons for expired offers
        for msg_id in channel_msg_ids:
            await remove_channel_buttons(msg_id)
        
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
            user_ids = set(o.user_id for o in expired_offers if o.user_id)
            for uid in user_ids:
                user_expired_count = sum(1 for o in expired_offers if o.user_id == uid)
                for _ in range(user_expired_count):
                    await decr_active_offer_count(uid)
        except Exception as e:
            logger.debug(f"Failed to update offer count cache: {e}")
    
    return count


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
