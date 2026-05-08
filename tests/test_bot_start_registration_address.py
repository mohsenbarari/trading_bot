import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_address


class FakeState:
    def __init__(self, token):
        self.token = token
        self.cleared = 0

    async def get_data(self):
        return {"token": self.token}

    async def clear(self):
        self.cleared += 1


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, invitation):
        self.invitation = invitation
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        return FakeExecuteResult(self.invitation)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_message(text="تهران خیابان آزادی پلاک ۱۰", user_id=5, username="u", full_name="Full Name"):
    return SimpleNamespace(
        bot=SimpleNamespace(),
        chat=SimpleNamespace(id=21),
        text=text,
        from_user=SimpleNamespace(id=user_id, username=username, full_name=full_name),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=66)),
    )


class BotStartRegistrationAddressTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_address_rejects_short_address(self):
        state = FakeState("tok")
        message = make_message(text="کوتاه")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()):
            await handle_address(message, state)

        self.assertIn("آدرس وارد شده کوتاه است", message.answer.await_args.args[0])

    async def test_handle_address_handles_invalid_invitation(self):
        state = FakeState("tok")
        message = make_message()

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(None)),
        ):
            await handle_address(message, state)

        self.assertEqual(state.cleared, 1)
        self.assertIn("دیگر معتبر نیست", message.answer.await_args.args[0])

    async def test_handle_address_creates_user_and_marks_invitation_used(self):
        invitation = SimpleNamespace(
            token="tok",
            account_name="acc",
            mobile_number="09120001122",
            role="standard",
            is_used=False,
        )
        session = FakeSession(invitation)
        state = FakeState("tok")
        message = make_message()

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch("bot.handlers.start.get_persistent_menu_keyboard", return_value="menu"), patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor, patch("bot.handlers.start.settings", SimpleNamespace(channel_invite_link="https://t.me/join", frontend_url="https://app")):
            await handle_address(message, state)

        self.assertEqual(state.cleared, 1)
        self.assertTrue(invitation.is_used)
        self.assertEqual(session.commits, 1)
        self.assertEqual(len(session.added), 1)
        new_user = session.added[0]
        self.assertEqual(new_user.telegram_id, 5)
        self.assertEqual(new_user.account_name, "acc")
        self.assertIn("عضویت در کانال معاملات", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(21, 66)


if __name__ == "__main__":
    unittest.main()