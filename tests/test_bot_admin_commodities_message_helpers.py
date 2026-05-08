import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import clear_state_retain_anchor, delete_user_message, safe_delete_message, update_anchor


class BotAdminCommoditiesMessageHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_delete_update_anchor_clear_state_and_delete_user_message(self):
        bot = SimpleNamespace(delete_message=AsyncMock())
        with patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await safe_delete_message(bot, 1, 2, delay=3)
        sleep_mock.assert_awaited_once_with(3)
        bot.delete_message.assert_awaited_once_with(1, 2)

        bot = SimpleNamespace(delete_message=AsyncMock(side_effect=RuntimeError("boom")))
        await safe_delete_message(bot, 1, 2)

        state = SimpleNamespace(get_data=AsyncMock(return_value={"anchor_id": 10}), update_data=AsyncMock())

        def fake_create_task(coro):
            coro.close()
            return SimpleNamespace()

        with patch("bot.handlers.admin_commodities.asyncio.create_task", side_effect=fake_create_task) as create_task_mock:
            await update_anchor(state, 20, bot=SimpleNamespace(), chat_id=5)
        state.update_data.assert_awaited_once_with(anchor_id=20)
        create_task_mock.assert_called_once()

        state = SimpleNamespace(get_data=AsyncMock(return_value={"anchor_id": 20}), clear=AsyncMock(), update_data=AsyncMock())
        await clear_state_retain_anchor(state)
        state.clear.assert_awaited_once()
        state.update_data.assert_awaited_once_with(anchor_id=20)

        message = SimpleNamespace(delete=AsyncMock())
        await delete_user_message(message)
        message.delete.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()