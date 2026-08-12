"""Approved Stage 9 bot inventory strings for offer overtime (M1–M37).

Prefer re-exporting constants that already match the planning doc from the
canonical service/contract modules; define bot-only copy here when needed.
"""
from __future__ import annotations

from core.services.offer_overtime_preference_service import (
    BOT_SAVE_UNAVAILABLE_MESSAGE,
    INVALID_OVERTIME_VALUE_MESSAGE,
    OVERTIME_NOT_AVAILABLE_MESSAGE,
    REACHABILITY_WARNING_MESSAGE,
    SAVE_SUCCESS_NONZERO_MESSAGE,
    SAVE_SUCCESS_ZERO_MESSAGE,
)
from core.services.offer_overtime_request_service import (
    ALREADY_TERMINAL_MESSAGE,
    COOLDOWN_MESSAGE,
    DECISION_EXPIRED_MESSAGE,
    NOT_OWNER_MESSAGE,
    REQUESTER_LIMIT_MESSAGE,
    REQUESTER_OWNER_LIMIT_MESSAGE,
    SAME_OFFER_BUSY_MESSAGE,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    M23_OWNER_APPROVAL_TITLE,
    M24_OWNER_APPROVAL_LEAD,
    M25_OWNER_APPROVAL_DEADLINE,
    M26_OWNER_APPROVAL_CLOSING,
    M27_OWNER_APPROVAL_QUANTITY_TEMPLATE,
    M28_OWNER_APPROVE_BUTTON,
    M28_OWNER_REJECT_BUTTON,
)
from core.trade_forward_pending import AMBIGUOUS_FORWARD_PENDING_MESSAGE

# --- Preference entry and typed confirm (M1–M3, M2b, M2c) ---

M1_OVERTIME_PREFERENCE_BUTTON = "⏳ وقت اضافه"

M2_OVERTIME_PREFERENCE_PROMPT_CURRENT_TEMPLATE = "وقت اضافه لفظ‌های جدید شما: {current_value}"
M2_OVERTIME_PREFERENCE_PROMPT_INSTRUCTION = "عددی بین ۰ تا ۱۰ دقیقه بفرستید. صفر یعنی غیرفعال."

M2B_OVERTIME_PREFERENCE_CONFIRM_NONZERO_TEMPLATE = "وقت اضافه روی {minutes} دقیقه تنظیم شود؟"
M2B_ZERO_OVERTIME_PREFERENCE_CONFIRM = "وقت اضافه غیرفعال شود؟"

M2C_OVERTIME_PREFERENCE_CONFIRM_BUTTON = "✅ تایید"
M2C_OVERTIME_PREFERENCE_CANCEL_BUTTON = "❌ انصراف"

M3_OVERTIME_PREFERENCE_ZERO_DISPLAY = "غیرفعال"

# --- Save outcomes (M4–M8) — re-exported from preference service ---

M4_SAVE_SUCCESS_NONZERO_MESSAGE = SAVE_SUCCESS_NONZERO_MESSAGE
M5_SAVE_SUCCESS_ZERO_MESSAGE = SAVE_SUCCESS_ZERO_MESSAGE
M6_REACHABILITY_WARNING_MESSAGE = REACHABILITY_WARNING_MESSAGE
M7_BOT_SAVE_UNAVAILABLE_MESSAGE = BOT_SAVE_UNAVAILABLE_MESSAGE
M8_INVALID_OVERTIME_VALUE_MESSAGE = INVALID_OVERTIME_VALUE_MESSAGE

M_NOT_AVAILABLE_MESSAGE = OVERTIME_NOT_AVAILABLE_MESSAGE

# --- WebApp-only labels kept for inventory completeness (M9, M21, M22) ---

M9_WEBAPP_OVERTIME_LABEL = "وقت اضافه"
M9_WEBAPP_OVERTIME_HELPER = (
    "پس از پایان زمان لفظ، تا این مدت درخواست معامله با تأیید شما پذیرفته می‌شود."
)
M21_WEBAPP_REQUESTER_QUEUED = "در حال ارسال درخواست..."
M22_WEBAPP_REQUESTER_COUNTDOWN_START = "۰۰:۳۰"
M35_WEBAPP_OWNER_TITLE = "درخواست معامله در وقت اضافه"
M36_WEBAPP_OWNER_APPROVE_BUTTON = "تأیید معامله"
M36_WEBAPP_OWNER_REJECT_BUTTON = "رد درخواست"

# --- Requester status and errors (M10–M20b) ---

M10_REQUESTER_STATUS_QUEUED = "⏳ درخواست معامله ثبت شد و در صف بررسی است."
M11_REQUESTER_STATUS_PRESENTED = "⏳ درخواست در حال بررسی است."
M12_REQUESTER_CANCEL_BUTTON = "لغو درخواست"
M13_REQUESTER_STATUS_APPROVED = "معامله انجام شد."
M14_REQUESTER_STATUS_TERMINAL_FAILURE = "درخواست انجام نشد."
M15_REQUESTER_STATUS_CANCELLED = "درخواست لغو شد."

M16_SAME_OFFER_BUSY_MESSAGE = SAME_OFFER_BUSY_MESSAGE
M17_COOLDOWN_MESSAGE = COOLDOWN_MESSAGE
M18_AMBIGUOUS_FORWARD_PENDING_MESSAGE = AMBIGUOUS_FORWARD_PENDING_MESSAGE
M19_REQUEST_SEND_FAILED_MESSAGE = "درخواست ارسال نشد. لطفاً دوباره تلاش کنید."
M20_REQUESTER_LIMIT_MESSAGE = REQUESTER_LIMIT_MESSAGE
M20B_REQUESTER_OWNER_LIMIT_MESSAGE = REQUESTER_OWNER_LIMIT_MESSAGE

# --- Owner approval message (M23–M28) — re-exported ---

M23_OWNER_APPROVAL_TITLE = M23_OWNER_APPROVAL_TITLE
M24_OWNER_APPROVAL_LEAD = M24_OWNER_APPROVAL_LEAD
M25_OWNER_APPROVAL_DEADLINE = M25_OWNER_APPROVAL_DEADLINE
M26_OWNER_APPROVAL_CLOSING = M26_OWNER_APPROVAL_CLOSING
M27_OWNER_APPROVAL_QUANTITY_TEMPLATE = M27_OWNER_APPROVAL_QUANTITY_TEMPLATE
M28_OWNER_APPROVE_BUTTON = M28_OWNER_APPROVE_BUTTON
M28_OWNER_REJECT_BUTTON = M28_OWNER_REJECT_BUTTON

# --- Owner terminal and callback answers (M29–M34, M37) ---

M29_OWNER_STATUS_APPROVED = "معامله انجام شد."
M30_OWNER_STATUS_REJECTED = "درخواست رد شد."
M31_OWNER_STATUS_CLOSED = "درخواست بسته شد."
M32_OWNER_DECISION_EXPIRED_MESSAGE = DECISION_EXPIRED_MESSAGE
M33_OWNER_ALREADY_TERMINAL_MESSAGE = ALREADY_TERMINAL_MESSAGE
M34_OWNER_NOT_OWNER_MESSAGE = NOT_OWNER_MESSAGE
M37_OWNER_REVALIDATION_FAILED_MESSAGE = "شرایط این لفظ تغییر کرده و معامله انجام نشد."


def format_overtime_preference_current_display(minutes: int) -> str:
    """Return M3 for zero or the whole-minute count otherwise."""
    if minutes == 0:
        return M3_OVERTIME_PREFERENCE_ZERO_DISPLAY
    return str(minutes)


def build_overtime_preference_value_prompt(current_minutes: int) -> str:
    """Return the two-line M2 prompt with the current persisted value."""
    current_value = format_overtime_preference_current_display(current_minutes)
    return (
        f"{M2_OVERTIME_PREFERENCE_PROMPT_CURRENT_TEMPLATE.format(current_value=current_value)}\n"
        f"{M2_OVERTIME_PREFERENCE_PROMPT_INSTRUCTION}"
    )


def build_overtime_preference_confirm_prompt(minutes: int) -> str:
    """Return M2b or M2b-zero for the typed value awaiting confirmation."""
    if minutes == 0:
        return M2B_ZERO_OVERTIME_PREFERENCE_CONFIRM
    return M2B_OVERTIME_PREFERENCE_CONFIRM_NONZERO_TEMPLATE.format(minutes=minutes)
