import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_address


class FakeState:
    def __init__(self, data):
        self.data = data
        self.cleared = 0

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared += 1


def make_message(text="تهران خیابان آزادی پلاک ۱۰"):
    return SimpleNamespace(
        bot=SimpleNamespace(),
        chat=SimpleNamespace(id=21, type="private"),
        text=text,
        from_user=SimpleNamespace(id=5, username="u", full_name="Full Name"),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=66)),
    )


class BotStartRegistrationAddressTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_registration_address_state_redirects_to_webapp(self):
        state = FakeState({"token": "raw-bearer"})
        message = make_message(text="کوتاه")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor, patch(
            "bot.handlers.start.public_webapp_url_for_links", return_value="https://app.example"
        ):
            await handle_address(message, state)

        self.assertEqual(state.cleared, 1)
        self.assertIn("این مسیر ثبت‌نام در ربات فعال نیست", message.answer.await_args.args[0])
        self.assertNotIn("raw-bearer", message.answer.await_args.args[0])
        self.assertNotIn("register?token=", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(21, 66)

    async def test_short_code_registration_address_state_uses_canonical_web_link(self):
        state = FakeState({"registration_invitation_short_code": "C0DE1234"})
        message = make_message()

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.set_anchor"
        ), patch(
            "bot.handlers.start.public_webapp_url_for_links", return_value="https://app.example"
        ):
            await handle_address(message, state)

        self.assertIn("https://app.example/i/C0DE1234", message.answer.await_args.args[0])
        self.assertNotIn("register?token=", message.answer.await_args.args[0])

    async def test_legacy_registration_address_state_without_token_still_stops_bot_registration(self):
        state = FakeState({})
        message = make_message()

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_address(message, state)

        self.assertEqual(state.cleared, 1)
        self.assertIn("برای تکمیل ثبت‌نام از وب‌اپ استفاده کنید", message.answer.await_args.args[0])
        self.assertNotIn("register?token=", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(21, 66)


if __name__ == "__main__":
    unittest.main()
