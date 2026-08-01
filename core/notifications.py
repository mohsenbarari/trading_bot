# core/notifications.py
"""Notification delivery boundary.

The historical Iran-to-FI notification relay is retired: it has neither the
private Object-Storage transport nor the witnessed authority required by the
three-site architecture.  Foreign-local Telegram delivery remains a separate,
authorized effect.
"""
import logging
from core.config import settings
from core.legacy_direct_fi_ir_transport_fence import (
    assert_legacy_direct_fi_ir_transport_retired,
)
from core.sync_push import push_sync_direct
from core import telegram_gateway
from core.external_effect_execution_gate import EXTERNAL_EFFECT_SCOPE_TELEGRAM_DIRECT_NOTIFICATION_EFFECT
from core.utils import utc_now_naive

logger = logging.getLogger(__name__)

async def send_telegram_message(chat_id: int, text: str, parse_mode: str = "Markdown"):
    """
    Send Telegram message independent of server location.
    - Iran: reject the retired direct FI relay before creating its payload.
    - Foreign: Send directly via Bot API.
    """
    if settings.server_mode == "iran":
        assert_legacy_direct_fi_ir_transport_retired(
            component="notifications",
            operation="Iran direct FI notification relay",
        )
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
        from core.db import require_external_effect_execution_authorization

        require_external_effect_execution_authorization(
            EXTERNAL_EFFECT_SCOPE_TELEGRAM_DIRECT_NOTIFICATION_EFFECT
        )
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
