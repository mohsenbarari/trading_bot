import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.block_service import BLOCK_STATUS_REASON_CUSTOMER_DELEGATED
from bot.handlers.block_manage import show_blocked_list, start_search, BlockStates


class FakeState:
    def __init__(self):
        self.states = []

    async def set_state(self, value):
        self.states.append(value)


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotBlockManageListSearchEntryTests(unittest.IsolatedAsyncioTestCase):
    normal_status = {"can_block": True, "can_block_now": True, "current_blocked": 0, "max_blocked": 3, "remaining": 3}

    async def test_show_blocked_list_handles_empty_and_renders_list(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        await show_blocked_list(callback, user=None)
        callback.answer.assert_awaited_once_with()

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[])
        ), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=self.normal_status)
        ):
            await show_blocked_list(callback, user=SimpleNamespace(id=5))
        callback.answer.assert_awaited_once_with("لیست خالی است", show_alert=True)

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[{"id": 1, "account_name": "u1"}])
        ), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=self.normal_status)
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await show_blocked_list(callback, user=SimpleNamespace(id=5))
        self.assertIn("کاربران مسدود شده", safe_edit.await_args.args[1])

    async def test_show_blocked_list_rejects_delegated_accounts(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        status = {
            "can_block": False,
            "reason_code": BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
            "reason_message": "سیستم بلاک مشتریان توسط مالک مدیریت می‌شود.",
        }
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.get_blocked_users", new=AsyncMock()) as blocked_mock:
            await show_blocked_list(callback, user=SimpleNamespace(id=5))

        callback.answer.assert_awaited_once_with(status["reason_message"], show_alert=True)
        blocked_mock.assert_not_awaited()

    async def test_start_search_moves_to_search_state(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        state = FakeState()
        await start_search(callback, state, user=None)
        callback.answer.assert_awaited_once_with()

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        state = FakeState()
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=self.normal_status)
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await start_search(callback, state, user=SimpleNamespace(id=5))

        self.assertEqual(state.states, [BlockStates.searching])
        self.assertIn("جستجوی کاربر", safe_edit.await_args.args[1])

    async def test_start_search_rejects_delegated_accounts(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        state = FakeState()
        status = {
            "can_block": False,
            "reason_code": BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
            "reason_message": "سیستم بلاک مشتریان توسط مالک مدیریت می‌شود.",
        }
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await start_search(callback, state, user=SimpleNamespace(id=5))

        callback.answer.assert_awaited_once_with(status["reason_message"], show_alert=True)
        self.assertEqual(state.states, [])
        safe_edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
