import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_contact
from bot.states import Registration


class FakeState:
    def __init__(self, data):
        self.data = data
        self.cleared = 0
        self.updated = []
        self.states = []

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared += 1

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)

    async def set_state(self, value):
        self.states.append(value)


def make_message(phone, contact_user_id=10, from_user_id=10):
    return SimpleNamespace(
        bot=SimpleNamespace(),
        chat=SimpleNamespace(id=20),
        contact=SimpleNamespace(phone_number=phone, user_id=contact_user_id),
        from_user=SimpleNamespace(id=from_user_id),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=91)),
    )


class BotStartRegistrationContactTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_contact_rejects_mismatched_phone_or_user(self):
        state = FakeState({"mobile_number": "09123334444", "token": "tok"})
        message = make_message("+989122223333", contact_user_id=99)

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()):
            await handle_contact(message, state)

        self.assertEqual(state.cleared, 1)
        self.assertIn("مطابقت ندارد", message.answer.await_args.args[0])

    async def test_handle_contact_marks_phone_verified_and_moves_to_address(self):
        state = FakeState({"mobile_number": "09123334444", "token": "tok"})
        message = make_message("9123334444")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_contact(message, state)

        self.assertEqual(state.updated, [{"phone_verified": True}])
        self.assertEqual(state.states, [Registration.awaiting_address])
        self.assertIn("شماره تماس تایید شد", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(20, 91)


if __name__ == "__main__":
    unittest.main()