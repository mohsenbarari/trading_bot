"""Characterization unit freezing the isolated behavior of api.routers.users._audit_user_summary.

This unit characterizes only _audit_user_summary and does not complete P1-00b or establish Offer/Request-origin interaction evidence.
"""

from __future__ import annotations

import types
import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import _audit_user_summary
from core.enums import UserAccountStatus, UserRole


class UsersAuditUserSummaryCharacterizationTests(unittest.TestCase):
    """Characterization tests freezing current behavior of _audit_user_summary.

    This unit characterizes only _audit_user_summary and does not complete P1-00b or establish Offer/Request-origin interaction evidence.
    """

    def test_audit_user_summary_keys_and_ordering(self):
        """Authoritative dictionary result shape preserves exact keys and key order."""
        expected_keys = [
            "role",
            "account_status",
            "is_deleted",
            "trading_restricted",
            "max_sessions",
            "can_block_users",
            "max_blocked_users",
            "max_accountants",
            "max_customers",
        ]
        user = types.SimpleNamespace()
        result = _audit_user_summary(user)
        self.assertEqual(list(result.keys()), expected_keys)

    def test_audit_user_summary_normal_projection_all_attributes_with_enums(self):
        """Normal projection with all nine source attributes populated with enums unwraps values."""
        user = types.SimpleNamespace(
            role=UserRole.SUPER_ADMIN,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
            trading_restricted_until="2026-10-01T00:00:00Z",
            max_sessions=3,
            can_block_users=True,
            max_blocked_users=25,
            max_accountants=5,
            max_customers=10,
        )
        result = _audit_user_summary(user)
        self.assertEqual(
            result,
            {
                "role": "مدیر ارشد",
                "account_status": "active",
                "is_deleted": False,
                "trading_restricted": True,
                "max_sessions": 3,
                "can_block_users": True,
                "max_blocked_users": 25,
                "max_accountants": 5,
                "max_customers": 10,
            },
        )

    def test_audit_user_summary_with_standard_role_and_inactive_status(self):
        """UserRole.STANDARD and UserAccountStatus.INACTIVE unwrap to their respective values."""
        user = types.SimpleNamespace(
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.INACTIVE,
            is_deleted=True,
            trading_restricted_until=None,
            max_sessions=1,
            can_block_users=False,
            max_blocked_users=1,
            max_accountants=0,
            max_customers=0,
        )
        result = _audit_user_summary(user)
        self.assertEqual(
            result,
            {
                "role": "عادی",
                "account_status": "inactive",
                "is_deleted": True,
                "trading_restricted": False,
                "max_sessions": 1,
                "can_block_users": False,
                "max_blocked_users": 1,
                "max_accountants": 0,
                "max_customers": 0,
            },
        )

    def test_audit_user_summary_with_raw_string_role_and_status(self):
        """Raw string values for role and account_status are preserved unchanged."""
        user = types.SimpleNamespace(
            role="custom_string_role",
            account_status="pending_verification",
            is_deleted=False,
            trading_restricted_until=None,
            max_sessions=2,
            can_block_users=True,
            max_blocked_users=10,
            max_accountants=1,
            max_customers=20,
        )
        result = _audit_user_summary(user)
        self.assertEqual(result["role"], "custom_string_role")
        self.assertEqual(result["account_status"], "pending_verification")
        self.assertFalse(result["trading_restricted"])

    def test_audit_user_summary_with_nested_value_objects(self):
        """Value-object role and account_status unwrap value attribute."""
        custom_role = types.SimpleNamespace(value="arbitrary_role_value")
        custom_status = types.SimpleNamespace(value="arbitrary_status_value")
        user = types.SimpleNamespace(
            role=custom_role,
            account_status=custom_status,
            is_deleted=False,
            trading_restricted_until=None,
            max_sessions=1,
            can_block_users=False,
            max_blocked_users=5,
            max_accountants=0,
            max_customers=0,
        )
        result = _audit_user_summary(user)
        self.assertEqual(result["role"], "arbitrary_role_value")
        self.assertEqual(result["account_status"], "arbitrary_status_value")

    def test_audit_user_summary_trading_restriction_anchor_truthiness(self):
        """trading_restricted is strictly bool(trading_restricted_until) across falsy and truthy anchors."""
        for falsy_val in (None, False, "", 0):
            user = types.SimpleNamespace(trading_restricted_until=falsy_val)
            self.assertIs(_audit_user_summary(user)["trading_restricted"], False)

        for truthy_val in ("2026-10-01", True, 1, object()):
            user = types.SimpleNamespace(trading_restricted_until=truthy_val)
            self.assertIs(_audit_user_summary(user)["trading_restricted"], True)

    def test_audit_user_summary_missing_attributes_and_empty_namespace(self):
        """Missing attributes project None, except trading_restricted_until which projects False."""
        user = types.SimpleNamespace()
        result = _audit_user_summary(user)
        self.assertEqual(
            result,
            {
                "role": None,
                "account_status": None,
                "is_deleted": None,
                "trading_restricted": False,
                "max_sessions": None,
                "can_block_users": None,
                "max_blocked_users": None,
                "max_accountants": None,
                "max_customers": None,
            },
        )

    def test_audit_user_summary_with_none_input(self):
        """Passing None as user projects None for missing fields and False for trading_restricted."""
        result = _audit_user_summary(None)
        self.assertEqual(
            result,
            {
                "role": None,
                "account_status": None,
                "is_deleted": None,
                "trading_restricted": False,
                "max_sessions": None,
                "can_block_users": None,
                "max_blocked_users": None,
                "max_accountants": None,
                "max_customers": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
