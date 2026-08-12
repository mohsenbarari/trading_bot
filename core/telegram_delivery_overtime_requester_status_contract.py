"""Opaque cancel callback + markup for bot overtime requester status."""
from __future__ import annotations

from typing import Any

from core.offer_request_identity import is_offer_request_public_id_shape
from core.offer_overtime_bot_copy import M12_REQUESTER_CANCEL_BUTTON


OVERTIME_REQUESTER_CANCEL_CALLBACK_PREFIX = "otc"


def _normalized_public_id(value: Any) -> str | None:
    if value is None:
        return None
    public_id = str(value).strip()
    if not public_id or not is_offer_request_public_id_shape(public_id):
        return None
    return public_id


def build_overtime_requester_cancel_callback_data(*, request_public_id: Any) -> str:
    public_id = _normalized_public_id(request_public_id)
    if public_id is None:
        raise ValueError("overtime_requester_cancel_request_public_id_invalid")
    return f"{OVERTIME_REQUESTER_CANCEL_CALLBACK_PREFIX}:{public_id}:cancel"


def parse_overtime_requester_cancel_callback_data(callback_data: Any) -> str | None:
    raw = str(callback_data or "").strip()
    parts = raw.split(":")
    if (
        len(parts) != 3
        or parts[0] != OVERTIME_REQUESTER_CANCEL_CALLBACK_PREFIX
        or parts[2].strip().lower() != "cancel"
    ):
        return None
    return _normalized_public_id(parts[1])


def build_overtime_requester_cancel_reply_markup(*, request_public_id: Any) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": M12_REQUESTER_CANCEL_BUTTON,
                    "callback_data": build_overtime_requester_cancel_callback_data(
                        request_public_id=request_public_id,
                    ),
                }
            ]
        ]
    }
