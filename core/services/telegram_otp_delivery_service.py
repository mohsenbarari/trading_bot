"""Foreign-only Telegram OTP delivery with a short-lived Redis dedupe receipt."""

from __future__ import annotations

from datetime import timezone
import hashlib
import json

from core import telegram_gateway
from core.registration_contracts import (
    TelegramOTPDeliveryCommand,
    TelegramOTPDeliveryOutcome,
    TelegramOTPDeliveryResponse,
)
from core.server_routing import SERVER_FOREIGN, current_server
from core.utils import utc_now


def _receipt_key(command: TelegramOTPDeliveryCommand) -> str:
    return f"telegram_otp_delivery:receipt:{command.otp_request_id}"


def _command_hash(command: TelegramOTPDeliveryCommand) -> str:
    body = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


async def deliver_telegram_otp_once(
    redis,
    *,
    command: TelegramOTPDeliveryCommand,
) -> TelegramOTPDeliveryResponse:
    if current_server() != SERVER_FOREIGN:
        raise RuntimeError("telegram_otp_delivery_requires_foreign")

    now = utc_now()
    expires_at = command.expires_at.astimezone(timezone.utc)
    if expires_at <= now:
        return TelegramOTPDeliveryResponse(
            otp_request_id=command.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.INVALID,
        )
    receipt_ttl = max(1, min(300, int((expires_at - now).total_seconds()) + 60))
    receipt_key = _receipt_key(command)
    command_hash = _command_hash(command)
    claimed = await redis.set(
        receipt_key,
        f"processing:{command_hash}",
        ex=receipt_ttl,
        nx=True,
    )
    if not claimed:
        existing = _text(await redis.get(receipt_key))
        existing_status, _, existing_hash = (existing or "").partition(":")
        return TelegramOTPDeliveryResponse(
            otp_request_id=command.otp_request_id,
            outcome=(
                TelegramOTPDeliveryOutcome.DUPLICATE_SENT
                if existing_status == TelegramOTPDeliveryOutcome.SENT.value
                and existing_hash == command_hash
                else (
                    TelegramOTPDeliveryOutcome.PROVIDER_ERROR
                    if existing_hash == command_hash
                    else TelegramOTPDeliveryOutcome.INVALID
                )
            ),
        )

    text = f"🔐 کد ورود شما: `{command.otp_code}`\n\nاین کد تا ۲ دقیقه معتبر است."
    try:
        result = await telegram_gateway.send_message(
            command.telegram_id,
            text,
            parse_mode="Markdown",
            timeout=5,
            idempotency_key=f"web-login-otp:{command.otp_request_id}",
        )
    except Exception:
        outcome = TelegramOTPDeliveryOutcome.PROVIDER_ERROR
    else:
        if result.ok:
            outcome = TelegramOTPDeliveryOutcome.SENT
        elif result.status_code == 429:
            outcome = TelegramOTPDeliveryOutcome.RATE_LIMITED
        elif result.status_code in {400, 403}:
            outcome = TelegramOTPDeliveryOutcome.UNREACHABLE
        else:
            outcome = TelegramOTPDeliveryOutcome.PROVIDER_ERROR
    await redis.set(receipt_key, f"{outcome.value}:{command_hash}", ex=receipt_ttl)
    return TelegramOTPDeliveryResponse(
        otp_request_id=command.otp_request_id,
        outcome=outcome,
    )
