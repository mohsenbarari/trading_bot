"""Direct-characterization tests for api.routers.chat.create_group customer-status lookup failure.

This is direct characterization only under P1-00b and WA-P1-00B-FREEZE-20260904-001,
and does not update the executed-seed registry, reduce a canonical blocker, promote evidence,
or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import create_group  # noqa: E402
from api.routers.chat_schemas import GroupCreateRequest  # noqa: E402


class CharacterizeChatCreateGroupCustomerLookupFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit test characterizing create_group customer lookup failure boundary."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_create_group_customer_lookup_failure(
        self,
        mock_is_user_customer: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_exc = RuntimeError("customer lookup failure during group creation")

        mock_is_user_customer.side_effect = sentinel_exc

        request_data = GroupCreateRequest(title="Lookup failure group")

        with self.assertRaises(RuntimeError) as ctx:
            await create_group(
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_exc)
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)

        mock_resolve_owned_avatar_file_id.assert_not_called()
        mock_resolve_owned_avatar_file_id.assert_not_awaited()
        mock_create_group_chat.assert_not_called()
        mock_create_group_chat.assert_not_awaited()
        mock_count_active_chat_members.assert_not_called()
        mock_count_active_chat_members.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
