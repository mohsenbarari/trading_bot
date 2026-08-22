import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import offer_overtime_preference as handler
from bot.keyboards import get_user_panel_keyboard
from bot.states import OfferOvertimePreference
from core.enums import UserRole
from core.offer_overtime_bot_copy import (
    M2B_ZERO_OVERTIME_PREFERENCE_CONFIRM,
    M7_BOT_SAVE_UNAVAILABLE_MESSAGE,
    M8_INVALID_OVERTIME_VALUE_MESSAGE,
    M1_OVERTIME_PREFERENCE_BUTTON,
)
from core.services.offer_overtime_preference_service import OfferOvertimePreferenceTransportError
from core.telegram_delivery_queue_contract import TelegramFlowExit


class FakeSessionContext:
    def __init__(self, session=None):
        self.session = session or SimpleNamespace(get=AsyncMock(return_value=None))

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.state = None
        self.cleared = False

    async def clear(self):
        self.data.clear()
        self.state = None
        self.cleared = True

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, value):
        self.state = value

    async def get_data(self):
        return dict(self.data)


def _user(**overrides):
    base = {
        "id": 11,
        "role": UserRole.STANDARD,
        "offer_overtime_minutes": 3,
        "account_status": None,
        "messenger_blocked_at": None,
        "messenger_grace_expires_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _message(text="5"):
    return SimpleNamespace(text=text, answer=AsyncMock())


def _callback():
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )


class BotOfferOvertimePreferenceTests(unittest.IsolatedAsyncioTestCase):
    def test_save_success_copy_contains_only_the_persisted_result(self):
        detail = "✅ وقت اضافه لفظ‌های جدید شما روی ۵ دقیقه تنظیم شد."
        self.assertEqual(handler._compose_save_success_text(detail), detail)

    def test_user_panel_keyboard_hides_overtime_button_when_not_eligible(self):
        keyboard = get_user_panel_keyboard(
            UserRole.STANDARD,
            standard_actions=True,
            show_overtime_preference=False,
        )
        button_texts = [button.text for row in keyboard.keyboard for button in row]
        self.assertNotIn(M1_OVERTIME_PREFERENCE_BUTTON, button_texts)

    def test_user_panel_keyboard_shows_overtime_button_when_eligible(self):
        keyboard = get_user_panel_keyboard(
            UserRole.STANDARD,
            standard_actions=True,
            show_overtime_preference=True,
        )
        button_texts = [button.text for row in keyboard.keyboard for button in row]
        self.assertIn(M1_OVERTIME_PREFERENCE_BUTTON, button_texts)
        self.assertLess(
            button_texts.index(M1_OVERTIME_PREFERENCE_BUTTON),
            button_texts.index("🔙 بازگشت"),
        )

    async def test_non_numeric_input_returns_m8(self):
        user = _user()
        state = FakeState()
        with patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=False),
        ), patch(
            "bot.handlers.offer_overtime_preference.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as answer_runtime:
            await handler.process_offer_overtime_preference_value(_message("abc"), state, user)

        answer_runtime.assert_awaited()
        self.assertEqual(answer_runtime.await_args.args[2], M8_INVALID_OVERTIME_VALUE_MESSAGE)

    async def test_out_of_range_input_returns_m8(self):
        user = _user()
        state = FakeState()
        with patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=False),
        ), patch(
            "bot.handlers.offer_overtime_preference.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as answer_runtime:
            await handler.process_offer_overtime_preference_value(_message("11"), state, user)

        self.assertEqual(answer_runtime.await_args.args[2], M8_INVALID_OVERTIME_VALUE_MESSAGE)

    async def test_zero_value_uses_zero_confirm_text(self):
        user = _user()
        state = FakeState()
        with patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=False),
        ), patch(
            "bot.handlers.offer_overtime_preference.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as answer_runtime:
            await handler.process_offer_overtime_preference_value(_message("0"), state, user)

        self.assertEqual(state.state, OfferOvertimePreference.awaiting_confirmation)
        self.assertEqual(answer_runtime.await_args.args[2], M2B_ZERO_OVERTIME_PREFERENCE_CONFIRM)

    async def test_cancel_clears_state(self):
        user = _user()
        state = FakeState({"overtime_preference_pending_minutes": 4})
        with patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new=AsyncMock(),
        ) as answer_message, patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new=AsyncMock(return_value="KB"),
        ):
            await handler.cancel_offer_overtime_preference(_callback(), state, user)

        self.assertTrue(state.cleared)
        self.assertEqual(answer_message.await_args.kwargs["reply_markup"], "KB")
        self.assertTrue(answer_message.await_args.kwargs["set_persistent_anchor"])
        self.assertEqual(answer_message.await_args.kwargs["flow_exit"], TelegramFlowExit.CANCEL)

    async def test_success_restores_persistent_menu_with_a_new_anchor(self):
        user = _user()
        state = FakeState({"overtime_preference_pending_minutes": 5})
        db_user = SimpleNamespace(id=11, offer_overtime_minutes=3)
        session = SimpleNamespace(get=AsyncMock(return_value=db_user))
        result = SimpleNamespace(
            offer_overtime_minutes=5,
            detail="✅ وقت اضافه لفظ‌های جدید شما روی ۵ دقیقه تنظیم شد.",
        )

        with patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new=AsyncMock(return_value=result),
        ), patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new=AsyncMock(),
        ) as answer_message, patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new=AsyncMock(return_value="KB"),
        ):
            await handler.confirm_offer_overtime_preference(_callback(), state, user)

        self.assertTrue(state.cleared)
        self.assertEqual(user.offer_overtime_minutes, 5)
        self.assertEqual(answer_message.await_args.args[2], result.detail)
        self.assertEqual(answer_message.await_args.kwargs["reply_markup"], "KB")
        self.assertTrue(answer_message.await_args.kwargs["set_persistent_anchor"])
        self.assertEqual(answer_message.await_args.kwargs["flow_exit"], TelegramFlowExit.SUCCESS)

    async def test_transport_error_surfaces_m7(self):
        user = _user()
        state = FakeState({"overtime_preference_pending_minutes": 2})
        db_user = SimpleNamespace(id=11, offer_overtime_minutes=3)
        session = SimpleNamespace(get=AsyncMock(return_value=db_user))

        with patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new=AsyncMock(side_effect=OfferOvertimePreferenceTransportError()),
        ), patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer_callback:
            await handler.confirm_offer_overtime_preference(_callback(), state, user)

        answer_callback.assert_awaited()
        self.assertEqual(answer_callback.await_args.args[1], M7_BOT_SAVE_UNAVAILABLE_MESSAGE)
        self.assertTrue(answer_callback.await_args.kwargs.get("show_alert"))


if __name__ == "__main__":
    unittest.main()
