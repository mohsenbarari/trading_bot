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
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from core.db import AsyncSessionLocal
from core.config import settings
from models.offer import Offer, OfferStatus

logger = logging.getLogger(__name__)

# How often to check for expired offers (seconds)
CHECK_INTERVAL = 15


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
    
    cutoff_time = datetime.utcnow() - timedelta(minutes=expiry_minutes)
    
    async with AsyncSessionLocal() as session:
        # Find active offers older than cutoff
        stmt = (
            select(Offer)
            .where(
                Offer.status == OfferStatus.ACTIVE,
                Offer.created_at < cutoff_time
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
            .where(Offer.id.in_(offer_ids))
            .values(status=OfferStatus.EXPIRED)
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


async def offer_expiry_loop() -> None:
    """
    Background loop that periodically checks and expires stale offers.
    
    Should be started as an asyncio task in the app lifespan.
    """
    logger.info(f"⏰ Offer expiry loop started (check every {CHECK_INTERVAL}s)")
    
    while True:
        try:
            count = await expire_stale_offers()
            if count > 0:
                logger.info(f"⏰ Expiry cycle: {count} offers expired")
        except Exception as e:
            logger.error(f"❌ Error in offer expiry loop: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)
