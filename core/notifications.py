# core/notifications.py
"""Mixed-version legacy login-OTP relay; general notifications are rejected."""
import logging
from core.config import settings
from core.sync_push import push_sync_direct
from core import telegram_gateway
from core.telegram_legacy_otp_relay_contract import (
    LEGACY_TELEGRAM_OTP_RELAY_PURPOSE,
    validate_legacy_telegram_otp_relay,
)
from core.runtime_identity import resolve_runtime_identity
from core.utils import utc_now_naive

logger = logging.getLogger(__name__)

async def send_telegram_message(
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
    *,
    purpose: str | None = LEGACY_TELEGRAM_OTP_RELAY_PURPOSE,
):
    """Relay only the exact legacy login-OTP envelope to the foreign bot."""
    if bool(getattr(settings, "three_site_dr_enabled", False)) and bool(
        getattr(settings, "dr_event_protocol_strict", False)
    ) and resolve_runtime_identity(settings).is_webapp_authority:
        # A WebApp authority may not call or relay to the Telegram provider
        # without a durable, epoch-bound effect intent.  Existing callers can
        # fall back to their durable SMS path; Bot-FI remains unaffected.
        raise RuntimeError("strict DR forbids direct WebApp Telegram effects")

    relay = validate_legacy_telegram_otp_relay(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        purpose=purpose,
    )
    if settings.server_mode == "iran":
        logger.info("Relaying legacy Telegram OTP to Foreign server")
        
        payload = {
            "type": "notification",
            "purpose": relay.purpose,
            "chat_id": relay.chat_id,
            "text": relay.text,
            "parse_mode": relay.parse_mode,
            "timestamp": utc_now_naive().timestamp()
        }
        
        # Direct Push only; the OTP itself remains short-lived and outside the
        # durable shared queue by explicit product decision.
        try:
            push_sync_direct(payload)
        except Exception as e:
            logger.warning(f"⚡ Direct push notification failed: {e}")
            
    else:
        # We are on Foreign server (or standalone) - Send directly
        logger.info("Sending legacy Telegram OTP directly on Foreign server")
        result = await telegram_gateway.send_message(
            relay.chat_id,
            relay.text,
            parse_mode=relay.parse_mode,
            idempotency_key=f"legacy-login-otp:{relay.chat_id}",
        )
        if not result.ok:
            message = f"Telegram gateway failed for sendMessage: {result.error or result.status_code}"
            logger.error("❌ Failed to send Telegram message: %s", message)
            raise RuntimeError(message)
