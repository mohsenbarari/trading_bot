import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import UserAccountStatus, UserRole
from core.services.bot_access_policy import (
    BOT_ACCESS_REASON_ACCOUNTANT,
    BOT_ACCESS_REASON_CUSTOMER_TIER2,
    BOT_ACCESS_REASON_ROLE_FORBIDDEN,
    BOT_ACCESS_REASON_SYNC_PENDING,
    evaluate_bot_access,
    evaluate_bot_access_local_state,
)
from models.customer_relation import CustomerTier


class BotAccessPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_policy_denies_watch_and_inactive_users(self):
        watch_user = SimpleNamespace(role=UserRole.WATCH, account_status=UserAccountStatus.ACTIVE, is_deleted=False)
        inactive_user = SimpleNamespace(role=UserRole.STANDARD, account_status=UserAccountStatus.INACTIVE, is_deleted=False)
        standard_user = SimpleNamespace(role=UserRole.STANDARD, account_status=UserAccountStatus.ACTIVE, is_deleted=False)

        self.assertEqual(evaluate_bot_access_local_state(watch_user).reason, BOT_ACCESS_REASON_ROLE_FORBIDDEN)
        self.assertFalse(evaluate_bot_access_local_state(inactive_user).allowed)
        self.assertTrue(evaluate_bot_access_local_state(standard_user).allowed)

    async def test_local_policy_fails_closed_for_incomplete_non_user_object(self):
        incomplete_user = SimpleNamespace(id=9, account_status=UserAccountStatus.ACTIVE, is_deleted=False)

        decision = evaluate_bot_access_local_state(incomplete_user)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "pending_sync")

        incomplete_user = SimpleNamespace(id=10, account_status=UserAccountStatus.ACTIVE, is_deleted=False)
        incomplete_decision = evaluate_bot_access_local_state(incomplete_user)
        self.assertFalse(incomplete_decision.allowed)
        self.assertEqual(incomplete_decision.reason, BOT_ACCESS_REASON_SYNC_PENDING)

    async def test_relation_policy_denies_accountants_and_tier2_customers(self):
        db = AsyncSession()
        user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_status=UserAccountStatus.ACTIVE, is_deleted=False)

        with patch("core.services.bot_access_policy.is_user_accountant", new=AsyncMock(return_value=True)):
            decision = await evaluate_bot_access(db, user)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, BOT_ACCESS_REASON_ACCOUNTANT)

        tier2_relation = SimpleNamespace(customer_tier=CustomerTier.TIER_2, deleted_at=None)
        with patch("core.services.bot_access_policy.is_user_accountant", new=AsyncMock(return_value=False)), patch(
            "core.services.bot_access_policy.get_active_customer_relation_for_user",
            new=AsyncMock(return_value=tier2_relation),
        ):
            decision = await evaluate_bot_access(db, user)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, BOT_ACCESS_REASON_CUSTOMER_TIER2)

        await db.close()

    async def test_tier1_customer_is_allowed(self):
        db = AsyncSession()
        user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_status=UserAccountStatus.ACTIVE, is_deleted=False)
        tier1_relation = SimpleNamespace(customer_tier=CustomerTier.TIER_1, deleted_at=None)

        with patch("core.services.bot_access_policy.is_user_accountant", new=AsyncMock(return_value=False)), patch(
            "core.services.bot_access_policy.get_active_customer_relation_for_user",
            new=AsyncMock(return_value=tier1_relation),
        ):
            decision = await evaluate_bot_access(db, user)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.customer_tier, CustomerTier.TIER_1.value)
        await db.close()


if __name__ == "__main__":
    unittest.main()
