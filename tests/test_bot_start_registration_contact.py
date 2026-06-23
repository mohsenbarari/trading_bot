import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_contact


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


def make_message(phone="9123334444", contact_user_id=10, from_user_id=10):
    return SimpleNamespace(
        bot=SimpleNamespace(),
        chat=SimpleNamespace(id=20),
        contact=SimpleNamespace(phone_number=phone, user_id=contact_user_id),
        from_user=SimpleNamespace(id=from_user_id),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=91)),
    )


class BotStartRegistrationContactTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_registration_contact_state_redirects_to_webapp(self):
        state = FakeState({"mobile_number": "09123334444", "token": "tok"})
        message = make_message(contact_user_id=99)

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_contact(message, state)

        self.assertEqual(state.cleared, 1)
        self.assertEqual(state.updated, [])
        self.assertEqual(state.states, [])
        self.assertIn("ثبت‌نام از طریق وب‌اپ انجام می‌شود", message.answer.await_args.args[0])
        self.assertIn("register?token=tok", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(20, 91)


if __name__ == "__main__":
    unittest.main()
