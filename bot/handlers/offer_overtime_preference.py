"""Bot FSM for the Iran-authoritative offer overtime preference."""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.repeat_offer import build_user_panel_navigation_keyboard
from bot.states import OfferOvertimePreference
from bot.telegram_callback_answer import answer_callback_query_via_runtime
from bot.telegram_interaction_message import (
    answer_callback_message_via_runtime,
    answer_incoming_message_via_runtime,
)
from core.db import AsyncSessionLocal
from core.offer_overtime_bot_copy import (
    M2C_OVERTIME_PREFERENCE_CANCEL_BUTTON,
    M2C_OVERTIME_PREFERENCE_CONFIRM_BUTTON,
    M7_BOT_SAVE_UNAVAILABLE_MESSAGE,
    M8_INVALID_OVERTIME_VALUE_MESSAGE,
    M_NOT_AVAILABLE_MESSAGE,
    M1_OVERTIME_PREFERENCE_BUTTON,
    build_overtime_preference_confirm_prompt,
    build_overtime_preference_value_prompt,
)
from core.services.offer_overtime_preference_service import (
    OfferOvertimePreferenceError,
    OfferOvertimePreferenceNotAllowedError,
    OfferOvertimePreferenceTransportError,
    evaluate_overtime_preference_eligibility,
    normalize_overtime_minutes,
    read_persisted_overtime_minutes,
    save_overtime_preference_from_bot,
)
from core.services.user_account_status_service import is_user_global_web_locked
from models.user import User

router = Router()
logger = logging.getLogger(__name__)


class OfferOvertimePreferenceCallback(CallbackData, prefix="offer_ot_pref"):
    action: str


async def _overtime_preference_allowed(user: Optional[User]) -> tuple[bool, str | None]:
    if not user:
        return False, "کاربر شناسایی نشد."
    if is_user_global_web_locked(user):
        return False, "دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است."
    async with AsyncSessionLocal() as session:
        eligibility = await evaluate_overtime_preference_eligibility(session, user)
    if not eligibility.allowed:
        return False, M_NOT_AVAILABLE_MESSAGE
    return True, None


async def _user_panel_show_overtime_preference(user: User) -> bool:
    async with AsyncSessionLocal() as session:
        eligibility = await evaluate_overtime_preference_eligibility(session, user)
    return eligibility.allowed


async def _user_panel_reply_markup(user: User, *, standard_actions: bool = False, show_support: bool = False):
    return await build_user_panel_navigation_keyboard(
        user,
        standard_actions=standard_actions,
        show_support=show_support,
        show_overtime_preference=await _user_panel_show_overtime_preference(user),
    )


def _overtime_preference_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=M2C_OVERTIME_PREFERENCE_CANCEL_BUTTON,
                    callback_data=OfferOvertimePreferenceCallback(action="cancel").pack(),
                )
            ]
        ]
    )


def _overtime_preference_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=M2C_OVERTIME_PREFERENCE_CONFIRM_BUTTON,
                    callback_data=OfferOvertimePreferenceCallback(action="confirm").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=M2C_OVERTIME_PREFERENCE_CANCEL_BUTTON,
                    callback_data=OfferOvertimePreferenceCallback(action="cancel").pack(),
                )
            ],
        ]
    )


def _compose_save_success_text(detail: str, warning: str | None) -> str:
    if warning:
        return f"{detail}\n\n{warning}"
    return detail


@router.message(F.text == M1_OVERTIME_PREFERENCE_BUTTON)
async def start_offer_overtime_preference(message: types.Message, state: FSMContext, user: Optional[User]):
    allowed, reason = await _overtime_preference_allowed(user)
    if not allowed:
        await answer_incoming_message_via_runtime(
            message,
            user,
            reason or M_NOT_AVAILABLE_MESSAGE,
            source_key="overtime-preference-start-denied",
        )
        return

    current_minutes = read_persisted_overtime_minutes(user)
    await state.clear()
    await state.set_state(OfferOvertimePreference.awaiting_value)
    await answer_incoming_message_via_runtime(
        message,
        user,
        build_overtime_preference_value_prompt(current_minutes),
        source_key="overtime-preference-value-prompt",
        reply_markup=_overtime_preference_cancel_keyboard(),
        temporary_context_keyboard=True,
    )


@router.message(OfferOvertimePreference.awaiting_value)
async def process_offer_overtime_preference_value(
    message: types.Message,
    state: FSMContext,
    user: Optional[User],
):
    if not user:
        return

    from bot.handlers.panel import handoff_navigation_button

    if await handoff_navigation_button(message, state, user):
        return

    allowed, reason = await _overtime_preference_allowed(user)
    if not allowed:
        await state.clear()
        await answer_incoming_message_via_runtime(
            message,
            user,
            reason or M_NOT_AVAILABLE_MESSAGE,
            source_key="overtime-preference-value-access-denied",
        )
        return

    try:
        minutes = normalize_overtime_minutes(message.text)
    except OfferOvertimePreferenceError:
        await answer_incoming_message_via_runtime(
            message,
            user,
            M8_INVALID_OVERTIME_VALUE_MESSAGE,
            source_key="overtime-preference-value-invalid",
            reply_markup=_overtime_preference_cancel_keyboard(),
            temporary_context_keyboard=True,
        )
        return

    await state.update_data(overtime_preference_pending_minutes=minutes)
    await state.set_state(OfferOvertimePreference.awaiting_confirmation)
    await answer_incoming_message_via_runtime(
        message,
        user,
        build_overtime_preference_confirm_prompt(minutes),
        source_key="overtime-preference-confirm-prompt",
        reply_markup=_overtime_preference_confirm_keyboard(),
        temporary_context_keyboard=True,
    )


@router.callback_query(
    OfferOvertimePreferenceCallback.filter(F.action == "confirm"),
    StateFilter(OfferOvertimePreference.awaiting_confirmation),
)
async def confirm_offer_overtime_preference(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
):
    allowed, reason = await _overtime_preference_allowed(user)
    if not allowed:
        await state.clear()
        await answer_callback_query_via_runtime(callback, reason or M_NOT_AVAILABLE_MESSAGE, show_alert=True)
        return

    data = await state.get_data()
    pending = data.get("overtime_preference_pending_minutes")
    if pending is None:
        await state.clear()
        await answer_callback_query_via_runtime(callback, "اطلاعات تنظیم ناقص است.", show_alert=True)
        return

    try:
        minutes = normalize_overtime_minutes(pending)
    except OfferOvertimePreferenceError:
        await state.clear()
        await answer_callback_query_via_runtime(callback, M8_INVALID_OVERTIME_VALUE_MESSAGE, show_alert=True)
        return

    try:
        async with AsyncSessionLocal() as session:
            db_user = await session.get(User, user.id)
            if db_user is None:
                await state.clear()
                await answer_callback_query_via_runtime(callback, M_NOT_AVAILABLE_MESSAGE, show_alert=True)
                return
            result = await save_overtime_preference_from_bot(session, db_user, minutes)
    except OfferOvertimePreferenceNotAllowedError as exc:
        await state.clear()
        await answer_callback_query_via_runtime(callback, exc.message, show_alert=True)
        return
    except OfferOvertimePreferenceTransportError:
        await answer_callback_query_via_runtime(
            callback,
            M7_BOT_SAVE_UNAVAILABLE_MESSAGE,
            show_alert=True,
        )
        return
    except OfferOvertimePreferenceError as exc:
        await answer_callback_query_via_runtime(callback, exc.message, show_alert=True)
        return
    except Exception:
        logger.exception("Unexpected overtime preference save failure")
        await answer_callback_query_via_runtime(
            callback,
            M7_BOT_SAVE_UNAVAILABLE_MESSAGE,
            show_alert=True,
        )
        return

    await state.clear()
    setattr(user, "offer_overtime_minutes", result.offer_overtime_minutes)
    await answer_callback_query_via_runtime(callback)
    await answer_callback_message_via_runtime(
        callback,
        user,
        _compose_save_success_text(result.detail, result.warning),
        source_key="overtime-preference-save-success",
        reply_markup=await _user_panel_reply_markup(user),
    )


@router.callback_query(
    OfferOvertimePreferenceCallback.filter(F.action == "cancel"),
    StateFilter(OfferOvertimePreference),
)
async def cancel_offer_overtime_preference(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
):
    await state.clear()
    await answer_callback_query_via_runtime(callback, "لغو شد")
    if user:
        await answer_callback_message_via_runtime(
            callback,
            user,
            "تنظیم وقت اضافه لغو شد.",
            source_key="overtime-preference-cancelled",
            reply_markup=await _user_panel_reply_markup(user),
        )
