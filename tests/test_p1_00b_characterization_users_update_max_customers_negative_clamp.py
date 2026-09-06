"""Direct characterization test for api.routers.users.update_user negative max_customers clamping.

This file covers only the negative max_customers clamp branch and does not complete
P1-00b, seed inventory, Offer/Request-origin evidence, Stage acceptance, or production
readiness.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import update_user
from core.enums import UserAccountStatus, UserRole
import models.user
from schemas import UserUpdate


class MinimalAsyncDB:
    def __init__(self, target):
        self.target = target
        self.get_calls = []
        self.commit_calls = 0
        self.refresh_calls = []

    async def get(self, model, key):
        self.get_calls.append((model, key))
        return self.target

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, value):
        self.refresh_calls.append(value)


class UsersUpdateMaxCustomersNegativeClampCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_negative_max_customers_clamps_to_zero(self):
        actor_id = 7
        requested_user_id = 42
        target = SimpleNamespace(
            id=requested_user_id,
            role=UserRole.STANDARD,
            max_sessions=3,
            is_deleted=False,
            deleted_at=None,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=None,
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
            can_block_users=True,
            max_blocked_users=10,
            max_accountants=3,
            max_customers=5,
        )
        actor = SimpleNamespace(
            id=actor_id,
            role=UserRole.MIDDLE_MANAGER,
        )
        db = MinimalAsyncDB(target)
        user_update = UserUpdate(max_customers=-1)

        sentinel_result = object()
        queue_sentinel = object()

        with patch(
            "api.routers.users.sync_mandatory_channel_for_user_state_change",
            new_callable=AsyncMock,
        ) as mock_sync_channel, patch(
            "core.cache.invalidate_user_cache",
            new_callable=AsyncMock,
        ) as mock_invalidate_cache, patch(
            "api.routers.users.attach_customer_user_context",
            new_callable=AsyncMock,
            return_value=target,
        ) as mock_attach_context, patch(
            "api.routers.users.serialize_user_read",
            return_value=sentinel_result,
        ) as mock_serialize, patch(
            "api.routers.users.audit_log",
        ) as mock_audit_log, patch(
            "api.routers.users.configured_telegram_delivery_producer_mode",
            return_value=queue_sentinel,
        ) as mock_producer_mode, patch(
            "api.routers.users.send_block_notification",
            new_callable=AsyncMock,
        ) as mock_send_block_notification, patch(
            "api.routers.users.send_limitation_notification",
            new_callable=AsyncMock,
        ) as mock_send_limitation_notification, patch(
            "api.routers.users.enqueue_delayed_removal_telegram_notification_api",
            new_callable=AsyncMock,
        ) as mock_enqueue_delayed_removal, patch(
            "api.routers.users.send_delayed_removal_notification_api",
            new_callable=AsyncMock,
        ) as mock_send_delayed_removal, patch(
            "api.routers.users.asyncio.create_task",
        ) as mock_create_task:
            result = await update_user(
                requested_user_id,
                user_update,
                db=db,
                actor=actor,
                _admin_authority=None,
            )

        self.assertEqual(user_update.model_dump(exclude_unset=True), {"max_customers": -1})
        self.assertFalse(hasattr(db, "execute"))
        self.assertEqual(db.get_calls, [(models.user.User, requested_user_id)])
        self.assertEqual(db.commit_calls, 1)
        self.assertEqual(db.refresh_calls, [target])
        self.assertEqual(target.max_customers, 0)
        self.assertIs(result, sentinel_result)

        mock_attach_context.assert_awaited_once_with(db, target)
        mock_serialize.assert_called_once_with(target)
        mock_producer_mode.assert_called_once_with()
        mock_invalidate_cache.assert_awaited_once_with(None)
        mock_sync_channel.assert_awaited_once_with(
            db,
            user=target,
            previous_role=UserRole.STANDARD,
            previous_is_deleted=False,
            previous_deleted_at=None,
        )

        mock_send_block_notification.assert_not_called()
        mock_send_block_notification.assert_not_awaited()
        mock_send_limitation_notification.assert_not_called()
        mock_send_limitation_notification.assert_not_awaited()
        mock_enqueue_delayed_removal.assert_not_called()
        mock_enqueue_delayed_removal.assert_not_awaited()
        mock_send_delayed_removal.assert_not_called()
        mock_send_delayed_removal.assert_not_awaited()
        mock_create_task.assert_not_called()

        mock_audit_log.assert_called_once()
        audit_call_args, audit_call_kwargs = mock_audit_log.call_args
        self.assertEqual(audit_call_args, ("user.update",))
        self.assertEqual(audit_call_kwargs["target_type"], "user")
        self.assertEqual(audit_call_kwargs["target_id"], requested_user_id)
        self.assertEqual(audit_call_kwargs["actor_id"], actor_id)
        self.assertEqual(
            audit_call_kwargs["actor_role"],
            UserRole.MIDDLE_MANAGER.value,
        )
        self.assertEqual(
            audit_call_kwargs["after_summary"]["updated_fields"],
            ["max_customers"],
        )


if __name__ == "__main__":
    unittest.main()
