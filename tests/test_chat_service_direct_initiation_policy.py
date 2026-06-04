import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from core.enums import MessageType
from core.services import chat_service
from models.user import UserRole


class ChatServiceDirectInitiationPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_direct_message_send_allows_accountant_when_legacy_conversation_exists(self):
        sender = SimpleNamespace(id=5)
        receiver = SimpleNamespace(id=6, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(id=9)),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
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
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
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

    async def test_prepare_direct_message_send_allows_customer_to_owner(self):
        sender = SimpleNamespace(id=9, role=UserRole.STANDARD)
        receiver = SimpleNamespace(id=7, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[SimpleNamespace(owner_user_id=7), None]),
        ), patch(
            "core.services.chat_service.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[7, 11, 12, 40]),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=7,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

    async def test_prepare_direct_message_send_denies_customer_to_outside_user(self):
        sender = SimpleNamespace(id=9, role=UserRole.STANDARD)
        receiver = SimpleNamespace(id=99, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[SimpleNamespace(owner_user_id=7), None]),
        ), patch(
            "core.services.chat_service.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[7, 11, 12, 40]),
        ), patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_existing_direct_chat",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await chat_service.prepare_direct_message_send(
                    db,
                    sender=sender,
                    receiver_id=99,
                    content="hello",
                    message_type=MessageType.TEXT,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "کاربر مشتری در این فاز اجازه شروع گفتگوی مستقیم با این کاربر را ندارد")

    async def test_prepare_direct_message_send_denies_customer_to_outside_user_even_with_existing_thread(self):
        sender = SimpleNamespace(id=9, role=UserRole.STANDARD)
        receiver = SimpleNamespace(id=99, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[SimpleNamespace(owner_user_id=7), None]),
        ), patch(
            "core.services.chat_service.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[7, 11, 12, 40]),
        ), patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=SimpleNamespace(id=77)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await chat_service.prepare_direct_message_send(
                    db,
                    sender=sender,
                    receiver_id=99,
                    content="hello",
                    message_type=MessageType.TEXT,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "کاربر مشتری در این فاز اجازه شروع گفتگوی مستقیم با این کاربر را ندارد")

    async def test_prepare_direct_message_send_allows_customer_to_shared_group_accountant(self):
        sender = SimpleNamespace(id=9, role=UserRole.STANDARD)
        receiver = SimpleNamespace(id=44, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[SimpleNamespace(owner_user_id=7), None]),
        ), patch(
            "core.services.chat_service.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[7, 11, 12, 40, 44]),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=44,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

    async def test_prepare_direct_message_send_allows_owner_to_customer(self):
        sender = SimpleNamespace(id=7, role=UserRole.STANDARD)
        receiver = SimpleNamespace(id=9, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, SimpleNamespace(owner_user_id=7)]),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=9,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

    async def test_prepare_direct_message_send_allows_same_owner_accountant_to_customer(self):
        sender = SimpleNamespace(id=11, role=UserRole.STANDARD)
        receiver = SimpleNamespace(id=9, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=7)),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, SimpleNamespace(owner_user_id=7)]),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=9,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

    async def test_prepare_direct_message_send_denies_unrelated_accountant_to_customer(self):
        sender = SimpleNamespace(id=11, role=UserRole.STANDARD)
        receiver = SimpleNamespace(id=9, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=88)),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, SimpleNamespace(owner_user_id=7)]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await chat_service.prepare_direct_message_send(
                    db,
                    sender=sender,
                    receiver_id=9,
                    content="hello",
                    message_type=MessageType.TEXT,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "کاربر مشتری در این فاز اجازه شروع گفتگوی مستقیم با این کاربر را ندارد")

    async def test_prepare_direct_message_send_allows_middle_manager_to_own_customer(self):
        sender = SimpleNamespace(id=41, role=UserRole.MIDDLE_MANAGER)
        receiver = SimpleNamespace(id=9, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, SimpleNamespace(owner_user_id=41)]),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=9,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

    async def test_prepare_direct_message_send_allows_super_admin_to_own_customer(self):
        sender = SimpleNamespace(id=40, role=UserRole.SUPER_ADMIN)
        receiver = SimpleNamespace(id=9, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, SimpleNamespace(owner_user_id=40)]),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=9,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

    async def test_prepare_direct_message_send_denies_super_admin_to_unowned_customer(self):
        sender = SimpleNamespace(id=40, role=UserRole.SUPER_ADMIN)
        receiver = SimpleNamespace(id=9, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, SimpleNamespace(owner_user_id=7)]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await chat_service.prepare_direct_message_send(
                    db,
                    sender=sender,
                    receiver_id=9,
                    content="hello",
                    message_type=MessageType.TEXT,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "کاربر مشتری در این فاز اجازه شروع گفتگوی مستقیم با این کاربر را ندارد")

    async def test_prepare_direct_message_send_denies_middle_manager_to_unowned_customer(self):
        sender = SimpleNamespace(id=41, role=UserRole.MIDDLE_MANAGER)
        receiver = SimpleNamespace(id=9, is_deleted=False)
        db = SimpleNamespace(get=AsyncMock(return_value=receiver))

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, SimpleNamespace(owner_user_id=7)]),
        ), patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service.get_existing_direct_chat",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await chat_service.prepare_direct_message_send(
                    db,
                    sender=sender,
                    receiver_id=9,
                    content="hello",
                    message_type=MessageType.TEXT,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "کاربر مشتری در این فاز اجازه شروع گفتگوی مستقیم با این کاربر را ندارد")


if __name__ == "__main__":
    unittest.main()
