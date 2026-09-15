import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import types
from bot.handlers.link_account import cmd_link


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


if __name__ == "__main__":
    unittest.main()
