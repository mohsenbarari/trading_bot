"""Strict mixed-version contract for the legacy login OTP sync relay."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


LEGACY_TELEGRAM_OTP_RELAY_PURPOSE = "legacy_login_otp"
LEGACY_TELEGRAM_OTP_PARSE_MODE = "Markdown"
_OTP_TEXT_PATTERN = re.compile(
    r"\A🔐 کد ورود شما: `(?P<otp>[0-9]{5})`\n\n"
    r"این کد تا ۲ دقیقه معتبر است\.\Z"
)


@dataclass(frozen=True, slots=True)
class LegacyTelegramOTPRelay:
    chat_id: int
    text: str
    parse_mode: str
    purpose: str


def validate_legacy_telegram_otp_relay(
    *,
    chat_id: Any,
    text: Any,
    parse_mode: Any,
    purpose: Any = None,
) -> LegacyTelegramOTPRelay:
    """Accept only the exact OTP envelope, including old purpose-less senders."""
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0:
        raise ValueError("legacy_telegram_otp_chat_id_invalid")
    normalized_purpose = str(purpose or "").strip()
    if normalized_purpose not in {"", LEGACY_TELEGRAM_OTP_RELAY_PURPOSE}:
        raise ValueError("legacy_telegram_otp_purpose_invalid")
    normalized_parse_mode = str(parse_mode or "").strip()
    if normalized_parse_mode != LEGACY_TELEGRAM_OTP_PARSE_MODE:
        raise ValueError("legacy_telegram_otp_parse_mode_invalid")
    normalized_text = str(text or "")
    if _OTP_TEXT_PATTERN.fullmatch(normalized_text) is None:
        raise ValueError("legacy_telegram_otp_text_invalid")
    return LegacyTelegramOTPRelay(
        chat_id=chat_id,
        text=normalized_text,
        parse_mode=normalized_parse_mode,
        purpose=LEGACY_TELEGRAM_OTP_RELAY_PURPOSE,
    )
