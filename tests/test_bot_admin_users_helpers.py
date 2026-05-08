import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import clear_state_retain_anchors, delete_user_message, safe_delete_message, update_anchor


def consume_task(coro):
    coro.close()
    return None


class BotAdminUsersHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_delete_message_honors_delay_and_swallows_errors(self):
        bot = SimpleNamespace(delete_message=AsyncMock())
        with patch("bot.handlers.admin_users.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await safe_delete_message(bot, 10, 20, delay=5)
        sleep_mock.assert_awaited_once_with(5)
        bot.delete_message.assert_awaited_once_with(10, 20)

        bot = SimpleNamespace(delete_message=AsyncMock(side_effect=RuntimeError("boom")))
        await safe_delete_message(bot, 10, 20)

    async def test_update_anchor_replaces_old_anchor_and_preserves_same_anchor(self):
        state = SimpleNamespace(get_data=AsyncMock(return_value={"anchor_id": 11}), update_data=AsyncMock())
        with patch("bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task) as create_task_mock, patch(
            "bot.handlers.admin_users.safe_delete_message", new=AsyncMock()
        ):
            await update_anchor(state, 22, SimpleNamespace(), 33)
        state.update_data.assert_awaited_once_with(anchor_id=22)
        create_task_mock.assert_called_once()

        state = SimpleNamespace(get_data=AsyncMock(return_value={"anchor_id": 22}), update_data=AsyncMock())
        with patch("bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task) as create_task_mock, patch(
            "bot.handlers.admin_users.safe_delete_message", new=AsyncMock()
        ):
            await update_anchor(state, 22, SimpleNamespace(), 33)
        create_task_mock.assert_not_called()

    async def test_clear_state_retain_anchors_and_delete_user_message(self):
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"anchor_id": 7, "users_menu_id": 8, "other": 9}),
            clear=AsyncMock(),
            update_data=AsyncMock(),
        )
        await clear_state_retain_anchors(state)
        state.clear.assert_awaited_once()
        state.update_data.assert_awaited_once_with(anchor_id=7, users_menu_id=8)

        message = SimpleNamespace(delete=AsyncMock())
        await delete_user_message(message)
        message.delete.assert_awaited_once()

        message = SimpleNamespace(delete=AsyncMock(side_effect=RuntimeError("boom")))
        await delete_user_message(message)


if __name__ == "__main__":
    unittest.main()