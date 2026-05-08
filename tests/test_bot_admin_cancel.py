import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin import cancel_invitation_creation


class FakeState:
    def __init__(self, data=None):
        self.data = data or {}
        self.cleared = 0

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared += 1


class BotAdminCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_invitation_creation_clears_state_and_returns_to_panel(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(delete=AsyncMock(), answer=AsyncMock()))
        state = FakeState({"last_prompt_message_id": 10})

        with patch("bot.handlers.admin._return_to_admin_panel", new=AsyncMock()) as return_panel:
            await cancel_invitation_creation(callback, state, bot=SimpleNamespace())

        self.assertEqual(state.cleared, 1)
        callback.message.delete.assert_awaited_once()
        callback.message.answer.assert_awaited_once_with("عملیات لغو شد.")
        return_panel.assert_awaited_once()
        callback.answer.assert_awaited_once_with("لغو شد")


if __name__ == "__main__":
    unittest.main()