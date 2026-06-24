import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

import schemas
from core.enums import UserAccountStatus, UserRole
from api.routers.users import read_user


class FakeDB:
    def __init__(self, user, execute_results=None):
        self.user = user
        self.execute_results = list(execute_results or [])
        self.execute = AsyncMock(side_effect=self._execute)

    async def get(self, model, user_id):
        return self.user

    async def _execute(self, _stmt):
        if self.execute_results:
            return self.execute_results.pop(0)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


def relation_result(items):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(items)))


def make_user(**overrides):
    data = {
        "id": 7,
        "telegram_id": None,
        "username": None,
        "full_name": "user7",
        "account_name": "user7",
        "mobile_number": "09120000007",
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "deactivated_at": None,
        "messenger_grace_expires_at": None,
        "messenger_blocked_at": None,
        "has_bot_access": True,
        "is_accountant": False,
        "is_customer": False,
        "customer_tier": None,
        "customer_owner_user_id": None,
        "customer_owner_account_name": None,
        "customer_management_name": None,
        "is_deleted": False,
        "avatar_file_id": None,
        "created_at": datetime.utcnow(),
        "trading_restricted_until": None,
        "max_daily_trades": None,
        "max_active_commodities": None,
        "max_daily_requests": None,
        "limitations_expire_at": None,
        "trades_count": 0,
        "commodities_traded_count": 0,
        "channel_messages_count": 0,
        "last_seen_at": None,
        "can_block_users": True,
        "max_blocked_users": 10,
        "max_sessions": 1,
        "max_accountants": 3,
        "max_customers": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class UsersRouterReadOneTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_user_returns_user_when_found(self):
        user = make_user()
        result = await read_user(7, db=FakeDB(user))
        self.assertIsInstance(result, schemas.UserRead)
        self.assertEqual(result.id, user.id)
        self.assertEqual(result.account_name, user.account_name)

    async def test_read_user_raises_404_when_missing(self):
        with self.assertRaises(HTTPException) as exc_info:
            await read_user(7, db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_read_user_enriches_customer_context_when_target_is_active_customer(self):
        owner_user = SimpleNamespace(id=20, account_name="owner20", is_deleted=False)
        relation = SimpleNamespace(
            customer_user_id=7,
            owner_user=owner_user,
            owner_user_id=20,
            management_name="مشتری ویژه",
            customer_tier="tier2",
        )
        user = make_user(is_deleted=False)

        result = await read_user(7, db=FakeDB(user, execute_results=[relation_result([relation]), relation_result([])]))

        self.assertTrue(user.is_customer)
        self.assertEqual(user.customer_owner_user_id, 20)
        self.assertEqual(user.customer_owner_account_name, "owner20")
        self.assertEqual(user.customer_management_name, "مشتری ویژه")
        self.assertEqual(user.customer_tier, "tier2")
        self.assertTrue(result.is_customer)
        self.assertEqual(result.customer_owner_user_id, 20)
        self.assertEqual(result.customer_owner_account_name, "owner20")
        self.assertEqual(result.customer_management_name, "مشتری ویژه")
        self.assertEqual(result.customer_tier, "tier2")


if __name__ == "__main__":
    unittest.main()
