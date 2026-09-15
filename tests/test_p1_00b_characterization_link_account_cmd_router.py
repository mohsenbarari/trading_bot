import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import types
from bot.handlers.link_account import LinkState, cmd_link
from core.config import settings


class FakeState:
    def __init__(self, initial_data=None):
        self.cleared = 0
        self.data = dict(initial_data or {})
        self.states = []
        self.update_data_calls = []

    async def clear(self):
        self.cleared += 1

    async def set_state(self, state):
        self.states.append(state)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        self.update_data_calls.append(kwargs)

    async def get_data(self):
        return dict(self.data)


class TestLinkAccountCmdRouterCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_cmd_link_router_missing_user_sends_neutral_copy_and_clears_state(self):
        message = SimpleNamespace(
            answer=AsyncMock(),
            bot=SimpleNamespace(),
            from_user=SimpleNamespace(id=10, username="test_user", full_name="Test User"),
        )
        state = FakeState()

        await cmd_link(message, state, user=None)

        self.assertEqual(message.answer.await_count, 1)
        call_args, call_kwargs = message.answer.await_args
        expected_neutral = (
            "برای فعال\u200cسازی این مسیر، ابتدا ثبت\u200cنام را در سامانه کامل کنید "
            "و از همان\u200cجا وارد ربات شوید."
        )
        self.assertEqual(call_args[0], expected_neutral)
        self.assertIsInstance(call_kwargs.get("reply_markup"), types.ReplyKeyboardRemove)

        self.assertEqual(state.cleared, 1)
        self.assertEqual(state.states, [])
        self.assertEqual(state.data, {})
        self.assertEqual(state.update_data_calls, [])

    async def test_cmd_link_router_incomplete_address_prompts_for_completion(self):
        message = SimpleNamespace(
            answer=AsyncMock(),
            bot=SimpleNamespace(),
            from_user=SimpleNamespace(id=42, username="pending_user", full_name="Pending User"),
        )
        state = FakeState()
        user = SimpleNamespace(id=42, address="REGISTRATION_PENDING")

        with (
            patch(
                "bot.handlers.link_account.build_linked_account_panel_message",
                new_callable=AsyncMock,
            ) as mock_panel,
            patch(
                "bot.handlers.link_account.build_persistent_navigation_keyboard",
                new_callable=AsyncMock,
            ) as mock_nav,
        ):
            await cmd_link(message, state, user=user)

            mock_panel.assert_not_called()
            mock_nav.assert_not_called()

        self.assertEqual(message.answer.await_count, 1)
        call_args, call_kwargs = message.answer.await_args
        expected_prompt = (
            "✅ حساب شما شناسایی شد، اما ثبت\u200cنام هنوز کامل نشده است.\n\n"
            "📍 برای تکمیل ثبت\u200cنام، آدرس خود را جهت جابجایی سکه وارد نمایید:"
        )
        self.assertEqual(call_args[0], expected_prompt)
        self.assertIsInstance(call_kwargs.get("reply_markup"), types.ReplyKeyboardRemove)

        self.assertEqual(state.cleared, 0)
        self.assertEqual(state.states, [LinkState.waiting_for_address])
        self.assertEqual(state.data, {"link_user_id": 42})
        self.assertEqual(state.update_data_calls, [{"link_user_id": 42}])

    async def test_cmd_link_router_linked_user_sends_panel_and_preserves_state(self):
        bot = SimpleNamespace()
        message = SimpleNamespace(
            answer=AsyncMock(),
            bot=bot,
            from_user=SimpleNamespace(id=99, username="linked_user", full_name="Linked User"),
        )
        initial_data = {"preserved_token": "keep_intact"}
        state = FakeState(initial_data=initial_data)
        user = SimpleNamespace(id=99, address="Tehran, Valiasr St, No 123")

        panel_text = "✅ پنل کاربری حساب متصل شده"
        panel_markup = types.ReplyKeyboardMarkup(keyboard=[])

        with (
            patch(
                "bot.handlers.link_account.build_linked_account_panel_message",
                new_callable=AsyncMock,
                return_value=panel_text,
            ) as mock_panel,
            patch(
                "bot.handlers.link_account.build_persistent_navigation_keyboard",
                new_callable=AsyncMock,
                return_value=panel_markup,
            ) as mock_nav,
            patch(
                "bot.handlers.link_account.answer_pre_auth_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer,
        ):
            await cmd_link(message, state, user=user)

            mock_panel.assert_awaited_once_with(
                message.bot,
                user,
                already_linked=True,
            )
            mock_nav.assert_awaited_once_with(
                user,
                settings.frontend_url,
            )
            mock_answer.assert_awaited_once_with(
                message,
                panel_text,
                reply_markup=panel_markup,
            )

        self.assertEqual(state.cleared, 0)
        self.assertEqual(state.states, [])
        self.assertEqual(state.update_data_calls, [])
        self.assertEqual(state.data, initial_data)
        self.assertEqual(await state.get_data(), initial_data)
        self.assertEqual(message.answer.await_count, 0)
        self.assertNotIn(LinkState.waiting_for_address, state.states)
        self.assertNotIn("link_user_id", state.data)


if __name__ == "__main__":
    unittest.main()
