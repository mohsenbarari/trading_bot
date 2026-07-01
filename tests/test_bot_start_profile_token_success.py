import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import build_accountant_register_link_line, build_webapp_link_line, handle_start_with_token


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, value):
        self.value = value

    async def execute(self, stmt):
        return FakeExecuteResult(self.value)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotStartProfileTokenSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_link_helpers_and_logged_in_profile_token_show_keyboard(self):
        from bot.handlers import start as module

        with patch.object(module, "settings", SimpleNamespace(frontend_url="")):
            self.assertIsNone(build_webapp_link_line())
            self.assertIsNone(build_accountant_register_link_line("tok"))

        with patch.object(module, "settings", SimpleNamespace(frontend_url="https://app.example")):
            self.assertIn("https://app.example", build_webapp_link_line())
            self.assertIn("register?token=tok", build_accountant_register_link_line("tok"))

        target_user = SimpleNamespace(id=9)
        profile = SimpleNamespace(target_user=target_user, display_name="target", accountants=())
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=30),
            delete=AsyncMock(),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=91)),
        )
        user = SimpleNamespace(id=5)

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))), patch(
            "bot.handlers.start.load_bot_public_profile", new=AsyncMock(return_value=profile)
        ), patch("bot.handlers.start.build_bot_public_profile_text", return_value="👤 پروفایل"), patch(
            "bot.handlers.start.build_bot_public_profile_keyboard", return_value="KB"
        ), patch(
            "bot.handlers.start.delete_previous_anchor", new=AsyncMock()
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_with_token(message, SimpleNamespace(args="profile_9"), state=SimpleNamespace(), user=user)

        self.assertEqual(message.answer.await_args.args[0], "👤 پروفایل")
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "KB")
        set_anchor.assert_called_once_with(30, 91)

    async def test_handle_start_with_profile_token_requires_logged_in_viewer(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=30),
            delete=AsyncMock(),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))):
            await handle_start_with_token(message, SimpleNamespace(args="profile_9"), state=SimpleNamespace(), user=None)

        message.delete.assert_awaited_once()
        self.assertIn("پروفایل در دسترس نیست", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
