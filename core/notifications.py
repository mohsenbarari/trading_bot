# core/notifications.py
"""
Notification system that handles cross-server delivery.
If on Iran server, relays notifications to Foreign server via sync mechanism.
If on Foreign server, sends directly via Telegram Bot.
"""
import logging
from core.config import settings
from core.sync_push import push_sync_direct
from core import telegram_gateway
from core.utils import utc_now_naive

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
            "timestamp": utc_now_naive().timestamp()
        }
        
        # Direct Push only (no Redis backup for notifications to avoid double-send)
        # Notifications are ephemeral - if delivery fails, user can retry
        try:
            push_sync_direct(payload)
        except Exception as e:
            logger.warning(f"⚡ Direct push notification failed: {e}")
            
    else:
        # We are on Foreign server (or standalone) - Send directly
        logger.info(f"🌍 Sending Telegram message directly to {chat_id}")
        result = await telegram_gateway.send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            idempotency_key=f"notification:{chat_id}",
        )
        if not result.ok:
            message = f"Telegram gateway failed for sendMessage: {result.error or result.status_code}"
            logger.error("❌ Failed to send Telegram message: %s", message)
            raise RuntimeError(message)
