import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.block_service import BLOCK_STATUS_REASON_CUSTOMER_DELEGATED
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
    normal_status = {"can_block": True, "can_block_now": True, "current_blocked": 0, "max_blocked": 3, "remaining": 3}

    async def test_handle_search_query_validates_length_and_handles_empty_and_found_results(self):
        state = FakeState()
        message = make_message("a")
        await handle_search_query(message, state, user=SimpleNamespace(id=5))
        self.assertIn("حداقل 2 کاراکتر", message.answer.await_args.args[0])

        state = FakeState()
        message = make_message("ali")
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=self.normal_status)
        ), patch(
            "bot.handlers.block_manage.search_users_for_block", new=AsyncMock(return_value=[])
        ):
            await handle_search_query(message, state, user=SimpleNamespace(id=5))
        self.assertIn("کاربری یافت نشد", message.answer.await_args.args[0])

        state = FakeState()
        message = make_message("ali")
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=self.normal_status)
        ), patch(
            "bot.handlers.block_manage.search_users_for_block", new=AsyncMock(return_value=[{"id": 1, "account_name": "u1", "is_blocked": False}])
        ):
            await handle_search_query(message, state, user=SimpleNamespace(id=5))
        self.assertEqual(state.cleared, 1)
        self.assertIn("نتایج جستجو", message.answer.await_args.args[0])

    async def test_handle_search_query_rejects_delegated_accounts(self):
        state = FakeState()
        message = make_message("ali")
        status = {
            "can_block": False,
            "reason_code": BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
            "reason_message": "سیستم بلاک مشتریان توسط مالک مدیریت می‌شود.",
        }
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.search_users_for_block", new=AsyncMock()) as search_mock:
            await handle_search_query(message, state, user=SimpleNamespace(id=5))

        self.assertEqual(state.cleared, 1)
        self.assertIn(status["reason_message"], message.answer.await_args.args[0])
        search_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
