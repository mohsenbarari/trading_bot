import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models.chat import Chat

from core.services.chat_service import (
    apply_direct_message_delete,
    apply_direct_message_edit,
    apply_direct_message_reaction_toggle,
)


class ChatServiceApplyMutationTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_direct_message_edit_routes_through_guard_update_and_persist(self):
        now = datetime(2026, 5, 7, 23, 40, tzinfo=timezone.utc)
        message = SimpleNamespace(id=11)
        persisted = SimpleNamespace(id=11, content="edited")
        db = object()

        with patch("core.services.chat_service.datetime") as datetime_mock, patch(
            "core.services.chat_service.get_editable_direct_message",
            new=AsyncMock(return_value=message),
        ) as guard_mock, patch(
            "core.services.chat_service.update_direct_message_content"
        ) as update_mock, patch(
            "core.services.chat_service.persist_direct_message_change",
            new=AsyncMock(return_value=persisted),
        ) as persist_mock:
            datetime_mock.now.return_value = now
            result = await apply_direct_message_edit(
                db,
                message_id=11,
                actor_id=7,
                content="edited",
            )

        self.assertIs(result, persisted)
        guard_mock.assert_awaited_once_with(db, message_id=11, actor_id=7, now=now)
        update_mock.assert_called_once_with(message, content="edited", updated_at=now)
        persist_mock.assert_awaited_once_with(db, message)

    async def test_apply_direct_message_reaction_toggle_routes_through_guard_toggle_and_persist(self):
        message = SimpleNamespace(id=12)
        persisted = SimpleNamespace(id=12, reactions=[{"emoji": "🔥", "user_id": 7}])
        db = object()

        with patch(
            "core.services.chat_service.get_reactable_direct_message",
            new=AsyncMock(return_value=message),
        ) as guard_mock, patch(
            "core.services.chat_service.toggle_direct_message_reaction_state"
        ) as toggle_mock, patch(
            "core.services.chat_service.persist_direct_message_change",
            new=AsyncMock(return_value=persisted),
        ) as persist_mock:
            result = await apply_direct_message_reaction_toggle(
                db,
                message_id=12,
                actor_id=7,
                emoji="🔥",
            )

        self.assertIs(result, persisted)
        guard_mock.assert_awaited_once_with(db, message_id=12, actor_id=7)
        toggle_mock.assert_called_once_with(message, acting_user_id=7, emoji="🔥")
        persist_mock.assert_awaited_once_with(db, message, include_sender=True)

    async def test_apply_direct_message_delete_routes_through_guard_mark_and_persist(self):
        now = datetime(2026, 5, 7, 23, 50, tzinfo=timezone.utc)
        message = SimpleNamespace(id=13)
        db = object()

        with patch("core.services.chat_service.datetime") as datetime_mock, patch(
            "core.services.chat_service.get_deletable_direct_message",
            new=AsyncMock(return_value=message),
        ) as guard_mock, patch(
            "core.services.chat_service.mark_direct_message_deleted"
        ) as mark_mock, patch(
            "core.services.chat_service.persist_direct_message_change",
            new=AsyncMock(return_value=None),
        ) as persist_mock:
            datetime_mock.now.return_value = now
            await apply_direct_message_delete(
                db,
                message_id=13,
                actor_id=7,
            )

        guard_mock.assert_awaited_once_with(db, message_id=13, actor_id=7, now=now)
        mark_mock.assert_called_once_with(message, deleted_at=now)
        persist_mock.assert_awaited_once_with(db, message)

    async def test_apply_direct_message_delete_clears_pinned_chat_metadata_when_needed(self):
        now = datetime(2026, 5, 8, 0, 5, tzinfo=timezone.utc)
        message = SimpleNamespace(id=14, chat_id=77)
        chat = SimpleNamespace(
            pinned_message_id=14,
            pinned_message_at=now,
            pinned_message_by_id=7,
            updated_at=None,
        )
        db = SimpleNamespace(get=AsyncMock(return_value=chat))

        with patch("core.services.chat_service.datetime") as datetime_mock, patch(
            "core.services.chat_service.get_deletable_direct_message",
            new=AsyncMock(return_value=message),
        ), patch(
            "core.services.chat_service.mark_direct_message_deleted"
        ) as mark_mock, patch(
            "core.services.chat_service.persist_direct_message_change",
            new=AsyncMock(return_value=None),
        ):
            datetime_mock.now.return_value = now
            await apply_direct_message_delete(db, message_id=14, actor_id=7)

        db.get.assert_awaited_once_with(Chat, 77)
        self.assertIsNone(chat.pinned_message_id)
        self.assertIsNone(chat.pinned_message_at)
        self.assertIsNone(chat.pinned_message_by_id)
        self.assertEqual(chat.updated_at, now)
        mark_mock.assert_called_once_with(message, deleted_at=now)


if __name__ == "__main__":
    unittest.main()