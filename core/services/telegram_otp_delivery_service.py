"""Foreign API enqueue path for one Telegram login OTP command."""

from __future__ import annotations

from core.registration_contracts import TelegramOTPDeliveryCommand, TelegramOTPDeliveryResponse
from core.services.telegram_otp_ephemeral_queue import (
    enqueue_telegram_otp_and_wait,
)


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
    """Enqueue an encrypted OTP command and wait for the bot receipt.

    This function must never call Telegram. The foreign bot worker is the
    only executor.
    """

    return await enqueue_telegram_otp_and_wait(redis, command=command)
