"""Stage: P1-00b.
Assignment: WA-P1-00B-FREEZE-20260904-001.
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
Seed: API-AUTH-GET-AUTH-READ-USERS-ME-L1295.

Characterization only.
Execute the real api.routers.auth.read_users_me handler once for the single
currently unmet Tier-2 customer Telegram-denial projection outcome.

Explicit exclusions:
- Production/source/docs/inventory changes
- Real DB/session/table/network/provider access
- Tier-1 outcomes
- Accountant outcomes
- Linked-Telegram outcomes
- Missing-relation outcomes
- Inactive/deleted-role outcomes
- Mutation outcomes
- Closure/count/documentation claims
"""

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from api.routers.auth import read_users_me
from core.enums import UserAccountStatus, UserRole
from core.services.bot_access_policy import (
    BOT_ACCESS_REASON_CUSTOMER_TIER2,
    BotAccessDecision,
)
from models.customer_relation import CustomerTier
import schemas


class _OpaqueDBSentinel:
    """Opaque non-AsyncSession DB sentinel with no execute/commit/rollback."""
    pass


class TestP100bCharacterizationReadUsersMeRelationAccess(unittest.IsolatedAsyncioTestCase):
    async def test_read_users_me_tier2_customer_telegram_denial_projection(self):
        user = SimpleNamespace(
            id=7,
            telegram_id=None,
            username="ali_user",
            full_name="علی رضایی",
            account_name="ali",
            mobile_number="09120000000",
            address="تهران",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            deactivated_at=None,
            messenger_grace_expires_at=None,
            messenger_blocked_at=None,
            has_bot_access=True,
            is_deleted=False,
            avatar_file_id=None,
            created_at=datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc),
            trading_restricted_until=None,
            offer_overtime_minutes=0,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
            trades_count=0,
            commodities_traded_count=0,
            channel_messages_count=0,
            last_seen_at=None,
            can_block_users=True,
            max_blocked_users=10,
            max_sessions=1,
            max_accountants=3,
            max_customers=5,
        )

        db = _OpaqueDBSentinel()
        fixed_management_name = "مشتری ویژه ۲"
        customer_relation = SimpleNamespace(
            customer_tier=CustomerTier.TIER_2,
            owner_user_id=20,
            management_name=fixed_management_name,
        )
        denial_decision = BotAccessDecision(
            False,
            BOT_ACCESS_REASON_CUSTOMER_TIER2,
            CustomerTier.TIER_2.value,
        )

        with patch(
            "api.routers.auth.is_user_accountant",
            new=AsyncMock(return_value=False),
        ) as accountant_mock, patch(
            "api.routers.auth.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=customer_relation),
        ) as customer_relation_mock, patch(
            "api.routers.auth.evaluate_bot_access",
            new=AsyncMock(return_value=denial_decision),
        ) as bot_access_mock:
            result = await read_users_me(current_user=user, db=db)

        accountant_mock.assert_awaited_once_with(db, 7)
        customer_relation_mock.assert_awaited_once_with(db, 7)
        bot_access_mock.assert_awaited_once_with(db, user)

        self.assertIs(accountant_mock.await_args.args[0], db)
        self.assertEqual(accountant_mock.await_args.args[1], 7)
        self.assertIs(customer_relation_mock.await_args.args[0], db)
        self.assertEqual(customer_relation_mock.await_args.args[1], 7)
        self.assertIs(bot_access_mock.await_args.args[0], db)
        self.assertIs(bot_access_mock.await_args.args[1], user)

        self.assertIsInstance(result, schemas.UserRead)
        self.assertEqual(result.id, 7)
        self.assertEqual(result.account_name, "ali")
        self.assertEqual(result.mobile_number, "09120000000")
        self.assertEqual(result.full_name, "علی رضایی")
        self.assertEqual(result.role, UserRole.STANDARD)
        self.assertEqual(result.account_status, UserAccountStatus.ACTIVE)

        self.assertFalse(result.is_accountant)
        self.assertIsNone(result.accountant_owner_user_id)
        self.assertIsNone(result.accountant_owner_account_name)
        self.assertTrue(result.is_customer)
        self.assertEqual(result.customer_tier, CustomerTier.TIER_2)
        self.assertEqual(result.customer_owner_user_id, 20)
        self.assertIsNone(result.customer_owner_account_name)
        self.assertEqual(result.customer_management_name, fixed_management_name)
        self.assertFalse(result.telegram_linked)
        self.assertFalse(result.can_connect_telegram)
        self.assertEqual(
            result.telegram_link_denial_reason,
            BOT_ACCESS_REASON_CUSTOMER_TIER2,
        )


if __name__ == "__main__":
    unittest.main()
