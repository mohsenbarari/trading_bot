import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin import process_invitation_account_name
from bot.states import InvitationCreation


class FakeState:
    def __init__(self, data=None):
        self.data = data or {}
        self.updated = []
        self.states = []

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)

    async def set_state(self, value):
        self.states.append(value)


def make_message(text):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=10),
        bot=SimpleNamespace(delete_message=AsyncMock()),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=71)),
    )


class BotAdminAccountNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_invitation_account_name_handles_invalid_and_valid_names(self):
        state = FakeState({"last_prompt_message_id": 50})
        message = make_message("!")
        await process_invitation_account_name(message, state)
        self.assertIn("نام کاربری نامعتبر", message.answer.await_args.args[0])

        state = FakeState({"last_prompt_message_id": 50})
        message = make_message("ali_user")
        with patch("bot.handlers.admin.normalize_account_name", return_value="normalized"):
            await process_invitation_account_name(message, state)

        self.assertEqual(state.updated[0], {"account_name": "normalized"})
        self.assertEqual(state.states, [InvitationCreation.awaiting_mobile_number])
        self.assertIn("شماره موبایل", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()