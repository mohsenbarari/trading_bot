"""Pure contract for private overtime owner-approval delivery jobs.

Stage 8 owns durable queue identity, opaque callback payloads, and approved
copy composition. Bot click handlers that interpret those callbacks land in
Stage 9.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from core.offer_lifecycle import (
    compute_lifecycle_deadlines,
    read_overtime_minutes_snapshot,
)
from core.offer_request_identity import is_offer_request_public_id_shape
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramFeederKind,
)


OVERTIME_OWNER_APPROVAL_FRESHNESS_ACTIONS = frozenset(
    {TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL}
)
OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION = "overtime-owner-approval-v1"
OVERTIME_OWNER_APPROVAL_CALLBACK_PREFIX = "ota"

# Approved inventory (M23–M28). Exact wording is gated by the planning doc.
M23_OWNER_APPROVAL_TITLE = "⏳ **درخواست معامله در وقت اضافه**"
M24_OWNER_APPROVAL_LEAD = "درخواست معامله برای لفظ شما:"
M25_OWNER_APPROVAL_DEADLINE = "⏱ مهلت پاسخ: ۳۰ ثانیه"
M26_OWNER_APPROVAL_CLOSING = "در صورت تأیید، معامله پس از بررسی نهایی ثبت می‌شود."
M27_OWNER_APPROVAL_QUANTITY_TEMPLATE = "📦 مقدار درخواستی: {count} عدد"
M28_OWNER_APPROVE_BUTTON = "✅ تأیید معامله"
M28_OWNER_REJECT_BUTTON = "❌ رد درخواست"


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _normalized_public_id(value: Any) -> str | None:
    if value is None:
        return None
    public_id = str(value).strip()
    if not public_id or not is_offer_request_public_id_shape(public_id):
        return None
    return public_id


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def overtime_owner_approval_feeder() -> TelegramFeederKind:
    return TelegramFeederKind.DIRECT


def overtime_owner_approval_source_natural_id(request_public_id: Any) -> str:
    public_id = _normalized_public_id(request_public_id)
    if public_id is None:
        raise ValueError("overtime_owner_approval_request_public_id_invalid")
    return f"overtime-owner-approval:{public_id}"


def overtime_owner_approval_destination_key(owner_user_id: Any) -> str:
    owner_id = _positive_int(owner_user_id)
    if owner_id is None:
        raise ValueError("overtime_owner_approval_owner_user_id_invalid")
    return f"private:user:{owner_id}"


def build_overtime_owner_approval_callback_data(
    *,
    request_public_id: Any,
    decision: str,
) -> str:
    public_id = _normalized_public_id(request_public_id)
    if public_id is None:
        raise ValueError("overtime_owner_approval_request_public_id_invalid")
    normalized = str(decision or "").strip().lower()
    if normalized not in {"approve", "reject"}:
        raise ValueError("overtime_owner_approval_decision_invalid")
    return f"{OVERTIME_OWNER_APPROVAL_CALLBACK_PREFIX}:{public_id}:{normalized}"


def parse_overtime_owner_approval_callback_data(
    callback_data: Any,
) -> tuple[str, str] | None:
    raw = str(callback_data or "").strip()
    parts = raw.split(":")
    if len(parts) != 3 or parts[0] != OVERTIME_OWNER_APPROVAL_CALLBACK_PREFIX:
        return None
    public_id = _normalized_public_id(parts[1])
    decision = parts[2].strip().lower()
    if public_id is None or decision not in {"approve", "reject"}:
        return None
    return public_id, decision


def offer_is_lot_based(offer: Any) -> bool:
    if bool(getattr(offer, "is_wholesale", True)):
        return False
    lots = getattr(offer, "lot_sizes", None)
    return bool(lots)


def build_overtime_owner_approval_text(
    *,
    offer_text: Any,
    requested_quantity: Any = None,
    include_quantity_line: bool = False,
) -> str:
    body = str(offer_text or "").strip()
    if not body:
        raise ValueError("overtime_owner_approval_offer_text_invalid")
    lines = [
        M23_OWNER_APPROVAL_TITLE,
        "",
        M24_OWNER_APPROVAL_LEAD,
        "",
        body,
        "",
        M25_OWNER_APPROVAL_DEADLINE,
        "",
        M26_OWNER_APPROVAL_CLOSING,
    ]
    if include_quantity_line:
        quantity = _positive_int(requested_quantity)
        if quantity is None:
            raise ValueError("overtime_owner_approval_quantity_invalid")
        # Insert M27 after the offer body block and before the deadline.
        lines[5:5] = [
            "",
            M27_OWNER_APPROVAL_QUANTITY_TEMPLATE.format(count=quantity),
        ]
    return "\n".join(lines)


def build_overtime_owner_approval_reply_markup(
    *,
    request_public_id: Any,
) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": M28_OWNER_APPROVE_BUTTON,
                    "callback_data": build_overtime_owner_approval_callback_data(
                        request_public_id=request_public_id,
                        decision="approve",
                    ),
                },
                {
                    "text": M28_OWNER_REJECT_BUTTON,
                    "callback_data": build_overtime_owner_approval_callback_data(
                        request_public_id=request_public_id,
                        decision="reject",
                    ),
                },
            ]
        ]
    }


def build_overtime_owner_approval_payload(
    *,
    chat_id: Any,
    request_public_id: Any,
    offer_text: Any,
    requested_quantity: Any = None,
    include_quantity_line: bool = False,
) -> dict[str, Any]:
    telegram_chat_id = _positive_int(chat_id)
    if telegram_chat_id is None:
        raise ValueError("overtime_owner_approval_chat_id_invalid")
    return {
        "chat_id": telegram_chat_id,
        "text": build_overtime_owner_approval_text(
            offer_text=offer_text,
            requested_quantity=requested_quantity,
            include_quantity_line=include_quantity_line,
        ),
        "parse_mode": "Markdown",
        "reply_markup": build_overtime_owner_approval_reply_markup(
            request_public_id=request_public_id,
        ),
        "request_public_id": _normalized_public_id(request_public_id),
    }


def overtime_owner_approval_delivery_deadline(
    offer: Any,
    *,
    normal_lifetime_minutes: int,
) -> datetime:
    """Finite delivery deadline = offer final public lifetime end (aware UTC)."""
    _normal, final = compute_lifecycle_deadlines(
        getattr(offer, "created_at", None),
        normal_lifetime_minutes=int(normal_lifetime_minutes),
        overtime_minutes_snapshot=read_overtime_minutes_snapshot(offer),
    )
    if final is None:
        raise ValueError("overtime_owner_approval_final_deadline_missing")
    return _aware_utc(final)


def payload_request_public_id(payload: Mapping[str, Any] | Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    return _normalized_public_id(payload.get("request_public_id"))
