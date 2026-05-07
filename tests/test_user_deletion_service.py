import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.services.user_deletion_service import delete_user_account


class DeleteUserAccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_linked_user_deletion_revokes_sessions_and_telegram_side_effects(self):
        user = SimpleNamespace(
            id=17,
            telegram_id=44112233,
            mobile_number="09120000000",
            account_name="ali",
            is_deleted=False,
            soft_delete=Mock(),
        )
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
        revoked_sessions = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2")]

        with patch("core.services.user_deletion_service.deactivate_active_sessions", AsyncMock(return_value=revoked_sessions)) as deactivate_sessions, \
             patch("core.services.user_deletion_service.mark_deleted_telegram_user", AsyncMock()) as mark_deleted_telegram_user, \
             patch("core.services.user_deletion_service.send_telegram_notification", AsyncMock(return_value=True)) as send_telegram_notification, \
             patch("core.services.user_deletion_service.publish_session_revocation", AsyncMock()) as publish_session_revocation, \
             patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock()) as remove_user_from_channel:
            result = await delete_user_account(db, user)

        self.assertEqual(db.execute.await_count, 4)
        deactivate_sessions.assert_awaited_once_with(db, user.id)
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()
        user.soft_delete.assert_called_once_with()

        mark_deleted_telegram_user.assert_awaited_once_with(user.telegram_id)
        send_telegram_notification.assert_awaited_once()
        publish_session_revocation.assert_awaited_once_with(user.id, revoked_sessions)
        remove_user_from_channel.assert_awaited_once_with(user.telegram_id)

        self.assertEqual(result.user_id, user.id)
        self.assertEqual(result.telegram_id, user.telegram_id)
        self.assertEqual(result.revoked_session_count, 2)

    async def test_unlinked_user_deletion_only_revokes_sessions(self):
        user = SimpleNamespace(
            id=18,
            telegram_id=None,
            mobile_number="09123334444",
            account_name="sara",
            is_deleted=False,
            soft_delete=Mock(),
        )
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
        revoked_sessions = [SimpleNamespace(id="s3")]

        with patch("core.services.user_deletion_service.deactivate_active_sessions", AsyncMock(return_value=revoked_sessions)), \
             patch("core.services.user_deletion_service.mark_deleted_telegram_user", AsyncMock()) as mark_deleted_telegram_user, \
             patch("core.services.user_deletion_service.send_telegram_notification", AsyncMock(return_value=True)) as send_telegram_notification, \
             patch("core.services.user_deletion_service.publish_session_revocation", AsyncMock()) as publish_session_revocation, \
             patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock()) as remove_user_from_channel:
            result = await delete_user_account(db, user)

        user.soft_delete.assert_called_once_with()
        mark_deleted_telegram_user.assert_not_awaited()
        send_telegram_notification.assert_not_awaited()
        remove_user_from_channel.assert_not_awaited()
        publish_session_revocation.assert_awaited_once_with(user.id, revoked_sessions)
        self.assertEqual(result.revoked_session_count, 1)

    async def test_already_deleted_user_is_rejected_without_mutation(self):
        user = SimpleNamespace(
            id=19,
            telegram_id=998877,
            mobile_number="09125556666",
            account_name="mina",
            is_deleted=True,
            soft_delete=Mock(),
        )
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with self.assertRaisesRegex(ValueError, "already deleted"):
            await delete_user_account(db, user)

        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()
        user.soft_delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()