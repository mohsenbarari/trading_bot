import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin import process_invitation_mobile
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
        answer=AsyncMock(return_value=SimpleNamespace(message_id=72)),
    )


class BotAdminMobileTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_invitation_mobile_handles_invalid_and_valid_numbers(self):
        state = FakeState({"last_prompt_message_id": 50})
        message = make_message("123")
        await process_invitation_mobile(message, state)
        self.assertIn("شماره موبایل نامعتبر", message.answer.await_args.args[0])

        state = FakeState({"last_prompt_message_id": 50})
        message = make_message("۰۹۱۲۳۴۵۶۷۸۹")
        with patch("bot.handlers.admin.normalize_persian_numerals", return_value="09123456789"):
            await process_invitation_mobile(message, state)

        self.assertEqual(state.updated[0], {"mobile_number": "09123456789"})
        self.assertEqual(state.states, [InvitationCreation.awaiting_role])
        self.assertIn("نقش", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()