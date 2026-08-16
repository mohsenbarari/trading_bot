import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.utils.customer_display import resolve_customer_display_name_for_viewer


class ViewerScopedCustomerDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_customer_keeps_account_name(self):
        user = SimpleNamespace(id=41, account_name="ordinary-user")
        with patch(
            "bot.utils.customer_display.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            result = await resolve_customer_display_name_for_viewer(
                object(),
                user,
                viewer_user_id=99,
            )
        self.assertEqual(result, "ordinary-user")

    async def test_customer_identity_is_visible_to_owner_and_same_owner_accountant(self):
        user = SimpleNamespace(id=41, account_name="customer-41")
        relation = SimpleNamespace(owner_user_id=7, management_name="مشتری بازار")
        with patch(
            "bot.utils.customer_display.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ), patch(
            "bot.utils.customer_display.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            owner_result = await resolve_customer_display_name_for_viewer(
                object(),
                user,
                viewer_user_id=7,
            )
        with patch(
            "bot.utils.customer_display.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ), patch(
            "bot.utils.customer_display.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=7)),
        ):
            accountant_result = await resolve_customer_display_name_for_viewer(
                object(),
                user,
                viewer_user_id=17,
            )

        self.assertEqual(owner_result, "مشتری بازار")
        self.assertEqual(accountant_result, "مشتری بازار")

    async def test_foreign_customer_identity_is_redacted_even_for_unrelated_admin(self):
        user = SimpleNamespace(id=41, account_name="customer-secret")
        relation = SimpleNamespace(owner_user_id=7, management_name="نام محرمانه")
        with patch(
            "bot.utils.customer_display.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ), patch(
            "bot.utils.customer_display.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            result = await resolve_customer_display_name_for_viewer(
                object(),
                user,
                viewer_user_id=99,
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
