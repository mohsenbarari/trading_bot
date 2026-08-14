import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_history import back_to_profile


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


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))


class BotTradeHistoryBackToProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_back_to_profile_returns_early_without_user(self):
        callback = make_callback()

        await back_to_profile(callback, SimpleNamespace(target_user_id=5), state=SimpleNamespace(), user=None)

        callback.answer.assert_not_awaited()

    async def test_back_to_profile_rebuilds_profile_text_and_answers(self):
        target_user = SimpleNamespace(id=5, account_name="target", mobile_number="0912", address="تهران")
        profile = SimpleNamespace(target_user=target_user, display_name="target", accountants=())
        callback = make_callback()

        with patch("bot.handlers.trade_history.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))), patch(
            "bot.handlers.trade_history.load_bot_public_profile", new=AsyncMock(return_value=profile)
        ), patch("bot.handlers.trade_history.build_bot_public_profile_text", return_value="👤 پروفایل"), patch(
            "bot.handlers.trade_history.build_bot_public_profile_keyboard", return_value="KB"
        ):
            await back_to_profile(callback, SimpleNamespace(target_user_id=5), state=SimpleNamespace(), user=SimpleNamespace(id=2))

        callback.message.edit_text.assert_awaited_once_with("👤 پروفایل", reply_markup="KB")
        callback.answer.assert_awaited_once()

    async def test_back_to_profile_handles_self_target_and_missing_target(self):
        callback = make_callback()
        user = SimpleNamespace(id=2, account_name="self", full_name="Self User", telegram_id=99, role=SimpleNamespace(value="role"))
        with patch("core.config.settings.bot_username", "botname"), patch(
            "bot.handlers.trade_history.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.keyboards.get_user_panel_keyboard", return_value="KB"
        ):
            await back_to_profile(callback, SimpleNamespace(target_user_id=2), state=SimpleNamespace(), user=user)
        self.assertIn("پروفایل شما", callback.message.edit_text.await_args.args[0])
        callback.message.edit_text.assert_awaited_once_with(
            callback.message.edit_text.await_args.args[0],
            parse_mode="Markdown",
            reply_markup="KB",
        )

        callback = make_callback()
        with patch("bot.handlers.trade_history.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))), patch(
            "bot.handlers.trade_history.load_bot_public_profile", new=AsyncMock(return_value=None)
        ):
            await back_to_profile(callback, SimpleNamespace(target_user_id=5), state=SimpleNamespace(), user=SimpleNamespace(id=2))
        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
