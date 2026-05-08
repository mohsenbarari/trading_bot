import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import handle_search_query


class FakeState:
    def __init__(self):
        self.cleared = 0

    async def clear(self):
        self.cleared += 1


def make_message(text):
    return SimpleNamespace(text=text, answer=AsyncMock())


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotBlockManageSearchQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_search_query_validates_length_and_handles_empty_and_found_results(self):
        state = FakeState()
        message = make_message("a")
        await handle_search_query(message, state, user=SimpleNamespace(id=5))
        self.assertIn("حداقل 2 کاراکتر", message.answer.await_args.args[0])

        state = FakeState()
        message = make_message("ali")
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.search_users_for_block", new=AsyncMock(return_value=[])
        ):
            await handle_search_query(message, state, user=SimpleNamespace(id=5))
        self.assertIn("کاربری یافت نشد", message.answer.await_args.args[0])

        state = FakeState()
        message = make_message("ali")
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.search_users_for_block", new=AsyncMock(return_value=[{"id": 1, "account_name": "u1", "is_blocked": False}])
        ):
            await handle_search_query(message, state, user=SimpleNamespace(id=5))
        self.assertEqual(state.cleared, 1)
        self.assertIn("نتایج جستجو", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()