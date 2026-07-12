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
    evaluate_bot_access_projection,
    evaluate_invitation_bot_access,
)
from models.customer_relation import CustomerTier
from models.invitation import InvitationKind


class BotAccessPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_locked_projection_policy_uses_current_relation_truth(self):
        user = SimpleNamespace(
            id=9,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )
        accountant = evaluate_bot_access_projection(
            user,
            is_accountant=True,
            customer_relation_present=False,
            customer_tier=None,
        )
        tier2 = evaluate_bot_access_projection(
            user,
            is_accountant=False,
            customer_relation_present=True,
            customer_tier=CustomerTier.TIER_2,
        )
        missing_tier = evaluate_bot_access_projection(
            user,
            is_accountant=False,
            customer_relation_present=True,
            customer_tier=None,
        )
        self.assertEqual(accountant.reason, BOT_ACCESS_REASON_ACCOUNTANT)
        self.assertEqual(tier2.reason, BOT_ACCESS_REASON_CUSTOMER_TIER2)
        self.assertFalse(missing_tier.allowed)

    def test_invitation_policy_role_kind_tier_matrix_fails_closed(self):
        allowed_roles = {
            UserRole.STANDARD,
            UserRole.POLICE,
            UserRole.MIDDLE_MANAGER,
            UserRole.SUPER_ADMIN,
        }
        for role in UserRole:
            for kind in InvitationKind:
                tiers = (None, CustomerTier.TIER_1, CustomerTier.TIER_2)
                for tier in tiers:
                    with self.subTest(role=role, kind=kind, tier=tier):
                        decision = evaluate_invitation_bot_access(
                            role=role,
                            invitation_kind=kind,
                            customer_tier=tier,
                        )
                        expected = role in allowed_roles and (
                            kind == InvitationKind.STANDARD
                            or (
                                kind == InvitationKind.CUSTOMER
                                and tier == CustomerTier.TIER_1
                            )
                        )
                        self.assertEqual(decision.allowed, expected)
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

    async def test_deleted_customer_relation_fails_closed(self):
        db = AsyncSession()
        user = SimpleNamespace(
            id=9,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )
        deleted_relation = SimpleNamespace(
            customer_tier=CustomerTier.TIER_1,
            deleted_at="2026-07-12T00:00:00Z",
        )
        with patch(
            "core.services.bot_access_policy.is_user_accountant",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.bot_access_policy.get_active_customer_relation_for_user",
            new=AsyncMock(return_value=deleted_relation),
        ):
            decision = await evaluate_bot_access(db, user)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "customer_unavailable")
        await db.close()


if __name__ == "__main__":
    unittest.main()
