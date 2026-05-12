import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_start_with_token
from bot.states import Registration


class FakeState:
    def __init__(self):
        self.updated = []
        self.states = []

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)

    async def set_state(self, value):
        self.states.append(value)


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, value):
        self.value = value

    async def execute(self, stmt):
        return FakeExecuteResult(self.value)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotStartInvitationEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_token_returns_panel_for_existing_user(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=11),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=88)),
        )
        user = SimpleNamespace(role="standard")
        state = FakeState()
        command = SimpleNamespace(args="invite-token")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.start.get_persistent_menu_keyboard", return_value="menu"
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_with_token(message, command, state, user)

        delete_anchor.assert_awaited_once()
        self.assertIn("قبلاً", message.answer.await_args.args[0])
        self.assertIn("پنل", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(11, 88)

    async def test_handle_start_with_token_handles_invalid_and_valid_invitation(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=12),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=34)),
        )
        state = FakeState()
        command = SimpleNamespace(args="invite-token")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(None)),
        ):
            await handle_start_with_token(message, command, state, user=None)

        self.assertIn("نامعتبر یا منقضی", message.answer.await_args.args[0])

        invitation = SimpleNamespace(token="invite-token", mobile_number="09120000000", is_used=False)
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=13),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=77)),
        )
        state = FakeState()
        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(invitation)),
        ), patch("bot.handlers.start.get_share_contact_keyboard", return_value="share"), patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_start_with_token(message, command, state, user=None)

        self.assertEqual(state.updated, [{"token": "invite-token", "mobile_number": "09120000000"}])
        self.assertEqual(state.states, [Registration.awaiting_contact])
        self.assertIn("لینک دعوت شما معتبر است", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(13, 77)

    async def test_handle_start_with_token_redirects_accountant_invitation_to_web_only_flow(self):
        invitation = SimpleNamespace(token="ACCT-token", mobile_number="09120000000", is_used=False)
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=13),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=77)),
        )
        state = FakeState()
        command = SimpleNamespace(args="ACCT-token")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(invitation)),
        ), patch(
            "bot.handlers.start.get_pending_accountant_relation_by_invitation_token",
            new=AsyncMock(return_value=SimpleNamespace(id=1)),
        ), patch(
            "bot.handlers.start.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            await handle_start_with_token(message, command, state, user=None)

        self.assertEqual(state.updated, [])
        self.assertEqual(state.states, [])
        self.assertIn("فقط از طریق وب‌اپ", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()