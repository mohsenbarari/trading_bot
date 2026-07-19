import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.link_account import (
    INCOMPLETE_ADDRESS_SENTINELS,
    LinkState,
    build_accountant_web_only_message,
    build_customer_web_only_message,
    build_webapp_link_line,
    cmd_link,
    user_requires_address_completion,
)


class FakeState:
    def __init__(self):
        self.states = []
        self.data = {}
        self.cleared = 0

    async def set_state(self, state):
        self.states.append(state)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared += 1


class BotLinkAccountCmdTests(unittest.IsolatedAsyncioTestCase):
    async def test_link_helpers_and_cmd_link_for_incomplete_address(self):
        self.assertTrue(user_requires_address_completion(SimpleNamespace(address="")))
        for sentinel in INCOMPLETE_ADDRESS_SENTINELS:
            self.assertTrue(user_requires_address_completion(SimpleNamespace(address=sentinel)))
        self.assertFalse(user_requires_address_completion(SimpleNamespace(address="تهران خیابان آزادی پلاک ۱۰")))

        from bot.handlers import link_account as module

        with unittest.mock.patch.object(module, "settings", SimpleNamespace(frontend_url="")):
            self.assertIsNone(build_webapp_link_line())

        with unittest.mock.patch.object(module, "settings", SimpleNamespace(frontend_url="https://app.example")):
            self.assertIn("https://app.example", build_webapp_link_line())
            self.assertIn("وباپ".replace("\u000c", "‌"), build_accountant_web_only_message())
            self.assertIn("وباپ".replace("\u000c", "‌"), build_customer_web_only_message())

        with unittest.mock.patch.object(
            module,
            "settings",
            SimpleNamespace(
                iran_server_url="https://coin.gold-trade.ir/",
                frontend_url="https://coin.362514.ir",
            ),
        ):
            self.assertIn("https://coin.gold-trade.ir", build_webapp_link_line())
            self.assertNotIn("coin.362514.ir", build_webapp_link_line())

        message = SimpleNamespace(answer=AsyncMock())
        state = FakeState()
        user = SimpleNamespace(id=7, role="standard", address="System Default")
        await cmd_link(message, state, user=user)
        self.assertIn("ثبتنام هنوز کامل نشده".replace("\u000c", "‌"), message.answer.await_args.args[0])
        self.assertEqual(state.states, [LinkState.waiting_for_address])

    async def test_cmd_link_without_registered_user_returns_neutral_fallback(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = FakeState()

        await cmd_link(message, state, user=None)

        self.assertIn("ثبت‌نام را در سامانه کامل کنید", message.answer.await_args.args[0])
        self.assertEqual(state.states, [])
        self.assertEqual(state.cleared, 1)

    async def test_cmd_link_skips_contact_for_already_linked_user(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = FakeState()
        user = SimpleNamespace(id=7, role="standard", address="تهران خیابان آزادی پلاک ۱۰")

        # This is a command-unit test, not an integration test.  Keep the
        # display-name enrichment boundary synthetic so importing the global
        # application session factory cannot open a process-lifetime asyncpg
        # pool connection on the per-test event loop.
        with patch(
            "bot.handlers.link_account.attach_customer_management_names",
            new=AsyncMock(),
        ):
            await cmd_link(message, state, user=user)

        self.assertIn("قبلاً به تلگرام متصل شده", message.answer.await_args.args[0])
        self.assertEqual(state.states, [])


if __name__ == "__main__":
    unittest.main()
