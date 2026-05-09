import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.link_account import LinkState, cmd_link


class FakeState:
    def __init__(self):
        self.states = []

    async def set_state(self, state):
        self.states.append(state)


class BotLinkAccountCmdTests(unittest.IsolatedAsyncioTestCase):
    async def test_cmd_link_prompts_for_contact_and_sets_state(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = FakeState()

        await cmd_link(message, state, user=None)

        self.assertIn("شماره موبایل", message.answer.await_args.args[0])
        self.assertEqual(state.states, [LinkState.waiting_for_contact])

    async def test_cmd_link_skips_contact_for_already_linked_user(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = FakeState()
        user = SimpleNamespace(id=7, role="standard", address="تهران خیابان آزادی پلاک ۱۰")

        await cmd_link(message, state, user=user)

        self.assertIn("قبلاً به تلگرام متصل شده", message.answer.await_args.args[0])
        self.assertEqual(state.states, [])


if __name__ == "__main__":
    unittest.main()