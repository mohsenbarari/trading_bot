"""Strict source contract for the private Offer success preview edit."""
from __future__ import annotations

from typing import Any


TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS = "offer_success_preview"
OFFER_SUCCESS_TEMPLATE_VERSION = "offer-success-preview-v1"

OFFER_SUCCESS_COPY_LEGACY = "✅ لفظ شما با موفقیت در کانال ارسال شد!"
OFFER_SUCCESS_COPY_TEXT = "✅ لفظ شما با موفقیت در کانال منتشر شد!"
OFFER_SUCCESS_ALLOWED_COPIES = frozenset(
    {
        OFFER_SUCCESS_COPY_LEGACY,
        OFFER_SUCCESS_COPY_TEXT,
    }
)


def validate_offer_success_copy(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized not in OFFER_SUCCESS_ALLOWED_COPIES:
        raise ValueError("offer_success_copy_invalid")
    return normalized


def build_offer_success_text(*, success_copy: Any, offer_text: Any) -> str:
    copy = validate_offer_success_copy(success_copy)
    normalized_offer_text = str(offer_text or "").strip()
    if not normalized_offer_text:
        raise ValueError("offer_success_offer_text_invalid")
    return f"{copy}\n\n**لفظ شما:**\n\n{normalized_offer_text}"


def build_offer_success_reply_markup(offer_id: Any) -> dict[str, Any]:
    if isinstance(offer_id, bool) or not isinstance(offer_id, int) or offer_id <= 0:
        raise ValueError("offer_success_offer_id_invalid")
    return {
        "inline_keyboard": [
            [
                {
                    "text": "❌ منقضی کردن",
                    "callback_data": f"expire_offer:{offer_id}",
                }
            ]
        ]
    }
