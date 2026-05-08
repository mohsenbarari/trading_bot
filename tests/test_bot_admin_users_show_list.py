import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import show_users_list


def consume_task(coro):
    coro.close()
    return None


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class FakeUsersResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalars(self.values)


class FakeSession:
    def __init__(self, results=None, error=None):
        self.results = iter(results or [])
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return next(self.results)


class BotAdminUsersShowListTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_users_list_handles_empty_and_edit_fallback(self):
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=51)))
        state = SimpleNamespace()
        with patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
            return_value=FakeSession([FakeScalarResult(0), FakeUsersResult([])]),
        ), patch("bot.handlers.admin_users.update_anchor", new=AsyncMock()) as anchor_mock:
            await show_users_list(bot, 1, state, page=1)
        self.assertIn("هیچ کاربری یافت نشد", bot.send_message.await_args.args[1])
        anchor_mock.assert_awaited_once_with(state, 51, bot, 1)

        users = [SimpleNamespace(id=1, account_name="ali")]
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(side_effect=RuntimeError("edit failed")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=52)),
        )
        with patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
            return_value=FakeSession([FakeScalarResult(1), FakeUsersResult(users)]),
        ), patch("bot.handlers.admin_users.get_users_list_inline_keyboard", return_value="KB") as keyboard_mock, patch(
            "bot.handlers.admin_users.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await show_users_list(bot, 2, state, page=2, message_id_to_edit=77)
        keyboard_mock.assert_called_once_with(users, 2, 1, 10)
        bot.send_message.assert_awaited_once()
        anchor_mock.assert_awaited_once_with(state, 52, bot, 2)

    async def test_show_users_list_handles_top_level_exception(self):
        error_msg = SimpleNamespace(message_id=99)
        bot = SimpleNamespace(send_message=AsyncMock(return_value=error_msg))
        with patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
            return_value=FakeSession(error=RuntimeError("db down")),
        ), patch("bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task) as create_task_mock:
            await show_users_list(bot, 3, SimpleNamespace(), page=1)
        self.assertIn("خطایی در دریافت لیست کاربران", bot.send_message.await_args.args[1])
        create_task_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()