import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_user_delete_confirm, handle_user_delete_request
from core.enums import UserRole


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


class BotAdminUsersDeleteFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_handlers_ignore_unauthorized_and_reject_protected_targets(self):
        callback = SimpleNamespace(
            data="user_ask_delete_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        await handle_user_delete_request(callback, user=None)
        callback.answer.assert_not_awaited()

        protected_user = SimpleNamespace(id=9, role=UserRole.SUPER_ADMIN)
        callback = SimpleNamespace(
            data="user_ask_delete_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_user)):
            await handle_user_delete_request(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        denied_confirm = SimpleNamespace(
            data="user_delete_confirm_9",
            answer=AsyncMock(),
        )
        await handle_user_delete_confirm(denied_confirm, user=None, state=SimpleNamespace())
        denied_confirm.answer.assert_not_awaited()

    async def test_handle_user_delete_request_shows_confirmation(self):
        callback = SimpleNamespace(
            data="user_ask_delete_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, role=UserRole.STANDARD))), patch(
            "bot.handlers.admin_users.get_user_delete_confirm_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_user_delete_request(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(9)
        self.assertIn("آیا از حذف این کاربر", callback.message.edit_text.await_args.args[0])
        callback.answer.assert_awaited_once()

        callback = SimpleNamespace(data="user_ask_delete_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_delete_request(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

    async def test_handle_user_delete_confirm_covers_success_error_and_missing_user(self):
        actor = SimpleNamespace(role=UserRole.SUPER_ADMIN)
        target_user = SimpleNamespace(id=9, is_deleted=False, role=UserRole.STANDARD)
        callback = SimpleNamespace(
            data="user_delete_confirm_9",
            bot=SimpleNamespace(),
            message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=77),
            answer=AsyncMock(),
        )
        state = SimpleNamespace()
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.delete_user_account", new=AsyncMock()
        ) as delete_account_mock, patch("bot.handlers.admin_users.show_users_list", new=AsyncMock()) as show_mock:
            await handle_user_delete_confirm(callback, user=actor, state=state)
        delete_account_mock.assert_awaited_once()
        callback.answer.assert_awaited_once_with("✅ کاربر با موفقیت حذف شد.")
        show_mock.assert_awaited_once_with(callback.bot, 1, state, page=1, message_id_to_edit=77, actor=actor)

        callback = SimpleNamespace(data="user_delete_confirm_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.delete_user_account", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await handle_user_delete_confirm(callback, user=actor, state=state)
        self.assertIn("boom", callback.answer.await_args.args[0])
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

        protected_target = SimpleNamespace(id=9, is_deleted=False, role=UserRole.SUPER_ADMIN)
        denied_callback = SimpleNamespace(data="user_delete_confirm_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_target)):
            await handle_user_delete_confirm(denied_callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER), state=state)
        denied_callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        callback = SimpleNamespace(
            data="user_delete_confirm_9",
            bot=SimpleNamespace(),
            message=SimpleNamespace(chat=SimpleNamespace(id=2), message_id=88),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, is_deleted=True, role=UserRole.STANDARD))), patch(
            "bot.handlers.admin_users.show_users_list", new=AsyncMock()
        ) as show_mock:
            await handle_user_delete_confirm(callback, user=actor, state=state)
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد یا قبلاً حذف شده است.", show_alert=True)
        show_mock.assert_awaited_once_with(callback.bot, 2, state, page=1, message_id_to_edit=88, actor=actor)


if __name__ == "__main__":
    unittest.main()