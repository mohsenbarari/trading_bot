import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from models.accountant_relation import AccountantRelationStatus
from core.services.user_deletion_service import delete_user_account


def scalar_result(rows):
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    return result


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
        invitations = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result(invitations)]),
            delete=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        revoked_sessions = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2")]

        with patch("core.services.user_deletion_service.deactivate_active_sessions", AsyncMock(return_value=revoked_sessions)) as deactivate_sessions, \
            patch("core.services.user_deletion_service.sync_mandatory_channel_for_user_state_change", AsyncMock()) as mandatory_sync, \
            patch("core.services.user_deletion_service.mark_deleted_telegram_user", AsyncMock()) as mark_deleted_telegram_user, \
            patch("core.services.user_deletion_service.send_telegram_notification", AsyncMock(return_value=True)) as send_telegram_notification, \
            patch("core.services.user_deletion_service.publish_session_revocation", AsyncMock()) as publish_session_revocation, \
            patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock()) as remove_user_from_channel:
            result = await delete_user_account(db, user)

        self.assertEqual(db.execute.await_count, 6)
        self.assertEqual(db.delete.await_count, 2)
        deactivate_sessions.assert_awaited_once_with(db, user.id)
        mandatory_sync.assert_awaited_once_with(db, user=user, previous_is_deleted=False, previous_deleted_at=None)
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
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result([])]),
            delete=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        revoked_sessions = [SimpleNamespace(id="s3")]

        with patch("core.services.user_deletion_service.deactivate_active_sessions", AsyncMock(return_value=revoked_sessions)), \
            patch("core.services.user_deletion_service.sync_mandatory_channel_for_user_state_change", AsyncMock()) as mandatory_sync, \
            patch("core.services.user_deletion_service.mark_deleted_telegram_user", AsyncMock()) as mark_deleted_telegram_user, \
            patch("core.services.user_deletion_service.send_telegram_notification", AsyncMock(return_value=True)) as send_telegram_notification, \
            patch("core.services.user_deletion_service.publish_session_revocation", AsyncMock()) as publish_session_revocation, \
            patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock()) as remove_user_from_channel:
            result = await delete_user_account(db, user)

        user.soft_delete.assert_called_once_with()
        mandatory_sync.assert_awaited_once_with(db, user=user, previous_is_deleted=False, previous_deleted_at=None)
        db.delete.assert_not_awaited()
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
        db = SimpleNamespace(execute=AsyncMock(), delete=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())

        with self.assertRaisesRegex(ValueError, "already deleted"):
            await delete_user_account(db, user)

        db.execute.assert_not_awaited()
        db.delete.assert_not_awaited()
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()
        user.soft_delete.assert_not_called()

    async def test_db_failure_rolls_back_and_skips_post_commit_side_effects(self):
        user = SimpleNamespace(
            id=20,
            telegram_id=55443322,
            mobile_number="09127778888",
            account_name="nima",
            is_deleted=False,
            soft_delete=Mock(),
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result([])]),
            delete=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )

        with patch("core.services.user_deletion_service.deactivate_active_sessions", AsyncMock(side_effect=RuntimeError("session revoke failed"))), \
            patch("core.services.user_deletion_service.sync_mandatory_channel_for_user_state_change", AsyncMock()) as mandatory_sync, \
            patch("core.services.user_deletion_service.mark_deleted_telegram_user", AsyncMock()) as mark_deleted_telegram_user, \
            patch("core.services.user_deletion_service.send_telegram_notification", AsyncMock()) as send_telegram_notification, \
            patch("core.services.user_deletion_service.publish_session_revocation", AsyncMock()) as publish_session_revocation, \
            patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock()) as remove_user_from_channel:
            with self.assertRaisesRegex(RuntimeError, "session revoke failed"):
                await delete_user_account(db, user)

        db.commit.assert_not_awaited()
        db.rollback.assert_awaited_once()
        user.soft_delete.assert_not_called()
        mandatory_sync.assert_not_awaited()
        mark_deleted_telegram_user.assert_not_awaited()
        send_telegram_notification.assert_not_awaited()
        publish_session_revocation.assert_not_awaited()
        remove_user_from_channel.assert_not_awaited()

    async def test_post_commit_telegram_failures_do_not_abort_revocation_or_result(self):
        user = SimpleNamespace(
            id=21,
            telegram_id=66554433,
            mobile_number="09129990000",
            account_name="pouya",
            is_deleted=False,
            soft_delete=Mock(),
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result([])]),
            delete=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        revoked_sessions = [SimpleNamespace(id="s10")]

        with patch("core.services.user_deletion_service.deactivate_active_sessions", AsyncMock(return_value=revoked_sessions)), \
            patch("core.services.user_deletion_service.sync_mandatory_channel_for_user_state_change", AsyncMock()) as mandatory_sync, \
            patch("core.services.user_deletion_service.mark_deleted_telegram_user", AsyncMock(side_effect=RuntimeError("redis write failed"))) as mark_deleted_telegram_user, \
            patch("core.services.user_deletion_service.send_telegram_notification", AsyncMock(side_effect=RuntimeError("telegram send failed"))) as send_telegram_notification, \
            patch("core.services.user_deletion_service.publish_session_revocation", AsyncMock()) as publish_session_revocation, \
            patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock(side_effect=RuntimeError("channel cleanup failed"))) as remove_user_from_channel:
            result = await delete_user_account(db, user)

        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()
        user.soft_delete.assert_called_once_with()
        mandatory_sync.assert_awaited_once_with(db, user=user, previous_is_deleted=False, previous_deleted_at=None)
        mark_deleted_telegram_user.assert_awaited_once_with(user.telegram_id)
        send_telegram_notification.assert_awaited_once_with(user.telegram_id, unittest.mock.ANY)
        publish_session_revocation.assert_awaited_once_with(user.id, revoked_sessions)
        remove_user_from_channel.assert_awaited_once_with(user.telegram_id)
        self.assertEqual(result.user_id, user.id)
        self.assertEqual(result.revoked_session_count, 1)

    async def test_owner_deletion_cascades_to_accountant_relations_and_dependent_active_accountants(self):
        owner = SimpleNamespace(
            id=31,
            telegram_id=111222,
            mobile_number="09120000001",
            account_name="owner31",
            is_deleted=False,
            soft_delete=Mock(),
        )
        accountant = SimpleNamespace(
            id=32,
            telegram_id=333444,
            mobile_number="09120000002",
            account_name="acc32",
            is_deleted=False,
            soft_delete=Mock(),
        )
        pending_relation = SimpleNamespace(
            id=401,
            owner_user_id=31,
            accountant_user=None,
            invitation_token="ACCT-pending",
            status=AccountantRelationStatus.PENDING,
            deleted_at=None,
        )
        active_relation = SimpleNamespace(
            id=402,
            owner_user_id=31,
            accountant_user=accountant,
            accountant_user_id=32,
            invitation_token="ACCT-active",
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
        )
        pending_invitation = SimpleNamespace(is_used=False, expires_at=None)

        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    scalar_result([pending_relation, active_relation]),
                    Mock(scalar_one_or_none=Mock(return_value=pending_invitation)),
                    scalar_result([]),
                    scalar_result([active_relation]),
                    Mock(),
                    Mock(),
                    Mock(),
                    scalar_result([]),
                    scalar_result([]),
                    Mock(),
                    Mock(),
                    Mock(),
                    scalar_result([]),
                ]
            ),
            delete=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        owner_revoked_sessions = [SimpleNamespace(id="owner-session")]
        accountant_revoked_sessions = [SimpleNamespace(id="acc-session")]

        with patch(
            "core.services.user_deletion_service.deactivate_active_sessions",
            AsyncMock(side_effect=[accountant_revoked_sessions, owner_revoked_sessions]),
        ) as deactivate_sessions, patch(
            "core.services.user_deletion_service.sync_mandatory_channel_for_user_state_change",
            AsyncMock(),
        ) as mandatory_sync, patch(
            "core.services.user_deletion_service.mark_deleted_telegram_user",
            AsyncMock(),
        ) as mark_deleted_telegram_user, patch(
            "core.services.user_deletion_service.send_telegram_notification",
            AsyncMock(return_value=True),
        ) as send_telegram_notification, patch(
            "core.services.user_deletion_service.publish_session_revocation",
            AsyncMock(),
        ) as publish_session_revocation, patch(
            "core.services.user_deletion_service.remove_user_from_telegram_channel",
            AsyncMock(),
        ) as remove_user_from_channel:
            result = await delete_user_account(db, owner)

        self.assertEqual(deactivate_sessions.await_count, 2)
        mandatory_sync.assert_any_await(db, user=accountant, previous_is_deleted=False, previous_deleted_at=None)
        mandatory_sync.assert_any_await(db, user=owner, previous_is_deleted=False, previous_deleted_at=None)
        owner.soft_delete.assert_called_once_with()
        accountant.soft_delete.assert_called_once_with()
        self.assertTrue(pending_invitation.is_used)
        self.assertIsNotNone(pending_relation.deleted_at)
        self.assertIsNotNone(active_relation.deleted_at)
        self.assertEqual(pending_relation.status.value, "revoked")
        self.assertEqual(active_relation.status.value, "deleted")
        self.assertEqual(mark_deleted_telegram_user.await_count, 2)
        self.assertEqual(send_telegram_notification.await_count, 2)
        self.assertEqual(remove_user_from_channel.await_count, 2)
        publish_session_revocation.assert_any_await(accountant.id, accountant_revoked_sessions)
        publish_session_revocation.assert_any_await(owner.id, owner_revoked_sessions)
        self.assertEqual(result.user_id, owner.id)
        self.assertEqual(result.revoked_session_count, 1)

if __name__ == "__main__":
    unittest.main()