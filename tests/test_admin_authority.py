import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.admin_authority import require_shared_admin_write_authority
from core.admin_authority import (
    ADMIN_SHARED_AUTHORITY_SERVER,
    check_shared_admin_write_authority,
)


class AdminAuthorityTests(unittest.TestCase):
    def test_iran_server_is_authoritative_for_shared_admin_tables(self):
        decision = check_shared_admin_write_authority(
            "trading_settings",
            operation="update",
            surface="webapp_admin",
            server_mode="iran",
        )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.authority_server, ADMIN_SHARED_AUTHORITY_SERVER)

    def test_foreign_server_rejects_shared_admin_write_with_visible_detail(self):
        decision = check_shared_admin_write_authority(
            "commodities",
            operation="create",
            surface="telegram_bot_admin",
            server_mode="foreign",
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.reason, "admin_write_not_authoritative")
        self.assertEqual(
            decision.as_error_detail(),
            {
                "error": "admin_write_not_authoritative",
                "table": "commodities",
                "operation": "create",
                "surface": "telegram_bot_admin",
                "current_server": "foreign",
                "authority_server": "iran",
            },
        )

    def test_non_shared_table_is_not_blocked_by_admin_authority_policy(self):
        decision = check_shared_admin_write_authority(
            "invitations",
            operation="create",
            surface="telegram_bot_admin",
            server_mode="foreign",
        )

        self.assertTrue(decision.ok)

    def test_fastapi_dependency_fails_closed_on_non_authoritative_server(self):
        dependency = require_shared_admin_write_authority(
            "users",
            operation="limit_update",
            surface="webapp_admin",
        )

        with patch("core.admin_authority.current_server", return_value="foreign"):
            with self.assertRaises(HTTPException) as exc_info:
                dependency()

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail["error"], "admin_write_not_authoritative")
        self.assertEqual(exc_info.exception.detail["authority_server"], "iran")


if __name__ == "__main__":
    unittest.main()
