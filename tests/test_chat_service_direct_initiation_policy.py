import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import MessageType
from core.services import chat_service


class ChatServiceDirectInitiationPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_direct_message_send_allows_accountant_when_legacy_conversation_exists(self):
        sender = SimpleNamespace(id=5)
        receiver = SimpleNamespace(id=6, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(id=9)),
        ), patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=SimpleNamespace(id=77)),
        ), patch(
            "core.services.chat_service.get_existing_direct_chat",
            new=AsyncMock(return_value=None),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=6,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

    async def test_prepare_direct_message_send_allows_accountant_when_generic_direct_chat_exists(self):
        sender = SimpleNamespace(id=5)
        receiver = SimpleNamespace(id=6, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(id=9)),
        ), patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_existing_direct_chat",
            new=AsyncMock(return_value=SimpleNamespace(id=88)),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=6,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")


if __name__ == "__main__":
    unittest.main()