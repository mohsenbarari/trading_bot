# core/notifications.py
"""Notification delivery boundary.

The historical Iran-to-FI notification relay is retired: it has neither the
private Object-Storage transport nor the witnessed authority required by the
three-site architecture. Foreign-local Telegram delivery remains separate.
"""
import logging
from core.config import settings
from core.legacy_direct_fi_ir_transport_fence import assert_legacy_direct_fi_ir_transport_retired
# Kept as a module attribute solely for legacy test/import compatibility. It
# is never invoked: the Iran branch fences before any direct relay.
from core.sync_push import push_sync_direct
from core import telegram_gateway

logger = logging.getLogger(__name__)

async def send_telegram_message(chat_id: int, text: str, parse_mode: str = "Markdown"):
    """
    Send Telegram message independent of server location.
    - Iran: Never relay over a direct FI<->IR peer path.
    - Foreign: Send directly via Bot API.
    """
    if settings.server_mode == "iran":
        assert_legacy_direct_fi_ir_transport_retired(
            component="notifications",
            operation="direct IR-to-FI notification relay",
        )
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
