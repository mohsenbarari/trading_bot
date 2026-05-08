import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import start_invitation_creation, start_invitation_creation_inline
from core.enums import UserRole
from bot.states import InvitationCreation


class FakeState:
    def __init__(self):
        self.states = []
        self.updated = []

    async def set_state(self, value):
        self.states.append(value)

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)


class BotAdminStartCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_invitation_creation_requires_super_admin_and_starts_fsm(self):
        state = FakeState()
        message = SimpleNamespace(answer=AsyncMock(return_value=SimpleNamespace(message_id=91)))
        await start_invitation_creation(message, state, user=None)
        message.answer.assert_not_awaited()

        await start_invitation_creation(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(state.states, [InvitationCreation.awaiting_account_name])
        self.assertIn("نام کاربری", message.answer.await_args.args[0])
        self.assertEqual(state.updated[-1], {"last_prompt_message_id": 91})

    async def test_start_invitation_creation_inline_requires_super_admin_and_starts_fsm(self):
        state = FakeState()
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock(), message_id=51))
        await start_invitation_creation_inline(callback, state, user=None)
        callback.answer.assert_awaited_once()

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock(), message_id=52))
        state = FakeState()
        await start_invitation_creation_inline(callback, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(state.states, [InvitationCreation.awaiting_account_name])
        callback.message.edit_text.assert_awaited_once()
        self.assertEqual(state.updated[-1], {"last_prompt_message_id": 52})


if __name__ == "__main__":
    unittest.main()