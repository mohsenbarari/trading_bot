"""Characterization test for ChannelBulkMemberAddRequest conflicting-selection failure boundary."""

import unittest

from pydantic import ValidationError

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestChannelBulkMemberAddConflictingSelection(unittest.TestCase):
    """Verify ChannelBulkMemberAddRequest raises ValidationError when both user_ids and select_all_active_users are provided."""

    def test_explicit_user_ids_and_select_all_active_users_are_rejected(self):
        with self.assertRaisesRegex(
            ValidationError, "Provide either user_ids or select_all_active_users"
        ):
            ChannelBulkMemberAddRequest(
                user_ids=[7],
                select_all_active_users=True,
            )


if __name__ == "__main__":
    unittest.main()
