# core/notifications.py
"""
Notification system that handles cross-server delivery.
If on Iran server, relays notifications to Foreign server via sync mechanism.
If on Foreign server, sends directly via Telegram Bot.
"""
import logging
import json
from aiogram import Bot
from core.config import settings
from core.events import _get_sync_redis
from core.sync_push import push_sync_direct
from datetime import datetime

logger = logging.getLogger(__name__)

async def send_telegram_message(chat_id: int, text: str, parse_mode: str = "Markdown"):
    """
    Send Telegram message independent of server location.
    - Iran: Push to sync queue -> Foreign server sends it.
    - Foreign: Send directly via Bot API.
    """
    if settings.server_mode == "iran":
        logger.info(f"🇮🇷 Relaying notification to Foreign server for {chat_id}")
        
        payload = {
            "type": "notification",
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "timestamp": datetime.utcnow().timestamp()
        }
        
        # 1. Push to Redis (backup)
        try:
            r = _get_sync_redis()
            r.lpush("sync:outbound", json.dumps(payload, default=str))
        except Exception as e:
            logger.error(f"❌ Failed to push notification to Redis: {e}")
            
        # 2. Direct Push (fast path)
        try:
            push_sync_direct(payload)
        except Exception as e:
            logger.warning(f"⚡ Direct push notification failed: {e}")
            
    else:
        # We are on Foreign server (or standalone) - Send directly
        logger.info(f"🌍 Sending Telegram message directly to {chat_id}")
        try:
            async with Bot(token=settings.bot_token) as bot:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram message: {e}")
            # If valid token but network error, might raise. 
            # If invalid token, will raise.
            raise e
