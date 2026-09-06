"""Characterization unit freezing the isolated behavior of api.routers.users.attach_customer_user_context.

This unit characterizes only attach_customer_user_context, does not complete P1-00b,
and does not establish Offer/Request-origin interaction evidence.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import attach_customer_user_context


class UsersAttachCustomerContextCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests freezing current behavior of attach_customer_user_context.

    This unit characterizes only attach_customer_user_context, does not complete P1-00b,
    and does not establish Offer/Request-origin interaction evidence.
    """

    async def test_attach_customer_user_context_success_returns_identical_user(self):
        """Prove success path: dependency awaited once with db and [user], user returned by identity."""
        db_sentinel = object()
        user_sentinel = object()
        ignored_dependency_return = [object(), object()]

        with patch(
            "api.routers.users.attach_user_management_relation_context",
            new_callable=AsyncMock,
        ) as mock_attach:
            mock_attach.return_value = ignored_dependency_return

            result = await attach_customer_user_context(db_sentinel, user_sentinel)

            mock_attach.assert_awaited_once_with(db_sentinel, [user_sentinel])
            call_args = mock_attach.await_args
            self.assertIs(call_args.args[0], db_sentinel)
            self.assertEqual(len(call_args.args[1]), 1)
            self.assertIs(call_args.args[1][0], user_sentinel)
            self.assertIs(result, user_sentinel)

    async def test_attach_customer_user_context_failure_propagates_exception(self):
        """Prove failure path: distinctive RuntimeError raised by dependency propagates unconcealed."""
        db_sentinel = object()
        user_sentinel = object()
        expected_error = RuntimeError("attach_user_management_relation_context failure")

        with patch(
            "api.routers.users.attach_user_management_relation_context",
            new_callable=AsyncMock,
        ) as mock_attach:
            mock_attach.side_effect = expected_error

            with self.assertRaises(RuntimeError) as ctx:
                await attach_customer_user_context(db_sentinel, user_sentinel)

            self.assertIs(ctx.exception, expected_error)
            mock_attach.assert_awaited_once_with(db_sentinel, [user_sentinel])
            call_args = mock_attach.await_args
            self.assertIs(call_args.args[0], db_sentinel)
            self.assertEqual(len(call_args.args[1]), 1)
            self.assertIs(call_args.args[1][0], user_sentinel)


if __name__ == "__main__":
    unittest.main()
