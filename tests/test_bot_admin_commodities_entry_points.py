import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import handle_back_to_list, handle_manage_aliases, handle_manage_commodities


class BotAdminCommoditiesEntryPointTests(unittest.IsolatedAsyncioTestCase):
    async def test_manage_entry_points_delegate_to_view_helpers(self):
        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=1))
        state = SimpleNamespace()
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock:
            await handle_manage_commodities(message, user=SimpleNamespace(id=1), state=state)
        delete_mock.assert_awaited_once_with(message)
        show_list_mock.assert_awaited_once_with(message.bot, 1, unittest.mock.ANY, state)

        query = SimpleNamespace(bot=SimpleNamespace(), message=SimpleNamespace(chat=SimpleNamespace(id=2)), data="comm_back_to_list")
        with patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock:
            await handle_back_to_list(query, user=SimpleNamespace(id=1), state=state)
        clear_mock.assert_awaited_once_with(state)
        show_list_mock.assert_awaited_once_with(query.bot, 2, unittest.mock.ANY, state)

        query = SimpleNamespace(bot=SimpleNamespace(), message=SimpleNamespace(chat=SimpleNamespace(id=3)), data="comm_manage_aliases_9")
        with patch("bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()) as show_aliases_mock:
            await handle_manage_aliases(query, user=SimpleNamespace(id=1), state=state)
        show_aliases_mock.assert_awaited_once_with(query.bot, 3, unittest.mock.ANY, state, 9)


if __name__ == "__main__":
    unittest.main()