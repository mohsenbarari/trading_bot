import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from models.accountant_relation import AccountantRelationStatus
from models.customer_relation import CustomerRelationStatus
from core.services.user_deletion_service import (
    _close_linked_accountant_relations,
    _close_linked_customer_relations,
    _close_owned_accountant_relations,
    _close_owned_customer_relations,
    _delete_user_account_in_transaction,
    delete_user_account,
    remove_user_from_telegram_channel,
)


def scalar_result(rows):
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    return result


class _HttpClientContext:
    def __init__(self):
        self.post = AsyncMock(return_value=SimpleNamespace(status_code=200, text="", json=lambda: {"ok": True}))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DeleteUserAccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_remove_user_from_telegram_channel_respects_config_and_posts_ban_unban(self):
        with patch("core.services.user_deletion_service.settings", SimpleNamespace(channel_id=None, bot_token="bot-token")), patch(
            "core.telegram_gateway.httpx.AsyncClient"
        ) as client_factory:
            await remove_user_from_telegram_channel(123)
        client_factory.assert_not_called()

        client = _HttpClientContext()
        with patch("core.services.user_deletion_service.current_server", return_value="foreign"), patch(
            "core.services.user_deletion_service.settings", SimpleNamespace(channel_id=-100123, bot_token="bot-token")
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            await remove_user_from_telegram_channel(123)

        self.assertEqual(client.post.await_count, 2)
        self.assertIn("banChatMember", client.post.await_args_list[0].args[0])
        self.assertIn("unbanChatMember", client.post.await_args_list[1].args[0])

        with patch("core.services.user_deletion_service.current_server", return_value="iran"), patch(
            "core.services.user_deletion_service.settings", SimpleNamespace(channel_id=-100123, bot_token="bot-token")
        ), patch("core.telegram_gateway.httpx.AsyncClient") as client_factory:
            await remove_user_from_telegram_channel(123)
        client_factory.assert_not_called()

    async def test_accountant_relation_helpers_cover_pending_active_and_skip_paths(self):
        user = SimpleNamespace(id=31, is_deleted=False)
        active_relation = SimpleNamespace(
            id=101,
            status=AccountantRelationStatus.ACTIVE,
            accountant_user=None,
            invitation_token="active-token",
            deleted_at=None,
        )
        expired_relation = SimpleNamespace(
            id=102,
            status=AccountantRelationStatus.EXPIRED,
            accountant_user=None,
            invitation_token="expired-token",
            deleted_at=None,
        )
        owned_db = SimpleNamespace(execute=AsyncMock(return_value=scalar_result([active_relation, expired_relation])))

        with patch(
            "core.services.user_deletion_service._lock_accountant_relation_transition",
            new=AsyncMock(side_effect=[(None, active_relation), (None, expired_relation)]),
        ), patch("core.services.user_deletion_service._revoke_pending_relation_invitation", AsyncMock()) as revoke_mock, patch(
            "core.services.user_deletion_service._delete_user_account_in_transaction", AsyncMock()
        ) as delete_in_tx_mock:
            await _close_owned_accountant_relations(owned_db, user, processed_user_ids=set(), effects=[])

        delete_in_tx_mock.assert_not_awaited()
        revoke_mock.assert_not_awaited()
        self.assertEqual(active_relation.status, AccountantRelationStatus.DELETED)
        self.assertIsNotNone(active_relation.deleted_at)
        self.assertIsNotNone(expired_relation.deleted_at)

        pending_relation = SimpleNamespace(
            id=103,
            status=AccountantRelationStatus.PENDING,
            invitation_token="pending-token",
            deleted_at=None,
        )
        linked_db = SimpleNamespace(execute=AsyncMock(return_value=scalar_result([pending_relation])))
        with patch(
            "core.services.user_deletion_service._lock_accountant_relation_transition",
            new=AsyncMock(return_value=(None, pending_relation)),
        ), patch(
            "core.services.user_deletion_service._revoke_pending_relation_invitation",
            new=AsyncMock(),
        ) as revoke_mock:
            await _close_linked_accountant_relations(linked_db, user)

        revoke_mock.assert_awaited_once()
        self.assertEqual(pending_relation.status, AccountantRelationStatus.REVOKED)
        self.assertIsNotNone(pending_relation.deleted_at)

        active_customer_relation = SimpleNamespace(
            id=201,
            status=CustomerRelationStatus.ACTIVE,
            customer_user=None,
            invitation_token="active-customer-token",
            deleted_at=None,
        )
        expired_customer_relation = SimpleNamespace(
            id=202,
            status=CustomerRelationStatus.EXPIRED,
            customer_user=None,
            invitation_token="expired-customer-token",
            deleted_at=None,
        )
        owned_customer_db = SimpleNamespace(execute=AsyncMock(return_value=scalar_result([active_customer_relation, expired_customer_relation])))

        with patch(
            "core.services.user_deletion_service._lock_customer_relation_transition",
            new=AsyncMock(
                side_effect=[(None, active_customer_relation), (None, expired_customer_relation)]
            ),
        ), patch(
            "core.services.user_deletion_service._revoke_pending_relation_invitation",
            new=AsyncMock(),
        ) as revoke_customer_mock, patch(
            "core.services.user_deletion_service._delete_user_account_in_transaction", AsyncMock()
        ) as delete_in_tx_mock:
            await _close_owned_customer_relations(owned_customer_db, user, processed_user_ids=set(), effects=[])

        delete_in_tx_mock.assert_not_awaited()
        revoke_customer_mock.assert_not_awaited()
        self.assertEqual(active_customer_relation.status, CustomerRelationStatus.DELETED)
        self.assertIsNotNone(active_customer_relation.deleted_at)
        self.assertIsNotNone(expired_customer_relation.deleted_at)

        pending_customer_relation = SimpleNamespace(
            id=203,
            status=CustomerRelationStatus.PENDING,
            invitation_token="pending-customer-token",
            deleted_at=None,
        )
        linked_customer_db = SimpleNamespace(execute=AsyncMock(return_value=scalar_result([pending_customer_relation])))
        with patch(
            "core.services.user_deletion_service._lock_customer_relation_transition",
            new=AsyncMock(return_value=(None, pending_customer_relation)),
        ), patch(
            "core.services.user_deletion_service._revoke_pending_relation_invitation",
            new=AsyncMock(),
        ) as revoke_customer_mock:
            await _close_linked_customer_relations(linked_customer_db, user)

        revoke_customer_mock.assert_awaited_once()
        self.assertEqual(pending_customer_relation.status, CustomerRelationStatus.REVOKED)
        self.assertIsNotNone(pending_customer_relation.deleted_at)

        skip_db = SimpleNamespace(execute=AsyncMock(), delete=AsyncMock())
        await _delete_user_account_in_transaction(skip_db, SimpleNamespace(id=31, is_deleted=False), processed_user_ids={31}, effects=[])
        await _delete_user_account_in_transaction(skip_db, SimpleNamespace(id=32, is_deleted=True), processed_user_ids=set(), effects=[])
        skip_db.execute.assert_not_awaited()

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
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result(invitations)]),
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
            patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock()) as remove_user_from_channel, \
            patch("core.services.user_deletion_service._soft_revoke_pending_invitations_for_user_identity", AsyncMock()) as revoke_invitations:
            result = await delete_user_account(db, user)

        self.assertEqual(db.execute.await_count, 7)
        db.delete.assert_not_awaited()
        revoke_invitations.assert_awaited_once()
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

    async def test_linked_user_deletion_on_iran_skips_direct_telegram_api_cleanup(self):
        user = SimpleNamespace(
            id=117,
            telegram_id=44112234,
            mobile_number="09120000001",
            account_name="reza",
            is_deleted=False,
            soft_delete=Mock(),
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result([])]),
            delete=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        revoked_sessions = [SimpleNamespace(id="iran-s1")]

        with patch("core.services.user_deletion_service.current_server", return_value="iran"), \
            patch("core.services.user_deletion_service.deactivate_active_sessions", AsyncMock(return_value=revoked_sessions)), \
            patch("core.services.user_deletion_service.sync_mandatory_channel_for_user_state_change", AsyncMock()), \
            patch("core.services.user_deletion_service.mark_deleted_telegram_user", AsyncMock()) as mark_deleted_telegram_user, \
            patch("core.services.user_deletion_service.send_telegram_notification", AsyncMock(return_value=True)) as send_telegram_notification, \
            patch("core.services.user_deletion_service.publish_session_revocation", AsyncMock()) as publish_session_revocation, \
            patch("core.services.user_deletion_service.remove_user_from_telegram_channel", AsyncMock()) as remove_user_from_channel:
            result = await delete_user_account(db, user)

        db.commit.assert_awaited_once()
        user.soft_delete.assert_called_once_with()
        mark_deleted_telegram_user.assert_awaited_once_with(user.telegram_id)
        send_telegram_notification.assert_not_awaited()
        remove_user_from_channel.assert_not_awaited()
        publish_session_revocation.assert_awaited_once_with(user.id, revoked_sessions)
        self.assertEqual(result.telegram_id, user.telegram_id)

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
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result([])]),
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
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result([])]),
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
            execute=AsyncMock(side_effect=[scalar_result([]), scalar_result([]), scalar_result([]), scalar_result([]), Mock(), Mock(), Mock(), scalar_result([])]),
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
        pending_invitation = SimpleNamespace(
            id=301,
            is_used=False,
            expires_at=None,
            revoked_at=None,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
        )

        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    scalar_result([pending_relation, active_relation]),
                    scalar_result([]),
                    scalar_result([active_relation]),
                    Mock(),
                    Mock(),
                    Mock(),
                    scalar_result([]),
                    Mock(),
                    Mock(),
                    Mock(),
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
            "core.services.user_deletion_service._close_owned_customer_relations",
            AsyncMock(),
        ), patch(
            "core.services.user_deletion_service._close_linked_customer_relations",
            AsyncMock(),
        ), patch(
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
        ) as remove_user_from_channel, patch(
            "core.services.user_deletion_service._lock_accountant_relation_transition",
            AsyncMock(
                side_effect=[
                    (pending_invitation, pending_relation),
                    (None, active_relation),
                    (None, active_relation),
                ]
            ),
        ), patch(
            "core.services.user_deletion_service._revoke_pending_relation_invitation",
            AsyncMock(side_effect=lambda *_args, **_kwargs: setattr(pending_invitation, "revoked_at", object())),
        ), patch(
            "core.services.user_deletion_service._soft_revoke_pending_invitations_for_user_identity",
            AsyncMock(),
        ):
            result = await delete_user_account(db, owner)

        self.assertEqual(deactivate_sessions.await_count, 2)
        mandatory_sync.assert_any_await(db, user=accountant, previous_is_deleted=False, previous_deleted_at=None)
        mandatory_sync.assert_any_await(db, user=owner, previous_is_deleted=False, previous_deleted_at=None)
        owner.soft_delete.assert_called_once_with()
        accountant.soft_delete.assert_called_once_with()
        self.assertFalse(pending_invitation.is_used)
        self.assertIsNotNone(pending_invitation.revoked_at)
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

    async def test_owner_deletion_cascades_to_customer_relations_and_dependent_active_customers(self):
        owner = SimpleNamespace(
            id=41,
            telegram_id=211122,
            mobile_number="09120000011",
            account_name="owner41",
            is_deleted=False,
            soft_delete=Mock(),
        )
        customer = SimpleNamespace(
            id=42,
            telegram_id=333555,
            mobile_number="09120000012",
            account_name="cust42",
            is_deleted=False,
            soft_delete=Mock(),
        )
        pending_relation = SimpleNamespace(
            id=501,
            owner_user_id=41,
            customer_user=None,
            invitation_token="CUST-pending",
            status=CustomerRelationStatus.PENDING,
            deleted_at=None,
        )
        active_relation = SimpleNamespace(
            id=502,
            owner_user_id=41,
            customer_user=customer,
            customer_user_id=42,
            invitation_token="CUST-active",
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
        )
        pending_invitation = SimpleNamespace(
            id=302,
            is_used=False,
            expires_at=None,
            revoked_at=None,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
        )

        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    scalar_result([pending_relation, active_relation]),
                    scalar_result([]),
                    scalar_result([active_relation]),
                    Mock(),
                    Mock(),
                    Mock(),
                    scalar_result([]),
                    Mock(),
                    Mock(),
                    Mock(),
                ]
            ),
            delete=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        owner_revoked_sessions = [SimpleNamespace(id="owner-session")]
        customer_revoked_sessions = [SimpleNamespace(id="cust-session")]

        with patch(
            "core.services.user_deletion_service.deactivate_active_sessions",
            AsyncMock(side_effect=[customer_revoked_sessions, owner_revoked_sessions]),
        ) as deactivate_sessions, patch(
            "core.services.user_deletion_service._close_owned_accountant_relations",
            AsyncMock(),
        ), patch(
            "core.services.user_deletion_service._close_linked_accountant_relations",
            AsyncMock(),
        ), patch(
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
        ) as remove_user_from_channel, patch(
            "core.services.user_deletion_service._lock_customer_relation_transition",
            AsyncMock(
                side_effect=[
                    (pending_invitation, pending_relation),
                    (None, active_relation),
                    (None, active_relation),
                ]
            ),
        ), patch(
            "core.services.user_deletion_service._revoke_pending_relation_invitation",
            AsyncMock(side_effect=lambda *_args, **_kwargs: setattr(pending_invitation, "revoked_at", object())),
        ), patch(
            "core.services.user_deletion_service._soft_revoke_pending_invitations_for_user_identity",
            AsyncMock(),
        ):
            result = await delete_user_account(db, owner)

        self.assertEqual(deactivate_sessions.await_count, 2)
        mandatory_sync.assert_any_await(db, user=customer, previous_is_deleted=False, previous_deleted_at=None)
        mandatory_sync.assert_any_await(db, user=owner, previous_is_deleted=False, previous_deleted_at=None)
        owner.soft_delete.assert_called_once_with()
        customer.soft_delete.assert_called_once_with()
        self.assertFalse(pending_invitation.is_used)
        self.assertIsNotNone(pending_invitation.revoked_at)
        self.assertIsNotNone(pending_relation.deleted_at)
        self.assertIsNotNone(active_relation.deleted_at)
        self.assertEqual(pending_relation.status.value, "revoked")
        self.assertEqual(active_relation.status.value, "deleted")
        self.assertEqual(mark_deleted_telegram_user.await_count, 2)
        self.assertEqual(send_telegram_notification.await_count, 2)
        self.assertEqual(remove_user_from_channel.await_count, 2)
        publish_session_revocation.assert_any_await(customer.id, customer_revoked_sessions)
        publish_session_revocation.assert_any_await(owner.id, owner_revoked_sessions)
        self.assertEqual(result.user_id, owner.id)
        self.assertEqual(result.revoked_session_count, 1)

    async def test_delete_user_account_raises_when_primary_effect_is_missing(self):
        user = SimpleNamespace(id=77, is_deleted=False)
        db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

        with patch("core.services.user_deletion_service._delete_user_account_in_transaction", AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "Primary deleted user effect"):
                await delete_user_account(db, user)

        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()
