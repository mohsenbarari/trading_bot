import unittest
from datetime import datetime, timedelta

from core.enums import MessageType
from core.services.chat_service import (
    mark_direct_message_deleted,
    normalize_message_reactions,
    toggle_direct_message_reaction_state,
    update_direct_message_content,
)
from models.message import Message


class ChatServicePureHelperTests(unittest.TestCase):
    def _make_message(self, *, content="hello", edit_history=None, reactions=None):
        return Message(
            sender_id=1,
            receiver_id=2,
            content=content,
            message_type=MessageType.TEXT,
            edit_history=edit_history if edit_history is not None else [],
            reactions=reactions if reactions is not None else [],
        )

    def test_normalize_message_reactions_filters_invalid_duplicates_and_sorts(self):
        normalized = normalize_message_reactions(
            [
                {"emoji": "🔥", "user_id": "2"},
                {"emoji": "👍", "user_id": "1"},
                {"emoji": "🔥", "user_id": 2},
                {"emoji": "invalid", "user_id": 9},
                {"emoji": "❤️", "user_id": "bad"},
                {"emoji": "👍", "user_id": -1},
                "bad-entry",
            ]
        )

        self.assertEqual(
            normalized,
            [
                {"emoji": "👍", "user_id": 1},
                {"emoji": "🔥", "user_id": 2},
            ],
        )

    def test_normalize_message_reactions_returns_empty_for_non_list_input(self):
        self.assertEqual(normalize_message_reactions(None), [])
        self.assertEqual(normalize_message_reactions({"emoji": "🔥", "user_id": 1}), [])

    def test_toggle_direct_message_reaction_adds_sorted_reaction(self):
        message = self._make_message(
            reactions=[
                {"emoji": "🔥", "user_id": 2},
                {"emoji": "👍", "user_id": 4},
            ]
        )

        toggle_direct_message_reaction_state(message, acting_user_id=3, emoji="❤️")

        self.assertEqual(
            message.reactions,
            [
                {"emoji": "👍", "user_id": 4},
                {"emoji": "❤️", "user_id": 3},
                {"emoji": "🔥", "user_id": 2},
            ],
        )

    def test_toggle_direct_message_reaction_removes_only_matching_user_reaction(self):
        message = self._make_message(
            reactions=[
                {"emoji": "🔥", "user_id": 2},
                {"emoji": "🔥", "user_id": 5},
                {"emoji": "👍", "user_id": 1},
            ]
        )

        toggle_direct_message_reaction_state(message, acting_user_id=2, emoji="🔥")

        self.assertEqual(
            message.reactions,
            [
                {"emoji": "👍", "user_id": 1},
                {"emoji": "🔥", "user_id": 5},
            ],
        )

    def test_toggle_direct_message_reaction_replaces_same_users_previous_reaction(self):
        message = self._make_message(
            reactions=[
                {"emoji": "🔥", "user_id": 2},
                {"emoji": "👍", "user_id": 1},
            ]
        )

        toggle_direct_message_reaction_state(message, acting_user_id=2, emoji="❤️")

        self.assertEqual(
            message.reactions,
            [
                {"emoji": "👍", "user_id": 1},
                {"emoji": "❤️", "user_id": 2},
            ],
        )

    def test_update_direct_message_content_appends_history_and_caps_to_three(self):
        message = self._make_message(
            content="v4",
            edit_history=[
                {"content": "v1", "updated_at": "2026-05-07 10:00:00"},
                {"content": "v2", "updated_at": "2026-05-07 10:05:00"},
                {"content": "v3", "updated_at": "2026-05-07 10:10:00"},
            ],
        )
        updated_at = datetime(2026, 5, 7, 10, 15, 0)

        update_direct_message_content(message, content="v5", updated_at=updated_at)

        self.assertEqual(message.content, "v5")
        self.assertEqual(message.updated_at, updated_at)
        self.assertEqual(
            message.edit_history,
            [
                {"content": "v2", "updated_at": "2026-05-07 10:05:00"},
                {"content": "v3", "updated_at": "2026-05-07 10:10:00"},
                {"content": "v4", "updated_at": str(updated_at)},
            ],
        )

    def test_update_direct_message_content_handles_empty_history(self):
        message = self._make_message(content="old")
        updated_at = datetime(2026, 5, 7, 10, 0, 0)

        update_direct_message_content(message, content="new", updated_at=updated_at)

        self.assertEqual(
            message.edit_history,
            [{"content": "old", "updated_at": str(updated_at)}],
        )

    def test_mark_direct_message_deleted_sets_soft_delete_fields(self):
        message = self._make_message(content="to-delete")
        deleted_at = datetime(2026, 5, 7, 11, 0, 0) + timedelta(minutes=1)

        mark_direct_message_deleted(message, deleted_at=deleted_at)

        self.assertTrue(message.is_deleted)
        self.assertEqual(message.updated_at, deleted_at)


if __name__ == "__main__":
    unittest.main()