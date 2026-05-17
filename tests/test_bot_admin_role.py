import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from bot.handlers.admin import process_invitation_role
from core.enums import UserRole


class FakeState:
    def __init__(self, data=None):
        self.data = data or {}
        self.cleared = 0

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared += 1


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback(data="set_role_STANDARD"):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock(), answer=AsyncMock()),
    )


class BotAdminRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_invitation_role_checks_access_and_invalid_role(self):
        callback = make_callback()
        await process_invitation_role(callback, FakeState(), user=None, bot=SimpleNamespace())
        callback.answer.assert_awaited_once()

        callback = make_callback(data="bad")
        state = FakeState({"last_prompt_message_id": 10})
        await process_invitation_role(callback, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), bot=SimpleNamespace())
        self.assertIn("نامعتبر", callback.answer.await_args.args[0])

        callback = make_callback(data="set_role_POLICE")
        state = FakeState({"last_prompt_message_id": 10})
        await process_invitation_role(callback, state, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER), bot=SimpleNamespace())
        self.assertIn("مجاز نیست", callback.answer.await_args.args[0])

    async def test_process_invitation_role_handles_missing_data_success_and_existing_active_link(self):
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="botname")))
        callback = make_callback(data="set_role_STANDARD")
        state = FakeState({})
        with patch("bot.handlers.admin._return_to_admin_panel", new=AsyncMock()) as return_panel:
            await process_invitation_role(callback, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), bot=bot)
        callback.message.answer.assert_awaited_once()
        return_panel.assert_awaited_once()

        callback = make_callback(data="set_role_STANDARD")
        state = FakeState({"account_name": "acc", "mobile_number": "09123456789", "last_prompt_message_id": 10})
        with patch("bot.handlers.admin.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.admin.create_invitation", new=AsyncMock(return_value={"token": "tok"})
        ), patch("bot.handlers.admin._return_to_admin_panel", new=AsyncMock()):
            await process_invitation_role(callback, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), bot=bot)
        self.assertIn("لینک دعوت با موفقیت", callback.message.answer.await_args.args[0])

        callback = make_callback(data="set_role_STANDARD")
        state = FakeState({"account_name": "acc", "mobile_number": "09123456789"})
        with patch("bot.handlers.admin.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.admin.create_invitation", new=AsyncMock(side_effect=HTTPException(status_code=400, detail="EXISTING_ACTIVE_LINK::acc::tok"))
        ), patch("bot.handlers.admin._return_to_admin_panel", new=AsyncMock()):
            await process_invitation_role(callback, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), bot=bot)
        self.assertIn("لینک قبلی هنوز فعال است", callback.message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()