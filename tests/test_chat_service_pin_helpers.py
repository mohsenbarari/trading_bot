import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType
from core.services import chat_service
from models.chat import Chat
from models.message import Message


class _ScalarResult:
    def __init__(self, first_value=None):
        self._first_value = first_value

    def first(self):
        return self._first_value


class _ExecuteResult:
    def __init__(self, *, rows=None, scalar_first=None, scalar_one=None):
        self._rows = rows or []
        self._scalar_first = scalar_first
        self._scalar_one = scalar_one

    def all(self):
        return self._rows

    def scalars(self):
        return _ScalarResult(self._scalar_first)

    def scalar_one_or_none(self):
        return self._scalar_one


class ChatServicePinHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_message_loader_handles_empty_deleted_and_valid_messages(self):
        db = SimpleNamespace()
        chat = SimpleNamespace(pinned_message_id=None)
        self.assertIsNone(await chat_service.get_pinned_message_for_chat(db, chat))

        chat.pinned_message_id = 55
        with patch.object(chat_service, "reload_direct_message", new=AsyncMock(return_value=None)) as reload_message:
            self.assertIsNone(await chat_service.get_pinned_message_for_chat(db, chat))
        reload_message.assert_awaited_once_with(db, 55, include_sender=True)

        deleted_message = SimpleNamespace(is_deleted=True)
        with patch.object(chat_service, "reload_direct_message", new=AsyncMock(return_value=deleted_message)):
            self.assertIsNone(await chat_service.get_pinned_message_for_chat(db, chat))

        message = SimpleNamespace(is_deleted=False)
        with patch.object(chat_service, "reload_direct_message", new=AsyncMock(return_value=message)):
            self.assertIs(await chat_service.get_pinned_message_for_chat(db, chat), message)

    async def test_get_pinnable_message_guards_direct_and_room_membership(self):
        missing_db = SimpleNamespace(get=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException) as missing_error:
            await chat_service.get_pinnable_message(missing_db, message_id=1, actor_id=10)
        self.assertEqual(missing_error.exception.status_code, 404)

        deleted_message = SimpleNamespace(id=2, is_deleted=True)
        deleted_db = SimpleNamespace(get=AsyncMock(return_value=deleted_message))
        with self.assertRaises(HTTPException) as deleted_error:
            await chat_service.get_pinnable_message(deleted_db, message_id=2, actor_id=10)
        self.assertEqual(deleted_error.exception.status_code, 404)

        foreign_direct_message = SimpleNamespace(id=3, is_deleted=False, chat_id=None, sender_id=11, receiver_id=12)
        foreign_db = SimpleNamespace(get=AsyncMock(return_value=foreign_direct_message))
        with self.assertRaises(HTTPException) as foreign_error:
            await chat_service.get_pinnable_message(foreign_db, message_id=3, actor_id=10)
        self.assertEqual(foreign_error.exception.status_code, 403)

        orphan_direct_message = SimpleNamespace(id=4, is_deleted=False, chat_id=None, sender_id=10, receiver_id=12)
        orphan_db = SimpleNamespace(get=AsyncMock(side_effect=[orphan_direct_message, None]))
        with patch.object(chat_service, "ensure_direct_message_chat_link", new=AsyncMock()) as ensure_link:
            with self.assertRaises(HTTPException) as orphan_error:
                await chat_service.get_pinnable_message(orphan_db, message_id=4, actor_id=10)
        ensure_link.assert_awaited_once_with(orphan_db, orphan_direct_message)
        self.assertEqual(orphan_error.exception.status_code, 404)

        direct_chat = SimpleNamespace(id=40, type=ChatType.DIRECT, is_deleted=False)
        direct_message = SimpleNamespace(id=5, is_deleted=False, chat_id=40, sender_id=10, receiver_id=12)
        direct_db = SimpleNamespace(get=AsyncMock(side_effect=[direct_message, direct_chat]))
        message, chat = await chat_service.get_pinnable_message(direct_db, message_id=5, actor_id=12)
        self.assertIs(message, direct_message)
        self.assertIs(chat, direct_chat)

        foreign_direct_db = SimpleNamespace(get=AsyncMock(side_effect=[direct_message, direct_chat]))
        with self.assertRaises(HTTPException) as foreign_direct_error:
            await chat_service.get_pinnable_message(foreign_direct_db, message_id=5, actor_id=99)
        self.assertEqual(foreign_direct_error.exception.status_code, 403)

        room_message = SimpleNamespace(id=6, is_deleted=False, chat_id=60, sender_id=20, receiver_id=20)
        room_chat = SimpleNamespace(id=60, type=ChatType.GROUP, is_deleted=False)
        no_member_db = SimpleNamespace(
            get=AsyncMock(side_effect=[room_message, room_chat]),
            execute=AsyncMock(return_value=_ExecuteResult(scalar_first=None)),
        )
        with self.assertRaises(HTTPException) as no_member_error:
            await chat_service.get_pinnable_message(no_member_db, message_id=6, actor_id=10)
        self.assertEqual(no_member_error.exception.status_code, 403)

        member = SimpleNamespace(role=ChatMemberRole.MEMBER)
        member_db = SimpleNamespace(
            get=AsyncMock(side_effect=[room_message, room_chat]),
            execute=AsyncMock(return_value=_ExecuteResult(scalar_first=member)),
        )
        with self.assertRaises(HTTPException) as member_error:
            await chat_service.get_pinnable_message(member_db, message_id=6, actor_id=10)
        self.assertEqual(member_error.exception.status_code, 403)

        admin_member = SimpleNamespace(role=ChatMemberRole.ADMIN)
        admin_db = SimpleNamespace(
            get=AsyncMock(side_effect=[room_message, room_chat]),
            execute=AsyncMock(return_value=_ExecuteResult(scalar_first=admin_member)),
        )
        message, chat = await chat_service.get_pinnable_message(admin_db, message_id=6, actor_id=10)
        self.assertIs(message, room_message)
        self.assertIs(chat, room_chat)

    async def test_apply_message_pin_state_pins_unpins_and_rejects_wrong_unpin(self):
        message = SimpleNamespace(id=77, is_deleted=False, chat_id=700, sender_id=10, receiver_id=20)
        chat = SimpleNamespace(
            id=700,
            type=ChatType.DIRECT,
            is_deleted=False,
            pinned_message_id=None,
            pinned_message_at=None,
            pinned_message_by_id=None,
            updated_at=None,
        )
        db = SimpleNamespace(get=AsyncMock(side_effect=[message, chat]), execute=AsyncMock(), commit=AsyncMock())

        with patch.object(chat_service, "get_pinned_message_for_chat", new=AsyncMock(return_value=message)):
            pinned_chat, pinned_message = await chat_service.apply_message_pin_state(
                db,
                message_id=77,
                actor_id=10,
                pinned=True,
            )

        self.assertIs(pinned_chat, chat)
        self.assertIs(pinned_message, message)
        self.assertEqual(chat.pinned_message_id, 77)
        self.assertEqual(chat.pinned_message_by_id, 10)
        db.commit.assert_awaited_once()

        db.commit.reset_mock()
        db.get = AsyncMock(side_effect=[message, chat])
        unpinned_chat, unpinned_message = await chat_service.apply_message_pin_state(
            db,
            message_id=77,
            actor_id=10,
            pinned=False,
        )
        self.assertIs(unpinned_chat, chat)
        self.assertIsNone(unpinned_message)
        self.assertIsNone(chat.pinned_message_id)
        db.commit.assert_awaited_once()

        chat.pinned_message_id = 999
        db.get = AsyncMock(side_effect=[message, chat])
        with self.assertRaises(HTTPException) as wrong_unpin_error:
            await chat_service.apply_message_pin_state(db, message_id=77, actor_id=10, pinned=False)
        self.assertEqual(wrong_unpin_error.exception.status_code, 400)

    async def test_pin_order_helpers_calculate_next_order_and_reorder_pinned_rows(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_ExecuteResult(scalar_one=4)), commit=AsyncMock())
        self.assertEqual(await chat_service.get_next_chat_member_pin_order(db, user_id=10), 5)

        db.execute = AsyncMock(return_value=_ExecuteResult(scalar_one=None))
        self.assertEqual(await chat_service.get_next_chat_member_pin_order(db, user_id=10), 1)

        with self.assertRaises(HTTPException) as invalid_error:
            await chat_service.reorder_chat_member_pin_order(db, user_id=10, chat_id=1, direction="sideways")
        self.assertEqual(invalid_error.exception.status_code, 400)

        first = SimpleNamespace(id=1, chat_id=101, pin_order=3, updated_at=None)
        second = SimpleNamespace(id=2, chat_id=102, pin_order=2, updated_at=None)
        third = SimpleNamespace(id=3, chat_id=103, pin_order=1, updated_at=None)
        rows = [(first, SimpleNamespace()), (second, SimpleNamespace()), (third, SimpleNamespace())]
        db.execute = AsyncMock(return_value=_ExecuteResult(rows=rows))
        db.commit = AsyncMock()

        moved = await chat_service.reorder_chat_member_pin_order(db, user_id=10, chat_id=102, direction="up")
        self.assertIs(moved, second)
        self.assertEqual([first.pin_order, second.pin_order, third.pin_order], [2, 3, 1])
        db.commit.assert_awaited_once()

        db.commit.reset_mock()
        db.execute = AsyncMock(return_value=_ExecuteResult(rows=[(second, SimpleNamespace()), (first, SimpleNamespace()), (third, SimpleNamespace())]))
        moved = await chat_service.reorder_chat_member_pin_order(db, user_id=10, chat_id=102, direction="down")
        self.assertIs(moved, second)
        self.assertEqual([first.pin_order, second.pin_order, third.pin_order], [3, 2, 1])
        db.commit.assert_awaited_once()

        db.execute = AsyncMock(return_value=_ExecuteResult(rows=[]))
        with self.assertRaises(HTTPException) as missing_error:
            await chat_service.reorder_chat_member_pin_order(db, user_id=10, chat_id=999, direction="up")
        self.assertEqual(missing_error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
