import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.auth import read_users_me, update_my_avatar
from models.customer_relation import CustomerTier
from models.user import UserRole


def make_user(**overrides):
    data = {
        'id': 7,
        'telegram_id': None,
        'username': None,
        'full_name': "علی رضایی",
        'account_name': "ali",
        'mobile_number': "09120000000",
        'role': UserRole.STANDARD,
        'account_status': 'active',
        'messenger_grace_expires_at': None,
        'messenger_blocked_at': None,
        'has_bot_access': True,
        'is_deleted': False,
        'avatar_file_id': None,
        'created_at': datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc),
        'trading_restricted_until': None,
        'max_daily_trades': None,
        'max_active_commodities': None,
        'max_daily_requests': None,
        'limitations_expire_at': None,
        'trades_count': 0,
        'commodities_traded_count': 0,
        'channel_messages_count': 0,
        'last_seen_at': None,
        'can_block_users': True,
        'max_blocked_users': 10,
        'max_sessions': 1,
        'max_accountants': 3,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class AuthRouterCurrentUserContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_users_me_includes_accountant_state(self):
        user = make_user(
            account_status='inactive',
            messenger_grace_expires_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
            messenger_blocked_at=datetime(2026, 5, 21, 8, 0, tzinfo=timezone.utc),
        )

        with patch("api.routers.auth.is_user_accountant", new=AsyncMock(return_value=True)) as accountant_mock, patch(
            "api.routers.auth.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ) as customer_relation_mock:
            result = await read_users_me(current_user=user, db=object())

        accountant_mock.assert_awaited_once_with(unittest.mock.ANY, 7)
        customer_relation_mock.assert_awaited_once_with(unittest.mock.ANY, 7)
        self.assertEqual(result.id, 7)
        self.assertTrue(result.is_accountant)
        self.assertFalse(result.is_customer)
        self.assertIsNone(result.customer_tier)
        self.assertEqual(result.account_name, "ali")
        self.assertEqual(result.global_lock_grace_expires_at, user.messenger_grace_expires_at)
        self.assertEqual(result.global_web_locked_at, user.messenger_blocked_at)

    async def test_update_my_avatar_preserves_accountant_state_in_response(self):
        user = make_user()
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

        with patch(
            "api.routers.auth.resolve_owned_avatar_file_id",
            new=AsyncMock(return_value="avatar-1"),
        ) as resolve_mock, patch(
            "api.routers.auth.is_user_accountant",
            new=AsyncMock(return_value=False),
        ) as accountant_mock, patch(
            "api.routers.auth.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=SimpleNamespace(customer_tier=CustomerTier.TIER_2)),
        ) as customer_relation_mock:
            result = await update_my_avatar(
                payload=SimpleNamespace(avatar_file_id="avatar-1"),
                current_user=user,
                db=db,
            )

        resolve_mock.assert_awaited_once_with(
            db,
            actor_id=7,
            avatar_file_id="avatar-1",
        )
        accountant_mock.assert_awaited_once_with(db, 7)
        customer_relation_mock.assert_awaited_once_with(db, 7)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(user)
        self.assertEqual(user.avatar_file_id, "avatar-1")
        self.assertFalse(result.is_accountant)
        self.assertTrue(result.is_customer)
        self.assertEqual(result.customer_tier, CustomerTier.TIER_2)
        self.assertEqual(result.avatar_file_id, "avatar-1")


if __name__ == "__main__":
    unittest.main()